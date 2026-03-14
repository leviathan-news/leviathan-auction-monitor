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
import asyncio
import logging
from datetime import datetime, timezone

from ape.types import ContractLog
from silverback import SilverbackBot, StateSnapshot

import bot.logger  # noqa: F401 — side-effect import that calls setup_logging()
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

    # Fetch minimum bid from contract — synchronous RPC, run in thread.
    # Guarded so an RPC failure doesn't prevent the notification or DB tracking.
    min_bid_str = "N/A"
    try:
        minimum_total_bid = await asyncio.to_thread(
            lambda: int(auction_house().minimum_total_bid(auction_id)) / 1e18
        )
        min_bid_str = f"{minimum_total_bid:.4f} WETH"
    except Exception as e:
        logger.warning("RPC call for minimum_total_bid failed for auction %s: %s", auction_id, e)

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
            f"💵 <b>Minimum Total Bid:</b> {min_bid_str}"
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
            f"💵 <b>Minimum Total Bid:</b> {min_bid_str}"
            f"{link_line}"
        )

    # Always track auction end time in SQLite — even if notification failed,
    # the ending-soon cron still needs to know about this auction.
    await add_auction(auction_id, int(event.end_time))
    logger.info("AuctionCreated: id=%s end_time=%s min_bid=%s", auction_id, end_time_str, min_bid_str)


@bot.on_(auction_house().AuctionBid)
async def on_auction_bid(event: ContractLog) -> None:
    """Handle AuctionBid event — notify with bidder ENS name and tx link.

    Resolves the bidder address to ENS in a thread to avoid blocking.
    Includes an Etherscan transaction link for transparency.
    """
    bidder_name = await asyncio.to_thread(ens_name, event.bidder)
    tx_hash = event.transaction_hash
    tx_link = f"{explorer_tx_url()}{tx_hash}"

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
    # Calculate remaining time using direct integer division — avoids the
    # timedelta.seconds truncation bug (which wraps at 24 hours).
    seconds_remaining = int(event.end_time) - int(datetime.now(tz=timezone.utc).timestamp())
    minutes_remaining = max(seconds_remaining // 60, 0)

    ui_link = auction_ui_url(auction_id)
    link_line = f'\n🔗 <a href="{ui_link}">View Auction</a>' if ui_link else ""

    await notify_group_chat(
        f"🕰️ <b>Auction {auction_id}</b> has been extended (<b>~{minutes_remaining}m</b> remaining).{link_line}"
    )

    # Update the auction's end_time in SQLite so the ending-soon cron uses the
    # new deadline. add_auction() upserts and resets notified_ending_soon to 0,
    # making the auction eligible for a fresh ending-soon alert.
    await add_auction(auction_id, int(event.end_time))
    logger.info("AuctionExtended: id=%s ~%dm remaining", auction_id, minutes_remaining)


@bot.on_(auction_house().AuctionSettled)
async def on_auction_settled(event: ContractLog) -> None:
    """Handle AuctionSettled event — notify with winner, amount, and links.

    Resolves winner address to ENS. Includes both the winning tx link
    and the auction page link. Removes the auction from SQLite state
    since it no longer needs ending-soon tracking.
    """
    winner_name = await asyncio.to_thread(ens_name, event.winner)
    tx_hash = event.transaction_hash
    tx_link = f"{explorer_tx_url()}{tx_hash}"
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
            f"⏰ <b>Auction {auction_id}</b> is ending soon (<b>~{minutes_left}m</b> left).{bid_info}{link_line}"
        )

        await mark_notified(auction_id)
        logger.info("Ending soon alert sent: auction=%s minutes_left=%d", auction_id, minutes_left)
