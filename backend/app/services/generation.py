import asyncio
import json
import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models.campaign import Campaign, CampaignRecipient, CampaignStatus
from app.models.draft import DraftStatus, EmailDraft
from app.models.rag_file import CampaignRagFile
from app.models.recipient import Recipient
from app.services.enrichment import enrich_recipient_from_website
from app.services.llm_client import LLMClient, get_model_pricing, parse_json_response
from app.services.rag import DIGEST_MAX_CHARS, build_or_get_rag_digest
from app.services.segments import (
    apply_zones,
    extract_zone_names,
    get_segment_for_recipient,
    parse_zones_json,
    strip_unresolved_zones,
    template_variant_a,
    zone_values_from_excel,
)
from app.models.segment import CampaignSegment, SegmentTemplate, TemplateStatus

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты копирайтер персонализированных B2B-писем. "
    "На основе промпта кампании, общих RAG-материалов и данных получателя из Excel "
    "сгенерируй письмо. "
    "Верни строго JSON с полями: subject (тема письма), body (текст письма, обычный текст). "
    "Письмо должно быть персонализированным, без воды и шаблонных клише. "
    "Не используй markdown-разметку в body."
)

SEGMENT_TEMPLATE_SYSTEM = (
    "Ты копирайтер B2B-рассылок. Создай ОДИН шаблон письма для сегмента аудитории. "
    "В subject и body используй зоны персонализации в формате {{zone_name}}. "
    "Имена зон могут быть на русском и совпадать с колонками Excel "
    "(напр. {{регион_(фо)}}, {{звонков_/_встреч_в_мес.}}) либо латиницей. "
    "Верни строго JSON: "
    '{"subject":"...","body":"...","zones":[{"name":"zone_name","hint":"что писать в зоне"}]}. '
    "Все {{zone_name}} из текста должны быть перечислены в zones. "
    "Каркас письма общий для сегмента; зоны — короткие персональные вставки. "
    "Без markdown."
)

ZONE_FILL_SYSTEM = (
    "Ты копирайтер. Дан шаблон письма с зонами {{name}} "
    "(имена могут быть на русском и совпадать с колонками Excel, напр. {{регион_(фо)}}). "
    "Заполни ТОЛЬКО значения зон под конкретную компанию. "
    "Если зона = колонка Excel — подставь значение Excel как есть. "
    "Верни строго JSON: {\"zones\":{\"zone_name\":\"текст\"}}. "
    "Ключи zones должны точно совпадать с именами зон в шаблоне. "
    "Не меняй каркас шаблона. Без markdown."
)

COMPLETION_TOKENS_EST = 450
ZONE_COMPLETION_TOKENS_EST = 180
CHARS_PER_TOKEN = 4


def _parse_extra(recipient: Recipient) -> dict[str, Any]:
    if not recipient.extra_fields_json:
        return {}
    try:
        data = json.loads(recipient.extra_fields_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_company_profile(recipient: Recipient) -> dict[str, Any]:
    enrichment: dict[str, Any] = {}
    if recipient.enrichment_json:
        try:
            enrichment = json.loads(recipient.enrichment_json)
        except json.JSONDecodeError:
            enrichment = {"raw": recipient.enrichment_json}

    extra = _parse_extra(recipient)
    return {
        "email": recipient.email,
        "contact_name": recipient.name,
        "company_name": recipient.company_name,
        "industry": recipient.industry,
        "description": recipient.description,
        "website_url": recipient.website_url,
        "excel_fields": extra or None,
        "website_analysis": enrichment or None,
    }


def client_for_campaign(campaign: Campaign) -> LLMClient:
    settings = get_settings()
    provider = campaign.llm_provider or settings.llm_provider
    model = campaign.llm_model or (
        settings.openrouter_default_model
        if provider == "openrouter"
        else settings.runpod_model
    )
    return LLMClient(model=model, provider=provider)


async def generate_email_content(
    client: LLMClient,
    *,
    base_prompt: str,
    recipient: Recipient,
    rag_digest: str = "",
    use_web_search: bool = False,
) -> tuple[str, str, str]:
    profile = build_company_profile(recipient)
    rag_block = rag_digest.strip() or "(нет общих RAG-материалов)"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Промпт кампании:\n{base_prompt}\n\n"
                f"Общие RAG-материалы (для всех писем):\n{rag_block}\n\n"
                f"Данные получателя из Excel / профиля (JSON):\n"
                f"{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
                "Сгенерируй subject и body."
            ),
        },
    ]
    raw = await client.chat_completion(
        messages,
        temperature=0.7,
        response_format={"type": "json_object"},
        enable_web_search=use_web_search,
    )
    try:
        data = parse_json_response(raw)
    except Exception as exc:
        raise ValueError(f"{exc} | raw_prefix={raw[:400]!r}") from exc
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or data.get("body_text") or "").strip()
    if not subject or not body:
        raise ValueError(f"LLM вернул пустые subject/body | raw_prefix={raw[:400]!r}")
    return subject, body, raw


