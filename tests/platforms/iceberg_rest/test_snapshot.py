# SPDX-License-Identifier: Apache-2.0
"""Iceberg 테이블에 추가 데이터를 append 한 뒤 sync 를 재실행하면
``iceberg.current_snapshot_id`` 가 바뀌고 ``iceberg.last_updated_ms`` 가
증가하는지 검증.
"""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.assertions import assert_dataset_exists, assert_property
from lib.sync_client import SyncClient
from .seed.tables import append_events_extra_day, drop_all, seed_all
from .test_sync import _urn


@pytest.mark.iceberg
def test_snapshot_advances_on_append(
    iceberg_catalog,
    iceberg_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_iceberg_datasets,
):
    drop_all(iceberg_catalog)
    seed_all(iceberg_catalog)

    # 1차 sync — 베이스라인 스냅샷/타임스탬프 기록.
    first = sync_client.run_now(iceberg_datasource_id)
    assert first["success"], f"Initial sync failed: {first.get('errors')}"

    events_before = assert_dataset_exists(
        argus_client, _urn("analytics.events", iceberg_datasource_id),
    )
    snap_before = assert_property(events_before, "iceberg.current_snapshot_id")
    updated_before = int(assert_property(events_before, "iceberg.last_updated_ms"))

    # 2. 새 데이터 append → 새 스냅샷 생성 (Iceberg 쪽 변화)
    new_snap = append_events_extra_day(iceberg_catalog)
    assert new_snap != snap_before, (
        "Iceberg append did not produce a new snapshot — test setup invariant broken"
    )

    # 3. sync 재실행
    second = sync_client.run_now(iceberg_datasource_id)
    assert second["success"], f"Second sync failed: {second.get('errors')}"

    # 4. 카탈로그에서도 갱신이 반영되었는지
    events_after = assert_dataset_exists(
        argus_client, _urn("analytics.events", iceberg_datasource_id),
    )
    snap_after = assert_property(events_after, "iceberg.current_snapshot_id")
    updated_after = int(assert_property(events_after, "iceberg.last_updated_ms"))

    assert snap_after == new_snap, (
        f"Catalog current_snapshot_id={snap_after!r} but Iceberg now reports {new_snap!r}"
    )
    assert updated_after > updated_before, (
        f"last_updated_ms did not advance: before={updated_before}, after={updated_after}"
    )
