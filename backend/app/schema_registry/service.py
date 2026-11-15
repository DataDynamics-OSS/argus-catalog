# SPDX-License-Identifier: Apache-2.0
"""스키마 레지스트리 조회·등록.

성능 설계 (TODO-052)
- id·(데이터셋, 버전) 조회는 결과가 바뀌지 않으므로 별도 캐시(``_immutable``)에 오래 둔다.
- 최신 버전(``latest``)은 외부 API 캐시와 같은 TTL 캐시(``sr_latest:{dataset_id}``)에 두고, Avro 입력이 바뀐 커밋 직후
  ``app.schema_registry.changes`` 가 지운다. 미스면 지금 Avro(외부 API 의 ``avro:`` 캐시 경유)의 지문으로 등록 행을
  찾거나 만든다 — 등록돼 있으면 DB 한 행 조회로 끝난다.
- 새 버전은 변경 직후 백그라운드에서 미리 등록된다(``changes.start_background``).
"""

from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset
from app.core.config import settings
from app.external.cache import MetadataCache, get_cache
from app.external.service import _resolve_dataset_id, get_dataset_avro_schema
from app.schema_registry.models import SchemaRegistryVersion

logger = logging.getLogger(__name__)

_immutable: MetadataCache | None = None


def immutable_cache() -> MetadataCache:
    """id·버전 번호 조회 결과 캐시(내용이 바뀌지 않는 응답)."""
    global _immutable
    if _immutable is None:
        _immutable = MetadataCache(
            max_size=settings.schema_registry_immutable_cache_size,
            ttl_seconds=settings.schema_registry_immutable_cache_seconds,
        )
    return _immutable


def reset_caches() -> None:
    """테스트·설정 변경용."""
    global _immutable
    _immutable = None


def _latest_key(dataset_id: int) -> str:
    return f"sr_latest:{dataset_id}"


def forget_latest_nowait(dataset_id: int) -> int:
    """최신 버전 캐시 한 건을 지운다(동기 — 커밋 이벤트용)."""
    return int(get_cache().discard(_latest_key(dataset_id)))


# ---------------------------------------------------------------------------
# 지문
# ---------------------------------------------------------------------------

def schema_text(avro: dict) -> str:
    """Confluent 응답의 ``schema`` 문자열 — 키 순서는 그대로 둔 압축 JSON."""
    return json.dumps(avro, ensure_ascii=False, separators=(",", ":"))


