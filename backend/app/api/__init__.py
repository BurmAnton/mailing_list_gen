import csv
import io
import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_db
from app.models.campaign import Campaign, CampaignRecipient, CampaignStatus
from app.models.draft import DraftStatus, EmailDraft
from app.models.rag_file import CampaignRagFile
from app.models.recipient import Recipient
from app.providers import get_provider, list_providers
from app.models.segment import CampaignSegment, SegmentTemplate, TemplateStatus
from app.schemas import (
    CampaignCreate,
    CampaignIdsIn,
    CampaignOut,
    CampaignStatusOut,
    CampaignUpdate,
    CostEstimateOut,
    DraftOut,
    DraftUpdate,
    GenerateRequest,
    ImportResult,
    LLMModelOut,
    ProviderOut,
    RagFileOut,
    RagWebsiteIn,
    RecipientOut,
    SegmentConfigIn,
    SegmentFieldOut,
    SegmentOut,
    SegmentProgressOut,
    SegmentTemplateOut,
    SegmentTemplateUpdate,
    ZoneOut,
)
from app.services.generation import (
    build_company_profile,
    client_for_campaign,
    estimate_campaign_cost,
    generate_segment_template,
    prepare_drafts_for_campaign,
    regenerate_draft,
    request_cancel_generation,
    run_campaign_generation,
)
from app.services.import_recipients import parse_recipients_file
from app.services.llm_client import list_openrouter_search_rag_models
from app.services.rag import build_or_get_rag_digest, delete_rag_file, save_rag_file, save_rag_website
from app.services.segments import (
    compute_segment_progress,
    discover_segment_fields,
    extract_zone_names,
    load_campaign_recipients,
    normalize_segment_value,
    parse_zones_json,
    rebuild_segments,
    recipient_field_value,
    segments_templates_ready,
    template_variant_a,
    zones_to_json,
)
router = APIRouter(prefix="/api")

# #region agent log
def _agent_debug_log(payload: dict) -> None:
    import time
    from pathlib import Path

    data = {
        "sessionId": "bd0245",
        "timestamp": int(time.time() * 1000),
        "runId": payload.get("runId") or "post-fix",
        **payload,
    }
    line = json.dumps(data, ensure_ascii=False) + "\n"
    seen: set[str] = set()
    for p in (
        Path(r"D:\Projects\mailing_list_generator\debug-bd0245.log"),
        Path(__file__).resolve().parents[3] / "debug-bd0245.log",
    ):
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            continue


@router.post("/_debug/log")
async def agent_debug_log(payload: dict) -> dict:
    _agent_debug_log(payload)
    return {"ok": True}


# #endregion


def _recipient_out(recipient: Recipient) -> RecipientOut:
    extra = None
    if recipient.extra_fields_json:
        try:
            parsed = json.loads(recipient.extra_fields_json)
            if isinstance(parsed, dict):
                extra = parsed
        except json.JSONDecodeError:
            extra = None
    return RecipientOut(
        id=recipient.id,
        email=recipient.email,
        name=recipient.name,
        company_name=recipient.company_name,
        industry=recipient.industry,
        description=recipient.description,
        website_url=recipient.website_url,
        enrichment_json=recipient.enrichment_json,
        extra_fields=extra,
    )


def _campaign_out(
    campaign: Campaign,
    recipient_count: int = 0,
    draft_count: int = 0,
    rag_file_count: int = 0,
) -> CampaignOut:
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        base_prompt=campaign.base_prompt,
        llm_provider=getattr(campaign, "llm_provider", None) or "openrouter",
        llm_model=getattr(campaign, "llm_model", None),
        use_web_search=bool(getattr(campaign, "use_web_search", False)),
        use_segment_templates=bool(getattr(campaign, "use_segment_templates", False)),
        segment_field=getattr(campaign, "segment_field", None),
        status=campaign.status,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        recipient_count=recipient_count,
        draft_count=draft_count,
        rag_file_count=rag_file_count,
    )


