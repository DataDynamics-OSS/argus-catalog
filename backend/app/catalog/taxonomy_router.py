# SPDX-License-Identifier: Apache-2.0
"""데이터셋 분류체계(Taxonomy) API. catalog_router 와 동일 prefix(/api/v1/catalog).

조회는 인증 없이 공개, 생성·수정·삭제·매핑 변경은 ``taxonomies.edit`` 기능 권한이 필요하다.
"""

from __future__ import annotations

import logging
from typing import Annotated

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
from app.core.auth import TokenUser
from app.core.database import get_session
from app.permissions.router import require_feature_granted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog-taxonomy"])

# 분류체계·분류·데이터셋 매핑 편집 — 명시 허용형 기능 권한(미설정이면 admin 전용,
# 권한 관리 › 기능 권한에서 역할에 허용 가능). 조회는 공개.
TAXONOMIES_EDIT_FEATURE = "taxonomies.edit"
TaxonomyEditor = Annotated[TokenUser, Depends(require_feature_granted(TAXONOMIES_EDIT_FEATURE))]


# ---------------------------------------------------------------------------
# 분류체계(Taxonomy)
# ---------------------------------------------------------------------------

@router.get("/taxonomies", response_model=list[TaxonomyResponse])
async def list_taxonomies(session: AsyncSession = Depends(get_session)):
    """분류체계 목록(sort_order, 이름 순)."""
    logger.debug("GET /catalog/taxonomies")
    return await svc.list_taxonomies(session)


@router.post("/taxonomies", response_model=TaxonomyResponse, status_code=201)
async def create_taxonomy(_editor: TaxonomyEditor, req: TaxonomyCreate, session: AsyncSession = Depends(get_session)):
    """분류체계 생성 — code 는 이름으로 자동 생성된다."""
    return await svc.create_taxonomy(session, req)


@router.put("/taxonomies/{taxonomy_id}", response_model=TaxonomyResponse)
async def update_taxonomy(_editor: TaxonomyEditor, taxonomy_id: int, req: TaxonomyUpdate, session: AsyncSession = Depends(get_session)):
    """분류체계 수정 — 보낸 필드만 반영. 없으면 404."""
    tx = await svc.update_taxonomy(session, taxonomy_id, req)
    if tx is None:
        logger.warning("수정 대상 분류체계 없음: taxonomy_id=%d", taxonomy_id)
        raise HTTPException(status_code=404, detail="분류체계를 찾을 수 없습니다.")
    return tx


@router.delete("/taxonomies/{taxonomy_id}")
async def delete_taxonomy(_editor: TaxonomyEditor, taxonomy_id: int, session: AsyncSession = Depends(get_session)):
    """분류체계 삭제 — 분류가 하나라도 있으면 409, 분류체계가 없으면 404."""
    count = await svc.count_taxonomy_categories(session, taxonomy_id)
    if count > 0:
        logger.warning("분류체계 삭제 거부 — 하위 분류 존재: taxonomy_id=%d categories=%d", taxonomy_id, count)
        raise HTTPException(status_code=409, detail=f"분류체계를 삭제할 수 없습니다: 하위 분류 {count}개가 있습니다.")
    if not await svc.delete_taxonomy(session, taxonomy_id):
        logger.warning("삭제 대상 분류체계 없음: taxonomy_id=%d", taxonomy_id)
        raise HTTPException(status_code=404, detail="분류체계를 찾을 수 없습니다.")
    return {"status": "ok", "message": "Taxonomy deleted"}


@router.get("/taxonomies/{taxonomy_id}/categories", response_model=list[CategoryResponse])
async def list_categories(taxonomy_id: int, session: AsyncSession = Depends(get_session)):
    """분류체계의 분류 평면 목록(트리 아님). 분류체계가 없어도 404 가 아니라 빈 목록."""
    logger.debug("분류 목록 조회: taxonomy_id=%d", taxonomy_id)
    return await svc.list_categories(session, taxonomy_id)


@router.get("/taxonomies/{taxonomy_id}/tree", response_model=TaxonomyTreeResponse)
async def get_taxonomy_tree(taxonomy_id: int, session: AsyncSession = Depends(get_session)):
    """분류 트리 + 서브트리 데이터셋 수 + 미분류 수. 분류체계가 없으면 404."""
    logger.debug("분류 트리 조회: taxonomy_id=%d", taxonomy_id)
    tree = await svc.get_taxonomy_tree(session, taxonomy_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="분류체계를 찾을 수 없습니다.")
    return tree


