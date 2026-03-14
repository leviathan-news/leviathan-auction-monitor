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
