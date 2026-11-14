"""The bucket (or a local folder) as an fsspec root, with atomic writes.

Layout (docs/cloud-architecture.md):
    photolists/zones/<zone>.parquet
    dots/<obs_id>D<det>.parquet
    cache/skybot/<zone>/run_<first obs_id>.json
    results/<zone>/...
A file's existence means its step is done. Writes are atomic: a local file is written under a
temporary name and renamed; a GCS object only appears once its upload has completed.
"""

from __future__ import annotations

import io
import json
import os
import uuid
from typing import Any

import fsspec
import pandas as pd


class Store:
    def __init__(self, root: str):
        self.root = root.rstrip("/")
        self.fs, base = fsspec.core.url_to_fs(self.root)
        self.base = base.rstrip("/")
        self.local = "file" in (self.fs.protocol if isinstance(self.fs.protocol, tuple) else (self.fs.protocol,))

    def path(self, rel: str) -> str:
        return f"{self.base}/{rel.lstrip('/')}"

    def exists(self, rel: str) -> bool:
        return self.fs.exists(self.path(rel))

    def write_bytes(self, rel: str, data: bytes) -> None:
        p = self.path(rel)
        if self.local:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = f"{p}.tmp-{uuid.uuid4().hex[:8]}"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        else:
            self.fs.pipe_file(p, data)  # one upload; the object exists only when it completes

    def read_bytes(self, rel: str) -> bytes:
        return self.fs.cat_file(self.path(rel))

    def write_parquet(self, rel: str, df: pd.DataFrame) -> int:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False, compression="zstd")
        data = buf.getvalue()
        self.write_bytes(rel, data)
        return len(data)

    def write_table(self, rel: str, table) -> int:
        """A pyarrow Table (keeps exact Arrow types, e.g. fixed-size lists)."""
        import pyarrow.parquet as pq

        buf = io.BytesIO()
        pq.write_table(table, buf, compression="zstd")
        data = buf.getvalue()
        self.write_bytes(rel, data)
        return len(data)

    def read_parquet(self, rel: str, columns: list[str] | None = None) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self.read_bytes(rel)), columns=columns)

    def write_json(self, rel: str, obj: Any) -> None:
        self.write_bytes(rel, json.dumps(obj, indent=1, default=_json_default).encode())

    def read_json(self, rel: str) -> Any:
        return json.loads(self.read_bytes(rel))

    def listdir(self, rel: str) -> list[str]:
        """Base names under a prefix ([] if it doesn't exist)."""
        p = self.path(rel)
        try:
            return [os.path.basename(x.rstrip("/")) for x in self.fs.ls(p, detail=False)]
        except FileNotFoundError:
            return []


def _json_default(o: Any) -> Any:
    import numpy as np

    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o)}")


def dots_rel(obs_id: str, det: int) -> str:
    return f"dots/{obs_id}D{int(det)}.parquet"



def photolist_rel(zone: str, part: str = "") -> str:
    return f"photolists/zones/{zone}{'_' + part if part else ''}.parquet"


def results_rel(zone: str, name: str) -> str:
    return f"results/{zone}/{name}"
