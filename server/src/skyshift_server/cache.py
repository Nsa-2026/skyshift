"""Byte cache shared by the engine and the app: gs://nasa-glazing-data/cache/ or a local folder.

SKYSHIFT_CACHE = "gs://nasa-glazing-data/cache" (Cloud Run, the machines) or a local path (tests,
laptops). Default: ~/.cache/skyshift. Keys are relative paths such as "cutouts/<obs_id>D2/<ra>_<dec>_<size>.fits".
"""

from __future__ import annotations

import os
from pathlib import Path


class Cache:
    def __init__(self, root: str | None = None) -> None:
        self.root = root or os.environ.get("SKYSHIFT_CACHE") or str(Path.home() / ".cache" / "skyshift")
        self._bucket = None
        self._prefix = ""
        if self.root.startswith("gs://"):
            from google.cloud import storage  # imported only when used

            name, _, prefix = self.root[5:].partition("/")
            client = storage.Client()
            try:  # many threads share the client: keep more than requests' default 10 connections
                import requests

                client._http.mount("https://", requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64))
            except Exception:  # noqa: BLE001 - a private attribute; the default pool works too
                pass
            self._bucket = client.bucket(name)
            self._prefix = prefix.strip("/")

    def _blob(self, key: str):
        return self._bucket.blob(f"{self._prefix}/{key}" if self._prefix else key)

    def get(self, key: str) -> bytes | None:
        if self._bucket is not None:
            from google.api_core.exceptions import NotFound

            try:  # one round trip (exists() + download was two)
                return self._blob(key).download_as_bytes()
            except NotFound:
                return None
        p = Path(self.root) / key
        return p.read_bytes() if p.exists() else None

    def keys(self, prefix: str) -> list[str]:
        """Every key that starts with `prefix` (e.g. "search/"), relative to the cache root."""
        if self._bucket is not None:
            full = f"{self._prefix}/{prefix}" if self._prefix else prefix
            cut = len(self._prefix) + 1 if self._prefix else 0
            return [b.name[cut:] for b in self._bucket.list_blobs(prefix=full)]
        root = Path(self.root)
        start = root / prefix.rstrip("/")
        if not start.is_dir():
            return []
        return sorted(p.relative_to(root).as_posix() for p in start.rglob("*")
                      if p.is_file() and not p.name.endswith(".tmp"))

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        if self._bucket is not None:
            self._blob(key).upload_from_string(data, content_type=content_type)
            return
        p = Path(self.root) / key
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)  # atomic: a half-written file never counts as cached
