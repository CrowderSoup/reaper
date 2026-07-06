from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from reaper.db.repositories.mod_actions import ModActionRepository
from reaper.db.session import get_session
from reaper.web.auth import current_user, require_guild_access
from reaper.web.templating import templates

router = APIRouter(prefix="/guilds/{guild_id}/incidents")


@router.get("", response_class=HTMLResponse)
async def list_incidents(request: Request, guild_id: int, user: dict = Depends(current_user)) -> HTMLResponse:
    require_guild_access(guild_id)(user)
    async with get_session() as session:
        actions = await ModActionRepository(session, guild_id).list_recent(limit=100)
    return templates.TemplateResponse(request, "incidents.html", {"user": user, "guild_id": guild_id, "actions": actions})


@router.post("/{action_id}/review", response_class=HTMLResponse)
async def mark_reviewed(
    request: Request, guild_id: int, action_id: int, user: dict = Depends(current_user)
) -> HTMLResponse:
    require_guild_access(guild_id)(user)
    async with get_session() as session:
        action = await ModActionRepository(session, guild_id).mark_reviewed(action_id, int(user["user_id"]))
        await session.commit()
    return templates.TemplateResponse(
        request, "partials/incident_row.html", {"action": action}
    )
