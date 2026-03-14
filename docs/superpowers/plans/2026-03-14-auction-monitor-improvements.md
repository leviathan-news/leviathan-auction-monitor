# Auction Monitor Hardening + Enrichment — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the auction monitor bot with SQLite state, async HTTP, structured logging, env-driven config, and enriched Telegram notifications.

**Architecture:** Bottom-up build — foundational modules first (logger, config, db, tg, api), then rewire bot.py event handlers and lifecycle hooks on top. Each module is independently testable.

**Tech Stack:** Python 3.12, Silverback 0.7.27, aiosqlite, aiohttp, python-telegram-bot 22.0, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-14-auction-monitor-improvements-design.md`

---

## Chunk 1: Foundation (Dependencies, Logger, Config)

### Task 1: Add dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml:7-15`

- [ ] **Step 1: Add aiohttp, aiosqlite, and test dependencies**

```toml
dependencies = [
    "ruff==0.11.5",
    "mypy==1.15.0",
    "python-dotenv==1.1.0",
    "python-telegram-bot==22.0",
    "eth-ape==0.8.36",
    "silverback==0.7.27",
    "ape-etherscan==0.8.4",
    "aiohttp>=3.9",
    "aiosqlite>=0.19",
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
```

- [ ] **Step 2: Add pytest-asyncio config to pyproject.toml**

Append this section to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: Create tests directory**

```bash
mkdir -p tests && touch tests/__init__.py
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/__init__.py
git commit -m "chore: add aiohttp, aiosqlite, pytest, pytest-asyncio dependencies"
```

---

### Task 2: Create structured logging module

**Files:**
- Create: `bot/logger.py`

- [ ] **Step 1: Create bot/logger.py**

```python
import logging
import os


def setup_logging() -> None:
    """Configure structured logging for the bot.

    Reads LOG_LEVEL from environment (default: INFO).
    Sets a consistent format across all bot modules:
    timestamp [LEVEL] module: message
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )


# Configure logging on import so all modules get the same setup
setup_logging()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd /Users/zero/dev/leviathan-auction-monitor && python -c "from bot.logger import setup_logging; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add bot/logger.py
git commit -m "feat: add structured logging module"
```

---

### Task 3: Make config.py env-driven

**Files:**
- Modify: `bot/config.py`

- [ ] **Step 1: Rewrite bot/config.py to read from env vars**

```python
import logging
import os
from typing import cast

from ape import Contract, networks
from ape.contracts.base import ContractInstance

logger = logging.getLogger(__name__)

# Contract address — configurable via env, defaults to mainnet Leviathan Auction House
AUCTION_HOUSE_ADDRESS = os.getenv("AUCTION_HOUSE_ADDRESS", "0xfF737F349e40418Abd9D7b3c865683f93cA3c890")

# Block explorer base URL — configurable for different chains/explorers
EXPLORER_BASE_URL = os.getenv("EXPLORER_BASE_URL", "https://etherscan.io")

# Frontend auction page base URL — used to link notifications to the UI
AUCTION_UI_BASE_URL = os.getenv("AUCTION_UI_BASE_URL", "")


def auction_house() -> ContractInstance:
    """Return the Auction House contract instance.

    Reads contract address from AUCTION_HOUSE_ADDRESS env var.
    The Contract() call fetches the ABI via Etherscan plugin.
    """
    return cast(ContractInstance, Contract(AUCTION_HOUSE_ADDRESS))


def explorer_address_url() -> str:
    """Return the block explorer address URL prefix.

    Callers append the address to build the full URL.
    Example: explorer_address_url() + "0xabc..." -> "https://etherscan.io/address/0xabc..."
    """
    return f"{EXPLORER_BASE_URL}/address/"


def explorer_tx_url() -> str:
    """Return the block explorer transaction URL prefix.

    Callers append the tx hash to build the full URL.
    Example: explorer_tx_url() + "0xdef..." -> "https://etherscan.io/tx/0xdef..."
    """
    return f"{EXPLORER_BASE_URL}/tx/"


def auction_ui_url(auction_id: int) -> str:
    """Return the frontend URL for a specific auction.

    Returns empty string if AUCTION_UI_BASE_URL is not configured,
    so callers can conditionally include the link.
    """
    if not AUCTION_UI_BASE_URL:
        return ""
    return f"{AUCTION_UI_BASE_URL}/auction/{auction_id}"


def ens_name(address: str) -> str:
    """Resolve an Ethereum address to its ENS name.

    Falls back to the raw address string if ENS lookup fails
    or no name is registered. This is a synchronous RPC call —
    callers in async context must wrap with asyncio.to_thread().
    """
    try:
        resolved = networks.active_provider.web3.ens.name(address)
        if resolved is None:
            return str(address)
        return str(resolved)
    except Exception:
        logger.warning("ENS resolution failed for %s", address)
        return str(address)
```

