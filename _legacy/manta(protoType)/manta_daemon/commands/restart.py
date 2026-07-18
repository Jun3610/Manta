"""
commands/restart.py — 봇 재시작 / 종료 / 노션 세션 자동 기록
"""
import asyncio
import os
import subprocess
import sys

import manta_daemon.state as state
from manta_daemon.config import _PROJECT_ROOT


async def _save_bridge_session_to_notion(channel):
    """재시작 시 커밋 있으면 노션에 자동 기록 (확인 없이)"""
    from manta_daemon.integrations.notion import _get_next_bug_info, _create_bug_toggle_in_notion

    if not state.notion:
        state._bridge_session_log.clear()
        return

    def _get_session_commits():
        try:
            result = subprocess.run(
                ["git", "-C", _PROJECT_ROOT, "log",
                 "--oneline", "--since=3 hours ago", "--format=%h %s (%an, %ar)"],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.strip().splitlines()
            return lines if lines else []
        except Exception:
            return []

    commits = await asyncio.get_running_loop().run_in_executor(None, _get_session_commits)

    # 커밋도 없고 로그도 없으면 기록 불필요
    if not commits and not state._bridge_session_log:
        return

    log_text = "\n".join(state._bridge_session_log)
    summary_prompt = (
        "다음은 만타 봇 코드(manta_daemon.py) 수정 세션 기록이야.\n"
        "이걸 사람이 읽기 좋게 변경 로그로 정리해줘.\n\n"
        "형식:\n"
        "🐛 수정된 오류:\n- ...\n\n"
        "✨ 신규 기능:\n- ...\n\n"
        "🔧 기타 개선:\n- ...\n\n"
        "기술적인 내용은 사람 말로 풀어서, 없는 항목은 생략해. 한국어로.\n\n"
        f"세션 로그:\n{log_text[:4000]}"
    )
    try:
        summary_resp = state.ai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=800,
        )
        summary = summary_resp.choices[0].message.content.strip()
    except Exception as e:
        summary = f"(요약 실패: {e})"

    n, last_block_id = await asyncio.get_running_loop().run_in_executor(None, _get_next_bug_info)
    await channel.send(f"📝 노션에 **{n}차 버그** 자동 기록 중... (커밋 {len(commits)}개)")
    result = await asyncio.get_running_loop().run_in_executor(
        None, _create_bug_toggle_in_notion, n, last_block_id, summary, commits
    )
    state._bridge_session_log.clear()
    await channel.send(result)


async def _cmd_restart(channel):
    """봇 재시작 (커밋 있으면 노션 자동 기록 후 재시작)"""
    await _save_bridge_session_to_notion(channel)
    state._save_memory()
    with open("/tmp/.manta_restart_channel", "w") as _f:
        _f.write(str(channel.id))
    await channel.send("🔄 만타 재시작합니다. 잠깐만요!")
    await state.bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)


async def _cmd_shutdown(channel):
    """봇 종료 (대화 저장 후 종료)"""
    state._save_memory()
    await channel.send("💤 만타 종료합니다. 대화 저장 완료! 잘 자요~")
    await state.bot.close()
