# SPDX-License-Identifier: Apache-2.0
"""데이터 카탈로그 API 엔드포인트 (``/api/v1/catalog``).

대시보드 통계, 데이터 소스(접속 설정·연결 테스트·동기화·삭제), 태그, 용어집, 데이터셋 CRUD,
ERD·리니지·사용 기반 컬럼 관계, 샘플 데이터, 파이프라인, 데이터셋 리니지 엔드포인트를 둔다.

권한 규칙 요약:
- 데이터 소스 관리/동기화/접속 정보 조회·태그·용어집 편집: 명시 허용형 기능 권한(미설정이면 admin 전용)
- 데이터셋 수정·삭제와 태그·소유자·용어·스키마·속성·샘플 편집: 생성자 또는 관리자
- 관계 적재/초기화, 파이프라인·데이터셋 리니지 쓰기, 샘플 parquet 수신: 관리자 전용
"""

import asyncio
import json
import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import service
from app.catalog.schemas import (
    CatalogStats,
    ColumnMappingCreate,
    DatasetCreate,
    DatasetLineageCreate,
    DatasetLineageResponse,
    DatasetPropertyCreate,
    DatasetPropertyResponse,
    DatasetResponse,
    DatasetUpdate,
    DatasourceConfigurationResponse,
    DatasourceConfigurationSave,
    DatasourceConnectionTest,
    DatasourceConnectionTestResult,
    DatasourceCreate,
    DatasourceMetadataResponse,
    DatasourceResponse,
    DatasourceUpdate,
    FKLineageReplaceRequest,
    GlossaryTermCreate,
    GlossaryTermResponse,
    GlossaryTermUpdate,
    OwnerCreate,
    OwnerResponse,
    PaginatedDatasets,
    PipelineCreate,
    PipelineResponse,
    PipelineUpdate,
    SchemaFieldCreate,
    SchemaFieldResponse,
    TagCreate,
    TagResponse,
    TagUpdate,
    TagUsage,
)
from app.catalog.sync_runs_service import legacy_sync_locks
from app.core.auth import AdminUser, CurrentUser, OptionalUser, TokenUser
from app.core.config import settings
from app.core.database import get_session
from app.permissions.router import (
    check_feature,
    require_dataset_create,
    require_dataset_feature,
    require_feature,
    require_feature_granted,
    require_menu,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])

# 리니지 · 파이프라인 편집 — 명시 허용형(미설정이면 관리자만, TODO-122). FK 리니지 갱신(수집)은 관리자 그대로.
LineageEditor = Annotated[TokenUser, Depends(require_feature_granted("lineage.edit"))]


# ---------------------------------------------------------------------------
# 대시보드
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=CatalogStats)
async def get_catalog_stats(
    user: CurrentUser,
    tz: str | None = Query(None, max_length=64, description="추이 날짜 기준 IANA 시간대 (예: Asia/Seoul, 기본 UTC)"),
    session: AsyncSession = Depends(get_session),
):
    """카탈로그 개요 통계를 조회한다. 로그인 필요 — 화면(대시보드)은 항상 authFetch 로 호출한다."""
    logger.debug("대시보드 통계 조회: user=%s tz=%s", user.username, tz)
    return await service.get_catalog_stats(session, tz=tz)


# ---------------------------------------------------------------------------
# 데이터 소스 엔드포인트
# ---------------------------------------------------------------------------

# 데이터 소스·토폴로지 관리 / 메타데이터 동기화 — 명시 허용형 기능 권한(미설정이면 admin 전용).
DatasourceManager = Annotated[TokenUser, Depends(require_feature_granted("datasources.manage"))]
DatasourceSyncer = Annotated[TokenUser, Depends(require_feature_granted("datasources.sync"))]

# 데이터 소스별 동기화 락 — 같은 데이터 소스 동시 동기화(재시도 겹침)는 409 로 거절한다.
# 단일 프로세스 기준(다중 워커면 워커별로 동작). 키는 데이터 소스 내부 PK(int).
# 화면 수집(start_run)도 이 잠금을 확인하도록 sync_runs_service 에 둔다(DEF-070).
_sync_locks = legacy_sync_locks

async def reject_manual_datasource(datasource_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """경로의 데이터 소스가 수동 등록 데이터 소스(TODO-CAT-001)면 409 — 원천 접속 · 직접 관리 조작을 막는다."""
    from app.catalog.catalogs_service import is_manual_datasource
    from app.catalog.models import Datasource as _Datasource
    if is_manual_datasource(await session.get(_Datasource, datasource_id)):
        raise HTTPException(
            status_code=409,
            detail="수동 등록 데이터 소스는 원천 접속이 없고 카탈로그가 관리합니다. 카탈로그 화면에서 관리하세요.")


NotManual = Depends(reject_manual_datasource)


@router.get("/datasources", response_model=list[DatasourceResponse])
async def list_datasources(
    _user: CurrentUser,
    include_manual: bool = Query(False, description="수동 등록 데이터 소스(카탈로그 내부용)도 포함"),
    session: AsyncSession = Depends(get_session),
):
    """전체 데이터 소스 목록을 조회한다. 수동 등록 데이터 소스는 기본 제외(TODO-CAT-001)."""
    return await service.list_datasources(session, include_manual=include_manual)


@router.post("/datasources", response_model=DatasourceResponse)
async def create_datasource(_guard: DatasourceManager, req: DatasourceCreate, session: AsyncSession = Depends(get_session)):
    """새 데이터 소스를 등록한다."""
    return await service.create_datasource(session, req)


@router.post("/datasources/test-connection", response_model=DatasourceConnectionTestResult)
async def test_datasource_connection(
    _guard: DatasourceManager, req: DatasourceConnectionTest, session: AsyncSession = Depends(get_session),
):
    """주어진 연결 설정으로 실제 접속을 시도해 성공 여부를 반환한다(저장 전 검증 가능).

    ``datasource_id`` 가 있으면 비워 둔(마스킹된) 비밀값을 저장된 값으로 채워 테스트한다.
    """
    config = req.config
    if req.datasource_id is not None:
        await reject_manual_datasource(req.datasource_id, session)
        from app.catalog.datasource_secrets import (
            DatasourceSecretError,
            SecretTargetChangedError,
            fill_stored_secrets,
            guard_secret_reuse,
        )

        stored = await service.get_datasource_stored_config(session, req.datasource_id)
        ds = await service.get_datasource(session, req.datasource_id)
        try:
            # 저장된 비밀번호는 저장된 접속 대상에만 쓴다 — host 등을 바꿔 임의 서버로 보내지 않도록
            guard_secret_reuse(config, stored, type_changed=bool(ds and ds.type != req.type))
            config = fill_stored_secrets(config, stored)
        except (DatasourceSecretError, SecretTargetChangedError) as e:
            logger.warning("연결 테스트 거부 — 저장된 비밀값 사용 불가: datasource_id=%d reason=%s",
                           req.datasource_id, type(e).__name__)
            return {"ok": False, "message": str(e)}
    return await service.test_datasource_connection(req.type, config)


@router.put("/datasources/{datasource_id}", response_model=DatasourceResponse, dependencies=[NotManual])
async def update_datasource(_guard: DatasourceManager,
    datasource_id: int, req: DatasourceUpdate, session: AsyncSession = Depends(get_session)
):
    """데이터 소스 이름·환경(origin)을 수정한다. 환경이 바뀌면 소속 데이터셋 origin 도 갱신된다."""
    datasource = await service.update_datasource(session, datasource_id, req)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    return datasource


@router.get("/datasources/{datasource_id}/metadata", response_model=DatasourceMetadataResponse)
async def get_datasource_metadata(
    datasource_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)
):
    """데이터 소스 메타데이터(데이터 타입, 테이블 타입, 저장 포맷, 기능)를 조회한다."""
    metadata = await service.get_datasource_metadata(session, datasource_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    return metadata


@router.get("/datasources/{datasource_id}/configuration", response_model=DatasourceConfigurationResponse, dependencies=[NotManual])
async def get_datasource_configuration(
    datasource_id: int, session: AsyncSession = Depends(get_session),
    # 접속 정보 — 명시 허용형 기능 권한(datasources.connection, 미설정이면 admin 전용).
    # 비밀값은 응답에서 마스킹되지만 호스트·계정 등도 민감 정보라 기본 거부.
    _perm=Depends(require_feature_granted("datasources.connection")),
):
    """데이터 소스의 연결/설정 정보를 조회한다."""
    config = await service.get_datasource_configuration(session, datasource_id)
    if not config:
        raise HTTPException(status_code=404, detail="데이터 소스 설정을 찾을 수 없습니다.")
    return config


@router.put("/datasources/{datasource_id}/configuration", response_model=DatasourceConfigurationResponse, dependencies=[NotManual])
async def save_datasource_configuration(_guard: DatasourceManager,
    datasource_id: int,
    req: DatasourceConfigurationSave,
    session: AsyncSession = Depends(get_session),
):
    """데이터 소스의 연결/설정 정보를 저장하거나 갱신한다.

    접속 대상 형식 오류·대상 변경 시 비밀값 재사용(SecretTargetChangedError, ValueError 하위)은 400.
    """
    datasource = await service.get_datasource(session, datasource_id)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    try:
        return await service.save_datasource_configuration(session, datasource_id, req.config)
    except ValueError as e:
        logger.warning("데이터 소스 설정 저장 거부: datasource_id=%d reason=%s", datasource_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/datasources/{datasource_id}/dataset-count")
async def get_datasource_dataset_count(
    datasource_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)
):
    """이 데이터 소스를 사용하는 데이터셋 수를 조회한다."""
    datasource = await service.get_datasource(session, datasource_id)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    count = await service.get_datasource_dataset_count(session, datasource_id)
    return {"datasource_id": datasource_id, "dataset_count": count}


@router.get("/datasources/{datasource_id}/delete-impact")
async def get_datasource_delete_impact(
    datasource_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)
):
    """데이터 소스 삭제 영향(데이터셋 수, 타 데이터 소스 연결 리니지 수)."""
    datasource = await service.get_datasource(session, datasource_id)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    return await service.get_datasource_delete_impact(session, datasource_id)


