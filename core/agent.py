"""
core/agent.py
MantaAgent — AgentExecutor 기반 단순 요청 처리 (SPEC 2장, Phase 0)

핵심 설계 원칙:
  - LLM 구체 클래스(ChatAnthropic 등)를 직접 import 하지 않는다 (SPEC 2.6절).
    대신 core.providers.get_provider() 를 통해 LLMProvider 인터페이스로만 접근.
  - 시스템 프롬프트 맨 첫 줄에 [CRITICAL LANGUAGE CONSTRAINT] 삽입 (SPEC 1.1절).
  - LLM 호출 성공/실패 모두 infrastructure.metrics 에 기록 (SPEC 2.7절).
  - LLM 응답 생성 실패 시 services.fallback_formatter 로 규칙 기반 응답 반환 (SPEC 2.5절).
"""
from __future__ import annotations

import logging
import time
import warnings
from langchain_core._api.deprecation import LangChainDeprecationWarning
warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)

from typing import Any

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory

from infrastructure.database import get_session_history
from infrastructure.metrics import record_llm_call_async
from services.fallback_formatter import format_tool_results

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 시스템 프롬프트 (SPEC 1.1절 — 언어 강제 프롬프트 최상단 필수)
# ---------------------------------------------------------------------------
_BASE_SYSTEM_PROMPT = """\
[CRITICAL LANGUAGE CONSTRAINT]
너는 Manta, 한국어 전용 비서야. 모든 응답은 무조건 한국어로만 해.
중국어, 일본어, 영어 등 다른 언어는 절대 사용 금지. 사용자가 외국어를 써도 한국어로 답변해야 해.
도구 호출 인자도 모두 한국어로 작성해.

[역할]
너는 사용자를 돕는 비서 Manta야. 친절하고 정중하며 자연스럽게 답변해 줘.
도구를 적절히 사용하여 사용자의 명령을 수행해 줘.

[캘린더 조회 제한]
⚠️ 캘린더 조회 시 절대 전체 캘린더를 요청하지 마라.
반드시 today / tomorrow / week / date range / keyword 중 하나를 사용해 구체적으로 조회하라.
전체 조회는 관리자 전용이므로 금지된다.

[날짜/기간 처리]
"이번 달", "지금까지", "다음 주" 같은 상대 표현은 그대로 도구에 전달하라.
날짜를 직접 추론하거나 연도를 임의로 추정하지 마라. Python 서비스 레이어가 처리한다.
"""


def _build_system_prompt(facts: dict[str, str]) -> str:
    """
    베이스 시스템 프롬프트에 사용자 장기 메모리를 주입한 프롬프트를 반환한다.

    저장된 사실이 없으면 [사용자 정보] 섹션을 생략해 욬필요한 토큰 낙비를 마는다.

    Args:
        facts: MemoryService.get_facts()가 반환한 {key: value} dict.

    Returns:
        완성된 시스템 프롬프트 문자열.
    """
    if not facts:
        return _BASE_SYSTEM_PROMPT

    facts_lines = "\n".join(f"  - {k}: {v}" for k, v in facts.items())
    memory_section = f"""
[사용자 정보 — 장기 메모리]
이 정보는 이전 대화에서 학습한 사실이다. 대화 컨텍스트로 활용하되, 비출하지 마라.
{facts_lines}
"""
    return _BASE_SYSTEM_PROMPT + memory_section


