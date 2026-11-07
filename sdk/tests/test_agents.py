# SPDX-License-Identifier: Apache-2.0
"""``agents`` — 정의 파일 수집 규칙, 정의 파일 위치(source_ref), 내보내기 이름 검사."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus_catalog_sdk import agents
from argus_catalog_sdk.agents import ManifestFileError, collect_files


def touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x: 1\n", encoding="utf-8")


def test_collect_skips_hidden_paths_under_directory(tmp_path):
    touch(tmp_path, "a.yaml", "sub/b.YML", "c.txt", ".github/workflows/ci.yml", "sub/.draft.yaml", ".hidden.yaml")
    got = [f.relative_to(tmp_path).as_posix() for f in collect_files([tmp_path])]
    assert got == ["a.yaml", "sub/b.YML"]


def test_collect_reads_hidden_file_given_directly(tmp_path):
    touch(tmp_path, ".hidden.yaml")
    assert collect_files([tmp_path / ".hidden.yaml"]) == [tmp_path / ".hidden.yaml"]


def test_collect_inside_hidden_dir_given_as_root(tmp_path):
    """숨김 판정은 준 디렉터리 기준 상대 경로라, 숨김 디렉터리를 직접 주면 그 안은 읽는다."""
    touch(tmp_path, ".agents/a.yaml")
    assert [f.name for f in collect_files([tmp_path / ".agents"])] == ["a.yaml"]


def test_collect_dedupes_and_rejects_missing(tmp_path):
    touch(tmp_path, "a.yaml")
    assert collect_files([tmp_path, tmp_path / "a.yaml"]) == [tmp_path / "a.yaml"]
    with pytest.raises(ManifestFileError):
        collect_files([tmp_path / "nope"])


# ---------------------------------------------------------------------------
# 정의 파일 위치(source_ref) · 정리 범위
# ---------------------------------------------------------------------------



@pytest.mark.parametrize("url,expected", [
    ("https://x-access-token:ghs_SECRET@github.com/org/repo.git", "https://github.com/org/repo.git"),
    ("https://ghp_TOKEN@github.com/org/repo", "https://github.com/org/repo"),
    ("ssh://git@gitlab.corp:2222/team/agents.git", "ssh://gitlab.corp:2222/team/agents.git"),
    ("git@github.com:org/repo.git", "git@github.com:org/repo.git"),      # scp 형식 — 비밀 없음
    ("https://github.com/org/repo", "https://github.com/org/repo"),
    ("my-agents", "my-agents"),
])
def test_strip_credentials(url, expected):
    assert agents.strip_credentials(url) == expected


def _fake_git(top: Path, origin: str | None):
    def run(cwd, *args):
        if args[:1] == ("rev-parse",):
            return str(top)
        if args[:2] == ("remote", "get-url"):
            return origin
        return None
    return run


def test_source_ref_from_origin_drops_token(tmp_path, monkeypatch):
    touch(tmp_path, "agents/a.yaml")
    monkeypatch.setattr(agents, "_git", _fake_git(tmp_path, "https://x-access-token:SECRET@github.com/org/repo.git"))
    r = agents.SourceResolver()
    assert r.ref(tmp_path / "agents/a.yaml") == "https://github.com/org/repo.git#agents/a.yaml"
    assert r.prefix(tmp_path / "agents") == "https://github.com/org/repo.git#agents/"
    assert r.prefix(tmp_path / "agents/a.yaml") == "https://github.com/org/repo.git#agents/a.yaml"


def test_source_ref_explicit_source_wins(tmp_path, monkeypatch):
    touch(tmp_path, "a.yaml")
    monkeypatch.setattr(agents, "_git", _fake_git(tmp_path, "https://github.com/org/repo"))
    assert agents.SourceResolver("my-repo").ref(tmp_path / "a.yaml") == "my-repo#a.yaml"


def test_source_ref_outside_git_uses_file_root(tmp_path, monkeypatch):
    touch(tmp_path, "a.yaml")
    monkeypatch.setattr(agents, "_git", lambda cwd, *a: None)
    monkeypatch.chdir(tmp_path)
    assert agents.SourceResolver().ref(tmp_path / "a.yaml") == f"file:{tmp_path.resolve().as_posix()}#a.yaml"


class _Rec:
    def __init__(self):
        self.calls = []

    def export_agent(self, name):
        self.calls.append(name)
        return f"name: {name}\n"


@pytest.mark.parametrize("bad", ["../x", "a/b", "Upper", ""])
def test_export_rejects_bad_names_before_any_request(tmp_path, bad):
    rec = _Rec()
    with pytest.raises(ValueError, match="이름이 올바르지 않습니다"):
        agents.export_agents(rec, ["ok.agent", bad], tmp_path / "out")
    assert rec.calls == []
    assert not (tmp_path / "out").exists()


def test_export_writes_files(tmp_path):
    rec = _Rec()
    texts = agents.export_agents(rec, ["cs.refund-helper"], tmp_path / "out")
    assert texts == [("cs.refund-helper", "name: cs.refund-helper\n")]
    assert (tmp_path / "out/cs.refund-helper.yaml").read_text(encoding="utf-8") == "name: cs.refund-helper\n"
