# SPDX-License-Identifier: Apache-2.0
"""OCI 모델 허브 Pydantic 스키마(요청·응답 DTO).

라우터(``app/oci_hub/router.py``) 와 서비스(``app/oci_hub/service.py``) 가
공유하는 모델 / 버전 / 태그 / 리니지 / 임포트 DTO 를 모아둔다.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.schemas import validate_model_name

# 라이프사이클 상태 — 승인(approved)·운영(production) 지정은 관리자만 (라우터 ``_assert_status_allowed`` 에서 검사)
OCI_STATUSES = ("draft", "review", "approved", "production", "deprecated", "archived")
OCI_ADMIN_ONLY_STATUSES = ("approved", "production")
# mlflow = Argus 의 MLflow 등록 모델(source_id 는 모델 이름) — 상세 화면에서 해당 모델로 연결된다.
LINEAGE_SOURCE_TYPES = ("dataset", "model", "mlflow", "external")
LINEAGE_RELATIONS = ("trained_on", "fine_tuned_from", "distilled_from", "derived_from", "converted_from", "produces")


def _check_status(v: str | None) -> str | None:
    """상태 값을 소문자로 정규화하고 ``OCI_STATUSES`` 밖이면 ``ValueError`` (Pydantic 에서는 422)."""
    if v is None:
        return v
    v = v.strip().lower()
    if v not in OCI_STATUSES:
        raise ValueError(f"status 는 {', '.join(OCI_STATUSES)} 중 하나여야 합니다.")
    return v

# ---------------------------------------------------------------------------
# OCI 모델
# ---------------------------------------------------------------------------

class OciModelCreate(BaseModel):
    """OCI 모델 등록 요청. ``source_type`` 이 없으면 서비스에서 ``"my"`` 로 채운다."""

    name: str = Field(..., min_length=1, max_length=255)
    display_name: str | None = None
    description: str | None = None
    readme: str | None = None
    task: str | None = None
    framework: str | None = None
    language: str | None = None
    license: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    owner: str | None = None
    status: str = "draft"

    # 이름은 S3 키 프리픽스(``{name}/``)로 그대로 쓰이므로 ``/``·``..`` 를 막는다(MLflow 모델과 같은 규칙)
    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return validate_model_name(v)

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        return _check_status(v) or "draft"


class OciModelUpdate(BaseModel):
    """OCI 모델 부분 갱신 요청 — 보낸 필드만 반영하고, 명시적 null·빈 문자열은 값 지우기(status 제외)."""

    display_name: str | None = None
    description: str | None = None
    task: str | None = None
    framework: str | None = None
    language: str | None = None
    license: str | None = None
    owner: str | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str | None) -> str | None:
        return _check_status(v)


class OciModelSummary(BaseModel):
    """목록 페이지 카드 뷰 요약."""
    id: int
    name: str
    display_name: str | None = None
    description: str | None = None
    task: str | None = None
    framework: str | None = None
    language: str | None = None
    license: str | None = None
    source_type: str | None = None
    owner: str | None = None
    version_count: int = 0
    total_size: int = 0
    download_count: int = 0
    status: str = "draft"
    created_by: str | None = None
    tags: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OciModelDetail(BaseModel):
    """README · 태그 · 리니지를 포함한 전체 상세."""
    id: int
    name: str
    display_name: str | None = None
    description: str | None = None
    readme: str | None = None
    task: str | None = None
    framework: str | None = None
    language: str | None = None
    license: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    source_revision: str | None = None
    bucket: str | None = None
    storage_prefix: str | None = None
    owner: str | None = None
    created_by: str | None = None
    version_count: int = 0
    total_size: int = 0
    download_count: int = 0
    status: str = "draft"
    tags: list[dict] = Field(default_factory=list)
    lineage: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginatedOciModels(BaseModel):
    items: list[OciModelSummary]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# 버전
# ---------------------------------------------------------------------------

class OciModelVersionResponse(BaseModel):
    id: int
    model_id: int
    version: int
    manifest: str | None = None
    content_digest: str | None = None
    file_count: int = 0
    total_size: int = 0
    extra_metadata: dict | None = None
    status: str = "ready"
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 리니지
# ---------------------------------------------------------------------------

class LineageCreate(BaseModel):
    source_type: str = Field(..., description="dataset, model, mlflow, external")
    source_id: str = Field(..., min_length=1, max_length=255)
    source_name: str | None = Field(None, max_length=255)
    relation_type: str = Field(..., description="trained_on, fine_tuned_from, distilled_from, derived_from, converted_from, produces")
    description: str | None = None

    @field_validator("source_type")
    @classmethod
    def _check_source_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in LINEAGE_SOURCE_TYPES:
            raise ValueError(f"source_type 은 {', '.join(LINEAGE_SOURCE_TYPES)} 중 하나여야 합니다.")
        return v

    @field_validator("relation_type")
    @classmethod
    def _check_relation(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in LINEAGE_RELATIONS:
            raise ValueError(f"relation_type 은 {', '.join(LINEAGE_RELATIONS)} 중 하나여야 합니다.")
        return v


class LineageResponse(BaseModel):
    id: int
    model_id: int
    source_type: str
    source_id: str
    source_name: str | None = None
    relation_type: str
    description: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 임포트
# ---------------------------------------------------------------------------

class HuggingFaceImportRequest(BaseModel):
    hf_model_id: str = Field(..., description="HuggingFace model ID (e.g. bert-base-uncased)")
    name: str | None = Field(None, description="Override model name (default: hf_model_id with '/' replaced by '-')")
    description: str | None = None
    owner: str | None = None
    task: str | None = None
    framework: str | None = None
    language: str | None = None
    revision: str = "main"

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return validate_model_name(v) if v else v


class ImportResponse(BaseModel):
    """HF 가져오기 접수 결과 — 실제 다운로드·업로드는 백그라운드에서 진행(``status``=importing).
    진행 상황은 버전 목록의 해당 버전 ``status`` (importing → ready | failed) 로 확인한다."""
    name: str
    version: int
    file_count: int = 0
    total_size: int = 0
    status: str


class OciFacets(BaseModel):
    """목록 필터 선택지 — 실제 데이터에 있는 값만."""
    tasks: list[str]
    frameworks: list[str]
    languages: list[str]
    statuses: list[str]
