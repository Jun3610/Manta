# Phase 0 & 0.5 통합 버그 트래킹 및 해결 리포트

## 📖 개요
Phase 0 (롤백/메모리) 및 Phase 0.5 (오류 수정) 작업 과정에서 발견된 주요 버그들의 발생 원인과 해결 과정을 기록한 통합 이슈 문서입니다.

---

## 🐛 버그 목록 및 해결 과정

### 1. SQLite 비동기 통신 라이브러리 부재 (`aiosqlite` & `greenlet`)
- **증상**: `AsyncEngine`을 통해 SQLite DB(`user_memory.db`)에 접근 시 `ValueError: the greenlet library is required...` 에러와 함께 봇 실행 중단.
- **원인**: SQLAlchemy가 비동기 I/O 처리를 위해 `greenlet`에 의존하고, SQLite 비동기 처리에 `aiosqlite` 드라이버가 필요하나 누락됨.
- **해결 과정**: `requirements.txt`에 해당 패키지 추가 후, 환경에 반영하여 정상적인 비동기 DB 통신 복구 완료.

### 2. Apple Calendar 날짜 수정 오류 (-10025 에러)
- **증상**: 캘린더 일정의 시작/종료 시간을 동시에 수정할 때, AppleScript 내부에서 간헐적으로 시작일이 종료일보다 뒤처지는 순간이 발생하며 `-10025` 에러가 뜸.
- **해결 과정**: `apple_calendar_provider.py`의 `modify_event` 메서드에서, 일정을 수정하기 직전에 임시로 종료일을 10년 뒤(아주 먼 미래)로 밀어둔 다음 시작일과 종료일을 순차적으로 안전하게 덮어쓰도록 우회(Workaround) 적용 완료.

### 3. LLM 상대 날짜 파싱 실패 문제
- **증상**: "7월 일정 뭐뭐 있어?" 질문 시 오류는 없으나, 봇이 "7월에는 일정이 없다"며 빈 결과(`📭`)를 반환.
- **원인**: LLM은 룰(SPEC)에 따라 "7월"이라는 문자열을 임의 조작 없이 그대로 `get_events_by_range` 도구로 넘겼으나, 파이썬 코드 단에서 이를 `YYYY-MM-DD` 범위로 치환해주는 로직이 부재하여 DB 조회가 불가능했음.
- **해결 과정**: 
  - `services/date_utils.py` 공통 유틸리티를 신규 생성하여 "X월", "이번 주", "내일", "YYYY-MM" 등 다양한 표현을 정규식으로 안전하게 절대 날짜(`YYYY-MM-DD`) 범위로 환산하도록 구현.
  - 캘린더 조회, 추가 기능(`calendar_tools.py`) 및 일괄 변경(`calendar_bulk_update.py`)이 인자를 받자마자 무조건 이 유틸리티를 거치도록 전면 리팩토링 및 적용 완료.

### 4. 봇 터미널 로깅 지저분함 이슈 (DX 개선 및 필터 적용 시점 문제)
- **증상**: 사용자가 백그라운드에 띄워둔 봇 실행 터미널에 `LangChainDeprecationWarning`과 `httpx/httpcore` INFO 로그 등 불필요한 시스템 메시지가 지나치게 길게 찍힘. 이후 필터를 적용했으나 경고문이 여전히 사라지지 않음.
- **원인**: 파이썬의 `warnings.filterwarnings`가 파일 중간이나 라이브러리 `import` 이후에 선언되어, LangChain 클래스가 메모리에 올라오면서 발생하는 최초 경고를 잡아내지 못함.
- **해결 과정**: 
  - `bot.py` 전역 설정에서 서드파티 HTTP 통신 로그를 Warning 레벨로 격상시켜 숨김 처리.
  - `warnings.filterwarnings` 코드를 `bot.py` 및 `core/agent.py`의 **가장 최상단(다른 모든 패키지 import 이전)**으로 끌어올려 선제적으로 완벽 차단.
  - `AgentExecutor`의 `verbose=False` 처리를 통해 지저분한 중간 툴 고민 과정 메시지를 제거하고 터미널 출력을 깔끔하게 정리.


