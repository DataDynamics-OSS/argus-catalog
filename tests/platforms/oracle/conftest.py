"""Oracle 통합 테스트 fixture — mysql/postgresql 와 동일 패턴.

gvenzl/oracle-free 컨테이너의 PDB(FREEPDB1) 에 APP_USER(=스키마 ARGUS_TEST)를 쓰며,
oracledb 드라이버로 직접 접속해 시드한다. Oracle 은 따옴표 없는 식별자를 대문자로
저장하므로 스키마/테이블/컬럼은 모두 대문자(ARGUS_TEST.ORDERS …)가 된다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from lib.compose import ComposeStack
from lib.argus_client import ArgusClient

# 호스트에서 본 Oracle 좌표. compose 의 11521 → 컨테이너 1521.
ORA_HOST = os.environ.get("ARGUS_TEST_ORACLE_HOST", "localhost")
ORA_PORT = int(os.environ.get("ARGUS_TEST_ORACLE_PORT", "11521"))
ORA_USER = os.environ.get("ARGUS_TEST_ORACLE_USER", "argus_test")
ORA_PASSWORD = os.environ.get("ARGUS_TEST_ORACLE_PASSWORD", "argus_test")
ORA_SERVICE = os.environ.get("ARGUS_TEST_ORACLE_SERVICE", "FREEPDB1")
# Oracle 은 식별자를 대문자로 저장 — 접속 유저 argus_test 의 스키마는 ARGUS_TEST.
ORA_SCHEMA = os.environ.get("ARGUS_TEST_ORACLE_SCHEMA", "ARGUS_TEST")

# metadata-sync 컨테이너에서 본 Oracle 좌표 — 호스트와 다를 수 있어 별도 변수.
SYNC_ORA_HOST = os.environ.get("ARGUS_TEST_ORACLE_SYNC_HOST", ORA_HOST)
SYNC_ORA_PORT = int(os.environ.get("ARGUS_TEST_ORACLE_SYNC_PORT", str(ORA_PORT)))

# 카탈로그에 등록된 Oracle 데이터소스를 재사용한다. datasource_id 는
# ``oracle_datasource_id`` fixture 가 해석(없으면 자동 등록).
# ARGUS_TEST_ORACLE_DATASOURCE_ID 로 강제 지정 가능.


def _url() -> str:
    return (
        f"oracle+oracledb://{ORA_USER}:{ORA_PASSWORD}@{ORA_HOST}:{ORA_PORT}"
        f"/?service_name={ORA_SERVICE}"
    )


@pytest.fixture(scope="session")
def oracle_stack() -> ComposeStack:
    compose_file = Path(__file__).parent / "compose.yml"
    stack = ComposeStack(compose_file, project_name="argus-test-oracle")
    keep_running = os.environ.get("ARGUS_TEST_KEEP_STACK") == "1"
    if not keep_running:
        stack.up(wait=True)

    # Oracle 첫 부팅은 느려(PDB open + APP_USER 생성) polling 을 길게 둔다.
    deadline = time.monotonic() + 300
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            eng = create_engine(_url(), pool_pre_ping=True)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1 FROM DUAL"))
            eng.dispose()
            break
        except Exception as e:
            last_err = e
            time.sleep(2)
    else:
        raise TimeoutError(f"Oracle not ready: {last_err}")

    yield stack

    if not keep_running:
        stack.down(volumes=True)


@pytest.fixture(scope="session")
def oracle_engine(oracle_stack) -> Engine:
    engine = create_engine(_url(), pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def oracle_datasource_id(argus_client: ArgusClient) -> str:
    """카탈로그에 등록된 Oracle 데이터소스 id 해석(없으면 자동 등록)."""
    override = os.environ.get("ARGUS_TEST_ORACLE_DATASOURCE_ID")
    if override:
        return override
    return argus_client.ensure_datasource("oracle", name="Test Oracle")


@pytest.fixture
def cleanup_oracle_datasets(argus_client: ArgusClient, oracle_datasource_id: str):
    """``ARGUS_TEST_PRESERVE_DATASETS=1`` 이면 setup/teardown 양쪽 모두 skip."""
    preserve = os.environ.get("ARGUS_TEST_PRESERVE_DATASETS") == "1"
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(oracle_datasource_id)
        except Exception:
            pass
    yield
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(oracle_datasource_id)
        except Exception:
            pass


@pytest.fixture
def configure_sync(sync_client, oracle_datasource_id):
    """metadata-sync 의 Oracle 연결 설정을 테스트 컨테이너 좌표로 강제 동기화."""
    sync_client.session.put(
        f"{sync_client.base_url}/sync/oracle/connection",
        json={
            "datasource_id": oracle_datasource_id,
            "host": SYNC_ORA_HOST,
            "port": SYNC_ORA_PORT,
            "service_name": ORA_SERVICE,
            "username": ORA_USER,
            "password": ORA_PASSWORD,
            # 앱 스키마만 sync. 시스템 스키마(SYS/SYSTEM …)는 어댑터 기본 제외.
            "schemas": [ORA_SCHEMA],
            "origin": "DEV",
        },
        timeout=sync_client.timeout,
    ).raise_for_status()
    return SYNC_ORA_HOST
