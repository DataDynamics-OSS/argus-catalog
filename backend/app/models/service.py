"""MLflow 모델 레지스트리 서비스 레이어(비즈니스 로직).

Unity Catalog OSS 패턴을 따라 ``RegisteredModel`` 과 ``ModelVersion`` 의
CRUD, 대시보드 통계, 모델 상세, 다운로드 통계를 구현한다. 라우터는 이
모듈의 함수만 호출하고 HTTP 변환만 수행한다.

모델 이름 규칙: 3-part 형식이 필수 (catalog.schema.model_name).
  예) ``argus.ml.iris_classifier``

버전 라이프사이클: PENDING_REGISTRATION → READY / FAILED_REGISTRATION
  - ``create_model_version``  : PENDING_REGISTRATION 으로 생성
  - ``finalize_model_version``: 아티팩트 스캔 후 READY 또는 FAILED 로 전이

스토리지: ``file://`` URI 가 ``data_dir/model-artifacts/{name}/versions/{ver}/``
아래에 매핑됨. MLflow 의 ``LocalArtifactRepository`` 가 동작하려면 ``file://``
프리픽스가 반드시 있어야 한다 (없으면 클라우드 자격증명 조회로 폴백되어 실패).

확정 시점에 채워지는 감사 필드:
  - ``artifact_count`` / ``artifact_size`` : 파일시스템 스캔 결과
  - ``finished_at``                         : 완료 시각
  - ``status_message``                      : 실패 사유(있을 때만)

로깅 정책: 변경 동작(create/update/delete/finalize) 은 INFO 로 영향을
받은 모델명·버전을 기록한다. 버전 라이프사이클 이상(예: PENDING 이 아닌
버전을 finalize 시도, 디렉토리 미존재) 은 WARNING 으로 남긴다.
"""

import datetime as _dt
import logging
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import CatalogModel, ModelVersion, RegisteredModel
from app.models.schemas import (
    DataPoint,
    DownloadLogEntry,
    CatalogModelDetail,
    ModelDownloadStats,
    ModelDetailResponse,
    ModelSizeInfo,
    ModelStats,
    ModelSummary,
    ModelVersionCount,
    ModelVersionCreate,
    ModelVersionFinalize,
    ModelVersionResponse,
    ModelVersionStatusCount,
    ModelVersionUpdate,
    PaginatedModelSummaries,
    PaginatedModelVersions,
    PaginatedRegisteredModels,
    RegisteredModelCreate,
    RegisteredModelResponse,
    RegisteredModelUpdate,
)

logger = logging.getLogger(__name__)


def _model_artifacts_root() -> Path:
    """MLflow 모델 아티팩트 저장소 루트(``data_dir/model-artifacts``) 를 돌려준다."""
    return settings.data_dir / "model-artifacts"


def _generate_model_urn(name: str) -> str:
    """등록 모델의 URN 을 ``{name}.PROD.model`` 형식으로 생성한다."""
    return f"{name}.PROD.model"


# ---------------------------------------------------------------------------
# RegisteredModel 동작
# ---------------------------------------------------------------------------

async def get_model_created_by(session: AsyncSession, name: str) -> str | None:
    """모델 생성자 — 소유권 체크용."""
    return (await session.execute(
        select(RegisteredModel.created_by).where(RegisteredModel.name == name)
    )).scalar_one_or_none()


async def create_registered_model(
    session: AsyncSession, req: RegisteredModelCreate, created_by: str | None = None,
) -> RegisteredModelResponse:
    """새 모델 등록. 이름 중복 시 ``ValueError`` 발생."""
    # 이름 중복 검사 (소프트 삭제된 모델까지 포함해 unique 보장)
    existing = await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == req.name)
    )
    if existing.scalars().first():
        logger.warning("RegisteredModel 생성 거부(이름 중복): %s", req.name)
        raise ValueError(f"Model with name '{req.name}' already exists")

    raw_path = req.storage_location or str(_model_artifacts_root() / req.name)
    storage = raw_path if raw_path.startswith("file://") else f"file://{raw_path}"
    urn = _generate_model_urn(req.name)

    model = RegisteredModel(
        created_by=created_by,
        name=req.name,
        urn=urn,
        datasource_id=req.datasource_id,
        description=req.description,
        owner=req.owner,
        storage_location=storage,
        max_version_number=0,
        status="active",
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)

    # 아티팩트 디렉토리 생성
    try:
        Path(storage.replace("file://", "")).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("%s 의 아티팩트 디렉토리 생성 실패: %s", model.name, e)
    logger.info("RegisteredModel 생성됨: %s (id=%d, urn=%s)", model.name, model.id, model.urn)
    return RegisteredModelResponse.model_validate(model)