def fingerprint(avro) -> str:
    """키 정렬 압축 JSON 의 SHA-256. 공백·키 순서가 달라도 같은 스키마면 같은 값."""
    canonical = json.dumps(avro, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_dict(row: SchemaRegistryVersion, subject: str) -> dict:
    return {"subject": subject, "version": row.version, "id": row.id, "schema": row.schema_text,
            "dataset_id": row.dataset_id}


# ---------------------------------------------------------------------------
# subject(데이터셋) 해석·등록
# ---------------------------------------------------------------------------

async def resolve_subject(session: AsyncSession, subject: str) -> tuple[int, str] | None:
    """subject(데이터셋 URN — 옛 URN·별칭 포함) → (dataset_id, 정규 URN). 외부 API 의 URN 매핑 캐시를 같이 쓴다."""
    return await _resolve_dataset_id(session, subject)


async def _register(session: AsyncSession, dataset_id: int, avro: dict) -> SchemaRegistryVersion:
    """지금 Avro 를 등록 행으로 — 같은 지문이 있으면 그 행, 없으면 다음 버전으로 추가."""
    fp = fingerprint(avro)
    for attempt in range(3):
        row = (await session.execute(
            select(SchemaRegistryVersion).where(
                SchemaRegistryVersion.dataset_id == dataset_id,
                SchemaRegistryVersion.fingerprint == fp,
            )
        )).scalars().first()
        if row is not None:
            return row
        next_version = (await session.execute(
            select(func.coalesce(func.max(SchemaRegistryVersion.version), 0))
            .where(SchemaRegistryVersion.dataset_id == dataset_id)
        )).scalar_one() + 1
        row = SchemaRegistryVersion(dataset_id=dataset_id, version=next_version, fingerprint=fp,
                                    schema_text=schema_text(avro))
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            # 다른 요청·백그라운드 등록이 같은 순간 같은 버전 번호나 지문을 넣었다 — 다시 읽는다
            await session.rollback()
            logger.debug("스키마 등록 경합 — 다시 시도: dataset_id=%d attempt=%d", dataset_id, attempt + 1)
            continue
        logger.info("스키마 레지스트리 새 버전: dataset_id=%d version=%d id=%d", dataset_id, row.version, row.id)
        return row
    raise RuntimeError(f"스키마 버전 등록 경합이 계속됩니다: dataset_id={dataset_id}")


async def latest(session: AsyncSession, dataset_id: int, urn: str) -> tuple[dict, bool] | None:
    """데이터셋의 지금 스키마를 등록 버전으로 — ``({subject, version, id, schema}, 캐시 적중)``. Avro 를 못 만들면 None."""
    cache = get_cache()
    key = _latest_key(dataset_id)
    if settings.cache_enabled:
        hit = await cache.get(key)
        if hit is not None:
            return hit, True
    result = await get_dataset_avro_schema(session, urn, no_cache=not settings.cache_enabled)
    if result is None:
        return None
    avro, _ = result
    row = await _register(session, dataset_id, avro)
    value = _row_dict(row, urn)
    if settings.cache_enabled:
        await cache.put(key, value)
    return value, False


async def register_current(session: AsyncSession, dataset_id: int) -> dict | None:
    """백그라운드 미리 등록 — 캐시를 거치지 않고 지금 Avro 를 등록한다."""
    resolved = await _resolve_dataset_id(session, dataset_id)
    if resolved is None:
        return None
    _, urn = resolved
    result = await get_dataset_avro_schema(session, dataset_id, no_cache=True)
    if result is None:
        return None
    row = await _register(session, dataset_id, result[0])
    return _row_dict(row, urn)


# ---------------------------------------------------------------------------
# 바뀌지 않는 조회
# ---------------------------------------------------------------------------

async def by_id(session: AsyncSession, schema_id: int) -> tuple[dict, bool] | None:
    """스키마 id → ``{subject, version, id, schema, dataset_id}``."""
    cache = immutable_cache()
    key = f"id:{schema_id}"
    hit = await cache.get(key)
    if hit is not None:
        return hit, True
    row = (await session.execute(
        select(SchemaRegistryVersion, Dataset.urn)
        .join(Dataset, Dataset.id == SchemaRegistryVersion.dataset_id)
        .where(SchemaRegistryVersion.id == schema_id)
    )).first()
    if row is None:
        return None
    value = _row_dict(row[0], row[1])
    await cache.put(key, value)
    return value, False


async def by_version(session: AsyncSession, dataset_id: int, urn: str, version: int) -> tuple[dict, bool] | None:
    """(데이터셋, 버전 번호) → 등록 행."""
    cache = immutable_cache()
    key = f"ver:{dataset_id}:{version}"
    hit = await cache.get(key)
    if hit is not None:
        return hit, True
    row = (await session.execute(
        select(SchemaRegistryVersion).where(
            SchemaRegistryVersion.dataset_id == dataset_id, SchemaRegistryVersion.version == version)
    )).scalars().first()
    if row is None:
        return None
    value = _row_dict(row, urn)
    await cache.put(key, value)
    return value, False


async def by_schema(session: AsyncSession, dataset_id: int, urn: str, schema: str) -> dict | None:
    """클라이언트가 보낸 스키마 문자열이 이 subject 에 등록돼 있으면 그 행(``POST /subjects/{subject}``)."""
    try:
        parsed = json.loads(schema)
    except (TypeError, ValueError):
        raise ValueError("스키마가 JSON 이 아닙니다.") from None
    fp = fingerprint(parsed)
    cache = immutable_cache()
    key = f"fp:{dataset_id}:{fp}"
    hit = await cache.get(key)
    if hit is not None:
        return hit
    row = (await session.execute(
        select(SchemaRegistryVersion).where(
            SchemaRegistryVersion.dataset_id == dataset_id, SchemaRegistryVersion.fingerprint == fp)
    )).scalars().first()
    if row is None:
        return None
    value = _row_dict(row, urn)
    await cache.put(key, value)
    return value


async def versions(session: AsyncSession, dataset_id: int) -> list[int]:
    """등록된 버전 번호(오름차순)."""
    rows = await session.execute(
        select(SchemaRegistryVersion.version)
        .where(SchemaRegistryVersion.dataset_id == dataset_id)
        .order_by(SchemaRegistryVersion.version)
    )
    return [r[0] for r in rows]


async def subjects(session: AsyncSession) -> list[str]:
    """subject 목록 — 원본에서 사라지지 않은 데이터셋의 URN."""
    rows = await session.execute(
        select(Dataset.urn).where(Dataset.status != "removed").order_by(Dataset.urn)
    )
    return [r[0] for r in rows]
