# SPDX-License-Identifier: Apache-2.0
"""변경 도구 호출 감사 이벤트 수집·조회 (설계 §5.5).

Argus Forge ``Audit`` JSONL 한 줄을 그대로 받는다::

    {"ts": "2026-10-21T11:15:30+0900", "session": "…", "tool": "create_topic", "user": "…",
     "args": {"topic": "orders"}, "ok": true, "id": "…"}

- 인증: 배포별 **수집 토큰** (``Authorization: Bearer mcpi_…`` 또는 ``X-Argus-Ingest-Token``) —
  서버 쪽 shipper 가 사용. 화면 업로드는 로그인 사용자(소유자·관리자).
- 토큰은 sha256 해시만 저장하고 원문은 발급 응답에서 한 번만 보여준다.
- 같은 줄은 해시로 중복 제거한다(shipper 재전송 안전).
- 도구 → 그룹·위험도를 붙이고, 도구의 ``resource_types`` 기준으로 ``args`` 에서 리소스 값을
  추출해 **승인 정책**(없으면 관측 범위)으로 판정한다. 위반은 finding(audit_policy_violation)이 된다.
- 보존 기간(배포별 ``audit_retention_days``, 기본 180일)이 지난 이벤트는 수집 시 정리한다.
- 인자 값은 서버가 이미 마스킹(``***``)해서 보낸다. 카탈로그도 비밀값 키를 한 번 더 가린다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql import LIKE_ESCAPE, like_contains
from app.mcp_servers import normalize
from app.mcp_servers.models import (
    McpAuditEvent,
    McpDeployment,
    McpDeploymentToolState,
    McpServerTool,
    McpServerToolGroup,
)
from app.mcp_servers.schemas import (
    AuditEventResponse,
    AuditIngestRequest,
    AuditIngestResult,
    IngestTokenResponse,
    PaginatedAuditEvents,
    ResourceScopeResponse,
)

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "mcpi_"  # 수집 토큰 접두어 — 다른 종류 토큰과 구분
MAX_EVENTS = 20_000  # 한 번의 수집 요청에서 받는 최대 이벤트 수
# 인자 키에 이 단어가 들어가면 값을 ``***`` 로 가린다(부분 일치·대소문자 무시)
SECRET_WORDS = ("password", "secret", "token", "authorization", "credential", "key")

# 리소스 유형별로 인자 키 후보 (일반 규칙: <type>, <type>_name, <type>_id, <type>s 도 함께 본다)
ARG_KEYS: dict[str, tuple[str, ...]] = {
    "topic": ("topic", "topic_name", "topics"),
    "consumer_group": ("group", "group_id", "consumer_group", "groups"),
    "znode_path": ("path", "znode", "paths", "node_path"),
    "bucket": ("bucket", "bucket_name", "buckets"),
    "dag": ("dag_id", "dag", "dag_ids"),
    "ranger_service": ("service", "service_name", "services"),
    "entity_type": ("type_name", "typeName", "entity_type", "type"),
    "table": ("table", "table_name", "tables", "full_table_name"),
    "database": ("database", "db", "database_name", "schema"),
    "process_group": ("process_group_id", "pg_id", "process_group"),
}


def _hash_token(token: str) -> str:
    """수집 토큰의 sha256 hex — DB 에는 이 값만 저장한다."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 수집 토큰
# ---------------------------------------------------------------------------


async def issue_token(session: AsyncSession, dep: McpDeployment, by: str | None = None) -> IngestTokenResponse:
    """배포의 수집 토큰을 새로 발급(기존 토큰은 덮어써 무효화)하고 커밋한다.

    DB 에는 해시와 앞뒤 일부만 보이는 힌트만 저장하며, 원문 토큰은 이 응답에서만 돌려준다.
    """
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    dep.ingest_token_hash = _hash_token(token)
    dep.ingest_token_hint = f"{token[:9]}…{token[-4:]}"
    dep.ingest_token_created_at = _now()
    dep.updated_by = by
    await session.commit()
    await session.refresh(dep)
    logger.info("MCP 감사 수집 토큰 발급: %s by=%s", dep.name, by)
    return IngestTokenResponse(token=token, hint=dep.ingest_token_hint, created_at=dep.ingest_token_created_at,
                               ingest_url=f"/api/v1/mcp-deployments/{dep.name}/audit-events")


async def revoke_token(session: AsyncSession, dep: McpDeployment) -> None:
    """배포의 수집 토큰(해시·힌트·발급 시각)을 지우고 커밋한다 — 이후 토큰 수집은 401."""
    dep.ingest_token_hash = dep.ingest_token_hint = dep.ingest_token_created_at = None
    await session.commit()
    logger.info("MCP 감사 수집 토큰 폐기: %s", dep.name)


