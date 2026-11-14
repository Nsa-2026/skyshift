"""The spectrum's photometry routes (obj.point_photometry): wings for a saturated core, a joint fit
next to a Gaia star, and leaving a point out when it can't be measured."""

import numpy as np
import pytest
from skyshift_core.flags import OVERFLOW, SUR_ERROR, TRANSIENT
from skyshift_core.photometry import APERTURE_CORRECTION

from skyshift_server import obj

N = 41
C = 20.3, 19.6  # source position, 0-based px


def _r(x0=C[0], y0=C[1]):
    yy, xx = np.mgrid[:N, :N]
    return np.hypot(xx - x0, yy - y0)


def _saturated_star(total: float, sky: float = 2.0, det: int = 2):
    """A star following the wing profile out to 4.75 px, its core (r < 1.6 px) NaN and OVERFLOW,
    the next ring TRANSIENT | SUR_ERROR (as bright stars flag their own wings)."""
    r = _r()
    prof = np.exp(np.interp(r, obj.WING_PROFILE_R, np.log(np.asarray(obj.WING_PROFILE[det]) * 1e-4)))
    data = np.where(r <= 4.75, total * prof, 0.0) + sky
    flags = np.zeros((N, N), np.int32)
    core = r < 1.6
    data[core] = np.nan
    flags[core] = OVERFLOW
    flags[(r >= 1.6) & (r < 3.0)] |= TRANSIENT | SUR_ERROR
    return data.astype(np.float32), flags


def test_wing_photometry_recovers_the_total_and_carries_the_systematic():
    data, flags = _saturated_star(20000.0)
    flux, err, stat = obj.wing_photometry(data, flags, *C, 2, conv=889.7)
    assert flux == pytest.approx(20000.0 * 889.7, rel=0.02)
    assert err == pytest.approx(np.hypot(stat, obj.WING_SYSTEMATIC * flux))
    assert err >= 0.5 * flux


def test_wing_photometry_needs_pixels():
    data, flags = _saturated_star(20000.0)
    flags[_r() < 6.0] |= 1 << 6  # NONFUNC: no usable wing pixels
    assert obj.wing_photometry(data, flags, *C, 2, conv=889.7) is None


def test_blended_photometry_separates_two_stars():
    det = 2
    sigma, k = obj.BLEND_PSF[det]
    target, neighbour = (20.2, 20.1), (21.9, 20.6)  # 1.8 px (11") apart
    data = 5.0 + 0.0 * _r()
    data = data + 40.0 * obj._gauss_psf(N, N, *target, sigma) + 300.0 * obj._gauss_psf(N, N, *neighbour, sigma)
    var = np.full((N, N), 0.01, np.float32)
    flux, err = obj.blended_photometry(data.astype(np.float32), np.zeros((N, N), np.int32), var, *target,
                                       [neighbour], det, conv=1.0)
    assert flux == pytest.approx(40.0 * k * APERTURE_CORRECTION, rel=1e-3)
    assert 0 < err < 1.0
    # a target that isn't there: zero within the error, so the site shows an upper limit
    data0 = 5.0 + 300.0 * obj._gauss_psf(N, N, *neighbour, sigma)
    flux0, err0 = obj.blended_photometry(data0.astype(np.float32), np.zeros((N, N), np.int32), var, *target,
                                         [neighbour], det, conv=1.0)
    assert abs(flux0) < 3 * err0 + 1e-6


def test_neighbours_at_moves_stars_and_skips_the_target_and_faint_stars():
    target = {"id": "1", "ra": 10.0, "dec": 0.0, "pmra": 0.0, "pmdec": 0.0, "parallax": None, "g": 12.0}
    near = {"id": "2", "ra": 10.0 + 8.0 / 3600, "dec": 0.0, "pmra": 0.0, "pmdec": 0.0, "parallax": None, "g": 14.0}
    faint = {"id": "3", "ra": 10.0, "dec": 6.0 / 3600, "pmra": 0.0, "pmdec": 0.0, "parallax": None, "g": 17.0}
    far = {"id": "4", "ra": 10.0, "dec": 30.0 / 3600, "pmra": 0.0, "pmdec": 0.0, "parallax": None, "g": 12.0}
    fast = {"id": "5", "ra": 10.0, "dec": 60.0 / 3600, "pmra": 0.0, "pmdec": -5000.0, "parallax": None, "g": 12.0}
    mjd = obj.GAIA_EPOCH_MJD + 10 * 365.25  # `fast` has come 50" south, to 10" from the target
    nb = obj.neighbours_at([target, near, faint, far, fast], target, mjd, 10.0, 0.0)
    assert sorted(round(s) for s, _, _ in nb) == [8, 10]
    # a target Gaia doesn't see (no G): every star counts
    nb = obj.neighbours_at([near, faint, far], dict(target, id=None, g=None), mjd, 10.0, 0.0)
    assert sorted(round(s) for s, _, _ in nb) == [6, 8]
    assert obj.neighbours_at([near], None, mjd, 10.0, 0.0) == []
