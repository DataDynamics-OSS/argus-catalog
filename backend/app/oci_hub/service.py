# SPDX-License-Identifier: Apache-2.0
"""OCI 모델 허브 서비스 레이어(비즈니스 로직).

CRUD · 태그 · 리니지 · 버전 라이프사이클 · HuggingFace 임포트까지를
한 모듈에서 다룬다. 라우터(``app/oci_hub/router.py``) 는 이 모듈의
함수만 호출하고 HTTP 변환·권한 검사만 담당한다.

저장소: 파일은 model-store 와 같은 버킷의 ``{name}/v{version}/`` 키에 둔다. MLflow 등록 모델과
이름이 같으면 같은 키를 쓰게 되므로(파일 섞임·삭제 시 상대 파일까지 삭제) **두 레지스트리
사이에서 이름이 겹치지 않게** 생성 시 검사한다.

로깅 정책: 변경 동작(create / update / delete / readme / tag / lineage /
finalize / import) 은 INFO 로 영향 받은 모델명·식별자를 함께 기록.
도메인 위반(예: 잘못된 finalize 상태) 은 WARNING, 조회 세부는 DEBUG 로 남긴다.
"""

import asyncio
import datetime as _dt
import json
import logging

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sql import LIKE_ESCAPE, like_contains
from app.oci_hub.models import (
    OciModel,
    OciModelDownloadLog,
    OciModelLineage,
    OciModelTag,
    OciModelVersion,
)
from app.oci_hub.schemas import (
    ImportResponse,
    LineageResponse,
    OciFacets,
    OciModelCreate,
    OciModelDetail,
    OciModelSummary,
    OciModelUpdate,
    OciModelVersionResponse,
    PaginatedOciModels,
)

logger = logging.getLogger(__name__)


class OciConflictError(ValueError):
    """이름 중복·중복 연결·진행 중 작업 등 상태 충돌(HTTP 409)."""


class OciNotFoundError(LookupError):
    """대상(모델·태그·리니지·버전) 없음(HTTP 404)."""