async def get_registered_model(
    session: AsyncSession, model_id: int,
) -> RegisteredModelResponse | None:
    result = await session.execute(
        select(RegisteredModel).where(RegisteredModel.id == model_id)
    )
    model = result.scalars().first()
    if not model:
        return None
    return RegisteredModelResponse.model_validate(model)


async def get_registered_model_by_name(
    session: AsyncSession, name: str,
) -> RegisteredModelResponse | None:
    """3-part 이름으로 모델 조회 (MLflow UC 플러그인에서도 사용). 삭제된 모델은 제외."""
    result = await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.name == name,
            RegisteredModel.status != "deleted",
        )
    )
    model = result.scalars().first()
    if not model:
        return None
    return RegisteredModelResponse.model_validate(model)


async def list_registered_models(
    session: AsyncSession,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedRegisteredModels:
    base = select(RegisteredModel).where(RegisteredModel.status != "deleted")

    if search:
        pattern = f"%{search}%"
        base = base.where(RegisteredModel.name.ilike(pattern))

    # 개수 집계
    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # 페이징
    offset = (page - 1) * page_size
    query = base.order_by(RegisteredModel.updated_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    items = [RegisteredModelResponse.model_validate(m) for m in result.scalars().all()]

    return PaginatedRegisteredModels(items=items, total=total, page=page, page_size=page_size)


async def list_model_summaries(
    session: AsyncSession,
    search: str | None = None,
    status: str | None = None,
    python_version: str | None = None,
    sklearn_version: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedModelSummaries:
    """최신 버전 상태와 ``catalog_models`` 메타데이터를 합쳐 모델 요약 목록을 반환.

    ``status`` / ``python_version`` / ``sklearn_version`` 필터는
    ``catalog_model_versions`` / ``catalog_models`` 조인이 필요해 SQL
    레벨이 아니라 post-query 단계에서 적용한다 (대량 결과에서는 비용 주의).
    """
    base = select(RegisteredModel).where(RegisteredModel.status != "deleted")
    if search:
        base = base.where(RegisteredModel.name.ilike(f"%{search}%"))

    # 조건에 맞는 모든 모델을 먼저 조회(post-filter 적용 전)
    query = base.order_by(RegisteredModel.updated_at.desc())
    result = await session.execute(query)
    models = result.scalars().all()

    # 모든 모델의 다운로드 카운트를 미리 로드
    from app.models.download_log import get_download_count_by_model
    download_counts = await get_download_count_by_model(session)

    # 조인 데이터를 합쳐 요약 구성
    all_items: list[ModelSummary] = []
    for m in models:
        # 최신 버전 상태 조회
        latest_ver = (await session.execute(
            select(ModelVersion.status).where(
                ModelVersion.model_id == m.id,
            ).order_by(ModelVersion.version.desc()).limit(1)
        )).scalar()

        # 최신 버전의 catalog_models 조회
        cm = (await session.execute(
            select(CatalogModel).where(
                CatalogModel.model_name == m.name,
            ).order_by(CatalogModel.version.desc()).limit(1)
        )).scalars().first()

        summary = ModelSummary(
            id=m.id,
            name=m.name,
            description=m.description,
            owner=m.owner,
            max_version_number=m.max_version_number,
            status=m.status,
            latest_version_status=latest_ver,
            sklearn_version=cm.sklearn_version if cm else None,
            python_version=cm.python_version if cm else None,
            model_size_bytes=cm.model_size_bytes if cm else None,
            download_count=download_counts.get(m.name, 0),
            updated_at=m.updated_at,
        )

        # post-query 필터 적용
        if status and latest_ver != status:
            continue
        if python_version and (summary.python_version or "") != python_version:
            continue
        if sklearn_version and (summary.sklearn_version or "") != sklearn_version:
            continue

        all_items.append(summary)

    # 필터링된 결과에 수동 페이징 적용
    total = len(all_items)
    offset = (page - 1) * page_size
    items = all_items[offset:offset + page_size]

    return PaginatedModelSummaries(items=items, total=total, page=page, page_size=page_size)


async def get_model_stats(session: AsyncSession) -> ModelStats:
    """MLflow 모델 대시보드 통계 집계.

    소프트 삭제(``status == "deleted"``) 된 모델과 그 버전은 모든 집계에서 제외한다.
    """
    # 활성 모델 ID 목록
    active_ids_q = select(RegisteredModel.id).where(RegisteredModel.status != "deleted")
    active_ids = (await session.execute(active_ids_q)).scalars().all()

    total_models = len(active_ids)

    if not active_ids:
        return ModelStats(
            total_models=0, total_versions=0,
            ready_models=0, ready_versions=0,
            pending_count=0, failed_count=0, total_download=0,
            status_distribution=[], model_sizes=[], versions_per_model=[],
            daily_download_1d=[], daily_download_7d=[], daily_download_30d=[],
            download_by_model={},
            total_publish=0,
            daily_publish_1d=[], daily_publish_7d=[], daily_publish_30d=[],
        )

    # 활성 모델의 버전만 대상으로
    active_ver_base = select(ModelVersion).where(ModelVersion.model_id.in_(active_ids))

    total_versions = (await session.execute(
        select(func.count()).select_from(active_ver_base.subquery())
    )).scalar() or 0

    # 상태별 개수(활성 모델만)
    ready_versions = (await session.execute(
        select(func.count()).where(
            ModelVersion.model_id.in_(active_ids),
            ModelVersion.status == "READY",
        )
    )).scalar() or 0

    pending = (await session.execute(
        select(func.count()).where(
            ModelVersion.model_id.in_(active_ids),
            ModelVersion.status == "PENDING_REGISTRATION",
        )
    )).scalar() or 0

    failed = (await session.execute(
        select(func.count()).where(
            ModelVersion.model_id.in_(active_ids),
            ModelVersion.status == "FAILED_REGISTRATION",
        )
    )).scalar() or 0

    # READY 모델: READY 버전을 하나 이상 가진 모델
    ready_model_ids = (await session.execute(
        select(ModelVersion.model_id).where(
            ModelVersion.model_id.in_(active_ids),
            ModelVersion.status == "READY",
        ).group_by(ModelVersion.model_id)
    )).scalars().all()
    ready_models = len(ready_model_ids)

    status_distribution = [
        ModelVersionStatusCount(status="READY", count=ready_versions),
        ModelVersionStatusCount(status="PENDING", count=pending),
        ModelVersionStatusCount(status="FAILED", count=failed),
    ]

    # 모델 크기(catalog_models 기준, 활성 모델만)
    active_names = (await session.execute(
        select(RegisteredModel.name).where(RegisteredModel.status != "deleted")
    )).scalars().all()

    cm_result = await session.execute(
        select(CatalogModel.model_name, CatalogModel.model_size_bytes)
        .where(
            CatalogModel.model_name.in_(active_names),
            CatalogModel.model_size_bytes.is_not(None),
        )
        .order_by(CatalogModel.model_size_bytes.desc())
    )
    seen: set[str] = set()
    model_sizes = []
    for name, size in cm_result.all():
        if name not in seen and size:
            seen.add(name)
            model_sizes.append(ModelSizeInfo(model_name=name, model_size_bytes=size))

    # 모델별 버전 수(활성만)
    ver_result = await session.execute(
        select(RegisteredModel.name, RegisteredModel.max_version_number)
        .where(RegisteredModel.status != "deleted")
        .order_by(RegisteredModel.max_version_number.desc())
    )
    versions_per_model = [
        ModelVersionCount(model_name=name, version_count=count)
        for name, count in ver_result.all()
    ]

    # 다운로드 통계
    from app.models.download_log import (
        get_total_download_count,
        get_hourly_download,
        get_daily_download,
        get_download_count_by_model,
    )
    total_download = await get_total_download_count(session)

    hourly_raw = await get_hourly_download(session, hours=24)
    daily_1d = [DataPoint(date=d["date"], count=d["count"]) for d in hourly_raw]

    daily_7d_raw = await get_daily_download(session, days=7)
    daily_7d = [DataPoint(date=d["date"], count=d["count"]) for d in daily_7d_raw]

    daily_30d_raw = await get_daily_download(session, days=30)
    daily_30d = [DataPoint(date=d["date"], count=d["count"]) for d in daily_30d_raw]

    download_by_model = await get_download_count_by_model(session)

    # 게시 통계
    from app.models.download_log import (
        get_total_publish_count,
        get_hourly_publish,
        get_daily_publish,
    )
    total_publish = await get_total_publish_count(session)

    pub_1d_raw = await get_hourly_publish(session, hours=24)
    pub_1d = [DataPoint(date=d["date"], count=d["count"]) for d in pub_1d_raw]

    pub_7d_raw = await get_daily_publish(session, days=7)
    pub_7d = [DataPoint(date=d["date"], count=d["count"]) for d in pub_7d_raw]

    pub_30d_raw = await get_daily_publish(session, days=30)
    pub_30d = [DataPoint(date=d["date"], count=d["count"]) for d in pub_30d_raw]

    return ModelStats(
        total_models=total_models,
        total_versions=total_versions,
        ready_models=ready_models,
        ready_versions=ready_versions,
        pending_count=pending,
        failed_count=failed,
        total_download=total_download,
        status_distribution=status_distribution,
        model_sizes=model_sizes,
        versions_per_model=versions_per_model,
        daily_download_1d=daily_1d,
        daily_download_7d=daily_7d,
        daily_download_30d=daily_30d,
        download_by_model=download_by_model,
        total_publish=total_publish,
        daily_publish_1d=pub_1d,
        daily_publish_7d=pub_7d,
        daily_publish_30d=pub_30d,
    )


async def get_model_detail(
    session: AsyncSession, name: str,
) -> ModelDetailResponse | None:
    """모델 상세 (최신 버전 메타데이터·다운로드 카운트 포함). 모델 없으면 None."""
    model = await _resolve_model(session, name)
    if not model:
        return None

    # 최신 버전 상태
    latest_ver = (await session.execute(
        select(ModelVersion.status).where(
            ModelVersion.model_id == model.id,
        ).order_by(ModelVersion.version.desc()).limit(1)
    )).scalar()

    # 최신 버전의 catalog_models
    cm = (await session.execute(
        select(CatalogModel).where(
            CatalogModel.model_name == name,
        ).order_by(CatalogModel.version.desc()).limit(1)
    )).scalars().first()

    catalog = None
    if cm:
        catalog = CatalogModelDetail(
            predict_fn=cm.predict_fn,
            python_version=cm.python_version,
            serialization_format=cm.serialization_format,
            sklearn_version=cm.sklearn_version,
            mlflow_version=cm.mlflow_version,
            mlflow_model_id=cm.mlflow_model_id,
            model_size_bytes=cm.model_size_bytes,
            utc_time_created=cm.utc_time_created,
            requirements=cm.requirements,
            conda=cm.conda,
            python_env=cm.python_env,
            source_type=cm.source_type,
        )

    # 다운로드 카운트
    from app.models.download_log import get_download_count_by_model
    download_counts = await get_download_count_by_model(session)

    return ModelDetailResponse(
        id=model.id,
        name=model.name,
        urn=model.urn,
        description=model.description,
        owner=model.owner,
        storage_type=model.storage_type,
        storage_location=model.storage_location,
        max_version_number=model.max_version_number,
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
        latest_version_status=latest_ver,
        catalog=catalog,
        download_count=download_counts.get(name, 0),
    )


async def get_model_download_stats(
    session: AsyncSession, name: str,
) -> ModelDownloadStats:
    """모델 단위 다운로드 통계(총합 + 최근 30일 차트 + 최근 50건 로그)."""
    from app.models.models import ModelDownloadLog

    # 이 모델의 총 다운로드 수
    total = (await session.execute(
        select(func.count()).where(ModelDownloadLog.model_name == name)
    )).scalar() or 0

    # 일별 다운로드(최근 30일)
    from datetime import timezone, timedelta
    since = _dt.datetime.now(timezone.utc) - timedelta(days=30)
    daily_result = await session.execute(
        select(
            func.date(ModelDownloadLog.downloaded_at).label("day"),
            func.count().label("count"),
        )
        .where(ModelDownloadLog.model_name == name, ModelDownloadLog.downloaded_at >= since)
        .group_by(func.date(ModelDownloadLog.downloaded_at))
        .order_by(func.date(ModelDownloadLog.downloaded_at))
    )
    daily = [DataPoint(date=str(r.day), count=r.count) for r in daily_result.all()]

    # 최근 로그(최근 50건)
    recent_result = await session.execute(
        select(ModelDownloadLog)
        .where(ModelDownloadLog.model_name == name)
        .order_by(ModelDownloadLog.downloaded_at.desc())
        .limit(50)
    )
    recent = [
        DownloadLogEntry(
            downloaded_at=r.downloaded_at,
            version=r.version,
            download_type=r.download_type,
            client_ip=r.client_ip,
            user_agent=r.user_agent,
        )
        for r in recent_result.scalars().all()
    ]

    return ModelDownloadStats(total_download=total, daily_download=daily, recent_logs=recent)


async def update_registered_model(
    session: AsyncSession, name: str, req: RegisteredModelUpdate,
) -> RegisteredModelResponse | None:
    """모델 메타데이터 부분 갱신. 이름 변경 시 중복 검사 + URN 재생성."""
    result = await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.name == name,
            RegisteredModel.status != "deleted",
        )
    )
    model = result.scalars().first()
    if not model:
        return None

    changed_fields: list[str] = []
    if req.name is not None:
        # 이름 변경 시 새 이름 중복 검사
        if req.name != model.name:
            dup = await session.execute(
                select(RegisteredModel).where(RegisteredModel.name == req.name)
            )
            if dup.scalars().first():
                logger.warning("RegisteredModel 이름 변경 거부(이름 중복): %s → %s", model.name, req.name)
                raise ValueError(f"Model with name '{req.name}' already exists")
            model.name = req.name
            model.urn = _generate_model_urn(req.name)
            changed_fields.append("name")
    if req.description is not None:
        model.description = req.description
        changed_fields.append("description")
    if req.owner is not None:
        model.owner = req.owner
        changed_fields.append("owner")

    await session.commit()
    await session.refresh(model)
    logger.info("RegisteredModel 갱신됨: %s (id=%d, fields=%s)", model.name, model.id, changed_fields)
    return RegisteredModelResponse.model_validate(model)


async def delete_registered_model(session: AsyncSession, name: str) -> bool:
    """모델 소프트 삭제. ``PENDING_REGISTRATION`` 상태 버전들은 FAILED 로 자동 마감."""
    result = await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.name == name,
            RegisteredModel.status != "deleted",
        )
    )
    model = result.scalars().first()
    if not model:
        return False

    model.status = "deleted"

    # PENDING 상태 버전을 FAILED 로 마감
    pending = await session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model.id,
            ModelVersion.status == "PENDING_REGISTRATION",
        )
    )
    for ver in pending.scalars().all():
        ver.status = "FAILED_REGISTRATION"
        ver.status_message = "Model deleted during registration"

    await session.commit()
    logger.info("RegisteredModel 소프트 삭제됨: %s (id=%d)", model.name, model.id)
    return True


