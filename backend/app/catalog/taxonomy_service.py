# SPDX-License-Identifier: Apache-2.0
"""데이터셋 분류체계(Taxonomy) 서비스 — 다중 분류체계, 카테고리 트리, N:M 매핑."""

from __future__ import annotations

import logging
import re

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Category, Dataset, DatasetCategory, Taxonomy
from app.catalog.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    DatasetCategoryRef,
    TaxonomyCreate,
    TaxonomyResponse,
    TaxonomyTreeCategory,
    TaxonomyTreeResponse,
    TaxonomyUpdate,
)

logger = logging.getLogger(__name__)


class DuplicateCategoryNameError(ValueError):
    """같은 분류체계·같은 상위 아래에 같은 이름의 분류가 이미 있음 (라우터에서 409)."""


async def _ensure_unique_sibling_name(
    session: AsyncSession, taxonomy_id: int, parent_id: int | None, name: str,
    exclude_id: int | None = None,
) -> None:
    """형제 분류 이름 중복 검사.

    DB UNIQUE(taxonomy_id, parent_id, name) 는 parent_id 가 NULL 인 루트 분류끼리를
    서로 다른 값으로 보아 중복을 막지 못하므로 서비스에서 루트·하위 모두 검사한다.
    """
    stmt = select(Category.id).where(
        Category.taxonomy_id == taxonomy_id,
        Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id,
        Category.name == name,
    )
    if exclude_id is not None:
        stmt = stmt.where(Category.id != exclude_id)
    if await session.scalar(stmt.limit(1)) is not None:
        logger.debug("형제 분류 이름 중복: taxonomy_id=%d parent_id=%s name=%s", taxonomy_id, parent_id, name)
        # 문구는 DB UNIQUE 위반(전역 IntegrityError 핸들러)과 동일하게 맞춘다
        raise DuplicateCategoryNameError("같은 이름(키)의 항목이 이미 있습니다. 다른 값을 사용하세요.")


def _slugify(name: str) -> str:
    """이름 → slug. 영숫자·한글 외 문자 연속은 '-' 하나로 바꾸고, 결과가 비면 "item"."""
    s = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", name.strip().lower()).strip("-")
    return s or "item"


async def _unique_code(session: AsyncSession, model, name: str) -> str:
    """``model.code`` 와 겹치지 않는 slug — 이미 있으면 "-2", "-3" … 을 붙인다."""
    base = _slugify(name)
    code, n = base, 1
    while await session.scalar(select(model.id).where(model.code == code)):
        n += 1
        code = f"{base}-{n}"
    return code


# ---------------------------------------------------------------------------
# 분류체계(Taxonomy)
# ---------------------------------------------------------------------------

async def list_taxonomies(session: AsyncSession) -> list[TaxonomyResponse]:
    """분류체계 전체(sort_order, 이름 순)."""
    rows = (await session.execute(
        select(Taxonomy).order_by(Taxonomy.sort_order, Taxonomy.name)
    )).scalars().all()
    return [TaxonomyResponse.model_validate(t) for t in rows]


async def get_taxonomy(session: AsyncSession, taxonomy_id: int) -> Taxonomy | None:
    return await session.get(Taxonomy, taxonomy_id)


