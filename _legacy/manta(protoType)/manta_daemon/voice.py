"""Discord 음성 연결, STT, TTS와 음성 모드 UI."""
import asyncio
import json
import os
import re
from datetime import datetime

import discord

import manta_daemon.state as state
from manta_daemon.integrations.gpt import tools
from manta_daemon.integrations.calendar_ops import (
    get_apple_calendar, add_apple_calendar_event,
    modify_apple_calendar_event, delete_apple_calendar_event,
    delete_all_calendar_events_on_date,
)
from manta_daemon.integrations.weather import get_weather

# ── 음성 상태 ─────────────────────────────────────────────────────────────────
import discord.ext.voice_recv as voice_recv
from manta_daemon.config import MY_DISCORD_UID, VOICE_CHANNEL_ID

_VOICE_MANTA_SPEAKS  = False
_VOICE_USER_SPEAKS   = False
_VOICE_CLIENT: voice_recv.VoiceRecvClient | None = None
_VOICE_SINK: "_MantaVoiceSink | None" = None
_VOICE_TTS_VOICE     = "nova"
_VOICE_TTS_MODEL     = "tts-1"
_VOICE_TTS_SPEED     = 1.05
_VOICE_TTS_QUEUE: asyncio.Queue | None = None
_VOICE_TTS_WORKER: asyncio.Task | None = None

# STT 중복 방지: uid → (text, monotonic_time)
_stt_recent: dict[int, tuple[str, float]] = {}


