# SPDX-License-Identifier: Apache-2.0
"""모델 아티팩트 스토어 — S3/MinIO 백엔드 + OCI 매니페스트 지원.

주요 책임:
  - 모델 파일을 S3 로 업로드 (직접 또는 presigned URL 경유)
  - 모델 파일 다운로드 (presigned URL 발급)
  - OCI 호환 ``manifest.json`` 생성
  - HuggingFace Hub·로컬 디렉터리로부터 모델 가져오기 (import-hf / import-local)
  - 파일별 SHA256 다이제스트 계산(객체 메타데이터 ``sha256`` 에 기록, manifest 레이어에 사용)

저장 키 규칙: ``{model_name}/v{version}/{filename}`` (content-addressable 아님 — 같은 키에 덮어쓴다).

로깅 정책: 업로드/다운로드/임포트/매니페스트 생성·삭제 동작은 INFO 로 모델·버전·
파일 수 정보를 기록하고, 조회·URL 발급 같은 세부는 DEBUG 로 남긴다. S3 호출 실패는
대부분 예외로 호출 측에 올리고, 부가 파일 파싱 실패 등 복구 가능한 오류만 WARNING 으로 남긴다.
"""

import datetime as _dt
import hashlib
import json
import logging
import tempfile
from pathlib import Path

from app.core.config import settings
from app.core.s3 import ensure_bucket, get_s3_client, presigned_url

logger = logging.getLogger(__name__)


# =========================================================================== #
# 헬퍼
# =========================================================================== #


def _s3_prefix(model_name: str, version: int) -> str:
    """모델 버전에 대응되는 S3 key 프리픽스(``{name}/v{ver}/``) 를 생성한다."""
    return f"{model_name}/v{version}/"


def safe_filename(filename: str) -> str:
    """버전 디렉터리 안의 상대 파일 경로인지 검증한다(``/`` 로 시작·``..``·빈 조각 금지).

    S3 키는 문자열이라 ``../`` 로 다른 모델 프리픽스를 가리키는 키를 만들 수 있으므로 막는다."""
    parts = (filename or "").replace("\\", "/").split("/")
    if not filename or filename.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"잘못된 파일 이름입니다: '{filename}' (버전 디렉터리 안의 상대 경로만 허용)")
    return "/".join(parts)


def _sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    """대용량 파일도 메모리에 다 올리지 않고 SHA256 을 계산한다."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


#: ``delete_objects`` 한 번에 지울 수 있는 최대 키 수(S3 API 제한)
_DELETE_BATCH = 1000


async def _list_all_objects(s3, bucket: str, prefix: str) -> list[dict]:
    """``list_objects_v2`` 를 ``ContinuationToken`` 으로 끝까지 따라가 프리픽스 하위 객체를 모두 모은다.

    한 페이지(최대 1000개)만 보면 큰 모델 버전의 파일이 목록·매니페스트·삭제에서 빠지므로 전부 순회한다.
    """
    objects: list[dict] = []
    continuation = None
    while True:
        params = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            params["ContinuationToken"] = continuation
        resp = await s3.list_objects_v2(**params)
        objects.extend(resp.get("Contents", []))
        if not resp.get("IsTruncated"):
            break
        continuation = resp.get("NextContinuationToken")
        if not continuation:
            break
    return objects


async def _delete_keys(s3, bucket: str, objects: list[dict]) -> None:
    """객체 목록을 ``delete_objects`` 제한(1000개) 단위로 나눠 지운다."""
    for i in range(0, len(objects), _DELETE_BATCH):
        batch = objects[i:i + _DELETE_BATCH]
        await s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": obj["Key"]} for obj in batch]},
        )


def manifest_digest(manifest: dict) -> str:
    """S3 에 저장되는 ``manifest.json`` 바이트(들여쓰기 2) 의 ``sha256:`` 다이제스트."""
    return "sha256:" + hashlib.sha256(json.dumps(manifest, indent=2).encode()).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    """바이트열의 SHA256 다이제스트(hex) 를 반환한다."""
    return hashlib.sha256(data).hexdigest()


def _media_type_for_file(filename: str) -> str:
    """파일명을 OCI media type 으로 매핑(weights / config / tokenizer 등)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mapping = {
        "pkl": "application/vnd.argus.model.weights",
        "bin": "application/vnd.argus.model.weights",
        "safetensors": "application/vnd.argus.model.weights.safetensors",
        "pt": "application/vnd.argus.model.weights.pytorch",
        "onnx": "application/vnd.argus.model.weights.onnx",
        "json": "application/vnd.argus.model.config+json",
        "yaml": "application/vnd.argus.model.config+yaml",
        "yml": "application/vnd.argus.model.config+yaml",
        "txt": "application/vnd.argus.model.metadata+text",
        "md": "application/vnd.argus.model.metadata+text",
    }
    # 특수 파일명
    name_mapping = {
        "MLmodel": "application/vnd.argus.model.mlflow.mlmodel+yaml",
        "conda.yaml": "application/vnd.argus.model.mlflow.conda+yaml",
        "python_env.yaml": "application/vnd.argus.model.mlflow.python-env+yaml",
        "requirements.txt": "application/vnd.argus.model.mlflow.requirements+text",
        "config.json": "application/vnd.argus.model.config+json",
        "tokenizer.json": "application/vnd.argus.model.tokenizer+json",
        "tokenizer_config.json": "application/vnd.argus.model.tokenizer-config+json",
    }
    return name_mapping.get(filename, mapping.get(ext, "application/octet-stream"))


