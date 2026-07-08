from __future__ import annotations

import datetime as dt

from reaper.db.models import BurstWindow
from reaper.db.repositories.burst_windows import BurstWindowRepository, delete_expired


async def test_record_hit_creates_new_window(session, make_guild):
    await make_guild(guild_id=1)
    repo = BurstWindowRepository(session, guild_id=1)

    window = await repo.record_hit(user_id=42, content_hash="abc", channel_id=10, window_seconds=60)

    assert window.channel_ids_hit == [10]
    assert window.window_expires_at > dt.datetime.now(dt.timezone.utc)


async def test_record_hit_reuses_unexpired_window_and_adds_channel(session, make_guild):
    await make_guild(guild_id=1)
    repo = BurstWindowRepository(session, guild_id=1)

    first = await repo.record_hit(user_id=42, content_hash="abc", channel_id=10, window_seconds=60)
    second = await repo.record_hit(user_id=42, content_hash="abc", channel_id=20, window_seconds=60)

    assert second.id == first.id
    assert set(second.channel_ids_hit) == {10, 20}


async def test_record_hit_does_not_duplicate_repeated_channel(session, make_guild):
    await make_guild(guild_id=1)
    repo = BurstWindowRepository(session, guild_id=1)

    await repo.record_hit(user_id=42, content_hash="abc", channel_id=10, window_seconds=60)
    window = await repo.record_hit(user_id=42, content_hash="abc", channel_id=10, window_seconds=60)

    assert window.channel_ids_hit == [10]


async def test_record_hit_starts_new_window_after_expiry(session, make_guild):
    await make_guild(guild_id=1)
    repo = BurstWindowRepository(session, guild_id=1)

    first = await repo.record_hit(user_id=42, content_hash="abc", channel_id=10, window_seconds=-1)
    second = await repo.record_hit(user_id=42, content_hash="abc", channel_id=20, window_seconds=60)

    assert second.id != first.id
    assert second.channel_ids_hit == [20]


def test_distinct_channel_count():
    window = BurstWindow(channel_ids_hit=[10, 20, 10, 30])
    assert BurstWindowRepository.distinct_channel_count(window) == 3


async def test_delete_expired_removes_only_expired_rows(session, make_guild):
    await make_guild(guild_id=1)
    repo = BurstWindowRepository(session, guild_id=1)

    expired = await repo.record_hit(user_id=1, content_hash="expired", channel_id=1, window_seconds=-10)
    active = await repo.record_hit(user_id=2, content_hash="active", channel_id=2, window_seconds=600)

    deleted_count = await delete_expired(session)
    await session.flush()

    assert deleted_count == 1
    assert await session.get(BurstWindow, expired.id) is None
    assert await session.get(BurstWindow, active.id) is not None
