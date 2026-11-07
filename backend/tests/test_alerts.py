# SPDX-License-Identifier: Apache-2.0
"""알림(규칙·알림·전달) 서비스·권한 테스트 (in-memory SQLite, 자기완결형).

검증:
- 스키마: 채널(EMAIL 거부)·불리언·Webhook URL·트리거별 trigger_config 형태·SYNC_STALE 거부·수정 null 가드
- 규칙 조합: 범위 대상 필수·존재, MCP 트리거는 ALL/DATASOURCE 만, WEBHOOK 은 URL 필수, 생성자 기록
- 수정: 병합 상태 재검증, 활성 토글은 범위 대상 삭제와 무관, Webhook 채널 해제 시 URL 제거
- 평가: 깨진 trigger_config 규칙은 건너뛰고 나머지는 평가, 요약 500자 절단, 한국어 요약
- 전달: IN_APP 수신자 기록, WEBHOOK 결과(SENT/FAILED) 기록·Slack 호환 text
- 상태 전이: 허용 전이·처리자 기록·다시 열기 시 비움, 권한(관리자/데이터셋 소유자), 일괄 처리
- 목록: 범위 대상 삭제 표시, Webhook URL 은 관리자에게만, 보관 정리
- HTTP: 비로그인 401, 규칙 관리 403/201, 상태 변경 403/200/409

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_alerts
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.alert import schemas as s
from app.alert import service
from app.alert.models import AlertNotification, AlertRule, LineageAlert
from app.permissions import router as perm
from app.permissions.models import Permission


def _expect_invalid(**kw) -> None:
    base = {"rule_name": "r", "scope_type": "ALL"}
    base.update(kw)
    try:
        s.AlertRuleCreate(**base)
    except ValidationError:
        return
    raise AssertionError(f"검증 통과하면 안 됨: {kw}")


def _test_schema_validation() -> None:
    r = s.AlertRuleCreate(
        rule_name="  감시 ", scope_type="ALL", trigger_type="COLUMN_WATCH",
        trigger_config=json.dumps({"columns": [" a ", "a", "b"], "change_types": ["DROP"], "junk": 1}),
        channels="in_app, WEBHOOK,IN_APP", notify_owners=True, webhook_url=" https://hooks.example.com/x ",
        subscribers="alice, bob,\nalice",
    )
    assert r.rule_name == "감시"
    assert json.loads(r.trigger_config) == {"columns": ["a", "b"], "change_types": ["DROP"]}
    assert r.channels == "IN_APP,WEBHOOK" and r.notify_owners == "true"
    assert r.webhook_url == "https://hooks.example.com/x" and r.subscribers == "alice,bob"

    _expect_invalid(channels="EMAIL")
    _expect_invalid(channels="IN_APP,SMS")
    _expect_invalid(channels=" , ")
    _expect_invalid(trigger_type="COLUMN_WATCH")                                     # columns 필수
    _expect_invalid(trigger_type="COLUMN_WATCH", trigger_config='{"columns": []}')
    _expect_invalid(trigger_type="SCHEMA_CHANGE", trigger_config='{"change_types": []}')
    _expect_invalid(trigger_type="SCHEMA_CHANGE", trigger_config='{"change_types": ["RENAME"]}')
    _expect_invalid(trigger_config="[1]")
    _expect_invalid(trigger_config="{not json")
    _expect_invalid(trigger_type="SYNC_STALE")
    _expect_invalid(trigger_type="QUALITY_FAILED", trigger_config='{"min_severity": "HIGH"}')
    _expect_invalid(trigger_type="MCP_POLICY_DRIFT", trigger_config='{"environments": ["QA"]}')
    _expect_invalid(webhook_url="ftp://x")
    _expect_invalid(webhook_url="hooks.example.com")
    _expect_invalid(notify_owners="maybe")
    _expect_invalid(rule_name="   ")
    _expect_invalid(description="x" * 2001)

    mcp = s.AlertRuleCreate(rule_name="m", scope_type="ALL", trigger_type="MCP_SECURITY_WARNING",
                            trigger_config={"min_severity": "HIGH", "environments": ["PROD"]})
    assert json.loads(mcp.trigger_config) == {"min_severity": "high", "environments": ["PROD"]}

    for bad in ({"rule_name": None}, {"channels": None}, {"is_active": None}, {"trigger_type": None}):
        try:
            s.AlertRuleUpdate(**bad)
        except ValidationError:
            continue
        raise AssertionError(f"null 허용하면 안 됨: {bad}")
    assert s.AlertRuleUpdate(description=None, webhook_url=None).model_dump(exclude_unset=True) == {
        "description": None, "webhook_url": None}
    assert s.AlertRuleUpdate(is_active=False).is_active == "false"
    try:
        s.AlertUpdateStatus(status="CLOSED")
        raise AssertionError("알 수 없는 상태 허용")
    except ValidationError:
        pass


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    tables = [
        cm.Base.metadata.tables["catalog_datasources"],
        cm.Base.metadata.tables["catalog_datasets"],
        cm.Base.metadata.tables["catalog_tags"],
        cm.Base.metadata.tables["catalog_dataset_tags"],
        cm.Base.metadata.tables["catalog_owners"],
        cm.DatasetLineage.__table__,
        cm.DatasetColumnMapping.__table__,
        AlertRule.__table__,
        LineageAlert.__table__,
        AlertNotification.__table__,
        Permission.__table__,
    ]
    pending, seen = list(tables), set()
    while pending:
        t = pending.pop()
        if t.name in seen:
            continue
        seen.add(t.name)
        if t not in tables:
            tables.append(t)
        pending.extend(fk.column.table for fk in t.foreign_keys)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(db) -> dict:
    src = cm.Datasource(datasource_id="pg-1", name="PG", type="postgresql")
    db.add(src)
    await db.flush()
    a = cm.Dataset(urn="urn:a", name="orders", datasource_id=src.id, created_by="alice")
    b = cm.Dataset(urn="urn:b", name="orders_fact", datasource_id=src.id, created_by="carol")
    db.add_all([a, b])
    await db.flush()
    db.add(cm.Owner(dataset_id=a.id, owner_name="olivia"))
    tag = cm.Tag(name="PII")
    db.add(tag)
    await db.flush()
    db.add(cm.DatasetTag(dataset_id=a.id, tag_id=tag.id))
    lin = cm.DatasetLineage(source_dataset_id=a.id, target_dataset_id=b.id, relation_type="ETL",
                            lineage_source="MANUAL")
    db.add(lin)
    await db.flush()
    db.add(cm.DatasetColumnMapping(dataset_lineage_id=lin.id, source_column="amount", target_column="amt"))
    await db.commit()
    return {"src": src.id, "a": a.id, "b": b.id, "tag": tag.id, "lin": lin.id}


def _user(name: str, admin: bool = False):
    return SimpleNamespace(username=name, is_admin=admin, is_superuser=False)


async def _expect_rule_error(coro, contains: str) -> None:
    try:
        await coro
    except service.RuleValidationError as e:
        assert contains in str(e), str(e)
        return
    raise AssertionError(f"RuleValidationError 기대: {contains}")


async def _run() -> None:
    engine, factory = await _make_session()
    hooks: list[httpx.Request] = []

    def _hook(request: httpx.Request) -> httpx.Response:
        hooks.append(request)
        return httpx.Response(500 if "fail" in request.url.path else 200)

    service._webhook_transport = httpx.MockTransport(_hook)

    async with factory() as db:
        ids = await _seed(db)

        # ── 규칙 조합 검증 ──
        await _expect_rule_error(service.create_rule(db, s.AlertRuleCreate(
            rule_name="x", scope_type="DATASET")), "감시 대상")
        await _expect_rule_error(service.create_rule(db, s.AlertRuleCreate(
            rule_name="x", scope_type="DATASET", scope_id=9999)), "존재하지")
        await _expect_rule_error(service.create_rule(db, s.AlertRuleCreate(
            rule_name="x", scope_type="TAG", scope_id=ids["tag"], trigger_type="MCP_POLICY_DRIFT")), "MCP")
        await _expect_rule_error(service.create_rule(db, s.AlertRuleCreate(
            rule_name="x", scope_type="ALL", channels="IN_APP,WEBHOOK")), "Webhook URL")

        # ALL 범위는 scope_id 를 버리고, Webhook 채널이 아니면 URL 을 보관하지 않는다
        r_all = await service.create_rule(db, s.AlertRuleCreate(
            rule_name="전체", scope_type="ALL", scope_id=123, webhook_url="https://x.example.com/h",
            subscribers="sub1"), created_by="admin")
        assert r_all.scope_id is None and r_all.webhook_url is None and r_all.created_by == "admin"

        r_watch = await service.create_rule(db, s.AlertRuleCreate(
            rule_name="감시", scope_type="DATASET", scope_id=ids["a"], trigger_type="COLUMN_WATCH",
            trigger_config='{"columns": ["amount"]}', channels="IN_APP,WEBHOOK",
            webhook_url="https://hooks.example.com/ok", notify_owners="true"), created_by="admin")
        r_map = await service.create_rule(db, s.AlertRuleCreate(
            rule_name="매핑", scope_type="LINEAGE", scope_id=ids["lin"], trigger_type="MAPPING_BROKEN",
            channels="WEBHOOK", webhook_url="https://hooks.example.com/fail"), created_by="admin")
        r_tag = await service.create_rule(db, s.AlertRuleCreate(
            rule_name="태그", scope_type="TAG", scope_id=ids["tag"], trigger_type="SCHEMA_CHANGE",
            trigger_config='{"change_types": ["ADD"]}'), created_by="admin")
        await db.commit()

        # 깨진 설정을 DB 에 직접 심는다(과거 데이터) — 이 규칙만 건너뛰어야 함
        db.add(AlertRule(rule_name="깨짐", scope_type="ALL", trigger_type="ANY", trigger_config="{oops",
                         channels="IN_APP", notify_owners="true", is_active="true"))
        db.add(AlertRule(rule_name="배열", scope_type="ALL", trigger_type="SCHEMA_CHANGE", trigger_config="[1]",
                         channels="IN_APP,EMAIL", notify_owners="true", is_active="true"))
        await db.commit()

        # ── 수정: 병합 재검증 ──
        await _expect_rule_error(service.update_rule(db, r_tag.id, s.AlertRuleUpdate(
            trigger_type="COLUMN_WATCH")), "columns")
        await _expect_rule_error(service.update_rule(db, r_tag.id, s.AlertRuleUpdate(
            trigger_type="MCP_SECURITY_WARNING")), "MCP")
        await _expect_rule_error(service.update_rule(db, r_all.id, s.AlertRuleUpdate(
            channels="WEBHOOK")), "Webhook URL")
        await db.rollback()
        upd = await service.update_rule(db, r_watch.id, s.AlertRuleUpdate(channels="IN_APP"))
        assert upd.channels == "IN_APP" and upd.webhook_url is None          # Webhook 해제 → URL 제거
        upd = await service.update_rule(db, r_watch.id, s.AlertRuleUpdate(
            channels="IN_APP,WEBHOOK", webhook_url="https://hooks.example.com/ok"))
        assert upd.webhook_url == "https://hooks.example.com/ok"
        await db.commit()

        # ── 스키마 변경 평가 ──
        changes = [
            {"field": "amount", "type": "DROP", "before": {"field_type": "INT"}, "after": None},
            {"field": "x" * 600, "type": "ADD", "before": None, "after": {"field_type": "STRING"}},
        ]
        alerts = await service.evaluate_rules_and_create_alerts(db, ids["a"], changes)
        await db.commit()
        by_rule = {a.rule_id: a for a in alerts}
        alert_ids = {rid: a.id for rid, a in by_rule.items()}
        assert set(by_rule) == {r_all.id, r_watch.id, r_map.id, r_tag.id}, set(by_rule)  # 깨진 규칙 2개 제외
        assert by_rule[r_all.id].change_summary == "스키마 변경: 컬럼 삭제 1개, 추가 1개"
        assert by_rule[r_all.id].severity == "BREAKING"
        assert by_rule[r_watch.id].change_summary == "감시 컬럼 변경: 'amount' 삭제"
        assert by_rule[r_map.id].affected_dataset_id == ids["b"]
        assert by_rule[r_map.id].change_summary == "매핑 컬럼 변경: 'amount' 삭제 (매핑: amt)"
        assert by_rule[r_tag.id].severity == "INFO"
        assert all(len(a.change_summary) <= 500 for a in alerts)

        # 품질 알림 — 긴 요약도 500자 이내
        q_rule = await service.create_rule(db, s.AlertRuleCreate(
            rule_name="품질", scope_type="ALL", trigger_type="QUALITY_FAILED",
            trigger_config='{"min_severity": "WARNING"}'), created_by="admin")
        q = await service.evaluate_quality_alerts(db, ids["a"], 42.0, [
            {"rule_name": "n" * 300, "severity": "WARNING"}, {"rule_name": "m" * 300, "severity": "BREAKING"},
            {"rule_name": "low", "severity": "INFO"},
        ])
        await db.commit()
        assert len(q) == 1 and len(q[0].change_summary) <= 500 and q[0].severity == "BREAKING"
        assert json.loads(q[0].change_detail)["failed_rules"][-1]["rule_name"] == "m" * 300
        assert q[0].rule_id == q_rule.id

        # ── 전달 기록 ──
        notes = (await db.execute(select(AlertNotification))).scalars().all()
        by_alert: dict[int, list[AlertNotification]] = {}
        for n in notes:
            by_alert.setdefault(n.alert_id, []).append(n)
        all_notes = {(n.channel, n.recipient, n.status) for n in by_alert[by_rule[r_all.id].id]}
        assert all_notes == {("IN_APP", "sub1", "SENT"), ("IN_APP", "olivia", "SENT")}, all_notes
        watch_notes = {(n.channel, n.recipient, n.status) for n in by_alert[by_rule[r_watch.id].id]}
        assert ("WEBHOOK", "hooks.example.com", "SENT") in watch_notes, watch_notes
        map_notes = {(n.channel, n.status) for n in by_alert[by_rule[r_map.id].id]}
        assert map_notes == {("WEBHOOK", "FAILED")}, map_notes                    # HTTP 500 → FAILED
        assert all(n.channel != "EMAIL" for n in notes)
        sent = json.loads(hooks[0].content)
        assert sent["text"].startswith("[심각] 감시 컬럼 변경") and sent["rule_name"] == "감시"

        # ── 규칙 목록: 범위 대상 삭제 표시, Webhook 은 관리자에게만 ──
        rules = {r.rule_name: r for r in await service.list_rules(db, reveal_webhook=False)}
        assert rules["감시"].webhook_url is None and rules["감시"].webhook_configured
        assert rules["감시"].alert_count == 1 and rules["매핑"].scope_name == "orders → orders_fact"
        assert rules["태그"].scope_name == "PII" and not rules["태그"].scope_missing
        tag_row = (await db.execute(select(cm.Tag).where(cm.Tag.id == ids["tag"]))).scalar_one()
        await db.delete(tag_row)
        await db.commit()
        rules = {r.rule_name: r for r in await service.list_rules(db)}
        assert rules["태그"].scope_missing and rules["태그"].scope_name is None
        assert rules["감시"].webhook_url == "https://hooks.example.com/ok"
        # 범위 대상이 지워져도 활성 토글·이름 변경은 가능, 범위 재지정은 검증
        await service.update_rule(db, r_tag.id, s.AlertRuleUpdate(is_active=False, rule_name="태그2"))
        await _expect_rule_error(service.update_rule(db, r_tag.id, s.AlertRuleUpdate(
            scope_type="TAG")), "존재하지")
        await db.rollback()

        # ── 상태 전이·권한 ──
        admin, alice, olivia, carol, bob = (_user("admin", True), _user("alice"), _user("olivia"),
                                            _user("carol"), _user("bob"))
        a_id, m_id = alert_ids[r_all.id], alert_ids[r_map.id]   # rollback 으로 만료됐으므로 다시 읽는다
        a_alert = await service.get_alert(db, a_id)    # 출처 a(alice 생성, olivia 소유)
        m_alert = await service.get_alert(db, m_id)    # 출처 a → 영향 b(carol 생성)
        assert await service.can_update_alert(db, a_alert, alice, False)
        assert await service.can_update_alert(db, a_alert, olivia, False)
        assert not await service.can_update_alert(db, a_alert, carol, False)
        assert await service.can_update_alert(db, m_alert, carol, False)       # 영향 데이터셋 생성자
        assert not await service.can_update_alert(db, a_alert, bob, False)
        assert await service.can_update_alert(db, a_alert, bob, True)          # 규칙 관리자

        service.apply_status(a_alert, "ACKNOWLEDGED", "alice")
        assert a_alert.resolved_by == "alice" and a_alert.resolved_at is not None
        service.apply_status(a_alert, "OPEN", "alice")
        assert a_alert.resolved_by is None and a_alert.resolved_at is None
        service.apply_status(a_alert, "RESOLVED", "olivia")
        for bad in ("RESOLVED", "ACKNOWLEDGED", "DISMISSED"):
            try:
                service.apply_status(a_alert, bad, "x")
                raise AssertionError(f"RESOLVED → {bad} 허용하면 안 됨")
            except service.AlertTransitionError:
                pass
        service.apply_status(a_alert, "OPEN", "olivia")
        await db.commit()

        listed = await service.list_alerts(db, status="OPEN", page_size=100, current=carol, is_manager=False)
        can = {i.id: i.can_update for i in listed.items}
        assert can[m_alert.id] and not can[a_alert.id]

        res = await service.bulk_update_status(db, [a_alert.id, m_alert.id, 99999, a_alert.id], "DISMISSED",
                                               carol, False)
        await db.commit()
        assert res.updated == [m_alert.id], res
        reasons = {x["id"]: x["reason"] for x in res.skipped}
        assert "권한" in reasons[a_alert.id] and "찾을 수 없" in reasons[99999]
        assert m_alert.status == "DISMISSED" and m_alert.resolved_by == "carol"

        # ── 보관 정리 ──
        m_alert.resolved_at = datetime.now(timezone.utc) - timedelta(days=100)
        await db.commit()
        assert await service.purge_closed_alerts(db, 90) == 1
        await db.commit()
        assert await service.get_alert(db, m_alert.id) is None
        left = (await db.execute(select(AlertNotification).where(AlertNotification.alert_id == m_alert.id)))
        assert left.scalars().all() == []

        # ── 권한 키: 명시 허용형 ──
        assert "alerts.rules" in perm.DEFAULT_DENY_FEATURES
        assert not await perm.check_feature_granted(db, bob, "alerts.rules")
        assert await perm.check_feature_granted(db, admin, "alerts.rules")

    await _run_http(factory, ids)
    service._webhook_transport = None
    await engine.dispose()


async def _savepoint_isolation(factory, ids) -> None:
    """알림 평가가 실패해도 savepoint 덕분에 바깥 트랜잭션의 변경은 남는다."""
    async with factory() as db:
        db.add(cm.Tag(name="outer-change"))
        await db.flush()
        try:
            async with db.begin_nested():
                db.add(LineageAlert(alert_type="SCHEMA_CHANGE", severity="INFO", source_dataset_id=ids["a"],
                                    change_summary=None))  # NOT NULL 위반
                await db.flush()
        except Exception:  # noqa: BLE001
            pass
        await db.commit()
        assert (await db.execute(select(cm.Tag).where(cm.Tag.name == "outer-change"))).scalar_one()


async def _run_http(factory, ids) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.alert.router import router
    from app.core.auth import TokenUser, get_current_user
    from app.core.database import get_session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    who: dict = {"user": None}

    def _current():
        if who["user"] is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="unauthorized")
        return who["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current

    from app.core.config import settings

    def tu(name: str, admin: bool = False) -> TokenUser:
        return TokenUser(sub=name, username=name,
                         roles=[settings.auth_keycloak_admin_role] if admin else ["argus-user"])

    c = TestClient(app)
    assert c.get("/api/v1/alerts/summary").status_code == 401
    assert c.get("/api/v1/alerts/rules").status_code == 401

    body = {"rule_name": "h", "scope_type": "DATASET", "scope_id": ids["b"], "trigger_type": "ANY"}
    who["user"] = tu("bob")
    assert c.get("/api/v1/alerts").status_code == 200
    assert c.post("/api/v1/alerts/rules", json=body).status_code == 403
    who["user"] = tu("admin", True)
    assert who["user"].is_admin, "TokenUser admin 판정 확인"
    r = c.post("/api/v1/alerts/rules", json=body)
    assert r.status_code == 201 and r.json()["created_by"] == "admin", r.text
    assert c.post("/api/v1/alerts/rules", json={**body, "channels": "EMAIL"}).status_code == 422
    assert c.post("/api/v1/alerts/rules", json={**body, "scope_id": 9999}).status_code == 400
    rid = r.json()["id"]
    assert c.put(f"/api/v1/alerts/rules/{rid}", json={"is_active": False}).json()["is_active"] == "false"

    # 상태 변경 — 출처 a 알림: bob 403, alice 200, 같은 상태 409
    async with factory() as db:
        alert = LineageAlert(alert_type="SCHEMA_CHANGE", severity="WARNING", source_dataset_id=ids["a"],
                             change_summary="t")
        db.add(alert)
        await db.commit()
        aid = alert.id
    who["user"] = tu("bob")
    assert c.put(f"/api/v1/alerts/{aid}/status", json={"status": "RESOLVED"}).status_code == 403
    assert c.get(f"/api/v1/alerts/{aid}").json()["can_update"] is False
    who["user"] = tu("alice")
    r = c.put(f"/api/v1/alerts/{aid}/status", json={"status": "RESOLVED", "resolved_by": "someone-else"})
    assert r.status_code == 200 and r.json()["resolved_by"] == "alice", r.text
    assert c.put(f"/api/v1/alerts/{aid}/status", json={"status": "RESOLVED"}).status_code == 409
    assert c.delete("/api/v1/alerts/closed").status_code == 403
    assert c.put("/api/v1/alerts/99999/status", json={"status": "OPEN"}).status_code == 404

    await _savepoint_isolation(factory, ids)


def main() -> None:
    _test_schema_validation()
    asyncio.run(_run())
    print("OK")


def test_alerts() -> None:
    main()


if __name__ == "__main__":
    main()
