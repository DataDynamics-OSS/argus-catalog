# SPDX-License-Identifier: Apache-2.0
"""임베딩 제공자의 추상 베이스 클래스.

모든 임베딩 제공자(local, OpenAI, Ollama)가 이 인터페이스를 구현한다.
레지스트리 모듈이 활성 제공자를 싱글턴으로 관리한다.
"""

from abc import ABC, abstractmethod

# 기본 임베딩 모델 — 한글 메타데이터(설명·용어)를 다루므로 다국어(한국어+영어) 모델을 기본으로 한다.
# 384차원이라 pgvector 컬럼(vector(384))과 그대로 맞는다. 에어갭이면 모델 경로로 바꿔 지정.
DEFAULT_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingProvider(ABC):
    """플러그형 임베딩 제공자 인터페이스."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 배치를 임베딩. float 벡터 리스트를 반환."""
        ...

    @abstractmethod
    def dimension(self) -> int:
        """이 제공자/모델의 임베딩 벡터 차원을 반환."""
        ...

    @abstractmethod
    def model_name(self) -> str:
        """모델 식별자 문자열을 반환 (예: 'all-MiniLM-L6-v2')."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """제공자 타입을 반환: 'local', 'openai', 'ollama'."""
        ...

    async def close(self) -> None:
        """리소스를 해제. 정리가 필요하면 오버라이드."""
        pass
