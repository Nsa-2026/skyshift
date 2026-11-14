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


def t(session: int, exposure: int = 0) -> float:
    return T0 + session * SESSION_STEP + exposure * EXPOSURE_STEP


@pytest.fixture
def rng():
    return np.random.default_rng(1)
