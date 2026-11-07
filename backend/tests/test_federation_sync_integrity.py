# SPDX-License-Identifier: Apache-2.0
"""페더레이션 HARVEST 동기화 정합성 테스트 (in-memory SQLite, 자기완결형).

검증:
- 부분 실패(중간 페이지 오류)는 watermark 를 전진시키지 않는다 → 다음 증분이 같은 기준으로 재조회
- 마지막 성공한 전체 동기화 후 주기가 지나면 증분 요청이 전체 동기화(prune)로 승격
- 전체 동기화에서 관측 수 < peer 보고 전체 수면 prune 보류(offset 밀림 오삭제 방지)
- 진행 중 RUNNING 이 있으면 SKIPPED, 오래된 RUNNING 은 FAILED 로 정리 후 실행
- 샘플 미노출 전환 시 저장된 미러 샘플 정리, 표시 선택에서 샘플 제외 시 서빙 차단

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_sync_integrity
    # 또는: pytest tests/test_federation_sync_integrity.py
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.federation.models as fm
from app.core.config import settings
from app.federation import client, harvester, samples, service
from app.federation import schemas as s

# federation_lineage 는 일부러 만들지 않는다 — 리니지 단계가 DB 쓰기 중 실패해도 SAVEPOINT 로
# 격리되어 본 가져오기가 SUCCESS 로 끝나는지(rollback 후 만료 객체 접근 회귀)를 함께 검증한다.
_TABLES = ["federation_instances", "federation_datasets", "federation_sync_runs"]
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [fm.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: fm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


def _ds(i: int, updated: datetime = _T0) -> s.FederatedExportDataset:
    return s.FederatedExportDataset(
        urn=f"mysql-1.t{i}.dataset", name=f"t{i}", datasource_name="mysql-1",
        datasource_type="mysql", updated_at=updated, fields=[],
    )


def _page(items, total, offset=0) -> s.FederatedExportDatasetsResponse:
    return s.FederatedExportDatasetsResponse(items=items, total=total, limit=200, offset=offset)


class _Patched:
    """client/provider/설정 monkeypatch — 네트워크 없이 harvest 를 돌린다."""

    def __init__(self) -> None:
        self.orig = {
            "fetch": client.fetch_export_datasets,
            "lineage": client.fetch_export_lineage,
            "caps": client.fetch_export_capabilities,
            "hp": harvester.get_provider,
            "full_iv": settings.federation_full_sync_interval_seconds,
            "samples": settings.federation_harvest_samples,
            "data_dir": settings.data_dir,
        }

    def __enter__(self):
        async def no_provider():
            return None

        async def lineage(instance, *a, **kw):
            return s.FederatedExportLineageResponse(edges=[], total=0)

        harvester.get_provider = no_provider
        client.fetch_export_lineage = lineage
        settings.federation_harvest_samples = False
        settings.federation_full_sync_interval_seconds = 86400
        return self

    def __exit__(self, *exc):
        client.fetch_export_datasets = self.orig["fetch"]
        client.fetch_export_lineage = self.orig["lineage"]
        client.fetch_export_capabilities = self.orig["caps"]
        harvester.get_provider = self.orig["hp"]
        settings.federation_full_sync_interval_seconds = self.orig["full_iv"]
        settings.federation_harvest_samples = self.orig["samples"]
        settings.data_dir = self.orig["data_dir"]


async def _new_peer(Session, key="team-a", display_fields=None) -> int:  # noqa: N803
    async with Session() as db:
        peer = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key=key, name=key, base_url="https://a.internal", mode="HARVEST",
            display_fields=display_fields,
        ))
        return peer.id


async def _harvest(Session, peer_id, full=False) -> s.FederationSyncResult:  # noqa: N803
    async with Session() as db:
        inst = await service.get_instance(db, peer_id)
        return await harvester.harvest_instance(db, inst, full=full)


async def _mirror_count(Session, peer_id) -> int:  # noqa: N803
    async with Session() as db:
        return (await db.execute(
            select(func.count()).select_from(fm.FederatedDataset)
            .where(fm.FederatedDataset.instance_id == peer_id)
        )).scalar()


async def _test_partial_failure_keeps_watermark() -> None:
    session_factory = await _make_session()
    peer_id = await _new_peer(session_factory)
    seen_after: list = []

    with _Patched():
        # 1차 full 성공 → watermark = T0
        async def full(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            seen_after.append(updated_after)
            return _page([_ds(1), _ds(2)], 2) if offset == 0 else _page([], 2, offset)

        client.fetch_export_datasets = full
        assert (await _harvest(session_factory, peer_id)).status == "SUCCESS"

        # 2차 증분: 1페이지(최근 수정분 포함) 커밋 후 2페이지에서 실패
        newer = _T0 + timedelta(days=10)

        async def partial(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            seen_after.append(updated_after)
            if offset == 0:
                return _page([_ds(i, newer) for i in range(3, 3 + limit)], limit + 5)
            raise RuntimeError("peer timeout")

        client.fetch_export_datasets = partial
        r2 = await _harvest(session_factory, peer_id)
        assert r2.status == "FAILED"

        # 3차 증분: 기준 시각은 여전히 1차 성공의 watermark(T0 - overlap) — 부분 실패분은 무시
        seen_after.clear()

        async def empty(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            seen_after.append(updated_after)
            return _page([], 0, offset)

        client.fetch_export_datasets = empty
        assert (await _harvest(session_factory, peer_id)).status == "SUCCESS"
        overlap = timedelta(seconds=settings.federation_watermark_overlap_seconds)
        assert seen_after[0] is not None
        assert seen_after[0].replace(tzinfo=None) == (_T0 - overlap).replace(tzinfo=None)

        # 변경분 없는 성공 실행은 watermark 를 유지(후퇴·소실 없음)
        async with session_factory() as db:
            wm = (await db.execute(
                select(fm.FederationSyncRun.watermark)
                .where(fm.FederationSyncRun.status == "SUCCESS")
                .order_by(fm.FederationSyncRun.id.desc()).limit(1)
            )).scalar()
        assert wm.replace(tzinfo=None) == _T0.replace(tzinfo=None)


async def _test_periodic_full_and_prune_guard() -> None:
    session_factory = await _make_session()
    peer_id = await _new_peer(session_factory)
    seen_after: list = []

    with _Patched():
        async def three(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            seen_after.append(updated_after)
            return _page([_ds(1), _ds(2), _ds(3)], 3) if offset == 0 else _page([], 3, offset)

        client.fetch_export_datasets = three
        await _harvest(session_factory, peer_id)
        assert await _mirror_count(session_factory, peer_id) == 3

        # 전체 동기화 주기 경과 → 증분 요청이 full 로 승격되어 사라진 t3 를 prune
        async with session_factory() as db:
            run = (await db.execute(select(fm.FederationSyncRun))).scalars().first()
            run.started_at = datetime.now(timezone.utc) - timedelta(days=2)
            await db.commit()

        seen_after.clear()

        async def two(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            seen_after.append(updated_after)
            return _page([_ds(1), _ds(2)], 2) if offset == 0 else _page([], 2, offset)

        client.fetch_export_datasets = two
        r = await _harvest(session_factory, peer_id)            # full 인자 없음(증분 요청)
        assert seen_after[0] is None                            # full 로 승격됨
        assert r.datasets_pruned == 1
        assert await _mirror_count(session_factory, peer_id) == 2

        # prune 보류: peer 가 전체 3건이라 보고했는데 1건만 관측 → 미러를 지우지 않는다
        async def short(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            return _page([_ds(1)], 3) if offset == 0 else _page([], 3, offset)

        client.fetch_export_datasets = short
        r = await _harvest(session_factory, peer_id, full=True)
        assert r.status == "SUCCESS" and r.datasets_pruned == 0
        assert await _mirror_count(session_factory, peer_id) == 2


async def _test_concurrency_guard() -> None:
    session_factory = await _make_session()
    peer_id = await _new_peer(session_factory)

    with _Patched():
        async def one(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            return _page([_ds(1)], 1) if offset == 0 else _page([], 1, offset)

        client.fetch_export_datasets = one

        # 다른 프로세스가 진행 중(최근 RUNNING) → SKIPPED, 새 실행 행을 만들지 않음
        async with session_factory() as db:
            db.add(fm.FederationSyncRun(instance_id=peer_id, status="RUNNING"))
            await db.commit()
        r = await _harvest(session_factory, peer_id)
        assert r.status == "SKIPPED"
        async with session_factory() as db:
            n_runs = (await db.execute(select(func.count()).select_from(fm.FederationSyncRun))).scalar()
        assert n_runs == 1

        # 오래된 RUNNING(고아) → FAILED 로 정리하고 정상 실행
        async with session_factory() as db:
            run = (await db.execute(select(fm.FederationSyncRun))).scalars().first()
            run.started_at = datetime.now(timezone.utc) - timedelta(
                seconds=settings.federation_stale_run_seconds + 60
            )
            await db.commit()
        r = await _harvest(session_factory, peer_id)
        assert r.status == "SUCCESS"
        async with session_factory() as db:
            statuses = (await db.execute(
                select(fm.FederationSyncRun.status).order_by(fm.FederationSyncRun.id)
            )).scalars().all()
        assert statuses == ["FAILED", "SUCCESS"]

        # 같은 프로세스 동시 호출 → 하나만 실행, 나머지는 SKIPPED
        gate = asyncio.Event()

        async def slow(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            await gate.wait()
            return _page([], 0, offset)

        client.fetch_export_datasets = slow
        t1 = asyncio.create_task(_harvest(session_factory, peer_id, full=True))
        await asyncio.sleep(0.05)
        r2 = await _harvest(session_factory, peer_id, full=True)
        gate.set()
        r1 = await t1
        assert r2.status == "SKIPPED" and r1.status == "SUCCESS"


async def _test_sample_purge_and_gate() -> None:
    session_factory = await _make_session()
    peer_id = await _new_peer(session_factory)

    with _Patched(), tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = Path(tmp)

        async def one(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            return _page([_ds(1)], 1) if offset == 0 else _page([], 1, offset)

        client.fetch_export_datasets = one
        await _harvest(session_factory, peer_id)

        # 이전 실행에서 샘플을 저장해 둔 상태를 만든다
        async with session_factory() as db:
            row = (await db.execute(select(fm.FederatedDataset))).scalars().first()
            fid = row.id
            row.has_sample = True
            await db.commit()
        samples.write_sample(peer_id, fid, {"columns": ["a"], "rows": [[1]]})
        assert samples.read_sample(peer_id, fid) is not None

        # peer 가 샘플 노출을 끔 → 다음 가져오기에서 저장분 정리
        settings.federation_harvest_samples = True

        async def caps_no_sample(instance, *a, **kw):
            return s.CapabilitiesResponse(items=[], groups=[], exposed=["schema"])

        client.fetch_export_capabilities = caps_no_sample
        await _harvest(session_factory, peer_id)
        assert samples.read_sample(peer_id, fid) is None
        async with session_factory() as db:
            row = (await db.execute(select(fm.FederatedDataset))).scalars().first()
            assert row.has_sample is False

        # 소비자 표시 선택에서 sample 제외 → 서빙 차단(미러·LIVE 모두)
        async with session_factory() as db:
            inst = await service.get_instance(db, peer_id)
            await service.update_instance(
                db, inst, s.FederatedInstanceUpdate(display_fields=["schema"]),
            )
        async with session_factory() as db:
            try:
                await service.federated_dataset_sample(db, "team-a::mysql-1.t1.dataset")
                raise AssertionError("expected SampleNotDisplayedError")
            except service.SampleNotDisplayedError:
                pass


async def _run() -> None:
    await _test_partial_failure_keeps_watermark()
    await _test_periodic_full_and_prune_guard()
    await _test_concurrency_guard()
    await _test_sample_purge_and_gate()


def test_federation_sync_integrity() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_sync_integrity OK")
