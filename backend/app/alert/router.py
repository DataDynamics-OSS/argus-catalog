"""리니지 변경 알림 API 엔드포인트.

엔드포인트:
- GET    /alerts/summary              벨 배지용 미해결 알림 건수
- GET    /alerts                      알림 목록 (필터: status, severity, dataset_id)
- GET    /alerts/{id}                 알림 상세
- PUT    /alerts/{id}/status          알림 상태 변경
- POST   /alerts/rules                알림 규칙 생성
- GET    /alerts/rules                알림 규칙 목록
- GET    /alerts/rules/{id}           알림 규칙 상세
- PUT    /alerts/rules/{id}           알림 규칙 수정
- DELETE /alerts/rules/{id}           알림 규칙 삭제
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.alert import service
from app.alert.schemas import (
    AlertResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    AlertSummary,
    AlertUpdateStatus,
    PaginatedAlerts,
)
from app.core.auth import AdminUser
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


# ---------------------------------------------------------------------------
# 알림 요약 (벨 배지용)
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=AlertSummary)
async def get_alert_summary(session: AsyncSession = Depends(get_session)):
    """미해결(OPEN) 알림의 심각도별 건수를 반환."""
    return await service.get_alert_summary(session)


# ---------------------------------------------------------------------------
# Alert Rule CRUD
# ---------------------------------------------------------------------------

@router.post("/rules", response_model=AlertRuleResponse, status_code=201)
async def create_rule(_guard: AdminUser,
    data: AlertRuleCreate, session: AsyncSession = Depends(get_session),
):
    """알림 규칙을 생성한다."""
    result = await service.create_rule(session, data)
    await session.commit()
    logger.info("알림 규칙 생성됨: id=%d, name=%s", result.id, result.name)
    return result


@router.get("/rules", response_model=list[AlertRuleResponse])
async def list_rules(session: AsyncSession = Depends(get_session)):
    """알림 규칙 목록을 조회한다."""
    return await service.list_rules(session)


@router.get("/rules/{rule_id}", response_model=AlertRuleResponse)
async def get_rule(rule_id: int, session: AsyncSession = Depends(get_session)):
    """알림 규칙 단건 조회. 적용 범위 이름과 누적 알림 건수가 함께 채워진다."""
    rule = await service.get_rule(session, rule_id)
    if not rule:
        logger.warning("규칙 조회 실패(없음): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을(를) 찾을 수 없습니다.")
    return await service._build_rule_response(session, rule)


@router.put("/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_rule(_guard: AdminUser,
    rule_id: int, data: AlertRuleUpdate, session: AsyncSession = Depends(get_session),
):
    """알림 규칙 부분 갱신. ``is_active`` 토글로 일시 정지/재개가 가능하다."""
    result = await service.update_rule(session, rule_id, data)
    if not result:
        logger.warning("규칙 수정 실패(없음): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을(를) 찾을 수 없습니다.")
    await session.commit()
    logger.info("알림 규칙 수정됨: id=%d", rule_id)
    return result


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(_guard: AdminUser, rule_id: int, session: AsyncSession = Depends(get_session)):
    """알림 규칙 삭제. 이미 발생한 알림 행의 ``rule_id`` 는 NULL 로 떨어진다(SET NULL)."""
    deleted = await service.delete_rule(session, rule_id)
    if not deleted:
        logger.warning("규칙 삭제 실패(없음): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을(를) 찾을 수 없습니다.")
    await session.commit()
    logger.info("알림 규칙 삭제됨: id=%d", rule_id)


# ---------------------------------------------------------------------------
# Alert CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=PaginatedAlerts)
async def list_alerts(
    status: str | None = Query(None),
    severity: str | None = Query(None),
    dataset_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """알림 목록을 조회한다."""
    return await service.list_alerts(session, status, severity, dataset_id, page, page_size)


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: int, session: AsyncSession = Depends(get_session)):
    """알림 단건 조회. 출처·영향 데이터셋 이름과 규칙 이름이 함께 채워진다."""
    alert = await service.get_alert(session, alert_id)
    if not alert:
        logger.warning("알림 조회 실패(없음): alert_id=%d", alert_id)
        raise HTTPException(status_code=404, detail="알림을(를) 찾을 수 없습니다.")
    return await service._build_alert_response(session, alert)


@router.put("/{alert_id}/status", response_model=AlertResponse)
async def update_alert_status(_guard: AdminUser,
    alert_id: int,
    data: AlertUpdateStatus,
    session: AsyncSession = Depends(get_session),
):
    """알림 상태 전이 (OPEN → ACKNOWLEDGED/RESOLVED/DISMISSED)."""
    result = await service.update_alert_status(session, alert_id, data)
    if not result:
        logger.warning("알림 상태 변경 실패(없음): alert_id=%d", alert_id)
        raise HTTPException(status_code=404, detail="알림을(를) 찾을 수 없습니다.")
    await session.commit()
    logger.info("알림 상태 변경됨: id=%d → %s", alert_id, data.status)
    return result