# =========================================================================== #
# 1. S3 로 파일 업로드
# =========================================================================== #


async def upload_file(
    model_name: str,
    version: int,
    filename: str,
    data: bytes,
    bucket: str | None = None,
) -> dict:
    """단일 파일을 ``{name}/v{ver}/{filename}`` 키로 S3 에 업로드(같은 키는 덮어씀).

    파일 이름이 안전한 상대 경로가 아니면 ``ValueError``. 객체 메타데이터에 ``sha256`` 을 기록한다.
    반환: ``{"key", "size", "sha256"}``.
    """
    bucket = bucket or settings.os_bucket
    await ensure_bucket(bucket)
    key = _s3_prefix(model_name, version) + safe_filename(filename)
    digest = _sha256_bytes(data)

    async with get_s3_client() as s3:
        await s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            Metadata={"sha256": digest},
        )

    logger.info("업로드 완료: s3://%s/%s (%d bytes, sha256=%s)", bucket, key, len(data), digest[:12])
    return {"key": key, "size": len(data), "sha256": digest}


async def upload_files(
    model_name: str,
    version: int,
    files: list[tuple[str, bytes]],
    bucket: str | None = None,
) -> list[dict]:
    """여러 파일을 순차 업로드. 각 항목은 ``(filename, bytes)`` 튜플. 중간 실패 시 앞서 올린 파일은 남는다."""
    results = []
    for filename, data in files:
        result = await upload_file(model_name, version, filename, data, bucket)
        results.append(result)
    return results


# =========================================================================== #
# 2. Presigned URL
# =========================================================================== #


async def generate_upload_url(
    model_name: str,
    version: int,
    filename: str,
    bucket: str | None = None,
) -> dict:
    """클라이언트 직접 업로드용 presigned PUT URL 발급.

    서명은 저장소에 묻지 않고 만들어지므로, 버킷 확인(``strict``)으로 저장소에 닿는지 먼저 본다 —
    장애 중에 쓸 수 없는 URL 을 200 으로 내주지 않고 예외를 올려 라우터가 503 으로 바꾸게 한다."""
    bucket = bucket or settings.os_bucket
    await ensure_bucket(bucket, strict=True)
    key = _s3_prefix(model_name, version) + safe_filename(filename)
    expiry = settings.os_presigned_url_expiry

    url = await presigned_url("put_object", bucket, key, expiry)
    logger.debug("업로드 presigned URL 발급: bucket=%s key=%s expires_in=%d", bucket, key, expiry)

    return {"url": url, "key": key, "expires_in": expiry}


