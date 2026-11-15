# SPDX-License-Identifier: Apache-2.0
"""AI 에이전트 메타데이터 보강(P2) 단위 테스트.

- TODO-018: 기반 모델 이름 등 숨어 있던 정보가 상세 응답에 나온다.
- TODO-019: 데이터 접근(데이터 소스·데이터셋) 등록과 자원 쪽 역방향 조회.
- TODO-020: 위험 등급·영향 평가·준수 대상·로그 보존·자율 수준과 목록 필터.
- TODO-021: 실행 신원·권한 범위·위임과 사용자 목록 대조.
- TODO-024: 스킬(A2A AgentSkill)과 기본 입출력 형식, 카드 내보내기.

정의 파일 왕복(내보낸 문서를 그대로 반영하면 '변경 없음')과 정책 번들 반영도 확인한다.

실행:
    cd backend
    pytest tests/test_agent_p2_metadata.py
"""

from __future__ import annotations

import pytest
import yaml
from sqlalchemy import select

from app.catalog.models import Dataset, DatasetSchema, Datasource
from app.models.models import RegisteredModel
from app.usermgr.models import ArgusRole, ArgusUser
from tests.test_agent_manifest import ALICE, BOB, Api, _make_factory, agent_doc


@pytest.fixture
async def api():
    a = Api(await _make_factory())
    async with a.factory() as db:
        src = Datasource(datasource_id="trino-pay01", name="결제 DW", type="trino", origin="PROD")
        role = ArgusRole(role_id="argus-user", name="사용자")
        db.add_all([src, role])
        await db.flush()
        db.add_all([
            Dataset(urn="trino-pay01.pay.refunds.dataset", name="pay.refunds", datasource_id=src.id, origin="PROD"),
            Dataset(urn="trino-pay01.pay.orders.dataset", name="pay.orders", datasource_id=src.id, origin="PROD"),
            RegisteredModel(name="argus.ml.refund_cls", urn="argus.ml.refund_cls.PROD.model"),
            ArgusUser(username="svc-refund", email="svc-refund@corp.example", first_name="s", last_name="r",
                      password_hash="x", status="inactive", role_id=role.id),
        ])
        await db.flush()
        refunds = (await db.execute(select(Dataset.id).where(Dataset.name == "pay.refunds"))).scalar_one()
        db.add(DatasetSchema(dataset_id=refunds, field_path="email", field_type="STRING", native_type="varchar",
                             pii_type="EMAIL"))
        await db.commit()
    yield a
    await a.client.aclose()


async def _ids(api: Api) -> dict[str, int]:
    async with api.factory() as db:
        rows = (await db.execute(select(Dataset.urn, Dataset.id))).all()
        src = (await db.execute(select(Datasource.id))).scalar_one()
        model = (await db.execute(select(RegisteredModel.id))).scalar_one()
    return {**dict(rows), "src": src, "model": model}


def full_doc(name: str = "cs.refund") -> dict:
    return agent_doc(
        name,
        model={"name": "refund-cls", "registryRef": "argus.ml.refund_cls"},
        interface={"endpoint": "https://agents.corp.example/refund", "protocol": "a2a",
                   "inputModes": ["text/plain", "application/json"], "outputModes": ["application/json"]},
        governance={"piiHandling": "masked", "riskTier": "high", "riskRationale": "환불 금액을 결정한다",
                    "impactAssessment": {"assessedAt": "2026-10-01", "result": "conditional", "ref": "wiki/IA-12"},
                    "compliance": ["PIPA", "EU AI Act"], "logRetentionDays": 365, "autonomyLevel": "supervised"},
        identity={"type": "service_account", "principal": "svc-refund", "provider": "keycloak",
                  "scopes": ["refund:write", "order:read"], "onBehalfOf": True},
        skills=[{"id": "refund-check", "name": "환불 가능 여부 판단", "description": "규정에 따라 판단",
                 "tags": ["환불"], "examples": ["이 주문 환불돼요?"]},
                {"name": "Handoff To Agent", "outputModes": ["text/plain"]}],
        dataAccess=[{"dataset": "trino-pay01.pay.refunds.dataset", "access": "read_write", "description": "환불 기록"},
                    {"datasource": "결제 DW", "access": "read"}],
    )


