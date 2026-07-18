"""
config.py — 환경변수, 채널 ID, 상수.
manta 내부 임포트 없음.
"""
import os
import re
from dotenv import load_dotenv

# 패키지 루트(Manta-Bot/) 기준으로 .env 로드
_PACKAGE_DIR  = os.path.dirname(os.path.abspath(__file__))   # .../Manta-Bot/manta/
_PROJECT_ROOT = os.path.dirname(_PACKAGE_DIR)                 # .../Manta-Bot/

env_path = os.path.join(_PROJECT_ROOT, ".env")
load_dotenv(dotenv_path=env_path)

# ==================== [ Discord / 채널 ID ] ====================
MY_DISCORD_UID           = int(os.getenv("MY_DISCORD_UID", "0"))
SYSTEM_CHANNEL_ID        = 1520863023718334704
SCHEDULE_CHANNEL_ID      = 1520859916225609938
LMS_CHANNEL_ID           = 1520860829476458587
MANTA_CHANNEL_ID         = int(os.getenv("MANTA_CHANNEL_ID", "0"))
HEALTH_CHANNEL_ID        = 1524307347063832677
CLAUDE_BRIDGE_CHANNEL_ID = int(os.getenv("CLAUDE_BRIDGE_CHANNEL_ID", "0"))
VOICE_CHANNEL_ID         = int(os.getenv("VOICE_CHANNEL_ID", "0"))

DISCORD_BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
NOTION_TOKEN       = os.getenv("NOTION_TOKEN")
NOTION_PAGE_ID     = os.getenv("NOTION_PAGE_ID")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

# ==================== [ 파일 경로 ] ====================
MEMORY_FILE   = os.path.join(_PROJECT_ROOT, "manta_memory.json")
_LOG_FILE     = os.path.join(_PROJECT_ROOT, "manta_log.txt")
_CAL_DB_PATH  = os.path.join(_PROJECT_ROOT, "calendar_store.db")
_USER_DB_PATH = os.path.join(_PROJECT_ROOT, "user_profile.db")

# Claude Code CLI
CLAUDE_CLI       = os.path.expanduser("~/.local/bin/claude")
_CLAUDE_CLI_PATH = os.path.expanduser("~/.local/bin/claude")
_CLAUDE_CLI_AVAILABLE = os.path.isfile(_CLAUDE_CLI_PATH)

# Gmail
GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN_PATH       = os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json")

# ==================== [ 앱 / LLM ] ====================
_DESKTOP_LLM_APPS = [
    v.strip()
    for k, v in os.environ.items()
    if k.startswith("DESKTOP_LLM_") and v.strip()
]

_DESKTOP_APP_EMOJI = {
    "claude":  "🟣",
    "gemini":  "🔵",
    "chatgpt": "🟢",
    "gpt":     "🟢",
}

ALLOWED_MAC_APPS = {
    "notion": "Notion", "노션": "Notion",
    "safari": "Safari", "사파리": "Safari",
    "preview": "Preview", "미리보기": "Preview",
    "visual studio code": "Visual Studio Code",
    "vscode": "Visual Studio Code", "비주얼": "Visual Studio Code",
    "intellij": "IntelliJ IDEA", "인텔리": "IntelliJ IDEA",
    "mail": "Mail", "메일": "Mail",
    "spotify": "Spotify", "스포티파이": "Spotify",
    "discord": "Discord", "디코": "Discord", "디스코드": "Discord",
}

# ==================== [ 엔터테인먼트 서비스 ] ====================
ENTERTAINMENT_SERVICES = {
    "youtube":   {"label": "▶️  YouTube",  "emoji": "▶️",  "url": "https://www.youtube.com",   "app": None},
    "instagram": {"label": "📸 Instagram", "emoji": "📸",  "url": "https://www.instagram.com", "app": None},
    "netflix":   {"label": "🎬 Netflix",   "emoji": "🎬",  "url": "https://www.netflix.com",   "app": None},
    "spotify":   {"label": "🎵 Spotify",   "emoji": "🎵",  "url": None,                        "app": "Spotify"},
}

# ==================== [ LMS ] ====================
LMS_ID   = os.getenv("LMS_ID", "")
LMS_PW   = os.getenv("LMS_PW", "")
LMS_BASE = "https://lms.pknu.ac.kr"

# ALLOWED_DOMAIN_1, ALLOWED_DOMAIN_2, ... 형식으로 .env에서 읽음
ALLOWED_DOMAINS = [
    v.strip()
    for k, v in os.environ.items()
    if k.startswith("ALLOWED_DOMAIN_") and v.strip()
] or ["lms.pknu.ac.kr"]

