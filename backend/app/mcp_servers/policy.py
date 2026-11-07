# SPDX-License-Identifier: Apache-2.0
"""승인 정책·드리프트/보안 finding (설계 §5.4, Phase 2 — L2 거버넌스).

승인 정책(approved)은 Argus 에서만 관리하는 "이 배포는 이렇게 동작해야 한다" 는 기준이다.
  - 그룹 on/off           → ``catalog_mcp_deployment_group_states.approved_enabled``
  - 도구 차단·승인 필요     → ``catalog_mcp_deployment_tool_states.approved_blocked / approved_requires_approval``
  - 리소스 범위            → ``catalog_mcp_resource_scopes`` (layer=approved)
  - limits · 승인 버전     → ``catalog_mcp_deployments.approved_limits / approved_version``

관측값(observed — 서버 설정·manifest·수집 결과)과 비교해 finding 을 만든다. ``observe`` 모드는
서버에 강제하지 않는다(관측·경보). ``enforce`` 모드면 서버가 정책 번들(``bundle.py``)을 pull 해 도구 호출 직전에
집행하고(Phase 3), 받아가지 않으면 ``policy_not_applied`` finding 을 연다.

finding 은 ``fingerprint``(종류+대상)로 한 행을 유지하며, 조건이 사라지면 자동 resolved,
다시 생기면 재오픈한다. 새로 열리거나 재오픈된 finding 은 알림 규칙
(``MCP_POLICY_DRIFT`` / ``MCP_SECURITY_WARNING``)으로 발행한다.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_servers import normalize
from app.mcp_servers.models import (
    McpAuditEvent,
    McpDeployment,
    McpDeploymentFinding,
    McpDeploymentGroupState,
    McpDeploymentToolState,
    McpPolicyHistory,
    McpResourceScope,
    McpServer,
    McpServerTool,
    McpServerToolGroup,
)
from app.mcp_servers.schemas import (
    AdoptObservedRequest,
    DeploymentPolicy,
    FindingResponse,
    FindingUpdate,
    PaginatedFindings,
    PolicyGroup,
    PolicyHistoryResponse,
    PolicyTool,
    PolicyUpdate,
    ResourceScopeResponse,
)

logger = logging.getLogger(__name__)

UNHEALTHY_THRESHOLD = 3  # 연속 헬스 실패가 이 횟수 이상이면 unhealthy finding
AUDIT_VIOLATION_WINDOW = timedelta(days=7)  # 감사 위반 finding 을 집계하는 최근 기간

FINDING_KINDS: dict[str, str] = {
    "group_enabled_unapproved": "승인되지 않은 도구 그룹 켜짐",
    "tool_exposed_blocked": "차단 도구 노출",
    "new_tool_unreviewed": "위험도 미확정 변경 도구 노출",
    "scope_wider_than_approved": "리소스 범위가 승인보다 넓음",
    "limit_exceeds_approved": "제한값이 승인보다 큼",
    "version_mismatch": "승인 버전과 다른 버전 배포",
    "security_warning": "보안 경고",
    "unhealthy": "헬스 체크 연속 실패",
    "audit_policy_violation": "승인 정책 위반 호출",
    "policy_not_applied": "enforce 정책 미적용",
}
# 이 종류는 MCP_SECURITY_WARNING, 나머지는 MCP_POLICY_DRIFT 알림으로 발행
SECURITY_KINDS = {"security_warning"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sev(value: str | None, default: str = "medium") -> str:
    """알려진 위험도(low~critical)면 그대로, 아니면 ``default``."""
    return value if value in normalize.RISK_ORDER else default


# ---------------------------------------------------------------------------
# 승인 정책 조회·저장
# ---------------------------------------------------------------------------


async def _get_deployment(session: AsyncSession, name: str) -> McpDeployment | None:
    """이름으로 배포 조회(없으면 None)."""
    return (await session.execute(select(McpDeployment).where(McpDeployment.name == name))).scalars().first()


async def _scope_rows(session: AsyncSession, dep_id: int, layer: str) -> list[McpResourceScope]:
    """배포 전체에 적용되는(에이전트별 범위 제외) ``layer``(observed/approved) 범위 규칙 목록."""
    return list((
        await session.execute(
            select(McpResourceScope).where(McpResourceScope.deployment_id == dep_id,
                                           McpResourceScope.layer == layer,
                                           McpResourceScope.agent_link_id.is_(None))
            .order_by(McpResourceScope.resource_type, McpResourceScope.id)
        )
    ).scalars().all())


async def build_policy(session: AsyncSession, dep: McpDeployment) -> DeploymentPolicy:
    """배포의 승인 정책 화면 응답 — 그룹·도구의 관측/승인값, 승인/관측 범위, 번들 pull 상태를 모은다.

    부작용: 그룹 상태 행이 없으면 ``_init_group_states`` 가 만든다(flush).
    """
    from app.mcp_servers import service as svc

    server = await session.get(McpServer, dep.mcp_server_id)
    await svc._init_group_states(session, server, dep)
    eff_tools, eff_groups = await svc._effective_tools(session, dep)
    tool_rows = {
        t.id: t for t in (
            await session.execute(select(McpServerTool).where(McpServerTool.mcp_server_id == dep.mcp_server_id))
        ).scalars().all()
    }
    tstates = {
        s.tool_id: s for s in (
            await session.execute(select(McpDeploymentToolState).where(McpDeploymentToolState.deployment_id == dep.id))
        ).scalars().all()
    }
    return DeploymentPolicy(
        deployment=dep.name, policy_mode=dep.policy_mode, policy_version=dep.policy_version,
        policy_updated_at=dep.policy_updated_at, policy_updated_by=dep.policy_updated_by,
        approved_version=dep.approved_version, deployed_version=dep.deployed_version,
        approved_limits=dep.approved_limits, observed_limits=dep.limits,
        groups=[
            PolicyGroup(key=g.key, label=g.label, config_key=g.config_key, mutating=g.mutating, risk=g.risk,
                        observed_enabled=g.observed_enabled, observed_basis=g.observed_basis,
                        approved_enabled=g.approved_enabled)
            for g in eff_groups
        ],
        tools=[
            PolicyTool(
                id=t.id, name=t.name, group_key=t.group_key, risk=t.risk,
                risk_confirmed=bool(tool_rows[t.id].risk_confirmed) if t.id in tool_rows else False,
                mutating=t.mutating, observed_exposed=t.exposed,
                blocked=bool(tstates[t.id].approved_blocked) if t.id in tstates else False,
                requires_approval=tstates[t.id].approved_requires_approval if t.id in tstates else None,
                tool_requires_approval=bool(tool_rows[t.id].requires_approval) if t.id in tool_rows else False,
            )
            for t in eff_tools
        ],
        approved_scopes=[ResourceScopeResponse.model_validate(r) for r in await _scope_rows(session, dep.id, "approved")],
        observed_scopes=[ResourceScopeResponse.model_validate(r) for r in await _scope_rows(session, dep.id, "observed")],
        **await _bundle_state(session, dep),
    )


async def _bundle_state(session: AsyncSession, dep: McpDeployment) -> dict:
    """정책 화면용 번들 상태 — 현재 ETag, 마지막 pull 기록, 적용 상태, 연결 에이전트 수."""
    from app.mcp_servers import bundle as bundle_mod

    b = await bundle_mod.build_bundle(session, dep)
    etag = bundle_mod.bundle_etag(b)
    return {
        "has_service_token": bool(dep.ingest_token_hash), "bundle_etag": etag,
        "policy_pulled_at": dep.policy_pulled_at, "policy_pulled_version": dep.policy_pulled_version,
        "policy_pull_instance": dep.policy_pull_instance, "pull_state": bundle_mod.pull_state(dep, etag),
        "agent_count": len(b["agents"]),
    }


async def get_policy(session: AsyncSession, name: str) -> DeploymentPolicy | None:
    """배포 이름으로 승인 정책 조회(배포가 없으면 None)."""
    dep = await _get_deployment(session, name)
    if not dep:
        logger.debug("승인 정책 조회 대상 배포 없음: deployment=%s", name)
        return None
    return await build_policy(session, dep)


async def policy_snapshot(session: AsyncSession, dep: McpDeployment) -> dict:
    """승인 정책 전체 스냅숏 (이력 저장·Phase 3 정책 번들의 원본)."""
    groups = (
        await session.execute(
            select(McpServerToolGroup.key, McpDeploymentGroupState.approved_enabled)
            .join(McpDeploymentGroupState, McpDeploymentGroupState.group_id == McpServerToolGroup.id)
            .where(McpDeploymentGroupState.deployment_id == dep.id)
            .order_by(McpServerToolGroup.key)
        )
    ).all()
    tools = (
        await session.execute(
            select(McpServerTool.name, McpDeploymentToolState.approved_blocked,
                   McpDeploymentToolState.approved_requires_approval)
            .join(McpDeploymentToolState, McpDeploymentToolState.tool_id == McpServerTool.id)
            .where(McpDeploymentToolState.deployment_id == dep.id)
            .order_by(McpServerTool.name)
        )
    ).all()
    scopes = await _scope_rows(session, dep.id, "approved")
    return {
        "policy_mode": dep.policy_mode,
        "groups": {k: v for k, v in groups if v is not None},
        "blocked_tools": [n for n, b, _ in tools if b],
        "approval_required_tools": [n for n, _, r in tools if r is True],
        "approval_exempt_tools": [n for n, _, r in tools if r is False],
        "resource_scopes": [
            {"resource_type": s.resource_type, "action": s.action, "effect": s.effect,
             "patterns": list(s.patterns or []), "match": s.match}
            for s in scopes
        ],
        "limits": dep.approved_limits or {},
        "approved_version": dep.approved_version,
    }


async def _bump_policy(session: AsyncSession, dep: McpDeployment, note: str | None, by: str | None) -> None:
    """정책 버전을 ``<UTC 시각>#<이력 순번>`` 으로 올리고 현재 스냅숏을 이력에 추가한다(flush 전)."""
    n = (await session.scalar(
        select(func.count(McpPolicyHistory.id)).where(McpPolicyHistory.deployment_id == dep.id)
    ) or 0) + 1
    now = _now()
    dep.policy_version = f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')}#{n}"
    dep.policy_updated_at = now
    dep.policy_updated_by = by
    session.add(McpPolicyHistory(deployment_id=dep.id, policy_version=dep.policy_version,
                                 snapshot=await policy_snapshot(session, dep), note=note, changed_by=by))


