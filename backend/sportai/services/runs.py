"""Run an analysis against the configured providers and persist everything.

A provider failure produces a run with status `provider_unavailable` and no
candidates: the analysis is switched off, never filled with invented data.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DB

from sportai.config import Settings
from sportai.connectors.api_football import CALENDAR_YEAR as API_CALENDAR_YEAR
from sportai.connectors.api_football import ApiFootballProvider
from sportai.connectors.api_football_odds import parse_odds as parse_api_football_odds
from sportai.connectors.base import ProviderUnavailable
from sportai.connectors.openfootball import CALENDAR_YEAR, FILES as OPENFOOTBALL_FILES
from sportai.connectors.openfootball import OpenFootballProvider
from sportai.connectors.simulation import SimulationProvider
from sportai.connectors.teams import normalise
from sportai.connectors.the_odds_api import (FRENCH_EXTRA_MARKETS, FRENCH_EXTRA_WITHIN, TheOddsApiProvider,
                                             fixtures_from_events, french_only, is_french)
from sportai.db.models import AnalysisRun, CandidateRow, OddsObservation, ProviderStatus, Selection, Setting
from sportai.selection.engine import COTE_MIN, SelectionConfig
from sportai.engine.football import dixon_coles as dc
from sportai.engine.football.markets import MarketKey
from sportai.services.goals_refresh import load_book_safely
from sportai.services.movement import classify, previous_consensus
from sportai.services.prematch import journee, run_prematch

SELECTION_SETTINGS_KEY = "selection_config"


def selection_config(db: DB) -> SelectionConfig:
    row = db.get(Setting, SELECTION_SETTINGS_KEY)
    cfg = SelectionConfig()
    if row:
        for k, v in row.value.items():
            if hasattr(cfg, k):
                setattr(cfg, k, tuple(v) if isinstance(getattr(cfg, k), tuple) else v)
    cfg.min_odds = max(cfg.min_odds, COTE_MIN)  # a stored setting never goes under the owner's floor
    return cfg


def save_selection_config(db: DB, values: dict, user_id: str) -> SelectionConfig:
    cfg = selection_config(db)
    for k, v in values.items():
        if not hasattr(cfg, k):
            raise ValueError(f"unknown setting {k}")
        setattr(cfg, k, tuple(v) if isinstance(getattr(cfg, k), tuple) else type(getattr(cfg, k))(v))
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
DOMESTIC_CUPS = {"ENC": "EN1"}  # cup -> the league whose history rates its clubs
# European cups analysed with the cross-league model (owner 2026-10-15: the Europa League too, same rules as the
# Champions League). Its calibration was fitted on the Champions League only (scripts/backtest_ucl.py).
EUROPEAN_CUPS = ("UCL", "UEL")
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
                     max_hours_ahead: float | None = None, absence_lookup=None) -> AnalysisRun:
    """goal_markets: also fetch "both teams to score" and totals odds, match by match, for the matches
    inside the analysis window (costs 2 credits per match; used at the 45 min pass).
    max_hours_ahead: widen the analysis window (the 8 am pass covers every match of the day).
    absence_lookup(fixture) -> (f_home, f_away) or None: missing key players, result markets only
    (prematch.run_prematch, docs/22-absences.md); league matches only, the study did not cover the European cups."""
    now = now or datetime.now(timezone.utc)
    mode = mode or settings.data_mode
    cfg = selection_config(db)
    if max_hours_ahead is not None:
        cfg = replace(cfg, max_hours_ahead=max_hours_ahead)
    if competition in EUROPEAN_CUPS:
        # cross-league model not validated: information only, no market on trial either (the tickets and the player
        # take its 1X2 bets under their own Champions League rules)
        cfg = replace(cfg, enabled_markets=(), trial_markets=())
    run = AnalysisRun(kind="prematch", competition=competition, mode=mode, status="running",
                      config={**cfg.to_dict(), "enabled_markets": list(cfg.enabled_markets)}, triggered_by=triggered_by,
                      started_at=now)
    db.add(run)
    db.flush()

    current = "configuration"
    unpriced: dict[str, list[dict]] = {}
    raw_odds: list[dict] = []  # API-Football /odds items, for the scorer market priced after the match model
    window: list = []
    fixtures_p = None
    try:
        if mode == "simulation":
            sim = SimulationProvider(now=now)
            history, fixtures, quotes = sim.results(), sim.upcoming_fixtures(), sim.odds()
            run.data_sources = {"history": "simulation", "fixtures": "simulation", "odds": "simulation"}
        else:
            current = ApiFootballProvider.name
            fixtures_p = ApiFootballProvider(settings.api_football_key)
            backup = OpenFootballProvider() if competition in OPENFOOTBALL_FILES else None
            current = fixtures_p.name
            # a domestic cup: its clubs are rated on their league's results (owner 2026-10-28: EFL Cup)
            history, used = load_history(db, DOMESTIC_CUPS.get(competition, competition), now, fixtures_p,
                                         backup if competition not in DOMESTIC_CUPS else None)
            if competition in EUROPEAN_CUPS:
                # Clubs of different leagues are only comparable through a model fitted on all of them at once.
                for dom in UCL_DOMESTIC:
                    more, more_used = load_history(db, dom, now, fixtures_p,
                                                   OpenFootballProvider() if dom in OPENFOOTBALL_FILES else None)
                    history += more
                    used |= more_used
                for extra in [c for c in ("UCL", *UCL_EXTRA) if c != competition]:  # API-Football only; a missing one costs coverage, never the run
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
                fixtures, fixtures_source = None, TheOddsApiProvider.name
            # Odds: API-Football first (every bookmaker and bet type of its feed, 7 500 requests a day), The Odds API
            # (500 credits a month) only when API-Football has no odds for the window.
            quotes, odds_source = [], None
            window = [f for f in fixtures or [] if _in_window(f, now, cfg)]
            if window:
                try:
                    quotes, unpriced = api_football_quotes(fixtures_p, competition, window, raw_odds)
                    odds_source = fixtures_p.name
                except ProviderUnavailable as e:
                    _mark(db, fixtures_p.name, f"odds: {e}")
            # API-Football refreshes its odds only every few hours: once they are past their age limit, nothing
            # could be priced, so The Odds API supplies the current 1X2 odds (2026-10-14: 16:30 pass found none).
            stale_af: list = []
            if quotes and not any(q.expires_at is None or q.expires_at > now for q in quotes):
                _mark(db, fixtures_p.name, "odds: every API-Football quote past its age limit")
                stale_af, quotes, odds_source = quotes, [], None
            elif quotes:  # some matches refreshed, others not: the old ones keep their non-result markets
                ok = [q.expires_at is None or q.expires_at > now for q in quotes]
                fresh = [q for q, f in zip(quotes, ok) if f]
                quotes = fresh + keep_stale_quotes([q for q, f in zip(quotes, ok) if not f], fresh, now)
            if not quotes and (fixtures is None or window):
                current = TheOddsApiProvider.name
                try:
                    # Each market requested costs one credit: totals only when that market is enabled.
                    odds_p = TheOddsApiProvider(settings.the_odds_api_key, regions=settings.odds_regions,
                                                bookmakers=settings.odds_bookmakers,
                                                markets="h2h,totals" if "totals" in cfg.enabled_markets or french_only()
                                                else "h2h")
                    quotes = odds_p.odds(competition)
                    _mark(db, odds_p.name, quota=odds_p.last_quota)
                    if fixtures is None:
                        fixtures = fixtures_from_events(odds_p.last_events, competition, now, timedelta(hours=24))
                    if french_only() and FRENCH_EXTRA_MARKETS:  # the French rule's extra markets, last pass only
                        quotes += [q for q in _french_extra(odds_p, competition, fixtures, now) if is_french(q.bookmaker)]
                    if goal_markets:
                        quotes += _goal_quotes(odds_p, competition, fixtures, now, cfg)
                    odds_source = odds_p.name
                except ProviderUnavailable as e:
                    if not stale_af:  # nothing else to price with
                        raise
                    _mark(db, TheOddsApiProvider.name, str(e))  # no credits left: API-Football's other markets still
                # 2026-10-23 (Stuttgart - Gladbach, Alavés - Málaga, Ipswich - Forest): API-Football had not refreshed
                # these matches for over 4 hours, so its ~1 100 quotes (goals, both teams score, team totals,
                # handicaps...) were all thrown away and the page kept The Odds API's three 1X2 prices only. The
                # markets the fallback does not price are now kept from API-Football (keep_stale_quotes).
                if stale_af:
                    kept = keep_stale_quotes(stale_af, quotes, now)
                    if kept:
                        quotes += kept
                        odds_source = "+".join(x for x in (odds_source, fixtures_p.name) if x)
            # French rule (owner 2026-10-28): positions only on French bookmakers' odds, which API-Football does not
            # carry. Their 1X2 and totals come from The Odds API, region fr (2 credits a league per pass).
            if french_only() and window and quotes and TheOddsApiProvider.name not in (odds_source or ""):
                try:
                    odds_p = TheOddsApiProvider(settings.the_odds_api_key, regions="fr", markets="h2h,totals")
                    quotes += [q for q in odds_p.odds(competition) if is_french(q.bookmaker)]
                    if FRENCH_EXTRA_MARKETS:
                        quotes += [q for q in _french_extra(odds_p, competition, window, now) if is_french(q.bookmaker)]
                    _mark(db, odds_p.name, quota=odds_p.last_quota)
                    odds_source = "+".join(x for x in (odds_source, odds_p.name) if x)
                except ProviderUnavailable as e:  # no French odds: nothing will be proposed on this league
                    _mark(db, TheOddsApiProvider.name, str(e))
            run.data_sources = {"history": "+".join(sorted(used)), "fixtures": fixtures_source, "odds": odds_source}
    except ProviderUnavailable as e:
        _mark(db, current, str(e))
        run.status, run.status_detail, run.finished_at = "provider_unavailable", str(e), datetime.now(timezone.utc)
        db.commit()
        return run

    try:
        # A club seen only in a few European matches cannot be placed against the five leagues.
        fit_cfg = dc.FitConfig(min_matches_per_team=UCL_MIN_MATCHES) if competition in EUROPEAN_CUPS else None
        goals_lookup = goals_label = None
        if mode == "real" and competition not in EUROPEAN_CUPS:
            book = load_book_safely(now)
            if book is not None:
                goals_lookup = lambda h, a: book.lookup(competition, h, a)  # noqa: E731
                goals_label = book.data["model"]
        report = run_prematch(history, fixtures, quotes, cfg, fit_cfg=fit_cfg, now=now, goals_lookup=goals_lookup,
                              goals_model_label=goals_label,
                              calibration=UCL_CALIBRATION if competition in EUROPEAN_CUPS else None,
                              absence_lookup=absence_lookup if competition not in EUROPEAN_CUPS else None)
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
                     "goals_model_note": f.goals_model_note,
                     **({"absence_factors": list(f.absence_factors)} if f.absence_factors else {}),
                     **({"cotes_fr": f.french_odds} if f.french_odds is not None else {}),
                     # bets of the odds feed the score model cannot price: listed with their odds, "non analysé"
                     **({"non_analyses": unpriced[f.fixture.provider_event_id]}
                        if unpriced.get(f.fixture.provider_event_id) and not f.skipped_reason else {})}
                    for f in report.fixtures]
    if raw_odds and fixtures_p is not None and fixtures_p.name in (run.data_sources.get("odds") or "").split("+"):
        run.fixtures = scorer_markets(db, fixtures_p, competition, raw_odds, window, run.fixtures, now)
        run.fixtures = half_markets(competition, raw_odds, run.fixtures)
        run.fixtures = count_markets(competition, raw_odds, window, run.fixtures, now)
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


API_FOOTBALL_ODDS_VALIDITY = timedelta(hours=4)  # the feed refreshes pre-match odds about every 3 hours
# When the feed has not refreshed a match for over 4 hours (2026-10-23: Bundesliga, LaLiga, Premier League evening
# matches last updated at 13:22 / 13:59 UTC, passes at 18:16 and 18:45), the markets The Odds API fallback does not
# price stay priced from API-Football's last prices, up to this age and never past kick-off. Goal markets (number of
# goals, both teams score, team totals) move little in the last hours; the result markets, which follow the team
# news, never use an old price: their current odds come from the fallback. Prices older than the 4-hour limit are
# still analysed and offered to the tickets and the player, but the selection engine keeps its own age limit
# (max_odds_age_by_provider) for the official selections.
API_FOOTBALL_STALE_VALIDITY = timedelta(hours=12)
RESULT_MARKETS = ("1x2", "double_chance", "dnb")


def keep_stale_quotes(stale: list, fallback: list, now: datetime) -> list:
    """API-Football quotes past their 4-hour validity, kept for the markets (and lines) the fallback quotes do not
    cover, valid up to API_FOOTBALL_STALE_VALIDITY after their observation and never past kick-off."""
    covered = {(normalise(q.home), normalise(q.away), q.market, q.line) for q in fallback}
    out = []
    for q in stale:
        if q.market in RESULT_MARKETS or (normalise(q.home), normalise(q.away), q.market, q.line) in covered:
            continue
        expires = min(q.observed_at + API_FOOTBALL_STALE_VALIDITY, q.kickoff)
        if expires > now:
            out.append(replace(q, expires_at=expires))
    return out


def _in_window(f, now: datetime, cfg: SelectionConfig) -> bool:
    return (f.status == "scheduled" and now < f.kickoff <= now + timedelta(hours=cfg.max_hours_ahead)
            and journee(f.kickoff) == journee(now))


def api_football_quotes(api: ApiFootballProvider, competition: str, fixtures,
                        raw_out: list | None = None) -> tuple[list, dict[str, list[dict]]]:
    """Odds of these fixtures from API-Football: one request per page of 10 fixtures per UTC date (usually one
    per competition and pass), plus one per fixture the date listing missed. Every bookmaker, every bet type.
    raw_out: receives the raw items (the scorer market is priced from them once the match model has run)."""
    teams = {f.provider_event_id: (f.home, f.away, f.kickoff) for f in fixtures}
    raw = []
    for day in sorted({f.kickoff.date() for f in fixtures}):
        raw += api.odds_on(competition, day)
    seen = {str((r.get("fixture") or {}).get("id")) for r in raw}
    for fid in sorted(set(teams) - seen):
        raw += api.fixture_odds(fid)
    if raw_out is not None:
        raw_out += raw
    return parse_api_football_odds(raw, teams, API_FOOTBALL_ODDS_VALIDITY)


def scorer_markets(db: DB, api: ApiFootballProvider, competition: str, raw: list[dict], window, fixtures: list[dict],
                   now: datetime) -> list[dict]:
    """The bookmakers' anytime goalscorer selections of each analysed fixture, priced by the scorer model
    (services/buteurs.py, owner 2026-10-15): stored on the run's fixture as "buteurs_cotes" (and the names dropped as
    "buteurs_ecartes"), the market then leaving the "non analysé" list. A failure costs this market only."""
    from sportai.engine.football.scorers import SCORER_BETS
    from sportai.services.buteurs import price_fixture

    items = {str((r.get("fixture") or {}).get("id")): r for r in raw}
    by_id = {f.provider_event_id: f for f in window}
    season = now.year if competition in API_CALENDAR_YEAR else (now.year if now.month >= 7 else now.year - 1)
    out = []
    for f in fixtures:
        fx, item = by_id.get(f["match_id"]), items.get(f["match_id"])
        if fx is None or item is None or f.get("skipped_reason") or not f.get("expected_goals"):
            out.append(f)
            continue
        try:
            priced = price_fixture(api, item, fx, f["expected_goals"], now, season)
        except ProviderUnavailable as e:
            _mark(db, api.name, f"scorers {f['match']}: {e}")
            priced = None
        if not priced:
            out.append(f)
            continue
        out.append({**f, "buteurs_cotes": priced["rows"], "buteurs_ecartes": priced["ecartes"],
                    "buteurs_composition": priced["composition"],
                    "non_analyses": [o for o in f.get("non_analyses") or [] if o.get("marche") not in SCORER_BETS]})
    return out


def half_markets(competition: str, raw: list[dict], fixtures: list[dict]) -> list[dict]:
    """The half-time bets of each analysed fixture (half-time result, second half, half-time / full-time, both
    halves...), priced by the half-time model (services/mi_temps.py, owner 2026-10-27): stored as "mi_temps_cotes",
    the bets then leaving the "non analysé" list. A failure costs these bets only."""
    from sportai.engine.football.halves import HALF_BETS
    from sportai.services.mi_temps import price_halves

    items = {str((r.get("fixture") or {}).get("id")): r for r in raw}
    out = []
    for f in fixtures:
        item = items.get(f["match_id"])
        if item is None or f.get("skipped_reason") or not f.get("expected_goals"):
            out.append(f)
            continue
        home, _, away = f["match"].partition(" - ")
        try:
            rows = price_halves(item, home, away, f["expected_goals"], competition)
        except (ValueError, TypeError, KeyError):
            rows = []
        out.append({**f, "mi_temps_cotes": rows,
                    "non_analyses": [o for o in f.get("non_analyses") or [] if o.get("marche") not in HALF_BETS]}
                   if rows else f)
    return out


def count_markets(competition: str, raw: list[dict], window, fixtures: list[dict], now: datetime) -> list[dict]:
    """The corners, cards and shots bets of each analysed fixture, priced by the count model
    (services/comptages.py, owner 2026-10-27): stored as "comptages_cotes", the bets then leaving the "non analysé"
    list. A failure costs these bets only."""
    from sportai.engine.football.counts import FEED
    from sportai.services.comptages import price_counts
    from sportai.services.goals_refresh import cache_dir

    items = {str((r.get("fixture") or {}).get("id")): r for r in raw}
    by_id = {f.provider_event_id: f for f in window}
    out = []
    for f in fixtures:
        item, fx = items.get(f["match_id"]), by_id.get(f["match_id"])
        if item is None or f.get("skipped_reason"):
            out.append(f)
            continue
        home, _, away = f["match"].partition(" - ")
        if fx is not None:
            home, away = fx.home, fx.away
        try:
            rows = price_counts(item, home, away, competition, now, str(cache_dir()),
                                getattr(fx, "referee", None) if fx is not None else None)
        except (ValueError, TypeError, KeyError, OSError) as e:
            print(f"count markets unavailable for {f['match']} ({str(e)[:120]})", file=sys.stderr)
            rows = []
        out.append({**f, "comptages_cotes": rows,
                    "non_analyses": [o for o in f.get("non_analyses") or [] if o.get("marche") not in FEED]}
                   if rows else f)
    return out


def _french_extra(odds_p: TheOddsApiProvider, competition: str, fixtures, now: datetime):
    """Double chance, draw no bet and both teams score at the French sites, for the matches of the last pass."""
    events = {(normalise(e["home_team"]), normalise(e["away_team"])): e["id"] for e in odds_p.last_events}
    out = []
    for f in fixtures:
        event_id = events.get((normalise(f.home), normalise(f.away)))
        if f.status == "scheduled" and now < f.kickoff <= now + FRENCH_EXTRA_WITHIN and event_id:
            try:
                out += odds_p.event_odds(competition, event_id, FRENCH_EXTRA_MARKETS)
            except ProviderUnavailable as e:  # the match keeps its 1X2 and totals
                print(f"French extra markets {f.home} - {f.away}: {str(e)[:120]}", file=sys.stderr)
    return out


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
    if not e.validated:
        parts.insert(0, f"Marché non validé par le backtest, retenu à l'essai avec des seuils relevés (espérance "
                        f"{cfg.trial_min_ev:.0%} au moins, prudente {cfg.trial_min_ev_low:.0%}) : prudence.")
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

