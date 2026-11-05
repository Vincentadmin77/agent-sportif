"""A blank, tipster-style evening selection (owner 2026-11-05 07:11Z, decision card): at most 5 positions per evening,
only on matches that hold together, mostly same-match « équipe ou nul + buts » combos (also both teams score, over
1.5 first half, draw no bet, team + goals), total odds 1.60-2.00, high market chance. No straight wins. Own ratio,
never mixed with the model, the M or the specialists; compared with the Telegram tipster at the Monday bilan.

Prices and chances come only from the bookmakers' own quotes, never from our model:
- a quoted market: best odds of any bookmaker; market chance = each bookmaker's margin-free chance, median;
- « équipe ou nul + buts » is not quoted in the feed: it is the sum of two quoted outcomes of the same bookmaker
  (« équipe/plus de X » + « nul/plus de X »), so its price is the one obtained by covering both outcomes at that
  bookmaker (dutching): 1 / (1/o1 + 1/o2). Flagged cote_type « couverture de deux issues ».

    python scripts/selection_telegram.py passage --jour 2026-11-05 --ligues 3 --doc DOC.json --out DOC.json
    python scripts/selection_telegram.py regler --doc DOC.json --out DOC.json

DOC is the page's selection_telegram/<jour> document (absent the first time). Positions already frozen (kickoff
-45 min passed at a pass) are never changed. API_FOOTBALL_KEY in the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "https://v3.football.api-sports.io/"
MAX_PLACES = 5
ODDS_MIN, ODDS_MAX = 1.60, 2.00
MIN_CHANCE = 0.55  # both teams score, first-half goals, draw no bet
MIN_CHANCE_COMBO = 0.50  # same-match combos (team or draw + goals, team + goals)
FREEZE = timedelta(minutes=45)
REGLE_SPORT = {
    "tennis": ("Même méthode à part, sur les axes du tennis (vainqueur, score en sets, au moins un set, handicap "
               "jeux, total de jeux) : au plus 5 positions, une par match, cote 1,50-2,00, au moins 55 % de chance "
               "selon le marché, vainqueur et sets d'abord, les plus sûres d'abord. À blanc, ratio à part."),
    "basket": ("Même méthode à part, sur les axes du basket (total de points, handicap de points, points d'une "
               "équipe) : au plus 5 positions, une par match, cote 1,50-2,00, au moins 55 % de chance selon le "
               "marché, les plus sûres d'abord. À blanc, ratio à part."),
}
REGLE = ("Méthode à part (ni le modèle, ni le M) : au plus 5 positions par soir sur les matchs les plus cohérents, "
         "d'abord des combinés sur un même match (équipe ou nul + buts, équipe + buts) à au moins 50 % de chance "
         "selon le marché, sinon les deux équipes marquent, plus de 1,5 but en 1re mi-temps ou remboursé si nul à "
         "au moins 55 %, cote 1,60-2,00, pas de victoire sèche, équipes instables écartées d'après leurs 5 derniers "
         "matchs. À blanc, ratio à part.")


def _get(path: str, **q) -> dict:
    req = urllib.request.Request(API + path + "?" + urllib.parse.urlencode(q),
                                 headers={"x-apisports-key": os.environ["API_FOOTBALL_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if v > 1 else None
    except (TypeError, ValueError):
        return None


def _fmt(x: float) -> str:
    return f"{x:g}".replace(".", ",")


# ---- candidate markets: key -> (label, settle) -------------------------------------------------------------------
def _outcomes(bk: dict) -> dict:
    """One bookmaker's quotes as {(bet_id, value): odds}."""
    out = {}
    for b in bk.get("bets") or []:
        for v in b.get("values") or []:
            o = _num(v.get("odd"))
            if o:
                out[(b["id"], str(v.get("value")))] = o
    return out


def _fair(q: dict, keys: list) -> dict | None:
    """Margin-free chances of a complete set of outcomes of one bookmaker (None when one is missing)."""
    if not all(k in q for k in keys):
        return None
    s = sum(1 / q[k] for k in keys)
    return {k: (1 / q[k]) / s for k in keys}


