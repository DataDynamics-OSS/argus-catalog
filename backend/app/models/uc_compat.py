# SPDX-License-Identifier: Apache-2.0
"""Unity Catalog OSS 호환 라우터 — MLflow 통합용.

MLflow 의 ``UnityCatalogOssStore`` (``uc_oss_rest_store.py``) 가 호출하는
UC OSS 레지스트리 API 를 우리 내부 모델 서비스(``app/models/service.py``) 로
번역하는 어댑터 레이어. 사용자가 직접 부르는 API 가 아니라 MLflow SDK
호출에서만 사용한다.

Base path: ``/api/2.1/unity-catalog/``
참고: ``mlflow/store/_unity_catalog/registry/uc_oss_rest_store.py``

MLflow 측에서 ``registered_model_name`` 이 설정됐을 때의 흐름:
  1. ``POST /models``                                         → ``CreateRegisteredModel``
  2. ``POST /models/versions``                                → ``CreateModelVersion`` (PENDING)
  3. ``file://`` 스토리지로 ``LocalArtifactRepository`` 가 직접 업로드
  4. ``PATCH /models/{name}/versions/{ver}/finalize``         → READY 로 전이

설계 메모:
  - 3-part 이름(catalog.schema.model) 은 ``name`` 컬럼에 그대로 저장
  - 경로 파라미터는 ``{cat}.{sch}.{mdl}`` 형태 — 와일드카드 충돌 회피
  - ``storage_location`` 은 ``file://`` 프리픽스 → MLflow 가 자격증명 조회를
    건너뛰고 ``LocalArtifactRepository`` 로 직접 파일시스템 접근
  - ``/temporary-model-version-credentials`` 는 빈 응답(로컬 스토리지에서는 no-op)

인증:
  - 모든 엔드포인트(조회 포함)가 로그인 토큰(``Authorization: Bearer``)을 요구한다. MLflow 3.x 클라이언트는
    ``MLFLOW_UC_OSS_TOKEN`` 환경변수 값을 Bearer 토큰으로 보낸다.
  - 모델 생성(``POST /models``)은 ``models.register`` 기능 권한이 필요하다.
  - 쓰기(버전 생성·수정·삭제·finalize, 모델 수정·삭제)는 관리자·모델 등록자·모델 소유자만
    (``/api/v1/models`` 의 ``assert_model_editor`` 와 같은 기준).

사용 예:
    export MLFLOW_UC_OSS_TOKEN=<Argus Catalog 액세스 토큰>
    mlflow.set_registry_uri("uc:http://localhost:4600")
    mlflow.sklearn.log_model(model, "model", registered_model_name="argus.ml.my_model")

로깅 정책: 각 UC 엔드포인트 진입 시 INFO 로 모델/버전 식별자를 남기고(목록 조회는 DEBUG),
400 / 404 / 충돌 분기는 WARNING 으로 기록한다.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.database import get_session
from app.models import service
from app.models.router import assert_model_editor
from app.models.schemas import (
    ModelVersionCreate,
    ModelVersionFinalize,
    ModelVersionStatus,
    ModelVersionUpdate,
    RegisteredModelCreate,
    RegisteredModelResponse,
    RegisteredModelUpdate,
)
from app.permissions.router import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/2.1/unity-catalog",
    tags=["unity-catalog-compat"],
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _ts_ms(dt) -> int:
    """datetime 을 UC API 응답용 밀리초 타임스탬프로 변환한다."""
    if dt is None:
        return 0
    return int(dt.timestamp() * 1000)


def _model_to_uc(m: RegisteredModelResponse) -> dict:
    """내부 RegisteredModelResponse 를 UC API 응답 포맷으로 변환한다.

    이름이 3-part 가 아니면 catalog/schema 를 ``default`` 로 채우고 전체 이름을 model 이름으로 쓴다.
    """
    parts = m.name.split(".", 2)
    if len(parts) == 3:
        catalog_name, schema_name, model_name = parts
    else:
        catalog_name, schema_name, model_name = "default", "default", m.name
    return {
        "name": model_name,
        "catalog_name": catalog_name,
        "schema_name": schema_name,
        "full_name": m.name,
        "comment": m.description or "",
        "storage_location": m.storage_location or "",
        "owner": m.owner or "",
        "id": str(m.id),
        "created_at": _ts_ms(m.created_at),
        "created_by": m.created_by or "",
        "updated_at": _ts_ms(m.updated_at),
        "updated_by": m.updated_by or "",
    }


def _version_to_uc(v) -> dict:
    """내부 ModelVersionResponse 를 UC API 응답 포맷으로 변환한다."""
    parts = v.model_name.split(".", 2)
    if len(parts) == 3:
        catalog_name, schema_name, model_name = parts
    else:
        catalog_name, schema_name, model_name = "default", "default", v.model_name
    return {
        "model_name": model_name,
        "catalog_name": catalog_name,
        "schema_name": schema_name,
        "version": v.version,
        "source": v.source or "",
        "run_id": v.run_id or "",
        "comment": v.description or "",
        "status": v.status,
        "storage_location": v.storage_location or "",
        "id": str(v.id),
        "created_at": _ts_ms(v.created_at),
        "created_by": v.created_by or "",
        "updated_at": _ts_ms(v.updated_at),
        "updated_by": v.updated_by or "",
    }


# ---------------------------------------------------------------------------
# RegisteredModel 엔드포인트
# ---------------------------------------------------------------------------

@router.post("/models")
async def create_registered_model(request: Request, current: CurrentUser, _perm=Depends(require_feature("models.register")), session: AsyncSession = Depends(get_session)):
    """UC ``CreateRegisteredModel`` — 이름 규칙·저장 경로 위반 400, 이름 중복 409."""
    body = await request.json()
    full_name = f"{body.get('catalog_name', 'default')}.{body.get('schema_name', 'default')}.{body.get('name', '')}"
    logger.info("[UC] CreateRegisteredModel: %s (요청자 %s)", full_name, current.username)
    try:
        result = await service.create_registered_model(session, RegisteredModelCreate(
            name=full_name, description=body.get("comment"), storage_location=body.get("storage_location"),
        ), created_by=current.username)
        return JSONResponse(_model_to_uc(result))
    except (ValidationError, service.ModelValidationError) as e:
        # 이름 규칙·저장 경로 위반 (pydantic ValidationError 도 ValueError 라 먼저 잡는다)
        msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
        logger.warning("[UC] CreateRegisteredModel 거부(잘못된 값): %s: %s", full_name, msg)
        return JSONResponse({"error_code": "INVALID_PARAMETER_VALUE", "message": msg}, status_code=400)
    except ValueError as e:
        logger.warning("[UC] CreateRegisteredModel 충돌: %s: %s", full_name, e)
        return JSONResponse({"error_code": "ALREADY_EXISTS", "message": str(e)}, status_code=409)


@router.get("/models")
async def list_registered_models(request: Request, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """UC ``ListRegisteredModels`` — catalog·schema 가 모두 주어지면 그 접두어로 이름을 검색한다.

    페이지 토큰은 지원하지 않고 첫 페이지(``max_results`` 개, 기본 100)만 돌려준다.
    """
    max_results = int(request.query_params.get("max_results", "100"))
    catalog_name = request.query_params.get("catalog_name")
    schema_name = request.query_params.get("schema_name")
    search = f"{catalog_name}.{schema_name}." if catalog_name and schema_name else None
    logger.debug("[UC] ListRegisteredModels: search=%s max_results=%d", search, max_results)
    result = await service.list_registered_models(session, search=search, page=1, page_size=max_results)
    return JSONResponse({"registered_models": [_model_to_uc(m) for m in result.items]})


@router.post("/models/versions")
async def create_model_version(request: Request, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """UC ``CreateModelVersion`` — PENDING_REGISTRATION 버전을 만든다(모델 없음 404, 권한 없음 403)."""
    body = await request.json()
    full_name = f"{body.get('catalog_name', 'default')}.{body.get('schema_name', 'default')}.{body.get('model_name', '')}"
    logger.info("[UC] CreateModelVersion: %s, source=%s (요청자 %s)", full_name, body.get("source"), current.username)
    # 남의 모델에 버전을 추가하지 못하게 — 모델 정보 수정과 같은 기준(관리자·등록자·소유자)
    await assert_model_editor(current, session, full_name)
    try:
        result = await service.create_model_version(session, ModelVersionCreate(
            model_name=full_name, source=body.get("source"),
            run_id=body.get("run_id"), description=body.get("comment"),
        ))
        return JSONResponse(_version_to_uc(result))
    except ValueError as e:
        logger.warning("[UC] CreateModelVersion 실패: %s: %s", full_name, e)
        return JSONResponse({"error_code": "NOT_FOUND", "message": str(e)}, status_code=404)


@router.post("/temporary-model-version-credentials")
async def generate_temporary_credentials(request: Request, _user: CurrentUser):
    """UC 임시 자격증명 발급 — ``file://`` 로컬 저장소라 자격증명이 필요 없어 빈 객체를 돌려준다."""
    logger.info("[UC] GenerateTemporaryCredentials (로컬 스토리지에서는 no-op)")
    return JSONResponse({})


