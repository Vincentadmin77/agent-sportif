from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DB

from sportai.db.models import User
from sportai.db.session import get_db
from sportai.services.accounts import features_for, has_feature, user_for_token

COOKIE = "sportai_session"


def current_user(request: Request, db: DB = Depends(get_db)) -> User | None:
    return user_for_token(db, request.cookies.get(COOKIE))


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(401, "authentication required")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "admin only")
    return user


def require_feature(feature: str):
    def dep(user: User = Depends(require_user), db: DB = Depends(get_db)) -> User:
        if not has_feature(features_for(db, user), feature):
            raise HTTPException(403, f"your plan does not include {feature}")
        return user

    return dep


class RateLimiter:
    """In-process sliding window. Enough for one API instance; use a shared
    store (Postgres or Redis) when running several instances."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(429, "too many attempts, try again later")
        q.append(now)


login_limiter = RateLimiter(10, 300)
