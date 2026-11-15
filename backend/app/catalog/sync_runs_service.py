# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 수집 — 범위 저장, 실행(백그라운드), 진행 보고 반영, 응답 없음 판정.

design/datasource-sync-scope-design.md 1단계.

- 범위는 ``catalog_datasource_sync_settings`` 에 저장한다. 범위가 없으면 수집을 막는다(결정 사항 1).
- 실행은 두 갈래다.
  - BUILTIN: mysql·postgresql — 백엔드 내장 sync(``app.catalog.sync``)를 백그라운드 태스크로 돌린다.
  - METADATA_SYNC: 그 밖의 유형 — 저장된 접속 정보를 풀어 metadata-sync ``POST /v2/jobs`` 로 보낸다.
    진행은 metadata-sync 가 ``PATCH /internal/sync-runs/{id}`` 로 밀어 넣는다.
- 실행 행(``catalog_datasource_sync_runs``)이 정본이다. 보고가 2분 넘게 없으면 화면에 "응답 없음",
  5분이 지나면 FAILED 로 바꾼다(조회·새 실행 시점에 판정).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.datasource_metadata import datasource_role
from app.catalog.models import (
    Dataset,
    Datasource,
    DatasourceConfiguration,
    DatasourceSyncRun,
    DatasourceSyncRunItem,
    DatasourceSyncSetting,
)
from app.catalog.schemas import (
    SyncBrowseItem,
    SyncBrowseResponse,
    SyncRunReport,
    SyncRunResponse,
    SyncRunSummary,
    SyncScheduleUpdate,
    SyncScope,
    SyncScopePreview,
    SyncSettingsResponse,
    SyncSettingsUpdate,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

BUILTIN_TYPES = frozenset({"mysql", "postgresql"})
# metadata-sync ``sync.jobs.adapters.JOB_TYPES`` 와 맞춘다
JOB_TYPES = frozenset({"iceberg-rest", "iceberg-hadoop", "hive", "trino", "kudu", "starrocks", "greenplum", "oracle", "mssql"})
CONTAINER_LABELS = {
    "mysql": "데이터베이스", "postgresql": "스키마", "hive": "데이터베이스", "iceberg-rest": "네임스페이스",
    "iceberg-hadoop": "네임스페이스", "trino": "카탈로그", "starrocks": "데이터베이스", "greenplum": "스키마", "kudu": "대상",
    "oracle": "스키마", "mssql": "데이터베이스",
}
NO_CONTAINER_TYPES = frozenset({"kudu"})  # 1단계는 전체 수집만
NESTED_CONTAINER_TYPES = frozenset({"iceberg-rest", "iceberg-hadoop"})  # 하위 네임스페이스를 펼쳐 고를 수 있음
BROWSE_PAGE = 200

ACTIVE = ("QUEUED", "RUNNING")
TERMINAL = frozenset({"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"})
SILENT_WARN = timedelta(minutes=2)
SILENT_FAIL = timedelta(minutes=5)
STALE_MESSAGE = "수집 서비스 응답 없음으로 중단되었습니다(5분 넘게 진행 보고 없음). 다시 실행하세요."
MAX_ITEMS = 1000
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# 내장 sync 백그라운드 태스크 강참조(GC 방지)
_tasks: set[asyncio.Task] = set()


class SyncRunError(Exception):
    """사용자 입력·상태 오류 — 라우터에서 ``status_code`` 로 응답."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def executor_for(ds_type: str | None) -> str | None:
    t = (ds_type or "").lower()
    if t in BUILTIN_TYPES:
        return "BUILTIN"
    if t in JOB_TYPES:
        return "METADATA_SYNC"
    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite 등 naive 로 돌아오는 값은 UTC 로 본다."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _scope_from_json(raw: str | None) -> SyncScope | None:
    if not raw:
        return None
    try:
        return SyncScope.model_validate(json.loads(raw))
    except Exception:  # noqa: BLE001 — 깨진 값은 범위 없음으로
        logger.warning("저장된 수집 범위를 읽지 못해 무시합니다: %r", raw[:200])
        return None


def _sync_headers() -> dict:
    token = settings.metadata_sync_api_token
    return {"X-API-Token": token} if token else {}


async def _get_datasource(session: AsyncSession, datasource_id: int) -> Datasource:
    ds = await session.get(Datasource, datasource_id)
    if ds is None:
        raise SyncRunError("데이터 소스를 찾을 수 없습니다.", 404)
    return ds


# ---------------------------------------------------------------------------
# 범위
# ---------------------------------------------------------------------------

async def _last_queued_at(session: AsyncSession, datasource_id: int) -> datetime | None:
    """이 데이터 소스의 마지막 실행 접수 시각(수동·예약 모두)."""
    return _aware((await session.execute(
        select(func.max(DatasourceSyncRun.queued_at)).where(DatasourceSyncRun.datasource_id == datasource_id)
    )).scalar())


def _next_scheduled(row: DatasourceSyncSetting | None, last_queued: datetime | None) -> datetime | None:
    """다음 예약 시각 — 마지막 실행 접수(없으면 설정 저장) + 주기. 예약이 꺼졌거나 범위가 없으면 None."""
    if row is None or not row.schedule_enabled or not row.schedule_interval_minutes or not row.scope_json:
        return None
    base = last_queued or _aware(row.updated_at)
    return base + timedelta(minutes=row.schedule_interval_minutes) if base else None


async def _engine_sync_catalog_missing(session: AsyncSession, ds) -> bool:
    """쿼리 엔진인데 SYNC 카탈로그 연결이 없는지 — ``start_run`` 이 409 로 막는 조건과 같다."""
    if datasource_role(ds.type) != "ENGINE":
        return False
    from app.catalog.catalogs_service import sync_catalog_id

    return await sync_catalog_id(session, ds.id) is None


async def get_settings(session: AsyncSession, datasource_id: int) -> SyncSettingsResponse:
    ds = await _get_datasource(session, datasource_id)
    row = await session.get(DatasourceSyncSetting, datasource_id)
    nxt = _next_scheduled(row, await _last_queued_at(session, datasource_id)) if row else None
    return SyncSettingsResponse(
        sync_catalog_missing=await _engine_sync_catalog_missing(session, ds),
        datasource_id=datasource_id,
        scope=_scope_from_json(row.scope_json) if row else None,
        executor=executor_for(ds.type),
        container_label=CONTAINER_LABELS.get(ds.type, "대상"),
        supports_containers=ds.type not in NO_CONTAINER_TYPES,
        nested_containers=ds.type in NESTED_CONTAINER_TYPES,
        updated_by=row.updated_by if row else None,
        updated_at=row.updated_at if row else None,
        schedule_enabled=bool(row and row.schedule_enabled),
        schedule_interval_minutes=row.schedule_interval_minutes if row else None,
        next_scheduled_at=nxt,
    )


async def save_schedule(session: AsyncSession, datasource_id: int, req: SyncScheduleUpdate,
                        username: str | None) -> SyncSettingsResponse:
    """예약 수집 켜기·끄기·주기 변경. 켜려면 저장된 수집 범위가 있어야 한다(범위 없이 전체를 가져오지 않게)."""
    ds = await _get_datasource(session, datasource_id)
    if executor_for(ds.type) is None:
        raise SyncRunError(f"이 유형({ds.type})은 아직 화면 수집을 지원하지 않습니다.", 400)
    row = await session.get(DatasourceSyncSetting, datasource_id)
    if req.enabled and (row is None or not row.scope_json):
        raise SyncRunError("수집 범위를 먼저 선택하세요.", 400)
    if row is None:
        row = DatasourceSyncSetting(datasource_id=datasource_id, schedule_enabled=False)
        session.add(row)
    row.schedule_enabled = req.enabled
    if req.interval_minutes is not None:
        row.schedule_interval_minutes = req.interval_minutes
    row.updated_by = username
    await session.commit()
    await session.refresh(row)
    _schedule_backoff.pop(datasource_id, None)
    logger.info("수집 예약 저장: datasource_id=%d enabled=%s interval=%s by=%s", datasource_id, req.enabled,
                row.schedule_interval_minutes, username)
    return await get_settings(session, datasource_id)


def _check_scope_for_type(ds_type: str, scope: SyncScope) -> None:
    if ds_type in NO_CONTAINER_TYPES and not scope.include_all:
        raise SyncRunError("이 유형은 아직 전체 수집만 지원합니다.", 400)


async def save_settings(session: AsyncSession, datasource_id: int, req: SyncSettingsUpdate,
                        username: str | None) -> SyncSettingsResponse:
    ds = await _get_datasource(session, datasource_id)
    if req.scope is not None:
        _check_scope_for_type(ds.type, req.scope)
    row = await session.get(DatasourceSyncSetting, datasource_id)
    if row is None:
        row = DatasourceSyncSetting(datasource_id=datasource_id, schedule_enabled=False)
        session.add(row)
    row.scope_json = req.scope.model_dump_json(exclude_none=True) if req.scope else None
    row.updated_by = username
    removed = 0
    if req.remove_outside and req.scope is not None:
        outside = [d for d in await _synced_datasets(session, ds)
                   if not _covered(req.scope, ds.type, ds.datasource_id, d.urn)]
        if outside:
            await session.execute(update(Dataset).where(Dataset.id.in_([d.id for d in outside]))
                                  .values(status="removed"))
            removed = len(outside)
    await session.commit()
    # updated_at 은 onupdate(func.now()) 라 UPDATE 뒤 만료된다 — 비동기 세션에서 지연 로드하지 않도록 다시 읽는다(DEF-019)
    await session.refresh(row)
    logger.info("수집 범위 저장: datasource_id=%d by=%s scope=%s removed_outside=%d", datasource_id, username,
                "전체" if req.scope and req.scope.include_all else
                (len(req.scope.include) if req.scope else "없음"), removed)
    if req.scope is None and row.schedule_enabled:
        # 범위를 지우면 예약도 끈다 — 범위 없는 수집은 막혀 있어 예약이 매번 실패한다
        row.schedule_enabled = False
        await session.commit()
        await session.refresh(row)
    resp = await get_settings(session, datasource_id)
    resp.removed_outside = removed if req.remove_outside else None
    return resp


# ---------------------------------------------------------------------------
# 데이터셋 ↔ 컨테이너 (범위 밖 데이터셋·탐색 화면의 수집 건수)
# ---------------------------------------------------------------------------

def split_of(ds_type: str, datasource_key: str, urn: str) -> tuple[str | None, str | None]:
    """데이터셋 URN(``{key}.{qualified}.dataset``) → (수집 범위 컨테이너, 컨테이너 안 상대 이름). 해석 못 하면 (None, None).

    - Trino: ``catalog.schema.table`` → (카탈로그, ``schema.table``)
    - MS-SQL: ``db.schema.table`` → (데이터베이스, ``schema.table``) — 스키마는 고르지 않는 중간 단계
    - Greenplum: ``db.schema.table`` → (스키마, 테이블) — 접속 DB 하나가 대상
    - 그 밖: (테이블을 뺀 나머지, 테이블) — Iceberg 는 중첩 네임스페이스 전체, MySQL·Hive 는 DB, PostgreSQL·Oracle 은 스키마
    """
    prefix, suffix = f"{datasource_key}.", ".dataset"
    if not (urn.startswith(prefix) and urn.endswith(suffix)):
        return None, None
    parts = urn[len(prefix):-len(suffix)].split(".")
    if len(parts) < 2:
        return None, None
    if ds_type in ("trino", "mssql"):
        return parts[0], ".".join(parts[1:])
    if ds_type == "greenplum":
        return (parts[1], ".".join(parts[2:])) if len(parts) >= 3 else (None, None)
    return ".".join(parts[:-1]), parts[-1]


def container_of(ds_type: str, datasource_key: str, urn: str) -> str | None:
    """데이터셋 URN → 수집 범위 컨테이너(점 표기). 해석 못 하면 None."""
    return split_of(ds_type, datasource_key, urn)[0]


def scope_excludes(scope: SyncScope, container: str | None, rel: str) -> bool:
    """제외 패턴에 걸리는지 — 상대 이름 또는 마지막 부분(테이블 이름)과 glob 비교(대소문자 무시).
    metadata-sync ``sync.jobs.scope.ScopeRules.excluded`` 와 같은 규칙."""
    name = rel.lower()
    last = name.rsplit(".", 1)[-1]
    for ex in scope.exclude:
        if ex.path is not None and ".".join(ex.path) != container:
            continue
        pattern = ex.table_pattern.lower()
        if fnmatchcase(name, pattern) or fnmatchcase(last, pattern):
            return True
    return False


def scope_allows(scope: SyncScope, container: str | None, rel: str | None) -> bool:
    """범위가 이 테이블을 수집하는지. 해석 못 한 데이터셋(컨테이너 None)은 건드리지 않도록 '포함'으로 본다."""
    if container is None or rel is None:
        return True
    if scope_excludes(scope, container, rel):
        return False
    if scope.include_all:
        return True
    tables: set[str] | None = None
    found = False
    for item in scope.include:
        if ".".join(item.path) != container:
            continue
        found = True
        if not item.tables:
            return True
        tables = (tables or set()) | {t.lower() for t in item.tables}
    return found and tables is not None and rel.lower() in tables


def _covered(scope: SyncScope, ds_type: str, datasource_key: str, urn: str) -> bool:
    return scope_allows(scope, *split_of(ds_type, datasource_key, urn))


async def _synced_datasets(session: AsyncSession, ds: Datasource) -> list[Dataset]:
    """수집으로 들어온 활성 데이터셋."""
    return list((await session.execute(
        select(Dataset).where(Dataset.datasource_id == ds.id, Dataset.status == "active",
                              Dataset.is_synced == "true")
    )).scalars().all())


async def preview_scope(session: AsyncSession, datasource_id: int, scope: SyncScope | None) -> SyncScopePreview:
    """새 범위를 저장하면 범위 밖에 남는 수집 데이터셋 수(저장 확인 대화상자용)."""
    ds = await _get_datasource(session, datasource_id)
    synced = await _synced_datasets(session, ds)
    if scope is None:
        return SyncScopePreview(outside_count=0, in_scope_count=len(synced))
    outside = [d for d in synced if not _covered(scope, ds.type, ds.datasource_id, d.urn)]
    # 범위 밖이 된 곳 — 컨테이너가 통째로 빠졌으면 컨테이너, 일부 테이블만 빠졌으면(고르지 않음·제외 패턴) "컨테이너.테이블"
    included = {".".join(i.path) for i in scope.include}
    labels: set[str] = set()
    for d in outside:
        container, rel = split_of(ds.type, ds.datasource_id, d.urn)
        if container is None:
            continue
        labels.add(f"{container}.{rel}" if scope.include_all or container in included else container)
    return SyncScopePreview(outside_count=len(outside), outside_containers=sorted(labels)[:20],
                            in_scope_count=len(synced) - len(outside))


# ---------------------------------------------------------------------------
# 원본 탐색
# ---------------------------------------------------------------------------

def _table_item(container: list[str], table: str) -> dict:
    return {"name": table, "path": [*container, table], "kind": "table", "container": container, "table": table}


async def _browse_builtin(ds: Datasource, config: dict, path: list[str]) -> list[dict]:
    """mysql: 데이터베이스, postgresql: (접속 DB 의) 스키마. path 가 [컨테이너] 면 그 안의 테이블·뷰."""
    if len(path) > 1:
        return []
    from app.catalog.service import connection_ssl, validate_connection_target
    from app.catalog.sync import SYSTEM_DATABASES, SYSTEM_SCHEMAS_PG

    pg = ds.type == "postgresql"
    try:
        host, port = validate_connection_target(config, 5432 if pg else 3306)
    except ValueError as e:
        raise SyncRunError(str(e), 400) from None
    user, password = config.get("username", ""), config.get("password", "")
    ssl = connection_ssl(config, pg=pg)
    try:
        if pg:
            import asyncpg

            conn = await asyncpg.connect(host=host, port=port, user=user, password=password,
                                         database=config.get("database") or "postgres",
                                         timeout=10, command_timeout=20, ssl=ssl)
            try:
                if path:
                    rows = await conn.fetch(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = $1 AND table_type IN ('BASE TABLE', 'VIEW')", path[0])
                    return [_table_item(path, r["table_name"]) for r in rows]
                rows = await conn.fetch("SELECT schema_name FROM information_schema.schemata")
            finally:
                await conn.close()
            names = [r["schema_name"] for r in rows if not r["schema_name"].startswith("pg_temp")
                     and not r["schema_name"].startswith("pg_toast_temp")]
            return [{"name": n, "path": [n], "system": n in SYSTEM_SCHEMAS_PG, "expandable": True} for n in names]
        import aiomysql

        conn = await asyncio.wait_for(aiomysql.connect(
            host=host, port=port, user=user, password=password, db="information_schema",
            connect_timeout=10, ssl=ssl), timeout=15)
        try:
            async with conn.cursor() as cur:
                if path:
                    await cur.execute("SELECT TABLE_NAME FROM TABLES WHERE TABLE_SCHEMA = %s", (path[0],))
                    return [_table_item(path, r[0]) for r in await cur.fetchall()]
                await cur.execute("SELECT SCHEMA_NAME FROM SCHEMATA")
                rows = await cur.fetchall()
        finally:
            conn.close()
        return [{"name": r[0], "path": [r[0]], "system": r[0].lower() in SYSTEM_DATABASES, "expandable": True}
                for r in rows]
    except SyncRunError:
        raise
    except Exception as e:  # noqa: BLE001 — 원인은 로그, 응답은 분류만
        logger.warning("내장 탐색 실패: datasource=%s type=%s err=%r", ds.datasource_id, ds.type, e)
        raise SyncRunError(f"원본에 접속해 목록을 읽지 못했습니다 ({type(e).__name__}).", 502) from None


async def _browse_remote(ds: Datasource, config: dict, path: list[str]) -> list[dict]:
    url = f"{settings.metadata_sync_base_url.rstrip('/')}/v2/browse"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=5.0)) as client:
            resp = await client.post(url, json={"type": ds.type, "config": config, "path": path},
                                     headers=_sync_headers())
    except httpx.HTTPError as e:
        logger.warning("metadata-sync 탐색 호출 실패: datasource=%s err=%r", ds.datasource_id, e)
        raise SyncRunError("수집 서비스(metadata-sync)에 연결할 수 없습니다.", 503) from None
    if resp.status_code == 200:
        return resp.json().get("items") or []
    if resp.status_code in (401, 403):
        raise SyncRunError("수집 서비스 인증에 실패했습니다(metadata_sync.api_token 설정 확인).", 502)
    raise SyncRunError(_detail(resp), 400 if resp.status_code == 422 else 502)


async def browse(session: AsyncSession, datasource_id: int, path: list[str], q: str | None = None,
                 cursor: int = 0, limit: int = BROWSE_PAGE) -> SyncBrowseResponse:
    """원본에서 ``path`` 바로 아래 컨테이너·테이블을 읽고, 이미 수집된(활성)·제거된 데이터셋 수를 붙인다."""
    ds = await _get_datasource(session, datasource_id)
    executor = executor_for(ds.type)
    if executor is None:
        raise SyncRunError(f"이 유형({ds.type})은 아직 화면 수집을 지원하지 않습니다.", 400)
    if ds.type in NO_CONTAINER_TYPES:
        raise SyncRunError("이 유형은 아직 전체 수집만 지원합니다.", 400)
    config = await _load_config(session, ds)
    raw = await (_browse_builtin(ds, config, path) if executor == "BUILTIN" else _browse_remote(ds, config, path))

    # 컨테이너별·테이블별 이미 수집된(활성)·제거된 데이터셋 수
    counts: dict[str, list[int]] = {}
    tables: dict[tuple[str, str], list[int]] = {}
    rows = (await session.execute(
        select(Dataset.urn, Dataset.status).where(Dataset.datasource_id == ds.id, Dataset.is_synced == "true")
    )).all()
    for urn, status in rows:
        c, rel = split_of(ds.type, ds.datasource_id, urn)
        if c is not None:
            idx = 0 if status == "active" else 1
            counts.setdefault(c, [0, 0])[idx] += 1
            tables.setdefault((c, (rel or "").lower()), [0, 0])[idx] += 1

    needle = (q or "").strip().lower()
    items = [i for i in raw if not needle or needle in str(i.get("name", "")).lower()]
    total = len(items)
    page = items[cursor:cursor + limit]
    out = []
    for i in page:
        kind = i.get("kind") or "container"
        if kind == "table":
            container = i.get("container") or i["path"][:-1]
            collected, removed = tables.get((".".join(container), str(i.get("table") or i["name"]).lower()), [0, 0])
        elif kind == "group":
            container = None
            collected, removed = 0, 0
        else:
            container = None
            collected, removed = counts.get(".".join(i["path"]), [0, 0])
        out.append(SyncBrowseItem(name=i["name"], path=i["path"], system=bool(i.get("system")),
                                  expandable=bool(i.get("expandable")), kind=kind,
                                  container=container if kind == "table" else None,
                                  table=(i.get("table") or i["name"]) if kind == "table" else None,
                                  collected=collected, removed=removed))
    nxt = cursor + limit if cursor + limit < total else None
    logger.debug("탐색: datasource=%s path=%s total=%d", ds.datasource_id, path, total)
    return SyncBrowseResponse(path=path, items=out, total=total, next_cursor=nxt)


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

def _response(run: DatasourceSyncRun, ds: Datasource | None, now: datetime | None = None) -> SyncRunResponse:
    now = now or _utcnow()
    silent = None
    if run.status in ACTIVE:
        last = _aware(run.heartbeat_at) or _aware(run.queued_at)
        if last:
            silent = max(0, int((now - last).total_seconds()))
    return SyncRunResponse(
        id=run.id, datasource_id=run.datasource_id, datasource_key=ds.datasource_id if ds else None,
        datasource_name=ds.name if ds else None, datasource_type=ds.type if ds else None,
        trigger_type=run.trigger_type, executor=run.executor, scope=_scope_from_json(run.scope_json),
        status=run.status, phase=run.phase, current_target=run.current_target,
        progress_done=run.progress_done or 0, progress_total=run.progress_total,
        created_count=run.created_count or 0, updated_count=run.updated_count or 0,
        removed_count=run.removed_count or 0, failed_count=run.failed_count or 0,
        items_total=run.items_total or 0, error_message=run.error_message,
        requested_by=run.requested_by, cancel_requested_by=run.cancel_requested_by,
        queued_at=run.queued_at, started_at=run.started_at, finished_at=run.finished_at,
        heartbeat_at=run.heartbeat_at, silent_seconds=silent,
    )


# 예전 동기 API(POST /catalog/datasources/{id}/sync)의 데이터 소스별 잠금. 화면 수집(실행 행)과 서로
# 확인해 같은 데이터 소스를 두 경로로 동시에 수집하지 않게 한다(DEF-070). 단일 프로세스 기준. 키는 내부 PK.
legacy_sync_locks: dict[int, asyncio.Lock] = {}


def legacy_sync_active(datasource_id: int) -> bool:
    """예전 동기 API 로 이 데이터 소스를 동기화하는 중인지."""
    lock = legacy_sync_locks.get(datasource_id)
    return lock is not None and lock.locked()


async def active_run_id(session: AsyncSession, datasource_id: int) -> int | None:
    """데이터 소스의 대기·실행 중 실행 id(없으면 None). 응답이 끊긴 실행은 먼저 실패 처리한다(영원히 막히지 않게)."""
    await finalize_stale(session, datasource_id)
    row = (await session.execute(
        select(DatasourceSyncRun.id).where(
            DatasourceSyncRun.datasource_id == datasource_id, DatasourceSyncRun.status.in_(ACTIVE))
        .order_by(DatasourceSyncRun.id)
    )).first()
    return row[0] if row else None

async def finalize_stale(session: AsyncSession, datasource_id: int | None = None) -> int:
    """5분 넘게 보고가 없는 실행 중·대기 실행을 FAILED 로 바꾼다(SYNC_FAILED 알림 포함). 바꾼 수."""
    cutoff = _utcnow() - SILENT_FAIL
    q = select(DatasourceSyncRun).where(DatasourceSyncRun.status.in_(ACTIVE))
    if datasource_id is not None:
        q = q.where(DatasourceSyncRun.datasource_id == datasource_id)
    stale: list[DatasourceSyncRun] = []
    for run in (await session.execute(q)).scalars().all():
        last = _aware(run.heartbeat_at) or _aware(run.queued_at)
        if last and last < cutoff:
            run.status, run.phase, run.finished_at = "FAILED", "DONE", _utcnow()
            run.error_message = STALE_MESSAGE
            stale.append(run)
            logger.warning("수집 실행 응답 없음으로 실패 처리: run=%d datasource_id=%d", run.id, run.datasource_id)
    if stale:
        await session.commit()
        for run in stale:
            await _after_terminal(session, run)
    return len(stale)


async def list_runs(session: AsyncSession, datasource_id: int | None = None, status: str | None = None,
                    limit: int = 20, since: datetime | None = None, cursor: int = 0) -> list[SyncRunResponse]:
    """실행 목록(최신순). ``status``: 상태 하나, ``ACTIVE``(대기·실행 중), ``PROBLEM``(실패·일부 실패)."""
    await finalize_stale(session, datasource_id)
    q = select(DatasourceSyncRun, Datasource).join(Datasource, Datasource.id == DatasourceSyncRun.datasource_id)
    if datasource_id is not None:
        q = q.where(DatasourceSyncRun.datasource_id == datasource_id)
    if status == "ACTIVE":
        q = q.where(DatasourceSyncRun.status.in_(ACTIVE))
    elif status == "PROBLEM":
        q = q.where(DatasourceSyncRun.status.in_(("FAILED", "PARTIAL")))
    elif status:
        q = q.where(DatasourceSyncRun.status == status)
    if since is not None:
        q = q.where(DatasourceSyncRun.queued_at >= since)
    q = q.order_by(DatasourceSyncRun.id.desc()).offset(max(0, cursor)).limit(max(1, min(limit, 200)))
    now = _utcnow()
    return [_response(run, ds, now) for run, ds in (await session.execute(q)).all()]


async def get_run(session: AsyncSession, run_id: int) -> SyncRunResponse:
    run = await session.get(DatasourceSyncRun, run_id)
    if run is None:
        raise SyncRunError("수집 실행을 찾을 수 없습니다.", 404)
    await finalize_stale(session, run.datasource_id)
    await session.refresh(run)
    return _response(run, await session.get(Datasource, run.datasource_id))


async def list_items(session: AsyncSession, run_id: int, limit: int = 200, level: str | None = None,
                     q: str | None = None) -> list[DatasourceSyncRunItem]:
    if await session.get(DatasourceSyncRun, run_id) is None:
        raise SyncRunError("수집 실행을 찾을 수 없습니다.", 404)
    stmt = select(DatasourceSyncRunItem).where(DatasourceSyncRunItem.run_id == run_id)
    if level:
        stmt = stmt.where(DatasourceSyncRunItem.level == level)
    if q and q.strip():
        from app.core.sql import LIKE_ESCAPE, like_contains

        pat = like_contains(q.strip())
        stmt = stmt.where(DatasourceSyncRunItem.message.ilike(pat, escape=LIKE_ESCAPE)
                          | DatasourceSyncRunItem.target.ilike(pat, escape=LIKE_ESCAPE))
    stmt = stmt.order_by(DatasourceSyncRunItem.id).limit(max(1, min(limit, MAX_ITEMS)))
    return list((await session.execute(stmt)).scalars().all())


async def run_summary(session: AsyncSession) -> SyncRunSummary:
    await finalize_stale(session)
    since = _utcnow() - timedelta(hours=24)

    async def _count(*conds) -> int:
        return int(await session.scalar(select(func.count()).select_from(DatasourceSyncRun).where(*conds)) or 0)

    return SyncRunSummary(
        active=await _count(DatasourceSyncRun.status.in_(ACTIVE)),
        failed_24h=await _count(DatasourceSyncRun.status.in_(("FAILED", "PARTIAL")),
                                DatasourceSyncRun.finished_at >= since),
        succeeded_24h=await _count(DatasourceSyncRun.status == "SUCCEEDED", DatasourceSyncRun.finished_at >= since),
    )


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

async def _load_config(session: AsyncSession, ds: Datasource) -> dict:
    from app.catalog.datasource_secrets import DatasourceSecretError, unseal_config

    raw = (await session.execute(
        select(DatasourceConfiguration.config_json).where(DatasourceConfiguration.datasource_id == ds.id)
    )).scalar_one_or_none()
    if not raw:
        raise SyncRunError("접속 설정이 없습니다 — 연결 설정을 먼저 저장하세요.", 400)
    try:
        return unseal_config(raw)
    except DatasourceSecretError as e:
        raise SyncRunError(str(e), 400) from None


async def start_run(session: AsyncSession, datasource_id: int, scope_override: SyncScope | None,
                    username: str | None, trigger_type: str = "MANUAL") -> SyncRunResponse:
    """수집을 시작한다 — 실행 행을 만들고 백그라운드로 넘긴 뒤 바로 반환한다."""
    ds = await _get_datasource(session, datasource_id)
    executor = executor_for(ds.type)
    if executor is None:
        raise SyncRunError(f"이 유형({ds.type})은 아직 화면 수집을 지원하지 않습니다.", 400)
    if scope_override is not None:
        scope = scope_override
    else:
        row = await session.get(DatasourceSyncSetting, datasource_id)
        scope = _scope_from_json(row.scope_json) if row else None
    if scope is None:
        raise SyncRunError("수집 범위를 먼저 선택하세요.", 400)
    _check_scope_for_type(ds.type, scope)
    if await _engine_sync_catalog_missing(session, ds):
        raise SyncRunError("쿼리 엔진은 카탈로그에 '수집'으로 연결해야 엔진을 통해 수집할 수 있습니다.", 409)
    config = await _load_config(session, ds)

    active = await active_run_id(session, datasource_id)
    if active:
        raise SyncRunError(f"이 데이터 소스는 이미 수집 중입니다(실행 #{active}).", 409)
    if legacy_sync_active(datasource_id):
        raise SyncRunError("이 데이터 소스는 예전 동기화 API 로 동기화 중입니다. 끝난 뒤 다시 수집하세요.", 409)

    run = DatasourceSyncRun(
        datasource_id=datasource_id, trigger_type=trigger_type, executor=executor,
        scope_json=scope.model_dump_json(exclude_none=True), status="QUEUED", requested_by=username,
        queued_at=_utcnow(), heartbeat_at=_utcnow(),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    logger.info("수집 시작: run=%d datasource=%s type=%s executor=%s by=%s",
                run.id, ds.datasource_id, ds.type, executor, username)

    if executor == "BUILTIN":
        task = asyncio.create_task(_run_builtin(run.id))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    else:
        await _dispatch_job(session, run, ds, config, scope)
    await session.refresh(run)
    return _response(run, ds)


async def _fail(session: AsyncSession, run: DatasourceSyncRun, message: str) -> None:
    run.status, run.phase, run.finished_at, run.error_message = "FAILED", "DONE", _utcnow(), message[:2000]
    await session.commit()
    await _after_terminal(session, run)


async def _resolve_lineage_after_sync(session: AsyncSession, datasource_id: int) -> None:
    """수집이 만든 데이터셋으로 쿼리 리니지의 미해석 행을 채운다(TODO-033). 오류는 경고만."""
    try:
        from app.catalog.catalogs_service import sync_catalog_id
        from app.catalog.lineage_resolve import resolve_query_lineage_for_catalog
        await resolve_query_lineage_for_catalog(session, await sync_catalog_id(session, datasource_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("수집 후 리니지 해석 실패: datasource id=%d err=%s", datasource_id, e)
        await session.rollback()


async def _after_terminal(session: AsyncSession, run: DatasourceSyncRun) -> None:
    """실행이 끝난 뒤 처리 — 데이터셋을 만들거나 갱신했으면 쿼리 리니지 해석, 실패·일부 실패면
    ``SYNC_FAILED`` 알림. 이 처리의 오류가 수집 결과 기록을 막지 않는다.

    리니지 해석은 맨 뒤에 한다 — 해석 실패 시 롤백이 ``run`` 을 만료시키므로, 그 뒤에 ``run`` 을 쓰는
    호출자는 다시 읽어야 한다(``apply_report``).
    """
    resolve_ds = run.datasource_id if (
        run.status in ("SUCCEEDED", "PARTIAL") and (run.created_count or run.updated_count)) else None
    await _alert_failed_run(session, run)
    if resolve_ds is not None:
        await _resolve_lineage_after_sync(session, resolve_ds)


async def _alert_failed_run(session: AsyncSession, run: DatasourceSyncRun) -> None:
    """실패·일부 실패 실행이면 ``SYNC_FAILED`` 알림. 알림 오류는 로그만."""
    if run.status not in ("FAILED", "PARTIAL"):
        return
    try:
        from app.alert.service import evaluate_sync_alerts

        ds = await session.get(Datasource, run.datasource_id)
        name = ds.name if ds else f"데이터 소스 #{run.datasource_id}"
        if run.status == "FAILED":
            summary = f"수집 실패 (실행 #{run.id}) — {(run.error_message or '원인 미상')[:200]}"
        else:
            summary = f"수집 일부 실패 (실행 #{run.id}) — 실패 {run.failed_count or 0}건"
        await evaluate_sync_alerts(
            session, trigger_type="SYNC_FAILED", datasource_id=run.datasource_id, datasource_name=name,
            summary=summary, partial=run.status == "PARTIAL",
            severity="BREAKING" if run.status == "FAILED" else "WARNING",
            detail={"run_id": run.id, "status": run.status, "failed": run.failed_count,
                    "error_message": run.error_message, "datasource_id": run.datasource_id},
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("SYNC_FAILED 알림 처리 실패: run=%d", run.id)
        await session.rollback()


async def _dispatch_job(session: AsyncSession, run: DatasourceSyncRun, ds: Datasource, config: dict,
                        scope: SyncScope) -> None:
    body = {"job_id": run.id, "type": ds.type, "datasource_key": ds.datasource_id,
            "config": config, "scope": scope.model_dump(exclude_none=True)}
    url = f"{settings.metadata_sync_base_url.rstrip('/')}/v2/jobs"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=body, headers=_sync_headers())
    except httpx.HTTPError as e:
        logger.warning("metadata-sync 작업 전송 실패: run=%d err=%r", run.id, e)
        await _fail(session, run, "수집 서비스(metadata-sync)에 연결할 수 없습니다.")
        raise SyncRunError("수집 서비스(metadata-sync)에 연결할 수 없습니다.", 503) from None
    if resp.status_code == 202:
        return
    detail = _detail(resp)
    logger.warning("metadata-sync 작업 거절: run=%d status=%d detail=%s", run.id, resp.status_code, detail)
    await _fail(session, run, detail)
    if resp.status_code in (401, 403):
        raise SyncRunError("수집 서비스 인증에 실패했습니다(metadata_sync.api_token 설정 확인).", 502)
    raise SyncRunError(detail, 409 if resp.status_code == 409 else 400)


def _detail(resp: httpx.Response) -> str:
    try:
        d = resp.json().get("detail")
        if isinstance(d, str):
            return d
    except Exception:  # noqa: BLE001
        pass
    return f"수집 서비스 오류(HTTP {resp.status_code})"


async def cancel_run(session: AsyncSession, run_id: int, username: str | None) -> SyncRunResponse:
    run = await session.get(DatasourceSyncRun, run_id)
    if run is None:
        raise SyncRunError("수집 실행을 찾을 수 없습니다.", 404)
    if run.status in TERMINAL:
        raise SyncRunError("이미 끝난 실행입니다.", 409)
    run.cancel_requested_by = username
    await session.commit()
    logger.info("수집 취소 요청: run=%d by=%s executor=%s", run_id, username, run.executor)
    if run.executor == "METADATA_SYNC":
        url = f"{settings.metadata_sync_base_url.rstrip('/')}/v2/jobs/{run_id}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.delete(url, headers=_sync_headers())
            if resp.status_code == 404:
                # 서비스가 작업을 모름(재시작 등) — 여기서 바로 끝낸다
                run.status, run.phase, run.finished_at = "CANCELLED", "DONE", _utcnow()
                run.error_message = "중지되었습니다(수집 서비스에 작업이 없음)."
                await session.commit()
        except httpx.HTTPError as e:
            logger.warning("metadata-sync 취소 전송 실패(응답 없음 판정에 맡김): run=%d err=%r", run_id, e)
    return _response(run, await session.get(Datasource, run.datasource_id))


# ---------------------------------------------------------------------------
# 진행 보고 반영 (metadata-sync → 백엔드)
# ---------------------------------------------------------------------------

async def apply_report(session: AsyncSession, run_id: int, report: SyncRunReport) -> SyncRunResponse:
    run = await session.get(DatasourceSyncRun, run_id)
    if run is None:
        raise SyncRunError("수집 실행을 찾을 수 없습니다.", 404)
    if run.status in TERMINAL and not (run.status == "FAILED" and run.error_message == STALE_MESSAGE):
        # 이미 끝난 실행 — 늦게 온 보고는 무시(응답 없음으로 실패 처리한 뒤의 최종 보고만 받아들인다)
        logger.debug("끝난 실행의 보고 무시: run=%d status=%s report=%s", run_id, run.status, report.status)
        return _response(run, await session.get(Datasource, run.datasource_id))
    run.status = report.status
    run.phase = report.phase
    run.current_target = (report.current_target or None) and report.current_target[:500]
    run.progress_done = report.progress_done
    run.progress_total = report.progress_total
    run.created_count, run.updated_count = report.created, report.updated
    run.removed_count, run.failed_count = report.removed, report.failed
    if report.status in TERMINAL:
        run.error_message = report.error_message
    if report.started_at and not run.started_at:
        run.started_at = report.started_at
    run.heartbeat_at = _utcnow()
    if report.status in TERMINAL:
        run.finished_at = report.finished_at or _utcnow()
        await session.execute(delete(DatasourceSyncRunItem).where(DatasourceSyncRunItem.run_id == run_id))
        items = report.items or []
        for it in items[:MAX_ITEMS]:
            session.add(DatasourceSyncRunItem(run_id=run_id, level=it.level, target=it.target, message=it.message))
        run.items_total = report.items_total if report.items_total is not None else len(items)
        logger.info("수집 종료 보고: run=%d status=%s created=%d updated=%d failed=%d removed=%d",
                    run_id, report.status, report.created, report.updated, report.failed, report.removed)
    await session.commit()
    if report.status in TERMINAL:
        await _after_terminal(session, run)
        await session.refresh(run)  # 후처리의 롤백으로 만료됐을 수 있다
    return _response(run, await session.get(Datasource, run.datasource_id))


# ---------------------------------------------------------------------------
# 내장 sync (mysql·postgresql) — 백엔드 안 백그라운드 태스크
# ---------------------------------------------------------------------------

async def _heartbeat(run_id: int, stop: asyncio.Event, session_factory) -> None:
    """내장 sync 는 컨테이너 하나가 오래 걸릴 수 있어 30초마다 따로 살아 있음을 기록한다."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            async with session_factory() as s:
                await s.execute(update(DatasourceSyncRun).where(DatasourceSyncRun.id == run_id)
                                .values(heartbeat_at=_utcnow()))
                await s.commit()


async def _run_builtin(run_id: int, session_factory=None) -> None:
    """컨테이너(MySQL DB / PostgreSQL 스키마)마다 내장 sync 를 차례로 돌린다. 진행 단위는 컨테이너."""
    from app.catalog.sync import sync_datasource_metadata

    if session_factory is None:
        from app.core.database import async_session as session_factory

    stop = asyncio.Event()
    hb = asyncio.create_task(_heartbeat(run_id, stop, session_factory))
    try:
        async with session_factory() as session:
            run = await session.get(DatasourceSyncRun, run_id)
            ds = await session.get(Datasource, run.datasource_id)
            scope = _scope_from_json(run.scope_json)
            containers: list[str | None] = [None] if (scope is None or scope.include_all) else \
                [".".join(i.path) for i in scope.include]
            # 일부 테이블·제외 패턴이 있으면 테이블 단위로 거른다
            table_filter = None
            if scope is not None and (scope.exclude or any(i.tables for i in scope.include)):
                def table_filter(container: str, table: str, _scope=scope) -> bool:
                    return scope_allows(_scope, container, table)
            run.status, run.phase, run.started_at = "RUNNING", "COLLECTING", _utcnow()
            run.progress_total, run.heartbeat_at = len(containers), _utcnow()
            await session.commit()
            errors: list[str] = []
            for idx, container in enumerate(containers):
                await session.refresh(run)
                if run.cancel_requested_by:
                    run.status, run.error_message = "CANCELLED", "중지되었습니다."
                    break
                run.current_target, run.heartbeat_at = container, _utcnow()
                await session.commit()
                try:
                    result = await sync_datasource_metadata(session, ds.datasource_id, container, table_filter)
                except Exception as e:  # noqa: BLE001 — 컨테이너 하나 실패로 전체를 멈추지 않음
                    logger.exception("내장 수집 실패: run=%d container=%s", run_id, container)
                    errors.append(f"{container or '전체'}: {type(e).__name__}: {str(e)[:300]}")
                    await session.rollback()
                    run = await session.get(DatasourceSyncRun, run_id)
                else:
                    run.created_count = (run.created_count or 0) + result.tables_created
                    run.updated_count = (run.updated_count or 0) + result.tables_updated
                    run.removed_count = (run.removed_count or 0) + result.tables_removed
                    errors.extend(f"{container or '전체'}: {e}" for e in result.errors)
                run.progress_done, run.heartbeat_at = idx + 1, _utcnow()
                await session.commit()
            if run.status != "CANCELLED":
                run.status = ("FAILED" if errors and len(errors) >= len(containers) and not
                              (run.created_count or run.updated_count) else "PARTIAL" if errors else "SUCCEEDED")
                if errors:
                    run.error_message = errors[0][:2000]
            run.failed_count = len(errors)
            run.items_total = len(errors)
            for message in errors[:MAX_ITEMS]:
                session.add(DatasourceSyncRunItem(run_id=run_id, level="ERROR", message=message))
            run.phase, run.current_target, run.finished_at = "DONE", None, _utcnow()
            await session.commit()
            logger.info("내장 수집 종료: run=%d status=%s created=%d updated=%d removed=%d errors=%d",
                        run_id, run.status, run.created_count, run.updated_count, run.removed_count, len(errors))
            await _after_terminal(session, run)
    except Exception:  # noqa: BLE001 — 실행 행만은 끝난 상태로 남긴다
        logger.exception("내장 수집 태스크 오류: run=%d", run_id)
        try:
            async with session_factory() as session:
                run = await session.get(DatasourceSyncRun, run_id)
                if run and run.status in ACTIVE:
                    await _fail(session, run, "수집 중 내부 오류가 발생했습니다(서버 로그 확인).")
        except Exception:  # noqa: BLE001
            logger.exception("내장 수집 실패 기록도 실패: run=%d", run_id)
    finally:
        stop.set()
        hb.cancel()


# ---------------------------------------------------------------------------
# 연결 테스트·서비스 상태 (metadata-sync)
# ---------------------------------------------------------------------------

async def test_via_sync_service(ds_type: str, config: dict) -> dict:
    """metadata-sync ``/v2/test`` 로 연결을 확인한다. 실패해도 예외 대신 ``{"ok": False, ...}``."""
    url = f"{settings.metadata_sync_base_url.rstrip('/')}/v2/test"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            resp = await client.post(url, json={"type": ds_type, "config": config}, headers=_sync_headers())
    except httpx.HTTPError as e:
        logger.warning("metadata-sync 연결 테스트 호출 실패: type=%s err=%r", ds_type, e)
        return {"ok": False, "message": "수집 서비스(metadata-sync)에 연결할 수 없습니다."}
    if resp.status_code != 200:
        return {"ok": False, "message": _detail(resp)}
    data = resp.json()
    return {"ok": bool(data.get("ok")), "message": data.get("message") or "", "latency_ms": data.get("latency_ms")}


async def sync_service_status() -> dict:
    url = f"{settings.metadata_sync_base_url.rstrip('/')}/v2/status"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.get(url, headers=_sync_headers())
    except httpx.HTTPError:
        return {"reachable": False, "message": "수집 서비스(metadata-sync)에 연결할 수 없습니다."}
    if resp.status_code != 200:
        return {"reachable": False, "message": _detail(resp)}
    return {"reachable": True, **{k: resp.json().get(k) for k in ("workers", "running", "queued", "version")}}


# ---------------------------------------------------------------------------
# 주기 점검 — 응답 없음 판정, SYNC_STALE 알림, 보관 기간 지난 이력 정리
# ---------------------------------------------------------------------------

MONITOR_TICK_SECONDS = 60
STALE_CHECK_SECONDS = 600
PURGE_SECONDS = 3600


async def evaluate_stale_alerts(session: AsyncSession, now: datetime | None = None) -> int:
    """``SYNC_STALE`` 규칙마다, 예약이 켜진 데이터 소스 중 정해진 시간 안에 성공한 수집이 없는 곳을 알린다(§7.5, 4단계).

    기준 시간은 규칙의 ``stale_hours``, 없으면 예약 주기의 2배(최소 1시간). 성공은 SUCCEEDED·PARTIAL.
    한 번도 성공하지 않았으면 설정 저장 시각부터 잰다. 열린 알림이 있으면 다시 만들지 않는다.
    """
    from app.alert.models import AlertRule
    from app.alert.service import evaluate_sync_alerts

    now = now or _utcnow()
    rules = (await session.execute(
        select(AlertRule).where(AlertRule.is_active == "true", AlertRule.trigger_type == "SYNC_STALE")
    )).scalars().all()
    if not rules:
        return 0
    targets = (await session.execute(
        select(DatasourceSyncSetting, Datasource).join(Datasource, Datasource.id == DatasourceSyncSetting.datasource_id)
        .where(DatasourceSyncSetting.scope_json.is_not(None), DatasourceSyncSetting.schedule_enabled.is_(True),
               DatasourceSyncSetting.schedule_interval_minutes.is_not(None))
    )).all()
    last_ok = dict((await session.execute(
        select(DatasourceSyncRun.datasource_id, func.max(DatasourceSyncRun.finished_at))
        .where(DatasourceSyncRun.status.in_(("SUCCEEDED", "PARTIAL"))).group_by(DatasourceSyncRun.datasource_id)
    )).all())
    created = 0
    for setting, ds in targets:
        since = _aware(last_ok.get(ds.id)) or _aware(setting.updated_at)
        if since is None:
            continue
        idle_hours = (now - since).total_seconds() / 3600
        for rule in rules:
            try:
                configured = json.loads(rule.trigger_config or "{}").get("stale_hours")
                hours = int(configured) if configured is not None else \
                    max(1, math.ceil(setting.schedule_interval_minutes * 2 / 60))
            except (ValueError, TypeError, AttributeError):
                continue
            if idle_hours < hours:
                continue
            if rule.scope_type == "DATASOURCE" and rule.scope_id != ds.id:
                continue
            what = "마지막 성공 수집" if ds.id in last_ok else "예약 설정"
            made = await evaluate_sync_alerts(
                session, trigger_type="SYNC_STALE", datasource_id=ds.id, datasource_name=ds.name,
                summary=f"{hours}시간 넘게 성공한 수집이 없습니다({what} 후 {int(idle_hours)}시간)",
                detail={"stale_hours": hours, "since": since, "datasource_id": ds.id}, rule_id=rule.id,
            )
            created += len(made)
    await session.commit()
    if created:
        logger.info("SYNC_STALE 알림 %d건 생성", created)
    return created


# 예약 시작이 실행 행 없이 실패한 데이터 소스(접속 설정 없음 등) → 다시 시도할 시각. 매 분 같은 실패를 되풀이하지 않게.
_schedule_backoff: dict[int, datetime] = {}


async def run_due_schedules(session: AsyncSession, now: datetime | None = None) -> int:
    """예약 시각이 된 데이터 소스의 수집을 시작한다(trigger=SCHEDULE). 시작한 수를 돌려준다.

    다음 시각은 마지막 실행 접수(수동 포함) + 주기 — 수동으로 돌리면 예약도 그만큼 미뤄진다.
    이미 수집 중이면 건너뛰고 다음 점검에서 다시 본다. 단일 프로세스 기준(다중 워커면 실행 중 409 로 한 번만 돈다).
    """
    now = now or _utcnow()
    rows = (await session.execute(
        select(DatasourceSyncSetting).where(
            DatasourceSyncSetting.schedule_enabled.is_(True), DatasourceSyncSetting.scope_json.is_not(None),
            DatasourceSyncSetting.schedule_interval_minutes.is_not(None))
    )).scalars().all()
    if not rows:
        return 0
    last = dict((await session.execute(
        select(DatasourceSyncRun.datasource_id, func.max(DatasourceSyncRun.queued_at))
        .group_by(DatasourceSyncRun.datasource_id)
    )).all())
    started = 0
    for row in rows:
        ds_id = row.datasource_id
        due = _next_scheduled(row, _aware(last.get(ds_id)))
        if due is None or due > now or _schedule_backoff.get(ds_id, now) > now:
            continue
        try:
            await start_run(session, ds_id, None, "scheduler", trigger_type="SCHEDULE")
            started += 1
            _schedule_backoff.pop(ds_id, None)
        except SyncRunError as e:
            if e.status_code == 409:
                continue  # 수집 중 — 다음 점검에서
            # 503(수집 서비스 없음)은 실패 실행이 남아 다음 시각이 밀린다. 그 밖(설정 없음 등)은 주기만큼 쉬었다 다시.
            _schedule_backoff[ds_id] = now + timedelta(minutes=row.schedule_interval_minutes)
            logger.warning("예약 수집 시작 실패: datasource_id=%d: %s", ds_id, e)
        except Exception as e:  # noqa: BLE001 — 한 데이터 소스 실패가 나머지를 막지 않게
            await session.rollback()
            _schedule_backoff[ds_id] = now + timedelta(minutes=row.schedule_interval_minutes)
            logger.warning("예약 수집 시작 오류: datasource_id=%d: %s", ds_id, e)
    if started:
        logger.info("예약 수집 %d건 시작", started)
    return started


async def purge_old_runs(session: AsyncSession, retention_days: int | None = None) -> int:
    """보관 기간이 지난 끝난 실행(과 항목, FK CASCADE)을 지운다."""
    days = retention_days or settings.sync_run_retention_days
    cutoff = _utcnow() - timedelta(days=max(1, days))
    ids = [r for (r,) in (await session.execute(
        select(DatasourceSyncRun.id).where(DatasourceSyncRun.status.in_(tuple(TERMINAL)),
                                           DatasourceSyncRun.queued_at < cutoff)
    )).all()]
    if not ids:
        return 0
    # SQLite 등 FK CASCADE 가 꺼진 환경도 고려해 항목을 먼저 지운다
    await session.execute(delete(DatasourceSyncRunItem).where(DatasourceSyncRunItem.run_id.in_(ids)))
    await session.execute(delete(DatasourceSyncRun).where(DatasourceSyncRun.id.in_(ids)))
    await session.commit()
    logger.info("수집 실행 이력 정리: %d건 (보관 %d일)", len(ids), days)
    return len(ids)


async def sync_monitor_loop() -> None:
    """lifespan 에서 기동 — 1분마다 응답 없음 판정·예약 수집, 10분마다 SYNC_STALE, 1시간마다 이력 정리."""
    from app.core.database import async_session

    logger.info("수집 실행 점검 루프 시작 (tick=%ds)", MONITOR_TICK_SECONDS)
    last_stale = last_purge = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            async with async_session() as session:
                await finalize_stale(session)
                await run_due_schedules(session)
                now = loop.time()
                if now - last_stale >= STALE_CHECK_SECONDS:
                    last_stale = now
                    await evaluate_stale_alerts(session)
                if now - last_purge >= PURGE_SECONDS:
                    last_purge = now
                    await purge_old_runs(session)
        except asyncio.CancelledError:
            logger.info("수집 실행 점검 루프 중지됨")
            raise
        except Exception as e:  # noqa: BLE001 — 루프 보호
            logger.warning("수집 실행 점검 실패: %s", e)
        await asyncio.sleep(MONITOR_TICK_SECONDS)