def _candidates(q: dict, home: str, away: str) -> list[dict]:
    """All tipster-style options one bookmaker prices: {cle, pari, cote, chance} (chance may be None)."""
    c = []
    # both teams score, draw no bet, first-half goals
    f = _fair(q, [(8, "Yes"), (8, "No")])
    if f:
        c.append({"cle": "btts", "pari": "Les deux équipes marquent", "cote": q[(8, "Yes")], "chance": f[(8, "Yes")]})
    f = _fair(q, [(1, "Home"), (1, "Draw"), (1, "Away")])  # draw no bet: the chance to WIN (a draw only refunds)
    for side, team in (("Home", home), ("Away", away)):
        if (2, side) in q:
            c.append({"cle": f"dnb_{side[0].lower()}", "pari": f"{team} (remboursé si nul)", "cote": q[(2, side)],
                      "chance": f[(1, side)] if f else None})
    f = _fair(q, [(6, "Over 1.5"), (6, "Under 1.5")])
    if f:
        c.append({"cle": "ht_o1.5", "pari": "Plus de 1,5 but en 1re mi-temps", "cote": q[(6, "Over 1.5")],
                  "chance": f[(6, "Over 1.5")]})
    # result / total goals, per line; team-or-draw + goals by covering two outcomes of the same bookmaker
    for line in ("1.5", "2.5", "3.5", "4.5"):
        keys = [(25, f"{r}/{s} {line}") for r in ("Home", "Draw", "Away") for s in ("Over", "Under")]
        f = _fair(q, keys)
        for side in ("Over", "Under"):
            if line == "4.5" and side == "Over" or line == "1.5" and side == "Under":
                continue
            word = "plus" if side == "Over" else "moins"
            goals = f"{word} de {_fmt(float(line))} buts"
            for r, team in (("Home", home), ("Away", away)):
                k = (25, f"{r}/{side} {line}")
                if k in q:
                    c.append({"cle": f"{r[0].lower()}_{side[0].lower()}{line}", "pari": f"Victoire {team} et {goals}",
                              "cote": q[k], "chance": f[k] if f else None})
                kd = (25, f"Draw/{side} {line}")
                if k in q and kd in q:
                    cote = 1 / (1 / q[k] + 1 / q[kd])
                    c.append({"cle": f"{r[0].lower()}x_{side[0].lower()}{line}", "pari": f"{team} ou nul et {goals}",
                              "cote": round(cote, 3), "chance": (f[k] + f[kd]) if f else None,
                              "cote_type": "couverture de deux issues"})
    return c


def _settle(cle: str, h: int, a: int, hh: int | None, ha: int | None) -> str | None:
    t = h + a
    if cle == "btts":
        return "gagne" if h and a else "perdu"
    if cle in ("dnb_h", "dnb_a"):
        if h == a:
            return "rembourse"
        return "gagne" if (h > a) == (cle == "dnb_h") else "perdu"
    if cle == "ht_o1.5":
        return None if hh is None else ("gagne" if hh + ha >= 2 else "perdu")
    res, goals = cle.split("_")
    side, line = goals[0], float(goals[1:])
    ok_goals = t > line if side == "o" else t < line
    ok_res = {"h": h > a, "a": a > h, "hx": h >= a, "ax": a >= h}[res]
    return "gagne" if ok_goals and ok_res else "perdu"


def _form(team_id: int, before: str) -> str:
    """Last 5 results of a team before the day, newest first, as « V N D » letters."""
    rows = _get("fixtures", team=team_id, last=5, status="FT").get("response") or []
    out = []
    for r in rows:
        if r["fixture"]["date"][:10] >= before:
            continue
        g = r["goals"]
        mine, other = (g["home"], g["away"]) if r["teams"]["home"]["id"] == team_id else (g["away"], g["home"])
        out.append("V" if mine > other else "N" if mine == other else "D")
    return " ".join(out)


def _backed(cle: str) -> str | None:
    """Which team a candidate backs (h or a) and how: « win », « or draw », « draw no bet »; None for goals only."""
    if cle.startswith("dnb_"):
        return cle[4] + ":dnb"
    res = cle.split("_")[0]
    return {"h": "h:win", "a": "a:win", "hx": "h:draw", "ax": "a:draw"}.get(res)


