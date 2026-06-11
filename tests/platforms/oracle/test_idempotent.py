"""동일 데이터로 두 번 sync — created=0, total 행 수 동일."""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.sync_client import SyncClient
from .seed.tables import drop_all, seed_all


@pytest.mark.oracle
def test_repeat_sync_is_idempotent(
    oracle_engine,
    oracle_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_oracle_datasets,
):
    drop_all(oracle_engine)
    seed_all(oracle_engine)

    first = sync_client.run_now(oracle_datasource_id)
    assert first["success"], f"First sync failed: {first.get('errors')}"
    # orders + customers.
    assert first["created"] >= 2

    second = sync_client.run_now(oracle_datasource_id)
    assert second["success"], f"Second sync failed: {second.get('errors')}"
    assert second["created"] == 0
    assert second["updated"] >= first["created"]

    listing = argus_client.list_datasets(datasource=oracle_datasource_id, page=1, page_size=100)
    assert listing["total"] == first["created"]
