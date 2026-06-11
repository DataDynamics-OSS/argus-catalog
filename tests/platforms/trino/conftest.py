# SPDX-License-Identifier: Apache-2.0
"""Trino 통합 테스트 fixture.

스택은 Trino coordinator + Hive Metastore + MinIO (S3 호환) 의 조합. Trino 는
hive catalog 를 통해 HMS/MinIO 에 접근한다. ``starting=false`` 가 응답에 나타나야
coordinator 가 완전히 기동된 상태로 본다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lib.compose import ComposeStack, wait_for_http
from lib.argus_client import ArgusClient

TR_HOST = os.environ.get("ARGUS_TEST_TRINO_HOST", "localhost")
TR_PORT = int(os.environ.get("ARGUS_TEST_TRINO_PORT", "18080"))
TR_USER = os.environ.get("ARGUS_TEST_TRINO_USER", "argus_test")

SYNC_TR_HOST = os.environ.get("ARGUS_TEST_TRINO_SYNC_HOST", TR_HOST)
SYNC_TR_PORT = int(os.environ.get("ARGUS_TEST_TRINO_SYNC_PORT", str(TR_PORT)))

# 카탈로그에 등록된 Trino 데이터소스를 재사용한다. datasource_id 는
# ``trino_datasource_id`` fixture 가 카탈로그에서 해석(없으면 자동 등록)한다.
# ARGUS_TEST_TRINO_DATASOURCE_ID 로 강제 지정 가능.


@pytest.fixture(scope="session")
def trino_stack() -> ComposeStack:
    compose_file = Path(__file__).parent / "compose.yml"
    stack = ComposeStack(compose_file, project_name="argus-test-trino")
    keep_running = os.environ.get("ARGUS_TEST_KEEP_STACK") == "1"
    if not keep_running:
        stack.up(wait=True)

    # Trino coordinator 가 startup 끝날 때까지 폴링.
    wait_for_http(
        f"http://{TR_HOST}:{TR_PORT}/v1/info",
        timeout=120.0,
        predicate=lambda r: r.status_code == 200 and '"starting":false' in r.text.replace(" ", ""),
    )
    yield stack

    if not keep_running:
        stack.down(volumes=True)


@pytest.fixture(scope="session")
def trino_conn(trino_stack):
    """Trino DB-API 연결."""
    from trino.dbapi import connect

    conn = connect(host=TR_HOST, port=TR_PORT, user=TR_USER, catalog="hive", schema="default")
    yield conn
    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture(scope="session")
def trino_datasource_id(argus_client: ArgusClient) -> str:
    """카탈로그에 등록된 Trino 데이터소스 id 해석(없으면 자동 등록)."""
    override = os.environ.get("ARGUS_TEST_TRINO_DATASOURCE_ID")
    if override:
        return override
    return argus_client.ensure_datasource("trino", name="Test Trino")


@pytest.fixture
def cleanup_trino_datasets(argus_client: ArgusClient, trino_datasource_id: str):
    """``ARGUS_TEST_PRESERVE_DATASETS=1`` 이면 setup/teardown 양쪽 모두 skip."""
    preserve = os.environ.get("ARGUS_TEST_PRESERVE_DATASETS") == "1"
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(trino_datasource_id)
        except Exception:
            pass
    yield
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(trino_datasource_id)
        except Exception:
            pass


@pytest.fixture
def configure_sync(sync_client, trino_datasource_id):
    sync_client.session.put(
        f"{sync_client.base_url}/sync/trino/connection",
        json={
            "datasource_id": trino_datasource_id,
            "host": SYNC_TR_HOST,
            "port": SYNC_TR_PORT,
            "username": TR_USER,
            "password": "",
            "http_scheme": "http",
            "catalogs": ["hive"],
            "exclude_catalogs": ["system", "jmx", "tpch", "tpcds"],
            "exclude_schemas": ["information_schema"],
            "origin": "DEV",
        },
        timeout=sync_client.timeout,
    ).raise_for_status()
    return SYNC_TR_HOST
