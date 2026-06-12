# SPDX-License-Identifier: Apache-2.0
"""페더레이션 서비스 — peer 레지스트리 CRUD + scatter-gather 통합 검색.

Phase 0 PoC:
- peer 등록/조회/수정/삭제
- 로컬 hybrid_search + ACTIVE peer 들로의 동시 fan-out 을 병합한 통합 검색
"""

import asyncio
import json
import logging

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.catalog.models import (
    Dataset,
    DatasetGlossaryTerm,
    DatasetSchema,
    Datasource,
    GlossaryTerm,
)
from app.embedding.registry import get_provider
from app.federation import breaker, capabilities, client
from app.federation.models import (
    FederatedDataset,
    FederatedDatasetEmbedding,
    FederatedInstance,
)
from app.federation.schemas import (
    FederatedBrowseDataset,
    FederatedBrowseDatasource,
    FederatedBrowseResponse,
    FederatedExportDataset,
    FederatedExportDatasetsResponse,
    FederatedExportField,
    FederatedInstanceCreate,
    FederatedInstanceResponse,
    FederatedInstanceUpdate,
    FederatedSearchHit,
    FederatedSearchResponse,
)
from app.search import service as search_service

logger = logging.getLogger(__name__)

# HARVEST 미러를 이용해 로컬에서 검색하는 모드 (LIVE 는 요청 시 fan-out)
_MIRROR_MODES = ("HARVEST", "HYBRID")


# ---------------------------------------------------------------------------
# Peer 레지스트리 CRUD
# ---------------------------------------------------------------------------

def _parse_display_fields(raw: str | None) -> list[str] | None:
    """display_fields JSON 문자열 → capability 키 리스트(검증). 손상 시 None(전부 표시)."""
    if not raw:
        return None
    try:
        keys = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(keys, list):
        return None
    return capabilities.validate([str(k) for k in keys])


def _serialize_display_fields(keys: list[str] | None) -> str | None:
    """capability 키 리스트 → 저장용 JSON 문자열(검증). None/빈 값은 None."""
    if keys is None:
        return None
    valid = capabilities.validate(keys)
    return json.dumps(valid)


def _to_response(inst: FederatedInstance) -> FederatedInstanceResponse:
    return FederatedInstanceResponse(
        id=inst.id,
        instance_key=inst.instance_key,
        name=inst.name,
        base_url=inst.base_url,
        has_auth_token=bool(inst.auth_token),
        mode=inst.mode,
        sync_interval_sec=inst.sync_interval_sec,
        status=inst.status,
        description=inst.description,
        display_fields=_parse_display_fields(inst.display_fields),
        created_at=inst.created_at,
        updated_at=inst.updated_at,
    )


async def list_instances(session: AsyncSession) -> list[FederatedInstanceResponse]:
    result = await session.execute(
        select(FederatedInstance).order_by(FederatedInstance.name)
    )
    return [_to_response(i) for i in result.scalars().all()]


async def get_instance(session: AsyncSession, instance_id: int) -> FederatedInstance | None:
    result = await session.execute(
        select(FederatedInstance).where(FederatedInstance.id == instance_id)
    )
    return result.scalars().first()


async def create_instance(
    session: AsyncSession, req: FederatedInstanceCreate
) -> FederatedInstanceResponse:
    inst = FederatedInstance(
        instance_key=req.instance_key,
        name=req.name,
        base_url=req.base_url,
        auth_token=req.auth_token,
        mode=req.mode,
        sync_interval_sec=req.sync_interval_sec,
        description=req.description,
        display_fields=_serialize_display_fields(req.display_fields),
    )
    session.add(inst)
    await session.commit()
    await session.refresh(inst)
    logger.info("페더레이션 peer 등록: %s (%s)", inst.instance_key, inst.base_url)
    return _to_response(inst)


async def update_instance(
    session: AsyncSession, inst: FederatedInstance, req: FederatedInstanceUpdate
) -> FederatedInstanceResponse:
    data = req.model_dump(exclude_unset=True)
    if "display_fields" in data:
        # 리스트 → 저장용 JSON 문자열로 변환(None 이면 전부 표시로 초기화)
        data["display_fields"] = _serialize_display_fields(data["display_fields"])
    for field, value in data.items():
        setattr(inst, field, value)
    await session.commit()
    await session.refresh(inst)
    logger.info("페더레이션 peer 수정: %s", inst.instance_key)
    return _to_response(inst)


