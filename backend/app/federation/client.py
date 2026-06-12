# SPDX-License-Identifier: Apache-2.0
"""peer Argus Catalog 호출용 HTTP 클라이언트.

각 peer 의 페더레이션 export API(``/api/v1/federation/export/*``)를 호출한다.
타임아웃·인증 헤더를 캡슐화하며, 네트워크 오류는 호출자가 degrade 할 수 있도록
예외를 그대로 전파한다(circuit breaker 는 후속 단계).
"""

import logging
import time

import httpx

from app.federation.models import FederatedInstance
from app.federation.schemas import (
    CapabilitiesResponse,
    FederatedExportDatasetsResponse,
    FederatedExportLineageResponse,
    FederatedExportSearchResponse,
    InstanceHealth,
)

logger = logging.getLogger(__name__)

# peer 1곳에 대한 단일 요청 타임아웃(초). 느린 peer 가 전체 연합 검색을 막지 않도록 짧게.
DEFAULT_TIMEOUT_SEC = 5.0


def _headers(instance: FederatedInstance) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if instance.auth_token:
        headers["Authorization"] = f"Bearer {instance.auth_token}"
    return headers


def _base(instance: FederatedInstance) -> str:
    return instance.base_url.rstrip("/")


async def search_peer(
    instance: FederatedInstance,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> FederatedExportSearchResponse:
    """peer 의 export 검색 API 를 호출해 결과를 반환한다.

    네트워크/HTTP 오류는 그대로 raise — 호출자(service)가 instances_failed 로 집계한다.
    """
    url = f"{_base(instance)}/api/v1/federation/export/search"
    params = {"q": query, "limit": limit, "threshold": threshold}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, params=params, headers=_headers(instance))
        resp.raise_for_status()
        return FederatedExportSearchResponse.model_validate(resp.json())


async def fetch_export_datasets(
    instance: FederatedInstance,
    limit: int = 200,
    offset: int = 0,
    updated_after=None,
    timeout: float = 30.0,
) -> FederatedExportDatasetsResponse:
    """peer 의 export 데이터셋 목록을 한 페이지 가져온다(HARVEST 용).

    가져오기는 백그라운드 작업이라 검색보다 넉넉한 타임아웃을 둔다.
    ``updated_after`` (datetime) 가 주어지면 증분 가져오기용 쿼리 파라미터로 전달한다.
    """
    url = f"{_base(instance)}/api/v1/federation/export/datasets"
    params: dict = {"limit": limit, "offset": offset}
    if updated_after is not None:
        params["updated_after"] = updated_after.isoformat()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, params=params, headers=_headers(instance))
        resp.raise_for_status()
        return FederatedExportDatasetsResponse.model_validate(resp.json())


async def fetch_export_lineage(
    instance: FederatedInstance,
    timeout: float = 30.0,
) -> FederatedExportLineageResponse:
    """peer 의 리니지 엣지(URN→URN) 전체를 가져온다(cross-instance stitching 용)."""
    url = f"{_base(instance)}/api/v1/federation/export/lineage"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=_headers(instance))
        resp.raise_for_status()
        return FederatedExportLineageResponse.model_validate(resp.json())


async def fetch_export_dataset(
    instance: FederatedInstance,
    urn: str,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """peer 의 단일 데이터셋 상세 메타데이터(드릴다운)를 가져온다."""
    url = f"{_base(instance)}/api/v1/federation/export/dataset"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, params={"urn": urn}, headers=_headers(instance))
        resp.raise_for_status()
        return resp.json()


async def fetch_export_sample(
    instance: FederatedInstance,
    urn: str,
    limit: int = 100,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """peer 의 데이터셋 샘플 데이터(드릴다운)를 가져온다."""
    url = f"{_base(instance)}/api/v1/federation/export/dataset/sample"
    params = {"urn": urn, "limit": limit}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, params=params, headers=_headers(instance))
        resp.raise_for_status()
        return resp.json()


async def fetch_export_capabilities(
    instance: FederatedInstance,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> CapabilitiesResponse:
    """peer 가 advertise 하는 노출 정보 항목(capabilities)을 가져온다.

    ``instance`` 는 영속화 전 임시 객체여도 된다(base_url/auth_token 만 사용).
    """
    url = f"{_base(instance)}/api/v1/federation/export/capabilities"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=_headers(instance))
        resp.raise_for_status()
        return CapabilitiesResponse.model_validate(resp.json())


async def check_health(
    instance: FederatedInstance,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> InstanceHealth:
    """peer 의 ``/health`` 를 호출해 도달성/버전/지연을 점검한다(예외를 던지지 않음)."""
    url = f"{_base(instance)}/health"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=_headers(instance))
        latency_ms = int((time.monotonic() - started) * 1000)
        version = None
        try:
            version = resp.json().get("version")
        except Exception:  # noqa: BLE001 — 비정상 응답이어도 도달성 점검은 계속
            pass
        return InstanceHealth(
            instance_key=instance.instance_key,
            reachable=resp.status_code == 200,
            status_code=resp.status_code,
            version=version,
            latency_ms=latency_ms,
        )
    except httpx.HTTPError as e:
        # 도달 실패는 예외를 던지지 않고 reachable=False 로 보고하되,
        # 운영 진단을 위해 사유를 한 줄 남긴다(상태점검 UI 에도 같은 사유가 노출됨).
        logger.warning(
            "peer 상태점검 실패 [%s] %s: %s", instance.instance_key, url, e
        )
        return InstanceHealth(
            instance_key=instance.instance_key,
            reachable=False,
            error=str(e),
        )
