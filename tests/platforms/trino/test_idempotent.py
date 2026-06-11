# SPDX-License-Identifier: Apache-2.0
"""Trino — 두 번 sync 시 created=0."""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.sync_client import SyncClient
from .seed.tables import drop_all, seed_all


@pytest.mark.trino
@pytest.mark.slow
def test_repeat_sync_is_idempotent(
    trino_conn,
    trino_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_trino_datasets,
):
    drop_all(trino_conn)
    seed_all(trino_conn)

    first = sync_client.run_now(trino_datasource_id)
    assert first["success"], f"First sync failed: {first.get('errors')}"
    assert first["created"] >= 2

    second = sync_client.run_now(trino_datasource_id)
    assert second["success"], f"Second sync failed: {second.get('errors')}"
    assert second["created"] == 0
    assert second["updated"] >= first["created"]
