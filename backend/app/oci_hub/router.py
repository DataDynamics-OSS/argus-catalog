# SPDX-License-Identifier: Apache-2.0
"""OCI 모델 허브 라우터.

``/api/v1/oci-models`` 경로에서 HuggingFace 스타일의 모델 카탈로그
(README · 태그 · 리니지 · 버전 라이프사이클) 를 제공한다. 모델 아티팩트
실저장은 ``app/models/model_store.py`` 의 S3 백엔드를 공유한다.

로깅 정책: 변경 동작(create/update/delete, README/tag/lineage 변경,
finalize, import) 은 INFO 로 영향을 받은 이름·식별자를 함께 기록한다.
단순 조회(GET) 진입은 DEBUG. 403 / 404 / 충돌 / 외부 임포트 실패는 WARNING(또는 ERROR) 으로 남긴다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, TokenUser
from app.core.database import get_session
from app.models.store_router import _s3_http_error
from app.oci_hub import service
from app.oci_hub.schemas import (
    OCI_ADMIN_ONLY_STATUSES,
    HuggingFaceImportRequest,
    ImportResponse,
    LineageCreate,
    LineageResponse,
    OciFacets,
    OciModelCreate,
    OciModelDetail,
    OciModelUpdate,
    OciModelVersionResponse,
    PaginatedOciModels,
)
from app.permissions.router import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oci-models", tags=["oci-model-hub"])


# ---------------------------------------------------------------------------
# 서버 정보 (임포트 가이드 코드 샘플용)
# ---------------------------------------------------------------------------

@router.get("/server-info")
async def get_server_info(request: Request):
    """가이드 코드 샘플에 넣을 서버 주소 — 요청이 들어온 주소(프록시의 X-Forwarded-* 우선)를 돌려준다.

    컨테이너 내부 IP 는 클라이언트가 접근할 수 없으므로 쓰지 않는다."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host_header = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host_header = host_header.split(",")[0].strip()
    if not host_header:
        host_header = f"{request.url.hostname}:{request.url.port}" if request.url.port else str(request.url.hostname)
    host, _, port = host_header.partition(":")
    default_port = 443 if proto == "https" else 80
    return {
        "base_url": f"{proto}://{host_header}",
        "host": host,
        "port": int(port) if port.isdigit() else default_port,
    }


async def assert_oci_editor(current: TokenUser, session: AsyncSession, name: str):
    """OCI 모델 쓰기 가드 — 관리자, 생성자(created_by), 소유자(owner) 통과. 없으면 404, 아니면 403."""
    model = await service.get_oci_model(session, name)
    if not model:
        logger.warning("OCI 모델 쓰기 대상 없음: %s (요청자 %s)", name, current.username)
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    if current.is_admin or current.username in {model.created_by, model.owner} - {None, ""}:
        return model
    logger.warning("OCI 모델 쓰기 거부: %s (요청자 %s)", name, current.username)
    raise HTTPException(status_code=403, detail="OCI 모델의 소유자 또는 관리자만 수행할 수 있습니다.")


def _assert_status_allowed(current: TokenUser, status: str | None) -> None:
    """승인(approved)·운영(production) 지정은 관리자만 — 거버넌스 승인 절차를 우회하지 못하게."""
    if status in OCI_ADMIN_ONLY_STATUSES and not current.is_admin:
        logger.warning("OCI 상태 지정 거부(관리자 전용 상태): status=%s user=%s", status, current.username)
        raise HTTPException(status_code=403, detail=f"'{status}' 상태 지정은 관리자만 할 수 있습니다.")


@router.get("/stats")
async def get_hub_stats(session: AsyncSession = Depends(get_session)):
    """OCI 모델 허브 대시보드 통계(요약 카드·차트·다운로드/게시 추이)."""
    logger.debug("GET /oci-models/stats")
    return await service.get_hub_stats(session)


@router.get("/facets", response_model=OciFacets)
async def get_facets(session: AsyncSession = Depends(get_session)):
    """목록 필터 선택지(태스크·프레임워크·언어·상태)."""
    return await service.get_facets(session)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("/linked")
