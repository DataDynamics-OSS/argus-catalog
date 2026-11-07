# SPDX-License-Identifier: Apache-2.0
"""변경 관리 서비스 — DB CRUD + Temporal 워크플로우 트리거."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
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
    """올해 코드 중 가장 큰 일련번호 + 1 (``CR-YYYY-NNNN``).

    예전에는 연도별 건수 + 1 이라, 데이터셋 삭제로 CR 이 연쇄 삭제돼 건수가 줄면 이미 있는
    코드를 다시 만들어 이후 생성이 모두 409 였다(IT CHG-T26). 형식이 다른 코드는 무시한다.
    """
    year = datetime.now(timezone.utc).year
    prefix = f"CR-{year}-"
    codes = (await session.execute(
        select(models.ChangeRequest.cr_code).where(models.ChangeRequest.cr_code.like(f"{prefix}%"))
    )).scalars().all()
    seqs = [int(c[len(prefix):]) for c in codes if c[len(prefix):].isdigit()]
    return f"{prefix}{max(seqs, default=0) + 1:04d}"


# 동시 생성으로 같은 코드를 고른 경우 재시도 횟수 (UNIQUE 위반 시 다음 번호로 다시)
CR_CODE_RETRIES = 5


async def _insert_cr_with_code(session: AsyncSession, cr: models.ChangeRequest) -> None:
    """CR 행을 새 코드로 INSERT — 코드 UNIQUE 위반이면 SAVEPOINT 를 되돌리고 다음 번호로 재시도."""
    for attempt in range(1, CR_CODE_RETRIES + 1):
        cr.cr_code = await _next_cr_code(session)
        try:
            async with session.begin_nested():
                session.add(cr)
                await session.flush()  # cr.id 확보
            return
        except IntegrityError as e:
            # 코드 충돌이 아닌 위반(없는 dataset_id 등)이나 마지막 시도면 그대로 올린다(전역 핸들러 409)
            if "cr_code" not in str(e.orig) or attempt == CR_CODE_RETRIES:
                raise
            logger.warning("CR 코드 충돌(%s) — 재시도 %d/%d", cr.cr_code, attempt, CR_CODE_RETRIES)
            # 롤백된 SAVEPOINT 의 객체는 세션에서 빠진다 — 다음 시도에 다시 add 한다
            if cr in session:
                session.expunge(cr)


async def unknown_approvers(session: AsyncSession, approvers: list[str]) -> list[str]:
    """등록되지 않았거나 비활성인 결재자 — 로컬 인증 모드에서만 확인한다.

    Keycloak 모드에서는 ``argus_users`` 에 행이 없어(사용자 정보는 JWT) 확인할 수 없으므로 빈 목록.
    """
    from app.core.config import settings
    from app.usermgr.models import ArgusUser

    names = sorted(set(approvers))
    if not names or settings.auth_type != "local":
        return []
    active = set((await session.execute(
        select(ArgusUser.username).where(ArgusUser.username.in_(names), ArgusUser.status == "active")
    )).scalars().all())
    return [n for n in names if n not in active]


async def create_cr(
    session: AsyncSession, data: schemas.ChangeRequestCreate, requested_by: str
) -> models.ChangeRequest:
    """CR 을 DRAFT 로 만들고 결재 단계(PENDING)·참조자를 함께 추가한다(flush 만, 커밋은 호출 측).

    schema_before 가 비어 있으면 데이터셋 현재 스키마로 채운다. 이메일·Slack 대상이 모두 없는
    참조자는 건너뛴다.
    """
    # schema_before 미입력 시 데이터셋 현재 스키마로 자동 채움
    schema_before = data.schema_before
    if not (schema_before and schema_before.strip()):
        schema_before = await _dataset_schema_json(session, data.dataset_id)

    cr = models.ChangeRequest(
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
    await _insert_cr_with_code(session, cr)

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
    logger.debug("CR 생성(flush): cr=%s steps=%d referrers=%d",
                 cr.cr_code, len(data.approval_chain), len(data.referrers))
    return cr


async def list_referrers(session: AsyncSession, cr_id: int) -> list[models.ChangeReferrer]:
    """CR 의 참조자(CC) 목록(등록순)."""
    return list((await session.execute(
        select(models.ChangeReferrer).where(models.ChangeReferrer.cr_id == cr_id).order_by(models.ChangeReferrer.id)
    )).scalars().all())


async def notify_referrers(session: AsyncSession, cr: models.ChangeRequest) -> None:
    """변경 요청 참조자에게 통지. 통지 채널은 전역 설정(sidebar > 설정 > 변경관리)을 따른다.

    참조자별 ``channel`` 값은 쓰지 않고 change_notify_channel(기본 email)로 보낸다.
    EMAIL 은 참조자별 개별 발송, 그 외는 설정된 Slack/Mattermost 채널로 1회 발송한다.
    결과는 NotificationLog(stage=SUBMITTED)로 남기고 커밋한다.
    """
    refs = await list_referrers(session, cr.id)
    if not refs:
        return

    # 통지 사용 여부/채널은 전역 설정으로 일괄 관리한다(참조자별 채널 지정 없음).
    from app.settings.service import get_config_by_category
    cfg = await get_config_by_category(session, "change")
    if cfg.get("change_notify_enabled", "true").lower() not in ("true", "1", "yes"):
        logger.debug("참조자 통지 비활성 — 건너뜀: cr=%s", cr.cr_code)
        return
    channel = (cfg.get("change_notify_channel") or "email").upper()
    logger.debug("참조자 통지: cr=%s channel=%s referrers=%d", cr.cr_code, channel, len(refs))

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
    logger.info("참조자 통지 완료: cr=%s channel=%s referrers=%d", cr.cr_code, channel, len(refs))


async def get_cr(session: AsyncSession, cr_id: int) -> models.ChangeRequest | None:
    """CR 단건 조회(없으면 None)."""
    return await session.get(models.ChangeRequest, cr_id)


async def list_crs(
    session: AsyncSession, status: str | None = None, dataset_id: int | None = None
) -> list[models.ChangeRequest]:
    """CR 목록(최신 id 순). status·dataset_id 가 주어지면 필터한다."""
    stmt = select(models.ChangeRequest).order_by(models.ChangeRequest.id.desc())
    if status:
        stmt = stmt.where(models.ChangeRequest.status == status)
    if dataset_id:
        stmt = stmt.where(models.ChangeRequest.dataset_id == dataset_id)
    return (await session.execute(stmt)).scalars().all()


async def list_approval_steps(
    session: AsyncSession, cr_id: int
) -> list[models.ApprovalStep]:
    """CR 의 결재 단계 목록(step_order 순)."""
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


async def steps_by_cr(
    session: AsyncSession, cr_ids: list[int]
) -> dict[int, list[models.ApprovalStep]]:
    """여러 CR 의 결재 단계를 한 번에 조회해 CR id 별로 묶는다 (목록 화면용, N+1 회피)."""
    if not cr_ids:
        return {}
    rows = (await session.execute(
        select(models.ApprovalStep)
        .where(models.ApprovalStep.cr_id.in_(cr_ids))
        .order_by(models.ApprovalStep.cr_id, models.ApprovalStep.step_order)
    )).scalars().all()
    out: dict[int, list[models.ApprovalStep]] = {}
    for s in rows:
        out.setdefault(s.cr_id, []).append(s)
    return out


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
    by_cr = await steps_by_cr(session, [cr.id for cr in crs])
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

    반환값은 Temporal Workflow ID(``cr-{cr_code}``). 워크플로우 기동이 먼저라 기동 실패(RPCError)
    시 CR 상태는 바뀌지 않는다. flush 만 하고 커밋은 호출 측.
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
    workflow_id: str, step_order: int, decision: str, comment: str | None, decided_by: str,
    delegated_to: str | None = None,
) -> None:
    """결재자가 결재 처리할 때 Temporal 워크플로우로 Signal 을 전송한다.

    ``delegated_to`` 는 위임(DELEGATED) 결정의 대결자 — 결재 단계의 ``delegated_to`` 에 기록된다.
    """
    client = temporal_client.get_client()
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(
        "submit_decision",
        DecisionSignal(
            step_order=step_order,
            decision=decision,
            comment=comment,
            decided_by=decided_by,
            delegated_to=delegated_to,
        ),
    )
    logger.info(
        "결재 Signal 전송: workflow=%s step=%d decision=%s",
        workflow_id, step_order, decision,
    )


async def cancel_draft_cr(session: AsyncSession, cr_id: int) -> bool:
    """상신 전(DRAFT, 워크플로우 없음) CR 을 바로 CANCELLED 로 바꾼다. 커밋은 호출 측.

    조건부 UPDATE — 그 사이 상신돼 DRAFT 가 아니게 됐으면 False(아무것도 바꾸지 않음).
    """
    result = await session.execute(
        update(models.ChangeRequest)
        .where(
            models.ChangeRequest.id == cr_id,
            models.ChangeRequest.status == schemas.CRStatus.DRAFT.value,
            models.ChangeRequest.workflow_id.is_(None),
        )
        .values(status=schemas.CRStatus.CANCELLED.value, updated_at=func.now())
    )
    return (result.rowcount or 0) > 0


async def cancel_cr(workflow_id: str) -> None:
    """워크플로우에 ``cancel`` Signal 을 보낸다(RPCError 는 호출 측이 처리)."""
    logger.debug("취소 Signal 전송: workflow=%s", workflow_id)
    client = temporal_client.get_client()
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal("cancel")


# 워크플로우가 아직 끝나지 않은(취소 Signal 을 받을 수 있는) 상태
_IN_FLIGHT_STATUSES = (
    schemas.CRStatus.SUBMITTED.value, schemas.CRStatus.APPROVING.value,
    schemas.CRStatus.APPROVED.value, schemas.CRStatus.SCHEDULED.value,
)


async def in_flight_workflow_ids(session: AsyncSession, dataset_ids: list[int]) -> list[str]:
    """데이터셋들의 진행 중 CR 워크플로우 ID — 데이터셋 삭제 전에 모아 둔다(삭제 후엔 CR 행이 없다)."""
    if not dataset_ids:
        return []
    return list((await session.execute(
        select(models.ChangeRequest.workflow_id).where(
            models.ChangeRequest.dataset_id.in_(dataset_ids),
            models.ChangeRequest.workflow_id.is_not(None),
            models.ChangeRequest.status.in_(_IN_FLIGHT_STATUSES),
        )
    )).scalars().all())


async def cancel_workflows_best_effort(workflow_ids: list[str]) -> int:
    """데이터셋·데이터 소스 삭제로 CR 이 연쇄 삭제될 때 진행 중 워크플로우에 취소 Signal 을 보낸다.

    예전에는 워크플로우가 그대로 남아 결재 대기·예정 시각 대기를 이어갔다(IT CHG-T26).
    삭제는 막지 않는다 — Temporal 미연결·호출 실패는 로그만 남긴다(남은 워크플로우의 액티비티는
    없는 CR 을 건너뛴다, ``activities.py``). 보낸 건수를 반환한다.
    """
    if not workflow_ids:
        return 0
    if not temporal_client.is_connected():
        logger.warning("Temporal 미연결 — 삭제된 CR 의 워크플로우 %d건을 취소하지 못했습니다: %s",
                       len(workflow_ids), ", ".join(workflow_ids))
        return 0
    sent = 0
    for wf in workflow_ids:
        try:
            await cancel_cr(wf)
            sent += 1
        except Exception as e:  # noqa: BLE001 — 이미 종료된 워크플로우(NOT_FOUND) 등
            logger.warning("삭제된 CR 의 워크플로우 취소 실패: workflow=%s: %s", wf, e)
    logger.info("삭제된 CR 의 워크플로우 취소 Signal: requested=%d sent=%d", len(workflow_ids), sent)
    return sent


# ---------------------------------------------------------------------------
# Consumer / Notification
# ---------------------------------------------------------------------------

async def create_consumer(
    session: AsyncSession, data: schemas.ConsumerCreate
) -> models.Consumer:
    """소비자 등록(flush 만). (dataset_id, consumer_name) UNIQUE 위반은 IntegrityError 로 전파된다."""
    consumer = models.Consumer(**data.model_dump())
    session.add(consumer)
    await session.flush()
    return consumer


async def list_consumers(
    session: AsyncSession, dataset_id: int
) -> list[models.Consumer]:
    """데이터셋의 소비자 목록."""
    return (
        await session.execute(
            select(models.Consumer).where(models.Consumer.dataset_id == dataset_id)
        )
    ).scalars().all()


async def ack_notification(
    session: AsyncSession, log_id: int, comment: str | None
) -> models.NotificationLog | None:
    """통지 ACK. 없는 통지면 None(라우터가 404) — 예전에는 ValueError 가 그대로 올라가 500 이었다."""
    log = await session.get(models.NotificationLog, log_id)
    if log is None:
        return None
    log.status = "ACKED"
    log.acked_at = datetime.now(timezone.utc)
    log.ack_comment = comment
    await session.flush()
    return log


async def list_notifications(
    session: AsyncSession, cr_id: int
) -> list[models.NotificationLog]:
    """CR 의 통지 로그 목록(최신순)."""
    return (
        await session.execute(
            select(models.NotificationLog)
            .where(models.NotificationLog.cr_id == cr_id)
            .order_by(models.NotificationLog.id.desc())
        )
    ).scalars().all()
