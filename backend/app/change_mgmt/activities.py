# SPDX-License-Identifier: Apache-2.0
"""Temporal 액티비티.

워크플로우(workflow.py) 가 호출하는 비결정적(non-deterministic) 작업.
DB I/O, 외부 HTTP 호출, 통지 발송 등은 반드시 액티비티로 분리한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from temporalio import activity

from app.catalog.models import (
    Dataset,
    DatasetColumnMapping,
    DatasetLineage,
    DatasetSchema,
)
from app.catalog.service import apply_schema_fields, save_schema_snapshot
from app.change_mgmt import models, schemas
from app.core.database import async_session as async_session_factory

logger = logging.getLogger(__name__)


def _log_missing_cr(activity_name: str, cr_id: int) -> None:
    """CR 이 없을 때 — 데이터셋·데이터 소스 삭제로 연쇄 삭제된 경우(IT CHG-T26).

    예전에는 ValueError 로 재시도(5회) 끝에 워크플로우가 실패했다. 삭제 시 워크플로우를 강제 종료하지만
    (``service.cancel_workflows_best_effort``) Temporal 이 내려가 있었거나 종료 전에 실행된
    액티비티도 있으니, 액티비티는 없는 CR 을 오류 없이 건너뛴다.
    """
    logger.warning("%s: CR 이 없음(삭제됨) — 건너뜀 cr_id=%s", activity_name, cr_id)


# ---------------------------------------------------------------------------
# 액티비티 입출력 DTO (dataclass — Temporal 직렬화 친화)
# ---------------------------------------------------------------------------

@dataclass
class ImpactReport:
    """analyze_impact 결과 — CR.impact_report 에 JSON 으로 저장되고 워크플로우가 결재선 결정에 쓴다."""
    change_type: str
    changed_columns: list[str]              # 변경되는 컬럼 (upsert + drop)
    affected_downstream_count: int          # 다운스트림 데이터셋 수 (다단계)
    affected_dataset_ids: list[int]
    affected_columns: list[str]             # 컬럼 수준 영향 ("<dataset_id>.<column>")
    affected_consumer_count: int
    affected_consumers: list[int]
    mission_critical_consumers: int
    risk_score: float                       # 0.0 ~ 1.0
    summary: str


@dataclass
class NotifyInput:
    """send_notifications 입력 — CR id, 통지 단계, 발송 채널 목록."""
    cr_id: int
    stage: str                       # NotificationStage 값
    channels: list[str]              # ["EMAIL", "SLACK", ...]


@dataclass
class ApplyInput:
    """apply_change 입력."""
    cr_id: int


# ---------------------------------------------------------------------------
# 영향 분석
# ---------------------------------------------------------------------------

def _parse_schema_after(raw: str | None) -> tuple[list[dict], list[str]]:
    """CR 의 ``schema_after`` JSON 을 (upsert 필드 목록, drop 컬럼 목록) 으로 해석.

    지원 형식 (부분 변경 명세):
      1. 구조화: ``{"fields": [{"field_path": "...", "field_type": "...", ...}],
                    "drop": ["old_col"]}``
      2. 필드 배열: ``[{"field_path": "...", "field_type": "..."}, ...]``
      3. 평면 맵: ``{"email": "VARCHAR(255)"}``  (키=컬럼, 값=타입)
      4. 속성 맵: ``{"email": {"field_type": "VARCHAR(255)", "nullable": "false"}}``

    각 upsert 는 최소 ``field_path`` 와 ``field_type`` 를 갖도록 정규화한다.
    """
    if not raw:
        return [], []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # JSON 이 아니면 변경 컬럼을 특정할 수 없다 — 영향 분석은 컬럼 0개로 진행.
        logger.warning("schema_after 가 JSON 이 아님 — 컬럼 단위 분석 생략")
        return [], []

    def _normalize(field_path: str, value) -> dict:
        # 평면 맵 값 → upsert dict. 속성 맵의 "type" 키는 field_type 으로 옮긴다(없으면 STRING).
        if isinstance(value, dict):
            d = dict(value)
            d["field_path"] = field_path
            d.setdefault("field_type", d.pop("type", "STRING"))
            return d
        return {"field_path": field_path, "field_type": str(value)}

    # 1. 구조화 형식 — 삭제만 있는 명세(``{"drop": ["col"]}``)도 포함. 예전에는 "fields" 가 없으면
    #    평면 맵으로 읽어 "drop" 이라는 컬럼을 추가하려 했다(IT CHG-T11). "drop" 값이 목록일 때만
    #    구조화로 본다 — 평면 맵의 "drop" 컬럼(``{"drop": "INT"}``)과 구분.
    if isinstance(data, dict) and ("fields" in data or isinstance(data.get("drop"), list)):
        upserts = []
        for f in data.get("fields", []):
            if isinstance(f, dict) and f.get("field_path"):
                f.setdefault("field_type", "STRING")
                upserts.append(f)
        drops = [str(c) for c in data.get("drop", [])]
        return upserts, drops

    # 2. 필드 배열
    if isinstance(data, list):
        upserts = []
        for f in data:
            if isinstance(f, dict) and f.get("field_path"):
                f.setdefault("field_type", "STRING")
                upserts.append(f)
        return upserts, []

    # 3 / 4. 평면 맵
    if isinstance(data, dict):
        return [_normalize(k, v) for k, v in data.items()], []

    return [], []


# 변경 유형별 기본 위험도
_BASE_RISK = {"BREAKING": 0.9, "NON_BREAKING": 0.4, "ADDITIVE": 0.2, "COSMETIC": 0.05}


async def _downstream_dataset_ids(
    session, root_id: int, max_hops: int = 5
) -> list[int]:
    """리니지 그래프를 따라 다운스트림(영향받는) 데이터셋을 다단계로 수집한다.

    A → B → C 에서 A 변경 시 B, C 모두 영향. 순환 방지를 위해 visited 추적.
    BFS 로 최대 ``max_hops`` 단계까지만 따라가며, 루트 자신은 제외한 정렬된 id 목록을 반환한다.
    """
    visited: set[int] = set()
    frontier: set[int] = {root_id}
    for _ in range(max_hops):
        if not frontier:
            break
        rows = (
            await session.execute(
                select(DatasetLineage.target_dataset_id).where(
                    DatasetLineage.source_dataset_id.in_(frontier)
                )
            )
        ).scalars().all()
        nxt = {r for r in rows if r not in visited and r != root_id}
        visited |= nxt
        frontier = nxt
    return sorted(visited)


async def _affected_downstream_columns(
    session, root_id: int, changed_columns: set[str]
) -> list[str]:
    """변경 컬럼을 참조하는 직속(1단계) 다운스트림 컬럼을 컬럼 매핑에서 찾는다.

    반환 형식: ``"<target_dataset_id>.<target_column>"``.
    """
    if not changed_columns:
        return []
    edges = (
        await session.execute(
            select(DatasetLineage).where(DatasetLineage.source_dataset_id == root_id)
        )
    ).scalars().all()
    affected: list[str] = []
    for edge in edges:
        maps = (
            await session.execute(
                select(DatasetColumnMapping).where(
                    DatasetColumnMapping.dataset_lineage_id == edge.id,
                    DatasetColumnMapping.source_column.in_(changed_columns),
                )
            )
        ).scalars().all()
        for m in maps:
            affected.append(f"{edge.target_dataset_id}.{m.target_column}")
    return sorted(set(affected))


@activity.defn(name="analyze_impact")
async def analyze_impact(cr_id: int) -> ImpactReport:
    """리니지 + 소비자 등록부 기준으로 영향 범위와 위험도를 산정한다.

    - 다운스트림 데이터셋: 리니지 그래프 다단계 traversal (간접 영향 포함)
    - 컬럼 수준 영향: 변경 컬럼을 참조하는 다운스트림 컬럼 매핑
    - 위험도: 변경 유형(base) + reach(다운스트림/소비자 규모) + 소비자 중요도 가중
    """
    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, cr_id)
        if cr is None:
            _log_missing_cr("analyze_impact", cr_id)
            return ImpactReport(
                change_type="", changed_columns=[], affected_downstream_count=0,
                affected_dataset_ids=[], affected_columns=[], affected_consumer_count=0,
                affected_consumers=[], mission_critical_consumers=0, risk_score=0.0,
                summary="CR 이 삭제됨 — 영향 분석 생략",
            )

        # 변경 컬럼 추출 (schema_after 의 upsert + drop 키)
        upserts, drops = _parse_schema_after(cr.schema_after)
        changed_columns = {f["field_path"] for f in upserts} | set(drops)

        consumers = (
            await session.execute(
                select(models.Consumer).where(models.Consumer.dataset_id == cr.dataset_id)
            )
        ).scalars().all()
        mission_critical = sum(1 for c in consumers if c.criticality == "MISSION_CRITICAL")
        important = sum(1 for c in consumers if c.criticality == "IMPORTANT")

        downstream_ids = await _downstream_dataset_ids(session, cr.dataset_id)
        affected_columns = await _affected_downstream_columns(
            session, cr.dataset_id, changed_columns
        )

        # 위험도 산정: 변경 유형 기본값 + 0.1 × 규모(다운스트림+소비자, 20 이상이면 1.0)
        #             + 중요도 가중(MISSION_CRITICAL 0.3 / IMPORTANT 0.15), 최대 1.0
        base = _BASE_RISK.get(cr.change_type, 0.4)
        reach = min(len(downstream_ids) + len(consumers), 20) / 20.0
        crit_bump = 0.3 if mission_critical else (0.15 if important else 0.0)
        risk = round(min(1.0, base + 0.1 * reach + crit_bump), 2)

        report = ImpactReport(
            change_type=cr.change_type,
            changed_columns=sorted(changed_columns),
            affected_downstream_count=len(downstream_ids),
            affected_dataset_ids=downstream_ids,
            affected_columns=affected_columns,
            affected_consumer_count=len(consumers),
            affected_consumers=[c.id for c in consumers],
            mission_critical_consumers=mission_critical,
            risk_score=risk,
            summary=(
                f"다운스트림 {len(downstream_ids)}개 데이터셋, "
                f"컬럼 영향 {len(affected_columns)}건, "
                f"소비자 {len(consumers)}명(중대 {mission_critical}) "
                f"— 위험도 {risk:.2f}"
            ),
        )

        cr.impact_report = json.dumps(report.__dict__, ensure_ascii=False)
        await session.commit()
        logger.info("영향 분석 완료: cr=%s %s", cr.cr_code, report.summary)
        return report


# ---------------------------------------------------------------------------
# 결재 상태 기록
# ---------------------------------------------------------------------------

@activity.defn(name="record_decision")
async def record_decision(
    cr_id: int, step_order: int, decision: str, comment: str | None, decided_by: str,
    delegated_to: str | None = None,
) -> None:
    """결재 단계의 결정 결과를 DB에 기록한다.

    위임(DELEGATED)이면 요청의 대결자(``delegated_to``)를 기록한다 — 예전에는 결정자 본인을
    저장했다(IT CHG-T14). 대결자 없이 온 예전 Signal 은 비워 둔다.
    """
    async with async_session_factory() as session:
        step = (
            await session.execute(
                select(models.ApprovalStep).where(
                    models.ApprovalStep.cr_id == cr_id,
                    models.ApprovalStep.step_order == step_order,
                )
            )
        ).scalar_one_or_none()
        if step is None:
            # CR 이 연쇄 삭제되면 단계도 없다 — 재시도해도 소용없으니 건너뛴다
            _log_missing_cr("record_decision", cr_id)
            return

        step.decision = decision
        step.comment = comment
        step.decided_at = datetime.now(timezone.utc)
        if decision == schemas.Decision.DELEGATED.value:
            step.delegated_to = delegated_to
        await session.commit()
        logger.info("결재 결정 기록: cr_id=%s step=%s decision=%s by=%s",
                    cr_id, step_order, decision, decided_by)


@activity.defn(name="update_cr_status")
async def update_cr_status(cr_id: int, status: str) -> None:
    """CR 상태를 전이한다. APPLIED 로 바뀌면 applied_at 도 기록한다. CR 이 없으면 건너뛴다."""
    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, cr_id)
        if cr is None:
            _log_missing_cr("update_cr_status", cr_id)
            return
        prev = cr.status
        cr.status = status
        if status == schemas.CRStatus.APPLIED.value:
            cr.applied_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("CR 상태 전이: cr=%s %s -> %s", cr.cr_code, prev, status)


# ---------------------------------------------------------------------------
# 통지 (Notification)
# ---------------------------------------------------------------------------

def _build_notification_payload(cr, dataset_name: str, stage: str) -> dict:
    """소비자에게 보낼 통지 내용(템플릿)을 구성한다 (Before/After·일정·대응 가이드)."""
    return {
        "event": "schema_change_notification",
        "cr_code": cr.cr_code,
        "stage": stage,
        "dataset": dataset_name,
        "title": cr.title,
        "change_type": cr.change_type,
        "priority": cr.priority,
        "scheduled_at": cr.scheduled_at.isoformat() if cr.scheduled_at else None,
        "schema_before": _safe_json(cr.schema_before),
        "schema_after": _safe_json(cr.schema_after),
        "rollback_plan": cr.rollback_plan,
        "business_justification": cr.business_justification,
        "impact": _safe_json(cr.impact_report),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _safe_json(raw: str | None):
    """JSON 문자열이면 파싱한 값, 아니면 원문 그대로(빈 값은 None)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


