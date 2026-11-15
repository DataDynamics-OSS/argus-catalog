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
- PUT    /changes/{id}                     초안(DRAFT) 수정 — 결재선·참조자 포함 전체 교체
- DELETE /changes/{id}                     상신한 적 없는 CR 삭제(작성 중·상신 전 취소)
- POST   /changes/{id}/close               적용 완료 CR 종료 (APPLIED → CLOSED)
- POST   /changes/{id}/rollback            적용한 스키마 변경 되돌리기 (APPLIED → ROLLED_BACK)
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

from app.catalog import service as catalog_service
from app.change_mgmt import schemas, service, temporal_client
from app.change_mgmt.activities import CONSUMER_CHANNELS
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


NEED_INDEPENDENT_APPROVER = (
    "이 데이터셋을 직접 편집할 권한(생성자·관리자)이 없으면 결재선에 요청자가 아닌 결재자를 1명 이상 지정해야 합니다."
)


async def _require_independent_approver(session: AsyncSession, user: TokenUser, dataset_id: int,
                                        approvers: list[str]) -> None:
    """결재선이 비거나 요청자 본인뿐이면 결재 없이(자동 승인) 적용된다 — 데이터셋 편집 권한이 있는 사람만 허용(DEF-050).

    편집 권한이 없는 요청자가 이렇게 만들면 직접 편집 가드(``_require_dataset_editor``)를 우회해 남의 데이터셋 스키마를
    바꿀 수 있으므로 422 로 막는다.
    """
    if user.is_admin or any(a and a != user.username for a in approvers):
        return
    created_by = await catalog_service.get_dataset_created_by(session, dataset_id)
    if created_by and created_by == user.username:
        return
    logger.warning("CR 거부(독립 결재자 없음): by=%s dataset_id=%s approvers=%s", user.username, dataset_id, approvers)
    raise HTTPException(status_code=422, detail=NEED_INDEPENDENT_APPROVER)


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
    await _require_independent_approver(session, user, data.dataset_id, [s.approver for s in data.approval_chain])
    # 요청자는 인증 사용자에서 도출한다 (클라이언트가 위조할 수 없도록)
    cr = await service.create_cr(session, data, user.username)
    await session.commit()
    logger.info("CR 생성: cr=%s dataset_id=%s by=%s", cr.cr_code, cr.dataset_id, user.username)
    # 참조자(CC) 통지 — 이메일 우선, Slack/Mattermost 선택 (실패해도 생성은 유지)
    try:
        await service.notify_referrers(session, cr)
    except Exception as e:  # noqa: BLE001
        logger.warning("참조자(CC) 통지 실패 (CR 생성은 유지): cr=%s: %s", cr.cr_code, e)
    return await _detail_response(session, cr)


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
    names = await service.dataset_names(session, [cr.dataset_id for cr in crs])
    out: list[schemas.ChangeRequestResponse] = []
    for cr in crs:
        response = schemas.ChangeRequestResponse.model_validate(cr)
        response.dataset_name = names.get(cr.dataset_id)
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
    inbox = await service.list_inbox(session, user.username)
    # 결재자가 카드만 보고도 어느 데이터셋인지 알 수 있게(TODO-077)
    names = await service.dataset_names(session, [cr.dataset_id for cr, _ in inbox])
    for cr, steps in inbox:
        response = schemas.ChangeRequestResponse.model_validate(cr)
        response.dataset_name = names.get(cr.dataset_id)
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


async def _detail_response(session: AsyncSession, cr) -> schemas.ChangeRequestResponse:
    """CR 상세 응답 — 결재 단계·참조자·데이터셋 이름 포함."""
    steps = await service.list_approval_steps(session, cr.id)
    refs = await service.list_referrers(session, cr.id)
    response = schemas.ChangeRequestResponse.model_validate(cr)
    response.approval_steps = [schemas.ApprovalStepResponse.model_validate(s) for s in steps]
    response.referrers = [schemas.ReferrerResponse.model_validate(r) for r in refs]
    response.dataset_name = (await service.dataset_names(session, [cr.dataset_id])).get(cr.dataset_id)
    return response


@router.get("/{cr_id}", response_model=schemas.ChangeRequestResponse)
async def get_change_request(
    cr_id: int, session: AsyncSession = Depends(get_session)
):
    """CR 상세(결재 단계·참조자 포함). 없으면 404."""
    logger.debug("CR 상세 조회: cr_id=%d", cr_id)
    cr = await service.get_cr(session, cr_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="변경 요청을(를) 찾을 수 없습니다.")
    return await _detail_response(session, cr)


