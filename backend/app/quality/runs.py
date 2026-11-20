# SPDX-License-Identifier: Apache-2.0
"""품질 진단 실행 — 대기열, 엔진(Trino) 대상 해석, 모니터링.

구조: ``Catalog(실행 요청·모니터링) → 품질 워커(대기열 처리) → Trino(원본 접속·계산)``.
API 서버는 여기서 ``QUEUED`` 행을 만들고 상태를 읽기만 한다. 실제 쿼리는 별도 프로세스인
품질 워커(``extensions/argus-catalog-quality-worker``)가 낸다(카탈로그 쪽 절반은 ``execution.py``).

- **실행 행이 정본**이다(``catalog_quality_runs``). 워커는 진행·heartbeat·결과를 행에 적는다.
- **엔진 대상 해석**: 데이터셋 → Trino 의 ``catalog.schema.table``.
  - 데이터셋의 데이터 소스가 Trino 면 그 Trino 가 엔진이고 qualified_name 의 끝 세 토막이 이름이다.
  - 아니면 데이터셋이 속한 카탈로그에 ``QUERY`` 로 연결된 Trino 데이터 소스를 찾고, 연결의
    ``remote_name`` 이 Trino 카탈로그 이름, qualified_name 의 끝 두 토막이 스키마·테이블이다.
  원본 접속 정보는 Trino 카탈로그가 갖고 있다 — 워커는 Trino 에만 접속한다.
- **응답 없음**: RUNNING 인데 보고가 ``UNRESPONSIVE_SECONDS`` 넘게 없으면 화면에 "응답 없음",
  ``STALE_SECONDS`` 가 지나면 조회·새 실행 시점에 FAILED 로 바꾼다(데이터 소스 수집 실행과 같은 방식).
- **동시 실행**: 같은 데이터셋은 한 번에 하나만 대기·실행한다. 워커는 같은 원본 데이터 소스의 실행을
  동시에 하나만 잡는다(``claim_next``) — Trino 리소스 그룹은 Trino 자원만 제한하고 원본 DB 부하는
  막지 못하기 때문이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import (
    CatalogConnection,
    Dataset,
    DatasetSchema,
    Datasource,
    DatasourceConfiguration,
)
from app.quality import indicators as quality_indicators
from app.quality.models import (
    QualityDefinition,
    QualityJob,
    QualityJobRun,
    QualityRule,
    QualityRun,
    QualityWorker,
)
from app.quality.rule_direct import DIRECT_CHECK_TYPES, DIRECT_SOURCE_TYPES, ENGINE_CHECK_TYPES
from app.quality.rule_sql import TRINO_PATH_TYPES, TableRef
from app.quality.schemas import (
    QualityEngineStatus,
    QualityRunResponse,
    QualityServiceStatus,
    QualityWorkerResponse,
)

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("QUEUED", "RUNNING")
FINAL_STATUSES = ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED")
UNRESPONSIVE_SECONDS = 120
STALE_SECONDS = 300
# 살아 있는 워커가 하나도 없을 때, 대기 중인 실행을 이 시간이 지나면 실패로 끝낸다
QUEUE_TIMEOUT_SECONDS = 3600
WORKER_ALIVE_SECONDS = 60
# 이만큼 소식이 없는 워커 행은 목록에서 지운다
WORKER_FORGET_SECONDS = 24 * 3600
ENGINE_TYPE = "trino"
# 실행 경로 — Trino 연결이 있으면 Trino, 없으면 품질 워커가 원천을 직접 읽는다
TRINO_ENGINE = "TRINO"
DIRECT_ENGINE = "DIRECT"
# 실행 종류 — 규칙 진단 / 프로파일(컬럼 통계 수집)
CHECK_KIND = "CHECK"
PROFILE_KIND = "PROFILE"


class RunNotFoundError(LookupError):
    """실행 또는 데이터셋이 없다 (404)."""


class RunConflictError(RuntimeError):
    """지금 상태에서는 할 수 없는 요청이다 (409)."""


class WorkerOwnerError(PermissionError):
    """다른 인증 주체가 등록한 워커 ID 를 쓰려 했다."""


class EngineUnavailableError(RuntimeError):
    """이 데이터셋은 엔진으로 진단할 수 없다 (409) — 메시지가 사유."""


class _NoTrinoConnectionError(EngineUnavailableError):
    """Trino 연결이 없다 — 직접 진단으로 넘어갈 수 있는 사유."""


@dataclass(frozen=True)
class EngineTarget:
    """데이터셋을 진단할 경로와 대상.

    ``engine`` 이 ``TRINO`` 면 ``datasource_id`` 는 Trino 데이터 소스, ``table`` 은 Trino 안의 ``catalog.schema.table``.
    ``DIRECT`` 면 ``datasource_id`` 는 데이터셋의 원천 데이터 소스, ``table`` 은 (데이터 소스 이름, 스키마, 테이블)이고
    ``source_type`` 이 원천 유형이다 — 품질 워커가 원천 DB 를 직접 읽는다(TODO-095).

    ``TRINO`` 인데 데이터셋의 원본도 워커가 직접 읽을 수 있는 유형이면(``python_source_type``) 커스텀 Python 규칙은 같은
    실행 안에서 원천을 직접 읽어 평가한다 — 한 실행이 두 경로를 함께 쓴다(``python_datasource_id`` 가 그 원본).
    """

    datasource_id: int
    datasource_name: str
    table: TableRef
    engine: str = "TRINO"
    source_type: str | None = None
    python_source_type: str | None = None
    python_datasource_id: int | None = None

    @property
    def check_types(self) -> tuple[str, ...]:
        """이 경로로 평가할 수 있는 규칙 유형."""
        if self.engine == DIRECT_ENGINE:
            return DIRECT_CHECK_TYPES
        return (*TRINO_PATH_TYPES, "CUSTOM_PYTHON") if self.python_source_type else TRINO_PATH_TYPES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    """시간대 없는 값(SQLite 등)은 UTC 로 본다."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 엔진 대상 해석
