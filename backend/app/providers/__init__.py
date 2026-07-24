from app.providers.base import MailProvider, OutboundMessage, SendResult


class YandexSmtpProvider:
    """Заглушка под Яндекс.Почту (SMTP). Реальная отправка — в следующей итерации."""

    type = "yandex_smtp"
    display_name = "Яндекс.Почта (SMTP)"
    description = (
        "Отправка через smtp.yandex.ru. Потребуются логин и пароль приложения. "
        "Интеграция пока не подключена."
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
            message="Яндекс.Почта ещё не подключена. Используйте экспорт CSV/JSON.",
        )


class MailingServiceProvider:
    """Заглушка под сервисы рассылок (UniSender / SendPulse и т.п.)."""

    type = "mailing_service"
    display_name = "Сервис рассылок"
    description = (
        "Интеграция с UniSender / SendPulse / аналогами через API. "
        "Пока недоступна — используйте экспорт."
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
            message="Сервис рассылок ещё не подключен. Используйте экспорт CSV/JSON.",
        )


PROVIDERS: dict[str, MailProvider] = {
    YandexSmtpProvider.type: YandexSmtpProvider(),
    MailingServiceProvider.type: MailingServiceProvider(),
}


def list_providers() -> list[MailProvider]:
    return list(PROVIDERS.values())


def get_provider(provider_type: str) -> MailProvider | None:
    return PROVIDERS.get(provider_type)
