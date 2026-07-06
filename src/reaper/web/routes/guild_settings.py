from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from reaper.db.repositories.guilds import GuildRepository
from reaper.db.session import get_session
from reaper.web.auth import current_user, require_guild_access
from reaper.web.templating import templates

router = APIRouter(prefix="/guilds/{guild_id}/settings")


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, guild_id: int, user: dict = Depends(current_user)) -> HTMLResponse:
    require_guild_access(guild_id)(user)
    async with get_session() as session:
        guild = await GuildRepository(session).get(guild_id)
    return templates.TemplateResponse(request, "guild_settings.html", {"user": user, "guild": guild})


@router.post("", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    guild_id: int,
    user: dict = Depends(current_user),
    mod_alert_channel_id: int | None = Form(None),
    timeout_seconds: int | None = Form(None),
    burst_window_seconds: int | None = Form(None),
    burst_channel_threshold: int | None = Form(None),
) -> RedirectResponse:
    require_guild_access(guild_id)(user)
    async with get_session() as session:
        repo = GuildRepository(session)
        guild = await repo.get(guild_id)
        if guild is not None:
            guild.mod_alert_channel_id = mod_alert_channel_id
            guild.timeout_seconds = timeout_seconds
            guild.burst_window_seconds = burst_window_seconds
            guild.burst_channel_threshold = burst_channel_threshold
        await session.commit()
    return RedirectResponse(f"/guilds/{guild_id}/settings", status_code=303)
