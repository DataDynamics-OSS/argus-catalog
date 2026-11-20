# SPDX-License-Identifier: Apache-2.0
"""데이터셋 소유자 판정 — 생성자(``created_by``) 또는 소유자 목록(``catalog_owners``)에 오른 사용자 (DEF-138).

AI-Ready 알림 수신 · 알림 처리 · 오류 신고 처리 · 배포본 · 수동 판정 · 체크리스트 C30 · 데이터 카드가 모두
이 기준을 쓴다. 그래서 알림을 받는 사람과 처리할 수 있는 사람이 같다.

데이터셋 일반 편집(메타데이터 · 확장 메타데이터 · AI-Ready 항목 · 스키마 · 태그 · 용어 · 샘플 · 품질 · AI 생성 ·
표준 매핑 · 배포본 · 버전 이력 · 변경 소비자)도 같은 기준이다(TODO-154, :func:`assert_dataset_owner_or_admin`).
단 **데이터셋 삭제와 소유자 추가 · 삭제**는 생성자 · 관리자만 한다(:func:`assert_dataset_creator_or_admin`) —
목록 사용자가 다른 사람을 소유자로 올려 권한을 넓히지 못하게 하기 위해서다.

일반 자산 가드(``app.core.auth.assert_owner_or_admin``)는 생성자만 보며 API · MCP · 변경 요청 등 여러 자산이
함께 쓰므로 그대로 둔다.

소유자 목록의 ``owner_name`` 은 저장 시점마다 형식이 다르다 — 화면에서 고르면 ``"성이름 (username)"``,
API · 페더레이션 반입은 ``"username"`` 이 흔하다. 둘 다 사용자 아이디와 맞춘다.
"""

from __future__ import annotations

import logging
import re

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, Owner
from app.core.auth import PermissionDenied

logger = logging.getLogger(__name__)

_PAREN_ID = re.compile(r"\(([^()]+)\)\s*$")


def owner_username(owner_name: str | None) -> str:
    """소유자 목록 이름에서 사용자 아이디 — ``"홍길동 (hong)"`` → ``hong``, ``"hong"`` → ``hong``."""
    name = (owner_name or "").strip()
    m = _PAREN_ID.search(name)
    return m.group(1).strip() if m else name


def owner_matches(owner_name: str | None, username: str | None) -> bool:
    """소유자 목록 이름이 이 사용자를 가리키는지."""
    return bool(username) and owner_username(owner_name) == username


async def managed_dataset_ids(session: AsyncSession, username: str | None, dataset_ids: set[int]) -> set[int]:
    """주어진 데이터셋 중 사용자가 생성자이거나 소유자 목록에 오른 것."""
    if not dataset_ids or not username:
        return set()
    created = set((await session.execute(
        select(Dataset.id).where(Dataset.id.in_(dataset_ids), Dataset.created_by == username)
    )).scalars().all())
    owned = {ds_id for ds_id, name in (await session.execute(
        select(Owner.dataset_id, Owner.owner_name).where(Owner.dataset_id.in_(dataset_ids))
    )).all() if owner_matches(name, username)}
    return created | owned


async def owned_dataset_ids(session: AsyncSession, username: str | None) -> set[int]:
    """사용자가 생성자이거나 소유자 목록에 오른 모든 데이터셋(오류 신고 처리 '내 데이터셋' 필터)."""
    if not username:
        return set()
    created = set((await session.execute(
        select(Dataset.id).where(Dataset.created_by == username)
    )).scalars().all())
    # 소유자 이름은 "username" 또는 "표시명 (username)" — 후보를 좁힌 뒤 같은 규칙으로 맞춘다
    owned = {ds_id for ds_id, name in (await session.execute(
        select(Owner.dataset_id, Owner.owner_name).where(or_(
            Owner.owner_name == username, Owner.owner_name.contains(f"({username})", autoescape=True)))
    )).all() if owner_matches(name, username)}
    return created | owned


async def is_dataset_owner(session: AsyncSession, dataset_id: int, username: str | None) -> bool:
    """사용자가 이 데이터셋의 생성자이거나 소유자 목록에 올랐는지(관리자 여부는 보지 않는다)."""
    return dataset_id in await managed_dataset_ids(session, username, {dataset_id})


async def _dataset_exists_or_404(session: AsyncSession, dataset_id: int) -> str | None:
    """데이터셋 생성자 — 데이터셋이 없으면 404."""
    row = (await session.execute(select(Dataset.created_by).where(Dataset.id == dataset_id))).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="데이터셋을 찾을 수 없습니다.")
    return row[0]


async def assert_dataset_owner_or_admin(session: AsyncSession, current, dataset_id: int) -> None:
    """관리자 · 데이터셋 생성자 · 소유자 목록 사용자면 통과. 데이터셋이 없으면 404, 아니면 403.

    데이터셋 일반 편집의 기준이다(TODO-154). 거부는 권한 감사(``permission.denied``)에 남는다.
    """
    await _dataset_exists_or_404(session, dataset_id)
    if current.is_admin or await is_dataset_owner(session, dataset_id, current.username):
        return
    logger.warning("데이터셋 소유자 거부: user=%s dataset_id=%d", current.username, dataset_id)
    raise PermissionDenied(
        "데이터셋의 소유자(생성자 또는 소유자 목록) 또는 관리자만 수행할 수 있습니다.",
        user=current, kind="OWNER", key="데이터셋 소유자",
    )


async def assert_dataset_creator_or_admin(session: AsyncSession, current, dataset_id: int) -> None:
    """데이터셋 삭제 · 소유자 추가 · 삭제 — 생성자 또는 관리자만(소유자 목록 사용자는 403). 없으면 404.

    목록 사용자가 다른 사람을 소유자로 올리거나 생성자 · 다른 소유자를 빼서 권한을 넓히지 못하게 한다(TODO-154).
    생성자가 비어 있는 과거 데이터는 관리자만.
    """
    created_by = await _dataset_exists_or_404(session, dataset_id)
    if current.is_admin or (created_by and created_by == current.username):
        return
    logger.warning("데이터셋 생성자 거부: user=%s dataset_id=%d creator=%s", current.username, dataset_id, created_by)
    raise PermissionDenied(
        "데이터셋 삭제와 소유자 추가 · 삭제는 데이터셋 생성자 또는 관리자만 수행할 수 있습니다.",
        user=current, kind="OWNER", key="데이터셋 생성자",
    )


async def owner_names(session: AsyncSession, dataset_id: int, created_by: str | None) -> list[tuple[str, str]]:
    """표시용 소유자 — 소유자 목록 ``(이름, 유형)`` 에 생성자가 없으면 ``(생성자, "CREATOR")`` 를 덧붙인다."""
    rows = [(n, t) for n, t in (await session.execute(
        select(Owner.owner_name, Owner.owner_type).where(Owner.dataset_id == dataset_id).order_by(Owner.id)
    )).all()]
    if created_by and not any(owner_matches(n, created_by) for n, _ in rows):
        rows.append((created_by, "CREATOR"))
    return rows
