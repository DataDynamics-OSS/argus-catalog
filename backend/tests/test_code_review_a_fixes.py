# SPDX-License-Identifier: Apache-2.0
"""코드 리뷰 A 영역(catalog·comments·federation·quality) 결함 수정 회귀 테스트 (자기완결형).

확인하는 규칙:
- C04/C05: 샘플 경로 탈출(``..``)은 500 이 아니라 400 으로 거부한다.
- C06: 쿼리 리니지 테이블명의 ``_``·``%`` 를 LIKE 와일드카드로 오해석하지 않는다.
- C10: A←B←C 에서 B, A 를 차례로 지워도 살아 있는 C(와 삭제된 조상 자리표시)가 보인다.
- C38: DB 가 naive datetime(MariaDB DATETIME·SQLite)을 돌려줘도 스케줄러 tick 이 실패하지 않는다.

실행:
    cd backend
    pytest tests/test_code_review_a_fixes.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import TokenUser, require_admin
from app.core.config import settings

ADMIN = TokenUser(sub="a", username="admin", roles=["argus-admin"])


# ---------------------------------------------------------------------------
# C04 / C05 — 샘플 경로 탈출은 400
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return (tmp_path / "samples").resolve()


def test_sample_dir_rejects_escape(sample_root):
    from app.catalog.router import _sample_dir, _sample_dir_by_datasource_or_400

    assert _sample_dir("sakila.actor") == sample_root / "sakila.actor"
    assert _sample_dir("a/../b").parent == sample_root          # '/' 는 '_' 로 바뀌어 하위 이름이 됨
    for bad in ("..", "."):
        with pytest.raises(HTTPException) as ei:
            _sample_dir(bad)
        assert ei.value.status_code == 400

    assert sample_root in _sample_dir_by_datasource_or_400("ds-1", "db.t").parents
    with pytest.raises(HTTPException) as ei:
        _sample_dir_by_datasource_or_400("ds-1", "../../outside")
    assert ei.value.status_code == 400


async def test_sample_upload_escape_returns_400(sample_root):
    from app.catalog.router import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: ADMIN
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        bad = await c.post("/catalog/samples/upload", content=b"PAR1",
                           headers={"X-Datasource-Id": "ds-1", "X-Dataset-Name": "../../x"})
        ok = await c.post("/catalog/samples/upload", content=b"PAR1",
                          headers={"X-Datasource-Id": "ds-1", "X-Dataset-Name": "db.t"})
    assert bad.status_code == 400, bad.text
    assert ok.status_code == 200, ok.text
    assert (sample_root / "ds-1" / "db.t" / "sample.parquet").read_bytes() == b"PAR1"


# ---------------------------------------------------------------------------
# C06 — 리니지 테이블명 매칭은 와일드카드 없이 정확한 접미사 비교
# ---------------------------------------------------------------------------

async def test_lineage_resolve_no_wildcard():
    from app.catalog.lineage_resolve import resolve_lineage_for_dataset

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE argus_query_lineage (id INTEGER PRIMARY KEY, source_table TEXT, "
            "target_table TEXT, source_dataset_id INTEGER, target_dataset_id INTEGER)"
        ))
        rows = [
            (1, "a_b", "x"),          # '_' 와일드카드로 a_b 가 axb 에 매칭되면 안 됨
            (2, "%", "x"),            # '%' 단독도 매칭되면 안 됨
            (3, "AXB", "x"),          # 테이블명만(대소문자 무시)
            (4, "db.axb", "x"),       # qualified_name 과 정확히 같음
            (5, "other.axb", "x"),    # 다른 스키마의 같은 테이블 — 접미사가 아님
            (6, "x", "s.db.axb"),     # qualified_name 이 접미사인 게 아니라 그 반대 — 매칭 안 됨
        ]
        for rid, src, tgt in rows:
            await conn.execute(text(
                "INSERT INTO argus_query_lineage (id, source_table, target_table) VALUES (:i, :s, :t)"
            ), {"i": rid, "s": src, "t": tgt})
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        n = await resolve_lineage_for_dataset(s, 7, "db.axb")
        got = (await s.execute(text(
            "SELECT id, source_dataset_id, target_dataset_id FROM argus_query_lineage ORDER BY id"
        ))).fetchall()
    await engine.dispose()
    assert n == 2
    assert {r[0] for r in got if r[1] == 7} == {3, 4}
    assert all(r[2] is None for r in got)


# ---------------------------------------------------------------------------
# C10 — 조상 체인이 삭제돼도 살아 있는 답글이 보인다
# ---------------------------------------------------------------------------

def _flatten(items):
    for c in items:
        yield c
        yield from _flatten(c.replies)


async def test_comment_deleted_ancestor_chain_keeps_live_reply():
    from app.comments import service
    from app.comments.models import Comment
    from app.comments.schemas import CommentCreate

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: Comment.__table__.create(sc))
    make = async_sessionmaker(engine, expire_on_commit=False)

    def req(content, parent=None):
        return CommentCreate(entity_type="dataset", entity_id="1", content=content, parent_id=parent)

    async with make() as s:
        a = await service.create_comment(s, req("A"))
        b = await service.create_comment(s, req("B", a.id))
        c = await service.create_comment(s, req("C", b.id))
        assert await service.delete_comment(s, b.id)
        assert await service.delete_comment(s, a.id)

    async with make() as s:
        page = await service.list_comments(s, "dataset", "1")
        assert page.total == 1
        flat = list(_flatten(page.items))
        assert [x.id for x in flat] == [a.id, b.id, c.id]
        assert [x.is_deleted for x in flat] == [True, True, False]

    # 마지막 살아 있는 답글까지 지우면 조상 체인 전체가 숨겨진다
    async with make() as s:
        assert await service.delete_comment(s, c.id)
    async with make() as s:
        page = await service.list_comments(s, "dataset", "1")
        assert page.total == 0 and page.items == []
        counts = {r.id: r.reply_count for r in (await s.execute(
            Comment.__table__.select())).fetchall()}
        assert counts == {a.id: 0, b.id: 0, c.id: 0}

    # 형제가 살아 있으면 삭제된 부모는 남고, 살아 있는 부모 아래 잎 삭제는 카운트만 줄인다
    async with make() as s:
        p = await service.create_comment(s, req("P"))
        q1 = await service.create_comment(s, req("Q1", p.id))
        await service.create_comment(s, req("Q2", p.id))
        assert await service.delete_comment(s, q1.id)
        assert (await s.get(Comment, p.id)).reply_count == 1
    await engine.dispose()


# ---------------------------------------------------------------------------
# C38 — 스케줄러가 naive datetime 을 UTC 로 간주한다
# ---------------------------------------------------------------------------

@pytest.fixture
async def sched_db(monkeypatch):
    import app.core.database as database
    from app.catalog.models import DatasetProperty
    from app.federation.models import FederatedInstance, FederationSyncRun
    from app.quality.models import QualityScore

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for m in (FederatedInstance, FederationSyncRun, DatasetProperty, QualityScore):
            await conn.run_sync(lambda sc, t=m.__table__: t.create(sc))
    make = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", make)
    yield make
    await engine.dispose()


async def test_federation_scheduler_naive_started_at(sched_db):
    from app.federation import scheduler
    from app.federation.models import FederatedInstance, FederationSyncRun

    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC (MariaDB 와 같은 형태)
    async with sched_db() as s:
        s.add_all([
            FederatedInstance(id=1, instance_key="k1", name="p1", base_url="http://p1",
                              mode="HARVEST", status="ACTIVE", sync_interval_sec=900),
            FederatedInstance(id=2, instance_key="k2", name="p2", base_url="http://p2",
                              mode="HARVEST", status="ACTIVE", sync_interval_sec=900),
            FederationSyncRun(instance_id=1, status="SUCCESS", started_at=now - timedelta(hours=1)),
            FederationSyncRun(instance_id=2, status="SUCCESS", started_at=now - timedelta(seconds=10)),
        ])
        await s.commit()
    assert await scheduler._due_peers() == [1]


async def test_quality_scheduler_naive_scored_at(sched_db):
    from app.catalog.models import DatasetProperty
    from app.quality import scheduler
    from app.quality.models import QualityScore

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with sched_db() as s:
        s.add_all([
            DatasetProperty(dataset_id=1, property_key=scheduler.QUALITY_SCHEDULE_PROPERTY,
                            property_value="hourly"),
            DatasetProperty(dataset_id=2, property_key=scheduler.QUALITY_SCHEDULE_PROPERTY,
                            property_value="daily"),
            QualityScore(dataset_id=1, scored_at=now - timedelta(hours=2)),
            QualityScore(dataset_id=2, scored_at=now - timedelta(hours=2)),
        ])
        await s.commit()
    assert await scheduler._due_dataset_ids() == [1]


# ---------------------------------------------------------------------------
# C02 — datasource_id 가 DDL VARCHAR(36) 을 넘지 않는다 / 관계 upsert 방언 분기
# ---------------------------------------------------------------------------

def test_datasource_id_fits_ddl_length():
    from pydantic import ValidationError

    from app.catalog.schemas import DatasourceCreate
    from app.catalog.service import _generate_datasource_id

    req = DatasourceCreate(name="n", type="a" * 18)
    assert len(_generate_datasource_id(req.type)) <= 36
    with pytest.raises(ValidationError):
        DatasourceCreate(name="n", type="a" * 19)


def test_relationship_upsert_dialect():
    from app.catalog.relationship_service import _upsert_sql

    assert "ON DUPLICATE KEY UPDATE" in _upsert_sql("mysql")
    assert "ON DUPLICATE KEY UPDATE" in _upsert_sql("mariadb")
    assert "ON CONFLICT" in _upsert_sql("postgresql")
    assert "now()" not in _upsert_sql("mysql") + _upsert_sql("postgresql")
