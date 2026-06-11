"""외부 API 용 dataset 메타데이터/Avro 스키마 빌더 + 캐시 관리 서비스.

외부 컨슈머가 dataset 정보를 빠르게 얻을 수 있도록 메타데이터/Avro JSON 을 조립하고
TTL 캐시에 적재한다. 캐시 키는 URN(canonical identifier) 기준이고, ``dataset_id`` 입력은
내부적으로 URN 으로 매핑된다(``urn_map:{id}`` 매핑 자체도 캐시되어 반복 조회 비용을 줄인다).
"""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import (
    Dataset,
    DatasetSchema,
    DatasetTag,
    Datasource,
    Owner,
    Tag,
)
from app.external.cache import get_cache

logger = logging.getLogger(__name__)

# 카탈로그 일반 타입 → Avro 타입 매핑
AVRO_TYPE_MAP = {
    "STRING": "string",
    "NUMBER": "long",
    "DATE": {"type": "long", "logicalType": "timestamp-millis"},
    "BOOLEAN": "boolean",
    "BYTES": "bytes",
    "MAP": {"type": "map", "values": "string"},
    "ARRAY": {"type": "array", "items": "string"},
    "ENUM": "string",
}

# Avro 용 네이티브 타입 세부 보정
NATIVE_AVRO_OVERRIDES = {
    "int": "int",
    "integer": "int",
    "smallint": "int",
    "tinyint": "int",
    "mediumint": "int",
    "bigint": "long",
    "float": "float",
    "real": "float",
    "double": "double",
    "double precision": "double",
    "decimal": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 38,
        "scale": 10,
    },
    "numeric": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 38,
        "scale": 10,
    },
    "date": {"type": "int", "logicalType": "date"},
    "time": {"type": "long", "logicalType": "time-millis"},
    "timestamp": {"type": "long", "logicalType": "timestamp-millis"},
    "boolean": "boolean",
    "bool": "boolean",
    "bytea": "bytes",
    "blob": "bytes",
    "binary": "bytes",
    "varbinary": "bytes",
    "uuid": {"type": "string", "logicalType": "uuid"},
}


# ---------------------------------------------------------------------------
# URN 해석 — 인덱스 기반 빠른 조회 + 캐싱
# ---------------------------------------------------------------------------


async def _resolve_dataset_id(
    session: AsyncSession,
    identifier: str | int,
) -> tuple[int, str] | None:
    """식별자를 (dataset_id, urn) 으로 해석한다.

    식별자가 int 이면 → ID 로 조회.
    식별자가 str 이면 → URN(인덱스가 걸린 unique 컬럼)으로 조회.

    (dataset_id, urn) 을 반환하며 찾지 못하면 None.
    URN→ID 조회가 반복되지 않도록 캐시를 사용한다.
    """
    cache = get_cache()

    if isinstance(identifier, int):
        # ID 조회 — 캐시 키용 URN 함께 획득
        result = await session.execute(
            select(Dataset.id, Dataset.urn).where(Dataset.id == identifier)
        )
        row = result.first()
        return (row[0], row[1]) if row else None

    # URN 조회 — 먼저 매핑 캐시 확인
    mapping_key = f"urn_map:{identifier}"
    cached_id = await cache.get(mapping_key)
    if cached_id is not None:
        return (cached_id["dataset_id"], identifier)

    # URN 으로 DB 조회 (unique 인덱스 컬럼 — 빠름)
    result = await session.execute(select(Dataset.id, Dataset.urn).where(Dataset.urn == identifier))
    row = result.first()

    # 폴백 1: 환경 suffix(`...DEV.dataset`) 제거 후 재매칭 (구 URN 호환)
    if row is None:
        from app.catalog.service import strip_env_urn
        normalized = strip_env_urn(identifier)
        if normalized != identifier:
            result = await session.execute(
                select(Dataset.id, Dataset.urn).where(Dataset.urn == normalized)
            )
            row = result.first()

    # 폴백 2: URN alias 테이블 (구 URN → dataset)
    if row is None:
        from app.catalog.models import DatasetUrnAlias
        result = await session.execute(
            select(Dataset.id, Dataset.urn)
            .join(DatasetUrnAlias, DatasetUrnAlias.dataset_id == Dataset.id)
            .where(DatasetUrnAlias.old_urn == identifier)
        )
        row = result.first()

    if row is None:
        return None

    # URN→ID 매핑을 캐시 (가볍고 수명이 김)
    await cache.put(mapping_key, {"dataset_id": row[0]})
    return (row[0], row[1])


