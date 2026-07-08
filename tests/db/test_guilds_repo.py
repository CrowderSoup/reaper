from __future__ import annotations

from reaper.db.repositories.guilds import GuildRepository


async def test_get_returns_none_for_missing_guild(session):
    repo = GuildRepository(session)
    assert await repo.get(999) is None


async def test_upsert_inserts_new_guild(session):
    repo = GuildRepository(session)
    guild = await repo.upsert(1, name="AFSPECWAR", icon_hash="abc123")

    assert guild.guild_id == 1
    assert guild.name == "AFSPECWAR"
    assert guild.icon_hash == "abc123"
    assert guild.enabled_modules == []

    fetched = await repo.get(1)
    assert fetched is not None
    assert fetched.name == "AFSPECWAR"


async def test_upsert_updates_existing_guild_name_and_icon(session):
    repo = GuildRepository(session)
    await repo.upsert(1, name="Old Name")

    updated = await repo.upsert(1, name="New Name", icon_hash="newicon")

    assert updated.name == "New Name"
    assert updated.icon_hash == "newicon"


async def test_list_all_returns_every_guild(session):
    repo = GuildRepository(session)
    await repo.upsert(1, name="Guild One")
    await repo.upsert(2, name="Guild Two")

    guilds = await repo.list_all()

    assert {g.guild_id for g in guilds} == {1, 2}
