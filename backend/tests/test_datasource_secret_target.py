# SPDX-License-Identifier: Apache-2.0
"""저장된 접속 비밀값이 다른 접속 대상으로 새지 않는지 (자기완결형, in-memory SQLite).

- 연결 테스트: ``datasource_id`` + 빈 비밀번호 + 바뀐 host/port/유형 → 저장 비밀번호를 채우지 않고 거부
- 접속 설정 저장: 빈 비밀번호 + 바뀐 host → 400(ValueError), 비밀번호를 함께 넣으면 허용

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_datasource_secret_target
"""

from __future__ import annotations

import asyncio

from app.catalog import router as catalog_router
from app.catalog import service
from app.catalog.datasource_secrets import (
    SecretTargetChangedError,
    changed_target_keys,
    guard_secret_reuse,
    reused_secret_keys,
)
from app.catalog.schemas import DatasourceConnectionTest, DatasourceCreate
from tests.test_datasource_stage2 import _make_session

STORED = {"host": "db.internal", "port": "3306", "username": "u", "password": "s3cret", "database": "app"}


def _test_helpers() -> None:
    same = {**STORED, "password": ""}
    assert changed_target_keys(same, STORED) == []
    assert reused_secret_keys(same, STORED) == ["password"]
    guard_secret_reuse(same, STORED)                                   # 같은 대상 → 허용
    guard_secret_reuse({**same, "database": "other", "username": "v"}, STORED)  # 대상 동일
    guard_secret_reuse({**STORED, "host": "evil", "password": "new"}, STORED)   # 새 비밀번호 → 허용
    guard_secret_reuse({**same, "host": "DB.INTERNAL "}, STORED)        # 대소문자·공백 정규화

    for bad in [{**same, "host": "evil.example"}, {**same, "port": "13306"},
                {**same, "password": "enc:forged"}, {"host": "evil", "password": ""}]:
        if bad.get("password", "").startswith("enc:"):
            bad = {**bad, "host": "evil"}
        try:
            guard_secret_reuse(bad, STORED)
            raise AssertionError(f"expected SecretTargetChangedError: {bad}")
        except SecretTargetChangedError as e:
            assert "password" in str(e)
    try:
        guard_secret_reuse(same, STORED, type_changed=True)
        raise AssertionError("expected SecretTargetChangedError (type)")
    except SecretTargetChangedError:
        pass

    # URL 형 대상 키도 같은 규칙
    api = {"api_url": "https://a", "token": "t"}
    try:
        guard_secret_reuse({"api_url": "https://evil", "token": ""}, api)
        raise AssertionError("expected SecretTargetChangedError (api_url)")
    except SecretTargetChangedError:
        pass


async def _test_endpoints() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        await service.save_datasource_configuration(db, ds.id, dict(STORED))

        seen: list[dict] = []

        async def fake_test(t, config):
            seen.append(config)
            return {"ok": True, "message": "연결 성공"}

        orig = service.test_datasource_connection
        service.test_datasource_connection = fake_test
        try:
            # 1) 같은 대상 → 저장 비밀번호로 테스트
            r = await catalog_router.test_datasource_connection(
                None, DatasourceConnectionTest(type="mysql", config={**STORED, "password": ""},
                                               datasource_id=ds.id), db)
            assert r["ok"] and seen[-1]["password"] == "s3cret"

            # 2) host 변경 + 빈 비밀번호 → 접속 시도 없이 거부, 비밀번호 미전송
            n = len(seen)
            r = await catalog_router.test_datasource_connection(
                None, DatasourceConnectionTest(type="mysql", config={**STORED, "host": "attacker.example",
                                                                    "password": ""}, datasource_id=ds.id), db)
            assert r["ok"] is False and "다시 입력" in r["message"], r
            assert len(seen) == n

            # 3) 유형 변경 → 거부
            r = await catalog_router.test_datasource_connection(
                None, DatasourceConnectionTest(type="postgresql", config={**STORED, "password": ""},
                                               datasource_id=ds.id), db)
            assert r["ok"] is False and len(seen) == n

            # 4) 새 비밀번호를 직접 넣으면 다른 host 도 테스트 가능(저장값 아님)
            r = await catalog_router.test_datasource_connection(
                None, DatasourceConnectionTest(type="mysql", config={**STORED, "host": "other",
                                                                    "password": "typed"}, datasource_id=ds.id), db)
            assert r["ok"] and seen[-1]["password"] == "typed"
        finally:
            service.test_datasource_connection = orig

        # 5) 저장: host 만 바꾸고 비밀번호를 비우면 400(ValueError), 저장값 불변
        try:
            await service.save_datasource_configuration(db, ds.id, {**STORED, "host": "attacker.example",
                                                                    "password": ""})
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        stored = await service.get_datasource_stored_config(db, ds.id)
        from app.catalog.datasource_secrets import unseal_config
        assert unseal_config(stored)["host"] == "db.internal"

        # 6) 비밀번호를 함께 넣으면 대상 변경 저장 허용
        await service.save_datasource_configuration(db, ds.id, {**STORED, "host": "new.internal",
                                                                "password": "n3w"})
        plain = unseal_config(await service.get_datasource_stored_config(db, ds.id))
        assert plain["host"] == "new.internal" and plain["password"] == "n3w"


async def _run() -> None:
    _test_helpers()
    await _test_endpoints()


def test_datasource_secret_target() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_datasource_secret_target OK")
