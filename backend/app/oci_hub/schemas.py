"""OCI 모델 허브 Pydantic 스키마(요청·응답 DTO).

라우터(``app/oci_hub/router.py``) 와 서비스(``app/oci_hub/service.py``) 가
공유하는 모델 / 버전 / 태그 / 리니지 / 임포트 DTO 를 모아둔다.
"""

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# OCI 모델
# ---------------------------------------------------------------------------

class OciModelCreate(BaseModel):
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


class OciModelUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    task: str | None = None
    framework: str | None = None
    language: str | None = None
    license: str | None = None
    owner: str | None = None
    status: str | None = None


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
    source_type: str = Field(..., description="dataset, model, or external")
    source_id: str = Field(...)
    source_name: str | None = None
    relation_type: str = Field(..., description="trained_on, derived_from, fine_tuned_from, distilled_from")
    description: str | None = None


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
    name: str | None = Field(None, description="Override model name (default: hf_model_id)")
    description: str | None = None
    owner: str | None = None
    task: str | None = None
    framework: str | None = None
    language: str | None = None
    revision: str = "main"


class ImportResponse(BaseModel):
    name: str
    version: int
    file_count: int
    total_size: int
    status: str
