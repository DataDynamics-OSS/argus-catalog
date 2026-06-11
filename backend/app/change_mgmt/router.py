# SPDX-License-Identifier: Apache-2.0
"""변경 관리 API.

엔드포인트:
- POST   /changes                          CR 생성 (DRAFT)
- POST   /changes/{id}/submit              결재 워크플로우 기동
- GET    /changes                          CR 목록
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

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.change_mgmt import schemas, service, temporal_client
from app.core.auth import CurrentUser
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/changes", tags=["change-mgmt"])


def _require_temporal() -> None:
    """Temporal 워크플로우 엔진이 연결돼 있어야 하는 엔드포인트용 가드.

    미연결이면 503 으로 거부한다 (RuntimeError 가 500 으로 새어나가지 않도록).
    """
    if not temporal_client.is_connected():
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
    user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    # 요청자는 인증 사용자에서 도출한다 (클라이언트가 위조할 수 없도록)
    cr = await service.create_cr(session, data, user.username)
    await session.commit()
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
    crs = await service.list_crs(session, status=status, dataset_id=dataset_id)
    return [schemas.ChangeRequestResponse.model_validate(cr) for cr in crs]


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
async def submit_change_request(_guard: CurrentUser,
    cr_id: int,
    notify_channels: list[str] = Body(default=["EMAIL", "IN_APP"]),
    session: AsyncSession = Depends(get_session),
):
    _require_temporal()
    cr = await service.get_cr(session, cr_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="변경 요청을(를) 찾을 수 없습니다.")
    if cr.status != schemas.CRStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail=f"변경 요청은(는) DRAFT 상태여야 합니다 (현재={cr.status}).")

    workflow_id = await service.submit_cr(session, cr, notify_channels)
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
    user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """결재 처리 — 본인의 현재 차례 단계만 결정할 수 있다 (단계는 서버가 결정).

    결재자는 인증 사용자에서 도출하고, 대상 단계는 현재 결재 차례(가장 낮은
    PENDING)로 서버가 고른다 — 클라이언트가 단계/결재자를 위조할 수 없다.
    """
    _require_temporal()
    cr = await service.get_cr(session, cr_id)
    if cr is None or cr.workflow_id is None:
        raise HTTPException(status_code=404, detail="변경 요청 또는 워크플로우을(를) 찾을 수 없습니다.")

    steps = await service.list_approval_steps(session, cr_id)
    current = service.current_pending_step(steps)
    if current is None:
        raise HTTPException(status_code=400, detail="결재 대기 중인 단계가 없습니다.")
    if current.approver != user.username:
        raise HTTPException(status_code=403, detail="현재 결재 차례가 아닙니다.")

    await service.submit_decision_signal(
        cr.workflow_id, current.step_order, req.decision.value, req.comment, user.username
    )
    return {"accepted": True}


@router.post("/{cr_id}/cancel", status_code=202)
async def cancel_change_request(_guard: CurrentUser,
    cr_id: int, session: AsyncSession = Depends(get_session)
):
    _require_temporal()
    cr = await service.get_cr(session, cr_id)
    if cr is None or cr.workflow_id is None:
        raise HTTPException(status_code=404, detail="변경 요청 또는 워크플로우을(를) 찾을 수 없습니다.")
    await service.cancel_cr(cr.workflow_id)
    return {"accepted": True}


# ---------------------------------------------------------------------------
# Consumer / Notification
# ---------------------------------------------------------------------------

@router.post("/consumers", response_model=schemas.ConsumerResponse, status_code=201)
async def create_consumer(_guard: CurrentUser,
    data: schemas.ConsumerCreate, session: AsyncSession = Depends(get_session)
):
    consumer = await service.create_consumer(session, data)
    await session.commit()
    return schemas.ConsumerResponse.model_validate(consumer)


@router.get(
    "/consumers/{dataset_id}", response_model=list[schemas.ConsumerResponse]
)
async def list_consumers(dataset_id: int, session: AsyncSession = Depends(get_session)):
    consumers = await service.list_consumers(session, dataset_id)
    return [schemas.ConsumerResponse.model_validate(c) for c in consumers]


@router.get(
    "/{cr_id}/notifications", response_model=list[schemas.NotificationLogResponse]
)
async def list_notifications(cr_id: int, session: AsyncSession = Depends(get_session)):
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
    log = await service.ack_notification(session, log_id, comment)
    await session.commit()
    return schemas.NotificationLogResponse.model_validate(log)
