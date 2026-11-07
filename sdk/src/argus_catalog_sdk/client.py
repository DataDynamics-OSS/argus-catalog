# SPDX-License-Identifier: Apache-2.0
"""Argus Catalog 모델 클라이언트 (``ModelClient``).

카탈로그 서버의 REST API(``/api/v1``)를 감싼 동기 클라이언트다. 다루는 대상은 셋이다.

- **OCI 모델 허브** (``/api/v1/oci-models``): ``push`` 로 로컬 디렉터리를 새 버전으로 올리고,
  ``oci_import_huggingface`` 로 서버가 HuggingFace 에서 받게 한다. 이름 예: ``my-bert``.
- **MLflow 모델 레지스트리** (``/api/v1/models``): 목록·삭제와 서버 측 반입(``import_huggingface``·``import_local``).
  이름 예: ``argus.ml.churn_predictor``. 학습·등록·추론은 MLflow 클라이언트를 그대로 쓴다.
- **AI 에이전트 정의 파일** (``/api/v1/ai-agents``): ``apply_agents``·``prune_agents``·``export_agent``.

모델 파일은 저장소(S3/MinIO)의 ``<모델>/v<버전>/<상대 경로>`` 에 있고 ``/api/v1/model-store`` 로 올리고 받는다.
``pull``·``list_files`` 는 두 저장소 공통이며, MLflow 클라이언트로 기록해 파일이 서버 디스크에 있는 버전은
서버의 파일 브라우저 API 로 대신 찾는다.

인증: 조회는 대부분 익명으로 되지만 내려받기·쓰기는 로그인(``token=`` 또는 ``username=``/``password=``)이 필요하다.
서버 오류는 모두 ``ApiError`` (``status_code``·``detail``)로 올라온다.

사용 예::

    from argus_catalog_sdk import ModelClient

    client = ModelClient("http://catalog-server:4600")

    # 모델 목록
    models = client.list_models()

    # (MLflow 레지스트리) HuggingFace 에서 임포트
    result = client.import_huggingface("bert-base-uncased", "argus.ml.bert")

    # (OCI 모델 허브) HuggingFace 에서 가져오기 — 끝날 때까지 대기
    client.oci_import_huggingface("bert-base-uncased", name="my-bert")

    # 모델을 로컬로 내려받기 (OCI·MLflow 공통)
    client.pull("argus.ml.bert", version=1, dest="/tmp/model")

    # (OCI 모델 허브) 로컬 디렉터리를 새 버전으로 올리기 — 쓰기 작업은 로그인 필요
    client = ModelClient("http://catalog-server:4600", username="me", password="...")
    client.push("/path/to/model", "my-model", description="My model")
"""

import os
from pathlib import Path, PurePosixPath

import httpx

DOWNLOAD_CHUNK = 1024 * 1024  # pull 시 한 번에 디스크로 쓰는 크기(1 MiB)
MLFLOW_FILES_ROOT = "model-artifacts"  # 서버 파일 브라우저에서 MLflow 모델 파일이 있는 루트(root_sub)
# push: 확정 안 된 버전 파일이 이보다(초) 오래됐으면 끊긴 push 의 찌꺼기로 보고 지운다. 저장소 목록에는 업로드가 끝난
# 파일만 보이므로, 큰 파일을 오래 올리는 다른 push 를 찌꺼기로 오인하지 않게 넉넉히(24시간) 잡는다.
STALE_AFTER = 24 * 3600.0


