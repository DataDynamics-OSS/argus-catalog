# SPDX-License-Identifier: Apache-2.0
"""enrich·텔레메트리 클라이언트 — 재로그인(DEF-036)·타임아웃(DEF-038)·worker 처리 표시(DEF-037)·
셀프 등록 필수 항목(DEF-035)·SQL 검증(DEF-039)·컨텍스트 1회 수집(TODO-040).

가짜 카탈로그 API 를 임의 포트에 띄워 실제 HTTP 로 확인한다.

실행:
    cd ai-service
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_enrich_clients.py
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from argus_catalog_ai import cli, tasks
from argus_catalog_ai.catalog import CatalogClient
from argus_catalog_ai.telemetry import Telemetry
from argus_catalog_ai.tools import validate_sql


class FakeCatalog:
    """경로별 응답을 정해 두는 가짜 카탈로그. 요청 (메서드, 경로, 토큰, 본문)을 기록한다."""

    def __init__(self):
        self.requests: list[tuple] = []
        self.logins = 0
        self.valid_token = "t1"
        self.routes: dict[tuple[str, str], tuple[int, object]] = {}
        self.sleep = 0.0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _handle(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                outer.requests.append((method, self.path, token, body))
                if outer.sleep:
                    time.sleep(outer.sleep)
                if self.path == "/api/v1/auth/login":
                    outer.logins += 1
                    outer.valid_token = f"t{outer.logins}"
                    code, payload = 200, {"access_token": outer.valid_token}
                elif token != outer.valid_token:
                    code, payload = 401, {"detail": "expired"}
                else:
                    code, payload = outer.routes.get((method, self.path), (200, {}))
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._handle("GET")

            def do_POST(self):  # noqa: N802
                self._handle("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def paths(self, method=None):
        return [p for m, p, *_ in self.requests if method in (None, m)]


@pytest.fixture()
def fake():
    f = FakeCatalog()
    yield f
    f.server.shutdown()


# --------------------------------------------------------------- CatalogClient

def test_relogin_once_on_expired_token(fake):
    c = CatalogClient(fake.url, "u", "p")
    assert fake.logins == 1
    fake.valid_token = "rotated"            # 서버 쪽에서 토큰 만료
    fake.routes[("GET", "/api/v1/catalog/datasets/1")] = (200, {"id": 1})
    assert c.get_dataset(1) == {"id": 1}
    assert fake.logins == 2
    assert c.token == "t2"


def test_relogin_retries_only_once(fake, monkeypatch):
    c = CatalogClient(fake.url, "u", "p")
    # 다시 로그인해도 계속 401 이면 무한 반복하지 않고 오류
    fake.routes[("GET", "/api/v1/catalog/datasets/1")] = (401, {"detail": "no"})
    with pytest.raises(RuntimeError, match="401"):
        c.get_dataset(1)
    assert fake.logins == 2


def test_api_key_sent_without_login_or_relogin(fake):
    """API 키가 있으면 로그인하지 않고 키를 Bearer 로 보낸다. 401 이어도 다시 로그인하지 않는다(TODO-056)."""
    fake.valid_token = "argus_sk_abc"
    fake.routes[("GET", "/api/v1/catalog/datasets/1")] = (200, {"id": 1})
    c = CatalogClient(fake.url, api_key="argus_sk_abc")
    assert c.get_dataset(1) == {"id": 1}
    assert fake.logins == 0 and fake.requests[-1][2] == "argus_sk_abc"
    fake.valid_token = "other"               # 폐기된 키
    with pytest.raises(RuntimeError, match="401"):
        c.get_dataset(1)
    assert fake.logins == 0


def test_request_timeout(fake):
    c = CatalogClient(fake.url, "u", "p", timeout=0.3)
    fake.sleep = 1.0
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="연결 실패"):
        c.get_dataset(1)
    assert time.monotonic() - started < 0.9


# --------------------------------------------------------------- worker

class _Stub:
    def __init__(self, pending):
        self.pending = pending
        self.fetched = []

    def list_datasets_by_datasource(self, _ds):
        return [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 3, "name": "c"}]

    def list_pending_suggestions(self, dataset_id):
        if dataset_id == 3:
            raise RuntimeError("조회 실패")
        return self.pending.get(dataset_id, [])

    def get_dataset(self, dataset_id):
        self.fetched.append(dataset_id)
        return {"id": dataset_id, "name": str(dataset_id)}


def test_worker_skips_pending_and_retries_total_failure(monkeypatch):
    stub = _Stub(pending={1: [{"id": 99}]})
    results = {2: {"describe": "실패: x", "tags": "실패: y"}, 3: {"describe": "제안 1건"}}
    runs = []

    def fake_run(_c, _l, _cfg, ds, _names):
        runs.append(ds["id"])
        return results[ds["id"]]

    monkeypatch.setattr(cli, "run_for_dataset", fake_run)
    sleeps = []

    def fake_sleep(_s):
        sleeps.append(_s)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    cfg = SimpleNamespace(poll_interval=1, worker_target="missing")
    with pytest.raises(KeyboardInterrupt):
        cli._worker_loop(stub, None, cfg, SimpleNamespace(datasource_id="ds", once=False))
    # 1: 미적용 제안이 있어 건너뜀, 2: 모두 실패 → 다음 주기 재시도, 3: 제안 조회 실패여도 진행·성공 → 한 번만
    assert runs == [2, 3, 2]


# --------------------------------------------------------------- enrich 컨텍스트

def test_run_for_dataset_gathers_context_once(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "_gather_context", lambda c, d: calls.append(d["id"]) or ({"rows": []}, []))
    seen = []
    monkeypatch.setattr(tasks, "TASKS", {n: (lambda *a, context=None, _n=n: seen.append((_n, context)) or "ok")
                                         for n in tasks.TASK_NAMES})
    out = tasks.run_for_dataset(None, None, None, {"id": 5, "name": "x"}, list(tasks.TASK_NAMES))
    assert calls == [5]
    assert all(ctx == ({"rows": []}, []) for _, ctx in seen)
    assert set(out) == set(tasks.TASK_NAMES)


# --------------------------------------------------------------- 텔레메트리

def _telemetry(fake, **kw):
    return Telemetry(fake.url, "assistant-x", "m", "http://localhost:11434/v1", **kw)


def test_registration_includes_owner_and_department(fake):
    fake.valid_token = "user"
    t = _telemetry(fake, owner_email="ops@example.com", department="데이터팀")
    t.record("user", tool_names=["a"], status="success", latency_ms=1, tokens_in=1, tokens_out=1)
    reg = next(b for m, p, _t, b in fake.requests if p == "/api/v1/ai-agents")
    assert reg["owner_email"] == "ops@example.com" and reg["department"] == "데이터팀"
    assert reg["category"]
    assert "/api/v1/ai-agents/assistant-x/invocations" in fake.paths("POST")


def test_no_owner_skips_registration_and_404_disables(fake):
    fake.valid_token = "user"
    fake.routes[("POST", "/api/v1/ai-agents/assistant-x/invocations")] = (404, {"detail": "없음"})
    t = _telemetry(fake)
    for _ in range(3):
        t.record("user", tool_names=[], status="success", latency_ms=1, tokens_in=0, tokens_out=0)
    assert "/api/v1/ai-agents" not in fake.paths("POST")
    # 첫 404 뒤로는 적재를 시도하지 않는다
    assert fake.paths("POST").count("/api/v1/ai-agents/assistant-x/invocations") == 1
    assert t.enabled is False


def test_preregistered_agent_gets_invocations_without_owner(fake):
    fake.valid_token = "user"
    t = _telemetry(fake)
    for _ in range(2):
        t.record("user", tool_names=[], status="success", latency_ms=1, tokens_in=0, tokens_out=0)
    assert fake.paths("POST").count("/api/v1/ai-agents/assistant-x/invocations") == 2
    assert t.enabled is True


# --------------------------------------------------------------- validate_sql

@pytest.mark.parametrize("sql", [
    "SELECT * FROM t WHERE status = 'delete'",
    "select 'a;b' from t",
    'select "update" from t',
    "select `drop` from t",
    "select * from t where x = 'it''s; drop table t'",
    "/* note */ WITH a AS (SELECT 1) SELECT * FROM a;",
    "select created_at, updated_by from t -- delete later",
])
def test_validate_sql_accepts_reads(sql):
    assert validate_sql(None, sql)["valid"] is True


@pytest.mark.parametrize("sql", [
    "select 1; drop table t",
    "delete from t",
    "with x as (delete from t returning *) select * from x",
    "",
    "-- only comment",
    "select 1 /* ; */ ; update t set a = 1",
])
def test_validate_sql_rejects_writes(sql):
    assert validate_sql(None, sql)["valid"] is False
