import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    generating = "generating"
    ready = "ready"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(32), default="openrouter", nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    use_web_search: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_segment_templates: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    segment_field: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rag_digest_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rag_digest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus), default=CampaignStatus.draft, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    recipients: Mapped[list["CampaignRecipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    drafts: Mapped[list["EmailDraft"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    rag_files: Mapped[list["CampaignRagFile"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    segments: Mapped[list["CampaignSegment"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("recipients.id", ondelete="CASCADE"))

    campaign: Mapped["Campaign"] = relationship(back_populates="recipients")
    recipient: Mapped["Recipient"] = relationship(back_populates="campaign_links")


from app.models.draft import EmailDraft  # noqa: E402
from app.models.rag_file import CampaignRagFile  # noqa: E402
from app.models.recipient import Recipient  # noqa: E402
from app.models.segment import CampaignSegment  # noqa: E402
