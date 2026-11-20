# SPDX-License-Identifier: Apache-2.0
"""프로파일 실행(품질 워커) 테스트 — TODO-095 3단계 (자기완결형, SQLite — Trino · 원천은 가짜).

- A: 계획 — 묶은 통계 쿼리, 컬럼별 재실행 쿼리, 최빈값 쿼리, 타입별 통계(바이너리 · 복합 · 숫자), 경로별 인용
- B: 결과 읽기 — 묶은 쿼리, 컬럼별 재실행(일부 실패), 최빈값은 고유값이 적은 컬럼만
- C: 실제 워커 코드로 — Trino 경로 · 원천 직접 경로의 프로파일 저장, 데이터셋 행 수 갱신, 실패, 진단 쿼리 API
- D: 규칙 진단도 데이터셋 행 수를 갱신(원천 직접이 상한에 걸리면 갱신하지 않음)
- E: 요청 — 스키마 없음, 규칙 없이도 프로파일, 진행 중 실행과 겹침, API 의 kind

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_profile_runs.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.catalog.models as cm
from app.core.auth import TokenUser
from app.permissions.models import Permission
from app.quality import profiling, runs
from app.quality.models import DataProfile, QualityRun
from app.quality.rule_sql import TableRef
from tests.test_quality_trino_runs import (  # noqa: F401 — _no_alerts 는 autouse 픽스처
    _app,
    _make_sessionmaker,
    _no_alerts,
    _run,
    _seed,
    _work,
    worker_trino,
)

# 품질 워커 소스는 test_quality_trino_runs 가 sys.path 에 넣는다 — 그 뒤에 불러온다
worker_direct = importlib.import_module("quality_worker.direct")

TABLE = TableRef("pg_sales", "public", "orders")
COLUMNS = [["code", "STRING", "char(1)"], ["amt", "NUMBER", "numeric(10,2)"], ["photo", "BYTES", "bytea"],
           ["tags", "ARRAY", "text[]"]]


# ---------------------------------------------------------------- A: 계획

def test_profile_step_builds_one_query_with_per_column_fallback() -> None:
    step = profiling.profile_step(TABLE, COLUMNS, direct=False, source_type=None)
    assert step["key"] == "profile" and step["kind"] == "profile"
    assert step["sql"] == (
        'SELECT count(*), count("code"), count(DISTINCT "code"), min("code"), max("code"), '
        'count("amt"), count(DISTINCT "amt"), min("amt"), max("amt"), avg(CAST("amt" AS double)), '
        'count("photo"), count(DISTINCT "photo"), count("tags") FROM "pg_sales"."public"."orders"')
    assert [f["key"] for f in step["fallback"]] == ["profile:col:code", "profile:col:amt", "profile:col:photo",
                                                    "profile:col:tags"]
    assert step["fallback"][2]["sql"] == 'SELECT count(*), count("photo"), count(DISTINCT "photo") FROM "pg_sales"."public"."orders"'
    # 최빈값은 비교할 수 있는 컬럼만 — 바이너리 · 복합 타입은 없음. 고유값 자리는 묶은 쿼리의 2번째 · 컬럼별 쿼리의 2번째
    assert [(t["key"], t["at"], t["fallback_at"]) for t in step["top"]] == [("profile:top:code", 2, 2), ("profile:top:amt", 6, 2)]
    assert step["top"][0]["sql"] == ('SELECT "code", count(*) FROM "pg_sales"."public"."orders" WHERE "code" IS NOT NULL '
                                     'GROUP BY "code" ORDER BY count(*) DESC LIMIT 5')
    assert step["top_max"] == profiling.TOP_MAX_DISTINCT


def test_profile_step_quotes_for_direct_source() -> None:
    step = profiling.profile_step(TableRef("", "sakila", "film"), [["length", "NUMBER", "smallint unsigned"]],
                                  direct=True, source_type="mysql")
    assert step["sql"] == ("SELECT count(*), count(`length`), count(DISTINCT `length`), min(`length`), "
                           "max(`length`), avg(`length`) FROM `sakila`.`film`")
    assert step["fallback"] == [], "컬럼이 하나면 같은 쿼리를 다시 내지 않는다"
    pg = profiling.profile_step(TableRef("", "public", "t"), [["a", "STRING", "text"]], direct=True, source_type="postgresql")
    assert pg["sql"].endswith('FROM "public"."t"')


def test_direct_text_stats_count_bytes_like_trino() -> None:
    """원천 직접 경로의 문자열 컬럼은 대소문자 · 끝 공백을 구분해 센다 — MySQL 기본 정렬 규칙을 따르지 않는다(TODO-097)."""
    my = profiling.profile_step(TableRef("", "sakila", "customer"), [["email", "STRING", "varchar(50)"]],
                                direct=True, source_type="mysql")
    key = "CAST(CONVERT(`email` USING utf8mb4) AS BINARY)"
    assert my["sql"] == (f"SELECT count(*), count(`email`), count(DISTINCT {key}), CONVERT(min({key}) USING utf8mb4), "
                         f"CONVERT(max({key}) USING utf8mb4) FROM `sakila`.`customer`")
    # 최빈값은 바이트로 묶고, ONLY_FULL_GROUP_BY 에 걸리지 않게 묶은 값을 min 으로 꺼낸다
    assert my["top"][0]["sql"] == (f"SELECT CONVERT(min({key}) USING utf8mb4), count(*) FROM `sakila`.`customer` "
                                   f"WHERE `email` IS NOT NULL GROUP BY {key} ORDER BY count(*) DESC LIMIT 5")
    pg = profiling.profile_step(TableRef("", "public", "t"), [["a", "STRING", "character varying(10)"]],
                                direct=True, source_type="postgresql")
    assert pg["sql"] == ('SELECT count(*), count("a"), count(DISTINCT "a"), min("a" COLLATE "C"), max("a" COLLATE "C") '
                         'FROM "public"."t"')
    assert pg["top"][0]["sql"].startswith('SELECT "a", count(*)')
    # Trino 경로는 그대로 — Trino 는 이미 바이트로 센다
    trino = profiling.profile_step(TABLE, [["code", "STRING", "char(1)"]], direct=False, source_type=None)
    assert 'min("code")' in trino["sql"] and "COLLATE" not in trino["sql"]


# ---------------------------------------------------------------- B: 결과 읽기

def test_direct_profile_handles_lob_and_legacy_types() -> None:
    """원천 DB 가 집계하지 못하는 타입 — Oracle LOB 은 NULL 수만(count(LOB) 도 ORA-22849 라 IS NOT NULL 로 센다),
    SQL Server 의 TEXT · NTEXT · IMAGE · XML 은 (MAX) 타입으로, BIT 는 정수로 바꿔 센다(실제 DB 에서 확인)."""
    table = TableRef("", "S", "T")
    ora_cols = [["C", "STRING", "CLOB"], ["B", "BINARY", "BLOB"], ["N", "NUMBER", "NUMBER(10)"]]
    ora = profiling.profile_step(table, ora_cols, direct=True, source_type="oracle")
    assert ora["sql"] == ('SELECT count(*), count(CASE WHEN "C" IS NOT NULL THEN 1 END), '
                          'count(CASE WHEN "B" IS NOT NULL THEN 1 END), count("N"), count(DISTINCT "N"), min("N"), '
                          'max("N"), avg("N") FROM "S"."T"')
    assert [t["key"] for t in ora["top"]] == ["profile:top:N"]
    # 같은 컬럼이 Trino 경로면 Trino 가 문자열로 센다 — 통계를 빼지 않는다
    assert profiling.column_stats("STRING", "CLOB") == ("nonnull", "distinct", "min", "max")
    count, cols, missing = profiling.read_profile(ora_cols, {"profile": {"row": [3, 2, 1, 3, 3, 1, 9, 5]}},
                                                  source_type="oracle")
    assert (count, missing) == (3, [])
    assert [(c.null_count, c.unique_count, c.max_value) for c in cols] == [(1, 0, None), (2, 0, None), (0, 3, "9")]

    ms_cols = [["t", "STRING", "TEXT"], ["nt", "STRING", "NTEXT"], ["img", "BINARY", "IMAGE"], ["x", "STRING", "XML"],
               ["b", "BOOLEAN", "BIT"]]
    ms = profiling.profile_step(TableRef("", "dbo", "t"), ms_cols, direct=True, source_type="mssql")
    sql = ms["sql"]
    assert "count(CAST([t] AS VARCHAR(MAX)))" in sql
    assert "count(DISTINCT CAST(CAST([nt] AS NVARCHAR(MAX)) AS VARBINARY(MAX)))" in sql
    assert "min(CAST([nt] AS NVARCHAR(MAX)) COLLATE Latin1_General_BIN2)" in sql
    assert "count(DISTINCT CAST([img] AS VARBINARY(MAX)))" in sql and "min(CAST([img]" not in sql
    assert "count(CAST([x] AS NVARCHAR(MAX)))" in sql and "DISTINCT CAST([x]" not in sql
    assert "min(CAST([b] AS TINYINT)), max(CAST([b] AS TINYINT))" in sql
    top = {t["key"]: t["sql"] for t in ms["top"]}
    assert top["profile:top:t"].startswith("SELECT TOP (5) min(CAST([t] AS VARCHAR(MAX))), count(*)")


def test_read_profile_from_combined_row_and_top_values() -> None:
    row = [10, 9, 2, "N", "Y", 10, 10, Decimal("1.50"), "99.00", 42.123456, 3, 3, 0]
    count, cols, missing = profiling.read_profile(COLUMNS, {
        "profile": {"row": row},
        "profile:top:code": {"rows": [["Y", 6], ["N", 3]]},
        "profile:top:amt": {"rows": [["1.50", 1]]},  # 고유값 10개 — 20 이하라 워커가 냈다
    })
    assert count == 10 and missing == []
    code, amt, photo, tags = cols
    assert (code.null_count, code.null_percent, code.unique_count, code.min_value, code.max_value) == (1, 10.0, 2, "N", "Y")
    assert code.top_values == [{"value": "Y", "count": 6}, {"value": "N", "count": 3}]
    assert (amt.unique_percent, amt.min_value, amt.mean_value) == (100.0, "1.50", 42.1235)
    assert (photo.null_count, photo.unique_count, photo.min_value, photo.top_values) == (7, 3, None, None)
    assert (tags.null_count, tags.unique_count) == (10, 0)
    assert all(c.total_count == 10 for c in cols)
    assert code.column_type == "STRING"


def test_profile_values_match_between_paths() -> None:
    """Trino 의 시각 문자열을 원천 직접(드라이버 값의 str)과 같은 모양으로 — 최신성 규칙이 최대값을 시각으로 읽는다."""
    assert profiling.text_value("2006-02-15 05:03:42.000 UTC") == "2006-02-15 05:03:42+00:00"
    assert profiling.text_value("2005-05-24 22:53:30.000") == "2005-05-24 22:53:30"
    assert profiling.text_value("2005-05-24 22:53:30.250") == "2005-05-24 22:53:30.25"
    assert profiling.text_value("2005-05-24 22:53:30 +09:00") == "2005-05-24 22:53:30+09:00"
    assert profiling.text_value("al-Sharqiya") == "al-Sharqiya" and profiling.text_value(5) == "5"
    # MySQL YEAR — 카탈로그 타입은 NUMBER 지만 원천 타입으로 평균을 뺀다
    assert "avg" not in profiling.column_stats("NUMBER", "year")
    assert "avg" in profiling.column_stats("NUMBER", "tinyint unsigned")


def test_read_profile_from_per_column_queries_keeps_failed_columns_bare() -> None:
    count, cols, missing = profiling.read_profile(COLUMNS, {
        "profile": {"error": "boom"},
        "profile:col:code": {"row": [4, 4, 1, "Y", "Y"]},
        "profile:col:amt": {"error": "type error"},
        "profile:col:photo": {"row": [4, 0, 0]},
        "profile:col:tags": {"row": [4, 2]},
    })
    assert count == 4 and missing == ["amt"]
    assert cols[0].unique_count == 1 and cols[0].total_count == 4
    assert cols[1].column_name == "amt" and cols[1].unique_count == 0 and cols[1].total_count == 4
    assert cols[3].null_count == 2
    # 아무 행도 얻지 못했으면 행 수 None
    assert profiling.read_profile(COLUMNS, {"profile": {"error": "x"}})[0] is None


# ---------------------------------------------------------------- C: 실제 워커 코드로

class _Fake:
    """Trino(``execute``) 대역 — SQL 에 따라 행 목록 또는 오류."""

    def __init__(self, respond):
        self.respond = respond
        self.sqls: list[str] = []

    async def __call__(self, endpoint, sql, *, on_poll=None, **_):
        self.sqls.append(sql)
        query_id = f"q{len(self.sqls)}"
        if on_poll is not None:
            await on_poll(query_id)
        rows = self.respond(sql)
        if isinstance(rows, Exception):
            raise rows
        return worker_trino.TrinoResult(columns=[], rows=rows, query_id=query_id)


def _profile_columns(sm, ds_id: int, columns: dict[str, tuple[str, str]]) -> None:
    async def go(s):
        for f in (await s.execute(select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == ds_id))).scalars():
            await s.delete(f)
        await s.flush()
        for i, (name, (ftype, native)) in enumerate(columns.items()):
            s.add(cm.DatasetSchema(dataset_id=ds_id, field_path=name, field_type=ftype, native_type=native, ordinal=i))
    _run(sm, go)


def _latest_profile(sm, ds_id: int) -> DataProfile | None:
    return _run(sm, lambda s: s.scalar(select(DataProfile).where(DataProfile.dataset_id == ds_id)
                                        .order_by(DataProfile.id.desc()).limit(1)))


def _row_count(sm, ds_id: int):
    return _run(sm, lambda s: s.scalar(select(cm.Dataset.row_count).where(cm.Dataset.id == ds_id)))


def test_trino_profile_run_saves_profile_and_row_count(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, []))  # 규칙 없이도 프로파일은 된다
    _profile_columns(sm, ds_id, {"use_yn": ("STRING", "char(1)"), "amt": ("NUMBER", "numeric")})

    def respond(sql: str):
        if "GROUP BY" in sql:
            assert '"use_yn"' in sql, "고유값이 많은 amt 는 최빈값을 구하지 않는다"
            return [["Y", 700], ["N", 290]]
        return [[1000, 990, 2, "N", "Y", 1000, 500, "0.00", "99.00", 12.5]]

    fake = _Fake(respond)
    monkeypatch.setattr(worker_trino, "execute", fake)
    client = TestClient(_app(sm))
    resp = client.post(f"/api/v1/quality/datasets/{ds_id}/runs", json={"kind": "PROFILE"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["kind"] == "PROFILE" and resp.json()["rules_total"] == 0
    run_id = resp.json()["id"]
    assert asyncio.run(_work(sm)) is True

    run = client.get(f"/api/v1/quality/runs/{run_id}").json()
    assert run["status"] == "SUCCEEDED" and run["error_message"] is None and run["kind"] == "PROFILE"
    assert run["query_ids"] == ["q1", "q2"] and run["score"] is None
    assert len(fake.sqls) == 2

    profile = client.get(f"/api/v1/quality/datasets/{ds_id}/profile").json()
    assert profile["row_count"] == 1000
    yn, amt = profile["columns"]
    assert (yn["null_count"], yn["unique_count"], yn["top_values"]) == (10, 2, [{"value": "Y", "count": 700},
                                                                                {"value": "N", "count": 290}])
    assert (amt["unique_percent"], amt["mean_value"], amt["top_values"]) == (50.0, 12.5, None)
    assert _row_count(sm, ds_id) == 1000

    queries = client.get(f"/api/v1/quality/runs/{run_id}/queries").json()
    assert [q["key"] for q in queries] == ["profile", "profile:top:use_yn", "profile:top:amt"]
    assert client.get(f"/api/v1/quality/runs/{run_id}/results").json() == []

    # DEF-164 — 샘플 보기 권한이 없으면 최솟값 · 최댓값 · 최빈값을 빼고 통계만, 추천도 값이 드러나는 후보는 뺀다
    assert profile["values_hidden"] is False and amt["min_value"] == "0.00"
    recs = {r["check_type"] for r in client.get(f"/api/v1/quality/datasets/{ds_id}/rules/recommendations").json()}
    assert "ACCEPTED_VALUES" in recs

    async def _restrict(sess):
        sess.add(Permission(kind="FEATURE", perm_key="datasets.sample-view", role_id="argus-superuser"))
        await sess.commit()
    _run(sm, _restrict)
    bob = TestClient(_app(sm, TokenUser(sub="9", username="bob", roles=["argus-user"])))
    hidden = bob.get(f"/api/v1/quality/datasets/{ds_id}/profile").json()
    assert hidden["values_hidden"] is True and hidden["row_count"] == 1000
    h_yn, h_amt = hidden["columns"]
    assert (h_yn["min_value"], h_yn["max_value"], h_yn["top_values"]) == (None, None, None)
    assert (h_amt["min_value"], h_amt["mean_value"], h_yn["null_count"]) == (None, 12.5, 10)
    h_recs = bob.get(f"/api/v1/quality/datasets/{ds_id}/rules/recommendations").json()
    assert "ACCEPTED_VALUES" not in {r["check_type"] for r in h_recs}
    assert all("0.00" not in r["reason"] for r in h_recs)


def test_profile_run_falls_back_per_column_and_reports_partial(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, []))
    _profile_columns(sm, ds_id, {"a": ("STRING", "varchar"), "doc": ("STRING", "json")})

    def respond(sql: str):
        if '"doc"' in sql:
            return worker_trino.TrinoQueryError("Type json is not orderable", error_type="USER_ERROR")
        if "GROUP BY" in sql:
            return [["x", 3]]
        return [[3, 3, 1, "x", "x"]]

    monkeypatch.setattr(worker_trino, "execute", _Fake(respond))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice", kind=runs.PROFILE_KIND))
    assert asyncio.run(_work(sm)) is True
    done = _run(sm, lambda s: runs.get_run(s, run.id))
    assert done.status == "PARTIAL" and "doc" in done.error_message
    saved = json.loads(_latest_profile(sm, ds_id).profile_json)
    assert [(c["column_name"], c["unique_count"], c["top_values"]) for c in saved] == [
        ("a", 1, [{"value": "x", "count": 3}]), ("doc", 0, None)]


def test_profile_run_fails_when_no_query_succeeds(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, []))
    monkeypatch.setattr(worker_trino, "execute", _Fake(
        lambda _sql: worker_trino.TrinoQueryError("Table not found", error_type="USER_ERROR")))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice", kind=runs.PROFILE_KIND))
    assert asyncio.run(_work(sm)) is True
    done = _run(sm, lambda s: runs.get_run(s, run.id))
    assert done.status == "FAILED" and "Table not found" in done.error_message
    assert done.error_message.startswith("Trino 쿼리를 실행하지 못했습니다")
    assert _latest_profile(sm, ds_id) is None, "실패하면 프로파일을 남기지 않는다"


def test_direct_profile_run_reads_source(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], connect=False, source_type="mysql", source_config={
        "host": "db.local", "port": 3306, "database": "sales", "username": "ro", "password": "pw"}))
    _profile_columns(sm, ds_id, {"use_yn": ("STRING", "char(1)")})
    calls: list[tuple[str, int]] = []

    async def fake_rows(source, sql, *, limit, **_):
        assert source["password"] == "pw" and source["type"] == "mysql"
        calls.append((sql, limit))
        return [["Y", 5]] if "GROUP BY" in sql else [[5, 5, 1, "Y", "Y"]]

    monkeypatch.setattr(worker_direct, "fetch_rows", fake_rows)
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice", kind=runs.PROFILE_KIND))
    assert run.engine == runs.DIRECT_ENGINE
    assert asyncio.run(_work(sm)) is True
    key = "CAST(CONVERT(`use_yn` USING utf8mb4) AS BINARY)"  # 문자열은 바이트로 센다 — Trino 와 같은 값
    assert calls[0] == (f"SELECT count(*), count(`use_yn`), count(DISTINCT {key}), CONVERT(min({key}) USING utf8mb4), "
                        f"CONVERT(max({key}) USING utf8mb4) FROM `public`.`orders`", 1)
    assert calls[1][1] == 1, "고유값 수만큼만 받는다"
    assert _run(sm, lambda s: runs.get_run(s, run.id)).status == "SUCCEEDED"
    assert _latest_profile(sm, ds_id).row_count == 5 and _row_count(sm, ds_id) == 5


def test_worker_json_value_converts_driver_values() -> None:
    import datetime as dt

    assert worker_direct.json_value(Decimal("15985")) == 15985 and isinstance(worker_direct.json_value(Decimal("3")), int)
    assert worker_direct.json_value(Decimal("0.00")) == "0.00", "decimal 의 자리는 Trino 처럼 지킨다"
    assert worker_direct.json_value(dt.datetime(2005, 5, 24, 22, 53, 30)) == "2005-05-24 22:53:30"
    assert worker_direct.json_value(dt.date(2005, 5, 24)) == "2005-05-24"
    assert worker_direct.json_value(None) is None and worker_direct.json_value("x") == "x"


# ---------------------------------------------------------------- D: 규칙 진단의 행 수 갱신

def test_check_run_updates_dataset_row_count(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    monkeypatch.setattr(worker_trino, "execute", _Fake(lambda _sql: [[1234, 0]]))
    _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    assert _row_count(sm, ds_id) == 1234


@pytest.mark.parametrize("truncated, expected", [(False, 77), (True, None)])
def test_direct_check_run_updates_row_count_unless_truncated(tmp_path, monkeypatch, truncated, expected) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    # 행을 읽는 규칙(정규식)만 있을 때 — 상한에 걸리면 전체 행 수를 모른다
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "REGEX", "column": "use_yn", "expected": "^Y"}], connect=False,
                                 source_type="mysql", source_config={"host": "db.local", "port": 3306}))

    async def fake_scan(source, sql, evaluator, *, max_rows, **_):
        for _ in range(77):
            evaluator.add(("Y",))
        return 77, truncated

    monkeypatch.setattr(worker_direct, "scan", fake_scan)
    _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    assert _row_count(sm, ds_id) == expected


def test_direct_aggregate_counts_rows_even_when_read_is_truncated(tmp_path, monkeypatch) -> None:
    # 원천 집계가 있으면 그 첫 값(count(*))이 전체 행 수다 — 행 읽기가 상한에 걸려도
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"},
                                      {"check_type": "REGEX", "column": "use_yn", "expected": "^Y"}], connect=False,
                                 source_type="mysql", source_config={"host": "db.local", "port": 3306}))

    async def fake_scan(source, sql, evaluator, *, max_rows, **_):
        for _ in range(10):
            evaluator.add(("Y",))
        return 10, True

    async def fake_table(source, sql, *, limit, **_):
        return [], [[9000, 0]]

    monkeypatch.setattr(worker_direct, "scan", fake_scan)
    monkeypatch.setattr(worker_direct, "fetch_table", fake_table)
    _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    assert _row_count(sm, ds_id) == 9000


# ---------------------------------------------------------------- E: 요청

def test_profile_request_rules(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, []))
    client = TestClient(_app(sm))
    assert client.post(f"/api/v1/quality/datasets/{ds_id}/runs", json={"kind": "NOPE"}).status_code == 422
    # 규칙이 없으면 규칙 진단은 409, 프로파일은 된다
    assert client.post(f"/api/v1/quality/datasets/{ds_id}/runs").status_code == 409
    assert client.post(f"/api/v1/quality/datasets/{ds_id}/runs", json={"kind": "PROFILE"}).status_code == 202
    second = client.post(f"/api/v1/quality/datasets/{ds_id}/runs", json={"kind": "PROFILE"})
    assert second.status_code == 409 and "이미" in second.json()["detail"]

    _profile_columns(sm, ds_id, {})
    _run(sm, lambda s: s.execute(QualityRun.__table__.delete()))
    no_schema = client.post(f"/api/v1/quality/datasets/{ds_id}/runs", json={"kind": "PROFILE"})
    assert no_schema.status_code == 409 and "스키마" in no_schema.json()["detail"]
