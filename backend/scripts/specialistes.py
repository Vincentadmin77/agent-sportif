"""The three sport specialists, à blanc (docs/25-specialistes.md): one choice per match, own ratio per sport, never
published, never in the official or the M's ratio.

    python scripts/specialistes.py --page PAGE --out OUT [--jour 2026-10-31] [--no-tennis] [--foot-seul]

PAGE holds the page's collections as ArtifactData `list` saves them: matchs/ and basket_matchs/ (the day's documents),
specialistes/ (the earlier days, for the running ratio). Football and basketball read the match documents the
official passes wrote (no API call). Tennis prices the day's matches itself with the tennis pass's ratings and the
api-tennis.com odds (2 calls a circuit; API_TENNIS_KEY), on the history the tennis pass refreshes every morning.
Writes OUT/specialistes/<jour>.json and OUT/specialistes/bilan.json, for the page's collection `specialistes`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sportai.services import specialistes as S  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")
FREEZE = timedelta(minutes=45)
# API-Football quota (coordinator 27/11 19:06Z, 7 382 of 7 500 used): the hourly run is a new process each time, so
# without a disk cache every run downloaded both squads (/players, all pages) and next fixtures of every match again.
# One provider per run and a disk cache kept between runs: a squad is then fetched about every 6 h, not every hour.
HTTP_CACHE = "/mnt/project-files/agent-sportif/state/httpcache-specialiste"
QUOTA_FLOOR = 1500  # coordinator 27/11 19:46Z: under this many calls left today, the specialist calls nothing (the Direct first)
_API = None
_QUOTA: list = []


def _quota_ok() -> bool:
    """True while API-Football has more than QUOTA_FLOOR calls left today (/status does not count, read once a run).
    Otherwise the specialist runs on the page documents alone: squad absences only, no calendar rule."""
    if not _QUOTA:
        try:
            r = _api()._get("/status", {}, ttl=0)["response"]["requests"]
            _QUOTA.append(int(r["limit_day"]) - int(r["current"]) > QUOTA_FLOOR)
        except Exception as e:  # noqa: BLE001 - unknown quota: stay on the safe side
            print(f"API-Football quota unknown ({str(e)[:120]}): no call this run", file=sys.stderr)
            _QUOTA.append(False)
        if not _QUOTA[0]:
            print(f"API-Football: {QUOTA_FLOOR} calls left or fewer, the specialist calls nothing this run", file=sys.stderr)
    return _QUOTA[0]


def _api():
    global _API
    if _API is None:
        from sportai.config import get_settings
        from sportai.connectors.api_football import ApiFootballProvider
        if not os.getenv("SPORTAI_HTTP_CACHE") and Path(HTTP_CACHE).parent.is_dir():
            os.environ["SPORTAI_HTTP_CACHE"] = HTTP_CACHE
        _API = ApiFootballProvider(get_settings().api_football_key)
    return _API


def _load(folder: str, name: str) -> dict[str, dict]:
    d = Path(folder) / name
    if not d.exists():
        return {}
    docs = {p.stem: json.loads(p.read_text()) for p in d.glob("*.json")}
    return {k: v.get("data", v) if isinstance(v.get("data"), dict) else v for k, v in docs.items()}


def _aware(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _keep(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None or k == "pari"}


def _keys_of(m: dict, when: datetime) -> tuple[dict | None, dict | None]:
    """Key players and regular attackers / goalkeepers of both sides (API-Football /players, cached 6 h);
    (None, None) when unavailable: the weakened-side rule then uses the squad absences only."""
    if not _quota_ok():
        return None, None
    try:
        api = _api()
        out, roles = {}, {}
        for side, tid in (("domicile", m.get("id_domicile")), ("exterieur", m.get("id_exterieur"))):
            if not tid:
                return None, None
            keys, pl = [], []
            for season in (when.year if when.month >= 7 else when.year - 1, when.year):
                pl = api.team_players(int(tid), season)
                keys = S.key_players(pl, int(tid))
                if keys:
                    break
            out[side], roles[side] = keys, S.regular_roles(pl, int(tid))
        return out, roles
    except Exception as e:  # noqa: BLE001
        print(f"key players unavailable ({str(e)[:120]})", file=sys.stderr)
        return None, None


EUROPEAN = ("UCL", "UEL", "UECL", "ENC", "Ligue des champions", "Ligue Europa", "Coupe")  # tested on league games only


TREVE = "la trêve internationale"
# National-team competitions (World Cup, its qualifiers in every zone, Nations League, friendlies): one of them
# played inside a long gap between two club matches means the players leave for their national team.
NATIONAL_LEAGUES = {1, 5, 10, 29, 30, 31, 32, 33, 34, 37}
BREAK_GAP_DAYS = 10


def _ucl_calendar(m: dict, ko: datetime | None) -> dict | None:
    """Owner 28/11 08:26-08:27Z: « 1 match avant seulement la LDC » / « LDC ou sélection nationale pour la coupe du
    monde ». For each side whose very next match after this league game is a Champions League match, or that leaves
    for an international break after it, {side: days until that, "libelle": {side: what it is}}; None in a European
    game, when no side has one, or when unknown. API-Football /fixtures?team=&next= (6 h) and, for a gap of
    BREAK_GAP_DAYS or more, /fixtures?date= in the middle of it (24 h, shared by every match)."""
    if not ko or any(x in str(m.get("competition") or "") or x == m.get("code_ligue") for x in EUROPEAN):
        return None
    if not _quota_ok():
        return None
    try:
        api = _api()
        out, label = {}, {}
        for side, tid in (("domicile", m.get("id_domicile")), ("exterieur", m.get("id_exterieur"))):
            nxt = sorted((f for f in (api.team_next(int(tid), 3) if tid else []) if f["fixture"].get("timestamp")),
                         key=lambda f: f["fixture"]["timestamp"])
            after = [f for f in nxt if f["fixture"]["timestamp"] > ko.timestamp() + 3 * 3600]
            if not after:
                continue
            f = after[0]
            days = (datetime.fromtimestamp(f["fixture"]["timestamp"], timezone.utc) - ko).total_seconds() / 86400
            what = "la Ligue des champions" if (f.get("league") or {}).get("id") == 2 else None
            if what is None and days >= BREAK_GAP_DAYS:
                probe = (ko + timedelta(days=5)).date().isoformat()
                if any((g.get("league") or {}).get("id") in NATIONAL_LEAGUES
                       for g in api._get("/fixtures", {"date": probe}, ttl=86400).get("response", [])):
                    what = TREVE
            if what:
                out[side], label[side] = max(1, round(days)), what
        return {**out, "libelle": label} if out else None
    except Exception as e:  # noqa: BLE001 - the calendar rule is then skipped
        print(f"calendar unavailable ({str(e)[:120]})", file=sys.stderr)
        return None


def foot(day: str, matchs: dict[str, dict], prev: dict, now: datetime) -> dict:
    try:  # xG of every league game, from the API-Football cache the morning goals refresh keeps up to date
        from sportai.services.goals_refresh import refresh_data
        history = refresh_data(now, None)[0]
    except Exception:  # noqa: BLE001 - the xG filter is then skipped
        history = {}
    out = {}
    for mid, m in sorted(matchs.items()):
        if m.get("jour") != day or m.get("sport", "foot") != "foot":
            continue
        ko = _aware(m.get("coup_envoi"))
        frozen = bool((m.get("passages") or {}).get("m45")) or bool(ko and now >= ko - FREEZE)
        luck = (S.xg_luck(history, m.get("domicile"), ko or now), S.xg_luck(history, m.get("exterieur"), ko or now))
        p = prev.get(mid)
        weak = None
        if not (p and (p.get("fige") or (ko and now >= ko))):  # a stored choice that no longer moves needs nothing
            weak = S.weakened(m, *_keys_of(m, ko or now)) if S.weakened(m) else None
        cal = _ucl_calendar(m, ko) if weak else None
        c = S.record(p, S.foot_choice(m, luck, weak, cal), ko, now, frozen)
        S.foot_settle(c, m)
        out[mid] = _keep({**c, "match": f"{m.get('domicile')} - {m.get('exterieur')}", "competition": m.get("competition"),
                          "coup_envoi": m.get("coup_envoi"), "score": m.get("score") if m.get("statut") == "termine" else None})
    return out


def basket(day: str, bmatchs: dict[str, dict], prev: dict, now: datetime) -> dict:
    try:  # NBA injuries (ESPN, internal test) and the box scores to weigh them
        from sportai.services import basket_absents as BA
        injuries, boxes = BA.fetch(now), BA.box_scores()
    except Exception:  # noqa: BLE001 - V2 then works without injuries
        injuries = boxes = None
    out = {}
    for mid, bm in sorted(bmatchs.items()):
        if bm.get("jour") != day:
            continue
        ko = _aware(bm.get("coup_envoi"))
        frozen = bool((bm.get("passages") or {}).get("m45")) or bool(ko and now >= ko - FREEZE)
        adj = None
        if injuries and (bm.get("competition") or bm.get("code_ligue")) == "NBA":
            adj = BA.game_adjustment(bm.get("domicile") or "", bm.get("exterieur") or "", injuries, boxes)
        new = S.basket_choice(bm, day, adj)
        if adj and (adj["domicile"]["absents"] or adj["exterieur"]["absents"]):
            new["absents"] = {"domicile": adj["domicile"]["absents"], "exterieur": adj["exterieur"]["absents"],
                              "points_ecart": adj["marge"], "points_total": adj["total"]}
        c = S.record(prev.get(mid), new, ko, now, frozen)
        S.basket_settle(c, bm)
        out[mid] = _keep({**c, "match": f"{bm.get('domicile')} - {bm.get('exterieur')}",
                          "competition": bm.get("competition"), "coup_envoi": bm.get("coup_envoi"),
                          "score": bm.get("score") if bm.get("statut") == "termine" else None})
    return out


def tennis(day: str, prev: dict, now: datetime) -> dict:
    """The day's ATP and WTA singles (08:00 to 02:00 Paris), priced as the tennis pass prices them."""
    import backtest_tennis as bt
    import fetch_tennis
    from tennis_jour import fit, label, settle

    d = date.fromisoformat(day)
    history = bt.load()
    ratings, acesm = fit(history, day)
    last: dict[str, str] = {}
    for m in history:
        if m.status == "Finished" and m.day < day:
            for p in (m.a, m.b):
                last[p] = max(last.get(p, ""), m.day)
    surf = bt.surfaces()
    span = {"date_start": str(d), "date_stop": str(d + timedelta(days=1)), "timezone": "Europe/Paris"}
    out = {}
    for tour, key in fetch_tennis.TOURS.items():
        fixtures = fetch_tennis.call("get_fixtures", event_type_key=key, **span).get("result") or []
        odds = fetch_tennis.call("get_odds", event_type_key=key,
                                 **{k: v for k, v in span.items() if k != "timezone"}).get("result") or {}
        for raw in fixtures:
            when = f"{raw['event_date']} {raw.get('event_time') or '00:00'}"
            if not f"{d} 08:00" <= when <= f"{d + timedelta(days=1)} 02:00":
                continue
            if not raw.get("first_player_key") or not raw.get("second_player_key"):
                continue
            m = bt.parse(raw, tour, surf, odds if isinstance(odds, dict) else {})
            ko = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=PARIS).astimezone(timezone.utc)
            started = (raw.get("event_status") or "") not in ("", "Cancelled", "Walk Over")
            old = prev.get(m.key)
            r = ratings[tour]
            known = r.known(m.a, bt.MIN_MATCHES) and r.known(m.b, bt.MIN_MATCHES)
            pm = r.p_win(m.a, m.b, m.surface)
            pr = bt.Pricer(pm, m, None, None)
            if acesm[tour].serve[m.a].den > 0 and acesm[tour].serve[m.b].den > 0:
                pr = bt.Pricer(pm, m, *acesm[tour].expected(m.a, m.b, tour, m.surface, pr.expected_games()))
            if old and (old.get("fige") or started or now >= ko):
                c = old
            else:
                cands = bt.candidates(pr, bt.markets(m.odds))
                lay = [(d - date.fromisoformat(last[p])).days for p in (m.a, m.b) if p in last]
                lay = max(lay) if lay else None
                s = S.tennis_choice(cands, lay, bt.markets(m.odds)) if known else None
                kept = s is not None
                s = s or S.tennis_fallback(cands, bt.markets(m.odds))
                if s:
                    new = {"pari": label(s["key"], m), "marche": bt.LABEL[s["key"][0]], "cle": list(s["key"]),
                           "cote_prise": s["best"], "p_marche": round(s["fair"], 3), "p_modele": round(s["p"], 3),
                           "chance": round(s["conf"], 3)}
                    if kept:
                        new["raison"] = (f"Le marché voit {round(s['fair'] * 100)} %, l'Elo sur {m.surface} {round(s['p'] * 100)} % : "
                                         f"chance retenue {round(s['conf'] * 100)} %, la cote de {s['best']:.2f} rapporte au moins 3 % de plus.")
                    else:
                        why = ("un des joueurs a trop peu de matchs pour un Elo fiable" if not known
                               else S.tennis_pass_reason(cands, lay).removeprefix("Je passe : ").rstrip("."))
                        new.update(retenu=False, raison=f"{S.NOT_KEPT} : {why}.")
                elif not known:
                    new = {"pari": None, "raison": "Je passe : un des joueurs a trop peu de matchs pour un Elo fiable."}
                else:
                    new = {"pari": None, "raison": S.tennis_pass_reason(cands, lay)}
                c = S.record(old, new, ko, now, now >= ko - FREEZE)
            if c.get("pari") and c.get("resultat") not in S.SETTLED:
                settle(c, m)
            if not c.get("pari"):
                c = {**c, "option_envisagee": _tennis_option(c.get("option_envisagee"), pr, m, known, bt, label, settle)}
            out[m.key] = _keep({**c, "match": f"{m.name_a} - {m.name_b}", "competition": f"{tour} {m.tournament}",
                                "surface": m.surface, "coup_envoi": ko.isoformat(),
                                "score": (raw.get("event_final_result") or "").replace(" ", "") or None
                                if raw.get("event_status") == "Finished" else None})
    for k, c in prev.items():  # a match the fixtures no longer list (moved, cancelled) keeps its stored choice
        out.setdefault(k, c)
    return out


