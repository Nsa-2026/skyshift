"""Byte cache shared by the engine and the app: gs://nasa-glazing-data/cache/ or a local folder.

SKYSHIFT_CACHE = "gs://nasa-glazing-data/cache" (Cloud Run, the machines) or a local path (tests,
laptops). Default: ~/.cache/skyshift. Keys are relative paths such as "cutouts/<obs_id>D2/<ra>_<dec>_<size>.fits".
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class Cache:
    def __init__(self, root: str | None = None) -> None:
        self.root = root or os.environ.get("SKYSHIFT_CACHE") or str(Path.home() / ".cache" / "skyshift")
        self._bucket = None
        self._prefix = ""
        if self.root.startswith("gs://"):
            from google.cloud import storage  # imported only when used

            name, _, prefix = self.root[5:].partition("/")
            self._bucket = storage.Client().bucket(name)
            self._prefix = prefix.strip("/")

    def _blob(self, key: str):
        return self._bucket.blob(f"{self._prefix}/{key}" if self._prefix else key)

    def get(self, key: str) -> bytes | None:
        if self._bucket is not None:
            b = self._blob(key)
            return b.download_as_bytes() if b.exists() else None
        p = Path(self.root) / key
        return p.read_bytes() if p.exists() else None

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        if self._bucket is not None:
            self._blob(key).upload_from_string(data, content_type=content_type)
            return
        p = Path(self.root) / key
        p.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=p.parent, prefix=p.name + ".", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            try:
                f.write(data)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        try:
            tmp.replace(p)  # unique per writer; readers see one complete value
        finally:
            tmp.unlink(missing_ok=True)