def _form_ok(cle: str, fh: str, fa: str) -> bool:
    """Skip unstable teams (owner 2026-11-05): a win needs 3 wins in the last 5, « ou nul » at most 2 defeats,
    « remboursé si nul » 2 wins and at most 2 defeats."""
    b = _backed(cle)
    if not b:
        return True
    form = (fh if b[0] == "h" else fa).split()
    if len(form) < 5:
        return False
    w, l = form.count("V"), form.count("D")
    return {"win": w >= 3, "draw": l <= 2, "dnb": w >= 2 and l <= 2}[b[2:]]


def _is_combo(cle: str) -> bool:
    return cle not in ("btts", "ht_o1.5") and not cle.startswith("dnb_")


# ---- tennis and basket (owner 2026-11-05 07:17Z: the test selection per sport, like the M's) ----------------------
TENNIS = "https://api.api-tennis.com/tennis/"
BASKET = "https://v1.basketball.api-sports.io/"
ODDS_MIN_SPORT = {"foot": 1.60, "tennis": 1.50, "basket": 1.50}
FIRST = {"tennis": ("t_w", "t_s", "t_set")}  # tennis: winner and sets first (owner's 4/11 tipster example)
OUTLIER = 1.08  # best odds kept only within 8 % of the median of the sites (a lone stale quote is not a price)


