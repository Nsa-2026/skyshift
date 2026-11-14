"""HTTP access to the SkyShift site: static files first, then /api/ (contracts/api.md)."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import urljoin

import httpx

DEFAULT_BASE = "https://skyshift.web.app"
API_TIMEOUT = 75.0        # the API answers by 48 s (503 "upstream" after that); leave room for a cold start
STATIC_TIMEOUT = 30.0
CANDIDATES_TTL = 600.0    # seconds to keep candidates.json in memory


class SkyShiftError(Exception):
    """An error the assistant should see as the tool's answer."""


class Client:
    def __init__(self, base: str | None = None, http: httpx.AsyncClient | None = None):
        self.base = (base or os.environ.get("SKYSHIFT_BASE") or DEFAULT_BASE).rstrip("/")
        self.http = http or httpx.AsyncClient(follow_redirects=True,
                                              headers={"User-Agent": "skyshift-mcp/0.1"})
        self._cand: tuple[float, dict[str, Any]] | None = None
        self._cand_lock = asyncio.Lock()

    def url(self, path: str) -> str:
        return urljoin(self.base + "/", path.lstrip("/"))

    async def get_json(self, path_or_url: str, params: dict[str, Any] | None = None,
                       timeout: float = STATIC_TIMEOUT) -> dict[str, Any]:
        url = path_or_url if "://" in path_or_url else self.url(path_or_url)
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in (0, 1):
            try:
                r = await self.http.get(url, params=params, timeout=timeout)
            except httpx.TimeoutException as e:
                raise SkyShiftError(f"{url} did not answer in {timeout:.0f} s") from e
            except httpx.HTTPError as e:
                raise SkyShiftError(f"could not reach {url}: {e}") from e
            if r.status_code == 503 and attempt == 0 and "retry-after" in r.headers:
                await asyncio.sleep(min(10.0, float(r.headers.get("retry-after") or 5)))
                continue
            break
        if r.status_code >= 400:
            msg = r.text[:300]
            try:
                err = r.json().get("error") or {}
                msg = f"{err.get('code', 'error')}: {err.get('message', msg)}"
            except ValueError:
                pass
            raise SkyShiftError(f"HTTP {r.status_code} from {url}: {msg}")
        try:
            return r.json()
        except ValueError as e:
            raise SkyShiftError(f"{url} did not return JSON") from e

    async def get_bytes(self, url: str, timeout: float = STATIC_TIMEOUT) -> bytes:
        try:
            r = await self.http.get(url, timeout=timeout)
        except httpx.HTTPError as e:
            raise SkyShiftError(f"could not fetch frame {url}: {e}") from e
        if r.status_code >= 400:
            raise SkyShiftError(f"HTTP {r.status_code} for frame {url}")
        return r.content

    async def api(self, path: str, **params: Any) -> dict[str, Any]:
        return await self.get_json(path, params, timeout=API_TIMEOUT)

    async def candidates(self) -> dict[str, Any]:
        async with self._cand_lock:
            if self._cand and time.monotonic() - self._cand[0] < CANDIDATES_TTL:
                return self._cand[1]
            data = await self.get_json("/data/candidates.json")
            self._cand = (time.monotonic(), data)
            return data

    async def resolve(self, query: str) -> dict[str, Any]:
        return await self.api("/api/resolve", q=query)
