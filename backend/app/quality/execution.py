# SPDX-License-Identifier: Apache-2.0
"""품질 진단 실행 — 카탈로그 쪽 절반.

진단 쿼리는 카탈로그가 실행하지 않는다. 별도 서비스인 품질 워커(``extensions/argus-catalog-quality-worker``)가
워커용 API(``/quality/worker/*``)로 실행을 가져가 Trino 에 쿼리를 내고 결과 행을 돌려준다. 이 모듈은 그 API 의
뒤에서 다음을 한다.

  1. ``claim``    대기 실행을 하나 잡아 **실행 계획**으로 바꾼다 — 규칙을 SQL 로 바꾸고(``rule_sql``) 같은 테이블의
                  규칙은 스캔 쿼리 하나로 묶는다. 계획의 바탕(규칙·컬럼 타입·참조 대상)은 실행 행에 남긴다.
  2. ``report``   워커의 진행 보고를 실행 행에 적고 취소 요청 여부를 돌려준다.
  3. ``complete`` 워커가 돌려준 결과 행을 남겨 둔 계획으로 판정해 저장하고 점수를 다시 계산한다
                  (``service.import_results`` — 이번에 평가하지 않은 활성 규칙은 직전 결과를 이어받는다).
  4. ``finish``   결과 없이 끝낸다(실패·취소) 또는 대기열로 되돌린다(워커 종료).

규칙의 뜻(SQL 변환·판정)이 카탈로그 한곳에만 있으므로 워커는 규칙 유형이 늘어도 바뀌지 않는다. 계획을 실행 행에
남기는 이유는 실행 도중 규칙이 수정돼도 낸 쿼리와 같은 기준으로 판정하기 위해서다.

경로는 둘이다(``runs.resolve_engine_target``). Trino 연결이 있으면 ``TRINO`` — 위와 같다. 없으면 ``DIRECT`` — 워커가
원천 DB 에 직접 쿼리를 낸다. 집계의 뜻이 DB 마다 같은 규칙(NULL · 고유값 · 범위 · 허용 값 · 컬럼 비교 · 행 수)은
원천이 세는 집계 쿼리 하나(``rule_direct.aggregate`` — 결과 한 행)로, 값의 모양을 보는 규칙(정규식 · 날짜 형식 · 글자 깨짐 ·
공백)과 조건 · 계산식은 원천을 읽어 규칙마다 명세(``rule_direct.probe``)를 Python 으로 계산한다(TODO-097). 집계 식과
명세의 값은 Trino 집계 식과 같은 모양이라 판정은 두 경로가 같은 ``Compiled.judge`` 를 쓴다(TODO-095).

프로파일(컬럼 통계 수집)도 같은 대기열 · 같은 경로로 실행한다(``kind = PROFILE`` — 계획과 결과 읽기는 ``profiling``).
규칙 진단이 끝나면 전체 행 수로 데이터셋의 행 수를 갱신한다(원천 직접 경로에서 행 수 상한에 걸린 실행은 제외).

위반 샘플: 실패한 규칙의 위반 행 몇 개(``rule_sql.SAMPLE_ROWS``)를 결과에 남긴다. 판정은 결과가 와야 하므로 Trino 경로는
``complete`` 의 응답에 실패한 규칙의 샘플 쿼리(``samples``)를 실어 주고, 워커가 이어서 내 ``attach_samples`` 로 보낸다(실행은 이미
끝난 상태 — 샘플은 덤이라 실패해도 실행 결과는 그대로다). 원천 직접 경로는 읽는 동안 위반 행을 바로 모아 결과와 함께 보낸다
(참조관계만 Trino 경로처럼 이어서 낸다).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, DatasetSchema
from app.core.config import settings
from app.quality import indicators as quality_indicators
from app.quality import profiling, rule_direct, rule_sql, runs, service, trino_client
from app.quality.models import DataProfile, QualityDefinition, QualityRule, QualityRun
from app.quality.rule_sql import Compiled, Outcome, RuleSpec, TableRef
from app.quality.schemas import ResultImportItem, ResultsImportRequest

logger = logging.getLogger(__name__)

SCAN_KEY = "scan"
# Trino 경로 실행 안의 원천 직접 읽기(커스텀 Python) — 한 실행이 두 경로를 함께 쓸 때
PYTHON_KEY = "python"
# 원천 직접 — 원천이 세는 집계 쿼리(결과 한 행)
AGGREGATE_KEY = "aggregate"
# 직접 진단에서 워커가 함께 돌려주는 읽기 요약(읽은 행 수·상한에 걸렸는지)
DIRECT_META_KEY = "direct"
# 한 번의 claim 에서 계획을 세우지 못한 실행을 건너뛰며 다음 실행을 찾는 횟수 상한
_CLAIM_ATTEMPTS = 20
# 위반 샘플 — 한 실행에서 샘플 쿼리를 낼 실패 규칙 수, 샘플 한 행에 남길 컬럼 수 · 값 길이
_SAMPLE_RULES = 20
_SAMPLE_COLUMNS = 30
_SAMPLE_VALUE_CHARS = 200


def rule_key(rule_id: int) -> str:
    return f"rule:{rule_id}"


@dataclass
class _Plan:
    table: TableRef
    scan: list[Compiled] = field(default_factory=list)      # 스캔 쿼리 하나로 묶을 규칙
    separate: list[Compiled] = field(default_factory=list)  # 쿼리를 따로 낼 규칙
    outcomes: dict[int, Outcome] = field(default_factory=dict)  # 쿼리 없이 판정이 끝난 규칙
    engine: str = runs.TRINO_ENGINE
    source_type: str | None = None                          # DIRECT — 원천 유형
    probes: dict[int, dict] = field(default_factory=dict)   # DIRECT — 규칙별 명세
    aggregate: list[Compiled] = field(default_factory=list)  # DIRECT — 원천 집계 쿼리 하나로 셀 규칙(식은 원천 방언)
    types: dict[str, str] = field(default_factory=dict)     # 컬럼 → 타입
    skipped: list[str] = field(default_factory=list)        # 이 경로로 평가하지 못해 건너뛴 규칙
    python: list[Compiled] = field(default_factory=list)    # 커스텀 Python(판정은 플러그인이 한다) — 늘 원천을 직접 읽는다
    python_cfg: dict[int, dict] = field(default_factory=dict)  # 규칙별 플러그인 설정
    # TRINO 실행이 커스텀 Python 을 위해 원천을 직접 읽을 때의 원천 유형 · 대상(DIRECT 실행은 table · source_type 그대로)
    python_source_type: str | None = None
    python_table: TableRef | None = None

    @property
    def direct(self) -> bool:
        return self.engine == runs.DIRECT_ENGINE

    @property
    def mixed(self) -> bool:
        """Trino 실행이 커스텀 Python 을 원천 직접으로 함께 평가한다."""
        return not self.direct and bool(self.python)


# ---------------------------------------------------------------------------
# 계획
# ---------------------------------------------------------------------------

async def _resolve_reference(
    session: AsyncSession, expected: str | None, engine_datasource_id: int, engine: str = runs.TRINO_ENGINE,
    source_type: str | None = None, database: str | None = None,
):
    """REFERENTIAL 기대값 → (참조 테이블, 컬럼) 또는 해석하지 못한 사유.

    Trino 경로는 참조 데이터셋도 같은 Trino 데이터 소스로 읽을 수 있어야 하고, 원천 직접 경로는 참조 데이터셋이
    **같은 원본 데이터 소스**에 있어야 한다(원본에 조인 쿼리 하나를 낸다).
    """
    parsed = rule_sql.parse_reference(expected)
    if parsed is None:
        return "expected_value 는 '<참조 데이터셋 URN 또는 ID>#<컬럼>' 형식이어야 합니다"
    ref, column = parsed
    # ID 는 ASCII 숫자 9자리까지만 — '²' 같은 숫자 문자나 정수 범위를 넘는 값은 URN 으로 찾아 "찾을 수 없음" 이 된다
    if ref.isascii() and ref.isdigit() and len(ref) <= 9:
        parent = await session.get(Dataset, int(ref))
    else:
        parent = (await session.execute(select(Dataset).where(Dataset.urn == ref))).scalar_one_or_none()
    if parent is None:
        return f"참조 데이터셋을 찾을 수 없습니다: {ref}"
    if engine == runs.DIRECT_ENGINE:
        if parent.datasource_id != engine_datasource_id:
            return "원천 직접 진단의 참조관계는 참조 데이터셋이 같은 원본 데이터 소스에 있어야 합니다"
        parts = [p for p in (parent.qualified_name or "").split(".") if p]
        if len(parts) < 2:
            return "참조 데이터셋 이름에서 스키마·테이블을 알 수 없습니다"
        if source_type == "mssql" and _source_database("mssql", parent.qualified_name) != database:
            return "SQL Server 의 참조관계는 참조 데이터셋이 같은 데이터베이스에 있어야 합니다"
        return TableRef(parts[0] if len(parts) > 2 else "", parts[-2], parts[-1]), column
    try:
        target = await runs.resolve_engine_target(session, parent, direct=False, connection=False)
    except runs.EngineUnavailableError as e:
        return f"참조 데이터셋을 Trino 에서 찾을 수 없습니다: {e}"
    if target.datasource_id != engine_datasource_id:
        return "참조 데이터셋이 다른 Trino 데이터 소스에 연결돼 있습니다"
    return target.table, column


async def resolve_code_group(session: AsyncSession, check_type: str | None, expected: str | None) -> str | None:
    """허용 값(ACCEPTED_VALUES)이 표준 코드 그룹을 가리키면 그 그룹의 사용 중인 코드 값 목록(JSON 배열 문자열)으로 푼다.

    진단을 요청할 때 풀어 실행에 남긴다 — 코드 값을 나중에 고쳐도 이 실행은 그때의 값으로 판정한다. 연결이 아니면 그대로,
    그룹이 없거나 사용 중인 코드 값이 없으면 연결 그대로 둔다(``rule_sql.compile_rule`` 이 규칙 오류로 판정한다).
    """
    group_id = rule_sql.code_group_ref(expected) if check_type == "ACCEPTED_VALUES" else None
    if group_id is None:
        return expected
    from app.core.text_list import encode_list
    from app.standard.models import CodeValue

    values = (await session.execute(
        select(CodeValue.code_value).where(CodeValue.code_group_id == group_id, CodeValue.is_active != "false")
        .order_by(CodeValue.sort_order, CodeValue.code_value)
    )).scalars().all()
    return encode_list(values) or expected


def _table_json(table: TableRef) -> list[str]:
    return [table.catalog, table.schema, table.table]


_DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306, "mariadb": 3306, "oracle": 1521, "mssql": 1433}


def _direct_source(source_type: str, config: dict, database: str | None = None) -> dict:
    """원천 접속 설정 → 워커가 원천에 접속할 정보(비밀번호 포함). 형식이 틀리면 ``ValueError``.

    ``database`` 는 SQL Server 에서 데이터셋이 있는 데이터베이스(qualified_name 의 ``<db>.<schema>.<table>``) — 그 DB 에
    접속해 ``[schema].[table]`` 로 읽는다. Oracle 은 Service Name 이 있으면 그것, 없으면 SID 로 접속한다.
    """
    from app.catalog.service import connection_ssl, validate_connection_target

    pg = source_type == "postgresql"
    host, port = validate_connection_target(config, _DEFAULT_PORTS.get(source_type, 3306))
    info = {
        "type": source_type, "host": host, "port": port,
        "database": str(config.get("database") or ("postgres" if pg else "")),
        "user": str(config.get("username") or ""), "password": str(config.get("password") or ""),
        "ssl": connection_ssl(config, pg=True) is not None,
    }
    if source_type == "oracle":
        service, sid = str(config.get("service_name") or "").strip(), str(config.get("sid") or "").strip()
        if not service and not sid:
            raise ValueError("Oracle 접속 설정에 Service Name 또는 SID 가 없습니다.")
        info.update({"database": "", "service_name": service, "sid": "" if service else sid})
    elif source_type == "mssql":
        info["database"] = database or info["database"] or "master"
    return info


def _source_database(source_type: str | None, qualified_name: str | None) -> str | None:
    """SQL Server 데이터셋의 데이터베이스 — qualified_name 의 끝 세 토막 가운데 첫째. 다른 유형은 None."""
    parts = [p for p in (qualified_name or "").split(".") if p]
    return parts[-3] if source_type == "mssql" and len(parts) >= 3 else None


async def _source_connection(session: AsyncSession, datasource_id: int, name: str, what: str, build):
    """데이터 소스의 저장된 접속 설정 → ``build(config)``. 없거나 틀리면 ``EngineUnavailableError``."""
    stored = await runs.stored_connection_config(session, datasource_id, name, what)
    from app.catalog.datasource_secrets import DatasourceSecretError, unseal_config

    try:
        return build(unseal_config(stored))
    except (ValueError, DatasourceSecretError) as e:
        raise runs.EngineUnavailableError(f"{what} 접속 설정 오류: {e}") from e


async def _python_source(session: AsyncSession, dataset: Dataset, target: runs.EngineTarget) -> tuple[dict, dict]:
    """Trino 실행에서 커스텀 Python 을 위해 원천을 직접 읽을 정보 — (계획에 남길 대상, 접속 정보)."""
    from app.catalog.models import Datasource

    source = await session.get(Datasource, target.python_datasource_id)
    if source is None:
        raise runs.EngineUnavailableError("원본 데이터 소스가 없습니다.")
    connection = await _source_connection(
        session, source.id, source.name, "원천",
        lambda config: _direct_source(target.python_source_type or "", config,
                                      _source_database(target.python_source_type, dataset.qualified_name)))
    table = runs.direct_table(dataset, source)
    return {"source_type": target.python_source_type, "table": _table_json(table)}, connection


async def _snapshot(session: AsyncSession, run: QualityRun) -> tuple[dict, trino_client.TrinoEndpoint | dict, dict | None]:
    """실행 행 → (계획의 바탕, 접속 정보, 커스텀 Python 용 원천 접속 정보). 대상·접속 설정을 찾지 못하면 ``EngineUnavailableError``.

    접속 정보는 Trino 경로면 ``TrinoEndpoint``, 직접 경로면 원천 접속 정보(``_direct_source``)다. Trino 경로에서 커스텀 Python
    규칙을 원천 직접으로 함께 평가하면 셋째 값이 그 원천의 접속 정보다(아니면 None).
    바탕은 JSON 으로 실행 행에 남긴다. 비밀번호는 넣지 않는다.
    """
    dataset = await session.get(Dataset, run.dataset_id)
    if dataset is None:
        raise runs.EngineUnavailableError("데이터셋이 삭제되었습니다.")
    # 수동 데이터셋(TODO-CAT-001)은 원천 매핑의 데이터 소스 · 경로로 읽는다
    dataset = await runs.source_view(session, dataset)
    target = await runs.resolve_engine_target(session, dataset)
    direct = target.engine == runs.DIRECT_ENGINE
    connection = await _source_connection(
        session, target.datasource_id, target.datasource_name, "원천" if direct else "Trino",
        (lambda config: _direct_source(target.source_type or "", config,
                                       _source_database(target.source_type, dataset.qualified_name))) if direct
        else trino_client.TrinoEndpoint.from_config)

    fields = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset.id)
        .order_by(DatasetSchema.ordinal, DatasetSchema.id)
    )).scalars().all()
    if run.kind == runs.PROFILE_KIND:
        run.engine = target.engine
        run.engine_datasource_id = target.datasource_id
        run.engine_table = target.table.display
        snapshot = {
            "kind": runs.PROFILE_KIND, "engine": target.engine, "table": _table_json(target.table),
            "columns": [[f.field_path, f.field_type or "", f.native_type or ""] for f in fields],
        }
        if direct:
            snapshot["source_type"] = target.source_type
        return snapshot, connection, None
    rules = (await session.execute(
        select(QualityRule).where(QualityRule.dataset_id == dataset.id, QualityRule.is_active == "true")
        .order_by(QualityRule.created_at, QualityRule.id)
    )).scalars().all()
    run.engine = target.engine
    run.engine_datasource_id = target.datasource_id
    run.engine_table = target.table.display

    # 실행이 품질 지표를 골랐으면 그 지표의 규칙만 낸다 — 나머지는 직전 결과를 이어받는다
    wanted = quality_indicators.load(run.indicators)
    wanted_definitions = runs.load_definition_ids(run.definition_ids)
    # 분모는 이 실행이 진단하는 규칙만 — 고르지 않은 지표 · 직접 만든 규칙은 넣지 않는다(DEF-149, create_run 과 같은 기준)
    run.rules_total = sum(1 for r in rules if runs.in_scope(
        wanted, wanted_definitions, r.check_type, r.domain, r.column_name, r.definition_id))
    used = {r.definition_id for r in rules if r.definition_id is not None}
    definition_names: dict[int, str] = dict((await session.execute(
        select(QualityDefinition.id, QualityDefinition.name).where(QualityDefinition.id.in_(used)))).all()) if used else {}
    items = []
    skipped = []
    python_source: dict | None = None
    python_connection: dict | None = None
    python_error: str | None = None
    check_types = target.check_types
    for rule in rules:
        if rule.check_type not in rule_direct.ENGINE_CHECK_TYPES:
            continue  # 정밀 진단이 모르는 유형(예전 데이터) — 직전 결과를 이어받는다
        if not quality_indicators.wants(wanted, rule.check_type, rule.domain, rule.column_name):
            continue
        if not runs.wants_definition(wanted_definitions, rule.definition_id):
            continue
        if rule.check_type == "CUSTOM_PYTHON" and not direct and "CUSTOM_PYTHON" in check_types \
                and python_source is None and python_error is None:
            # Trino 실행 안에서 커스텀 Python 만 원천을 직접 읽는다 — 원천 접속 설정이 없으면 그 규칙만 건너뛴다
            try:
                python_source, python_connection = await _python_source(session, dataset, target)
            except runs.EngineUnavailableError as e:
                python_error = str(e)
                check_types = runs.TRINO_PATH_TYPES
        if rule.check_type not in check_types:
            # 이 경로로 평가하지 못하는 유형(원천 직접: Trino 함수·커스텀 SQL, Trino: 커스텀 Python) — 직전 결과를 이어받고 실행 메시지로 알린다
            skipped.append(rule.rule_name or f"{rule.check_type}({rule.column_name or ''})")
            continue
        reference = None
        if rule.check_type == "REFERENTIAL":
            resolved = await _resolve_reference(
                session, rule.expected_value, target.datasource_id, target.engine, target.source_type,
                _source_database(target.source_type, dataset.qualified_name))
            reference = resolved if isinstance(resolved, str) else {
                "table": _table_json(resolved[0]), "column": resolved[1]}
        items.append({
            "id": rule.id, "check_type": rule.check_type, "column_name": rule.column_name,
            # 표준 코드 그룹을 가리키는 허용 값은 지금의 코드 값으로 풀어 남긴다
            "expected_value": await resolve_code_group(session, rule.check_type, rule.expected_value),
            "threshold": float(rule.threshold) if rule.threshold is not None else 100.0,
            "reference": reference,
            # 리포트용 — 규칙이나 지표가 나중에 바뀌어도 이 실행은 당시의 분류로 집계한다
            "rule_name": rule.rule_name, "domain": rule.domain, "definition_id": rule.definition_id,
            "definition_name": definition_names.get(rule.definition_id),
        })
    snapshot = {
        "engine": target.engine,
        "table": _table_json(target.table),
        "types": {f.field_path: (f.native_type or f.field_type or "") for f in fields},
        "rules": items,
    }
    if direct:
        snapshot["source_type"] = target.source_type
    if python_source is not None:
        snapshot["python_source"] = python_source
    if python_error:
        snapshot["python_error"] = python_error
    if any(i["check_type"] == "FRESHNESS" for i in items):
        # 최신성의 시간대 없는 시각 값을 해석할 원천 데이터 시간대 — 판정도 이 값으로(설정을 나중에 바꿔도 이 실행은 그대로)
        snapshot["source_timezone"] = await service.get_source_timezone_name(session)
    if skipped:
        snapshot["skipped"] = skipped
    return snapshot, connection, python_connection


def compile_snapshot(snapshot: dict) -> _Plan:
    """계획의 바탕 → 컴파일된 계획. 같은 바탕은 언제나 같은 SQL·판정이 된다."""
    table = TableRef(*snapshot["table"])
    types = snapshot.get("types") or {}
    plan = _Plan(table=table, engine=snapshot.get("engine") or runs.TRINO_ENGINE, types=dict(types),
                 source_type=snapshot.get("source_type"), skipped=list(snapshot.get("skipped") or []))
    python_source = snapshot.get("python_source")
    if plan.direct:
        plan.python_source_type, plan.python_table = plan.source_type, table
    elif isinstance(python_source, dict):
        plan.python_source_type = python_source.get("source_type")
        plan.python_table = TableRef(*python_source["table"])
    scope = rule_sql.DIRECT_SCOPE if plan.direct else rule_sql.TRINO_SCOPE
    for item in snapshot.get("rules") or []:
        spec = RuleSpec(
            id=item["id"], check_type=item["check_type"], column_name=item.get("column_name"),
            expected_value=item.get("expected_value"), threshold=float(item.get("threshold", 100.0)),
        )
        reference = item.get("reference")
        if isinstance(reference, dict):
            reference = (TableRef(*reference["table"]), reference["column"])
        if spec.check_type == "CUSTOM_PYTHON":
            if plan.python_table is not None:
                _plan_python(plan, spec)
            continue
        compiled = rule_sql.compile_rule(spec, table, types, reference, scope=scope,
                                         source_timezone=snapshot.get("source_timezone"))
        if compiled is None:
            continue
        if isinstance(compiled, Outcome):
            plan.outcomes[spec.id] = compiled
        elif plan.direct and spec.check_type == "REFERENTIAL":
            # 같은 원본 안의 조인 — 원본의 SQL 방언으로 바꿔 따로 낸다. 판정은 Trino 경로와 같다
            parent, parent_column = reference
            sql = rule_direct.referential_sql(
                plan.source_type or "", (table.schema, table.table), spec.column_name or "",
                (parent.schema, parent.table), parent_column)
            sample = rule_direct.referential_sample_sql(
                plan.source_type or "", (table.schema, table.table), spec.column_name or "",
                (parent.schema, parent.table), parent_column, rule_sql.SAMPLE_ROWS)
            plan.separate.append(replace(compiled, own_sql=sql, sample_sql=sample))
        elif plan.direct and spec.check_type in ("DIVERSITY", "COMPOSITE_UNIQUE"):
            # 값(키) 묶음별 집계 — 원본의 SQL 방언으로 바꿔 따로 낸다. 판정은 Trino 경로와 같다
            source, own = plan.source_type or "", (table.schema, table.table)
            try:
                if spec.check_type == "DIVERSITY":
                    sql, sample = rule_direct.diversity_sql(source, own, spec.column_name or "", types), None
                else:
                    sql, sample = rule_direct.composite_unique_sql(source, own, spec.expected_value, types,
                                                                   rule_sql.SAMPLE_ROWS)
            except (rule_sql.ExpressionError, ValueError) as e:
                plan.outcomes[spec.id] = Outcome(False, "N/A", str(e))
                continue
            plan.separate.append(replace(compiled, own_sql=sql, sample_sql=sample))
        elif plan.direct:
            try:
                agg = rule_direct.aggregate(spec, types, plan.source_type or "", (table.schema, table.table),
                                            rule_sql.SAMPLE_ROWS)
            except (rule_sql.ExpressionError, ValueError) as e:
                plan.outcomes[spec.id] = Outcome(False, "N/A", str(e))
                continue
            if agg is not None:
                plan.aggregate.append(replace(compiled, exprs=agg.exprs, sample_sql=agg.sample_sql))
                continue
            try:
                probe = rule_direct.probe(spec, types)
            except (rule_sql.ExpressionError, ValueError) as e:
                plan.outcomes[spec.id] = Outcome(False, "N/A", str(e))
                continue
            if probe is None:
                continue
            plan.scan.append(compiled)
            plan.probes[spec.id] = probe
        elif compiled.own_sql:
            plan.separate.append(compiled)
        else:
            plan.scan.append(compiled)
    return plan


def _plan_python(plan: _Plan, spec: RuleSpec) -> None:
    try:
        cfg = rule_direct.custom_python(spec.expected_value)
    except rule_sql.ExpressionError as e:
        plan.outcomes[spec.id] = Outcome(False, "N/A", str(e))
        return
    label = f"CUSTOM_PYTHON({cfg['module']}.{cfg['fn']})"
    plan.python.append(Compiled(spec.id, (), _python_judge, label=label))
    plan.python_cfg[spec.id] = cfg


def _python_judge(_total: int, _values: list) -> Outcome:  # pragma: no cover — 플러그인이 판정한다(_judge 가 직접 읽음)
    raise AssertionError("커스텀 Python 은 워커가 돌려준 판정을 그대로 쓴다")


def plan_steps(plan: _Plan) -> list[dict]:
    """워커가 실행할 단계 목록. 스캔 단계의 ``fallback`` 은 스캔이 실패했을 때 대신 낼 규칙별 쿼리다.

    따로 내는 쿼리(참조관계·커스텀 SQL)의 ``catalog``·``schema`` 는 Trino 세션의 기본값 — 커스텀 SQL 에 테이블 이름만
    적어도(``FROM rental``) 이 데이터셋의 Trino 카탈로그·스키마에서 찾는다.
    """
    steps: list[dict] = []
    display = plan.table.display
    if plan.direct:
        if plan.aggregate:
            steps.append(_aggregate_step(plan))
        if plan.scan or plan.python:
            steps.append(_direct_step(plan))
        steps.extend({"key": rule_key(c.rule_id), "label": f"{c.label} — {display}", "sql": c.own_sql, "fallback": []}
                     for c in plan.separate)
        return steps
    if plan.scan:
        steps.append({
            "key": SCAN_KEY,
            "label": f"스캔 {display} (규칙 {len(plan.scan)}개)",
            "sql": rule_sql.scan_sql(plan.table, plan.scan),
            # 규칙이 하나뿐이면 같은 쿼리를 다시 낼 뿐이라 두지 않는다
            "fallback": [
                {"key": rule_key(c.rule_id), "label": f"{c.label} — {display}",
                 "sql": rule_sql.scan_sql(plan.table, [c])}
                for c in plan.scan
            ] if len(plan.scan) > 1 else [],
        })
    for c in plan.separate:
        steps.append({"key": rule_key(c.rule_id), "label": f"{c.label} — {display}", "sql": c.own_sql, "fallback": [],
                      "catalog": plan.table.catalog, "schema": plan.table.schema})
    if plan.python:
        # 커스텀 Python 은 Trino 로 낼 수 없다 — 같은 실행 안에서 원천을 직접 읽는 단계(engine: DIRECT)
        steps.append({**_read_step(plan, [], key=PYTHON_KEY), "engine": runs.DIRECT_ENGINE})
    return steps


def _aggregate_step(plan: _Plan) -> dict:
    """원천 집계 단계 — 원천이 규칙들을 쿼리 하나로 세고 워커는 결과 한 행을 돌려준다. 실패하면 규칙별 쿼리를 다시 낸다."""
    source, table = plan.source_type or "", (plan.table.schema, plan.table.table)
    display = plan.table.display
    return {
        "key": AGGREGATE_KEY,
        "label": f"원천 집계 {display} (규칙 {len(plan.aggregate)}개)",
        "sql": rule_direct.aggregate_sql(source, table, [e for c in plan.aggregate for e in c.exprs]),
        "fallback": [
            {"key": rule_key(c.rule_id), "label": f"{c.label} — {display}",
             "sql": rule_direct.aggregate_sql(source, table, list(c.exprs))}
            for c in plan.aggregate
        ] if len(plan.aggregate) > 1 else [],
    }


def _direct_step(plan: _Plan) -> dict:
    """직접 진단 단계 — 워커는 ``sql`` 로 원천을 한 번 읽으며 ``probes`` 를 모두 계산한다(``_read_step``)."""
    return _read_step(plan, plan.scan, key=SCAN_KEY)


def _read_step(plan: _Plan, scan: list[Compiled], *, key: str) -> dict:
    """원천 읽기 단계 — 워커는 ``sql`` 로 원천을 한 번 읽으며 ``probes`` 를 모두 계산하고 커스텀 Python 을 부른다.

    ``columns`` 는 ``sql`` 이 읽는 컬럼의 순서(명세의 컬럼 이름 → 행의 자리), ``char_columns`` 는 그 가운데 CHAR(n)
    컬럼 — 워커가 값 뒤의 공백을 잘라 낸다. 결과는 규칙마다
    ``{key: "rule:<id>", row: [행 수, 값…]}`` 로 돌려준다 — Trino 경로의 규칙별 재실행 결과와 같은 모양이다.
    """
    # samples — 위반 행을 이만큼 모아 결과와 함께 돌려준다(이를 모르는 예전 워커는 무시한다)
    table = plan.python_table or plan.table
    source_type = plan.python_source_type or plan.source_type or ""
    probes = [{"key": rule_key(c.rule_id), "label": c.label, **plan.probes[c.rule_id], "samples": rule_sql.SAMPLE_ROWS}
              for c in scan]
    columns = rule_direct.columns_of(probes)
    python = [{"key": rule_key(c.rule_id), "label": c.label, **plan.python_cfg[c.rule_id]} for c in plan.python]
    if python:
        # 플러그인은 데이터프레임 전체를 받는다 — 스키마의 모든 컬럼을 읽는다
        columns += [c for c in plan.types if c not in columns]
    count = len(scan) + len(plan.python)
    return {
        "key": key,
        "label": f"원천 읽기 {table.display} (규칙 {count}개)",
        "sql": rule_direct.select_sql(source_type, table.schema, table.table, columns),
        "columns": columns,
        "char_columns": [c for c in columns if rule_direct.is_char(plan.types.get(c))],
        "probes": probes,
        "python": python,
        "fallback": [],
    }


# ---------------------------------------------------------------------------
# 워커용 API 의 뒤
# ---------------------------------------------------------------------------

async def claim(
    session: AsyncSession, worker_id: str, hostname: str | None, version: str | None, owner: str | None = None,
) -> dict | None:
    """대기 실행 하나를 잡아 워커에 줄 작업으로 돌려준다. 줄 것이 없으면 None.

    계획을 세우지 못한 실행(Trino 연결이 사라짐 등)은 실패로 끝내고, 낼 쿼리가 없는 실행은 그 자리에서 끝낸 뒤
    다음 대기 실행을 본다. 호출자가 커밋한다.
    """
    await runs.worker_heartbeat(session, worker_id, hostname, None, version, owner)
    for _ in range(_CLAIM_ATTEMPTS):
        run_id = await runs.claim_next(session, worker_id)
        if run_id is None:
            return None
        run = await session.get(QualityRun, run_id)
        try:
            snapshot, connection, python_connection = await _snapshot(session, run)
            profile = snapshot.get("kind") == runs.PROFILE_KIND
            if profile:
                table = TableRef(*snapshot["table"])
                engine = snapshot["engine"]
                steps = [_profile_step(snapshot)] if snapshot.get("columns") else []
            else:
                plan = compile_snapshot(snapshot)
                table, engine = plan.table, plan.engine
                steps = plan_steps(plan)
            run.plan_json = json.dumps(snapshot, ensure_ascii=False)
            run.progress_total = len(steps)
            run.progress_done = 0
            if not steps:
                if profile:
                    _end(run, "FAILED", "스키마(컬럼) 정보가 없어 프로파일을 수집하지 못했습니다.")
                else:
                    await _store(session, run, plan, {})  # 낸 쿼리가 없어 샘플도 없다
                await session.commit()
                continue
        except runs.EngineUnavailableError as e:
            _end(run, "FAILED", str(e))
            await session.commit()
            logger.warning("품질 진단 실행 계획 실패: run_id=%d %s", run_id, e)
            continue
        except Exception as e:  # noqa: BLE001 — 한 실행의 오류가 대기열 전체를 막지 않게 그 실행만 실패로 끝낸다
            await _fail_unplannable(session, run_id, e)
            continue
        await runs.worker_heartbeat(session, worker_id, hostname, run_id, version)
        logger.info("품질 %s 실행 시작: run_id=%d worker=%s table=%s 쿼리 %d개",
                    "프로파일" if profile else "진단", run_id, worker_id, table.display, len(steps))
        job = {"run_id": run_id, "dataset_id": run.dataset_id, "table": table.display, "engine": engine,
               "kind": snapshot.get("kind") or runs.CHECK_KIND, "steps": steps}
        if engine == runs.DIRECT_ENGINE:
            # 원천 접속 정보(비밀번호 포함) — 워커용 API 는 관리자 역할 API 키·워커 ID 묶음만 받는다
            job["direct"] = connection
        else:
            if python_connection is not None:
                # 커스텀 Python 단계(engine: DIRECT)가 원천을 직접 읽는다
                job["direct"] = python_connection
            job.update({
                "trino": {"base_url": connection.base_url, "user": connection.user, "password": connection.password},
                "source": settings.quality_trino_source,
                "client_tags": [settings.quality_trino_source],
                "session": {"query_max_execution_time": f"{settings.quality_trino_query_timeout_minutes}m"},
            })
        return job
    return None


def _profile_step(snapshot: dict) -> dict:
    return profiling.profile_step(
        TableRef(*snapshot["table"]), snapshot.get("columns") or [],
        direct=snapshot.get("engine") == runs.DIRECT_ENGINE, source_type=snapshot.get("source_type"))


async def _fail_unplannable(session: AsyncSession, run_id: int, error: Exception) -> None:
    """계획을 세우다 예상하지 못한 오류가 난 실행을 실패로 끝낸다.

    그대로 두면 롤백으로 대기 중으로 돌아가 다음 폴링에서도 맨 앞에서 같은 오류를 내고, 뒤의 실행이 모두 멈춘다.
    """
    logger.exception("품질 진단 실행 계획 중 오류: run_id=%d", run_id, exc_info=error)
    await session.rollback()
    run = await session.get(QualityRun, run_id)
    if run is not None and run.status in runs.ACTIVE_STATUSES:
        _end(run, "FAILED", f"실행 계획을 만들지 못했습니다({type(error).__name__}: {str(error)[:300]}). 서버 로그를 확인하세요.")
        await session.commit()


async def _owned(session: AsyncSession, run_id: int, worker_id: str) -> QualityRun:
    """이 워커가 실행 중인 실행 행(잠금). 아니면 ``RunConflictError`` — 워커는 그 실행을 그만둔다."""
    run = (await session.execute(
        select(QualityRun).where(QualityRun.id == run_id).with_for_update()
    )).scalar_one_or_none()
    if run is None or run.status != "RUNNING" or run.worker_id != worker_id:
        raise runs.RunConflictError("이 워커가 실행 중인 진단이 아닙니다.")
    return run


def run_queries(run: QualityRun) -> list[dict]:
    """이 실행이 Trino 에 낸 진단 쿼리 — 저장한 계획의 바탕으로 워커가 받은 것과 같은 SQL 을 다시 만든다.

    쿼리 ID 는 실행 순서로 짝짓는다(워커는 단계를 차례로 실행하고 ID 를 받은 순서대로 보고한다). 스캔이 실패해
    규칙별 쿼리를 다시 냈으면 그 쿼리들도 넣는다. 순서로 짝지을 수 없으면 ``query_id`` 를 비워 둔다.
    아직 워커가 가져가지 않아 계획이 없는 실행은 빈 목록.
    """
    try:
        snapshot = json.loads(run.plan_json or "")
        if snapshot.get("kind") == runs.PROFILE_KIND:
            # 프로파일 — 묶은 통계 쿼리와 최빈값 쿼리(고유값이 적은 컬럼만 실제로 낸다)
            return [{**q, "retry": False, "formatted_sql": q["sql"], "query_id": None}
                    for q in profiling.step_queries(_profile_step(snapshot))] if snapshot.get("columns") else []
        plan = compile_snapshot(snapshot)
    except (ValueError, KeyError, TypeError, AttributeError):
        return []
    steps = plan_steps(plan)
    if plan.direct:
        # 원천에 낸 읽기 쿼리 하나 — Trino 쿼리 ID 가 없다
        return [{"key": q["key"], "label": q["label"], "sql": q["sql"], "retry": False, "formatted_sql": q["sql"],
                 "query_id": None} for q in steps]
    # 화면용 — 식마다 줄을 바꾸고 규칙 이름을 주석으로 단다
    formatted = {SCAN_KEY: rule_sql.format_scan(plan.table, plan.scan)} if plan.scan else {}
    formatted.update({rule_key(c.rule_id): rule_sql.format_scan(plan.table, [c]) for c in plan.scan})
    formatted.update({rule_key(c.rule_id): rule_sql.format_own(c.own_sql) for c in plan.separate})
    try:
        ids = [str(q) for q in json.loads(run.query_ids or "[]")]
    except ValueError:
        ids = []
    executed = [{**step, "retry": False} for step in steps if step.get("engine") != runs.DIRECT_ENGINE]
    direct_reads = [{"key": q["key"], "label": q["label"], "sql": q["sql"], "retry": False, "formatted_sql": q["sql"],
                     "query_id": None} for q in steps if q.get("engine") == runs.DIRECT_ENGINE]
    if len(ids) > len(steps) and steps and steps[0]["fallback"]:
        executed[1:1] = [{**sub, "retry": True} for sub in steps[0]["fallback"]]
    if len(ids) > len(executed):
        ids = []
    return [
        {"key": q["key"], "label": q["label"], "sql": q["sql"], "retry": q["retry"],
         "formatted_sql": formatted.get(q["key"], q["sql"]),
         "query_id": ids[n] if n < len(ids) else None}
        for n, q in enumerate(executed)
    ] + direct_reads


def _set_query_ids(run: QualityRun, query_ids: list[str] | None) -> None:
    if query_ids:
        run.query_ids = json.dumps([str(q) for q in query_ids][:200])


async def report(
    session: AsyncSession, worker_id: str, hostname: str | None, version: str | None, run_id: int | None,
    *, progress_done: int | None = None, progress_total: int | None = None,
    current_target: str | None = None, query_ids: list[str] | None = None, owner: str | None = None,
) -> dict:
    """생존·진행 보고. 반환 ``cancel`` 은 취소 요청 여부, ``active`` 가 False 면 워커는 그 실행을 그만둔다."""
    if run_id is None:
        await runs.worker_heartbeat(session, worker_id, hostname, None, version, owner)
        return {"cancel": False, "active": True}
    try:
        run = await _owned(session, run_id, worker_id)
    except runs.RunConflictError:
        await runs.worker_heartbeat(session, worker_id, hostname, None, version)
        return {"cancel": False, "active": False}
    run.heartbeat_at = runs._now()
    if progress_done is not None:
        run.progress_done = max(0, progress_done)
    if progress_total is not None:
        run.progress_total = max(0, progress_total)
    if current_target is not None:
        run.current_target = current_target[:500]
    _set_query_ids(run, query_ids)
    await runs.worker_heartbeat(session, worker_id, hostname, run_id, version)
    return {"cancel": bool(run.cancel_requested_by), "active": True}


_FAILURE_PREFIX = {runs.TRINO_ENGINE: "Trino 쿼리를 실행하지 못했습니다", runs.DIRECT_ENGINE: "원천을 직접 읽어 평가하지 못했습니다"}


def _failure(message: str | None, engine: str = runs.TRINO_ENGINE) -> Outcome:
    text = (message or "").strip() or "알 수 없는 오류"
    return Outcome(False, "N/A", f"{_FAILURE_PREFIX.get(engine, _FAILURE_PREFIX[runs.TRINO_ENGINE])}: {text[:500]}")


def _judge(plan: _Plan, results: dict[str, dict]) -> tuple[dict[int, Outcome], dict[int, str], list[str], int]:
    """워커가 돌려준 결과를 판정한다.

    반환: (규칙별 판정, 진단하지 못한 규칙 ID → 설명, 규칙이 잘못돼 쿼리가 실패한 규칙 설명, 성공한 쿼리 수).
    """
    outcomes = dict(plan.outcomes)
    failed: dict[int, str] = {}
    rule_errors: list[str] = []
    succeeded = 0

    def read(compiled_list: list[Compiled], result: dict | None, own: bool = False) -> bool:
        """결과 하나를 판정에 반영한다. 반영했으면 True, 쿼리가 실패했거나 결과가 없으면 False."""
        if not result or result.get("row") is None:
            return False
        try:
            if own:
                outcomes[compiled_list[0].rule_id] = rule_sql.read_own(compiled_list[0], result["row"])
            else:
                outcomes.update(rule_sql.read_scan(compiled_list, result["row"]))
        except Exception as e:  # noqa: BLE001 — 워커가 보낸 행의 모양이 계획과 다르면 그 쿼리만 실패로 본다
            logger.warning("진단 결과 행을 읽지 못했습니다: %r", e)
            result["error"] = f"결과 행을 읽지 못했습니다({type(e).__name__})"
            return False
        return True

    def fail(compiled: Compiled, result: dict | None, fallback: dict | None = None, engine: str | None = None) -> None:
        source = result if (result or {}).get("error") else (fallback or {})
        outcomes[compiled.rule_id] = _failure(source.get("error") or "결과를 받지 못했습니다", engine or plan.engine)
        # 규칙이 잘못돼 난 오류(잘못된 정규식·없는 컬럼·함수 등, Trino USER_ERROR)는 그 규칙의 실패다 — 고칠 때까지 실패로 보여야 한다.
        # 시간 초과·자원 부족·접속 문제처럼 진단하지 못한 것만 "쿼리 실패" 로 센다(결과를 남기지 않는다).
        if source.get("error_type") == "USER_ERROR":
            rule_errors.append(compiled.label)
        else:
            failed[compiled.rule_id] = compiled.label

    # Trino 스캔 · 원천 집계는 묶은 쿼리 하나, 실패하면 규칙별 재실행 결과를 읽는다.
    # 원천 직접 읽기(probes)는 결과가 처음부터 규칙별이다
    grouped = [(plan.aggregate, AGGREGATE_KEY), (plan.scan, SCAN_KEY)] if plan.direct else [(plan.scan, SCAN_KEY)]
    for compiled_list, key in grouped:
        if not compiled_list:
            continue
        whole = results.get(key)
        if not (plan.direct and key == SCAN_KEY) and read(compiled_list, whole):
            succeeded += 1
            continue
        for c in compiled_list:
            sub = results.get(rule_key(c.rule_id))
            if read([c], sub):
                succeeded += 1
            else:
                # 규칙별 재실행 결과가 없으면(규칙이 하나뿐인 묶음) 묶은 쿼리의 오류가 그 규칙의 오류다
                fail(c, sub, whole)
    for c in plan.separate:
        result = results.get(rule_key(c.rule_id))
        if read([c], result, own=True):
            succeeded += 1
        else:
            fail(c, result)
    for c in plan.python:
        # 커스텀 Python — 플러그인이 돌려준 (통과, 실제값, 설명)을 그대로 쓴다
        result = results.get(rule_key(c.rule_id))
        verdict = (result or {}).get("outcome")
        if isinstance(verdict, dict) and "passed" in verdict:
            outcomes[c.rule_id] = Outcome(
                bool(verdict["passed"]), str(verdict.get("actual") or "")[:500],
                f"{str(verdict.get('detail') or '')[:1500]} (커스텀 Python, {rule_sql.DIRECT_SCOPE})")
            succeeded += 1
        else:
            fail(c, result, results.get(SCAN_KEY if plan.direct else PYTHON_KEY), runs.DIRECT_ENGINE)
    return outcomes, failed, rule_errors, succeeded


def run_score(passed: int, evaluated: int) -> float | None:
    """실행의 품질 점수 — 그 실행에서 평가한 규칙 가운데 통과한 비율(%). 평가한 규칙이 없으면 None.

    데이터셋의 품질 점수와 다르다: 품질 지표를 골라 일부 규칙만 진단한 실행은 그 규칙만 센다.
    """
    return round(passed / evaluated * 100, 1) if evaluated else None


def _end(run: QualityRun, status: str, message: str | None) -> None:
    run.status = status
    run.error_message = message
    run.finished_at = runs._now()
    run.heartbeat_at = run.finished_at
    run.current_target = None


def sample_rows(columns: list | None, rows: list | None) -> list[dict] | None:
    """워커가 보낸 위반 행 → 저장 형식(``{컬럼: 문자열 또는 None}`` 목록, 결과 응답의 ``failed_samples``)."""
    if not rows:
        return None
    names = [str(c) for c in (columns or [])][:_SAMPLE_COLUMNS]
    out = []
    for row in rows[:rule_sql.SAMPLE_ROWS]:
        if isinstance(row, dict):
            items = list(row.items())[:_SAMPLE_COLUMNS]
        elif isinstance(row, (list, tuple)):
            items = list(zip(names, row))
        else:
            continue
        # Trino 시각 값은 프로파일과 같은 모양으로("2006-02-15 05:03:42+00:00")
        out.append({str(k): (None if v is None else (profiling.text_value(v) or "")[:_SAMPLE_VALUE_CHARS])
                    for k, v in items})
    return out or None


def _sample_steps(plan: _Plan, outcomes: dict[int, Outcome], stored: set[int]) -> list[dict]:
    """판정이 실패한(위반이 있는) 규칙의 샘플 쿼리 — 워커가 실행 뒤에 이어서 낸다."""
    steps = []
    # 원천 직접 읽기(probes)는 읽는 동안 위반 행을 모은다 — 원천 집계 · 참조관계만 샘플 쿼리를 이어서 낸다
    candidates = [*plan.aggregate, *plan.separate] if plan.direct else [*plan.scan, *plan.separate]
    for c in candidates:
        o = outcomes.get(c.rule_id)
        if c.rule_id not in stored or o is None or o.passed or not o.error_count:
            continue
        # 원천 직접 경로(집계 · 참조관계)는 compile_snapshot 이 원본 방언의 샘플 쿼리로 바꿔 둔다
        if c.sample_sql:
            steps.append({"key": rule_key(c.rule_id), "label": f"위반 샘플 {c.label}", "sql": c.sample_sql})
        if len(steps) >= _SAMPLE_RULES:
            break
    return steps


async def _store(session: AsyncSession, run: QualityRun, plan: _Plan, results: dict[str, dict]) -> list[dict]:
    """판정을 결과로 저장하고 실행을 끝낸다. 반환: 워커가 이어서 낼 위반 샘플 쿼리."""
    outcomes, failed, rule_errors, succeeded = _judge(plan, results)
    meta = results.get(DIRECT_META_KEY) or {}
    truncated = (plan.direct or plan.mixed) and bool(meta.get("truncated"))
    if truncated:
        # 워커의 행 수 상한(direct_max_rows)에 걸려 앞부분만 읽었다 — 판정 설명에 남긴다
        note = f" — 행 수 상한으로 앞 {int(meta.get('rows_read') or 0):,}행만 검사"
        for c in (*(plan.scan if plan.direct else ()), *plan.python):
            o = outcomes.get(c.rule_id)
            if o is not None and c.rule_id not in failed:
                outcomes[c.rule_id] = replace(o, detail=o.detail + note)
    # 실행 도중 지워지거나 꺼진 규칙은 뺀다
    active = set((await session.execute(
        select(QualityRule.id).where(QualityRule.dataset_id == run.dataset_id, QualityRule.is_active == "true")
    )).scalars().all())
    items = [
        ResultImportItem(
            rule_id=rule_id, passed=o.passed, actual_value=o.actual_value, detail=o.detail,
            checked_count=o.checked_count, error_count=o.error_count,
            # 원천 직접 경로는 읽는 동안 모은 위반 행이 결과와 함께 온다
            failed_samples=None if o.passed else sample_rows(
                (results.get(rule_key(rule_id)) or {}).get("sample_columns"),
                (results.get(rule_key(rule_id)) or {}).get("samples")),
        )
        # 쿼리가 실패해 진단하지 못한 규칙은 결과를 남기지 않는다 — 실패로 저장하면 점수·품질 상태·알림에 섞인다(직전 결과를 이어받음)
        for rule_id, o in outcomes.items() if rule_id in active and rule_id not in failed
    ]
    all_failed = bool(failed or rule_errors) and succeeded == 0
    await _update_row_count(session, run.dataset_id, _scanned_rows(plan, results, truncated))
    if items and not all_failed:
        # 데이터셋의 품질 점수(활성 규칙 전체, 이번에 평가하지 않은 규칙은 직전 결과를 이어받음)는 import_results 가 갱신한다
        await service.import_results(
            session, run.dataset_id, ResultsImportRequest(results=items),
            label="품질 진단 (원천 직접)" if plan.direct else
            "품질 진단 (Trino + 원천 직접)" if plan.mixed else "품질 진단 (Trino)", run_id=run.id)
        run.score = run_score(sum(1 for i in items if i.passed), len(items))
    counted = [o for rule_id, o in outcomes.items() if rule_id in active and o.checked_count is not None]
    run.rules_evaluated = 0 if all_failed else len(items)
    run.checked_count = sum(o.checked_count or 0 for o in counted) if counted and not all_failed else None
    run.error_count = sum(o.error_count or 0 for o in counted) if counted and not all_failed else None
    if all_failed:
        # 모든 쿼리가 실패했다 — 접속·권한·테이블 이름 문제일 가능성이 높아 결과를 남기지 않는다
        detail = next((o.detail for o in outcomes.values() if o.detail.startswith(tuple(_FAILURE_PREFIX.values()))), "")
        _end(run, "FAILED", f"모든 쿼리가 실패했습니다. {detail}".strip())
    elif failed or rule_errors:
        parts = []
        if rule_errors:
            parts.append("쿼리가 실패한 규칙: " + ", ".join(rule_errors[:20]))
        if failed:
            parts.append("진단하지 못한 규칙(직전 결과 유지): " + ", ".join(list(failed.values())[:20]))
        _end(run, "PARTIAL", " / ".join(parts))
    else:
        _end(run, "SUCCEEDED", None if items else "평가할 규칙이 없었습니다.")
    if run.status in ("SUCCEEDED", "PARTIAL"):
        notes = [run.error_message] if run.error_message else []
        if truncated:
            notes.append(f"행이 많아 앞 {int(meta.get('rows_read') or 0):,}행만 진단했습니다(품질 워커 direct_max_rows).")
        if plan.skipped:
            python_error = snapshot_note(run, "python_error")
            why = ("원천 직접 진단으로 평가하지 못해 건너뛴 규칙(직전 결과 유지 — Trino 함수·커스텀 SQL 은 Trino 연결 필요): "
                   if plan.direct else
                   f"커스텀 Python 을 원천 직접으로 평가하지 못해 건너뛴 규칙(직전 결과 유지 — {python_error}): " if python_error else
                   "Trino 로 평가하지 못해 건너뛴 규칙(직전 결과 유지 — 커스텀 Python 은 품질 워커가 원본을 직접 읽을 수 있는 "
                   "데이터 소스에서만): ")
            notes.append(why + ", ".join(plan.skipped[:20]))
        run.error_message = " / ".join(notes) or None
    logger.info("품질 진단 실행 종료: run_id=%d status=%s rules=%d checked=%s errors=%s",
                run.id, run.status, run.rules_evaluated, run.checked_count, run.error_count)
    if all_failed or not items:
        return []
    return _sample_steps(plan, outcomes, {i.rule_id for i in items if not i.failed_samples})


def snapshot_note(run: QualityRun, key: str) -> str | None:
    """실행 행에 남긴 계획의 바탕에서 값 하나(설명용)."""
    try:
        value = json.loads(run.plan_json or "{}").get(key)
    except (ValueError, AttributeError):
        return None
    return str(value) if value else None


def _scanned_rows(plan: _Plan, results: dict[str, dict], truncated: bool) -> int | None:
    """이번 진단이 센 테이블 전체 행 수 — 스캔 · 원천 집계(또는 규칙별 재실행)의 첫 값.
    원천 직접 읽기만 있었는데 상한에 걸렸으면 None."""
    if plan.direct:
        for key in (AGGREGATE_KEY, *(rule_key(c.rule_id) for c in plan.aggregate)):
            row = (results.get(key) or {}).get("row")
            if row:
                try:
                    return int(row[0])
                except (TypeError, ValueError):
                    break
        meta = results.get(DIRECT_META_KEY) or {}
        return None if truncated or "rows_read" not in meta else int(meta.get("rows_read") or 0)
    for key in (SCAN_KEY, *(rule_key(c.rule_id) for c in plan.scan)):
        row = (results.get(key) or {}).get("row")
        if row:
            try:
                return int(row[0])
            except (TypeError, ValueError):
                return None
    meta = results.get(DIRECT_META_KEY) or {}
    if plan.mixed and not truncated and "rows_read" in meta:
        return int(meta.get("rows_read") or 0)  # 스캔 없이 커스텀 Python 만 원천을 읽었다
    return None


async def _update_row_count(session: AsyncSession, dataset_id: int, rows: int | None) -> None:
    """품질 워커가 센 전체 행 수로 데이터셋의 행 수를 갱신한다."""
    if rows is None:
        return
    dataset = await session.get(Dataset, dataset_id)
    if dataset is not None and dataset.row_count != rows:
        dataset.row_count = rows


def _step_error(results: dict[str, dict]) -> str:
    for r in results.values():
        if r.get("error"):
            return str(r["error"])[:500]
    return "결과를 받지 못했습니다"


async def _store_profile(session: AsyncSession, run: QualityRun, snapshot: dict, results: dict[str, dict]) -> None:
    """프로파일 결과를 저장하고 실행을 끝낸다. 행 수를 하나도 얻지 못했으면 실패(저장하지 않음)."""
    columns = snapshot.get("columns") or []
    direct = snapshot.get("engine") == runs.DIRECT_ENGINE
    row_count, profiles, missing = profiling.read_profile(
        columns, results, source_type=snapshot.get("source_type") if direct else None)
    if row_count is None:
        _end(run, "FAILED", f"{_FAILURE_PREFIX[runs.DIRECT_ENGINE if direct else runs.TRINO_ENGINE]}: {_step_error(results)}")
        logger.warning("프로파일 실행 실패: run_id=%d %s", run.id, run.error_message)
        return
    session.add(DataProfile(
        dataset_id=run.dataset_id, row_count=row_count,
        profile_json=json.dumps([cp.model_dump() for cp in profiles], ensure_ascii=False, default=str),
    ))
    await _update_row_count(session, run.dataset_id, row_count)
    run.checked_count = None
    run.error_count = None
    if missing:
        _end(run, "PARTIAL", "통계를 내지 못한 컬럼(이름 · 타입만 남김): " + ", ".join(missing[:30]))
    else:
        _end(run, "SUCCEEDED", None)
    logger.info("프로파일 실행 종료: run_id=%d status=%s rows=%d columns=%d", run.id, run.status, row_count, len(profiles))


async def complete(
    session: AsyncSession, run_id: int, worker_id: str, results: list[dict], query_ids: list[str] | None,
) -> tuple[QualityRun, list[dict]]:
    """워커가 돌려준 결과 행으로 판정·저장하고 실행을 끝낸다. 이 워커의 실행이 아니면 ``RunConflictError``.

    반환: (실행, 위반 샘플 쿼리) — 샘플 쿼리는 워커가 이어서 내 ``attach_samples`` 로 보낸다.
    """
    run = await _owned(session, run_id, worker_id)
    samples: list[dict] = []
    _set_query_ids(run, query_ids)
    by_key = {str(r.get("key")): dict(r) for r in results if r.get("key")}
    try:
        snapshot = json.loads(run.plan_json or "")
        plan = None if snapshot.get("kind") == runs.PROFILE_KIND else compile_snapshot(snapshot)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        _end(run, "FAILED", "실행 계획을 읽지 못해 결과를 판정하지 못했습니다.")
        logger.error("품질 진단 실행 계획 손상: run_id=%d %r", run_id, e)
    else:
        run.progress_done = run.progress_total or run.progress_done
        if plan is None:
            await _store_profile(session, run, snapshot, by_key)
        else:
            samples = await _store(session, run, plan, by_key)
            if samples and not plan.direct:
                # Trino 경로의 샘플 쿼리도 커스텀 SQL 처럼 데이터셋의 카탈로그 · 스키마를 기본값으로
                samples = [{**q, "catalog": plan.table.catalog, "schema": plan.table.schema} for q in samples]
    await runs.worker_heartbeat(session, worker_id, None, None)
    return run, samples


async def attach_samples(session: AsyncSession, run_id: int, worker_id: str, results: list[dict]) -> int:
    """워커가 이어서 낸 위반 샘플을 그 실행의 실패 결과에 붙인다. 반환: 붙인 규칙 수.

    실행은 이미 끝난 상태다 — 이 실행을 처리한 워커만 보낼 수 있다(아니면 ``RunConflictError``).
    """
    from app.quality.models import QualityResult

    run = await session.get(QualityRun, run_id)
    if run is None or run.worker_id != worker_id or run.status not in ("SUCCEEDED", "PARTIAL"):
        raise runs.RunConflictError("이 워커가 끝낸 진단이 아닙니다.")
    attached = 0
    for r in results:
        key = str(r.get("key") or "")
        if not key.startswith("rule:") or r.get("error"):
            continue
        try:
            rule_id = int(key.split(":", 1)[1])
        except ValueError:
            continue
        rows = sample_rows(r.get("columns"), r.get("rows"))
        if not rows:
            continue
        result = (await session.execute(
            select(QualityResult).where(QualityResult.run_id == run.id, QualityResult.rule_id == rule_id,
                                        QualityResult.passed == "false").order_by(QualityResult.id.desc()).limit(1)
        )).scalar_one_or_none()
        if result is not None and not result.failed_samples:
            result.failed_samples = json.dumps(rows, ensure_ascii=False)
            attached += 1
    logger.info("위반 샘플 저장: run_id=%d 규칙 %d개", run_id, attached)
    return attached


async def finish(
    session: AsyncSession, run_id: int, worker_id: str, outcome: str, message: str | None,
    query_ids: list[str] | None = None,
) -> QualityRun:
    """결과 없이 끝낸다(``FAILED``·``CANCELLED``) 또는 대기열로 되돌린다(``REQUEUE`` — 워커 종료)."""
    run = await _owned(session, run_id, worker_id)
    _set_query_ids(run, query_ids)
    if outcome == "REQUEUE" and not run.cancel_requested_by:
        run.status = "QUEUED"
        run.worker_id = None
        run.started_at = None
        run.heartbeat_at = None
        run.current_target = None
        run.progress_done = 0
        logger.info("품질 진단 실행을 대기열에 되돌림: run_id=%d worker=%s", run_id, worker_id)
    elif outcome in ("CANCELLED", "REQUEUE"):
        _end(run, "CANCELLED", "사용자가 중지했습니다.")
    else:
        _end(run, "FAILED", (message or "품질 워커가 실행에 실패했습니다.")[:2000])
        logger.warning("품질 진단 실행 실패: run_id=%d worker=%s %s", run_id, worker_id, message)
    await runs.worker_heartbeat(session, worker_id, None, None)
    return run