STAGE_LABELS = {"T_MINUS_7": "적용 7일 전", "T_MINUS_1": "적용 1일 전", "APPLIED": "적용 완료"}

# 소비자 통지 채널 — 상신 API 가 받는 값. SMS·IN_APP 은 예전 기본값 호환으로만 받고 보내지 않는다(SKIPPED).
CONSUMER_CHANNELS = ("WEBHOOK", "EMAIL", "SLACK", "SMS", "IN_APP")
DEFAULT_NOTIFY_CHANNELS = ["WEBHOOK", "EMAIL"]


def _notification_text(payload: dict) -> tuple[str, str]:
    """메일·메신저용 (제목, 본문) — webhook 은 payload JSON 을 그대로 보낸다."""
    stage = STAGE_LABELS.get(payload.get("stage") or "", payload.get("stage") or "")
    subject = f"[변경요청 {payload['cr_code']}] {payload['dataset']} 스키마 변경 통지 ({stage})"
    lines = [
        f"데이터셋 '{payload['dataset']}' 의 스키마 변경 요청 {payload['cr_code']} '{payload['title']}' — {stage}",
        "",
        f"- 변경 유형: {payload.get('change_type') or '-'}",
        f"- 우선순위: {payload.get('priority') or '-'}",
        f"- 적용 예정: {payload.get('scheduled_at') or '즉시'}",
    ]
    if payload.get("rollback_plan"):
        lines.append(f"- 롤백 계획: {payload['rollback_plan']}")
    lines += ["", "카탈로그의 변경 요청 상세에서 변경 내용을 확인하고 수신 확인(ACK)해 주세요."]
    return subject, "\n".join(lines)