@router.delete("/datasources/{datasource_id}", dependencies=[NotManual])
async def delete_datasource(_guard: DatasourceManager,
    datasource_id: int,
    force: bool = Query(False, description="true 면 소속 데이터셋과 하위를 함께 삭제(cascade)"),
    session: AsyncSession = Depends(get_session),
):
    """데이터 소스 삭제. 활성 데이터셋이 있으면 기본 차단(409); force=true 면 cascade 삭제.

    동기화(예전 동기 API)나 수집 실행(대기·실행 중)이 있으면 409. 수집 설정·실행 이력은 함께 지워진다. removed 데이터셋은 force 없이도 함께 정리된다(service.delete_datasource).
    """
    if (lock := _sync_locks.get(datasource_id)) is not None and lock.locked():
        logger.warning("데이터 소스 삭제 거부 — 동기화 진행 중: datasource_id=%d", datasource_id)
        raise HTTPException(status_code=409, detail="동기화가 진행 중인 데이터 소스는 삭제할 수 없습니다.")
    # 화면 수집(지금 수집·예약)은 실행 행으로 돈다 — 예전 잠금만 보면 수집 중에도 지워졌다(DEF-051)
    from app.catalog.sync_runs_service import active_run_id
    if (run_id := await active_run_id(session, datasource_id)) is not None:
        logger.warning("데이터 소스 삭제 거부 — 수집 실행 중: datasource_id=%d run=%d", datasource_id, run_id)
        raise HTTPException(
            status_code=409,
            detail=f"수집이 진행 중인 데이터 소스는 삭제할 수 없습니다(실행 #{run_id}). 수집이 끝난 뒤 다시 삭제하세요.",
        )
    count = await service.get_datasource_dataset_count(session, datasource_id)
    if count > 0 and not force:
        logger.warning("데이터 소스 삭제 거부 — 활성 데이터셋 존재: datasource_id=%d datasets=%d", datasource_id, count)
        raise HTTPException(
            status_code=409,
            detail=f"데이터 소스를 삭제할 수 없습니다: 활성 데이터셋 {count}개가 이 데이터 소스에 속해 있습니다. "
            "데이터셋까지 함께 삭제하도록 선택해 주세요.",
        )
    datasource = await service.get_datasource(session, datasource_id)
    if not await service.delete_datasource(session, datasource_id, force=force):
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    _sync_locks.pop(datasource_id, None)
    if datasource is not None:
        # 커밋 후 샘플 디렉터리 정리(실패해도 삭제 결과에는 영향 없음)
        await service.remove_datasource_samples(datasource.datasource_id)
    return {"status": "ok", "message": "Datasource deleted"}


@router.post("/datasources/{datasource_id}/sync", dependencies=[NotManual])
async def sync_datasource(_guard: DatasourceSyncer,
    datasource_id: int,
    database: str | None = Query(None, description="Specific database to sync (optional)"),
    session: AsyncSession = Depends(get_session),
):
    """외부 데이터 소스의 메타데이터를 카탈로그로 동기화한다(요청 안에서 동기 실행).

    같은 데이터 소스가 이미 동기화 중이면 409, 동기화 오류는 첫 오류 메시지로 400.
    """
    datasource = await service.get_datasource(session, datasource_id)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")

    lock = _sync_locks.setdefault(datasource_id, asyncio.Lock())
    if lock.locked():
        logger.warning("동기화 거부 — 이미 진행 중: datasource_id=%d", datasource_id)
        raise HTTPException(status_code=409, detail="이 데이터 소스는 이미 동기화 중입니다. 완료 후 다시 시도하세요.")

    logger.info("동기화 요청: datasource=%s (id=%d), database=%s",
                datasource.datasource_id, datasource_id, database or "all")

    from app.catalog.sync import sync_datasource_metadata
    from app.catalog.sync_runs_service import active_run_id
    # 잠금을 먼저 잡은 뒤 화면 수집 실행을 확인한다 — 둘 사이에 다른 요청이 끼어들지 않게(DEF-070)
    async with lock:
        if (run_id := await active_run_id(session, datasource_id)) is not None:
            logger.warning("동기화 거부 — 화면 수집 실행 중: datasource_id=%d run=%d", datasource_id, run_id)
            raise HTTPException(
                status_code=409,
                detail=f"이 데이터 소스는 수집이 진행 중입니다(실행 #{run_id}). 수집이 끝난 뒤 다시 시도하세요.",
            )
        result = await sync_datasource_metadata(session, datasource.datasource_id, database)

    if result.errors:
        logger.warning("동기화 실패: datasource=%s, error=%s", datasource.datasource_id, result.errors[0])
        raise HTTPException(status_code=400, detail=result.errors[0])

    logger.info("동기화 완료: datasource=%s, 생성=%d, 갱신=%d, 제거=%d, 샘플=%d",
                result.datasource_id, result.tables_created, result.tables_updated,
                result.tables_removed, result.samples_uploaded)
    if result.tables_created or result.tables_updated:
        from app.catalog.sync_runs_service import _resolve_lineage_after_sync
        await _resolve_lineage_after_sync(session, datasource_id)

    return {
        "status": "ok",
        "datasource_id": result.datasource_id,
        "databases_scanned": result.databases_scanned,
        "tables_created": result.tables_created,
        "tables_updated": result.tables_updated,
        "tables_removed": result.tables_removed,
        "tables_total": result.tables_total,
        "tables_skipped": result.tables_skipped,
        "samples_uploaded": result.samples_uploaded,
    }


# ---------------------------------------------------------------------------
# 태그 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/tags", response_model=list[TagResponse])
async def list_tags(session: AsyncSession = Depends(get_session)):
    """전체 태그 목록 조회."""
    return await service.list_tags(session)


# 태그 관리(생성·수정·삭제) — 명시 허용형 기능 권한(미설정이면 admin 전용)
TAGS_MANAGE_FEATURE = "tags.manage"
_tag_manager = Depends(require_feature_granted(TAGS_MANAGE_FEATURE))


def _tag_http_error(e: service.TagError) -> HTTPException:
    """서비스의 TagError 를 같은 상태코드의 HTTPException 으로 바꾼다(거부 사유는 경고 로그)."""
    logger.warning("태그 요청 거부(%d): %s", e.status_code, e.message)
    return HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/tags", response_model=TagResponse)
async def create_tag(req: TagCreate, _manager=_tag_manager, session: AsyncSession = Depends(get_session)):
    """태그 생성. 이름 중복(대소문자 무시)이면 409, 색상은 #rrggbb."""
    try:
        result = await service.create_tag(session, req)
    except service.TagError as e:
        raise _tag_http_error(e) from e
    logger.info("태그 생성: id=%d, name=%s", result.id, result.name)
    return result


@router.put("/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: int, req: TagUpdate, _manager=_tag_manager, session: AsyncSession = Depends(get_session),
):
    """태그 이름·설명·색상 수정. 데이터셋·OCI 모델·알림 규칙 연결은 유지된다."""
    try:
        result = await service.update_tag(session, tag_id, req)
    except service.TagError as e:
        raise _tag_http_error(e) from e
    if not result:
        logger.warning("수정 대상 태그 없음: tag_id=%d", tag_id)
        raise HTTPException(status_code=404, detail="태그를 찾을 수 없습니다.")
    return result


@router.get("/tags/{tag_id}/usage", response_model=TagUsage)
async def get_tag_usage(tag_id: int, session: AsyncSession = Depends(get_session)):
    """태그 사용 현황 — 데이터셋·OCI 모델·TAG 범위 알림 규칙. 태그 삭제 전 영향 범위 확인용."""
    usage = await service.get_tag_usage(session, tag_id)
    if not usage:
        logger.warning("태그 사용 현황 조회 실패(없음): tag_id=%d", tag_id)
        raise HTTPException(status_code=404, detail="태그를 찾을 수 없습니다.")
    return usage


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: int, _manager=_tag_manager, session: AsyncSession = Depends(get_session)):
    """태그 삭제. 데이터셋·OCI 모델 연결은 CASCADE 로 함께 제거되고, TAG 범위 알림 규칙이 있으면 409."""
    try:
        deleted = await service.delete_tag(session, tag_id)
    except service.TagError as e:
        raise _tag_http_error(e) from e
    if not deleted:
        logger.warning("태그 삭제 실패(없음): tag_id=%d", tag_id)
        raise HTTPException(status_code=404, detail="태그를 찾을 수 없습니다.")
    logger.info("태그 삭제: id=%d", tag_id)
    return {"status": "ok", "message": "Tag deleted"}


# ---------------------------------------------------------------------------
# 용어집 항목 엔드포인트
# ---------------------------------------------------------------------------

# 용어집 편집 — 명시 허용형 기능 권한(미설정이면 admin 전용, 권한 관리에서 역할에 허용 가능)
GLOSSARY_EDIT_FEATURE = "glossary.edit"
_glossary_editor = Depends(require_feature_granted(GLOSSARY_EDIT_FEATURE))


def _glossary_http_error(e: service.GlossaryError) -> HTTPException:
    """서비스의 GlossaryError 를 같은 상태코드의 HTTPException 으로 바꾼다(거부 사유는 경고 로그)."""
    logger.warning("용어집 요청 거부(%d): %s", e.status_code, e.message)
    return HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/glossary", response_model=list[GlossaryTermResponse])
async def list_glossary_terms(session: AsyncSession = Depends(get_session)):
    """전체 용어집 항목 조회. CATEGORY/TERM 모두 포함, 계층 구조는 ``parent_id`` 로 표현."""
    return await service.list_glossary_terms(session)


@router.post("/glossary", response_model=GlossaryTermResponse)
async def create_glossary_term(
    req: GlossaryTermCreate, _editor=_glossary_editor, session: AsyncSession = Depends(get_session),
):
    """용어집 항목 생성. ``term_type`` 으로 분류/용어를 구분하며, 상위는 분류(CATEGORY)만 가능."""
    try:
        return await service.create_glossary_term(session, req)
    except service.GlossaryError as e:
        raise _glossary_http_error(e) from e


@router.put("/glossary/{term_id}", response_model=GlossaryTermResponse)
async def update_glossary_term(
    term_id: int, req: GlossaryTermUpdate, _editor=_glossary_editor,
    session: AsyncSession = Depends(get_session),
):
    """용어집 항목 부분 갱신. ``parent_id`` 변경으로 다른 분류로 이동 가능(순환 이동은 400)."""
    try:
        result = await service.update_glossary_term(session, term_id, req.model_dump(exclude_unset=True))
    except service.GlossaryError as e:
        raise _glossary_http_error(e) from e
    if not result:
        logger.warning("용어집 항목 수정 실패(없음): term_id=%d", term_id)
        raise HTTPException(status_code=404, detail="용어를 찾을 수 없습니다.")
    return result


