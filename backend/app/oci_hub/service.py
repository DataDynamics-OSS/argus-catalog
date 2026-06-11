"""OCI 모델 허브 서비스 레이어(비즈니스 로직).

CRUD · 태그 · 리니지 · 버전 라이프사이클 · HuggingFace 임포트까지를
한 모듈에서 다룬다. 라우터(``app/oci_hub/router.py``) 는 이 모듈의
함수만 호출하고 HTTP 변환만 담당한다.

로깅 정책: 변경 동작(create / update / delete / readme / tag / lineage /
finalize / import) 은 INFO 로 영향 받은 모델명·식별자를 함께 기록.
도메인 위반(예: 잘못된 finalize 상태) 은 WARNING 으로 남긴다.
"""

import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.oci_hub.models import OciModel, OciModelLineage, OciModelTag, OciModelVersion
from app.oci_hub.schemas import (
    ImportResponse,
    LineageResponse,
    OciModelCreate,
    OciModelDetail,
    OciModelSummary,
    OciModelUpdate,
    OciModelVersionResponse,
    PaginatedOciModels,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

async def _get_tags(session: AsyncSession, model_id: int) -> list[dict]:
    """모델에 연결된 태그를 ``catalog_tags`` 와 조인해서 가져온다."""
    from sqlalchemy import text
    result = await session.execute(text(
        "SELECT t.id, t.name, t.color FROM catalog_tags t "
        "JOIN catalog_oci_model_tags mt ON mt.tag_id = t.id "
        "WHERE mt.model_id = :mid ORDER BY t.name"
    ), {"mid": model_id})
    return [{"id": r[0], "name": r[1], "color": r[2]} for r in result.all()]


async def _get_lineage(session: AsyncSession, model_id: int) -> list[dict]:
    """모델의 리니지 엔트리(생성순) 를 반환한다."""
    result = await session.execute(
        select(OciModelLineage).where(OciModelLineage.model_id == model_id)
        .order_by(OciModelLineage.created_at)
    )
    return [
        {
            "id": r.id, "source_type": r.source_type, "source_id": r.source_id,
            "source_name": r.source_name, "relation_type": r.relation_type,
            "description": r.description, "created_at": r.created_at.isoformat(),
        }
        for r in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def list_models(
    session: AsyncSession,
    search: str | None = None,
    task: str | None = None,
    framework: str | None = None,
    language: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 12,
) -> PaginatedOciModels:
    """OCI 모델 목록을 필터·페이징과 함께 조회."""
    base = select(OciModel)
    if search:
        base = base.where(OciModel.name.ilike(f"%{search}%"))
    if task:
        base = base.where(OciModel.task == task)
    if framework:
        base = base.where(OciModel.framework == framework)
    if language:
        base = base.where(OciModel.language == language)
    if status:
        base = base.where(OciModel.status == status)

    total = (await session.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    offset = (page - 1) * page_size
    query = base.order_by(OciModel.updated_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    models = result.scalars().all()

    items = []
    for m in models:
        tags = await _get_tags(session, m.id)
        items.append(OciModelSummary(
            id=m.id, name=m.name, display_name=m.display_name,
            description=m.description, task=m.task, framework=m.framework,
            language=m.language, license=m.license, source_type=m.source_type,
            owner=m.owner, version_count=m.version_count,
            total_size=m.total_size or 0, download_count=m.download_count,
            status=m.status, tags=tags,
            created_at=m.created_at, updated_at=m.updated_at,
        ))

    logger.info("OCI 모델 목록 조회: search=%s, total=%d, page=%d", search, total, page)
    return PaginatedOciModels(items=items, total=total, page=page, page_size=page_size)


async def get_oci_created_by(session: AsyncSession, name: str) -> str | None:
    """OCI 모델 생성자 — 소유권 체크용."""
    return (await session.execute(
        select(OciModel.created_by).where(OciModel.name == name)
    )).scalar_one_or_none()


async def create_model(session: AsyncSession, req: OciModelCreate, created_by: str | None = None) -> OciModelDetail:
    """새 OCI 모델 등록. 이름 중복 시 ``ValueError`` 발생."""
    existing = await session.execute(
        select(OciModel).where(OciModel.name == req.name)
    )
    if existing.scalars().first():
        logger.warning("OCI 모델 생성 거부 (이름 중복): %s", req.name)
        raise ValueError(f"Model '{req.name}' already exists")

    model = OciModel(
        name=req.name, display_name=req.display_name or req.name,
        description=req.description, readme=req.readme,
        task=req.task, framework=req.framework, language=req.language,
        license=req.license, source_type=req.source_type, source_id=req.source_id,
        owner=req.owner, status=req.status, created_by=created_by,
        bucket=settings.os_bucket, storage_prefix=f"{req.name}/",
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    logger.info("OCI 모델 생성됨: %s (id=%d)", model.name, model.id)
    return await get_model_detail(session, model.name)


async def get_model_detail(session: AsyncSession, name: str) -> OciModelDetail | None:
    """모델 상세 + 태그·리니지를 조합해서 반환. 없으면 ``None``."""
    result = await session.execute(
        select(OciModel).where(OciModel.name == name)
    )
    m = result.scalars().first()
    if not m:
        return None

    tags = await _get_tags(session, m.id)
    lineage = await _get_lineage(session, m.id)

    return OciModelDetail(
        id=m.id, name=m.name, display_name=m.display_name,
        description=m.description, readme=m.readme,
        task=m.task, framework=m.framework, language=m.language,
        license=m.license, source_type=m.source_type, source_id=m.source_id,
        source_revision=m.source_revision, bucket=m.bucket,
        storage_prefix=m.storage_prefix, owner=m.owner,
        version_count=m.version_count, total_size=m.total_size or 0,
        download_count=m.download_count, status=m.status,
        tags=tags, lineage=lineage,
        created_at=m.created_at, updated_at=m.updated_at,
    )


async def update_model(session: AsyncSession, name: str, req: OciModelUpdate) -> OciModelDetail | None:
    """모델 메타데이터 부분 갱신(이름 / 설명 / 소유자 / 태스크 / 프레임워크 등)."""
    result = await session.execute(select(OciModel).where(OciModel.name == name))
    m = result.scalars().first()
    if not m:
        return None

    for field in ["display_name", "description", "task", "framework", "language", "license", "owner", "status"]:
        val = getattr(req, field)
        if val is not None:
            setattr(m, field, val)

    await session.commit()
    await session.refresh(m)
    logger.info("OCI 모델 갱신됨: %s", name)
    return await get_model_detail(session, name)


async def update_readme(session: AsyncSession, name: str, readme: str) -> bool:
    """모델 README(Markdown 본문) 를 갱신."""
    result = await session.execute(select(OciModel).where(OciModel.name == name))
    m = result.scalars().first()
    if not m:
        return False
    m.readme = readme
    await session.commit()
    logger.info("OCI 모델 README 갱신됨: %s", name)
    return True


async def delete_model(session: AsyncSession, name: str) -> bool:
    """모델 및 관련 데이터(버전 / 태그 / 리니지) 일괄 삭제. S3 파일도 함께 제거."""
    result = await session.execute(select(OciModel).where(OciModel.name == name))
    m = result.scalars().first()
    if not m:
        return False
    storage_prefix = m.storage_prefix or f"{name}/"
    bucket = m.bucket
    await session.delete(m)
    await session.commit()

    # S3/MinIO 의 모델 아티팩트(prefix) 삭제 — 실패해도 DB 삭제는 유지
    try:
        await _delete_s3_prefix(bucket, storage_prefix)
    except Exception as e:  # noqa: BLE001 — 스토리지 미사용/연결 실패는 경고만
        logger.warning("OCI 모델 %s 의 S3 아티팩트 삭제 실패: %s", name, e)

    logger.info("OCI 모델 삭제됨: %s (id=%d)", name, m.id)
    return True


async def _delete_s3_prefix(bucket: str | None, prefix: str) -> int:
    """버킷의 prefix 하위 모든 객체 삭제 (페이지네이션)."""
    if not bucket or not prefix:
        return 0
    from app.core.s3 import get_s3_client  # 공용 S3 클라이언트 재사용
    total = 0
    async with get_s3_client() as s3:
        token = None
        while True:
            params = {"Bucket": bucket, "Prefix": prefix}
            if token:
                params["ContinuationToken"] = token
            resp = await s3.list_objects_v2(**params)
            objects = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
            if objects:
                await s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                total += len(objects)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
    logger.info("s3://%s/%s 에서 객체 %d개 삭제", bucket, prefix, total)
    return total


# ---------------------------------------------------------------------------
# 태그
# ---------------------------------------------------------------------------

async def add_tag(session: AsyncSession, name: str, tag_id: int) -> bool:
    """모델 ↔ 태그 매핑을 추가. 모델 또는 태그가 없으면 False."""
    result = await session.execute(select(OciModel).where(OciModel.name == name))
    m = result.scalars().first()
    if not m:
        return False
    session.add(OciModelTag(model_id=m.id, tag_id=tag_id))
    await session.commit()
    logger.info("OCI 모델 %s 에 태그 %d 추가", name, tag_id)
    return True


async def remove_tag(session: AsyncSession, name: str, tag_id: int) -> bool:
    """모델 ↔ 태그 매핑 제거."""
    result = await session.execute(select(OciModel).where(OciModel.name == name))
    m = result.scalars().first()
    if not m:
        return False
    tag = (await session.execute(
        select(OciModelTag).where(OciModelTag.model_id == m.id, OciModelTag.tag_id == tag_id)
    )).scalars().first()
    if not tag:
        return False
    await session.delete(tag)
    await session.commit()
    logger.info("OCI 모델 %s 에서 태그 %d 제거", name, tag_id)
    return True


# ---------------------------------------------------------------------------
# 리니지
# ---------------------------------------------------------------------------

async def add_lineage(session: AsyncSession, name: str, **kwargs) -> LineageResponse | None:
    """리니지 엔트리 추가(데이터셋 / 부모 모델 등 외부 자원과 연결)."""
    result = await session.execute(select(OciModel).where(OciModel.name == name))
    m = result.scalars().first()
    if not m:
        return None
    entry = OciModelLineage(model_id=m.id, **kwargs)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    logger.info("OCI 모델 %s 에 리니지 추가: %s -> %s", name, kwargs.get("relation_type"), kwargs.get("source_id"))
    return LineageResponse.model_validate(entry)


async def remove_lineage(session: AsyncSession, lineage_id: int) -> bool:
    """리니지 엔트리 삭제."""
    entry = await session.get(OciModelLineage, lineage_id)
    if not entry:
        return False
    await session.delete(entry)
    await session.commit()
    logger.info("리니지 %d 제거됨", lineage_id)
    return True


# ---------------------------------------------------------------------------
# 버전
# ---------------------------------------------------------------------------

async def list_versions(session: AsyncSession, name: str) -> list[OciModelVersionResponse]:
    """모델의 전체 버전을 새 순서로 조회(다운로드 카운트 포함)."""
    result = await session.execute(select(OciModel).where(OciModel.name == name))
    m = result.scalars().first()
    if not m:
        return []
    versions = (await session.execute(
        select(OciModelVersion).where(OciModelVersion.model_id == m.id)
        .order_by(OciModelVersion.version.desc())
    )).scalars().all()
    return [OciModelVersionResponse.model_validate(v) for v in versions]


# ---------------------------------------------------------------------------
# Finalize Push (SDK/수동 업로드)
# ---------------------------------------------------------------------------

async def finalize_push(
    session: AsyncSession,
    name: str,
    version: int,
    readme: str | None = None,
) -> OciModelVersionResponse | None:
    """수동 push 한 버전을 확정한다.

    S3 파일을 스캔해 OCI manifest 를 만들고 버전 레코드를 작성한 뒤,
    모델 통계(``version_count`` / ``total_size`` / README) 를 갱신한다.
    SDK 가 ``model-store`` API 로 파일을 모두 업로드한 직후 호출한다.
    """
    from app.models import model_store

    result = await session.execute(select(OciModel).where(OciModel.name == name))
    model = result.scalars().first()
    if not model:
        return None

    # S3 파일 스캔
    file_count, total_size = await model_store.get_total_size(name, version)
    if file_count == 0:
        logger.warning("Finalize 거부 (S3 파일 없음): %s v%d", name, version)
        raise ValueError(f"No files found in S3 for {name}/v{version}")

    # OCI manifest 생성
    manifest = await model_store.generate_manifest(name, version)

    # 버전 레코드가 이미 존재하는지 확인
    existing = (await session.execute(
        select(OciModelVersion).where(
            OciModelVersion.model_id == model.id,
            OciModelVersion.version == version,
        )
    )).scalars().first()

    if existing:
        # 기존 레코드 갱신
        existing.file_count = file_count
        existing.total_size = total_size
        existing.manifest = json.dumps(manifest)
        existing.status = "ready"
        ver = existing
    else:
        # 새 버전 레코드 생성
        ver = OciModelVersion(
            model_id=model.id, version=version,
            manifest=json.dumps(manifest),
            file_count=file_count,
            total_size=total_size,
            status="ready",
        )
        session.add(ver)

    # 모델 통계 갱신
    model.version_count = max(model.version_count, version)
    model.total_size = total_size
    if readme:
        model.readme = readme
    if model.status == "draft":
        model.status = "approved"

    await session.commit()
    await session.refresh(ver)

    logger.info("Finalize push: %s v%d (파일 %d개, %d bytes)", name, version, file_count, total_size)
    return OciModelVersionResponse.model_validate(ver)


# ---------------------------------------------------------------------------
# HuggingFace 임포트
# ---------------------------------------------------------------------------

async def import_from_huggingface(session: AsyncSession, **kwargs) -> ImportResponse:
    """HuggingFace Hub 의 모델을 받아 OCI 모델 허브에 등록한다.

    - HF 에서 메타데이터·파일을 가져와 S3 에 업로드한다.
    - 등록 모델·버전 레코드를 DB 에 생성한다.
    - 외부 의존성 오류(네트워크/HF/S3) 는 ERROR 로 표면화한다.
    """
    from app.models import model_store

    hf_model_id = kwargs["hf_model_id"]
    name = kwargs.get("name") or hf_model_id.replace("/", "-")
    revision = kwargs.get("revision", "main")

    logger.info("HF 모델 %s 를 %s 로 임포트 중", hf_model_id, name)

    # 모델 생성 또는 조회
    result = await session.execute(select(OciModel).where(OciModel.name == name))
    model = result.scalars().first()
    if not model:
        model = OciModel(
            name=name, display_name=name,
            description=kwargs.get("description"),
            task=kwargs.get("task"), framework=kwargs.get("framework"),
            language=kwargs.get("language"),
            source_type="huggingface", source_id=hf_model_id,
            source_revision=revision, owner=kwargs.get("owner"),
            created_by=kwargs.get("created_by"),
            bucket=settings.os_bucket, storage_prefix=f"{name}/",
            status="draft",
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)

    # 버전 증가
    model.version_count += 1
    version = model.version_count

    # 파일을 S3 로 임포트
    metadata = await model_store.import_from_huggingface(
        hf_model_id=hf_model_id, model_name=name,
        version=version, revision=revision,
    )

    # 전체 메타데이터로 버전 레코드 생성
    ver_metadata = {
        "source": f"huggingface:{hf_model_id}",
        "model_type": metadata.get("model_type"),
        "architectures": metadata.get("architectures"),
        "torch_dtype": metadata.get("torch_dtype"),
        "transformers_version": metadata.get("transformers_version"),
        "hidden_size": metadata.get("hidden_size"),
        "num_hidden_layers": metadata.get("num_hidden_layers"),
        "num_attention_heads": metadata.get("num_attention_heads"),
        "vocab_size": metadata.get("vocab_size"),
        "tokenizer_class": metadata.get("tokenizer_class"),
    }
    # None 값 제거
    ver_metadata = {k: v for k, v in ver_metadata.items() if v is not None}

    ver = OciModelVersion(
        model_id=model.id, version=version,
        manifest=json.dumps(metadata.get("manifest")),
        file_count=metadata["file_count"],
        total_size=metadata["total_size"],
        extra_metadata=ver_metadata,
        status="ready",
    )
    session.add(ver)
    model.total_size = metadata["total_size"]

    # HF 메타데이터로 모델 필드 채우기
    if not model.framework and metadata.get("transformers_version"):
        model.framework = "pytorch"
    if metadata.get("model_type"):
        model.description = model.description or f"{metadata['model_type']} model from HuggingFace"

    # README 를 Model Card 로 저장
    if metadata.get("readme") and not model.readme:
        model.readme = metadata["readme"]

    # 임포트 완료 — 상태를 approved 로 설정
    model.status = "approved"

    await session.commit()

    logger.info("HF 임포트 완료: %s v%d (파일 %d개, %d bytes)",
                name, version, metadata["file_count"], metadata["total_size"])

    return ImportResponse(
        name=name, version=version,
        file_count=metadata["file_count"],
        total_size=metadata["total_size"],
        status="ready",
    )


# ---------------------------------------------------------------------------
# 대시보드 통계
# ---------------------------------------------------------------------------

async def get_hub_stats(session: AsyncSession) -> dict:
    """OCI 모델 허브 대시보드 통계 집계(요약 카드·차트·다운로드/게시 추이)."""
    # 전체 모델 수
    total_models = (await session.execute(select(func.count()).select_from(OciModel))).scalar() or 0

    # 전체 버전 수
    total_versions = (await session.execute(select(func.count()).select_from(OciModelVersion))).scalar() or 0

    # 출처 유형별 집계
    source_counts_raw = (await session.execute(
        select(OciModel.source_type, func.count()).group_by(OciModel.source_type)
    )).all()
    source_counts = {(s or "unknown"): c for s, c in source_counts_raw}
    hf_count = source_counts.get("huggingface", 0)
    my_count = source_counts.get("my", 0)

    # 전체 다운로드 수
    total_downloads = (await session.execute(
        select(func.sum(OciModel.download_count))
    )).scalar() or 0

    # 출처 분포 (도넛 차트용)
    source_distribution = [
        {"source": s, "count": c} for s, c in source_counts.items()
    ]

    # 모델 크기 top 10
    size_result = (await session.execute(
        select(OciModel.name, OciModel.total_size)
        .where(OciModel.total_size > 0)
        .order_by(OciModel.total_size.desc())
        .limit(10)
    )).all()
    model_sizes = [{"model_name": n, "total_size": s} for n, s in size_result]

    # 다운로드 top 10
    dl_result = (await session.execute(
        select(OciModel.name, OciModel.download_count)
        .where(OciModel.download_count > 0)
        .order_by(OciModel.download_count.desc())
        .limit(10)
    )).all()
    top_downloads = [{"model_name": n, "download_count": c} for n, c in dl_result]

    # 다운로드 추이 (OCI 전용 다운로드 로그 기반)
    from datetime import timezone, timedelta
    from sqlalchemy import text
    import datetime as _dt
    from app.oci_hub.models import OciModelDownloadLog

    now = _dt.datetime.now(timezone.utc)

    # 시간별 다운로드 (24h)
    since_1d = now - timedelta(hours=24)
    result = await session.execute(
        select(
            func.date_trunc("hour", OciModelDownloadLog.downloaded_at).label("hour"),
            func.count().label("count"),
        )
        .where(OciModelDownloadLog.downloaded_at >= since_1d)
        .group_by(text("hour")).order_by(text("hour"))
    )
    download_1d = [{"date": r.hour.strftime("%H:%M") if r.hour else "", "count": r.count} for r in result.all()]

    # 일별 다운로드 (7d)
    since_7d = now - timedelta(days=7)
    result = await session.execute(
        select(func.date(OciModelDownloadLog.downloaded_at).label("day"), func.count().label("count"))
        .where(OciModelDownloadLog.downloaded_at >= since_7d)
        .group_by(func.date(OciModelDownloadLog.downloaded_at)).order_by(text("day"))
    )
    download_7d = [{"date": str(r.day), "count": r.count} for r in result.all()]

    # 일별 다운로드 (30d)
    since_30d = now - timedelta(days=30)
    result = await session.execute(
        select(func.date(OciModelDownloadLog.downloaded_at).label("day"), func.count().label("count"))
        .where(OciModelDownloadLog.downloaded_at >= since_30d)
        .group_by(func.date(OciModelDownloadLog.downloaded_at)).order_by(text("day"))
    )
    download_30d = [{"date": str(r.day), "count": r.count} for r in result.all()]

    # OCI 모델 전체 다운로드 수
    total_download = (await session.execute(
        select(func.count()).select_from(OciModelDownloadLog)
    )).scalar() or 0

    # 게시 추이 (oci_model_versions.created_at 기반)
    # 시간별 게시 (24h)
    pub_1d_result = await session.execute(
        select(func.date_trunc("hour", OciModelVersion.created_at).label("hour"), func.count().label("count"))
        .where(OciModelVersion.created_at >= now - timedelta(hours=24))
        .group_by(text("hour")).order_by(text("hour"))
    )
    publish_1d = [{"date": r.hour.strftime("%H:%M") if r.hour else "", "count": r.count} for r in pub_1d_result.all()]

    # 일별 게시 (7d)
    pub_7d_result = await session.execute(
        select(func.date(OciModelVersion.created_at).label("day"), func.count().label("count"))
        .where(OciModelVersion.created_at >= now - timedelta(days=7))
        .group_by(func.date(OciModelVersion.created_at)).order_by(text("day"))
    )
    publish_7d = [{"date": str(r.day), "count": r.count} for r in pub_7d_result.all()]

    # 일별 게시 (30d)
    pub_30d_result = await session.execute(
        select(func.date(OciModelVersion.created_at).label("day"), func.count().label("count"))
        .where(OciModelVersion.created_at >= now - timedelta(days=30))
        .group_by(func.date(OciModelVersion.created_at)).order_by(text("day"))
    )
    publish_30d = [{"date": str(r.day), "count": r.count} for r in pub_30d_result.all()]

    total_publish = total_versions

    return {
        "total_models": total_models,
        "total_versions": total_versions,
        "hf_count": hf_count,
        "my_count": my_count,
        "total_downloads": total_downloads,
        "total_download": total_download,
        "total_publish": total_publish,
        "source_distribution": source_distribution,
        "model_sizes": model_sizes,
        "top_downloads": top_downloads,
        "download_1d": download_1d,
        "download_7d": download_7d,
        "download_30d": download_30d,
        "publish_1d": publish_1d,
        "publish_7d": publish_7d,
        "publish_30d": publish_30d,
    }