def _get_url(base: str, path: str, headers: dict | None = None, **q) -> dict:
    req = urllib.request.Request(base + path + "?" + urllib.parse.urlencode(q), headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _pick(rows: list[tuple[float, float | None]]) -> tuple[float, float] | None:
    """rows = [(odds, margin-free chance or None) per site] -> (best sane odds, median chance)."""
    odds = [o for o, _ in rows]
    ch = [c for _, c in rows if c is not None]
    if not odds or not ch:
        return None
    med = statistics.median(odds)
    best = max(o for o in odds if o <= med * OUTLIER)
    return best, statistics.median(ch)


def _tennis_candidates(odds: dict, j1: str, j2: str) -> dict[str, dict]:
    """cle -> {pari, rows:[(odds, chance)]} from one api-tennis match odds block (Home = j1)."""
    out: dict[str, dict] = {}

    def add(cle, pari, o, ch, typ=None):
        e = out.setdefault(cle, {"pari": pari, "rows": []})
        e["rows"].append((o, ch))
        if typ:
            e["cote_type"] = typ

    ha = odds.get("Home/Away") or {}
    for b, oh in (ha.get("Home") or {}).items():
        oa = (ha.get("Away") or {}).get(b)
        oh, oa = _num(oh), _num(oa)
        if oh and oa:
            s = 1 / oh + 1 / oa
            add("t_w1", f"{j1} gagne le match", oh, (1 / oh) / s)
            add("t_w2", f"{j2} gagne le match", oa, (1 / oa) / s)
    sb = odds.get("Set Betting") or {}
    books = set().union(*[set(v) for v in sb.values()]) if sb else set()
    for b in books:
        q = {k: _num((sb.get(k) or {}).get(b)) for k in ("2:0", "2:1", "0:2", "1:2")}
        if not all(q.values()):
            continue
        s = sum(1 / o for o in q.values())
        f = {k: (1 / o) / s for k, o in q.items()}
        add("t_s20", f"{j1} gagne 2 sets à 0", q["2:0"], f["2:0"])
        add("t_s02", f"{j2} gagne 2 sets à 0", q["0:2"], f["0:2"])
        for cle, team, ks in (("t_set1", j1, ("2:0", "2:1", "1:2")), ("t_set2", j2, ("0:2", "1:2", "2:1"))):
            add(cle, f"{team} gagne au moins un set", 1 / sum(1 / q[k] for k in ks), sum(f[k] for k in ks),
                "couverture de plusieurs issues")
    ou = odds.get("Over/Under by Games in Match") or {}
    ov, un = ou.get("Over/Under by Games in Match Over") or {}, ou.get("Over/Under by Games in Match Under") or {}
    for line, bo in ov.items():
        for b, o in bo.items():
            o, u = _num(o), _num((un.get(line) or {}).get(b))
            if o and u and float(line) % 1 == 0.5:
                s = 1 / o + 1 / u
                add(f"t_o{line}", f"Plus de {_fmt(float(line))} jeux dans le match", o, (1 / o) / s)
                add(f"t_u{line}", f"Moins de {_fmt(float(line))} jeux dans le match", u, (1 / u) / s)
    ah = odds.get("Asian Handicap (Games)") or {}
    hh, aa = ah.get("Asian Handicap (Games) Home") or {}, ah.get("Asian Handicap (Games) Away") or {}
    for line, bo in hh.items():  # the feed's « Away L » is the other side of « Home L » (away -L)
        try:
            ln = float(line)
        except ValueError:
            continue
        if ln % 1 != 0.5:
            continue
        for b, o in bo.items():
            o, a2 = _num(o), _num((aa.get(line) or {}).get(b))
            if o and a2 and 1.0 < 1 / o + 1 / a2 < 1.15:
                s = 1 / o + 1 / a2
                add(f"t_h1{ln:+g}", f"{j1} handicap {_fmt(ln) if ln < 0 else '+' + _fmt(ln)} jeux", o, (1 / o) / s)
                add(f"t_h2{-ln:+g}", f"{j2} handicap {_fmt(-ln) if ln > 0 else '+' + _fmt(-ln)} jeux", a2, (1 / a2) / s)
    return out


def _settle_tennis(cle: str, sets: list[tuple[int, int]]) -> str:
    s1 = sum(a > b for a, b in sets)
    s2 = len(sets) - s1
    g1, g2 = sum(a for a, _ in sets), sum(b for _, b in sets)
    if cle in ("t_w1", "t_w2"):
        return "gagne" if (s1 > s2) == (cle == "t_w1") else "perdu"
    if cle == "t_s20":
        return "gagne" if (s1, s2) == (2, 0) else "perdu"
    if cle == "t_s02":
        return "gagne" if (s1, s2) == (0, 2) else "perdu"
    if cle in ("t_set1", "t_set2"):
        return "gagne" if (s1 if cle == "t_set1" else s2) >= 1 else "perdu"
    if cle[:3] in ("t_o", "t_u"):
        line = float(cle[3:])
        return "gagne" if ((g1 + g2 > line) if cle[2] == "o" else (g1 + g2 < line)) else "perdu"
    line = float(cle[4:])  # t_h1-2.5 / t_h2+2.5
    diff = (g1 - g2) if cle[3] == "1" else (g2 - g1)
    return "gagne" if diff + line > 0 else "perdu"


def _basket_candidates(bookmakers: list[dict], home: str, away: str) -> dict[str, dict]:
    from sportai.services.basket import parse_odds
    quotes = parse_odds({"bookmakers": bookmakers})
    other = {"home": "away", "away": "home", "over": "under", "under": "over"}
    out: dict[str, dict] = {}
    for (market, sel, line), books in quotes.items():
        if market not in ("handicap", "total", "team_total_home", "team_total_away"):
            continue
        opp = quotes.get((market, other[sel], line)) or {}
        if market == "handicap":
            hl = line if sel == "home" else -line
            team = home if sel == "home" else away
            pari = f"{team} handicap {'+' if hl > 0 else ''}{_fmt(hl)} points"
        elif market == "total":
            pari = f"{'Plus' if sel == 'over' else 'Moins'} de {_fmt(line)} points dans le match"
        else:
            team = home if market.endswith("home") else away
            pari = f"{team} : {'plus' if sel == 'over' else 'moins'} de {_fmt(line)} points"
        cle = f"b_{market}_{sel}_{line:g}"
        rows = [(o, (1 / o) / (1 / o + 1 / opp[b]) if b in opp else None) for b, o in books.items()]
        out[cle] = {"pari": pari, "rows": rows}
    return out


def _settle_basket(cle: str, h: int, a: int) -> str:
    _, rest = cle.split("_", 1)
    market, sel, line = rest.rsplit("_", 2)
    line = float(line)
    if market == "handicap":
        d = (h - a + line) if sel == "home" else (a - h - line)
        return "gagne" if d > 0 else "perdu"
    pts = {"total": h + a, "team_total_home": h, "team_total_away": a}[market]
    return "gagne" if (pts > line) == (sel == "over") else "perdu"


def _choose(pool: list[dict], sport: str, places: int, now: datetime) -> list[dict]:
    """pool = [{match, match_id, competition, coup_envoi, cands}] -> the best candidate per match, then the top."""
    best = []
    for x in pool:
        top = None
        for cle, e in x["cands"].items():
            got = _pick(e["rows"])
            if not got:
                continue
            o, ch = got
            if not (ODDS_MIN_SPORT[sport] <= o <= ODDS_MAX) or ch < MIN_CHANCE:
                continue
            pri = cle.startswith(FIRST.get(sport, ("",)))
            if top is None or (pri, ch) > (top["_pri"], top["proba_marche"]):
                top = {"cle": cle, "pari": e["pari"], "cote": round(o, 2), "proba_marche": round(ch, 4),
                       "cote_type": e.get("cote_type", "cotée"), "nb_sites": len(e["rows"]), "_pri": pri}
        if top:
            best.append({**{k: x[k] for k in ("match", "match_id", "competition", "coup_envoi")}, **top})
    best.sort(key=lambda b: (not b["_pri"], -b["proba_marche"]))
    out = []
    for b in best[:places]:
        b.pop("_pri")
        how = (" Cote obtenue en couvrant plusieurs issues chez le même site." if b["cote_type"] != "cotée" else "")
        out.append({"sport": sport, **b, "resultat": None, "fige": False, "choisi_a": now.isoformat(timespec="minutes"),
                    "explication": (f"Le marché donne {round(b['proba_marche'] * 100)} % de chances à "
                                    f"« {b['pari']} » (cote {_fmt(b['cote'])}).{how}")})
    return out


def _paris_iso(day: str, hhmm: str) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=ZoneInfo("Europe/Paris")).isoformat()


