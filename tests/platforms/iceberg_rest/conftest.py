"""Iceberg REST Catalog 테스트용 fixture.

compose 스택을 세션 단위로 한 번 띄우고, 매 테스트마다 깨끗한 namespace 와
샘플 데이터로 다시 채운 뒤 catalog 의 잔여 데이터셋도 정리한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib.compose import ComposeStack, wait_for_http
from lib.argus_client import ArgusClient

# 카탈로그에 등록된 Iceberg REST 데이터소스를 재사용한다. datasource_id 는
# ``iceberg_datasource_id`` fixture 가 카탈로그에서 해석(없으면 자동 등록)한다.
# ARGUS_TEST_ICEBERG_DATASOURCE_ID 로 강제 지정 가능.

# 호스트에서 본 카탈로그 URI. compose 의 18181 → 컨테이너의 8181.
HOST_CATALOG_URI = os.environ.get(
    "ARGUS_TEST_ICEBERG_URI", "http://localhost:18181/"
)
# 호스트에서 본 MinIO S3 endpoint. 19000 → 9000.
HOST_S3_ENDPOINT = os.environ.get(
    "ARGUS_TEST_ICEBERG_S3_ENDPOINT", "http://localhost:19000"
)
# argus-catalog-metadata-sync 컨테이너/호스트 어디서 보냐에 따라 카탈로그 URI 가
# 달라질 수 있어 별도 변수로 분리 — 기본은 호스트와 동일.
SYNC_CATALOG_URI = os.environ.get(
    "ARGUS_TEST_ICEBERG_SYNC_URI", HOST_CATALOG_URI
)
SYNC_S3_ENDPOINT = os.environ.get(
    "ARGUS_TEST_ICEBERG_SYNC_S3_ENDPOINT", HOST_S3_ENDPOINT
)


@pytest.fixture(scope="session")
def iceberg_stack() -> ComposeStack:
    """Lakekeeper-stand-in (``tabulario/iceberg-rest``) + MinIO 를 세션 단위로 기동."""
    compose_file = Path(__file__).parent / "compose.yml"
    stack = ComposeStack(compose_file, project_name="argus-test-iceberg")

    # ``--wait`` 가 컨테이너 healthcheck 까지 기다리지만, REST Catalog 의 /v1/config 가
    # 200 을 주는지 한 번 더 확인해 진짜 ready 상태에서만 테스트가 시작되도록 한다.
    keep_running = os.environ.get("ARGUS_TEST_KEEP_STACK") == "1"
    if not keep_running:
        stack.up(wait=True)

    wait_for_http(f"{HOST_CATALOG_URI.rstrip('/')}/v1/config", timeout=60.0)
    yield stack

    if not keep_running:
        stack.down(volumes=True)


@pytest.fixture(scope="session")
def iceberg_catalog(iceberg_stack):
    """pyiceberg ``RestCatalog`` 인스턴스. 호스트에서 직접 카탈로그에 접속한다.

    metadata.json 의 S3 path 가 호스트에서도 보이도록 S3 endpoint 를 명시.
    """
    from pyiceberg.catalog.rest import RestCatalog

    catalog = RestCatalog(
        name="argus-test",
        uri=HOST_CATALOG_URI,
        warehouse="s3://warehouse/",
        **{
            "s3.endpoint": HOST_S3_ENDPOINT,
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
            "s3.region": "us-east-1",
            "s3.path-style-access": "true",
        },
    )
    return catalog


@pytest.fixture(scope="session")
def iceberg_datasource_id(argus_client: ArgusClient) -> str:
    """카탈로그에 등록된 Iceberg REST 데이터소스 id 해석(없으면 자동 등록).

    ARGUS_TEST_ICEBERG_DATASOURCE_ID 로 강제 지정 가능. 미지정 시 sync 어댑터의
    datasource_name 인 ``iceberg-rest`` 로 find-or-create 한다. type 검증으로
    실패하면 기존 iceberg 계열 데이터소스를 찾는 폴백을 시도한다.
    """
    override = os.environ.get("ARGUS_TEST_ICEBERG_DATASOURCE_ID")
    if override:
        return override
    try:
        return argus_client.ensure_datasource("iceberg-rest", name="Test Iceberg")
    except Exception:
        existing = argus_client.find_datasource_by_type("iceberg-rest")
        if existing:
            return existing["datasource_id"]
        raise


@pytest.fixture
def cleanup_iceberg_datasets(argus_client: ArgusClient, iceberg_datasource_id: str):
    """매 테스트 시작/종료 시 ``iceberg-rest`` 플랫폼의 모든 dataset 을 정리.

    catalog DB 는 공유 상태라 이전 테스트의 잔여 행 (또는 외부 환경의 사전 데이터)
    이 다음 테스트의 카운트/멱등 검증을 오염시키지 않도록 setup 시점에도 정리.
    ``ARGUS_TEST_PRESERVE_DATASETS=1`` 이면 양쪽 모두 skip.
    """
    preserve = os.environ.get("ARGUS_TEST_PRESERVE_DATASETS") == "1"
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(iceberg_datasource_id)
        except Exception:
            pass
    yield
    if not preserve:
        try:
            argus_client.delete_all_datasets_for(iceberg_datasource_id)
        except Exception:
            pass


@pytest.fixture
def configure_sync(sync_client, iceberg_datasource_id):
    """metadata-sync 의 iceberg-rest 연결 설정을 테스트 컨테이너 좌표로 강제 동기화."""
    sync_client.update_iceberg_rest_connection({
        "uri": SYNC_CATALOG_URI,
        "datasource_id": iceberg_datasource_id,
        "warehouse": "",
        "auth": {"type": "none", "token": "", "credential": "", "scope": ""},
        "namespaces": [],
        "exclude_namespaces": [],
        "properties": {
            "s3.endpoint": SYNC_S3_ENDPOINT,
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
            "s3.region": "us-east-1",
            "s3.path-style-access": "true",
        },
        "origin": "DEV",
    })
    return SYNC_CATALOG_URI
