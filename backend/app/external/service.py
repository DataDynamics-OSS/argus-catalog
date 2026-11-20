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

_AVRO_TIMESTAMP = {"type": "long", "logicalType": "timestamp-millis"}
# Avro 명세상 time-millis 는 int 다(long 은 time-micros). ``time(6)`` 처럼 정밀도가 붙어도 time-millis 로 통일한다 (DEF-190)
_AVRO_TIME = {"type": "int", "logicalType": "time-millis"}
_AVRO_DECIMAL = {"type": "bytes", "logicalType": "decimal", "precision": 38, "scale": 10}

# 카탈로그 일반 타입 → Avro 타입 매핑.
# ``DATE`` 는 수집(``app/catalog/sync.py``)이 date · datetime · timestamp · time · year 를 모두 넣는 공통 타입이라,
# 네이티브 타입이 없으면 시각까지 담을 수 있는 timestamp-millis 로 둔다(날짜 전용 컬럼은 네이티브 ``date`` 로 구분).
AVRO_TYPE_MAP = {
    "STRING": "string",
    "NUMBER": "long",
    "DATE": _AVRO_TIMESTAMP,
    "BOOLEAN": "boolean",
    "BYTES": "bytes",
    "MAP": {"type": "map", "values": "string"},
    "ARRAY": {"type": "array", "items": "string"},
    "ENUM": "string",
    # 타입 선택지(``app/catalog/datasource_metadata.py``)에만 있는 이름 — 네이티브 타입으로는 원천마다 뜻이 달라
    # 네이티브 표에 두지 않는다(``int8``: PostgreSQL 8바이트 · Kudu 8비트 → 넓은 쪽, ``long``: Oracle 은 문자열).
    "INT8": "long",
    "LONG": "long",
}

# UI(AvroSchemaCard)에서 저장한 Avro 편집본 — 있으면 자동 생성 대신 이것을 내려준다
AVRO_OVERRIDE_PROPERTY = "argus.avro_schema"

# Avro 용 네이티브 타입 세부 보정 — 키는 ``_native_base`` 가 정리한 이름(소문자, 괄호 · 부호 수식어 제외).
# 화면 Avro 탭(``frontend/apps/web/features/datasets/avro.ts`` ``NATIVE_AVRO_TYPES``)과 같은 표여야 한다.
NATIVE_AVRO_OVERRIDES = {
    "int": "int",
    "integer": "int",
    "smallint": "int",
    "tinyint": "int",
    "mediumint": "int",
    "int16": "int",
    "int32": "int",
    "byte": "int",
    "short": "int",
    "serial": "int",
    "year": "int",
    "bigint": "long",
    "int64": "long",
    "bigserial": "long",
    "float": "float",
    "real": "float",
    "float4": "float",
    "float32": "float",
    "half_float": "float",
    "double": "double",
    "double precision": "double",
    "float8": "double",
    "float64": "double",
    "decimal": _AVRO_DECIMAL,
    "numeric": _AVRO_DECIMAL,
    # 통화 — 자릿수가 원천마다 달라(PostgreSQL 2자리, SQL Server (19,4)·(10,4)) 기본 decimal 로. long 이면 소수부가 빠진다(DEF-194)
    "money": _AVRO_DECIMAL,
    "smallmoney": _AVRO_DECIMAL,
    "number": _AVRO_DECIMAL,
    "date": {"type": "int", "logicalType": "date"},
    "time": _AVRO_TIME,
    "timetz": _AVRO_TIME,
    "time with time zone": _AVRO_TIME,
    "time without time zone": _AVRO_TIME,
    "timestamp": _AVRO_TIMESTAMP,
    "timestamptz": _AVRO_TIMESTAMP,
    "timestamp with time zone": _AVRO_TIMESTAMP,
    "timestamp without time zone": _AVRO_TIMESTAMP,
    "timestamp with local time zone": _AVRO_TIMESTAMP,
    "timestamp_ntz": _AVRO_TIMESTAMP,
    "timestamp_ltz": _AVRO_TIMESTAMP,
    "timestamp_tz": _AVRO_TIMESTAMP,
    "datetime": _AVRO_TIMESTAMP,
    "datetime2": _AVRO_TIMESTAMP,
    "smalldatetime": _AVRO_TIMESTAMP,
    "datetimeoffset": _AVRO_TIMESTAMP,
    "unixtime_micros": _AVRO_TIMESTAMP,
    "interval": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "bytea": "bytes",
    "blob": "bytes",
    "binary": "bytes",
    "varbinary": "bytes",
    "bindata": "bytes",
    "uuid": {"type": "string", "logicalType": "uuid"},
}

