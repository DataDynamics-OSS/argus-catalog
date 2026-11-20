# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 API 엔드포인트.

품질 규칙 · 지표 CRUD, 진단 · 프로파일 실행(품질 워커 대기열), 결과 · 점수 조회, 품질 진단 작업, 품질 워커 API.
진단과 프로파일은 모두 품질 워커가 한다 — Trino 연결이 있으면 Trino, 없으면 원천 DB 를 직접 읽는다(TODO-095).
"""

import asyncio
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, PermissionDenied, TokenUser
from app.core.database import get_session
from app.permissions.router import (
    check_feature,
    check_feature_granted,
    require_feature,
    require_feature_granted,
)
from app.quality import definitions, execution, jobs, report_pdf, reports, runs, service
from app.quality import indicators as quality_indicators
from app.quality.schemas import (
    ProfileResponse,
    QualityAssignment,
    QualityAssignRequest,
    QualityAssignResult,
    QualityCodeGroupOption,
    QualityColumnCandidate,
    QualityDefinitionResponse,
    QualityDefinitionWrite,
    QualityEngineStatus,
    QualityIndicatorResponse,
    QualityJobIndicatorRules,
    QualityJobPreview,
    QualityJobPreviewRequest,
    QualityJobResponse,
    QualityJobRulesRequest,
    QualityJobRunReport,
    QualityJobRunResponse,
    QualityJobTrend,
    QualityJobWrite,
    QualityResultResponse,
    QualityRuleCreate,
    QualityRuleResponse,
    QualityRuleUpdate,
    QualityRunCreate,
    QualityRunQuery,
    QualityRunResponse,
    QualitySchemaOption,
    QualityScoreResponse,
    QualityServiceStatus,
    QualitySqlPreview,
    QualitySqlPreviewRequest,
    QualityUnassignRequest,
    QualityWorkerResponse,
    WorkerComplete,
    WorkerCompleteResponse,
    WorkerFinish,
    WorkerHeartbeat,
    WorkerIdentity,
    WorkerSamples,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quality", tags=["quality"])

# 기능 권한(권한 관리 › 기능 권한) — 미설정이면 로그인 사용자 모두 허용(open-by-default)
RuleManager = Annotated[TokenUser, Depends(require_feature("quality.rules-manage"))]
CheckRunner = Annotated[TokenUser, Depends(require_feature("quality.run"))]
# 관리 > 데이터 품질 메뉴의 API — 메뉴와 같은 기준(명시 허용형 quality.manage): 관리자이거나 이 권한을 명시적으로 받은 역할만.
# 예전에는 기본 허용형인 quality.run 을 메뉴 권한으로도 써서, 전 역할에 진단 실행을 허용하면(행이 지워져) 메뉴가 모두에게
# 닫히는 문제가 있었다(DEF-128). 데이터셋 품질 탭이 쓰는 API(진단 실행·취소, 규칙·지표 조회)는 위의 기본 허용형 가드를 쓴다.
QUALITY_MANAGE = "quality.manage"
MenuUser = Annotated[TokenUser, Depends(require_feature_granted(QUALITY_MANAGE))]
_MENU = [Depends(require_feature_granted(QUALITY_MANAGE))]


async def _worker_key(user: AdminUser) -> TokenUser:
    """품질 워커 API 는 관리자 역할의 **API 키** 만 — Trino 접속 정보를 돌려주므로 브라우저 세션으로는 부르지 못한다."""
    if not user.is_api_key:
        raise HTTPException(status_code=403, detail="품질 워커 API 는 API 키로만 호출할 수 있습니다.")
    return user


WorkerKey = Annotated[TokenUser, Depends(_worker_key)]


async def _own_worker(session: AsyncSession, user: TokenUser, worker_id: str) -> None:
    """이 워커 ID 가 다른 API 키의 것이면 403."""
    try:
        await runs.assert_worker_owner(session, worker_id, user.sub)
    except runs.WorkerOwnerError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


async def _require_schedule(session: AsyncSession, user: TokenUser, req: QualityJobWrite) -> None:
    """주기가 있는 작업을 저장하려면 주기 설정 권한(quality.schedule)도 필요하다."""
    if req.schedule_type != "NONE" and not await check_feature(session, user, "quality.schedule"):
        raise PermissionDenied("기능 권한이 없습니다: quality.schedule", user=user, kind="FEATURE", key="quality.schedule")

DATASET_NOT_FOUND = "데이터셋을 찾을 수 없습니다."


async def _require_dataset_owner(session: AsyncSession, user: TokenUser, dataset_ids) -> None:
    """데이터셋의 품질을 바꾸거나 진단을 돌리는 일은 그 데이터셋의 소유자(생성자 또는 소유자 목록) 또는 관리자만 — 아니면 403.

    데이터셋 수정과 같은 기준(``app.catalog.dataset_owner``, TODO-154)이다. 기능 권한(기본 허용형)은 그대로 먼저 검사하고, 이 검사가
    그 위에 더해진다. 없는 데이터셋은 건너뛴다(뒤 단계의 404 에 맡긴다). 생성자가 비어 있는 데이터셋은 관리자만.
    """
    if user.is_admin:
        return
    from sqlalchemy import select

    from app.catalog.dataset_owner import managed_dataset_ids
    from app.catalog.models import Dataset

    ids = sorted({int(i) for i in dataset_ids})
    if not ids:
        return
    existing = set((await session.execute(select(Dataset.id).where(Dataset.id.in_(ids)))).scalars().all())
    denied = sorted(existing - await managed_dataset_ids(session, user.username, existing))
    if denied:
        logger.warning("품질 변경 거부(403): user=%s dataset_ids=%s", user.username, denied)
        raise PermissionDenied("데이터셋의 소유자 또는 관리자만 할 수 있습니다.", user=user, kind="OWNER", key="데이터셋 소유자")


async def _datasets_of_rules(session: AsyncSession, rule_ids) -> list[int]:
    from sqlalchemy import select

    from app.quality.models import QualityRule

    return list((await session.execute(
        select(QualityRule.dataset_id).where(QualityRule.id.in_(list(rule_ids))).distinct())).scalars().all())


async def _dataset_of_run(session: AsyncSession, run_id: int) -> list[int]:
    from sqlalchemy import select

    from app.quality.models import QualityRun

    found = (await session.execute(select(QualityRun.dataset_id).where(QualityRun.id == run_id))).scalar_one_or_none()
    return [found] if found is not None else []


# ---------------------------------------------------------------------------
# 프로파일 — 실행은 POST /datasets/{id}/runs 에 {"kind": "PROFILE"}
# ---------------------------------------------------------------------------

SAMPLE_VIEW_FEATURE = "datasets.sample-view"


@router.get("/datasets/{dataset_id}/profile", response_model=ProfileResponse)
async def get_profile(dataset_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """데이터셋의 최신 프로파일을 조회한다.

    최솟값 · 최댓값 · 최빈값은 실제 데이터 값이라 샘플 보기 권한(``datasets.sample-view``)이 있을 때만 들어간다.
    없으면 그 값을 빼고 ``values_hidden: true`` — 행 수 · NULL · 고유 비율 · 평균 같은 통계는 그대로(DEF-164).
    """
    logger.debug("GET 프로파일: dataset_id=%d", dataset_id)
    result = await service.get_latest_profile(session, dataset_id)
    if not result:
        raise HTTPException(status_code=404, detail="프로파일을 찾을 수 없습니다. 먼저 프로파일링을 실행하십시오.")
    if not await check_feature(session, current, SAMPLE_VIEW_FEATURE):
        logger.info("프로파일 값 숨김(샘플 보기 권한 없음): user=%s dataset_id=%d", current.username, dataset_id)
        result = result.model_copy(update={
            "values_hidden": True,
            "columns": [c.model_copy(update={"min_value": None, "max_value": None, "top_values": None})
                        for c in result.columns],
        })
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
    # 데이터셋 행의 점수는 정수로 반올림해 둔 값(33.3 → 33)이라 품질 탭 · 점수 이력과 어긋난다 — 최신 점수 기록의
    # 소수 한 자리 값을 쓴다(DEF-150). 점수 기록이 없으면 데이터셋 행의 값.
    latest: dict[int, float] = {}
    for source_id in {r[0] for r in rows}:
        recorded = await service.get_latest_score(session, source_id)
        if recorded is not None:
            latest[source_id] = float(recorded.score)
    return [
        {"id": r[0], "name": r[1], "urn": r[2], "quality_status": r[3],
         "quality_score": latest.get(r[0], float(r[4]) if r[4] is not None else None),
         "relation_type": r[5]}
        for r in rows
    ]


@router.get("/datasets/{dataset_id}/rules/recommendations")
async def recommend_rules(dataset_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """프로파일 기반 품질 규칙 추천 후보 (생성은 별도 — 기존 규칙과 중복 제외).

    샘플 보기 권한이 없으면 실제 값이 드러나는 후보(허용 값 목록)는 빼고 준다(DEF-164)."""
    logger.debug("GET 규칙 추천: dataset_id=%d", dataset_id)
    reveal = await check_feature(session, current, SAMPLE_VIEW_FEATURE)
    return await service.recommend_rules(session, dataset_id, reveal_values=reveal)


# ---------------------------------------------------------------------------
# 품질 설정 — 원천 데이터 시간대(FRESHNESS 의 시간대 없는 시각 해석)
# ---------------------------------------------------------------------------

class QualitySettings(BaseModel):
    """품질 설정 — 시간대 없는 시각 값을 해석할 원천 데이터 시간대."""
    source_timezone: str = service.DEFAULT_SOURCE_TIMEZONE


@router.get("/settings", response_model=QualitySettings)
async def get_quality_settings(session: AsyncSession = Depends(get_session)):
    """품질 설정 조회 — ``source_timezone``: 최신성(FRESHNESS) 규칙이 시간대 없는 시각 값(MySQL DATETIME 등)을
    해석할 원천 데이터 시간대. 정밀 진단을 요청할 때의 값이 그 실행에 쓰인다."""
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
async def create_rule(user: RuleManager, data: QualityRuleCreate, session: AsyncSession = Depends(get_session)):
    """품질 검사 규칙을 생성한다(데이터셋 소유자 또는 관리자)."""
    await _require_dataset_owner(session, user, [data.dataset_id])
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
async def update_rule(user: RuleManager, rule_id: int, data: QualityRuleUpdate, session: AsyncSession = Depends(get_session)):
    """품질 규칙을 수정한다(데이터셋 소유자 또는 관리자)."""
    await _require_dataset_owner(session, user, await _datasets_of_rules(session, [rule_id]))
    try:
        result = await service.update_rule(session, rule_id, data)
    except ValueError as e:  # 유형에 없는 도메인, 읽을 수 없는 조건·계산식
        logger.warning("품질 규칙 수정 거부(422): rule_id=%d reason=%s", rule_id, e)
        raise HTTPException(status_code=422, detail=str(e)) from None
    if not result:
        logger.warning("수정할 품질 규칙 없음(404): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을(를) 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(user: RuleManager, rule_id: int, session: AsyncSession = Depends(get_session)):
    """품질 규칙을 삭제한다(데이터셋 소유자 또는 관리자)."""
    await _require_dataset_owner(session, user, await _datasets_of_rules(session, [rule_id]))
    if not await service.delete_rule(session, rule_id):
        logger.warning("삭제할 품질 규칙 없음(404): rule_id=%d", rule_id)
        raise HTTPException(status_code=404, detail="규칙을(를) 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 품질 지표 — 미리 정해 두고 컬럼에 할당하는 진단 기준 (definitions.py)
# ---------------------------------------------------------------------------

def _definition_error(e: Exception) -> HTTPException:
    if isinstance(e, definitions.DefinitionNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, definitions.DefinitionConflictError):
        return HTTPException(status_code=409, detail=str(e))
    return HTTPException(status_code=422, detail=str(e))


_DEFINITION_ERRORS = (definitions.DefinitionNotFoundError, definitions.DefinitionConflictError, ValueError)


# 품질 지표(정의)는 모든 데이터셋이 함께 쓰는 기준이고 수정이 할당된 규칙까지 바꾸므로, 쓰기는 관리 › 데이터 품질 메뉴와 같은
# quality.manage(명시 허용형)로 막는다(DEF-135). 할당 · 해제는 대상 데이터셋 소유자 가드가 있어 RuleManager 그대로.
@router.get("/definitions", response_model=list[QualityDefinitionResponse])
async def list_definitions(session: AsyncSession = Depends(get_session)):
    """품질 지표 목록 — 분류 순, 지표마다 할당된 컬럼·데이터셋 수."""
    return await definitions.list_definitions(session)


@router.post("/definitions", response_model=QualityDefinitionResponse, status_code=201)
async def create_definition(
    _guard: MenuUser, req: QualityDefinitionWrite, session: AsyncSession = Depends(get_session),
):
    """품질 지표를 만든다. 같은 이름이 있으면 409."""
    try:
        result = await definitions.create_definition(session, req)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e
    await session.commit()
    return result


@router.post("/definitions/defaults")
async def add_default_definitions(_guard: MenuUser, session: AsyncSession = Depends(get_session)) -> dict:
    """기본 지표(필수값·날짜 YYYYMMDD·여부 Y/N …) 가운데 같은 이름이 없는 것을 넣는다."""
    added = await definitions.add_defaults(session)
    await session.commit()
    return {"added": added}


@router.post("/sql-preview", response_model=QualitySqlPreview)
async def preview_sql(req: QualitySqlPreviewRequest, session: AsyncSession = Depends(get_session)):
    """이 유형·기준값으로 진단할 때 실행하는 Trino 쿼리와 통과 판정 방법. 쿼리를 실행하지는 않는다."""
    try:
        return await definitions.preview_sql(session, req)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e


@router.get("/definitions/{definition_id}", response_model=QualityDefinitionResponse)
async def get_definition(definition_id: int, session: AsyncSession = Depends(get_session)):
    try:
        return await definitions.get_definition(session, definition_id)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e


@router.put("/definitions/{definition_id}", response_model=QualityDefinitionResponse)
async def update_definition(
    _guard: MenuUser, definition_id: int, req: QualityDefinitionWrite, session: AsyncSession = Depends(get_session),
):
    """품질 지표를 바꾼다 — 할당된 컬럼의 규칙도 같이 바뀐다."""
    try:
        result = await definitions.update_definition(session, definition_id, req)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e
    await session.commit()
    return result


@router.delete("/definitions/{definition_id}")
async def delete_definition(_guard: MenuUser, definition_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """품질 지표를 지운다. 할당된 규칙은 직접 정한 규칙으로 남는다(``kept_rules``)."""
    try:
        kept = await definitions.delete_definition(session, definition_id)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e
    await session.commit()
    return {"kept_rules": kept}


@router.get("/definitions/{definition_id}/assignments", response_model=list[QualityAssignment])
async def list_definition_assignments(definition_id: int, session: AsyncSession = Depends(get_session)):
    """지표가 할당된 컬럼 목록."""
    try:
        return await definitions.list_assignments(session, definition_id)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e


@router.get("/definitions/{definition_id}/candidates", response_model=list[QualityColumnCandidate])
async def list_definition_candidates(
    definition_id: int,
    q: str | None = Query(None, max_length=200),
    catalog_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """지표를 할당할 컬럼 후보 — 검색어가 없으면 컬럼 이름·제약으로 추천한 것만."""
    try:
        return await definitions.list_candidates(session, definition_id, q, catalog_id)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e


@router.post("/definitions/{definition_id}/assign", response_model=QualityAssignResult)
async def assign_definition(
    user: RuleManager, definition_id: int, req: QualityAssignRequest, session: AsyncSession = Depends(get_session),
):
    """지표를 컬럼들에 할당한다 — 컬럼의 데이터셋마다 소유자 또는 관리자."""
    await _require_dataset_owner(session, user, [c.dataset_id for c in req.columns])
    try:
        result = await definitions.assign(session, definition_id, req)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e
    await session.commit()
    return result


@router.post("/definitions/{definition_id}/unassign")
async def unassign_definition(
    user: RuleManager, definition_id: int, req: QualityUnassignRequest, session: AsyncSession = Depends(get_session),
) -> dict:
    """할당을 해제한다 — 그 컬럼의 규칙과 결과를 지운다. 규칙의 데이터셋마다 소유자 또는 관리자."""
    await _require_dataset_owner(session, user, await _datasets_of_rules(session, req.rule_ids))
    try:
        removed = await definitions.unassign(session, definition_id, req.rule_ids)
    except _DEFINITION_ERRORS as e:
        raise _definition_error(e) from e
    await session.commit()
    return {"removed": removed}


# ---------------------------------------------------------------------------
# 결과 & 점수
# ---------------------------------------------------------------------------

async def _mask_samples(
    session: AsyncSession, current: TokenUser, results: list[QualityResultResponse],
) -> list[QualityResultResponse]:
    """위반 샘플은 실데이터 — 샘플 탭과 같은 기능 권한(``datasets.sample-view``)이 없으면 값을 빼고 건수만 남긴다(DEF-153)."""
    if not any(r.failed_samples for r in results) or await check_feature(session, current, SAMPLE_VIEW_FEATURE):
        return results
    logger.info("위반 샘플 숨김(샘플 보기 권한 없음): user=%s", current.username)
    return [r.model_copy(update={"failed_samples": None, "samples_hidden": True}) if r.failed_samples else r
            for r in results]


@router.get("/datasets/{dataset_id}/results", response_model=list[QualityResultResponse])
async def get_results(dataset_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """데이터셋의 최신 품질 검사 결과를 조회한다.

    활성 규칙별 가장 최근 결과. 서버가 평가할 수 없는 유형인데 결과가 없으면 미평가(``passed: null``) 항목.
    위반 샘플은 샘플 보기 권한이 있을 때만 값이 들어간다(DEF-153).
    """
    logger.debug("GET 최신 결과: dataset_id=%d", dataset_id)
    return await _mask_samples(session, current, await service.get_latest_results(session, dataset_id))


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


# ---------------------------------------------------------------------------
# 품질 진단 작업 — 대상·품질 지표·주기를 정해 주기적으로 진단 (app/quality/jobs.py)
# ---------------------------------------------------------------------------

def _job_error(e: Exception) -> HTTPException:
    if isinstance(e, jobs.JobNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, jobs.JobConflictError):
        return HTTPException(status_code=409, detail=str(e))
    return HTTPException(status_code=422, detail=str(e))


_JOB_ERRORS = (jobs.JobNotFoundError, jobs.JobConflictError, ValueError)


@router.get("/jobs", response_model=list[QualityJobResponse], dependencies=_MENU)
async def list_jobs(session: AsyncSession = Depends(get_session)):
    """품질 진단 작업 목록 — 이름순. 작업마다 다음 실행 시각과 마지막 작업 실행을 붙인다."""
    result = await jobs.list_jobs(session)
    await session.commit()  # 끝난 작업 실행의 마감 반영
    return result


@router.post("/jobs", response_model=QualityJobResponse, status_code=201)
async def create_job(user: MenuUser, req: QualityJobWrite, session: AsyncSession = Depends(get_session)):
    """품질 진단 작업을 등록한다. 이름 중복은 409, 대상·지표·주기 오류는 422."""
    await _require_schedule(session, user, req)
    try:
        result = await jobs.create_job(session, req, user.username)
        await session.commit()
        return result
    except _JOB_ERRORS as e:
        raise _job_error(e) from e


@router.post("/jobs/preview", response_model=QualityJobPreview, dependencies=_MENU)
async def preview_job(req: QualityJobPreviewRequest, session: AsyncSession = Depends(get_session)):
    """이 대상·지표로 지금 실행하면 몇 개 데이터셋이 진단되는지 — 등록 화면."""
    try:
        return await jobs.preview(session, req.targets, req.indicators, req.definition_ids)
    except ValueError as e:
        raise _job_error(e) from e


@router.post("/jobs/rules", response_model=list[QualityJobIndicatorRules], dependencies=_MENU)
async def list_job_rules(req: QualityJobRulesRequest, session: AsyncSession = Depends(get_session)):
    """이 대상의 데이터셋이 지표마다 가진 활성 규칙(컬럼·기대값) — 등록 화면이 지표가 실제로 무엇을 보는지 보여 준다."""
    try:
        return await jobs.rules_overview(session, req.targets, req.group_by)
    except ValueError as e:
        raise _job_error(e) from e


@router.get("/jobs/schemas", response_model=list[QualitySchemaOption], dependencies=_MENU)
async def list_job_schemas(catalog_id: int, session: AsyncSession = Depends(get_session)):
    """카탈로그의 스키마 이름과 데이터셋 수 — 대상 선택기."""
    return await jobs.list_schemas(session, catalog_id)


@router.get("/jobs/{job_id}", response_model=QualityJobResponse, dependencies=_MENU)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    try:
        result = await jobs.get_job(session, job_id)
        await session.commit()
        return result
    except jobs.JobNotFoundError as e:
        raise _job_error(e) from e


@router.put("/jobs/{job_id}", response_model=QualityJobResponse)
async def update_job(
    user: MenuUser, job_id: int, req: QualityJobWrite, session: AsyncSession = Depends(get_session),
):
    """품질 진단 작업을 고친다. 주기를 고치면 다음 실행 시각을 지금 기준으로 다시 잡는다. 이미 넣은 작업 실행은 그대로."""
    await _require_schedule(session, user, req)
    try:
        result = await jobs.update_job(session, job_id, req, user.username)
        await session.commit()
        return result
    except _JOB_ERRORS as e:
        raise _job_error(e) from e


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(user: MenuUser, job_id: int, session: AsyncSession = Depends(get_session)):
    """품질 진단 작업과 그 작업 이력을 지운다(실행 중이면 409). 데이터셋 실행과 규칙 결과는 남는다."""
    try:
        await jobs.delete_job(session, job_id, user.username)
        await session.commit()
    except _JOB_ERRORS as e:
        raise _job_error(e) from e


@router.post("/jobs/{job_id}/run", response_model=QualityJobRunResponse, status_code=202)
async def run_job(user: MenuUser, job_id: int, session: AsyncSession = Depends(get_session)):
    """지금 실행 — 대상 데이터셋의 진단을 대기열에 넣는다 (202). 이전 작업 실행이 끝나지 않았으면 409."""
    try:
        result = await jobs.run_now(session, job_id, user.username)
        await session.commit()
        return result
    except _JOB_ERRORS as e:
        raise _job_error(e) from e


@router.get("/job-runs", response_model=list[QualityJobRunResponse], dependencies=_MENU)
async def list_job_runs(
    job_id: int | None = Query(None, description="이 품질 진단 작업의 이력만"),
    cursor: int = Query(0, ge=0, description="건너뛸 개수(더 보기)"),
    limit: int = Query(30, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """작업 이력 — 최근 것부터."""
    result = await jobs.list_job_runs(session, job_id=job_id, limit=limit, cursor=cursor)
    await session.commit()
    return result


@router.get("/job-runs/{job_run_id}", response_model=QualityJobRunResponse, dependencies=_MENU)
async def get_job_run(job_run_id: int, session: AsyncSession = Depends(get_session)):
    try:
        result = await jobs.get_job_run(session, job_run_id)
        await session.commit()
        return result
    except jobs.JobNotFoundError as e:
        raise _job_error(e) from e


@router.get("/job-runs/{job_run_id}/report", response_model=QualityJobRunReport, dependencies=_MENU)
async def get_job_run_report(job_run_id: int, session: AsyncSession = Depends(get_session)):
    """작업 실행 한 번의 리포트 — 요약, 영역·지표별, 데이터셋별(오류율 높은 순), 규칙별."""
    try:
        result = await reports.job_run_report(session, job_run_id)
        await session.commit()
        return result
    except jobs.JobNotFoundError as e:
        raise _job_error(e) from e


@router.get("/job-runs/{job_run_id}/report.xlsx", dependencies=[*_MENU, Depends(require_feature("quality.export"))])
async def download_job_run_report(
    job_run_id: int,
    tz: str | None = Query(None, max_length=64, description="시각을 적을 IANA 시간대(브라우저 시간대, 기본 Asia/Seoul)"),
    session: AsyncSession = Depends(get_session),
):
    """작업 실행 리포트를 엑셀로 내려받는다 (시트: 요약 / 진단 기준별 / AI-Ready 지표별 / 데이터셋별 / 규칙별)."""
    try:
        report = await reports.job_run_report(session, job_run_id)
        await session.commit()
    except jobs.JobNotFoundError as e:
        raise _job_error(e) from e
    logger.info("작업 실행 리포트 내려받기: job_run_id=%d 규칙 %d건", job_run_id, report.rules_total)
    return Response(
        content=await asyncio.to_thread(reports.report_xlsx, report, tz),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="quality-report-{job_run_id}.xlsx"'},
    )


@router.get("/job-runs/{job_run_id}/report.pdf", dependencies=[*_MENU, Depends(require_feature("quality.export"))])
async def download_job_run_report_pdf(
    job_run_id: int,
    tz: str | None = Query(None, max_length=64, description="시각을 적을 IANA 시간대(브라우저 시간대, 기본 Asia/Seoul)"),
    session: AsyncSession = Depends(get_session),
):
    """작업 실행 리포트를 PDF 로 내려받는다 (핵심 숫자 · 실행 정보 · 영역별 · 분류별 · 데이터셋별 · 규칙별)."""
    try:
        report = await reports.job_run_report(session, job_run_id)
        await session.commit()
    except jobs.JobNotFoundError as e:
        raise _job_error(e) from e
    logger.info("품질 진단 리포트 PDF 내려받기: job_run_id=%d 규칙 %d건", job_run_id, report.rules_total)
    # 줄이 많으면 조판에 시간이 걸린다 — 이벤트 루프를 막지 않게 스레드에서 만든다
    content = await asyncio.to_thread(report_pdf.report_pdf, report, tz)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="quality-report-{job_run_id}.pdf"'},
    )


@router.get("/jobs/{job_id}/trend", response_model=QualityJobTrend, dependencies=_MENU)
async def get_job_trend(
    job_id: int, limit: int = Query(30, ge=1, le=200), session: AsyncSession = Depends(get_session),
):
    """품질 진단 작업의 회차별 오류율(전체·지표별) — 오래된 것부터 최근 ``limit`` 회. 건너뛴 차례와 실행 중인 것은 뺀다."""
    try:
        return await reports.job_trend(session, job_id, limit)
    except jobs.JobNotFoundError as e:
        raise _job_error(e) from e


@router.post("/job-runs/{job_run_id}/cancel", response_model=QualityJobRunResponse)
async def cancel_job_run(user: MenuUser, job_run_id: int, session: AsyncSession = Depends(get_session)):
    """작업 실행 취소 — 끝나지 않은 데이터셋 실행을 모두 취소한다. 이미 끝났으면 409."""
    try:
        result = await jobs.cancel_job_run(session, job_run_id, user.username)
        await session.commit()
        return result
    except _JOB_ERRORS as e:
        raise _job_error(e) from e


# ---------------------------------------------------------------------------
# 진단 실행 — 품질 워커(extensions/argus-catalog-quality-worker)가 Trino 로 실행한다
# ---------------------------------------------------------------------------

def _run_error(e: Exception) -> HTTPException:
    if isinstance(e, runs.RunNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=409, detail=str(e))


@router.get("/datasets/{dataset_id}/engine", response_model=QualityEngineStatus)
async def get_engine_status(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """이 데이터셋을 Trino 로 진단할 수 있는지, 안 되면 그 이유와 살아 있는 워커 수."""
    try:
        return await runs.engine_status(session, dataset_id)
    except runs.RunNotFoundError as e:
        raise _run_error(e) from e


@router.get("/indicators", response_model=list[QualityIndicatorResponse])
async def list_indicators():
    """품질 지표 목록 — 진단 실행·품질 진단 작업이 측정할 품질을 고르는 단위(관리지침 진단 기준 16개 + 건수 + AI-Ready 더미 값·
    데이터 연속성). 각 기준이 속한 AI-Ready 품질 지표(가이드라인 §2.3)도 함께 준다."""
    definable = {i.key for i in quality_indicators.definable()}
    return [
        QualityIndicatorResponse(
            key=i.key, name=i.name, area=i.area, area_name=quality_indicators.AREAS[i.area],
            check_types=list(i.check_types), description=i.description, domain=i.domain,
            definable=i.key in definable,
            ai_ready=i.ai_ready[0] if i.ai_ready else None,
            ai_ready_name=quality_indicators.AI_READY_AREAS[i.ai_ready[0]] if i.ai_ready else None,
            ai_ready_detail=i.ai_ready[1] if i.ai_ready else None)
        for i in quality_indicators.INDICATORS
    ]


@router.get("/code-groups", response_model=list[QualityCodeGroupOption])
async def list_code_groups(session: AsyncSession = Depends(get_session)):
    """허용 값 규칙이 가리킬 수 있는 표준 코드 그룹(모든 사전) — 규칙은 ``{"code_group_id": N}`` 으로 연결하고, 진단할 때 그
    그룹의 사용 중인 코드 값을 허용 값으로 쓴다(TODO-096)."""
    return await definitions.list_code_groups(session)


@router.post("/datasets/{dataset_id}/runs", response_model=QualityRunResponse, status_code=202)
async def create_run(
    user: CheckRunner, dataset_id: int, req: QualityRunCreate | None = None,
    session: AsyncSession = Depends(get_session),
):
    """진단 실행을 대기열에 넣는다 (202). 실행은 품질 워커가 하고, 상태는 실행 조회로 본다.

    본문의 ``indicators`` 로 측정할 품질 지표를 고른다(없으면 전체, 모르는 키는 422).
    ``kind`` 가 ``PROFILE`` 이면 규칙 대신 컬럼 통계(프로파일)를 수집하고 데이터셋의 행 수를 갱신한다.
    같은 데이터셋의 실행이 대기·실행 중이거나, 진단 경로(Trino 연결 또는 원천 직접)가 없거나, 평가할 활성 규칙이 없으면 409
    (프로파일은 규칙 대신 스키마 정보가 있어야 한다). 데이터셋 소유자 또는 관리자만.
    """
    await _require_dataset_owner(session, user, [dataset_id])
    try:
        result = await runs.create_run(
            session, dataset_id, user.username, indicators=req.indicators if req else None,
            kind=req.kind if req else runs.CHECK_KIND)
        await session.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (runs.RunNotFoundError, runs.RunConflictError, runs.EngineUnavailableError) as e:
        raise _run_error(e) from e


@router.get("/datasets/{dataset_id}/runs", response_model=list[QualityRunResponse])
async def list_dataset_runs(
    dataset_id: int,
    limit: int = Query(10, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 진단 실행 이력 — 최근 것부터."""
    result = await runs.list_runs(session, dataset_id=dataset_id, limit=limit)
    await session.commit()  # 응답 없음 → FAILED 판정 반영
    return result


