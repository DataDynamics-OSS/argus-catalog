# SPDX-License-Identifier: Apache-2.0
"""모델 아티팩트 스토어 — S3/MinIO 백엔드 + OCI 매니페스트 지원.

주요 책임:
  - 모델 파일을 S3 로 업로드 (직접 또는 presigned URL 경유)
  - 모델 파일 다운로드 (presigned URL 발급)
  - OCI 호환 ``manifest.json`` 생성
  - HuggingFace Hub 로부터 모델 가져오기 (import-hf)
  - SHA256 다이제스트 기반 content-addressable 저장 구조

로깅 정책: 업로드/다운로드/임포트/매니페스트 생성 동작은 INFO 로 모델·버전·
파일 수 정보를 기록. S3 호출 실패, HF 다운로드 실패 등 외부 의존성 오류는
WARNING 또는 ERROR 로 표면화한다.
"""

import datetime as _dt
import hashlib
import json
import logging
import tempfile
from pathlib import Path

from app.core.config import settings
from app.core.s3 import ensure_bucket, get_s3_client

logger = logging.getLogger(__name__)


# =========================================================================== #
# 헬퍼
# =========================================================================== #


def _s3_prefix(model_name: str, version: int) -> str:
    """모델 버전에 대응되는 S3 key 프리픽스(``{name}/v{ver}/``) 를 생성한다."""
    return f"{model_name}/v{version}/"


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
    """단일 파일을 ``{name}/v{ver}/{filename}`` 키로 S3 에 업로드."""
    bucket = bucket or settings.os_bucket
    await ensure_bucket(bucket)
    key = _s3_prefix(model_name, version) + filename
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
    """여러 파일을 일괄 업로드. 각 항목은 ``(filename, bytes)`` 튜플."""
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
    """클라이언트 직접 업로드용 presigned PUT URL 발급."""
    bucket = bucket or settings.os_bucket
    await ensure_bucket(bucket)
    key = _s3_prefix(model_name, version) + filename
    expiry = settings.os_presigned_url_expiry

    async with get_s3_client() as s3:
        url = await s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry,
        )

    return {"url": url, "key": key, "expires_in": expiry}


async def generate_download_url(
    model_name: str,
    version: int,
    filename: str,
    bucket: str | None = None,
) -> dict:
    """파일 다운로드용 presigned GET URL 발급."""
    bucket = bucket or settings.os_bucket
    key = _s3_prefix(model_name, version) + filename
    expiry = settings.os_presigned_url_expiry

    async with get_s3_client() as s3:
        url = await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry,
        )

    return {"url": url, "key": key, "expires_in": expiry}


async def generate_download_urls(
    model_name: str,
    version: int,
    bucket: str | None = None,
) -> dict[str, str]:
    """버전 내 모든 파일에 대해 presigned download URL 을 일괄 발급."""
    bucket = bucket or settings.os_bucket
    prefix = _s3_prefix(model_name, version)
    expiry = settings.os_presigned_url_expiry

    async with get_s3_client() as s3:
        resp = await s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        urls = {}
        for obj in resp.get("Contents", []):
            filename = obj["Key"][len(prefix):]
            if not filename or filename.endswith("/"):
                continue
            url = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": obj["Key"]},
                ExpiresIn=expiry,
            )
            urls[filename] = url

    return urls


# =========================================================================== #
# 3. 파일 목록 / 정보 조회
# =========================================================================== #


async def list_files(
    model_name: str,
    version: int,
    bucket: str | None = None,
) -> list[dict]:
    """모델 버전의 모든 파일을 S3 에서 리스팅."""
    bucket = bucket or settings.os_bucket
    prefix = _s3_prefix(model_name, version)

    async with get_s3_client() as s3:
        resp = await s3.list_objects_v2(Bucket=bucket, Prefix=prefix)

    files = []
    for obj in resp.get("Contents", []):
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
    logger.info("S3 목록 조회: bucket=%s, prefix=%s, folders=%d, files=%d",
                bucket, s3_prefix, len(folders), len(files))
    return {"folders": folders, "files": files, "current_path": current_path}


