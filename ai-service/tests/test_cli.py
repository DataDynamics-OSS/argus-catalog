# SPDX-License-Identifier: Apache-2.0
"""CLI — 명령 구성과 0.7.0 에서 제거한 이전 이름 (TODO-044).

실행:
    cd ai-service
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_cli.py
"""

from __future__ import annotations

import pytest

from argus_catalog_ai import cli
from argus_catalog_ai.config import load_config


@pytest.mark.parametrize("old,new", sorted(cli.REMOVED_COMMANDS.items()))
def test_removed_commands_point_to_new_ones(old, new, capsys):
    assert cli.main([old, "--urn", "x"]) == 2
    err = capsys.readouterr().err
    assert f"argus-catalog-ai {new}" in err and "0.7.0" in err


def test_parser_has_only_new_commands():
    parser = cli.build_parser()
    assert cli._normalize(parser.parse_args(["assistant"])) == ("assistant", None)
    assert cli._normalize(parser.parse_args(["enrich", "all", "--datasource-id", "d"])) == ("enrich", "all")
    assert cli._normalize(parser.parse_args(["enrich", "worker", "--once"])) == ("enrich", "worker")
    with pytest.raises(SystemExit):
        parser.parse_args(["generate-all"])


def test_legacy_env_names_are_ignored(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "old-model")
    monkeypatch.setenv("AGENT_TELEMETRY", "off")
    monkeypatch.delenv("ARGUS_AI_MODEL", raising=False)
    monkeypatch.delenv("ARGUS_AI_TELEMETRY", raising=False)
    cfg = load_config(cli.build_parser().parse_args(["assistant"]))
    assert cfg.model == "qwen2.5:7b"
    assert cfg.telemetry_enabled is True


def test_new_env_names_are_read(monkeypatch):
    monkeypatch.setenv("ARGUS_AI_MODEL", "m2")
    monkeypatch.setenv("ARGUS_AI_TELEMETRY", "off")
    cfg = load_config(cli.build_parser().parse_args(["assistant"]))
    assert cfg.model == "m2" and cfg.telemetry_enabled is False
