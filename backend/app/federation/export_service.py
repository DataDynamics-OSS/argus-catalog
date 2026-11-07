# SPDX-License-Identifier: Apache-2.0
"""노출자(export) 측 — 노출 항목 정책, 상세 보강·필터, HARVEST 목록·리니지 빌더."""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.catalog.models import (
    Dataset,
    DatasetGlossaryTerm,
    DatasetSchema,
    Datasource,
    GlossaryTerm,
)
from app.federation import capabilities
from app.federation.schemas import (
    FederatedExportDataset,
    FederatedExportDatasetsResponse,
    FederatedExportField,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 노출자(provider) 정책 — 이 인스턴스가 외부에 줄 정보 항목(capability) 설정
#   catalog_configuration 에 CSV 로 보관. 미설정이면 visibility(PII) 기반 기본값.
# ---------------------------------------------------------------------------

_EXPOSED_CATEGORY = "federation"
_EXPOSED_KEY = "federation.exposed_fields"


async def get_effective_exposed_fields(session: AsyncSession) -> list[str]:
    """export API 에 실제로 적용할 노출 항목.

    export 토큰이 설정되지 않은 상태(인증 비강제)에서는 실데이터인 ``sample`` 을 기본적으로
    노출하지 않는다(``federation.export_allow_anonymous_sample=true`` 로만 허용). 관리 화면의
    정책 값(get_exposed_fields)은 그대로 두고 export 시점에만 적용한다.
    """
    from app.core.config import settings

    exposed = await get_exposed_fields(session)
    anonymous = not settings.federation_export_token
    if anonymous and not settings.federation_export_allow_anonymous_sample:
        # 인증 없는 export 에서는 실데이터(sample)를 숨긴다.
        logger.debug("export 토큰 미설정 — 익명 export 에서 sample 제외")
        exposed = [k for k in exposed if k != "sample"]
    return exposed


async def get_exposed_fields(session: AsyncSession) -> list[str]:
    """이 인스턴스가 외부에 노출하는 capability 키 목록(레지스트리 순서로 정규화)."""
    from app.core.config import settings
    from app.settings.service import get_config_by_category

    conf = await get_config_by_category(session, _EXPOSED_CATEGORY)
    raw = conf.get(_EXPOSED_KEY)
    if raw is None:
        logger.debug(
            "노출 항목 미설정 — 기본값 사용: exclude_pii=%s",
            settings.federation_export_exclude_pii,
        )
        return capabilities.default_exposed(settings.federation_export_exclude_pii)
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    return capabilities.validate(keys)


async def set_exposed_fields(session: AsyncSession, keys: list[str]) -> list[str]:
    """노출 capability 키를 검증해 저장하고 정규화된 결과를 반환한다(admin)."""
    from app.settings.service import update_config

    valid = capabilities.validate(keys)
    if len(valid) != len(set(keys)):
        # 레지스트리에 없는 키는 조용히 버려지므로 운영자가 알 수 있게 남긴다.
        logger.warning(
            "알 수 없는 노출 항목 키 무시: ignored=%s",
            sorted(set(keys) - set(valid)),
        )
    await update_config(session, _EXPOSED_CATEGORY, {_EXPOSED_KEY: ",".join(valid)})
    logger.info("페더레이션 노출 항목 갱신: %d개", len(valid))
    return valid


async def _fetch_ddl(session: AsyncSession, dataset_id: int) -> str | None:
    """데이터셋의 DDL 문자열(없으면 None)."""
    row = (
        await session.execute(select(Dataset.ddl).where(Dataset.id == dataset_id))
    ).first()
    return row[0] if row else None


async def _fetch_glossary(session: AsyncSession, dataset_id: int) -> list[str]:
    """데이터셋에 연결된 용어집 용어 이름 목록."""
    rows = (
        await session.execute(
            select(GlossaryTerm.name)
            .join(DatasetGlossaryTerm, DatasetGlossaryTerm.term_id == GlossaryTerm.id)
            .where(DatasetGlossaryTerm.dataset_id == dataset_id)
        )
    ).all()
    return [r[0] for r in rows]


# 확장 메타데이터로 노출하는 Dataset 컬럼(로컬 상세가 보여주는 전체 항목).
_EXTENDED_FIELDS = (
    "summary", "display_name", "note",
    # 생명주기·운영
    "ingestion_frequency", "ingestion_time", "ingestion_day", "ingestion_timezone",
    "ingestion_cron", "ingestion_mode", "update_type", "freshness_sla",
    "last_ingested_at", "retention_days", "purge_days",
    # 물리·형식
    "data_category", "data_format", "compression", "encoding",
    "row_count", "byte_size", "file_count",
    # 거버넌스·보안
    "sensitivity", "contains_pii", "pii_fields", "compliance_tags",
    # 비즈니스
    "tier", "certification", "steward",
    # 사용·인기
    "view_count", "query_count", "last_accessed_at",
    # 품질
    "quality_score", "quality_status",
)


async def _fetch_extended(session: AsyncSession, dataset_id: int) -> dict:
    """Dataset 의 확장 메타데이터 전체를 dict 로 반환한다(값이 있는 항목만)."""
    row = (
        await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    ).scalars().first()
    if row is None:
        return {}
    out: dict = {}
    for f in _EXTENDED_FIELDS:
        v = getattr(row, f, None)
        if v is None or v == "":
            continue
        if hasattr(v, "isoformat"):                # datetime → ISO 문자열
            v = v.isoformat()
        out[f] = v
    return out


async def _inject_schema_display_names(
    session: AsyncSession, dataset_id: int, metadata: dict,
) -> None:
    """스키마 컬럼에 논리명(display_name)을 채운다(로컬 상세와 동일 표시)."""
    schema = metadata.get("schema")
    if not schema:
        return
    rows = (
        await session.execute(
            select(DatasetSchema.field_path, DatasetSchema.display_name)
            .where(DatasetSchema.dataset_id == dataset_id)
        )
    ).all()
    names = {fp: dn for fp, dn in rows if dn}
    for field in schema:
        dn = names.get(field.get("field_path"))
        if dn:
            field["display_name"] = dn


async def augment_and_filter_detail(
    session: AsyncSession, metadata: dict, exposed: list[str],
) -> dict:
    """export 상세 메타데이터를 확장 항목으로 보강하고 노출 키로 필터링한다.

    ddl/glossary/extended/스키마 논리명은 기본 메타데이터에 없으므로, 노출 대상일 때만
    DB 에서 조회해 덧붙인 뒤 필터링한다. 이로써 페더레이션 상세가 로컬 상세와
    동일한 수준의 메타데이터를 노출한다.
    """
    allow = set(exposed)
    ds_id = metadata.get("dataset_id")
    if ds_id is not None:
        if "ddl" in allow:
            metadata["ddl"] = await _fetch_ddl(session, ds_id)
        if "glossary" in allow:
            metadata["glossary"] = await _fetch_glossary(session, ds_id)
        if "extended" in allow:
            metadata["extended"] = await _fetch_extended(session, ds_id)
        # 스키마 논리명은 schema 가 노출될 때만 의미 있음(식별 컬럼이라 필터에서 유지됨)
        if "schema" in allow:
            await _inject_schema_display_names(session, ds_id, metadata)
    return capabilities.filter_metadata(metadata, exposed)


# ---------------------------------------------------------------------------
# Export — 이 인스턴스를 peer 로서 노출 (HARVEST 대상 데이터셋 목록)
# ---------------------------------------------------------------------------

async def build_export_datasets(
    session: AsyncSession, limit: int, offset: int, updated_after=None,
) -> FederatedExportDatasetsResponse:
    """허브가 가져갈 수 있도록 이 인스턴스의 데이터셋 목록을 페이지로 반환한다.

    각 항목에 스키마 컬럼을 포함해, 허브가 자신의 임베딩 모델로 동일 품질의
    텍스트를 재구성(재임베딩)할 수 있게 한다. ``federation_export_*`` visibility 정책
    (PII/민감도/데이터소스 allow-list)을 적용한다.

    ``updated_after`` 가 주어지면 그 시각 이후 변경된 데이터셋만 반환한다(증분 동기화).
    """
    from app.federation.visibility import dataset_visibility_conditions

    conds = [Dataset.status != "removed", *dataset_visibility_conditions()]
    if updated_after is not None:
        conds.append(Dataset.updated_at > updated_after)

    total = (await session.execute(
        select(func.count())
        .select_from(Dataset)
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(*conds)
    )).scalar() or 0

    rows = (await session.execute(
        select(
            Dataset.id, Dataset.urn, Dataset.name, Dataset.display_name,
            Dataset.summary, Dataset.description,
            Dataset.qualified_name, Dataset.origin, Dataset.created_at, Dataset.updated_at,
            Datasource.name.label("datasource_name"),
            Datasource.type.label("datasource_type"),
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(*conds)
        .order_by(Dataset.id)
        .limit(limit)
        .offset(offset)
    )).all()

    # 상세와 동일한 노출 항목(exposed_fields) 정책을 목록 export 에도 적용한다.
    exposed = await get_effective_exposed_fields(session)

    # 페이지의 스키마 컬럼을 한 번에 조회(데이터셋마다 조회하던 N+1 제거)
    fields_by_ds: dict[int, list] = {r.id: [] for r in rows}
    if rows:
        for f in (await session.execute(
            select(
                DatasetSchema.dataset_id, DatasetSchema.field_path,
                DatasetSchema.field_type, DatasetSchema.description,
            )
            .where(DatasetSchema.dataset_id.in_(list(fields_by_ds)))
            .order_by(DatasetSchema.dataset_id, DatasetSchema.ordinal)
        )).all():
            fields_by_ds[f.dataset_id].append(f)

    items: list[FederatedExportDataset] = []
    for r in rows:
        fields_rows = fields_by_ds[r.id]
        item = FederatedExportDataset(
            urn=r.urn, name=r.name, display_name=r.display_name,
            datasource_name=r.datasource_name, datasource_type=r.datasource_type,
            summary=r.summary, description=r.description,
            qualified_name=r.qualified_name, origin=r.origin,
            created_at=r.created_at, updated_at=r.updated_at,
            fields=[
                FederatedExportField(field_path=f.field_path, field_type=f.field_type,
                                     description=f.description)
                for f in fields_rows
            ],
        )
        items.append(FederatedExportDataset.model_validate(
            capabilities.filter_export_item(item.model_dump(), exposed)
        ))

    logger.debug(
        "export 데이터셋 페이지 구성: offset=%d limit=%d items=%d total=%d incremental=%s",
        offset, limit, len(items), total, updated_after is not None,
    )
    return FederatedExportDatasetsResponse(
        items=items, total=total, limit=limit, offset=offset,
    )


async def build_export_lineage(
    session: AsyncSession, limit: int = 10_000,
) -> "FederatedExportLineageResponse":
    """이 인스턴스의 리니지 엣지(URN→URN)를 반환한다(cross-instance stitching 용).

    visibility 정책을 적용해, 양 끝 데이터셋이 모두 노출 가능한 엣지만 포함한다.
    """
    from app.catalog.models import DatasetLineage
    from app.federation.schemas import (
        FederatedExportLineageEdge,
        FederatedExportLineageResponse,
    )
    from app.federation.visibility import exportable_dataset_ids

    src = aliased(Dataset)
    tgt = aliased(Dataset)
    rows = (await session.execute(
        select(
            DatasetLineage.source_dataset_id, DatasetLineage.target_dataset_id,
            src.urn.label("source_urn"), tgt.urn.label("target_urn"),
            DatasetLineage.relation_type, DatasetLineage.lineage_source,
            DatasetLineage.description,
        )
        .join(src, src.id == DatasetLineage.source_dataset_id)
        .join(tgt, tgt.id == DatasetLineage.target_dataset_id)
        .order_by(DatasetLineage.id)
        .limit(limit)
    )).all()

    # visibility: 양 끝이 모두 노출 가능한 엣지만(import 사본은 정책과 무관하게 항상 제외)
    ids = {r.source_dataset_id for r in rows} | {r.target_dataset_id for r in rows}
    allowed = await exportable_dataset_ids(session, list(ids))

    edges = [
        FederatedExportLineageEdge(
            source_urn=r.source_urn, target_urn=r.target_urn,
            relation_type=r.relation_type, lineage_source=r.lineage_source,
            description=r.description,
        )
        for r in rows
        if r.source_dataset_id in allowed and r.target_dataset_id in allowed
    ]
    logger.debug(
        "export 리니지 구성: fetched=%d exported=%d limit=%d", len(rows), len(edges), limit
    )
    return FederatedExportLineageResponse(edges=edges, total=len(edges))


