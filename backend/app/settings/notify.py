# SPDX-License-Identifier: Apache-2.0
"""Slack / Mattermost 알림 발송 — DB(catalog_configuration, category='notify') 설정 사용.

단일 설정(비-멀티테넌트) + incoming webhook 방식의 notify 서비스.
Slack·Mattermost 모두 incoming webhook 은 동일하게
``{text, channel?, username?, icon_emoji?}`` JSON 을 받으므로 단일 페이로드로 처리.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings.service import get_config_by_category

logger = logging.getLogger(__name__)


class SlackConfig(BaseModel):
    webhook_url: str = ""          # Slack 수신 webhook URL
    channel: str = ""              # 선택 (#channel) — webhook 기본 채널 override
    username: str = ""             # 선택 — 표시 이름 override
    icon_emoji: str = ":bell:"     # 선택 (예: :bell:)


class MattermostConfig(BaseModel):
    webhook_url: str = ""          # Mattermost 수신 webhook URL
    channel: str = ""              # 선택 (채널명)
    username: str = ""             # 선택
    icon_emoji: str = ""           # 선택


class NotifyConfig(BaseModel):
    enabled: bool = False
    provider: str = "slack"        # "slack" | "mattermost"
    timeout_seconds: int = 10
    slack: SlackConfig = SlackConfig()
    mattermost: MattermostConfig = MattermostConfig()


def _bool(v: str | bool) -> bool:
    return str(v).lower() in ("true", "1", "yes")


async def load_notify_config(session: AsyncSession) -> NotifyConfig:
    """DB(category='notify')에서 알림 설정을 읽어 NotifyConfig 로 구성."""
    cfg = await get_config_by_category(session, "notify")
    return NotifyConfig(
        enabled=_bool(cfg.get("notify_enabled", "false")),
        provider=cfg.get("notify_provider", "slack"),
        timeout_seconds=int(cfg.get("notify_timeout_seconds", "10") or 10),
        slack=SlackConfig(
            webhook_url=cfg.get("notify_slack_webhook_url", ""),
            channel=cfg.get("notify_slack_channel", ""),
            username=cfg.get("notify_slack_username", ""),
            icon_emoji=cfg.get("notify_slack_icon_emoji", ":bell:"),
        ),
        mattermost=MattermostConfig(
            webhook_url=cfg.get("notify_mattermost_webhook_url", ""),
            channel=cfg.get("notify_mattermost_channel", ""),
            username=cfg.get("notify_mattermost_username", ""),
            icon_emoji=cfg.get("notify_mattermost_icon_emoji", ""),
        ),
    )


def _active(cfg: NotifyConfig) -> SlackConfig | MattermostConfig:
    return cfg.mattermost if cfg.provider == "mattermost" else cfg.slack


async def send_notify(
    cfg: NotifyConfig,
    text: str,
    channel: str | None = None,
    username: str | None = None,
    raise_errors: bool = False,
) -> bool:
    """활성 provider 의 incoming webhook 으로 메시지 발송.

    비활성/미설정/실패 시 False 반환(raise_errors=True 면 예외 전파).
    """
    if not cfg.enabled:
        logger.info("알림 비활성화; 건너뜀 (provider=%s)", cfg.provider)
        if raise_errors:
            raise RuntimeError("알림이 비활성화되어 있습니다.")
        return False

    sub = _active(cfg)
    if not sub.webhook_url:
        if raise_errors:
            raise RuntimeError(f"{cfg.provider} webhook URL 이 설정되지 않았습니다.")
        logger.warning("알림 webhook 이 설정되지 않음 (provider=%s)", cfg.provider)
        return False

    # Slack·Mattermost incoming webhook 공통 페이로드.
    payload: dict[str, str] = {"text": text}
    ch = channel or sub.channel
    if ch:
        payload["channel"] = ch
    un = username or sub.username
    if un:
        payload["username"] = un
    if sub.icon_emoji:
        payload["icon_emoji"] = sub.icon_emoji

    try:
        import httpx

        async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
            resp = await client.post(sub.webhook_url, json=payload)
        if resp.status_code >= 400:
            msg = f"webhook 응답 {resp.status_code}: {resp.text[:200]}"
            if raise_errors:
                raise RuntimeError(msg)
            logger.warning("알림 발송 실패: %s", msg)
            return False
        logger.info("%s 로 알림 발송됨", cfg.provider)
        return True
    except Exception as exc:
        logger.warning("알림 발송 실패: %s", exc, exc_info=True)
        if raise_errors:
            raise
        return False


async def send_notify_db(
    session: AsyncSession,
    text: str,
    channel: str | None = None,
    username: str | None = None,
) -> bool:
    """DB 설정을 읽어 발송하는 편의 함수(다른 기능에서 재사용)."""
    cfg = await load_notify_config(session)
    return await send_notify(cfg, text, channel=channel, username=username)
