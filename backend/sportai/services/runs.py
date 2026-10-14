"""Run an analysis against the configured providers and persist everything.

A provider failure produces a run with status `provider_unavailable` and no
candidates: the analysis is switched off, never filled with invented data.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DB

from sportai.config import Settings
from sportai.connectors.api_football import CALENDAR_YEAR as API_CALENDAR_YEAR
from sportai.connectors.api_football import ApiFootballProvider
from sportai.connectors.base import ProviderUnavailable
from sportai.connectors.openfootball import CALENDAR_YEAR, FILES as OPENFOOTBALL_FILES
from sportai.connectors.openfootball import OpenFootballProvider
from sportai.connectors.simulation import SimulationProvider
from sportai.connectors.teams import normalise
from sportai.connectors.the_odds_api import TheOddsApiProvider, fixtures_from_events
from sportai.db.models import AnalysisRun, CandidateRow, OddsObservation, ProviderStatus, Selection, Setting
from sportai.selection.engine import SelectionConfig
from sportai.engine.football import dixon_coles as dc
from sportai.engine.football.markets import MarketKey
from sportai.services.goals_refresh import load_book_safely
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


def _history_seasons(now: datetime, competition: str = "") -> list[str]:
    if competition in CALENDAR_YEAR or competition in API_CALENDAR_YEAR:
        return [str(now.year - k) for k in (2, 1, 0)]
    y = now.year if now.month >= 7 else now.year - 1
    return [f"{y - k}-{str(y - k + 1)[-2:]}" for k in (2, 1, 0)]


UCL_DOMESTIC = ("FR1", "EN1", "ES1", "DE1", "IT1")
# Clubs of the other leagues (Porto, Feyenoord, Bodø/Glimt, Shakhtar...) need their own league's results to be
# known, and the Europa League / Conference League to tie those leagues to the big five: a club dominating a
# weak league is placed through its league's European results, not ranked with the big clubs.
UCL_EXTRA_CUPS = ("UEL", "UECL")
UCL_EXTRA_LEAGUES = ("PT1", "NL1", "TR1", "BE1", "AT1", "SC1", "GR1", "UA1", "CH1", "CZ1", "DK1", "HR1", "RS1",
                     "NO1", "SK1", "CY1", "PL1", "IL1", "SE1", "KZ1", "AZ1", "BG1", "HU1", "RO1", "SI1")
UCL_EXTRA = UCL_EXTRA_CUPS + UCL_EXTRA_LEAGUES
UCL_MIN_MATCHES = 20
# The cross-league model under-rates favourites in the Champions League (backtest docs/04-backtest.md):
# the strength gap log(lambda / mu) of a UCL match is stretched by 1.2 (fitted on the 579 UCL predictions
# 2019-20..2025-26 by scripts/backtest_ucl.py; walk-forward fits 1.07..1.19). None switches it off.
UCL_CALIBRATION: dc.RateCalibration | None = dc.RateCalibration(stretch=1.2)


def load_history(db: DB, competition: str, now: datetime, primary, backup) -> tuple[list, set[str]]:
    """Results of the last three seasons, season by season from the primary source (API-Football, up to date
    with a paid plan), else from the backup (openfootball). Only the current season is required."""
    history, used = [], set()
    seasons = _history_seasons(now, competition)
    for season in seasons:
        for p in (primary, backup):
            if p is None:
                continue
            try:
                history += p.results(competition, season)
                used.add(p.name)
                break
            except ProviderUnavailable as e:
                _mark(db, p.name, str(e))
        else:
            if season == seasons[-1]:
                raise ProviderUnavailable(f"no history source for {competition} {season}")
    for name in used:
        _mark(db, name)
    return history, used


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
                     mode: str | None = None, now: datetime | None = None, goal_markets: bool = False,
                     max_hours_ahead: float | None = None) -> AnalysisRun:
    """goal_markets: also fetch "both teams to score" and totals odds, match by match, for the matches
    inside the analysis window (costs 2 credits per match; used at the 45 min pass).
    max_hours_ahead: widen the analysis window (the 8 am pass covers every match of the day)."""
    now = now or datetime.now(timezone.utc)
    mode = mode or settings.data_mode
    cfg = selection_config(db)
    if max_hours_ahead is not None:
        cfg = replace(cfg, max_hours_ahead=max_hours_ahead)
    if competition == "UCL":
        cfg = replace(cfg, enabled_markets=())  # cross-league model not validated: information only
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
            # Each market requested costs one credit: totals only when that market is enabled.
            odds_p = TheOddsApiProvider(settings.the_odds_api_key, regions=settings.odds_regions,
                                        bookmakers=settings.odds_bookmakers,
                                        markets="h2h,totals" if "totals" in cfg.enabled_markets else "h2h")
            backup = OpenFootballProvider() if competition in OPENFOOTBALL_FILES else None
            current = fixtures_p.name
            history, used = load_history(db, competition, now, fixtures_p, backup)
            if competition == "UCL":
                # Clubs of different leagues are only comparable through a model fitted on all of them at once.
                for dom in UCL_DOMESTIC:
                    more, more_used = load_history(db, dom, now, fixtures_p,
                                                   OpenFootballProvider() if dom in OPENFOOTBALL_FILES else None)
                    history += more
                    used |= more_used
                for extra in UCL_EXTRA:  # API-Football only; a missing one costs coverage, never the run
                    for season in _history_seasons(now, extra):
                        try:
                            history += fixtures_p.results(extra, season)
                        except ProviderUnavailable as e:
                            _mark(db, fixtures_p.name, f"{extra} {season}: {e}")
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
            if goal_markets:
                quotes += _goal_quotes(odds_p, competition, fixtures, now, cfg)
            run.data_sources = {"history": "+".join(sorted(used)), "fixtures": fixtures_source, "odds": odds_p.name}
    except ProviderUnavailable as e:
        _mark(db, current, str(e))
        run.status, run.status_detail, run.finished_at = "provider_unavailable", str(e), datetime.now(timezone.utc)
        db.commit()
        return run

    try:
        # A club seen only in a few European matches cannot be placed against the five leagues.
        fit_cfg = dc.FitConfig(min_matches_per_team=UCL_MIN_MATCHES) if competition == "UCL" else None
        goals_lookup = goals_label = None
        if mode == "real" and competition != "UCL":
            book = load_book_safely(now)
            if book is not None:
                goals_lookup = lambda h, a: book.lookup(competition, h, a)  # noqa: E731
                goals_label = book.data["model"]
        report = run_prematch(history, fixtures, quotes, cfg, fit_cfg=fit_cfg, now=now, goals_lookup=goals_lookup,
                              goals_model_label=goals_label,
                              calibration=UCL_CALIBRATION if competition == "UCL" else None)
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
                     "probabilities": f.probabilities, "skipped_reason": f.skipped_reason, "n_quotes": f.n_quotes,
                     "likely_score": f.likely_score, "goals_model": f.goals_model,
                     "goals_model_note": f.goals_model_note}
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
    by_match: dict[str, dict] = {}
    for e in report.selection.selected + report.selection.rejected:
        if id(e) in moves:
            m = moves[id(e)]
            label = MarketKey(e.candidate.market, e.candidate.selection, e.candidate.line).label()
            by_match.setdefault(e.candidate.match_id, {})[label] = {"kind": m.kind, "before": m.before, "after": m.after}
    run.fixtures = [{**f, "market_moves": by_match.get(f["match_id"], {})} for f in run.fixtures]
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


def _goal_quotes(odds_p: TheOddsApiProvider, competition: str, fixtures, now: datetime, cfg: SelectionConfig):
    events = {(normalise(e["home_team"]), normalise(e["away_team"])): e["id"] for e in odds_p.last_events}
    out = []
    for f in fixtures:
        if f.status != "scheduled" or not (now < f.kickoff <= now + timedelta(hours=cfg.max_hours_ahead)):
            continue
        event_id = events.get((normalise(f.home), normalise(f.away)))
        if event_id:
            out += odds_p.event_odds(competition, event_id)
    return out


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

