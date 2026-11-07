# SPDX-License-Identifier: Apache-2.0
"""페더레이션 관리/검색 API.

- peer 레지스트리 CRUD (`/federation/instances`)·관측 요약(`/federation/stats`)
- 노출자 정책(`/federation/export-policy`)·peer capabilities 조회
- peer 연결 상태 점검 (`/federation/instances/{id}/health`)
- HARVEST 수동 실행·실행 이력, 미러 둘러보기
- 통합 연합 검색 (`/federation/search`)
- LIVE 드릴다운(상세·샘플·리니지)·로컬 승격(import)

peer 등록/변경 같은 관리 작업은 admin 전용. 조회·검색·드릴다운은 로그인 사용자면 가능
(샘플은 ``datasets.sample-view``, DDL 은 ``datasets.ddl-view`` 기능 권한으로 추가 통제).
"""

import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.core.config import settings
from app.core.database import get_session
from app.federation import capabilities, client, service
from app.federation.harvester import harvest_all, harvest_instance
from app.federation.models import FederatedInstance, FederationSyncRun
from app.federation.registry import _MIRROR_MODES
from app.federation.schemas import (
    CapabilitiesResponse,
    ExportPolicyResponse,
    ExportPolicyUpdate,
    FederatedBrowseResponse,
    FederatedImportRequest,
    FederatedImportResponse,
    FederatedInstanceCreate,
    FederatedInstanceResponse,
    FederatedInstanceUpdate,
    FederatedLineageGraph,
    FederatedSearchResponse,
    FederationStats,
    FederationSyncResult,
    FederationSyncRunResponse,
    InstanceHealth,
    ProbeCapabilitiesRequest,
)
from app.permissions.router import check_feature, require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/federation", tags=["federation"])


# ---------------------------------------------------------------------------
# Peer 레지스트리
# ---------------------------------------------------------------------------

def _url_key(url: str) -> tuple[str, str, str]:
    """URL 비교 키 — scheme/host 대소문자·기본 포트·끝 슬래시 차이를 무시한다."""
    p = urlparse(url.strip())
    scheme = (p.scheme or "").lower()
    host = (p.hostname or "").lower()
    port = p.port or {"http": 80, "https": 443}.get(scheme)
    return scheme, f"{host}:{port}", p.path.rstrip("/")


def _reject_self_registration(base_url: str | None) -> None:
    """자기 자신(이 인스턴스의 공개 base URL)을 peer 로 등록하지 못하게 막는다.

    ``federation.public_base_url`` 이 설정된 경우에만 검사한다(미설정 시 판별 불가 → 생략).
    """
    own = settings.federation_public_base_url
    if not own or not base_url:
        return
    if _url_key(base_url) == _url_key(own):
        logger.warning("자기 자신 peer 등록 거부: base_url=%s", base_url)
        raise HTTPException(
            status_code=422,
            detail="자기 자신(이 인스턴스의 base URL)은 peer 로 등록할 수 없습니다",
        )


