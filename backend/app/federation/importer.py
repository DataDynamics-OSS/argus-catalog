# SPDX-License-Identifier: Apache-2.0
"""로컬 승격(import) — 페더레이션 데이터셋을 로컬 카탈로그의 1급 데이터셋으로 복사."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.federation.drilldown import federated_dataset_detail, federated_dataset_sample

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 로컬 승격(import) — 페더레이션(미러) 데이터셋을 로컬 카탈로그의 1급 데이터셋으로 복사
#   전용 "Federation Imports" 데이터소스에 담고, peer 드릴다운으로 전체 메타(스키마·태그·
#   소유자·용어집·DDL·확장)를 가져와 materialize 한다. 1회 스냅샷(이후 peer 와 분리).
# ---------------------------------------------------------------------------

_IMPORT_DS_NAME = "Federation Imports"  # visibility.IMPORT_DATASOURCE_NAME 과 동일(export 제외 기준)
_IMPORT_DS_TYPE = "federation"

# 확장 메타데이터를 로컬 Dataset 컬럼에 옮길 때의 타입별 키 목록.
_EXT_INT_FIELDS = (
    "retention_days", "purge_days", "row_count", "byte_size", "file_count",
    "view_count", "query_count", "quality_score",
)
_EXT_DT_FIELDS = ("last_ingested_at", "last_accessed_at")
_EXT_STR_FIELDS = (
    "ingestion_frequency", "ingestion_time", "ingestion_day", "ingestion_timezone",
    "ingestion_cron", "ingestion_mode", "update_type", "freshness_sla",
    "data_category", "data_format", "compression", "encoding",
    "sensitivity", "contains_pii", "pii_fields", "compliance_tags",
    "tier", "certification", "steward", "quality_status", "note",
)


async def _get_or_create_import_datasource(session: AsyncSession):
    """import 전용 로컬 데이터소스를 찾거나 생성한다(이름 기준 단일)."""
    from app.catalog.models import Datasource
    from app.catalog.schemas import DatasourceCreate
    from app.catalog.service import create_datasource

    row = (await session.execute(
        select(Datasource).where(Datasource.name == _IMPORT_DS_NAME)
    )).scalars().first()
    if row is not None:
        return row
    logger.info("import 전용 데이터소스 생성: name=%s", _IMPORT_DS_NAME)
    resp = await create_datasource(
        session, DatasourceCreate(name=_IMPORT_DS_NAME, type=_IMPORT_DS_TYPE)
    )
    return (await session.execute(
        select(Datasource).where(Datasource.id == resp.id)
    )).scalars().first()


# import 데이터셋에 항상 붙이는 마커 태그 — 로컬에서 페더레이션 출처를 구분/필터.
_IMPORT_MARKER_TAG = "페더레이션"
_IMPORT_MARKER_TAG_COLOR = "#6366f1"


async def _match_tag_id(session: AsyncSession, name: str) -> int | None:
    """같은 이름의 로컬 태그가 있으면 id, 없으면 None(새로 만들지 않음).

    import 시 peer 태그로 로컬 태그 목록을 오염시키지 않기 위해 '매칭만' 한다.
    """
    from app.catalog.models import Tag

    row = (await session.execute(
        select(Tag).where(Tag.name == name)
    )).scalars().first()
    return row.id if row is not None else None


async def _match_glossary_id(session: AsyncSession, name: str) -> int | None:
    """같은 이름의 로컬 용어(TERM)가 있으면 id, 없으면 None(새로 만들지 않음).

    이름은 유형별로만 유일하므로(분류와 용어가 같은 이름일 수 있음) 데이터셋에 연결 가능한 TERM 만 찾는다.
    """
    from app.catalog.models import GlossaryTerm

    row = (await session.execute(
        select(GlossaryTerm).where(GlossaryTerm.name == name, GlossaryTerm.term_type == "TERM")
    )).scalars().first()
    return row.id if row is not None else None


async def _get_or_create_marker_tag_id(session: AsyncSession) -> int:
    """'페더레이션' 마커 태그를 찾거나(없으면) 생성해 id 를 반환한다.

    peer 태그와 달리 이 태그는 의도적으로 만든다 — import 데이터셋을 한눈에 구분/필터하기 위함.
    """
    from app.catalog.models import Tag
    from app.catalog.schemas import TagCreate
    from app.catalog.service import create_tag

    row = (await session.execute(
        select(Tag).where(Tag.name == _IMPORT_MARKER_TAG)
    )).scalars().first()
    if row is not None:
        return row.id
    logger.info("import 마커 태그 생성: name=%s", _IMPORT_MARKER_TAG)
    resp = await create_tag(
        session, TagCreate(name=_IMPORT_MARKER_TAG, color=_IMPORT_MARKER_TAG_COLOR)
    )
    return resp.id


def _apply_extended(dataset, ext: dict) -> None:
    """확장 메타데이터 dict 를 로컬 Dataset 행에 타입 맞춰 반영한다."""
    from datetime import datetime

    for k in _EXT_STR_FIELDS:
        v = ext.get(k)
        if v is not None and v != "":
            setattr(dataset, k, str(v))
    for k in _EXT_INT_FIELDS:
        v = ext.get(k)
        if v is not None and v != "":
            try:
                setattr(dataset, k, int(v))
            except (TypeError, ValueError):
                logger.debug("확장 메타 정수 변환 실패 — 무시: key=%s", k)
    for k in _EXT_DT_FIELDS:
        v = ext.get(k)
        if isinstance(v, str) and v:
            try:
                setattr(dataset, k, datetime.fromisoformat(v))
            except ValueError:
                logger.debug("확장 메타 시각 변환 실패 — 무시: key=%s", k)


async def _copy_sample_to_local(
    session: AsyncSession, federated_urn: str, datasource, dataset
) -> bool:
    """페더레이션 샘플(미러 JSON 또는 LIVE)을 로컬 parquet 으로 변환 저장한다."""
    try:
        sample = await federated_dataset_sample(session, federated_urn, limit=1000)
    except Exception as e:  # noqa: BLE001 — 샘플은 best-effort
        logger.warning("import 샘플 조회 실패 — 샘플 없이 진행: urn=%s error=%s", federated_urn, e)
        return False
    if not sample:
        logger.debug("import 샘플 없음: urn=%s", federated_urn)
        return False
    columns = sample.get("columns") or []
    rows = sample.get("rows") or []
    if not columns:
        return False
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        from app.catalog.router import _sample_dir_by_datasource

        cols = {
            c: [r[i] if i < len(r) else None for r in rows]
            for i, c in enumerate(columns)
        }
        table = pa.table(cols)
        dest_dir = _sample_dir_by_datasource(datasource.datasource_id, dataset.name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, dest_dir / "sample.parquet")
        logger.debug("import 샘플 저장: urn=%s rows=%d", dataset.urn, len(rows))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("import 샘플 저장 실패 (%s): %s", dataset.urn, e)
        return False


class AlreadyImportedError(Exception):
    """같은 federated URN 을 이미 로컬로 가져옴(409)."""


def _clip(v, n: int):
    """문자열을 최대 n 자로 자른다(None·비문자열은 그대로)."""
    return v[:n] if isinstance(v, str) else v


def _safe_dataset_name(raw: str) -> str:
    """peer 데이터셋 이름을 로컬 이름으로 정규화 — 경로 구분자/상위 참조 제거, 255자 제한."""
    name = str(raw).replace("\\", "_").replace("/", "_").strip()
    while ".." in name:
        name = name.replace("..", ".")
    name = name.strip(". ") or "imported"
    return name[:255]


async def import_federated_dataset(
    session: AsyncSession, federated_urn: str, created_by: str | None = None,
) -> dict | None:
    """페더레이션(미러) 데이터셋을 로컬 카탈로그로 승격(import)한다.

    peer 드릴다운으로 전체 메타데이터를 받아 전용 데이터소스에 1급 데이터셋으로 생성하고,
    스키마·태그·소유자·용어집·DDL·확장 메타·샘플을 복사한 뒤 로컬 임베딩을 생성한다.
    이미 가져온 데이터셋이면 AlreadyImportedError(라우터가 409 로 변환), 해석 불가면 None.
    """
    from app.catalog import service as catalog_service
    from app.catalog.models import Dataset, DatasetProperty
    from app.catalog.schemas import (
        DatasetCreate,
        DatasetOrigin,
        DatasetPropertyCreate,
        OwnerCreate,
        OwnerType,
        SchemaFieldCreate,
    )

    logger.debug("페더레이션 데이터셋 import 요청: urn=%s", federated_urn)
    detail = await federated_dataset_detail(session, federated_urn, include_peer_exposed=False)
    if detail is None:
        logger.debug("import 대상 URN 미해석: urn=%s", federated_urn)
        return None
    meta = detail.get("metadata") or {}
    remote_urn = detail["remote_urn"]
    instance_key = detail.get("source_instance_key") or "peer"

    # 중복 import 차단 — imported_from 속성으로 추적
    dup = (await session.execute(
        select(DatasetProperty).where(
            DatasetProperty.property_key == "imported_from",
            DatasetProperty.property_value == federated_urn,
        )
    )).scalars().first()
    if dup is not None:
        logger.warning("중복 import 거부(409): urn=%s", federated_urn)
        raise AlreadyImportedError(f"이미 로컬로 가져온 데이터셋입니다: {federated_urn}")

    ext: dict = meta.get("extended") or {}
    datasource = await _get_or_create_import_datasource(session)

    # peer 가 준 값은 신뢰하지 않는다 — 로컬 스키마 길이 제한에 맞춰 자르고, 이름은 샘플 저장
    # 경로(data_dir/samples/{ds}/{name}) 구성요소로도 쓰이므로 경로 구분자·상위 참조를 제거한다.
    name = _safe_dataset_name(meta.get("name") or remote_urn)
    # URN/qualified_name 유일성 — peer 식별 키를 경로 앞에 둔다.
    base_path = meta.get("qualified_name") or name
    qualified_for_urn = f"{instance_key}.{base_path}"

    def _origin() -> DatasetOrigin:
        try:
            return DatasetOrigin(meta.get("origin") or "PROD")
        except ValueError:
            return DatasetOrigin.PROD

    def _owner_type(t: str | None) -> OwnerType:
        try:
            return OwnerType(t)
        except ValueError:
            return OwnerType.TECHNICAL_OWNER

    schema_fields = [
        SchemaFieldCreate(
            field_path=s.get("field_path", ""),
            display_name=s.get("display_name"),
            field_type=s.get("field_type") or "",
            native_type=s.get("native_type"),
            description=s.get("description"),
            nullable=s.get("nullable", "true"),
            is_primary_key=s.get("is_primary_key", "false"),
            is_unique=s.get("is_unique", "false"),
            is_indexed=s.get("is_indexed", "false"),
            is_partition_key=s.get("is_partition_key", "false"),
            is_distribution_key=s.get("is_distribution_key", "false"),
            ordinal=s.get("ordinal", idx),
        )
        for idx, s in enumerate(meta.get("schema") or [])
        if s.get("field_path")
    ]
    # 스키마가 비면 peer 가 schema 를 노출하지 않았거나 빈 데이터셋 — 부분 import 로 진행.
    if not schema_fields:
        logger.warning(
            "import 스키마 없음 — 스키마 컬럼 없이 가져옵니다: %s", federated_urn
        )

    # 태그: peer 태그는 '매칭만'(이미 있는 로컬 태그에만 연결, 새로 만들지 않음) →
    # 로컬 태그 목록 오염 방지. 매칭 안 된 것 포함 원본 전체는 imported_tags 속성으로 보존.
    peer_tags = [t for t in (meta.get("tags") or []) if t]
    tag_ids: list[int] = []
    for t in peer_tags:
        tid = await _match_tag_id(session, t)
        if tid is not None:
            tag_ids.append(tid)
    # import 데이터셋임을 표시하는 마커 태그(없으면 생성)를 항상 부착.
    marker_id = await _get_or_create_marker_tag_id(session)
    if marker_id not in tag_ids:
        tag_ids.append(marker_id)

    properties = [
        DatasetPropertyCreate(key="imported_from", value=federated_urn),
        DatasetPropertyCreate(key="imported_from_instance", value=instance_key),
    ]
    if peer_tags:  # 원본 태그 전체 보존(provenance) — 매칭 여부와 무관
        properties.append(
            DatasetPropertyCreate(key="imported_tags", value=", ".join(peer_tags))
        )
    peer_glossary = [g for g in (meta.get("glossary") or []) if g]
    if peer_glossary:  # 원본 용어 전체 보존(provenance)
        properties.append(
            DatasetPropertyCreate(key="imported_glossary", value=", ".join(peer_glossary))
        )

    req = DatasetCreate(
        name=name,
        display_name=_clip(ext.get("display_name") or meta.get("display_name"), 255),
        datasource_id=datasource.id,
        summary=_clip(ext.get("summary") or meta.get("summary"), 200),
        description=meta.get("description"),
        origin=_origin(),
        qualified_name=qualified_for_urn,
        table_type=meta.get("table_type"),
        storage_format=meta.get("storage_format"),
        ddl=meta.get("ddl"),
        schema_fields=schema_fields,
        tags=tag_ids,
        owners=[
            OwnerCreate(owner_name=o.get("name", ""), owner_type=_owner_type(o.get("type")))
            for o in (meta.get("owners") or []) if o.get("name")
        ],
        properties=properties,
    )
    resp = await catalog_service.create_dataset(session, req, created_by=created_by)

    # create_dataset 가 다루지 않는 확장 메타데이터를 행에 직접 반영.
    dataset = (await session.execute(
        select(Dataset).where(Dataset.id == resp.id)
    )).scalars().first()
    if ext:
        _apply_extended(dataset, ext)
        await session.commit()
    logger.debug(
        "import 메타 구성: urn=%s fields=%d tags=%d/%d glossary=%d",
        federated_urn, len(schema_fields), len(tag_ids), len(peer_tags), len(peer_glossary),
    )

    # 용어집: '매칭만'(이미 있는 로컬 용어에만 연결, 새로 만들지 않음). 원본은 위 imported_glossary 속성에 보존.
    for gname in peer_glossary:
        try:
            term_id = await _match_glossary_id(session, gname)
            if term_id is not None:
                await catalog_service.add_dataset_glossary_term(session, dataset.id, term_id)
        except Exception as e:  # noqa: BLE001 — 용어집은 best-effort
            logger.warning("import 용어집 부착 실패 (%s): %s", gname, e)

    # 샘플 복사(미러 JSON/LIVE → 로컬 parquet)
    await _copy_sample_to_local(session, federated_urn, datasource, dataset)

    # 로컬 임베딩 생성(검색 편입) — 설정과 무관하게 명시적으로.
    try:
        from app.embedding.service import embed_dataset
        await embed_dataset(session, dataset.id)
    except Exception as e:  # noqa: BLE001
        logger.warning("import 임베딩 실패 (%s): %s", dataset.urn, e)

    logger.info(
        "페더레이션 데이터셋 로컬 승격: %s → id=%d urn=%s", federated_urn, dataset.id, dataset.urn
    )
    return {"id": dataset.id, "urn": dataset.urn, "name": dataset.name}


