"""StarRocks — 두 번 sync 시 created=0."""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.sync_client import SyncClient
from .seed.tables import drop_all, seed_all


@pytest.mark.starrocks
@pytest.mark.slow
def test_repeat_sync_is_idempotent(
    starrocks_engine,
    sr_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_starrocks_datasets,
):
    drop_all(starrocks_engine)
    seed_all(starrocks_engine)

    first = sync_client.run_now(sr_datasource_id)
    assert first["success"], f"First sync failed: {first.get('errors')}"
    assert first["created"] >= 2

    second = sync_client.run_now(sr_datasource_id)
    assert second["success"], f"Second sync failed: {second.get('errors')}"
    assert second["created"] == 0
    assert second["updated"] >= first["created"]