class MantaAgent:
    """
    Manta2 챗봇의 핵심 에이전트 클래스 (단순 요청 경로).

    Discord 또는 CLI 로부터 독립되어 순수 텍스트 입출력을 처리하며,
    세션(channel_id) 별로 대화 히스토리를 관리한다.

    복잡한 다단계 요청은 core/router.py → core/graphs/*.py 경로로 처리되며,
    이 클래스는 단순 조회/단건 작업만 담당한다 (SPEC 2.1절).
    """

    def __init__(self) -> None:
        # 1. LLMProvider 인터페이스를 통해 모델 획득 (구체 클래스 직접 import 금지)
        from core.providers.anthropic_provider import get_provider
        provider = get_provider()
        self._llm = provider.get_chat_model(role="chat")
        self._model_name = self._llm.model  # metrics 기록용

        # 2. 도구 바인딩
        self._tools = _load_tools()

        logger.info("[MantaAgent] 초기화 완료. 모델: %s", self._model_name)

    def _build_runnable(self, system_prompt: str):
        """
        주어진 시스템 프롬프트로 AgentExecutor + 히스토리 랩퍼를 조립한다.

        chat() 호출 마다 메모리 주입으로 프롬프트가 달라지므로
        AgentExecutor를 정적으로 저장하지 않고 동적으로 생성한다.
        """
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        agent = create_tool_calling_agent(self._llm, self._tools, prompt)
        executor = AgentExecutor(
            agent=agent,
            tools=self._tools,
            verbose=False,
            handle_parsing_errors=True,
        )
        return RunnableWithMessageHistory(
            executor,
            get_session_history,
            input_messages_key="input",
            history_messages_key="history",
        )

    async def chat(self, session_id: str, message: str) -> str:
        """
        주어진 세션 ID 의 대화 히스토리를 유지하며 에이전트와 대화한다.

        장기 메모리 주입:
          MemoryService.get_facts()로 저장된 사실을 조회한 후
          시스템 프롬프트에 [사용자 정보] 섹션으로 주입한다.
          저장된 사실이 없으면 인자 프롬프트를 사용하여 토큰 낙비를 막는다.

        LLM 호출 성공/실패 여부를 metrics 에 기록한다 (SPEC 2.7절).
        실패 시 fallback_formatter 로 규칙 기반 응답을 반환한다 (SPEC 2.5절).

        Args:
            session_id: Discord 채널 ID (히스토리 격리 키).
            message:    사용자 입력 텍스트 (멘션 제거 후).

        Returns:
            에이전트 응답 텍스트. 실패 시 규칙 기반 fallback 텍스트.
        """
        # 장기 메모리 조회 후 동적 시스템 프롬프트 구성
        try:
            from services.memory_service import MemoryService
            facts = MemoryService().get_facts(session_id)
        except Exception as mem_err:
            logger.warning("[MantaAgent] 메모리 조회 실패 (무시 후 계속): %s", mem_err)
            facts = {}

        system_prompt = _build_system_prompt(facts)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=LangChainDeprecationWarning)
            agent_with_history = self._build_runnable(system_prompt)

        config_dict = {"configurable": {"session_id": session_id}}
        start_ms = int(time.monotonic() * 1000)

        try:
            response = await agent_with_history.ainvoke(
                {"input": message},
                config=config_dict,
            )
            latency_ms = int(time.monotonic() * 1000) - start_ms

            output = response.get("output", "응답을 생성하지 못했습니다.")

            # Claude 가 list 형태 complex output 을 반환하는 경우 텍스트 추출
            if isinstance(output, list):
                text_parts = [
                    item.get("text", "")
                    for item in output
                    if isinstance(item, dict) and "text" in item
                ]
                output = "".join(text_parts) if text_parts else str(output)

            # 메트릭 기록 (SPEC 2.7절)
            await record_llm_call_async(
                role="chat",
                model=self._model_name,
                channel_id=session_id,
                latency_ms=latency_ms,
                status="success",
            )

            return output

        except Exception as e:
            latency_ms = int(time.monotonic() * 1000) - start_ms
            error_type = _classify_error(e)

            logger.error(
                "[MantaAgent] 세션 %s 에서 오류 발생 (error_type=%s): %s",
                session_id, error_type, e,
                exc_info=True,
            )

            # 메트릭 기록 (실패)
            await record_llm_call_async(
                role="chat",
                model=self._model_name,
                channel_id=session_id,
                latency_ms=latency_ms,
                status="error",
                error_type=error_type,
            )

            # Fallback: 빈 응답/에러 메시지만 보내지 않는다 (SPEC 2.5절)
            return (
                "⚠️ 응답 생성에 실패했습니다.\n"
                f"오류 유형: {error_type}\n\n"
                "잠시 후 다시 시도해 주세요."
            )


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _load_tools() -> list:
    """
    tools/ 디렉토리에서 도구를 로드한다.
    Phase 0 에서는 calendar_tools 만 포함.
    Phase 2 에서 나머지 도구를 추가한다.
    """
    try:
        from tools.calendar_tools import (
            get_today_events,
            get_tomorrow_events,
            get_week_events,
            get_events_by_range,
            search_events,
            get_event_detail,
            add_apple_calendar_event,
            modify_apple_calendar_event,
            delete_apple_calendar_event,
            delete_all_calendar_events_on_date,
        )
        return [
            get_today_events,
            get_tomorrow_events,
            get_week_events,
            get_events_by_range,
            search_events,
            get_event_detail,
            add_apple_calendar_event,
            modify_apple_calendar_event,
            delete_apple_calendar_event,
            delete_all_calendar_events_on_date,
        ]
    except ImportError as e:
        logger.warning("[MantaAgent] calendar_tools 로드 실패: %s. 도구 없이 기동.", e)
        return []


def _classify_error(exc: Exception) -> str:
    """예외를 분류해 error_type 문자열로 반환 (metrics 기록용)."""
    exc_str = str(exc).lower()
    if "429" in exc_str or "rate_limit" in exc_str or "quota" in exc_str:
        return "429"
    if "timeout" in exc_str or "timed out" in exc_str:
        return "timeout"
    return type(exc).__name__