- [ ] **Step 2: Commit**

```bash
git add bot/config.py
git commit -m "feat: make config env-driven with AUCTION_HOUSE_ADDRESS, EXPLORER_BASE_URL, AUCTION_UI_BASE_URL"
```

---

## Chunk 2: Database and Telegram Modules

### Task 4: Create SQLite state module

**Files:**
- Create: `bot/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the failing test for db.py**

```python
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
    # Auction ends in 1 hour (3600s) — within 2-hour horizon
    await add_auction(auction_id=1, end_time=now + 3600)
    results = await get_ending_soon(horizon_seconds=7200, now_timestamp=now)
    assert len(results) == 1
    assert results[0] == (1, now + 3600)


async def test_get_ending_soon_excludes_far_future(db):
    """Auctions ending beyond the horizon are not returned."""
    now = 1000000
    # Auction ends in 3 hours — outside 2-hour horizon
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zero/dev/leviathan-auction-monitor && python -m pytest tests/test_db.py -v`

Expected: FAIL — `bot.db` module does not exist yet.

- [ ] **Step 3: Create bot/db.py**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/zero/dev/leviathan-auction-monitor && python -m pytest tests/test_db.py -v`

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/db.py tests/test_db.py
git commit -m "feat: add async SQLite state persistence module with tests"
```

---

### Task 5: Refactor Telegram module — singleton Bot + photo support

**Files:**
- Modify: `bot/tg.py`

- [ ] **Step 1: Rewrite bot/tg.py with singleton Bot and photo support**

```python
"""Telegram notification layer.

Provides two send functions:
- notify_group_chat(): sends HTML text messages
- notify_group_chat_photo(): sends a photo with HTML caption, falls back to text

The Bot instance is created once at module level and reused across
all sends, avoiding the overhead of instantiation per message.
"""

import logging
import os

from telegram import Bot

logger = logging.getLogger(__name__)

BOT_ACCESS_TOKEN = os.getenv("BOT_ACCESS_TOKEN", "")
if BOT_ACCESS_TOKEN == "":
    raise RuntimeError("!BOT_ACCESS_TOKEN")

GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
if GROUP_CHAT_ID == 0:
    raise RuntimeError("!GROUP_CHAT_ID")

ERROR_GROUP_CHAT_ID = int(os.getenv("ERROR_GROUP_CHAT_ID", "0"))
if ERROR_GROUP_CHAT_ID == 0:
    raise RuntimeError("!ERROR_GROUP_CHAT_ID")

# Singleton Bot instance — created once, reused for all sends
_bot = Bot(token=BOT_ACCESS_TOKEN)


async def notify_group_chat(
    text: str,
    parse_mode: str = "HTML",
    chat_id: int = GROUP_CHAT_ID,
    disable_web_page_preview: bool = True,
) -> None:
    """Send an HTML text message to a Telegram group chat.

    Args:
        text: HTML-formatted message body.
        parse_mode: Telegram parse mode (default HTML).
        chat_id: Target chat ID (defaults to GROUP_CHAT_ID).
        disable_web_page_preview: Suppress link previews (default True).
    """
    try:
        await _bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as e:
        logger.error("Failed to send message to chat %s: %s", chat_id, e)


