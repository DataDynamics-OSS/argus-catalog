"""Top-level pytest fixtures shared by all platform test suites.

argus-catalog-server 와 argus-catalog-metadata-sync 는 **호스트에 이미 실행 중**
이라고 가정한다. ``ARGUS_CATALOG_URL`` / ``ARGUS_SYNC_URL`` 환경 변수로 위치를
오버라이드할 수 있다.
"""

from __future__ import annotations

import os
import pytest

from lib.argus_client import ArgusClient
from lib.sync_client import SyncClient


@pytest.fixture(scope="session")
def argus_catalog_url() -> str:
    return os.environ.get("ARGUS_CATALOG_URL", "http://localhost:4600")


@pytest.fixture(scope="session")
def argus_sync_url() -> str:
    return os.environ.get("ARGUS_SYNC_URL", "http://localhost:4610")


@pytest.fixture(scope="session")
def argus_client(argus_catalog_url: str) -> ArgusClient:
    """Argus Catalog Server REST 클라이언트. 세션 전체에서 공유."""
    client = ArgusClient(argus_catalog_url)
    # 헬스 체크 — 호스트에 떠 있지 않으면 즉시 실패해 의미 있는 에러 메시지로 끝낸다.
    if not client.health():
        pytest.exit(
            f"argus-catalog-server is not reachable at {argus_catalog_url}. "
            "Start it on the host before running integration tests "
            "(ARGUS_CATALOG_URL to override)."
        )
    return client


@pytest.fixture(scope="session")
def sync_client(argus_sync_url: str) -> SyncClient:
    """argus-catalog-metadata-sync REST 클라이언트."""
    client = SyncClient(argus_sync_url)
    if not client.health():
        pytest.exit(
            f"argus-catalog-metadata-sync is not reachable at {argus_sync_url}. "
            "Start it on the host before running integration tests "
            "(ARGUS_SYNC_URL to override)."
        )
    return client
