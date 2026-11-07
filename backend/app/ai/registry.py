# SPDX-License-Identifier: Apache-2.0
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


def create_provider(config: dict[str, str]) -> LLMProvider:
    """설정 dict 로 LLM 제공자 인스턴스를 새로 만든다 — 전역 제공자는 건드리지 않는다.

    연결 테스트처럼 잠깐 쓰고 버릴 인스턴스가 필요할 때 쓰며, 다 쓴 뒤 호출한 쪽이 ``close()`` 한다.

    Args:
        config: llm_provider / llm_model / llm_api_key / llm_api_url 키를 가진 설정 dict.

    Raises:
        ValueError: llm_provider 가 openai/ollama/anthropic 이 아닌 경우.
    """
    provider_type = config.get("llm_provider", "openai")
    model_id = config.get("llm_model", "gpt-4o-mini")

    if provider_type == "openai":
        from app.ai.providers.openai import OpenAILLMProvider
        return OpenAILLMProvider(
            api_key=config.get("llm_api_key", ""),
            model_id=model_id,
            base_url=config.get("llm_api_url", "") or "https://api.openai.com/v1",
        )

    if provider_type == "ollama":
        from app.ai.providers.ollama import OllamaLLMProvider
        return OllamaLLMProvider(
            base_url=config.get("llm_api_url", "") or "http://localhost:11434",
            model_id=model_id,
        )

    if provider_type == "anthropic":
        from app.ai.providers.anthropic import AnthropicLLMProvider
        return AnthropicLLMProvider(
            api_key=config.get("llm_api_key", ""),
            model_id=model_id,
            base_url=config.get("llm_api_url", "") or "https://api.anthropic.com",
        )

    logger.warning("알 수 없는 LLM 제공자 설정: provider=%s", provider_type)
    raise ValueError(f"Unknown LLM provider: {provider_type}")


async def initialize_provider(config: dict[str, str]) -> LLMProvider:
    """DB 설정으로부터 활성 LLM 제공자를 생성하고 설정한다.

    새 제공자를 생성하기 전에 기존 제공자를 닫는다.

    Args:
        config: llm_provider / llm_model / llm_api_key / llm_api_url 키를 가진 설정 dict.

    Raises:
        ValueError: llm_provider 가 openai/ollama/anthropic 이 아닌 경우.
            (이때 기존 제공자는 이미 닫혀 None 상태가 된다.)
    """
    global _current_provider

    if _current_provider is not None:
        await _current_provider.close()
        _current_provider = None

    _current_provider = create_provider(config)
    logger.info(
        "LLM 제공자 초기화 완료: %s (model=%s)",
        config.get("llm_provider", "openai"), _current_provider.model_name(),
    )
    return _current_provider


async def shutdown_provider() -> None:
    """현재 제공자를 닫고 해제."""
    global _current_provider
    if _current_provider:
        await _current_provider.close()
        _current_provider = None
        logger.info("LLM 제공자 종료됨")
