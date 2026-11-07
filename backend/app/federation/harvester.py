# SPDX-License-Identifier: Apache-2.0
"""HARVEST — peer 메타데이터를 로컬 미러로 가져오고 허브 모델로 재임베딩.

각 peer 의 export 데이터셋 목록을 페이지로 받아 ``federation_datasets`` 에 upsert 하고,
``source_text`` 가 바뀐 항목만 허브의 임베딩 제공자로 재임베딩해
``federation_dataset_embeddings`` 에 저장한다. 더 이상 존재하지 않는 항목은 정리(prune)한다.

peer 마다 임베딩 모델이 달라 cross-instance 유사도 비교가 깨지는 문제를 **허브 모델
재임베딩**으로 해결하는 것이 이 단계의 핵심이다.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
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
from app.federation.registry import _parse_display_fields
from app.federation.schemas import FederatedExportDataset, FederationSyncResult

logger = logging.getLogger(__name__)

# export 페이지 크기 / 안전 상한(무한 루프 방지)
_PAGE_SIZE = 200
_MAX_PAGES = 10_000
# 재임베딩 묶음 크기 — 이 건수씩 provider.embed(list) 로 호출하고 진척을 커밋한다.
_EMBED_BATCH = 32
# 샘플 가져오기 진척을 이 건수마다 커밋
_SAMPLE_PROGRESS_EVERY = 10

# 같은 프로세스 안에서 instance 별 가져오기 직렬화(수동 실행 ↔ 스케줄러 tick 겹침 방지).
# 프로세스 간(멀티 워커·레플리카) 겹침은 DB 의 RUNNING 행 검사로 막는다.
_instance_locks: dict[int, asyncio.Lock] = {}


def _aware(dt: datetime | None) -> datetime | None:
    """naive datetime(SQLite·일부 peer)을 UTC aware 로 정규화한다."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


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


async def _embed_mirror_batch(
    session: AsyncSession, provider, items: list[tuple[int, str]],
) -> int:
    """미러 데이터셋 묶음을 허브 모델로 재임베딩한다. 갱신 건수 반환.

    기존 임베딩을 한 번에 조회해, source_text 가 같고 **모델·차원도 현재 제공자와 같으면**
    건너뛴다(임베딩 모델을 바꾸면 전량 재임베딩되어 서로 다른 벡터 공간이 섞이지 않는다).
    필요한 항목만 ``provider.embed(list)`` 한 번으로 묶어 호출한다.
    """
    if not items:
        return 0
    model, prov_name, dim = provider.model_name(), provider.provider_name(), provider.dimension()
    ids = [fid for fid, _ in items]
    existing = {
        e.federation_dataset_id: e
        for e in (await session.execute(
            select(FederatedDatasetEmbedding)
            .where(FederatedDatasetEmbedding.federation_dataset_id.in_(ids))
        )).scalars().all()
    }
    todo = [
        (fid, text_) for fid, text_ in items
        if not (
            (e := existing.get(fid)) is not None
            and e.source_text == text_ and e.model_name == model and e.dimension == dim
        )
    ]
    if not todo:
        logger.debug("재임베딩 대상 없음: batch=%d", len(items))
        return 0

    logger.debug("재임베딩 묶음: batch=%d todo=%d model=%s", len(items), len(todo), model)
    vectors = await provider.embed([t for _, t in todo])
    for (fid, text_), vector in zip(todo, vectors):
        e = existing.get(fid)
        if e is not None:
            e.embedding = vector
            e.source_text = text_
            e.model_name = model
            e.provider = prov_name
            e.dimension = dim
        else:
            session.add(FederatedDatasetEmbedding(
                federation_dataset_id=fid, embedding=vector, source_text=text_,
                model_name=model, provider=prov_name, dimension=dim,
            ))
    return len(todo)


