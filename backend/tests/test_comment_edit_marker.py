# SPDX-License-Identifier: Apache-2.0
"""댓글 "(수정됨)" 표시 기준 회귀 테스트 (in-memory SQLite).

화면은 updated_at 이 created_at 보다 뒤면 "(수정됨)" 을 표시한다. 답글 생성으로 부모의
reply_count 를 갱신할 때 onupdate 로 부모 updated_at 이 바뀌면 수정하지 않은 댓글에
"(수정됨)" 이 붙으므로, 답글 수 갱신은 updated_at 을 유지해야 한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.comments import service
from app.comments.models import Comment
from app.comments.schemas import CommentCreate, CommentUpdate

OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _run() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: Comment.__table__.create(sc))
    make = async_sessionmaker(engine, expire_on_commit=False)

    async with make() as s:
        parent = await service.create_comment(
            s, CommentCreate(entity_type="dataset", entity_id="1", content="<p>a</p>"))
        await s.execute(update(Comment).where(Comment.id == parent.id)
                        .values(created_at=OLD, updated_at=OLD))
        await s.commit()

    async with make() as s:
        await service.create_comment(s, CommentCreate(
            entity_type="dataset", entity_id="1", parent_id=parent.id, content="<p>b</p>"))

    async with make() as s:
        row = await s.get(Comment, parent.id)
        assert row.reply_count == 1
        assert row.updated_at.replace(tzinfo=timezone.utc) == OLD, row.updated_at  # 답글로 바뀌지 않음

    async with make() as s:
        await service.update_comment(s, parent.id, CommentUpdate(content="<p>a2</p>"))
    async with make() as s:
        row = await s.get(Comment, parent.id)
        assert row.content == "<p>a2</p>"
        assert row.updated_at.replace(tzinfo=timezone.utc) > OLD  # 본문 수정은 반영
    await engine.dispose()


def test_comment_edit_marker() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_comment_edit_marker()
    print("test_comment_edit_marker OK")