def passage_tennis(day: str, tennis_doc: dict, kept: list[dict], now: datetime) -> list[dict]:
    taken = {str(p["match_id"]) for p in kept if p["sport"] == "tennis"}
    odds = (_get_url(TENNIS, "", method="get_odds", APIkey=os.environ["API_TENNIS_KEY"], date_start=day,
                     date_stop=day).get("result") or {})
    pool = []
    for m in tennis_doc.get("matchs") or []:
        mid, ko = str(m["id"]), _paris_iso(day, m["heure_paris"])
        if mid in taken or now >= datetime.fromisoformat(ko) - FREEZE or mid not in odds:
            continue
        pool.append({"match": f"{m['joueur_1']} - {m['joueur_2']}", "match_id": mid,
                     "competition": m.get("tournoi"), "coup_envoi": ko,
                     "cands": _tennis_candidates(odds[mid], m["joueur_1"], m["joueur_2"])})
    return _choose(pool, "tennis", MAX_PLACES - len(taken), now)


def passage_basket(day: str, basket_doc: dict, kept: list[dict], now: datetime) -> list[dict]:
    taken = {str(p["match_id"]) for p in kept if p["sport"] == "basket"}
    pool = []
    for g in (basket_doc.get("paris_retenus") or {}).values():
        mid, ko = str(g["match_id"]), g["coup_envoi"].replace("Z", "+00:00")
        if mid in taken or now >= datetime.fromisoformat(ko) - FREEZE:
            continue
        resp = _get_url(BASKET, "odds", {"x-apisports-key": os.environ["API_FOOTBALL_KEY"]}, game=mid).get("response")
        if not resp:
            continue
        home, away = g["match"].split(" - ", 1)
        pool.append({"match": g["match"], "match_id": mid, "competition": g.get("competition"), "coup_envoi": ko,
                     "cands": _basket_candidates(resp[0].get("bookmakers") or [], home, away)})
    return _choose(pool, "basket", MAX_PLACES - len(taken), now)


