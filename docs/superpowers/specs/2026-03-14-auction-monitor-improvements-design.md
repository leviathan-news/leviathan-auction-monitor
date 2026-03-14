# Auction Monitor — Hardening + Enrichment Overhaul

**Date:** 2026-03-14
**Status:** Approved
**Scope:** Reliability fixes, async migration, notification enrichment, structured logging, config externalization

---

## 1. State Persistence — SQLite

Replace `bot_state.json` with SQLite (`bot_state.db`) using `aiosqlite` for non-blocking access.

Silverback's built-in `bot.state`/`Datastore` persists to JSON in `.silverback-sessions/` and lacks atomic per-row updates and crash-safe writes. SQLite with WAL mode provides both, making it the better fit for auction tracking.

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS auctions (
    auction_id INTEGER PRIMARY KEY,
    end_time INTEGER NOT NULL,
    notified_ending_soon BOOLEAN DEFAULT 0
);
```

- WAL mode enabled for crash safety.
- A single `aiosqlite` connection is opened in `init_db()` and stored as a module-level variable. It is closed in a corresponding `close_db()` called from `@bot.on_worker_shutdown()`.
- DB path configurable but defaults to `bot_state.db` in working directory.
- Auction rows inserted on `AuctionCreated`, flagged on ending-soon notification, deleted on `AuctionSettled`.
- Eliminates the missing `save_state()` bug — each mutation is an atomic SQL statement.

**New module:** `bot/db.py`
- `async def init_db()` — open connection, create table, enable WAL. Store connection as module-level `_conn`.
- `async def close_db()` — close the module-level connection.
- `async def add_auction(auction_id: int, end_time: int)`
- `async def get_ending_soon(horizon_seconds: int) -> list[tuple[int, int]]` — returns auctions ending within horizon that haven't been notified
- `async def mark_notified(auction_id: int)`
- `async def remove_auction(auction_id: int)`

---

## 2. Async HTTP — aiohttp

Replace synchronous `requests` with `aiohttp`.

**Changes to `bot/api.py`:**
- `auction_data()` becomes `async def auction_data()`
- A shared `aiohttp.ClientSession` is created in `@bot.on_worker_startup()` via `init_session()` and closed in `@bot.on_worker_shutdown()` via `close_session()`. Stored as module-level variable.
- Timeout: 15 seconds (matching current behavior)
- Error handling: `aiohttp.ClientError` replaces `requests.RequestException`. On failure, the function raises rather than silently returning `{}`. Callers handle the exception — log the error and send a degraded notification (without metadata) rather than dropping the event.

**Dependency change:** Add `aiohttp`. (`requests` is not a direct dependency in `pyproject.toml` — only used at import level in `bot/api.py`, so the change is removing the import.)

---

## 3. Telegram Client — Singleton + Image Support

**Changes to `bot/tg.py`:**
- `Bot()` instantiated once at module level, reused across all sends.
- Existing `notify_group_chat()` keeps its signature (text, parse_mode, chat_id, disable_web_page_preview).
- New function: `async def notify_group_chat_photo(photo_url: str, caption: str, ...)` — sends an image with HTML caption to the group chat. Falls back to `notify_group_chat()` (text-only) if the photo send fails.

---

## 4. Notification Enrichment

### AuctionCreated
- Send as photo message (auction image from API's `image_url`) with caption.
- Caption includes: name, description, auction ID, end time, minimum bid, link to auction page.
- Falls back to text-only if no image URL or photo send fails.
- **Degraded fallback** (API failure): Send text-only notification with auction ID, end time, and minimum bid from on-chain data. Log the API error at WARNING level.

### AuctionBid
- Add Etherscan transaction link (from `event.transaction_hash`).
- Format: existing message + `\n🔗 <a href="{EXPLORER_BASE_URL}/tx/{tx_hash}">View Transaction</a>`

### AuctionExtended
- Add link to auction page.

### AuctionSettled
- Add link to winning transaction.
- Add link to auction page.
- Add `await remove_auction(event.auction_id)` to clean up the auction from SQLite state. This is new behavior — the current handler does not remove settled auctions from state.

### Ending Soon (cron)
- The hourly "still alive" heartbeat message to `ERROR_GROUP_CHAT_ID` is retained.
- Query the current auction state via `asyncio.to_thread(auction_house().auctions, auction_id)` to get the current highest bid amount and bidder address. The `auctions()` view method returns a struct with `highestBid` and `highestBidder` fields.
- Resolve bidder to ENS via `asyncio.to_thread(ens_name, address)`.
- If the RPC call fails, send the notification without bid info (just time remaining). Log the RPC error at WARNING level.
- Include highest bid amount and bidder (ENS-resolved) in the alert.
- Example: `⏰ Auction 42 ending in ~45m — current highest bid: 1.2 WETH by vitalik.eth`

### URL Patterns
- Auction page: `{AUCTION_UI_BASE_URL}/auction/{auction_id}` (configurable via .env)
- Transaction: `{EXPLORER_BASE_URL}/tx/{tx_hash}` — `explorer_tx_url()` returns the full prefix `f"{EXPLORER_BASE_URL}/tx/"` (preserving current behavior, callers append the hash)
- Address: `{EXPLORER_BASE_URL}/address/{address}` — `explorer_address_url()` returns `f"{EXPLORER_BASE_URL}/address/"` (same pattern)

---

## 5. Configuration — .env Driven

**New env vars:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUCTION_HOUSE_ADDRESS` | `0xfF737F349e40418Abd9D7b3c865683f93cA3c890` | Contract address |
| `EXPLORER_BASE_URL` | `https://etherscan.io` | Block explorer base URL |
| `AUCTION_UI_BASE_URL` | *(required)* | Frontend URL for auction links |
| `LOG_LEVEL` | `INFO` | Python logging level |

