"""Cross-channel burst tracking (spec 5.1).

Same user_id + content_hash landing in N distinct channels within the window
is the exact July 4th attack signature that native AutoMod's single-message
mention limit can't catch.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, select

from reaper.db.models import BurstWindow
from reaper.db.repositories.base import GuildScopedRepository


class BurstWindowRepository(GuildScopedRepository):
    async def record_hit(self, *, user_id: int, content_hash: str, channel_id: int, window_seconds: int) -> BurstWindow:
        now = dt.datetime.now(dt.timezone.utc)
        stmt = select(BurstWindow).where(
            BurstWindow.guild_id == self.guild_id,
            BurstWindow.user_id == user_id,
            BurstWindow.content_hash == content_hash,
            BurstWindow.window_expires_at > now,
        )
        window = await self.session.scalar(stmt)

        if window is None:
            window = BurstWindow(
                guild_id=self.guild_id,
                user_id=user_id,
                content_hash=content_hash,
                channel_ids_hit=[channel_id],
                window_expires_at=now + dt.timedelta(seconds=window_seconds),
            )
            self.session.add(window)
        elif channel_id not in window.channel_ids_hit:
            # reassign (not .append) so SQLAlchemy's change tracking picks up the JSON mutation
            window.channel_ids_hit = [*window.channel_ids_hit, channel_id]

        await self.session.flush()
        return window

    @staticmethod
    def distinct_channel_count(window: BurstWindow) -> int:
        return len(set(window.channel_ids_hit))


async def delete_expired(session, *, now: dt.datetime | None = None) -> int:
    """Cross-guild cleanup job, run on a schedule (spec 8) rather than per-guild."""
    now = now or dt.datetime.now(dt.timezone.utc)
    result = await session.execute(delete(BurstWindow).where(BurstWindow.window_expires_at <= now))
    await session.flush()
    return result.rowcount or 0
