# SPDX-License-Identifier: Apache-2.0
"""MLflow 모델 레지스트리 서비스 레이어(비즈니스 로직).

Unity Catalog OSS 패턴을 따라 ``RegisteredModel`` 과 ``ModelVersion`` 의
CRUD, 대시보드 통계, 모델 상세, 다운로드 통계를 구현한다. 라우터는 이
모듈의 함수만 호출하고 HTTP 변환만 수행한다.

모델 이름 규칙: MLflow(UC 호환 API)는 3-part 형식(catalog.schema.model_name)을 쓴다.
  예) ``argus.ml.iris_classifier``
  이 모듈은 3-part 를 강제하지 않는다 — 문자 규칙은 ``schemas.validate_model_name`` 이 검사한다.

버전 라이프사이클: PENDING_REGISTRATION → READY / FAILED_REGISTRATION
  - ``create_model_version``  : PENDING_REGISTRATION 으로 생성
  - ``finalize_model_version``: 아티팩트 스캔 후 READY 또는 FAILED 로 전이

스토리지: 로컬 모델은 ``file://`` URI 가 ``data_dir/model-artifacts/{name}/versions/{ver}/``
아래에 매핑됨. MLflow 의 ``LocalArtifactRepository`` 가 동작하려면 ``file://``
프리픽스가 반드시 있어야 한다 (없으면 클라우드 자격증명 조회로 폴백되어 실패).
오브젝트 스토리지(S3/MinIO) 모델은 model-store 키 규칙 ``s3://{bucket}/{name}/v{ver}/`` 을 쓴다.

확정 시점에 채워지는 감사 필드:
  - ``artifact_count`` / ``artifact_size`` : 파일시스템 스캔 결과(S3 버전은 ``record_store_artifacts`` /
    ``finalize_store_version`` 이 기록)
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
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import settings
from app.core.sql import LIKE_ESCAPE, like_contains
from app.models.models import CatalogModel, ModelVersion, RegisteredModel
from app.models.schemas import (
    CatalogModelDetail,
    DataPoint,
    DownloadLogEntry,
    ModelDetailResponse,
    ModelDownloadStats,
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
    normalize_stage,
)

logger = logging.getLogger(__name__)


class ModelValidationError(ValueError):
    """요청 값이 규칙에 맞지 않음(HTTP 400) — 이름 중복(409)과 구분한다."""


def _model_artifacts_root() -> Path:
    """MLflow 모델 아티팩트 저장소 루트(``data_dir/model-artifacts``) 를 돌려준다."""
    return settings.data_dir / "model-artifacts"


def _local_path(storage: str | None) -> Path | None:
    """``file://`` 또는 절대경로 저장소 URI 를 로컬 경로로. 원격(s3:// 등)·빈 값은 None."""
    if not storage:
        return None
    if storage.startswith("file://"):
        return Path(storage[len("file://"):])
    if "://" in storage:
        return None
    return Path(storage)


def _is_s3_model(model: RegisteredModel) -> bool:
    """모델 아티팩트가 오브젝트 스토리지(S3/MinIO)에 있는지."""
    return model.storage_type == "s3" or (model.storage_location or "").startswith("s3://")


def _s3_version_uri(model: RegisteredModel, version: int) -> str:
    """model-store 의 버전 키 프리픽스(``{name}/v{ver}/``)에 대응하는 S3 URI."""
    bucket = model.bucket_name or settings.os_bucket
    return f"s3://{bucket}/{model.name}/v{version}/"


