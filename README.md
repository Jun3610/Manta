# Manta2

> 이 README는 SPEC.md 기준으로 자동 생성됩니다. 최신 설계는 [SPEC.md](./SPEC.md)를 참고하세요.

# 리빌드 설계 명세서

> 버전: v2.2 (Phase 0 버그 수정 + 롤백 + 사용자 장기 메모리 반영)
> 기준: manta1(discord.py 단일 프로세스) 코드 분석 + v2.0 아키텍처 + Phase 0 장애 리포트 후속 결정 통합
> 대상: LLM 기반 코딩 에이전트(Aider / Claude Code / Antigravity 등)에게 그대로 투입 가능한 형태

---

## 0. 이 문서의 목적

이전 manta1은 discord.py는 있었지만 **LLM 에이전트 로직(tool 호출 → 실행 → 재입력 루프)을 프레임워크 없이 직접 손으로 짠 상태**였다. 33개에 달하는 tool을 `if func_name == "..."` 체인으로 분기 처리했고, 이 과정에서 다음과 같은 구조적 사고가 실제로 발생했다:

> **`bot.py`, `lifecycle.py`, `message_handler.py` 세 파일이 각각 `@bot.event`로 `on_message`/`on_ready`를 중복 등록.** discord.py의 `@bot.event`는 핸들러를 쌓지 않고 덮어쓰기 때문에, import 순서에 따라 어느 파일의 로직이 "조용히 무시"되는지가 결정되는 사고였다. "봇이 메시지에 2번 응답한다"는 증상은 사실 이 구조적 결함의 곁가지였고, 메시지 ID 중복 체크로 땜빵되어 있었다.

v2.0은 이 실패를 반복하지 않도록 LangChain `AgentExecutor` 프레임워크 위에서 다시 짰다. 그런데 **Phase 0 실가동 중 두 번째 장애가 발생**했고, 이번 v2.1은 그 장애 리포트에서 나온 결론을 구조에 반영한 버전이다.

### 0.1 Phase 0 장애 요약 (v2.1 개정 사유)

- **증상**: "7월 금토일 출근을 OP/CL 태그별로 다른 시간(06:00~15:00 / 15:00~24:00)으로 일괄 변경하고, 평일 근무일은 별도 보고" 요청 처리 중 응답 생성 실패
- **원인 A**: `gemini-3.5-flash` 무료 tier 일일 할당량(20회) 초과 (429 Too Many Requests) — 유료 전환 후에도 재발
- **원인 B**: LLM이 "이번 달"을 상대 연도 없이 2024년으로 잘못 추론 → 불필요한 tool 재호출 1회 발생
- **구조적 결론**: 일정 조회는 정상 동작했으나, **결정론적 작업(날짜 계산·조건 필터링·다중 수정)에도 LLM이 매 단계 관여**하는 구조라 호출 횟수·비용·오류 가능성이 함께 늘어남

이 리포트를 근거로 아래 결정이 내려졌다:

| 결정 사항 | 근거 |
|---|---|
| 런타임 LLM을 Gemini → **Claude API**로 전환 | 무료 tier 재발 문제와 별개로, 날짜 추론 오류 자체가 모델 품질 문제였음. 비용 차이는 개인 프로젝트 규모(연 10만원 안팎)에서 미미 |
| 단순 조회는 `AgentExecutor` 유지, **복잡한 다단계 작업은 LangGraph로 순서 고정** | AgentExecutor는 "몇 번 도구를 부를지"를 LLM이 매번 판단 → 재호출·오류가 통제 밖에 있음. 이번 장애 유형(필터+분기+다중수정+리포트)은 순서를 코드로 못박는 게 안전 |
| 날짜/필터/업데이트 로직을 **Python Service 레이어로 이동**, LLM은 "의도 파싱 + 결과 요약"만 담당 | 날짜 계산은 LLM이 틀릴 수 있는 영역이 아니라 애초에 `datetime.now()` 기준 Python이 처리하면 구조적으로 오류 자체가 불가능해짐 |
| OpenClaw / Hermes(Nous Research) 등 완제품 에이전트 프레임워크는 **검토 후 채택하지 않음** | 0.2절 참조 |
| 개발 도구(Antigravity)와 런타임 LLM(Claude API)은 **완전히 독립적인 결정** | 개발 도구 구독/무료 제공 여부가 런타임 API 선택에 영향을 주지 않음. Claude Pro/Code 구독과 Claude API는 별도 결제 체계이며 상호 대체 불가 |

### 0.2 OpenClaw / Hermes 검토 기록 (참고용, 재검토 시 여기부터 볼 것)

Manta2 설계 중 OpenClaw(Steinberger & Contributors), Hermes Agent(Nous Research) 같은 완성형 자율 에이전트 런타임으로 전환하는 안을 검토했으나 **채택하지 않기로 결정**했다. 이유:

1. **도메인 도구가 핵심**: Manta의 가치는 Apple Calendar/LMS/Notion 등 4장에 명시된 33개 커스텀 도구에 있다. 완제품 프레임워크는 Discord 연결·세션 관리 같은 범용 "몸통"은 대신해주지만, 이 커스텀 도구들은 어느 쪽을 택하든 동일하게 직접 구현해야 한다. 즉 채택 실익이 작업량 감소로 이어지지 않는다.
2. **신뢰성 검증 기간 부족**: 두 프로젝트 모두 2025년 11월~2026년 2월 사이 출시된 신생 프로젝트이며, 보안 취약점 연구·표절 의혹 등 리스크 사례가 존재한다. LMS 학번/비밀번호 등 민감정보를 다루는 Manta에 이 시점에 얹기엔 이르다.
3. **비용은 프레임워크와 무관**: 어느 쪽을 택해도 내부적으로 호출하는 LLM API 비용은 동일하게 발생한다. "완제품이니 비용이 절감된다"는 전제 자체가 성립하지 않는다.
4. **discord.py + LangChain도 이미 검증된 표준 조합**: "내가 만든 것 vs 대기업이 만든 것"이라는 비교는 성립하지 않는다. discord.py는 Discord 공식 라이브러리, LangChain은 업계 표준 오픈소스 프레임워크로, OpenClaw/Hermes보다 검증 기간이 길다.

**재검토 조건**: "여러 메시징 플랫폼(Telegram, Slack 등) 동시 지원이 명확히 필요해졌을 때"처럼 구체적 요구가 생기면 그때 재검토한다. 막연히 "완성도가 높아 보여서" 또는 "이미 유명해서"는 재검토 사유로 채택하지 않는다.

---

## 1. 핵심 설계 원칙 (v1 교훈 + Phase 0 장애 → v2.1 규칙)