def passage(day: str, leagues: list[int], doc: dict, now: datetime, tennis_doc: dict | None = None,
            basket_doc: dict | None = None) -> dict:
    doc = {"jour": day, "regle": REGLE, "positions": [], **doc}
    doc["regle"] = REGLE
    doc["regle_par_sport"] = {"foot": REGLE, **REGLE_SPORT}
    kept = [p for p in doc["positions"] if p.get("fige") or now >= datetime.fromisoformat(p["coup_envoi"]) - FREEZE]
    for p in kept:
        p["fige"] = True
    taken = {p["match_id"] for p in kept}
    kept_foot = [p for p in kept if p.get("sport", "foot") == "foot"]
    pool, scanned = [], []
    for lg in leagues:
        season = int(day[:4]) if int(day[5:7]) >= 7 else int(day[:4]) - 1
        for fx in _get("fixtures", league=lg, season=season, date=day, timezone="Europe/Paris").get("response") or []:
            fid, ko = fx["fixture"]["id"], fx["fixture"]["date"]
            if fid in taken or now >= datetime.fromisoformat(ko) - FREEZE:
                continue
            home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
            fh, fa = _form(fx["teams"]["home"]["id"], day), _form(fx["teams"]["away"]["id"], day)
            odds = (_get("odds", fixture=fid).get("response") or [{}])[0].get("bookmakers") or []
            by_key: dict[str, list] = {}
            for bk in odds:
                for cand in _candidates(_outcomes(bk), home, away):
                    by_key.setdefault(cand["cle"], []).append(cand)
            best = None
            for cle, rows in by_key.items():
                chances = [r["chance"] for r in rows if r["chance"] is not None]
                top = max(rows, key=lambda r: r["cote"])
                if not chances or not (ODDS_MIN <= top["cote"] <= ODDS_MAX):
                    continue
                cand = {**top, "chance": round(statistics.median(chances), 4), "nb_bookmakers": len(rows)}
                combo = _is_combo(cle)
                if cand["chance"] < (MIN_CHANCE_COMBO if combo else MIN_CHANCE) or not _form_ok(cle, fh, fa):
                    continue
                if best is None or (combo, cand["chance"]) > (_is_combo(best["cle"]), best["chance"]):
                    best = cand
            scanned.append(f"{home} - {away}")
            if best:
                pool.append({"fx": fx, "home": home, "away": away, "best": best, "fh": fh, "fa": fa})
    pool.sort(key=lambda x: (not _is_combo(x["best"]["cle"]), -x["best"]["chance"]))
    fresh = []
    for x in pool[: max(0, MAX_PLACES - len(kept_foot))]:
        fx, b, fh, fa = x["fx"], x["best"], x["fh"], x["fa"]
        how = (" Cote obtenue en couvrant les deux issues chez le même site." if b.get("cote_type") else "")
        fresh.append({
            "sport": "foot", "match": f"{x['home']} - {x['away']}", "match_id": fx["fixture"]["id"],
            "competition": fx["league"]["name"], "coup_envoi": fx["fixture"]["date"], "pari": b["pari"],
            "cle": b["cle"], "cote": round(b["cote"], 2), "proba_marche": b["chance"],
            "cote_type": b.get("cote_type", "cotée"), "nb_sites": b["nb_bookmakers"],
            "forme_5": {"domicile": fh, "exterieur": fa},
            "explication": (f"Le marché donne {round(b['chance'] * 100)} % de chances à « {b['pari']} » "
                            f"(cote {_fmt(round(b['cote'], 2))}). Forme sur 5 matchs (du plus récent) : "
                            f"{x['home']} {fh or '?'}, {x['away']} {fa or '?'}.{how}"),
            "resultat": None, "fige": False, "choisi_a": now.isoformat(timespec="minutes")})
    fresh += passage_tennis(day, tennis_doc, kept, now) if tennis_doc else []
    fresh += passage_basket(day, basket_doc, kept, now) if basket_doc else []
    doc["positions"] = kept + fresh
    doc["matchs_etudies"] = sorted(set(doc.get("matchs_etudies") or []) | set(scanned))
    doc["non_cote"] = ("« Équipe ou nul + buts » n'est pas proposé tel quel dans nos cotes : prix obtenu en couvrant "
                       "les deux issues chez le même site.")
    doc["maj"] = now.isoformat(timespec="minutes")
    return _ratio(doc)


