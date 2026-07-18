"""
tasks/schedule.py — 채널 토픽 주기적 갱신
"""
import asyncio
import time as _time
from datetime import datetime

import manta_daemon.state as state


def _build_status_topic() -> str:
    """채널 토픽용 상태 문자열 생성 (일정+LMS만)"""
    import calendar as _cal
    now = datetime.now()

    if _time.time() - state._cal_topic_cache["updated"] > 300:
        try:
            from manta_daemon.integrations.calendar_ops import get_apple_calendar
            first_day = now.strftime("%Y-%m-01")
            last_day_num = _cal.monthrange(now.year, now.month)[1]
            last_day = now.strftime(f"%Y-%m-{last_day_num:02d}")
            month_raw = get_apple_calendar(start_date=first_day, end_date=last_day)
            state._cal_topic_cache["month_count"] = len([l for l in month_raw.splitlines() if "|" in l and "날짜" not in l])
            state._cal_topic_cache["updated"] = _time.time()
        except Exception:
            pass
    month_label = f"📆{now.month}월 {state._cal_topic_cache['month_count']}개"

    if state._vacation_mode:
        lms_label = "🏖️방학중"
    else:
        if _time.time() - state._lms_topic_cache["updated"] > 600:
            try:
                from manta_daemon.integrations.lms import lms_get_all_homework
                lms_raw = lms_get_all_homework()
                state._lms_topic_cache["count"] = len([l for l in lms_raw.splitlines() if l.strip().startswith("•") and len(l.strip()) > 3])
                state._lms_topic_cache["updated"] = _time.time()
            except Exception:
                pass
        lms_count = state._lms_topic_cache["count"]
        lms_label = f"📚미제출 {lms_count}개" if lms_count else "📚미제출 없음"

    return f"{month_label} | {lms_label}"


async def _status_topic_task(channel):
    """채널 토픽을 5분마다 갱신"""
    state._status_topic_channel = channel
    while True:
        try:
            topic = await asyncio.get_running_loop().run_in_executor(None, _build_status_topic)
            await channel.edit(topic=topic)
        except Exception as e:
            print(f"[토픽] 갱신 실패: {e}")
        await asyncio.sleep(300)
