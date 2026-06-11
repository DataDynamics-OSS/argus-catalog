"""임베딩 제공자 레지스트리 — 싱글턴 매니저.

활성 임베딩 제공자의 생명주기를 관리한다. catalog_configuration DB 테이블에서
설정을 로드하고 적절한 제공자 인스턴스를 생성한다. 한 번에 하나의 제공자만
활성화된다.
"""

import logging

from app.embedding.base import EmbeddingProvider

logger = logging.getLogger(__name__)

_current_provider: EmbeddingProvider | None = None


async def get_provider() -> EmbeddingProvider | None:
    """현재 임베딩 제공자를 반환하거나, 초기화되지 않았으면 None 반환."""
    return _current_provider


async def initialize_provider(config: dict[str, str]) -> EmbeddingProvider:
    """DB 설정으로부터 활성 임베딩 제공자를 생성하고 설정한다.

    새 제공자를 생성하기 전에 기존 제공자를 닫는다.
    """
    global _current_provider

    if _current_provider is not None:
        await _current_provider.close()
        _current_provider = None

    provider_type = config.get("embedding_provider", "local")
    model_id = config.get("embedding_model", "all-MiniLM-L6-v2")

    if provider_type == "local":
        from app.embedding.providers.local import LocalEmbeddingProvider
        _current_provider = LocalEmbeddingProvider(model_id=model_id)

    elif provider_type == "openai":
        from app.embedding.providers.openai import OpenAIEmbeddingProvider
        _current_provider = OpenAIEmbeddingProvider(
            api_key=config.get("embedding_api_key", ""),
            model_id=model_id,
            base_url=config.get("embedding_api_url", "https://api.openai.com/v1"),
        )

    elif provider_type == "ollama":
        from app.embedding.providers.ollama import OllamaEmbeddingProvider
        _current_provider = OllamaEmbeddingProvider(
            base_url=config.get("embedding_api_url", "http://localhost:11434"),
            model_id=model_id,
        )

    else:
        raise ValueError(f"Unknown embedding provider: {provider_type}")

    logger.info(
        "임베딩 제공자 초기화 완료: %s (model=%s)",
        provider_type, _current_provider.model_name(),
    )
    return _current_provider


async def shutdown_provider() -> None:
    """현재 제공자를 닫고 해제."""
    global _current_provider
    if _current_provider:
        await _current_provider.close()
        _current_provider = None
        logger.info("임베딩 제공자 종료됨")