@router.get("/runs", response_model=list[QualityRunResponse], dependencies=_MENU)
async def list_runs(
    status: str | None = Query(
        None, description="QUEUED/RUNNING/SUCCEEDED/PARTIAL/FAILED/CANCELLED, 또는 묶음 ACTIVE(대기·실행 중)·PROBLEM(실패·일부 실패)"),
    datasource_id: int | None = Query(None, description="진단 대상 데이터셋의 원본 데이터 소스"),
    since: datetime | None = Query(None, description="이 시각 이후에 요청된 실행만"),
    job_run_id: int | None = Query(None, description="이 작업 실행이 넣은 실행만"),
    cursor: int = Query(0, ge=0, description="건너뛸 개수(더 보기)"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """모든 데이터셋의 진단 실행 — 데이터 품질 화면."""
    result = await runs.list_runs(
        session, status=status, limit=limit, datasource_id=datasource_id, since=since, cursor=cursor,
        job_run_id=job_run_id)
    await session.commit()
    return result


@router.get("/runs/status", response_model=QualityServiceStatus, dependencies=_MENU)
async def get_service_status(session: AsyncSession = Depends(get_session)):
    """살아 있는 품질 워커와 대기열 크기."""
    return await runs.service_status(session)


@router.get("/runs/{run_id}", response_model=QualityRunResponse)
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)):
    """진단 실행 1건의 상태·진행·결과."""
    try:
        result = await runs.get_run(session, run_id)
        await session.commit()
        return result
    except runs.RunNotFoundError as e:
        raise _run_error(e) from e


