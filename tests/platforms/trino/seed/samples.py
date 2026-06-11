# SPDX-License-Identifier: Apache-2.0
"""Trino 테이블에서 sample row 를 뽑아 CSV 로 직렬화하는 헬퍼.

테스트 시 seed 가 INSERT 까지 수행하진 않지만, 외부 S3 location 에 어떤 파일이
들어있다면 그대로 읽힌다. 데이터가 없으면 header 만 있는 CSV 가 반환되며 catalog
서버는 빈 sample 도 정상적으로 받아 저장한다.
"""

from __future__ import annotations

import csv
import io


MAX_ROWS = 50
MAX_BYTES = 100 * 1024  # catalog 의 sample upload 한도와 일치.


def fetch_sample_csv(conn, qualified: str) -> bytes:
    """``qualified`` (예: ``hive.sales.orders``) 에서 sample 행을 CSV bytes 로 반환."""
    target_rows = MAX_ROWS
    while target_rows > 0:
        body = _fetch_csv(conn, qualified, target_rows)
        if len(body) <= MAX_BYTES:
            return body
        target_rows = max(target_rows // 2, target_rows - 1)
    return _fetch_csv(conn, qualified, 1)[:MAX_BYTES]


def _fetch_csv(conn, qualified: str, n: int) -> bytes:
    # Trino DB-API 는 named binding 이 없으므로 정수를 직접 포맷 (내부 상수라 안전).
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {qualified} LIMIT {int(n)}")
    rows = cur.fetchall()
    columns = [d[0] for d in (cur.description or [])]
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return buf.getvalue().encode("utf-8")
