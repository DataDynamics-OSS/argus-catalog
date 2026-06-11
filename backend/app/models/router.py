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
- 404 / 409 분기는 WARNING 으로 실패한 식별자를 남겨 호출자가 잘못된
  이름·버전을 던지는 흐름이 보이도록 한다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, assert_owner_or_admin
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/models", tags=["models"])


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=ModelStats)
async def get_model_stats(session: AsyncSession = Depends(get_session)):
    """대시보드 통계: 요약 카드·차트·다운로드/게시 추이."""
    logger.info("GET /models/stats")
    return await service.get_model_stats(session)


# ---------------------------------------------------------------------------
# RegisteredModel 엔드포인트
# ---------------------------------------------------------------------------

@router.post("", response_model=RegisteredModelResponse)
async def create_registered_model(current: CurrentUser,
    req: RegisteredModelCreate, session: AsyncSession = Depends(get_session),
):
    """새 모델을 등록한다(이름 중복 시 409)."""
    logger.info("POST /models: name=%s", req.name)
    try:
        result = await service.create_registered_model(session, req, created_by=current.username)
        logger.info("모델 생성됨: %s (id=%d)", result.name, result.id)
        return result
    except ValueError as e:
        logger.warning("POST /models 충돌: %s", e)
        raise HTTPException(status_code=409, detail=str(e))