async def hard_delete_registered_model(session: AsyncSession, name: str) -> bool:
    """모델 영구 삭제: 3개 DB 테이블(``catalog_models``/``model_versions``/``registered_models``) +
    디스크 아티팩트 디렉토리. 디스크 삭제 실패는 WARNING 으로 남기고 DB 삭제는 그대로 진행."""
    import shutil

    result = await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == name)
    )
    model = result.scalars().first()
    if not model:
        return False

    # catalog_models 행 삭제
    cm_result = await session.execute(
        select(CatalogModel).where(CatalogModel.model_name == name)
    )
    for cm in cm_result.scalars().all():
        await session.delete(cm)

    # model_versions 행 삭제
    mv_result = await session.execute(
        select(ModelVersion).where(ModelVersion.model_id == model.id)
    )
    for mv in mv_result.scalars().all():
        await session.delete(mv)

    # 등록 모델 삭제
    await session.delete(model)
    await session.commit()

    # 디스크의 아티팩트 디렉토리 삭제
    art_dir = _model_artifacts_root() / name
    if art_dir.exists():
        try:
            shutil.rmtree(art_dir)
            logger.info("아티팩트 디렉토리 삭제: %s", art_dir)
        except OSError as e:
            logger.warning("아티팩트 디렉토리 삭제 실패 %s: %s", art_dir, e)

    # S3/MinIO 아티팩트도 정리 (오브젝트 스토리지 사용 시) — 실패해도 DB 삭제는 유지
    try:
        from app.models import model_store
        deleted = await model_store.delete_model_files(name)
        logger.info("모델 %s 의 S3 객체 %d개 삭제", name, deleted)
    except Exception as e:  # noqa: BLE001 — 스토리지 미사용/연결 실패는 경고만
        logger.warning("%s 의 S3 아티팩트 삭제 실패: %s", name, e)

    logger.info("RegisteredModel 영구 삭제됨: %s (id=%d)", name, model.id)
    return True


