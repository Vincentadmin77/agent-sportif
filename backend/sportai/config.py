"""Runtime configuration from environment variables. No secret has a default."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_mode: str  # "simulation" or "real"
    the_odds_api_key: str | None
    api_football_key: str | None
    odds_regions: str
    odds_bookmakers: list[str] | None
    shopify_webhook_secret: str | None
    cookie_secure: bool
    session_days: int
    anthropic_api_key: str | None
    agent_budget_usd_per_run: float


@lru_cache
def get_settings() -> Settings:
    mode = os.getenv("DATA_MODE", "simulation")
    if mode not in ("simulation", "real"):
        raise ValueError("DATA_MODE must be 'simulation' or 'real'")
    books = os.getenv("ODDS_BOOKMAKERS")
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./sportai-dev.db"),
        data_mode=mode,
        the_odds_api_key=os.getenv("THE_ODDS_API_KEY") or None,
        api_football_key=os.getenv("API_FOOTBALL_KEY") or None,
        odds_regions=os.getenv("ODDS_REGIONS", "fr"),
        odds_bookmakers=[b.strip() for b in books.split(",")] if books else None,
        shopify_webhook_secret=os.getenv("SHOPIFY_WEBHOOK_SECRET") or None,
        cookie_secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        session_days=int(os.getenv("SESSION_DAYS", "14")),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        agent_budget_usd_per_run=float(os.getenv("AGENT_BUDGET_USD_PER_RUN", "0.50")),
    )
