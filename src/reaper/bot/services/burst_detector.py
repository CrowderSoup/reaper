"""Cross-channel burst detection (spec 5.1)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from reaper.bot.services.image_hash_matcher import hash_attachment
from reaper.db.repositories.burst_windows import BurstWindowRepository


async def content_hash_for_message(message: discord.Message) -> str | None:
    """Combined hash of text + attachment perceptual hashes. None if no role mention
    (only role-mention messages are burst-tracked, per spec 5.1).
    """
    if not message.role_mentions:
        return None

    attachment_hashes = sorted(
        h for a in message.attachments if (h := await hash_attachment(a)) is not None
    )
    digest_input = "|".join([message.content.strip(), *attachment_hashes])
    return hashlib.sha256(digest_input.encode()).hexdigest()


@dataclass
class BurstResult:
    triggered: bool
    distinct_channel_count: int
    channel_ids_hit: list[int]


async def evaluate_burst(
    session: AsyncSession,
    *,
    guild_id: int,
    message: discord.Message,
    window_seconds: int,
    channel_threshold: int,
) -> BurstResult | None:
    """Records this message's hit and reports whether the burst threshold has
    been crossed. Returns None if the message isn't burst-trackable (no role mention).
    """
    content_hash = await content_hash_for_message(message)
    if content_hash is None:
        return None

    repo = BurstWindowRepository(session, guild_id)
    window = await repo.record_hit(
        user_id=message.author.id,
        content_hash=content_hash,
        channel_id=message.channel.id,
        window_seconds=window_seconds,
    )
    count = repo.distinct_channel_count(window)
    return BurstResult(
        triggered=count >= channel_threshold,
        distinct_channel_count=count,
        channel_ids_hit=window.channel_ids_hit,
    )
