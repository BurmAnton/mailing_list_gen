from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider: openrouter | runpod
    llm_provider: str = "openrouter"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_default_model: str = "perplexity/sonar-pro"
    openrouter_http_referer: str = "http://localhost:5173"
    openrouter_app_title: str = "MailCraft"
    openrouter_web_plugin: bool = True  # enable openrouter:web_search server tool when requested

    runpod_api_key: str = ""
    runpod_endpoint_url: str = "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/openai/v1"
    runpod_model: str = "default"

    generation_concurrency: int = 3
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2
    # backward-compatible aliases
    runpod_timeout_seconds: int | None = None
    runpod_max_retries: int | None = None

    database_url: str = "sqlite+aiosqlite:///./data/mailing.db"
    cors_origins: str = "http://localhost:5173"

    # Yandex OAuth (Mail SMTP + Metrika)
    yandex_oauth_client_id: str = ""
    yandex_oauth_client_secret: str = ""
    yandex_oauth_redirect_uri: str = "https://oauth.yandex.ru/verification_code"
    yandex_oauth_scopes: str = "mail:smtp mail:imap_ro metrika:read"
    yandex_mail_from: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    tracking_hmac_secret: str = "mailcraft-dev-tracking-secret"
    send_throttle_seconds: float = 45.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def timeout_seconds(self) -> int:
        return self.runpod_timeout_seconds or self.llm_timeout_seconds

    @property
    def max_retries(self) -> int:
        return self.runpod_max_retries if self.runpod_max_retries is not None else self.llm_max_retries


@lru_cache
def get_settings() -> Settings:
    return Settings()