# ---------------------------------------------------------------------------
# ModelVersion 동작
# ---------------------------------------------------------------------------

def _build_version_response(model: RegisteredModel, ver: ModelVersion) -> ModelVersionResponse:
    """ORM ``RegisteredModel`` / ``ModelVersion`` 쌍에서 응답 DTO 를 구성한다."""
    return ModelVersionResponse(
        id=ver.id,
        model_id=ver.model_id,
        model_name=model.name,
        version=ver.version,
        source=ver.source,
        run_id=ver.run_id,
        run_link=ver.run_link,
        description=ver.description,
        status=ver.status,
        status_message=ver.status_message,
        storage_location=ver.storage_location,
        artifact_count=ver.artifact_count or 0,
        artifact_size=ver.artifact_size or 0,
        finished_at=ver.finished_at,
        created_at=ver.created_at,
        updated_at=ver.updated_at,
        created_by=ver.created_by,
        updated_by=ver.updated_by,
    )


async def _resolve_model(session: AsyncSession, name: str) -> RegisteredModel | None:
    """삭제되지 않은 모델을 이름으로 조회. 없으면 ``None``."""
    result = await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.name == name,
            RegisteredModel.status != "deleted",
        )
    )
    return result.scalars().first()


async def create_model_version(
    session: AsyncSession, req: ModelVersionCreate,
) -> ModelVersionResponse:
    """``PENDING_REGISTRATION`` 상태로 새 모델 버전을 생성한다.

    MLflow 가 등록 모델 생성 직후 호출한다. 버전 번호는 ``max_version_number``
    를 원자적으로 증가시켜 부여하고, storage URI 는 ``file://`` 프리픽스를
    붙여 MLflow 의 ``LocalArtifactRepository`` 가 업로드 경로로 사용하도록 한다.
    """
    logger.info("모델 버전 생성: model=%s, source=%s, run_id=%s",
                req.model_name, req.source, req.run_id)

    model = await _resolve_model(session, req.model_name)
    if not model:
        logger.warning("버전 생성 실패(모델 없음): %s", req.model_name)
        raise ValueError(f"Model '{req.model_name}' not found")

    # 버전 번호를 원자적으로 증가
    model.max_version_number += 1
    new_version = model.max_version_number
    logger.info("버전 번호 부여: %s → v%d", model.name, new_version)

    # storage 위치 계산(경로 연산을 위해 file:// 제거, URI 용으로 다시 추가)
    base_path = model.storage_location.replace("file://", "") if model.storage_location else ""
    ver_path = str(Path(base_path) / "versions" / str(new_version))
    ver_storage = f"file://{ver_path}"

    ver = ModelVersion(
        model_id=model.id,
        version=new_version,
        source=req.source,
        run_id=req.run_id,
        run_link=req.run_link,
        description=req.description,
        status="PENDING_REGISTRATION",
        storage_location=ver_storage,
    )
    session.add(ver)
    await session.commit()
    await session.refresh(ver)

    # 아티팩트 디렉토리 생성
    try:
        Path(ver_path).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("%s v%d 의 버전 디렉토리 생성 실패: %s", model.name, new_version, e)
    logger.info("ModelVersion 생성됨: %s v%d (id=%d, status=PENDING_REGISTRATION)",
                model.name, new_version, ver.id)
    return _build_version_response(model, ver)