async def delete_instance(session: AsyncSession, inst: FederatedInstance) -> None:
    from app.federation import samples

    key = inst.instance_key
    instance_id = inst.id
    await session.delete(inst)
    await session.commit()
    # 미러 데이터(DB)는 CASCADE 로 정리되지만, 저장된 샘플 파일은 별도로 삭제한다.
    samples.delete_instance_samples(instance_id)
    logger.info("페더레이션 peer 삭제: %s", key)


async def list_active_peers(session: AsyncSession) -> list[FederatedInstance]:
    """연합 검색에 포함할 ACTIVE peer 목록."""
    result = await session.execute(
        select(FederatedInstance).where(FederatedInstance.status == "ACTIVE")
    )
    return list(result.scalars().all())


async def federation_stats(session: AsyncSession) -> "FederationStats":
    """페더레이션 관측 요약 — peer 별 미러 카운트·최근 동기화·breaker 상태."""
    from app.federation.models import (
        FederatedDataset,
        FederationLineage,
        FederationSyncRun,
    )
    from app.federation.schemas import FederationStats, FederationStatsInstance

    instances = (await session.execute(
        select(FederatedInstance).order_by(FederatedInstance.name)
    )).scalars().all()

    # 미러 카운트 집계(instance_id 별)
    ds_counts = dict((await session.execute(
        select(FederatedDataset.instance_id, func.count())
        .group_by(FederatedDataset.instance_id)
    )).all())
    lin_counts = dict((await session.execute(
        select(FederationLineage.instance_id, func.count())
        .group_by(FederationLineage.instance_id)
    )).all())

    bsnap = breaker.snapshot()

    rows: list[FederationStatsInstance] = []
    total_ds = 0
    total_lin = 0
    active = 0
    for inst in instances:
        mirror_ds = int(ds_counts.get(inst.id, 0))
        mirror_lin = int(lin_counts.get(inst.id, 0))
        total_ds += mirror_ds
        total_lin += mirror_lin
        if inst.status == "ACTIVE":
            active += 1

        last = (await session.execute(
            select(FederationSyncRun)
            .where(FederationSyncRun.instance_id == inst.id)
            .order_by(FederationSyncRun.started_at.desc())
            .limit(1)
        )).scalars().first()

        bstate = bsnap.get(inst.instance_key, {})
        rows.append(FederationStatsInstance(
            id=inst.id, instance_key=inst.instance_key, name=inst.name,
            mode=inst.mode, status=inst.status,
            mirror_datasets=mirror_ds, mirror_lineage=mirror_lin,
            last_sync_status=last.status if last else None,
            last_sync_started_at=last.started_at if last else None,
            last_sync_finished_at=last.finished_at if last else None,
            last_sync_seen=last.datasets_seen if last else None,
            last_sync_embedded=last.datasets_embedded if last else None,
            last_error=last.error if last else None,
            breaker_open=bool(bstate.get("open", False)),
            breaker_failures=int(bstate.get("failures", 0)),
        ))

    return FederationStats(
        total_instances=len(instances),
        active_instances=active,
        total_mirror_datasets=total_ds,
        total_mirror_lineage=total_lin,
        instances=rows,
    )


# 단일 인스턴스 탐색에서 한 번에 반환하는 미러 데이터셋 상한.
# 트리 UI 가 인스턴스 단위로 lazy-load 하므로 충분히 크게 잡되, 과대 인스턴스에서
# 응답이 폭증하지 않도록 cap 을 두고 truncated 플래그로 알린다.
_BROWSE_CAP = 2000

# datasource_name 이 NULL 인 미러 데이터셋의 표시용 그룹 이름.
_BROWSE_UNGROUPED = "(미지정)"