# ---------------------------------------------------------------------------

async def stored_connection_config(session: AsyncSession, datasource_id: int, name: str, what: str) -> str:
    """데이터 소스에 저장된 접속 설정(암호화된 JSON). 없으면 ``EngineUnavailableError``.

    경로 확인(``resolve_engine_target``)과 실행(``execution._source_connection``)이 같이 써 두 판단이 어긋나지 않게 한다(DEF-154).
    """
    stored = await session.scalar(select(DatasourceConfiguration.config_json).where(
        DatasourceConfiguration.datasource_id == datasource_id))
    if not stored:
        raise EngineUnavailableError(f"{what} 데이터 소스 '{name}' 에 접속 설정이 없습니다.")
    return stored


async def resolve_engine_target(
    session: AsyncSession, dataset: Dataset, *, direct: bool = True, connection: bool = True,
) -> EngineTarget:
    """데이터셋 → 진단 경로와 대상. 해석하지 못하면 ``EngineUnavailableError``(사유 포함).

    카탈로그에 Trino 연결(QUERY)이 있으면 Trino 로 진단한다. 연결이 없으면 원천이 직접 읽을 수 있는 유형
    (``DIRECT_SOURCE_TYPES``)일 때 품질 워커가 원천을 직접 읽는다 — ``direct=False`` 면 Trino 대상만 찾는다.
    Trino 연결은 있는데 설정이 잘못됐으면(카탈로그 이름 없음 등) 직접 진단으로 넘어가지 않고 그 사유를 알린다.
    ``connection`` 이면 대상 데이터 소스에 접속 설정이 저장돼 있는지도 실행과 같은 기준으로 본다(DEF-154).
    """
    target = await _resolve_target(session, dataset, direct=direct)
    if connection:
        await stored_connection_config(session, target.datasource_id, target.datasource_name,
                                       "원천" if target.engine == DIRECT_ENGINE else "Trino")
    return target


async def source_view(session: AsyncSession, dataset: Dataset):
    """수동 데이터셋(TODO-CAT-001)이면 원천 매핑을 데이터셋처럼 보이게 한 대리 객체, 아니면 데이터셋 그대로.

    대리 객체는 매핑된 데이터 소스 · ``{datasource_id}.{원천 경로}`` · 그 데이터 소스의 SYNC 카탈로그를 가진다 —
    수집 데이터셋과 같은 규칙(Trino 연결 · 원천 직접)으로 대상을 정할 수 있다. 매핑이 없으면 ``EngineUnavailableError``.
    """
    from types import SimpleNamespace

    from app.catalog.catalogs_service import is_manual_datasource, sync_catalog_id
    from app.catalog.models import DatasetSourceMap
    if not is_manual_datasource(await session.get(Datasource, dataset.datasource_id)):
        return dataset
    m = (await session.execute(
        select(DatasetSourceMap).where(DatasetSourceMap.dataset_id == dataset.id))).scalars().first()
    if m is None:
        raise EngineUnavailableError(
            "원천 매핑이 없는 수동 데이터셋입니다. 데이터셋 상세에서 실제 데이터 소스와 원천 경로를 매핑하면 진단할 수 있습니다.")
    source = await session.get(Datasource, m.datasource_id)
    return SimpleNamespace(
        id=dataset.id, datasource_id=m.datasource_id,
        qualified_name=f"{source.datasource_id}.{m.remote_path}",
        catalog_id=await sync_catalog_id(session, m.datasource_id),
    )


