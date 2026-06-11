"""Oracle 동기화 어댑터 end-to-end 검증 — ARGUS_TEST 스키마 기반."""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.assertions import (
    assert_dataset_exists,
    assert_property,
    assert_schema_fields,
)
from lib.sync_client import SyncClient
from .conftest import ORA_SCHEMA
from .seed.tables import drop_all, seed_all


def _urn(qualified_name: str, datasource_id: str) -> str:
    # 현재 URN 포맷(환경 origin 제외): {datasource_id}.{qualified_name}.dataset
    return f"{datasource_id}.{qualified_name}.dataset"


def _qn(table: str) -> str:
    """Oracle 은 식별자를 대문자로 저장 → qualified_name 은 ``SCHEMA.TABLE``."""
    return f"{ORA_SCHEMA}.{table.upper()}"


@pytest.mark.oracle
def test_sync_creates_expected_datasets(
    oracle_engine,
    oracle_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_oracle_datasets,
):
    drop_all(oracle_engine)
    seed_all(oracle_engine)

    result = sync_client.run_now(oracle_datasource_id)
    assert result["success"], f"Sync failed: {result.get('errors')}"
    # orders + customers 2개. 하한만 검증.
    assert result["created"] + result["updated"] >= 2

    # URN 은 sync 어댑터가 쓴 datasource_id 로 구성된다.
    platform_id_value = oracle_datasource_id

    # ----- orders (NUMBER→DECIMAL, VARCHAR2→VARCHAR, PK 추적) -----
    orders = assert_dataset_exists(
        argus_client, _urn(_qn("orders"), platform_id_value),
    )
    assert_schema_fields(orders, [
        {"field_path": "ORDER_ID", "field_type": "DECIMAL",
         "is_primary_key": "true", "nullable": "false"},
        {"field_path": "CUSTOMER_ID", "field_type": "DECIMAL"},
        {"field_path": "AMOUNT_CENTS", "field_type": "DECIMAL"},
        {"field_path": "STATUS", "field_type": "VARCHAR"},
    ])
    assert_property(orders, "oracle.schema", ORA_SCHEMA)

    # ----- customers (NOT NULL 컬럼 nullable 표기 확인) -----
    customers = assert_dataset_exists(
        argus_client, _urn(_qn("customers"), platform_id_value),
    )
    assert_schema_fields(customers, [
        {"field_path": "CUSTOMER_ID", "field_type": "DECIMAL", "is_primary_key": "true"},
        {"field_path": "NAME", "field_type": "VARCHAR", "nullable": "false"},
        {"field_path": "EMAIL", "field_type": "VARCHAR"},
    ])
