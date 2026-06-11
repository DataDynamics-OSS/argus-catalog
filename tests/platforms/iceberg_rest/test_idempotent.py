# SPDX-License-Identifier: Apache-2.0
"""동일 데이터로 sync 를 두 번 실행했을 때 created=0, updated>=N 이어야 한다."""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.sync_client import SyncClient
from .seed.tables import drop_all, seed_all


@pytest.mark.iceberg
def test_repeat_sync_is_idempotent(
    iceberg_catalog,
    iceberg_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_iceberg_datasets,
):
    drop_all(iceberg_catalog)
    seed_all(iceberg_catalog)

    first = sync_client.run_now(iceberg_datasource_id)
    assert first["success"], f"First sync failed: {first.get('errors')}"
    assert first["created"] >= 4, (
        f"First sync should create 4 datasets, got created={first['created']}"
    )

    second = sync_client.run_now(iceberg_datasource_id)
    assert second["success"], f"Second sync failed: {second.get('errors')}"
    assert second["created"] == 0, (
        f"Second sync must not create rows, got created={second['created']}"
    )
    assert second["updated"] >= first["created"], (
        f"Second sync should re-touch all existing rows, "
        f"first.created={first['created']} second.updated={second['updated']}"
    )

    # 카탈로그 측 총 데이터셋 수가 1차 sync 후와 동일해야 함 (중복 생성 없음)
    listing = argus_client.list_datasets(datasource=iceberg_datasource_id, page=1, page_size=100)
    assert listing["total"] >= first["created"]
    # iceberg-rest 전용 — total 이 first.created 와 정확히 일치하는지도 검증
    # (다른 테스트가 cleanup_iceberg_datasets 로 정리되므로 시점상 격리 가능)
    assert listing["total"] == first["created"], (
        f"Expected total={first['created']} but got {listing['total']}; "
        f"items={[i['name'] for i in listing.get('items', [])]}"
    )