def _template_out(tpl: SegmentTemplate | None) -> SegmentTemplateOut | None:
    if not tpl:
        return None
    zones = [ZoneOut(**z) for z in parse_zones_json(tpl.zones_json)]
    return SegmentTemplateOut(
        id=tpl.id,
        segment_id=tpl.segment_id,
        variant_key=tpl.variant_key or "A",
        subject_template=tpl.subject_template or "",
        body_template=tpl.body_template or "",
        zones=zones,
        status=tpl.status,
    )


def _segment_out(seg: CampaignSegment) -> SegmentOut:
    return SegmentOut(
        id=seg.id,
        campaign_id=seg.campaign_id,
        value=seg.value,
        label=seg.label,
        recipient_count=seg.recipient_count,
        template=_template_out(template_variant_a(seg)),
    )


async def _counts(session: AsyncSession, campaign_id: int) -> tuple[int, int, int]:
    recipients = await session.scalar(
        select(func.count())
        .select_from(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign_id)
    )
    drafts = await session.scalar(
        select(func.count()).select_from(EmailDraft).where(EmailDraft.campaign_id == campaign_id)
    )
    rag = await session.scalar(
        select(func.count())
        .select_from(CampaignRagFile)
        .where(CampaignRagFile.campaign_id == campaign_id)
    )
    return int(recipients or 0), int(drafts or 0), int(rag or 0)


@router.get("/campaigns", response_model=list[CampaignOut])
async def list_campaigns(db: AsyncSession = Depends(get_db)) -> list[CampaignOut]:
    result = await db.execute(select(Campaign).order_by(Campaign.id.desc()))
    campaigns = list(result.scalars().all())
    out: list[CampaignOut] = []
    for c in campaigns:
        rc, dc, ragc = await _counts(db, c.id)
        out.append(_campaign_out(c, rc, dc, ragc))
    return out


@router.post("/campaigns", response_model=CampaignOut)
async def create_campaign(payload: CampaignCreate, db: AsyncSession = Depends(get_db)) -> CampaignOut:
    settings = get_settings()
    campaign = Campaign(
        name=payload.name,
        base_prompt=payload.base_prompt,
        llm_provider=payload.llm_provider or settings.llm_provider,
        llm_model=payload.llm_model or settings.openrouter_default_model,
        use_web_search=bool(payload.use_web_search),
        status=CampaignStatus.draft,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return _campaign_out(campaign)


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)) -> CampaignOut:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    rc, dc, ragc = await _counts(db, campaign.id)
    return _campaign_out(campaign, rc, dc, ragc)


@router.patch("/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
) -> CampaignOut:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    if payload.name is not None:
        campaign.name = payload.name
    if payload.base_prompt is not None:
        campaign.base_prompt = payload.base_prompt
    if payload.llm_provider is not None:
        campaign.llm_provider = payload.llm_provider
    if payload.llm_model is not None:
        campaign.llm_model = payload.llm_model
    if payload.use_web_search is not None:
        campaign.use_web_search = payload.use_web_search
    await db.commit()
    await db.refresh(campaign)
    rc, dc, ragc = await _counts(db, campaign.id)
    return _campaign_out(campaign, rc, dc, ragc)


