# SPDX-License-Identifier: Apache-2.0
"""HARVEST — peer 메타데이터를 로컬 미러로 가져오고 허브 모델로 재임베딩.

각 peer 의 export 데이터셋 목록을 페이지로 받아 ``federation_datasets`` 에 upsert 하고,
``source_text`` 가 바뀐 항목만 허브의 임베딩 제공자로 재임베딩해
``federation_dataset_embeddings`` 에 저장한다. 더 이상 존재하지 않는 항목은 정리(prune)한다.

peer 마다 임베딩 모델이 달라 cross-instance 유사도 비교가 깨지는 문제를 **허브 모델
재임베딩**으로 해결하는 것이 이 단계의 핵심이다.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.embedding.registry import get_provider
from app.federation import capabilities, samples
from app.federation.models import (
    FederatedDataset,
    FederatedDatasetEmbedding,
    FederatedInstance,
    FederationLineage,
    FederationSyncRun,
)
from app.federation.schemas import FederatedExportDataset, FederationSyncResult
from app.federation.service import _parse_display_fields

logger = logging.getLogger(__name__)

# export 페이지 크기 / 안전 상한(무한 루프 방지)
_PAGE_SIZE = 200
_MAX_PAGES = 10_000
# 재임베딩 진척을 이 건수마다 커밋(폴링 가시성 ↔ 커밋 오버헤드 균형)
_EMBED_PROGRESS_EVERY = 20
# 샘플 가져오기 진척을 이 건수마다 커밋
_SAMPLE_PROGRESS_EVERY = 10


def build_source_text(ds: FederatedExportDataset) -> str:
    """export 데이터셋 → 임베딩 입력 텍스트.

    ``app.embedding.service.build_source_text`` 와 동일한 구성으로, 허브 모델
    재임베딩이 로컬 데이터셋과 같은 의미 공간에 놓이도록 한다.
    """
    parts = [ds.name]
    if ds.summary:
        parts.append(ds.summary)
    if ds.description:
        parts.append(ds.description)
    if ds.qualified_name:
        parts.append(ds.qualified_name)
    if ds.datasource_name:
        parts.append(ds.datasource_name)
    if ds.datasource_type:
        parts.append(ds.datasource_type)
    field_lines = []
    for f in ds.fields:
        line = f"{f.field_path} {f.field_type or ''}".strip()
        if f.description:
            line += f" -- {f.description}"
        field_lines.append(line)
    if field_lines:
        parts.append("schema: " + "; ".join(field_lines))
    return " | ".join(p for p in parts if p)


async def _embed_mirror(
    session: AsyncSession, fed_dataset_id: int, source_text: str,
) -> bool:
    """미러 데이터셋을 허브 모델로 재임베딩(변경 없으면 건너뜀). 갱신 시 True."""
    provider = await get_provider()
    if provider is None:
        return False

    existing = (await session.execute(
        select(FederatedDatasetEmbedding)
        .where(FederatedDatasetEmbedding.federation_dataset_id == fed_dataset_id)
    )).scalars().first()
    if existing is not None and existing.source_text == source_text:
        return False  # 변경 없음

    vectors = await provider.embed([source_text])
    vector = vectors[0]
    if existing is not None:
        existing.embedding = vector
        existing.source_text = source_text
        existing.model_name = provider.model_name()
        existing.provider = provider.provider_name()
        existing.dimension = provider.dimension()
    else:
        session.add(FederatedDatasetEmbedding(
            federation_dataset_id=fed_dataset_id,
            embedding=vector,
            source_text=source_text,
            model_name=provider.model_name(),
            provider=provider.provider_name(),
            dimension=provider.dimension(),
        ))
    return True


async def harvest_instance(
    session: AsyncSession, instance: FederatedInstance, full: bool = False,
) -> FederationSyncResult:
    """단일 peer 를 가져온다 — upsert + 재임베딩 + (full 시)prune + 실행 이력 기록.

    증분 동기화(기본): 마지막 가져오기의 watermark(max remote_updated_at) 이후 변경분만
    가져온다. 변경분만 보므로 삭제는 감지 못 해 **prune 을 건너뛴다**.

    전체 동기화(``full=True`` 또는 watermark 없음): 전량을 받아 사라진 항목을 prune 한다.

    예외를 던지지 않고 FederationSyncResult(status=FAILED) 로 보고한다(스케줄러 격리).
    """
    from app.federation import client

    # watermark 계산 — 증분일 때만. NULL/없음이면 전체 동기화로 승격.
    since = None
    if not full:
        since = (await session.execute(
            select(func.max(FederatedDataset.remote_updated_at))
            .where(FederatedDataset.instance_id == instance.id)
        )).scalar()
    is_full = full or since is None

    run = FederationSyncRun(instance_id=instance.id, status="RUNNING")
    session.add(run)
    await session.commit()
    await session.refresh(run)

    seen_urns: set[str] = set()
    upserted = 0
    embedded = 0
    try:
        # --- 1단계: 가져오기(FETCH) — peer 페이지를 받아 upsert ---
        run.phase = "FETCH"
        offset = 0
        for _ in range(_MAX_PAGES):
            page = await client.fetch_export_datasets(
                instance, limit=_PAGE_SIZE, offset=offset, updated_after=since,
            )
            if not page.items:
                break
            if offset == 0:
                run.datasets_total = page.total          # 첫 페이지에서 전체 수 확정
                run.phase_total = page.total
            for ds in page.items:
                changed = await _upsert_dataset(session, instance, ds)
                seen_urns.add(ds.urn)
                if changed:
                    upserted += 1
            # 진행률 관측용 — 페이지마다 RUNNING 행의 seen/phase 를 갱신(클라가 폴링)
            run.datasets_seen = len(seen_urns)
            run.phase_done = len(seen_urns)
            await session.commit()
            offset += len(page.items)
            if offset >= page.total or len(page.items) < _PAGE_SIZE:
                break

        # --- 2단계: 재임베딩(EMBED) — source_text 가 바뀐 항목만 ---
        rows = (await session.execute(
            select(FederatedDataset.id, FederatedDataset.source_text)
            .where(FederatedDataset.instance_id == instance.id)
        )).all()
        run.phase = "EMBED"
        run.phase_total = len(rows)
        run.phase_done = 0
        await session.commit()
        for i, (fid, source_text) in enumerate(rows, start=1):
            if source_text and await _embed_mirror(session, fid, source_text):
                embedded += 1
            # 스캔 진척(분모=전체 행)을 주기적으로 커밋해 폴링에 보이게 한다.
            if i % _EMBED_PROGRESS_EVERY == 0 or i == len(rows):
                run.phase_done = i
                run.datasets_embedded = embedded
                await session.commit()

        # --- 2.5단계: 샘플 미러링(SAMPLE) — 선택적, best-effort ---
        await _harvest_samples(session, instance, run, is_full)

        # --- 3단계: 정리/리니지(FINALIZE) ---
        run.phase = "FINALIZE"
        run.phase_total = 1
        run.phase_done = 0
        await session.commit()
        # prune 은 전체 동기화일 때만(증분은 absence ≠ deletion)
        pruned = await _prune_missing(session, instance, seen_urns) if is_full else 0
        await session.commit()

        # 리니지 엣지 가져오기(전량 교체) — cross-instance stitching 용. 실패해도 본 가져오기는 성공 처리.
        try:
            await _harvest_lineage(session, instance)
            await session.commit()
        except Exception as le:  # noqa: BLE001 — 리니지는 best-effort
            await session.rollback()
            logger.warning("리니지 가져오기 건너뜀 [%s]: %s", instance.instance_key, le)

        run.status = "SUCCESS"
        run.datasets_seen = len(seen_urns)
        run.datasets_upserted = upserted
        run.datasets_embedded = embedded
        run.datasets_pruned = pruned
        run.phase = "FINALIZE"
        run.phase_total = 1
        run.phase_done = 1
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info(
            "HARVEST 완료 [%s] (%s): seen=%d upserted=%d embedded=%d pruned=%d",
            instance.instance_key, "full" if is_full else "incremental",
            len(seen_urns), upserted, embedded, pruned,
        )
        return FederationSyncResult(
            instance_key=instance.instance_key, status="SUCCESS",
            datasets_seen=len(seen_urns), datasets_upserted=upserted,
            datasets_embedded=embedded, datasets_pruned=pruned,
        )
    except Exception as e:  # noqa: BLE001 — peer 단위 격리
        await session.rollback()
        logger.warning("HARVEST 실패 [%s]: %s", instance.instance_key, e)
        # 실행 이력에 실패 기록 (별도 트랜잭션)
        run_row = (await session.execute(
            select(FederationSyncRun).where(FederationSyncRun.id == run.id)
        )).scalars().first()
        if run_row is not None:
            run_row.status = "FAILED"
            run_row.error = str(e)[:2000]
            run_row.finished_at = datetime.now(timezone.utc)
            await session.commit()
        return FederationSyncResult(
            instance_key=instance.instance_key, status="FAILED", error=str(e),
        )


async def _upsert_dataset(
    session: AsyncSession, instance: FederatedInstance, ds: FederatedExportDataset,
) -> bool:
    """미러 데이터셋 1건 upsert. source_text 가 바뀌었으면 True(재임베딩 후보)."""
    federated_urn = f"{instance.instance_key}::{ds.urn}"
    source_text = build_source_text(ds)

    row = (await session.execute(
        select(FederatedDataset).where(
            FederatedDataset.instance_id == instance.id,
            FederatedDataset.remote_urn == ds.urn,
        )
    )).scalars().first()

    changed = row is None or row.source_text != source_text
    if row is None:
        row = FederatedDataset(instance_id=instance.id, remote_urn=ds.urn)
        session.add(row)
    row.federated_urn = federated_urn
    row.name = ds.name
    row.display_name = ds.display_name
    row.datasource_name = ds.datasource_name
    row.datasource_type = ds.datasource_type
    row.summary = ds.summary
    row.description = ds.description
    row.qualified_name = ds.qualified_name
    row.origin = ds.origin
    row.field_count = len(ds.fields)
    row.source_text = source_text
    row.remote_created_at = ds.created_at
    row.remote_updated_at = ds.updated_at
    return changed


async def _prune_missing(
    session: AsyncSession, instance: FederatedInstance, seen_urns: set[str],
) -> int:
    """이번 가져오기에서 보이지 않은 미러 데이터셋을 삭제(임베딩은 CASCADE). 삭제 수 반환."""
    existing = (await session.execute(
        select(FederatedDataset.id, FederatedDataset.remote_urn)
        .where(FederatedDataset.instance_id == instance.id)
    )).all()
    stale_ids = [fid for fid, urn in existing if urn not in seen_urns]
    if not stale_ids:
        return 0
    # 미러 행과 함께 저장된 샘플 파일도 정리
    for fid in stale_ids:
        samples.delete_sample(instance.id, fid)
    await session.execute(
        delete(FederatedDataset).where(FederatedDataset.id.in_(stale_ids))
    )
    return len(stale_ids)


async def _harvest_samples(
    session: AsyncSession,
    instance: FederatedInstance,
    run: FederationSyncRun,
    is_full: bool,
) -> None:
    """미러 데이터셋의 샘플 데이터를 peer 에서 받아 로컬(federation/samples)에 저장한다.

    조건: 설정 켜짐 + 소비자가 ``sample`` 선택 + peer 가 ``sample`` 노출. best-effort.
    증분: 아직 샘플이 없는(``has_sample=False``) 행만. 전체 동기화: 전량 갱신.
    """
    from app.federation import client

    if not settings.federation_harvest_samples:
        return
    fields = _parse_display_fields(instance.display_fields)  # None=전부 표시
    if fields is not None and "sample" not in fields:
        return
    # peer 가 sample 을 노출하지 않으면 스킵(불필요한 호출 방지)
    try:
        caps = await client.fetch_export_capabilities(instance)
        if not capabilities.is_exposed("sample", caps.exposed):
            return
    except Exception:  # noqa: BLE001 — capabilities 조회 실패 시 샘플 스킵
        return

    q = select(FederatedDataset.id, FederatedDataset.remote_urn).where(
        FederatedDataset.instance_id == instance.id
    )
    if not is_full:
        q = q.where(FederatedDataset.has_sample.is_(False))  # 증분: 아직 없는 것만
    rows = (await session.execute(q.order_by(FederatedDataset.id))).all()

    run.phase = "SAMPLE"
    run.phase_total = len(rows)
    run.phase_done = 0
    await session.commit()
    if not rows:
        return

    stored = 0  # 샘플 파일을 저장한 데이터셋 수(관측용)
    for i, (fid, remote_urn) in enumerate(rows, start=1):
        ok = False
        try:
            sample = await client.fetch_export_sample(
                instance, remote_urn, limit=settings.federation_sample_limit
            )
            if sample and sample.get("columns"):
                samples.write_sample(instance.id, fid, sample)
                ok = True
        except Exception:  # noqa: BLE001 — 개별 샘플 실패는 '없음' 으로 처리
            ok = False
        if not ok:
            samples.delete_sample(instance.id, fid)
        else:
            stored += 1
        await session.execute(
            update(FederatedDataset)
            .where(FederatedDataset.id == fid)
            .values(has_sample=ok)
        )
        if i % _SAMPLE_PROGRESS_EVERY == 0 or i == len(rows):
            run.phase_done = i
            await session.commit()
    await session.commit()
    logger.info(
        "샘플 가져오기 [%s]: %d/%d 저장", instance.instance_key, stored, len(rows)
    )


async def _harvest_lineage(
    session: AsyncSession, instance: FederatedInstance,
) -> int:
    """peer 리니지 엣지를 가져와 이 instance 의 미러를 전량 교체한다. 적재 수 반환."""
    from app.federation import client

    export = await client.fetch_export_lineage(instance)
    await session.execute(
        delete(FederationLineage).where(FederationLineage.instance_id == instance.id)
    )
    for e in export.edges:
        session.add(FederationLineage(
            instance_id=instance.id,
            source_urn=e.source_urn, target_urn=e.target_urn,
            relation_type=e.relation_type, lineage_source=e.lineage_source,
            description=e.description,
        ))
    logger.info("리니지 가져오기 [%s]: %d 엣지", instance.instance_key, len(export.edges))
    return len(export.edges)


async def harvest_all(
    session: AsyncSession, full: bool = False,
) -> list[FederationSyncResult]:
    """ACTIVE 이며 미러 모드(HARVEST/HYBRID)인 모든 peer 를 가져온다."""
    peers = (await session.execute(
        select(FederatedInstance).where(
            FederatedInstance.status == "ACTIVE",
            FederatedInstance.mode.in_(("HARVEST", "HYBRID")),
        )
    )).scalars().all()
    results: list[FederationSyncResult] = []
    for peer in peers:
        results.append(await harvest_instance(session, peer, full=full))
    return results
