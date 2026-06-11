"""Iceberg REST Catalog 테이블에서 sample row 를 뽑아 CSV 로 직렬화하는 헬퍼.

pyiceberg 의 ``Table.scan().to_arrow()`` 로 Arrow Table 을 얻은 뒤 head(n) 으로
잘라 CSV 로 변환. 100 KB 한도를 넘으면 sakila 와 동일하게 n 을 절반씩 줄여 재시도.
"""

from __future__ import annotations

import csv
import io


MAX_ROWS = 50
MAX_BYTES = 100 * 1024  # catalog 의 sample upload 한도와 일치.


def fetch_sample_csv(catalog, identifier) -> bytes:
    """``identifier`` (예: ``("analytics", "orders")`` 또는 ``"analytics.orders"``).

    pyiceberg 의 RestCatalog 는 두 형식 모두 받아준다. 빈 테이블이면 header 만 있는
    CSV 가 반환된다.
    """
    table = catalog.load_table(identifier)
    arrow_table = table.scan().to_arrow()

    target_rows = MAX_ROWS
    while target_rows > 0:
        body = _arrow_head_to_csv(arrow_table, target_rows)
        if len(body) <= MAX_BYTES:
            return body
        target_rows = max(target_rows // 2, target_rows - 1)
    return _arrow_head_to_csv(arrow_table, 1)[:MAX_BYTES]


def _arrow_head_to_csv(arrow_table, n: int) -> bytes:
    # pyarrow.Table.slice 로 앞 n 행만 추출 (table.num_rows 가 0 이면 빈 결과).
    head = arrow_table.slice(0, min(int(n), arrow_table.num_rows)) if arrow_table.num_rows else arrow_table

    columns = list(arrow_table.schema.names)
    # pylist 변환 — nested/struct 컬럼은 dict/list 로 들어오므로 str() 처리.
    rows = head.to_pylist() if head.num_rows else []

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(c) is None else str(row.get(c)) for c in columns])
    return buf.getvalue().encode("utf-8")
