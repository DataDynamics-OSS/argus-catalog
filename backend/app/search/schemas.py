"""시맨틱/하이브리드 검색 API 용 Pydantic 스키마."""

from pydantic import BaseModel, Field

from app.catalog.schemas import DatasetSummary


class SemanticSearchResult(BaseModel):
    """연관도 점수를 포함한 단일 검색 결과."""
    dataset: DatasetSummary
    score: float = Field(..., description="Relevance score (0.0 - 1.0)")
    match_type: str = Field(..., description="'semantic', 'keyword', or 'hybrid'")


class SemanticSearchResponse(BaseModel):
    """결과와 메타데이터를 포함한 검색 응답."""
    items: list[SemanticSearchResult]
    total: int
    query: str
    provider: str | None = None
    model: str | None = None


class EntitySearchResult(BaseModel):
    """비-데이터셋 엔티티(용어집/AI Agent/API) 검색 결과."""
    entity_type: str = Field(..., description="'glossary_term', 'ai_agent', or 'api'")
    id: int
    name: str
    display_name: str | None = None
    description: str | None = None
    extra: dict = Field(default_factory=dict, description="타입별 부가 필드")
    score: float = Field(..., description="Relevance score (0.0 - 1.0)")
    match_type: str = Field(..., description="'semantic', 'keyword', or 'hybrid'")


class UnifiedSearchResponse(BaseModel):
    """데이터셋 + 용어집 + AI Agent + API 통합 검색 응답."""
    query: str
    provider: str | None = None
    model: str | None = None
    datasets: list[SemanticSearchResult]
    glossary_terms: list[EntitySearchResult]
    ai_agents: list[EntitySearchResult]
    apis: list[EntitySearchResult]
    total: int
