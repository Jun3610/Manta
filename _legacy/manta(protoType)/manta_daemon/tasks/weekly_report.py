"""매주 일요일 밤 다음 주 일정과 주간 체중 그래프를 전송한다."""
import asyncio
from datetime import datetime, timedelta

import discord

import manta_daemon.state as state
from manta_daemon.config import SCHEDULE_CHANNEL_ID, HEALTH_CHANNEL_ID
from manta_daemon.integrations.calendar_ops import get_apple_calendar
from manta_daemon.integrations.health import _build_health_report


def _next_run(now: datetime) -> datetime:
    days_until_sunday = (6 - now.weekday()) % 7
    target = (now + timedelta(days=days_until_sunday)).replace(
        hour=21, minute=0, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=7)
    return target


async def _send_weekly_report() -> None:
    now = datetime.now()
    next_monday = (now + timedelta(days=(7 - now.weekday()))).date()
    next_sunday = next_monday + timedelta(days=6)
    loop = asyncio.get_running_loop()

    schedule = await loop.run_in_executor(
        None,
        lambda: get_apple_calendar(
            start_date=next_monday.isoformat(), end_date=next_sunday.isoformat()
        ),
    )
    schedule_channel = state.bot.get_channel(SCHEDULE_CHANNEL_ID)
    if schedule_channel:
        await schedule_channel.send(
            f"🗓️ **다음 주 일정 리포트**\n"
            f"`{next_monday:%m/%d} ~ {next_sunday:%m/%d}`\n\n{schedule}"
        )

    health_channel = state.bot.get_channel(HEALTH_CHANNEL_ID)
    if health_channel:
        embed, image = await loop.run_in_executor(
            None, lambda: _build_health_report("week", weight_only=True)
        )
        if image:
            image.seek(0)
            embed.set_image(url="attachment://weekly_weight.png")
            await health_channel.send(
                embed=embed,
                file=discord.File(image, filename="weekly_weight.png"),
            )
        else:
            await health_channel.send(embed=embed)


async def _weekly_report_task() -> None:
    while True:
        target = _next_run(datetime.now())
        await asyncio.sleep((target - datetime.now()).total_seconds())
        try:
            await _send_weekly_report()
        except Exception as e:
            print(f"[주간 리포트] 전송 실패: {e}")


def start_weekly_report() -> asyncio.Task:
    return asyncio.create_task(_weekly_report_task())
