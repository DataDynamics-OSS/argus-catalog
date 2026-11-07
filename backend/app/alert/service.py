# SPDX-License-Identifier: Apache-2.0
"""리니지 변경 알림 서비스 레이어(Alert Rule Engine 포함).

핵심 비즈니스 로직:
  1. Rule 평가     : 스키마 변경 발생 시 활성 Rule 을 순회하며 scope/trigger 매칭
  2. 영향 분석     : 변경 컬럼과 리니지 컬럼 매핑을 교차 확인해 영향 범위 산정
  3. 알림 생성     : 매칭된 Rule 에 따라 ``LineageAlert`` 레코드 생성
  4. 알림 전달     : 구독자 + 데이터셋 Owner 에게 IN_APP 기록, WEBHOOK 발송(결과 기록)

안전 정책:
  - 규칙 하나의 설정(trigger_config)이 깨져 있어도 그 규칙만 건너뛰고 나머지는 평가한다.
  - 요약 문자열은 컬럼 길이(500)에 맞춰 자른다 — flush 실패로 호출자(스키마 동기화·품질
    검증)의 트랜잭션이 깨지지 않도록. 호출자는 savepoint 안에서 평가를 부른다.
  - 지원 채널은 IN_APP / WEBHOOK. EMAIL 은 발송기가 없어 스키마 단계에서 거부한다.

로깅 정책:
  - Rule 생성/수정/삭제는 서비스(이름·변경 필드)와 라우터(요청자) 양쪽에서 INFO 로 기록한다.
  - 알림 상태 변경·오래된 알림 정리는 본 모듈에서 INFO 로 기록한다.
  - 알림 생성·발송은 본 모듈에서 INFO 로 기록한다.
  - Webhook 호출 실패, 깨진 규칙 설정 등은 WARNING 으로 표면화한다.
"""

import json as _json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alert.models import AlertNotification, AlertRule, LineageAlert
from app.alert.schemas import (
    ENTITY_TRIGGERS,
    SUPPORTED_CHANNELS,
    AlertBulkResult,
    AlertResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    AlertSummary,
    PaginatedAlerts,
    normalize_trigger_config,
)
from app.catalog.models import (
    Dataset,
    DatasetColumnMapping,
    DatasetLineage,
    DatasetTag,
    Datasource,
    Owner,
    Tag,
)

logger = logging.getLogger(__name__)

# 심각도 순위(높을수록 심각) — 모듈 전체에서 이 하나만 쓴다.
_SEVERITY_RANK = {"INFO": 1, "WARNING": 2, "BREAKING": 3}
_SEVERITY_LABEL = {"BREAKING": "심각", "WARNING": "경고", "INFO": "정보"}
_CHANGE_LABEL = {"DROP": "삭제", "MODIFY": "변경", "ADD": "추가"}

_SUMMARY_MAX = 500          # LineageAlert.change_summary 컬럼 길이
_RECIPIENT_MAX = 200        # AlertNotification.recipient 컬럼 길이

# Webhook — 스키마 동기화 경로에서 인라인 호출되므로 짧게 끊는다.
_WEBHOOK_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
# 테스트에서 httpx.MockTransport 를 끼우는 지점
_webhook_transport: httpx.AsyncBaseTransport | None = None


class RuleValidationError(ValueError):
    """규칙 조합 검증 실패(범위 대상 없음, 범위·트리거 불일치, Webhook URL 누락 등) → 400."""


class AlertTransitionError(ValueError):
    """허용되지 않는 상태 전이 → 409."""


