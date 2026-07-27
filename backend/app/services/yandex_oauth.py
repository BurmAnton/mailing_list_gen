"""Yandex OAuth token storage and exchange."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.provider import ProviderConfig

logger = logging.getLogger(__name__)

PROVIDER_TYPE = "yandex"
PROVIDER_DISPLAY = "Яндекс (Почта + Метрика)"


def authorize_url() -> str:
    settings = get_settings()
    if not settings.yandex_oauth_client_id:
        raise RuntimeError("YANDEX_OAUTH_CLIENT_ID не задан в .env")
    params = {
        "response_type": "code",
        "client_id": settings.yandex_oauth_client_id,
        "redirect_uri": settings.yandex_oauth_redirect_uri,
        "scope": settings.yandex_oauth_scopes,
    }
    return "https://oauth.yandex.ru/authorize?" + urlencode(params)


async def _get_or_create_config(session: AsyncSession) -> ProviderConfig:
    result = await session.execute(
        select(ProviderConfig).where(ProviderConfig.type == PROVIDER_TYPE)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = ProviderConfig(
            type=PROVIDER_TYPE,
            display_name=PROVIDER_DISPLAY,
            credentials_json=None,
            enabled=False,
        )
        session.add(cfg)
        await session.flush()
    return cfg


def _parse_creds(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def get_credentials(session: AsyncSession) -> dict[str, Any]:
    cfg = await _get_or_create_config(session)
    return _parse_creds(cfg.credentials_json)


async def save_credentials(session: AsyncSession, creds: dict[str, Any], *, enabled: bool = True) -> ProviderConfig:
    cfg = await _get_or_create_config(session)
    cfg.credentials_json = json.dumps(creds, ensure_ascii=False)
    cfg.enabled = enabled
    cfg.display_name = PROVIDER_DISPLAY
    await session.flush()
    return cfg


async def clear_credentials(session: AsyncSession) -> None:
    cfg = await _get_or_create_config(session)
    cfg.credentials_json = None
    cfg.enabled = False
    await session.flush()


async def exchange_code(session: AsyncSession, code: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.yandex_oauth_client_id or not settings.yandex_oauth_client_secret:
        raise RuntimeError("Задайте YANDEX_OAUTH_CLIENT_ID и YANDEX_OAUTH_CLIENT_SECRET в .env")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://oauth.yandex.ru/token",
            data={
                "grant_type": "authorization_code",
                "code": code.strip(),
                "client_id": settings.yandex_oauth_client_id,
                "client_secret": settings.yandex_oauth_client_secret,
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"OAuth exchange failed: {resp.status_code} {resp.text[:400]}")
        token_data = resp.json()

        access = token_data.get("access_token")
        if not access:
            raise RuntimeError("В ответе OAuth нет access_token")

        email = settings.yandex_mail_from or ""
        try:
            info = await client.get(
                "https://login.yandex.ru/info",
                params={"format": "json"},
                headers={"Authorization": f"OAuth {access}"},
            )
            if info.status_code < 400:
                payload = info.json()
                email = (
                    payload.get("default_email")
                    or (payload.get("emails") or [None])[0]
                    or payload.get("login")
                    or email
                )
                if email and "@" not in str(email):
                    email = f"{email}@yandex.ru"
        except Exception as exc:
            logger.warning("Could not fetch Yandex login info: %s", exc)

    expires_in = int(token_data.get("expires_in") or 31536000)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    creds = {
        "email": email,
        "access_token": access,
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": expires_at.isoformat(),
        "scopes": token_data.get("scope") or settings.yandex_oauth_scopes,
        "token_type": token_data.get("token_type") or "bearer",
    }
    await save_credentials(session, creds, enabled=True)
    return creds


async def save_manual_token(
    session: AsyncSession, *, access_token: str, email: str, refresh_token: str | None = None
) -> dict[str, Any]:
    settings = get_settings()
    creds = {
        "email": email.strip(),
        "access_token": access_token.strip(),
        "refresh_token": refresh_token,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
        "scopes": settings.yandex_oauth_scopes,
        "token_type": "bearer",
    }
    await save_credentials(session, creds, enabled=True)
    return creds


async def refresh_access_token(session: AsyncSession) -> dict[str, Any]:
    settings = get_settings()
    creds = await get_credentials(session)
    refresh = creds.get("refresh_token")
    if not refresh:
        return creds

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://oauth.yandex.ru/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": settings.yandex_oauth_client_id,
                "client_secret": settings.yandex_oauth_client_secret,
            },
        )
        if resp.status_code >= 400:
            logger.warning("Refresh token failed: %s", resp.text[:300])
            return creds
        token_data = resp.json()

    access = token_data.get("access_token")
    if not access:
        return creds
    expires_in = int(token_data.get("expires_in") or 31536000)
    creds["access_token"] = access
    if token_data.get("refresh_token"):
        creds["refresh_token"] = token_data["refresh_token"]
    creds["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()
    await save_credentials(session, creds, enabled=True)
    return creds


async def get_valid_access_token(session: AsyncSession) -> tuple[str, str]:
    """Return (access_token, email), refreshing if needed."""
    creds = await get_credentials(session)
    access = creds.get("access_token")
    email = (creds.get("email") or get_settings().yandex_mail_from or "").strip()
    if not access:
        raise RuntimeError("Яндекс не подключён. Пройдите OAuth на вкладке «Интеграции».")
    if not email:
        raise RuntimeError("Неизвестный From-адрес. Укажите YANDEX_MAIL_FROM или переподключите OAuth.")

    expires_raw = creds.get("expires_at")
    if expires_raw:
        try:
            expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc) + timedelta(minutes=5):
                creds = await refresh_access_token(session)
                access = creds.get("access_token") or access
                email = (creds.get("email") or email).strip()
        except ValueError:
            pass
    return str(access), email


def status_payload(creds: dict[str, Any], enabled: bool) -> dict[str, Any]:
    return {
        "connected": bool(creds.get("access_token")) and enabled,
        "email": creds.get("email") or None,
        "scopes": creds.get("scopes") or None,
        "expires_at": creds.get("expires_at") or None,
        "has_refresh_token": bool(creds.get("refresh_token")),
    }