@router.delete("/glossary/{term_id}")
async def delete_glossary_term(
    term_id: int, _editor=_glossary_editor, session: AsyncSession = Depends(get_session),
):
    """용어집 항목 삭제.

    데이터셋 매핑은 CASCADE 로 정리되지만, 하위 용어(parent_id)가 있으면 FK 로 차단되므로
    먼저 막아 409 를 반환한다(분류·조직 삭제와 동일).
    """
    categories, terms = await service.glossary_term_children_by_type(session, term_id)
    if categories or terms:
        target = await session.get(service.GlossaryTerm, term_id)
        noun = "분류" if target is not None and target.term_type == "CATEGORY" else "용어"
        parts = [p for p in (f"하위 분류 {categories}개" if categories else "",
                             f"용어 {terms}개" if terms else "") if p]
        logger.warning("용어집 항목 삭제 거부 — 하위 항목 존재: term_id=%d categories=%d terms=%d",
                       term_id, categories, terms)
        raise HTTPException(
            status_code=409,
            detail=f"{noun}를 삭제할 수 없습니다: {', '.join(parts)}가 있습니다.",
        )
    if not await service.delete_glossary_term(session, term_id):
        logger.warning("용어집 항목 삭제 실패(없음): term_id=%d", term_id)
        raise HTTPException(status_code=404, detail="용어를 찾을 수 없습니다.")
    return {"status": "ok", "message": "Glossary term deleted"}


