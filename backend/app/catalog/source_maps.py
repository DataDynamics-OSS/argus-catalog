# SPDX-License-Identifier: Apache-2.0
"""수동 데이터셋 원천 매핑 (TODO-CAT-001, design/catalog-registration-design.md 5 · 6장).

수동 데이터셋(직접 등록한 ARGUS 카탈로그의 수동 등록 데이터 소스 소속)에 "실제로는 데이터 소스 X 의
``db.table``" 이라는 참조를 단다. 매핑해도 데이터셋의 소속 · URN 은 바뀌지 않는다.

매핑할 때 검사
- ① 중복(차단): 같은 데이터 소스 · 경로를 다른 수동 데이터셋이 매핑했거나, 그 경로의 데이터셋
  (URN ``{datasource_id}.{path}.dataset``)이 이미 있으면 409 — 한 테이블에 데이터셋 하나.
- ② 존재 · ③ 스키마 비교(확인용): 원천에서 테이블을 읽어 결과만 기록한다. 매핑을 막지 않는다.
  비교 결과로 수동 메타데이터를 자동으로 바꾸지 않고, [원천 스키마 가져오기]로 사람이 반영한다.

수집과의 관계: 매핑된 경로는 내장 수집 · metadata-sync 가 데이터셋을 만들지 않는다
(``find_mapping_for_path`` · ``mapped_paths``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.catalogs_service import (
    CatalogConflictError,
    CatalogNotFoundError,
    is_manual_datasource,
)
from app.catalog.models import Dataset, DatasetSchema, DatasetSourceMap, Datasource
from app.catalog.schemas import (
    DatasetSourceMapRequest,
    DatasetSourceMapResponse,
    DatasourceSourceMapEntry,
    SchemaColumnDiff,
    SourceCheckResult,
    SourceMapConflict,
    SourceMapPreview,
)

logger = logging.getLogger(__name__)


class SourceMapConflictError(CatalogConflictError):
    """① 중복 — 라우터가 409 와 함께 충돌 데이터셋을 돌려준다."""

    def __init__(self, conflict: SourceMapConflict):
        self.conflict = conflict
        label = "수집된 데이터셋" if conflict.kind == "SYNCED_DATASET" else "다른 수동 데이터셋의 매핑"
        super().__init__(
            f"이 테이블은 이미 {label}({conflict.dataset_name}, id={conflict.dataset_id})이 있습니다. "
            "한 테이블에는 데이터셋 하나만 둡니다 — 수집된 데이터셋을 쓰거나, 수집 범위에서 빼고 그 데이터셋을 지운 뒤 매핑하세요."
            if conflict.kind == "SYNCED_DATASET" else
            f"이 테이블은 이미 {label}({conflict.dataset_name}, id={conflict.dataset_id})에 쓰이고 있습니다.")


def normalize_path(path: str) -> str:
    """원천 경로 정리 — 앞뒤 공백 · 빈 토막 제거. 대소문자는 보존한다(Oracle 등 원천 이름 그대로 접속)."""
    return ".".join(p.strip() for p in path.strip().strip(".").split(".") if p.strip())


def path_key(path: str) -> str:
    """경로 비교 키 — 대소문자 무시(수집 경로 · 다른 매핑과 비교할 때)."""
    return normalize_path(path).lower()


# ---------------------------------------------------------------------------
# 수집이 쓰는 조회
# ---------------------------------------------------------------------------

async def find_mapping_for_path(session: AsyncSession, datasource_id: int, path: str) -> DatasetSourceMap | None:
    """데이터 소스 · 경로를 매핑한 수동 데이터셋 매핑. 없으면 None."""
    return (await session.execute(
        select(DatasetSourceMap).where(
            DatasetSourceMap.datasource_id == datasource_id,
            func.lower(DatasetSourceMap.remote_path) == path_key(path),
        )
    )).scalars().first()


async def mapped_paths(session: AsyncSession, datasource_id: int) -> set[str]:
    """데이터 소스에 매핑된 경로 비교 키(소문자) 집합 — 내장 수집이 건너뛸 테이블."""
    return {path_key(p) for p in (await session.execute(
        select(DatasetSourceMap.remote_path).where(DatasetSourceMap.datasource_id == datasource_id)
    )).scalars().all()}


async def list_datasource_maps(session: AsyncSession, datasource_id: int) -> list[DatasourceSourceMapEntry]:
    """데이터 소스에 매핑된 경로 목록(``GET /datasources/{id}/source-maps``)."""
    if await session.get(Datasource, datasource_id) is None:
        raise CatalogNotFoundError("데이터 소스를 찾을 수 없습니다.")
    rows = (await session.execute(
        select(DatasetSourceMap.remote_path, DatasetSourceMap.dataset_id)
        .where(DatasetSourceMap.datasource_id == datasource_id)
        .order_by(DatasetSourceMap.remote_path)
    )).all()
    return [DatasourceSourceMapEntry(remote_path=p, dataset_id=d) for p, d in rows]


# ---------------------------------------------------------------------------
# 매핑 조회 · 미리보기 · 저장 · 해제
# ---------------------------------------------------------------------------

async def _manual_dataset_or_error(session: AsyncSession, dataset_id: int) -> Dataset:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise CatalogNotFoundError("데이터셋을 찾을 수 없습니다.")
    if not is_manual_datasource(await session.get(Datasource, dataset.datasource_id)):
        raise CatalogConflictError("원천 매핑은 직접 등록한 카탈로그의 수동 데이터셋에만 할 수 있습니다.")
    return dataset


async def _target_datasource(session: AsyncSession, datasource_id: int) -> Datasource:
    ds = await session.get(Datasource, datasource_id)
    if ds is None:
        raise CatalogNotFoundError("데이터 소스를 찾을 수 없습니다.")
    if is_manual_datasource(ds):
        raise CatalogConflictError("수동 등록 데이터 소스에는 매핑할 수 없습니다. 실제 데이터 소스를 고르세요.")
    return ds


async def _find_conflict(
    session: AsyncSession, dataset_id: int, ds: Datasource, path: str,
) -> SourceMapConflict | None:
    """① 중복 — 다른 수동 데이터셋의 매핑, 또는 그 경로의 (수집) 데이터셋."""
    other = (await session.execute(
        select(DatasetSourceMap.dataset_id, Dataset.name, Dataset.urn)
        .join(Dataset, Dataset.id == DatasetSourceMap.dataset_id)
        .where(DatasetSourceMap.datasource_id == ds.id,
               func.lower(DatasetSourceMap.remote_path) == path_key(path),
               DatasetSourceMap.dataset_id != dataset_id)
    )).first()
    if other is not None:
        return SourceMapConflict(kind="MANUAL_MAPPING", dataset_id=other[0], dataset_name=other[1], urn=other[2])
    urn = f"{ds.datasource_id}.{path}.dataset".lower()
    synced = (await session.execute(
        select(Dataset.id, Dataset.name, Dataset.urn)
        .where(Dataset.datasource_id == ds.id, func.lower(Dataset.urn) == urn)
    )).first()
    if synced is not None:
        return SourceMapConflict(kind="SYNCED_DATASET", dataset_id=synced[0], dataset_name=synced[1], urn=synced[2])
    return None


async def _manual_columns(session: AsyncSession, dataset_id: int) -> list[DatasetSchema]:
    return list((await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id).order_by(DatasetSchema.ordinal)
    )).scalars().all())


def _norm_type(value: str | None) -> str:
    return "".join((value or "").split()).lower()


def compare_schema(manual: list[DatasetSchema], source_columns) -> list[SchemaColumnDiff]:
    """③ 스키마 비교 — 컬럼 이름(대소문자 무시) 기준으로 원천에만 · 수동에만 · 타입 다름.

    타입은 카탈로그 공통 타입(field_type)을 비교하고, 수동 쪽에 네이티브 타입을 적었으면 그것도 비교한다.
    """
    src = {c.name.lower(): c for c in source_columns}
    mine = {f.field_path.lower(): f for f in manual}
    diffs: list[SchemaColumnDiff] = []
    for c in source_columns:
        f = mine.get(c.name.lower())
        if f is None:
            diffs.append(SchemaColumnDiff(name=c.name, change="ONLY_SOURCE", source_type=c.native_type or c.data_type))
            continue
        common_diff = _norm_type(f.field_type) != _norm_type(c.data_type)
        native_diff = bool(f.native_type) and _norm_type(f.native_type) != _norm_type(c.native_type)
        if common_diff or native_diff:
            diffs.append(SchemaColumnDiff(
                name=c.name, change="TYPE_DIFF",
                manual_type=f.native_type or f.field_type,
                source_type=c.native_type or c.data_type,
            ))
    for f in manual:
        if f.field_path.lower() not in src:
            diffs.append(SchemaColumnDiff(name=f.field_path, change="ONLY_MANUAL", manual_type=f.native_type or f.field_type))
    return diffs


async def _read_source(session: AsyncSession, ds: Datasource, path: str):
    """원천 테이블 읽기 — (TableInfo | None, SourceCheckResult | None). 두 번째 값은 확인할 수 없을 때의 결과."""
    from app.catalog.sync import SourceUnavailableError, read_source_table
    try:
        return await read_source_table(session, ds, path), None
    except NotImplementedError:
        return None, SourceCheckResult(
            status="UNSUPPORTED", message=f"{ds.type} 유형은 원천 확인을 지원하지 않습니다. 확인 없이 매핑할 수 있습니다.")
    except SourceUnavailableError as e:
        return None, SourceCheckResult(status="UNREACHABLE", message=str(e))


async def check_source(session: AsyncSession, dataset_id: int, ds: Datasource, path: str) -> SourceCheckResult:
    """② 존재 · ③ 스키마 비교. 실패해도 예외 대신 상태로 돌려준다(매핑을 막지 않음)."""
    table, unavailable = await _read_source(session, ds, path)
    if unavailable is not None:
        return unavailable
    if table is None:
        return SourceCheckResult(status="NOT_FOUND", message=f"원천에서 '{path}' 테이블을 찾지 못했습니다.")
    diffs = compare_schema(await _manual_columns(session, dataset_id), table.columns)
    return SourceCheckResult(
        status="DIFF" if diffs else "MATCH",
        message=None if not diffs else f"원천과 다른 컬럼이 {len(diffs)}개 있습니다.",
        source_column_count=len(table.columns),
        diffs=diffs,
    )


async def preview(session: AsyncSession, dataset_id: int, req: DatasetSourceMapRequest) -> SourceMapPreview:
    """매핑 미리보기(저장 없음) — ①은 conflict, ②③은 check(skip_check 면 생략)."""
    await _manual_dataset_or_error(session, dataset_id)
    ds = await _target_datasource(session, req.datasource_id)
    path = normalize_path(req.remote_path)
    conflict = await _find_conflict(session, dataset_id, ds, path)
    check = None
    if conflict is None and not req.skip_check:
        check = await check_source(session, dataset_id, ds, path)
    return SourceMapPreview(datasource_id=ds.id, datasource_name=ds.name, remote_path=path,
                            conflict=conflict, check=check)


async def get_map(session: AsyncSession, dataset_id: int) -> DatasetSourceMapResponse | None:
    row = (await session.execute(
        select(DatasetSourceMap, Datasource)
        .join(Datasource, Datasource.id == DatasetSourceMap.datasource_id)
        .where(DatasetSourceMap.dataset_id == dataset_id)
    )).first()
    if row is None:
        return None
    return _response(*row)


def _response(m: DatasetSourceMap, ds: Datasource) -> DatasetSourceMapResponse:
    detail = None
    if m.check_detail:
        try:
            detail = SourceCheckResult.model_validate_json(m.check_detail)
        except ValueError:
            detail = None
    return DatasetSourceMapResponse(
        dataset_id=m.dataset_id, datasource_id=ds.id, datasource_key=ds.datasource_id,
        datasource_name=ds.name, datasource_type=ds.type, remote_path=m.remote_path,
        check_status=m.check_status, check_detail=detail, checked_at=m.checked_at,
        created_by=m.created_by, created_at=m.created_at, updated_at=m.updated_at,
    )


def _apply_check(m: DatasetSourceMap, check: SourceCheckResult | None) -> None:
    if check is None:
        m.check_status, m.check_detail, m.checked_at = None, None, None
        return
    m.check_status = check.status
    m.check_detail = check.model_dump_json()
    m.checked_at = datetime.now(timezone.utc)


async def save(
    session: AsyncSession, dataset_id: int, req: DatasetSourceMapRequest, actor: str | None,
) -> tuple[DatasetSourceMapResponse, dict | None]:
    """매핑 저장(새로 · 변경). ①이면 :class:`SourceMapConflictError`. (응답, 이전 매핑 요약) — 감사 기록용."""
    p = await preview(session, dataset_id, req)
    if p.conflict is not None:
        raise SourceMapConflictError(p.conflict)
    m = (await session.execute(
        select(DatasetSourceMap).where(DatasetSourceMap.dataset_id == dataset_id)
    )).scalars().first()
    before = None
    if m is None:
        m = DatasetSourceMap(dataset_id=dataset_id, datasource_id=p.datasource_id,
                             remote_path=p.remote_path, created_by=actor)
        session.add(m)
    else:
        before = {"datasource_id": m.datasource_id, "remote_path": m.remote_path}
        m.datasource_id, m.remote_path = p.datasource_id, p.remote_path
    _apply_check(m, p.check)
    await session.commit()
    await session.refresh(m)
    ds = await session.get(Datasource, m.datasource_id)
    logger.info("수동 데이터셋 원천 매핑: dataset=%d → %s.%s (check=%s)",
                dataset_id, ds.datasource_id, m.remote_path, m.check_status)
    await _reresolve_lineage(session, dataset_id)
    return _response(m, ds), before


async def delete(session: AsyncSession, dataset_id: int) -> dict:
    """매핑 해제. 없으면 :class:`CatalogNotFoundError`. 지운 매핑 요약을 돌려준다(감사 기록용)."""
    await _manual_dataset_or_error(session, dataset_id)
    m = (await session.execute(
        select(DatasetSourceMap).where(DatasetSourceMap.dataset_id == dataset_id)
    )).scalars().first()
    if m is None:
        raise CatalogNotFoundError("원천 매핑이 없습니다.")
    removed = {"datasource_id": m.datasource_id, "remote_path": m.remote_path}
    await session.delete(m)
    await session.commit()
    logger.info("수동 데이터셋 원천 매핑 해제: dataset=%d (%s)", dataset_id, removed)
    await _reresolve_lineage(session, dataset_id)
    return removed


async def recheck(session: AsyncSession, dataset_id: int) -> DatasetSourceMapResponse:
    """저장된 매핑의 ②③ 다시 확인."""
    await _manual_dataset_or_error(session, dataset_id)
    row = (await session.execute(
        select(DatasetSourceMap, Datasource)
        .join(Datasource, Datasource.id == DatasetSourceMap.datasource_id)
        .where(DatasetSourceMap.dataset_id == dataset_id)
    )).first()
    if row is None:
        raise CatalogNotFoundError("원천 매핑이 없습니다.")
    m, ds = row
    _apply_check(m, await check_source(session, dataset_id, ds, m.remote_path))
    await session.commit()
    await session.refresh(m)
    return _response(m, ds)


async def import_schema(session: AsyncSession, dataset_id: int) -> dict:
    """원천 스키마 가져오기 — 사람이 누를 때만. 원천 컬럼을 더하고 타입을 맞춘다.

    이름이 같은 컬럼의 설명 · 논리명 · 단위 · 산정기준 · PII 등 큐레이션 값은 그대로 두고, 수동에만 있는 컬럼도
    지우지 않는다. 가져온 뒤 확인 결과를 다시 기록한다. 원천을 읽을 수 없으면 409.
    """
    await _manual_dataset_or_error(session, dataset_id)
    row = (await session.execute(
        select(DatasetSourceMap, Datasource)
        .join(Datasource, Datasource.id == DatasetSourceMap.datasource_id)
        .where(DatasetSourceMap.dataset_id == dataset_id)
    )).first()
    if row is None:
        raise CatalogNotFoundError("원천 매핑이 없습니다.")
    m, ds = row
    table, unavailable = await _read_source(session, ds, m.remote_path)
    if unavailable is not None:
        raise CatalogConflictError(unavailable.message or "원천을 읽을 수 없습니다.")
    if table is None:
        raise CatalogConflictError(f"원천에서 '{m.remote_path}' 테이블을 찾지 못했습니다.")
    manual = await _manual_columns(session, dataset_id)
    by_name = {f.field_path.lower(): f for f in manual}
    added = updated = 0
    next_ordinal = max((f.ordinal for f in manual), default=-1) + 1
    for c in table.columns:
        f = by_name.get(c.name.lower())
        if f is None:
            session.add(DatasetSchema(
                dataset_id=dataset_id, field_path=c.name, field_type=c.data_type, native_type=c.native_type,
                description=c.comment or None, nullable="true" if c.nullable else "false",
                is_primary_key="true" if c.is_primary_key else "false",
                is_unique="true" if c.is_unique else "false",
                is_indexed="true" if c.is_indexed else "false",
                ordinal=next_ordinal,
            ))
            next_ordinal += 1
            added += 1
        elif (f.field_type, f.native_type) != (c.data_type, c.native_type):
            f.field_type, f.native_type = c.data_type, c.native_type
            updated += 1
    await session.flush()
    diffs = compare_schema(await _manual_columns(session, dataset_id), table.columns)
    _apply_check(m, SourceCheckResult(
        status="DIFF" if diffs else "MATCH",
        message=None if not diffs else f"원천과 다른 컬럼이 {len(diffs)}개 있습니다.",
        source_column_count=len(table.columns), diffs=diffs))
    await session.commit()
    logger.info("원천 스키마 가져오기: dataset=%d added=%d updated=%d", dataset_id, added, updated)
    return {"added": added, "updated": updated, "check_status": m.check_status}


async def _reresolve_lineage(session: AsyncSession, dataset_id: int) -> None:
    """매핑이 바뀌면 이 데이터셋에 붙을 수 있는 쿼리 리니지 미해석 행을 다시 해석한다(best-effort)."""
    try:
        from app.catalog.lineage_resolve import resolve_lineage_for_mapping
        await resolve_lineage_for_mapping(session, dataset_id)
    except Exception as e:  # noqa: BLE001 — 리니지 재해석 실패가 매핑을 막지 않도록
        logger.warning("매핑 변경 후 쿼리 리니지 재해석 실패: dataset=%d err=%s", dataset_id, e)
