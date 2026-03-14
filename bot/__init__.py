try:
    from .bot import bot as bot
except Exception:
    # Allow importing submodules (e.g., bot.db) in test environments where
    # ape is not connected or Telegram env vars are not set.
    # bot.bot imports bot.tg which raises RuntimeError if BOT_ACCESS_TOKEN is missing,
    # and bot.config requires an active Ape network provider.
    pass