def regler(doc: dict, now: datetime) -> dict:
    for p in doc.get("positions") or []:
        if now >= datetime.fromisoformat(p["coup_envoi"].replace("Z", "+00:00")) - FREEZE:
            p["fige"] = True
        if p.get("resultat"):
            continue
        if p.get("sport") == "tennis":
            r = (_get_url(TENNIS, "", method="get_fixtures", APIkey=os.environ["API_TENNIS_KEY"],
                          date_start=p["coup_envoi"][:10], date_stop=p["coup_envoi"][:10],
                          match_key=p["match_id"]).get("result") or [None])[0]
            if r and r.get("event_status") == "Finished" and r.get("scores"):
                sets = [(int(float(x["score_first"])), int(float(x["score_second"]))) for x in r["scores"]]
                p["score"] = r.get("event_final_result")
                p["resultat"] = _settle_tennis(p["cle"], sets)
            elif r and r.get("event_status") in ("Retired", "Walk Over", "Cancelled"):
                p["resultat"], p["score"] = "annule", r.get("event_status")
            continue
        if p.get("sport") == "basket":
            g = (_get_url(BASKET, "games", {"x-apisports-key": os.environ["API_FOOTBALL_KEY"]},
                          id=p["match_id"]).get("response") or [None])[0]
            if g and g["status"]["short"] in ("FT", "AOT"):
                h, a = g["scores"]["home"]["total"], g["scores"]["away"]["total"]
                p["score"], p["resultat"] = f"{h}-{a}", _settle_basket(p["cle"], h, a)
            continue
        fx = (_get("fixtures", id=p["match_id"]).get("response") or [None])[0]
        if not fx or fx["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
            continue
        sc = fx["score"]
        h, a = sc["fulltime"]["home"], sc["fulltime"]["away"]  # 90 minutes
        p["score"] = f"{h}-{a}"
        p["resultat"] = _settle(p["cle"], h, a, sc["halftime"]["home"], sc["halftime"]["away"])
    doc["maj"] = now.isoformat(timespec="minutes")
    return _ratio(doc)


def _count(ps: list[dict]) -> dict:
    s = [p for p in ps if p.get("resultat") in ("gagne", "perdu")]
    return {"gagnes": sum(p["resultat"] == "gagne" for p in s), "joues": len(s),
            "rembourses": sum(p.get("resultat") == "rembourse" for p in ps)}


def _ratio(doc: dict) -> dict:
    """One ratio per sport, never mixed (owner 2026-11-05 07:17Z)."""
    ps = doc.get("positions") or []
    for p in ps:
        p.setdefault("sport", "foot")
    doc["ratio_par_sport"] = {sp: _count([p for p in ps if p["sport"] == sp]) for sp in ("foot", "tennis", "basket")}
    doc.pop("ratio", None)
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("passage", "regler"))
    ap.add_argument("--jour")
    ap.add_argument("--ligues", default="3")
    ap.add_argument("--doc")
    ap.add_argument("--tennis", help="the page's tennis/<jour> document (JSON) for the tennis part")
    ap.add_argument("--basket", help="the page's basket_pronostic/<jour> document (JSON) for the basket part")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    doc = json.loads(Path(a.doc).read_text()) if a.doc and Path(a.doc).exists() else {}
    doc = doc.get("data", doc)
    for k in ("version", "id", "updated_at", "created_at"):
        doc.pop(k, None)
    if a.cmd == "passage":
        side = {}
        for k in ("tennis", "basket"):
            f = getattr(a, k)
            if f and Path(f).exists():
                d = json.loads(Path(f).read_text())
                side[k + "_doc"] = d.get("data", d)
        doc = passage(a.jour, [int(x) for x in a.ligues.split(",")], doc, now, **side)
    else:
        doc = regler(doc, now)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    print(json.dumps([(p["sport"], p["match"], p["pari"], p["cote"], p["proba_marche"], p["resultat"])
                      for p in doc["positions"]],
                     ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main()
