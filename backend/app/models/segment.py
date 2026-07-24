import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class TemplateStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"


class CampaignSegment(Base):
    __tablename__ = "campaign_segments"
    __table_args__ = (UniqueConstraint("campaign_id", "value", name="uq_campaign_segment_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    value: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    label: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    campaign: Mapped["Campaign"] = relationship(back_populates="segments")
    templates: Mapped[list["SegmentTemplate"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )


class SegmentTemplate(Base):
    __tablename__ = "segment_templates"
    __table_args__ = (
        UniqueConstraint("segment_id", "variant_key", name="uq_segment_template_variant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_segments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    variant_key: Mapped[str] = mapped_column(String(32), nullable=False, default="A")
    subject_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    zones_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[TemplateStatus] = mapped_column(
        Enum(TemplateStatus), default=TemplateStatus.draft, nullable=False
    )
    raw_llm_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    segment: Mapped["CampaignSegment"] = relationship(back_populates="templates")


from app.models.campaign import Campaign  # noqa: E402
