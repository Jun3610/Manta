"""
state.py — bot 인스턴스 + 모든 전역 변수.
config만 임포트.
"""
import os
import json
import discord
import requests
from discord.ext import commands
from openai import OpenAI
from notion_client import Client

from manta_daemon.config import (
    OPENAI_API_KEY, NOTION_TOKEN, NOTION_PAGE_ID,
    MEMORY_FILE, _PROJECT_ROOT,
)

# ==================== [ SDK 선택적 임포트 ] ====================
try:
    import anthropic as _anthropic_sdk
    _HAS_ANTHROPIC = True
except ImportError:
    _anthropic_sdk = None
    _HAS_ANTHROPIC = False

try:
    from google import genai as _genai_sdk
    _HAS_GEMINI = True
except ImportError:
    _genai_sdk = None
    _HAS_GEMINI = False

# ==================== [ Discord Bot ] ====================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== [ OpenAI / Notion 클라이언트 ] ====================
ai_client = OpenAI(api_key=OPENAI_API_KEY)

if NOTION_TOKEN and NOTION_PAGE_ID:
    notion = Client(auth=NOTION_TOKEN)
else:
    notion = None

# ==================== [ LMS 세션 ] ====================
lms_session = requests.Session()
lms_session.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
lms_logged_in     = False
lms_current_course = None  # {"name": str, "kjkey": str}

# ==================== [ 메모리 (대화 기록) ] ====================
def _load_memory() -> list:
    """저장된 대화 기록 로드 (없으면 빈 리스트)"""
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                history = data.get("history", [])
                print(f"[메모리] 대화 기록 {len(history)}턴 로드됨")
                return history
    except Exception as e:
        print(f"[메모리] 로드 실패: {e}")
    return []


def _save_memory():
    """현재 대화 기록을 파일에 저장"""
    from datetime import datetime
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "saved_at": datetime.now().isoformat(),
                "history": conversation_history[-30:]
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[메모리] 저장 실패: {e}")


conversation_history: list = _load_memory()
current_context: dict      = {}
current_workspace          = None  # {"name": str, "path": str}

# ==================== [ 백그라운드 작업 ] ====================
_active_timers: dict  = {}   # {name: asyncio.Task}
_timer_meta:    dict  = {}   # {name: {"label": str, "started": str}}

_daily_report_channel  = None
_daily_report_task_ref = None

_system_embed_msg      = None
_system_embed_task_ref = None
_status_topic_task_ref = None
_status_topic_channel  = None

_cal_db_syncing        = False

# ==================== [ 상태 플래그 ] ====================
_processed_message_ids: set  = set()   # on_message 중복 처리 방지
_claude_bridge_active: bool  = False
_bridge_session_log:   list  = []

_vacation_mode:     bool = False
_vacation_end_date: str  = ""

_openai_quota_alerted: bool = False
_last_reported_errors: set  = set()

# ==================== [ Gmail ] ====================
_gmail_service       = None
_gmail_notified_ids: set = set()
_gmail_task_ref      = None

# ==================== [ 캐시 ] ====================
_lms_topic_cache = {"count": 0, "updated": 0}
_cal_topic_cache = {"month_count": 0, "updated": 0}
