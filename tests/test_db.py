"""Tests for bot/db.py — async SQLite state persistence.

Uses a temporary in-memory database to verify CRUD operations
on the auctions table without touching disk.
"""

import pytest

from bot.db import add_auction, close_db, get_ending_soon, init_db, mark_notified, remove_auction


@pytest.fixture
async def db():
    """Initialize an in-memory SQLite database for each test, close after."""
    await init_db(":memory:")
    yield
    await close_db()


async def test_add_and_get_ending_soon(db):
    """Adding an auction and querying it within the horizon returns it."""
    now = 1000000
    await add_auction(auction_id=1, end_time=now + 3600)
    results = await get_ending_soon(horizon_seconds=7200, now_timestamp=now)
    assert len(results) == 1
    assert results[0] == (1, now + 3600)


async def test_get_ending_soon_excludes_far_future(db):
    """Auctions ending beyond the horizon are not returned."""
    now = 1000000
    await add_auction(auction_id=2, end_time=now + 10800)
    results = await get_ending_soon(horizon_seconds=7200, now_timestamp=now)
    assert len(results) == 0


async def test_get_ending_soon_excludes_already_ended(db):
    """Auctions that have already ended (end_time <= now) are not returned."""
    now = 1000000
    await add_auction(auction_id=3, end_time=now - 100)
    results = await get_ending_soon(horizon_seconds=7200, now_timestamp=now)
    assert len(results) == 0


async def test_mark_notified_excludes_from_ending_soon(db):
    """Once marked as notified, an auction no longer appears in get_ending_soon."""
    now = 1000000
    await add_auction(auction_id=4, end_time=now + 3600)
    await mark_notified(auction_id=4)
    results = await get_ending_soon(horizon_seconds=7200, now_timestamp=now)
    assert len(results) == 0


async def test_remove_auction(db):
    """Removing an auction deletes it from the table entirely."""
    now = 1000000
    await add_auction(auction_id=5, end_time=now + 3600)
    await remove_auction(auction_id=5)
    results = await get_ending_soon(horizon_seconds=7200, now_timestamp=now)
    assert len(results) == 0


async def test_add_duplicate_auction_updates(db):
    """Adding an auction with an existing ID updates the end_time via upsert."""
    now = 1000000
    await add_auction(auction_id=6, end_time=now + 3600)
    await add_auction(auction_id=6, end_time=now + 7200)
    results = await get_ending_soon(horizon_seconds=7200, now_timestamp=now)
    assert len(results) == 1
    assert results[0] == (6, now + 7200)
