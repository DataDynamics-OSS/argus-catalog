# SPDX-License-Identifier: Apache-2.0
"""AI 메타데이터 생성 API 용 Pydantic 스키마."""


from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 요청 스키마
# ---------------------------------------------------------------------------

class AiReadyDraftRequest(BaseModel):
    """AI-Ready 초안 요청 — 항목을 비우면 편향 · 한계 · 관련법령 · 개인정보 보호조치 · 허용 이용 유형 모두.
    ``force`` 면 값이 있는 항목도 다시 쓴다."""
    fields: list[str] | None = Field(
        None, description="data_biases, known_limitations, legal_basis, privacy_measures, allowed_uses")
    force: bool = False
    language: str | None = None


class AiReadyDraft(BaseModel):
    field: str
    label: str
    text: str
    log_id: int
    reason: str | None = None  # 고른 근거 — 관련법령 · 허용 이용 유형


class AiReadySkipped(BaseModel):
    field: str
    label: str
    reason: str


class AiReadyDraftResponse(BaseModel):
    """만든 초안(적용 대기 제안)과 만들지 않은 항목 · 사유. ``legal_reason`` 은 관련법령을 고른 근거."""
    dataset_id: int
    drafts: list[AiReadyDraft] = Field(default_factory=list)
    skipped: list[AiReadySkipped] = Field(default_factory=list)
    legal_reason: str | None = None
    confidence: float | None = None


class GenerateRequest(BaseModel):
    """생성 엔드포인트 공통 요청 바디."""
    apply: bool = Field(False, description="Apply generated metadata directly (True) or preview only (False)")
    force: bool = Field(False, description="Regenerate even if metadata already exists")
    language: str | None = Field(None, description="Target language override (en, ko, etc.)")


class BulkGenerateRequest(BaseModel):
    """일괄 생성 요청 바디 — generation_types 는 description / columns / tags / pii / ai_ready 중 선택.

    ``ai_ready`` 는 편향 · 한계 · 관련법령 · 개인정보 보호조치 · 허용 이용 유형 초안(TODO-096) — ``apply`` 와 관계없이 제안으로만
    남고, ``empty_only`` 면 그 항목 중 하나라도 빈 데이터셋이 대상이다(다른 유형과 함께 고르면 설명이 빈 데이터셋)."""
    generation_types: list[str] = Field(
        default=["description"],
        description="Types to generate: description, columns, tags, pii, ai_ready",
    )
    apply: bool = False
    language: str | None = None
    datasource_id: int | None = Field(None, description="Filter by datasource ID")
    empty_only: bool = Field(True, description="Only process datasets with empty descriptions")


# ---------------------------------------------------------------------------
# 응답 스키마
# ---------------------------------------------------------------------------

class ColumnDescriptionResult(BaseModel):
    field_path: str
    description: str
    confidence: float
    had_existing: bool
    log_id: int | None = None


class GenerateDescriptionResponse(BaseModel):
    dataset_id: int
    description: str = ""
    confidence: float = 0.0
    applied: bool = False
    skipped: bool = False
    reason: str | None = None
    log_id: int | None = None


class GenerateSummaryResponse(BaseModel):
    dataset_id: int
    summary: str = ""
    confidence: float = 0.0
    applied: bool = False
    skipped: bool = False
    reason: str | None = None
    log_id: int | None = None


class GenerateColumnsResponse(BaseModel):
    dataset_id: int
    columns: list[ColumnDescriptionResult] = Field(default_factory=list)
    total_generated: int = 0
    applied: bool = False
    skipped: bool = False
    reason: str | None = None


class TagSuggestion(BaseModel):
    name: str
    description: str = ""


class TagSuggestionResponse(BaseModel):
    dataset_id: int
    suggested_tags: list[str] = Field(default_factory=list)
    new_tags: list[TagSuggestion] = Field(default_factory=list)
    applied_tags: list[str] = Field(default_factory=list)
    created_tags: list[str] = Field(default_factory=list)
    applied: bool = False
    log_id: int | None = None


class PIIColumnResult(BaseModel):
    name: str
    pii_type: str
    confidence: float
    reason: str = ""


class PIIDetectionResponse(BaseModel):
    dataset_id: int
    pii_columns: list[PIIColumnResult] = Field(default_factory=list)
    applied: bool = False
    log_id: int | None = None


class GenerateAllResponse(BaseModel):
    dataset_id: int
    description: dict
    columns: dict
    tags: dict
    pii: dict


class BulkGenerateResponse(BaseModel):
    total: int
    processed: int
    errors: int
    results: list[dict] = Field(default_factory=list)


class SuggestionItem(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    field_name: str | None = None
    generation_type: str
    generated_text: str
    provider: str
    model: str
    created_at: str | None = None
    # 적용하면 바뀔 대상의 지금 값 — 화면에서 제안값과 나란히 비교한다.
    # 설명·요약·컬럼 설명은 평문, 태그는 현재 태그 이름 JSON 배열, PII 는
    # 제안에 나온 컬럼별 현재 pii_type JSON 배열([{"name", "pii_type"}]). 없으면 None.
    current_text: str | None = None
    reason: str | None = None  # 제안 근거 — AI-Ready 초안의 관련법령 · 허용 이용 유형을 고른 이유


class ApplyRejectResponse(BaseModel):
    id: int
    applied: bool = False
    rejected: bool = False
    already_applied: bool = False


class ApplySuggestionsRequest(BaseModel):
    """여러 제안을 한 번에 적용하기 위한 요청 — 컬럼 설명 일괄 적용 등."""
    suggestion_ids: list[int] = Field(default_factory=list)


class ApplySuggestionsResponse(BaseModel):
    applied_ids: list[int] = Field(default_factory=list)
    count: int = 0


class AIStatsResponse(BaseModel):
    total_generations: int
    applied_count: int
    pending_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    description_coverage: dict
    by_type: dict
    provider: str | None = None
    model: str | None = None


# ---------------------------------------------------------------------------
# AI 어시스턴트 답변 평가 (TODO-068)
# ---------------------------------------------------------------------------

class AssistantFeedbackRequest(BaseModel):
    """답변 평가 — ``rating`` 이 null 이면 평가를 지운다."""
    answer_id: str = Field(min_length=1, max_length=64)
    conversation_id: str | None = Field(None, max_length=100)
    rating: Literal["up", "down"] | None
    comment: str | None = Field(None, max_length=2000)
    question: str | None = None
    answer: str | None = None


class AssistantFeedbackResponse(BaseModel):
    answer_id: str
    rating: Literal["up", "down"] | None