async def _deliver(session, channel: str, consumer, payload: dict) -> tuple[str, str | None]:
    """단일 채널로 소비자에게 통지를 전달하고 (status, error) 를 반환한다 (DEF-005).

    - WEBHOOK : ``consumer.webhook_url`` 로 payload JSON POST (성공 DELIVERED / 실패 FAILED)
    - EMAIL   : ``consumer.contact_emails`` 로 메일 발송(설정 > 메일) — 성공 SENT / 실패 FAILED
    - SLACK   : 메신저 설정(설정 > 알림, Slack·Mattermost)의 webhook 으로 ``consumer.slack_channel`` 에 발송
    - SMS · IN_APP : 소비자에게 보낼 수단이 없어 보내지 않는다.
    받는 곳이 없거나 발송 설정이 꺼져 있으면 보내지 않고 ``SKIPPED`` 와 사유를 남긴다(예전에는 보내지 않고 SENT).
    """
    if channel == "WEBHOOK":
        if not consumer.webhook_url:
            return "SKIPPED", "소비자에 webhook URL 이 등록되어 있지 않습니다"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(consumer.webhook_url, json=payload)
            if resp.status_code >= 400:
                return "FAILED", f"HTTP {resp.status_code}"
            return "DELIVERED", None
        except Exception as e:  # 네트워크 오류 등
            return "FAILED", str(e) or type(e).__name__

    if channel == "EMAIL":
        from app.settings.mail import load_mail_config, send_mail

        to = [a.strip() for a in (consumer.contact_emails or "").split(",") if a.strip()]
        if not to:
            return "SKIPPED", "소비자에 담당자 이메일이 등록되어 있지 않습니다"
        cfg = await load_mail_config(session)
        if not cfg.enabled:
            return "SKIPPED", "메일 발송이 꺼져 있습니다(설정 > 메일)"
        subject, body = _notification_text(payload)
        try:
            await send_mail(cfg, subject, body, to=to, raise_errors=True)
        except Exception as e:  # noqa: BLE001 — 발송 실패는 이력으로 남긴다
            return "FAILED", str(e) or type(e).__name__
        return "SENT", None

    if channel == "SLACK":
        from app.settings.notify import load_notify_config, send_notify

        if not consumer.slack_channel:
            return "SKIPPED", "소비자에 Slack 채널이 등록되어 있지 않습니다"
        cfg = await load_notify_config(session)
        if not cfg.enabled:
            return "SKIPPED", "메신저 알림이 꺼져 있습니다(설정 > 알림)"
        subject, body = _notification_text(payload)
        try:
            await send_notify(cfg, f"*{subject}*\n{body}", channel=consumer.slack_channel, raise_errors=True)
        except Exception as e:  # noqa: BLE001
            return "FAILED", str(e) or type(e).__name__
        return "SENT", None

    if channel == "SMS":
        return "SKIPPED", "SMS 발송 수단이 없습니다"
    if channel == "IN_APP":
        return "SKIPPED", "소비자는 사용자 계정이 아니라 인앱 알림을 받을 수 없습니다"
    return "SKIPPED", f"지원하지 않는 채널입니다: {channel}"


