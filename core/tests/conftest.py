"""Shared fixtures and synthetic-image helpers for core/ tests."""

from __future__ import annotations

import json
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pytest
from astropy.wcs import WCS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fits"
CUTOUT = FIXTURES / "cutout_2025W18_1B_0074_2D1.fits"
CUTOUT_STARS = FIXTURES / "cutout_2025W18_1B_0074_2D1.stars.json"
PIXEL_ARCSEC = 6.152

_verf = np.vectorize(erf)


def gaussian_image(shape: tuple[int, int], xs, ys, fluxes, sigma: float) -> np.ndarray:
    """Pixel-integrated circular Gaussians (exact, via erf) at 0-based (x, y)."""
    ny, nx = shape
    img = np.zeros(shape)
    ex = np.arange(nx + 1) - 0.5
    ey = np.arange(ny + 1) - 0.5
    for x0, y0, f in zip(np.atleast_1d(xs), np.atleast_1d(ys), np.atleast_1d(fluxes)):
        cx = np.diff(0.5 * _verf((ex - x0) / (sqrt(2) * sigma)))
        cy = np.diff(0.5 * _verf((ey - y0) / (sqrt(2) * sigma)))
        img += f * np.outer(cy, cx)
    return img


def tan_wcs(ra: float, dec: float, shape: tuple[int, int], scale_arcsec: float = PIXEL_ARCSEC) -> WCS:
    """Simple TAN WCS, north up, east left, centred on (ra, dec)."""
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(shape[1] + 1) / 2, (shape[0] + 1) / 2]
    w.wcs.cdelt = [-scale_arcsec / 3600, scale_arcsec / 3600]
    w.pixel_shape = (shape[1], shape[0])
    return w


@pytest.fixture(scope="session")
def cutout_path() -> Path:
    assert CUTOUT.exists(), "run core/scripts/make_cutout_fixture.py"
    return CUTOUT


@pytest.fixture(scope="session")
def cutout_stars() -> dict:
    return json.loads(CUTOUT_STARS.read_text())
