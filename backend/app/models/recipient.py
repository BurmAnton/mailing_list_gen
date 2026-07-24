from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Recipient(Base):
    __tablename__ = "recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    enrichment_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_row: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_fields_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    campaign_links: Mapped[list["CampaignRecipient"]] = relationship(back_populates="recipient")
    drafts: Mapped[list["EmailDraft"]] = relationship(back_populates="recipient")


from app.models.campaign import CampaignRecipient  # noqa: E402
from app.models.draft import EmailDraft  # noqa: E402
