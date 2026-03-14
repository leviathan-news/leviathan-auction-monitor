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