async def generate_download_url(
    model_name: str,
    version: int,
    filename: str,
    bucket: str | None = None,
) -> dict:
    """파일 다운로드용 presigned GET URL 발급. 객체가 없으면 ``FileNotFoundError``."""
    bucket = bucket or settings.os_bucket
    key = _s3_prefix(model_name, version) + safe_filename(filename)
    expiry = settings.os_presigned_url_expiry

    # 없는 파일만 FileNotFoundError — 저장소 연결 오류는 그대로 올려 호출 측이 503 으로 구분한다
    await head_s3_object(key, bucket)
    url = await presigned_url("get_object", bucket, key, expiry)
    logger.debug("다운로드 presigned URL 발급: bucket=%s key=%s", bucket, key)

    return {"url": url, "key": key, "expires_in": expiry}


async def generate_download_urls(
    model_name: str,
    version: int,
    bucket: str | None = None,
) -> dict[str, str]:
    """버전 내 모든 파일에 대해 presigned download URL 을 일괄 발급. 반환: ``{filename: url}``.

    ``list_objects_v2`` 를 페이지네이션해 1000개를 넘는 파일도 모두 포함한다.
    """
    bucket = bucket or settings.os_bucket
    prefix = _s3_prefix(model_name, version)
    expiry = settings.os_presigned_url_expiry

    async with get_s3_client() as s3:
        objects = await _list_all_objects(s3, bucket, prefix)
    urls = {}
    for obj in objects:
        filename = obj["Key"][len(prefix):]
        if not filename or filename.endswith("/"):
            continue
        urls[filename] = await presigned_url("get_object", bucket, obj["Key"], expiry)

    logger.debug("다운로드 URL 일괄 발급: %s v%d count=%d", model_name, version, len(urls))
    return urls


# =========================================================================== #
# 3. 파일 목록 / 정보 조회
# =========================================================================== #


async def list_files(
    model_name: str,
    version: int,
    bucket: str | None = None,
) -> list[dict]:
    """모델 버전의 파일을 S3 에서 리스팅(``manifest.json`` 포함, 디렉터리 표식 키 제외).

    ``list_objects_v2`` 를 페이지네이션해 1000개를 넘는 파일도 모두 본다.
    """
    bucket = bucket or settings.os_bucket
    prefix = _s3_prefix(model_name, version)

    async with get_s3_client() as s3:
        objects = await _list_all_objects(s3, bucket, prefix)

    files = []
    for obj in objects:
        filename = obj["Key"][len(prefix):]
        if not filename or filename.endswith("/"):
            continue
        files.append({
            "filename": filename,
            "key": obj["Key"],
            "size": obj.get("Size", 0),
            "last_modified": obj["LastModified"].isoformat() if obj.get("LastModified") else "",
        })
    return files


async def list_s3_directory(
    prefix: str = "",
    bucket: str | None = None,
) -> dict:
    """프리픽스 하위의 S3 객체·폴더를 디렉토리 구조처럼 리스팅(파일 브라우저용).

    폴더 단위 탐색을 위해 delimiter='/' 를 사용한다. UI 호환을 위해 로컬
    파일시스템 목록 API 와 동일한 형태를 반환한다.
    """
    bucket = bucket or settings.os_bucket
    clean = prefix.strip("/")
    s3_prefix = f"{clean}/" if clean else ""

    async with get_s3_client() as s3:
        resp = await s3.list_objects_v2(Bucket=bucket, Prefix=s3_prefix, Delimiter="/")

    folders = []
    for cp in resp.get("CommonPrefixes", []):
        fp = cp["Prefix"]
        name = fp.rstrip("/").rsplit("/", 1)[-1]
        folders.append({
            "key": "/" + fp.rstrip("/") + "/",
            "name": name,
            "owner": "", "group": "", "permissions": "",
        })

    files = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key == s3_prefix:
            continue
        name = key.rsplit("/", 1)[-1]
        if not name:
            continue
        files.append({
            "key": "/" + key,
            "name": name,
            "size": obj.get("Size", 0),
            "last_modified": obj["LastModified"].isoformat() if obj.get("LastModified") else "",
            "owner": "", "group": "", "permissions": "",
        })

    current_path = "/" + clean if clean else "/"
    logger.debug("S3 목록 조회: bucket=%s, prefix=%s, folders=%d, files=%d",
                bucket, s3_prefix, len(folders), len(files))
    return {"folders": folders, "files": files, "current_path": current_path}


