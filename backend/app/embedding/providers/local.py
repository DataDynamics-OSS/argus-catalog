# SPDX-License-Identifier: Apache-2.0
"""sentence-transformers 를 사용하는 로컬 임베딩 제공자.

sentence-transformers 모델을 로컬에 로드하고, async 이벤트 루프를 막지 않도록
스레드 풀에서 추론을 실행한다.

기본 모델: all-MiniLM-L6-v2 (384 차원, 약 80MB)
한국어+영어: paraphrase-multilingual-MiniLM-L12-v2 (384 차원, 약 470MB)
"""

import asyncio
import logging

from app.embedding.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class LocalEmbeddingProvider(EmbeddingProvider):
    """sentence-transformers 기반 로컬 임베딩 제공자."""

    def __init__(self, model_id: str = "all-MiniLM-L6-v2"):
        self._model_id = model_id
        self._model = None
        self._dim: int | None = None

    def _get_model(self):
        """sentence-transformers 모델을 지연 로드(lazy-load)."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers is required for local embedding. "
                    "Install with: pip install sentence-transformers"
                )
            logger.info("sentence-transformers 모델 로딩 중: %s", self._model_id)
            self._model = SentenceTransformer(self._model_id)
            self._dim = self._model.get_sentence_embedding_dimension()
            logger.info("모델 로드 완료: %s (dim=%d)", self._model_id, self._dim)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """sentence-transformers 로 텍스트를 임베딩 (스레드 풀에서 실행)."""
        model = self._get_model()
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: model.encode(texts, normalize_embeddings=True).tolist(),
        )
        return embeddings

    def dimension(self) -> int:
        """임베딩 차원을 반환 (필요 시 모델을 로드)."""
        if self._dim is None:
            self._get_model()
        return self._dim

    def model_name(self) -> str:
        return self._model_id

    def provider_name(self) -> str:
        return "local"

    async def close(self) -> None:
        self._model = None
        self._dim = None
        logger.info("로컬 임베딩 제공자 종료됨")
