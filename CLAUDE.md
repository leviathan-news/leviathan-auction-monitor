# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run bot
silverback run --network :mainnet

# Run with Docker
docker compose up -d

# Tests
python -m pytest tests/ -v
python -m pytest tests/test_db.py::test_add_and_get_ending_soon -v

# Lint & format
ruff check .
ruff check --fix .
ruff format .

# Type checking
mypy bot
```

## Architecture

Silverback bot that subscribes to Ethereum mainnet events from the Leviathan Auction House contract (`0xfF737F349e40418Abd9D7b3c865683f93cA3c890`) via WebSocket and sends Telegram notifications.

```
Ethereum (WS) → Silverback event loop → bot/bot.py handlers → bot/tg.py → Telegram
                                              ↕                    ↕
                                         bot/db.py (SQLite)   bot/api.py (Leviathan API)
```

### Module-level singleton pattern

`db.py`, `api.py`, and `tg.py` each hold a module-level singleton (`_conn`, `_session`, `_bot`). Access is guarded by `_get_conn()` / `_get_session()` which raise `RuntimeError` if the resource isn't initialized. Lifecycle is managed by Silverback hooks in `bot.py`.

### Silverback lifecycle hooks (bot.py)

- `@bot.on_worker_startup()` — initializes SQLite (`init_db`) and aiohttp (`init_session`) per worker
- `@bot.on_worker_shutdown()` — closes both per worker
- `@bot.on_startup()` / `@bot.on_shutdown()` — sends Telegram operational alerts (runs once globally)

Resource init goes in `on_worker_startup`, not `on_startup`. Application-level notifications go in `on_startup`.

### Async discipline

All Ape contract calls and ENS lookups are **synchronous RPC** — they must be wrapped in `asyncio.to_thread()` inside async handlers. `ens_name()` in `config.py` is intentionally sync; callers wrap it.

`api.py` uses `aiohttp` (async). `db.py` uses `aiosqlite` (async). `tg.py` uses `python-telegram-bot` (async).

### bot/__init__.py

Uses `except Exception: pass` around `from .bot import bot` so that submodules (`bot.db`, `bot.config`) can be imported in test environments where Ape isn't connected or Telegram env vars are missing.

### Event handlers

Four contract events: `AuctionCreated` (photo + metadata from API, degraded fallback on failure), `AuctionBid` (ENS + tx link), `AuctionExtended` (time remaining + DB end_time update), `AuctionSettled` (winner + cleanup from DB). One cron (`0 * * * *`): heartbeat + ending-soon alerts with highest bid info.

### SQLite state (db.py)

Single table `auctions(auction_id PK, end_time, notified_ending_soon)`. WAL mode. Used to track active auctions for the ending-soon cron. `add_auction()` is an upsert that resets the notified flag — used by both `AuctionCreated` and `AuctionExtended`.

## Testing

pytest with `asyncio_mode = "auto"`. Tests use `:memory:` SQLite via the `db` fixture. Only `bot/db.py` has unit tests — other modules require Ape network or Telegram credentials.

## Deployment

Runs on dev server (`ssh dev`) at `~/server/leviathan-auction-monitor/` via pm2 (`pm2 restart auction-monitor`). Dependencies managed with `uv`. Python 3.12 required.

## Config

All config via env vars (see `.env.example`). `AUCTION_HOUSE_ADDRESS`, `EXPLORER_BASE_URL`, `AUCTION_UI_BASE_URL`, `LOG_LEVEL` have defaults. `BOT_ACCESS_TOKEN`, `GROUP_CHAT_ID`, `ERROR_GROUP_CHAT_ID`, `ETHERSCAN_API_KEY`, `ETH_RPC_URL`, `ETH_WS_URL` are required. RPC endpoints configured in `ape-config.yaml` via `$ETH_RPC_URL` / `$ETH_WS_URL`.
