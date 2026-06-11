"""StarRocks 테스트용 테이블 + 데이터.

StarRocks 는 ENGINE/DISTRIBUTED BY/DUPLICATE KEY 같은 자체 DDL 을 요구한다.
sync 어댑터가 ``starrocks.table_model`` / ``distribute_*`` 같은 properties 를
채우므로 그 값을 검증할 수 있도록 의도적으로 다양한 모델을 사용.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

TABLES: tuple[str, ...] = ("orders", "events")

_DDL = [
    "DROP TABLE IF EXISTS orders",
    "DROP TABLE IF EXISTS events",
    # PRIMARY_KEYS 모델 — 컬럼 코멘트 + 분산 키
    """
    CREATE TABLE orders (
      order_id     BIGINT       NOT NULL COMMENT '주문 ID',
      customer_id  BIGINT       NOT NULL,
      amount_cents BIGINT       COMMENT '결제 금액(센트)',
      status       VARCHAR(20)
    ) ENGINE=OLAP
    PRIMARY KEY (order_id)
    DISTRIBUTED BY HASH(order_id) BUCKETS 4
    PROPERTIES("replication_num"="1")
    """,
    # DUPLICATE 모델 — 파티션 키 검증
    """
    CREATE TABLE events (
      event_id   BIGINT       NOT NULL,
      user_id    BIGINT,
      event_type VARCHAR(50),
      ts         DATETIME     NOT NULL
    ) ENGINE=OLAP
    DUPLICATE KEY (event_id)
    PARTITION BY RANGE(ts) (
      PARTITION p20260501 VALUES LESS THAN ('2026-05-02')
    )
    DISTRIBUTED BY HASH(event_id) BUCKETS 4
    PROPERTIES("replication_num"="1")
    """,
]

_DATA = [
    "INSERT INTO orders (order_id, customer_id, amount_cents, status) VALUES "
    "(1, 100, 12000, 'PAID'), "
    "(2, 100, 5000,  'PAID'), "
    "(3, 101, NULL,  'PENDING')",
    "INSERT INTO events (event_id, user_id, event_type, ts) VALUES "
    "(1, 10, 'click', '2026-05-01 00:30:00'), "
    "(2, 10, 'view',  '2026-05-01 12:00:00')",
]


def seed_all(engine: Engine) -> None:
    with engine.begin() as conn:
        for sql in _DDL:
            conn.execute(text(sql))
        for sql in _DATA:
            conn.execute(text(sql))


def drop_all(engine: Engine) -> None:
    with engine.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
