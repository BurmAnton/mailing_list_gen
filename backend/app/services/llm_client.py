import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    decoder = json.JSONDecoder()

    # Prefer raw_decode so trailing junk / second objects don't raise "Extra data".
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            data, _end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    raise ValueError(f"Не удалось разобрать JSON из ответа LLM: {raw[:500]}")


def _extract_content(content: Any) -> str:
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content)


class LLMClient:
    """OpenAI-compatible client for OpenRouter or RunPod."""

    def __init__(self, model: str | None = None, provider: str | None = None) -> None:
        self.settings = get_settings()
        self.provider = (provider or self.settings.llm_provider or "openrouter").lower()
        if self.provider == "openrouter":
            self.model = model or self.settings.openrouter_default_model
        else:
            self.model = model or self.settings.runpod_model

    @property
    def configured(self) -> bool:
        if self.provider == "openrouter":
            return bool(self.settings.openrouter_api_key)
        return bool(self.settings.runpod_api_key) and "YOUR_ENDPOINT_ID" not in (
            self.settings.runpod_endpoint_url or ""
        )

    def _endpoint_and_headers(self) -> tuple[str, dict[str, str]]:
        if self.provider == "openrouter":
            if not self.settings.openrouter_api_key:
                raise RuntimeError("OpenRouter не настроен: укажите OPENROUTER_API_KEY в .env")
            url = self.settings.openrouter_base_url.rstrip("/") + "/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": self.settings.openrouter_http_referer,
                "X-Title": self.settings.openrouter_app_title,
            }
            return url, headers

        if not self.configured:
            raise RuntimeError(
                "RunPod не настроен: укажите RUNPOD_API_KEY и RUNPOD_ENDPOINT_URL в .env"
            )
        url = self.settings.runpod_endpoint_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.runpod_api_key}",
            "Content-Type": "application/json",
        }
        return url, headers

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        response_format: dict[str, Any] | None = None,
        enable_web_search: bool = False,
        model: str | None = None,
    ) -> str:
        url, headers = self._endpoint_and_headers()
        used_model = model or self.model
        payload: dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
        }

        # OpenRouter server tool: web search (replaces deprecated plugins/web and :online)
        if (
            self.provider == "openrouter"
            and enable_web_search
            and self.settings.openrouter_web_plugin
        ):
            payload["tools"] = [
                {
                    "type": "openrouter:web_search",
                    "parameters": {
                        "engine": "auto",
                        "max_results": 5,
                        "max_total_results": 15,
                    },
                }
            ]
            payload["max_tool_calls"] = 5

        last_error: Exception | None = None
        use_response_format = response_format
        for attempt in range(self.settings.max_retries + 1):
            try:
                payload_attempt = dict(payload)
                if use_response_format:
                    payload_attempt["response_format"] = use_response_format
                async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload_attempt)
                    if response.status_code >= 400:
                        detail = response.text[:500]
                        # Some models (e.g. Perplexity Sonar) reject response_format.
                        if (
                            use_response_format
                            and response.status_code == 400
                            and "response_format" in detail.lower()
                        ):
                            logger.warning(
                                "%s rejected response_format; retrying without it",
                                self.provider,
                            )
                            use_response_format = None
                            continue
                        raise RuntimeError(f"LLM HTTP {response.status_code}: {detail}")
                    data = response.json()
                    choice = (data.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    content = _extract_content(message.get("content"))
                    if not content.strip():
                        # Rare: unfinished tool loop — surface for retry/visibility
                        tool_calls = message.get("tool_calls") or []
                        if tool_calls:
                            raise RuntimeError(
                                "LLM вернул tool_calls без финального текста "
                                f"(tools={len(tool_calls)})"
                            )
                        raise RuntimeError("LLM вернул пустой content")
                    return content
            except Exception as exc:
                last_error = exc
                logger.warning("%s attempt %s failed: %s", self.provider, attempt + 1, exc)
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))

        raise RuntimeError(f"{self.provider} request failed: {last_error}") from last_error

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.5,
        enable_web_search: bool = False,
        model: str | None = None,
    ) -> dict[str, Any]:
        raw = await self.chat_completion(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            enable_web_search=enable_web_search,
            model=model,
        )
        return parse_json_response(raw)


# Backward-compatible alias
RunPodClient = LLMClient


