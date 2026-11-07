# SPDX-License-Identifier: Apache-2.0
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
        """`/chat/completions` 를 호출해 첫 번째 choice 의 메시지 내용을 반환한다.

        HTTP 오류는 ``httpx.HTTPStatusError`` 로 그대로 전파된다.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.debug(
            "OpenAI 생성 요청: model=%s prompt_len=%d max_tokens=%d",
            self._model_id, len(prompt), max_tokens,
        )
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
        logger.debug(
            "OpenAI 생성 응답: model=%s prompt_tokens=%s completion_tokens=%s",
            self._model_id, usage.get("prompt_tokens"), usage.get("completion_tokens"),
        )
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
