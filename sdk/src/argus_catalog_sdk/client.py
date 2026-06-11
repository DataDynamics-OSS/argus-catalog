"""Argus Catalog Model Client.

Provides Python API for managing ML models in Argus Catalog:
  - List, create, delete models
  - Upload/download model files via presigned URLs
  - Import from HuggingFace Hub
  - Import from local directory (airgap)
  - Pull model files to local directory

Usage::

    from argus_catalog_sdk import ModelClient

    client = ModelClient("http://catalog-server:4600")

    # List models
    models = client.list_models()

    # Import from HuggingFace
    result = client.import_huggingface("bert-base-uncased", "argus.ml.bert")

    # Pull model to local
    client.pull("argus.ml.bert", version=1, dest="/tmp/model")

    # Push local directory
    client.push("/path/to/model", "argus.ml.custom", description="My model")
"""

import json
from pathlib import Path

import httpx


class ModelClient:
    """Client for Argus Catalog Model Registry."""

    def __init__(self, base_url: str, timeout: float = 300.0,
                 token: str | None = None,
                 username: str | None = None, password: str | None = None):
        """Initialize the client.

        Args:
            base_url: Catalog server URL (e.g. "http://localhost:4600")
            timeout: Request timeout in seconds (default 300s for large uploads)
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
    # Model CRUD
    # -----------------------------------------------------------------

    def list_models(
        self, search: str | None = None, page: int = 1, page_size: int = 20,
    ) -> dict:
        """List registered models."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        resp = self._request("GET", self._url("/models"), params=params)
        return resp.json()

    def get_model(self, name: str) -> dict:
        """Get a registered model by name."""
        resp = self._request("GET", self._url(f"/models/{name}"))
        return resp.json()

    def create_model(
        self, name: str, description: str | None = None, owner: str | None = None,
    ) -> dict:
        """Create a registered model."""
        payload = {"name": name}
        if description:
            payload["description"] = description
        if owner:
            payload["owner"] = owner
        resp = self._request("POST", self._url("/models"), json=payload)
        return resp.json()

    def delete_model(self, name: str) -> dict:
        """Soft-delete a model."""
        resp = self._request("DELETE", self._url(f"/models/{name}"))
        return resp.json()

    def hard_delete_models(self, names: list[str]) -> dict:
        """Permanently delete models (DB + disk/S3)."""
        resp = self._request("POST", self._url("/models/hard-delete"), json={"names": names})
        return resp.json()

    # -----------------------------------------------------------------
    # Model Store: Upload
    # -----------------------------------------------------------------

    def upload_file(
        self, model_name: str, version: int, filepath: str | Path,
    ) -> dict:
        """Upload a single file to a model version."""
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
        """Get a presigned upload URL for direct S3 upload."""
        resp = self._request(
            "POST",
            self._store_url(f"/{model_name}/versions/{version}/upload-url"),
            json={"filename": filename},
        )
        return resp.json()

    def upload_via_presigned(
        self, model_name: str, version: int, filepath: str | Path,
    ) -> dict:
        """Upload a file using presigned URL (for large files)."""
        p = Path(filepath)
        url_info = self.get_upload_url(model_name, version, p.name)
        with open(p, "rb") as f:
            resp = httpx.put(url_info["url"], content=f, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Upload failed: {resp.status_code}")
        return {"key": url_info["key"], "status": "uploaded"}

    # -----------------------------------------------------------------
    # Model Store: Download / Pull
    # -----------------------------------------------------------------

    def get_download_url(
        self, model_name: str, version: int, filename: str,
    ) -> dict:
        """Get a presigned download URL."""
        resp = self._request(
            "GET",
            self._store_url(f"/{model_name}/versions/{version}/download-url"),
            params={"filename": filename},
        )
        return resp.json()

    def get_download_urls(self, model_name: str, version: int) -> dict:
        """Get presigned download URLs for all files."""
        resp = self._request(
            "GET",
            self._store_url(f"/{model_name}/versions/{version}/download-urls"),
        )
        return resp.json()

    def pull(
        self, model_name: str, version: int, dest: str | Path,
    ) -> list[str]:
        """Download all model files to a local directory."""
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
    # Model Store: List / Manifest
    # -----------------------------------------------------------------

    def list_files(self, model_name: str, version: int) -> list[dict]:
        """List all files for a model version."""
        resp = self._request(
            "GET",
            self._store_url(f"/{model_name}/versions/{version}/files"),
        )
        return resp.json()

    def get_manifest(self, model_name: str, version: int) -> dict:
        """Get OCI manifest for a model version."""
        resp = self._request(
            "GET",
            self._store_url(f"/{model_name}/versions/{version}/manifest"),
        )
        return resp.json()

    # -----------------------------------------------------------------
    # Model Store: Finalize
    # -----------------------------------------------------------------

    def finalize(
        self, model_name: str, version: int, annotations: dict | None = None,
    ) -> dict:
        """Finalize a model version (scan files, generate manifest)."""
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
    # Model Store: Import
    # -----------------------------------------------------------------

    def import_huggingface(
        self,
        hf_model_id: str,
        model_name: str,
        revision: str = "main",
        description: str | None = None,
        owner: str | None = None,
    ) -> dict:
        """Import a model from HuggingFace Hub."""
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
        """Import model from a local directory (server-side).

        The directory must be accessible to the catalog server.
        For airgap: transfer files to server first, then call this.
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
        """Push a local model directory to the OCI Model Hub.

        1. Creates the OCI model if it doesn't exist
        2. Uploads all files to S3 via model-store API
        3. Finalizes the version (scan files, generate manifest, update DB)

        Unlike import_local (server-side), this uploads from the client side.
        Works when the client cannot access the server's filesystem.
        """
        local_path = Path(local_dir)
        if not local_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {local_path}")

        # Create OCI model if needed
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

        # Determine version number
        versions = self._request("GET", self._url(f"/oci-models/{model_name}/versions")).json()
        new_version = max((v["version"] for v in versions), default=0) + 1

        # Collect files
        files = []
        for fp in sorted(local_path.rglob("*")):
            if fp.is_file() and not str(fp.relative_to(local_path)).startswith("."):
                files.append(fp)

        # Upload each file to S3 via model-store
        for fp in files:
            self.upload_file(model_name, new_version, fp)

        # Read README if present
        readme = None
        readme_path = local_path / "README.md"
        if readme_path.is_file():
            readme = readme_path.read_text(encoding="utf-8")

        # Finalize via OCI Hub API (creates version record + updates model stats)
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