def _clean_for_tts(text: str) -> str:
    clean = re.sub(r'```[\s\S]*?```', '', text)
    clean = re.sub(r'`[^`]+`', '', clean)
    clean = re.sub(r'https?://\S+', '링크', clean)
    clean = re.sub(r'<[^>]+>', '', clean)
    clean = re.sub(r'[*_~|>]', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean[:600]


async def _tts_queue_worker():
    global _VOICE_TTS_QUEUE
    while True:
        text = await _VOICE_TTS_QUEUE.get()
        try:
            await _tts_play_once(text)
        except Exception as e:
            print(f"[TTS 큐] 오류: {e}")
        finally:
            _VOICE_TTS_QUEUE.task_done()


async def _tts_play_once(text: str):
    global _VOICE_SINK
    import time as _time
    if not _VOICE_CLIENT or not _VOICE_CLIENT.is_connected():
        return
    clean = _clean_for_tts(text)
    if len(clean) < 2:
        return

    def _generate():
        resp = state.ai_client.audio.speech.create(
            model=_VOICE_TTS_MODEL, voice=_VOICE_TTS_VOICE,
            input=clean, speed=_VOICE_TTS_SPEED, response_format="mp3",
        )
        tmp = f"/tmp/manta_tts_{int(_time.time() * 1000)}.mp3"
        resp.stream_to_file(tmp)
        return tmp

    loop = asyncio.get_running_loop()
    tmp_path = await loop.run_in_executor(None, _generate)

    # TTS 재생 중 STT 에코 방지: 수신 일시 중단
    sink_to_restore = _VOICE_SINK if _VOICE_CLIENT.is_listening() else None
    if sink_to_restore:
        _VOICE_CLIENT.stop_listening()

    done_evt = asyncio.Event()
    def _after(err):
        try: os.remove(tmp_path)
        except Exception: pass
        loop.call_soon_threadsafe(done_evt.set)

    if _VOICE_CLIENT.is_playing():
        _VOICE_CLIENT.stop()
    source = discord.FFmpegPCMAudio(tmp_path, executable="/opt/homebrew/bin/ffmpeg")
    _VOICE_CLIENT.play(source, after=_after)
    await done_evt.wait()

    # STT 재개
    if sink_to_restore and _VOICE_USER_SPEAKS and _VOICE_CLIENT and _VOICE_CLIENT.is_connected():
        try:
            new_sink = _MantaVoiceSink(sink_to_restore.text_channel)
            _VOICE_SINK = new_sink
            _VOICE_CLIENT.listen(new_sink)
        except Exception as e:
            print(f"[TTS] STT 재개 실패: {e}")


async def _tts_speak(text: str):
    global _VOICE_TTS_QUEUE, _VOICE_TTS_WORKER
    if not _VOICE_MANTA_SPEAKS or not _VOICE_CLIENT:
        return
    if _VOICE_TTS_QUEUE is None:
        _VOICE_TTS_QUEUE = asyncio.Queue()
    if _VOICE_TTS_WORKER is None or _VOICE_TTS_WORKER.done():
        _VOICE_TTS_WORKER = asyncio.create_task(_tts_queue_worker())
    await _VOICE_TTS_QUEUE.put(text)


async def _join_voice_channel(channel: discord.VoiceChannel) -> bool:
    global _VOICE_CLIENT, _VOICE_TTS_QUEUE, _VOICE_TTS_WORKER
    try:
        if _VOICE_CLIENT and _VOICE_CLIENT.is_connected():
            await _VOICE_CLIENT.move_to(channel)
        else:
            _VOICE_CLIENT = await channel.connect(cls=voice_recv.VoiceRecvClient)

        if _VOICE_TTS_QUEUE is None:
            _VOICE_TTS_QUEUE = asyncio.Queue()
        if _VOICE_TTS_WORKER is None or _VOICE_TTS_WORKER.done():
            _VOICE_TTS_WORKER = asyncio.create_task(_tts_queue_worker())
        return True
    except Exception as e:
        print(f"[음성] 입장 실패: {e}")
        return False


async def _leave_voice_channel():
    global _VOICE_CLIENT, _VOICE_MANTA_SPEAKS, _VOICE_USER_SPEAKS, _VOICE_SINK
    if _VOICE_CLIENT and _VOICE_CLIENT.is_connected():
        _VOICE_CLIENT.stop_listening()
        await _VOICE_CLIENT.disconnect()
    _VOICE_CLIENT = None
    _VOICE_MANTA_SPEAKS = False
    _VOICE_USER_SPEAKS = False
    _VOICE_SINK = None


async def test_tts(text_channel) -> None:
    if not _VOICE_CLIENT or not _VOICE_CLIENT.is_connected():
        await text_channel.send("⚠️ 먼저 `!만타입장`으로 음성채널에 불러주세요.")
        return
    try:
        await _tts_play_once("만타 음성 테스트입니다. 잘 들리나요?")
        await text_channel.send("✅ TTS 생성과 재생이 정상적으로 끝났어요.")
    except Exception as e:
        await text_channel.send(f"❌ TTS 진단 실패: `{type(e).__name__}: {e}`")


# ── STT: 사용자 음성 → Whisper ─────────────────────────────────────────────────
class _MantaVoiceSink(voice_recv.AudioSink):
    _SILENCE_SEC = 0.3   # 침묵 감지 (0.5→0.3)으로 응답 속도 개선
    _MIN_MS      = 200   # 최소 음성 길이 (500→200ms)

    def __init__(self, text_channel):
        super().__init__()
        self.text_channel = text_channel
        self._loop = asyncio.get_running_loop()
        self._buffers: dict[int, bytearray] = {}
        self._last_spoke: dict[int, float] = {}
        self._processing: set[int] = set()

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData):
        import time
        if user is None or user.bot:
            return
        uid = user.id
        now = time.monotonic()
        if uid not in self._buffers:
            self._buffers[uid] = bytearray()
        self._buffers[uid].extend(data.pcm)
        self._last_spoke[uid] = now
        if uid not in self._processing:
            self._processing.add(uid)
            asyncio.run_coroutine_threadsafe(self._watch(user), self._loop)

    async def _watch(self, user):
        import time
        uid = user.id
        while True:
            await asyncio.sleep(self._SILENCE_SEC)
            if time.monotonic() - self._last_spoke.get(uid, 0) >= self._SILENCE_SEC:
                pcm = bytes(self._buffers.pop(uid, b''))
                self._last_spoke.pop(uid, None)
                self._processing.discard(uid)
                if pcm:
                    asyncio.create_task(self._transcribe(user, pcm))
                break

    async def _transcribe(self, user, pcm: bytes):
        import wave, tempfile
        if len(pcm) / (48000 * 2 * 2) * 1000 < self._MIN_MS:
            return
        loop = asyncio.get_running_loop()

        def _wav():
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            with wave.open(path, "wb") as wf:
                wf.setnchannels(2); wf.setsampwidth(2)
                wf.setframerate(48000); wf.writeframes(pcm)
            return path

        path = await loop.run_in_executor(None, _wav)

        def _whisper():
            try:
                with open(path, "rb") as f:
                    r = state.ai_client.audio.transcriptions.create(
                        model="whisper-1", file=f, language="ko"
                    )
                return r.text.strip()
            finally:
                try: os.remove(path)
                except Exception: pass

        try:
            text = await loop.run_in_executor(None, _whisper)
            if not text:
                return
            print(f"[STT] {user.display_name}: {text}")
            await self.text_channel.send(f"🎤 **{user.display_name}**: {text}")
            await _handle_stt_text(self.text_channel, text, user_id=user.id)
        except Exception as e:
            print(f"[STT] 오류: {e}")

    def cleanup(self):
        self._buffers.clear()


