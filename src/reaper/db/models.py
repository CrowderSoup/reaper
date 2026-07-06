"""SQLAlchemy 2.x async models, shared by the bot and web processes.

Every tenant-scoped table carries guild_id as a foreign key to Guild (spec 3.2/4).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    # every dt.datetime column is timestamptz -- the app deals exclusively in
    # timezone-aware UTC datetimes (e.g. burst_detector's window_expires_at),
    # and asyncpg rejects binding an aware datetime against a naive column.
    type_annotation_map = {
        dt.datetime: DateTime(timezone=True),
    }


class Guild(Base):
    """Tenant registry. One row per Discord guild Reaper is installed in."""

    __tablename__ = "guilds"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    icon_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    installed_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    # e.g. ["spam_defense"]
    enabled_modules: Mapped[list[str]] = mapped_column(JSON, default=list)

    # role IDs that count as "mod" for admin-UI access (spec 3.3)
    mod_role_ids: Mapped[list[int]] = mapped_column(JSON, default=list)

    # per-guild Module 1 config, overrides the fallback defaults in config.py
    mod_alert_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    timeout_seconds: Mapped[int | None] = mapped_column(nullable=True)
    burst_window_seconds: Mapped[int | None] = mapped_column(nullable=True)
    burst_channel_threshold: Mapped[int | None] = mapped_column(nullable=True)


class ModAction(Base):
    """Audit log row for every automated (or manual) moderation action."""

    __tablename__ = "mod_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guilds.guild_id"), index=True)

    action_type: Mapped[str] = mapped_column(String(20))  # timeout | ban | alert_only
    target_user_id: Mapped[int] = mapped_column(BigInteger)
    trigger_reason: Mapped[str] = mapped_column(Text)
    matched_pattern: Mapped[str] = mapped_column(String(30))  # cross_channel_burst | image_hash_match | manual

    channel_ids: Mapped[list[int]] = mapped_column(JSON, default=list)

    # content + attachment URLs captured *before* deletion (spec 5.3 / incident report gap)
    message_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), index=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ScamImageHash(Base):
    """Perceptual hash of a known scam image template.

    guild_id is nullable: the hash list is global by default (confirmed decision,
    spec section 9) so a hash hit on one guild protects every guild running Reaper.
    """

    __tablename__ = "scam_image_hashes"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("guilds.guild_id"), nullable=True, index=True)

    phash: Mapped[str] = mapped_column(String(64), index=True)
    source_incident_id: Mapped[int | None] = mapped_column(ForeignKey("mod_actions.id"), nullable=True)
    added_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class BurstWindow(Base):
    """Transient tracking table for cross-channel burst detection (spec 5.1).

    A cheap cleanup job (APScheduler) deletes expired rows; move to Redis only
    if write volume becomes a real concern (spec 8).
    """

    __tablename__ = "burst_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guilds.guild_id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))

    channel_ids_hit: Mapped[list[int]] = mapped_column(JSON, default=list)

    first_seen_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    window_expires_at: Mapped[dt.datetime] = mapped_column(index=True)

    __table_args__ = (
        Index("ix_burst_windows_lookup", "guild_id", "user_id", "content_hash"),
    )
