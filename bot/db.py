"""Async SQLite state persistence for auction tracking.

Manages a single persistent aiosqlite connection with WAL mode
for crash-safe writes. Stores auction end times and notification
status so the hourly cron can alert on auctions ending soon.

Connection lifecycle:
- init_db() opens the connection (called from @bot.on_worker_startup)
- close_db() closes it (called from @bot.on_worker_shutdown)
"""

import logging
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

# Module-level connection — initialized by init_db(), closed by close_db()
_conn: Optional[aiosqlite.Connection] = None


def _get_conn() -> aiosqlite.Connection:
    """Return the active database connection.

    Raises RuntimeError if init_db() hasn't been called yet,
    which indicates a lifecycle ordering bug.
    """
    if _conn is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _conn


async def init_db(db_path: str = "bot_state.db") -> None:
    """Open SQLite connection, create schema, enable WAL mode.

    Args:
        db_path: Path to the SQLite database file. Use ":memory:" for tests.
    """
    global _conn
    _conn = await aiosqlite.connect(db_path)
    # WAL mode provides crash safety and allows concurrent reads
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auctions (
            auction_id INTEGER PRIMARY KEY,
            end_time INTEGER NOT NULL,
            notified_ending_soon BOOLEAN DEFAULT 0
        )
        """
    )
    await _conn.commit()
    logger.info("Database initialized at %s", db_path)


async def close_db() -> None:
    """Close the SQLite connection. Safe to call even if not initialized."""
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None
        logger.info("Database connection closed")


async def add_auction(auction_id: int, end_time: int) -> None:
    """Insert or update an auction's end time.

    Uses INSERT OR REPLACE so that if an auction is re-created
    (e.g. after an extension event), the end_time is updated
    and notified_ending_soon is reset to 0.
    """
    conn = _get_conn()
    await conn.execute(
        "INSERT OR REPLACE INTO auctions (auction_id, end_time, notified_ending_soon) VALUES (?, ?, 0)",
        (auction_id, end_time),
    )
    await conn.commit()


async def get_ending_soon(horizon_seconds: int, now_timestamp: Optional[int] = None) -> list[tuple[int, int]]:
    """Return auctions ending within the given horizon that haven't been notified.

    Args:
        horizon_seconds: How many seconds into the future to look (e.g. 7200 for 2 hours).
        now_timestamp: Current UTC timestamp. If None, uses time.time(). Exposed for testing.

    Returns:
        List of (auction_id, end_time) tuples for auctions ending within the horizon
        that have not yet been notified.
    """
    if now_timestamp is None:
        import time

        now_timestamp = int(time.time())

    conn = _get_conn()
    cursor = await conn.execute(
        """
        SELECT auction_id, end_time FROM auctions
        WHERE notified_ending_soon = 0
          AND end_time > ?
          AND end_time <= ?
        """,
        (now_timestamp, now_timestamp + horizon_seconds),
    )
    rows = await cursor.fetchall()
    return [(row[0], row[1]) for row in rows]


async def mark_notified(auction_id: int) -> None:
    """Flag an auction as having received its ending-soon notification.

    This is an atomic update — no risk of the save being lost
    (unlike the old JSON approach where save_state() was missing).
    """
    conn = _get_conn()
    await conn.execute(
        "UPDATE auctions SET notified_ending_soon = 1 WHERE auction_id = ?",
        (auction_id,),
    )
    await conn.commit()


async def remove_auction(auction_id: int) -> None:
    """Delete a settled auction from the tracking table.

    Called from the AuctionSettled handler to clean up auctions
    that no longer need ending-soon notifications.
    """
    conn = _get_conn()
    await conn.execute("DELETE FROM auctions WHERE auction_id = ?", (auction_id,))
    await conn.commit()
