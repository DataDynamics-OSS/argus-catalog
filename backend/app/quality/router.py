# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 API 엔드포인트.

프로파일링, 품질 규칙 CRUD, 검사 실행, 점수 추적, 외부(품질 배치) 결과 반입을 제공한다.
프로파일은 원천 DB 에 직접 SQL(Method A)을 실행하고, 실패 시 스키마 기반(Method B)으로 폴백한다.
ACCEPTED_VALUES·REGEX·CUSTOM_SQL·CUSTOM_PYTHON 은 서버가 평가하지 않고, 품질 배치
(quality/python-quality.py, quality/dataset-quality.py)가 원본 전체 데이터로 평가해 반입한 결과를 쓴다.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, TokenUser
from app.core.database import get_session
from app.permissions.router import require_feature
from app.quality import service
from app.quality.schemas import (
    ProfileImportRequest,
    ProfileResponse,
    QualityResultResponse,
    QualityRuleCreate,
    QualityRuleResponse,
    QualityRuleUpdate,
    QualityScoreResponse,
    ResultsImportRequest,
    RunCheckResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quality", tags=["quality"])

# 기능 권한(권한 관리 › 기능 권한) — 미설정이면 로그인 사용자 모두 허용(open-by-default)
RuleManager = Annotated[TokenUser, Depends(require_feature("quality.rules-manage"))]
CheckRunner = Annotated[TokenUser, Depends(require_feature("quality.run"))]
ScheduleManager = Annotated[TokenUser, Depends(require_feature("quality.schedule"))]

DATASET_NOT_FOUND = "데이터셋을 찾을 수 없습니다."


# ---------------------------------------------------------------------------
# 프로파일링
# ---------------------------------------------------------------------------

@router.post("/datasets/{dataset_id}/profile", response_model=ProfileResponse)
async def profile_dataset(_guard: CheckRunner, dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋에 대해 데이터 프로파일링을 실행한다.

    데이터소스 설정을 통해 원천 DB 에 접속해 컬럼 단위 통계
    (NULL 수, 고유값 수, 최소/최대, 평균)를 수집한다.
    원천 DB 에 도달할 수 없으면 스키마 기반 프로파일로 폴백한다.
    """
    try:
        result = await service.profile_dataset(session, dataset_id)
        await session.commit()
        return result
    except ValueError as e:
        logger.warning("프로파일링 대상 데이터셋 없음(404): dataset_id=%d", dataset_id)
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/datasets/{dataset_id}/profile", response_model=ProfileResponse)
async def get_profile(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋의 최신 프로파일을 조회한다."""
    logger.debug("GET 프로파일: dataset_id=%d", dataset_id)
    result = await service.get_latest_profile(session, dataset_id)
    if not result:
        raise HTTPException(status_code=404, detail="프로파일을 찾을 수 없습니다. 먼저 프로파일링을 실행하십시오.")
    return result


# ---------------------------------------------------------------------------
# 품질 규칙
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/upstream-quality")
async def get_upstream_quality(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """업스트림(원천) 데이터셋 중 품질 상태가 WARN/BAD 인 것 — 품질 전파 경고.

    이 데이터셋을 target 으로 하는 리니지의 source 데이터셋을 조회한다.
    원천 품질이 나쁘면 파생 데이터도 의심해야 한다는 신호를 준다.
    """
    from sqlalchemy import text as sql_text

    # 이 데이터셋이 target 인 리니지 엣지의 source 중 품질 WARN/BAD 만 — 같은 source 가
    # 여러 relation 으로 이어지면 relation_type 별로 한 행씩 나온다(DISTINCT 는 전체 컬럼 기준).
    rows = (await session.execute(sql_text("""
        SELECT DISTINCT d.id, d.name, d.urn, d.quality_status, d.quality_score, l.relation_type
        FROM argus_dataset_lineage l
        JOIN catalog_datasets d ON d.id = l.source_dataset_id
        WHERE l.target_dataset_id = :did
          AND d.quality_status IN ('WARN', 'BAD')
    """), {"did": dataset_id})).fetchall()
    logger.debug("업스트림 품질 경고 조회: dataset_id=%d count=%d", dataset_id, len(rows))
    return [
        {"id": r[0], "name": r[1], "urn": r[2], "quality_status": r[3],
         "quality_score": float(r[4]) if r[4] is not None else None,
         "relation_type": r[5]}
        for r in rows
    ]


@router.get("/datasets/{dataset_id}/rules/recommendations")
async def recommend_rules(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """프로파일 기반 품질 규칙 추천 후보 (생성은 별도 — 기존 규칙과 중복 제외)."""
    logger.debug("GET 규칙 추천: dataset_id=%d", dataset_id)
    return await service.recommend_rules(session, dataset_id)


@router.get("/datasets/{dataset_id}/schedule")
async def get_quality_schedule(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋의 품질 검증 주기 설정 조회 (없으면 null)."""
    from sqlalchemy import select

    from app.catalog.models import DatasetProperty
    from app.quality.scheduler import QUALITY_SCHEDULE_PROPERTY

    value = (await session.execute(
        select(DatasetProperty.property_value).where(
            DatasetProperty.dataset_id == dataset_id,
            DatasetProperty.property_key == QUALITY_SCHEDULE_PROPERTY,
        )
    )).scalar()
    return {"schedule": value}


@router.put("/datasets/{dataset_id}/schedule")
async def set_quality_schedule(_guard: ScheduleManager,
    dataset_id: int, req: dict, session: AsyncSession = Depends(get_session),
):
    """데이터셋의 품질 검증 주기 설정 (hourly/daily/weekly, null 이면 해제).

    백그라운드 스케줄러가 10분 간격으로 주기 도래 여부를 확인해
    서버 검증(run_quality_check)을 자동 실행한다.
    """
    from sqlalchemy import delete as sql_delete
    from sqlalchemy import select

    from app.catalog.models import DatasetProperty
    from app.quality.scheduler import INTERVALS, QUALITY_SCHEDULE_PROPERTY

    schedule = req.get("schedule")
    if schedule is not None and schedule not in INTERVALS:
        logger.warning("지원하지 않는 품질 검증 주기 거부(422): dataset_id=%d schedule=%r", dataset_id, schedule)
        raise HTTPException(status_code=422, detail=f"지원하지 않는 주기: {schedule} (hourly/daily/weekly)")
    # 존재 확인 — 없으면 FK 위반이 409 로 오인 응답되므로 먼저 404
    if not await service.dataset_exists(session, dataset_id):
        logger.warning("품질 주기 설정 대상 데이터셋 없음(404): dataset_id=%d", dataset_id)
        raise HTTPException(status_code=404, detail=DATASET_NOT_FOUND)

    if schedule is None:
        await session.execute(sql_delete(DatasetProperty).where(
            DatasetProperty.dataset_id == dataset_id,
            DatasetProperty.property_key == QUALITY_SCHEDULE_PROPERTY,
        ))
    else:
        row = (await session.execute(select(DatasetProperty).where(
            DatasetProperty.dataset_id == dataset_id,
            DatasetProperty.property_key == QUALITY_SCHEDULE_PROPERTY,
        ))).scalar_one_or_none()
        if row:
            row.property_value = schedule
        else:
            session.add(DatasetProperty(
                dataset_id=dataset_id,
                property_key=QUALITY_SCHEDULE_PROPERTY,
                property_value=schedule,
            ))
    await session.commit()
    logger.info("품질 검증 주기 갱신됨: dataset_id=%d, schedule=%s", dataset_id, schedule)
    return {"schedule": schedule}


# ---------------------------------------------------------------------------
# 품질 설정 — 원천 데이터 시간대(FRESHNESS 의 시간대 없는 시각 해석)
# ---------------------------------------------------------------------------

class QualitySettings(BaseModel):
    """품질 설정 — 시간대 없는 시각 값을 해석할 원천 데이터 시간대."""
    source_timezone: str = service.DEFAULT_SOURCE_TIMEZONE


@router.get("/settings", response_model=QualitySettings)
async def get_quality_settings(session: AsyncSession = Depends(get_session)):
    """품질 설정 조회 — ``source_timezone``: 시간대 없는 시각 값(MySQL DATETIME 등)을 해석할 원천 데이터 시간대.

    품질 배치(quality/*.py)는 같은 값을 ``--source-timezone``(기본 Asia/Seoul)으로 받는다.
    """
    return QualitySettings(source_timezone=await service.get_source_timezone_name(session))


@router.put("/settings", response_model=QualitySettings)
async def update_quality_settings(
    body: QualitySettings, _admin: AdminUser, session: AsyncSession = Depends(get_session),
):
    """품질 설정 저장(관리자). 시간대는 IANA 이름(Asia/Seoul·UTC) 또는 +09:00 형식 — 아니면 422."""
    from app.settings.service import update_config

    name = body.source_timezone.strip()
    try:
        service.resolve_timezone(name)
    except ValueError as e:
        logger.warning("잘못된 원천 데이터 시간대 거부(422): value=%r", name)
        raise HTTPException(status_code=422, detail=str(e))
    await update_config(session, service.QUALITY_CONFIG_CATEGORY, {service.SOURCE_TIMEZONE_KEY: name}, actor=_admin)
    logger.info("품질 설정 변경: source_timezone=%s", name)
    return QualitySettings(source_timezone=name)


@router.post("/rules", response_model=QualityRuleResponse, status_code=201)
async def create_rule(_guard: RuleManager, data: QualityRuleCreate, session: AsyncSession = Depends(get_session)):
    """품질 검사 규칙을 생성한다."""
    if not await service.dataset_exists(session, data.dataset_id):
        logger.warning("규칙 생성 대상 데이터셋 없음(404): dataset_id=%d", data.dataset_id)
        raise HTTPException(status_code=404, detail=DATASET_NOT_FOUND)
    result = await service.create_rule(session, data)
    await session.commit()
    return result


@router.get("/rules", response_model=list[QualityRuleResponse])
async def list_rules(dataset_id: int = Query(...), session: AsyncSession = Depends(get_session)):
    """데이터셋의 품질 규칙 목록을 조회한다."""
    logger.debug("GET 규칙 목록: dataset_id=%d", dataset_id)
    return await service.list_rules(session, dataset_id)


@router.put("/rules/{rule_id}", response_model=QualityRuleResponse)
async def update_rule(_guard: RuleManager, rule_id: int, data: QualityRuleUpdate, session: AsyncSession = Depends(get_session)):
    """품질 규칙을 수정한다."""
    result = await service.update_rule(session, rule_id, data)
    if not result:
        logger.warning("수정할 품질 규칙 없음(404): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을(를) 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(_guard: RuleManager, rule_id: int, session: AsyncSession = Depends(get_session)):
    """품질 규칙을 삭제한다."""
    if not await service.delete_rule(session, rule_id):
        logger.warning("삭제할 품질 규칙 없음(404): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을(를) 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 검사 실행
# ---------------------------------------------------------------------------

@router.post("/datasets/{dataset_id}/check", response_model=RunCheckResponse)
async def run_check(_guard: CheckRunner, dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋의 모든 활성 품질 규칙을 실행한다.

    기존 프로파일이 없으면 자동으로 프로파일링한 뒤(있으면 재사용), 프로파일 데이터에
    대해 각 활성 규칙을 평가한다.
    규칙별 결과와 품질 점수를 반환한다. 서버가 평가할 수 없는 유형은 직전(배치) 결과를
    이어받거나(``carried_forward``) 미평가(``passed: null``)로 돌려주며, 미평가는 점수 분모에서 빠진다.
    """
    try:
        result = await service.run_quality_check(session, dataset_id)
    except ValueError:
        # 없는 데이터셋 — 미처리 시 500
        logger.warning("검증 대상 데이터셋 없음(404): dataset_id=%d", dataset_id)
        raise HTTPException(status_code=404, detail=DATASET_NOT_FOUND) from None
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# 결과 & 점수
# ---------------------------------------------------------------------------

@router.post("/datasets/{dataset_id}/profile/import", response_model=ProfileResponse)
async def import_profile(
    dataset_id: int,
    req: ProfileImportRequest,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """외부 엔진(PySpark 등)이 계산한 프로파일을 반입한다. 관리자 전용.

    quality/dataset-quality.py 같은 외부 배치가 원본 전체 데이터로 계산한
    통계를 서버 프로파일과 동일한 형식으로 저장한다.
    """
    try:
        result = await service.import_profile(session, dataset_id, req)
        await session.commit()
        return result
    except ValueError as e:
        logger.warning("프로파일 반입 대상 데이터셋 없음(404): dataset_id=%d", dataset_id)
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/datasets/{dataset_id}/results/import", response_model=RunCheckResponse)
async def import_results(
    dataset_id: int,
    req: ResultsImportRequest,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """외부 엔진이 평가한 규칙 결과를 반입한다. 관리자 전용.

    반입한 결과 행을 저장하고, 서버 검증(run_quality_check)과 같은 시맨틱으로 점수를 기록한다 —
    데이터셋의 활성 규칙 전체 기준(반입에 없는 활성 규칙은 직전 결과 이어받기, 없으면 미평가로 제외).
    빈 목록·같은 rule_id 중복은 422(스키마), 다른 데이터셋·없는·비활성 규칙은 400.
    """
    try:
        result = await service.import_results(session, dataset_id, req)
        await session.commit()
        return result
    except ValueError as e:
        logger.warning("품질 결과 반입 거부(400): dataset_id=%d reason=%s", dataset_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/datasets/{dataset_id}/results", response_model=list[QualityResultResponse])
async def get_results(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋의 최신 품질 검사 결과를 조회한다.

    활성 규칙별 가장 최근 결과. 서버가 평가할 수 없는 유형인데 결과가 없으면 미평가(``passed: null``) 항목.
    """
    logger.debug("GET 최신 결과: dataset_id=%d", dataset_id)
    return await service.get_latest_results(session, dataset_id)


@router.get("/datasets/{dataset_id}/score", response_model=QualityScoreResponse)
async def get_score(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋의 최신 품질 점수를 조회한다."""
    logger.debug("GET 최신 점수: dataset_id=%d", dataset_id)
    result = await service.get_latest_score(session, dataset_id)
    if not result:
        raise HTTPException(status_code=404, detail="점수를 찾을 수 없습니다. 먼저 품질 검사를 실행하십시오.")
    return result


@router.get("/datasets/{dataset_id}/score/history", response_model=list[QualityScoreResponse])
async def get_score_history(
    dataset_id: int,
    limit: int = Query(30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 품질 점수 이력을 조회한다(오래된 순, 최근 ``limit`` 건)."""
    logger.debug("GET 점수 이력: dataset_id=%d limit=%d", dataset_id, limit)
    return await service.get_score_history(session, dataset_id, limit)
