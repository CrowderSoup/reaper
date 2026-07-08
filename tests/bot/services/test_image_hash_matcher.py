from __future__ import annotations

import io

import pytest
from PIL import Image

from reaper.bot.services.image_hash_matcher import hash_attachment, is_image_only_role_mention


def _png_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=color).save(buf, format="PNG")
    return buf.getvalue()


async def test_hash_attachment_returns_none_for_non_image(make_attachment):
    attachment = make_attachment(content_type="text/plain", content=b"hello")

    assert await hash_attachment(attachment) is None


async def test_hash_attachment_returns_hex_hash_for_image(make_attachment):
    attachment = make_attachment(content_type="image/png", content=_png_bytes())

    result = await hash_attachment(attachment)

    assert isinstance(result, str)
    assert len(result) == 16


async def test_hash_attachment_is_deterministic_for_same_image(make_attachment):
    a = make_attachment(content=_png_bytes((10, 20, 30)))
    b = make_attachment(content=_png_bytes((10, 20, 30)))

    assert await hash_attachment(a) == await hash_attachment(b)


@pytest.mark.parametrize(
    "role_mention_ids,content,attachments,expected",
    [
        ([1], "", ["image"], True),
        ([1], "<@&1>", ["image"], True),
        ([1], "extra text", ["image"], False),
        ([], "", ["image"], False),
        ([1], "", [], False),
        ([1], "", ["not-image"], False),
    ],
)
def test_is_image_only_role_mention(make_message, make_attachment, role_mention_ids, content, attachments, expected):
    attachment_mocks = [
        make_attachment(content_type="image/png") if a == "image" else make_attachment(content_type="text/plain")
        for a in attachments
    ]
    message = make_message(
        content=content,
        role_mention_ids=role_mention_ids,
        attachments=attachment_mocks,
    )

    assert is_image_only_role_mention(message) is expected
