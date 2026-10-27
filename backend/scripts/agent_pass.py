"""One scheduled step of the agent, for the routines that drive the results page.

    python scripts/agent_pass.py plan [--competitions FR1 ES1]  # today's kick-offs and pass times (free request)
    python scripts/agent_pass.py prematch --page P --out O [--goals] [--matin] FR1 ES1
    python scripts/agent_pass.py scores --page P --out O [--final] FR1
    python scripts/agent_pass.py live --page P --out O FR1 ES1  # matches under way (API-Football)
    python scripts/agent_pass.py pronostic --page P --out O  # "Mon pronostic" tab again, no API call
    python scripts/agent_pass.py goals-refresh [--max-requests 60]  # once a day, before the first prematch pass

Every prematch pass and the final scores step also write pronostic/<day>.json for the "Mon pronostic"
tab (the day's three best odds and three tickets; sportai/services/pronostic.py) and stats/bilan.json
for "Résultats et statistiques" with the bankroll (sportai/services/bilan.py, setting reglages/bankroll).

goals-refresh updates the current-season results and match statistics from API-Football (cached,
incremental, at most --max-requests requests), refits the enriched goals model and saves it to
GOALS_MODEL_PATH (default state/goals_model.json). Prematch passes of the same day (Paris) then price
btts and over/under 1.5 / 2.5 / 3.5 with it; without it, or for a team it does not know, Dixon-Coles.

Odds come from API-Football (every bookmaker and bet type of its feed, 1 to 2 requests per competition and pass);
every bet decided by the final score is priced, those the backtest has not validated marked "non validé" on the
page, in the tickets and for the player. The Odds API is only the fallback when API-Football has no odds, or only
odds older than 4 h: then it supplies the result markets and every other market keeps API-Football's last prices
(up to 12 h old, never past kick-off: runs.keep_stale_quotes), with or without --goals and The Odds API credits;
--goals also fetches its btts / totals odds match by match.

Each pronostic step first reviews the earlier days (sportai/services/revue.py, owner 2026-10-24): the winning bets
nobody chose and the chosen ones that lost, the record of each family of bets, and a correction of the model's
chances by family applied to the day's tickets, ideas and player; pronostic/<day>.json "revue" holds it.

Basketball (NBA, WNBA, docs/21-basket-passes.md): the same subcommands with the codes NBA / WNBA, from
API-Basketball (fixtures, quarter scores, odds; never The Odds API); its day runs to 04:00 Paris. Basketball has
collections of its own on the page: it reads --page/basket_matchs and --page/basket_pronostic and writes only
out/basket_matchs/<game id>.json and out/basket_pronostic/<day>.json, never matchs/, pronostic/ or stats/.

--page is a folder holding the page's current documents (matchs/<id>.json);
--out receives the documents to write back, plus manifest.json. The analysis
database lives in STATE_DB so the 45 min pass can compare with the 3 h pass;
it is copied to a temporary file while the step runs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DB = Path(os.getenv("STATE_DB", "/mnt/project-files/agent-sportif/state/agent.db"))
LIVE_STEPS = tuple(range(0, 120, 15))  # minutes after kick-off: each step (re)starts the 2-minute live loop
FOOT_COMPETITIONS = ["FR1", "EN1", "ES1", "DE1", "IT1", "BR1", "TR1", "UCL", "UEL"]
# Basketball (owner 2026-10-27): NBA and WNBA from API-Basketball, same rules as football (docs/21-basket-passes.md).
BASKET_COMPETITIONS = ["NBA", "WNBA"]
COMPETITIONS = FOOT_COMPETITIONS + BASKET_COMPETITIONS
BASKET_LIVE_STEPS = tuple(range(0, 180, 30))  # a game lasts about 2 h 15: one light update every 30 min
BASKET_FINAL_AFTER = timedelta(hours=2, minutes=30)  # final results after the last tip-off (NBA: ~05:00-06:30 Paris)
# The "Joueur pro" was removed by the owner on 2026-10-26 (too much like gambling, poor results): its code and its
# history stay, but no pass computes or writes its positions any more, pre-match or live.
JOUEUR_PRO = False


def _split(comps: list[str]) -> tuple[list[str], list[str]]:
    """(football competitions, basketball competitions) of a step."""
    return [c for c in comps if c not in BASKET_COMPETITIONS], [c for c in comps if c in BASKET_COMPETITIONS]


def _basket_api():
    from sportai.config import get_settings
    from sportai.connectors.api_basketball import ApiBasketballProvider

    return ApiBasketballProvider(get_settings().api_football_key)


def _load_page(folder: str | None) -> dict[str, dict]:
    """The page's football documents (matchs/<id>.json; a basketball one left there by an older pass is ignored)."""
    if not folder or not (Path(folder) / "matchs").exists():
        return {}
    docs = {p.stem: json.loads(p.read_text()) for p in (Path(folder) / "matchs").glob("*.json")}
    return {k: d for k, d in docs.items() if d.get("sport") != "basket"}


