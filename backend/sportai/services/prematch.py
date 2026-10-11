"""Pre-match analysis pipeline: history -> model -> priced markets -> candidates -> selection.

Pure function of its inputs (no database, no network) so a run is
reproducible from the stored inputs. Mixing simulated and real inputs is an
error, not a warning.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import zlib
from zoneinfo import ZoneInfo

import numpy as np

from sportai.connectors.base import Fixture, HistoricalResult, OddsQuote
from sportai.connectors.teams import normalise
from sportai.engine.football import dixon_coles as dc
from sportai.engine.football.markets import MarketKey, likely_score, market_group, outcome
from sportai.engine.value import Outcome, devig_shin
from sportai.selection.engine import Candidate, SelectionConfig, SelectionResult, select


LOCAL_TZ = ZoneInfo("Europe/Paris")


# Goal markets always computed from the model, with or without odds.
GOAL_KEYS = [MarketKey("btts", "yes"), MarketKey("btts", "no")] + [
    MarketKey("totals", side, line) for line in (1.5, 2.5, 3.5) for side in ("over", "under")]


# Enriched goals model (sportai/services/goals_refresh.py): it replaces Dixon-Coles on these labels when
# the day's saved model knows both teams. Its probabilities come with no parameter samples of their own,
# so the pessimistic quantile reuses Dixon-Coles' spread around the enriched value, minus a modest haircut.
GOALS_HAIRCUT = 0.02
GOALS_MODEL_DC = f"{dc.MODEL_NAME} {dc.MODEL_VERSION}"
GoalsLookup = Callable[[str, str], tuple[dict[str, float] | None, str | None]]


class MixedModeError(ValueError):
    pass


@dataclass
class FixtureAnalysis:
    fixture: Fixture
    expected_goals: tuple[float, float] | None
    probabilities: dict[str, float] = field(default_factory=dict)
    skipped_reason: str | None = None
    n_quotes: int = 0
    likely_score: tuple[int, int, float] | None = None  # estimated final score (most likely outcome, then score) and its probability
    goals_model: str | None = None  # which model priced btts / totals
    goals_model_note: str | None = None  # why the enriched model was not used, when it was not


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
    goals_lookup: GoalsLookup | None = None,
    goals_model_label: str | None = None,
) -> PrematchReport:
    """goals_lookup(home, away) -> (enriched probabilities by market label, None) or (None, reason): when
    given, btts and over/under 1.5 / 2.5 / 3.5 are priced by the enriched goals model (fallback: Dixon-Coles)."""
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
        model_only = {k.label(): round(outcome(central, k).win, 4) for k in GOAL_KEYS}
        enriched, note = goals_lookup(fx.home, fx.away) if goals_lookup else (None, None)
        if enriched:
            model_only.update({k: round(v, 4) for k, v in enriched.items()})
        samples = model.sample_score_matrices(h, a, n=n_samples, seed=zlib.crc32(f"{h}|{a}".encode()))
        fq = by_event.get(fx.provider_event_id, [])
        fa = FixtureAnalysis(fx, lam_mu, probabilities=dict(model_only), n_quotes=len(fq),
                             goals_model=(goals_model_label or "enriched") if enriched else GOALS_MODEL_DC,
                             goals_model_note=None if enriched else note)
        fa.likely_score = likely_score(central)
        if not fq:
            fa.skipped_reason = "no odds observed"
        consensus = _market_consensus(fq)
        for key, best in _best_prices(fq, now).items():
            out = outcome(central, key)
            win_samples = np.array([outcome(s, key).win for s in samples])
            if enriched and key.label() in enriched:
                p = float(enriched[key.label()])
                win_samples = np.clip((p + win_samples - out.win) * (1 - GOALS_HAIRCUT), 0.0, 1.0)
                out = Outcome(win=p)  # half lines only: no push
            fa.probabilities[key.label()] = round(out.win, 4)
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