async def notify_group_chat_photo(
    photo_url: str,
    caption: str,
    parse_mode: str = "HTML",
    chat_id: int = GROUP_CHAT_ID,
) -> None:
    """Send a photo with caption to a Telegram group chat.

    Attempts to send the image from photo_url with an HTML caption.
    If the photo send fails (bad URL, Telegram rejects it, caption
    too long for photo mode's 1024 char limit), falls back to a
    plain text message via notify_group_chat().

    Args:
        photo_url: URL of the image to send.
        caption: HTML-formatted caption text.
        chat_id: Target chat ID (defaults to GROUP_CHAT_ID).
    """
    try:
        await _bot.send_photo(
            chat_id=chat_id,
            photo=photo_url,
            caption=caption,
            parse_mode=parse_mode,
        )
    except Exception as e:
        logger.warning("Photo send failed (falling back to text): %s", e)
        await notify_group_chat(text=caption, parse_mode=parse_mode, chat_id=chat_id)
```

- [ ] **Step 2: Commit**

```bash
git add bot/tg.py
git commit -m "feat: singleton Telegram Bot + photo notification support"
```

---

## Chunk 3: Async API Module

### Task 6: Migrate api.py from requests to aiohttp

**Files:**
- Modify: `bot/api.py`

- [ ] **Step 1: Rewrite bot/api.py as async with aiohttp**

```python
"""Leviathan API client — fetches auction metadata.

Uses a shared aiohttp.ClientSession for non-blocking HTTP requests.
Session lifecycle:
- init_session() creates the session (called from @bot.on_worker_startup)
- close_session() closes it (called from @bot.on_worker_shutdown)

On failure, auction_data() raises rather than silently returning {}.
Callers are responsible for catching exceptions and sending degraded
notifications.
"""

import logging
from typing import Any, Dict, Optional

import aiohttp
from ape import networks

from bot.config import auction_house

logger = logging.getLogger(__name__)

# Module-level session — initialized by init_session(), closed by close_session()
_session: Optional[aiohttp.ClientSession] = None


def _get_session() -> aiohttp.ClientSession:
    """Return the active aiohttp session.

    Raises RuntimeError if init_session() hasn't been called yet.
    """
    if _session is None:
        raise RuntimeError("HTTP session not initialized — call init_session() first")
    return _session


async def init_session() -> None:
    """Create the shared aiohttp.ClientSession with a 15-second timeout.

    Called once from @bot.on_worker_startup().
    """
    global _session
    timeout = aiohttp.ClientTimeout(total=15)
    _session = aiohttp.ClientSession(timeout=timeout)
    logger.info("HTTP session initialized")


async def close_session() -> None:
    """Close the shared aiohttp session. Safe to call even if not initialized."""
    global _session
    if _session is not None:
        await _session.close()
        _session = None
        logger.info("HTTP session closed")


def _leviathan_base_url() -> str:
    """Build the Leviathan API base URL from the active chain and contract address.

    Pattern: https://api.leviathannews.xyz/api/v1/auction_contract/{chain_id}/{address}/
    """
    chain_id = networks.active_provider.chain_id
    house = auction_house().address
    return f"https://api.leviathannews.xyz/api/v1/auction_contract/{chain_id}/{house}/"


