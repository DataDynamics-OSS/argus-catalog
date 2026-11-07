# SPDX-License-Identifier: Apache-2.0
"""외부 API 용 dataset 메타데이터/Avro 스키마 빌더 + 캐시 관리 서비스.

외부 컨슈머가 dataset 정보를 빠르게 얻을 수 있도록 메타데이터/Avro JSON 을 조립하고
TTL 캐시에 적재한다. 캐시 키는 URN 기준(``metadata:{urn}`` / ``avro:{urn}``)이고, ``dataset_id``
입력은 DB 에서 URN 을 조회해 같은 키로 매핑된다. URN 입력은 ``urn_map:{urn}`` 키로
URN → dataset_id 매핑을 캐시해 반복 조회 비용을 줄인다(ID 입력은 매핑을 캐시하지 않는다).
"""

import json
import logging
import re
from html import unescape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import (
    Dataset,
    DatasetProperty,
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

# UI(AvroSchemaCard)에서 저장한 Avro 편집본 — 있으면 자동 생성 대신 이것을 내려준다
AVRO_OVERRIDE_PROPERTY = "argus.avro_schema"

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

    식별자가 int 이면 → ID 로 조회(캐시 없음).
    식별자가 str 이면 → URN(인덱스가 걸린 unique 컬럼)으로 조회하고, 없으면 환경 suffix 제거
    URN → URN alias 테이블 순으로 폴백한다.

    (dataset_id, urn) 을 반환하며 찾지 못하면 None.
    URN→ID 조회가 반복되지 않도록 ``urn_map:{urn}`` 캐시를 사용한다. 반환 URN 은 캐시 적중
    여부와 무관하게 항상 DB 에 저장된(정규) URN 이다 — 옛/별칭 URN 으로 들어와도 캐시 키
    (``metadata:{urn}`` 등)가 정규 URN 하나로 모이도록 매핑 캐시에 정규 URN 을 함께 저장한다.
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
    if cached_id is not None and cached_id.get("urn"):
        logger.debug("URN 매핑 캐시 적중: urn=%s dataset_id=%s", identifier, cached_id["dataset_id"])
        return (cached_id["dataset_id"], cached_id["urn"])

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
            if row is not None:
                logger.debug("환경 suffix 제거 URN 으로 매칭: urn=%s normalized=%s", identifier, normalized)

    # 폴백 2: URN alias 테이블 (구 URN → dataset)
    if row is None:
        from app.catalog.models import DatasetUrnAlias
        result = await session.execute(
            select(Dataset.id, Dataset.urn)
            .join(DatasetUrnAlias, DatasetUrnAlias.dataset_id == Dataset.id)
            .where(DatasetUrnAlias.old_urn == identifier)
        )
        row = result.first()
        if row is not None:
            logger.debug("URN alias 로 매칭: old_urn=%s dataset_id=%d", identifier, row[0])

    if row is None:
        logger.debug("URN 해석 실패(데이터셋 없음): urn=%s", identifier)
        return None

    # URN→ID 매핑을 캐시 (가볍고 수명이 김)
    # 정규 URN 도 함께 저장 — 캐시 적중 시에도 입력(옛/별칭) URN 이 아닌 정규 URN 을 돌려준다
    await cache.put(mapping_key, {"dataset_id": row[0], "urn": row[1]})
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
        no_cache: 캐시 우회(조회 결과는 그래도 캐시에 다시 적재한다).

    Returns: 메타데이터 dict(``_cache`` 진단 키 포함). dataset 이 없으면 ``None``.
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
            # 캐시 원본에 진단 키가 섞이지 않도록 얕은 복사본에 붙여 반환한다
            result = dict(cached)
            result["_cache"] = {
                "cached": True,
                "hit": True,
                "ttl_seconds": cache.ttl_seconds,
            }
            return result

    logger.debug("메타데이터 캐시 미스/우회 — DB 에서 구성: dataset_id=%d no_cache=%s", dataset_id, no_cache)
    metadata = await _build_metadata(session, dataset_id)
    if metadata is None:
        return None

    await cache.put(cache_key, metadata)
    # 캐시에 넣은 dict 와 분리 — 진단 키는 반환용 얕은 복사본에만 붙인다
    result = dict(metadata)
    result["_cache"] = {
        "cached": False,
        "hit": False,
        "ttl_seconds": cache.ttl_seconds,
    }
    return result


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
        반환 객체는 표준 Avro record 만 포함하며(비표준 키 없음), 캐시 적중 여부는 튜플의
        두 번째 값으로 돌려줘 라우트가 ``X-Cache-*`` 응답 헤더로 노출한다.
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

    logger.debug("Avro 캐시 미스/우회 — DB 에서 구성: dataset_id=%d no_cache=%s", dataset_id, no_cache)
    avro = await _build_avro_schema(session, dataset_id)
    if avro is None:
        return None

    await cache.put(cache_key, avro)
    return avro, False


# ---------------------------------------------------------------------------
# 빌더
# ---------------------------------------------------------------------------


async def invalidate_dataset_avro(urn: str) -> None:
    """Avro 캐시 한 건 무효화 — 속성(Avro 편집본) 변경 직후 외부 API 가 옛 값을 주지 않도록."""
    removed = await get_cache().invalidate(f"avro:{urn}")
    logger.debug("Avro 캐시 무효화 요청: urn=%s removed=%s", urn, removed)


_MD_RULES: list[tuple[re.Pattern, str]] = [
    # 화면 features/search/excerpt.ts ``markdownToPlain`` 과 같은 순서·규칙(JS \w 는 ASCII 라 re.ASCII)
    (re.compile(r"```[\s\S]*?```"), " "),                          # 코드 블록
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),                     # 이미지
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),                  # 링크 → 텍스트
    (re.compile(r"^\s{0,3}#{1,6}\s+[^\n]*$", re.M), " "),            # 제목 줄은 통째로 뺀다
    (re.compile(r"^\s*>\s?", re.M), ""),                             # 인용
    (re.compile(r"^\s*([-*+]|\d+\.)\s+", re.M), ""),                 # 목록 기호
    (re.compile(r"^\s*\|?\s*:?-{3,}.*$", re.M), " "),                # 표 구분선
    (re.compile(r"\|"), " "),                                       # 표 칸
    (re.compile(r"`([^`]*)`"), r"\1"),                               # 인라인 코드
    (re.compile(r"(\*\*|__|~~)(?=\S)([\s\S]*?\S)\1"), r"\2"),        # 굵게·취소선
    (re.compile(r"(^|[^\w*])\*(?=\S)([^*]*?\S)\*(?!\w)", re.A), r"\1\2"),  # 기울임(*)
    (re.compile(r"(^|\W)_(?=\S)([^_]*?\S)_(?!\w)", re.A), r"\1\2"),       # 기울임(_)
]


