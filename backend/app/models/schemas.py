# SPDX-License-Identifier: Apache-2.0
"""MLflow 모델 레지스트리의 Pydantic 스키마(요청·응답 DTO).

``/api/v1/models`` 라우터와 서비스 레이어가 사용하는 요청/응답 모델을 모아둔다.
UC 호환 라우터(``app/models/uc_compat.py``) 는 UC protobuf 포맷과 이 스키마
사이의 변환을 내부적으로 처리한다.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 열거형(Enum)
# ---------------------------------------------------------------------------

class ModelVersionStatus(str, Enum):
    PENDING_REGISTRATION = "PENDING_REGISTRATION"
    READY = "READY"
    FAILED_REGISTRATION = "FAILED_REGISTRATION"


# ---------------------------------------------------------------------------
# RegisteredModel 스키마
# ---------------------------------------------------------------------------

class RegisteredModelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    owner: str | None = None
    storage_location: str | None = None
    datasource_id: int | None = None


class RegisteredModelUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    owner: str | None = None


class RegisteredModelResponse(BaseModel):
    id: int
    name: str
    urn: str
    datasource_id: int | None = None
    description: str | None = None
    owner: str | None = None
    storage_location: str | None = None
    max_version_number: int
    status: str
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    updated_by: str | None = None

    model_config = {"from_attributes": True}


class PaginatedRegisteredModels(BaseModel):
    items: list[RegisteredModelResponse]
    total: int
    page: int
    page_size: int


class ModelSummary(BaseModel):
    """모델 목록 화면용 조인 요약 (registered_models + 최신 버전 + catalog_models)."""

    id: int
    name: str
    description: str | None = None
    owner: str | None = None
    max_version_number: int
    status: str
    # 최신 버전 정보
    latest_version_status: str | None = None
    # catalog_models 에서 (최신 버전)
    sklearn_version: str | None = None
    python_version: str | None = None
    model_size_bytes: int | None = None
    download_count: int = 0
    updated_at: datetime


class PaginatedModelSummaries(BaseModel):
    items: list[ModelSummary]
    total: int
    page: int
    page_size: int


class ModelVersionStatusCount(BaseModel):
    status: str
    count: int


class ModelSizeInfo(BaseModel):
    model_name: str
    model_size_bytes: int


class ModelVersionCount(BaseModel):
    model_name: str
    version_count: int


class DataPoint(BaseModel):
    date: str
    count: int


class ModelStats(BaseModel):
    """MLflow Models 페이지용 대시보드 통계."""

    total_models: int
    total_versions: int
    ready_models: int
    ready_versions: int
    pending_count: int
    failed_count: int
    total_download: int
    status_distribution: list[ModelVersionStatusCount]
    model_sizes: list[ModelSizeInfo]
    versions_per_model: list[ModelVersionCount]
    daily_download_1d: list[DataPoint]
    daily_download_7d: list[DataPoint]
    daily_download_30d: list[DataPoint]
    download_by_model: dict[str, int]
    total_publish: int
    daily_publish_1d: list[DataPoint]
    daily_publish_7d: list[DataPoint]
    daily_publish_30d: list[DataPoint]


class CatalogModelDetail(BaseModel):
    """catalog_models 테이블에서 파싱된 메타데이터."""

    predict_fn: str | None = None
    python_version: str | None = None
    serialization_format: str | None = None
    sklearn_version: str | None = None
    mlflow_version: str | None = None
    mlflow_model_id: str | None = None
    model_size_bytes: int | None = None
    utc_time_created: str | None = None
    requirements: str | None = None
    conda: str | None = None
    python_env: str | None = None
    source_type: str | None = None


class ModelDetailResponse(BaseModel):
    """최신 버전 메타데이터를 포함한 모델 전체 상세."""

    id: int
    name: str
    urn: str
    description: str | None = None
    owner: str | None = None
    storage_type: str = "local"
    storage_location: str | None = None
    max_version_number: int
    status: str
    created_at: datetime
    updated_at: datetime
    latest_version_status: str | None = None
    catalog: CatalogModelDetail | None = None
    download_count: int = 0


class DownloadLogEntry(BaseModel):
    """단일 다운로드 로그 항목."""

    downloaded_at: datetime
    version: int
    download_type: str
    client_ip: str | None = None
    user_agent: str | None = None


class ModelDownloadStats(BaseModel):
    """단일 모델의 다운로드 통계."""

    total_download: int
    daily_download: list[DataPoint]
    recent_logs: list[DownloadLogEntry]


# ---------------------------------------------------------------------------
# ModelVersion 스키마
# ---------------------------------------------------------------------------

class ModelVersionCreate(BaseModel):
    model_name: str = Field(..., min_length=1, max_length=255)
    source: str | None = None
    run_id: str | None = None
    run_link: str | None = None
    description: str | None = None


class ModelVersionUpdate(BaseModel):
    description: str | None = None
    source: str | None = None


class ModelVersionFinalize(BaseModel):
    status: ModelVersionStatus = Field(
        ..., description="READY or FAILED_REGISTRATION"
    )
    status_message: str | None = None


class ModelVersionResponse(BaseModel):
    id: int
    model_id: int
    model_name: str
    version: int
    source: str | None = None
    run_id: str | None = None
    run_link: str | None = None
    description: str | None = None
    status: str
    status_message: str | None = None
    storage_location: str | None = None
    artifact_count: int = 0
    artifact_size: int = 0
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    updated_by: str | None = None

    model_config = {"from_attributes": True}


class PaginatedModelVersions(BaseModel):
    items: list[ModelVersionResponse]
    total: int
    page: int
    page_size: int
