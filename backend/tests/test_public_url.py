# SPDX-License-Identifier: Apache-2.0
"""외부 공개 주소(설정 › AI-Ready) — DCAT · Croissant · 데이터 카드에 넣는 이 서버 주소.

- 요청 주소: 프록시가 넘긴 X-Forwarded-Host · Proto 를 먼저(웹 서버 프록시 뒤에서 localhost:4600 이 나오지 않게)
- FQDN · 바인딩 IP: 스킴 · 포트(비우면 백엔드 포트), 기본 포트 생략, IPv6 괄호
- 직접 입력: 그 URL, 설정 검증(직접 입력이면 URL 필수, http(s) 만)
- 내보내기 API 가 설정을 따름

실행:
    cd backend
    pytest tests/test_public_url.py
"""

from __future__ import annotations

import pytest

from app.catalog import public_url
from app.settings.models import CatalogConfiguration
from tests.test_ai_ready_check import V1, env  # noqa: F401 — env 는 픽스처


class _Req:
    def __init__(self, headers: dict, base: str = "http://localhost:4600/"):
        from starlette.datastructures import URL
        self.headers = headers
        self.base_url = URL(base)
        self.url = URL(base)


def test_request_base_prefers_forwarded_headers() -> None:
    assert public_url.request_base(_Req({})) == "http://localhost:4600/"
    assert public_url.request_base(_Req({"x-forwarded-host": "192.0.2.50:3000", "x-forwarded-proto": "http"})) \
        == "http://192.0.2.50:3000/"
    assert public_url.request_base(_Req({"x-forwarded-host": "catalog.go.kr, inner:3000",
                                         "x-forwarded-proto": "https, http"})) == "https://catalog.go.kr/"
    assert public_url.request_base(_Req({"x-forwarded-host": "h:1", "x-forwarded-proto": "gopher"})) == "http://h:1/"


def test_compose_and_resolve(monkeypatch) -> None:
    monkeypatch.setattr(public_url.settings, "port", 4600)
    assert public_url.compose("192.0.2.50") == "http://192.0.2.50:4600/"
    assert public_url.compose("h.example", "https", 443) == "https://h.example/"
    assert public_url.compose("h.example", "http", 80) == "http://h.example/"
    assert public_url.compose("fe80::1", "http", 3000) == "http://[fe80::1]:3000/"

    monkeypatch.setattr(public_url, "fqdn", lambda: "argus01.corp.local")
    monkeypatch.setattr(public_url, "binding_ip", lambda: "192.0.2.50")
    req = _Req({"x-forwarded-host": "192.0.2.50:3000"})
    resolve = public_url.resolve
    assert resolve({}, req) == "http://192.0.2.50:3000/", "기본은 요청 주소"
    assert resolve({"ai_ready_public_mode": "FQDN"}, req) == "http://argus01.corp.local:4600/"
    assert resolve({"ai_ready_public_mode": "IP", "ai_ready_public_port": "3000"}, req) == "http://192.0.2.50:3000/"
    assert resolve({"ai_ready_public_mode": "IP", "ai_ready_public_scheme": "https", "ai_ready_public_port": "443"},
                   req) == "https://192.0.2.50/"
    assert resolve({"ai_ready_public_mode": "CUSTOM", "ai_ready_public_url": "https://catalog.go.kr/argus"},
                   req) == "https://catalog.go.kr/argus/"
    assert resolve({"ai_ready_public_mode": "CUSTOM"}, req) == "http://192.0.2.50:3000/", "URL 이 없으면 요청 주소"
    assert resolve({"ai_ready_public_port": "x"}, None) == "http://192.0.2.50:4600/", "요청이 없으면 바인딩 IP"


def test_binding_ip(monkeypatch) -> None:
    monkeypatch.setattr(public_url.settings, "host", "192.168.0.7")
    assert public_url.binding_ip() == "192.168.0.7"
    monkeypatch.setattr(public_url.settings, "host", "0.0.0.0")
    ip = public_url.binding_ip()
    assert ip and ip != "0.0.0.0"


def test_settings_validation() -> None:
    from app.settings.router import AiReadyConfig

    assert AiReadyConfig(public_mode="CUSTOM", public_url="https://catalog.go.kr/").public_url == "https://catalog.go.kr"
    with pytest.raises(ValueError, match="URL 을 넣어야"):
        AiReadyConfig(public_mode="CUSTOM", public_url="")
    # 주소를 보내지 않은 부분 갱신은 저장된 값과 합친 뒤 검사한다(DEF-143)
    AiReadyConfig(public_mode="CUSTOM")
    with pytest.raises(ValueError):
        AiReadyConfig(public_mode="CUSTOM", public_url="ftp://x")
    with pytest.raises(ValueError):
        AiReadyConfig(public_mode="HOST")
    with pytest.raises(ValueError):
        AiReadyConfig(public_mode="IP", public_port=70000)


def test_exports_follow_setting(env) -> None:  # noqa: F811 — 픽스처
    c, _who, ids, run = env
    path = f"{V1}/external/datasets/{ids['bare']}"
    card = c.get(f"{path}/data-card", headers={"X-Forwarded-Host": "192.0.2.50:3000"}).text
    assert f"http://192.0.2.50:3000/api/v1/external/datasets/{ids['bare']}/dcat" in card
    assert "localhost" not in card and "testserver" not in card

    async def _custom(db):
        db.add_all([CatalogConfiguration(category="ai_ready", config_key="ai_ready_public_mode", config_value="CUSTOM"),
                    CatalogConfiguration(category="ai_ready", config_key="ai_ready_public_url",
                                         config_value="https://catalog.go.kr")])
    run(_custom)
    card = c.get(f"{path}/data-card", headers={"X-Forwarded-Host": "192.0.2.50:3000"}).text
    assert f"https://catalog.go.kr/api/v1/external/datasets/{ids['bare']}/croissant" in card
    dcat = c.get(f"{path}/dcat").json()
    assert "https://catalog.go.kr/" in str(dcat) and "testserver" not in str(dcat)
    assert "https://catalog.go.kr/" in str(c.get(f"{V1}/external/dcat/catalog").json())
