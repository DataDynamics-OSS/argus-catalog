# SPDX-License-Identifier: Apache-2.0
"""플러그인 로더 단위 테스트 (외부 의존성 없음).

entry point 발견을 가짜로 바꿔 `app.plugins.load_plugin_routers` 와 `app.main` 의
플러그인 등록 시점을 검증한다.

실행:
    cd backend
    pytest tests/test_plugins.py
"""

from __future__ import annotations

import types
from unittest import mock

import pytest
from fastapi import APIRouter, FastAPI

from app import plugins
from app.core import config


def _ep(name: str, *, load_error: Exception | None = None, **attrs):
    """`name` 경로 하나를 가진 라우터를 노출하는 가짜 entry point."""
    router = APIRouter()
    router.add_api_route(f"/{name}", lambda: {"ok": name})
    ep = mock.Mock()
    ep.name = name
    if load_error is not None:
        ep.load.side_effect = load_error
    else:
        ep.load.return_value = types.SimpleNamespace(router=router, **attrs)
    return ep


def _paths(app: FastAPI) -> list[str]:
    return sorted(r.path for r in app.routes if r.path.rsplit("/", 1)[-1] in {"a", "b", "c", "d"})


def _load(eps, enabled=None) -> tuple[FastAPI, list[str]]:
    app = FastAPI()
    with mock.patch.object(plugins, "entry_points", return_value=eps):
        loaded = plugins.load_plugin_routers(app, enabled=enabled)
    return app, loaded


# ---------------------------------------------------------------------------
# load_plugin_routers
# ---------------------------------------------------------------------------


def test_prefix_falls_back_to_default_for_missing_none_or_empty():
    eps = [_ep("a"), _ep("b", prefix=None), _ep("c", prefix=""), _ep("d", prefix="/custom")]
    app, loaded = _load(eps, enabled=["*"])
    assert loaded == ["a", "b", "c", "d"]
    assert _paths(app) == ["/api/v1/a", "/api/v1/b", "/api/v1/c", "/custom/d"]


def test_enabled_filters_plugins():
    app, loaded = _load([_ep("a"), _ep("b")], enabled=["b"])
    assert loaded == ["b"]
    assert _paths(app) == ["/api/v1/b"]


@pytest.mark.parametrize("enabled", [None, []])
def test_empty_enabled_loads_nothing(enabled):
    """기본(미설정/빈 목록)은 설치돼 있어도 아무 것도 로드하지 않는다(명시 허용)."""
    app, loaded = _load([_ep("a"), _ep("b")], enabled=enabled)
    assert loaded == []
    assert _paths(app) == []


def test_wildcard_loads_all():
    _, loaded = _load([_ep("a"), _ep("b")], enabled=["*"])
    assert loaded == ["a", "b"]


def test_enabled_but_not_installed_is_warned(caplog):
    with caplog.at_level("WARNING", logger="app.plugins"):
        _, loaded = _load([_ep("a")], enabled=["a", "typo"])
    assert loaded == ["a"]
    assert "typo" in caplog.text


def test_failures_are_isolated():
    no_router = mock.Mock()
    no_router.name = "c"
    no_router.load.return_value = types.SimpleNamespace()
    app, loaded = _load([_ep("a", load_error=ImportError("boom")), no_router, _ep("b")], enabled=["*"])
    assert loaded == ["b"]
    assert _paths(app) == ["/api/v1/b"]


# ---------------------------------------------------------------------------
# app.main — 플러그인은 import 시점이 아니라 lifespan 에서, 그 시점 설정으로 등록
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_settings(monkeypatch):
    """init_settings 가 바꾸는 전역 설정을 테스트 후 원래대로 돌린다."""
    for name in ("_raw", "_yaml_path", "_properties_path"):
        monkeypatch.setattr(config, name, getattr(config, name))
    snapshot = dict(config.settings.__dict__)
    yield
    config.settings.__dict__.clear()
    config.settings.__dict__.update(snapshot)


def test_cli_config_plugins_enabled_is_respected(tmp_path, restore_settings):
    """--config-yaml 로 넘긴 plugins.enabled 가 플러그인 등록에 반영된다(회귀 테스트)."""
    from app import main

    yaml_path = tmp_path / "config.yml"
    yaml_path.write_text("plugins:\n  enabled: b\n", encoding="utf-8")
    props_path = tmp_path / "config.properties"
    props_path.write_text("", encoding="utf-8")
    # run() 이 --config-yaml 을 받았을 때와 같은 호출 — app.main import 이후에 일어난다.
    config.init_settings(yaml_path=str(yaml_path), properties_path=str(props_path))
    assert config.settings.enabled_plugins == ["b"]

    app = FastAPI()
    with mock.patch.object(plugins, "entry_points", return_value=[_ep("a"), _ep("b")]):
        main._load_plugins_once(app)
    assert _paths(app) == ["/api/v1/b"]


def test_load_plugins_once_is_idempotent(monkeypatch):
    from app import main

    monkeypatch.setattr(config.settings, "enabled_plugins", ["*"])
    app = FastAPI()
    with mock.patch.object(plugins, "entry_points", return_value=[_ep("a")]):
        main._load_plugins_once(app)
        main._load_plugins_once(app)
    assert _paths(app) == ["/api/v1/a"]


async def test_lifespan_registers_plugins_before_startup_work(monkeypatch):
    """lifespan 이 시작되면 DB 초기화 같은 다른 기동 작업보다 먼저 플러그인을 등록한다."""
    from app import main

    class _StopStartupError(Exception):
        pass

    async def _stop():
        raise _StopStartupError

    monkeypatch.setattr(config.settings, "enabled_plugins", ["*"])
    monkeypatch.setattr(main, "init_database", _stop)
    app = FastAPI()
    with mock.patch.object(plugins, "entry_points", return_value=[_ep("a")]):
        with pytest.raises(_StopStartupError):
            async with main.lifespan(app):
                pass
    assert _paths(app) == ["/api/v1/a"]
