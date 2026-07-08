from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from reaper.bot.cogs.spam_defense import (
    _EULOGY_BOT_LINE,
    _EULOGY_CLEAN_LINES,
    _EULOGY_PATTERN_LINES,
    SpamDefenseCog,
    _is_mod,
)
from reaper.db.repositories.mod_actions import ModActionRepository


@pytest.fixture
def cog() -> SpamDefenseCog:
    return SpamDefenseCog(bot=MagicMock())


@pytest.fixture(autouse=True)
def patch_get_session(monkeypatch, session):
    @asynccontextmanager
    async def _fake_get_session():
        yield session

    monkeypatch.setattr("reaper.bot.cogs.spam_defense.get_session", _fake_get_session)


async def _create_action(session, guild_id: int, target_user_id: int, matched_pattern: str = "manual"):
    repo = ModActionRepository(session, guild_id=guild_id)
    action = await repo.create(
        action_type="alert_only",
        target_user_id=target_user_id,
        trigger_reason="test",
        matched_pattern=matched_pattern,
        channel_ids=[10],
        message_snapshot={"content": "hi"},
    )
    await session.commit()
    return action


def _history_command():
    return SpamDefenseCog.reaper.get_command("history").callback


def _eulogy_command():
    return SpamDefenseCog.reaper.get_command("eulogy").callback


# -- _is_mod --------------------------------------------------------------


def test_is_mod_true_for_manage_guild_permission(make_member, make_guild):
    member = make_member(manage_guild=True)
    guild = MagicMock(mod_role_ids=[])
    assert _is_mod(member, guild) is True


def test_is_mod_true_for_mod_role_membership(make_member):
    member = make_member(manage_guild=False, role_ids=[555])
    guild = MagicMock(mod_role_ids=[555])
    assert _is_mod(member, guild) is True


def test_is_mod_false_without_permission_or_role(make_member):
    member = make_member(manage_guild=False, role_ids=[111])
    guild = MagicMock(mod_role_ids=[555])
    assert _is_mod(member, guild) is False


# -- /reaper history --------------------------------------------------------


async def test_history_denies_non_mod_caller(session, make_guild, make_member, make_interaction, cog):
    await make_guild(guild_id=1, mod_role_ids=[])
    caller = make_member(manage_guild=False, role_ids=[])
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=42)

    await _history_command()(cog, interaction, user=target, count=10)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    assert "permission" in interaction.response.send_message.call_args.args[0]
    assert kwargs["ephemeral"] is True


async def test_history_reports_no_actions_for_mod_caller(session, make_guild, make_member, make_interaction, cog):
    await make_guild(guild_id=1)
    caller = make_member(manage_guild=True)
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=42)

    await _history_command()(cog, interaction, user=target, count=10)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "No recorded actions" in args[0]
    assert kwargs["ephemeral"] is True


async def test_history_shows_embed_with_actions_for_mod_caller(session, make_guild, make_member, make_interaction, cog):
    await make_guild(guild_id=1)
    caller = make_member(manage_guild=True)
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=42)
    await _create_action(session, guild_id=1, target_user_id=42)
    await _create_action(session, guild_id=1, target_user_id=42)

    await _history_command()(cog, interaction, user=target, count=10)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs["embed"]
    assert "2 most recent" in embed.footer.text
    assert kwargs["ephemeral"] is True


# -- /reaper eulogy ---------------------------------------------------------


async def test_eulogy_short_circuits_for_bot_user(session, make_interaction, make_member, cog):
    interaction = make_interaction()
    target = make_member(user_id=99, bot=True)

    await _eulogy_command()(cog, interaction, user=target)

    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs["embed"]
    assert embed.description == _EULOGY_BOT_LINE


async def test_eulogy_uses_clean_line_when_no_history(session, make_guild, make_interaction, make_member, cog):
    await make_guild(guild_id=1)
    interaction = make_interaction(guild_id=1)
    target = make_member(user_id=42, bot=False)

    await _eulogy_command()(cog, interaction, user=target)

    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs["embed"]
    assert embed.description in _EULOGY_CLEAN_LINES


async def test_eulogy_reports_pattern_and_count_with_history(session, make_guild, make_interaction, make_member, cog):
    await make_guild(guild_id=1)
    interaction = make_interaction(guild_id=1)
    target = make_member(user_id=42, bot=False)
    await _create_action(session, guild_id=1, target_user_id=42, matched_pattern="image_hash_match")
    await _create_action(session, guild_id=1, target_user_id=42, matched_pattern="image_hash_match")
    await _create_action(session, guild_id=1, target_user_id=42, matched_pattern="cross_channel_burst")

    await _eulogy_command()(cog, interaction, user=target)

    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs["embed"]
    assert any(line in embed.description for line in _EULOGY_PATTERN_LINES["image_hash_match"])
    assert "Reaped 3 times" in embed.footer.text
    assert "image_hash_match" in embed.footer.text
