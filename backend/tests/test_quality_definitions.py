# SPDX-License-Identifier: Apache-2.0
"""품질 지표(``app.quality.definitions``) 테스트 (자기완결형, SQLite).

- A: 지표 — 만들기·검증·이름 중복·기본 지표
- B: 할당 — 규칙 생성, 이미 있는 규칙 연결, 건너뜀, 해제, 추천 컬럼
- C: 연결 — 지표를 바꾸면 규칙이 따라 바뀜, 연결된 규칙은 기준을 직접 못 바꿈, 지표를 지우면 직접 정한 규칙으로 남음
- D: 쿼리 미리보기 — 진단과 같은 SQL, 예시 이름·실제 이름
- E: API

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_definitions.py
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy import select

import app.catalog.models as cm
from app.quality import definitions, execution, indicators, rule_sql, service
from app.quality.models import QualityDefinition, QualityRule
from app.quality.schemas import (
    QualityAssignRequest,
    QualityDefinitionWrite,
    QualityRuleUpdate,
    QualitySqlPreviewRequest,
)
from tests.test_quality_trino_runs import (  # noqa: F401 — _no_alerts 는 autouse fixture
    _api,
    _make_sessionmaker,
    _no_alerts,
    _run,
    _seed,
)


def _write(**kw) -> QualityDefinitionWrite:
    base = {"name": "여부 Y/N", "category": "YN", "expected_value": "Y,N"}
    return QualityDefinitionWrite(**{**base, **kw})


def _cols(*pairs) -> QualityAssignRequest:
    return QualityAssignRequest(columns=[{"dataset_id": d, "column_name": c} for d, c in pairs])


def _setup(tmp_path, rules: list[dict] | None = None):
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, rule_ids = asyncio.run(_seed(sm, rules or []))
    return sm, ds_id, rule_ids


def _rules(sm, ds_id: int) -> list[QualityRule]:
    async def go(s):
        return list((await s.execute(
            select(QualityRule).where(QualityRule.dataset_id == ds_id).order_by(QualityRule.id))).scalars().all())
    return _run(sm, go)


# ---------------------------------------------------------------------------
# A — 지표
# ---------------------------------------------------------------------------

def test_write_fills_type_and_validates() -> None:
    assert _write().check_type == "ACCEPTED_VALUES"
    assert _write(category="amount", check_type="MAX_VALUE", expected_value="100").category == "AMOUNT"
    assert _write(category="AMOUNT", expected_value="0").check_type == "MIN_VALUE", "유형을 안 적으면 분류의 첫 유형"
    for bad in (
        {"category": "LOGICAL", "expected_value": "IF a = 1 THEN b = 2"},   # 미리 정의할 수 없는 분류
        {"category": "NOPE"},
        {"category": "REQUIRED", "check_type": "REGEX"},                     # 분류에 없는 유형
        {"category": "YN", "expected_value": " "},                           # 기준값 없음
        {"category": "AMOUNT", "expected_value": "zero"},                    # 숫자가 아님
        {"category": "DATE", "expected_value": "YY-MM-DD"},                  # 읽을 수 없는 날짜 형식
        {"name": "  "},
    ):
        with pytest.raises(ValidationError):
            _write(**bad)


def test_function_definition_takes_only_a_function_name(tmp_path) -> None:
    """Trino 함수 지표 — 기준값은 미리 등록한 함수 이름. 정규식처럼 여러 분류에서 쓴다."""
    ok = _write(name="사업자등록번호 검증", category="NUMBER", check_type="FUNCTION", expected_value="memory.default.is_valid_brno")
    assert ok.check_type == "FUNCTION"
    assert _write(name="율 검증", category="RATE", check_type="FUNCTION", expected_value="dq.is_rate").category == "RATE"
    for bad in (None, "", "is_valid(x)", "a b"):
        with pytest.raises(ValueError):
            _write(name="x", category="NUMBER", check_type="FUNCTION", expected_value=bad)
    with pytest.raises(ValueError):
        _write(name="x", category="REQUIRED", check_type="FUNCTION", expected_value="f")
    assert indicators.indicator_of("FUNCTION", None, "biz_amt").key == "NUMBER", "적지 않으면 번호 — 이름으로 추정하지 않는다"
    assert indicators.indicator_of("FUNCTION", "RATE", "x").key == "RATE"


def test_regex_definition_counts_under_its_category(tmp_path) -> None:
    """정규식은 번호뿐 아니라 날짜·코드 등 어느 분류의 지표에도 쓴다 — 할당한 규칙은 그 분류로 센다."""
    sm, ds_id, _ = _setup(tmp_path)
    assert _write(name="코드 3자리", category="CODE", check_type="REGEX", expected_value="^[A-Z]{3}$").check_type == "REGEX"
    assert _write(category="CODE").check_type == "ACCEPTED_VALUES", "유형을 안 적으면 분류의 원래 유형"
    made = {}
    for name, category in (("코드 3자리", "CODE"), ("연도", "DATE"), ("영문 대문자", "PATTERN"), ("전화", "NUMBER")):
        d = _run(sm, lambda s, n=name, c=category: definitions.create_definition(
            s, _write(name=n, category=c, check_type="REGEX", expected_value="^[A-Z]{3}$")))
        made[category] = d.id
    for column, category in (("use_yn", "CODE"), ("reg_ymd", "DATE"), ("bgn_ymd", "PATTERN"), ("end_ymd", "NUMBER")):
        _run(sm, lambda s, c=column, k=category: definitions.assign(s, made[k], _cols((ds_id, c))))
    listed = {r.column_name: (r.check_type, r.domain, r.indicator) for r in _run(sm, lambda s: service.list_rules(s, ds_id))}
    assert listed == {
        "use_yn": ("REGEX", "CODE", "CODE"), "reg_ymd": ("REGEX", "DATE", "DATE"),
        "bgn_ymd": ("REGEX", "PATTERN", "PATTERN"), "end_ymd": ("REGEX", "NUMBER", "NUMBER")}
    # 날짜 형식 지표는 도메인을 적지 않는다(유형만으로 날짜)
    fmt = _run(sm, lambda s: definitions.create_definition(s, _write(name="날짜", category="DATE", expected_value="YYYYMMDD")))
    _run(sm, lambda s: definitions.assign(s, fmt.id, _cols((ds_id, "cust_id"))))
    rule = next(r for r in _run(sm, lambda s: service.list_rules(s, ds_id)) if r.column_name == "cust_id")
    assert (rule.domain, rule.indicator) == (None, "DATE")
    # 지표의 분류를 바꾸면 규칙의 분류가 따라 바뀐다
    _run(sm, lambda s: definitions.update_definition(
        s, made["CODE"], _write(name="코드 3자리", category="PATTERN", check_type="REGEX", expected_value="^[A-Z]{3}$")))
    moved = next(r for r in _run(sm, lambda s: service.list_rules(s, ds_id)) if r.column_name == "use_yn")
    assert moved.indicator == "PATTERN"


def test_every_definable_category_has_only_column_level_types() -> None:
    for category in indicators.definable():
        assert set(category.check_types) <= indicators.DEFINABLE_TYPES
        assert not set(category.check_types) & set(rule_sql.TABLE_LEVEL_TYPES)
    assert {d[1] for d in definitions.DEFAULTS} <= {c.key for c in indicators.definable()}
    for name, category, check_type, expected, _ in definitions.DEFAULTS:
        QualityDefinitionWrite(name=name, category=category, check_type=check_type, expected_value=expected)


def test_create_list_and_name_conflict(tmp_path) -> None:
    sm, _, _ = _setup(tmp_path)
    _run(sm, lambda s: definitions.create_definition(s, _write(name="날짜 YYYYMMDD", category="DATE", expected_value="YYYYMMDD")))
    made = _run(sm, lambda s: definitions.create_definition(s, _write()))
    assert (made.category_name, made.area_name, made.rules) == ("여부", "유효성", 0)
    with pytest.raises(definitions.DefinitionConflictError):
        _run(sm, lambda s: definitions.create_definition(s, _write(name="여부 y/n")))
    assert [d.name for d in _run(sm, definitions.list_definitions)] == ["날짜 YYYYMMDD", "여부 Y/N"], "분류 순"

    assert _run(sm, definitions.add_defaults) == len(definitions.DEFAULTS) - 2, "같은 이름은 건너뜀"
    assert _run(sm, definitions.add_defaults) == 0


# ---------------------------------------------------------------------------
# B — 할당
# ---------------------------------------------------------------------------

def test_assign_creates_rules_adopts_and_skips(tmp_path) -> None:
    # use_yn 에 같은 기준(허용 값 Y,N)으로 직접 만든 규칙이 이미 있다
    sm, ds_id, (direct_id,) = _setup(tmp_path, [{"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"}])
    yn = _run(sm, lambda s: definitions.create_definition(s, _write(severity="BREAKING")))
    date = _run(sm, lambda s: definitions.create_definition(s, _write(name="날짜", category="DATE", expected_value="YYYYMMDD")))

    r = _run(sm, lambda s: definitions.assign(s, yn.id, _cols((ds_id, "USE_YN"), (ds_id, "use_yn"))))
    assert (r.created, r.adopted, r.skipped) == (0, 1, []), "있던 규칙을 연결 — 같은 컬럼을 두 번 적어도 한 번"
    r = _run(sm, lambda s: definitions.assign(s, date.id, _cols((ds_id, "REG_YMD"), (ds_id, "nope"), (999, "x"), (ds_id, "bgn_ymd"))))
    assert r.created == 2
    assert [(k.column_name, k.reason) for k in r.skipped] == [
        ("nope", "데이터셋에 이 컬럼이 없습니다"), ("x", "데이터셋을 찾을 수 없습니다")]
    r = _run(sm, lambda s: definitions.assign(s, date.id, _cols((ds_id, "reg_ymd"))))
    assert (r.created, r.skipped[0].reason) == (0, "이미 할당돼 있습니다")

    rules = _rules(sm, ds_id)
    assert [(x.id == direct_id, x.column_name, x.check_type, x.expected_value, x.definition_id, x.severity, x.domain)
            for x in rules] == [
        (True, "use_yn", "ACCEPTED_VALUES", "Y,N", yn.id, "BREAKING", "YN"),
        (False, "reg_ymd", "DATE_FORMAT", "YYYYMMDD", date.id, "WARNING", None),   # 스키마의 컬럼 이름 그대로
        (False, "bgn_ymd", "DATE_FORMAT", "YYYYMMDD", date.id, "WARNING", None),
    ]
    assert rules[1].rule_name == "날짜"

    listed = {d.name: (d.rules, d.datasets) for d in _run(sm, definitions.list_definitions)}
    assert listed == {"날짜": (2, 1), "여부 Y/N": (1, 1)}
    assigned = _run(sm, lambda s: definitions.list_assignments(s, date.id))
    assert [(a.dataset_name, a.datasource_name, a.column_name) for a in assigned] == [
        ("orders", "sales-pg", "bgn_ymd"), ("orders", "sales-pg", "reg_ymd")], "이름이 같은 데이터셋은 데이터 소스로 가른다"

    # 규칙 목록에는 지표 이름이 나온다
    by_column = {x.column_name: x.definition_name for x in _run(sm, lambda s: service.list_rules(s, ds_id))}
    assert by_column == {"use_yn": "여부 Y/N", "reg_ymd": "날짜", "bgn_ymd": "날짜"}

    # 해제 — 이 지표의 규칙만 지운다
    removed = _run(sm, lambda s: definitions.unassign(s, date.id, [r.id for r in rules]))
    assert removed == 2 and [x.column_name for x in _rules(sm, ds_id)] == ["use_yn"]


def test_candidates_suggest_by_column_name_and_constraint(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], columns={
        "id": "integer", "use_yn": "char(1)", "dsyn": "varchar", "reg_ymd": "varchar(8)", "totalAmt": "numeric",
        "memo": "varchar"}))

    async def flags(s):
        for f in (await s.execute(select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == ds_id))).scalars():
            if f.field_path == "id":
                f.is_primary_key, f.nullable = "true", "false"
    _run(sm, flags)

    def make(name, category, expected=None):
        return _run(sm, lambda s: definitions.create_definition(
            s, QualityDefinitionWrite(name=name, category=category, expected_value=expected))).id

    def names(def_id, **kw):
        return [(c.column_name, c.suggested, c.assigned)
                for c in _run(sm, lambda s: definitions.list_candidates(s, def_id, **kw))]

    yn = make("여부", "YN", "Y,N")
    assert names(yn) == [("use_yn", True, False)], "끝 글자만 같은 dsyn 은 추천하지 않는다"
    _run(sm, lambda s: definitions.assign(s, yn, _cols((ds_id, "use_yn"))))
    assert names(yn) == [("use_yn", True, True)]
    assert names(yn, q="YN") == [("use_yn", True, True), ("dsyn", False, False)], "검색하면 추천이 아닌 것도"
    assert names(yn, catalog_id=999) == []
    assert names(make("날짜", "DATE", "YYYYMMDD")) == [("reg_ymd", True, False)]
    assert names(make("금액", "AMOUNT", "0")) == [("totalAmt", True, False)]
    assert names(make("필수", "REQUIRED")) == [("id", True, False)]
    assert names(make("중복", "DUPLICATE")) == [("id", True, False)]
    assert names(make("깨짐", "BROKEN_TEXT")) == [], "이름으로 추정할 수 없는 분류는 검색해서 고른다"
    assert len(names(make("공백", "WHITESPACE"), q="orders")) == 6, "데이터셋 이름으로도 찾는다"


# ---------------------------------------------------------------------------
# C — 연결
# ---------------------------------------------------------------------------

def test_updating_definition_updates_assigned_rules(tmp_path) -> None:
    sm, ds_id, _ = _setup(tmp_path)
    d = _run(sm, lambda s: definitions.create_definition(s, _write(name="필수", category="REQUIRED", expected_value=None, threshold=100)))
    _run(sm, lambda s: definitions.assign(s, d.id, _cols((ds_id, "use_yn"), (ds_id, "amt"))))
    use_yn, amt = _rules(sm, ds_id)
    # 한 컬럼은 임계값과 이름을 따로 줬다
    _run(sm, lambda s: service.update_rule(s, amt.id, QualityRuleUpdate(threshold=90, rule_name="금액 필수")))

    changed = _write(name="필수값", category="REQUIRED", expected_value=None, threshold=95, severity="BREAKING")
    out = _run(sm, lambda s: definitions.update_definition(s, d.id, changed))
    assert (out.name, out.threshold, out.rules) == ("필수값", 95, 2)
    assert [(r.rule_name, float(r.threshold), r.severity) for r in _rules(sm, ds_id)] == [
        ("필수값", 95.0, "BREAKING"), ("금액 필수", 90.0, "BREAKING")], "따로 준 임계값·이름은 남는다"

    # 분류·유형·기준값을 바꾸면 규칙의 유형·기준값·도메인이 따라 바뀐다
    _run(sm, lambda s: definitions.update_definition(s, d.id, _write(name="필수값", category="YN", expected_value="Y,N")))
    assert {(r.check_type, r.expected_value, r.domain) for r in _rules(sm, ds_id)} == {("ACCEPTED_VALUES", "Y,N", "YN")}
    with pytest.raises(definitions.DefinitionNotFoundError):
        _run(sm, lambda s: definitions.update_definition(s, 999, changed))


def test_linked_rule_cannot_change_criteria_until_unlinked(tmp_path) -> None:
    sm, ds_id, _ = _setup(tmp_path)
    d = _run(sm, lambda s: definitions.create_definition(s, _write()))
    _run(sm, lambda s: definitions.assign(s, d.id, _cols((ds_id, "use_yn"))))
    (rule,) = _rules(sm, ds_id)

    with pytest.raises(ValueError, match="품질 지표에 연결된 규칙"):
        _run(sm, lambda s: service.update_rule(s, rule.id, QualityRuleUpdate(expected_value="Y,N,U")))
    # 같은 값을 다시 보내는 것, 임계값·활성 여부는 된다
    same = QualityRuleUpdate(expected_value="Y,N", check_type="ACCEPTED_VALUES", severity="WARNING", threshold=80,
                             is_active="false")
    out = _run(sm, lambda s: service.update_rule(s, rule.id, same))
    assert (out.threshold, out.is_active, out.definition_name, out.indicator) == (80, "false", "여부 Y/N", "YN")
    with pytest.raises(ValidationError):
        QualityRuleUpdate(definition_id=d.id)   # 연결은 할당으로만

    # 연결을 끊으면 직접 정한 규칙 — 기준을 바꿀 수 있다
    out = _run(sm, lambda s: service.update_rule(s, rule.id, QualityRuleUpdate(definition_id=None, expected_value="Y,N,U")))
    assert (out.definition_id, out.definition_name, out.expected_value) == (None, None, "Y,N,U")


def test_deleting_definition_keeps_rules_as_direct(tmp_path) -> None:
    sm, ds_id, _ = _setup(tmp_path)
    d = _run(sm, lambda s: definitions.create_definition(s, _write()))
    _run(sm, lambda s: definitions.assign(s, d.id, _cols((ds_id, "use_yn"))))
    assert _run(sm, lambda s: definitions.delete_definition(s, d.id)) == 1
    (rule,) = _rules(sm, ds_id)
    assert (rule.definition_id, rule.check_type, rule.expected_value) == (None, "ACCEPTED_VALUES", "Y,N")
    assert _run(sm, lambda s: s.scalar(select(QualityDefinition.id))) is None


# ---------------------------------------------------------------------------
# D — 쿼리 미리보기
# ---------------------------------------------------------------------------

def _preview(sm, **kw):
    return _run(sm, lambda s: definitions.preview_sql(s, QualitySqlPreviewRequest(**kw)))


def test_preview_uses_sample_names_without_dataset(tmp_path) -> None:
    sm, _, _ = _setup(tmp_path)
    p = _preview(sm, check_type="ACCEPTED_VALUES", expected_value="Y,N")
    assert p.sql == (
        'SELECT\n'
        '  count(*),  -- 전체 행 수\n'
        '  count("column_name"),  -- 진단 건수 (NULL 제외)\n'
        '  count_if("column_name" IS NOT NULL AND CAST("column_name" AS varchar) NOT IN (\'Y\', \'N\'))  -- 오류 건수\n'
        'FROM "catalog"."schema"."table"')
    assert not p.actual_table and "예시" in p.note and "오류 건수가 0" in p.judgement
    assert "임계값 95%" in _preview(sm, check_type="NOT_NULL", threshold=95).judgement
    assert "최대값이 100 이하" in _preview(sm, check_type="MAX_VALUE", expected_value="100").judgement

    bad = _preview(sm, check_type="DATE_FORMAT", expected_value="YY")
    assert bad.sql is None and "쿼리를 만들 수 없습니다" in bad.note and bad.judgement == ""
    assert _preview(sm, check_type="FRESHNESS", expected_value="24").sql is None


def test_preview_matches_the_query_the_run_executes(tmp_path) -> None:
    rules = [
        {"check_type": "DATE_FORMAT", "column": "reg_ymd", "expected": "YYYYMMDD"},
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"},
        {"check_type": "MIN_VALUE", "column": "amt", "expected": "0"},
        {"check_type": "LOGICAL", "expected": "IF use_yn = 'Y' THEN amt >= 0"},
    ]
    sm, ds_id, _ = _setup(tmp_path, rules)
    table = rule_sql.TableRef("pg_sales", "public", "orders")
    types = {"use_yn": "char(1)", "reg_ymd": "varchar(8)", "bgn_ymd": "varchar(8)", "end_ymd": "varchar(8)",
             "cust_id": "integer", "amt": "numeric"}
    for n, r in enumerate(rules):
        p = _preview(sm, check_type=r["check_type"], column_name=r.get("column"), expected_value=r["expected"],
                     dataset_id=ds_id)
        assert p.actual_table and p.table == "pg_sales.public.orders" and p.note is None
        spec = rule_sql.RuleSpec(id=n, check_type=r["check_type"], column_name=r.get("column"), expected_value=r["expected"])
        compiled = rule_sql.compile_rule(spec, table, types)
        # 주석과 줄바꿈을 빼면 진단이 실행하는 스캔 쿼리와 같다
        flat = " ".join(line.split("  -- ")[0].strip() for line in p.sql.splitlines())
        assert flat.replace("SELECT ", "SELECT ", 1) == rule_sql.scan_sql(table, [compiled]).replace(", ", ", "), r
    assert "rtrim(CAST(\"use_yn\" AS varchar))" in _preview(
        sm, check_type="ACCEPTED_VALUES", column_name="use_yn", expected_value="Y", dataset_id=ds_id).sql, "실제 컬럼 타입"

    missing = _preview(sm, check_type="NOT_NULL", column_name="nope", dataset_id=ds_id)
    assert missing.sql is None and "'nope' 컬럼이 없습니다" in missing.note
    with pytest.raises(definitions.DefinitionNotFoundError):
        _preview(sm, check_type="NOT_NULL", column_name="a", dataset_id=999)


def test_preview_explains_when_trino_table_is_unknown(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    # 원천을 직접 읽을 수 없는 유형(Hive)이라 원천 직접 경로도 없다 — 원천 직접이면 원천 방언으로 보인다(DEF-152)
    ds_id, _ = asyncio.run(_seed(sm, [], connect=False, source_type="hive"))
    p = _preview(sm, check_type="NOT_NULL", column_name="use_yn", dataset_id=ds_id)
    assert not p.actual_table and 'FROM "catalog"."public"."orders"' in p.sql and "카탈로그 이름은 예시" in p.note


def test_preview_referential_resolves_reference(tmp_path) -> None:
    sm, ds_id, _ = _setup(tmp_path)
    p = _preview(sm, check_type="REFERENTIAL", column_name="cust_id", expected_value=f"{ds_id}#cust_id", dataset_id=ds_id)
    assert p.sql.startswith("SELECT count(*), count_if(p.k IS NULL)\nFROM \"pg_sales\".\"public\".\"orders\" c\nLEFT JOIN (")
    assert "참조 테이블에 없는 값" in p.judgement
    sample = _preview(sm, check_type="REFERENTIAL", expected_value="12#cust_id")
    assert '"reference_table"' in sample.sql
    assert execution._resolve_reference  # 미리보기가 쓰는 실행부 함수


# ---------------------------------------------------------------------------
# E — API
# ---------------------------------------------------------------------------

def test_api_definition_lifecycle(tmp_path) -> None:
    sm, ds_id, _ = _setup(tmp_path)
    client = _api(sm)
    base = "/api/v1/quality/definitions"
    assert client.post(f"{base}/defaults").json() == {"added": len(definitions.DEFAULTS)}
    listed = client.get(base).json()
    assert len(listed) == len(definitions.DEFAULTS) and listed[0]["area_name"] == "완결성"

    created = client.post(base, json={"name": "사용 여부", "category": "YN", "expected_value": "Y,N"})
    assert created.status_code == 201, created.text
    def_id = created.json()["id"]
    assert client.post(base, json={"name": "사용 여부", "category": "YN", "expected_value": "Y,N"}).status_code == 409
    assert client.post(base, json={"name": "x", "category": "LOGICAL"}).status_code == 422

    cands = client.get(f"{base}/{def_id}/candidates").json()
    assert [(c["datasource_name"], c["column_name"], c["suggested"]) for c in cands] == [("sales-pg", "use_yn", True)]
    out = client.post(f"{base}/{def_id}/assign", json={"columns": [{"dataset_id": ds_id, "column_name": "use_yn"}]})
    assert out.status_code == 200 and out.json()["created"] == 1
    assigned = client.get(f"{base}/{def_id}/assignments").json()
    assert [(a["dataset_name"], a["column_name"]) for a in assigned] == [("orders", "use_yn")]
    rule_id = assigned[0]["rule_id"]

    rules = client.get("/api/v1/quality/rules", params={"dataset_id": ds_id}).json()
    assert (rules[0]["definition_id"], rules[0]["definition_name"], rules[0]["indicator"]) == (def_id, "사용 여부", "YN")
    locked = client.put(f"/api/v1/quality/rules/{rule_id}", json={"expected_value": "1,0"})
    assert locked.status_code == 422 and "품질 지표에 연결된 규칙" in locked.json()["detail"]

    updated = client.put(f"{base}/{def_id}", json={"name": "사용 여부", "category": "YN", "expected_value": "1,0"})
    assert updated.status_code == 200 and updated.json()["rules"] == 1
    assert client.get("/api/v1/quality/rules", params={"dataset_id": ds_id}).json()[0]["expected_value"] == "1,0"

    sql = client.post("/api/v1/quality/sql-preview", json={
        "check_type": "ACCEPTED_VALUES", "expected_value": "1,0", "column_name": "use_yn", "dataset_id": ds_id}).json()
    assert sql["actual_table"] and "NOT IN ('1', '0')" in sql["sql"]
    assert client.post("/api/v1/quality/sql-preview", json={"check_type": "NOPE"}).status_code == 422
    assert client.post("/api/v1/quality/sql-preview", json={"check_type": "NOT_NULL", "dataset_id": 999}).status_code == 404

    # 품질 진단 작업 등록 화면의 규칙 목록에도 지표가 따라온다
    overview = client.post("/api/v1/quality/jobs/rules", json={"targets": [{"type": "dataset", "dataset_id": ds_id}]}).json()
    assert overview[0]["rules"][0]["definition_id"] == def_id

    assert client.post(f"{base}/{def_id}/unassign", json={"rule_ids": [rule_id, 999]}).json() == {"removed": 1}
    client.post(f"{base}/{def_id}/assign", json={"columns": [{"dataset_id": ds_id, "column_name": "use_yn"}]})
    assert client.delete(f"{base}/{def_id}").json() == {"kept_rules": 1}
    assert client.get(f"{base}/{def_id}").status_code == 404
    left = client.get("/api/v1/quality/rules", params={"dataset_id": ds_id}).json()
    assert len(left) == 1 and left[0]["definition_id"] is None
