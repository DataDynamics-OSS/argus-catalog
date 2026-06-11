# SPDX-License-Identifier: Apache-2.0
"""Kudu 통합 테스트 fixture.

``kudu-python`` 은 native 의존성이라 venv 에 설치 안 될 수도 있다. import 실패
시 pytest 전체를 skip 한다.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest

from lib.compose import ComposeStack
from lib.argus_client import ArgusClient

KUDU_MASTER = os.environ.get("ARGUS_TEST_KUDU_MASTER", "localhost:17051")
SYNC_KUDU_MASTER = os.environ.get("ARGUS_TEST_KUDU_SYNC_MASTER", KUDU_MASTER)

# 카탈로그에 등록된 Kudu 데이터소스를 재사용한다. datasource_id 는
# ``kudu_datasource_id`` fixture 가 카탈로그에서 해석(없으면 자동 등록)한다.
# ARGUS_TEST_KUDU_DATASOURCE_ID 로 강제 지정 가능.


# ---------------------------------------------------------------------------
# kudu-python 의존성이 없으면 전체 디렉터리 테스트를 skip.
# ---------------------------------------------------------------------------
kudu = pytest.importorskip(
    "kudu",
    reason="kudu-python 이 설치되어 있지 않습니다 — pip install kudu-python (libkudu_client 필요)",
)


def _parse_master(addr: str) -> tuple[str, int]:
    host, port = addr.split(":", 1)
    return host, int(port)


def _wait_for_port(host: str, port: int, timeout: float = 120.0) -> None:
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
def kudu_stack() -> ComposeStack:
    compose_file = Path(__file__).parent / "compose.yml"
    stack = ComposeStack(compose_file, project_name="argus-test-kudu")
    keep_running = os.environ.get("ARGUS_TEST_KEEP_STACK") == "1"
    if not keep_running:
        stack.up(wait=True)

    host, port = _parse_master(KUDU_MASTER)
    _wait_for_port(host, port)
    yield stack

    if not keep_running:
        stack.down(volumes=True)


@pytest.fixture(scope="session")
def kudu_client(kudu_stack):
    """Kudu Python 클라이언트. seed 가 사용."""
    host, port = _parse_master(KUDU_MASTER)
    client = kudu.connect(host=host, port=port)
    yield client


@pytest.fixture(scope="session")
def kudu_datasource_id(argus_client: ArgusClient) -> str:
    """카탈로그에 등록된 Kudu 데이터소스 id 해석(없으면 자동 등록)."""
    override = os.environ.get("ARGUS_TEST_KUDU_DATASOURCE_ID")
    if override:
        return override
    return argus_client.ensure_datasource("kudu", name="Test Kudu")


@pytest.fixture
def cleanup_kudu_datasets(argus_client: ArgusClient, kudu_datasource_id: str):
    """``ARGUS_TEST_PRESERVE_DATASETS=1`` 이면 setup/teardown 양쪽 모두 skip."""
    preserve = os.environ.get("ARGUS_TEST_PRESERVE_DATASETS") == "1"
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(kudu_datasource_id)
        except Exception:
            pass
    yield
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(kudu_datasource_id)
        except Exception:
            pass


@pytest.fixture
def configure_sync(sync_client, kudu_datasource_id):
    """metadata-sync 의 Kudu master_addresses 를 테스트 컨테이너 좌표로 강제."""
    sync_client.session.put(
        f"{sync_client.base_url}/sync/kudu/connection",
        json={
            "datasource_id": kudu_datasource_id,
            "master_addresses": SYNC_KUDU_MASTER,
            "table_filter": "",
            "default_database": "default",
            "parse_impala_naming": True,
            "origin": "DEV",
            "kerberos_enabled": False,
            "kerberos_principal": "",
            "kerberos_keytab": "",
            "sasl_protocol_name": "kudu",
            "require_authentication": False,
            "encryption_policy": "optional",
            "trusted_certificates": [],
        },
        timeout=sync_client.timeout,
    ).raise_for_status()
    return SYNC_KUDU_MASTER