# 같은 네이티브 이름이 원천마다 다른 뜻이라, 수집이 넣은 카탈로그 타입 쪽을 따르는 조합 (DEF-193).
# 키는 ``_native_base`` 이름, 값은 그 이름과 어긋나는 카탈로그 타입 — 수집 서비스(metadata-sync)의 타입 표에 실제로
# 있는 쌍만 둔다. 카탈로그 ``DATE`` 는 날짜 · 일시 · 시각을 모두 넣는 공통 타입이라 넣지 않는다(네이티브 ``date`` 가 맞다).
# 화면 Avro 탭(``avro.ts`` ``CATALOG_OVER_NATIVE``)과 같은 표여야 한다.
CATALOG_OVER_NATIVE = {
    "date": ("TIMESTAMP",),   # Oracle DATE — 시각 포함
    "float": ("DOUBLE",),     # SQL Server FLOAT(기본 8바이트) · Oracle FLOAT
    "timestamp": ("BINARY",),  # SQL Server TIMESTAMP(rowversion) — 일시가 아닌 8바이트 값
}

# 타입 이름 뒤에 붙는 부호 · 채움 수식어(MySQL ``int(10) unsigned zerofill``) — Avro 타입 판정에서는 뺀다
_NATIVE_MODIFIERS = re.compile(r"\b(?:unsigned|signed|zerofill)\b", re.ASCII)
_NATIVE_SPACES = re.compile(r"[ \t\n\r\f\v]+")
_DECIMAL_ARG = re.compile(r"[0-9]{1,9}")
_DECIMAL_BASES = ("decimal", "numeric", "number")


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
        if row is None:
            return None
        _remember_urn(row[0], row[1])
        return (row[0], row[1])

    # URN 조회 — 먼저 매핑 캐시 확인
    mapping_key = f"urn_map:{identifier}"
    cached_id = await cache.get(mapping_key)
    if cached_id is not None and cached_id.get("urn"):
        logger.debug("URN 매핑 캐시 적중: urn=%s dataset_id=%s", identifier, cached_id["dataset_id"])
        _remember_urn(cached_id["dataset_id"], cached_id["urn"], identifier)
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
    _remember_urn(row[0], row[1], identifier)
    return (row[0], row[1])


# 캐시에 올린 데이터셋의 id → (정규 URN, 조회에 쓰인 URN들). 스키마가 바뀐 데이터셋을 id 로만 알 때
# (SQLAlchemy 커밋 이벤트) URN 기준 캐시 키를 DB 조회 없이 지우려고 둔다. 데이터셋 수만큼만 자란다.
_urns_by_id: dict[int, set[str]] = {}


def _remember_urn(dataset_id: int, urn: str, alias: str | None = None) -> None:
    keys = _urns_by_id.setdefault(int(dataset_id), set())
    keys.add(urn)
    if alias and alias != urn:
        keys.add(alias)


