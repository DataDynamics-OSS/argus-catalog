# SPDX-License-Identifier: Apache-2.0
"""변경 요청 상태 전이 보완(TODO-009) — 초안 수정·삭제, 적용 후 종료(CLOSED)·롤백(ROLLED_BACK), 예정(SCHEDULED).

- 롤백은 CR 이 건드린 컬럼만 적용 직전 상태로 되돌린다(추가 → 삭제, 수정·삭제 → 원래대로).
- 적용(apply_change) 때 실제 적용 직전 스키마를 schema_before 에 다시 저장한다.
- 생성 때 자동 채운 예전 schema_before 형식(name·type·nullable bool)으로도 되돌린다.

in-memory SQLite. 워크플로우 SCHEDULED 전이는 Temporal 시간 건너뛰기 서버가 필요해 여기서는 다루지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.catalog.models as cm
import app.change_mgmt.models as chm
from app.change_mgmt import activities as acts
from app.change_mgmt import router as cr_router
from app.change_mgmt import service
from app.change_mgmt.activities import reverse_schema_spec
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from tests.test_change_mgmt_remaining_fixes import _cr, _make_db, _seed_dataset

# ---------------------------------------------------------------------------
# 역변경 명세(순수 함수)
# ---------------------------------------------------------------------------


def test_reverse_spec_new_format() -> None:
    before = json.dumps({"fields": [
        {"field_path": "id", "field_type": "INT", "nullable": "false", "ordinal": 0},
        {"field_path": "email", "field_type": "VARCHAR(100)", "nullable": "true", "ordinal": 1},
        {"field_path": "phone", "field_type": "VARCHAR(20)", "nullable": "true", "ordinal": 2},
    ]})
    after = json.dumps({"fields": [{"field_path": "email", "field_type": "VARCHAR(255)"},
                                   {"field_path": "created_at", "field_type": "TIMESTAMP"}],
                        "drop": ["phone"]})
    ups, drops = reverse_schema_spec(before, after)
    assert drops == ["created_at"]
    assert [(u["field_path"], u["field_type"], u["ordinal"]) for u in ups] == [
        ("email", "VARCHAR(100)", 1), ("phone", "VARCHAR(20)", 2)]


def test_reverse_spec_legacy_format_and_missing_info() -> None:
    # 생성 때 자동 채운 예전 형식 — name·type·nullable(bool)·primary_key(bool)
    legacy = json.dumps({"fields": [{"name": "id", "type": "INT", "nullable": False, "primary_key": True}]})
    ups, drops = reverse_schema_spec(legacy, json.dumps({"id": "BIGINT"}))
    assert drops == [] and ups == [{"field_path": "id", "field_type": "INT", "nullable": "false",
                                    "is_primary_key": "true"}]
    # 삭제한 컬럼의 적용 전 정보가 없으면 되돌릴 수 없다
    with pytest.raises(ValueError, match="ghost"):
        reverse_schema_spec(legacy, json.dumps({"drop": ["ghost"]}))
    # 변경 없는 CR 은 역변경도 없음
    assert reverse_schema_spec(None, None) == ([], [])


# ---------------------------------------------------------------------------
# 적용 → 롤백(DB)
# ---------------------------------------------------------------------------


async def _fields(db, ds_id) -> dict[str, tuple[str, str]]:
    rows = (await db.execute(select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == ds_id))).scalars().all()
    return {r.field_path: (r.field_type, r.nullable) for r in rows}


async def _run_apply_then_rollback() -> None:
    engine, factory = await _make_db()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.SchemaSnapshot.__table__.create(sc, checkfirst=True))

    @asynccontextmanager
    async def _factory():
        async with factory() as s:
            yield s

    saved = acts.async_session_factory
    acts.async_session_factory = _factory
    try:
        async with factory() as db:
            ds_id = await _seed_dataset(db)
            for i, (p, t) in enumerate((("id", "INT"), ("email", "VARCHAR(100)"), ("phone", "VARCHAR(20)"))):
                db.add(cm.DatasetSchema(dataset_id=ds_id, field_path=p, field_type=t, native_type=t,
                                        nullable="true", ordinal=i))
            cr = _cr(ds_id, "CR-1", "APPROVED", "wf-1")
            cr.change_type = "BREAKING"
            cr.schema_after = json.dumps({"fields": [{"field_path": "email", "field_type": "VARCHAR(255)",
                                                      "nullable": "false"},
                                                     {"field_path": "created_at", "field_type": "TIMESTAMP"}],
                                          "drop": ["phone"]})
            # 생성 뒤 스키마가 바뀌었다고 가정 — 생성 때 schema_before 에는 phone 이 없다
            cr.schema_before = json.dumps({"fields": [{"name": "id", "type": "INT", "nullable": True}]})
            db.add(cr)
            await db.commit()
            cr_id = cr.id

        await acts.apply_change(acts.ApplyInput(cr_id=cr_id))
        async with factory() as db:
            assert await _fields(db, ds_id) == {"id": ("INT", "true"), "email": ("VARCHAR(255)", "false"),
                                                "created_at": ("TIMESTAMP", "true")}
            cr = await db.get(chm.ChangeRequest, cr_id)
            assert cr.status == "APPLIED"
            # 적용 직전 실제 스키마로 다시 저장 — phone 포함
            assert {f["field_path"] for f in json.loads(cr.schema_before)["fields"]} == {"id", "email", "phone"}

            await service.rollback_cr(db, cr)
            await db.commit()
            assert cr.status == "ROLLED_BACK"
            assert await _fields(db, ds_id) == {"id": ("INT", "true"), "email": ("VARCHAR(100)", "true"),
                                                "phone": ("VARCHAR(20)", "true")}
            # 적용·롤백 모두 스키마 변경 이력에 남는다
            snaps = (await db.execute(select(cm.SchemaSnapshot).where(cm.SchemaSnapshot.dataset_id == ds_id))).scalars().all()
            assert len(snaps) >= 2
    finally:
        acts.async_session_factory = saved
        await engine.dispose()


def test_apply_then_rollback_restores_touched_columns() -> None:
    asyncio.run(_run_apply_then_rollback())


# ---------------------------------------------------------------------------
# 라우터 — 수정·삭제·종료·롤백 상태 검사
# ---------------------------------------------------------------------------


async def _seed_router_db():
    from app.permissions.models import Permission
    from app.usermgr.models import ArgusRole, ArgusUser

    engine, factory = await _make_db()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.SchemaSnapshot.__table__.create(sc, checkfirst=True))
        # 기능 권한(changes.create) — 행이 없으면 모두 허용
        await conn.run_sync(lambda sc: Permission.__table__.create(sc, checkfirst=True))
        # 로컬 인증이면 결재자가 등록·활성 사용자인지 확인한다
        await conn.run_sync(lambda sc: ArgusRole.__table__.create(sc, checkfirst=True))
        await conn.run_sync(lambda sc: ArgusUser.__table__.create(sc, checkfirst=True))
    async with factory() as db:
        ds_id = await _seed_dataset(db)
        crs = {}
        for code, status, wf in (("D", "DRAFT", None), ("C0", "CANCELLED", None), ("C1", "CANCELLED", "wf-c"),
                                 ("A", "APPLIED", "wf-a"), ("A2", "APPLIED", "wf-a2"), ("R", "REJECTED", "wf-r")):
            cr = _cr(ds_id, code, status, wf)
            db.add(cr)
            await db.flush()
            crs[code] = cr.id
        db.add(chm.ApprovalStep(cr_id=crs["D"], step_order=1, approver="bob", decision="PENDING"))
        role = ArgusRole(role_id="argus-user", name="사용자")
        db.add(role)
        await db.flush()
        for u in ("carol", "dave"):
            db.add(ArgusUser(username=u, email=f"{u}@corp.example", first_name=u, last_name="k",
                             password_hash="x", status="active", role_id=role.id))
        await db.commit()
    return engine, factory, ds_id, crs


def test_router_edit_delete_close_rollback() -> None:
    engine, factory, ds_id, crs = asyncio.run(_seed_router_db())

    async def _session():
        async with factory() as s:
            yield s

    users = {"who": TokenUser(sub="alice", username="alice", roles=["argus-user"])}
    app = FastAPI()
    app.include_router(cr_router.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: users["who"]
    c = TestClient(app)
    base = "/api/v1/changes"
    body = {"title": "새 제목", "dataset_id": ds_id, "change_type": "ADDITIVE", "rollback_plan": "r",
            "business_justification": "b", "schema_after": json.dumps({"x": "INT"}),
            "approval_chain": [{"step_order": 1, "approver": "carol"}, {"step_order": 2, "approver": "dave"}]}
    try:
        # 남의 CR 은 403
        users["who"] = TokenUser(sub="eve", username="eve", roles=["argus-user"])
        assert c.put(f"{base}/{crs['D']}", json=body).status_code == 403
        users["who"] = TokenUser(sub="alice", username="alice", roles=["argus-user"])

        # 초안 수정 — 결재선 통째로 교체, 응답에 데이터셋 이름
        r = c.put(f"{base}/{crs['D']}", json=body)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["title"] == "새 제목" and j["change_type"] == "ADDITIVE" and j["dataset_name"] == "m.db.t"
        assert [s["approver"] for s in j["approval_steps"]] == ["carol", "dave"]
        # 상신한(또는 끝난) CR 수정은 409
        assert c.put(f"{base}/{crs['A']}", json=body).status_code == 409

        # 종료 — APPLIED 만
        assert c.post(f"{base}/{crs['R']}/close").status_code == 409
        r = c.post(f"{base}/{crs['A']}/close")
        assert r.status_code == 200 and r.json()["status"] == "CLOSED"
        assert c.post(f"{base}/{crs['A']}/rollback").status_code == 409   # 종료 뒤에는 롤백 불가
        # 롤백 — 변경 없는 CR 은 상태만 ROLLED_BACK
        r = c.post(f"{base}/{crs['A2']}/rollback")
        assert r.status_code == 200 and r.json()["status"] == "ROLLED_BACK"
        assert c.post(f"{base}/{crs['A2']}/close").status_code == 409

        # 삭제 — 상신한 적 없는 것만
        assert c.delete(f"{base}/{crs['C1']}").status_code == 409
        assert c.delete(f"{base}/{crs['R']}").status_code == 409
        assert c.delete(f"{base}/{crs['C0']}").status_code == 204
        assert c.delete(f"{base}/{crs['D']}").status_code == 204
        assert c.get(f"{base}/{crs['D']}").status_code == 404
        # 목록에도 데이터셋 이름
        assert {x["dataset_name"] for x in c.get(base).json()} == {"m.db.t"}
    finally:
        asyncio.run(engine.dispose())