async def _handle_stt_text(channel, text: str, user_id: int = 0):
    """STT 텍스트 → GPT 처리 → TTS 응답 (도구 실행 포함, 중복 방지)"""
    import time as _t
    # 같은 사용자의 같은 텍스트가 4초 내 반복되면 무시
    now = _t.monotonic()
    prev_text, prev_ts = _stt_recent.get(user_id, ("", 0.0))
    if text == prev_text and now - prev_ts < 4.0:
        print(f"[STT] 중복 무시: {text!r}")
        return
    _stt_recent[user_id] = (text, now)

    loop = asyncio.get_running_loop()
    state.conversation_history.append({"role": "user", "content": text})

    # 방학 모드 등 현재 상태를 반영한 간결한 system prompt
    _today = datetime.now().strftime("%Y-%m-%d")
    _stt_sys = (
        f"너는 주인의 음성 비서 '만타(Manta)'야. 간결하고 자연스럽게 한국어 구어체로 대답해줘.\n"
        f"오늘 날짜: {_today}\n"
        + ("- 방학 모드 ON. LMS 관련 기능(lms_get_all_homework, lms_get_course_homework, scrap_lms_website의 LMS 접속)은 호출하지 말 것. 일정/과제는 캘린더만 조회.\n"
           if state._vacation_mode else "")
    )
    _stt_messages = [{"role": "system", "content": _stt_sys}] + state.conversation_history[-10:]

    try:
        response = state.ai_client.chat.completions.create(
            model="llama3.1",
            messages=_stt_messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            reply = msg.content or ""
            state.conversation_history.append({"role": "assistant", "content": reply})
            await _tts_speak(reply)
            return

        # ── 도구 실행 ─────────────────────────────────────────────────────────
        state.conversation_history.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            fname = tc.function.name
            try:
                fargs = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                fargs = {}

            if fname == "get_apple_calendar":
                result = await loop.run_in_executor(None, lambda a=fargs: get_apple_calendar(**a))
            elif fname == "get_weather":
                result = await loop.run_in_executor(None, lambda a=fargs: get_weather(**a))
            elif fname == "add_apple_calendar_event":
                result = await loop.run_in_executor(None, lambda a=fargs: add_apple_calendar_event(**a))
            elif fname == "modify_apple_calendar_event":
                result = await loop.run_in_executor(None, lambda a=fargs: modify_apple_calendar_event(**a))
            elif fname == "delete_apple_calendar_event":
                result = await loop.run_in_executor(None, lambda a=fargs: delete_apple_calendar_event(**a))
            elif fname == "delete_all_calendar_events_on_date":
                result = await loop.run_in_executor(None, lambda a=fargs: delete_all_calendar_events_on_date(**a))
            else:
                result = f"(음성으로 처리하기 어려운 기능: {fname})"

            state.conversation_history.append({
                "tool_call_id": tc.id,
                "role": "tool",
                "content": str(result),
            })

        # 2차 GPT 호출 → 최종 응답
        # 2차 호출: 같은 system prompt + 도구 결과 포함된 최신 히스토리
        _stt_messages2 = [{"role": "system", "content": _stt_sys}] + state.conversation_history[-12:]
        response2 = state.ai_client.chat.completions.create(
            model="llama3.1",
            messages=_stt_messages2,
        )
        reply = response2.choices[0].message.content or ""
        state.conversation_history.append({"role": "assistant", "content": reply})
        await _tts_speak(reply)

    except Exception as e:
        print(f"[STT GPT] 오류: {e}")


# ── 음성 모드 View (4버튼) ────────────────────────────────────────────────────
class VoiceModeView(discord.ui.View):
    def __init__(self, text_channel=None):
        super().__init__(timeout=120)
        self._text_channel = text_channel

    def _label(self):
        if _VOICE_MANTA_SPEAKS and _VOICE_USER_SPEAKS: return "🔊🎤 둘다"
        if _VOICE_MANTA_SPEAKS: return "🔊 만타만"
        if _VOICE_USER_SPEAKS:  return "🎤 나만"
        return "✖ 끄기"

    async def _apply(self, interaction: discord.Interaction, manta: bool, user: bool):
        global _VOICE_MANTA_SPEAKS, _VOICE_USER_SPEAKS, _VOICE_SINK
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        await interaction.response.defer()

        if not _VOICE_CLIENT or not _VOICE_CLIENT.is_connected():
            await interaction.edit_original_response(
                content="⚠️ 먼저 `!만타입장` 으로 음성채널에 불러주세요!", view=None
            )
            return

        prev_user = _VOICE_USER_SPEAKS
        _VOICE_MANTA_SPEAKS = manta
        _VOICE_USER_SPEAKS  = user
        status = ""

        # STT 시작
        if user and not prev_user:
            if not _VOICE_CLIENT.is_listening():
                ch = self._text_channel or interaction.channel
                try:
                    new_sink = _MantaVoiceSink(ch)
                    _VOICE_SINK = new_sink
                    _VOICE_CLIENT.listen(new_sink)
                except Exception as e:
                    status = f"\n⚠️ STT 시작 실패: {e}"
                    _VOICE_USER_SPEAKS = False
                    _VOICE_SINK = None

        # STT 중지
        elif not user and prev_user:
            _VOICE_CLIENT.stop_listening()
            _VOICE_SINK = None

        await interaction.edit_original_response(
            content=f"🎙️ **음성 모드** → {self._label()}{status}", view=None
        )

    @discord.ui.button(label="🔊 만타만", style=discord.ButtonStyle.primary, row=0)
    async def btn_manta(self, i, b): await self._apply(i, True, False)

    @discord.ui.button(label="🎤 나만", style=discord.ButtonStyle.secondary, row=0)
    async def btn_user(self, i, b): await self._apply(i, False, True)

    @discord.ui.button(label="🔊🎤 둘다", style=discord.ButtonStyle.success, row=0)
    async def btn_both(self, i, b): await self._apply(i, True, True)

    @discord.ui.button(label="✖ 끄기", style=discord.ButtonStyle.danger, row=0)
    async def btn_off(self, i, b): await self._apply(i, False, False)