def _validate_patterns(match: str, patterns: list[str]) -> None:
    """regex 매칭 규칙의 패턴이 컴파일되는지 검사한다(fnmatch 는 검사하지 않음).

    Raises:
        ValueError: 정규식 오류.
    """
    if match != "regex":
        return
    for p in patterns:
        try:
            re.compile(p)
        except re.error as exc:
            logger.warning("승인 범위 정규식 오류: pattern=%s error=%s", p, exc)
            raise ValueError(f"정규식 오류 '{p}': {exc}") from exc


async def save_policy(session: AsyncSession, name: str, req: PolicyUpdate, by: str | None = None) -> DeploymentPolicy | None:
    """승인 정책 저장 — 요청에 온 항목만 바꾸고 정책 버전·이력을 올린 뒤 finding 을 재평가하고 커밋한다.

    ``scopes`` 는 전체 교체, ``limits``·``approved_version`` 은 요청에 키가 있을 때만 반영한다.
    배포가 없으면 None.

    Raises:
        ValueError: 알 수 없는 그룹·도구, 정규식 오류, 서비스 토큰 없이 enforce 전환.
    """
    from app.mcp_servers import service as svc

    dep = await _get_deployment(session, name)
    if not dep:
        logger.debug("승인 정책 저장 대상 배포 없음: deployment=%s", name)
        return None
    server = await session.get(McpServer, dep.mcp_server_id)
    fields = req.model_fields_set
    if req.groups is not None:
        groups = await svc._groups_by_key(session, server.id)
        unknown = sorted(set(req.groups) - set(groups))
        if unknown:
            logger.warning("승인 정책 저장 거부(알 수 없는 그룹): deployment=%s groups=%s", name, unknown)
            raise ValueError(f"알 수 없는 도구 그룹: {', '.join(unknown)}")
        await svc._init_group_states(session, server, dep)
        gstates = {
            gs.group_id: gs for gs in (
                await session.execute(select(McpDeploymentGroupState).where(McpDeploymentGroupState.deployment_id == dep.id))
            ).scalars().all()
        }
        for key, val in req.groups.items():
            gstates[groups[key].id].approved_enabled = val
    if req.tools is not None:
        tools = {
            t.name: t for t in (
                await session.execute(select(McpServerTool).where(McpServerTool.mcp_server_id == server.id))
            ).scalars().all()
        }
        unknown = sorted(set(req.tools) - set(tools))
        if unknown:
            logger.warning("승인 정책 저장 거부(알 수 없는 도구): deployment=%s tools=%s", name, unknown[:10])
            raise ValueError(f"알 수 없는 도구: {', '.join(unknown[:10])}")
        tstates = {
            s.tool_id: s for s in (
                await session.execute(select(McpDeploymentToolState).where(McpDeploymentToolState.deployment_id == dep.id))
            ).scalars().all()
        }
        for tname, val in req.tools.items():
            t = tools[tname]
            st = tstates.get(t.id)
            if st is None:
                st = McpDeploymentToolState(deployment_id=dep.id, tool_id=t.id)
                session.add(st)
                tstates[t.id] = st
            st.approved_blocked = val.blocked
            st.approved_requires_approval = val.requires_approval
    if req.scopes is not None:
        for sc in req.scopes:
            _validate_patterns(sc.match, sc.patterns)
        for old in await _scope_rows(session, dep.id, "approved"):
            await session.delete(old)
        for sc in req.scopes:
            session.add(McpResourceScope(deployment_id=dep.id, layer="approved", source="manual", **sc.model_dump()))
    if "limits" in fields:
        dep.approved_limits = req.limits or None
    if "approved_version" in fields:
        dep.approved_version = (req.approved_version or "").strip() or None
    if req.policy_mode:
        if req.policy_mode == "enforce" and not dep.ingest_token_hash:
            logger.warning("enforce 전환 거부(서비스 토큰 없음): deployment=%s", name)
            raise ValueError("enforce 모드는 서비스 토큰이 있어야 합니다 — 감사 이벤트 탭에서 서비스 토큰을 먼저 발급하세요")
        dep.policy_mode = req.policy_mode
    await session.flush()
    await _bump_policy(session, dep, req.note, by)
    await evaluate_findings(session, dep)
    await session.commit()
    await session.refresh(dep)
    logger.info("MCP 승인 정책 저장: %s version=%s by=%s", name, dep.policy_version, by)
    return await build_policy(session, dep)


