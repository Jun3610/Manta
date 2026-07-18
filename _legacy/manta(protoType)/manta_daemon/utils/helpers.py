"""
utils/helpers.py — send_long, send_as_file, log_activity, _bring_discord_to_front 등
"""
import os
import io
import subprocess
from datetime import datetime

import discord

from manta_daemon.config import _LOG_FILE, ENTERTAINMENT_SERVICES


def log_activity(action_type, details):
    current_time = datetime.now().strftime('%H:%M:%S')
    line = f"[{current_time}] [{action_type}] {details}"
    print(line)
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 로그 파일 최대 500줄 유지
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 500:
            with open(_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-300:])
    except Exception:
        pass


async def send_long(channel, text, chunk_size=1900):
    """2000자 Discord 제한을 넘는 텍스트를 청크로 분할해서 전송"""
    text = str(text)
    if not text.strip():
        return
    for i in range(0, len(text), chunk_size):
        await channel.send(text[i:i + chunk_size])


async def send_as_file(channel, text, filename, prefix=""):
    """텍스트가 너무 길면 파일로 전송"""
    buf = io.BytesIO(text.encode("utf-8"))
    f = discord.File(fp=buf, filename=filename)
    await channel.send(content=prefix, file=f)


def _bring_discord_to_front():
    """작업 완료 시 Discord를 최전면으로"""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Discord" to activate'],
            timeout=5, capture_output=True
        )
    except Exception:
        pass


def _open_entertainment_service(key: str) -> str:
    """엔터테인먼트 서비스 열기 (브라우저 URL 또는 앱)"""
    svc = ENTERTAINMENT_SERVICES.get(key)
    if not svc:
        return f"❌ 알 수 없는 서비스: {key}"
    try:
        if svc["app"]:
            subprocess.run(["open", "-a", svc["app"]], check=True)
        else:
            subprocess.run(["open", svc["url"]], check=True)
        return f"{svc['emoji']} {svc['app'] or svc['url']} 켰어요!"
    except Exception as e:
        return f"😥 실행 오류: {e}"


async def _offer_entertainment(channel):
    """오래 걸리는 작업 시작 시 봇 상태 변경"""
    from manta_daemon.state import bot
    await bot.change_presence(activity=discord.Game(name="⏳ 작업 처리 중..."))


async def _notify_task_done(channel, summary: str = ""):
    """작업 완료 후 Discord 최전면 + 완료 메시지"""
    _bring_discord_to_front()
    msg = "✅ 작업 완료했어요!"
    if summary:
        msg += f"\n{summary}"
    await channel.send(msg)