async def harvest_instance(
    session: AsyncSession, instance: FederatedInstance, full: bool = False,
) -> FederationSyncResult:
    """단일 peer 를 가져온다 — upsert + 재임베딩 + (full 시)prune + 실행 이력 기록.

    증분 동기화(기본): 마지막 **성공** 실행의 watermark 이후 변경분만 가져온다. 변경분만
    보므로 삭제·비노출 전환은 감지 못 해 prune 을 건너뛴다.

    전체 동기화(``full=True``, 성공 watermark 없음, 또는 마지막 성공한 전체 동기화 후
    ``federation_full_sync_interval_seconds`` 경과): 전량을 받아 사라진 항목을 prune 한다.

    같은 peer 의 가져오기가 이미 진행 중이면 실행하지 않고 ``status=SKIPPED`` 로 보고한다.
    예외를 던지지 않고 FederationSyncResult(status=FAILED) 로 보고한다(스케줄러 격리).
    """
    key = instance.instance_key
    lock = _instance_locks.setdefault(instance.id, asyncio.Lock())
    if lock.locked():
        logger.info("HARVEST 건너뜀 [%s]: 이미 진행 중(동일 프로세스)", key)
        return _skipped(key)
    async with lock:
        if await _has_active_run(session, instance.id, key):
            return _skipped(key)
        return await _harvest_locked(session, instance, full)


def _skipped(key: str) -> FederationSyncResult:
    """이미 진행 중이라 건너뛴 가져오기의 결과(status=SKIPPED)."""
    return FederationSyncResult(
        instance_key=key, status="SKIPPED", error="이미 가져오기가 진행 중입니다",
    )


async def _has_active_run(session: AsyncSession, instance_id: int, key: str) -> bool:
    """다른 프로세스가 진행 중인 RUNNING 실행이 있는지. 오래된 RUNNING 은 FAILED 로 정리한다."""
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=settings.federation_stale_run_seconds)
    running = (await session.execute(
        select(FederationSyncRun.id, FederationSyncRun.started_at).where(
            FederationSyncRun.instance_id == instance_id,
            FederationSyncRun.status == "RUNNING",
        )
    )).all()
    active = False
    stale_ids: list[int] = []
    for rid, started_at in running:
        started = _aware(started_at)
        if started is not None and started < stale_before:
            stale_ids.append(rid)
        else:
            active = True
    if stale_ids:
        logger.warning("HARVEST 고아 RUNNING 정리 [%s]: %s", key, stale_ids)
        await session.execute(
            update(FederationSyncRun)
            .where(FederationSyncRun.id.in_(stale_ids))
            .values(status="FAILED", error="비정상 종료(오래된 RUNNING 정리)", finished_at=now)
        )
        await session.commit()
    if active:
        logger.info("HARVEST 건너뜀 [%s]: 진행 중인 실행 있음", key)
    return active


async def _plan_sync(
    session: AsyncSession, instance_id: int, full: bool,
) -> tuple[bool, datetime | None, str]:
    """(is_full, 증분 기준 시각, 사유)를 정한다."""
    # 우선순위: 명시 요청 → 성공 watermark 부재 → 주기적 전체 동기화 → 증분
    if full:
        return True, None, "요청"
    last_ok = (await session.execute(
        select(FederationSyncRun.watermark)
        .where(
            FederationSyncRun.instance_id == instance_id,
            FederationSyncRun.status == "SUCCESS",
            FederationSyncRun.watermark.is_not(None),
        )
        .order_by(FederationSyncRun.started_at.desc(), FederationSyncRun.id.desc())
        .limit(1)
    )).scalar()
    if last_ok is None:
        return True, None, "성공 watermark 없음"

    interval = settings.federation_full_sync_interval_seconds
    if interval > 0:
        last_full = (await session.execute(
            select(func.max(FederationSyncRun.started_at)).where(
                FederationSyncRun.instance_id == instance_id,
                FederationSyncRun.status == "SUCCESS",
                FederationSyncRun.is_full.is_(True),
            )
        )).scalar()
        last_full = _aware(last_full)
        now = datetime.now(timezone.utc)
        if last_full is None or (now - last_full).total_seconds() >= interval:
            return True, None, "주기적 전체 동기화"

    since = _aware(last_ok) - timedelta(seconds=settings.federation_watermark_overlap_seconds)
    return False, since, "증분"


