# SPDX-License-Identifier: Apache-2.0
"""Oracle 통합 테스트 시드 — APP 스키마(ARGUS_TEST)에 테이블 직접 생성.

Oracle 은 따옴표 없는 식별자를 대문자로 저장하므로, 생성한 ``orders`` 는
``ORDERS``, 컬럼 ``order_id`` 는 ``ORDER_ID`` 로 조회된다.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


TABLES: tuple[str, ...] = ("orders", "customers")

_DDL: dict[str, str] = {
    "orders": """
        CREATE TABLE orders (
            order_id     NUMBER(19)   PRIMARY KEY,
            customer_id  NUMBER(19),
            amount_cents NUMBER(19),
            status       VARCHAR2(32)
        )
    """,
    "customers": """
        CREATE TABLE customers (
            customer_id NUMBER(19)    PRIMARY KEY,
            name        VARCHAR2(128) NOT NULL,
            email       VARCHAR2(256)
        )
    """,
}


def drop_all(engine: Engine) -> None:
    """테스트 테이블 제거. 없으면(ORA-00942) 무시. 각 DROP 을 독립 트랜잭션으로."""
    for tbl in TABLES:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"DROP TABLE {tbl} CASCADE CONSTRAINTS"))
        except Exception:
            pass


def seed_all(engine: Engine) -> None:
    """테스트 테이블을 (재)생성."""
    drop_all(engine)
    with engine.begin() as conn:
        for tbl in TABLES:
            conn.execute(text(_DDL[tbl]))
