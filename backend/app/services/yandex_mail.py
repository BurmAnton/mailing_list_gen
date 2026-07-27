"""SMTP XOAUTH2 helpers and Yandex provider."""

from __future__ import annotations

import base64
import email.utils
import logging
from email.message import EmailMessage

import aiosmtplib

from app.providers.base import OutboundMessage, SendItemResult, SendResult

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.yandex.com"
SMTP_PORT = 465
IMAP_HOST = "imap.yandex.com"
IMAP_PORT = 993


def xoauth2_b64(user: str, access_token: str) -> str:
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def build_mime(
    *,
    from_email: str,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    message_id: str | None = None,
    from_name: str | None = "ComQuaAgent",
) -> EmailMessage:
    msg = EmailMessage()
    if from_name:
        msg["From"] = email.utils.formataddr((from_name, from_email))
    else:
        msg["From"] = from_email
    msg["To"] = to_email
    msg["Reply-To"] = from_email
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    # Helps Gmail treat as legitimate correspondence / bulk with opt-out path
    msg["List-Unsubscribe"] = f"<mailto:{from_email}?subject=unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if message_id:
        msg["Message-ID"] = message_id
    else:
        domain = from_email.split("@")[-1] if "@" in from_email else "yandex.ru"
        msg["Message-ID"] = email.utils.make_msgid(domain=domain)
    msg.set_content(body_text or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg


def _norm_mid(mid: str) -> str:
    return mid.strip().strip("<>").lower()


def _header_blob(fetch_data: object) -> str:
    parts: list[str] = []
    for part in fetch_data or []:  # type: ignore[union-attr]
        if isinstance(part, bytearray):
            parts.append(part.decode("utf-8", errors="replace"))
        elif isinstance(part, bytes) and b":" in part[:120]:
            parts.append(part.decode("utf-8", errors="replace"))
    return "\n".join(parts)


async def append_to_sent(
    *,
    email_addr: str,
    access_token: str,
    message_bytes: bytes,
) -> bool:
    """Yandex SMTP does not auto-save copies; APPEND into Sent ourselves."""
    try:
        from aioimaplib import IMAP4_SSL
    except ImportError:
        logger.warning("aioimaplib not installed; cannot APPEND to Sent")
        return False
    try:
        imap = IMAP4_SSL(host=IMAP_HOST, port=IMAP_PORT, timeout=60)
        await imap.wait_hello_from_server()
        await imap.xoauth2(email_addr, access_token)
        res = await imap.append(message_bytes, mailbox="Sent", flags=r"(\Seen)")
        await imap.logout()
        ok = getattr(res, "result", None) == "OK"
        if not ok:
            logger.warning("IMAP APPEND to Sent failed: %s", res)
        return ok
    except Exception as exc:
        logger.warning("IMAP APPEND to Sent failed: %s", exc)
        return False


async def smtp_send_one(
    *,
    from_email: str,
    access_token: str,
    message: OutboundMessage,
    message_id: str | None = None,
    save_to_sent: bool = True,
) -> SendItemResult:
    mime = build_mime(
        from_email=from_email,
        to_email=message.to_email,
        subject=message.subject,
        body_text=message.body_text,
        body_html=message.body_html,
        message_id=message_id,
    )
    mid = str(mime["Message-ID"] or "")
    try:
        smtp = aiosmtplib.SMTP(
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            use_tls=True,
            timeout=90,
        )
        await smtp.connect()
        auth_line = f"XOAUTH2 {xoauth2_b64(from_email, access_token)}"
        response = await smtp.execute_command(b"AUTH", auth_line.encode("ascii"))
        if response.code != 235:
            raise RuntimeError(f"XOAUTH2 rejected: {response.code} {response.message}")
        await smtp.send_message(mime)
        await smtp.quit()
        if save_to_sent:
            raw = mime.as_bytes()
            saved = await append_to_sent(
                email_addr=from_email, access_token=access_token, message_bytes=raw
            )
            if not saved:
                logger.warning("Sent SMTP ok but Sent-folder copy failed for %s", message.to_email)
        return SendItemResult(
            to_email=message.to_email,
            success=True,
            provider_message_id=mid or None,
        )
    except Exception as exc:
        logger.exception("SMTP send failed to %s", message.to_email)
        return SendItemResult(
            to_email=message.to_email,
            success=False,
            error=str(exc),
        )


async def verify_in_sent_folder(
    *,
    email_addr: str,
    access_token: str,
    message_ids: list[str],
    to_emails: list[str] | None = None,
) -> dict[str, bool]:
    """Match Sent copies. Yandex HEADER Message-ID SEARCH is unreliable; use TO + header fetch."""
    found = {mid: False for mid in message_ids if mid}
    if not found:
        return found
    try:
        from aioimaplib import IMAP4_SSL
    except ImportError:
        logger.warning("aioimaplib not installed")
        return found

    mid_by_norm = {_norm_mid(mid): mid for mid in found}
    tos = [t for t in (to_emails or []) if t]
    try:
        imap = IMAP4_SSL(host=IMAP_HOST, port=IMAP_PORT, timeout=90)
        await imap.wait_hello_from_server()
        auth = await imap.xoauth2(email_addr, access_token)
        if getattr(auth, "result", None) != "OK":
            raise RuntimeError(f"IMAP XOAUTH2 failed: {auth}")

        selected = False
        # Prefer English «Sent» (Yandex 360); Cyrillic name often fails encoding.
        for folder in ("Sent", "INBOX.Sent", "Отправленные", '"Sent Items"'):
            try:
                res = await imap.select(folder)
                if getattr(res, "result", None) == "OK":
                    selected = True
                    break
            except Exception:
                continue
        if not selected:
            await imap.logout()
            raise RuntimeError("Не удалось открыть папку Sent в IMAP")

        seq_ids: set[str] = set()
        for to in tos:
            try:
                typ, data = await imap.search(f'TO "{to}"')
                if typ == "OK" and data and data[0]:
                    seq_ids.update(data[0].decode().split())
            except Exception as exc:
                logger.debug("IMAP TO search failed for %s: %s", to, exc)

        if not seq_ids:
            await imap.logout()
            return found

        for seq in seq_ids:
            try:
                _typ, data = await imap.fetch(
                    seq, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID TO)])"
                )
                blob = _header_blob(data).lower()
                for norm, original in mid_by_norm.items():
                    if norm and norm in blob.replace("<", "").replace(">", ""):
                        found[original] = True
                    elif original.lower() in blob:
                        found[original] = True
            except Exception as exc:
                logger.debug("IMAP fetch failed for %s: %s", seq, exc)

        await imap.logout()
    except Exception as exc:
        logger.warning("IMAP verify failed: %s", exc)
        raise
    return found


class YandexSmtpProvider:
    type = "yandex_smtp"
    display_name = "Яндекс.Почта (SMTP XOAUTH2)"
    description = "Отправка через smtp.yandex.com с OAuth (mail:smtp)."

    def __init__(self, *, from_email: str | None = None, access_token: str | None = None) -> None:
        self.from_email = from_email
        self.access_token = access_token

    @property
    def available(self) -> bool:
        return bool(self.from_email and self.access_token)

    async def validate(self) -> bool:
        return self.available

    async def send_batch(self, messages: list[OutboundMessage]) -> SendResult:
        if not self.available:
            return SendResult(
                provider=self.type,
                sent=0,
                failed=len(messages),
                message="Яндекс OAuth не подключён",
            )
        items: list[SendItemResult] = []
        for msg in messages:
            assert self.from_email and self.access_token
            items.append(
                await smtp_send_one(
                    from_email=self.from_email,
                    access_token=self.access_token,
                    message=msg,
                )
            )
        sent = sum(1 for i in items if i.success)
        return SendResult(
            provider=self.type,
            sent=sent,
            failed=len(items) - sent,
            items=items,
            message="ok" if sent else "all failed",
        )