# ==================== [ 사이트명 → URL 매핑 ] ====================
SITE_NAME_MAP = {
    "구글": "https://www.google.com", "google": "https://www.google.com",
    "네이버": "https://www.naver.com", "naver": "https://www.naver.com",
    "다음": "https://www.daum.net", "daum": "https://www.daum.net",
    "빙": "https://www.bing.com", "bing": "https://www.bing.com",
    "유튜브": "https://www.youtube.com", "youtube": "https://www.youtube.com",
    "인스타": "https://www.instagram.com", "인스타그램": "https://www.instagram.com", "instagram": "https://www.instagram.com",
    "페이스북": "https://www.facebook.com", "facebook": "https://www.facebook.com",
    "트위터": "https://www.twitter.com", "twitter": "https://www.twitter.com",
    "엑스": "https://www.x.com", "x.com": "https://www.x.com",
    "틱톡": "https://www.tiktok.com", "tiktok": "https://www.tiktok.com",
    "넷플릭스": "https://www.netflix.com", "netflix": "https://www.netflix.com",
    "왓챠": "https://watcha.com", "watcha": "https://watcha.com",
    "웨이브": "https://www.wavve.com", "wavve": "https://www.wavve.com",
    "티빙": "https://www.tving.com", "tving": "https://www.tving.com",
    "깃허브": "https://www.github.com", "github": "https://www.github.com",
    "스택오버플로우": "https://stackoverflow.com", "stackoverflow": "https://stackoverflow.com",
    "쿠팡": "https://www.coupang.com", "coupang": "https://www.coupang.com",
    "아마존": "https://www.amazon.com", "amazon": "https://www.amazon.com",
    "lms": "https://lms.pknu.ac.kr", "학교": "https://lms.pknu.ac.kr",
    "학교 lms": "https://lms.pknu.ac.kr", "lms 학교": "https://lms.pknu.ac.kr",
    "부경대": "https://lms.pknu.ac.kr", "부경대학교": "https://lms.pknu.ac.kr",
    "부경대 lms": "https://lms.pknu.ac.kr", "부경대학교 lms": "https://lms.pknu.ac.kr",
    "부경 lms": "https://lms.pknu.ac.kr", "pknu lms": "https://lms.pknu.ac.kr",
    "pknu": "https://www.pknu.ac.kr",
}

# ==================== [ 경로 상수 ] ====================
HOME              = os.path.expanduser("~")
WORK_STATION_ROOT = os.path.join(HOME, "work-station")

# ==================== [ 터미널 보안 ] ====================
ALLOWED_TERMINAL_CMDS = {
    "ls", "pwd", "echo", "cat", "head", "tail", "wc", "grep",
    "git", "python3", "pip", "pip3",
    "java", "javac", "mvn", "gradle",
    "node", "npm", "yarn",
    "find", "du", "df",
}
BLOCKED_TERMINAL_CMDS = {
    "rm", "rmdir", "sudo", "su", "chmod", "chown",
    "curl", "wget", "nc", "netcat", "ssh", "scp", "rsync",
    "dd", "mkfs", "fdisk", "kill", "pkill",
    ">", ">>", "|", ";", "&&", "||",
}

# ==================== [ btop 캡처 ] ====================
_BTOP_PATH = "/opt/homebrew/bin/btop"
_BTOP_COLS = 100
_BTOP_ROWS = 40

# ==================== [ Discord UI 제한 ] ====================
MAX_BUTTONS_PER_PAGE  = 20
MAX_WORKSPACE_BUTTONS = 20
MAX_COURSE_BUTTONS    = 18

# ==================== [ 웹 보안 ] ====================
_SUSPICIOUS_TLDS = {".xyz", ".tk", ".ml", ".ga", ".cf", ".gq", ".top", ".click", ".download", ".zip"}
_SUSPICIOUS_KEYWORDS = ["phish", "malware", "virus", "hack", "crack", "keygen", "warez",
                        "free-download", "torrent", "pirat", "nulled", "exploit"]
_IP_URL_PATTERN = re.compile(r'https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')

# ==================== [ 파일 검색 ] ====================
_SEND_FILE_SEARCH_ROOTS = [
    WORK_STATION_ROOT,
    os.path.join(HOME, "Downloads"),
    os.path.join(HOME, "Desktop"),
    os.path.join(HOME, "Documents"),
]
_MAX_SEND_FILE_SIZE = 25 * 1024 * 1024  # Discord 25MB 제한

_EXT_KEYWORDS = {
    "pdf":       [".pdf"],
    "이미지":    [".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"],
    "사진":      [".png", ".jpg", ".jpeg", ".heic"],
    "워드":      [".docx", ".doc"],
    "엑셀":      [".xlsx", ".xls", ".csv"],
    "파워포인트":[".pptx", ".ppt"],
    "발표":      [".pptx", ".ppt", ".key"],
    "텍스트":    [".txt", ".md"],
    "코드":      [".py", ".js", ".ts", ".java", ".swift", ".kt"],
    "압축":      [".zip", ".tar", ".gz", ".7z"],
}

# 한국어 힌트 → 영어 키워드 매핑
_KO_HINT_MAP = {
    "어드민": "admin", "관리자": "admin",
    "컨트롤러": "controller", "서비스": "service",
    "레포지토리": "repository", "레포": "repository",
    "엔티티": "entity", "도메인": "domain",
    "설정": "config", "보안": "security",
    "필터": "filter", "인터셉터": "interceptor",
    "메인": "main", "테스트": "test",
    "로그인": "login", "회원": "member", "유저": "user",
    "오어스": "oauth", "제이더블유티": "jwt", "토큰": "token",
    "파이썬": "python", "자바": "java",
    "자바스크립트": "javascript", "타입스크립트": "typescript",
}

_COMPILED_EXTS = {".class", ".pyc", ".o", ".obj", ".jar", ".war"}
_SOURCE_EXTS   = {".java", ".py", ".js", ".ts", ".kt", ".go", ".rs", ".cpp", ".c", ".cs", ".pdf", ".md", ".txt"}

# ==================== [ 노션 ] ====================
NOTION_CODE_LANGS = {
    "python", "javascript", "typescript", "java", "c", "c++", "cpp",
    "go", "rust", "shell", "bash", "sql", "html", "css", "json",
    "markdown", "plain text", "kotlin", "swift", "ruby", "php"
}

# ==================== [ LMS 캐시 기본값 ] ====================
_LMS_TOPIC_CACHE_DEFAULT  = {"count": 0, "updated": 0}
_CAL_TOPIC_CACHE_DEFAULT  = {"month_count": 0, "updated": 0}

# 캘린더 싱크 대상
_SYNC_CALENDARS = ["캘린더", "junp3610@gmail.com"]
