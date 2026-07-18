"""자연어 알림 시간 파싱과 Discord 알림 태스크."""
import asyncio
import re
from datetime import datetime, timedelta

import discord

import manta_daemon.state as state
from manta_daemon.config import MY_DISCORD_UID


_TIME_WORDS = {
    "아침": (9, 0),
    "오전": (9, 0),
    "점심": (12, 0),
    "오후": (15, 0),
    "저녁": (19, 0),
    "밤": (21, 0),
}

_TRIGGER = re.compile(
    r"알려\s*줘|알림|리마인드|기억시켜|말해\s*줘|말해줘|깨워\s*줘|깨워줘",
    re.I,
)


def _extract_label(text: str) -> str:
    label = text
    patterns = [
        r"\d+\s*(?:분|시간)\s*(?:후|뒤)(?:에)?",
        r"(?:오늘|내일|모레)(?:\s*(?:아침|오전|점심|오후|저녁|밤))?(?:에)?",
        r"(?:오전|오후)?\s*\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?(?:에)?",
        r"좀\s*있다가|이따가|조금\s*뒤에?",
        r"(?:알려\s*줘|알려줘|알림(?:해)?줘|리마인드(?:해)?줘|기억시켜줘|말해\s*줘|말해줘|깨워\s*줘|깨워줘)\s*$",
    ]
    for pattern in patterns:
        label = re.sub(pattern, " ", label, flags=re.I)
    label = re.sub(r"\s+", " ", label).strip(" ,.!?~")
    return label or "알림"


def _parse_clock(text: str) -> tuple[int, int] | None:
    clock = re.search(r"(?:(오전|오후)\s*)?(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", text)
    if not clock:
        for word, clock_value in _TIME_WORDS.items():
            if word in text:
                return clock_value
        return None

    ampm, raw_hour, raw_minute = clock.groups()
    hour = int(raw_hour)
    minute = int(raw_minute or 0)
    if ampm == "오후" and hour < 12:
        hour += 12
    elif ampm == "오전" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour, minute


def parse_reminder(text: str, now: datetime | None = None) -> tuple[datetime, str] | None:
    """지원 예: 30분 뒤, 2시간 후, 좀 있다가, 내일 저녁, 5시에 XXX 알려줘."""
    text = text.strip()
    if not text:
        return None

    now = now or datetime.now()
    has_trigger = bool(_TRIGGER.search(text))

    relative = re.search(r"(\d+)\s*(분|시간)\s*(후|뒤)", text)
    if relative:
        label = _extract_label(text)
        if not has_trigger and len(label) < 2:
            return None
        amount = int(relative.group(1))
        delta = timedelta(minutes=amount if relative.group(2) == "분" else amount * 60)
        return now + delta, label

    if re.search(r"좀\s*있다가|조금\s*뒤", text):
        if not has_trigger:
            return None
        return now + timedelta(minutes=10), _extract_label(text)
    if "이따" in text:
        if not has_trigger:
            return None
        return now + timedelta(minutes=30), _extract_label(text)

    day_match = re.search(r"오늘|내일|모레", text)
    clock = _parse_clock(text)

    if clock and has_trigger and not day_match:
        hour, minute = clock
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target, _extract_label(text)

    if not day_match:
        return None
    if not has_trigger:
        return None

    day_offset = {"오늘": 0, "내일": 1, "모레": 2}[day_match.group()]
    if clock is None:
        hour, minute = 9, 0
    else:
        hour, minute = clock

    target = (now + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if target <= now:
        return None
    return target, _extract_label(text)


async def _reminder_task(channel, fire_at: datetime, label: str, key: str) -> None:
    try:
        await asyncio.sleep(max(0, (fire_at - datetime.now()).total_seconds()))
        await channel.send(f"⏰ <@{MY_DISCORD_UID}> **알림:** {label}")
    finally:
        state._active_timers.pop(key, None)
        state._timer_meta.pop(key, None)


def schedule_reminder(channel, fire_at: datetime, label: str) -> str:
    key = f"reminder:{fire_at.timestamp()}:{label}"
    task = asyncio.create_task(_reminder_task(channel, fire_at, label, key))
    state._active_timers[key] = task
    state._timer_meta[key] = {
        "label": label,
        "started": datetime.now().strftime("%m/%d %H:%M"),
        "fire_at": fire_at.isoformat(),
    }
    return f"⏰ {discord.utils.format_dt(fire_at, style='F')}에 **{label}** 알려드릴게요."