def _tennis_option(opt, pr, m, known, bt, label, settle) -> dict | None:
    """When V3 passes: the option it would have taken without its rule (the most likely at odds 1.50-2.30 with the
    same 90/10 blend, any odds when none is in that range, the market alone when a player has no reliable Elo),
    settled after the match. Never counted: it only shows whether passing was right."""
    if not opt:
        cands = bt.candidates(pr, bt.markets(m.odds))
        if not cands:
            lo, hi = bt.MIN_ODDS, bt.MAX_ODDS
            bt.MIN_ODDS, bt.MAX_ODDS = 1.01, 100.0
            try:
                cands = bt.candidates(pr, bt.markets(m.odds))
            finally:
                bt.MIN_ODDS, bt.MAX_ODDS = lo, hi
        if not known:
            cands = [{**c, "p": c["fair"]} for c in cands]
        s = S.tennis_fallback(cands, bt.markets(m.odds))
        if not s:
            return None
        opt = {"pari": label(s["key"], m), "marche": bt.LABEL[s["key"][0]], "cle": list(s["key"]), "cote_prise": s["best"],
               "chance": round(s["conf"], 3)}
    if opt.get("resultat") not in S.SETTLED:
        settle(opt, m)
    return opt


# Vincent 8/11 12:01Z: basket specialist (V2) removed. From this day on no basket picks; past days stay archived.
BASKET_STOP = "2026-11-08"