async def get_model_version(
    session: AsyncSession, model_name: str, version: int,
) -> ModelVersionResponse | None:
    """모델명 + 버전 번호로 단건 조회. 모델 또는 버전이 없으면 ``None``."""
    model = await _resolve_model(session, model_name)
    if not model:
        return None

    result = await session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model.id,
            ModelVersion.version == version,
        )
    )
    ver = result.scalars().first()
    if not ver:
        return None
    return _build_version_response(model, ver)


async def list_model_versions(
    session: AsyncSession,
    model_name: str,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedModelVersions | None:
    """모델 전 버전을 최신순으로 페이징 조회. 모델 없으면 ``None``."""
    model = await _resolve_model(session, model_name)
    if not model:
        return None

    base = select(ModelVersion).where(ModelVersion.model_id == model.id)

    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    offset = (page - 1) * page_size
    query = base.order_by(ModelVersion.version.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    items = [_build_version_response(model, v) for v in result.scalars().all()]

    return PaginatedModelVersions(items=items, total=total, page=page, page_size=page_size)


async def update_model_version(
    session: AsyncSession, model_name: str, version: int, req: ModelVersionUpdate,
) -> ModelVersionResponse | None:
    """버전 메타데이터(설명·source) 부분 갱신."""
    model = await _resolve_model(session, model_name)
    if not model:
        return None

    result = await session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model.id,
            ModelVersion.version == version,
        )
    )
    ver = result.scalars().first()
    if not ver:
        return None

    if req.description is not None:
        ver.description = req.description
    if req.source is not None:
        ver.source = req.source

    await session.commit()
    await session.refresh(ver)
    logger.info("ModelVersion 갱신됨: %s v%d", model.name, version)
    return _build_version_response(model, ver)