async def browse_instance_datasets(
    session: AsyncSession, inst: FederatedInstance, q: str | None = None,
) -> FederatedBrowseResponse:
    """단일 인스턴스의 HARVEST 미러 데이터셋을 데이터소스별로 묶어 반환한다.

    통합 검색과 달리 검색어가 없어도 동작하는 '둘러보기' 용도다. ``q`` 가 주어지면
    name/qualified_name/description 부분일치로 트리를 좁힌다. 반환은 데이터소스명
    오름차순, 각 그룹 안에서 데이터셋명 오름차순으로 정렬한다.
    """
    base = select(FederatedDataset).where(FederatedDataset.instance_id == inst.id)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(
            or_(
                func.lower(FederatedDataset.name).like(like),
                func.lower(FederatedDataset.qualified_name).like(like),
                func.lower(FederatedDataset.description).like(like),
            )
        )

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        await session.execute(
            base.order_by(
                FederatedDataset.datasource_name.asc(),
                FederatedDataset.name.asc(),
            ).limit(_BROWSE_CAP)
        )
    ).scalars().all()

    # 데이터소스명 기준 그룹핑(정렬 순서 유지). type 은 그룹 내 첫 비어있지 않은 값 채택.
    groups: dict[str, FederatedBrowseDatasource] = {}
    for ds in rows:
        key = ds.datasource_name or _BROWSE_UNGROUPED
        grp = groups.get(key)
        if grp is None:
            grp = FederatedBrowseDatasource(
                datasource_name=key,
                datasource_type=ds.datasource_type,
                dataset_count=0,
                datasets=[],
            )
            groups[key] = grp
        if grp.datasource_type is None and ds.datasource_type:
            grp.datasource_type = ds.datasource_type
        grp.datasets.append(
            FederatedBrowseDataset(
                federated_urn=ds.federated_urn,
                remote_urn=ds.remote_urn,
                name=ds.name,
                display_name=ds.display_name,
                summary=ds.summary,
                description=ds.description,
                qualified_name=ds.qualified_name,
                origin=ds.origin,
                field_count=ds.field_count,
                remote_created_at=ds.remote_created_at,
                remote_updated_at=ds.remote_updated_at,
                harvested_at=ds.harvested_at,
            )
        )
        grp.dataset_count += 1

    return FederatedBrowseResponse(
        instance_id=inst.id,
        instance_key=inst.instance_key,
        instance_name=inst.name,
        total_datasets=total,
        truncated=total > len(rows),
        datasources=list(groups.values()),
    )


async def get_instance_by_key(
    session: AsyncSession, instance_key: str,
) -> FederatedInstance | None:
    result = await session.execute(
        select(FederatedInstance).where(FederatedInstance.instance_key == instance_key)
    )
    return result.scalars().first()


async def resolve_federated_urn(
    session: AsyncSession, federated_urn: str,
) -> tuple[FederatedInstance, str] | None:
    """``{instance_key}::{remote_urn}`` → (peer 인스턴스, remote_urn).

    구분자 ``::`` 가 없거나 인스턴스를 찾지 못하면 None.
    """
    if "::" not in federated_urn:
        return None
    instance_key, remote_urn = federated_urn.split("::", 1)
    instance = await get_instance_by_key(session, instance_key)
    if instance is None or not remote_urn:
        return None
    return instance, remote_urn


# ---------------------------------------------------------------------------
# LIVE drill-down — federated URN 의 상세/샘플을 원 인스턴스에서 실시간 프록시
# ---------------------------------------------------------------------------

async def federated_dataset_detail(
    session: AsyncSession, federated_urn: str,
) -> dict | None:
    """federated URN 의 전체 메타데이터(스키마/태그/소유자/속성)를 peer 에서 가져온다.

    반환 dict 에 출처 인스턴스 정보를 부가한다. 인스턴스 미해석이면 None.
    peer 호출 실패는 예외로 전파(라우터가 502 로 변환).
    """
    resolved = await resolve_federated_urn(session, federated_urn)
    if resolved is None:
        return None
    instance, remote_urn = resolved
    metadata = await _call_peer_with_breaker(
        instance, lambda: client.fetch_export_dataset(instance, remote_urn)
    )
    return {
        "federated_urn": federated_urn,
        "remote_urn": remote_urn,
        "source_instance_key": instance.instance_key,
        "source_instance_name": instance.name,
        "source_base_url": instance.base_url,
        # 소비자(허브) 표시 선택 — 프론트가 (노출자 노출 ∩ 이 선택)만 렌더한다.
        "display_fields": _parse_display_fields(instance.display_fields),
        "metadata": metadata,
    }