| # | 발생한 문제 | v2.1 규칙 |
|---|---|---|
| 1 | on_message/on_ready가 3개 파일에서 중복 등록되어 서로 덮어씀 | **이벤트 리스너는 오직 Cog 안에서, `@commands.Cog.listener()`로만 등록.** 일반 함수 + `@bot.event` 조합은 프로젝트 전체에서 금지. |
| 2 | tool 호출 로직이 여러 파일에 중복 복붙됨 | **파일 삭제는 코드가 아니라 git 브랜치로 관리.** "혹시 몰라서 남겨두는" 파일은 `_legacy/`로 격리하고 import 경로에서 완전히 배제. |
| 3 | LLM tool 선택/실행 루프를 33개 if/elif로 하드코딩 | **LangChain `AgentExecutor` + `@tool` 데코레이터로 도구를 등록식(플러그인) 관리.** 새 도구 추가 시 if/elif를 건드릴 필요 없이 `tools/` 아래 파일 하나 추가 + 리스트 등록만 하면 됨. |
| 4 | 동기 클라이언트를 `run_in_executor`로 감싸 스트리밍 시도 → I/O 블로킹으로 봇 전체 다운 | **비동기 상용 LLM 클라이언트(`ChatAnthropic` 등)만 비동기로 사용.** 스트리밍은 `astream_events` 기반으로만 구현. |
| 5 | LMS 학번/비밀번호 저장 방식이 어디에도 명시되지 않음 | **자격증명은 `.env` 평문 저장을 유지하되, `.gitignore` 확인 + 향후 `keyring`/OS 자격증명 저장소 이관을 Phase 백로그에 명시.** SQLite에는 절대 평문 저장 금지. |
| 6 | 채널별 대화 히스토리 격리 방식이 불명확 | **채널 ID를 key로 하는 세션 딕셔너리로 memory를 명시적으로 분리.** `{channel_id: ChatMessageHistory}` 구조, `RunnableWithMessageHistory`로 격리. |
| 7 | (Phase 0) 무료 tier 할당량 초과(429) + 날짜 추론 오류 → 응답 생성 실패 | **런타임 LLM을 Claude API(`claude-sonnet-5`)로 전환.** 날짜/기간 계산은 LLM 추론에 맡기지 않고 Python이 `datetime.now()` 기준으로 직접 산출(2.2절). LLM 응답 생성 실패 시에도 Tool 결과는 사용자에게 fallback으로 전달(2.3절). |
| 8 | (Phase 0) 결정론적 다단계 작업(필터+분기+다중수정+리포트)에서 LLM이 불필요하게 여러 번 재호출됨 | **복잡도가 높은 작업은 LangGraph로 노드 순서를 고정.** 2.2절 참조. |

### 1.1 언어 강제 프롬프트 (필수 삽입)
`ChatPromptTemplate`의 system 메시지 맨 첫 줄에 아래 문구를 **다른 어떤 지시보다 먼저** 삽입한다.

```
[CRITICAL LANGUAGE CONSTRAINT]
너는 Manta, 한국어 전용 비서야. 모든 응답은 무조건 한국어로만 해.
중국어, 일본어, 영어 등 다른 언어는 절대 사용 금지. 사용자가 외국어를 써도 한국어로 답변해야 해.
도구 호출 인자도 모두 한국어로 작성해.
```

- `temperature=0.3` 이하 유지 (tool-calling 신뢰도 + 언어 일관성 둘 다에 도움)
- 그래도 새는 경우가 잦다면: 응답 후처리 단계에서 정규식으로 한자/히라가나/가타카나 유니코드 범위(`\u4e00-\u9fff`, `\u3040-\u30ff`)를 감지해 재생성 요청하는 필터를 Phase 3(UX 고도화)에 추가 고려.

---

## 2. 아키텍처

```
Controller 계층    : discord.py Cogs
Application 계층   : core/agent.py (단순 요청)  +  core/graphs/*.py (복잡한 다단계 요청)
Service 계층       : services/*.py
Infrastructure 계층: providers/*.py, infrastructure/database.py
Tool 계층          : tools/*.py
Task 계층          : tasks/*.py
```

- Controller(Cog)는 이벤트 수신/응답 송신만 담당한다.
- Application은 요청 복잡도에 따라 두 경로로 나뉜다 (2.1절).
- Service는 비즈니스 로직을 담당한다. **날짜 계산, 요일/조건 필터링, 다중 항목 업데이트는 반드시 Service에서 Python으로 처리하고 LLM에 위임하지 않는다.**
- Infrastructure는 Apple Calendar, SQLite, 외부 API 등 실제 시스템과의 통신을 담당한다.
- Tool은 LLM이 호출하는 진입점 역할만 수행하며, 비즈니스 로직을 직접 포함하지 않는다.

v1에서 이미 검증된 `tasks/` 레이어(daily_report, weekly_report, schedule, system_status, timers, reminders)는 구조를 그대로 계승하고, 내부에서 LLM을 직접 호출하는 부분(있다면)만 Agent 경유로 교체한다.

### 2.1 요청 복잡도에 따른 라우팅 (신규)

| 유형 | 예시 | 처리 경로 |
|---|---|---|
| 단순 조회/단건 작업 | "오늘 일정 알려줘", "이 일정 삭제해줘" | `core/agent.py` — LangChain `AgentExecutor` (자유 루프, tool 1~2회) |
| 복잡한 다단계/조건부 수정 작업 | "이번 달 금토일 출근을 OP/CL별로 다른 시간으로 바꾸고 평일은 따로 보고" | `core/graphs/*.py` — 6단계 전체(Approval 포함), 2.2/2.3절 |
| 자료 생성/보고서 작업 (신규) | "이번 달 캘린더 정리해서 PDF로 줘", "로컬 문서 요약해서 그래프로 보여줘" | `core/graphs/*.py` — Parse→Gather→Render→Summary, **Approval 생략** (2.2절 예외) |

**`core/router.py`(신규)** 가 이 분류를 명시적으로 담당하는 컴포넌트다. Cog에서 메시지를 받으면 바로 `AgentExecutor`나 특정 그래프로 보내지 않고, 반드시 Router를 거친다.

```python
# core/router.py (개념 구조)
def route(user_message: str, channel_id: str) -> RouteDecision:
    """
    반환값: {"path": "agent" | "graph", "graph_name": Optional[str]}
    분류 기준(우선순위 순):
    1. 규칙 기반 키워드 매칭 (예: "일괄", "전부", "~별로 다르게" 등장 시 graph 후보)
    2. 대상 도구가 4장에서 LangGraph 전용으로 명시된 도구(예: bulk_update_events_by_condition)와
       일치하면 graph
    3. 애매하면 경량 LLM 분류 호출 1회(Haiku) 또는 기본값 agent로 폴백
    """
```