@router.get("/runs/{run_id}/results", response_model=list[QualityResultResponse], dependencies=_MENU)
async def get_run_results(run_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """진단 실행 하나가 남긴 규칙별 결과(진단 건수·오류 건수·품질 지표). 그 실행에서 평가한 규칙만 나온다.

    위반 샘플은 샘플 보기 권한이 있을 때만 값이 들어간다(DEF-153)."""
    try:
        await runs.get_run(session, run_id)
    except runs.RunNotFoundError as e:
        raise _run_error(e) from e
    return await _mask_samples(session, current, await service.get_run_results(session, run_id))


@router.get("/runs/{run_id}/queries", response_model=list[QualityRunQuery], dependencies=_MENU)
async def get_run_queries(run_id: int, session: AsyncSession = Depends(get_session)):
    """진단 실행이 Trino 에 낸 진단 쿼리(SQL)와 쿼리 ID. 워커가 가져가기 전의 실행은 빈 목록."""
    from app.quality.models import QualityRun

    run = await session.get(QualityRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="진단 실행을 찾을 수 없습니다.")
    return execution.run_queries(run)


@router.post("/runs/{run_id}/cancel", response_model=QualityRunResponse)
async def cancel_run(user: CheckRunner, run_id: int, session: AsyncSession = Depends(get_session)):
    """진단 실행을 취소한다. 실행 중이면 워커가 Trino 쿼리를 취소한 뒤 CANCELLED 가 된다.

    품질 탭에서는 데이터셋 소유자 또는 관리자만 — 관리 > 데이터 품질 메뉴를 쓸 수 있는 사용자(``quality.manage`` 명시 허용)는
    메뉴의 다른 동작(작업 실행 취소·다시 실행)과 같이 소유와 무관하게 취소할 수 있다.
    """
    if not await check_feature_granted(session, user, QUALITY_MANAGE):
        await _require_dataset_owner(session, user, await _dataset_of_run(session, run_id))
    try:
        result = await runs.cancel_run(session, run_id, user.username)
        await session.commit()
        return result
    except (runs.RunNotFoundError, runs.RunConflictError) as e:
        raise _run_error(e) from e


@router.post("/runs/{run_id}/retry", response_model=QualityRunResponse, status_code=202)
async def retry_run(user: MenuUser, run_id: int, session: AsyncSession = Depends(get_session)):
    """끝난 실행의 데이터셋을 다시 진단한다 — 새 실행을 대기열에 넣는다 (202)."""
    try:
        result = await runs.retry_run(session, run_id, user.username)
        await session.commit()
        return result
    except (runs.RunNotFoundError, runs.RunConflictError, runs.EngineUnavailableError) as e:
        raise _run_error(e) from e


@router.get("/workers", response_model=list[QualityWorkerResponse], dependencies=_MENU)
async def list_workers(session: AsyncSession = Depends(get_session)):
    """품질 워커 목록과 생존 여부."""
    return await runs.list_workers(session)


# ---------------------------------------------------------------------------
# 워커용 API — 품질 워커(extensions/argus-catalog-quality-worker)만 부른다.
# Trino 접속 정보(비밀번호 포함)를 돌려주므로 관리자 전용이다. 워커는 API 키(범위 catalog:write, 역할 관리자)로 부른다.
# ---------------------------------------------------------------------------

@router.post("/worker/claim")
async def worker_claim(user: WorkerKey, body: WorkerIdentity, session: AsyncSession = Depends(get_session)):
    """대기 중인 실행 하나를 가져간다. 응답 ``job`` 이 null 이면 할 일이 없다.

    ``job`` 은 실행할 쿼리 목록(``steps``)과 Trino 접속 정보다. 부를 때마다 워커 생존도 함께 기록된다.
    """
    await _own_worker(session, user, body.worker_id)
    job = await execution.claim(session, body.worker_id, body.hostname, body.version, user.sub)
    await session.commit()
    return {"job": job}


@router.post("/worker/heartbeat")
async def worker_heartbeat(user: WorkerKey, body: WorkerHeartbeat, session: AsyncSession = Depends(get_session)):
    """생존·진행 보고. ``cancel`` 이 true 면 취소 요청, ``active`` 가 false 면 그 실행은 더 이상 이 워커의 것이 아니다."""
    await _own_worker(session, user, body.worker_id)
    reply = await execution.report(
        session, body.worker_id, body.hostname, body.version, body.run_id,
        progress_done=body.progress_done, progress_total=body.progress_total,
        current_target=body.current_target, query_ids=body.query_ids, owner=user.sub)
    await session.commit()
    return reply


@router.post("/worker/runs/{run_id}/complete", response_model=WorkerCompleteResponse)
async def worker_complete(
    user: WorkerKey, run_id: int, body: WorkerComplete, session: AsyncSession = Depends(get_session),
):
    """쿼리 결과를 제출한다 — 카탈로그가 판정·저장하고 실행을 끝낸다. 이 워커의 실행이 아니면 409.

    응답의 ``samples`` 는 실패한 규칙의 위반 샘플 쿼리 — 워커가 이어서 내고 ``/worker/runs/{id}/samples`` 로 보낸다.
    """
    await _own_worker(session, user, body.worker_id)
    try:
        run, samples = await execution.complete(
            session, run_id, body.worker_id, [r.model_dump() for r in body.results], body.query_ids)
        await session.commit()
        return WorkerCompleteResponse(**runs.to_response(run).model_dump(), samples=samples)
    except runs.RunConflictError as e:
        raise _run_error(e) from e


@router.post("/worker/runs/{run_id}/samples")
async def worker_samples(
    user: WorkerKey, run_id: int, body: WorkerSamples, session: AsyncSession = Depends(get_session),
):
    """위반 샘플을 제출한다 — 이 워커가 끝낸 실행의 실패 결과에 붙인다. 아니면 409."""
    await _own_worker(session, user, body.worker_id)
    try:
        attached = await execution.attach_samples(
            session, run_id, body.worker_id, [r.model_dump() for r in body.results])
        await session.commit()
        return {"attached": attached}
    except runs.RunConflictError as e:
        raise _run_error(e) from e


@router.post("/worker/runs/{run_id}/finish", response_model=QualityRunResponse)
async def worker_finish(
    user: WorkerKey, run_id: int, body: WorkerFinish, session: AsyncSession = Depends(get_session),
):
    """결과 없이 끝낸다(FAILED·CANCELLED) 또는 대기열로 되돌린다(REQUEUE). 이 워커의 실행이 아니면 409."""
    await _own_worker(session, user, body.worker_id)
    try:
        run = await execution.finish(session, run_id, body.worker_id, body.outcome, body.message, body.query_ids)
        await session.commit()
        return runs.to_response(run)
    except runs.RunConflictError as e:
        raise _run_error(e) from e


@router.delete("/worker/{worker_id}", status_code=204)
async def worker_leave(user: WorkerKey, worker_id: str, session: AsyncSession = Depends(get_session)):
    """워커가 정상 종료하며 목록에서 빠진다."""
    await _own_worker(session, user, worker_id)
    await runs.remove_worker(session, worker_id)
    await session.commit()
