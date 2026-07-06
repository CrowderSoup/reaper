"""Discord OAuth2 flow + session-based admin-UI auth (spec 3.3).

No separate password/account system -- Discord identity is the only login.
Session cookie stores user_id + the list of guild_ids the user is authorized
for; every admin route checks the requested guild_id against that list. The
cookie itself expires after SESSION_MAX_AGE_SECONDS, which is how a demoted
mod eventually loses access without a live Discord API round-trip on every
page load.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from reaper.config import get_settings
from reaper.db.repositories.guilds import GuildRepository
from reaper.db.session import get_session

DISCORD_API_BASE = "https://discord.com/api/v10"
OAUTH_SCOPES = "identify guilds guilds.members.read"
MANAGE_GUILD_BIT = 0x20

router = APIRouter()


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    settings = get_settings()
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "prompt": "consent",
    }
    return RedirectResponse(f"{DISCORD_API_BASE}/oauth2/authorize?{urlencode(params)}")


@router.get("/auth/callback")
async def callback(request: Request, code: str, state: str) -> RedirectResponse:
    if state != request.session.pop("oauth_state", None):
        raise HTTPException(400, "Invalid OAuth state")

    settings = get_settings()
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"{DISCORD_API_BASE}/oauth2/token",
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oauth_redirect_uri,
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        auth_header = {"Authorization": f"Bearer {access_token}"}
        user_resp = await client.get(f"{DISCORD_API_BASE}/users/@me", headers=auth_header)
        user_resp.raise_for_status()
        user = user_resp.json()

        guilds_resp = await client.get(f"{DISCORD_API_BASE}/users/@me/guilds", headers=auth_header)
        guilds_resp.raise_for_status()
        user_guilds = guilds_resp.json()

        async with get_session() as db_session:
            installed = {g.guild_id: g for g in await GuildRepository(db_session).list_all()}

        authorized_guild_ids: list[int] = []
        for g in user_guilds:
            guild_id = int(g["id"])
            installed_guild = installed.get(guild_id)
            if installed_guild is None:
                continue

            has_manage_guild = (int(g.get("permissions", 0)) & MANAGE_GUILD_BIT) != 0
            if has_manage_guild:
                authorized_guild_ids.append(guild_id)
                continue

            member_resp = await client.get(
                f"{DISCORD_API_BASE}/users/@me/guilds/{guild_id}/member", headers=auth_header
            )
            if member_resp.status_code != 200:
                continue
            member_role_ids = {int(r) for r in member_resp.json().get("roles", [])}
            if member_role_ids & set(installed_guild.mod_role_ids):
                authorized_guild_ids.append(guild_id)

    if not authorized_guild_ids and int(user["id"]) not in [int(i) for i in settings.bot_superadmin_ids]:
        raise HTTPException(403, "You aren't a mod on any guild Reaper is installed in.")

    request.session["user_id"] = user["id"]
    request.session["username"] = user["username"]
    request.session["authorized_guild_ids"] = authorized_guild_ids
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/")


def current_user(request: Request) -> dict:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(401, "Not logged in")
    return {
        "user_id": user_id,
        "username": request.session.get("username"),
        "authorized_guild_ids": request.session.get("authorized_guild_ids", []),
    }


def require_guild_access(guild_id: int):
    def _dependency(user: dict = Depends(current_user)) -> dict:
        settings = get_settings()
        is_superadmin = str(user["user_id"]) in settings.bot_superadmin_ids
        if not is_superadmin and guild_id not in user["authorized_guild_ids"]:
            raise HTTPException(403, "Not authorized for this guild")
        return user

    return _dependency
