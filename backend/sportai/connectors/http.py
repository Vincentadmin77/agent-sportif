"""Small HTTP layer shared by real connectors: timeouts, TTL cache, errors.

Caching avoids burning provider quotas on repeated identical requests. Any
transport or HTTP error becomes ProviderUnavailable so callers can switch off
dependent analyses instead of producing numbers from nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from sportai.connectors.base import ProviderUnavailable


class CachedClient:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=base_url, headers=headers or {}, timeout=timeout, transport=transport)
        self._cache: dict[tuple, tuple[float, Any, dict]] = {}
        self.last_headers: dict[str, str] = {}
        # Optional disk cache (owner 2026-11-15): the live loop runs one process per cycle, so an in-memory
        # cache alone re-downloaded team histories, squads and injuries every few minutes and emptied the
        # daily quota. Only answers kept at least a minute are stored, with a wall-clock expiry.
        d = os.getenv("SPORTAI_HTTP_CACHE")
        self._disk = Path(d) if d else None
        self._base = str(base_url)

    def _disk_path(self, key: tuple) -> Path | None:
        if self._disk is None:
            return None
        h = hashlib.sha256(repr((self._base, key)).encode()).hexdigest()
        return self._disk / h[:2] / f"{h}.json"

    def get_json(self, path: str, params: dict | None = None, ttl: float = 60.0) -> Any:
        key = (path, tuple(sorted((params or {}).items())))
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            self.last_headers = hit[2]
            return hit[1]
        path_on_disk = self._disk_path(key) if ttl >= 60 else None
        if path_on_disk is not None:
            try:
                saved = json.loads(path_on_disk.read_text())
                if saved["expires"] > time.time():
                    self.last_headers = saved["headers"]
                    self._cache[key] = (time.monotonic() + saved["expires"] - time.time(), saved["data"],
                                        self.last_headers)
                    return saved["data"]
            except (OSError, ValueError, KeyError):
                pass
        try:
            r = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"{self._client.base_url}{path}: {e}") from e
        if r.status_code != 200:
            raise ProviderUnavailable(f"{self._client.base_url}{path}: HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        self.last_headers = dict(r.headers)
        self._cache[key] = (time.monotonic() + ttl, data, self.last_headers)
        if path_on_disk is not None:
            try:
                path_on_disk.parent.mkdir(parents=True, exist_ok=True)
                tmp = path_on_disk.with_suffix(".tmp")
                tmp.write_text(json.dumps({"expires": time.time() + ttl, "data": data, "headers": self.last_headers}))
                tmp.replace(path_on_disk)
            except OSError:
                pass
        return data