def _is_under(path: Path, root: Path) -> bool:
    """``path`` 가 ``root`` 와 같거나 그 하위인지 (심볼릭 링크·``..`` 해석 후 비교)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def files_browser_path(storage: str | None) -> str | None:
    """저장소 URI 를 MLflow 모델 파일 브라우저 기준 경로(``/argus.ml.x/versions/1``)로 변환.

    저장소가 ``model-artifacts`` 루트 밖(또는 원격)이면 브라우저로 열 수 없으므로 None."""
    p = _local_path(storage)
    if p is None:
        return None
    root = _model_artifacts_root()
    try:
        rel = p.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return "/" + rel.as_posix() if rel.as_posix() != "." else "/"


def _version_files_present(storage: str | None) -> bool | None:
    """로컬 버전 디렉터리에 파일이 하나라도 있는지. 원격 저장소·경로 없음은 None(확인 불가)."""
    p = _local_path(storage)
    if p is None:
        return None
    try:
        return p.is_dir() and any(f.is_file() for f in p.rglob("*"))
    except OSError:
        return False


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
    """새 모델 등록. 이름 중복(소프트 삭제 모델·OCI 모델 포함) 시 ``ValueError``,
    저장 경로가 ``model-artifacts`` 루트 하위가 아니면 ``ModelValidationError``.

    부작용: 로컬 아티팩트 디렉터리를 만든다(실패해도 등록은 유지, WARNING).
    """
    # 이름 중복 검사 (소프트 삭제된 모델까지 포함해 unique 보장)
    existing = await session.execute(
        select(RegisteredModel).where(RegisteredModel.name == req.name)
    )
    dup = existing.scalars().first()
    if dup:
        logger.warning("RegisteredModel 생성 거부(이름 중복): %s (status=%s)", req.name, dup.status)
        if dup.status == "deleted":
            raise ValueError(
                f"Model with name '{req.name}' already exists (삭제된 모델 — 관리자가 '삭제된 모델'에서 복원하거나 영구 삭제해야 같은 이름을 쓸 수 있습니다)"
            )
        raise ValueError(f"Model with name '{req.name}' already exists")
    # OCI 모델 허브와 같은 S3 키 프리픽스(``{name}/v{n}/``)를 쓰므로 이름이 겹치지 않게 한다
    from app.oci_hub.models import OciModel
    if (await session.execute(select(OciModel.id).where(OciModel.name == req.name))).first():
        logger.warning("RegisteredModel 생성 거부(OCI 모델과 이름 중복): %s", req.name)
        raise ValueError(
            f"OCI 모델 허브에 '{req.name}' 모델이 있습니다. 두 레지스트리는 같은 저장소 경로를 쓰므로 다른 이름을 사용하세요."
        )

    # 저장 경로는 model-artifacts 루트 아래만 허용 — 임의 경로에 디렉터리를 만들거나,
    # 나중에 하드 삭제가 루트 밖을 지우는 일을 막는다.
    root = _model_artifacts_root()
    if req.storage_location:
        local = _local_path(req.storage_location)
        if local is None or not local.is_absolute() or not _is_under(local, root) or local.resolve() == root.resolve():
            logger.warning("RegisteredModel 생성 거부(저장 경로가 루트 밖): %s → %s", req.name, req.storage_location)
            raise ModelValidationError(
                f"storage_location 은 모델 저장소 루트({root}) 아래 경로만 지정할 수 있습니다."
            )
        raw_path = str(local)
    else:
        raw_path = str(root / req.name)
    storage = f"file://{raw_path}"
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
    """ID 로 모델 조회(소프트 삭제된 모델도 포함). 없으면 ``None``."""
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
    """삭제되지 않은 모델을 최근 수정순으로 페이징 조회(UC 호환 API 용). ``search`` 는 이름 부분 일치."""
    base = select(RegisteredModel).where(RegisteredModel.status != "deleted")

    if search:
        pattern = like_contains(search)
        base = base.where(RegisteredModel.name.ilike(pattern, escape=LIKE_ESCAPE))

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
    flavor: str | None = None,
    deleted: bool = False,
) -> PaginatedModelSummaries:
    """최신 버전 상태와 ``catalog_models`` 메타데이터를 합쳐 모델 요약 목록을 반환.

    ``status`` / ``python_version`` / ``sklearn_version`` / ``flavor`` 필터는
    ``catalog_model_versions`` / ``catalog_models`` 조인이 필요해 SQL
    레벨이 아니라 post-query 단계에서 적용한다 (모델마다 추가 쿼리 2회 — 대량 결과에서는 비용 주의).
    ``deleted=True`` 면 소프트 삭제된 모델만 돌려준다(관리자의 복원·영구 삭제 화면용).
    """
    base = select(RegisteredModel).where(
        RegisteredModel.status == "deleted" if deleted else RegisteredModel.status != "deleted"
    )
    if search:
        base = base.where(RegisteredModel.name.ilike(like_contains(search), escape=LIKE_ESCAPE))

    # 조건에 맞는 모든 모델을 먼저 조회(post-filter 적용 전)
    query = base.order_by(RegisteredModel.updated_at.desc())
    result = await session.execute(query)
    models = result.scalars().all()

    # 모든 모델의 다운로드 카운트를 미리 로드
    from app.models.download_log import get_download_count_by_model
    download_counts = await get_download_count_by_model(session)

    # 모델별 운영(PRODUCTION) 스테이지 최고 버전 — 목록의 "운영 버전" 열
    prod_rows = (await session.execute(
        select(ModelVersion.model_id, func.max(ModelVersion.version))
        .where(ModelVersion.stage == "PRODUCTION")
        .group_by(ModelVersion.model_id)
    )).all()
    production_by_model = {mid: ver for mid, ver in prod_rows}

    # 조인 데이터를 합쳐 요약 구성
    all_items: list[ModelSummary] = []
    for m in models:
        # 최신 버전 상태·크기 조회
        latest_row = (await session.execute(
            select(ModelVersion.status, ModelVersion.artifact_size).where(
                ModelVersion.model_id == m.id,
            ).order_by(ModelVersion.version.desc()).limit(1)
        )).first()
        latest_ver = latest_row[0] if latest_row else None
        latest_size = latest_row[1] if latest_row else None

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
            flavor=_flavor_of(cm),
            framework=_framework_label(cm),
            # MLmodel 크기가 없으면(메타 미수집·외부 등록) 최신 버전 아티팩트 총 크기로 대체
            model_size_bytes=(cm.model_size_bytes if cm and cm.model_size_bytes else None) or latest_size or None,
            download_count=download_counts.get(m.name, 0),
            created_by=m.created_by,
            updated_at=m.updated_at,
            production_version=production_by_model.get(m.id),
        )

        # post-query 필터 적용
        if status and latest_ver != status:
            continue
        if python_version and (summary.python_version or "") != python_version:
            continue
        if sklearn_version and (summary.sklearn_version or "") != sklearn_version:
            continue
        if flavor and (summary.flavor or "") != flavor:
            continue

        all_items.append(summary)

    # 필터링된 결과에 수동 페이징 적용
    total = len(all_items)
    logger.debug("모델 요약 목록: scanned=%d matched=%d page=%d deleted=%s",
                 len(models), total, page, deleted)
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
        logger.debug("모델 통계: 활성 모델 없음 — 빈 통계 반환")
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
    # MLmodel 메타가 없는 모델은 최신 버전의 아티팩트 총 크기로 대체 (목록 화면과 같은 기준)
    latest_sizes = await session.execute(
        select(RegisteredModel.name, ModelVersion.artifact_size)
        .join(ModelVersion, ModelVersion.model_id == RegisteredModel.id)
        .where(
            RegisteredModel.status != "deleted",
            ModelVersion.version == RegisteredModel.max_version_number,
        )
    )
    for name, size in latest_sizes.all():
        if name not in seen and size:
            seen.add(name)
            model_sizes.append(ModelSizeInfo(model_name=name, model_size_bytes=size))
    model_sizes.sort(key=lambda x: x.model_size_bytes, reverse=True)

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
        get_daily_download,
        get_download_count_by_model,
        get_hourly_download,
        get_total_download_count,
    )
    total_download = await get_total_download_count(session)

    hourly_raw = await get_hourly_download(session, hours=24)
    daily_1d = [DataPoint(date=d["date"], count=d["count"]) for d in hourly_raw]

    daily_7d_raw = await get_daily_download(session, days=7)
    daily_7d = [DataPoint(date=d["date"], count=d["count"]) for d in daily_7d_raw]

    daily_30d_raw = await get_daily_download(session, days=30)
    daily_30d = [DataPoint(date=d["date"], count=d["count"]) for d in daily_30d_raw]

    # 삭제된 모델(또는 테스트로 남은 로그)은 순위에서 뺀다
    active_name_set = set(active_names)
    download_by_model = {
        n: c for n, c in (await get_download_count_by_model(session)).items() if n in active_name_set
    }
    download_30d_total = sum(d["count"] for d in daily_30d_raw)

    production_models = len((await session.execute(
        select(ModelVersion.model_id).where(
            ModelVersion.model_id.in_(active_ids),
            ModelVersion.stage == "PRODUCTION",
        ).group_by(ModelVersion.model_id)
    )).scalars().all())

    # 게시 통계
    from app.models.download_log import (
        get_daily_publish,
        get_hourly_publish,
        get_total_publish_count,
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
        production_models=production_models,
        download_30d_total=download_30d_total,
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
            flavor=_flavor_of(cm),
            flavor_version=cm.flavor_version or (cm.sklearn_version if _flavor_of(cm) == "sklearn" else None),
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
        created_by=model.created_by,
        files_path=files_browser_path(model.storage_location),
    )


async def get_model_download_stats(
    session: AsyncSession, name: str,
) -> ModelDownloadStats:
    """모델 단위 다운로드 통계(총합 + 최근 30일 차트 + 최근 50건 로그).

    모델 존재 여부는 확인하지 않는다(라우터에서 확인). 로그는 모델 이름 기준으로 모은다."""
    from app.models.models import ModelDownloadLog

    # 이 모델의 총 다운로드 수
    total = (await session.execute(
        select(func.count()).where(ModelDownloadLog.model_name == name)
    )).scalar() or 0

    # 일별 다운로드(최근 30일)
    from datetime import timedelta, timezone
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
    """모델 메타데이터 부분 갱신. 이름 변경 시 중복 검사(``ValueError``) + URN 재생성.

    새 이름은 생성 때와 같은 규칙으로 검사한다 — 다른 등록 모델(소프트 삭제 포함)·OCI 모델 허브
    모델과 겹치면 ``ValueError`` (두 레지스트리가 같은 S3 키 프리픽스 ``{name}/v{n}/`` 를 쓰므로).
    모델이 없거나 삭제됐으면 ``None``. 저장 경로(``storage_location``)·버전 경로는 바꾸지 않는다.
    """
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
            # 생성 시와 같은 규칙 — OCI 모델 허브와 이름(=S3 키 프리픽스)이 겹치지 않게 한다
            from app.oci_hub.models import OciModel
            if (await session.execute(select(OciModel.id).where(OciModel.name == req.name))).first():
                logger.warning("RegisteredModel 이름 변경 거부(OCI 모델과 이름 중복): %s → %s", model.name, req.name)
                raise ValueError(
                    f"OCI 모델 허브에 '{req.name}' 모델이 있습니다. 두 레지스트리는 같은 저장소 경로를 쓰므로 다른 이름을 사용하세요."
                )
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
    """모델 소프트 삭제. ``PENDING_REGISTRATION`` 상태 버전들은 FAILED_REGISTRATION 으로 자동 마감.

    삭제되지 않은 모델이 없으면 False."""
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
    closed = 0
    for ver in pending.scalars().all():
        ver.status = "FAILED_REGISTRATION"
        ver.status_message = "Model deleted during registration"
        closed += 1
    if closed:
        logger.debug("소프트 삭제로 진행 중 버전 마감: %s count=%d", name, closed)

    await session.commit()
    logger.info("RegisteredModel 소프트 삭제됨: %s (id=%d)", model.name, model.id)
    return True


async def restore_registered_model(session: AsyncSession, name: str) -> bool:
    """소프트 삭제된 모델을 되살린다. 삭제된 모델이 없으면 False.

    삭제 시 FAILED 로 마감한 PENDING 버전은 되돌리지 않는다(업로드가 이미 끊긴 상태이므로)."""
    model = (await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.name == name, RegisteredModel.status == "deleted",
        )
    )).scalars().first()
    if not model:
        return False
    model.status = "active"
    await session.commit()
    logger.info("RegisteredModel 복원됨: %s (id=%d)", model.name, model.id)
    return True


def _foreign_s3_version_prefixes(
    versions: list[ModelVersion], current_prefixes: set[str], current_bucket: str,
) -> set[tuple[str, str]]:
    """버전 ``storage_location`` 중 현재 이름의 ``{name}/v{n}/`` 와 다른 S3 프리픽스를 ``(bucket, prefix)`` 로.

    이름 변경 전에 만든 버전은 옛 이름 프리픽스에 파일이 남아 있다. 버킷 전체·상위 경로를 지우는
    일이 없도록 ``.../v{버전}/`` 형태의 프리픽스만 받는다.
    """
    result: set[tuple[str, str]] = set()
    for mv in versions:
        loc = mv.storage_location or ""
        if not loc.startswith("s3://"):
            continue
        bucket, _, key = loc[len("s3://"):].partition("/")
        if not bucket or not key.endswith(f"/v{mv.version}/") or ".." in key.split("/"):
            logger.warning("S3 버전 프리픽스 정리 건너뜀(형식 이상): version=%s location=%s", mv.version, loc)
            continue
        if key in current_prefixes and bucket == current_bucket:
            continue  # 현재 이름 프리픽스는 delete_version_files 가 지운다
        result.add((bucket, key))
    return result


async def hard_delete_registered_model(session: AsyncSession, name: str) -> bool:
    """모델 영구 삭제: DB 테이블(``catalog_models``/``catalog_model_versions``/``catalog_registered_models``/
    다운로드 기록, 리니지·메트릭·모델 카드는 FK CASCADE) + 디스크 아티팩트 디렉토리 + S3 객체.
    소프트 삭제 여부와 무관하게 이름으로 찾으며 없으면 False.

    S3 객체는 이름 프리픽스(``{name}/``) 통째가 아니라 **버전 프리픽스** 단위로 지운다 — 현재 이름의
    ``{name}/v{n}/`` (n = 1..max_version_number 및 남은 버전) 와, 이름 변경 전에 만들어진 버전의
    ``storage_location`` 프리픽스(옛 이름 하위 고아 객체). 같은 이름 프리픽스를 쓰는 다른
    레지스트리(OCI) 파일을 지우지 않기 위해서다.
    DB 커밋 후 파일을 지우므로 디스크·S3 삭제 실패는 WARNING 으로만 남는다(DB 삭제는 유지)."""
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

    # model_versions 행 삭제 — 삭제 전에 S3 정리 대상 버전 프리픽스를 모아 둔다
    mv_result = await session.execute(
        select(ModelVersion).where(ModelVersion.model_id == model.id)
    )
    versions = mv_result.scalars().all()
    version_numbers = set(range(1, (model.max_version_number or 0) + 1)) | {mv.version for mv in versions}
    current_prefixes = {f"{name}/v{n}/" for n in version_numbers}
    extra_prefixes = _foreign_s3_version_prefixes(
        versions, current_prefixes, model.bucket_name or settings.os_bucket,
    )
    for mv in versions:
        await session.delete(mv)

    # 다운로드 기록 삭제 — 같은 이름으로 다시 만들면 예전 다운로드 수·차트가 이어 붙지 않게
    from app.models.models import ModelDownloadLog
    await session.execute(sa_delete(ModelDownloadLog).where(ModelDownloadLog.model_name == name))

    # 등록 모델 삭제
    await session.delete(model)
    await session.commit()

    # 디스크의 아티팩트 디렉토리 삭제 — 모델 저장 경로가 model-artifacts 루트의 "하위" 일 때만.
    # (루트 자체·루트 밖 경로는 다른 모델/다른 저장소를 지울 수 있으므로 건드리지 않는다)
    root = _model_artifacts_root()
    art_dir = _local_path(model.storage_location) or (root / name)
    if not _is_under(art_dir, root) or art_dir.resolve() == root.resolve():
        logger.warning("아티팩트 디렉토리 삭제 건너뜀(저장소 루트 밖): %s → %s", name, art_dir)
    elif art_dir.exists():
        try:
            shutil.rmtree(art_dir)
            logger.info("아티팩트 디렉토리 삭제: %s", art_dir)
        except OSError as e:
            logger.warning("아티팩트 디렉토리 삭제 실패 %s: %s", art_dir, e)

    # S3/MinIO 아티팩트도 정리 (오브젝트 스토리지 사용 시) — 버전 프리픽스 단위, 실패해도 DB 삭제는 유지
    from app.models import model_store
    deleted = 0
    try:
        for n in sorted(version_numbers):
            deleted += await model_store.delete_version_files(name, n, model.bucket_name)
        for bucket, prefix in sorted(extra_prefixes):
            logger.debug("이름 변경 전 버전 프리픽스 정리: s3://%s/%s", bucket, prefix)
            deleted += await model_store.delete_prefix_files(prefix, bucket)
        logger.info("모델 %s 의 S3 객체 %d개 삭제(버전 %d개, 옛 프리픽스 %d개)",
                    name, deleted, len(version_numbers), len(extra_prefixes))
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
        stage=normalize_stage(ver.stage),
        stage_changed_at=ver.stage_changed_at,
        stage_changed_by=ver.stage_changed_by or None,
        files_present=_version_files_present(ver.storage_location),
        files_path=files_browser_path(ver.storage_location),
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
    를 DB 수준 원자적 UPDATE(``SET max_version_number = max_version_number + 1``)로 증가시켜
    부여한다 — UPDATE 가 모델 행을 커밋까지 잠그므로 동시 요청도 서로 다른 번호를 받는다.
    로컬 모델의 storage URI 는
    ``file://`` 프리픽스를 붙여 MLflow 의 ``LocalArtifactRepository`` 가 업로드 경로로 사용하도록 한다.
    S3 모델은 ``s3://{bucket}/{name}/v{ver}/`` 를 쓴다. 모델이 없거나 삭제됐으면 ``ValueError``.
    """
    logger.info("모델 버전 생성: model=%s, source=%s, run_id=%s",
                req.model_name, req.source, req.run_id)

    model = await _resolve_model(session, req.model_name)
    if not model:
        logger.warning("버전 생성 실패(모델 없음): %s", req.model_name)
        raise ValueError(f"Model '{req.model_name}' not found")

    # 버전 번호 증가 — ORM 읽기-수정-쓰기(동시 요청이 같은 번호를 받음) 대신 DB 수준 원자적 UPDATE.
    # UPDATE 가 행을 잠근 뒤 같은 트랜잭션에서 증가된 값을 다시 읽는다(SQLite/PostgreSQL/MySQL 공통).
    await session.execute(
        sa_update(RegisteredModel)
        .where(RegisteredModel.id == model.id)
        .values(max_version_number=RegisteredModel.max_version_number + 1)
        .execution_options(synchronize_session=False)
    )
    new_version = (await session.execute(
        select(RegisteredModel.max_version_number).where(RegisteredModel.id == model.id)
    )).scalar_one()
    # ORM 객체의 값도 맞춰 둔다(커밋된 값으로 취급 — 추가 UPDATE 를 만들지 않음)
    set_committed_value(model, "max_version_number", new_version)
    logger.debug("버전 번호 부여: %s → v%d", model.name, new_version)

    # storage 위치 계산 — 오브젝트 스토리지 모델은 model-store 키 규칙(``{name}/v{ver}/``),
    # 로컬 모델은 ``{storage}/versions/{ver}`` (경로 연산을 위해 file:// 제거, URI 용으로 다시 추가)
    ver_path: str | None = None
    if _is_s3_model(model):
        ver_storage = _s3_version_uri(model, new_version)
    else:
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

    # 아티팩트 디렉토리 생성 (로컬 저장소만)
    if ver_path:
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
    """버전 메타데이터(설명·source) 부분 갱신. None 인 필드는 그대로 둔다. 모델·버전이 없으면 ``None``."""
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

    모델이 없거나(소프트 삭제된 모델 포함)·버전이 없으면 ``LookupError``, 상태가 맞지 않으면 ``ValueError``.
    아티팩트 스캔은 로컬(``file://``) 경로만 대상이다 — S3 버전은 0 으로 남는다.
    """
    logger.info("모델 버전 확정 중: %s v%d → %s", model_name, version, req.status.value)

    model = await _resolve_model(session, model_name)
    if not model:
        logger.warning("확정 실패(모델 없음): %s", model_name)
        raise LookupError(f"Model '{model_name}' not found")

    result = await session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model.id,
            ModelVersion.version == version,
        )
    )
    ver = result.scalars().first()
    if not ver:
        logger.warning("확정 실패(버전 없음): %s v%d", model_name, version)
        raise LookupError(f"Version {version} not found for model '{model_name}'")

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
        else:
            logger.debug("확정 시 아티팩트 경로 없음(스캔 생략): %s v%d path=%s", model_name, version, art_path)

    # 아티팩트 파일을 파싱해 catalog_models 에 저장(READY 일 때만)
    if req.status.value == "READY" and art_path and art_path.exists():
        await _save_catalog_model(session, model.name, version, ver.id, art_path)

    await session.commit()
    await session.refresh(ver)
    logger.info("ModelVersion 확정됨: %s v%d → %s (artifacts=%d, size=%d)",
                model.name, version, ver.status,
                ver.artifact_count or 0, ver.artifact_size or 0)
    return _build_version_response(model, ver)


async def finalize_store_version(
    session: AsyncSession,
    model_name: str,
    version: int,
    *,
    file_count: int,
    total_size: int,
    manifest: dict | None = None,
    meta_files: dict[str, bytes] | None = None,
    source_type: str = "model-store",
) -> ModelVersionResponse:
    """model-store(S3) 로 올린 버전을 DB 에 확정한다 — ``/model-store/.../finalize`` 용.

    - 버전 레코드: ``PENDING_REGISTRATION`` → ``READY`` (이미 READY 면 파일 수·크기·manifest 만 갱신),
      ``FAILED_REGISTRATION`` 은 거부. ``storage_location`` 을 S3 키 프리픽스로, ``finished_at`` 기록
    - ``catalog_models``: manifest·digest 저장, ``meta_files`` (MLmodel·requirements.txt·conda.yaml·
      python_env.yaml 원본) 가 있으면 로컬 확정과 같은 방식으로 메타데이터 파싱

    모델/버전이 없으면 ``LookupError``, 상태가 맞지 않으면 ``ValueError``.
    """
    import json as _json
    import tempfile

    from app.models.model_store import manifest_digest

    model = await _resolve_model(session, model_name)
    if not model:
        logger.warning("model-store 확정 실패(모델 없음): %s", model_name)
        raise LookupError(f"등록된 MLflow 모델 '{model_name}' 이(가) 없습니다.")
    ver = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model.id, ModelVersion.version == version,
        )
    )).scalars().first()
    if not ver:
        logger.warning("model-store 확정 실패(버전 없음): %s v%d", model_name, version)
        raise LookupError(
            f"모델 '{model_name}' 의 버전 {version} 이(가) 없습니다. "
            f"먼저 POST /api/v1/models/{model_name}/versions 로 버전을 만드세요."
        )
    if ver.status == "FAILED_REGISTRATION":
        logger.warning("model-store 확정 거부(FAILED 버전): %s v%d", model_name, version)
        raise ValueError(f"버전 {version} 은(는) 실패(FAILED_REGISTRATION)로 마감되어 확정할 수 없습니다.")

    was_ready = ver.status == "READY"
    ver.status = "READY"
    ver.status_message = None
    ver.storage_location = _s3_version_uri(model, version)
    ver.artifact_count = file_count
    ver.artifact_size = total_size
    ver.finished_at = _dt.datetime.now(_dt.timezone.utc)

    # catalog_models — 로컬 확정과 같은 파서로 메타 추출(임시 디렉터리에 원본 파일을 풀어서)
    ml_fields: dict = {}
    texts: dict[str, str | None] = {}
    if meta_files:
        with tempfile.TemporaryDirectory() as tmp:
            for fname, data in meta_files.items():
                (Path(tmp) / fname).write_bytes(data)
            ml_fields = _extract_mlmodel_fields(Path(tmp))
            texts = {
                "requirements": _read_file_text(Path(tmp) / "requirements.txt"),
                "conda": _read_file_text(Path(tmp) / "conda.yaml"),
                "python_env": _read_file_text(Path(tmp) / "python_env.yaml"),
            }
    # digest 는 S3 에 실제 저장되는 manifest.json 바이트(indent=2) 기준 — model_store 와 같은 함수 재사용
    manifest_json = _json.dumps(manifest) if manifest is not None else None
    digest = manifest_digest(manifest) if manifest is not None else None

    cm = (await session.execute(
        select(CatalogModel).where(CatalogModel.model_name == model.name, CatalogModel.version == version)
    )).scalars().first()
    if cm is None:
        cm = CatalogModel(model_version_id=ver.id, model_name=model.name, version=version)
        session.add(cm)
    for k, v in {**ml_fields, **texts}.items():
        if v is not None:
            setattr(cm, k, v)
    cm.manifest = manifest_json
    cm.content_digest = digest
    cm.source_type = cm.source_type or source_type

    await session.commit()
    await session.refresh(ver)
    logger.info("model-store 버전 확정: %s v%d → READY%s (files=%d, size=%d, meta=%s)",
                model.name, version, " (재확정)" if was_ready else "",
                file_count, total_size, sorted(meta_files or {}))
    return _build_version_response(model, ver)


async def record_store_artifacts(
    session: AsyncSession, model_name: str, version: int, file_count: int, total_size: int,
) -> None:
    """임포트(HF·로컬 → S3) 버전의 저장 위치·파일 수·크기를 기록한다(``finalize_model_version`` 은
    로컬 디렉터리만 세므로 S3 버전은 0 으로 남는다). 모델·버전이 없으면 아무것도 하지 않는다."""
    model = await _resolve_model(session, model_name)
    if not model:
        logger.warning("S3 아티팩트 기록 건너뜀(모델 없음): %s v%d", model_name, version)
        return
    ver = (await session.execute(
        select(ModelVersion).where(ModelVersion.model_id == model.id, ModelVersion.version == version)
    )).scalars().first()
    if not ver:
        logger.warning("S3 아티팩트 기록 건너뜀(버전 없음): %s v%d", model_name, version)
        return
    ver.storage_location = _s3_version_uri(model, version)
    ver.artifact_count = file_count
    ver.artifact_size = total_size
    await session.commit()
    logger.debug("S3 아티팩트 기록: %s v%d files=%d size=%d", model_name, version, file_count, total_size)


async def delete_model_version(
    session: AsyncSession, model_name: str, version: int,
) -> bool:
    """모델 버전 삭제 — DB 행과 그 버전의 파일(서버 디스크 ``versions/<n>``·저장소 ``{name}/v{n}/``)을 함께 지운다.

    예전에는 DB 행만 지워 파일이 남았고, 남은 디스크 디렉터리는 등록 모델 보호 규칙 때문에 파일 화면에서도
    지울 수 없어 모델을 영구 삭제해야만 사라졌다. 파일 삭제 실패는 경고만 남기고 DB 삭제는 유지한다.
    모델/버전 없으면 False 반환."""
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

    storage = ver.storage_location
    await session.delete(ver)
    await session.commit()
    logger.info("ModelVersion 삭제됨: %s v%d", model.name, version)
    await _delete_version_files(model, version, storage)
    return True


async def _delete_version_files(model: RegisteredModel, version: int, storage: str | None) -> None:
    """버전 하나의 파일 정리 — 실패는 경고만(호출자는 DB 삭제를 되돌리지 않는다)."""
    import shutil

    # 서버 디스크: model-artifacts 루트 하위이고 모델 디렉터리 자체가 아닐 때만(다른 버전·모델을 지우지 않게)
    local = _local_path(storage)
    root = _model_artifacts_root()
    if local is not None:
        model_dir = _local_path(model.storage_location) or (root / model.name)
        if not _is_under(local, root) or local.resolve() in (root.resolve(), model_dir.resolve()):
            logger.warning("버전 디렉터리 삭제 건너뜀(허용 범위 밖): %s v%d → %s", model.name, version, local)
        elif local.exists():
            try:
                shutil.rmtree(local)
                logger.info("버전 디렉터리 삭제: %s", local)
            except OSError as e:
                logger.warning("버전 디렉터리 삭제 실패 %s: %s", local, e)

    # 오브젝트 스토리지: 반입(import)·SDK 로 올린 버전은 ``{name}/v{n}/`` 키.
    # 저장 위치가 아직 file:// 인 확정 전 버전에도 model-store 업로드 파일이 있을 수 있으므로
    # (정리 API 는 기록 있는 버전을 409 로 거부하고 버전 삭제를 안내한다) 저장 위치와 무관하게 항상 지운다
    # — 모델 영구 삭제와 같은 규칙.
    try:
        from app.models import model_store
        deleted = await model_store.delete_version_files(model.name, version, model.bucket_name)
        logger.info("%s v%d 의 저장소 객체 %d개 삭제", model.name, version, deleted)
    except Exception as e:  # noqa: BLE001 — 저장소 미사용·장애여도 DB 삭제는 유지
        logger.warning("%s v%d 의 저장소 파일 삭제 실패: %s", model.name, version, e)


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

    # safe_load — 업로드된 YAML 의 임의 객체 생성(코드 실행)을 막는다

    try:
        data = yaml.safe_load(mlmodel_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("%s 의 MLmodel YAML 파싱 실패: %s", art_path, e)
        return {}

    if not isinstance(data, dict):
        logger.warning("%s 의 MLmodel 이 매핑(YAML dict)이 아님 — 메타데이터 생략", art_path)
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

    # 대표 flavor — python_function 은 모든 모델에 붙는 공통 래퍼라 제외하고 첫 flavor 를 쓴다
    # 버전은 flavor 설정의 ``*_version`` 키(sklearn_version, pytorch_version, xgb_version …)에서.
    # (커스텀 pyfunc 모델처럼 python_function 만 있으면 그대로 python_function)
    flavor = next((k for k in flavors if k != "python_function"), None) or (
        "python_function" if "python_function" in flavors else None
    )
    flavor_version = None
    if flavor and isinstance(flavors.get(flavor), dict):
        flavor_version = next(
            (str(v) for k, v in flavors[flavor].items()
             if k.endswith("_version") and k != "python_version" and v), None,
        )

    return {
        "predict_fn": predict_fn,
        "python_version": python_version,
        "serialization_format": serialization_format,
        "sklearn_version": sklearn_version,
        "flavor": flavor,
        "flavor_version": flavor_version,
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
    메타데이터를 추출해 ``catalog_models`` 테이블에 추가한다(커밋은 호출 측).
    실패는 이 함수 안에서 ERROR 로그 후 삼킨다 — 확정 자체는 계속된다."""
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


# ---------------------------------------------------------------------------
# 표시용 프레임워크 · 파일 브라우저 정합성 가드
# ---------------------------------------------------------------------------

def _flavor_of(cm: CatalogModel | None) -> str | None:
    """대표 flavor. 과거 데이터(flavor 컬럼 이전)는 sklearn_version 이 있으면 sklearn 으로 본다."""
    if cm is None:
        return None
    return cm.flavor or ("sklearn" if cm.sklearn_version else None)


def _framework_label(cm: CatalogModel | None) -> str | None:
    """목록·모델 카드에 쓰는 표시 문자열 — 예: ``sklearn 1.5.2``."""
    flavor = _flavor_of(cm)
    if not flavor:
        return None
    ver = cm.flavor_version or (cm.sklearn_version if flavor == "sklearn" else None)
    return f"{flavor} {ver}" if ver else flavor


async def find_registered_model_for_path(session: AsyncSession, path: Path) -> tuple[str, bool] | None:
    """파일 브라우저에서 지우거나 옮기려는 경로가 등록 모델의 저장소와 겹치면 그 모델 이름.

    겹침 = 경로가 모델 디렉터리 자체·그 하위·또는 그 상위(상위를 지우면 모델 파일도 사라짐).
    원격(S3) 저장소 모델은 로컬 경로가 없으므로 비교하지 않는다. 모든 모델을 순회한다(O(모델 수)).
    소프트 삭제된 모델도 포함한다 — 복원하면 파일이 그대로 필요하므로, 파일은 관리자 영구 삭제
    (``hard_delete_registered_model``)로만 지운다. 겹치면 ``(모델 이름, 경로가 모델 디렉터리의 상위인지)``,
    아니면 None.
    """
    target = path.resolve()
    rows = (await session.execute(
        select(RegisteredModel.name, RegisteredModel.storage_location)
    )).all()
    for name, storage in rows:
        local = _local_path(storage)
        if local is None:
            continue
        model_dir = local.resolve()
        if target == model_dir or _is_under(target, model_dir):
            return name, False
        if _is_under(model_dir, target):
            return name, True
    return None


async def find_readable_model_for_path(session: AsyncSession, path: Path) -> str | None:
    """일반 사용자가 볼 수 있는 경로인가 — 삭제되지 않은 등록 모델의 저장 디렉터리(그 자체·하위)면 그 모델 이름.

    MLflow 모델 파일 브라우저 API 의 조회 범위 검사에 쓴다(OCI 쪽 ``_require_browse_scope`` 와 같은 규칙).
    상위 경로(모델 디렉터리를 품는 폴더)와 소프트 삭제된 모델은 None — 관리자만 본다.
    """
    target = path.resolve()
    rows = (await session.execute(
        select(RegisteredModel.name, RegisteredModel.storage_location)
        .where(RegisteredModel.status != "deleted")
    )).all()
    for name, storage in rows:
        local = _local_path(storage)
        if local is None:
            continue
        model_dir = local.resolve()
        if target == model_dir or _is_under(target, model_dir):
            return name
    return None
