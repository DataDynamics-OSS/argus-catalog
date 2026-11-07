# SPDX-License-Identifier: Apache-2.0
"""메뉴별 검색어의 LIKE 와일드카드(``%`` ``_``)가 글자로 매칭되는지 (자기완결형, in-memory SQLite).

AI 에이전트·MCP 서버·OCI 모델 허브·모델·사용자 관리·API 리니지 대상 검색 등이 검색어를
``f"%{search}%"`` 로 그대로 넣어 ``%`` 하나로 전체가, ``_`` 로 아무 글자나 매칭됐다.
``app.core.sql.like_contains`` + ``escape=LIKE_ESCAPE`` 로 통일했다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_like_escape_menus
"""

from __future__ import annotations

import asyncio
import pathlib
import re

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models  # noqa: F401 — FK 대상 테이블 등록
import app.models.models  # noqa: F401
from app.agents import service as agent_service
from app.agents.models import AIAgent
from app.mcp_servers import service as mcp_service
from app.mcp_servers.models import McpServer
from app.oci_hub import service as oci_service
from app.oci_hub.models import OciModel

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


def _with_fk_targets(tables):
    """FK 로 참조하는 테이블까지 포함(전체 create_all 은 모듈 간 FK 로 SQLite 에서 실패)."""
    seen, todo = {}, list(tables)
    while todo:
        t = todo.pop()
        if t.name in seen:
            continue
        seen[t.name] = t
        todo.extend(fk.column.table for fk in t.foreign_keys)
    return list(seen.values())


async def _run() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: AIAgent.metadata.create_all(
            sc, tables=_with_fk_targets([t for n, t in AIAgent.metadata.tables.items()
                                     if n.startswith(("catalog_ai_agent", "catalog_mcp_")) or n in ("catalog_oci_models", "catalog_oci_model_tags", "catalog_tags")])))
    make_db = async_sessionmaker(engine, expire_on_commit=False)
    async with make_db() as db:
        # "a_b" 만 '_' 를 글자로 가진다. "axb" 는 '_' 가 와일드카드면 같이 걸린다.
        for n in ("a_b", "axb", "pct100%"):
            db.add(AIAgent(name=n, urn=f"urn:agent:{n}"))
            db.add(McpServer(name=n, urn=f"urn:mcp:{n}"))
            db.add(OciModel(name=n))
        await db.commit()

        async def names(fn, q):
            return sorted(x.name for x in (await fn(db, search=q)).items)

        for fn in (agent_service.list_agents, mcp_service.list_servers, oci_service.list_models):
            assert await names(fn, "a_b") == ["a_b"], fn.__module__
            assert await names(fn, "%") == ["pct100%"], fn.__module__
            assert await names(fn, "b") == ["a_b", "axb"], fn.__module__   # 일반 부분 일치는 그대로
    await engine.dispose()


def _unescaped_like_calls() -> list[str]:
    """``.ilike(``/``.like(`` 호출 중 ``escape=`` 가 없는 것 — 사용자 입력이 섞이면 와일드카드가 된다.

    고정 접두사(내부 코드) 패턴처럼 일부러 이스케이프하지 않는 곳은 ``ALLOWED`` 에 둔다.
    """
    allowed = {"app/change_mgmt/service.py"}   # cr_code 접두사 — 서버가 만든 값
    bad = []
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR.parent).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\.i?like\(", text):
            # 괄호 짝을 맞춰 호출 인자 전체를 본다
            depth, i = 1, m.end()
            while depth and i < len(text):
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            if "escape=" not in text[m.end():i]:
                bad.append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1}")
    return bad


async def _main() -> None:
    await _run()
    bad = _unescaped_like_calls()
    assert not bad, f"escape 없는 LIKE: {bad}"


def test_like_escape_menus() -> None:
    """pytest 진입점."""
    asyncio.run(_main())


if __name__ == "__main__":
    asyncio.run(_main())
    print("test_like_escape_menus OK")
