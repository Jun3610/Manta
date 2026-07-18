"""Discord 텍스트 메시지 라우팅과 LLM 도구 실행."""
import asyncio
import json
import os
import re
import signal
import subprocess
from datetime import datetime, timedelta

import discord

import manta_daemon.state as state
from manta_daemon import voice
from manta_daemon.config import (
    MY_DISCORD_UID, MANTA_CHANNEL_ID, LMS_CHANNEL_ID,
    SYSTEM_CHANNEL_ID, SCHEDULE_CHANNEL_ID, HEALTH_CHANNEL_ID, VOICE_CHANNEL_ID,
    WORK_STATION_ROOT, LMS_BASE, LMS_ID, LMS_PW,
    DISCORD_BOT_TOKEN, SITE_NAME_MAP,
    MAX_BUTTONS_PER_PAGE, MAX_WORKSPACE_BUTTONS, MAX_COURSE_BUTTONS,
)

# ── 유틸 ────────────────────────────────────────────────────────────────────
from manta_daemon.utils.helpers import (
    log_activity, send_long, send_as_file,
    _bring_discord_to_front, _offer_entertainment,
)
from manta_daemon.utils.errors import (
    report_error_to_discord, _check_openai_quota_error, _notify_openai_quota,
)

# ── UI Views ─────────────────────────────────────────────────────────────────
from manta_daemon.ui.views import (
    ConfirmView, RepoSelectView, WorkspaceSelectView,
    NotionDeleteView, LMSCourseSelectView,
    get_workspace_folders, delegate_write,
)

# ── GPT + Notion + LMS + Calendar + Weather ─────────────────────────────────
from manta_daemon.integrations.gpt import tools, analyze_and_save_profile
from manta_daemon.prompts import build_system_prompt
from manta_daemon.tool_executor import execute_tool_calls
from manta_daemon.integrations.notion import (
    create_notion_page, read_notion_page, update_notion_page,
    list_notion_subpages, delete_notion_page, append_to_notion_page,
)
from manta_daemon.integrations.lms import (
    lms_get_courses, lms_get_homework, lms_get_all_homework,
)
from manta_daemon.integrations.calendar_ops import (
    cal_db_init, cal_db_full_sync,
    user_db_init, load_user_profile_summary,
    get_apple_calendar, add_apple_calendar_event,
    modify_apple_calendar_event, delete_apple_calendar_event,
    delete_all_calendar_events_on_date,
)
from manta_daemon.integrations.weather import get_weather
from manta_daemon.integrations.health import handle_health_message
from manta_daemon.integrations.gmail import (
    _init_gmail, _gmail_poll_loop,
    _gmail_fetch_inbox_sync, _gmail_sender_name,
)

# ── 파일 / Git / 시스템 / 웹 ─────────────────────────────────────────────────
from manta_daemon.tools.file_ops import (
    find_local_file_for_discord, read_local_file, read_pdf,
    analyze_and_suggest_code, write_local_file, list_folder_contents,
)
from manta_daemon.tools.git_ops import (
    _find_git_repos, get_git_status_by_path, get_git_status,
)
from manta_daemon.tools.system_ops import (
    open_mac_app, quit_mac_app, get_notion_app_context,
    read_mac_mail, get_system_status, run_terminal_command, run_python_code,
)
from manta_daemon.tools.web_ops import web_confirm_gate, scrap_lms_website

# ── 태스크 ────────────────────────────────────────────────────────────────────
from manta_daemon.tasks.system_status import _system_embed_task
from manta_daemon.tasks.schedule import _status_topic_task
from manta_daemon.tasks.daily_report import _start_daily_report_when_ready, get_daily_briefing
from manta_daemon.tasks.timers import (
    _timer_task, _pomodoro_task,
    list_background_tasks, cancel_background_task,
)
from manta_daemon.tasks.reminders import parse_reminder, schedule_reminder

# ── 커맨드 ────────────────────────────────────────────────────────────────────
from manta_daemon.commands.restart import _cmd_restart, _cmd_shutdown
from manta_daemon.commands.vacation import _cmd_vacation, _cmd_vacation_end
from manta_daemon.commands.bridge import _handle_claude_bridge_oneshot



