# SPDX-License-Identifier: Apache-2.0
"""Kudu 테이블에서 sample row 를 뽑아 CSV 로 직렬화하는 헬퍼.

kudu-python 은 SQL 이 없으므로 scanner 로 직접 스캔해 row 를 dict 로 받는다.
seed 가 INSERT 하지 않는 경우엔 header 만 있는 CSV 가 반환되며 catalog 서버는
빈 sample 도 정상적으로 받아 저장한다.
"""

from __future__ import annotations

import csv
import io


MAX_ROWS = 50
MAX_BYTES = 100 * 1024  # catalog 의 sample upload 한도와 일치.


def fetch_sample_csv(client, table_name: str) -> bytes:
    """``table_name`` 의 sample 행을 CSV bytes 로 반환.

    ``table_name`` 은 kudu raw 이름 (``impala::default.events`` 같은 prefix 포함).
    catalog 가 보여주는 dataset name 과 다를 수 있어 호출 측에서 매핑을 한다.
    """
    target_rows = MAX_ROWS
    while target_rows > 0:
        body = _fetch_csv(client, table_name, target_rows)
        if len(body) <= MAX_BYTES:
            return body
        target_rows = max(target_rows // 2, target_rows - 1)
    return _fetch_csv(client, table_name, 1)[:MAX_BYTES]


def _fetch_csv(client, table_name: str, n: int) -> bytes:
    table = client.table(table_name)
    columns = [c.name for c in table.schema]

    scanner = table.scanner()
    scanner.open()
    rows: list[tuple] = []
    try:
        # read_all_tuples 는 컬럼 순서대로 tuple 로 반환.
        all_rows = scanner.read_all_tuples()
        rows = list(all_rows)[: int(n)]
    finally:
        try:
            scanner.close()
        except Exception:
            pass

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return buf.getvalue().encode("utf-8")
