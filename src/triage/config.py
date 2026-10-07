from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from triage.decisions import Backend


class Settings(BaseSettings):
    """Read from TRIAGE_* environment variables or a .env file."""

    model_config = SettingsConfigDict(env_prefix="TRIAGE_", env_file=".env", extra="ignore")

    backend: Backend = Backend.FAKE
    database_url: str = "sqlite+aiosqlite:///./triage.db"
    routing: Literal["cost", "threshold"] = "cost"
    review_cost: float = Field(2.0, ge=0)
    """Cost routing: what a quick human check costs, in the same units as triage.costs."""
    escalate_cost: float = Field(10.0, ge=0)
    """Cost routing: expected mistake cost above which a person handles the ticket from scratch."""
    auto_threshold: float = Field(0.85, ge=0, le=1)
    review_threshold: float = Field(0.60, ge=0, le=1)
    audit_rate: float = Field(0.05, ge=0, le=1)
    """Share of AUTO tickets also sent for human review, to measure automated accuracy."""
    model: str | None = None
    """Model name override; the SDK default is jev-latest."""
    request_timeout: float = Field(5.0, gt=0)
    typesafe_api_key: SecretStr | None = Field(None, validation_alias="TYPESAFE_API_KEY")
    """A TypeSafe key, or an OpenRouter key when jev_base_url points at OpenRouter."""
    jev_base_url: str | None = None
    """Defaults to TypeSafe's API; https://openrouter.ai/api serves the same protocol."""
    laya_base_url: str | None = None
    laya_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = Field(None, validation_alias="OPENROUTER_API_KEY")
    """Benchmark only, for the LLMs. Falls back to TYPESAFE_API_KEY when Jev itself goes through OpenRouter."""

    def openrouter_key(self) -> str | None:
        if self.openrouter_api_key:
            return self.openrouter_api_key.get_secret_value()
        if self.typesafe_api_key and self.jev_base_url and "openrouter.ai" in self.jev_base_url:
            return self.typesafe_api_key.get_secret_value()
        return None
