import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, cast

from ape.types import ContractLog
from silverback import SilverbackBot, StateSnapshot

from bot.api import auction_data
from bot.config import auction_house, ens_name
from bot.tg import ERROR_GROUP_CHAT_ID, notify_group_chat

# =============================================================================
# Bot Configuration & Constants
# =============================================================================


bot = SilverbackBot()

STATE_FILE = "bot_state.json"


# =============================================================================
# Startup / Shutdown
# =============================================================================


@bot.on_startup()
async def bot_startup(startup_state: StateSnapshot) -> None:
    await notify_group_chat(
        "🟢 🐙 <b>leviathan auction bot started successfully</b>",
        chat_id=ERROR_GROUP_CHAT_ID,
    )

    # # TEST on_auction_created
    # logs = list(auction_house().AuctionCreated.range(24219892, 24219894))
    # for log in logs:
    #     await on_auction_created(log)

    # # TEST on_auction_bid
    # logs = list(auction_house().AuctionBid.range(24147384, 24147386))
    # for log in logs:
    #     await on_auction_bid(log)

    # # TEST on_auction_extended
    # logs = list(auction_house().AuctionExtended.range(25134259, 25137180))
    # for log in logs:
    #     await on_auction_extended(log)

    # # TEST on_auction_settled
    # logs = list(auction_house().AuctionSettled.range(24149742, 24149744))
    # for log in logs:
    #     await on_auction_settled(log)


@bot.on_shutdown()
async def bot_shutdown() -> None:
    await notify_group_chat(
        "🔴 🐙 <b>leviathan auction bot shutdown successfully</b>",
        chat_id=ERROR_GROUP_CHAT_ID,
    )


# =============================================================================
# Chain Events
# =============================================================================


@bot.on_(auction_house().AuctionCreated)
async def on_auction_created(event: ContractLog) -> None:
    auction_id = event.auction_id
    end_time = datetime.fromtimestamp(event.end_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    minimum_total_bid = int(auction_house().minimum_total_bid(auction_id)) / 1e18

    # Get some data from API
    data = auction_data(auction_id)
    auction_name = data["name"]
    auction_description = data["description"]

    await notify_group_chat(
        "🐙 A new auction has been created!\n\n"
        f"<b>{auction_name}</b>\n"
        f"{auction_description}\n\n"
        f"📌 <b>Auction ID:</b> {auction_id}\n"
        f"⏳ <b>End Time:</b> {end_time}\n"
        f"💵 <b>Minimum Total Bid:</b> {minimum_total_bid:.4f} WETH"
    )

    # Track auction end times
    state = load_state()
    state.setdefault("auction_end_times", {})[auction_id] = int(event.end_time)
    save_state(state)


@bot.on_(auction_house().AuctionBid)
async def on_auction_bid(event: ContractLog) -> None:
    await notify_group_chat(
        f"🦍 A new bid on of <b>{int(event.value) / 1e18:.4f} WETH</b> "
        f"on <b>Auction {event.auction_id}</b> by <code>{ens_name(event.bidder)}</code>."
    )


@bot.on_(auction_house().AuctionExtended)
async def on_auction_extended(event: ContractLog) -> None:
    print("AUCTION EXTENDED")
    auction_id = event.auction_id
    seconds_added = int(event.end_time) - int(datetime.now(tz=timezone.utc).timestamp())
    await notify_group_chat(
        f"🕰️ <b>Auction {auction_id}</b> has been extended by <b>~{int(timedelta(seconds=seconds_added).seconds / 60)}m</b>."
    )


@bot.on_(auction_house().AuctionSettled)
async def on_auction_settled(event: ContractLog) -> None:
    print("AUCTION SETTLED")
    print(event)
    print(event.amount)
    await notify_group_chat(
        f"🏆 <b>Auction {event.auction_id}</b> has been settled. "
        f"The winner is <code>{ens_name(event.winner)}</code> with a bid of <b>{int(event.amount) / 1e18:.4f} WETH</b>."
    )


# =============================================================================
# Cron Jobs
# =============================================================================


@bot.cron("0 * * * *")  # Top of every hour
async def notify_ending_soon(_: datetime) -> None:
    # await notify_group_chat(f"🟢 🐙 STILL ALIVE")

    now_s = int(datetime.now(tz=timezone.utc).timestamp())

    state = load_state()
    auction_end_times = state.get("auction_end_times", {})
    if not auction_end_times:
        return

    to_remove = []
    for auction_id, end_time in auction_end_times.items():
        if 0 < (end_time - now_s) <= 2 * 60 * 60:  # 2 hours
            minutes_left = (end_time - now_s) // 60
            await notify_group_chat(f"⏰ <b>Auction {auction_id}</b> is ending soon (<b>~{minutes_left}m</b> left).")
            to_remove.append(auction_id)

    # remove auctions we just notified about
    for auction_id in to_remove:
        auction_end_times.pop(auction_id, None)


# =============================================================================
# Helpers
# =============================================================================


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r") as f:
            return cast(Dict[str, Any], json.load(f))
    except FileNotFoundError:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)
