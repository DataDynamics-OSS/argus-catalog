# SPDX-License-Identifier: Apache-2.0
"""명령줄 도구 ``agent-apply``·``agent-export`` 종단 테스트.

SDK(``sdk/src``)의 명령줄 진입점(``argus_catalog_sdk.cli.main``)을 그대로 실행하고, SDK 가 보내는 HTTP 요청
(``httpx.Client``)을 테스트용 서버(in-memory SQLite + 에이전트 라우터)로 넘긴다.
설계: design/ai-agent-bulk-registration-design.md §4.6

실행:
    cd backend
    pytest tests/test_agent_cli.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "src"))

from argus_catalog_sdk import cli as sdk_cli  # noqa: E402
from argus_catalog_sdk import client as sdk_client  # noqa: E402

from tests.test_agent_manifest import ALICE, Api, _make_factory, agent_doc, to_yaml  # noqa: E402

SOURCE = "git@git.corp.example:ai/agents.git"


@pytest.fixture
async def cli(monkeypatch):
    """``run(*argv)`` → (종료 코드, 표준 출력). 명령줄 도구는 별도 스레드에서 돌고 요청은 이 루프의 서버로 간다."""
    api = Api(await _make_factory())
    loop = asyncio.get_running_loop()

    def bridge(request: httpx.Request) -> httpx.Response:
        # 명령줄 도구 스레드의 동기 요청 → 이 루프의 비동기 테스트 서버
        resp = asyncio.run_coroutine_threadsafe(api.client.request(
            request.method, str(request.url), content=request.read(),
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
        ), loop).result()
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding")}
        return httpx.Response(resp.status_code, headers=headers, content=resp.content)

    real_client = httpx.Client
    monkeypatch.setattr(sdk_client.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(bridge), **kw))
    monkeypatch.setattr(sdk_cli.console, "width", 200, raising=False)

    async def run(*argv: str, capsys=None) -> tuple[int, str]:
        def _main():
            try:
                sdk_cli.main([argv[0], "--server", "http://test", "--token", "t", *argv[1:]])
            except SystemExit as e:
                return int(e.code or 0)
            return 0

        code = await asyncio.to_thread(_main)
        return code, (capsys.readouterr().out if capsys else "")

    run.api = api
    yield run
    await api.client.aclose()


def write(path: Path, *docs: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_yaml(*docs), encoding="utf-8")
    return path


async def detail(api: Api, name: str) -> dict:
    r = await api.get(f"/{name}")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# agent-apply
# ---------------------------------------------------------------------------


async def test_apply_directory_with_prompt_file_and_source_ref(cli, tmp_path, capsys):
    repo = tmp_path / "repo"
    write(repo / "cs" / "refund.yaml", agent_doc("cs.refund", release={"systemPromptFile": "prompts/refund.md"}))
    (repo / "cs" / "prompts").mkdir()
    (repo / "cs" / "prompts" / "refund.md").write_text("너는 환불 상담원이다.\n", encoding="utf-8")
    write(repo / "sales" / "quote.yml", agent_doc("sales.quote"), agent_doc("sales.discount"))
    (repo / "README.md").write_text("정의 파일 아님", encoding="utf-8")

    code, out = await cli("agent-apply", "-f", str(repo), "--source", SOURCE, capsys=capsys)
    assert code == 0, out
    assert "생성 3" in out

    d = await detail(cli.api, "cs.refund")
    assert d["managed_by"] == "manifest"
    # --source 가 있으면 경로는 git 최상위(없으면 현재 위치) 기준 — 여기서는 tmp 는 git 이 아니므로 현재 위치 기준
    assert d["source_ref"].startswith(SOURCE + "#") and d["source_ref"].endswith("cs/refund.yaml")
    assert (await detail(cli.api, "sales.discount"))["source_ref"].endswith("sales/quote.yml")
    versions = (await cli.api.get("/cs.refund/versions")).json()
    prompts = [v.get("system_prompt") for v in (versions if isinstance(versions, list) else versions.get("items", []))]
    assert "너는 환불 상담원이다.\n" in prompts

    # 같은 파일을 다시 반영하면 모두 '변경 없음'
    code, out = await cli("agent-apply", "-f", str(repo), "--source", SOURCE, capsys=capsys)
    assert code == 0
    assert "생성 0 · 갱신 0 · 변경 없음 3" in out


async def test_dry_run_changes_nothing(cli, tmp_path, capsys):
    f = write(tmp_path / "a.yaml", agent_doc("cs.refund"))
    code, out = await cli("agent-apply", "-f", str(f), "--dry-run", "--source", SOURCE, capsys=capsys)
    assert code == 0
    assert "반영 미리 보기" in out and "생성 1" in out
    assert (await cli.api.get("/cs.refund")).status_code == 404


async def test_default_source_is_file_path_relative_to_cwd(cli, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "agents" / "x" / "a.yaml", agent_doc("cs.refund"))
    code, _ = await cli("agent-apply", "-f", "agents", capsys=capsys)
    assert code == 0
    ref = (await detail(cli.api, "cs.refund"))["source_ref"]
    assert ref == f"file:{tmp_path.resolve().as_posix()}#agents/x/a.yaml"


async def test_prune_deprecates_removed_file_only_in_scope(cli, tmp_path, capsys):
    repo = tmp_path / "repo"
    write(repo / "agents" / "a.yaml", agent_doc("cs.keep"))
    old = write(repo / "agents" / "b.yaml", agent_doc("cs.removed"))
    write(repo / "other" / "c.yaml", agent_doc("other.agent"))
    for p in ("agents", "other"):
        code, _ = await cli("agent-apply", "-f", str(repo / p), "--source", SOURCE, capsys=capsys)
        assert code == 0

    old.unlink()
    # 미리 보기 — 대상만 보여 주고 바꾸지 않음
    code, out = await cli("agent-apply", "-f", str(repo / "agents"), "--source", SOURCE, "--prune", "--dry-run",
                          capsys=capsys)
    assert code == 0
    assert "cs.removed" in out and "사용 중단" in out
    assert (await detail(cli.api, "cs.removed"))["status"] == "active"

    code, out = await cli("agent-apply", "-f", str(repo / "agents"), "--source", SOURCE, "--prune", capsys=capsys)
    assert code == 0, out
    assert (await detail(cli.api, "cs.removed"))["status"] == "deprecated"
    assert (await detail(cli.api, "cs.keep"))["status"] == "active"
    # 다른 디렉터리(other/)에서 관리되는 에이전트는 범위 밖
    assert (await detail(cli.api, "other.agent"))["status"] == "active"


async def test_prune_skipped_when_a_file_is_broken(cli, tmp_path, capsys):
    repo = tmp_path / "repo"
    write(repo / "a.yaml", agent_doc("cs.keep"))
    old = write(repo / "b.yaml", agent_doc("cs.removed"))
    assert (await cli("agent-apply", "-f", str(repo), "--source", SOURCE, capsys=capsys))[0] == 0

    old.write_text("metadata: [깨진", encoding="utf-8")
    code, out = await cli("agent-apply", "-f", str(repo), "--source", SOURCE, "--prune", capsys=capsys)
    assert code == 1
    assert "파일 오류" in out and "정리 건너뜀" in out
    assert (await detail(cli.api, "cs.removed"))["status"] == "active"


async def test_failures_exit_nonzero(cli, tmp_path, capsys):
    f = write(tmp_path / "a.yaml", agent_doc("cs.ok"), agent_doc("Bad Name!"))
    code, out = await cli("agent-apply", "-f", str(f), "--source", SOURCE, capsys=capsys)
    assert code == 1
    assert "생성 1" in out and "실패 1" in out

    both = write(tmp_path / "b.yaml", agent_doc("cs.both", release={"systemPrompt": "a", "systemPromptFile": "x.md"}))
    code, out = await cli("agent-apply", "-f", str(both), "--source", SOURCE, capsys=capsys)
    assert code == 1 and "함께 쓸 수 없습니다" in out

    code, out = await cli("agent-apply", "-f", str(tmp_path / "없는경로"), capsys=capsys)
    assert code == 1 and "경로가 없습니다" in out


async def test_auth_error_exits_nonzero(cli, tmp_path, capsys):
    f = write(tmp_path / "a.yaml", agent_doc("cs.refund"))
    cli.api.as_(None)
    code, out = await cli("agent-apply", "-f", str(f), "--source", SOURCE, capsys=capsys)
    assert code == 1 and "401" in out


async def test_owner_permission_respected(cli, tmp_path, capsys):
    f = write(tmp_path / "a.yaml", agent_doc("cs.refund"))
    assert (await cli("agent-apply", "-f", str(f), "--source", SOURCE, capsys=capsys))[0] == 0
    cli.api.as_(ALICE)  # 소유자 — 갱신 가능
    write(f, agent_doc("cs.refund", description="바뀐 설명"))
    code, out = await cli("agent-apply", "-f", str(f), "--source", SOURCE, capsys=capsys)
    assert code == 0 and "갱신 1" in out


# ---------------------------------------------------------------------------
# agent-export
# ---------------------------------------------------------------------------


async def test_export_then_apply_is_unchanged(cli, tmp_path, capsys):
    # 화면에서 만든 에이전트를 내보내 저장소에 두고 반영 → 그다음부터는 같은 파일 반영이 '변경 없음'
    src = write(tmp_path / "src.yaml", agent_doc("cs.refund"), agent_doc("sales.quote"))
    assert (await cli("agent-apply", "-f", str(src), "--source", SOURCE, capsys=capsys))[0] == 0

    out_dir = tmp_path / "exported"
    code, _ = await cli("agent-export", "cs.refund", "sales.quote", "-o", str(out_dir), capsys=capsys)
    assert code == 0
    assert sorted(p.name for p in out_dir.iterdir()) == ["cs.refund.yaml", "sales.quote.yaml"]

    # 내용은 같고 정의 파일 위치만 바뀐다
    code, out = await cli("agent-apply", "-f", str(out_dir), "--source", SOURCE, "-v", capsys=capsys)
    assert code == 0
    assert "생성 0 · 갱신 2 · 변경 없음 0" in out, out
    assert (await detail(cli.api, "cs.refund"))["source_ref"].endswith("exported/cs.refund.yaml")

    # 다시 내보내 덮어쓰고 반영 → 변경 없음
    assert (await cli("agent-export", "cs.refund", "sales.quote", "-o", str(out_dir), capsys=capsys))[0] == 0
    code, out = await cli("agent-apply", "-f", str(out_dir), "--source", SOURCE, capsys=capsys)
    assert code == 0
    assert "생성 0 · 갱신 0 · 변경 없음 2" in out, out


async def test_path_outside_cwd_uses_absolute_path(cli, tmp_path, capsys, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    f = write(tmp_path / "elsewhere" / "a.yaml", agent_doc("cs.refund"))
    assert (await cli("agent-apply", "-f", str(f), capsys=capsys))[0] == 0
    ref = (await detail(cli.api, "cs.refund"))["source_ref"]
    assert ref == f"file:/#{f.resolve().as_posix().lstrip('/')}"
    assert ".." not in ref


async def test_export_stdout_by_filter(cli, tmp_path, capsys):
    other = agent_doc("ops.monitor")
    other["metadata"]["department"] = "운영팀"
    src = write(tmp_path / "src.yaml", agent_doc("cs.refund"), agent_doc("cs.faq"), other)
    assert (await cli("agent-apply", "-f", str(src), "--source", SOURCE, capsys=capsys))[0] == 0

    code, out = await cli("agent-export", "--department", "고객지원팀", capsys=capsys)
    assert code == 0
    docs = [d for d in yaml.safe_load_all(out) if d]
    assert sorted(d["metadata"]["name"] for d in docs) == ["cs.faq", "cs.refund"]

    code, out = await cli("agent-export", "no-such.agent", capsys=capsys)
    assert code == 1 and "404" in out

    # 서버 이름 규칙에 맞지 않는 이름은 요청 전에 거부(경로를 벗어나는 이름이 저장 위치를 벗어나지 않게)
    code, out = await cli("agent-export", "../escape", capsys=capsys)
    assert code == 1 and "이름이 올바르지 않습니다" in out


def test_bridge_uses_httpx_client():
    # SDK 가 요청을 직접 만든 ``httpx.Client`` 로 보낸다는 전제(위 테스트의 연결 방식)를 고정
    assert sdk_client.httpx is httpx
    c = sdk_client.ModelClient("http://test", token="t")
    assert type(c._http) is httpx.Client
    c.close()