async def finalize_model_version(
    session: AsyncSession, model_name: str, version: int, req: ModelVersionFinalize,
) -> ModelVersionResponse:
    """버전을 ``PENDING_REGISTRATION`` → ``READY`` 또는 ``FAILED_REGISTRATION`` 으로 확정.

    MLflow 가 아티팩트 업로드 완료 후 호출한다. 확정 시점에:
      - 아티팩트 디렉토리를 스캔해 파일 수 / 총 크기 기록
      - ``finished_at`` 타임스탬프 기록
      - 실패 시 ``status_message`` 에 사유 저장

    이 감사 필드를 통해 DB 만으로 버전 상태를 판별할 수 있다:
      ``READY + artifact_count>0 + finished_at != NULL`` → 정상
      ``PENDING + finished_at = NULL + 오래된 created_at`` → 멈춤
    """
    logger.info("모델 버전 확정 중: %s v%d → %s", model_name, version, req.status.value)

    model = await _resolve_model(session, model_name)
    if not model:
        logger.warning("확정 실패(모델 없음): %s", model_name)
        raise ValueError(f"Model '{model_name}' not found")

    result = await session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model.id,
            ModelVersion.version == version,
        )
    )
    ver = result.scalars().first()
    if not ver:
        logger.warning("확정 실패(버전 없음): %s v%d", model_name, version)
        raise ValueError(f"Version {version} not found for model '{model_name}'")

    if ver.status != "PENDING_REGISTRATION":
        logger.warning("확정 거부(잘못된 상태): %s v%d 상태가 %s", model_name, version, ver.status)
        raise ValueError(
            f"Cannot finalize version {version}: current status is '{ver.status}', "
            "expected 'PENDING_REGISTRATION'"
        )

    if req.status.value not in ("READY", "FAILED_REGISTRATION"):
        logger.warning("확정 거부(유효하지 않은 대상 상태): %s v%d → %s", model_name, version, req.status.value)
        raise ValueError(f"Invalid finalize status: {req.status.value}")

    ver.status = req.status.value
    ver.status_message = req.status_message
    ver.finished_at = _dt.datetime.now(_dt.timezone.utc)

    # 디스크의 아티팩트 수 집계
    art_path = None
    if ver.storage_location:
        art_path = Path(ver.storage_location.replace("file://", ""))
        if art_path.exists():
            files = [f for f in art_path.rglob("*") if f.is_file()]
            ver.artifact_count = len(files)
            ver.artifact_size = sum(f.stat().st_size for f in files)

    # 아티팩트 파일을 파싱해 catalog_models 에 저장(READY 일 때만)
    if req.status.value == "READY" and art_path and art_path.exists():
        await _save_catalog_model(session, model.name, version, ver.id, art_path)

    await session.commit()
    await session.refresh(ver)
    logger.info("ModelVersion 확정됨: %s v%d → %s (artifacts=%d, size=%d)",
                model.name, version, ver.status,
                ver.artifact_count or 0, ver.artifact_size or 0)
    return _build_version_response(model, ver)