# ---------------------------------------------------------------------------
# Dataset 메타데이터
# ---------------------------------------------------------------------------


async def get_dataset_metadata(
    session: AsyncSession,
    identifier: str | int,
    no_cache: bool = False,
) -> dict | None:
    """dataset 메타데이터 JSON 을 조회한다. 가능하면 캐시를 사용한다.

    Args:
        identifier: Dataset ID(int) 또는 URN(string).
        no_cache: 캐시 우회.
    """
    resolved = await _resolve_dataset_id(session, identifier)
    if resolved is None:
        return None
    dataset_id, urn = resolved

    cache = get_cache()
    cache_key = f"metadata:{urn}"

    if not no_cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            cached["_cache"] = {
                "cached": True,
                "hit": True,
                "ttl_seconds": cache.ttl_seconds,
            }
            return cached

    metadata = await _build_metadata(session, dataset_id)
    if metadata is None:
        return None

    await cache.put(cache_key, metadata)
    metadata["_cache"] = {
        "cached": False,
        "hit": False,
        "ttl_seconds": cache.ttl_seconds,
    }
    return metadata


# ---------------------------------------------------------------------------
# Avro 스키마
# ---------------------------------------------------------------------------


async def get_dataset_avro_schema(
    session: AsyncSession,
    identifier: str | int,
    no_cache: bool = False,
) -> tuple[dict, bool] | None:
    """dataset 의 Avro 스키마를 조회한다. 가능하면 캐시를 사용한다.

    Args:
        identifier: Dataset ID(int) 또는 URN(string).
        no_cache: 캐시 우회.

    Returns: ``(avro_schema, cache_hit)`` 또는 dataset 없음 시 ``None``.
        반환 객체는 표준 Avro record 만 포함하며 비표준 키(``_cache``, ``metadata``)
        는 라우트에서 응답 헤더로 분리해 전달한다.
    """
    resolved = await _resolve_dataset_id(session, identifier)
    if resolved is None:
        return None
    dataset_id, urn = resolved

    cache = get_cache()
    cache_key = f"avro:{urn}"

    if not no_cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached, True

    avro = await _build_avro_schema(session, dataset_id)
    if avro is None:
        return None

    await cache.put(cache_key, avro)
    return avro, False


# ---------------------------------------------------------------------------
# 빌더
# ---------------------------------------------------------------------------


