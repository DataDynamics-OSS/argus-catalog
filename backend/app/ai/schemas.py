"""AI 메타데이터 생성 API 용 Pydantic 스키마."""


from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 요청 스키마
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    """생성 엔드포인트 공통 요청 바디."""
    apply: bool = Field(False, description="Apply generated metadata directly (True) or preview only (False)")
    force: bool = Field(False, description="Regenerate even if metadata already exists")
    language: str | None = Field(None, description="Target language override (en, ko, etc.)")


class BulkGenerateRequest(BaseModel):
    """Request body for bulk generation."""
    generation_types: list[str] = Field(
        default=["description"],
        description="Types to generate: description, columns, tags, pii",
    )
    apply: bool = False
    language: str | None = None
    datasource_id: int | None = Field(None, description="Filter by datasource ID")
    empty_only: bool = Field(True, description="Only process datasets with empty descriptions")


# ---------------------------------------------------------------------------
# Response schemas
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
