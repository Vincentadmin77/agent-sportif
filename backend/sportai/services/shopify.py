"""Shopify webhooks -> entitlements.

Shopify sells the subscription (Shopify Subscriptions app or another app using
selling plans); this backend decides access. Flow:
- orders/paid: each line item whose SKU belongs to a plan grants (or extends)
  that plan for the customer's email, until paid_at + period + grace.
  Renewal orders created by the subscription app arrive the same way.
- subscription_contracts/update with status cancelled/expired/failed: the
  entitlement linked to the contract's origin order stops renewing (access
  runs to the end of the paid period).
- refunds/create on a plan line item, orders/cancelled: access is revoked now.

Every webhook is verified (HMAC-SHA256 of the raw body, base64, header
X-Shopify-Hmac-Sha256) and stored once per X-Shopify-Event-Id (idempotency).
No payment card data ever reaches this app.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from sportai.db.models import Entitlement, PaymentEvent, Plan, User
from sportai.services.accounts import audit

PERIOD = timedelta(days=31)
GRACE = timedelta(days=3)
HANDLED_TOPICS = {"orders/paid", "orders/cancelled", "refunds/create", "subscription_contracts/update",
                  "subscription_contracts/create"}


def verify_hmac(raw_body: bytes, header_hmac: str | None, secret: str | None) -> bool:
    if not secret or not header_hmac:
        return False
    digest = base64.b64encode(hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, header_hmac)


def record_event(db: DB, event_id: str, topic: str, payload: dict) -> PaymentEvent | None:
    """Store the event; returns None if it was already received (duplicate delivery)."""
    ev = PaymentEvent(provider="shopify", event_id=event_id, topic=topic, payload=payload)
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return ev


def process_event(db: DB, ev: PaymentEvent, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    try:
        if ev.topic not in HANDLED_TOPICS:
            ev.status = "ignored"
        elif ev.topic == "orders/paid":
            ev.status = _order_paid(db, ev.payload, now)
        elif ev.topic == "orders/cancelled":
            ev.status = _revoke(db, f"order:{ev.payload['id']}", "cancelled")
        elif ev.topic == "refunds/create":
            ev.status = _refund(db, ev.payload)
        else:
            ev.status = _contract(db, ev.payload)
        ev.error = None
    except (KeyError, TypeError, ValueError) as e:
        db.rollback()
        ev.status, ev.error = "error", f"{type(e).__name__}: {e}"
    db.add(ev)
    db.commit()


def replay_awaiting_account(db: DB, email: str) -> int:
    """After sign-up, apply paid orders that arrived before the account existed."""
    n = 0
    for ev in db.scalars(select(PaymentEvent).where(PaymentEvent.status == "awaiting_account")):
        if _email(ev.payload) == email.lower():
            process_event(db, ev)
            n += 1
    return n


def _plans_by_sku(db: DB) -> dict[str, Plan]:
    out = {}
    for p in db.scalars(select(Plan).where(Plan.active.is_(True))):
        for sku in p.shopify_skus or []:
            out[sku] = p
    return out


def _email(payload: dict) -> str | None:
    e = payload.get("email") or (payload.get("customer") or {}).get("email")
    return e.lower() if e else None


def _order_paid(db: DB, order: dict, now: datetime) -> str:
    plans = _plans_by_sku(db)
    items = [(li, plans[li.get("sku")]) for li in order.get("line_items", []) if li.get("sku") in plans]
    if not items:
        return "ignored"
    user = db.scalar(select(User).where(User.email == _email(order))) if _email(order) else None
    if user is None:
        return "awaiting_account"
    paid_at = _parse(order.get("processed_at") or order.get("created_at")) or now
    for li, plan in items:
        contract = li.get("subscription_contract_id") or (order.get("source_identifier") if order.get("source_name") == "subscription_contract" else None)
        ref = f"order:{order['id']}"
        existing = db.scalar(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.plan_code == plan.code,
                                                       Entitlement.source == "shopify", Entitlement.status == "active"))
        ends = paid_at + PERIOD + GRACE
        if existing:
            # A renewal extends the running entitlement instead of stacking a new one.
            existing.ends_at = max(_aware(existing.ends_at) if existing.ends_at else ends, ends)
            existing.external_ref = existing.external_ref or ref
        else:
            db.add(Entitlement(user_id=user.id, plan_code=plan.code, source="shopify", external_ref=ref,
                               status="active", starts_at=paid_at, ends_at=ends))
        audit(db, None, "entitlement.grant", user.id, {"plan": plan.code, "order": order["id"], "contract": contract})
    return "processed"


def _revoke(db: DB, ref: str, status: str) -> str:
    rows = list(db.scalars(select(Entitlement).where(Entitlement.external_ref == ref, Entitlement.status == "active")))
    for e in rows:
        e.status = status
        e.ends_at = datetime.now(timezone.utc)
        audit(db, None, f"entitlement.{status}", e.user_id, {"ref": ref})
    return "processed" if rows else "ignored"


def _refund(db: DB, refund: dict) -> str:
    plans = _plans_by_sku(db)
    refunded_plan_items = [r for r in refund.get("refund_line_items", []) if (r.get("line_item") or {}).get("sku") in plans]
    if not refunded_plan_items:
        return "ignored"
    return _revoke(db, f"order:{refund['order_id']}", "refunded")


def _contract(db: DB, contract: dict) -> str:
    status = str(contract.get("status", "")).lower()
    if status in ("cancelled", "expired", "failed"):
        rows = list(db.scalars(select(Entitlement).where(
            Entitlement.external_ref == f"order:{contract['origin_order_id']}", Entitlement.status == "active")))
        for e in rows:
            # Already paid until ends_at; no renewal will extend it.
            audit(db, None, "entitlement.contract_" + status, e.user_id, {"contract": contract.get("id")})
        return "processed" if rows else "ignored"
    return "ignored"


def _parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
