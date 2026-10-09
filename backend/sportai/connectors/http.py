"""Small HTTP layer shared by real connectors: timeouts, TTL cache, errors.

Caching avoids burning provider quotas on repeated identical requests. Any
transport or HTTP error becomes ProviderUnavailable so callers can switch off
dependent analyses instead of producing numbers from nothing.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from sportai.connectors.base import ProviderUnavailable


class CachedClient:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=base_url, headers=headers or {}, timeout=timeout, transport=transport)
        self._cache: dict[tuple, tuple[float, Any, dict]] = {}
        self.last_headers: dict[str, str] = {}

    def get_json(self, path: str, params: dict | None = None, ttl: float = 60.0) -> Any:
        key = (path, tuple(sorted((params or {}).items())))
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            self.last_headers = hit[2]
            return hit[1]
        try:
            r = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"{self._client.base_url}{path}: {e}") from e
        if r.status_code != 200:
            raise ProviderUnavailable(f"{self._client.base_url}{path}: HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        self.last_headers = dict(r.headers)
        self._cache[key] = (time.monotonic() + ttl, data, self.last_headers)
        return data