async def build_lineage_graph(
    session: AsyncSession, root_urn: str, depth: int = 1,
) -> "FederatedLineageGraph":
    """root 데이터셋을 중심으로 로컬+미러 리니지를 URN 매칭으로 stitch 한 그래프.

    엔드포인트 URN 은 데이터소스 전역 유일이라, 로컬 리니지와 여러 peer 가 보고한
    미러 리니지를 같은 URN 키로 합쳐 cross-instance 데이터 흐름을 만든다.
    """
    from app.catalog.models import DatasetLineage
    from app.federation.models import FederationLineage
    from app.federation.schemas import (
        FederatedLineageEdge,
        FederatedLineageGraph,
        FederatedLineageNode,
    )

    # federated_urn 으로 들어와도 그래프 키는 plain URN(remote_urn)
    root = root_urn.split("::", 1)[1] if "::" in root_urn else root_urn

    # 전체 엣지 적재(로컬 + 미러) — (src, tgt, relation, reported_by)
    src = aliased(Dataset)
    tgt = aliased(Dataset)
    local_rows = (await session.execute(
        select(src.urn, tgt.urn, DatasetLineage.relation_type)
        .join(src, src.id == DatasetLineage.source_dataset_id)
        .join(tgt, tgt.id == DatasetLineage.target_dataset_id)
    )).all()
    mirror_rows = (await session.execute(
        select(
            FederationLineage.source_urn, FederationLineage.target_urn,
            FederationLineage.relation_type, FederatedInstance.instance_key,
        )
        .join(FederatedInstance, FederatedInstance.id == FederationLineage.instance_id)
        .where(FederatedInstance.status == "ACTIVE")
    )).all()

    edges_all: list[tuple[str, str, str, str | None]] = []
    adj: dict[str, set[str]] = {}
    for s_urn, t_urn, rel in local_rows:
        edges_all.append((s_urn, t_urn, rel, None))
        adj.setdefault(s_urn, set()).add(t_urn)
        adj.setdefault(t_urn, set()).add(s_urn)
    for s_urn, t_urn, rel, key in mirror_rows:
        edges_all.append((s_urn, t_urn, rel, key))
        adj.setdefault(s_urn, set()).add(t_urn)
        adj.setdefault(t_urn, set()).add(s_urn)

    # BFS 로 depth 까지 방문 노드 수집
    visited = {root}
    frontier = {root}
    for _ in range(max(depth, 0)):
        nxt: set[str] = set()
        for u in frontier:
            nxt |= adj.get(u, set())
        nxt -= visited
        if not nxt:
            break
        visited |= nxt
        frontier = nxt

    # 양 끝이 모두 방문 노드인 엣지만(중복 제거)
    seen_edges: set[tuple] = set()
    out_edges: list[FederatedLineageEdge] = []
    for s_urn, t_urn, rel, key in edges_all:
        if s_urn in visited and t_urn in visited:
            sig = (s_urn, t_urn, rel, key)
            if sig in seen_edges:
                continue
            seen_edges.add(sig)
            out_edges.append(FederatedLineageEdge(
                source_urn=s_urn, target_urn=t_urn, relation_type=rel, reported_by=key,
            ))

    nodes = await _resolve_lineage_nodes(session, visited)
    return FederatedLineageGraph(
        root_urn=root, depth=depth, nodes=nodes, edges=out_edges,
    )


async def _resolve_lineage_nodes(
    session: AsyncSession, urns: set[str],
) -> list["FederatedLineageNode"]:
    """URN 집합을 노드로 해석한다(로컬 우선 → 미러 → 미해석 placeholder)."""
    from app.federation.schemas import FederatedLineageNode

    if not urns:
        return []
    url_list = list(urns)

    local = {
        r.urn: r for r in (await session.execute(
            select(
                Dataset.urn, Dataset.name,
                Datasource.name.label("datasource_name"),
                Datasource.type.label("datasource_type"),
            )
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(Dataset.urn.in_(url_list))
        )).all()
    }
    mirror: dict[str, object] = {}
    for r in (await session.execute(
        select(
            FederatedDataset.remote_urn, FederatedDataset.name,
            FederatedDataset.datasource_name, FederatedDataset.datasource_type,
            FederatedInstance.instance_key, FederatedInstance.name.label("instance_name"),
        )
        .join(FederatedInstance, FederatedInstance.id == FederatedDataset.instance_id)
        .where(FederatedDataset.remote_urn.in_(url_list))
    )).all():
        mirror.setdefault(r.remote_urn, r)   # 동일 URN 이 여러 peer 에 있으면 첫 번째

    nodes: list[FederatedLineageNode] = []
    for u in url_list:
        if u in local:
            r = local[u]
            nodes.append(FederatedLineageNode(
                urn=u, name=r.name, datasource_name=r.datasource_name,
                datasource_type=r.datasource_type,
                source_instance_key=None, source_instance_name=None,
            ))
        elif u in mirror:
            r = mirror[u]
            nodes.append(FederatedLineageNode(
                urn=u, name=r.name, datasource_name=r.datasource_name,
                datasource_type=r.datasource_type,
                source_instance_key=r.instance_key, source_instance_name=r.instance_name,
            ))
        else:
            nodes.append(FederatedLineageNode(urn=u, unresolved=True))
    return nodes


