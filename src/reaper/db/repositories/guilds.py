"""Guild is the tenant registry itself, so this repository is not guild-scoped
the way the others are -- it's what the other repositories scope against.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reaper.db.models import Guild


class GuildRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, guild_id: int) -> Guild | None:
        return await self.session.get(Guild, guild_id)

    async def upsert(
        self,
        guild_id: int,
        name: str,
        icon_hash: str | None = None,
        *,
        enabled_modules: list[str] | None = None,
    ) -> Guild:
        guild = await self.get(guild_id)
        if guild is None:
            guild = Guild(guild_id=guild_id, name=name, icon_hash=icon_hash, enabled_modules=enabled_modules or [])
            self.session.add(guild)
        else:
            guild.name = name
            guild.icon_hash = icon_hash
        await self.session.flush()
        return guild

    async def list_all(self) -> list[Guild]:
        result = await self.session.scalars(select(Guild))
        return list(result)