# ---------------------------------------------------------------------------
# 데이터셋 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/datasets", response_model=PaginatedDatasets)
async def list_datasets(
    search: str | None = Query(None, description="Search in name, description, URN"),
    datasource: str | None = Query(None, description="Filter by datasource_id (쉼표로 여러 개)"),
    origin: str | None = Query(None, description="Filter by origin (PROD/DEV/STAGING, 쉼표로 여러 개)"),
    tag: str | None = Query(None, description="Filter by tag name"),
    status: str | None = Query(None, description="Filter by status (쉼표로 여러 개)"),
    org_id: int | None = Query(None, description="Filter by organization (subtree)"),
    system_id: int | None = Query(None, description="Filter by system"),
    category_id: int | None = Query(None, description="Filter by taxonomy category (subtree)"),
    taxonomy_id: int | None = Query(None, description="With uncategorized=true: datasets unmapped in this taxonomy"),
    uncategorized: bool = Query(False, description="taxonomy_id 의 미분류 데이터셋만"),
    tag_id: str | None = Query(None, description="Filter by tag id (쉼표로 여러 개)"),
    term_id: int | None = Query(None, description="Filter by glossary term id"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    card: bool = Query(False, description="카드 보기용 필드(컬럼 미리보기)도 채운다"),
    catalog_id: int | None = Query(None, description="소속 카탈로그"),
    manual: bool | None = Query(None, description="true: 수동 데이터셋만, false: 수동 데이터셋 제외"),
    session: AsyncSession = Depends(get_session),
):
    """필터·페이지네이션 옵션으로 데이터셋 목록을 조회한다(status 미지정이면 removed 제외)."""
    return await service.list_datasets(
        session, search=search, datasource=datasource, origin=origin,
        tag=tag, status=status, org_id=org_id, system_id=system_id,
        category_id=category_id, taxonomy_id=taxonomy_id, uncategorized=uncategorized,
        tag_id=tag_id, term_id=term_id,
        page=page, page_size=page_size, card=card,
        catalog_id=catalog_id, manual=manual,
    )


@router.post("/datasets", response_model=DatasetResponse)
async def create_dataset(req: DatasetCreate, current: CurrentUser, _perm=Depends(require_dataset_create), session: AsyncSession = Depends(get_session)):
    """데이터셋 직접 등록 — 관리자·동기화 계정(datasources.sync), 또는 설정에서 허용한 경우 일반 사용자
    (기능 권한 datasets.create 적용). 생성자(created_by)가 소유자가 된다."""
    from app.catalog.catalogs_service import (
        CatalogConflictError,
        CatalogNotFoundError,
        ManualMappedError,
    )
    try:
        return await service.create_dataset(session, req, created_by=current.username)
    except ManualMappedError as e:
        # metadata-sync 는 이 code 를 실패가 아닌 "수동 매핑으로 건너뜀"으로 센다(TODO-CAT-001)
        logger.info("데이터셋 생성 건너뜀(수동 매핑): user=%s reason=%s", current.username, e)
        raise HTTPException(status_code=409, detail={"code": ManualMappedError.code, "message": str(e)})
    except CatalogConflictError as e:
        logger.warning("데이터셋 생성 거부(카탈로그): user=%s reason=%s", current.username, e)
        raise HTTPException(status_code=409, detail=str(e))
    except CatalogNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        logger.warning("데이터셋 생성 거부: user=%s reason=%s", current.username, e)
        raise HTTPException(status_code=400, detail=str(e))


async def _hide_ddl_if_denied(session: AsyncSession, user, dataset):
    """DDL 조회 기능 권한(datasets.ddl-view)이 없으면 응답에서 DDL 을 뺀다(페더레이션 상세와 동일)."""
    if getattr(dataset, "ddl", None) and not await check_feature(session, user, "datasets.ddl-view"):
        dataset = dataset.model_copy(update={"ddl": None})
    return dataset


@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
async def get_dataset(dataset_id: int, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """ID 로 데이터셋 상세를 조회한다."""
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return await _hide_ddl_if_denied(session, user, dataset)


@router.get("/datasets/urn/{urn:path}", response_model=DatasetResponse)
async def get_dataset_by_urn(urn: str, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """URN 으로 데이터셋 상세를 조회한다."""
    dataset = await service.get_dataset_by_urn(session, urn)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return await _hide_ddl_if_denied(session, user, dataset)


@router.put("/datasets/{dataset_id}", response_model=DatasetResponse, dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def update_dataset(
    dataset_id: int, req: DatasetUpdate, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """데이터셋 수정 — 소유자(생성자 또는 소유자 목록) 또는 관리자(TODO-154)."""
    await _assert_dataset_editor(session, dataset_id, current)
    try:
        dataset = await service.update_dataset(session, dataset_id, req, actor=current.username)
    except ValueError as e:  # 저장값과 합친 검증(AI-Ready 시간범위 앞뒤) — 변경은 커밋 전이라 버려진다
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return dataset


# ---------------------------------------------------------------------------
# AI-Ready 메타데이터 (TODO-096) — 기관 기본값, 프로파일 기반 자동 채움 제안
# ---------------------------------------------------------------------------

@router.get("/ai-ready/defaults")
async def get_ai_ready_defaults(_user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """AI-Ready 항목의 기관 기본값(설정 › AI-Ready)과 선택지 — 데이터셋에 값이 없을 때 화면이 보여 준다."""
    from app.catalog import ai_ready

    return {
        "defaults": await ai_ready.load_defaults(session),
        "licenses": [{"code": k, "label": v} for k, v in ai_ready.LICENSES.items()],
        "allowed_uses": [{"code": k, "label": v} for k, v in ai_ready.ALLOWED_USES.items()],
    }


@router.get("/ai-ready/public-base")
async def get_ai_ready_public_base(_user: CurrentUser, request: Request, session: AsyncSession = Depends(get_session)):
    """내보내기에 쓰는 외부 공개 주소(설정 › AI-Ready) — 화면의 *API 주소* 복사용(DEF-142).

    DCAT ``@id`` · 데이터 카드와 같은 값(``public_base``)이다. 설정 미리보기(``/settings/ai-ready/public-url``)는
    관리자 전용이라, 로그인한 사용자에게는 방식과 확정된 주소만 읽기 전용으로 준다. ``mode`` 가 REQUEST 면
    요청 주소라 화면은 자기 주소(브라우저 origin)를 그대로 써도 된다.
    """
    from app.catalog import public_url
    from app.settings.service import get_config_by_category

    cfg = await get_config_by_category(session, "ai_ready")
    mode = (cfg.get(public_url.KEY_MODE) or "REQUEST").upper()
    return {"mode": mode, "base": public_url.resolve(cfg, request)}


@router.get("/datasets/{dataset_id}/ai-ready/suggestions")
async def get_ai_ready_suggestions(
    dataset_id: int, _user: OptionalUser, session: AsyncSession = Depends(get_session),
):
    """최신 프로파일에서 계산한 시간범위·결측 정보 제안(저장하지 않음). 프로파일이 없으면 ``profile: null``."""
    from app.catalog import ai_ready

    if not await service.dataset_exists(session, dataset_id):
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return await ai_ready.build_suggestions(session, dataset_id)


class AiReadyJudgmentRequest(BaseModel):
    status: str
    note: str | None = None


@router.get("/ai-ready/items")
async def get_ai_ready_items(_user: OptionalUser):
    """AI-Ready 체크리스트 항목 정의(가이드라인 부록 4 — 공통 33, 유형별 5)."""
    from app.catalog import ai_ready_check

    return ai_ready_check.item_catalog()


@router.get("/ai-ready/scopes")
async def get_ai_ready_scopes(_user: OptionalUser, _menu=Depends(require_menu("ai-ready")),
                               session: AsyncSession = Depends(get_session)):
    """AI-Ready 점검 목록의 범위 선택지 — 카탈로그와 그 아래 DB(활성 데이터셋 수). AI-Ready 메뉴 권한 필요(DEF-126)."""
    from app.catalog import ai_ready_check

    return await ai_ready_check.scopes(session)


@router.get("/ai-ready/overview")
async def get_ai_ready_overview(
    _user: OptionalUser,
    _menu=Depends(require_menu("ai-ready")),
    q: str | None = Query(None, description="db.dataset 경로·표시 이름 검색"),
    catalog_id: int | None = Query(None, ge=0, description="카탈로그 PK (0 = 카탈로그 없음)"),
    db: str | None = Query(None, description="DB(스키마) 이름 — catalog_id 와 함께. 빈 문자열은 DB 단계가 없는 데이터셋"),
    only_unmet: bool = Query(False, description="필수 항목이 하나라도 적정이 아닌 데이터셋만"),
    sort: str = Query("required", description="required(필수 충족률) · recommended(권장 충족률) · name(경로)"),
    desc: bool = Query(False, description="내림차순 — 기본은 오름차순(충족률 낮은 순 · 경로 가나다순)"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """활성 데이터셋별 AI-Ready 충족률 목록과 요약(카탈로그별, 카탈로그를 고르면 DB 별 평균).

    AI-Ready 점검 메뉴가 막힌 역할은 403(DEF-126) — 데이터셋 상세의 AI-Ready 탭(데이터셋별 점검)은 그대로 열린다."""
    from app.catalog import ai_ready_check

    return await ai_ready_check.overview(session, q=q, catalog_id=catalog_id, db=db, only_unmet=only_unmet,
                                         sort=sort, desc=desc, page=page, size=size)


@router.get("/datasets/{dataset_id}/ai-ready/check")
async def get_ai_ready_check(
    dataset_id: int, _user: OptionalUser, session: AsyncSession = Depends(get_session),
):
    """데이터셋의 AI-Ready 점검 — 항목별 자동 판정·수동 판정·근거, 필수·권장 충족률, 최소 요건."""
    from app.catalog import ai_ready_check

    result = await ai_ready_check.evaluate_dataset(session, dataset_id)
    if result is None:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return result


@router.put("/datasets/{dataset_id}/ai-ready/check/{item_key}",
            dependencies=[Depends(require_dataset_feature("datasets.edit")), Depends(require_feature("aiready.judge"))])
async def put_ai_ready_judgment(
    dataset_id: int, item_key: str, req: AiReadyJudgmentRequest, current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """체크리스트 항목의 수동 판정 저장 — 데이터셋 소유자(생성자 또는 소유자 목록) 또는 관리자. 자동 판정보다 우선한다."""
    from app.catalog import ai_ready_check

    await _assert_dataset_editor(session, dataset_id, current)
    try:
        await ai_ready_check.set_judgment(session, dataset_id, item_key, req.status, req.note, current.username)
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    await session.commit()
    return await ai_ready_check.evaluate_dataset(session, dataset_id)


@router.delete("/datasets/{dataset_id}/ai-ready/check/{item_key}",
               dependencies=[Depends(require_dataset_feature("datasets.edit")), Depends(require_feature("aiready.judge"))])
async def delete_ai_ready_judgment(
    dataset_id: int, item_key: str, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """수동 판정을 지워 자동 판정으로 되돌린다 — 데이터셋 소유자(생성자 또는 소유자 목록) 또는 관리자."""
    from app.catalog import ai_ready_check

    await _assert_dataset_editor(session, dataset_id, current)
    await ai_ready_check.clear_judgment(session, dataset_id, item_key)
    await session.commit()
    return await ai_ready_check.evaluate_dataset(session, dataset_id)


class DistributionRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    format: str | None = None
    media_type: str | None = None
    access_url: str
    download_url: str | None = None
    byte_size: int | None = None
    checksum_algorithm: str | None = None
    checksum: str | None = None


async def _assert_dataset_editor(session: AsyncSession, dataset_id: int, current) -> None:
    """데이터셋 일반 편집 — 데이터셋 소유자(생성자 또는 소유자 목록) 또는 관리자(TODO-154).

    AI-Ready 알림을 받는 사람과 같은 기준이다(DEF-138). 데이터셋이 없으면 404.
    삭제 · 소유자 추가 · 삭제는 생성자 · 관리자만(:func:`_require_dataset_creator`).
    """
    from app.catalog.dataset_owner import assert_dataset_owner_or_admin

    await assert_dataset_owner_or_admin(session, current, dataset_id)


@router.get("/datasets/{dataset_id}/distributions")
async def list_distributions(dataset_id: int, _user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """데이터셋 배포본 목록(dcat:Distribution) — 포털·API·파일 서버에 있는 배포본의 위치·형식·용량·체크섬."""
    from app.catalog import ai_ready_export

    if not await service.dataset_exists(session, dataset_id):
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return [ai_ready_export.distribution_dict(d) for d in await ai_ready_export.list_distributions(session, dataset_id)]


@router.post("/datasets/{dataset_id}/distributions", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def create_distribution(
    dataset_id: int, req: DistributionRequest, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """배포본 추가 — 데이터셋 소유자 또는 관리자. 접속 URL 필수, 체크섬은 알고리즘별 16진수 길이를 검사한다."""
    from app.catalog import ai_ready_export

    await _assert_dataset_editor(session, dataset_id, current)
    try:
        row = await ai_ready_export.create_distribution(session, dataset_id, req.model_dump())
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    await session.commit()
    await session.refresh(row)
    return ai_ready_export.distribution_dict(row)


class DistributionOrderRequest(BaseModel):
    ids: list[int]


@router.post("/datasets/{dataset_id}/distributions/reorder", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def reorder_distributions(
    dataset_id: int, req: DistributionOrderRequest, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """배포본 순서 바꾸기 — 이 데이터셋 배포본 id 전부를 새 순서로. 데이터셋 소유자(생성자 또는 소유자 목록) 또는 관리자.

    DCAT · Croissant · 데이터 카드 · 페더레이션 상세가 이 순서를 따른다. 목록이 서버와 다르면 409.
    """
    from app.catalog import ai_ready_export

    await _assert_dataset_editor(session, dataset_id, current)
    try:
        rows = await ai_ready_export.reorder_distributions(session, dataset_id, req.ids)
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    await session.commit()
    for r in rows:
        await session.refresh(r)
    return [ai_ready_export.distribution_dict(r) for r in rows]


@router.put("/datasets/{dataset_id}/distributions/{dist_id}", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def update_distribution(
    dataset_id: int, dist_id: int, req: DistributionRequest, current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """배포본 수정(전체 교체) — 데이터셋 소유자(생성자 또는 소유자 목록) 또는 관리자."""
    from app.catalog import ai_ready_export

    await _assert_dataset_editor(session, dataset_id, current)
    try:
        row = await ai_ready_export.update_distribution(session, dataset_id, dist_id, req.model_dump())
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="배포본을 찾을 수 없습니다.")
    await session.commit()
    await session.refresh(row)
    return ai_ready_export.distribution_dict(row)


@router.delete("/datasets/{dataset_id}/distributions/{dist_id}", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def delete_distribution(
    dataset_id: int, dist_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """배포본 삭제 — 데이터셋 소유자(생성자 또는 소유자 목록) 또는 관리자."""
    from app.catalog import ai_ready_export

    await _assert_dataset_editor(session, dataset_id, current)
    if not await ai_ready_export.delete_distribution(session, dataset_id, dist_id):
        raise HTTPException(status_code=404, detail="배포본을 찾을 수 없습니다.")
    await session.commit()
    return {"status": "ok"}


# 데이터 버전 이력 (TODO-096 4단계) — 기록은 데이터셋 수정(PUT /datasets/{id})이 '버전'·'버전 노트'를 바꿀 때 한다.

@router.get("/datasets/{dataset_id}/versions")
async def list_dataset_versions(dataset_id: int, _user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """데이터 버전 이력 — 최근 순."""
    from app.catalog import ai_ready

    if not await service.dataset_exists(session, dataset_id):
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return await ai_ready.list_versions(session, dataset_id)


@router.delete("/datasets/{dataset_id}/versions/{version_id}", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def delete_dataset_version(
    dataset_id: int, version_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """잘못 남긴 버전 이력 한 줄 삭제 — 데이터셋 소유자(생성자 또는 소유자 목록) 또는 관리자. 현재 버전 값은 바꾸지 않는다."""
    from sqlalchemy import delete as sql_delete

    from app.catalog.models import DatasetVersion

    await _assert_dataset_editor(session, dataset_id, current)
    res = await session.execute(sql_delete(DatasetVersion).where(
        DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset_id))
    if not res.rowcount:
        raise HTTPException(status_code=404, detail="버전 이력을 찾을 수 없습니다.")
    await session.commit()
    logger.info("데이터 버전 이력 삭제: dataset_id=%d version_id=%d by=%s", dataset_id, version_id, current.username)
    return {"status": "ok"}


@router.delete("/datasets/{dataset_id}", dependencies=[Depends(require_feature("datasets.delete"))])
async def delete_dataset(dataset_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """데이터셋 삭제 — 생성자 또는 관리자만(소유자 목록 사용자는 403, TODO-154)."""
    await _require_dataset_creator(current, session, dataset_id)
    if not await service.delete_dataset(session, dataset_id):
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return {"status": "ok", "message": "Dataset deleted"}


# ---------------------------------------------------------------------------
# 리니지
# ---------------------------------------------------------------------------

@router.get("/datasources/{datasource_id}/erd", dependencies=[NotManual])
async def get_datasource_erd(
    datasource_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """데이터 소스 전체 ER 다이어그램 — 모든 테이블 + FK 관계."""
    if not await service.get_datasource(session, datasource_id):
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    from app.catalog.erd import build_datasource_erd
    return await build_datasource_erd(session, datasource_id)


@router.get("/datasets/{dataset_id}/erd")
async def get_dataset_erd(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """ER 다이어그램 — 중심 데이터셋 + FK 직접 연결 테이블 (DDL 파싱 + 리니지 폴백).

    데이터셋이 없으면 404 가 아니라 빈 ERD(tables·relations 빈 목록)를 반환한다.
    """
    from app.catalog.erd import build_erd
    return await build_erd(session, dataset_id)


# ---------------------------------------------------------------------------
# Column relationships (사용 기반 — 쿼리 JOIN 키 집계)
# ---------------------------------------------------------------------------

class JoinKeyPair(BaseModel):
    """SQL 에서 추출한 JOIN 키 컬럼쌍(테이블·컬럼은 SQL 상의 이름)."""

    a_table: str
    a_col: str
    b_table: str
    b_col: str
    kind: str = "explicit"  # explicit(JOIN ON) | implicit(WHERE 등치)


class RelationshipIngest(BaseModel):
    """컬럼 관계 적재 요청 — datasource_id 는 외부 문자열 ID 또는 유형."""

    datasource_id: str
    pairs: list[JoinKeyPair]


@router.post("/relationships/ingest")
async def ingest_relationships(
    req: RelationshipIngest, current: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """확장(metadata-sync)이 추출한 JOIN 키 컬럼쌍을 해석·집계해 적재한다(관리자).

    metadata-sync 는 관리자 계정 토큰으로 호출한다(같은 클라이언트의 reset 도 관리자 전용).
    """
    from app.catalog import relationship_service
    logger.debug("컬럼 관계 적재 요청: datasource=%s pairs=%d", req.datasource_id, len(req.pairs))
    return await relationship_service.ingest_join_keys(
        session, req.datasource_id, [p.model_dump() for p in req.pairs],
        username=getattr(current, "username", None),
    )


@router.post("/relationships/reset")
async def reset_relationships(
    _guard: AdminUser,
    datasource_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """관계 전체(또는 datasource 범위) 삭제 — 전체 재계산 전에 호출(관리자)."""
    from app.catalog import relationship_service
    deleted = await relationship_service.reset_relationships(session, datasource_id)
    return {"deleted": deleted}


@router.post("/relationships/recompute")
async def recompute_relationships(_guard: AdminUser, reset: bool = Query(default=True)):
    """metadata-sync 의 전체 재계산을 트리거(프록시)한다(관리자).

    파서(JOIN 키 추출)는 metadata-sync 가 소유하므로, 카탈로그는 sync 의 recompute 를
    호출해 결과를 그대로 반환한다.
    """
    import httpx

    from app.catalog.impala_profile_router import metadata_sync_headers
    url = f"{settings.metadata_sync_base_url.rstrip('/')}/collector/relationships/recompute"
    logger.info("컬럼 관계 재계산 트리거: reset=%s", reset)
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, params={"reset": str(reset).lower()},
                                     headers=metadata_sync_headers())
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        logger.warning("metadata-sync recompute 호출 실패: %s", e)
        raise HTTPException(
            status_code=502, detail=f"metadata-sync recompute 호출 실패: {e}",
        )


@router.get("/datasets/{dataset_id}/relationships/graph")
async def get_dataset_relationship_graph(
    dataset_id: int,
    depth: int = Query(default=2, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
):
    """관계 그래프(무방향, depth 홉, 최대 3) — React Flow 시각화용 nodes/edges. 데이터셋이 없으면 빈 그래프."""
    from app.catalog import relationship_service
    return await relationship_service.get_relationship_graph(session, dataset_id, depth)


@router.get("/datasets/{dataset_id}/relationships")
async def get_dataset_relationships(
    dataset_id: int, session: AsyncSession = Depends(get_session),
):
    """데이터셋의 사용 기반 컬럼 관계(자주 함께 조인된 컬럼/데이터셋)."""
    from app.catalog import relationship_service
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return await relationship_service.get_relationships(session, dataset_id)


@router.post("/lineage/resolve")
async def resolve_query_lineage(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """argus_query_lineage 의 NULL dataset_id 를 이름→데이터셋 매칭으로 채운다(관리자 백필).

    확장은 테이블명(+ 엔진·엔진 쪽 카탈로그 이름)만 적재하므로, 이 해석을 거쳐야 쿼리 기반 lineage 가
    카탈로그에 노출된다. 엔진을 아는 행은 엔진의 카탈로그 연결(``remote_name``)로 정한 카탈로그 안에서만
    찾는다(``lineage_resolve`` 참고). 데이터셋 생성·수집 종료·카탈로그 연결 추가/해제 때 자동으로도 해석되지만,
    과거 이력 일괄 반영용이다. 응답 ``unresolved_catalog`` 는 카탈로그를 정하지 못해 남긴 칸 수다.
    """
    from app.catalog import lineage_resolve
    return await lineage_resolve.resolve_all_query_lineage(session)


@router.get("/datasets/{dataset_id}/lineage")
async def get_dataset_lineage(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋의 업스트림·다운스트림 리니지를 조회한다.

    UI 에서 리니지 DAG 를 렌더링하기에 적합한 노드(데이터셋)와 엣지(source→target 관계)를
    반환한다. 쿼리 기반 리니지(argus_query_lineage)와 데이터셋 단위 리니지
    (argus_dataset_lineage)를 병합해 타 데이터 소스 간 관계까지 포함한다.
    """
    # 데이터셋 존재 여부 확인
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    # 참고: 1~2단계는 관련 데이터셋 ID 만 모은다. 응답 엣지는 4단계에서 related_ids 사이의
    # 모든 엣지를 다시 조회해 만든다.
    # ------------------------------------------------------------------
    # 1. 두 리니지 출처 모두에서 관련 ID 를 수집
    # ------------------------------------------------------------------
    related_ids: set[int] = {dataset_id}

    # 1a. 쿼리 기반 리니지 (같은 데이터 소스, 자동 수집)
    upstream_rows = (await session.execute(text("""
        SELECT DISTINCT source_table, source_dataset_id
        FROM argus_query_lineage
        WHERE target_dataset_id = :ds_id AND source_dataset_id IS NOT NULL
    """), {"ds_id": dataset_id})).fetchall()

    downstream_rows = (await session.execute(text("""
        SELECT DISTINCT target_table, target_dataset_id
        FROM argus_query_lineage
        WHERE source_dataset_id = :ds_id AND target_dataset_id IS NOT NULL
    """), {"ds_id": dataset_id})).fetchall()

    for row in upstream_rows:
        related_ids.add(row[1])

    for row in downstream_rows:
        related_ids.add(row[1])

    # 1b. 데이터셋 단위 리니지 (타 데이터 소스 간, 수동/파이프라인)
    # FK(외래키)는 데이터 흐름이 아니라 구조적 관계라 lineage 에서 제외한다(관계는 별도).
    dl_rows = (await session.execute(text("""
        SELECT id, source_dataset_id, target_dataset_id, lineage_source, relation_type
        FROM argus_dataset_lineage
        WHERE (source_dataset_id = :ds_id OR target_dataset_id = :ds_id)
          AND lineage_source <> 'FK'
    """), {"ds_id": dataset_id})).fetchall()

    for row in dl_rows:
        related_ids.add(row[1])
        related_ids.add(row[2])

    # 2. 두 출처에서 2차 연결(2-hop)을 조회
    first_level_ids = related_ids - {dataset_id}
    if first_level_ids:
        # IN 목록은 :id0, :id1 … 바인드 파라미터로 만든다(값을 SQL 에 직접 넣지 않음)
        placeholders = ", ".join(f":id{i}" for i in range(len(first_level_ids)))
        params = {f"id{i}": fid for i, fid in enumerate(first_level_ids)}

        # 쿼리 리니지 2-hop
        for direction_col, filter_col in [
            ("source_dataset_id", "target_dataset_id"),
            ("target_dataset_id", "source_dataset_id"),
        ]:
            rows2 = (await session.execute(text(f"""
                SELECT DISTINCT {direction_col}
                FROM argus_query_lineage
                WHERE {filter_col} IN ({placeholders}) AND {direction_col} IS NOT NULL
            """), params)).fetchall()
            for r in rows2:
                related_ids.add(r[0])

        # 데이터셋 리니지 2-hop
        rows2 = (await session.execute(text(f"""
            SELECT DISTINCT source_dataset_id, target_dataset_id,
                   id, lineage_source, relation_type
            FROM argus_dataset_lineage
            WHERE (source_dataset_id IN ({placeholders})
               OR target_dataset_id IN ({placeholders}))
              AND lineage_source <> 'FK'
        """), params)).fetchall()
        for r in rows2:
            related_ids.add(r[0])
            related_ids.add(r[1])

    # 3. 노드 구성
    nodes = []
    if related_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(related_ids)))
        params = {f"id{i}": rid for i, rid in enumerate(related_ids)}
        ds_rows = (await session.execute(text(f"""
            SELECT d.id, d.name, d.urn, p.type AS datasource_type, p.name AS datasource_name,
                   d.quality_status
            FROM catalog_datasets d
            JOIN catalog_datasources p ON d.datasource_id = p.id
            WHERE d.id IN ({placeholders})
        """), params)).fetchall()
        for row in ds_rows:
            nodes.append({
                "id": row[0],
                "name": row[1],
                "urn": row[2],
                "datasourceType": row[3],
                "datasourceName": row[4],
                "isCurrent": row[0] == dataset_id,
                "qualityStatus": row[5],
            })

    # 4. 엣지 구성 — 쿼리 기반 + 데이터셋 단위(FK 제외), 양 끝이 모두 related_ids 인 것만
    edges = []
    if related_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(related_ids)))
        params = {f"id{i}": rid for i, rid in enumerate(related_ids)}
        edge_rows = (await session.execute(text(f"""
            SELECT DISTINCT source_dataset_id, target_dataset_id, source_table, target_table
            FROM argus_query_lineage
            WHERE source_dataset_id IN ({placeholders})
              AND target_dataset_id IN ({placeholders})
              AND source_dataset_id IS NOT NULL
              AND target_dataset_id IS NOT NULL
        """), params)).fetchall()
        # edge dedup 은 (source, target, lineage_source) 까지 봐야 multi-source 가 안 가려진다.
        # MANUAL 과 PIPELINE 이 같은 쌍에 양립하거나 QUERY_AGGREGATED 와 MANUAL 이 같은 쌍에 동시에
        # 있을 때, 한 쌍에 대해 한 source 만 표시되는 게 아니라 모두 응답에 노출(FK 는 위에서 제외).
        seen_edges: set[tuple[int, int, str]] = set()
        for row in edge_rows:
            key = (row[0], row[1], "QUERY_AGGREGATED")
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({
                "source": row[0],
                "target": row[1],
                "sourceTable": row[2],
                "targetTable": row[3],
                "lineageSource": "QUERY_AGGREGATED",
                "lineageId": None,
            })

        # 데이터셋 단위 리니지 엣지 추가 (타 데이터 소스 간)
        dl_edge_rows = (await session.execute(text(f"""
            SELECT id, source_dataset_id, target_dataset_id,
                   lineage_source, relation_type, description
            FROM argus_dataset_lineage
            WHERE source_dataset_id IN ({placeholders})
              AND target_dataset_id IN ({placeholders})
              AND lineage_source <> 'FK'
        """), params)).fetchall()
        for row in dl_edge_rows:
            key = (row[1], row[2], row[3])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({
                "source": row[1],
                "target": row[2],
                "sourceTable": "",
                "targetTable": "",
                "lineageSource": row[3],
                "lineageId": row[0],
                "relationType": row[4],
                "description": row[5],
            })

    # 5. 컬럼 리니지 (쿼리 기반 + 데이터셋 단위 병합)
    # related_ids 는 그래프에 그려지는 모든 노드(2-hop 포함). columnLineage 가 graph 의
    # edge 와 일치하지 않으면 UI 가 edge click 시 매핑 정보를 못 찾으니, 1-hop 만이
    # 아니라 전체 related_ids 를 대상으로 column mapping 을 모은다.
    column_lineage = []
    direct_ids = list(related_ids)

    if direct_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(direct_ids)))
        params = {f"id{i}": did for i, did in enumerate(direct_ids)}

        # Query-based column lineage — lineageSource 는 항상 QUERY_AGGREGATED.
        # DISTINCT — 같은 데이터셋 쌍이 엔진(또는 카탈로그 이름)별 엣지 여러 행으로 들어올 수 있다.
        cl_rows = (await session.execute(text(f"""
            SELECT DISTINCT ql.source_dataset_id, ql.target_dataset_id,
                   cl.source_column, cl.target_column, cl.transform_type
            FROM argus_column_lineage cl
            JOIN argus_query_lineage ql ON cl.query_lineage_id = ql.id
            WHERE ql.source_dataset_id IN ({placeholders})
              AND ql.target_dataset_id IN ({placeholders})
              AND ql.source_dataset_id IS NOT NULL
              AND ql.target_dataset_id IS NOT NULL
        """), params)).fetchall()
        for row in cl_rows:
            column_lineage.append({
                "sourceDatasetId": row[0],
                "targetDatasetId": row[1],
                "sourceColumn": row[2],
                "targetColumn": row[3],
                "transformType": row[4],
                "lineageSource": "QUERY_AGGREGATED",
            })

        # Dataset-level column mappings — MANUAL/PIPELINE 만(FK 는 데이터 흐름 아님 → 제외).
        dcm_rows = (await session.execute(text(f"""
            SELECT dl.source_dataset_id, dl.target_dataset_id,
                   cm.source_column, cm.target_column, cm.transform_type,
                   dl.lineage_source
            FROM argus_dataset_column_mapping cm
            JOIN argus_dataset_lineage dl ON cm.dataset_lineage_id = dl.id
            WHERE dl.source_dataset_id IN ({placeholders})
              AND dl.target_dataset_id IN ({placeholders})
              AND dl.lineage_source <> 'FK'
        """), params)).fetchall()
        for row in dcm_rows:
            column_lineage.append({
                "sourceDatasetId": row[0],
                "targetDatasetId": row[1],
                "sourceColumn": row[2],
                "targetColumn": row[3],
                "transformType": row[4],
                "lineageSource": row[5],
            })

    logger.debug("리니지 조회: dataset_id=%d nodes=%d edges=%d column_lineage=%d",
                 dataset_id, len(nodes), len(edges), len(column_lineage))
    return {
        "datasetId": dataset_id,
        "nodes": nodes,
        "edges": edges,
        "columnLineage": column_lineage,
    }


# ---------------------------------------------------------------------------
# 데이터셋 관계 엔드포인트
# ---------------------------------------------------------------------------

async def _require_dataset_editor(current, session: AsyncSession, dataset_id: int) -> None:
    """태그 · 용어 연결 · 스키마 · 속성 · 샘플 — 데이터셋 수정과 같은 기준(생성자 · 소유자 목록 · 관리자). 없으면 404."""
    await _assert_dataset_editor(session, dataset_id, current)


async def _require_dataset_creator(current, session: AsyncSession, dataset_id: int) -> None:
    """데이터셋 삭제 · 소유자 추가 · 삭제 — 생성자 또는 관리자만(권한 상승 방지, TODO-154). 없으면 404."""
    from app.catalog.dataset_owner import assert_dataset_creator_or_admin

    await assert_dataset_creator_or_admin(session, current, dataset_id)


@router.post("/datasets/{dataset_id}/tags/{tag_id}", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def add_dataset_tag(current: CurrentUser,
    dataset_id: int, tag_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에 태그 부착."""
    await _require_dataset_editor(current, session, dataset_id)
    try:
        added = await service.add_dataset_tag(session, dataset_id, tag_id)
    except service.TagError as e:
        raise _tag_http_error(e) from e
    if not added:
        logger.warning("데이터셋 태그 부착 실패(데이터셋 또는 태그 없음): dataset_id=%d, tag_id=%d", dataset_id, tag_id)
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    logger.info("데이터셋 태그 부착: dataset_id=%d, tag_id=%d", dataset_id, tag_id)
    return {"status": "ok"}


@router.delete("/datasets/{dataset_id}/tags/{tag_id}", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def remove_dataset_tag(current: CurrentUser,
    dataset_id: int, tag_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에서 태그 분리."""
    await _require_dataset_editor(current, session, dataset_id)
    if not await service.remove_dataset_tag(session, dataset_id, tag_id):
        logger.warning("데이터셋 태그 분리 실패(연결 없음): dataset_id=%d, tag_id=%d", dataset_id, tag_id)
        raise HTTPException(status_code=404, detail="태그 연결을 찾을 수 없습니다.")
    logger.info("데이터셋 태그 분리: dataset_id=%d, tag_id=%d", dataset_id, tag_id)
    return {"status": "ok"}


@router.post("/datasets/{dataset_id}/owners", response_model=OwnerResponse, dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def add_dataset_owner(current: CurrentUser,
    dataset_id: int, req: OwnerCreate, session: AsyncSession = Depends(get_session)
):
    """데이터셋에 소유자를 추가한다 — 생성자 또는 관리자만(소유자 목록 사용자는 403)."""
    await _require_dataset_creator(current, session, dataset_id)
    owner = await service.add_dataset_owner(session, dataset_id, req)
    if not owner:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return owner


@router.delete("/datasets/{dataset_id}/owners/{owner_id}", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def remove_dataset_owner(current: CurrentUser,
    dataset_id: int, owner_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에서 소유자를 제거한다 — 생성자 또는 관리자만(소유자 목록 사용자는 403)."""
    await _require_dataset_creator(current, session, dataset_id)
    if not await service.remove_dataset_owner(session, owner_id, dataset_id):
        raise HTTPException(status_code=404, detail="소유자를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.post("/datasets/{dataset_id}/glossary/{term_id}", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def add_dataset_glossary_term(current: CurrentUser,
    dataset_id: int, term_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에 용어집 용어를 연결한다. 이미 연결돼 있으면 그대로 성공(멱등)."""
    await _require_dataset_editor(current, session, dataset_id)
    try:
        ok = await service.add_dataset_glossary_term(session, dataset_id, term_id)
    except service.GlossaryError as e:
        raise _glossary_http_error(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/datasets/{dataset_id}/glossary/{term_id}", dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def remove_dataset_glossary_term(current: CurrentUser,
    dataset_id: int, term_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에서 용어집 용어를 분리한다."""
    await _require_dataset_editor(current, session, dataset_id)
    if not await service.remove_dataset_glossary_term(session, dataset_id, term_id):
        raise HTTPException(status_code=404, detail="용어 연결을 찾을 수 없습니다.")
    return {"status": "ok"}


@router.put("/datasets/{dataset_id}/schema", response_model=list[SchemaFieldResponse], dependencies=[Depends(require_dataset_feature("datasets.schema-edit"))])
async def update_schema_fields(
    dataset_id: int,
    fields: list[SchemaFieldCreate],
    current: CurrentUser,
    session: AsyncSession = Depends(get_session),
    clear_empty: bool = Query(False, description="화면 편집 — 보낸 논리명 · 설명이 비어 있으면 지운다(수집은 빈 값이면 기존 값 유지)"),
):
    """데이터셋의 스키마 필드 전체를 교체한다 — 소유자(생성자 또는 소유자 목록) 또는 관리자. 데이터셋이 없으면 404.

    수집(metadata-sync)은 원천 코멘트가 없으면 빈 설명을 보내므로 기본은 빈 논리명 · 설명이면 기존 값을 둔다.
    화면의 스키마 편집은 ``clear_empty=true`` 로 비운 칸을 지운다(DEF-166).
    """
    await _require_dataset_editor(current, session, dataset_id)
    # 같은 field_path 가 중복되면 저장(첫 항목)과 변경 이력(마지막 항목)이 어긋나므로 거절
    paths = [f.field_path for f in fields]
    dups = sorted({p for p in paths if paths.count(p) > 1})
    if dups:
        logger.warning("스키마 교체 거부 — field_path 중복: dataset_id=%d dups=%s", dataset_id, dups)
        raise HTTPException(
            status_code=422, detail=f"중복된 field_path 가 있습니다: {', '.join(dups)}",
        )
    # 단위·산정기준은 요청에 키가 있을 때만 넘긴다 — 키가 없는 호출(수집 어댑터·이전 화면)은 담당자 값을 보존
    payload = []
    for f in fields:
        d = f.model_dump()
        # 화면 편집(clear_empty)의 논리명 · 설명도 보낸 키만 — 보내지 않은 칸까지 지우지 않게
        for k in ("unit", "calculation") + (("display_name", "description") if clear_empty else ()):
            if k not in f.model_fields_set:
                d.pop(k)
        payload.append(d)
    return await service.update_schema_fields(session, dataset_id, payload, clear_empty=clear_empty)


@router.put("/datasets/{dataset_id}/properties", response_model=list[DatasetPropertyResponse], dependencies=[Depends(require_dataset_feature("datasets.edit"))])
async def update_dataset_properties(
    dataset_id: int,
    properties: list[DatasetPropertyCreate],
    current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 속성 전체를 교체한다 — 소유자(생성자 또는 소유자 목록) 또는 관리자. 데이터셋이 없으면 404.

    metadata-sync 어댑터가 매 sync 마다 datasource-specific 메타를 갱신하기 위해 사용
    (예: Iceberg 의 current_snapshot_id / last_updated_ms). ``argus.`` 내부 키는 payload 에
    없으면 보존된다(service.update_dataset_properties).
    """
    await _require_dataset_editor(current, session, dataset_id)
    result = await service.update_dataset_properties(
        session, dataset_id,
        [p.model_dump() for p in properties],
    )
    # Avro 편집본(argus.avro_schema)이 바뀌면 외부 Avro API·스키마 레지스트리 캐시는 커밋 때 지워진다
    # (service.update_dataset_properties → app.schema_registry.changes)
    return result


@router.get("/datasets/{dataset_id}/schema/history")
async def get_schema_history(
    dataset_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 스키마 변경 이력 스냅샷을 조회한다."""
    return await service.get_schema_history(session, dataset_id, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# 샘플 데이터 엔드포인트
# ---------------------------------------------------------------------------

def _sample_dir(qualified_name: str):
    """data_dir/samples/{qualified_name}/ 아래의 샘플 디렉터리 경로를 구한다(레거시 CSV 위치).

    '/'·'\\' 를 '_' 로 바꾼 뒤, resolve 결과가 samples 루트 바로 아래가 아니면
    (예: ``..``) HTTPException(400) 으로 거부한다(방어적 경로 탈출 차단).
    """
    safe_name = qualified_name.replace("/", "_").replace("\\", "_")
    root = (settings.data_dir / "samples").resolve()
    path = (root / safe_name).resolve()
    if path.parent != root:
        logger.warning("샘플 경로 탈출 시도 차단: qualified_name=%s", qualified_name)
        raise HTTPException(status_code=400, detail="잘못된 샘플 경로입니다.")
    return path


def _sample_dir_by_datasource(datasource_id: str, name: str):
    """샘플 디렉터리 경로를 구한다: data_dir/samples/{datasource_id}/{name}/.

    이름에 ``..`` 등이 섞여 samples 루트 밖을 가리키면 ValueError(경로 탈출 차단).
    """
    root = (settings.data_dir / "samples").resolve()
    ds_root = (root / datasource_id).resolve()
    path = (ds_root / name).resolve()
    # 이 데이터 소스 디렉터리 안으로 한정(다른 데이터 소스 샘플 접근·덮어쓰기 차단).
    if ds_root.parent != root or path == ds_root or ds_root not in path.parents:
        logger.warning("샘플 경로 탈출 시도 차단: datasource_id=%s name=%s", datasource_id, name)
        raise ValueError(f"잘못된 샘플 경로입니다: {datasource_id}/{name}")
    return path


def _sample_dir_by_datasource_or_400(datasource_id: str, name: str):
    """:func:`_sample_dir_by_datasource` 의 HTTP 핸들러용 래퍼 — 경로 탈출(ValueError)을 400 으로 바꾼다."""
    try:
        return _sample_dir_by_datasource(datasource_id, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="잘못된 샘플 경로입니다.") from e


def _sample_path(qualified_name: str):
    """data_dir/samples/{qualified_name}/sample.csv 의 샘플 CSV 경로를 구한다."""
    return _sample_dir(qualified_name) / "sample.csv"


@router.post("/datasets/{dataset_id}/sample", dependencies=[Depends(require_feature("datasets.sample-manage"))])
async def upload_sample_data(
    dataset_id: int,
    file: UploadFile,
    current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 CSV 샘플 데이터 파일을 업로드한다 — 소유자(생성자 또는 소유자 목록) 또는 관리자."""
    await _require_dataset_editor(current, session, dataset_id)
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    qn = dataset.qualified_name or dataset.name
    dest = _sample_path(qn)
    dest.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    if len(content) > 100 * 1024:
        logger.warning("샘플 업로드 거부 — 크기 초과: dataset_id=%d size=%d", dataset_id, len(content))
        raise HTTPException(
            status_code=413,
            detail=f"파일 크기({len(content) / 1024:.1f} KB)가 100 KB 제한을 초과합니다.",
        )
    dest.write_bytes(content)
    logger.info("샘플 데이터 업로드 완료: %s (%d bytes)", dest, len(content))
    # 서버 내부 절대 경로(data_dir)는 응답에 싣지 않는다 — 저장된 파일 이름만(경로는 로그에만).
    return {"status": "ok", "file": dest.name, "size": len(content)}


@router.get("/datasets/{dataset_id}/sample")
async def get_sample_data(
    dataset_id: int,
    session: AsyncSession = Depends(get_session),
    # 실데이터 노출 — 기능 권한 매트릭스(datasets.sample-view)로 통제
    _perm=Depends(require_feature("datasets.sample-view")),
):
    """데이터셋의 샘플 데이터를 내려받는다.

    parquet 은 JSON {format, columns, rows}(값은 모두 문자열 또는 null)로, 레거시 파일은
    원본 CSV 로 반환한다. 둘 다 없으면 404.
    """
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    # parquet 경로를 먼저 시도
    parquet_path = _sample_dir_by_datasource_or_400(
        dataset.datasource.datasource_id, dataset.name,
    ) / "sample.parquet"
    if parquet_path.is_file():
        import pyarrow.parquet as pq
        table = pq.read_table(parquet_path)
        columns = table.column_names
        rows = []
        for i in range(table.num_rows):
            rows.append([
                str(v) if v is not None else None
                for v in (table.column(c)[i].as_py() for c in range(table.num_columns))
            ])
        return JSONResponse(content={
            "format": "parquet",
            "columns": columns,
            "rows": rows,
        })

    # 레거시 CSV 경로로 폴백
    qn = dataset.qualified_name or dataset.name
    csv_path = _sample_path(qn)
    if csv_path.is_file():
        return FileResponse(csv_path, media_type="text/csv", filename="sample.csv")

    logger.debug("샘플 데이터 없음: dataset_id=%d", dataset_id)
    raise HTTPException(status_code=404, detail="사용 가능한 샘플 데이터가 없습니다.")


# 이스케이프 표기: \xHH(그룹1) · \uHHHH(그룹2) · \n \r \t \0 \\(그룹3)
_ESCAPE_RE = re.compile(r"\\x([0-9A-Fa-f]{2})|\\u([0-9A-Fa-f]{4})|\\([nrt0\\])")
_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "\\": "\\"}


def _parse_escape_sequences(value: str) -> str:
    """UI(sample-data-tab.tsx parseEscapeSequences)와 같은 규칙으로 \\t·\\x1F·\\u0001 등을 해석한다."""
    def _sub(m: re.Match) -> str:
        if m.group(1):
            return chr(int(m.group(1), 16))
        if m.group(2):
            return chr(int(m.group(2), 16))
        return _SIMPLE_ESCAPES[m.group(3)]
    return _ESCAPE_RE.sub(_sub, value)


def _load_delimiter_config(path) -> dict:
    """저장된 구분자 설정(delimiter.json)을 CSV 파싱 인자로 바꾼다 — UI 미리보기와 같은 해석.

    설정 파일이 없으면 기존 동작(쉼표·UTF-8·첫 행 헤더·큰따옴표 인용)을 유지한다.
    """
    default = {"encoding": "utf-8", "delimiter": ",", "has_header": True, "quotechar": '"'}
    if not path.is_file():
        return default
    try:
        cfg = DelimiterConfig(**json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as e:
        logger.warning("구분자 설정 파싱 실패, 기본값 사용: %s (%s)", path, e)
        return default

    delimiter = cfg.delimiter or ","
    if cfg.delimiter_mode == "escape":
        delimiter = _parse_escape_sequences(cfg.delimiter_input)[:1] or delimiter
    elif cfg.delimiter_mode == "custom":
        delimiter = cfg.delimiter_input[:1] or delimiter

    if cfg.is_custom_quote:
        quotechar = cfg.custom_quote_char[:1] or None
    elif cfg.quote_char in ("", "__none__"):
        quotechar = None
    else:
        quotechar = cfg.quote_char[:1]
    return {
        "encoding": cfg.encoding or "utf-8",
        "delimiter": delimiter[:1],
        "has_header": cfg.has_header,
        "quotechar": quotechar,
    }


@router.post("/datasets/{dataset_id}/sample/convert-to-parquet", dependencies=[Depends(require_feature("datasets.sample-manage"))])
async def convert_sample_to_parquet(
    dataset_id: int,
    current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """기존 CSV 샘플을 parquet 포맷으로 변환한다 — 소유자(생성자 또는 소유자 목록) 또는 관리자.

    저장된 구분자 설정(delimiter.json — 구분자·헤더 유무·인코딩·인용 문자)을 그대로 적용한다.
    """
    await _require_dataset_editor(current, session, dataset_id)
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    qn = dataset.qualified_name or dataset.name
    csv_path = _sample_path(qn)
    if not csv_path.is_file():
        raise HTTPException(status_code=404, detail="변환할 CSV 샘플 데이터가 없습니다.")

    import csv as csv_mod
    import io

    import pyarrow as pa
    import pyarrow.parquet as _pq

    cfg = _load_delimiter_config(csv_path.parent / "delimiter.json")
    try:
        text = csv_path.read_bytes().decode(cfg["encoding"], errors="replace")
    except LookupError:
        logger.warning("CSV 변환 거부 — 알 수 없는 인코딩: dataset_id=%d encoding=%s", dataset_id, cfg["encoding"])
        raise HTTPException(status_code=400, detail=f"지원하지 않는 인코딩입니다: {cfg['encoding']}")
    reader_kwargs: dict = {"delimiter": cfg["delimiter"]}
    if cfg["quotechar"]:
        reader_kwargs["quotechar"] = cfg["quotechar"]
    else:
        reader_kwargs["quoting"] = csv_mod.QUOTE_NONE
    try:
        all_rows = [r for r in csv_mod.reader(io.StringIO(text), **reader_kwargs) if r]
    except (csv_mod.Error, TypeError) as e:
        logger.warning("CSV 변환 실패 — 파싱 오류: dataset_id=%d error=%s", dataset_id, e)
        raise HTTPException(status_code=400, detail=f"CSV 파싱에 실패했습니다: {e}")
    if not all_rows:
        raise HTTPException(status_code=400, detail="CSV 파일이 비어 있습니다.")

    if cfg["has_header"]:
        header = all_rows[0]
        data_rows = all_rows[1:]
    else:
        # 헤더 없음 — 첫 행도 데이터, 컬럼명은 col_N
        width = max(len(r) for r in all_rows)
        header = [f"col_{i}" for i in range(width)]
        data_rows = all_rows

    columns = {}
    for ci, col_name in enumerate(header):
        columns[col_name or f"col_{ci}"] = [
            row[ci] if ci < len(row) else None for row in data_rows
        ]

    arrow_table = pa.table(columns)
    dest_dir = _sample_dir_by_datasource_or_400(dataset.datasource.datasource_id, dataset.name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "sample.parquet"
    _pq.write_table(arrow_table, dest)

    # 기존 CSV 와 구분자 설정 제거
    csv_path.unlink()
    delim_path = csv_path.parent / "delimiter.json"
    if delim_path.is_file():
        delim_path.unlink()
    try:
        csv_path.parent.rmdir()
    except OSError:
        pass

    logger.info("CSV → parquet 변환 완료: %s (%d rows)", dest, len(data_rows))
    return {"status": "ok", "rows": len(data_rows), "columns": len(header)}


@router.delete("/datasets/{dataset_id}/sample", dependencies=[Depends(require_feature("datasets.sample-manage"))])
async def delete_sample_data(
    dataset_id: int,
    current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 샘플 데이터(parquet 또는 CSV)를 삭제한다 — 소유자(생성자 또는 소유자 목록) 또는 관리자."""
    await _require_dataset_editor(current, session, dataset_id)
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    deleted = False

    # parquet 경로 시도
    parquet_path = _sample_dir_by_datasource_or_400(
        dataset.datasource.datasource_id, dataset.name,
    ) / "sample.parquet"
    if parquet_path.is_file():
        parquet_path.unlink()
        try:
            parquet_path.parent.rmdir()
        except OSError:
            pass
        deleted = True

    # 레거시 CSV 경로 시도
    qn = dataset.qualified_name or dataset.name
    csv_path = _sample_path(qn)
    if csv_path.is_file():
        csv_path.unlink()
        delim_path = csv_path.parent / "delimiter.json"
        if delim_path.is_file():
            delim_path.unlink()
        try:
            csv_path.parent.rmdir()
        except OSError:
            pass
        deleted = True

    if deleted:
        logger.info("샘플 데이터 삭제: dataset_id=%d", dataset_id)
        return {"status": "ok", "message": "Sample data deleted"}
    raise HTTPException(status_code=404, detail="사용 가능한 샘플 데이터가 없습니다.")


class DelimiterConfig(BaseModel):
    """샘플 CSV 와 함께 저장되는 구분자/파싱 설정(UI 의 구분자 설정 폼과 같은 필드).

    ``delimiter_mode`` 가 "escape" 면 ``delimiter_input`` 을 이스케이프 해석, "custom" 이면 그 첫 글자를
    구분자로 쓴다. ``quote_char`` 가 "__none__"/빈 값이면 인용 없음.
    """
    encoding: str = "UTF-8"
    line_delimiter: str = "\n"
    delimiter: str = ","
    delimiter_mode: str | None = None
    delimiter_input: str = ""
    has_header: bool = True
    quote_char: str = "__none__"
    custom_quote_char: str = ""
    is_custom_quote: bool = False


@router.put("/datasets/{dataset_id}/sample/delimiter", dependencies=[Depends(require_feature("datasets.sample-manage"))])
async def save_delimiter_config(
    dataset_id: int,
    config: DelimiterConfig,
    current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 샘플 데이터의 구분자/파싱 설정을 저장한다 — 소유자(생성자 또는 소유자 목록) 또는 관리자."""
    await _require_dataset_editor(current, session, dataset_id)
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    qn = dataset.qualified_name or dataset.name
    sample_dir = _sample_dir(qn)
    sample_dir.mkdir(parents=True, exist_ok=True)

    dest = sample_dir / "delimiter.json"
    dest.write_text(json.dumps(config.model_dump(), ensure_ascii=False), encoding="utf-8")
    logger.info("구분자 설정 저장 완료: %s", dest)
    return {"status": "ok"}


@router.get("/datasets/{dataset_id}/sample/delimiter")
async def get_delimiter_config(
    dataset_id: int,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 샘플 데이터의 구분자/파싱 설정을 조회한다."""
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    qn = dataset.qualified_name or dataset.name
    path = _sample_dir(qn) / "delimiter.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="사용 가능한 구분자 설정이 없습니다.")

    data = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


# ---------------------------------------------------------------------------
# 샘플 데이터 수집 (동기화 업로드)
# ---------------------------------------------------------------------------

@router.post("/samples/upload")
async def upload_sample_parquet(_guard: AdminUser,
    request: Request,
    x_datasource_id: str = Header(..., alias="X-Datasource-Id"),
    x_dataset_name: str = Header(..., alias="X-Dataset-Name"),
):
    """동기화 프로세스로부터 parquet 샘플 파일을 수신한다(관리자 전용, 같은 위치면 덮어씀).

    헤더 값이 samples/{datasource_id}/ 밖을 가리키면 400(경로 탈출 차단).

    헤더:
        X-Datasource-Id: datasource_id (예: mysql-19d0bfe954e2cfdaa)
        X-Dataset-Name: database.table (예: sakila.country)
    본문:
        원본 parquet 바이트.
    """
    body = await request.body()
    if not body:
        logger.warning("샘플 parquet 수신 거부 — 빈 본문: datasource=%s dataset=%s", x_datasource_id, x_dataset_name)
        raise HTTPException(status_code=400, detail="요청 본문이 비어 있습니다.")

    dest_dir = _sample_dir_by_datasource_or_400(x_datasource_id, x_dataset_name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "sample.parquet"
    dest.write_bytes(body)
    logger.info("샘플 parquet 업로드 완료: %s (%d bytes)", dest, len(body))
    # 서버 내부 절대 경로는 응답에 싣지 않는다 — 샘플 루트 기준 상대 키(데이터 소스/데이터셋/파일)만.
    return {"status": "ok", "file": dest.name,
            "key": f"{x_datasource_id}/{x_dataset_name}/{dest.name}", "size": len(body)}


# ---------------------------------------------------------------------------
# 데이터 파이프라인 CRUD — ETL/CDC/파일 내보내기 파이프라인 레지스트리
# ---------------------------------------------------------------------------

@router.post("/pipelines", response_model=PipelineResponse, status_code=201)
async def create_pipeline(_guard: LineageEditor, data: PipelineCreate, session: AsyncSession = Depends(get_session)):
    """데이터 파이프라인(ETL, CDC, 파일 내보내기 등)을 등록한다."""
    result = await service.create_pipeline(session, data)
    await session.commit()
    return result


@router.get("/pipelines", response_model=list[PipelineResponse])
async def list_pipelines(session: AsyncSession = Depends(get_session)):
    """등록된 모든 데이터 파이프라인을 조회한다."""
    return await service.list_pipelines(session)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(pipeline_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 데이터 파이프라인을 조회한다."""
    pipeline = await service.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="파이프라인을 찾을 수 없습니다.")
    return PipelineResponse.model_validate(pipeline)


@router.put("/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def update_pipeline(_guard: LineageEditor,
    pipeline_id: int, data: PipelineUpdate, session: AsyncSession = Depends(get_session),
):
    """데이터 파이프라인을 수정한다."""
    result = await service.update_pipeline(session, pipeline_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="파이프라인을 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/pipelines/{pipeline_id}", status_code=204)
async def delete_pipeline(_guard: LineageEditor, pipeline_id: int, session: AsyncSession = Depends(get_session)):
    """데이터 파이프라인을 삭제한다."""
    deleted = await service.delete_pipeline(session, pipeline_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="파이프라인을 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 타 데이터 소스 간 데이터셋 리니지 — 시스템 간 명시적 데이터 흐름
# ---------------------------------------------------------------------------

@router.post("/lineage", response_model=DatasetLineageResponse, status_code=201)
async def create_lineage(_guard: LineageEditor,
    data: DatasetLineageCreate, session: AsyncSession = Depends(get_session),
):
    """타 데이터 소스 간 데이터셋 리니지 관계를 등록한다."""
    # 소스/대상 데이터셋 존재 여부 검증
    src = await service.get_dataset(session, data.source_dataset_id)
    if not src:
        raise HTTPException(status_code=404, detail="소스 데이터셋을 찾을 수 없습니다.")
    tgt = await service.get_dataset(session, data.target_dataset_id)
    if not tgt:
        raise HTTPException(status_code=404, detail="대상 데이터셋을 찾을 수 없습니다.")
    if data.source_dataset_id == data.target_dataset_id:
        logger.warning("리니지 생성 거부 — 소스와 대상이 같음: dataset_id=%d", data.source_dataset_id)
        raise HTTPException(status_code=400, detail="소스와 대상은 서로 달라야 합니다.")
    if data.pipeline_id:
        pipeline = await service.get_pipeline(session, data.pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="파이프라인을 찾을 수 없습니다.")

    # 등록자는 인증 사용자로 강제 — 본문 created_by 는 무시(사칭 방지, 화면은 보내지 않아 null 로 남던 문제)
    data = data.model_copy(update={"created_by": _guard.username})
    result = await service.create_dataset_lineage(session, data)
    await session.commit()
    return result


@router.get("/lineage", response_model=list[DatasetLineageResponse])
async def list_lineages(
    dataset_id: int | None = Query(None, description="Filter by dataset ID"),
    lineage_source: str | None = Query(None, description="Filter: MANUAL, PIPELINE, etc."),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 리니지 관계 목록을 조회한다(FK 출처 포함 — lineage_source 로 필터 가능)."""
    return await service.list_dataset_lineages(session, dataset_id, lineage_source)


@router.get("/lineage/{lineage_id}", response_model=DatasetLineageResponse)
async def get_lineage(lineage_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 데이터셋 리니지를 컬럼 매핑과 함께 조회한다."""
    lineage = await service.get_dataset_lineage(session, lineage_id)
    if not lineage:
        raise HTTPException(status_code=404, detail="리니지를 찾을 수 없습니다.")
    return await service._build_lineage_response(session, lineage_id)


@router.put("/lineage/{lineage_id}/column-mappings", response_model=DatasetLineageResponse)
async def update_lineage_column_mappings(_guard: LineageEditor,
    lineage_id: int,
    column_mappings: list[ColumnMappingCreate],
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 리니지의 컬럼 매핑을 교체한다."""
    result = await service.update_dataset_lineage_column_mappings(
        session, lineage_id, column_mappings,
    )
    if not result:
        raise HTTPException(status_code=404, detail="리니지를 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/lineage/{lineage_id}", status_code=204)
async def delete_lineage(_guard: LineageEditor, lineage_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋 리니지 관계를 삭제한다."""
    deleted = await service.delete_dataset_lineage(session, lineage_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="리니지를 찾을 수 없습니다.")
    await session.commit()


@router.put("/datasets/{dataset_id}/lineage/fk")
async def replace_fk_lineage(
    dataset_id: int,
    req: FKLineageReplaceRequest,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """``lineage_source=FK`` lineage 를 통째 교체. metadata-sync 어댑터가 매 sync 마다
    이 dataset 의 FK 제약을 통째 다시 보내는 멱등 엔드포인트. 다른 출처
    (MANUAL/PIPELINE/QUERY_AGGREGATED) 의 lineage 는 건드리지 않는다.
    """
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    result = await service.replace_fk_lineage_for_target(
        session, dataset_id, req.entries,
    )
    await session.commit()
    return result
