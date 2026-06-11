"""댓글 요청/응답 검증용 Pydantic 스키마."""

from datetime import datetime

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    entity_type: str = Field(..., description="Entity type (dataset, model, glossary, ...)")
    entity_id: str = Field(..., description="Entity identifier")
    parent_id: int | None = Field(None, description="Parent comment ID (for replies)")
    content: str = Field(..., min_length=1, description="HTML content from Tiptap editor")
    content_plain: str | None = Field(None, description="Plain text for search")
    category: str = Field("general", description="Comment category: general, suggestion, feature, bug")
    author_name: str = Field(..., min_length=1, description="Author display name")
    author_email: str | None = None
    author_avatar: str | None = None


class CommentUpdate(BaseModel):
    content: str = Field(..., min_length=1)
    content_plain: str | None = None
    category: str | None = None


class CommentResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: str
    parent_id: int | None = None
    root_id: int | None = None
    depth: int = 0
    content: str
    content_plain: str | None = None
    category: str = "general"
    author_name: str
    author_email: str | None = None
    author_avatar: str | None = None
    reply_count: int = 0
    created_at: datetime
    updated_at: datetime
    is_deleted: bool = False
    replies: list["CommentResponse"] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PaginatedComments(BaseModel):
    items: list[CommentResponse]
    total: int
    page: int
    page_size: int
