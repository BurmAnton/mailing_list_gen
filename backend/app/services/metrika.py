"""Yandex Metrika API client (metrika:read)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.yandex_oauth import get_valid_access_token

logger = logging.getLogger(__name__)

MANAGEMENT = "https://api-metrika.yandex.net/management/v1"
STAT = "https://api-metrika.yandex.net/stat/v1/data"


async def _auth_headers(session: AsyncSession) -> dict[str, str]:
    access, _email = await get_valid_access_token(session)
    return {"Authorization": f"OAuth {access}"}


async def list_counters(session: AsyncSession) -> list[dict[str, Any]]:
    headers = await _auth_headers(session)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{MANAGEMENT}/counters", headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Metrika counters error: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
    out = []
    for c in data.get("counters") or []:
        out.append(
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "site": c.get("site") or c.get("site2", {}).get("site"),
                "permission": c.get("permission"),
            }
        )
    return out


async def list_goals(session: AsyncSession, counter_id: int) -> list[dict[str, Any]]:
    headers = await _auth_headers(session)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{MANAGEMENT}/counter/{counter_id}/goals", headers=headers
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Metrika goals error: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
    out = []
    for g in data.get("goals") or []:
        out.append(
            {
                "id": g.get("id"),
                "name": g.get("name"),
                "type": g.get("type"),
                "is_retargeting": g.get("is_retargeting"),
            }
        )
    return out


async def fetch_email_utm_stats(
    session: AsyncSession,
    *,
    counter_id: int,
    goal_id: int | None = None,
    days: int = 30,
) -> dict[str, Any]:
    headers = await _auth_headers(session)
    date2 = date.today()
    date1 = date2 - timedelta(days=max(1, days))
    metrics = ["ym:s:visits", "ym:s:users"]
    if goal_id:
        metrics.append(f"ym:s:goal{goal_id}reaches")
        metrics.append(f"ym:s:goal{goal_id}visits")
        metrics.append(f"ym:s:goal{goal_id}conversionRate")
    params: dict[str, Any] = {
        "ids": counter_id,
        "date1": date1.isoformat(),
        "date2": date2.isoformat(),
        "metrics": ",".join(metrics),
        "dimensions": "ym:s:UTMSource,ym:s:UTMMedium,ym:s:UTMCampaign,ym:s:UTMContent",
        "filters": "ym:s:UTMSource=='email'",
        "limit": 1000,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(STAT, headers=headers, params=params)
        if resp.status_code >= 400:
            # retry without filter if syntax rejected
            params.pop("filters", None)
            resp = await client.get(STAT, headers=headers, params=params)
            if resp.status_code >= 400:
                raise RuntimeError(f"Metrika report error: {resp.status_code} {resp.text[:400]}")
        data = resp.json()

    rows = []
    for item in data.get("data") or []:
        dims = item.get("dimensions") or []
        mets = item.get("metrics") or []
        utm_source = (dims[0] or {}).get("name") if len(dims) > 0 else None
        utm_medium = (dims[1] or {}).get("name") if len(dims) > 1 else None
        utm_campaign = (dims[2] or {}).get("name") if len(dims) > 2 else None
        utm_content = (dims[3] or {}).get("name") if len(dims) > 3 else None
        if utm_source and utm_source != "email":
            continue
        row = {
            "utm_source": utm_source,
            "utm_medium": utm_medium,
            "utm_campaign": utm_campaign,
            "utm_content": utm_content,
            "visits": mets[0] if len(mets) > 0 else 0,
            "users": mets[1] if len(mets) > 1 else 0,
        }
        if goal_id and len(mets) >= 5:
            row["goal_reaches"] = mets[2]
            row["goal_visits"] = mets[3]
            row["goal_conversion_rate"] = mets[4]
        rows.append(row)

    totals = data.get("totals") or []
    return {
        "counter_id": counter_id,
        "goal_id": goal_id,
        "date1": date1.isoformat(),
        "date2": date2.isoformat(),
        "rows": rows,
        "totals": totals,
        "metric_names": metrics,
    }