def forget_dataset_nowait(dataset_id: int) -> int:
    """데이터셋 하나의 외부 API 캐시(메타데이터·Avro·URN 매핑)를 즉시 지운다. 지운 항목 수.

    스키마·설명·Avro 편집본이 바뀐 커밋 직후 ``app.schema_registry.changes`` 가 부른다(동기 — 이벤트 루프 스레드 전용).
    """
    cache = get_cache()
    removed = 0
    for urn in _urns_by_id.pop(int(dataset_id), set()):
        for prefix in ("metadata:", "avro:", "urn_map:"):
            removed += int(cache.discard(f"{prefix}{urn}"))
    return removed


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
        field: dict = {"name": avro_name(s.field_path), "type": avro_type}
        if isinstance(avro_type, list) and avro_type and avro_type[0] == "null":
            field["default"] = None     # null 이 먼저인 union 의 기본값 (화면 Avro 탭과 동일)
        if s.description:
            field["doc"] = s.description
        avro_fields.append(field)

    # qualified_name 은 ``{datasource_id}.{path}`` 형식. datasource_id 를 prefix 로 떼어
    # 내고 나머지를 path 로 본다. UI 의 AvroSchemaCard 와 동일한 통일된 표기:
    #   - namespace = datasource_id 를 Avro 이름 규칙으로 (``mysql-19d0…`` → ``mysql_19d0…``)
    #   - name      = path 를 Avro 이름 규칙으로 (``.`` 등 → ``_``)
    # 데이터소스 ID 에는 항상 ``-`` 가 있어, 그대로 쓰면 Java Avro·fastavro 가 스키마를 거부했다(DEF-040).
    qn = row.qualified_name or row.name or ""
    datasource_id_str = row.datasource_id_str or ""
    prefix = f"{datasource_id_str}."
    path = qn[len(prefix):] if datasource_id_str and qn.startswith(prefix) else qn
    record_name = avro_name(path or row.name or "")
    namespace = avro_namespace(datasource_id_str)

    return {
        "type": "record",
        "name": record_name,
        "namespace": namespace,
        "doc": _description_doc(row.description) or f"Avro schema for {path or row.name}",
        "fields": avro_fields,
    }


_AVRO_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_]")


def avro_name(text: str) -> str:
    """Avro 이름 규칙(``[A-Za-z_][A-Za-z0-9_]*``)에 맞춘 이름 — 그 밖의 문자는 ``_``, 숫자로 시작하면 앞에 ``_``.

    화면 Avro 탭(``frontend/apps/web/features/datasets/avro.ts`` ``avroName``)과 같은 규칙.
    """
    name = _AVRO_INVALID_CHARS.sub("_", text or "") or "_"
    return f"_{name}" if name[0].isdigit() else name


def avro_namespace(text: str) -> str:
    """점으로 나눈 각 부분을 ``avro_name`` 으로 맞춘 네임스페이스 — 예: ``s3-1a2b`` → ``s3_1a2b``."""
    return ".".join(avro_name(p) for p in (text or "").split(".") if p) or "default"


def _native_base(type_text: str) -> tuple[str, str | None]:
    """네이티브 타입 문자열을 ``(기본 타입명, 괄호 안 인자)`` 로 나눈다 — 인자가 없으면 None.

    첫 ``(`` 부터 마지막 ``)`` 까지를 인자로 떼고 앞뒤를 이어 붙여, 괄호 뒤에 붙은 말도 타입명에 남긴다
    (``timestamp(6) with time zone`` → ``timestamp with time zone``). 부호 · 채움 수식어는 빼고 공백은 하나로 줄인다
    (``DECIMAL(10,2) UNSIGNED`` → ``decimal`` + ``10,2``, ``int(10) unsigned zerofill`` → ``int``).
    """
    text = type_text.lower()
    args = None
    start = text.find("(")
    if start >= 0:
        end = text.rfind(")")
        if end < start:
            end = len(text)
        args = text[start + 1:end]
        text = f"{text[:start]} {text[end + 1:]}"
    return " ".join(w for w in _NATIVE_SPACES.split(_NATIVE_MODIFIERS.sub(" ", text)) if w), args


