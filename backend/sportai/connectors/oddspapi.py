"""OddsPapi (https://oddspapi.io) — French sites' odds the other feeds lack (owner 2026-11-03).

The Odds API gives the French sites only the 1X2, goals (PMU, NetBet) and both teams score (NetBet). OddsPapi,
tested 2026-11-03 on the Champions League, gives Winamax FR, PMU, Bet365 FR, Bwin FR and PokerStars FR with 1X2,
double chance, draw no bet, both teams score and goals (corners and cards only at Bet365 FR). Betclic FR is refused
on the free plan; Unibet FR, FDJ, ZEbet and NetBet FR are listed but had no odds.

Economy mode (owner's card 2026-11-03, free plan = 250 requests a month): one /odds request per match, only at the
last pass before kick-off (runs.FRENCH_EXTRA_WITHIN), at most ODDSPAPI_DAILY_CAP matches a day and
ODDSPAPI_MONTH_CAP a month, plus one /fixtures request a day. Every request is written to the ledger
state/oddspapi_ledger.json, so a match is never asked twice the same day. ODDSPAPI=0 switches it off.

Quotes come out with bookmaker keys ending in "_fr" (winamax_fr, bet365_fr...), so the_odds_api.is_french and the
site's blue dot treat them as French sites. Endpoints (v4): /fixtures?sportId=10&from&to, /odds?fixtureId&bookmakers,
/markets (ids below, read once from it on 2026-11-03).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import unicodedata
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sportai.connectors.base import OddsQuote
from sportai.connectors.teams import normalise

BASE = "https://api.oddspapi.io/v4"
NAME = "oddspapi"
LEDGER = Path(os.getenv("ODDSPAPI_LEDGER", "/mnt/project-files/agent-sportif/state/oddspapi_ledger.json"))
CACHE = Path(os.getenv("ODDSPAPI_CACHE", "/mnt/project-files/agent-sportif/state/oddspapi"))
DAILY_CAP = int(os.getenv("ODDSPAPI_DAILY_CAP", "45"))
# owner 2026-11-04: the French odds at every pass (morning, 3 h, 45 min) to follow their trend. The earlier passes
# read only while more than EARLY_RESERVE requests are left today, so the last pass before kick-off keeps its own.
EARLY = os.getenv("ODDSPAPI_EARLY", "1") != "0"
EARLY_RESERVE = int(os.getenv("ODDSPAPI_EARLY_RESERVE", "15"))
REASK = timedelta(minutes=60)  # a match read less than an hour ago is served from the cache
MONTH_CAP = int(os.getenv("ODDSPAPI_MONTH_CAP", "225"))  # 250 free, 13 spent on the 2026-11-03 test, a margin kept
# OddsPapi slug -> our French key. betclic.fr refused on the free plan (403 RESTRICTED_ACCESS for the whole request).
FRENCH_BOOKS = {"winamax.fr": "winamax_fr", "pmu": "pmu_fr", "bet365.fr": "bet365_fr", "bwin.fr": "bwin_fr",
                "pokerstars.fr": "pokerstars_fr", "unibet.fr": "unibet_fr", "fdj": "fdj_fr", "zebet.fr": "zebet_fr",
                "netbet.fr": "netbet_fr"}
# Full-time markets of /markets (sportId 10): id -> (market, {outcomeId: selection}).
MARKETS = {
    101: ("1x2", {101: "home", 102: "draw", 103: "away"}),
    104: ("btts", {104: "yes", 105: "no"}),
}


def enabled() -> bool:
    return bool(os.getenv("ODDSPAPI_KEY")) and os.getenv("ODDSPAPI", "1") != "0"


def _get(path: str, **params) -> object:
    params["apiKey"] = os.environ["ODDSPAPI_KEY"]
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:  # "wait 1.97 seconds" between two calls of one endpoint
                time.sleep(2.5)
                continue
            raise
    raise RuntimeError("unreachable")


def _ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except (OSError, ValueError):
        return {"requests": []}


def _spend(kind: str, ref: str, now: datetime) -> None:
    led = _ledger()
    led["requests"].append({"at": now.isoformat(), "kind": kind, "ref": ref})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=0))


def budget_left(now: datetime) -> tuple[int, int]:
    """(odds requests left today, requests left this month), from the ledger."""
    reqs = _ledger()["requests"]
    day, month = now.date().isoformat(), now.strftime("%Y-%m")
    today = sum(1 for r in reqs if r["kind"] == "odds" and r["at"][:10] == day)
    this_month = sum(1 for r in reqs if r["at"][:7] == month)
    return DAILY_CAP - today, MONTH_CAP - this_month


def already_asked(ref: str, now: datetime) -> bool:
    """Read less than REASK ago (the same pass run twice, or two competitions' steps at once)."""
    for r in _ledger()["requests"]:
        if r["kind"] == "odds" and r["ref"] == ref:
            try:
                if now - datetime.fromisoformat(r["at"]) < REASK:
                    return True
            except ValueError:
                continue
    return False


def day_fixtures(now: datetime) -> list[dict]:
    """The day's football fixtures with odds (one request a day, cached in state/oddspapi/fixtures_<day>.json)."""
    day = now.date().isoformat()
    path = CACHE / f"fixtures_{day}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    if budget_left(now)[1] <= 0:
        return []
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    data = _get("/fixtures", sportId=10, hasOdds="true", **{"from": (start + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                          "to": (start + timedelta(hours=28)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    _spend("fixtures", day, now)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))
    return data if isinstance(data, list) else []


def _words(name: str) -> set[str]:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return {w for w in re.split(r"[^a-z0-9]+", s) if len(w) >= 4}


def _similar(a: str, b: str) -> float:
    """1.0 for the same club ("Inter" / "Inter Milano", "Bodo/Glimt" / "Bodoe/Glimt"), lower for others."""
    na, nb = normalise(a), normalise(b)
    if na == nb or (na and nb and (na in nb or nb in na)) or (_words(a) & _words(b)):
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def find_fixture(fixtures: list[dict], home: str, away: str, kickoff: datetime) -> dict | None:
    """The OddsPapi fixture of the same match: same kick-off (30 min) and both names alike."""
    best, score = None, 0.0
    for fx in fixtures:
        try:
            ko = datetime.fromisoformat(fx["startTime"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if abs(ko - kickoff) > timedelta(minutes=30):
            continue
        s = min(_similar(home, fx.get("participant1Name", "")), _similar(away, fx.get("participant2Name", "")))
        if s > score:
            best, score = fx, s
    return best if score >= 0.6 else None


def parse(data: dict, home: str, away: str, kickoff: datetime, event_id: str, now: datetime,
          validity: timedelta = timedelta(minutes=45)) -> list[OddsQuote]:
    """French sites' full-time 1X2, double chance, draw no bet, both teams score and goals as OddsQuotes."""
    out: list[OddsQuote] = []

    def add(book: str, market: str, selection: str, line: float | None, price, changed: str | None):
        try:
            odds = float(price)
        except (TypeError, ValueError):
            return
        observed = now
        if changed:
            try:
                observed = datetime.fromisoformat(changed.replace("Z", "+00:00"))
            except ValueError:
                pass
        out.append(OddsQuote(provider=NAME, bookmaker=book, provider_event_id=event_id, home=home, away=away,
                             kickoff=kickoff, market=market, selection=selection, line=line, odds=odds,
                             observed_at=observed, mode="real", expires_at=now + validity,
                             validation_status="valid" if odds > 1.0 else "rejected"))

    for slug, bk in (data.get("bookmakerOdds") or {}).items():
        book = FRENCH_BOOKS.get(slug)
        if not book or not bk.get("bookmakerIsActive", True) or bk.get("suspended"):
            continue
        for mid, m in (bk.get("markets") or {}).items():
            spec = MARKET_SPECS.get(int(mid)) if str(mid).isdigit() else None
            if not spec:
                continue
            market, outcomes, line = spec
            for oid, o in (m.get("outcomes") or {}).items():
                sel = outcomes.get(int(oid)) if str(oid).isdigit() else None
                p = ((o.get("players") or {}).get("0")) or {}
                if sel and p.get("active", True):
                    add(book, market, sel, line, p.get("price"), p.get("changedAt"))
    return out


def load_market_specs(path: Path | None = None) -> dict[int, tuple[str, dict[int, str], float | None]]:
    """Full-time football market ids, from the /markets reply of 2026-11-03 cut to these markets
    (oddspapi_markets.json beside this file): 1X2, double chance, draw no bet, both teams score, every goals line."""
    path = path or Path(__file__).with_name("oddspapi_markets.json")
    try:
        rows = json.loads(path.read_text())
    except (OSError, ValueError):
        return {k: (v[0], v[1], None) for k, v in MARKETS.items()}
    names = {"1": "home", "X": "draw", "2": "away", "Yes": "yes", "No": "no", "Over": "over", "Under": "under",
             "1X": "1X", "2X": "X2", "X2": "X2", "12": "12"}
    kinds = {"1x2": "1x2", "doublechance": "double_chance", "drawnobet": "dnb", "bothteamsscore": "btts",
             "totals": "totals"}
    out = {}
    for m in rows:
        if m.get("sportId") != 10 or m.get("period") != "fulltime" or m.get("playerProp"):
            continue
        kind = kinds.get(m.get("marketType"))
        if not kind:
            continue
        sels = {o["outcomeId"]: names[o["outcomeName"]] for o in m.get("outcomes", []) if o.get("outcomeName") in names}
        if sels:
            out[m["marketId"]] = (kind, sels, float(m.get("handicap") or 0) if kind == "totals" else None)
    return out


MARKET_SPECS = load_market_specs()


def french_quotes(fixtures, competition: str, now: datetime, within: timedelta) -> list[OddsQuote]:
    """French sites' quotes for the matches starting within `within` (the last pass), inside the budget."""
    if not enabled():
        return []
    last = [f for f in fixtures if f.status == "scheduled" and now < f.kickoff <= now + within]
    early = [f for f in fixtures if EARLY and f.status == "scheduled" and f.kickoff > now + within
             and f.kickoff <= now + timedelta(hours=20)] if EARLY else []
    todo = last + sorted(early, key=lambda f: f.kickoff)
    if not todo:
        return []
    try:
        listed = day_fixtures(now)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"OddsPapi fixtures: {str(e)[:120]}", file=sys.stderr)
        return []
    out: list[OddsQuote] = []
    for f in todo:
        fx = find_fixture(listed, f.home, f.away, f.kickoff)
        if fx is None:
            print(f"OddsPapi: {f.home} - {f.away} not found", file=sys.stderr)
            continue
        cache = CACHE / f"odds_{fx['fixtureId']}_{now.date().isoformat()}.json"
        data = None
        if already_asked(fx["fixtureId"], now):
            try:
                data = json.loads(cache.read_text())
            except (OSError, ValueError):
                continue
        else:
            day_left, month_left = budget_left(now)
            reserve = EARLY_RESERVE if f not in last else 0
            if day_left <= reserve or month_left <= reserve:
                print(f"OddsPapi budget spent (day {day_left}, month {month_left}): {f.home} - {f.away} skipped",
                      file=sys.stderr)
                continue
            try:
                data = _get("/odds", fixtureId=fx["fixtureId"], bookmakers=",".join(FRENCH_BOOKS_ALLOWED))
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"OddsPapi odds {f.home} - {f.away}: {str(e)[:120]}", file=sys.stderr)
                continue
            finally:
                _spend("odds", fx["fixtureId"], now)
            CACHE.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False))
        out += parse(data, f.home, f.away, f.kickoff, f.provider_event_id, now)
    return out


# The free plan refuses the whole request when it names Betclic FR: ask only the others.
FRENCH_BOOKS_ALLOWED = [b for b in FRENCH_BOOKS if b != "betclic.fr"]
