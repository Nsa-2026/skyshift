"""Shared paths and helpers for the engine tests (offline: every upstream call is replaced)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest

DATA = Path(__file__).parent / "data"


@pytest.fixture(autouse=True)
def _measured_off(monkeypatch):
    """The measured layer reads the data bucket: off unless a test turns it on (test_measured.py)."""
    monkeypatch.setenv("SKYSHIFT_MEASURED", "0")
FIXTURE_CUTOUT = Path(__file__).resolve().parents[2] / "fixtures" / "fits" / "cutout_2025W18_1B_0074_2D1.fits"


@pytest.fixture
def cutout_bytes() -> bytes:
    return FIXTURE_CUTOUT.read_bytes()


def sia_row(obs_id: str, det: int, mjd: float, collection: str = "spherex_dr1", **extra):
    half = 113.0 / 86400.0 / 2.0
    row = {
        "obs_collection": collection, "obs_id": obs_id, "energy_bandpassname": f"SPHEREx-D{det}",
        "t_min": mjd - half, "t_max": mjd + half,
        "access_url": f"https://irsa.example/ibe/{collection}/{obs_id}D{det}.fits",
        "cloud_access": '{"aws": {"bucket_name": "nasa-irsa-spherex", "key":"x/%sD%d.fits", "region": "us-east-1"}}' % (obs_id, det),
        "dataproduct_type": "image", "dataproduct_subtype": "science",
    }
    row.update(extra)
    return row


def tiny_index_npz() -> bytes:
    """A 3-row MPC known-object index (skyshift_core.known_index format): Ceres-like and a numbered
    main-belt orbit, and one comet. For tests only: the real one is built from the MPC snapshot."""
    from skyshift_core import known_index as ki

    blob, off = ki._pack_names(["Ceres", "(12345)", "C/2025 A1"])
    data = {
        "version": np.array(ki.INDEX_VERSION), "snapshot": np.array("synthetic"),
        "built": np.array("2026-11-14T00:00:00Z"), "mjd_lo": np.array(ki.MJD_LO), "mjd_hi": np.array(ki.MJD_HI),
        "v_keep": np.array(ki.V_KEEP),
        "q": np.array([2.55, 2.1, 0.9]), "e": np.array([0.079, 0.15, 0.995]), "inc": np.array([10.6, 5.0, 40.0]),
        "node": np.array([80.3, 120.0, 200.0]), "argp": np.array([73.6, 30.0, 10.0]),
        "tp": np.array([2461500.0, 2461200.0, 2461100.0]), "epoch": np.full(3, 2461200.5),
        "h": np.array([3.3, 15.0, np.nan], np.float32), "g": np.array([0.12, 0.15, 0.15], np.float32),
        "comet": np.array([False, False, True]), "err_deg": np.array([0.01, 0.02, np.nan], np.float32),
        "always": np.zeros(3, bool), "name_blob": blob, "name_off": off,
    }
    b = io.BytesIO()
    np.savez_compressed(b, **data)
    return b.getvalue()
