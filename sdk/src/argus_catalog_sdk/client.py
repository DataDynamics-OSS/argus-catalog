# SPDX-License-Identifier: Apache-2.0
"""Argus Catalog 모델 클라이언트.

Argus Catalog 의 ML 모델을 관리하는 Python API 를 제공한다:
  - 모델 목록 조회 / 생성 / 삭제
  - presigned URL 을 통한 모델 파일 업로드/다운로드
  - HuggingFace Hub 에서 임포트
  - 로컬 디렉터리에서 임포트 (에어갭)
  - 모델 파일을 로컬 디렉터리로 내려받기(pull)

사용 예::

    from argus_catalog_sdk import ModelClient

    client = ModelClient("http://catalog-server:4600")

    # 모델 목록
    models = client.list_models()

    # HuggingFace 에서 임포트
    result = client.import_huggingface("bert-base-uncased", "argus.ml.bert")

    # 모델을 로컬로 내려받기
    client.pull("argus.ml.bert", version=1, dest="/tmp/model")

    # 로컬 디렉터리 업로드
    client.push("/path/to/model", "argus.ml.custom", description="My model")
"""

import json
from pathlib import Path

import httpx


class ModelClient:
    """Argus Catalog 모델 레지스트리 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 300.0,
                 token: str | None = None,
                 username: str | None = None, password: str | None = None):
        """클라이언트를 초기화한다.

        Args:
            base_url: 카탈로그 서버 URL (예: "http://localhost:4600")
            timeout: 요청 타임아웃(초). 대용량 업로드를 위해 기본 300초.
            token: 액세스 토큰(Bearer). 모델 등록/삭제 등 변이 작업에 필요.
            username/password: 토큰 대신 자격증명을 주면 생성 시 자동 로그인한다.

        인증 우선순위: token > (username, password 로그인). 둘 다 없으면
        조회는 가능하지만 변이 작업은 서버에서 401 로 거부된다.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = token
        if not self.token and username and password:
            self.token = self._login(username, password)

    def _login(self, username: str, password: str) -> str:
        """username/password 로 로그인해 액세스 토큰을 얻는다."""
        resp = httpx.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def _store_url(self, path: str) -> str:
        return f"{self.base_url}/api/v1/model-store{path}"

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        kwargs.setdefault("timeout", self.timeout)
        # 인증 헤더 자동 첨부 (호출자가 headers 를 줘도 병합)
        headers = {**self._auth_headers(), **(kwargs.pop("headers", None) or {})}
        if headers:
            kwargs["headers"] = headers
        resp = httpx.request(method, url, **kwargs)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(f"API error {resp.status_code}: {detail}")
        return resp

    # -----------------------------------------------------------------
    # 모델 CRUD
    # -----------------------------------------------------------------

    def list_models(
        self, search: str | None = None, page: int = 1, page_size: int = 20,
    ) -> dict:
        """등록된 모델 목록을 조회한다."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        resp = self._request("GET", self._url("/models"), params=params)
        return resp.json()

    def get_model(self, name: str) -> dict:
        """이름으로 등록된 모델을 조회한다."""
        resp = self._request("GET", self._url(f"/models/{name}"))
        return resp.json()

    def create_model(
        self, name: str, description: str | None = None, owner: str | None = None,
    ) -> dict:
        """모델을 등록(생성)한다."""
        payload = {"name": name}
        if description:
            payload["description"] = description
        if owner:
            payload["owner"] = owner
        resp = self._request("POST", self._url("/models"), json=payload)
        return resp.json()

    def delete_model(self, name: str) -> dict:
        """모델을 소프트 삭제한다."""
        resp = self._request("DELETE", self._url(f"/models/{name}"))
        return resp.json()

    def hard_delete_models(self, names: list[str]) -> dict:
        """모델을 영구 삭제한다 (DB + 디스크/S3)."""
        resp = self._request("POST", self._url("/models/hard-delete"), json={"names": names})
        return resp.json()

    # -----------------------------------------------------------------
    # 모델 스토어: 업로드
    # -----------------------------------------------------------------

    def upload_file(
        self, model_name: str, version: int, filepath: str | Path,
    ) -> dict:
        """모델 버전에 단일 파일을 업로드한다."""
        p = Path(filepath)
        with open(p, "rb") as f:
            resp = self._request(
                "POST",
                self._store_url(f"/{model_name}/versions/{version}/upload"),
                files={"file": (p.name, f)},
            )
        return resp.json()

    def get_upload_url(
        self, model_name: str, version: int, filename: str,
    ) -> dict:
        """S3 직접 업로드용 presigned 업로드 URL 을 발급받는다."""
        resp = self._request(
            "POST",
            self._store_url(f"/{model_name}/versions/{version}/upload-url"),
            json={"filename": filename},
        )
        return resp.json()

    def upload_via_presigned(
        self, model_name: str, version: int, filepath: str | Path,
    ) -> dict:
        """presigned URL 로 파일을 업로드한다 (대용량 파일용)."""
        p = Path(filepath)
        url_info = self.get_upload_url(model_name, version, p.name)
        with open(p, "rb") as f:
            resp = httpx.put(url_info["url"], content=f, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Upload failed: {resp.status_code}")
        return {"key": url_info["key"], "status": "uploaded"}

    # -----------------------------------------------------------------
    # 모델 스토어: 다운로드 / Pull
    # -----------------------------------------------------------------

    def get_download_url(
        self, model_name: str, version: int, filename: str,
    ) -> dict:
        """presigned 다운로드 URL 을 발급받는다."""
        resp = self._request(
            "GET",
            self._store_url(f"/{model_name}/versions/{version}/download-url"),
            params={"filename": filename},
        )
        return resp.json()

    def get_download_urls(self, model_name: str, version: int) -> dict:
        """모든 파일에 대한 presigned 다운로드 URL 을 발급받는다."""
        resp = self._request(
            "GET",
            self._store_url(f"/{model_name}/versions/{version}/download-urls"),
        )
        return resp.json()

    def pull(
        self, model_name: str, version: int, dest: str | Path,
    ) -> list[str]:
        """모델의 모든 파일을 로컬 디렉터리로 내려받는다."""
        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)

        urls = self.get_download_urls(model_name, version)
        downloaded = []
        for filename, url in urls.get("files", {}).items():
            resp = httpx.get(url, timeout=self.timeout, follow_redirects=True)
            if resp.status_code >= 400:
                raise RuntimeError(f"Download failed for {filename}: {resp.status_code}")
            file_path = dest_path / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(resp.content)
            downloaded.append(str(file_path))

        return downloaded

    # -----------------------------------------------------------------
    # 모델 스토어: 목록 / Manifest
    # -----------------------------------------------------------------

    def list_files(self, model_name: str, version: int) -> list[dict]:
        """모델 버전의 모든 파일을 조회한다."""
        resp = self._request(
            "GET",
            self._store_url(f"/{model_name}/versions/{version}/files"),
        )
        return resp.json()

    def get_manifest(self, model_name: str, version: int) -> dict:
        """모델 버전의 OCI manifest 를 조회한다."""
        resp = self._request(
            "GET",
            self._store_url(f"/{model_name}/versions/{version}/manifest"),
        )
        return resp.json()

    # -----------------------------------------------------------------
    # 모델 스토어: Finalize
    # -----------------------------------------------------------------

    def finalize(
        self, model_name: str, version: int, annotations: dict | None = None,
    ) -> dict:
        """모델 버전을 확정(finalize)한다 (파일 스캔, manifest 생성)."""
        body = {}
        if annotations:
            body["annotations"] = annotations
        resp = self._request(
            "POST",
            self._store_url(f"/{model_name}/versions/{version}/finalize"),
            json=body if body else None,
        )
        return resp.json()

    # -----------------------------------------------------------------
    # 모델 스토어: 임포트
    # -----------------------------------------------------------------

    def import_huggingface(
        self,
        hf_model_id: str,
        model_name: str,
        revision: str = "main",
        description: str | None = None,
        owner: str | None = None,
    ) -> dict:
        """HuggingFace Hub 에서 모델을 임포트한다."""
        payload = {
            "hf_model_id": hf_model_id,
            "model_name": model_name,
            "revision": revision,
        }
        if description:
            payload["description"] = description
        if owner:
            payload["owner"] = owner
        resp = self._request(
            "POST",
            self._store_url("/import/huggingface"),
            json=payload,
        )
        return resp.json()

    def import_local(
        self,
        local_dir: str | Path,
        model_name: str,
        description: str | None = None,
        owner: str | None = None,
        source: str = "local",
    ) -> dict:
        """로컬 디렉터리에서 모델을 임포트한다 (서버 측 처리).

        해당 디렉터리는 카탈로그 서버에서 접근 가능해야 한다.
        에어갭의 경우: 먼저 파일을 서버로 전송한 뒤 이 메서드를 호출한다.
        """
        payload = {
            "local_dir": str(local_dir),
            "model_name": model_name,
            "source": source,
        }
        if description:
            payload["description"] = description
        if owner:
            payload["owner"] = owner
        resp = self._request(
            "POST",
            self._store_url("/import/local"),
            json=payload,
        )
        return resp.json()

    def push(
        self,
        local_dir: str | Path,
        model_name: str,
        description: str | None = None,
        owner: str | None = None,
        task: str | None = None,
        framework: str | None = None,
        source_type: str = "my",
    ) -> dict:
        """로컬 모델 디렉터리를 OCI Model Hub 로 푸시한다.

        1. OCI 모델이 없으면 생성한다
        2. model-store API 로 모든 파일을 S3 에 업로드한다
        3. 버전을 확정한다 (파일 스캔, manifest 생성, DB 갱신)

        import_local(서버 측)과 달리 클라이언트 측에서 업로드한다.
        클라이언트가 서버 파일시스템에 접근할 수 없는 경우에 동작한다.
        """
        local_path = Path(local_dir)
        if not local_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {local_path}")

        # 필요 시 OCI 모델 생성
        try:
            payload = {"name": model_name, "source_type": source_type}
            if description:
                payload["description"] = description
            if owner:
                payload["owner"] = owner
            if task:
                payload["task"] = task
            if framework:
                payload["framework"] = framework
            self._request("POST", self._url("/oci-models"), json=payload)
        except RuntimeError as e:
            if "409" not in str(e):
                raise

        # 버전 번호 결정
        versions = self._request("GET", self._url(f"/oci-models/{model_name}/versions")).json()
        new_version = max((v["version"] for v in versions), default=0) + 1

        # 파일 수집
        files = []
        for fp in sorted(local_path.rglob("*")):
            if fp.is_file() and not str(fp.relative_to(local_path)).startswith("."):
                files.append(fp)

        # 각 파일을 model-store 로 S3 업로드
        for fp in files:
            self.upload_file(model_name, new_version, fp)

        # README 가 있으면 읽기
        readme = None
        readme_path = local_path / "README.md"
        if readme_path.is_file():
            readme = readme_path.read_text(encoding="utf-8")

        # OCI Hub API 로 확정 (버전 레코드 생성 + 모델 통계 갱신)
        body = {}
        if readme:
            body["readme"] = readme
        resp = self._request(
            "POST",
            self._url(f"/oci-models/{model_name}/versions/{new_version}/finalize"),
            json=body if body else None,
        )
        result = resp.json()

        return {
            "model_name": model_name,
            "version": new_version,
            "file_count": len(files),
            "total_size": result.get("total_size", 0),
            "status": result.get("status", "ready"),
        }
