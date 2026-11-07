# SPDX-License-Identifier: Apache-2.0
"""``ModelClient`` 단위 테스트 — 서버 대신 ``httpx.MockTransport`` 위의 가짜 서버로 요청을 받는다.

실행:
    cd sdk
    pip install -e '.[dev]'
    pytest
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from argus_catalog_sdk import ApiError, ModelClient
from argus_catalog_sdk import client as client_mod

BASE = "http://catalog.test"
S3 = "http://s3.test"


class FakeServer:
    """SDK 가 쓰는 OCI 허브·model-store 엔드포인트만 흉내 낸다."""

    def __init__(self):
        self.oci: dict[str, dict] = {}            # name → model
        self.mlflow: set[str] = set()             # OCI 와 이름이 겹치면 409
        self.versions: dict[str, list[dict]] = {}  # name → [version]
        self.store: dict[tuple[str, int], dict[str, bytes]] = {}  # (name, ver) → {filename: data}
        self.download_names: dict[tuple[str, int], list[str]] | None = None  # download-urls 가 돌려줄 이름(조작용)
        self.version_polls: list = []  # oci_list_versions 응답을 차례로 바꿀 때
        self.store_mtime: dict[tuple[str, int], str] = {}  # (name, ver) → files 목록의 last_modified
        self.cleanup_status: int | None = None  # 확정 전 파일 정리 API 가 돌려줄 오류(이전 서버 흉내)
        self.disk: dict[tuple[str, int], dict[str, bytes]] = {}  # 서버 디스크의 MLflow 버전 파일
        self.requests: list[tuple[str, str]] = []
        self.auth_seen: dict[str, str | None] = {}  # 경로 → 받은 authorization 헤더

    # -- 라우팅 --------------------------------------------------------------
    def __call__(self, req: httpx.Request) -> httpx.Response:
        req.read()
        path = req.url.path
        self.requests.append((req.method, path))
        if req.url.host == "s3.test":
            name, ver, fn = re.match(r"^/([^/]+)/v(\d+)/(.+)$", path).groups()
            if req.method == "PUT":
                assert "authorization" not in req.headers  # 저장소로 토큰을 보내지 않는다
                self.store.setdefault((name, int(ver)), {})[fn] = req.content
                return httpx.Response(200)
            data = self.store.get((name, int(ver)), {}).get(fn)
            return httpx.Response(200, content=data) if data is not None else httpx.Response(404)

        if req.method == "POST" and path == "/api/v1/auth/login":
            body = _json(req)
            if body == {"username": "admin", "password": "admin"}:
                return httpx.Response(200, json={"access_token": "tok"})
            return httpx.Response(401, json={"detail": "아이디 또는 비밀번호가 틀립니다"})

        if req.method == "POST" and path == "/api/v1/oci-models":
            name = _json(req)["name"]
            if name in self.oci or name in self.mlflow:
                return httpx.Response(409, json={"detail": f"'{name}' 이름이 이미 있습니다"})
            self.oci[name] = {"name": name}
            self.versions[name] = []
            return httpx.Response(200, json=self.oci[name])

        if req.method == "POST" and path == "/api/v1/oci-models/import/huggingface":
            return httpx.Response(202, json={"name": "hf", "version": 1, "status": "importing"})

        if m := re.match(r"^/api/v1/oci-models/([^/]+)/versions/(\d+)/finalize$", path):
            name, ver = m.group(1), int(m.group(2))
            files = self.store.get((name, ver), {})
            v = {"version": ver, "status": "ready", "file_count": len(files),
                 "total_size": sum(map(len, files.values()))}
            self.versions[name].append(v)
            return httpx.Response(200, json=v)

        if m := re.match(r"^/api/v1/oci-models/([^/]+)/versions$", path):
            if self.version_polls:
                status, body = self.version_polls.pop(0)
                return httpx.Response(status, json=body)
            if m.group(1) not in self.versions:
                return httpx.Response(404, json={"detail": "모델을 찾을 수 없습니다."})
            return httpx.Response(200, json=self.versions[m.group(1)])

        if m := re.match(r"^/api/v1/oci-models/([^/]+)$", path):
            if m.group(1) not in self.oci:
                return httpx.Response(404, json={"detail": "모델을 찾을 수 없습니다."})
            return httpx.Response(200, json=self.oci[m.group(1)])

        if m := re.match(r"^/api/v1/models/([^/]+)/versions/(\d+)$", path):
            key = (m.group(1), int(m.group(2)))
            if key not in self.disk:
                return httpx.Response(404, json={"detail": "버전을 찾을 수 없습니다."})
            return httpx.Response(200, json={"version": key[1], "files_path": f"/{key[0]}/versions/{key[1]}"})

        if path in ("/api/v1/filesystem/list", "/api/v1/filesystem/download"):
            self.auth_seen[path] = req.headers.get("authorization")
            if req.url.params.get("root_sub") != "model-artifacts":
                return httpx.Response(400, json={"detail": "root_sub"})
            if "authorization" not in req.headers:
                return httpx.Response(401, json={"detail": "Not authenticated"})
            return self._filesystem(path.rsplit("/", 1)[1], req.url.params["path"])

        if m := re.match(r"^/api/v1/model-store/([^/]+)/versions/(\d+)/(\w[\w-]*)$", path):
            key, action = (m.group(1), int(m.group(2))), m.group(3)
            if action == "files" and req.method == "DELETE":
                if self.cleanup_status:
                    return httpx.Response(self.cleanup_status, json={"detail": "x"})
                deleted = len(self.store.pop(key, {}))
                return httpx.Response(200, json={"model_name": key[0], "version": key[1], "deleted": deleted})
            if action == "upload-url":
                fn = _json(req)["filename"]
                return httpx.Response(200, json={"url": f"{S3}/{key[0]}/v{key[1]}/{fn}",
                                                 "key": f"{key[0]}/v{key[1]}/{fn}", "expires_in": 3600})
            if action == "upload":
                fn, data = _multipart_file(req)
                self.store.setdefault(key, {})[fn] = data
                return httpx.Response(200, json={"key": f"{key[0]}/v{key[1]}/{fn}", "size": len(data)})
            if action == "files":
                mtime = self.store_mtime.get(key)
                return httpx.Response(200, json=[{"filename": f, "size": len(d), **({"last_modified": mtime}
                                                                                     if mtime else {})}
                                                 for f, d in self.store.get(key, {}).items()])
            if action == "download-urls":
                names = (self.download_names or {}).get(key) or list(self.store.get(key, {}))
                if not names:
                    return httpx.Response(404, json={"detail": "파일이 없습니다."})
                return httpx.Response(200, json={"files": {f: f"{S3}/{key[0]}/v{key[1]}/{f}" for f in names}})
        return httpx.Response(404, json={"detail": f"no route {req.method} {path}"})

    def _filesystem(self, op: str, fs_path: str) -> httpx.Response:
        """model-artifacts 루트 아래 ``/{name}/versions/{v}/...`` 파일 브라우저."""
        m = re.match(r"^/([^/]+)/versions/(\d+)(?:/(.*))?$", fs_path)
        files = self.disk.get((m.group(1), int(m.group(2))), {}) if m else {}
        sub = (m.group(3) or "").strip("/") if m else ""
        if op == "download":
            data = files.get(sub)
            return httpx.Response(200, content=data) if data is not None else httpx.Response(404)
        prefix = f"{sub}/" if sub else ""
        inside = {f[len(prefix):]: d for f, d in files.items() if f.startswith(prefix)}
        if not inside:
            return httpx.Response(404, json={"detail": "경로가 없습니다."})
        folders = sorted({f.split("/", 1)[0] for f in inside if "/" in f})
        return httpx.Response(200, json={
            "folders": [{"name": d} for d in folders],
            "files": [{"name": f, "size": len(d), "last_modified": "2026-01-01T00:00:00"}
                      for f, d in inside.items() if "/" not in f],
        })


def _json(req: httpx.Request):
    import json
    return json.loads(req.content)


def _multipart_file(req: httpx.Request) -> tuple[str, bytes]:
    boundary = req.headers["content-type"].split("boundary=")[1].encode()
    part = req.content.split(b"--" + boundary)[1]
    head, _, body = part.partition(b"\r\n\r\n")
    filename = re.search(rb'filename="([^"]*)"', head).group(1).decode()
    return filename, body.removesuffix(b"\r\n")


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def http(server) -> httpx.Client:
    with httpx.Client(transport=httpx.MockTransport(server)) as h:
        yield h


@pytest.fixture
def client(http) -> ModelClient:
    return ModelClient(BASE, token="tok", http_client=http)


def make_tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


# ---------------------------------------------------------------------------
# 오류
# ---------------------------------------------------------------------------


def test_api_error_carries_status_and_stays_runtime_error(client):
    with pytest.raises(ApiError) as ei:
        client.oci_get_model("nope")
    assert ei.value.status_code == 404
    assert ei.value.detail == "모델을 찾을 수 없습니다."
    assert isinstance(ei.value, RuntimeError)
    assert str(ei.value) == "API error 404: 모델을 찾을 수 없습니다."


def test_login_failure_raises_api_error(http):
    with pytest.raises(ApiError) as ei:
        ModelClient(BASE, username="admin", password="wrong", http_client=http)
    assert ei.value.status_code == 401
    assert ModelClient(BASE, username="admin", password="admin", http_client=http).token == "tok"


# ---------------------------------------------------------------------------
# 연결 재사용 · 닫기
# ---------------------------------------------------------------------------


def test_reuses_one_http_client_and_closes_only_its_own(http):
    with ModelClient(BASE, token="tok") as own:
        inner = own._http
        assert isinstance(inner, httpx.Client) and not inner.is_closed
    assert inner.is_closed

    with ModelClient(BASE, token="tok", http_client=http) as borrowed:
        assert borrowed._http is http
    assert not http.is_closed  # 넘겨받은 클라이언트는 닫지 않는다


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


def test_push_keeps_subdirectories_and_skips_hidden(client, server, tmp_path):
    src = make_tree(tmp_path / "m", {
        "config.json": b"{}",
        "onnx/model.onnx": b"ONNX",
        "a/config.json": b"A",
        "b/config.json": b"B",
        ".gitattributes": b"x",
        ".git/HEAD": b"x",
        "sub/.DS_Store": b"x",
    })
    result = client.push(src, "my-model")
    assert result["version"] == 1
    assert result["file_count"] == 4
    assert server.store[("my-model", 1)] == {
        "a/config.json": b"A", "b/config.json": b"B", "config.json": b"{}", "onnx/model.onnx": b"ONNX",
    }
    assert result["total_size"] == 2 + 4 + 1 + 1


def test_push_large_files_via_presigned(client, server, tmp_path):
    src = make_tree(tmp_path / "m", {"small.json": b"{}", "w/big.bin": b"B" * 100})
    result = client.push(src, "my-model", presigned_min_size=50)
    assert server.store[("my-model", 1)] == {"small.json": b"{}", "w/big.bin": b"B" * 100}
    posts = [p for m, p in server.requests if m == "POST" and "/model-store/" in p]
    assert posts == ["/api/v1/model-store/my-model/versions/1/upload",       # small.json — 서버 경유
                     "/api/v1/model-store/my-model/versions/1/upload-url"]  # w/big.bin — presigned
    assert ("PUT", "/my-model/v1/w/big.bin") in server.requests
    assert result["file_count"] == 2


def test_push_existing_model_adds_next_version(client, server, tmp_path):
    src = make_tree(tmp_path / "m", {"w.bin": b"1"})
    client.push(src, "my-model")
    assert client.push(src, "my-model")["version"] == 2


def test_push_skips_version_with_leftover_files(client, server, tmp_path):
    """확정 전에 실패한 push 가 남긴 v1 파일이 새 버전에 섞이지 않는다."""
    server.oci["my-model"] = {"name": "my-model"}
    server.versions["my-model"] = []
    server.store[("my-model", 1)] = {"stale.bin": b"old"}
    src = make_tree(tmp_path / "m", {"w.bin": b"new"})
    result = client.push(src, "my-model")
    assert result["version"] == 2
    assert server.store[("my-model", 2)] == {"w.bin": b"new"}


def _leftover(server, ver: int, mtime: str):
    server.oci["my-model"] = {"name": "my-model"}
    server.versions["my-model"] = []
    server.store[("my-model", ver)] = {"stale.bin": b"old"}
    server.store_mtime[("my-model", ver)] = mtime


def test_push_cleans_stale_leftover_and_reuses_version(client, server, tmp_path):
    """한 시간보다 오래된 남은 파일은 끊긴 push 의 찌꺼기 — 지우고 그 번호를 다시 쓴다."""
    _leftover(server, 1, "2020-01-01T00:00:00+00:00")
    src = make_tree(tmp_path / "m", {"w.bin": b"new"})
    result = client.push(src, "my-model")
    assert (result["version"], result["cleaned_versions"]) == (1, [1])
    assert server.store[("my-model", 1)] == {"w.bin": b"new"}
    assert ("DELETE", "/api/v1/model-store/my-model/versions/1/files") in server.requests


def test_push_skips_recent_leftover(client, server, tmp_path):
    """최근 파일은 진행 중인 다른 push 일 수 있어 건드리지 않고 다음 번호를 쓴다."""
    from datetime import datetime, timezone

    _leftover(server, 1, datetime.now(timezone.utc).isoformat())
    result = client.push(make_tree(tmp_path / "m", {"w.bin": b"new"}), "my-model")
    assert (result["version"], result["cleaned_versions"]) == (2, [])
    assert server.store[("my-model", 1)] == {"stale.bin": b"old"}
    assert not any(m == "DELETE" for m, _ in server.requests)


def test_push_stale_after_zero_cleans_even_recent(client, server, tmp_path):
    from datetime import datetime, timezone

    _leftover(server, 1, datetime.now(timezone.utc).isoformat())
    result = client.push(make_tree(tmp_path / "m", {"w.bin": b"new"}), "my-model", stale_after=0)
    assert (result["version"], result["cleaned_versions"]) == (1, [1])


@pytest.mark.parametrize("status", [404, 405, 409])
def test_push_falls_back_to_skip_when_cleanup_refused(client, server, tmp_path, status):
    """정리 API 가 없는 이전 서버(404·405)나 버전 기록이 있는 번호(409)면 예전처럼 건너뛴다."""
    _leftover(server, 1, "2020-01-01T00:00:00")
    server.cleanup_status = status
    result = client.push(make_tree(tmp_path / "m", {"w.bin": b"new"}), "my-model")
    assert (result["version"], result["cleaned_versions"]) == (2, [])


def test_push_cleanup_other_error_propagates(client, server, tmp_path):
    _leftover(server, 1, "2020-01-01T00:00:00")
    server.cleanup_status = 403
    with pytest.raises(ApiError) as ei:
        client.push(make_tree(tmp_path / "m", {"w.bin": b"new"}), "my-model")
    assert ei.value.status_code == 403
    assert not any("upload" in p for _, p in server.requests)


@pytest.mark.parametrize("ts,expected", [
    ("2020-01-01T00:00:00", True),            # 시간대 없으면 UTC 로 본다
    ("2020-01-01T00:00:00+09:00", True),
    ("", False),                              # 시각을 모르면 지우지 않는다
    ("not-a-date", False),
])
def test_older_than(ts, expected):
    assert client_mod._older_than([{"last_modified": ts}], 3600) is expected


def test_push_name_taken_by_mlflow_model(client, server, tmp_path):
    server.mlflow.add("taken")
    src = make_tree(tmp_path / "m", {"w.bin": b"1"})
    with pytest.raises(ApiError, match="OCI 모델이 아닌 다른 모델") as ei:
        client.push(src, "taken")
    assert ei.value.status_code == 409
    assert not any("upload" in p for _, p in server.requests)


def test_push_empty_directory_creates_nothing(client, server, tmp_path):
    src = make_tree(tmp_path / "m", {".hidden": b"x"})
    with pytest.raises(ValueError, match="올릴 파일이 없습니다"):
        client.push(src, "my-model")
    assert server.requests == []


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


def test_pull_streams_files_into_subdirectories(client, server, tmp_path):
    server.store[("m", 1)] = {"config.json": b"{}", "onnx/model.onnx": b"X" * (3 * client_mod.DOWNLOAD_CHUNK + 7)}
    out = client.pull("m", 1, tmp_path / "out")
    assert sorted(Path(p).relative_to(tmp_path / "out").as_posix() for p in out) == ["config.json", "onnx/model.onnx"]
    assert (tmp_path / "out/onnx/model.onnx").stat().st_size == 3 * client_mod.DOWNLOAD_CHUNK + 7
    assert not list((tmp_path / "out").rglob("*.part"))


def test_pull_returns_paths_relative_to_given_dest(client, server, tmp_path, monkeypatch):
    server.store[("m", 1)] = {"a.txt": b"a", "sub/b.txt": b"b"}
    monkeypatch.chdir(tmp_path)
    assert sorted(client.pull("m", 1, "out")) == [str(Path("out/a.txt")), str(Path("out/sub/b.txt"))]


@pytest.mark.parametrize("bad",["../escape.txt", "a/../../escape.txt", "/etc/passwd", "a//b", "./x"])
def test_pull_rejects_names_outside_dest(client, server, tmp_path, bad):
    server.store[("m", 1)] = {"ok.txt": b"ok"}
    server.download_names = {("m", 1): ["ok.txt", bad]}
    with pytest.raises(ValueError):
        client.pull("m", 1, tmp_path / "out")
    # 이름 검사가 받기 전에 끝나므로 아무 파일도 쓰지 않는다
    assert list((tmp_path / "out").iterdir()) == []
    assert not (tmp_path / "escape.txt").exists()


def test_pull_failed_download_leaves_no_partial_file(client, server, tmp_path):
    server.store[("m", 1)] = {"a.txt": b"a"}
    server.download_names = {("m", 1): ["a.txt", "missing.txt"]}
    with pytest.raises(RuntimeError, match="missing.txt"):
        client.pull("m", 1, tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["a.txt"]


def test_delete_unfinalized_files_returns_count(client, server):
    server.store[("m", 3)] = {"a": b"1", "b": b"2"}
    assert client.delete_unfinalized_files("m", 3) == 2
    assert ("m", 3) not in server.store


# ---------------------------------------------------------------------------
# MLflow 모델 — 파일이 서버 디스크에 있을 때
# ---------------------------------------------------------------------------


def test_list_files_falls_back_to_server_disk(client, server):
    server.disk[("ml", 1)] = {"MLmodel": b"m", "model.pkl": b"pp", "env/conda.yaml": b"c"}
    files = client.list_files("ml", 1)
    assert [(f["filename"], f["size"]) for f in files] == [("MLmodel", 1), ("env/conda.yaml", 1), ("model.pkl", 2)]
    assert set(files[0]) == {"filename", "size", "last_modified"}


def test_list_files_prefers_store(client, server):
    server.store[("ml", 1)] = {"s3.bin": b"x"}
    server.disk[("ml", 1)] = {"MLmodel": b"m"}
    assert [f["filename"] for f in client.list_files("ml", 1)] == ["s3.bin"]
    assert not any("filesystem" in p for _, p in server.requests)


def test_list_files_empty_for_unknown_version(client, server):
    assert client.list_files("nope", 1) == []


def test_pull_falls_back_to_server_disk_with_auth(client, server, tmp_path):
    server.disk[("ml", 2)] = {"MLmodel": b"m", "env/conda.yaml": b"conda"}
    out = client.pull("ml", 2, tmp_path / "out")
    assert sorted(Path(p).relative_to(tmp_path / "out").as_posix() for p in out) == ["MLmodel", "env/conda.yaml"]
    assert (tmp_path / "out/env/conda.yaml").read_bytes() == b"conda"
    assert server.auth_seen["/api/v1/filesystem/download"] == "Bearer tok"


def test_pull_server_disk_needs_login(http, server, tmp_path):
    server.disk[("ml", 2)] = {"MLmodel": b"m"}
    anon = ModelClient(BASE, http_client=http)
    with pytest.raises(ApiError, match="로그인") as ei:
        anon.pull("ml", 2, tmp_path / "out")
    assert ei.value.status_code == 401


def test_pull_missing_everywhere_keeps_store_404(client, server, tmp_path):
    with pytest.raises(ApiError) as ei:
        client.pull("nope", 1, tmp_path / "out")
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# HF 가져오기 대기
# ---------------------------------------------------------------------------


def _hf(client, **kw):
    return client.oci_import_huggingface("org/hf", poll_interval=0, wait_timeout=5, **kw)


def test_hf_import_waits_until_ready(client, server):
    server.version_polls = [(200, [{"version": 1, "status": "importing"}]),
                            (200, [{"version": 1, "status": "ready", "file_count": 3}])]
    assert _hf(client) == {"name": "hf", "version": 1, "status": "ready", "file_count": 3}


def test_hf_import_failed(client, server):
    server.version_polls = [(200, [{"version": 1, "status": "failed", "extra_metadata": {"error": "boom"}}])]
    with pytest.raises(RuntimeError, match="boom"):
        _hf(client)


def test_hf_import_version_disappears(client, server):
    server.version_polls = [(200, [{"version": 1, "status": "importing"}]), (200, [])]
    with pytest.raises(RuntimeError, match="사라졌습니다"):
        _hf(client)


def test_hf_import_model_deleted(client, server):
    server.version_polls = [(404, {"detail": "모델을 찾을 수 없습니다."})]
    with pytest.raises(RuntimeError, match="삭제됐습니다"):
        _hf(client)


# ---------------------------------------------------------------------------
# 리뷰 후속 — 기본 정리 기준 · 422 오류 문구 · 로그인 실패 시 연결 정리
# ---------------------------------------------------------------------------


def test_push_default_keeps_leftover_younger_than_a_day(client, server, tmp_path):
    """기본 기준(24시간) — 몇 시간 된 파일은 큰 파일을 오래 올리는 다른 push 일 수 있어 지우지 않는다."""
    from datetime import datetime, timedelta, timezone

    _leftover(server, 1, (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
    result = client.push(make_tree(tmp_path / "m", {"w.bin": b"new"}), "my-model")
    assert (result["version"], result["cleaned_versions"]) == (2, [])
    assert client_mod.STALE_AFTER == 24 * 3600


def test_validation_error_detail_is_readable():
    resp = httpx.Response(422, json={"detail": [
        {"loc": ["body", "name"], "msg": "String should have at most 64 characters", "type": "string_too_long"},
        {"loc": ["query", "page_size"], "msg": "Input should be less than or equal to 50"},
    ]})
    assert client_mod._error_detail(resp) == ("name: String should have at most 64 characters; "
                                              "query.page_size: Input should be less than or equal to 50")


def test_login_failure_closes_own_http_client(monkeypatch):
    made = []

    class Tracking(httpx.Client):
        def __init__(self, *a, **kw):
            super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "no"})))
            made.append(self)

    monkeypatch.setattr(client_mod.httpx, "Client", Tracking)
    with pytest.raises(ApiError):
        ModelClient(BASE, username="admin", password="wrong")
    assert made and made[0].is_closed