**Existing env vars (unchanged):**
`BOT_ACCESS_TOKEN`, `GROUP_CHAT_ID`, `ERROR_GROUP_CHAT_ID`, `ETHERSCAN_API_KEY`, `ETH_RPC_URL`, `ETH_WS_URL`

**Changes to `bot/config.py`:**
- `auction_house()` reads address from `AUCTION_HOUSE_ADDRESS` env var.
- `explorer_tx_url() -> str` returns `f"{EXPLORER_BASE_URL}/tx/"` (full prefix, callers append hash).
- `explorer_address_url() -> str` returns `f"{EXPLORER_BASE_URL}/address/"` (full prefix, callers append address).
- New: `auction_ui_url(auction_id: int) -> str` returns `f"{AUCTION_UI_BASE_URL}/auction/{auction_id}"`.

---

## 6. Structured Logging

**New module:** `bot/logger.py`
- Configures Python `logging` with format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- Log level from `LOG_LEVEL` env var.
- Called once at import time.

**Per-module loggers:** Each module uses `logger = logging.getLogger(__name__)`.

**Log levels:**
- `INFO` — normal events (auction created, bid placed, settled, startup/shutdown)
- `WARNING` — recoverable issues (API timeout, ENS resolution failure, photo send fallback, RPC call failure in cron)
- `ERROR` — failures (Telegram send failed, DB write failed)

All `print()` statements removed.

---

## 7. Resource Lifecycle — Silverback Hooks

Silverback provides two hook pairs:
- `on_startup()` / `on_shutdown()` — runs once, intended for backfill/notification logic.
- `on_worker_startup()` / `on_worker_shutdown()` — runs on every worker, intended for resource initialization (DB connections, HTTP sessions).

**Resource initialization in `@bot.on_worker_startup()`:**
- `await init_db()` — open SQLite connection, create table, enable WAL
- `await init_session()` — create shared `aiohttp.ClientSession`

**Resource cleanup in `@bot.on_worker_shutdown()`:**
- `await close_session()` — close aiohttp session
- `await close_db()` — close SQLite connection

**Application-level hooks (`@bot.on_startup()` / `@bot.on_shutdown()`):**
- Keep startup/shutdown Telegram notifications here (same as current behavior).

---

## 8. Blocking Contract Calls

Several handlers call synchronous Ape contract methods inside async handlers:
- `auction_house().minimum_total_bid(auction_id)` in `on_auction_created`
- `auction_house().auctions(auction_id)` in `notify_ending_soon` (new)
- `ens_name(address)` in multiple handlers

All synchronous RPC/ENS calls must be wrapped in `asyncio.to_thread()` to avoid blocking the event loop. This applies to existing calls (minimum_total_bid, ens_name) and new calls (auctions query in cron).

---

## 9. Bug Fixes

| Bug | Fix |
|-----|-----|
| `notify_ending_soon` never calls `save_state()` after removing notified auctions | Replaced by SQLite `mark_notified()` — atomic per-row update |
| Synchronous `requests.get()` blocks async event loop | Replaced by `aiohttp` |
| Synchronous contract/ENS calls block async event loop | Wrapped in `asyncio.to_thread()` |
| New `Bot()` instance created per message | Singleton at module level |
| `auction_data()` silently returns `{}` on failure | Raises exception; callers log + send degraded notification |
| Settled auctions never removed from state | `on_auction_settled` now calls `remove_auction()` |

---

## 10. Dependency Changes

```diff
# pyproject.toml dependencies — add these two:
+ "aiohttp>=3.9",
+ "aiosqlite>=0.19",
```

All existing dependencies unchanged (`python-dotenv`, `python-telegram-bot`, `eth-ape`, `silverback`, `ape-etherscan`, `ruff`, `mypy`). `requests` was never a direct dependency — only imported in `bot/api.py`, which switches to `aiohttp`.

---

## 11. File Structure After Changes

```
bot/
├── __init__.py          # unchanged
├── api.py               # async aiohttp, raises on failure
├── bot.py               # event handlers with enriched notifications, SQLite state, asyncio.to_thread for RPC
├── config.py            # env-driven config, auction_ui_url()
├── db.py                # NEW — async SQLite wrapper (single persistent connection)
├── logger.py            # NEW — structured logging setup
└── tg.py                # singleton Bot, notify_group_chat_photo()
```

---

## 12. .env.example After Changes

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
