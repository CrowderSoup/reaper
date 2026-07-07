from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from reaper.db.repositories.guilds import GuildRepository
from reaper.db.repositories.mod_actions import ModActionRepository
from reaper.db.session import get_session
from reaper.web.auth import current_user, require_guild_access
from reaper.web.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    if request.session.get("user_id") is None:
        return templates.TemplateResponse(request, "landing.html", {"user": None})

    user = current_user(request)
    async with get_session() as session:
        all_guilds = await GuildRepository(session).list_all()

    authorized_ids = set(user["authorized_guild_ids"])
    guilds = [g for g in all_guilds if g.guild_id in authorized_ids]

    return templates.TemplateResponse(
        request, "dashboard.html", {"user": user, "guilds": guilds}
    )


@router.get("/guilds/{guild_id}", response_class=HTMLResponse)
async def guild_dashboard(request: Request, guild_id: int, user: dict = Depends(current_user)) -> HTMLResponse:
    require_guild_access(guild_id)(user)

    async with get_session() as session:
        guild = await GuildRepository(session).get(guild_id)
        recent_actions = await ModActionRepository(session, guild_id).list_recent(limit=10)

    return templates.TemplateResponse(
        request,
        "guild_dashboard.html",
        {"user": user, "guild": guild, "recent_actions": recent_actions},
    )
