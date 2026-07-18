"""
tasks/daily_report.py — 매일 자정 일정/LMS 자동 보고
"""
import asyncio
from datetime import datetime, timedelta

import manta_daemon.state as state
from manta_daemon.config import SCHEDULE_CHANNEL_ID, LMS_CHANNEL_ID
from manta_daemon.utils.helpers import _bring_discord_to_front


async def _daily_report_task(channel):
    """매일 자정(00:00)에 오늘 하루 수고 메시지 + 당일 일정 + LMS 미제출 자동 보고"""
    while True:
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_sec = (tomorrow - now).total_seconds()
        await asyncio.sleep(wait_sec)

        _bring_discord_to_front()

        today_str = datetime.now().strftime("%Y-%m-%d")
        if state._vacation_mode and state._vacation_end_date and today_str >= state._vacation_end_date:
            state._vacation_mode = False
            state._vacation_end_date = ""
            sch_ch = state.bot.get_channel(SCHEDULE_CHANNEL_ID) or channel
            await sch_ch.send("📚 **개강일이 됐어요!** 방학 모드 자동 해제 — LMS 기능 다시 활성화됩니다.")

        # 방학 모드면 알림 없음
        if state._vacation_mode:
            continue

        from manta_daemon.integrations.calendar_ops import get_apple_calendar
        schedule_ch = state.bot.get_channel(SCHEDULE_CHANNEL_ID) or channel
        schedule = get_apple_calendar(start_date=today_str, end_date=today_str)
        await schedule_ch.send(
            f"🌙 **오늘 하루도 수고하셨어요! 내일 일정 알려드릴게요.**\n\n"
            f"{schedule}"
        )

        if not state._vacation_mode:
            from manta_daemon.integrations.lms import lms_get_all_homework
            lms_ch = state.bot.get_channel(LMS_CHANNEL_ID) or channel
            try:
                lms_raw = lms_get_all_homework()
                if "미완료 과제가 없어요" in lms_raw or "🎉" in lms_raw:
                    await lms_ch.send("✅ **미제출 과제 없음!**")
                else:
                    await lms_ch.send(f"📚 {lms_raw}")
            except Exception as e:
                await lms_ch.send(f"📚 LMS 조회 실패: {e}")


def get_daily_briefing(date_str: str = "") -> str:
    """오늘(또는 지정 날짜) 할 일 = 캘린더 + LMS 미완료 과제 통합 브리핑."""
    from datetime import date as _date, timedelta as _td
    from manta_daemon.integrations.calendar_ops import get_apple_calendar
    today = _date.today()
    if date_str:
        target = date_str
        try:
            d = _date.fromisoformat(date_str)
            if d == today:
                label = "오늘"
            elif d == today + _td(days=1):
                label = "내일"
            elif d == today - _td(days=1):
                label = "어제"
            else:
                label = date_str
        except Exception:
            label = date_str
    else:
        target = today.strftime("%Y-%m-%d")
        label = "오늘"

    cal = get_apple_calendar(start_date=target, end_date=target)
    lines = [f"📋 **{label}의 브리핑**\n"]
    lines.append(cal)
    if state._vacation_mode:
        lines.append("\n🏖️ **방학 모드** — LMS 조회 비활성")
    else:
        from manta_daemon.integrations.lms import lms_get_all_homework
        lms = lms_get_all_homework()
        lines.append("\n**📚 LMS 미완료 과제**")
        lines.append(lms)
    return "\n".join(lines)


async def _start_daily_report_when_ready():
    """데일리 리포트 시작 (schedule 채널 기준)"""
    schedule_ch = state.bot.get_channel(SCHEDULE_CHANNEL_ID)
    if schedule_ch is None:
        while state._daily_report_channel is None:
            await asyncio.sleep(5)
        schedule_ch = state._daily_report_channel
    state._daily_report_task_ref = asyncio.create_task(_daily_report_task(schedule_ch))
