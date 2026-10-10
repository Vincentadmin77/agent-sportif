"""Admin CLI.

  python -m sportai.manage init            # seed plans and competitions (after `alembic upgrade head`)
  python -m sportai.manage create-admin EMAIL   # password read from the ADMIN_PASSWORD env var or prompted
  python -m sportai.manage run-prematch FR1 [--mode simulation|real]
  python -m sportai.manage set-plan-skus football_premium SKU1,SKU2
"""

from __future__ import annotations

import argparse
import getpass
import os

from sportai.config import get_settings
from sportai.db.models import Competition, Plan
from sportai.db.session import session_factory
from sportai.services import accounts, runs

COMPETITIONS = [("FR1", "Ligue 1"), ("EN1", "Premier League"), ("ES1", "LaLiga"), ("DE1", "Bundesliga"),
                ("IT1", "Serie A"), ("UCL", "Ligue des champions"), ("BR1", "Brasileirão Série A"), ("SIM", "Ligue simulée (démo)")]


def init(db) -> None:
    accounts.seed_plans(db)
    for code, name in COMPETITIONS:
        if db.get(Competition, code) is None:
            db.add(Competition(code=code, name=name, monitored=code == "FR1"))
    db.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("create-admin")
    a.add_argument("email")
    r = sub.add_parser("run-prematch")
    r.add_argument("competition")
    r.add_argument("--mode", choices=["simulation", "real"])
    s = sub.add_parser("set-plan-skus")
    s.add_argument("plan")
    s.add_argument("skus")
    args = ap.parse_args()
    db = session_factory()()
    if args.cmd == "init":
        init(db)
        print("seeded plans and competitions")
    elif args.cmd == "create-admin":
        pw = os.getenv("ADMIN_PASSWORD") or getpass.getpass("password: ")
        u = accounts.create_user(db, args.email, pw, role="admin")
        print(f"admin created: {u.email}")
    elif args.cmd == "run-prematch":
        run = runs.execute_prematch(db, get_settings(), args.competition, "cli", mode=args.mode)
        print(run.id, run.status, run.outcome or "", run.status_detail or "")
    elif args.cmd == "set-plan-skus":
        p = db.get(Plan, args.plan)
        p.shopify_skus = [x.strip() for x in args.skus.split(",") if x.strip()]
        db.commit()
        print(p.code, p.shopify_skus)


if __name__ == "__main__":
    main()
