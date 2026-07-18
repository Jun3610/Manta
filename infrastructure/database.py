import sqlite3
import os
import logging
from typing import Optional
from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory

logger = logging.getLogger(__name__)

# 경로 설정
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

CALENDAR_DB_PATH = os.path.join(DATA_DIR, "calendar_store.db")
USER_PROFILE_DB_PATH = os.path.join(DATA_DIR, "user_profile.db")
CHAT_HISTORY_DB_PATH = os.path.join(DATA_DIR, "chat_history.db")

def init_all_dbs():
    """앱 시작 시 필요한 모든 데이터베이스 및 테이블을 초기화합니다."""
    logger.info("데이터베이스 초기화를 시작합니다.")
    _init_calendar_db()
    _init_user_profile_db()
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

def get_session_history(session_id: str) -> BaseChatMessageHistory:
    """
    주어진 세션 ID에 대한 영구적인 채팅 기록(SQLChatMessageHistory)을 반환합니다.
    """
    connection_string = f"sqlite+aiosqlite:///{CHAT_HISTORY_DB_PATH}"
    return SQLChatMessageHistory(
        session_id=session_id,
        connection=connection_string,
        async_mode=True
    )
