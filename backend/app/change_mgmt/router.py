# SPDX-License-Identifier: Apache-2.0
"""변경 관리 API.

엔드포인트:
- POST   /changes                          CR 생성 (DRAFT)
- POST   /changes/{id}/submit              결재 워크플로우 기동
- GET    /changes                          CR 목록
- GET    /changes/inbox                    내 결재 차례 CR 목록
- GET    /changes/inbox/count              내 결재 대기 건수 (뱃지용)
- GET    /changes/{id}                     CR 상세 (결재 단계 포함)
- POST   /changes/{id}/decisions           결재 처리 (Signal)
- POST   /changes/{id}/cancel              CR 취소 (Signal)
- POST   /changes/consumers                소비자 등록
- GET    /changes/consumers/{dataset_id}   소비자 목록
- GET    /changes/{id}/notifications       통지 로그
- POST   /changes/notifications/{log_id}/ack  통지 수신 확인
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.service import RPCError, RPCStatusCode

from app.change_mgmt import schemas, service, temporal_client
from app.core.auth import CurrentUser, TokenUser, assert_owner_or_admin
from app.core.database import get_session
from app.permissions.router import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/changes", tags=["change-mgmt"])

# 기능 권한(권한 관리 › 기능 권한) — 미설정이면 로그인 사용자 모두 허용(open-by-default).
# 결재는 이 권한과 별개로 "현재 차례 결재자 본인" 검사가 그대로 적용된다.
ChangeCreator = Annotated[TokenUser, Depends(require_feature("changes.create"))]
ChangeApprover = Annotated[TokenUser, Depends(require_feature("changes.approve"))]

# 워크플로우가 취소 Signal 을 반영하는 상태 — 적용(APPLIED) 전까지
CANCELLABLE_STATUSES = frozenset({
    schemas.CRStatus.SUBMITTED.value, schemas.CRStatus.APPROVING.value,
    schemas.CRStatus.APPROVED.value, schemas.CRStatus.SCHEDULED.value,
})


TEMPORAL_CALL_FAILED = "Temporal 워크플로우 엔진 호출에 실패했습니다."


async def _require_temporal() -> None:
    """Temporal 워크플로우 엔진이 연결돼 있어야 하는 엔드포인트용 가드.

    미연결이면 한 번 재연결을 시도하고(간격 제한, ``temporal_client.ensure_connected``),
    그래도 안 되면 503 으로 거부한다 (RuntimeError 가 500 으로 새어나가지 않도록).
    """
    if not await temporal_client.ensure_connected():
        logger.warning("Temporal 미연결로 요청 거부(503)")
        raise HTTPException(
            status_code=503,
            detail="Temporal 워크플로우 엔진을(를) 사용할 수 없습니다. "
            "다음 명령으로 시작하세요: docker compose -f backend/deploy/temporal/docker-compose.yml up -d",
        )


# ---------------------------------------------------------------------------
# CR CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=schemas.ChangeRequestResponse, status_code=201)
async def create_change_request(
    data: schemas.ChangeRequestCreate,
    user: ChangeCreator,
    session: AsyncSession = Depends(get_session),
):
    """CR 을 DRAFT 로 생성하고 결재선·참조자를 저장한다. 없는/비활성 결재자가 있으면 422."""
    # 결재자가 없는 사용자면 아무도 결재할 수 없어 7일 뒤 타임아웃 반려된다 — 생성 때 막는다
    missing = await service.unknown_approvers(session, [s.approver for s in data.approval_chain])
    if missing:
        logger.warning("CR 생성 거부(알 수 없는 결재자): by=%s missing=%s", user.username, missing)
        raise HTTPException(status_code=422, detail=f"등록되지 않았거나 비활성인 결재자입니다: {', '.join(missing)}")
    # 요청자는 인증 사용자에서 도출한다 (클라이언트가 위조할 수 없도록)
    cr = await service.create_cr(session, data, user.username)
    await session.commit()
    logger.info("CR 생성: cr=%s dataset_id=%s by=%s", cr.cr_code, cr.dataset_id, user.username)
    # 참조자(CC) 통지 — 이메일 우선, Slack/Mattermost 선택 (실패해도 생성은 유지)
    try:
        await service.notify_referrers(session, cr)
    except Exception as e:  # noqa: BLE001
        logger.warning("참조자(CC) 통지 실패 (CR 생성은 유지): cr=%s: %s", cr.cr_code, e)
    steps = await service.list_approval_steps(session, cr.id)
    refs = await service.list_referrers(session, cr.id)
    response = schemas.ChangeRequestResponse.model_validate(cr)
    response.approval_steps = [schemas.ApprovalStepResponse.model_validate(s) for s in steps]
    response.referrers = [schemas.ReferrerResponse.model_validate(r) for r in refs]
    return response


@router.get("", response_model=list[schemas.ChangeRequestResponse])
async def list_change_requests(
    status: str | None = Query(None),
    dataset_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """CR 목록(필터: 상태·데이터셋)을 결재 단계와 함께 반환한다."""
    logger.debug("CR 목록 조회: status=%s dataset_id=%s", status, dataset_id)
    crs = await service.list_crs(session, status=status, dataset_id=dataset_id)
    # 목록 "결재" 열(승인 단계/전체·반려)용 결재 단계 — 예전에는 빈 배열이라 항상 "—" 였다(IT CHG-T04)
    steps_by_cr = await service.steps_by_cr(session, [cr.id for cr in crs])
    out: list[schemas.ChangeRequestResponse] = []
    for cr in crs:
        response = schemas.ChangeRequestResponse.model_validate(cr)
        response.approval_steps = [
            schemas.ApprovalStepResponse.model_validate(s) for s in steps_by_cr.get(cr.id, [])
        ]
        out.append(response)
    return out


# ---------------------------------------------------------------------------
# 결재 인박스 — "지금 내가 결재할 차례인 CR" (경로 충돌 방지를 위해 /{cr_id} 보다 먼저 선언)
# ---------------------------------------------------------------------------

@router.get("/inbox", response_model=list[schemas.ChangeRequestResponse])
async def list_my_inbox(
    user: CurrentUser, session: AsyncSession = Depends(get_session)
):
    """현재 사용자가 지금 결재해야 하는 변경 요청 목록 (현재 단계 결재자=나)."""
    out: list[schemas.ChangeRequestResponse] = []
    for cr, steps in await service.list_inbox(session, user.username):
        response = schemas.ChangeRequestResponse.model_validate(cr)
        response.approval_steps = [
            schemas.ApprovalStepResponse.model_validate(s) for s in steps
        ]
        out.append(response)
    return out


@router.get("/inbox/count")
async def my_inbox_count(
    user: CurrentUser, session: AsyncSession = Depends(get_session)
):
    """인박스 뱃지용 — 내가 결재 대기 중인 건수."""
    return {"count": await service.count_inbox(session, user.username)}


@router.get("/{cr_id}", response_model=schemas.ChangeRequestResponse)
async def get_change_request(
    cr_id: int, session: AsyncSession = Depends(get_session)
):
    """CR 상세(결재 단계·참조자 포함). 없으면 404."""
    logger.debug("CR 상세 조회: cr_id=%d", cr_id)
    cr = await service.get_cr(session, cr_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="변경 요청을(를) 찾을 수 없습니다.")
    steps = await service.list_approval_steps(session, cr_id)
    refs = await service.list_referrers(session, cr_id)
    response = schemas.ChangeRequestResponse.model_validate(cr)
    response.approval_steps = [schemas.ApprovalStepResponse.model_validate(s) for s in steps]
    response.referrers = [schemas.ReferrerResponse.model_validate(r) for r in refs]
    return response


# ---------------------------------------------------------------------------
# 워크플로우 트리거
# ---------------------------------------------------------------------------

@router.post("/{cr_id}/submit", response_model=schemas.ChangeRequestSubmitResponse)
async def submit_change_request(user: CurrentUser,
    cr_id: int,
    notify_channels: list[str] = Body(default=["EMAIL", "IN_APP"]),
    session: AsyncSession = Depends(get_session),
):
    """결재 상신 — 요청자 본인 또는 관리자만. DRAFT 가 아니면 400, Temporal 장애 시 503."""
    await _require_temporal()
    cr = await service.get_cr(session, cr_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="변경 요청을(를) 찾을 수 없습니다.")
    assert_owner_or_admin(user, cr.requested_by, "변경 요청")
    if cr.status != schemas.CRStatus.DRAFT.value:
        logger.warning("CR 상신 거부(DRAFT 아님): cr_id=%d status=%s", cr_id, cr.status)
        raise HTTPException(status_code=400, detail=f"변경 요청은(는) DRAFT 상태여야 합니다 (현재={cr.status}).")

    try:
        workflow_id = await service.submit_cr(session, cr, notify_channels)
    except RPCError as e:
        # 연결을 맺은 뒤 Temporal 이 내려간 경우 — 예전에는 500. 상태 변경은 기동 뒤라 CR 은 DRAFT 그대로
        await session.rollback()
        logger.warning("워크플로우 기동 실패: cr=%d: %s", cr_id, e)
        raise HTTPException(status_code=503, detail=TEMPORAL_CALL_FAILED) from e
    await session.commit()
    return schemas.ChangeRequestSubmitResponse(
        cr_id=cr.id,
        cr_code=cr.cr_code,
        workflow_id=workflow_id,
        status=schemas.CRStatus(cr.status),
    )


@router.post("/{cr_id}/decisions", status_code=202)
async def submit_decision(
    cr_id: int,
    req: schemas.ApprovalDecisionRequest,
    user: ChangeApprover,
    session: AsyncSession = Depends(get_session),
):
    """결재 처리 — 본인의 현재 차례 단계만 결정할 수 있다 (단계는 서버가 결정).

    결재자는 인증 사용자에서 도출하고, 대상 단계는 현재 결재 차례(가장 낮은
    PENDING)로 서버가 고른다 — 클라이언트가 단계/결재자를 위조할 수 없다.
    """
    await _require_temporal()
    cr = await service.get_cr(session, cr_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="변경 요청을(를) 찾을 수 없습니다.")
    # 결재 진행 중(APPROVING)일 때만 받는다 — 있는 CR 이 결재할 수 없는 상태면 모두 409.
    # 반려·취소로 끝난 CR 에도 PENDING 단계가 남아, 상태를 보지 않으면 종료된 워크플로우에 Signal 을
    # 보내다 500 이 났다(CHG-T17). 예전에는 DRAFT 가 404(워크플로우 없음), APPLIED 가 400 으로 제각각이었다.
    if cr.status != schemas.CRStatus.APPROVING.value:
        logger.warning("결재 거부(APPROVING 아님): cr_id=%d status=%s user=%s",
                       cr_id, cr.status, user.username)
        raise HTTPException(
            status_code=409,
            detail=f"결재할 수 없는 상태입니다 — 결재 진행 중(APPROVING)인 변경 요청만 결재할 수 있습니다 (현재={cr.status}).",
        )
    if cr.workflow_id is None:
        raise HTTPException(status_code=404, detail="변경 요청 또는 워크플로우을(를) 찾을 수 없습니다.")

    steps = await service.list_approval_steps(session, cr_id)
    current = service.current_pending_step(steps)
    if current is None:
        raise HTTPException(status_code=400, detail="결재 대기 중인 단계가 없습니다.")
    if current.approver != user.username:
        logger.warning("결재 거부(차례 아님): cr_id=%d step=%d user=%s",
                       cr_id, current.step_order, user.username)
        raise HTTPException(status_code=403, detail="현재 결재 차례가 아닙니다.")

    # 위임은 요청의 대결자를 기록한다(필수, 예전에는 결정자 본인이 저장됐다 — IT CHG-T14).
    # 대결자 라우팅(대결자가 이 단계를 결재)은 아직 없다 — 워크플로우는 위임을 승인처럼 다음 단계로 넘긴다.
    extra: dict = {}
    if req.decision == schemas.DecisionInput.DELEGATED:
        if not req.delegated_to:
            raise HTTPException(status_code=422, detail="위임(DELEGATED) 결정에는 대결자(delegated_to)가 필요합니다.")
        missing = await service.unknown_approvers(session, [req.delegated_to])
        if missing:
            raise HTTPException(status_code=422, detail=f"등록되지 않았거나 비활성인 대결자입니다: {', '.join(missing)}")
        extra["delegated_to"] = req.delegated_to
    try:
        await service.submit_decision_signal(
            cr.workflow_id, current.step_order, req.decision.value, req.comment, user.username, **extra
        )
    except RPCError as e:
        logger.warning("결재 Signal 실패: cr=%d workflow=%s: %s", cr_id, cr.workflow_id, e)
        if e.status == RPCStatusCode.NOT_FOUND:
            # DB 상태 갱신보다 워크플로우 종료가 먼저인 짧은 틈 — 이미 끝난(또는 없는) 워크플로우
            raise HTTPException(status_code=409, detail="결재를 받을 수 없는 상태입니다 — 워크플로우가 이미 종료되었습니다.") from e
        raise HTTPException(status_code=503, detail=TEMPORAL_CALL_FAILED) from e
    return {"accepted": True}


@router.post("/{cr_id}/cancel", status_code=202)
async def cancel_change_request(user: CurrentUser,
    cr_id: int, session: AsyncSession = Depends(get_session)
):
    """취소 — 요청자 본인 또는 관리자만, 적용 전(작성 중~승인·예정 대기) CR 만.

    - DRAFT(상신 전): 워크플로우가 없으니 DB 상태만 바로 CANCELLED 로 바꾼다(Temporal 불필요).
      예전에는 ``workflow_id`` 가 없다고 404 였다.
    - 상신 후: 워크플로우에 취소 Signal — 적용 전 어느 단계에서든 반영된다(``workflow.py``).
    - 이미 끝난 CR(적용·반려·취소)은 409 — 예전에는 종료된 워크플로우에 Signal 을 보내다 500 이었다.
    """
    cr = await service.get_cr(session, cr_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="변경 요청 또는 워크플로우을(를) 찾을 수 없습니다.")
    assert_owner_or_admin(user, cr.requested_by, "변경 요청")
    if cr.status == schemas.CRStatus.DRAFT.value and cr.workflow_id is None:
        if not await service.cancel_draft_cr(session, cr.id):
            # 그 사이 상신됨 — 새 상태로 다시 시도하게 한다
            logger.warning("DRAFT CR 취소 충돌(그 사이 상신됨): cr_id=%d", cr_id)
            raise HTTPException(status_code=409, detail="변경 요청 상태가 바뀌었습니다. 새로고침 후 다시 시도하세요.")
        await session.commit()
        logger.info("DRAFT CR 취소: cr_id=%d by=%s", cr_id, user.username)
        return {"accepted": True, "status": schemas.CRStatus.CANCELLED.value}
    # 상태 검사를 워크플로우 존재 검사보다 먼저 — 상신 전에 취소된 CR(워크플로우 없음)을 다시 취소하면
    # 예전에는 404 "…워크플로우을(를) 찾을 수 없습니다." 였다(IT CHG-T12). 종료 상태는 Temporal 없이 409.
    if cr.status not in CANCELLABLE_STATUSES:
        logger.warning("CR 취소 거부(종료 상태): cr_id=%d status=%s", cr_id, cr.status)
        raise HTTPException(status_code=409, detail=f"취소할 수 없는 상태입니다 (현재={cr.status}).")
    await _require_temporal()
    if cr.workflow_id is None:
        raise HTTPException(status_code=404, detail="변경 요청 또는 워크플로우을(를) 찾을 수 없습니다.")
    try:
        await service.cancel_cr(cr.workflow_id)
    except RPCError as e:
        logger.warning("취소 Signal 실패: cr=%d workflow=%s: %s", cr_id, cr.workflow_id, e)
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=409, detail="취소할 수 없는 상태입니다 — 워크플로우가 이미 종료되었습니다.") from e
        raise HTTPException(status_code=503, detail=TEMPORAL_CALL_FAILED) from e
    logger.info("CR 취소 Signal 전송: cr_id=%d workflow=%s by=%s", cr_id, cr.workflow_id, user.username)
    return {"accepted": True}


# ---------------------------------------------------------------------------
# Consumer / Notification
# ---------------------------------------------------------------------------

@router.post("/consumers", response_model=schemas.ConsumerResponse, status_code=201)
async def create_consumer(_guard: CurrentUser,
    data: schemas.ConsumerCreate, _perm=Depends(require_feature("changes.consumers")), session: AsyncSession = Depends(get_session)
):
    """데이터셋 소비자를 등록한다(``changes.consumers`` 기능 권한)."""
    consumer = await service.create_consumer(session, data)
    await session.commit()
    logger.info("소비자 등록: id=%s dataset_id=%s name=%s by=%s",
                consumer.id, consumer.dataset_id, consumer.consumer_name, _guard.username)
    return schemas.ConsumerResponse.model_validate(consumer)


@router.get(
    "/consumers/{dataset_id}", response_model=list[schemas.ConsumerResponse]
)
async def list_consumers(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋의 소비자 목록."""
    logger.debug("소비자 목록 조회: dataset_id=%d", dataset_id)
    consumers = await service.list_consumers(session, dataset_id)
    return [schemas.ConsumerResponse.model_validate(c) for c in consumers]