def _clip(text: str, limit: int = _SUMMARY_MAX) -> str:
    """컬럼 길이에 맞춰 자른다(말줄임표 포함)."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _load_config(rule: AlertRule) -> dict | None:
    """규칙 trigger_config 를 안전하게 읽는다. 깨져 있으면 경고 후 None(그 규칙은 건너뜀)."""
    raw = rule.trigger_config
    if not raw:
        return {}
    try:
        cfg = _json.loads(raw)
    except ValueError:
        logger.warning("알림 규칙 설정이 올바른 JSON 이 아니어서 건너뜀: rule_id=%s", rule.id)
        return None
    if not isinstance(cfg, dict):
        logger.warning("알림 규칙 설정이 JSON 객체가 아니어서 건너뜀: rule_id=%s", rule.id)
        return None
    return cfg


def _str_set(value, default: list[str]) -> set[str]:
    """설정의 목록 값을 문자열 집합으로(형태가 이상하면 기본값)."""
    if isinstance(value, list):
        return {str(v) for v in value}
    return set(default)


# ---------------------------------------------------------------------------
# 규칙 엔진 — 스키마 변경 시 활성 규칙 평가
# ---------------------------------------------------------------------------

async def evaluate_rules_and_create_alerts(
    session: AsyncSession,
    dataset_id: int,
    changes: list[dict],
) -> list[LineageAlert]:
    """스키마 변경에 대해 활성 규칙을 평가하고 알림을 생성한다.

    ``save_schema_snapshot()`` 이 변경 사항을 감지했을 때 호출된다.

    처리 흐름:
        1. 활성 규칙 전부 조회
        2. 규칙마다 ``scope`` 매칭 → ``trigger`` 평가 → 매칭되면 알림 생성
           (규칙 하나의 평가 오류는 그 규칙만 건너뛴다)
        3. 생성된 알림을 구독자/소유자에게 전달 (IN_APP / WEBHOOK)
    """
    if not changes:
        return []

    rules = (await session.execute(
        select(AlertRule).where(
            AlertRule.is_active == "true",
            AlertRule.trigger_type.in_(("ANY", "SCHEMA_CHANGE", "COLUMN_WATCH", "MAPPING_BROKEN")),
        )
    )).scalars().all()
    logger.debug("스키마 변경 규칙 평가: dataset_id=%d changes=%d active_rules=%d",
                 dataset_id, len(changes), len(rules))
    if not rules:
        return []

    dataset = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if not dataset:
        return []

    tag_ids = set((await session.execute(
        select(DatasetTag.tag_id).where(DatasetTag.dataset_id == dataset_id)
    )).scalars().all())

    lineages = (await session.execute(
        select(DatasetLineage).where(
            or_(
                DatasetLineage.source_dataset_id == dataset_id,
                DatasetLineage.target_dataset_id == dataset_id,
            )
        )
    )).scalars().all()
    lineage_ids = {lin.id for lin in lineages}

    change_map = {c["field"]: c for c in changes if isinstance(c, dict) and "field" in c}
    created_alerts: list[LineageAlert] = []

    for rule in rules:
        if not _match_scope(rule, dataset_id, dataset.datasource_id, tag_ids, lineage_ids):
            continue
        config = _load_config(rule)
        if config is None:
            continue
        try:
            alerts = await _evaluate_trigger(
                session, rule, config, dataset_id, changes, change_map, lineages,
            )
        except Exception:  # noqa: BLE001 — 규칙 하나가 나머지 규칙 평가를 막지 않도록
            logger.warning("알림 규칙 평가 실패로 건너뜀: rule_id=%s", rule.id, exc_info=True)
            continue
        created_alerts.extend(alerts)

    if created_alerts:
        await _persist_and_dispatch(session, created_alerts)
        logger.info("규칙 평가 완료: dataset_id=%d, alerts_created=%d", dataset_id, len(created_alerts))
    return created_alerts


async def evaluate_quality_alerts(
    session: AsyncSession,
    dataset_id: int,
    score: float,
    failed: list[dict],
) -> list[LineageAlert]:
    """품질 검증 실패에 대해 QUALITY_FAILED 트리거 규칙을 평가하고 알림을 생성한다.

    ``run_quality_check``(서버 검증) / ``import_results``(배치 반입)가 실패
    결과를 저장한 직후 호출된다. ``failed`` 는 실패 규칙 요약 dict 목록:
    ``{"rule_name", "check_type", "severity", "actual", "detail"}``.

    trigger_config(JSON) 옵션:
        {"min_severity": "BREAKING"}  # 이 심각도 이상 실패가 있을 때만 발동 (기본 INFO)
    """
    if not failed:
        return []

    rules = (await session.execute(
        select(AlertRule).where(
            AlertRule.is_active == "true",
            AlertRule.trigger_type == "QUALITY_FAILED",
        )
    )).scalars().all()
    logger.debug("품질 실패 규칙 평가: dataset_id=%d failed=%d active_rules=%d",
                 dataset_id, len(failed), len(rules))
    if not rules:
        return []

    dataset = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if not dataset:
        return []

    tag_ids = set((await session.execute(
        select(DatasetTag.tag_id).where(DatasetTag.dataset_id == dataset_id)
    )).scalars().all())
    lineage_ids = set((await session.execute(
        select(DatasetLineage.id).where(
            or_(
                DatasetLineage.source_dataset_id == dataset_id,
                DatasetLineage.target_dataset_id == dataset_id,
            )
        )
    )).scalars().all())

    def rank(f: dict) -> int:
        return _SEVERITY_RANK.get(f.get("severity", "INFO"), 1)

    created: list[LineageAlert] = []
    for rule in rules:
        if not _match_scope(rule, dataset_id, dataset.datasource_id, tag_ids, lineage_ids):
            continue
        config = _load_config(rule)
        if config is None:
            continue
        min_sev = _SEVERITY_RANK.get(str(config.get("min_severity", "INFO")), 1)
        matched = [f for f in failed if rank(f) >= min_sev]
        if not matched:
            continue
        # 발동 조건을 만족한 실패 중 최고 심각도 → 알림 심각도 (규칙 강제값 우선)
        worst = max(matched, key=rank).get("severity", "WARNING")
        names = ", ".join(str(f.get("rule_name", "?")) for f in matched[:3])
        more = f" 외 {len(matched) - 3}건" if len(matched) > 3 else ""
        created.append(LineageAlert(
            alert_type="QUALITY_FAILED",
            severity=rule.severity_override or worst,
            source_dataset_id=dataset_id,
            rule_id=rule.id,
            change_summary=_clip(f"품질 검증 실패: {dataset.name} — {names}{more} (점수 {score:.1f}%)"),
            change_detail=_json.dumps(
                {"score": score, "failed_rules": matched}, ensure_ascii=False, default=str,
            ),
        ))

    if created:
        await _persist_and_dispatch(session, created)
        logger.info("품질 알림 생성됨: dataset_id=%d, count=%d", dataset_id, len(created))
    return created


# ---------------------------------------------------------------------------
# 엔터티 알림 — 데이터셋이 아닌 대상(MCP 배포 등)
# ---------------------------------------------------------------------------

# MCP finding 심각도 → 알림 심각도
_ENTITY_SEVERITY = {"critical": "BREAKING", "high": "BREAKING", "medium": "WARNING", "low": "INFO"}
_ENTITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


async def evaluate_entity_alerts(
    session: AsyncSession,
    *,
    trigger_type: str,
    entity_type: str,
    entity_ref: str,
    severity: str,
    summary: str,
    detail: dict,
    kind: str | None = None,
    environment: str | None = None,
    datasource_id: int | None = None,
    owners: list[str] | None = None,
) -> list[LineageAlert]:
    """데이터셋이 아닌 엔터티 이벤트에 대해 ``trigger_type`` 규칙을 평가하고 알림을 만든다.

    규칙 scope: ``ALL`` 또는 ``DATASOURCE`` (엔터티의 대상 데이터소스와 일치할 때).
    trigger_config(JSON) 옵션::

        {"min_severity": "high", "environments": ["PROD"], "kinds": ["security_warning"]}

    ``owners`` 는 엔터티 담당자 — 규칙의 ``notify_owners`` 가 true 면 수신자에 더한다.
    """
    rules = (await session.execute(
        select(AlertRule).where(AlertRule.is_active == "true", AlertRule.trigger_type == trigger_type)
    )).scalars().all()
    logger.debug("엔터티 규칙 평가: trigger=%s entity=%s/%s severity=%s active_rules=%d",
                 trigger_type, entity_type, entity_ref, severity, len(rules))
    if not rules:
        return []
    created: list[LineageAlert] = []
    for rule in rules:
        if rule.scope_type == "DATASOURCE":
            if not datasource_id or rule.scope_id != datasource_id:
                continue
        elif rule.scope_type != "ALL":
            continue
        config = _load_config(rule)
        if config is None:
            continue
        # finding 심각도가 규칙의 최소 심각도(기본 low) 미만이면 발동하지 않는다
        if _ENTITY_RANK.get(severity, 0) < _ENTITY_RANK.get(str(config.get("min_severity", "low")).lower(), 1):
            continue
        envs = config.get("environments") or []
        if envs and environment not in envs:
            continue
        kinds = config.get("kinds") or []
        if kinds and kind not in kinds:
            continue
        created.append(LineageAlert(
            alert_type=trigger_type,
            severity=rule.severity_override or _ENTITY_SEVERITY.get(severity, "WARNING"),
            source_dataset_id=None,
            entity_type=entity_type,
            entity_ref=entity_ref[:500],
            rule_id=rule.id,
            change_summary=_clip(summary),
            change_detail=_json.dumps(detail, ensure_ascii=False, default=str),
        ))
    if created:
        await _persist_and_dispatch(session, created, extra_owners=owners)
        logger.info("엔터티 알림 생성됨: %s %s (%s) count=%d", entity_type, entity_ref, trigger_type, len(created))
    return created


async def _persist_and_dispatch(session: AsyncSession, alerts: list[LineageAlert],
                                extra_owners: list[str] | None = None) -> None:
    """알림 저장 후 전달. 전달 실패는 알림 자체를 막지 않는다.

    flush 로 알림 id 를 확보한 뒤 규칙을 일괄 조회해 전달한다(커밋은 호출자 책임).
    """
    for alert in alerts:
        session.add(alert)
    await session.flush()
    rule_ids = {a.rule_id for a in alerts if a.rule_id}
    rules = {}
    if rule_ids:
        rules = {r.id: r for r in (await session.execute(
            select(AlertRule).where(AlertRule.id.in_(rule_ids))
        )).scalars().all()}
    for alert in alerts:
        await _dispatch_notifications(session, alert, rules.get(alert.rule_id), extra_owners=extra_owners)
    await session.flush()


def _match_scope(
    rule: AlertRule,
    dataset_id: int,
    datasource_id: int,
    tag_ids: set[int],
    lineage_ids: set[int],
) -> bool:
    """규칙의 적용 범위(scope)가 변경된 데이터셋과 매칭되는지 확인한다."""
    if rule.scope_type == "ALL":
        return True
    if rule.scope_type == "DATASET":
        return rule.scope_id == dataset_id
    if rule.scope_type == "TAG":
        return rule.scope_id in tag_ids
    if rule.scope_type == "LINEAGE":
        return rule.scope_id in lineage_ids
    if rule.scope_type == "DATASOURCE":
        return rule.scope_id == datasource_id
    return False


def _change_phrase(changes: list[dict], limit: int = 3) -> str:
    """'컬럼' 변경유형 나열(최대 limit 개 + 외 N건)."""
    parts = [f"'{c.get('field')}' {_CHANGE_LABEL.get(c.get('type'), c.get('type'))}" for c in changes[:limit]]
    text = "; ".join(parts)
    if len(changes) > limit:
        text += f" 외 {len(changes) - limit}건"
    return text


async def _evaluate_trigger(
    session: AsyncSession,
    rule: AlertRule,
    config: dict,
    dataset_id: int,
    changes: list[dict],
    change_map: dict[str, dict],
    lineages: list,
) -> list[LineageAlert]:
    """규칙의 트리거 조건을 변경 사항과 대조해 ``LineageAlert`` 객체를 생성한다."""
    alerts: list[LineageAlert] = []
    all_types = ["DROP", "MODIFY", "ADD"]

    if rule.trigger_type == "ANY":
        severity = rule.severity_override or _auto_severity(changes)
        alerts.append(_create_alert(
            rule, dataset_id, None, None, severity,
            _build_generic_summary(changes),
            _json.dumps(changes, ensure_ascii=False, default=str),
        ))

    elif rule.trigger_type == "SCHEMA_CHANGE":
        allowed_types = _str_set(config.get("change_types"), all_types)
        filtered = [c for c in changes if c.get("type") in allowed_types]
        if filtered:
            severity = rule.severity_override or _auto_severity(filtered)
            alerts.append(_create_alert(
                rule, dataset_id, None, None, severity,
                _build_generic_summary(filtered),
                _json.dumps(filtered, ensure_ascii=False, default=str),
            ))

    elif rule.trigger_type == "COLUMN_WATCH":
        watch_cols = _str_set(config.get("columns"), [])
        allowed_types = _str_set(config.get("change_types"), all_types)
        matched = [c for c in changes if c.get("field") in watch_cols and c.get("type") in allowed_types]
        if matched:
            severity = rule.severity_override or _auto_severity(matched)
            alerts.append(_create_alert(
                rule, dataset_id, None, None, severity,
                "감시 컬럼 변경: " + _change_phrase(matched),
                _json.dumps(matched, ensure_ascii=False, default=str),
            ))

    elif rule.trigger_type == "MAPPING_BROKEN":
        if rule.scope_type == "LINEAGE" and rule.scope_id:
            target_lineages = [lin for lin in lineages if lin.id == rule.scope_id]
        else:
            target_lineages = lineages
        for lineage in target_lineages:
            alerts.extend(await _check_mapping_impact(session, rule, dataset_id, lineage, change_map))

    return alerts


async def _check_mapping_impact(
    session: AsyncSession,
    rule: AlertRule,
    dataset_id: int,
    lineage,
    change_map: dict[str, dict],
) -> list[LineageAlert]:
    """특정 리니지 관계에 대해 컬럼 매핑이 변경 사항의 영향을 받는지 확인한다.

    변경된 데이터셋이 리니지의 출발(source) 쪽이면 source_column 을, 도착(target) 쪽이면
    target_column 을 변경 컬럼과 대조하고, 반대편 데이터셋을 영향 대상으로 기록한다.
    영향 항목이 있으면 리니지당 알림 1건(최고 심각도)을 돌려준다.
    """
    if lineage.source_dataset_id == dataset_id:
        affected_id = lineage.target_dataset_id
        mapping_field = "source_column"
    else:
        affected_id = lineage.source_dataset_id
        mapping_field = "target_column"

    mappings = (await session.execute(
        select(DatasetColumnMapping).where(
            DatasetColumnMapping.dataset_lineage_id == lineage.id
        )
    )).scalars().all()
    if not mappings:
        return []

    impact_items = []
    for mapping in mappings:
        mapped_col = getattr(mapping, mapping_field)
        if mapped_col in change_map:
            change = change_map[mapped_col]
            other_col = (
                mapping.target_column if mapping_field == "source_column"
                else mapping.source_column
            )
            impact_items.append({
                "changed_column": mapped_col,
                "mapped_to": other_col,
                "change_type": change.get("type"),
                "severity": _determine_severity(change),
                "before": change.get("before"),
                "after": change.get("after"),
            })

    if not impact_items:
        return []

    max_sev = max(impact_items, key=lambda x: _SEVERITY_RANK.get(x["severity"], 0))
    severity = rule.severity_override or max_sev["severity"]

    parts = [
        f"'{it['changed_column']}' {_CHANGE_LABEL.get(it['change_type'], it['change_type'])} (매핑: {it['mapped_to']})"
        for it in impact_items[:3]
    ]
    summary = "매핑 컬럼 변경: " + "; ".join(parts)
    if len(impact_items) > 3:
        summary += f" 외 {len(impact_items) - 3}건"

    return [_create_alert(
        rule, dataset_id, affected_id, lineage.id, severity,
        summary, _json.dumps(impact_items, ensure_ascii=False, default=str),
    )]


def _create_alert(
    rule: AlertRule,
    source_dataset_id: int,
    affected_dataset_id: int | None,
    lineage_id: int | None,
    severity: str,
    summary: str,
    detail: str,
) -> LineageAlert:
    """매칭된 규칙·변경 정보로 ``LineageAlert`` ORM 객체를 만들어 돌려준다(미커밋)."""
    return LineageAlert(
        alert_type="SCHEMA_CHANGE",
        severity=severity,
        source_dataset_id=source_dataset_id,
        affected_dataset_id=affected_dataset_id,
        lineage_id=lineage_id,
        rule_id=rule.id,
        change_summary=_clip(summary),
        change_detail=detail,
    )


def _determine_severity(change: dict) -> str:
    """변경 유형에 따라 심각도를 산정한다(DROP=BREAKING, 타입 변경=WARNING, 그 외=INFO)."""
    if change.get("type") == "DROP":
        return "BREAKING"
    if change.get("type") == "MODIFY":
        before = change.get("before") or {}
        after = change.get("after") or {}
        if "field_type" in before or "field_type" in after:
            return "WARNING"
        if "native_type" in before or "native_type" in after:
            return "WARNING"
        return "INFO"
    return "INFO"


def _auto_severity(changes: list[dict]) -> str:
    """여러 변경에 대해 가장 높은 심각도를 자동 계산한다."""
    max_sev = "INFO"
    for c in changes:
        s = _determine_severity(c)
        if _SEVERITY_RANK.get(s, 0) > _SEVERITY_RANK.get(max_sev, 0):
            max_sev = s
    return max_sev


def _build_generic_summary(changes: list[dict]) -> str:
    """기본 요약 문자열 — '스키마 변경: 컬럼 삭제 1개, 변경 2개, 추가 1개'."""
    counts = {t: sum(1 for c in changes if c.get("type") == t) for t in ("DROP", "MODIFY", "ADD")}
    parts = [f"{_CHANGE_LABEL[t]} {n}개" for t, n in counts.items() if n]
    return "스키마 변경: 컬럼 " + ", ".join(parts) if parts else "스키마 변경"


# ---------------------------------------------------------------------------
# Notification Dispatch
# ---------------------------------------------------------------------------

def rule_channels(rule: AlertRule | None) -> list[str]:
    """규칙의 채널 중 지원 채널만(과거 데이터의 EMAIL 등은 무시). 없으면 IN_APP."""
    if not rule or not rule.channels:
        return ["IN_APP"]
    chans = [c.strip().upper() for c in rule.channels.split(",")]
    out = [c for c in dict.fromkeys(chans) if c in SUPPORTED_CHANNELS]
    return out or ["IN_APP"]


async def _dispatch_notifications(session: AsyncSession, alert: LineageAlert, rule: AlertRule | None,
                                  extra_owners: list[str] | None = None) -> None:
    """알림을 규칙 구독자와 데이터셋(또는 엔터티) 소유자에게 전달한다.

    - IN_APP : 수신자마다 ``AlertNotification`` 기록
    - WEBHOOK: 규칙의 URL 로 1회 POST, 결과(SENT/FAILED)를 ``AlertNotification`` 에 기록
    """
    recipients: list[str] = []

    def add(name: str | None) -> None:
        name = (name or "").strip()
        if name and name not in recipients:
            recipients.append(name)

    if rule and rule.subscribers:
        for sub in rule.subscribers.split(","):
            add(sub)

    if not rule or rule.notify_owners == "true":
        ds_ids = [i for i in (alert.source_dataset_id, alert.affected_dataset_id) if i]
        if ds_ids:
            for owner in (await session.execute(
                select(Owner.owner_name).where(Owner.dataset_id.in_(ds_ids))
            )).scalars().all():
                add(owner)
        for owner in extra_owners or []:
            add(owner)

    channels = rule_channels(rule)
    logger.debug("알림 전달: alert_id=%s channels=%s recipients=%d",
                 alert.id, channels, len(recipients))

    if "IN_APP" in channels:
        for recipient in recipients:
            session.add(AlertNotification(
                alert_id=alert.id, channel="IN_APP", recipient=recipient[:_RECIPIENT_MAX], status="SENT",
            ))

    if "WEBHOOK" in channels and rule and rule.webhook_url:
        ok = await _send_webhook(session, alert, rule)
        host = urlparse(rule.webhook_url).netloc or rule.webhook_url
        session.add(AlertNotification(
            alert_id=alert.id, channel="WEBHOOK", recipient=host[:_RECIPIENT_MAX],
            status="SENT" if ok else "FAILED",
        ))


async def _send_webhook(session: AsyncSession, alert: LineageAlert, rule: AlertRule) -> bool:
    """규칙의 Webhook URL 로 알림 payload 를 POST 한다. 2xx 면 True.

    payload 는 Slack Incoming Webhook 호환 ``text`` 필드 + 구조화 필드를 함께 담는다.
    """
    ids = [i for i in (alert.source_dataset_id, alert.affected_dataset_id) if i]
    names: dict[int, tuple[str, str]] = {}
    if ids:
        for ds_id, name, ds_type in (await session.execute(
            select(Dataset.id, Dataset.name, Datasource.type)
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(Dataset.id.in_(ids))
        )).all():
            names[ds_id] = (name, ds_type)
    src_name, src_type = names.get(alert.source_dataset_id or 0, ("", ""))
    aff_name, aff_type = names.get(alert.affected_dataset_id or 0, ("", ""))

    try:
        changes = _json.loads(alert.change_detail) if alert.change_detail else []
    except ValueError:
        changes = alert.change_detail

    target = (f"{_entity_label(alert.entity_type)} {alert.entity_ref}" if alert.entity_type
              else f"{src_type}.{src_name}" if src_name else "")
    text = f"[{_SEVERITY_LABEL.get(alert.severity, alert.severity)}] {alert.change_summary}"
    if target:
        text += f"\n대상: {target}"
    if aff_name:
        text += f"\n영향: {aff_type}.{aff_name}"
    text += f"\n규칙: {rule.rule_name}"

    payload = {
        "text": text,
        "alert_id": alert.id,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "rule_name": rule.rule_name,
        "source": {"dataset": src_name, "datasource": src_type},
        "affected": {"dataset": aff_name, "datasource": aff_type},
        "entity": ({"type": alert.entity_type, "ref": alert.entity_ref} if alert.entity_type else None),
        "change_summary": alert.change_summary,
        "changes": changes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    host = urlparse(rule.webhook_url).netloc
    try:
        async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT, transport=_webhook_transport) as client:
            resp = await client.post(rule.webhook_url, json=payload)
    except Exception as e:  # noqa: BLE001 — 외부 호출 실패는 기록만
        logger.warning("Webhook 전송 실패: rule_id=%s host=%s - %s", rule.id, host, e)
        return False
    if resp.is_success:
        logger.info("Webhook 전송 완료: rule_id=%s host=%s (HTTP %d)", rule.id, host, resp.status_code)
        return True
    logger.warning("Webhook 응답 오류: rule_id=%s host=%s (HTTP %d)", rule.id, host, resp.status_code)
    return False


def _entity_label(entity_type: str | None) -> str:
    """엔터티 유형 코드를 사람이 읽는 라벨로 바꾼다(모르는 값은 그대로)."""
    return {"mcp_deployment": "MCP 배포"}.get(entity_type or "", entity_type or "")


# ---------------------------------------------------------------------------
# Alert Rule CRUD
# ---------------------------------------------------------------------------

async def _scope_exists(session: AsyncSession, scope_type: str, scope_id: int) -> bool:
    """범위 대상(데이터셋·태그·리니지·데이터소스) 행이 존재하는지 확인한다."""
    model = {"DATASET": Dataset, "TAG": Tag, "LINEAGE": DatasetLineage, "DATASOURCE": Datasource}.get(scope_type)
    if model is None:
        return False
    return (await session.execute(select(model.id).where(model.id == scope_id))).scalar() is not None


async def _validate_rule_state(session: AsyncSession, *, scope_type: str, scope_id: int | None,
                               trigger_type: str, channels: str, webhook_url: str | None) -> None:
    """병합된 규칙 상태의 조합 검증. 실패 시 ``RuleValidationError``."""
    logger.debug("알림 규칙 조합 검증: scope=%s/%s trigger=%s channels=%s",
                 scope_type, scope_id, trigger_type, channels)
    if scope_type != "ALL":
        if not scope_id:
            raise RuleValidationError("감시 대상(범위)을 선택해야 합니다.")
        if not await _scope_exists(session, scope_type, scope_id):
            raise RuleValidationError("선택한 감시 대상이 존재하지 않습니다(삭제되었을 수 있습니다).")
    if trigger_type in ENTITY_TRIGGERS and scope_type not in ("ALL", "DATASOURCE"):
        raise RuleValidationError("MCP 트리거는 범위를 '전체' 또는 '데이터 소스'로만 지정할 수 있습니다.")
    if trigger_type == "SYNC_STALE":
        raise RuleValidationError("SYNC_STALE 트리거는 지원하지 않습니다.")
    if "WEBHOOK" in channels.split(",") and not webhook_url:
        raise RuleValidationError("Webhook 채널을 선택하면 Webhook URL 을 입력해야 합니다.")


async def create_rule(session: AsyncSession, data: AlertRuleCreate,
                      created_by: str | None = None) -> AlertRuleResponse:
    """알림 규칙 생성. 범위 대상 존재·범위/트리거 조합·Webhook URL 을 검증한다."""
    scope_id = None if data.scope_type.value == "ALL" else data.scope_id
    await _validate_rule_state(
        session, scope_type=data.scope_type.value, scope_id=scope_id,
        trigger_type=data.trigger_type.value, channels=data.channels, webhook_url=data.webhook_url,
    )
    rule = AlertRule(
        rule_name=data.rule_name,
        description=data.description,
        scope_type=data.scope_type.value,
        scope_id=scope_id,
        trigger_type=data.trigger_type.value,
        trigger_config=data.trigger_config,
        severity_override=data.severity_override.value if data.severity_override else None,
        channels=data.channels,
        notify_owners=data.notify_owners,
        # Webhook 채널이 아니면 URL 은 보관하지 않는다(시크릿 최소 보관)
        webhook_url=data.webhook_url if "WEBHOOK" in data.channels.split(",") else None,
        subscribers=data.subscribers,
        created_by=created_by,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    logger.info("알림 규칙 생성됨: id=%d, name=%s, scope=%s/%s", rule.id, rule.rule_name, rule.scope_type, rule.scope_id)
    return (await _build_rule_responses(session, [rule]))[0]


async def list_rules(session: AsyncSession, reveal_webhook: bool = True) -> list[AlertRuleResponse]:
    """알림 규칙 전체 목록(최신 생성순). 적용 범위 이름과 누적 알림 건수가 같이 채워진다."""
    rules = (await session.execute(
        select(AlertRule).order_by(AlertRule.created_at.desc(), AlertRule.id.desc())
    )).scalars().all()
    return await _build_rule_responses(session, list(rules), reveal_webhook=reveal_webhook)


async def get_rule(session: AsyncSession, rule_id: int) -> AlertRule | None:
    """규칙 ORM 객체 단건 조회. 없으면 ``None``."""
    return (await session.execute(
        select(AlertRule).where(AlertRule.id == rule_id)
    )).scalar_one_or_none()


async def get_rule_response(session: AsyncSession, rule: AlertRule,
                            reveal_webhook: bool = True) -> AlertRuleResponse:
    """규칙 1건을 응답 DTO 로 변환한다(범위 이름·알림 건수 포함)."""
    return (await _build_rule_responses(session, [rule], reveal_webhook=reveal_webhook))[0]


def _enum_value(v):
    """Enum 이면 .value, 아니면 그대로 — model_dump 결과를 ORM 컬럼 값으로 맞춘다."""
    return v.value if hasattr(v, "value") else v


async def update_rule(
    session: AsyncSession, rule_id: int, data: AlertRuleUpdate,
) -> AlertRuleResponse | None:
    """규칙 부분 갱신. 기존 값과 병합한 상태로 설정·조합을 다시 검증한다."""
    rule = await get_rule(session, rule_id)
    if not rule:
        return None
    changes = {k: _enum_value(v) for k, v in data.model_dump(exclude_unset=True).items()}

    merged_trigger = changes.get("trigger_type", rule.trigger_type)
    merged_scope = changes.get("scope_type", rule.scope_type)
    merged_scope_id = None if merged_scope == "ALL" else changes.get("scope_id", rule.scope_id)
    merged_channels = changes.get("channels", rule.channels or "IN_APP")
    merged_webhook = changes.get("webhook_url", rule.webhook_url)

    # 트리거·설정이 바뀌면 병합된 트리거 기준으로 설정을 다시 검증·정규화
    if "trigger_config" in changes or "trigger_type" in changes:
        try:
            changes["trigger_config"] = normalize_trigger_config(
                merged_trigger, changes.get("trigger_config", rule.trigger_config),
            )
        except ValueError as e:
            raise RuleValidationError(str(e)) from e

    # 다른 필드만 바꾸는 경우(예: 활성 토글)는 범위 대상 삭제로 막히지 않도록 범위 검증을 건너뛴다
    touches_def = bool({"scope_type", "scope_id", "trigger_type", "channels", "webhook_url"} & changes.keys())
    if touches_def:
        await _validate_rule_state(
            session, scope_type=merged_scope, scope_id=merged_scope_id, trigger_type=merged_trigger,
            channels=merged_channels, webhook_url=merged_webhook,
        )
    elif changes.get("is_active") == "true" and rule.trigger_type == "SYNC_STALE":
        raise RuleValidationError("SYNC_STALE 트리거는 지원하지 않아 활성화할 수 없습니다.")

    changes["scope_id"] = merged_scope_id
    if "WEBHOOK" not in merged_channels.split(","):
        changes["webhook_url"] = None
    for field, value in changes.items():
        setattr(rule, field, value)
    await session.flush()
    await session.refresh(rule)
    logger.info("알림 규칙 수정됨: id=%d, name=%s, fields=%s", rule.id, rule.rule_name, list(changes.keys()))
    return (await _build_rule_responses(session, [rule]))[0]


async def delete_rule(session: AsyncSession, rule_id: int) -> bool:
    """알림 규칙 삭제. 이미 발생한 알림은 ``rule_id`` 가 NULL 로 떨어지고 그대로 남는다(``ON DELETE SET NULL``)."""
    rule = await get_rule(session, rule_id)
    if not rule:
        return False
    logger.info("알림 규칙 삭제됨: id=%d, name=%s", rule.id, rule.rule_name)
    await session.delete(rule)
    await session.flush()
    return True


async def _build_rule_responses(session: AsyncSession, rules: list[AlertRule],
                                reveal_webhook: bool = True) -> list[AlertRuleResponse]:
    """규칙 응답 목록 — 범위 이름·누적 알림 건수를 유형별 일괄 조회로 채운다(N+1 없음)."""
    if not rules:
        return []
    ids_by_type: dict[str, set[int]] = {}
    for r in rules:
        if r.scope_type != "ALL" and r.scope_id:
            ids_by_type.setdefault(r.scope_type, set()).add(r.scope_id)

    names: dict[tuple[str, int], str] = {}
    if ids := ids_by_type.get("DATASET"):
        for i, n in (await session.execute(select(Dataset.id, Dataset.name).where(Dataset.id.in_(ids)))).all():
            names[("DATASET", i)] = n
    if ids := ids_by_type.get("TAG"):
        for i, n in (await session.execute(select(Tag.id, Tag.name).where(Tag.id.in_(ids)))).all():
            names[("TAG", i)] = n
    if ids := ids_by_type.get("DATASOURCE"):
        for i, n in (await session.execute(
            select(Datasource.id, Datasource.name).where(Datasource.id.in_(ids))
        )).all():
            names[("DATASOURCE", i)] = n
    if ids := ids_by_type.get("LINEAGE"):
        lin_rows = (await session.execute(
            select(DatasetLineage.id, DatasetLineage.source_dataset_id, DatasetLineage.target_dataset_id)
            .where(DatasetLineage.id.in_(ids))
        )).all()
        ds_ids = {x for _, s, t in lin_rows for x in (s, t) if x}
        ds_names = dict((await session.execute(
            select(Dataset.id, Dataset.name).where(Dataset.id.in_(ds_ids))
        )).all()) if ds_ids else {}
        for lid, s, t in lin_rows:
            src, tgt = ds_names.get(s, "?"), ds_names.get(t, "?")
            names[("LINEAGE", lid)] = f"{src} → {tgt}"

    counts = dict((await session.execute(
        select(LineageAlert.rule_id, func.count(LineageAlert.id))
        .where(LineageAlert.rule_id.in_([r.id for r in rules]))
        .group_by(LineageAlert.rule_id)
    )).all())

    out = []
    for rule in rules:
        scope_name = names.get((rule.scope_type, rule.scope_id)) if rule.scope_id else None
        scope_missing = rule.scope_type != "ALL" and scope_name is None
        out.append(AlertRuleResponse(
            id=rule.id,
            rule_name=rule.rule_name,
            description=rule.description,
            scope_type=rule.scope_type,
            scope_id=rule.scope_id,
            scope_name=scope_name,
            scope_missing=scope_missing,
            trigger_type=rule.trigger_type,
            trigger_config=rule.trigger_config or "{}",
            severity_override=rule.severity_override,
            channels=rule.channels,
            notify_owners=rule.notify_owners,
            webhook_url=rule.webhook_url if reveal_webhook else None,
            webhook_configured=bool(rule.webhook_url),
            subscribers=rule.subscribers,
            is_active=rule.is_active,
            created_by=rule.created_by,
            created_at=rule.created_at,
            updated_at=rule.updated_at,
            alert_count=counts.get(rule.id, 0),
        ))
    return out


# ---------------------------------------------------------------------------
# Alert 조회·상태 전이
# ---------------------------------------------------------------------------

# 허용 상태 전이 — 다시 열기(→OPEN)는 처리 기록을 비운다.
_TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"ACKNOWLEDGED", "RESOLVED", "DISMISSED"},
    "ACKNOWLEDGED": {"OPEN", "RESOLVED", "DISMISSED"},
    "RESOLVED": {"OPEN"},
    "DISMISSED": {"OPEN"},
}
_STATUS_LABEL = {"OPEN": "미확인", "ACKNOWLEDGED": "확인", "RESOLVED": "해결", "DISMISSED": "무시"}


async def managed_dataset_ids(session: AsyncSession, username: str, dataset_ids: set[int]) -> set[int]:
    """주어진 데이터셋 중 사용자가 생성자이거나 소유자(Owner)로 등록된 것."""
    if not dataset_ids or not username:
        return set()
    created = set((await session.execute(
        select(Dataset.id).where(Dataset.id.in_(dataset_ids), Dataset.created_by == username)
    )).scalars().all())
    owned = set((await session.execute(
        select(Owner.dataset_id).where(Owner.dataset_id.in_(dataset_ids), Owner.owner_name == username)
    )).scalars().all())
    return created | owned


def _alert_dataset_ids(alert: LineageAlert) -> set[int]:
    """알림의 출처·영향 데이터셋 id 집합(NULL 제외)."""
    return {i for i in (alert.source_dataset_id, alert.affected_dataset_id) if i}


async def _updatable_ids(session: AsyncSession, alerts: list[LineageAlert], current,
                         is_manager: bool) -> set[int]:
    """현재 사용자가 상태를 바꿀 수 있는 알림 id 집합.

    규칙 관리자(admin 포함)는 전부. 그 외에는 관련 데이터셋(출처·영향)의 생성자·소유자만.
    엔터티(MCP) 알림은 규칙 관리자만.
    """
    if current is None:
        return set()
    if is_manager:
        return {a.id for a in alerts}
    all_ds = set().union(*(_alert_dataset_ids(a) for a in alerts)) if alerts else set()
    mine = await managed_dataset_ids(session, current.username, all_ds)
    return {a.id for a in alerts if _alert_dataset_ids(a) & mine}


async def list_alerts(
    session: AsyncSession,
    status: str | None = None,
    severity: str | None = None,
    dataset_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
    alert_type: str | None = None,
    entity_type: str | None = None,
    entity_ref: str | None = None,
    *,
    current=None,
    is_manager: bool = False,
) -> PaginatedAlerts:
    """알림 목록을 필터(상태·심각도·유형·관련 데이터셋) 와 페이징으로 조회한다."""
    conds = []
    if status:
        conds.append(LineageAlert.status == status)
    if severity:
        conds.append(LineageAlert.severity == severity)
    for col, val in ((LineageAlert.alert_type, alert_type), (LineageAlert.entity_type, entity_type),
                     (LineageAlert.entity_ref, entity_ref)):
        if val:
            conds.append(col == val)
    if dataset_id:
        conds.append(or_(
            LineageAlert.source_dataset_id == dataset_id,
            LineageAlert.affected_dataset_id == dataset_id,
        ))

    total = (await session.execute(select(func.count(LineageAlert.id)).where(*conds))).scalar() or 0
    offset = (page - 1) * page_size
    alerts = (await session.execute(
        select(LineageAlert).where(*conds)
        .order_by(LineageAlert.created_at.desc(), LineageAlert.id.desc())
        .offset(offset).limit(page_size)
    )).scalars().all()

    items = await build_alert_responses(session, list(alerts), current=current, is_manager=is_manager)
    return PaginatedAlerts(items=items, total=total, page=page, page_size=page_size)


async def get_alert(session: AsyncSession, alert_id: int) -> LineageAlert | None:
    """알림 ORM 객체 단건 조회. 없으면 ``None``."""
    return (await session.execute(
        select(LineageAlert).where(LineageAlert.id == alert_id)
    )).scalar_one_or_none()


async def can_update_alert(session: AsyncSession, alert: LineageAlert, current, is_manager: bool) -> bool:
    """현재 사용자가 이 알림의 상태를 바꿀 수 있는지 반환한다(_updatable_ids 규칙)."""
    return alert.id in await _updatable_ids(session, [alert], current, is_manager)


def apply_status(alert: LineageAlert, new_status: str, actor: str) -> None:
    """상태 전이 적용. 허용되지 않으면 ``AlertTransitionError``.

    확인·해결·무시는 처리자·시각을 기록하고, 다시 열기(→OPEN)는 비운다.
    """
    prev = alert.status
    if new_status == prev:
        raise AlertTransitionError(f"이미 '{_STATUS_LABEL.get(prev, prev)}' 상태입니다.")
    if new_status not in _TRANSITIONS.get(prev, set()):
        raise AlertTransitionError(
            f"'{_STATUS_LABEL.get(prev, prev)}' 상태에서 '{_STATUS_LABEL.get(new_status, new_status)}'(으)로 "
            "바꿀 수 없습니다."
        )
    alert.status = new_status
    if new_status == "OPEN":
        alert.resolved_by = None
        alert.resolved_at = None
    else:
        alert.resolved_by = actor
        alert.resolved_at = datetime.now(timezone.utc)
    logger.info("알림 상태 변경됨: id=%d, %s -> %s, by=%s", alert.id, prev, new_status, actor)


async def bulk_update_status(session: AsyncSession, ids: list[int], new_status: str, current,
                             is_manager: bool) -> AlertBulkResult:
    """여러 알림 상태를 한 번에 바꾼다. 권한 없음·전이 불가·없음은 건너뛰고 사유를 돌려준다."""
    uniq = list(dict.fromkeys(ids))
    alerts = {a.id: a for a in (await session.execute(
        select(LineageAlert).where(LineageAlert.id.in_(uniq))
    )).scalars().all()}
    allowed = await _updatable_ids(session, list(alerts.values()), current, is_manager)
    result = AlertBulkResult()
    for aid in uniq:
        alert = alerts.get(aid)
        if alert is None:
            result.skipped.append({"id": aid, "reason": "알림을 찾을 수 없습니다."})
        elif aid not in allowed:
            result.skipped.append({"id": aid, "reason": "상태를 바꿀 권한이 없습니다."})
        else:
            try:
                apply_status(alert, new_status, current.username)
                result.updated.append(aid)
            except AlertTransitionError as e:
                result.skipped.append({"id": aid, "reason": str(e)})
    await session.flush()
    logger.debug("알림 상태 일괄 변경 결과: status=%s updated=%d skipped=%d",
                 new_status, len(result.updated), len(result.skipped))
    return result


async def purge_closed_alerts(session: AsyncSession, older_than_days: int) -> int:
    """해결·무시된 지 ``older_than_days`` 일이 지난 알림(과 전달 기록)을 삭제한다.

    기준 시각은 resolved_at(없으면 created_at). 500건 단위로 나눠 전달 기록 → 알림 순으로
    삭제하며, 삭제 건수를 반환한다(커밋은 호출자 책임).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    cond = (
        LineageAlert.status.in_(("RESOLVED", "DISMISSED")),
        func.coalesce(LineageAlert.resolved_at, LineageAlert.created_at) < cutoff,
    )
    ids = (await session.execute(select(LineageAlert.id).where(*cond))).scalars().all()
    if not ids:
        logger.debug("정리할 종료 알림 없음: older_than_days=%d", older_than_days)
        return 0
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        await session.execute(delete(AlertNotification).where(AlertNotification.alert_id.in_(chunk)))
        await session.execute(delete(LineageAlert).where(LineageAlert.id.in_(chunk)))
    await session.flush()
    logger.info("오래된 처리 완료 알림 삭제: %d건 (기준 %d일)", len(ids), older_than_days)
    return len(ids)


