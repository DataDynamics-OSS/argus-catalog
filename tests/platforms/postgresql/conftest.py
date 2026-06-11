# SPDX-License-Identifier: Apache-2.0
"""PostgreSQL 통합 테스트 fixture.

compose 스택을 세션 단위로 한 번 띄우고, 매 테스트마다 깨끗한 schema/테이블/
샘플 데이터로 다시 채운 뒤 catalog 의 잔여 데이터셋도 정리한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from lib.compose import ComposeStack, wait_for_http
from lib.argus_client import ArgusClient

# 호스트에서 본 PG 좌표. compose 의 15432 → 컨테이너 5432.
PG_HOST = os.environ.get("ARGUS_TEST_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("ARGUS_TEST_PG_PORT", "15432"))
PG_USER = os.environ.get("ARGUS_TEST_PG_USER", "argus_test")
PG_PASSWORD = os.environ.get("ARGUS_TEST_PG_PASSWORD", "argus_test")
PG_DATABASE = os.environ.get("ARGUS_TEST_PG_DATABASE", "northwind")

# metadata-sync 가 PG 에 접속할 때의 호스트/포트. sync 컨테이너 안에서 호스트와 다를 수 있어
# 별도 변수로. 기본은 호스트와 동일.
SYNC_PG_HOST = os.environ.get("ARGUS_TEST_PG_SYNC_HOST", PG_HOST)
SYNC_PG_PORT = int(os.environ.get("ARGUS_TEST_PG_SYNC_PORT", str(PG_PORT)))

# 카탈로그에 등록된 PostgreSQL 데이터소스를 재사용한다. datasource_id 는
# ``pg_datasource_id`` fixture 가 카탈로그에서 해석(없으면 자동 등록)한다.
# ARGUS_TEST_PG_DATASOURCE_ID 로 강제 지정 가능.


@pytest.fixture(scope="session")
def postgres_stack() -> ComposeStack:
    """PG 컨테이너를 세션 단위로 기동."""
    compose_file = Path(__file__).parent / "compose.yml"
    stack = ComposeStack(compose_file, project_name="argus-test-postgresql")

    keep_running = os.environ.get("ARGUS_TEST_KEEP_STACK") == "1"
    if not keep_running:
        stack.up(wait=True)

    # northwind initdb (수백 KB) 적재까지 시간이 더 걸려 polling 을 길게.
    # psycopg 가 없을 수 있어 sqlalchemy 로 연결 시도.
    import time
    deadline = time.monotonic() + 180
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            eng = create_engine(_pg_url(), pool_pre_ping=True)
            with eng.connect():
                pass
            eng.dispose()
            break
        except Exception as e:
            last_err = e
            time.sleep(1)
    else:
        raise TimeoutError(f"PG not ready: {last_err}")

    yield stack

    if not keep_running:
        stack.down(volumes=True)


def _pg_url() -> str:
    return f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"


@pytest.fixture(scope="session")
def postgres_engine(postgres_stack) -> Engine:
    """SQLAlchemy 엔진 — seed 스크립트가 schema/테이블/데이터를 채울 때 사용."""
    engine = create_engine(_pg_url(), pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def pg_datasource_id(argus_client: ArgusClient) -> str:
    """카탈로그에 등록된 PostgreSQL 데이터소스 id 해석(없으면 자동 등록)."""
    override = os.environ.get("ARGUS_TEST_PG_DATASOURCE_ID")
    if override:
        return override
    return argus_client.ensure_datasource("postgresql", name="Test PostgreSQL")


@pytest.fixture
def cleanup_postgresql_datasets(argus_client: ArgusClient, pg_datasource_id: str):
    """매 테스트 시작/종료 시 ``postgresql`` 플랫폼의 모든 dataset 을 정리.

    catalog DB 가 다른 테스트의 잔여 행으로 오염되지 않도록 setup/teardown 모두에서.
    ``ARGUS_TEST_PRESERVE_DATASETS=1`` 이면 양쪽 모두 skip — 테스트 실행 결과를
    UI 등에서 직접 확인하고 싶을 때 사용.
    """
    preserve = os.environ.get("ARGUS_TEST_PRESERVE_DATASETS") == "1"
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(pg_datasource_id)
        except Exception:
            pass
    yield
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(pg_datasource_id)
        except Exception:
            pass


@pytest.fixture
def configure_sync(sync_client, pg_datasource_id):
    """metadata-sync 의 PostgreSQL 연결 설정을 테스트 컨테이너 좌표로 강제 동기화."""
    sync_client.session.put(
        f"{sync_client.base_url}/sync/postgresql/connection",
        json={
            "datasource_id": pg_datasource_id,
            "host": SYNC_PG_HOST,
            "port": SYNC_PG_PORT,
            "database": PG_DATABASE,
            "username": PG_USER,
            "password": PG_PASSWORD,
            "databases": [],
            "exclude_databases": ["template0", "template1", "postgres"],
            "schemas": [],
            "exclude_schemas": ["pg_catalog", "information_schema", "pg_toast"],
            "origin": "DEV",
        },
        timeout=sync_client.timeout,
    ).raise_for_status()
    return SYNC_PG_HOST
