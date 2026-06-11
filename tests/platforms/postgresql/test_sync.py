# SPDX-License-Identifier: Apache-2.0
"""PostgreSQL 동기화 어댑터 end-to-end 검증 — northwind 샘플 DB 기반."""

from __future__ import annotations

import logging

import pytest

from lib.argus_client import ArgusClient
from lib.assertions import (
    assert_dataset_exists,
    assert_property,
    assert_schema_fields,
)
from lib.sync_client import SyncClient
from .seed.samples import fetch_sample_csv
from .seed.tables import drop_all, seed_all

logger = logging.getLogger(__name__)


def _urn(qualified_name: str, datasource_id: str) -> str:
    """현재 URN 포맷(환경 origin 제외): ``{datasource_id}.{qualified_name}.dataset``."""
    return f"{datasource_id}.{qualified_name}.dataset"


def _qn(table: str) -> str:
    """PG 어댑터는 ``database.schema.table`` 형식의 qualified_name 을 만든다.

    northwind 는 모두 ``public`` schema 에 들어가므로 schema 부분이 ``public`` 으로 고정.
    """
    return f"northwind.public.{table}"


@pytest.mark.postgresql
def test_sync_creates_expected_datasets(
    postgres_engine,
    pg_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_postgresql_datasets,
):
    drop_all(postgres_engine)
    seed_all(postgres_engine)

    result = sync_client.run_now(pg_datasource_id)
    assert result["success"], f"Sync failed: {result.get('errors')}"
    # northwind 는 14 base tables. 하한만 검증.
    assert result["created"] + result["updated"] >= 14

    # URN 은 sync 어댑터가 쓴 datasource_id 로 구성된다.
    platform_id_value = pg_datasource_id

    # ----- public.customers -----
    customers = assert_dataset_exists(
        argus_client, _urn(_qn("customers"), platform_id_value),
    )
    assert_schema_fields(customers, [
        {"field_path": "customer_id", "is_primary_key": "true", "nullable": "false"},
        {"field_path": "company_name", "nullable": "false"},
    ])
    assert_property(customers, "postgresql.database", "northwind")

    # ----- public.orders -----
    orders = assert_dataset_exists(
        argus_client, _urn(_qn("orders"), platform_id_value),
    )
    assert_schema_fields(orders, [
        {"field_path": "order_id", "is_primary_key": "true", "nullable": "false"},
        {"field_path": "customer_id", "nullable": "true"},
    ])

    # ----- public.products -----
    products = assert_dataset_exists(
        argus_client, _urn(_qn("products"), platform_id_value),
    )
    assert_schema_fields(products, [
        {"field_path": "product_id", "is_primary_key": "true", "nullable": "false"},
        {"field_path": "product_name", "nullable": "false"},
    ])

    # ----- sample data 업로드 -----
    # sync 가 만든 모든 dataset 에 대해 PG 의 일부 row 를 CSV 로 업로드하고 parquet 으로
    # 변환해 catalog UI 의 Sample Data 탭에서 바로 조회 가능하게 한다.
    listing = argus_client.list_datasets(datasource=pg_datasource_id, page=1, page_size=100)
    items = listing.get("items", [])
    assert items, "Expected postgresql datasets in catalog after sync"
    uploaded = 0
    for ds in items:
        # list endpoint 는 qualified_name 을 노출하지 않으므로 URN 에서 path 를 복원해
        # database token 을 떨어뜨려 ``schema.table`` 로 SELECT 한다.
        urn = ds.get("urn") or ""
        parts = urn.split(".")
        # ``{datasource_id}.{database}.{schema}.{table}.dataset`` — 앞 1, 뒤 1(dataset) 제거.
        path_tokens = parts[1:-1] if len(parts) >= 4 else []
        if len(path_tokens) < 3:
            logger.warning("Cannot derive qualified name from URN %s", urn)
            continue
        # database 토큰을 제거하고 ``schema.table`` 만 사용.
        qualified = ".".join(path_tokens[1:])
        try:
            csv_bytes = fetch_sample_csv(postgres_engine, qualified)
        except Exception as e:
            logger.warning("Failed to fetch sample for %s: %s", qualified, e)
            continue
        try:
            argus_client.upload_sample(ds["id"], csv_bytes)
            argus_client.convert_sample_to_parquet(ds["id"])
            uploaded += 1
        except Exception as e:
            logger.warning("Failed to upload sample for dataset %s: %s", ds["name"], e)
    assert uploaded >= 10, f"Expected to upload samples for >=10 datasets, got {uploaded}"
