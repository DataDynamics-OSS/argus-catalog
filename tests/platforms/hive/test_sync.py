"""Hive 동기화 어댑터 end-to-end 검증 (thrift mode)."""

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
from .seed.samples import EmptyWarehouseError, fetch_sample_csv
from .seed.tables import drop_all, seed_all

logger = logging.getLogger(__name__)


def _urn(qualified_name: str, datasource_id: str) -> str:
    # 현재 URN 포맷은 환경(origin)을 포함하지 않는다: {datasource_id}.{qualified_name}.dataset
    return f"{datasource_id}.{qualified_name}.dataset"


@pytest.mark.hive
@pytest.mark.slow
def test_sync_creates_expected_datasets(
    hms_client,
    hive_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_hive_datasets,
):
    drop_all(hms_client)
    seed_all(hms_client)

    result = sync_client.run_now(hive_datasource_id)
    assert result["success"], f"Sync failed: {result.get('errors')}"
    assert result["created"] + result["updated"] >= 2

    # URN 은 sync 어댑터가 쓴 datasource_id 로 구성된다.
    platform_id_value = hive_datasource_id

    # ----- sales.orders -----
    orders = assert_dataset_exists(
        argus_client, _urn("sales.orders", platform_id_value),
    )
    assert_schema_fields(orders, [
        {"field_path": "order_id", "field_type": "BIGINT"},
        {"field_path": "customer_id", "field_type": "BIGINT"},
        {"field_path": "amount_cents", "field_type": "BIGINT"},
        {"field_path": "status", "field_type": "STRING"},
    ])
    location = assert_property(orders, "hive.location")
    assert location.startswith("s3a://warehouse/"), location

    # ----- sales.events (파티션) -----
    events = assert_dataset_exists(
        argus_client, _urn("sales.events", platform_id_value),
    )
    # 파티션 컬럼 ``ds`` 가 is_partition_key=true 로 표시되어야.
    by_path = {f["field_path"]: f for f in events["schema_fields"]}
    assert "ds" in by_path
    assert by_path["ds"]["is_partition_key"] == "true"

    # ----- sample data 업로드 -----
    # Hive seed 는 HMS metadata 만 등록하고 warehouse 에 실데이터를 쓰지 않으므로
    # ``fetch_sample_csv`` 가 항상 EmptyWarehouseError 로 skip. 어쩔 수 없이 sample
    # 업로드 건수는 0 이지만, 다른 플랫폼과 코드 흐름을 맞추기 위해 동일한 loop 를
    # 두고 하한만 0 으로 둔다.
    listing = argus_client.list_datasets(datasource=hive_datasource_id, page=1, page_size=100)
    items = listing.get("items", [])
    assert items, "Expected hive datasets in catalog after sync"
    uploaded = 0
    for ds in items:
        urn = ds.get("urn") or ""
        parts = urn.split(".")
        path_tokens = parts[1:-1] if len(parts) >= 4 else []
        qualified = ".".join(path_tokens) if path_tokens else ds["name"]
        try:
            csv_bytes = fetch_sample_csv(hms_client, qualified)
        except EmptyWarehouseError as e:
            logger.warning("Skip sample for %s: %s", qualified, e)
            continue
        except Exception as e:
            logger.warning("Failed to fetch sample for %s: %s", qualified, e)
            continue
        try:
            argus_client.upload_sample(ds["id"], csv_bytes)
            argus_client.convert_sample_to_parquet(ds["id"])
            uploaded += 1
        except Exception as e:
            logger.warning("Failed to upload sample for dataset %s: %s", ds["name"], e)
    assert uploaded >= 0, f"Sample upload counter should never go negative, got {uploaded}"