@router.get("/instances", response_model=list[FederatedInstanceResponse])
async def list_instances(
    user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """등록된 연합 peer 목록."""
    logger.debug("GET /federation/instances: user=%s", user.username)
    return await service.list_instances(session)


@router.post("/instances", response_model=FederatedInstanceResponse, status_code=201)
async def create_instance(
    req: FederatedInstanceCreate,
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """연합 peer 등록 (admin).

    ``instance_key`` 는 허브 내 전역 유일이어야 한다. 이미 존재하면 409 로 거절한다.
    사전 조회로 일반적인 경우를 막고, 동시 등록 경합은 UNIQUE 제약(IntegrityError)으로
    한 번 더 방어한다.
    """
    _reject_self_registration(req.base_url)
    existing = await service.get_instance_by_key(session, req.instance_key)
    if existing is not None:
        logger.warning("peer 등록 충돌(409): instance_key=%s", req.instance_key)
        raise HTTPException(
            status_code=409,
            detail=f"이미 사용 중인 식별 키입니다: {req.instance_key}",
        )
    try:
        return await service.create_instance(session, req)
    except IntegrityError:
        await session.rollback()
        logger.warning("peer 등록 동시 경합 충돌(409): instance_key=%s", req.instance_key)
        raise HTTPException(
            status_code=409,
            detail=f"이미 사용 중인 식별 키입니다: {req.instance_key}",
        )


@router.put("/instances/{instance_id}", response_model=FederatedInstanceResponse)
async def update_instance(
    instance_id: int,
    req: FederatedInstanceUpdate,
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """연합 peer 수정 (admin)."""
    inst = await service.get_instance(session, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="연합 peer 를 찾을 수 없습니다")
    _reject_self_registration(req.base_url)
    return await service.update_instance(session, inst, req)


@router.delete("/instances/{instance_id}", status_code=204)
async def delete_instance(
    instance_id: int,
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """연합 peer 삭제 (admin)."""
    inst = await service.get_instance(session, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="연합 peer 를 찾을 수 없습니다")
    await service.delete_instance(session, inst)


@router.get("/stats", response_model=FederationStats)
async def federation_stats(
    user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """페더레이션 관측 요약 — peer 별 미러 카운트·최근 동기화·breaker 상태."""
    logger.debug("GET /federation/stats")
    return await service.federation_stats(session)


# ---------------------------------------------------------------------------
# 노출자(provider) 정책 + advertise — 이 인스턴스가 외부에 줄 정보 항목
# ---------------------------------------------------------------------------

@router.get("/export-policy", response_model=ExportPolicyResponse)
async def get_export_policy(
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """이 인스턴스가 외부에 노출하는 정보 항목 정책(전체 레지스트리 + 현재 노출)."""
    exposed = await service.get_exposed_fields(session)
    return ExportPolicyResponse(
        items=capabilities.registry_items(),
        groups=capabilities.groups(),
        exposed=exposed,
    )


@router.put("/export-policy", response_model=ExportPolicyResponse)
async def update_export_policy(
    req: ExportPolicyUpdate,
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """노출 정보 항목 정책을 변경한다 (admin)."""
    logger.info("노출 정책 변경 요청: admin=%s", user.username)
    exposed = await service.set_exposed_fields(session, req.exposed)
    return ExportPolicyResponse(
        items=capabilities.registry_items(),
        groups=capabilities.groups(),
        exposed=exposed,
    )


@router.post("/probe-capabilities", response_model=CapabilitiesResponse)
async def probe_capabilities(
    req: ProbeCapabilitiesRequest,
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """등록 전 peer 의 노출 항목을 사전 조회한다 (admin).

    base_url/auth_token 만으로 임시 호출 — 소비자가 표시할 항목을 고르게 한다.
    """
    transient = FederatedInstance(base_url=req.base_url, auth_token=req.auth_token)
    try:
        return await client.fetch_export_capabilities(transient)
    except Exception as e:  # noqa: BLE001 — peer 호출 오류를 502 로 변환
        logger.warning("peer capabilities 사전 조회 실패: base_url=%s error=%s", req.base_url, e)
        raise HTTPException(status_code=502, detail=f"peer capabilities 조회 실패: {e}")


@router.get("/instances/{instance_id}/capabilities", response_model=CapabilitiesResponse)
async def instance_capabilities(
    instance_id: int,
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """등록된 peer 의 노출 항목을 조회한다(수정 다이얼로그용)."""
    inst = await service.get_instance(session, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="연합 peer 를 찾을 수 없습니다")
    try:
        return await client.fetch_export_capabilities(inst)
    except Exception as e:  # noqa: BLE001
        logger.warning("peer capabilities 조회 실패: instance=%s error=%s", inst.instance_key, e)
        raise HTTPException(status_code=502, detail=f"peer capabilities 조회 실패: {e}")


@router.get("/instances/{instance_id}/health", response_model=InstanceHealth)
async def instance_health(
    instance_id: int,
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """peer 도달성/버전/지연 점검."""
    inst = await service.get_instance(session, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="연합 peer 를 찾을 수 없습니다")
    return await client.check_health(inst)


# ---------------------------------------------------------------------------
# HARVEST — 수동 트리거 + 동기화 이력
# ---------------------------------------------------------------------------

@router.post("/instances/{instance_id}/harvest", response_model=FederationSyncResult)
async def harvest_one(
    instance_id: int,
    user: AdminUser,
    full: bool = Query(False, description="전체 동기화(prune 포함). 기본=증분"),
    session: AsyncSession = Depends(get_session),
):
    """단일 peer 를 즉시 가져온다 (admin). 스케줄과 별개로 수동 실행.

    기본은 증분(watermark 이후 변경분). ``full=true`` 면 전체 재동기화 + prune.
    """
    inst = await service.get_instance(session, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="연합 peer 를 찾을 수 없습니다")
    # 전체 가져오기·스케줄러와 같은 기준 — ACTIVE 이고 미러 모드(HARVEST/HYBRID)인 peer 만.
    if inst.status != "ACTIVE":
        logger.warning(
            "비활성 peer 수동 가져오기 거부(409): instance=%s status=%s",
            inst.instance_key, inst.status,
        )
        raise HTTPException(
            status_code=409,
            detail=f"비활성({inst.status}) 상태의 peer 는 가져올 수 없습니다. 상태를 ACTIVE 로 바꾼 뒤 실행하세요",
        )
    if inst.mode not in _MIRROR_MODES:
        logger.warning(
            "미러 모드 아닌 peer 가져오기 거부(409): instance=%s mode=%s",
            inst.instance_key, inst.mode,
        )
        raise HTTPException(
            status_code=409,
            detail=f"{inst.mode} 모드 peer 는 가져오기(미러) 대상이 아닙니다. HARVEST 또는 HYBRID 모드에서만 실행할 수 있습니다",
        )
    logger.info(
        "수동 가져오기 요청 [%s] full=%s (admin=%s)",
        inst.instance_key, full, user.username,
    )
    result = await harvest_instance(session, inst, full=full)
    if result.status == "SKIPPED":
        logger.warning("수동 가져오기 중복 실행 거부(409): instance=%s", inst.instance_key)
        raise HTTPException(status_code=409, detail=result.error or "이미 가져오기가 진행 중입니다")
    return result


@router.post("/harvest", response_model=list[FederationSyncResult])
async def harvest_all_peers(
    user: AdminUser,
    full: bool = Query(False, description="전체 동기화(prune 포함). 기본=증분"),
    session: AsyncSession = Depends(get_session),
):
    """ACTIVE 미러 모드(HARVEST/HYBRID) peer 전체를 즉시 가져온다 (admin)."""
    logger.info("전체 수동 가져오기 요청 full=%s (admin=%s)", full, user.username)
    return await harvest_all(session, full=full)


@router.get("/instances/{instance_id}/sync-runs",
            response_model=list[FederationSyncRunResponse])
async def list_sync_runs(
    instance_id: int,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """peer 의 최근 HARVEST 실행 이력."""
    logger.debug("GET sync-runs: instance_id=%d limit=%d", instance_id, limit)
    rows = (await session.execute(
        select(FederationSyncRun)
        .where(FederationSyncRun.instance_id == instance_id)
        .order_by(FederationSyncRun.started_at.desc())
        .limit(limit)
    )).scalars().all()
    return [FederationSyncRunResponse.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# 인스턴스별 미러 데이터셋 탐색(browse) — 분류체계 트리
# ---------------------------------------------------------------------------

@router.get("/instances/{instance_id}/datasets",
            response_model=FederatedBrowseResponse)
async def browse_instance_datasets(
    instance_id: int,
    user: CurrentUser,
    q: str | None = Query(None, max_length=200, description="이름/설명 부분일치 필터(선택)"),
    session: AsyncSession = Depends(get_session),
):
    """단일 peer 의 HARVEST 미러 데이터셋을 데이터소스별로 묶어 반환한다.

    검색어 없이 인스턴스 → 데이터소스 → 데이터셋 계층으로 둘러보는 용도.
    """
    inst = await service.get_instance(session, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="연합 peer 를 찾을 수 없습니다")
    if inst.status != "ACTIVE":
        # 비활성 peer 는 드릴다운이 막히므로 탐색에서도 제외한다(목록→상세 불일치 방지).
        raise HTTPException(status_code=409, detail="비활성 상태의 peer 입니다")
    return await service.browse_instance_datasets(session, inst, q=q)


# ---------------------------------------------------------------------------
# 연합 검색
# ---------------------------------------------------------------------------

@router.get("/search", response_model=FederatedSearchResponse)
async def federated_search(
    user: CurrentUser,
    q: str = Query(..., min_length=1, max_length=500, description="검색어"),
    limit: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.3, ge=0.0, le=1.0),
    include_local: bool = Query(True, description="이 허브(로컬) 결과 포함 여부"),
    session: AsyncSession = Depends(get_session),
):
    """로컬 + 모든 ACTIVE peer 를 fan-out 한 통합 검색."""
    logger.info("GET /federation/search: q='%s'", q)
    return await service.federated_search(
        session, q, limit=limit, threshold=threshold, include_local=include_local
    )


# ---------------------------------------------------------------------------
# LIVE drill-down — federated URN 의 상세/샘플을 원 인스턴스에서 프록시
# ---------------------------------------------------------------------------

def _drilldown_error(urn: str, e: Exception) -> HTTPException:
    """peer 호출 오류를 적절한 HTTP 상태로 변환한다.

    circuit open → 503, peer 404 → 404(peer 사유 보존), peer 401/403 → 502(토큰 문제),
    그 밖의 오류 → 502.
    """
    from app.federation.breaker import CircuitOpenError

    logger.warning("peer 드릴다운 실패: urn=%s error=%s: %s", urn, type(e).__name__, e)

    if isinstance(e, CircuitOpenError):
        return HTTPException(status_code=503, detail="peer 일시 차단됨(circuit open) — 잠시 후 재시도")
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            # peer 가 준 실제 사유(예: "사용 가능한 샘플 데이터가 없습니다")를 보존한다.
            # 사유를 못 읽으면 일반 메시지로 폴백.
            peer_detail = None
            try:
                peer_detail = e.response.json().get("detail")
            except Exception:  # noqa: BLE001 — 비정상 응답이어도 폴백 메시지 사용
                pass
            return HTTPException(
                status_code=404,
                detail=peer_detail or f"데이터셋을 찾을 수 없습니다: {urn}",
            )
        if code in (401, 403):
            return HTTPException(status_code=502, detail="peer 인증 실패(서비스 토큰 확인)")
    return HTTPException(status_code=502, detail=f"peer 드릴다운 실패: {e}")


@router.get("/datasets/detail")
async def federated_dataset_detail(
    user: CurrentUser,
    urn: str = Query(..., description="federated URN ({instance_key}::{remote_urn})"),
    session: AsyncSession = Depends(get_session),
):
    """federated URN 의 전체 메타데이터를 원 인스턴스에서 실시간 조회한다."""
    try:
        result = await service.federated_dataset_detail(session, urn)
    except Exception as e:  # noqa: BLE001 — peer 호출 오류를 502/404 로 변환
        raise _drilldown_error(urn, e)
    if result is None:
        raise HTTPException(status_code=404, detail=f"인스턴스를 해석할 수 없습니다: {urn}")
    # DDL 은 로컬 상세와 같은 기능 권한(datasets.ddl-view)으로 통제한다.
    metadata = result.get("metadata")
    if isinstance(metadata, dict) and "ddl" in metadata:
        if not await check_feature(session, user, "datasets.ddl-view"):
            logger.debug("DDL 열람 권한 없음 — ddl 제거: user=%s", user.username)
            metadata.pop("ddl", None)
    return result


@router.get("/datasets/sample")
async def federated_dataset_sample(
    user: CurrentUser,
    urn: str = Query(..., description="federated URN ({instance_key}::{remote_urn})"),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    # 실데이터 노출 — 로컬 샘플과 동일한 기능 권한(datasets.sample-view)으로 통제
    _perm=Depends(require_feature("datasets.sample-view")),
):
    """federated URN 의 샘플 데이터를 원 인스턴스에서 실시간 조회한다."""
    try:
        result = await service.federated_dataset_sample(session, urn, limit=limit)
    except service.SampleNotDisplayedError:
        logger.warning("샘플 비표시 peer 의 샘플 요청 거부(403): urn=%s user=%s", urn, user.username)
        raise HTTPException(status_code=403, detail="이 peer 의 샘플은 표시하지 않도록 설정되어 있습니다")
    except Exception as e:  # noqa: BLE001
        raise _drilldown_error(urn, e)
    if result is None:
        raise HTTPException(status_code=404, detail=f"인스턴스를 해석할 수 없습니다: {urn}")
    return result


@router.post("/datasets/import", response_model=FederatedImportResponse, status_code=201)
async def import_federated_dataset(
    req: FederatedImportRequest,
    user: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """페더레이션(미러) 데이터셋을 로컬 카탈로그로 승격(import)한다 (admin).

    peer 드릴다운으로 전체 메타데이터를 받아 전용 'Federation Imports' 데이터소스에
    1급 데이터셋으로 생성한다(1회 스냅샷). 이미 가져왔으면 409.
    """
    try:
        result = await service.import_federated_dataset(
            session, req.federated_urn, created_by=user.username,
        )
    except service.AlreadyImportedError as e:
        # 거부 사실은 importer 가 WARNING 으로 남긴다.
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        # peer 메타데이터가 로컬 스키마 검증을 통과하지 못함(형식 오류) — 중복(409)과 구분
        logger.warning("import 메타데이터 검증 실패 [%s]: %s", req.federated_urn, e)
        raise HTTPException(
            status_code=502, detail="peer 메타데이터 형식이 로컬 카탈로그와 맞지 않아 가져올 수 없습니다",
        )
    except IntegrityError:
        await session.rollback()
        logger.warning("import 충돌(409) — 같은 URN 로컬 데이터셋 존재: urn=%s", req.federated_urn)
        raise HTTPException(
            status_code=409, detail="로컬에 같은 URN 의 데이터셋이 이미 있어 가져올 수 없습니다",
        )
    except Exception as e:  # noqa: BLE001 — peer 호출 오류를 502/404 로 변환
        raise _drilldown_error(req.federated_urn, e)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"인스턴스를 해석할 수 없습니다: {req.federated_urn}",
        )
    return result


@router.get("/datasets/lineage", response_model=FederatedLineageGraph)
async def federated_dataset_lineage(
    user: CurrentUser,
    urn: str = Query(..., description="데이터셋 URN(로컬 또는 federated)"),
    depth: int = Query(1, ge=1, le=5, description="BFS 탐색 깊이"),
    session: AsyncSession = Depends(get_session),
):
    """로컬 + 미러 리니지를 URN 매칭으로 stitch 한 cross-instance 그래프를 반환한다."""
    logger.info("GET /federation/datasets/lineage: urn=%s depth=%d", urn, depth)
    return await service.build_lineage_graph(session, urn, depth=depth)
