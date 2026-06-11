# SPDX-License-Identifier: Apache-2.0
"""Ollama 임베딩 제공자.

로컬에서 실행 중인 Ollama 인스턴스를 임베딩에 사용한다.
기본 엔드포인트: http://localhost:11434
기본 모델: all-minilm (384 차원)
"""

import logging

import httpx

from app.embedding.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Ollama API 기반 임베딩 제공자."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model_id: str = "all-minilm",
    ):
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._client = httpx.AsyncClient(timeout=60.0)
        self._dim: int | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Ollama API 로 텍스트를 임베딩 (텍스트당 요청 1회)."""
        results = []
        for text in texts:
            resp = await self._client.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model_id, "prompt": text},
            )
            resp.raise_for_status()
            vec = resp.json()["embedding"]
            results.append(vec)
            if self._dim is None:
                self._dim = len(vec)
                logger.info("Ollama 모델 %s 차원 감지: %d", self._model_id, self._dim)
        return results

    def dimension(self) -> int:
        return self._dim or 384

    def model_name(self) -> str:
        return self._model_id

    def provider_name(self) -> str:
        return "ollama"

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("Ollama 임베딩 제공자 종료됨")
