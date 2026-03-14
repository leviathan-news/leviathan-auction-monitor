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
