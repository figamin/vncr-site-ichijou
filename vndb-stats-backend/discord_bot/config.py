"""Discord bot configuration."""

import os
from functools import lru_cache


class BotSettings:
    """Bot settings loaded from environment variables."""

    def __init__(self):
        self.token: str = os.environ.get("DISCORD_BOT_TOKEN", "")
        guild_id_str = os.environ.get("DISCORD_GUILD_ID", "").strip()
        self.guild_id: int = int(guild_id_str) if guild_id_str else 0

        # Parse comma-separated admin user ID list
        user_ids = os.environ.get("DISCORD_ADMIN_USER_IDS", "")
        self.admin_user_ids: list[int] = [
            int(x.strip()) for x in user_ids.split(",") if x.strip()
        ]

        # Frontend links. Treat an empty env value (the .env.example
        # placeholder) as unset: Discord requires absolute http(s) URLs for
        # link buttons, so frontend_url must never be "".
        frontend_url = os.environ.get("FRONTEND_URL", "").strip().rstrip("/")
        self.frontend_url: str = frontend_url or "https://vnclub.org"
        self.blacklist_refresh_secret: str = os.environ.get("BLACKLIST_REFRESH_SECRET", "")


@lru_cache()
def get_bot_settings() -> BotSettings:
    return BotSettings()
