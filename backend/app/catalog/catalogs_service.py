# SPDX-License-Identifier: Apache-2.0
"""카탈로그 엔티티 서비스 (1·2단계 — design/catalog-entity-design.md).

카탈로그는 데이터셋을 묶는 거버넌스 단위이고, 데이터 소스는 접속 정보다. 1단계에서는
데이터 소스마다 카탈로그를 하나씩 자동으로 만들어(SYNC 연결) 기존 동작을 그대로 유지한다.

- 데이터 소스 생성 → ``ensure_datasource_catalog`` 가 자동 카탈로그와 SYNC 연결을 만든다.
- 데이터셋 생성 → ``sync_catalog_id`` 로 데이터 소스의 SYNC 카탈로그를 ``catalog_id`` 에 채운다.
- 데이터 소스 이름 변경 → 자동 카탈로그 이름도 따라간다.
- 데이터 소스 삭제 → 남은 데이터셋이 없는 자동 카탈로그를 함께 지운다.

기존 설치의 데이터는 스키마 DDL(``argus-catalog-*.sql``)의 백필 구문이 같은 규칙으로 채운다.

2단계: 데이터 소스에 역할(CATALOG / DATABASE / ENGINE, ``datasource_metadata.datasource_role``)이 생겼다.
엔진(Impala·Trino)은 카탈로그를 자동으로 만들지 않고 기존 카탈로그에 QUERY 로 연결한다. 엔진을
통해서만 닿는 카탈로그는 엔진을 SYNC 로 연결해 엔진 경유로 수집한다. SYNC 연결이 없는 엔진은
데이터셋을 만들 수 없다(``ensure_can_own_datasets`` → 409).
"""

from __future__ import annotations

import logging
import secrets
import time

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.datasource_metadata import QUERY_CAPABLE_TYPES, datasource_role
from app.catalog.models import Catalog, CatalogConnection, Dataset, Datasource
from app.catalog.schemas import (
    CatalogConnectionCreate,
    CatalogConnectionResponse,
    CatalogCreate,
    CatalogResponse,
    CatalogUpdate,
    EngineCatalogEntry,
    EngineCatalogsResponse,
)

logger = logging.getLogger(__name__)

ROLE_SYNC = "SYNC"
ROLE_QUERY = "QUERY"


class CatalogNotFoundError(LookupError):
    """카탈로그·데이터 소스·연결이 없음 → 라우터가 404 로 응답."""


class CatalogConflictError(Exception):
    """현재 상태와 맞지 않는 요청 → 라우터가 409 로 응답."""


# ---------------------------------------------------------------------------
# 데이터 소스 수명주기 훅 (service.py 가 같은 트랜잭션 안에서 호출, 커밋은 호출자)
# ---------------------------------------------------------------------------

async def ensure_datasource_catalog(session: AsyncSession, datasource: Datasource) -> Catalog:
    """데이터 소스의 SYNC 카탈로그를 돌려준다. 없으면 자동 카탈로그와 SYNC 연결을 만든다(멱등).

    자동 카탈로그의 ``catalog_key`` 는 데이터 소스의 ``datasource_id`` 문자열이다 — 카탈로그 기준
    URN 이 지금의 데이터 소스 기준 URN 과 같아지도록.
    """
    existing = await _sync_catalog(session, datasource.id)
    if existing is not None:
        return existing
    catalog = (await session.execute(
        select(Catalog).where(Catalog.catalog_key == datasource.datasource_id)
    )).scalars().first()
    if catalog is None:
        catalog = Catalog(
            catalog_key=datasource.datasource_id,
            name=datasource.name,
            kind=datasource.type,
            source_type="EXTERNAL",
            auto_created=True,
            status="active",
        )
        session.add(catalog)
        await session.flush()
    session.add(CatalogConnection(
        catalog_id=catalog.id, datasource_id=datasource.id, role=ROLE_SYNC,
    ))
    await session.flush()
    logger.info("카탈로그 자동 생성/연결: %s (catalog id=%d, datasource id=%d)",
                catalog.catalog_key, catalog.id, datasource.id)
    return catalog


def creates_own_catalog(datasource_type: str | None) -> bool:
    """데이터 소스 생성 시 자동 카탈로그를 만드는 유형인지 — 엔진은 만들지 않는다."""
    return datasource_role(datasource_type) != "ENGINE"


