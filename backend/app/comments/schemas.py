# SPDX-License-Identifier: Apache-2.0
"""댓글 요청/응답 검증용 Pydantic 스키마."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.html_sanitize import RequiredRichHtml


class CommentCreate(BaseModel):
    """댓글·답글 생성 요청 — ``parent_id`` 가 있으면 그 댓글에 대한 답글."""

    entity_type: str = Field(..., description="Entity type (dataset, model, glossary, ...)")
    entity_id: str = Field(..., description="Entity identifier")
    parent_id: int | None = Field(None, description="Parent comment ID (for replies)")
    # 저장 전에 허용 목록으로 정화한다(TODO-027) — 스크립트·이벤트 속성·javascript: URL 제거
    content: RequiredRichHtml = Field(..., min_length=1, description="HTML content from Tiptap editor")
    content_plain: str | None = Field(None, description="Plain text for search")
    category: str = Field("general", description="Comment category: general, suggestion, feature, bug")
    # 하위 호환용으로만 받는다 — 서버가 인증 사용자로 작성자를 정하므로 값은 무시된다(사칭 방지).
    author_name: str | None = Field(None, description="Ignored — author is the authenticated user")
    author_email: str | None = None
    author_avatar: str | None = None


class CommentUpdate(BaseModel):
    """댓글 수정 요청 — 본문은 필수, ``content_plain``·``category`` 는 None 이면 기존 값 유지."""

    content: RequiredRichHtml = Field(..., min_length=1)
    content_plain: str | None = None
    category: str | None = None


class CommentResponse(BaseModel):
    """댓글 응답 — ``replies`` 에 하위 답글이 재귀적으로 중첩된다."""

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
    # 오류 신고·개선 요청 처리 (TODO-096 4단계) — 처리 대상이 아니면 status 가 None
    status: str | None = None
    due_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution: str | None = None
    handled_by: str | None = None
    overdue: bool = False
    replies: list["CommentResponse"] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PaginatedComments(BaseModel):
    """최상위 댓글 기준 페이지 응답(``total`` 도 최상위 댓글 수)."""

    items: list[CommentResponse]
    total: int
    page: int
    page_size: int


class ReportStatusUpdate(BaseModel):
    """오류 신고·개선 요청의 처리 상태 변경 — 소명(REJECTED)은 사유가 필요하다."""

    status: str = Field(..., description="OPEN · IN_PROGRESS · RESOLVED · REJECTED")
    resolution: str | None = Field(None, max_length=4000, description="처리 결과 또는 소명")


class ReportItem(BaseModel):
    """신고 목록의 한 줄 — 데이터셋 이름은 라우터가 붙인다."""

    id: int
    entity_type: str
    entity_id: str
    entity_name: str | None = None
    entity_path: str | None = None
    category: str
    content_plain: str | None = None
    author_name: str
    created_at: datetime
    status: str
    due_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution: str | None = None
    handled_by: str | None = None
    overdue: bool = False
    reply_count: int = 0


class ReportPage(BaseModel):
    items: list[ReportItem]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]
