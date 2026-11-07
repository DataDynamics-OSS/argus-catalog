# SPDX-License-Identifier: Apache-2.0
"""통지 ACK — 없는 통지는 404 (자기완결형, in-memory SQLite + HTTP).

예전에는 ``service.ack_notification`` 이 ``ValueError`` 를 던져 그대로 HTTP 500 이었다(IT CHG-T10).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_notification_ack
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.change_mgmt import models as chm
from app.change_mgmt import router as cr_router
from app.change_mgmt import service
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session


def _with_fk_targets(tables):
    seen, todo = {}, list(tables)
    while todo:
        t = todo.pop()
        if t.name not in seen:
            seen[t.name] = t
            todo.extend(fk.column.table for fk in t.foreign_keys)
    return list(seen.values())


def test_notification_ack() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sc: cm.Base.metadata.create_all(
                sc, tables=_with_fk_targets([chm.NotificationLog.__table__])))
        async with factory() as db:
            src = cm.Datasource(datasource_id="m", name="m", type="mysql")
            db.add(src)
            await db.flush()
            ds = cm.Dataset(urn="m.db.t.dataset", name="db.t", datasource_id=src.id, origin="DEV",
                            qualified_name="m.db.t", status="active")
            db.add(ds)
            await db.flush()
            cr = chm.ChangeRequest(cr_code="CR-1", title="t", status="APPLIED", dataset_id=ds.id,
                                   change_type="COSMETIC", rollback_plan="r", business_justification="b",
                                   requested_by="admin")
            db.add(cr)
            await db.flush()
            log = chm.NotificationLog(cr_id=cr.id, channel="WEBHOOK", stage="APPLIED", status="SENT")
            db.add(log)
            await db.commit()
            # 서비스: 없는 id 는 None (예외 아님)
            assert await service.ack_notification(db, 999999, "x") is None
            return log.id

    log_id = asyncio.run(_seed())

    app = FastAPI()
    app.include_router(cr_router.router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    who: dict = {"user": None}

    def _current():
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        return who["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current
    c = TestClient(app)

    url = f"/api/v1/changes/notifications/{log_id}/ack"
    assert c.post(url, json={"comment": "x"}).status_code == 401
    who["user"] = TokenUser(sub="admin", username="admin", roles=["argus-user"])

    r = c.post("/api/v1/changes/notifications/999999/ack", json={"comment": "x"})
    assert (r.status_code, r.json()["detail"]) == (404, "통지 이력을(를) 찾을 수 없습니다.")

    r = c.post(url, json={"comment": "대응 완료"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ACKED" and body["ack_comment"] == "대응 완료" and body["acked_at"]

    asyncio.run(engine.dispose())


if __name__ == "__main__":
    test_notification_ack()
    print("test_cr_notification_ack OK")
