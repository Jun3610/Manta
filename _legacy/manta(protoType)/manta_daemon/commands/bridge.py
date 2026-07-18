"""
commands/bridge.py — Claude Desktop 브릿지 (!만타수정)
UI 탐색 없이 창 하단 좌표 클릭으로 입력창 포커싱 → 붙여넣기+전송
"""
import asyncio
import os
import subprocess

import manta_daemon.state as state

# Claude Desktop 입력창: 창 하단에서 위로 70px, 가로 중앙
_INPUT_OFFSET_FROM_BOTTOM = 70

_ACTIVATE_AND_PASTE_SCRIPT = """
tell application "Claude" to activate
delay 1.0

tell application "System Events"
    tell process "Claude"
        set frontmost to true
        delay 0.4

        -- 창 위치/크기로 입력창 좌표 계산 (항상 창 하단)
        set w to window 1
        set pos to position of w
        set sz to size of w
        set clickX to (item 1 of pos) + (item 1 of sz) / 2
        set clickY to (item 2 of pos) + (item 2 of sz) - {offset}

        click at {{clickX as integer, clickY as integer}}
        delay 0.4

        keystroke "v" using command down
        delay 0.4
        key code 36
    end tell
end tell
""".format(offset=_INPUT_OFFSET_FROM_BOTTOM)

_PASTE_IMAGE_SCRIPT = """
tell application "System Events"
    tell process "Claude"
        keystroke "v" using command down
        delay 0.6
        key code 36
    end tell
end tell
"""

_DISCORD_FOCUS = 'tell application "Discord" to activate'


def _run_as(script: str, timeout: int = 15) -> str | None:
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            check=True, timeout=timeout, capture_output=True,
        )
        return None
    except subprocess.CalledProcessError as e:
        return (e.stderr or b"").decode(errors="ignore").strip()[:300]
    except subprocess.TimeoutExpired:
        return f"타임아웃 ({timeout}초)"
    except Exception as e:
        return str(e)


async def _handle_claude_bridge_oneshot(channel, prompt: str, attachments=None):
    """!만타수정 → Claude Desktop에 텍스트+이미지 전달 → Discord 재포커싱"""
    _IMG_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "heif"}

    # ── 이미지 첨부 다운로드 ───────────────────────────────────────────────────
    image_paths: list[str] = []
    if attachments:
        for att in attachments:
            ext = att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else ""
            if (att.content_type and att.content_type.startswith("image/")) or ext in _IMG_EXTS:
                tmp = f"/tmp/manta_bridge_{att.id}.{ext or 'png'}"
                try:
                    with open(tmp, "wb") as f:
                        f.write(await att.read())
                    image_paths.append(tmp)
                except Exception as e:
                    await channel.send(f"⚠️ 이미지 다운로드 실패: {e}")

    full_prompt = (prompt or "").strip()
    if not full_prompt and not image_paths:
        await channel.send("프롬프트나 이미지를 같이 보내줘요!\n예: `!만타수정 버그 고쳐줘`")
        return

    preview = full_prompt[:200] if full_prompt else f"(이미지 {len(image_paths)}장)"
    img_tag = f" + 🖼 {len(image_paths)}장" if image_paths else ""
    await channel.send(f"📋 Claude Desktop에 전달 중...\n> {preview}{img_tag}")

    loop = asyncio.get_running_loop()

    # ── 텍스트 붙여넣기 ────────────────────────────────────────────────────────
    if full_prompt:
        try:
            subprocess.run(["pbcopy"], input=full_prompt.encode("utf-8"), check=True)
        except Exception as e:
            await channel.send(f"❌ 클립보드 복사 실패: {e}")
            return

        err = await loop.run_in_executor(None, lambda: _run_as(_ACTIVATE_AND_PASTE_SCRIPT))
        if err:
            await channel.send(
                f"❌ 전달 실패: {err}\n"
                "확인: **시스템 설정 → 손쉬운 사용**에 Terminal이 체크돼 있어야 해요."
            )
            return

    # ── 이미지 붙여넣기 ────────────────────────────────────────────────────────
    for img_path in image_paths:
        await asyncio.sleep(0.5)
        ext = os.path.splitext(img_path)[1].lower()
        img_type = "«class PNGf»" if ext == ".png" else "JPEG picture"
        set_clip = f'set the clipboard to (read (POSIX file "{img_path}") as {img_type})'
        err = await loop.run_in_executor(None, lambda s=set_clip: _run_as(s, timeout=5))
        if err:
            await channel.send(f"⚠️ 이미지 클립보드 실패: {err}")
            continue
        err = await loop.run_in_executor(None, lambda: _run_as(_PASTE_IMAGE_SCRIPT, timeout=5))
        if err:
            await channel.send(f"⚠️ 이미지 붙여넣기 실패: {err}")

    state._bridge_session_log.append(f"[→Claude Desktop] {preview}")

    await asyncio.sleep(2)
    await loop.run_in_executor(None, lambda: _run_as(_DISCORD_FOCUS, timeout=5))
    await channel.send(f"✅ Claude Desktop에 전달했어요!{img_tag}")
