"""
tasks/timers.py — 타이머 / 뽀모도로 / 백그라운드 작업 관리
"""
import asyncio
from datetime import datetime, timedelta

import manta_daemon.state as state


# ── 타이머 ──────────────────────────────────────────────────────────────────

async def _timer_task(channel, minutes: int, label: str):
    """단순 카운트다운 타이머"""
    await asyncio.sleep(minutes * 60)
    await channel.send(f"⏰ **{label}** 시간 됐어요! ({minutes}분 경과)")
    state._active_timers.pop(label, None)


# ── 뽀모도로 ─────────────────────────────────────────────────────────────────

async def _pomodoro_task(channel, work_min: int, break_min: int, rounds: int):
    """뽀모도로 타이머 (집중 → 휴식 반복)"""
    for i in range(1, rounds + 1):
        await channel.send(f"🍅 **뽀모도로 {i}회차 시작!** {work_min}분 집중해요!")
        await asyncio.sleep(work_min * 60)
        if i < rounds:
            await channel.send(f"☕ **{i}회차 완료!** {break_min}분 휴식해요~")
            await asyncio.sleep(break_min * 60)
        else:
            await channel.send(f"🎉 **뽀모도로 {rounds}회 전부 완료!** 수고했어요!")
    state._active_timers.pop("pomodoro", None)


# ── 백그라운드 작업 관리 ──────────────────────────────────────────────────────

def list_background_tasks() -> str:
    """현재 실행 중인 백그라운드 작업 목록 반환"""
    # 완료된 작업 먼저 정리
    done_keys = [k for k, t in state._active_timers.items() if t.done()]
    for k in done_keys:
        state._active_timers.pop(k, None)
        state._timer_meta.pop(k, None)

    tasks = []
    for name, task in state._active_timers.items():
        if not task.done():
            meta = state._timer_meta.get(name, {})
            label = meta.get("label", name)
            started = meta.get("started", "?")
            tasks.append(f"⏱ `{name}` — {label} (시작: {started})")

    if state._daily_report_task_ref and not state._daily_report_task_ref.done():
        tasks.append("🌙 `daily_report` — 매일 자정 당일 일정 자동 보고")

    if not tasks:
        return "📭 현재 실행 중인 백그라운드 작업이 없어요."

    return (
        "📋 **백그라운드 작업 목록**\n\n"
        + "\n".join(tasks)
        + "\n\n종료하려면 작업 이름을 알려줘요. (예: `pomodoro 종료`, `daily_report 종료`)"
    )


def cancel_background_task(name: str) -> str:
    """이름으로 백그라운드 작업 취소"""
    name_lower = name.strip().lower()

    # daily_report 취소
    if any(k in name_lower for k in ("daily", "9시", "아침", "리포트", "자정")):
        if state._daily_report_task_ref and not state._daily_report_task_ref.done():
            state._daily_report_task_ref.cancel()
            state._daily_report_task_ref = None
            return "✅ 데일리 리포트 취소했어요."
        return "❌ 실행 중인 데일리 리포트가 없어요."

    # _active_timers에서 검색
    matched = None
    for key in list(state._active_timers.keys()):
        if name_lower in key.lower() or key.lower() in name_lower:
            matched = key
            break

    if matched:
        task = state._active_timers.pop(matched)
        state._timer_meta.pop(matched, None)
        task.cancel()
        return f"✅ `{matched}` 작업 취소했어요."

    return (
        f"❌ `{name}` 이름의 작업을 찾지 못했어요. "
        f"`백그라운드 작업 뭐있어`로 목록 확인해봐요."
    )
