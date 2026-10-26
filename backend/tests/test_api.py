import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///./test-sportai.db")
SECRET = "whsec_test"


@pytest.fixture(scope="module")
def client():
    os.environ.update(DATABASE_URL=DB_URL, DATA_MODE="simulation", COOKIE_SECURE="false", STRIPE_WEBHOOK_SECRET=SECRET,
                      PAYPAL_CLIENT_ID="pp-id", PAYPAL_CLIENT_SECRET="pp-secret", PAYPAL_WEBHOOK_ID="WH-1")
    from sportai.config import get_settings
    get_settings.cache_clear()
    from alembic import command
    from alembic.config import Config

    eng = create_engine(DB_URL)
    with eng.begin() as c:
        if eng.dialect.name == "postgresql":
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    eng.dispose()
    if DB_URL.startswith("sqlite"):
        path = DB_URL.removeprefix("sqlite:///")
        if os.path.exists(path):
            os.remove(path)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.upgrade(cfg, "head")

    from sportai.db.session import reset_engine, session_factory
    reset_engine()
    from sportai import manage
    from sportai.services import accounts
    db = session_factory()()
    manage.init(db)
    accounts.create_user(db, "admin@example.com", "admin-password-1", role="admin")
    db.close()
    from sportai.api.main import app
    from sportai.api.deps import login_limiter
    login_limiter.hits.clear()
    yield TestClient(app)