async def test_manifest_round_trip_and_detail(api):
    r = await api.as_(ALICE).apply(full_doc())
    assert r.json()["summary"]["created"] == 1, r.text
    d = (await api.get("/cs.refund")).json()
    # TODO-018: 기반 모델 이름
    assert d["base_model_ref"] and d["base_model_name"] == "argus.ml.refund_cls"
    # TODO-020
    assert (d["risk_tier"], d["autonomy_level"], d["log_retention_days"]) == ("high", "supervised", 365)
    assert (d["impact_assessed_at"], d["impact_assessment_result"]) == ("2026-10-01", "conditional")
    assert d["compliance"] == ["PIPA", "EU AI Act"]
    # TODO-021: 사용자 목록에 비활성으로 있는 서비스 계정
    assert (d["identity_type"], d["identity_principal"], d["on_behalf_of"]) == ("service_account", "svc-refund", True)
    assert d["granted_scopes"] == ["refund:write", "order:read"] and d["identity_state"] == "inactive"
    # TODO-024: 스킬 순서·id 생성
    assert [(k["skill_key"], k["sort_order"]) for k in d["skills"]] == [("refund-check", 0), ("handoff-to-agent", 1)]
    assert d["default_output_modes"] == ["application/json"]
    # TODO-019: 대상 이름이 붙는다
    acc = {x["target_type"]: x for x in d["data_access"]}
    assert acc["dataset"]["target_urn"] == "trino-pay01.pay.refunds.dataset"
    assert acc["dataset"]["datasource_name"] == "결제 DW" and acc["dataset"]["access"] == "read_write"
    assert acc["datasource"]["target_name"] == "결제 DW" and acc["datasource"]["target_urn"] == "trino-pay01"
    # 개인정보 컬럼이 있는 데이터셋 표시 (데이터 소스 전체는 판단하지 않음)
    assert acc["dataset"]["contains_pii"] is True and acc["datasource"]["contains_pii"] is None
    # 리니지 대상이 카탈로그에 없으면 연결하지 않는다(외부 대상·오타)
    assert [x["target_id"] for x in d["lineage"]] == [None]

    # 내보낸 문서를 그대로 반영하면 변경 없음
    exported = yaml.safe_load((await api.get("/cs.refund/manifest")).text)
    assert exported["spec"]["identity"]["scopes"] == ["refund:write", "order:read"]
    assert exported["spec"]["dataAccess"][1] == {"datasource": "trino-pay01", "access": "read"}
    r = await api.apply(exported)
    assert r.json()["summary"]["unchanged"] == 1, r.json()

    # 목록 바꾸기 — 스킬 하나 빼고, 데이터 접근 수정
    doc = full_doc()
    doc["spec"]["skills"] = doc["spec"]["skills"][1:]
    doc["spec"]["dataAccess"] = [{"dataset": "trino-pay01.pay.refunds.dataset", "access": "read"}]
    changes = (await api.apply(doc)).json()["results"][0]["changes"]
    assert "skills: -refund-check" in changes
    assert any(c.startswith("skills: ~handoff-to-agent") for c in changes)  # 순서가 바뀜
    assert any(c.startswith("dataAccess: ~trino-pay01.pay.refunds.dataset") for c in changes)
    assert any(c.startswith("dataAccess: -datasource#") for c in changes)


async def test_manifest_rejects_unknown_data_target_and_bad_values(api):
    api.as_(ALICE)
    r = await api.apply(agent_doc(dataAccess=[{"dataset": "nope.dataset"}]))
    res = r.json()["results"][0]
    assert res["action"] == "failed" and "데이터셋을 찾을 수 없습니다" in res["error"]
    r = await api.apply(agent_doc(governance={"riskTier": "extreme"}))
    assert r.json()["results"][0]["action"] == "failed"


