"""LLM 제공자 레지스트리 — 싱글턴 매니저.

활성 LLM 제공자의 생명주기를 관리한다. catalog_configuration DB 테이블에서
설정을 로드하고 적절한 제공자 인스턴스를 생성한다.
"""

import logging

from app.ai.base import LLMProvider

logger = logging.getLogger(__name__)

_current_provider: LLMProvider | None = None


async def get_provider() -> LLMProvider | None:
    """현재 LLM 제공자를 반환하거나, 초기화되지 않았으면 None 반환."""
    return _current_provider


async def initialize_provider(config: dict[str, str]) -> LLMProvider:
    """DB 설정으로부터 활성 LLM 제공자를 생성하고 설정한다.

    새 제공자를 생성하기 전에 기존 제공자를 닫는다.
    """
    global _current_provider

    if _current_provider is not None:
        await _current_provider.close()
        _current_provider = None

    provider_type = config.get("llm_provider", "openai")
    model_id = config.get("llm_model", "gpt-4o-mini")

    if provider_type == "openai":
        from app.ai.providers.openai import OpenAILLMProvider
        _current_provider = OpenAILLMProvider(
            api_key=config.get("llm_api_key", ""),
            model_id=model_id,
            base_url=config.get("llm_api_url", "") or "https://api.openai.com/v1",
        )

    elif provider_type == "ollama":
        from app.ai.providers.ollama import OllamaLLMProvider
        _current_provider = OllamaLLMProvider(
            base_url=config.get("llm_api_url", "") or "http://localhost:11434",
            model_id=model_id,
        )

    elif provider_type == "anthropic":
        from app.ai.providers.anthropic import AnthropicLLMProvider
        _current_provider = AnthropicLLMProvider(
            api_key=config.get("llm_api_key", ""),
            model_id=model_id,
            base_url=config.get("llm_api_url", "") or "https://api.anthropic.com",
        )

    else:
        raise ValueError(f"Unknown LLM provider: {provider_type}")

    logger.info(
        "LLM 제공자 초기화 완료: %s (model=%s)",
        provider_type, _current_provider.model_name(),
    )
    return _current_provider


async def shutdown_provider() -> None:
    """현재 제공자를 닫고 해제."""
    global _current_provider
    if _current_provider:
        await _current_provider.close()
        _current_provider = None
        logger.info("LLM 제공자 종료됨")