async def adopt_observed(session: AsyncSession, name: str, req: AdoptObservedRequest,
                         by: str | None = None) -> DeploymentPolicy | None:
    """관측값을 승인 기준선으로 채택 (설계 §8-3).

    요청에서 켠 항목(그룹·리소스 범위·limits·버전)만 관측값으로 덮어쓰고, 정책 버전을 올린 뒤
    finding 을 재평가하고 커밋한다. 배포가 없으면 None.
    """
    from app.mcp_servers import service as svc

    dep = await _get_deployment(session, name)
    if not dep:
        logger.debug("관측값 채택 대상 배포 없음: deployment=%s", name)
        return None
    server = await session.get(McpServer, dep.mcp_server_id)
    adopted = []
    if req.groups:
        await svc._init_group_states(session, server, dep)
        for gs in (
            await session.execute(select(McpDeploymentGroupState).where(McpDeploymentGroupState.deployment_id == dep.id))
        ).scalars().all():
            if gs.observed_enabled is not None:
                gs.approved_enabled = gs.observed_enabled
        adopted.append("그룹")
    if req.scopes:
        for old in await _scope_rows(session, dep.id, "approved"):
            await session.delete(old)
        for s in await _scope_rows(session, dep.id, "observed"):
            session.add(McpResourceScope(
                deployment_id=dep.id, layer="approved", source="adopted", resource_type=s.resource_type,
                action=s.action, effect=s.effect, patterns=list(s.patterns or []), match=s.match,
                config_key=s.config_key, description=s.description,
            ))
        adopted.append("리소스 범위")
    if req.limits:
        dep.approved_limits = dict(dep.limits) if dep.limits else None
        adopted.append("limits")
    if req.version and dep.deployed_version:
        dep.approved_version = dep.deployed_version
        adopted.append("버전")
    await session.flush()
    await _bump_policy(session, dep, req.note or f"관측값 채택: {', '.join(adopted) or '없음'}", by)
    await evaluate_findings(session, dep)
    await session.commit()
    await session.refresh(dep)
    logger.info("MCP 관측값 채택: %s (%s) by=%s", name, adopted, by)
    return await build_policy(session, dep)


