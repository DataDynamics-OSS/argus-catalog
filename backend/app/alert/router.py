# SPDX-License-Identifier: Apache-2.0
"""리니지 변경 알림 API 엔드포인트.

엔드포인트(모두 로그인 필요):
- GET    /alerts/summary              벨 배지용 미해결 알림 건수
- GET    /alerts                      알림 목록 (필터: status, severity, alert_type, dataset_id)
- GET    /alerts/{id}                 알림 상세
- PUT    /alerts/{id}/status          알림 상태 변경 — 규칙 관리자 또는 관련 데이터셋 생성자·소유자
- POST   /alerts/bulk-status          알림 상태 일괄 변경 (권한 없는 항목은 건너뜀)
- DELETE /alerts/closed               오래된 해결·무시 알림 정리 — admin
- POST   /alerts/rules                알림 규칙 생성 — ``alerts.rules`` 권한(명시 허용형)
- GET    /alerts/rules                알림 규칙 목록 (Webhook URL 은 규칙 관리자에게만)
- GET    /alerts/rules/{id}           알림 규칙 상세
- PUT    /alerts/rules/{id}           알림 규칙 수정 — ``alerts.rules``
- DELETE /alerts/rules/{id}           알림 규칙 삭제 — ``alerts.rules``
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.alert import service
from app.alert.schemas import (
    AlertBulkResult,
    AlertBulkStatus,
    AlertPurgeResult,
    AlertResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    AlertSummary,
    AlertUpdateStatus,
    PaginatedAlerts,
)
from app.core.auth import AdminUser, CurrentUser, TokenUser
from app.core.database import get_session
from app.permissions.router import check_feature_granted, require_feature_granted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])

RULES_FEATURE = "alerts.rules"
RuleManager = Depends(require_feature_granted(RULES_FEATURE))


async def _is_manager(session: AsyncSession, current: TokenUser) -> bool:
    """현재 사용자가 ``alerts.rules`` 권한(규칙 관리자, admin 포함)을 가졌는지 반환한다."""
    return await check_feature_granted(session, current, RULES_FEATURE)


# ---------------------------------------------------------------------------
# 알림 요약 (벨 배지용)
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=AlertSummary)
async def get_alert_summary(_current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """미해결(OPEN) 알림의 심각도별 건수를 반환."""
    logger.debug("알림 요약 조회")
    return await service.get_alert_summary(session)


# ---------------------------------------------------------------------------
# Alert Rule CRUD
# ---------------------------------------------------------------------------

@router.post("/rules", response_model=AlertRuleResponse, status_code=201)
async def create_rule(
    data: AlertRuleCreate,
    current: TokenUser = RuleManager,
    session: AsyncSession = Depends(get_session),
):
    """알림 규칙을 생성한다. 생성자는 로그인 사용자로 기록된다."""
    try:
        result = await service.create_rule(session, data, created_by=current.username)
    except service.RuleValidationError as e:
        logger.warning("알림 규칙 생성 거부(검증 실패): by=%s reason=%s", current.username, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    logger.info("알림 규칙 생성됨: id=%d, name=%s, by=%s", result.id, result.rule_name, current.username)
    return result


@router.get("/rules", response_model=list[AlertRuleResponse])
async def list_rules(current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """알림 규칙 목록을 조회한다. Webhook URL 은 규칙 관리자에게만 노출한다."""
    logger.debug("알림 규칙 목록 조회: user=%s", current.username)
    return await service.list_rules(session, reveal_webhook=await _is_manager(session, current))


@router.get("/rules/{rule_id}", response_model=AlertRuleResponse)
async def get_rule(rule_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """알림 규칙 단건 조회. 적용 범위 이름과 누적 알림 건수가 함께 채워진다."""
    rule = await service.get_rule(session, rule_id)
    if not rule:
        logger.warning("규칙 조회 실패(없음): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을 찾을 수 없습니다.")
    return await service.get_rule_response(session, rule, reveal_webhook=await _is_manager(session, current))


@router.put("/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_rule(
    rule_id: int, data: AlertRuleUpdate,
    current: TokenUser = RuleManager,
    session: AsyncSession = Depends(get_session),
):
    """알림 규칙 부분 갱신. ``is_active`` 토글로 일시 정지/재개가 가능하다."""
    try:
        result = await service.update_rule(session, rule_id, data)
    except service.RuleValidationError as e:
        logger.warning("알림 규칙 수정 거부(검증 실패): rule_id=%d reason=%s", rule_id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not result:
        logger.warning("규칙 수정 실패(없음): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을 찾을 수 없습니다.")
    await session.commit()
    logger.info("알림 규칙 수정됨: id=%d, by=%s", rule_id, current.username)
    return result


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    current: TokenUser = RuleManager,
    session: AsyncSession = Depends(get_session),
):
    """알림 규칙 삭제. 이미 발생한 알림 행의 ``rule_id`` 는 NULL 로 떨어진다(SET NULL)."""
    deleted = await service.delete_rule(session, rule_id)
    if not deleted:
        logger.warning("규칙 삭제 실패(없음): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을 찾을 수 없습니다.")
    await session.commit()
    logger.info("알림 규칙 삭제됨: id=%d, by=%s", rule_id, current.username)


# ---------------------------------------------------------------------------
# Alert 조회·상태
# ---------------------------------------------------------------------------

@router.get("", response_model=PaginatedAlerts)
async def list_alerts(
    current: CurrentUser,
    status: str | None = Query(None),
    severity: str | None = Query(None),
    dataset_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    alert_type: str | None = Query(None),
    entity_type: str | None = Query(None, description="데이터셋이 아닌 엔터티 알림 (예: mcp_deployment)"),
    entity_ref: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """알림 목록을 조회한다. 항목별 ``can_update`` 로 상태 변경 가능 여부를 알려준다."""
    logger.debug(
        "알림 목록 조회: status=%s severity=%s dataset_id=%s alert_type=%s entity_type=%s page=%d",
        status, severity, dataset_id, alert_type, entity_type, page,
    )
    return await service.list_alerts(
        session, status, severity, dataset_id, page, page_size,
        alert_type=alert_type, entity_type=entity_type, entity_ref=entity_ref,
        current=current, is_manager=await _is_manager(session, current),
    )


@router.post("/bulk-status", response_model=AlertBulkResult)
async def bulk_update_status(
    data: AlertBulkStatus, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """여러 알림의 상태를 한 번에 바꾼다. 권한 없음·전이 불가 항목은 사유와 함께 건너뛴다."""
    result = await service.bulk_update_status(
        session, data.ids, data.status.value, current, await _is_manager(session, current),
    )
    await session.commit()
    logger.info("알림 상태 일괄 변경: → %s, updated=%d, skipped=%d, by=%s",
                data.status.value, len(result.updated), len(result.skipped), current.username)
    return result


@router.delete("/closed", response_model=AlertPurgeResult)
async def purge_closed_alerts(
    _admin: AdminUser,
    older_than_days: int = Query(90, ge=1, le=3650),
    session: AsyncSession = Depends(get_session),
):
    """해결·무시 처리된 지 N일이 지난 알림과 전달 기록을 삭제한다(보관 정리)."""
    deleted = await service.purge_closed_alerts(session, older_than_days)
    await session.commit()
    return AlertPurgeResult(deleted=deleted)


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """알림 단건 조회. 출처·영향 데이터셋 이름과 규칙 이름이 함께 채워진다."""
    alert = await service.get_alert(session, alert_id)
    if not alert:
        logger.warning("알림 조회 실패(없음): alert_id=%d", alert_id)
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    return await service.build_alert_response(
        session, alert, current=current, is_manager=await _is_manager(session, current),
    )


@router.put("/{alert_id}/status", response_model=AlertResponse)
async def update_alert_status(
    alert_id: int,
    data: AlertUpdateStatus,
    current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """알림 상태 전이. 규칙 관리자(admin 포함) 또는 관련 데이터셋의 생성자·소유자만 가능.

    허용 전이: 미확인 → 확인·해결·무시, 확인 → 해결·무시·다시 열기, 해결·무시 → 다시 열기.
    """
    alert = await service.get_alert(session, alert_id)
    if not alert:
        logger.warning("알림 상태 변경 실패(없음): alert_id=%d", alert_id)
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    is_manager = await _is_manager(session, current)
    if not await service.can_update_alert(session, alert, current, is_manager):
        logger.warning("알림 상태 변경 거부(권한 없음): alert_id=%d user=%s", alert_id, current.username)
        raise HTTPException(
            status_code=403,
            detail="알림 상태를 바꿀 권한이 없습니다(규칙 관리자 또는 관련 데이터셋 소유자만 가능).",
        )
    try:
        service.apply_status(alert, data.status.value, current.username)
    except service.AlertTransitionError as e:
        logger.warning("알림 상태 전이 충돌: alert_id=%d → %s reason=%s",
                       alert_id, data.status.value, e)
        raise HTTPException(status_code=409, detail=str(e)) from e
    await session.flush()
    await session.commit()
    await session.refresh(alert)
    return await service.build_alert_response(session, alert, current=current, is_manager=is_manager)