def bilan(days: dict[str, dict], sports=("foot", "basket", "tennis")) -> dict:
    out, tous = {}, {}
    for sport in sports:
        rows = [c for d in days.values() for c in (d.get(sport) or {}).values()]
        out[sport] = S.ratio(rows)
        tous[sport] = S.ratio_tous(rows)
    note = ("Le spécialiste foot (seul spécialiste gardé depuis le 26/11)." if sports == ("foot",)
            else "À blanc : jamais publié, hors ratio officiel et hors ratio du M.")
    return {"ratio": out, "ratio_tous": tous, "jours": sorted(days), "note": note}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jour")
    ap.add_argument("--no-tennis", action="store_true")
    ap.add_argument("--foot-seul", action="store_true",
                    help="Vincent 26/11: only the foot specialist is kept; basket/tennis stopped and their history erased")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    paris = now.astimezone(PARIS)
    day = a.jour or (paris - timedelta(hours=4) if paris.hour < 4 else paris).date().isoformat()
    past = _load(a.page, "specialistes")
    past.pop("bilan", None)
    prev = past.get(day) or {}
    doc = {"jour": day, "agents": S.NAMES, "maj": now.isoformat(timespec="seconds"),
           "foot": foot(day, _load(a.page, "matchs"), prev.get("foot") or {}, now),
           "basket": {} if day >= BASKET_STOP else basket(day, _load(a.page, "basket_matchs"), prev.get("basket") or {}, now),
           "tennis": prev.get("tennis") or {}}
    if day >= BASKET_STOP:
        doc["basket_arrete"] = "Spécialiste basket (V2) arrêté le 8/11 sur décision de Vincent ; historique conservé."
    if not a.no_tennis:
        try:
            doc["tennis"] = tennis(day, prev.get("tennis") or {}, now)
        except Exception as e:  # noqa: BLE001 - the other two sports are still written
            doc["erreur_tennis"] = str(e)[:300]
    sports = ("foot", "basket", "tennis")
    if a.foot_seul:
        sports = ("foot",)
        doc.update(agents={"foot": S.NAMES["foot"]}, basket={}, tennis={})
        doc.pop("basket_arrete", None)
        doc.pop("erreur_tennis", None)
    doc["ratio"] = {s: S.ratio(list(doc[s].values())) for s in sports}
    doc["ratio_tous"] = {s: S.ratio_tous(list(doc[s].values())) for s in sports}
    past[day] = doc
    o = Path(a.out) / "specialistes"
    o.mkdir(parents=True, exist_ok=True)
    (o / f"{day}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    (o / "bilan.json").write_text(json.dumps(bilan(past, sports), ensure_ascii=False, indent=1))
    for s in sports:
        picks = [f"{c['match']}: {c['pari']} @ {c['cote_prise']}{' (figé)' if c.get('fige') else ''}{' [non retenu]' if c.get('retenu') is False else ''}"
                 for c in doc[s].values() if c.get("pari")]
        print(f"{s}: {len(doc[s])} matchs, {len(picks)} positions, ratio {doc['ratio'][s]}, tous {doc['ratio_tous'][s]}")
        for p in picks:
            print("  ", p)


if __name__ == "__main__":
    main()