def _parse_auction(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a raw API auction object into a flat dictionary.

    Extracts metadata fields (name, description, image_url) and
    flattens NFT-style attributes into a {trait_type: value} dict.
    """
    meta = obj.get("metadata") or {}
    attrs = {a.get("trait_type"): a.get("value") for a in meta.get("attributes", []) if isinstance(a, dict)}
    return {
        "auction_id": obj.get("auction_id"),
        "chain_id": obj.get("chain_id"),
        "contract_address": obj.get("contract_addr"),
        "name": meta.get("name"),
        "description": meta.get("description"),
        "image_url": meta.get("image_url"),
        "attributes": attrs,
        "created_at": obj.get("created_at"),
        "updated_at": obj.get("updated_at"),
        "ipfs_hash": obj.get("ipfs_hash"),
        "ipfs_status": obj.get("ipfs_status"),
    }


async def auction_data(auction_id: int) -> Dict[str, Any]:
    """Fetch metadata for a specific auction from the Leviathan API.

    Args:
        auction_id: The on-chain auction ID to look up.

    Returns:
        Parsed auction dict with name, description, image_url, etc.

    Raises:
        aiohttp.ClientError: On HTTP request failure (timeout, 4xx, 5xx).
        ValueError: If the response body is not valid JSON.
    """
    session = _get_session()
    url = f"{_leviathan_base_url()}{auction_id}/"

    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    return _parse_auction(data)
```

- [ ] **Step 2: Commit**

```bash
git add bot/api.py
git commit -m "feat: migrate api.py from sync requests to async aiohttp"
```

---

## Chunk 4: Bot.py Overhaul (Event Handlers + Lifecycle)

### Task 7: Rewrite bot.py with enriched notifications, lifecycle hooks, and async contract calls

**Files:**
- Modify: `bot/bot.py`

- [ ] **Step 1: Rewrite bot.py**

```python
"""Silverback bot — listens to Leviathan Auction House events on Ethereum mainnet.

Subscribes to four contract events (AuctionCreated, AuctionBid, AuctionExtended,
AuctionSettled) via WebSocket and sends enriched Telegram notifications.

Resource lifecycle:
- on_worker_startup: initializes SQLite DB and aiohttp session
- on_worker_shutdown: closes both
- on_startup: sends "bot started" to error group
- on_shutdown: sends "bot stopped" to error group

Blocking RPC/ENS calls are wrapped in asyncio.to_thread() to avoid
blocking the Silverback async event loop.
"""

# Ensure logging is configured before any other bot.* imports
import bot.logger  # noqa: F401 — side-effect import that calls setup_logging()

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ape.types import ContractLog
from silverback import SilverbackBot, StateSnapshot

from bot.api import auction_data, close_session, init_session
from bot.config import auction_house, auction_ui_url, ens_name, explorer_tx_url
from bot.db import add_auction, close_db, get_ending_soon, init_db, mark_notified, remove_auction
from bot.tg import ERROR_GROUP_CHAT_ID, notify_group_chat, notify_group_chat_photo

logger = logging.getLogger(__name__)

bot = SilverbackBot()


# =============================================================================
# Resource Lifecycle (per-worker)
# =============================================================================


@bot.on_worker_startup()
async def worker_startup(state: StateSnapshot) -> None:
    """Initialize database connection and HTTP session for this worker.

    Called by Silverback on every worker process. These resources are
    needed by event handlers and the cron job.
    """
    await init_db()
    await init_session()
    logger.info("Worker resources initialized")


@bot.on_worker_shutdown()
async def worker_shutdown() -> None:
    """Close database connection and HTTP session for this worker."""
    await close_session()
    await close_db()
    logger.info("Worker resources cleaned up")


# =============================================================================
# Application Startup / Shutdown (runs once)
# =============================================================================


@bot.on_startup()
async def bot_startup(startup_state: StateSnapshot) -> None:
    """Send a startup notification to the error group.

    Runs once across all workers — used for operational alerts,
    not resource initialization.
    """
    await notify_group_chat(
        "🟢 🐙 <b>leviathan auction bot started successfully</b>",
        chat_id=ERROR_GROUP_CHAT_ID,
    )
    logger.info("Bot startup notification sent")


@bot.on_shutdown()
async def bot_shutdown() -> None:
    """Send a shutdown notification to the error group."""
    await notify_group_chat(
        "🔴 🐙 <b>leviathan auction bot shutdown successfully</b>",
        chat_id=ERROR_GROUP_CHAT_ID,
    )
    logger.info("Bot shutdown notification sent")


# =============================================================================
# Chain Events
# =============================================================================


@bot.on_(auction_house().AuctionCreated)
async def on_auction_created(event: ContractLog) -> None:
    """Handle AuctionCreated event — fetch metadata, send photo notification, track state.

    Fetches the auction name/description/image from the Leviathan API.
    Sends a photo message if an image URL is available, otherwise text-only.
    On API failure, sends a degraded text notification with on-chain data only.
    Stores the auction end time in SQLite for the ending-soon cron.
    """
    auction_id = event.auction_id
    end_time_str = datetime.fromtimestamp(event.end_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")

    # Fetch minimum bid from contract — synchronous RPC, run in thread
    minimum_total_bid = await asyncio.to_thread(lambda: int(auction_house().minimum_total_bid(auction_id)) / 1e18)

    # Build the auction page link (empty string if AUCTION_UI_BASE_URL not set)
    ui_link = auction_ui_url(auction_id)
    link_line = f'\n🔗 <a href="{ui_link}">View Auction</a>' if ui_link else ""

    # Try to fetch metadata from the Leviathan API
    try:
        data = await auction_data(auction_id)
        auction_name = data.get("name", f"Auction {auction_id}")
        auction_description = data.get("description", "")
        image_url = data.get("image_url", "")

        caption = (
            "🐙 A new auction has been created!\n\n"
            f"<b>{auction_name}</b>\n"
            f"{auction_description}\n\n"
            f"📌 <b>Auction ID:</b> {auction_id}\n"
            f"⏳ <b>End Time:</b> {end_time_str}\n"
            f"💵 <b>Minimum Total Bid:</b> {minimum_total_bid:.4f} WETH"
            f"{link_line}"
        )

        # Send as photo if image available, otherwise text
        if image_url:
            await notify_group_chat_photo(photo_url=image_url, caption=caption)
        else:
            await notify_group_chat(caption)

    except Exception as e:
        # Degraded notification — on-chain data only, no API metadata
        logger.warning("API fetch failed for auction %s, sending degraded notification: %s", auction_id, e)
        await notify_group_chat(
            "🐙 A new auction has been created!\n\n"
            f"📌 <b>Auction ID:</b> {auction_id}\n"
            f"⏳ <b>End Time:</b> {end_time_str}\n"
            f"💵 <b>Minimum Total Bid:</b> {minimum_total_bid:.4f} WETH"
            f"{link_line}"
        )

    # Track auction end time in SQLite for the ending-soon cron
    await add_auction(auction_id, int(event.end_time))
    logger.info("AuctionCreated: id=%s end_time=%s min_bid=%.4f", auction_id, end_time_str, minimum_total_bid)


@bot.on_(auction_house().AuctionBid)
async def on_auction_bid(event: ContractLog) -> None:
    """Handle AuctionBid event — notify with bidder ENS name and tx link.

    Resolves the bidder address to ENS in a thread to avoid blocking.
    Includes an Etherscan transaction link for transparency.
    """
    bidder_name = await asyncio.to_thread(ens_name, event.bidder)
    tx_hash = event.transaction_hash
    tx_link = f'{explorer_tx_url()}{tx_hash}'

    await notify_group_chat(
        f"🦍 A new bid of <b>{int(event.value) / 1e18:.4f} WETH</b> "
        f"on <b>Auction {event.auction_id}</b> by <code>{bidder_name}</code>.\n"
        f'🔗 <a href="{tx_link}">View Transaction</a>'
    )
    logger.info("AuctionBid: auction=%s bidder=%s value=%.4f", event.auction_id, bidder_name, int(event.value) / 1e18)


@bot.on_(auction_house().AuctionExtended)
async def on_auction_extended(event: ContractLog) -> None:
    """Handle AuctionExtended event — notify with time extension and auction link.

    Calculates how much extra time was added by comparing the new end_time
    against the current UTC timestamp.
    """
    auction_id = event.auction_id
    seconds_remaining = int(event.end_time) - int(datetime.now(tz=timezone.utc).timestamp())
    minutes_added = int(timedelta(seconds=seconds_remaining).seconds / 60)

    ui_link = auction_ui_url(auction_id)
    link_line = f'\n🔗 <a href="{ui_link}">View Auction</a>' if ui_link else ""

    await notify_group_chat(
        f"🕰️ <b>Auction {auction_id}</b> has been extended by <b>~{minutes_added}m</b>."
        f"{link_line}"
    )
    logger.info("AuctionExtended: id=%s +%dm", auction_id, minutes_added)


@bot.on_(auction_house().AuctionSettled)
async def on_auction_settled(event: ContractLog) -> None:
    """Handle AuctionSettled event — notify with winner, amount, and links.

    Resolves winner address to ENS. Includes both the winning tx link
    and the auction page link. Removes the auction from SQLite state
    since it no longer needs ending-soon tracking.
    """
    winner_name = await asyncio.to_thread(ens_name, event.winner)
    tx_hash = event.transaction_hash
    tx_link = f'{explorer_tx_url()}{tx_hash}'
    auction_id = event.auction_id

    ui_link = auction_ui_url(auction_id)
    link_line = f'\n🔗 <a href="{ui_link}">View Auction</a>' if ui_link else ""

    await notify_group_chat(
        f"🏆 <b>Auction {auction_id}</b> has been settled. "
        f"The winner is <code>{winner_name}</code> with a bid of <b>{int(event.amount) / 1e18:.4f} WETH</b>.\n"
        f'🔗 <a href="{tx_link}">View Transaction</a>'
        f"{link_line}"
    )

    # Clean up — settled auctions don't need ending-soon tracking
    await remove_auction(auction_id)
    logger.info("AuctionSettled: id=%s winner=%s amount=%.4f", auction_id, winner_name, int(event.amount) / 1e18)


# =============================================================================
# Cron Jobs
# =============================================================================


@bot.cron("0 * * * *")  # Top of every hour
async def notify_ending_soon(_: datetime) -> None:
    """Hourly cron — send heartbeat and alert on auctions ending within 2 hours.

    1. Sends a "still alive" heartbeat to the error group for monitoring.
    2. Queries SQLite for auctions ending within 2 hours that haven't been notified.
    3. For each, tries to fetch the current highest bid from the contract.
    4. Sends a Telegram alert with time remaining and bid info (if available).
    5. Marks each auction as notified so it won't alert again.
    """
    # Heartbeat — confirms the bot is running
    await notify_group_chat("🟢 🐙 STILL ALIVE", chat_id=ERROR_GROUP_CHAT_ID)

    now_s = int(datetime.now(tz=timezone.utc).timestamp())
    ending_soon = await get_ending_soon(horizon_seconds=2 * 60 * 60, now_timestamp=now_s)

    if not ending_soon:
        return

    for auction_id, end_time in ending_soon:
        minutes_left = (end_time - now_s) // 60

        # Try to get current highest bid from the contract
        bid_info = ""
        try:
            auction_state = await asyncio.to_thread(auction_house().auctions, auction_id)
            highest_bid = int(auction_state.highestBid) / 1e18
            highest_bidder = await asyncio.to_thread(ens_name, str(auction_state.highestBidder))
            bid_info = f"\n💰 <b>Current highest bid:</b> {highest_bid:.4f} WETH by <code>{highest_bidder}</code>"
        except Exception as e:
            logger.warning("Failed to fetch auction state for %s: %s", auction_id, e)

        ui_link = auction_ui_url(auction_id)
        link_line = f'\n🔗 <a href="{ui_link}">View Auction</a>' if ui_link else ""

        await notify_group_chat(
            f"⏰ <b>Auction {auction_id}</b> is ending soon (<b>~{minutes_left}m</b> left)."
            f"{bid_info}"
            f"{link_line}"
        )

        await mark_notified(auction_id)
        logger.info("Ending soon alert sent: auction=%s minutes_left=%d", auction_id, minutes_left)
```

- [ ] **Step 2: Commit**

```bash
git add bot/bot.py
git commit -m "feat: rewrite bot.py with enriched notifications, lifecycle hooks, async RPC calls"
```

---

## Chunk 5: Config Files and Cleanup

### Task 8: Update .env.example and .gitignore

**Files:**
- Modify: `.env.example`
- Modify: `.gitignore`

Note: `docker-compose.yml` needs no changes — the `.:/app` bind mount already persists `bot_state.db` to the host filesystem.

- [ ] **Step 1: Update .env.example with new vars**

```
BOT_ACCESS_TOKEN=""
GROUP_CHAT_ID=""
ERROR_GROUP_CHAT_ID=""
ETHERSCAN_API_KEY=""
ETH_RPC_URL=""
ETH_WS_URL=""
AUCTION_HOUSE_ADDRESS="0xfF737F349e40418Abd9D7b3c865683f93cA3c890"
EXPLORER_BASE_URL="https://etherscan.io"
AUCTION_UI_BASE_URL=""
LOG_LEVEL="INFO"
```

- [ ] **Step 3: Update .gitignore to exclude the SQLite database**

Add to `.gitignore`:
```
bot_state.db
bot_state.db-wal
bot_state.db-shm
```

- [ ] **Step 4: Commit**

```bash
git add .env.example .gitignore
git commit -m "chore: update .env.example with new config vars, gitignore SQLite files"
```

---

### Task 9: Delete bot_state.json and old state helpers

**Files:**
- Verify: `bot_state.json` does not exist in repo (it's in .gitignore)
- Verify: `load_state()` and `save_state()` are removed from bot.py (done in Task 7)
- Verify: `import json` is removed from bot.py (done in Task 7)
- Verify: `STATE_FILE` constant is removed from bot.py (done in Task 7)

- [ ] **Step 1: Verify no references to old state system remain**

Run: `grep -r "load_state\|save_state\|STATE_FILE\|bot_state.json" bot/`

Expected: No matches.

- [ ] **Step 2: Run ruff to lint all files**

Run: `cd /Users/zero/dev/leviathan-auction-monitor && ruff check bot/ && ruff format --check bot/`

Expected: Clean (or fix any issues).

- [ ] **Step 3: Fix any lint issues and commit**

```bash
ruff format bot/
ruff check --fix bot/
git add -A
git commit -m "chore: lint cleanup"
```

---

### Task 10: Sync to dev server and verify

- [ ] **Step 1: Push to GitHub**

```bash
git push origin main
```

- [ ] **Step 2: Pull on dev server**

```bash
ssh dev "cd ~/server/leviathan-auction-monitor && git pull origin main"
```

- [ ] **Step 3: Install dependencies on dev server**

```bash
ssh dev "cd ~/server/leviathan-auction-monitor && export PATH=\$HOME/.local/bin:\$PATH && uv python install 3.12 && uv venv --python 3.12 && uv sync"
```

- [ ] **Step 4: Run tests on dev server**

```bash
ssh dev "cd ~/server/leviathan-auction-monitor && export PATH=\$HOME/.local/bin:\$PATH && . .venv/bin/activate && python -m pytest tests/ -v"
```

Expected: All tests pass.

- [ ] **Step 5: Set up .env on dev server**

```bash
ssh dev "cd ~/server/leviathan-auction-monitor && cp .env.example .env"
# Then edit .env with actual values
```

- [ ] **Step 6: Start the bot**

```bash
ssh dev "cd ~/server/leviathan-auction-monitor && export PATH=\$HOME/.local/bin:\$PATH && . .venv/bin/activate && export \$(grep -v '^#' .env | xargs) && silverback run --network :mainnet"
```
