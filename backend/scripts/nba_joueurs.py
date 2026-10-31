"""NBA player bets of a Paris day: refresh the box scores, price every quoted player line, keep the singles that
pass football's current rules (odds 1.50 to 2.30, value >= 3 %, model within 10 points of the bookmakers).
All "non validé". Writes /mnt/project-files/agent-sportif/nba/joueurs-<date>.json.

Usage: python scripts/nba_joueurs.py --date 2026-10-29
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.services import basket_players as B  # noqa: E402

MIN_ODDS, MAX_ODDS, MIN_VALUE, MAX_GAP = 1.50, 2.30, 0.03, 0.10
SEASONS = ["2024-2025", "2025-2026", "2026-2027"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    key = os.environ["API_FOOTBALL_KEY"]
    cl = httpx.Client(base_url=B.BASE, headers={"x-apisports-key": key}, timeout=30)
    games = {s: json.load(gzip.open(B.CACHE / f"games_nba_{s}.json.gz", "rt"))["response"] for s in SEASONS[:-1]}
    games[SEASONS[-1]] = cl.get("/games", params={"league": 12, "season": SEASONS[-1]}).json()["response"]
    B.refresh(SEASONS[-1], games[SEASONS[-1]], key)
    roster = B.Roster(SEASONS, games)
    day = cl.get("/games", params={"league": 12, "season": SEASONS[-1], "date": a.date,
                                   "timezone": "Europe/Paris"}).json()["response"]
    out = []
    for g in day:
        odds = cl.get("/odds", params={"game": g["id"]}).json().get("response") or []
        rows = B.price(roster, [g["teams"]["home"]["id"], g["teams"]["away"]["id"]],
                       B.parse_odds(odds[0]["bookmakers"] if odds else []))
        keep = [dict(r, pari=B.label(r), marche=B.MARKET_NAMES[r["marche_cle"].removeprefix("joueur_")], valide=False)
                for r in rows if MIN_ODDS <= r["cote_mediane"] <= MAX_ODDS and r["valeur_mediane"] >= MIN_VALUE
                and abs(r["proba"] - r["proba_marche"]) <= MAX_GAP]
        out.append({"id": g["id"], "heure_paris": g["date"][11:16], "domicile": g["teams"]["home"]["name"],
                    "exterieur": g["teams"]["away"]["name"], "lignes_cotees": len(rows), "retenus": keep})
    res = {"date": a.date, "genere": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matchs": out}
    path = Path(f"/mnt/project-files/agent-sportif/nba/joueurs-{a.date}.json")
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    for m in out:
        print(m["heure_paris"], m["domicile"], "-", m["exterieur"], m["lignes_cotees"], "lignes")
        for r in m["retenus"][:3]:
            print("   ", r["pari"], r["cote_mediane"], "p", r["proba"], "bk", r["proba_marche"], "attendu", r["attendu"])


if __name__ == "__main__":
    main()
