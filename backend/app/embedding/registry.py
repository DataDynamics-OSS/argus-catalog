# SPDX-License-Identifier: Apache-2.0
"""임베딩 제공자 레지스트리 — 싱글턴 매니저.

활성 임베딩 제공자의 생명주기를 관리한다. catalog_configuration DB 테이블에서
설정을 로드하고 적절한 제공자 인스턴스를 생성한다. 한 번에 하나의 제공자만
활성화된다.
"""

import logging

from app.embedding.base import DEFAULT_EMBEDDING_MODEL, EmbeddingProvider

logger = logging.getLogger(__name__)

_current_provider: EmbeddingProvider | None = None

# 설정 화면이 제공하는 모델의 알려진 출력 차원 — 저장 시 pgvector 컬럼 차원과 대조한다.
# 목록에 없는 모델은 요청의 dimension 값으로 판단한다.
KNOWN_MODEL_DIMENSIONS: dict[tuple[str, str], int] = {
    ("local", "all-MiniLM-L6-v2"): 384,
    ("local", "paraphrase-multilingual-MiniLM-L12-v2"): 384,
    ("local", "bge-small-en-v1.5"): 384,
    ("openai", "text-embedding-3-small"): 1536,
    ("openai", "text-embedding-3-large"): 3072,
    ("openai", "text-embedding-ada-002"): 1536,
    ("ollama", "all-minilm"): 384,
    ("ollama", "nomic-embed-text"): 768,
    ("ollama", "mxbai-embed-large"): 1024,
}


async def get_provider() -> EmbeddingProvider | None:
    """현재 임베딩 제공자를 반환하거나, 초기화되지 않았으면 None 반환."""
    return _current_provider


def create_provider(config: dict[str, str]) -> EmbeddingProvider:
    """설정 dict 로 제공자 인스턴스만 만든다 — 전역 제공자는 건드리지 않는다.

    설정 화면의 연결 테스트처럼 임시 인스턴스가 필요할 때 쓴다(호출 측이 close 책임).
    """
    provider_type = config.get("embedding_provider", "local")
    model_id = config.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
    # embedding_api_key 는 값이 아니라 존재 여부만 남긴다
    logger.debug("임베딩 제공자 인스턴스 생성: provider=%s model=%s api_key_set=%s",
                 provider_type, model_id, bool(config.get("embedding_api_key")))

    if provider_type == "local":
        from app.embedding.providers.local import LocalEmbeddingProvider
        return LocalEmbeddingProvider(model_id=model_id)

    if provider_type == "openai":
        from app.embedding.providers.openai import OpenAIEmbeddingProvider
        return OpenAIEmbeddingProvider(
            api_key=config.get("embedding_api_key", ""),
            model_id=model_id,
            base_url=config.get("embedding_api_url") or "https://api.openai.com/v1",
        )

    if provider_type == "ollama":
        from app.embedding.providers.ollama import OllamaEmbeddingProvider
        return OllamaEmbeddingProvider(
            base_url=config.get("embedding_api_url") or "http://localhost:11434",
            model_id=model_id,
        )

    logger.warning("알 수 없는 임베딩 제공자 설정: provider=%s", provider_type)
    raise ValueError(f"Unknown embedding provider: {provider_type}")


async def initialize_provider(config: dict[str, str]) -> EmbeddingProvider:
    """DB 설정으로부터 활성 임베딩 제공자를 생성하고 설정한다.

    새 제공자를 생성하기 전에 기존 제공자를 닫는다. 알 수 없는 제공자면 ``create_provider`` 의
    ``ValueError`` 가 그대로 올라가며, 이때 전역 제공자는 None 으로 남는다.
    """
    global _current_provider

    if _current_provider is not None:
        logger.info("기존 임베딩 제공자 교체: %s", _current_provider.provider_name())
        await _current_provider.close()
        _current_provider = None

    _current_provider = create_provider(config)

    logger.info(
        "임베딩 제공자 초기화 완료: %s (model=%s)",
        config.get("embedding_provider", "local"), _current_provider.model_name(),
    )
    return _current_provider


async def shutdown_provider() -> None:
    """현재 제공자를 닫고 해제."""
    global _current_provider
    if _current_provider:
        await _current_provider.close()
        _current_provider = None
        logger.info("임베딩 제공자 종료됨")
