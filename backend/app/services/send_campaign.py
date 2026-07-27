"""Campaign send orchestration with throttle, banner, tracking."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models.campaign import Campaign
from app.models.draft import DraftStatus, EmailDraft
from app.models.sending import AssetKind, CampaignAsset, EmailSend, SendStatus
from app.providers.base import OutboundMessage
from app.services.tracking import (
    build_email_html,
    ensure_landing_in_plain,
    fetch_banner_html,
    new_tracking_token,
)
from app.services.yandex_mail import smtp_send_one, verify_in_sent_folder
from app.services.yandex_oauth import get_valid_access_token

logger = logging.getLogger(__name__)

_running_sends: set[int] = set()


def is_send_running(campaign_id: int) -> bool:
    return campaign_id in _running_sends


async def get_or_set_banner(
    session: AsyncSession,
    campaign_id: int,
    *,
    source_url: str | None = None,
    content: str | None = None,
) -> CampaignAsset | None:
    result = await session.execute(
        select(CampaignAsset).where(CampaignAsset.campaign_id == campaign_id)
    )
    assets = list(result.scalars().all())
    by_kind = {a.kind: a for a in assets}

    if content is not None:
        asset = by_kind.get(AssetKind.banner_html) or CampaignAsset(
            campaign_id=campaign_id, kind=AssetKind.banner_html
        )
        asset.content = content
        asset.source_url = source_url
        session.add(asset)
        await session.flush()
        return asset

    if source_url:
        html = await fetch_banner_html(source_url)
        asset = by_kind.get(AssetKind.banner_url) or CampaignAsset(
            campaign_id=campaign_id, kind=AssetKind.banner_url
        )
        asset.source_url = source_url
        asset.content = html
        session.add(asset)
        html_asset = by_kind.get(AssetKind.banner_html) or CampaignAsset(
            campaign_id=campaign_id, kind=AssetKind.banner_html
        )
        html_asset.content = html
        html_asset.source_url = source_url
        session.add(html_asset)
        await session.flush()
        return asset

    return by_kind.get(AssetKind.banner_html) or by_kind.get(AssetKind.banner_url)


async def resolve_banner_html(session: AsyncSession, campaign_id: int) -> str | None:
    result = await session.execute(
        select(CampaignAsset)
        .where(CampaignAsset.campaign_id == campaign_id)
        .order_by(CampaignAsset.id.desc())
    )
    for asset in result.scalars().all():
        if asset.content:
            return asset.content
        if asset.source_url:
            try:
                html = await fetch_banner_html(asset.source_url)
                asset.content = html
                await session.flush()
                return html
            except Exception as exc:
                logger.warning("Banner fetch failed: %s", exc)
    return None


async def start_campaign_send(
    campaign_id: int,
    *,
    draft_ids: list[int] | None = None,
    limit: int | None = None,
    track: bool = True,
    throttle_seconds: float | None = None,
) -> dict[str, Any]:
    if campaign_id in _running_sends:
        return {"ok": False, "error": "Отправка уже запущена"}
    _running_sends.add(campaign_id)
    settings = get_settings()
    delay = (
        throttle_seconds
        if throttle_seconds is not None
        else float(settings.send_throttle_seconds)
    )

    try:
        async with AsyncSessionLocal() as session:
            campaign = await session.get(Campaign, campaign_id)
            if not campaign:
                _running_sends.discard(campaign_id)
                return {"ok": False, "error": "Кампания не найдена"}
            access, from_email = await get_valid_access_token(session)
            banner_html = await resolve_banner_html(session, campaign_id)
            landing = campaign.landing_url or "https://comquaagent.copp-hightech.ru/"

            q = (
                select(EmailDraft)
                .where(EmailDraft.campaign_id == campaign_id)
                .where(EmailDraft.status.in_([DraftStatus.ready, DraftStatus.edited]))
                .options(selectinload(EmailDraft.recipient))
                .order_by(EmailDraft.id)
            )
            if draft_ids:
                q = q.where(EmailDraft.id.in_(draft_ids))
            result = await session.execute(q)
            drafts = list(result.scalars().all())

            sent_result = await session.execute(
                select(EmailSend.draft_id).where(
                    EmailSend.campaign_id == campaign_id,
                    EmailSend.status == SendStatus.sent,
                )
            )
            already = set(sent_result.scalars().all())
            drafts = [d for d in drafts if d.id not in already]
            if limit is not None:
                drafts = drafts[: max(0, int(limit))]

            if not drafts:
                _running_sends.discard(campaign_id)
                return {"ok": True, "queued": 0, "message": "Нет писем для отправки"}

            send_ids: list[int] = []
            for draft in drafts:
                assert draft.recipient
                token = new_tracking_token()
                html_body = build_email_html(
                    body_text=draft.body_text or "",
                    banner_html=banner_html,
                    landing_url=landing,
                    draft_id=draft.id,
                    send_token=token,
                    track=track,
                )
                row = EmailSend(
                    campaign_id=campaign_id,
                    draft_id=draft.id,
                    to_email=draft.recipient.email,
                    status=SendStatus.queued,
                    html_snapshot=html_body,
                    tracking_token=token,
                )
                session.add(row)
                await session.flush()
                send_ids.append(row.id)
            await session.commit()

        asyncio.create_task(
            _run_send_loop(
                campaign_id,
                send_ids,
                from_email=from_email,
                access_token=access,
                delay=delay,
            )
        )
        return {"ok": True, "queued": len(send_ids), "send_ids": send_ids}
    except Exception as exc:
        _running_sends.discard(campaign_id)
        logger.exception("start_campaign_send failed")
        return {"ok": False, "error": str(exc)}


async def _run_send_loop(
    campaign_id: int,
    send_ids: list[int],
    *,
    from_email: str,
    access_token: str,
    delay: float,
) -> None:
    try:
        for idx, send_id in enumerate(send_ids):
            async with AsyncSessionLocal() as session:
                if idx > 0 and idx % 5 == 0:
                    try:
                        access_token, from_email = await get_valid_access_token(session)
                    except Exception:
                        pass

                row = await session.get(EmailSend, send_id)
                if not row:
                    continue
                draft = await session.get(
                    EmailDraft,
                    row.draft_id,
                    options=(selectinload(EmailDraft.recipient),),
                )
                campaign = await session.get(Campaign, campaign_id)
                landing = (
                    (campaign.landing_url if campaign else None)
                    or "https://comquaagent.copp-hightech.ru/"
                )
                if not draft or not draft.recipient:
                    row.status = SendStatus.failed
                    row.error = "Черновик не найден"
                    await session.commit()
                    continue

                row.status = SendStatus.sending
                await session.commit()

                plain = ensure_landing_in_plain(draft.body_text or "", landing, draft.id)
                msg = OutboundMessage(
                    to_email=row.to_email,
                    subject=draft.subject or "(без темы)",
                    body_text=plain,
                    body_html=row.html_snapshot,
                )
                result = await smtp_send_one(
                    from_email=from_email,
                    access_token=access_token,
                    message=msg,
                )
                row = await session.get(EmailSend, send_id)
                if not row:
                    continue
                if result.success:
                    row.status = SendStatus.sent
                    row.sent_at = datetime.now(timezone.utc)
                    row.provider_message_id = result.provider_message_id
                    row.error = None
                else:
                    row.status = SendStatus.failed
                    row.error = result.error
                await session.commit()

            if idx < len(send_ids) - 1 and delay > 0:
                await asyncio.sleep(delay)
    finally:
        _running_sends.discard(campaign_id)


async def confirm_sends_via_imap(session: AsyncSession, campaign_id: int) -> dict[str, Any]:
    access, email_addr = await get_valid_access_token(session)
    result = await session.execute(
        select(EmailSend).where(
            EmailSend.campaign_id == campaign_id,
            EmailSend.status == SendStatus.sent,
            EmailSend.provider_message_id.is_not(None),
        )
    )
    rows = list(result.scalars().all())
    mids = [r.provider_message_id for r in rows if r.provider_message_id]
    tos = [r.to_email for r in rows if r.to_email]
    found = await verify_in_sent_folder(
        email_addr=email_addr,
        access_token=access,
        message_ids=mids,
        to_emails=tos,
    )
    confirmed = 0
    for row in rows:
        mid = row.provider_message_id
        if mid and found.get(mid):
            row.confirmed_in_sent = True
            confirmed += 1
    await session.flush()
    note = None
    if rows and confirmed == 0:
        note = (
            "Копии в папке Sent не найдены. Яндекс не сохраняет SMTP-отправки автоматически — "
            "новые письма после этого обновления копируются в Sent сами. "
            "Уже отправленные без копии подтвердить через IMAP нельзя."
        )
    elif not rows:
        note = "Нет писем со статусом sent для проверки."
    return {
        "checked": len(rows),
        "confirmed": confirmed,
        "note": note,
        "details": [
            {
                "send_id": r.id,
                "message_id": r.provider_message_id,
                "to_email": r.to_email,
                "ok": found.get(r.provider_message_id or "", False),
            }
            for r in rows
        ],
    }


async def send_progress(session: AsyncSession, campaign_id: int) -> dict[str, Any]:
    result = await session.execute(
        select(EmailSend).where(EmailSend.campaign_id == campaign_id).order_by(EmailSend.id.desc())
    )
    rows = list(result.scalars().all())
    counts = {s.value: 0 for s in SendStatus}
    for r in rows:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    return {
        "campaign_id": campaign_id,
        "running": is_send_running(campaign_id),
        "total": len(rows),
        "counts": counts,
        "confirmed_in_sent": sum(1 for r in rows if r.confirmed_in_sent),
    }
