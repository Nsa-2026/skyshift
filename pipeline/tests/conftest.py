"""Shared helpers for pipeline tests. Offline by default; network tests need SKYSHIFT_NETWORK=1."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline.sky import tile_of

REPO = Path(__file__).resolve().parents[2]
CUTOUT = REPO / "fixtures" / "fits" / "cutout_2025W18_1B_0074_2D1.fits"
SKYBOT_SAMPLE = REPO / "fixtures" / "pipeline" / "skybot_conesearch_sample.json"

T0 = 61000.0
SESSION_STEP = 0.3        # days between sessions
EXPOSURE_STEP = 2.1 / 1440  # 2.1 minutes


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SKYSHIFT_NETWORK") == "1":
        return
    skip = pytest.mark.skip(reason="network test: set SKYSHIFT_NETWORK=1")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


def t(session: int, exposure: int = 0) -> float:
    return T0 + session * SESSION_STEP + exposure * EXPOSURE_STEP


class DotMaker:
    """Builds a synthetic in-memory dot table in link's load format."""

    def __init__(self):
        self.rows = []
        self.exposures: dict[float, int] = {}

    def add(self, ra, dec, mjd, det=1, snr=20.0, flux=100.0, flags=0, elong=1.1):
        e = self.exposures.setdefault(round(mjd, 9), len(self.exposures))
        self.rows.append(dict(detector=det, mjd=mjd, ra=ra, dec=dec, flux_ujy=flux, snr=snr, flags=flags,
                              elong=elong, session=0, visit=0, photo=e * 10 + det, exposure=e))
        return self

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def tiles_for(points: list[tuple[float, float, int]]) -> pd.DataFrame:
    """Tile table with a chosen session coverage per (ra, dec, n_sessions)."""
    return pd.DataFrame({"tile": [int(tile_of(r, d)) for r, d, _ in points],
                         "n_sessions": [n for _, _, n in points]})


@pytest.fixture
def rng():
    return np.random.default_rng(1)