async def ensure_can_own_datasets(session: AsyncSession, datasource: Datasource) -> None:
    """엔진 데이터 소스는 SYNC 연결(엔진 경유 수집)이 있어야 데이터셋을 가질 수 있다."""
    if datasource_role(datasource.type) != "ENGINE":
        return
    if await _sync_catalog(session, datasource.id) is None:
        raise CatalogConflictError(
            f"엔진 데이터 소스({datasource.name})는 자체 카탈로그가 없습니다. 카탈로그에 SYNC 로 "
            "연결해야 이 엔진을 통해 메타데이터를 수집할 수 있습니다.")


async def sync_catalog_id(session: AsyncSession, datasource_id: int) -> int | None:
    """데이터 소스의 SYNC 카탈로그 ID. 연결이 없으면 None(데이터셋 catalog_id 는 NULL 로 남는다)."""
    catalog = await _sync_catalog(session, datasource_id)
    return catalog.id if catalog is not None else None


async def rename_auto_catalog(session: AsyncSession, datasource: Datasource) -> None:
    """데이터 소스 이름이 바뀌면 그 데이터 소스에서 자동 생성된 카탈로그 이름도 맞춘다."""
    catalog = await _sync_catalog(session, datasource.id)
    if catalog is not None and catalog.auto_created and catalog.name != datasource.name:
        catalog.name = datasource.name


async def cleanup_datasource_catalogs(session: AsyncSession, datasource_id: int) -> int:
    """데이터 소스 삭제 직전에 호출. 이 데이터 소스에서 자동 생성됐고 남은 데이터셋이 없는 카탈로그를 지운다.

    다른 데이터 소스가 연결돼 있거나 데이터셋이 남은 카탈로그는 두고, 연결 행만 FK CASCADE 로
    사라지게 한다. 지운 카탈로그 수를 돌려준다.
    """
    catalog_ids = list((await session.execute(
        select(CatalogConnection.catalog_id)
        .join(Catalog, Catalog.id == CatalogConnection.catalog_id)
        .where(CatalogConnection.datasource_id == datasource_id,
               CatalogConnection.role == ROLE_SYNC,
               Catalog.auto_created.is_(True))
    )).scalars().all())
    removed = 0
    for cid in catalog_ids:
        others = await session.scalar(
            select(func.count()).select_from(CatalogConnection)
            .where(CatalogConnection.catalog_id == cid,
                   CatalogConnection.datasource_id != datasource_id))
        remaining = await session.scalar(
            select(func.count()).select_from(Dataset)
            .where(Dataset.catalog_id == cid, Dataset.datasource_id != datasource_id))
        if others or remaining:
            continue
        await session.execute(delete(CatalogConnection).where(CatalogConnection.catalog_id == cid))
        await session.execute(delete(Catalog).where(Catalog.id == cid))
        removed += 1
    if removed:
        logger.info("데이터 소스 id=%d 삭제로 자동 카탈로그 %d개 정리", datasource_id, removed)
    return removed


async def _sync_catalog(session: AsyncSession, datasource_id: int) -> Catalog | None:
    # 1단계는 데이터 소스당 SYNC 연결이 하나다. 여럿이면 가장 먼저 만든 것을 쓴다.
    return (await session.execute(
        select(Catalog)
        .join(CatalogConnection, CatalogConnection.catalog_id == Catalog.id)
        .where(CatalogConnection.datasource_id == datasource_id,
               CatalogConnection.role == ROLE_SYNC)
        .order_by(CatalogConnection.id)
        .limit(1)
    )).scalars().first()


# ---------------------------------------------------------------------------
# 카탈로그 생성·수정·삭제 (커밋 포함)
# ---------------------------------------------------------------------------

def _new_catalog_key(kind: str | None) -> str:
    # 데이터 소스 datasource_id 와 같은 모양 — "{kind}-{ts_hex}{rand_hex}"
    prefix = "".join(ch for ch in (kind or "catalog").lower() if ch.isalnum() or ch in "-_")[:40] or "catalog"
    return f"{prefix}-{int(time.time() * 1000):x}{secrets.token_hex(3)}"


async def create_catalog(session: AsyncSession, req: CatalogCreate) -> CatalogResponse:
    catalog = Catalog(
        catalog_key=_new_catalog_key(req.kind),
        name=req.name.strip(),
        kind=req.kind,
        source_type=req.source_type,
        auto_created=False,
        summary=req.summary,
        description=req.description,
        owner=req.owner,
        status="active",
    )
    session.add(catalog)
    await session.commit()
    await session.refresh(catalog)  # 서버 기본값(created_at/updated_at) — async 세션은 지연 로딩 불가
    logger.info("카탈로그 생성: %s (id=%d, kind=%s)", catalog.catalog_key, catalog.id, catalog.kind)
    return (await _responses(session, [catalog]))[0]