async def _build_avro_schema(session: AsyncSession, dataset_id: int) -> dict | None:
    """dataset 메타데이터로부터 Avro 스키마를 구성한다."""
    result = await session.execute(
        select(
            Dataset.id,
            Dataset.name,
            Dataset.qualified_name,
            Dataset.description,
            Datasource.datasource_id.label("datasource_id_str"),
            Datasource.type.label("datasource_type"),
            Datasource.name.label("datasource_name"),
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(Dataset.id == dataset_id)
    )
    row = result.first()
    if not row:
        return None

    schema_result = await session.execute(
        select(DatasetSchema)
        .where(DatasetSchema.dataset_id == dataset_id)
        .order_by(DatasetSchema.ordinal)
    )
    schema_rows = schema_result.scalars().all()

    # 표준 Avro record/field 만 출력. 추가 메타(native_type/primary_key/pii_type
    # /dataset_id/datasource 등) 는 외부 컨슈머(Schema Registry, fastavro 등) 가
    # 거부할 수 있으므로 응답 본문에서 제외한다. 필요 시 별도 ``/metadata`` 엔드포인트 사용.
    avro_fields = []
    for s in schema_rows:
        avro_type = _resolve_avro_type(s.field_type, s.native_type, s.nullable == "true")
        field: dict = {"name": s.field_path, "type": avro_type}
        if s.description:
            field["doc"] = s.description
        avro_fields.append(field)

    # qualified_name 은 ``{datasource_id}.{path}`` 형식. datasource_id 를 prefix 로 떼어
    # 내고 나머지를 path 로 본다. UI 의 AvroSchemaCard 와 동일한 통일된 표기:
    #   - namespace = datasource_id (그대로)
    #   - name      = path (Avro spec 상 dot 불가하므로 ``.`` → ``_`` 로 sanitize)
    qn = row.qualified_name or row.name or ""
    datasource_id_str = row.datasource_id_str or ""
    prefix = f"{datasource_id_str}."
    path = qn[len(prefix):] if datasource_id_str and qn.startswith(prefix) else qn
    record_name = (path or row.name or "").replace(".", "_")
    namespace = datasource_id_str or "default"

    return {
        "type": "record",
        "name": record_name,
        "namespace": namespace,
        "doc": row.description or f"Avro schema for {path or row.name}",
        "fields": avro_fields,
    }


def _resolve_avro_type(field_type: str, native_type: str | None, nullable: bool):
    """카탈로그 타입을 Avro 타입으로 해석한다. nullable union 을 지원한다."""
    avro_type = None
    if native_type:
        base_native = native_type.split("(")[0].strip().lower()
        base_native = base_native.replace(" unsigned", "")
        avro_type = NATIVE_AVRO_OVERRIDES.get(base_native)

        if base_native in ("decimal", "numeric") and "(" in native_type:
            try:
                params = native_type.split("(")[1].rstrip(")")
                parts = params.split(",")
                precision = int(parts[0].strip())
                scale = int(parts[1].strip()) if len(parts) > 1 else 0
                avro_type = {
                    "type": "bytes",
                    "logicalType": "decimal",
                    "precision": precision,
                    "scale": scale,
                }
            except (ValueError, IndexError):
                pass

    if avro_type is None:
        avro_type = AVRO_TYPE_MAP.get(field_type, "string")

    if nullable:
        return ["null", avro_type]
    return avro_type


async def _build_metadata(session: AsyncSession, dataset_id: int) -> dict | None:
    """DB 조회 결과로부터 메타데이터 dict 를 구성한다."""
    result = await session.execute(
        select(
            Dataset.id,
            Dataset.urn,
            Dataset.name,
            Dataset.description,
            Dataset.origin,
            Dataset.status,
            Dataset.qualified_name,
            Dataset.table_type,
            Dataset.storage_format,
            Dataset.is_synced,
            Dataset.datasource_properties,
            Datasource.id.label("datasource_pk"),
            Datasource.datasource_id.label("datasource_uid"),
            Datasource.name.label("datasource_name"),
            Datasource.type.label("datasource_type"),
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(Dataset.id == dataset_id)
    )
    row = result.first()
    if not row:
        return None

    schema_result = await session.execute(
        select(DatasetSchema)
        .where(DatasetSchema.dataset_id == dataset_id)
        .order_by(DatasetSchema.ordinal)
    )
    schema_rows = schema_result.scalars().all()

    tag_result = await session.execute(
        select(Tag.name)
        .join(DatasetTag, DatasetTag.tag_id == Tag.id)
        .where(DatasetTag.dataset_id == dataset_id)
    )
    tags = [t[0] for t in tag_result.all()]

    owner_result = await session.execute(
        select(Owner.owner_name, Owner.owner_type).where(Owner.dataset_id == dataset_id)
    )
    owners = [{"name": o[0], "type": o[1]} for o in owner_result.all()]

    properties = {}
    if row.datasource_properties:
        try:
            properties = json.loads(row.datasource_properties)
        except (json.JSONDecodeError, TypeError):
            # datasource_properties 가 손상되어도 메타데이터 응답 자체는 막지 않는다
            logger.warning(
                "datasource_properties JSON decode failed: dataset_id=%d", dataset_id
            )
            properties = {}

    return {
        "dataset_id": row.id,
        "urn": row.urn,
        "name": row.name,
        "description": row.description,
        "origin": row.origin,
        "status": row.status,
        "qualified_name": row.qualified_name,
        "table_type": row.table_type,
        "storage_format": row.storage_format,
        "is_synced": row.is_synced,
        "datasource": {
            "id": row.datasource_pk,
            "datasource_id": row.datasource_uid,
            "name": row.datasource_name,
            "type": row.datasource_type,
        },
        "schema": [
            {
                "field_path": s.field_path,
                "field_type": s.field_type,
                "native_type": s.native_type,
                "description": s.description,
                "nullable": s.nullable,
                "is_primary_key": s.is_primary_key,
                "is_unique": s.is_unique,
                "is_indexed": s.is_indexed,
                "is_partition_key": s.is_partition_key,
                "is_distribution_key": s.is_distribution_key,
                "ordinal": s.ordinal,
                "pii_type": s.pii_type,
            }
            for s in schema_rows
        ],
        "tags": tags,
        "owners": owners,
        "properties": properties,
    }
