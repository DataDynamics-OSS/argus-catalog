# SPDX-License-Identifier: Apache-2.0
"""LIVE 드릴다운 — federated URN 의 상세·샘플·리니지를 원 인스턴스에서 조회(+breaker)."""

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.catalog.models import (
    Dataset,
    Datasource,
)
from app.federation import breaker, client
from app.federation.models import (
    FederatedDataset,
    FederatedInstance,
)
from app.federation.registry import _parse_display_fields, resolve_federated_urn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LIVE drill-down — federated URN 의 상세/샘플을 원 인스턴스에서 실시간 프록시
# ---------------------------------------------------------------------------

async def federated_dataset_detail(
    session: AsyncSession, federated_urn: str, *, include_peer_exposed: bool = True,
) -> dict | None:
    """federated URN 의 전체 메타데이터(스키마/태그/소유자/속성)를 peer 에서 가져온다.

    반환 dict 에 출처 인스턴스 정보를 부가한다. 인스턴스 미해석이면 None.
    peer 호출 실패는 예외로 전파(라우터가 502 로 변환).

    ``peer_exposed`` — peer 가 노출하는 capability 키(``/export/capabilities``). 화면이 peer 가
    노출하지 않는 샘플·리니지 탭을 숨기는 데 쓴다(예전에는 탭을 보이고 열면 오류였다 — IT FED).
    조회 실패면 None(알 수 없음 → 화면은 예전처럼 탭을 보인다). 가져오기처럼 화면 표시가 필요 없는
    호출은 ``include_peer_exposed=False`` 로 추가 호출을 생략한다.
    """
    resolved = await resolve_federated_urn(session, federated_urn)
    if resolved is None:
        logger.debug("federated URN 미해석(상세): urn=%s", federated_urn)
        return None
    instance, remote_urn = resolved
    logger.debug(
        "LIVE 상세 드릴다운: instance=%s remote_urn=%s", instance.instance_key, remote_urn
    )
    metadata = await _call_peer_with_breaker(
        instance, lambda: client.fetch_export_dataset(instance, remote_urn)
    )
    peer_exposed = await _peer_exposed(instance) if include_peer_exposed else None
    return {
        "federated_urn": federated_urn,
        "remote_urn": remote_urn,
        "source_instance_key": instance.instance_key,
        "source_instance_name": instance.name,
        "source_base_url": instance.base_url,
        # 소비자(허브) 표시 선택 — 프론트가 (노출자 노출 ∩ 이 선택)만 렌더한다.
        "display_fields": _parse_display_fields(instance.display_fields),
        # 노출자(peer) 노출 항목 — None=알 수 없음(조회 실패)
        "peer_exposed": peer_exposed,
        "metadata": metadata,
    }


async def _peer_exposed(instance: FederatedInstance) -> list[str] | None:
    """peer 노출 capability 키 — best-effort(실패해도 상세 조회는 성공, breaker 에 반영하지 않음)."""
    try:
        caps = await client.fetch_export_capabilities(instance)
    except Exception as e:  # noqa: BLE001 — 구버전 peer(엔드포인트 없음)·일시 장애 → 알 수 없음
        logger.info("peer capabilities 조회 실패(상세 탭 표시 판단 생략): %s: %s", instance.instance_key, e)
        return None
    return list(caps.exposed)


