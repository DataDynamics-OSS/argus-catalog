# SPDX-License-Identifier: Apache-2.0
"""Iceberg REST Catalog 동기화 어댑터의 end-to-end 검증.

흐름:
  1. compose 스택 기동 (session fixture ``iceberg_stack``)
  2. ``seed_all`` 로 namespace/테이블/데이터 생성
  3. metadata-sync 에 연결 설정 PUT
  4. ``/sync/iceberg-rest/run`` 트리거
  5. argus-catalog-server 의 ``/datasets`` 응답을 검증
"""

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
from .seed.tables import seed_all, drop_all

logger = logging.getLogger(__name__)


def _urn(name: str, datasource_id: str) -> str:
    """현재 URN 포맷(환경 origin 제외): ``{datasource_id}.{name}.dataset``."""
    return f"{datasource_id}.{name}.dataset"


@pytest.mark.iceberg
def test_sync_creates_expected_datasets(
    iceberg_catalog,
    iceberg_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_iceberg_datasets,
):
    """seed 후 sync 한 번 → catalog 에 세 개의 dataset 이 만들어진다."""
    # 1. 기존 잔여 정리 후 깨끗한 상태로 seed
    drop_all(iceberg_catalog)
    seed_all(iceberg_catalog)

    # 2. sync 트리거
    result = sync_client.run_now(iceberg_datasource_id)
    assert result["success"], (
        f"Sync reported failure: {result.get('errors')}"
    )
    assert result["created"] + result["updated"] >= 4, (
        f"Expected at least 4 datasets (orders/events/items/bucketed_users), got {result}"
    )

    # 3. 평면 테이블(``analytics.orders``) — schema/타입/identifier_field_ids 검증
    orders = assert_dataset_exists(
        argus_client, _urn("analytics.orders", iceberg_datasource_id),
    )
    assert orders["urn"] == _urn("analytics.orders", iceberg_datasource_id)
    assert orders["table_type"] == "ICEBERG_TABLE"
    assert orders["storage_format"] == "ICEBERG"
    assert_schema_fields(orders, [
        {"field_path": "order_id", "field_type": "BIGINT",
         "is_primary_key": "true", "nullable": "false"},
        {"field_path": "customer_id", "field_type": "BIGINT",
         "nullable": "false", "is_primary_key": "false"},
        {"field_path": "amount_cents", "field_type": "BIGINT",
         "nullable": "true"},
        {"field_path": "status", "field_type": "STRING",
         "nullable": "true"},
        {"field_path": "created_at", "field_type": "TIMESTAMP",
         "nullable": "true"},
    ])
    # 핵심 platform_properties — Iceberg 고유 메타
    assert_property(orders, "iceberg.namespace", "analytics")
    assert_property(orders, "iceberg.format_version")  # 존재만 확인
    location = assert_property(orders, "iceberg.location")
    assert location.startswith("s3://warehouse/"), (
        f"Unexpected Iceberg location: {location}"
    )

    # 4. 파티션 테이블 — partition_columns 가 정확히 ``ts`` 한 개여야 함
    events = assert_dataset_exists(
        argus_client, _urn("analytics.events", iceberg_datasource_id),
    )
    assert_property(events, "iceberg.partition_columns", "ts")
    # ts 컬럼만 partition key
    assert_schema_fields(events, [
        {"field_path": "ts", "is_partition_key": "true", "nullable": "false"},
        {"field_path": "event_id", "is_partition_key": "false"},
    ])

    # 5. nested 테이블 — STRUCT / ARRAY 타입 매핑
    items = assert_dataset_exists(
        argus_client, _urn("warehouse.items", iceberg_datasource_id),
    )
    by_path = {f["field_path"]: f for f in items["schema_fields"]}
    assert by_path["dimensions"]["field_type"].startswith("STRUCT<"), (
        f"Expected STRUCT type for dimensions, got {by_path['dimensions']['field_type']}"
    )
    assert by_path["tags"]["field_type"].startswith("ARRAY<"), (
        f"Expected ARRAY type for tags, got {by_path['tags']['field_type']}"
    )

    # ----- sample data 업로드 -----
    # sync 가 만든 모든 dataset 에 대해 pyiceberg 로 일부 row 를 CSV 로 업로드하고
    # parquet 으로 변환해 catalog UI 의 Sample Data 탭에서 바로 조회 가능하게 한다.
    listing = argus_client.list_datasets(datasource=iceberg_datasource_id, page=1, page_size=100)
    items = listing.get("items", [])
    assert items, "Expected iceberg datasets in catalog after sync"
    uploaded = 0
    for ds in items:
        # URN 의 ``namespace.table`` 부분을 그대로 identifier 로 사용 (RestCatalog 는
        # 점 표기 문자열도 받는다).
        urn = ds.get("urn") or ""
        parts = urn.split(".")
        path_tokens = parts[1:-1] if len(parts) >= 4 else []
        if not path_tokens:
            logger.warning("Cannot derive identifier from URN %s", urn)
            continue
        identifier = tuple(path_tokens)
        try:
            csv_bytes = fetch_sample_csv(iceberg_catalog, identifier)
        except Exception as e:
            logger.warning("Failed to fetch sample for %s: %s", ".".join(path_tokens), e)
            continue
        try:
            argus_client.upload_sample(ds["id"], csv_bytes)
            argus_client.convert_sample_to_parquet(ds["id"])
            uploaded += 1
        except Exception as e:
            logger.warning("Failed to upload sample for dataset %s: %s", ds["name"], e)
    assert uploaded >= 1, f"Expected to upload samples for >=1 datasets, got {uploaded}"