async def test_card_exports_skills_and_modes(api):
    await api.as_(ALICE).apply(full_doc())
    card = (await api.get("/cs.refund/card")).json()
    assert card["defaultInputModes"] == ["text/plain", "application/json"]
    assert card["defaultOutputModes"] == ["application/json"]
    s0, s1 = card["skills"]
    assert s0 == {"id": "refund-check", "name": "환불 가능 여부 판단", "description": "규정에 따라 판단",
                  "tags": ["환불"], "examples": ["이 주문 환불돼요?"]}
    assert s1["id"] == "handoff-to-agent" and s1["outputModes"] == ["text/plain"] and "examples" not in s1
    params = card["capabilities"]["extensions"][0]["params"]
    assert params["riskTier"] == "high" and params["skillSource"] == "skills"

    # 스킬이 없으면 기능 목록에서 만든다
    await api.apply(agent_doc("cs.faq"))
    faq = (await api.get("/cs.faq/card")).json()
    assert [s["name"] for s in faq["skills"]] == ["환불 접수", "상담원 연결"]
    assert faq["capabilities"]["extensions"][0]["params"]["skillSource"] == "capabilities"


async def test_skill_and_data_access_api(api):
    await api.as_(ALICE).apply(agent_doc())
    ids = await _ids(api)
    c = api.client
    r = await c.post("/api/v1/ai-agents/cs.refund/skills", json={"name": "Refund Check", "tags": ["a", "a", " "]})
    assert r.status_code == 200, r.text
    sk = r.json()
    assert sk["skill_key"] == "refund-check" and sk["tags"] == ["a"]
    assert (await c.post("/api/v1/ai-agents/cs.refund/skills",
                         json={"skill_key": "refund-check", "name": "x"})).status_code == 409
    r = await c.patch(f"/api/v1/ai-agents/cs.refund/skills/{sk['id']}", json={"description": "설명"})
    assert r.json()["description"] == "설명" and r.json()["skill_key"] == "refund-check"

    r = await c.post("/api/v1/ai-agents/cs.refund/data-access",
                     json={"dataset_id": ids["trino-pay01.pay.orders.dataset"], "access": "read"})
    assert r.status_code == 200, r.text
    acc = r.json()
    assert acc["target_name"] == "pay.orders" and acc["datasource_name"] == "결제 DW"
    # 중복·대상 없음·둘 다 지정은 거절
    assert (await c.post("/api/v1/ai-agents/cs.refund/data-access",
                         json={"dataset_id": ids["trino-pay01.pay.orders.dataset"]})).status_code == 400
    assert (await c.post("/api/v1/ai-agents/cs.refund/data-access",
                         json={"datasource_name": "없는 소스"})).status_code == 400
    assert (await c.post("/api/v1/ai-agents/cs.refund/data-access",
                         json={"dataset_id": 1, "datasource_id": 1})).status_code == 422

    # 소유자가 아니면 바꿀 수 없다
    api.as_(BOB)
    assert (await c.post("/api/v1/ai-agents/cs.refund/skills", json={"name": "x"})).status_code == 403
    api.as_(ALICE)

    # 정책 번들에 데이터 접근이 실린다
    pol = (await api.get("/cs.refund/policy")).json()
    assert pol["data_access"] == [{"target_type": "dataset", "name": "pay.orders",
                                   "urn": "trino-pay01.pay.orders.dataset", "access": "read"}]

    assert (await c.delete(f"/api/v1/ai-agents/cs.refund/data-access/{acc['id']}")).status_code == 200
    assert (await c.delete(f"/api/v1/ai-agents/cs.refund/skills/{sk['id']}")).status_code == 200
    d = (await api.get("/cs.refund")).json()
    assert d["skills"] == [] and d["data_access"] == []


