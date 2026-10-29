"""Download ATP and WTA singles history (results, match stats, odds) from api-tennis.com.

One gzip file per week and tour in /mnt/project-files/agent-sportif/data/api-tennis/:
  fixtures_<tour>_<monday>.json.gz  matches without the point-by-point (scores per set, serve stats incl. aces)
  odds_<tour>_<monday>.json.gz      odds per match, only the markets we use (winner, sets, games, handicap, aces)
plus tournaments.json.gz (surface of each tournament). Past weeks are fetched once; the current week is refreshed.
The key comes from API_TENNIS_KEY and is never printed.

Usage: python scripts/fetch_tennis.py --since 2023-01-02 [--challengers]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

CACHE = Path("/mnt/project-files/agent-sportif/data/api-tennis")
URL = "https://api.api-tennis.com/tennis/"
TOURS = {"ATP": 265, "WTA": 266}
# Challenger singles: results only, to rate the players who move between the two levels (no odds needed).
CHALLENGERS = {"CHM": 281, "CHW": 272}
KEEP_MARKETS = ("Home/Away", "Number of sets", "Set Betting", "1x2 Aces", "Over/Under", "Handicap", "Total")


def call(method: str, **params) -> dict:
    for attempt in range(4):
        try:
            r = httpx.get(URL, params={"method": method, "APIkey": os.environ["API_TENNIS_KEY"], **params}, timeout=120)
            r.raise_for_status()
            d = r.json()
            if d.get("success") == 1:
                return d
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"api-tennis {method} failed")


def save(path: Path, data) -> None:
    with gzip.open(path, "wt") as f:
        json.dump(data, f)


def week(tour: str, monday: date, refresh: bool, key: int, with_odds: bool = True) -> None:
    sunday = monday + timedelta(days=6)
    fx_path = CACHE / f"fixtures_{tour}_{monday}.json.gz"
    od_path = CACHE / f"odds_{tour}_{monday}.json.gz"
    if fx_path.exists() and (od_path.exists() or not with_odds) and not refresh:
        return
    span = {"date_start": str(monday), "date_stop": str(sunday), "event_type_key": key}
    fixtures = call("get_fixtures", **span).get("result") or []
    for m in fixtures:
        m.pop("pointbypoint", None)
        m.pop("event_first_player_logo", None)
        m.pop("event_second_player_logo", None)
    save(fx_path, fixtures)
    if not with_odds:
        return
    odds = call("get_odds", **span).get("result") or {}
    if isinstance(odds, dict):
        odds = {k: {m: v for m, v in mk.items() if m.startswith(KEEP_MARKETS)} for k, mk in odds.items()}
    save(od_path, odds)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2023-01-02")
    ap.add_argument("--challengers", action="store_true", help="Challenger results instead of the main tours")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    if not args.challengers:
        save(CACHE / "tournaments.json.gz", call("get_tournaments").get("result") or [])
    tours = CHALLENGERS if args.challengers else TOURS
    start = date.fromisoformat(args.since)
    start -= timedelta(days=start.weekday())
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    d = start
    while d <= this_monday:
        for tour, key in tours.items():
            week(tour, d, d >= this_monday - timedelta(days=7), key, with_odds=not args.challengers)
        print(d, flush=True)
        d += timedelta(days=7)


if __name__ == "__main__":
    main()
