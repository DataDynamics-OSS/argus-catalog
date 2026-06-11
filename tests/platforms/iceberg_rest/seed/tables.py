# SPDX-License-Identifier: Apache-2.0
"""Iceberg REST Catalog 에 테스트용 namespace/테이블/데이터를 만든다.

호출하는 쪽이 ``RestCatalog`` 인스턴스를 넘기면 in-place 로 테이블을 생성하고
PyArrow Table 로 한 번 append 해 스냅샷 1개를 남긴다.

테이블 매트릭스:
  - ``analytics.orders``      : 평면 테이블 (PK + nullable 컬럼)
  - ``analytics.events``      : days(ts) 파티션
  - ``warehouse.items``       : nested struct 컬럼
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import (
    NamespaceAlreadyExistsError,
    NoSuchNamespaceError,
    NoSuchTableError,
    TableAlreadyExistsError,
)
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import BucketTransform, DayTransform, IdentityTransform
from pyiceberg.types import (
    IntegerType,
    ListType,
    LongType,
    NestedField,
    StringType,
    StructType,
    TimestampType,
)


NAMESPACES = ("analytics", "warehouse")


def _ensure_namespace(catalog: Catalog, ns: str) -> None:
    try:
        catalog.create_namespace(ns)
    except NamespaceAlreadyExistsError:
        pass


def _drop_table_if_exists(catalog: Catalog, identifier: tuple[str, ...]) -> None:
    try:
        catalog.drop_table(identifier)
    except NoSuchTableError:
        pass


def _drop_namespace_if_empty(catalog: Catalog, ns: tuple[str, ...]) -> None:
    try:
        # list_tables 가 비어 있어야 drop 가능
        if not list(catalog.list_tables(ns)):
            catalog.drop_namespace(ns)
    except (NoSuchNamespaceError, Exception):
        pass


# ---------------------------------------------------------------------------
# 1. analytics.orders — 평면, identifier_field_ids 로 PK 표현
# ---------------------------------------------------------------------------

ORDERS_SCHEMA = Schema(
    NestedField(1, "order_id", LongType(), required=True, doc="주문 ID (PK)"),
    NestedField(2, "customer_id", LongType(), required=True),
    NestedField(3, "amount_cents", LongType(), required=False, doc="결제 금액(센트)"),
    NestedField(4, "status", StringType(), required=False),
    NestedField(5, "created_at", TimestampType(), required=False),
    identifier_field_ids=[1],
)

ORDERS_ARROW = pa.schema([
    pa.field("order_id", pa.int64(), nullable=False),
    pa.field("customer_id", pa.int64(), nullable=False),
    pa.field("amount_cents", pa.int64(), nullable=True),
    pa.field("status", pa.string(), nullable=True),
    pa.field("created_at", pa.timestamp("us"), nullable=True),
])


# ---------------------------------------------------------------------------
# 2. analytics.events — days(ts) 파티션
# ---------------------------------------------------------------------------

EVENTS_SCHEMA = Schema(
    NestedField(1, "event_id", LongType(), required=True),
    NestedField(2, "user_id", LongType(), required=False),
    NestedField(3, "event_type", StringType(), required=False),
    NestedField(4, "ts", TimestampType(), required=True),
)

EVENTS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=4, field_id=1000, transform=DayTransform(), name="ts_day",
    ),
)

EVENTS_ARROW = pa.schema([
    pa.field("event_id", pa.int64(), nullable=False),
    pa.field("user_id", pa.int64(), nullable=True),
    pa.field("event_type", pa.string(), nullable=True),
    pa.field("ts", pa.timestamp("us"), nullable=False),
])


# ---------------------------------------------------------------------------
# 3. warehouse.items — nested struct + list
# ---------------------------------------------------------------------------

ITEMS_SCHEMA = Schema(
    NestedField(1, "item_id", LongType(), required=True),
    NestedField(2, "name", StringType(), required=False),
    NestedField(
        3,
        "dimensions",
        StructType(
            NestedField(10, "width_mm", IntegerType(), required=False),
            NestedField(11, "height_mm", IntegerType(), required=False),
            NestedField(12, "depth_mm", IntegerType(), required=False),
        ),
        required=False,
    ),
    NestedField(
        4,
        "tags",
        ListType(element_id=20, element_type=StringType(), element_required=False),
        required=False,
    ),
)

ITEMS_ARROW = pa.schema([
    pa.field("item_id", pa.int64(), nullable=False),
    pa.field("name", pa.string(), nullable=True),
    pa.field(
        "dimensions",
        pa.struct([
            pa.field("width_mm", pa.int32(), nullable=True),
            pa.field("height_mm", pa.int32(), nullable=True),
            pa.field("depth_mm", pa.int32(), nullable=True),
        ]),
        nullable=True,
    ),
    pa.field("tags", pa.list_(pa.string()), nullable=True),
])


# ---------------------------------------------------------------------------
# 4. analytics.bucketed_users — bucket(16, user_id) + identity(region) 다중 파티션
# transform 매핑 정밀 검증용. 두 개의 파티션 필드로 partition_columns / transforms
# CSV 가 정확한 순서로 노출되는지 본다.
# ---------------------------------------------------------------------------

BUCKETED_USERS_SCHEMA = Schema(
    NestedField(1, "user_id", LongType(), required=True),
    NestedField(2, "region", StringType(), required=True),
    NestedField(3, "name", StringType(), required=False),
)

BUCKETED_USERS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=1, field_id=1000, transform=BucketTransform(16), name="user_id_bucket",
    ),
    PartitionField(
        source_id=2, field_id=1001, transform=IdentityTransform(), name="region",
    ),
)

BUCKETED_USERS_ARROW = pa.schema([
    pa.field("user_id", pa.int64(), nullable=False),
    pa.field("region", pa.string(), nullable=False),
    pa.field("name", pa.string(), nullable=True),
])


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def seed_all(catalog: Catalog) -> dict[str, str]:
    """모든 테스트 namespace/테이블/데이터를 (재)생성하고 ``{full_name: location}`` 반환."""

    for ns in NAMESPACES:
        _ensure_namespace(catalog, ns)

    created: dict[str, str] = {}

    # ---- analytics.orders -------------------------------------------------
    _drop_table_if_exists(catalog, ("analytics", "orders"))
    orders = catalog.create_table(
        ("analytics", "orders"),
        schema=ORDERS_SCHEMA,
        properties={"comment": "테스트용 주문 데이터셋", "owner": "argus-tests"},
    )
    orders_data = pa.Table.from_pydict({
        "order_id": [1, 2, 3],
        "customer_id": [100, 100, 101],
        "amount_cents": [12000, 5000, None],
        "status": ["PAID", "PAID", "PENDING"],
        "created_at": [
            datetime(2026, 5, 1, 10, 0, 0),
            datetime(2026, 5, 2, 11, 30, 0),
            datetime(2026, 5, 3, 9, 15, 0),
        ],
    }, schema=ORDERS_ARROW)
    orders.append(orders_data)
    created["analytics.orders"] = orders.metadata.location

    # ---- analytics.events -------------------------------------------------
    _drop_table_if_exists(catalog, ("analytics", "events"))
    events = catalog.create_table(
        ("analytics", "events"),
        schema=EVENTS_SCHEMA,
        partition_spec=EVENTS_PARTITION_SPEC,
        properties={"comment": "이벤트 로그(일자 파티션)"},
    )
    events_data = pa.Table.from_pydict({
        "event_id": [1, 2, 3, 4],
        "user_id": [10, 10, 11, 12],
        "event_type": ["click", "view", "click", "purchase"],
        "ts": [
            datetime(2026, 5, 1, 0, 30, 0),
            datetime(2026, 5, 1, 12, 0, 0),
            datetime(2026, 5, 2, 8, 45, 0),
            datetime(2026, 5, 3, 19, 20, 0),
        ],
    }, schema=EVENTS_ARROW)
    events.append(events_data)
    created["analytics.events"] = events.metadata.location

    # ---- warehouse.items --------------------------------------------------
    _drop_table_if_exists(catalog, ("warehouse", "items"))
    items = catalog.create_table(
        ("warehouse", "items"),
        schema=ITEMS_SCHEMA,
        properties={"description": "물품 마스터 (nested 컬럼 검증)"},
    )
    items_data = pa.Table.from_pydict({
        "item_id": [1, 2],
        "name": ["A4 용지", "볼펜"],
        "dimensions": [
            {"width_mm": 210, "height_mm": 297, "depth_mm": 5},
            {"width_mm": 8, "height_mm": 150, "depth_mm": 8},
        ],
        "tags": [["paper", "office"], ["pen", "office"]],
    }, schema=ITEMS_ARROW)
    items.append(items_data)
    created["warehouse.items"] = items.metadata.location

    # ---- analytics.bucketed_users -----------------------------------------
    _drop_table_if_exists(catalog, ("analytics", "bucketed_users"))
    bucketed = catalog.create_table(
        ("analytics", "bucketed_users"),
        schema=BUCKETED_USERS_SCHEMA,
        partition_spec=BUCKETED_USERS_PARTITION_SPEC,
        properties={"comment": "bucket+identity 다중 파티션 검증"},
    )
    bucketed_data = pa.Table.from_pydict({
        "user_id": [1, 2, 3, 4],
        "region": ["kr", "kr", "us", "jp"],
        "name": ["Alice", "Bob", "Carol", "Dan"],
    }, schema=BUCKETED_USERS_ARROW)
    bucketed.append(bucketed_data)
    created["analytics.bucketed_users"] = bucketed.metadata.location

    return created


def seed_namespaces(catalog: Catalog) -> Iterable[str]:
    """``seed_all`` 대신 namespace 만 만들고 싶을 때."""
    for ns in NAMESPACES:
        _ensure_namespace(catalog, ns)
    return NAMESPACES


def drop_all(catalog: Catalog) -> None:
    """모든 테스트 테이블/namespace 제거. 다음 테스트가 깨끗하게 시작하도록 사용."""
    for ns, tbl in (
        ("analytics", "orders"),
        ("analytics", "events"),
        ("analytics", "bucketed_users"),
        ("warehouse", "items"),
    ):
        _drop_table_if_exists(catalog, (ns, tbl))
    for ns in NAMESPACES:
        _drop_namespace_if_empty(catalog, (ns,))


def append_events_extra_day(catalog: Catalog) -> str:
    """이미 존재하는 ``analytics.events`` 에 추가 데이터를 append.

    snapshot 갱신 검증용. 새 스냅샷 id 와 last_updated_ms 증가를 노린다.
    반환값은 새 current_snapshot_id (문자열).
    """
    table = catalog.load_table(("analytics", "events"))
    extra = pa.Table.from_pydict({
        "event_id": [100, 101],
        "user_id": [99, 99],
        "event_type": ["view", "click"],
        "ts": [
            datetime(2026, 5, 10, 1, 0, 0),
            datetime(2026, 5, 10, 2, 0, 0),
        ],
    }, schema=EVENTS_ARROW)
    table.append(extra)
    table.refresh()
    return str(table.metadata.current_snapshot_id)
