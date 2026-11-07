# SPDX-License-Identifier: Apache-2.0
"""결재선 입력 검증 — 단계 번호 1..N(중복·건너뜀 422), 결재자 존재(로컬 인증 모드) (IT CHG-T22).

예전에는
- 단계 번호 1, 3 처럼 건너뛰어도 201 → 워크플로우는 1·2 순번으로, 결재 API 는 실제 3 으로
  Signal 을 보내 CR 이 결재 중에서 멈췄다.
- 중복 번호는 DB UNIQUE 위반으로 409, 없는 결재자는 201(아무도 결재 못 해 7일 뒤 타임아웃).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_approval_chain
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.change_mgmt import router as cr_router
from app.change_mgmt import schemas, service
from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.permissions import router as perm
from app.usermgr.models import ArgusRole, ArgusUser

BASE = dict(title="t", dataset_id=1, change_type="COSMETIC", rollback_plan="r", business_justification="b")


def _chain(*pairs):
    return [{"step_order": o, "approver": a} for o, a in pairs]


def test_chain_schema() -> None:
    ok = schemas.ChangeRequestCreate(**BASE, approval_chain=_chain((2, " mjkim "), (1, "admin")))
    assert [(s.step_order, s.approver) for s in ok.approval_chain] == [(2, "mjkim"), (1, "admin")]
    assert schemas.ChangeRequestCreate(**BASE).approval_chain == []   # 결재선 없음은 허용

    cases = {
        "중복": (_chain((1, "admin"), (1, "mjkim")), "결재선 단계 번호가 중복됩니다: 1"),
        "건너뜀": (_chain((1, "admin"), (3, "mjkim")), "1부터 빠짐없이 이어져야 합니다 (받은 값: 1, 3)"),
        "2부터": (_chain((2, "admin"),), "1부터 빠짐없이 이어져야 합니다 (받은 값: 2)"),
        "공백 결재자": (_chain((1, "   "),), "결재자 이름이 비어 있습니다."),
    }
    for name, (chain, msg) in cases.items():
        with pytest.raises(ValidationError) as ei:
            schemas.ChangeRequestCreate(**BASE, approval_chain=chain)
        assert msg in str(ei.value), (name, str(ei.value))
    with pytest.raises(ValidationError):   # step_order ge=1 (기존)
        schemas.ChangeRequestCreate(**BASE, approval_chain=_chain((0, "admin"),))
    with pytest.raises(ValidationError):   # 빈 문자열
        schemas.ChangeRequestCreate(**BASE, approval_chain=_chain((1, ""),))


async def _users_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: [m.__table__.create(sc) for m in (ArgusRole, ArgusUser)])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        role = ArgusRole(role_id="argus-user", name="User")
        db.add(role)
        await db.flush()
        for name, status in (("admin", "active"), ("mjkim", "active"), ("gone", "inactive")):
            db.add(ArgusUser(username=name, email=f"{name}@x", first_name=name, last_name=name,
                             password_hash="x", status=status, role_id=role.id))
        await db.commit()
    return engine, factory


def test_unknown_approvers(monkeypatch) -> None:
    async def run():
        engine, factory = await _users_db()
        async with factory() as db:
            monkeypatch.setattr(settings, "auth_type", "local")
            assert await service.unknown_approvers(db, ["admin", "mjkim"]) == []
            assert await service.unknown_approvers(db, ["admin", "no-such-user", "gone"]) == ["gone", "no-such-user"]
            assert await service.unknown_approvers(db, []) == []
            # Keycloak 모드 — argus_users 에 행이 없으므로 확인하지 않는다
            monkeypatch.setattr(settings, "auth_type", "keycloak")
            assert await service.unknown_approvers(db, ["no-such-user"]) == []
        await engine.dispose()
    asyncio.run(run())


def test_create_rejects_bad_chain(monkeypatch) -> None:
    """HTTP — 결재선 오류는 CR 을 만들기 전에 422."""
    engine, factory = asyncio.run(_users_db())
    created: list[str] = []

    async def create_cr(_s, data, by):
        created.append(data.title)
        return SimpleNamespace(cr_code="CR-1")

    async def notify(*_a, **_k):
        return None

    async def allow(*_a, **_k):
        return True

    async def _session():
        async with factory() as db:
            yield db

    monkeypatch.setattr(settings, "auth_type", "local")
    monkeypatch.setattr(service, "create_cr", create_cr)
    monkeypatch.setattr(service, "notify_referrers", notify)
    monkeypatch.setattr(perm, "check_feature", allow)

    app = FastAPI()
    app.include_router(cr_router.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="admin", username="admin", roles=["argus-user"])
    c = TestClient(app, raise_server_exceptions=False)

    def post(title, chain):
        return c.post("/api/v1/changes", json={**BASE, "title": title, "approval_chain": chain})

    assert post("dup", _chain((1, "admin"), (1, "mjkim"))).status_code == 422
    assert post("gap", _chain((1, "admin"), (3, "mjkim"))).status_code == 422
    r = post("ghost", _chain((1, "no-such-user"),))
    assert (r.status_code, r.json()["detail"]) == (422, "등록되지 않았거나 비활성인 결재자입니다: no-such-user")
    assert post("inactive", _chain((1, "gone"),)).status_code == 422
    assert created == []           # 하나도 만들어지지 않았다

    # 정상 결재선은 통과(응답 직렬화는 스텁이라 보지 않는다 — create_cr 호출 여부만)
    post("ok", _chain((1, "admin"), (2, "mjkim")))
    assert created == ["ok"]
    asyncio.run(engine.dispose())


if __name__ == "__main__":
    test_chain_schema()
    print("test_cr_approval_chain (schema) OK — 나머지는 pytest 로 실행")
