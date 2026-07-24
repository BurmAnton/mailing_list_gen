from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class OutboundMessage:
    to_email: str
    subject: str
    body_text: str
    body_html: str | None = None
    recipient_name: str | None = None


@dataclass
class SendItemResult:
    to_email: str
    success: bool
    error: str | None = None
    provider_message_id: str | None = None


@dataclass
class SendResult:
    provider: str
    sent: int = 0
    failed: int = 0
    items: list[SendItemResult] = field(default_factory=list)
    message: str = ""


class MailProvider(Protocol):
    type: str
    display_name: str
    description: str

    @property
    def available(self) -> bool: ...

    async def validate(self) -> bool: ...

    async def send_batch(self, messages: list[OutboundMessage]) -> SendResult: ...