def login(c, email, pw):
    c.cookies.clear()
    r = c.post("/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text


def make_real_selection(odds_age_minutes=5, status="pending_review"):
    from sportai.db.models import AnalysisRun, CandidateRow, Selection
    from sportai.db.session import session_factory
    db = session_factory()()
    now = datetime.now(timezone.utc)
    run = AnalysisRun(kind="prematch", competition="FR1", mode="real", status="completed", outcome="selections")
    db.add(run); db.flush()
    observed = now - timedelta(minutes=odds_age_minutes)
    cand = CandidateRow(run_id=run.id, mode="real", match_id="1", match_label="A - B", market="1x2", selection="home",
                        odds=1.85, bookmaker="winamax_fr", odds_observed_at=observed, p_win=0.6, p_push=0, p_low=0.57,
                        fair_odds=1.67, ev=0.11, ev_low=0.05, ev_stress=0.07, data_quality=0.9, score=0.04,
                        accepted=True, reasons=[])
    db.add(cand); db.flush()
    sel = Selection(run_id=run.id, candidate_id=cand.id, kind="prematch", mode="real", status=status, match_id="1",
                    match_label="A - B", competition="FR1", market="1x2", selection="home", odds=1.85,
                    bookmaker="winamax_fr", odds_observed_at=observed, p_win=0.6, p_push=0, fair_odds=1.67, ev=0.11,
                    ev_low=0.05, model_name="dixon-coles", model_version="1.0.0", rationale="test")
    db.add(sel); db.commit()
    sid = sel.id
    db.close()
    return sid


def test_health_and_public(client):
    assert client.get("/health").json()["status"] == "ok"
    assert {p["code"] for p in client.get("/public/plans").json()} >= {"free", "football_premium", "live_alerts"}
    assert client.get("/public/performance").json()["n"] == 0


def test_register_login_and_gating(client):
    client.cookies.clear()
    r = client.post("/auth/register", json={"email": "user@example.com", "password": "user-password-1"})
    assert r.status_code == 201
    me = client.get("/me").json()
    assert me["role"] == "user" and "football.selections" not in me["features"]
    assert client.get("/selections").json()["access"] == "teaser"
    assert client.get("/selections/history").status_code == 403
    assert client.get("/alerts").status_code == 403
    assert client.get("/admin/runs").status_code == 403
    client.cookies.clear()
    assert client.get("/me").status_code == 401
    assert client.post("/auth/login", json={"email": "user@example.com", "password": "wrong-password"}).status_code == 401


def test_mutations_require_json(client):
    r = client.post("/auth/login", data={"email": "a@b.co", "password": "x" * 12})
    assert r.status_code == 415


def test_simulation_run_never_publishable(client):
    login(client, "admin@example.com", "admin-password-1")
    run = client.post("/admin/runs/prematch", json={"mode": "simulation"}).json()
    assert run["status"] == "completed" and run["mode"] == "simulation"
    detail = client.get(f"/admin/runs/{run['id']}").json()
    assert len(detail["candidates"]) > 0
    for s in detail["selections"]:
        assert s["status"] == "simulation_only"
        assert client.post(f"/admin/selections/{s['id']}", json={"action": "publish"}).status_code == 409


def test_real_run_without_keys_is_marked_unavailable(client):
    login(client, "admin@example.com", "admin-password-1")
    run = client.post("/admin/runs/prematch", json={"mode": "real", "competition": "FR1"}).json()
    assert run["status"] == "provider_unavailable" and "not set" in run["status_detail"]
    assert client.get(f"/admin/runs/{run['id']}").json()["candidates"] == []


def test_publish_settle_and_track_record(client):
    sid = make_real_selection()
    stale = make_real_selection(odds_age_minutes=120)
    login(client, "admin@example.com", "admin-password-1")
    assert client.post(f"/admin/selections/{stale}", json={"action": "publish"}).status_code == 409
    assert client.post(f"/admin/selections/{sid}", json={"action": "publish"}).status_code == 200
    r = client.post(f"/admin/selections/{sid}", json={"action": "correct", "field": "odds", "value": "2.5", "reason": "x"})
    assert r.status_code == 409
    assert client.post(f"/admin/selections/{sid}", json={"action": "settle", "result": "lose"}).status_code == 200
    perf = client.get("/public/performance").json()
    assert perf["n"] == 1 and perf["roi"] == -1.0 and "sample too small" in perf["note"]
    hist = client.get("/public/history").json()
    assert any(h["id"] == sid and h["result"] == "lose" for h in hist)


def test_published_odds_immutable_in_database(client):
    if not DB_URL.startswith("postgresql"):
        pytest.skip("database trigger is PostgreSQL-only")
    sid = make_real_selection()
    login(client, "admin@example.com", "admin-password-1")
    client.post(f"/admin/selections/{sid}", json={"action": "publish"})
    eng = create_engine(DB_URL)
    with pytest.raises(Exception, match="immutable"):
        with eng.begin() as c:
            c.execute(text("UPDATE selections SET odds = 3.0 WHERE id = :i"), {"i": sid})
    with pytest.raises(Exception, match="cannot be deleted"):
        with eng.begin() as c:
            c.execute(text("DELETE FROM selections WHERE id = :i"), {"i": sid})


def test_selection_settings_validation(client):
    login(client, "admin@example.com", "admin-password-1")
    assert client.put("/admin/settings/selection", json={"max_selections": 9}).status_code == 400
    r = client.put("/admin/settings/selection", json={"min_ev": 0.04})
    assert r.status_code == 200 and r.json()["min_ev"] == 0.04


def _stripe(client, topic, obj, event_id, secret=SECRET, ts=None):
    raw = json.dumps({"id": event_id, "type": topic, "data": {"object": obj}}).encode()
    t = str(int(ts or time.time()))
    sig = hmac.new(secret.encode(), t.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return client.post("/webhooks/stripe", content=raw, headers={
        "content-type": "application/json", "stripe-signature": f"t={t},v1={sig}"})


def _set_prices(prices):
    from sportai.db.models import Plan
    from sportai.db.session import session_factory
    db = session_factory()()
    db.get(Plan, "football_premium").payment_prices = prices
    db.commit(); db.close()


def test_stripe_webhook_flow(client):
    _set_prices({"price_month": 31, "P-QUARTER": 92})
    now = int(time.time())
    invoice = {"id": "in_1", "customer": "cus_1", "customer_email": "buyer@example.com", "subscription": "sub_1",
               "status_transitions": {"paid_at": now},
               "lines": {"data": [{"price": {"id": "price_month"}, "period": {"start": now, "end": now + 31 * 86400}}]}}
    assert _stripe(client, "invoice.paid", invoice, "evt_1", secret="wrong").status_code == 401
    assert _stripe(client, "invoice.paid", invoice, "evt_1", ts=now - 3600).status_code == 401  # replayed
    assert _stripe(client, "invoice.paid", invoice, "evt_1").json()["status"] == "awaiting_account"
    assert _stripe(client, "invoice.paid", invoice, "evt_1").json()["status"] == "duplicate"

    client.cookies.clear()
    client.post("/auth/register", json={"email": "buyer@example.com", "password": "buyer-password-1"})
    assert "football.selections" in client.get("/me").json()["features"]
    assert client.get("/selections").json()["access"] == "full"

    assert _stripe(client, "customer.subscription.deleted", {"id": "sub_1", "customer": "cus_1"},
                   "evt_2").json()["status"] == "processed"
    assert "football.selections" in client.get("/me").json()["features"]  # paid period still runs
    assert _stripe(client, "charge.refunded", {"id": "ch_1", "customer": "cus_1"}, "evt_3").json()["status"] == "processed"
    assert "football.selections" not in client.get("/me").json()["features"]
    assert _stripe(client, "customer.created", {"id": "cus_2"}, "evt_4").json()["status"] == "ignored"


def test_paypal_webhook_flow(client, monkeypatch):
    from sportai.services import payments
    _set_prices({"price_month": 31, "P-QUARTER": 92})
    genuine = {"ok": True}
    monkeypatch.setattr(payments, "verify_paypal", lambda headers, event, s: genuine["ok"])

    def hook(topic, resource, event_id):
        return client.post("/webhooks/paypal", json={"id": event_id, "event_type": topic, "resource": resource})

    client.cookies.clear()
    client.post("/auth/register", json={"email": "paypal@example.com", "password": "paypal-password-1"})
    sub = {"id": "I-SUB1", "plan_id": "P-QUARTER", "subscriber": {"email_address": "PayPal@example.com"},
           "start_time": datetime.now(timezone.utc).isoformat()}
    genuine["ok"] = False
    assert hook("BILLING.SUBSCRIPTION.ACTIVATED", sub, "WH-A").status_code == 401
    genuine["ok"] = True
    assert hook("BILLING.SUBSCRIPTION.ACTIVATED", sub, "WH-A").json()["status"] == "processed"
    assert "football.selections" in client.get("/me").json()["features"]
    sale = {"id": "SALE-1", "billing_agreement_id": "I-SUB1", "create_time": datetime.now(timezone.utc).isoformat()}
    assert hook("PAYMENT.SALE.COMPLETED", sale, "WH-B").json()["status"] == "processed"
    assert hook("BILLING.SUBSCRIPTION.CANCELLED", {"id": "I-SUB1"}, "WH-C").json()["status"] == "processed"
    assert "football.selections" in client.get("/me").json()["features"]
    assert hook("PAYMENT.SALE.REFUNDED", {"id": "R-1", "sale_id": "SALE-1"}, "WH-D").json()["status"] == "processed"
    assert "football.selections" not in client.get("/me").json()["features"]


def test_paypal_verification_fails_closed():
    from sportai.config import get_settings
    from sportai.services import payments
    assert payments.verify_paypal({}, {"id": "x"}, get_settings()) is False  # headers missing, no network call


def test_admin_cannot_demote_self(client):
    login(client, "admin@example.com", "admin-password-1")
    me = client.get("/me").json()
    assert client.put(f"/admin/users/{me['id']}", json={"role": "user"}).status_code == 400
