# SPDX-License-Identifier: Apache-2.0
"""MLflow 모델 단위 테스트 (자기완결형, in-memory SQLite + 임시 data_dir + 가짜 S3).

통합 테스트 문서(design/integration-test/11-mlflow-models.md)의 규칙 중 MinIO·실제 MLflow 클라이언트 없이
검증할 수 있는 것을 자동화한다. ``/api/v1/models``·``/api/2.1/unity-catalog``·``/api/v1/model-store`` 라우터를
HTTP 로 호출해 권한·상태 코드·UC 오류 형식까지 확인한다. 로컬 저장소(``data_dir/model-artifacts``)는
테스트마다 임시 디렉터리를 쓰고, S3(model_store)는 가짜로 바꾼다.

다루지 않는 것: 실제 MinIO 업로드·presigned URL, MLflow 클라이언트 연동, HuggingFace·로컬 디렉터리 반입,
파일 브라우저 HTTP API(filesystemmgr — 등록 모델 보호 판단 함수만 직접 검증).

실행:
    cd backend
    pytest tests/test_mlflow_models.py
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.responses import JSONResponse
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import app.catalog.models as cm
import app.models.models as mm
import app.oci_hub.models as om
from app.core.auth import TokenUser, get_current_user, get_optional_user
from app.core.config import settings
from app.core.database import get_session
from app.models import model_store, service
from app.models.router import router as models_router
from app.models.store_router import router as store_router
from app.models.uc_compat import router as uc_router


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(_type, _compiler, **_kw):  # SQLite 테스트 DB 에서는 JSON 으로 만든다
    return "JSON"


ADMIN = TokenUser(sub="1", username="admin", roles=[settings.auth_keycloak_admin_role])
ALICE = TokenUser(sub="2", username="alice", roles=[settings.auth_keycloak_user_role])
BOB = TokenUser(sub="3", username="bob", roles=[settings.auth_keycloak_user_role])

MLMODEL = """\
artifact_path: model
flavors:
  python_function:
    loader_module: mlflow.sklearn
    model_path: model.pkl
    predict_fn: predict
    python_version: 3.12.3
  sklearn:
    pickled_model: model.pkl
    serialization_format: cloudpickle
    sklearn_version: 1.5.2
