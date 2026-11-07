# SPDX-License-Identifier: Apache-2.0
"""정책 번들 (설계 §5.6, Phase 3 — L3 제어).

MCP 서버(Forge ``argus_mcp_common.catalog_policy``)가 주기적으로 pull 해 도구 호출 직전에 집행하는 문서다.
원본은 배포의 승인 정책(``policy.policy_snapshot``)과 이 배포에 연결된 에이전트별 허용 범위
(``catalog_ai_agent_mcp_servers.allowed_groups / allowed_tools`` + 에이전트 리소스 범위)다.

- ``policy_mode`` 가 ``observe`` 면 서버는 번들을 받아 보고만 하고 집행하지 않는다.
- ``status=blocked`` (배포 상태 blocked/retired) 면 서버는 변경 도구를 모두 거부한다.
- 서버는 로컬 설정과 합칠 때 **더 좁은 쪽**을 택한다 — 번들로 꺼진 그룹을 켤 수는 없다.
- ETag 는 번들 본문 해시다. ``policy_version`` 은 승인 정책 저장 때만 바뀌므로 에이전트 연결이 바뀌어도
  서버가 새 번들을 받도록 본문 전체로 계산한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_servers.models import McpDeployment, McpResourceScope

logger = logging.getLogger(__name__)

BUNDLE_VERSION = 1  # 번들 문서 형식 버전(서버 쪽 파서와 맞춘다)
# enforce 배포가 이 시간 넘게 번들을 받아가지 않으면 policy_not_applied finding
PULL_STALE_AFTER = timedelta(minutes=30)
# enforce 로 바꾼 직후·정책 변경 직후 서버가 새 번들을 받아갈 여유 (서버 기본 refresh 60초)
PULL_GRACE = timedelta(minutes=10)
# 이 배포 상태면 번들 status 를 blocked 로 내보내 서버가 변경 도구를 모두 거부하게 한다
BLOCKED_STATUSES = {"blocked", "retired"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scope_dict(s: McpResourceScope) -> dict:
    """리소스 범위 행 → 번들용 dict(match 미지정이면 fnmatch)."""
    return {"resource_type": s.resource_type, "action": s.action, "effect": s.effect,
            "patterns": list(s.patterns or []), "match": s.match or "fnmatch"}


async def _agent_entries(session: AsyncSession, dep: McpDeployment) -> dict[str, dict]:
    """이 배포에 연결된 에이전트별 허용 범위 — {에이전트 이름: {urn, status, allowed_groups, allowed_tools, resource_scopes}}.

    ``allowed_groups``/``allowed_tools`` 가 None 이면 제한 없음(배포 정책을 그대로 따름)을 뜻한다.
    """
    from app.agents.models import AIAgent, AIAgentMcpServer

    rows = (
        await session.execute(
            select(AIAgentMcpServer, AIAgent.name, AIAgent.urn, AIAgent.status)
            .join(AIAgent, AIAgent.id == AIAgentMcpServer.agent_id)
            .where(AIAgentMcpServer.mcp_deployment_id == dep.id)
            .order_by(AIAgent.name, AIAgentMcpServer.id)
        )
    ).all()
    if not rows:
        return {}
    link_ids = [link.id for link, *_ in rows]
    scopes: dict[int, list[dict]] = {}
    for s in (
        await session.execute(
            select(McpResourceScope).where(McpResourceScope.agent_link_id.in_(link_ids))
            .order_by(McpResourceScope.resource_type, McpResourceScope.id)
        )
    ).scalars().all():
        scopes.setdefault(s.agent_link_id, []).append(_scope_dict(s))
    out: dict[str, dict] = {}
    for link, name, urn, status in rows:
        # 같은 에이전트가 한 배포를 두 번 연결했으면 첫 연결을 쓴다(화면에서 중복 연결을 막지만 방어).
        if name in out:
            continue
        out[name] = {
            "urn": urn,
            "status": status,
            "allowed_groups": list(link.allowed_groups) if link.allowed_groups is not None else None,
            "allowed_tools": list(link.allowed_tools) if link.allowed_tools is not None else None,
            "resource_scopes": scopes.get(link.id, []),
        }
    return out


async def build_bundle(session: AsyncSession, dep: McpDeployment) -> dict:
    """배포의 정책 번들 본문을 만든다(승인 정책 스냅숏 + 에이전트별 허용 범위). DB 는 읽기만 한다."""
    from app.mcp_servers.policy import policy_snapshot

    snap = await policy_snapshot(session, dep)
    logger.debug("정책 번들 생성: deployment=%s policy_version=%s mode=%s status=%s",
                 dep.name, dep.policy_version, dep.policy_mode, dep.status)
    return {
        "bundle_version": BUNDLE_VERSION,
        "deployment": dep.name,
        "policy_version": dep.policy_version,
        "policy_mode": dep.policy_mode,
        "status": "blocked" if dep.status in BLOCKED_STATUSES else "active",
        "groups": snap["groups"],
        "blocked_tools": snap["blocked_tools"],
        "approval_required_tools": snap["approval_required_tools"],
        "resource_scopes": snap["resource_scopes"],
        "limits": snap["limits"],
        "approved_version": snap["approved_version"],
        "agents": await _agent_entries(session, dep),
    }


def bundle_etag(bundle: dict) -> str:
    """번들 본문(키 정렬 JSON)의 sha256 앞 32자를 따옴표로 감싼 강한 ETag."""
    raw = json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return '"' + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32] + '"'


def etag_matches(header: str | None, etag: str) -> bool:
    """``If-None-Match`` 헤더가 ETag 와 맞는지 — ``*``, 쉼표 목록, 약한 ETag(``W/``)·따옴표 유무를 모두 허용."""
    if not header:
        return False
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or etag in tags or etag.strip('"') in (t.strip('"').removeprefix("W/").strip('"') for t in tags)


def record_pull(dep: McpDeployment, *, etag: str, instance_id: str | None) -> None:
    """서버가 번들을 받아간 기록 (commit 은 호출자). 버전 표시는 policy_version + ETag 앞 8자."""
    dep.policy_pulled_at = _now()
    dep.policy_pulled_version = current_pull_tag(dep, etag)
    if instance_id:
        dep.policy_pull_instance = instance_id[:200]
    logger.debug("정책 번들 pull 기록: deployment=%s version=%s instance=%s",
                 dep.name, dep.policy_pulled_version, instance_id)


def current_pull_tag(dep: McpDeployment, etag: str) -> str:
    """화면 표시·적용 여부 비교용 버전 태그 ``<policy_version>@<ETag 앞 8자>`` (최대 100자)."""
    return f"{dep.policy_version or '-'}@{etag.strip(chr(34))[:8]}"[:100]


def _aware(value: datetime | None) -> datetime | None:
    """시간대 없는 datetime(SQLite 등)을 UTC 로 간주해 aware 로 맞춘다."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def pull_state(dep: McpDeployment, etag: str | None) -> str:
    """배포의 번들 적용 상태: never(받아간 적 없음) / stale(오래 안 받아감) / outdated(옛 번들) / applied."""
    pulled = _aware(dep.policy_pulled_at)
    if pulled is None:
        return "never"
    if _now() - pulled > PULL_STALE_AFTER:
        return "stale"
    if etag is not None and dep.policy_pulled_version != current_pull_tag(dep, etag):
        return "outdated"
    return "applied"


def not_applied_reason(dep: McpDeployment, etag: str) -> tuple[str, str] | None:
    """enforce 배포가 정책을 적용하지 못하고 있으면 (상태, 설명). 유예 시간 안이면 None."""
    if dep.policy_mode != "enforce" or dep.status == "retired":
        return None
    state = pull_state(dep, etag)
    now = _now()
    if state == "never":
        since = _aware(dep.policy_updated_at) or _aware(dep.updated_at)
        if since is not None and now - since < PULL_GRACE:
            return None
        return state, "enforce 모드지만 서버가 정책 번들을 한 번도 받아가지 않음 — 서버의 catalog_policy 설정 확인"
    if state == "stale":
        return state, f"서버가 {int(PULL_STALE_AFTER.total_seconds() // 60)}분 넘게 정책 번들을 받아가지 않음 — 마지막 번들로 동작 중"
    if state == "outdated" and now - _aware(dep.policy_pulled_at) > PULL_GRACE:
        return state, "서버가 이전 정책 번들로 동작 중"
    return None
