"""Ollama LLM 제공자.

로컬에서 실행 중인 Ollama 인스턴스를 텍스트 생성에 사용한다.
기본 엔드포인트: http://localhost:11434
"""

import logging

import httpx

from app.ai.base import LLMProvider

logger = logging.getLogger(__name__)


class OllamaLLMProvider(LLMProvider):
    """Ollama API 기반 LLM 제공자."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model_id: str = "llama3.1",
    ):
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._client = httpx.AsyncClient(timeout=180.0)

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> dict:
        payload: dict = {
            "model": self._model_id,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system_prompt:
            payload["system"] = system_prompt

        resp = await self._client.post(
            f"{self._base_url}/api/generate",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "text": data.get("response", ""),
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
        }

    def model_name(self) -> str:
        return self._model_id

    def provider_name(self) -> str:
        return "ollama"

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("Ollama LLM 제공자 종료됨")
