# SPDX-License-Identifier: Apache-2.0
"""Anthropic (Claude) LLM 제공자.

텍스트 생성에 Anthropic Messages API 를 사용한다.
"""

import logging

import httpx

from app.ai.base import LLMProvider

logger = logging.getLogger(__name__)


class AnthropicLLMProvider(LLMProvider):
    """Anthropic Messages API 기반 LLM 제공자."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "claude-sonnet-4-20250514",
        base_url: str = "https://api.anthropic.com",
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
        """Messages API(`/v1/messages`)를 호출해 텍스트와 토큰 사용량을 반환한다.

        응답의 content 블록 중 type=text 인 것만 이어 붙인다.
        HTTP 오류는 ``httpx.HTTPStatusError`` 로 그대로 전파된다.
        """
        payload: dict = {
            "model": self._model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

        logger.debug(
            "Anthropic 생성 요청: model=%s prompt_len=%d max_tokens=%d",
            self._model_id, len(prompt), max_tokens,
        )
        resp = await self._client.post(
            f"{self._base_url}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        usage = data.get("usage", {})
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        logger.debug(
            "Anthropic 생성 응답: model=%s input_tokens=%s output_tokens=%s text_len=%d",
            self._model_id, usage.get("input_tokens"), usage.get("output_tokens"), len(text),
        )
        return {
            "text": text,
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
        }

    def model_name(self) -> str:
        return self._model_id

    def provider_name(self) -> str:
        return "anthropic"

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("Anthropic LLM 제공자 종료됨")
