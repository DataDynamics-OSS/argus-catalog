"""LLM 클라이언트 — OpenAI 호환 chat/completions 하나로 통일.

ollama(기본)·vLLM·OpenAI·LiteLLM 이 모두 같은 엔드포인트를 제공하므로
provider 분기 없이 base_url + model 만 바꾸면 어떤 백엔드든 사용할 수 있다.
표준 라이브러리만 사용한다 (외부 의존성 0).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterator

logger = logging.getLogger(__name__)


class LLMClient:
    """OpenAI 호환 /chat/completions 최소 클라이언트."""

    def __init__(self, base_url: str, model: str, api_key: str = "ollama",
                 temperature: float = 0.3, max_tokens: int = 2048,
                 timeout: int = 300) -> None:
        self.base = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout  # 로컬 7B 모델은 첫 로드가 느릴 수 있어 넉넉히

    def generate(self, prompt: str, system_prompt: str | None = None) -> dict:
        """프롬프트를 보내고 {"text", "prompt_tokens", "completion_tokens"} 를 반환."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        req = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=json.dumps({
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": False,
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"LLM 호출 실패 {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"LLM 서버에 연결할 수 없습니다 ({self.base}): {e.reason} — "
                "ollama 가 실행 중인지, --llm-url 이 올바른지 확인하세요."
            ) from e

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        return {
            "text": text.strip(),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        }

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """멀티턴 + tool-calling 대화 호출.

        OpenAI 호환 형식 그대로 — qwen2.5(ollama)·vLLM·OpenAI 모두 동일하게
        동작한다. 반환: {"message": <assistant message dict>, "usage": {...}}
        message.tool_calls 가 있으면 호출자가 도구를 실행하고
        role=tool 메시지를 붙여 다시 호출한다.
        """
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        req = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"LLM 호출 실패 {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"LLM 서버에 연결할 수 없습니다 ({self.base}): {e.reason}"
            ) from e
        return {
            "message": data["choices"][0]["message"],
            "usage": data.get("usage") or {},
        }

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[dict]:
        """스트리밍 tool-calling 호출 — 답변 토큰을 실시간으로 흘린다.

        OpenAI 호환 SSE(`data: {choices:[{delta:{...}}]}`)를 그대로 파싱하므로
        ollama/vLLM/OpenAI 모두 동일하게 동작한다. yield 하는 이벤트:
          {"type": "content", "text": <조각>}  — 답변 토큰 조각 (실시간 표시용)
          {"type": "final", "message": <조립된 assistant 메시지>, "usage": {...}}  — 마지막 1회

        ``message`` 는 비스트리밍 :meth:`chat` 의 반환과 동형(content +
        tool_calls)이라 호출부(assistant.py)의 도구 실행 로직을 그대로 쓴다.
        tool 호출 라운드는 보통 content 가 비어 있어, content 조각을 즉시
        흘려보내도 최종 답변만 표시된다.
        """
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
            # 일부 백엔드(OpenAI)는 이 옵션이 있어야 마지막 청크에 usage 를 싣는다
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools

        req = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"LLM 호출 실패 {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"LLM 서버에 연결할 수 없습니다 ({self.base}): {e.reason}"
            ) from e

        content_parts: list[str] = []
        # index → 부분 조립된 tool_call (인자 문자열은 조각으로 나뉘어 온다)
        tool_calls: dict[int, dict] = {}
        usage: dict = {}
        with resp:
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue  # keep-alive 등 비정상 라인 무시
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                    yield {"type": "content", "text": delta["content"]}
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_calls.setdefault(idx, {"id": None, "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]

        # 스트림 종료 — 비스트리밍 chat() 과 동형의 메시지로 조립
        message: dict = {"role": "assistant", "content": "".join(content_parts) or None}
        if tool_calls:
            message["tool_calls"] = [
                {"id": slot["id"] or f"call_{idx}", "type": "function",
                 "function": {"name": slot["name"], "arguments": slot["args"] or "{}"}}
                for idx, slot in sorted(tool_calls.items())
            ]
        yield {"type": "final", "message": message, "usage": usage}

    def generate_json(self, prompt: str, system_prompt: str | None = None):
        """JSON 응답을 기대하는 호출 — 코드펜스 제거 후 파싱, 실패 시 None.

        소형 모델이 ```json ... ``` 으로 감싸는 경우가 흔해 방어적으로 벗긴다.
        """
        result = self.generate(prompt, system_prompt)
        text = result["text"]
        if text.startswith("```"):
            # 첫 줄(```json)과 마지막 펜스 제거
            lines = text.split("\n")
            if lines[-1].strip().startswith("```"):
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines)
        try:
            return json.loads(text), result
        except json.JSONDecodeError:
            logger.warning("LLM JSON 응답 파싱 실패 — 원문 일부: %s", text[:200])
            return None, result
