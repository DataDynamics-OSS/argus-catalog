"""Kudu 동기화 어댑터 end-to-end 검증."""

from __future__ import annotations

import logging

import pytest

from lib.argus_client import ArgusClient
from lib.assertions import assert_dataset_exists, assert_schema_fields
from lib.sync_client import SyncClient
from .seed.samples import fetch_sample_csv
from .seed.tables import drop_all, seed_all

logger = logging.getLogger(__name__)


def _urn(qualified_name: str, datasource_id: str) -> str:
    """현재 URN 포맷(환경 origin 제외): ``{datasource_id}.{qualified_name}.dataset``."""
    return f"{datasource_id}.{qualified_name}.dataset"


@pytest.mark.kudu
@pytest.mark.slow
def test_sync_creates_expected_datasets(
    kudu_client,
    kudu_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_kudu_datasets,
):
    drop_all(kudu_client)
    seed_all(kudu_client)

    result = sync_client.run_now(kudu_datasource_id)
    assert result["success"], f"Sync failed: {result.get('errors')}"
    assert result["created"] + result["updated"] >= 2

    # URN 은 sync 어댑터가 쓴 datasource_id 로 구성된다.
    platform_id_value = kudu_datasource_id

    # ----- orders (prefix 없는 plain) -----
    # 어댑터의 default_database 가 ``default`` 라 qualified_name 은 ``default.orders``.
    orders = assert_dataset_exists(
        argus_client, _urn("default.orders", platform_id_value),
    )
    assert_schema_fields(orders, [
        {"field_path": "order_id", "is_primary_key": "true", "nullable": "false"},
        {"field_path": "customer_id", "is_primary_key": "false", "nullable": "false"},
        {"field_path": "amount_cents", "nullable": "true"},
        {"field_path": "status", "nullable": "true"},
    ])

    # ----- impala::default.events — parse_impala_naming=true 라 prefix 가 벗겨진다 -----
    events = assert_dataset_exists(
        argus_client, _urn("default.events", platform_id_value),
    )
    assert_schema_fields(events, [
        {"field_path": "event_id", "is_primary_key": "true", "nullable": "false"},
    ])

    # ----- sample data 업로드 -----
    # sync 가 만든 모든 dataset 에 대해 kudu scanner 로 일부 row 를 CSV 로 업로드하고
    # parquet 으로 변환해 catalog UI 의 Sample Data 탭에서 바로 조회 가능하게 한다.
    listing = argus_client.list_datasets(datasource=kudu_datasource_id, page=1, page_size=100)
    items = listing.get("items", [])
    assert items, "Expected kudu datasets in catalog after sync"
    uploaded = 0
    for ds in items:
        # dataset name 은 ``orders`` 처럼 plain 이거나, parse_impala_naming 으로
        # prefix 가 벗겨진 ``events`` 일 수 있다. 후보를 순서대로 시도.
        name = ds["name"]
        candidates = [name, f"impala::default.{name}"]
        table_name = None
        for cand in candidates:
            try:
                if kudu_client.table_exists(cand):
                    table_name = cand
                    break
            except Exception:
                continue
        if not table_name:
            logger.warning("No matching Kudu table for dataset %s", name)
            continue
        try:
            csv_bytes = fetch_sample_csv(kudu_client, table_name)
        except Exception as e:
            logger.warning("Failed to fetch sample for %s: %s", table_name, e)
            continue
        try:
            argus_client.upload_sample(ds["id"], csv_bytes)
            argus_client.convert_sample_to_parquet(ds["id"])
            uploaded += 1
        except Exception as e:
            logger.warning("Failed to upload sample for dataset %s: %s", ds["name"], e)
    assert uploaded >= 1, f"Expected to upload samples for >=1 datasets, got {uploaded}"