async def _harvest_locked(
    session: AsyncSession, instance: FederatedInstance, full: bool,
) -> FederationSyncResult:
    """harvest_instance 본체 — instance 단위 잠금을 잡은 상태에서 호출된다."""
    from app.federation import client

    # rollback 은 세션의 ORM 객체를 모두 만료시키고, AsyncSession 에서 만료 속성 접근은
    # lazy load(MissingGreenlet)를 일으킨다. 실패 경로에서 쓸 식별자는 미리 지역 변수로 잡아 둔다.
    key = instance.instance_key

    is_full, since, reason = await _plan_sync(session, instance.id, full)
    logger.info("HARVEST 시작 [%s]: %s (%s)", key, "full" if is_full else "incremental", reason)
    logger.debug("HARVEST 증분 기준 시각 [%s]: since=%s", key, since)
    started_mono = time.monotonic()

    run = FederationSyncRun(instance_id=instance.id, status="RUNNING", is_full=is_full)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    run_id = run.id

    seen_urns: set[str] = set()
    upserted = 0
    embedded = 0
    # 이번 실행에서 관측한 peer updated_at 최댓값 — 성공 시 다음 증분의 watermark
    max_updated: datetime | None = None
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
            existing = await _preload_mirror_rows(
                session, instance.id, [ds.urn for ds in page.items],
            )
            for ds in page.items:
                if len(ds.urn) > 500:  # 미러 식별자 컬럼 한도 — 잘라 쓸 수 없으므로 건너뛴다
                    logger.warning("HARVEST 항목 건너뜀 [%s]: URN 길이 초과(%d)", key, len(ds.urn))
                    continue
                changed = await _upsert_dataset(session, instance, ds, existing)
                seen_urns.add(ds.urn)
                upd = _aware(ds.updated_at)
                if upd is not None and (max_updated is None or upd > max_updated):
                    max_updated = upd
                if changed:
                    upserted += 1
            # 진행률 관측용 — 페이지마다 RUNNING 행의 seen/phase 를 갱신(클라가 폴링)
            run.datasets_seen = len(seen_urns)
            run.phase_done = len(seen_urns)
            await session.commit()
            offset += len(page.items)
            logger.debug(
                "HARVEST 페이지 처리 [%s]: offset=%d total=%d seen=%d upserted=%d",
                key, offset, page.total, len(seen_urns), upserted,
            )
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
        provider = await get_provider()
        if provider is not None:
            for start in range(0, len(rows), _EMBED_BATCH):
                chunk = rows[start:start + _EMBED_BATCH]
                embedded += await _embed_mirror_batch(
                    session, provider, [(fid, t) for fid, t in chunk if t],
                )
                # 스캔 진척(분모=전체 행)을 묶음마다 커밋해 폴링에 보이게 한다.
                run.phase_done = start + len(chunk)
                run.datasets_embedded = embedded
                await session.commit()
        else:
            # 임베딩 제공자 미설정 — 미러는 저장하되 시맨틱 검색 대상에서는 빠진다.
            logger.warning("임베딩 제공자 없음 — 재임베딩 생략 [%s]: rows=%d", key, len(rows))
            run.phase_done = len(rows)
            await session.commit()

        # --- 2.5단계: 샘플 미러링(SAMPLE) — 선택적, best-effort ---
        await _harvest_samples(session, instance, run, is_full)

        # --- 3단계: 정리/리니지(FINALIZE) ---
        run.phase = "FINALIZE"
        run.phase_total = 1
        run.phase_done = 0
        await session.commit()
        # prune 은 전체 동기화일 때만(증분은 absence ≠ deletion).
        # offset 페이지네이션 중 peer 에서 삭제가 일어나면 항목을 건너뛸 수 있으므로, 관측 수가
        # 첫 페이지의 전체 수보다 적으면 멀쩡한 미러를 지우지 않도록 prune 을 보류한다.
        pruned = 0
        if is_full:
            if len(seen_urns) >= (run.datasets_total or 0):
                pruned = await _prune_missing(session, instance, seen_urns)
            else:
                logger.warning(
                    "HARVEST prune 보류 [%s]: 관측 %d < 전체 %d (가져오는 중 peer 변경 추정)",
                    key, len(seen_urns), run.datasets_total,
                )
        await session.commit()

        # 리니지 엣지 가져오기(전량 교체) — cross-instance stitching 용. 실패해도 본 가져오기는 성공 처리.
        # SAVEPOINT 로 감싸 실패 시 리니지 변경만 되돌린다(전체 rollback 은 객체를 만료시킴).
        try:
            async with session.begin_nested():
                await _harvest_lineage(session, instance)
            await session.commit()
        except Exception as le:  # noqa: BLE001 — 리니지는 best-effort
            logger.warning("리니지 가져오기 건너뜀 [%s]: %s", key, le)

        run.status = "SUCCESS"
        # watermark 는 성공 실행에서만 전진. 겹침 구간 재수신으로 후퇴하지 않도록 기존 값과 비교한다.
        prev_wm = (
            since + timedelta(seconds=settings.federation_watermark_overlap_seconds)
            if since is not None else None
        )
        candidates = [w for w in (prev_wm, max_updated) if w is not None]
        run.watermark = max(candidates) if candidates else None
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
            "HARVEST 완료 [%s] (%s): seen=%d upserted=%d embedded=%d pruned=%d elapsed=%.1fs",
            key, "full" if is_full else "incremental",
            len(seen_urns), upserted, embedded, pruned, time.monotonic() - started_mono,
        )
        return FederationSyncResult(
            instance_key=key, status="SUCCESS",
            datasets_seen=len(seen_urns), datasets_upserted=upserted,
            datasets_embedded=embedded, datasets_pruned=pruned,
        )
    except Exception as e:  # noqa: BLE001 — peer 단위 격리
        await session.rollback()
        logger.warning("HARVEST 실패 [%s]: %s", key, e)
        message = describe_harvest_error(e)
        # 실행 이력에 실패 기록 (별도 트랜잭션)
        run_row = (await session.execute(
            select(FederationSyncRun).where(FederationSyncRun.id == run_id)
        )).scalars().first()
        if run_row is not None:
            run_row.status = "FAILED"
            run_row.error = message[:2000]
            run_row.finished_at = datetime.now(timezone.utc)
            await session.commit()
        return FederationSyncResult(
            instance_key=key, status="FAILED", error=message,
        )


