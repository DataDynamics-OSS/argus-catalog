# SPDX-License-Identifier: Apache-2.0
"""데이터 카탈로그 서비스 계층.

데이터셋, 데이터 소스, 태그, 용어집 용어, 소유자에 대한 CRUD 작업을 제공한다.
"""

import logging
import os
import time

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import (
    DataPipeline,
    Dataset,
    DatasetColumnMapping,
    DatasetGlossaryTerm,
    DatasetLineage,
    DatasetProperty,
    DatasetSchema,
    DatasetTag,
    Datasource,
    DatasourceConfiguration,
    DatasourceDataType,
    DatasourceFeature,
    DatasourceStorageFormat,
    DatasourceTableType,
    GlossaryTerm,
    Owner,
    SchemaSnapshot,
    System,
    Tag,
)
from app.catalog.schemas import (
    CatalogStats,
    ColumnMappingCreate,
    ColumnMappingResponse,
    DatasetCreate,
    DatasetLineageCreate,
    DatasetLineageResponse,
    DatasetPropertyResponse,
    DatasetResponse,
    DatasetSummary,
    DatasetUpdate,
    DatasourceCreate,
    DatasourceDataTypeResponse,
    DatasourceFeatureResponse,
    DatasourceMetadataResponse,
    DatasourceResponse,
    DatasourceStorageFormatResponse,
    DatasourceTableTypeResponse,
    DatasourceUpdate,
    GlossaryTermCreate,
    GlossaryTermResponse,
    OwnerCreate,
    OwnerResponse,
    PaginatedDatasets,
    PaginatedSchemaSnapshots,
    PipelineCreate,
    PipelineResponse,
    PipelineUpdate,
    SchemaChangeEntry,
    SchemaFieldResponse,
    SchemaSnapshotResponse,
    TagCreate,
    TagResponse,
    TagUsage,
)

logger = logging.getLogger(__name__)


def _generate_datasource_id(datasource_type: str) -> str:
    """데이터 소스 ID 생성: {type}-{timestamp_hex}{random_hex} (suffix 합계 = 16자).

    형식: 10자 ms 타임스탬프 hex + 6자 랜덤 hex = 16자.
    같은 밀리초여도 랜덤 suffix 가 달라진다(2^24 = 1600만 경우의 수).
    """
    timestamp_ms = int(time.time() * 1000)
    ts_hex = format(timestamp_ms, "010x")
    rand_hex = os.urandom(3).hex()  # 6자 hex = 24비트
    return f"{datasource_type}-{ts_hex}{rand_hex}"


import re as _re

# 구 URN 의 환경 suffix (`.DEV.dataset` 등) 를 제거해 신 포맷으로 정규화한다.
# 마이그레이션 / 해소 폴백 / alias 에서 공용.
_ENV_URN_SUFFIX = _re.compile(r"\.(DEV|STAGING|PROD)\.dataset$")


def strip_env_urn(urn: str) -> str:
    """``{datasource}.{path}.{ENV}.dataset`` → ``{datasource}.{path}.dataset``."""
    return _ENV_URN_SUFFIX.sub(".dataset", urn)


def _generate_urn(datasource_id: str, path: str, entity_type: str = "dataset") -> str:
    """URN 생성: {datasource_id}.{path}.{type}

    환경(DEV/STAGING/PROD)은 URN 에 포함하지 않는다 — datasource_id 가 이미 인스턴스
    고유값이라 유일성에 충분하고, 환경은 ``Dataset.origin`` 컬럼으로 표현한다.
    이렇게 하면 서버 승격(DEV→PROD) 시에도 URN 이 불변이다.
    """
    return f"{datasource_id}.{path}.{entity_type}"


# ---------------------------------------------------------------------------
# 데이터 소스 작업
# ---------------------------------------------------------------------------

async def list_datasources(session: AsyncSession) -> list[DatasourceResponse]:
    """등록된 모든 데이터 소스를 이름순으로 조회한다."""
    result = await session.execute(select(Datasource).order_by(Datasource.name))
    return [DatasourceResponse.model_validate(p) for p in result.scalars().all()]


async def create_datasource(session: AsyncSession, req: DatasourceCreate) -> DatasourceResponse:
    datasource = Datasource(
        datasource_id=_generate_datasource_id(req.type),
        name=req.name,
        type=req.type,
        logo_url=req.logo_url,
        origin=req.origin.value,
    )
    session.add(datasource)
    await session.commit()
    await session.refresh(datasource)
    logger.info("데이터 소스 생성: %s (id=%d)", datasource.name, datasource.id)
    return DatasourceResponse.model_validate(datasource)


