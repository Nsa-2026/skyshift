"""Shared paths and helpers for the engine tests (offline: every upstream call is replaced)."""

from __future__ import annotations

from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
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
