# SPDX-License-Identifier: Apache-2.0
"""PostgreSQL 테이블에서 sample row 를 뽑아 CSV 로 직렬화하는 헬퍼.

각 dataset 의 sample 은 catalog 의 ``POST /datasets/{id}/sample`` 로 업로드된다
(100 KB 제한이라 row 수는 ``MAX_ROWS`` 로 캡). 비ASCII 값은 UTF-8 그대로 두고
CSV quoting 으로 줄바꿈·콤마를 안전하게 인용한다.
"""

from __future__ import annotations

import csv
import io

from sqlalchemy import text
from sqlalchemy.engine import Engine


MAX_ROWS = 50
MAX_BYTES = 100 * 1024  # catalog 의 sample upload 한도와 일치.


def fetch_sample_csv(engine: Engine, qualified: str) -> bytes:
    """``qualified`` (예: ``sales.orders``) 에서 sample 행을 CSV bytes 로 반환.

    한 행이 크거나 BLOB 컬럼이 있어 ``MAX_BYTES`` 를 넘으면 row 수를 절반씩 줄여
    재시도. 끝까지 못 맞추면 행 수 1까지 시도하고, 그래도 초과면 그 1행을 잘라낸다.
    """
    target_rows = MAX_ROWS
    while target_rows > 0:
        body = _fetch_csv(engine, qualified, target_rows)
        if len(body) <= MAX_BYTES:
            return body
        target_rows = max(target_rows // 2, target_rows - 1)
    # 마지막 안전망 — 1 row 도 한도를 넘으면 잘라서 반환.
    return _fetch_csv(engine, qualified, 1)[:MAX_BYTES]


def _fetch_csv(engine: Engine, qualified: str, n: int) -> bytes:
    # PostgreSQL 은 ``schema.table`` 식별자에 quoting 이 필요 없다 (seed 가 lowercase 만 사용).
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {qualified} LIMIT :n"), {"n": n})
        columns = list(result.keys())
        rows = result.fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return buf.getvalue().encode("utf-8")
