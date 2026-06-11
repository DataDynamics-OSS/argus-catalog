# SPDX-License-Identifier: Apache-2.0
"""동일 데이터로 두 번 sync — created=0, total 행 수 동일."""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.sync_client import SyncClient
from .seed.tables import drop_all, seed_all


@pytest.mark.postgresql
def test_repeat_sync_is_idempotent(
    postgres_engine,
    pg_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_postgresql_datasets,
):
    drop_all(postgres_engine)
    seed_all(postgres_engine)

    first = sync_client.run_now(pg_datasource_id)
    assert first["success"], f"First sync failed: {first.get('errors')}"
    # northwind 는 14 base tables. 하한만 검증.
    assert first["created"] >= 14, (
        f"First sync should create >=14 datasets, got {first['created']}"
    )

    second = sync_client.run_now(pg_datasource_id)
    assert second["success"], f"Second sync failed: {second.get('errors')}"
    assert second["created"] == 0, (
        f"Second sync must not create rows, got {second['created']}"
    )
    assert second["updated"] >= first["created"]

    listing = argus_client.list_datasets(datasource=pg_datasource_id, page=1, page_size=100)
    assert listing["total"] == first["created"], (
        f"Expected total={first['created']} but got {listing['total']}"
    )
