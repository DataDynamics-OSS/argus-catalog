# SPDX-License-Identifier: Apache-2.0
"""페더레이션 보안 보강 테스트 (in-memory SQLite, 자기완결형).

검증:
- 비활성(PAUSED) peer 는 URN 해석이 거부되어 드릴다운·샘플·import 호출이 나가지 않는다
- export 토큰 미설정 시 sample capability 는 노출되지 않는다(명시 허용 시에만)
- import 시 peer 가 준 이름의 경로 구분자·상위 참조 제거
- 로컬 샘플 디렉터리 경로가 samples 루트를 벗어나지 않는다

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_security
    # 또는: pytest tests/test_federation_security.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.federation.models as fm
from app.core.config import settings
from app.federation import schemas as s
from app.federation import service
from app.settings.models import CatalogConfiguration


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [fm.Base.metadata.tables["federation_instances"], CatalogConfiguration.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: fm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _test_inactive_peer_not_resolved() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        created = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-a", name="A", base_url="https://a.internal",
        ))
        assert await service.resolve_federated_urn(db, "team-a::x.dataset") is not None

        inst = await service.get_instance(db, created.id)
        await service.update_instance(db, inst, s.FederatedInstanceUpdate(status="PAUSED"))
        assert await service.resolve_federated_urn(db, "team-a::x.dataset") is None
        # 해석 불가 → 드릴다운도 네트워크 호출 없이 None
        assert await service.federated_dataset_detail(db, "team-a::x.dataset") is None


async def _test_anonymous_sample_export() -> None:
    session_factory = await _make_session()
    orig_token = settings.federation_export_token
    orig_allow = settings.federation_export_allow_anonymous_sample
    try:
        async with session_factory() as db:
            settings.federation_export_token = ""
            settings.federation_export_allow_anonymous_sample = False
            exposed = await service.get_effective_exposed_fields(db)
            assert "sample" not in exposed and "schema" in exposed
            # 관리 화면용 정책 값은 그대로(sample 포함)
            assert "sample" in await service.get_exposed_fields(db)

            settings.federation_export_allow_anonymous_sample = True
            assert "sample" in await service.get_effective_exposed_fields(db)

            settings.federation_export_allow_anonymous_sample = False
            settings.federation_export_token = "t0k3n"
            assert "sample" in await service.get_effective_exposed_fields(db)
    finally:
        settings.federation_export_token = orig_token
        settings.federation_export_allow_anonymous_sample = orig_allow


def _test_safe_dataset_name() -> None:
    f = service._safe_dataset_name
    assert f("orders") == "orders"
    assert f("sakila.actor") == "sakila.actor"
    assert "/" not in f("../../../etc/x") and ".." not in f("../../../etc/x")
    assert "\\" not in f("..\\..\\x")
    assert f("..") == "imported"
    assert len(f("a" * 400)) == 255


def _test_sample_dir_containment() -> None:
    from app.catalog.router import _sample_dir_by_datasource

    orig = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = Path(tmp)
        try:
            root = (Path(tmp) / "samples").resolve()
            ok = _sample_dir_by_datasource("ds-1", "sakila.actor")
            assert root in ok.parents
            nested = _sample_dir_by_datasource("ds-1", "a/b")      # 하위 경로는 허용
            assert root in nested.parents
            for bad in ("../../../tmp/x", "../../outside"):
                try:
                    _sample_dir_by_datasource("ds-1", bad)
                    raise AssertionError(f"expected ValueError for {bad}")
                except ValueError:
                    pass
        finally:
            settings.data_dir = orig


async def _run() -> None:
    await _test_inactive_peer_not_resolved()
    await _test_anonymous_sample_export()
    _test_safe_dataset_name()
    _test_sample_dir_containment()


def test_federation_security() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_security OK")
