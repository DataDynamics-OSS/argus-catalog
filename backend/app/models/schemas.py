# SPDX-License-Identifier: Apache-2.0
"""MLflow 모델 레지스트리의 Pydantic 스키마(요청·응답 DTO).

``/api/v1/models`` 라우터와 서비스 레이어가 사용하는 요청/응답 모델을 모아둔다.
UC 호환 라우터(``app/models/uc_compat.py``) 는 UC protobuf 포맷과 이 스키마
사이의 변환을 내부적으로 처리한다.
"""

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# 열거형(Enum)
# ---------------------------------------------------------------------------

# 모델 이름 규칙 — 영문·숫자로 시작, 영문·숫자·``.``·``_``·``-`` 만 허용, ``..`` 금지.
# 이름이 아티팩트 디렉터리 경로(``model-artifacts/{name}``)로 그대로 쓰이므로 ``/``·``..`` 같은
# 경로 문자를 막아 다른 디렉터리를 가리키지 못하게 한다(3-part ``catalog.schema.model`` 은 통과).
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_model_name(name: str) -> str:
    """모델 이름 검증. 규칙 위반 시 ``ValueError`` (Pydantic 에서는 422)."""
    if not _MODEL_NAME_RE.match(name) or ".." in name:
        raise ValueError(
            "모델 이름은 영문·숫자로 시작하고 영문·숫자·'.'·'_'·'-' 만 쓸 수 있습니다('..' 불가). "
            "예: argus.ml.iris_classifier"
        )
    return name


# 버전 스테이지 — API 는 대문자만 쓴다(과거 데이터의 'Production' 같은 표기는 읽을 때 정규화).
MODEL_STAGES = ("NONE", "STAGING", "PRODUCTION", "ARCHIVED")


def normalize_stage(stage: str | None) -> str:
    """스테이지 값을 대문자로 정규화한다. 비었거나 알 수 없는 값은 ``"NONE"``."""
    s = (stage or "NONE").strip().upper()
    return s if s in MODEL_STAGES else "NONE"


class ModelVersionStatus(str, Enum):
    """모델 버전 등록 상태 — PENDING_REGISTRATION → READY | FAILED_REGISTRATION."""

    PENDING_REGISTRATION = "PENDING_REGISTRATION"
    READY = "READY"
    FAILED_REGISTRATION = "FAILED_REGISTRATION"


# ---------------------------------------------------------------------------
# RegisteredModel 스키마
# ---------------------------------------------------------------------------

class RegisteredModelCreate(BaseModel):
    """모델 등록 요청."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    owner: str | None = None
    # 아티팩트 저장 경로 — 생략 시 ``model-artifacts/{name}``. 지정하면 그 아래 경로만 허용(서비스에서 검증).
    storage_location: str | None = None
    datasource_id: int | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return validate_model_name(v)


class RegisteredModelUpdate(BaseModel):
    """모델 부분 갱신 요청 — None 인 필드는 바꾸지 않는다(빈 값으로 지우기 불가)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    owner: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return validate_model_name(v) if v is not None else v


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
    # 대표 flavor 와 표시용 프레임워크 문자열 (예: "sklearn 1.5.2")
    flavor: str | None = None
    framework: str | None = None
    # MLmodel 의 model_size_bytes, 없으면 최신 READY 버전의 아티팩트 총 크기로 대체
    model_size_bytes: int | None = None
    download_count: int = 0
    created_by: str | None = None
    updated_at: datetime
    # 운영(PRODUCTION) 스테이지 중 가장 높은 버전 (없으면 None)
    production_version: int | None = None


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
    # 운영(PRODUCTION) 스테이지 버전이 하나라도 있는 활성 모델 수
    production_models: int = 0
    # 최근 30일 다운로드 수 (다운로드 로그 기준)
    download_30d_total: int = 0


class CatalogModelDetail(BaseModel):
    """catalog_models 테이블에서 파싱된 메타데이터."""

    predict_fn: str | None = None
    python_version: str | None = None
    serialization_format: str | None = None
    sklearn_version: str | None = None
    flavor: str | None = None
    flavor_version: str | None = None
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
    created_by: str | None = None
    # MLflow 모델 파일 브라우저(``/dashboard/mlflow-files``) 기준 경로 — 저장소가 그 루트 밖이면 None
    files_path: str | None = None


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
    """버전 확정 요청 — PENDING_REGISTRATION 버전을 READY 또는 FAILED_REGISTRATION 으로."""

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
    # 스테이지 (NONE/STAGING/PRODUCTION/ARCHIVED) 와 마지막 변경 정보
    stage: str = "NONE"
    stage_changed_at: datetime | None = None
    stage_changed_by: str | None = None
    # 파일 정합성 — 로컬 저장소일 때 버전 디렉터리에 파일이 있는지 (None = 확인 불가/원격 저장소)
    files_present: bool | None = None
    # MLflow 모델 파일 브라우저 기준 경로 (루트 밖이면 None)
    files_path: str | None = None

    model_config = {"from_attributes": True}


class PaginatedModelVersions(BaseModel):
    items: list[ModelVersionResponse]
    total: int
    page: int
    page_size: int
