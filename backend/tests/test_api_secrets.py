# SPDX-License-Identifier: Apache-2.0
"""운영 시크릿 점검·자격증명 복호화 실패·Base URL/계약 문서 URL 검증 테스트 (in-memory SQLite, 자기완결형).

검증:
- ARGUS_REQUIRE_SECRETS 꺼짐: 미설정이면 개발용 기본값으로 동작(기존 호환)
- ARGUS_REQUIRE_SECRETS 켜짐: 미설정·개발용 기본값·16자 미만이면 기동 점검·암복호화 거부
- 키가 바뀌어 저장 자격증명을 복호화하지 못하면 인증 없이 호출하지 않고 CredentialDecryptError
- Base URL·계약 문서 URL 은 http(s) 절대 주소만(공백 제거, 빈 값은 None)

실행:
    cd backend
    pytest tests/test_api_secrets.py
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.apis import crypto, service
from app.apis.models import CatalogApi, CatalogApiCredential
from app.core import secrets
from app.core.database import Base

GOOD_KEY = "k" * 32
OTHER_KEY = "z" * 32


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ARGUS_SECRET_KEY", "ARGUS_JWT_SECRET", secrets.REQUIRE_ENV):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# 운영 시크릿 점검
# ---------------------------------------------------------------------------

def test_not_required_uses_dev_default():
    assert not secrets.secrets_required()
    secrets.check_required_secrets()  # 아무 일도 없음
    assert secrets.resolve_secret("ARGUS_SECRET_KEY") == secrets.DEV_SECRET_KEY
    assert crypto.decrypt(crypto.encrypt("hello")) == "hello"


@pytest.mark.parametrize("value,expected", [
    (None, "설정되지 않았습니다"),
    ("   ", "설정되지 않았습니다"),
    (secrets.DEV_SECRET_KEY, "개발용 기본값"),
    ("short-key", "너무 짧습니다"),
    (GOOD_KEY, None),
])
def test_secret_problem(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("ARGUS_SECRET_KEY", value)
    problem = secrets.secret_problem("ARGUS_SECRET_KEY")
    assert (problem is None) if expected is None else (expected in problem)


@pytest.mark.parametrize("flag", ["true", "1", "YES", "on"])
def test_required_flag_values(monkeypatch, flag):
    monkeypatch.setenv(secrets.REQUIRE_ENV, flag)
    assert secrets.secrets_required()


def test_required_rejects_missing_and_dev_values(monkeypatch):
    monkeypatch.setenv(secrets.REQUIRE_ENV, "true")
    monkeypatch.setenv("ARGUS_JWT_SECRET", secrets.DEV_JWT_SECRET)
    with pytest.raises(secrets.InsecureSecretError) as e:
        secrets.check_required_secrets()
    msg = str(e.value)
    assert "ARGUS_SECRET_KEY 가 설정되지 않았습니다" in msg
    assert "ARGUS_JWT_SECRET 가 개발용 기본값입니다" in msg
    # 기동 점검을 거치지 않는 경로(암복호화)도 거부
    with pytest.raises(secrets.InsecureSecretError):
        crypto.encrypt("x")


def test_required_accepts_good_values(monkeypatch):
    monkeypatch.setenv(secrets.REQUIRE_ENV, "true")
    monkeypatch.setenv("ARGUS_SECRET_KEY", GOOD_KEY)
    monkeypatch.setenv("ARGUS_JWT_SECRET", GOOD_KEY + "-jwt")
    secrets.check_required_secrets()
    assert crypto.decrypt(crypto.encrypt("hello")) == "hello"


def test_value_is_not_stripped(monkeypatch):
    # 값을 가공하면 기존 암호문의 키가 달라진다 — 공백도 키의 일부로 그대로 사용
    monkeypatch.setenv("ARGUS_SECRET_KEY", GOOD_KEY + " ")
    assert secrets.resolve_secret("ARGUS_SECRET_KEY") == GOOD_KEY + " "


# ---------------------------------------------------------------------------
# 자격증명 복호화 실패
# ---------------------------------------------------------------------------

async def test_build_auth_raises_when_key_changed(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[CatalogApi.__table__, CatalogApiCredential.__table__])
    make_session = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("ARGUS_SECRET_KEY", GOOD_KEY)
    async with make_session() as s:
        api = CatalogApi(name="it-sec", urn="it-sec.api", protocol="REST", source="manual")
        s.add(api)
        await s.flush()
        cred = CatalogApiCredential(api_id=api.id, label="운영 키", type="bearer",
                                    secret=crypto.encrypt(json.dumps({"token": "t0k3n"})))
        s.add(cred)
        await s.commit()
        api_id, cred_id = api.id, cred.id

    async with make_session() as s:  # 같은 키 → 정상 주입
        headers, _ = await service._build_auth(s, api_id, cred_id)
        assert headers.get("Authorization") == "Bearer t0k3n"

    monkeypatch.setenv("ARGUS_SECRET_KEY", OTHER_KEY)  # 키 교체
    async with make_session() as s:
        with pytest.raises(service.CredentialDecryptError) as e:
            await service._build_auth(s, api_id, cred_id)
        assert "운영 키" in str(e.value) and "ARGUS_SECRET_KEY" in str(e.value)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Base URL · 계약 문서 URL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn", [service.normalize_base_url, service.normalize_contract_url])
@pytest.mark.parametrize("value", [
    "javascript:alert(1)", "JAVASCRIPT:alert(1)", "ftp://example.com/a", "data:text/html,x", "file:///etc/passwd",
    "https://", "http:///x", "//example.com/a", "example.com", "/v1", "https://ex ample.com", "https://a.com/\tb",
    "https://example.com/" + "a" * 1000,
])
def test_url_rejected(fn, value):
    with pytest.raises(service.ApiInputError):
        fn(value)


@pytest.mark.parametrize("fn", [service.normalize_base_url, service.normalize_contract_url])
@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), ("   ", None),
    ("  https://api.example.com/v1  ", "https://api.example.com/v1"),
    ("HTTP://webhook-receiver:8080", "HTTP://webhook-receiver:8080"),
    ("http://10.0.0.5:8080/base?x=1", "http://10.0.0.5:8080/base?x=1"),
])
def test_url_accepted(fn, value, expected):
    assert fn(value) == expected


def test_url_error_messages():
    with pytest.raises(service.ApiInputError, match="^Base URL 은 http"):
        service.normalize_base_url("ftp://x")
    with pytest.raises(service.ApiInputError, match="^계약 문서 URL 은 http"):
        service.normalize_contract_url("ftp://x")