async def _delete_campaign_cascade(db: AsyncSession, campaign_id: int) -> bool:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        return False

    request_cancel_generation(campaign_id)

    rag_result = await db.execute(
        select(CampaignRagFile).where(CampaignRagFile.campaign_id == campaign_id)
    )
    for rag in rag_result.scalars().all():
        await delete_rag_file(db, rag)

    await db.execute(EmailDraft.__table__.delete().where(EmailDraft.campaign_id == campaign_id))
    # segments → templates (ORM cascade)
    seg_result = await db.execute(
        select(CampaignSegment)
        .where(CampaignSegment.campaign_id == campaign_id)
        .options(selectinload(CampaignSegment.templates))
    )
    for seg in seg_result.scalars().all():
        await db.delete(seg)
    await db.flush()

    link_result = await db.execute(
        select(CampaignRecipient.recipient_id).where(CampaignRecipient.campaign_id == campaign_id)
    )
    recipient_ids = [row[0] for row in link_result.all()]
    await db.execute(
        CampaignRecipient.__table__.delete().where(CampaignRecipient.campaign_id == campaign_id)
    )
    await db.delete(campaign)
    await db.flush()

    for rid in recipient_ids:
        still = await db.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(CampaignRecipient.recipient_id == rid)
        )
        if not still:
            recip = await db.get(Recipient, rid)
            if recip:
                await db.delete(recip)
    return True


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    ok = await _delete_campaign_cascade(db, campaign_id)
    if not ok:
        raise HTTPException(404, "Кампания не найдена")
    await db.commit()
    return {"ok": True, "deleted": [campaign_id]}


@router.post("/campaigns/delete")
async def delete_campaigns_bulk(payload: CampaignIdsIn, db: AsyncSession = Depends(get_db)) -> dict:
    deleted: list[int] = []
    for cid in payload.ids:
        if await _delete_campaign_cascade(db, cid):
            deleted.append(cid)
    await db.commit()
    return {"ok": True, "deleted": deleted, "requested": payload.ids}


@router.get("/models", response_model=list[LLMModelOut])
async def list_models(capability: str = Query("search_rag")) -> list[LLMModelOut]:
    if capability not in ("search_rag", "all_search"):
        raise HTTPException(400, "Поддерживается capability=search_rag")
    try:
        models = await list_openrouter_search_rag_models()
    except Exception as exc:
        raise HTTPException(502, f"Не удалось загрузить модели OpenRouter: {exc}") from exc
    return [LLMModelOut(**m) for m in models]


@router.get("/campaigns/{campaign_id}/recipients", response_model=list[RecipientOut])
async def list_recipients(campaign_id: int, db: AsyncSession = Depends(get_db)) -> list[RecipientOut]:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    result = await db.execute(
        select(Recipient)
        .join(CampaignRecipient, CampaignRecipient.recipient_id == Recipient.id)
        .where(CampaignRecipient.campaign_id == campaign_id)
        .order_by(Recipient.id)
    )
    return [_recipient_out(r) for r in result.scalars().all()]


