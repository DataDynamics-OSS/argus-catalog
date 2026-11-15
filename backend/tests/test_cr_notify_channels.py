# SPDX-License-Identifier: Apache-2.0
"""소비자 통지 채널별 실제 발송·SKIPPED 기록 (DEF-005, IT CHG-T08).

- EMAIL 은 소비자 담당자 이메일로, SLACK 은 메신저 설정의 webhook 으로 소비자 채널에 보낸다.
- 받는 곳이 없거나 발송 설정이 꺼져 있거나 보낼 수단이 없는 채널(SMS·IN_APP)은 보내지 않고 ``SKIPPED`` + 사유.
  예전에는 보내지 않고 ``SENT`` 로 남겼다.
- 상신 API 는 모르는 채널·빈 목록을 400 으로 거부하고, 기본 채널은 WEBHOOK·EMAIL.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_cr_notify_channels.py
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.settings.mail as mail_mod
import app.settings.notify as notify_mod
from app.change_mgmt import activities as acts
from app.change_mgmt import models as chm
from app.change_mgmt.router import _normalize_channels
from tests.test_cr_notification_ack import _with_fk_targets


def _setup(monkeypatch, *, mail_on=True, notify_on=True, mail_error=None):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(acts, "async_session_factory", factory)

    mails: list[tuple[str, list[str]]] = []
    chats: list[tuple[str, str]] = []

    async def load_mail(_s):
        return mail_mod.EmailConfig(enabled=mail_on, from_email="argus@example.com")

    async def fake_mail(_cfg, subject, _body, to=None, **_k):
        if mail_error:
            raise RuntimeError(mail_error)
        mails.append((subject, to))
        return True

    async def load_notify(_s):
        return notify_mod.NotifyConfig(enabled=notify_on)

    async def fake_notify(_cfg, text, channel=None, **_k):
        chats.append((text, channel))
        return True

    monkeypatch.setattr(mail_mod, "load_mail_config", load_mail)
    monkeypatch.setattr(mail_mod, "send_mail", fake_mail)
    monkeypatch.setattr(notify_mod, "load_notify_config", load_notify)
    monkeypatch.setattr(notify_mod, "send_notify", fake_notify)

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sc: cm.Base.metadata.create_all(
                sc, tables=_with_fk_targets([chm.NotificationLog.__table__])))
        async with factory() as db:
            src = cm.Datasource(datasource_id="m", name="m", type="mysql")
            db.add(src)
            await db.flush()
            ds = cm.Dataset(urn="m.db.t.dataset", name="db.t", datasource_id=src.id, origin="DEV",
                            qualified_name="m.db.t", status="active")
            db.add(ds)
            await db.flush()
            cr = chm.ChangeRequest(cr_code="CR-1", title="컬럼 삭제", status="APPROVED", dataset_id=ds.id,
                                   change_type="BREAKING", rollback_plan="r", business_justification="b",
                                   requested_by="kim")
            db.add(cr)
            db.add(chm.Consumer(dataset_id=ds.id, consumer_name="full",
                                contact_emails="a@example.com, b@example.com", slack_channel="#etl"))
            db.add(chm.Consumer(dataset_id=ds.id, consumer_name="bare"))
            await db.commit()
            return cr.id

    async def logs(cr_id):
        async with factory() as db:
            rows = (await db.execute(
                select(chm.NotificationLog, chm.Consumer.consumer_name)
                .join(chm.Consumer, chm.Consumer.id == chm.NotificationLog.consumer_id)
                .where(chm.NotificationLog.cr_id == cr_id)
            )).all()
            return {(name, log.channel): (log.status, log.error, log.sent_at) for log, name in rows}

    return seed, logs, mails, chats


def _run(seed, channels):
    cr_id = asyncio.run(seed())
    asyncio.run(acts.send_notifications(acts.NotifyInput(cr_id=cr_id, stage="APPLIED", channels=channels)))
    return cr_id


def test_email_and_slack_are_sent_to_consumer_contacts(monkeypatch) -> None:
    seed, logs, mails, chats = _setup(monkeypatch)
    cr_id = _run(seed, ["EMAIL", "SLACK"])
    r = asyncio.run(logs(cr_id))
    assert r[("full", "EMAIL")][:2] == ("SENT", None) and r[("full", "EMAIL")][2] is not None
    assert r[("full", "SLACK")][:2] == ("SENT", None)
    assert mails == [("[변경요청 CR-1] db.t 스키마 변경 통지 (적용 완료)", ["a@example.com", "b@example.com"])]
    assert len(chats) == 1 and chats[0][1] == "#etl" and "CR-1" in chats[0][0]
    # 받는 곳이 없는 소비자는 보내지 않고 사유를 남긴다(예전에는 SENT)
    assert r[("bare", "EMAIL")][:2] == ("SKIPPED", "소비자에 담당자 이메일이 등록되어 있지 않습니다")
    assert r[("bare", "SLACK")][:2] == ("SKIPPED", "소비자에 Slack 채널이 등록되어 있지 않습니다")
    assert r[("bare", "EMAIL")][2] is None


def test_disabled_settings_and_unsupported_channels_are_skipped(monkeypatch) -> None:
    seed, logs, mails, chats = _setup(monkeypatch, mail_on=False, notify_on=False)
    cr_id = _run(seed, ["EMAIL", "SLACK", "SMS", "IN_APP", "WEBHOOK"])
    r = asyncio.run(logs(cr_id))
    assert r[("full", "EMAIL")][:2] == ("SKIPPED", "메일 발송이 꺼져 있습니다(설정 > 메일)")
    assert r[("full", "SLACK")][:2] == ("SKIPPED", "메신저 알림이 꺼져 있습니다(설정 > 알림)")
    assert r[("full", "SMS")][0] == "SKIPPED" and r[("full", "IN_APP")][0] == "SKIPPED"
    assert r[("full", "WEBHOOK")][:2] == ("SKIPPED", "소비자에 webhook URL 이 등록되어 있지 않습니다")
    assert not mails and not chats
    assert all(status != "SENT" for status, _e, _t in r.values())


def test_mail_failure_is_failed_with_reason(monkeypatch) -> None:
    seed, logs, _mails, _chats = _setup(monkeypatch, mail_error="SMTP 연결 거부")
    cr_id = _run(seed, ["EMAIL"])
    assert asyncio.run(logs(cr_id))[("full", "EMAIL")][:2] == ("FAILED", "SMTP 연결 거부")


def test_submit_channels_are_validated() -> None:
    assert _normalize_channels(["webhook", "EMAIL", "email", " slack "]) == ["WEBHOOK", "EMAIL", "SLACK"]
    assert _normalize_channels(["IN_APP"]) == ["IN_APP"]  # 예전 호출 호환(보내지 않고 SKIPPED)
    with pytest.raises(HTTPException) as e:
        _normalize_channels(["EMAIL", "FAX"])
    assert e.value.status_code == 400 and "FAX" in e.value.detail
    with pytest.raises(HTTPException) as e:
        _normalize_channels([])
    assert e.value.status_code == 400
