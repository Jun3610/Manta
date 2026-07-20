"""
services/memory_service.py
사용자 장기 메모리 서비스 (SPEC 6절).

대화 세션이 끝나도 사용자에 대한 사실(선호/습관/일정 패턴)을
SQLite DB에 저장하고, 다음 대화에서 시스템 프롬프트로 주입한다.

보안 주의:
  ⚠️ 비밀번호, 학번, 금융 계좌번호 등 민감 정보는 절대 저장하지 않는다.
     LLM에게 전달하는 추출 프롬프트에 해당 금지 지시가 명시되어 있다.

설계 원칙:
  - "사실 저장" 전용 기능. LLM이 새 tool이나 코드를 만들어 실행하는 기능이 아님.
  - channel_id + key 기준 UPSERT → 동일 키는 최신 값으로만 유지.
  - get_facts() 반환값은 dict로, agent.py 시스템 프롬프트 주입에 직접 활용.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Optional

from infrastructure.database import USER_MEMORY_DB_PATH

logger = logging.getLogger(__name__)


class MemoryService:
    """
    사용자 장기 메모리 CRUD를 담당하는 서비스.

    channel_id(Discord 채널 ID)를 세션 격리 키로 사용한다.
    """

    def _db_conn(self):
        conn = sqlite3.connect(USER_MEMORY_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def save_fact(self, channel_id: str, key: str, value: str) -> None:
        """
        사실 하나를 저장(UPSERT)한다.

        동일 (channel_id, key) 조합이 이미 존재하면 value와 updated_at을 갱신한다.

        Args:
            channel_id: Discord 채널 ID (세션 격리 키).
            key:        사실 분류 키 (예: "work_schedule", "habit", "preference").
            value:      저장할 자연어 사실 (예: "OP 출근 시간은 06:00~15:00").

        Raises:
            ValueError: key 또는 value가 비어 있을 때.
        """
        if not key or not value:
            raise ValueError(f"[MemoryService.save_fact] key와 value는 비어있을 수 없습니다. key={key!r}")

        now = datetime.now().isoformat(timespec="seconds")
        with self._db_conn() as conn:
            conn.execute(
                """
                INSERT INTO user_memory (channel_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id, key) DO UPDATE SET
                    value      = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (channel_id, key, value, now),
            )
            conn.commit()

        logger.info(
            "[MemoryService] 저장 완료: channel=%s | key=%s | value=%r",
            channel_id, key, value[:80] + ("..." if len(value) > 80 else ""),
        )

    def get_facts(self, channel_id: str) -> dict[str, str]:
        """
        채널 ID에 해당하는 모든 저장 사실을 반환한다.

        Args:
            channel_id: Discord 채널 ID.

        Returns:
            {key: value} 형태의 dict. 저장된 사실이 없으면 빈 dict.
        """
        with self._db_conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM user_memory WHERE channel_id = ? ORDER BY key",
                (channel_id,),
            ).fetchall()

        facts = {row["key"]: row["value"] for row in rows}
        logger.debug(
            "[MemoryService] 조회 완료: channel=%s → %d건",
            channel_id, len(facts),
        )
        return facts

    def delete_fact(self, channel_id: str, key: str) -> bool:
        """
        특정 사실 항목을 삭제한다.

        Args:
            channel_id: Discord 채널 ID.
            key:        삭제할 사실 키.

        Returns:
            True: 삭제 성공 (해당 행이 존재했음). False: 해당 행 없음.
        """
        with self._db_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM user_memory WHERE channel_id = ? AND key = ?",
                (channel_id, key),
            )
            conn.commit()
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info("[MemoryService] 삭제 완료: channel=%s | key=%s", channel_id, key)
        else:
            logger.warning("[MemoryService] 삭제 대상 없음: channel=%s | key=%s", channel_id, key)
        return deleted

    def clear_all_facts(self, channel_id: str) -> int:
        """
        채널 ID에 해당하는 모든 사실을 삭제한다.

        Args:
            channel_id: Discord 채널 ID.

        Returns:
            삭제된 행 수.
        """
        with self._db_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM user_memory WHERE channel_id = ?",
                (channel_id,),
            )
            conn.commit()
            count = cursor.rowcount

        logger.info("[MemoryService] 전체 삭제: channel=%s → %d건 삭제", channel_id, count)
        return count