def describe_harvest_error(e: Exception) -> str:
    """가져오기 실패 사유를 화면용 한 줄로 — httpx 원문(전체 URL·MDN 안내 링크) 대신 상태 코드와 조치.

    이력(sync-runs)·관측성·인스턴스 행에 그대로 표시되므로 사람이 읽을 수 있어야 한다.
    """
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403):
            return f"peer 인증 실패(HTTP {code}) — 서비스 토큰을 확인하세요"
        detail = None
        try:
            detail = e.response.json().get("detail")
        except Exception:  # noqa: BLE001 — 비정상 응답이면 사유 없이
            pass
        return f"peer 응답 오류(HTTP {code})" + (f": {detail}" if detail else "")
    if isinstance(e, httpx.TimeoutException):
        return "peer 응답 시간 초과"
    if isinstance(e, httpx.RequestError):
        return f"peer 연결 실패: {e}"
    return str(e) or e.__class__.__name__


async def _preload_mirror_rows(
    session: AsyncSession, instance_id: int, urns: list[str],
) -> dict[str, FederatedDataset]:
    """페이지의 기존 미러 행을 한 번에 조회(remote_urn → 행) — 행마다 SELECT 하던 N+1 제거."""
    if not urns:
        return {}
    rows = (await session.execute(
        select(FederatedDataset).where(
            FederatedDataset.instance_id == instance_id,
            FederatedDataset.remote_urn.in_(urns),
        )
    )).scalars().all()
    return {r.remote_urn: r for r in rows}


def _clip(v: str | None, n: int) -> str | None:
    """peer 값을 미러 컬럼 길이에 맞춘다(초과 시 PostgreSQL 이 가져오기 전체를 실패시킴)."""
    return v[:n] if isinstance(v, str) else v


async def _upsert_dataset(
    session: AsyncSession, instance: FederatedInstance, ds: FederatedExportDataset,
    existing: dict[str, FederatedDataset] | None = None,
) -> bool:
    """미러 데이터셋 1건 upsert. source_text 가 바뀌었으면 True(재임베딩 후보).

    ``existing`` 이 주어지면(페이지 단위 선조회) 그 안에서 기존 행을 찾고, 새로 만든 행도
    등록해 같은 페이지 안의 중복 URN 이 두 번 insert 되지 않게 한다.
    """
    federated_urn = f"{instance.instance_key}::{ds.urn}"
    source_text = build_source_text(ds)

    if existing is None:
        existing = await _preload_mirror_rows(session, instance.id, [ds.urn])
    row = existing.get(ds.urn)

    changed = row is None or row.source_text != source_text
    if row is None:
        row = FederatedDataset(instance_id=instance.id, remote_urn=ds.urn)
        session.add(row)
        existing[ds.urn] = row
    row.federated_urn = federated_urn
    row.name = _clip(ds.name, 255)
    row.display_name = _clip(ds.display_name, 255)
    row.datasource_name = _clip(ds.datasource_name, 200)
    row.datasource_type = _clip(ds.datasource_type, 100)
    row.summary = _clip(ds.summary, 200)
    row.description = ds.description
    row.qualified_name = _clip(ds.qualified_name, 500)
    row.origin = _clip(ds.origin, 50)
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
    logger.info("미러 데이터셋 prune [%s]: %d건", instance.instance_key, len(stale_ids))
    # 미러 행과 함께 저장된 샘플 파일도 정리
    for fid in stale_ids:
        samples.delete_sample(instance.id, fid)
    await session.execute(
        delete(FederatedDataset).where(FederatedDataset.id.in_(stale_ids))
    )
    return len(stale_ids)


