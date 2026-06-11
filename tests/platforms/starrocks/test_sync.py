"""StarRocks 동기화 어댑터 end-to-end 검증."""

from __future__ import annotations

import logging

import pytest

from lib.argus_client import ArgusClient
from lib.assertions import assert_dataset_exists, assert_property
from lib.sync_client import SyncClient
from .conftest import SR_DATABASE
from .seed.samples import fetch_sample_csv
from .seed.tables import drop_all, seed_all

logger = logging.getLogger(__name__)


def _urn(qualified_name: str, datasource_id: str) -> str:
    """현재 URN 포맷(환경 origin 제외): ``{datasource_id}.{qualified_name}.dataset``."""
    return f"{datasource_id}.{qualified_name}.dataset"


def _qn(table: str) -> str:
    """StarRocks 도 MySQL 처럼 schema 가 없어 qualified_name 은 ``database.table``."""
    return f"argus_test.{table}"


@pytest.mark.starrocks
@pytest.mark.slow
def test_sync_creates_expected_datasets(
    starrocks_engine,
    sr_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_starrocks_datasets,
):
    drop_all(starrocks_engine)
    seed_all(starrocks_engine)

    result = sync_client.run_now(sr_datasource_id)
    assert result["success"], f"Sync failed: {result.get('errors')}"
    assert result["created"] + result["updated"] >= 2

    # URN 은 sync 어댑터가 쓴 datasource_id 로 구성된다.
    platform_id_value = sr_datasource_id

    orders = assert_dataset_exists(
        argus_client, _urn(_qn("orders"), platform_id_value),
    )
    # PRIMARY_KEYS 모델, HASH distribute
    assert_property(orders, "starrocks.table_model")
    assert_property(orders, "starrocks.distribute_key", "order_id")
    assert_property(orders, "starrocks.distribute_type", "HASH")

    events = assert_dataset_exists(
        argus_client, _urn(_qn("events"), platform_id_value),
    )
    assert_property(events, "starrocks.partition_key", "ts")

    # ----- sample data 업로드 -----
    # sync 가 만든 모든 dataset 에 대해 StarRocks 의 일부 row 를 CSV 로 업로드하고
    # parquet 으로 변환해 catalog UI 의 Sample Data 탭에서 바로 조회 가능하게 한다.
    listing = argus_client.list_datasets(datasource=sr_datasource_id, page=1, page_size=100)
    items = listing.get("items", [])
    assert items, "Expected starrocks datasets in catalog after sync"
    uploaded = 0
    for ds in items:
        # MySQL 과 동일: ``database.table`` 로 SELECT.
        qualified = f"{SR_DATABASE}.{ds['name']}"
        try:
            csv_bytes = fetch_sample_csv(starrocks_engine, qualified)
        except Exception as e:
            logger.warning("Failed to fetch sample for %s: %s", qualified, e)
            continue
        try:
            argus_client.upload_sample(ds["id"], csv_bytes)
            argus_client.convert_sample_to_parquet(ds["id"])
            uploaded += 1
        except Exception as e:
            logger.warning("Failed to upload sample for dataset %s: %s", ds["name"], e)
    assert uploaded >= 2, f"Expected to upload samples for >=2 datasets, got {uploaded}"
