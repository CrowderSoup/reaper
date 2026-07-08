"""Shared test fixtures.

The env vars below must be set *before* anything under `reaper` is imported --
`reaper.config.get_settings()` reads them eagerly via `os.environ[...]`, and
`reaper.db.session` calls `get_settings()` at module import time to build its
engine. `create_async_engine` doesn't connect eagerly, so a dummy DATABASE_URL
is fine here; it's never dialed in tests.
"""

from __future__ import annotations

import os

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-bot-token")
os.environ.setdefault("DISCORD_CLIENT_ID", "test-client-id")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret")

from typing import AsyncIterator, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from reaper.db.models import Base, Guild


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s

    await engine.dispose()


@pytest_asyncio.fixture
def make_guild(session: AsyncSession) -> Callable:
    async def _make_guild(guild_id: int = 1, **overrides) -> Guild:
        guild = Guild(
            guild_id=guild_id,
            name=overrides.pop("name", "Test Guild"),
            enabled_modules=overrides.pop("enabled_modules", ["spam_defense"]),
            mod_role_ids=overrides.pop("mod_role_ids", []),
            **overrides,
        )
        session.add(guild)
        await session.flush()
        return guild

    return _make_guild


def _make_role(role_id: int) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    return role


@pytest_asyncio.fixture
def make_member() -> Callable:
    def _make_member(
        *,
        user_id: int = 100,
        bot: bool = False,
        manage_guild: bool = False,
        role_ids: list[int] | None = None,
        display_name: str = "TestMember",
    ) -> MagicMock:
        member = MagicMock()
        member.id = user_id
        member.bot = bot
        member.mention = f"<@{user_id}>"
        member.display_name = display_name
        member.display_avatar.url = "https://cdn.example.com/avatar.png"
        member.guild_permissions.manage_guild = manage_guild
        member.roles = [_make_role(rid) for rid in (role_ids or [])]
        return member

    return _make_member


@pytest_asyncio.fixture
def make_attachment() -> Callable:
    def _make_attachment(*, content_type: str | None = "image/png", content: bytes = b"") -> MagicMock:
        attachment = MagicMock()
        attachment.content_type = content_type
        attachment.url = "https://cdn.example.com/attachment.png"
        attachment.read = AsyncMock(return_value=content)
        return attachment

    return _make_attachment


@pytest_asyncio.fixture
def make_message(make_member: Callable) -> Callable:
    def _make_message(
        *,
        author: MagicMock | None = None,
        guild_id: int = 1,
        channel_id: int = 10,
        content: str = "",
        role_mention_ids: list[int] | None = None,
        attachments: list[MagicMock] | None = None,
    ) -> MagicMock:
        message = MagicMock()
        message.author = author or make_member()
        message.guild.id = guild_id
        message.channel.id = channel_id
        message.content = content
        message.role_mentions = [_make_role(rid) for rid in (role_mention_ids or [])]
        message.attachments = attachments or []
        message.delete = AsyncMock()
        return message

    return _make_message


@pytest_asyncio.fixture
def make_interaction(make_member: Callable) -> Callable:
    def _make_interaction(*, user: MagicMock | None = None, guild_id: int = 1) -> MagicMock:
        interaction = MagicMock()
        interaction.user = user or make_member()
        interaction.guild_id = guild_id
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        return interaction

    return _make_interaction