@router.get("", response_model=PaginatedModelSummaries)
async def list_registered_models(
    search: str | None = Query(None, description="Search by model name"),
    status: str | None = Query(None, description="Filter by latest version status"),
    python_version: str | None = Query(None, description="Filter by Python version"),
    sklearn_version: str | None = Query(None, description="Filter by sklearn version"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """모델 목록(최신 버전 상태·메타데이터 포함) 페이징 조회."""
    logger.info("GET /models: search=%s, status=%s, page=%d", search, status, page)
    return await service.list_model_summaries(
        session, search=search, status=status,
        python_version=python_version, sklearn_version=sklearn_version,
        page=page, page_size=page_size,
    )


@router.get("/{model_name}/detail", response_model=ModelDetailResponse)
async def get_model_detail(
    model_name: str, session: AsyncSession = Depends(get_session),
):
    """모델 상세(최신 버전 메타데이터·다운로드 카운트 포함)."""
    logger.info("GET /models/%s/detail", model_name)
    detail = await service.get_model_detail(session, model_name)
    if not detail:
        logger.warning("모델을 찾을 수 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    return detail


@router.get("/{model_name}/download", response_model=ModelDownloadStats)
async def get_model_download_stats(
    model_name: str, session: AsyncSession = Depends(get_session),
):
    """모델 단위 다운로드 통계(일별 차트 + 최근 로그)."""
    logger.info("GET /models/%s/download", model_name)
    return await service.get_model_download_stats(session, model_name)


@router.get("/{model_name}", response_model=RegisteredModelResponse)
async def get_registered_model(
    model_name: str, session: AsyncSession = Depends(get_session),
):
    """이름으로 모델 단건 조회."""
    logger.info("GET /models/%s", model_name)
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
    """모델 메타데이터(이름·설명·소유자) 부분 갱신."""
    logger.info("PATCH /models/%s", model_name)
    try:
        assert_owner_or_admin(current, await service.get_model_created_by(session, model_name), "모델")
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
    """모델 소프트 삭제(목록에서만 숨김, 아티팩트는 보존)."""
    logger.info("DELETE /models/%s (soft)", model_name)
    assert_owner_or_admin(current, await service.get_model_created_by(session, model_name), "모델")
    if not await service.delete_registered_model(session, model_name):
        logger.warning("삭제할 모델을 찾을 수 없음: %s", model_name)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
    logger.info("모델 소프트 삭제됨: %s", model_name)
    return {"status": "ok", "message": f"Model '{model_name}' deleted"}


@router.post("/hard-delete")
async def hard_delete_models(
    body: dict,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """모델 영구 삭제(DB 3테이블 + 디스크/S3 아티팩트). 관리자 권한 필요."""
    names: list[str] = body.get("names", [])
    if not names:
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
async def create_model_version(_guard: AdminUser,
    model_name: str,
    req: ModelVersionCreate,
    session: AsyncSession = Depends(get_session),
):
    """새 모델 버전 생성(초기 상태 PENDING_REGISTRATION)."""
    logger.info("POST /models/%s/versions: source=%s, run_id=%s", model_name, req.source, req.run_id)
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
    logger.info("GET /models/%s/versions: page=%d", model_name, page)
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
    logger.info("GET /models/%s/versions/%d", model_name, version)
    ver = await service.get_model_version(session, model_name, version)
    if not ver:
        logger.warning("버전을 찾을 수 없음: %s v%d", model_name, version)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'의 버전 {version}을(를) 찾을 수 없습니다.")
    return ver


@router.patch("/{model_name}/versions/{version}", response_model=ModelVersionResponse)
async def update_model_version(_guard: AdminUser,
    model_name: str,
    version: int,
    req: ModelVersionUpdate,
    session: AsyncSession = Depends(get_session),
):
    """모델 버전 메타데이터(설명·source) 부분 갱신."""
    logger.info("PATCH /models/%s/versions/%d", model_name, version)
    ver = await service.update_model_version(session, model_name, version, req)
    if not ver:
        logger.warning("갱신할 버전을 찾을 수 없음: %s v%d", model_name, version)
        raise HTTPException(status_code=404, detail=f"모델 '{model_name}'의 버전 {version}을(를) 찾을 수 없습니다.")
    logger.info("버전 갱신됨: %s v%d", model_name, version)
    return ver


@router.patch("/{model_name}/versions/{version}/finalize", response_model=ModelVersionResponse)
async def finalize_model_version(_guard: AdminUser,
    model_name: str,
    version: int,
    req: ModelVersionFinalize,
    session: AsyncSession = Depends(get_session),
):
    """모델 버전 확정: PENDING_REGISTRATION → READY 또는 FAILED."""
    logger.info("PATCH /models/%s/versions/%d/finalize: status=%s", model_name, version, req.status)
    try:
        return await service.finalize_model_version(session, model_name, version, req)
    except ValueError as e:
        logger.warning("확정 실패 %s v%d: %s", model_name, version, e)
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{model_name}/versions/{version}")
async def delete_model_version(_guard: AdminUser,
    model_name: str, version: int, session: AsyncSession = Depends(get_session),
):
    """모델 버전 삭제."""
    logger.info("DELETE /models/%s/versions/%d", model_name, version)
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
async def update_version_stage(_guard: AdminUser,
    model_name: str, version: int,
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    """버전 스테이지 변경(NONE → STAGING → PRODUCTION → ARCHIVED)."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.models import ModelVersion, RegisteredModel

    stage = body.get("stage", "NONE")
    changed_by = body.get("changed_by", "")

    if stage not in ("NONE", "STAGING", "PRODUCTION", "ARCHIVED"):
        logger.warning("스테이지 변경 거부(유효하지 않은 스테이지): %s v%d → %s", model_name, version, stage)
        raise HTTPException(status_code=400, detail=f"유효하지 않은 스테이지: {stage}")

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name)
    )).scalar_one_or_none()
    if not model:
        logger.warning("스테이지 변경 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")

    ver = (await session.execute(
        select(ModelVersion).where(ModelVersion.model_id == model.id, ModelVersion.version == version)
    )).scalar_one_or_none()
    if not ver:
        logger.warning("스테이지 변경 실패(버전 없음): %s v%d", model_name, version)
        raise HTTPException(status_code=404, detail="버전을 찾을 수 없습니다.")

    ver.stage = stage
    ver.stage_changed_at = datetime.now(timezone.utc)
    ver.stage_changed_by = changed_by
    await session.commit()
    logger.info("스테이지 변경됨: %s v%d → %s (변경자 %s)", model_name, version, stage, changed_by)
    return {"status": "ok", "model": model_name, "version": version, "stage": stage}


# ---------------------------------------------------------------------------
# 모델-데이터셋 리니지 — 학습/평가 데이터 소스 추적
# ---------------------------------------------------------------------------

@router.post("/{model_name}/lineage")
async def add_model_dataset_lineage(_guard: AdminUser,
    model_name: str, body: dict,
    session: AsyncSession = Depends(get_session),
):
    """모델과 학습/평가 데이터셋 연결(리니지 추가)."""
    from sqlalchemy import select

    from app.models.models import ModelDatasetLineage, RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name)
    )).scalar_one_or_none()
    if not model:
        logger.warning("리니지 추가 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")

    lineage = ModelDatasetLineage(
        model_id=model.id,
        model_version=body.get("model_version"),
        dataset_id=body["dataset_id"],
        relation_type=body.get("relation_type", "TRAINING_DATA"),
        description=body.get("description"),
    )
    session.add(lineage)
    await session.commit()
    await session.refresh(lineage)
    logger.info("모델-데이터셋 리니지 생성: %s → dataset_id=%d", model_name, body["dataset_id"])
    return {"id": lineage.id, "model": model_name, "dataset_id": body["dataset_id"], "relation_type": lineage.relation_type}


@router.get("/{model_name}/lineage")
async def get_model_dataset_lineage(model_name: str, session: AsyncSession = Depends(get_session)):
    """모델에 연결된 데이터셋 리니지 목록."""
    from sqlalchemy import select

    from app.catalog.models import Dataset, Datasource
    from app.models.models import ModelDatasetLineage, RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name)
    )).scalar_one_or_none()
    if not model:
        logger.warning("리니지 목록 조회 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")

    lineages = (await session.execute(
        select(ModelDatasetLineage).where(ModelDatasetLineage.model_id == model.id)
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
async def delete_model_dataset_lineage(_guard: AdminUser,
    model_name: str, lineage_id: int, session: AsyncSession = Depends(get_session),
):
    """모델-데이터셋 리니지 링크 삭제."""
    from sqlalchemy import select

    from app.models.models import ModelDatasetLineage

    l = (await session.execute(
        select(ModelDatasetLineage).where(ModelDatasetLineage.id == lineage_id)
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
async def add_model_metrics(_guard: AdminUser,
    model_name: str, version: int, body: dict,
    session: AsyncSession = Depends(get_session),
):
    """모델 버전에 메트릭을 추가/갱신. body 예: {"metrics": {"accuracy": 0.95, "f1": 0.88}}."""
    from sqlalchemy import select

    from app.models.models import ModelMetric, RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name)
    )).scalar_one_or_none()
    if not model:
        logger.warning("메트릭 추가 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")

    metrics = body.get("metrics", {})
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
    """모델 전 버전의 메트릭(버전 간 비교용) 조회."""
    from sqlalchemy import select

    from app.models.models import ModelMetric, RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name)
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
        select(RegisteredModel).where(RegisteredModel.name == model_name)
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
async def update_model_card(_guard: AdminUser, model_name: str, body: dict, session: AsyncSession = Depends(get_session)):
    """모델 카드 생성 또는 갱신."""
    from sqlalchemy import select

    from app.models.models import ModelCard, RegisteredModel

    model = (await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == model_name)
    )).scalar_one_or_none()
    if not model:
        logger.warning("모델 카드 갱신 실패(모델 없음): %s", model_name)
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")

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
    logger.info("모델 카드 갱신됨: %s", model_name)
    return {"status": "ok", "model": model_name}
