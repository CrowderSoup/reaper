from __future__ import annotations

from sqlalchemy import select

from reaper.db.models import ModAction
from reaper.db.repositories.base import GuildScopedRepository


class ModActionRepository(GuildScopedRepository):
    async def create(
        self,
        *,
        action_type: str,
        target_user_id: int,
        trigger_reason: str,
        matched_pattern: str,
        channel_ids: list[int],
        message_snapshot: dict,
    ) -> ModAction:
        action = ModAction(
            guild_id=self.guild_id,
            action_type=action_type,
            target_user_id=target_user_id,
            trigger_reason=trigger_reason,
            matched_pattern=matched_pattern,
            channel_ids=channel_ids,
            message_snapshot=message_snapshot,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def list_recent(self, limit: int = 20) -> list[ModAction]:
        stmt = (
            select(ModAction)
            .where(ModAction.guild_id == self.guild_id)
            .order_by(ModAction.created_at.desc())
            .limit(limit)
        )
        result = await self.session.scalars(stmt)
        return list(result)

    async def mark_reviewed(self, action_id: int, reviewer_user_id: int) -> ModAction | None:
        action = await self.session.get(ModAction, action_id)
        if action is None or action.guild_id != self.guild_id:
            return None
        action.reviewed_by = reviewer_user_id
        await self.session.flush()
        return action