@activity.defn(name="send_notifications")
async def send_notifications(payload: NotifyInput) -> int:
    """등록된 소비자 전원에게 지정 채널로 통지를 발송하고 로그를 남긴다.

    채널별 전달 규칙은 ``_deliver`` 참고. 보내지 않은 채널은 ``SKIPPED`` 와 사유로 남는다.
    """
    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, payload.cr_id)
        if cr is None:
            _log_missing_cr("send_notifications", payload.cr_id)
            return 0

        dataset = await session.get(Dataset, cr.dataset_id)
        dataset_name = dataset.name if dataset else str(cr.dataset_id)

        consumers = (
            await session.execute(
                select(models.Consumer).where(models.Consumer.dataset_id == cr.dataset_id)
            )
        ).scalars().all()

        content = _build_notification_payload(cr, dataset_name, payload.stage)
        logger.debug("통지 발송 시작: cr=%s stage=%s consumers=%d channels=%s",
                     cr.cr_code, payload.stage, len(consumers), payload.channels)

        count = 0
        failed = skipped = 0  # 완료 요약 로그용
        for consumer in consumers:
            for channel in payload.channels:
                status, error = await _deliver(session, channel, consumer, content)
                log = models.NotificationLog(
                    cr_id=cr.id,
                    consumer_id=consumer.id,
                    channel=channel,
                    stage=payload.stage,
                    status=status,
                    sent_at=datetime.now(timezone.utc) if status in ("SENT", "DELIVERED") else None,
                    error=error,
                )
                session.add(log)
                count += 1
                # 루프 안 건별 로그 — 실패는 WARNING, 성공은 DEBUG(완료 요약은 루프 뒤 INFO)
                if status == "FAILED":
                    failed += 1
                    logger.warning(
                        "통지 실패: cr=%s consumer=%s channel=%s stage=%s error=%s",
                        cr.cr_code, consumer.consumer_name, channel, payload.stage, error,
                    )
                else:
                    skipped += status == "SKIPPED"
                    logger.debug(
                        "통지 %s: cr=%s consumer=%s channel=%s stage=%s",
                        status, cr.cr_code, consumer.consumer_name, channel, payload.stage,
                    )
        await session.commit()
        logger.info("통지 발송 완료: cr=%s stage=%s total=%d failed=%d skipped=%d",
                    cr.cr_code, payload.stage, count, failed, skipped)
        return count