### 5. 대화 히스토리 세션 미유지 — `AttributeError: 'dict' object has no attribute 'type'`
- **증상**: 매 요청마다 대화 맥락이 초기화되어 "내일 휴식 추가해줘" → "몇 시?" → "1시부터 3시" 같은 다단계 대화가 불가능. 동시에 터미널에 `AttributeError("'dict' object has no attribute 'type'")` 워닝이 반복 출력됨.
- **원인**: `infrastructure/database.py`의 `get_session_history`가 `sqlite+aiosqlite://` (비동기) 드라이버와 `async_mode=True`로 `SQLChatMessageHistory`를 생성하고 있었음. 이 조합에서 메시지 객체가 역직렬화될 때 `LangChain` 내부에서 `dict` 형태로 잘못 변환되어 `.type` 속성이 없는 오류가 발생, 히스토리 로드 자체가 실패함.
- **해결 과정**: `get_session_history` 내 드라이버를 `sqlite:///` (동기)로 교체하고 `async_mode=True` 인자를 제거하여, 메시지 직렬화·역직렬화가 LangChain 표준 경로를 정상적으로 타도록 수정.
- **수정 파일**: `infrastructure/database.py`

### 6. `get_events_by_range` 도구 설명이 LLM의 도구 선택을 잘못 유도
- **증상**: "7월 일정 뭐뭐 있어?" 요청 시, LLM이 날짜 범위 조회 도구(`get_events_by_range`) 대신 키워드 검색 도구(`search_events(keyword="7월")`)를 선택하여, 8월에 있는 "7월_급여" 같은 엉뚱한 일정을 반환함.
- **원인**: `calendar_tools.py`의 `get_events_by_range` docstring에 `"YYYY-MM-DD 형식 필수"` 라고만 명시되어 있어, LLM이 "7월"이라는 자연어를 날짜 형식에 맞지 않는다고 판단하고 도구를 선택 기피함.
- **해결 과정**: docstring의 파라미터 설명을 `"예: 2026-07-01, 7월, 이번 주, 오늘"`과 같이 자연어 예시를 포함하도록 완화하여, LLM이 상대 날짜 표현도 이 도구로 자신 있게 전달하도록 유도. 파이썬 서비스 레이어(`date_utils.py`)가 변환을 담당하므로 기능 정확도는 동일하게 유지됨.
- **수정 파일**: `tools/calendar_tools.py`

### 7. Parse 노드 `day_filter` 단일 요일만 파싱 (복합 요일 누락)
- **증상**: "이번달 금토일 출근을 OP/CL 시간대로 바꿔줘" 요청 시, Preview에 금요일 일정 2건만 나오고 토/일 일정은 완전히 누락됨.
- **원인**: `parse_node`의 시스템 프롬프트 스키마에서 `day_filter`를 단일 문자열(`"friday"`)로만 정의하고 있었음. LLM이 "금토일"을 받으면 단 하나의 요일만 출력하거나 임의의 첫 번째 요일만 선택하는 잘못된 동작을 유발함. `_apply_day_filter`는 이미 쉼표 구분 복합 지정을 지원하고 있었으나 Parse 단에서 막힘.
- **해결 과정**:
  - `parse_node` 시스템 프롬프트의 `day_filter` 스키마를 리스트 형태(`["friday","saturday","sunday"]`)로 변경.
  - 프롬프트에 `"금토일 → ["friday","saturday","sunday"]"` 예시를 명시적으로 주입.
  - `filter_node`에서 `day_filter`가 `list`로 오면 쉼표로 join하여 `_apply_day_filter`에 전달하도록 정규화 로직 추가.
  - LLM이 기존 str로 반환할 경우를 대비한 `isinstance(df, str)` 방어 코드도 parse 단에 추가.
- **수정 파일**: `core/graphs/calendar_bulk_update.py`

