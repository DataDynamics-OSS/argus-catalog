"""MySQL 동기화 어댑터 end-to-end 검증 — Sakila 샘플 DB 기반."""

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
    # 현재 URN 포맷은 환경(origin)을 포함하지 않는다: {datasource_id}.{qualified_name}.dataset
    return f"{datasource_id}.{qualified_name}.dataset"


def _qn(table: str) -> str:
    """MySQL 은 schema 개념이 없어 qualified_name 이 ``database.table`` 형태."""
    return f"sakila.{table}"


@pytest.mark.mysql
def test_sync_creates_expected_datasets(
    mysql_engine,
    mysql_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_mysql_datasets,
):
    drop_all(mysql_engine)
    seed_all(mysql_engine)

    result = sync_client.run_now(mysql_datasource_id)
    assert result["success"], f"Sync failed: {result.get('errors')}"
    # sakila 는 16 base tables. 어댑터가 view 도 sync 한다면 더 많을 수도 있어 하한만 검증.
    assert result["created"] + result["updated"] >= 16

    # URN 은 sync 어댑터가 쓴 datasource_id(=mysql_datasource_id) 로 구성된다.
    platform_id_value = mysql_datasource_id

    # ----- actor -----
    actor = assert_dataset_exists(
        argus_client, _urn(_qn("actor"), platform_id_value),
    )
    assert_schema_fields(actor, [
        {"field_path": "actor_id", "is_primary_key": "true", "nullable": "false"},
        {"field_path": "first_name", "nullable": "false"},
        {"field_path": "last_name", "nullable": "false"},
        {"field_path": "last_update", "nullable": "false"},
    ])
    assert_property(actor, "mysql.engine", "InnoDB")

    # ----- film -----
    film = assert_dataset_exists(
        argus_client, _urn(_qn("film"), platform_id_value),
    )
    assert_schema_fields(film, [
        {"field_path": "film_id", "is_primary_key": "true", "nullable": "false"},
        {"field_path": "title", "nullable": "false"},
        {"field_path": "language_id", "nullable": "false"},
    ])

    # ----- customer (FK 가 여러 개 있어 어댑터의 FK 추적이 동작하는지) -----
    customer = assert_dataset_exists(
        argus_client, _urn(_qn("customer"), platform_id_value),
    )
    assert_schema_fields(customer, [
        {"field_path": "customer_id", "is_primary_key": "true", "nullable": "false"},
        {"field_path": "store_id", "nullable": "false"},
        {"field_path": "first_name", "nullable": "false"},
    ])

    # ----- sample data 업로드 -----
    # sync 가 만든 모든 dataset 에 대해 sakila 의 일부 row 를 CSV 로 업로드하고
    # parquet 으로 변환해 catalog UI 의 Sample Data 탭에서 바로 조회 가능하게 한다.
    listing = argus_client.list_datasets(datasource=mysql_datasource_id, page=1, page_size=100)
    items = listing.get("items", [])
    assert items, "Expected sakila datasets in catalog after sync"
    uploaded = 0
    for ds in items:
        # list endpoint 는 qualified_name 을 노출하지 않으므로 dataset name 으로 직접 매칭.
        sakila_table = f"sakila.{ds['name']}"
        try:
            csv_bytes = fetch_sample_csv(mysql_engine, sakila_table)
        except Exception as e:
            logger.warning("Failed to fetch sample for %s: %s", sakila_table, e)
            continue
        try:
            argus_client.upload_sample(ds["id"], csv_bytes)
            argus_client.convert_sample_to_parquet(ds["id"])
            uploaded += 1
        except Exception as e:
            logger.warning("Failed to upload sample for dataset %s: %s", ds["name"], e)
    assert uploaded >= 16, f"Expected to upload samples for >=16 datasets, got {uploaded}"
