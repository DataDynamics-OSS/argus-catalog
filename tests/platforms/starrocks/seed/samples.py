# SPDX-License-Identifier: Apache-2.0
"""StarRocks 테이블에서 sample row 를 뽑아 CSV 로 직렬화하는 헬퍼.

StarRocks 는 MySQL wire protocol 을 쓰지만 ``LIMIT`` 에 named parameter binding 이
호환되지 않을 수 있으므로 정수를 직접 포맷한다 (값은 내부 상수라 안전).
"""

from __future__ import annotations

import csv
import io

from sqlalchemy import text
from sqlalchemy.engine import Engine


MAX_ROWS = 50
MAX_BYTES = 100 * 1024  # catalog 의 sample upload 한도와 일치.


def fetch_sample_csv(engine: Engine, qualified: str) -> bytes:
    """``qualified`` (예: ``argus_test.orders``) 에서 sample 행을 CSV bytes 로 반환."""
    target_rows = MAX_ROWS
    while target_rows > 0:
        body = _fetch_csv(engine, qualified, target_rows)
        if len(body) <= MAX_BYTES:
            return body
        target_rows = max(target_rows // 2, target_rows - 1)
    return _fetch_csv(engine, qualified, 1)[:MAX_BYTES]


def _fetch_csv(engine: Engine, qualified: str, n: int) -> bytes:
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {qualified} LIMIT {int(n)}"))
        columns = list(result.keys())
        rows = result.fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return buf.getvalue().encode("utf-8")
