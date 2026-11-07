# SPDX-License-Identifier: Apache-2.0
"""CR 목록 — 결재 단계가 함께 내려온다 (자기완결형, in-memory SQLite + HTTP).

예전에는 ``GET /changes`` 가 결재 단계를 채우지 않아(항상 ``[]``) 목록 "결재" 열이 모든 CR 에서
``—`` 였고, 반려된 CR 의 빨간 "반려" 표시도 나오지 않았다(IT CHG-T04 · T11).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_list_steps
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.change_mgmt import models as chm
from app.change_mgmt import router as cr_router
from app.core.database import get_session


def _with_fk_targets(tables):
    seen, todo = {}, list(tables)
    while todo:
        t = todo.pop()
        if t.name not in seen:
            seen[t.name] = t
            todo.extend(fk.column.table for fk in t.foreign_keys)
    return list(seen.values())


def test_list_includes_approval_steps() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sc: cm.Base.metadata.create_all(
                sc, tables=_with_fk_targets([chm.ApprovalStep.__table__])))
        async with factory() as db:
            src = cm.Datasource(datasource_id="m", name="m", type="mysql")
            db.add(src)
            await db.flush()
            ds = cm.Dataset(urn="m.db.t.dataset", name="db.t", datasource_id=src.id, origin="DEV",
                            qualified_name="m.db.t", status="active")
            db.add(ds)
            await db.flush()

            def _cr(code: str, status: str) -> chm.ChangeRequest:
                return chm.ChangeRequest(cr_code=code, title=code, status=status, dataset_id=ds.id,
                                         change_type="COSMETIC", rollback_plan="r",
                                         business_justification="b", requested_by="admin")

            a, b, c = _cr("CR-A", "APPROVING"), _cr("CR-B", "REJECTED"), _cr("CR-C", "DRAFT")
            db.add_all([a, b, c])
            await db.flush()
            db.add_all([
                chm.ApprovalStep(cr_id=a.id, step_order=2, approver="mjkim", decision="PENDING"),
                chm.ApprovalStep(cr_id=a.id, step_order=1, approver="admin", decision="APPROVED"),
                chm.ApprovalStep(cr_id=b.id, step_order=1, approver="admin", decision="REJECTED",
                                 comment="영향 과다"),
            ])
            await db.commit()

    asyncio.run(_seed())

    app = FastAPI()
    app.include_router(cr_router.router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    r = TestClient(app).get("/api/v1/changes")
    assert r.status_code == 200, r.text
    by_code = {cr["cr_code"]: cr for cr in r.json()}
    # 단계 순서대로, CR 별로 묶여 내려온다
    assert [(s["step_order"], s["decision"]) for s in by_code["CR-A"]["approval_steps"]] == [
        (1, "APPROVED"), (2, "PENDING")]
    assert [(s["decision"], s["comment"]) for s in by_code["CR-B"]["approval_steps"]] == [
        ("REJECTED", "영향 과다")]
    assert by_code["CR-C"]["approval_steps"] == []

    asyncio.run(engine.dispose())


if __name__ == "__main__":
    test_list_includes_approval_steps()
    print("test_cr_list_steps OK")