- Router의 분류 결과와 근거는 로그에 남겨, 나중에 "이 요청이 왜 이 경로로 갔는지" 추적 가능하게 한다.
- 신규 도구/요청 유형이 "여러 단계 + 조건부 로직 + 다중 항목 처리"에 해당하면 그래프 경로로, 그렇지 않으면 AgentExecutor 경로로 분류한다. 애매한 경우 기본값은 AgentExecutor.

### 2.2 Graph 구조 일반화 (신규)

모든 LangGraph 파이프라인은 아래 6단계를 표준 뼈대로 사용한다. 개별 그래프(`core/graphs/calendar_bulk_update.py` 등)는 이 뼈대에 도메인 로직만 채워 넣는 구현체다.

```
사용자 요청
   ↓
[Parse]      자연어 → 구조화된 명령 (LLM 호출 1회)
             예: {"range": "이번달", "day_filter": "weekend",
                  "rules": {"OP": ["06:00","15:00"], "CL": ["15:00","24:00"]},
                  "report_filter": "weekday"}
   ↓
[Filter]     Service 레이어 순수 Python 함수 (LLM 호출 없음)
             - range를 datetime.now() 기준 실제 날짜로 환산 (2.3절)
             - day_filter/조건으로 대상 항목만 추려냄
             - 이 시점에는 아직 아무것도 실행/수정하지 않는다
   ↓
[Preview]    Filter 결과를 사용자가 확인할 수 있는 형태로 미리 보여줌 (LLM 호출 없음, 템플릿 렌더링)
             예: "다음 5건이 변경됩니다: 7/3 CL2 → 15:00~24:00, ..."
   ↓
[Approval]   실행 여부를 사용자에게 확인받음 (2.3절)
             거부/타임아웃 시 Execute로 진행하지 않고 종료
   ↓
[Execute]    Service 레이어 순수 Python 함수 (LLM 호출 없음)
             - rules에 따라 각 대상 항목을 실제로 수정/생성/삭제
             - 각 작업 성공/실패를 개별 기록 (부분 실패 허용, 5.2.1절 동기화 순서 준수)
   ↓
[Summary]    결과를 사람이 읽기 좋은 형태로 정리 (LLM 호출 1회, 실패 시 2.5절 fallback)
```

- **모든 노드가 모든 그래프에 필수는 아니다.** 예를 들어 조회 전용 그래프는 Preview/Approval/Execute 없이 Parse → Filter → Summary만으로 구성 가능. 단, **데이터를 수정/삭제/생성하는 그래프는 Approval 노드를 생략할 수 없다** (2.3절).
- **예외 — 자료 생성/보고서 작업**: PDF, 그래프, 요약본처럼 "새 산출물을 만들 뿐 기존 데이터를 건드리지 않는" 작업은 원본이 파괴되지 않으므로 Approval을 생략한다. 대신 아래 명칭으로 구성한다 (2.8절 상세):
  ```
  [Parse]   자연어 → 구조화된 명령 (예: {"source": "calendar", "range": "이번달", "output": "pdf"})
     ↓
  [Gather]  Filter에 대응 — 대상 데이터를 실제로 수집·집계 (캘린더 조회, 로컬 문서 읽기 등, LLM 호출 없음)
     ↓
  [Render]  Execute에 대응 — PDF/차트/요약본을 실제로 생성 (LLM 호출 없음, 2.8절 라이브러리 사용)
     ↓
  [Summary] 생성된 파일에 대한 짧은 설명과 함께 Discord로 전달 (LLM 호출 1회, 실패 시 2.5절 fallback)
  ```
- 각 노드는 독립된 함수/파일로 구현하고, 그래프 정의(`core/graphs/*.py`)는 노드를 조립하는 역할만 한다. 노드 자체의 비즈니스 로직은 `services/*.py`에 위치 (4장 Service 레이어 책임 원칙과 동일).

### 2.3 Approval Node (신규)

- **다건 수정/삭제/생성처럼 되돌리기 번거로운 작업은 Execute 전에 반드시 사용자 승인을 받는다.** 이는 이번 장애 사례(다수 이벤트 일괄 수정)처럼 LLM의 조건 해석이 미묘하게 틀렸을 때, 실행 전에 사람이 잡아낼 수 있는 마지막 안전장치다.
- Discord 구현: Preview 노드가 만든 요약과 함께 "✅ 실행" / "❌ 취소" 버튼을 첨부해 전송. 버튼 클릭(또는 정해진 타임아웃, 예 5분)까지 그래프 실행을 일시 정지(LangGraph의 `interrupt`/체크포인트 기능 활용).
- 승인 없이 즉시 실행해도 되는 예외: 단건 조회, 단건 생성처럼 되돌리기 쉬운 AgentExecutor 경로의 작업 (이 경우 Approval 노드 자체가 없음, 2.2절 참고).
- 타임아웃/거부 시 Execute를 건너뛰고 "취소되었습니다"로 Summary를 대체한다. 이 경우도 사용자에게 반드시 결과를 알린다(무응답 금지).

### 2.4 날짜/기간 계산 원칙

- **LLM은 상대적 시간 표현("이번 달", "지금까지", "다음 주")을 그대로 Parse 결과에 문자열로 남기고, 실제 날짜 변환은 하지 않는다.**
- Parse가 출력한 상대 표현은 Filter 노드에서 `user_time_v0` 또는 서버 `datetime.now()` 기준으로 절대 날짜 범위로 환산한다.
- 이 원칙으로 Phase 0에서 발생한 "이번 달을 2024년으로 잘못 추론" 유형의 오류는 구조적으로 재발 불가능해진다.

### 2.5 LLM 실패 시 Fallback 정책

- Tool 호출과 데이터 조회는 성공했으나 최종 응답 생성 단계(Summary 노드 등)에서 LLM 호출이 실패(429, timeout, 기타 API 오류)한 경우, **원본 Tool/Execute 결과를 규칙 기반 템플릿으로 정리해 사용자에게 반환**한다. 빈 응답이나 에러 메시지만 보내지 않는다.
- 예시 fallback 출력:
  ```
  일정 조회 결과:

  주말 출근:
  - 7/3 CL2
  - 7/4 CL1
  - 7/5 CL1

  평일 출근:
  - 7/6 CL1

  (※ 자동 요약 생성에 실패해 원본 데이터를 표시합니다)
  ```
- 구현 위치: `core/agent.py`, `core/graphs/*.py` 양쪽 모두 LLM 호출부를 try/except로 감싸고, 실패 시 `services/fallback_formatter.py`(신규)를 호출.

### 2.6 LLM Provider 추상화 (신규)

