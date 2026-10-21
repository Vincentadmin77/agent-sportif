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
page, in the tickets and for the player. The Odds API is only the fallback when API-Football has no odds; --goals
then fetches its btts / totals odds match by match.

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
COMPETITIONS = ["FR1", "EN1", "ES1", "DE1", "IT1", "BR1", "UCL", "UEL"]


def _load_page(folder: str | None) -> dict[str, dict]:
    if not folder or not (Path(folder) / "matchs").exists():
        return {}
    return {p.stem: json.loads(p.read_text()) for p in (Path(folder) / "matchs").glob("*.json")}


def _write(out: str, docs: dict[str, dict], message: str, now: datetime) -> None:
    d = Path(out) / "matchs"
    d.mkdir(parents=True, exist_ok=True)
    for key, doc in docs.items():
        (d / f"{key}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    (Path(out) / "etat.json").write_text(json.dumps({"maj": now.isoformat(), "message": message}, ensure_ascii=False))
    (Path(out) / "manifest.json").write_text(json.dumps({"matchs": sorted(docs), "message": message}, ensure_ascii=False))
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
    legs = legs_from_db(db, date.fromisoformat(day))
    doc = build_pronostic(legs, matchs, days.get(day), now, passage,
                          date.fromisoformat(day))
    banks = replay_by_ticket({d: v for d, v in days.items() if d < day}, settings)
    days[day] = apply_stakes(doc, settings, {k: b for k, (b, _) in banks.items()})
    doc["bankroll_du_jour"] = replay({d: v for d, v in days.items() if d < day}, settings)[0]  # combined, for reference
    try:  # "Agent joueur pro": decides which tickets / top 3 picks to play and the stakes (0 when it passes)
        from sportai.services.joueur import ticket_stakes
        from sportai.services.joueur import write as write_player

        # every validated single with value, Champions League included
        ticket_stakes(doc, write_player(page, out, doc, matchs, settings, now, legs))
    except Exception as e:  # the player is a bonus: never lose the pass for it
        print(f"joueur unavailable ({str(e)[:160]})", file=sys.stderr)
    try:  # "Le chasseur alpin": a montante on one coherent single a day, its own gauge (collection chasseur)
        from sportai.services.chasseur import write as write_chasseur

        write_chasseur(page, out, legs, matchs, now, day)
    except Exception as e:  # a bonus too: never lose the pass for it
        print(f"chasseur unavailable ({str(e)[:160]})", file=sys.stderr)
    bilan = build_bilan(matchs, days, settings, now)
    for sub, name, body in (("pronostic", day, doc), ("stats", "bilan", bilan)):
        (Path(out) / sub).mkdir(parents=True, exist_ok=True)
        (Path(out) / sub / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return doc["message"]


def _state_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=create_engine(f"sqlite:///file:{STATE_DB}?mode=ro&uri=true"))()


def pronostic(page: str | None, out: str, now: datetime, day: str | None = None) -> None:
    db = _state_session()
    print(json.dumps({"pronostic": _pronostic(db, page, {}, out, now, "maj", day)}, ensure_ascii=False))
    db.close()


def plan(now: datetime, comps: list[str] | None = None) -> None:
    from sportai.config import get_settings
    from sportai.connectors.http import CachedClient
    from sportai.connectors.the_odds_api import BASE_URL, SPORT_KEYS
    from sportai.services.prematch import journee

    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services import calendrier

    comps = comps or COMPETITIONS
    today = journee(now)
    by_time: dict[str, set] = defaultdict(set)
    source = "calendrier API-Football"
    try:  # the season calendar of each competition says which matches are played today, and when
        cals = calendrier.refresh(ApiFootballProvider(get_settings().api_football_key), comps, now)
        by_time = calendrier.kickoffs_on(cals, today, now)
        print("semaine:", json.dumps(calendrier.next_days(cals, today)), file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - fall back on the odds feed's event list
        print(f"calendrier unavailable ({str(e)[:160]}), odds events used", file=sys.stderr)
        source = "événements The Odds API"
        http = CachedClient(BASE_URL)
        key = get_settings().the_odds_api_key
        for comp in comps:
            for ev in http.get_json(f"/v4/sports/{SPORT_KEYS[comp]}/events", {"apiKey": key, "dateFormat": "iso"}):
                ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                if ko > now and journee(ko) == today:
                    by_time[ko.isoformat()].add(comp)
    steps = []
    for ko_s, comps in sorted(by_time.items()):
        ko = datetime.fromisoformat(ko_s)
        # m15: last look at the odds 15 min before kick-off, so an official ticket still empty at the 45-min pass
        # is filled when its odds cross the threshold late (owner, 2026-10-13: Atalanta went from 1.49 to 1.52).
        for kind, at in (("h3", ko - timedelta(hours=3)), ("m45", ko - timedelta(minutes=45)),
                         ("m15", ko - timedelta(minutes=15))):
            if at > now:
                steps.append({"at": at.isoformat(), "kind": kind, "kickoff": ko_s, "competitions": sorted(comps)})
        # Live follow-up (API-Football): steps every 15 min restart the 2-minute loop if the session went idle.
        for minutes in LIVE_STEPS:
            at = ko + timedelta(minutes=minutes)
            if at > now:
                steps.append({"at": at.isoformat(), "kind": "live", "kickoff": ko_s, "competitions": sorted(comps)})
    if by_time:
        # One final step after the last match settles the whole day (2 credits per competition).
        last = max(datetime.fromisoformat(k) for k in by_time)
        steps.append({"at": (last + timedelta(hours=2, minutes=15)).isoformat(), "kind": "final",
                      "competitions": sorted(set().union(*by_time.values()))})
    merged: dict[tuple[str, str], set] = defaultdict(set)
    for st in steps:
        merged[(st["at"], st["kind"])].update(st["competitions"])
    steps = [{"at": at, "kind": kind, "competitions": sorted(c)} for (at, kind), c in sorted(merged.items())]
    print(json.dumps({"source": source, "kickoffs": {k: sorted(v) for k, v in sorted(by_time.items())},
                      "steps": steps}, indent=1))


def prematch(comps: list[str], page: str | None, out: str, now: datetime, goals: bool = False,
             morning: bool = False) -> None:
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
    current, docs, notes, fixtures = _load_page(page), {}, [], []
    for comp in comps:
        run = execute_prematch(db, get_settings(), comp, "routine", now=now, goal_markets=goals,
                              max_hours_ahead=24 if morning else None)
        notes.append(f"{comp} {run.status}" + (f" ({run.status_detail[:120]})" if run.status_detail else ""))
        if run.status == "completed":
            new = build_match_docs(db, run, {**current, **docs})
            docs.update(new)
            fixtures += run.fixtures or []
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
    try:
        _context(comps, docs, current, now)
    except Exception as e:  # context is a bonus: never lose the pass for it
        notes.append(f"contexte unavailable ({str(e)[:120]})")
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(work, STATE_DB)
    n_sel = sum(len(d.get("selections") or []) for d in docs.values())
    _write(out, docs, f"{len(docs)} match(s) mis à jour, {n_sel} sélection(s)", now)
    print("runs:", "; ".join(notes), file=sys.stderr)


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
    once a day per match. Context only: no probability uses it yet."""
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
            if not prev.get("series") or journee(datetime.fromisoformat(prev["maj"])) != journee(now):
                try:
                    last_h, last_a = api.team_last(fx["teams"]["home"]["id"]), api.team_last(fx["teams"]["away"]["id"])
                except Exception as e:  # noqa: BLE001 - keep yesterday's streaks
                    print(f"séries unavailable for {key} ({str(e)[:120]})", file=sys.stderr)
            docs[key]["contexte"] = build_context(src, fx, prev, last_h, last_a)
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
    from sportai.config import get_settings
    from sportai.connectors.the_odds_api import TheOddsApiProvider
    from sportai.services.dashboard import apply_scores

    s = get_settings()
    odds = TheOddsApiProvider(s.the_odds_api_key, regions=s.odds_regions)
    current, docs = _load_page(page), {}
    for comp in comps:
        docs.update(apply_scores(odds.scores(comp, completed_too=final), current, now))
    if final and STATE_DB.exists():
        db = _state_session()
        for day in sorted({d["jour"] for d in docs.values() if d.get("jour")}):  # a late final runs past midnight
            print("pronostic:", _pronostic(db, page, docs, out, now, "resultats", day), file=sys.stderr)
        db.close()
    _write(out, docs, f"scores mis à jour pour {len(docs)} match(s)", now)
    print("quota:", odds.last_quota, file=sys.stderr)


def live(comps: list[str], page: str | None, out: str, now: datetime) -> None:
    """Follow the page's matches under way (and those that just finished) with API-Football."""
    import sqlite3

    from sportai.config import get_settings
    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services.joueur import cibles
    from sportai.services.live_page import (FINISHED, find_doc, fixture_identity, head_to_head_rows, live_doc,
                                             pre_match_rates, recent_form, stats_by_side)
    from sportai.services.prematch import journee

    api = ApiFootballProvider(get_settings().api_football_key)
    current, docs = _load_page(page), {}
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
                                                 if d.get("jour") and d.get("statut") != "termine"}
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
        from sportai.services.joueur import live_write
        live_write(page, out, {**current, **docs}, now)
    except Exception as e:
        print(f"joueur live unavailable ({str(e)[:160]})", file=sys.stderr)
    try:  # "Le chasseur alpin": its pick frozen 30 min before kick-off, followed live, settled at the final whistle
        from sportai.services.chasseur import live_write as chasseur_live

        chasseur_live(page, out, {**current, **docs}, now)
    except Exception as e:
        print(f"chasseur live unavailable ({str(e)[:160]})", file=sys.stderr)
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
