# SPDX-License-Identifier: Apache-2.0
"""데이터셋 분류체계(Taxonomy) API. catalog_router 와 동일 prefix(/api/v1/catalog)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import taxonomy_service as svc
from app.catalog.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    DatasetCategoryRef,
    TaxonomyCreate,
    TaxonomyResponse,
    TaxonomyTreeResponse,
    TaxonomyUpdate,
)
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog-taxonomy"])


# ---------------------------------------------------------------------------
# 분류체계(Taxonomy)
# ---------------------------------------------------------------------------

@router.get("/taxonomies", response_model=list[TaxonomyResponse])
async def list_taxonomies(session: AsyncSession = Depends(get_session)):
    return await svc.list_taxonomies(session)


@router.post("/taxonomies", response_model=TaxonomyResponse, status_code=201)
async def create_taxonomy(req: TaxonomyCreate, session: AsyncSession = Depends(get_session)):
    return await svc.create_taxonomy(session, req)


@router.put("/taxonomies/{taxonomy_id}", response_model=TaxonomyResponse)
async def update_taxonomy(taxonomy_id: int, req: TaxonomyUpdate, session: AsyncSession = Depends(get_session)):
    tx = await svc.update_taxonomy(session, taxonomy_id, req)
    if tx is None:
        raise HTTPException(status_code=404, detail="분류체계를 찾을 수 없습니다.")
    return tx


@router.delete("/taxonomies/{taxonomy_id}")
async def delete_taxonomy(taxonomy_id: int, session: AsyncSession = Depends(get_session)):
    count = await svc.count_taxonomy_categories(session, taxonomy_id)
    if count > 0:
        raise HTTPException(status_code=409, detail=f"분류체계를 삭제할 수 없습니다: 하위 분류 {count}개가 있습니다.")
    if not await svc.delete_taxonomy(session, taxonomy_id):
        raise HTTPException(status_code=404, detail="분류체계를 찾을 수 없습니다.")
    return {"status": "ok", "message": "Taxonomy deleted"}


@router.get("/taxonomies/{taxonomy_id}/categories", response_model=list[CategoryResponse])
async def list_categories(taxonomy_id: int, session: AsyncSession = Depends(get_session)):
    return await svc.list_categories(session, taxonomy_id)


@router.get("/taxonomies/{taxonomy_id}/tree", response_model=TaxonomyTreeResponse)
async def get_taxonomy_tree(taxonomy_id: int, session: AsyncSession = Depends(get_session)):
    tree = await svc.get_taxonomy_tree(session, taxonomy_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="분류체계를 찾을 수 없습니다.")
    return tree


# ---------------------------------------------------------------------------
# 분류(Category)
# ---------------------------------------------------------------------------

@router.post("/categories", response_model=CategoryResponse, status_code=201)
async def create_category(req: CategoryCreate, session: AsyncSession = Depends(get_session)):
    try:
        return await svc.create_category(session, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(category_id: int, req: CategoryUpdate, session: AsyncSession = Depends(get_session)):
    try:
        cat = await svc.update_category(session, category_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if cat is None:
        raise HTTPException(status_code=404, detail="분류를 찾을 수 없습니다.")
    return cat


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, session: AsyncSession = Depends(get_session)):
    count = await svc.count_category_children(session, category_id)
    if count > 0:
        raise HTTPException(status_code=409, detail=f"분류를 삭제할 수 없습니다: 하위 분류 {count}개가 있습니다.")
    if not await svc.delete_category(session, category_id):
        raise HTTPException(status_code=404, detail="분류를 찾을 수 없습니다.")
    return {"status": "ok", "message": "Category deleted"}


# ---------------------------------------------------------------------------
# 데이터셋 ↔ 분류 매핑
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/categories", response_model=list[DatasetCategoryRef])
async def list_dataset_categories(dataset_id: int, session: AsyncSession = Depends(get_session)):
    return await svc.list_dataset_categories(session, dataset_id)


@router.post("/datasets/{dataset_id}/categories/{category_id}")
async def add_dataset_category(dataset_id: int, category_id: int, session: AsyncSession = Depends(get_session)):
    if not await svc.add_dataset_category(session, dataset_id, category_id):
        raise HTTPException(status_code=404, detail="데이터셋 또는 분류를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/datasets/{dataset_id}/categories/{category_id}")
async def remove_dataset_category(dataset_id: int, category_id: int, session: AsyncSession = Depends(get_session)):
    await svc.remove_dataset_category(session, dataset_id, category_id)
    return {"status": "ok"}
