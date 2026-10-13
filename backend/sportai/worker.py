"""Background worker: scheduled pre-match scans and server-side live monitoring.

Pre-match passes run 3 h and 45 min before each kick-off of the day (line-ups
come out about 1 h before and odds move then); a calendar refresh every
PREMATCH_INTERVAL_MIN finds the day's kick-offs. Matches in progress are then
followed live.

Runs independently of any Claude conversation:
    python -m sportai.worker
On PostgreSQL a session advisory lock guarantees a single active scheduler
even if several worker replicas are started.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session as DB

from sportai.config import Settings, get_settings
from sportai.connectors.api_football import ApiFootballProvider
from sportai.connectors.base import ProviderUnavailable
from sportai.connectors.simulation import SimulationProvider
from sportai.connectors.teams import normalise
from sportai.connectors.the_odds_api import TheOddsApiProvider
from sportai.db.models import AlertRow, AnalysisRun, Competition
from sportai.db.session import engine, session_factory
from sportai.engine.football.dixon_coles import score_matrix
from sportai.engine.football.markets import MarketKey, outcome
from sportai.services.live import Alert, evaluate_live
from sportai.services.runs import execute_prematch

log = logging.getLogger("sportai.worker")
LOCK_ID = 727_001
ALERT_DEDUP = timedelta(minutes=10)
PASSES = (timedelta(hours=3), timedelta(minutes=45), timedelta(minutes=15))  # before kick-off
PASS_WINDOW = timedelta(minutes=15)  # a pass is due from its start until this much later


def due_prematch(db: DB, now: datetime, interval: timedelta, mode: str) -> list[str]:
    due = []
    comps = ["SIM"] if mode == "simulation" else [c.code for c in db.scalars(select(Competition).where(
        Competition.monitored.is_(True), Competition.code != "SIM"))]
    for code in comps:
        last = db.scalar(select(AnalysisRun.started_at).where(AnalysisRun.competition == code, AnalysisRun.kind == "prematch",
                                                              AnalysisRun.mode == mode).order_by(AnalysisRun.started_at.desc()))
        last = last if last is None or last.tzinfo else last.replace(tzinfo=timezone.utc)
        if last is None or now - last >= interval or _pass_open(db, code, mode, now, last):
            due.append(code)
    return due


def _pass_open(db: DB, code: str, mode: str, now: datetime, last: datetime) -> bool:
    """True when a pre-match pass window for a known kick-off is open and no run started inside it."""
    latest = db.scalar(select(AnalysisRun).where(AnalysisRun.competition == code, AnalysisRun.kind == "prematch",
                                                 AnalysisRun.mode == mode, AnalysisRun.status == "completed")
                       .order_by(AnalysisRun.started_at.desc()))
    for f in (latest.fixtures or []) if latest else []:
        ko = datetime.fromisoformat(f["kickoff"])
        for before in PASSES:
            start = ko - before
            if start <= now < start + PASS_WINDOW and last < start:
                return True
    return False


def live_targets(db: DB, now: datetime, mode: str) -> list[tuple[str, dict]]:
    """Fixtures from recent completed runs whose kickoff was within the last 2.5 hours."""
    out, seen = [], set()
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.status == "completed", AnalysisRun.mode == mode,
                                                AnalysisRun.started_at >= now - timedelta(days=4)))
    for r in runs:
        for f in r.fixtures or []:
            ko = datetime.fromisoformat(f["kickoff"])
            if f.get("expected_goals") and now - timedelta(hours=2, minutes=30) <= ko <= now and f["match_id"] not in seen:
                seen.add(f["match_id"])
                out.append((r.competition, f))
    return out


def store_alerts(db: DB, alerts: list[Alert], mode: str, now: datetime) -> int:
    n = 0
    for a in alerts:
        dup = db.scalar(select(AlertRow.id).where(AlertRow.match_id == a.match_id, AlertRow.kind == a.kind,
                                                  AlertRow.score == a.score, AlertRow.market.is_(a.market) if a.market is None
                                                  else AlertRow.market == a.market, AlertRow.created_at >= now - ALERT_DEDUP))
        if dup:
            continue
        db.add(AlertRow(kind=a.kind, mode=mode, commercial=a.commercial, match_id=a.match_id, match_label=a.match_label,
                        minute=a.minute, score=a.score, market=a.market, odds=a.odds, bookmaker=a.bookmaker,
                        probability=a.probability, fair_odds=a.fair_odds, ev=a.ev, data_updated_at=a.data_updated_at,
                        reasons=a.reasons, invalidation=a.invalidation, created_at=now))
        n += 1
    db.commit()
    return n


def tick_live(db: DB, settings: Settings, now: datetime, sim_minute: float | None = None) -> int:
    mode = settings.data_mode
    targets = live_targets(db, now, mode)
    if not targets:
        return 0
    stored = 0
    if mode == "simulation":
        sim = SimulationProvider(now=now)
        for comp, f in targets[:1]:
            snap = replace(sim.live_snapshot(f["match_id"], minute=sim_minute or 25, score=(0, 1), xg=(0.9, 0.2)),
                           observed_at=now - timedelta(seconds=5))
            stored += _evaluate(db, f, snap, [], mode, now)
        return stored
    try:
        live_p = ApiFootballProvider(settings.api_football_key)
        odds_p = TheOddsApiProvider(settings.the_odds_api_key, regions=settings.odds_regions, cache_ttl=20)
    except ProviderUnavailable as e:
        log.warning("live monitoring disabled: %s", e)
        return 0
    for comp, f in targets:
        try:
            snap = live_p.live_snapshot(f["match_id"])
            home, away = (normalise(x) for x in f["match"].split(" - ", 1))
            quotes = [q for q in odds_p.odds(comp, live=True)
                      if normalise(q.home) == home and normalise(q.away) == away]
        except ProviderUnavailable as e:
            log.warning("live data unavailable for %s: %s", f["match"], e)
            continue
        stored += _evaluate(db, f, snap, quotes, mode, now)
    return stored


def _evaluate(db, f, snap, quotes, mode, now) -> int:
    lam, mu = f["expected_goals"]
    m = score_matrix(lam, mu, 0.0)
    ph, pa = outcome(m, MarketKey("1x2", "home")).win, outcome(m, MarketKey("1x2", "away")).win
    alerts = evaluate_live(f["match_id"], f["match"], lam, mu, ph, pa, snap, quotes, now=now)
    return store_alerts(db, alerts, mode, now)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    prematch_every = timedelta(minutes=int(os.getenv("PREMATCH_INTERVAL_MIN", "360")))
    live_every = float(os.getenv("LIVE_INTERVAL_S", "60"))
    eng = engine()
    lock_conn = None
    if eng.dialect.name == "postgresql":
        lock_conn = eng.connect()
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_ID}).scalar():
            log.info("another worker holds the scheduler lock; exiting")
            return
    log.info("worker started in %s mode", settings.data_mode)
    while True:
        now = datetime.now(timezone.utc)
        db = session_factory()()
        try:
            for comp in due_prematch(db, now, prematch_every, settings.data_mode):
                run = execute_prematch(db, settings, comp, "worker")
                log.info("prematch %s: %s %s %s", comp, run.status, run.outcome or "", run.status_detail or "")
            n = tick_live(db, settings, now)
            if n:
                log.info("stored %d live alerts", n)
        except Exception:  # keep the loop alive; the error is logged with its traceback
            log.exception("worker iteration failed")
            db.rollback()
        finally:
            db.close()
        time.sleep(live_every)


if __name__ == "__main__":
    main()
