"""
core/providers/anthropic_provider.py
AnthropicProvider — LLMProvider 기본 구현체 (SPEC 2.6절)

role 별 모델 매핑은 config.py 에서 관리한다.
  - chat    → ANTHROPIC_MODEL_CHAT    (기본: claude-sonnet-4-5)
  - parse   → ANTHROPIC_MODEL_PARSE   (기본: claude-haiku-4-5-20251001)
  - summary → ANTHROPIC_MODEL_SUMMARY (기본: claude-haiku-4-5-20251001)

temperature=0.3 이하 고정 (SPEC 1.1절 — tool-calling 신뢰도 + 언어 일관성)
"""
import logging
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel

import config

logger = logging.getLogger(__name__)

# role → (모델명, temperature) 매핑
_ROLE_CONFIG: dict[str, tuple[str, float]] = {
    "chat": (config.ANTHROPIC_MODEL_CHAT, 0.3),
    "parse": (config.ANTHROPIC_MODEL_PARSE, 0.1),    # 구조화 변환 → 더 결정론적
    "summary": (config.ANTHROPIC_MODEL_SUMMARY, 0.3),
}


class AnthropicProvider:
    """
    Anthropic Claude 기반 LLMProvider 구현체.

    agent.py, graphs/*.py 는 이 클래스를 직접 import 하지 않고
    factory 함수(get_provider)를 통해 LLMProvider 인터페이스로 사용한다.
    """

    def __init__(self) -> None:
        if not config.ANTHROPIC_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY 가 .env 에 설정되어 있지 않습니다. "
                "console.anthropic.com 에서 키를 발급한 뒤 .env 에 추가하세요."
            )
        logger.info(
            "[AnthropicProvider] 초기화 완료. "
            "chat=%s / parse=%s / summary=%s",
            config.ANTHROPIC_MODEL_CHAT,
            config.ANTHROPIC_MODEL_PARSE,
            config.ANTHROPIC_MODEL_SUMMARY,
        )

    def get_chat_model(
        self, role: Literal["parse", "summary", "chat"] = "chat"
    ) -> BaseChatModel:
        """
        role 에 맞는 ChatAnthropic 인스턴스를 반환한다.

        매번 새 인스턴스를 생성한다 — ChatAnthropic 은 상태가 없으므로
        재사용해도 무방하지만, 설정 변경 적용을 단순하게 유지하기 위해
        호출 시점에 생성한다. 성능 이슈 발생 시 캐싱으로 전환.
        """
        if role not in _ROLE_CONFIG:
            logger.warning(
                "[AnthropicProvider] 알 수 없는 role '%s' → 'chat' 로 폴백.", role
            )
            role = "chat"

        model_name, temperature = _ROLE_CONFIG[role]
        logger.debug(
            "[AnthropicProvider] role=%s → model=%s, temperature=%.1f",
            role, model_name, temperature,
        )

        return ChatAnthropic(
            model=model_name,
            temperature=temperature,
            api_key=config.ANTHROPIC_API_KEY,
            max_tokens=4096,
        )


def get_provider() -> AnthropicProvider:
    """
    현재 config.LLM_PROVIDER 에 맞는 Provider 인스턴스를 반환하는 팩토리 함수.

    향후 다른 provider 추가 시 이 함수만 수정하면 되며,
    agent.py / graphs/*.py 는 수정 불필요 (SPEC 2.6절 원칙).
    """
    provider_name = getattr(config, "LLM_PROVIDER", "anthropic").lower()

    if provider_name == "anthropic":
        return AnthropicProvider()

    # 미래 확장 시 여기에 추가
    # elif provider_name == "openai":
    #     return OpenAIProvider()

    raise ValueError(
        f"지원하지 않는 LLM 프로바이더: '{provider_name}'. "
        f"config.LLM_PROVIDER 를 확인하세요."
    )
