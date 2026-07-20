# Phase 0 버그 수정: 근무일정 일괄변경 + 롤백 + 사용자 장기 메모리

**날짜**: 2026-07-20  
**범위**: Phase 0 — `calendar_bulk_update` 버그 3종 수정 + 롤백 기능 + 사용자 장기 메모리

---

## 변경된 파일 목록

### 신규 생성

| 파일 | 역할 |
|------|------|
| `infrastructure/rollback_store.py` | 롤백 스냅샷 JSON 저장/로드/삭제 유틸리티 |
| `core/graphs/calendar_rollback.py` | 롤백 LangGraph (Load→Preview→Approval→Execute→Summary) |
| `services/memory_service.py` | 사용자 장기 메모리 CRUD (`save_fact` / `get_facts` / `delete_fact`) |
| `reports/` | 작업 보고서 저장 폴더 (이 파일부터 시작) |

### 수정

| 파일 | 주요 변경 내용 |
|------|--------------|
| `config.py` | `DEFAULT_SHIFT_RULES` 상수 추가 (OP: 06:00~15:00, CL: 15:00~24:00) |
| `infrastructure/database.py` | `user_memory` 테이블 스키마 추가 + `init_all_dbs()` 연결 |
| `services/calendar_service.py` | `build_rollback_snapshot()` 메서드 추가 (execute_node에서 위임) |
| `core/graphs/calendar_bulk_update.py` | 버그 3종 수정 + rollback_snapshot 필드 + `memory_extract_node` 추가 |
| `core/router.py` | 롤백 키워드 (되돌려/롤백/undo/원래대로) → `calendar_rollback` 라우팅 |
| `core/agent.py` | 메모리 주입 (`_build_system_prompt(facts)`) + `_build_runnable()` 동적 생성 |
| `cogs/chat.py` | `_handle_graph`에 `calendar_rollback` 분기 추가 |
| `SPEC.md` | v2.2 업데이트 — 6절(장기메모리), 7절(DEFAULT_SHIFT_RULES), 8절(롤백), 9절(보고서 컨벤션) |

---

## 버그 수정 상세

### Bug 1: 비업무 일정 포함 (필터링 누락)

- **원인**: `rules=null`이면 제목 필터를 건너뜀 → 포토모리, 월급 같은 비업무 일정까지 대상에 포함
- **수정**: `filter_node`에서 `rules` 없을 때 `"출근"` 포함 이벤트만 자동 필터링
- **파일**: `core/graphs/calendar_bulk_update.py` → `filter_node()`

### Bug 2: OP/CL 시간 변경 미실행

- **원인**: `rules=null`이면 `update_event_time()` 자체가 호출되지 않음
- **수정**: 제목에서 OP/CL 자동 추론 → `config.DEFAULT_SHIFT_RULES` 참조
- **우선순위**: 명시적 `rules` > `DEFAULT_SHIFT_RULES` 자동 추론
- **파일**: `config.py`, `core/graphs/calendar_bulk_update.py` → `execute_node()`

### Bug 3: 평일 근무 별도 보고 미구현

- **원인**: `report_filter=weekday` 파싱은 됐지만 보고 로직 없음
- **수정**: `weekday_work_events` 필드 추가 → 미리보기·결과에 별도 섹션 표시
- **파일**: `core/graphs/calendar_bulk_update.py` → `filter_node()`, `preview_node()`, `summary_node()`

---

## 신규 기능

### 롤백 (되돌리기)

```
Execute 시작 전
  └── CalendarService.build_rollback_snapshot(events)  ← 서비스 계층으로 분리
  └── rollback_store.save_snapshot() → data/rollback_snapshot.json

"롤백해줘" / "되돌려줘" / "undo" 입력 시
  └── Router → calendar_rollback 그래프
  └── Load → Preview → Approval → Execute → Summary
  └── 성공 시 snapshot.json 삭제 (중복 롤백 방지)
```

> **제약**: 마지막 1회 일괄 수정만 롤백 가능. 히스토리 스택 없음.

### 사용자 장기 메모리

```
대화 후 (calendar_bulk_update Summary 뒤)
  └── memory_extract_node: LLM 1회로 "기억할 사실" 추출
      → MemoryService.save_fact(channel_id, key, value)
      → 없으면 LLM 호출 자체 생략 (토큰 낭비 없음)

다음 대화 시작 시
  └── MantaAgent.chat() → MemoryService.get_facts(session_id)
  └── _build_system_prompt(facts) → [사용자 정보] 섹션 주입
      → 저장된 사실 없으면 베이스 프롬프트만 사용
```

> ⚠️ **보안**: 비밀번호, 학번, 금융 정보는 추출 프롬프트에 금지 명시.  
> 이 기능은 "사실 저장" 전용이며, LLM이 새 tool/코드를 만들어 실행하는 기능이 **절대** 아님.

---

## 리팩토링

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| OP/CL 시간 상수 위치 | `calendar_bulk_update.py` 인라인 `_DEFAULT_WORK_RULES` | `config.py` `DEFAULT_SHIFT_RULES` (단일 진실 공급원) |
| 스냅샷 수집 로직 | `execute_node` 인라인 반복문 | `CalendarService.build_rollback_snapshot()` 위임 |
| 시스템 프롬프트 구성 | `__init__`에서 정적 생성 | `chat()` 호출 시 `_build_system_prompt(facts)` 동적 생성 |

---

## 검증 결과

```
✅ config.py
✅ infrastructure/database.py
✅ infrastructure/rollback_store.py
✅ services/calendar_service.py
✅ services/memory_service.py
✅ core/agent.py
✅ core/router.py
✅ core/graphs/calendar_bulk_update.py
✅ core/graphs/calendar_rollback.py
✅ cogs/chat.py
```

단위 테스트 (이전 세션 20개 포함):

| 테스트 | 결과 |
|--------|------|
| `_infer_work_type` 6종 | ✅ |
| `_get_work_time_rule` 4종 | ✅ |
| `_weekday_kr` 3종 | ✅ |
| Router 라우팅 7종 (rollback 4 + bulk 2 + agent 1) | ✅ |

---

## 다음 실행 항목

- [ ] 봇 재시작 후 "이번 달 금토일 근무 일정 일괄변경해줘" 실제 테스트
- [ ] Apple Calendar에서 OP/CL 시간 변경 확인
- [ ] "롤백해줘" 입력 → 원래 시간 복원 확인
- [ ] 2회 이상 대화 후 `data/user_memory.db` 사실 저장 확인
