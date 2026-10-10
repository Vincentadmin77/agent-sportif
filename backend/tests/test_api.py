import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///./test-sportai.db")
SECRET = "test-shopify-secret"


PROVIDER_KEYS = ("API_FOOTBALL_KEY", "THE_ODDS_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture(scope="module")
def client():
    os.environ.update(DATABASE_URL=DB_URL, DATA_MODE="simulation", COOKIE_SECURE="false", SHOPIFY_WEBHOOK_SECRET=SECRET)
    # Tests never call real providers, even on a machine where real keys are configured.
    saved_keys = {k: os.environ.pop(k) for k in PROVIDER_KEYS if k in os.environ}
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
    os.environ.update(saved_keys)
    get_settings.cache_clear()


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


def _hook(client, topic, payload, event_id, secret=SECRET):
    raw = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
    return client.post("/webhooks/shopify", content=raw, headers={
        "content-type": "application/json", "x-shopify-hmac-sha256": sig, "x-shopify-topic": topic,
        "x-shopify-event-id": event_id})


def test_shopify_webhook_flow(client):
    from sportai.db.models import Plan
    from sportai.db.session import session_factory
    db = session_factory()()
    db.get(Plan, "football_premium").shopify_skus = ["FOOT-PREM-M"]
    db.commit(); db.close()

    order = {"id": 1001, "email": "buyer@example.com", "processed_at": datetime.now(timezone.utc).isoformat(),
             "line_items": [{"sku": "FOOT-PREM-M", "quantity": 1}]}
    assert _hook(client, "orders/paid", order, "evt-1", secret="wrong").status_code == 401
    assert _hook(client, "orders/paid", order, "evt-1").json()["status"] == "awaiting_account"
    assert _hook(client, "orders/paid", order, "evt-1").json()["status"] == "duplicate"

    client.cookies.clear()
    client.post("/auth/register", json={"email": "buyer@example.com", "password": "buyer-password-1"})
    assert "football.selections" in client.get("/me").json()["features"]
    assert client.get("/selections").json()["access"] == "full"

    refund = {"id": 5, "order_id": 1001, "refund_line_items": [{"line_item": {"sku": "FOOT-PREM-M"}}]}
    assert _hook(client, "refunds/create", refund, "evt-2").json()["status"] == "processed"
    assert "football.selections" not in client.get("/me").json()["features"]
    assert _hook(client, "carts/update", {"id": 1}, "evt-3").json()["status"] == "ignored"


def test_admin_cannot_demote_self(client):
    login(client, "admin@example.com", "admin-password-1")
    me = client.get("/me").json()
    assert client.put(f"/admin/users/{me['id']}", json={"role": "user"}).status_code == 400
