# SPDX-License-Identifier: Apache-2.0
"""대시보드 통계(`GET /catalog/stats`) 결함 수정 회귀 테스트 (자기완결형, in-memory SQLite).

- DASH-T07: 동명 데이터 소스·데이터셋이 차트에서 한 항목으로 합쳐지지 않음(id 기준 GROUP BY)
- DASH-T08: 소유자 카드 = removed 제외 서로 다른 소유자 수, 용어집 카드 = TERM 만
- DASH-T03: 추이 버킷이 UTC 기준·요청 시간대 날짜로 묶이고, 24시간 창에 같은 시각이 두 번 나오지 않음
- /catalog/stats 는 로그인 필요

PostgreSQL 전용 식(date_trunc/timezone)은 SQLite 에서 strftime 분기로 대체되어 실행된다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_dashboard_stats_fixes.py
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.catalog import router as catalog_router
from app.catalog import service


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        # catalog_* 테이블만 — 전체 metadata.create_all 은 타 모듈 FK 로 실패한다
        tables = [t for t in cm.Base.metadata.sorted_tables if t.name.startswith("catalog_")]
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


async def _seed(db, *, created_at=None):
    ds1 = cm.Datasource(datasource_id="dup-1", name="dup", type="postgresql")
    ds2 = cm.Datasource(datasource_id="dup-2", name="dup", type="mysql")
    db.add_all([ds1, ds2])
    await db.flush()
    kw = {"created_at": created_at} if created_at else {}
    d1 = cm.Dataset(urn="u1", name="t.dup", datasource_id=ds1.id, **kw)
    d2 = cm.Dataset(urn="u2", name="t.dup", datasource_id=ds2.id, **kw)
    d3 = cm.Dataset(urn="u3", name="t.gone", datasource_id=ds1.id, status="removed", **kw)
    db.add_all([d1, d2, d3])
    await db.flush()
    tags = [cm.Tag(name=f"tag{i}") for i in range(3)]
    db.add_all(tags)
    await db.flush()
    for d in (d1, d2):
        for t in tags:
            db.add(cm.DatasetTag(dataset_id=d.id, tag_id=t.id))
        db.add(cm.DatasetSchema(dataset_id=d.id, field_path="id", field_type="INTEGER"))
    # 같은 소유자가 두 데이터셋에 지정 + removed 데이터셋에만 있는 소유자
    db.add_all([
        cm.Owner(dataset_id=d1.id, owner_name="alice"),
        cm.Owner(dataset_id=d2.id, owner_name="alice"),
        cm.Owner(dataset_id=d2.id, owner_name="bob"),
        cm.Owner(dataset_id=d3.id, owner_name="ghost"),
    ])
    cat = cm.GlossaryTerm(name="분류", term_type="CATEGORY")
    db.add(cat)
    await db.flush()
    db.add_all([
        cm.GlossaryTerm(name="용어1", term_type="TERM", parent_id=cat.id),
        cm.GlossaryTerm(name="용어2", term_type="TERM"),
    ])
    await db.commit()


def test_group_by_id_not_name():
    async def run():
        make_db = await _make_session()
        async with make_db() as db:
            await _seed(db)
            st = await service.get_catalog_stats(db)
        assert sorted(x["count"] for x in st.datasets_by_datasource if x["datasource"] == "dup") == [1, 1]
        assert [x["count"] for x in st.schema_fields_by_datasource if x["datasource"] == "dup"] == [1, 1]
        assert [x["count"] for x in st.top_tagged_datasets if x["name"] == "t.dup"] == [3, 3]
    asyncio.run(run())


def test_owner_and_glossary_cards():
    async def run():
        make_db = await _make_session()
        async with make_db() as db:
            await _seed(db)
            st = await service.get_catalog_stats(db)
        assert st.total_owners == 2          # alice, bob (중복·removed 의 ghost 제외)
        assert st.total_glossary_terms == 2  # CATEGORY 제외
        assert st.total_datasets == 2
    asyncio.run(run())


def test_trend_buckets_timezone_and_no_duplicate_hour():
    async def run():
        now = datetime.now(timezone.utc)
        make_db = await _make_session()
        async with make_db() as db:
            await _seed(db, created_at=_naive_utc(now))
            # 정확히 24시간 전(창 시작) — 현재 시각과 같은 HH 버킷이 다시 나오면 안 된다
            ds = cm.Datasource(datasource_id="old", name="old", type="mysql")
            db.add(ds)
            await db.flush()
            db.add(cm.Dataset(urn="old1", name="old1", datasource_id=ds.id,
                              created_at=_naive_utc(now - timedelta(hours=24))))
            await db.commit()
            st_utc = await service.get_catalog_stats(db)
            st_kst = await service.get_catalog_stats(db, tz="Asia/Seoul")
            st_bad = await service.get_catalog_stats(db, tz="Not/AZone")

        # 1d: ISO UTC 시각, 현재 시 버킷만(24시간 전 버킷 제외), 라벨 중복 없음
        labels = [x["date"] for x in st_utc.daily_datasets_1d]
        assert len(labels) == len(set(labels))
        cur_hour = now.replace(minute=0, second=0, microsecond=0)
        assert st_utc.daily_datasets_1d == [{"date": cur_hour.isoformat(), "count": 2}]
        assert datetime.fromisoformat(labels[0]).utcoffset() == timedelta(0)

        # 7d/30d: 요청 시간대 날짜로 묶음
        kst_today = now.astimezone(timezone(timedelta(hours=9))).date().isoformat()
        assert st_kst.daily_datasets_7d[-1] == {"date": kst_today, "count": 2}
        assert sum(x["count"] for x in st_kst.daily_datasets_30d) == 3
        utc_today = now.date().isoformat()
        assert st_utc.daily_datasets_7d[-1] == {"date": utc_today, "count": 2}
        # 알 수 없는 시간대는 UTC 로 대체
        assert st_bad.daily_datasets_7d == st_utc.daily_datasets_7d
    asyncio.run(run())


def test_stats_requires_login():
    params = inspect.signature(catalog_router.get_catalog_stats).parameters
    assert "user" in params
    # CurrentUser = Annotated[TokenUser, Depends(get_current_user)]
    from app.core.auth import get_current_user
    meta = getattr(params["user"].annotation, "__metadata__", ())
    assert any(getattr(m, "dependency", None) is get_current_user for m in meta)