def verify_token(dep: McpDeployment, token: str | None) -> bool:
    """제시된 토큰이 배포의 수집 토큰과 같은지 상수 시간 비교로 확인한다(토큰 미발급이면 False)."""
    if not token or not dep.ingest_token_hash or not token.startswith(TOKEN_PREFIX):
        return False
    return secrets.compare_digest(_hash_token(token), dep.ingest_token_hash)


# ---------------------------------------------------------------------------
# 파싱·리소스 추출
# ---------------------------------------------------------------------------


def _mask(value: Any, depth: int = 0) -> Any:
    """비밀값 키를 ``***`` 로 가리고 크기를 제한한 사본을 만든다.

    깊이 6 초과는 ``…``, dict·list 는 앞 60개, 1000자 넘는 문자열은 잘라서 저장 크기를 묶는다.
    """
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {
            k: ("***" if any(w in str(k).lower() for w in SECRET_WORDS) else _mask(v, depth + 1))
            for k, v in list(value.items())[:60]
        }
    if isinstance(value, list):
        return [_mask(v, depth + 1) for v in value[:60]]
    if isinstance(value, str) and len(value) > 1000:
        return value[:997] + "..."
    return value


def _parse_ts(value: Any) -> datetime | None:
    """이벤트 시각 해석 — epoch 숫자·ISO 8601(``Z``·``+0900`` 포함)을 받고, 시간대 없으면 UTC 로 본다.

    해석할 수 없으면 None(호출 쪽에서 invalid 로 센다).
    """
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    # Forge 형식 +0900 → +09:00
    if len(v) >= 5 and v[-5] in "+-" and v[-4:].isdigit() and ":" not in v[-5:]:
        v = v[:-2] + ":" + v[-2:]
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _as_values(v: Any) -> list[str]:
    """인자 값을 문자열 목록으로 — 스칼라는 1개, 리스트는 앞 50개. 빈 값·마스킹 값(``***``)은 뺀다."""
    if isinstance(v, (str, int)) and str(v) and str(v) != "***":
        return [str(v)]
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, (str, int)) and str(x) and str(x) != "***"][:50]
    return []


def extract_resources(args: dict | None, resource_types: list[str] | None) -> list[dict]:
    """도구 인자에서 ``resource_types`` 에 해당하는 값을 꺼낸다.

    유형의 마지막 마디(``kafka.topic`` → ``topic``)로 ``ARG_KEYS`` 후보와 일반 규칙 키를 순서대로 보고,
    처음 값이 나온 키를 쓴다. ``table`` 은 같은 인자에 database 가 있으면 ``db.table`` 로 합친다.
    반환: ``[{"type": 리소스 유형, "value": 값}, ...]``.
    """
    if not isinstance(args, dict) or not resource_types:
        return []
    out: list[dict] = []
    lower = {str(k).lower(): v for k, v in args.items()}
    for rt in resource_types:
        tail = rt.split(".")[-1].lower()
        keys = ARG_KEYS.get(tail, ()) + (tail, f"{tail}_name", f"{tail}_id", f"{tail}s")
        values: list[str] = []
        for k in keys:
            if k in lower:
                values = _as_values(lower[k])
                if values:
                    break
        if tail == "table" and values:
            db = next((lower[k] for k in ARG_KEYS["database"] if isinstance(lower.get(k), str)), None)
            if db:
                values = [v if "." in v else f"{db}.{v}" for v in values]
        for v in values:
            out.append({"type": rt, "value": v})
    return out


def _action_of(group_key: str | None, tool_name: str, mutating: bool) -> str:
    """정책 판정용 동작 — 비변경 도구는 read, destructive 그룹·삭제류 이름은 delete, 나머지는 write."""
    if not mutating:
        return "read"
    name = tool_name.lower()
    if group_key == "destructive" or name.startswith(("delete", "drop", "purge", "remove", "truncate")):
        return "delete"
    return "write"


