# SPDX-License-Identifier: Apache-2.0
"""동일 데이터로 두 번 sync — created=0."""

from __future__ import annotations

import pytest

from lib.argus_client import ArgusClient
from lib.sync_client import SyncClient
from .seed.tables import drop_all, seed_all


@pytest.mark.mysql
def test_repeat_sync_is_idempotent(
    mysql_engine,
    mysql_datasource_id,
    configure_sync,
    sync_client: SyncClient,
    argus_client: ArgusClient,
    cleanup_mysql_datasets,
):
    drop_all(mysql_engine)
    seed_all(mysql_engine)

    first = sync_client.run_now(mysql_datasource_id)
    assert first["success"], f"First sync failed: {first.get('errors')}"
    # sakila base tables 16 + 어댑터가 view 까지 잡으면 더 많을 수 있음.
    assert first["created"] >= 16

    second = sync_client.run_now(mysql_datasource_id)
    assert second["success"], f"Second sync failed: {second.get('errors')}"
    assert second["created"] == 0
    assert second["updated"] >= first["created"]

    listing = argus_client.list_datasets(datasource=mysql_datasource_id, page=1, page_size=100)
    assert listing["total"] == first["created"]