async def update_catalog(session: AsyncSession, catalog_id: int, req: CatalogUpdate) -> CatalogResponse:
    catalog = await _get_catalog_or_404(session, catalog_id)
    if req.name is not None:
        if catalog.auto_created:
            raise CatalogConflictError("데이터 소스와 함께 만들어진 카탈로그의 이름은 데이터 소스 이름을 따릅니다.")
        catalog.name = req.name.strip()
    for field in ("summary", "description", "owner"):
        value = getattr(req, field)
        if value is not None:
            setattr(catalog, field, value)
    await session.commit()
    await session.refresh(catalog)  # onupdate updated_at 은 커밋 후 만료된다
    return (await _responses(session, [catalog]))[0]


async def delete_catalog(session: AsyncSession, catalog_id: int) -> None:
    catalog = await _get_catalog_or_404(session, catalog_id)
    if catalog.auto_created:
        raise CatalogConflictError("데이터 소스와 함께 만들어진 카탈로그는 데이터 소스를 삭제하면 함께 정리됩니다.")
    datasets = await session.scalar(
        select(func.count()).select_from(Dataset).where(Dataset.catalog_id == catalog_id))
    if datasets:
        raise CatalogConflictError(f"카탈로그에 데이터셋이 {datasets}개 있어 삭제할 수 없습니다.")
    await session.execute(delete(CatalogConnection).where(CatalogConnection.catalog_id == catalog_id))
    await session.execute(delete(Catalog).where(Catalog.id == catalog_id))
    await session.commit()
    logger.info("카탈로그 삭제: %s (id=%d)", catalog.catalog_key, catalog_id)


# ---------------------------------------------------------------------------
# 연결 관리 (커밋 포함)
# ---------------------------------------------------------------------------

async def add_connection(
    session: AsyncSession, catalog_id: int, req: CatalogConnectionCreate
) -> CatalogResponse:
    """카탈로그에 데이터 소스를 연결한다.

    - QUERY: 엔진·외부 카탈로그를 붙이는 DB(QUERY_CAPABLE_TYPES)만.
    - SYNC: 카탈로그당 하나, 데이터 소스당 하나(2단계 제약 — 데이터셋 소속을 하나로 정하기 위해).
    """
    catalog = await _get_catalog_or_404(session, catalog_id)
    datasource = await session.get(Datasource, req.datasource_id)
    if datasource is None:
        raise CatalogNotFoundError("데이터 소스를 찾을 수 없습니다.")
    dup = await session.scalar(select(CatalogConnection.id).where(
        CatalogConnection.catalog_id == catalog_id,
        CatalogConnection.datasource_id == datasource.id))
    if dup is not None:
        raise CatalogConflictError("이미 연결된 데이터 소스입니다.")
    if req.role == ROLE_QUERY and datasource.type not in QUERY_CAPABLE_TYPES:
        raise CatalogConflictError(f"{datasource.type} 유형은 카탈로그를 조회(QUERY)로 연결할 수 없습니다.")
    if req.role == ROLE_SYNC:
        owner = await session.scalar(select(CatalogConnection.datasource_id).where(
            CatalogConnection.catalog_id == catalog_id, CatalogConnection.role == ROLE_SYNC))
        if owner is not None:
            raise CatalogConflictError("이 카탈로그는 이미 다른 데이터 소스가 수집(SYNC)하고 있습니다.")
        if await _sync_catalog(session, datasource.id) is not None:
            raise CatalogConflictError("이 데이터 소스는 이미 다른 카탈로그를 수집(SYNC)하고 있습니다.")
    session.add(CatalogConnection(
        catalog_id=catalog.id, datasource_id=datasource.id, role=req.role,
        remote_name=(req.remote_name or "").strip() or None,
    ))
    await session.commit()
    logger.info("카탈로그 연결: catalog=%s datasource=%s role=%s remote=%s",
                catalog.catalog_key, datasource.datasource_id, req.role, req.remote_name)
    return (await _responses(session, [catalog]))[0]


