from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration from environment variables prefixed with PARTI_ (or a .env file)."""

    model_config = SettingsConfigDict(env_prefix="PARTI_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://parti@127.0.0.1:5432/parti"
    secret_key: str = Field(default="dev-only-change-me", min_length=16)
    base_url: str = "http://127.0.0.1:8000"

    # OpenID Connect. Locally this points at dev/mock_idp.py.
    oidc_issuer: str = "http://127.0.0.1:8081"
    oidc_client_id: str = "parti-local"
    oidc_client_secret: str = "parti-local-secret"
    oidc_scopes: str = "openid email profile"

    session_max_age_seconds: int = 8 * 60 * 60

    demo_user_email: str | None = None
    display_timezone: str = "Europe/Madrid"

    notify_to: str = ""
    notify_from: str = "Parti <parti@localhost>"
    notify_each_signup: bool = True
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True

    @model_validator(mode="after")
    def _no_dev_secrets_in_production(self) -> "Settings":
        if self.env == "production":
            if self.secret_key == "dev-only-change-me" or self.oidc_client_secret == "parti-local-secret":
                raise ValueError("Set PARTI_SECRET_KEY and PARTI_OIDC_CLIENT_SECRET for production.")
            if not self.base_url.startswith("https://"):
                raise ValueError("PARTI_BASE_URL must use https in production.")
            if self.demo_user_email:
                raise ValueError("PARTI_DEMO_USER_EMAIL is for local demos only; unset it in production.")
        return self

    @property
    def notify_recipients(self) -> list[str]:
        return [a.strip() for a in self.notify_to.split(",") if a.strip()]

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