# 리니지 frontier 조회 시 IN 절 묶음 크기(파라미터 수 제한 회피)
_LINEAGE_IN_CHUNK = 500


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
    )

    # federated_urn 으로 들어와도 그래프 키는 plain URN(remote_urn)
    root = root_urn.split("::", 1)[1] if "::" in root_urn else root_urn

    # frontier 에 닿는 엣지만 단계별로 조회한다(요청마다 전체 엣지를 적재하던 방식 대체).
    src = aliased(Dataset)
    tgt = aliased(Dataset)

    async def _edges_touching(urns: set[str]) -> list[tuple[str, str, str, str | None]]:
        """URN 집합에 한쪽 끝이 닿는 로컬+미러 엣지 — (src, tgt, relation, reported_by)."""
        out: list[tuple[str, str, str, str | None]] = []
        ulist = list(urns)
        for i in range(0, len(ulist), _LINEAGE_IN_CHUNK):
            chunk = ulist[i:i + _LINEAGE_IN_CHUNK]
            for s_urn, t_urn, rel in (await session.execute(
                select(src.urn, tgt.urn, DatasetLineage.relation_type)
                .join(src, src.id == DatasetLineage.source_dataset_id)
                .join(tgt, tgt.id == DatasetLineage.target_dataset_id)
                .where(or_(src.urn.in_(chunk), tgt.urn.in_(chunk)))
            )).all():
                out.append((s_urn, t_urn, rel, None))
            for s_urn, t_urn, rel, key in (await session.execute(
                select(
                    FederationLineage.source_urn, FederationLineage.target_urn,
                    FederationLineage.relation_type, FederatedInstance.instance_key,
                )
                .join(FederatedInstance, FederatedInstance.id == FederationLineage.instance_id)
                .where(
                    FederatedInstance.status == "ACTIVE",
                    or_(
                        FederationLineage.source_urn.in_(chunk),
                        FederationLineage.target_urn.in_(chunk),
                    ),
                )
            )).all():
                out.append((s_urn, t_urn, rel, key))
        return out

    # BFS 로 depth 까지 방문 노드 수집
    edges_all: list[tuple[str, str, str, str | None]] = []
    visited = {root}
    frontier = {root}
    for _ in range(max(depth, 0)):
        touching = await _edges_touching(frontier)
        edges_all.extend(touching)
        nxt: set[str] = set()
        for s_urn, t_urn, _rel, _key in touching:
            nxt.add(s_urn)
            nxt.add(t_urn)
        nxt -= visited
        if not nxt:
            frontier = set()
            break
        visited |= nxt
        frontier = nxt
    # 마지막 단계 노드끼리의 엣지(확장하지 않고 연결만 표시)
    if frontier and depth > 0:
        edges_all.extend(await _edges_touching(frontier))

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
    logger.debug(
        "연합 리니지 그래프 구성: root=%s depth=%d nodes=%d edges=%d",
        root, depth, len(nodes), len(out_edges),
    )
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
    logger.debug(
        "리니지 노드 해석: total=%d local=%d mirror=%d",
        len(url_list), len(local), len(mirror),
    )
    return nodes


class SampleNotDisplayedError(Exception):
    """소비자(허브) 표시 선택에서 샘플이 제외된 peer 의 샘플 요청."""


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
        logger.debug("federated URN 미해석(샘플): urn=%s", federated_urn)
        return None
    instance, remote_urn = resolved

    # 허브가 이 peer 의 샘플을 표시하지 않기로 했으면 미러·LIVE 모두 제공하지 않는다.
    fields = _parse_display_fields(instance.display_fields)
    if fields is not None and "sample" not in fields:
        logger.debug(
            "샘플 표시 제외 peer 의 샘플 요청 거부: instance=%s", instance.instance_key
        )
        raise SampleNotDisplayedError(federated_urn)

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
            logger.debug("미러 샘플 제공(오프라인): urn=%s", federated_urn)
            return {**envelope, **stored}
        # has_sample 인데 파일이 없거나 읽기 실패 — LIVE 로 대체한다.
        logger.warning(
            "미러 샘플 파일 없음/손상, LIVE 조회로 대체: instance=%s urn=%s",
            instance.instance_key, remote_urn,
        )

    # 2) 폴백 — LIVE 드릴다운(peer 실시간 조회)
    logger.debug(
        "LIVE 샘플 드릴다운: instance=%s remote_urn=%s limit=%d",
        instance.instance_key, remote_urn, limit,
    )
    sample = await _call_peer_with_breaker(
        instance, lambda: client.fetch_export_sample(instance, remote_urn, limit=limit)
    )
    return {**envelope, **sample}


async def _call_peer_with_breaker(instance: FederatedInstance, coro_factory):
    """drill-down 류 단일 peer 호출을 breaker 로 감싼다.

    ``coro_factory`` 는 매 호출마다 새 coroutine 을 만드는 0-인자 콜러블.
    """
    key = instance.instance_key
    if breaker.is_open(key):
        logger.debug("circuit open 으로 드릴다운 호출 차단: instance=%s", key)
        raise breaker.CircuitOpenError(key)
    try:
        result = await coro_factory()
    except Exception as e:
        _record_peer_error(key, e)
        raise
    breaker.record_success(key)
    return result


def _record_peer_error(key: str, exc: Exception) -> None:
    """peer 호출 예외를 breaker 에 반영 — 장애만 실패로, 4xx 는 정상 응답(도달)으로 본다."""
    # 실패 자체는 호출자(검색 집계·라우터 오류 변환)가 WARNING 으로 남기므로 여기선 분류만 기록.
    logger.debug(
        "peer 호출 예외 breaker 반영: instance=%s peer_fault=%s error=%s",
        key, breaker.is_peer_fault(exc), type(exc).__name__,
    )
    if breaker.is_peer_fault(exc):
        breaker.record_failure(key)
    else:
        breaker.record_success(key)