async def _resolve_target(session: AsyncSession, dataset: Dataset, *, direct: bool) -> EngineTarget:
    dataset = await source_view(session, dataset)
    try:
        target = await _trino_target(session, dataset)
    except _NoTrinoConnectionError as e:
        if not direct:
            raise
        datasource = await session.get(Datasource, dataset.datasource_id)
        source_type = (datasource.type or "").lower() if datasource is not None else ""
        if source_type not in DIRECT_SOURCE_TYPES:
            raise EngineUnavailableError(
                f"{e} 원천을 직접 읽는 진단은 PostgreSQL·MySQL·MariaDB·Oracle·SQL Server 데이터 소스만 지원합니다.") from None
        return EngineTarget(datasource.id, datasource.name, direct_table(dataset, datasource),
                            engine=DIRECT_ENGINE, source_type=source_type)
    if direct and target.datasource_id != dataset.datasource_id:
        # Trino 로 읽는 원본을 워커가 직접 읽을 수도 있으면 커스텀 Python 은 원천 직접으로 함께 평가한다
        source = await session.get(Datasource, dataset.datasource_id)
        source_type = (source.type or "").lower() if source is not None else ""
        if source_type in DIRECT_SOURCE_TYPES:
            target = replace(target, python_source_type=source_type, python_datasource_id=source.id)
    return target


def direct_table(dataset: Dataset, datasource: Datasource) -> TableRef:
    """원천 직접 경로의 대상 — (데이터 소스 이름, 스키마, 테이블). qualified_name 의 끝 두 토막이 스키마 · 테이블이다."""
    parts = [p for p in (dataset.qualified_name or "").split(".") if p]
    return TableRef(datasource.name, parts[-2] if len(parts) > 1 else "", parts[-1] if parts else "")


async def _trino_target(session: AsyncSession, dataset: Dataset) -> EngineTarget:
    parts = [p for p in (dataset.qualified_name or "").split(".") if p]
    datasource = await session.get(Datasource, dataset.datasource_id)
    if datasource is not None and (datasource.type or "").lower() == ENGINE_TYPE:
        if len(parts) < 3:
            raise EngineUnavailableError(
                "데이터셋 이름에서 Trino 의 카탈로그·스키마·테이블을 알 수 없습니다.")
        return EngineTarget(datasource.id, datasource.name, TableRef(*parts[-3:]))

    if len(parts) < 2:
        raise EngineUnavailableError("데이터셋 이름에서 스키마·테이블을 알 수 없습니다.")
    if dataset.catalog_id is None:
        raise _NoTrinoConnectionError("데이터셋이 카탈로그에 배정되지 않아 Trino 연결을 찾을 수 없습니다.")
    row = (await session.execute(
        select(CatalogConnection.remote_name, Datasource.id, Datasource.name)
        .join(Datasource, Datasource.id == CatalogConnection.datasource_id)
        .where(
            CatalogConnection.catalog_id == dataset.catalog_id,
            CatalogConnection.role == "QUERY",
            func.lower(Datasource.type) == ENGINE_TYPE,
        )
        .order_by(CatalogConnection.id)
    )).first()
    if row is None:
        raise _NoTrinoConnectionError(
            "이 데이터셋의 카탈로그에 연결된 Trino 데이터 소스가 없습니다. "
            "Trino 데이터 소스의 '카탈로그 연결'에서 이 카탈로그를 연결하세요.")
    remote_name, ds_id, ds_name = row
    if not remote_name:
        raise EngineUnavailableError(
            f"Trino 데이터 소스 '{ds_name}' 와의 연결에 Trino 카탈로그 이름이 비어 있습니다.")
    return EngineTarget(ds_id, ds_name, TableRef(remote_name, parts[-2], parts[-1]))


