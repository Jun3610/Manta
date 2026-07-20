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

---
*(이슈가 해결되었으므로 본 리포트는 닫힘 상태(Closed)로 간주합니다.)*
