"""Top 5 in real time (owner 2026-11-08 13:39Z: « mets à jour en temps réel, le match est déjà terminé »).

Every few minutes, for each position of top5/<jour> (every actor, every sport): live status (a_venir / en_cours /
termine) and score from the data feeds, and the result as soon as the match is over, settled here from the final
score without waiting for each actor's own pass:
- football: API-Football fixtures of the day (90-minute score) and, for corners / cards, the match statistics;
- tennis: api-tennis fixtures of the day (sets and games);
- basketball: API-Basketball games of the day (final score, overtime included).
A result already written by the actor's own source is never changed.

Usage: top5_live.py --jour D --top5 CUR.json --out OUT.json [--boucle MINUTES]
With --boucle, it polls every 3 minutes and exits (code 0) as soon as a result or a status changed, so the caller
can write the doc, or when time is up.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LOOP_S = 180
FOOT_LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}
FOOT_DONE = {"FT", "AET", "PEN"}
FOOT_VOID = {"PST", "CANC", "ABD", "AWD", "WO"}


def _get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _n(s: str) -> float:
    return float(s.replace(",", "."))


def _key(match: str) -> str:
    return re.sub(r"\s+", " ", match or "").strip().lower()


# ---------------------------------------------------------------- feeds
def foot_fixtures(jour: str) -> dict[str, dict]:
    h = {"x-apisports-key": os.environ["API_FOOTBALL_KEY"]}
    d = _get(f"https://v3.football.api-sports.io/fixtures?date={jour}&timezone=Europe/Paris", h).get("response") or []
    return {_key(f"{f['teams']['home']['name']} - {f['teams']['away']['name']}"): f for f in d}


def foot_stats(fid: int) -> list[dict]:
    h = {"x-apisports-key": os.environ["API_FOOTBALL_KEY"]}
    rows = _get(f"https://v3.football.api-sports.io/fixtures/statistics?fixture={fid}", h).get("response") or []
    out = []
    for r in rows:
        st = {s["type"]: s["value"] for s in r.get("statistics") or []}
        out.append({"corners": st.get("Corner Kicks") or 0,
                    "cartons": (st.get("Yellow Cards") or 0) + (st.get("Red Cards") or 0), "ok": bool(st)})
    return out


def tennis_fixtures(jour: str) -> dict[str, dict]:
    q = urllib.parse.urlencode({"method": "get_fixtures", "APIkey": os.environ["API_TENNIS_KEY"], "date_start": jour,
                                "date_stop": jour, "timezone": "Europe/Paris"})
    r = _get(f"https://api.api-tennis.com/tennis/?{q}").get("result") or []
    return {_key(f"{e.get('event_first_player')} - {e.get('event_second_player')}"): e for e in r}


def basket_games(jour: str) -> dict[str, dict]:
    from scripts.agent_pass import _basket_api
    gs = _basket_api().games_on(["NBA"], date.fromisoformat(jour), ttl=0)
    return {_key(f"{g['teams']['home']['name']} - {g['teams']['away']['name']}"): g for g in gs}


# ---------------------------------------------------------------- settling from labels
def _ou(over: bool, value: float, line: float) -> str:
    if value == line:
        return "rembourse"
    return "gagne" if (value > line) == over else "perdu"


def _asian(diff: float) -> str:
    """diff = my goals + handicap - other goals; quarter lines split in two halves."""
    frac = abs(diff) % 1
    if abs(frac - 0.25) < 1e-9 or abs(frac - 0.75) < 1e-9:
        a, b = _asian(diff - 0.25), _asian(diff + 0.25)
        if a == b:
            return a
        return "gagne" if "gagne" in (a, b) and "perdu" not in (a, b) else \
            "perdu" if "perdu" in (a, b) and "gagne" not in (a, b) else "rembourse"
    return "gagne" if diff > 0 else "perdu" if diff < 0 else "rembourse"


def settle_foot(pari: str, home: str, away: str, h: int, a: int, stats=None) -> str | None:
    p = pari.strip()
    side = lambda name: (h, a) if name == home else (a, h) if name == away else (None, None)  # noqa: E731
    m = re.fullmatch(r"(Corners|Cartons)(?: (.+?))? : (plus|moins) de ([\d,]+)(?: dans le match)?", p)
    if m:
        if not stats or len(stats) != 2 or not all(s["ok"] for s in stats):
            return None
        k = "corners" if m.group(1) == "Corners" else "cartons"
        team = m.group(2)
        v = stats[0][k] + stats[1][k] if not team else stats[0][k] if team == home else stats[1][k] if team == away else None
        return None if v is None else _ou(m.group(3) == "plus", v, _n(m.group(4)))
    m = re.fullmatch(r"(.+) ou nul et (plus|moins) de ([\d,]+) buts?", p)
    if m:
        mine, other = side(m.group(1))
        if mine is None:
            return None
        return "gagne" if mine >= other and _ou(m.group(2) == "plus", h + a, _n(m.group(3))) == "gagne" else "perdu"
    m = re.fullmatch(r"(.+) et (plus|moins) de ([\d,]+) buts?", p)
    if m and m.group(1).startswith("Victoire "):
        mine, other = side(m.group(1)[9:])
        if mine is None:
            return None
        return "gagne" if mine > other and _ou(m.group(2) == "plus", h + a, _n(m.group(3))) == "gagne" else "perdu"
    m = re.fullmatch(r"(Plus|Moins) de ([\d,]+) buts?(?: dans le match)?", p)
    if m:
        return _ou(m.group(1) == "Plus", h + a, _n(m.group(2)))
    m = re.fullmatch(r"(.+) : (plus|moins) de ([\d,]+) buts?", p)
    if m:
        mine, _ = side(m.group(1))
        return None if mine is None else _ou(m.group(2) == "plus", mine, _n(m.group(3)))
    m = re.fullmatch(r"(.+) handicap(?: asiatique)? ([+-]?[\d,]+)(?: buts?)?", p)
    if m:
        mine, other = side(m.group(1))
        return None if mine is None else _asian(mine + _n(m.group(2)) - other)
    if p.startswith("Les deux équipes marquent"):
        return "gagne" if (h > 0 and a > 0) == p.endswith("oui") else "perdu"
    if p.startswith("Victoire "):
        mine, other = side(p[9:])
        return None if mine is None else ("gagne" if mine > other else "perdu")
    if p == "Match nul":
        return "gagne" if h == a else "perdu"
    m = re.fullmatch(r"(.+) ou nul", p)
    if m:
        mine, other = side(m.group(1))
        return None if mine is None else ("gagne" if mine >= other else "perdu")
    m = re.fullmatch(r"(.+) \(remboursé si nul\)", p)
    if m:
        mine, other = side(m.group(1).replace("Victoire ", ""))
        return None if mine is None else ("rembourse" if h == a else "gagne" if mine > other else "perdu")
    return None


def settle_tennis(pari: str, j1: str, j2: str, sets: list[tuple[int, int]]) -> str | None:
    p = pari.strip()
    s1 = sum(x > y for x, y in sets)
    s2 = len(sets) - s1
    g1, g2 = sum(x for x, _ in sets), sum(y for _, y in sets)
    who = lambda n: 1 if n == j1 else 2 if n == j2 else None  # noqa: E731
    m = re.fullmatch(r"(.+) gagne le match", p) or re.fullmatch(r"Victoire (.+)", p)
    if m:
        w = who(m.group(1))
        return None if not w else ("gagne" if (s1 > s2) == (w == 1) else "perdu")
    m = re.fullmatch(r"(.+) gagne au moins un set", p)
    if m:
        w = who(m.group(1))
        return None if not w else ("gagne" if (s1 if w == 1 else s2) >= 1 else "perdu")
    m = re.fullmatch(r"(.+) gagne (\d)[-–](\d)(?: en sets)?", p)
    if m:
        w = who(m.group(1))
        if not w:
            return None
        mine, other = (s1, s2) if w == 1 else (s2, s1)
        return "gagne" if (mine, other) == (int(m.group(2)), int(m.group(3))) else "perdu"
    m = re.fullmatch(r"(.+) handicap ([+-]?[\d.,]+) jeux", p)
    if m:
        w = who(m.group(1))
        if not w:
            return None
        d = (g1 - g2 if w == 1 else g2 - g1) + _n(m.group(2))
        return "gagne" if d > 0 else "perdu" if d < 0 else "rembourse"
    m = re.fullmatch(r"(Plus|Moins) de ([\d.,]+) jeux(?: dans le match)?", p)
    if m:
        return _ou(m.group(1) == "Plus", g1 + g2, _n(m.group(2)))
    return None


def settle_basket(pari: str, home: str, away: str, h: int, a: int) -> str | None:
    p = pari.strip()
    side = lambda name: (h, a) if name == home else (a, h) if name == away else (None, None)  # noqa: E731
    m = re.fullmatch(r"(Plus|Moins) de ([\d,]+) points dans le match", p)
    if m:
        return _ou(m.group(1) == "Plus", h + a, _n(m.group(2)))
    m = re.fullmatch(r"(.+) : (plus|moins) de ([\d,]+) points", p)
    if m:
        mine, _ = side(m.group(1))
        return None if mine is None else _ou(m.group(2) == "plus", mine, _n(m.group(3)))
    m = re.fullmatch(r"(.+) handicap ([+-]?[\d,]+) points", p) or re.fullmatch(r"(.+) ([+-][\d,]+) points \(handicap\)", p)
    if m:
        mine, other = side(m.group(1))
        if mine is None:
            return None
        d = mine + _n(m.group(2)) - other
        return "gagne" if d > 0 else "perdu" if d < 0 else "rembourse"
    if p.startswith("Victoire "):
        mine, other = side(p[9:])
        return None if mine is None else ("gagne" if mine > other else "perdu")
    return None


# ---------------------------------------------------------------- one pass
def _sets(e: dict) -> list[tuple[int, int]]:
    out = []
    for s in e.get("scores") or []:
        try:
            out.append((int(float(s["score_first"])), int(float(s["score_second"]))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def update(doc: dict, jour: str, feeds: dict | None = None) -> bool:
    feeds = feeds if feeds is not None else {}
    changed = False
    stats_cache: dict[int, list] = {}
    for sport in ("foot", "tennis", "basket"):
        actors = doc.get(sport) or {}
        todo = [p for v in actors.values() for p in v.get("positions") or [] if p.get("resultat") is None]
        if not todo:
            continue
        try:
            if sport not in feeds:
                feeds[sport] = {"foot": foot_fixtures, "tennis": tennis_fixtures, "basket": basket_games}[sport](jour)
                if sport == "basket":  # late NBA games are listed on the next UTC date too: games_on covers it
                    pass
        except Exception as ex:  # noqa: BLE001
            print(f"{sport}: feed unavailable ({str(ex)[:120]})", flush=True)
            continue
        fx = feeds[sport]
        for p in todo:
            f = fx.get(_key(p.get("match")))
            if not f:
                continue
            before = (p.get("statut"), p.get("score"), p.get("resultat"))
            home, away = [x.strip() for x in p["match"].split(" - ", 1)]
            if sport == "foot":
                st = f["fixture"]["status"]["short"]
                g = f["goals"]
                if st in FOOT_LIVE:
                    p["statut"], p["score"] = "en_cours", f"{g['home']}-{g['away']} ({f['fixture']['status'].get('elapsed')}')"
                elif st in FOOT_DONE:
                    ft = f["score"]["fulltime"]
                    h, a = ft["home"], ft["away"]
                    p["statut"], p["score"] = "termine", f"{h}-{a}"
                    stats = None
                    if re.match(r"(Corners|Cartons)", p["pari"]):
                        fid = f["fixture"]["id"]
                        if fid not in stats_cache:
                            try:
                                stats_cache[fid] = foot_stats(fid)
                            except Exception:  # noqa: BLE001
                                stats_cache[fid] = None
                        stats = stats_cache[fid]
                    r = settle_foot(p["pari"], home, away, h, a, stats)
                    if r:
                        p["resultat"] = r
                elif st in FOOT_VOID:
                    p["statut"], p["resultat"] = "termine", "rembourse"
            elif sport == "tennis":
                st = str(f.get("event_status") or "")
                sets = _sets(f)
                sc = " ".join(f"{x}-{y}" for x, y in sets)
                if st == "Finished":
                    p["statut"], p["score"] = "termine", sc
                    r = settle_tennis(p["pari"], home, away, sets)
                    if r:
                        p["resultat"] = r
                elif st in ("Retired", "Walk Over", "Cancelled"):
                    p["statut"], p["resultat"], p["score"] = "termine", "rembourse", sc
                elif st.startswith("Set") or f.get("event_live") == "1":
                    p["statut"], p["score"] = "en_cours", sc
            else:
                st = (f.get("status") or {}).get("short")
                s = f.get("scores") or {}
                h, a = (s.get("home") or {}).get("total"), (s.get("away") or {}).get("total")
                if st in ("FT", "AOT") and h is not None:
                    p["statut"], p["score"] = "termine", f"{h}-{a}"
                    r = settle_basket(p["pari"], home, away, h, a)
                    if r:
                        p["resultat"] = r
                elif st in ("Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT") and h is not None:
                    p["statut"], p["score"] = "en_cours", f"{h}-{a} ({st})"
                elif st in ("POST", "CANC", "ABD", "AWD"):
                    p["statut"], p["resultat"] = "termine", "rembourse"
            if (p.get("statut"), p.get("score"), p.get("resultat")) != before:
                changed = True
    for sport in ("foot", "tennis", "basket"):
        for v in (doc.get(sport) or {}).values():
            ps = v.get("positions") or []
            g = sum(x.get("resultat") == "gagne" for x in ps)
            l = sum(x.get("resultat") == "perdu" for x in ps)
            r = sum(x.get("resultat") == "rembourse" for x in ps)
            v["ratio"] = {"gagnees": g, "perdues": l, "remboursees": r, "jouees": g + l}
    if changed:
        doc["maj"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return changed


def _important(old: dict, new: dict) -> bool:
    """A result or a status change (not a mere score tick) is worth writing at once."""
    def sig(d):
        return [(s, a, p.get("match_id"), p.get("statut"), p.get("resultat")) for s in ("foot", "tennis", "basket")
                for a, v in (d.get(s) or {}).items() for p in v.get("positions") or []]
    return sig(old) != sig(new)


def _results(d: dict) -> list:
    return [p.get("resultat") for s in ("foot", "tennis", "basket") for v in (d.get(s) or {}).values()
            for p in v.get("positions") or []]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jour", required=True)
    ap.add_argument("--top5", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--boucle", type=float, default=0)
    a = ap.parse_args()
    d = json.loads(Path(a.top5).read_text())
    doc = d.get("data", d) if isinstance(d.get("data"), dict) else d
    start = time.time()
    end = start + a.boucle * 60
    base = json.loads(json.dumps(doc))
    while True:
        old = json.loads(json.dumps(doc))
        try:
            update(doc, a.jour)
        except Exception as ex:  # noqa: BLE001
            print("update:", ex, flush=True)
        Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        # a result is written at once; a mere status change (match started) waits up to 15 minutes, gathered
        imp = _results(old) != _results(doc) or (_important(base, doc) and time.time() - start >= 900) \
            or (not a.boucle and _important(old, doc))
        if not a.boucle or imp or time.time() + LOOP_S > end:
            imp = imp or _important(base, doc)
            print("changement" if imp else "rien de neuf", flush=True)
            return
        time.sleep(LOOP_S)


if __name__ == "__main__":
    main()