async def generate_s3_download_url(
    path: str,
    bucket: str | None = None,
) -> str:
    """경로(S3 key) 로 임의 객체의 presigned download URL 을 발급(존재 확인 없음 — 호출 측이 먼저 확인)."""
    bucket = bucket or settings.os_bucket
    key = path.lstrip("/")
    expiry = settings.os_presigned_url_expiry

    return await presigned_url("get_object", bucket, key, expiry)


async def head_s3_object(path: str, bucket: str | None = None) -> dict:
    """경로(S3 key) 객체의 크기·형식. 없으면 ``FileNotFoundError``."""
    bucket = bucket or settings.os_bucket
    key = path.lstrip("/")
    if not key or key.endswith("/"):
        raise FileNotFoundError(f"파일이 저장소에 없습니다: {path}")
    async with get_s3_client() as s3:
        try:
            head = await s3.head_object(Bucket=bucket, Key=key)
        except Exception as e:  # noqa: BLE001 — botocore ClientError(404) 와 연결 오류를 구분한다
            code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                raise FileNotFoundError(f"파일이 저장소에 없습니다: {path}") from e
            raise
    return {"key": key, "size": head.get("ContentLength", 0), "content_type": head.get("ContentType")}


async def iter_s3_object(path: str, bucket: str | None = None, chunk_size: int = 1024 * 1024):
    """S3 객체를 조각으로 읽어 흘려보낸다(전체를 메모리에 올리지 않음). 비동기 제너레이터."""
    bucket = bucket or settings.os_bucket
    async with get_s3_client() as s3:
        obj = await s3.get_object(Bucket=bucket, Key=path.lstrip("/"))
        body = obj["Body"]  # StreamingBody — `async with` 로 열면 크기 인자 없는 aiohttp 응답이 나오므로 직접 읽는다
        try:
            while chunk := await body.read(chunk_size):
                yield chunk
        finally:
            body.close()


async def get_total_size(
    model_name: str,
    version: int,
    bucket: str | None = None,
) -> tuple[int, int]:
    """버전 단위 ``(파일 수, 총 바이트)`` 를 돌려준다 (확정 때 만든 ``manifest.json`` 제외)."""
    files = [f for f in await list_files(model_name, version, bucket) if f["filename"] != "manifest.json"]
    return len(files), sum(f["size"] for f in files)


# =========================================================================== #
# 4. OCI 매니페스트 생성
# =========================================================================== #