# ---------------------------------------------------------------------------
# 경로 기반 엔드포인트: {catalog}.{schema}.{model}
# 와일드카드 충돌을 피하기 위해 명시적 3-part 경로 파라미터 사용
# ---------------------------------------------------------------------------

@router.get("/models/{cat}.{sch}.{mdl}")
async def get_registered_model(cat: str, sch: str, mdl: str, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """UC ``GetRegisteredModel`` — 소프트 삭제된 모델은 404."""
    full_name = f"{cat}.{sch}.{mdl}"
    logger.info("[UC] GetRegisteredModel: %s", full_name)
    result = await service.get_registered_model_by_name(session, full_name)
    if not result:
        logger.warning("[UC] GetRegisteredModel 대상 없음: %s", full_name)
        return JSONResponse({"error_code": "NOT_FOUND", "message": f"Model '{full_name}' not found"}, status_code=404)
    return JSONResponse(_model_to_uc(result))


@router.patch("/models/{cat}.{sch}.{mdl}")
async def update_registered_model(cat: str, sch: str, mdl: str, request: Request, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """UC ``UpdateRegisteredModel`` — 설명(``comment``)·이름(``new_name``) 변경. 새 이름 중복 409."""
    full_name = f"{cat}.{sch}.{mdl}"
    await assert_model_editor(current, session, full_name)
    body = await request.json()
    logger.info("[UC] UpdateRegisteredModel: %s", full_name)
    try:
        result = await service.update_registered_model(session, full_name, RegisteredModelUpdate(
            description=body.get("comment"), name=body.get("new_name"),
        ))
    except ValueError as e:
        logger.warning("[UC] UpdateRegisteredModel 충돌: %s: %s", full_name, e)
        return JSONResponse({"error_code": "ALREADY_EXISTS", "message": str(e)}, status_code=409)
    if not result:
        logger.warning("[UC] UpdateRegisteredModel 대상 없음: %s", full_name)
        return JSONResponse({"error_code": "NOT_FOUND"}, status_code=404)
    return JSONResponse(_model_to_uc(result))


@router.delete("/models/{cat}.{sch}.{mdl}")
async def delete_registered_model(cat: str, sch: str, mdl: str, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """UC ``DeleteRegisteredModel`` — 소프트 삭제(진행 중 버전은 FAILED 로 마감)."""
    full_name = f"{cat}.{sch}.{mdl}"
    await assert_model_editor(current, session, full_name)
    logger.info("[UC] DeleteRegisteredModel: %s", full_name)
    if not await service.delete_registered_model(session, full_name):
        logger.warning("[UC] DeleteRegisteredModel 대상 없음: %s", full_name)
        return JSONResponse({"error_code": "NOT_FOUND"}, status_code=404)
    return JSONResponse({})


# ---------------------------------------------------------------------------
# ModelVersion 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/models/{cat}.{sch}.{mdl}/versions")
async def list_model_versions(
    cat: str, sch: str, mdl: str,
    request: Request, _user: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """UC ``ListModelVersions`` — 최신 버전부터 ``max_results`` 개. 모델이 없으면 빈 목록(404 아님)."""
    full_name = f"{cat}.{sch}.{mdl}"
    max_results = int(request.query_params.get("max_results", "100"))
    logger.debug("[UC] ListModelVersions: %s max_results=%d", full_name, max_results)
    result = await service.list_model_versions(session, full_name, page=1, page_size=max_results)
    if result is None:
        logger.debug("[UC] ListModelVersions 모델 없음 — 빈 목록 반환: %s", full_name)
        return JSONResponse({"model_versions": []})
    return JSONResponse({"model_versions": [_version_to_uc(v) for v in result.items]})


@router.get("/models/{cat}.{sch}.{mdl}/versions/{version}")
async def get_model_version(
    cat: str, sch: str, mdl: str, version: int,
    request: Request, _user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """UC ``GetModelVersion`` — MLflow 가 모델을 load 할 때 호출하므로 ``load`` 다운로드로 기록한다."""
    full_name = f"{cat}.{sch}.{mdl}"
    logger.info("[UC] GetModelVersion: %s v%d", full_name, version)
    result = await service.get_model_version(session, full_name, version)
    if not result:
        logger.warning("[UC] GetModelVersion 대상 없음: %s v%d", full_name, version)
        return JSONResponse({"error_code": "NOT_FOUND"}, status_code=404)

    # 다운로드 로깅(실패해도 응답에는 영향 없음)
    from app.models.download_log import log_download
    await log_download(
        session, full_name, version, "load",
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    return JSONResponse(_version_to_uc(result))


@router.patch("/models/{cat}.{sch}.{mdl}/versions/{version}/finalize")
async def finalize_model_version(
    cat: str, sch: str, mdl: str, version: int,
    request: Request, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """UC ``FinalizeModelVersion`` — 업로드가 끝난 PENDING 버전을 READY 로 전이(아티팩트 스캔·메타 파싱).

    모델·버전 없음 404, PENDING 이 아니면 409(``INVALID_STATE``).
    """
    full_name = f"{cat}.{sch}.{mdl}"
    await assert_model_editor(current, session, full_name)
    logger.info("[UC] FinalizeModelVersion: %s v%d", full_name, version)
    try:
        result = await service.finalize_model_version(
            session, full_name, version,
            ModelVersionFinalize(status=ModelVersionStatus.READY),
        )
        return JSONResponse(_version_to_uc(result))
    except LookupError as e:
        logger.warning("[UC] FinalizeModelVersion 대상 없음: %s v%d: %s", full_name, version, e)
        return JSONResponse({"error_code": "NOT_FOUND", "message": str(e)}, status_code=404)
    except ValueError as e:
        logger.warning("[UC] FinalizeModelVersion 상태 충돌: %s v%d: %s", full_name, version, e)
        return JSONResponse({"error_code": "INVALID_STATE", "message": str(e)}, status_code=409)


@router.patch("/models/{cat}.{sch}.{mdl}/versions/{version}")
async def update_model_version(
    cat: str, sch: str, mdl: str, version: int,
    request: Request, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """UC ``UpdateModelVersion`` — 버전 설명(``comment``)만 갱신."""
    full_name = f"{cat}.{sch}.{mdl}"
    await assert_model_editor(current, session, full_name)
    body = await request.json()
    logger.info("[UC] UpdateModelVersion: %s v%d", full_name, version)
    result = await service.update_model_version(
        session, full_name, version, ModelVersionUpdate(description=body.get("comment")),
    )
    if not result:
        logger.warning("[UC] UpdateModelVersion 대상 없음: %s v%d", full_name, version)
        return JSONResponse({"error_code": "NOT_FOUND"}, status_code=404)
    return JSONResponse(_version_to_uc(result))


@router.delete("/models/{cat}.{sch}.{mdl}/versions/{version}")
async def delete_model_version(
    cat: str, sch: str, mdl: str, version: int,
    current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """UC ``DeleteModelVersion`` — DB 행과 그 버전의 파일(디스크·오브젝트 스토리지)을 함께 지운다."""
    full_name = f"{cat}.{sch}.{mdl}"
    await assert_model_editor(current, session, full_name)
    logger.info("[UC] DeleteModelVersion: %s v%d", full_name, version)
    if not await service.delete_model_version(session, full_name, version):
        logger.warning("[UC] DeleteModelVersion 대상 없음: %s v%d", full_name, version)
        return JSONResponse({"error_code": "NOT_FOUND"}, status_code=404)
    return JSONResponse({})