@router.post("/campaigns/{campaign_id}/recipients/import", response_model=ImportResult)
async def import_recipients(
    campaign_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ImportResult:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    content = await file.read()
    try:
        rows, skipped_invalid = parse_recipients_file(file.filename or "data.csv", content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    existing = await db.execute(
        select(Recipient.email)
        .join(CampaignRecipient, CampaignRecipient.recipient_id == Recipient.id)
        .where(CampaignRecipient.campaign_id == campaign_id)
    )
    existing_emails = {e.lower() for e in existing.scalars().all()}

    imported: list[Recipient] = []
    skipped_duplicates = 0
    for row in rows:
        email = (row.get("email") or "").lower()
        if email in existing_emails:
            skipped_duplicates += 1
            continue
        existing_emails.add(email)
        extra = row.get("extra_fields") or {}
        recipient = Recipient(
            email=row["email"],
            name=row.get("name"),
            company_name=row.get("company_name") or "—",
            industry=row.get("industry"),
            description=row.get("description"),
            website_url=row.get("website_url"),
            source_row=row.get("source_row"),
            extra_fields_json=json.dumps(extra, ensure_ascii=False) if extra else None,
        )
        db.add(recipient)
        await db.flush()
        db.add(CampaignRecipient(campaign_id=campaign_id, recipient_id=recipient.id))
        imported.append(recipient)

    campaign.status = CampaignStatus.draft
    await db.commit()
    for r in imported:
        await db.refresh(r)

    return ImportResult(
        imported=len(imported),
        skipped_duplicates=skipped_duplicates,
        skipped_invalid=skipped_invalid,
        recipients=[_recipient_out(r) for r in imported],
    )


@router.get("/campaigns/{campaign_id}/rag", response_model=list[RagFileOut])
async def list_rag_files(campaign_id: int, db: AsyncSession = Depends(get_db)) -> list[RagFileOut]:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    result = await db.execute(
        select(CampaignRagFile)
        .where(CampaignRagFile.campaign_id == campaign_id)
        .order_by(CampaignRagFile.id)
    )
    return [RagFileOut.model_validate(f) for f in result.scalars().all()]


@router.post("/campaigns/{campaign_id}/rag", response_model=list[RagFileOut])
async def upload_rag_files(
    campaign_id: int,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
) -> list[RagFileOut]:
    # #region agent log
    def _dbg(message: str, data: dict, hypothesis_id: str) -> None:
        _agent_debug_log(
            {
                "hypothesisId": hypothesis_id,
                "location": "api/__init__.py:upload_rag_files",
                "message": message,
                "data": data,
                "runId": "post-fix",
            }
        )
        print(f"[DBG-bd0245] {message} {data}", flush=True)

    # #endregion

    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    # #region agent log
    _dbg(
        "rag_upload_entry",
        {
            "campaign_id": campaign_id,
            "file_count": len(files) if files else 0,
            "names": [getattr(f, "filename", None) for f in (files or [])],
            "content_types": [getattr(f, "content_type", None) for f in (files or [])],
        },
        "B",
    )
    # #endregion

    saved: list[CampaignRagFile] = []
    for upload in files:
        content = await upload.read()
        # #region agent log
        name = upload.filename or "file"
        suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        _dbg(
            "rag_file_received",
            {
                "filename": name,
                "suffix": suffix,
                "size": len(content),
                "content_type": upload.content_type,
            },
            "C",
        )
        # #endregion
        try:
            rag = await save_rag_file(
                db,
                campaign_id=campaign_id,
                filename=upload.filename or "file.txt",
                content=content,
                content_type=upload.content_type,
            )
            saved.append(rag)
            # #region agent log
            _dbg(
                "rag_file_saved",
                {"filename": name, "char_count": rag.char_count, "rag_id": rag.id},
                "D",
            )
            # #endregion
        except ValueError as exc:
            # #region agent log
            _dbg(
                "rag_file_value_error",
                {"filename": name, "error": str(exc)},
                "C",
            )
            # #endregion
            raise HTTPException(400, f"{name}: {exc}") from exc
        except Exception as exc:
            # #region agent log
            _dbg(
                "rag_file_exception",
                {"filename": name, "error": str(exc), "type": type(exc).__name__},
                "D",
            )
            # #endregion
            raise HTTPException(400, f"{name}: не удалось обработать файл ({exc})") from exc

    await db.commit()
    for rag in saved:
        await db.refresh(rag)
    # #region agent log
    _dbg("rag_upload_ok", {"saved": len(saved)}, "B")
    # #endregion
    return [RagFileOut.model_validate(f) for f in saved]


@router.post("/campaigns/{campaign_id}/rag/url", response_model=RagFileOut)
async def add_rag_website(
    campaign_id: int,
    payload: RagWebsiteIn,
    db: AsyncSession = Depends(get_db),
) -> RagFileOut:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    try:
        rag = await save_rag_website(db, campaign_id=campaign_id, url=payload.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Не удалось загрузить сайт: {exc}") from exc
    await db.commit()
    await db.refresh(rag)
    return RagFileOut.model_validate(rag)


@router.delete("/campaigns/{campaign_id}/rag/{file_id}")
async def remove_rag_file(
    campaign_id: int,
    file_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    rag = await db.get(CampaignRagFile, file_id)
    if not rag or rag.campaign_id != campaign_id:
        raise HTTPException(404, "RAG-файл не найден")
    await delete_rag_file(db, rag)
    await db.commit()
    return {"ok": True}


@router.get("/campaigns/{campaign_id}/segment-fields", response_model=list[SegmentFieldOut])
async def get_segment_fields(
    campaign_id: int, db: AsyncSession = Depends(get_db)
) -> list[SegmentFieldOut]:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    fields = await discover_segment_fields(db, campaign_id)
    return [SegmentFieldOut(**f) for f in fields]


@router.put("/campaigns/{campaign_id}/segments/config", response_model=list[SegmentOut])
async def put_segments_config(
    campaign_id: int,
    payload: SegmentConfigIn,
    db: AsyncSession = Depends(get_db),
) -> list[SegmentOut]:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    use = bool(payload.use_segment_templates)
    field = (payload.segment_field or "").strip() or None

    if use:
        fields = await discover_segment_fields(db, campaign_id)
        if not fields:
            raise HTTPException(
                400,
                "Нет распознанных столбцов для сегментации. Сначала загрузите Excel.",
            )
        allowed = {f["key"] for f in fields}
        if not field or field not in allowed:
            raise HTTPException(
                400,
                "segment_field должен быть одним из распознанных столбцов кампании",
            )
    else:
        field = None

    campaign.use_segment_templates = use
    campaign.segment_field = field
    await rebuild_segments(db, campaign)
    await db.commit()

    result = await db.execute(
        select(CampaignSegment)
        .where(CampaignSegment.campaign_id == campaign_id)
        .options(selectinload(CampaignSegment.templates))
        .order_by(CampaignSegment.recipient_count.desc(), CampaignSegment.value)
    )
    return [_segment_out(s) for s in result.scalars().all()]


@router.get("/campaigns/{campaign_id}/segments", response_model=list[SegmentOut])
async def list_segments(campaign_id: int, db: AsyncSession = Depends(get_db)) -> list[SegmentOut]:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    result = await db.execute(
        select(CampaignSegment)
        .where(CampaignSegment.campaign_id == campaign_id)
        .options(selectinload(CampaignSegment.templates))
        .order_by(CampaignSegment.recipient_count.desc(), CampaignSegment.value)
    )
    return [_segment_out(s) for s in result.scalars().all()]


@router.post(
    "/campaigns/{campaign_id}/segments/generate-templates",
    response_model=list[SegmentOut],
)
async def generate_segment_templates(
    campaign_id: int, db: AsyncSession = Depends(get_db)
) -> list[SegmentOut]:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    if not campaign.use_segment_templates or not campaign.segment_field:
        raise HTTPException(400, "Сначала включите сегментные шаблоны и выберите поле")

    result = await db.execute(
        select(CampaignSegment)
        .where(CampaignSegment.campaign_id == campaign_id)
        .options(selectinload(CampaignSegment.templates))
        .order_by(CampaignSegment.recipient_count.desc(), CampaignSegment.value)
    )
    segments = list(result.scalars().all())
    if not segments:
        raise HTTPException(400, "Нет сегментов — пересоберите сегменты")

    client = client_for_campaign(campaign)
    digest = await build_or_get_rag_digest(db, campaign, client=client)
    await db.commit()

    recipients = await load_campaign_recipients(db, campaign_id)
    field = campaign.segment_field or ""

    for seg in segments:
        samples = [
            build_company_profile(r)
            for r in recipients
            if normalize_segment_value(recipient_field_value(r, field)) == seg.value
        ][:5]
        if not samples:
            samples = [build_company_profile(r) for r in recipients[:3]]

        tpl = template_variant_a(seg)
        if not tpl:
            tpl = SegmentTemplate(
                segment_id=seg.id,
                variant_key="A",
                subject_template="",
                body_template="",
                zones_json="[]",
                status=TemplateStatus.draft,
            )
            db.add(tpl)
            await db.flush()

        try:
            subject, body, zones, raw = await generate_segment_template(
                client,
                base_prompt=campaign.base_prompt,
                segment_label=seg.label,
                segment_field=field,
                sample_profiles=samples,
                rag_digest=digest,
                use_web_search=False,
            )
            tpl.subject_template = subject
            tpl.body_template = body
            tpl.zones_json = zones_to_json(zones)
            tpl.raw_llm_response = raw
            tpl.status = TemplateStatus.draft
        except Exception as exc:
            tpl.status = TemplateStatus.draft
            tpl.raw_llm_response = str(exc)
            raise HTTPException(502, f"Ошибка генерации шаблона «{seg.label}»: {exc}") from exc

    await db.commit()

    result = await db.execute(
        select(CampaignSegment)
        .where(CampaignSegment.campaign_id == campaign_id)
        .options(selectinload(CampaignSegment.templates))
        .order_by(CampaignSegment.recipient_count.desc(), CampaignSegment.value)
    )
    return [_segment_out(s) for s in result.scalars().all()]


@router.patch(
    "/campaigns/{campaign_id}/segments/{segment_id}/template",
    response_model=SegmentTemplateOut,
)
async def patch_segment_template(
    campaign_id: int,
    segment_id: int,
    payload: SegmentTemplateUpdate,
    db: AsyncSession = Depends(get_db),
) -> SegmentTemplateOut:
    seg = await db.get(
        CampaignSegment,
        segment_id,
        options=(selectinload(CampaignSegment.templates),),
    )
    if not seg or seg.campaign_id != campaign_id:
        raise HTTPException(404, "Сегмент не найден")

    tpl = template_variant_a(seg)
    if not tpl:
        raise HTTPException(404, "Шаблон сегмента не найден")

    if payload.subject_template is not None:
        tpl.subject_template = payload.subject_template
    if payload.body_template is not None:
        tpl.body_template = payload.body_template
    if payload.zones is not None:
        tpl.zones_json = zones_to_json([z.model_dump() for z in payload.zones])
    else:
        # keep hints for known zones; add new names from {{...}} in text
        names = extract_zone_names(tpl.subject_template, tpl.body_template)
        by_name = {z["name"]: z for z in parse_zones_json(tpl.zones_json)}
        synced = [
            by_name.get(n) or {"name": n, "hint": ""}
            for n in names
        ]
        tpl.zones_json = zones_to_json(synced)
    if payload.status is not None:
        if payload.status == TemplateStatus.ready:
            if not (tpl.subject_template or "").strip() or not (tpl.body_template or "").strip():
                raise HTTPException(400, "Нельзя утвердить пустой шаблон")
        tpl.status = payload.status

    await db.commit()
    await db.refresh(tpl)
    out = _template_out(tpl)
    assert out is not None
    return out


@router.get("/campaigns/{campaign_id}/cost-estimate", response_model=CostEstimateOut)
async def cost_estimate(campaign_id: int, db: AsyncSession = Depends(get_db)) -> CostEstimateOut:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")
    data = await estimate_campaign_cost(db, campaign)
    return CostEstimateOut(**data)


@router.post("/campaigns/{campaign_id}/generate")
async def start_generate(
    campaign_id: int,
    payload: GenerateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.generation import is_campaign_generation_running

    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    rc, _, _ = await _counts(db, campaign_id)
    if rc == 0:
        raise HTTPException(400, "Сначала импортируйте получателей")

    if campaign.use_segment_templates:
        if not await segments_templates_ready(db, campaign_id):
            raise HTTPException(
                400,
                "Сначала утвердите шаблоны на вкладке Сегменты",
            )

    if campaign.status == CampaignStatus.generating:
        if is_campaign_generation_running(campaign_id):
            raise HTTPException(409, "Генерация уже запущена")
        # Stale lock after uvicorn reload / crash — allow restart.
        result = await db.execute(
            select(EmailDraft).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.status == DraftStatus.generating,
            )
        )
        for draft in result.scalars().all():
            draft.status = DraftStatus.pending
            draft.error = None

    await prepare_drafts_for_campaign(db, campaign_id)
    campaign.status = CampaignStatus.generating
    await db.commit()

    # create_task: do not block uvicorn shutdown/reload on long generation
    # (BackgroundTasks would freeze the API until all 231 drafts finish).
    import asyncio

    asyncio.create_task(run_campaign_generation(campaign_id, payload.enrich_websites))
    return {"ok": True, "campaign_id": campaign_id, "status": "generating"}


@router.post("/campaigns/{campaign_id}/generate/stop")
async def stop_generate(campaign_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    from app.services.generation import is_campaign_generation_running

    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    was_running = request_cancel_generation(campaign_id)
    if campaign.status != CampaignStatus.generating and not was_running:
        raise HTTPException(400, "Генерация сейчас не запущена")

    if not was_running and campaign.status == CampaignStatus.generating:
        # Stale generating status without live worker
        result = await db.execute(
            select(EmailDraft).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.status == DraftStatus.generating,
            )
        )
        for draft in result.scalars().all():
            draft.status = DraftStatus.pending
            draft.error = None
        campaign.status = CampaignStatus.draft
        await db.commit()
        return {"ok": True, "campaign_id": campaign_id, "status": "draft", "stopped": True}

    return {
        "ok": True,
        "campaign_id": campaign_id,
        "status": "stopping",
        "message": "Остановка запрошена — текущие запросы к модели доработают, новые не стартуют",
    }


@router.get("/campaigns/{campaign_id}/status", response_model=CampaignStatusOut)
async def campaign_status(campaign_id: int, db: AsyncSession = Depends(get_db)) -> CampaignStatusOut:
    from app.services.generation import is_campaign_generation_running

    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    worker_alive = is_campaign_generation_running(campaign_id)

    # Uvicorn --reload kills the worker but leaves status=generating — heal it.
    if campaign.status == CampaignStatus.generating and not worker_alive:
        result = await db.execute(
            select(EmailDraft).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.status == DraftStatus.generating,
            )
        )
        for draft in result.scalars().all():
            draft.status = DraftStatus.pending
            draft.error = None
        campaign.status = CampaignStatus.draft
        await db.commit()
        await db.refresh(campaign)

    result = await db.execute(select(EmailDraft.status).where(EmailDraft.campaign_id == campaign_id))
    statuses = list(result.scalars().all())
    counts = {"pending": 0, "generating": 0, "ready": 0, "error": 0, "edited": 0}
    for s in statuses:
        counts[s.value] = counts.get(s.value, 0) + 1

    segment_rows: list[SegmentProgressOut] = []
    if campaign.use_segment_templates:
        raw_segs = await compute_segment_progress(db, campaign)
        segment_rows = [SegmentProgressOut(**row) for row in raw_segs]

    return CampaignStatusOut(
        campaign_id=campaign_id,
        status=campaign.status,
        total=len(statuses),
        pending=counts["pending"],
        generating=counts["generating"],
        ready=counts["ready"],
        error=counts["error"],
        edited=counts["edited"],
        worker_alive=is_campaign_generation_running(campaign_id),
        use_segment_templates=bool(campaign.use_segment_templates),
        segments=segment_rows,
    )


@router.get("/campaigns/{campaign_id}/drafts", response_model=list[DraftOut])
async def list_drafts(campaign_id: int, db: AsyncSession = Depends(get_db)) -> list[DraftOut]:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    result = await db.execute(
        select(EmailDraft)
        .where(EmailDraft.campaign_id == campaign_id)
        .options(selectinload(EmailDraft.recipient))
        .order_by(EmailDraft.id)
    )
    drafts = []
    for d in result.scalars().all():
        out = DraftOut.model_validate(d)
        if d.recipient:
            out.recipient = _recipient_out(d.recipient)
        drafts.append(out)
    return drafts


@router.get("/drafts/{draft_id}", response_model=DraftOut)
async def get_draft(draft_id: int, db: AsyncSession = Depends(get_db)) -> DraftOut:
    draft = await db.get(EmailDraft, draft_id, options=(selectinload(EmailDraft.recipient),))
    if not draft:
        raise HTTPException(404, "Черновик не найден")
    out = DraftOut.model_validate(draft)
    if draft.recipient:
        out.recipient = _recipient_out(draft.recipient)
    return out


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
async def update_draft(
    draft_id: int,
    payload: DraftUpdate,
    db: AsyncSession = Depends(get_db),
) -> DraftOut:
    draft = await db.get(EmailDraft, draft_id, options=(selectinload(EmailDraft.recipient),))
    if not draft:
        raise HTTPException(404, "Черновик не найден")
    if payload.subject is not None:
        draft.subject = payload.subject
    if payload.body_text is not None:
        draft.body_text = payload.body_text
    if payload.body_html is not None:
        draft.body_html = payload.body_html
    draft.status = DraftStatus.edited
    await db.commit()
    await db.refresh(draft)
    out = DraftOut.model_validate(draft)
    if draft.recipient:
        out.recipient = _recipient_out(draft.recipient)
    return out


@router.post("/drafts/{draft_id}/regenerate", response_model=DraftOut)
async def regenerate(
    draft_id: int,
    enrich_websites: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> DraftOut:
    try:
        draft = await regenerate_draft(draft_id, enrich_websites=enrich_websites)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

    draft = await db.get(EmailDraft, draft.id, options=(selectinload(EmailDraft.recipient),))
    if not draft:
        raise HTTPException(404, "Черновик не найден")
    out = DraftOut.model_validate(draft)
    if draft.recipient:
        out.recipient = _recipient_out(draft.recipient)
    return out


@router.get("/campaigns/{campaign_id}/export")
async def export_campaign(
    campaign_id: int,
    format: str = Query("csv", pattern="^(csv|json)$"),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    result = await db.execute(
        select(EmailDraft)
        .where(EmailDraft.campaign_id == campaign_id)
        .options(selectinload(EmailDraft.recipient))
        .order_by(EmailDraft.id)
    )
    drafts = list(result.scalars().all())
    rows = []
    for d in drafts:
        rows.append(
            {
                "email": d.recipient.email if d.recipient else "",
                "name": d.recipient.name if d.recipient else "",
                "company_name": d.recipient.company_name if d.recipient else "",
                "subject": d.subject or "",
                "body": d.body_text or "",
                "status": d.status.value,
            }
        )

    if format == "json":
        payload = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
        return StreamingResponse(
            io.BytesIO(payload),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="campaign_{campaign_id}.json"'},
        )

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["email", "name", "company_name", "subject", "body", "status"],
    )
    writer.writeheader()
    writer.writerows(rows)
    data = buffer.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="campaign_{campaign_id}.csv"'},
    )


@router.get("/providers", response_model=list[ProviderOut])
async def providers() -> list[ProviderOut]:
    return [
        ProviderOut(
            type=p.type,
            display_name=p.display_name,
            enabled=False,
            available=p.available,
            description=p.description,
        )
        for p in list_providers()
    ]


@router.post("/providers/{provider_type}/send")
async def send_via_provider(
    provider_type: str,
    campaign_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    provider = get_provider(provider_type)
    if not provider:
        raise HTTPException(404, "Провайдер не найден")
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Кампания не найдена")

    result = await db.execute(
        select(EmailDraft)
        .where(
            EmailDraft.campaign_id == campaign_id,
            EmailDraft.status.in_([DraftStatus.ready, DraftStatus.edited]),
        )
        .options(selectinload(EmailDraft.recipient))
    )
    from app.providers.base import OutboundMessage

    messages = []
    for d in result.scalars().all():
        if not d.recipient or not d.subject or not d.body_text:
            continue
        messages.append(
            OutboundMessage(
                to_email=d.recipient.email,
                subject=d.subject,
                body_text=d.body_text,
                body_html=d.body_html,
                recipient_name=d.recipient.name,
            )
        )
    send_result = await provider.send_batch(messages)
    return {
        "provider": send_result.provider,
        "sent": send_result.sent,
        "failed": send_result.failed,
        "message": send_result.message,
    }
