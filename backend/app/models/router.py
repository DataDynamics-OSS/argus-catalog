# SPDX-License-Identifier: Apache-2.0
"""MLflow 모델 레지스트리 라우터.

``/api/v1/models`` 경로에서 등록 모델(``RegisteredModel``)과 버전
(``ModelVersion``) 의 CRUD, 대시보드 통계, 모델 상세, 다운로드 통계,
스테이지/리니지/메트릭/모델 카드 관리 엔드포인트를 제공한다.

MLflow Tracking SDK 로부터의 ``log_model`` / ``register_model`` 호출은
이 라우터가 아니라 ``app/models/uc_compat.py`` 의 Unity Catalog 호환
API 가 처리한다.

로깅 정책:
- 모든 변형 동작(create / update / delete / finalize / stage / lineage /
  metrics / card)은 INFO 로 영향을 받은 식별자를 함께 기록한다.
- 단순 조회(GET) 요청 진입은 DEBUG 로 남긴다.
- 400 / 403 / 404 / 409 분기는 WARNING 으로 실패한 식별자를 남겨 호출자가 잘못된
  이름·버전을 던지는 흐름이 보이도록 한다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, OptionalUser, TokenUser
from app.core.database import get_session
from app.models import service
from app.models.schemas import (
    ModelDetailResponse,
    ModelDownloadStats,
    ModelStats,
    ModelVersionCreate,
    ModelVersionFinalize,
    ModelVersionResponse,
    ModelVersionUpdate,
    PaginatedModelSummaries,
    PaginatedModelVersions,
    RegisteredModelCreate,
    RegisteredModelResponse,
    RegisteredModelUpdate,
)
from app.permissions.router import require_feature

logger = logging.getLogger(__name__)


PRODUCTION_STAGE = "PRODUCTION"
PROMOTE_FEATURE = "models.promote"

router = APIRouter(prefix="/models", tags=["models"])

_RELATION_TYPES = ("TRAINING_DATA", "EVALUATION_DATA", "FEATURE_SOURCE")


async def assert_model_editor(current: TokenUser, session: AsyncSession, model_name: str):
    """모델 쓰기 가드 — 관리자, 모델 생성자, 또는 모델 소유자(owner)면 통과.

    모델 정보·스테이지·메트릭·모델 카드·리니지 변경이 모두 같은 기준을 쓴다.
    모델이 없으면 404, 권한이 없으면 403. 통과하면 ORM 모델을 돌려준다.
    """
    from sqlalchemy import select

    from app.models.models import RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.name == model_name, RegisteredModel.status != "deleted",
        )
    )).scalar_one_or_none()
    if not model:
        logger.warning("모델을 찾을 수 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    if current.is_admin or current.username in {model.created_by, model.owner} - {None, ""}:
        return model
    logger.warning("모델 쓰기 거부: %s (요청자 %s)", model_name, current.username)
    raise HTTPException(status_code=403, detail="모델의 소유자 또는 관리자만 수행할 수 있습니다.")


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=ModelStats)
async def get_model_stats(session: AsyncSession = Depends(get_session)):
    """대시보드 통계: 요약 카드·차트·다운로드/게시 추이."""
    logger.debug("GET /models/stats")
    return await service.get_model_stats(session)


# ---------------------------------------------------------------------------
# RegisteredModel 엔드포인트
# ---------------------------------------------------------------------------

@router.post("", response_model=RegisteredModelResponse)
async def create_registered_model(current: CurrentUser,
    req: RegisteredModelCreate, _perm=Depends(require_feature("models.register")), session: AsyncSession = Depends(get_session),
):
    """새 모델을 등록한다(``models.register`` 기능 권한 필요). 저장 경로 위반 400, 이름 중복 409."""
    logger.info("POST /models: name=%s", req.name)
    try:
        result = await service.create_registered_model(session, req, created_by=current.username)
        logger.info("모델 생성됨: %s (id=%d)", result.name, result.id)
        return result
    except service.ModelValidationError as e:
        logger.warning("POST /models 거부(잘못된 값): name=%s: %s", req.name, e)
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        logger.warning("POST /models 충돌: %s", e)
        raise HTTPException(status_code=409, detail=str(e))


@router.get("", response_model=PaginatedModelSummaries)
async def list_registered_models(
    user: OptionalUser,
    search: str | None = Query(None, description="Search by model name"),
    status: str | None = Query(None, description="Filter by latest version status"),
    python_version: str | None = Query(None, description="Filter by Python version"),
    sklearn_version: str | None = Query(None, description="Filter by sklearn version"),
    flavor: str | None = Query(None, description="Filter by MLflow flavor (sklearn, pytorch, …)"),
    deleted: bool = Query(False, description="소프트 삭제된 모델만 조회 (관리자 전용)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """모델 목록(최신 버전 상태·메타데이터 포함) 페이징 조회. ``deleted=true`` 는 관리자만."""
    logger.debug("GET /models: search=%s, status=%s, deleted=%s, page=%d", search, status, deleted, page)
    if deleted and not (user and user.is_admin):
        logger.warning("삭제된 모델 목록 조회 거부: user=%s", user.username if user else None)
        raise HTTPException(
            status_code=401 if user is None else 403,
            detail="삭제된 모델 목록은 관리자만 볼 수 있습니다.",
        )
    return await service.list_model_summaries(
        session, search=search, status=status,
        python_version=python_version, sklearn_version=sklearn_version,
        page=page, page_size=page_size, flavor=flavor, deleted=deleted,
    )


@router.get("/{model_name}/detail", response_model=ModelDetailResponse)
async def get_model_detail(
    model_name: str, session: AsyncSession = Depends(get_session),
):
    """모델 상세(최신 버전 메타데이터·다운로드 카운트 포함). 없거나 삭제된 모델은 404."""
    logger.debug("GET /models/%s/detail", model_name)
    detail = await service.get_model_detail(session, model_name)
    if not detail:
        logger.warning("모델을 찾을 수 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    return detail


@router.get("/{model_name}/download", response_model=ModelDownloadStats)
async def get_model_download_stats(
    model_name: str, session: AsyncSession = Depends(get_session),
):
    """모델 단위 다운로드 통계(일별 차트 + 최근 로그). 없거나 삭제된 모델은 404."""
    logger.debug("GET /models/%s/download", model_name)
    if not await service.get_registered_model_by_name(session, model_name):
        logger.warning("다운로드 통계 조회 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    return await service.get_model_download_stats(session, model_name)


@router.get("/{model_name}", response_model=RegisteredModelResponse)
async def get_registered_model(
    model_name: str, session: AsyncSession = Depends(get_session),
):
    """이름으로 모델 단건 조회(소프트 삭제된 모델은 404)."""
    logger.debug("GET /models/%s", model_name)
    model = await service.get_registered_model_by_name(session, model_name)
    if not model:
        logger.warning("모델을 찾을 수 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    return model


@router.patch("/{model_name}", response_model=RegisteredModelResponse)
async def update_registered_model(current: CurrentUser,
    model_name: str,
    req: RegisteredModelUpdate,
    session: AsyncSession = Depends(get_session),
):
    """모델 메타데이터(이름·설명·소유자) 부분 갱신(관리자·모델 등록자·소유자). 새 이름 중복 409."""
    logger.info("PATCH /models/%s", model_name)
    try:
        await assert_model_editor(current, session, model_name)
        model = await service.update_registered_model(session, model_name, req)
    except ValueError as e:
        logger.warning("PATCH /models/%s 충돌: %s", model_name, e)
        raise HTTPException(status_code=409, detail=str(e))
    if not model:
        logger.warning("갱신할 모델을 찾을 수 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    logger.info("모델 갱신됨: %s", model_name)
    return model


@router.delete("/{model_name}")
async def delete_registered_model(current: CurrentUser,
    model_name: str, session: AsyncSession = Depends(get_session),
):
    """모델 소프트 삭제(목록에서만 숨김, 아티팩트는 보존). 관리자·모델 등록자·소유자.

    등록 대기(PENDING_REGISTRATION) 버전은 FAILED_REGISTRATION 으로 마감된다."""
    logger.info("DELETE /models/%s (soft)", model_name)
    await assert_model_editor(current, session, model_name)
    if not await service.delete_registered_model(session, model_name):
        logger.warning("삭제할 모델을 찾을 수 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    logger.info("모델 소프트 삭제됨: %s", model_name)
    return {"status": "ok", "message": f"Model '{model_name}' deleted"}


@router.post("/{model_name}/restore")
async def restore_registered_model(
    model_name: str, _admin: AdminUser, session: AsyncSession = Depends(get_session),
):
    """소프트 삭제된 모델 복원 (관리자). 삭제된 모델이 아니면 404."""
    logger.info("POST /models/%s/restore", model_name)
    if not await service.restore_registered_model(session, model_name):
        logger.warning("복원할 삭제된 모델 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"삭제된 모델 '{model_name}'을(를) 찾을 수 없습니다.")
    return {"status": "ok", "message": f"Model '{model_name}' restored"}


@router.post("/hard-delete")
async def hard_delete_models(
    body: dict,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """모델 영구 삭제(관리자). DB(catalog_models·버전·등록 모델·다운로드 기록, 리니지·메트릭·카드는
    FK CASCADE) + 디스크/S3 아티팩트를 지운다. 소프트 삭제 여부와 무관하게 이름으로 찾는다.

    body: ``{"names": [모델 이름, ...]}``. 반환: ``{"deleted": [...], "not_found": [...]}``."""
    names: list[str] = body.get("names", [])
    if not names:
        logger.warning("영구 삭제 요청 거부(이름 없음)")
        raise HTTPException(status_code=400, detail="모델 이름이 제공되지 않았습니다.")

    logger.info("영구 삭제 요청: %d개 모델: %s", len(names), names)
    deleted: list[str] = []
    not_found: list[str] = []
    for name in names:
        if await service.hard_delete_registered_model(session, name):
            deleted.append(name)
        else:
            not_found.append(name)

    logger.info("영구 삭제 결과: deleted=%s, not_found=%s", deleted, not_found)
    return {"deleted": deleted, "not_found": not_found}


# ---------------------------------------------------------------------------
# ModelVersion 엔드포인트
# ---------------------------------------------------------------------------

@router.post("/{model_name}/versions", response_model=ModelVersionResponse)
async def create_model_version(current: CurrentUser,
    model_name: str,
    req: ModelVersionCreate,
    session: AsyncSession = Depends(get_session),
):
    """새 모델 버전 생성(초기 상태 PENDING_REGISTRATION).

    UC 호환 API(``POST /api/2.1/unity-catalog/models/versions``)와 같은 기준 — 관리자·모델 등록자·소유자만.
    """
    logger.info("POST /models/%s/versions: source=%s, run_id=%s", model_name, req.source, req.run_id)
    await assert_model_editor(current, session, model_name)
    req.model_name = model_name
    try:
        return await service.create_model_version(session, req)
    except ValueError as e:
        logger.warning("POST /models/%s/versions 실패: %s", model_name, e)
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{model_name}/versions", response_model=PaginatedModelVersions)
async def list_model_versions(
    model_name: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """모델의 전체 버전을 페이징 조회."""
    logger.debug("GET /models/%s/versions: page=%d", model_name, page)
    result = await service.list_model_versions(session, model_name, page=page, page_size=page_size)
    if result is None:
        logger.warning("버전 목록 조회할 모델을 찾을 수 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    return result


@router.get("/{model_name}/versions/{version}", response_model=ModelVersionResponse)
async def get_model_version(
    model_name: str, version: int, session: AsyncSession = Depends(get_session),
):
    """모델명 + 버전 번호로 단건 조회."""
    logger.debug("GET /models/%s/versions/%d", model_name, version)
    ver = await service.get_model_version(session, model_name, version)
    if not ver:
        logger.warning("버전을 찾을 수 없음: %s v%d", model_name, version)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'의 버전 {version}을(를) 찾을 수 없습니다.")
    return ver


@router.patch("/{model_name}/versions/{version}", response_model=ModelVersionResponse)
async def update_model_version(current: CurrentUser,
    model_name: str,
    version: int,
    req: ModelVersionUpdate,
    session: AsyncSession = Depends(get_session),
):
    """모델 버전 메타데이터(설명·source) 부분 갱신(관리자·모델 등록자·소유자)."""
    logger.info("PATCH /models/%s/versions/%d", model_name, version)
    await assert_model_editor(current, session, model_name)
    ver = await service.update_model_version(session, model_name, version, req)
    if not ver:
        logger.warning("갱신할 버전을 찾을 수 없음: %s v%d", model_name, version)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'의 버전 {version}을(를) 찾을 수 없습니다.")
    logger.info("버전 갱신됨: %s v%d", model_name, version)
    return ver


@router.patch("/{model_name}/versions/{version}/finalize", response_model=ModelVersionResponse)
async def finalize_model_version(current: CurrentUser,
    model_name: str,
    version: int,
    req: ModelVersionFinalize,
    session: AsyncSession = Depends(get_session),
):
    """모델 버전 확정: PENDING_REGISTRATION → READY 또는 FAILED(관리자·모델 등록자·소유자)."""
    logger.info("PATCH /models/%s/versions/%d/finalize: status=%s", model_name, version, req.status)
    await assert_model_editor(current, session, model_name)
    try:
        return await service.finalize_model_version(session, model_name, version, req)
    except LookupError as e:
        logger.warning("확정 실패 %s v%d: %s", model_name, version, e)
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        logger.warning("확정 실패 %s v%d: %s", model_name, version, e)
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{model_name}/versions/{version}")
async def delete_model_version(current: CurrentUser,
    model_name: str, version: int, session: AsyncSession = Depends(get_session),
):
    """모델 버전 삭제(관리자·모델 등록자·소유자)."""
    logger.info("DELETE /models/%s/versions/%d", model_name, version)
    await assert_model_editor(current, session, model_name)
    if not await service.delete_model_version(session, model_name, version):
        logger.warning("삭제할 버전을 찾을 수 없음: %s v%d", model_name, version)
        raise HTTPException(
            status_code=404,
            detail=f"모델 '{model_name}'의 버전 {version}을(를) 찾을 수 없습니다.",
        )
    logger.info("버전 삭제됨: %s v%d", model_name, version)
    return {"status": "ok", "message": f"Version {version} of model '{model_name}' deleted"}


# ---------------------------------------------------------------------------
# 스테이지 관리 — 버전 배포 라이프사이클
# NONE → STAGING → PRODUCTION → ARCHIVED
# ---------------------------------------------------------------------------

@router.put("/{model_name}/versions/{version}/stage")
async def update_version_stage(current: CurrentUser,
    model_name: str, version: int,
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    """버전 스테이지 변경(NONE / STAGING / PRODUCTION / ARCHIVED). 모델 소유자 또는 관리자.

    PRODUCTION 으로 올리거나 PRODUCTION 에서 내리는 변경은 ``models.promote`` 권한도 필요하다
    (명시 허용형 — 권한 관리에서 역할을 허용하지 않으면 관리자만).

    변경자는 요청 본문이 아니라 로그인 사용자로 기록한다. 대소문자는 구분하지 않는다."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.models import ModelVersion
    from app.models.schemas import MODEL_STAGES

    stage = str(body.get("stage") or "").strip().upper()
    if stage not in MODEL_STAGES:
        logger.warning("스테이지 변경 거부(유효하지 않은 스테이지): %s v%d → %s", model_name, version, stage)
        raise HTTPException(
            status_code=400,
            detail=f"유효하지 않은 스테이지: {body.get('stage')!r} (허용: {', '.join(MODEL_STAGES)})",
        )

    model = await assert_model_editor(current, session, model_name)

    ver = (await session.execute(
        select(ModelVersion).where(ModelVersion.model_id == model.id, ModelVersion.version == version)
    )).scalar_one_or_none()
    if not ver:
        logger.warning("스테이지 변경 실패(버전 없음): %s v%d", model_name, version)
        raise HTTPException(status_code=404, detail="버전을 찾을 수 없습니다.")

    # 운영(Production) 승격·해제는 운영에 쓰이는 버전을 바꾸므로 별도 권한 — 설정 안 하면 관리자만
    if PRODUCTION_STAGE in (stage, (ver.stage or "").upper()) and stage != (ver.stage or "").upper():
        from app.permissions.router import check_feature_granted

        if not await check_feature_granted(session, current, PROMOTE_FEATURE):
            logger.warning("스테이지 변경 거부(운영 승격 권한 없음): %s v%d %s → %s by %s",
                           model_name, version, ver.stage, stage, current.username)
            raise HTTPException(status_code=403, detail=f"기능 권한이 없습니다: {PROMOTE_FEATURE} (운영 단계 변경)")

    ver.stage = stage
    ver.stage_changed_at = datetime.now(timezone.utc)
    ver.stage_changed_by = current.username
    await session.commit()
    logger.info("스테이지 변경됨: %s v%d → %s (변경자 %s)", model_name, version, stage, current.username)
    return {
        "status": "ok", "model": model_name, "version": version, "stage": stage,
        "stage_changed_by": current.username, "stage_changed_at": ver.stage_changed_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# 모델-데이터셋 리니지 — 학습/평가 데이터 소스 추적
# ---------------------------------------------------------------------------

@router.post("/{model_name}/lineage")
async def add_model_dataset_lineage(current: CurrentUser,
    model_name: str, body: dict,
    session: AsyncSession = Depends(get_session),
):
    """모델과 학습/평가 데이터셋 연결(리니지 추가). 모델 소유자 또는 관리자.

    body: ``{"dataset_id": int, "model_version": int|null, "relation_type": "TRAINING_DATA"|
    "EVALUATION_DATA"|"FEATURE_SOURCE", "description": str|null}``"""
    from sqlalchemy import select

    from app.catalog.models import Dataset
    from app.models.models import ModelDatasetLineage, ModelVersion

    # bool 은 int 의 하위 타입이므로 별도로 거른다(true/false 를 1/0 으로 받지 않게)
    dataset_id = body.get("dataset_id")
    if not isinstance(dataset_id, int) or isinstance(dataset_id, bool):
        logger.warning("리니지 추가 거부(dataset_id 형식): model=%s", model_name)
        raise HTTPException(status_code=400, detail="dataset_id(정수)는 필수입니다.")
    relation_type = str(body.get("relation_type") or "TRAINING_DATA").upper()
    if relation_type not in _RELATION_TYPES:
        logger.warning("리니지 추가 거부(relation_type=%s): model=%s", relation_type, model_name)
        raise HTTPException(status_code=400, detail=f"relation_type 은 {', '.join(_RELATION_TYPES)} 중 하나여야 합니다.")
    model_version = body.get("model_version")
    if model_version is not None and (not isinstance(model_version, int) or isinstance(model_version, bool)):
        logger.warning("리니지 추가 거부(model_version 형식): model=%s", model_name)
        raise HTTPException(status_code=400, detail="model_version 은 정수 또는 null 이어야 합니다.")

    model = await assert_model_editor(current, session, model_name)

    if not (await session.execute(select(Dataset.id).where(Dataset.id == dataset_id))).first():
        logger.warning("리니지 추가 실패(데이터셋 없음): model=%s dataset_id=%d", model_name, dataset_id)
        raise HTTPException(status_code=404, detail=f"데이터셋(id={dataset_id})을 찾을 수 없습니다.")
    if model_version is not None and not (await session.execute(
        select(ModelVersion.id).where(ModelVersion.model_id == model.id, ModelVersion.version == model_version)
    )).first():
        logger.warning("리니지 추가 실패(버전 없음): model=%s v%d", model_name, model_version)
        raise HTTPException(status_code=404, detail=f"버전 v{model_version} 을 찾을 수 없습니다.")
    # 중복 판정 — model_version 이 null 이면 SQL 의 ``= NULL`` 이 항상 거짓이라 IS NULL 로 비교한다
    dup = (await session.execute(select(ModelDatasetLineage.id).where(
        ModelDatasetLineage.model_id == model.id,
        ModelDatasetLineage.dataset_id == dataset_id,
        ModelDatasetLineage.relation_type == relation_type,
        ModelDatasetLineage.model_version.is_(None) if model_version is None
        else ModelDatasetLineage.model_version == model_version,
    ))).first()
    if dup:
        logger.warning("리니지 추가 충돌(이미 연결됨): model=%s dataset_id=%d relation=%s",
                       model_name, dataset_id, relation_type)
        raise HTTPException(status_code=409, detail="이미 같은 데이터셋·관계·버전으로 연결되어 있습니다.")

    lineage = ModelDatasetLineage(
        model_id=model.id,
        model_version=model_version,
        dataset_id=dataset_id,
        relation_type=relation_type,
        description=body.get("description") or None,
    )
    session.add(lineage)
    await session.commit()
    await session.refresh(lineage)
    logger.info("모델-데이터셋 리니지 생성: %s → dataset_id=%d (%s, 요청자 %s)",
                model_name, dataset_id, relation_type, current.username)
    return {"id": lineage.id, "model": model_name, "dataset_id": dataset_id, "relation_type": lineage.relation_type}


@router.get("/{model_name}/lineage")
async def get_model_dataset_lineage(model_name: str, session: AsyncSession = Depends(get_session)):
    """모델에 연결된 데이터셋 리니지 목록(데이터셋 이름·데이터소스 유형 포함, 생성순). 모델 없으면 404."""
    from sqlalchemy import select

    from app.catalog.models import Dataset, Datasource
    from app.models.models import ModelDatasetLineage, RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name, RegisteredModel.status != "deleted")
    )).scalar_one_or_none()
    if not model:
        logger.warning("리니지 목록 조회 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")

    lineages = (await session.execute(
        select(ModelDatasetLineage).where(ModelDatasetLineage.model_id == model.id)
        .order_by(ModelDatasetLineage.id)
    )).scalars().all()

    results = []
    for l in lineages:
        ds = (await session.execute(
            select(Dataset.name, Datasource.type)
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(Dataset.id == l.dataset_id)
        )).first()
        results.append({
            "id": l.id,
            "model_version": l.model_version,
            "dataset_id": l.dataset_id,
            "dataset_name": ds[0] if ds else None,
            "datasource_type": ds[1] if ds else None,
            "relation_type": l.relation_type,
            "description": l.description,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        })
    return results


