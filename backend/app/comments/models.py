"""댓글(Comment) ORM 모델.

entity_type + entity_id 를 통해 어떤 엔티티(dataset, model, glossary 등)에도 붙일 수 있는
다형성(polymorphic) 댓글 시스템. 효율적인 조회를 위해 root_id 로 중첩 답글을 지원한다.

계층 구조:
  - parent_id=NULL, root_id=NULL → 최상위 댓글(페이지네이션 대상)
  - parent_id=X, root_id=X      → 댓글 X 에 대한 직접 답글
  - parent_id=Y, root_id=X      → 루트 댓글 X 아래의 답글 Y 에 대한 답글
"""

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func,
)

from app.core.database import Base


class Comment(Base):
    __tablename__ = "catalog_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 다형성 엔티티 참조 (FK 없음 — 어떤 엔티티에도 동작)
    entity_type = Column(String(50), nullable=False, index=True)
    entity_id = Column(String(255), nullable=False, index=True)

    # 계층 구조
    parent_id = Column(Integer, ForeignKey("catalog_comments.id", ondelete="CASCADE"))
    root_id = Column(Integer, ForeignKey("catalog_comments.id", ondelete="CASCADE"))
    depth = Column(Integer, nullable=False, default=0)

    # 본문
    content = Column(Text, nullable=False)
    content_plain = Column(Text)
    # 댓글 분류: general, suggestion, feature, bug
    category = Column(String(20), nullable=False, default="general")

    # 작성자
    author_name = Column(String(100), nullable=False)
    author_email = Column(String(255))
    author_avatar = Column(String(500))

    # 비정규화된 답글 수 (답글 생성/삭제 시 갱신)
    reply_count = Column(Integer, nullable=False, default=0)

    # 타임스탬프
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 소프트 삭제
    is_deleted = Column(Boolean, nullable=False, default=False)
