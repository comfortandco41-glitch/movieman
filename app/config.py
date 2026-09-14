"""Typed configuration loaded from environment variables.

Uses pydantic-settings to validate and type all configuration.
Fails fast if required values are missing.
Never logs secret values.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Application configuration loaded from .env file."""

    # Telegram
    telegram_bot_token: str = Field(..., description="Telegram bot token from @BotFather")
    telegram_channel_id: Optional[str] = Field(
        None,
        description="Public channel ID (e.g. @moviemanchannel) for uploads",
    )
    # Telethon MTProto (for up to 2GB uploads via user account)
    telegram_api_id: Optional[int] = Field(
        None,
        description="Telegram API ID from my.telegram.org for 2GB uploads",
    )
    telegram_api_hash: Optional[str] = Field(
        None,
        description="Telegram API Hash from my.telegram.org for 2GB uploads",
    )
    telethon_session: Optional[str] = Field(
        "telethon.session",
        description="Telethon session file path or StringSession",
    )

    # Mega Account (optional)
    mega_email: Optional[str] = Field(None, description="Mega.nz account email")
    mega_password: Optional[str] = Field(None, description="Mega.nz account password")

    # MMSubChannel
    mmsubchannel_base_url: str = Field(
        "https://mmsubchannel.com",
        description="Base URL for the movie source website",
    )

    # Auto-scrape
    scrape_interval_minutes: int = Field(
        0,
        ge=0,
        description="Auto-scrape interval in minutes (0 = disabled)",
    )

    # Workspace
    workspace_dir: Path = Field(Path("./workspace"), description="Base workspace directory")

    # Processing
    max_concurrent_jobs: int = Field(1, ge=1, le=10, description="Max parallel jobs")

    # Upload
    max_upload_size_mb: int = Field(
        2000,
        description="Max upload size in MB (50 for standard API, 2000 for Telethon)",
    )

    # Web Server (Movie Store Frontend)
    web_host: str = Field("0.0.0.0", description="Web server bind host")
    web_port: int = Field(8080, ge=1, le=65535, description="Web server port")

    # Turso Cloud SQLite (optional, falls back to local workspace/movie_store.db)
    turso_database_url: Optional[str] = Field(
        None,
        description="Turso database URL (e.g. libsql://movieman.turso.io or https://...)",
    )
    turso_auth_token: Optional[str] = Field(
        None,
        description="Turso auth token for cloud SQLite access",
    )

    # Logging
    log_level: str = Field("INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")

    # Playwright
    browser_type: str = Field("chromium", description="Browser: chromium, firefox, webkit")
    browser_headless: bool = Field(True, description="Run browser in headless mode")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }

    @field_validator("web_port", mode="before")
    @classmethod
    def resolve_port(cls, v: Any) -> Any:
        """Support standard PORT environment variable from Render / Railway."""
        port_env = os.getenv("PORT")
        if port_env:
            try:
                return int(port_env)
            except ValueError:
                pass
        return v

    @field_validator("workspace_dir")
    @classmethod
    def ensure_workspace_dir(cls, v: Path) -> Path:
        """Ensure workspace directory exists."""
        v.mkdir(parents=True, exist_ok=True)
        return v.resolve()

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return v

    @property
    def jobs_dir(self) -> Path:
        """Directory for job workspaces."""
        d = self.workspace_dir / "jobs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def max_upload_size_bytes(self) -> int:
        """Max upload size in bytes."""
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def has_channel(self) -> bool:
        """Whether a Telegram channel is configured."""
        return self.telegram_channel_id is not None

    @property
    def use_telethon(self) -> bool:
        """Whether Telethon MTProto is configured for 2GB uploads."""
        return bool(self.telegram_api_id and self.telegram_api_hash)

    @property
    def has_mega_account(self) -> bool:
        """Whether Mega account credentials are provided."""
        return bool(self.mega_email and self.mega_password)

    @property
    def auto_scrape_enabled(self) -> bool:
        """Whether automatic scraping is enabled."""
        return self.scrape_interval_minutes > 0

    def safe_repr(self) -> str:
        """Return a representation that masks secret values."""
        def mask(val: str | None) -> str:
            if not val:
                return "<not set>"
            if len(val) <= 8:
                return "****"
            return val[:4] + "****" + val[-4:]

        return (
            f"Config(\n"
            f"  telegram_bot_token={mask(self.telegram_bot_token)},\n"
            f"  telegram_channel_id={self.telegram_channel_id or '<not set>'},\n"
            f"  telegram_api_id={'<set>' if self.telegram_api_id else '<not set>'},\n"
            f"  telegram_api_hash={mask(self.telegram_api_hash)},\n"
            f"  use_telethon={self.use_telethon},\n"
            f"  mega_email={mask(self.mega_email)},\n"
            f"  mmsubchannel_base_url={self.mmsubchannel_base_url},\n"
            f"  scrape_interval_minutes={self.scrape_interval_minutes},\n"
            f"  workspace_dir={self.workspace_dir},\n"
            f"  max_concurrent_jobs={self.max_concurrent_jobs},\n"
            f"  max_upload_size_mb={self.max_upload_size_mb},\n"
            f"  browser_type={self.browser_type},\n"
            f"  browser_headless={self.browser_headless},\n"
            f"  web_port={self.web_port},\n"
            f"  turso_database_url={self.turso_database_url or '<not set>'},\n"
            f"  turso_auth_token={'<set>' if self.turso_auth_token else '<not set>'},\n"
            f")"
        )


def load_config(env_file: str | None = None) -> Config:
    """Load and validate configuration from environment.

    Args:
        env_file: Optional path to .env file. Defaults to .env in current dir.

    Returns:
        Validated Config instance.

    Raises:
        ValidationError: If required configuration is missing or invalid.
    """
    if env_file:
        os.environ.setdefault("ENV_FILE", env_file)
        return Config(_env_file=env_file)
    return Config()
