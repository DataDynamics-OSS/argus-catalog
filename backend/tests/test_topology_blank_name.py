# SPDX-License-Identifier: Apache-2.0
"""조직·시스템 생성 이름 검증 — 공백만이면 422, 앞뒤 공백은 제거 (자기완결형).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_topology_blank_name
"""

from __future__ import annotations

import asyncio

from pydantic import ValidationError

from app.catalog import topology_service
from app.catalog.schemas import OrganizationCreate, SystemCreate
from tests.test_datasource_stage2 import _make_session


def _test_schemas() -> None:
    for model in (OrganizationCreate, SystemCreate):
        for bad in ("   ", "\t\n", ""):
            try:
                model(name=bad)
                raise AssertionError(f"expected ValidationError: {model.__name__}({bad!r})")
            except ValidationError:
                pass
        assert model(name="  재무  ").name == "재무"


async def _test_service_trims() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        org = await topology_service.create_organization(db, OrganizationCreate(name="  본부 "))
        assert org.name == "본부"
        sys_ = await topology_service.create_system(db, SystemCreate(name=" 결제 ", org_id=org.id))
        assert sys_.name == "결제"


async def _run() -> None:
    _test_schemas()
    await _test_service_trims()


def test_topology_blank_name() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_topology_blank_name OK")