mlflow_version: 2.17.0
model_size_bytes: 1024
utc_time_created: '2026-11-06 00:00:00.000000'
"""


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
        # 통계 쿼리의 PostgreSQL date_trunc 대용 — 버킷 정확도는 검증 대상이 아니다
        dbapi_conn.create_function("date_trunc", 2, lambda _unit, ts: ts)

    import app.permissions.models  # noqa: F401  — 기능 권한(require_feature) 조회용 테이블 등록
    tables = [
        cm.Base.metadata.tables["argus_permissions"],
        cm.Base.metadata.tables["catalog_datasources"],
        cm.Base.metadata.tables["catalog_datasets"],
        mm.RegisteredModel.__table__,
        mm.ModelVersion.__table__,
        mm.CatalogModel.__table__,
        mm.ModelDownloadLog.__table__,
        mm.ModelMetric.__table__,
        mm.ModelCard.__table__,
        mm.ModelDatasetLineage.__table__,
        om.OciModel.__table__,
        om.OciModelVersion.__table__,
        om.OciModelDownloadLog.__table__,
    ]
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
        app.include_router(models_router, prefix="/api/v1")
        app.include_router(store_router, prefix="/api/v1")
        app.include_router(uc_router)

        @app.exception_handler(StarletteHTTPException)
        async def _uc_errors(request: Request, exc: StarletteHTTPException):
            # app.main 과 같은 규칙 — UC 경로의 HTTPException 은 UC 오류 형식으로
            from app.main import _UC_ERROR_CODES, _UC_PREFIX

            if not request.url.path.startswith(_UC_PREFIX):
                return await default_http_exception_handler(request, exc)
            return JSONResponse(status_code=exc.status_code, content={
                "error_code": _UC_ERROR_CODES.get(exc.status_code, "INTERNAL_ERROR"), "message": str(exc.detail),
            })

        async def _session():
            async with factory() as db:
                yield db

        async def _user():
            if self.user is None:
                raise HTTPException(status_code=401, detail="인증이 필요합니다.")
            return self.user

        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_optional_user] = lambda: self.user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    def as_(self, user: TokenUser | None) -> "Api":
        self.user = user
        return self

    async def req(self, method: str, path: str, **kw) -> httpx.Response:
        if not path.startswith("/api/"):
            path = "/api/v1" + path
        return await self.client.request(method, path, **kw)


@pytest.fixture(autouse=True)
def _no_real_storage_delete(monkeypatch):
    """버전·모델 삭제는 저장소 파일도 지우므로, 기본은 실제 S3 에 붙지 않게 한다(연결 재시도로 느려짐).
    저장소 호출을 확인하는 테스트는 ``fake_import`` 등이 다시 덮어쓴다."""
    async def noop(*_a, **_kw):
        return 0

    monkeypatch.setattr(model_store, "delete_version_files", noop)
    monkeypatch.setattr(model_store, "delete_prefix_files", noop)
    monkeypatch.setattr(model_store, "delete_model_files", noop)


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "model-artifacts").mkdir()
    return tmp_path


@pytest.fixture
async def api(data_dir):
    a = Api(await _make_factory())
    yield a
    await a.client.aclose()


async def _create(api: Api, name: str, user: TokenUser = ADMIN, **body) -> httpx.Response:
    return await api.as_(user).req("POST", "/models", json={"name": name, **body})


async def _new_version(api: Api, name: str) -> dict:
    r = await api.as_(ADMIN).req("POST", f"/models/{name}/versions", json={"model_name": name, "source": "it"})
    assert r.status_code == 200, r.text
    return r.json()


def _version_dir(data_dir: Path, name: str, version: int) -> Path:
    return data_dir / "model-artifacts" / name / "versions" / str(version)


# ---------------------------------------------------------------------------
# 등록 · 이름/경로 규칙 · 권한
# ---------------------------------------------------------------------------


async def test_create_sets_urn_storage_and_makes_directory(api, data_dir):
    r = await _create(api, "argus.it.iris", description="붓꽃")
    assert r.status_code == 200, r.text
    body = r.json()
    root = data_dir / "model-artifacts"
    assert body["urn"] == "argus.it.iris.PROD.model"
    assert body["storage_location"] == f"file://{root / 'argus.it.iris'}"
    assert (root / "argus.it.iris").is_dir()


async def test_create_rejects_duplicates_bad_names_paths_and_anonymous(api, data_dir):
    assert (await _create(api, "argus.it.iris")).status_code == 200
    assert (await _create(api, "argus.it.iris")).status_code == 409
    assert (await _create(api, "argus.it.anon", user=None)).status_code == 401
    for bad in ("../oci-artifacts", "a/b", "a..b"):
        assert (await _create(api, bad)).status_code == 422, bad
    root = data_dir / "model-artifacts"
    for loc in ("/etc/argus-x", f"file://{root}", f"file://{root}/../escape", "s3://bucket/x"):
        assert (await _create(api, "argus.it.bad", storage_location=loc)).status_code == 400, loc
    assert (await _create(api, "argus.it.ok", storage_location=f"file://{root}/custom/ok")).status_code == 200


async def test_create_conflicts_with_oci_model_name(api):
    async with api.factory() as db:
        db.add(om.OciModel(name="shared"))
        await db.commit()
    r = await _create(api, "shared")
    assert r.status_code == 409 and "OCI" in r.json()["detail"]


async def test_editor_guard_owner_creator_admin(api):
    await _create(api, "argus.it.user", user=ALICE)
    assert (await api.as_(BOB).req("PATCH", "/models/argus.it.user", json={"description": "x"})).status_code == 403
    assert (await api.as_(ALICE).req("PATCH", "/models/argus.it.user", json={"description": "x"})).status_code == 200
    r = await api.as_(ADMIN).req("PATCH", "/models/argus.it.user", json={"owner": "bob"})
    assert r.status_code == 200 and r.json()["owner"] == "bob"
    assert (await api.as_(BOB).req("PATCH", "/models/argus.it.user", json={"description": "y"})).status_code == 200
    assert (await api.as_(ADMIN).req("PATCH", "/models/argus.it.none", json={"description": "x"})).status_code == 404


async def test_rename_regenerates_urn_and_rejects_taken_name(api):
    await _create(api, "argus.it.a")
    await _create(api, "argus.it.b")
    assert (await api.req("PATCH", "/models/argus.it.a", json={"name": "argus.it.b"})).status_code == 409
    r = await api.req("PATCH", "/models/argus.it.a", json={"name": "argus.it.c"})
    assert r.status_code == 200 and r.json()["urn"] == "argus.it.c.PROD.model"
    assert (await api.req("GET", "/models/argus.it.a")).status_code == 404


# ---------------------------------------------------------------------------
# 목록 · 통계
# ---------------------------------------------------------------------------


async def test_list_search_paging_and_stats(api):
    for n in ("argus.it.a", "argus.it.b", "other.x.c"):
        await _create(api, n)
    await _new_version(api, "argus.it.a")

    page = (await api.as_(None).req("GET", "/models", params={"search": "argus.it", "page_size": 1})).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert (await api.req("GET", "/models", params={"page_size": 101})).status_code == 422

    stats = (await api.req("GET", "/models/stats")).json()
    assert stats["total_models"] == 3 and stats["total_versions"] == 1
    assert stats["pending_count"] == 1 and stats["production_models"] == 0


# ---------------------------------------------------------------------------
# 버전 라이프사이클 · finalize(로컬)
# ---------------------------------------------------------------------------


async def test_version_create_pending_and_permissions(api, data_dir):
    await _create(api, "argus.it.iris")
    ver = await _new_version(api, "argus.it.iris")
    assert ver["version"] == 1 and ver["status"] == "PENDING_REGISTRATION"
    assert ver["storage_location"] == f"file://{_version_dir(data_dir, 'argus.it.iris', 1)}"
    assert _version_dir(data_dir, "argus.it.iris", 1).is_dir()
    assert (await _new_version(api, "argus.it.iris"))["version"] == 2

    r = await api.as_(ALICE).req("POST", "/models/argus.it.iris/versions", json={"model_name": "x"})
    assert r.status_code == 403
    r = await api.as_(ADMIN).req("POST", "/models/argus.it.none/versions", json={"model_name": "x"})
    assert r.status_code == 404
    assert (await api.as_(None).req("POST", "/models/argus.it.iris/versions",
                                    json={"model_name": "x"})).status_code == 401


async def test_version_writes_allowed_for_model_owner(api, data_dir):
    """버전 생성·수정·확정·삭제는 UC 호환 API 와 같은 기준 — 관리자·모델 등록자·소유자."""
    await _create(api, "argus.it.user", user=ALICE)                     # 등록자 alice
    await _create(api, "argus.it.owned", owner="bob")                   # admin 등록, 소유자 bob
    base = "/models/argus.it.user/versions"

    r = await api.as_(ALICE).req("POST", base, json={"model_name": "x", "source": "it"})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["model_name"] == "argus.it.user"
    assert (await api.req("PATCH", f"{base}/1", json={"description": "등록자 수정"})).status_code == 200
    assert (await api.req("PATCH", f"{base}/1/finalize", json={"status": "READY"})).status_code == 200
    assert (await api.req("DELETE", f"{base}/1")).status_code == 200

    # 남의 모델(등록자·소유자 아님)은 403
    await api.as_(ALICE).req("POST", base, json={"model_name": "x"})    # v2
    api.as_(BOB)
    assert (await api.req("POST", base, json={"model_name": "x"})).status_code == 403
    assert (await api.req("PATCH", f"{base}/2", json={"description": "x"})).status_code == 403
    assert (await api.req("PATCH", f"{base}/2/finalize", json={"status": "READY"})).status_code == 403
    assert (await api.req("DELETE", f"{base}/2")).status_code == 403

    # 소유자(owner)로 지정된 사용자도 허용
    r = await api.as_(BOB).req("POST", "/models/argus.it.owned/versions", json={"model_name": "x"})
    assert r.status_code == 200, r.text
    assert (await api.as_(ALICE).req("POST", "/models/argus.it.owned/versions",
                                     json={"model_name": "x"})).status_code == 403


async def test_finalize_ready_counts_files_and_parses_mlmodel(api, data_dir):
    await _create(api, "argus.it.iris")
    await _new_version(api, "argus.it.iris")
    vdir = _version_dir(data_dir, "argus.it.iris", 1)
    (vdir / "MLmodel").write_text(MLMODEL)
    (vdir / "requirements.txt").write_text("scikit-learn==1.5.2\n")
    (vdir / "model.pkl").write_bytes(b"x" * 1024)

    r = await api.as_(ADMIN).req("PATCH", "/models/argus.it.iris/versions/1/finalize", json={"status": "READY"})
    assert r.status_code == 200, r.text
    ver = r.json()
    assert ver["status"] == "READY" and ver["artifact_count"] == 3 and ver["finished_at"]
    assert ver["files_present"] is True and ver["files_path"] == "/argus.it.iris/versions/1"

    catalog = (await api.as_(None).req("GET", "/models/argus.it.iris/detail")).json()["catalog"]
    assert catalog["sklearn_version"] == "1.5.2" and catalog["predict_fn"] == "predict"
    assert "scikit-learn" in catalog["requirements"]

    # 재확정은 409 (PENDING_REGISTRATION 아님)
    r = await api.as_(ADMIN).req("PATCH", "/models/argus.it.iris/versions/1/finalize", json={"status": "READY"})
    assert r.status_code == 409


async def test_finalize_failed_and_invalid_target_status(api):
    await _create(api, "argus.it.iris")
    await _new_version(api, "argus.it.iris")
    path = "/models/argus.it.iris/versions/1/finalize"
    r = await api.as_(ADMIN).req("PATCH", path, json={"status": "FAILED_REGISTRATION", "status_message": "upload error"})
    assert r.status_code == 200 and r.json()["status_message"] == "upload error"
    assert (await api.req("PATCH", path, json={"status": "READY"})).status_code == 409

    await _new_version(api, "argus.it.iris")
    path2 = "/models/argus.it.iris/versions/2/finalize"
    assert (await api.req("PATCH", path2, json={"status": "PENDING_REGISTRATION"})).status_code == 409
    assert (await api.req("PATCH", path2, json={"status": "BOGUS"})).status_code == 422
    assert (await api.as_(ALICE).req("PATCH", path2, json={"status": "READY"})).status_code == 403


async def test_version_update_delete_and_listing(api):
    await _create(api, "argus.it.iris")
    for _ in range(3):
        await _new_version(api, "argus.it.iris")
    r = await api.as_(ADMIN).req("PATCH", "/models/argus.it.iris/versions/2", json={"description": "실패 케이스"})
    assert r.status_code == 200 and r.json()["description"] == "실패 케이스"
    assert (await api.req("DELETE", "/models/argus.it.iris/versions/2")).status_code == 200
    assert (await api.as_(None).req("GET", "/models/argus.it.iris/versions/2")).status_code == 404
    listing = (await api.req("GET", "/models/argus.it.iris/versions")).json()
    assert [v["version"] for v in listing["items"]] == [3, 1] and listing["total"] == 2
    assert (await api.req("GET", "/models/argus.it.none/versions")).status_code == 404
    assert (await api.as_(ADMIN).req("DELETE", "/models/argus.it.iris/versions/99")).status_code == 404


# ---------------------------------------------------------------------------
# 스테이지
# ---------------------------------------------------------------------------


async def test_stage_change_normalizes_records_user_and_checks_permission(api):
    await _create(api, "argus.it.iris", user=ALICE)
    await _new_version(api, "argus.it.iris")
    path = "/models/argus.it.iris/versions/1/stage"

    r = await api.as_(ALICE).req("PUT", path, json={"stage": "staging"})
    assert r.status_code == 200
    assert r.json()["stage"] == "STAGING" and r.json()["stage_changed_by"] == "alice"
    assert (await api.req("PUT", path, json={"stage": "LIVE"})).status_code == 400
    assert (await api.as_(BOB).req("PUT", path, json={"stage": "ARCHIVED"})).status_code == 403
    assert (await api.as_(ADMIN).req("PUT", path, json={"stage": "PRODUCTION", "stage_changed_by": "spoof"})).status_code == 200
    assert (await api.req("PUT", "/models/argus.it.iris/versions/9/stage", json={"stage": "NONE"})).status_code == 404

    ver = (await api.as_(None).req("GET", "/models/argus.it.iris/versions/1")).json()
    assert ver["stage"] == "PRODUCTION" and ver["stage_changed_by"] == "admin" and ver["stage_changed_at"]
    assert (await api.req("GET", "/models/stats")).json()["production_models"] == 1


# ---------------------------------------------------------------------------
# 메트릭 · 모델 카드 · 데이터셋 리니지 · 다운로드 통계
# ---------------------------------------------------------------------------


async def test_metrics_validation_upsert_and_permission(api):
    await _create(api, "argus.it.iris")
    await _new_version(api, "argus.it.iris")
    path = "/models/argus.it.iris/versions/1/metrics"
    assert (await api.as_(ADMIN).req("POST", path, json={"metrics": {"accuracy": 0.96, "f1": 0.95}})).status_code == 200
    assert (await api.req("POST", path, json={"metrics": {"accuracy": 0.97}})).status_code == 200  # 같은 키는 갱신
    for bad in ({}, {"metrics": {}}, {"metrics": {"acc": "high"}}, {"metrics": {"acc": True}}):
        assert (await api.req("POST", path, json=bad)).status_code == 400, bad
    assert (await api.as_(BOB).req("POST", path, json={"metrics": {"accuracy": 1}})).status_code == 403
    assert (await api.as_(None).req("GET", "/models/argus.it.iris/metrics")).json() == [
        {"version": 1, "metrics": {"accuracy": 0.97, "f1": 0.95}},
    ]


async def test_model_card_create_partial_update_and_permission(api):
    await _create(api, "argus.it.iris")
    empty = (await api.as_(None).req("GET", "/models/argus.it.iris/card")).json()
    assert empty["purpose"] is None
    body = {"purpose": "붓꽃 품종 분류", "limitations": "샘플 150건", "license": "Apache-2.0"}
    assert (await api.as_(ADMIN).req("PUT", "/models/argus.it.iris/card", json=body)).status_code == 200
    assert (await api.req("PUT", "/models/argus.it.iris/card", json={"limitations": "보완"})).status_code == 200
    card = (await api.req("GET", "/models/argus.it.iris/card")).json()
    assert card["purpose"] == "붓꽃 품종 분류" and card["limitations"] == "보완" and card["license"] == "Apache-2.0"
    assert (await api.as_(BOB).req("PUT", "/models/argus.it.iris/card", json={"purpose": "x"})).status_code == 403


async def test_dataset_lineage_validation_duplicate_and_scoped_delete(api):
    await _create(api, "argus.it.iris")
    await _create(api, "argus.it.other")
    await _new_version(api, "argus.it.iris")
    async with api.factory() as db:
        src = cm.Datasource(datasource_id="d1", name="sakila", type="mysql", origin="PROD")
        db.add(src)
        await db.flush()
        ds = cm.Dataset(urn="x.film.dataset", name="film", datasource_id=src.id, origin="PROD", status="active")
        db.add(ds)
        await db.commit()
        dsid = ds.id

    path = "/models/argus.it.iris/lineage"
    r = await api.as_(ADMIN).req("POST", path, json={"dataset_id": dsid})
    assert r.status_code == 200 and r.json()["relation_type"] == "TRAINING_DATA"
    lid = r.json()["id"]
    assert (await api.req("POST", path, json={"dataset_id": dsid})).status_code == 409
    assert (await api.req("POST", path, json={"dataset_id": dsid, "relation_type": "evaluation_data"})).status_code == 200
    assert (await api.req("POST", path, json={})).status_code == 400
    assert (await api.req("POST", path, json={"dataset_id": "1"})).status_code == 400
    assert (await api.req("POST", path, json={"dataset_id": dsid, "relation_type": "bogus"})).status_code == 400
    assert (await api.req("POST", path, json={"dataset_id": 999999})).status_code == 404
    assert (await api.req("POST", path, json={"dataset_id": dsid, "model_version": 9})).status_code == 404
    assert (await api.as_(BOB).req("POST", path, json={"dataset_id": dsid, "model_version": 1})).status_code == 403

    rows = (await api.as_(None).req("GET", path)).json()
    assert [(x["dataset_name"], x["datasource_type"]) for x in rows] == [("film", "mysql"), ("film", "mysql")]
    assert (await api.as_(ADMIN).req("DELETE", f"/models/argus.it.other/lineage/{lid}")).status_code == 404
    assert (await api.req("DELETE", f"{path}/{lid}")).status_code == 200


# ---------------------------------------------------------------------------
# 소프트 삭제 · 복원 · 영구 삭제
# ---------------------------------------------------------------------------


async def test_soft_delete_hides_model_and_closes_pending_versions(api):
    await _create(api, "argus.it.user", user=ALICE)
    await _create(api, "argus.it.keep")
    await _new_version(api, "argus.it.user")
    assert (await api.as_(BOB).req("DELETE", "/models/argus.it.user")).status_code == 403
    assert (await api.as_(ALICE).req("DELETE", "/models/argus.it.user")).status_code == 200

    async with api.factory() as db:
        model = (await db.execute(select(mm.RegisteredModel).where(mm.RegisteredModel.name == "argus.it.user"))).scalar_one()
        ver = (await db.execute(select(mm.ModelVersion).where(mm.ModelVersion.model_id == model.id))).scalar_one()
    assert model.status == "deleted"
    assert ver.status == "FAILED_REGISTRATION" and ver.status_message == "Model deleted during registration"

    # 조회·상세·버전·쓰기 모두 404, 목록·통계에서 제외
    api.as_(ALICE)
    for method, path in (("GET", "/models/argus.it.user"), ("GET", "/models/argus.it.user/detail"),
                         ("GET", "/models/argus.it.user/versions"), ("PATCH", "/models/argus.it.user")):
        kw = {"json": {"description": "x"}} if method == "PATCH" else {}
        assert (await api.req(method, path, **kw)).status_code == 404, path
    assert [m["name"] for m in (await api.req("GET", "/models")).json()["items"]] == ["argus.it.keep"]
    assert (await api.req("GET", "/models/stats")).json()["total_models"] == 1
    # 같은 이름 재등록은 409(복원·영구 삭제 안내)
    r = await _create(api, "argus.it.user", user=ADMIN)
    assert r.status_code == 409 and "삭제된 모델" in r.json()["detail"]


async def test_deleted_list_and_restore_are_admin_only(api):
    await _create(api, "argus.it.user", user=ALICE)
    await api.as_(ALICE).req("DELETE", "/models/argus.it.user")
    assert (await api.as_(None).req("GET", "/models", params={"deleted": "true"})).status_code == 401
    assert (await api.as_(ALICE).req("GET", "/models", params={"deleted": "true"})).status_code == 403
    deleted = (await api.as_(ADMIN).req("GET", "/models", params={"deleted": "true"})).json()
    assert [m["name"] for m in deleted["items"]] == ["argus.it.user"]

    assert (await api.as_(ALICE).req("POST", "/models/argus.it.user/restore")).status_code == 403
    assert (await api.as_(ADMIN).req("POST", "/models/argus.it.user/restore")).status_code == 200
    assert (await api.req("POST", "/models/argus.it.user/restore")).status_code == 404  # 이미 복원됨
    assert (await api.as_(None).req("GET", "/models/argus.it.user")).status_code == 200


async def test_hard_delete_removes_rows_and_directory(api, data_dir, monkeypatch):
    deleted_s3: list[tuple[str, int]] = []

    async def delete_version_files(name, version, bucket=None):
        deleted_s3.append((name, version))
        return 0

    monkeypatch.setattr(model_store, "delete_version_files", delete_version_files)
    await _create(api, "argus.it.user")
    await _new_version(api, "argus.it.user")
    model_dir = data_dir / "model-artifacts" / "argus.it.user"
    assert model_dir.is_dir()

    assert (await api.as_(ALICE).req("POST", "/models/hard-delete", json={"names": ["argus.it.user"]})).status_code == 403
    assert (await api.as_(ADMIN).req("POST", "/models/hard-delete", json={"names": []})).status_code == 400
    r = await api.req("POST", "/models/hard-delete", json={"names": ["argus.it.user", "argus.it.none"]})
    assert r.json() == {"deleted": ["argus.it.user"], "not_found": ["argus.it.none"]}
    # S3 는 이름 프리픽스 통째가 아니라 버전 프리픽스 단위로 지운다
    assert not model_dir.exists() and deleted_s3 == [("argus.it.user", 1)]
    async with api.factory() as db:
        assert (await db.execute(select(mm.ModelVersion))).scalars().all() == []
    assert (await _create(api, "argus.it.user")).status_code == 200  # 이름 재사용 가능


# ---------------------------------------------------------------------------
# Unity Catalog 호환 REST
# ---------------------------------------------------------------------------

UC = "/api/2.1/unity-catalog"


async def test_uc_requires_login_and_uses_uc_error_format(api):
    r = await api.as_(None).req("GET", f"{UC}/models", params={"catalog_name": "argus", "schema_name": "it"})
    assert r.status_code == 401 and r.json()["error_code"] == "UNAUTHENTICATED"
    r = await api.as_(None).req("GET", f"{UC}/models/argus.it.uc/versions/1")
    assert r.status_code == 401


async def test_uc_model_lifecycle_and_permissions(api, data_dir):
    body = {"catalog_name": "argus", "schema_name": "it", "name": "uc", "comment": "UC 경로 등록"}
    r = await api.as_(ALICE).req("POST", f"{UC}/models", json=body)
    assert r.status_code == 200 and r.json()["full_name"] == "argus.it.uc"
    r = await api.req("POST", f"{UC}/models", json=body)
    assert r.status_code == 409 and r.json()["error_code"] == "ALREADY_EXISTS"
    r = await api.req("POST", f"{UC}/models", json={"catalog_name": "..", "schema_name": "x", "name": "y"})
    assert r.status_code == 400 and r.json()["error_code"] == "INVALID_PARAMETER_VALUE"

    listed = (await api.req("GET", f"{UC}/models", params={"catalog_name": "argus", "schema_name": "it"})).json()
    assert [m["full_name"] for m in listed["registered_models"]] == ["argus.it.uc"]
    assert (await api.req("GET", f"{UC}/models/argus.it.uc")).json()["comment"] == "UC 경로 등록"

    # 남의 모델 수정·삭제는 PERMISSION_DENIED
    r = await api.as_(BOB).req("PATCH", f"{UC}/models/argus.it.uc", json={"comment": "x"})
    assert r.status_code == 403 and r.json()["error_code"] == "PERMISSION_DENIED"
    assert (await api.as_(BOB).req("DELETE", f"{UC}/models/argus.it.uc")).status_code == 403
    r = await api.as_(ALICE).req("PATCH", f"{UC}/models/argus.it.uc", json={"comment": "수정"})
    assert r.status_code == 200 and r.json()["comment"] == "수정"

    assert (await api.as_(ALICE).req("DELETE", f"{UC}/models/argus.it.uc")).status_code == 200
    r = await api.req("GET", f"{UC}/models/argus.it.uc")
    assert r.status_code == 404 and r.json()["error_code"] == "NOT_FOUND"


async def test_uc_version_lifecycle_and_load_logging(api, data_dir):
    await api.as_(ALICE).req("POST", f"{UC}/models", json={"catalog_name": "argus", "schema_name": "it", "name": "uc"})
    ver_body = {"catalog_name": "argus", "schema_name": "it", "model_name": "uc", "source": "file:///tmp/x"}
    r = await api.as_(BOB).req("POST", f"{UC}/models/versions", json=ver_body)
    assert r.status_code == 403 and r.json()["error_code"] == "PERMISSION_DENIED"
    r = await api.as_(ALICE).req("POST", f"{UC}/models/versions", json=ver_body)
    assert r.status_code == 200 and r.json()["status"] == "PENDING_REGISTRATION"
    assert r.json()["storage_location"] == f"file://{_version_dir(data_dir, 'argus.it.uc', 1)}"
    assert (await api.req("POST", f"{UC}/temporary-model-version-credentials", json={})).json() == {}

    vpath = f"{UC}/models/argus.it.uc/versions/1"
    assert (await api.as_(BOB).req("PATCH", f"{vpath}/finalize", json={})).status_code == 403
    r = await api.as_(ALICE).req("PATCH", f"{vpath}/finalize", json={})
    assert r.status_code == 200 and r.json()["status"] == "READY"
    r = await api.req("PATCH", f"{vpath}/finalize", json={})
    assert r.status_code == 409 and r.json()["error_code"] == "INVALID_STATE"

    assert (await api.as_(BOB).req("PATCH", vpath, json={"comment": "x"})).status_code == 403
    assert (await api.as_(ALICE).req("PATCH", vpath, json={"comment": "v1 설명"})).json()["comment"] == "v1 설명"
    versions = (await api.req("GET", f"{UC}/models/argus.it.uc/versions")).json()["model_versions"]
    assert [v["version"] for v in versions] == [1]

    # 버전 조회는 다운로드 로그(load)를 남긴다
    assert (await api.req("GET", vpath)).status_code == 200
    stats = (await api.req("GET", "/models/argus.it.uc/download")).json()
    assert stats["total_download"] == 1 and stats["recent_logs"][0]["download_type"] == "load"

    assert (await api.as_(BOB).req("DELETE", vpath)).status_code == 403
    assert (await api.as_(ALICE).req("DELETE", vpath)).status_code == 200
    assert (await api.req("GET", vpath)).status_code == 404


# ---------------------------------------------------------------------------
# model-store finalize (S3) — MLflow 등록 모델 버전 확정
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_s3(monkeypatch):
    files: dict[tuple[str, int], dict[str, bytes]] = {}

    async def list_files(name, version):
        return [{"filename": f, "size": len(b)} for f, b in files.get((name, version), {}).items()]

    async def generate_manifest(name, version, annotations=None):
        return {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                "layers": [{"title": f} for f in files.get((name, version), {})]}

    async def download_file(name, version, filename):
        return files[(name, version)][filename]

    monkeypatch.setattr(model_store, "list_files", list_files)
    monkeypatch.setattr(model_store, "generate_manifest", generate_manifest)
    monkeypatch.setattr(model_store, "download_file", download_file)
    return files


async def test_store_finalize_updates_db_and_parses_meta(api, fake_s3):
    await _create(api, "argus.it.iris", user=ALICE)
    await _new_version(api, "argus.it.iris")
    path = "/model-store/argus.it.iris/versions/1/finalize"

    assert (await api.as_(ALICE).req("POST", path, json={})).status_code == 400  # 파일 없음
    fake_s3[("argus.it.iris", 1)] = {"MLmodel": MLMODEL.encode(), "model.pkl": b"x" * 100, "manifest.json": b"{}"}
    assert (await api.as_(BOB).req("POST", path, json={})).status_code == 403
    r = await api.as_(ALICE).req("POST", path, json={})
    assert r.status_code == 200, r.text
    assert r.json()["file_count"] == 2  # manifest.json 제외

    ver = (await api.req("GET", "/models/argus.it.iris/versions/1")).json()
    assert ver["status"] == "READY" and ver["artifact_count"] == 2
    assert ver["storage_location"] == f"s3://{settings.os_bucket}/argus.it.iris/v1/"
    catalog = (await api.req("GET", "/models/argus.it.iris/detail")).json()["catalog"]
    assert catalog["sklearn_version"] == "1.5.2"
    async with api.factory() as db:
        digest = (await db.execute(select(mm.CatalogModel.content_digest))).scalar_one()
    assert digest.startswith("sha256:")
    assert (await api.req("POST", path, json={})).status_code == 200  # 재확정은 값만 갱신


async def test_store_finalize_rejects_unknown_targets_and_failed_versions(api, fake_s3, monkeypatch):
    await _create(api, "argus.it.iris")
    await _new_version(api, "argus.it.iris")
    await api.as_(ADMIN).req("PATCH", "/models/argus.it.iris/versions/1/finalize",
                             json={"status": "FAILED_REGISTRATION"})
    fake_s3[("argus.it.iris", 1)] = {"model.pkl": b"x"}
    r = await api.req("POST", "/model-store/argus.it.none/versions/1/finalize", json={})
    assert r.status_code == 404 and "oci-models" in r.json()["detail"]
    assert (await api.req("POST", "/model-store/argus.it.iris/versions/9/finalize", json={})).status_code == 404

    # FAILED 버전은 manifest 를 만들기(저장소에 manifest.json 을 쓰기) 전에 409
    manifest_calls: list[tuple] = []

    async def generate_manifest(*a, **_kw):
        manifest_calls.append(a)
        return {"layers": []}

    monkeypatch.setattr(model_store, "generate_manifest", generate_manifest)
    r = await api.req("POST", "/model-store/argus.it.iris/versions/1/finalize", json={})
    assert r.status_code == 409 and "FAILED_REGISTRATION" in r.json()["detail"]
    assert manifest_calls == []

    # 소프트 삭제된 모델에는 업로드할 수 없다(등록되지 않은 모델과 같은 404)
    await api.req("DELETE", "/models/argus.it.iris")
    r = await api.req("POST", "/model-store/argus.it.iris/versions/1/upload", files={"file": ("a.bin", b"x")})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 파일 브라우저 — 등록 모델 파일 보호 판단
# ---------------------------------------------------------------------------


async def test_file_browser_protection_covers_model_dir_children_and_parents(api, data_dir):
    await _create(api, "argus.it.iris")
    root = data_dir / "model-artifacts"
    async with api.factory() as db:
        assert await service.find_registered_model_for_path(db, root / "argus.it.iris") == ("argus.it.iris", False)
        assert await service.find_registered_model_for_path(db, root / "argus.it.iris" / "versions" / "1") == (
            "argus.it.iris", False)
        assert await service.find_registered_model_for_path(db, root) == ("argus.it.iris", True)
        assert await service.find_registered_model_for_path(db, root / "it-folder") is None


async def test_file_browser_protection_keeps_soft_deleted_model_files(api, data_dir, monkeypatch):
    """소프트 삭제된 모델의 파일도 보호 — 복원할 수 있어야 하므로 영구 삭제로만 지운다."""

    async def delete_model_files(_name):
        return 0

    monkeypatch.setattr(model_store, "delete_model_files", delete_model_files)
    await _create(api, "argus.it.user")
    await api.as_(ADMIN).req("DELETE", "/models/argus.it.user")
    root = data_dir / "model-artifacts"
    async with api.factory() as db:
        assert await service.find_registered_model_for_path(db, root / "argus.it.user") == ("argus.it.user", False)
    await api.req("POST", "/models/hard-delete", json={"names": ["argus.it.user"]})
    async with api.factory() as db:
        assert await service.find_registered_model_for_path(db, root / "argus.it.user") is None


async def test_download_stats_unknown_or_deleted_model_is_404(api):
    assert (await api.as_(None).req("GET", "/models/argus.it.none/download")).status_code == 404
    await _create(api, "argus.it.user")
    assert (await api.as_(None).req("GET", "/models/argus.it.user/download")).status_code == 200
    await api.as_(ADMIN).req("DELETE", "/models/argus.it.user")
    assert (await api.as_(None).req("GET", "/models/argus.it.user/download")).status_code == 404


async def test_finalize_unknown_model_or_version_is_404(api):
    body = {"status": "READY"}
    assert (await api.as_(ADMIN).req("PATCH", "/models/argus.it.none/versions/1/finalize", json=body)).status_code == 404
    await _create(api, "argus.it.iris")
    assert (await api.req("PATCH", "/models/argus.it.iris/versions/9/finalize", json=body)).status_code == 404
    r = await api.req("PATCH", f"{UC}/models/argus.it.iris/versions/9/finalize", json={})
    assert r.status_code == 404 and r.json()["error_code"] == "NOT_FOUND"


async def test_metrics_for_unknown_version_is_404(api):
    await _create(api, "argus.it.iris")
    r = await api.as_(ADMIN).req("POST", "/models/argus.it.iris/versions/9/metrics", json={"metrics": {"acc": 1}})
    assert r.status_code == 404
    assert (await api.req("GET", "/models/argus.it.iris/metrics")).json() == []


async def test_deleted_model_lineage_metrics_card_reads_are_404(api):
    await _create(api, "argus.it.user")
    await api.as_(ADMIN).req("DELETE", "/models/argus.it.user")
    for sub in ("lineage", "metrics", "card"):
        assert (await api.as_(None).req("GET", f"/models/argus.it.user/{sub}")).status_code == 404, sub
    await api.as_(ADMIN).req("POST", "/models/argus.it.user/restore")
    for sub in ("lineage", "metrics", "card"):
        assert (await api.as_(None).req("GET", f"/models/argus.it.user/{sub}")).status_code == 200, sub


# ---------------------------------------------------------------------------
# model-store 반입(import) — 사전 확인 · 실패 시 되돌리기
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_import(monkeypatch):
    """반입 중 저장소 호출을 가짜로 — ``fail`` 을 True 로 두면 파일 적재 단계에서 실패한다."""
    state = {"fail": False, "deleted_versions": [], "deleted_models": []}

    async def import_from_local_directory(local_dir, model_name, version, source="local", bucket=None):
        if state["fail"]:
            raise RuntimeError("S3 업로드 실패(가짜)")
        return {"source": source, "file_count": 1, "total_size": 3, "manifest": {"layers": []}}

    async def import_from_huggingface(hf_model_id, model_name, version, revision="main", bucket=None):
        if state["fail"]:
            raise RuntimeError("HF 다운로드 실패(가짜)")
        return {"file_count": 2, "total_size": 10, "manifest": {"layers": []}}

    async def delete_version_files(name, version, bucket=None):
        state["deleted_versions"].append((name, version))
        return 0

    async def delete_model_files(name, bucket=None):
        state["deleted_models"].append(name)
        return 0

    async def check_hf_repo(hf_model_id, revision):
        from app.oci_hub.service import OciUpstreamError
        if hf_model_id.startswith("no-such"):
            raise OciUpstreamError("HuggingFace 에서 모델을 찾을 수 없습니다.", 404)
        if revision == "no-such-rev":
            raise OciUpstreamError("revision 'no-such-rev' 을(를) 찾을 수 없습니다.", 400)

    monkeypatch.setattr(model_store, "import_from_local_directory", import_from_local_directory)
    monkeypatch.setattr(model_store, "import_from_huggingface", import_from_huggingface)
    monkeypatch.setattr(model_store, "delete_version_files", delete_version_files)
    monkeypatch.setattr(model_store, "delete_model_files", delete_model_files)
    monkeypatch.setattr("app.oci_hub.service._check_hf_repo", check_hf_repo)
    return state


async def _model_names(api: Api) -> list[str]:
    async with api.factory() as db:
        return list((await db.execute(select(mm.RegisteredModel.name))).scalars())


async def _version_statuses(api: Api, name: str) -> list[tuple[int, str]]:
    vers = (await api.req("GET", f"/models/{name}/versions")).json()["items"]
    return sorted((v["version"], v["status"]) for v in vers)


async def test_import_local_rejects_missing_or_empty_dir_without_creating_model(api, fake_import, tmp_path):
    r = await api.req("POST", "/model-store/import/local",
                      json={"local_dir": str(tmp_path / "no-such"), "model_name": "argus.it.bad"})
    assert r.status_code == 400 and "디렉터리가 없습니다" in r.json()["detail"]
    empty = tmp_path / "empty"
    (empty / ".git").mkdir(parents=True)
    (empty / ".git" / "HEAD").write_text("x")
    r = await api.req("POST", "/model-store/import/local", json={"local_dir": str(empty), "model_name": "argus.it.bad"})
    assert r.status_code == 400 and "파일이 없습니다" in r.json()["detail"]
    assert await _model_names(api) == []


async def test_import_local_success_and_failure_marks_failed(api, fake_import, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "model.pkl").write_bytes(b"abc")
    body = {"local_dir": str(src), "model_name": "argus.it.local"}

    r = await api.req("POST", "/model-store/import/local", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["storage_location"].endswith("/argus.it.local/v1/")

    # 반입 도중 실패 → 그 버전은 '진행 중'(PENDING)이 아니라 FAILED_REGISTRATION 으로 남는다
    fake_import["fail"] = True
    r = await api.req("POST", "/model-store/import/local", json=body)
    assert r.status_code == 500 and "로컬 반입 실패" in r.json()["detail"]
    assert await _version_statuses(api, "argus.it.local") == [(1, "READY"), (2, "FAILED_REGISTRATION")]


async def test_import_hf_checks_repo_first(api, fake_import):
    r = await api.req("POST", "/model-store/import/huggingface",
                      json={"hf_model_id": "no-such/model", "model_name": "argus.it.hf"})
    assert r.status_code == 404 and "찾을 수 없습니다" in r.json()["detail"]
    r = await api.req("POST", "/model-store/import/huggingface",
                      json={"hf_model_id": "org/model", "revision": "no-such-rev", "model_name": "argus.it.hf"})
    assert r.status_code == 400 and "revision" in r.json()["detail"]
    assert await _model_names(api) == []

    fake_import["fail"] = True
    r = await api.req("POST", "/model-store/import/huggingface",
                      json={"hf_model_id": "org/model", "model_name": "argus.it.hf"})
    assert r.status_code == 500
    assert await _version_statuses(api, "argus.it.hf") == [(1, "FAILED_REGISTRATION")]

    fake_import["fail"] = False
    r = await api.req("POST", "/model-store/import/huggingface",
                      json={"hf_model_id": "org/model", "model_name": "argus.it.hf"})
    assert r.status_code == 200 and r.json()["file_count"] == 2


# ---------------------------------------------------------------------------
# 확정 안 된 버전의 저장소 파일 삭제 (끊긴 push 정리)
# ---------------------------------------------------------------------------


async def test_delete_unfinalized_files_guards(api, fake_import):
    await _create(api, "argus.it.iris", user=ALICE)
    await _new_version(api, "argus.it.iris")  # v1 = MLflow 버전 기록(PENDING)
    path = "/model-store/argus.it.iris/versions/{}/files"

    assert (await api.as_(None).req("DELETE", path.format(2))).status_code == 401
    assert (await api.as_(BOB).req("DELETE", path.format(2))).status_code == 403
    assert (await api.as_(ALICE).req("DELETE", "/model-store/argus.it.none/versions/2/files")).status_code == 404
    r = await api.as_(ALICE).req("DELETE", path.format(1))
    assert r.status_code == 409 and "버전을 삭제하면 파일도 함께" in r.json()["detail"]
    r = await api.as_(ALICE).req("DELETE", path.format(2))
    assert r.status_code == 200 and r.json() == {"model_name": "argus.it.iris", "version": 2, "deleted": 0}
    assert fake_import["deleted_versions"] == [("argus.it.iris", 2)]


async def test_delete_unfinalized_files_refuses_oci_versions(api, fake_import):
    async with api.factory() as db:
        m = om.OciModel(name="it-oci", created_by="alice", owner="alice")
        db.add(m)
        await db.flush()
        db.add(om.OciModelVersion(model_id=m.id, version=1, status="ready"))
        await db.commit()
    assert (await api.as_(ALICE).req("DELETE", "/model-store/it-oci/versions/1/files")).status_code == 409
    assert (await api.as_(ALICE).req("DELETE", "/model-store/it-oci/versions/2/files")).status_code == 200


# ---------------------------------------------------------------------------
# 버전 삭제 시 파일 정리 · 영구 삭제 시 다운로드 기록 정리
# ---------------------------------------------------------------------------


async def test_version_delete_removes_only_that_version_dir(api, data_dir):
    await _create(api, "argus.it.iris")
    await _new_version(api, "argus.it.iris")
    await _new_version(api, "argus.it.iris")
    v1, v2 = _version_dir(data_dir, "argus.it.iris", 1), _version_dir(data_dir, "argus.it.iris", 2)
    (v2 / "MLmodel").write_text("x")
    assert v1.is_dir() and v2.is_dir()
    assert (await api.as_(ADMIN).req("DELETE", "/models/argus.it.iris/versions/2")).status_code == 200
    assert not v2.exists() and v1.is_dir() and v1.parent.parent.is_dir()


async def test_version_delete_skips_dir_outside_artifacts_root(api, data_dir, tmp_path_factory):
    """오염된 저장 위치(루트 밖)는 지우지 않는다 — DB 행만 삭제."""
    await _create(api, "argus.it.iris")
    await _new_version(api, "argus.it.iris")
    outside = tmp_path_factory.mktemp("outside")
    (outside / "keep.txt").write_text("x")
    async with api.factory() as db:
        ver = (await db.execute(select(mm.ModelVersion))).scalars().one()
        ver.storage_location = f"file://{outside}"
        await db.commit()
    assert (await api.as_(ADMIN).req("DELETE", "/models/argus.it.iris/versions/1")).status_code == 200
    assert (outside / "keep.txt").exists()


async def test_version_delete_removes_storage_files_and_survives_outage(api, fake_import, tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "model.pkl").write_bytes(b"abc")
    body = {"local_dir": str(src), "model_name": "argus.it.local"}
    for _ in range(2):
        assert (await api.req("POST", "/model-store/import/local", json=body)).status_code == 200
    assert (await api.as_(ADMIN).req("DELETE", "/models/argus.it.local/versions/1")).status_code == 200
    assert fake_import["deleted_versions"] == [("argus.it.local", 1)]

    async def down(*_a, **_kw):
        raise ConnectionError("Could not connect to the endpoint URL")

    monkeypatch.setattr(model_store, "delete_version_files", down)
    assert (await api.req("DELETE", "/models/argus.it.local/versions/2")).status_code == 200  # DB 삭제는 유지
    assert await _version_statuses(api, "argus.it.local") == []


async def test_hard_delete_clears_download_logs(api, fake_import):
    await _create(api, "argus.it.dl")
    await _new_version(api, "argus.it.dl")
    async with api.factory() as db:
        db.add_all([mm.ModelDownloadLog(model_name="argus.it.dl", version=1, download_type="pull"),
                    mm.ModelDownloadLog(model_name="argus.it.other", version=1, download_type="pull")])
        await db.commit()
    r = await api.as_(ADMIN).req("POST", "/models/hard-delete", json={"names": ["argus.it.dl"]})
    assert r.json()["deleted"] == ["argus.it.dl"]
    async with api.factory() as db:
        names = (await db.execute(select(mm.ModelDownloadLog.model_name))).scalars().all()
    assert names == ["argus.it.other"]  # 다른 모델 기록은 그대로


async def test_version_delete_removes_store_uploads_of_unfinalized_local_version(api, fake_import):
    """확정 전(file://) 버전에 model-store 로 올린 파일도 버전 삭제로 지워진다 — 정리 API 409 안내와 일치."""
    await _create(api, "argus.it.clean")
    await _new_version(api, "argus.it.clean")  # v1 PENDING, storage file://…/versions/1
    r = await api.as_(ADMIN).req("DELETE", "/model-store/argus.it.clean/versions/1/files")
    assert r.status_code == 409
    assert (await api.req("DELETE", "/models/argus.it.clean/versions/1")).status_code == 200
    assert fake_import["deleted_versions"] == [("argus.it.clean", 1)]



# ---------------------------------------------------------------------------
# 버전 번호 원자적 증가 · 이름 변경 OCI 충돌 · 영구 삭제의 버전 프리픽스 정리
# ---------------------------------------------------------------------------


async def test_version_number_increment_is_atomic_with_stale_model_row(api):
    """다른 세션이 먼저 버전을 만들어 메모리의 모델 행(max_version_number)이 낡아도 번호가 겹치지 않는다."""
    from app.models.schemas import ModelVersionCreate

    await _create(api, "argus.it.race")
    async with api.factory() as stale:
        # stale 세션이 모델을 먼저 읽어 identity map 에 max_version_number=0 을 들고 있게 한다
        assert (await service._resolve_model(stale, "argus.it.race")).max_version_number == 0
        await _new_version(api, "argus.it.race")  # 다른 세션이 v1 을 만든다
        ver = await service.create_model_version(
            stale, ModelVersionCreate(model_name="argus.it.race", source="it"),
        )
    assert ver.version == 2
    assert await _version_statuses(api, "argus.it.race") == [(1, "PENDING_REGISTRATION"), (2, "PENDING_REGISTRATION")]
    async with api.factory() as db:
        model = (await db.execute(select(mm.RegisteredModel))).scalars().one()
    assert model.max_version_number == 2


async def test_rename_rejects_oci_model_name(api):
    async with api.factory() as db:
        db.add(om.OciModel(name="it-oci-taken", created_by="admin", owner="admin"))
        await db.commit()
    await _create(api, "argus.it.ren")
    r = await api.as_(ADMIN).req("PATCH", "/models/argus.it.ren", json={"name": "it-oci-taken"})
    assert r.status_code == 409 and "OCI 모델 허브" in r.json()["detail"]
    assert (await api.req("GET", "/models/argus.it.ren")).status_code == 200


async def test_hard_delete_cleans_version_prefixes_including_pre_rename(api, monkeypatch):
    """영구 삭제는 이름 프리픽스 통째가 아니라 버전 프리픽스 단위 — 이름 변경 전 옛 프리픽스도 정리."""
    calls: dict[str, list] = {"versions": [], "prefixes": [], "model": []}

    async def delete_version_files(name, version, bucket=None):
        calls["versions"].append((name, version))
        return 0

    async def delete_prefix_files(prefix, bucket=None):
        calls["prefixes"].append((bucket, prefix))
        return 0

    async def delete_model_files(name, bucket=None):
        calls["model"].append(name)
        return 0

    monkeypatch.setattr(model_store, "delete_version_files", delete_version_files)
    monkeypatch.setattr(model_store, "delete_prefix_files", delete_prefix_files)
    monkeypatch.setattr(model_store, "delete_model_files", delete_model_files)

    await _create(api, "argus.it.old")
    await _new_version(api, "argus.it.old")
    bucket = settings.os_bucket
    async with api.factory() as db:
        ver = (await db.execute(select(mm.ModelVersion))).scalars().one()
        ver.storage_location = f"s3://{bucket}/argus.it.old/v1/"
        await db.commit()
    assert (await api.req("PATCH", "/models/argus.it.old", json={"name": "argus.it.new"})).status_code == 200
    await _new_version(api, "argus.it.new")  # v2 는 새 이름 프리픽스

    r = await api.req("POST", "/models/hard-delete", json={"names": ["argus.it.new"]})
    assert r.json()["deleted"] == ["argus.it.new"]
    assert sorted(calls["versions"]) == [("argus.it.new", 1), ("argus.it.new", 2)]
    assert calls["prefixes"] == [(bucket, "argus.it.old/v1/")]
    assert calls["model"] == []  # 이름 프리픽스 통째 삭제는 하지 않는다
