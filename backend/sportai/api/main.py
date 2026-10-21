"""HTTP API. Every permission is checked here, server-side."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from sportai.api.deps import COOKIE, current_user, login_limiter, require_admin, require_feature, require_user
from sportai.config import get_settings
from sportai.db.models import (
    AlertRow, AnalysisRun, AuditLog, CandidateRow, CarnetEntry, Competition, Entitlement, PaymentEvent, Plan, ProviderStatus,
    Selection, SelectionCorrection, User,
)
from sportai.db.session import get_db
from sportai.services import accounts, publication, runs, shopify

app = FastAPI(title="SportAI API", version="0.1.0")

PUBLIC_SELECTION_FIELDS = ("id", "kind", "match_label", "competition", "status", "published_at", "result")
FULL_SELECTION_FIELDS = PUBLIC_SELECTION_FIELDS + (
    "market", "selection", "line", "odds", "bookmaker", "odds_observed_at", "p_win", "p_push", "fair_odds", "ev",
    "ev_low", "model_name", "model_version", "rationale", "profit", "settled_at", "mode", "qc_review",
)


def _row(obj, fields) -> dict:
    out = {}
    for f in fields:
        v = getattr(obj, f)
        out[f] = v.isoformat() if isinstance(v, datetime) else v
    return out


@app.middleware("http")
async def json_only_mutations(request: Request, call_next):
    # Cookie auth + SameSite=Lax; requiring JSON on state-changing calls blocks
    # cross-site form posts (CSRF). Shopify webhooks are authenticated by HMAC.
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.url.path.startswith("/webhooks/"):
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "content-type must be application/json"}, status_code=415)
    return await call_next(request)


# ---------- health & public ----------

@app.get("/health")
def health(db: DB = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "data_mode": get_settings().data_mode}


@app.get("/public/plans")
def public_plans(db: DB = Depends(get_db)):
    return [{"code": p.code, "name": p.name, "features": p.features, "price": p.price_label}
            for p in db.scalars(select(Plan).where(Plan.active.is_(True)))]


@app.get("/public/performance")
def public_performance(kind: str | None = None, db: DB = Depends(get_db)):
    return publication.performance(db, kind)


@app.get("/public/history")
def public_history(limit: int = 100, db: DB = Depends(get_db)):
    """Published real selections, winners and losers alike. Details for subscribers via /selections/history."""
    rows = db.scalars(select(Selection).where(Selection.mode == "real", Selection.published_at.is_not(None))
                      .order_by(Selection.published_at.desc()).limit(min(limit, 500)))
    return [_row(s, PUBLIC_SELECTION_FIELDS + ("market", "selection", "line", "odds", "bookmaker", "profit")) for s in rows]


# ---------- auth ----------

class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax",
                    max_age=s.session_days * 86400, path="/")


@app.post("/auth/register", status_code=201)
def register(body: Credentials, request: Request, response: Response, db: DB = Depends(get_db)):
    login_limiter.check(f"reg:{request.client.host if request.client else '?'}")
    try:
        u = accounts.create_user(db, body.email, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    shopify.replay_awaiting_account(db, u.email)
    _set_cookie(response, accounts.create_session(db, u, get_settings().session_days))
    return {"id": u.id, "email": u.email}


@app.post("/auth/login")
def login(body: Credentials, request: Request, response: Response, db: DB = Depends(get_db)):
    login_limiter.check(f"login:{request.client.host if request.client else '?'}:{body.email.lower()}")
    u = accounts.authenticate(db, body.email, body.password)
    if u is None:
        raise HTTPException(401, "invalid credentials")
    _set_cookie(response, accounts.create_session(db, u, get_settings().session_days))
    return {"id": u.id, "email": u.email, "role": u.role}


@app.post("/auth/logout")
def logout(request: Request, response: Response, db: DB = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token:
        accounts.delete_session(db, token)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/me")
def me(user: User = Depends(require_user), db: DB = Depends(get_db)):
    ents = db.scalars(select(Entitlement).where(Entitlement.user_id == user.id))
    return {
        "id": user.id, "email": user.email, "role": user.role,
        "features": sorted(accounts.features_for(db, user)),
        "notification_prefs": user.notification_prefs,
        "entitlements": [_row(e, ("plan_code", "status", "source", "starts_at", "ends_at")) for e in ents],
    }


class Prefs(BaseModel):
    email_alerts: bool = False
    live_alerts: bool = False


@app.put("/me/preferences")
def update_prefs(body: Prefs, user: User = Depends(require_user), db: DB = Depends(get_db)):
    user.notification_prefs = body.model_dump()
    db.commit()
    return user.notification_prefs


# ---------- carnet: what subscribers put in their notebook ----------

CARNET_KINDS = ("ticket", "idee", "position", "joueur", "pari", "chasseur")


class CarnetItem(BaseModel):
    item_id: str = Field(min_length=3, max_length=160)
    kind: str = Field(pattern="^(" + "|".join(CARNET_KINDS) + ")$")
    label: str = Field(default="", max_length=255)


def _carnet_counts(db: DB, ids: list[str]) -> dict[str, int]:
    rows = db.execute(select(CarnetEntry.item_id, func.count()).where(CarnetEntry.item_id.in_(ids))
                      .group_by(CarnetEntry.item_id))
    return {i: 0 for i in ids} | {i: n for i, n in rows}


@app.get("/carnet")
def my_carnet(user: User = Depends(require_user), db: DB = Depends(get_db)):
    rows = list(db.scalars(select(CarnetEntry).where(CarnetEntry.user_id == user.id)
                           .order_by(CarnetEntry.created_at.desc())))
    counts = _carnet_counts(db, [r.item_id for r in rows])
    return [{**_row(r, ("item_id", "kind", "label", "created_at")), "personnes": counts[r.item_id]} for r in rows]


@app.post("/carnet", status_code=201)
def add_to_carnet(body: CarnetItem, user: User = Depends(require_user), db: DB = Depends(get_db)):
    if db.scalar(select(CarnetEntry).where(CarnetEntry.user_id == user.id, CarnetEntry.item_id == body.item_id)) is None:
        db.add(CarnetEntry(user_id=user.id, **body.model_dump()))
        db.commit()
    return {"item_id": body.item_id, "personnes": _carnet_counts(db, [body.item_id])[body.item_id]}


@app.delete("/carnet/{item_id}")
def remove_from_carnet(item_id: str, user: User = Depends(require_user), db: DB = Depends(get_db)):
    entry = db.scalar(select(CarnetEntry).where(CarnetEntry.user_id == user.id, CarnetEntry.item_id == item_id))
    if entry is not None:
        db.delete(entry)
        db.commit()
    return {"item_id": item_id, "personnes": _carnet_counts(db, [item_id])[item_id]}


@app.get("/carnet/compteurs")
def carnet_counts(ids: str, user: User = Depends(require_user), db: DB = Depends(get_db)):
    """How many subscribers hold each proposition: ``ids`` is a comma-separated list of item ids."""
    wanted = [i for i in dict.fromkeys(ids.split(",")) if i][:200]
    return _carnet_counts(db, wanted)


# ---------- subscriber content ----------

@app.get("/selections")
def current_selections(user: User = Depends(require_user), db: DB = Depends(get_db)):
    feats = accounts.features_for(db, user)
    rows = list(db.scalars(select(Selection).where(Selection.status == "published", Selection.result.is_(None),
                                                   Selection.mode == "real").order_by(Selection.published_at.desc())))
    latest = db.scalar(select(AnalysisRun).where(AnalysisRun.mode == "real", AnalysisRun.status == "completed")
                       .order_by(AnalysisRun.finished_at.desc()))
    full = accounts.has_feature(feats, "football.selections")
    return {
        "access": "full" if full else "teaser",
        "count": len(rows),
        "latest_run": None if latest is None else {"at": latest.finished_at.isoformat() if latest.finished_at else None,
                                                    "outcome": latest.outcome},
        "selections": [_row(s, FULL_SELECTION_FIELDS if full else PUBLIC_SELECTION_FIELDS) for s in rows],
    }


@app.get("/selections/history")
def selection_history(user: User = Depends(require_feature("football.selections")), db: DB = Depends(get_db)):
    rows = db.scalars(select(Selection).where(Selection.mode == "real", Selection.published_at.is_not(None))
                      .order_by(Selection.published_at.desc()).limit(500))
    out = []
    for s in rows:
        d = _row(s, FULL_SELECTION_FIELDS)
        d["corrections"] = [_row(c, ("field", "old_value", "new_value", "reason", "at"))
                            for c in db.scalars(select(SelectionCorrection).where(SelectionCorrection.selection_id == s.id))]
        out.append(d)
    return out


@app.get("/analyses/{run_id}")
def analysis_detail(run_id: str, user: User = Depends(require_feature("football.analysis")), db: DB = Depends(get_db)):
    run = db.get(AnalysisRun, run_id)
    if run is None or (run.mode != "real" and user.role != "admin"):
        raise HTTPException(404, "not found")
    return {"id": run.id, "competition": run.competition, "finished_at": run.finished_at, "model": run.model_name,
            "model_version": run.model_version, "fixtures": run.fixtures, "outcome": run.outcome}


@app.get("/alerts")
def alerts(user: User = Depends(require_feature("live.alerts")), db: DB = Depends(get_db)):
    rows = db.scalars(select(AlertRow).where(AlertRow.mode == "real").order_by(AlertRow.created_at.desc()).limit(100))
    return [_row(a, ("id", "kind", "commercial", "match_label", "minute", "score", "market", "odds", "bookmaker",
                     "probability", "fair_odds", "ev", "data_updated_at", "reasons", "invalidation", "created_at"))
            for a in rows]


# ---------- admin ----------

class RunRequest(BaseModel):
    competition: str = "FR1"
    mode: str | None = Field(default=None, pattern="^(simulation|real)$")


@app.post("/admin/runs/prematch")
def admin_run(body: RunRequest, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    competition = "SIM" if body.mode == "simulation" else body.competition
    run = runs.execute_prematch(db, get_settings(), competition, f"admin:{admin.id}", mode=body.mode)
    accounts.audit(db, admin.id, "run.prematch", run.id, {"competition": competition, "mode": run.mode})
    db.commit()
    return _run_summary(run)


def _run_summary(r: AnalysisRun) -> dict:
    return _row(r, ("id", "kind", "competition", "mode", "status", "status_detail", "outcome", "model_name",
                    "model_version", "started_at", "finished_at", "triggered_by", "data_sources"))


@app.get("/admin/runs")
def admin_runs(admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    return [_run_summary(r) for r in db.scalars(select(AnalysisRun).order_by(AnalysisRun.started_at.desc()).limit(100))]


@app.get("/admin/runs/{run_id}")
def admin_run_detail(run_id: str, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    r = db.get(AnalysisRun, run_id)
    if r is None:
        raise HTTPException(404, "not found")
    cands = db.scalars(select(CandidateRow).where(CandidateRow.run_id == run_id).order_by(CandidateRow.score.desc()))
    sels = db.scalars(select(Selection).where(Selection.run_id == run_id))
    return {**_run_summary(r), "config": r.config, "fixtures": r.fixtures,
            "candidates": [_row(c, ("id", "match_label", "market", "selection", "line", "odds", "bookmaker",
                                    "odds_observed_at", "p_win", "p_low", "fair_odds", "ev", "ev_low", "ev_stress",
                                    "market_fair_prob", "data_quality", "accepted", "reasons")) for c in cands],
            "selections": [_row(s, FULL_SELECTION_FIELDS) for s in sels]}


@app.get("/admin/selections")
def admin_selections(status: str | None = None, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    q = select(Selection).order_by(Selection.created_at.desc()).limit(200)
    if status:
        q = q.where(Selection.status == status)
    return [_row(s, FULL_SELECTION_FIELDS) for s in db.scalars(q)]


class Action(BaseModel):
    action: str = Field(pattern="^(publish|suspend|reject|settle|correct)$")
    reason: str = ""
    result: str | None = None
    field: str | None = None
    value: str | None = None


@app.post("/admin/selections/{sel_id}")
def admin_selection_action(sel_id: str, body: Action, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    sel = db.get(Selection, sel_id)
    if sel is None:
        raise HTTPException(404, "not found")
    try:
        if body.action == "publish":
            publication.publish(db, sel, admin.id)
        elif body.action in ("suspend", "reject"):
            publication.set_status(db, sel, {"suspend": "suspended", "reject": "rejected"}[body.action], admin.id, body.reason)
        elif body.action == "settle":
            publication.settle(db, sel, body.result or "", admin.id)
        else:
            publication.correct(db, sel, body.field or "", body.value or "", admin.id, body.reason)
    except publication.PublicationError as e:
        raise HTTPException(409, str(e))
    return _row(sel, FULL_SELECTION_FIELDS)


class NewsItem(BaseModel):
    title: str = Field(max_length=500)
    source: str = Field(max_length=300)
    published_at: str | None = None
    text: str = Field(default="", max_length=8000)


class ReviewRequest(BaseModel):
    news: list[NewsItem] = Field(default_factory=list, max_length=30)


@app.post("/admin/selections/{sel_id}/review")
def admin_review(sel_id: str, body: ReviewRequest, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    from sportai.agents import review as qc

    s = get_settings()
    if not s.anthropic_api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured; the review agent is disabled")
    sel = db.get(Selection, sel_id)
    if sel is None:
        raise HTTPException(404, "not found")
    import anthropic

    try:
        r = qc.review_selection(anthropic.Anthropic(api_key=s.anthropic_api_key), _row(sel, FULL_SELECTION_FIELDS),
                                [n.model_dump() for n in body.news], s.agent_budget_usd_per_run)
    except qc.BudgetExceeded as e:
        raise HTTPException(400, str(e))
    sel.qc_review = {"verdict": r.verdict, "facts": r.facts, "concerns": r.concerns, "summary": r.summary,
                     "model": r.model, "cost_usd": round(r.cost_usd, 4),
                     "at": datetime.now(timezone.utc).isoformat()}
    accounts.audit(db, admin.id, "selection.qc_review", sel.id, {"verdict": r.verdict, "cost_usd": r.cost_usd})
    db.commit()
    return sel.qc_review


@app.get("/admin/settings/selection")
def get_selection_settings(admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    cfg = runs.selection_config(db)
    return {**cfg.to_dict(), "enabled_markets": list(cfg.enabled_markets)}


@app.put("/admin/settings/selection")
def put_selection_settings(body: dict, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    try:
        cfg = runs.save_selection_config(db, body, admin.id)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    accounts.audit(db, admin.id, "settings.selection", None, body)
    db.commit()
    return {**cfg.to_dict(), "enabled_markets": list(cfg.enabled_markets)}


@app.get("/admin/competitions")
def get_competitions(admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    return [_row(c, ("code", "sport", "name", "monitored")) for c in db.scalars(select(Competition))]


class Monitor(BaseModel):
    monitored: bool


@app.put("/admin/competitions/{code}")
def set_competition(code: str, body: Monitor, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    c = db.get(Competition, code)
    if c is None:
        raise HTTPException(404, "not found")
    c.monitored = body.monitored
    accounts.audit(db, admin.id, "competition.monitor", code, {"monitored": body.monitored})
    db.commit()
    return _row(c, ("code", "sport", "name", "monitored"))


@app.get("/admin/users")
def admin_users(admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    out = []
    for u in db.scalars(select(User).order_by(User.created_at.desc()).limit(500)):
        out.append({**_row(u, ("id", "email", "role", "is_active", "created_at")),
                    "entitlements": [_row(e, ("id", "plan_code", "status", "source", "ends_at"))
                                     for e in db.scalars(select(Entitlement).where(Entitlement.user_id == u.id))]})
    return out


class Grant(BaseModel):
    plan_code: str
    ends_at: datetime | None = None


@app.post("/admin/users/{user_id}/entitlements")
def admin_grant(user_id: str, body: Grant, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    if db.get(User, user_id) is None or db.get(Plan, body.plan_code) is None:
        raise HTTPException(404, "user or plan not found")
    e = Entitlement(user_id=user_id, plan_code=body.plan_code, source="admin", ends_at=body.ends_at)
    db.add(e)
    accounts.audit(db, admin.id, "entitlement.admin_grant", user_id, {"plan": body.plan_code})
    db.commit()
    return _row(e, ("id", "plan_code", "status", "source", "ends_at"))


class RoleBody(BaseModel):
    role: str = Field(pattern="^(user|admin)$")
    is_active: bool = True


@app.put("/admin/users/{user_id}")
def admin_set_role(user_id: str, body: RoleBody, admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "not found")
    if u.id == admin.id and (body.role != "admin" or not body.is_active):
        raise HTTPException(400, "you cannot demote or deactivate yourself")
    u.role, u.is_active = body.role, body.is_active
    accounts.audit(db, admin.id, "user.update", user_id, body.model_dump())
    db.commit()
    return _row(u, ("id", "email", "role", "is_active"))


@app.get("/admin/audit")
def admin_audit(admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    return [_row(a, ("id", "actor_id", "action", "target", "detail", "at"))
            for a in db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(300))]


@app.get("/admin/providers")
def admin_providers(admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    s = get_settings()
    return {
        "data_mode": s.data_mode,
        "configured": {"the-odds-api": bool(s.the_odds_api_key), "api-football": bool(s.api_football_key),
                       "shopify-webhooks": bool(s.shopify_webhook_secret), "anthropic": bool(s.anthropic_api_key)},
        "status": [_row(p, ("provider", "last_ok_at", "last_error_at", "last_error", "quota"))
                   for p in db.scalars(select(ProviderStatus))],
    }


@app.get("/admin/payments")
def admin_payments(admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    return [_row(p, ("id", "event_id", "topic", "status", "error", "received_at"))
            for p in db.scalars(select(PaymentEvent).order_by(PaymentEvent.received_at.desc()).limit(200))]


@app.get("/admin/performance")
def admin_performance(admin: User = Depends(require_admin), db: DB = Depends(get_db)):
    counts = dict(db.execute(select(Selection.status, func.count()).group_by(Selection.status)).all())
    return {"prematch": publication.performance(db, "prematch"), "live": publication.performance(db, "live"),
            "selection_counts": counts}


# ---------- webhooks ----------

@app.post("/webhooks/shopify")
async def shopify_webhook(request: Request, db: DB = Depends(get_db)):
    raw = await request.body()
    if not shopify.verify_hmac(raw, request.headers.get("x-shopify-hmac-sha256"), get_settings().shopify_webhook_secret):
        raise HTTPException(401, "invalid signature")
    event_id = request.headers.get("x-shopify-event-id") or request.headers.get("x-shopify-webhook-id")
    topic = request.headers.get("x-shopify-topic", "")
    if not event_id:
        raise HTTPException(400, "missing event id")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    ev = shopify.record_event(db, event_id, topic, payload)
    if ev is None:
        return {"status": "duplicate"}
    shopify.process_event(db, ev)
    return {"status": ev.status}


@app.get("/time")
def server_time():
    return {"now": datetime.now(timezone.utc).isoformat()}