def _description_doc(description: str | None) -> str:
    """데이터셋 설명(마크다운, 예전 리치 텍스트는 HTML) → Avro ``doc`` 용 한 줄 평문.

    HTML 태그·엔티티를 걷어낸 뒤 마크다운 기호를 뺀다(제목 줄 제외). 화면 Avro 탭
    (``features/datasets/avro.ts`` ``descriptionToAvroDoc``)과 같은 결과여야 한다.
    """
    if not description:
        return ""
    text = re.sub(r"<(br|/p|/li|/h[1-6]|/div)\s*/?>", "\n", description, flags=re.IGNORECASE)
    text = unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
    for pattern, repl in _MD_RULES:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()


async def _build_avro_schema(session: AsyncSession, dataset_id: int) -> dict | None:
    """dataset 메타데이터로부터 Avro 스키마를 구성한다.

    우선순위: 사용자 편집본(``argus.avro_schema`` 속성, JSON 객체) → 스키마 컬럼으로 자동 생성.
    dataset 이 없으면 ``None``.
    """
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

    # 사용자 편집본(argus.avro_schema)이 있으면 우선 — UI "가져오기 가이드"와 일치.
    # JSON 이 아니거나 객체가 아니면(손상) 경고 후 자동 생성으로 폴백한다.
    override = (await session.execute(
        select(DatasetProperty.property_value).where(
            DatasetProperty.dataset_id == dataset_id,
            DatasetProperty.property_key == AVRO_OVERRIDE_PROPERTY,
        )
    )).scalar()
    if override:
        try:
            parsed = json.loads(override)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            logger.debug("Avro 편집본 사용: dataset_id=%d", dataset_id)
            return parsed
        logger.warning("Avro 편집본이 JSON 객체가 아니어서 무시: dataset_id=%d", dataset_id)

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
        if isinstance(avro_type, list) and avro_type and avro_type[0] == "null":
            field["default"] = None     # null 이 먼저인 union 의 기본값 (화면 Avro 탭과 동일)
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
        "doc": _description_doc(row.description) or f"Avro schema for {path or row.name}",
        "fields": avro_fields,
    }


def _native_to_avro(type_text: str | None):
    """DB 네이티브 타입 문자열(``DECIMAL(12,2)``, ``int unsigned`` 등)을 Avro 타입으로. 모르면 None.

    decimal/numeric 은 괄호 안 ``(precision, scale)`` 을 읽어 반영하고, 파싱 실패 시 기본값(38, 10)을 쓴다.
    """
    if not type_text:
        return None
    # 괄호 앞 기본 타입명만 소문자로 비교(``varchar(255)`` → ``varchar``)
    base_native = type_text.split("(")[0].strip().lower()
    base_native = base_native.replace(" unsigned", "")
    avro_type = NATIVE_AVRO_OVERRIDES.get(base_native)

    if base_native in ("decimal", "numeric") and "(" in type_text:
        try:
            params = type_text.split("(")[1].rstrip(")")
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
    return avro_type


def _resolve_avro_type(field_type: str, native_type: str | None, nullable: bool):
    """카탈로그 타입을 Avro 타입으로 해석한다. nullable union 을 지원한다.

    native_type 이 비어 있으면(수동 등록·스키마 편집 그리드) field_type 자체(``INTEGER``,
    ``DECIMAL(12,2)``, ``varchar`` 등)를 네이티브 타입처럼 해석한다 — 화면 Avro 탭과 같은 결과.
    """
    avro_type = _native_to_avro(native_type)
    if avro_type is None:
        avro_type = AVRO_TYPE_MAP.get((field_type or "").upper()) or _native_to_avro(field_type) or "string"

    if nullable:
        return ["null", avro_type]
    return avro_type


async def _build_metadata(session: AsyncSession, dataset_id: int) -> dict | None:
    """DB 조회 결과로부터 메타데이터 dict 를 구성한다(스키마·태그·소유자·데이터소스 속성 포함).

    dataset 이 없으면 ``None``.
    """
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
                "datasource_properties JSON 파싱 실패 — 빈 속성으로 대체: dataset_id=%d", dataset_id
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
