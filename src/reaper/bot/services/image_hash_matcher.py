"""Perceptual-hash based scam image detection (spec 5.2)."""

from __future__ import annotations

import io

import discord
import imagehash
from PIL import Image


async def hash_attachment(attachment: discord.Attachment) -> str | None:
    """Perceptual-hash an image attachment. Returns None for non-image attachments."""
    if attachment.content_type is None or not attachment.content_type.startswith("image/"):
        return None
    raw = await attachment.read()
    image = Image.open(io.BytesIO(raw))
    return str(imagehash.phash(image))


def is_image_only_role_mention(message: discord.Message) -> bool:
    """Spec 5.2: role mention + image attachment(s) + no other text is an anomaly
    on its own, even before any repetition or hash match.
    """
    has_role_mention = len(message.role_mentions) > 0
    has_image_attachment = any(
        (a.content_type or "").startswith("image/") for a in message.attachments
    )
    has_no_other_text = message.content.strip() == "" or message.content.strip() == " ".join(
        f"<@&{r.id}>" for r in message.role_mentions
    )
    return has_role_mention and has_image_attachment and has_no_other_text
