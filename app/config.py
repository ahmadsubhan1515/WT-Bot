from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _parse_owner_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


@dataclass(frozen=True)
class Settings:
    discord_token: str
    guild_id: int
    owner_ids: set[int]
    dashboard_password: str
    session_secret: str
    host: str
    port: int
    database_url: str


@lru_cache
def get_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")

    gid = int(os.getenv("DISCORD_GUILD_ID", "0"))
    if gid <= 0:
        raise RuntimeError("DISCORD_GUILD_ID must be a positive integer")

    pwd = os.getenv("DASHBOARD_PASSWORD", "").strip()
    if not pwd:
        raise RuntimeError("DASHBOARD_PASSWORD is required for dashboard access")

    secret = os.getenv("SESSION_SECRET", "").strip()
    if len(secret) < 16:
        raise RuntimeError("SESSION_SECRET must be at least 16 characters")

    db = os.getenv("DATABASE_URL", "").strip()
    if not db:
        db = "sqlite+aiosqlite:///./data/warrior.db"
    elif db.startswith("postgres://"):
        db = "postgresql+asyncpg://" + db.removeprefix("postgres://")
    elif db.startswith("postgresql://"):
        db = "postgresql+asyncpg://" + db.removeprefix("postgresql://")

    return Settings(
        discord_token=token,
        guild_id=gid,
        owner_ids=_parse_owner_ids(os.getenv("DISCORD_OWNER_IDS")),
        dashboard_password=pwd,
        session_secret=secret,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        database_url=db,
    )