async def federated_dataset_sample(
    session: AsyncSession, federated_urn: str, limit: int = 100,
) -> dict | None:
    """federated URN 의 샘플 데이터를 반환한다.

    HARVEST 로 미리 받아 로컬에 저장한 미러 샘플이 있으면 **오프라인으로** 제공하고
    (peer 가 죽어도 동작), 없으면 LIVE 드릴다운으로 peer 에서 실시간 조회한다.
    """
    from app.federation import samples

    resolved = await resolve_federated_urn(session, federated_urn)
    if resolved is None:
        return None
    instance, remote_urn = resolved

    envelope = {
        "federated_urn": federated_urn,
        "remote_urn": remote_urn,
        "source_instance_key": instance.instance_key,
        "source_instance_name": instance.name,
        "source_base_url": instance.base_url,
    }

    # 1) 미러 샘플(HARVEST 저장분) 우선 — federation/samples 에서 읽는다.
    mirror = (await session.execute(
        select(FederatedDataset.id, FederatedDataset.has_sample).where(
            FederatedDataset.instance_id == instance.id,
            FederatedDataset.remote_urn == remote_urn,
        )
    )).first()
    if mirror and mirror.has_sample:
        stored = samples.read_sample(instance.id, mirror.id)
        if stored is not None:
            return {**envelope, **stored}

    # 2) 폴백 — LIVE 드릴다운(peer 실시간 조회)
    sample = await _call_peer_with_breaker(
        instance, lambda: client.fetch_export_sample(instance, remote_urn, limit=limit)
    )
    return {**envelope, **sample}


# ---------------------------------------------------------------------------
# 로컬 승격(import) — 페더레이션(미러) 데이터셋을 로컬 카탈로그의 1급 데이터셋으로 복사
#   전용 "Federation Imports" 데이터소스에 담고, peer 드릴다운으로 전체 메타(스키마·태그·
#   소유자·용어집·DDL·확장)를 가져와 materialize 한다. 1회 스냅샷(이후 peer 와 분리).
# ---------------------------------------------------------------------------

