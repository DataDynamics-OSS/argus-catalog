# SPDX-License-Identifier: Apache-2.0
"""Trino 동기화 어댑터 end-to-end 검증."""

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


@pytest.mark.trino
@pytest.mark.slow
def test_sync_creates_expected_datasets(
    trino_conn,
    trino_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_trino_datasets,
):
    drop_all(trino_conn)
    seed_all(trino_conn)

    result = sync_client.run_now(trino_datasource_id)
    assert result["success"], f"Sync failed: {result.get('errors')}"
    assert result["created"] + result["updated"] >= 2

    # URN 은 sync 어댑터가 쓴 datasource_id 로 구성된다.
    platform_id_value = trino_datasource_id

    # Trino 의 qualified_name 은 ``catalog.schema.table``.
    orders = assert_dataset_exists(
        argus_client, _urn("hive.sales.orders", platform_id_value),
    )
    assert_schema_fields(orders, [
        {"field_path": "order_id", "field_type": "BIGINT"},
        {"field_path": "customer_id", "field_type": "BIGINT"},
        {"field_path": "amount_cents", "field_type": "BIGINT"},
    ])

    # ----- sample data 업로드 -----
    # sync 가 만든 모든 dataset 에 대해 Trino 로 일부 row 를 CSV 로 업로드하고
    # parquet 으로 변환해 catalog UI 의 Sample Data 탭에서 바로 조회 가능하게 한다.
    listing = argus_client.list_datasets(datasource=trino_datasource_id, page=1, page_size=100)
    items = listing.get("items", [])
    assert items, "Expected trino datasets in catalog after sync"
    uploaded = 0
    for ds in items:
        # URN 에서 ``catalog.schema.table`` path 를 복원해 그대로 SELECT 에 사용.
        urn = ds.get("urn") or ""
        parts = urn.split(".")
        path_tokens = parts[1:-1] if len(parts) >= 5 else []
        if len(path_tokens) < 3:
            logger.warning("Cannot derive qualified name from URN %s", urn)
            continue
        qualified = ".".join(path_tokens)
        try:
            csv_bytes = fetch_sample_csv(trino_conn, qualified)
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