# ---------------------------------------------------------------------------
# 분류(Category)
# ---------------------------------------------------------------------------

@router.post("/categories", response_model=CategoryResponse, status_code=201)
async def create_category(_editor: TaxonomyEditor, req: CategoryCreate, session: AsyncSession = Depends(get_session)):
    """분류 생성 — 이름 중복 409, 분류체계·상위 분류 오류 400."""
    try:
        return await svc.create_category(session, req)
    except svc.DuplicateCategoryNameError as e:
        logger.warning("분류 생성 충돌 — 형제 이름 중복: taxonomy_id=%d name=%s", req.taxonomy_id, req.name)
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        logger.warning("분류 생성 거부: taxonomy_id=%d reason=%s", req.taxonomy_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(_editor: TaxonomyEditor, category_id: int, req: CategoryUpdate, session: AsyncSession = Depends(get_session)):
    """분류 수정·이동 — 이름 중복 409, 순환·다른 분류체계로 이동 등 400, 없으면 404."""
    try:
        cat = await svc.update_category(session, category_id, req)
    except svc.DuplicateCategoryNameError as e:
        logger.warning("분류 수정 충돌 — 형제 이름 중복: category_id=%d", category_id)
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        logger.warning("분류 수정 거부: category_id=%d reason=%s", category_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if cat is None:
        logger.warning("수정 대상 분류 없음: category_id=%d", category_id)
        raise HTTPException(status_code=404, detail="분류를 찾을 수 없습니다.")
    return cat


@router.delete("/categories/{category_id}")
async def delete_category(_editor: TaxonomyEditor, category_id: int, session: AsyncSession = Depends(get_session)):
    """분류 삭제 — 하위 분류가 있으면 409. 데이터셋 매핑은 함께 삭제(CASCADE)된다."""
    count = await svc.count_category_children(session, category_id)
    if count > 0:
        logger.warning("분류 삭제 거부 — 하위 분류 존재: category_id=%d children=%d", category_id, count)
        raise HTTPException(status_code=409, detail=f"분류를 삭제할 수 없습니다: 하위 분류 {count}개가 있습니다.")
    if not await svc.delete_category(session, category_id):
        logger.warning("삭제 대상 분류 없음: category_id=%d", category_id)
        raise HTTPException(status_code=404, detail="분류를 찾을 수 없습니다.")
    return {"status": "ok", "message": "Category deleted"}


# ---------------------------------------------------------------------------
# 데이터셋 ↔ 분류 매핑
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/categories", response_model=list[DatasetCategoryRef])
async def list_dataset_categories(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋에 매핑된 분류 목록("분류체계 > 상위 > 분류" 경로 포함)."""
    logger.debug("데이터셋 분류 조회: dataset_id=%d", dataset_id)
    return await svc.list_dataset_categories(session, dataset_id)


@router.get("/categories/{category_id}/direct-dataset-ids", response_model=list[int])
async def list_direct_dataset_ids(category_id: int, session: AsyncSession = Depends(get_session)):
    """이 분류에 직접 매핑된 데이터셋 id (하위 분류 매핑 제외) — 매핑 해제 가능 여부 표시용."""
    ids = await svc.list_direct_dataset_ids(session, category_id)
    if ids is None:
        raise HTTPException(status_code=404, detail="분류를 찾을 수 없습니다.")
    return ids


@router.post("/datasets/{dataset_id}/categories/{category_id}")
async def add_dataset_category(_editor: TaxonomyEditor, dataset_id: int, category_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋을 분류에 매핑한다(이미 있으면 그대로 성공). 데이터셋·분류가 없으면 404."""
    if not await svc.add_dataset_category(session, dataset_id, category_id):
        logger.warning("분류 매핑 실패 — 데이터셋 또는 분류 없음: dataset_id=%d category_id=%d", dataset_id, category_id)
        raise HTTPException(status_code=404, detail="데이터셋 또는 분류를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/datasets/{dataset_id}/categories/{category_id}")
async def remove_dataset_category(_editor: TaxonomyEditor, dataset_id: int, category_id: int, session: AsyncSession = Depends(get_session)):
    """직접 매핑 하나를 해제한다. 매핑이 없으면 404."""
    if not await svc.remove_dataset_category(session, dataset_id, category_id):
        logger.warning("해제 대상 분류 매핑 없음: dataset_id=%d category_id=%d", dataset_id, category_id)
        raise HTTPException(status_code=404, detail="데이터셋-분류 매핑을 찾을 수 없습니다.")
    return {"status": "ok"}
