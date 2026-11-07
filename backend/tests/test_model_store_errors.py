# SPDX-License-Identifier: Apache-2.0
"""모델 저장소(S3) 오류 처리 — 저장소 장애 503, 로컬 반입 경로 확인, 반입 실패 버전 표시.

배경(통합 시험 MDL-T10·OCI-T09):
- 로컬 반입에 없는 경로를 주면 HTTP 500 이었고, 그 전에 모델·버전을 먼저 만들어 빈 버전이 남았다.
- 저장소 장애 중 다운로드 주소 발급이 404(파일 없음)로 보였다. presigned 서명은 저장소에 묻지 않아
  ``/browse/download`` 는 없는 파일·장애 중에도 주소를 내줬다.
- 반입이 도중에 실패하면 버전이 PENDING_REGISTRATION 으로 남아 진행 중처럼 보였다.

실행:
    cd backend
    pytest tests/test_model_store_errors.py
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import model_store
from app.models.models import ModelVersion, RegisteredModel
from tests import test_mlflow_models as mlflow_t
from tests.test_oci_hub import ADMIN, Api, _create, _make_factory

DOWN = "Could not connect to the endpoint URL: http://minio:9000/model-artifacts"


@pytest.fixture
async def api():
    a = Api(await _make_factory())
    yield a
    await a.client.aclose()


@pytest.fixture
def s3(monkeypatch):
    """head_s3_object 를 가짜로 — ``objects`` 에 있는 키만 있고, ``state['down']`` 이면 연결 오류."""
    objects = {"m/v1/model.onnx": 10}
    state = {"down": False}

    async def head(path, bucket=None):
        if state["down"]:
            raise ConnectionError(DOWN)
        key = path.lstrip("/")
        if key not in objects:
            raise FileNotFoundError(f"파일이 저장소에 없습니다: {path}")
        return {"key": key, "size": objects[key], "content_type": None}

    monkeypatch.setattr(model_store, "head_s3_object", head)
    return state


# ---------------------------------------------------------------------------
# 다운로드 주소 발급 — 없음 404 와 장애 503 구분
# ---------------------------------------------------------------------------


async def test_download_url_distinguishes_missing_and_outage(api, s3):
    await _create(api, "m")
    path = "/model-store/m/versions/1/download-url"
    r = await api.as_(ADMIN).req("GET", path, params={"filename": "model.onnx"})
    assert r.status_code == 200 and "model.onnx" in r.json()["url"]
    assert (await api.req("GET", path, params={"filename": "nope.bin"})).status_code == 404

    s3["down"] = True
    r = await api.req("GET", path, params={"filename": "model.onnx"})
    assert r.status_code == 503 and "연결할 수 없습니다" in r.json()["detail"]
    # 실패한 발급은 다운로드 수에 들어가지 않는다(성공 1회만)
    assert (await api.as_(None).req("GET", "/oci-models/m")).json()["download_count"] == 1


async def test_browse_download_checks_file_before_signing(api, s3):
    api.as_(ADMIN)
    r = await api.req("GET", "/model-store/browse/download", params={"path": "/m/v1/model.onnx"})
    assert r.status_code == 200 and r.json()["url"]
    assert (await api.req("GET", "/model-store/browse/download", params={"path": "/m/v1/none"})).status_code == 404
    s3["down"] = True
    r = await api.req("GET", "/model-store/browse/download", params={"path": "/m/v1/model.onnx"})
    assert r.status_code == 503


async def test_outage_is_503_for_listing_and_bulk_urls(api, monkeypatch):
    async def down(*_a, **_kw):
        raise ConnectionError(DOWN)

    for fn in ("list_s3_directory", "generate_download_urls", "list_files", "generate_upload_url", "upload_file"):
        monkeypatch.setattr(model_store, fn, down)
    await _create(api, "m")
    api.as_(ADMIN)
    assert (await api.req("GET", "/model-store/browse/list", params={"path": "/"})).status_code == 503
    assert (await api.req("GET", "/model-store/m/versions/1/download-urls")).status_code == 503
    assert (await api.req("GET", "/model-store/m/versions/1/files")).status_code == 503
    assert (await api.req("POST", "/model-store/m/versions/1/upload-url", json={"filename": "a.bin"})).status_code == 503
    r = await api.req("POST", "/model-store/m/versions/1/upload", files={"file": ("a.bin", b"x")})
    assert r.status_code == 503


async def test_finalize_during_outage_is_503(api, monkeypatch):
    """확정(finalize)도 파일 크기를 저장소에서 읽는다 — 장애면 500 이 아니라 503."""
    async def down(*_a, **_kw):
        raise ConnectionError(DOWN)

    monkeypatch.setattr(model_store, "get_total_size", down)
    await _create(api, "m")
    r = await api.as_(ADMIN).req("POST", "/oci-models/m/versions/1/finalize", json={})
    assert r.status_code == 503 and "연결할 수 없습니다" in r.json()["detail"]

    async def broken(*_a, **_kw):
        raise RuntimeError("bucket policy denied")

    monkeypatch.setattr(model_store, "get_total_size", broken)
    r = await api.req("POST", "/oci-models/m/versions/1/finalize", json={})
    assert r.status_code == 500 and "bucket policy denied" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 로컬 반입
# ---------------------------------------------------------------------------


@pytest.fixture
async def mapi(tmp_path, monkeypatch):
    """MLflow 모델 테이블이 있는 테스트 앱(로컬 반입은 MLflow 등록 모델·버전을 만든다)."""
    monkeypatch.setattr(mlflow_t.settings, "data_dir", tmp_path / "data")
    a = mlflow_t.Api(await mlflow_t._make_factory())
    yield a
    await a.client.aclose()


async def _models(api) -> list[str]:
    async with api.factory() as db:
        return list((await db.execute(select(RegisteredModel.name))).scalars().all())


async def test_local_import_bad_dir_is_400_and_creates_nothing(mapi, tmp_path):
    api = mapi
    api.as_(ADMIN)
    r = await api.req("POST", "/model-store/import/local",
                      json={"local_dir": str(tmp_path / "none"), "model_name": "argus.it.bad"})
    assert r.status_code == 400 and "디렉터리가 없습니다" in r.json()["detail"]
    (tmp_path / "empty" / ".git").mkdir(parents=True)
    (tmp_path / "empty" / ".git" / "HEAD").write_text("x")       # 숨김 파일만 있으면 가져올 것이 없다
    r = await api.req("POST", "/model-store/import/local",
                      json={"local_dir": str(tmp_path / "empty"), "model_name": "argus.it.bad"})
    assert r.status_code == 400 and "가져올 파일이 없습니다" in r.json()["detail"]
    assert "argus.it.bad" not in await _models(api)


async def test_local_import_storage_failure_marks_version_failed(mapi, tmp_path, monkeypatch):
    api = mapi
    src = tmp_path / "model"
    src.mkdir()
    (src / "config.json").write_text("{}")

    async def upload_down(*_a, **_kw):
        raise ConnectionError(DOWN)

    monkeypatch.setattr(model_store, "upload_files", upload_down)
    r = await api.as_(ADMIN).req("POST", "/model-store/import/local",
                                 json={"local_dir": str(src), "model_name": "argus.it.local"})
    assert r.status_code == 503 and "연결할 수 없습니다" in r.json()["detail"]
    async with api.factory() as db:
        ver = (await db.execute(
            select(ModelVersion).join(RegisteredModel, ModelVersion.model_id == RegisteredModel.id)
            .where(RegisteredModel.name == "argus.it.local")
        )).scalars().one()
    assert ver.status == "FAILED_REGISTRATION" and "연결할 수 없습니다" in (ver.status_message or "")

    # 저장소 외 오류는 500 + 사유, 역시 실패로 표시
    async def upload_broken(*_a, **_kw):
        raise RuntimeError("bucket policy denied")

    monkeypatch.setattr(model_store, "upload_files", upload_broken)
    r = await api.req("POST", "/model-store/import/local", json={"local_dir": str(src), "model_name": "argus.it.local"})
    assert r.status_code == 500 and "로컬 반입 실패: bucket policy denied" in r.json()["detail"]
    async with api.factory() as db:
        statuses = (await db.execute(
            select(ModelVersion.status).join(RegisteredModel, ModelVersion.model_id == RegisteredModel.id)
            .where(RegisteredModel.name == "argus.it.local").order_by(ModelVersion.version)
        )).scalars().all()
    assert statuses == ["FAILED_REGISTRATION", "FAILED_REGISTRATION"]


# ---------------------------------------------------------------------------
# 장애 503 일관화 — 업로드 주소 발급 · manifest · MLflow 확정 (OCI-T09·MDL-T09 리뷰 후속)
# ---------------------------------------------------------------------------


class _DownS3:
    """head_bucket·create_bucket 이 모두 연결 오류를 내는 가짜 S3 클라이언트."""

    async def head_bucket(self, **_kw):
        raise ConnectionError(DOWN)

    async def create_bucket(self, **_kw):
        raise ConnectionError(DOWN)


async def test_upload_url_during_outage_is_503_not_signed(api, monkeypatch):
    """presign 은 저장소에 묻지 않으므로, 버킷 확인이 연결 오류를 삼키면 장애 중에도 200 이 나왔다."""
    from contextlib import asynccontextmanager

    from app.core import s3 as s3mod

    signed = []

    @asynccontextmanager
    async def down_client():
        yield _DownS3()

    async def presign(*a, **kw):
        signed.append(a)
        return "http://minio/x"

    monkeypatch.setattr(s3mod, "get_s3_client", down_client)
    monkeypatch.setattr(model_store, "presigned_url", presign)
    await _create(api, "m")
    r = await api.as_(ADMIN).req("POST", "/model-store/m/versions/1/upload-url", json={"filename": "a.bin"})
    assert r.status_code == 503 and "연결할 수 없습니다" in r.json()["detail"]
    assert signed == []  # 쓸 수 없는 주소를 만들지 않음


async def test_ensure_bucket_lenient_by_default(monkeypatch):
    """기동 시 등 기본 호출은 예전처럼 경고만 남기고 넘어간다."""
    from contextlib import asynccontextmanager

    from app.core import s3 as s3mod

    @asynccontextmanager
    async def down_client():
        yield _DownS3()

    monkeypatch.setattr(s3mod, "get_s3_client", down_client)
    await s3mod.ensure_bucket("b")  # 예외 없음
    with pytest.raises(ConnectionError):
        await s3mod.ensure_bucket("b", strict=True)


async def test_manifest_distinguishes_missing_and_outage(api, monkeypatch):
    state = {"err": FileNotFoundError("NoSuchKey")}

    async def download(*_a, **_kw):
        raise state["err"]

    monkeypatch.setattr(model_store, "download_file", download)
    path = "/model-store/m/versions/1/manifest"
    assert (await api.as_(None).req("GET", path)).status_code == 401  # 로그인 필요
    assert (await api.as_(ADMIN).req("GET", path)).status_code == 404
    state["err"] = ConnectionError(DOWN)
    r = await api.req("GET", path)
    assert r.status_code == 503 and "연결할 수 없습니다" in r.json()["detail"]


async def test_mlflow_store_finalize_outage_uses_common_503(mapi, monkeypatch):
    """MLflow 버전 확정도 다른 저장소 API 와 같은 안내 문구로 503, 저장소 외 오류는 500."""
    api = mapi
    await mlflow_t._create(api, "argus.it.fin")
    await mlflow_t._new_version(api, "argus.it.fin")

    async def down(*_a, **_kw):
        raise ConnectionError(DOWN)

    monkeypatch.setattr(model_store, "list_files", down)
    path = "/model-store/argus.it.fin/versions/1/finalize"
    r = await api.as_(ADMIN).req("POST", path, json={})
    assert r.status_code == 503 and r.json()["detail"].startswith("오브젝트 스토리지에 연결할 수 없습니다")

    async def broken(*_a, **_kw):
        raise RuntimeError("bucket policy denied")

    monkeypatch.setattr(model_store, "list_files", broken)
    r = await api.req("POST", path, json={})
    assert r.status_code == 500 and "bucket policy denied" in r.json()["detail"]