async def test_resource_usage_reverse_lookup(api):
    await api.as_(ALICE).apply(full_doc("cs.refund"))
    # 리니지에 데이터셋 URN 을 적은 에이전트도 함께 나온다
    await api.apply(agent_doc("cs.audit", lineage=[
        {"targetType": "dataset", "targetRef": "trino-pay01.pay.orders.dataset", "relation": "reads"},
        {"targetType": "model", "targetRef": "argus.ml.refund_cls", "relation": "depends_on"}]))
    ids = await _ids(api)

    r = await api.get(f"/usage?dataset_id={ids['trino-pay01.pay.refunds.dataset']}")
    assert sorted((u["agent_name"], u["via"], u["relation"]) for u in r.json()) == [
        ("cs.refund", "data_access", "read_write"), ("cs.refund", "datasource", "read")]
    r = await api.get(f"/usage?dataset_id={ids['trino-pay01.pay.orders.dataset']}")
    assert sorted((u["agent_name"], u["via"]) for u in r.json()) == [
        ("cs.audit", "lineage"), ("cs.refund", "datasource")]
    r = await api.get(f"/usage?datasource_id={ids['src']}")
    assert [(u["agent_name"], u["relation"]) for u in r.json()] == [("cs.refund", "read")]
    r = await api.get(f"/usage?model_id={ids['model']}")
    assert sorted((u["agent_name"], u["via"]) for u in r.json()) == [("cs.audit", "lineage"),
                                                                     ("cs.refund", "base_model")]
    assert (await api.get("/usage")).status_code == 400
    assert (await api.get(f"/usage?dataset_id=1&model_id={ids['model']}")).status_code == 400
    assert (await api.get("/usage?dataset_id=99999")).json() == []

    # 리니지 대상이 카탈로그 엔티티로 해석된다
    lin = {x["target_type"]: x["target_id"] for x in (await api.get("/cs.audit")).json()["lineage"]}
    assert lin == {"dataset": ids["trino-pay01.pay.orders.dataset"], "model": ids["model"]}


async def test_list_filters_by_risk_tier_and_autonomy(api):
    await api.as_(ALICE).apply(full_doc("cs.refund"), agent_doc("cs.faq"))
    names = lambda r: sorted(i["name"] for i in r.json()["items"])  # noqa: E731
    assert names(await api.get("?risk_tier=high")) == ["cs.refund"]
    assert names(await api.get("?risk_tier=none")) == ["cs.faq"]
    assert names(await api.get("?risk_tier=high,none")) == ["cs.faq", "cs.refund"]
    assert names(await api.get("?autonomy_level=supervised")) == ["cs.refund"]
    assert (await api.get("?risk_tier=extreme")).status_code == 422
    item = next(i for i in (await api.get("")).json()["items"] if i["name"] == "cs.refund")
    assert (item["risk_tier"], item["autonomy_level"]) == ("high", "supervised")
    st = (await api.get("/stats")).json()
    assert st["by_risk_tier"] == [{"name": "high", "count": 1}]
    assert st["unassessed_risk_count"] == 1  # cs.faq
    assert st["high_risk_unassessed_count"] == 0  # cs.refund 는 영향 평가 기록이 있다


async def test_identity_state(api):
    api.as_(ALICE)
    doc = agent_doc(identity={"type": "oauth_client", "principal": "refund-client"})
    await api.apply(doc)
    assert (await api.get("/cs.refund")).json()["identity_state"] is None  # OAuth 클라이언트는 대조 대상 아님
    doc["spec"]["identity"] = {"type": "service_account", "principal": "nobody"}
    await api.apply(doc)
    assert (await api.get("/cs.refund")).json()["identity_state"] == "unknown"
    doc["spec"]["identity"] = {"type": "user_delegated", "principal": "SVC-REFUND@corp.example"}
    await api.apply(doc)
    assert (await api.get("/cs.refund")).json()["identity_state"] == "inactive"