@router.delete("/{model_name}/lineage/{lineage_id}")
async def delete_model_dataset_lineage(current: CurrentUser,
    model_name: str, lineage_id: int, session: AsyncSession = Depends(get_session),
):
    """모델-데이터셋 리니지 링크 삭제. 모델 소유자 또는 관리자 (다른 모델의 링크는 404)."""
    from sqlalchemy import select

    from app.models.models import ModelDatasetLineage

    model = await assert_model_editor(current, session, model_name)
    l = (await session.execute(
        select(ModelDatasetLineage).where(
            ModelDatasetLineage.id == lineage_id, ModelDatasetLineage.model_id == model.id,
        )
    )).scalar_one_or_none()
    if not l:
        logger.warning("리니지 삭제 실패(대상 없음): id=%d", lineage_id)
        raise HTTPException(status_code=404, detail="리니지를 찾을 수 없습니다.")
    await session.delete(l)
    await session.commit()
    logger.info("모델-데이터셋 리니지 삭제: id=%d (model=%s)", lineage_id, model_name)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 모델 메트릭 — 버전별 성능 추적 및 비교
# ---------------------------------------------------------------------------

@router.post("/{model_name}/versions/{version}/metrics")
async def add_model_metrics(current: CurrentUser,
    model_name: str, version: int, body: dict,
    session: AsyncSession = Depends(get_session),
):
    """모델 버전에 메트릭을 추가/갱신 (모델 소유자 또는 관리자).
    body 예: {"metrics": {"accuracy": 0.95, "f1": 0.88}}. 같은 키는 값을 덮어쓴다(upsert)."""
    from sqlalchemy import select

    from app.models.models import ModelMetric

    metrics = body.get("metrics")
    if not isinstance(metrics, dict) or not metrics or not all(
        isinstance(k, str) and k and isinstance(v, (int, float)) and not isinstance(v, bool)
        for k, v in metrics.items()
    ):
        logger.warning("메트릭 기록 거부(형식 오류): %s v%d", model_name, version)
        raise HTTPException(status_code=400, detail="metrics 는 {이름: 숫자} 형식의 비어 있지 않은 객체여야 합니다.")

    model = await assert_model_editor(current, session, model_name)
    if not await service.get_model_version(session, model_name, version):
        logger.warning("메트릭 기록 실패(버전 없음): %s v%d", model_name, version)
        raise HTTPException(status_code=404, detail=f"버전 v{version} 을 찾을 수 없습니다.")

    for key, value in metrics.items():
        existing = (await session.execute(
            select(ModelMetric).where(
                ModelMetric.model_id == model.id,
                ModelMetric.version == version,
                ModelMetric.metric_key == key,
            )
        )).scalar_one_or_none()
        if existing:
            existing.metric_value = value
        else:
            session.add(ModelMetric(
                model_id=model.id, version=version,
                metric_key=key, metric_value=value,
            ))

    await session.commit()
    logger.info("메트릭 기록됨: %s v%d, 메트릭 %d개", model_name, version, len(metrics))
    return {"status": "ok", "model": model_name, "version": version, "metrics_count": len(metrics)}