async def _ack_summary(session, cr_id: int) -> dict:
    """소비자 단위 ACK 현황 — 통지를 받은 소비자 중 한 통지라도 ACK 했으면 확인한 것으로 본다.

    통지 로그는 소비자 × 채널 × 단계로 여러 행이므로 행 수가 아니라 소비자 수로 센다(참조자·에스컬레이션 행 제외).
    """
    rows = (await session.execute(
        select(models.NotificationLog.consumer_id, models.NotificationLog.acked_at, models.Consumer.consumer_name)
        .join(models.Consumer, models.Consumer.id == models.NotificationLog.consumer_id)
        .where(models.NotificationLog.cr_id == cr_id)
    )).all()
    names: dict[int, str] = {}
    acked: set[int] = set()
    for consumer_id, acked_at, name in rows:
        names[consumer_id] = name
        if acked_at is not None:
            acked.add(consumer_id)
    pending = sorted((names[c] for c in names if c not in acked), key=str)
    return {"total": len(names), "acked": len(acked), "pending": len(pending), "pending_consumers": pending}


@activity.defn(name="wait_acks_summary")
async def wait_acks_summary(cr_id: int) -> dict:
    """소비자 단위 ACK 현황 ``{"total", "acked", "pending", "pending_consumers"}`` (DEF-004)."""
    async with async_session_factory() as session:
        summary = await _ack_summary(session, cr_id)
    logger.debug("ACK 집계: cr_id=%s %s", cr_id, summary)
    return summary