_IMPORT_DS_NAME = "Federation Imports"
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
    """같은 이름의 로컬 용어가 있으면 id, 없으면 None(새로 만들지 않음)."""
    from app.catalog.models import GlossaryTerm

    row = (await session.execute(
        select(GlossaryTerm).where(GlossaryTerm.name == name)
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
                pass
    for k in _EXT_DT_FIELDS:
        v = ext.get(k)
        if isinstance(v, str) and v:
            try:
                setattr(dataset, k, datetime.fromisoformat(v))
            except ValueError:
                pass


async def _copy_sample_to_local(
    session: AsyncSession, federated_urn: str, datasource, dataset
) -> bool:
    """페더레이션 샘플(미러 JSON 또는 LIVE)을 로컬 parquet 으로 변환 저장한다."""
    try:
        sample = await federated_dataset_sample(session, federated_urn, limit=1000)
    except Exception:  # noqa: BLE001 — 샘플은 best-effort
        return False
    if not sample:
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
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("import 샘플 저장 실패 (%s): %s", dataset.urn, e)
        return False


async def import_federated_dataset(
    session: AsyncSession, federated_urn: str, created_by: str | None = None,
) -> dict | None:
    """페더레이션(미러) 데이터셋을 로컬 카탈로그로 승격(import)한다.

    peer 드릴다운으로 전체 메타데이터를 받아 전용 데이터소스에 1급 데이터셋으로 생성하고,
    스키마·태그·소유자·용어집·DDL·확장 메타·샘플을 복사한 뒤 로컬 임베딩을 생성한다.
    이미 가져온 데이터셋이면 ValueError, 해석 불가면 None.
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

    detail = await federated_dataset_detail(session, federated_urn)
    if detail is None:
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
        raise ValueError(f"이미 로컬로 가져온 데이터셋입니다: {federated_urn}")

    ext: dict = meta.get("extended") or {}
    datasource = await _get_or_create_import_datasource(session)

    name = meta.get("name") or remote_urn
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
        display_name=ext.get("display_name") or meta.get("display_name"),
        datasource_id=datasource.id,
        summary=ext.get("summary") or meta.get("summary"),
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


# ---------------------------------------------------------------------------
# 노출자(provider) 정책 — 이 인스턴스가 외부에 줄 정보 항목(capability) 설정
#   catalog_configuration 에 CSV 로 보관. 미설정이면 visibility(PII) 기반 기본값.
# ---------------------------------------------------------------------------

_EXPOSED_CATEGORY = "federation"
_EXPOSED_KEY = "federation.exposed_fields"


async def get_exposed_fields(session: AsyncSession) -> list[str]:
    """이 인스턴스가 외부에 노출하는 capability 키 목록(레지스트리 순서로 정규화)."""
    from app.core.config import settings
    from app.settings.service import get_config_by_category

    conf = await get_config_by_category(session, _EXPOSED_CATEGORY)
    raw = conf.get(_EXPOSED_KEY)
    if raw is None:
        return capabilities.default_exposed(settings.federation_export_exclude_pii)
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    return capabilities.validate(keys)


async def set_exposed_fields(session: AsyncSession, keys: list[str]) -> list[str]:
    """노출 capability 키를 검증해 저장하고 정규화된 결과를 반환한다(admin)."""
    from app.settings.service import update_config

    valid = capabilities.validate(keys)
    await update_config(session, _EXPOSED_CATEGORY, {_EXPOSED_KEY: ",".join(valid)})
    logger.info("페더레이션 노출 항목 갱신: %d개", len(valid))
    return valid


async def _fetch_ddl(session: AsyncSession, dataset_id: int) -> str | None:
    row = (
        await session.execute(select(Dataset.ddl).where(Dataset.id == dataset_id))
    ).first()
    return row[0] if row else None


async def _fetch_glossary(session: AsyncSession, dataset_id: int) -> list[str]:
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

    items: list[FederatedExportDataset] = []
    for r in rows:
        fields_rows = (await session.execute(
            select(DatasetSchema.field_path, DatasetSchema.field_type, DatasetSchema.description)
            .where(DatasetSchema.dataset_id == r.id)
            .order_by(DatasetSchema.ordinal)
        )).all()
        items.append(FederatedExportDataset(
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
        ))

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
    from app.federation.visibility import exportable_dataset_ids, has_visibility_policy

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

    # visibility: 양 끝이 모두 노출 가능한 엣지만
    allowed: set[int] | None = None
    if has_visibility_policy():
        ids = {r.source_dataset_id for r in rows} | {r.target_dataset_id for r in rows}
        allowed = await exportable_dataset_ids(session, list(ids))

    edges = [
        FederatedExportLineageEdge(
            source_urn=r.source_urn, target_urn=r.target_urn,
            relation_type=r.relation_type, lineage_source=r.lineage_source,
            description=r.description,
        )
        for r in rows
        if allowed is None
        or (r.source_dataset_id in allowed and r.target_dataset_id in allowed)
    ]
    return FederatedExportLineageResponse(edges=edges, total=len(edges))


# ---------------------------------------------------------------------------
# Scatter-gather 통합 검색
# ---------------------------------------------------------------------------

async def _local_hits(
    session: AsyncSession, query: str, limit: int, threshold: float
) -> list[FederatedSearchHit]:
    """이 허브(로컬)의 hybrid_search 결과를 연합 hit 으로 변환."""
    scored = await search_service.hybrid_search(
        session, query, limit=limit, threshold=threshold
    )
    hits: list[FederatedSearchHit] = []
    for ds_id, score, match_type in scored:
        summary = await search_service._build_dataset_summary(session, ds_id)
        if not summary:
            continue
        hits.append(FederatedSearchHit(
            urn=summary.urn,
            name=summary.name,
            datasource_name=summary.datasource_name,
            datasource_type=summary.datasource_type,
            description=summary.description,
            origin=summary.origin,
            score=round(score, 4),
            match_type=match_type,
            source_instance_key=None,        # None = 로컬
            source_instance_name=None,
            source_base_url=None,
        ))
    return hits


async def _peer_hits(
    instance: FederatedInstance, query: str, limit: int, threshold: float
) -> list[FederatedSearchHit]:
    """단일 peer 호출 → 연합 hit 변환. 실패 시 예외를 전파(호출자가 집계).

    circuit breaker 가 열려 있으면 네트워크 호출 없이 즉시 실패시킨다.
    """
    key = instance.instance_key
    if breaker.is_open(key):
        raise breaker.CircuitOpenError(key)
    try:
        export = await client.search_peer(
            instance, query, limit=limit, threshold=threshold
        )
    except Exception:
        breaker.record_failure(key)
        raise
    breaker.record_success(key)
    return [
        FederatedSearchHit(
            **hit.model_dump(),
            source_instance_key=instance.instance_key,
            source_instance_name=instance.name,
            source_base_url=instance.base_url,
        )
        for hit in export.items
    ]


async def _call_peer_with_breaker(instance: FederatedInstance, coro_factory):
    """drill-down 류 단일 peer 호출을 breaker 로 감싼다.

    ``coro_factory`` 는 매 호출마다 새 coroutine 을 만드는 0-인자 콜러블.
    """
    key = instance.instance_key
    if breaker.is_open(key):
        raise breaker.CircuitOpenError(key)
    try:
        result = await coro_factory()
    except Exception:
        breaker.record_failure(key)
        raise
    breaker.record_success(key)
    return result


# ---- HARVEST 미러 검색 (로컬 pgvector + 키워드) ---------------------------

async def _mirror_keyword_ids(
    session: AsyncSession, query: str, limit: int,
) -> set[int]:
    """미러 데이터셋 키워드 매칭 → federation_datasets.id 집합 (ACTIVE·미러모드 한정)."""
    pattern = f"%{query}%"
    rows = (await session.execute(
        select(FederatedDataset.id)
        .join(FederatedInstance, FederatedInstance.id == FederatedDataset.instance_id)
        .where(
            FederatedInstance.status == "ACTIVE",
            FederatedInstance.mode.in_(_MIRROR_MODES),
            or_(
                FederatedDataset.name.ilike(pattern),
                FederatedDataset.description.ilike(pattern),
                FederatedDataset.federated_urn.ilike(pattern),
                FederatedDataset.qualified_name.ilike(pattern),
            ),
        )
        .limit(limit)
    )).all()
    return {r[0] for r in rows}


async def _mirror_semantic(
    session: AsyncSession, query_vec: list[float], limit: int, threshold: float,
) -> dict[int, float]:
    """미러 임베딩 pgvector 검색 → {federation_dataset_id: similarity}."""
    result = await session.execute(text("""
        SELECT e.federation_dataset_id,
               1 - (e.embedding <=> CAST(:qv AS vector)) AS similarity
        FROM federation_dataset_embeddings e
        JOIN federation_datasets fd ON fd.id = e.federation_dataset_id
        JOIN federation_instances fi ON fi.id = fd.instance_id
        WHERE fi.status = 'ACTIVE' AND fi.mode IN ('HARVEST', 'HYBRID')
          AND 1 - (e.embedding <=> CAST(:qv AS vector)) >= :threshold
        ORDER BY e.embedding <=> CAST(:qv AS vector)
        LIMIT :lim
    """), {"qv": str(query_vec), "threshold": threshold, "lim": limit})
    return {row[0]: float(row[1]) for row in result.fetchall()}


async def search_mirror(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    keyword_weight: float = 0.3,
    semantic_weight: float = 0.7,
) -> list[FederatedSearchHit]:
    """HARVEST 미러를 로컬에서 hybrid 검색한다.

    임베딩 제공자가 없거나 pgvector 가 없으면 키워드 전용으로 폴백한다. 점수 결합은
    데이터셋 ``hybrid_search`` 와 동일한 방식.
    """
    semantic_map: dict[int, float] = {}
    provider = await get_provider()
    if provider is not None:
        try:
            vectors = await provider.embed([query])
            semantic_map = await _mirror_semantic(session, vectors[0], limit * 2, threshold)
        except Exception as e:  # noqa: BLE001 — 키워드로 degrade
            logger.warning("미러 시맨틱 검색 불가, 키워드로 폴백: %s", e)

    keyword_ids = await _mirror_keyword_ids(session, query, limit * 2)

    scored: dict[int, tuple[float, str]] = {}
    for fid in set(semantic_map) | keyword_ids:
        sem = semantic_map.get(fid, 0.0)
        kw = 1.0 if fid in keyword_ids else 0.0
        combined = (semantic_weight * sem + keyword_weight * kw) if semantic_map else kw
        if sem > 0 and kw > 0:
            mt = "hybrid"
        elif sem > 0:
            mt = "semantic"
        else:
            mt = "keyword"
        scored[fid] = (combined, mt)

    top_ids = sorted(scored, key=lambda i: scored[i][0], reverse=True)[:limit]
    if not top_ids:
        return []

    # 상위 미러 행 + 출처 인스턴스 정보 로드
    rows = (await session.execute(
        select(
            FederatedDataset.id, FederatedDataset.federated_urn, FederatedDataset.name,
            FederatedDataset.datasource_name, FederatedDataset.datasource_type,
            FederatedDataset.description, FederatedDataset.origin,
            FederatedInstance.instance_key, FederatedInstance.name.label("instance_name"),
            FederatedInstance.base_url,
        )
        .join(FederatedInstance, FederatedInstance.id == FederatedDataset.instance_id)
        .where(FederatedDataset.id.in_(top_ids))
    )).all()

    hits: list[FederatedSearchHit] = []
    for r in rows:
        combined, mt = scored[r.id]
        hits.append(FederatedSearchHit(
            urn=r.federated_urn, name=r.name,
            datasource_name=r.datasource_name, datasource_type=r.datasource_type,
            description=r.description, origin=r.origin,
            score=round(combined, 4), match_type=mt,
            source_instance_key=r.instance_key,
            source_instance_name=r.instance_name,
            source_base_url=r.base_url,
        ))
    return hits


async def federated_search(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    include_local: bool = True,
) -> FederatedSearchResponse:
    """로컬 + ACTIVE peer 들을 병합한 통합 검색.

    모드별 경로:
    - HARVEST/HYBRID peer → 로컬 미러를 pgvector 로 검색(빠름·일관·내결함성).
    - LIVE peer → 요청 시점에 실시간 fan-out(scatter-gather).

    LIVE peer 가 도달 실패하면 ``instances_failed`` 로 보고하고 나머지로 degrade 한다.
    HARVEST 미러는 단일 로컬 쿼리라 peer 가 죽어도 검색이 동작한다.
    """
    peers = await list_active_peers(session)
    live_peers = [p for p in peers if p.mode not in _MIRROR_MODES]
    mirror_peers = [p for p in peers if p.mode in _MIRROR_MODES]

    # 동시 실행 task. 출처 라벨: None=로컬, "__mirror__"=미러, 그 외 instance_key.
    tasks: list = []
    task_keys: list[str | None] = []

    if include_local:
        tasks.append(_local_hits(session, query, limit, threshold))
        task_keys.append(None)

    if mirror_peers:
        tasks.append(search_mirror(session, query, limit=limit, threshold=threshold))
        task_keys.append("__mirror__")

    for peer in live_peers:
        tasks.append(_peer_hits(peer, query, limit, threshold))
        task_keys.append(peer.instance_key)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged: list[list[FederatedSearchHit]] = []
    failed: list[str] = []
    for key, result in zip(task_keys, results):
        if isinstance(result, Exception):
            label = key or "local"
            logger.warning("연합 검색 부분 실패 [%s]: %s", label, result)
            # 로컬/미러 실패는 peer 단위 instances_failed 에 넣지 않는다.
            if key is not None and key != "__mirror__":
                failed.append(key)
            continue
        merged.append(result)  # type: ignore[arg-type]

    flat: list[FederatedSearchHit] = [hit for group in merged for hit in group]
    flat.sort(key=lambda h: h.score, reverse=True)
    flat = flat[:limit]

    # 시도한 인스턴스 수: 로컬 + 미러가 커버한 peer + LIVE peer
    instances_queried = (1 if include_local else 0) + len(mirror_peers) + len(live_peers)

    return FederatedSearchResponse(
        query=query,
        items=flat,
        total=len(flat),
        instances_queried=instances_queried,
        instances_failed=failed,
    )
