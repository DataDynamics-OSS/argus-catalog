# SPDX-License-Identifier: Apache-2.0
"""페더레이션 잔여 결함 회귀 테스트 (09-federation.md 알려진 이슈).

자기완결형(in-memory SQLite, 네트워크 호출 없음 — peer 호출은 stub). 검증 범위:
- FED-T12: import 사본("Federation Imports")은 export 목록·리니지·드릴다운 가드에서 항상 제외
- FED-T16: probe-capabilities base_url 검증(422), mode/status 허용 값 검증(422)
- FED-T20: 개별 수동 가져오기 — PAUSED/LIVE peer 는 409, ACTIVE 미러 모드만 실행
- 자기 자신 등록 차단(federation.public_base_url 설정 시)
- 패키지 기본 config.yml 의 federation 섹션

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_federation_remaining_fixes.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.federation.models as fm
from app.core.config import settings
from app.federation import schemas as s
from app.settings.models import CatalogConfiguration


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    models = (
        cm.Datasource, cm.Dataset, cm.DatasetSchema, cm.DatasetLineage,
        CatalogConfiguration, fm.FederatedInstance, fm.FederationSyncRun,
    )
    async with engine.begin() as conn:
        for m in models:
            await conn.run_sync(lambda sc, m=m: m.__table__.create(sc, checkfirst=True))
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# FED-T12 — import 사본 export 제외(에코 방지)
# ---------------------------------------------------------------------------

async def _seed_with_import(db) -> dict[str, int]:
    from app.federation.importer import _IMPORT_DS_NAME, _IMPORT_DS_TYPE

    local = cm.Datasource(datasource_id="mysql-a", name="mysql-a", type="mysql", origin="PROD")
    imp = cm.Datasource(datasource_id="fed-imp", name=_IMPORT_DS_NAME,
                        type=_IMPORT_DS_TYPE, origin="PROD")
    db.add_all([local, imp])
    await db.commit()
    own = cm.Dataset(urn="mysql-a.t.dataset", name="t", datasource_id=local.id,
                     status="active", origin="PROD")
    copied = cm.Dataset(urn="fed-imp.peer_t.dataset", name="peer_t", datasource_id=imp.id,
                        status="active", origin="PROD")
    db.add_all([own, copied])
    await db.commit()
    db.add(cm.DatasetLineage(source_dataset_id=copied.id, target_dataset_id=own.id,
                             relation_type="ETL", lineage_source="MANUAL"))
    await db.commit()
    return {"own": own.id, "copied": copied.id}


async def _test_import_excluded() -> None:
    from app.federation import visibility
    from app.federation.export_service import build_export_datasets, build_export_lineage
    from app.federation.importer import _IMPORT_DS_NAME

    assert visibility.IMPORT_DATASOURCE_NAME == _IMPORT_DS_NAME

    saved = (settings.federation_export_exclude_pii,
             settings.federation_export_exclude_sensitivity,
             settings.federation_export_datasource_allowlist)
    settings.federation_export_exclude_pii = False
    settings.federation_export_exclude_sensitivity = []
    settings.federation_export_datasource_allowlist = []
    try:
        factory = await _make_session()
        async with factory() as db:
            ids = await _seed_with_import(db)
            # 정책이 없어도 import 사본은 제외
            assert not visibility.has_visibility_policy()
            allowed = await visibility.exportable_dataset_ids(db, list(ids.values()))
            assert allowed == {ids["own"]}
            assert not await visibility.is_dataset_exportable(db, ids["copied"])
            assert await visibility.is_dataset_exportable(db, ids["own"])

            page = await build_export_datasets(db, limit=100, offset=0)
            assert {it.urn for it in page.items} == {"mysql-a.t.dataset"}
            assert page.total == 1

            # 한쪽 끝이 import 사본인 리니지 엣지도 노출하지 않는다
            lin = await build_export_lineage(db)
            assert lin.edges == [] and lin.total == 0
    finally:
        (settings.federation_export_exclude_pii,
         settings.federation_export_exclude_sensitivity,
         settings.federation_export_datasource_allowlist) = saved


def test_import_datasets_not_exported():
    asyncio.run(_test_import_excluded())


# ---------------------------------------------------------------------------
# FED-T16 — 스키마 검증
# ---------------------------------------------------------------------------

def test_probe_request_validates_base_url():
    for bad in ("file:///etc/passwd", "ftp://h/x", "/relative", "http://u:p@peer:4600"):
        with pytest.raises(ValidationError):
            s.ProbeCapabilitiesRequest(base_url=bad)
    assert s.ProbeCapabilitiesRequest(base_url=" http://peer:4600/ ").base_url == "http://peer:4600"


def test_mode_and_status_validated():
    base = {"instance_key": "p1", "name": "p", "base_url": "http://peer:4600"}
    assert s.FederatedInstanceCreate(**base).mode == "LIVE"
    assert s.FederatedInstanceCreate(**base, mode="harvest").mode == "HARVEST"
    with pytest.raises(ValidationError):
        s.FederatedInstanceCreate(**base, mode="MIRROR")
    assert s.FederatedInstanceUpdate(status="paused").status == "PAUSED"
    assert s.FederatedInstanceUpdate(mode="HYBRID").mode == "HYBRID"
    assert s.FederatedInstanceUpdate().status is None
    with pytest.raises(ValidationError):
        s.FederatedInstanceUpdate(status="DISABLED")
    with pytest.raises(ValidationError):
        s.FederatedInstanceUpdate(mode="")


# ---------------------------------------------------------------------------
# HTTP — probe 422 / 수동 가져오기 409 / 자기 자신 등록 차단
# ---------------------------------------------------------------------------

async def _seed_instances(factory) -> dict[str, int]:
    async with factory() as db:
        rows = {
            "ok": fm.FederatedInstance(instance_key="ok", name="ok", base_url="http://ok:4600",
                                       mode="HARVEST", status="ACTIVE"),
            "paused": fm.FederatedInstance(instance_key="paused", name="paused",
                                           base_url="http://paused:4600",
                                           mode="HYBRID", status="PAUSED"),
            "live": fm.FederatedInstance(instance_key="live", name="live",
                                         base_url="http://live:4600",
                                         mode="LIVE", status="ACTIVE"),
        }
        db.add_all(list(rows.values()))
        await db.commit()
        return {k: v.id for k, v in rows.items()}


def test_http_federation_fixes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.auth import TokenUser, get_current_user
    from app.core.database import get_session
    from app.federation import client as fed_client
    from app.federation import router as fed_router

    factory = asyncio.run(_make_session())
    ids = asyncio.run(_seed_instances(factory))

    app = FastAPI()
    app.include_router(fed_router.router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    who = {"admin": True}

    def _current():
        roles = [settings.auth_keycloak_admin_role] if who["admin"] else ["argus-user"]
        return TokenUser(sub="u", username="u", roles=roles)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current

    # peer 호출 stub — 네트워크 금지. 호출되면 기록.
    probe_calls: list[str] = []

    async def _fake_caps(inst):
        probe_calls.append(inst.base_url)
        return s.CapabilitiesResponse(items=[], groups=[], exposed=[])

    monkeypatch.setattr(fed_client, "fetch_export_capabilities", _fake_caps)

    harvest_calls: list[tuple[str, bool]] = []

    async def _fake_harvest(session, inst, full=False):
        harvest_calls.append((inst.instance_key, full))
        return s.FederationSyncResult(instance_key=inst.instance_key, status="SUCCESS")

    monkeypatch.setattr(fed_router, "harvest_instance", _fake_harvest)

    c = TestClient(app)
    api = "/api/v1/federation"

    # --- probe-capabilities: 잘못된 base_url 은 peer 호출 없이 422 ---
    for bad in ("file:///etc/passwd", "http://user:pw@peer:4600"):
        r = c.post(f"{api}/probe-capabilities", json={"base_url": bad})
        assert r.status_code == 422, r.text
    assert probe_calls == []
    r = c.post(f"{api}/probe-capabilities", json={"base_url": "http://peer:4600"})
    assert r.status_code == 200, r.text
    assert probe_calls == ["http://peer:4600"]

    # --- mode/status 검증 ---
    r = c.put(f"{api}/instances/{ids['ok']}", json={"status": "FOO"})
    assert r.status_code == 422, r.text
    r = c.post(f"{api}/instances", json={"instance_key": "x", "name": "x",
                                         "base_url": "http://x:4600", "mode": "MIRROR"})
    assert r.status_code == 422, r.text

    # --- 수동 가져오기: PAUSED/LIVE 는 409, 실행 안 함 ---
    r = c.post(f"{api}/instances/{ids['paused']}/harvest")
    assert r.status_code == 409 and "PAUSED" in r.json()["detail"], r.text
    r = c.post(f"{api}/instances/{ids['live']}/harvest?full=true")
    assert r.status_code == 409 and "LIVE" in r.json()["detail"], r.text
    assert harvest_calls == []
    r = c.post(f"{api}/instances/{ids['ok']}/harvest?full=true")
    assert r.status_code == 200, r.text
    assert harvest_calls == [("ok", True)]
    # admin 가드 유지
    who["admin"] = False
    assert c.post(f"{api}/instances/{ids['ok']}/harvest").status_code == 403
    who["admin"] = True

    # --- 자기 자신 등록 차단 ---
    saved = settings.federation_public_base_url
    try:
        # 미설정 → 판별 불가, 등록 허용
        settings.federation_public_base_url = ""
        r = c.post(f"{api}/instances", json={"instance_key": "me0", "name": "me",
                                             "base_url": "http://catalog.local:4600"})
        assert r.status_code == 201, r.text

        settings.federation_public_base_url = "http://Catalog.Local:4600"
        for url in ("http://catalog.local:4600", "http://CATALOG.local:4600/"):
            r = c.post(f"{api}/instances", json={"instance_key": "me1", "name": "me",
                                                 "base_url": url})
            assert r.status_code == 422 and "자기 자신" in r.json()["detail"], r.text
        r = c.put(f"{api}/instances/{ids['ok']}", json={"base_url": "http://catalog.local:4600"})
        assert r.status_code == 422, r.text
        # 다른 URL·다른 포트는 허용
        r = c.post(f"{api}/instances", json={"instance_key": "other", "name": "o",
                                             "base_url": "http://catalog.local:4601"})
        assert r.status_code == 201, r.text
        # 기본 포트 생략도 같은 주소로 본다
        settings.federation_public_base_url = "https://cat.example.com"
        r = c.post(f"{api}/instances", json={"instance_key": "me2", "name": "me",
                                             "base_url": "https://cat.example.com:443"})
        assert r.status_code == 422, r.text
    finally:
        settings.federation_public_base_url = saved


# ---------------------------------------------------------------------------
# 패키지 기본 config.yml — federation 섹션
# ---------------------------------------------------------------------------

def test_packaging_config_has_federation_section():
    from app.core.config_loader import load_config

    cfg_dir = Path(__file__).resolve().parents[1] / "packaging" / "config"
    fed = load_config(cfg_dir).get("federation")
    assert isinstance(fed, dict)
    # 코드 기본값과 동일해야 한다
    assert fed["harvest_enabled"] == "true"
    assert str(fed["harvest_tick_seconds"]) == "300"
    assert str(fed["full_sync_interval_seconds"]) == "86400"
    assert str(fed["breaker_threshold"]) == "3"
    assert fed["export_token"] == "" and fed["public_base_url"] == ""