def model_supports_search_rag(model: dict[str, Any]) -> list[str]:
    """Return match reasons if model can use OpenRouter web search.

    Prefer models advertising ``web_search_options`` (native search params).
    Also include models with ``tools`` — OpenRouter ``openrouter:web_search``
    works as a server tool for tool-capable chat models.
    """
    mid = (model.get("id") or "").lower()
    name = (model.get("name") or "").lower()

    out_mods = (model.get("architecture") or {}).get("output_modalities") or []
    if "image" in out_mods and "text" not in out_mods:
        return []
    if re.search(r"(^|/)[^/\s]*image[^/\s]*($|:)", mid) or (
        "image" in name and "nano banana" in name
    ):
        return []
    if "-image" in mid or mid.endswith("image") or "/image" in mid:
        return []

    params = [str(p) for p in (model.get("supported_parameters") or [])]
    reasons: list[str] = []
    if "web_search_options" in params:
        reasons.append("web_search_options")
    if "tools" in params:
        reasons.append("tools")
    if ":online" in mid:
        reasons.append("online")

    seen: set[str] = set()
    uniq: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_model_pricing(pricing: dict[str, Any]) -> dict[str, Any]:
    """OpenRouter prices are USD per token; expose per 1M tokens."""
    prompt = _to_float(pricing.get("prompt"))
    completion = _to_float(pricing.get("completion"))

    # OpenRouter may return negative values for free / special pricing
    if prompt is not None and prompt < 0:
        prompt = 0.0
    if completion is not None and completion < 0:
        completion = 0.0

    prompt_1m = prompt * 1_000_000 if prompt is not None else None
    completion_1m = completion * 1_000_000 if completion is not None else None

    def fmt(v: float | None) -> str:
        if v is None:
            return "?"
        if v == 0:
            return "$0"
        if v < 0.01:
            return f"${v:.4f}"
        if v < 1:
            return f"${v:.3f}"
        return f"${v:.2f}"

    label = f"{fmt(prompt_1m)} / {fmt(completion_1m)} за 1M tok"
    return {
        "price_prompt_per_1m": prompt_1m,
        "price_completion_per_1m": completion_1m,
        "price_label": label,
    }


async def list_openrouter_search_rag_models() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise RuntimeError("OpenRouter не настроен: укажите OPENROUTER_API_KEY в .env")

    url = settings.openrouter_base_url.rstrip("/") + "/models"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.openrouter_http_referer,
        "X-Title": settings.openrouter_app_title,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()

    models = payload.get("data") or []
    result: list[dict[str, Any]] = []
    for m in models:
        reasons = model_supports_search_rag(m)
        if not reasons:
            continue
        pricing = m.get("pricing") or {}
        price_fields = format_model_pricing(pricing)
        result.append(
            {
                "id": m.get("id"),
                "name": m.get("name") or m.get("id"),
                "description": (m.get("description") or "")[:400],
                "context_length": m.get("context_length"),
                "supported_parameters": m.get("supported_parameters") or [],
                "supports_tools": "tools" in (m.get("supported_parameters") or []),
                "pricing_prompt": pricing.get("prompt"),
                "pricing_completion": pricing.get("completion"),
                **price_fields,
                "match_reasons": reasons,
                "provider": "openrouter",
            }
        )

    result.sort(
        key=lambda x: (
            # Prefer models with native web_search_options, then cheaper
            0 if "web_search_options" in (x.get("match_reasons") or []) else 1,
            x.get("price_prompt_per_1m") is None,
            x.get("price_prompt_per_1m") if x.get("price_prompt_per_1m") is not None else 1e9,
            (x.get("name") or x.get("id") or "").lower(),
        )
    )
    return result


async def get_model_pricing(model_id: str) -> dict[str, Any] | None:
    models = await list_openrouter_search_rag_models()
    for m in models:
        if m.get("id") == model_id:
            return m
    # Fallback: fetch all models without filter for pricing of non-listed ids
    settings = get_settings()
    url = settings.openrouter_base_url.rstrip("/") + "/models"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.openrouter_http_referer,
        "X-Title": settings.openrouter_app_title,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        for m in response.json().get("data") or []:
            if m.get("id") == model_id:
                pricing = m.get("pricing") or {}
                return {
                    "id": model_id,
                    "name": m.get("name") or model_id,
                    **format_model_pricing(pricing),
                    "pricing_prompt": pricing.get("prompt"),
                    "pricing_completion": pricing.get("completion"),
                }
    return None