- 지금까지 "config 스위치 하나로 교체 가능"이라는 서술적 원칙만 있었으나, 이를 명시적인 인터페이스로 고정한다.
- `core/providers/base.py`에 공통 인터페이스(예: `LLMProvider` 프로토콜/추상클래스)를 정의하고, `core/agent.py`와 `core/graphs/*.py`는 **구체 클래스(`ChatAnthropic`, `ChatGoogleGenerativeAI` 등)를 직접 import하지 않고 이 인터페이스에만 의존**한다.

```python
# core/providers/base.py (개념 구조)
class LLMProvider(Protocol):
    def get_chat_model(self, role: Literal["parse", "summary", "chat"]) -> BaseChatModel: ...
```

- `role`별로 다른 모델을 매핑할 수 있게 한다 — 예: `parse`/`summary`는 저비용 모델(Haiku), 사용자와 직접 대화하는 `chat`(AgentExecutor)은 상위 모델(Sonnet). 이는 2.4절 다운그레이드 검토를 코드 구조로 뒷받침한다.
- 현재 기본 구현체: `AnthropicProvider` (`claude-sonnet-5` 기본, `role` 매핑은 `config.py`에서 관리).
- Provider 교체(예: 향후 Gemini 재검토, 로컬 모델 실험)는 `config.py`의 provider 이름 문자열 하나만 바꾸면 되도록 유지한다. 새 provider 추가 시 `core/providers/` 아래 파일 하나 + `LLMProvider` 구현만 하면 되고, `agent.py`/`graphs/*.py`는 수정하지 않는다.

### 2.7 Metrics 저장소 예약 (신규)

- Phase 0 장애(할당량 소진)가 사후에야 로그로 확인된 점을 감안해, **LLM 호출 자체를 관측 가능하게 만드는 저장소를 예약**한다. Phase 0에서 스키마만 만들고, 실제 대시보드/알림은 이후 Phase 백로그로 둔다.
- `infrastructure/metrics.py` + SQLite 테이블(`llm_calls`)로 다음을 매 LLM 호출마다 기록:
  - `timestamp`, `channel_id`, `role`(parse/summary/chat), `model`, `provider`
  - `input_tokens`, `output_tokens`, `latency_ms`
  - `status`(success/error), `error_type`(429/timeout/기타, 실패 시)
- 이 테이블이 있으면: (a) 하루 호출 횟수를 실시간으로 계산해 할당량 근접 시 경고 가능, (b) 어떤 role/그래프가 호출을 가장 많이 쓰는지 파악해 Haiku 다운그레이드 우선순위 판단 가능, (c) 이번 같은 장애 발생 시 "언제부터 실패가 시작됐는지" 로그 대신 쿼리로 바로 확인 가능.
- Phase 0 범위: 테이블 생성 + 기록만 구현 (조회용 명령어나 대시보드는 Phase 3 이후 백로그).

---

## 3. 기술 스택

- **UI Interface**: `discord.py` (Cogs 기반, v1과 동일하게 유지)
- **AI Framework**: `LangChain` (`langchain-anthropic`, `AgentExecutor`, `RunnableWithMessageHistory`) + `LangGraph` (복잡한 다단계 작업 전용, 2.2절)
- **Routing**: `core/router.py` — 요청을 AgentExecutor/그래프 경로로 분류 (2.1절)
- **Provider 추상화**: `core/providers/` — `LLMProvider` 인터페이스, role별 모델 매핑 (2.6절)
- **주력 LLM**: Anthropic Claude `claude-sonnet-5` (Tool Calling 신뢰도, 다단계 추론, 한국어 성능 검증됨)
- **DB**: SQLite (대화 히스토리, 캘린더 캐시, 유저 프로필, LLM 호출 metrics — v1의 `calendar_store.db`, `user_profile.db` 계승 + `llm_calls` 신규)
- **개발 도구**: Antigravity (학교 제공 Pro, 런타임과 무관)
- **환경**: MacBook M4 Pro, 24GB RAM, macOS

---

## 4. 기존(v1) 기능 전체 목록 — 코드 분석 기반 확정본

> 아래는 실제 `bot.py`/`tool_executor.py`/`tasks/*.py`에서 확인된 전체 기능이다. 32~33개 도구 + 6개 백그라운드 태스크로 구성된다.

### A. 캘린더 (Apple Calendar 연동)
| 도구 | 기능 | 처리 경로 |
|---|---|---|
| get_today_events | 오늘 일정 조회 | AgentExecutor |
| get_tomorrow_events | 내일 일정 조회 | AgentExecutor |
| get_events_by_range | 기간 기반 일정 조회 | AgentExecutor |
| search_events | 키워드 기반 일정 검색 | AgentExecutor |
| get_event_detail | event_uid 기반 상세 조회 | AgentExecutor |
| add_apple_calendar_event | 단일/연속 일정 추가 | AgentExecutor |
| modify_apple_calendar_event | event_uid 기반 수정 | AgentExecutor (단건) / LangGraph (조건부 다건, 2.1절) |
| delete_apple_calendar_event | event_uid 기반 삭제 | AgentExecutor |
| delete_all_calendar_events_on_date | 날짜 단위 전체 삭제 | AgentExecutor |
| **bulk_update_events_by_condition** (신규) | 요일/태그 등 조건 기반 다건 일괄 수정 | **LangGraph 전용**, Parse→Filter→Preview→Approval→Execute→Summary 전체 6단계 사용 (2.2, 2.3절) |

### B. LMS (부경대 학사 연동)
| 도구 | 기능 |
|---|---|
| `lms_get_courses` | 수강 과목 목록 (KJKEY 매핑) |
| `lms_get_all_homework` | 전체 미제출 과제 조회 (Todo 기반) |
| `lms_get_course_homework` | 특정 과목 전체 과제 (약어 매핑: 컴개론→컴퓨터과학개론 등) |
| `scrap_lms_website` | LMS 외 사이트명 기반 스크래핑 (`SITE_NAME_MAP` 매핑, 20+ 사이트) |
| — | **방학 모드**: `_vacation_mode` 플래그로 LMS 기능 전체 비활성화, 개강일 자동 감지 후 해제 |

### C. Notion 연동
| 도구 | 기능 |
|---|---|
| `create_notion_page` | 페이지 생성 |
| `read_notion_page` | 페이지 읽기 (줄번호 포함 컨텍스트 로드) |
| `update_notion_page` | 페이지 수정 |
| `append_to_notion_page` | 끝에 이어쓰기 (update와 구분) |
| `list_notion_subpages` | 하위 페이지 목록 (삭제/수정 전 page_id 확인용) |
| `delete_notion_page` | 페이지 삭제 |
| `get_notion_app_context` | 맥 Notion 앱에서 현재 열린 컨텍스트 가져오기 |

