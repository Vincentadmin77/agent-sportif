"""Accounts, sessions, plans and entitlements. All access decisions happen here,
server-side; the frontend only reflects them."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from sportai.db.models import AuditLog, Entitlement, Plan, Session, User

_hasher = PasswordHasher()

DEFAULT_PLANS = [
    ("free", "Accès gratuit", ["public.history", "football.teaser"], "0 €"),
    ("football_premium", "Football Premium", ["football.selections", "football.analysis"], "à définir"),
    ("basketball_premium", "Basket Premium", ["basketball.selections", "basketball.analysis"], "à définir"),
    ("tennis_premium", "Tennis Premium", ["tennis.selections", "tennis.analysis"], "à définir"),
    ("multisport", "Offre multi-sports", ["football.selections", "football.analysis", "basketball.selections",
                                          "basketball.analysis", "tennis.selections", "tennis.analysis"], "à définir"),
    ("live_alerts", "Alertes live", ["live.alerts"], "à définir"),
    ("deep_analysis", "Analyses approfondies", ["football.analysis"], "à définir"),
]


def seed_plans(db: DB) -> None:
    for code, name, features, price in DEFAULT_PLANS:
        if db.get(Plan, code) is None:
            db.add(Plan(code=code, name=name, features=features, price_label=price, shopify_skus=[]))
    db.commit()


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(pw: str, h: str) -> bool:
    try:
        return _hasher.verify(h, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(db: DB, email: str, password: str, role: str = "user") -> User:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("email already registered")
    u = User(email=email, password_hash=hash_password(password), role=role)
    db.add(u)
    db.flush()
    db.add(Entitlement(user_id=u.id, plan_code="free", source="system"))
    audit(db, u.id, "user.register", u.id)
    db.commit()
    return u


def authenticate(db: DB, email: str, password: str) -> User | None:
    u = db.scalar(select(User).where(User.email == email.strip().lower()))
    if u is None:
        verify_password(password, _DUMMY_HASH)  # equalise timing for unknown emails
        return None
    if not u.is_active or not verify_password(password, u.password_hash):
        return None
    return u


def create_session(db: DB, user: User, days: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Session(token_hash=_token_hash(token), user_id=user.id,
                   expires_at=datetime.now(timezone.utc) + timedelta(days=days)))
    db.commit()
    return token


def user_for_token(db: DB, token: str | None) -> User | None:
    if not token:
        return None
    s = db.get(Session, _token_hash(token))
    if s is None or _aware(s.expires_at) < datetime.now(timezone.utc):
        return None
    u = db.get(User, s.user_id)
    return u if u and u.is_active else None


def delete_session(db: DB, token: str) -> None:
    s = db.get(Session, _token_hash(token))
    if s:
        db.delete(s)
        db.commit()


def features_for(db: DB, user: User | None) -> set[str]:
    if user is None:
        return {"public.history"}
    if user.role == "admin":
        return {"*"}
    now = datetime.now(timezone.utc)
    feats: set[str] = set()
    for e in db.scalars(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.status == "active")):
        if e.ends_at is not None and _aware(e.ends_at) < now:
            continue
        plan = db.get(Plan, e.plan_code)
        if plan and plan.active:
            feats |= set(plan.features)
    return feats


def has_feature(features: set[str], feature: str) -> bool:
    return "*" in features or feature in features


def audit(db: DB, actor: str | None, action: str, target: str | None = None, detail: dict | None = None) -> None:
    db.add(AuditLog(actor_id=actor, action=action, target=target, detail=detail or {}))


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")
