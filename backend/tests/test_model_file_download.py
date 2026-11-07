# SPDX-License-Identifier: Apache-2.0
"""모델 파일 다운로드·보기 단위 테스트 (자기완결형, 임시 data_dir + 가짜 S3).

배경: OCI 모델 파일은 presigned URL(``http://minio:9000/...``)로 내려받아 브라우저에서 열리지 않았고,
MLflow 모델 파일은 화면이 파일 전체를 메모리에 받은 뒤 저장해 큰 파일이 멈췄다.

확인하는 규칙:
- presigned URL 은 외부용 주소(``object_storage_public_endpoint``)로 서명한다(비우면 endpoint).
- ``POST /filesystem/download-link``·``POST /model-store/browse/download-link`` 로 받은 짧은 토큰으로
  로그인 헤더 없이 그 파일만 내려받을 수 있다(다른 파일·다른 종류의 토큰·만료·변조는 거부).
- ``GET /model-store/browse/file`` 은 서버가 저장소에서 읽어 흘려보낸다(없으면 404, 저장소 장애 503).
- 한글 파일 이름도 ``Content-Disposition`` 의 ``filename*`` 로 깨지지 않는다.
- MLflow 모델 파일(``/filesystem/*``) 조회는 관리자는 전체, 일반 사용자는 삭제되지 않은 등록 모델의
  디렉터리 안만(OCI ``/model-store/browse/*`` 와 같은 규칙). 변경은 관리자만.

실행:
    cd backend
    pytest tests/test_model_file_download.py
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from jose import jwt

from app.core import download_link, s3
from app.core.auth import (
    LOCAL_JWT_ALGORITHM,
    LOCAL_JWT_SECRET_KEY,
    TokenUser,
    get_current_user,
    get_optional_user,
)
from app.core.config import settings
from app.core.database import Base, get_session
from app.filesystemmgr.router import router as fs_router
from app.models import model_store
from app.models.store_router import router as store_router

ADMIN = TokenUser(sub="admin-sub", username="admin", roles=["argus-admin"])
ALICE = TokenUser(sub="alice-sub", username="alice", roles=["argus-user"])


class Api:
    def __init__(self, session_factory=None):
        self.user: TokenUser | None = ADMIN
        app = FastAPI()
        app.include_router(fs_router, prefix="/api/v1")
        app.include_router(store_router, prefix="/api/v1")

        async def _user():
            if self.user is None:
                raise HTTPException(status_code=401, detail="인증이 필요합니다.")
            return self.user

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_optional_user] = lambda: self.user
        if session_factory is not None:
            async def _session():
                async with session_factory() as s:
                    yield s
            app.dependency_overrides[get_session] = _session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    def as_(self, user: TokenUser | None) -> "Api":
        self.user = user
        return self

    async def req(self, method: str, path: str, **kw) -> httpx.Response:
        return await self.client.request(method, "/api/v1" + path, **kw)


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    d = tmp_path / "model-artifacts" / "argus.it.iris" / "versions" / "1"
    d.mkdir(parents=True)
    (d / "MLmodel").write_text("flavors: {}\n")
    (d / "모델 설명.txt").write_text("한글 이름 파일")
    return tmp_path


@pytest.fixture
async def db(data_dir):
    """OCI 모델·MLflow 등록 모델(경로 범위 판단)·권한 매트릭스(메뉴 차단)만 둔 임시 DB.

    OCI 모델 'm'·'legacy', MLflow 모델 'argus.it.iris'(활성)·'argus.it.gone'(소프트 삭제)이 등록돼 있다."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import app.catalog.models  # noqa: F401 — RegisteredModel.datasource_id 의 FK 대상 테이블을 메타데이터에 등록
    from app.models.models import RegisteredModel
    from app.oci_hub.models import OciModel
    from app.permissions.models import Permission

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,
                            tables=[OciModel.__table__, Permission.__table__, RegisteredModel.__table__])
    make = async_sessionmaker(engine, expire_on_commit=False)
    root = data_dir / "model-artifacts"
    async with make() as s:
        s.add(OciModel(name="m", storage_prefix="m/", bucket="model-artifacts"))
        s.add(OciModel(name="legacy", storage_prefix="legacy", bucket="model-artifacts"))  # 끝 "/" 없는 예전 데이터
        s.add(RegisteredModel(name="argus.it.iris", urn="argus.it.iris.PROD.model",
                              storage_location=f"file://{root / 'argus.it.iris'}"))
        s.add(RegisteredModel(name="argus.it.gone", urn="argus.it.gone.PROD.model", status="deleted",
                              storage_location=f"file://{root / 'argus.it.gone'}"))
        await s.commit()
    yield make
    await engine.dispose()