# ── on_message ───────────────────────────────────────────────────────────────

@state.bot.event
async def on_message(message):
    print(f"======================")
    print(f"[DEBUG] 메시지 들어옴! 텍스트: {message.content}")
    print(f"[DEBUG] 보낸사람: {message.author.id}, 설정된 MY_UID: {MY_DISCORD_UID}")
    print(f"[DEBUG] 채널: {message.channel.id}, 채널이름: {message.channel.name if hasattr(message.channel, 'name') else 'DM'}")
    print(f"======================")
    # 같은 메시지 ID 중복 처리 방지
    if message.id in state._processed_message_ids:
        print("[DEBUG] 중복 메시지 무시")
        return
    state._processed_message_ids.add(message.id)
    if len(state._processed_message_ids) > 200:
        state._processed_message_ids.clear()

    if state._daily_report_channel is None:
        state._daily_report_channel = message.channel
    if message.author == state.bot.user or message.author.id != MY_DISCORD_UID:
        return

    user_cmd = message.content.strip()

    # 모든 채널 공통 청소: 주인과 만타가 작성한 최근 메시지만 삭제
    if user_cmd.lower() in {"!청소", "청소", "클리어", "clear"}:
        try:
            start_msg = await message.channel.send("🧹 채널 정리를 시작할게요...")
            deleted = await message.channel.purge(
                limit=500,
                check=lambda m: m.author.id in {message.author.id, state.bot.user.id} and m.id != start_msg.id,
            )
            await start_msg.edit(content=f"🧹 채널 정리 완료! (총 {len(deleted)}개 삭제)")
        except Exception as e:
            deleted_count = 0
            start_msg = await message.channel.send("🧹 채널 정리를 시작할게요...")
            async for m in message.channel.history(limit=500):
                if m.author.id in {message.author.id, state.bot.user.id} and m.id != start_msg.id:
                    try:
                        await m.delete()
                        deleted_count += 1
                    except Exception:
                        pass
            await start_msg.edit(content=f"🧹 채널 정리 완료! (총 {deleted_count}개 삭제)")
        return

    # ── !버그리포트 ──
    if user_cmd == "!버그리포트":
        import os
        bug_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bug_report.txt")
        if os.path.exists(bug_file):
            with open(bug_file, "r", encoding="utf-8") as f:
                content = f.read()
            if len(content) > 1900:
                await message.channel.send("📋 **버그 리포트** (내용이 길어 마지막 일부만 표시합니다)\n```\n" + content[-1900:] + "\n```")
            else:
                await message.channel.send("📋 **버그 리포트**\n```\n" + (content or "내용 없음") + "\n```")
        else:
            await message.channel.send("📭 아직 누적된 버그 리포트가 없어요.")
        return

    # ── ! 명령어 채널 제한 체크 ──
    # ── ! 명령어 채널 제한 체크 ──
    _MANTA_CMDS = ("!만타재시작", "!만타종료", "!만타수정", "!tts", "!버그리포트")
    _LMS_CMDS   = ("!방학", "!개강")
    _ALL_BANG_CMDS = _MANTA_CMDS + _LMS_CMDS

    if user_cmd.startswith("!") and any(user_cmd == c or user_cmd.startswith(c) for c in _ALL_BANG_CMDS):
        is_manta_cmd = any(user_cmd == c or user_cmd.startswith(c) for c in _MANTA_CMDS)
        is_lms_cmd   = any(user_cmd == c or user_cmd.startswith(c) for c in _LMS_CMDS)

        wrong_channel = False
        guide = ""
        if is_manta_cmd and message.channel.id != MANTA_CHANNEL_ID:
            wrong_channel = True
            manta_ch = f"<#{MANTA_CHANNEL_ID}>" if MANTA_CHANNEL_ID else "#manta"
            guide = (
                f"⛔ **만타 전용 명령어**예요.\n"
                f"{manta_ch} 채널에서만 사용할 수 있어요.\n\n"
                f"**사용 가능한 명령어:**\n"
                f"`!만타재시작` `!만타종료` `!만타수정 [프롬프트]`"
            )
        elif is_lms_cmd and message.channel.id != LMS_CHANNEL_ID:
            wrong_channel = True
            lms_ch = f"<#{LMS_CHANNEL_ID}>"
            guide = (
                f"⛔ **LMS 전용 명령어**예요.\n"
                f"{lms_ch} 채널에서만 사용할 수 있어요.\n\n"
                f"**사용 가능한 명령어:**\n"
                f"`!방학 YYYY-MM-DD` `!개강`"
            )

        if wrong_channel:
            warn = await message.channel.send(guide)

            async def _delete_warn():
                await asyncio.sleep(5)
                try:
                    await message.delete()
                except Exception:
                    pass
                try:
                    await warn.delete()
                except Exception:
                    pass

            asyncio.create_task(_delete_warn())
            return

    # ── 시스템 채널: 배너 전용 — 유저 메시지 즉시 삭제 ──
    if message.channel.id == SYSTEM_CHANNEL_ID:
        try:
            await message.delete()
        except Exception:
            pass
        return

    # ── 음성채널 명령어 (어느 채널에서나 동작) ──────────────────────────────────
    if user_cmd == "!만타입장":
        target_vc = None
        # 유저가 이미 음성채널에 있으면 거기로, 없으면 환경변수 채널로
        member = message.guild.get_member(message.author.id) if message.guild else None
        if member and member.voice and member.voice.channel:
            target_vc = member.voice.channel
        elif VOICE_CHANNEL_ID:
            target_vc = message.guild.get_channel(VOICE_CHANNEL_ID) if message.guild else None

        if not target_vc:
            await message.channel.send("⚠️ 음성채널을 찾을 수 없어요. 먼저 음성채널에 들어가 주세요!")
        else:
            ok = await voice._join_voice_channel(target_vc)
            if ok:
                view = voice.VoiceModeView(text_channel=message.channel)
                await message.channel.send(
                    f"✅ **{target_vc.name}** 입장!\n"
                    f"🎙️ 음성 모드를 선택해줘요:",
                    view=view,
                )
            else:
                await message.channel.send("❌ 음성채널 입장에 실패했어요.")
        return

    if user_cmd == "!만타퇴장":
        if voice._VOICE_CLIENT and voice._VOICE_CLIENT.is_connected():
            ch_name = voice._VOICE_CLIENT.channel.name if voice._VOICE_CLIENT.channel else "음성채널"
            await voice._leave_voice_channel()
            await message.channel.send(f"👋 **{ch_name}** 에서 나왔어요.")
        else:
            await message.channel.send("현재 음성채널에 있지 않아요.")
        return

    # ── 음성채널 텍스트: !만타입장/퇴장 외 모든 명령어 차단 ─────────────────────
    if VOICE_CHANNEL_ID and message.channel.id == VOICE_CHANNEL_ID:
        if user_cmd.startswith("!"):
            try:
                await message.delete()
            except Exception:
                pass
            guide_msg = await message.channel.send(
                "🎙️ 이 채팅방은 음성 전용이에요.\n"
                "`!만타입장` — 만타 음성채널 입장 + 모드 선택\n"
                "`!만타퇴장` — 음성채널 퇴장",
                delete_after=6,
            )
        return

    # ── 만타 메인 채널: ! 명령어 처리 ──
    if MANTA_CHANNEL_ID and message.channel.id == MANTA_CHANNEL_ID:
        if user_cmd == "!만타재시작":
            await _cmd_restart(message.channel)
            return
        elif user_cmd == "!만타종료":
            await _cmd_shutdown(message.channel)
            return
        elif user_cmd == "!tts":
            if voice._VOICE_MANTA_SPEAKS and voice._VOICE_USER_SPEAKS: mode = "🔊🎤 둘다"
            elif voice._VOICE_MANTA_SPEAKS: mode = "🔊 만타만"
            elif voice._VOICE_USER_SPEAKS:  mode = "🎤 나만"
            else: mode = "✖ 끄기"
            view = voice.VoiceModeView(text_channel=message.channel)
            await message.channel.send(f"🎙️ **음성 모드** 현재: {mode}", view=view)
            return
        elif user_cmd == "!tts테스트":
            await voice.test_tts(message.channel)
            return
        elif user_cmd == "!만타기록":
            from manta_daemon.commands.restart import _save_bridge_session_to_notion
            await _save_bridge_session_to_notion(message.channel)
            return
        elif user_cmd.startswith("!만타수정"):
            prompt = user_cmd[len("!만타수정"):].strip()
            has_images = any(
                att.content_type and att.content_type.startswith("image/")
                for att in message.attachments
            )
            if not prompt and not has_images:
                await message.channel.send("프롬프트나 이미지를 같이 보내줘요!\n예: `!만타수정 버그 수정해줘`")
            else:
                log_entry = prompt if prompt else f"(이미지 {sum(1 for a in message.attachments if a.content_type and a.content_type.startswith('image/'))}장)"
                state._bridge_session_log.append(f"[{datetime.now().strftime('%H:%M')}] {log_entry}")
                await _handle_claude_bridge_oneshot(message.channel, prompt, message.attachments)
            return
        elif user_cmd.startswith("!"):
            # 인식 못한 ! 명령어만 안내 (일반 대화는 GPT로 넘어감)
            await message.channel.send(
                "이 채널 전용 명령어:\n"
                "`!만타재시작` `!만타종료` `!만타수정 [프롬프트]` `!만타기록` `!tts`\n"
                "어느 채널: `!만타입장` `!만타퇴장`",
                delete_after=8,
            )
            return
        # 일반 메시지 → GPT로 fall-through

    # ── LMS 채널: !방학 / !개강 처리 후 LMS 질문은 GPT로 fall-through ──
    if message.channel.id == LMS_CHANNEL_ID:
        if user_cmd.startswith("!방학"):
            parts = user_cmd.split(maxsplit=1)
            date_arg = parts[1].strip() if len(parts) > 1 else ""
            await _cmd_vacation(message.channel, date_arg)
            return
        elif user_cmd == "!개강":
            await _cmd_vacation_end(message.channel)
            return
        # LMS 관련 질문 → GPT로 fall-through (채널 제한은 system_prompt에서)

    # ── Health 채널: 전용 핸들러로 처리 (GPT로 넘기지 않음) ──────────────────
    if message.channel.id == HEALTH_CHANNEL_ID:
        await handle_health_message(message)
        return

    log_activity("유저 활동", f"메시지 수신: '{user_cmd}'")

    # ── PDF 첨부파일 드롭 처리 ──
    if message.attachments:
        for att in message.attachments:
            if any(att.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                import base64
                img_bytes = await att.read()
                img_b64 = base64.b64encode(img_bytes).decode('utf-8')
                mime_type = "image/jpeg"
                if att.filename.lower().endswith(".png"): mime_type = "image/png"
                elif att.filename.lower().endswith(".gif"): mime_type = "image/gif"
                elif att.filename.lower().endswith(".webp"): mime_type = "image/webp"
                
                b64_url = f"data:{mime_type};base64,{img_b64}"
                
                vision_messages = [
                    {"role": "system", "content": (
                        f"오늘 날짜: {datetime.now().strftime('%Y-%m-%d')}. "
                        "이미지에서 일정/할일/과제/날짜 정보를 추출해. "
                        "일정이 여러 개면 각 줄에 '제목 | YYYY-MM-DD | HH:MM | 비고' 형식으로. "
                        "일정이 없으면 이미지 내용을 간단히 설명해."
                    )},
                    {"role": "user", "content": [
                        {"type": "text", "text": user_cmd or "이 이미지에서 일정 정보를 추출해줘"},
                        {"type": "image_url", "image_url": {"url": b64_url, "detail": "high"}}
                    ]}
                ]
                async with message.channel.typing():
                    loop = asyncio.get_running_loop()
                    vision_resp = await loop.run_in_executor(
                        None,
                        lambda: state.ai_client.chat.completions.create(
                            model="llama3.2-vision", messages=vision_messages
                        )
                    )
                vision_text = vision_resp.choices[0].message.content or ""
                state.conversation_history.append({"role": "user", "content": f"[이미지 분석 결과]\n{vision_text}"})
                if any(kw in user_cmd for kw in ["추가", "넣어", "등록", "캘린더", "일정"]):
                    await send_long(message.channel,
                        f"🖼️ **이미지에서 추출한 일정**\n\n{vision_text}\n\n"
                        "바로 캘린더에 추가할게요! (제목/날짜/시간 확인 후 추가)"
                    )
                    user_cmd = f"위 이미지에서 추출한 일정들을 모두 캘린더에 추가해줘: {vision_text}"
                else:
                    await send_long(message.channel, f"🖼️ **이미지 분석**\n\n{vision_text}")
                    if not user_cmd:
                        return
                continue
            if att.filename.lower().endswith(".pdf"):
                await message.channel.send(f"📄 PDF 받았어요! `{att.filename}` 다운로드 중...")
                save_dir = os.path.join(WORK_STATION_ROOT, ".manta_pdfs")
                os.makedirs(save_dir, exist_ok=True)
                save_path = os.path.join(save_dir, att.filename)
                pdf_bytes = await att.read()
                with open(save_path, "wb") as f:
                    f.write(pdf_bytes)
                result = read_pdf(save_path)
                if "error" in result:
                    await send_long(message.channel, result["error"])
                else:
                    total = result["total_pages"]
                    name = result["name"]
                    preview = result["content"][:800]
                    await send_long(
                        message.channel,
                        f"📖 **`{name}` 열었어요!** (총 {total}페이지)\n\n"
                        f"```\n{preview}\n```\n"
                        f"{'...(이후 생략)' if len(result['content']) > 800 else ''}\n\n"
                        f"이제 질문하면 같이 볼게요!"
                    )
                    state.conversation_history.append({
                        "role": "assistant",
                        "content": f"PDF '{name}'을 열었어요. 총 {total}페이지."
                    })
                if not user_cmd:
                    return

    reminder = parse_reminder(user_cmd)
    if reminder:
        fire_at, label = reminder
        await message.channel.send(schedule_reminder(message.channel, fire_at, label))
        return

    # ── 백그라운드 작업 목록/취소 ──
    if any(kw in user_cmd for kw in ["백그라운드 작업", "background 작업", "실행중인 작업", "작업 목록"]):
        await message.channel.send(list_background_tasks())
        return
    _pomo_cancel_kws = ["뽀모도로 꺼", "뽀모도로 종료", "뽀모도로 취소", "뽀모도로 멈춰", "뽀모도로 중단",
                        "포모도로 꺼", "포모도로 종료", "포모도로 취소", "pomodoro 꺼", "pomodoro 종료"]
    if any(kw in user_cmd for kw in _pomo_cancel_kws):
        await message.channel.send(cancel_background_task("pomodoro"))
        return
    cancel_match = re.search(r"(작업|타이머|알람|데일리|리포트)\s*(.+?)\s*(취소|종료|멈춰|중단|꺼줘|꺼)", user_cmd)
    if cancel_match:
        target = cancel_match.group(2).strip()
        await message.channel.send(cancel_background_task(target))
        return

    # ── 작업공간 진입 명령 ──
    workspace_enter_cmds = ["작업", "작업공간", "폴더 선택", "workspace", "작업 선택", "작업폴더"]
    if any(user_cmd == cmd or user_cmd.startswith(cmd + " ") for cmd in workspace_enter_cmds):
        folders = get_workspace_folders()
        if not folders:
            await message.channel.send("📂 work-station 안에 폴더가 없어요!")
            return
        view = WorkspaceSelectView(folders)
        total_pages = max(1, (len(folders) - 1) // MAX_WORKSPACE_BUTTONS + 1)
        page_info = f" — 1/{total_pages}페이지" if total_pages > 1 else ""
        ws_status = f"\n> 현재: 🎯 `{state.current_workspace['name']}`" if state.current_workspace else ""
        sent = await message.channel.send(
            content=f"📂 **작업공간 선택** ({len(folders)}개 폴더){page_info}{ws_status}\n어느 폴더에서 작업할까요?",
            view=view
        )
        view.origin_message = sent
        return

    # ── 작업공간 나가기 명령 ──
    workspace_exit_cmds = ["나가자", "작업 종료", "작업공간 나가", "workspace 종료", "폴더 나가", "나가기", "작업 나가"]
    if any(user_cmd == cmd or user_cmd.startswith(cmd) for cmd in workspace_exit_cmds):
        if state.current_workspace:
            old = state.current_workspace["name"]
            state.current_workspace = None
            state.current_context = {}
            state.conversation_history = []
            await message.channel.send(f"👋 **`{old}`** 작업공간에서 나왔어요!\n이제 work-station 전체를 탐색할게요.")
        else:
            await message.channel.send("지금 특정 작업공간에 있지 않아요!")
        return

    # ── 뽀모도로 명령 (시작) ──
    _pomo_stop_kws = ["꺼", "종료", "취소", "멈춰", "중단"]
    pomo_match = re.match(r"(뽀모도로|포모도로|pomodoro)\s*(\d+)?회?(?:\s*(\d+)분?작업)?(?:\s*(\d+)분?휴식)?", user_cmd, re.I)
    if pomo_match and not any(kw in user_cmd for kw in _pomo_stop_kws):
        rounds    = int(pomo_match.group(2) or 4)
        work_min  = int(pomo_match.group(3) or 25)
        break_min = int(pomo_match.group(4) or 5)
        if "pomodoro" in state._active_timers:
            state._active_timers["pomodoro"].cancel()
        task = asyncio.create_task(_pomodoro_task(message.channel, work_min, break_min, rounds))
        state._active_timers["pomodoro"] = task
        state._timer_meta["pomodoro"] = {
            "label": f"뽀모도로 {rounds}회 {work_min}분/{break_min}분",
            "started": datetime.now().strftime("%H:%M")
        }
        await message.channel.send(
            f"🍅 **뽀모도로 시작!** {rounds}회 × (집중 {work_min}분 + 휴식 {break_min}분)"
        )
        return

    # ── 리마인더 명령 ──
    remind_match = re.search(r"(\d+)\s*(분|시간)\s*(후|뒤)에?\s*(.+)\s*(알려줘|알림|remind)", user_cmd)
    if remind_match:
        amount  = int(remind_match.group(1))
        unit    = remind_match.group(2)
        label   = remind_match.group(4).strip() or "리마인더"
        minutes = amount if unit == "분" else amount * 60
        task = asyncio.create_task(_timer_task(message.channel, minutes, label))
        state._active_timers[label] = task
        state._timer_meta[label] = {"label": label, "started": datetime.now().strftime("%H:%M")}
        await message.channel.send(f"⏰ **{minutes}분 후** `{label}` 알려드릴게요!")
        return

    # ── LMS 강의 선택 명령 ──
    lms_cmds = ["lms", "강의", "강의 선택", "과목", "수업", "강의목록", "lms 강의"]
    if any(user_cmd == cmd or user_cmd.startswith(cmd + " ") for cmd in lms_cmds):
        if not LMS_ID or not LMS_PW:
            await message.channel.send(
                "❌ `.env`에 `LMS_ID`와 `LMS_PW`가 없어요!\n"
                "```\nLMS_ID=학번\nLMS_PW=비밀번호\n```\n추가한 뒤 재시작해줘요."
            )
            return
        async with message.channel.typing():
            await message.channel.send("🔄 LMS 로그인 중...")
            courses, err = lms_get_courses()
            if err and not courses:
                await send_long(message.channel, err)
                return
            if not courses:
                await message.channel.send("📭 수강 중인 강의가 없어요!")
                return
            total_pages = max(1, (len(courses) - 1) // MAX_COURSE_BUTTONS + 1)
            page_info = f" — 1/{total_pages}페이지" if total_pages > 1 else ""
            cur = f"\n> 현재: 📚 `{state.lms_current_course['name']}`" if state.lms_current_course else ""
            view = LMSCourseSelectView(courses)
            await message.channel.send(
                content=f"📚 **수강 중인 강의** ({len(courses)}개){page_info}{cur}\n어떤 강의를 볼까요?",
                view=view
            )
        return

    state.conversation_history.append({"role": "user", "content": user_cmd})

    async with message.channel.typing():
        try:
            # --- [Dynamic Tool Injection & Routing] ---
            import time
            from manta_daemon.integrations.gpt import get_route_category, get_tools_for_category

            start_total = time.time()
            start_router = time.time()
            route_cats = get_route_category(user_cmd, message.channel.id)
            router_time = time.time() - start_router
            
            print(f"\n======================")
            print(f"[Router] Input: {user_cmd}")
            print(f"[Router] Category: {route_cats} (Time: {router_time:.2f}s)")
            
            injected_tools = []
            for cat in route_cats:
                injected_tools.extend(get_tools_for_category(cat))
                
            tool_choice = "auto" if injected_tools else "none"
            print(f"[Router] Injected Tools: {len(injected_tools)}")
            print(f"======================\n")

            system_prompt = build_system_prompt(message.channel.id, route_cats)

            # 대화 히스토리는 최근 4턴(2왕복)만 사용 (토큰 절약)
            messages_for_gpt = [{"role": "system", "content": system_prompt}] + state.conversation_history[-4:]

            # ── 1차 GPT 호출 (로컬 Llama 3.1 사용, 비동기 블로킹 방지) ──
            loop = asyncio.get_running_loop()
            
            def call_llm():
                return state.ai_client.chat.completions.create(
                    model="llama3.1",
                    messages=messages_for_gpt,
                    tools=injected_tools if injected_tools else None,
                    tool_choice=tool_choice
                )
                
            start_llm = time.time()
            response = await loop.run_in_executor(None, call_llm)
            llm_time = time.time() - start_llm

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            if not tool_calls:
                ai_reply = response_message.content or ""
                
                # [P0] Tool Call 실패 시 Raw JSON 노출 방지
                if '{"name":' in ai_reply or '{"arguments":' in ai_reply:
                    ai_reply = "앗, 요청하신 내용을 처리하다가 제가 헷갈렸어요! (양식 오류) 조금만 다르게 다시 말씀해주실래요?"
                
                state.conversation_history.append({"role": "assistant", "content": ai_reply})
                await send_long(message.channel, ai_reply)
                asyncio.create_task(voice._tts_speak(ai_reply))
                asyncio.ensure_future(asyncio.get_running_loop().run_in_executor(
                    None, analyze_and_save_profile, user_cmd, ai_reply
                ))
                return
            
            start_tool = time.time()
            await execute_tool_calls(message, tool_calls, user_cmd)
            tool_time = time.time() - start_tool
            
            total_time = time.time() - start_total
            print(f"\n[Performance Summary]")
            print(f" - Router: {router_time:.2f}s")
            print(f" - Main LLM: {llm_time:.2f}s (Prompt Length: {len(system_prompt)}, History Tns: {len(messages_for_gpt)}, Tools: {len(injected_tools)})")
            print(f" - Tool Exec: {tool_time:.2f}s")
            print(f" - Total: {total_time:.2f}s\n")

        except discord.HTTPException as e:
            log_activity("Discord 오류", str(e))
            await message.channel.send(f"🚨 Discord 전송 오류: {e}")
        except Exception as e:
            log_activity("런타임 오류", str(e))
            if _check_openai_quota_error(e):
                await _notify_openai_quota()
            else:
                await message.channel.send(f"🚨 런타임 오류: {e}")
                
            # [P2] 버그 리포트 연동 컨텍스트 고도화
            from manta_daemon.utils.errors import report_error_to_discord
            error_ctx = (
                f"사용자 입력: {user_cmd}\n"
                f"Router 카테고리: {locals().get('route_cats', 'N/A')}\n"
                f"Injected Tools: {len(locals().get('injected_tools', []))}개\n"
            )
            asyncio.create_task(report_error_to_discord(e, context=error_ctx))