async def workers_alive(session: AsyncSession) -> int:
    """heartbeat 가 살아 있는 워커 수."""
    cutoff = _now() - timedelta(seconds=WORKER_ALIVE_SECONDS)
    rows = (await session.execute(select(QualityWorker.heartbeat_at))).scalars().all()
    return sum(1 for hb in rows if (_as_utc(hb) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff)


async def engine_status(session: AsyncSession, dataset_id: int) -> QualityEngineStatus:
    """이 데이터셋을 정밀 진단(Trino 또는 원천 직접)할 수 있는지 — 품질 탭이 버튼을 켤지 정하는 데 쓴다."""
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise RunNotFoundError("데이터셋을 찾을 수 없습니다.")
    alive = await workers_alive(session)
    try:
        target = await resolve_engine_target(session, dataset)
    except EngineUnavailableError as e:
        return QualityEngineStatus(available=False, reason=str(e), workers_alive=alive)
    return QualityEngineStatus(
        available=True, engine=target.engine, engine_datasource_id=target.datasource_id,
        engine_datasource_name=target.datasource_name, engine_table=target.table.display,
        workers_alive=alive, check_types=list(target.check_types),
    )


# ---------------------------------------------------------------------------
# 응답·응답 없음 판정
# ---------------------------------------------------------------------------

def dump_definition_ids(ids: list[int] | None) -> str | None:
    """고른 품질 지표 ID → DB 컬럼 값(쉼표 구분, 오름차순). 고르지 않았으면 NULL."""
    return ",".join(str(i) for i in sorted({int(i) for i in ids})) if ids else None


def load_definition_ids(value: str | None) -> list[int]:
    return [int(k) for k in (value or "").split(",") if k.strip().isdigit()]


def wants_definition(definition_ids: list[int] | None, rule_definition_id: int | None) -> bool:
    """고른 품질 지표로 이 규칙을 진단하는가. 고르지 않았으면 모든 규칙(지표 없이 직접 만든 규칙 포함)."""
    return not definition_ids or rule_definition_id in definition_ids


def in_scope(
    indicator_keys: list[str] | None, definition_ids: list[int] | None,
    check_type: str | None, domain: str | None, column_name: str | None, rule_definition_id: int | None,
) -> bool:
    """이 실행이 진단하는 규칙인가 — 정밀 진단이 아는 유형이고, 고른 진단 기준 · 품질 지표에 든다.

    실행의 ``rules_total`` 은 이 규칙들의 수다(DEF-149) — 작업이 고르지 않은 지표의 규칙이나 지표 없이 직접 만든 규칙은
    분모에 넣지 않아 작업 이력 · 실행 현황 · 리포트가 같은 수를 보인다. 고른 범위 안인데 경로가 평가하지 못하는 유형
    (원천 직접의 Trino 함수 등)은 넣는다 — 실행이 건너뛴 규칙으로 알린다.
    """
    return (check_type in ENGINE_CHECK_TYPES
            and quality_indicators.wants(indicator_keys or [], check_type, domain, column_name)
            and wants_definition(definition_ids, rule_definition_id))


async def fill_definition_names(session: AsyncSession, responses: list) -> None:
    """응답들의 ``definition_ids`` 에 맞춰 ``definitions``(이름)를 채운다. 지운 지표는 "삭제된 지표 #ID"."""
    ids = {i for r in responses for i in r.definition_ids}
    if not ids:
        return
    rows = await session.execute(select(QualityDefinition.id, QualityDefinition.name).where(QualityDefinition.id.in_(ids)))
    names = dict(rows.all())
    for r in responses:
        r.definitions = [names.get(i, f"삭제된 지표 #{i}") for i in r.definition_ids]
        if hasattr(r, "deleted_definition_ids"):
            # 작업 응답 — 지운 지표를 따로 알려 화면이 "더는 진단하지 않음" 을 설명하게 한다(DEF-148)
            r.deleted_definition_ids = [i for i in r.definition_ids if i not in names]


def to_response(run: QualityRun) -> QualityRunResponse:
    """실행 행 → 응답. 오류율과 "응답 없음" 표시를 계산해 붙인다."""
    resp = QualityRunResponse.model_validate(run)
    if run.checked_count:
        resp.error_rate = round((run.error_count or 0) / run.checked_count * 100, 4)
    if run.status == "RUNNING":
        last = _as_utc(run.heartbeat_at or run.started_at)
        resp.unresponsive = last is not None and (_now() - last).total_seconds() > UNRESPONSIVE_SECONDS
    return resp


async def settle_stale(session: AsyncSession, runs: list[QualityRun]) -> None:
    """끝나지 못할 실행을 FAILED 로 바꾼다 (조회·새 실행 시점에 판정).

    - RUNNING 인데 보고가 오래 끊긴 실행
    - QUEUED 인데 살아 있는 워커가 하나도 없이 ``QUEUE_TIMEOUT_SECONDS`` 를 넘긴 실행 — 그대로 두면 작업 실행이
      끝없이 "실행 중" 이고 같은 데이터셋의 새 진단도 막힌다. 워커가 살아 있으면 대기가 길어도 기다린다.
    """
    now = _now()
    alive: int | None = None
    for run in runs:
        if run.status == "RUNNING":
            last = _as_utc(run.heartbeat_at or run.started_at)
            if last is not None and (now - last).total_seconds() > STALE_SECONDS:
                run.status = "FAILED"
                run.finished_at = now
                run.error_message = "품질 워커의 응답이 없어 실패로 처리했습니다. 워커 상태를 확인하세요."
                logger.warning("품질 진단 실행 응답 없음 → FAILED: run_id=%d worker=%s", run.id, run.worker_id)
        elif run.status == "QUEUED":
            queued = _as_utc(run.queued_at)
            if queued is None or (now - queued).total_seconds() <= QUEUE_TIMEOUT_SECONDS:
                continue
            if alive is None:
                alive = await workers_alive(session)
            if alive == 0:
                run.status = "FAILED"
                run.finished_at = now
                run.error_message = (
                    f"살아 있는 품질 워커가 없어 {QUEUE_TIMEOUT_SECONDS // 60}분 넘게 시작하지 못했습니다. 워커를 기동한 뒤 다시 실행하세요.")
                logger.warning("품질 진단 실행 대기 만료 → FAILED: run_id=%d", run.id)
    await session.flush()


# ---------------------------------------------------------------------------
# 요청·조회·취소 (API 서버)
# ---------------------------------------------------------------------------

async def create_run(
    session: AsyncSession, dataset_id: int, requested_by: str | None, trigger_type: str = "MANUAL",
    *, indicators: list[str] | None = None, definition_ids: list[int] | None = None,
    job_run_id: int | None = None, kind: str = CHECK_KIND,
) -> QualityRunResponse:
    """진단 실행을 대기열에 넣는다. 실제 실행은 품질 워커가 한다.

    ``kind`` 가 ``PROFILE`` 이면 규칙 대신 컬럼 통계(프로파일)를 수집하는 실행이다 — 규칙이 없어도 되고 지표는 쓰지 않는다.

    ``indicators`` 는 측정할 품질 지표 키(``app/quality/indicators.py``) — 비우면 이 경로(Trino·직접)로 평가할 수 있는 규칙 전부.
    ``definition_ids`` 는 측정할 품질 지표(미리 정의한 지표) — 주면 그 지표에 할당된 규칙만 낸다.
    모르는 지표 키는 ``ValueError``. ``job_run_id`` 는 이 실행을 넣은 작업 실행.
    """
    keys = quality_indicators.normalize(indicators)
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise RunNotFoundError("데이터셋을 찾을 수 없습니다.")
    target = await resolve_engine_target(session, dataset)

    active = list((await session.execute(
        select(QualityRun).where(QualityRun.dataset_id == dataset_id, QualityRun.status.in_(ACTIVE_STATUSES))
    )).scalars().all())
    await settle_stale(session, active)
    # 같은 종류(진단 · 프로파일)는 데이터셋마다 하나만 — 진단과 프로파일은 함께 대기할 수 있다(품질 진단 작업이 둘 다 넣는다).
    # 같은 원본 데이터 소스의 실행은 워커가 하나씩 잡으므로(claim_next) 실제로는 차례로 돈다
    busy = [r for r in active if r.status in ACTIVE_STATUSES and (r.kind or CHECK_KIND) == kind]
    if busy:
        what = "프로파일" if kind == PROFILE_KIND else "진단"
        raise RunConflictError(f"이 데이터셋의 {what}이 이미 대기 중이거나 실행 중입니다.")

    if kind == PROFILE_KIND:
        has_schema = await session.scalar(select(DatasetSchema.id).where(DatasetSchema.dataset_id == dataset_id).limit(1))
        if has_schema is None:
            raise RunConflictError("스키마(컬럼) 정보가 없어 프로파일을 수집할 수 없습니다. 먼저 메타데이터를 동기화하세요.")
        run = QualityRun(
            dataset_id=dataset_id, kind=PROFILE_KIND, engine=target.engine, engine_datasource_id=target.datasource_id,
            engine_table=target.table.display, trigger_type=trigger_type, status="QUEUED", rules_total=0,
            requested_by=requested_by, queued_at=_now(), job_run_id=job_run_id,
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        logger.info("프로파일 실행 요청: run_id=%d dataset_id=%d engine=%s table=%s by=%s",
                    run.id, dataset_id, run.engine, run.engine_table, requested_by)
        return to_response(run)

    rules = (await session.execute(
        select(QualityRule.check_type, QualityRule.domain, QualityRule.column_name, QualityRule.definition_id).where(
            QualityRule.dataset_id == dataset_id, QualityRule.is_active == "true")
    )).all()
    runnable = [r for r in rules if r.check_type in target.check_types]
    if not rules:
        # 규칙이 아예 없으면 경로 이야기를 섞지 않는다(DEF-157)
        raise RunConflictError("이 데이터셋에 활성 규칙이 없습니다. 품질 규칙을 먼저 추가하세요.")
    if not runnable:
        raise RunConflictError(
            "원천을 직접 읽어 평가할 수 있는 활성 규칙이 없습니다(Trino 함수·커스텀 SQL 규칙은 Trino 연결이 필요합니다)."
            if target.engine == DIRECT_ENGINE else "Trino 로 평가할 수 있는 활성 규칙이 없습니다.")
    if keys and not any(quality_indicators.wants(keys, r.check_type, r.domain, r.column_name) for r in runnable):
        raise RunConflictError("선택한 진단 기준에 해당하는 활성 규칙이 없습니다.")
    if definition_ids and not any(
            wants_definition(definition_ids, r.definition_id)
            and quality_indicators.wants(keys, r.check_type, r.domain, r.column_name) for r in runnable):
        raise RunConflictError("선택한 품질 지표가 할당된 활성 규칙이 없습니다.")

    run = QualityRun(
        dataset_id=dataset_id, engine=target.engine, engine_datasource_id=target.datasource_id,
        engine_table=target.table.display, trigger_type=trigger_type, status="QUEUED",
        rules_total=sum(1 for r in rules if in_scope(
            keys, definition_ids, r.check_type, r.domain, r.column_name, r.definition_id)),
        requested_by=requested_by, queued_at=_now(),
        indicators=quality_indicators.dump(keys), definition_ids=dump_definition_ids(definition_ids),
        job_run_id=job_run_id,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    logger.info("품질 진단 실행 요청: run_id=%d dataset_id=%d engine=%s table=%s indicators=%s definitions=%s by=%s",
                run.id, dataset_id, run.engine, run.engine_table, run.indicators or "전체", run.definition_ids or "전체", requested_by)
    return to_response(run)


# 목록의 상태 묶음 — 데이터 품질 화면의 필터
STATUS_GROUPS: dict[str, tuple[str, ...]] = {
    "ACTIVE": ACTIVE_STATUSES,
    "PROBLEM": ("FAILED", "PARTIAL"),
}


async def _decorate(session: AsyncSession, runs: list[QualityRun]) -> list[QualityRunResponse]:
    """실행 행 → 응답. 데이터셋·데이터 소스 이름을 붙인다(목록 화면용)."""
    dataset_ids = {r.dataset_id for r in runs}
    datasets: dict[int, tuple[str, int | None]] = {}
    if dataset_ids:
        rows = await session.execute(
            select(Dataset.id, Dataset.name, Dataset.datasource_id).where(Dataset.id.in_(dataset_ids)))
        datasets = {i: (name, ds) for i, name, ds in rows.all()}
    source_ids = {ds for _, ds in datasets.values() if ds is not None} | {
        r.engine_datasource_id for r in runs if r.engine_datasource_id is not None}
    names: dict[int, str] = {}
    if source_ids:
        rows = await session.execute(select(Datasource.id, Datasource.name).where(Datasource.id.in_(source_ids)))
        names = dict(rows.all())
    job_run_ids = {r.job_run_id for r in runs if r.job_run_id is not None}
    jobs: dict[int, tuple[int, str]] = {}
    if job_run_ids:
        rows = await session.execute(
            select(QualityJobRun.id, QualityJob.id, QualityJob.name)
            .join(QualityJob, QualityJob.id == QualityJobRun.job_id).where(QualityJobRun.id.in_(job_run_ids)))
        jobs = {job_run_id: (job_id, name) for job_run_id, job_id, name in rows.all()}
    out = []
    for run in runs:
        resp = to_response(run)
        resp.job_id, resp.job_name = jobs.get(run.job_run_id, (None, None))
        name, source_id = datasets.get(run.dataset_id, (None, None))
        resp.dataset_name = name
        resp.datasource_id = source_id
        resp.datasource_name = names.get(source_id) if source_id is not None else None
        resp.engine_datasource_name = names.get(run.engine_datasource_id) if run.engine_datasource_id else None
        out.append(resp)
    await fill_definition_names(session, out)
    return out


async def list_runs(
    session: AsyncSession, dataset_id: int | None = None, status: str | None = None, limit: int = 20,
    *, datasource_id: int | None = None, since: datetime | None = None, cursor: int = 0,
    job_run_id: int | None = None, kind: str | None = None,
) -> list[QualityRunResponse]:
    """실행 목록 — 최근 것부터.

    ``status`` 는 상태 하나 또는 묶음(``ACTIVE`` = 대기·실행 중, ``PROBLEM`` = 실패·일부 실패).
    ``datasource_id`` 는 진단 대상 데이터셋의 원본 데이터 소스, ``since`` 는 요청 시각 하한, ``cursor`` 는 건너뛸 개수,
    ``job_run_id`` 는 작업 실행(그 작업 실행이 넣은 데이터셋 실행만).
    """
    stmt = select(QualityRun).order_by(QualityRun.id.desc()).offset(max(cursor, 0)).limit(limit)
    if dataset_id is not None:
        stmt = stmt.where(QualityRun.dataset_id == dataset_id)
    if status:
        group = STATUS_GROUPS.get(status.upper())
        stmt = stmt.where(QualityRun.status.in_(group) if group else QualityRun.status == status.upper())
    if datasource_id is not None:
        stmt = stmt.where(QualityRun.dataset_id.in_(
            select(Dataset.id).where(Dataset.datasource_id == datasource_id)))
    if since is not None:
        stmt = stmt.where(QualityRun.queued_at >= since)
    if job_run_id is not None:
        stmt = stmt.where(QualityRun.job_run_id == job_run_id)
    if kind is not None:
        stmt = stmt.where(QualityRun.kind == kind)
    runs = list((await session.execute(stmt)).scalars().all())
    await settle_stale(session, runs)
    return await _decorate(session, runs)


async def get_run(session: AsyncSession, run_id: int) -> QualityRunResponse:
    run = await session.get(QualityRun, run_id)
    if run is None:
        raise RunNotFoundError("진단 실행을 찾을 수 없습니다.")
    await settle_stale(session, [run])
    return (await _decorate(session, [run]))[0]


async def retry_run(session: AsyncSession, run_id: int, requested_by: str | None) -> QualityRunResponse:
    """끝난 실행의 데이터셋을 다시 진단한다 — 원래 실행과 같은 종류(진단 · 프로파일)와 범위(진단 기준 · 품질 지표)로
    새 실행을 대기열에 넣는다(작업 실행에는 묶지 않는다, DEF-151)."""
    run = await session.get(QualityRun, run_id)
    if run is None:
        raise RunNotFoundError("진단 실행을 찾을 수 없습니다.")
    await settle_stale(session, [run])
    if run.status in ACTIVE_STATUSES:
        raise RunConflictError("아직 끝나지 않은 실행입니다.")
    return await create_run(
        session, run.dataset_id, requested_by, indicators=quality_indicators.load(run.indicators),
        definition_ids=load_definition_ids(run.definition_ids), kind=run.kind or CHECK_KIND)


async def cancel_run(session: AsyncSession, run_id: int, requested_by: str | None) -> QualityRunResponse:
    """취소 — 대기 중이면 바로 취소하고, 실행 중이면 워커가 다음 폴링에서 Trino 쿼리를 취소한다."""
    run = await session.get(QualityRun, run_id)
    if run is None:
        raise RunNotFoundError("진단 실행을 찾을 수 없습니다.")
    await settle_stale(session, [run])
    if run.status not in ACTIVE_STATUSES:
        raise RunConflictError("이미 끝난 실행은 중지할 수 없습니다.")
    run.cancel_requested_by = requested_by or "unknown"
    if run.status == "QUEUED":
        run.status = "CANCELLED"
        run.finished_at = _now()
    await session.flush()
    logger.info("품질 진단 실행 취소 요청: run_id=%d status=%s by=%s", run.id, run.status, requested_by)
    return to_response(run)


def _worker_response(w: QualityWorker, cutoff: datetime) -> QualityWorkerResponse:
    hb = _as_utc(w.heartbeat_at)
    return QualityWorkerResponse(
        worker_id=w.worker_id, hostname=w.hostname, version=w.version, started_at=w.started_at,
        heartbeat_at=w.heartbeat_at, current_run_id=w.current_run_id,
        alive=hb is not None and hb >= cutoff,
    )


async def list_workers(session: AsyncSession) -> list[QualityWorkerResponse]:
    """워커 목록과 생존 여부."""
    cutoff = _now() - timedelta(seconds=WORKER_ALIVE_SECONDS)
    rows = (await session.execute(
        select(QualityWorker).order_by(QualityWorker.worker_id)
    )).scalars().all()
    return [_worker_response(w, cutoff) for w in rows]


async def service_status(session: AsyncSession) -> QualityServiceStatus:
    """살아 있는 워커와 대기열 크기 — 데이터 품질 화면 상단."""
    workers = [w for w in await list_workers(session) if w.alive]
    counts = dict((await session.execute(
        select(QualityRun.status, func.count()).where(QualityRun.status.in_(ACTIVE_STATUSES))
        .group_by(QualityRun.status)
    )).all())
    return QualityServiceStatus(
        workers_alive=len(workers), running=counts.get("RUNNING", 0), queued=counts.get("QUEUED", 0),
        workers=workers,
    )


async def remove_worker(session: AsyncSession, worker_id: str) -> None:
    """워커가 정상 종료하며 자기 행을 지운다."""
    worker = await session.get(QualityWorker, worker_id)
    if worker is not None:
        await session.delete(worker)
        await session.flush()


# ---------------------------------------------------------------------------
# 대기열 (품질 워커)
# ---------------------------------------------------------------------------

async def claim_next(session: AsyncSession, worker_id: str) -> int | None:
    """대기 중인 실행 하나를 잡아 RUNNING 으로 바꾸고 id 를 돌려준다. 없으면 None.

    대기·실행 중인 행을 모두 잠그고 고르므로 워커 여럿이 같은 실행을 잡지 않는다. 같은 원본 데이터 소스의
    실행이 이미 돌고 있으면 그 데이터 소스의 대기 실행은 건너뛴다.
    """
    rows = (await session.execute(
        select(QualityRun, Dataset.datasource_id)
        .join(Dataset, Dataset.id == QualityRun.dataset_id)
        .where(QualityRun.status.in_(ACTIVE_STATUSES))
        .order_by(QualityRun.queued_at, QualityRun.id)
        .with_for_update(of=QualityRun)
    )).all()
    await settle_stale(session, [r for r, _ in rows])
    busy = {source_id for r, source_id in rows if r.status == "RUNNING"}
    for run, source_id in rows:
        if run.status != "QUEUED" or source_id in busy:
            continue
        now = _now()
        run.status = "RUNNING"
        run.worker_id = worker_id
        run.started_at = now
        run.heartbeat_at = now
        await session.flush()
        return run.id
    return None


async def assert_worker_owner(session: AsyncSession, worker_id: str, owner: str | None) -> None:
    """이 워커 ID 를 다른 인증 주체가 등록했으면 ``WorkerOwnerError``. 주인이 없는 행(예전 버전)은 이 주체의 것으로 한다.

    오래(``STALE_SECONDS``) 소식이 없는 워커의 ID 는 넘겨준다 — 그 워커의 실행은 이미 실패로 정리됐고, API 키를 새로
    발급해 같은 ID 로 다시 띄우는 경우를 막지 않기 위해서다.
    """
    worker = await session.get(QualityWorker, worker_id)
    if worker is None or not owner:
        return
    if worker.owner is None:
        worker.owner = owner
    elif worker.owner != owner:
        seen = _as_utc(worker.heartbeat_at)
        if seen is not None and seen > _now() - timedelta(seconds=STALE_SECONDS):
            raise WorkerOwnerError("다른 API 키가 등록한 워커 ID 입니다.")
        worker.owner = owner


async def worker_heartbeat(
    session: AsyncSession, worker_id: str, hostname: str | None, current_run_id: int | None,
    version: str | None = None, owner: str | None = None,
) -> None:
    """워커 생존 보고 — 행이 없으면 만든다(``owner`` 를 주인으로). 오래 소식이 없는 다른 워커 행은 지운다."""
    worker = await session.get(QualityWorker, worker_id)
    now = _now()
    if worker is None:
        session.add(QualityWorker(
            worker_id=worker_id, hostname=hostname, version=version, started_at=now, heartbeat_at=now,
            current_run_id=current_run_id, owner=owner))
    else:
        worker.heartbeat_at = now
        worker.current_run_id = current_run_id
        if hostname:
            worker.hostname = hostname
        if version:
            worker.version = version
    # 비정상 종료한 워커의 행이 쌓이지 않게 치운다
    await session.execute(delete(QualityWorker).where(
        QualityWorker.worker_id != worker_id,
        QualityWorker.heartbeat_at < now - timedelta(seconds=WORKER_FORGET_SECONDS)))
    await session.flush()