ESCALATION_STAGE = "ESCALATION"


@activity.defn(name="escalate_unacked_consumers")
async def escalate_unacked_consumers(cr_id: int) -> dict:
    """적용 직전 미확인 소비자 에스컬레이션 (DEF-004) — 적용은 막지 않는다.

    통지를 받고 ACK 하지 않은 소비자가 있으면 요청자·결재자에게 변경 관리 통지 채널(설정 > 변경관리
    ``change_notify_channel``, 참조자 통지와 같음)로 알리고, 받는 사람마다 ``stage=ESCALATION`` 통지 이력을
    남긴다(상세 화면 "알림 발송 이력"). 통지가 꺼져 있으면 발송하지 않고 이력만 ``SKIPPED`` 로 남긴다.
    반환값은 소비자 단위 ACK 현황.
    """
    from app.settings.mail import send_mail_db
    from app.settings.notify import send_notify_db
    from app.settings.service import get_config_by_category

    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, cr_id)
        if cr is None:
            _log_missing_cr("escalate_unacked_consumers", cr_id)
            return {"total": 0, "acked": 0, "pending": 0, "pending_consumers": []}
        summary = await _ack_summary(session, cr_id)
        if not summary["pending"]:
            logger.info("미확인 소비자 없음 — 에스컬레이션 생략: cr=%s total=%d", cr.cr_code, summary["total"])
            return summary

        approvers = (await session.execute(
            select(models.ApprovalStep.approver).where(models.ApprovalStep.cr_id == cr_id)
            .order_by(models.ApprovalStep.step_order)
        )).scalars().all()
        recipients = list(dict.fromkeys([cr.requested_by, *approvers]))  # 순서 유지 중복 제거

        cfg = await get_config_by_category(session, "change")
        enabled = cfg.get("change_notify_enabled", "true").lower() in ("true", "1", "yes")
        channel = (cfg.get("change_notify_channel") or "email").upper()
        names = ", ".join(summary["pending_consumers"])
        subject = f"[변경요청 {cr.cr_code}] 미확인 소비자 {summary['pending']}곳 — 예정대로 적용합니다"
        body = (
            f"변경 요청 {cr.cr_code} '{cr.title}' 을(를) 적용합니다. 아래 소비자가 통지를 확인(ACK)하지 않았습니다.\n\n"
            f"- 미확인 소비자({summary['pending']}/{summary['total']}): {names}\n"
            f"- 적용 예정: {cr.scheduled_at.isoformat() if cr.scheduled_at else '즉시'}\n"
            f"- 요청자: {cr.requested_by}\n\n소비자 담당자에게 직접 확인해 주세요.\n"
        )

        def log(recipient: str, status: str, error: str | None) -> None:
            session.add(models.NotificationLog(
                cr_id=cr.id, recipient=recipient, channel=channel, stage=ESCALATION_STAGE, status=status,
                sent_at=datetime.now(timezone.utc) if status == "SENT" else None, error=error,
            ))

        if not enabled:
            for r in recipients:
                log(r, "SKIPPED", "변경 통지가 꺼져 있습니다(설정 > 변경관리)")
        elif channel == "EMAIL":
            from app.usermgr.models import ArgusUser
            emails = dict((await session.execute(
                select(ArgusUser.username, ArgusUser.email).where(ArgusUser.username.in_(recipients))
            )).all())
            for r in recipients:
                if not emails.get(r):
                    log(r, "FAILED", "사용자 이메일을 찾을 수 없습니다")
                    continue
                try:
                    ok, err = await send_mail_db(session, subject, body, to=[emails[r]]), None
                except Exception as e:  # noqa: BLE001 — 발송 실패는 이력으로 남기고 적용은 계속
                    ok, err = False, str(e)
                log(r, "SENT" if ok else "FAILED", None if ok else (err or "메일 발송 실패(메일 설정 확인)"))
        else:
            try:
                ok, err = await send_notify_db(session, f"*{subject}*\n받는 사람: {', '.join(recipients)}\n{body}"), None
            except Exception as e:  # noqa: BLE001
                ok, err = False, str(e)
            for r in recipients:
                log(r, "SENT" if ok else "FAILED", None if ok else (err or "메신저 발송 실패(알림 설정 확인)"))
        await session.commit()
        logger.warning("미확인 소비자 에스컬레이션: cr=%s pending=%d/%d recipients=%s channel=%s",
                       cr.cr_code, summary["pending"], summary["total"], recipients,
                       channel if enabled else "off")
        return summary


