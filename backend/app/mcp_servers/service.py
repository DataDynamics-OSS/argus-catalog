# SPDX-License-Identifier: Apache-2.0
"""MCP Server 카탈로그 서비스 레이어.

라우터는 이 모듈의 함수만 호출한다. 이름 중복·사용 중 삭제 등 충돌은 ``ConflictError``,
입력 오류는 ``ValueError``, 대상 없음은 ``None`` 반환으로 표현한다.

로깅 정책(AI Agent 와 동일): 변경 동작은 INFO, 409·입력 오류 분기는 WARNING(404 는 라우터의 ``_404`` 가 남김),
조회·중간 계산은 DEBUG.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models import AIAgent, AIAgentMcpServer
from app.core.sql import LIKE_ESCAPE, like_contains
from app.mcp_servers import interop, normalize
from app.mcp_servers.forge_mapping import (
    FORGE_REPO,
    FORGE_SERVERS,
    READ_GROUP,
    ForgeServer,
    catalog_name,
    find_forge_server,
)
from app.mcp_servers.mcp_client import (
    CollectError,
    Credential,
    ManifestNotAvailableError,
    McpSnapshot,
    check_health,
    collect_forge_rest,
    collect_mcp,
    fetch_forge_manifest,
    origin_of,
)
from app.mcp_servers.models import (
    McpAuditEvent,
    McpDeployment,
    McpDeploymentCredential,
    McpDeploymentFinding,
    McpDeploymentGroupState,
    McpDeploymentToolState,
    McpResourceScope,
    McpServer,
    McpServerPrompt,
    McpServerResource,
    McpServerTool,
    McpServerToolGroup,
    McpStatusHistory,
    McpSyncLog,
)
from app.mcp_servers.schemas import (
    AuditEventResponse,
    CollectRequest,
    CollectResult,
    ConfigImportRequest,
    ControlCell,
    ControlOverview,
    ControlRow,
    CredentialPut,
    CredentialResponse,
    DeploymentAgentLink,
    DeploymentCreate,
    DeploymentDetail,
    DeploymentSummary,
    DeploymentUpdate,
    EffectiveGroup,
    EffectiveState,
    EffectiveTool,
    ForgeSeedResult,
    ImportItem,
    ImportRequest,
    ImportResult,
    ManifestImportRequest,
    McpServerCreate,
    McpServerDetail,
    McpServerSummary,
    McpServerUpdate,
    McpStats,
    NameCount,
    PaginatedDeployments,
    PaginatedMcpServers,
    PolicyCoverage,
    PromptCreate,
    PromptResponse,
    ResourceCreate,
    ResourceResponse,
    ResourceScopeCreate,
    ResourceScopeResponse,
    RiskyDeployment,
    SimulateRequest,
    SimulateResult,
    StatusHistoryResponse,
    SyncLogResponse,
    ToolCreate,
    ToolGroupResponse,
    ToolResponse,
    ToolUpdate,
    TrendPoint,
    UnregisteredMcp,
)

logger = logging.getLogger(__name__)


class ConflictError(Exception):
    """이름 중복·사용 중 삭제 등 409 대상."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _server_urn(name: str) -> str:
    """서버 정의 URN — ``<name>.mcp``."""
    return f"{name}.mcp"


def _deployment_urn(name: str, environment: str) -> str:
    """배포 URN — ``<name>.<environment>.mcp`` (환경이 바뀌면 다시 계산)."""
    return f"{name}.{environment}.mcp"


def _risk_max(values: list[str | None]) -> str | None:
    """알려진 위험도 중 가장 높은 값(없으면 None)."""
    ranked = [v for v in values if v in normalize.RISK_ORDER]
    return max(ranked, key=lambda v: normalize.RISK_ORDER[v]) if ranked else None


async def _get_server(session: AsyncSession, name: str) -> McpServer | None:
    return (await session.execute(select(McpServer).where(McpServer.name == name))).scalars().first()


async def _get_deployment(session: AsyncSession, name: str) -> McpDeployment | None:
    return (
        await session.execute(select(McpDeployment).where(McpDeployment.name == name))
    ).scalars().first()


async def server_exists(session: AsyncSession, name: str) -> bool:
    return await _get_server(session, name) is not None


async def deployment_exists(session: AsyncSession, name: str) -> bool:
    return await _get_deployment(session, name) is not None


async def get_server_created_by(session: AsyncSession, name: str) -> str | None:
    return (
        await session.execute(select(McpServer.created_by).where(McpServer.name == name))
    ).scalar_one_or_none()


async def get_deployment_created_by(session: AsyncSession, name: str) -> str | None:
    return (
        await session.execute(select(McpDeployment.created_by).where(McpDeployment.name == name))
    ).scalar_one_or_none()


def _record_status(session: AsyncSession, entity_type: str, entity_id: int, prev: str | None,
                   new: str, note: str | None, by: str | None) -> None:
    """상태 변경 이력 행 추가(commit 은 호출자). ``entity_type``: server / deployment."""
    session.add(
        McpStatusHistory(entity_type=entity_type, entity_id=entity_id, from_status=prev,
                         to_status=new, note=note, changed_by=by)
    )


def _log_sync(session: AsyncSession, server_id: int, deployment_id: int | None, kind: str,
              source: str | None, ok: bool, message: str | None = None, diff: dict | None = None,
              duration_ms: int | None = None, by: str | None = None) -> None:
    """수집·헬스·가져오기 실행 이력 행 추가(commit 은 호출자)."""
    logger.debug("MCP 실행 이력 기록: server_id=%d deployment_id=%s kind=%s source=%s ok=%s",
                 server_id, deployment_id, kind, source, ok)
    session.add(
        McpSyncLog(mcp_server_id=server_id, deployment_id=deployment_id, kind=kind, source=source,
                   ok=ok, message=message, diff=diff, duration_ms=duration_ms, triggered_by=by)
    )


def _forge_of(server: McpServer) -> ForgeServer | None:
    """argus-forge 서버면 이름 → target_platform → source_path(``platforms/<name>/…``) 순으로 Forge 정의를 찾는다."""
    if server.framework != "argus-forge":
        return None
    return (
        find_forge_server(server.name)
        or find_forge_server(server.target_platform)
        or find_forge_server(_platform_dir(server.source_path))
    )


def _platform_dir(source_path: str | None) -> str | None:
    """``platforms/<name>/…`` 형태의 source_path 에서 ``<name>`` 을 꺼낸다. 구분자가 없거나 비면 None."""
    parts = (source_path or "").split("/")
    return (parts[1] or None) if len(parts) > 1 else None


# ---------------------------------------------------------------------------
# 도구 그룹
# ---------------------------------------------------------------------------


async def _groups_by_key(session: AsyncSession, server_id: int) -> dict[str, McpServerToolGroup]:
    """서버의 도구 그룹 {key: 행}."""
    rows = (
        await session.execute(select(McpServerToolGroup).where(McpServerToolGroup.mcp_server_id == server_id))
    ).scalars().all()
    return {g.key: g for g in rows}


async def _ensure_groups(session: AsyncSession, server: McpServer) -> dict[str, McpServerToolGroup]:
    """서버의 도구 그룹 보장. Forge 면 매핑 그룹(+read·unclassified), 아니면 annotation 기반 자동 그룹.

    이미 있는 그룹은 건드리지 않고 없는 key 만 추가한다(flush). 반환: {key: 그룹 행}.
    """
    existing = await _groups_by_key(session, server.id)
    forge = _forge_of(server)
    specs: list[dict] = []
    if forge:
        for i, g in enumerate((READ_GROUP, *forge.groups)):
            specs.append({"key": g.key, "label": g.label, "description": g.description or None,
                          "config_key": g.config_key or None, "default_enabled": g.default_enabled,
                          "default_risk": g.risk, "mutating": g.mutating, "sort_order": i * 10})
        specs.append({**normalize.GENERIC_GROUPS[3], "sort_order": 999, "default_enabled": True})
    else:
        specs = [{**g, "default_enabled": True} for g in normalize.GENERIC_GROUPS]
    added = 0
    for spec in specs:
        if spec["key"] not in existing:
            g = McpServerToolGroup(mcp_server_id=server.id, **spec)
            session.add(g)
            existing[spec["key"]] = g
            added += 1
    await session.flush()
    if added:
        logger.debug("MCP 도구 그룹 추가: server=%s forge=%s added=%d", server.name, bool(forge), added)
    return existing


async def _seed_forge_tools(session: AsyncSession, server: McpServer,
                            groups: dict[str, McpServerToolGroup]) -> None:
    """Forge 매핑에 이름이 알려진 도구를 정의에 미리 등록 (source=forge_seed)."""
    forge = _forge_of(server)
    if not forge:
        return
    have = {
        n for (n,) in (
            await session.execute(select(McpServerTool.name).where(McpServerTool.mcp_server_id == server.id))
        ).all()
    }
    for g in forge.groups:
        for tool in g.tools:
            if tool in have:
                continue
            have.add(tool)
            session.add(
                McpServerTool(
                    mcp_server_id=server.id, group_id=groups[g.key].id, name=tool,
                    mutating=g.mutating, risk=g.risk, source="forge_seed",
                    resource_types=list(forge.resource_types.get(tool, ())) or None,
                )
            )
    await session.flush()


# ---------------------------------------------------------------------------
# 서버 정의 CRUD
# ---------------------------------------------------------------------------


async def _reindex(server_id: int) -> None:
    """통합 검색 임베딩 갱신 예약 — embed-on-write 가 켜져 있을 때만, 디바운스로 1회."""
    from app.embedding.service import embed_entity_background

    await embed_entity_background("mcp_server", server_id)


async def _reindex_names(session: AsyncSession, names: list[str]) -> None:
    """이름 목록에 해당하는 서버들의 임베딩 갱신 예약."""
    if not names:
        return
    for sid in (await session.execute(select(McpServer.id).where(McpServer.name.in_(names)))).scalars().all():
        await _reindex(sid)


async def create_server(session: AsyncSession, req: McpServerCreate,
                        created_by: str | None = None, commit: bool = True) -> McpServerSummary:
    """서버 정의 생성 — 도구 그룹을 만들고, Forge 면 빈 필드를 매핑값으로 채우고 알려진 도구를 시드한다.

    ``commit=False`` 면 flush 까지만 한다(일괄 가져오기에서 호출자가 commit/rollback).

    Raises:
        ConflictError: 같은 이름의 서버가 있음.
    """
    if await _get_server(session, req.name):
        logger.warning("MCP 서버 생성 거부(이름 중복): %s", req.name)
        raise ConflictError(f"MCP 서버 '{req.name}' 이(가) 이미 있습니다")
    data = req.model_dump(exclude={"name"}, exclude_none=True)
    forge = find_forge_server(req.name) if data.get("framework") == "argus-forge" else None
    if forge:
        data.setdefault("target_platform", forge.target_platform)
        data.setdefault("source_repo", FORGE_REPO)
        data.setdefault("source_path", forge.source_path)
        data.setdefault("description", forge.description)
        data.setdefault("display_name", forge.display_name)
        data.setdefault("provider_type", "internal")
    server = McpServer(name=req.name, urn=_server_urn(req.name), created_by=created_by, **data)
    session.add(server)
    await session.flush()
    groups = await _ensure_groups(session, server)
    await _seed_forge_tools(session, server, groups)
    if commit:
        await session.commit()
        await session.refresh(server)
        await _reindex(server.id)
    logger.info("MCP 서버 생성됨: %s (framework=%s)", server.name, server.framework)
    return McpServerSummary.model_validate(server)


async def _server_counts(session: AsyncSession, server_ids: list[int]) -> dict[int, dict]:
    """서버별 집계 — retired 아닌 배포 수, 제거되지 않은 도구 수·변경 도구 수."""
    out = {sid: {"deployment_count": 0, "tool_count": 0, "mutating_tool_count": 0} for sid in server_ids}
    if not server_ids:
        return out
    for sid, cnt in (
        await session.execute(
            select(McpDeployment.mcp_server_id, func.count(McpDeployment.id))
            .where(McpDeployment.mcp_server_id.in_(server_ids), McpDeployment.status != "retired")
            .group_by(McpDeployment.mcp_server_id)
        )
    ).all():
        out[sid]["deployment_count"] = cnt
    for sid, mutating, cnt in (
        await session.execute(
            select(McpServerTool.mcp_server_id, McpServerTool.mutating, func.count(McpServerTool.id))
            .where(McpServerTool.mcp_server_id.in_(server_ids), McpServerTool.removed_at.is_(None))
            .group_by(McpServerTool.mcp_server_id, McpServerTool.mutating)
        )
    ).all():
        out[sid]["tool_count"] += cnt
        if mutating:
            out[sid]["mutating_tool_count"] += cnt
    return out


