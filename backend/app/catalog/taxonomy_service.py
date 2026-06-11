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


def _slugify(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", name.strip().lower()).strip("-")
    return s or "item"


async def _unique_code(session: AsyncSession, model, name: str) -> str:
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
    rows = (await session.execute(
        select(Taxonomy).order_by(Taxonomy.sort_order, Taxonomy.name)
    )).scalars().all()
    return [TaxonomyResponse.model_validate(t) for t in rows]


async def get_taxonomy(session: AsyncSession, taxonomy_id: int) -> Taxonomy | None:
    return await session.get(Taxonomy, taxonomy_id)


async def create_taxonomy(session: AsyncSession, req: TaxonomyCreate) -> TaxonomyResponse:
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
    tx = await session.get(Taxonomy, taxonomy_id)
    if tx is None:
        return None
    for k, v in req.model_dump(exclude_unset=True).items():
        setattr(tx, k, v)
    await session.commit()
    await session.refresh(tx)
    return TaxonomyResponse.model_validate(tx)


async def count_taxonomy_categories(session: AsyncSession, taxonomy_id: int) -> int:
    return int(await session.scalar(
        select(func.count(Category.id)).where(Category.taxonomy_id == taxonomy_id)
    ) or 0)


async def delete_taxonomy(session: AsyncSession, taxonomy_id: int) -> bool:
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
    rows = (await session.execute(
        select(Category).where(Category.taxonomy_id == taxonomy_id)
        .order_by(Category.sort_order, Category.name)
    )).scalars().all()
    return [CategoryResponse.model_validate(c) for c in rows]


async def get_category(session: AsyncSession, category_id: int) -> Category | None:
    return await session.get(Category, category_id)


async def create_category(session: AsyncSession, req: CategoryCreate) -> CategoryResponse:
    if await session.get(Taxonomy, req.taxonomy_id) is None:
        raise ValueError(f"분류체계를 찾을 수 없습니다: {req.taxonomy_id}")
    if req.parent_id is not None:
        parent = await session.get(Category, req.parent_id)
        if parent is None:
            raise ValueError(f"상위 분류를 찾을 수 없습니다: {req.parent_id}")
        if parent.taxonomy_id != req.taxonomy_id:
            raise ValueError("상위 분류는 같은 분류체계에 속해야 합니다.")
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
    """주어진 카테고리의 모든 후손 id (자기 자신 제외)."""
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
    for k, v in data.items():
        setattr(cat, k, v)
    await session.commit()
    await session.refresh(cat)
    return CategoryResponse.model_validate(cat)


async def count_category_children(session: AsyncSession, category_id: int) -> int:
    return int(await session.scalar(
        select(func.count(Category.id)).where(Category.parent_id == category_id)
    ) or 0)


async def delete_category(session: AsyncSession, category_id: int) -> bool:
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
    tx = await session.get(Taxonomy, taxonomy_id)
    if tx is None:
        return None

    cats = (await session.execute(
        select(Category).where(Category.taxonomy_id == taxonomy_id)
        .order_by(Category.sort_order, Category.name)
    )).scalars().all()
    cat_ids = [c.id for c in cats]

    # category_id → set(dataset_id) 직접 매핑 (이 taxonomy 의 카테고리만)
    direct: dict[int, set[int]] = {cid: set() for cid in cat_ids}
    if cat_ids:
        rows = (await session.execute(
            select(DatasetCategory.category_id, DatasetCategory.dataset_id)
            .where(DatasetCategory.category_id.in_(cat_ids))
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

    # 미분류 = 비제거 데이터셋 − 이 taxonomy 에 매핑된 distinct 데이터셋
    total = int(await session.scalar(
        select(func.count(Dataset.id)).where(Dataset.status != "removed")
    ) or 0)
    mapped = 0
    if cat_ids:
        mapped = int(await session.scalar(
            select(func.count(func.distinct(DatasetCategory.dataset_id)))
            .where(DatasetCategory.category_id.in_(cat_ids))
        ) or 0)

    return TaxonomyTreeResponse(
        taxonomy=TaxonomyResponse.model_validate(tx),
        categories=roots,
        uncategorized_count=max(0, total - mapped),
    )


# ---------------------------------------------------------------------------
# 데이터셋 ↔ 분류 매핑
# ---------------------------------------------------------------------------

async def list_dataset_categories(session: AsyncSession, dataset_id: int) -> list[DatasetCategoryRef]:
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
    return True


async def remove_dataset_category(session: AsyncSession, dataset_id: int, category_id: int) -> None:
    await session.execute(
        delete(DatasetCategory).where(
            DatasetCategory.dataset_id == dataset_id,
            DatasetCategory.category_id == category_id,
        )
    )
    await session.commit()
