# SPDX-License-Identifier: Apache-2.0
"""품질 지표 — 미리 정해 두고 컬럼에 할당하는 진단 기준.

"여부는 Y/N", "날짜는 YYYYMMDD", "금액은 0 이상" 같은 기준을 데이터셋마다 규칙으로 다시 적지 않도록, 한곳에서
지표로 정하고 컬럼에 할당한다.

용어 — 화면의 **품질 지표** 는 이 모듈의 정의(``QualityDefinition``)다. 관리지침의 진단 기준 16개
(``indicators.INDICATORS`` — 날짜·여부·금액 …)는 지표를 묶는 **진단 기준** 으로 쓴다(코드에서는 예전 이름대로
indicator · ``category``).

- 할당: 컬럼마다 이 지표를 가리키는 규칙(``QualityRule.definition_id``)을 만든다. 진단·결과·점수·보고서는 지금까지처럼
  규칙으로 돌아가므로 실행 쪽은 지표를 모른다.
- 연결: 지표의 유형·기준값·심각도를 바꾸면 할당된 규칙을 같이 바꾼다(``update_definition``). 규칙의 임계값은 지표와
  같던 것만 따라 바뀐다 — 컬럼마다 따로 준 임계값은 남는다.
- 지표를 지우면 할당된 규칙은 직접 정한 규칙으로 남는다(FK ``SET NULL``).
- 미리 정의할 수 있는 것은 컬럼 하나와 기준값으로 정해지는 유형이다(``indicators.DEFINABLE_TYPES``). 컬럼 간 비교·
  논리관계·계산식·참조관계는 테이블마다 내용이 달라 데이터셋의 품질 탭에서 직접 만든다.
- ``preview_sql``: 지표·규칙이 실행할 진단 쿼리를 진단과 같은 코드(``rule_sql.compile_rule``)로 만들어 보여 준다 — 데이터셋이
  원천 직접 경로면 원천 DB 방언(``rule_direct``)으로.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, DatasetSchema, Datasource
from app.core.sql import LIKE_ESCAPE, like_contains
from app.quality import indicators as quality_indicators
from app.quality import rule_direct, rule_sql, runs, service
from app.quality.models import QualityDefinition, QualityJob, QualityRule
from app.quality.rule_sql import Compiled, Outcome, RuleSpec, TableRef
from app.quality.schemas import (
    QualityAssignment,
    QualityAssignRequest,
    QualityAssignResult,
    QualityAssignSkipped,
    QualityCodeGroupOption,
    QualityColumnCandidate,
    QualityDefinitionJobRef,
    QualityDefinitionResponse,
    QualityDefinitionWrite,
    QualitySqlPreview,
    QualitySqlPreviewRequest,
)

logger = logging.getLogger(__name__)

# 할당할 컬럼 후보를 한 번에 돌려주는 수
CANDIDATE_LIMIT = 300

# 쿼리를 미리 볼 때 실제 테이블을 모르면 쓰는 예시 이름
_SAMPLE_TABLE = TableRef("catalog", "schema", "table")
_SAMPLE_COLUMN = "column_name"

# 기본 지표 — (이름, 분류, 유형, 기준값, 설명). ``add_defaults`` 가 같은 이름이 없는 것만 넣는다
DEFAULTS: tuple[tuple[str, str, str, str | None, str], ...] = (
    ("필수값", "REQUIRED", "NOT_NULL", None, "반드시 있어야 하는 값 — NULL 이 없어야 합니다"),
    ("중복 없음", "DUPLICATE", "UNIQUE", None, "식별자처럼 겹치면 안 되는 값 — 같은 값이 두 번 나오지 않아야 합니다"),
    ("글자 깨짐 없음", "BROKEN_TEXT", "BROKEN_TEXT", None, "깨진 글자(�)나 낱자만 있는 한글(ㄱ·ㅏ)이 없어야 합니다"),
    ("앞뒤 공백·제어 문자 없음", "WHITESPACE", "WHITESPACE", None, "값 앞뒤의 공백과 값 안의 탭·줄바꿈이 없어야 합니다"),
    ("날짜 YYYYMMDD", "DATE", "DATE_FORMAT", "YYYYMMDD", "8자리 날짜(20240131) — 달력에 있는 날짜여야 합니다"),
    ("날짜 YYYY-MM-DD", "DATE", "DATE_FORMAT", "YYYY-MM-DD", "하이픈으로 나눈 날짜(2024-01-31)"),
    ("연월 YYYYMM", "DATE", "DATE_FORMAT", "YYYYMM", "6자리 연월(202401)"),
    ("일시 YYYY-MM-DD HH24:MI:SS", "DATE", "DATE_FORMAT", "YYYY-MM-DD HH24:MI:SS", "날짜와 시각(2024-01-31 13:05:00)"),
    ("일시 YYYY-MM-DD HH24:MI:SS.SSS", "DATE", "DATE_FORMAT", "YYYY-MM-DD HH24:MI:SS.SSS",
     "밀리초까지 있는 날짜와 시각(2024-01-31 13:05:00.123)"),
    ("전화번호", "NUMBER", "REGEX", r"^0\d{1,2}-\d{3,4}-\d{4}$", "하이픈으로 나눈 전화번호(02-123-4567, 010-1234-5678)"),
    ("우편번호", "NUMBER", "REGEX", r"^\d{5}$", "5자리 우편번호"),
    ("사업자등록번호", "NUMBER", "REGEX", r"^\d{3}-\d{2}-\d{5}$", "하이픈으로 나눈 사업자등록번호(123-45-67890)"),
    ("여부 Y/N", "YN", "ACCEPTED_VALUES", "Y,N", "여부 컬럼 — Y 또는 N 만 있어야 합니다"),
    ("금액 0 이상", "AMOUNT", "MIN_VALUE", "0", "금액은 음수가 될 수 없습니다"),
    ("수량 0 이상", "QUANTITY", "MIN_VALUE", "0", "수량·건수는 음수가 될 수 없습니다"),
    ("비율 0 이상", "RATE", "MIN_VALUE", "0", "비율(%)의 하한"),
    ("비율 100 이하", "RATE", "MAX_VALUE", "100", "비율(%)의 상한"),
    # AI-Ready 품질 지표(TODO-096 5단계)
    ("더미 값 없음", "DUMMY", "DUMMY_VALUE", None,
     "999999 · -999 · N/A 처럼 값이 없음을 대신 적은 자리채움 값이 없어야 합니다 (AI-Ready 통계적 타당성)"),
    ("일 단위 연속", "CONTINUITY", "CONTINUITY", "DAY",
     "날짜 컬럼의 처음부터 마지막 날까지 빠진 날이 없어야 합니다 — 문자열 날짜면 형식을 덧붙입니다(DAY YYYYMMDD) (AI-Ready 적시성)"),
    ("월 단위 연속", "CONTINUITY", "CONTINUITY", "MONTH",
     "처음부터 마지막 달까지 빠진 달이 없어야 합니다 (AI-Ready 적시성)"),
    ("한 값 쏠림 90% 이하", "DIVERSITY", "DIVERSITY", None,
     "범주 · 라벨 컬럼에서 가장 많은 값이 전체(NULL 제외)의 90% 를 넘지 않아야 합니다 (AI-Ready 데이터 · 라벨 다양성)"),
)


class DefinitionNotFoundError(LookupError):
    """지표가 없다 → 404."""


class DefinitionConflictError(RuntimeError):
    """같은 이름의 지표가 있다 → 409."""


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------

async def _get(session: AsyncSession, definition_id: int) -> QualityDefinition:
    definition = await session.get(QualityDefinition, definition_id)
    if definition is None:
        raise DefinitionNotFoundError(f"품질 지표를 찾을 수 없습니다: {definition_id}")
    return definition


async def _check_name(session: AsyncSession, name: str, own_id: int | None = None) -> None:
    taken = await session.scalar(select(QualityDefinition.id).where(func.lower(QualityDefinition.name) == name.lower()))
    if taken is not None and taken != own_id:
        raise DefinitionConflictError(f"같은 이름의 품질 지표가 있습니다: {name}")


async def _responses(session: AsyncSession, definitions: list[QualityDefinition]) -> list[QualityDefinitionResponse]:
    counts: dict[int, tuple[int, int]] = {}
    ids = [d.id for d in definitions]
    if ids:
        rows = await session.execute(
            select(QualityRule.definition_id, func.count(QualityRule.id), func.count(func.distinct(QualityRule.dataset_id)))
            .where(QualityRule.definition_id.in_(ids)).group_by(QualityRule.definition_id))
        counts = {r[0]: (r[1], r[2]) for r in rows.all()}
    users = await _jobs_using(session, set(ids))
    out = []
    for d in definitions:
        item = QualityDefinitionResponse.model_validate(d)
        item.rules, item.datasets = counts.get(d.id, (0, 0))
        item.jobs = users.get(d.id, [])
        out.append(item)
    return out


async def _jobs_using(session: AsyncSession, ids: set[int]) -> dict[int, list[QualityDefinitionJobRef]]:
    """지표 ID → 그 지표를 진단 범위(definition_ids)로 고른 작업들(이름 순) (DEF-148).

    작업의 지표 목록은 저장할 때 정해진다 — 지표를 지우면 그 작업은 이 지표의 규칙을 진단하지 않으므로 삭제 확인 창이 알린다.
    """
    if not ids:
        return {}
    rows = await session.execute(
        select(QualityJob.id, QualityJob.name, QualityJob.definition_ids)
        .where(QualityJob.definition_ids.is_not(None)).order_by(QualityJob.name, QualityJob.id))
    out: dict[int, list[QualityDefinitionJobRef]] = {}
    for job_id, name, value in rows.all():
        chosen = runs.load_definition_ids(value)
        for i in chosen:
            if i in ids:
                out.setdefault(i, []).append(QualityDefinitionJobRef(id=job_id, name=name, only=len(set(chosen)) == 1))
    return out


async def list_definitions(session: AsyncSession) -> list[QualityDefinitionResponse]:
    """지표 전체 — 분류 순서, 그 안에서는 이름 순."""
    definitions = list((await session.execute(select(QualityDefinition))).scalars().all())
    order = {i.key: n for n, i in enumerate(quality_indicators.INDICATORS)}
    definitions.sort(key=lambda d: (order.get(d.category, len(order)), d.name))
    return await _responses(session, definitions)


async def get_definition(session: AsyncSession, definition_id: int) -> QualityDefinitionResponse:
    return (await _responses(session, [await _get(session, definition_id)]))[0]


def _domain_of(definition: QualityDefinition) -> str | None:
    """이 지표의 규칙에 적을 도메인 — 분류가 도메인으로 갈리는 유형(허용 값·범위)일 때만."""
    category = quality_indicators.get(definition.category)
    if category is None or definition.check_type not in quality_indicators.DOMAINS:
        return None
    return category.domain


def _fill(definition: QualityDefinition, req: QualityDefinitionWrite) -> None:
    definition.name = req.name
    definition.category = req.category
    definition.check_type = req.check_type
    definition.expected_value = req.expected_value
    definition.threshold = req.threshold
    definition.severity = req.severity
    definition.description = (req.description or "").strip() or None


async def create_definition(session: AsyncSession, req: QualityDefinitionWrite) -> QualityDefinitionResponse:
    await _check_name(session, req.name)
    definition = QualityDefinition()
    _fill(definition, req)
    session.add(definition)
    await session.flush()
    await session.refresh(definition)
    logger.info("품질 지표 생성됨: id=%d name=%s type=%s", definition.id, definition.name, definition.check_type)
    return (await _responses(session, [definition]))[0]


async def update_definition(
    session: AsyncSession, definition_id: int, req: QualityDefinitionWrite,
) -> QualityDefinitionResponse:
    """지표를 바꾸고 할당된 규칙을 맞춘다. 규칙 이름·임계값은 지표와 같던 것만 따라 바뀐다."""
    definition = await _get(session, definition_id)
    await _check_name(session, req.name, definition.id)
    old_name = definition.name
    old_threshold = float(definition.threshold) if definition.threshold is not None else 100.0
    _fill(definition, req)
    domain = _domain_of(definition)
    rules = (await session.execute(
        select(QualityRule).where(QualityRule.definition_id == definition.id))).scalars().all()
    for rule in rules:
        rule.check_type = definition.check_type
        rule.expected_value = definition.expected_value
        rule.severity = definition.severity
        rule.domain = domain
        if rule.rule_name == old_name:
            rule.rule_name = definition.name
        if rule.threshold is None or float(rule.threshold) == old_threshold:
            rule.threshold = definition.threshold
    await session.flush()
    await session.refresh(definition)
    logger.info("품질 지표 수정됨: id=%d name=%s 할당된 규칙 %d개 반영", definition.id, definition.name, len(rules))
    return (await _responses(session, [definition]))[0]


async def delete_definition(session: AsyncSession, definition_id: int) -> int:
    """지표를 지운다. 할당된 규칙은 직접 정한 규칙으로 남는다. 남은 규칙 수를 돌려준다."""
    definition = await _get(session, definition_id)
    rules = (await session.execute(
        select(QualityRule).where(QualityRule.definition_id == definition.id))).scalars().all()
    for rule in rules:
        rule.definition_id = None  # DB 의 SET NULL 을 기다리지 않고 세션의 객체도 맞춘다
    await session.flush()
    await session.delete(definition)
    await session.flush()
    logger.info("품질 지표 삭제됨: id=%d name=%s (직접 정한 규칙으로 남은 것 %d개)", definition_id, definition.name, len(rules))
    return len(rules)


async def add_defaults(session: AsyncSession) -> int:
    """기본 지표 가운데 같은 이름이 없는 것을 넣는다. 넣은 수를 돌려준다."""
    names = {n.lower() for n in (await session.execute(select(QualityDefinition.name))).scalars().all()}
    added = 0
    for name, category, check_type, expected, description in DEFAULTS:
        if name.lower() in names:
            continue
        session.add(QualityDefinition(
            name=name, category=category, check_type=check_type, expected_value=expected,
            threshold=100.0, severity="WARNING", description=description))
        added += 1
    await session.flush()
    logger.info("기본 품질 지표 %d개 추가", added)
    return added


# ---------------------------------------------------------------------------
# 할당
# ---------------------------------------------------------------------------

async def list_assignments(session: AsyncSession, definition_id: int) -> list[QualityAssignment]:
    """지표가 할당된 컬럼들 — 데이터셋 이름, 컬럼 순."""
    await _get(session, definition_id)
    rows = await session.execute(
        select(QualityRule, Dataset.name, Datasource.name).join(Dataset, Dataset.id == QualityRule.dataset_id)
        .outerjoin(Datasource, Datasource.id == Dataset.datasource_id)
        .where(QualityRule.definition_id == definition_id)
        .order_by(Dataset.name, Datasource.name, QualityRule.dataset_id, QualityRule.column_name, QualityRule.id))
    return [
        QualityAssignment(
            rule_id=rule.id, dataset_id=rule.dataset_id, dataset_name=name, datasource_name=source,
            column_name=rule.column_name,
            threshold=float(rule.threshold) if rule.threshold is not None else 100.0, is_active=rule.is_active)
        for rule, name, source in rows.all()
    ]


def _same_expected(a: str | None, b: str | None) -> bool:
    return (a or "").strip() == (b or "").strip()


async def assign(session: AsyncSession, definition_id: int, req: QualityAssignRequest) -> QualityAssignResult:
    """지표를 컬럼들에 할당한다 — 컬럼마다 이 지표를 가리키는 규칙을 만든다.

    이미 할당된 컬럼은 건너뛴다. 같은 유형·기준값으로 직접 만들어 둔 규칙이 있으면 새로 만들지 않고 그 규칙을
    지표에 연결한다(결과 이력이 이어진다).
    """
    definition = await _get(session, definition_id)
    domain = _domain_of(definition)
    dataset_ids = sorted({c.dataset_id for c in req.columns})
    columns: dict[int, dict[str, str]] = {}
    existing: dict[tuple[int, str], list[QualityRule]] = {}
    for start in range(0, len(dataset_ids), 500):
        chunk = dataset_ids[start:start + 500]
        for ds_id, path in (await session.execute(
            select(DatasetSchema.dataset_id, DatasetSchema.field_path).where(DatasetSchema.dataset_id.in_(chunk))
        )).all():
            columns.setdefault(ds_id, {})[path.lower()] = path
        for rule in (await session.execute(
            select(QualityRule).where(QualityRule.dataset_id.in_(chunk), QualityRule.check_type == definition.check_type)
        )).scalars().all():
            existing.setdefault((rule.dataset_id, (rule.column_name or "").lower()), []).append(rule)
        for ds_id in (await session.execute(select(Dataset.id).where(Dataset.id.in_(chunk)))).scalars().all():
            columns.setdefault(ds_id, {})

    result = QualityAssignResult()
    seen: set[tuple[int, str]] = set()
    for ref in req.columns:
        key = (ref.dataset_id, ref.column_name.strip().lower())
        if key in seen:
            continue
        seen.add(key)

        def skip(reason: str, ref=ref) -> None:
            result.skipped.append(QualityAssignSkipped(dataset_id=ref.dataset_id, column_name=ref.column_name, reason=reason))

        if ref.dataset_id not in columns:
            skip("데이터셋을 찾을 수 없습니다")
            continue
        column = columns[ref.dataset_id].get(key[1])
        if column is None:
            skip("데이터셋에 이 컬럼이 없습니다")
            continue
        same_column = existing.get(key, [])
        if any(r.definition_id == definition.id for r in same_column):
            skip("이미 할당돼 있습니다")
            continue
        direct = next((r for r in same_column
                       if r.definition_id is None and _same_expected(r.expected_value, definition.expected_value)), None)
        if direct is not None:
            direct.definition_id = definition.id
            direct.severity = definition.severity
            direct.domain = domain
            result.adopted += 1
            continue
        session.add(QualityRule(
            dataset_id=ref.dataset_id, rule_name=definition.name, check_type=definition.check_type, column_name=column,
            expected_value=definition.expected_value, threshold=definition.threshold, severity=definition.severity,
            domain=domain, definition_id=definition.id, is_active="true"))
        result.created += 1
    await session.flush()
    logger.info("품질 지표 할당: id=%d name=%s 새 규칙 %d개, 연결 %d개, 건너뜀 %d개",
                definition.id, definition.name, result.created, result.adopted, len(result.skipped))
    return result


async def unassign(session: AsyncSession, definition_id: int, rule_ids: list[int]) -> int:
    """할당을 해제한다 — 그 컬럼의 규칙을 지운다(결과도 함께). 이 지표의 규칙이 아닌 것은 건드리지 않는다."""
    await _get(session, definition_id)
    owned = (await session.execute(
        select(QualityRule.id).where(QualityRule.id.in_(rule_ids), QualityRule.definition_id == definition_id)
    )).scalars().all()
    removed = 0
    for rule_id in owned:
        if await service.delete_rule(session, rule_id):
            removed += 1
    logger.info("품질 지표 할당 해제: id=%d 규칙 %d개 삭제", definition_id, removed)
    return removed


def _constraint_hint(category: str):
    """컬럼의 제약으로 보아 이 분류가 맞을 법한 조건(SQL). 이름으로만 추정하는 분류는 None."""
    if category == "REQUIRED":
        return or_(DatasetSchema.nullable == "false", DatasetSchema.is_primary_key == "true")
    if category == "DUPLICATE":
        return or_(DatasetSchema.is_primary_key == "true", DatasetSchema.is_unique == "true")
    return None


async def list_candidates(
    session: AsyncSession, definition_id: int, q: str | None = None, catalog_id: int | None = None,
) -> list[QualityColumnCandidate]:
    """지표를 할당할 컬럼 후보.

    검색어가 없으면 추천만 — 컬럼 이름의 끝 단어(``_yn``·``_amt``·``_ymd`` …)나 제약(NOT NULL·PK·UNIQUE)으로 보아
    이 지표의 분류에 맞을 법한 컬럼. 검색어가 있으면 데이터셋·컬럼 이름에 그 말이 들어간 컬럼 전부.
    """
    definition = await _get(session, definition_id)
    category = definition.category
    stmt = (
        select(DatasetSchema, Dataset.name, Datasource.name).join(Dataset, Dataset.id == DatasetSchema.dataset_id)
        .outerjoin(Datasource, Datasource.id == Dataset.datasource_id)
        .where(Dataset.status == "active")
    )
    if catalog_id is not None:
        stmt = stmt.where(Dataset.catalog_id == catalog_id)
    text = (q or "").strip().lower()
    hint = _constraint_hint(category)
    words = quality_indicators.hint_words(category)
    if text:
        like = like_contains(text)
        stmt = stmt.where(or_(DatasetSchema.field_path.ilike(like, escape=LIKE_ESCAPE),
                              Dataset.name.ilike(like, escape=LIKE_ESCAPE)))
    else:
        guesses = [func.lower(DatasetSchema.field_path).endswith(w.lower(), autoescape=True) for w in sorted(words)]
        if hint is not None:
            guesses.append(hint)
        if not guesses:
            return []  # 이름·제약으로 추정할 수 없는 분류 — 검색해서 고른다
        stmt = stmt.where(or_(*guesses))
    rows = (await session.execute(
        stmt.order_by(Dataset.name, Datasource.name, DatasetSchema.dataset_id, DatasetSchema.ordinal).limit(CANDIDATE_LIMIT * 4)
    )).all()

    def by_constraint(field: DatasetSchema) -> bool:
        if category == "REQUIRED":
            return field.nullable == "false" or field.is_primary_key == "true"
        if category == "DUPLICATE":
            return field.is_primary_key == "true" or field.is_unique == "true"
        return False

    assigned = {
        (ds_id, (column or "").lower()) for ds_id, column in (await session.execute(
            select(QualityRule.dataset_id, QualityRule.column_name).where(QualityRule.definition_id == definition_id)
        )).all()
    }
    out: list[QualityColumnCandidate] = []
    for field, dataset_name, datasource_name in rows:
        suggested = by_constraint(field) or quality_indicators.suggests(category, field.field_path)
        if not text and not suggested:
            continue  # LIKE 는 끝 글자만 본다 — 끝 단어가 아닌 것(예: ``…syn``)을 걸러 낸다
        out.append(QualityColumnCandidate(
            dataset_id=field.dataset_id, dataset_name=dataset_name, datasource_name=datasource_name,
            column_name=field.field_path,
            field_type=field.native_type or field.field_type, suggested=suggested,
            assigned=(field.dataset_id, field.field_path.lower()) in assigned))
        if len(out) >= CANDIDATE_LIMIT:
            break
    return out


# ---------------------------------------------------------------------------
# 표준 코드 그룹 — 허용 값 규칙이 가리킬 수 있는 것 (TODO-096)
# ---------------------------------------------------------------------------

_CODE_SAMPLE = 5


async def list_code_groups(session: AsyncSession) -> list[QualityCodeGroupOption]:
    """모든 표준 사전의 코드 그룹 — 사전 · 그룹 이름 순. 사용 중인 코드 값 수와 앞의 값 몇 개를 함께 준다."""
    from app.standard.models import CodeGroup, CodeValue, StandardDictionary

    groups = (await session.execute(
        select(CodeGroup, StandardDictionary.dict_name)
        .outerjoin(StandardDictionary, StandardDictionary.id == CodeGroup.dictionary_id)
        .order_by(StandardDictionary.dict_name, CodeGroup.group_name)
    )).all()
    values: dict[int, list[str]] = {}
    for group_id, value in (await session.execute(
        select(CodeValue.code_group_id, CodeValue.code_value).where(CodeValue.is_active != "false")
        .order_by(CodeValue.code_group_id, CodeValue.sort_order, CodeValue.code_value)
    )).all():
        values.setdefault(group_id, []).append(value)
    return [
        QualityCodeGroupOption(id=g.id, name=g.group_name, dictionary_name=dict_name,
                               value_count=len(values.get(g.id, [])), sample_values=values.get(g.id, [])[:_CODE_SAMPLE])
        for g, dict_name in groups
    ]


# ---------------------------------------------------------------------------
# 진단 쿼리 미리보기
# ---------------------------------------------------------------------------

def _judgement(check_type: str, expected: str | None, threshold: float) -> tuple[str, tuple[str, ...]]:
    """(통과 판정 설명, 쿼리의 각 식이 뜻하는 것). 식의 순서는 ``rule_sql.compile_rule`` 과 같다."""
    if check_type == "NOT_NULL":
        return (f"NULL 이 아닌 값의 비율((전체 행 수 − NULL 건수) ÷ 전체 행 수)이 임계값 {threshold:g}% 이상이면 통과합니다.",
                ("NULL 건수",))
    if check_type == "UNIQUE":
        return (f"고유값 수 ÷ 전체 행 수가 임계값 {threshold:g}% 이상이면 통과합니다.", ("고유값 수",))
    if check_type in ("MIN_VALUE", "MAX_VALUE"):
        is_min = check_type == "MIN_VALUE"
        word, sign = ("최소값", "이상") if is_min else ("최대값", "이하")
        return (f"{word}이 {expected} {sign}이면 통과합니다. 벗어난 값의 수가 오류 건수입니다(숫자로 읽을 수 없는 값은 세지 않습니다).",
                (word, "진단 건수 (NULL 제외)", "오류 건수 — 범위를 벗어난 값"))
    if check_type == "ROW_COUNT":
        return (f"전체 행 수가 {expected or 0} 이상이면 통과합니다.", ())
    if check_type == "LOGICAL":
        return ("전제가 참인 행 가운데 결론이 거짓인 행이 없으면 통과합니다.",
                ("진단 건수 — 전제가 참인 행", "오류 건수 — 결론이 거짓인 행"))
    if check_type == "FUNCTION":
        return ("함수가 true 를 돌려주지 않은 값이 하나도 없으면 통과합니다. 값이 NULL 이면 진단하지 않고, 함수가 NULL 을 돌려주면 "
                "오류로 셉니다. 함수는 Trino 에 미리 등록해 두어야 하며 컬럼 값을 인자 하나로 받아 boolean 을 돌려줘야 합니다.",
                ("진단 건수 (NULL 제외)", "오류 건수 — 함수가 true 가 아닌 값"))
    if check_type == "REFERENTIAL":
        return ("참조 테이블에 없는 값이 하나도 없으면 통과합니다. NULL 은 진단하지 않습니다.", ())
    if check_type == "DUMMY_VALUE":
        values = ", ".join(rule_sql.dummy_values(expected))
        return (f"더미 값({values})이 아닌 값의 비율((진단 건수 − 더미 값 건수) ÷ 진단 건수)이 임계값 {threshold:g}% 이상이면 "
                "통과합니다. 숫자 컬럼은 숫자 더미 값만 비교합니다. NULL 은 진단하지 않습니다.",
                ("진단 건수 (NULL 제외)", "오류 건수 — 더미 값"))
    if check_type == "CONTINUITY":
        return ("가장 이른 기간부터 가장 늦은 기간까지의 기간 수가 진단 건수, 값이 하나도 없는 기간 수가 오류 건수입니다. "
                f"있는 기간의 비율이 임계값 {threshold:g}% 이상이면 통과합니다(100 이면 빠진 기간이 없어야 합니다). "
                "주(WEEK)는 월요일에 시작하는 ISO 주입니다.",
                ("값이 있는 기간 수", "가장 이른 기간", "가장 늦은 기간"))
    if check_type == "DIVERSITY":
        try:
            limit = f"{rule_sql.diversity_limit(expected):g}"
        except rule_sql.ExpressionError:
            limit = str(expected)
        return (f"값마다 행 수를 세어, 가장 많은 값의 행 수 ÷ 진단 건수(NULL 제외)가 {limit}% 이하이면 통과합니다. "
                "상한을 넘친 최빈값 행 수가 오류 건수입니다. 결과 순서: 진단 건수, 최빈값 건수, 가장 적은 값의 건수, 고유값 수.", ())
    if check_type == "COMPOSITE_UNIQUE":
        return (f"키 컬럼 묶음이 겹치지 않는 행의 비율이 임계값 {threshold:g}% 이상이면 통과합니다(100 이면 겹치는 키가 없어야 "
                "합니다). 겹치는 키를 가진 행은 모두 오류 건수로 셉니다. 키 컬럼 가운데 하나라도 NULL 인 행은 진단하지 않습니다. "
                "결과 순서: 진단 건수, 키가 겹치는 행 수.", ())
    return ("오류 건수가 0 이면 통과합니다. NULL 은 진단하지 않습니다.", ("진단 건수 (NULL 제외)", "오류 건수"))


def _format_scan(table: TableRef | str, compiled: Compiled, meanings: tuple[str, ...]) -> str:
    """스캔 쿼리를 읽기 좋게 — 식마다 한 줄, 뒤에 그 식이 뜻하는 것. 내용은 ``rule_sql.scan_sql`` 과 같다.

    ``table`` 이 문자열이면 그대로 FROM 에 쓴다 — 원천 직접 경로의 원천 집계(``rule_direct.aggregate_sql``)."""
    exprs = [("count(*)", "전체 행 수")]
    exprs += [(e, meanings[n] if n < len(meanings) else "") for n, e in enumerate(compiled.exprs)]
    lines = []
    for n, (expr, meaning) in enumerate(exprs):
        comma = "," if n < len(exprs) - 1 else ""
        lines.append(f"  {expr}{comma}" + (f"  -- {meaning}" if meaning else ""))
    return "SELECT\n" + "\n".join(lines) + f"\nFROM {table if isinstance(table, str) else table.sql}"


# 원천 유형 → 화면에 보일 이름
_SOURCE_NAMES = {"postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "oracle": "Oracle",
                 "mssql": "SQL Server"}


async def _preview_direct(
    session: AsyncSession, req: QualitySqlPreviewRequest, dataset: Dataset, target: runs.EngineTarget,
    types: dict[str, str] | None, column: str,
) -> QualitySqlPreview:
    """원천 직접 경로(Trino 연결 없음)의 진단 쿼리 — 원천 DB 방언으로, 진단과 같은 계획 코드(``execution.compile_snapshot``)로
    만든다(DEF-152). 원천이 집계로 세는 규칙은 집계 쿼리, 행을 읽어 품질 워커가 판정하는 규칙은 읽기 쿼리를 보여 준다."""
    from app.quality import execution

    check = req.check_type
    source_type = target.source_type or ""
    source_name = _SOURCE_NAMES.get(source_type, source_type)
    preview = QualitySqlPreview(table=target.table.display, actual_table=True, engine=runs.DIRECT_ENGINE,
                                source_type=source_type)
    if check not in rule_direct.DIRECT_CHECK_TYPES:
        preview.note = ("이 데이터셋은 카탈로그에 Trino 연결이 없어 품질 워커가 원천 DB 를 직접 읽어 진단합니다. "
                        "이 유형은 Trino 에서만 평가할 수 있어(Trino 에 등록한 함수) 이 데이터셋의 진단에서는 건너뜁니다.")
        return preview
    reference = None
    if check == "REFERENTIAL":
        resolved = await execution._resolve_reference(
            session, req.expected_value, target.datasource_id, runs.DIRECT_ENGINE, source_type,
            execution._source_database(source_type, dataset.qualified_name))
        reference = resolved if isinstance(resolved, str) else {
            "table": execution._table_json(resolved[0]), "column": resolved[1]}
    plan = execution.compile_snapshot({
        "engine": runs.DIRECT_ENGINE, "table": execution._table_json(target.table), "types": types or {},
        "source_type": source_type,
        "rules": [{"id": 0, "check_type": check, "column_name": column or None, "expected_value": req.expected_value,
                   "threshold": req.threshold, "reference": reference}],
    })
    judgement, meanings = _judgement(check, req.expected_value, req.threshold)
    preview.judgement = judgement
    table = (target.table.schema, target.table.table)
    notes = []
    if 0 in plan.outcomes:
        notes.append(f"쿼리를 만들 수 없습니다 — {plan.outcomes[0].detail}")
        preview.judgement = ""
    elif plan.aggregate:
        preview.sql = _format_scan(rule_direct.source_table(source_type, *table), plan.aggregate[0], meanings)
        preview.mode = "AGGREGATE"
    elif plan.separate:
        preview.sql = rule_sql.format_own(plan.separate[0].own_sql or "")
        preview.mode = "SEPARATE"
    elif plan.scan:
        columns = rule_direct.columns_of([plan.probes[0]])
        preview.sql = rule_direct.select_sql(source_type, *table, columns)
        preview.mode = "READ"
        notes.append(f"이 규칙은 {source_name} 에서 집계로 세지 않고(DB 마다 함수 · 문자열 비교가 달라서) 품질 워커가 위 쿼리로 "
                     "행을 읽어 판정합니다. 행이 많으면 워커의 행 수 상한(direct_max_rows)까지만 읽습니다.")
    else:
        notes.append("이 유형은 진단 쿼리를 미리 보여 주지 않습니다 — 정밀 진단에서 평가합니다.")
    if check == "DATE_FORMAT" and plan.aggregate and plan.aggregate[0].exprs[-1:] == ("0",):
        notes.append("날짜·시각 타입 컬럼이라 값의 유효성은 DBMS 가 보장합니다 — 진단 건수만 셉니다.")
    preview.note = " ".join(notes) or None
    return preview


async def preview_sql(session: AsyncSession, req: QualitySqlPreviewRequest) -> QualitySqlPreview:
    """이 유형·기준값이 실행할 진단 쿼리. 데이터셋을 주면 그 테이블 이름과 컬럼 타입으로, 없으면 예시 이름으로 만든다.

    진단과 같은 ``rule_sql.compile_rule`` 을 쓴다. 실제 진단은 같은 테이블의 규칙들을 한 쿼리(스캔)로 묶어 실행한다.
    데이터셋이 원천 직접 경로(Trino 연결 없음)면 원천 DB 방언의 쿼리를 보여 준다(``_preview_direct``).
    """
    check = req.check_type
    if check not in rule_sql.TRINO_CHECK_TYPES:
        return QualitySqlPreview(note="진단 쿼리를 미리 보여 주지 않는 유형입니다 — 정밀 진단에서 평가합니다.")
    if rule_sql.code_group_ref(req.expected_value) is not None:
        # 표준 코드 그룹 연결 — 진단처럼 지금의 코드 값으로 풀어 보여 준다
        from app.quality import execution

        req = req.model_copy(update={
            "expected_value": await execution.resolve_code_group(session, check, req.expected_value)})
    table, types, actual, notes = _SAMPLE_TABLE, None, False, []
    column = (req.column_name or "").strip()
    reference = None
    engine_datasource_id = None
    if req.dataset_id is not None:
        dataset = await session.get(Dataset, req.dataset_id)
        if dataset is None:
            raise DefinitionNotFoundError(f"데이터셋을 찾을 수 없습니다: {req.dataset_id}")
        fields = (await session.execute(
            select(DatasetSchema).where(DatasetSchema.dataset_id == dataset.id))).scalars().all()
        types = {f.field_path: (f.native_type or f.field_type or "") for f in fields} or None
        try:
            # 진단과 같은 경로 해석 — Trino 연결이 없고 원천을 직접 읽을 수 있으면 원천 직접 경로(DEF-152)
            target = await runs.resolve_engine_target(session, dataset, connection=False)
            if target.engine == runs.DIRECT_ENGINE:
                return await _preview_direct(session, req, dataset, target, types, column)
            table, actual, engine_datasource_id = target.table, True, target.datasource_id
        except runs.EngineUnavailableError as e:
            parts = [p for p in (dataset.qualified_name or "").split(".") if p]
            if len(parts) >= 2:
                table = TableRef(_SAMPLE_TABLE.catalog, parts[-2], parts[-1])
            notes.append(f"Trino 의 테이블 이름을 알 수 없어 카탈로그 이름은 예시입니다 — {e}")
    else:
        notes.append("테이블·컬럼 이름은 예시입니다. 할당된 컬럼을 고르면 실제 이름으로 보여 줍니다.")
    if not column and check not in rule_sql.TABLE_LEVEL_TYPES and req.dataset_id is None:
        column = _SAMPLE_COLUMN
    if check == "REFERENTIAL":
        if engine_datasource_id is not None:
            from app.quality import execution

            resolved = await execution._resolve_reference(session, req.expected_value, engine_datasource_id)
            reference = resolved
        else:
            parsed = rule_sql.parse_reference(req.expected_value)
            if parsed:
                reference = (TableRef(_SAMPLE_TABLE.catalog, _SAMPLE_TABLE.schema, "reference_table"), parsed[1])

    spec = RuleSpec(id=0, check_type=check, column_name=column or None, expected_value=req.expected_value,
                    threshold=req.threshold)
    compiled = rule_sql.compile_rule(spec, table, types, reference)
    judgement, meanings = _judgement(check, req.expected_value, req.threshold)
    preview = QualitySqlPreview(judgement=judgement, table=table.display, actual_table=actual,
                                engine=runs.TRINO_ENGINE)
    if isinstance(compiled, Outcome):
        notes.insert(0, f"쿼리를 만들 수 없습니다 — {compiled.detail}")
        preview.judgement = ""
    elif compiled is not None:
        preview.sql = rule_sql.format_own(compiled.own_sql) if compiled.own_sql else _format_scan(table, compiled, meanings)
        if check == "DATE_FORMAT" and compiled.exprs and compiled.exprs[-1] == "0":
            notes.append("날짜·시각 타입 컬럼이라 값의 유효성은 DBMS 가 보장합니다 — 진단 건수만 셉니다.")
    preview.note = " ".join(notes) or None
    return preview
