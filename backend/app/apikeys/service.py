# SPDX-License-Identifier: Apache-2.0
"""API 키 발급·검증 (TODO-056).

- 발급·재발급: ``argus_sk_`` + 무작위 43자. 평문은 응답에서 한 번만 내주고 SHA-256 해시만 저장한다.
- 검증(:func:`lookup`): 해시로 키를 찾아 ``api_keys_cache_seconds`` 동안 기억한다(찾지 못한 결과도). 같은 서버에서 키를
  바꾸면(중지·폐기·재발급·역할·범위) :func:`invalidate` 로 바로 지우고, 다른 서버에는 최대 그 시간 늦게 반영된다.
  만료는 기억한 값으로 매번 다시 판단한다.
- 마지막 사용 시각(:func:`touch`)은 키마다 ``api_keys_last_used_interval_seconds`` 에 한 번만 백그라운드로 쓴다 —
  요청마다 쓰기·커밋하지 않는다.
- 범위(:data:`SCOPES`): 키가 부를 수 있는 API 묶음. 역할(사용자와 같은 권한 기준)과 함께 본다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cachetools import TTLCache
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.apikeys.models import ArgusApiKey
from app.core.config import settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "argus_sk_"
_DISPLAY_PREFIX_LEN = 16
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# 범위 코드 → (이름, 설명, 경로 접두어, 허용 메서드 — None 이면 전부)
SCOPES: dict[str, tuple[str, str, str, frozenset[str] | None]] = {
    "catalog:read": ("카탈로그 읽기", "모든 API 조회(GET). 역할이 허용하는 범위 안에서", "/api/", READ_METHODS),
    "catalog:write": ("카탈로그 읽기·쓰기", "모든 API 조회·등록·수정·삭제. 역할이 허용하는 범위 안에서", "/api/", None),
    "external:read": ("외부 연동 조회", "외부 연동 API(/api/v1/external — Avro 스키마·메타데이터) 조회만", "/api/v1/external", READ_METHODS),
    "schema-registry:read": ("스키마 레지스트리", "Confluent 호환 스키마 레지스트리(/api/v1/schema-registry) 조회만",
                             "/api/v1/schema-registry", READ_METHODS | {"POST"}),
}

# 키로는 부를 수 없는 경로 — 계정 전용(프로필·비밀번호·토큰 갱신)과 키 관리(키로 키를 만들지 못하게)
KEY_FORBIDDEN_PREFIXES = ("/api/v1/auth/", "/api/v1/api-keys")
# 위 접두어 안에서도 범위와 무관하게 키로 부를 수 있는 읽기 전용 신원 확인 — ai-service 등이 키의 주인(sub=apikey:<id>)을
# 확인한다(DEF-085). 프로필 수정(PUT /auth/me)·아바타 등 나머지 /auth/* 는 계속 막는다.
KEY_ALWAYS_ALLOWED = frozenset({("GET", "/api/v1/auth/me")})

# 테스트에서 세션 팩토리를 바꿔 끼우기 위한 훅(None 이면 app.core.database.async_session)
session_factory = None
_cache: TTLCache | None = None
_last_touch: dict[int, float] = {}
_tasks: set[asyncio.Task] = set()


@dataclass(frozen=True)
class KeyInfo:
    id: int
    name: str
    role: str
    scopes: frozenset[str]
    status: str
    expires_at: datetime | None


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """(평문, 표시용 앞부분, 해시)."""
    raw = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return raw, raw[:_DISPLAY_PREFIX_LEN], hash_key(raw)


def looks_like_key(value: str | None) -> bool:
    return bool(value) and value.startswith(KEY_PREFIX)


def owner_identity(key_id: int) -> str:
    """키가 만든 행의 소유자 표기(``created_by`` 등)·감사 로그 작업자 — ``apikey#<id>``."""
    return f"apikey#{key_id}"


def _factory():
    if session_factory is not None:
        return session_factory
    from app.core.database import async_session
    return async_session


def _key_cache() -> TTLCache:
    global _cache
    if _cache is None:
        _cache = TTLCache(maxsize=4096, ttl=max(1, settings.api_keys_cache_seconds))
    return _cache


def invalidate() -> None:
    """검증 캐시를 비운다 — 키를 바꾼 직후."""
    if _cache is not None:
        _cache.clear()


def reset_caches() -> None:
    global _cache
    _cache = None
    _last_touch.clear()


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _info(row: ArgusApiKey) -> KeyInfo:
    return KeyInfo(id=row.id, name=row.name, role=row.role_id,
                   scopes=frozenset(s for s in (row.scopes or "").split(",") if s),
                   status=row.status, expires_at=_aware(row.expires_at))


async def lookup(raw_key: str) -> KeyInfo | None:
    """평문 키 → 키 정보(상태와 무관하게). 없으면 None. 결과는 짧게 기억한다."""
    digest = hash_key(raw_key)
    cache = _key_cache()
    if digest in cache:
        return cache[digest]
    async with _factory()() as session:
        row = (await session.execute(select(ArgusApiKey).where(ArgusApiKey.key_hash == digest))).scalar_one_or_none()
    info = None if row is None else _info(row)
    cache[digest] = info
    return info