async def get_alert_summary(session: AsyncSession) -> AlertSummary:
    """미해결(OPEN) 알림을 심각도별로 집계해 헤더 벨 배지에 쓰는 요약을 만든다."""
    rows = (await session.execute(
        select(LineageAlert.severity, func.count(LineageAlert.id))
        .where(LineageAlert.status == "OPEN")
        .group_by(LineageAlert.severity)
    )).all()

    summary = AlertSummary()
    for severity, count in rows:
        summary.total_open += count
        if severity == "BREAKING":
            summary.breaking_count = count
        elif severity == "WARNING":
            summary.warning_count = count
        elif severity == "INFO":
            summary.info_count = count
    return summary


async def build_alert_responses(session: AsyncSession, alerts: list[LineageAlert], *,
                                current=None, is_manager: bool = False) -> list[AlertResponse]:
    """알림 응답 목록 — 데이터셋·규칙 이름을 일괄 조회로 채운다(N+1 없음)."""
    if not alerts:
        return []
    ds_ids = set().union(*(_alert_dataset_ids(a) for a in alerts))
    ds_info: dict[int, tuple[str, str]] = {}
    if ds_ids:
        for i, name, ds_type in (await session.execute(
            select(Dataset.id, Dataset.name, Datasource.type)
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(Dataset.id.in_(ds_ids))
        )).all():
            ds_info[i] = (name, ds_type)
    rule_ids = {a.rule_id for a in alerts if a.rule_id}
    rule_names = dict((await session.execute(
        select(AlertRule.id, AlertRule.rule_name).where(AlertRule.id.in_(rule_ids))
    )).all()) if rule_ids else {}
    updatable = await _updatable_ids(session, alerts, current, is_manager)

    out = []
    for alert in alerts:
        src = ds_info.get(alert.source_dataset_id or 0, (None, None))
        aff = ds_info.get(alert.affected_dataset_id or 0, (None, None))
        out.append(AlertResponse(
            id=alert.id,
            alert_type=alert.alert_type,
            severity=alert.severity,
            source_dataset_id=alert.source_dataset_id,
            entity_type=alert.entity_type,
            entity_ref=alert.entity_ref,
            source_dataset_name=src[0],
            source_datasource_type=src[1],
            affected_dataset_id=alert.affected_dataset_id,
            affected_dataset_name=aff[0],
            affected_datasource_type=aff[1],
            lineage_id=alert.lineage_id,
            rule_id=alert.rule_id,
            rule_name=rule_names.get(alert.rule_id),
            change_summary=alert.change_summary,
            change_detail=alert.change_detail,
            status=alert.status,
            resolved_by=alert.resolved_by,
            resolved_at=alert.resolved_at,
            created_at=alert.created_at,
            can_update=alert.id in updatable,
        ))
    return out


async def build_alert_response(session: AsyncSession, alert: LineageAlert, *,
                               current=None, is_manager: bool = False) -> AlertResponse:
    """알림 1건을 응답 DTO 로 변환한다(build_alert_responses 의 단건 버전)."""
    return (await build_alert_responses(session, [alert], current=current, is_manager=is_manager))[0]
