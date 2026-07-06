"""ScamImageHash is global-by-default (nullable guild_id, spec section 9), so this
repository takes guild_id per-call rather than binding to it like GuildScopedRepository.
"""

from __future__ import annotations

import imagehash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reaper.db.models import ScamImageHash


class ScamImageHashRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        phash: str,
        added_by: int,
        guild_id: int | None = None,
        source_incident_id: int | None = None,
    ) -> ScamImageHash:
        row = ScamImageHash(
            guild_id=guild_id,
            phash=phash,
            added_by=added_by,
            source_incident_id=source_incident_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_guild(self, guild_id: int) -> list[ScamImageHash]:
        """Hashes scoped to this guild plus the shared global list (guild_id is null)."""
        stmt = select(ScamImageHash).where(
            (ScamImageHash.guild_id == guild_id) | (ScamImageHash.guild_id.is_(None))
        )
        result = await self.session.scalars(stmt)
        return list(result)

    async def find_match(self, phash: str, guild_id: int, max_distance: int) -> ScamImageHash | None:
        candidate = imagehash.hex_to_hash(phash)
        for row in await self.list_for_guild(guild_id):
            if candidate - imagehash.hex_to_hash(row.phash) <= max_distance:
                return row
        return None