# ---------------------------------------------------------------------------
# 변경 적용
# ---------------------------------------------------------------------------

def _merge_schema(current: list[dict], upserts: list[dict], drops: list[str]) -> list[dict]:
    """현재 스키마에 CR 의 변경(upsert/drop)을 병합해 완전한 신 스키마를 만든다.

    - upsert: 기존 컬럼이면 속성 갱신, 신규면 추가
    - drop  : 해당 컬럼 제거
    ordinal 은 기존 순서를 유지하고 신규 컬럼은 뒤에 append.
    """
    by_path: dict[str, dict] = {f["field_path"]: dict(f) for f in current}
    max_ordinal = max((f.get("ordinal", i) for i, f in enumerate(current)), default=-1)

    for up in upserts:
        path = up["field_path"]
        if path in by_path:
            by_path[path].update({k: v for k, v in up.items() if k != "field_path"})
        else:
            max_ordinal += 1
            new_field = {"ordinal": max_ordinal}
            new_field.update(up)
            by_path[path] = new_field

    for d in drops:
        by_path.pop(d, None)

    return sorted(by_path.values(), key=lambda f: f.get("ordinal", 0))


def _field_dict(f) -> dict:
    """``DatasetSchema`` 행 → 병합·적용용 필드 dict (``apply_schema_fields`` 입력 형식)."""
    return {
        "field_path": f.field_path,
        "field_type": f.field_type,
        "native_type": f.native_type,
        "description": f.description,
        "nullable": f.nullable,
        "is_primary_key": f.is_primary_key,
        "is_unique": f.is_unique,
        "is_indexed": f.is_indexed,
        "is_partition_key": f.is_partition_key,
        "is_distribution_key": f.is_distribution_key,
        "display_name": f.display_name,
        "ordinal": f.ordinal,
    }


async def apply_schema_spec(session, dataset_id: int, upserts: list[dict], drops: list[str],
                            label: str) -> list[dict]:
    """데이터셋 현재 스키마에 부분 변경(upsert/drop)을 병합해 적용하고, 적용 직전 필드 목록을 돌려준다.

    1. 현재 ``catalog_dataset_schemas`` 필드를 읽어 변경을 병합
    2. 변경 이력 스냅샷 저장(``save_schema_snapshot``) — alert rule 평가까지 연동됨
    3. 기존 필드를 신 스키마로 갱신(``apply_schema_fields`` — 행 id·표준 용어 매핑·pii_type 보존)

    CR 적용(``apply_change``)과 롤백(``service.rollback_cr``)이 같이 쓴다. flush 만, 커밋은 호출 측.
    """
    from types import SimpleNamespace

    old_fields = (
        await session.execute(select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id))
    ).scalars().all()
    current = [_field_dict(f) for f in old_fields]
    merged = _merge_schema(current, upserts, drops)

    # 변경 이력 스냅샷 (old ORM vs 신 스키마). from_sync=True 경로가 col.name 등
    # attribute 접근을 쓰므로 SimpleNamespace 로 변환해 넘긴다.
    new_columns_obj = [
        SimpleNamespace(
            name=f["field_path"],
            data_type=f["field_type"],
            native_type=f.get("native_type") or "",
            nullable=str(f.get("nullable", "true")).lower() == "true",
            is_primary_key=str(f.get("is_primary_key", "false")).lower() == "true",
            is_unique=str(f.get("is_unique", "false")).lower() == "true",
            is_indexed=str(f.get("is_indexed", "false")).lower() == "true",
            is_partition_key=str(f.get("is_partition_key", "false")).lower() == "true",
            is_distribution_key=str(f.get("is_distribution_key", "false")).lower() == "true",
            ordinal=int(f.get("ordinal", idx)),
        )
        for idx, f in enumerate(merged)
    ]
    try:
        await save_schema_snapshot(session, dataset_id, old_fields, new_columns_obj, from_sync=True)
    except Exception as e:
        logger.warning("%s: 스냅샷 저장 실패 dataset=%s: %s", label, dataset_id, e)

    # field_path 기준 diff-upsert(직접 편집·동기화와 같은 경로).
    # 전체 삭제 후 재삽입하면 행 id 가 바뀌어 표준 용어 매핑(schema_id CASCADE)과 pii_type 이 사라진다(DEF-049).
    await apply_schema_fields(session, dataset_id, list(old_fields), merged)
    await session.flush()
    return current