async def generate_manifest(
    model_name: str,
    version: int,
    annotations: dict[str, str] | None = None,
    bucket: str | None = None,
) -> dict:
    """버전에 대한 OCI 호환 ``manifest.json`` 을 생성한다.

    버전 프리픽스 하위의 모든 S3 파일을 스캔해 다이제스트를 계산하고,
    OCI Image Manifest 스펙을 따르는 매니페스트를 구성한다.

    config 디스크립터는 ``config.json`` 또는 ``MLmodel`` 파일, 없으면 이름·버전만 담은 빈 config 의
    다이제스트를 쓴다(빈 config 자체는 저장하지 않음). 부작용: ``{prefix}manifest.json`` 을 S3 에
    덮어쓴다. ``list_objects_v2`` 를 페이지네이션해 모든 파일을 레이어로 넣는다.
    """
    bucket = bucket or settings.os_bucket
    prefix = _s3_prefix(model_name, version)

    layers = []
    config_digest = None
    config_size = 0

    async with get_s3_client() as s3:
        for obj in await _list_all_objects(s3, bucket, prefix):
            filename = obj["Key"][len(prefix):]
            # 이전 확정 때 만든 manifest.json 자신은 레이어가 아니다(재확정 시 중복 방지)
            if not filename or filename.endswith("/") or filename == "manifest.json":
                continue

            size = obj.get("Size", 0)

            # 다이제스트 — 업로드 때 기록한 메타데이터(sha256)를 쓰고, 없으면(presigned 업로드 등)
            # 객체를 조각 단위로 읽어 계산한다(대용량 가중치 파일을 메모리에 통째로 올리지 않음).
            head = await s3.head_object(Bucket=bucket, Key=obj["Key"])
            sha = (head.get("Metadata") or {}).get("sha256")
            if not sha:
                h = hashlib.sha256()
                file_resp = await s3.get_object(Bucket=bucket, Key=obj["Key"])
                async for chunk in file_resp["Body"].iter_chunks(8 * 1024 * 1024):
                    h.update(chunk)
                sha = h.hexdigest()
                logger.debug("다이제스트 직접 계산(메타데이터 없음): key=%s", obj["Key"])
            digest = f"sha256:{sha}"

            media_type = _media_type_for_file(filename)

            layer = {
                "mediaType": media_type,
                "digest": digest,
                "size": size,
                "annotations": {
                    "org.opencontainers.image.title": filename,
                },
            }
            layers.append(layer)

            # config.json 또는 MLmodel 을 config 디스크립터로 사용
            if filename in ("config.json", "MLmodel") and config_digest is None:
                config_digest = digest
                config_size = size

    # config 파일이 없으면 빈 config 를 생성
    if config_digest is None:
        empty_config = json.dumps({"model_name": model_name, "version": version}).encode()
        config_digest = f"sha256:{_sha256_bytes(empty_config)}"
        config_size = len(empty_config)

    base_annotations = {
        "org.opencontainers.image.created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "ai.argus.model.name": model_name,
        "ai.argus.model.version": str(version),
    }
    if annotations:
        base_annotations.update(annotations)

    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.argus.model.config.v1+json",
            "digest": config_digest,
            "size": config_size,
        },
        "layers": layers,
        "annotations": base_annotations,
    }

    # 매니페스트를 S3 에 저장
    manifest_json = json.dumps(manifest, indent=2).encode()
    manifest_key = prefix + "manifest.json"
    async with get_s3_client() as s3:
        await s3.put_object(Bucket=bucket, Key=manifest_key, Body=manifest_json)

    logger.info(
        "OCI 매니페스트 생성: %s v%d (레이어 %d개, %d bytes)",
        model_name, version, len(layers), sum(l["size"] for l in layers),
    )
    return manifest


# =========================================================================== #
# 5. 다운로드 / Pull
# =========================================================================== #


async def download_file(
    model_name: str,
    version: int,
    filename: str,
    bucket: str | None = None,
) -> bytes:
    """S3 에서 단일 파일을 받아 bytes 로 반환(전체를 메모리에 올린다). 파일 이름 검증은 하지 않는다."""
    bucket = bucket or settings.os_bucket
    key = _s3_prefix(model_name, version) + filename

    async with get_s3_client() as s3:
        resp = await s3.get_object(Bucket=bucket, Key=key)
        return await resp["Body"].read()


