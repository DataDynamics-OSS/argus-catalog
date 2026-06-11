# SPDX-License-Identifier: Apache-2.0
"""이메일(SMTP) 발송 — DB(catalog_configuration, category='mail')의 설정을 사용.

단일 설정(비-멀티테넌트) 환경용 MailService.
- ``aiosmtplib`` 로 비동기 발송. 미설치/미설정 시 예외 없이 False 반환(graceful).
- HTML 메일은 multipart/alternative(plain fallback + html) 로 구성.
"""

from __future__ import annotations

import logging
import re
import ssl
from email.message import EmailMessage

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings.service import get_config_by_category

logger = logging.getLogger(__name__)


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    use_tls: bool = True   # STARTTLS (587)
    use_ssl: bool = False  # implicit SSL (465)
    timeout_seconds: int = 10
    from_email: str = ""
    from_name: str = "Argus Catalog"
    smtp_user: str = ""    # 비우면 from_email 로 인증
    smtp_password: str = ""
    subject_prefix: str = "[Argus Catalog]"
    default_recipients: str = ""  # 쉼표 구분


def _bool(v: str | bool) -> bool:
    return str(v).lower() in ("true", "1", "yes")


async def load_mail_config(session: AsyncSession) -> EmailConfig:
    """DB(category='mail')에서 메일 설정을 읽어 EmailConfig 로 구성."""
    cfg = await get_config_by_category(session, "mail")
    return EmailConfig(
        enabled=_bool(cfg.get("mail_enabled", "false")),
        smtp_host=cfg.get("mail_smtp_host", "smtp.gmail.com"),
        smtp_port=int(cfg.get("mail_smtp_port", "587") or 587),
        use_tls=_bool(cfg.get("mail_use_tls", "true")),
        use_ssl=_bool(cfg.get("mail_use_ssl", "false")),
        timeout_seconds=int(cfg.get("mail_timeout_seconds", "10") or 10),
        from_email=cfg.get("mail_from_email", ""),
        from_name=cfg.get("mail_from_name", "Argus Catalog"),
        smtp_user=cfg.get("mail_smtp_user", ""),
        smtp_password=cfg.get("mail_smtp_password", ""),
        subject_prefix=cfg.get("mail_subject_prefix", "[Argus Catalog]"),
        default_recipients=cfg.get("mail_default_recipients", ""),
    )


def _strip_html(html: str) -> str:
    """HTML → 거친 plain text (multipart/alternative fallback 용)."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>|</div>|</li>|</h[1-6]>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _recipients(cfg: EmailConfig, to: list[str] | None) -> list[str]:
    recipients = list(to or [])
    if not recipients and cfg.default_recipients:
        recipients = [r.strip() for r in cfg.default_recipients.split(",")]
    return [r for r in recipients if r]


async def send_mail(
    cfg: EmailConfig,
    subject: str,
    body: str,
    to: list[str] | None = None,
    html: bool = False,
    attachments: list[tuple[str, bytes, str]] | None = None,
    raise_errors: bool = False,
) -> bool:
    """메일 발송. 비활성/미설정/실패 시 False 반환(raise_errors=True 면 예외 전파).

    attachments: [(filename, content_bytes, mime_type), ...]
    """
    if not cfg.enabled:
        logger.info("메일 비활성화; 발송 건너뜀 (subject=%r)", subject)
        if raise_errors:
            raise RuntimeError("메일 발송이 비활성화되어 있습니다.")
        return False
    if not cfg.from_email or not cfg.smtp_password:
        logger.warning("메일 발신자가 설정되지 않음; 발송 건너뜀")
        if raise_errors:
            raise RuntimeError("발신자 이메일/비밀번호가 설정되지 않았습니다.")
        return False

    recipients = _recipients(cfg, to)
    if not recipients:
        if raise_errors:
            raise RuntimeError("수신자가 없습니다.")
        logger.warning("수신자 없음; 발송 건너뜀 (subject=%r)", subject)
        return False

    msg = EmailMessage()
    msg["From"] = f"{cfg.from_name} <{cfg.from_email}>"
    msg["To"] = ", ".join(recipients)
    prefix = cfg.subject_prefix.strip()
    msg["Subject"] = f"{prefix} {subject}" if prefix else subject

    if html:
        plain_fallback = _strip_html(body) or "HTML 본문은 메일 클라이언트의 HTML 보기에서 확인하세요."
        msg.set_content(plain_fallback, subtype="plain")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body, subtype="plain")

    for filename, content, mime in attachments or []:
        maintype, _, subtype = mime.partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    try:
        import aiosmtplib
    except ImportError:
        logger.warning("aiosmtplib 미설치 — `pip install aiosmtplib` 필요. 메일 발송 스킵.")
        if raise_errors:
            raise RuntimeError("aiosmtplib 가 설치되어 있지 않습니다. `pip install aiosmtplib` 후 다시 시도하세요.")
        return False

    try:
        await aiosmtplib.send(
            msg,
            hostname=cfg.smtp_host,
            port=cfg.smtp_port,
            username=cfg.smtp_user or cfg.from_email,
            password=cfg.smtp_password,
            start_tls=cfg.use_tls and not cfg.use_ssl,
            use_tls=cfg.use_ssl,
            timeout=cfg.timeout_seconds,
            tls_context=ssl.create_default_context() if cfg.use_ssl else None,
        )
        logger.info("메일 발송됨: subject=%r to=%s", subject, recipients)
        return True
    except Exception as exc:
        logger.warning("메일 발송 실패: %s", exc, exc_info=True)
        if raise_errors:
            raise
        return False


async def send_mail_db(
    session: AsyncSession,
    subject: str,
    body: str,
    to: list[str] | None = None,
    html: bool = False,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> bool:
    """DB 설정을 읽어 발송하는 편의 함수(다른 기능에서 재사용)."""
    cfg = await load_mail_config(session)
    return await send_mail(cfg, subject, body, to=to, html=html, attachments=attachments)
