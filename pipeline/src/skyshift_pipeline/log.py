"""Timestamped logging to stdout (the machines stream stdout to Cloud Logging)."""

from __future__ import annotations

import logging
import sys

_FMT = "%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s"


def setup(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(getattr(h, "_skyshift", False) for h in root.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter(_FMT, datefmt="%Y-%m-%dT%H:%M:%S"))
        h._skyshift = True  # type: ignore[attr-defined]
        root.addHandler(h)
    root.setLevel(level)
    # never touch the "astroquery"/"astropy" loggers here: they must be created by astropy
    for noisy in ("urllib3", "aiohttp", "fsspec", "gcsfs", "google", "pyvo"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"skyshift.{name}")
