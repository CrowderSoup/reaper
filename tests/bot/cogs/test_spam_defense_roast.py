from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from anthropic import APIConnectionError

from reaper.bot.cogs.spam_defense import _EULOGY_BOT_LINE, RoastReviewView, SpamDefenseCog


@pytest.fixture
def cog() -> SpamDefenseCog:
    return SpamDefenseCog(bot=MagicMock())


@pytest.fixture(autouse=True)
def patch_get_session(monkeypatch, session):
    @asynccontextmanager
    async def _fake_get_session():
        yield session

    monkeypatch.setattr("reaper.bot.cogs.spam_defense.get_session", _fake_get_session)


def _fake_settings(*, anthropic_api_key: str = "test-key") -> SimpleNamespace:
    return SimpleNamespace(
        anthropic_api_key=anthropic_api_key,
        roast_model="claude-haiku-4-5",
        roast_max_messages=25,
        roast_channel_scan_limit=300,
    )


def _roast_command():
    return SpamDefenseCog.reaper.get_command("roast").callback


async def test_roast_denies_non_mod_caller(session, make_guild, make_member, make_interaction, cog):
    await make_guild(guild_id=1, mod_role_ids=[])
    caller = make_member(manage_guild=False, role_ids=[])
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=42)

    await _roast_command()(cog, interaction, user=target)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "permission" in args[0]
    assert kwargs["ephemeral"] is True


async def test_roast_short_circuits_for_bot_user(session, make_guild, make_interaction, make_member, cog):
    await make_guild(guild_id=1)
    caller = make_member(manage_guild=True)
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=99, bot=True)

    await _roast_command()(cog, interaction, user=target)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert args[0] == _EULOGY_BOT_LINE
    assert kwargs["ephemeral"] is True


async def test_roast_reports_missing_api_key(monkeypatch, session, make_guild, make_interaction, make_member, cog):
    await make_guild(guild_id=1)
    caller = make_member(manage_guild=True)
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=42, bot=False)
    monkeypatch.setattr(
        "reaper.bot.cogs.spam_defense.get_settings", lambda: _fake_settings(anthropic_api_key="")
    )

    await _roast_command()(cog, interaction, user=target)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "ANTHROPIC_API_KEY" in args[0]
    assert kwargs["ephemeral"] is True


async def test_roast_reports_no_messages_found(monkeypatch, session, make_guild, make_interaction, make_member, cog):
    await make_guild(guild_id=1)
    caller = make_member(manage_guild=True)
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=42, bot=False)
    monkeypatch.setattr("reaper.bot.cogs.spam_defense.get_settings", _fake_settings)
    monkeypatch.setattr(
        "reaper.bot.cogs.spam_defense.fetch_recent_messages", AsyncMock(return_value=[])
    )

    await _roast_command()(cog, interaction, user=target)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    assert "hasn't said enough" in args[0]
    assert kwargs["ephemeral"] is True


async def test_roast_posts_ephemeral_preview_with_generated_roast(
    monkeypatch, session, make_guild, make_interaction, make_member, cog
):
    await make_guild(guild_id=1)
    caller = make_member(manage_guild=True)
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=42, bot=False, display_name="Target")
    monkeypatch.setattr("reaper.bot.cogs.spam_defense.get_settings", _fake_settings)
    monkeypatch.setattr(
        "reaper.bot.cogs.spam_defense.fetch_recent_messages",
        AsyncMock(return_value=["I love pineapple pizza"]),
    )
    monkeypatch.setattr(
        "reaper.bot.cogs.spam_defense.generate_roast",
        AsyncMock(return_value="Even the Reaper won't touch your pizza order."),
    )

    await _roast_command()(cog, interaction, user=target)

    interaction.followup.send.assert_awaited_once()
    _, kwargs = interaction.followup.send.call_args
    embed = kwargs["embed"]
    assert "Even the Reaper won't touch your pizza order." in embed.description
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], RoastReviewView)


async def test_roast_handles_generation_failure(
    monkeypatch, session, make_guild, make_interaction, make_member, cog
):
    await make_guild(guild_id=1)
    caller = make_member(manage_guild=True)
    interaction = make_interaction(user=caller, guild_id=1)
    target = make_member(user_id=42, bot=False)
    monkeypatch.setattr("reaper.bot.cogs.spam_defense.get_settings", _fake_settings)
    monkeypatch.setattr(
        "reaper.bot.cogs.spam_defense.fetch_recent_messages", AsyncMock(return_value=["hi"])
    )
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    monkeypatch.setattr(
        "reaper.bot.cogs.spam_defense.generate_roast",
        AsyncMock(side_effect=APIConnectionError(request=request)),
    )

    await _roast_command()(cog, interaction, user=target)

    interaction.followup.send.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    assert "failed" in args[0]
    assert kwargs["ephemeral"] is True


async def test_roast_review_view_post_button_sends_and_edits(make_member):
    embed = MagicMock()
    view = RoastReviewView(embed=embed, target_mention="<@42>")
    interaction = MagicMock()
    interaction.channel.send = AsyncMock()
    interaction.response.edit_message = AsyncMock()

    await view.post.callback(interaction)

    interaction.channel.send.assert_awaited_once_with(content="<@42>", embed=embed)
    interaction.response.edit_message.assert_awaited_once_with(content="Posted.", embed=None, view=None)
