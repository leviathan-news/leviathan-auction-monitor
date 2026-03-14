try:
    from .bot import bot as bot
except ImportError:
    # Allow importing submodules (e.g., bot.db) without ape installed (test environments)
    pass