async def generate_segment_template(
    client: LLMClient,
    *,
    base_prompt: str,
    segment_label: str,
    segment_field: str,
    sample_profiles: list[dict[str, Any]],
    rag_digest: str = "",
    use_web_search: bool = False,
) -> tuple[str, str, list[dict[str, str]], str]:
    rag_block = rag_digest.strip() or "(нет общих RAG-материалов)"
    messages = [
        {"role": "system", "content": SEGMENT_TEMPLATE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Промпт кампании:\n{base_prompt}\n\n"
                f"Поле сегментации: {segment_field}\n"
                f"Сегмент: {segment_label}\n\n"
                f"Примеры компаний сегмента (JSON):\n"
                f"{json.dumps(sample_profiles, ensure_ascii=False, indent=2)}\n\n"
                f"Общие RAG-материалы:\n{rag_block}\n\n"
                "Сгенерируй шаблон subject/body с зонами {{...}} и список zones."
            ),
        },
    ]
    raw = await client.chat_completion(
        messages,
        temperature=0.6,
        response_format={"type": "json_object"},
        enable_web_search=use_web_search,
    )
    data = parse_json_response(raw)
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    zones_raw = data.get("zones") or []
    zones: list[dict[str, str]] = []
    if isinstance(zones_raw, list):
        for z in zones_raw:
            if isinstance(z, dict) and z.get("name"):
                zones.append(
                    {"name": str(z["name"]).strip(), "hint": str(z.get("hint") or "").strip()}
                )
    # sync zones with placeholders in text
    names_in_text = extract_zone_names(subject, body)
    by_name = {z["name"]: z for z in zones}
    for name in names_in_text:
        if name not in by_name:
            by_name[name] = {"name": name, "hint": ""}
    zones = [by_name[n] for n in names_in_text] or list(by_name.values())
    if not subject or not body:
        raise ValueError(f"Пустой шаблон сегмента | raw_prefix={raw[:400]!r}")
    return subject, body, zones, raw


async def generate_email_from_template(
    client: LLMClient,
    *,
    template: SegmentTemplate,
    recipient: Recipient,
    rag_digest: str = "",
    use_web_search: bool = False,
) -> tuple[str, str, str]:
    profile = build_company_profile(recipient)
    zones = parse_zones_json(template.zones_json)
    zone_names = [z["name"] for z in zones] or extract_zone_names(
        template.subject_template, template.body_template
    )
    rag_block = rag_digest.strip() or "(нет общих RAG-материалов)"
    messages = [
        {"role": "system", "content": ZONE_FILL_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Шаблон темы:\n{template.subject_template}\n\n"
                f"Шаблон тела:\n{template.body_template}\n\n"
                f"Зоны для заполнения (JSON):\n{json.dumps(zones or [{'name': n} for n in zone_names], ensure_ascii=False)}\n\n"
                f"RAG:\n{rag_block}\n\n"
                f"Данные компании (JSON):\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
                "Верни значения zones."
            ),
        },
    ]
    raw = await client.chat_completion(
        messages,
        temperature=0.7,
        response_format={"type": "json_object"},
        enable_web_search=use_web_search,
    )
    data = parse_json_response(raw)
    zone_map = data.get("zones") if isinstance(data.get("zones"), dict) else data
    if not isinstance(zone_map, dict):
        raise ValueError(f"Ожидался объект zones | raw_prefix={raw[:400]!r}")
    llm_values = {str(k).strip(): str(v).strip() for k, v in zone_map.items()}
    # Excel columns win for matching zone names (facts), LLM fills the rest
    excel_values = zone_values_from_excel(recipient, zone_names)
    values = {**llm_values, **excel_values}
    subject = apply_zones(template.subject_template, values).strip()
    body = apply_zones(template.body_template, values).strip()
    subject = strip_unresolved_zones(subject)
    body = strip_unresolved_zones(body)
    if not subject or not body:
        raise ValueError(f"После подстановки зон пустые subject/body | raw_prefix={raw[:400]!r}")
    return subject, body, raw


