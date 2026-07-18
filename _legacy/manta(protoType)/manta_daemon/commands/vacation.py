"""
commands/vacation.py — 방학 모드 on/off 핸들러
"""
import manta_daemon.state as state


async def _cmd_vacation(channel, date_str: str):
    """방학 모드 활성화 (!방학 YYYY-MM-DD)"""
    from datetime import date as _date
    try:
        _date.fromisoformat(date_str)
    except ValueError:
        await channel.send(
            "❌ 날짜 형식이 잘못됐어요.\n"
            "`!방학 YYYY-MM-DD` 형식으로 입력해주세요.\n"
            "예: `!방학 2025-09-01`"
        )
        return
    state._vacation_mode = True
    state._vacation_end_date = date_str
    await channel.send(
        f"🏖️ **방학 모드 ON!**\n"
        f"개강일 **{date_str}** 까지 LMS 알림·조회가 전부 꺼져요.\n"
        f"`!개강` 으로 언제든 수동 해제 가능해요."
    )


async def _cmd_vacation_end(channel):
    """방학 모드 해제 (!개강)"""
    state._vacation_mode = False
    state._vacation_end_date = ""
    await channel.send("📚 **개강 모드!** LMS 알림·조회 다시 활성화됐어요.")
