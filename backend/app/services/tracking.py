"""Email HTML building, banner, UTM, and open/click tracking tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings

HREF_RE = re.compile(r'href=(["\'])(https?://.*?)\1', re.IGNORECASE)


def new_tracking_token() -> str:
    return secrets.token_urlsafe(24)


def _sign(payload: str) -> str:
    secret = get_settings().tracking_hmac_secret.encode("utf-8")
    dig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(dig[:16]).decode("ascii").rstrip("=")


def make_click_token(send_token: str, url: str) -> str:
    raw = f"{send_token}|{url}"
    sig = _sign(raw)
    blob = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{blob}.{sig}"


def parse_click_token(token: str) -> tuple[str, str] | None:
    try:
        blob, sig = token.rsplit(".", 1)
        pad = "=" * (-len(blob) % 4)
        raw = base64.urlsafe_b64decode(blob + pad).decode("utf-8")
        if not hmac.compare_digest(_sign(raw), sig):
            return None
        send_token, url = raw.split("|", 1)
        if not send_token or not url.startswith("http"):
            return None
        return send_token, url
    except Exception:
        return None


def with_utm(url: str, *, draft_id: int, campaign_slug: str = "comquaagent") -> str:
    parsed = urlparse(url)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    q.setdefault("utm_source", "email")
    q.setdefault("utm_medium", "mail")
    q.setdefault("utm_campaign", campaign_slug)
    q["utm_content"] = str(draft_id)
    return urlunparse(parsed._replace(query=urlencode(q)))


def is_public_tracking_base(public_base: str) -> bool:
    """Local/dev bases make Gmail treat links as spam — skip wrapping then."""
    try:
        host = (urlparse(public_base).hostname or "").lower()
    except Exception:
        return False
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return False
    if host.endswith(".local") or host.startswith("192.168.") or host.startswith("10."):
        return False
    if not public_base.lower().startswith("https://"):
        return False
    return True


MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*(https?://[^)\s]+)\s*\)")


def normalize_markdown_links_plain(text: str) -> str:
    """Turn [label](url) into a single readable URL/label for plain-text body."""

    def repl(match: re.Match[str]) -> str:
        label = (match.group(1) or "").strip()
        url = (match.group(2) or "").strip()
        if url.startswith("http://comquaagent.") or url.startswith("https://comquaagent."):
            url = "https://" + url.split("://", 1)[1]
        label_l = label.lower().removeprefix("https://").removeprefix("http://").strip("/")
        host = (urlparse(url).netloc or "").lower()
        if label_l == host or label_l == host + (urlparse(url).path or "").rstrip("/"):
            return url
        return f"{label}: {url}"

    return MD_LINK_RE.sub(repl, text or "")


def text_to_html_paragraphs(text: str) -> str:
    parts = [p.strip() for p in (text or "").replace("\r\n", "\n").split("\n\n")]
    blocks: list[str] = []
    for part in parts:
        if not part:
            continue
        line_htmls: list[str] = []
        for line in part.split("\n"):
            # Extract markdown links, escape the rest, then inject <a>
            slots: list[str] = []

            def stash(match: re.Match[str]) -> str:
                label = (match.group(1) or "").strip()
                url = (match.group(2) or "").strip()
                if url.startswith("http://comquaagent."):
                    url = "https://" + url[len("http://") :]
                idx = len(slots)
                slots.append(
                    f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
                )
                return f"\x00LINK{idx}\x00"

            marked = MD_LINK_RE.sub(stash, line)
            escaped = html.escape(marked)
            for i, anchor in enumerate(slots):
                escaped = escaped.replace(f"\x00LINK{i}\x00", anchor)
            # Autolink bare URLs not already inside our placeholders/anchors
            escaped = re.sub(
                r"(?<!href=\")(https?://[^\s<>\"]+)",
                lambda m: f'<a href="{html.escape(m.group(1), quote=True)}">{html.escape(m.group(1))}</a>',
                escaped,
            )
            line_htmls.append(escaped)
        lines = "<br>\n".join(line_htmls)
        blocks.append(f'<p style="margin:0 0 12px;line-height:1.5">{lines}</p>')
    return "\n".join(blocks) or "<p></p>"


def sanitize_banner_html(html_body: str) -> str:
    """Remove designer placeholders that leak into the visible email body."""
    text = html_body or ""
    # Visible label from cold-audit.html (real preheader is in display:none div below)
    text = re.sub(
        r"\s*Preheader\s*\(\s*скрытый\s+превью-текст\s*\)\s*",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*Preheader\s*\([^)]*preview[^)]*\)\s*",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    return text


async def fetch_banner_html(source_url: str) -> str:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(source_url)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").lower()
        text = resp.text
    if "html" not in content_type and "<html" not in text.lower()[:500]:
        # fallback card
        safe = html.escape(source_url)
        return (
            f'<div style="margin:16px 0;padding:12px;border:1px solid #ddd">'
            f'<a href="{safe}">Открыть предложение</a></div>'
        )
    soup = BeautifulSoup(text, "lxml")
    # Prefer body inner HTML for email embedding
    body = soup.body
    if body:
        # Remove scripts
        for tag in body.find_all(["script", "iframe", "noscript"]):
            tag.decompose()
        inner = "".join(str(x) for x in body.contents)
        return sanitize_banner_html(f'<div class="banner-embed" style="margin:16px 0">{inner}</div>')
    return sanitize_banner_html(f'<div class="banner-embed" style="margin:16px 0">{text[:50000]}</div>')


def rewrite_links_for_tracking(html_body: str, send_token: str, public_base: str) -> str:
    def repl(match: re.Match[str]) -> str:
        quote = match.group(1)
        url = match.group(2)
        if "/t/c/" in url or "/t/o/" in url:
            return match.group(0)
        click = make_click_token(send_token, url)
        tracked = f"{public_base.rstrip('/')}/t/c/{click}"
        return f"href={quote}{tracked}{quote}"

    return HREF_RE.sub(repl, html_body)


def inject_open_pixel(html_body: str, send_token: str, public_base: str) -> str:
    pixel = (
        f'<img src="{public_base.rstrip("/")}/t/o/{send_token}.gif" width="1" height="1" '
        f'alt="" style="display:block;width:1px;height:1px;border:0" />'
    )
    if "</body>" in html_body.lower():
        return re.sub(r"</body>", pixel + "</body>", html_body, count=1, flags=re.IGNORECASE)
    return html_body + pixel


def build_email_html(
    *,
    body_text: str,
    banner_html: str | None,
    landing_url: str,
    draft_id: int,
    send_token: str,
    track: bool = True,
) -> str:
    settings = get_settings()
    public_base = settings.public_base_url
    landing = with_utm(landing_url or "https://comquaagent.copp-hightech.ru/", draft_id=draft_id)
    body_html = text_to_html_paragraphs(body_text)
    cta = (
        f'<p style="margin:20px 0">'
        f'<a href="{html.escape(landing)}" style="display:inline-block;padding:10px 16px;'
        f'background:#1a4d3c;color:#fff;text-decoration:none;border-radius:4px">'
        f"Записаться на аудит / демо</a></p>"
        f'<p style="font-size:12px;color:#666">Или перейдите: '
        f'<a href="{html.escape(landing)}">{html.escape(landing)}</a></p>'
    )
    banner = sanitize_banner_html(banner_html or "")
    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;max-width:640px;margin:0 auto;padding:16px">
{body_html}
{banner}
{cta}
</body></html>"""
    if track:
        if is_public_tracking_base(public_base):
            doc = rewrite_links_for_tracking(doc, send_token, public_base)
            doc = inject_open_pixel(doc, send_token, public_base)
        # else: keep direct landing/banner URLs — localhost tracking = spam
    return doc


def ensure_landing_in_plain(body_text: str, landing_url: str, draft_id: int) -> str:
    landing = with_utm(landing_url or "https://comquaagent.copp-hightech.ru/", draft_id=draft_id)
    text = normalize_markdown_links_plain(body_text or "").rstrip()
    if "comquaagent.copp-hightech.ru" not in text.lower():
        text += f"\n\nЗаписаться на аудит / демо: {landing}\n"
    return text


TRANSPARENT_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)
