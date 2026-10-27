"""Download per-player box scores of finished NBA games from API-Basketball.

One request per game (/games/statistics/players?id=...). Results are kept per
season in /mnt/project-files/agent-sportif/data/api-basketball/players_nba_<season>.json.gz
as {game_id: [player rows]}; games already in the file are never downloaded
again, and the file is rewritten every 200 games so an interrupted run resumes.

The key comes from API_FOOTBALL_KEY (same API-Sports account) and is never
printed. A hard budget (--budget, default 5000) and the provider's remaining
daily quota (response header) are checked before each call.

Usage: python scripts/fetch_nba_players.py 2023-2024 2024-2025 2025-2026 2026-2027
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from pathlib import Path

import httpx

CACHE = Path("/mnt/project-files/agent-sportif/data/api-basketball")
BASE = "https://v1.basketball.api-sports.io"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("seasons", nargs="+")
    ap.add_argument("--budget", type=int, default=5000)
    a = ap.parse_args()
    client = httpx.Client(base_url=BASE, headers={"x-apisports-key": os.environ["API_FOOTBALL_KEY"]}, timeout=30)
    spent = 0
    for season in a.seasons:
        games = json.load(gzip.open(CACHE / f"games_nba_{season}.json.gz", "rt"))["response"]
        ids = [g["id"] for g in games if g["status"]["short"] in ("FT", "AOT")]
        path = CACHE / f"players_nba_{season}.json.gz"
        store = json.load(gzip.open(path, "rt")) if path.exists() else {}
        todo = [i for i in ids if str(i) not in store]
        print(season, len(ids), "games,", len(todo), "to fetch", flush=True)
        for n, gid in enumerate(todo, 1):
            if spent >= a.budget:
                break
            try:
                r = client.get("/games/statistics/players", params={"id": gid})
            except httpx.TransportError as e:
                print("network error", gid, type(e).__name__, flush=True)
                time.sleep(10)
                continue
            spent += 1
            left = int(r.headers.get("x-ratelimit-requests-remaining", "9999"))
            data = r.json()
            if data.get("errors"):
                print("error", gid, data["errors"], flush=True)
                time.sleep(5)
                continue
            store[str(gid)] = data["response"]
            if n % 200 == 0 or n == len(todo):
                with gzip.open(path, "wt") as f:
                    json.dump(store, f)
                print(season, n, "/", len(todo), "quota left", left, flush=True)
            if left < 300:
                print("daily quota nearly spent, stopping", flush=True)
                a.budget = spent
            time.sleep(0.25)
        with gzip.open(path, "wt") as f:
            json.dump(store, f)
    print("requests spent", spent)


if __name__ == "__main__":
    main()
