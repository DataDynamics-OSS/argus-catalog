"""OpenAI API LLM 제공자.

OpenAI Chat Completions API 및 호환 엔드포인트
(Azure OpenAI, vLLM, LiteLLM 등)를 지원한다.
"""

import logging

import httpx

from app.ai.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    """OpenAI API 기반 LLM 제공자."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
    ):
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> dict:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        resp = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        usage = data.get("usage", {})
        return {
            "text": data["choices"][0]["message"]["content"],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        }

    def model_name(self) -> str:
        return self._model_id

    def provider_name(self) -> str:
        return "openai"

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("OpenAI LLM 제공자 종료됨")
