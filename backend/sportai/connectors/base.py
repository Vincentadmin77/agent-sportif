"""Provider-neutral data types and adapter interfaces.

The engine and the selection pipeline only see these types, so a provider can
be swapped (Winamax via The Odds API today, a direct partner feed tomorrow)
without touching the statistical code.

Every record carries `mode`: "real" for data that came from an authorised
provider, "simulation" for generated data. Publication refuses anything that
is not "real".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol

DataMode = Literal["real", "simulation"]


class ProviderUnavailable(RuntimeError):
    """The provider could not be reached or refused the request.

    Callers must disable the analyses that need this data, never fill the gap.
    """


class ProviderNotConfigured(ProviderUnavailable):
    """No credentials were configured for this provider."""


@dataclass(frozen=True)
class Fixture:
    provider: str
    provider_event_id: str
    competition: str
    kickoff: datetime
    home: str
    away: str
    mode: DataMode
    status: str = "scheduled"  # scheduled, live, finished, postponed, cancelled
    home_goals: int | None = None
    away_goals: int | None = None


@dataclass(frozen=True)
class OddsQuote:
    """One price observed at one bookmaker at one instant."""

    provider: str
    bookmaker: str
    provider_event_id: str
    home: str
    away: str
    kickoff: datetime
    market: str  # internal market key: 1x2, totals, btts, dnb, double_chance
    selection: str
    line: float | None
    odds: float
    observed_at: datetime
    mode: DataMode
    validation_status: str = "unvalidated"  # unvalidated, valid, rejected
    expires_at: datetime | None = None
    is_live: bool = False


@dataclass(frozen=True)
class LiveSnapshot:
    provider: str
    provider_event_id: str
    minute: float
    home_goals: int
    away_goals: int
    observed_at: datetime
    mode: DataMode
    home_red_cards: int = 0
    away_red_cards: int = 0
    home_xg: float | None = None
    away_xg: float | None = None
    events: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class HistoricalResult:
    provider: str
    competition: str
    played_on: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    mode: DataMode


class FixturesProvider(Protocol):
    name: str

    def upcoming_fixtures(self, competition: str, days_ahead: int = 3) -> list[Fixture]: ...

    def results(self, competition: str, season: str) -> list[HistoricalResult]: ...


class OddsProvider(Protocol):
    name: str

    def odds(self, competition: str) -> list[OddsQuote]: ...


class LiveProvider(Protocol):
    name: str

    def live_snapshot(self, provider_event_id: str) -> LiveSnapshot: ...