### D. 파일 / 코드 / Git
| 도구 | 기능 |
|---|---|
| `read_local_file` | 로컬 파일 읽기 |
| `write_local_file` | 파일 생성/수정 (작업공간 상대경로 기준) |
| `read_pdf` | PDF 페이지 단위 읽기 |
| `analyze_and_suggest_code` | 코드 분석 + 줄번호 기반 질의응답 |
| `list_folder_contents` | 폴더 탐색 (한국어 힌트 → 영어 키워드 매핑, `_KO_HINT_MAP`) |
| `send_file_to_discord` | 파일 검색(work-station/Downloads/Desktop/Documents) 후 Discord 첨부 (후보 다중 시 버튼 선택 UI) |
| `get_git_status` | Git 상태 조회 |
| `run_terminal_command` | 화이트리스트 기반 터미널 실행 (`ALLOWED_TERMINAL_CMDS`/`BLOCKED_TERMINAL_CMDS`로 rm/sudo/curl 등 차단) |
| `run_python_code` | 파이썬 코드 실행 |
| `delegate_write` | 코드/문서 창작 요청을 위임 처리 (직접 생성 금지 원칙) |

### E. 시스템 / macOS 제어
| 도구 | 기능 |
|---|---|
| `open_mac_app` / `quit_mac_app` | 화이트리스트 기반 앱 실행/종료 (`ALLOWED_MAC_APPS`) |
| `read_mac_mail` | Mail.app 메일 조회 |
| `get_system_status` | CPU/RAM/배터리/디스크 상태 (PIL 렌더링 카드 별도 — 아래 태스크 참조) |

### F. 통신 / 기타
| 도구 | 기능 |
|---|---|
| `get_weather` | 날씨 조회 (기본 도시: 부산) |
| `get_gmail_inbox` | Gmail 받은편지함 조회 (읽음/안읽음, 발신자명 정규화) |
| `get_daily_briefing` | 캘린더+LMS 통합 브리핑 |
| `list_background_tasks` / `cancel_background_task` | 실행 중인 타이머/리마인더 조회·취소 |

### G. 백그라운드 태스크 (도구가 아닌 상시 루프)
| 태스크 | 주기 | 기능 | LLM 호출 여부 |
|---|---|---|---|
| `daily_report` | 매일 00:00 | 당일 일정 + LMS 미제출 자동 보고, 방학 종료 자동 감지 | 없음 (순수 데이터 조회/포맷) |
| `weekly_report` | 매주 일 21:00 | 다음주 일정 + 주간 체중 그래프(`health` 채널) | 없음 |
| `schedule` (토픽 갱신) | 5분 | 채널 토픽에 월간 일정 수 + LMS 미제출 수 표시 (캐시 TTL 300s/600s) | 없음 |
| `system_status` | 60초 | PIL로 직접 렌더링한 시스템 카드(CPU/RAM/배터리/디스크/네트워크/상위 3프로세스) PNG 전송 | 없음 |
| `timers` | 즉시 실행형 | 일반 타이머 + 뽀모도로(집중/휴식 반복) | 없음 |
| `reminders` | 즉시 실행형 | 자연어 시간 파싱("30분 뒤", "내일 저녁 6시에" 등) → 알림 예약 | 파싱만 LLM 또는 정규식 (2.2절 원칙 적용) |

> **확인 사항**: 위 6개 태스크는 설계상 LLM 호출이 없어야 하나, 실제 v1 코드에 LLM 호출이 섞여 있는지 Phase 2 이식 시 반드시 재확인한다. 이는 하루 288회(5분 주기) 같은 고빈도 태스크에서 LLM을 호출하면 할당량/비용을 가장 빠르게 소진하는 지점이기 때문이다.

### H. 명령어 체계 (LLM 우회, 정규식/Prefix 즉시 실행)
- `!tts [텍스트]`, `!청소`/`청소`/`클리어`, `!버그리포트`
- `!만타재시작`/`!만타종료`/`!만타수정` (MANTA_CHANNEL 전용)
- `!방학`/`!개강` (LMS_CHANNEL 전용)
- `작업`/`폴더 선택`(작업공간 진입 UI) / `나가자`/`작업 종료`(작업공간 해제)
- `뽀모도로 N회 M분작업` (정규식 매칭)
- `N분 뒤에 [할일] 알려줘` (정규식 매칭)
- `lms`/`강의`/`강의 선택` (강의 버튼 UI)

### I. 채널별 접근 제어
- `SCHEDULE_CHANNEL`: 일정/과제 질문만 허용, 그 외 주제는 `#manta`로 안내
- `LMS_CHANNEL`: LMS 관련 질문만 허용
- `MANTA_CHANNEL`: 봇 자체 관리 명령어(`!만타재시작` 전용)

### J. Voice (음성) — **차기 개발 예정, 이번 리빌드 범위 제외**
> v1에는 `voice.py` 및 `bot.py` 내 STT(Whisper 기반)/TTS(OpenAI TTS) 로직이 이미 존재하나, 오너 요청에 따라 **이번 Phase 로드맵에서는 제외**하고 별도 사이클로 진행한다. 단, `core/agent.py` 설계 시 음성 입출력이 나중에 얹혀도 되도록 인터페이스만 열어둔다.

### Discord Output Safety
Discord 메시지 최대 길이(2000자)를 초과할 가능성이 있는 경우:
1. 결과를 요약
2. 상위 N개만 표시
3. "외 n개 일정 존재" 표시

### Service 레이어 책임
1. 절대 원본 전체를 Discord에 출력하지 않는다.
2. Tool은 입력 검증과 Service 호출만 담당한다.
3. 비즈니스 로직은 Service에만 존재한다.
4. **날짜/기간 계산, 조건 필터링, 다중 항목 업데이트는 Service의 순수 Python 함수로만 수행한다 (2.2절).**

Tool 내부에서:
- SQLite 직접 접근 금지
- Apple Calendar 직접 접근 금지
- 비즈니스 규칙 구현 금지

### Sync 정책
Sync 실패 시 Apple Calendar를 우선한다.
SQLite Cache 손상 시:
1. Cache 초기화
2. Apple Calendar Full Sync 재실행

으로 복구한다. SQLite는 영구 저장소가 아니다.

---

## 5. 보안/동시성 설계

### 5.1 자격증명 보안
- LMS 학번/비밀번호는 `.env`에만 위치, 코드/DB 어디에도 평문 하드코딩 금지
- `.gitignore`에 `.env`, `*.db`, `venv/` 포함 확인
- Claude API 키도 동일 원칙 적용 — `.env`에만 위치, git에 커밋 금지
- 향후 백로그: macOS Keychain 연동으로 이관 (Phase 3 이후 선택 과제)

