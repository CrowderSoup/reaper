"""Environment-driven settings shared by the bot and web processes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _list_env(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _int_env(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # Discord application
    discord_bot_token: str = field(default_factory=lambda: os.environ["DISCORD_BOT_TOKEN"])
    discord_client_id: str = field(default_factory=lambda: os.environ["DISCORD_CLIENT_ID"])
    discord_client_secret: str = field(default_factory=lambda: os.environ["DISCORD_CLIENT_SECRET"])
    oauth_redirect_uri: str = field(
        default_factory=lambda: os.environ.get(
            "OAUTH_REDIRECT_URI", "https://reaper.crowdersoup.com/auth/callback"
        )
    )

    # Database
    database_url: str = field(default_factory=lambda: os.environ["DATABASE_URL"])

    # Web session
    session_secret_key: str = field(default_factory=lambda: os.environ["SESSION_SECRET_KEY"])
    session_max_age_seconds: int = field(default_factory=lambda: _int_env("SESSION_MAX_AGE_SECONDS", 6 * 3600))

    # config-level allowlist of Discord user IDs that bypass per-guild checks
    # for support/debug only -- never a normal user-facing path (spec 3.2)
    bot_superadmin_ids: list[str] = field(default_factory=lambda: _list_env("BOT_SUPERADMIN_IDS"))

    # Module 1 defaults (per-guild overrides live in Guild config, these are fallbacks)
    default_burst_window_seconds: int = field(default_factory=lambda: _int_env("DEFAULT_BURST_WINDOW_SECONDS", 60))
    default_burst_channel_threshold: int = field(default_factory=lambda: _int_env("DEFAULT_BURST_CHANNEL_THRESHOLD", 3))
    default_timeout_seconds: int = field(default_factory=lambda: _int_env("DEFAULT_TIMEOUT_SECONDS", 3600))
    scam_image_hamming_threshold: int = field(default_factory=lambda: _int_env("SCAM_IMAGE_HAMMING_THRESHOLD", 10))

    # /reaper roast (mod-only, LLM-generated) -- empty api key disables the command
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    roast_model: str = field(default_factory=lambda: os.environ.get("ROAST_MODEL", "claude-haiku-4-5"))
    roast_max_messages: int = field(default_factory=lambda: _int_env("ROAST_MAX_MESSAGES", 25))
    roast_channel_scan_limit: int = field(default_factory=lambda: _int_env("ROAST_CHANNEL_SCAN_LIMIT", 300))


@lru_cache
def get_settings() -> Settings:
    return Settings()
