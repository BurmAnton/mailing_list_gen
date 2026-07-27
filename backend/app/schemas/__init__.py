from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.campaign import CampaignStatus
from app.models.draft import DraftStatus
from app.models.segment import TemplateStatus


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    base_prompt: str = Field(min_length=1)
    llm_provider: str = "openrouter"
    llm_model: str | None = None
    use_web_search: bool = False


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_prompt: str | None = Field(default=None, min_length=1)
    llm_provider: str | None = None
    llm_model: str | None = None
    use_web_search: bool | None = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    base_prompt: str
    llm_provider: str = "openrouter"
    llm_model: str | None = None
    use_web_search: bool = False
    use_segment_templates: bool = False
    segment_field: str | None = None
    metrika_counter_id: int | None = None
    metrika_goal_id: int | None = None
    landing_url: str | None = None
    status: CampaignStatus
    created_at: datetime | None = None
    updated_at: datetime | None = None
    recipient_count: int = 0
    draft_count: int = 0
    rag_file_count: int = 0


class RecipientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr | str
    name: str | None = None
    company_name: str
    industry: str | None = None
    description: str | None = None
    website_url: str | None = None
    enrichment_json: str | None = None
    extra_fields: dict[str, Any] | None = None


class ImportResult(BaseModel):
    imported: int
    skipped_duplicates: int
    skipped_invalid: int
    recipients: list[RecipientOut]


class GenerateRequest(BaseModel):
    enrich_websites: bool = False


class CampaignIdsIn(BaseModel):
    ids: list[int] = Field(min_length=1)


class CampaignStatusOut(BaseModel):
    campaign_id: int
    status: CampaignStatus
    total: int
    pending: int
    generating: int
    ready: int
    error: int
    edited: int
    worker_alive: bool = False
    use_segment_templates: bool = False
    segments: list["SegmentProgressOut"] = []


class SegmentProgressOut(BaseModel):
    segment_id: int | None = None
    value: str = ""
    label: str
    total: int = 0
    pending: int = 0
    generating: int = 0
    ready: int = 0
    error: int = 0
    edited: int = 0


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    recipient_id: int
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    status: DraftStatus
    error: str | None = None
    recipient: RecipientOut | None = None
    updated_at: datetime | None = None


class DraftUpdate(BaseModel):
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None


class ProviderOut(BaseModel):
    type: str
    display_name: str
    enabled: bool
    available: bool
    description: str


class LLMModelOut(BaseModel):
    id: str
    name: str
    description: str = ""
    context_length: int | None = None
    supported_parameters: list[str] = []
    supports_tools: bool = False
    pricing_prompt: str | None = None
    pricing_completion: str | None = None
    price_prompt_per_1m: float | None = None
    price_completion_per_1m: float | None = None
    price_label: str = ""
    match_reasons: list[str] = []
    provider: str = "openrouter"


class RagFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    filename: str
    content_type: str | None = None
    source_type: str = "file"
    source_url: str | None = None
    char_count: int = 0
    created_at: datetime | None = None


class RagWebsiteIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class CostEstimateOut(BaseModel):
    campaign_id: int
    recipient_count: int
    model_id: str | None = None
    price_label: str = ""
    estimated_prompt_tokens: int
    estimated_completion_tokens: int
    estimated_digest_tokens: int
    estimated_cost_usd: float
    notes: list[str] = []


class SegmentFieldOut(BaseModel):
    key: str
    label: str
    non_empty_count: int = 0
    sample_values: list[str] = []


class SegmentConfigIn(BaseModel):
    use_segment_templates: bool = False
    segment_field: str | None = None


class ZoneOut(BaseModel):
    name: str
    hint: str = ""


class SegmentTemplateOut(BaseModel):
    id: int
    segment_id: int
    variant_key: str = "A"
    subject_template: str = ""
    body_template: str = ""
    zones: list[ZoneOut] = []
    status: TemplateStatus = TemplateStatus.draft


class SegmentTemplateUpdate(BaseModel):
    subject_template: str | None = None
    body_template: str | None = None
    zones: list[ZoneOut] | None = None
    status: TemplateStatus | None = None


class SegmentOut(BaseModel):
    id: int
    campaign_id: int
    value: str
    label: str
    recipient_count: int = 0
    template: SegmentTemplateOut | None = None
