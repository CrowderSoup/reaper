"""LLM-generated roasts based on a member's recent messages (mod-only)."""

from __future__ import annotations

import discord
from anthropic import AsyncAnthropic

_SYSTEM_PROMPT = (
    "You are the Reaper, a Discord moderation bot with a dark-humor personality. "
    "A moderator has asked you to roast a member using snippets of their own recent "
    "messages. Write ONE short, funny roast line (under 300 characters) in the "
    "Reaper's voice. Keep it playful and clever, never cruel: no slurs or insults "
    "about race, gender, sexuality, religion, disability, or other protected traits, "
    "no harassment, no threats, no sexual content. Reference specific things from "
    "their messages when you can, to make it feel personal. Respond with ONLY the "
    "roast line -- no preamble, no quotation marks."
)


async def fetch_recent_messages(
    guild: discord.Guild,
    user: discord.Member,
    *,
    invoking_channel: discord.abc.Messageable | None = None,
    max_messages: int,
    channel_scan_limit: int,
) -> list[str]:
    """Scan channels the bot can read, newest first, for this user's recent text.

    There's no standing log of member messages (spec's ModAction snapshots only
    capture content at auto-action time), so this does a live, capped scan --
    the invoking channel first (which may be a thread), then the guild's other
    text channels -- rather than sweeping the whole guild, which would be slow
    and rate-limit-prone.
    """
    contents: list[str] = []
    channels: list[discord.TextChannel | discord.Thread] = []
    if isinstance(invoking_channel, (discord.TextChannel, discord.Thread)):
        channels.append(invoking_channel)
    channels.extend(c for c in guild.text_channels if c not in channels)

    scanned = 0
    me = guild.me
    for channel in channels:
        if len(contents) >= max_messages or scanned >= channel_scan_limit:
            break
        if me is not None and not channel.permissions_for(me).read_message_history:
            continue
        try:
            async for message in channel.history(limit=channel_scan_limit - scanned):
                scanned += 1
                if message.author.id == user.id and message.content.strip():
                    contents.append(message.content.strip())
                    if len(contents) >= max_messages:
                        break
                if scanned >= channel_scan_limit:
                    break
        except discord.Forbidden:
            continue

    return contents


async def generate_roast(
    *,
    api_key: str,
    model: str,
    display_name: str,
    messages: list[str],
) -> str:
    client = AsyncAnthropic(api_key=api_key)
    snippets = "\n".join(f"- {m[:200]}" for m in messages)
    response = await client.messages.create(
        model=model,
        max_tokens=200,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Member: {display_name}\nRecent messages:\n{snippets}",
            }
        ],
    )
    return next((block.text for block in response.content if block.type == "text"), "").strip()
