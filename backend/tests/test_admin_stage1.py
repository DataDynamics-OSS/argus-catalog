# SPDX-License-Identifier: Apache-2.0
"""관리 기능 1단계(보안 결함) 테스트 (in-memory SQLite, 자기완결형).

검증:
- 비밀번호: PBKDF2 해시·검증, 예전 SHA-256 호환·재해시 판단, 정책(8자·소문자·숫자)
- 사용자 관리: 마지막 "활성" 관리자 보호(비활성 관리자 제외), 본인 삭제·비활성화·역할 변경 차단,
  정책 강제(임시 비밀번호 예외), 관리자 재설정 = 다음 로그인 강제 변경, 시드 admin 강제 변경, 중복 409
- 로그인: 예전 해시로 로그인 성공 시 새 형식으로 다시 저장
- 권한: 관리 메뉴·알 수 없는 기능 key 는 저장 안 함, 관리 메뉴는 /me 에서 항상 차단,
  메뉴 서버 강제(require_menu), 비로그인 기능 검사, 로컬 DDL 숨김, 어시스턴트 기능 권한
- 설정: 캐시 설정 변경은 관리자만, 저장된 LLM·임베딩 키를 다른 제공자/URL 로 못 씀,
  인증 설정 잠금 방지(형식·역할·확인·로컬 관리자), ARGUS_AUTH_FORCE_LOCAL 복구

실행:
    cd backend
    pytest tests/test_admin_stage1.py
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import passwords
from app.core.auth import TokenUser, get_current_user, get_optional_user
from app.core.config import settings
from app.core.database import Base, get_session
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings.models import CatalogConfiguration
from app.usermgr import ownership
from app.usermgr import service as usvc
from app.usermgr.models import ArgusRole, ArgusUser

ADMIN_ROLE = settings.auth_keycloak_admin_role


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fast_hash(monkeypatch):
    # 반복수를 줄여 테스트를 빠르게(형식·검증 로직은 동일)
    monkeypatch.setattr(passwords, "ITERATIONS", 1000)
    orig = passwords.hash_password
    monkeypatch.setattr(passwords, "hash_password", lambda p, *, iterations=1000: orig(p, iterations=iterations))


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            ArgusRole.__table__, ArgusUser.__table__, Permission.__table__, CatalogConfiguration.__table__,
            # 사용자 삭제 시 소유 자산 확인(2단계)이 읽는 테이블
            *(a.model.__table__ for a in ownership._registry()),
        ])
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        for rid in ("argus-admin", "argus-superuser", "argus-user"):
            s.add(ArgusRole(role_id=rid, name=rid))
        await s.commit()
    perm._menu_cache.clear()
    yield make
    await engine.dispose()


async def _add(make, username: str, role: str = "argus-admin", status: str = "active",
               password_hash: str = "x") -> int:
    async with make() as s:
        rid = (await s.execute(select(ArgusRole.id).where(ArgusRole.role_id == role))).scalar_one()
        u = ArgusUser(username=username, email=f"{username}@ex.com", first_name="F", last_name="L",
                      password_hash=password_hash, status=status, role_id=rid)
        s.add(u)
        await s.commit()
        return u.id


def tu(name: str, role: str = "argus-user", sub: str | None = None) -> TokenUser:
    return TokenUser(sub=sub or name, username=name, roles=[role])


def _app(factory, *routers) -> tuple[FastAPI, dict]:
    app = FastAPI()
    for r in routers:
        app.include_router(r, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    who: dict = {"user": None}

    def _current():
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        return who["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current
    app.dependency_overrides[get_optional_user] = lambda: who["user"]
    return app, who


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


# ---------------------------------------------------------------------------
# 비밀번호
# ---------------------------------------------------------------------------

def test_password_hash_and_verify():
    h = passwords.hash_password("secret12")
    assert h.startswith("pbkdf2_sha256$") and "secret12" not in h
    assert passwords.hash_password("secret12") != h  # salt
    assert passwords.verify_password("secret12", h)
    assert not passwords.verify_password("secret13", h)
    assert not passwords.verify_password("x", None) and not passwords.verify_password("x", "garbage$$")


def test_password_legacy_sha256():
    legacy = hashlib.sha256(b"admin").hexdigest()
    assert passwords.verify_password("admin", legacy)
    assert not passwords.verify_password("admin2", legacy)
    assert passwords.needs_rehash(legacy)
    assert passwords.needs_rehash("(external/keycloak)")
    assert not passwords.needs_rehash(passwords.hash_password("abc12345", iterations=passwords.ITERATIONS))


@pytest.mark.parametrize("pw,ok", [
    ("abc1234", False), ("ABCDEFG1", False), ("abcdefgh", False), ("abcdefg1", True), ("Pass word 9", True),
])
def test_password_policy(pw, ok):
    assert (passwords.password_problem(pw) is None) == ok


# ---------------------------------------------------------------------------
# 사용자 관리 — 마지막 활성 관리자
# ---------------------------------------------------------------------------

async def test_last_active_admin_ignores_inactive(factory):
    a = await _add(factory, "a1")
    b = await _add(factory, "a2", status="inactive")  # 비활성 관리자는 수에 안 듦
    async with factory() as s:
        assert await usvc._is_last_admin(s, a)
        with pytest.raises(ValueError, match="마지막 활성 관리자"):
            await usvc.deactivate_user(s, a)
    async with factory() as s:
        with pytest.raises(ValueError):
            await usvc.delete_user(s, a)
    async with factory() as s:  # 이미 비활성인 관리자는 삭제 가능(활성 관리자 수 불변)
        assert await usvc.delete_user(s, b)


async def test_bulk_deactivate_keeps_one_admin(factory):
    a = await _add(factory, "a1")
    b = await _add(factory, "a2")
    async with factory() as s:
        await usvc.deactivate_user(s, a)
    async with factory() as s:
        with pytest.raises(ValueError):
            await usvc.deactivate_user(s, b)


async def test_demote_last_active_admin_blocked(factory):
    from app.usermgr.schemas import RoleName, UserChangeRoleRequest
    a = await _add(factory, "a1")
    await _add(factory, "a2", status="inactive")
    async with factory() as s:
        with pytest.raises(ValueError):
            await usvc.change_role(s, a, UserChangeRoleRequest(role=RoleName.USER))


# ---------------------------------------------------------------------------
# 사용자 관리 — 비밀번호 정책·재설정·시드
# ---------------------------------------------------------------------------

async def test_add_user_policy_and_temp_password(factory):
    from app.usermgr.schemas import UserAddRequest
    base = dict(email="n@ex.com", first_name="N", last_name="U", role="argus-user")
    async with factory() as s:
        with pytest.raises(ValueError, match="8자"):
            await usvc.add_user(s, UserAddRequest(username="n1", password="abcd", **base))
    async with factory() as s:  # 최초 로그인 때 바꾸는 임시 비밀번호(LDAP 생년월일 등)는 허용
        u = await usvc.add_user(s, UserAddRequest(username="n2", password="0101", must_change_password=True,
                                                  **base))
        assert u.username == "n2"
        row = (await s.execute(select(ArgusUser).where(ArgusUser.username == "n2"))).scalar_one()
        assert row.password_hash.startswith("pbkdf2_sha256$")


async def test_set_password_policy_and_force_change(factory):
    uid = await _add(factory, "u1", role="argus-user")
    async with factory() as s:
        with pytest.raises(ValueError):
            await usvc.set_password(s, uid, "abcdefgh")
    async with factory() as s:
        await usvc.set_password(s, uid, "abcdefg1", must_change_password=True)
        row = await s.get(ArgusUser, uid)
        assert row.must_change_password and passwords.verify_password("abcdefg1", row.password_hash)


async def test_seed_admin_must_change(factory, monkeypatch):
    monkeypatch.setattr(settings, "auth_type", "local")
    async with factory() as s:
        await usvc.seed_admin_user(s)
        row = (await s.execute(select(ArgusUser).where(ArgusUser.username == "admin"))).scalar_one()
        assert row.must_change_password and passwords.verify_password("admin", row.password_hash)


# ---------------------------------------------------------------------------
# 사용자 관리 — HTTP (본인 보호·중복 409·재설정 플래그)
# ---------------------------------------------------------------------------

async def test_usermgr_http_self_protection(factory, monkeypatch):
    from app.usermgr.router import router
    monkeypatch.setattr(settings, "auth_type", "local")
    me = await _add(factory, "boss")
    other = await _add(factory, "boss2")
    usr = await _add(factory, "u1", role="argus-user")
    app, who = _app(factory, router)
    who["user"] = tu("boss", ADMIN_ROLE, sub=str(me))
    async with _client(app) as c:
        for method, path, body in [
            ("DELETE", f"/api/v1/usermgr/users/{me}", None),
            ("PUT", f"/api/v1/usermgr/users/{me}/deactivate", None),
            ("PUT", f"/api/v1/usermgr/users/{me}/role", {"role": "argus-user"}),
        ]:
            r = await c.request(method, path, json=body)
            assert r.status_code == 400 and "본인 계정" in r.json()["detail"], (path, r.text)
        # 같은 역할로의 '변경'은 본인이어도 무해 — 허용
        assert (await c.put(f"/api/v1/usermgr/users/{me}/role", json={"role": "argus-admin"})).status_code == 200
        # 다른 관리자는 처리 가능
        assert (await c.put(f"/api/v1/usermgr/users/{other}/deactivate")).status_code == 200
        # 이메일 중복 → 409
        r = await c.put(f"/api/v1/usermgr/users/{usr}", json={"email": "boss2@ex.com"})
        assert r.status_code == 409, r.text
        # 관리자 재설정 — 남은 다음 로그인 강제 변경, 본인은 제외
        assert (await c.put(f"/api/v1/usermgr/users/{usr}/password", json={"password": "abcdefg1"})).status_code == 200
        assert (await c.put(f"/api/v1/usermgr/users/{me}/password", json={"password": "abcdefg2"})).status_code == 200
        r = await c.put(f"/api/v1/usermgr/users/{usr}/password", json={"password": "ABCDEFGH1"})
        assert r.status_code == 400
    async with factory() as s:
        assert (await s.get(ArgusUser, usr)).must_change_password
        assert not (await s.get(ArgusUser, me)).must_change_password


async def test_login_rehashes_legacy_hash(factory, monkeypatch):
    from app.auth.router import LoginRequest, _login_local
    monkeypatch.setattr(settings, "auth_type", "local")
    uid = await _add(factory, "old", role="argus-user", password_hash=hashlib.sha256(b"oldpass1").hexdigest())
    async with factory() as s:
        tok = await _login_local(LoginRequest(username="old", password="oldpass1"), s)
        assert tok.access_token
    async with factory() as s:
        row = await s.get(ArgusUser, uid)
        assert row.password_hash.startswith("pbkdf2_sha256$")
        assert passwords.verify_password("oldpass1", row.password_hash)
    async with factory() as s:
        with pytest.raises(HTTPException) as e:
            await _login_local(LoginRequest(username="old", password="wrong"), s)
        assert e.value.status_code == 401


# ---------------------------------------------------------------------------
# 권한
# ---------------------------------------------------------------------------

async def test_put_matrix_skips_admin_menus_and_unknown_features(factory):
    async with factory() as s:
        await perm._put_matrix(s, "MENU", {"users": [], "settings": [], "apis": ["argus-superuser"]})
        await perm._put_matrix(s, "FEATURE", {"datasets.manage": [], "apis.credentials": []})
        rows = {(r.kind, r.perm_key) for r in (await s.execute(select(Permission))).scalars()}
    assert rows == {("MENU", "apis"), ("FEATURE", "apis.credentials")}


async def test_me_always_denies_admin_menus(factory):
    async with factory() as s:
        me = await perm.get_my_permissions(tu("u"), s)
        assert {"users", "settings", "permissions", "mlflow-files", "oci-files"} <= set(me["denied_menus"])
        assert (await perm.get_my_permissions(tu("a", ADMIN_ROLE), s))["denied_menus"] == []


async def test_require_menu_server_enforced(factory):
    from fastapi import APIRouter, Depends
    r = APIRouter(prefix="/apis", dependencies=[Depends(perm.require_menu("apis"))])

    @r.get("/ping")
    async def ping():
        return {"ok": True}

    app, who = _app(factory, r, perm.router)
    async with _client(app) as c:
        who["user"] = tu("u")
        assert (await c.get("/api/v1/apis/ping")).status_code == 200  # 미설정 = 허용
        who["user"] = tu("a", ADMIN_ROLE)
        assert (await c.put("/api/v1/permissions/menus", json={"apis": ["argus-superuser"]})).status_code == 200
        who["user"] = tu("u")
        r1 = await c.get("/api/v1/apis/ping")
        assert r1.status_code == 403 and "apis" in r1.json()["detail"]
        who["user"] = tu("s", "argus-superuser")
        assert (await c.get("/api/v1/apis/ping")).status_code == 200
        who["user"] = tu("a", ADMIN_ROLE)
        assert (await c.get("/api/v1/apis/ping")).status_code == 200
        who["user"] = None  # 사용자 없음 — 토큰을 빼서 메뉴 차단을 우회하지 못한다
        assert (await c.get("/api/v1/apis/ping")).status_code == 401


async def test_require_menu_service_token_path_left_to_endpoint(factory):
    """사용자 토큰이 아닌 요청은 자체 서비스 토큰 경로(auth_gate 예외)만 엔드포인트 인증에 맡긴다."""
    from fastapi import APIRouter, Depends
    r = APIRouter(prefix="/mcp-deployments", dependencies=[Depends(perm.require_menu("mcp-servers"))])

    @r.get("/{name}/policy-bundle")
    async def bundle(name: str):
        return {"ok": True}

    @r.get("/{name}")
    async def detail(name: str):
        return {"ok": True}

    app, who = _app(factory, r, perm.router)
    async with _client(app) as c:
        who["user"] = None
        assert (await c.get("/api/v1/mcp-deployments/d1/policy-bundle")).status_code == 200
        assert (await c.get("/api/v1/mcp-deployments/d1")).status_code == 401


def test_server_enforced_menus_are_guarded_in_main():
    import inspect

    import app.main as main
    src = inspect.getsource(main)
    for key in perm.SERVER_ENFORCED_MENUS:
        assert f'_menu_guard("{key}")' in src, key


async def test_check_feature_anonymous(factory):
    async with factory() as s:
        assert await perm.check_feature(s, None, "datasets.ddl-view")
        s.add(Permission(kind="FEATURE", perm_key="datasets.ddl-view", role_id="argus-superuser"))
        await s.commit()
        assert not await perm.check_feature(s, None, "datasets.ddl-view")
        assert not await perm.check_feature(s, tu("u"), "datasets.ddl-view")
        assert await perm.check_feature(s, tu("s", "argus-superuser"), "datasets.ddl-view")


async def test_local_dataset_ddl_hidden(factory):
    from types import SimpleNamespace

    from app.catalog.router import _hide_ddl_if_denied

    class _DS(SimpleNamespace):
        def model_copy(self, update):
            return _DS(**{**self.__dict__, **update})

    ds = _DS(id=1, ddl="CREATE TABLE t (a int)")
    async with factory() as s:
        assert (await _hide_ddl_if_denied(s, tu("u"), ds)).ddl  # 미설정 = 노출
        s.add(Permission(kind="FEATURE", perm_key="datasets.ddl-view", role_id="__none__"))
        await s.commit()
        assert (await _hide_ddl_if_denied(s, tu("u"), ds)).ddl is None
        assert (await _hide_ddl_if_denied(s, tu("a", ADMIN_ROLE), ds)).ddl


def test_assistant_chat_requires_feature():
    from app.ai.router import router
    route = next(r for r in router.routes if getattr(r, "path", "") == "/ai/assistant/chat")
    deps = [d.call for d in route.dependant.dependencies]
    assert any(getattr(d, "__qualname__", "").startswith("require_feature") for d in deps)


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

async def test_cache_config_admin_only(factory):
    from app.external.router import router
    app, who = _app(factory, router)
    body = {"max_size": 500, "ttl_seconds": 60, "enabled": True}
    async with _client(app) as c:
        assert (await c.put("/api/v1/external/cache/config", json=body)).status_code == 401
        who["user"] = tu("u")
        assert (await c.put("/api/v1/external/cache/config", json=body)).status_code == 403
        who["user"] = tu("a", ADMIN_ROLE)
        assert (await c.put("/api/v1/external/cache/config", json=body)).status_code == 200


async def test_masked_key_not_sent_to_new_target(factory, monkeypatch):
    import app.settings.router as srt
    monkeypatch.setattr(srt, "async_session", factory)
    async with factory() as s:
        for k, v in {"llm_provider": "openai", "llm_api_url": "https://api.openai.com/v1",
                     "llm_api_key": "sk-real"}.items():
            s.add(CatalogConfiguration(category="llm", config_key=k, config_value=v))
        await s.commit()
    mask = srt._SECRET_MASK
    assert not await srt._masked_key_retargeted(mask, "llm", "llm", "openai", "https://api.openai.com/v1/")
    assert await srt._masked_key_retargeted(mask, "llm", "llm", "openai", "https://evil.example/v1")
    assert await srt._masked_key_retargeted(mask, "llm", "llm", "anthropic", "https://api.openai.com/v1")
    assert not await srt._masked_key_retargeted("sk-new", "llm", "llm", "openai", "https://evil.example/v1")

    r = await srt.test_llm(tu("a", ADMIN_ROLE), srt.LLMConfig(provider="openai", api_key=mask,
                                                              api_url="https://evil.example/v1"))
    assert not r.success and "다시 입력" in r.message
    async with factory() as s:
        with pytest.raises(HTTPException) as e:
            await srt.update_llm_config(tu("a", ADMIN_ROLE), srt.LLMConfig(
                provider="openai", api_key=mask, api_url="https://evil.example/v1"), s)
        assert e.value.status_code == 400


async def test_auth_change_guards(factory, monkeypatch):
    import app.settings.router as srt
    monkeypatch.setattr(settings, "auth_type", "local")
    monkeypatch.setattr(settings, "auth_keycloak_admin_role", "argus-admin")
    admin = TokenUser(sub="1", username="admin", roles=["argus-admin"])
    local = dict(admin_role="argus-admin", superuser_role="argus-superuser", user_role="argus-user")

    async def check(**kw):
        async with factory() as s:
            await srt._check_auth_change(srt.AuthConfig(**kw), admin, s)

    async def status_of(**kw):
        try:
            await check(**kw)
        except HTTPException as e:
            return e.status_code, e.detail
        return 200, None

    assert (await status_of(type="ldap", **local))[0] == 400
    assert (await status_of(type="local", admin_role="", superuser_role="x", user_role="y"))[0] == 400
    assert (await status_of(type="local", **{**local, "admin_role": "boss"}))[0] == 400
    assert (await status_of(type="local", **local))[0] == 200  # 변경 없음

    # local → keycloak: 도달 불가면 400, 도달하면 확인 요구(409) → confirm 이면 통과
    kc = dict(type="keycloak", server_url="https://kc.example", realm="argus", client_id="c", **local)
    monkeypatch.setattr(srt, "_discovery_ok", _async_return("연결 실패"))
    assert (await status_of(**kc))[0] == 400
    monkeypatch.setattr(srt, "_discovery_ok", _async_return(None))
    code, detail = await status_of(**kc)
    assert code == 409 and detail["code"] == "confirm_required"
    assert (await status_of(**kc, confirm=True))[0] == 200

    # keycloak 모드에서 현재 계정에 없는 admin 역할로 바꾸면 확인으로도 진행 불가
    monkeypatch.setattr(settings, "auth_type", "keycloak")
    monkeypatch.setattr(settings, "auth_keycloak_server_url", "https://kc.example")
    monkeypatch.setattr(settings, "auth_keycloak_realm", "argus")
    monkeypatch.setattr(settings, "auth_keycloak_client_id", "c")
    code, detail = await status_of(**{**kc, "admin_role": "kc-admin"}, confirm=True)
    assert code == 400 and "관리자 권한을 잃습니다" in detail

    # keycloak → local: 로컬 비밀번호로 로그인 가능한 활성 관리자가 없으면 400
    await _add(factory, "kcadmin", password_hash="(external/keycloak)")
    assert (await status_of(type="local", **local, confirm=True))[0] == 400
    await _add(factory, "localadmin", password_hash=passwords.hash_password("abcdefg1"))
    assert (await status_of(type="local", **local))[0] == 409
    assert (await status_of(type="local", **local, confirm=True))[0] == 200


def _async_return(value):
    async def _f(*_a, **_k):
        return value
    return _f


def test_force_local_env(monkeypatch):
    from types import SimpleNamespace

    from app.settings.service import _apply_force_local
    st = SimpleNamespace(auth_type="keycloak", auth_keycloak_admin_role="kc-admin",
                         auth_keycloak_superuser_role="kc-su", auth_keycloak_user_role="kc-user")
    _apply_force_local(st)
    assert st.auth_type == "keycloak"
    monkeypatch.setenv("ARGUS_AUTH_FORCE_LOCAL", "true")
    _apply_force_local(st)
    assert st.auth_type == "local" and st.auth_keycloak_admin_role == "argus-admin"
