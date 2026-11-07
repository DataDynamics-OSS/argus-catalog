# SPDX-License-Identifier: Apache-2.0
"""서버 capability 버전 스냅숏.

수집·manifest 가져오기로 **서버 정의**(제거되지 않은 도구·리소스·프롬프트)가 바뀌면 한 행을
남긴다. 배포마다 노출 도구가 다르므로 수집 스냅숏 해시가 아니라 정의 전체로 계산한다 —
같은 서버의 여러 배포를 수집해도 정의가 그대로면 새 버전이 생기지 않는다.

스냅숏의 도구 값은 (설명·입력 스키마·annotations·그룹) 해시라서, 도구 설명만 슬쩍 바뀐
경우(tool poisoning 류 변경)도 ``changed`` 로 드러난다.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_servers import normalize
from app.mcp_servers.models import (
    McpDeployment,
    McpServer,
    McpServerPrompt,
    McpServerResource,
    McpServerTool,
    McpServerToolGroup,
    McpServerVersion,
)
from app.mcp_servers.schemas import ServerVersionDetail, ServerVersionResponse

logger = logging.getLogger(__name__)


async def _definition_snapshot(session: AsyncSession, server: McpServer) -> dict:
    """제거되지 않은 정의 스냅숏 — {"tools": {이름: 속성 해시 16자}, "resources": [uri], "prompts": [이름]}."""
    groups = dict(
        (await session.execute(
            select(McpServerToolGroup.id, McpServerToolGroup.key).where(McpServerToolGroup.mcp_server_id == server.id)
        )).all()
    )
    tools = (
        await session.execute(
            select(McpServerTool).where(McpServerTool.mcp_server_id == server.id, McpServerTool.removed_at.is_(None))
        )
    ).scalars().all()
    resources = (
        await session.execute(
            select(McpServerResource.uri).where(McpServerResource.mcp_server_id == server.id,
                                                McpServerResource.removed_at.is_(None))
        )
    ).scalars().all()
    prompts = (
        await session.execute(
            select(McpServerPrompt.name).where(McpServerPrompt.mcp_server_id == server.id,
                                               McpServerPrompt.removed_at.is_(None))
        )
    ).scalars().all()
    return {
        "tools": {
            t.name: normalize.stable_hash({"description": t.description, "input_schema": t.input_schema,
                                           "annotations": t.annotations, "group": groups.get(t.group_id),
                                           "mutating": t.mutating})[:16]
            for t in tools
            # 아직 서버에서 확인되지 않은 시드·수동 도구는 버전에 넣지 않는다
            if t.source not in ("forge_seed", "manual")
        },
        "resources": sorted(resources),
        "prompts": sorted(prompts),
    }


def _diff(prev: dict | None, cur: dict) -> dict:
    """직전 스냅숏 대비 차이. 첫 버전이면 ``initial: True`` 와 전체 도구를 added 로."""
    if prev is None:
        return {"initial": True, "tools": {"added": sorted(cur["tools"]), "removed": [], "changed": []}}
    pt, ct = prev.get("tools", {}), cur["tools"]
    out = {
        "tools": {
            "added": sorted(set(ct) - set(pt)),
            "removed": sorted(set(pt) - set(ct)),
            "changed": sorted(n for n in set(pt) & set(ct) if pt[n] != ct[n]),
        },
        "resources": {
            "added": sorted(set(cur["resources"]) - set(prev.get("resources", []))),
            "removed": sorted(set(prev.get("resources", [])) - set(cur["resources"])),
        },
        "prompts": {
            "added": sorted(set(cur["prompts"]) - set(prev.get("prompts", []))),
            "removed": sorted(set(prev.get("prompts", [])) - set(cur["prompts"])),
        },
    }
    return out


async def record_version(session: AsyncSession, server: McpServer, *, source: str | None,
                         deployment: McpDeployment | None = None, reported_version: str | None = None,
                         by: str | None = None) -> McpServerVersion | None:
    """정의가 직전 스냅숏과 다르면 새 버전 행을 추가한다 (flush 만, commit 은 호출자)."""
    snap = await _definition_snapshot(session, server)
    if not snap["tools"] and not snap["resources"] and not snap["prompts"]:
        logger.debug("capability 버전 기록 생략(빈 정의): server=%s", server.name)
        return None
    h = normalize.stable_hash(snap)
    last = (
        await session.execute(
            select(McpServerVersion).where(McpServerVersion.mcp_server_id == server.id)
            .order_by(McpServerVersion.id.desc()).limit(1)
        )
    ).scalars().first()
    if last and last.capability_hash == h:
        logger.debug("capability 버전 변화 없음: server=%s hash=%s", server.name, h[:12])
        return None
    row = McpServerVersion(
        mcp_server_id=server.id, capability_hash=h, reported_version=reported_version,
        tool_count=len(snap["tools"]), snapshot=snap, diff=_diff(last.snapshot if last else None, snap),
        source=source, deployment_id=deployment.id if deployment else None, created_by=by,
    )
    session.add(row)
    server.capability_hash = h
    await session.flush()
    logger.info("capability 새 버전 기록: server=%s version_id=%s tools=%d source=%s",
                server.name, row.id, row.tool_count, source)
    return row


async def list_versions(session: AsyncSession, server_id: int, limit: int = 100) -> list[ServerVersionResponse]:
    """서버의 capability 버전 목록(최신순, 최대 ``limit`` 건) — 스냅숏 본문은 빼고 diff 만."""
    rows = (
        await session.execute(
            select(McpServerVersion, McpDeployment.name)
            .outerjoin(McpDeployment, McpDeployment.id == McpServerVersion.deployment_id)
            .where(McpServerVersion.mcp_server_id == server_id)
            .order_by(McpServerVersion.id.desc()).limit(limit)
        )
    ).all()
    return [
        ServerVersionResponse(
            id=v.id, capability_hash=v.capability_hash, reported_version=v.reported_version,
            tool_count=v.tool_count, diff=v.diff, source=v.source, deployment_name=dep_name,
            created_by=v.created_by, created_at=v.created_at,
        )
        for v, dep_name in rows
    ]


async def get_version(session: AsyncSession, server_id: int, version_id: int) -> ServerVersionDetail | None:
    """버전 한 건(스냅숏 포함). 다른 서버의 버전 id 면 None."""
    v = await session.get(McpServerVersion, version_id)
    if not v or v.mcp_server_id != server_id:
        logger.debug("capability 버전 없음: server_id=%d version_id=%d", server_id, version_id)
        return None
    dep_name = None
    if v.deployment_id:
        dep_name = await session.scalar(select(McpDeployment.name).where(McpDeployment.id == v.deployment_id))
    return ServerVersionDetail(
        id=v.id, capability_hash=v.capability_hash, reported_version=v.reported_version, tool_count=v.tool_count,
        diff=v.diff, source=v.source, deployment_name=dep_name, created_by=v.created_by, created_at=v.created_at,
        snapshot=v.snapshot,
    )


async def version_count(session: AsyncSession, server_id: int) -> int:
    """서버의 capability 버전 수."""
    return await session.scalar(
        select(func.count(McpServerVersion.id)).where(McpServerVersion.mcp_server_id == server_id)
    ) or 0
