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
COMPETITIONS = ["FR1", "EN1", "ES1", "DE1", "IT1", "BR1", "UCL"]


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

    from sportai.services.bilan import apply_stakes, bankroll_settings, build_bilan, replay
    from sportai.services.prematch import LOCAL_TZ
    from sportai.services.pronostic import build_pronostic, legs_from_db

    day = day or f"{now.astimezone(LOCAL_TZ):%Y-%m-%d}"
    folder = Path(page or "/nonexistent")
    days = {p.stem: json.loads(p.read_text()) for p in (folder / "pronostic").glob("*.json")}
    raw = folder / "reglages" / "bankroll.json"
    settings = bankroll_settings(json.loads(raw.read_text()) if raw.exists() else None)
    matchs = {**_load_page(page), **docs}
    doc = build_pronostic(legs_from_db(db, date.fromisoformat(day)), matchs, days.get(day), now, passage,
                          date.fromisoformat(day))
    bank_at_start, _ = replay({d: v for d, v in days.items() if d < day}, settings)
    days[day] = apply_stakes(doc, settings, bank_at_start)
    etat = None
    try:  # "Agent joueur pro": decides which tickets / top 3 picks to play and the stakes (0 when it passes)
        from sportai.services.joueur import ticket_stakes
        from sportai.services.joueur import write as write_player

        ticket_stakes(doc, write_player(page, out, doc, matchs, settings, now))
        etat = json.loads((Path(out) / "joueur" / "etat.json").read_text())
    except Exception as e:  # the player is a bonus: never lose the pass for it
        print(f"joueur unavailable ({str(e)[:160]})", file=sys.stderr)
    bilan = build_bilan(matchs, days, settings, now)
    if etat and isinstance(etat.get("bankroll"), dict):  # the bankroll follows the player's actual bets
        bilan["bankroll"].update({k: v for k, v in etat["bankroll"].items() if k in ("actuelle", "historique")})
    for sub, name, body in (("pronostic", day, doc), ("stats", "bilan", bilan)):
        (Path(out) / sub).mkdir(parents=True, exist_ok=True)
        (Path(out) / sub / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return doc["message"]


def _state_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=create_engine(f"sqlite:///file:{STATE_DB}?mode=ro&uri=true"))()


def pronostic(page: str | None, out: str, now: datetime) -> None:
    db = _state_session()
    print(json.dumps({"pronostic": _pronostic(db, page, {}, out, now, "maj")}, ensure_ascii=False))
    db.close()


def plan(now: datetime, comps: list[str] | None = None) -> None:
    from sportai.config import get_settings
    from sportai.connectors.http import CachedClient
    from sportai.connectors.the_odds_api import BASE_URL, SPORT_KEYS
    from sportai.services.prematch import LOCAL_TZ

    http = CachedClient(BASE_URL)
    key = get_settings().the_odds_api_key
    by_time: dict[str, set] = defaultdict(set)
    for comp in comps or COMPETITIONS:
        for ev in http.get_json(f"/v4/sports/{SPORT_KEYS[comp]}/events", {"apiKey": key, "dateFormat": "iso"}):
            ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if ko > now and ko.astimezone(LOCAL_TZ).date() == now.astimezone(LOCAL_TZ).date():
                by_time[ko.isoformat()].add(comp)
    steps = []
    for ko_s, comps in sorted(by_time.items()):
        ko = datetime.fromisoformat(ko_s)
        for kind, at in (("h3", ko - timedelta(hours=3)), ("m45", ko - timedelta(minutes=45))):
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
    print(json.dumps({"kickoffs": {k: sorted(v) for k, v in sorted(by_time.items())}, "steps": steps}, indent=1))


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
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(work, STATE_DB)
    n_sel = sum(len(d.get("selections") or []) for d in docs.values())
    _write(out, docs, f"{len(docs)} match(s) mis à jour, {n_sel} sélection(s)", now)
    print("runs:", "; ".join(notes), file=sys.stderr)


