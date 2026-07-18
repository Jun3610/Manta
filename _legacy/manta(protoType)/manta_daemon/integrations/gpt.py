"""
integrations/gpt.py — ai_client, 툴 정의 딕셔너리, LLM 호출 함수들
"""
import subprocess

from manta_daemon.config import (
    _CLAUDE_CLI_PATH, _CLAUDE_CLI_AVAILABLE,
    ANTHROPIC_API_KEY, GEMINI_API_KEY,
)
import manta_daemon.state as state

# ==================== [ SDK 선택적 임포트 (state에서 참조) ] ====================
_anthropic_sdk = state._anthropic_sdk
_genai_sdk     = state._genai_sdk
_HAS_ANTHROPIC = state._HAS_ANTHROPIC
_HAS_GEMINI    = state._HAS_GEMINI

# ai_client는 state에서 가져옴 (중앙화)
ai_client = state.ai_client


# ==================== [ LLM 호출 함수들 ] ====================

def _call_claude_cli(prompt: str) -> str:
    """Claude Code CLI로 텍스트 생성"""
    try:
        result = subprocess.run(
            [_CLAUDE_CLI_PATH, "-p", prompt, "--output-format", "text", "--dangerously-skip-permissions"],
            capture_output=True, text=True, timeout=60
        )
        return result.stdout.strip() or result.stderr.strip() or "❌ Claude CLI 응답 없음"
    except subprocess.TimeoutExpired:
        return "❌ Claude CLI 타임아웃 (60초)"
    except Exception as e:
        return f"❌ Claude CLI 오류: {e}"


def _call_claude_api(prompt: str) -> str:
    if not _HAS_ANTHROPIC or not ANTHROPIC_API_KEY:
        return "❌ Anthropic API 키가 없어요. .env에 ANTHROPIC_API_KEY 추가해줘요."
    try:
        client = _anthropic_sdk.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"❌ Claude API 오류: {e}"


def _call_gemini_api(prompt: str) -> str:
    if not _HAS_GEMINI or not GEMINI_API_KEY:
        return "❌ Gemini API 키가 없어요. .env에 GEMINI_API_KEY 추가해줘요."
    try:
        client = _genai_sdk.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"❌ Gemini API 오류: {e}"


def _call_gpt_api(prompt: str) -> str:
    try:
        resp = ai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content or "❌ GPT 응답 없음"
    except Exception as e:
        return f"❌ GPT API 오류: {e}"


_LLM_CALLERS = {
    "claude_code": ("⚡ Claude Code",  _call_claude_cli),
    "claude":      ("🟣 Claude API",   _call_claude_api),
    "gemini":      ("🔵 Gemini",       _call_gemini_api),
    "gpt":         ("🟢 GPT-4o",       _call_gpt_api),
}


# ==================== [ 툴 정의 딕셔너리 ] ====================

