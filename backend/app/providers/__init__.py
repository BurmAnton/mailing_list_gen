from app.providers.base import MailProvider, OutboundMessage, SendResult


class YandexSmtpProvider:
    """Wrapper: available when OAuth credentials are configured at send-time."""

    type = "yandex_smtp"
    display_name = "Яндекс.Почта (SMTP XOAUTH2)"
    description = (
        "Отправка через smtp.yandex.com с OAuth. "
        "Подключите аккаунт на вкладке «Интеграции»."
    )

    @property
    def available(self) -> bool:
        return True

    async def validate(self) -> bool:
        return True

    async def send_batch(self, messages: list[OutboundMessage]) -> SendResult:
        return SendResult(
            provider=self.type,
            sent=0,
            failed=len(messages),
            message="Используйте POST /api/campaigns/{id}/send",
        )


class MailingServiceProvider:
    type = "mailing_service"
    display_name = "Сервис рассылок"
    description = (
        "Интеграция с UniSender / SendPulse / аналогами через API. "
        "Пока недоступна — используйте Яндекс.Почту."
    )

    @property
    def available(self) -> bool:
        return False

    async def validate(self) -> bool:
        return False

    async def send_batch(self, messages: list[OutboundMessage]) -> SendResult:
        return SendResult(
            provider=self.type,
            sent=0,
            failed=len(messages),
            message="Сервис рассылок ещё не подключен.",
        )


PROVIDERS: dict[str, MailProvider] = {
    YandexSmtpProvider.type: YandexSmtpProvider(),
    MailingServiceProvider.type: MailingServiceProvider(),
}


def list_providers() -> list[MailProvider]:
    return list(PROVIDERS.values())


def get_provider(provider_type: str) -> MailProvider | None:
    return PROVIDERS.get(provider_type)


__all__ = [
    "PROVIDERS",
    "YandexSmtpProvider",
    "MailingServiceProvider",
    "get_provider",
    "list_providers",
]