async def delete_model_version(
    session: AsyncSession, model_name: str, version: int,
) -> bool:
    """모델 버전 삭제. 모델/버전 없으면 False 반환."""
    model = await _resolve_model(session, model_name)
    if not model:
        return False

    result = await session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model.id,
            ModelVersion.version == version,
        )
    )
    ver = result.scalars().first()
    if not ver:
        return False

    await session.delete(ver)
    await session.commit()
    logger.info("ModelVersion 삭제됨: %s v%d", model.name, version)
    return True


# ---------------------------------------------------------------------------
# Catalog 모델 메타데이터 추출
# ---------------------------------------------------------------------------

def _read_file_text(path: Path) -> str | None:
    """텍스트 파일을 읽어 문자열로 반환. 파일이 없으면 ``None``."""
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def _parse_utc_to_local(utc_str: str | None) -> _dt.datetime | None:
    """UTC 문자열(``YYYY-mm-dd HH:MM:SS.ffffff``) 을 로컬 timezone 으로 변환. 파싱 실패 시 WARNING + None."""
    if not utc_str:
        return None
    try:
        utc_dt = _dt.datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=_dt.timezone.utc,
        )
        return utc_dt.astimezone()
    except (ValueError, TypeError):
        logger.warning("utc_time_created 파싱 실패: %s", utc_str)
        return None


