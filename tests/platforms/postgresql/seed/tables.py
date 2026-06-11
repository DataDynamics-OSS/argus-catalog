"""PostgreSQL 통합 테스트 데이터는 ``compose.yml`` 의 docker-entrypoint-initdb.d
로 컨테이너 첫 기동 시 northwind SQL 이 직접 적재된다. 여기서는 ``seed_all`` /
``drop_all`` 시그니처만 유지해 다른 platform 의 패턴과 정렬한다.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def seed_all(engine: Engine) -> None:
    """northwind 가 실제로 적재됐는지만 확인."""
    with engine.connect() as conn:
        row_count = conn.execute(text("SELECT COUNT(*) FROM public.customers")).scalar()
        if not row_count or row_count < 1:
            raise RuntimeError(
                "northwind DB 가 비어 있다 — compose.yml 의 northwind volume 마운트를 확인하세요."
            )


def drop_all(engine: Engine) -> None:
    """northwind 는 read-only 샘플이라 재생성하지 않는다. compose 의 ``down -v`` 로 매 세션 fresh."""
    return None
