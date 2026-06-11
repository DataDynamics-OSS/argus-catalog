"""파티션 transform 매핑 정밀 검증.

``iceberg.partition_columns`` 와 ``iceberg.partition_transforms`` 가
다음 두 케이스에서 정확히 들어가는지 본다.

  - ``analytics.events``         : days(ts) 단일 파티션
  - ``analytics.bucketed_users`` : bucket(16, user_id) + identity(region) 다중 파티션

또한 schema_fields 의 ``is_partition_key`` 도 함께 검증한다.
"""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.assertions import (
    assert_dataset_exists,
    assert_property,
    assert_schema_fields,
)
from lib.sync_client import SyncClient
from .seed.tables import drop_all, seed_all
from .test_sync import _urn


@pytest.mark.iceberg
def test_partition_transforms(
    iceberg_catalog,
    iceberg_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_iceberg_datasets,
):
    drop_all(iceberg_catalog)
    seed_all(iceberg_catalog)

    result = sync_client.run_now(iceberg_datasource_id)
    assert result["success"], f"Sync failed: {result.get('errors')}"

    # ----- days(ts) -------------------------------------------------------
    events = assert_dataset_exists(
        argus_client, _urn("analytics.events", iceberg_datasource_id),
    )
    cols = assert_property(events, "iceberg.partition_columns")
    transforms = assert_property(events, "iceberg.partition_transforms")
    assert cols == "ts", f"events partition_columns={cols!r}"
    # pyiceberg 의 DayTransform.__str__ 은 ``day``
    assert transforms == "day", f"events partition_transforms={transforms!r}"
    assert_schema_fields(events, [
        {"field_path": "ts", "is_partition_key": "true"},
        {"field_path": "event_id", "is_partition_key": "false"},
        {"field_path": "user_id", "is_partition_key": "false"},
    ])

    # ----- bucket(16, user_id) + identity(region) -------------------------
    bucketed = assert_dataset_exists(
        argus_client, _urn("analytics.bucketed_users", iceberg_datasource_id),
    )
    cols = assert_property(bucketed, "iceberg.partition_columns")
    transforms = assert_property(bucketed, "iceberg.partition_transforms")
    # 동기화 어댑터는 spec 순서를 유지해 ``user_id,region`` 으로 직렬화한다.
    assert cols == "user_id,region", (
        f"bucketed_users partition_columns={cols!r}"
    )
    # BucketTransform.__str__ 은 ``bucket[16]``, IdentityTransform 은 ``identity``
    assert transforms == "bucket[16],identity", (
        f"bucketed_users partition_transforms={transforms!r}"
    )
    assert_schema_fields(bucketed, [
        {"field_path": "user_id", "is_partition_key": "true",
         "nullable": "false"},
        {"field_path": "region", "is_partition_key": "true",
         "nullable": "false"},
        {"field_path": "name", "is_partition_key": "false"},
    ])
