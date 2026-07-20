# Phase 1: DB 인프라 구축 및 세션 히스토리 영구 저장

**날짜**: 2026-07-20  
**범위**: Phase 1 — 데이터베이스 인프라 셋업 및 대화 기록(Chat History) 연동

---

## 작업 내용 요약

Manta2의 상태 및 대화 기록을 관리하기 위한 로컬 SQLite 기반의 데이터베이스 인프라를 구축했습니다. `langchain_community`의 `SQLChatMessageHistory`를 활용하여 채널별(session_id별) 대화 기록을 영구적으로 저장할 수 있게 되었습니다.

## 구현 상세

### 1. SQLite 데이터베이스 초기화 모듈 구축
- 파일: `infrastructure/database.py`
- 앱 시작 시 자동으로 `data/` 디렉토리를 생성하고 필요한 모든 DB와 테이블을 초기화하는 `init_all_dbs()` 구현.
- **포함된 데이터베이스 목록**:
  - `calendar_store.db`: 캘린더 이벤트 미러 캐시용.
  - `user_profile.db`: 사용자의 자주 찾는 주제(`topics`), 설정(`preferences`) 등을 저장.
  - `chat_history.db`: LangChain과 연동되어 세션별 대화 메시지 기록 보관.

### 2. 세션별 대화 히스토리(Memory) 영구 저장 연동
- 채널 ID(`session_id`)를 기준으로 메시지를 격리하여 저장하는 `get_session_history()` 팩토리 함수 구현.
- 향후 `core/agent.py`에서 `RunnableWithMessageHistory`와 결합되어 봇이 이전 대화의 맥락을 잃지 않고 영구적으로 기억할 수 있게 함.

## 변경된 파일 목록

### 신규 생성
- `infrastructure/database.py`
- `reports/2026-07-20_phase1-db-infrastructure.md` (본 보고서)

## 다음 실행 항목
- [ ] Phase 2 돌입: 기존 33개 도구(Tool)들을 LangChain `@tool` 형태로 이식 (가장 최우선 순위: **캘린더 툴**)
