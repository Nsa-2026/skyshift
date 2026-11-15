"""Optional API keys (contracts/api.md v3.14, "API keys").

Every endpoint works without a key. A key names the caller: its id shows in the logs, a per-key limit
applies, and it can be revoked alone. The server keeps only the key's SHA-256, one small JSON file per key:
<store>/<sha256>.json = { id, label, created }. The store is SKYSHIFT_KEYS (gs://… or a local folder);
default: the `apikeys/` folder of SKYSHIFT_CACHE's bucket (gs://nasa-glazing-data/apikeys), or
~/.cache/skyshift-keys locally.

Limits are counted per server instance (0–8 instances): good enough to stop a runaway script, not billing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cache import Cache

PREFIX = "sks_"
KEY_RE = re.compile(r"^sks_[A-Za-z0-9_-]{32}$")
HEADER = "X-API-Key"
LABEL_MAX = 60
CREATE_PER_HOUR = 10       # new keys per address per instance
CHECK_TTL_S = 300.0        # a checked key is remembered this long (revocation reaches other instances by then)
MISS_TTL_S = 60.0          # an unknown key is remembered this long
WINDOW_S = 3600.0


def _int_env(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return v if v > 0 else None


def key_limit() -> int | None:
    return _int_env("SKYSHIFT_KEY_LIMIT", 1000)


def anon_limit() -> int | None:
    return _int_env("SKYSHIFT_ANON_LIMIT", None)


def store_root() -> str:
    explicit = os.environ.get("SKYSHIFT_KEYS")
    if explicit:
        return explicit
    cache = os.environ.get("SKYSHIFT_CACHE", "")
    if cache.startswith("gs://"):
        bucket = cache[5:].split("/", 1)[0]
        return f"gs://{bucket}/apikeys"
    return str(Path.home() / ".cache" / "skyshift-keys")


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def clean_label(label: Any) -> str | None:
    """None for missing or blank; ValueError for anything else that is not 1-60 printable characters."""
    if label is None:
        return None
    if not isinstance(label, str):
        raise ValueError("label must be a string")
    label = label.strip()
    if not label:
        return None
    if len(label) > LABEL_MAX:
        raise ValueError(f"label must be at most {LABEL_MAX} characters")
    if not label.isprintable():
        raise ValueError("label must be printable characters")
    return label


def from_headers(headers: Any) -> str | None:
    """The key a request sends: X-API-Key, else Authorization: Bearer. '' (sent but empty) counts as sent."""
    key = headers.get(HEADER)
    if key is None:
        auth = headers.get("authorization") or ""
        scheme, _, rest = auth.partition(" ")
        if scheme.lower() != "bearer":
            return None
        key = rest
    return key.strip()


class RateLimiter:
    """Sliding one-hour window per name, in memory."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, name: str, limit: int | None, now: float | None = None) -> int:
        """Count one request; 0 if allowed, else the seconds until the oldest one leaves the window."""
        if limit is None:
            return 0
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(name, deque())
            while q and q[0] <= now - WINDOW_S:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(q[0] + WINDOW_S - now) + 1)
            q.append(now)
            if len(self._hits) > 50_000:  # forget idle names
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - WINDOW_S]:
                    del self._hits[k]
            return 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class KeyStore:
    def __init__(self, root: str | None = None) -> None:
        self.cache = Cache(root or store_root())
        self._seen: dict[str, tuple[float, dict | None]] = {}
        self._lock = threading.Lock()

    def create(self, label: str | None) -> dict:
        key = PREFIX + secrets.token_urlsafe(24)  # 24 bytes -> 32 URL-safe characters
        h = digest(key)
        record = {"id": h[:12], "label": label,
                  "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.cache.put(f"{h}.json", json.dumps(record).encode(), "application/json")
        with self._lock:
            self._seen[h] = (time.monotonic() + CHECK_TTL_S, record)
        return {"key": key, **record, "header": HEADER}

    def lookup(self, key: str) -> dict | None:
        """The key's record, or None when it is malformed, unknown or revoked."""
        if not KEY_RE.match(key):
            return None
        h = digest(key)
        now = time.monotonic()
        with self._lock:
            hit = self._seen.get(h)
        if hit and hit[0] > now:
            return hit[1]
        raw = self.cache.get(f"{h}.json")
        record = json.loads(raw) if raw is not None else None
        with self._lock:
            self._seen[h] = (now + (CHECK_TTL_S if record else MISS_TTL_S), record)
            if len(self._seen) > 50_000:
                self._seen = {k: v for k, v in self._seen.items() if v[0] > now}
        return record

    def revoke(self, key: str) -> None:
        h = digest(key)
        self.cache.delete(f"{h}.json")
        with self._lock:
            self._seen[h] = (time.monotonic() + MISS_TTL_S, None)