### 5.2 멀티채널 동시성
- Agent 인스턴스는 프로세스당 1개(싱글톤)이되, `RunnableWithMessageHistory`에 `session_id=channel_id`를 명시적으로 전달하여 채널 간 대화 히스토리 교차 오염을 방지
- 동시에 여러 채널에서 멘션이 들어와도 각 요청은 독립된 코루틴으로 처리되며, SQLite 접근은 커넥션 풀 또는 WAL 모드로 동시 쓰기 충돌 방지

원칙: Apple Calendar = Source of Truth / SQLite = Cache Only / event_uid = Single Source Identifier

### 5.2.1 캘린더 캐시 동기화
Apple Calendar를 Source of Truth로 사용한다. SQLite는 검색 성능 향상 및 빠른 조회를 위한 캐시(Mirror DB)로만 사용한다.

Manta를 통해 일정을 생성/수정/삭제한 경우:
1. Apple Calendar 반영
2. 성공 시 SQLite 캐시 반영

순서로 처리한다. 사용자가 iPhone 또는 Calendar.app에서 직접 수정한 변경사항은 주기적 Sync Task를 통해 SQLite 캐시에 반영한다. 상세 내용은 `get_event_detail(event_uid)`를 통해 조회한다.

`calendar_sync_task`는 기본 5분 주기로 실행한다. 이 Task는 iPhone 또는 Calendar.app에서 직접 수행된 외부 변경사항을 SQLite Cache에 반영하기 위한 목적이다. Manta를 통한 CRUD 직후에는 Full Sync를 수행하지 않는다.

### 5.3 중복 이벤트 처리 방지
- `processed_message_ids` 방식은 유지하되, 이는 "이중 응답 방지"용 안전장치일 뿐 **근본 해결책은 1장 규칙 1번(Cog 단일 등록)**임을 명시

---

## 6. Phase 로드맵

### Phase 0 — 코어 인프라 (신규 프레임워크 도입)
- `core/providers/base.py` + `core/providers/anthropic_provider.py`: `LLMProvider` 인터페이스와 기본 구현체 (2.6절) — 다른 Phase 0 항목보다 먼저 구현 (agent/graph가 이 인터페이스에 의존하므로)
- `core/router.py`: 요청을 AgentExecutor/그래프 경로로 분류하는 Router (2.1절)
- `core/agent.py`: `LLMProvider` 경유로 `AgentExecutor` + `RunnableWithMessageHistory` 조립 (구체 클래스 직접 import 금지)
- `core/agent.py`: 시스템 프롬프트에서 `temperature≤0.3` 설정
- `core/graphs/calendar_bulk_update.py`: Parse→Filter→Preview→Approval→Execute→Summary 6단계 파이프라인 최초 구현 (2.2, 2.3절)
- `core/graphs/nodes/approval.py`: Discord 버튼 기반 승인 노드 공통 구현 (2.3절, 다른 그래프에서도 재사용)
- `infrastructure/metrics.py` + `llm_calls` 테이블: LLM 호출 기록 (2.7절)
- `services/fallback_formatter.py`: LLM 호출 실패 시 규칙 기반 응답 생성 (2.5절)
- `cogs/chat.py`: `commands.Cog` + `@commands.Cog.listener()`로 `on_message` **단 하나만** 등록
- Claude API 키 발급 (console.anthropic.com), `.env`에 `ANTHROPIC_API_KEY` 등록
- 검증: 봇 기동 → 멘션 응답 정상 작동, 로그에 이벤트 핸들러 중복 등록 경고 없음, "이번 달 금토일 일괄 변경" 요청이 정확한 연도/날짜로 처리되고 실행 전 Approval 버튼이 표시됨, `llm_calls` 테이블에 호출 기록이 쌓임

### Phase 1 — DB 인프라
- `infrastructure/database.py`: SQLite 커넥션 (v1의 `calendar_store.db`, `user_profile.db` 스키마 계승)
- `ChatMessageHistory`를 세션(channel_id)별로 DB에 영구 저장

### Phase 2 — 기존 도구 이식 (4장의 A~F 항목 전체, 33개)
이식 우선순위:
1. 캘린더 (A) — Tool Calling 검증 1순위, `bulk_update_events_by_condition`은 LangGraph 경로로 별도 구현
2. 타이머/리마인더/시스템상태 (G 일부, **LLM 무관 태스크이므로 LLM 호출 자체가 없는지 재확인 후 이관**)
3. LMS (B)
4. Notion (C)
5. 파일/Git/시스템 제어 (D, E)
6. Gmail/날씨 등 나머지 (F)

캘린더 이식 시 다음 구조를 따른다:

```
Tool → CalendarService → AppleCalendarProvider → Apple Calendar
                                 ↘ SQLite Cache
```

- Apple Calendar를 Source of Truth로 사용한다. SQLite는 조회 성능 향상을 위한 Mirror Cache로만 사용한다.
- Manta를 통한 CRUD 성공 시: 1) Apple Calendar 반영 → 2) SQLite Cache 반영 순서로 처리한다.
- 일정 수정/삭제는 title 기준이 아닌 event_uid 기준으로 수행한다.
- 캘린더 Tool은 전체 일정 반환을 금지한다. Tool은 최대 50개 일정까지만 반환하며, 상세 조회는 event_uid 기반으로 수행한다.

각 도구는 v1 코드를 복붙하지 않고 `@tool` 데코레이터 + 명확한 Docstring으로 새로 작성. 단, **LMS 약어 매핑, 사이트명 매핑, 화이트리스트 커맨드 목록 등 v1에서 검증된 상수(config.py 값)는 그대로 재사용**.

### Calendar Tool Response Policy
캘린더 Tool은 원본 데이터를 그대로 반환하지 않는다. 조회 Tool은 반드시 Summary 형태로 응답한다.

반환 규칙: 최대 50개 일정 / 제목 / 시작시간 / 종료시간 / event_uid 만 반환 가능. description, notes, attendees, url 등 대용량 필드는 기본 조회에서 반환 금지. 상세 정보가 필요한 경우, `get_event_detail(event_uid)`를 별도 호출한다.

### Phase 3 — UX 고도화
- `astream_events` 기반 스트리밍 (비동기 클라이언트 필수, 1장 규칙 4)
- 에러 리포트 중앙화 (`ErrorReporter` 클래스, 채널 컨텍스트 일관 전달)
- 명령어 체계(H) 이식 — LLM 우회 정규식/Prefix 처리
- 필요 시 파싱/요약 단계 모델을 `claude-sonnet-5` → `claude-haiku-4-5-20251001`로 다운그레이드 테스트 (2.4절)

