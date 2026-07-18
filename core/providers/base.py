"""
core/providers/base.py
LLMProvider 공통 인터페이스 정의 (SPEC 2.6절)

core/agent.py 와 core/graphs/*.py 는 이 인터페이스에만 의존하며,
구체 클래스(ChatAnthropic 등)를 직접 import 하지 않는다.
"""
from typing import Literal
from typing_extensions import Protocol, runtime_checkable
from langchain_core.language_models.chat_models import BaseChatModel


@runtime_checkable
class LLMProvider(Protocol):
    """
    LLM Provider 공통 인터페이스.

    role 별로 서로 다른 모델 인스턴스를 반환할 수 있어
    비용/성능 트레이드오프를 config 수준에서 관리할 수 있다.

    role 종류 (SPEC 2.6절):
      - "chat"    : 사용자와 직접 대화하는 AgentExecutor 전용 (상위 모델 권장)
      - "parse"   : LangGraph Parse 노드 — 자연어 → 구조화 명령 (저비용 모델 가능)
      - "summary" : LangGraph Summary 노드 — 결과 요약 (저비용 모델 가능)
    """

    def get_chat_model(
        self, role: Literal["parse", "summary", "chat"] = "chat"
    ) -> BaseChatModel:
        """
        지정한 role 에 맞는 LangChain BaseChatModel 인스턴스를 반환한다.

        Args:
            role: 모델 사용 용도. "chat" | "parse" | "summary"

        Returns:
            BaseChatModel: 해당 role 에 맞게 초기화된 모델 인스턴스.
        """
        ...
