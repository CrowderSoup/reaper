from __future__ import annotations

from reaper.bot.services.burst_detector import content_hash_for_message, evaluate_burst


async def test_content_hash_for_message_none_without_role_mention(make_message):
    message = make_message(content="hello", role_mention_ids=[])

    assert await content_hash_for_message(message) is None


async def test_content_hash_for_message_same_content_same_hash(make_message):
    a = make_message(content="scam text", role_mention_ids=[1])
    b = make_message(content="scam text", role_mention_ids=[1])

    assert await content_hash_for_message(a) == await content_hash_for_message(b)


async def test_content_hash_for_message_different_content_different_hash(make_message):
    a = make_message(content="scam text", role_mention_ids=[1])
    b = make_message(content="other text", role_mention_ids=[1])

    assert await content_hash_for_message(a) != await content_hash_for_message(b)


async def test_evaluate_burst_returns_none_for_non_trackable_message(session, make_guild, make_message):
    await make_guild(guild_id=1)
    message = make_message(content="no mention here", role_mention_ids=[])

    result = await evaluate_burst(
        session, guild_id=1, message=message, window_seconds=60, channel_threshold=3
    )

    assert result is None


async def test_evaluate_burst_triggers_once_channel_threshold_crossed(session, make_guild, make_message, make_member):
    await make_guild(guild_id=1)
    author = make_member(user_id=42)

    async def hit(channel_id: int):
        message = make_message(
            author=author,
            content="scam text",
            role_mention_ids=[1],
            channel_id=channel_id,
        )
        return await evaluate_burst(
            session, guild_id=1, message=message, window_seconds=60, channel_threshold=3
        )

    first = await hit(10)
    second = await hit(20)
    third = await hit(30)

    assert first.triggered is False
    assert first.distinct_channel_count == 1
    assert second.triggered is False
    assert second.distinct_channel_count == 2
    assert third.triggered is True
    assert third.distinct_channel_count == 3
    assert set(third.channel_ids_hit) == {10, 20, 30}