def _lineups(comps: list[str], docs: dict[str, dict], fixtures: list[dict], now: datetime,
             window: timedelta = timedelta(minutes=75)) -> dict[str, dict]:
    """Official line-ups, notable absentees and goalscorer estimates for the matches close to kick-off."""
    from sportai.config import get_settings
    from sportai.connectors.api_football import ApiFootballProvider
    from sportai.services.lineups import SCORER_NO_ODDS, SCORER_WARNING, lineup_block, merge_players
    from sportai.services.live_page import find_doc, pre_match_rates
    from sportai.services.prematch import LOCAL_TZ

    soon = {k: d for k, d in docs.items()
            if d.get("coup_envoi") and now < datetime.fromisoformat(d["coup_envoi"]) <= now + window}
    if not soon:
        return {}
    api = ApiFootballProvider(get_settings().api_football_key)
    day = now.astimezone(LOCAL_TZ).date()
    out = {}
    for fx in api.fixtures_on([c for c in comps if c != "UCL"], day):
        home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
        key = find_doc(soon, f"{day:%Y-%m-%d}", home, away)
        if not key:
            continue
        doc = dict(soon[key])
        teams = {t["team"]["id"]: t for t in api.lineups(fx["fixture"]["id"])}
        if len(teams) < 2:
            doc["compositions"] = {"attente": "Compositions officielles pas encore publiées."}
            out[key] = doc
            continue
        season = fx["league"]["season"]
        pre = pre_match_rates(fixtures, doc["domicile"], doc["exterieur"])
        xg = pre[0] if pre else (None, None)
        blocks, scorers = {}, []
        for side, team, team_xg in (("domicile", fx["teams"]["home"], xg[0]), ("exterieur", fx["teams"]["away"], xg[1])):
            pages = [api.team_players(team["id"], s) for s in (season, season - 1)]
            block = lineup_block(teams[team["id"]], merge_players(pages, season), team_xg)
            scorers += [{**p, "equipe": doc[side]} for p in block.pop("buteurs", [])]
            blocks[side] = block
        doc["compositions"] = blocks
        if scorers:
            doc["buteurs"] = {"avertissement": SCORER_WARNING, "raison": SCORER_NO_ODDS,
                              "joueurs": sorted(scorers, key=lambda p: -p["proba"])[:8]}
        out[key] = doc
    return out


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
    from sportai.services.live_page import FINISHED, find_doc, live_doc, pre_match_rates, stats_by_side
    from sportai.services.prematch import LOCAL_TZ

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
    days = {now.astimezone(LOCAL_TZ).date()} | {_date.fromisoformat(d["jour"]) for d in current.values()
                                                 if d.get("jour") and d.get("statut") != "termine"}
    raw = [(day, fx) for day in sorted(days) for fx in api.fixtures_on(comps, day)]
    for day, fx in raw:
        short = fx["fixture"]["status"]["short"]
        if short in ("NS", "TBD", "PST", "CANC"):
            continue
        home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
        key = find_doc(current, f"{day:%Y-%m-%d}", home, away)
        if not key or current[key].get("statut") == "termine":
            continue
        snap, raw_stats = api.live_details(fx)
        odds = None if fx["fixture"]["status"]["short"] in FINISHED else api.live_odds(fx["fixture"]["id"])
        doc = live_doc(current[key], fx, snap, stats_by_side(raw_stats, fx["teams"]["home"]["id"]),
                       pre_match_rates(fixtures, current[key]["domicile"], current[key]["exterieur"]), now, odds)
        docs[key] = doc
    n_live = sum(d["statut"] == "en_direct" for d in docs.values())
    _write(out, docs, f"Direct : {n_live} match(s) en cours, {len(docs) - n_live} terminé(s)", now)


def goals_refresh(now: datetime, max_requests: int, download: bool = True) -> None:
    from sportai.config import get_settings
    from sportai.services.goals_refresh import run_refresh

    report = run_refresh(now, get_settings().api_football_key, max_requests=max_requests, download=download)
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
                           help="also price both-teams-to-score and totals, match by match (2 credits per match)")
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
        pronostic(a.page, a.out, now)
    elif a.cmd == "live":
        live(a.competitions, a.page, a.out, now)
    else:
        scores(a.competitions, a.page, a.out, a.final, now)


if __name__ == "__main__":
    main()
