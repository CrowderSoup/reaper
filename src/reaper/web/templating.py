from pathlib import Path
from urllib.parse import urlencode

from fastapi.templating import Jinja2Templates

from reaper.config import get_settings

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Permissions Reaper's bot needs in a guild: View Channel, Send Messages,
# Manage Messages (delete spam), Embed Links, Read Message History, Ban
# Members, Moderate Members (timeout) -- matches the actions taken in
# reaper.bot.cogs.spam_defense.
BOT_INVITE_PERMISSIONS = 1_099_511_720_964


def bot_invite_url() -> str:
    settings = get_settings()
    params = {
        "client_id": settings.discord_client_id,
        "permissions": str(BOT_INVITE_PERMISSIONS),
        "scope": "bot applications.commands",
    }
    return f"https://discord.com/oauth2/authorize?{urlencode(params)}"


templates.env.globals["bot_invite_url"] = bot_invite_url
