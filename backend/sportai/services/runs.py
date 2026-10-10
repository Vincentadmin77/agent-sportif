"""Run an analysis against the configured providers and persist everything.

A provider failure produces a run with status `provider_unavailable` and no
candidates: the analysis is switched off, never filled with invented data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DB

from sportai.config import Settings
from sportai.connectors.api_football import ApiFootballProvider
from sportai.connectors.base import ProviderUnavailable
from sportai.connectors.openfootball import FILES as OPENFOOTBALL_FILES
from sportai.connectors.openfootball import OpenFootballProvider
from sportai.connectors.simulation import SimulationProvider
from sportai.connectors.teams import normalise
from sportai.connectors.the_odds_api import TheOddsApiProvider, fixtures_from_events
from sportai.db.models import AnalysisRun, CandidateRow, OddsObservation, ProviderStatus, Selection, Setting
from sportai.selection.engine import SelectionConfig
from sportai.engine.football.markets import MarketKey
from sportai.services.movement import classify, previous_consensus
from sportai.services.prematch import run_prematch

SELECTION_SETTINGS_KEY = "selection_config"


def selection_config(db: DB) -> SelectionConfig:
    row = db.get(Setting, SELECTION_SETTINGS_KEY)
    cfg = SelectionConfig()
    if row:
        for k, v in row.value.items():
            if hasattr(cfg, k):
                setattr(cfg, k, tuple(v) if k == "enabled_markets" else v)
    return cfg


def save_selection_config(db: DB, values: dict, user_id: str) -> SelectionConfig:
    cfg = selection_config(db)
    for k, v in values.items():
        if not hasattr(cfg, k):
            raise ValueError(f"unknown setting {k}")
        setattr(cfg, k, tuple(v) if k == "enabled_markets" else type(getattr(cfg, k))(v))
    if not (0 <= cfg.max_selections <= 4):
        raise ValueError("max_selections must be between 0 and 4")
    if cfg.min_odds <= 1.0 or cfg.max_odds <= cfg.min_odds:
        raise ValueError("invalid odds range")
    row = db.get(Setting, SELECTION_SETTINGS_KEY) or Setting(key=SELECTION_SETTINGS_KEY, value={})
    row.value = {**cfg.to_dict(), "enabled_markets": list(cfg.enabled_markets)}
    row.updated_by = user_id
    db.merge(row)
    return cfg


def _history_seasons(now: datetime) -> list[str]:
    y = now.year if now.month >= 7 else now.year - 1
    return [f"{y - k}-{str(y - k + 1)[-2:]}" for k in (2, 1, 0)]


def _mark(db: DB, provider: str, error: str | None = None, quota: dict | None = None) -> None:
    row = db.get(ProviderStatus, provider) or ProviderStatus(provider=provider)
    now = datetime.now(timezone.utc)
    if error:
        row.last_error_at, row.last_error = now, error[:2000]
    else:
        row.last_ok_at = now
    if quota:
        row.quota = quota
    db.merge(row)


def execute_prematch(db: DB, settings: Settings, competition: str, triggered_by: str,
                     mode: str | None = None, now: datetime | None = None) -> AnalysisRun:
    now = now or datetime.now(timezone.utc)
    mode = mode or settings.data_mode
    cfg = selection_config(db)
    run = AnalysisRun(kind="prematch", competition=competition, mode=mode, status="running",
                      config={**cfg.to_dict(), "enabled_markets": list(cfg.enabled_markets)}, triggered_by=triggered_by,
                      started_at=now)
    db.add(run)
    db.flush()

    current = "configuration"
    try:
        if mode == "simulation":
            sim = SimulationProvider(now=now)
            history, fixtures, quotes = sim.results(), sim.upcoming_fixtures(), sim.odds()
            run.data_sources = {"history": "simulation", "fixtures": "simulation", "odds": "simulation"}
        else:
            current = ApiFootballProvider.name
            fixtures_p = ApiFootballProvider(settings.api_football_key)
            current = TheOddsApiProvider.name
            odds_p = TheOddsApiProvider(settings.the_odds_api_key, regions=settings.odds_regions,
                                        bookmakers=settings.odds_bookmakers)
            history_p = OpenFootballProvider() if competition in OPENFOOTBALL_FILES else fixtures_p
            current = history_p.name
            history = []
            for season in _history_seasons(now):
                try:
                    history += history_p.results(competition, season)
                except ProviderUnavailable:
                    if season == _history_seasons(now)[-1]:
                        raise
            _mark(db, history_p.name)
            fixtures_source = fixtures_p.name
            try:
                fixtures = fixtures_p.upcoming_fixtures(competition)
                _mark(db, fixtures_p.name)
            except ProviderUnavailable as e:
                # Pre-match only needs who plays whom and when; the odds feed lists that too.
                _mark(db, fixtures_p.name, str(e))
                fixtures, fixtures_source = None, odds_p.name
            current = odds_p.name
            quotes = odds_p.odds(competition)
            _mark(db, odds_p.name, quota=odds_p.last_quota)
            if fixtures is None:
                fixtures = fixtures_from_events(odds_p.last_events, competition, now, timedelta(hours=24))
            run.data_sources = {"history": history_p.name, "fixtures": fixtures_source, "odds": odds_p.name}
    except ProviderUnavailable as e:
        _mark(db, current, str(e))
        run.status, run.status_detail, run.finished_at = "provider_unavailable", str(e), datetime.now(timezone.utc)
        db.commit()
        return run

    try:
        report = run_prematch(history, fixtures, quotes, cfg, now=now)
    except ValueError as e:
        run.status, run.status_detail, run.finished_at = "failed", str(e), datetime.now(timezone.utc)
        db.commit()
        return run

    for q in quotes:
        db.add(OddsObservation(run_id=run.id, provider=q.provider, bookmaker=q.bookmaker, event_id=q.provider_event_id,
                               event_label=f"{q.home} - {q.away}", market=q.market, selection=q.selection, line=q.line,
                               odds=q.odds, observed_at=q.observed_at, expires_at=q.expires_at,
                               validation_status=q.validation_status, is_live=q.is_live, mode=q.mode))
    run.model_name, run.model_version = report.model_name, report.model_version
    run.fixtures = [{"match_id": f.fixture.provider_event_id, "match": f"{f.fixture.home} - {f.fixture.away}",
                     "kickoff": f.fixture.kickoff.isoformat(), "expected_goals": f.expected_goals,
                     "probabilities": f.probabilities, "skipped_reason": f.skipped_reason, "n_quotes": f.n_quotes}
                    for f in report.fixtures]
    kickoffs = {f.fixture.provider_event_id: f.fixture.kickoff for f in report.fixtures}
    before = previous_consensus(db, competition, mode, now)
    moves = {}
    for e in report.selection.selected + report.selection.rejected:
        c = e.candidate
        home, _, away = c.match_label.partition(" - ")
        p_before = before.get((normalise(home), normalise(away)), {}).get(MarketKey(c.market, c.selection, c.line))
        if p_before is not None and c.market_fair_prob is not None:
            moves[id(e)] = classify(c.outcome.win, p_before, c.market_fair_prob)
    rows = {}
    for e in report.selection.selected + report.selection.rejected:
        s = e.summary()
        row = CandidateRow(run_id=run.id, mode=mode, match_id=s["match_id"], match_label=s["match"], market=s["market"],
                           selection=s["selection"], line=s["line"], odds=s["odds"], bookmaker=s["bookmaker"],
                           odds_observed_at=e.candidate.odds_observed_at, p_win=s["p_win"], p_push=s["p_push"],
                           p_low=s["p_low"], fair_odds=s["fair_odds"], ev=s["ev"], ev_low=s["ev_low"],
                           ev_stress=s["ev_stress"], market_fair_prob=s["market_fair_prob"],
                           data_quality=s["data_quality"], score=s["score"], accepted=s["accepted"], reasons=s["reasons"])
        db.add(row)
        rows[id(e)] = row
    db.flush()
    for e in report.selection.selected:
        row = rows[id(e)]
        db.add(Selection(
            run_id=run.id, candidate_id=row.id, kind="prematch", mode=mode,
            # Simulated selections can be inspected but never enter the publication workflow.
            status="pending_review" if mode == "real" else "simulation_only",
            match_id=row.match_id, match_label=row.match_label, competition=competition, market=row.market,
            selection=row.selection, line=row.line, odds=row.odds, bookmaker=row.bookmaker,
            odds_observed_at=row.odds_observed_at, p_win=row.p_win, p_push=row.p_push, fair_odds=row.fair_odds,
            ev=row.ev, ev_low=row.ev_low, model_name=report.model_name, model_version=report.model_version,
            rationale=_rationale(e, cfg, kickoffs.get(row.match_id), moves.get(id(e))),
        ))
    run.outcome = report.selection.status
    run.status, run.finished_at = "completed", datetime.now(timezone.utc)
    db.commit()
    return run


def _rationale(e, cfg: SelectionConfig, kickoff, movement=None) -> str:
    c = e.candidate
    parts = [
        f"Probabilité modèle {c.outcome.win:.1%} (10e centile {e.p_low:.1%}), cote juste {e.fair_odds:.2f}, "
        f"cote observée {c.odds:.2f} chez {c.bookmaker} à {c.odds_observed_at:%d/%m %H:%M} UTC.",
        f"Espérance {e.ev:+.1%} ; prudente {e.ev_low:+.1%} ; après retrait de {cfg.stress_delta:.0%} "
        f"de probabilité {e.ev_stress:+.1%}.",
    ]
    if c.market_fair_prob is not None:
        parts.append(f"Probabilité de marché sans marge {c.market_fair_prob:.1%}.")
    if movement is not None:
        parts.append(movement.describe())
    if kickoff:
        parts.append(f"Coup d'envoi {kickoff:%d/%m %H:%M} UTC.")
    return " ".join(parts)


def odds_snapshot_expired(sel: Selection, now: datetime, max_age: timedelta) -> bool:
    observed = sel.odds_observed_at if sel.odds_observed_at.tzinfo else sel.odds_observed_at.replace(tzinfo=timezone.utc)
    return now - observed > max_age

