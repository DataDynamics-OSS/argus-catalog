# SPDX-License-Identifier: Apache-2.0
"""MySQL/MariaDB 통합 테스트 fixture — iceberg_rest / postgresql 와 동일 패턴."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from lib.compose import ComposeStack
from lib.argus_client import ArgusClient

# 호스트에서 본 MySQL 좌표. compose 의 13306 → 컨테이너 3306 (로컬 MySQL 과 충돌 회피).
MY_HOST = os.environ.get("ARGUS_TEST_MYSQL_HOST", "localhost")
MY_PORT = int(os.environ.get("ARGUS_TEST_MYSQL_PORT", "13306"))
MY_USER = os.environ.get("ARGUS_TEST_MYSQL_USER", "root")
MY_PASSWORD = os.environ.get("ARGUS_TEST_MYSQL_PASSWORD", "argus_test")
MY_DATABASE = os.environ.get("ARGUS_TEST_MYSQL_DATABASE", "sakila")

# metadata-sync 컨테이너에서 본 MySQL 좌표 — 호스트와 다를 수 있어 별도 변수.
SYNC_MY_HOST = os.environ.get("ARGUS_TEST_MYSQL_SYNC_HOST", MY_HOST)
SYNC_MY_PORT = int(os.environ.get("ARGUS_TEST_MYSQL_SYNC_PORT", str(MY_PORT)))

# 통합 테스트는 카탈로그에 미리 등록된 MySQL 데이터소스(예: "Sakila (MySQL)")를 재사용한다.
# 그 datasource_id 는 ``mysql_datasource_id`` fixture 가 카탈로그에서 동적으로 해석한다.
# (ARGUS_TEST_MYSQL_DATASOURCE_ID 로 강제 지정 가능 — 미지정 시 type=='mysql' 첫 행 사용.)


def _url() -> str:
    return f"mysql+pymysql://{MY_USER}:{MY_PASSWORD}@{MY_HOST}:{MY_PORT}/{MY_DATABASE}"


@pytest.fixture(scope="session")
def mysql_stack() -> ComposeStack:
    compose_file = Path(__file__).parent / "compose.yml"
    stack = ComposeStack(compose_file, project_name="argus-test-mysql")
    keep_running = os.environ.get("ARGUS_TEST_KEEP_STACK") == "1"
    if not keep_running:
        stack.up(wait=True)

    # MySQL 첫 부팅 + sakila initdb 적재(3.2MB) 까지 시간이 더 걸려 polling 을 길게.
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
            time.sleep(1)
    else:
        raise TimeoutError(f"MySQL not ready: {last_err}")

    yield stack

    if not keep_running:
        stack.down(volumes=True)


@pytest.fixture(scope="session")
def mysql_engine(mysql_stack) -> Engine:
    engine = create_engine(_url(), pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def mysql_datasource_id(argus_client: ArgusClient) -> str:
    """카탈로그에 등록된 MySQL 데이터소스의 datasource_id 를 해석한다.

    ARGUS_TEST_MYSQL_DATASOURCE_ID 로 강제 지정 가능. 미지정 시 type=='mysql' 인
    첫 데이터소스를 사용한다. 없으면 테스트 skip.
    """
    override = os.environ.get("ARGUS_TEST_MYSQL_DATASOURCE_ID")
    if override:
        return override
    return argus_client.ensure_datasource("mysql", name="Test MySQL")


@pytest.fixture
def cleanup_mysql_datasets(argus_client: ArgusClient, mysql_datasource_id: str):
    """``ARGUS_TEST_PRESERVE_DATASETS=1`` 이면 setup/teardown 양쪽 모두 skip."""
    preserve = os.environ.get("ARGUS_TEST_PRESERVE_DATASETS") == "1"
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(mysql_datasource_id)
        except Exception:
            pass
    yield
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(mysql_datasource_id)
        except Exception:
            pass


@pytest.fixture
def configure_sync(sync_client, mysql_datasource_id):
    """metadata-sync 의 MySQL 연결 설정을 테스트 컨테이너 좌표로 강제 동기화."""
    sync_client.session.put(
        f"{sync_client.base_url}/sync/mysql/connection",
        json={
            "datasource_id": mysql_datasource_id,
            "host": SYNC_MY_HOST,
            "port": SYNC_MY_PORT,
            "username": MY_USER,
            "password": MY_PASSWORD,
            # sakila 만 sync. mysql 가 자동 생성하는 시스템 DB 는 제외.
            "databases": [MY_DATABASE],
            "exclude_databases": ["information_schema", "mysql", "performance_schema", "sys"],
            "origin": "DEV",
        },
        timeout=sync_client.timeout,
    ).raise_for_status()
    return SYNC_MY_HOST
