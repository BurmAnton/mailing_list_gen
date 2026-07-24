"""Segment discovery, rebuild, and recipient→segment resolution."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.campaign import Campaign, CampaignRecipient
from app.models.recipient import Recipient
from app.models.segment import CampaignSegment, SegmentTemplate, TemplateStatus

TYPED_SEGMENT_FIELDS: list[tuple[str, str]] = [
    ("industry", "Отрасль (industry)"),
    ("company_name", "Компания (company_name)"),
    ("description", "Описание (description)"),
    ("website_url", "Сайт (website_url)"),
    ("name", "Контакт (name)"),
]

ZONE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def parse_extra(recipient: Recipient) -> dict[str, Any]:
    if not recipient.extra_fields_json:
        return {}
    try:
        data = json.loads(recipient.extra_fields_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def recipient_field_value(recipient: Recipient, field_key: str) -> str:
    key = (field_key or "").strip()
    if not key:
        return ""
    typed = {
        "industry": recipient.industry,
        "company_name": recipient.company_name,
        "description": recipient.description,
        "website_url": recipient.website_url,
        "name": recipient.name,
    }
    if key in typed:
        return str(typed[key] or "").strip()
    extra = parse_extra(recipient)
    val = extra.get(key)
    if val is None:
        return ""
    return str(val).strip()


def normalize_segment_value(raw: str) -> str:
    return (raw or "").strip()


async def load_campaign_recipients(session: AsyncSession, campaign_id: int) -> list[Recipient]:
    result = await session.execute(
        select(Recipient)
        .join(CampaignRecipient, CampaignRecipient.recipient_id == Recipient.id)
        .where(CampaignRecipient.campaign_id == campaign_id)
        .order_by(Recipient.id)
    )
    return list(result.scalars().all())


async def discover_segment_fields(
    session: AsyncSession, campaign_id: int
) -> list[dict[str, Any]]:
    recipients = await load_campaign_recipients(session, campaign_id)
    if not recipients:
        return []

    samples: dict[str, list[str]] = defaultdict(list)
    counts: Counter[str] = Counter()

    for key, _label in TYPED_SEGMENT_FIELDS:
        for r in recipients:
            val = recipient_field_value(r, key)
            if val:
                counts[key] += 1
                if len(samples[key]) < 5 and val not in samples[key]:
                    samples[key].append(val)

    extra_keys: set[str] = set()
    for r in recipients:
        for k, v in parse_extra(r).items():
            if not k or k == "email":
                continue
            extra_keys.add(str(k))
            text = str(v).strip() if v is not None else ""
            if text:
                counts[str(k)] += 1
                if len(samples[str(k)]) < 5 and text not in samples[str(k)]:
                    samples[str(k)].append(text)

    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, label in TYPED_SEGMENT_FIELDS:
        if counts.get(key, 0) <= 0:
            continue
        seen.add(key)
        fields.append(
            {
                "key": key,
                "label": label,
                "non_empty_count": counts[key],
                "sample_values": samples.get(key, []),
            }
        )

    for key in sorted(extra_keys):
        if key in seen or counts.get(key, 0) <= 0:
            continue
        fields.append(
            {
                "key": key,
                "label": key,
                "non_empty_count": counts[key],
                "sample_values": samples.get(key, []),
            }
        )
    return fields


async def rebuild_segments(
    session: AsyncSession, campaign: Campaign
) -> list[CampaignSegment]:
    field = (campaign.segment_field or "").strip()
    if not campaign.use_segment_templates or not field:
        # clear segments when disabled
        existing = await session.execute(
            select(CampaignSegment).where(CampaignSegment.campaign_id == campaign.id)
        )
        for seg in existing.scalars().all():
            await session.delete(seg)
        await session.flush()
        return []

    recipients = await load_campaign_recipients(session, campaign.id)
    buckets: Counter[str] = Counter()
    for r in recipients:
        buckets[normalize_segment_value(recipient_field_value(r, field))] += 1

    existing = await session.execute(
        select(CampaignSegment)
        .where(CampaignSegment.campaign_id == campaign.id)
        .options(selectinload(CampaignSegment.templates))
    )
    by_value = {s.value: s for s in existing.scalars().all()}
    keep_values = set(buckets.keys())

    for value, obsolete in list(by_value.items()):
        if value not in keep_values:
            await session.delete(obsolete)
            by_value.pop(value, None)

    segments: list[CampaignSegment] = []
    for value, count in sorted(buckets.items(), key=lambda x: (-x[1], x[0])):
        label = value if value else "(пусто / не указано)"
        seg = by_value.get(value)
        if seg is None:
            seg = CampaignSegment(
                campaign_id=campaign.id,
                value=value,
                label=label,
                recipient_count=count,
            )
            session.add(seg)
            await session.flush()
            tpl = SegmentTemplate(
                segment_id=seg.id,
                variant_key="A",
                subject_template="",
                body_template="",
                zones_json="[]",
                status=TemplateStatus.draft,
            )
            session.add(tpl)
        else:
            seg.label = label
            seg.recipient_count = count
            if not seg.templates:
                session.add(
                    SegmentTemplate(
                        segment_id=seg.id,
                        variant_key="A",
                        subject_template="",
                        body_template="",
                        zones_json="[]",
                        status=TemplateStatus.draft,
                    )
                )
        segments.append(seg)

    await session.flush()
    return segments


def resolve_segment_value(recipient: Recipient, segment_field: str | None) -> str:
    return normalize_segment_value(recipient_field_value(recipient, segment_field or ""))


def parse_zones_json(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict) and item.get("name"):
            out.append(
                {
                    "name": str(item["name"]).strip(),
                    "hint": str(item.get("hint") or "").strip(),
                }
            )
    return out


def zones_to_json(zones: list[dict[str, str]] | list[Any]) -> str:
    cleaned = []
    for z in zones:
        if isinstance(z, dict) and z.get("name"):
            cleaned.append({"name": str(z["name"]).strip(), "hint": str(z.get("hint") or "").strip()})
    return json.dumps(cleaned, ensure_ascii=False)


def extract_zone_names(subject: str, body: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for text in (subject, body):
        for m in ZONE_RE.finditer(text or ""):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def apply_zones(template: str, zone_values: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        return zone_values.get(name, match.group(0))

    return ZONE_RE.sub(repl, template or "")


async def get_segment_for_recipient(
    session: AsyncSession, campaign: Campaign, recipient: Recipient
) -> CampaignSegment | None:
    if not campaign.use_segment_templates or not campaign.segment_field:
        return None
    value = resolve_segment_value(recipient, campaign.segment_field)
    result = await session.execute(
        select(CampaignSegment)
        .where(
            CampaignSegment.campaign_id == campaign.id,
            CampaignSegment.value == value,
        )
        .options(selectinload(CampaignSegment.templates))
    )
    return result.scalar_one_or_none()


def template_variant_a(segment: CampaignSegment) -> SegmentTemplate | None:
    for t in segment.templates or []:
        if t.variant_key == "A":
            return t
    return segment.templates[0] if segment.templates else None


async def segments_templates_ready(session: AsyncSession, campaign_id: int) -> bool:
    result = await session.execute(
        select(CampaignSegment)
        .where(CampaignSegment.campaign_id == campaign_id)
        .options(selectinload(CampaignSegment.templates))
    )
    segments = list(result.scalars().all())
    if not segments:
        return False
    for seg in segments:
        tpl = template_variant_a(seg)
        if not tpl or tpl.status != TemplateStatus.ready:
            return False
        if not (tpl.subject_template or "").strip() or not (tpl.body_template or "").strip():
            return False
    return True


async def compute_segment_progress(
    session: AsyncSession, campaign: Campaign
) -> list[dict[str, Any]]:
    """Per-segment draft counts for campaign status / UI progress."""
    from app.models.draft import EmailDraft

    if not campaign.use_segment_templates or not campaign.segment_field:
        return []

    seg_result = await session.execute(
        select(CampaignSegment)
        .where(CampaignSegment.campaign_id == campaign.id)
        .order_by(CampaignSegment.recipient_count.desc(), CampaignSegment.value)
    )
    segments = list(seg_result.scalars().all())
    if not segments:
        return []

    draft_result = await session.execute(
        select(EmailDraft)
        .where(EmailDraft.campaign_id == campaign.id)
        .options(selectinload(EmailDraft.recipient))
    )
    drafts = list(draft_result.scalars().all())

    empty = {"pending": 0, "generating": 0, "ready": 0, "error": 0, "edited": 0}
    by_value: dict[str, dict[str, int]] = {
        s.value: {**empty, "total": 0} for s in segments
    }
    orphan = {**empty, "total": 0}

    field = campaign.segment_field or ""
    for draft in drafts:
        if not draft.recipient:
            continue
        value = resolve_segment_value(draft.recipient, field)
        bucket = by_value.get(value)
        if bucket is None:
            bucket = orphan
        bucket["total"] += 1
        key = draft.status.value if hasattr(draft.status, "value") else str(draft.status)
        if key in bucket:
            bucket[key] += 1

    out: list[dict[str, Any]] = []
    has_drafts = len(drafts) > 0
    for seg in segments:
        counts = by_value[seg.value]
        total = counts["total"] if has_drafts else int(seg.recipient_count or 0)
        pending = counts["pending"] if has_drafts else total
        out.append(
            {
                "segment_id": seg.id,
                "value": seg.value,
                "label": seg.label,
                "total": total,
                "pending": pending,
                "generating": counts["generating"] if has_drafts else 0,
                "ready": counts["ready"] if has_drafts else 0,
                "error": counts["error"] if has_drafts else 0,
                "edited": counts["edited"] if has_drafts else 0,
            }
        )
    if orphan["total"] > 0:
        out.append(
            {
                "segment_id": None,
                "value": "__other__",
                "label": "(вне сегментов)",
                "total": orphan["total"],
                "pending": orphan["pending"],
                "generating": orphan["generating"],
                "ready": orphan["ready"],
                "error": orphan["error"],
                "edited": orphan["edited"],
            }
        )
    return out