_running_campaigns: set[int] = set()
_cancelled_campaigns: set[int] = set()


def is_campaign_generation_running(campaign_id: int) -> bool:
    return campaign_id in _running_campaigns


def request_cancel_generation(campaign_id: int) -> bool:
    """Signal running generation to stop. Returns True if it was running."""
    running = campaign_id in _running_campaigns
    _cancelled_campaigns.add(campaign_id)
    return running


def is_generation_cancelled(campaign_id: int) -> bool:
    return campaign_id in _cancelled_campaigns


def clear_cancel_flag(campaign_id: int) -> None:
    _cancelled_campaigns.discard(campaign_id)


async def run_campaign_generation(campaign_id: int, enrich_websites: bool = False) -> None:
    if campaign_id in _running_campaigns:
        return
    clear_cancel_flag(campaign_id)
    _running_campaigns.add(campaign_id)
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.generation_concurrency)
    cancelled = False

    try:
        async with AsyncSessionLocal() as session:
            campaign = await session.get(Campaign, campaign_id)
            if not campaign:
                return
            campaign.status = CampaignStatus.generating
            await session.commit()

            client = client_for_campaign(campaign)
            rag_digest = await build_or_get_rag_digest(session, campaign, client=client)
            await session.commit()

            if is_generation_cancelled(campaign_id):
                cancelled = True
                drafts = []
            else:
                result = await session.execute(
                    select(EmailDraft)
                    .where(EmailDraft.campaign_id == campaign_id)
                    .options(selectinload(EmailDraft.recipient))
                )
                drafts = list(result.scalars().all())

        async def process_one(draft_id: int) -> None:
            nonlocal cancelled
            if is_generation_cancelled(campaign_id):
                cancelled = True
                return

            async with semaphore:
                if is_generation_cancelled(campaign_id):
                    cancelled = True
                    return

                base_prompt = ""
                digest = ""
                use_web = False
                enrich = enrich_websites
                use_segments = False
                template_id: int | None = None
                recipient_snapshot: Recipient | None = None
                provider = "openrouter"
                model: str | None = None

                async with AsyncSessionLocal() as session:
                    draft = await session.get(
                        EmailDraft,
                        draft_id,
                        options=(selectinload(EmailDraft.recipient),),
                    )
                    if not draft or not draft.recipient:
                        return
                    if draft.status in {DraftStatus.ready, DraftStatus.edited}:
                        return
                    campaign = await session.get(Campaign, campaign_id)
                    if not campaign:
                        return
                    local_client = client_for_campaign(campaign)
                    provider = local_client.provider
                    model = local_client.model
                    base_prompt = campaign.base_prompt
                    digest = campaign.rag_digest_text or ""
                    use_web = bool(campaign.use_web_search)
                    use_segments = bool(getattr(campaign, "use_segment_templates", False))
                    recipient_snapshot = draft.recipient
                    if use_segments:
                        seg = await get_segment_for_recipient(
                            session, campaign, draft.recipient
                        )
                        tpl = template_variant_a(seg) if seg else None
                        if not tpl or tpl.status != TemplateStatus.ready:
                            draft.status = DraftStatus.error
                            draft.error = (
                                "Нет готового шаблона сегмента. "
                                "Сгенерируйте и утвердите шаблоны на вкладке «Сегменты»."
                            )
                            await session.commit()
                            return
                        template_id = tpl.id

                    draft.status = DraftStatus.generating
                    draft.error = None
                    await session.commit()

                if is_generation_cancelled(campaign_id):
                    cancelled = True
                    async with AsyncSessionLocal() as session:
                        draft = await session.get(EmailDraft, draft_id)
                        if draft and draft.status == DraftStatus.generating:
                            draft.status = DraftStatus.pending
                            draft.error = None
                            await session.commit()
                    return

                err: str | None = None
                subject = ""
                body = ""
                raw = ""
                try:
                    assert recipient_snapshot is not None
                    local_client = LLMClient(provider=provider, model=model)
                    if enrich:
                        if is_generation_cancelled(campaign_id):
                            cancelled = True
                            raise asyncio.CancelledError()
                        async with AsyncSessionLocal() as session:
                            draft = await session.get(
                                EmailDraft,
                                draft_id,
                                options=(selectinload(EmailDraft.recipient),),
                            )
                            if draft and draft.recipient:
                                enrichment = await enrich_recipient_from_website(
                                    local_client,
                                    company_name=draft.recipient.company_name,
                                    website_url=draft.recipient.website_url,
                                    existing_enrichment=draft.recipient.enrichment_json,
                                    industry=draft.recipient.industry,
                                    description=draft.recipient.description,
                                    use_web_search=use_web,
                                )
                                draft.recipient.enrichment_json = enrichment
                                await session.commit()
                                recipient_snapshot = draft.recipient

                    if is_generation_cancelled(campaign_id):
                        cancelled = True
                        raise asyncio.CancelledError()

                    if use_segments and template_id is not None:
                        async with AsyncSessionLocal() as session:
                            tpl = await session.get(SegmentTemplate, template_id)
                            if not tpl:
                                raise ValueError("Шаблон сегмента не найден")
                            subject, body, raw = await generate_email_from_template(
                                local_client,
                                template=tpl,
                                recipient=recipient_snapshot,
                                rag_digest=digest,
                                use_web_search=False,
                            )
                    else:
                        subject, body, raw = await generate_email_content(
                            local_client,
                            base_prompt=base_prompt,
                            recipient=recipient_snapshot,
                            rag_digest=digest,
                            use_web_search=False,
                        )
                except asyncio.CancelledError:
                    err = None
                    async with AsyncSessionLocal() as session:
                        draft = await session.get(EmailDraft, draft_id)
                        if draft and draft.status == DraftStatus.generating:
                            draft.status = DraftStatus.pending
                            draft.error = None
                            await session.commit()
                    return
                except Exception as exc:
                    logger.error("Draft %s generation failed: %s", draft_id, exc)
                    err = str(exc)

                if is_generation_cancelled(campaign_id):
                    cancelled = True
                    async with AsyncSessionLocal() as session:
                        draft = await session.get(EmailDraft, draft_id)
                        if draft and draft.status == DraftStatus.generating:
                            draft.status = DraftStatus.pending
                            draft.error = None
                            await session.commit()
                    return

                async with AsyncSessionLocal() as session:
                    draft = await session.get(EmailDraft, draft_id)
                    if not draft:
                        return
                    if err:
                        draft.status = DraftStatus.error
                        draft.error = err
                        if raw:
                            draft.raw_llm_response = raw
                    else:
                        draft.subject = subject
                        draft.body_text = body
                        draft.body_html = None
                        draft.raw_llm_response = raw
                        draft.status = DraftStatus.ready
                        draft.error = None
                    await session.commit()

        await asyncio.gather(*(process_one(d.id) for d in drafts))
        cancelled = cancelled or is_generation_cancelled(campaign_id)

        async with AsyncSessionLocal() as session:
            campaign = await session.get(Campaign, campaign_id)
            if campaign:
                if cancelled:
                    # unfinished in-flight → pending
                    result = await session.execute(
                        select(EmailDraft).where(
                            EmailDraft.campaign_id == campaign_id,
                            EmailDraft.status == DraftStatus.generating,
                        )
                    )
                    for draft in result.scalars().all():
                        draft.status = DraftStatus.pending
                        draft.error = None
                    campaign.status = CampaignStatus.draft
                else:
                    campaign.status = CampaignStatus.ready
                await session.commit()
    finally:
        _running_campaigns.discard(campaign_id)
        clear_cancel_flag(campaign_id)


