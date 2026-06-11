"""StarRocks 통합 테스트 fixture.

StarRocks 는 MySQL wire protocol 을 쓰므로 PyMySQL 로 접속. FE/BE 부팅이 느려
다른 플랫폼보다 긴 대기시간을 둔다. compose 의 19030 → FE 의 query 포트 9030.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from lib.compose import ComposeStack
from lib.argus_client import ArgusClient

SR_HOST = os.environ.get("ARGUS_TEST_SR_HOST", "localhost")
SR_PORT = int(os.environ.get("ARGUS_TEST_SR_PORT", "19030"))
SR_USER = os.environ.get("ARGUS_TEST_SR_USER", "root")
SR_PASSWORD = os.environ.get("ARGUS_TEST_SR_PASSWORD", "")
SR_DATABASE = os.environ.get("ARGUS_TEST_SR_DATABASE", "argus_test")

SYNC_SR_HOST = os.environ.get("ARGUS_TEST_SR_SYNC_HOST", SR_HOST)
SYNC_SR_PORT = int(os.environ.get("ARGUS_TEST_SR_SYNC_PORT", str(SR_PORT)))

# 카탈로그에 등록된 StarRocks 데이터소스를 재사용한다. datasource_id 는
# ``sr_datasource_id`` fixture 가 카탈로그에서 해석(없으면 자동 등록)한다.
# ARGUS_TEST_SR_DATASOURCE_ID 로 강제 지정 가능.


def _url(database: str | None = None) -> str:
    pwd = f":{SR_PASSWORD}" if SR_PASSWORD else ""
    db = f"/{database}" if database else ""
    return f"mysql+pymysql://{SR_USER}{pwd}@{SR_HOST}:{SR_PORT}{db}"


@pytest.fixture(scope="session")
def starrocks_stack() -> ComposeStack:
    compose_file = Path(__file__).parent / "compose.yml"
    stack = ComposeStack(compose_file, project_name="argus-test-starrocks")
    keep_running = os.environ.get("ARGUS_TEST_KEEP_STACK") == "1"
    if not keep_running:
        stack.up(wait=True)

    # StarRocks 부팅이 느려 충분히 대기.
    deadline = time.monotonic() + 180
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            eng = create_engine(_url(), pool_pre_ping=True)
            with eng.connect():
                pass
            eng.dispose()
            break
        except Exception as e:
            last_err = e
            time.sleep(3)
    else:
        raise TimeoutError(f"StarRocks not ready: {last_err}")

    # 테스트용 database 생성
    eng = create_engine(_url())
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {SR_DATABASE}"))
    eng.dispose()

    yield stack

    if not keep_running:
        stack.down(volumes=True)


@pytest.fixture(scope="session")
def starrocks_engine(starrocks_stack) -> Engine:
    engine = create_engine(_url(SR_DATABASE), pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def sr_datasource_id(argus_client: ArgusClient) -> str:
    """카탈로그에 등록된 StarRocks 데이터소스 id 해석(없으면 자동 등록)."""
    override = os.environ.get("ARGUS_TEST_SR_DATASOURCE_ID")
    if override:
        return override
    return argus_client.ensure_datasource("starrocks", name="Test StarRocks")


@pytest.fixture
def cleanup_starrocks_datasets(argus_client: ArgusClient, sr_datasource_id: str):
    """``ARGUS_TEST_PRESERVE_DATASETS=1`` 이면 setup/teardown 양쪽 모두 skip."""
    preserve = os.environ.get("ARGUS_TEST_PRESERVE_DATASETS") == "1"
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(sr_datasource_id)
        except Exception:
            pass
    yield
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(sr_datasource_id)
        except Exception:
            pass


@pytest.fixture
def configure_sync(sync_client, sr_datasource_id):
    sync_client.session.put(
        f"{sync_client.base_url}/sync/starrocks/connection",
        json={
            "datasource_id": sr_datasource_id,
            "host": SYNC_SR_HOST,
            "port": SYNC_SR_PORT,
            "username": SR_USER,
            "password": SR_PASSWORD,
            "databases": [SR_DATABASE],
            "exclude_databases": [
                "information_schema", "_statistics_", "starrocks_monitor",
            ],
            "origin": "DEV",
        },
        timeout=sync_client.timeout,
    ).raise_for_status()
    return SYNC_SR_HOST