async def download_all(
    model_name: str,
    version: int,
    dest_dir: str | Path,
    bucket: str | None = None,
) -> list[str]:
    """버전 내 모든 파일을 로컬 디렉토리로 일괄 다운로드. 디렉토리 자동 생성. 반환: 저장된 로컬 경로 목록."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    files = await list_files(model_name, version, bucket)

    downloaded = []
    for f in files:
        data = await download_file(model_name, version, f["filename"], bucket)
        file_path = dest / f["filename"]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        downloaded.append(str(file_path))

    logger.info("%d개 파일을 %s 로 다운로드", len(downloaded), dest)
    return downloaded


# =========================================================================== #
# 6. 삭제
# =========================================================================== #


async def delete_version_files(
    model_name: str,
    version: int,
    bucket: str | None = None,
) -> int:
    """버전 단위 S3 파일 삭제 (manifest 포함). 반환: 삭제한 객체 수.

    ``list_objects_v2`` 를 페이지네이션해 모두 모은 뒤 ``delete_objects`` 1000개 단위로 지운다.
    """
    return await delete_prefix_files(_s3_prefix(model_name, version), bucket)


async def delete_prefix_files(prefix: str, bucket: str | None = None) -> int:
    """키 프리픽스 하위 S3 객체를 모두 삭제한다. 반환: 삭제한 객체 수.

    버킷 전체·임의 상위 경로를 지우는 실수를 막기 위해 프리픽스는 비어 있지 않고 ``/`` 로 끝나야
    하며 ``..`` 조각을 포함할 수 없다(어기면 ``ValueError``).
    """
    if not prefix or not prefix.endswith("/") or prefix.startswith("/") or ".." in prefix.split("/"):
        raise ValueError(f"삭제할 수 없는 S3 프리픽스입니다: {prefix!r}")
    bucket = bucket or settings.os_bucket

    async with get_s3_client() as s3:
        objects = await _list_all_objects(s3, bucket, prefix)
        if not objects:
            logger.debug("삭제할 객체 없음: s3://%s/%s", bucket, prefix)
            return 0
        await _delete_keys(s3, bucket, objects)

    logger.info("s3://%s/%s 에서 객체 %d개 삭제", bucket, prefix, len(objects))
    return len(objects)


async def delete_model_files(
    model_name: str,
    bucket: str | None = None,
) -> int:
    """모델 이름 프리픽스(``{model_name}/``) 하위 S3 파일을 일괄 삭제. 페이지네이션으로 전부 지운다.

    주의: 이름 프리픽스 통째 기준이라 같은 이름의 OCI 모델 파일도 함께 지워진다. 모델 영구 삭제는
    이 함수 대신 버전 프리픽스 단위(``delete_version_files``/``delete_prefix_files``)로 지운다.
    반환: 삭제한 객체 수.
    """
    bucket = bucket or settings.os_bucket
    prefix = f"{model_name}/"

    async with get_s3_client() as s3:
        total = 0
        continuation = None
        while True:
            params = {"Bucket": bucket, "Prefix": prefix}
            if continuation:
                params["ContinuationToken"] = continuation
            resp = await s3.list_objects_v2(**params)
            objects = resp.get("Contents", [])
            if objects:
                await s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
                )
                total += len(objects)
            if not resp.get("IsTruncated"):
                break
            continuation = resp.get("NextContinuationToken")

    logger.info("s3://%s/%s 에서 객체 %d개 삭제", bucket, prefix, total)
    return total


# =========================================================================== #
# 7. HuggingFace 임포트
# =========================================================================== #


async def import_from_huggingface(
    hf_model_id: str,
    model_name: str,
    version: int,
    revision: str = "main",
    bucket: str | None = None,
) -> dict:
    """HuggingFace Hub 에서 모델을 받아 S3 에 업로드한다(import-hf 흐름).

    huggingface_hub 라이브러리로 모든 모델 파일을 다운로드한 뒤,
    각 파일을 모델 버전 프리픽스 하위로 S3 에 업로드한다.

    임포트한 모델의 메타데이터를 반환한다(``source``·``revision``·``file_count``·``total_size``,
    config.json 주요 필드, ``readme``, ``tokenizer_class``, ``manifest``). 다운로드·업로드 실패는 예외로 올린다.
    """
    import asyncio

    from huggingface_hub import snapshot_download

    bucket = bucket or settings.os_bucket
    await ensure_bucket(bucket)
    logger.info("HuggingFace 모델 임포트: %s (revision=%s) -> %s v%d",
                hf_model_id, revision, model_name, version)

    # 임시 디렉토리로 다운로드 — snapshot_download 는 동기(블로킹) 함수라 스레드에서 돌려
    # 이벤트 루프(다른 API 요청)를 멈추지 않게 한다.
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_dir = await asyncio.to_thread(
            snapshot_download, repo_id=hf_model_id, revision=revision, local_dir=tmp_dir,
        )
        local_path = Path(local_dir)

        # 파일 수집 — 숨김(.git, .cache) 제외. 내용은 메모리에 올리지 않는다.
        paths = [
            fp for fp in sorted(local_path.rglob("*"))
            if fp.is_file() and not any(part.startswith(".") for part in fp.relative_to(local_path).parts)
        ]
        total_size = sum(fp.stat().st_size for fp in paths)
        logger.info("HuggingFace 에서 %d개 파일 다운로드 (총 %d bytes)", len(paths), total_size)

        # S3 로 업로드 — 파일 경로 기반 관리형 전송(대용량은 멀티파트)으로 스트리밍,
        # 다이제스트는 조각 단위로 계산해 메타데이터에 남긴다(manifest 생성 때 재사용).
        prefix = _s3_prefix(model_name, version)
        async with get_s3_client() as s3:
            for fp in paths:
                rel = fp.relative_to(local_path).as_posix()
                sha = await asyncio.to_thread(_sha256_file, fp)
                await s3.upload_file(
                    str(fp), bucket, prefix + rel, ExtraArgs={"Metadata": {"sha256": sha}},
                )

        metadata = {
            "source": f"huggingface:{hf_model_id}",
            "revision": revision,
            "file_count": len(paths),
            "total_size": total_size,
        }

        # 모델 메타데이터를 위해 config.json 파싱
        config_path = local_path / "config.json"
        if config_path.is_file():
            try:
                config = json.loads(config_path.read_text())
                for k in ("model_type", "architectures", "torch_dtype", "transformers_version",
                          "hidden_size", "num_hidden_layers", "num_attention_heads", "vocab_size"):
                    metadata[k] = config.get(k)
            except Exception as e:
                logger.warning("%s 의 config.json 파싱 실패: %s", hf_model_id, e)

        # Model Card 용 README.md 읽기
        readme_path = local_path / "README.md"
        if readme_path.is_file():
            try:
                metadata["readme"] = readme_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("%s 의 README.md 읽기 실패: %s", hf_model_id, e)

        # 토크나이저 정보를 위해 tokenizer_config.json 읽기
        tok_path = local_path / "tokenizer_config.json"
        if tok_path.is_file():
            try:
                tok_config = json.loads(tok_path.read_text())
                metadata["tokenizer_class"] = tok_config.get("tokenizer_class")
            except Exception as e:
                logger.warning("%s 의 tokenizer_config.json 파싱 실패: %s", hf_model_id, e)

    # OCI 매니페스트 생성
    annotations = {
        "ai.argus.model.source": f"huggingface:{hf_model_id}",
        "ai.argus.model.source.revision": revision,
    }
    if metadata.get("model_type"):
        annotations["ai.argus.model.type"] = metadata["model_type"]

    manifest = await generate_manifest(model_name, version, annotations, bucket)
    metadata["manifest"] = manifest

    logger.info("HuggingFace 임포트 완료: %s -> %s v%d (파일 %d개, %d bytes)",
                hf_model_id, model_name, version, len(paths), total_size)
    return metadata


async def import_from_local_directory(
    local_dir: str | Path,
    model_name: str,
    version: int,
    source: str = "local",
    bucket: str | None = None,
) -> dict:
    """로컬 디렉토리(에어갭/사내 망)에서 모델 파일을 S3 로 가져온다(import-local).

    에어갭 시나리오에서 사용한다: 먼저 USB/SCP 로 파일을 전송한 뒤,
    로컬 파일시스템에서 MinIO 로 임포트한다. 디렉터리가 없으면 ``FileNotFoundError``.

    주의: 모든 파일 내용을 메모리에 올린 뒤 업로드한다(대용량 모델은 메모리 사용 큼).
    숨김 판정은 최상위 경로 조각이 ``.`` 으로 시작하는지만 본다.
    """
    local_path = Path(local_dir)
    if not local_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {local_path}")

    all_files: list[tuple[str, bytes]] = []
    total_size = 0
    for file_path in sorted(local_path.rglob("*")):
        if file_path.is_file():
            rel = file_path.relative_to(local_path)
            if str(rel).startswith("."):
                continue
            data = file_path.read_bytes()
            all_files.append((str(rel), data))
            total_size += len(data)

    logger.info("%s 에서 %d개 파일 임포트 (%d bytes)", local_path, len(all_files), total_size)

    results = await upload_files(model_name, version, all_files, bucket)

    annotations = {"ai.argus.model.source": source}
    manifest = await generate_manifest(model_name, version, annotations, bucket)
    logger.info("로컬 임포트 완료: %s -> %s v%d (파일 %d개, %d bytes)",
                local_path, model_name, version, len(results), total_size)

    return {
        "source": source,
        "file_count": len(results),
        "total_size": total_size,
        "manifest": manifest,
    }
