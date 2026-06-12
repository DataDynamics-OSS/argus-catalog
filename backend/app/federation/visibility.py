# SPDX-License-Identifier: Apache-2.0
"""Export visibility 거버넌스 — 이 인스턴스가 peer 에게 노출하는 데이터셋 범위 제한.

노출자(이 인스턴스) 측 정책이다. ``settings.federation_export_*`` 로 PII 제외/민감도
제외/데이터소스 allow-list 를 설정하고, export 의 목록·검색·드릴다운 전 경로에 일관되게
적용한다. 정책이 비어 있으면 전체 노출(기존 동작).
"""

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, Datasource
from app.core.config import settings

logger = logging.getLogger(__name__)


def dataset_visibility_conditions() -> list:
    """노출 가능 데이터셋을 거르는 SQLAlchemy 조건 목록.

    조건은 ``Dataset`` 과 ``Datasource`` 컬럼을 참조하므로, 호출하는 SELECT 는 둘을
    모두 FROM 에 포함(join)해야 한다.
    """
    conds = []
    if settings.federation_export_exclude_pii:
        # contains_pii 는 "true"/"false" 문자열. NULL 은 노출 허용.
        conds.append(or_(Dataset.contains_pii.is_(None), Dataset.contains_pii != "true"))
    excluded = settings.federation_export_exclude_sensitivity
    if excluded:
        conds.append(or_(Dataset.sensitivity.is_(None), Dataset.sensitivity.notin_(excluded)))
    allowlist = settings.federation_export_datasource_allowlist
    if allowlist:
        conds.append(Datasource.name.in_(allowlist))
    return conds


def has_visibility_policy() -> bool:
    """노출 제한 정책이 하나라도 설정돼 있으면 True."""
    return bool(
        settings.federation_export_exclude_pii
        or settings.federation_export_exclude_sensitivity
        or settings.federation_export_datasource_allowlist
    )


async def exportable_dataset_ids(
    session: AsyncSession, candidate_ids: list[int],
) -> set[int]:
    """후보 id 중 visibility 정책을 통과하는 것만 반환한다."""
    if not candidate_ids:
        return set()
    conds = dataset_visibility_conditions()
    if not conds:
        return set(candidate_ids)
    rows = (await session.execute(
        select(Dataset.id)
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(Dataset.id.in_(candidate_ids), *conds)
    )).all()
    return {r[0] for r in rows}


async def is_dataset_exportable(session: AsyncSession, dataset_id: int) -> bool:
    """단일 데이터셋이 노출 가능한지 검사(드릴다운 가드)."""
    allowed = await exportable_dataset_ids(session, [dataset_id])
    return dataset_id in allowed