class OciUpstreamError(RuntimeError):
    """외부(HuggingFace) 조회 실패 — 400/502 로 구분해 알린다."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

async def get_oci_model(session: AsyncSession, name: str) -> OciModel | None:
    """이름으로 OCI 모델 ORM 객체를 조회한다. 없으면 ``None``."""
    return (await session.execute(select(OciModel).where(OciModel.name == name))).scalars().first()


async def _ensure_name_free(session: AsyncSession, name: str) -> None:
    """OCI·MLflow 두 레지스트리 모두에서 이름이 비어 있는지 (같은 S3 키 프리픽스를 쓰기 때문)."""
    from app.models.models import RegisteredModel

    if await get_oci_model(session, name):
        raise OciConflictError(f"Model '{name}' already exists")
    # MLflow 쪽은 소프트 삭제된 모델도 이름을 점유한다(복원 가능하므로)
    if (await session.execute(select(RegisteredModel.id).where(RegisteredModel.name == name))).first():
        raise OciConflictError(
            f"MLflow 모델 '{name}' 과 이름이 같습니다. 두 레지스트리는 같은 저장소 경로를 쓰므로 다른 이름을 사용하세요."
        )


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
        .order_by(OciModelLineage.created_at, OciModelLineage.id)
    )
    return [
        {
            "id": r.id, "source_type": r.source_type, "source_id": r.source_id,
            "source_name": r.source_name, "relation_type": r.relation_type,
            "description": r.description, "created_at": r.created_at.isoformat(),
        }
        for r in result.scalars().all()
    ]


async def _refresh_model_stats(session: AsyncSession, model: OciModel) -> None:
    """준비된(ready) 버전 기준으로 ``version_count``(최신 버전 번호)·``total_size``(전체 저장 용량) 갱신.

    커밋하지 않는다(호출 측이 커밋). ``version_count`` 는 이름과 달리 개수가 아니라 최대 버전 번호다.
    """
    row = (await session.execute(
        select(func.max(OciModelVersion.version), func.coalesce(func.sum(OciModelVersion.total_size), 0))
        .where(OciModelVersion.model_id == model.id, OciModelVersion.status == "ready")
    )).first()
    model.version_count = row[0] or 0
    model.total_size = int(row[1] or 0)


def _summary(m: OciModel, tags: list[dict]) -> OciModelSummary:
    """ORM 모델 + 태그 목록을 목록 카드용 요약 DTO 로 변환한다."""
    return OciModelSummary(
        id=m.id, name=m.name, display_name=m.display_name,
        description=m.description, task=m.task, framework=m.framework,
        language=m.language, license=m.license, source_type=m.source_type,
        owner=m.owner, created_by=m.created_by, version_count=m.version_count,
        total_size=m.total_size or 0, download_count=m.download_count,
        status=m.status, tags=tags,
        created_at=m.created_at, updated_at=m.updated_at,
    )


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
    """OCI 모델 목록을 필터·페이징과 함께 조회(최근 수정순). 검색어는 이름·표시 이름·설명에서 부분 일치로 찾는다.

    태스크·프레임워크·언어·상태는 정확히 일치. 태그는 모델마다 추가 쿼리로 가져온다.
    """
    base = select(OciModel)
    if search:
        like = like_contains(search)
        base = base.where(or_(
            OciModel.name.ilike(like, escape=LIKE_ESCAPE), OciModel.display_name.ilike(like, escape=LIKE_ESCAPE), OciModel.description.ilike(like, escape=LIKE_ESCAPE),
        ))
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
    query = base.order_by(OciModel.updated_at.desc(), OciModel.id.desc()).offset(offset).limit(page_size)
    models = (await session.execute(query)).scalars().all()
    items = [_summary(m, await _get_tags(session, m.id)) for m in models]

    logger.debug("OCI 모델 목록 조회: search=%s, total=%d, page=%d", search, total, page)
    return PaginatedOciModels(items=items, total=total, page=page, page_size=page_size)


async def get_facets(session: AsyncSession) -> OciFacets:
    """목록 필터 선택지 — 데이터에 실제 있는 태스크·프레임워크·언어·상태."""
    async def distinct(col) -> list[str]:
        rows = (await session.execute(select(col).where(col.isnot(None), col != "").distinct().order_by(col))).all()
        return [r[0] for r in rows]

    return OciFacets(
        tasks=await distinct(OciModel.task),
        frameworks=await distinct(OciModel.framework),
        languages=await distinct(OciModel.language),
        statuses=await distinct(OciModel.status),
    )


async def get_oci_created_by(session: AsyncSession, name: str) -> str | None:
    """OCI 모델 생성자 — 소유권 체크용."""
    return (await session.execute(
        select(OciModel.created_by).where(OciModel.name == name)
    )).scalar_one_or_none()


async def create_model(session: AsyncSession, req: OciModelCreate, created_by: str | None = None) -> OciModelDetail:
    """새 OCI 모델 등록. 이름 중복(OCI·MLflow) 시 ``OciConflictError``.

    ``display_name`` 기본값은 이름, ``owner`` 기본값은 생성자, ``source_type`` 기본값은 ``"my"``.
    저장 위치는 현재 설정 버킷의 ``{name}/`` 프리픽스로 고정한다.
    """
    try:
        await _ensure_name_free(session, req.name)
    except OciConflictError:
        logger.warning("OCI 모델 생성 거부 (이름 중복): %s", req.name)
        raise

    model = OciModel(
        name=req.name, display_name=req.display_name or req.name,
        description=req.description, readme=req.readme,
        task=req.task, framework=req.framework, language=req.language,
        license=req.license, source_type=req.source_type or "my", source_id=req.source_id,
        owner=req.owner or created_by, status=req.status, created_by=created_by,
        bucket=settings.os_bucket, storage_prefix=f"{req.name}/",
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    logger.info("OCI 모델 생성됨: %s (id=%d)", model.name, model.id)
    return await get_model_detail(session, model.name)


async def get_model_detail(session: AsyncSession, name: str) -> OciModelDetail | None:
    """모델 상세 + 태그·리니지를 조합해서 반환. 없으면 ``None``."""
    m = await get_oci_model(session, name)
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
        storage_prefix=m.storage_prefix, owner=m.owner, created_by=m.created_by,
        version_count=m.version_count, total_size=m.total_size or 0,
        download_count=m.download_count, status=m.status,
        tags=tags, lineage=lineage,
        created_at=m.created_at, updated_at=m.updated_at,
    )


async def update_model(session: AsyncSession, name: str, req: OciModelUpdate) -> OciModelDetail | None:
    """모델 메타데이터 부분 갱신(표시 이름 / 설명 / 소유자 / 태스크 / 프레임워크 / 상태 등). 없으면 ``None``.

    상태 권한(approved·production 은 관리자만) 검사는 라우터 책임이다.
    """
    m = await get_oci_model(session, name)
    if not m:
        return None

    # 보낸 필드만 반영 — 명시적 null 은 값 지우기. status 는 비울 수 없어 null 이면 무시.
    for field, val in req.model_dump(exclude_unset=True).items():
        if field == "status" and val is None:
            continue
        if isinstance(val, str):
            val = val.strip() or None
            if field == "status" and val is None:
                continue
        setattr(m, field, val)

    await session.commit()
    await session.refresh(m)
    logger.info("OCI 모델 갱신됨: %s", name)
    return await get_model_detail(session, name)


async def update_readme(session: AsyncSession, name: str, readme: str) -> bool:
    """모델 README(Markdown 본문) 를 갱신. 모델이 없으면 False."""
    m = await get_oci_model(session, name)
    if not m:
        return False
    m.readme = readme
    await session.commit()
    logger.info("OCI 모델 README 갱신됨: %s", name)
    return True


async def delete_model(session: AsyncSession, name: str) -> bool:
    """모델 및 관련 데이터(버전 / 태그 / 리니지) 일괄 삭제. S3 파일도 함께 제거.

    가져오기가 진행 중인 버전이 있으면 ``OciConflictError`` (업로드 중인 파일과 엇갈림 방지)."""
    m = await get_oci_model(session, name)
    if not m:
        return False
    if (await session.execute(select(OciModelVersion.id).where(
        OciModelVersion.model_id == m.id, OciModelVersion.status == "importing",
    ))).first():
        raise OciConflictError("가져오기가 진행 중인 버전이 있어 삭제할 수 없습니다. 완료(또는 실패) 후 다시 시도하세요.")
    # 저장 프리픽스는 이름 규칙상 항상 ``{name}/`` — 저장된 값이 다르게 오염돼 있어도 이름 기준으로만 지운다
    storage_prefix = f"{name}/"
    bucket = m.bucket
    # 다운로드 기록(이름 기준, FK 없음) 도 지운다 — 같은 이름으로 다시 만들면 예전 다운로드 수가 이어 붙지 않게
    await session.execute(sa_delete(OciModelDownloadLog).where(OciModelDownloadLog.model_name == name))
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
    """버킷의 prefix 하위 모든 객체 삭제 (페이지네이션). 반환: 삭제한 객체 수.

    버킷이 없거나 prefix 가 비었거나 ``/`` 면(버킷 전체 삭제 위험) 아무것도 하지 않는다.
    """
    if not bucket or not prefix or prefix in ("/", ""):
        logger.warning("S3 prefix 삭제 건너뜀(버킷/프리픽스 비어 있음): bucket=%s prefix=%r", bucket, prefix)
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
    """모델 ↔ 태그 매핑을 추가. 모델 없으면 False, 태그 없음 ``OciNotFoundError``, 이미 연결 ``OciConflictError``."""
    from sqlalchemy import text

    m = await get_oci_model(session, name)
    if not m:
        return False
    # catalog_tags 는 다른 모듈의 테이블이라 ORM import 없이 존재만 확인한다(바인드 파라미터 사용)
    if not (await session.execute(text("SELECT 1 FROM catalog_tags WHERE id = :t"), {"t": tag_id})).first():
        raise OciNotFoundError(f"태그 {tag_id} 을(를) 찾을 수 없습니다.")
    if (await session.execute(select(OciModelTag.id).where(
        OciModelTag.model_id == m.id, OciModelTag.tag_id == tag_id,
    ))).first():
        raise OciConflictError("이미 연결된 태그입니다.")
    session.add(OciModelTag(model_id=m.id, tag_id=tag_id))
    await session.commit()
    logger.info("OCI 모델 %s 에 태그 %d 추가", name, tag_id)
    return True


async def remove_tag(session: AsyncSession, name: str, tag_id: int) -> bool:
    """모델 ↔ 태그 매핑 제거. 모델이 없거나 연결돼 있지 않으면 False."""
    m = await get_oci_model(session, name)
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

async def list_lineage(session: AsyncSession, name: str) -> list[LineageResponse] | None:
    """모델의 리니지 엔트리를 생성순으로 반환한다. 모델이 없으면 ``None`` (라우터에서 404)."""
    m = await get_oci_model(session, name)
    if not m:
        return None
    entries = (await session.execute(
        select(OciModelLineage).where(OciModelLineage.model_id == m.id)
        .order_by(OciModelLineage.created_at, OciModelLineage.id)
    )).scalars().all()
    return [LineageResponse.model_validate(e) for e in entries]


async def list_linked_to_mlflow(session: AsyncSession, mlflow_model: str) -> list[dict]:
    """MLflow 등록 모델에서 파생된 OCI 모델 — 출처(source_type=mlflow) 또는 리니지로 연결된 것.

    관계(relation_type)는 리니지가 있으면 그 값, 출처로만 연결됐으면 ``converted_from``.
    한 모델에 같은 MLflow 모델을 가리키는 리니지가 여러 개면 *먼저 등록된* 리니지의 관계를 쓴다
    (조회할 때마다 결과가 달라지지 않게)."""
    via_lineage = select(OciModelLineage.model_id, OciModelLineage.relation_type).where(
        OciModelLineage.source_type.in_(("mlflow", "model")),
        OciModelLineage.source_id == mlflow_model,
    ).order_by(OciModelLineage.id)
    relations: dict[int, str] = {}
    for mid, rel in (await session.execute(via_lineage)).all():
        relations.setdefault(mid, rel)

    linked_by_source = (OciModel.source_type == "mlflow") & (OciModel.source_id == mlflow_model)
    condition = or_(OciModel.id.in_(list(relations)), linked_by_source) if relations else linked_by_source
    models = (await session.execute(
        select(OciModel).where(condition).order_by(OciModel.name)
    )).scalars().all()
    return [
        {
            "name": m.name,
            "display_name": m.display_name,
            "status": m.status,
            "framework": m.framework,
            "version_count": m.version_count,
            "relation_type": relations.get(m.id, "converted_from"),
        }
        for m in models
    ]


async def add_lineage(session: AsyncSession, name: str, **kwargs) -> LineageResponse | None:
    """리니지 엔트리 추가(데이터셋 / 부모 모델 등 외부 자원과 연결). 같은 연결이 있으면 ``OciConflictError``.

    ``kwargs`` 는 ``OciModelLineage`` 컬럼(source_type·source_id·source_name·relation_type·description).
    중복 판정은 (source_type, source_id, relation_type) 기준. 모델이 없으면 ``None``.
    """
    m = await get_oci_model(session, name)
    if not m:
        return None
    dup = (await session.execute(select(OciModelLineage.id).where(
        OciModelLineage.model_id == m.id,
        OciModelLineage.source_type == kwargs.get("source_type"),
        OciModelLineage.source_id == kwargs.get("source_id"),
        OciModelLineage.relation_type == kwargs.get("relation_type"),
    ))).first()
    if dup:
        raise OciConflictError("같은 리니지 연결이 이미 있습니다.")
    entry = OciModelLineage(model_id=m.id, **kwargs)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    logger.info("OCI 모델 %s 에 리니지 추가: %s -> %s", name, kwargs.get("relation_type"), kwargs.get("source_id"))
    return LineageResponse.model_validate(entry)


async def remove_lineage(session: AsyncSession, name: str, lineage_id: int) -> bool:
    """리니지 엔트리 삭제 — 해당 모델 소속일 때만(다른 모델의 엔트리 id 로 지우지 못하게)."""
    m = await get_oci_model(session, name)
    if not m:
        return False
    entry = (await session.execute(select(OciModelLineage).where(
        OciModelLineage.id == lineage_id, OciModelLineage.model_id == m.id,
    ))).scalars().first()
    if not entry:
        return False
    await session.delete(entry)
    await session.commit()
    logger.info("OCI 모델 %s 리니지 %d 제거됨", name, lineage_id)
    return True


# ---------------------------------------------------------------------------
# 버전
# ---------------------------------------------------------------------------

async def list_versions(session: AsyncSession, name: str) -> list[OciModelVersionResponse] | None:
    """모델의 전체 버전을 새 순서로 조회. 모델이 없으면 ``None`` (라우터에서 404)."""
    m = await get_oci_model(session, name)
    if not m:
        return None
    versions = (await session.execute(
        select(OciModelVersion).where(OciModelVersion.model_id == m.id)
        .order_by(OciModelVersion.version.desc())
    )).scalars().all()
    return [OciModelVersionResponse.model_validate(v) for v in versions]


async def delete_version(session: AsyncSession, name: str, version: int) -> bool | None:
    """버전 하나 삭제 — DB 행과 저장소의 ``{name}/v{version}/`` 파일을 지우고 모델 통계를 다시 계산한다.

    모델이 없으면 ``None``, 버전이 없으면 ``False`` (둘 다 라우터에서 404).
    가져오는 중(importing)인 버전은 백그라운드 작업이 파일을 쓰고 있으므로 ``OciConflictError``.
    파일 삭제 실패는 경고만 남기고 DB 삭제는 유지한다(모델 삭제와 같은 규칙)."""
    m = await get_oci_model(session, name)
    if not m:
        return None
    v = (await session.execute(select(OciModelVersion).where(
        OciModelVersion.model_id == m.id, OciModelVersion.version == version,
    ))).scalars().first()
    if not v:
        return False
    if v.status == "importing":
        raise OciConflictError(f"v{version} 은(는) 가져오기가 진행 중이라 삭제할 수 없습니다. 완료(또는 실패) 후 다시 시도하세요.")
    bucket = m.bucket or settings.os_bucket
    await session.delete(v)
    await session.flush()
    await _refresh_model_stats(session, m)
    await session.commit()
    try:
        deleted = await _delete_s3_prefix(bucket, f"{name}/v{version}/")
        logger.info("OCI 모델 %s v%d 의 저장소 객체 %d개 삭제", name, version, deleted)
    except Exception as e:  # noqa: BLE001 — 저장소 장애여도 DB 삭제는 유지
        logger.warning("OCI 모델 %s v%d 의 저장소 파일 삭제 실패: %s", name, version, e)
    logger.info("OCI 모델 버전 삭제됨: %s v%d", name, version)
    return True


# ---------------------------------------------------------------------------
# Finalize Push (SDK/수동 업로드)
# ---------------------------------------------------------------------------

async def finalize_push(
    session: AsyncSession,
    name: str,
    version: int,
    readme: str | None = None,
    approve: bool = False,
) -> OciModelVersionResponse | None:
    """수동 push 한 버전을 확정한다.

    S3 파일을 스캔해 OCI manifest 를 만들고 버전 레코드(파일 수·크기·다이제스트)를 작성한 뒤,
    모델 통계(``version_count`` / ``total_size``) 와 README 를 갱신한다.
    ``approve`` (관리자 확정) 이면 draft 모델을 approved 로 올린다 — 일반 사용자의 푸시는 상태를 바꾸지 않는다.

    이미 있는 버전(ready·failed)은 다시 확정해 덮어쓴다. 모델이 없으면 ``None``, 가져오는 중인 버전은
    ``OciConflictError``, 업로드된 파일이 없으면 ``ValueError``. S3 오류는 그대로 올린다.
    """
    from app.models import model_store

    model = await get_oci_model(session, name)
    if not model:
        return None

    existing = (await session.execute(
        select(OciModelVersion).where(
            OciModelVersion.model_id == model.id,
            OciModelVersion.version == version,
        )
    )).scalars().first()
    if existing and existing.status == "importing":
        raise OciConflictError(f"v{version} 은(는) 가져오기가 진행 중입니다.")

    # S3 파일 스캔 (이전 확정 때 만든 manifest.json 제외)
    file_count, total_size = await model_store.get_total_size(name, version)
    if file_count == 0:
        logger.warning("Finalize 거부 (S3 파일 없음): %s v%d", name, version)
        raise ValueError(f"{name}/v{version} 에 업로드된 파일이 없습니다 — 파일을 먼저 업로드하세요.")

    manifest = await model_store.generate_manifest(name, version)
    digest = model_store.manifest_digest(manifest)

    if existing:
        ver = existing
    else:
        ver = OciModelVersion(model_id=model.id, version=version)
        session.add(ver)
    ver.file_count = file_count
    ver.total_size = total_size
    ver.manifest = json.dumps(manifest)
    ver.content_digest = digest
    ver.status = "ready"

    await session.flush()
    await _refresh_model_stats(session, model)
    if readme:
        model.readme = readme
    if approve and model.status == "draft":
        model.status = "approved"

    await session.commit()
    await session.refresh(ver)

    logger.info("Finalize push: %s v%d (파일 %d개, %d bytes, %s)", name, version, file_count, total_size, digest[:19])
    return OciModelVersionResponse.model_validate(ver)


# ---------------------------------------------------------------------------
# HuggingFace 임포트 (접수는 즉시, 다운로드·업로드는 백그라운드)
# ---------------------------------------------------------------------------

_import_tasks: set[asyncio.Task] = set()


async def _check_hf_repo(hf_model_id: str, revision: str) -> None:
    """HF 저장소가 있는지 먼저 확인해 오타·비공개 모델을 즉시 알린다(다운로드 시작 전).

    동기 HF API 를 스레드에서 30초 제한으로 호출한다. 실패는 ``OciUpstreamError`` 로 바꾼다:
    gated·revision 없음 400, 저장소 없음 404, 연결 불가·타임아웃·기타 502.
    """
    logger.debug("HF 저장소 확인: hf=%s revision=%s", hf_model_id, revision)
    from huggingface_hub import HfApi
    from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError, RevisionNotFoundError

    api = HfApi()
    try:
        info = await asyncio.wait_for(asyncio.to_thread(api.model_info, hf_model_id, revision=revision), timeout=30)
        # gated 저장소도 메타데이터(model_info)는 공개라 통과한다 — 파일 접근 권한을 따로 확인한다
        if getattr(info, "gated", False):
            await asyncio.wait_for(asyncio.to_thread(api.auth_check, hf_model_id), timeout=30)
    except GatedRepoError as e:
        # RepositoryNotFoundError 의 하위 클래스이므로 먼저 잡아야 한다
        raise OciUpstreamError(f"'{hf_model_id}' 은(는) 접근 승인이 필요한(gated) 모델입니다.", 400) from e
    except RepositoryNotFoundError as e:
        raise OciUpstreamError(f"HuggingFace 에서 '{hf_model_id}' 모델을 찾을 수 없습니다(이름 또는 비공개 여부 확인).", 404) from e
    except RevisionNotFoundError as e:
        raise OciUpstreamError(f"'{hf_model_id}' 에 revision '{revision}' 이(가) 없습니다.", 400) from e
    except (asyncio.TimeoutError, OSError) as e:
        logger.warning("HF 저장소 확인 실패(연결 불가/타임아웃): hf=%s: %s", hf_model_id, e)
        raise OciUpstreamError(f"HuggingFace 에 연결할 수 없습니다: {e}", 502) from e
    except Exception as e:  # noqa: BLE001 — requests/httpx 연결 오류 등
        logger.warning("HF 저장소 확인 실패: hf=%s: %s", hf_model_id, e)
        raise OciUpstreamError(f"HuggingFace 조회 실패: {e}", 502) from e


async def start_hf_import(session: AsyncSession, **kwargs) -> ImportResponse:
    """HF 가져오기를 접수한다 — 저장소 확인 → 모델(없으면 생성)·버전(importing) 기록 → 백그라운드 실행.

    대용량 모델(수 GB)도 요청 타임아웃 없이 처리되도록 실제 다운로드·업로드는 백그라운드에서 하고,
    진행 상태는 버전 ``status`` (importing → ready | failed, 실패 사유는 ``extra_metadata.error``) 로 남긴다.

    ``kwargs``: hf_model_id(필수)·name·description·owner·task·framework·language·revision·created_by.
    이름이 없으면 ``hf_model_id`` 의 ``/`` 를 ``-`` 로 바꿔 쓴다. 이름 규칙 위반은 ``OciUpstreamError``(400),
    이미 가져오는 중이거나 이름이 MLflow 모델과 겹치면 ``OciConflictError``.
    백그라운드 작업은 모듈 전역 ``_import_tasks`` 에 참조를 붙들어 GC 로 취소되지 않게 한다.
    """
    hf_model_id = kwargs["hf_model_id"]
    name = kwargs.get("name") or hf_model_id.replace("/", "-")
    revision = kwargs.get("revision") or "main"

    from app.models.schemas import validate_model_name
    try:
        validate_model_name(name)
    except ValueError as e:
        raise OciUpstreamError(f"모델 이름 '{name}': {e}", 400) from e

    await _check_hf_repo(hf_model_id, revision)

    model = await get_oci_model(session, name)
    if not model:
        await _ensure_name_free(session, name)
        model = OciModel(
            name=name, display_name=name,
            description=kwargs.get("description"),
            task=kwargs.get("task"), framework=kwargs.get("framework"),
            language=kwargs.get("language"),
            source_type="huggingface", source_id=hf_model_id,
            source_revision=revision, owner=kwargs.get("owner") or kwargs.get("created_by"),
            created_by=kwargs.get("created_by"),
            bucket=settings.os_bucket, storage_prefix=f"{name}/",
            status="draft",
        )
        session.add(model)
        await session.flush()
    elif (await session.execute(select(OciModelVersion.id).where(
        OciModelVersion.model_id == model.id, OciModelVersion.status == "importing",
    ))).first():
        raise OciConflictError(f"'{name}' 은(는) 이미 가져오기가 진행 중입니다.")

    # 다음 버전 번호 — 실패한 버전 번호도 건너뛴다(같은 S3 키 재사용 방지)
    max_ver = (await session.execute(
        select(func.max(OciModelVersion.version)).where(OciModelVersion.model_id == model.id)
    )).scalar() or 0
    version = max(max_ver, model.version_count or 0) + 1
    logger.debug("HF 가져오기 버전 번호 결정: %s max_ver=%d version_count=%s → v%d",
                 name, max_ver, model.version_count, version)
    session.add(OciModelVersion(
        model_id=model.id, version=version, status="importing",
        extra_metadata={"source": f"huggingface:{hf_model_id}", "revision": revision},
    ))
    await session.commit()

    task = asyncio.create_task(_run_hf_import(model.id, name, version, hf_model_id, revision))
    _import_tasks.add(task)
    task.add_done_callback(_import_tasks.discard)
    logger.info("HF 가져오기 접수: %s -> %s v%d", hf_model_id, name, version)
    return ImportResponse(name=name, version=version, status="importing")


async def _run_hf_import(model_id: int, name: str, version: int, hf_model_id: str, revision: str) -> None:
    """백그라운드 가져오기 본체 — 요청 세션이 끝난 뒤 실행되므로 자체 DB 세션을 사용한다.

    성공: 버전 ready + manifest·통계 기록, 모델의 빈 framework·description·readme 보충, draft 면 approved.
    실패: 부분 업로드 정리 후 버전 failed + ``extra_metadata.error``. 예외를 밖으로 올리지 않는다.
    """
    from app.core.database import async_session
    from app.models import model_store

    try:
        metadata = await model_store.import_from_huggingface(
            hf_model_id=hf_model_id, model_name=name, version=version, revision=revision,
        )
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 버전 상태로 남긴다
        logger.error("HF 가져오기 실패: %s -> %s v%d: %s", hf_model_id, name, version, e)
        try:
            await _delete_s3_prefix(settings.os_bucket, f"{name}/v{version}/")
        except Exception as ce:  # noqa: BLE001
            logger.warning("실패한 가져오기의 부분 업로드 정리 실패 %s v%d: %s", name, version, ce)
        async with async_session() as session:
            ver = (await session.execute(select(OciModelVersion).where(
                OciModelVersion.model_id == model_id, OciModelVersion.version == version,
            ))).scalars().first()
            if ver:
                ver.status = "failed"
                ver.extra_metadata = {**(ver.extra_metadata or {}), "error": str(e)[:1000]}
                await session.commit()
        return

    ver_metadata = {
        "source": f"huggingface:{hf_model_id}",
        "revision": revision,
        **{k: metadata.get(k) for k in (
            "model_type", "architectures", "torch_dtype", "transformers_version", "hidden_size",
            "num_hidden_layers", "num_attention_heads", "vocab_size", "tokenizer_class",
        )},
    }
    ver_metadata = {k: v for k, v in ver_metadata.items() if v is not None}

    from app.models.model_store import manifest_digest
    async with async_session() as session:
        model = await session.get(OciModel, model_id)
        ver = (await session.execute(select(OciModelVersion).where(
            OciModelVersion.model_id == model_id, OciModelVersion.version == version,
        ))).scalars().first()
        if not model or not ver:  # 가져오는 동안 모델이 삭제된 경우(삭제는 진행 중이면 막지만 방어)
            logger.warning("HF 가져오기 완료 시점에 모델/버전이 없음: %s v%d", name, version)
            return
        ver.manifest = json.dumps(metadata.get("manifest"))
        ver.content_digest = manifest_digest(metadata["manifest"]) if metadata.get("manifest") else None
        ver.file_count = metadata["file_count"]
        ver.total_size = metadata["total_size"]
        ver.extra_metadata = ver_metadata
        ver.status = "ready"
        await session.flush()
        await _refresh_model_stats(session, model)

        if not model.framework and metadata.get("transformers_version"):
            model.framework = "pytorch"
        if metadata.get("model_type"):
            model.description = model.description or f"{metadata['model_type']} model from HuggingFace"
        if metadata.get("readme") and not model.readme:
            model.readme = metadata["readme"]
        # 관리자가 가져온 모델이므로 첫 성공 시 승인 상태로
        if model.status == "draft":
            model.status = "approved"
        await session.commit()

    logger.info("HF 가져오기 완료: %s v%d (파일 %d개, %d bytes)",
                name, version, metadata["file_count"], metadata["total_size"])


async def fail_stale_imports(session: AsyncSession) -> int:
    """서버 재시작 등으로 끊긴 가져오기(importing)를 failed 로 정리한다 — 기동 시 호출. 반환: 정리한 건수."""
    rows = (await session.execute(
        select(OciModelVersion).where(OciModelVersion.status == "importing")
    )).scalars().all()
    for v in rows:
        v.status = "failed"
        v.extra_metadata = {**(v.extra_metadata or {}), "error": "서버 재시작으로 가져오기가 중단되었습니다. 다시 가져오세요."}
    if rows:
        await session.commit()
        logger.warning("중단된 OCI 가져오기 %d건을 failed 로 정리", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# 대시보드 통계
# ---------------------------------------------------------------------------

async def get_hub_stats(session: AsyncSession) -> dict:
    """OCI 모델 허브 대시보드 통계 집계(요약 카드·차트·다운로드/게시 추이).

    ``total_downloads`` 는 모델의 비정규화 카운터 합, ``total_download`` 는 다운로드 로그 행 수다.
    게시 추이는 버전 ``created_at`` 기준이며 상태(ready/failed/importing)를 거르지 않는다.
    """
    # 전체 모델 수
    total_models = (await session.execute(select(func.count()).select_from(OciModel))).scalar() or 0

    # 전체 버전 수 (준비된 버전만 — 가져오는 중·실패 제외)
    total_versions = (await session.execute(
        select(func.count()).select_from(OciModelVersion).where(OciModelVersion.status == "ready")
    )).scalar() or 0

    # 출처 유형별 집계
    source_counts_raw = (await session.execute(
        select(OciModel.source_type, func.count()).group_by(OciModel.source_type)
    )).all()
    source_counts = {(s or "unknown"): c for s, c in source_counts_raw}
    hf_count = source_counts.get("huggingface", 0)
    # 직접 등록 = HuggingFace 가 아닌 모든 출처(내 모델·MLflow 변환·로컬 등)
    my_count = total_models - hf_count

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
    from datetime import timedelta, timezone

    from sqlalchemy import text

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

    # 게시 추이 (oci_model_versions.created_at 기반) — total_publish 와 같이 ready 버전만 센다
    # (importing·failed 버전이 추이에 섞이지 않게)
    # 시간별 게시 (24h)
    pub_1d_result = await session.execute(
        select(func.date_trunc("hour", OciModelVersion.created_at).label("hour"), func.count().label("count"))
        .where(OciModelVersion.created_at >= now - timedelta(hours=24), OciModelVersion.status == "ready")
        .group_by(text("hour")).order_by(text("hour"))
    )
    publish_1d = [{"date": r.hour.strftime("%H:%M") if r.hour else "", "count": r.count} for r in pub_1d_result.all()]

    # 일별 게시 (7d)
    pub_7d_result = await session.execute(
        select(func.date(OciModelVersion.created_at).label("day"), func.count().label("count"))
        .where(OciModelVersion.created_at >= now - timedelta(days=7), OciModelVersion.status == "ready")
        .group_by(func.date(OciModelVersion.created_at)).order_by(text("day"))
    )
    publish_7d = [{"date": str(r.day), "count": r.count} for r in pub_7d_result.all()]

    # 일별 게시 (30d)
    pub_30d_result = await session.execute(
        select(func.date(OciModelVersion.created_at).label("day"), func.count().label("count"))
        .where(OciModelVersion.created_at >= now - timedelta(days=30), OciModelVersion.status == "ready")
        .group_by(func.date(OciModelVersion.created_at)).order_by(text("day"))
    )
    publish_30d = [{"date": str(r.day), "count": r.count} for r in pub_30d_result.all()]

    total_publish = total_versions

    # 상태별 모델 수 · 저장 용량 · 최근 30일 다운로드 (대시보드 요약 카드용)
    status_counts = {
        st: c for st, c in (await session.execute(
            select(OciModel.status, func.count()).group_by(OciModel.status)
        )).all()
    }
    total_storage = (await session.execute(select(func.sum(OciModel.total_size)))).scalar() or 0
    download_30d_total = sum(d["count"] for d in download_30d)

    return {
        "status_counts": status_counts,
        "total_storage": int(total_storage),
        "download_30d_total": download_30d_total,
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
