# SPDX-License-Identifier: Apache-2.0
"""OpenAI API 임베딩 제공자.

OpenAI 임베딩 API 및 호환 엔드포인트(Azure OpenAI 등)를 지원한다.
API 키가 필요하다. 기본 모델: text-embedding-3-small (1536 차원).
"""

import logging

import httpx

from app.embedding.base import EmbeddingProvider

logger = logging.getLogger(__name__)

# 알려진 모델별 차원
_DIM_MAP = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI API 기반 임베딩 제공자."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
    ):
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)
        self._dim = _DIM_MAP.get(model_id, 1536)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """OpenAI API 로 텍스트를 임베딩. 배치 처리는 내부에서 처리한다."""
        resp = await self._client.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"input": texts, "model": self._model_id},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # 순서를 유지하기 위해 index 기준으로 정렬
        return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]

    def dimension(self) -> int:
        return self._dim

    def model_name(self) -> str:
        return self._model_id

    def provider_name(self) -> str:
        return "openai"

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("OpenAI 임베딩 제공자 종료됨")
