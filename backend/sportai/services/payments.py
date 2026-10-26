"""Stripe and PayPal webhooks -> entitlements.

The site sells the subscription itself: the customer creates an account first,
then pays on Stripe Checkout or PayPal, and this backend decides access. Each
plan lists the provider prices that sell it (`Plan.payment_prices`: Stripe
price id or PayPal plan id -> billing period in days), so the three formulas
(1, 3 and 12 months) can all open the same plan.

Stripe (subscriptions):
- invoice.paid grants or extends the plan until the invoice line's period end
  (first payment and every renewal arrive the same way);
- customer.subscription.deleted stops renewing (access runs to the paid end);
- charge.refunded revokes access now.

PayPal (subscriptions):
- BILLING.SUBSCRIPTION.ACTIVATED grants the plan until the next billing time;
- PAYMENT.SALE.COMPLETED on that subscription extends it by one period;
- BILLING.SUBSCRIPTION.CANCELLED / EXPIRED / SUSPENDED stop renewing;
- PAYMENT.SALE.REFUNDED revokes access now.

Every webhook is authenticated (Stripe: HMAC-SHA256 signature header; PayPal:
the verify-webhook-signature API) and stored once per (provider, event id).
No payment card data ever reaches this app.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from sportai.config import Settings
from sportai.db.models import Entitlement, PaymentEvent, Plan, User
from sportai.services.accounts import audit

GRACE = timedelta(days=3)
STRIPE_TOLERANCE_S = 300
STRIPE_TOPICS = {"invoice.paid", "customer.subscription.deleted", "charge.refunded"}
PAYPAL_TOPICS = {"BILLING.SUBSCRIPTION.ACTIVATED", "PAYMENT.SALE.COMPLETED", "BILLING.SUBSCRIPTION.CANCELLED",
                 "BILLING.SUBSCRIPTION.EXPIRED", "BILLING.SUBSCRIPTION.SUSPENDED", "PAYMENT.SALE.REFUNDED"}


# ---------- authentication ----------

def verify_stripe(raw_body: bytes, header: str | None, secret: str | None, now: float | None = None) -> bool:
    """Stripe-Signature: t=<unix>,v1=<hex hmac of "t.body">[,v1=...]."""
    if not secret or not header:
        return False
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    ts = next((v for k, v in parts if k == "t"), None)
    sigs = [v for k, v in parts if k == "v1"]
    if not ts or not sigs or not ts.isdigit():
        return False
    if abs((now or time.time()) - int(ts)) > STRIPE_TOLERANCE_S:
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


def verify_paypal(headers: dict, event: dict, s: Settings) -> bool:
    """Ask PayPal whether this delivery is genuine (verify-webhook-signature)."""
    if not (s.paypal_client_id and s.paypal_client_secret and s.paypal_webhook_id):
        return False
    body = {"auth_algo": headers.get("paypal-auth-algo"), "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": s.paypal_webhook_id, "webhook_event": event}
    if not all(body.values()):
        return False
    try:
        with httpx.Client(base_url=s.paypal_api_base, timeout=10) as c:
            tok = c.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                         auth=(s.paypal_client_id, s.paypal_client_secret))
            tok.raise_for_status()
            r = c.post("/v1/notifications/verify-webhook-signature", json=body,
                       headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
            r.raise_for_status()
            return r.json().get("verification_status") == "SUCCESS"
    except (httpx.HTTPError, KeyError, ValueError):
        return False


# ---------- storage ----------

def record_event(db: DB, provider: str, event_id: str, topic: str, payload: dict) -> PaymentEvent | None:
    """Store the event; returns None if it was already received (duplicate delivery)."""
    ev = PaymentEvent(provider=provider, event_id=event_id, topic=topic, payload=payload)
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return ev


def process_event(db: DB, ev: PaymentEvent, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    handlers = {"stripe": (STRIPE_TOPICS, _stripe), "paypal": (PAYPAL_TOPICS, _paypal)}
    topics, handler = handlers.get(ev.provider, (set(), None))
    try:
        ev.status = handler(db, ev, now) if ev.topic in topics else "ignored"
        ev.error = None
    except (KeyError, TypeError, ValueError) as e:
        db.rollback()
        ev.status, ev.error = "error", f"{type(e).__name__}: {e}"
    db.add(ev)
    db.commit()


def replay_awaiting_account(db: DB, email: str) -> int:
    """After sign-up, apply payments that arrived before the account existed."""
    n = 0
    for ev in db.scalars(select(PaymentEvent).where(PaymentEvent.status == "awaiting_account")):
        if _event_email(ev) == email.lower():
            process_event(db, ev)
            n += 1
    return n


# ---------- Stripe ----------

def _stripe(db: DB, ev: PaymentEvent, now: datetime) -> str:
    obj = ev.payload["data"]["object"]
    if ev.topic == "invoice.paid":
        prices = _prices(db)
        lines = [(li, prices[(li.get("price") or {}).get("id")]) for li in (obj.get("lines") or {}).get("data", [])
                 if (li.get("price") or {}).get("id") in prices]
        if not lines:
            return "ignored"
        user = _user(db, _event_email(ev))
        if user is None:
            return "awaiting_account"
        paid_at = _ts((obj.get("status_transitions") or {}).get("paid_at")) or now
        for li, (plan, days) in lines:
            period_end = _ts((li.get("period") or {}).get("end"))
            _grant(db, user, plan, f"stripe:{obj['customer']}", paid_at,
                   max(period_end or paid_at, paid_at + timedelta(days=days)) + GRACE,
                   {"invoice": obj.get("id"), "subscription": obj.get("subscription")})
        return "processed"
    if ev.topic == "customer.subscription.deleted":
        return _stop_renewal(db, f"stripe:{obj['customer']}", "cancelled", obj.get("id"))
    return _revoke(db, f"stripe:{obj['customer']}", "refunded")  # charge.refunded


# ---------- PayPal ----------

def _paypal(db: DB, ev: PaymentEvent, now: datetime) -> str:
    res = ev.payload["resource"]
    if ev.topic == "BILLING.SUBSCRIPTION.ACTIVATED":
        price = _prices(db).get(res.get("plan_id"))
        if price is None:
            return "ignored"
        user = _user(db, _event_email(ev))
        if user is None:
            return "awaiting_account"
        plan, days = price
        start = _iso(res.get("start_time")) or now
        nxt = _iso((res.get("billing_info") or {}).get("next_billing_time"))
        _grant(db, user, plan, f"paypal:{res['id']}", start, max(nxt or start, start + timedelta(days=days)) + GRACE,
               {"subscription": res["id"]})
        return "processed"
    if ev.topic == "PAYMENT.SALE.COMPLETED":
        sub = res.get("billing_agreement_id")
        activated = _paypal_activation(db, sub) if sub else None
        price = _prices(db).get(activated["plan_id"]) if activated else None
        if price is None:
            return "ignored"
        paid_at = _iso(res.get("create_time")) or now
        rows = _active(db, f"paypal:{sub}")
        for e in rows:
            e.ends_at = max(_aware(e.ends_at) if e.ends_at else paid_at, paid_at + timedelta(days=price[1]) + GRACE)
            audit(db, None, "entitlement.renew", e.user_id, {"subscription": sub, "sale": res.get("id")})
        return "processed" if rows else "ignored"
    if ev.topic == "PAYMENT.SALE.REFUNDED":
        sale = _paypal_sale(db, res.get("sale_id"))
        sub = sale and sale.get("billing_agreement_id")
        return _revoke(db, f"paypal:{sub}", "refunded") if sub else "ignored"
    return _stop_renewal(db, f"paypal:{res['id']}", ev.topic.rsplit(".", 1)[1].lower(), res["id"])


def _paypal_activation(db: DB, sub: str) -> dict | None:
    for ev in db.scalars(select(PaymentEvent).where(PaymentEvent.provider == "paypal",
                                                    PaymentEvent.topic == "BILLING.SUBSCRIPTION.ACTIVATED")):
        if (ev.payload.get("resource") or {}).get("id") == sub:
            return ev.payload["resource"]
    return None


def _paypal_sale(db: DB, sale_id: str | None) -> dict | None:
    if not sale_id:
        return None
    for ev in db.scalars(select(PaymentEvent).where(PaymentEvent.provider == "paypal",
                                                    PaymentEvent.topic == "PAYMENT.SALE.COMPLETED")):
        if (ev.payload.get("resource") or {}).get("id") == sale_id:
            return ev.payload["resource"]
    return None


# ---------- shared ----------

def _prices(db: DB) -> dict[str, tuple[Plan, int]]:
    out = {}
    for p in db.scalars(select(Plan).where(Plan.active.is_(True))):
        for price_id, days in (p.payment_prices or {}).items():
            out[price_id] = (p, int(days))
    return out


def _event_email(ev: PaymentEvent) -> str | None:
    if ev.provider == "stripe":
        obj = (ev.payload.get("data") or {}).get("object") or {}
        e = obj.get("customer_email")
    else:
        e = ((ev.payload.get("resource") or {}).get("subscriber") or {}).get("email_address")
    return e.lower() if e else None


def _user(db: DB, email: str | None) -> User | None:
    return db.scalar(select(User).where(User.email == email)) if email else None


def _active(db: DB, ref: str) -> list[Entitlement]:
    return list(db.scalars(select(Entitlement).where(Entitlement.external_ref == ref, Entitlement.status == "active")))


def _grant(db: DB, user: User, plan: Plan, ref: str, starts: datetime, ends: datetime, detail: dict) -> None:
    source = ref.split(":", 1)[0]
    existing = db.scalar(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.plan_code == plan.code,
                                                   Entitlement.source == source, Entitlement.status == "active"))
    if existing:
        # A renewal extends the running entitlement instead of stacking a new one.
        existing.ends_at = max(_aware(existing.ends_at) if existing.ends_at else ends, ends)
        existing.external_ref = existing.external_ref or ref
    else:
        db.add(Entitlement(user_id=user.id, plan_code=plan.code, source=source, external_ref=ref,
                           status="active", starts_at=starts, ends_at=ends))
    audit(db, None, "entitlement.grant", user.id, {"plan": plan.code, **detail})


def _revoke(db: DB, ref: str, status: str) -> str:
    rows = _active(db, ref)
    for e in rows:
        e.status = status
        e.ends_at = datetime.now(timezone.utc)
        audit(db, None, f"entitlement.{status}", e.user_id, {"ref": ref})
    return "processed" if rows else "ignored"


def _stop_renewal(db: DB, ref: str, status: str, subscription: str | None) -> str:
    rows = _active(db, ref)
    for e in rows:
        # Already paid until ends_at; no renewal will extend it.
        audit(db, None, "entitlement.subscription_" + status, e.user_id, {"subscription": subscription})
    return "processed" if rows else "ignored"


def _ts(v) -> datetime | None:
    return datetime.fromtimestamp(int(v), timezone.utc) if v else None


def _iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