### Phase 4 (백로그) — Voice
- v1의 STT/TTS 코드를 참고하되, Phase 0~3 완료 후 별도 사이클로 착수

---

## 7. 파일 구조 (목표)

```
manta2/
├── .env                         # ANTHROPIC_API_KEY 포함
├── config.py
├── bot.py                       # main()만 존재, 이벤트 핸들러 등록 금지
├── cogs/
│   └── chat.py                  # on_message 리스너 (유일한 등록 지점)
├── core/
│   ├── router.py                 # 요청 → AgentExecutor/그래프 경로 분류
│   ├── agent.py                  # AgentExecutor, 세션 관리 (단순 요청)
│   ├── providers/                # LLM Provider 추상화
│   │   ├── base.py               # LLMProvider 인터페이스
│   │   └── anthropic_provider.py # 기본 구현체 (role별 모델 매핑)
│   └── graphs/                   # LangGraph 파이프라인 (복잡한 다단계 요청)
│       ├── calendar_bulk_update.py
│       └── nodes/
│           └── approval.py       # 공용 승인 노드 (Discord 버튼)
├── infrastructure/
│   ├── database.py               # SQLite
│   └── metrics.py                # llm_calls 기록
├── tools/
│   ├── calendar_tools.py
│   ├── lms_tools.py
│   ├── notion_tools.py
│   ├── file_tools.py
│   ├── system_tools.py
│   └── gmail_tools.py
├── tasks/                       # v1 구조 계승
│   ├── calendar_sync_task.py
│   ├── daily_report.py
│   ├── weekly_report.py
│   ├── schedule.py
│   ├── system_status.py
│   ├── timers.py
│   └── reminders.py
├── services/
│   ├── calendar_service.py      # 날짜/필터/업데이트 순수 Python 로직 포함
│   ├── lms_service.py
│   ├── notion_service.py
│   └── fallback_formatter.py    # LLM 실패 시 규칙 기반 응답 생성
├── providers/
│   └── apple_calendar_provider.py
└── _legacy/                     # v1 코드 참고용 (import 경로에서 완전 배제)
```

---

## 8. 구현 시 주의사항

1. **SPEC.md에 명시되지 않은 기능 추가 금지**
2. **기존 v1 코드를 그대로 복붙하지 말고 구조만 참고**
3. **모든 Phase 완료 후 반드시 다음 항목을 보고할 것:**
   - 생성 파일 목록
   - 삭제 파일 목록
   - 변경 파일 목록
   - 아키텍처 다이어그램
4. **추측 구현 금지**
5. **모호한 부분 발견 시 코드 작성 전에 질문**
6. **Phase 단위 커밋 수행**
7. **Phase 완료 후 테스트 결과 제출**
8. **실패한 테스트가 있으면 다음 Phase 진행 금지**
9. **구현/수정 완료 시 `reports/YYYY-MM-DD_작업명.md` 형식으로 보고서 저장 (9절 참조)**

---

## 6. 사용자 장기 메모리

대화 세션이 끝나도 사용자에 대한 사실(선호/습관/일정 패턴)을 기억해 다음 대화에서 컨텍스트로 활용한다.

### 6.1 구현 파일

| 파일 | 역할 |
|------|------|
| `infrastructure/database.py` | `user_memory` 테이블 스키마 정의 및 초기화 |
| `services/memory_service.py` | `save_fact()` / `get_facts()` / `delete_fact()` CRUD |
| `core/agent.py` | `chat()` 호출 시 `get_facts()`로 메모리 조회 후 시스템 프롬프트에 주입 |
| `core/graphs/calendar_bulk_update.py` | `memory_extract_node` (Summary 뒤 선택적 단계) |

### 6.2 테이블 스키마 (`data/user_memory.db`)

```sql
CREATE TABLE user_memory (
    channel_id TEXT NOT NULL,   -- Discord 채널 ID (세션 격리 키)
    key        TEXT NOT NULL,   -- 사실 분류 키 (예: "work_schedule", "habit")
    value      TEXT NOT NULL,   -- 자연어 사실
    updated_at TEXT NOT NULL,   -- ISO 8601
    PRIMARY KEY (channel_id, key)
);
```

동일 `(channel_id, key)` 조합은 UPSERT로 최신 값만 유지한다.

### 6.3 메모리 주입 흐름

```
MantaAgent.chat(session_id, message)
  └── MemoryService.get_facts(session_id) → {key: value} dict
  └── _build_system_prompt(facts) → 베이스 프롬프트 + [사용자 정보] 섹션 주입
      → 저장된 사실이 없으면 베이스 프롬프트만 사용 (토큰 절약)
```

### 6.4 보안 원칙

⚠️ **절대 저장 금지 항목:**
- 비밀번호, PIN, 보안 코드
- 주민등록번호, 학번, 계좌번호
- 의료 정보

`memory_extract_node` 프롬프트에 이 금지 목록이 명시되어 있으므로 LLM이 추출을 시도하더라도 저장되지 않는다.

**이 기능은 "사실 저장" 전용이다.** LLM이 스스로 새 tool이나 코드를 만들어 실행하는 기능이 아니며, 그런 기능은 **절대 추가하지 않는다.**

---

## 7. 근무 시간 기본 규칙 (DEFAULT_SHIFT_RULES)

### 7.1 정의 위치

`config.py` 하단에 `DEFAULT_SHIFT_RULES: dict[str, tuple[str, str]]`로 정의.
**시간대 변경 또는 새 태그 추가 시 이 파일만 수정하면 전체에 반영된다.**

### 7.2 적용 우선순위

| 우선순위 | 조건 |
|---------|------|
| 1위 (highest) | 사용자가 명시적으로 다른 시간 지정 (`parsed_command.rules` dict 존재 시) |
| 2위 | `matched_rule_key`("OP" / "CL")로 `config.DEFAULT_SHIFT_RULES` 자동 추론 |

### 7.3 현재 규칙

| 태그 | 시작 | 종료 | 비고 |
|------|------|------|------|
| OP | 06:00 | 15:00 | 오전 6시 ~ 오후 3시 (휴게 12:00~13:00) |
| CL | 15:00 | 24:00 | 오후 3시 ~ 자정 (휴게 18:00~19:00) |

---

## 8. 롤백(Rollback) 기능

### 8.1 동작 원리

```
일괄 수정 Execute 시작 전
  └── CalendarService.build_rollback_snapshot(events) → 원본 상태 dict 목록
  └── rollback_store.save_snapshot() → data/rollback_snapshot.json 저장

"롤백해줘" / "되돌려줘" / "undo" / "원래대로" 입력 시
  └── Router → calendar_rollback 그래프
  └── Load → Preview → Approval → Execute → Summary
  └── Execute: CalendarService.modify_event()로 원본 시간 복원
  └── 성공 시 rollback_snapshot.json 삭제 (중복 롤백 방지)
```