@router.get("/{model_name}/metrics")
async def get_model_metrics(model_name: str, session: AsyncSession = Depends(get_session)):
    """모델 전 버전의 메트릭(버전 간 비교용) 조회. 반환: ``[{"version", "metrics": {키: 값}}]`` (버전 오름차순)."""
    from sqlalchemy import select

    from app.models.models import ModelMetric, RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name, RegisteredModel.status != "deleted")
    )).scalar_one_or_none()
    if not model:
        logger.warning("메트릭 목록 조회 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")

    metrics = (await session.execute(
        select(ModelMetric).where(ModelMetric.model_id == model.id)
        .order_by(ModelMetric.version, ModelMetric.metric_key)
    )).scalars().all()

    # 버전별로 그룹핑
    by_version: dict[int, dict] = {}
    for m in metrics:
        if m.version not in by_version:
            by_version[m.version] = {"version": m.version, "metrics": {}}
        by_version[m.version]["metrics"][m.metric_key] = float(m.metric_value)

    return list(by_version.values())


# ---------------------------------------------------------------------------
# 모델 카드 — 구조화된 거버넌스 문서
# ---------------------------------------------------------------------------

@router.get("/{model_name}/card")
async def get_model_card(model_name: str, session: AsyncSession = Depends(get_session)):
    """모델 카드(거버넌스 문서) 조회. 없으면 빈 필드 반환."""
    from sqlalchemy import select

    from app.models.models import ModelCard, RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name, RegisteredModel.status != "deleted")
    )).scalar_one_or_none()
    if not model:
        logger.warning("모델 카드 조회 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")

    card = (await session.execute(
        select(ModelCard).where(ModelCard.model_id == model.id)
    )).scalar_one_or_none()

    if not card:
        return {"model_id": model.id, "purpose": None, "performance": None,
                "limitations": None, "training_data": None, "framework": None,
                "license": None, "contact": None}

    return {
        "model_id": card.model_id, "purpose": card.purpose,
        "performance": card.performance, "limitations": card.limitations,
        "training_data": card.training_data, "framework": card.framework,
        "license": card.license, "contact": card.contact,
        "updated_at": card.updated_at.isoformat() if card.updated_at else None,
    }


@router.put("/{model_name}/card")
async def update_model_card(current: CurrentUser, model_name: str, body: dict, session: AsyncSession = Depends(get_session)):
    """모델 카드 생성 또는 갱신 (모델 소유자 또는 관리자).

    기존 카드는 body 에 있는 키만 바꾸고, 새 카드는 없는 키를 None 으로 만든다."""
    from sqlalchemy import select

    from app.models.models import ModelCard

    model = await assert_model_editor(current, session, model_name)

    card = (await session.execute(
        select(ModelCard).where(ModelCard.model_id == model.id)
    )).scalar_one_or_none()

    if card:
        for k in ("purpose", "performance", "limitations", "training_data", "framework", "license", "contact"):
            if k in body:
                setattr(card, k, body[k])
    else:
        card = ModelCard(model_id=model.id, **{k: body.get(k) for k in
            ("purpose", "performance", "limitations", "training_data", "framework", "license", "contact")})
        session.add(card)

    await session.commit()
    await session.refresh(card)
    logger.info("모델 카드 갱신됨: %s (요청자 %s)", model_name, current.username)
    return {"status": "ok", "model": model_name,
            "updated_at": card.updated_at.isoformat() if card.updated_at else None}