async def _own_cr(session: AsyncSession, user: TokenUser, cr_id: int):
    """요청자 본인 또는 관리자만 다루는 CR 을 읽는다 — 없으면 404, 남의 것이면 403."""
    cr = await service.get_cr(session, cr_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="변경 요청을(를) 찾을 수 없습니다.")
    assert_owner_or_admin(user, cr.requested_by, "변경 요청")
    return cr


@router.put("/{cr_id}", response_model=schemas.ChangeRequestResponse)
async def update_change_request(
    cr_id: int,
    data: schemas.ChangeRequestCreate,
    user: ChangeCreator,
    session: AsyncSession = Depends(get_session),
):
    """상신 전 초안(DRAFT) 수정 — 요청자 본인 또는 관리자. 본문은 생성과 같고 결재선·참조자까지 통째로 바꾼다.

    상신했거나 끝난 CR 은 409. 결재자·독립 결재자 검사는 생성과 같다.
    """
    cr = await _own_cr(session, user, cr_id)
    if cr.status != schemas.CRStatus.DRAFT.value or cr.workflow_id is not None:
        raise HTTPException(status_code=409, detail=f"작성 중(DRAFT)인 변경 요청만 수정할 수 있습니다 (현재={cr.status}).")
    missing = await service.unknown_approvers(session, [s.approver for s in data.approval_chain])
    if missing:
        raise HTTPException(status_code=422, detail=f"등록되지 않았거나 비활성인 결재자입니다: {', '.join(missing)}")
    await _require_independent_approver(session, user, data.dataset_id, [s.approver for s in data.approval_chain])
    await service.replace_draft_cr(session, cr, data)
    await session.commit()
    await session.refresh(cr)
    logger.info("CR 수정: cr=%s by=%s", cr.cr_code, user.username)
    return await _detail_response(session, cr)


@router.delete("/{cr_id}", status_code=204)
async def delete_change_request(user: CurrentUser, cr_id: int, session: AsyncSession = Depends(get_session)):
    """상신한 적 없는 CR(작성 중, 또는 상신 전에 취소) 삭제 — 요청자 본인 또는 관리자.

    상신한 CR 은 결재·통지 이력이 있어 지우지 않는다(409) — 취소하거나 종료한다.
    """
    cr = await _own_cr(session, user, cr_id)
    if cr.workflow_id is not None or cr.status not in (schemas.CRStatus.DRAFT.value, schemas.CRStatus.CANCELLED.value):
        raise HTTPException(status_code=409, detail="상신한 변경 요청은 이력이 남아 삭제할 수 없습니다 — 취소하거나 종료하세요.")
    code = cr.cr_code
    await service.delete_unsubmitted_cr(session, cr)
    await session.commit()
    logger.info("CR 삭제: cr=%s by=%s", code, user.username)


@router.post("/{cr_id}/close", response_model=schemas.ChangeRequestResponse)
async def close_change_request(user: CurrentUser, cr_id: int, session: AsyncSession = Depends(get_session)):
    """적용 완료(APPLIED) CR 종료 — 적용 결과를 확인한 뒤 요청자 본인 또는 관리자가 닫는다. 종료하면 롤백할 수 없다."""
    cr = await _own_cr(session, user, cr_id)
    if cr.status != schemas.CRStatus.APPLIED.value:
        raise HTTPException(status_code=409, detail=f"적용 완료(APPLIED)된 변경 요청만 종료할 수 있습니다 (현재={cr.status}).")
    if not await service.close_cr(session, cr.id):
        raise HTTPException(status_code=409, detail="변경 요청 상태가 바뀌었습니다. 새로고침 후 다시 시도하세요.")
    await session.commit()
    await session.refresh(cr)
    logger.info("CR 종료: cr=%s by=%s", cr.cr_code, user.username)
    return await _detail_response(session, cr)


@router.post("/{cr_id}/rollback", response_model=schemas.ChangeRequestResponse,
             dependencies=[Depends(require_feature("changes.rollback"))])
async def rollback_change_request(user: CurrentUser, cr_id: int, session: AsyncSession = Depends(get_session)):
    """적용한 스키마 변경 되돌리기 — 요청자 본인 또는 관리자, 적용 완료(APPLIED) CR 만.

    CR 이 건드린 컬럼만 적용 직전 상태로 되돌린다(추가한 컬럼은 삭제, 바꾼·삭제한 컬럼은 원래대로).
    되돌릴 적용 전 정보가 없으면 409. 스키마 변경 이력에 남고 알림 규칙도 평가된다.
    """
    cr = await _own_cr(session, user, cr_id)
    if cr.status != schemas.CRStatus.APPLIED.value:
        raise HTTPException(status_code=409, detail=f"적용 완료(APPLIED)된 변경 요청만 되돌릴 수 있습니다 (현재={cr.status}).")
    try:
        ups, drops = await service.rollback_cr(session, cr)
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    await session.commit()
    await session.refresh(cr)
    logger.info("CR 롤백: cr=%s dataset=%s (+%d upsert, -%d drop) by=%s", cr.cr_code, cr.dataset_id, ups, drops, user.username)
    return await _detail_response(session, cr)