def unusable_reason(info: KeyInfo | None) -> str | None:
    """쓸 수 없는 키면 사유, 쓸 수 있으면 None."""
    if info is None:
        return "API 키가 올바르지 않습니다."
    if info.status == "revoked":
        return "폐기된 API 키입니다."
    if info.status != "active":
        return "중지된 API 키입니다."
    if info.expires_at is not None and info.expires_at <= datetime.now(timezone.utc):
        return "만료된 API 키입니다."
    return None


def allows(scopes: frozenset[str], method: str, path: str) -> bool:
    """범위 중 하나라도 이 요청(메서드·경로)을 허용하는가."""
    method = method.upper()
    if (method, path.rstrip("/")) in KEY_ALWAYS_ALLOWED:
        return True
    if path.startswith(KEY_FORBIDDEN_PREFIXES):
        return False
    for code in scopes:
        spec = SCOPES.get(code)
        if spec is None:
            continue
        prefix, methods = spec[2], spec[3]
        if (path == prefix or path.startswith(prefix.rstrip("/") + "/")) and (methods is None or method in methods):
            return True
    return False


def touch(key_id: int, client_ip: str | None) -> None:
    """마지막 사용 시각을 간격마다 한 번 백그라운드로 쓴다(요청을 기다리게 하지 않음)."""
    now = time.monotonic()
    last = _last_touch.get(key_id)
    if last is not None and now - last < settings.api_keys_last_used_interval_seconds:
        return
    _last_touch[key_id] = now
    try:
        task = asyncio.get_running_loop().create_task(_write_last_used(key_id, client_ip))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _write_last_used(key_id: int, client_ip: str | None) -> None:
    try:
        async with _factory()() as session:
            await session.execute(update(ArgusApiKey).where(ArgusApiKey.id == key_id).values(
                last_used_at=datetime.now(timezone.utc), last_used_ip=(client_ip or None) and client_ip[:64]))
            await session.commit()
    except Exception as e:  # noqa: BLE001 — 사용 시각 기록 실패가 요청에 영향을 주지 않게
        logger.warning("API 키 마지막 사용 시각 기록 실패: id=%s %s", key_id, e)


async def drain_touches() -> None:
    """남은 사용 시각 기록을 기다린다(테스트·종료 시)."""
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


# ---------------------------------------------------------------------------
# 관리 (관리자)
# ---------------------------------------------------------------------------

def resolve_expiry(expires_in_days: int | None) -> datetime | None:
    """요청한 만료 일수 → 만료 시각. 서버 기본값·최대값을 적용한다(넘으면 ValueError)."""
    limit = settings.api_keys_max_expire_days
    if expires_in_days is None:
        days = settings.api_keys_default_expire_days
        # 기본값이 최대값보다 길면(무기한 포함) 최대값으로 줄인다 — 최대값만 정한 운영자도 기본 선택으로 발급된다(DEF-078)
        if limit > 0 and (days == 0 or days > limit):
            days = limit
    else:
        days = expires_in_days
    if limit > 0 and (days == 0 or days > limit):
        raise ValueError(f"만료는 {limit}일 이내로 정해야 합니다(무기한 불가).")
    if days == 0:
        return None
    return datetime.now(timezone.utc) + timedelta(days=days)


async def list_keys(session: AsyncSession, include_revoked: bool = False) -> list[ArgusApiKey]:
    stmt = select(ArgusApiKey).order_by(ArgusApiKey.id.desc())
    if not include_revoked:
        stmt = stmt.where(ArgusApiKey.status != "revoked")
    return list((await session.execute(stmt)).scalars().all())


async def get_key(session: AsyncSession, key_id: int) -> ArgusApiKey | None:
    return await session.get(ArgusApiKey, key_id)


async def create_key(session: AsyncSession, *, name: str, description: str | None, role: str,
                     scopes: list[str], expires_at: datetime | None,
                     created_by: str | None) -> tuple[ArgusApiKey, str]:
    raw, prefix, digest = generate_key()
    row = ArgusApiKey(name=name, description=description, key_prefix=prefix, key_hash=digest, role_id=role,
                      scopes=",".join(sorted(scopes)), status="active", expires_at=expires_at, created_by=created_by)
    session.add(row)
    await session.flush()
    invalidate()
    return row, raw


async def rotate_key(session: AsyncSession, row: ArgusApiKey) -> str:
    """새 평문을 만들고 이전 평문은 바로 못 쓰게 한다(id·이름·범위는 그대로)."""
    raw, prefix, digest = generate_key()
    row.key_prefix, row.key_hash = prefix, digest
    row.rotated_at = datetime.now(timezone.utc)
    await session.flush()
    invalidate()
    return raw


async def revoke_key(session: AsyncSession, row: ArgusApiKey, by: str | None) -> None:
    row.status = "revoked"
    row.revoked_at = datetime.now(timezone.utc)
    row.revoked_by = by
    await session.flush()
    invalidate()
