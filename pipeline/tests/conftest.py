"""Shared helpers for pipeline tests. Offline by default; network tests need SKYSHIFT_NETWORK=1."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest


REPO = Path(__file__).resolve().parents[2]
CUTOUT = REPO / "fixtures" / "fits" / "cutout_2025W18_1B_0074_2D1.fits"

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


@pytest.fixture(autouse=True)
def _offline_deep_gaia(monkeypatch):
    """highpm's deep Gaia check (ESA TAP) never goes online in tests: it sees a failing service unless a test gives
    its own `tap` (or SKYSHIFT_NETWORK=1)."""
    if os.environ.get("SKYSHIFT_NETWORK") == "1":
        return

    def offline(adql: str):
        raise ConnectionError("offline test: no ESA TAP")

    from skyshift_pipeline import highpm
    monkeypatch.setattr(highpm, "deep_gaia_tap", offline)


@pytest.fixture(autouse=True)
def _dots_dir_env(monkeypatch):
    """The CLI and candidates set the dot-file folder in the environment (store.use_dots_dir): every test starts on
    dots/ and nothing a test sets leaks into the next."""
    from skyshift_pipeline.store import DOTS_DIR_ENV
    monkeypatch.setenv(DOTS_DIR_ENV, "dots")


def t(session: int, exposure: int = 0) -> float:
    return T0 + session * SESSION_STEP + exposure * EXPOSURE_STEP


@pytest.fixture
def rng():
    return np.random.default_rng(1)
