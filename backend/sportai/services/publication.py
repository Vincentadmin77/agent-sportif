"""Publication workflow (semi-automatic), settlement, corrections, performance.

Rules:
- only `real` selections can be published; simulation never reaches users;
- a selection is published at the odds observed by the run; that price is
  never changed afterwards, and a stale price blocks publication;
- losing selections stay in the history; every correction is recorded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from sportai.db.models import Selection, SelectionCorrection
from sportai.engine.evaluation import betting_record
from sportai.services.accounts import audit

MAX_ODDS_AGE_AT_PUBLICATION = timedelta(minutes=30)
CORRECTABLE_FIELDS = {"rationale"}


class PublicationError(ValueError):
    pass


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def publish(db: DB, sel: Selection, admin_id: str, now: datetime | None = None) -> Selection:
    now = now or datetime.now(timezone.utc)
    if sel.mode != "real":
        raise PublicationError("simulated data can never be published")
    if sel.status not in ("pending_review", "suspended"):
        raise PublicationError(f"cannot publish a selection in status {sel.status}")
    if now - _aware(sel.odds_observed_at) > MAX_ODDS_AGE_AT_PUBLICATION:
        raise PublicationError("the observed odds are too old; run the analysis again before publishing")
    first = sel.published_at is None
    sel.status = "published"
    if first:
        sel.published_at, sel.published_by = now, admin_id
    audit(db, admin_id, "selection.publish", sel.id, {"odds": sel.odds, "bookmaker": sel.bookmaker})
    db.commit()
    return sel


def set_status(db: DB, sel: Selection, status: str, admin_id: str, reason: str) -> Selection:
    allowed = {"suspended": {"published", "pending_review"}, "rejected": {"pending_review", "suspended"}}
    if status not in allowed or sel.status not in allowed[status]:
        raise PublicationError(f"cannot move from {sel.status} to {status}")
    if not reason.strip():
        raise PublicationError("a reason is required")
    db.add(SelectionCorrection(selection_id=sel.id, field="status", old_value=sel.status, new_value=status,
                               reason=reason, by_user=admin_id))
    audit(db, admin_id, f"selection.{status}", sel.id, {"reason": reason})
    sel.status = status
    db.commit()
    return sel


def correct(db: DB, sel: Selection, field: str, value: str, admin_id: str, reason: str) -> Selection:
    if field not in CORRECTABLE_FIELDS:
        raise PublicationError(f"field {field} cannot be corrected (published odds and probabilities are immutable)")
    if not reason.strip():
        raise PublicationError("a reason is required")
    db.add(SelectionCorrection(selection_id=sel.id, field=field, old_value=str(getattr(sel, field)),
                               new_value=value, reason=reason, by_user=admin_id))
    setattr(sel, field, value)
    audit(db, admin_id, "selection.correct", sel.id, {"field": field, "reason": reason})
    db.commit()
    return sel


def settle(db: DB, sel: Selection, result: str, admin_id: str, now: datetime | None = None) -> Selection:
    if result not in ("win", "lose", "push", "void"):
        raise PublicationError("result must be win, lose, push or void")
    if sel.status != "published":
        raise PublicationError("only published selections are settled")
    if sel.result is not None:
        db.add(SelectionCorrection(selection_id=sel.id, field="result", old_value=sel.result, new_value=result,
                                   reason="re-settlement", by_user=admin_id))
    sel.result = result
    sel.profit = {"win": sel.odds - 1.0, "lose": -1.0, "push": 0.0, "void": 0.0}[result]
    sel.settled_at = now or datetime.now(timezone.utc)
    audit(db, admin_id, "selection.settle", sel.id, {"result": result})
    db.commit()
    return sel


def performance(db: DB, kind: str | None = None) -> dict:
    """Public track record: real, published, settled selections only."""
    q = select(Selection).where(Selection.mode == "real", Selection.published_at.is_not(None),
                                Selection.result.in_(("win", "lose", "push")))
    if kind:
        q = q.where(Selection.kind == kind)
    rows = list(db.scalars(q))
    if not rows:
        return {"n": 0, "note": "Aucune sélection réelle réglée pour le moment.", "by_market": {}}
    profits = np.array([r.profit for r in rows])
    won = np.array([r.result == "win" for r in rows], dtype=float)
    rec = betting_record(profits, won)
    by_market: dict[str, dict] = {}
    for r in rows:
        m = by_market.setdefault(r.market, {"n": 0, "profit": 0.0, "wins": 0})
        m["n"] += 1
        m["profit"] += r.profit
        m["wins"] += r.result == "win"
    dates = sorted(_aware(r.published_at) for r in rows)
    return {
        "n": rec.n_bets, "hit_rate": rec.hit_rate, "roi": rec.roi, "roi_ci95": [rec.roi_ci_low, rec.roi_ci_high],
        "avg_odds": float(np.mean([r.odds for r in rows])), "period": [dates[0].isoformat(), dates[-1].isoformat()],
        "note": rec.note, "by_market": by_market,
        "method": "Mise d'une unité par sélection à la cote observée au moment de la publication. "
                  "Intervalle de confiance à 95 % par bootstrap. Les performances passées ne préjugent pas des résultats futurs.",
    }