async def generate_s3_download_url(
    path: str,
    bucket: str | None = None,
) -> str:
    """경로(S3 key) 로 임의 객체의 presigned download URL 을 발급."""
    bucket = bucket or settings.os_bucket
    key = path.lstrip("/")
    expiry = settings.os_presigned_url_expiry

    async with get_s3_client() as s3:
        url = await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry,
        )
    return url


async def get_total_size(
    model_name: str,
    version: int,
    bucket: str | None = None,
) -> tuple[int, int]:
    """버전 단위 ``(파일 수, 총 바이트)`` 를 돌려준다."""
    files = await list_files(model_name, version, bucket)
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
    """
    bucket = bucket or settings.os_bucket
    prefix = _s3_prefix(model_name, version)

    layers = []
    config_digest = None
    config_size = 0

    async with get_s3_client() as s3:
        resp = await s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        for obj in resp.get("Contents", []):
            filename = obj["Key"][len(prefix):]
            if not filename or filename.endswith("/"):
                continue

            size = obj.get("Size", 0)

            # 다이제스트 계산을 위해 파일을 읽는다
            file_resp = await s3.get_object(Bucket=bucket, Key=obj["Key"])
            data = await file_resp["Body"].read()
            digest = f"sha256:{_sha256_bytes(data)}"

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
    """S3 에서 단일 파일을 받아 bytes 로 반환."""
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
    """버전 내 모든 파일을 로컬 디렉토리로 일괄 다운로드. 디렉토리 자동 생성."""
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
    """버전 단위 S3 파일 전체 삭제 (manifest 포함)."""
    bucket = bucket or settings.os_bucket
    prefix = _s3_prefix(model_name, version)

    async with get_s3_client() as s3:
        resp = await s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        objects = resp.get("Contents", [])
        if not objects:
            return 0

        await s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
        )

    logger.info("s3://%s/%s 에서 객체 %d개 삭제", bucket, prefix, len(objects))
    return len(objects)


async def delete_model_files(
    model_name: str,
    bucket: str | None = None,
) -> int:
    """모델의 모든 버전 파일을 S3 에서 일괄 삭제 (모델 hard-delete 시 사용)."""
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

    임포트한 모델의 메타데이터를 반환한다.
    """
    from huggingface_hub import snapshot_download

    logger.info("HuggingFace 모델 임포트: %s (revision=%s) -> %s v%d",
                hf_model_id, revision, model_name, version)

    # 임시 디렉토리로 다운로드
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_dir = snapshot_download(
            repo_id=hf_model_id,
            revision=revision,
            local_dir=tmp_dir,
        )
        local_path = Path(local_dir)

        # 모든 파일 수집
        all_files: list[tuple[str, bytes]] = []
        total_size = 0
        for file_path in sorted(local_path.rglob("*")):
            if file_path.is_file():
                # .git 내부 파일 건너뛰기
                rel = file_path.relative_to(local_path)
                if str(rel).startswith("."):
                    continue
                data = file_path.read_bytes()
                all_files.append((str(rel), data))
                total_size += len(data)

        logger.info("HuggingFace 에서 %d개 파일 다운로드 (총 %d bytes)",
                    len(all_files), total_size)

        # S3 로 업로드
        results = await upload_files(model_name, version, all_files, bucket)

        # 메타데이터 추출을 위해 config.json 이 있으면 파싱
        metadata = {
            "source": f"huggingface:{hf_model_id}",
            "revision": revision,
            "file_count": len(results),
            "total_size": total_size,
        }

        # 모델 메타데이터를 위해 config.json 파싱
        config_path = local_path / "config.json"
        if config_path.is_file():
            try:
                config = json.loads(config_path.read_text())
                metadata["model_type"] = config.get("model_type")
                metadata["architectures"] = config.get("architectures")
                metadata["torch_dtype"] = config.get("torch_dtype")
                metadata["transformers_version"] = config.get("transformers_version")
                metadata["hidden_size"] = config.get("hidden_size")
                metadata["num_hidden_layers"] = config.get("num_hidden_layers")
                metadata["num_attention_heads"] = config.get("num_attention_heads")
                metadata["vocab_size"] = config.get("vocab_size")
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
                hf_model_id, model_name, version, len(results), total_size)
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
    로컬 파일시스템에서 MinIO 로 임포트한다.
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

    return {
        "source": source,
        "file_count": len(results),
        "total_size": total_size,
        "manifest": manifest,
    }