def _before_fields(raw: str | None) -> dict[str, dict]:
    """``schema_before`` → {컬럼: 필드 dict}. 적용 때 저장한 전체 형식(field_path…)과
    생성 때 자동 채운 예전 형식(name·type·nullable bool·primary_key)을 모두 읽는다."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    items = data.get("fields", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    out: dict[str, dict] = {}
    for f in items:
        if not isinstance(f, dict):
            continue
        path = f.get("field_path") or f.get("name")
        if not path:
            continue
        d = {k: v for k, v in f.items() if k not in ("name", "type", "primary_key")}
        d["field_path"] = path
        d["field_type"] = f.get("field_type") or f.get("type") or "STRING"
        if isinstance(d.get("nullable"), bool):
            d["nullable"] = "true" if d["nullable"] else "false"
        if "is_primary_key" not in d and isinstance(f.get("primary_key"), bool):
            d["is_primary_key"] = "true" if f["primary_key"] else "false"
        out[str(path)] = d
    return out


def reverse_schema_spec(schema_before: str | None, schema_after: str | None) -> tuple[list[dict], list[str]]:
    """적용한 변경을 되돌리는 부분 변경 명세 — CR 이 건드린 컬럼만 적용 전 상태로.

    - 바꾼 컬럼(적용 전에 있던 것): 적용 전 속성으로 upsert
    - 추가한 컬럼(적용 전에 없던 것): drop
    - 삭제한 컬럼: 적용 전 속성으로 다시 추가

    삭제한 컬럼의 적용 전 정보가 없으면 되돌릴 수 없어 ``ValueError``.
    """
    upserts, drops = _parse_schema_after(schema_after)
    before = _before_fields(schema_before)
    rev_upserts: list[dict] = []
    rev_drops: list[str] = []
    for up in upserts:
        path = up["field_path"]
        if path in before:
            rev_upserts.append(dict(before[path]))
        else:
            rev_drops.append(path)
    missing = [d for d in drops if d not in before]
    if missing:
        raise ValueError(f"삭제한 컬럼의 적용 전 정보가 없어 되돌릴 수 없습니다: {', '.join(missing)}")
    rev_upserts.extend(dict(before[d]) for d in drops)
    return rev_upserts, rev_drops


@activity.defn(name="apply_change")
async def apply_change(payload: ApplyInput) -> None:
    """승인된 변경을 실제 카탈로그 스키마에 적용하고(``apply_schema_spec``) CR 을 APPLIED 로 전이한다.

    적용 직전 스키마를 ``schema_before`` 에 다시 저장한다 — 생성 뒤 동기화·편집으로 스키마가 바뀌었어도
    롤백(``service.rollback_cr``)이 실제 적용 전 상태로 되돌리게 한다.

    검색용 임베딩 재생성은 워커 프로세스에 provider 가 없을 수 있어 여기서는 생략한다
    (동기화/편집 경로가 후속으로 처리).
    """
    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, payload.cr_id)
        if cr is None:
            _log_missing_cr("apply_change", payload.cr_id)
            return

        upserts, drops = _parse_schema_after(cr.schema_after)
        if not upserts and not drops:
            logger.warning(
                "apply_change: 적용할 스키마 변경이 없음 cr=%s — 상태만 APPLIED 로 전이",
                cr.cr_code,
            )
        else:
            before = await apply_schema_spec(session, cr.dataset_id, upserts, drops, f"apply_change cr={cr.cr_code}")
            cr.schema_before = json.dumps({"fields": before}, ensure_ascii=False, indent=2)

        logger.info(
            "변경 적용: cr=%s dataset=%s (+%d upsert, -%d drop)",
            cr.cr_code, cr.dataset_id, len(upserts), len(drops),
        )
        cr.status = schemas.CRStatus.APPLIED.value
        cr.applied_at = datetime.now(timezone.utc)
        await session.commit()