async def _purge_samples(
    session: AsyncSession, instance: FederatedInstance, reason: str,
) -> int:
    """instance 의 저장된 미러 샘플을 모두 삭제하고 has_sample 을 내린다. 삭제 수 반환."""
    ids = (await session.execute(
        select(FederatedDataset.id).where(
            FederatedDataset.instance_id == instance.id,
            FederatedDataset.has_sample.is_(True),
        )
    )).scalars().all()
    # 행 플래그와 무관하게 디렉터리째 정리(플래그 누락으로 남은 파일까지)
    samples.delete_instance_samples(instance.id)
    if ids:
        await session.execute(
            update(FederatedDataset)
            .where(FederatedDataset.id.in_(ids))
            .values(has_sample=False)
        )
        await session.commit()
        logger.info("미러 샘플 정리 [%s]: %d건 (%s)", instance.instance_key, len(ids), reason)
    return len(ids)


async def _harvest_samples(
    session: AsyncSession,
    instance: FederatedInstance,
    run: FederationSyncRun,
    is_full: bool,
) -> None:
    """미러 데이터셋의 샘플 데이터를 peer 에서 받아 로컬(federation/samples)에 저장한다.

    조건: 설정 켜짐 + 소비자가 ``sample`` 선택 + peer 가 ``sample`` 노출. best-effort.
    증분: 아직 샘플이 없는(``has_sample=False``) 행만. 전체 동기화: 전량 갱신.

    조건 중 하나라도 확실히 거짓이면(설정 꺼짐·표시 선택 해제·peer 비노출) 이전에 저장한
    샘플을 모두 삭제한다 — peer 가 PII 우려로 샘플 노출을 끈 뒤에도 허브가 예전 실데이터를
    계속 제공하지 않도록. capabilities 조회 실패처럼 판단 불가일 때는 기존 샘플을 유지한다.
    """
    from app.federation import client

    if not settings.federation_harvest_samples:
        await _purge_samples(session, instance, "허브 샘플 가져오기 비활성")
        return
    fields = _parse_display_fields(instance.display_fields)  # None=전부 표시
    if fields is not None and "sample" not in fields:
        await _purge_samples(session, instance, "소비자 표시 선택에서 샘플 제외")
        return
    # peer 가 sample 을 노출하지 않으면 스킵(불필요한 호출 방지)
    try:
        caps = await client.fetch_export_capabilities(instance)
        if not capabilities.is_exposed("sample", caps.exposed):
            await _purge_samples(session, instance, "peer 가 샘플 비노출")
            return
    except Exception as e:  # noqa: BLE001 — capabilities 조회 실패 시 샘플 스킵
        # 판단 불가 — 기존 샘플은 유지하고 이번 실행의 샘플 단계만 건너뛴다.
        logger.warning(
            "peer capabilities 조회 실패 — 샘플 가져오기 건너뜀 [%s]: %s",
            instance.instance_key, e,
        )
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
        except Exception as e:  # noqa: BLE001 — 개별 샘플 실패는 '없음' 으로 처리
            logger.debug(
                "샘플 가져오기 실패(없음 처리) [%s]: urn=%s error=%s",
                instance.instance_key, remote_urn, e,
            )
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
    peer_ids = (await session.execute(
        select(FederatedInstance.id).where(
            FederatedInstance.status == "ACTIVE",
            FederatedInstance.mode.in_(("HARVEST", "HYBRID")),
        )
    )).scalars().all()
    logger.debug("HARVEST 대상 peer: count=%d full=%s", len(peer_ids), full)
    results: list[FederationSyncResult] = []
    for pid in peer_ids:
        # 앞 peer 실패의 rollback 으로 객체가 만료됐을 수 있으므로 매번 새로 로드한다.
        peer = await session.get(FederatedInstance, pid, populate_existing=True)
        if peer is None:
            # 조회 직후 삭제된 peer — 건너뛴다.
            logger.debug("HARVEST 대상 peer 사라짐: id=%s", pid)
            continue
        results.append(await harvest_instance(session, peer, full=full))
    return results
