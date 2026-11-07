# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 응답의 system_name 이 모든 경로에서 채워지는지 (자기완결형, in-memory SQLite).

- 시스템 배정 ``PUT /catalog/datasources/{id}/system`` 응답
- 메타데이터 ``GET /catalog/datasources/{id}/metadata`` 의 ``datasource``
- 생성 응답(미배정이면 null)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_datasource_system_name
"""

from __future__ import annotations

import asyncio

import app.catalog.models as cm
from app.catalog import service, topology_router
from app.catalog.schemas import DatasourceCreate, DatasourceSystemAssign
from tests.test_datasource_stage2 import _make_session


async def _run() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        sys_ = cm.System(name="결제", code="pay")
        db.add(sys_)
        await db.commit()

        created = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        assert created.system_id is None and created.system_name is None

        # 시스템 배정 응답
        resp = await topology_router.assign_datasource_system(
            None, created.id, DatasourceSystemAssign(system_id=sys_.id), db)
        assert resp.system_id == sys_.id and resp.system_name == "결제", resp

        # 메타데이터 응답
        meta = await service.get_datasource_metadata(db, created.id)
        assert meta.datasource.system_name == "결제", meta.datasource

        # 배정 해제 → null
        resp = await topology_router.assign_datasource_system(
            None, created.id, DatasourceSystemAssign(system_id=None), db)
        assert resp.system_id is None and resp.system_name is None


def test_datasource_system_name() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_datasource_system_name OK")