async def get_datasource(session: AsyncSession, datasource_id: int) -> DatasourceResponse | None:
    """ID 로 데이터 소스 하나를 조회한다. 없으면 None."""
    result = await session.execute(select(Datasource).where(Datasource.id == datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        return None
    return DatasourceResponse.model_validate(datasource)


async def update_datasource(
    session: AsyncSession, datasource_id: int, req: DatasourceUpdate
) -> DatasourceResponse | None:
    """데이터 소스 메타데이터를 수정한다. 없으면 None 반환."""
    result = await session.execute(select(Datasource).where(Datasource.id == datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        logger.warning("수정할 데이터 소스를 찾을 수 없음: id=%d", datasource_id)
        return None
    if req.name is not None:
        datasource.name = req.name
    if req.origin is not None and req.origin.value != datasource.origin:
        # 환경 변경 → 소속 데이터셋 origin 도 함께 갱신 (dataset.origin 은 datasource origin 을 미러).
        # URN 에는 origin 이 없으므로 식별자는 불변.
        from sqlalchemy import update as _sql_update
        old_origin = datasource.origin
        datasource.origin = req.origin.value
        result = await session.execute(
            _sql_update(Dataset)
            .where(Dataset.datasource_id == datasource_id)
            .values(origin=req.origin.value)
        )
        logger.info(
            "데이터 소스 origin 변경: id=%d %s->%s, 데이터셋 %d개 갱신",
            datasource_id, old_origin, req.origin.value, result.rowcount or 0,
        )
    await session.commit()
    await session.refresh(datasource)
    logger.info("데이터 소스 수정: %s (id=%d)", datasource.name, datasource.id)
    return DatasourceResponse.model_validate(datasource)


async def test_datasource_connection(datasource_type: str, config: dict) -> dict:
    """주어진 연결 설정으로 실제 접속을 시도해 성공 여부를 반환한다.

    백엔드가 드라이버를 보유한 유형(postgresql / mysql / mariadb)만 직접 테스트한다.
    그 외 유형은 metadata-sync 서비스가 담당하므로 여기서는 미지원으로 응답한다.
    """
    import asyncio
    import time

    t = (datasource_type or "").lower()
    host = config.get("host", "localhost")
    user = config.get("username", "")
    password = config.get("password", "")
    database = config.get("database")
    start = time.monotonic()
    try:
        if t == "postgresql":
            import asyncpg
            port = int(config.get("port", 5432))
            conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=host, port=port, user=user, password=password,
                    database=database or "postgres",
                ),
                timeout=8,
            )
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
        elif t in ("mysql", "mariadb"):
            import aiomysql
            port = int(config.get("port", 3306))
            conn = await asyncio.wait_for(
                aiomysql.connect(
                    host=host, port=port, user=user, password=password,
                    db=database or None,
                ),
                timeout=8,
            )
            try:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
            finally:
                conn.close()
        else:
            return {"ok": False, "message": f"서버 측 연결 테스트 미지원 유형: {datasource_type}"}
    except asyncio.TimeoutError:
        return {"ok": False, "message": "연결 시간 초과 (8초)"}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}

    return {"ok": True, "message": "연결 성공", "latency_ms": int((time.monotonic() - start) * 1000)}


async def get_datasource_delete_impact(session: AsyncSession, datasource_id: int) -> dict:
    """데이터 소스 삭제 시 영향 — 데이터셋 수 + 타 데이터 소스과 연결된 리니지 엣지 수.

    external_lineage 는 이 데이터 소스의 데이터셋과 *다른* 데이터 소스의 데이터셋을 잇는 엣지로,
    cascade 삭제 시 함께 사라진다(상대 데이터 소스 리니지에 영향).
    """
    from sqlalchemy import and_, not_, or_

    dataset_count = await get_datasource_dataset_count(session, datasource_id)
    dp = select(Dataset.id).where(Dataset.datasource_id == datasource_id).scalar_subquery()
    src_in = DatasetLineage.source_dataset_id.in_(dp)
    tgt_in = DatasetLineage.target_dataset_id.in_(dp)
    external_lineage = await session.scalar(
        select(func.count())
        .select_from(DatasetLineage)
        .where(or_(and_(src_in, not_(tgt_in)), and_(tgt_in, not_(src_in))))
    )
    return {"dataset_count": dataset_count, "external_lineage_count": int(external_lineage or 0)}


async def delete_datasource(
    session: AsyncSession, datasource_id: int, force: bool = False
) -> bool:
    """데이터 소스 삭제. force=True 면 소속 데이터셋(및 모든 하위)을 먼저 삭제한다.

    데이터셋 하위(스키마/리니지/컬럼매핑/댓글/임베딩/스냅샷/변경관리/URN alias)는 모두
    ``ON DELETE CASCADE`` 라 데이터셋 행만 지우면 DB 가 연쇄 삭제한다.
    """
    result = await session.execute(select(Datasource).where(Datasource.id == datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        logger.warning("삭제할 데이터 소스를 찾을 수 없음: id=%d", datasource_id)
        return False
    if force:
        from sqlalchemy import delete as _sql_delete
        res = await session.execute(
            _sql_delete(Dataset).where(Dataset.datasource_id == datasource_id)
        )
        logger.info("데이터 소스 id=%d 의 데이터셋 %d개 연쇄 삭제", res.rowcount or 0, datasource_id)
    await session.delete(datasource)
    await session.commit()
    logger.info("데이터 소스 삭제: %s (id=%d, force=%s)", datasource.name, datasource.id, force)
    return True


async def get_datasource_configuration(
    session: AsyncSession, datasource_id: int
) -> dict | None:
    """데이터 소스의 설정 dict 를 반환한다. 없으면 None."""
    import json as _json

    result = await session.execute(
        select(DatasourceConfiguration).where(
            DatasourceConfiguration.datasource_id == datasource_id
        )
    )
    row = result.scalars().first()
    if not row:
        return None
    return {
        "id": row.id,
        "datasource_id": row.datasource_id,
        "config": _json.loads(row.config_json),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def save_datasource_configuration(
    session: AsyncSession, datasource_id: int, config_dict: dict
) -> dict:
    """데이터 소스 설정을 upsert 하고 저장된 레코드를 반환한다."""
    import json as _json

    result = await session.execute(
        select(DatasourceConfiguration).where(
            DatasourceConfiguration.datasource_id == datasource_id
        )
    )
    row = result.scalars().first()

    config_text = _json.dumps(config_dict, ensure_ascii=False)

    if row:
        row.config_json = config_text
    else:
        row = DatasourceConfiguration(
            datasource_id=datasource_id,
            config_json=config_text,
        )
        session.add(row)

    await session.commit()
    await session.refresh(row)
    logger.info("데이터 소스 설정 저장: datasource_id=%d", datasource_id)
    return {
        "id": row.id,
        "datasource_id": row.datasource_id,
        "config": _json.loads(row.config_json),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def get_datasource_dataset_count(session: AsyncSession, datasource_id: int) -> int:
    """주어진 데이터 소스에 속한 데이터셋 수를 센다."""
    result = await session.execute(
        select(func.count()).select_from(Dataset).where(
            Dataset.datasource_id == datasource_id,
            Dataset.status != "removed",
        )
    )
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# 태그 작업
# ---------------------------------------------------------------------------

async def list_tags(session: AsyncSession) -> list[TagResponse]:
    """전체 태그 목록을 이름 오름차순으로 조회."""
    result = await session.execute(select(Tag).order_by(Tag.name))
    return [TagResponse.model_validate(t) for t in result.scalars().all()]


async def create_tag(session: AsyncSession, req: TagCreate) -> TagResponse:
    """태그 생성(이름·설명·색상)."""
    tag = Tag(name=req.name, description=req.description, color=req.color)
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    logger.info("태그 생성: %s (id=%d)", tag.name, tag.id)
    return TagResponse.model_validate(tag)


async def get_tag_usage(session: AsyncSession, tag_id: int) -> TagUsage | None:
    """태그 사용 현황(어떤 데이터셋이 참조하는지) 조회. 태그 삭제 전 영향 확인용."""
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalars().first()
    if not tag:
        return None

    tag_resp = TagResponse.model_validate(tag)

    # 이 태그를 사용하는 데이터셋 조회
    dataset_query = (
        select(
            Dataset.id,
            Dataset.urn,
            Dataset.name,
            Dataset.display_name,
            Datasource.name.label("datasource_name"),
            Datasource.type.label("datasource_type"),
            Dataset.summary,
            Dataset.description,
            Dataset.origin,
            Dataset.status,
            Dataset.created_at,
            Dataset.updated_at,
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(
            Dataset.id.in_(
                select(DatasetTag.dataset_id).where(DatasetTag.tag_id == tag_id)
            )
        )
        .order_by(Dataset.name)
    )
    result = await session.execute(dataset_query)
    rows = result.all()

    datasets = [
        DatasetSummary(
            id=row.id,
            urn=row.urn,
            name=row.name,
            display_name=row.display_name,
            datasource_name=row.datasource_name,
            datasource_type=row.datasource_type,
            summary=row.summary,
            description=row.description,
            origin=row.origin,
            status=row.status,
            is_synced="false",
            tag_count=0,
            owner_count=0,
            schema_field_count=0,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]

    return TagUsage(tag=tag_resp, datasets=datasets, total_datasets=len(datasets))


async def delete_tag(session: AsyncSession, tag_id: int) -> bool:
    """태그 삭제. 모든 데이터셋 연결은 CASCADE 로 함께 제거된다."""
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalars().first()
    if not tag:
        logger.warning("삭제할 태그를 찾을 수 없음: id=%d", tag_id)
        return False
    await session.delete(tag)
    await session.commit()
    logger.info("태그 삭제: %s (id=%d)", tag.name, tag.id)
    return True


# ---------------------------------------------------------------------------
# 용어집 용어 작업
# ---------------------------------------------------------------------------

async def list_glossary_terms(session: AsyncSession) -> list[GlossaryTermResponse]:
    """모든 용어집 용어를 이름순으로 조회한다."""
    result = await session.execute(select(GlossaryTerm).order_by(GlossaryTerm.name))
    return [GlossaryTermResponse.model_validate(t) for t in result.scalars().all()]


async def create_glossary_term(
    session: AsyncSession, req: GlossaryTermCreate
) -> GlossaryTermResponse:
    term = GlossaryTerm(
        name=req.name, description=req.description,
        parent_id=req.parent_id,
        term_type=req.term_type,
    )
    session.add(term)
    await session.commit()
    await session.refresh(term)
    logger.info("용어집 용어 생성: %s (id=%d)", term.name, term.id)

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 생성 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("glossary_term", term.id)

    return GlossaryTermResponse.model_validate(term)


async def update_glossary_term(
    session: AsyncSession, term_id: int, data: dict,
) -> GlossaryTermResponse | None:
    result = await session.execute(
        select(GlossaryTerm).where(GlossaryTerm.id == term_id)
    )
    term = result.scalars().first()
    if not term:
        return None
    for k, v in data.items():
        if hasattr(term, k):
            setattr(term, k, v)
    await session.commit()
    await session.refresh(term)
    logger.info("용어집 용어 수정: id=%d, name=%s", term.id, term.name)

    from app.embedding.service import embed_entity_background
    await embed_entity_background("glossary_term", term.id)

    return GlossaryTermResponse.model_validate(term)


async def delete_glossary_term(session: AsyncSession, term_id: int) -> bool:
    """용어집 용어를 삭제하고 모든 데이터셋 연결을 제거한다."""
    result = await session.execute(
        select(GlossaryTerm).where(GlossaryTerm.id == term_id)
    )
    term = result.scalars().first()
    if not term:
        logger.warning("삭제할 용어집 용어를 찾을 수 없음: id=%d", term_id)
        return False
    # 임베딩 행 정리 (다형 테이블이라 FK CASCADE 가 없음)
    from app.embedding.service import delete_entity_embedding
    await delete_entity_embedding(session, "glossary_term", term_id)

    await session.delete(term)
    await session.commit()
    logger.info("용어집 용어 삭제: %s (id=%d)", term.name, term.id)
    return True


# ---------------------------------------------------------------------------
# 데이터셋 작업
# ---------------------------------------------------------------------------

async def _build_dataset_response(
    session: AsyncSession, dataset: Dataset
) -> DatasetResponse:
    """관련 엔티티를 모두 포함한 전체 DatasetResponse 를 구성한다."""
    # 데이터 소스
    result = await session.execute(select(Datasource).where(Datasource.id == dataset.datasource_id))
    datasource = result.scalars().first()

    # 스키마 필드
    result = await session.execute(
        select(DatasetSchema)
        .where(DatasetSchema.dataset_id == dataset.id)
        .order_by(DatasetSchema.ordinal)
    )
    schema_fields = [SchemaFieldResponse.model_validate(f) for f in result.scalars().all()]

    # 태그
    result = await session.execute(
        select(Tag)
        .join(DatasetTag, DatasetTag.tag_id == Tag.id)
        .where(DatasetTag.dataset_id == dataset.id)
    )
    tags = [TagResponse.model_validate(t) for t in result.scalars().all()]

    # 소유자
    result = await session.execute(
        select(Owner).where(Owner.dataset_id == dataset.id)
    )
    owners = [OwnerResponse.model_validate(o) for o in result.scalars().all()]

    # 용어집 용어
    result = await session.execute(
        select(GlossaryTerm)
        .join(DatasetGlossaryTerm, DatasetGlossaryTerm.term_id == GlossaryTerm.id)
        .where(DatasetGlossaryTerm.dataset_id == dataset.id)
    )
    glossary_terms = [GlossaryTermResponse.model_validate(t) for t in result.scalars().all()]

    # 속성
    result = await session.execute(
        select(DatasetProperty).where(DatasetProperty.dataset_id == dataset.id)
    )
    properties = [DatasetPropertyResponse.model_validate(p) for p in result.scalars().all()]

    import json as _json
    pp = None
    if dataset.datasource_properties:
        try:
            pp = _json.loads(dataset.datasource_properties)
        except (ValueError, TypeError):
            pass

    return DatasetResponse(
        id=dataset.id,
        urn=dataset.urn,
        created_by=dataset.created_by,
        name=dataset.name,
        display_name=dataset.display_name,
        datasource=DatasourceResponse.model_validate(datasource),
        summary=dataset.summary,
        description=dataset.description,
        origin=dataset.origin,
        qualified_name=dataset.qualified_name,
        table_type=dataset.table_type,
        storage_format=dataset.storage_format,
        ddl=dataset.ddl,
        status=dataset.status,
        is_synced=dataset.is_synced or "false",
        ingestion_frequency=dataset.ingestion_frequency,
        ingestion_time=dataset.ingestion_time,
        ingestion_day=dataset.ingestion_day,
        ingestion_timezone=dataset.ingestion_timezone,
        ingestion_cron=dataset.ingestion_cron,
        ingestion_mode=dataset.ingestion_mode,
        update_type=dataset.update_type,
        freshness_sla=dataset.freshness_sla,
        last_ingested_at=dataset.last_ingested_at,
        retention_days=dataset.retention_days,
        purge_days=dataset.purge_days,
        data_category=dataset.data_category,
        data_format=dataset.data_format,
        compression=dataset.compression,
        encoding=dataset.encoding,
        row_count=dataset.row_count,
        byte_size=dataset.byte_size,
        file_count=dataset.file_count,
        sensitivity=dataset.sensitivity,
        contains_pii=(dataset.contains_pii == "true") if dataset.contains_pii is not None else None,
        pii_fields=dataset.pii_fields,
        compliance_tags=dataset.compliance_tags,
        tier=dataset.tier,
        certification=dataset.certification,
        steward=dataset.steward,
        view_count=dataset.view_count or 0,
        query_count=dataset.query_count or 0,
        last_accessed_at=dataset.last_accessed_at,
        quality_score=dataset.quality_score,
        quality_status=dataset.quality_status,
        show_quality_score=(dataset.show_quality_score == "true"),
        note=dataset.note,
        datasource_properties=pp,
        schema_fields=schema_fields,
        tags=tags,
        owners=owners,
        glossary_terms=glossary_terms,
        properties=properties,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


async def get_dataset_created_by(session: AsyncSession, dataset_id: int) -> str | None:
    """데이터셋의 생성자(created_by) — 소유권 체크용. 데이터셋이 없으면 None."""
    return (await session.execute(
        select(Dataset.created_by).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()


async def create_dataset(session: AsyncSession, req: DatasetCreate, created_by: str | None = None) -> DatasetResponse:
    # URN 생성을 위한 데이터 소스 조회
    result = await session.execute(select(Datasource).where(Datasource.id == req.datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        raise ValueError(f"Datasource with id {req.datasource_id} not found")

    path = req.qualified_name or req.name
    urn = _generate_urn(datasource.datasource_id, path)
    qualified_name = f"{datasource.datasource_id}.{path}"

    import json as _json
    dataset = Dataset(
        urn=urn,
        name=req.name,
        display_name=req.display_name,
        datasource_id=req.datasource_id,
        summary=req.summary,
        description=req.description,
        created_by=created_by,
        origin=req.origin.value,
        qualified_name=qualified_name,
        table_type=req.table_type,
        storage_format=req.storage_format,
        ddl=req.ddl,
        # DatasourceSpecificCard 가 columns/indexes 를 읽는 자리 — JSON 직렬화 저장.
        datasource_properties=(
            _json.dumps(req.datasource_properties, ensure_ascii=False)
            if req.datasource_properties else None
        ),
        status="active",
    )
    session.add(dataset)
    await session.flush()

    # 스키마 필드 추가
    for idx, field in enumerate(req.schema_fields):
        schema_field = DatasetSchema(
            dataset_id=dataset.id,
            field_path=field.field_path,
            display_name=field.display_name,
            field_type=field.field_type,
            native_type=field.native_type,
            description=field.description,
            nullable=field.nullable,
            is_primary_key=field.is_primary_key,
            is_unique=field.is_unique,
            is_indexed=field.is_indexed,
            is_partition_key=field.is_partition_key,
            is_distribution_key=field.is_distribution_key,
            ordinal=field.ordinal or idx,
        )
        session.add(schema_field)

    # 태그 부착
    for tag_id in req.tags:
        session.add(DatasetTag(dataset_id=dataset.id, tag_id=tag_id))

    # 소유자 추가
    for owner in req.owners:
        session.add(Owner(
            dataset_id=dataset.id,
            owner_name=owner.owner_name,
            owner_type=owner.owner_type.value,
        ))

    # 속성 추가
    for prop in req.properties:
        session.add(DatasetProperty(
            dataset_id=dataset.id,
            property_key=prop.key,
            property_value=prop.value,
        ))

    await session.commit()
    await session.refresh(dataset)
    logger.info("데이터셋 생성: %s (id=%d, urn=%s)", dataset.name, dataset.id, dataset.urn)

    # 시맨틱 검색용 백그라운드 임베딩 트리거
    from app.embedding.service import embed_dataset_background
    await embed_dataset_background(dataset.id)

    # 쿼리 lineage 의 dataset_id 점증 해석 — 이 데이터셋명과 매칭되는 lineage 엣지를 연결.
    try:
        from app.catalog.lineage_resolve import resolve_lineage_for_dataset
        await resolve_lineage_for_dataset(session, dataset.id, dataset.qualified_name)
    except Exception as e:
        logger.warning("생성 시 리니지 해석 실패 (%s): %s", dataset.name, e)

    return await _build_dataset_response(session, dataset)


async def get_dataset(session: AsyncSession, dataset_id: int) -> DatasetResponse | None:
    """ID 로 데이터셋 하나를 모든 연관 관계와 함께 조회한다."""
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalars().first()
    if not dataset:
        return None
    return await _build_dataset_response(session, dataset)


async def get_dataset_by_urn(session: AsyncSession, urn: str) -> DatasetResponse | None:
    """URN 으로 데이터셋 하나를 모든 연관 관계와 함께 조회한다.

    구 URN(`...DEV.dataset`)으로 들어와도 해소되도록 폴백한다:
    직접 매칭 → 환경 suffix 제거 정규화 → URN alias 테이블.
    """
    result = await session.execute(select(Dataset).where(Dataset.urn == urn))
    dataset = result.scalars().first()

    if dataset is None:
        normalized = strip_env_urn(urn)
        if normalized != urn:
            result = await session.execute(select(Dataset).where(Dataset.urn == normalized))
            dataset = result.scalars().first()

    if dataset is None:
        from app.catalog.models import DatasetUrnAlias
        result = await session.execute(
            select(Dataset)
            .join(DatasetUrnAlias, DatasetUrnAlias.dataset_id == Dataset.id)
            .where(DatasetUrnAlias.old_urn == urn)
        )
        dataset = result.scalars().first()

    if not dataset:
        return None
    return await _build_dataset_response(session, dataset)


async def update_dataset(
    session: AsyncSession, dataset_id: int, req: DatasetUpdate
) -> DatasetResponse | None:
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalars().first()
    if not dataset:
        return None

    if req.name is not None:
        dataset.name = req.name
    if req.display_name is not None:
        # 빈 문자열을 보내면 명시적으로 logical name 을 지우는 것으로 간주.
        dataset.display_name = req.display_name or None
    if req.summary is not None:
        dataset.summary = req.summary
    if req.description is not None:
        dataset.description = req.description
    if req.origin is not None:
        dataset.origin = req.origin.value
    if req.qualified_name is not None:
        dataset.qualified_name = req.qualified_name
    if req.table_type is not None:
        dataset.table_type = req.table_type
    if req.storage_format is not None:
        dataset.storage_format = req.storage_format
    if req.ddl is not None:
        # 빈 문자열도 의미 있는 값으로 받아들여 그대로 저장. NULL 로 되돌리려면 명시적 None
        # 을 보내야 한다 (Pydantic Optional, None 이면 이 분기를 타지 않음).
        dataset.ddl = req.ddl
    if req.datasource_properties is not None:
        import json as _json
        dataset.datasource_properties = (
            _json.dumps(req.datasource_properties, ensure_ascii=False)
            if req.datasource_properties else None
        )
    if req.status is not None:
        dataset.status = req.status.value

    # --- 확장 메타데이터 (단순 passthrough; None 이면 미변경) ---
    _ext_fields = (
        "ingestion_frequency", "ingestion_time", "ingestion_day", "ingestion_timezone",
        "ingestion_cron", "ingestion_mode", "update_type", "freshness_sla",
        "last_ingested_at", "retention_days", "purge_days",
        "data_category", "data_format", "compression", "encoding", "row_count", "byte_size", "file_count",
        "sensitivity", "pii_fields", "compliance_tags",
        "tier", "certification", "steward", "quality_status", "note",
    )
    for f in _ext_fields:
        v = getattr(req, f, None)
        if v is not None:
            setattr(dataset, f, v)
    # bool → "true"/"false" 문자열로 저장
    if req.contains_pii is not None:
        dataset.contains_pii = "true" if req.contains_pii else "false"
    if req.show_quality_score is not None:
        dataset.show_quality_score = "true" if req.show_quality_score else "false"

    # qualified_name 이 바뀔 때만 URN 을 재생성한다.
    # origin 은 더 이상 URN 에 포함되지 않으므로 승격(origin 변경) 시 URN 은 불변이다.
    if req.qualified_name is not None:
        datasource_row = await session.execute(
            select(Datasource).where(Datasource.id == dataset.datasource_id)
        )
        datasource = datasource_row.scalars().first()
        if datasource:
            qn = dataset.qualified_name or dataset.name
            prefix = f"{datasource.datasource_id}."
            path = qn[len(prefix):] if qn.startswith(prefix) else qn
            dataset.urn = _generate_urn(datasource.datasource_id, path)

    await session.commit()
    await session.refresh(dataset)
    logger.info("데이터셋 수정: %s (id=%d)", dataset.name, dataset.id)

    # 시맨틱 검색용 재임베딩
    from app.embedding.service import embed_dataset_background
    await embed_dataset_background(dataset.id)

    return await _build_dataset_response(session, dataset)


async def delete_dataset(session: AsyncSession, dataset_id: int) -> bool:
    """데이터셋과 모든 연관 관계(태그, 소유자, 스키마 등)를 삭제한다."""
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalars().first()
    if not dataset:
        return False
    await session.delete(dataset)
    await session.commit()
    logger.info("데이터셋 삭제: %s (id=%d)", dataset.name, dataset.id)
    return True


async def list_datasets(
    session: AsyncSession,
    search: str | None = None,
    datasource: str | None = None,
    origin: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    org_id: int | None = None,
    system_id: int | None = None,
    category_id: int | None = None,
    taxonomy_id: int | None = None,
    uncategorized: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedDatasets:
    """선택적 필터·페이지네이션으로 데이터셋 목록을 조회한다.

    ``org_id`` 는 해당 조직 + 모든 하위 조직(서브트리)의 시스템에 속한 데이터 소스의
    데이터셋을 포함한다. ``system_id`` 는 해당 시스템의 데이터 소스만 필터.
    ``category_id`` 는 해당 분류 + 모든 하위 분류(서브트리)에 매핑된 데이터셋.
    ``taxonomy_id`` + ``uncategorized=True`` 는 해당 분류체계에 미매핑된 데이터셋.
    """
    base = (
        select(
            Dataset.id,
            Dataset.urn,
            Dataset.name,
            Dataset.display_name,
            Datasource.name.label("datasource_name"),
            Datasource.type.label("datasource_type"),
            Dataset.summary,
            Dataset.description,
            Dataset.origin,
            Dataset.status,
            Dataset.is_synced,
            Dataset.created_at,
            Dataset.updated_at,
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
    )

    if search:
        pattern = f"%{search}%"
        base = base.where(
            or_(
                Dataset.name.ilike(pattern),
                Dataset.display_name.ilike(pattern),
                Dataset.summary.ilike(pattern),
                Dataset.description.ilike(pattern),
                Dataset.urn.ilike(pattern),
                Dataset.qualified_name.ilike(pattern),
            )
        )

    if datasource:
        base = base.where(Datasource.datasource_id == datasource)

    if system_id is not None:
        base = base.where(Datasource.system_id == system_id)

    if org_id is not None:
        # 조직 서브트리(자기 + 하위)의 시스템에 속한 데이터 소스만
        from app.catalog.topology_service import _descendant_org_ids
        org_ids = {org_id} | await _descendant_org_ids(session, org_id)
        base = base.where(
            Datasource.system_id.in_(
                select(System.id).where(System.org_id.in_(org_ids))
            )
        )

    if category_id is not None:
        # 분류 서브트리(자기 + 하위)에 매핑된 데이터셋
        from app.catalog.models import Category, DatasetCategory
        from app.catalog.taxonomy_service import _descendant_category_ids
        cat_ids = {category_id} | await _descendant_category_ids(session, category_id)
        base = base.where(
            Dataset.id.in_(
                select(DatasetCategory.dataset_id).where(DatasetCategory.category_id.in_(cat_ids))
            )
        )

    if taxonomy_id is not None and uncategorized:
        # 해당 분류체계의 어떤 카테고리에도 매핑되지 않은 데이터셋
        from app.catalog.models import Category, DatasetCategory
        mapped_sub = (
            select(DatasetCategory.dataset_id)
            .join(Category, Category.id == DatasetCategory.category_id)
            .where(Category.taxonomy_id == taxonomy_id)
        )
        base = base.where(Dataset.id.notin_(mapped_sub))

    if origin:
        base = base.where(Dataset.origin == origin)

    if status:
        base = base.where(Dataset.status == status)
    else:
        base = base.where(Dataset.status != "removed")

    if tag:
        base = base.where(
            Dataset.id.in_(
                select(DatasetTag.dataset_id)
                .join(Tag, DatasetTag.tag_id == Tag.id)
                .where(Tag.name == tag)
            )
        )

    # 개수 집계
    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # 페이지네이션
    offset = (page - 1) * page_size
    query = base.order_by(Dataset.updated_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    rows = result.all()

    # 개수 정보를 포함한 요약 구성
    items = []
    for row in rows:
        # 각 데이터셋의 개수 조회
        tag_count = (await session.execute(
            select(func.count()).where(DatasetTag.dataset_id == row.id)
        )).scalar() or 0
        owner_count = (await session.execute(
            select(func.count()).where(Owner.dataset_id == row.id)
        )).scalar() or 0
        field_count = (await session.execute(
            select(func.count()).where(DatasetSchema.dataset_id == row.id)
        )).scalar() or 0

        items.append(DatasetSummary(
            id=row.id,
            urn=row.urn,
            name=row.name,
            display_name=row.display_name,
            datasource_name=row.datasource_name,
            datasource_type=row.datasource_type,
            summary=row.summary,
            description=row.description,
            origin=row.origin,
            status=row.status,
            is_synced=row.is_synced or "false",
            tag_count=tag_count,
            owner_count=owner_count,
            schema_field_count=field_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
        ))

    return PaginatedDatasets(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# 데이터셋 연관 관계 작업
# ---------------------------------------------------------------------------

async def add_dataset_tag(session: AsyncSession, dataset_id: int, tag_id: int) -> bool:
    """데이터셋에 태그를 연결한다. 데이터셋이 없으면 False."""
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    if not result.scalars().first():
        logger.warning("태그 추가 대상 데이터셋을 찾을 수 없음: dataset_id=%d", dataset_id)
        return False
    session.add(DatasetTag(dataset_id=dataset_id, tag_id=tag_id))
    await session.commit()
    logger.info("태그 %d 를 데이터셋 %d 에 추가", tag_id, dataset_id)
    return True


async def remove_dataset_tag(session: AsyncSession, dataset_id: int, tag_id: int) -> bool:
    """데이터셋-태그 연결 제거. 연결이 없으면 False."""
    result = await session.execute(
        select(DatasetTag)
        .where(DatasetTag.dataset_id == dataset_id, DatasetTag.tag_id == tag_id)
    )
    dt = result.scalars().first()
    if not dt:
        return False
    await session.delete(dt)
    await session.commit()
    logger.info("태그 %d 를 데이터셋 %d 에서 제거", tag_id, dataset_id)
    return True


async def add_dataset_owner(
    session: AsyncSession, dataset_id: int, req: OwnerCreate
) -> OwnerResponse | None:
    """데이터셋에 소유자를 추가한다."""
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    if not result.scalars().first():
        logger.warning("소유자 추가 대상 데이터셋을 찾을 수 없음: dataset_id=%d", dataset_id)
        return None
    owner = Owner(
        dataset_id=dataset_id,
        owner_name=req.owner_name,
        owner_type=req.owner_type.value,
    )
    session.add(owner)
    await session.commit()
    await session.refresh(owner)
    logger.info("소유자 '%s' (%s) 를 데이터셋 %d 에 추가", req.owner_name, req.owner_type.value, dataset_id)
    return OwnerResponse.model_validate(owner)


async def remove_dataset_owner(session: AsyncSession, owner_id: int) -> bool:
    """데이터셋에서 소유자를 제거한다."""
    result = await session.execute(select(Owner).where(Owner.id == owner_id))
    owner = result.scalars().first()
    if not owner:
        return False
    await session.delete(owner)
    await session.commit()
    logger.info("소유자 제거: id=%d", owner_id)
    return True


async def add_dataset_glossary_term(
    session: AsyncSession, dataset_id: int, term_id: int
) -> bool:
    """데이터셋에 용어집 용어를 연결한다."""
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    if not result.scalars().first():
        logger.warning("용어집 용어 추가 대상 데이터셋을 찾을 수 없음: dataset_id=%d", dataset_id)
        return False
    session.add(DatasetGlossaryTerm(dataset_id=dataset_id, term_id=term_id))
    await session.commit()
    logger.info("용어집 용어 %d 를 데이터셋 %d 에 추가", term_id, dataset_id)
    return True


async def remove_dataset_glossary_term(
    session: AsyncSession, dataset_id: int, term_id: int
) -> bool:
    """데이터셋에서 용어집 용어 연결을 제거한다."""
    result = await session.execute(
        select(DatasetGlossaryTerm)
        .where(DatasetGlossaryTerm.dataset_id == dataset_id,
               DatasetGlossaryTerm.term_id == term_id)
    )
    dgt = result.scalars().first()
    if not dgt:
        return False
    await session.delete(dgt)
    await session.commit()
    logger.info("용어집 용어 %d 를 데이터셋 %d 에서 제거", term_id, dataset_id)
    return True


async def update_schema_fields(
    session: AsyncSession, dataset_id: int, fields: list[dict]
) -> list[SchemaFieldResponse]:
    """데이터셋의 스키마 필드 전체를 교체한다."""
    from types import SimpleNamespace

    from sqlalchemy import delete as sql_delete

    # DELETE 전에 기존 fields 를 가져와 변경 이력 스냅샷을 만든다. metadata-sync 어댑터가
    # 호출하는 경로라 dict payload 가 들어오는데, ``save_schema_snapshot`` 의 from_sync=True
    # 모드는 ``col.name`` 같이 attribute 접근을 요구하므로 SimpleNamespace 로 변환해 넘긴다.
    existing_result = await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
    )
    old_fields = list(existing_result.scalars().all())
    # sync 어댑터는 logical(display_name)을 모른다. 기존 사용자 설정값을 field_path 기준으로
    # 보존했다가 재생성 시 다시 붙여 준다. 페이로드에 display_name 이 명시되어 있으면 그것을 우선.
    preserved_display_names = {
        f.field_path: f.display_name for f in old_fields if f.display_name
    }
    new_columns_obj = [
        SimpleNamespace(
            name=f["field_path"],
            data_type=f["field_type"],
            native_type=f.get("native_type") or "",
            nullable=str(f.get("nullable", "true")).lower() == "true",
            is_primary_key=str(f.get("is_primary_key", "false")).lower() == "true",
            is_unique=str(f.get("is_unique", "false")).lower() == "true",
            is_indexed=str(f.get("is_indexed", "false")).lower() == "true",
            ordinal=int(f.get("ordinal", idx)),
        )
        for idx, f in enumerate(fields)
    ]
    try:
        await save_schema_snapshot(
            session, dataset_id, old_fields, new_columns_obj, from_sync=True,
        )
    except Exception as e:
        # snapshot 실패가 schema 갱신 자체를 막아서는 안 된다 (예: schema_snapshots 권한 이슈).
        logger.warning("스키마 스냅샷 저장 실패 dataset_id=%d: %s", dataset_id, e)

    # 기존 행을 명시적 DELETE 후 flush — properties 와 같은 ordering 안전화.
    await session.execute(
        sql_delete(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
    )
    await session.flush()

    # 새 필드 추가
    new_fields = []
    for idx, field_data in enumerate(fields):
        field_path = field_data["field_path"]
        schema_field = DatasetSchema(
            dataset_id=dataset_id,
            field_path=field_path,
            display_name=field_data.get("display_name")
                or preserved_display_names.get(field_path),
            field_type=field_data["field_type"],
            native_type=field_data.get("native_type"),
            description=field_data.get("description"),
            nullable=field_data.get("nullable", "true"),
            is_primary_key=field_data.get("is_primary_key", "false"),
            is_unique=field_data.get("is_unique", "false"),
            is_indexed=field_data.get("is_indexed", "false"),
            is_partition_key=field_data.get("is_partition_key", "false"),
            is_distribution_key=field_data.get("is_distribution_key", "false"),
            ordinal=field_data.get("ordinal", idx),
        )
        session.add(schema_field)
        new_fields.append(schema_field)

    await session.commit()
    for f in new_fields:
        await session.refresh(f)
    # 스키마 변경 → 검색용 임베딩 source_text 도 바뀌어야 한다. 백그라운드로 재임베딩.
    from app.embedding.service import embed_dataset_background
    await embed_dataset_background(dataset_id)
    return [SchemaFieldResponse.model_validate(f) for f in new_fields]


async def update_dataset_properties(
    session: AsyncSession, dataset_id: int, properties: list[dict],
) -> list[DatasetProperty]:
    """데이터셋의 properties 를 전체 교체.

    metadata-sync 가 datasource-specific 메타(예: Iceberg snapshot id) 를 매번
    덮어쓰기 위해 사용. ``properties`` 는 ``[{key, value}, ...]`` 형식.
    """
    from sqlalchemy import delete as sql_delete

    # 1) 기존 행을 먼저 ``DELETE`` 로 즉시 제거하고 flush. ``session.delete`` 만 큐잉하면
    #    SQLAlchemy 가 INSERT 를 먼저 보내 unique 제약(dataset_id, property_key) 을
    #    위반할 수 있다.
    await session.execute(
        sql_delete(DatasetProperty).where(DatasetProperty.dataset_id == dataset_id)
    )
    await session.flush()

    new_props: list[DatasetProperty] = []
    seen: set[str] = set()
    for p in properties:
        key = p.get("key") or p.get("property_key")
        value = p.get("value") if "value" in p else p.get("property_value")
        if not key or key in seen:
            continue
        seen.add(key)
        row = DatasetProperty(
            dataset_id=dataset_id,
            property_key=key,
            property_value="" if value is None else str(value),
        )
        session.add(row)
        new_props.append(row)

    await session.commit()
    for p in new_props:
        await session.refresh(p)
    return new_props


# ---------------------------------------------------------------------------
# 대시보드 / 통계
# ---------------------------------------------------------------------------

async def get_catalog_stats(session: AsyncSession) -> CatalogStats:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    active_filter = Dataset.status != "removed"

    total_datasets = (await session.execute(
        select(func.count()).select_from(Dataset).where(active_filter)
    )).scalar() or 0

    total_datasources = (await session.execute(
        select(func.count()).select_from(Datasource)
    )).scalar() or 0

    total_tags = (await session.execute(
        select(func.count()).select_from(Tag)
    )).scalar() or 0

    total_glossary_terms = (await session.execute(
        select(func.count()).select_from(GlossaryTerm)
    )).scalar() or 0

    total_owners = (await session.execute(
        select(func.count()).select_from(Owner)
    )).scalar() or 0

    synced_datasets = (await session.execute(
        select(func.count()).select_from(Dataset)
        .where(active_filter, Dataset.is_synced == "true")
    )).scalar() or 0

    # 데이터 소스 이름별 데이터셋 수 (막대 차트용)
    result = await session.execute(
        select(Datasource.name, func.count(Dataset.id))
        .join(Dataset, Dataset.datasource_id == Datasource.id)
        .where(active_filter)
        .group_by(Datasource.name)
        .order_by(func.count(Dataset.id).desc())
    )
    datasets_by_datasource = [
        {"datasource": name, "count": count} for name, count in result.all()
    ]

    # 데이터 소스 유형별 데이터셋 수 (도넛 차트용)
    result = await session.execute(
        select(Datasource.type, func.count(Dataset.id))
        .join(Dataset, Dataset.datasource_id == Datasource.id)
        .where(active_filter)
        .group_by(Datasource.type)
        .order_by(func.count(Dataset.id).desc())
    )
    datasets_by_datasource_type = [
        {"type": ptype, "count": count} for ptype, count in result.all()
    ]

    # origin 별 데이터셋 수 (도넛 차트용)
    result = await session.execute(
        select(Dataset.origin, func.count(Dataset.id))
        .where(active_filter)
        .group_by(Dataset.origin)
    )
    datasets_by_origin = [
        {"origin": origin, "count": count} for origin, count in result.all()
    ]

    # 데이터 소스별 스키마 필드 수 (막대 차트)
    result = await session.execute(
        select(Datasource.name, func.count(DatasetSchema.id))
        .join(Dataset, Dataset.datasource_id == Datasource.id)
        .join(DatasetSchema, DatasetSchema.dataset_id == Dataset.id)
        .where(active_filter)
        .group_by(Datasource.name)
        .order_by(func.count(DatasetSchema.id).desc())
        .limit(10)
    )
    schema_fields_by_datasource = [
        {"datasource": name, "count": count} for name, count in result.all()
    ]

    # 태그가 가장 많은 데이터셋 상위 목록
    result = await session.execute(
        select(Dataset.name, func.count(DatasetTag.id).label("tag_count"))
        .join(DatasetTag, DatasetTag.dataset_id == Dataset.id)
        .where(active_filter)
        .group_by(Dataset.name)
        .order_by(text("tag_count DESC"))
        .limit(10)
    )
    top_tagged_datasets = [
        {"name": name, "count": count} for name, count in result.all()
    ]

    # 일별 데이터셋 생성 추이
    now = datetime.now(timezone.utc)

    # 시간별 (24시간)
    since_1d = now - timedelta(hours=24)
    result = await session.execute(
        select(
            func.date_trunc("hour", Dataset.created_at).label("hour"),
            func.count().label("count"),
        )
        .where(active_filter, Dataset.created_at >= since_1d)
        .group_by(text("hour")).order_by(text("hour"))
    )
    daily_datasets_1d = [
        {"date": r.hour.strftime("%H:%M") if r.hour else "", "count": r.count}
        for r in result.all()
    ]

    # 일별 (7일)
    since_7d = now - timedelta(days=7)
    result = await session.execute(
        select(func.date(Dataset.created_at).label("day"), func.count().label("count"))
        .where(active_filter, Dataset.created_at >= since_7d)
        .group_by(func.date(Dataset.created_at)).order_by(text("day"))
    )
    daily_datasets_7d = [
        {"date": str(r.day), "count": r.count} for r in result.all()
    ]

    # 일별 (30일)
    since_30d = now - timedelta(days=30)
    result = await session.execute(
        select(func.date(Dataset.created_at).label("day"), func.count().label("count"))
        .where(active_filter, Dataset.created_at >= since_30d)
        .group_by(func.date(Dataset.created_at)).order_by(text("day"))
    )
    daily_datasets_30d = [
        {"date": str(r.day), "count": r.count} for r in result.all()
    ]

    # 최근 데이터셋
    recent = await list_datasets(session, page=1, page_size=10)

    return CatalogStats(
        total_datasets=total_datasets,
        total_datasources=total_datasources,
        total_tags=total_tags,
        total_glossary_terms=total_glossary_terms,
        total_owners=total_owners,
        synced_datasets=synced_datasets,
        datasets_by_datasource=datasets_by_datasource,
        datasets_by_origin=datasets_by_origin,
        datasets_by_datasource_type=datasets_by_datasource_type,
        schema_fields_by_datasource=schema_fields_by_datasource,
        top_tagged_datasets=top_tagged_datasets,
        daily_datasets_1d=daily_datasets_1d,
        daily_datasets_7d=daily_datasets_7d,
        daily_datasets_30d=daily_datasets_30d,
        recent_datasets=recent.items,
    )


# ---------------------------------------------------------------------------
# 스키마 이력
# ---------------------------------------------------------------------------

def _schema_field_to_dict(f) -> dict:
    """DatasetSchema ORM 객체를 비교 가능한 dict 로 변환한다."""
    return {
        "field_type": f.field_type,
        "native_type": f.native_type or "",
        "nullable": f.nullable or "true",
        "is_primary_key": f.is_primary_key or "false",
        "is_unique": f.is_unique or "false",
        "is_indexed": f.is_indexed or "false",
    }


def _schema_field_to_snapshot(f) -> dict:
    """DatasetSchema ORM 객체를 전체 스냅샷 항목으로 변환한다."""
    return {
        "field_path": f.field_path,
        "field_type": f.field_type,
        "native_type": f.native_type or "",
        "nullable": f.nullable or "true",
        "is_primary_key": f.is_primary_key or "false",
        "is_unique": f.is_unique or "false",
        "is_indexed": f.is_indexed or "false",
        "ordinal": f.ordinal,
    }


def _col_info_to_dict(col) -> dict:
    """동기화 ColumnInfo 를 비교 가능한 dict 로 변환한다."""
    return {
        "field_type": col.data_type,
        "native_type": col.native_type or "",
        "nullable": "true" if col.nullable else "false",
        "is_primary_key": "true" if col.is_primary_key else "false",
        "is_unique": "true" if col.is_unique else "false",
        "is_indexed": "true" if col.is_indexed else "false",
    }


def _col_info_to_snapshot(col) -> dict:
    """동기화 ColumnInfo 를 전체 스냅샷 항목으로 변환한다."""
    return {
        "field_path": col.name,
        "field_type": col.data_type,
        "native_type": col.native_type or "",
        "nullable": "true" if col.nullable else "false",
        "is_primary_key": "true" if col.is_primary_key else "false",
        "is_unique": "true" if col.is_unique else "false",
        "is_indexed": "true" if col.is_indexed else "false",
        "ordinal": col.ordinal,
    }


def detect_schema_changes(
    old_fields: list, new_columns: list, from_sync: bool = False,
) -> list[dict]:
    """기존 스키마 필드와 새 컬럼 간의 ADD/MODIFY/DROP 변경을 감지한다.

    Args:
        old_fields: DatasetSchema ORM 객체 목록(기존)
        new_columns: DatasetSchema ORM 객체 또는 동기화 ColumnInfo 객체 목록
        from_sync: True 면 new_columns 가 동기화에서 온 ColumnInfo 객체

    Returns:
        변경 dict 목록: [{type, field, before, after}, ...]
    """
    if from_sync:
        old_map = {f.field_path: _schema_field_to_dict(f) for f in old_fields}
        new_map = {c.name: _col_info_to_dict(c) for c in new_columns}
    else:
        old_map = {f.field_path: _schema_field_to_dict(f) for f in old_fields}
        new_map = {f.field_path: _schema_field_to_dict(f) for f in new_columns}

    changes = []
    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    # 추가됨
    for key in sorted(new_keys - old_keys):
        changes.append({"type": "ADD", "field": key, "before": None, "after": new_map[key]})

    # 삭제됨
    for key in sorted(old_keys - new_keys):
        changes.append({"type": "DROP", "field": key, "before": old_map[key], "after": None})

    # 변경됨
    for key in sorted(old_keys & new_keys):
        old_val = old_map[key]
        new_val = new_map[key]
        if old_val != new_val:
            # 변경된 속성만 기록
            before_diff = {k: v for k, v in old_val.items() if v != new_val.get(k)}
            after_diff = {k: v for k, v in new_val.items() if v != old_val.get(k)}
            changes.append({
                "type": "MODIFY", "field": key,
                "before": before_diff, "after": after_diff,
            })

    return changes


async def save_schema_snapshot(
    session: AsyncSession,
    dataset_id: int,
    old_fields: list,
    new_columns: list,
    from_sync: bool = False,
) -> SchemaSnapshot | None:
    """기존 스키마와 새 스키마를 비교해 변경이 있으면 스냅샷을 저장한다.

    저장된 스냅샷을 반환하며, 변경이 없으면 None.
    """
    import json as _json

    changes = detect_schema_changes(old_fields, new_columns, from_sync=from_sync)

    # 이 데이터셋의 첫 스냅샷인지 확인
    existing_snapshots = await session.execute(
        select(func.count()).where(SchemaSnapshot.dataset_id == dataset_id)
    )
    has_prior_snapshot = (existing_snapshots.scalar() or 0) > 0

    # 변경이 감지되었거나, 이전 스냅샷이 없는 경우(최초 기준점) 저장
    if not changes and has_prior_snapshot:
        return None

    # 새 컬럼에서 스냅샷 JSON 구성
    if from_sync:
        schema_entries = [_col_info_to_snapshot(c) for c in new_columns]
    else:
        schema_entries = [_schema_field_to_snapshot(f) for f in new_columns]

    # Build change summary (한글 — UI 의 변경 이력 tab 에 그대로 노출되는 값).
    added = sum(1 for c in changes if c["type"] == "ADD")
    modified = sum(1 for c in changes if c["type"] == "MODIFY")
    dropped = sum(1 for c in changes if c["type"] == "DROP")
    parts = []
    if added:
        parts.append(f"추가 {added}개")
    if modified:
        parts.append(f"변경 {modified}개")
    if dropped:
        parts.append(f"삭제 {dropped}개")
    summary = ", ".join(parts) if parts else ("최초 동기화" if not has_prior_snapshot else "변경 없음")

    snapshot = SchemaSnapshot(
        dataset_id=dataset_id,
        schema_json=_json.dumps(schema_entries, ensure_ascii=False),
        field_count=len(schema_entries),
        change_summary=summary,
        changes_json=_json.dumps(changes, ensure_ascii=False) if changes else "[]",
    )
    session.add(snapshot)
    await session.flush()

    # 스키마 변경을 분석해 리니지 알림 생성
    if changes:
        try:
            from app.alert.service import evaluate_rules_and_create_alerts
            alerts = await evaluate_rules_and_create_alerts(session, dataset_id, changes)
            if alerts:
                logger.info("dataset_id=%d 에 대해 리니지 알림 %d건 생성", dataset_id, len(alerts))
        except Exception as e:
            logger.warning("dataset_id=%d 의 리니지 알림 생성 실패: %s", dataset_id, e)

    # 데이터셋당 최신 20개 스냅샷만 유지
    max_snapshots = 20
    count_result = await session.execute(
        select(func.count()).where(SchemaSnapshot.dataset_id == dataset_id)
    )
    total = count_result.scalar() or 0
    if total > max_snapshots:
        old_snaps = await session.execute(
            select(SchemaSnapshot)
            .where(SchemaSnapshot.dataset_id == dataset_id)
            .order_by(SchemaSnapshot.synced_at.asc())
            .limit(total - max_snapshots)
        )
        for old in old_snaps.scalars().all():
            await session.delete(old)
        logger.info("dataset_id=%d 의 오래된 스냅샷 %d개 정리", dataset_id, total - max_snapshots)

    logger.info("스키마 스냅샷 저장: dataset_id=%d, %s, fields=%d",
                dataset_id, summary, len(schema_entries))
    return snapshot


async def get_schema_history(
    session: AsyncSession,
    dataset_id: int,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedSchemaSnapshots:
    """데이터셋의 스키마 변경 이력을 조회한다."""
    import json as _json

    base = select(SchemaSnapshot).where(SchemaSnapshot.dataset_id == dataset_id)

    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    offset = (page - 1) * page_size
    query = base.order_by(SchemaSnapshot.synced_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)

    items = []
    for snap in result.scalars().all():
        changes_raw = _json.loads(snap.changes_json) if snap.changes_json else []
        changes = [SchemaChangeEntry(**c) for c in changes_raw]
        items.append(SchemaSnapshotResponse(
            id=snap.id,
            dataset_id=snap.dataset_id,
            synced_at=snap.synced_at,
            field_count=snap.field_count,
            change_summary=snap.change_summary,
            changes=changes,
        ))

    return PaginatedSchemaSnapshots(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# 시드 데이터
# ---------------------------------------------------------------------------

async def seed_datasource_metadata(session: AsyncSession) -> None:
    """데이터 소스 메타데이터(데이터 타입, 테이블 타입, 저장 포맷, 기능)를 시드한다."""
    from app.catalog.datasource_metadata import DATASOURCE_METADATA

    for datasource_name, meta in DATASOURCE_METADATA.items():
        result = await session.execute(
            select(Datasource).where(Datasource.type == datasource_name)
        )
        datasource = result.scalars().first()
        if not datasource:
            continue

        # 이미 시드되었는지 확인(4개 테이블 중 하나라도)
        already_seeded = False
        for model_cls in (DatasourceDataType, DatasourceTableType, DatasourceStorageFormat, DatasourceFeature):
            cnt = (await session.execute(
                select(func.count()).where(model_cls.datasource_id == datasource.id)
            )).scalar() or 0
            if cnt > 0:
                already_seeded = True
                break
        if already_seeded:
            continue

        pid = datasource.id

        for idx, (type_name, cat, desc) in enumerate(meta.get("data_types", [])):
            session.add(DatasourceDataType(
                datasource_id=pid, type_name=type_name, type_category=cat,
                description=desc, ordinal=idx,
            ))

        for idx, (type_name, display, desc, is_def) in enumerate(meta.get("table_types", [])):
            session.add(DatasourceTableType(
                datasource_id=pid, type_name=type_name, display_name=display,
                description=desc, is_default=is_def, ordinal=idx,
            ))

        for idx, (fmt, display, desc, is_def) in enumerate(meta.get("storage_formats", [])):
            session.add(DatasourceStorageFormat(
                datasource_id=pid, format_name=fmt, display_name=display,
                description=desc, is_default=is_def, ordinal=idx,
            ))

        for idx, (key, display, desc, vtype, is_req) in enumerate(meta.get("features", [])):
            session.add(DatasourceFeature(
                datasource_id=pid, feature_key=key, display_name=display,
                description=desc, value_type=vtype, is_required=is_req, ordinal=idx,
            ))

        logger.info("데이터 소스 메타데이터 시드 완료: %s", datasource_name)

    await session.commit()


# ---------------------------------------------------------------------------
# 데이터 소스 메타데이터 조회
# ---------------------------------------------------------------------------

async def get_datasource_metadata(
    session: AsyncSession, datasource_id: int
) -> DatasourceMetadataResponse | None:
    result = await session.execute(select(Datasource).where(Datasource.id == datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        return None

    data_types = (await session.execute(
        select(DatasourceDataType)
        .where(DatasourceDataType.datasource_id == datasource_id)
        .order_by(DatasourceDataType.ordinal)
    )).scalars().all()

    table_types = (await session.execute(
        select(DatasourceTableType)
        .where(DatasourceTableType.datasource_id == datasource_id)
        .order_by(DatasourceTableType.ordinal)
    )).scalars().all()

    storage_formats = (await session.execute(
        select(DatasourceStorageFormat)
        .where(DatasourceStorageFormat.datasource_id == datasource_id)
        .order_by(DatasourceStorageFormat.ordinal)
    )).scalars().all()

    features = (await session.execute(
        select(DatasourceFeature)
        .where(DatasourceFeature.datasource_id == datasource_id)
        .order_by(DatasourceFeature.ordinal)
    )).scalars().all()

    return DatasourceMetadataResponse(
        datasource=DatasourceResponse.model_validate(datasource),
        data_types=[DatasourceDataTypeResponse.model_validate(t) for t in data_types],
        table_types=[DatasourceTableTypeResponse.model_validate(t) for t in table_types],
        storage_formats=[DatasourceStorageFormatResponse.model_validate(f) for f in storage_formats],
        features=[DatasourceFeatureResponse.model_validate(f) for f in features],
    )


# ---------------------------------------------------------------------------
# 데이터 파이프라인 CRUD
# ---------------------------------------------------------------------------

async def create_pipeline(session: AsyncSession, data: PipelineCreate) -> PipelineResponse:
    pipeline = DataPipeline(
        pipeline_name=data.pipeline_name,
        description=data.description,
        pipeline_type=data.pipeline_type.value,
        schedule=data.schedule,
        owner=data.owner,
    )
    session.add(pipeline)
    await session.flush()
    await session.refresh(pipeline)
    logger.info("파이프라인 생성: id=%d, name=%s, type=%s", pipeline.id, pipeline.pipeline_name, pipeline.pipeline_type)
    return PipelineResponse.model_validate(pipeline)


async def list_pipelines(session: AsyncSession) -> list[PipelineResponse]:
    result = await session.execute(
        select(DataPipeline).order_by(DataPipeline.pipeline_name)
    )
    return [PipelineResponse.model_validate(p) for p in result.scalars().all()]


async def get_pipeline(session: AsyncSession, pipeline_id: int) -> DataPipeline | None:
    result = await session.execute(
        select(DataPipeline).where(DataPipeline.id == pipeline_id)
    )
    return result.scalar_one_or_none()


async def update_pipeline(
    session: AsyncSession, pipeline_id: int, data: PipelineUpdate,
) -> PipelineResponse | None:
    pipeline = await get_pipeline(session, pipeline_id)
    if not pipeline:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "pipeline_type" and value is not None:
            value = value.value if hasattr(value, "value") else value
        if field == "status" and value is not None:
            value = value.value if hasattr(value, "value") else value
        setattr(pipeline, field, value)
    await session.flush()
    await session.refresh(pipeline)
    return PipelineResponse.model_validate(pipeline)


async def delete_pipeline(session: AsyncSession, pipeline_id: int) -> bool:
    pipeline = await get_pipeline(session, pipeline_id)
    if not pipeline:
        return False
    await session.delete(pipeline)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# 타 데이터 소스 간 데이터셋 리니지
# ---------------------------------------------------------------------------

async def create_dataset_lineage(
    session: AsyncSession, data: DatasetLineageCreate,
) -> DatasetLineageResponse:
    lineage = DatasetLineage(
        source_dataset_id=data.source_dataset_id,
        target_dataset_id=data.target_dataset_id,
        relation_type=data.relation_type.value,
        lineage_source=data.lineage_source.value,
        pipeline_id=data.pipeline_id,
        description=data.description,
        created_by=data.created_by,
    )
    session.add(lineage)
    await session.flush()
    await session.refresh(lineage)

    # 컬럼 매핑 생성
    for cm in data.column_mappings:
        mapping = DatasetColumnMapping(
            dataset_lineage_id=lineage.id,
            source_column=cm.source_column,
            target_column=cm.target_column,
            transform_type=cm.transform_type,
            transform_expr=cm.transform_expr,
        )
        session.add(mapping)
    await session.flush()

    logger.info("데이터셋 리니지 생성: id=%d, source=%d, target=%d, type=%s, mappings=%d",
                lineage.id, data.source_dataset_id, data.target_dataset_id,
                data.relation_type.value, len(data.column_mappings))
    return await _build_lineage_response(session, lineage.id)


async def list_dataset_lineages(
    session: AsyncSession,
    dataset_id: int | None = None,
    lineage_source: str | None = None,
) -> list[DatasetLineageResponse]:
    stmt = select(DatasetLineage)
    if dataset_id is not None:
        stmt = stmt.where(
            or_(
                DatasetLineage.source_dataset_id == dataset_id,
                DatasetLineage.target_dataset_id == dataset_id,
            )
        )
    if lineage_source is not None:
        stmt = stmt.where(DatasetLineage.lineage_source == lineage_source)
    stmt = stmt.order_by(DatasetLineage.created_at.desc())
    result = await session.execute(stmt)
    lineages = result.scalars().all()
    return [await _build_lineage_response(session, l.id) for l in lineages]


async def get_dataset_lineage(session: AsyncSession, lineage_id: int) -> DatasetLineage | None:
    result = await session.execute(
        select(DatasetLineage).where(DatasetLineage.id == lineage_id)
    )
    return result.scalar_one_or_none()


async def replace_fk_lineage_for_target(
    session: AsyncSession,
    target_dataset_id: int,
    entries: list,
) -> dict:
    """target dataset 의 ``lineage_source=FK`` lineage 를 통째 교체.

    metadata-sync 어댑터가 FK 제약을 발견할 때마다 호출. 다른 출처 (MANUAL /
    PIPELINE / QUERY_AGGREGATED) 의 lineage 는 건드리지 않는다.

    ``entries`` 의 각 항목:
      - source_urn: 참조 대상 dataset URN (부모)
      - columns: [{local, referenced}, ...]
      - description: optional

    Returns:
      {"created": N, "skipped_missing_source": M, "deleted": K}
    """
    from sqlalchemy import delete as sql_delete

    # 1) target 의 기존 FK lineage 제거 (column_mappings 는 FK CASCADE)
    del_result = await session.execute(
        sql_delete(DatasetLineage).where(
            DatasetLineage.target_dataset_id == target_dataset_id,
            DatasetLineage.lineage_source == "FK",
        )
    )
    deleted = del_result.rowcount or 0

    # target 의 schema (column → native_type) — 이 dataset 만 한 번 fetch.
    target_fields = await _column_native_type_map(session, target_dataset_id)

    # 2) 새 entries 로 재생성. source_urn → source_dataset_id lookup.
    created = 0
    skipped = 0
    for entry in entries:
        result = await session.execute(
            select(Dataset.id).where(Dataset.urn == entry.source_urn)
        )
        source_id = result.scalar_one_or_none()
        if source_id is None:
            # 부모 dataset 이 아직 sync 안 됨 — 다음 sync 때 다시 시도되도록 skip.
            skipped += 1
            continue
        if source_id == target_dataset_id:
            # 자기참조 FK 는 의미 약함 + 그래프 시각화 noise. skip.
            skipped += 1
            continue
        source_fields = await _column_native_type_map(session, source_id)

        lineage = DatasetLineage(
            source_dataset_id=source_id,
            target_dataset_id=target_dataset_id,
            relation_type="READ_WRITE",
            lineage_source="FK",
            description=entry.description,
        )
        session.add(lineage)
        await session.flush()
        for cp in entry.columns:
            src_type = source_fields.get(cp.referenced) or ""
            tgt_type = target_fields.get(cp.local) or ""
            # native_type 이 둘 다 알려져 있고 다르면 CAST, 그 외에는 DIRECT 로 분류.
            # 한 쪽이라도 모르는 상태에서 단순 문자열 비교만으로 CAST 로 단정짓는 건
            # noise 가 될 수 있어 DIRECT 로 유지.
            if src_type and tgt_type and src_type.strip().lower() != tgt_type.strip().lower():
                transform_type = "CAST"
                transform_expr = f"CAST({cp.local} AS {src_type})"
            else:
                transform_type = "DIRECT"
                transform_expr = None
            session.add(DatasetColumnMapping(
                dataset_lineage_id=lineage.id,
                source_column=cp.referenced,
                target_column=cp.local,
                transform_type=transform_type,
                transform_expr=transform_expr,
            ))
        created += 1

    await session.flush()
    return {"created": created, "skipped_missing_source": skipped, "deleted": deleted}


async def _column_native_type_map(
    session: AsyncSession, dataset_id: int,
) -> dict[str, str]:
    """dataset 의 ``field_path → native_type`` 매핑. transform_type 추론에 사용."""
    result = await session.execute(
        select(DatasetSchema.field_path, DatasetSchema.native_type)
        .where(DatasetSchema.dataset_id == dataset_id)
    )
    return {row[0]: (row[1] or "") for row in result.all()}


async def delete_dataset_lineage(session: AsyncSession, lineage_id: int) -> bool:
    lineage = await get_dataset_lineage(session, lineage_id)
    if not lineage:
        return False
    await session.delete(lineage)
    await session.flush()
    return True


async def update_dataset_lineage_column_mappings(
    session: AsyncSession,
    lineage_id: int,
    column_mappings: list[ColumnMappingCreate],
) -> DatasetLineageResponse | None:
    lineage = await get_dataset_lineage(session, lineage_id)
    if not lineage:
        return None

    # 기존 매핑 삭제
    existing = await session.execute(
        select(DatasetColumnMapping).where(
            DatasetColumnMapping.dataset_lineage_id == lineage_id
        )
    )
    for m in existing.scalars().all():
        await session.delete(m)

    # 새 매핑 생성
    for cm in column_mappings:
        mapping = DatasetColumnMapping(
            dataset_lineage_id=lineage_id,
            source_column=cm.source_column,
            target_column=cm.target_column,
            transform_type=cm.transform_type,
            transform_expr=cm.transform_expr,
        )
        session.add(mapping)
    await session.flush()

    return await _build_lineage_response(session, lineage_id)


async def _build_lineage_response(
    session: AsyncSession, lineage_id: int,
) -> DatasetLineageResponse:
    lineage = await get_dataset_lineage(session, lineage_id)

    # 소스 데이터셋 정보 조회 (중첩 데이터 소스를 포함한 DatasetResponse 반환)
    src = await get_dataset(session, lineage.source_dataset_id)
    tgt = await get_dataset(session, lineage.target_dataset_id)

    # 파이프라인 이름 조회
    pipeline_name = None
    if lineage.pipeline_id:
        pipeline = await get_pipeline(session, lineage.pipeline_id)
        if pipeline:
            pipeline_name = pipeline.pipeline_name

    # 컬럼 매핑 조회
    mappings_result = await session.execute(
        select(DatasetColumnMapping).where(
            DatasetColumnMapping.dataset_lineage_id == lineage_id
        )
    )
    column_mappings = [
        ColumnMappingResponse.model_validate(m) for m in mappings_result.scalars().all()
    ]

    return DatasetLineageResponse(
        id=lineage.id,
        source_dataset_id=lineage.source_dataset_id,
        target_dataset_id=lineage.target_dataset_id,
        source_dataset_name=src.name if src else None,
        target_dataset_name=tgt.name if tgt else None,
        source_datasource_type=src.datasource.type if src else None,
        target_datasource_type=tgt.datasource.type if tgt else None,
        source_datasource_name=src.datasource.name if src else None,
        target_datasource_name=tgt.datasource.name if tgt else None,
        relation_type=lineage.relation_type,
        lineage_source=lineage.lineage_source,
        pipeline_id=lineage.pipeline_id,
        pipeline_name=pipeline_name,
        description=lineage.description,
        created_by=lineage.created_by,
        query_count=lineage.query_count,
        last_seen_at=lineage.last_seen_at,
        created_at=lineage.created_at,
        column_mappings=column_mappings,
    )