def _native_to_avro(type_text: str | None):
    """DB 네이티브 타입 문자열(``DECIMAL(12,2)``, ``int unsigned`` 등)을 Avro 타입으로. 모르면 None.

    decimal/numeric/number 는 괄호 안 ``(precision, scale)`` 을 읽어 반영한다. 인자가 숫자가 아니거나 셋 이상이거나
    Avro 명세(precision ≥ 1, 0 ≤ scale ≤ precision)에 맞지 않으면 기본값(38, 10)을 쓴다.
    """
    if not type_text:
        return None
    base_native, args = _native_base(type_text)
    avro_type = NATIVE_AVRO_OVERRIDES.get(base_native)

    if base_native in _DECIMAL_BASES and args is not None:
        parts = [" ".join(w for w in _NATIVE_SPACES.split(p) if w) for p in args.split(",")]
        if len(parts) <= 2 and all(_DECIMAL_ARG.fullmatch(p) for p in parts):
            precision = int(parts[0])
            scale = int(parts[1]) if len(parts) > 1 else 0
            if precision >= 1 and scale <= precision:
                avro_type = {
                    "type": "bytes",
                    "logicalType": "decimal",
                    "precision": precision,
                    "scale": scale,
                }
    return avro_type


def _resolve_avro_type(field_type: str, native_type: str | None, nullable: bool):
    """카탈로그 타입을 Avro 타입으로 해석한다. nullable union 을 지원한다.

    native_type 이 비어 있으면(수동 등록·스키마 편집 그리드) field_type 자체(``INTEGER``,
    ``DECIMAL(12,2)``, ``varchar`` 등)를 네이티브 타입처럼 해석한다 — 화면 Avro 탭과 같은 결과.
    네이티브 이름이 ``CATALOG_OVER_NATIVE`` 의 카탈로그 타입과 짝이면 네이티브 대신 카탈로그 타입으로 해석한다
    (Oracle ``DATE`` + ``TIMESTAMP`` → timestamp-millis).
    """
    avro_type = _native_to_avro(native_type)
    catalog_type = (field_type or "").upper()
    if avro_type is not None and catalog_type in CATALOG_OVER_NATIVE.get(_native_base(native_type)[0], ()):
        # 원천마다 뜻이 다른 이름 — 수집이 정한 카탈로그 타입으로 해석한다 (DEF-193)
        avro_type = None
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

    # AI-Ready 점검 요약(TODO-096 3단계, 검토 문서 §6.2) — 이용자가 AI 학습에 써도 되는 상태인지 판단하는 근거.
    # 점검이 실패해도 메타데이터 응답은 막지 않는다.
    ai_ready_summary = None
    try:
        from app.catalog import ai_ready_check

        check = await ai_ready_check.evaluate_dataset(session, dataset_id)
        if check is not None:
            ai_ready_summary = {
                "ready": check["min_requirements"]["met"]
                and not [i for i in check["items"] if i["level"] == "REQUIRED" and i["status"] not in ("PASS", "NA")],
                "required_rate": check["required"]["rate"],
                "recommended_rate": check["recommended"]["rate"],
                "min_requirements_met": check["min_requirements"]["met"],
                "unmet_required": [
                    {"key": i["key"], "status": i["status"], "reason": i["evidence"]}
                    for i in check["items"] if i["level"] == "REQUIRED" and i["status"] not in ("PASS", "NA")
                ],
            }
    except Exception:  # noqa: BLE001 — 점검 모듈 오류가 외부 메타데이터 API 를 깨지 않게
        logger.exception("외부 메타데이터의 AI-Ready 요약 계산 실패: dataset_id=%d", dataset_id)

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
                "unit": s.unit,
                "calculation": s.calculation,
            }
            for s in schema_rows
        ],
        "tags": tags,
        "owners": owners,
        "properties": properties,
        "ai_ready": ai_ready_summary,
    }
