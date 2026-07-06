"""Entry point for the bot process: `python -m reaper.bot.main`."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from reaper.config import get_settings
from reaper.db.repositories.guilds import GuildRepository
from reaper.db.session import get_session

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("reaper.bot")

INITIAL_EXTENSIONS = ["reaper.bot.cogs.spam_defense"]


class Reaper(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self) -> None:
        for extension in INITIAL_EXTENSIONS:
            await self.load_extension(extension)
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        async with get_session() as session:
            await GuildRepository(session).upsert(
                guild.id, name=guild.name, icon_hash=guild.icon.key if guild.icon else None
            )
            await session.commit()
        log.info("Joined guild %s (%s)", guild.name, guild.id)


def main() -> None:
    settings = get_settings()
    bot = Reaper()
    bot.run(settings.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