def _parse_lines(req: AuditIngestRequest) -> tuple[list[dict], int]:
    """요청의 ``events`` 배열과 ``jsonl`` 텍스트를 합쳐 이벤트 dict 목록과 무효 건수를 돌려준다."""
    events: list[dict] = []
    invalid = 0
    for e in req.events or []:
        if isinstance(e, dict):
            events.append(e)
        else:
            invalid += 1
    for line in (req.jsonl or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            invalid += 1
            continue
        if isinstance(obj, dict):
            events.append(obj)
        else:
            invalid += 1
    return events, invalid


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------


async def ingest(session: AsyncSession, dep: McpDeployment, req: AuditIngestRequest, via: str) -> AuditIngestResult:
    """감사 이벤트를 파싱·중복 제거·정책 판정해 저장하고, 보존 기간이 지난 이벤트를 정리한 뒤 커밋한다.

    - ``via``: 수집 경로 표시(토큰 수집·화면 업로드 등) — 이벤트의 ``received_via`` 에 저장.
    - 판정 기준은 승인 범위, 승인 범위가 없으면 관측 범위.
    - 도구·시각이 없거나 보존 기간 밖인 줄은 invalid 로 세고 건너뛴다.
    - 위반(deny/blocked, 서버 enforce 거부 제외)이 있으면 finding 을 다시 평가한다.

    Raises:
        ValueError: 이벤트가 ``MAX_EVENTS`` 를 넘을 때.
    """
    from app.mcp_servers.policy import evaluate_findings, evaluate_tool_call

    raw, invalid = _parse_lines(req)
    logger.debug("MCP 감사 이벤트 파싱: deployment=%s via=%s parsed=%d invalid=%d", dep.name, via, len(raw), invalid)
    if len(raw) > MAX_EVENTS:
        logger.warning("MCP 감사 이벤트 수집 거부(건수 초과): deployment=%s received=%d max=%d",
                       dep.name, len(raw), MAX_EVENTS)
        raise ValueError(f"한 번에 최대 {MAX_EVENTS}건까지 받을 수 있습니다 (받은 {len(raw)}건)")

    groups = dict((await session.execute(
        select(McpServerToolGroup.id, McpServerToolGroup.key).where(McpServerToolGroup.mcp_server_id == dep.mcp_server_id)
    )).all())
    tools = {
        t.name: t for t in (
            await session.execute(select(McpServerTool).where(McpServerTool.mcp_server_id == dep.mcp_server_id))
        ).scalars().all()
    }
    blocked = {
        tid for (tid,) in (
            await session.execute(
                select(McpDeploymentToolState.tool_id).where(McpDeploymentToolState.deployment_id == dep.id,
                                                             McpDeploymentToolState.approved_blocked.is_(True))
            )
        ).all()
    }
    from app.mcp_servers.policy import _scope_rows

    approved = [ResourceScopeResponse.model_validate(s) for s in await _scope_rows(session, dep.id, "approved")]
    scopes = approved or [ResourceScopeResponse.model_validate(s) for s in await _scope_rows(session, dep.id, "observed")]
    retention_from = _now() - timedelta(days=dep.audit_retention_days or 180)
    logger.debug("MCP 감사 판정 기준: deployment=%s scope_layer=%s scopes=%d tools=%d blocked=%d",
                 dep.name, "approved" if approved else "observed", len(scopes), len(tools), len(blocked))

    prepared: list[tuple[str, dict]] = []
    for e in raw:
        tool_name = e.get("tool") or e.get("tool_name")
        ts = _parse_ts(e.get("ts") or e.get("timestamp"))
        if not tool_name or ts is None:
            invalid += 1
            continue
        if ts < retention_from:
            invalid += 1
            continue
        h = normalize.stable_hash(e)
        prepared.append((h, {**e, "_tool": str(tool_name)[:200], "_ts": ts}))
    hashes = [h for h, _ in prepared]
    existing: set[str] = set()
    for i in range(0, len(hashes), 500):
        chunk = hashes[i:i + 500]
        existing |= set((await session.execute(
            select(McpAuditEvent.event_hash).where(McpAuditEvent.deployment_id == dep.id,
                                                   McpAuditEvent.event_hash.in_(chunk))
        )).scalars().all())

    inserted = duplicates = violations = 0
    seen: set[str] = set()
    for h, e in prepared:
        if h in existing or h in seen:
            duplicates += 1
            continue
        seen.add(h)
        tool = tools.get(e["_tool"])
        group_key = groups.get(tool.group_id) if tool else None
        args = _mask(e.get("args")) if isinstance(e.get("args"), dict) else None
        resources = extract_resources(args, tool.resource_types if tool else None)
        decision, reason = evaluate_tool_call(
            tool_blocked=bool(tool and tool.id in blocked), scopes=scopes, resources=resources,
            action=_action_of(group_key, e["_tool"], tool.mutating if tool else True),
        )
        if not tool:
            reason = "카탈로그에 없는 도구 — " + reason
        # Forge enforce 모드: 서버가 카탈로그 정책으로 거부한 호출 ({"decision": "denied", "reason": …})
        server_decision = "denied" if str(e.get("decision") or "").lower() == "denied" else None
        if server_decision:
            reason = f"서버가 거부(enforce): {e.get('reason') or '카탈로그 정책'} · 카탈로그 판정: {reason}"
        elif decision in ("deny", "blocked"):
            violations += 1
        agent = e.get("agent")
        session.add(McpAuditEvent(
            deployment_id=dep.id, ts=e["_ts"], session_id=str(e.get("session") or "")[:200] or None,
            tool_name=e["_tool"], tool_id=tool.id if tool else None, group_key=group_key,
            risk=tool.risk if tool else None, user_name=str(e.get("user") or "")[:200] or None, args=args,
            ok=bool(e.get("ok", True)), error=str(e["error"])[:500] if e.get("error") else None,
            result_id=str(e["id"])[:300] if e.get("id") is not None else None, resources=resources or None,
            policy_decision=decision, policy_reason=reason[:500], event_hash=h, received_via=via,
            agent_name=str(agent)[:300] if isinstance(agent, str) and agent else None, server_decision=server_decision,
        ))
        inserted += 1
    purged = (await session.execute(
        delete(McpAuditEvent).where(McpAuditEvent.deployment_id == dep.id, McpAuditEvent.ts < retention_from)
    )).rowcount or 0
    await session.flush()
    if violations:
        await evaluate_findings(session, dep)
    await session.commit()
    logger.info("MCP 감사 이벤트 수집: %s via=%s inserted=%d dup=%d invalid=%d violations=%d purged=%d",
                dep.name, via, inserted, duplicates, invalid, violations, purged)
    if invalid:
        logger.warning("MCP 감사 이벤트 일부 무시(형식 오류·시각 누락·보존 기간 밖): deployment=%s invalid=%d",
                       dep.name, invalid)
    return AuditIngestResult(received=len(raw), inserted=inserted, duplicates=duplicates, invalid=invalid,
                             violations=violations, purged=purged)


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------


async def list_events(session: AsyncSession, dep: McpDeployment | None, *, tool: str | None = None,
                      user: str | None = None, ok: bool | None = None, decision: str | None = None,
                      resource: str | None = None, since: datetime | None = None, page: int = 1,
                      page_size: int = 50) -> PaginatedAuditEvents:
    """감사 이벤트 목록(최신순) — ``dep`` 가 None 이면 전체 배포.

    ``decision``: ``violation``(위반이면서 서버 거부가 아닌 것) / ``enforced``(서버가 거부) / 그 밖의 값은
    ``policy_decision`` 일치. ``resource`` 는 사유·결과 id·리소스 JSON 텍스트 부분 일치로 근사한다.
    """
    logger.debug("MCP 감사 이벤트 조회: deployment=%s tool=%s decision=%s page=%d size=%d",
                 dep.name if dep is not None else None, tool, decision, page, page_size)
    q = select(McpAuditEvent, McpDeployment.name).join(McpDeployment, McpDeployment.id == McpAuditEvent.deployment_id)
    if dep is not None:
        q = q.where(McpAuditEvent.deployment_id == dep.id)
    if tool:
        q = q.where(McpAuditEvent.tool_name.ilike(like_contains(tool), escape=LIKE_ESCAPE))
    if user:
        q = q.where(McpAuditEvent.user_name.ilike(like_contains(user), escape=LIKE_ESCAPE))
    if ok is not None:
        q = q.where(McpAuditEvent.ok.is_(ok))
    if decision == "violation":
        q = q.where(McpAuditEvent.policy_decision.in_(("deny", "blocked")),
                    or_(McpAuditEvent.server_decision.is_(None), McpAuditEvent.server_decision != "denied"))
    elif decision == "enforced":
        q = q.where(McpAuditEvent.server_decision == "denied")
    elif decision:
        q = q.where(McpAuditEvent.policy_decision == decision)
    if since:
        q = q.where(McpAuditEvent.ts >= since)
    if resource:
        # JSON 컬럼 텍스트 검색은 DB 마다 달라 인자·리소스를 문자열 비교로 근사한다
        like = like_contains(resource)
        q = q.where(or_(McpAuditEvent.policy_reason.ilike(like, escape=LIKE_ESCAPE), McpAuditEvent.result_id.ilike(like, escape=LIKE_ESCAPE),
                        func.cast(McpAuditEvent.resources, _text_type()).ilike(like, escape=LIKE_ESCAPE)))
    total = await session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (
        await session.execute(
            q.order_by(McpAuditEvent.ts.desc(), McpAuditEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = []
    for ev, dname in rows:
        r = AuditEventResponse.model_validate(ev)
        r.deployment_name = dname
        items.append(r)
    return PaginatedAuditEvents(items=items, total=total, page=page, page_size=page_size)


def _text_type():
    """JSON 컬럼을 문자열로 CAST 할 때 쓰는 SQLAlchemy Text 타입(지연 import)."""
    from sqlalchemy import Text

    return Text
