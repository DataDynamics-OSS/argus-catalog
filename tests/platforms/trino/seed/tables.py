"""Trino + Hive connector — 스키마/테이블 생성. warehouse 는 MinIO S3.

CREATE TABLE 만으로 데이터 파일은 만들지 않지만 metadata 는 HMS 에 등록되어
sync 어댑터가 그대로 읽어간다.
"""

from __future__ import annotations

SCHEMA = "sales"

_STATEMENTS = [
    "DROP TABLE IF EXISTS hive.sales.orders",
    "DROP TABLE IF EXISTS hive.sales.events",
    "DROP SCHEMA IF EXISTS hive.sales",

    "CREATE SCHEMA hive.sales WITH (location = 's3a://warehouse/sales')",

    """
    CREATE TABLE hive.sales.orders (
      order_id      BIGINT  COMMENT '주문 ID',
      customer_id   BIGINT,
      amount_cents  BIGINT  COMMENT '결제 금액(센트)',
      status        VARCHAR
    )
    WITH (
      format = 'PARQUET',
      external_location = 's3a://warehouse/sales/orders'
    )
    """,

    """
    CREATE TABLE hive.sales.events (
      event_id    BIGINT,
      user_id     BIGINT,
      event_type  VARCHAR,
      ds          VARCHAR
    )
    WITH (
      format = 'PARQUET',
      partitioned_by = ARRAY['ds'],
      external_location = 's3a://warehouse/sales/events'
    )
    """,
]


def seed_all(conn) -> None:
    cur = conn.cursor()
    for stmt in _STATEMENTS:
        cur.execute(stmt)
        cur.fetchall()


def drop_all(conn) -> None:
    cur = conn.cursor()
    for stmt in (
        "DROP TABLE IF EXISTS hive.sales.orders",
        "DROP TABLE IF EXISTS hive.sales.events",
        "DROP SCHEMA IF EXISTS hive.sales",
    ):
        try:
            cur.execute(stmt)
            cur.fetchall()
        except Exception:
            pass
