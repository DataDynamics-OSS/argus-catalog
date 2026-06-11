"""리니지 변경 알림 서비스 레이어(Alert Rule Engine 포함).

핵심 비즈니스 로직:
  1. Rule 평가     : 스키마 변경 발생 시 활성 Rule 을 순회하며 scope/trigger 매칭
  2. 영향 분석     : 변경 컬럼과 리니지 컬럼 매핑을 교차 확인해 영향 범위 산정
  3. 알림 생성     : 매칭된 Rule 에 따라 ``LineageAlert`` 레코드 생성
  4. 알림 전달     : 구독자 + 데이터셋 Owner 에게 IN_APP / WEBHOOK 발송

로깅 정책:
  - Rule 생성/수정/삭제는 라우터에서 INFO 로 기록한다.
  - 알림 생성·발송은 본 모듈에서 INFO 로 기록한다 (몇 개 만들었는지 / 어디로
    보냈는지).
  - Webhook 호출 실패, 영향 데이터셋 누락 등 외부/일관성 문제는 WARNING 또는
    ERROR 로 표면화한다.
"""

import json as _json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alert.models import AlertNotification, AlertRule, LineageAlert
from app.alert.schemas import (
    AlertResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    AlertSummary,
    AlertUpdateStatus,
    PaginatedAlerts,
)
from app.catalog.models import (
    Dataset,
    DatasetColumnMapping,
    DatasetLineage,
    DatasetTag,
    Owner,
    Datasource,
    Tag,
)

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "BREAKING": 2}


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
        3. 생성된 알림을 구독자/소유자에게 전달 (IN_APP / WEBHOOK)
    """
    if not changes:
        return []

    rules = (await session.execute(
        select(AlertRule).where(AlertRule.is_active == "true")
    )).scalars().all()

    if not rules:
        return []

    # 변경된 데이터셋 정보 조회
    dataset = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if not dataset:
        return []

    # 이 데이터셋에 붙은 태그 ID
    tag_ids = set((await session.execute(
        select(DatasetTag.tag_id).where(DatasetTag.dataset_id == dataset_id)
    )).scalars().all())

    # 이 데이터셋이 포함된 리니지 관계
    lineages = (await session.execute(
        select(DatasetLineage).where(
            or_(
                DatasetLineage.source_dataset_id == dataset_id,
                DatasetLineage.target_dataset_id == dataset_id,
            )
        )
    )).scalars().all()
    lineage_ids = {l.id for l in lineages}

    change_map = {c["field"]: c for c in changes}
    created_alerts: list[LineageAlert] = []

    for rule in rules:
        # ── 1. 범위(scope) 매칭 ──
        if not _match_scope(rule, dataset_id, dataset.datasource_id, tag_ids, lineage_ids):
            continue

        # ── 2. 트리거 평가 + 알림 생성 ──
        alerts = await _evaluate_trigger(
            session, rule, dataset_id, changes, change_map, lineages,
        )
        created_alerts.extend(alerts)

    if created_alerts:
        for alert in created_alerts:
            session.add(alert)
        await session.flush()

        for alert in created_alerts:
            await _dispatch_notifications(session, alert)

    if created_alerts:
        logger.info("규칙 평가 완료: dataset_id=%d, alerts_created=%d", dataset_id, len(created_alerts))
    return created_alerts


_SEVERITY_RANK = {"BREAKING": 3, "WARNING": 2, "INFO": 1}


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

    # 실패 규칙 중 최고 심각도 → 알림 심각도 (규칙의 severity_override 가 있으면 그 값)
    worst = max(failed, key=lambda f: _SEVERITY_RANK.get(f.get("severity", "INFO"), 1))
    worst_severity = worst.get("severity", "WARNING")

    created: list[LineageAlert] = []
    for rule in rules:
        if not _match_scope(rule, dataset_id, dataset.datasource_id, tag_ids, lineage_ids):
            continue
        config = _json.loads(rule.trigger_config) if rule.trigger_config else {}
        min_sev = _SEVERITY_RANK.get(config.get("min_severity", "INFO"), 1)
        matched = [f for f in failed if _SEVERITY_RANK.get(f.get("severity", "INFO"), 1) >= min_sev]
        if not matched:
            continue

        names = ", ".join(f["rule_name"] for f in matched[:3])
        more = f" 외 {len(matched) - 3}건" if len(matched) > 3 else ""
        alert = LineageAlert(
            alert_type="QUALITY_FAILED",
            severity=rule.severity_override or worst_severity,
            source_dataset_id=dataset_id,
            rule_id=rule.id,
            change_summary=f"품질 검증 실패: {dataset.name} — {names}{more} (점수 {score:.1f}%)",
            change_detail=_json.dumps(
                {"score": score, "failed_rules": matched}, ensure_ascii=False, default=str,
            ),
        )
        created.append(alert)

    if created:
        for alert in created:
            session.add(alert)
        await session.flush()
        for alert in created:
            await _dispatch_notifications(session, alert)
        logger.info("품질 알림 생성됨: dataset_id=%d, count=%d", dataset_id, len(created))
    return created


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


async def _evaluate_trigger(
    session: AsyncSession,
    rule: AlertRule,
    dataset_id: int,
    changes: list[dict],
    change_map: dict[str, dict],
    lineages: list,
) -> list[LineageAlert]:
    """규칙의 트리거 조건을 변경 사항과 대조해 ``LineageAlert`` 객체를 생성한다."""
    config = _json.loads(rule.trigger_config) if rule.trigger_config else {}
    alerts: list[LineageAlert] = []

    if rule.trigger_type == "ANY":
        # 모든 변경에 대해 알림
        severity = rule.severity_override or _auto_severity(changes)
        alerts.append(_create_alert(
            rule, dataset_id, None, None, severity,
            _build_generic_summary(changes),
            _json.dumps(changes, ensure_ascii=False),
        ))

    elif rule.trigger_type == "SCHEMA_CHANGE":
        # 변경 유형(DROP/MODIFY/ADD)으로 필터링
        allowed_types = set(config.get("change_types", ["DROP", "MODIFY", "ADD"]))
        filtered = [c for c in changes if c["type"] in allowed_types]
        if filtered:
            severity = rule.severity_override or _auto_severity(filtered)
            alerts.append(_create_alert(
                rule, dataset_id, None, None, severity,
                _build_generic_summary(filtered),
                _json.dumps(filtered, ensure_ascii=False),
            ))

    elif rule.trigger_type == "COLUMN_WATCH":
        # 특정 컬럼만 감시
        watch_cols = set(config.get("columns", []))
        allowed_types = set(config.get("change_types", ["DROP", "MODIFY", "ADD"]))
        matched = [c for c in changes if c["field"] in watch_cols and c["type"] in allowed_types]
        if matched:
            severity = rule.severity_override or _auto_severity(matched)
            summary_parts = [f"'{c['field']}' {c['type'].lower()}" for c in matched[:3]]
            summary = "; ".join(summary_parts)
            if len(matched) > 3:
                summary += f" (+{len(matched) - 3} more)"
            alerts.append(_create_alert(
                rule, dataset_id, None, None, severity,
                summary,
                _json.dumps(matched, ensure_ascii=False),
            ))

    elif rule.trigger_type == "MAPPING_BROKEN":
        # 매핑된 컬럼이 변경될 때만 발동
        if rule.scope_type == "LINEAGE" and rule.scope_id:
            # 해당 리니지만 확인
            target_lineages = [l for l in lineages if l.id == rule.scope_id]
        else:
            target_lineages = lineages

        for lineage in target_lineages:
            impact_alerts = await _check_mapping_impact(
                session, rule, dataset_id, lineage, change_map,
            )
            alerts.extend(impact_alerts)

    return alerts


async def _check_mapping_impact(
    session: AsyncSession,
    rule: AlertRule,
    dataset_id: int,
    lineage,
    change_map: dict[str, dict],
) -> list[LineageAlert]:
    """특정 리니지 관계에 대해 컬럼 매핑이 변경 사항의 영향을 받는지 확인한다."""
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
            severity = _determine_severity(change)
            impact_items.append({
                "changed_column": mapped_col,
                "mapped_to": other_col,
                "change_type": change["type"],
                "severity": severity,
                "before": change.get("before"),
                "after": change.get("after"),
            })

    if not impact_items:
        return []

    max_sev = max(impact_items, key=lambda x: _SEVERITY_RANK.get(x["severity"], 0))
    severity = rule.severity_override or max_sev["severity"]

    parts = []
    for item in impact_items[:3]:
        if item["change_type"] == "DROP":
            parts.append(f"'{item['changed_column']}' dropped (mapped to {item['mapped_to']})")
        elif item["change_type"] == "MODIFY":
            parts.append(f"'{item['changed_column']}' modified (mapped to {item['mapped_to']})")
    summary = "; ".join(parts)
    if len(impact_items) > 3:
        summary += f" (+{len(impact_items) - 3} more)"

    return [_create_alert(
        rule, dataset_id, affected_id, lineage.id, severity,
        summary, _json.dumps(impact_items, ensure_ascii=False),
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
        change_summary=summary,
        change_detail=detail,
    )


def _determine_severity(change: dict) -> str:
    """변경 유형에 따라 심각도를 산정한다(DROP=BREAKING, 타입 변경=WARNING, 그 외=INFO)."""
    if change["type"] == "DROP":
        return "BREAKING"
    if change["type"] == "MODIFY":
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
    """규칙이 별도 템플릿을 두지 않았을 때 사용할 기본 요약 문자열을 생성한다."""
    added = sum(1 for c in changes if c["type"] == "ADD")
    dropped = sum(1 for c in changes if c["type"] == "DROP")
    modified = sum(1 for c in changes if c["type"] == "MODIFY")
    parts = []
    if dropped:
        parts.append(f"{dropped} column(s) dropped")
    if modified:
        parts.append(f"{modified} column(s) modified")
    if added:
        parts.append(f"{added} column(s) added")
    return "Schema changed: " + ", ".join(parts)


# ---------------------------------------------------------------------------
# Notification Dispatch
# ---------------------------------------------------------------------------

async def _dispatch_notifications(session: AsyncSession, alert: LineageAlert) -> None:
    """알림을 규칙 구독자와 데이터셋 소유자에게 전달한다(IN_APP/WEBHOOK)."""
    rule = None
    if alert.rule_id:
        rule = (await session.execute(
            select(AlertRule).where(AlertRule.id == alert.rule_id)
        )).scalar_one_or_none()

    recipients: set[str] = set()

    # 규칙 구독자
    if rule and rule.subscribers:
        for sub in rule.subscribers.split(","):
            sub = sub.strip()
            if sub:
                recipients.add(sub)

    # Owner 알림
    if not rule or rule.notify_owners == "true":
        for ds_id in (alert.source_dataset_id, alert.affected_dataset_id):
            if ds_id:
                owners = (await session.execute(
                    select(Owner.owner_name).where(Owner.dataset_id == ds_id)
                )).scalars().all()
                recipients.update(owners)

    channels = ["IN_APP"]
    if rule:
        channels = [ch.strip() for ch in rule.channels.split(",")]

    for recipient in recipients:
        for channel in channels:
            notification = AlertNotification(
                alert_id=alert.id,
                channel=channel,
                recipient=recipient,
            )
            session.add(notification)

    # Webhook 전송
    webhook_url = rule.webhook_url if rule else None
    if webhook_url and "WEBHOOK" in channels:
        await _send_webhook(session, alert, webhook_url)

    await session.flush()


async def _send_webhook(session: AsyncSession, alert: LineageAlert, webhook_url: str) -> None:
    """외부 Webhook URL 로 알림 payload 를 POST 전송한다."""
    src_name, src_datasource = "", ""
    if alert.source_dataset_id:
        row = (await session.execute(
            select(Dataset.name, Datasource.type)
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(Dataset.id == alert.source_dataset_id)
        )).first()
        if row:
            src_name, src_datasource = row

    aff_name, aff_datasource = "", ""
    if alert.affected_dataset_id:
        row = (await session.execute(
            select(Dataset.name, Datasource.type)
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(Dataset.id == alert.affected_dataset_id)
        )).first()
        if row:
            aff_name, aff_datasource = row

    # 규칙 이름
    rule_name = None
    if alert.rule_id:
        rule = (await session.execute(
            select(AlertRule.rule_name).where(AlertRule.id == alert.rule_id)
        )).scalar_one_or_none()
        rule_name = rule

    payload = {
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "rule_name": rule_name,
        "source": {"dataset": src_name, "datasource": src_datasource},
        "affected": {"dataset": aff_name, "datasource": aff_datasource},
        "change_summary": alert.change_summary,
        "changes": _json.loads(alert.change_detail) if alert.change_detail else [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            logger.info("Webhook 전송 완료: %s (HTTP %d)", webhook_url, resp.status_code)
    except Exception as e:
        logger.warning("Webhook 전송 실패: %s - %s", webhook_url, e)


# ---------------------------------------------------------------------------
# Alert Rule CRUD
# ---------------------------------------------------------------------------

async def create_rule(session: AsyncSession, data: AlertRuleCreate) -> AlertRuleResponse:
    """알림 규칙 생성. ``scope_type`` / ``trigger_type`` / ``severity_override`` 는 Enum → 문자열로 풀어 저장."""
    rule = AlertRule(
        rule_name=data.rule_name,
        description=data.description,
        scope_type=data.scope_type.value,
        scope_id=data.scope_id,
        trigger_type=data.trigger_type.value,
        trigger_config=data.trigger_config,
        severity_override=data.severity_override.value if data.severity_override else None,
        channels=data.channels,
        notify_owners=data.notify_owners,
        webhook_url=data.webhook_url,
        subscribers=data.subscribers,
        created_by=data.created_by,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    logger.info("알림 규칙 생성됨: id=%d, name=%s, scope=%s/%s", rule.id, rule.rule_name, rule.scope_type, rule.scope_id)
    return await _build_rule_response(session, rule)


async def list_rules(session: AsyncSession) -> list[AlertRuleResponse]:
    """알림 규칙 전체 목록(최신 생성순). 적용 범위 이름과 누적 알림 건수가 같이 채워진다."""
    rules = (await session.execute(
        select(AlertRule).order_by(AlertRule.created_at.desc())
    )).scalars().all()
    return [await _build_rule_response(session, r) for r in rules]


async def get_rule(session: AsyncSession, rule_id: int) -> AlertRule | None:
    """규칙 ORM 객체 단건 조회. 없으면 ``None``."""
    return (await session.execute(
        select(AlertRule).where(AlertRule.id == rule_id)
    )).scalar_one_or_none()


async def update_rule(
    session: AsyncSession, rule_id: int, data: AlertRuleUpdate,
) -> AlertRuleResponse | None:
    """규칙 부분 갱신. Enum 필드는 `.value` 로 풀어 문자열 그대로 저장한다."""
    rule = await get_rule(session, rule_id)
    if not rule:
        return None
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field == "severity_override" and value is not None:
            value = value.value if hasattr(value, "value") else value
        if field == "scope_type" and value is not None:
            value = value.value if hasattr(value, "value") else value
        if field == "trigger_type" and value is not None:
            value = value.value if hasattr(value, "value") else value
        setattr(rule, field, value)
    await session.flush()
    await session.refresh(rule)
    logger.info(
        "알림 규칙 수정됨: id=%d, name=%s, fields=%s",
        rule.id, rule.rule_name, list(changes.keys()),
    )
    return await _build_rule_response(session, rule)


async def delete_rule(session: AsyncSession, rule_id: int) -> bool:
    """알림 규칙 삭제. 이미 발생한 알림은 ``rule_id`` 가 NULL 로 떨어지고 그대로 남는다(``ON DELETE SET NULL``)."""
    rule = await get_rule(session, rule_id)
    if not rule:
        return False
    logger.info("알림 규칙 삭제됨: id=%d, name=%s", rule.id, rule.rule_name)
    await session.delete(rule)
    await session.flush()
    return True


async def _build_rule_response(session: AsyncSession, rule: AlertRule) -> AlertRuleResponse:
    """규칙 응답 객체에 적용 범위 이름과 누적 알림 건수를 채워 반환한다."""
    scope_name = None
    if rule.scope_type == "DATASET" and rule.scope_id:
        ds = (await session.execute(
            select(Dataset.name).where(Dataset.id == rule.scope_id)
        )).scalar_one_or_none()
        scope_name = ds
    elif rule.scope_type == "TAG" and rule.scope_id:
        tag = (await session.execute(
            select(Tag.name).where(Tag.id == rule.scope_id)
        )).scalar_one_or_none()
        scope_name = tag
    elif rule.scope_type == "LINEAGE" and rule.scope_id:
        row = (await session.execute(
            select(Dataset.name)
            .join(DatasetLineage, DatasetLineage.source_dataset_id == Dataset.id)
            .where(DatasetLineage.id == rule.scope_id)
        )).scalar_one_or_none()
        if row:
            tgt = (await session.execute(
                select(Dataset.name)
                .join(DatasetLineage, DatasetLineage.target_dataset_id == Dataset.id)
                .where(DatasetLineage.id == rule.scope_id)
            )).scalar_one_or_none()
            scope_name = f"{row} → {tgt}" if tgt else row
    elif rule.scope_type == "DATASOURCE" and rule.scope_id:
        p = (await session.execute(
            select(Datasource.name).where(Datasource.id == rule.scope_id)
        )).scalar_one_or_none()
        scope_name = p

    # 이 규칙으로 생성된 알림 수 집계
    alert_count = (await session.execute(
        select(func.count(LineageAlert.id)).where(LineageAlert.rule_id == rule.id)
    )).scalar() or 0

    return AlertRuleResponse(
        id=rule.id,
        rule_name=rule.rule_name,
        description=rule.description,
        scope_type=rule.scope_type,
        scope_id=rule.scope_id,
        scope_name=scope_name,
        trigger_type=rule.trigger_type,
        trigger_config=rule.trigger_config or "{}",
        severity_override=rule.severity_override,
        channels=rule.channels,
        notify_owners=rule.notify_owners,
        webhook_url=rule.webhook_url,
        subscribers=rule.subscribers,
        is_active=rule.is_active,
        created_by=rule.created_by,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
        alert_count=alert_count,
    )


# ---------------------------------------------------------------------------
# Alert CRUD
# ---------------------------------------------------------------------------

async def list_alerts(
    session: AsyncSession,
    status: str | None = None,
    severity: str | None = None,
    dataset_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedAlerts:
    """알림 목록을 필터(상태·심각도·관련 데이터셋) 와 페이징으로 조회한다."""
    base = select(LineageAlert)
    count_base = select(func.count(LineageAlert.id))

    if status:
        base = base.where(LineageAlert.status == status)
        count_base = count_base.where(LineageAlert.status == status)
    if severity:
        base = base.where(LineageAlert.severity == severity)
        count_base = count_base.where(LineageAlert.severity == severity)
    if dataset_id:
        base = base.where(or_(
            LineageAlert.source_dataset_id == dataset_id,
            LineageAlert.affected_dataset_id == dataset_id,
        ))
        count_base = count_base.where(or_(
            LineageAlert.source_dataset_id == dataset_id,
            LineageAlert.affected_dataset_id == dataset_id,
        ))

    total = (await session.execute(count_base)).scalar() or 0
    offset = (page - 1) * page_size
    alerts = (await session.execute(
        base.order_by(LineageAlert.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all()

    items = [await _build_alert_response(session, a) for a in alerts]
    return PaginatedAlerts(items=items, total=total, page=page, page_size=page_size)


async def get_alert(session: AsyncSession, alert_id: int) -> LineageAlert | None:
    """알림 ORM 객체 단건 조회. 없으면 ``None``."""
    return (await session.execute(
        select(LineageAlert).where(LineageAlert.id == alert_id)
    )).scalar_one_or_none()


async def update_alert_status(
    session: AsyncSession, alert_id: int, data: AlertUpdateStatus,
) -> AlertResponse | None:
    """알림 상태 전이. RESOLVED/DISMISSED 로 가는 경우 처리자·시각도 같이 기록한다."""
    alert = await get_alert(session, alert_id)
    if not alert:
        return None
    prev_status = alert.status
    new_status = data.status.value
    alert.status = new_status
    if new_status in ("RESOLVED", "DISMISSED"):
        alert.resolved_by = data.resolved_by
        alert.resolved_at = datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(alert)
    logger.info(
        "알림 상태 변경됨: id=%d, %s -> %s, by=%s",
        alert.id, prev_status, new_status, data.resolved_by,
    )
    return await _build_alert_response(session, alert)


async def get_alert_summary(session: AsyncSession) -> AlertSummary:
    """미해결(OPEN) 알림을 심각도별로 집계해 사이드바 벨 배지에 쓰는 요약을 만든다."""
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


async def _build_alert_response(session: AsyncSession, alert: LineageAlert) -> AlertResponse:
    """알림 응답 DTO 에 출처/영향 데이터셋 이름과 규칙 이름을 채워 돌려준다."""
    src_name = src_datasource = None
    if alert.source_dataset_id:
        row = (await session.execute(
            select(Dataset.name, Datasource.type)
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(Dataset.id == alert.source_dataset_id)
        )).first()
        if row:
            src_name, src_datasource = row

    aff_name = aff_datasource = None
    if alert.affected_dataset_id:
        row = (await session.execute(
            select(Dataset.name, Datasource.type)
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(Dataset.id == alert.affected_dataset_id)
        )).first()
        if row:
            aff_name, aff_datasource = row

    rule_name = None
    if alert.rule_id:
        rule_name = (await session.execute(
            select(AlertRule.rule_name).where(AlertRule.id == alert.rule_id)
        )).scalar_one_or_none()

    return AlertResponse(
        id=alert.id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        source_dataset_id=alert.source_dataset_id,
        source_dataset_name=src_name,
        source_datasource_type=src_datasource,
        affected_dataset_id=alert.affected_dataset_id,
        affected_dataset_name=aff_name,
        affected_datasource_type=aff_datasource,
        lineage_id=alert.lineage_id,
        rule_id=alert.rule_id,
        rule_name=rule_name,
        change_summary=alert.change_summary,
        change_detail=alert.change_detail,
        status=alert.status,
        resolved_by=alert.resolved_by,
        resolved_at=alert.resolved_at,
        created_at=alert.created_at,
    )
