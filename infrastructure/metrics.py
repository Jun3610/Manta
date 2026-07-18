"""
infrastructure/metrics.py
LLM 호출 관측성 저장소 (SPEC 2.7절)

Phase 0 범위: 테이블 생성 + 기록만 구현.
조회용 명령어/대시보드는 Phase 3 이후 백로그.

기록 항목:
  - timestamp, channel_id, role, model, provider
  - input_tokens, output_tokens, latency_ms
  - status ("success" | "error"), error_type (실패 시)

활용 예시:
  (a) 하루 호출 횟수 조회 → 할당량 근접 경고
  (b) role/그래프별 호출 빈도 → Haiku 다운그레이드 우선순위 판단
  (c) 장애 발생 시 "언제부터 실패했는지" 쿼리로 즉시 확인
"""
import asyncio
import logging
import sqlite3
import os
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
METRICS_DB_PATH = os.path.join(DATA_DIR, "metrics.db")


@contextmanager
def _get_conn():
    """WAL 모드 SQLite 커넥션 컨텍스트 매니저."""
    conn = sqlite3.connect(METRICS_DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def init_metrics_db() -> None:
    """
    llm_calls 테이블을 생성한다 (이미 존재하면 무시).
    bot.py → infrastructure/database.init_all_dbs() 에서 호출된다.
    """
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_calls (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT    NOT NULL,
                channel_id   TEXT    NOT NULL DEFAULT '',
                role         TEXT    NOT NULL,
                model        TEXT    NOT NULL,
                provider     TEXT    NOT NULL DEFAULT 'anthropic',
                input_tokens  INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                latency_ms   INTEGER DEFAULT 0,
                status       TEXT    NOT NULL DEFAULT 'success',
                error_type   TEXT    DEFAULT NULL
            )
        """)
        conn.commit()
    logger.info("[Metrics] llm_calls 테이블 초기화 완료. DB: %s", METRICS_DB_PATH)


def record_llm_call(
    *,
    role: str,
    model: str,
    provider: str = "anthropic",
    channel_id: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    status: str = "success",
    error_type: Optional[str] = None,
) -> None:
    """
    LLM 호출 1건을 llm_calls 테이블에 동기로 기록한다.

    호출 시점: core/agent.py 및 core/graphs/*.py 의 LLM 호출 직후.
    비동기 컨텍스트에서 호출 시 블로킹을 피하려면 record_llm_call_async() 사용.

    Args:
        role:          모델 사용 목적 ("chat" | "parse" | "summary")
        model:         실제 사용된 모델명 (예: "claude-sonnet-4-5")
        provider:      프로바이더 이름 (기본: "anthropic")
        channel_id:    Discord 채널 ID (없으면 빈 문자열)
        input_tokens:  입력 토큰 수 (usage 정보 없으면 0)
        output_tokens: 출력 토큰 수
        latency_ms:    호출 소요 시간(ms)
        status:        "success" 또는 "error"
        error_type:    오류 유형 (429/timeout 등, 성공 시 None)
    """
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()

    try:
        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls
                    (timestamp, channel_id, role, model, provider,
                     input_tokens, output_tokens, latency_ms, status, error_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, channel_id, role, model, provider,
                 input_tokens, output_tokens, latency_ms, status, error_type),
            )
            conn.commit()
    except Exception as e:
        # metrics 기록 실패가 본 기능을 막으면 안 됨 — 로그만 남기고 계속
        logger.error("[Metrics] llm_calls 기록 실패: %s", e, exc_info=True)


async def record_llm_call_async(
    *,
    role: str,
    model: str,
    provider: str = "anthropic",
    channel_id: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    status: str = "success",
    error_type: Optional[str] = None,
) -> None:
    """
    비동기 컨텍스트에서 LLM 호출을 기록한다 (executor 에서 동기 함수 호출).

    core/agent.py 의 async chat() 메서드 등에서 사용한다.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: record_llm_call(
            role=role,
            model=model,
            provider=provider,
            channel_id=channel_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status=status,
            error_type=error_type,
        ),
    )
