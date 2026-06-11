"""OCI 모델 허브 라우터.

``/api/v1/oci-models`` 경로에서 HuggingFace 스타일의 모델 카탈로그
(README · 태그 · 리니지 · 버전 라이프사이클) 를 제공한다. 모델 아티팩트
실저장은 ``app/models/model_store.py`` 의 S3 백엔드를 공유한다.

로깅 정책: 변경 동작(create/update/delete, README/tag/lineage 변경,
finalize, import) 은 INFO 로 영향을 받은 이름·식별자를 함께 기록한다.
404 / 충돌 / 외부 임포트 실패는 WARNING(또는 ERROR) 으로 남긴다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, assert_owner_or_admin
from app.core.database import get_session
from app.oci_hub import service
from app.oci_hub.schemas import (
    HuggingFaceImportRequest,
    ImportResponse,
    LineageCreate,
    LineageResponse,
    OciModelCreate,
    OciModelDetail,
    OciModelUpdate,
    OciModelVersionResponse,
    PaginatedOciModels,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oci-models", tags=["oci-model-hub"])


# ---------------------------------------------------------------------------
# 서버 정보 (임포트 가이드 코드 샘플용)
# ---------------------------------------------------------------------------

@router.get("/server-info")
async def get_server_info():
    """임포트 가이드 코드 샘플에 표시할 서버 host/port (``0.0.0.0`` 은 실제 IP 로 해석)."""
    import socket

    from app.core.config import settings
    # 실제 hostname/IP 조회 — 0.0.0.0 은 클라이언트에 쓸모가 없음
    host = settings.host
    if host == "0.0.0.0":
        try:
            host = socket.gethostbyname(socket.gethostname())
        except Exception:
            host = "localhost"
    return {"host": host, "port": settings.port}


@router.get("/stats")
async def get_hub_stats(session: AsyncSession = Depends(get_session)):
    """OCI 모델 허브 대시보드 통계(요약 카드·차트·다운로드/게시 추이)."""
    logger.info("GET /oci-models/stats")
    return await service.get_hub_stats(session)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

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
    """OCI 모델 목록 페이징 조회(태스크/프레임워크/언어/상태 필터 지원)."""
    logger.info("GET /oci-models: search=%s, task=%s, framework=%s, page=%d", search, task, framework, page)
    return await service.list_models(session, search=search, task=task, framework=framework,
                                     language=language, status=status, page=page, page_size=page_size)


@router.post("", response_model=OciModelDetail)
async def create_model(current: CurrentUser,
    req: OciModelCreate, session: AsyncSession = Depends(get_session),
):
    """새 OCI 모델 등록(이름 중복 시 409)."""
    logger.info("POST /oci-models: name=%s", req.name)
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
    logger.info("GET /oci-models/%s", name)
    detail = await service.get_model_detail(session, name)
    if not detail:
        logger.warning("OCI 모델을 찾을 수 없음: %s", name)
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return detail


@router.patch("/{name}", response_model=OciModelDetail)
async def update_model(current: CurrentUser,
    name: str, req: OciModelUpdate, session: AsyncSession = Depends(get_session),
):
    """모델 메타데이터 부분 갱신(설명·태스크·프레임워크·언어 등)."""
    logger.info("PATCH /oci-models/%s", name)
    assert_owner_or_admin(current, await service.get_oci_created_by(session, name), "OCI 모델")
    result = await service.update_model(session, name, req)
    if not result:
        logger.warning("OCI 모델 갱신 실패 (없음): %s", name)
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return result


@router.delete("/{name}")
async def delete_model(current: CurrentUser,
    name: str, session: AsyncSession = Depends(get_session),
):
    """모델과 관련 데이터(버전·태그·리니지) 일괄 삭제. S3 파일도 함께 정리."""
    logger.info("DELETE /oci-models/%s", name)
    assert_owner_or_admin(current, await service.get_oci_created_by(session, name), "OCI 모델")
    if not await service.delete_model(session, name):
        logger.warning("OCI 모델 삭제 실패 (없음): %s", name)
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    logger.info("OCI 모델 삭제됨: %s", name)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

class ReadmeBody(BaseModel):
    readme: str

@router.put("/{name}/readme")
async def update_readme(
    name: str, body: ReadmeBody, _admin: AdminUser, session: AsyncSession = Depends(get_session),
):
    """모델 README(Markdown) 갱신. 관리자 권한 필요."""
    logger.info("PUT /oci-models/%s/readme (len=%d)", name, len(body.readme))
    if not await service.update_readme(session, name, body.readme):
        logger.warning("README 갱신 실패 (모델 없음): %s", name)
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 태그
# ---------------------------------------------------------------------------

@router.post("/{name}/tags/{tag_id}")
async def add_tag(_guard: AdminUser,
    name: str, tag_id: int, session: AsyncSession = Depends(get_session),
):
    """모델에 태그 추가(모델 또는 태그 미존재 시 404)."""
    logger.info("POST /oci-models/%s/tags/%d", name, tag_id)
    if not await service.add_tag(session, name, tag_id):
        logger.warning("태그 연결 실패 (모델 또는 태그 없음): name=%s, tag_id=%d", name, tag_id)
        raise HTTPException(status_code=404, detail="모델 또는 태그을(를) 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/{name}/tags/{tag_id}")
async def remove_tag(_guard: AdminUser,
    name: str, tag_id: int, session: AsyncSession = Depends(get_session),
):
    """모델에서 태그 분리."""
    logger.info("DELETE /oci-models/%s/tags/%d", name, tag_id)
    if not await service.remove_tag(session, name, tag_id):
        logger.warning("태그 분리 실패 (모델 또는 태그 없음): name=%s, tag_id=%d", name, tag_id)
        raise HTTPException(status_code=404, detail="모델 또는 태그을(를) 찾을 수 없습니다.")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 리니지
# ---------------------------------------------------------------------------

@router.get("/{name}/lineage", response_model=list[LineageResponse])
async def get_lineage(
    name: str, session: AsyncSession = Depends(get_session),
):
    """모델의 리니지 엔트리 목록."""
    detail = await service.get_model_detail(session, name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    # 리니지를 올바른 타입으로 재조회
    from sqlalchemy import select

    from app.oci_hub.models import OciModel, OciModelLineage
    m = (await session.execute(select(OciModel).where(OciModel.name == name))).scalars().first()
    entries = (await session.execute(
        select(OciModelLineage).where(OciModelLineage.model_id == m.id)
    )).scalars().all()
    return [LineageResponse.model_validate(e) for e in entries]


@router.post("/{name}/lineage", response_model=LineageResponse)
async def add_lineage(_guard: AdminUser,
    name: str, req: LineageCreate, session: AsyncSession = Depends(get_session),
):
    """리니지 엔트리 추가(데이터셋 / 부모 모델 등 외부 자원과 연결)."""
    logger.info("POST /oci-models/%s/lineage: %s -> %s", name, req.relation_type, req.source_id)
    result = await service.add_lineage(
        session, name,
        source_type=req.source_type, source_id=req.source_id,
        source_name=req.source_name, relation_type=req.relation_type,
        description=req.description,
    )
    if not result:
        logger.warning("리니지 추가 실패 (모델 없음): %s", name)
        raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
    return result


@router.delete("/{name}/lineage/{lineage_id}")
async def remove_lineage(_guard: AdminUser,
    name: str, lineage_id: int, session: AsyncSession = Depends(get_session),
):
    """리니지 엔트리 삭제."""
    logger.info("DELETE /oci-models/%s/lineage/%d", name, lineage_id)
    if not await service.remove_lineage(session, lineage_id):
        logger.warning("리니지 삭제 실패 (없음): id=%d", lineage_id)
        raise HTTPException(status_code=404, detail="리니지 엔트리을(를) 찾을 수 없습니다.")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 버전 + Finalize
# ---------------------------------------------------------------------------

@router.get("/{name}/versions", response_model=list[OciModelVersionResponse])
async def list_versions(
    name: str, session: AsyncSession = Depends(get_session),
):
    """모델의 모든 버전 목록(다운로드 횟수 등 메타 포함)."""
    logger.info("GET /oci-models/%s/versions", name)
    return await service.list_versions(session, name)


class FinalizeVersionRequest(BaseModel):
    readme: str | None = None

@router.post("/{name}/versions/{version}/finalize", response_model=OciModelVersionResponse)
async def finalize_version(_guard: AdminUser,
    name: str, version: int,
    body: FinalizeVersionRequest | None = None,
    session: AsyncSession = Depends(get_session),
):
    """업로드 완료된 버전 확정: S3 파일 스캔 → OCI manifest 생성 → 버전 레코드 작성 → 모델 통계 갱신."""
    logger.info("POST /oci-models/%s/versions/%d/finalize", name, version)
    try:
        result = await service.finalize_push(session, name, version, readme=body.readme if body else None)
        if not result:
            logger.warning("Finalize 실패 (모델 없음): %s v%d", name, version)
            raise HTTPException(status_code=404, detail=f"모델 '{name}'을(를) 찾을 수 없습니다.")
        return result
    except ValueError as e:
        logger.warning("Finalize 거부됨 %s v%d: %s", name, version, e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Finalize 오류 %s v%d: %s", name, version, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 임포트
# ---------------------------------------------------------------------------

@router.post("/import/huggingface", response_model=ImportResponse)
async def import_huggingface(
    req: HuggingFaceImportRequest, current: AdminUser, session: AsyncSession = Depends(get_session),
):
    """HuggingFace Hub 에서 모델을 가져와 OCI 허브에 등록. 관리자 권한 필요."""
    logger.info("POST /oci-models/import/huggingface: hf=%s, name=%s by %s",
                req.hf_model_id, req.name, current.username)
    try:
        return await service.import_from_huggingface(
            session, hf_model_id=req.hf_model_id, name=req.name,
            description=req.description, owner=req.owner,
            task=req.task, framework=req.framework, language=req.language,
            revision=req.revision, created_by=current.username,
        )
    except Exception as e:
        logger.error("HF 임포트 실패 (hf=%s, name=%s): %s", req.hf_model_id, req.name, e)
        raise HTTPException(status_code=500, detail=str(e))
