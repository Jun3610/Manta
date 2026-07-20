"""
infrastructure/rollback_store.py
일괄 수정 전 원본 이벤트 스냅샷을 로컬 JSON 파일에 저장/로드하는 유틸리티.

- 마지막 일괄 수정 1회분만 보관 (덮어쓰기 방식).
- save_snapshot : 이벤트 목록을 원본 상태 그대로 저장.
- load_snapshot : 저장된 스냅샷을 반환. 없으면 None.
- clear_snapshot : 파일 삭제.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 스냅샷 파일 경로 (bot.py 기준 data/ 디렉토리)
_SNAPSHOT_PATH = Path(__file__).parent.parent / "data" / "rollback_snapshot.json"


def save_snapshot(events: list[dict]) -> None:
    """
    일괄 수정 직전 이벤트 목록을 스냅샷으로 저장한다.

    Args:
        events: CalendarService.get_events_in_range()가 반환한 이벤트 dict 목록.
                각 항목은 uid, title, date, time, duration_min, calendar_name 포함.
    """
    _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "events": events,
    }
    with open(_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(
        "[RollbackStore] 스냅샷 저장 완료: %d건 → %s",
        len(events), _SNAPSHOT_PATH,
    )


def load_snapshot() -> Optional[dict]:
    """
    저장된 롤백 스냅샷을 반환한다.

    Returns:
        {"saved_at": str, "events": list[dict]} 또는 None (스냅샷 없음).
    """
    if not _SNAPSHOT_PATH.exists():
        logger.info("[RollbackStore] 스냅샷 파일 없음.")
        return None

    try:
        with open(_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(
            "[RollbackStore] 스냅샷 로드: %d건 (저장 시각: %s)",
            len(data.get("events", [])), data.get("saved_at"),
        )
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[RollbackStore] 스냅샷 로드 실패: %s", e)
        return None


def clear_snapshot() -> None:
    """저장된 스냅샷 파일을 삭제한다."""
    try:
        if _SNAPSHOT_PATH.exists():
            os.remove(_SNAPSHOT_PATH)
            logger.info("[RollbackStore] 스냅샷 삭제 완료.")
    except OSError as e:
        logger.error("[RollbackStore] 스냅샷 삭제 실패: %s", e)