### 8.2 제약

- **마지막 1회** 일괄 수정만 롤백 가능 (단일 스냅샷, 히스토리 스택 없음)
- `data/rollback_snapshot.json`은 `.gitignore`에 의해 **커밋 금지** (`*.json` 규칙에 포함)
- 롤백도 Approval 노드 필수 (데이터 수정 작업이므로 생략 불가)

---

## 9. 보고서 컨벤션 (reports/)

구현/수정 작업을 완료할 때마다 `reports/` 폴더에 마크다운 파일로 보고서를 저장한다.

### 파일명 규칙

```
reports/YYYY-MM-DD_작업명.md
예: reports/2026-07-20_phase0-bugfix-rollback-memory.md
```

### 필수 형식

- **첫 줄**: `# ` 로 시작하는 제목 한 줄 (GitHub 이슈 제목으로 자동 사용)
- 변경된 파일 목록
- 버그 수정 / 신규 기능 / 리팩토링 상세
- 검증 결과 (문법 검사, 단위 테스트)
- 다음 실행 항목


---

## 10. Manta Phase 5 — 확장 로드맵 (Discord Activity 대시보드 + 상시 TTS)

> 이 문서는 `SPEC.md`(Phase 0~4)와 별개의 성격을 띄나, 통합 관리를 위해 이 곳에 명시한다.
**전제조건: SPEC.md의 Phase 0~4가 전부 완료되고 안정화된 이후에 착수한다.**

### 10.1. Discord Activity 상시 대시보드

#### 목적
- SPEC.md 2.7절에서 예약해둔 `llm_calls` metrics 테이블(호출 횟수, 토큰, 지연시간, 성공/실패)을 실시간 그래프로 상시 확인
- 필요시 캘린더 현황, LMS 미제출 현황 등도 같은 화면에 함께 표시하는 통합 대시보드로 확장 가능

#### 기술 스택 (SPEC.md와 별개)
- **Discord Embedded App SDK** (`@discord/embedded-app-sdk`, JavaScript)
- 프론트엔드: 웹앱(iframe) — 차트 라이브러리(Chart.js/Recharts 등)로 렌더링
- 백엔드: 별도 API 서버 필요 — Manta 봇 프로세스(Python)가 쌓은 SQLite `llm_calls` 데이터를 읽어 JSON으로 노출하는 경량 엔드포인트(예: FastAPI) 하나 추가
- OAuth2 클라이언트 등록 (Discord Developer Portal에서 Activity 활성화 필요)
- 호스팅: 웹앱을 어딘가에 배포해야 함 (로컬 M4 Pro로 상시 노출은 부적합 — 별도 소규모 호스팅 검토 필요)

#### 검토 필요 사항 (착수 전 확인)
- Activity는 음성 채널 진입이 전제인 기능이라, "그냥 텍스트 채널에서 상시 보이는 대시보드"가 목적이면 Activity보다 **채널 토픽 갱신(schedule 태스크 방식) + 명령어 조회**가 더 간단할 수 있음 — 착수 전 "정말 Activity 형태가 필요한지" 재확인
- Manta 봇 프로세스와 웹앱 백엔드가 같은 SQLite 파일에 동시 접근 시 락 경합 가능 → WAL 모드 또는 API 경유 단일 접근점 필요
- 개인 프로젝트 규모에서 OAuth2 + 웹 호스팅까지 갖추는 게 실제로 필요한 투자인지, 착수 시점에 다시 판단

#### 최소 구현 범위 (1차 목표)
1. FastAPI(또는 Flask) 경량 서버: `GET /metrics/daily` → 오늘 호출 횟수/토큰/에러율 JSON 반환
2. Activity 프론트엔드: 해당 API를 폴링해 막대/선 그래프 1~2개만 표시 (일별 호출 수, 성공/실패 비율)
3. Discord Developer Portal에서 Activity 등록 + 특정 채널에서 실행 가능하도록 설정
4. 범위 확장(캘린더/LMS 현황 등)은 1차 목표 검증 후 판단

### 10.2. 상시 TTS

#### v1과의 관계
- SPEC.md 4장 J항목에 명시된 대로, v1에는 이미 STT(Whisper)/TTS(OpenAI TTS) 코드가 존재했으나 이번 리빌드에서 명시적으로 제외됨
- 이번에 논의하는 "상시 TTS"는 v1의 "명령 시 TTS 실행"(`!tts [텍스트]`)과 다르게, **트리거 없이 지속적으로 특정 이벤트를 음성으로 알려주는 형태**로 이해하고 아래 설계

#### 목적 (가정 — 착수 시 재확인 필요)
- 음성 채널에 봇이 상주하면서, 리마인더/타이머 완료/새 알림 등 텍스트로 오던 알림을 자동으로 음성으로도 읽어줌
- 예: "30분 뒤 알려줘" 타이머가 끝나면 텍스트 메시지뿐 아니라 음성 채널에서 TTS로도 알림

#### 설계 방향
- discord.py의 `VoiceClient` 활용, 봇이 특정 음성 채널에 상시 연결 유지
- 알림 발생 시점(`tasks/reminders.py`, `tasks/timers.py` 등 SPEC.md 기존 태스크)에서 텍스트 메시지 전송과 동시에 TTS 재생 트리거를 추가하는 구조 — **기존 태스크 로직 자체는 그대로 두고, 알림 전송 지점에 음성 출력 훅만 추가**
- TTS 엔진: v1의 OpenAI TTS 재사용 여부, 또는 Claude API 생태계로 통일할지는 착수 시점에 별도 결정 (TTS는 Claude API 범위 밖이므로 provider 추상화 대상 아님)

#### 검토 필요 사항 (착수 전 확인)
- "상시"의 정확한 의미 확정 필요: (a) 봇이 음성 채널에 24시간 상주, (b) 알림 발생 시에만 음성 채널 잠깐 입장 후 재생하고 나감 — 리소스/사용자 경험 관점에서 다름
- 여러 채널에서 동시에 알림이 발생하면 음성 재생 순서/충돌 처리 필요
- 사용자가 음성 채널에 없는데 봇만 상주하는 상황 방지 (인원 0명 시 자동 퇴장 등)

### 10.3. 착수 순서 제안
1. SPEC.md Phase 0~4 완료 및 최소 1~2주 실사용 안정화
2. 10.1절(대시보드 최소 구현)부터 먼저 — TTS보다 리소스 투입이 적고 SPEC.md의 metrics 저장소(2.7절)와 바로 연결되므로
3. 대시보드 안정화 후 상시 TTS 착수, 이때 10.2절 미정 사항들을 실제 설계로 확정