async def regenerate_draft(draft_id: int, enrich_websites: bool = False) -> EmailDraft:
    async with AsyncSessionLocal() as session:
        draft = await session.get(
            EmailDraft,
            draft_id,
            options=(selectinload(EmailDraft.recipient), selectinload(EmailDraft.campaign)),
        )
        if not draft or not draft.recipient or not draft.campaign:
            raise ValueError("Черновик не найден")

        client = client_for_campaign(draft.campaign)
        use_web_search = bool(draft.campaign.use_web_search)
        digest = await build_or_get_rag_digest(session, draft.campaign, client=client)

        draft.status = DraftStatus.generating
        draft.error = None
        await session.commit()

        try:
            recipient = draft.recipient
            campaign = draft.campaign
            if enrich_websites:
                enrichment = await enrich_recipient_from_website(
                    client,
                    company_name=recipient.company_name,
                    website_url=recipient.website_url,
                    existing_enrichment=recipient.enrichment_json,
                    industry=recipient.industry,
                    description=recipient.description,
                    use_web_search=use_web_search,
                )
                recipient.enrichment_json = enrichment
                await session.commit()

            if campaign.use_segment_templates:
                seg = await get_segment_for_recipient(session, campaign, recipient)
                tpl = template_variant_a(seg) if seg else None
                if not tpl or tpl.status != TemplateStatus.ready:
                    raise ValueError(
                        "Нет готового шаблона сегмента. "
                        "Сгенерируйте и утвердите шаблоны на вкладке «Сегменты»."
                    )
                subject, body, raw = await generate_email_from_template(
                    client,
                    template=tpl,
                    recipient=recipient,
                    rag_digest=digest,
                    use_web_search=False,
                )
            else:
                subject, body, raw = await generate_email_content(
                    client,
                    base_prompt=campaign.base_prompt,
                    recipient=recipient,
                    rag_digest=digest,
                    use_web_search=False,
                )
            draft.subject = subject
            draft.body_text = body
            draft.raw_llm_response = raw
            draft.status = DraftStatus.ready
            draft.error = None
        except Exception as exc:
            draft.status = DraftStatus.error
            draft.error = str(exc)
            await session.commit()
            raise

        await session.commit()
        await session.refresh(draft, attribute_names=["recipient"])
        return draft


