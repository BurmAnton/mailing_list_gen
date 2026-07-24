import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

MAX_PAGE_CHARS = 8000
USER_AGENT = (
    "Mozilla/5.0 (compatible; MailingListGenerator/1.0; +https://localhost) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def fetch_website_text(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL должен начинаться с http:// или https://")

    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            raise ValueError(f"Неподдерживаемый content-type: {content_type}")
        return extract_text_from_html(response.text)


def extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)[:MAX_PAGE_CHARS]


async def summarize_company_context(
    client: LLMClient,
    *,
    company_name: str,
    website_url: str | None,
    page_text: str | None,
    industry: str | None = None,
    description: str | None = None,
    use_web_search: bool = True,
) -> dict[str, Any]:
    context_parts = [f"Компания: {company_name}"]
    if website_url:
        context_parts.append(f"Сайт: {website_url}")
    if industry:
        context_parts.append(f"Отрасль: {industry}")
    if description:
        context_parts.append(f"Описание из CRM: {description}")
    if page_text:
        context_parts.append(f"Текст страницы:\n{page_text}")

    search_hint = ""
    if use_web_search and client.provider == "openrouter":
        search_hint = (
            " При необходимости используй веб-поиск/актуальные данные о компании. "
        )

    messages = [
        {
            "role": "system",
            "content": (
                "Ты аналитик B2B."
                f"{search_hint}"
                "Верни JSON-объект с полями: "
                "summary (краткое описание 2-4 предложения), "
                "products_or_services (массив строк), "
                "target_audience (строка), "
                "tone_hints (строка — как лучше обращаться в письме), "
                "key_facts (массив коротких фактов). "
                "Отвечай только JSON."
            ),
        },
        {
            "role": "user",
            "content": "\n".join(context_parts),
        },
    ]
    return await client.chat_json(
        messages,
        temperature=0.3,
        enable_web_search=use_web_search,
    )


async def enrich_recipient_from_website(
    client: LLMClient,
    *,
    company_name: str,
    website_url: str | None,
    existing_enrichment: str | None = None,
    industry: str | None = None,
    description: str | None = None,
    use_web_search: bool = True,
) -> str | None:
    cache_key = website_url or company_name
    if existing_enrichment:
        try:
            cached = json.loads(existing_enrichment)
            if cached.get("cache_key") == cache_key and cached.get("summary"):
                return existing_enrichment
            if website_url and cached.get("website_url") == website_url and cached.get("summary"):
                return existing_enrichment
        except json.JSONDecodeError:
            pass

    page_text: str | None = None
    if website_url:
        try:
            page_text = await fetch_website_text(website_url)
        except Exception as exc:
            logger.warning("Site fetch failed for %s: %s", website_url, exc)

    if not page_text and not use_web_search and not description and not industry:
        return existing_enrichment

    try:
        summary = await summarize_company_context(
            client,
            company_name=company_name,
            website_url=website_url,
            page_text=page_text,
            industry=industry,
            description=description,
            use_web_search=use_web_search,
        )
        payload = {
            "website_url": website_url,
            "cache_key": cache_key,
            "source": "website+llm" if page_text else "llm_search",
            **summary,
        }
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Enrichment failed for %s: %s", cache_key, exc)
        return json.dumps(
            {
                "website_url": website_url,
                "cache_key": cache_key,
                "error": str(exc),
            },
            ensure_ascii=False,
        )
