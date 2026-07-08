from __future__ import annotations

import datetime as dt

from reaper.db.repositories.mod_actions import ModActionRepository


async def _create_action(repo: ModActionRepository, *, target_user_id: int = 42, matched_pattern: str = "manual"):
    return await repo.create(
        action_type="alert_only",
        target_user_id=target_user_id,
        trigger_reason="test",
        matched_pattern=matched_pattern,
        channel_ids=[10],
        message_snapshot={"content": "hi"},
    )


async def test_create_persists_action(session, make_guild):
    await make_guild(guild_id=1)
    repo = ModActionRepository(session, guild_id=1)

    action = await _create_action(repo)

    assert action.id is not None
    assert action.guild_id == 1
    assert action.reviewed_by is None


async def test_list_recent_orders_newest_first_and_respects_limit(session, make_guild):
    await make_guild(guild_id=1)
    repo = ModActionRepository(session, guild_id=1)
    # SQLite's CURRENT_TIMESTAMP only has 1-second resolution, so rows created
    # back-to-back can tie on created_at -- assign explicit increasing
    # timestamps to exercise the ordering deterministically.
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    for i in range(5):
        action = await _create_action(repo, target_user_id=i)
        action.created_at = base + dt.timedelta(seconds=i)
    await session.flush()

    rows = await repo.list_recent(limit=3)

    assert [r.target_user_id for r in rows] == [4, 3, 2]


async def test_list_for_user_filters_by_guild_and_user(session, make_guild):
    await make_guild(guild_id=1)
    await make_guild(guild_id=2)
    repo_g1 = ModActionRepository(session, guild_id=1)
    repo_g2 = ModActionRepository(session, guild_id=2)

    await _create_action(repo_g1, target_user_id=42)
    await _create_action(repo_g1, target_user_id=99)
    await _create_action(repo_g2, target_user_id=42)

    rows = await repo_g1.list_for_user(42)

    assert len(rows) == 1
    assert rows[0].guild_id == 1
    assert rows[0].target_user_id == 42


async def test_mark_reviewed_updates_matching_guild_action(session, make_guild):
    await make_guild(guild_id=1)
    repo = ModActionRepository(session, guild_id=1)
    action = await _create_action(repo)

    reviewed = await repo.mark_reviewed(action.id, reviewer_user_id=555)

    assert reviewed is not None
    assert reviewed.reviewed_by == 555


async def test_mark_reviewed_returns_none_for_wrong_guild(session, make_guild):
    await make_guild(guild_id=1)
    await make_guild(guild_id=2)
    repo_g1 = ModActionRepository(session, guild_id=1)
    repo_g2 = ModActionRepository(session, guild_id=2)
    action = await _create_action(repo_g1)

    result = await repo_g2.mark_reviewed(action.id, reviewer_user_id=555)

    assert result is None


async def test_mark_reviewed_returns_none_for_missing_action(session, make_guild):
    await make_guild(guild_id=1)
    repo = ModActionRepository(session, guild_id=1)

    result = await repo.mark_reviewed(9999, reviewer_user_id=555)

    assert result is None
