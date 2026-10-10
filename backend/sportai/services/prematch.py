"""Pre-match analysis pipeline: history -> model -> priced markets -> candidates -> selection.

Pure function of its inputs (no database, no network) so a run is
reproducible from the stored inputs. Mixing simulated and real inputs is an
error, not a warning.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import zlib
from zoneinfo import ZoneInfo

import numpy as np

from sportai.connectors.base import Fixture, HistoricalResult, OddsQuote
from sportai.connectors.teams import normalise
from sportai.engine.football import dixon_coles as dc
from sportai.engine.football.markets import MarketKey, market_group, outcome
from sportai.engine.value import devig_shin
from sportai.selection.engine import Candidate, SelectionConfig, SelectionResult, select


LOCAL_TZ = ZoneInfo("Europe/Paris")


class MixedModeError(ValueError):
    pass


@dataclass
class FixtureAnalysis:
    fixture: Fixture
    expected_goals: tuple[float, float] | None
    probabilities: dict[str, float] = field(default_factory=dict)
    skipped_reason: str | None = None
    n_quotes: int = 0


@dataclass
class PrematchReport:
    mode: str
    model_name: str
    model_version: str
    model_fitted_on: str
    n_history: int
    fixtures: list[FixtureAnalysis]
    selection: SelectionResult
    generated_at: datetime


def run_prematch(
    history: list[HistoricalResult],
    fixtures: list[Fixture],
    quotes: list[OddsQuote],
    cfg: SelectionConfig | None = None,
    fit_cfg: dc.FitConfig | None = None,
    now: datetime | None = None,
    n_samples: int = 300,
) -> PrematchReport:
    now = now or datetime.now(timezone.utc)
    cfg = cfg or SelectionConfig()
    modes = {r.mode for r in history} | {f.mode for f in fixtures} | {q.mode for q in quotes}
    if len(modes) > 1:
        raise MixedModeError(f"inputs mix data modes {sorted(modes)}")
    mode = modes.pop() if modes else "real"

    # Fit on canonical names so the same club matches across providers.
    canon = [dc.MatchResult(r.played_on, normalise(r.home), normalise(r.away), r.home_goals, r.away_goals)
             for r in history if r.played_on < now.date()]
    model = dc.fit(canon, reference_date=now.date(), config=fit_cfg)

    by_event = _quotes_by_fixture(fixtures, quotes)
    analyses, candidates = [], []
    for fx in fixtures:
        h, a = normalise(fx.home), normalise(fx.away)
        if fx.status != "scheduled":
            analyses.append(FixtureAnalysis(fx, None, skipped_reason=f"status {fx.status}"))
            continue
        if fx.kickoff <= now:
            analyses.append(FixtureAnalysis(fx, None, skipped_reason="already kicked off"))
            continue
        if fx.kickoff.astimezone(LOCAL_TZ).date() != now.astimezone(LOCAL_TZ).date():
            analyses.append(FixtureAnalysis(fx, None, skipped_reason="not played today"))
            continue
        if fx.kickoff > now + timedelta(hours=cfg.max_hours_ahead):
            analyses.append(FixtureAnalysis(fx, None, skipped_reason=f"more than {cfg.max_hours_ahead:g}h before kick-off"))
            continue
        if not (model.knows(h) and model.knows(a)):
            analyses.append(FixtureAnalysis(fx, None, skipped_reason="insufficient match history for one team"))
            continue
        lam_mu = model.expected_goals(h, a)
        central = model.score_matrix(h, a)
        samples = model.sample_score_matrices(h, a, n=n_samples, seed=zlib.crc32(f"{h}|{a}".encode()))
        fq = by_event.get(fx.provider_event_id, [])
        fa = FixtureAnalysis(fx, lam_mu, n_quotes=len(fq))
        if not fq:
            fa.skipped_reason = "no odds observed"
        consensus = _market_consensus(fq)
        for key, best in _best_prices(fq, now).items():
            out = outcome(central, key)
            fa.probabilities[key.label()] = round(out.win, 4)
            win_samples = np.array([outcome(s, key).win for s in samples])
            min_matches = min(model.matches_per_team[h], model.matches_per_team[a])
            age = (now - best.observed_at).total_seconds()
            quality = (0.5 * min(1.0, min_matches / 30)
                       + 0.25 * max(0.0, 1 - age / 1800)
                       + 0.25 * (1.0 if key in consensus else 0.0))
            candidates.append(Candidate(
                match_id=fx.provider_event_id, match_label=f"{fx.home} - {fx.away}",
                market=key.market, selection=key.selection, line=key.line, odds=best.odds,
                bookmaker=best.bookmaker, odds_observed_at=best.observed_at, odds_expires_at=best.expires_at,
                outcome=out, win_prob_samples=win_samples, push_prob=out.push,
                market_fair_prob=consensus.get(key), data_quality=quality, data_mode=mode,
            ))
        analyses.append(fa)

    return PrematchReport(mode, dc.MODEL_NAME, model.version, model.reference_date.isoformat(), model.n_matches,
                          analyses, select(candidates, cfg, now), now)


def _quotes_by_fixture(fixtures: list[Fixture], quotes: list[OddsQuote]) -> dict[str, list[OddsQuote]]:
    index = {}
    for fx in fixtures:
        index[(normalise(fx.home), normalise(fx.away))] = fx
    out: dict[str, list[OddsQuote]] = defaultdict(list)
    for q in quotes:
        fx = index.get((normalise(q.home), normalise(q.away)))
        if fx and abs(fx.kickoff - q.kickoff) <= timedelta(hours=3):
            out[fx.provider_event_id].append(q)
    return out


def _best_prices(quotes: list[OddsQuote], now: datetime) -> dict[MarketKey, OddsQuote]:
    best: dict[MarketKey, OddsQuote] = {}
    for q in quotes:
        if q.validation_status != "valid" or (q.expires_at and q.expires_at <= now):
            continue
        k = MarketKey(q.market, q.selection, q.line)
        if k not in best or q.odds > best[k].odds:
            best[k] = q
    return best


def _market_consensus(quotes: list[OddsQuote]) -> dict[MarketKey, float]:
    """Margin-free market probability: Shin de-vig per bookmaker, then averaged."""
    per_book: dict[tuple, dict[MarketKey, float]] = defaultdict(dict)
    for q in quotes:
        if q.validation_status == "valid":
            per_book[(q.bookmaker,)][MarketKey(q.market, q.selection, q.line)] = q.odds
    acc: dict[MarketKey, list[float]] = defaultdict(list)
    for prices in per_book.values():
        seen = set()
        for k in prices:
            group = market_group(k)
            if not group or tuple(group) in seen or not all(g in prices for g in group):
                continue
            seen.add(tuple(group))
            for g, p in zip(group, devig_shin([prices[g] for g in group])):
                acc[g].append(p)
    return {k: float(np.mean(v)) for k, v in acc.items()}