async def list_policy_history(session: AsyncSession, name: str) -> list[PolicyHistoryResponse] | None:
    """승인 정책 저장 이력(최신순 100건). 배포가 없으면 None."""
    dep = await _get_deployment(session, name)
    if not dep:
        return None
    rows = (
        await session.execute(
            select(McpPolicyHistory).where(McpPolicyHistory.deployment_id == dep.id)
            .order_by(McpPolicyHistory.id.desc()).limit(100)
        )
    ).scalars().all()
    return [PolicyHistoryResponse.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# finding 계산
# ---------------------------------------------------------------------------


def _covered(pattern: str, approved: list[str]) -> bool:
    """관측 패턴 ``pattern`` 이 승인 패턴 목록 중 하나에 포함되는지 (glob 기준)."""
    for q in approved:
        if pattern == q or q == "*":
            return True
        # 관측 패턴이 리터럴이거나 승인 glob 의 부분 집합이면 포함
        if fnmatch.fnmatchcase(pattern, q):
            return True
    return False


def _action_covers(rule_action: str, target: str) -> bool:
    """규칙 action 이 대상 action 을 포함하는지 — any 는 전부, write 는 delete 도 포함."""
    return rule_action == "any" or rule_action == target or (target == "delete" and rule_action == "write")


def _scope_findings(observed: list[McpResourceScope], approved: list[McpResourceScope]) -> list[dict]:
    """관측 범위가 승인 범위보다 느슨한 곳을 찾는다(승인 범위가 없으면 비교하지 않음).

    1) 승인된 deny/protect 패턴이 서버의 같은 유형 deny/protect 규칙에 없음
    2) 서버 allow 목록이 없거나, 승인 allow 패턴으로 덮이지 않는 패턴이 있음
    반환: ``[{subject, title, detail}]`` — 호출 쪽에서 ``scope_wider_than_approved`` 로 등록.
    """
    out: list[dict] = []
    if not approved:
        return out
    # 1) 승인된 deny/protect 가 서버 설정에 없음
    for a in approved:
        if a.effect not in ("deny", "protect"):
            continue
        strong = [o for o in observed
                  if o.resource_type == a.resource_type and o.effect in ("deny", "protect")
                  and (a.effect == "deny" or o.effect == "protect")
                  and _action_covers(o.action, a.action if a.action != "any" else "any")]
        have = {p for o in strong for p in (o.patterns or [])}
        missing = [p for p in (a.patterns or []) if p not in have and not _covered(p, list(have))]
        if missing:
            out.append({
                "subject": f"{a.resource_type}:{a.action}:{a.effect}",
                "title": f"승인된 {a.effect} 규칙이 서버 설정에 없음 — {a.resource_type} ({a.action})",
                "detail": {"resource_type": a.resource_type, "action": a.action, "effect": a.effect,
                           "missing_patterns": missing},
            })
    # 2) 서버의 allow 목록이 승인 allow 목록보다 넓음 (또는 서버에 allow 목록이 아예 없음)
    approved_allow: dict[str, list[str]] = defaultdict(list)
    for a in approved:
        if a.effect == "allow":
            approved_allow[a.resource_type].extend(a.patterns or [])
    for rt, a_patterns in approved_allow.items():
        o_allow = [o for o in observed if o.resource_type == rt and o.effect == "allow"]
        if not o_allow:
            out.append({
                "subject": f"{rt}:allow:missing",
                "title": f"서버에 {rt} 허용 목록이 없음 — 승인 허용 목록보다 넓게 동작",
                "detail": {"resource_type": rt, "approved_patterns": a_patterns},
            })
            continue
        wider = sorted({p for o in o_allow for p in (o.patterns or []) if not _covered(p, a_patterns)})
        if wider:
            out.append({
                "subject": f"{rt}:allow:wider",
                "title": f"서버 {rt} 허용 목록이 승인보다 넓음",
                "detail": {"resource_type": rt, "extra_patterns": wider, "approved_patterns": a_patterns},
            })
    return out


async def compute_findings(session: AsyncSession, dep: McpDeployment) -> dict[str, dict]:
    """현재 상태 기준 finding 목록 {fingerprint: {kind, subject, severity, title, detail}}.

    retired 배포는 빈 목록. fingerprint 는 ``<kind>:<subject>`` (300자 제한).
    """
    from app.mcp_servers import service as svc

    found: dict[str, dict] = {}

    def add(kind: str, subject: str, severity: str, title: str, detail: dict | None = None) -> None:
        fp = f"{kind}:{subject}"[:300]
        found[fp] = {"kind": kind, "subject": subject[:500], "severity": _sev(severity),
                     "title": title[:500], "detail": detail}

    if dep.status == "retired":
        return found

    eff_tools, eff_groups = await svc._effective_tools(session, dep)
    group_risk = {g.key: g.risk for g in eff_groups}
    # 감사 이벤트는 도구 이름으로 오므로 도구 → 그룹 key 로 바꿔 그룹 위험도를 찾는다
    tool_group = {t.name: t.group_key for t in eff_tools}
    for g in eff_groups:
        if g.observed_enabled is True and g.approved_enabled is False:
            add("group_enabled_unapproved", g.key, g.risk,
                f"승인되지 않은 도구 그룹 '{g.label or g.key}' 이(가) 켜져 있음",
                {"group": g.key, "config_key": g.config_key, "exposed_tools": g.exposed_count,
                 "basis": g.observed_basis})

    confirmed = dict(
        (await session.execute(
            select(McpServerTool.id, McpServerTool.risk_confirmed).where(McpServerTool.mcp_server_id == dep.mcp_server_id)
        )).all()
    )
    unreviewed = []
    for t in eff_tools:
        if t.exposed and t.blocked:
            add("tool_exposed_blocked", t.name, t.risk or "high",
                f"차단 승인된 도구 '{t.name}' 이(가) 서버에 노출됨",
                {"tool": t.name, "group": t.group_key, "risk": t.risk})
        if t.exposed and t.mutating and not confirmed.get(t.id):
            unreviewed.append(t.name)
    if unreviewed:
        add("new_tool_unreviewed", "*", "medium",
            f"위험도가 확정되지 않은 변경 도구 {len(unreviewed)}개가 노출됨",
            {"tools": sorted(unreviewed)[:200], "count": len(unreviewed)})

    observed = await _scope_rows(session, dep.id, "observed")
    approved = await _scope_rows(session, dep.id, "approved")
    for f in _scope_findings(observed, approved):
        add("scope_wider_than_approved", f["subject"], "high", f["title"], f["detail"])

    for key, limit in (dep.approved_limits or {}).items():
        cur = (dep.limits or {}).get(key)
        if isinstance(limit, (int, float)) and not isinstance(limit, bool) \
                and isinstance(cur, (int, float)) and not isinstance(cur, bool) and cur > limit:
            add("limit_exceeds_approved", key, "medium", f"제한값 {key} = {cur} (승인 {limit})",
                {"key": key, "observed": cur, "approved": limit})

    if dep.approved_version and dep.deployed_version and dep.approved_version != dep.deployed_version:
        add("version_mismatch", "version", "low",
            f"배포 버전 {dep.deployed_version} — 승인 버전 {dep.approved_version} 과 다름",
            {"deployed": dep.deployed_version, "approved": dep.approved_version})

    for w in dep.security_warnings or []:
        add("security_warning", normalize.stable_hash(w)[:16], "high", f"보안 경고: {w}", {"warning": w})

    if (dep.health_fail_count or 0) >= UNHEALTHY_THRESHOLD:
        add("unhealthy", "health", "medium", f"헬스 체크 {dep.health_fail_count}회 연속 실패",
            {"fail_count": dep.health_fail_count, "health_status": dep.health_status})

    since = _now() - AUDIT_VIOLATION_WINDOW
    for tool_name, decision, cnt, last_ts in (
        await session.execute(
            select(McpAuditEvent.tool_name, McpAuditEvent.policy_decision, func.count(McpAuditEvent.id),
                   func.max(McpAuditEvent.ts))
            .where(McpAuditEvent.deployment_id == dep.id, McpAuditEvent.ts >= since,
                   McpAuditEvent.policy_decision.in_(("deny", "blocked")),
                   # 서버가 카탈로그 정책으로 실제 거부한 호출은 위반이 아니라 집행 결과
                   or_(McpAuditEvent.server_decision.is_(None), McpAuditEvent.server_decision != "denied"))
            .group_by(McpAuditEvent.tool_name, McpAuditEvent.policy_decision)
        )
    ).all():
        sev = "critical" if decision == "blocked" else "high"
        what = "차단 도구 호출" if decision == "blocked" else "승인 범위 밖 리소스 접근"
        add("audit_policy_violation", f"{tool_name}:{decision}", sev,
            f"{what} — {tool_name} {cnt}건 (최근 7일)",
            {"tool": tool_name, "decision": decision, "count": cnt,
             "last_ts": last_ts.isoformat() if last_ts else None,
             "group_risk": group_risk.get(tool_group.get(tool_name))})

    if dep.policy_mode == "enforce":
        from app.mcp_servers import bundle as bundle_mod

        etag = bundle_mod.bundle_etag(await bundle_mod.build_bundle(session, dep))
        reason = bundle_mod.not_applied_reason(dep, etag)
        if reason:
            state, text = reason
            add("policy_not_applied", "bundle", "high", text,
                {"state": state, "policy_version": dep.policy_version,
                 "pulled_version": dep.policy_pulled_version,
                 "pulled_at": dep.policy_pulled_at.isoformat() if dep.policy_pulled_at else None,
                 "instance": dep.policy_pull_instance})
    return found


async def evaluate_findings(session: AsyncSession, dep: McpDeployment) -> list[McpDeploymentFinding]:
    """finding 을 계산해 DB 와 동기화하고, 새로 열린 finding 은 알림을 발행한다 (commit 은 호출자)."""
    current = await compute_findings(session, dep)
    rows = {
        f.fingerprint: f for f in (
            await session.execute(select(McpDeploymentFinding).where(McpDeploymentFinding.deployment_id == dep.id))
        ).scalars().all()
    }
    now = _now()
    opened: list[McpDeploymentFinding] = []
    for fp, f in current.items():
        row = rows.get(fp)
        if row is None:
            row = McpDeploymentFinding(deployment_id=dep.id, fingerprint=fp, status="open",
                                       first_seen_at=now, last_seen_at=now, **f)
            session.add(row)
            opened.append(row)
            continue
        if row.status == "resolved":
            row.status = "open"
            row.resolved_at = None
            row.acknowledged_by = row.acknowledged_at = row.ack_note = None
            opened.append(row)
        row.severity, row.title, row.detail, row.subject = f["severity"], f["title"], f["detail"], f["subject"]
        row.last_seen_at = now
    resolved = 0
    for fp, row in rows.items():
        if fp not in current and row.status != "resolved":
            row.status = "resolved"
            row.resolved_at = now
            resolved += 1
    await session.flush()
    logger.debug("MCP finding 평가: deployment=%s current=%d opened=%d resolved=%d",
                 dep.name, len(current), len(opened), resolved)
    if resolved:
        logger.info("MCP finding 자동 해소: deployment=%s count=%d", dep.name, resolved)
    if opened:
        await _emit_alerts(session, dep, opened)
        logger.info("MCP finding 오픈: %s %s", dep.name, [f.fingerprint for f in opened])
    return opened


async def evaluate_server_deployments(session: AsyncSession, server_id: int) -> None:
    """정의 변경(도구 위험도 확정 등) 후 그 서버의 배포 전체 finding 재계산."""
    deps = (
        await session.execute(
            select(McpDeployment).where(McpDeployment.mcp_server_id == server_id, McpDeployment.status != "retired")
        )
    ).scalars().all()
    logger.debug("서버 배포 finding 재계산: server_id=%d deployments=%d", server_id, len(deps))
    for dep in deps:
        await evaluate_findings(session, dep)


async def _emit_alerts(session: AsyncSession, dep: McpDeployment, findings: list[McpDeploymentFinding]) -> None:
    """새로 열린 finding 마다 알림 규칙을 평가하고, 발행된 마지막 알림 id 를 finding 에 기록한다."""
    try:
        from app.alert.service import evaluate_entity_alerts
    except Exception:  # noqa: BLE001 - 알림 모듈 없는 배포 형태
        logger.debug("알림 모듈 없음 — MCP finding 알림 생략: deployment=%s", dep.name)
        return
    server_name = await session.scalar(select(McpServer.name).where(McpServer.id == dep.mcp_server_id))
    for f in findings:
        trigger = "MCP_SECURITY_WARNING" if f.kind in SECURITY_KINDS else "MCP_POLICY_DRIFT"
        alerts = await evaluate_entity_alerts(
            session, trigger_type=trigger, entity_type="mcp_deployment", entity_ref=dep.name,
            severity=f.severity, kind=f.kind, environment=dep.environment, datasource_id=dep.datasource_id,
            summary=f"[MCP {dep.environment}] {dep.name}: {f.title}"[:500],
            detail={"deployment": dep.name, "server": server_name, "environment": dep.environment,
                    "finding_id": f.id, "kind": f.kind, "severity": f.severity, "detail": f.detail},
            owners=[dep.owner_email] if dep.owner_email else [],
        )
        if alerts:
            f.alert_id = alerts[-1].id


# ---------------------------------------------------------------------------
# finding 조회·확인
# ---------------------------------------------------------------------------


async def list_findings(session: AsyncSession, *, deployment_name: str | None = None, status: str | None = None,
                        severity: str | None = None, kind: str | None = None, page: int = 1,
                        page_size: int = 50) -> PaginatedFindings | None:
    """finding 목록(최근 확인 순). ``status``: unresolved(해소 외 전부) / all / 그 밖은 정확히 일치.

    ``deployment_name`` 을 줬는데 배포가 없으면 None.
    """
    logger.debug("MCP finding 조회: deployment=%s status=%s severity=%s kind=%s page=%d",
                 deployment_name, status, severity, kind, page)
    q = (
        select(McpDeploymentFinding, McpDeployment.name, McpDeployment.environment, McpServer.name)
        .join(McpDeployment, McpDeployment.id == McpDeploymentFinding.deployment_id)
        .join(McpServer, McpServer.id == McpDeployment.mcp_server_id)
    )
    if deployment_name:
        dep = await _get_deployment(session, deployment_name)
        if not dep:
            return None
        q = q.where(McpDeploymentFinding.deployment_id == dep.id)
    if status == "unresolved":
        q = q.where(McpDeploymentFinding.status != "resolved")
    elif status and status != "all":
        q = q.where(McpDeploymentFinding.status == status)
    if severity:
        q = q.where(McpDeploymentFinding.severity == severity)
    if kind:
        q = q.where(McpDeploymentFinding.kind == kind)
    total = await session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (
        await session.execute(
            q.order_by(McpDeploymentFinding.last_seen_at.desc(), McpDeploymentFinding.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = []
    for f, dname, env, sname in rows:
        r = FindingResponse.model_validate(f)
        r.deployment_name, r.environment, r.server_name = dname, env, sname
        items.append(r)
    # 같은 심각도 안에서는 최신순 — 화면에서 심각도 정렬은 클라이언트가 한다
    return PaginatedFindings(items=items, total=total, page=page, page_size=page_size)


async def update_finding(session: AsyncSession, deployment_name: str, finding_id: int, req: FindingUpdate,
                         by: str | None = None) -> FindingResponse | None:
    """finding 상태 변경(확인 acknowledged / 다시 open) 후 커밋. 없거나 다른 배포의 finding 이면 None.

    Raises:
        ValueError: 이미 resolved 인 finding.
    """
    dep = await _get_deployment(session, deployment_name)
    f = await session.get(McpDeploymentFinding, finding_id) if dep else None
    if not f or f.deployment_id != dep.id:
        return None
    if f.status == "resolved":
        logger.warning("해소된 finding 상태 변경 거부: deployment=%s finding_id=%d", deployment_name, finding_id)
        raise ValueError("이미 해소된 finding 입니다")
    f.status = req.status
    if req.status == "acknowledged":
        f.acknowledged_by, f.acknowledged_at, f.ack_note = by, _now(), req.note
    else:
        f.acknowledged_by = f.acknowledged_at = f.ack_note = None
    await session.commit()
    await session.refresh(f)
    logger.info("MCP finding %s: %s #%d by=%s", req.status, deployment_name, finding_id, by)
    r = FindingResponse.model_validate(f)
    r.deployment_name, r.environment = dep.name, dep.environment
    return r


async def finding_counts(session: AsyncSession, dep_ids: list[int]) -> dict[int, tuple[int, str | None]]:
    """배포별 (미해소 finding 수, 최고 심각도)."""
    out: dict[int, tuple[int, str | None]] = {}
    if not dep_ids:
        return out
    acc: dict[int, list[str]] = defaultdict(list)
    for dep_id, sev, cnt in (
        await session.execute(
            select(McpDeploymentFinding.deployment_id, McpDeploymentFinding.severity, func.count())
            .where(McpDeploymentFinding.deployment_id.in_(dep_ids), McpDeploymentFinding.status != "resolved")
            .group_by(McpDeploymentFinding.deployment_id, McpDeploymentFinding.severity)
        )
    ).all():
        acc[dep_id].extend([sev] * cnt)
    for dep_id, sevs in acc.items():
        out[dep_id] = (len(sevs), max(sevs, key=lambda s: normalize.RISK_ORDER.get(s, 0)))
    return out


def evaluate_tool_call(*, tool_blocked: bool, scopes: list[ResourceScopeResponse], resources: list[dict[str, Any]],
                       action: str) -> tuple[str, str]:
    """감사 이벤트 한 건을 승인 정책으로 판정 → (decision, reason).

    차단 도구면 blocked, 리소스를 못 찾으면 unknown, 리소스 하나라도 deny 면 deny,
    모두 allow 면 allow, 그 밖은 unknown.
    """
    from app.mcp_servers.schemas import SimulateRequest
    from app.mcp_servers.service import evaluate_scopes

    if tool_blocked:
        return "blocked", "승인 정책에서 차단된 도구"
    if not resources:
        return "unknown", "인자에서 리소스를 찾지 못함"
    decisions = []
    for r in resources:
        res = evaluate_scopes(scopes, SimulateRequest(resource_type=r["type"], resource=str(r["value"]),
                                                      action=action))
        if res.decision == "deny":
            return "deny", f"{r['type']}={r['value']}: {res.reason}"
        decisions.append(res.decision)
    if decisions and all(d == "allow" for d in decisions):
        return "allow", "승인 범위 안"
    return "unknown", "승인 정책에 해당 리소스 규칙 없음"