@pytest.fixture
async def api(data_dir, db):
    a = Api(db)
    yield a
    await a.client.aclose()


FS = {"root_sub": "model-artifacts"}
MLMODEL = "/argus.it.iris/versions/1/MLmodel"


# ---------------------------------------------------------------------------
# MLflow 모델 파일 (로컬 파일시스템)
# ---------------------------------------------------------------------------


async def test_fs_download_requires_login_or_token(api):
    r = await api.as_(None).req("GET", "/filesystem/download", params={**FS, "path": MLMODEL})
    assert r.status_code == 401
    # 일반 사용자도 등록 모델 디렉터리 안의 파일은 받을 수 있다
    r = await api.as_(ALICE).req("GET", "/filesystem/download", params={**FS, "path": MLMODEL})
    assert r.status_code == 200 and r.text == "flavors: {}\n"
    r = await api.as_(ADMIN).req("GET", "/filesystem/download", params={**FS, "path": MLMODEL})
    assert r.status_code == 200 and r.text == "flavors: {}\n"
    assert r.headers["content-disposition"].startswith('attachment; filename="MLmodel"')


async def test_fs_user_reads_only_inside_registered_model(api, data_dir):
    """일반 사용자 — 등록 모델 디렉터리 안은 조회 가능, 전체 탐색·상위·삭제 모델·다른 root_sub 는 403."""
    gone = data_dir / "model-artifacts" / "argus.it.gone" / "versions" / "1"
    gone.mkdir(parents=True)
    (gone / "MLmodel").write_text("x")
    (data_dir / "model-artifacts" / "loose.txt").write_text("등록 안 된 파일")

    api.as_(ALICE)
    r = await api.req("GET", "/filesystem/list", params={**FS, "path": "/argus.it.iris/versions/1"})
    assert r.status_code == 200 and {f["name"] for f in r.json()["files"]} == {"MLmodel", "모델 설명.txt"}
    assert (await api.req("GET", "/filesystem/list", params={**FS, "path": "/argus.it.iris"})).status_code == 200
    assert (await api.req("GET", "/filesystem/stat", params={**FS, "path": MLMODEL})).status_code == 200
    r = await api.req("POST", "/filesystem/download-link", params={**FS, "path": MLMODEL})
    assert r.status_code == 200
    token = r.json()["token"]
    r = await api.as_(None).req("GET", "/filesystem/download", params={**FS, "path": MLMODEL, "token": token})
    assert r.status_code == 200

    api.as_(ALICE)
    for path in ("/", "", "/loose.txt", "/argus.it.gone/versions/1", "/argus.it.gone/versions/1/MLmodel",
                 "/argus.it.iris/../loose.txt", "/argus.it.iris/../../etc"):
        r = await api.req("GET", "/filesystem/list", params={**FS, "path": path})
        assert r.status_code == 403, (path, r.text)
    detail = (await api.req("GET", "/filesystem/list", params={**FS, "path": "/"})).json()["detail"]
    assert "관리자" in detail
    for method, ep in (("GET", "/filesystem/stat"), ("POST", "/filesystem/download-link"),
                       ("GET", "/filesystem/preview"), ("GET", "/filesystem/download")):
        r = await api.req(method, ep, params={**FS, "path": "/argus.it.gone/versions/1/MLmodel"})
        assert r.status_code == 403, (ep, r.text)
    # 다른 root_sub(OCI 파일·전체 data 디렉터리)는 관리자 전용
    assert (await api.req("GET", "/filesystem/list",
                          params={"root_sub": "oci-artifacts", "path": "/argus.it.iris"})).status_code == 403
    assert (await api.req("GET", "/filesystem/list", params={"path": "/model-artifacts/argus.it.iris"})).status_code == 403
    # 변경은 계속 관리자만
    assert (await api.req("POST", "/filesystem/folders", params=FS,
                          json={"path": "/argus.it.iris/new"})).status_code == 403
    assert (await api.req("POST", "/filesystem/delete", params=FS,
                          json={"paths": [MLMODEL]})).status_code == 403
    # 관리자는 전체
    api.as_(ADMIN)
    assert (await api.req("GET", "/filesystem/list", params={**FS, "path": "/"})).status_code == 200
    assert (await api.req("GET", "/filesystem/list", params={**FS, "path": "/argus.it.gone/versions/1"})).status_code == 200


async def test_fs_user_blocked_when_models_menu_denied(api, db):
    """'MLflow 모델' 메뉴를 막은 역할은 모델 디렉터리 안도 403."""
    from app.permissions import router as perm
    from app.permissions.models import Permission

    async with db() as s:
        s.add(Permission(kind="MENU", perm_key="models", role_id="argus-superuser"))
        await s.commit()
    perm._menu_cache.clear()
    r = await api.as_(ALICE).req("GET", "/filesystem/list", params={**FS, "path": "/argus.it.iris"})
    assert r.status_code == 403 and "models" in r.json()["detail"]
    perm._menu_cache.clear()