async def remove_connection(session: AsyncSession, catalog_id: int, datasource_id: int) -> CatalogResponse:
    catalog = await _get_catalog_or_404(session, catalog_id)
    conn = (await session.execute(select(CatalogConnection).where(
        CatalogConnection.catalog_id == catalog_id,
        CatalogConnection.datasource_id == datasource_id))).scalars().first()
    if conn is None:
        raise CatalogNotFoundError("연결을 찾을 수 없습니다.")
    if conn.role == ROLE_SYNC and catalog.auto_created:
        raise CatalogConflictError("데이터 소스와 함께 만들어진 카탈로그의 수집(SYNC) 연결은 끊을 수 없습니다.")
    await session.delete(conn)
    await session.commit()
    logger.info("카탈로그 연결 해제: catalog=%s datasource id=%d", catalog.catalog_key, datasource_id)
    return (await _responses(session, [catalog]))[0]


async def list_datasource_catalogs(session: AsyncSession, datasource_id: int) -> list[CatalogResponse]:
    """데이터 소스가 연결된 카탈로그들(역할 무관)."""
    if await session.get(Datasource, datasource_id) is None:
        raise CatalogNotFoundError("데이터 소스를 찾을 수 없습니다.")
    catalogs = list((await session.execute(
        select(Catalog)
        .join(CatalogConnection, CatalogConnection.catalog_id == Catalog.id)
        .where(CatalogConnection.datasource_id == datasource_id)
        .order_by(Catalog.name, Catalog.id)
    )).scalars().all())
    return await _responses(session, catalogs)


# ---------------------------------------------------------------------------
# 엔진 카탈로그 탐색 (Trino: system.metadata.catalogs)
# ---------------------------------------------------------------------------

# Trino 커넥터 이름 → 연결 후보로 볼 Argus 카탈로그 kind
_CONNECTOR_KINDS: dict[str, tuple[str, ...]] = {
    "hive": ("hive",),
    "iceberg": ("iceberg-rest", "hive"),
    "delta_lake": ("hive",),
    "hudi": ("hive",),
    "kudu": ("kudu",),
    "postgresql": ("postgresql",),
    "mysql": ("mysql",),
    "mariadb": ("mysql",),
    "oracle": ("oracle",),
    "sqlserver": ("mssql",),
    "greenplum": ("greenplum",),
}
# 엔진 자체의 내부 카탈로그 — 연결 대상이 아니다
_TRINO_INTERNAL = frozenset({"system", "jmx", "tpch", "tpcds", "memory", "blackhole"})


async def discover_engine_catalogs(session: AsyncSession, datasource_id: int) -> EngineCatalogsResponse:
    """엔진이 노출하는 카탈로그 목록과 Argus 카탈로그 연결 후보. 지금은 Trino 만 지원."""
    datasource = await session.get(Datasource, datasource_id)
    if datasource is None:
        raise CatalogNotFoundError("데이터 소스를 찾을 수 없습니다.")
    if datasource.type != "trino":
        return EngineCatalogsResponse(
            supported=False,
            message=f"{datasource.type} 유형은 카탈로그 자동 탐색을 지원하지 않습니다. 연결할 카탈로그를 직접 고르세요.")
    from app.catalog.datasource_secrets import unseal_config
    from app.catalog.models import DatasourceConfiguration

    stored = await session.scalar(select(DatasourceConfiguration.config_json).where(
        DatasourceConfiguration.datasource_id == datasource_id))
    config = unseal_config(stored)
    try:
        rows = await _trino_catalogs(config)
    except ValueError as e:
        return EngineCatalogsResponse(supported=True, message=str(e))
    except Exception as e:  # noqa: BLE001 - 원시 메시지는 로그에만
        logger.warning("Trino 카탈로그 탐색 실패 (datasource id=%d): %r", datasource_id, e)
        return EngineCatalogsResponse(supported=True, message="Trino 에서 카탈로그 목록을 읽지 못했습니다. 접속 설정을 확인하세요.")

    connected = dict((await session.execute(
        select(CatalogConnection.remote_name, CatalogConnection.catalog_id)
        .where(CatalogConnection.datasource_id == datasource_id)
    )).all())
    by_kind: dict[str, list[int]] = {}
    for cid, kind in (await session.execute(select(Catalog.id, Catalog.kind))).all():
        by_kind.setdefault((kind or "").lower(), []).append(cid)
    out = []
    for name, connector in rows:
        if name in _TRINO_INTERNAL:
            continue
        kinds = _CONNECTOR_KINDS.get((connector or "").lower(), ())
        out.append(EngineCatalogEntry(
            remote_name=name,
            connector=connector,
            connected_catalog_id=connected.get(name),
            suggested_catalog_ids=[cid for k in kinds for cid in by_kind.get(k, [])],
        ))
    return EngineCatalogsResponse(catalogs=out)


