"""Runtime configuration: one pydantic-settings object, validated at startup.

Every field maps to an environment variable (see `.env.example`). Secrets are
`SecretStr` so they never appear in logs or `repr(settings)`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "claude-sonnet-5-5"


class ConfigError(ValueError):
    """Raised when the configuration is incomplete for the requested operation."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_parse_none_str="",  # `DOCPIPE_AS_OF=` in .env means "unset", not ""
        extra="ignore",
        populate_by_name=True,
    )

    # --- Claude ---
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    model: str = Field(default=DEFAULT_MODEL, alias="DOCPIPE_MODEL")
    llm_timeout_seconds: float = Field(default=120.0, alias="DOCPIPE_LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=3, alias="DOCPIPE_LLM_MAX_RETRIES", ge=0)
    mock: bool = Field(default=False, alias="DOCPIPE_MOCK")

    # --- Storage ---
    database_url: str = Field(
        default=f"sqlite:///{ROOT / 'out' / 'docpipe.db'}",
        validation_alias=AliasChoices("DATABASE_URL", "DOCPIPE_DATABASE_URL"),
    )
    auto_create_schema: bool | None = Field(default=None, alias="DOCPIPE_AUTO_CREATE_SCHEMA")

    # --- Paths ---
    inbox_dir: Path = Field(default=ROOT / "inbox", alias="DOCPIPE_INBOX_DIR")
    data_dir: Path = Field(default=ROOT / "data", alias="DOCPIPE_DATA_DIR")
    out_dir: Path = Field(default=ROOT / "out", alias="DOCPIPE_OUT_DIR")
    fixtures_file: Path = Field(
        default=ROOT / "tests" / "fixtures" / "extractions.json", alias="DOCPIPE_FIXTURES_FILE"
    )
    as_of: date | None = Field(default=None, alias="DOCPIPE_AS_OF")

    # --- Ingestion ---
    ingest_mode: Literal["folder", "imap"] = Field(default="folder", alias="DOCPIPE_INGEST_MODE")
    imap_host: str | None = Field(default=None, alias="DOCPIPE_IMAP_HOST")
    imap_port: int = Field(default=993, alias="DOCPIPE_IMAP_PORT")
    imap_user: str | None = Field(default=None, alias="DOCPIPE_IMAP_USER")
    imap_password: SecretStr | None = Field(default=None, alias="DOCPIPE_IMAP_PASSWORD")
    imap_mailbox: str = Field(default="INBOX", alias="DOCPIPE_IMAP_MAILBOX")
    imap_done_mailbox: str = Field(default="AP/Processed", alias="DOCPIPE_IMAP_DONE_MAILBOX")
    imap_ssl: bool = Field(default=True, alias="DOCPIPE_IMAP_SSL")

    # --- Review API ---
    api_key: SecretStr | None = Field(default=None, alias="DOCPIPE_API_KEY")
    api_host: str = Field(default="0.0.0.0", alias="DOCPIPE_API_HOST")
    api_port: int = Field(default=8000, alias="DOCPIPE_API_PORT")

    # --- Exceptions sink ---
    sink: Literal["csv", "sheets"] = Field(default="csv", alias="DOCPIPE_SINK")
    sheets_spreadsheet_id: str | None = Field(default=None, alias="DOCPIPE_SHEETS_SPREADSHEET_ID")
    sheets_worksheet: str = Field(default="exceptions", alias="DOCPIPE_SHEETS_WORKSHEET")
    google_credentials_file: Path | None = Field(
        default=None, alias="GOOGLE_APPLICATION_CREDENTIALS"
    )

    # --- Scheduler / logging ---
    run_interval_seconds: int = Field(default=300, alias="DOCPIPE_RUN_INTERVAL_SECONDS", ge=1)
    log_level: str = Field(default="INFO", alias="DOCPIPE_LOG_LEVEL")
    log_format: Literal["json", "console"] = Field(default="json", alias="DOCPIPE_LOG_FORMAT")

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.ingest_mode == "imap":
            missing = [
                name
                for name, value in (
                    ("DOCPIPE_IMAP_HOST", self.imap_host),
                    ("DOCPIPE_IMAP_USER", self.imap_user),
                    ("DOCPIPE_IMAP_PASSWORD", self.imap_password),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"DOCPIPE_INGEST_MODE=imap requires {', '.join(missing)}")
        if self.sink == "sheets" and not (
            self.sheets_spreadsheet_id and self.google_credentials_file
        ):
            raise ValueError(
                "DOCPIPE_SINK=sheets requires DOCPIPE_SHEETS_SPREADSHEET_ID and "
                "GOOGLE_APPLICATION_CREDENTIALS"
            )
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def should_create_schema(self) -> bool:
        """SQLite databases are created on the fly; Postgres is managed by alembic unless
        DOCPIPE_AUTO_CREATE_SCHEMA=true is set explicitly."""
        return self.is_sqlite if self.auto_create_schema is None else self.auto_create_schema

    def require_llm(self) -> None:
        if not self.mock and self.anthropic_api_key is None:
            raise ConfigError("ANTHROPIC_API_KEY is not set (or run with --mock / DOCPIPE_MOCK=1)")

    def require_api_key(self) -> str:
        if self.api_key is None or not self.api_key.get_secret_value():
            raise ConfigError("DOCPIPE_API_KEY must be set to serve the review API")
        return self.api_key.get_secret_value()


def load_settings(**overrides: object) -> Settings:
    """Build Settings from the environment (+ .env) with explicit overrides on top."""
    return Settings(**overrides)  # type: ignore[arg-type]
