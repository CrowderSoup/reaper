from __future__ import annotations

from reaper.db.repositories.scam_image_hashes import ScamImageHashRepository

# 64-bit hex hashes (16 hex chars, matching imagehash.phash's default hash_size=8).
# "...00" vs "...01" differ by 1 bit; vs "...ff" differ by 8 bits.
BASE_HASH = "0000000000000000"
CLOSE_HASH = "0000000000000001"
FAR_HASH = "00000000000000ff"


async def test_add_persists_hash(session):
    repo = ScamImageHashRepository(session)

    row = await repo.add(phash=BASE_HASH, added_by=1)

    assert row.id is not None
    assert row.guild_id is None


async def test_list_for_guild_includes_guild_specific_and_global(session, make_guild):
    await make_guild(guild_id=1)
    await make_guild(guild_id=2)
    repo = ScamImageHashRepository(session)

    await repo.add(phash=BASE_HASH, added_by=1, guild_id=1)
    await repo.add(phash=CLOSE_HASH, added_by=1, guild_id=None)
    await repo.add(phash=FAR_HASH, added_by=1, guild_id=2)

    hashes = await repo.list_for_guild(1)

    assert {h.phash for h in hashes} == {BASE_HASH, CLOSE_HASH}


async def test_find_match_within_hamming_threshold(session):
    repo = ScamImageHashRepository(session)
    await repo.add(phash=BASE_HASH, added_by=1)

    match = await repo.find_match(CLOSE_HASH, guild_id=1, max_distance=5)

    assert match is not None
    assert match.phash == BASE_HASH


async def test_find_match_returns_none_outside_hamming_threshold(session):
    repo = ScamImageHashRepository(session)
    await repo.add(phash=BASE_HASH, added_by=1)

    match = await repo.find_match(FAR_HASH, guild_id=1, max_distance=5)

    assert match is None