async def _trino_catalogs(config: dict) -> list[tuple[str, str | None]]:
    """Trino REST(/v1/statement)로 ``system.metadata.catalogs`` 를 읽는다. 형식 오류는 ValueError."""
    import httpx

    from app.catalog.service import validate_connection_target

    host, port = validate_connection_target(config, 8443)
    use_ssl = str(config.get("use_ssl", True)).strip().lower() in ("true", "1", "yes", "on")
    user = str(config.get("username") or "argus")
    password = config.get("password") or None
    base = f"{'https' if use_ssl else 'http'}://{host}:{port}"
    auth = (user, password) if password else None
    headers = {"X-Trino-User": user}
    sql = "SELECT catalog_name, connector_name FROM system.metadata.catalogs ORDER BY catalog_name"
    rows: list[tuple[str, str | None]] = []
    # 내부망 Trino 는 사설 인증서가 흔하다 — 접속 테스트(connection_ssl)와 같은 수준(암호화, 미검증)
    async with httpx.AsyncClient(timeout=10.0, verify=False, auth=auth) as client:
        resp = await client.post(f"{base}/v1/statement", content=sql, headers=headers)
        for _ in range(100):
            resp.raise_for_status()
            body = resp.json()
            if body.get("error"):
                raise RuntimeError(body["error"].get("message") or "Trino query failed")
            rows.extend((r[0], r[1] if len(r) > 1 else None) for r in body.get("data") or [])
            next_uri = body.get("nextUri")
            if not next_uri:
                return rows
            resp = await client.get(next_uri, headers=headers)
    raise RuntimeError("Trino 응답이 끝나지 않았습니다.")


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

async def list_catalogs(session: AsyncSession) -> list[CatalogResponse]:
    """모든 카탈로그를 이름순으로. 연결·활성 데이터셋 수는 각 1쿼리로 모아 붙인다(N+1 회피)."""
    catalogs = list((await session.execute(
        select(Catalog).order_by(Catalog.name, Catalog.id)
    )).scalars().all())
    return await _responses(session, catalogs)


async def _get_catalog_or_404(session: AsyncSession, catalog_id: int) -> Catalog:
    catalog = await session.get(Catalog, catalog_id)
    if catalog is None:
        raise CatalogNotFoundError("카탈로그를 찾을 수 없습니다.")
    return catalog


async def get_catalog(session: AsyncSession, catalog_id: int) -> CatalogResponse | None:
    catalog = await session.get(Catalog, catalog_id)
    if catalog is None:
        return None
    return (await _responses(session, [catalog]))[0]


async def _responses(session: AsyncSession, catalogs: list[Catalog]) -> list[CatalogResponse]:
    if not catalogs:
        return []
    ids = [c.id for c in catalogs]
    counts = dict((await session.execute(
        select(Dataset.catalog_id, func.count())
        .where(Dataset.catalog_id.in_(ids), Dataset.status != "removed")
        .group_by(Dataset.catalog_id)
    )).all())
    conns: dict[int, list[CatalogConnectionResponse]] = {cid: [] for cid in ids}
    rows = (await session.execute(
        select(CatalogConnection, Datasource)
        .join(Datasource, Datasource.id == CatalogConnection.datasource_id)
        .where(CatalogConnection.catalog_id.in_(ids))
        .order_by(CatalogConnection.id)
    )).all()
    for conn, ds in rows:
        conns[conn.catalog_id].append(CatalogConnectionResponse(
            datasource_id=ds.id,
            datasource_key=ds.datasource_id,
            datasource_name=ds.name,
            datasource_type=ds.type,
            role=conn.role,
            remote_name=conn.remote_name,
        ))
    return [
        CatalogResponse(
            id=c.id,
            catalog_key=c.catalog_key,
            name=c.name,
            kind=c.kind,
            source_type=c.source_type,
            auto_created=bool(c.auto_created),
            summary=c.summary,
            description=c.description,
            owner=c.owner,
            status=c.status,
            dataset_count=counts.get(c.id, 0),
            connections=conns[c.id],
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in catalogs
    ]