async def test_fs_download_link_lets_browser_download_without_header(api):
    r = await api.as_(ADMIN).req("POST", "/filesystem/download-link", params={**FS, "path": MLMODEL})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert r.json()["expires_in"] == download_link.DEFAULT_TTL_SECONDS

    r = await api.as_(None).req("GET", "/filesystem/download", params={**FS, "path": MLMODEL, "token": token})
    assert r.status_code == 200 and r.text == "flavors: {}\n"
    # 같은 토큰으로 다른 파일·다른 저장소는 안 된다
    other = "/argus.it.iris/versions/1/모델 설명.txt"
    assert (await api.req("GET", "/filesystem/download", params={**FS, "path": other, "token": token})).status_code == 403
    r = await api.req("GET", "/filesystem/download", params={"root_sub": "oci-artifacts", "path": MLMODEL, "token": token})
    assert r.status_code == 403
    # 변조·일반 접근 토큰은 거부
    assert (await api.req("GET", "/filesystem/download",
                          params={**FS, "path": MLMODEL, "token": token[:-2] + "xx"})).status_code == 401
    access_like = jwt.encode({"typ": "access", "sub": "x", "exp": int(time.time()) + 60},
                             LOCAL_JWT_SECRET_KEY, algorithm=LOCAL_JWT_ALGORITHM)
    assert (await api.req("GET", "/filesystem/download",
                          params={**FS, "path": MLMODEL, "token": access_like})).status_code == 401


async def test_fs_download_link_expires(api):
    expired = download_link.issue("fs:model-artifacts", MLMODEL, "alice-sub", ttl=-1)
    r = await api.as_(None).req("GET", "/filesystem/download", params={**FS, "path": MLMODEL, "token": expired})
    assert r.status_code == 401 and "만료" in r.json()["detail"]


async def test_fs_download_link_validates_target(api):
    r = await api.as_(None).req("POST", "/filesystem/download-link", params={**FS, "path": MLMODEL})
    assert r.status_code == 401
    api.as_(ADMIN)
    assert (await api.req("POST", "/filesystem/download-link", params={**FS, "path": "/nope"})).status_code == 404
    assert (await api.req("POST", "/filesystem/download-link",
                          params={**FS, "path": "/argus.it.iris/versions/1"})).status_code == 400
    assert (await api.req("POST", "/filesystem/download-link",
                          params={"root_sub": "../etc", "path": "/hostname"})).status_code == 403


async def test_fs_download_korean_filename_and_inline(api):
    path = "/argus.it.iris/versions/1/모델 설명.txt"
    r = await api.req("GET", "/filesystem/download", params={**FS, "path": path, "inline": "true"})
    assert r.status_code == 200 and r.text == "한글 이름 파일"
    cd = r.headers["content-disposition"]
    assert cd.startswith("inline;")
    assert unquote(cd.split("filename*=UTF-8''", 1)[1]) == "모델 설명.txt"


# ---------------------------------------------------------------------------
# OCI 모델 파일 (S3) — 서버가 읽어 흘려보내기
# ---------------------------------------------------------------------------


class StorageDownError(Exception):
    pass


@pytest.fixture
def fake_s3_objects(monkeypatch):
    objects: dict[str, bytes] = {"m/v1/config.json": b'{"arch":"it"}', "m/v1/가중치.bin": b"\x00" * 3000}
    state = {"down": False}

    async def head(path, bucket=None):
        if state["down"]:
            raise StorageDownError("Could not connect to the endpoint URL: http://minio:9000/")
        key = path.lstrip("/")
        if key not in objects:
            raise FileNotFoundError(f"파일이 저장소에 없습니다: {path}")
        return {"key": key, "size": len(objects[key]), "content_type": None}

    async def iter_obj(path, bucket=None, chunk_size=1024):
        data = objects[path.lstrip("/")]
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    monkeypatch.setattr(model_store, "head_s3_object", head)
    monkeypatch.setattr(model_store, "iter_s3_object", iter_obj)
    return state


