import sqlite3
import os
import logging
from typing import Optional
from langchain_core.chat_history import BaseChatMessageHistory

logger = logging.getLogger(__name__)

# 경로 설정
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

CALENDAR_DB_PATH = os.path.join(DATA_DIR, "calendar_store.db")
USER_PROFILE_DB_PATH = os.path.join(DATA_DIR, "user_profile.db")
USER_MEMORY_DB_PATH = os.path.join(DATA_DIR, "user_memory.db")
CHAT_HISTORY_DB_PATH = os.path.join(DATA_DIR, "chat_history.db")

def init_all_dbs():
    """앱 시작 시 필요한 모든 데이터베이스 및 테이블을 초기화합니다."""
    logger.info("데이터베이스 초기화를 시작합니다.")
    _init_calendar_db()
    _init_user_profile_db()
    _init_user_memory_db()
    # LLM 호출 metrics 테이블 초기화 (SPEC 2.7절)
    from infrastructure.metrics import init_metrics_db
    init_metrics_db()
    logger.info("데이터베이스 초기화가 완료되었습니다.")


def _init_calendar_db():
    with sqlite3.connect(CALENDAR_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                uid          TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                date         TEXT NOT NULL,
                time         TEXT NOT NULL,
                duration_min INTEGER DEFAULT 60,
                calendar_name TEXT DEFAULT '캘린더',
                notes        TEXT DEFAULT '',
                important    INTEGER DEFAULT 0,
                synced_at    TEXT
            )
        """)
        conn.commit()

def _init_user_profile_db():
    with sqlite3.connect(USER_PROFILE_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                topic      TEXT PRIMARY KEY,
                count      INTEGER DEFAULT 1,
                last_seen  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()


def _init_user_memory_db():
    """
    사용자 장기 메모리 테이블 초기화 (SPEC 6절 — 사용자 장기 메모리).

    user_memory 테이블 스키마:
      channel_id : Discord 채널 ID (세션 격리 키)
      key        : 사실 분류 키 (예: "work_schedule", "habit", "preference")
      value      : 저장할 사실 값 (자연어 문자열)
      updated_at : 마지막 갱신 시각 (ISO 8601)

    동일 channel_id + key 조합은 UPSERT로 최신 값만 유지한다.

    보안 주의:
      ⚠️ 비밀번호, 학번, 금융 정보 등 민감 정보는 이 테이블에 저장하지 않는다.
         LLM이 추출하는 시스템 프롬프트에 이미 해당 금지 지시가 포함되어 있다.
    """
    with sqlite3.connect(USER_MEMORY_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
                channel_id TEXT NOT NULL,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (channel_id, key)
            )
        """)
        conn.commit()
    logger.info("[Database] user_memory 테이블 초기화 완료. DB: %s", USER_MEMORY_DB_PATH)

def get_session_history(session_id: str) -> BaseChatMessageHistory:
    """
    세션 ID별로 JSON 파일에 대화 기록을 저장·조회합니다.
    SQLChatMessageHistory의 async/sync 충돌 문제를 우회하기 위해
    FileChatMessageHistory를 사용합니다.
    """
    from langchain_community.chat_message_histories import FileChatMessageHistory
    history_dir = os.path.join(DATA_DIR, "chat_history")
    os.makedirs(history_dir, exist_ok=True)
    # session_id(채널 ID)를 파일명으로 사용
    safe_id = str(session_id).replace("/", "_").replace("\\", "_")
    history_path = os.path.join(history_dir, f"{safe_id}.json")
    return FileChatMessageHistory(file_path=history_path)
