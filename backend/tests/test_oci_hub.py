# SPDX-License-Identifier: Apache-2.0
"""OCI 모델 허브 단위 테스트 (자기완결형, in-memory SQLite + 가짜 S3).

통합 테스트 문서(design/integration-test/12-oci-hub.md)의 규칙 중 MinIO·HuggingFace·PostgreSQL
없이 검증할 수 있는 것을 자동화한다. 라우터를 HTTP 로 호출해 권한·상태 코드까지 확인하고,
S3(model_store)·HuggingFace 호출은 가짜로 바꾼다.

다루지 않는 것: 대시보드 통계(get_hub_stats — PostgreSQL 전용 date_trunc 사용), 실제 S3 업로드.

실행:
    cd backend
    pytest tests/test_oci_hub.py
"""

from __future__ import annotations

import asyncio
import types
from unittest import mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.catalog.models as cm
import app.models.models as mm
import app.oci_hub.models as om
from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.models import model_store
from app.models.store_router import router as store_router
from app.oci_hub import service
from app.oci_hub.router import router as oci_router


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(_type, _compiler, **_kw):  # SQLite 테스트 DB 에서는 JSON 으로 만든다
    return "JSON"


ADMIN = TokenUser(sub="1", username="admin", roles=[settings.auth_keycloak_admin_role])
ALICE = TokenUser(sub="2", username="alice", roles=[settings.auth_keycloak_user_role])
BOB = TokenUser(sub="3", username="bob", roles=[settings.auth_keycloak_user_role])


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    import app.permissions.models  # noqa: F401  — 기능 권한(require_feature) 조회용 테이블 등록
    tables = [
        cm.Base.metadata.tables["argus_permissions"],
        cm.Base.metadata.tables["catalog_tags"],
        mm.RegisteredModel.__table__,
        mm.ModelDownloadLog.__table__,
        om.OciModel.__table__,
        om.OciModelVersion.__table__,
        om.OciModelTag.__table__,
        om.OciModelLineage.__table__,
        om.OciModelDownloadLog.__table__,
    ]
    # FK 를 켰으므로 참조 대상 테이블도 함께 만든다
    pending, seen = list(tables), set()
    while pending:
        t = pending.pop()
        if t.name in seen:
            continue
        seen.add(t.name)
        if t not in tables:
            tables.append(t)
        pending.extend(fk.column.table for fk in t.foreign_keys)

    async with engine.begin() as conn:
        await conn.run_sync(lambda c: cm.Base.metadata.create_all(c, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


class Api:
    """테스트용 HTTP 클라이언트 — ``as_(user)`` 로 요청자를 바꾼다(None = 비로그인)."""

    def __init__(self, factory):
        self.factory = factory
        self.user: TokenUser | None = ADMIN
        app = FastAPI()
        app.include_router(oci_router, prefix="/api/v1")
        app.include_router(store_router, prefix="/api/v1")

        async def _session():
            async with factory() as db:
                yield db

        async def _user():
            if self.user is None:
                raise HTTPException(status_code=401, detail="인증이 필요합니다.")
            return self.user

        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = _user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1")

    def as_(self, user: TokenUser | None) -> "Api":
        self.user = user
        return self

    async def req(self, method: str, path: str, **kw) -> httpx.Response:
        return await self.client.request(method, path, **kw)


@pytest.fixture
async def api():
    a = Api(await _make_factory())
    yield a
    await a.client.aclose()


@pytest.fixture
def fake_store(monkeypatch):
    """model_store 의 S3 접근을 가짜로 바꾼다. ``files[(name, version)] = [(파일명, 크기)]``."""
    files: dict[tuple[str, int], list[tuple[str, int]]] = {}

    async def get_total_size(name, version):
        entries = files.get((name, version), [])
        return len(entries), sum(size for _, size in entries)

    async def generate_manifest(name, version):
        return {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "layers": [{"annotations": {"org.opencontainers.image.title": f}, "size": s}
                       for f, s in files.get((name, version), [])],
        }

    async def generate_download_url(name, version, filename):
        if "/" in filename or ".." in filename:
            raise ValueError("잘못된 파일 이름")
        if filename not in {f for f, _ in files.get((name, version), [])}:
            raise FileNotFoundError(filename)
        return {"url": f"http://s3/{name}/v{version}/{filename}", "key": f"{name}/v{version}/{filename}", "expires_in": 3600}

    async def generate_download_urls(name, version):
        return {f: f"http://s3/{name}/v{version}/{f}" for f, _ in files.get((name, version), [])}

    deleted_prefixes: list[str] = []

    async def delete_prefix(bucket, prefix):
        deleted_prefixes.append(prefix)
        return 0

    monkeypatch.setattr(model_store, "get_total_size", get_total_size)
    monkeypatch.setattr(model_store, "generate_manifest", generate_manifest)
    monkeypatch.setattr(model_store, "generate_download_url", generate_download_url)
    monkeypatch.setattr(model_store, "generate_download_urls", generate_download_urls)
    monkeypatch.setattr(service, "_delete_s3_prefix", delete_prefix)
    return types.SimpleNamespace(files=files, deleted_prefixes=deleted_prefixes)


async def _create(api: Api, name: str, user: TokenUser = ADMIN, **body) -> httpx.Response:
    return await api.as_(user).req("POST", "/oci-models", json={"name": name, **body})


# ---------------------------------------------------------------------------
# 생성 · 이름 규칙 · 권한
# ---------------------------------------------------------------------------


async def test_create_sets_draft_owner_and_storage_prefix(api):
    r = await _create(api, "it-onnx-rec", display_name="IT 추천", task="recommendation")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["created_by"] == "admin" and body["owner"] == "admin"
    assert body["storage_prefix"] == "it-onnx-rec/"


async def test_create_rejects_duplicates_bad_names_and_anonymous(api):
    assert (await _create(api, "dup")).status_code == 200
    assert (await _create(api, "dup")).status_code == 409
    assert (await _create(api, "../x")).status_code == 422
    assert (await _create(api, "anon", user=None)).status_code == 401


async def test_create_conflicts_with_mlflow_model_name(api):
    async with api.factory() as db:
        db.add(mm.RegisteredModel(name="shared-name", urn="urn:mlflow:shared-name"))
        await db.commit()
    r = await _create(api, "shared-name")
    assert r.status_code == 409
    assert "MLflow" in r.json()["detail"]


async def test_only_admin_can_create_with_approved_or_production(api):
    assert (await _create(api, "u1", user=ALICE, status="approved")).status_code == 403
    assert (await _create(api, "u2", user=ALICE, status="production")).status_code == 403
    assert (await _create(api, "u3", user=ALICE, status="review")).status_code == 200
    assert (await _create(api, "a1", user=ADMIN, status="approved")).status_code == 200


async def test_editor_guard_owner_creator_admin(api):
    await _create(api, "alice-model", user=ALICE)
    assert (await api.as_(BOB).req("PATCH", "/oci-models/alice-model", json={"description": "x"})).status_code == 403
    assert (await api.as_(ALICE).req("PATCH", "/oci-models/alice-model", json={"description": "x"})).status_code == 200
    assert (await api.as_(ADMIN).req("PATCH", "/oci-models/alice-model", json={"description": "y"})).status_code == 200
    # 소유자를 bob 으로 넘기면 bob 도 편집 가능(생성자 alice 는 그대로 가능)
    await api.as_(ADMIN).req("PATCH", "/oci-models/alice-model", json={"owner": "bob"})
    assert (await api.as_(BOB).req("PUT", "/oci-models/alice-model/readme", json={"readme": "b"})).status_code == 200
    assert (await api.as_(ALICE).req("PUT", "/oci-models/alice-model/readme", json={"readme": "a"})).status_code == 200
    assert (await api.as_(ADMIN).req("PATCH", "/oci-models/no-such", json={"description": "x"})).status_code == 404


# ---------------------------------------------------------------------------
# PATCH 부분 수정 · 상태 변경 권한
# ---------------------------------------------------------------------------


async def test_patch_updates_only_sent_fields_and_null_clears(api):
    await _create(api, "m", description="원래 설명", license="apache-2.0", task="nlp")
    r = await api.as_(ADMIN).req("PATCH", "/oci-models/m", json={"description": "새 설명"})
    body = r.json()
    assert body["description"] == "새 설명"
    assert body["license"] == "apache-2.0" and body["task"] == "nlp"  # 보내지 않은 필드는 유지

    body = (await api.req("PATCH", "/oci-models/m", json={"license": None})).json()
    assert body["license"] is None  # 명시적 null 은 값 지움
    assert body["task"] == "nlp"

    body = (await api.req("PATCH", "/oci-models/m", json={"status": None})).json()
    assert body["status"] == "draft"  # 상태는 비울 수 없어 null 은 무시


async def test_owner_cannot_promote_but_can_edit_approved_model(api):
    await _create(api, "mine", user=ALICE)
    assert (await api.as_(ALICE).req("PATCH", "/oci-models/mine", json={"status": "approved"})).status_code == 403
    assert (await api.as_(ALICE).req("PATCH", "/oci-models/mine", json={"status": "deprecated"})).status_code == 200
    await api.as_(ADMIN).req("PATCH", "/oci-models/mine", json={"status": "approved"})
    # 이미 승인된 모델의 다른 필드(상태 값 그대로 포함)는 소유자가 고칠 수 있다
    r = await api.as_(ALICE).req("PATCH", "/oci-models/mine", json={"status": "approved", "description": "보완"})
    assert r.status_code == 200 and r.json()["description"] == "보완"


# ---------------------------------------------------------------------------
# 목록 · 필터 · facets
# ---------------------------------------------------------------------------


async def test_list_search_filters_paging_and_facets(api):
    await _create(api, "bert-ko", task="nlp", framework="pytorch", language="ko", description="한국어 BERT")
    await _create(api, "resnet", task="vision", framework="onnx")
    await _create(api, "other", task="nlp", framework="onnx")

    def names(r):
        return sorted(i["name"] for i in r.json()["items"])

    assert names(await api.as_(None).req("GET", "/oci-models", params={"search": "BERT"})) == ["bert-ko"]
    assert names(await api.req("GET", "/oci-models", params={"search": "한국어"})) == ["bert-ko"]
    assert names(await api.req("GET", "/oci-models", params={"task": "nlp", "framework": "onnx"})) == ["other"]
    page = (await api.req("GET", "/oci-models", params={"page_size": 2, "page": 2})).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert (await api.req("GET", "/oci-models", params={"page_size": 51})).status_code == 422

    facets = (await api.req("GET", "/oci-models/facets")).json()
    assert facets["tasks"] == ["nlp", "vision"]
    assert facets["frameworks"] == ["onnx", "pytorch"]
    assert facets["languages"] == ["ko"]
    assert facets["statuses"] == ["draft"]


# ---------------------------------------------------------------------------
# 태그 · 리니지
# ---------------------------------------------------------------------------


async def test_tags_add_remove_conflict_and_missing(api):
    await _create(api, "m")
    async with api.factory() as db:
        tag = cm.Tag(name="it-oci-tag")
        db.add(tag)
        await db.commit()
        tag_id = tag.id
    assert (await api.as_(ADMIN).req("POST", f"/oci-models/m/tags/{tag_id}")).status_code == 200
    assert [t["name"] for t in (await api.req("GET", "/oci-models/m")).json()["tags"]] == ["it-oci-tag"]
    assert (await api.req("POST", f"/oci-models/m/tags/{tag_id}")).status_code == 409
    assert (await api.req("POST", "/oci-models/m/tags/999999")).status_code == 404
    assert (await api.as_(BOB).req("POST", f"/oci-models/m/tags/{tag_id}")).status_code == 403
    assert (await api.as_(ADMIN).req("DELETE", f"/oci-models/m/tags/{tag_id}")).status_code == 200
    assert (await api.req("DELETE", f"/oci-models/m/tags/{tag_id}")).status_code == 404


async def test_lineage_add_validate_duplicate_and_scoped_delete(api):
    await _create(api, "m1")
    await _create(api, "m2")
    body = {"source_type": "dataset", "source_id": "42", "relation_type": "trained_on"}
    r = await api.as_(ADMIN).req("POST", "/oci-models/m1/lineage", json=body)
    assert r.status_code == 200
    lid = r.json()["id"]
    assert (await api.req("POST", "/oci-models/m1/lineage", json=body)).status_code == 409
    assert (await api.req("POST", "/oci-models/m1/lineage", json={**body, "relation_type": "bogus"})).status_code == 422
    assert (await api.req("POST", "/oci-models/m1/lineage", json={**body, "source_type": "bogus"})).status_code == 422
    assert (await api.as_(BOB).req("POST", "/oci-models/m1/lineage", json={**body, "source_id": "1"})).status_code == 403
    # 다른 모델 경로로는 지울 수 없다
    assert (await api.as_(ADMIN).req("DELETE", f"/oci-models/m2/lineage/{lid}")).status_code == 404
    assert len((await api.req("GET", "/oci-models/m1/lineage")).json()) == 1
    assert (await api.req("DELETE", f"/oci-models/m1/lineage/{lid}")).status_code == 200
    assert (await api.req("GET", "/oci-models/no-such/lineage")).status_code == 404


async def test_linked_models_from_mlflow(api):
    await _create(api, "by-source", source_type="mlflow", source_id="iris")
    await _create(api, "by-lineage")
    await _create(api, "unrelated")
    await api.req("POST", "/oci-models/by-lineage/lineage",
                  json={"source_type": "mlflow", "source_id": "iris", "relation_type": "distilled_from"})
    await api.req("POST", "/oci-models/unrelated/lineage",
                  json={"source_type": "mlflow", "source_id": "other", "relation_type": "converted_from"})

    r = await api.as_(None).req("GET", "/oci-models/linked", params={"mlflow_model": "iris"})
    assert r.status_code == 200
    assert [(m["name"], m["relation_type"]) for m in r.json()] == [
        ("by-lineage", "distilled_from"),
        ("by-source", "converted_from"),
    ]
    assert (await api.req("GET", "/oci-models/linked", params={"mlflow_model": "none"})).json() == []
    assert (await api.req("GET", "/oci-models/linked")).status_code == 422


async def test_linked_relation_is_first_registered_lineage(api):
    """같은 MLflow 모델을 가리키는 리니지가 여러 개면 먼저 등록된 리니지의 관계를 쓴다(항상 같은 결과)."""
    await _create(api, "m")
    for rel in ("distilled_from", "converted_from", "fine_tuned_from"):
        r = await api.as_(ADMIN).req("POST", "/oci-models/m/lineage",
                                     json={"source_type": "mlflow", "source_id": "iris", "relation_type": rel})
        assert r.status_code == 200
    for _ in range(3):
        linked = (await api.req("GET", "/oci-models/linked", params={"mlflow_model": "iris"})).json()
        assert [(m["name"], m["relation_type"]) for m in linked] == [("m", "distilled_from")]


# ---------------------------------------------------------------------------
# 버전 · finalize (수동 푸시)
# ---------------------------------------------------------------------------


async def test_finalize_without_files_is_400(api, fake_store):
    await _create(api, "m")
    r = await api.as_(ADMIN).req("POST", "/oci-models/m/versions/1/finalize", json={})
    assert r.status_code == 400
    assert "업로드된 파일이 없습니다" in r.json()["detail"]


async def test_admin_finalize_records_digest_stats_readme_and_approves(api, fake_store):
    await _create(api, "m")
    fake_store.files[("m", 1)] = [("model.onnx", 4096), ("config.json", 14)]
    r = await api.as_(ADMIN).req("POST", "/oci-models/m/versions/1/finalize", json={"readme": "# IT"})
    assert r.status_code == 200, r.text
    ver = r.json()
    assert ver["status"] == "ready" and ver["file_count"] == 2 and ver["total_size"] == 4110
    assert ver["content_digest"].startswith("sha256:")
    assert "application/vnd.oci.image.manifest.v1+json" in ver["manifest"]

    model = (await api.req("GET", "/oci-models/m")).json()
    assert model["status"] == "approved"  # 관리자 확정 → draft 에서 승인
    assert model["version_count"] == 1 and model["total_size"] == 4110
    assert model["readme"] == "# IT"


async def test_owner_finalize_keeps_draft_and_stats_sum_ready_versions(api, fake_store):
    await _create(api, "mine", user=ALICE)
    fake_store.files[("mine", 1)] = [("a.bin", 100)]
    fake_store.files[("mine", 2)] = [("a.bin", 300)]
    assert (await api.as_(BOB).req("POST", "/oci-models/mine/versions/1/finalize", json={})).status_code == 403
    assert (await api.as_(ALICE).req("POST", "/oci-models/mine/versions/1/finalize", json={})).status_code == 200
    assert (await api.req("POST", "/oci-models/mine/versions/2/finalize", json={})).status_code == 200

    model = (await api.req("GET", "/oci-models/mine")).json()
    assert model["status"] == "draft"  # 소유자 확정은 상태를 바꾸지 않는다
    assert model["version_count"] == 2 and model["total_size"] == 400
    versions = (await api.req("GET", "/oci-models/mine/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert (await api.req("GET", "/oci-models/no-such/versions")).status_code == 404


async def test_store_upload_guard(api):
    await _create(api, "mine", user=ALICE)
    files = {"file": ("model.onnx", b"x")}
    r = await api.as_(ALICE).req("POST", "/model-store/not-registered/versions/1/upload", files=files)
    assert r.status_code == 404
    r = await api.as_(BOB).req("POST", "/model-store/mine/versions/1/upload", files=files)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 다운로드 집계
# ---------------------------------------------------------------------------


async def test_download_count_only_on_successful_issue(api, fake_store):
    await _create(api, "m")
    fake_store.files[("m", 1)] = [("model.onnx", 10), ("config.json", 1)]

    async def count():
        return (await api.as_(None).req("GET", "/oci-models/m")).json()["download_count"]

    assert await count() == 0
    path = "/model-store/m/versions/1/download-url"
    assert (await api.as_(None).req("GET", path, params={"filename": "model.onnx"})).status_code == 401
    assert (await api.as_(ADMIN).req("GET", path, params={"filename": "nope.bin"})).status_code == 404
    assert (await api.req("GET", path, params={"filename": "../x"})).status_code == 400
    assert await count() == 0

    assert (await api.as_(ADMIN).req("GET", path, params={"filename": "model.onnx"})).status_code == 200
    assert (await api.req("GET", "/model-store/m/versions/1/download-urls")).status_code == 200
    assert (await api.req("GET", "/model-store/m/versions/9/download-urls")).status_code == 404
    assert await count() == 2

    async with api.factory() as db:
        logs = (await db.execute(select(om.OciModelDownloadLog.download_type))).scalars().all()
    assert sorted(logs) == ["download", "pull"]


# ---------------------------------------------------------------------------
# 삭제
# ---------------------------------------------------------------------------


async def test_delete_permissions_and_s3_prefix(api, fake_store):
    await _create(api, "admin-model")
    await _create(api, "alice-model", user=ALICE)
    assert (await api.as_(ALICE).req("DELETE", "/oci-models/admin-model")).status_code == 403
    assert (await api.as_(ALICE).req("DELETE", "/oci-models/alice-model")).status_code == 200
    assert (await api.as_(ADMIN).req("DELETE", "/oci-models/admin-model")).status_code == 200
    assert (await api.req("GET", "/oci-models/admin-model")).status_code == 404
    assert (await api.req("DELETE", "/oci-models/admin-model")).status_code == 404
    assert sorted(fake_store.deleted_prefixes) == ["admin-model/", "alice-model/"]


# ---------------------------------------------------------------------------
# HuggingFace 가져오기 (접수 · 상태 전이)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_hf(monkeypatch, api, fake_store):
    """HF 저장소 확인은 통과시키고, 백그라운드 실행은 기록만 한다(버전은 importing 으로 남는다)."""
    started: list[tuple] = []

    async def check(hf_model_id, revision):
        if hf_model_id.startswith("no-such"):
            raise service.OciUpstreamError("없음", 404)
        if hf_model_id.startswith("gated"):
            raise service.OciUpstreamError("gated", 400)

    async def run(*args):
        started.append(args)

    monkeypatch.setattr(service, "_check_hf_repo", check)
    monkeypatch.setattr(service, "_run_hf_import", run)
    monkeypatch.setattr("app.core.database.async_session", api.factory)
    return started


async def _import(api, hf_id, user=ADMIN, **body):
    return await api.as_(user).req("POST", "/oci-models/import/huggingface", json={"hf_model_id": hf_id, **body})


async def test_hf_import_accepts_and_blocks_while_importing(api, fake_hf):
    r = await _import(api, "prajjwal1/bert-tiny")
    assert r.status_code == 202
    assert {k: r.json()[k] for k in ("name", "version", "status")} == {
        "name": "prajjwal1-bert-tiny", "version": 1, "status": "importing"}
    await asyncio.sleep(0)  # 백그라운드 태스크 실행
    assert fake_hf == [(mock.ANY, "prajjwal1-bert-tiny", 1, "prajjwal1/bert-tiny", "main")]

    model = (await api.req("GET", "/oci-models/prajjwal1-bert-tiny")).json()
    assert model["source_type"] == "huggingface" and model["status"] == "draft"
    assert [v["status"] for v in (await api.req("GET", "/oci-models/prajjwal1-bert-tiny/versions")).json()] == ["importing"]

    assert (await _import(api, "prajjwal1/bert-tiny")).status_code == 409  # 진행 중 재요청
    assert (await api.req("DELETE", "/oci-models/prajjwal1-bert-tiny")).status_code == 409  # 진행 중 삭제
    assert (await api.req("POST", "/oci-models/prajjwal1-bert-tiny/versions/1/finalize", json={})).status_code == 409


async def test_hf_import_rejections(api, fake_hf):
    assert (await _import(api, "prajjwal1/bert-tiny", user=ALICE)).status_code == 403
    assert (await _import(api, "no-such-org/x")).status_code == 404
    assert (await _import(api, "gated/x")).status_code == 400
    assert (await _import(api, "a/b", name="../x")).status_code in (400, 422)
    # 실패한 요청은 모델 행을 남기지 않는다
    assert (await api.req("GET", "/oci-models", params={"page_size": 50})).json()["total"] == 0


async def test_check_hf_repo_rejects_gated_repo_without_access(monkeypatch):
    """gated 저장소는 model_info 가 성공하므로 접근 권한(auth_check)을 따로 확인해 400 으로 막는다."""
    import huggingface_hub
    from huggingface_hub.utils import GatedRepoError

    class FakeApi:
        checked: list[str] = []

        def model_info(self, repo_id, revision=None):
            return mock.Mock(gated="manual" if repo_id.startswith("gated") else False)

        def auth_check(self, repo_id):
            FakeApi.checked.append(repo_id)
            raise GatedRepoError("no access", response=mock.Mock(headers={}, status_code=401))

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    with pytest.raises(service.OciUpstreamError) as e:
        await service._check_hf_repo("gated/llama", "main")
    assert e.value.status_code == 400 and "gated" in str(e.value)
    await service._check_hf_repo("org/public", "main")  # 공개 저장소는 접근 확인을 하지 않는다
    assert FakeApi.checked == ["gated/llama"]


async def test_hf_import_next_version_skips_failed_numbers(api, fake_hf):
    await _import(api, "org/m")
    async with api.factory() as db:
        assert await service.fail_stale_imports(db) == 1  # v1 → failed (서버 재시작 정리)
    r = await _import(api, "org/m")
    assert r.status_code == 202 and r.json()["version"] == 2
    versions = (await api.req("GET", "/oci-models/org-m/versions")).json()
    assert [(v["version"], v["status"]) for v in versions] == [(2, "importing"), (1, "failed")]
    assert "서버 재시작" in versions[1]["extra_metadata"]["error"]


async def _start_import_version(api, monkeypatch) -> None:
    """모델과 importing 버전 1을 만든다(백그라운드는 실행하지 않음)."""
    monkeypatch.setattr(service, "_check_hf_repo", mock.AsyncMock())
    monkeypatch.setattr(service, "_run_hf_import", mock.AsyncMock())
    async with api.factory() as db:
        await service.start_hf_import(db, hf_model_id="org/m", created_by="admin")
    await asyncio.sleep(0)


async def test_hf_background_failure_marks_failed_and_cleans_partial_upload(api, monkeypatch, fake_store):
    real_run = service._run_hf_import
    await _start_import_version(api, monkeypatch)
    monkeypatch.setattr("app.core.database.async_session", api.factory)
    monkeypatch.setattr(model_store, "import_from_huggingface", mock.AsyncMock(side_effect=RuntimeError("네트워크 끊김")))

    model_id = (await api.req("GET", "/oci-models/org-m")).json()["id"]
    await real_run(model_id, "org-m", 1, "org/m", "main")

    versions = (await api.req("GET", "/oci-models/org-m/versions")).json()
    assert versions[0]["status"] == "failed"
    assert "네트워크 끊김" in versions[0]["extra_metadata"]["error"]
    assert fake_store.deleted_prefixes == ["org-m/v1/"]
    assert (await api.req("GET", "/oci-models/org-m")).json()["status"] == "draft"


async def test_hf_background_success_marks_ready_and_approves(api, monkeypatch, fake_store):
    real_run = service._run_hf_import
    await _start_import_version(api, monkeypatch)
    monkeypatch.setattr("app.core.database.async_session", api.factory)
    monkeypatch.setattr(model_store, "import_from_huggingface", mock.AsyncMock(return_value={
        "file_count": 3, "total_size": 999, "model_type": "bert", "transformers_version": "4.0",
        "readme": "# bert-tiny", "manifest": {"schemaVersion": 2, "layers": []},
    }))

    model_id = (await api.req("GET", "/oci-models/org-m")).json()["id"]
    await real_run(model_id, "org-m", 1, "org/m", "main")

    ver = (await api.req("GET", "/oci-models/org-m/versions")).json()[0]
    assert ver["status"] == "ready" and ver["file_count"] == 3
    assert ver["content_digest"].startswith("sha256:")
    model = (await api.req("GET", "/oci-models/org-m")).json()
    assert model["status"] == "approved" and model["readme"] == "# bert-tiny"
    assert model["framework"] == "pytorch" and model["version_count"] == 1 and model["total_size"] == 999


# ---------------------------------------------------------------------------
# 버전 삭제 · 모델 삭제 시 다운로드 기록 정리
# ---------------------------------------------------------------------------


async def _add_version(api, name: str, version: int, status: str, size: int = 0) -> None:
    async with api.factory() as db:
        m = (await db.execute(select(om.OciModel).where(om.OciModel.name == name))).scalars().one()
        db.add(om.OciModelVersion(model_id=m.id, version=version, status=status, total_size=size))
        await db.commit()


async def test_version_delete_permissions_conflict_and_stats(api, fake_store):
    await _create(api, "mine", user=ALICE)
    fake_store.files[("mine", 1)] = [("a.bin", 100)]
    fake_store.files[("mine", 2)] = [("a.bin", 300)]
    assert (await api.as_(ALICE).req("POST", "/oci-models/mine/versions/1/finalize", json={})).status_code == 200
    assert (await api.req("POST", "/oci-models/mine/versions/2/finalize", json={})).status_code == 200
    await _add_version(api, "mine", 3, "importing")

    path = "/oci-models/mine/versions/{}"
    assert (await api.as_(None).req("DELETE", path.format(2))).status_code == 401
    assert (await api.as_(BOB).req("DELETE", path.format(2))).status_code == 403
    r = await api.as_(ALICE).req("DELETE", path.format(3))
    assert r.status_code == 409 and "가져오기가 진행 중" in r.json()["detail"]
    assert (await api.req("DELETE", path.format(9))).status_code == 404
    assert (await api.req("DELETE", "/oci-models/no-such/versions/1")).status_code == 404

    assert (await api.req("DELETE", path.format(2))).status_code == 200
    assert fake_store.deleted_prefixes == ["mine/v2/"]
    model = (await api.req("GET", "/oci-models/mine")).json()
    assert model["version_count"] == 1 and model["total_size"] == 100  # 남은 ready 버전 기준으로 재계산
    assert [v["version"] for v in (await api.req("GET", "/oci-models/mine/versions")).json()] == [3, 1]
    assert (await api.as_(ADMIN).req("DELETE", path.format(1))).status_code == 200  # 관리자도 가능


async def test_version_delete_failed_version_and_storage_outage(api, fake_store, monkeypatch):
    await _create(api, "mine", user=ALICE)
    await _add_version(api, "mine", 1, "failed")

    async def down(*_a, **_kw):
        raise ConnectionError("Could not connect to the endpoint URL")

    monkeypatch.setattr(service, "_delete_s3_prefix", down)
    assert (await api.as_(ALICE).req("DELETE", "/oci-models/mine/versions/1")).status_code == 200  # DB 삭제는 유지
    assert (await api.req("GET", "/oci-models/mine/versions")).json() == []


async def test_model_delete_clears_download_logs(api, fake_store):
    await _create(api, "gone")
    async with api.factory() as db:
        db.add_all([om.OciModelDownloadLog(model_name="gone", version=1, download_type="pull"),
                    om.OciModelDownloadLog(model_name="kept", version=1, download_type="pull")])
        await db.commit()
    assert (await api.as_(ADMIN).req("DELETE", "/oci-models/gone")).status_code == 200
    async with api.factory() as db:
        names = (await db.execute(select(om.OciModelDownloadLog.model_name))).scalars().all()
    assert names == ["kept"]