async def list_linked_models(
    mlflow_model: str = Query(..., min_length=1, description="MLflow 등록 모델 이름"),
    session: AsyncSession = Depends(get_session),
):
    """MLflow 모델에서 파생된(변환·배포용) OCI 모델 목록 — MLflow 상세의 '배포 아티팩트' 표시용."""
    return await service.list_linked_to_mlflow(session, mlflow_model)


@router.get("", response_model=PaginatedOciModels)
async def list_models(
    search: str | None = Query(None),
    task: str | None = Query(None),
    framework: str | None = Query(None),
    language: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """OCI 모델 목록 페이징 조회(검색어는 이름·표시 이름·설명, 태스크/프레임워크/언어/상태 필터)."""
    logger.debug("GET /oci-models: search=%s, task=%s, framework=%s, page=%d", search, task, framework, page)
    return await service.list_models(session, search=search, task=task, framework=framework,
                                     language=language, status=status, page=page, page_size=page_size)


@router.post("", response_model=OciModelDetail)
async def create_model(current: CurrentUser,
    req: OciModelCreate, _perm=Depends(require_feature("oci.register")), session: AsyncSession = Depends(get_session),
):
    """새 OCI 모델 등록(``oci.register`` 기능 권한 필요). 이름 중복 시 409 — MLflow 모델 이름과도 겹칠 수 없음.

    approved·production 상태로 바로 만드는 것은 관리자만(403)."""
    logger.info("POST /oci-models: name=%s", req.name)
    _assert_status_allowed(current, req.status)
    try:
        return await service.create_model(session, req, created_by=current.username)
    except ValueError as e:
        logger.warning("OCI 모델 생성 충돌: %s", e)
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{name}", response_model=OciModelDetail)
async def get_model(
    name: str, session: AsyncSession = Depends(get_session),
):
    """모델 상세(README · 태그 · 리니지 포함)."""
    logger.debug("GET /oci-models/%s", name)
    detail = await service.get_model_detail(session, name)
    if not detail:
        logger.warning("OCI 모델을 찾을 수 없음: %s", name)
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return detail


@router.patch("/{name}", response_model=OciModelDetail)
async def update_model(current: CurrentUser,
    name: str, req: OciModelUpdate, session: AsyncSession = Depends(get_session),
):
    """모델 메타데이터 부분 갱신(표시 이름·설명·태스크·프레임워크·언어·라이선스·소유자·상태)."""
    logger.info("PATCH /oci-models/%s", name)
    model = await assert_oci_editor(current, session, name)
    # 상태를 바꿀 때만 검사 — 승인된 모델의 다른 필드를 소유자가 고치는 것은 허용.
    if req.status is not None and req.status != model.status:
        _assert_status_allowed(current, req.status)
    result = await service.update_model(session, name, req)
    if not result:
        logger.warning("OCI 모델 갱신 대상 없음: %s", name)
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return result


@router.delete("/{name}")
async def delete_model(current: CurrentUser,
    name: str, session: AsyncSession = Depends(get_session),
):
    """모델과 관련 데이터(버전·태그·리니지·다운로드 기록) 일괄 삭제. S3 파일도 함께 정리.

    가져오는 중인 버전이 있으면 409."""
    logger.info("DELETE /oci-models/%s", name)
    await assert_oci_editor(current, session, name)
    try:
        if not await service.delete_model(session, name):
            raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    except service.OciConflictError as e:
        logger.warning("OCI 모델 삭제 거부(진행 중 작업): %s: %s", name, e)
        raise HTTPException(status_code=409, detail=str(e))
    logger.info("OCI 모델 삭제됨: %s", name)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

class ReadmeBody(BaseModel):
    readme: str

@router.put("/{name}/readme")
async def update_readme(
    name: str, body: ReadmeBody, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """모델 README(Markdown) 갱신. 소유자·생성자·관리자."""
    logger.info("PUT /oci-models/%s/readme (len=%d)", name, len(body.readme))
    await assert_oci_editor(current, session, name)
    if not await service.update_readme(session, name, body.readme):
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 태그
# ---------------------------------------------------------------------------

@router.post("/{name}/tags/{tag_id}")
async def add_tag(current: CurrentUser,
    name: str, tag_id: int, session: AsyncSession = Depends(get_session),
):
    """모델에 태그 추가(모델·태그 없음 404, 이미 연결 409)."""
    logger.info("POST /oci-models/%s/tags/%d", name, tag_id)
    await assert_oci_editor(current, session, name)
    try:
        if not await service.add_tag(session, name, tag_id):
            raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    except service.OciNotFoundError as e:
        logger.warning("OCI 태그 추가 실패(태그 없음): %s tag_id=%d", name, tag_id)
        raise HTTPException(status_code=404, detail=str(e))
    except service.OciConflictError as e:
        logger.warning("OCI 태그 추가 충돌(이미 연결): %s tag_id=%d", name, tag_id)
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "ok"}


@router.delete("/{name}/tags/{tag_id}")
async def remove_tag(current: CurrentUser,
    name: str, tag_id: int, session: AsyncSession = Depends(get_session),
):
    """모델에서 태그 분리."""
    logger.info("DELETE /oci-models/%s/tags/%d", name, tag_id)
    await assert_oci_editor(current, session, name)
    if not await service.remove_tag(session, name, tag_id):
        logger.warning("OCI 태그 분리 대상 없음: %s tag_id=%d", name, tag_id)
        raise HTTPException(status_code=404, detail="모델에 연결된 태그가 아닙니다.")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 리니지
# ---------------------------------------------------------------------------

@router.get("/{name}/lineage", response_model=list[LineageResponse])
async def get_lineage(
    name: str, session: AsyncSession = Depends(get_session),
):
    """모델의 리니지 엔트리 목록(생성순). 모델이 없으면 404."""
    logger.debug("GET /oci-models/%s/lineage", name)
    result = await service.list_lineage(session, name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return result


@router.post("/{name}/lineage", response_model=LineageResponse)
async def add_lineage(current: CurrentUser,
    name: str, req: LineageCreate, session: AsyncSession = Depends(get_session),
):
    """리니지 엔트리 추가(데이터셋 / 부모 모델 등 외부 자원과 연결). 같은 연결은 409."""
    logger.info("POST /oci-models/%s/lineage: %s -> %s", name, req.relation_type, req.source_id)
    await assert_oci_editor(current, session, name)
    try:
        result = await service.add_lineage(
            session, name,
            source_type=req.source_type, source_id=req.source_id,
            source_name=req.source_name, relation_type=req.relation_type,
            description=req.description,
        )
    except service.OciConflictError as e:
        logger.warning("OCI 리니지 추가 충돌: %s: %s", name, e)
        raise HTTPException(status_code=409, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return result


@router.delete("/{name}/lineage/{lineage_id}")
async def remove_lineage(current: CurrentUser,
    name: str, lineage_id: int, session: AsyncSession = Depends(get_session),
):
    """리니지 엔트리 삭제 — 이 모델 소속 엔트리만."""
    logger.info("DELETE /oci-models/%s/lineage/%d", name, lineage_id)
    await assert_oci_editor(current, session, name)
    if not await service.remove_lineage(session, name, lineage_id):
        logger.warning("OCI 리니지 삭제 대상 없음: %s lineage_id=%d", name, lineage_id)
        raise HTTPException(status_code=404, detail="이 모델의 리니지 엔트리를 찾을 수 없습니다.")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 버전 + Finalize
# ---------------------------------------------------------------------------

@router.get("/{name}/versions", response_model=list[OciModelVersionResponse])
async def list_versions(
    name: str, session: AsyncSession = Depends(get_session),
):
    """모델의 모든 버전 목록(가져오는 중·실패 버전 포함). 모델이 없으면 404."""
    logger.debug("GET /oci-models/%s/versions", name)
    result = await service.list_versions(session, name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return result


@router.delete("/{name}/versions/{version}")
async def delete_version(current: CurrentUser,
    name: str, version: int, session: AsyncSession = Depends(get_session),
):
    """버전 하나 삭제(소유자·생성자·관리자) — DB 행과 저장소 파일을 지우고 모델 통계를 갱신한다.

    가져오는 중인 버전은 409. 모델·버전이 없으면 404."""
    logger.info("DELETE /oci-models/%s/versions/%d by %s", name, version, current.username)
    await assert_oci_editor(current, session, name)
    try:
        ok = await service.delete_version(session, name, version)
    except service.OciConflictError as e:
        logger.warning("OCI 버전 삭제 거부(가져오는 중): %s v%d", name, version)
        raise HTTPException(status_code=409, detail=str(e))
    if not ok:
        logger.warning("OCI 버전 삭제 대상 없음: %s v%d", name, version)
        raise HTTPException(status_code=404, detail=f"모델 '{name}' 의 v{version} 을(를) 찾을 수 없습니다.")
    return {"status": "ok"}


class FinalizeVersionRequest(BaseModel):
    readme: str | None = None

@router.post("/{name}/versions/{version}/finalize", response_model=OciModelVersionResponse)
async def finalize_version(current: CurrentUser,
    name: str, version: int,
    body: FinalizeVersionRequest | None = None,
    session: AsyncSession = Depends(get_session),
):
    """업로드 완료된 버전 확정: S3 파일 스캔 → OCI manifest 생성 → 버전 레코드 작성 → 모델 통계 갱신.

    소유자·생성자·관리자. 관리자가 확정하면 draft 모델을 approved 로 올린다.
    가져오는 중인 버전 409, 업로드된 파일 없음 400, 저장소 장애 503."""
    logger.info("POST /oci-models/%s/versions/%d/finalize", name, version)
    await assert_oci_editor(current, session, name)
    try:
        result = await service.finalize_push(
            session, name, version, readme=body.readme if body else None, approve=current.is_admin,
        )
    except service.OciConflictError as e:
        logger.warning("Finalize 충돌 %s v%d: %s", name, version, e)
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        logger.warning("Finalize 거부됨 %s v%d: %s", name, version, e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Finalize 오류 %s v%d: %s", name, version, e)
        raise _s3_http_error(e) from e   # 저장소 장애 503, 그 밖 500
    if not result:
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return result


# ---------------------------------------------------------------------------
# 임포트
# ---------------------------------------------------------------------------

@router.post("/import/huggingface", response_model=ImportResponse, status_code=202)
async def import_huggingface(
    req: HuggingFaceImportRequest, current: AdminUser, session: AsyncSession = Depends(get_session),
):
    """HuggingFace Hub 모델 가져오기 접수(관리자). 즉시 202 를 돌려주고 백그라운드에서 받아온다.

    진행 상황은 ``GET /oci-models/{name}/versions`` 의 해당 버전 ``status``
    (importing → ready | failed, 실패 사유 ``extra_metadata.error``) 로 확인한다.
    저장소가 없거나(404) revision 이 틀리면(400)·HF 에 연결할 수 없으면(502) 바로 오류를 돌려준다."""
    logger.info("POST /oci-models/import/huggingface: hf=%s, name=%s by %s",
                req.hf_model_id, req.name, current.username)
    try:
        return await service.start_hf_import(
            session, hf_model_id=req.hf_model_id, name=req.name,
            description=req.description, owner=req.owner,
            task=req.task, framework=req.framework, language=req.language,
            revision=req.revision, created_by=current.username,
        )
    except service.OciUpstreamError as e:
        logger.warning("HF 임포트 접수 거부 (hf=%s, status=%d): %s", req.hf_model_id, e.status_code, e)
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except service.OciConflictError as e:
        logger.warning("HF 임포트 접수 충돌 (hf=%s, name=%s): %s", req.hf_model_id, req.name, e)
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error("HF 임포트 접수 실패 (hf=%s, name=%s): %s", req.hf_model_id, req.name, e)
        raise HTTPException(status_code=500, detail=str(e))
