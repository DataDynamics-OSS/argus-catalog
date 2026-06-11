# SPDX-License-Identifier: Apache-2.0
"""Hive — 두 번 sync 시 created=0."""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.sync_client import SyncClient
from .seed.tables import drop_all, seed_all


@pytest.mark.hive
@pytest.mark.slow
def test_repeat_sync_is_idempotent(
    hms_client,
    hive_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_hive_datasets,
):
    drop_all(hms_client)
    seed_all(hms_client)

    first = sync_client.run_now(hive_datasource_id)
    assert first["success"], f"First sync failed: {first.get('errors')}"
    assert first["created"] >= 2

    second = sync_client.run_now(hive_datasource_id)
    assert second["success"], f"Second sync failed: {second.get('errors')}"
    assert second["created"] == 0
    assert second["updated"] >= first["created"]
