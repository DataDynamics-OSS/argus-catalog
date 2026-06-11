# SPDX-License-Identifier: Apache-2.0
"""Kudu 테스트용 테이블 — kudu-python API 로 직접 생성.

Kudu 는 자체 메타데이터 카탈로그를 가지므로 HMS 가 필요 없다. 테이블 이름은
freeform string. Impala 와 같이 쓸 때는 ``impala::default.foo`` 형태로 prefix 가
붙는다. 통합 테스트에서는 prefix 없는 plain 이름과 ``impala::`` prefix 두 형식을
모두 만들어 어댑터의 ``parse_impala_naming`` 동작도 검증한다.
"""

from __future__ import annotations

from typing import Any


TABLES = (
    "orders",
    "impala::default.events",
)


def _schema(kudu_mod):
    builder = kudu_mod.schema_builder()
    builder.add_column("order_id").type(kudu_mod.int64).nullable(False).primary_key()
    builder.add_column("customer_id").type(kudu_mod.int64).nullable(False)
    builder.add_column("amount_cents").type(kudu_mod.int64).nullable(True)
    builder.add_column("status").type(kudu_mod.string).nullable(True)
    return builder.build()


def _events_schema(kudu_mod):
    builder = kudu_mod.schema_builder()
    builder.add_column("event_id").type(kudu_mod.int64).nullable(False).primary_key()
    builder.add_column("user_id").type(kudu_mod.int64).nullable(True)
    builder.add_column("event_type").type(kudu_mod.string).nullable(True)
    return builder.build()


def seed_all(client: Any) -> None:
    import kudu
    from kudu.client import Partitioning

    drop_all(client)

    # orders — HASH 파티션 (4 buckets)
    client.create_table(
        "orders",
        _schema(kudu),
        Partitioning().add_hash_partitions(column_names=["order_id"], num_buckets=4),
    )

    # impala::default.events — Impala 가 만든 표시 형식
    client.create_table(
        "impala::default.events",
        _events_schema(kudu),
        Partitioning().add_hash_partitions(column_names=["event_id"], num_buckets=2),
    )


def drop_all(client: Any) -> None:
    for name in TABLES:
        try:
            if client.table_exists(name):
                client.delete_table(name)
        except Exception:
            pass
