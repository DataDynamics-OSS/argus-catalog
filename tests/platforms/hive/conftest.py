# SPDX-License-Identifier: Apache-2.0
"""Hive Metastore 통합 테스트 fixture.

HMS 는 Thrift 프로토콜을 사용하므로 ``hmsclient`` 로 직접 통신. compose 의
19083 → HMS 의 Thrift 포트 9083.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest

from lib.compose import ComposeStack
from lib.argus_client import ArgusClient

HMS_HOST = os.environ.get("ARGUS_TEST_HMS_HOST", "localhost")
HMS_PORT = int(os.environ.get("ARGUS_TEST_HMS_PORT", "19083"))

SYNC_HMS_HOST = os.environ.get("ARGUS_TEST_HMS_SYNC_HOST", HMS_HOST)
SYNC_HMS_PORT = int(os.environ.get("ARGUS_TEST_HMS_SYNC_PORT", str(HMS_PORT)))

# 카탈로그에 등록된 Hive 데이터소스를 재사용한다. datasource_id 는
# ``hive_datasource_id`` fixture 가 카탈로그에서 해석(없으면 자동 등록)한다.
# ARGUS_TEST_HIVE_DATASOURCE_ID 로 강제 지정 가능.


def _wait_for_port(host: str, port: int, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                return
        except OSError as e:
            last_err = e
            time.sleep(2)
    raise TimeoutError(f"{host}:{port} not reachable: {last_err}")


@pytest.fixture(scope="session")
def hive_stack() -> ComposeStack:
    compose_file = Path(__file__).parent / "compose.yml"
    stack = ComposeStack(compose_file, project_name="argus-test-hive")
    keep_running = os.environ.get("ARGUS_TEST_KEEP_STACK") == "1"
    if not keep_running:
        stack.up(wait=True)

    _wait_for_port(HMS_HOST, HMS_PORT)
    yield stack

    if not keep_running:
        stack.down(volumes=True)


@pytest.fixture(scope="session")
def hms_client(hive_stack):
    """HMS Thrift 클라이언트. seed 가 사용."""
    from hmsclient import hmsclient

    client = hmsclient.HMSClient(host=HMS_HOST, port=HMS_PORT)
    client.open()
    yield client
    try:
        client.close()
    except Exception:
        pass


@pytest.fixture(scope="session")
def hive_datasource_id(argus_client: ArgusClient) -> str:
    """카탈로그에 등록된 Hive 데이터소스 id 해석(없으면 자동 등록)."""
    override = os.environ.get("ARGUS_TEST_HIVE_DATASOURCE_ID")
    if override:
        return override
    return argus_client.ensure_datasource("hive", name="Test Hive")


@pytest.fixture
def cleanup_hive_datasets(argus_client: ArgusClient, hive_datasource_id: str):
    """``ARGUS_TEST_PRESERVE_DATASETS=1`` 이면 setup/teardown 양쪽 모두 skip."""
    preserve = os.environ.get("ARGUS_TEST_PRESERVE_DATASETS") == "1"
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(hive_datasource_id)
        except Exception:
            pass
    yield
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(hive_datasource_id)
        except Exception:
            pass


@pytest.fixture
def configure_sync(sync_client, hive_datasource_id):
    """metadata-sync 의 Hive 연결 설정. thrift mode 로 강제."""
    sync_client.session.put(
        f"{sync_client.base_url}/sync/hive/connection",
        json={
            "datasource_id": hive_datasource_id,
            "metastore_host": SYNC_HMS_HOST,
            "metastore_port": SYNC_HMS_PORT,
            "kerberos_enabled": False,
            "kerberos_principal": "",
            "kerberos_keytab": "",
            "databases": [],
            "exclude_databases": ["sys", "information_schema"],
            "origin": "DEV",
        },
        timeout=sync_client.timeout,
    ).raise_for_status()
    return SYNC_HMS_HOST
