"""Tennis test pass ("non validé"): price the day's ATP and WTA singles with the Elo + aces models against the odds.

Paris day D = matches from D 08:00 to D+1 02:00 (house rule). For each match, the five axes are priced (winner, set
score, total games, games handicap, aces 1x2) and ONE choice is kept, the same way as football and basketball:
best odds of any bookmaker between 1.50 and 2.30, confidence = 50 % model + 50 % market (élevée >= 60 %,
moyenne 50-60 %). A choice that fails the usual checks (no value at the best odds, or model too far from the
market) is still shown with "Le modèle ne le retient pas" and stays out of the ratio.

The choice is frozen ("fige") 45 minutes before the start: later runs of the day keep it and only add its result
("gagne", "perdu", "rembourse" on a retirement or walk-over). The file is then copied as is into the page's
database (collection "tennis", document = the date).

Before pricing, the current week's results are refreshed (fetch_tennis.py) so the ratings include yesterday.

Usage: python scripts/tennis_jour.py --date 2026-10-30 --out /mnt/project-files/agent-sportif/tennis/2026-10-30.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_tennis as bt  # noqa: E402
import fetch_tennis  # noqa: E402
from sportai.engine.tennis import elo  # noqa: E402
from sportai.engine.tennis.aces import AcesModel  # noqa: E402

NON_RETENU = "Le modèle ne le retient pas"
FREEZE_MIN = 45  # the choice is frozen 45 minutes before the start, as in football and basketball

# Country of each bookmaker's licence, for the coloured dot on the page (no name shown).
PAYS = {"bet365": "royaume_uni", "WilliamHill": "royaume_uni", "BetVictor": "royaume_uni", "Betfair": "royaume_uni",
        "Unibet": "malte", "Betsson": "malte", "NordicBet": "malte", "bwin": "malte", "10Bet": "malte",
        "Tipico": "malte", "Betway": "malte", "Betano": "malte", "1xBet": "curacao", "Marathon": "curacao",
        "Pncl": "curacao"}


def books_of(odds: dict, key: tuple) -> dict:
    """Bookmaker -> odds of one selection, read where backtest_tennis.markets() reads it."""
    mk = key[0]
    if mk == "vainqueur":
        return odds.get("Home/Away", {}).get(key[1]) or {}
    if mk == "score_sets":
        return (odds.get("Set Betting") or {}).get(key[1]) or {}
    if mk == "aces":
        return (odds.get("1x2 Aces") or {}).get(key[1]) or {}
    if mk == "jeux":
        side = (odds.get("Over/Under by Games in Match") or {}).get(f"Over/Under by Games in Match {key[1]}") or {}
        return next((b for line, b in side.items() if bt._half(line) and float(line) == key[2]), {})
    if mk == "handicap_jeux":
        side = (odds.get("Asian Handicap (Games)") or {}).get(f"Asian Handicap (Games) {key[1]}") or {}
        want = key[2] if key[1] == "Home" else -key[2]
        return next((b for line, b in side.items() if bt._half(line) and float(line) == want), {})
    return {}


def best_book(odds: dict, key: tuple) -> str | None:
    books = {k: float(v) for k, v in books_of(odds, key).items() if str(v).replace(".", "", 1).isdigit()}
    return max(books, key=books.get) if books else None


def settle(choix: dict, m: bt.Match) -> None:
    if m.status in ("Retired", "Walk Over", "Cancelled"):
        choix["resultat"] = "rembourse"
    elif m.status == "Finished" and m.sets and m.a_won is not None:
        w = bt.won(tuple(choix["cle"]), m)
        if w is not None:
            choix["resultat"] = "gagne" if w else "perdu"


def label(key: tuple, m: bt.Match) -> str:
    who = {"Home": m.name_a, "Away": m.name_b}
    mk = key[0]
    if mk == "vainqueur":
        return f"{who[key[1]]} gagne le match"
    if mk == "score_sets":
        a, b = key[1].split(":")
        return f"{m.name_a if a > b else m.name_b} gagne {max(a, b)} sets à {min(a, b)}"
    if mk == "jeux":
        return f"{'Plus' if key[1] == 'Over' else 'Moins'} de {key[2]:g} jeux dans le match"
    if mk == "handicap_jeux":
        return f"{who[key[1]]} handicap {key[2]:+g} jeux"
    if mk == "aces":
        return "Autant d'aces" if key[1] == "Draw" else f"{who[key[1]]} sert plus d'aces"
    return str(key)


def fit(history: list[bt.Match], before: str):
    ratings = {t: elo.SurfaceElo() for t in ("ATP", "WTA")}
    acesm = {t: AcesModel() for t in ("ATP", "WTA")}
    for m in history:
        if m.day >= before or m.status != "Finished" or m.a_won is None or not m.sets:
            continue
        ratings[m.tour].update(m.a if m.a_won else m.b, m.b if m.a_won else m.a, m.surface)
        if m.aces:
            acesm[m.tour].update(m.a, m.b, m.tour, m.surface, m.aces[0], m.aces[1], sum(m.games))
    return ratings, acesm


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--out")
    ap.add_argument("--no-refresh", action="store_true")
    a = ap.parse_args()
    day = date.fromisoformat(a.date)
    monday = day - timedelta(days=day.weekday())
    if not a.no_refresh:
        for tour, key in {**fetch_tennis.TOURS, **fetch_tennis.CHALLENGERS}.items():
            fetch_tennis.week(tour, monday, True, key, with_odds=tour in fetch_tennis.TOURS)
    history = bt.load()
    ratings, acesm = fit(history, a.date)
    surf = bt.surfaces()
    span = {"date_start": str(day), "date_stop": str(day + timedelta(days=1)), "timezone": "Europe/Paris"}
    # Choices already frozen in an earlier run of the day are kept as they are (only their result is filled in).
    before = {}
    if a.out and Path(a.out).exists():
        before = {x["id"]: x for x in json.loads(Path(a.out).read_text()).get("matchs", [])}
    now_paris = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d %H:%M")
    report = []
    for tour, key in fetch_tennis.TOURS.items():
        fixtures = fetch_tennis.call("get_fixtures", event_type_key=key, **span).get("result") or []
        odds = fetch_tennis.call("get_odds", event_type_key=key, **{k: v for k, v in span.items() if k != "timezone"})
        odds = odds.get("result") or {}
        for raw in fixtures:
            when = f"{raw['event_date']} {raw.get('event_time') or '00:00'}"
            if not f"{day} 08:00" <= when <= f"{day + timedelta(days=1)} 02:00":
                continue
            if not raw.get("first_player_key") or not raw.get("second_player_key"):
                continue
            m = bt.parse(raw, tour, surf, odds if isinstance(odds, dict) else {})
            r = ratings[tour]
            known = r.known(m.a, bt.MIN_MATCHES) and r.known(m.b, bt.MIN_MATCHES)
            pm = r.p_win(m.a, m.b, m.surface)
            pr0 = bt.Pricer(pm, m, None, None)
            ea = eb = None
            if acesm[tour].serve[m.a].den > 0 and acesm[tour].serve[m.b].den > 0:
                ea, eb = acesm[tour].expected(m.a, m.b, tour, m.surface, pr0.expected_games())
            pr = bt.Pricer(pm, m, ea, eb)
            mk = bt.markets(m.odds)
            cands = bt.candidates(pr, mk) if known else []
            c = bt.choose(cands)
            choix = None
            start = datetime.strptime(when, "%Y-%m-%d %H:%M")
            frozen = now_paris >= (start - timedelta(minutes=FREEZE_MIN)).strftime("%Y-%m-%d %H:%M")
            old = (before.get(m.key) or {}).get("choix")
            started = (raw.get("event_status") or "") not in ("", "Cancelled", "Walk Over")
            if old and old.get("fige"):
                choix = old
            elif started:
                pass  # no choice made once the match has begun (the odds would be live ones)
            elif c:
                choix = {"position": label(c["key"], m), "marche": bt.LABEL[c["key"][0]], "cle": list(c["key"]),
                         "cote": c["best"], "cote_mediane": c["median"], "proba_modele": round(c["p"], 3),
                         "proba_bookmakers": round(c["fair"], 3), "confiance": round(c["conf"], 3),
                         "niveau": "Confiance " + bt.level(c["conf"]),
                         "statut": "retenu" if c["retenu"] else NON_RETENU,
                         "pays_bookmaker": PAYS.get(best_book(m.odds, c["key"]), "autre"), "fige": frozen}
            if choix:
                settle(choix, m)
            report.append({
                "id": m.key, "circuit": tour, "heure_paris": raw.get("event_time"), "tournoi": m.tournament,
                "tour": raw.get("tournament_round"), "surface": m.surface, "sets_gagnants": m.best_of // 2 + 1,
                "joueur_1": m.name_a, "joueur_2": m.name_b, "statut_match": raw.get("event_status"),
                "score": (raw.get("event_final_result") or "").replace(" ", "") if raw.get("event_status") else "",
                "joueurs_connus": known, "proba_victoire_j1": round(pm, 3),
                "jeux_attendus": round(pr.expected_games(), 1),
                "aces_attendus": [round(ea, 1), round(eb, 1)] if ea is not None else None,
                "choix": choix, "nb_marches_cotes": len(mk),
            })
    report.sort(key=lambda x: (x["heure_paris"] or "", x["circuit"]))
    out = {"date": a.date, "statut": "non validé", "genere": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "matchs": report}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for x in report:
        c = x["choix"]
        print(x["heure_paris"], x["circuit"], x["tournoi"], x["joueur_1"], "-", x["joueur_2"], "P(j1)", x["proba_victoire_j1"],
              "|", (f"{c['position']} @ {c['cote']} conf {c['confiance']} {c['statut']}" if c else "pas de choix"))


if __name__ == "__main__":
    main()
