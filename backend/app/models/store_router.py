# SPDX-License-Identifier: Apache-2.0
"""모델 스토어 라우터 — S3 기반 모델 아티팩트 관리 API.

``/api/v1/model-store`` 경로에서 다음을 제공한다:
  - 모델 파일 업로드 (직접 / presigned URL)
  - 모델 파일 다운로드 (presigned URL)
  - 버전 파일 리스팅, OCI manifest 발급
  - HuggingFace Hub 임포트 (``import/huggingface``)
  - 에어갭 환경용 로컬 디렉토리 임포트 (``import/local``)

로깅 정책: 업로드/다운로드/임포트는 INFO 로 모델·버전·파일 수를 기록.
404 / 잘못된 요청은 WARNING 으로 식별자와 함께 남긴다.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.core.config import settings
from app.core.database import get_session
from app.models import model_store, service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-store", tags=["model-store"])


# ---------------------------------------------------------------------------
# 요청/응답 스키마
# ---------------------------------------------------------------------------

class UploadUrlRequest(BaseModel):
    filename: str = Field(..., description="Name of the file to upload")


class UploadUrlResponse(BaseModel):
    url: str
    key: str
    expires_in: int


class DownloadUrlResponse(BaseModel):
    url: str
    key: str
    expires_in: int


class FileInfo(BaseModel):
    filename: str
    key: str
    size: int
    last_modified: str = ""


class FinalizeRequest(BaseModel):
    annotations: dict[str, str] | None = None


class FinalizeResponse(BaseModel):
    status: str
    file_count: int
    total_size: int
    manifest: dict | None = None


class HuggingFaceImportRequest(BaseModel):
    hf_model_id: str = Field(..., description="HuggingFace model ID (e.g. 'bert-base-uncased')")
    model_name: str = Field(..., description="Target model name (e.g. 'argus.ml.bert')")
    revision: str = Field("main", description="HuggingFace revision/branch")
    description: str | None = None
    owner: str | None = None


class LocalImportRequest(BaseModel):
    local_dir: str = Field(..., description="Path to local directory containing model files")
    model_name: str = Field(..., description="Target model name")
    description: str | None = None
    owner: str | None = None
    source: str = Field("local", description="Source label")


class ImportResponse(BaseModel):
    model_name: str
    version: int
    file_count: int
    total_size: int
    storage_location: str


# ---------------------------------------------------------------------------
# 업로드 엔드포인트
# ---------------------------------------------------------------------------

@router.post("/{model_name}/versions/{version}/upload")
async def upload_file(_guard: AdminUser,
    model_name: str,
    version: int,
    file: UploadFile,
):
    """모델 버전에 단일 파일을 업로드 (multipart/form-data)."""
    try:
        data = await file.read()
        filename = file.filename or "uploaded_file"
        result = await model_store.upload_file(model_name, version, filename, data)
        return result
    except Exception as e:
        logger.error("업로드 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{model_name}/versions/{version}/upload-url", response_model=UploadUrlResponse)
async def get_upload_url(_guard: AdminUser,
    model_name: str,
    version: int,
    body: UploadUrlRequest,
):
    """클라이언트 → S3 직접 업로드용 presigned PUT URL 발급."""
    try:
        return await model_store.generate_upload_url(model_name, version, body.filename)
    except Exception as e:
        logger.error("업로드 URL 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 다운로드 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/{model_name}/versions/{version}/download-url", response_model=DownloadUrlResponse)
async def get_download_url(
    model_name: str,
    version: int,
    filename: str = Query(..., description="File to download"),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """단일 파일에 대한 presigned download URL 발급."""
    try:
        client_ip = request.client.host if request and request.client else None
        user_agent = request.headers.get("user-agent") if request else None

        from app.models.download_log import log_download
        await log_download(session, model_name, version, "download", client_ip, user_agent)

        # 해당되는 경우 OCI 다운로드 로그에도 기록
        from app.oci_hub.download_log import log_oci_download
        await log_oci_download(session, model_name, version, "download", client_ip, user_agent)

        return await model_store.generate_download_url(model_name, version, filename)
    except Exception as e:
        logger.error("다운로드 URL 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{model_name}/versions/{version}/download-urls")
async def get_download_urls(
    model_name: str,
    version: int,
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """버전 내 모든 파일에 대해 presigned download URL 을 일괄 발급."""
    try:
        client_ip = request.client.host if request and request.client else None
        user_agent = request.headers.get("user-agent") if request else None

        from app.models.download_log import log_download
        await log_download(session, model_name, version, "pull", client_ip, user_agent)

        # 해당되는 경우 OCI 다운로드 로그 기록 + 카운터 증가
        from app.oci_hub.download_log import log_oci_download
        await log_oci_download(session, model_name, version, "pull", client_ip, user_agent)

        urls = await model_store.generate_download_urls(model_name, version)
        return {"files": urls}
    except Exception as e:
        logger.error("다운로드 URL 일괄 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# S3 버킷 브라우저 (OCI 모델 파일 페이지용)
# ---------------------------------------------------------------------------

@router.get("/browse/list")
async def browse_s3_directory(
    path: str = Query("/", description="Directory path in S3 bucket"),
):
    """S3 객체/폴더를 디렉토리처럼 리스팅 (OCI 모델 파일 브라우저용)."""
    try:
        return await model_store.list_s3_directory(prefix=path.lstrip("/"))
    except Exception as e:
        logger.error("S3 탐색 오류: %s", e)
        msg = str(e)
        if any(k in msg.lower() for k in ("connect", "endpoint", "timed out", "9000")):
            raise HTTPException(
                status_code=503,
                detail="오브젝트 스토리지에 연결할 수 없습니다. MinIO 실행 여부와 설정을 확인하세요.",
            )
        raise HTTPException(status_code=500, detail=msg)


@router.get("/browse/download")
async def browse_s3_download(
    path: str = Query(..., description="File path in S3 bucket"),
):
    """경로(S3 key) 로 임의 객체의 presigned download URL 발급 (파일 브라우저용)."""
    try:
        url = await model_store.generate_s3_download_url(path)
        return {"url": url}
    except Exception as e:
        logger.error("S3 다운로드 URL 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 목록 / 매니페스트
# ---------------------------------------------------------------------------

@router.get("/{model_name}/versions/{version}/files", response_model=list[FileInfo])
async def list_version_files(
    model_name: str,
    version: int,
):
    """모델 버전의 전체 파일을 S3 에서 메타데이터와 함께 나열."""
    try:
        return await model_store.list_files(model_name, version)
    except Exception as e:
        logger.error("파일 목록 조회 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{model_name}/versions/{version}/manifest")
async def get_manifest(
    model_name: str,
    version: int,
):
    """버전의 OCI ``manifest.json`` 을 조회."""
    try:
        data = await model_store.download_file(model_name, version, "manifest.json")
        return json.loads(data)
    except Exception as e:
        logger.warning("%s v%d 의 manifest 를 찾을 수 없음: %s", model_name, version, e)
        raise HTTPException(status_code=404, detail="manifest을(를) 찾을 수 없습니다.")


# ---------------------------------------------------------------------------
# 확정 (manifest 생성 + DB 갱신)
# ---------------------------------------------------------------------------

@router.post("/{model_name}/versions/{version}/finalize", response_model=FinalizeResponse)
async def finalize_version(_guard: AdminUser,
    model_name: str,
    version: int,
    body: FinalizeRequest | None = None,
    session: AsyncSession = Depends(get_session),
):
    """버전 확정 흐름: S3 파일 스캔 → manifest 생성 → DB 메타 갱신 (READY/FAILED)."""
    try:
        # 파일 수 집계
        file_count, total_size = await model_store.get_total_size(model_name, version)
        if file_count == 0:
            raise HTTPException(status_code=400, detail="이 버전에 해당하는 파일을 찾을 수 없습니다.")

        # OCI 매니페스트 생성
        annotations = body.annotations if body else None
        manifest = await model_store.generate_manifest(
            model_name, version, annotations,
        )

        return FinalizeResponse(
            status="READY",
            file_count=file_count,
            total_size=total_size,
            manifest=manifest,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("확정 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 임포트: HuggingFace
# ---------------------------------------------------------------------------

@router.post("/import/huggingface", response_model=ImportResponse)
async def import_huggingface(current: AdminUser,
    body: HuggingFaceImportRequest,
    session: AsyncSession = Depends(get_session),
):
    """HuggingFace Hub 에서 모델을 가져와 S3 에 업로드 (import-hf 흐름).

    모델을 다운로드해 S3 에 저장하고 DB 레코드를 생성한다.
    """
    try:
        # 등록 모델 생성 또는 조회
        from app.models.schemas import RegisteredModelCreate
        try:
            model = await service.create_registered_model(
                session,
                RegisteredModelCreate(
                    name=body.model_name,
                    description=body.description,
                    owner=body.owner,
                    storage_location=f"s3://{settings.os_bucket}/{body.model_name}",
                ),
                created_by=current.username,
            )
        except ValueError:
            # 이미 존재하는 모델 — 조회
            model = await service.get_registered_model_by_name(session, body.model_name)
            if not model:
                raise HTTPException(status_code=404, detail=f"모델 '{body.model_name}'을(를) 찾을 수 없습니다.")

        # storage_type 을 s3 로 갱신
        from sqlalchemy import update

        from app.models.models import RegisteredModel
        await session.execute(
            update(RegisteredModel).where(
                RegisteredModel.name == body.model_name
            ).values(
                storage_type="s3",
                bucket_name=settings.os_bucket,
                storage_location=f"s3://{settings.os_bucket}/{body.model_name}",
            )
        )

        # 버전 생성
        from app.models.schemas import ModelVersionCreate
        version_resp = await service.create_model_version(
            session,
            ModelVersionCreate(model_name=body.model_name),
        )
        version = version_resp.version

        # HuggingFace 에서 임포트
        metadata = await model_store.import_from_huggingface(
            hf_model_id=body.hf_model_id,
            model_name=body.model_name,
            version=version,
            revision=body.revision,
        )

        # 버전을 READY 로 갱신
        from app.models.schemas import ModelVersionFinalize, ModelVersionStatus
        await service.finalize_model_version(
            session, body.model_name, version,
            ModelVersionFinalize(status=ModelVersionStatus.READY),
        )

        # catalog_models 메타데이터 저장
        import json as _json

        from app.models.models import CatalogModel
        cm = CatalogModel(
            model_version_id=version_resp.id,
            model_name=body.model_name,
            version=version,
            source_type="huggingface",
            manifest=_json.dumps(metadata.get("manifest")),
        )
        # HF config 메타데이터 파싱
        if metadata.get("model_type"):
            cm.serialization_format = metadata["model_type"]
        if metadata.get("transformers_version"):
            cm.mlflow_version = metadata["transformers_version"]

        session.add(cm)
        await session.commit()

        storage_loc = f"s3://{settings.os_bucket}/{body.model_name}/v{version}/"
        logger.info("HuggingFace 임포트 완료: %s -> %s v%d", body.hf_model_id, body.model_name, version)

        return ImportResponse(
            model_name=body.model_name,
            version=version,
            file_count=metadata["file_count"],
            total_size=metadata["total_size"],
            storage_location=storage_loc,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("HuggingFace 임포트 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 임포트: 로컬 디렉토리 (에어갭)
# ---------------------------------------------------------------------------

@router.post("/import/local", response_model=ImportResponse)
async def import_local(current: AdminUser,
    body: LocalImportRequest,
    session: AsyncSession = Depends(get_session),
):
    """로컬 디렉토리(에어갭/사내 망) 에서 모델 파일을 S3 로 임포트.

    에어갭 시나리오에서 사용한다: 먼저 USB/SCP 로 파일을 전송한 뒤,
    로컬 파일시스템에서 MinIO 로 임포트한다.
    """
    try:
        # 등록 모델 생성 또는 조회
        from app.models.schemas import RegisteredModelCreate
        try:
            model = await service.create_registered_model(
                session,
                RegisteredModelCreate(
                    name=body.model_name,
                    description=body.description,
                    owner=body.owner,
                    storage_location=f"s3://{settings.os_bucket}/{body.model_name}",
                ),
                created_by=current.username,
            )
        except ValueError:
            model = await service.get_registered_model_by_name(session, body.model_name)
            if not model:
                raise HTTPException(status_code=404, detail=f"모델 '{body.model_name}'을(를) 찾을 수 없습니다.")

        # storage_type 을 s3 로 갱신
        from sqlalchemy import update

        from app.models.models import RegisteredModel
        await session.execute(
            update(RegisteredModel).where(
                RegisteredModel.name == body.model_name
            ).values(
                storage_type="s3",
                bucket_name=settings.os_bucket,
                storage_location=f"s3://{settings.os_bucket}/{body.model_name}",
            )
        )

        # 버전 생성
        from app.models.schemas import ModelVersionCreate
        version_resp = await service.create_model_version(
            session,
            ModelVersionCreate(model_name=body.model_name),
        )
        version = version_resp.version

        # 로컬에서 임포트
        metadata = await model_store.import_from_local_directory(
            local_dir=body.local_dir,
            model_name=body.model_name,
            version=version,
            source=body.source,
        )

        # 확정
        from app.models.schemas import ModelVersionFinalize, ModelVersionStatus
        await service.finalize_model_version(
            session, body.model_name, version,
            ModelVersionFinalize(status=ModelVersionStatus.READY),
        )

        # catalog_models 저장
        import json as _json

        from app.models.models import CatalogModel
        cm = CatalogModel(
            model_version_id=version_resp.id,
            model_name=body.model_name,
            version=version,
            source_type=body.source,
            manifest=_json.dumps(metadata.get("manifest")),
        )
        session.add(cm)
        await session.commit()

        storage_loc = f"s3://{settings.os_bucket}/{body.model_name}/v{version}/"

        return ImportResponse(
            model_name=body.model_name,
            version=version,
            file_count=metadata["file_count"],
            total_size=metadata["total_size"],
            storage_location=storage_loc,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("로컬 임포트 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