### 8. LLM이 OP/CL 시간대를 매 요청마다 재추론 (일관성 불안정)
- **증상**: OP/CL 시간대 수정 명령 시 LLM이 때로는 맞고 때로는 틀린 시간대(예: CL을 16:00~24:00으로 잘못 추론)를 반환하는 간헐적 오류 발생.
- **원인**: Parse 노드의 프롬프트에 OP/CL 기준 시간이 명시되어 있지 않아, LLM이 학습 데이터에서 자체 추론하여 일관성 없는 결과를 냄.
- **해결 과정**: `config.py`의 `DEFAULT_SHIFT_RULES`를 파싱 시점에 Parse 노드 시스템 프롬프트에 문자열로 직접 주입(`shift_rules_str`)하여, LLM이 OP=06:00~15:00, CL=15:00~24:00을 항상 고정값으로 참조하도록 강제.
- **수정 파일**: `config.py`, `core/graphs/calendar_bulk_update.py`

### 9. "추가근무" 표현에 OP/CL 자동 규칙이 잘못 적용됨
- **증상**: "추가근무 일정 넣어줘"처럼 모호한 표현에도 봇이 OP/CL 규칙을 자동으로 적용하여 잘못된 시간대로 일정을 수정하려는 시도가 발생.
- **원인**: 추가근무는 OP/CL과 시간대가 달라 자동 규칙을 적용하면 안 되는 케이스이나, 별도 예외 처리 없이 매칭을 시도함.
- **해결 과정**:
  - `config.py`에 `OVERTIME_KEYWORDS` 상수를 신규 추가(`["추가근무", "연장근무", "오버타임", ...]`).
  - `parse_node` 내 LLM 호출 전, Python 단에서 해당 키워드를 선제적으로 감지하면 `parse_error`를 반환하고, 사용자에게 정확한 시간을 재문의하는 메시지를 출력하도록 처리.
- **수정 파일**: `config.py`, `core/graphs/calendar_bulk_update.py`

### 10. `SQLChatMessageHistory` async/sync 충돌 — `ValueError: Attempting to use async method in sync mode`
- **증상**: "내일 휴식 일정 추가해줘" 요청 시 봇이 `⚠️ 응답 생성에 실패했습니다. 오류 유형: ValueError`를 반환하며 완전히 동작 불가 상태.
- **에러 스택**:
  ```
  ValueError: Attempting to use an async method in when sync mode is turned on.
  Please use the corresponding async method instead.
    → langchain_community/chat_message_histories/sql.py, aget_messages()
  ```
- **원인**: 세션 히스토리 버그(#5) 수정 시 `sqlite+aiosqlite://` 드라이버를 `sqlite:///`(동기)로 교체했으나, `agent_with_history.ainvoke()`는 내부적으로 `aget_messages()`(비동기)를 호출함. `SQLChatMessageHistory`가 동기 모드일 때는 비동기 메서드 자체를 막아버려 `ValueError`가 발생. async 모드 ↔ sync 모드 어느 쪽도 정상 동작하지 않는 `SQLChatMessageHistory`의 구조적 딜레마.
- **해결 과정**: `SQLChatMessageHistory`를 완전히 제거하고 `FileChatMessageHistory`(JSON 파일 기반)로 교체. 채널 ID를 파일명으로 사용하여 `data/chat_history/{채널ID}.json`에 세션별 대화 기록을 저장. async/sync 구분이 없어 충돌 자체가 구조적으로 불가능하며, 디스크에 영구 보존됨.
- **수정 파일**: `infrastructure/database.py`

### 11. Apple Calendar 동기화 범위 고정 캘린더 이름에 의존 (조회 누락)
- **증상**: Apple 캘린더에 일정이 분명히 존재하는데 봇이 "일정이 없다"고 반환. 92개 일정으로 동기화 완료 로그가 찍혀도 특정 캘린더 일정은 누락됨.
- **원인**: `apple_calendar_provider.py`의 `fetch_all_events`가 `self.target_calendars`에 하드코딩된 캘린더 이름 목록(`["캘린더", "Home", "Work"]`)만 순회. 사용자가 다른 이름의 캘린더를 사용 중이면 해당 캘린더의 일정을 전혀 가져오지 못함.
- **해결 과정**: AppleScript의 `repeat with calName in {하드코딩 목록}` 방식을 `repeat with cal in calendars`(모든 캘린더 순회)로 변경하여, 이름에 관계없이 Apple 캘린더에 존재하는 모든 캘린더의 일정을 빠짐없이 동기화하도록 수정.
- **수정 파일**: `providers/apple_calendar_provider.py`
