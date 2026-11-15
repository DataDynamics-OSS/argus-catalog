"""문자열 컬럼에 담는 값 목록 (TODO-143).

예전에는 목록을 쉼표로 이어 저장해 값 안의 쉼표(``1,000`` · ``환불, 결제 문의``)가 두 값으로 쪼개졌다.
이제 JSON 배열 문자열(``["1,000","N/A"]``)로 저장하고, 읽을 때는 예전 쉼표 문자열도 그대로 받는다.

- ``decode_list``: 저장 값 → 목록. ``[`` 로 시작하고 JSON 문자열 배열로 읽히면 그대로, 아니면 예전 규칙(쉼표로 나누고
  앞뒤 공백 · 빈 항목 제거).
- ``encode_list``: 목록 → 저장 값(JSON, 한글 그대로). 빈 목록은 None.
- ``ListField``: API 스키마용 — 목록을 받거나 돌려주되, 예전 클라이언트가 보내는 쉼표 문자열도 받는다.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Annotated, Any

from pydantic import BeforeValidator


def _clean(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for v in values:
        s = str(v).strip() if v is not None else ""
        if s and s not in out:
            out.append(s)
    return out


def decode_list(value: str | list[str] | None) -> list[str]:
    """저장 값(JSON 배열 또는 예전 쉼표 문자열) → 값 목록 (앞뒤 공백 · 빈 항목 · 중복 제거)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return _clean(value)
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(x, (str, int, float)) for x in parsed):
            return _clean(parsed)
    return _clean(text.split(","))


def encode_list(values: Iterable[Any] | str | None) -> str | None:
    """값 목록 → 저장 값(JSON 배열 문자열). 비면 None. 문자열을 받으면 decode_list 규칙으로 먼저 푼다."""
    items = decode_list(values) if isinstance(values, str) or values is None else _clean(values)
    return json.dumps(items, ensure_ascii=False) if items else None


def _to_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    return decode_list(value)


ListField = Annotated[list[str] | None, BeforeValidator(_to_list)]
"""목록 항목 — 응답은 저장 문자열을 풀어 목록으로, 요청은 목록(또는 예전 쉼표 문자열)을 받는다."""
