# SPDX-License-Identifier: Apache-2.0
"""사용자 소유 자산 — 조회와 이관(사용자 삭제 시).

카탈로그 자산의 소유권은 사용자 ID 가 아니라 아이디 문자열(``created_by``/``owner``)로 저장되고,
편집 권한도 이 값과 요청자 아이디를 비교해 판단한다(``assert_owner_or_admin`` 등). 그래서 자산을
가진 사용자를 그냥 삭제한 뒤 같은 아이디로 새 계정을 만들면 그 사람이 이전 자산의 편집 권한을
이어받는다. 이를 막기 위해 삭제 전에 소유 자산을 다른 사용자에게 넘기게 한다.

``OWNERSHIP`` 은 편집 권한 판단에 쓰이는 컬럼만 담는다. 생성·수정 이력(``updated_by`` 등)이나
표시용 담당자 정보는 권한과 무관하므로 옮기지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnedAsset:
    """소유권 컬럼을 가진 자산 종류 — key(응답 키)·label(화면 이름)·ORM 모델·소유권 컬럼들."""
    key: str
    label: str
    model: type
    columns: tuple[str, ...]


def _registry() -> list[OwnedAsset]:
    """편집 권한이 아이디 문자열로 묶인 자산 종류 목록."""
    # 각 모듈 모델은 지연 import — usermgr 가 다른 도메인 모듈에 기동 순서로 묶이지 않게
    from app.agents.models import AIAgent
    from app.apis.models import CatalogApi
    from app.catalog.models import Dataset
    from app.mcp_servers.models import McpDeployment, McpServer
    from app.models.models import RegisteredModel
    from app.oci_hub.models import OciModel

    return [
        OwnedAsset("datasets", "데이터셋", Dataset, ("created_by",)),
        OwnedAsset("apis", "API", CatalogApi, ("created_by",)),
        OwnedAsset("ai_agents", "AI 에이전트", AIAgent, ("created_by",)),
        OwnedAsset("mcp_servers", "MCP 서버", McpServer, ("created_by",)),
        OwnedAsset("mcp_deployments", "MCP 배포", McpDeployment, ("created_by",)),
        OwnedAsset("models", "ML 모델", RegisteredModel, ("created_by", "owner")),
        OwnedAsset("oci_models", "OCI 모델", OciModel, ("created_by", "owner")),
    ]


async def count_owned(session: AsyncSession, username: str) -> list[dict]:
    """자산 종류별 소유 건수(0 건 포함)."""
    out = []
    for a in _registry():
        cond = or_(*(getattr(a.model, c) == username for c in a.columns))
        n = (await session.execute(select(func.count()).select_from(a.model).where(cond))).scalar() or 0
        out.append({"key": a.key, "label": a.label, "count": int(n)})
    logger.debug("소유 자산 집계: username=%s total=%d", username, sum(o["count"] for o in out))
    return out


async def transfer_owned(session: AsyncSession, from_username: str, to_username: str) -> int:
    """``from_username`` 이 소유한 자산을 ``to_username`` 으로 옮긴다(커밋하지 않음). 옮긴 행 수."""
    moved = 0
    for a in _registry():
        for col in a.columns:
            res = await session.execute(
                update(a.model).where(getattr(a.model, col) == from_username)
                .values({col: to_username}).execution_options(synchronize_session=False)
            )
            moved += res.rowcount or 0
            logger.debug("소유 자산 이관: asset=%s column=%s rows=%s", a.key, col, res.rowcount)
    logger.info("소유 자산 이관 반영(미커밋): from=%s to=%s rows=%d", from_username, to_username, moved)
    return moved
