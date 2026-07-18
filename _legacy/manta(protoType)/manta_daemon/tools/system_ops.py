"""
tools/system_ops.py — 앱 제어, 메일, 시스템 상태, 터미널, Python 실행
"""
import os
import subprocess
import time

import psutil
import pyautogui
import pyperclip

from manta_daemon.config import (
    ALLOWED_MAC_APPS, HOME, WORK_STATION_ROOT,
    ALLOWED_TERMINAL_CMDS, BLOCKED_TERMINAL_CMDS,
)
import manta_daemon.state as state


def open_mac_app(app_name):
    clean = app_name.strip().lower()
    matched = next((v for k, v in ALLOWED_MAC_APPS.items() if k in clean), None)
    if not matched:
        return f"❌ '{app_name}'은 허용된 앱 목록에 없어요!"
    try:
        subprocess.run(["open", "-a", matched], check=True)
        return f"✨ '{matched}' 켰어요!"
    except Exception as e:
        return f"😥 앱 실행 오류: {e}"


def quit_mac_app(app_name):
    clean = app_name.strip().lower()
    matched = next((v for k, v in ALLOWED_MAC_APPS.items() if k in clean), None)
    if not matched:
        return f"❌ '{app_name}'은 허용된 앱 목록에 없어요!"
    try:
        subprocess.run(['osascript', '-e', f'tell application "{matched}" to quit'], check=True)
        return f"🔴 '{matched}' 종료했어요!"
    except Exception as e:
        return f"😥 종료 오류: {e}"


def get_notion_app_context(target_app_name="Notion"):
    matched = next((v for k, v in ALLOWED_MAC_APPS.items() if k in target_app_name.lower()), None)
    if not matched:
        return f"❌ '{target_app_name}'은 허용 앱 목록에 없어요."
    try:
        subprocess.run(['osascript', '-e', f'tell application "{matched}" to activate'])
        time.sleep(0.8)
        original = pyperclip.paste()
        pyautogui.hotkey('command', 'a')
        time.sleep(0.3)
        pyautogui.hotkey('command', 'c')
        time.sleep(0.4)
        text = pyperclip.paste()
        pyperclip.copy(original)
        if not text or not text.strip():
            return f"'{matched}' 창이 비어 있거나 포커스가 없어요."
        return f"'{matched}' 앱 내용이에요! 👇\n{text[:3000]}"
    except Exception as e:
        return f"😥 {matched} 읽기 오류: {e}"


def read_mac_mail(count=5):
    try:
        script = f'''
        tell application "Mail"
            set output to ""
            set msgs to messages of inbox
            set n to count of msgs
            if n > {count} then set n to {count}
            repeat with i from 1 to n
                set m to item i of msgs
                set output to output & "---\\n제목: " & (subject of m) & "\\n보낸이: " & (sender of m) & "\\n날짜: " & ((date received of m) as string) & "\\n내용: " & (content of m) & "\\n"
            end repeat
            return output
        end tell
        '''
        result = subprocess.check_output(['osascript', '-e', script], timeout=15).decode('utf-8').strip()
        if not result:
            return "📭 받은 편지함에 메일이 없어요."
        return f"📬 **최근 메일 {count}개**\n\n{result[:3000]}"
    except subprocess.TimeoutExpired:
        return "⏱️ Mail 앱 응답 시간 초과."
    except Exception as e:
        return f"😥 메일 읽기 오류: {e}"


def get_system_status():
    """CPU / 메모리 / 디스크 상태 반환"""
    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        _data_vol = "/System/Volumes/Data"
        disk = psutil.disk_usage(_data_vol if os.path.isdir(_data_vol) else "/")
        battery = psutil.sensors_battery()
        bat_str = ""
        if battery:
            if battery.power_plugged and battery.percent >= 99:
                bat_label = "  🔌 완충 (AC 연결)"
            elif battery.power_plugged:
                bat_label = "  🔌 충전중"
            else:
                bat_label = ""
            bat_str = f"\n🔋 배터리: {battery.percent:.0f}%{bat_label}"
        return (
            f"🖥️ **시스템 상태**\n"
            f"CPU: {cpu}%\n"
            f"메모리: {mem.used // 1024**3:.1f}GB / {mem.total // 1024**3:.1f}GB ({mem.percent}%)\n"
            f"디스크: {disk.used // 1024**3:.1f}GB / {disk.total // 1024**3:.1f}GB ({disk.percent}%){bat_str}"
        )
    except Exception as e:
        return f"😥 시스템 상태 조회 오류: {e}"


def run_terminal_command(command: str):
    """화이트리스트 기반 터미널 명령 실행"""
    parts = command.strip().split()
    if not parts:
        return "❌ 명령어가 비어 있어요."
    base_cmd = parts[0].lower()

    cmd_str = command.lower()
    for blocked in BLOCKED_TERMINAL_CMDS:
        if blocked in cmd_str:
            return f"❌ 보안상 허용되지 않는 명령어예요: `{blocked}`"

    if base_cmd not in ALLOWED_TERMINAL_CMDS:
        return (
            f"❌ `{base_cmd}`은 허용 목록에 없어요.\n"
            f"허용 명령어: {', '.join(sorted(ALLOWED_TERMINAL_CMDS))}"
        )

    cwd = state.current_workspace["path"] if state.current_workspace else WORK_STATION_ROOT
    try:
        result = subprocess.run(
            parts,
            capture_output=True, text=True,
            timeout=15, cwd=cwd,
            env={**os.environ, "HOME": HOME}
        )
        out = (result.stdout + result.stderr).strip()
        if not out:
            return "✅ 명령 실행 완료 (출력 없음)"
        return f"```\n{out[:3000]}\n```"
    except subprocess.TimeoutExpired:
        return "⏱️ 명령 실행 시간 초과 (15초)"
    except Exception as e:
        return f"😥 실행 오류: {e}"


def run_python_code(code: str):
    """Python 코드 스니펫 실행 (타임아웃 샌드박스)"""
    try:
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True, text=True,
            timeout=10,
            cwd=state.current_workspace["path"] if state.current_workspace else WORK_STATION_ROOT,
            env={"HOME": HOME, "PATH": os.environ.get("PATH", "")}
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        lines = []
        if out:
            lines.append(f"**출력:**\n```\n{out[:2000]}\n```")
        if err:
            lines.append(f"**오류:**\n```\n{err[:500]}\n```")
        return "\n".join(lines) if lines else "✅ 실행 완료 (출력 없음)"
    except subprocess.TimeoutExpired:
        return "⏱️ 코드 실행 시간 초과 (10초)"
    except Exception as e:
        return f"😥 실행 오류: {e}"