async def list_servers(session: AsyncSession, *, search: str | None = None, status: str | None = None,
                       provider_type: str | None = None, framework: str | None = None,
                       target_platform: str | None = None, page: int = 1,
                       page_size: int = 20) -> PaginatedMcpServers:
    """서버 정의 목록(이름순, 페이지) + 배포·도구 집계."""
    base = select(McpServer)
    if search:
        like = like_contains(search)
        base = base.where(or_(McpServer.name.ilike(like, escape=LIKE_ESCAPE), McpServer.display_name.ilike(like, escape=LIKE_ESCAPE),
                              McpServer.title.ilike(like, escape=LIKE_ESCAPE), McpServer.description.ilike(like, escape=LIKE_ESCAPE),
                              McpServer.registry_name.ilike(like, escape=LIKE_ESCAPE)))
    if status:
        base = base.where(McpServer.status == status)
    if provider_type:
        base = base.where(McpServer.provider_type == provider_type)
    if framework:
        base = base.where(McpServer.framework == framework)
    if target_platform:
        base = base.where(McpServer.target_platform == target_platform)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await session.execute(base.order_by(McpServer.name).offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()
    counts = await _server_counts(session, [r.id for r in rows])
    items = [McpServerSummary.model_validate(r).model_copy(update=counts[r.id]) for r in rows]
    return PaginatedMcpServers(items=items, total=total, page=page, page_size=page_size)


async def _tool_responses(session: AsyncSession, server_id: int,
                          groups: dict[int, McpServerToolGroup]) -> list[ToolResponse]:
    """서버의 전체 도구(제거 표시 포함, 이름순) 응답 — group_key 를 채운다."""
    tools = (
        await session.execute(
            select(McpServerTool).where(McpServerTool.mcp_server_id == server_id).order_by(McpServerTool.name)
        )
    ).scalars().all()
    out = []
    for t in tools:
        r = ToolResponse.model_validate(t)
        g = groups.get(t.group_id) if t.group_id else None
        r.group_key = g.key if g else None
        out.append(r)
    return out


async def get_server_detail(session: AsyncSession, name: str) -> McpServerDetail | None:
    """서버 상세 — 그룹(도구 수)·도구·리소스·프롬프트·배포 요약 포함. 없으면 None."""
    server = await _get_server(session, name)
    if not server:
        logger.debug("MCP 서버 상세 조회 대상 없음: server=%s", name)
        return None
    group_rows = (
        await session.execute(
            select(McpServerToolGroup).where(McpServerToolGroup.mcp_server_id == server.id)
            .order_by(McpServerToolGroup.sort_order, McpServerToolGroup.key)
        )
    ).scalars().all()
    by_id = {g.id: g for g in group_rows}
    tools = await _tool_responses(session, server.id, by_id)
    tool_count_by_group = Counter(t.group_key for t in tools if not t.removed_at)
    groups = []
    for g in group_rows:
        r = ToolGroupResponse.model_validate(g)
        r.tool_count = tool_count_by_group.get(g.key, 0)
        groups.append(r)
    resources = (
        await session.execute(
            select(McpServerResource).where(McpServerResource.mcp_server_id == server.id)
            .order_by(McpServerResource.uri)
        )
    ).scalars().all()
    prompts = (
        await session.execute(
            select(McpServerPrompt).where(McpServerPrompt.mcp_server_id == server.id)
            .order_by(McpServerPrompt.name)
        )
    ).scalars().all()
    deployments = (
        await session.execute(
            select(McpDeployment).where(McpDeployment.mcp_server_id == server.id).order_by(McpDeployment.name)
        )
    ).scalars().all()
    counts = (await _server_counts(session, [server.id]))[server.id]
    detail = McpServerDetail.model_validate(server).model_copy(update=counts)
    detail.groups = groups
    detail.tools = tools
    detail.resources = [ResourceResponse.model_validate(r) for r in resources]
    detail.prompts = [PromptResponse.model_validate(p) for p in prompts]
    detail.deployments = await _deployment_summaries(session, list(deployments))
    return detail


async def update_server(session: AsyncSession, name: str, req: McpServerUpdate,
                        changed_by: str | None = None) -> McpServerSummary | None:
    """서버 정의 부분 수정(보낸 필드만). 상태가 바뀌면 이력을 남기고, framework 가 바뀌면 그룹·시드 도구를 보강한다."""
    server = await _get_server(session, name)
    if not server:
        return None
    data = req.model_dump(exclude_unset=True, exclude={"status_note"})
    prev_status = server.status
    prev_framework = server.framework
    for k, v in data.items():
        setattr(server, k, v)
    server.updated_by = changed_by
    if "status" in data and data["status"] and data["status"] != prev_status:
        _record_status(session, "server", server.id, prev_status, data["status"], req.status_note, changed_by)
        logger.info("MCP 서버 상태 변경: %s %s -> %s", name, prev_status, data["status"])
    if server.framework != prev_framework:
        groups = await _ensure_groups(session, server)
        await _seed_forge_tools(session, server, groups)
    await session.commit()
    await session.refresh(server)
    await _reindex(server.id)
    logger.info("MCP 서버 수정: %s fields=%s by=%s", name, sorted(data), changed_by)
    counts = (await _server_counts(session, [server.id]))[server.id]
    return McpServerSummary.model_validate(server).model_copy(update=counts)


async def retire_server(session: AsyncSession, name: str, by: str | None = None) -> bool:
    """소프트 삭제(status=retired). 활성 배포가 있으면 ConflictError."""
    server = await _get_server(session, name)
    if not server:
        return False
    active = (
        await session.execute(
            select(McpDeployment.name).where(McpDeployment.mcp_server_id == server.id,
                                             McpDeployment.status != "retired")
        )
    ).scalars().all()
    if active:
        logger.warning("MCP 서버 retire 거부(배포 사용 중): %s %s", name, active)
        raise ConflictError(f"retired 가 아닌 배포가 있습니다: {', '.join(active)}")
    if server.status != "retired":
        _record_status(session, "server", server.id, server.status, "retired", "삭제", by)
        server.status = "retired"
        server.updated_by = by
    from app.embedding.service import delete_entity_embedding

    await delete_entity_embedding(session, "mcp_server", server.id)
    await session.commit()
    logger.info("MCP 서버 retired: %s", name)
    return True


async def list_server_status_history(session: AsyncSession, name: str) -> list[StatusHistoryResponse] | None:
    """서버 정의 상태 변경 이력(최신순). 서버가 없으면 None."""
    server = await _get_server(session, name)
    if not server:
        return None
    return await _status_history(session, "server", server.id)


async def _status_history(session: AsyncSession, entity_type: str, entity_id: int) -> list[StatusHistoryResponse]:
    """엔터티의 상태 변경 이력(최신순)."""
    rows = (
        await session.execute(
            select(McpStatusHistory)
            .where(McpStatusHistory.entity_type == entity_type, McpStatusHistory.entity_id == entity_id)
            .order_by(McpStatusHistory.id.desc())
        )
    ).scalars().all()
    return [StatusHistoryResponse.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# 도구·리소스·프롬프트 (정의)
# ---------------------------------------------------------------------------


async def add_tool(session: AsyncSession, name: str, req: ToolCreate) -> ToolResponse | None:
    """도구 수동 등록(source=manual). 그룹 미지정이면 annotation 으로 분류, 위험도를 주면 확정으로 본다.

    서버가 없으면 None.

    Raises:
        ConflictError: 같은 이름의 도구가 있음.
        ValueError: 알 수 없는 그룹.
    """
    server = await _get_server(session, name)
    if not server:
        return None
    exists = (
        await session.execute(
            select(McpServerTool).where(McpServerTool.mcp_server_id == server.id, McpServerTool.name == req.name)
        )
    ).scalars().first()
    if exists:
        logger.warning("MCP 도구 등록 거부(이름 중복): %s/%s", name, req.name)
        raise ConflictError(f"도구 '{req.name}' 이(가) 이미 있습니다")
    groups = await _ensure_groups(session, server)
    group_key = req.group_key or normalize.classify_annotations(req.annotations)
    group = groups.get(group_key)
    if not group:
        logger.warning("MCP 도구 등록 거부(알 수 없는 그룹): %s/%s group=%s", name, req.name, group_key)
        raise ValueError(f"알 수 없는 도구 그룹: {group_key}")
    tool = McpServerTool(
        mcp_server_id=server.id, group_id=group.id, name=req.name, title=req.title,
        description=req.description, input_schema=req.input_schema, annotations=req.annotations,
        mutating=group.mutating, risk=req.risk or group.default_risk, risk_confirmed=req.risk is not None,
        requires_approval=req.requires_approval, resource_types=req.resource_types, source="manual",
    )
    session.add(tool)
    await session.commit()
    await session.refresh(tool)
    await _reindex(server.id)
    logger.info("MCP 도구 수동 등록: %s/%s", name, req.name)
    r = ToolResponse.model_validate(tool)
    r.group_key = group.key
    return r


async def update_tool(session: AsyncSession, name: str, tool_id: int, req: ToolUpdate) -> ToolResponse | None:
    """도구 거버넌스 속성 수정. 위험도만 바꾸면 확정(risk_confirmed)으로 표시하고, 그 서버 배포 finding 을 재계산한다.

    서버·도구가 없으면 None. 알 수 없는 그룹이면 ValueError.
    """
    server = await _get_server(session, name)
    if not server:
        return None
    tool = await session.get(McpServerTool, tool_id)
    if not tool or tool.mcp_server_id != server.id:
        return None
    data = req.model_dump(exclude_unset=True)
    groups = await _groups_by_key(session, server.id)
    if "group_key" in data:
        key = data.pop("group_key")
        group = groups.get(key) if key else None
        if key and not group:
            logger.warning("MCP 도구 수정 거부(알 수 없는 그룹): %s tool_id=%d group=%s", name, tool_id, key)
            raise ValueError(f"알 수 없는 도구 그룹: {key}")
        tool.group_id = group.id if group else None
        if group:
            tool.mutating = group.mutating
    for k, v in data.items():
        setattr(tool, k, v)
    if "risk" in data and "risk_confirmed" not in data:
        tool.risk_confirmed = True
    await session.flush()
    # 위험도 확정·그룹 이동은 배포 finding(new_tool_unreviewed 등)에 영향
    from app.mcp_servers.policy import evaluate_server_deployments

    await evaluate_server_deployments(session, server.id)
    await session.commit()
    await session.refresh(tool)
    await _reindex(server.id)
    logger.info("MCP 도구 수정: %s/%s fields=%s", name, tool.name, sorted(req.model_fields_set))
    r = ToolResponse.model_validate(tool)
    r.group_key = next((g.key for g in groups.values() if g.id == tool.group_id), None)
    return r


async def delete_tool(session: AsyncSession, name: str, tool_id: int) -> bool:
    """도구 행 삭제(하드 삭제). 서버·도구가 없으면 False."""
    server = await _get_server(session, name)
    if not server:
        return False
    tool = await session.get(McpServerTool, tool_id)
    if not tool or tool.mcp_server_id != server.id:
        return False
    await session.delete(tool)
    await session.commit()
    await _reindex(server.id)
    logger.info("MCP 도구 삭제: %s tool_id=%d", name, tool_id)
    return True


async def add_resource(session: AsyncSession, name: str, req: ResourceCreate) -> ResourceResponse | None:
    """리소스 수동 등록(source=manual). 서버가 없으면 None."""
    server = await _get_server(session, name)
    if not server:
        return None
    row = McpServerResource(mcp_server_id=server.id, source="manual", **req.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info("MCP 리소스 수동 등록: %s uri=%s", name, row.uri)
    return ResourceResponse.model_validate(row)


async def delete_resource(session: AsyncSession, name: str, resource_id: int) -> bool:
    """리소스 행 삭제. 서버·리소스가 없으면 False."""
    server = await _get_server(session, name)
    row = await session.get(McpServerResource, resource_id) if server else None
    if not row or row.mcp_server_id != server.id:
        return False
    await session.delete(row)
    await session.commit()
    logger.info("MCP 리소스 삭제: %s resource_id=%d", name, resource_id)
    return True


async def add_prompt(session: AsyncSession, name: str, req: PromptCreate) -> PromptResponse | None:
    """프롬프트 수동 등록(source=manual). 서버가 없으면 None."""
    server = await _get_server(session, name)
    if not server:
        return None
    row = McpServerPrompt(mcp_server_id=server.id, source="manual", **req.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info("MCP 프롬프트 수동 등록: %s prompt=%s", name, row.name)
    return PromptResponse.model_validate(row)


async def delete_prompt(session: AsyncSession, name: str, prompt_id: int) -> bool:
    """프롬프트 행 삭제. 서버·프롬프트가 없으면 False."""
    server = await _get_server(session, name)
    row = await session.get(McpServerPrompt, prompt_id) if server else None
    if not row or row.mcp_server_id != server.id:
        return False
    await session.delete(row)
    await session.commit()
    logger.info("MCP 프롬프트 삭제: %s prompt_id=%d", name, prompt_id)
    return True


# ---------------------------------------------------------------------------
# 배포 CRUD
# ---------------------------------------------------------------------------


async def _deployment_summaries(session: AsyncSession, rows: list[McpDeployment]) -> list[DeploymentSummary]:
    """배포 요약 목록 — 서버 이름, 켜진 변경 그룹(관측), 노출 도구 수·최고 위험도, 경고·에이전트·finding 집계."""
    if not rows:
        return []
    ids = [d.id for d in rows]
    server_names = dict(
        (
            await session.execute(
                select(McpServer.id, McpServer.name).where(McpServer.id.in_({d.mcp_server_id for d in rows}))
            )
        ).all()
    )
    enabled: dict[int, list[str]] = {i: [] for i in ids}
    for dep_id, key, mutating, observed, default in (
        await session.execute(
            select(McpDeploymentGroupState.deployment_id, McpServerToolGroup.key, McpServerToolGroup.mutating,
                   McpDeploymentGroupState.observed_enabled, McpServerToolGroup.default_enabled)
            .join(McpServerToolGroup, McpServerToolGroup.id == McpDeploymentGroupState.group_id)
            .where(McpDeploymentGroupState.deployment_id.in_(ids))
        )
    ).all():
        if mutating and observed:
            enabled[dep_id].append(key)
    exposed: dict[int, list[str | None]] = {i: [] for i in ids}
    for dep_id, risk in (
        await session.execute(
            select(McpDeploymentToolState.deployment_id, McpServerTool.risk)
            .join(McpServerTool, McpServerTool.id == McpDeploymentToolState.tool_id)
            .where(McpDeploymentToolState.deployment_id.in_(ids), McpDeploymentToolState.observed_exposed.is_(True))
        )
    ).all():
        exposed[dep_id].append(risk)
    agents = dict(
        (
            await session.execute(
                select(AIAgentMcpServer.mcp_deployment_id, func.count(AIAgentMcpServer.id))
                .where(AIAgentMcpServer.mcp_deployment_id.in_(ids))
                .group_by(AIAgentMcpServer.mcp_deployment_id)
            )
        ).all()
    )
    from app.mcp_servers.policy import finding_counts

    findings = await finding_counts(session, ids)
    out = []
    for d in rows:
        s = DeploymentSummary.model_validate(d)
        s.open_finding_count, s.max_finding_severity = findings.get(d.id, (0, None))
        s.server_name = server_names.get(d.mcp_server_id)
        s.enabled_mutating_groups = sorted(enabled[d.id])
        s.exposed_tool_count = len(exposed[d.id])
        s.max_exposed_risk = _risk_max(exposed[d.id])
        s.warning_count = len(d.security_warnings or [])
        s.agent_count = agents.get(d.id, 0)
        out.append(s)
    return out


async def create_deployment(session: AsyncSession, req: DeploymentCreate, created_by: str | None = None,
                            commit: bool = True) -> DeploymentSummary:
    """배포 생성 — 그룹 상태 행을 만들어 둔다. ``commit=False`` 면 flush 까지만.

    Raises:
        ValueError: 서버 정의 없음·endpoint 형식 오류.
        ConflictError: 같은 이름의 배포가 있음.
    """
    server = await _get_server(session, req.server_name)
    if not server:
        logger.warning("MCP 배포 생성 거부(서버 정의 없음): %s server=%s", req.name, req.server_name)
        raise ValueError(f"MCP 서버 '{req.server_name}' 을(를) 찾을 수 없습니다")
    if await _get_deployment(session, req.name):
        logger.warning("MCP 배포 생성 거부(이름 중복): %s", req.name)
        raise ConflictError(f"MCP 배포 '{req.name}' 이(가) 이미 있습니다")
    data = req.model_dump(exclude={"name", "server_name"}, exclude_none=True)
    _validate_connection(data.get("transport", "streamable_http"), data.get("endpoint"))
    env = data.get("environment", "DEV")
    dep = McpDeployment(name=req.name, urn=_deployment_urn(req.name, env), mcp_server_id=server.id,
                        created_by=created_by, **data)
    session.add(dep)
    await session.flush()
    await _init_group_states(session, server, dep)
    if commit:
        await session.commit()
        await session.refresh(dep)
        await _reindex(server.id)
    logger.info("MCP 배포 생성됨: %s (server=%s, env=%s)", dep.name, server.name, env)
    return (await _deployment_summaries(session, [dep]))[0]


def _validate_connection(transport: str, endpoint: str | None) -> None:
    """HTTP 계열 transport 의 endpoint 는 http(s):// 로 시작해야 한다(비어 있으면 통과). 아니면 ValueError."""
    if transport in ("streamable_http", "sse") and endpoint:
        if not re.match(r"^https?://", endpoint):
            logger.warning("MCP 배포 endpoint 형식 오류: transport=%s", transport)
            raise ValueError("endpoint 는 http(s):// URL 이어야 합니다")


async def _init_group_states(session: AsyncSession, server: McpServer, dep: McpDeployment) -> None:
    """배포에 서버의 모든 그룹에 대한 상태 행(관측·승인 값 NULL)이 있도록 보장한다(flush)."""
    groups = await _ensure_groups(session, server)
    have = {
        gid for (gid,) in (
            await session.execute(
                select(McpDeploymentGroupState.group_id).where(McpDeploymentGroupState.deployment_id == dep.id)
            )
        ).all()
    }
    for g in groups.values():
        if g.id not in have:
            session.add(McpDeploymentGroupState(deployment_id=dep.id, group_id=g.id))
    await session.flush()


async def list_deployments(session: AsyncSession, *, search: str | None = None, server: str | None = None,
                           environment: str | None = None, status: str | None = None,
                           health_status: str | None = None, mutating_enabled: bool | None = None,
                           page: int = 1, page_size: int = 20) -> PaginatedDeployments:
    """배포 목록(환경·이름순, 페이지). ``mutating_enabled`` 는 관측상 변경 그룹이 켜진 배포만(True)/제외(False)."""
    logger.debug("MCP 배포 목록 조회: search=%s server=%s env=%s status=%s page=%d",
                 search, server, environment, status, page)
    base = select(McpDeployment)
    if search:
        like = like_contains(search)
        base = base.where(or_(McpDeployment.name.ilike(like, escape=LIKE_ESCAPE), McpDeployment.display_name.ilike(like, escape=LIKE_ESCAPE),
                              McpDeployment.endpoint.ilike(like, escape=LIKE_ESCAPE), McpDeployment.target_endpoint.ilike(like, escape=LIKE_ESCAPE)))
    if server:
        base = base.join(McpServer, McpServer.id == McpDeployment.mcp_server_id).where(McpServer.name == server)
    if environment:
        base = base.where(McpDeployment.environment == environment)
    if status:
        base = base.where(McpDeployment.status == status)
    if health_status:
        base = base.where(McpDeployment.health_status == health_status)
    if mutating_enabled is not None:
        sub = (
            select(McpDeploymentGroupState.deployment_id)
            .join(McpServerToolGroup, McpServerToolGroup.id == McpDeploymentGroupState.group_id)
            .where(McpServerToolGroup.mutating.is_(True), McpDeploymentGroupState.observed_enabled.is_(True))
        )
        base = base.where(McpDeployment.id.in_(sub) if mutating_enabled else McpDeployment.id.not_in(sub))
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await session.execute(
            base.order_by(McpDeployment.environment, McpDeployment.name)
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return PaginatedDeployments(items=await _deployment_summaries(session, list(rows)), total=total,
                                page=page, page_size=page_size)


async def get_deployment_detail(session: AsyncSession, name: str) -> DeploymentDetail | None:
    """배포 상세 — 요약 집계 + 자격증명·수집 토큰 존재 여부, 서버 framework, 데이터소스 이름. 없으면 None."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    summary = (await _deployment_summaries(session, [dep]))[0]
    detail = DeploymentDetail.model_validate(dep).model_copy(update=summary.model_dump(
        include={"server_name", "enabled_mutating_groups", "exposed_tool_count", "max_exposed_risk",
                 "warning_count", "agent_count", "open_finding_count", "max_finding_severity"}))
    detail.has_ingest_token = bool(dep.ingest_token_hash)
    server = await session.get(McpServer, dep.mcp_server_id)
    detail.server_framework = server.framework if server else None
    detail.has_credential = (
        await session.scalar(
            select(func.count(McpDeploymentCredential.id)).where(McpDeploymentCredential.deployment_id == dep.id)
        )
    ) > 0
    if dep.datasource_id:
        from app.catalog.models import Datasource

        ds = await session.get(Datasource, dep.datasource_id)
        detail.datasource_name = ds.name if ds else None
    return detail


async def update_deployment(session: AsyncSession, name: str, req: DeploymentUpdate,
                            changed_by: str | None = None) -> DeploymentSummary | None:
    """배포 부분 수정(보낸 필드만). policy_mode 변경은 거부(정책 화면에서만), 환경이 바뀌면 URN 재계산.

    배포가 없으면 None. 잘못된 입력은 ValueError.
    """
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    data = req.model_dump(exclude_unset=True, exclude={"status_note"})
    # 정책 모드는 승인 정책의 일부 — 정책 화면(이력이 남는 경로)에서만 바꾼다
    if "policy_mode" in data:
        if data["policy_mode"] and data["policy_mode"] != dep.policy_mode:
            logger.warning("MCP 배포 수정 거부(policy_mode 는 정책 화면에서만): %s", name)
            raise ValueError("policy_mode 는 승인 정책 화면(PUT /mcp-deployments/{name}/policy)에서 변경하세요")
        data.pop("policy_mode")
    if "audit_retention_days" in data and not data["audit_retention_days"]:
        data.pop("audit_retention_days")
    _validate_connection(data.get("transport", dep.transport), data.get("endpoint", dep.endpoint))
    prev = dep.status
    for k, v in data.items():
        setattr(dep, k, v)
    if "environment" in data and data["environment"]:
        dep.urn = _deployment_urn(dep.name, data["environment"])
    dep.updated_by = changed_by
    if data.get("status") and data["status"] != prev:
        _record_status(session, "deployment", dep.id, prev, data["status"], req.status_note, changed_by)
        logger.info("MCP 배포 상태 변경: %s %s -> %s", name, prev, data["status"])
    await session.commit()
    await session.refresh(dep)
    logger.info("MCP 배포 수정: %s fields=%s by=%s", name, sorted(data), changed_by)
    return (await _deployment_summaries(session, [dep]))[0]


async def retire_deployment(session: AsyncSession, name: str, by: str | None = None) -> bool:
    """소프트 삭제(status=retired). 연결된 에이전트가 있으면 ConflictError, 배포가 없으면 False."""
    dep = await _get_deployment(session, name)
    if not dep:
        return False
    agents = (
        await session.execute(
            select(AIAgent.name).join(AIAgentMcpServer, AIAgentMcpServer.agent_id == AIAgent.id)
            .where(AIAgentMcpServer.mcp_deployment_id == dep.id)
        )
    ).scalars().all()
    if agents:
        logger.warning("MCP 배포 retire 거부(에이전트 사용 중): %s %s", name, agents)
        raise ConflictError(f"이 배포를 사용하는 에이전트가 있습니다: {', '.join(sorted(set(agents)))}")
    if dep.status != "retired":
        _record_status(session, "deployment", dep.id, dep.status, "retired", "삭제", by)
        dep.status = "retired"
        dep.updated_by = by
    await session.commit()
    logger.info("MCP 배포 retired: %s", name)
    return True


async def list_deployment_status_history(session: AsyncSession, name: str) -> list[StatusHistoryResponse] | None:
    """배포 상태 변경 이력(최신순). 배포가 없으면 None."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    return await _status_history(session, "deployment", dep.id)


# ---------------------------------------------------------------------------
# 자격증명
# ---------------------------------------------------------------------------


def _mask(secret: str) -> str:
    """화면 표시용 마스킹 — 8자 이하는 전부, 그보다 길면 앞뒤 2자만 보인다."""
    return "****" if len(secret) <= 8 else f"{secret[:2]}****{secret[-2:]}"


async def put_credential(session: AsyncSession, name: str, req: CredentialPut,
                         by: str | None = None) -> CredentialResponse | None:
    """배포 자격증명 저장(배포당 1개, 덮어쓰기) — 비밀값은 Fernet 암호문으로만 저장. 배포가 없으면 None."""
    from app.apis.crypto import encrypt

    dep = await _get_deployment(session, name)
    if not dep:
        return None
    row = (
        await session.execute(select(McpDeploymentCredential).where(McpDeploymentCredential.deployment_id == dep.id))
    ).scalars().first()
    if not row:
        row = McpDeploymentCredential(deployment_id=dep.id, secret_encrypted="")
        session.add(row)
    row.kind, row.header_name, row.username = req.kind, req.header_name, req.username
    row.secret_encrypted = encrypt(req.secret)
    row.updated_by = by
    await session.commit()
    await session.refresh(row)
    logger.info("MCP 배포 자격증명 저장: %s (kind=%s)", name, req.kind)
    return CredentialResponse(kind=row.kind, header_name=row.header_name, username=row.username,
                              masked=_mask(req.secret), updated_at=row.updated_at, updated_by=by)


async def get_credential(session: AsyncSession, name: str) -> CredentialResponse | None:
    """자격증명 메타데이터(값은 마스킹). 배포·자격증명이 없으면 None."""
    from app.apis.crypto import decrypt

    dep = await _get_deployment(session, name)
    if not dep:
        return None
    row = (
        await session.execute(select(McpDeploymentCredential).where(McpDeploymentCredential.deployment_id == dep.id))
    ).scalars().first()
    if not row:
        return None
    try:
        masked = _mask(decrypt(row.secret_encrypted))
    except Exception:  # noqa: BLE001 - 키 불일치 시에도 존재만 알린다
        logger.warning("MCP 배포 자격증명 복호화 실패(키 불일치 가능): deployment=%s", name)
        masked = "(복호화 불가)"
    return CredentialResponse(kind=row.kind, header_name=row.header_name, username=row.username,
                              masked=masked, updated_at=row.updated_at, updated_by=row.updated_by)


async def delete_credential(session: AsyncSession, name: str) -> bool:
    """자격증명 삭제. 배포·자격증명이 없으면 False."""
    dep = await _get_deployment(session, name)
    if not dep:
        return False
    row = (
        await session.execute(select(McpDeploymentCredential).where(McpDeploymentCredential.deployment_id == dep.id))
    ).scalars().first()
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    logger.info("MCP 배포 자격증명 삭제: %s", name)
    return True


async def _load_credential(session: AsyncSession, dep: McpDeployment) -> Credential | None:
    """수집·헬스 요청용 자격증명(복호화). 없으면 None. 복호화 실패는 예외가 그대로 올라간다."""
    from app.apis.crypto import decrypt

    row = (
        await session.execute(select(McpDeploymentCredential).where(McpDeploymentCredential.deployment_id == dep.id))
    ).scalars().first()
    if not row:
        logger.debug("MCP 배포 자격증명 없음 — 인증 헤더 없이 요청: deployment=%s", dep.name)
        return None
    return Credential(kind=row.kind, secret=decrypt(row.secret_encrypted), header_name=row.header_name,
                      username=row.username)


# ---------------------------------------------------------------------------
# 수집 (collect) — 정의에 도구 upsert + 배포 노출 상태 갱신
# ---------------------------------------------------------------------------


def _forge_group_of(forge: ForgeServer | None, tool: str) -> str | None:
    """Forge 매핑에서 도구가 속한 그룹 key(모르면 None)."""
    if not forge:
        return None
    for g in forge.groups:
        if tool in g.tools:
            return g.key
    return None


async def _apply_snapshot(session: AsyncSession, server: McpServer, dep: McpDeployment,
                          snap: McpSnapshot) -> dict:
    """스냅숏을 정의·배포에 반영하고 diff 를 돌려준다 (commit 은 호출자).

    - 새 도구: Forge 매핑 그룹 → (Forge 도구 REST 면 mutating 플래그로 read/unclassified) → annotation 분류.
    - 기존 도구: unclassified 였으면 새 annotation 으로 재분류(위험도 미확정일 때만 위험도도 갱신).
    - 배포 노출 상태는 이번 스냅숏에 있는지로, 그룹 on/off 는 노출 도구 유무로 추론(``inferred``).
    - 리소스·프롬프트·initialize 정보는 표준 MCP 수집일 때만 갱신한다.
    diff: ``tools``(added/changed/not_exposed), ``groups``, (mcp 면) ``resources``·``prompts``.
    """
    now = _now()
    forge = _forge_of(server)
    groups = await _ensure_groups(session, server)
    groups_by_id = {g.id: g for g in groups.values()}
    existing = {
        t.name: t for t in (
            await session.execute(select(McpServerTool).where(McpServerTool.mcp_server_id == server.id))
        ).scalars().all()
    }
    old_view = {n: {"description": t.description, "input_schema": t.input_schema, "annotations": t.annotations}
                for n, t in existing.items() if t.source not in ("forge_seed",) and not t.removed_at}
    new_view: dict[str, dict] = {}
    seen: set[str] = set()
    for raw in snap.tools:
        name = raw.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        annotations = raw.get("annotations")
        mutating_flag = raw.get("_mutating")
        new_view[name] = {"description": raw.get("description"), "input_schema": raw.get("inputSchema"),
                          "annotations": annotations}
        tool = existing.get(name)
        if tool is None:
            key = _forge_group_of(forge, name) or (
                ("read" if mutating_flag is False else "unclassified") if forge and snap.source == "forge_rest"
                else normalize.classify_annotations(annotations, mutating_flag)
            )
            group = groups.get(key) or groups["unclassified"]
            tool = McpServerTool(
                mcp_server_id=server.id, group_id=group.id, name=name, mutating=group.mutating,
                risk=group.default_risk, source=snap.source,
                resource_types=list(forge.resource_types.get(name, ())) or None if forge else None,
            )
            session.add(tool)
            existing[name] = tool
        else:
            if tool.source in ("forge_seed", "manual"):
                tool.source = snap.source
            tool.removed_at = None
            group = groups_by_id.get(tool.group_id)
            if group and group.key == "unclassified" and (annotations or mutating_flag is not None):
                key = normalize.classify_annotations(annotations, mutating_flag)
                if key in groups and key != "unclassified":
                    tool.group_id = groups[key].id
                    tool.mutating = groups[key].mutating
                    if not tool.risk_confirmed:
                        tool.risk = groups[key].default_risk
        tool.title = raw.get("title") or tool.title
        tool.description = raw.get("description")
        tool.input_schema = raw.get("inputSchema")
        tool.output_schema = raw.get("outputSchema") or tool.output_schema
        tool.annotations = annotations
        tool.meta = raw.get("_meta") or tool.meta
    await session.flush()

    diff = {"tools": normalize.diff_named(old_view, new_view, ("description", "input_schema", "annotations"))}
    diff["tools"]["not_exposed"] = sorted(n for n in existing if n not in seen)
    diff["tools"].pop("removed")

    # 배포별 노출 상태
    states = {
        s.tool_id: s for s in (
            await session.execute(select(McpDeploymentToolState).where(McpDeploymentToolState.deployment_id == dep.id))
        ).scalars().all()
    }
    for name, tool in existing.items():
        st = states.get(tool.id)
        if st is None:
            st = McpDeploymentToolState(deployment_id=dep.id, tool_id=tool.id)
            session.add(st)
        st.observed_exposed = name in seen
        st.observed_at = now

    # 그룹 on/off 추론: 그룹에 속한 도구 중 하나라도 노출되면 켜짐
    await _init_group_states(session, server, dep)
    exposed_groups: dict[int, bool] = {}
    for name, tool in existing.items():
        if tool.group_id is None:
            continue
        exposed_groups[tool.group_id] = exposed_groups.get(tool.group_id, False) or name in seen
    gstates = (
        await session.execute(select(McpDeploymentGroupState).where(McpDeploymentGroupState.deployment_id == dep.id))
    ).scalars().all()
    group_changes = {}
    for gs in gstates:
        if gs.group_id not in exposed_groups:
            continue
        new = exposed_groups[gs.group_id]
        if gs.observed_enabled != new:
            group_changes[groups_by_id[gs.group_id].key] = [gs.observed_enabled, new]
        gs.observed_enabled = new
        gs.observed_basis = "inferred"
    diff["groups"] = group_changes

    # 리소스·프롬프트 (MCP 프로토콜일 때만)
    if snap.source == "mcp":
        diff["resources"] = await _upsert_resources(session, server, snap)
        diff["prompts"] = await _upsert_prompts(session, server, snap)
        server.server_info = snap.server_info or server.server_info
        server.protocol_version = snap.protocol_version or server.protocol_version
        server.capabilities = snap.capabilities or server.capabilities
        server.instructions = snap.instructions or server.instructions
        dep.server_info = snap.server_info or None
        dep.protocol_version = snap.protocol_version
        dep.deployed_version = (snap.server_info or {}).get("version") or dep.deployed_version
    fingerprint = normalize.capability_fingerprint(snap.tools, snap.resources + snap.resource_templates,
                                                   snap.prompts)
    server.capability_hash = fingerprint
    server.last_synced_at = now
    dep.observed_hash = fingerprint
    dep.last_observed_at = now
    dep.observed_source = snap.source
    if snap.latency_ms is not None:
        dep.latency_ms = snap.latency_ms
        dep.health_status = "healthy"
        dep.last_health_check_at = now
    await session.flush()
    logger.debug("MCP 스냅숏 반영: server=%s deployment=%s source=%s exposed=%d added=%d changed=%d group_changes=%d",
                 server.name, dep.name, snap.source, len(seen), len(diff["tools"]["added"]),
                 len(diff["tools"]["changed"]), len(group_changes))
    return diff


async def _upsert_resources(session: AsyncSession, server: McpServer, snap: McpSnapshot) -> dict:
    """수집된 리소스·템플릿을 uri 기준 upsert. 이번에 빠진 mcp 출처 행은 removed_at 표시. 반환 {added, removed}."""
    rows = {
        r.uri: r for r in (
            await session.execute(select(McpServerResource).where(McpServerResource.mcp_server_id == server.id))
        ).scalars().all()
    }
    incoming = [(r.get("uri"), False, r) for r in snap.resources] + [
        (r.get("uriTemplate"), True, r) for r in snap.resource_templates
    ]
    seen = set()
    added = []
    for uri, is_template, raw in incoming:
        if not uri or uri in seen:
            continue
        seen.add(uri)
        row = rows.get(uri)
        if row is None:
            row = McpServerResource(mcp_server_id=server.id, uri=uri[:1000], source="mcp")
            session.add(row)
            added.append(uri)
        row.is_template = is_template
        row.name, row.title = raw.get("name"), raw.get("title")
        row.description, row.mime_type = raw.get("description"), raw.get("mimeType")
        row.annotations = raw.get("annotations")
        row.removed_at = None
    removed = []
    for uri, row in rows.items():
        if uri not in seen and row.source == "mcp" and not row.removed_at:
            row.removed_at = _now()
            removed.append(uri)
    return {"added": added, "removed": removed}


async def _upsert_prompts(session: AsyncSession, server: McpServer, snap: McpSnapshot) -> dict:
    """수집된 프롬프트를 이름 기준 upsert. 이번에 빠진 mcp 출처 행은 removed_at 표시. 반환 {added, removed}."""
    rows = {
        p.name: p for p in (
            await session.execute(select(McpServerPrompt).where(McpServerPrompt.mcp_server_id == server.id))
        ).scalars().all()
    }
    seen, added = set(), []
    for raw in snap.prompts:
        name = raw.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        row = rows.get(name)
        if row is None:
            row = McpServerPrompt(mcp_server_id=server.id, name=name, source="mcp")
            session.add(row)
            added.append(name)
        row.title, row.description, row.arguments = raw.get("title"), raw.get("description"), raw.get("arguments")
        row.removed_at = None
    removed = []
    for name, row in rows.items():
        if name not in seen and row.source == "mcp" and not row.removed_at:
            row.removed_at = _now()
            removed.append(name)
    return {"added": added, "removed": removed}


def _rest_base(dep: McpDeployment) -> str | None:
    """Forge REST API 기준 URL — 명시값, 없으면 endpoint origin + ``/api/v1``."""
    if dep.rest_api_base:
        return dep.rest_api_base
    if dep.endpoint:
        return origin_of(dep.endpoint) + "/api/v1"
    return None


async def _after_observe(session: AsyncSession, server: McpServer, dep: McpDeployment | None, *,
                         source: str, by: str | None, reported_version: str | None = None) -> None:
    """관측값 반영 뒤 공통 후처리 — 서버 버전 스냅숏 기록 + 배포 finding 재계산 (commit 은 호출자)."""
    from app.mcp_servers.policy import evaluate_findings, evaluate_server_deployments
    from app.mcp_servers.versions import record_version

    v = await record_version(session, server, source=source, deployment=dep, by=by,
                             reported_version=reported_version or (dep.deployed_version if dep else None))
    if v is not None:
        logger.info("MCP 서버 새 버전 기록: %s (%s, tools=%d)", server.name, v.capability_hash[:12], v.tool_count)
        # 정의가 바뀌면 같은 서버의 다른 배포 finding 도 바뀔 수 있다
        await evaluate_server_deployments(session, server.id)
    elif dep is not None:
        logger.debug("MCP 정의 변화 없음 — 배포 finding 만 재계산: deployment=%s", dep.name)
        await evaluate_findings(session, dep)


async def collect_deployment(session: AsyncSession, name: str, req: CollectRequest, by: str | None = None,
                             transport: httpx.AsyncBaseTransport | None = None) -> CollectResult | None:
    """배포에서 도구 등을 수집해 정의·관측값에 반영한다(배포가 없으면 None).

    source=auto: Forge 면 manifest → 404/405 면 도구 REST, 그 밖은 표준 MCP. 수집 실패는 예외가 아니라
    ``ok=False`` 결과와 실패 이력으로 남긴다. ``dry_run`` 이면 반영 후 롤백해 diff 만 돌려준다.
    ``transport`` 는 테스트용 httpx 전송 주입.
    """
    from app.mcp_servers.manifest import parse_manifest

    dep = await _get_deployment(session, name)
    if not dep:
        return None
    server = await session.get(McpServer, dep.mcp_server_id)
    source = req.source
    is_forge = server.framework == "argus-forge"
    started = time.monotonic()
    snap: McpSnapshot | None = None
    manifest = None
    latency = None
    fallback_note = None
    try:
        if dep.transport == "stdio":
            raise CollectError("stdio 배포는 카탈로그에서 실행하지 않습니다 — 설정 파일·manifest 가져오기를 사용하세요")
        rest_base = _rest_base(dep)
        if not dep.endpoint and not (source != "mcp" and dep.rest_api_base):
            raise CollectError("endpoint 가 없습니다")
        credential = await _load_credential(session, dep)
        if source == "manifest" or (source == "auto" and is_forge):
            # 설계 §6.2: manifest 를 먼저 시도하고, 없으면(404/405) 도구 REST 로 폴백
            try:
                body, latency = await fetch_forge_manifest(rest_base, credential, transport=transport)
                manifest = parse_manifest(body)
                source = "manifest"
            except ManifestNotAvailableError as exc:
                if source == "manifest":
                    raise
                fallback_note = str(exc)
                source = "forge_rest"
                logger.info("MCP manifest 없음 — 도구 REST 로 폴백: deployment=%s", name)
            except ValueError as exc:
                raise CollectError(str(exc)) from exc
        elif source == "auto":
            source = "mcp"
        if manifest is None:
            if dep.transport == "sse" and source == "mcp":
                raise CollectError("레거시 SSE 전송 수집은 지원하지 않습니다 (Streamable HTTP 필요)")
            if source == "forge_rest":
                snap = await collect_forge_rest(rest_base, credential, transport=transport)
            else:
                if not dep.endpoint:
                    raise CollectError("endpoint 가 없습니다")
                snap = await collect_mcp(dep.endpoint, credential, transport=transport)
    except CollectError as exc:
        ms = int((time.monotonic() - started) * 1000)
        logger.warning("MCP 수집 실패: %s (%s) %s", name, source, exc)
        if not req.dry_run:
            _log_sync(session, server.id, dep.id, "collect", source, False, str(exc), duration_ms=ms, by=by)
            await session.commit()
        return CollectResult(ok=False, source=source, message=str(exc), dry_run=req.dry_run)

    if manifest is not None:
        from app.mcp_servers.manifest import apply_manifest

        diff = await apply_manifest(session, server, dep, manifest)
        dep.latency_ms = latency
        dep.health_status = "healthy"
        dep.last_health_check_at = _now()
        dep.health_fail_count = 0
        tool_count = len(manifest.tools)
        exposed = sum(1 for t in manifest.tools if t.get("exposed", True))
    else:
        diff = await _apply_snapshot(session, server, dep, snap)
        tool_count = exposed = len(snap.tools)
        if snap.latency_ms is not None:
            dep.health_fail_count = 0
    total_tools = await session.scalar(
        select(func.count(McpServerTool.id)).where(McpServerTool.mcp_server_id == server.id,
                                                   McpServerTool.removed_at.is_(None))
    )
    message = f"도구 {tool_count}개 수집"
    if fallback_note:
        message += f" (manifest 없음 → 도구 REST 로 수집: {fallback_note})"
    if req.dry_run:
        await session.rollback()
        logger.debug("MCP 수집 dry-run — 롤백: deployment=%s source=%s tools=%d", name, source, tool_count)
    else:
        await _after_observe(session, server, dep, source=source, by=by,
                             reported_version=(manifest.server.get("version") if manifest else None))
        _log_sync(session, server.id, dep.id, "collect", source, True, message, diff,
                  int((time.monotonic() - started) * 1000), by)
        await session.commit()
        await _reindex(server.id)
        logger.info("MCP 수집 완료: %s (%s) tools=%d", name, source, tool_count)
    return CollectResult(ok=True, source=source, message=message, dry_run=req.dry_run, diff=diff,
                         tool_count=total_tools or 0, exposed_count=exposed)


async def import_manifest(session: AsyncSession, *, server_name: str | None = None,
                          deployment_name: str | None = None, req: ManifestImportRequest,
                          by: str | None = None) -> CollectResult | None:
    """manifest 파일 가져오기. 서버 대상이면 정의만, 배포 대상이면 정의+관측값을 갱신한다."""
    from app.mcp_servers.manifest import apply_manifest, parse_manifest

    logger.debug("MCP manifest 가져오기 시작: server=%s deployment=%s dry_run=%s",
                 server_name, deployment_name, req.dry_run)
    dep = None
    if deployment_name:
        dep = await _get_deployment(session, deployment_name)
        if not dep:
            return None
        server = await session.get(McpServer, dep.mcp_server_id)
    else:
        server = await _get_server(session, server_name or "")
        if not server:
            return None
    m = parse_manifest(req.content)
    diff = await apply_manifest(session, server, dep, m)
    exposed = sum(1 for t in m.tools if t.get("exposed", True))
    if req.dry_run:
        await session.rollback()
    else:
        await _after_observe(session, server, dep, source="manifest", by=by,
                             reported_version=m.server.get("version"))
        note = f"manifest 도구 {len(m.tools)}개 반영" + (" (정의만)" if dep is None else "")
        _log_sync(session, server.id, dep.id if dep else None, "manifest_import", "manifest", True, note, diff, by=by)
        await session.commit()
        await _reindex(server.id)
        logger.info("MCP manifest 가져오기: server=%s deployment=%s tools=%d", server.name,
                    dep.name if dep else None, len(m.tools))
    return CollectResult(ok=True, source="manifest", dry_run=req.dry_run, diff=diff, tool_count=len(m.tools),
                         exposed_count=exposed if dep is not None else 0)


async def health_check(session: AsyncSession, name: str, by: str | None = None,
                       transport: httpx.AsyncBaseTransport | None = None) -> DeploymentSummary | None:
    """배포 헬스 체크 후 상태·지연·연속 실패 횟수를 갱신하고 이력·finding 재평가까지 커밋한다.

    stdio 이거나 endpoint 가 없으면 검사하지 않고 unknown 으로 둔다(실패 횟수는 그대로). 배포가 없으면 None.
    """
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    server = await session.get(McpServer, dep.mcp_server_id)
    if dep.transport == "stdio" or not dep.endpoint:
        logger.debug("MCP 헬스 체크 불가(stdio 또는 endpoint 없음): deployment=%s", name)
        dep.health_status = "unknown"
        msg = "stdio 또는 endpoint 없음 — 헬스체크 불가"
        ok = False
    else:
        credential = await _load_credential(session, dep)
        result = await check_health(dep.endpoint, server.framework, credential, transport=transport)
        if result.instance_id and dep.instance_id and result.instance_id != dep.instance_id:
            logger.info("MCP 배포 재시작 감지: %s (%s -> %s)", name, dep.instance_id, result.instance_id)
        dep.health_status = result.status
        dep.latency_ms = result.latency_ms
        dep.instance_id = result.instance_id or dep.instance_id
        msg = result.message or result.status
        ok = result.status == "healthy"
        dep.health_fail_count = 0 if ok else (dep.health_fail_count or 0) + 1
    dep.last_health_check_at = _now()
    _log_sync(session, server.id, dep.id, "health", None, ok, msg, duration_ms=dep.latency_ms, by=by)
    from app.mcp_servers.policy import evaluate_findings

    await session.flush()
    await evaluate_findings(session, dep)
    await session.commit()
    await session.refresh(dep)
    logger.debug("MCP 헬스 체크 결과: deployment=%s status=%s fail_count=%d", name, dep.health_status,
                 dep.health_fail_count or 0)
    return (await _deployment_summaries(session, [dep]))[0]


async def import_config(session: AsyncSession, name: str, req: ConfigImportRequest,
                        by: str | None = None) -> CollectResult | None:
    """Forge 서버 config.yaml → 그룹 on/off·리소스 범위·limits·실행 계정 관측값."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    server = await session.get(McpServer, dep.mcp_server_id)
    forge = _forge_of(server)
    if not forge:
        logger.warning("MCP 설정 가져오기 거부(Forge 서버 아님): deployment=%s framework=%s", name, server.framework)
        raise ValueError("설정 파일 가져오기는 Argus Forge 서버(framework=argus-forge)만 지원합니다")
    obs = normalize.parse_forge_config(req.content, forge)

    groups = await _ensure_groups(session, server)
    await _init_group_states(session, server, dep)
    gstates = {
        gs.group_id: gs for gs in (
            await session.execute(select(McpDeploymentGroupState).where(McpDeploymentGroupState.deployment_id == dep.id))
        ).scalars().all()
    }
    group_changes = {}
    for key, enabled in obs.groups.items():
        g = groups.get(key)
        if not g:
            logger.debug("MCP 설정의 그룹이 정의에 없음 — 무시: deployment=%s group=%s", name, key)
            continue
        gs = gstates[g.id]
        if gs.observed_enabled != enabled:
            group_changes[key] = [gs.observed_enabled, enabled]
        gs.observed_enabled = enabled
        gs.observed_basis = "config"

    # 매핑으로 이름을 아는 도구의 노출 상태 = 그룹 상태
    tools = (
        await session.execute(select(McpServerTool).where(McpServerTool.mcp_server_id == server.id))
    ).scalars().all()
    states = {
        s.tool_id: s for s in (
            await session.execute(select(McpDeploymentToolState).where(McpDeploymentToolState.deployment_id == dep.id))
        ).scalars().all()
    }
    groups_by_id = {g.id: g for g in groups.values()}
    now = _now()
    for t in tools:
        g = groups_by_id.get(t.group_id)
        if not g or g.key not in obs.groups or g.key in ("read", "unclassified"):
            continue
        st = states.get(t.id)
        if st is None:
            st = McpDeploymentToolState(deployment_id=dep.id, tool_id=t.id)
            session.add(st)
        st.observed_exposed = obs.groups[g.key]
        st.observed_at = now

    old_scopes = (
        await session.execute(
            select(McpResourceScope).where(McpResourceScope.deployment_id == dep.id,
                                           McpResourceScope.layer == "observed",
                                           McpResourceScope.source == "config")
        )
    ).scalars().all()
    for s in old_scopes:
        await session.delete(s)
    for sc in obs.scopes:
        session.add(McpResourceScope(deployment_id=dep.id, layer="observed", source="config", match="fnmatch", **sc))

    dep.limits = obs.limits or None
    dep.target_endpoint = obs.target_endpoint or dep.target_endpoint
    dep.service_principal = obs.service_principal or dep.service_principal
    dep.principal_auth = obs.principal_auth or dep.principal_auth
    dep.security_warnings = obs.security_warnings or None
    if req.fill_endpoint and not dep.endpoint and obs.listen_endpoint:
        dep.endpoint = obs.listen_endpoint
        if obs.transport:
            dep.transport = obs.transport
    if obs.rest_api_path and dep.endpoint and not dep.rest_api_base:
        dep.rest_api_base = origin_of(dep.endpoint) + obs.rest_api_path
    dep.observed_source = "config"
    dep.last_observed_at = now
    diff = {"groups": group_changes, "scopes": len(obs.scopes), "limits": len(obs.limits),
            "security_warnings": obs.security_warnings}
    if req.dry_run:
        await session.rollback()
        logger.debug("MCP 설정 가져오기 dry-run — 롤백: deployment=%s", name)
    else:
        _log_sync(session, server.id, dep.id, "config_import", "config", True,
                  f"그룹 {len(obs.groups)}개·범위 규칙 {len(obs.scopes)}개 반영", diff, by=by)
        await session.flush()
        from app.mcp_servers.policy import evaluate_findings

        await evaluate_findings(session, dep)
        await session.commit()
        logger.info("MCP 설정 가져오기: %s groups=%s", name, obs.groups)
    return CollectResult(ok=True, source="config", dry_run=req.dry_run, diff=diff)


async def list_sync_log(session: AsyncSession, *, server_name: str | None = None,
                        deployment_name: str | None = None, limit: int = 50) -> list[SyncLogResponse] | None:
    """실행 이력(최신순, 최대 ``limit``). 배포 이름이 우선, 지정한 대상이 없으면 None, 둘 다 없으면 전체."""
    q = select(McpSyncLog)
    if deployment_name:
        dep = await _get_deployment(session, deployment_name)
        if not dep:
            return None
        q = q.where(McpSyncLog.deployment_id == dep.id)
    elif server_name:
        server = await _get_server(session, server_name)
        if not server:
            return None
        q = q.where(McpSyncLog.mcp_server_id == server.id)
    rows = (await session.execute(q.order_by(McpSyncLog.id.desc()).limit(limit))).scalars().all()
    return [SyncLogResponse.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# 실효 상태·리소스 범위·시뮬레이터
# ---------------------------------------------------------------------------


async def _effective_tools(session: AsyncSession, dep: McpDeployment) -> tuple[list[EffectiveTool], list[EffectiveGroup]]:
    """배포의 실효 도구·그룹 상태(제거된 도구 제외).

    도구 노출은 도구 관측값, 없으면 그룹 관측값으로 대신한다. 승인 필요는 배포 승인값 또는 도구 정의값.
    """
    groups = (
        await session.execute(
            select(McpServerToolGroup).where(McpServerToolGroup.mcp_server_id == dep.mcp_server_id)
            .order_by(McpServerToolGroup.sort_order, McpServerToolGroup.key)
        )
    ).scalars().all()
    by_id = {g.id: g for g in groups}
    gstates = {
        gs.group_id: gs for gs in (
            await session.execute(select(McpDeploymentGroupState).where(McpDeploymentGroupState.deployment_id == dep.id))
        ).scalars().all()
    }
    tools = (
        await session.execute(
            select(McpServerTool).where(McpServerTool.mcp_server_id == dep.mcp_server_id,
                                        McpServerTool.removed_at.is_(None))
            .order_by(McpServerTool.name)
        )
    ).scalars().all()
    tstates = {
        s.tool_id: s for s in (
            await session.execute(select(McpDeploymentToolState).where(McpDeploymentToolState.deployment_id == dep.id))
        ).scalars().all()
    }
    eff_tools = []
    counts: Counter = Counter()
    exposed_counts: Counter = Counter()
    for t in tools:
        st = tstates.get(t.id)
        g = by_id.get(t.group_id)
        exposed = st.observed_exposed if st else None
        if exposed is None and g is not None:
            gs = gstates.get(g.id)
            if gs and gs.observed_enabled is not None:
                exposed = gs.observed_enabled
        eff_tools.append(EffectiveTool(
            id=t.id, name=t.name, group_key=g.key if g else None, risk=t.risk, mutating=t.mutating,
            exposed=exposed, blocked=bool(st and st.approved_blocked),
            requires_approval=bool((st and st.approved_requires_approval) or t.requires_approval),
            resource_types=t.resource_types, source=t.source,
        ))
        if g:
            counts[g.id] += 1
            if exposed:
                exposed_counts[g.id] += 1
    eff_groups = []
    for g in groups:
        gs = gstates.get(g.id)
        eff_groups.append(EffectiveGroup(
            id=g.id, key=g.key, label=g.label, config_key=g.config_key, risk=g.default_risk, mutating=g.mutating,
            observed_enabled=gs.observed_enabled if gs else None, observed_basis=gs.observed_basis if gs else None,
            approved_enabled=gs.approved_enabled if gs else None, tool_count=counts[g.id],
            exposed_count=exposed_counts[g.id],
        ))
    return eff_tools, eff_groups


async def get_effective(session: AsyncSession, name: str) -> EffectiveState | None:
    """배포 실효 상태(그룹·도구·배포 전체 범위·limits·실행 계정). 배포가 없으면 None."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    server = await session.get(McpServer, dep.mcp_server_id)
    tools, groups = await _effective_tools(session, dep)
    scopes = await _scopes(session, dep.id)
    return EffectiveState(
        deployment=dep.name, server=server.name, framework=server.framework, observed_source=dep.observed_source,
        last_observed_at=dep.last_observed_at, target_endpoint=dep.target_endpoint,
        service_principal=dep.service_principal, principal_auth=dep.principal_auth, limits=dep.limits,
        security_warnings=list(dep.security_warnings or []), groups=groups, tools=tools, scopes=scopes,
    )


async def _scopes(session: AsyncSession, dep_id: int, agent_link_id: int | None = None) -> list[ResourceScopeResponse]:
    """배포 범위 규칙(observed·approved 모두) — ``agent_link_id`` 가 None 이면 배포 전체 규칙만, 아니면 그 연결 규칙만."""
    q = select(McpResourceScope).where(McpResourceScope.deployment_id == dep_id)
    q = q.where(McpResourceScope.agent_link_id.is_(None) if agent_link_id is None
                else McpResourceScope.agent_link_id == agent_link_id)
    rows = (await session.execute(q.order_by(McpResourceScope.resource_type, McpResourceScope.id))).scalars().all()
    return [ResourceScopeResponse.model_validate(r) for r in rows]


async def add_scope(session: AsyncSession, name: str, req: ResourceScopeCreate) -> ResourceScopeResponse | None:
    """관측 계층에 수동 범위 규칙 추가(source=manual). 배포가 없으면 None, 정규식 오류면 ValueError."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    if req.match == "regex":
        for p in req.patterns:
            try:
                re.compile(p)
            except re.error as exc:
                logger.warning("MCP 범위 규칙 정규식 오류: deployment=%s pattern=%s error=%s", name, p, exc)
                raise ValueError(f"정규식 오류 '{p}': {exc}") from exc
    row = McpResourceScope(deployment_id=dep.id, layer="observed", source="manual", **req.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info("MCP 관측 범위 규칙 추가: %s type=%s effect=%s", name, row.resource_type, row.effect)
    return ResourceScopeResponse.model_validate(row)


async def delete_scope(session: AsyncSession, name: str, scope_id: int) -> bool:
    """범위 규칙 삭제(관측 계층만). 대상이 없으면 False, 승인 계층이면 ValueError."""
    dep = await _get_deployment(session, name)
    row = await session.get(McpResourceScope, scope_id) if dep else None
    if not row or row.deployment_id != dep.id:
        return False
    if row.layer == "approved":
        logger.warning("승인 범위 규칙 직접 삭제 거부: deployment=%s scope_id=%d", name, scope_id)
        raise ValueError("승인 범위는 승인 정책 화면에서 변경하세요")
    await session.delete(row)
    await session.commit()
    logger.info("MCP 관측 범위 규칙 삭제: %s scope_id=%d", name, scope_id)
    return True


def _matches(scope: ResourceScopeResponse, value: str) -> bool:
    """규칙 패턴 중 하나라도 값과 맞으면 True — exact(일치) / regex(전체 일치) / fnmatch(대소문자 구분)."""
    for p in scope.patterns:
        if scope.match == "exact" and value == p:
            return True
        if scope.match == "regex" and re.fullmatch(p, value):
            return True
        if scope.match == "fnmatch" and fnmatch.fnmatchcase(value, p):
            return True
    return False


def evaluate_scopes(scopes: list[ResourceScopeResponse], req: SimulateRequest) -> SimulateResult:
    """Forge 와 같은 판정 순서: protect(write·delete 만) > deny > allow 목록. delete 는 write 규칙도 적용.

    allow 규칙이 하나라도 있으면 목록 밖은 deny, 해당 규칙이 전혀 없으면 unknown. mask 규칙은 판정에 쓰지 않는다.
    """
    actions = {req.action, "any"} | ({"write"} if req.action == "delete" else set())
    relevant = [s for s in scopes if s.resource_type == req.resource_type and s.action in actions
                and s.effect != "mask"]
    if req.action in ("write", "delete"):
        hit = [s for s in relevant if s.effect == "protect" and _matches(s, req.resource)]
        if hit:
            return SimulateResult(decision="deny", reason="보호(protect) 규칙에 해당", matched=hit)
    hit = [s for s in relevant if s.effect == "deny" and _matches(s, req.resource)]
    if hit:
        return SimulateResult(decision="deny", reason="거부(deny) 규칙에 해당", matched=hit)
    allows = [s for s in relevant if s.effect == "allow"]
    if allows:
        hit = [s for s in allows if _matches(s, req.resource)]
        if hit:
            return SimulateResult(decision="allow", reason="허용(allow) 목록에 해당", matched=hit)
        return SimulateResult(decision="deny", reason="허용(allow) 목록에 없음", matched=allows)
    return SimulateResult(decision="unknown",
                          reason="해당 리소스 종류·동작에 대한 규칙이 없음 — 서버 기본 동작과 대상 시스템 권한을 따름")


async def simulate(session: AsyncSession, name: str, req: SimulateRequest) -> SimulateResult | None:
    """리소스 접근 시뮬레이션 — observed / approved / combined(모든 계층 통과해야 허용). 배포가 없으면 None."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    logger.debug("MCP 접근 시뮬레이션: deployment=%s type=%s action=%s layer=%s",
                 name, req.resource_type, req.action, req.layer)
    scopes = await _scopes(session, dep.id)
    observed = [s for s in scopes if s.layer == "observed"]
    approved = [s for s in scopes if s.layer == "approved"]
    if req.layer == "observed":
        return evaluate_scopes(observed, req)
    if req.layer == "approved":
        return evaluate_scopes(approved, req)
    # combined: 서버 설정·승인 정책(·에이전트 범위)을 모두 통과해야 허용 — 가장 좁은 쪽
    layers = [("observed", "서버 설정", evaluate_scopes(observed, req)),
              ("approved", "승인 정책", evaluate_scopes(approved, req))]
    if req.agent_link_id is not None:
        agent_scopes = (await _agent_scopes(session, dep.id, [req.agent_link_id])).get(req.agent_link_id, [])
        layers.append(("agent", "에이전트 범위", evaluate_scopes(agent_scopes, req)))
    by_layer = {key: {"decision": r.decision, "reason": r.reason} for key, _, r in layers}
    for _, label, r in layers:
        if r.decision == "deny":
            return SimulateResult(decision="deny", reason=f"{label}: {r.reason}", matched=r.matched,
                                  by_layer=by_layer)
    allowed = [(label, r) for _, label, r in layers if r.decision == "allow"]
    if allowed:
        if len(allowed) == len(layers):
            reason = "·".join(label for _, label, _ in layers) + " 모두 허용"
        else:
            missing = "·".join(label for _, label, r in layers if r.decision != "allow")
            reason = "·".join(label for label, _ in allowed) + f" 허용 ({missing}에 해당 규칙 없음)"
        return SimulateResult(decision="allow", reason=reason,
                              matched=[m for _, r in allowed for m in r.matched], by_layer=by_layer)
    return SimulateResult(decision="unknown", reason=layers[0][2].reason, matched=[], by_layer=by_layer)


# ---------------------------------------------------------------------------
# AI Agent 연계
# ---------------------------------------------------------------------------


def _filter_agent_tools(tools: list[EffectiveTool], allowed_groups: list[str] | None,
                        allowed_tools: list[str] | None) -> list[EffectiveTool]:
    """에이전트가 쓸 수 있는 도구 — 미노출 확정·차단 도구를 빼고, 허용 그룹·도구 목록이 있으면 그 안으로 좁힌다."""
    out = [t for t in tools if t.exposed is not False and not t.blocked]
    if allowed_groups:
        out = [t for t in out if t.group_key in allowed_groups]
    if allowed_tools:
        out = [t for t in out if t.name in allowed_tools]
    return out


async def effective_tools_for_link(session: AsyncSession, link: AIAgentMcpServer) -> list[tuple[EffectiveTool, McpServerTool]]:
    """에이전트 연결의 실효 도구 (노출 − 차단 ∩ 허용 그룹·도구)."""
    if not link.mcp_deployment_id:
        return []
    dep = await session.get(McpDeployment, link.mcp_deployment_id)
    if not dep or dep.status in ("blocked", "retired"):
        return []
    tools, _ = await _effective_tools(session, dep)
    chosen = _filter_agent_tools(tools, link.allowed_groups, link.allowed_tools)
    rows = {
        t.id: t for t in (
            await session.execute(select(McpServerTool).where(McpServerTool.id.in_([c.id for c in chosen])))
        ).scalars().all()
    } if chosen else {}
    return [(c, rows[c.id]) for c in chosen if c.id in rows]


async def resolve_deployment(session: AsyncSession, name: str) -> tuple[McpDeployment, McpServer] | None:
    """배포 이름 → (배포, 서버 정의). 없으면 None."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    return dep, await session.get(McpServer, dep.mcp_server_id)


async def validate_agent_scope(session: AsyncSession, dep: McpDeployment, allowed_groups: list[str] | None,
                               allowed_tools: list[str] | None) -> None:
    """에이전트 연결의 허용 그룹·도구가 배포 서버 정의에 있는지 검사한다. 없으면 ValueError."""
    if allowed_groups:
        keys = set((await _groups_by_key(session, dep.mcp_server_id)).keys())
        unknown = sorted(set(allowed_groups) - keys)
        if unknown:
            logger.warning("에이전트 MCP 허용 그룹 검증 실패: deployment=%s unknown=%s", dep.name, unknown)
            raise ValueError(f"알 수 없는 도구 그룹: {', '.join(unknown)}")
    if allowed_tools:
        names = set(
            (await session.execute(select(McpServerTool.name).where(McpServerTool.mcp_server_id == dep.mcp_server_id)))
            .scalars().all()
        )
        unknown = sorted(set(allowed_tools) - names)
        if unknown:
            logger.warning("에이전트 MCP 허용 도구 검증 실패: deployment=%s unknown=%s", dep.name, unknown[:10])
            raise ValueError(f"알 수 없는 도구: {', '.join(unknown)}")


async def enrich_agent_links(session: AsyncSession, links: list[AIAgentMcpServer]) -> list[dict]:
    """AI Agent 응답용 MCP 연결 정보 (카탈로그 배포 정보 포함)."""
    dep_ids = {link.mcp_deployment_id for link in links if link.mcp_deployment_id}
    deps = {d.id: d for d in (
        await session.execute(select(McpDeployment).where(McpDeployment.id.in_(dep_ids)))
    ).scalars().all()} if dep_ids else {}
    servers = dict((
        await session.execute(select(McpServer.id, McpServer.name)
                              .where(McpServer.id.in_({d.mcp_server_id for d in deps.values()})))
    ).all()) if deps else {}
    out = []
    for link in links:
        item = {
            "id": link.id, "name": link.name, "url": link.url, "auth_method": link.auth_method,
            "description": link.description, "mcp_deployment_id": link.mcp_deployment_id,
            "allowed_groups": link.allowed_groups, "allowed_tools": link.allowed_tools, "registered": False,
        }
        dep = deps.get(link.mcp_deployment_id)
        if dep:
            item.update(
                registered=True, mcp_deployment_name=dep.name, mcp_deployment_urn=dep.urn,
                mcp_server_name=servers.get(dep.mcp_server_id), deployment_status=dep.status,
                deployment_environment=dep.environment,
                effective_tool_count=len(await effective_tools_for_link(session, link)),
            )
        out.append(item)
    return out


async def list_deployment_agents(session: AsyncSession, name: str) -> list[DeploymentAgentLink] | None:
    """배포에 연결된 에이전트 목록 — 실효 도구 수·에이전트별 범위 포함. 배포가 없으면 None."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    rows = (
        await session.execute(
            select(AIAgentMcpServer, AIAgent)
            .join(AIAgent, AIAgent.id == AIAgentMcpServer.agent_id)
            .where(AIAgentMcpServer.mcp_deployment_id == dep.id)
            .order_by(AIAgent.name)
        )
    ).all()
    tools, _ = await _effective_tools(session, dep)
    scopes = await _agent_scopes(session, dep.id, [link.id for link, _ in rows])
    return [
        DeploymentAgentLink(
            link_id=link.id, agent_name=agent.name, agent_display_name=agent.display_name,
            agent_status=agent.status, allowed_groups=link.allowed_groups, allowed_tools=link.allowed_tools,
            effective_tool_count=len(_filter_agent_tools(tools, link.allowed_groups, link.allowed_tools)),
            agent_urn=agent.urn, resource_scopes=scopes.get(link.id, []),
        )
        for link, agent in rows
    ]


async def _agent_scopes(session: AsyncSession, dep_id: int, link_ids: list[int]) -> dict[int, list[ResourceScopeResponse]]:
    """에이전트 연결별 범위 규칙 {link_id: [규칙]}."""
    if not link_ids:
        return {}
    out: dict[int, list[ResourceScopeResponse]] = {}
    for r in (
        await session.execute(
            select(McpResourceScope)
            .where(McpResourceScope.deployment_id == dep_id, McpResourceScope.agent_link_id.in_(link_ids))
            .order_by(McpResourceScope.resource_type, McpResourceScope.id)
        )
    ).scalars().all():
        out.setdefault(r.agent_link_id, []).append(ResourceScopeResponse.model_validate(r))
    return out


async def put_agent_scopes(session: AsyncSession, name: str, link_id: int, scopes: list[ResourceScopeCreate],
                           by: str | None = None) -> list[ResourceScopeResponse] | None:
    """에이전트별 리소스 범위 교체 — 인스턴스 승인 범위 위에 추가 제한(좁히기만).

    policy_version 은 바꾸지 않지만 번들 본문이 바뀌므로 ETag 가 달라져 서버가 다음 갱신 때 받는다.
    """
    dep = await _get_deployment(session, name)
    link = await session.get(AIAgentMcpServer, link_id) if dep else None
    if not link or link.mcp_deployment_id != dep.id:
        return None
    for sc in scopes:
        if sc.match == "regex":
            for p in sc.patterns:
                try:
                    re.compile(p)
                except re.error as exc:
                    logger.warning("에이전트 범위 규칙 정규식 오류: deployment=%s link=%d pattern=%s error=%s",
                                   name, link_id, p, exc)
                    raise ValueError(f"정규식 오류 '{p}': {exc}") from exc
    for old in (
        await session.execute(select(McpResourceScope).where(McpResourceScope.agent_link_id == link.id))
    ).scalars().all():
        await session.delete(old)
    for sc in scopes:
        session.add(McpResourceScope(deployment_id=dep.id, agent_link_id=link.id, layer="approved",
                                     source="manual", **sc.model_dump()))
    await session.commit()
    logger.info("MCP 에이전트 리소스 범위 저장: %s link=%s rules=%d by=%s", name, link.id, len(scopes), by)
    return (await _agent_scopes(session, dep.id, [link.id])).get(link.id, [])


async def list_unregistered(session: AsyncSession) -> list[UnregisteredMcp]:
    """카탈로그 배포에 연결되지 않은 에이전트 MCP 연결을 (이름 소문자, URL 끝 / 제거) 기준으로 묶는다."""
    rows = (
        await session.execute(
            select(AIAgentMcpServer, AIAgent.name)
            .join(AIAgent, AIAgent.id == AIAgentMcpServer.agent_id)
            .where(AIAgentMcpServer.mcp_deployment_id.is_(None))
            .order_by(AIAgentMcpServer.name)
        )
    ).all()
    grouped: dict[tuple, dict] = {}
    for link, agent_name in rows:
        key = (link.name.strip().lower(), (link.url or "").strip().rstrip("/"))
        g = grouped.setdefault(key, {"name": link.name, "url": link.url, "auth_method": link.auth_method,
                                     "agents": [], "link_ids": []})
        g["agents"].append(agent_name)
        g["link_ids"].append(link.id)
    return [UnregisteredMcp(agent_count=len(set(g["agents"])), **{**g, "agents": sorted(set(g["agents"]))})
            for g in grouped.values()]


async def link_unregistered(session: AsyncSession, deployment_name: str, link_ids: list[int]) -> int | None:
    """아직 미등록인 연결만 배포에 연결하고 연결한 건수를 돌려준다. 배포가 없으면 None."""
    dep = await _get_deployment(session, deployment_name)
    if not dep:
        return None
    rows = (
        await session.execute(
            select(AIAgentMcpServer).where(AIAgentMcpServer.id.in_(link_ids),
                                           AIAgentMcpServer.mcp_deployment_id.is_(None))
        )
    ).scalars().all()
    for r in rows:
        r.mcp_deployment_id = dep.id
    await session.commit()
    logger.info("미등록 MCP 연결 → 배포 %s: %d건", deployment_name, len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# 가져오기 / 내보내기 / Forge 시드
# ---------------------------------------------------------------------------


async def export_server_json(session: AsyncSession, name: str) -> dict | None:
    """서버 정의 → server.json(제거된 도구 제외, active HTTP 배포는 remotes 로). 서버가 없으면 None."""
    detail = await get_server_detail(session, name)
    if not detail:
        return None
    return interop.export_server_json(
        detail.model_dump(),
        tools=[{**t.model_dump(), "group": t.group_key} for t in detail.tools if not t.removed_at],
        deployments=[d.model_dump() for d in detail.deployments],
    )


async def import_documents(session: AsyncSession, req: ImportRequest, by: str | None = None) -> ImportResult:
    """server.json / mcp.json 가져오기 — 항목별로 생성·이미 있음·오류를 기록하고 한 번에 커밋(dry_run 이면 롤백).

    배포 이름은 ``<이름>-<환경 소문자>`` 로 만든다. 문서 형식 오류는 ValueError.
    """
    import json

    content = req.content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError as exc:
            raise ValueError(f"JSON 파싱 실패: {exc}") from exc
    items: list[ImportItem] = []
    if req.format == "server_json":
        entries = [{"server": s, "deployment": None} for s in interop.parse_server_json(content)]
    else:
        entries = interop.parse_mcp_json(content)
    for entry in entries:
        s = entry["server"]
        if await _get_server(session, s["name"]):
            items.append(ImportItem(kind="server", name=s["name"], action="exists"))
        else:
            try:
                await create_server(session, McpServerCreate(**s), created_by=by, commit=False)
                items.append(ImportItem(kind="server", name=s["name"], action="created"))
            except (ValueError, ConflictError) as exc:
                logger.warning("MCP 가져오기 서버 항목 실패: name=%s error=%s", s["name"], exc)
                items.append(ImportItem(kind="server", name=s["name"], action="error", message=str(exc)))
                continue
        d = entry.get("deployment")
        if d:
            dname = interop.slugify(f"{d['name']}-{req.environment.lower()}")
            if await _get_deployment(session, dname):
                items.append(ImportItem(kind="deployment", name=dname, action="exists"))
                continue
            try:
                await create_deployment(
                    session,
                    DeploymentCreate(**{**d, "name": dname, "server_name": s["name"], "environment": req.environment}),
                    created_by=by, commit=False,
                )
                items.append(ImportItem(kind="deployment", name=dname, action="created"))
            except (ValueError, ConflictError) as exc:
                logger.warning("MCP 가져오기 배포 항목 실패: name=%s error=%s", dname, exc)
                items.append(ImportItem(kind="deployment", name=dname, action="error", message=str(exc)))
    if req.dry_run:
        await session.rollback()
        logger.debug("MCP 가져오기 dry-run — 롤백: format=%s items=%d", req.format, len(items))
    else:
        await session.commit()
        await _reindex_names(session, [i.name for i in items if i.kind == "server" and i.action == "created"])
        logger.info("MCP 가져오기(%s): %d건", req.format, len(items))
    return ImportResult(dry_run=req.dry_run, items=items)


async def seed_forge(session: AsyncSession, by: str | None = None) -> ForgeSeedResult:
    """Argus Forge 10개 서버 정의를 등록 (이미 있으면 그룹·알려진 도구만 보강)."""
    items = []
    for forge in FORGE_SERVERS:
        name = catalog_name(forge)
        server = await _get_server(session, name)
        if server:
            if server.framework != "argus-forge":
                logger.warning("Forge 시드 건너뜀(같은 이름의 비 Forge 서버): %s", name)
                items.append(ImportItem(kind="server", name=name, action="exists",
                                        message="framework 가 argus-forge 가 아니어서 건너뜀"))
                continue
            groups = await _ensure_groups(session, server)
            await _seed_forge_tools(session, server, groups)
            items.append(ImportItem(kind="server", name=name, action="updated"))
            continue
        await create_server(
            session,
            McpServerCreate(
                name=name, framework="argus-forge", provider_type="internal", status="active",
                target_platform=forge.target_platform, display_name=forge.display_name,
                description=forge.description, source_repo=FORGE_REPO, source_path=forge.source_path,
                supported_transports=["stdio", "streamable_http"], category="data-platform",
                packages=[{"registryType": "pypi", "identifier": forge.package_name if forge.name != "minio-aistor"
                           else "minio-aistor-mcp", "transport": {"type": "stdio"}}],
            ),
            created_by=by, commit=False,
        )
        items.append(ImportItem(kind="server", name=name, action="created"))
    await session.commit()
    await _reindex_names(session, [i.name for i in items if i.action in ("created", "updated")])
    logger.info("Forge 시드: %s", [(i.name, i.action) for i in items])
    return ForgeSeedResult(servers=items)


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------


async def _group_count(session: AsyncSession, column, where=None) -> list[NameCount]:
    """컬럼 값별 건수(NULL 은 unknown)."""
    q = select(column, func.count()).group_by(column)
    if where is not None:
        q = q.where(where)
    return [NameCount(name=str(n or "unknown"), count=c) for n, c in (await session.execute(q)).all()]


async def get_stats(session: AsyncSession) -> McpStats:
    """대시보드 통계 — retired 제외 서버·배포 수, 도구 수, 미등록 연결, 변경 그룹·경고가 있는 배포 수, 분포."""
    logger.debug("MCP 대시보드 통계 조회")
    live = McpDeployment.status != "retired"
    mutating_sub = (
        select(func.count(func.distinct(McpDeploymentGroupState.deployment_id)))
        .join(McpServerToolGroup, McpServerToolGroup.id == McpDeploymentGroupState.group_id)
        .join(McpDeployment, McpDeployment.id == McpDeploymentGroupState.deployment_id)
        .where(McpServerToolGroup.mutating.is_(True), McpDeploymentGroupState.observed_enabled.is_(True), live)
    )
    warn_rows = (await session.execute(select(McpDeployment.security_warnings).where(live))).scalars().all()
    return McpStats(
        server_count=await session.scalar(select(func.count(McpServer.id)).where(McpServer.status != "retired")) or 0,
        deployment_count=await session.scalar(select(func.count(McpDeployment.id)).where(live)) or 0,
        tool_count=await session.scalar(select(func.count(McpServerTool.id)).where(McpServerTool.removed_at.is_(None))) or 0,
        unregistered_agent_mcp_count=await session.scalar(
            select(func.count(AIAgentMcpServer.id)).where(AIAgentMcpServer.mcp_deployment_id.is_(None))) or 0,
        deployments_with_mutating=await session.scalar(mutating_sub) or 0,
        deployments_with_warnings=sum(1 for w in warn_rows if w),
        servers_by_status=await _group_count(session, McpServer.status),
        servers_by_provider=await _group_count(session, McpServer.provider_type),
        deployments_by_environment=await _group_count(session, McpDeployment.environment, live),
        deployments_by_health=await _group_count(session, McpDeployment.health_status, live),
    )


# ---------------------------------------------------------------------------
# 통제 현황 (Phase 2 대시보드)
# ---------------------------------------------------------------------------

_ENV_ORDER = ["DEV", "STG", "PROD"]  # 통제 현황 매트릭스의 환경 열 순서(그 밖의 환경은 뒤에 이름순)


def _aware(dt: datetime) -> datetime:
    """SQLite·MariaDB 는 tz 정보 없이 돌려줄 수 있다 — UTC 로 간주."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _enforce_applied(session: AsyncSession, deps: list[McpDeployment]) -> int:
    """현재 번들(ETag)을 받아가 적용 중(applied)인 배포 수."""
    from app.mcp_servers import bundle as bundle_mod

    n = 0
    for d in deps:
        etag = bundle_mod.bundle_etag(await bundle_mod.build_bundle(session, d))
        if bundle_mod.pull_state(d, etag) == "applied":
            n += 1
    return n


async def get_control_overview(session: AsyncSession, *, trend_days: int = 14) -> ControlOverview:
    """통제 현황 대시보드 — 환경×서버 매트릭스, 위험 배포 Top 10, 미해소 finding 분포,
    최근 ``trend_days`` 일 열림/해소 추이, 최근 감사 이벤트 20건, 정책 커버리지(retired 제외).
    """
    logger.debug("MCP 통제 현황 조회: trend_days=%d", trend_days)
    deps = list((
        await session.execute(
            select(McpDeployment).where(McpDeployment.status != "retired")
            .order_by(McpDeployment.environment, McpDeployment.name)
        )
    ).scalars().all())
    summaries = {s.id: s for s in await _deployment_summaries(session, deps)}
    server_ids = {d.mcp_server_id for d in deps} or {0}
    servers = {
        s.id: s for s in (await session.execute(select(McpServer).where(McpServer.id.in_(server_ids)))).scalars().all()
    }
    envs = [e for e in _ENV_ORDER if any(d.environment == e for d in deps)]
    envs += sorted({d.environment for d in deps} - set(envs))

    # 1) 환경 × 서버 매트릭스
    rows: dict[int, ControlRow] = {}
    for d in deps:
        srv = servers.get(d.mcp_server_id)
        if srv is None:
            continue
        row = rows.get(srv.id)
        if row is None:
            row = rows[srv.id] = ControlRow(server=srv.name, server_display_name=srv.display_name,
                                            target_platform=srv.target_platform, cells={})
        s = summaries[d.id]
        row.cells.setdefault(d.environment, []).append(ControlCell(
            deployment=d.name, display_name=d.display_name, status=d.status, health_status=d.health_status,
            policy_mode=d.policy_mode, policy_set=bool(d.policy_version),
            enabled_mutating_groups=s.enabled_mutating_groups, open_findings=s.open_finding_count,
            max_finding_severity=s.max_finding_severity,
        ))
    matrix = sorted(rows.values(), key=lambda r: r.server)

    # 2) 고위험 도구가 노출된 배포 Top
    live_ids = [d.id for d in deps] or [0]
    high_exposed: Counter = Counter()
    for (dep_id,) in (
        await session.execute(
            select(McpDeploymentToolState.deployment_id)
            .join(McpServerTool, McpServerTool.id == McpDeploymentToolState.tool_id)
            .where(McpDeploymentToolState.deployment_id.in_(live_ids),
                   McpDeploymentToolState.observed_exposed.is_(True), McpServerTool.removed_at.is_(None),
                   McpServerTool.risk.in_(("high", "critical")))
        )
    ).all():
        high_exposed[dep_id] += 1
    risky = []
    for d in deps:
        s = summaries[d.id]
        if not high_exposed[d.id] and not s.open_finding_count:
            continue
        risky.append(RiskyDeployment(
            deployment=d.name, server_name=s.server_name, environment=d.environment,
            high_risk_exposed=high_exposed[d.id], exposed_tool_count=s.exposed_tool_count,
            max_exposed_risk=s.max_exposed_risk, enabled_mutating_groups=s.enabled_mutating_groups,
            open_findings=s.open_finding_count, policy_set=bool(d.policy_version),
        ))
    env_weight = {"PROD": 2, "STG": 1}
    risky.sort(key=lambda r: (-env_weight.get(r.environment, 0), -r.high_risk_exposed, -r.open_findings,
                              r.deployment))

    # 3) 미해소 finding 분포
    unresolved = (McpDeploymentFinding.status != "resolved", McpDeploymentFinding.deployment_id.in_(live_ids))
    by_kind = [NameCount(name=k, count=c) for k, c in (
        await session.execute(select(McpDeploymentFinding.kind, func.count()).where(*unresolved)
                              .group_by(McpDeploymentFinding.kind))
    ).all()]
    by_sev = [NameCount(name=k, count=c) for k, c in (
        await session.execute(select(McpDeploymentFinding.severity, func.count()).where(*unresolved)
                              .group_by(McpDeploymentFinding.severity))
    ).all()]
    by_kind.sort(key=lambda x: -x.count)
    by_sev.sort(key=lambda x: -normalize.RISK_ORDER.get(x.name, 0))

    # 4) 일별 열림/해소 추이 (DB 별 날짜 함수 차이를 피하려고 파이썬에서 집계)
    since = (_now() - timedelta(days=trend_days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    trend_acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for first, resolved in (
        await session.execute(
            select(McpDeploymentFinding.first_seen_at, McpDeploymentFinding.resolved_at)
            .where(or_(McpDeploymentFinding.first_seen_at >= since, McpDeploymentFinding.resolved_at >= since))
        )
    ).all():
        if first and _aware(first) >= since:
            trend_acc[_aware(first).date().isoformat()][0] += 1
        if resolved and _aware(resolved) >= since:
            trend_acc[_aware(resolved).date().isoformat()][1] += 1
    trend = []
    for i in range(trend_days):
        day = (since + timedelta(days=i)).date().isoformat()
        opened, resolved_n = trend_acc.get(day, [0, 0])
        trend.append(TrendPoint(date=day, opened=opened, resolved=resolved_n))

    # 5) 최근 변경 도구 호출
    recent = []
    for ev, dname in (
        await session.execute(
            select(McpAuditEvent, McpDeployment.name)
            .join(McpDeployment, McpDeployment.id == McpAuditEvent.deployment_id)
            .order_by(McpAuditEvent.ts.desc(), McpAuditEvent.id.desc()).limit(20)
        )
    ).all():
        r = AuditEventResponse.model_validate(ev)
        r.deployment_name = dname
        recent.append(r)

    coverage = PolicyCoverage(
        deployments=len(deps), with_policy=sum(1 for d in deps if d.policy_version),
        enforce=sum(1 for d in deps if d.policy_mode == "enforce"),
        enforce_applied=await _enforce_applied(session, [d for d in deps if d.policy_mode == "enforce"]),
        with_ingest_token=sum(1 for d in deps if d.ingest_token_hash),
    )
    return ControlOverview(environments=envs, matrix=matrix, risky=risky[:10], findings_by_kind=by_kind,
                           findings_by_severity=by_sev, trend=trend, recent_calls=recent, coverage=coverage)
