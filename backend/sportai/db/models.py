"""Database schema (SQLAlchemy 2.0). PostgreSQL in production; SQLite works for quick tests.

Design rules encoded here:
- every data row carries `mode` ("real" or "simulation");
- candidates are stored whether accepted or rejected, so the filter can be audited;
- a selection's published odds are immutable; corrections go to `selection_corrections`;
- payment events are stored once per provider event id (idempotency).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notification_prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    entitlements: Mapped[list[Entitlement]] = relationship(back_populates="user")


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Plan(Base):
    __tablename__ = "plans"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    features: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["football.selections", "live.alerts"]
    price_label: Mapped[str] = mapped_column(String(64), default="")
    shopify_skus: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Entitlement(Base):
    __tablename__ = "entitlements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    source: Mapped[str] = mapped_column(String(32))  # shopify | admin
    external_ref: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | cancelled | refunded | expired
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    user: Mapped[User] = relationship(back_populates="entitlements")


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (UniqueConstraint("provider", "event_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(255))
    topic: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="received")  # received | processed | ignored | error
    error: Mapped[str | None] = mapped_column(Text)


class Competition(Base):
    __tablename__ = "competitions"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    sport: Mapped[str] = mapped_column(String(32), default="football")
    name: Mapped[str] = mapped_column(String(128))
    monitored: Mapped[bool] = mapped_column(Boolean, default=False)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sport: Mapped[str] = mapped_column(String(32), default="football")
    kind: Mapped[str] = mapped_column(String(16))  # prematch | live
    competition: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))  # completed | provider_unavailable | failed
    status_detail: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(32))  # selections | no_opportunity
    model_name: Mapped[str | None] = mapped_column(String(64))
    model_version: Mapped[str | None] = mapped_column(String(32))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    data_sources: Mapped[dict] = mapped_column(JSON, default=dict)
    fixtures: Mapped[list] = mapped_column(JSON, default=list)
    triggered_by: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidates: Mapped[list[CandidateRow]] = relationship(back_populates="run")


class CandidateRow(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    match_id: Mapped[str] = mapped_column(String(64))
    match_label: Mapped[str] = mapped_column(String(255))
    market: Mapped[str] = mapped_column(String(32))
    selection: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float)
    odds: Mapped[float] = mapped_column(Float)
    bookmaker: Mapped[str] = mapped_column(String(64))
    odds_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    p_win: Mapped[float] = mapped_column(Float)
    p_push: Mapped[float] = mapped_column(Float)
    p_low: Mapped[float] = mapped_column(Float)
    fair_odds: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    ev_low: Mapped[float] = mapped_column(Float)
    ev_stress: Mapped[float] = mapped_column(Float)
    market_fair_prob: Mapped[float | None] = mapped_column(Float)
    data_quality: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    accepted: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    run: Mapped[AnalysisRun] = relationship(back_populates="candidates")


class Selection(Base):
    __tablename__ = "selections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    sport: Mapped[str] = mapped_column(String(32), default="football")
    kind: Mapped[str] = mapped_column(String(16))  # prematch | live
    mode: Mapped[str] = mapped_column(String(16))
    # pending_review | published | suspended | rejected | simulation_only
    status: Mapped[str] = mapped_column(String(24), index=True)
    match_id: Mapped[str] = mapped_column(String(64))
    match_label: Mapped[str] = mapped_column(String(255))
    competition: Mapped[str] = mapped_column(String(16))
    market: Mapped[str] = mapped_column(String(32))
    selection: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float)
    odds: Mapped[float] = mapped_column(Float)  # observed price; frozen once published
    bookmaker: Mapped[str] = mapped_column(String(64))
    odds_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    p_win: Mapped[float] = mapped_column(Float)
    p_push: Mapped[float] = mapped_column(Float)
    fair_odds: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    ev_low: Mapped[float] = mapped_column(Float)
    model_name: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(32))
    rationale: Mapped[str] = mapped_column(Text, default="")
    qc_review: Mapped[dict | None] = mapped_column(JSON)  # latest review by the QC agent (keep / flag / reject)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(String(36))
    result: Mapped[str | None] = mapped_column(String(8))  # win | lose | push | void
    profit: Mapped[float | None] = mapped_column(Float)  # per unit staked at published odds
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SelectionCorrection(Base):
    __tablename__ = "selection_corrections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    selection_id: Mapped[str] = mapped_column(ForeignKey("selections.id"), index=True)
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    by_user: Mapped[str] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OddsObservation(Base):
    __tablename__ = "odds_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    bookmaker: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    event_label: Mapped[str] = mapped_column(String(255))
    market: Mapped[str] = mapped_column(String(32))
    selection: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float)
    odds: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_status: Mapped[str] = mapped_column(String(16))
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(16))


class AlertRow(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(32), default="football")
    kind: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))
    commercial: Mapped[bool] = mapped_column(Boolean)
    match_id: Mapped[str] = mapped_column(String(64))
    match_label: Mapped[str] = mapped_column(String(255))
    minute: Mapped[float] = mapped_column(Float)
    score: Mapped[str] = mapped_column(String(16))
    market: Mapped[str | None] = mapped_column(String(64))
    odds: Mapped[float | None] = mapped_column(Float)
    bookmaker: Mapped[str | None] = mapped_column(String(64))
    probability: Mapped[float | None] = mapped_column(Float)
    fair_odds: Mapped[float | None] = mapped_column(Float)
    ev: Mapped[float | None] = mapped_column(Float)
    data_updated_at: Mapped[str] = mapped_column(String(40))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    invalidation: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderStatus(Base):
    __tablename__ = "provider_status"
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    quota: Mapped[dict] = mapped_column(JSON, default=dict)


class CarnetEntry(Base):
    """A proposition (official ticket, idea, match position, joueur pro bet) a subscriber put in their notebook.
    ``item_id`` is the page's id of the proposition, e.g. "ticket:2026-10-21:safe"; the page shows how many
    subscribers hold each one."""
    __tablename__ = "carnet_entries"
    __table_args__ = (UniqueConstraint("user_id", "item_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column(String(160), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # ticket | idee | position | joueur | pari | chasseur
    label: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