async def prepare_drafts_for_campaign(session: AsyncSession, campaign_id: int) -> list[EmailDraft]:
    """Ensure a draft exists for each recipient. Keeps ready/edited drafts on resume."""
    existing = await session.execute(select(EmailDraft).where(EmailDraft.campaign_id == campaign_id))
    by_recipient = {d.recipient_id: d for d in existing.scalars().all()}

    links = await session.execute(
        select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
    )
    drafts: list[EmailDraft] = []
    for link in links.scalars().all():
        draft = by_recipient.get(link.recipient_id)
        if draft is None:
            draft = EmailDraft(
                campaign_id=campaign_id,
                recipient_id=link.recipient_id,
                status=DraftStatus.pending,
            )
            session.add(draft)
        elif draft.status == DraftStatus.generating:
            # orphaned mid-flight after worker crash
            draft.status = DraftStatus.pending
            draft.error = None
        drafts.append(draft)
    await session.flush()
    return drafts


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


async def estimate_campaign_cost(session: AsyncSession, campaign: Campaign) -> dict[str, Any]:
    recipient_count = int(
        await session.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign.id)
        )
        or 0
    )
    rag_count = int(
        await session.scalar(
            select(func.count())
            .select_from(CampaignRagFile)
            .where(CampaignRagFile.campaign_id == campaign.id)
        )
        or 0
    )

    # Sample one recipient profile size if available
    sample_extra = ""
    link = await session.execute(
        select(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .limit(1)
    )
    first = link.scalar_one_or_none()
    if first:
        recip = await session.get(Recipient, first.recipient_id)
        if recip:
            sample_extra = json.dumps(build_company_profile(recip), ensure_ascii=False)

    digest = campaign.rag_digest_text or ""
    if not digest and rag_count:
        digest = "x" * min(DIGEST_MAX_CHARS, 4000)

    digest_tokens = estimate_tokens(digest) if rag_count and not campaign.rag_digest_hash else (
        estimate_tokens(digest) if rag_count else 0
    )
    # If digest not cached yet and files exist, count one digest LLM call (~input heavy)
    if rag_count and not campaign.rag_digest_text:
        digest_tokens = min(DIGEST_MAX_CHARS, 8000) // CHARS_PER_TOKEN

    use_segments = bool(getattr(campaign, "use_segment_templates", False))
    segment_count = 0
    if use_segments:
        segment_count = int(
            await session.scalar(
                select(func.count())
                .select_from(CampaignSegment)
                .where(CampaignSegment.campaign_id == campaign.id)
            )
            or 0
        )
        # zone-fill prompts are shorter than full-letter generation
        sample_template = (
            '{"subject":"Hello {{company}}","body":"... {{hook}} ...","zones":[{"name":"hook"}]}'
        )
        per_prompt = (
            estimate_tokens(ZONE_FILL_SYSTEM)
            + estimate_tokens(sample_template)
            + estimate_tokens(digest)
            + estimate_tokens(sample_extra or '{"email":"a@b.c","company_name":"Acme"}')
            + 40
        )
        completion_tokens = ZONE_COMPLETION_TOKENS_EST * max(recipient_count, 0)
        prompt_tokens = per_prompt * max(recipient_count, 0)
        # one LLM call per segment template (if not already ready, still count as plan cost)
        template_prompt = (
            estimate_tokens(SEGMENT_TEMPLATE_SYSTEM)
            + estimate_tokens(campaign.base_prompt or "")
            + estimate_tokens(digest)
            + estimate_tokens(sample_extra or "") * 3
            + 80
        )
        prompt_tokens += template_prompt * max(segment_count, 0)
        completion_tokens += COMPLETION_TOKENS_EST * max(segment_count, 0)
    else:
        per_prompt = (
            estimate_tokens(SYSTEM_PROMPT)
            + estimate_tokens(campaign.base_prompt or "")
            + estimate_tokens(digest)
            + estimate_tokens(sample_extra or '{"email":"a@b.c","company_name":"Acme"}')
            + 40
        )
        completion_tokens = COMPLETION_TOKENS_EST * max(recipient_count, 0)
        prompt_tokens = per_prompt * max(recipient_count, 0)

    model_id = campaign.llm_model or get_settings().openrouter_default_model
    pricing = await get_model_pricing(model_id) if model_id else None
    prompt_1m = (pricing or {}).get("price_prompt_per_1m") or 0.0
    completion_1m = (pricing or {}).get("price_completion_per_1m") or 0.0
    price_label = (pricing or {}).get("price_label") or "цена неизвестна"

    cost = (
        (prompt_tokens / 1_000_000) * float(prompt_1m)
        + (completion_tokens / 1_000_000) * float(completion_1m)
        + (digest_tokens / 1_000_000) * float(prompt_1m)
        + (min(DIGEST_MAX_CHARS, 2000) / CHARS_PER_TOKEN / 1_000_000) * float(completion_1m)
        if rag_count and not campaign.rag_digest_text
        else (prompt_tokens / 1_000_000) * float(prompt_1m)
        + (completion_tokens / 1_000_000) * float(completion_1m)
    )

    notes = [
        "Оценка приблизительная (±30%).",
        "RAG сжимается один раз на кампанию и переиспользуется.",
        "Веб-поиск выключен по умолчанию — включите явно в настройках.",
    ]
    if use_segments:
        notes.append(
            f"Сегментный режим: ~{segment_count} шаблонов сегментов + заполнение зон "
            f"по {recipient_count} получателям (короче полного письма)."
        )
    if campaign.use_web_search:
        notes.append("Веб-поиск включён: фактическая стоимость может быть выше.")

    return {
        "campaign_id": campaign.id,
        "recipient_count": recipient_count,
        "model_id": model_id,
        "price_label": price_label,
        "estimated_prompt_tokens": prompt_tokens,
        "estimated_completion_tokens": completion_tokens,
        "estimated_digest_tokens": digest_tokens if rag_count else 0,
        "estimated_cost_usd": round(float(cost), 6),
        "notes": notes,
    }
