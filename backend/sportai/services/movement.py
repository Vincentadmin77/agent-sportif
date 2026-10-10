"""Market movement between two pre-match passes (3 h and 45 min before kick-off).

Line-ups come out about an hour before kick-off and prices move then. Comparing
the margin-free market probability of the 3 h pass with the 45 min pass tells
whether the market has moved towards the model (confirmation), away from it,
or not yet (the price may still be there). Informative only: it never changes
a probability, an edge or a selection decision.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from sportai.connectors.base import OddsQuote
from sportai.connectors.teams import normalise
from sportai.db.models import AnalysisRun, OddsObservation
from sportai.engine.football.markets import MarketKey

STABLE = 0.015  # moves smaller than 1.5 points of probability count as "not moved"
LABELS = {"confirmation": "le marché s'est rapproché du modèle (confirmation)",
          "against": "le marché s'est éloigné du modèle",
          "stable": "la cote n'a pas encore bougé"}


@dataclass(frozen=True)
class Movement:
    kind: str  # confirmation, against, stable
    before: float
    after: float

    def describe(self) -> str:
        return (f"Mouvement depuis le passage précédent : marché {self.before:.1%} -> {self.after:.1%}, "
                f"{LABELS[self.kind]}.")


def classify(model_p: float, before: float, after: float, stable: float = STABLE) -> Movement:
    delta = after - before
    if abs(delta) < stable:
        return Movement("stable", before, after)
    towards_model = (model_p - before) * delta > 0
    return Movement("confirmation" if towards_model else "against", before, after)


def previous_consensus(db: DB, competition: str, mode: str, now: datetime,
                       min_gap: timedelta = timedelta(minutes=20), max_age: timedelta = timedelta(hours=4),
                       ) -> dict[tuple[str, str], dict[MarketKey, float]]:
    """Margin-free market probabilities from the latest earlier pass of the same competition, by match."""
    from sportai.services.prematch import _market_consensus  # avoid an import cycle

    run = db.scalar(select(AnalysisRun).where(
        AnalysisRun.competition == competition, AnalysisRun.kind == "prematch", AnalysisRun.mode == mode,
        AnalysisRun.status == "completed", AnalysisRun.started_at <= now - min_gap,
        AnalysisRun.started_at >= now - max_age).order_by(AnalysisRun.started_at.desc()))
    if run is None:
        return {}
    by_match: dict[tuple[str, str], list[OddsQuote]] = defaultdict(list)
    for o in db.scalars(select(OddsObservation).where(OddsObservation.run_id == run.id)):
        home, _, away = o.event_label.partition(" - ")
        observed = o.observed_at if o.observed_at.tzinfo else o.observed_at.replace(tzinfo=timezone.utc)
        by_match[(normalise(home), normalise(away))].append(OddsQuote(
            o.provider, o.bookmaker, o.event_id, home, away, observed, o.market, o.selection, o.line, o.odds,
            observed, o.mode, o.validation_status))
    return {k: _market_consensus(v) for k, v in by_match.items()}
