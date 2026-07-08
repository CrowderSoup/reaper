"""Module 1: spam / security defense (spec section 5).

Implements the July 4th incident report's proposed fix: cross-channel burst
detection plus image-hash-based scam detection, with mod-alert-channel
notifications and an audit trail of every automated action.
"""

from __future__ import annotations

import datetime as dt
import random
from collections import Counter

import anthropic
import discord
from discord import app_commands
from discord.ext import commands

from reaper.bot.services.burst_detector import BurstResult, evaluate_burst
from reaper.bot.services.image_hash_matcher import hash_attachment, is_image_only_role_mention
from reaper.bot.services.roast_generator import fetch_recent_messages, generate_roast
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


_EULOGY_BOT_LINE = "Bots have no soul for the Reaper to collect."

_EULOGY_CLEAN_LINES = [
    "Here lies an innocent soul, untouched by the Reaper's scythe... yet.",
    "No sins recorded. The Reaper finds this suspicious.",
    "A clean record. Either truly virtuous, or exceptionally careful.",
]

_EULOGY_PATTERN_LINES = {
    "cross_channel_burst": [
        "flooded one too many channels at once — the scythe does not forgive floods.",
        "set off alarms in every channel before the Reaper could blink.",
    ],
    "image_hash_match": [
        "fell for a cursed JPEG the Reaper had already catalogued.",
        "shared an image marked for judgment, and was judged accordingly.",
    ],
    "manual": [
        "drew the Reaper's eye with offerings too strange to automate.",
        "was judged for deeds too odd to ignore.",
    ],
}


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


class RoastReviewView(discord.ui.View):
    """Ephemeral preview attached to a generated roast: a mod must click Post
    before the LLM's output ever reaches the channel.
    """

    def __init__(self, *, embed: discord.Embed, target_mention: str) -> None:
        super().__init__(timeout=300)
        self.embed = embed
        self.target_mention = target_mention
        self.message: discord.Message | discord.WebhookMessage | None = None

    @discord.ui.button(label="Post", style=discord.ButtonStyle.danger)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.channel.send(content=self.target_mention, embed=self.embed)  # type: ignore[union-attr]
        await interaction.response.edit_message(content="Posted.", embed=None, view=None)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(content="Roast preview expired.", embed=None, view=None)
        except discord.HTTPException:
            pass


class SpamDefenseCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    reaper = app_commands.Group(name="reaper", description="Reaper moderation commands")
    hashlist = app_commands.Group(name="hashlist", description="Manage the scam image hash list", parent=reaper)
    modrole = app_commands.Group(name="modrole", description="Manage which roles Reaper treats as mods", parent=reaper)

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

    @modrole.command(name="add", description="Let members with this role use Reaper's mod-gated commands")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(role="Role to grant Reaper mod access to")
    async def modrole_add(self, interaction: discord.Interaction, role: discord.Role) -> None:
        async with get_session() as session:
            repo = GuildRepository(session)
            guild = await repo.get(interaction.guild_id)  # type: ignore[arg-type]
            if guild is None:
                guild = await repo.upsert(interaction.guild_id, name=interaction.guild.name)  # type: ignore[union-attr]
            if role.id not in guild.mod_role_ids:
                guild.mod_role_ids = [*guild.mod_role_ids, role.id]
            await session.commit()
        await interaction.response.send_message(f"{role.mention} can now use Reaper's mod commands.", ephemeral=True)

    @modrole.command(name="remove", description="Revoke a role's access to Reaper's mod-gated commands")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(role="Role to revoke Reaper mod access from")
    async def modrole_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        async with get_session() as session:
            repo = GuildRepository(session)
            guild = await repo.get(interaction.guild_id)  # type: ignore[arg-type]
            if guild is not None and role.id in guild.mod_role_ids:
                guild.mod_role_ids = [rid for rid in guild.mod_role_ids if rid != role.id]
                await session.commit()
        await interaction.response.send_message(f"{role.mention} no longer has Reaper mod access.", ephemeral=True)

    @modrole.command(name="list", description="Show which roles Reaper treats as mods")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def modrole_list(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            guild = await GuildRepository(session).get(interaction.guild_id)  # type: ignore[arg-type]
        if not guild or not guild.mod_role_ids:
            await interaction.response.send_message(
                "No mod roles configured (members with Manage Server can still use mod commands).", ephemeral=True
            )
            return
        mentions = ", ".join(f"<@&{rid}>" for rid in guild.mod_role_ids)
        await interaction.response.send_message(f"Mod roles: {mentions}", ephemeral=True)

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

    @reaper.command(name="history", description="Show a member's moderation history")
    @app_commands.describe(user="The member to look up", count="How many entries to show (default 10)")
    async def history(self, interaction: discord.Interaction, user: discord.Member, count: int = 10) -> None:
        async with get_session() as session:
            repo = GuildRepository(session)
            guild = await repo.get(interaction.guild_id)  # type: ignore[arg-type]
            if guild is None:
                guild = await repo.upsert(interaction.guild_id, name=interaction.guild.name)  # type: ignore[union-attr]
                await session.commit()
            if not _is_mod(interaction.user, guild):  # type: ignore[arg-type]
                await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
                return
            rows = await ModActionRepository(session, interaction.guild_id).list_for_user(user.id, limit=count)  # type: ignore[arg-type]

        if not rows:
            await interaction.response.send_message(f"No recorded actions for {user.mention}.", ephemeral=True)
            return

        embed = discord.Embed(title="Moderation history", color=discord.Color.blurple())
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.description = "\n".join(
            f"#{r.id} `{r.created_at:%Y-%m-%d %H:%M}` {r.action_type} / {r.matched_pattern}"
            f"{' (reviewed)' if r.reviewed_by else ''}"
            for r in rows
        )
        embed.set_footer(text=f"Showing {len(rows)} most recent")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @reaper.command(name="eulogy", description="The Reaper delivers a eulogy for a member's crimes")
    @app_commands.describe(user="Who the Reaper shall remember")
    async def eulogy(self, interaction: discord.Interaction, user: discord.Member) -> None:
        embed = discord.Embed(title=f"⚰️ Here lies {user.display_name}", color=discord.Color.from_str("#2b2d31"))
        embed.set_thumbnail(url=user.display_avatar.url)

        if user.bot:
            embed.description = _EULOGY_BOT_LINE
            await interaction.response.send_message(embed=embed)
            return

        async with get_session() as session:
            rows = await ModActionRepository(session, interaction.guild_id).list_for_user(user.id, limit=100)  # type: ignore[arg-type]

        if not rows:
            embed.description = random.choice(_EULOGY_CLEAN_LINES)
            await interaction.response.send_message(embed=embed)
            return

        pattern_counts = Counter(r.matched_pattern for r in rows)
        top_pattern, _ = pattern_counts.most_common(1)[0]
        lines = _EULOGY_PATTERN_LINES.get(top_pattern, _EULOGY_PATTERN_LINES["manual"])
        total = len(rows)
        embed.description = f"{user.mention} {random.choice(lines)}"
        embed.set_footer(text=f"Reaped {total} time{'s' if total != 1 else ''} · Preferred method: {top_pattern}")
        await interaction.response.send_message(embed=embed)

    @reaper.command(name="roast", description="The Reaper roasts a member based on their recent messages")
    @app_commands.describe(user="Who the Reaper shall roast")
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    async def roast(self, interaction: discord.Interaction, user: discord.Member) -> None:
        async with get_session() as session:
            repo = GuildRepository(session)
            guild = await repo.get(interaction.guild_id)  # type: ignore[arg-type]
            if guild is None:
                guild = await repo.upsert(interaction.guild_id, name=interaction.guild.name)  # type: ignore[union-attr]
                await session.commit()
            if not _is_mod(interaction.user, guild):  # type: ignore[arg-type]
                await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
                return

        if user.bot:
            await interaction.response.send_message(_EULOGY_BOT_LINE, ephemeral=True)
            return

        settings = get_settings()
        if not settings.anthropic_api_key:
            await interaction.response.send_message(
                "Roast isn't configured for this bot (missing ANTHROPIC_API_KEY).", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        messages = await fetch_recent_messages(
            interaction.guild,  # type: ignore[arg-type]
            user,
            invoking_channel=interaction.channel,
            max_messages=settings.roast_max_messages,
            channel_scan_limit=settings.roast_channel_scan_limit,
        )
        if not messages:
            await interaction.followup.send(
                f"{user.mention} hasn't said enough for the Reaper to work with.", ephemeral=True
            )
            return

        try:
            roast_line = await generate_roast(
                api_key=settings.anthropic_api_key,
                model=settings.roast_model,
                display_name=user.display_name,
                messages=messages,
            )
        except anthropic.APIError:
            await interaction.followup.send("The Reaper's wit failed them. Try again later.", ephemeral=True)
            return

        if not roast_line:
            await interaction.followup.send("The Reaper's wit failed them. Try again later.", ephemeral=True)
            return

        embed = discord.Embed(title="🔥 Roast", description=f"{user.mention} {roast_line}", color=discord.Color.orange())
        embed.set_thumbnail(url=user.display_avatar.url)
        view = RoastReviewView(embed=embed, target_mention=user.mention)
        view.message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpamDefenseCog(bot))