def _older_than(files: list[dict], seconds: float) -> bool:
    """모든 파일의 ``last_modified`` 가 ``seconds`` 초보다 오래됐는지. 시각을 알 수 없으면 False(지우지 않음)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for f in files:
        try:
            ts = datetime.fromisoformat(str(f.get("last_modified") or ""))
        except ValueError:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (now - ts).total_seconds() < seconds:
            return False
    return True


class ApiError(RuntimeError):
    """서버가 4xx/5xx 로 응답함. ``status_code`` 와 ``detail`` (서버 오류 내용)을 담는다.

    기존 호출자와의 호환을 위해 ``RuntimeError`` 를 상속하고, 메시지 형식도 ``API error <코드>: <내용>`` 그대로다.
    """

    def __init__(self, status_code: int, detail):
        """``detail`` 은 서버가 보낸 내용(대개 문자열, 드물게 dict) — 화면에 보여 줄 때는 ``str(e)`` 를 쓴다."""
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error {status_code}: {detail}")


def _error_detail(resp: httpx.Response):
    """오류 응답에서 사람이 읽을 내용을 꺼낸다.

    FastAPI 는 ``{"detail": ...}`` 로 답한다. ``detail`` 이 문자열이면 그대로, 422 검증 오류처럼 목록이면
    ``"필드: 내용; …"`` 으로 합친다(``loc`` 의 ``body`` 는 빼고 ``query.page_size`` 처럼 나머지를 잇는다).
    JSON 이 아니면(프록시 오류 페이지 등) 본문 텍스트를 그대로 돌려준다.
    """
    try:
        body = resp.json()
    except Exception:
        return resp.text
    if not isinstance(body, dict):
        return resp.text
    detail = body.get("detail", resp.text)
    if isinstance(detail, list):  # 422 입력 검증 오류 — "필드: 내용" 으로 읽기 쉽게
        msgs = []
        for d in detail:
            if isinstance(d, dict):
                loc = ".".join(str(x) for x in d.get("loc", []) if x != "body")
                msgs.append(f"{loc}: {d.get('msg', '')}" if loc else str(d.get("msg", d)))
            else:
                msgs.append(str(d))
        return "; ".join(msgs)
    return detail


def _is_hidden(rel: Path) -> bool:
    """상대 경로의 어느 조각이든 ``.`` 으로 시작하면 숨김(``.git/…``, ``sub/.DS_Store`` 등)."""
    return any(part.startswith(".") for part in rel.parts)


def _safe_dest(dest: Path, filename: str) -> Path:
    """서버가 준 파일 이름을 ``dest`` 아래 경로로 바꾼다. 절대 경로·``..`` 등으로 밖을 가리키면 거부한다."""
    rel = PurePosixPath(filename.replace("\\", "/"))
    if not filename or rel.is_absolute() or any(p in ("", ".", "..") for p in filename.replace("\\", "/").split("/")):
        raise ValueError(f"내려받을 파일 이름이 올바르지 않습니다: {filename!r}")
    target = dest / Path(*rel.parts)
    if not target.resolve().is_relative_to(dest.resolve()):
        raise ValueError(f"내려받을 파일이 대상 디렉터리 밖을 가리킵니다: {filename!r}")
    return target  # 호출자가 준 dest 기준 경로(상대 경로면 상대 경로 그대로)


class ModelClient:
    """Argus Catalog 모델 레지스트리 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 300.0,
                 token: str | None = None,
                 username: str | None = None, password: str | None = None,
                 http_client: httpx.Client | None = None):
        """클라이언트를 초기화한다.

        Args:
            base_url: 카탈로그 서버 URL (예: "http://localhost:4600")
            timeout: 요청 타임아웃(초). 대용량 업로드를 위해 기본 300초.
            token: 액세스 토큰(Bearer). 모델 등록/삭제 등 변이 작업에 필요.
            username/password: 토큰 대신 자격증명을 주면 생성 시 자동 로그인한다.

        인증 우선순위: token > (username, password 로그인). 둘 다 없으면
        조회는 가능하지만 변이 작업은 서버에서 401 로 거부된다.

        http_client: 요청에 쓸 ``httpx.Client`` (프록시·인증서·테스트용 transport 등). 없으면 만들어
            연결을 재사용하고, ``close()`` 나 ``with`` 블록이 끝날 때 닫는다. 넘겨받은 클라이언트는 닫지 않는다.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self.token = token
        if not self.token and username and password:
            try:
                self.token = self._login(username, password)
            except BaseException:
                self.close()  # 생성자가 실패하면 호출자가 close() 할 수 없으므로 여기서 닫는다
                raise

    def close(self) -> None:
        """직접 만든 HTTP 연결을 닫는다(넘겨받은 ``http_client`` 는 그대로 둔다)."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "ModelClient":
        """``with ModelClient(...) as c:`` — 블록이 끝나면 ``close()``."""
        return self

    def __exit__(self, *exc) -> None:
        """예외가 나도 연결을 닫는다(예외는 그대로 전파)."""
        self.close()

    def _login(self, username: str, password: str) -> str:
        """username/password 로 로그인해 액세스 토큰을 얻는다."""
        resp = self._http.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, _error_detail(resp))
        return resp.json()["access_token"]

    def _auth_headers(self) -> dict:
        """토큰이 있으면 ``Authorization: Bearer`` 헤더. presigned URL(저장소 직접 접근)에는 붙이지 않는다."""
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _url(self, path: str) -> str:
        """``/api/v1`` 아래 경로의 전체 URL. ``base_url`` 은 서버 주소나 웹 UI 주소(``/api/v1`` 을 서버로 넘겨 줌)."""
        return f"{self.base_url}/api/v1{path}"

    def _store_url(self, path: str) -> str:
        """모델 파일 API(``/api/v1/model-store``) 아래 경로의 전체 URL."""
        return f"{self.base_url}/api/v1/model-store{path}"

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """인증 헤더를 붙여 요청하고, 4xx/5xx 면 ``ApiError`` 를 낸다. 나머지 인자는 ``httpx.Client.request`` 로."""
        kwargs.setdefault("timeout", self.timeout)
        # 인증 헤더 자동 첨부 (호출자가 headers 를 줘도 병합)
        headers = {**self._auth_headers(), **(kwargs.pop("headers", None) or {})}
        if headers:
            kwargs["headers"] = headers
        resp = self._http.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, _error_detail(resp))
        return resp

    # -----------------------------------------------------------------
    # 모델 CRUD
    # -----------------------------------------------------------------

    def list_models(
        self, search: str | None = None, page: int = 1, page_size: int = 20,
    ) -> dict:
        """MLflow 등록 모델 목록 — ``{"items": [...], "total": n, ...}``. ``page_size`` 는 서버에서 최대 100.

        항목의 ``max_version_number``·``latest_version_status`` 로 최신 버전과 상태를 볼 수 있다.
        OCI 모델 허브 목록은 ``oci_list_models``."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        resp = self._request("GET", self._url("/models"), params=params)
        return resp.json()

    def get_model(self, name: str) -> dict:
        """MLflow 등록 모델 상세. 없거나 소프트 삭제된 모델이면 ``ApiError(404)``."""
        resp = self._request("GET", self._url(f"/models/{name}"))
        return resp.json()

    def create_model(
        self, name: str, description: str | None = None, owner: str | None = None,
    ) -> dict:
        """MLflow 등록 모델을 만든다(버전 없이). 같은 이름이 있으면 ``ApiError(409)``.

        파일까지 올리는 새 모델은 OCI 모델 허브의 ``push`` 를 권한다."""
        payload = {"name": name}
        if description:
            payload["description"] = description
        if owner:
            payload["owner"] = owner
        resp = self._request("POST", self._url("/models"), json=payload)
        return resp.json()

    def delete_model(self, name: str) -> dict:
        """MLflow 등록 모델을 소프트 삭제한다 — 목록에서 숨겨지고 파일은 남는다(웹 화면에서 복원 가능).

        파일까지 완전히 지우려면 ``hard_delete_models``(관리자)."""
        resp = self._request("DELETE", self._url(f"/models/{name}"))
        return resp.json()

    def hard_delete_models(self, names: list[str]) -> dict:
        """MLflow 등록 모델을 영구 삭제한다 — DB 기록과 파일(서버 디스크·저장소)을 함께 지운다. 관리자.

        결과는 ``{"deleted": [이름...], "not_found": [이름...]}`` — 없는 이름은 오류 대신 ``not_found`` 로 알려 준다."""
        resp = self._request("POST", self._url("/models/hard-delete"), json={"names": names})
        return resp.json()

    # -----------------------------------------------------------------
    # OCI 모델 허브 (/api/v1/oci-models) — 위의 list_models/create_model 등은 MLflow 레지스트리
    # -----------------------------------------------------------------

    def oci_list_models(
        self, search: str | None = None, task: str | None = None, framework: str | None = None,
        status: str | None = None, page: int = 1, page_size: int = 12,
    ) -> dict:
        """OCI 모델 허브 목록 — ``{"items": [...], "total": n}``. ``page_size`` 는 서버에서 최대 50.

        ``items[].version_count`` 가 최신 버전 번호, ``status`` 는 모델의 검토 상태(review·approved·production 등)다."""
        params = {"page": page, "page_size": page_size}
        for k, v in (("search", search), ("task", task), ("framework", framework), ("status", status)):
            if v:
                params[k] = v
        return self._request("GET", self._url("/oci-models"), params=params).json()

    def oci_get_model(self, name: str) -> dict:
        """OCI 모델 상세 (README·태그·리니지 포함). 없으면 ``ApiError(404)``."""
        return self._request("GET", self._url(f"/oci-models/{name}")).json()

    def oci_list_versions(self, name: str) -> list[dict]:
        """OCI 모델 버전 목록 — 확정됐거나 가져오는 중인 버전만(push 가 확정 전에 끊긴 번호는 없음).

        각 버전의 ``status`` 는 ready / importing / failed, 실패 사유는 ``extra_metadata.error``."""
        return self._request("GET", self._url(f"/oci-models/{name}/versions")).json()

    def oci_import_huggingface(
        self, hf_model_id: str, name: str | None = None, revision: str = "main",
        description: str | None = None, owner: str | None = None,
        task: str | None = None, framework: str | None = None, language: str | None = None,
        wait: bool = True, poll_interval: float = 5.0, wait_timeout: float = 3600.0,
    ) -> dict:
        """HuggingFace 모델을 OCI 허브로 가져온다 (관리자 토큰 필요).

        서버가 저장소·revision 을 먼저 확인해(없으면 404/400, HF 에 닿지 않으면 502) 접수하고,
        ``status=importing`` 으로 바로 응답한 뒤 백그라운드에서 받는다. ``name`` 을 빼면 ``hf_model_id`` 의
        ``/`` 를 ``-`` 로 바꾼 이름, 모델이 이미 있으면 다음 버전으로 추가된다.

        Args:
            wait: True(기본)면 ``poll_interval`` 초마다 버전 상태를 보며 ready 가 될 때까지 기다린다.
                False 면 접수 응답(``{"name", "version", "status": "importing"}``)을 바로 돌려준다.
            wait_timeout: 기다리는 최대 시간(초). 넘으면 ``TimeoutError`` — 서버의 가져오기는 계속된다.

        Returns:
            ready 가 된 버전 정보에 ``name`` 을 더한 dict(``version``·``file_count``·``total_size`` 등).

        Raises:
            RuntimeError: 가져오기가 failed 로 끝났거나, 기다리는 중에 모델·버전이 삭제됨.
        """
        import time

        payload = {"hf_model_id": hf_model_id, "revision": revision}
        for k, v in (("name", name), ("description", description), ("owner", owner),
                     ("task", task), ("framework", framework), ("language", language)):
            if v:
                payload[k] = v
        accepted = self._request("POST", self._url("/oci-models/import/huggingface"), json=payload).json()
        if not wait:
            return accepted
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            try:
                versions = self.oci_list_versions(accepted["name"])
            except ApiError as e:
                if e.status_code == 404:
                    raise RuntimeError(f"가져오는 중에 모델 '{accepted['name']}' 이(가) 삭제됐습니다.") from e
                raise
            v = next((v for v in versions if v["version"] == accepted["version"]), None)
            if v is None:
                raise RuntimeError(f"가져오는 중에 {accepted['name']} v{accepted['version']} 버전이 사라졌습니다"
                                   " (삭제됐거나 가져오기가 취소됨).")
            if v["status"] != "importing":
                if v["status"] == "failed":
                    err = (v.get("extra_metadata") or {}).get("error", "알 수 없는 오류")
                    raise RuntimeError(f"가져오기 실패: {err}")
                return {"name": accepted["name"], **v}
            time.sleep(poll_interval)
        raise TimeoutError(f"{wait_timeout}초 안에 가져오기가 끝나지 않았습니다 — 웹 UI 의 버전 탭에서 확인하세요.")

    def oci_delete_model(self, name: str) -> dict:
        """OCI 모델 삭제 (DB + S3 파일). 소유자·생성자·관리자."""
        return self._request("DELETE", self._url(f"/oci-models/{name}")).json()

    # -----------------------------------------------------------------
    # 모델 스토어: 업로드
    # -----------------------------------------------------------------

    def upload_file(
        self, model_name: str, version: int, filepath: str | Path, filename: str | None = None,
    ) -> dict:
        """모델 버전에 단일 파일을 업로드한다.

        ``filename`` 은 버전 디렉터리 안의 저장 경로(예: ``onnx/model.onnx``)로, 없으면 로컬 파일 이름을 쓴다.
        """
        p = Path(filepath)
        with open(p, "rb") as f:
            resp = self._request(
                "POST",
                self._store_url(f"/{model_name}/versions/{version}/upload"),
                files={"file": (filename or p.name, f)},
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
        self, model_name: str, version: int, filepath: str | Path, filename: str | None = None,
    ) -> dict:
        """presigned URL 로 파일을 저장소(S3)에 직접 업로드한다 (대용량 파일용 — 서버를 거치지 않음).

        ``filename`` 은 버전 디렉터리 안의 저장 경로로, 없으면 로컬 파일 이름을 쓴다.
        presigned URL 은 서버의 외부용 저장소 주소로 발급되므로 클라이언트가 그 주소에 닿아야 한다.
        """
        p = Path(filepath)
        url_info = self.get_upload_url(model_name, version, filename or p.name)
        with open(p, "rb") as f:
            # 인증 헤더는 저장소로 보내지 않는다(서명이 URL 에 들어 있음)
            resp = self._http.put(url_info["url"], content=f, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"저장소 업로드 실패 {filename or p.name}: HTTP {resp.status_code}")
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
        """모델의 모든 파일을 로컬 디렉터리로 내려받는다(하위 경로 유지).

        파일은 조각 단위로 디스크에 바로 쓰므로 대용량도 메모리에 다 올리지 않는다. 받는 동안은
        ``<파일>.part`` 에 쓰고 끝나면 이름을 바꾼다. 서버가 준 이름이 ``dest`` 밖을 가리키면 ``ValueError``.

        MLflow 클라이언트로 기록한 모델처럼 파일이 저장소(S3)가 아니라 **서버 디스크**에 있으면,
        서버의 MLflow 모델 파일 브라우저 API 로 같은 방식(하위 경로 유지)으로 받는다.
        """
        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)

        try:
            urls = self.get_download_urls(model_name, version).get("files", {})
        except ApiError as e:
            if e.status_code != 404:
                raise
            server_files = self._server_disk_files(model_name, version)
            if not server_files:
                raise
            targets = [(_safe_dest(dest_path, f["filename"]), f["filename"], self._url("/filesystem/download"),
                        {"path": f["server_path"], "root_sub": MLFLOW_FILES_ROOT}) for f in server_files]
            return self._download_all(targets, auth=True)

        # 받기 전에 모든 이름을 먼저 검사한다 — 하나라도 이상하면 아무것도 쓰지 않는다
        targets = [(_safe_dest(dest_path, filename), filename, url, None) for filename, url in urls.items()]
        return self._download_all(targets, auth=False)

    def _download_all(self, targets: list[tuple], auth: bool) -> list[str]:
        """``(저장 경로, 이름, URL, 쿼리)`` 목록을 차례로 스트리밍해 받는다. presigned URL(저장소)에는 토큰을 보내지 않는다."""
        headers = self._auth_headers() if auth else None
        downloaded = []
        for file_path, filename, url, params in targets:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            part = file_path.with_name(file_path.name + ".part")
            try:
                with self._http.stream("GET", url, params=params, headers=headers, timeout=self.timeout,
                                       follow_redirects=True) as resp:
                    if resp.status_code >= 400:
                        raise RuntimeError(f"다운로드 실패 {filename}: HTTP {resp.status_code}")
                    with open(part, "wb") as out:
                        for chunk in resp.iter_bytes(DOWNLOAD_CHUNK):
                            out.write(chunk)
                os.replace(part, file_path)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            downloaded.append(str(file_path))
        return downloaded

    # -----------------------------------------------------------------
    # 모델 스토어: 목록 / Manifest
    # -----------------------------------------------------------------

    def _store_files(self, model_name: str, version: int) -> list[dict]:
        """저장소(S3)의 ``<모델>/v<버전>/`` 아래 파일 목록 — 서버 디스크는 보지 않는다.

        ``push`` 가 확정 안 된 남은 파일을 찾을 때와 ``list_files`` 의 첫 단계에서 쓴다. 각 항목은
        ``filename``·``size``·``last_modified``(ISO 8601)."""
        return self._request("GET", self._store_url(f"/{model_name}/versions/{version}/files")).json()

    def _server_disk_files(self, model_name: str, version: int) -> list[dict]:
        """서버 디스크에 있는 MLflow 모델 버전 파일 — ``{filename, size, last_modified, server_path}`` 목록.

        MLflow 모델 버전이 아니거나(OCI 모델 등) 파일 브라우저 밖 저장소면 빈 목록. 로그인이 필요하다."""
        need_login = "서버 디스크에 저장된 MLflow 모델 파일은 로그인해야 볼 수 있습니다."
        try:
            ver = self._request("GET", self._url(f"/models/{model_name}/versions/{version}")).json()
        except ApiError as e:
            if e.status_code == 404:
                return []
            if e.status_code == 401:
                raise ApiError(401, need_login) from e
            raise
        root = ver.get("files_path")
        if not root:
            return []
        out: list[dict] = []
        pending = [root.rstrip("/") or "/"]
        while pending:
            cur = pending.pop()
            try:
                listing = self._request("GET", self._url("/filesystem/list"),
                                        params={"path": cur, "root_sub": MLFLOW_FILES_ROOT}).json()
            except ApiError as e:
                if e.status_code == 401:
                    raise ApiError(401, need_login) from e
                if e.status_code == 404:  # 버전 디렉터리가 없음(파일이 지워짐)
                    continue
                raise
            for d in listing.get("folders", []):
                pending.append(f"{cur.rstrip('/')}/{d['name']}")
            for f in listing.get("files", []):
                server_path = f"{cur.rstrip('/')}/{f['name']}"
                out.append({"filename": server_path[len(root.rstrip('/')) + 1:], "size": f.get("size") or 0,
                            "last_modified": f.get("last_modified") or "", "server_path": server_path})
        return sorted(out, key=lambda f: f["filename"])

    def list_files(self, model_name: str, version: int) -> list[dict]:
        """모델 버전의 모든 파일을 조회한다(``filename``·``size``·``last_modified``).

        저장소(S3)에 없으면 서버 디스크의 MLflow 모델 파일(MLflow 클라이언트로 기록한 모델)을 찾아 본다."""
        files = self._store_files(model_name, version)
        if files:
            return files
        return [{k: f[k] for k in ("filename", "size", "last_modified")}
                for f in self._server_disk_files(model_name, version)]

    def delete_unfinalized_files(self, model_name: str, version: int) -> int:
        """확정되지 않은 버전의 저장소 파일을 지운다(끊긴 push 정리). 지운 개수를 돌려준다.

        그 번호에 버전 기록이 있으면 서버가 409 로 거부한다. 소유자·생성자·관리자."""
        return self._request("DELETE", self._store_url(f"/{model_name}/versions/{version}/files")).json()["deleted"]

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
        """모델 버전을 확정(finalize)한다 (파일 스캔, manifest 생성, DB 갱신).

        대상은 MLflow 등록 모델의 이미 만든 버전이다(없으면 404). 버전이 READY 로 바뀌고
        파일 수·크기·메타데이터가 기록된다. OCI 허브 모델은 ``push()`` 를 사용한다.
        """
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
        """HuggingFace 모델을 MLflow 레지스트리로 반입한다 (관리자, 서버가 받는 동안 응답을 기다림).

        모델이 없으면 만들고 새 버전을 추가한다. 서버가 저장소·revision 을 먼저 확인하므로 없는 모델은
        모델·버전을 만들기 전에 404/400 으로 끝난다. 받는 도중 실패하면 그 버전은 ``FAILED_REGISTRATION``
        으로 남고 500(저장소 장애면 503)이 난다.

        Returns:
            ``{"model_name", "version", "file_count", "total_size", "storage_location"}``
        """
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
        """**카탈로그 서버의** 디렉터리를 MLflow 레지스트리로 반입한다 (관리자, 서버 측 처리).

        ``local_dir`` 는 이 코드를 실행하는 PC 가 아니라 서버(컨테이너) 안의 경로다. 에어갭에서는 파일을 서버로
        먼저 옮긴 뒤 호출한다. 클라이언트에 있는 파일이면 ``push`` (OCI 모델 허브)를 쓴다.

        경로가 없거나 숨김 파일만 있으면 모델·버전을 만들기 전에 ``ApiError(400)``. 도중에 실패하면 그 버전은
        ``FAILED_REGISTRATION`` 으로 남는다. ``source`` 는 카탈로그에 기록되는 반입 출처 표시다.

        Returns:
            ``{"model_name", "version", "file_count", "total_size", "storage_location"}``
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
        presigned_min_size: int | None = None,
        stale_after: float = STALE_AFTER,
    ) -> dict:
        """로컬 모델 디렉터리를 OCI Model Hub 로 푸시한다.

        1. OCI 모델이 없으면 생성한다
        2. model-store API 로 모든 파일을 S3 에 업로드한다
        3. 버전을 확정한다 (파일 스캔, manifest 생성, DB 갱신)

        import_local(서버 측)과 달리 클라이언트 측에서 업로드한다.
        클라이언트가 서버 파일시스템에 접근할 수 없는 경우에 동작한다.

        - 하위 디렉터리 구조는 그대로 유지된다(``onnx/model.onnx`` → 버전 안의 ``onnx/model.onnx``).
        - 숨김 경로(어느 단계든 ``.`` 으로 시작하는 조각: ``.git/``, ``sub/.DS_Store``)는 올리지 않는다.
        - 이전 push 가 확정 전에 끊겨 저장소에 파일만 남은 버전 번호는 — 남은 파일이 ``stale_after`` 초(기본 24시간)보다
          오래됐으면 끊긴 push 의 찌꺼기로 보고 **지운 뒤 그 번호를 다시 쓰고**, 최근 것이면(다른 push 가 올리는 중일 수
          있음) 건너뛰고 다음 번호를 쓴다. 어느 쪽이든 남은 파일이 새 버전에 섞이지 않는다. 지운 번호는 결과의
          ``cleaned_versions`` 에 담긴다.
        - ``presigned_min_size`` (바이트)를 주면 그 크기 이상인 파일은 서버를 거치지 않고 presigned URL 로
          저장소에 직접 올린다(서버가 업로드 파일을 메모리에 다 읽으므로 대용량은 이쪽이 안전). 기본은 모두 서버 경유.
          클라이언트가 서버의 외부용 저장소 주소에 닿아야 한다.

        Returns:
            ``{"model_name", "version", "file_count", "total_size", "status", "cleaned_versions"}``

        Raises:
            FileNotFoundError: ``local_dir`` 가 디렉터리가 아님.
            ValueError: 올릴 파일이 없음(숨김 파일만 있음) — 서버에 아무것도 만들지 않는다.
            ApiError: 409 = 같은 이름의 MLflow 모델이 있음, 403 = 남의 OCI 모델, 401 = 로그인 안 함.
        """
        local_path = Path(local_dir)
        if not local_path.is_dir():
            raise FileNotFoundError(f"디렉터리가 없습니다: {local_path}")

        # 파일 수집 — 업로드 전에 먼저 해서 빈 디렉터리면 서버에 아무것도 만들지 않는다
        files = [fp for fp in sorted(local_path.rglob("*"))
                 if fp.is_file() and not _is_hidden(fp.relative_to(local_path))]
        if not files:
            raise ValueError(f"올릴 파일이 없습니다: {local_path}")

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
        except ApiError as e:
            if e.status_code != 409:
                raise
            # 409 는 같은 이름의 OCI 모델이 있을 때뿐 아니라 MLflow 모델과 이름이 겹칠 때도 난다
            try:
                self.oci_get_model(model_name)
            except ApiError as e2:
                if e2.status_code == 404:
                    raise ApiError(409, f"'{model_name}' 은(는) OCI 모델이 아닌 다른 모델(MLflow 등)이 이미 쓰는 "
                                        f"이름입니다 — 다른 이름으로 push 하세요. (서버: {e.detail})") from e
                raise

        # 버전 번호 결정 — DB 의 최신 버전 다음 번호부터. 확정 안 된 파일이 남은 번호는 오래됐으면 정리해 다시 쓰고,
        # 최근 것이면(진행 중인 다른 push) 건너뛴다
        versions = self.oci_list_versions(model_name)
        new_version = max((v["version"] for v in versions), default=0) + 1
        cleaned: list[int] = []
        while left := self._store_files(model_name, new_version):
            if _older_than(left, stale_after):
                try:
                    self.delete_unfinalized_files(model_name, new_version)
                    cleaned.append(new_version)
                    break
                except ApiError as e:
                    if e.status_code not in (404, 405, 409):  # 404·405: 정리 API 가 없는 이전 서버
                        raise
            new_version += 1

        # 각 파일을 S3 로 업로드 (버전 안의 상대 경로 유지) — 큰 파일은 presigned 로 직접
        for fp in files:
            rel = fp.relative_to(local_path).as_posix()
            if presigned_min_size is not None and fp.stat().st_size >= presigned_min_size:
                self.upload_via_presigned(model_name, new_version, fp, filename=rel)
            else:
                self.upload_file(model_name, new_version, fp, filename=rel)

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
            "cleaned_versions": cleaned,
        }

    # -----------------------------------------------------------------
    # AI 에이전트 정의 파일 (agent.yaml) — design/ai-agent-bulk-registration-design.md
    # -----------------------------------------------------------------

    def apply_agents(
        self, docs: list[dict], dry_run: bool = False, source_ref: str | None = None,
    ) -> dict:
        """정의 문서 목록을 반영한다(최대 200개, 에이전트별 독립 처리). 결과 ``{dry_run, summary, results}``."""
        params: dict = {"dry_run": str(dry_run).lower()}
        if source_ref:
            params["source_ref"] = source_ref
        resp = self._request("POST", self._url("/ai-agents/apply"), params=params, json={"agents": docs})
        return resp.json()

    def prune_agents(self, source_prefix: str, keep: list[str], dry_run: bool = False) -> dict:
        """``source_prefix`` 로 관리되는 에이전트 중 ``keep`` 에 없는 것을 사용 중단(deprecated)으로 바꾼다."""
        resp = self._request("POST", self._url("/ai-agents/prune"),
                             json={"source_prefix": source_prefix, "keep": keep, "dry_run": dry_run})
        return resp.json()

    def export_agent(self, name: str) -> str:
        """에이전트의 현재 내용을 정의 파일(YAML 원문)로 내보낸다."""
        return self._request("GET", self._url(f"/ai-agents/{name}/manifest")).text

    def list_agents(
        self, search: str | None = None, status: str | None = None, category: str | None = None,
        page: int = 1, page_size: int = 100,
    ) -> dict:
        """AI 에이전트 목록(요약) — ``{"items": [...], "total": n}``. ``page_size`` 는 서버에서 최대 100.

        부서 조건은 서버가 받지 않으므로 ``agents.select_agents`` 가 받은 목록에서 거른다."""
        params: dict = {"page": page, "page_size": page_size}
        for k, v in (("search", search), ("status", status), ("category", category)):
            if v:
                params[k] = v
        return self._request("GET", self._url("/ai-agents"), params=params).json()