def _extract_mlmodel_fields(art_path: Path) -> dict:
    """``MLmodel`` YAML 을 파싱해 메타데이터 필드(predict_fn, sklearn_version 등) 를 추출.

    파일이 없거나 파싱 실패하면 WARNING 로그 + 빈 dict 반환 (호출 측에서 부분 메타로 계속 진행)."""
    mlmodel_path = art_path / "MLmodel"
    if not mlmodel_path.is_file():
        logger.warning("%s 에 MLmodel 파일이 없습니다", art_path)
        return {}

    try:
        data = yaml.safe_load(mlmodel_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("%s 의 MLmodel YAML 파싱 실패: %s", art_path, e)
        return {}

    if not isinstance(data, dict):
        return {}

    # 최상위 필드
    utc_time_created = data.get("utc_time_created")
    model_size_bytes = data.get("model_size_bytes")
    mlflow_version = data.get("mlflow_version")
    model_id = data.get("model_uuid") or data.get("model_id")

    # Flavors — sklearn 또는 python_function 에서 추출
    flavors = data.get("flavors", {})

    sklearn_flavor = flavors.get("sklearn", {})
    sklearn_version = sklearn_flavor.get("sklearn_version")
    serialization_format = sklearn_flavor.get("serialization_format")

    python_fn = flavors.get("python_function", {})
    predict_fn = python_fn.get("predict_fn")
    python_version = python_fn.get("python_version")

    return {
        "predict_fn": predict_fn,
        "python_version": python_version,
        "serialization_format": serialization_format,
        "sklearn_version": sklearn_version,
        "mlflow_version": mlflow_version,
        "mlflow_model_id": model_id,
        "model_size_bytes": int(model_size_bytes) if model_size_bytes is not None else None,
        "utc_time_created": utc_time_created,
        "time_created": _parse_utc_to_local(utc_time_created),
    }


async def _save_catalog_model(
    session: AsyncSession,
    model_name: str,
    version: int,
    model_version_id: int,
    art_path: Path,
) -> None:
    """아티팩트 파일(``MLmodel`` YAML, ``requirements.txt``, ``conda.yaml`` 등) 에서
    메타데이터를 추출해 ``catalog_models`` 테이블에 저장한다. 실패는 호출 측에서 무시(WARNING)."""
    try:
        # 원본 텍스트 파일 읽기
        requirements = _read_file_text(art_path / "requirements.txt")
        conda = _read_file_text(art_path / "conda.yaml")
        python_env = _read_file_text(art_path / "python_env.yaml")

        # MLmodel YAML 파싱
        ml_fields = _extract_mlmodel_fields(art_path)

        catalog_model = CatalogModel(
            model_version_id=model_version_id,
            model_name=model_name,
            version=version,
            requirements=requirements,
            conda=conda,
            python_env=python_env,
            **ml_fields,
        )
        session.add(catalog_model)
        logger.info(
            "CatalogModel 저장됨: %s v%d (predict_fn=%s, sklearn=%s, mlflow=%s)",
            model_name, version,
            ml_fields.get("predict_fn"),
            ml_fields.get("sklearn_version"),
            ml_fields.get("mlflow_version"),
        )
    except Exception as e:
        logger.error("%s v%d 의 catalog_model 저장 실패: %s", model_name, version, e)