async def test_s3_file_streams_through_server(api, fake_s3_objects):
    r = await api.as_(ALICE).req("GET", "/model-store/browse/file", params={"path": "/m/v1/config.json"})
    assert r.status_code == 200 and r.content == b'{"arch":"it"}'
    assert r.headers["content-type"].startswith("application/json")
    r = await api.req("GET", "/model-store/browse/file", params={"path": "/m/v1/가중치.bin"})
    assert r.status_code == 200 and len(r.content) == 3000 and r.headers["content-length"] == "3000"
    assert unquote(r.headers["content-disposition"].split("filename*=UTF-8''", 1)[1]) == "가중치.bin"
    assert (await api.req("GET", "/model-store/browse/file", params={"path": "/m/v1/none"})).status_code == 404
    assert (await api.as_(None).req("GET", "/model-store/browse/file",
                                    params={"path": "/m/v1/config.json"})).status_code == 401


async def test_s3_download_link(api, fake_s3_objects):
    r = await api.as_(ALICE).req("POST", "/model-store/browse/download-link", params={"path": "/m/v1/config.json"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = await api.as_(None).req("GET", "/model-store/browse/file", params={"path": "/m/v1/config.json", "token": token})
    assert r.status_code == 200 and r.content == b'{"arch":"it"}'
    r = await api.req("GET", "/model-store/browse/file", params={"path": "/m/v1/가중치.bin", "token": token})
    assert r.status_code == 403
    # 파일시스템용 토큰은 S3 에 쓸 수 없다
    fs_token = download_link.issue("fs:model-artifacts", "/m/v1/config.json", "alice-sub")
    r = await api.req("GET", "/model-store/browse/file", params={"path": "/m/v1/config.json", "token": fs_token})
    assert r.status_code == 403
    assert (await api.as_(ALICE).req("POST", "/model-store/browse/download-link",
                                     params={"path": "/m/v1/none"})).status_code == 404


async def test_s3_browse_scope_for_non_admin(api, db, fake_s3_objects):
    """일반 사용자는 등록된 OCI 모델 경로 안에서만 — 버킷 전체·다른 경로는 관리자만."""
    from app.permissions.models import Permission

    api.as_(ALICE)
    assert (await api.req("GET", "/model-store/browse/file", params={"path": "/other/x.json"})).status_code == 403
    assert (await api.req("GET", "/model-store/browse/list", params={"path": "/"})).status_code == 403
    assert (await api.req("GET", "/model-store/browse/list", params={"path": "/m/../other"})).status_code == 403
    assert (await api.req("POST", "/model-store/browse/download-link",
                          params={"path": "/other/x.json"})).status_code == 403
    assert (await api.req("GET", "/model-store/browse/download", params={"path": "/other/x.json"})).status_code == 403
    # 끝 "/" 없이 저장된 모델 경로도 허용 (없는 파일이면 404 — 범위 검사는 통과)
    assert (await api.req("GET", "/model-store/browse/file", params={"path": "/legacy/v1/x"})).status_code == 404
    # 관리자는 버킷 전체 (없는 파일이면 404 — 범위 검사는 통과)
    assert (await api.as_(ADMIN).req("GET", "/model-store/browse/file",
                                     params={"path": "/other/x.json"})).status_code == 404
    # OCI 모델 허브 메뉴를 막은 역할은 모델 경로도 403
    async with db() as s:
        s.add(Permission(kind="MENU", perm_key="oci-hub", role_id="argus-superuser"))
        await s.commit()
    from app.permissions import router as perm
    perm._menu_cache.clear()
    r = await api.as_(ALICE).req("GET", "/model-store/browse/file", params={"path": "/m/v1/config.json"})
    assert r.status_code == 403 and "oci-hub" in r.json()["detail"]
    perm._menu_cache.clear()


async def test_s3_file_storage_unavailable_is_503(api, fake_s3_objects):
    fake_s3_objects["down"] = True
    r = await api.req("GET", "/model-store/browse/file", params={"path": "/m/v1/config.json"})
    assert r.status_code == 503 and "연결할 수 없습니다" in r.json()["detail"]
    r = await api.req("POST", "/model-store/browse/download-link", params={"path": "/m/v1/config.json"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# presigned URL 외부용 주소
# ---------------------------------------------------------------------------


async def test_presigned_url_uses_public_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "os_endpoint", "http://minio:9000")
    monkeypatch.setattr(settings, "os_public_endpoint", "")
    assert s3.presign_endpoint() == "http://minio:9000"
    url = await model_store.generate_s3_download_url("/m/v1/config.json", bucket="model-artifacts")
    assert urlparse(url).netloc == "minio:9000"

    monkeypatch.setattr(settings, "os_public_endpoint", "https://models.example.com")
    assert s3.presign_endpoint() == "https://models.example.com"
    url = await model_store.generate_s3_download_url("/m/v1/config.json", bucket="model-artifacts")
    parsed = urlparse(url)
    assert parsed.netloc == "models.example.com" and parsed.path == "/model-artifacts/m/v1/config.json"
    assert "Signature=" in parsed.query or "X-Amz-Signature=" in parsed.query