tools = [
    {
        "type": "function",
        "function": {
            "name": "open_mac_app",
            "description": "맥 앱 켜기. '켜줘', '실행해줘' 요청에만 사용.",
            "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quit_mac_app",
            "description": "맥 앱 종료. '꺼줘', '종료해줘' 요청에 사용.",
            "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_notion_app_context",
            "description": "노션 등 특정 앱을 직접 활성화해서 화면 내용 긁어오기. '노션에서 ~해줘' 요청에 사용.",
            "parameters": {"type": "object", "properties": {"target_app_name": {"type": "string"}}, "required": ["target_app_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_mac_mail",
            "description": "맥 Mail 앱 최근 메일 읽기. '메일 확인', '받은 메일', '인증 메일' 요청에 사용.",
            "parameters": {"type": "object", "properties": {"count": {"type": "integer", "default": 5}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_local_file",
            "description": "로컬 파일 내용 읽기 및 표시. 파일 내용을 그대로 보여줄 때 사용. 코드 분석/줄번호 질문엔 analyze_and_suggest_code 사용.",
            "parameters": {"type": "object", "properties": {"target_hint": {"type": "string", "description": "파일명, 폴더명, 확장자 등 자연어 힌트"}}, "required": ["target_hint"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_file_to_discord",
            "description": "맥북에서 파일을 찾아 Discord 채팅방으로 전송. '파일 보내줘', '가져와', '첨부해줘' 요청에 사용. PDF/이미지/문서 모두 가능. work-station, Downloads, Desktop, Documents 검색.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hint": {"type": "string", "description": "찾을 파일명 힌트 (예: 'report.pdf', '발표자료', 'image.png')"},
                },
                "required": ["hint"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pdf",
            "description": "PDF 파일 읽기. 'PDF 열어줘', 'PDF 분석해줘', '몇 페이지 설명해줘' 요청에 사용. 한 번 열면 이후 페이지 질문은 재호출 없이 답변 가능.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_or_hint": {"type": "string", "description": "PDF 파일명 힌트 또는 절대 경로"},
                    "pages":        {"type": "string", "description": "읽을 페이지 범위. '1-5', '3' 형식. 비워두면 전체."},
                },
                "required": ["path_or_hint"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_and_suggest_code",
            "description": "파일 읽고 코드 분석, 줄번호 설명, 버그/수정 제안. 줄번호 언급 시 반드시 사용. 다른 파일 요청 시 컨텍스트 자동 전환.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_hint": {"type": "string"},
                    "question": {"type": "string"}
                },
                "required": ["target_hint", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrap_lms_website",
            "description": "부경대 LMS 스크래핑. URL 없어도 바로 실행.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_notion_page",
            "description": "노션 새 페이지 생성. 코드가 포함된 내용은 자동으로 코드블록으로 감싸짐.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_notion_page",
            "description": "노션 특정 페이지 본문 읽기. 읽은 내용은 컨텍스트로 저장되어 후속 질문(설명, 번역, 분석 등)에 바로 답할 수 있음. 반드시 list_notion_subpages로 page_id 먼저 확인.",
            "parameters": {"type": "object", "properties": {"page_id": {"type": "string"}}, "required": ["page_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_notion_page",
            "description": "노션 페이지 제목/본문 수정. 코드는 자동 코드블록 처리. 반드시 list_notion_subpages로 page_id 먼저 확인.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "new_title": {"type": "string"},
                    "new_content": {"type": "string"}
                },
                "required": ["page_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notion_subpages",
            "description": "노션 하위 페이지 목록 조회 + 삭제 UI 표시. 삭제/수정/읽기 요청 시 반드시 먼저 호출.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_notion_page",
            "description": "기존 노션 페이지 끝에 내용 추가. 기존 내용은 유지됨. '페이지 밑에 추가', '이어서 써줘' 요청에 사용. 반드시 list_notion_subpages로 page_id 먼저 확인.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["page_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_folder_contents",
            "description": "폴더 안에 뭐가 있는지 파일/하위폴더 목록 보기. '폴더에 뭐있어', '목록', '파일 목록' 요청에 사용. 현재 작업공간이 있으면 그 안에서 찾음.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_hint": {"type": "string", "description": "폴더명 힌트. '백엔드', '프로젝트', '현재' 등"}
                },
                "required": ["folder_hint"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lms_get_all_homework",
            "description": "LMS Todo 기반 미완료 과제·강의 목록 조회. '전체 과제', '미제출 과제', '과제 뭐있어', 'LMS 할 일' 요청에 사용.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lms_get_course_homework",
            "description": "특정 과목의 전체 과제 목록 조회 (제출 완료 + 미제출 포함). '~과목 과제 전체', '~과목 과제 몇개야', '제출한거 포함해서' 요청에 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_name": {"type": "string", "description": "과목명 (예: 인간공학, 확률및분포)"}
                },
                "required": ["course_name"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_briefing",
            "description": "특정 날짜 할 일 통합 브리핑 = 캘린더 일정 + LMS 미완료 과제. '오늘 할 일', '내일 할일', '뭐해야해', '브리핑해줘', '일정이랑 과제 같이 알려줘' 요청에 사용. 날짜 지정 없으면 오늘.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {"type": "string", "description": "조회할 날짜 YYYY-MM-DD. 비우면 오늘. 예: '내일 할일' → 내일 날짜 입력."}
                }
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "현재 맥의 CPU / 메모리 / 디스크 / 배터리 상태 확인. '시스템 상태', '메모리 얼마나 써', 'CPU' 요청에 사용.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": "허용된 터미널 명령어 실행 (ls, git, python3 등 화이트리스트). '명령어 실행', '터미널에서 ~해줘' 요청에 사용.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "실행할 명령어 (예: git status, ls -la)"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python_code",
            "description": "Python 코드 스니펫 실행하고 결과 반환. '이 코드 실행해봐', '파이썬으로 ~계산해줘' 요청에 사용.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "실행할 Python 코드"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_local_file",
            "description": "work-station 안에 파일 생성 또는 내용 수정. '파일 만들어줘', '저장해줘' 요청에 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "work-station 기준 상대 경로 (예: MyProject/test.py)"},
                    "content": {"type": "string"}
                },
                "required": ["relative_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_write",
            "description": "코드/글/문서 작성을 다른 LLM(Claude Code, Claude API, Gemini, GPT)에게 위임. '만들어줘', '작성해줘', '코드 짜줘', '구현해줘' 등 창작/생성 작업 요청 시 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {"type": "string", "description": "위임할 작업 전체 내용. 컨텍스트 포함해서 상세하게."}
                },
                "required": ["task_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_git_status",
            "description": "현재 작업공간 또는 지정 폴더의 git 브랜치/변경사항/최근 커밋 조회. 'git 상태', '커밋 뭐있어' 요청에 사용.",
            "parameters": {
                "type": "object",
                "properties": {"folder_hint": {"type": "string", "description": "폴더 힌트 (비워두면 현재 작업공간)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_apple_calendar",
            "description": (
                "Apple 캘린더 일정 조회. 사용자의 자연어 날짜 표현을 YYYY-MM-DD로 변환해서 넘길 것.\n"
                "오늘/내일/모레/이번주/저번주/다음주/이번달/저번달/다음달/올해/작년/지난 N일/최근 N주 등 모든 표현 처리 가능.\n"
                "특정 키워드 일정만 볼 때는 keyword 파라미터 사용 (예: keyword='출근', keyword='기말').\n"
                "통계/분석 질문('며칠 일했어', '총 몇 시간', '출근 몇 번')은 해당 기간 전체 조회 후 계산."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days":       {"type": "integer", "description": "오늘부터 며칠간 (start_date/end_date 우선)"},
                    "start_date": {"type": "string",  "description": "시작일 YYYY-MM-DD"},
                    "end_date":   {"type": "string",  "description": "종료일 YYYY-MM-DD"},
                    "keyword":    {"type": "string",  "description": "제목 필터 키워드 (예: '리마', '출근', '기말'). '리마 관련 일정', '출근 일정만' 등 특정 주제 조회 시 사용."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_apple_calendar_event",
            "description": "캘린더 특정 일정 삭제. '기말 일정 삭제', '오늘 미팅 지워줘', '경제성분석 기말 삭제' 요청에 사용. 특정 제목만 삭제할 때 사용. 날짜 전체 삭제는 delete_all_calendar_events_on_date 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_keyword": {"type": "string", "description": "삭제할 일정 제목에 포함된 키워드"},
                    "date_str":      {"type": "string", "description": "날짜 YYYY-MM-DD (특정 날짜 것만 삭제 시 지정, 선택)"},
                },
                "required": ["title_keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_all_calendar_events_on_date",
            "description": "특정 날짜의 모든 일정 전체 삭제. '오늘 일정 다 삭제', '내일 일정 모두 지워줘', '7월 4일 일정 전부 삭제' 요청에 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {"type": "string", "description": "날짜 YYYY-MM-DD. 비우면 오늘."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_apple_calendar_event",
            "description": (
                "기존 캘린더 일정 수정 (날짜/시간/제목 변경). '마감 출근 19일~21일로 바꿔줘', '미팅 시간 3시로 변경' 요청에 사용.\n"
                "삭제+재추가 말고 실제 수정. title_keyword로 기존 일정을 찾아서 new_* 값으로 변경."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title_keyword":    {"type": "string",  "description": "수정할 기존 일정 제목 키워드"},
                    "search_date":      {"type": "string",  "description": "찾을 날짜 YYYY-MM-DD (선택, 같은 제목 여러 개일 때)"},
                    "new_title":        {"type": "string",  "description": "새 제목 (변경 시)"},
                    "new_date":         {"type": "string",  "description": "새 날짜 YYYY-MM-DD (변경 시)"},
                    "new_time":         {"type": "string",  "description": "새 시작 시간 HH:MM (변경 시)"},
                    "new_duration_min": {"type": "integer", "description": "새 길이(분) (변경 시)"},
                },
                "required": ["title_keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_apple_calendar_event",
            "description": (
                "Apple 캘린더에 일정 추가. '6월 20일 3시에 발표 넣어줘' 요청에 사용.\n"
                "날짜 범위(금~일, 3일 연속 등): end_date 지정 → date_str~end_date 매일 같은 시간에 반복 추가.\n"
                "일정 제목은 반드시 사용자가 말한 그대로 사용. 이미 조회된 다른 일정 이름 절대 사용 금지."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title":         {"type": "string", "description": "일정 제목 (사용자가 말한 그대로)"},
                    "date_str":      {"type": "string", "description": "시작 날짜 YYYY-MM-DD"},
                    "end_date":      {"type": "string", "description": "반복 종료 날짜 YYYY-MM-DD (금~일 등 연속 추가 시 사용)"},
                    "time_str":      {"type": "string", "description": "시작 시간 HH:MM 24시간 형식 (기본 09:00)"},
                    "duration_min":  {"type": "integer", "description": "일정 길이(분) (기본 60)"},
                    "calendar_name": {"type": "string", "description": "캘린더 이름 (기본값: '캘린더')"},
                    "notes":         {"type": "string", "description": "메모/설명 (선택)"},
                    "important":     {"type": "boolean", "description": "'중요', '꼭' 등 강조 시 true. 알림 자동 설정."},
                },
                "required": ["title", "date_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "날씨 조회. 도시 미지정 시 부산 기준. '날씨', '오늘 날씨', '비 와?' 요청에 사용.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "도시명 (부산/서울/대구/인천 등)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_background_tasks",
            "description": "현재 실행 중인 백그라운드 작업 목록 조회. '백그라운드 작업 뭐있어', '실행중인 작업 알려줘' 요청에 사용.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_background_task",
            "description": "실행 중인 백그라운드 작업 취소. '뽀모도로 취소', '타이머 멈춰', '데일리 리포트 종료' 요청에 사용.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "취소할 작업 이름 (뽀모도로/데일리리포트/라벨명)"}},
                "required": ["name"],
            },
        },
    },
]


# ==================== [ analyze_and_save_profile ] ====================

def analyze_and_save_profile(user_msg: str, bot_reply: str):
    """GPT-4o-mini로 대화에서 주제/성향 추출 후 DB 저장 (백그라운드 호출용)"""
    import re as _re
    import json as _json
    try:
        prompt = (
            "아래 대화에서 다음 두 가지를 JSON으로 추출해줘. 없으면 빈 배열/객체.\n"
            "1. topics: 이 대화의 핵심 주제어 1~3개 (한글, 짧게. 예: '캘린더', 'LMS', '코드리뷰', '날씨', '노션')\n"
            "2. prefs: 사용자 성향/선호 파악된 것 (key-value 형태, 예: {\"말투\": \"반말 선호\", \"응답스타일\": \"짧고 간결하게\"})\n"
            "JSON만 출력. 설명 금지.\n\n"
            f"USER: {user_msg[:300]}\nASSISTANT: {bot_reply[:300]}"
        )
        resp = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if not m:
            return
        data = _json.loads(m.group())
        from manta_daemon.integrations.calendar_ops import _user_db_add_topic, _user_db_set_pref
        for t in data.get("topics", []):
            if isinstance(t, str) and t.strip():
                _user_db_add_topic(t.strip())
        for k, v in data.get("prefs", {}).items():
            if isinstance(k, str) and isinstance(v, str):
                _user_db_set_pref(k.strip(), v.strip())
    except Exception as e:
        print(f"[프로파일] 분석 실패: {e}")
