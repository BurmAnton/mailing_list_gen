import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SendStatus(str, enum.Enum):
    queued = "queued"
    sending = "sending"
    sent = "sent"
    failed = "failed"


class EventType(str, enum.Enum):
    open = "open"
    click = "click"


class AssetKind(str, enum.Enum):
    banner_html = "banner_html"
    banner_url = "banner_url"


class EmailSend(Base):
    __tablename__ = "email_sends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[SendStatus] = mapped_column(
        Enum(SendStatus), default=SendStatus.queued, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_in_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracking_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    events: Mapped[list["EmailEvent"]] = relationship(
        back_populates="send", cascade="all, delete-orphan"
    )


class EmailEvent(Base):
    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    send_id: Mapped[int] = mapped_column(
        ForeignKey("email_sends.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[EventType] = mapped_column(Enum(EventType), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    send: Mapped["EmailSend"] = relationship(back_populates="events")


class CampaignAsset(Base):
    __tablename__ = "campaign_assets"
    __table_args__ = (UniqueConstraint("campaign_id", "kind", name="uq_campaign_asset_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[AssetKind] = mapped_column(Enum(AssetKind), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
