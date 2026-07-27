"""Yandex OAuth, Metrika, send, tracking, dashboard API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models.campaign import Campaign
from app.models.draft import EmailDraft
from app.models.provider import ProviderConfig
from app.models.sending import EmailEvent, EmailSend, EventType, SendStatus
from app.services import metrika as metrika_svc
from app.services import yandex_oauth as oauth
from app.services.send_campaign import (
    confirm_sends_via_imap,
    get_or_set_banner,
    send_progress,
    start_campaign_send,
)
from app.services.tracking import TRANSPARENT_GIF, parse_click_token

router = APIRouter(prefix="/api")


class OAuthExchangeIn(BaseModel):
    code: str = Field(min_length=1)


class OAuthTokenIn(BaseModel):
    access_token: str = Field(min_length=10)
    email: str = Field(min_length=3)
    refresh_token: str | None = None


class BannerIn(BaseModel):
    source_url: str | None = None
    content: str | None = None


class CampaignSendIn(BaseModel):
    draft_ids: list[int] | None = None
    limit: int | None = None
    track: bool = True
    throttle_seconds: float | None = None


class MetrikaConfigIn(BaseModel):
    metrika_counter_id: int | None = None
    metrika_goal_id: int | None = None
    landing_url: str | None = None


@router.get("/integrations/yandex/oauth/start")
async def yandex_oauth_start() -> dict:
    try:
        return {"authorize_url": oauth.authorize_url()}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/integrations/yandex/oauth/exchange")
async def yandex_oauth_exchange(
    payload: OAuthExchangeIn, db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        creds = await oauth.exchange_code(db, payload.code)
        await db.commit()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **oauth.status_payload(creds, True)}


@router.post("/integrations/yandex/oauth/token")
async def yandex_oauth_token(payload: OAuthTokenIn, db: AsyncSession = Depends(get_db)) -> dict:
    creds = await oauth.save_manual_token(
        db,
        access_token=payload.access_token,
        email=payload.email,
        refresh_token=payload.refresh_token,
    )
    await db.commit()
    return {"ok": True, **oauth.status_payload(creds, True)}


@router.get("/integrations/yandex/status")
async def yandex_status(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(ProviderConfig).where(ProviderConfig.type == oauth.PROVIDER_TYPE)
    )
    cfg = result.scalar_one_or_none()
    creds = oauth._parse_creds(cfg.credentials_json if cfg else None)
    return oauth.status_payload(creds, bool(cfg.enabled) if cfg else False)


@router.post("/integrations/yandex/disconnect")
async def yandex_disconnect(db: AsyncSession = Depends(get_db)) -> dict:
    await oauth.clear_credentials(db)
    await db.commit()
    return {"ok": True}


@router.get("/integrations/metrika/counters")
async def metrika_counters(db: AsyncSession = Depends(get_db)) -> list[dict]:
    try:
        return await metrika_svc.list_counters(db)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/integrations/metrika/counters/{counter_id}/goals")
async def metrika_goals(counter_id: int, db: AsyncSession = Depends(get_db)) -> list[dict]:
    try:
        return await metrika_svc.list_goals(db, counter_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/campaigns/{campaign_id}/metrika/report")
async def campaign_metrika_report(
    campaign_id: int, days: int = 30, db: AsyncSession = Depends(get_db)
) -> dict:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    if not campaign.metrika_counter_id:
        raise HTTPException(400, "Выберите счётчик Метрики для кампании")
    try:
        return await metrika_svc.fetch_email_utm_stats(
            db,
            counter_id=campaign.metrika_counter_id,
            goal_id=campaign.metrika_goal_id,
            days=days,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/campaigns/{campaign_id}/metrika-config")
async def patch_metrika_config(
    campaign_id: int, payload: MetrikaConfigIn, db: AsyncSession = Depends(get_db)
) -> dict:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    if payload.metrika_counter_id is not None:
        campaign.metrika_counter_id = payload.metrika_counter_id
    if payload.metrika_goal_id is not None:
        campaign.metrika_goal_id = payload.metrika_goal_id
    if payload.landing_url is not None:
        campaign.landing_url = payload.landing_url
    await db.commit()
    return {
        "ok": True,
        "metrika_counter_id": campaign.metrika_counter_id,
        "metrika_goal_id": campaign.metrika_goal_id,
        "landing_url": campaign.landing_url,
    }


@router.get("/campaigns/{campaign_id}/banner")
async def get_banner(campaign_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    asset = await get_or_set_banner(db, campaign_id)
    if not asset:
        return {"source_url": None, "content": None, "has_content": False}
    return {
        "source_url": asset.source_url,
        "content": (asset.content or "")[:2000],
        "has_content": bool(asset.content),
    }


@router.put("/campaigns/{campaign_id}/banner")
async def put_banner(
    campaign_id: int, payload: BannerIn, db: AsyncSession = Depends(get_db)
) -> dict:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    if not payload.source_url and payload.content is None:
        raise HTTPException(400, "Укажите source_url или content")
    try:
        asset = await get_or_set_banner(
            db,
            campaign_id,
            source_url=payload.source_url,
            content=payload.content,
        )
        await db.commit()
    except Exception as exc:
        raise HTTPException(400, f"Не удалось загрузить баннер: {exc}") from exc
    return {
        "ok": True,
        "source_url": asset.source_url if asset else None,
        "has_content": bool(asset and asset.content),
    }


@router.post("/campaigns/{campaign_id}/send")
async def campaign_send(
    campaign_id: int, payload: CampaignSendIn, db: AsyncSession = Depends(get_db)
) -> dict:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    result = await start_campaign_send(
        campaign_id,
        draft_ids=payload.draft_ids,
        limit=payload.limit,
        track=payload.track,
        throttle_seconds=payload.throttle_seconds,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "Ошибка отправки")
    return result


@router.get("/campaigns/{campaign_id}/send/progress")
async def campaign_send_progress(
    campaign_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    return await send_progress(db, campaign_id)


@router.post("/campaigns/{campaign_id}/send/confirm")
async def campaign_send_confirm(
    campaign_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        data = await confirm_sends_via_imap(db, campaign_id)
        await db.commit()
        return {"ok": True, **data}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/campaigns/{campaign_id}/dashboard")
async def campaign_dashboard(
    campaign_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    sends = list(
        (
            await db.execute(
                select(EmailSend)
                .where(EmailSend.campaign_id == campaign_id)
                .options(selectinload(EmailSend.events))
                .order_by(EmailSend.id.desc())
            )
        ).scalars().all()
    )

    draft_ids = list({s.draft_id for s in sends})
    drafts_by_id: dict[int, EmailDraft] = {}
    if draft_ids:
        draft_rows = (
            await db.execute(
                select(EmailDraft)
                .where(EmailDraft.id.in_(draft_ids))
                .options(selectinload(EmailDraft.recipient))
            )
        ).scalars().all()
        drafts_by_id = {d.id: d for d in draft_rows}

    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    items = []
    for s in sends:
        opens = [e for e in (s.events or []) if e.type == EventType.open]
        clicks = [e for e in (s.events or []) if e.type == EventType.click]
        unique_open = 1 if opens else 0
        draft = drafts_by_id.get(s.draft_id)
        company = (
            (draft.recipient.company_name if draft and draft.recipient else None)
            or None
        )
        items.append(
            {
                "send_id": s.id,
                "draft_id": s.draft_id,
                "to_email": s.to_email,
                "company_name": company,
                "subject": (draft.subject if draft else None),
                "has_html": bool(s.html_snapshot),
                "status": s.status.value,
                "sent_at": s.sent_at.isoformat() if s.sent_at else None,
                "confirmed_in_sent": s.confirmed_in_sent,
                "error": s.error,
                "opens": len(opens),
                "unique_open": unique_open,
                "clicks": len(clicks),
                "message_id": s.provider_message_id,
            }
        )

    progress = await send_progress(db, campaign_id)
    metrika = None
    if campaign.metrika_counter_id:
        try:
            metrika = await metrika_svc.fetch_email_utm_stats(
                db,
                counter_id=campaign.metrika_counter_id,
                goal_id=campaign.metrika_goal_id,
                days=30,
            )
        except Exception as exc:
            metrika = {"error": str(exc)}

    return {
        "campaign_id": campaign_id,
        "landing_url": campaign.landing_url,
        "metrika_counter_id": campaign.metrika_counter_id,
        "metrika_goal_id": campaign.metrika_goal_id,
        "progress": progress,
        "sends": items,
        "metrika": metrika,
    }


@router.get("/campaigns/{campaign_id}/sends/{send_id}")
async def campaign_send_preview(
    campaign_id: int, send_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    send = await db.get(EmailSend, send_id)
    if not send or send.campaign_id != campaign_id:
        raise HTTPException(404, "Отправка не найдена")
    draft = await db.get(
        EmailDraft, send.draft_id, options=(selectinload(EmailDraft.recipient),)
    )
    company = draft.recipient.company_name if draft and draft.recipient else None
    html = send.html_snapshot
    if not html and draft and draft.body_text:
        # plain fallback for preview
        from html import escape

        paras = "".join(
            f"<p style='margin:0 0 12px;line-height:1.5'>{escape(p).replace(chr(10), '<br>')}</p>"
            for p in (draft.body_text or "").split("\n\n")
            if p.strip()
        )
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
            f"<body style='font-family:Arial,sans-serif;padding:16px;color:#222'>"
            f"<h2 style='font-size:18px'>{escape(draft.subject or '')}</h2>{paras}</body></html>"
        )
    return {
        "send_id": send.id,
        "draft_id": send.draft_id,
        "to_email": send.to_email,
        "company_name": company,
        "subject": draft.subject if draft else None,
        "body_text": draft.body_text if draft else None,
        "html": html,
        "status": send.status.value,
        "sent_at": send.sent_at.isoformat() if send.sent_at else None,
        "error": send.error,
    }


# Public tracking endpoints (also mounted without /api prefix in main)
track_router = APIRouter()


@track_router.get("/t/o/{token}.gif")
@track_router.get("/t/o/{token}")
async def track_open(
    token: str, request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    clean = token.replace(".gif", "")
    result = await db.execute(select(EmailSend).where(EmailSend.tracking_token == clean))
    send = result.scalar_one_or_none()
    if send:
        # dedupe: one open per day
        since = datetime.now(timezone.utc) - timedelta(days=1)
        exists = await db.scalar(
            select(func.count())
            .select_from(EmailEvent)
            .where(
                EmailEvent.send_id == send.id,
                EmailEvent.type == EventType.open,
                EmailEvent.created_at >= since,
            )
        )
        if not exists:
            db.add(
                EmailEvent(
                    send_id=send.id,
                    type=EventType.open,
                    user_agent=(request.headers.get("user-agent") or "")[:512],
                    ip=request.client.host if request.client else None,
                )
            )
            await db.commit()
    return Response(content=TRANSPARENT_GIF, media_type="image/gif")


@track_router.get("/t/c/{token}")
async def track_click(
    token: str, request: Request, db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    parsed = parse_click_token(token)
    if not parsed:
        raise HTTPException(400, "Некорректная ссылка")
    send_token, url = parsed
    result = await db.execute(select(EmailSend).where(EmailSend.tracking_token == send_token))
    send = result.scalar_one_or_none()
    if send:
        db.add(
            EmailEvent(
                send_id=send.id,
                type=EventType.click,
                url=url[:2048],
                user_agent=(request.headers.get("user-agent") or "")[:512],
                ip=request.client.host if request.client else None,
            )
        )
        await db.commit()
    return RedirectResponse(url=url, status_code=302)
