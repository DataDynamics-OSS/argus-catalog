# SPDX-License-Identifier: Apache-2.0
"""SQL 조립 공용 헬퍼."""

from __future__ import annotations

# ``ilike(pattern, escape=LIKE_ESCAPE)`` 와 짝으로 쓴다.
LIKE_ESCAPE = "\\"


def like_contains(text: str) -> str:
    """부분 일치 LIKE 패턴 — 사용자가 입력한 ``%`` ``_`` ``\\`` 는 와일드카드가 아니라 글자로 매칭.

    반드시 ``column.ilike(like_contains(q), escape=LIKE_ESCAPE)`` 처럼 escape 를 함께 지정한다.
    """
    esc = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"