# ---------------------------------------------------------------------------
# 워크플로우 트리거
# ---------------------------------------------------------------------------

def _normalize_channels(channels: list[str]) -> list[str]:
    """통지 채널을 대문자·중복 제거로 정리. 모르는 값이나 빈 목록은 400 (DEF-005)."""
    out = list(dict.fromkeys(str(c).strip().upper() for c in channels if str(c).strip()))
    unknown = [c for c in out if c not in CONSUMER_CHANNELS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 통지 채널입니다: {', '.join(unknown)} "
                                                    f"(가능: {', '.join(CONSUMER_CHANNELS)}).")
    if not out:
        raise HTTPException(status_code=400, detail="통지 채널을 하나 이상 고르세요.")
    return out


@router.post("/{cr_id}/submit", response_model=schemas.ChangeRequestSubmitResponse)
async def submit_change_request(user: CurrentUser,
    cr_id: int,
    notify_channels: list[str] = Body(default=["WEBHOOK", "EMAIL"]),
    session: AsyncSession = Depends(get_session),
):
    """결재 상신 — 요청자 본인 또는 관리자만. DRAFT 가 아니면 400, Temporal 장애 시 503.

    본문은 소비자 통지 채널 목록(WEBHOOK·EMAIL·SLACK, 기본 WEBHOOK·EMAIL). 모르는 채널이나 빈 목록은 400.
    SMS·IN_APP 은 예전 호출 호환으로 받지만 보내지 않고 이력에 ``SKIPPED`` 로 남는다(DEF-005).
    """
    notify_channels = _normalize_channels(notify_channels)
    await _require_temporal()
    cr = await service.get_cr(session, cr_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="변경 요청을(를) 찾을 수 없습니다.")
    assert_owner_or_admin(user, cr.requested_by, "변경 요청")
    if cr.status != schemas.CRStatus.DRAFT.value:
        logger.warning("CR 상신 거부(DRAFT 아님): cr_id=%d status=%s", cr_id, cr.status)
        raise HTTPException(status_code=400, detail=f"변경 요청은(는) DRAFT 상태여야 합니다 (현재={cr.status}).")
    # 이 검사 전에 만든 초안도 상신 때 막는다(요청자 본인이 상신하므로 user 가 요청자이거나 관리자)
    steps = await service.list_approval_steps(session, cr.id)
    await _require_independent_approver(session, user, cr.dataset_id, [s.approver for s in steps])

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
    """데이터셋 소비자를 등록한다(``changes.consumers`` 기능 권한 + 그 데이터셋의 소유자 또는 관리자).

    소비자에는 연락 이메일 · 웹훅 주소가 들어가 변경 내용이 그쪽으로 나간다 — 남의 데이터셋에 임의의 수신처를
    붙이지 못하게 데이터셋 소유자(생성자)로 묶는다(DEF-121). 없는 데이터셋은 404."""
    if not _guard.is_admin:
        exists, owner = await service.dataset_creator(session, data.dataset_id)
        if not exists:
            raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
        assert_owner_or_admin(_guard, owner, "데이터셋")
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
    """통지 수신 확인(ACK) — 상태를 ACKED 로 바꾸고 시각·코멘트를 기록한다. 없으면 404.

    수신자 본인 · 변경 요청자 · 데이터셋 소유자 · 관리자만 할 수 있다(그 밖은 403, DEF-122)."""
    from app.change_mgmt import models as cm_models

    existing = await session.get(cm_models.NotificationLog, log_id)
    if existing is None:
        logger.warning("통지 ACK 실패(없음): log_id=%d", log_id)
        raise HTTPException(status_code=404, detail="통지 이력을(를) 찾을 수 없습니다.")
    if not await service.can_ack_notification(session, existing, _guard):
        logger.warning("통지 ACK 거부(403): log_id=%d user=%s", log_id, _guard.username)
        raise HTTPException(status_code=403, detail="통지의 수신자, 변경 요청자, 데이터셋 소유자 또는 관리자만 수신 확인할 수 있습니다.")
    log = await service.ack_notification(session, log_id, comment)
    if log is None:
        raise HTTPException(status_code=404, detail="통지 이력을(를) 찾을 수 없습니다.")
    await session.commit()
    logger.info("통지 ACK: log_id=%d cr_id=%s by=%s", log_id, log.cr_id, _guard.username)
    return schemas.NotificationLogResponse.model_validate(log)
