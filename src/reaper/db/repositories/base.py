"""Base repository enforcing guild_id scoping on every query (spec 3.2).

No handler should be able to query cross-guild -- every repository method that
touches a tenant-scoped table takes guild_id as its required first argument.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class GuildScopedRepository:
    def __init__(self, session: AsyncSession, guild_id: int) -> None:
        self.session = session
        self.guild_id = guild_id