@router.get(
    "/{cr_id}/notifications", response_model=list[schemas.NotificationLogResponse]
)
async def list_notifications(cr_id: int, session: AsyncSession = Depends(get_session)):
    """CR 의 통지 로그 목록."""
    logger.debug("통지 로그 조회: cr_id=%d", cr_id)
    logs = await service.list_notifications(session, cr_id)
    return [schemas.NotificationLogResponse.model_validate(log) for log in logs]


@router.post(
    "/notifications/{log_id}/ack",
    response_model=schemas.NotificationLogResponse,
)
async def ack_notification(_guard: CurrentUser,
    log_id: int,
    comment: str | None = Body(None, embed=True),
    session: AsyncSession = Depends(get_session),
):
    """통지 수신 확인(ACK) — 상태를 ACKED 로 바꾸고 시각·코멘트를 기록한다. 없으면 404."""
    log = await service.ack_notification(session, log_id, comment)
    if log is None:
        logger.warning("통지 ACK 실패(없음): log_id=%d", log_id)
        raise HTTPException(status_code=404, detail="통지 이력을(를) 찾을 수 없습니다.")
    await session.commit()
    logger.info("통지 ACK: log_id=%d cr_id=%s by=%s", log_id, log.cr_id, _guard.username)
    return schemas.NotificationLogResponse.model_validate(log)