def _load_collection(folder: str | None, name: str) -> dict[str, dict]:
    if not folder or not (Path(folder) / name).exists():
        return {}
    return {p.stem: json.loads(p.read_text()) for p in (Path(folder) / name).glob("*.json")}


BASKET_KEYS = ("basket_matchs", "basket_pronostic")


def _write(out: str, docs: dict[str, dict], message: str, now: datetime) -> None:
    d = Path(out) / "matchs"
    d.mkdir(parents=True, exist_ok=True)
    for key, doc in docs.items():
        (d / f"{key}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    (Path(out) / "etat.json").write_text(json.dumps({"maj": now.isoformat(), "message": message}, ensure_ascii=False))
    manifest = {"matchs": sorted(docs), "message": message}
    try:  # a mixed step wrote its basketball collections first: keep them listed
        old = json.loads((Path(out) / "manifest.json").read_text())
        manifest.update({k: old[k] for k in BASKET_KEYS if k in old})
    except (OSError, ValueError):
        pass
    (Path(out) / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
    print(json.dumps({"written": sorted(docs), "message": message}, ensure_ascii=False))


def _pronostic(db, page: str | None, docs: dict[str, dict], out: str, now: datetime, passage: str,
               day: str | None = None) -> str:
    """Write out/pronostic/<day>.json (default today) and out/stats/bilan.json; returns the day's summary."""
    from datetime import date

    from sportai.services.bilan import apply_stakes, bankroll_settings, build_bilan, replay, replay_by_ticket
    from sportai.services.prematch import journee
    from sportai.services.pronostic import build_pronostic, legs_from_db

    day = day or f"{journee(now):%Y-%m-%d}"
    folder = Path(page or "/nonexistent")
    days = {p.stem: json.loads(p.read_text()) for p in (folder / "pronostic").glob("*.json")}
    raw = folder / "reglages" / "bankroll.json"
    settings = bankroll_settings(json.loads(raw.read_text()) if raw.exists() else None)
    matchs = {**_load_page(page), **docs}
    # The daily review of the errors and of the winning bets nobody chose (owner, 2026-10-24): its correction of each
    # family of bets is applied to the tickets, the ideas and the player's chances; its sentences go on the page.
    revue = None
    try:
        from sportai.services.revue import build_revue

        joueurs = {p.stem: json.loads(p.read_text()) for p in (folder / "joueur").glob("*.json") if p.stem != "etat"}
        revue = build_revue(matchs, days, joueurs, date.fromisoformat(day))
    except Exception as e:  # the review is a bonus: never lose the pass for it
        print(f"revue unavailable ({str(e)[:160]})", file=sys.stderr)
    legs = legs_from_db(db, date.fromisoformat(day), corrections=(revue or {}).get("corrections")) if db is not None else []
    doc = build_pronostic(legs, matchs, days.get(day), now, passage,
                          date.fromisoformat(day))
    if revue:
        doc["revue"] = revue
    banks = replay_by_ticket({d: v for d, v in days.items() if d < day}, settings)
    days[day] = apply_stakes(doc, settings, {k: b for k, (b, _) in banks.items()})
    doc["bankroll_du_jour"] = replay({d: v for d, v in days.items() if d < day}, settings)[0]  # combined, for reference
    try:  # "Agent joueur pro": decides which tickets / top 3 picks to play and the stakes (0 when it passes)
        if not JOUEUR_PRO:
            raise RuntimeError("désactivé par le propriétaire")
        from sportai.services.joueur import ticket_stakes
        from sportai.services.joueur import write as write_player

        # every validated single with value, Champions League included
        ticket_stakes(doc, write_player(page, out, doc, matchs, settings, now, legs))
    except Exception as e:  # the player is a bonus: never lose the pass for it
        print(f"joueur unavailable ({str(e)[:160]})", file=sys.stderr)
    bilan = build_bilan(matchs, days, settings, now)
    for sub, name, body in (("pronostic", day, doc), ("stats", "bilan", bilan)):
        (Path(out) / sub).mkdir(parents=True, exist_ok=True)
        (Path(out) / sub / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return doc["message"]


def _state_session():
    """The analysis database (read only), None when there is none yet (a basketball-only setup)."""
    if not STATE_DB.exists():
        return None
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=create_engine(f"sqlite:///file:{STATE_DB}?mode=ro&uri=true"))()


def pronostic(page: str | None, out: str, now: datetime, day: str | None = None) -> None:
    db = _state_session()
    print(json.dumps({"pronostic": _pronostic(db, page, {}, out, now, "maj", day)}, ensure_ascii=False))
    if db is not None:
        db.close()


def plan(now: datetime, comps: list[str] | None = None) -> None:
    from sportai.config import get_settings
    from sportai.connectors.http import CachedClient
    from sportai.connectors.the_odds_api import BASE_URL, SPORT_KEYS
    from sportai.services.prematch import journee

    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services import calendrier

    foot, basket = _split(comps or COMPETITIONS)
    today = journee(now)
    by_time: dict[str, set] = defaultdict(set)
    source = "calendrier API-Football"
    if foot:
        try:  # the season calendar of each competition says which matches are played today, and when
            cals = calendrier.refresh(ApiFootballProvider(get_settings().api_football_key), foot, now)
            by_time = calendrier.kickoffs_on(cals, today, now)
            print("semaine:", json.dumps(calendrier.next_days(cals, today)), file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - fall back on the odds feed's event list
            print(f"calendrier unavailable ({str(e)[:160]}), odds events used", file=sys.stderr)
            source = "événements The Odds API"
            http = CachedClient(BASE_URL)
            key = get_settings().the_odds_api_key
            for comp in foot:
                for ev in http.get_json(f"/v4/sports/{SPORT_KEYS[comp]}/events", {"apiKey": key, "dateFormat": "iso"}):
                    ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                    if ko > now and journee(ko) == today:
                        by_time[ko.isoformat()].add(comp)
    basket_times: dict[str, set] = defaultdict(set)
    if basket:
        try:  # the games of the basketball day (to 04:00 Paris the next night), 2 requests per competition
            basket_times = basket_kickoffs(_basket_api(), basket, now)
            source += " + matchs API-Basketball"
        except Exception as e:  # noqa: BLE001 - football is planned all the same
            print(f"basket unavailable ({str(e)[:160]})", file=sys.stderr)
    print(json.dumps(plan_steps(by_time, basket_times, now, source), indent=1))


def basket_kickoffs(api, comps: list[str], now: datetime) -> dict[str, set]:
    """Tip-off times (ISO, UTC) of the basketball day's games still to be played, with their competitions."""
    from sportai.connectors.api_basketball import DROPPED, kickoff
    from sportai.services.prematch import journee

    out: dict[str, set] = defaultdict(set)
    for comp in comps:
        for g in api.games_on([comp], journee(now, "basket")):
            ko = kickoff(g)
            if ko > now and g["status"]["short"] not in DROPPED:
                out[ko.isoformat()].add(comp)
    return out


def plan_steps(by_time: dict[str, set], basket_times: dict[str, set], now: datetime, source: str = "") -> dict:
    """The day's routine steps: football (h3, m45, m15, live every 15 min, final 2 h 15 after the last kick-off)
    and basketball (h3, m45, a light live update every 30 min, final 2 h 30 after the last tip-off)."""
    steps = []
    for times, prematch_steps, live_steps in (
            (by_time, (("h3", timedelta(hours=3)), ("m45", timedelta(minutes=45)), ("m15", timedelta(minutes=15))),
             LIVE_STEPS),
            (basket_times, (("h3", timedelta(hours=3)), ("m45", timedelta(minutes=45))), BASKET_LIVE_STEPS)):
        for ko_s, comps in sorted(times.items()):
            ko = datetime.fromisoformat(ko_s)
            # m15: last look at the odds 15 min before kick-off, so an official ticket still empty at the 45-min pass
            # is filled when its odds cross the threshold late (owner, 2026-10-13: Atalanta went from 1.49 to 1.52).
            for kind, before in prematch_steps:
                at = ko - before
                if at > now:
                    steps.append({"at": at.isoformat(), "kind": kind, "competitions": sorted(comps)})
            # Live follow-up: football steps every 15 min restart the 2-minute loop if the session went idle;
            # basketball steps are single updates (score and quarter).
            for minutes in live_steps:
                at = ko + timedelta(minutes=minutes)
                if at > now:
                    steps.append({"at": at.isoformat(), "kind": "live", "competitions": sorted(comps)})
    # One final step after the last match settles the whole day, one for each sport.
    for times, after in ((by_time, timedelta(hours=2, minutes=15)), (basket_times, BASKET_FINAL_AFTER)):
        if times:
            last = max(datetime.fromisoformat(k) for k in times)
            steps.append({"at": (last + after).isoformat(), "kind": "final",
                          "competitions": sorted(set().union(*times.values()))})
    merged: dict[tuple[str, str], set] = defaultdict(set)
    for st in steps:
        merged[(st["at"], st["kind"])].update(st["competitions"])
    steps = [{"at": at, "kind": kind, "competitions": sorted(c)} for (at, kind), c in sorted(merged.items())]
    kickoffs: dict[str, set] = defaultdict(set)
    for times in (by_time, basket_times):
        for k, v in times.items():
            kickoffs[k] |= set(v)
    return {"source": source, "kickoffs": {k: sorted(v) for k, v in sorted(kickoffs.items())}, "steps": steps}


def prematch(comps: list[str], page: str | None, out: str, now: datetime, goals: bool = False,
             morning: bool = False) -> None:
    comps, basket = _split(comps)
    bnotes = []
    if basket:  # NBA / WNBA: their own collections, written first (services/basket.py)
        bnotes = _basket_prematch(basket, page, out, now, "matin" if morning else "passage", final_step=not comps)
        if not comps:
            return
    current = _load_page(page)
    work = Path(tempfile.mkdtemp()) / "agent.db"
    if STATE_DB.exists():
        shutil.copy(STATE_DB, work)
    os.environ["DATABASE_URL"] = f"sqlite:///{work}"
    os.environ["DATA_MODE"] = "real"
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
    from sportai import manage
    from sportai.config import get_settings
    from sportai.db.session import session_factory
    from sportai.services.dashboard import build_match_docs
    from sportai.services.runs import execute_prematch

    db = session_factory()()
    manage.init(db)
    docs, notes, fixtures = {}, bnotes, []
    for comp in comps:
        run = execute_prematch(db, get_settings(), comp, "routine", now=now, goal_markets=goals,
                              max_hours_ahead=24 if morning else None)
        notes.append(f"{comp} {run.status}" + (f" ({run.status_detail[:120]})" if run.status_detail else ""))
        if run.status == "completed":
            new = build_match_docs(db, run, {**current, **docs})
            docs.update(new)
            fixtures += run.fixtures or []
    try:  # before the tickets: a high rotation risk (next match) keeps some bets out of them
        _context(comps, docs, current, now)
    except Exception as e:  # context is a bonus: never lose the pass for it
        notes.append(f"contexte unavailable ({str(e)[:120]})")
    try:
        notes.append("pronostic: " + _pronostic(db, page, docs, out, now, "matin" if morning else "passage"))
    except Exception as e:  # the tab is a bonus: never lose the pass for it
        notes.append(f"pronostic unavailable ({str(e)[:120]})")
    db.close()
    try:
        docs.update(_lineups(comps, docs, fixtures, now))
    except Exception as e:  # line-ups are a bonus: never lose the pass for them
        notes.append(f"line-ups unavailable ({str(e)[:120]})")
    try:
        _form(comps, docs, current, now)
    except Exception as e:  # form is a bonus: never lose the pass for it
        notes.append(f"forme unavailable ({str(e)[:120]})")
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(work, STATE_DB)
    n_sel = sum(len(d.get("selections") or []) for d in docs.values())
    _write(out, docs, f"{len(docs)} match(s) mis à jour, {n_sel} sélection(s)", now)
    print("runs:", "; ".join(notes), file=sys.stderr)


def _basket_prematch(comps: list[str], page: str | None, out: str, now: datetime, passage: str,
                     final_step: bool) -> list[str]:
    """NBA / WNBA: every game of the basketball day not started yet (services/basket.py), written to
    out/basket_matchs and out/basket_pronostic/<basketball day>. Returns the notes."""
    from sportai.services import basket
    from sportai.services.prematch import journee

    current = _load_collection(page, "basket_matchs")
    try:
        docs, offers, notes = basket.prematch(_basket_api(), comps, current, now)
    except Exception as e:  # noqa: BLE001 - football is analysed all the same
        docs, offers, notes = {}, {}, [f"basket failed ({str(e)[:120]})"]
    _write_basket(page, out, now, docs, offers, passage, [f"{journee(now, 'basket'):%Y-%m-%d}"],
                  f"{len(docs)} match(s) de basket mis à jour", final_step)
    if final_step:
        print("runs:", "; ".join(notes), file=sys.stderr)
    return notes


def _write_basket(page: str | None, out: str, now: datetime, docs: dict[str, dict], offers: dict[str, list[dict]],
                  passage: str, days: list[str], message: str, final_step: bool) -> None:
    """The basketball collections only: out/basket_matchs/<game id>.json (the documents of this step) and
    out/basket_pronostic/<day>.json for each day given (bets fixed 45 min before tip-off reused from the page's
    current document, settled once their game is over). final_step: a basketball-only step, which also writes
    etat.json and manifest.json (never out/matchs)."""
    from sportai.services import basket

    current = _load_collection(page, "basket_matchs")
    pronos = _load_collection(page, "basket_pronostic")
    matchs = {**current, **docs}
    written = {}
    for day in sorted(set(days)):
        try:
            written[day] = basket.build_pronostic(day, matchs, offers, pronos.get(day), now, passage)
        except Exception as e:  # noqa: BLE001 - the documents are written all the same
            print(f"basket pronostic {day} unavailable ({str(e)[:160]})", file=sys.stderr)
    for sub, items in (("basket_matchs", docs), ("basket_pronostic", written)):
        if items:
            (Path(out) / sub).mkdir(parents=True, exist_ok=True)
        for key, body in items.items():
            (Path(out) / sub / f"{key}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    listing = {"basket_matchs": sorted(docs), "basket_pronostic": sorted(written)}
    Path(out).mkdir(parents=True, exist_ok=True)
    if final_step:
        (Path(out) / "etat.json").write_text(json.dumps({"maj": now.isoformat(), "message": message},
                                                        ensure_ascii=False))
        (Path(out) / "manifest.json").write_text(json.dumps({"matchs": [], **listing, "message": message},
                                                            ensure_ascii=False))
    else:  # a mixed step: football's _write keeps these keys in its manifest
        (Path(out) / "manifest.json").write_text(json.dumps(listing, ensure_ascii=False))
    print(json.dumps({"written": listing, "message": message}, ensure_ascii=False))


def _form(comps: list[str], docs: dict[str, dict], current: dict[str, dict], now: datetime) -> None:
    """Last 5 results of each side, all competitions (European cups included), and their meetings: from the
    morning pass on, so every match shows them before kick-off (owner, 2026-10-14). Kept once fetched."""
    from sportai.config import get_settings
    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services.live_page import find_doc, head_to_head_rows, recent_form

    for key, d in docs.items():  # a pass rebuilds the document: keep what an earlier pass fetched
        for field in ("forme", "face_a_face"):
            if field not in d and field in (current.get(key) or {}):
                d[field] = current[key][field]
    days = {datetime.fromisoformat(d["coup_envoi"]) for d in docs.values()
            if "forme" not in d and d.get("coup_envoi") and datetime.fromisoformat(d["coup_envoi"]) > now}
    if not days:
        return
    from sportai.services.prematch import journee

    api = ApiFootballProvider(get_settings().api_football_key)
    for day in sorted({journee(k) for k in days}):
        for fx in api.fixtures_on(comps, day):
            key = find_doc(docs, f"{day:%Y-%m-%d}", fx["teams"]["home"]["name"], fx["teams"]["away"]["name"])
            if not key or "forme" in docs[key]:
                continue
            h, a = fx["teams"]["home"]["id"], fx["teams"]["away"]["id"]
            docs[key]["face_a_face"] = head_to_head_rows(api.head_to_head(h, a))
            docs[key]["forme"] = {"domicile": recent_form(api.team_last(h), h),
                                  "exterieur": recent_form(api.team_last(a), a)}


def _context(comps: list[str], docs: dict[str, dict], current: dict[str, dict], now: datetime) -> None:
    """Referee, standings, streaks and weather of the pass's matches still to play (sportai/services/contexte.py).
    Each pass refreshes them (the referee is often named late, the forecast improves); the streaks are fetched
    once a day per match, the teams' next matches too (rotation risk, docs/15-prochain-match.md). No probability
    uses any of it; a high rotation risk only keeps some bets out of the tickets and ideas (pronostic.py)."""
    from sportai.config import get_settings
    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services.contexte import ContextSources, build_context
    from sportai.services.live_page import find_doc
    from sportai.services.prematch import journee

    for key, d in docs.items():  # a pass rebuilds the document: keep what an earlier pass found
        if "contexte" not in d and "contexte" in (current.get(key) or {}):
            d["contexte"] = current[key]["contexte"]
    days = {journee(datetime.fromisoformat(d["coup_envoi"])) for d in docs.values()
            if d.get("coup_envoi") and datetime.fromisoformat(d["coup_envoi"]) > now}
    if not days:
        return
    api = ApiFootballProvider(get_settings().api_football_key)
    src = ContextSources(api, now)
    for day in sorted(days):
        for fx in api.fixtures_on(comps, day):
            if fx["fixture"]["status"]["short"] not in ("NS", "TBD"):
                continue
            key = find_doc(docs, f"{day:%Y-%m-%d}", fx["teams"]["home"]["name"], fx["teams"]["away"]["name"])
            if not key:
                continue
            prev = docs[key].get("contexte") or (current.get(key) or {}).get("contexte") or {}
            last_h = last_a = None
            same_day = bool(prev.get("maj")) and journee(datetime.fromisoformat(prev["maj"])) == journee(now)
            if not prev.get("series") or not same_day:
                try:
                    last_h, last_a = api.team_last(fx["teams"]["home"]["id"]), api.team_last(fx["teams"]["away"]["id"])
                except Exception as e:  # noqa: BLE001 - keep yesterday's streaks
                    print(f"séries unavailable for {key} ({str(e)[:120]})", file=sys.stderr)
            docs[key]["contexte"] = build_context(src, fx, prev, last_h, last_a,
                                                  next_refresh=not (prev.get("prochain_match") and same_day))
    if src.weather_down:
        print("météo unavailable (Open-Meteo unreachable)", file=sys.stderr)


def _lineups(comps: list[str], docs: dict[str, dict], fixtures: list[dict], now: datetime,
             window: timedelta = timedelta(minutes=75)) -> dict[str, dict]:
    """Official line-ups, notable absentees and goalscorer estimates for the matches close to kick-off."""
    from sportai.config import get_settings
    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services.lineups import scenario_with_squads
    from sportai.services.live_page import find_doc
    from sportai.services.prematch import journee

    soon = {k: d for k, d in docs.items()
            if d.get("coup_envoi") and now < datetime.fromisoformat(d["coup_envoi"]) <= now + window}
    if not soon:
        return {}
    api = ApiFootballProvider(get_settings().api_football_key)
    day = journee(now)
    out = {}
    for fx in api.fixtures_on(comps, day):
        home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
        key = find_doc(soon, f"{day:%Y-%m-%d}", home, away)
        if not key:
            continue
        doc = dict(soon[key])
        _fill_lineups(api, fx, doc, fixtures)
        doc["scenario"] = scenario_with_squads(doc)
        out[key] = doc
    return out


def _fill_lineups(api, fx: dict, doc: dict, fixtures: list[dict], ttl: float = 300) -> bool:
    """Official line-ups, notable absentees and goalscorer estimates of one fixture into its page document."""
    from sportai.services.lineups import fill_compositions
    from sportai.services.live_page import pre_match_rates

    pre = pre_match_rates(fixtures, doc["domicile"], doc["exterieur"])
    return fill_compositions(doc, fx, api.lineups(fx["fixture"]["id"], ttl=ttl), api.team_players,
                             pre[0] if pre else None)


def _squad_news(api, fx: dict, doc: dict, fixtures: list[dict], now: datetime) -> bool:
    """Before kick-off: the day's absentees (once a day) and the official line-ups (polled from 75 min before
    kick-off until both are out), then the scenario rebuilt with them. Returns True when the document changed."""
    from sportai.services.lineups import absentees, lineup_published, regular_starters, scenario_with_squads
    from sportai.services.prematch import journee

    changed = False
    today = f"{journee(now):%Y-%m-%d}"
    ko = fx["fixture"].get("timestamp")
    ko = datetime.fromtimestamp(ko, timezone.utc) if ko else None
    if ko and now < ko <= now + timedelta(minutes=75) and not lineup_published(doc):
        before = doc.get("compositions")
        try:
            _fill_lineups(api, fx, doc, fixtures, ttl=120)
            changed = doc.get("compositions") != before
        except Exception as e:  # noqa: BLE001 - extra context, never break the live follow-up
            print(f"line-ups unavailable for {doc.get('domicile')} ({str(e)[:120]})", file=sys.stderr)
    if (doc.get("absents") or {}).get("jour") != today:
        try:
            season, league = fx["league"]["season"], fx["league"].get("id")
            regulars = {t["id"]: regular_starters(api.team_players(t["id"], season), t["id"], league)
                        for t in (fx["teams"]["home"], fx["teams"]["away"])}
            doc["absents"] = {"jour": today, **absentees(api.injuries(fx["fixture"]["id"]), fx, regulars)}
            changed = True
        except Exception as e:  # noqa: BLE001 - tried once a day only, so a failure does not eat the quota
            doc["absents"] = {"jour": today, "indisponible": True}
            changed = True
            print(f"absents unavailable for {doc.get('domicile')} ({str(e)[:120]})", file=sys.stderr)
    text = scenario_with_squads(doc)
    if text != doc.get("scenario"):
        doc["scenario"] = text
        changed = True
    return changed


def scores(comps: list[str], page: str | None, out: str, final: bool, now: datetime) -> None:
    comps, basket = _split(comps)
    if basket:  # NBA / WNBA: results from API-Basketball (never The Odds API), their own collections
        _basket_follow(basket, page, out, now, "resultats" if final else "maj", final, not comps)
        if not comps:
            return
    from sportai.config import get_settings
    from sportai.connectors.the_odds_api import TheOddsApiProvider
    from sportai.services.dashboard import apply_scores

    s = get_settings()
    odds = TheOddsApiProvider(s.the_odds_api_key, regions=s.odds_regions)
    current, docs = _load_page(page), {}
    for comp in comps:
        docs.update(apply_scores(odds.scores(comp, completed_too=final), current, now))
    if final:
        _half_time_scores(comps, current, docs)
    if final and STATE_DB.exists():
        db = _state_session()
        for day in sorted({d["jour"] for d in docs.values() if d.get("jour")}):  # a late final runs past midnight
            print("pronostic:", _pronostic(db, page, docs, out, now, "resultats", day), file=sys.stderr)
        db.close()
    _write(out, docs, f"scores mis à jour pour {len(docs)} match(s)", now)
    print("quota:", odds.last_quota, file=sys.stderr)


def _half_time_scores(comps: list[str], current: dict, docs: dict) -> None:
    """The final pass marks matches finished from The Odds API, which has no half-time score: read it from
    API-Football for every finished match still without one, so the half-time bets settle."""
    from datetime import date as _date

    from sportai.config import get_settings
    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services.live_page import find_doc

    merged = {**current, **docs}
    days = {d["jour"] for d in merged.values() if d.get("jour") and d.get("statut") == "termine"
            and not d.get("score_mi_temps") and d.get("sport") != "basket"}
    if not days:
        return
    api = ApiFootballProvider(get_settings().api_football_key)
    for day in sorted(days):
        try:
            fixtures = api.fixtures_on(comps, _date.fromisoformat(day))
        except Exception as e:  # noqa: BLE001 - extra settlement data, never break the final pass
            print(f"half-time scores {day}: {str(e)[:120]}", file=sys.stderr)
            continue
        for fx in fixtures:
            key = find_doc(merged, day, fx["teams"]["home"]["name"], fx["teams"]["away"]["name"])
            ht = (fx.get("score") or {}).get("halftime") or {}
            if not key or merged[key].get("score_mi_temps") or ht.get("home") is None or ht.get("away") is None:
                continue
            merged[key] = docs[key] = {**merged[key], "score_mi_temps": f"{int(ht['home'])} - {int(ht['away'])}"}


def _basket_final_days(current: dict[str, dict], bdocs: dict[str, dict], now: datetime) -> list[str]:
    """The basketball days a final step settles: those of the games it just updated, and the night that just ended
    (the final runs around 05:00-06:30 Paris, already on the next basketball day) or the current one."""
    from sportai.services.prematch import journee

    today = journee(now, "basket")
    recent = {f"{today - timedelta(days=1):%Y-%m-%d}", f"{today:%Y-%m-%d}"}
    return sorted({d["jour"] for d in bdocs.values() if d.get("jour")}
                  | {d["jour"] for d in current.values() if d.get("sport") == "basket" and d.get("jour") in recent})


def _basket_follow(comps: list[str], page: str | None, out: str, now: datetime, passage: str, final: bool,
                   final_step: bool) -> dict[str, dict]:
    """Statut, score and quarter scores of the page's basketball games (services/basket.py, one or two requests a
    day), and their retained bets settled in basket_pronostic. Returns the updated documents."""
    from sportai.services import basket

    current = _load_collection(page, "basket_matchs")
    try:
        docs = basket.update_scores(_basket_api(), comps, current, now)
    except Exception as e:  # noqa: BLE001 - never lose the football follow-up for it
        print(f"basket scores unavailable ({str(e)[:160]})", file=sys.stderr)
        docs = {}
    days = (_basket_final_days(current, docs, now) if final
            else sorted({d["jour"] for d in docs.values() if d.get("jour")}))
    n_live = sum(d.get("statut") == "en_direct" for d in docs.values())
    n_done = sum(d.get("statut") == "termine" for d in docs.values())
    _write_basket(page, out, now, docs, {}, passage, days,
                  f"Basket : {n_live} match(s) en cours, {n_done} terminé(s)", final_step)
    return docs


def live(comps: list[str], page: str | None, out: str, now: datetime) -> None:
    """Follow the page's matches under way (and those that just finished): football with API-Football,
    basketball (score and quarter only) with API-Basketball."""
    comps, basket = _split(comps)
    if basket:
        _basket_follow(basket, page, out, now, "direct", False, not comps)
        if not comps:
            return
    _live_foot(comps, page, out, now, {})


def _live_foot(comps: list[str], page: str | None, out: str, now: datetime, docs: dict[str, dict]) -> None:
    import sqlite3

    from sportai.config import get_settings
    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services.joueur import cibles
    from sportai.services.live_page import (FINISHED, find_doc, fixture_identity, head_to_head_rows, live_doc,
                                             pre_match_rates, recent_form, stats_by_side)
    from sportai.services.prematch import journee

    api = ApiFootballProvider(get_settings().api_football_key)
    current = _load_page(page)
    fixtures: list[dict] = []
    if STATE_DB.exists():
        con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        for (raw,) in con.execute("SELECT fixtures FROM analysis_runs WHERE status = 'completed' AND fixtures IS NOT NULL "
                                  "ORDER BY started_at DESC"):
            fixtures += json.loads(raw) if isinstance(raw, str) else raw
        con.close()
    from datetime import date as _date
    # Today, plus the day of any page match still open (a late match runs past midnight in Paris).
    days = {journee(now)} | {_date.fromisoformat(d["jour"]) for d in current.values()
                                                 if d.get("jour") and d.get("statut") != "termine"
                                                 and d.get("sport") != "basket"}
    from sportai.connectors.base import ProviderUnavailable
    raw = []
    for day in sorted(days):  # one day failing upstream (2026-10-21: HTTP 500 on today's dates) must not block the others
        try:
            raw += [(day, fx) for fx in api.fixtures_on(comps, day)]
        except ProviderUnavailable as e:
            print(f"fixtures {day}: {str(e)[:120]}", file=sys.stderr)
    if not raw and days:
        raise ProviderUnavailable("no day of fixtures could be read")
    for day, fx in raw:
        short = fx["fixture"]["status"]["short"]
        home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
        key = find_doc(current, f"{day:%Y-%m-%d}", home, away)
        ident = fixture_identity(fx)
        if key and any(current[key].get(k) != v for k, v in ident.items()):  # crests for the page, once
            current[key] = docs[key] = {**current[key], **ident}
        if key and short in ("NS", "TBD"):  # a clear position on every match, even without a value bet
            from sportai.services.position import position
            pos = position(current[key])
            if pos and pos != current[key].get("position"):
                current[key] = docs[key] = {**current[key], "position": pos}
        if key and short in ("NS", "TBD") and "forme" not in current[key]:  # form and meetings, once a day
            try:
                h, a = fx["teams"]["home"]["id"], fx["teams"]["away"]["id"]
                current[key] = docs[key] = {
                    **current[key], "face_a_face": head_to_head_rows(api.head_to_head(h, a)),
                    "forme": {"domicile": recent_form(api.team_last(h), h),
                              "exterieur": recent_form(api.team_last(a), a)}}
            except Exception as e:  # noqa: BLE001 - extra context, never break the live follow-up
                print(f"forme unavailable for {key} ({str(e)[:120]})", file=sys.stderr)
        if key and short in ("NS", "TBD"):  # absentees and starting XIs, a few requests per match
            doc = dict(current[key])
            if _squad_news(api, fx, doc, fixtures, now):
                current[key] = docs[key] = doc
        if short in ("NS", "TBD", "PST", "CANC"):
            continue
        if not key or current[key].get("statut") == "termine":
            continue
        snap, raw_stats = api.live_details(fx)
        odds = None if fx["fixture"]["status"]["short"] in FINISHED else api.live_odds(fx["fixture"]["id"])
        lineups = None
        if not (current[key].get("feuille_de_match") or {}).get("titulaires"):  # once per match: settles scorer bets
            try:
                lineups = api.lineups(fx["fixture"]["id"], ttl=3600) or None
            except Exception as e:  # noqa: BLE001 - extra context, never break the live follow-up
                print(f"line-ups unavailable for {key} ({str(e)[:120]})", file=sys.stderr)
        players = None
        if odds and snap.minute >= 30 and cibles(current[key]):
            try:  # a targeted scorer: is he in his match? (owner 2026-10-16), one request per match and cycle
                players = api.fixture_players(fx["fixture"]["id"])
            except Exception as e:  # noqa: BLE001 - extra context, never break the live follow-up
                print(f"players unavailable for {key} ({str(e)[:120]})", file=sys.stderr)
        doc = live_doc(current[key], fx, snap, stats_by_side(raw_stats, fx["teams"]["home"]["id"]),
                       pre_match_rates(fixtures, current[key]["domicile"], current[key]["exterieur"]), now, odds,
                       lineups, players)
        docs[key] = doc
    n_live = sum(d["statut"] == "en_direct" for d in docs.values())
    try:  # the "Joueur pro" player's bets followed live
        if not JOUEUR_PRO:
            raise RuntimeError("désactivé par le propriétaire")
        from sportai.services.joueur import live_write
        live_write(page, out, {**current, **docs}, now)
    except Exception as e:
        print(f"joueur live unavailable ({str(e)[:160]})", file=sys.stderr)
    n_done = sum(d["statut"] == "termine" for d in docs.values())
    _write(out, docs, f"Direct : {n_live} match(s) en cours, {n_done} terminé(s)", now)


def goals_refresh(now: datetime, max_requests: int, download: bool = True) -> None:
    from sportai.config import get_settings
    from sportai.services.goals_refresh import run_refresh

    report = run_refresh(now, get_settings().api_football_key, max_requests=max_requests, download=download)
    try:  # referees' card profiles from the same cached matches (no request), read by every pass of the day
        from sportai.services.contexte import refresh_referee_index

        report["arbitres"] = refresh_referee_index(now)
    except Exception as e:  # noqa: BLE001 - context only, never lose the goals model for it
        report["arbitres"] = f"unavailable ({str(e)[:120]})"
    print(json.dumps(report, ensure_ascii=False, indent=1))


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--competitions", nargs="+", choices=COMPETITIONS, help="only these (the page's choice)")
    pr = sub.add_parser("pronostic")
    pr.add_argument("--page")
    pr.add_argument("--out", required=True)
    pr.add_argument("--day", help="YYYY-MM-DD, default today (Paris); a late match ending after midnight settles its own day")
    gr = sub.add_parser("goals-refresh")
    gr.add_argument("--max-requests", type=int, default=60)
    gr.add_argument("--no-download", action="store_true", help="refit from the cache only, no API request")
    lv = sub.add_parser("live")
    lv.add_argument("competitions", nargs="+", choices=COMPETITIONS)
    lv.add_argument("--page")
    lv.add_argument("--out", required=True)
    for name in ("prematch", "scores"):
        p = sub.add_parser(name)
        p.add_argument("competitions", nargs="+", choices=COMPETITIONS)
        p.add_argument("--page")
        p.add_argument("--out", required=True)
        if name == "scores":
            p.add_argument("--final", action="store_true")
        else:
            p.add_argument("--goals", action="store_true",
                           help="The Odds API fallback only: also fetch both-teams-to-score and totals, match by match "
                                "(2 credits per match); API-Football odds already hold every market")
            p.add_argument("--matin", action="store_true", help="8 am pass: every match of the day, not only the next 3 h")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    if a.cmd == "plan":
        plan(now, a.competitions)
    elif a.cmd == "prematch":
        prematch(a.competitions, a.page, a.out, now, a.goals, a.matin)
    elif a.cmd == "goals-refresh":
        goals_refresh(now, a.max_requests, not a.no_download)
    elif a.cmd == "pronostic":
        pronostic(a.page, a.out, now, a.day)
    elif a.cmd == "live":
        live(a.competitions, a.page, a.out, now)
    else:
        scores(a.competitions, a.page, a.out, a.final, now)


if __name__ == "__main__":
    main()
