# config.py
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# ====== 1. 디스코드 및 기본 설정 ======
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
MY_DISCORD_UID = int(os.getenv("MY_DISCORD_UID", 0))
CLAUDE_BRIDGE_CHANNEL_ID = int(os.getenv("CLAUDE_BRIDGE_CHANNEL_ID", 0))
MANTA_CHANNEL_ID = int(os.getenv("MANTA_CHANNEL_ID", 0))
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", 0))

# ====== 2. 상용 LLM API Keys 및 설정 ======
# 기본 프로바이더: anthropic (SPEC v2.1 결정)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")

# --- Anthropic 설정 (주력 LLM, SPEC 3절) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
# role별 모델 매핑 (SPEC 2.6절)
# chat: 사용자와 직접 대화하는 AgentExecutor에 사용 (상위 모델)
# parse: LangGraph Parse 노드에서 자연어 → 구조화 명령 변환 (저비용)
# summary: LangGraph Summary 노드에서 결과 요약 (저비용)
ANTHROPIC_MODEL_CHAT = os.getenv("ANTHROPIC_MODEL_CHAT", "claude-sonnet-4-5")
ANTHROPIC_MODEL_PARSE = os.getenv("ANTHROPIC_MODEL_PARSE", "claude-haiku-4-5-20251001")
ANTHROPIC_MODEL_SUMMARY = os.getenv("ANTHROPIC_MODEL_SUMMARY", "claude-haiku-4-5-20251001")

# --- OpenAI 설정 (폴백/대안) ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# --- Gemini 설정 (레거시, 재검토 전까지 비활성) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.5-flash"

# ====== 3. 타사 앱 연동 (인프라 계층용) ======
# 노션
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")

# 부경대 LMS
LMS_ID = os.getenv("LMS_ID")
LMS_PW = os.getenv("LMS_PW")
ALLOWED_DOMAINS = [os.getenv("ALLOWED_DOMAIN_1"), os.getenv("ALLOWED_DOMAIN_2")]

# 구글 지메일
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json")