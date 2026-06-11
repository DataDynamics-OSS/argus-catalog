"""AI Agent 카탈로그 단위 테스트 (자기완결형, in-memory SQLite).

외부 DB/서비스 없이 SQLite 메모리 엔진에 에이전트 테이블만 생성해 서비스
레이어의 Phase 1(CRUD·서브리소스)과 Phase 2(평가·미터링·평판·카드)를 검증한다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_agents
    # 또는: pytest tests/test_agents.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.agents.models as am
import app.catalog.models  # noqa: F401  (FK 대상 catalog_datasources 등록)
import app.embedding.models  # noqa: F401  (delete_agent 가 임베딩 행을 정리)
import app.models.models  # noqa: F401  (FK 대상 catalog_registered_models 등록)
from app.agents import schemas as s
from app.agents import service

_TABLES = [
    "catalog_datasources",
    "catalog_registered_models",
    "catalog_entity_embeddings",
    "catalog_ai_agents",
    "catalog_ai_agent_versions",
    "catalog_ai_agent_tools",
    "catalog_ai_agent_mcp_servers",
    "catalog_ai_agent_lineage",
    "catalog_ai_agent_evals",
    "catalog_ai_agent_invocation_log",
    "catalog_ai_agent_hook_events",
]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [am.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: am.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _run() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        # --- Phase 1: create / list / detail ---
        created = await service.create_agent(
            db,
            s.AIAgentCreate(
                name="cs.refund",
                display_name="환불 지원",
                status=s.AgentStatus.ACTIVE,
                framework="langgraph",
                endpoint="https://x/api",
                protocol="REST",
                auth_method="OAuth2.1",
                invocation_method="streaming",
                streaming=True,
                capabilities=["refund"],
                supported_languages=["ko", "en"],
                department="CS",
                owner_email="ops@x.com",
            ),
        )
        assert created.name == "cs.refund"
        assert created.status == "active"

        # 이름 중복 가드
        try:
            await service.create_agent(db, s.AIAgentCreate(name="cs.refund"))
            raise AssertionError("duplicate name should raise")
        except ValueError:
            pass

        tool = await service.add_tool(db, "cs.refund", s.AIAgentToolCreate(name="fetch_db"))
        await service.add_mcp_server(
            db, "cs.refund", s.AIAgentMcpServerCreate(name="finance-mcp", url="https://m")
        )
        await service.add_lineage(
            db,
            "cs.refund",
            s.AIAgentLineageCreate(target_type="model", target_ref="argus.ml.fraud"),
        )
        await service.create_version(
            db, "cs.refund", s.AIAgentVersionCreate(version="1.0.0", changelog="init")
        )

        detail = await service.get_agent_detail(db, "cs.refund")
        assert detail is not None
        assert len(detail.tools) == 1
        assert len(detail.mcp_servers) == 1
        assert len(detail.lineage) == 1
        assert len(detail.versions) == 1
        assert detail.capabilities == ["refund"]

        listed = await service.list_agents(db, search="refund")
        assert listed.total == 1

        # --- Phase 2: evaluation + metering + reputation ---
        await service.add_eval(
            db,
            "cs.refund",
            s.AIAgentEvalCreate(eval_type="accuracy", metric_key="em", metric_value=0.9),
        )
        await service.add_eval(
            db,
            "cs.refund",
            s.AIAgentEvalCreate(eval_type="task_success", metric_key="tsr", metric_value=0.8),
        )
        evals = await service.list_evals(db, "cs.refund")
        assert len(evals) == 2

        for status, lat, it, ot in [
            ("success", 100, 50, 20),
            ("success", 200, 60, 30),
            ("error", 300, 40, 10),
            ("success", 150, 55, 25),
        ]:
            await service.ingest_invocation(
                db,
                "cs.refund",
                s.AIAgentInvocationIngest(
                    status=status,
                    latency_ms=lat,
                    input_tokens=it,
                    output_tokens=ot,
                    cost=0.01,
                    consumer="team-a",
                ),
            )

        metering = await service.get_metering(db, "cs.refund")
        assert metering is not None
        assert metering.total_invocations == 4
        assert metering.success_count == 3
        assert metering.error_count == 1
        assert metering.success_rate == 0.75
        assert metering.error_rate == 0.25
        # reputation = (0.5 * 0.75 success_rate + 0.5 * 0.85 eval_avg) * 100 = 80.0
        assert metering.reputation_score == 80.0
        assert metering.by_consumer[0].name == "team-a"

        # 관측 캐시 컬럼 반영 확인
        cached = await service.get_agent_detail(db, "cs.refund")
        assert cached.usage_count == 4
        assert cached.error_rate == 0.25
        assert cached.reputation_score == 80.0

        # --- Phase 2: agent card (A2A) ---
        card = await service.get_agent_card(db, "cs.refund")
        assert card is not None
        assert card.streaming is True  # AIAgent.streaming 컬럼 값
        assert [sk.name for sk in card.skills] == ["fetch_db"]
        assert card.provider.organization == "CS"

        # --- Phase 3 연동 인터페이스: 정책 번들 pull ---
        policy = await service.get_policy_bundle(db, "cs.refund")
        assert policy is not None
        assert policy.name == "cs.refund"
        assert policy.auth_method == "OAuth2.1"
        assert [t.name for t in policy.allowed_tools] == ["fetch_db"]
        assert [m.name for m in policy.allowed_mcp_servers] == ["finance-mcp"]
        assert policy.policy_version  # 변경 감지 토큰 존재

        # --- Phase 3 연동 인터페이스: 집행 훅 이벤트 push/조회 ---
        await service.ingest_hook_event(
            db,
            "cs.refund",
            s.HookEventIngest(
                stage="pre_exec",
                decision="deny",
                action_type="network_egress",
                target="evil.example.com",
                policy_ref="network_allowlist",
                reason="허용되지 않은 도메인",
            ),
        )
        await service.ingest_hook_event(
            db,
            "cs.refund",
            s.HookEventIngest(
                stage="access", decision="mask", policy_ref="dlp_policies", target="ssn"
            ),
        )
        events = await service.list_hook_events(db, "cs.refund")
        assert events is not None and len(events) == 2
        denied = await service.list_hook_events(db, "cs.refund", decision="deny")
        assert len(denied) == 1
        assert denied[0].policy_ref == "network_allowlist"

        # --- Phase 2: sub-resource deletion ---
        assert await service.delete_tool(db, "cs.refund", tool.id) is True
        assert await service.delete_tool(db, "cs.refund", tool.id) is False
        after = await service.get_agent_detail(db, "cs.refund")
        assert len(after.tools) == 0

        # --- delete agent (CASCADE) ---
        assert await service.delete_agent(db, "cs.refund") is True
        assert (await service.list_agents(db)).total == 0


def test_agents_lifecycle() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_agents OK")
