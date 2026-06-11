# SPDX-License-Identifier: Apache-2.0
"""변경 관리 서비스 — DB CRUD + Temporal 워크플로우 트리거."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import DatasetSchema
from app.change_mgmt import models, schemas, temporal_client
from app.change_mgmt.workflow import (
    ChangeApprovalWorkflow,
    DecisionSignal,
    WorkflowInput,
)
from app.settings.mail import send_mail_db
from app.settings.notify import send_notify_db

logger = logging.getLogger(__name__)


async def _dataset_schema_json(session: AsyncSession, dataset_id: int) -> str | None:
    """데이터셋의 현재 스키마(카탈로그 필드)를 JSON 문자열로 — schema_before 자동 채움용."""
    import json as _json
    rows = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id).order_by(DatasetSchema.ordinal, DatasetSchema.id)
    )).scalars().all()
    if not rows:
        return None
    fields = [
        {
            "name": r.field_path,
            "type": r.field_type,
            "nullable": (r.nullable or "true") == "true",
            "primary_key": (r.is_primary_key or "false") == "true",
            **({"description": r.description} if r.description else {}),
        }
        for r in rows
    ]
    return _json.dumps({"fields": fields}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CR CRUD
# ---------------------------------------------------------------------------

async def _next_cr_code(session: AsyncSession) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"CR-{year}-"
    count = await session.scalar(
        select(func.count(models.ChangeRequest.id)).where(
            models.ChangeRequest.cr_code.like(f"{prefix}%")
        )
    )
    return f"{prefix}{(count or 0) + 1:04d}"


async def create_cr(
    session: AsyncSession, data: schemas.ChangeRequestCreate, requested_by: str
) -> models.ChangeRequest:
    # schema_before 미입력 시 데이터셋 현재 스키마로 자동 채움
    schema_before = data.schema_before
    if not (schema_before and schema_before.strip()):
        schema_before = await _dataset_schema_json(session, data.dataset_id)

    cr = models.ChangeRequest(
        cr_code=await _next_cr_code(session),
        title=data.title,
        description=data.description,
        dataset_id=data.dataset_id,
        change_type=data.change_type.value,
        priority=data.priority.value,
        status=schemas.CRStatus.DRAFT.value,
        schema_before=schema_before,
        schema_after=data.schema_after,
        rollback_plan=data.rollback_plan,
        business_justification=data.business_justification,
        scheduled_at=data.scheduled_at,
        requested_by=requested_by,
    )
    session.add(cr)
    await session.flush()  # cr.id 확보

    for step in data.approval_chain:
        session.add(
            models.ApprovalStep(
                cr_id=cr.id,
                step_order=step.step_order,
                approver=step.approver,
                role=step.role,
                due_at=step.due_at,
                decision=schemas.Decision.PENDING.value,
            )
        )
    for ref in data.referrers:
        if not ((ref.email and ref.email.strip()) or (ref.slack_target and ref.slack_target.strip())):
            continue  # 대상 식별 불가 — 건너뜀
        session.add(
            models.ChangeReferrer(
                cr_id=cr.id,
                name=(ref.name or "").strip() or None,
                email=(ref.email or "").strip() or None,
                channel=(ref.channel or "EMAIL").upper(),
                slack_target=(ref.slack_target or "").strip() or None,
            )
        )
    await session.flush()
    return cr


async def list_referrers(session: AsyncSession, cr_id: int) -> list[models.ChangeReferrer]:
    return list((await session.execute(
        select(models.ChangeReferrer).where(models.ChangeReferrer.cr_id == cr_id).order_by(models.ChangeReferrer.id)
    )).scalars().all())


async def notify_referrers(session: AsyncSession, cr: models.ChangeRequest) -> None:
    """변경 요청 참조자에게 통지. 통지 채널은 전역 설정(sidebar > 설정 > 변경관리)을 따른다."""
    refs = await list_referrers(session, cr.id)
    if not refs:
        return

    # 통지 사용 여부/채널은 전역 설정으로 일괄 관리한다(참조자별 채널 지정 없음).
    from app.settings.service import get_config_by_category
    cfg = await get_config_by_category(session, "change")
    if cfg.get("change_notify_enabled", "true").lower() not in ("true", "1", "yes"):
        return
    channel = (cfg.get("change_notify_channel") or "email").upper()

    subject = f"[변경요청 {cr.cr_code}] {cr.title}"
    body = (
        f"변경 요청이 등록되었습니다.\n\n"
        f"- 코드: {cr.cr_code}\n- 제목: {cr.title}\n- 데이터셋 ID: {cr.dataset_id}\n"
        f"- 변경 유형: {cr.change_type}\n- 우선순위: {cr.priority}\n- 상태: {cr.status}\n"
        f"- 적용 예정: {cr.scheduled_at.isoformat() if cr.scheduled_at else '미정'}\n"
        f"- 요청자: {cr.requested_by}\n\n"
        f"{cr.description or ''}\n"
    )

    if channel == "EMAIL":
        # 이메일은 참조자별로 개별 발송
        for r in refs:
            ok, err = False, None
            if not r.email:
                continue
            try:
                ok = await send_mail_db(session, subject, body, to=[r.email])
            except Exception as e:  # noqa: BLE001
                err = str(e)
                logger.warning("참조자 통지 실패(cr=%s, ref=%s): %s", cr.cr_code, r.id, e)
            session.add(models.NotificationLog(
                cr_id=cr.id, referrer_id=r.id, recipient=r.email,
                channel=channel, stage="SUBMITTED", status="SENT" if ok else "FAILED",
                sent_at=func.now() if ok else None, error=err,
            ))
    else:
        # Slack/Mattermost 는 설정된 기본 채널로 한 번에 발송하고 참조자 목록을 본문에 포함
        names = ", ".join(r.name or r.email or "-" for r in refs)
        ok, err = False, None
        try:
            ok = await send_notify_db(session, f"*{subject}*\n참조자: {names}\n{body}")
        except Exception as e:  # noqa: BLE001
            err = str(e)
            logger.warning("참조자 통지 실패(cr=%s, channel=%s): %s", cr.cr_code, channel, e)
        for r in refs:
            session.add(models.NotificationLog(
                cr_id=cr.id, referrer_id=r.id, recipient=r.name or r.email,
                channel=channel, stage="SUBMITTED", status="SENT" if ok else "FAILED",
                sent_at=func.now() if ok else None, error=err,
            ))
    await session.commit()


async def get_cr(session: AsyncSession, cr_id: int) -> models.ChangeRequest | None:
    return await session.get(models.ChangeRequest, cr_id)


async def list_crs(
    session: AsyncSession, status: str | None = None, dataset_id: int | None = None
) -> list[models.ChangeRequest]:
    stmt = select(models.ChangeRequest).order_by(models.ChangeRequest.id.desc())
    if status:
        stmt = stmt.where(models.ChangeRequest.status == status)
    if dataset_id:
        stmt = stmt.where(models.ChangeRequest.dataset_id == dataset_id)
    return (await session.execute(stmt)).scalars().all()


async def list_approval_steps(
    session: AsyncSession, cr_id: int
) -> list[models.ApprovalStep]:
    return (
        await session.execute(
            select(models.ApprovalStep)
            .where(models.ApprovalStep.cr_id == cr_id)
            .order_by(models.ApprovalStep.step_order)
        )
    ).scalars().all()


# ---------------------------------------------------------------------------
# 결재 인박스 — "지금 내가 결재할 차례인 CR"
# ---------------------------------------------------------------------------

def current_pending_step(
    steps: list[models.ApprovalStep],
) -> models.ApprovalStep | None:
    """현재 결재 차례 단계 = 가장 낮은 step_order 의 PENDING 단계.

    결재는 순차 진행(workflow.py)이라 이 단계보다 앞선 단계는 모두 처리된
    상태다. PENDING 이 없으면(모두 처리됨) None.
    """
    pending = [s for s in steps if s.decision == schemas.Decision.PENDING.value]
    return min(pending, key=lambda s: s.step_order) if pending else None


async def _approving_with_steps(
    session: AsyncSession,
) -> list[tuple[models.ChangeRequest, list[models.ApprovalStep]]]:
    """APPROVING 상태 CR + 각 결재 단계를 2개 쿼리로 모아 반환 (N+1 회피)."""
    crs = (await session.execute(
        select(models.ChangeRequest)
        .where(models.ChangeRequest.status == schemas.CRStatus.APPROVING.value)
        .order_by(models.ChangeRequest.id.desc())
    )).scalars().all()
    if not crs:
        return []
    ids = [cr.id for cr in crs]
    steps = (await session.execute(
        select(models.ApprovalStep)
        .where(models.ApprovalStep.cr_id.in_(ids))
        .order_by(models.ApprovalStep.step_order)
    )).scalars().all()
    by_cr: dict[int, list[models.ApprovalStep]] = {}
    for s in steps:
        by_cr.setdefault(s.cr_id, []).append(s)
    return [(cr, by_cr.get(cr.id, [])) for cr in crs]


async def list_inbox(
    session: AsyncSession, username: str
) -> list[tuple[models.ChangeRequest, list[models.ApprovalStep]]]:
    """현재 사용자가 '지금 결재할 차례'인 CR 목록.

    조건: status=APPROVING 이고, 현재 단계(가장 낮은 PENDING)의 approver 가
    곧 이 사용자 — 즉 앞선 단계가 모두 끝나 실제로 내 차례인 것만 담는다.
    """
    result = []
    for cr, steps in await _approving_with_steps(session):
        cur = current_pending_step(steps)
        if cur is not None and cur.approver == username:
            result.append((cr, steps))
    return result


async def count_inbox(session: AsyncSession, username: str) -> int:
    """인박스 뱃지용 카운트 — list_inbox 와 동일 기준."""
    return len(await list_inbox(session, username))


# ---------------------------------------------------------------------------
# Temporal 워크플로우 트리거
# ---------------------------------------------------------------------------

async def submit_cr(
    session: AsyncSession, cr: models.ChangeRequest, notify_channels: list[str]
) -> str:
    """CR을 SUBMITTED 로 전이하고 Temporal 워크플로우를 기동한다.

    반환값은 Temporal Workflow ID.
    """
    client = temporal_client.get_client()
    workflow_id = f"cr-{cr.cr_code}"

    steps = await list_approval_steps(session, cr.id)
    approver_chain = [step.approver for step in steps]

    scheduled_iso = cr.scheduled_at.isoformat() if cr.scheduled_at else None
    input = WorkflowInput(
        cr_id=cr.id,
        approver_chain=approver_chain,
        scheduled_at_iso=scheduled_iso,
        notify_channels=notify_channels,
    )

    await client.start_workflow(
        ChangeApprovalWorkflow.run,
        input,
        id=workflow_id,
        task_queue=temporal_client.TASK_QUEUE,
    )

    cr.status = schemas.CRStatus.SUBMITTED.value
    cr.workflow_id = workflow_id
    await session.flush()
    logger.info("Temporal 워크플로우 기동: cr=%s workflow=%s", cr.cr_code, workflow_id)
    return workflow_id


async def submit_decision_signal(
    workflow_id: str, step_order: int, decision: str, comment: str | None, decided_by: str
) -> None:
    """결재자가 결재 처리할 때 Temporal 워크플로우로 Signal 을 전송한다."""
    client = temporal_client.get_client()
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(
        "submit_decision",
        DecisionSignal(
            step_order=step_order,
            decision=decision,
            comment=comment,
            decided_by=decided_by,
        ),
    )
    logger.info(
        "결재 Signal 전송: workflow=%s step=%d decision=%s",
        workflow_id, step_order, decision,
    )


async def cancel_cr(workflow_id: str) -> None:
    client = temporal_client.get_client()
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal("cancel")


# ---------------------------------------------------------------------------
# Consumer / Notification
# ---------------------------------------------------------------------------

async def create_consumer(
    session: AsyncSession, data: schemas.ConsumerCreate
) -> models.Consumer:
    consumer = models.Consumer(**data.model_dump())
    session.add(consumer)
    await session.flush()
    return consumer


async def list_consumers(
    session: AsyncSession, dataset_id: int
) -> list[models.Consumer]:
    return (
        await session.execute(
            select(models.Consumer).where(models.Consumer.dataset_id == dataset_id)
        )
    ).scalars().all()


async def ack_notification(
    session: AsyncSession, log_id: int, comment: str | None
) -> models.NotificationLog:
    log = await session.get(models.NotificationLog, log_id)
    if log is None:
        raise ValueError(f"NotificationLog not found: {log_id}")
    log.status = "ACKED"
    log.acked_at = datetime.now(timezone.utc)
    log.ack_comment = comment
    await session.flush()
    return log


async def list_notifications(
    session: AsyncSession, cr_id: int
) -> list[models.NotificationLog]:
    return (
        await session.execute(
            select(models.NotificationLog)
            .where(models.NotificationLog.cr_id == cr_id)
            .order_by(models.NotificationLog.id.desc())
        )
    ).scalars().all()
