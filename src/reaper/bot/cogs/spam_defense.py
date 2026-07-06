"""Module 1: spam / security defense (spec section 5).

Implements the July 4th incident report's proposed fix: cross-channel burst
detection plus image-hash-based scam detection, with mod-alert-channel
notifications and an audit trail of every automated action.
"""

from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from reaper.bot.services.burst_detector import BurstResult, evaluate_burst
from reaper.bot.services.image_hash_matcher import hash_attachment, is_image_only_role_mention
from reaper.config import get_settings
from reaper.db.models import Guild, ScamImageHash
from reaper.db.repositories.guilds import GuildRepository
from reaper.db.repositories.mod_actions import ModActionRepository
from reaper.db.repositories.scam_image_hashes import ScamImageHashRepository
from reaper.db.session import get_session


def _snapshot(message: discord.Message) -> dict:
    return {
        "content": message.content,
        "attachment_urls": [a.url for a in message.attachments],
        "channel_id": message.channel.id,
        "channel_name": getattr(message.channel, "name", None),
    }


def _is_mod(member: discord.Member, guild: Guild) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    member_role_ids = {r.id for r in member.roles}
    return bool(member_role_ids & set(guild.mod_role_ids))


class AnomalyReviewView(discord.ui.View):
    """Buttons attached to an anomaly-only alert (spec 5.3): one click for a mod
    to ban, dismiss, or add the image to the shared hash list for future auto-catches.
    """

    def __init__(self, *, guild_id: int, target_user_id: int, mod_action_id: int, image_phash: str | None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_user_id = target_user_id
        self.mod_action_id = mod_action_id
        self.image_phash = image_phash

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger)
    async def ban(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        assert guild is not None
        member = guild.get_member(self.target_user_id)
        if member is not None:
            await guild.ban(member, reason=f"Reaper: mod-confirmed scam (ModAction #{self.mod_action_id})")
        async with get_session() as session:
            await ModActionRepository(session, self.guild_id).mark_reviewed(self.mod_action_id, interaction.user.id)
            await session.commit()
        await interaction.response.edit_message(content=f"Banned by {interaction.user.mention}.", view=None)

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with get_session() as session:
            await ModActionRepository(session, self.guild_id).mark_reviewed(self.mod_action_id, interaction.user.id)
            await session.commit()
        await interaction.response.edit_message(content=f"Dismissed by {interaction.user.mention}.", view=None)

    @discord.ui.button(label="Add image to hash list", style=discord.ButtonStyle.primary)
    async def add_to_hashlist(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.image_phash is None:
            await interaction.response.send_message("No image attached to this alert.", ephemeral=True)
            return
        async with get_session() as session:
            await ScamImageHashRepository(session).add(
                phash=self.image_phash,
                added_by=interaction.user.id,
                source_incident_id=self.mod_action_id,
            )
            await ModActionRepository(session, self.guild_id).mark_reviewed(self.mod_action_id, interaction.user.id)
            await session.commit()
        await interaction.response.edit_message(
            content=f"Added to hash list by {interaction.user.mention}. Future matches will auto-action.",
            view=None,
        )


class SpamDefenseCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    reaper = app_commands.Group(name="reaper", description="Reaper moderation commands")
    hashlist = app_commands.Group(name="hashlist", description="Manage the scam image hash list", parent=reaper)

    # -- message pipeline ---------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        settings = get_settings()
        async with get_session() as session:
            guild = await GuildRepository(session).get(message.guild.id)
            if guild is None or "spam_defense" not in guild.enabled_modules:
                return

            window_seconds = guild.burst_window_seconds or settings.default_burst_window_seconds
            channel_threshold = guild.burst_channel_threshold or settings.default_burst_channel_threshold
            timeout_seconds = guild.timeout_seconds or settings.default_timeout_seconds

            matched_hash, matched_phash = await self._check_image_hash_match(session, message, settings.scam_image_hamming_threshold)
            burst_result = await evaluate_burst(
                session,
                guild_id=message.guild.id,
                message=message,
                window_seconds=window_seconds,
                channel_threshold=channel_threshold,
            )

            if matched_hash is not None or (burst_result is not None and burst_result.triggered):
                await self._handle_high_confidence(session, guild, message, matched_hash, burst_result, timeout_seconds)
            elif is_image_only_role_mention(message):
                await self._handle_anomaly_only(session, guild, message, matched_phash)

            await session.commit()

    async def _check_image_hash_match(
        self, session, message: discord.Message, max_distance: int
    ) -> tuple[ScamImageHash | None, str | None]:
        repo = ScamImageHashRepository(session)
        for attachment in message.attachments:
            phash = await hash_attachment(attachment)
            if phash is None:
                continue
            match = await repo.find_match(phash, message.guild.id, max_distance)  # type: ignore[union-attr]
            if match is not None:
                return match, phash
        # no match found; still return the first computed phash (if any) so an
        # anomaly-only alert can offer "add to hash list"
        for attachment in message.attachments:
            phash = await hash_attachment(attachment)
            if phash is not None:
                return None, phash
        return None, None

    async def _handle_high_confidence(
        self,
        session,
        guild: Guild,
        message: discord.Message,
        matched_hash: ScamImageHash | None,
        burst_result: BurstResult | None,
        timeout_seconds: int,
    ) -> None:
        pattern = "image_hash_match" if matched_hash is not None else "cross_channel_burst"
        snapshot = _snapshot(message)

        action = await ModActionRepository(session, guild.guild_id).create(
            action_type="timeout",
            target_user_id=message.author.id,
            trigger_reason=f"Auto-actioned: {pattern}",
            matched_pattern=pattern,
            channel_ids=burst_result.channel_ids_hit if burst_result else [message.channel.id],
            message_snapshot=snapshot,
        )

        member = message.guild.get_member(message.author.id) if message.guild else None  # type: ignore[union-attr]
        if isinstance(member, discord.Member):
            until = discord.utils.utcnow() + dt.timedelta(seconds=timeout_seconds)
            await member.timeout(until, reason=f"Reaper auto-action: {pattern} (ModAction #{action.id})")

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        await self._post_alert(
            guild,
            title="High-confidence scam auto-actioned",
            description=f"{message.author.mention} timed out for **{pattern}**.",
            snapshot=snapshot,
        )

    async def _handle_anomaly_only(self, session, guild: Guild, message: discord.Message, phash: str | None) -> None:
        snapshot = _snapshot(message)
        action = await ModActionRepository(session, guild.guild_id).create(
            action_type="alert_only",
            target_user_id=message.author.id,
            trigger_reason="Anomaly: role mention + image + no other text",
            matched_pattern="manual",
            channel_ids=[message.channel.id],
            message_snapshot=snapshot,
        )
        await self._post_alert(
            guild,
            title="Anomaly flagged for review",
            description=f"{message.author.mention} posted an image + role mention with no other text.",
            snapshot=snapshot,
            view=AnomalyReviewView(
                guild_id=guild.guild_id,
                target_user_id=message.author.id,
                mod_action_id=action.id,
                image_phash=phash,
            ),
        )

    async def _post_alert(
        self,
        guild: Guild,
        *,
        title: str,
        description: str,
        snapshot: dict,
        view: discord.ui.View | None = None,
    ) -> None:
        if guild.mod_alert_channel_id is None:
            return
        channel = self.bot.get_channel(guild.mod_alert_channel_id)
        if channel is None:
            return
        embed = discord.Embed(title=title, description=description, color=discord.Color.red())
        if snapshot.get("content"):
            embed.add_field(name="Content", value=snapshot["content"][:1024], inline=False)
        if snapshot.get("attachment_urls"):
            embed.add_field(name="Attachments", value="\n".join(snapshot["attachment_urls"]), inline=False)
        await channel.send(embed=embed, view=view)  # type: ignore[union-attr]

    # -- slash commands (spec 5.4) -------------------------------------------

    @reaper.command(name="status", description="Show current burst/hash-list stats for this guild")
    async def status(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            guild = await GuildRepository(session).get(interaction.guild_id)  # type: ignore[arg-type]
            if guild is None:
                await interaction.response.send_message("Reaper isn't configured for this guild yet.", ephemeral=True)
                return
            hashes = await ScamImageHashRepository(session).list_for_guild(interaction.guild_id)  # type: ignore[arg-type]
            recent = await ModActionRepository(session, interaction.guild_id).list_recent(limit=5)  # type: ignore[arg-type]

        settings = get_settings()
        embed = discord.Embed(title="Reaper status", color=discord.Color.blurple())
        embed.add_field(name="Modules enabled", value=", ".join(guild.enabled_modules) or "none")
        embed.add_field(name="Hash list size (guild + global)", value=str(len(hashes)))
        embed.add_field(
            name="Burst window / threshold",
            value=f"{guild.burst_window_seconds or settings.default_burst_window_seconds}s / "
            f"{guild.burst_channel_threshold or settings.default_burst_channel_threshold} channels",
        )
        embed.add_field(name="Recent actions", value=str(len(recent)))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @hashlist.command(name="add", description="Manually add a known scam image to the hash list")
    @app_commands.describe(image="The scam image to hash and add")
    async def hashlist_add(self, interaction: discord.Interaction, image: discord.Attachment) -> None:
        phash = await hash_attachment(image)
        if phash is None:
            await interaction.response.send_message("That attachment isn't an image.", ephemeral=True)
            return
        async with get_session() as session:
            await ScamImageHashRepository(session).add(
                phash=phash, added_by=interaction.user.id, guild_id=interaction.guild_id
            )
            await session.commit()
        await interaction.response.send_message(f"Added hash `{phash}` to the list.", ephemeral=True)

    @hashlist.command(name="list", description="List current hashes for this guild + shared global list")
    async def hashlist_list(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            hashes = await ScamImageHashRepository(session).list_for_guild(interaction.guild_id)  # type: ignore[arg-type]
        lines = [f"`{h.phash}` {'(global)' if h.guild_id is None else '(this guild)'}" for h in hashes[:25]]
        await interaction.response.send_message(
            "\n".join(lines) if lines else "No hashes yet.", ephemeral=True
        )

    @reaper.command(name="config", description="Set mod-alert channel, timeout duration, burst threshold/window")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        mod_alert_channel="Channel Reaper posts alerts to",
        timeout_seconds="Auto-timeout duration in seconds",
        burst_window_seconds="Burst detection window in seconds",
        burst_channel_threshold="Distinct channels required to trigger a burst action",
    )
    async def config(
        self,
        interaction: discord.Interaction,
        mod_alert_channel: discord.TextChannel | None = None,
        timeout_seconds: int | None = None,
        burst_window_seconds: int | None = None,
        burst_channel_threshold: int | None = None,
    ) -> None:
        async with get_session() as session:
            repo = GuildRepository(session)
            guild = await repo.get(interaction.guild_id)  # type: ignore[arg-type]
            if guild is None:
                guild = await repo.upsert(interaction.guild_id, name=interaction.guild.name)  # type: ignore[union-attr]
            if mod_alert_channel is not None:
                guild.mod_alert_channel_id = mod_alert_channel.id
            if timeout_seconds is not None:
                guild.timeout_seconds = timeout_seconds
            if burst_window_seconds is not None:
                guild.burst_window_seconds = burst_window_seconds
            if burst_channel_threshold is not None:
                guild.burst_channel_threshold = burst_channel_threshold
            await session.commit()
        await interaction.response.send_message("Config updated.", ephemeral=True)

    @reaper.command(name="incidents", description="Pull last N ModAction rows")
    @app_commands.describe(count="How many recent incidents to show (default 10)")
    async def incidents(self, interaction: discord.Interaction, count: int = 10) -> None:
        async with get_session() as session:
            rows = await ModActionRepository(session, interaction.guild_id).list_recent(limit=count)  # type: ignore[arg-type]
        if not rows:
            await interaction.response.send_message("No incidents recorded.", ephemeral=True)
            return
        lines = [
            f"#{r.id} `{r.created_at:%Y-%m-%d %H:%M}` {r.action_type} / {r.matched_pattern} "
            f"-> <@{r.target_user_id}>{' (reviewed)' if r.reviewed_by else ''}"
            for r in rows
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpamDefenseCog(bot))