async def create_taxonomy(session: AsyncSession, req: TaxonomyCreate) -> TaxonomyResponse:
    """분류체계를 만들고 커밋한다. code 는 이름 기반 고유 slug."""
    tx = Taxonomy(
        code=await _unique_code(session, Taxonomy, req.name),
        name=req.name, description=req.description, sort_order=req.sort_order,
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    logger.info("분류체계 생성: %s (id=%d)", tx.name, tx.id)
    return TaxonomyResponse.model_validate(tx)


async def update_taxonomy(
    session: AsyncSession, taxonomy_id: int, req: TaxonomyUpdate
) -> TaxonomyResponse | None:
    """요청에 포함된 필드만 반영한다. 없으면 None."""
    tx = await session.get(Taxonomy, taxonomy_id)
    if tx is None:
        return None
    for k, v in req.model_dump(exclude_unset=True).items():
        setattr(tx, k, v)
    await session.commit()
    await session.refresh(tx)
    logger.info("분류체계 수정: id=%d", taxonomy_id)
    return TaxonomyResponse.model_validate(tx)


async def count_taxonomy_categories(session: AsyncSession, taxonomy_id: int) -> int:
    """분류체계에 속한 분류(모든 깊이) 수 — 삭제 가능 여부 판단용."""
    return int(await session.scalar(
        select(func.count(Category.id)).where(Category.taxonomy_id == taxonomy_id)
    ) or 0)


async def delete_taxonomy(session: AsyncSession, taxonomy_id: int) -> bool:
    """분류체계 삭제. 없으면 False. 남은 분류가 있는지는 호출 측(라우터)이 먼저 검사한다."""
    tx = await session.get(Taxonomy, taxonomy_id)
    if tx is None:
        return False
    await session.delete(tx)
    await session.commit()
    logger.info("분류체계 삭제: id=%d", taxonomy_id)
    return True


# ---------------------------------------------------------------------------
# 분류 노드(Category)
# ---------------------------------------------------------------------------

async def list_categories(session: AsyncSession, taxonomy_id: int) -> list[CategoryResponse]:
    """분류체계의 분류 평면 목록(sort_order, 이름 순)."""
    rows = (await session.execute(
        select(Category).where(Category.taxonomy_id == taxonomy_id)
        .order_by(Category.sort_order, Category.name)
    )).scalars().all()
    return [CategoryResponse.model_validate(c) for c in rows]


async def get_category(session: AsyncSession, category_id: int) -> Category | None:
    return await session.get(Category, category_id)


async def create_category(session: AsyncSession, req: CategoryCreate) -> CategoryResponse:
    """분류를 만들고 커밋한다.

    Raises:
        ValueError: 분류체계·상위 분류가 없거나 상위가 다른 분류체계에 속함.
        DuplicateCategoryNameError: 같은 상위 아래 같은 이름이 이미 있음.
    """
    if await session.get(Taxonomy, req.taxonomy_id) is None:
        raise ValueError(f"분류체계를 찾을 수 없습니다: {req.taxonomy_id}")
    if req.parent_id is not None:
        parent = await session.get(Category, req.parent_id)
        if parent is None:
            raise ValueError(f"상위 분류를 찾을 수 없습니다: {req.parent_id}")
        if parent.taxonomy_id != req.taxonomy_id:
            raise ValueError("상위 분류는 같은 분류체계에 속해야 합니다.")
    await _ensure_unique_sibling_name(session, req.taxonomy_id, req.parent_id, req.name)
    cat = Category(
        taxonomy_id=req.taxonomy_id,
        parent_id=req.parent_id,
        code=await _unique_code(session, Category, req.name),
        name=req.name, description=req.description, sort_order=req.sort_order,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    logger.info("분류 생성: %s (id=%d, taxonomy=%d)", cat.name, cat.id, cat.taxonomy_id)
    return CategoryResponse.model_validate(cat)


async def _descendant_category_ids(session: AsyncSession, category_id: int) -> set[int]:
    """주어진 카테고리의 모든 후손 id (자기 자신 제외). 레벨마다 쿼리 1회(BFS)."""
    descendants: set[int] = set()
    frontier = {category_id}
    while frontier:
        rows = (await session.execute(
            select(Category.id).where(Category.parent_id.in_(frontier))
        )).scalars().all()
        nxt = {r for r in rows if r not in descendants}
        descendants |= nxt
        frontier = nxt
    return descendants


async def update_category(
    session: AsyncSession, category_id: int, req: CategoryUpdate
) -> CategoryResponse | None:
    """분류 수정·이동(보낸 필드만 반영). 없으면 None.

    상위 변경 시 자기 자신·다른 분류체계·자기 후손 아래로의 이동은 ``ValueError``,
    이동/이름 변경 후 형제 이름이 겹치면 ``DuplicateCategoryNameError``.
    """
    cat = await session.get(Category, category_id)
    if cat is None:
        return None
    data = req.model_dump(exclude_unset=True)
    if "parent_id" in data:
        new_parent = data["parent_id"]
        if new_parent is not None:
            if new_parent == category_id:
                raise ValueError("분류는 자기 자신을 상위로 지정할 수 없습니다.")
            parent = await session.get(Category, new_parent)
            if parent is None:
                raise ValueError(f"상위 분류를 찾을 수 없습니다: {new_parent}")
            if parent.taxonomy_id != cat.taxonomy_id:
                raise ValueError("상위 분류는 같은 분류체계에 속해야 합니다.")
            if new_parent in await _descendant_category_ids(session, category_id):
                raise ValueError("분류를 자신의 하위 분류 아래로 이동할 수 없습니다(순환).")
    if "name" in data or "parent_id" in data:
        # 이름 변경·이동 후 위치에서 형제 이름 중복 검사 (루트 포함)
        await _ensure_unique_sibling_name(
            session, cat.taxonomy_id, data.get("parent_id", cat.parent_id),
            data.get("name", cat.name), exclude_id=category_id,
        )
    for k, v in data.items():
        setattr(cat, k, v)
    await session.commit()
    await session.refresh(cat)
    logger.info("분류 수정: id=%d fields=%s", category_id, sorted(data))
    return CategoryResponse.model_validate(cat)


async def count_category_children(session: AsyncSession, category_id: int) -> int:
    """직계 하위 분류 수(손자 이하 제외)."""
    return int(await session.scalar(
        select(func.count(Category.id)).where(Category.parent_id == category_id)
    ) or 0)


async def delete_category(session: AsyncSession, category_id: int) -> bool:
    """분류 삭제. 없으면 False. 하위 분류 존재 여부는 호출 측이 먼저 검사한다(FK RESTRICT)."""
    cat = await session.get(Category, category_id)
    if cat is None:
        return False
    await session.delete(cat)  # 매핑(catalog_dataset_categories)은 CASCADE
    await session.commit()
    logger.info("분류 삭제: id=%d", category_id)
    return True


# ---------------------------------------------------------------------------
# 트리 (서브트리 롤업 데이터셋 수 + 미분류)
# ---------------------------------------------------------------------------

async def get_taxonomy_tree(session: AsyncSession, taxonomy_id: int) -> TaxonomyTreeResponse | None:
    """분류 트리를 만든다. 각 노드의 dataset_count 는 하위 분류까지 포함한 distinct 데이터셋 수.

    removed 데이터셋은 모든 수에서 제외한다. 분류체계가 없으면 None.
    """
    tx = await session.get(Taxonomy, taxonomy_id)
    if tx is None:
        return None

    cats = (await session.execute(
        select(Category).where(Category.taxonomy_id == taxonomy_id)
        .order_by(Category.sort_order, Category.name)
    )).scalars().all()
    cat_ids = [c.id for c in cats]

    # category_id → set(dataset_id) 직접 매핑 (이 taxonomy 의 카테고리만).
    # 데이터셋 목록(list_datasets 기본값)과 맞추어 removed 데이터셋은 수에서 제외한다.
    direct: dict[int, set[int]] = {cid: set() for cid in cat_ids}
    if cat_ids:
        rows = (await session.execute(
            select(DatasetCategory.category_id, DatasetCategory.dataset_id)
            .join(Dataset, Dataset.id == DatasetCategory.dataset_id)
            .where(DatasetCategory.category_id.in_(cat_ids), Dataset.status != "removed")
        )).all()
        for cid, did in rows:
            direct.setdefault(cid, set()).add(did)

    children_by_parent: dict[int | None, list[Category]] = {}
    for c in cats:
        children_by_parent.setdefault(c.parent_id, []).append(c)

    # 후위 순회로 서브트리 distinct dataset 집합 누적
    def build(cat: Category) -> tuple[TaxonomyTreeCategory, set[int]]:
        subtree: set[int] = set(direct.get(cat.id, set()))
        child_nodes = []
        for ch in children_by_parent.get(cat.id, []):
            node, ds = build(ch)
            child_nodes.append(node)
            subtree |= ds
        return (
            TaxonomyTreeCategory(
                id=cat.id, name=cat.name, parent_id=cat.parent_id,
                dataset_count=len(subtree), children=child_nodes,
            ),
            subtree,
        )

    roots = [build(c)[0] for c in children_by_parent.get(None, [])]

    # 미분류 = 비제거 데이터셋 − 이 taxonomy 에 매핑된 distinct 비제거 데이터셋
    # (매핑된 removed 데이터셋을 빼면 미분류 수가 목록 건수보다 작아진다)
    total = int(await session.scalar(
        select(func.count(Dataset.id)).where(Dataset.status != "removed")
    ) or 0)
    mapped = 0
    if cat_ids:
        mapped = int(await session.scalar(
            select(func.count(func.distinct(DatasetCategory.dataset_id)))
            .join(Dataset, Dataset.id == DatasetCategory.dataset_id)
            .where(DatasetCategory.category_id.in_(cat_ids), Dataset.status != "removed")
        ) or 0)

    logger.debug("분류 트리: taxonomy_id=%d categories=%d total=%d mapped=%d", taxonomy_id, len(cats), total, mapped)
    return TaxonomyTreeResponse(
        taxonomy=TaxonomyResponse.model_validate(tx),
        categories=roots,
        uncategorized_count=max(0, total - mapped),
    )


# ---------------------------------------------------------------------------
# 데이터셋 ↔ 분류 매핑
# ---------------------------------------------------------------------------

async def list_dataset_categories(session: AsyncSession, dataset_id: int) -> list[DatasetCategoryRef]:
    """데이터셋에 직접 매핑된 분류 목록 — path 는 "분류체계 > 조상 … > 분류"."""
    rows = (await session.execute(
        select(Category.id, Category.name, Taxonomy.id, Taxonomy.name)
        .join(DatasetCategory, DatasetCategory.category_id == Category.id)
        .join(Taxonomy, Taxonomy.id == Category.taxonomy_id)
        .where(DatasetCategory.dataset_id == dataset_id)
        .order_by(Taxonomy.name, Category.name)
    )).all()

    # 분류체계별 카테고리 (id → name, parent_id) 캐시로 매핑 카테고리의 조상 경로 계산.
    cache: dict[int, dict[int, tuple[str, int | None]]] = {}
    result: list[DatasetCategoryRef] = []
    for cid, cname, tid, tname in rows:
        if tid not in cache:
            cats = (await session.execute(
                select(Category.id, Category.name, Category.parent_id).where(Category.taxonomy_id == tid)
            )).all()
            cache[tid] = {r[0]: (r[1], r[2]) for r in cats}
        # 조상 → 자신 순으로 이름 체인 구성
        chain: list[str] = []
        cur: int | None = cid
        seen: set[int] = set()
        # seen: 데이터가 깨져 parent 순환이 있어도 무한 루프에 빠지지 않도록
        while cur is not None and cur in cache[tid] and cur not in seen:
            seen.add(cur)
            name, parent = cache[tid][cur]
            chain.append(name)
            cur = parent
        chain.reverse()
        path = " > ".join([tname, *chain])
        result.append(DatasetCategoryRef(
            category_id=cid, category_name=cname, taxonomy_id=tid, taxonomy_name=tname, path=path,
        ))
    return result


async def add_dataset_category(session: AsyncSession, dataset_id: int, category_id: int) -> bool:
    """데이터셋-분류 매핑 추가(멱등). 데이터셋 또는 분류가 없으면 False."""
    if await session.get(Dataset, dataset_id) is None or await session.get(Category, category_id) is None:
        return False
    exists = await session.scalar(
        select(DatasetCategory.id).where(
            DatasetCategory.dataset_id == dataset_id,
            DatasetCategory.category_id == category_id,
        )
    )
    if not exists:
        session.add(DatasetCategory(dataset_id=dataset_id, category_id=category_id))
        await session.commit()
        logger.info("분류 매핑 추가: dataset_id=%d category_id=%d", dataset_id, category_id)
    else:
        logger.debug("분류 매핑 이미 존재: dataset_id=%d category_id=%d", dataset_id, category_id)
    return True


async def list_direct_dataset_ids(session: AsyncSession, category_id: int) -> list[int] | None:
    """이 분류에 *직접* 매핑된 데이터셋 id 목록(하위 분류 매핑 제외). 분류가 없으면 None.

    분류 화면의 데이터셋 목록은 하위 분류까지 포함하지만, 매핑 해제는 직접 매핑만 가능하다 —
    화면이 "제거" 를 직접 매핑 행에만 보이도록 쓴다.
    """
    from app.catalog.models import Category
    if await session.get(Category, category_id) is None:
        return None
    rows = await session.execute(
        select(DatasetCategory.dataset_id)
        .where(DatasetCategory.category_id == category_id)
        .order_by(DatasetCategory.dataset_id)
    )
    return [r for (r,) in rows.all()]


async def remove_dataset_category(session: AsyncSession, dataset_id: int, category_id: int) -> bool:
    """매핑 삭제. 해당 매핑이 없으면 False (라우터에서 404)."""
    result = await session.execute(
        delete(DatasetCategory).where(
            DatasetCategory.dataset_id == dataset_id,
            DatasetCategory.category_id == category_id,
        )
    )
    await session.commit()
    if result.rowcount:
        logger.info("분류 매핑 해제: dataset_id=%d category_id=%d", dataset_id, category_id)
    return bool(result.rowcount)
