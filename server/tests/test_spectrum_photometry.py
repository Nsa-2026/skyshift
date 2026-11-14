"""The spectrum's photometry routes (obj.point_photometry): wings for a saturated core, a joint fit
next to a Gaia star, and leaving a point out when it can't be measured."""

from collections import Counter
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.wcs import WCS
from skyshift_core import sky_to_pixel, ujy_per_mjysr
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


# --- a built-in target Gaia doesn't see, 4" from a Gaia star (WISE 0855 in visit 3, #143) ---------

PIX = 6.152  # arcsec
T_RA, T_DEC = 133.76, -7.24
MJD3 = 61160.0  # visit 3
# east 3.0", north 2.65": 4.0" (0.65 px) from the target, as the G = 16.3 star in visit 3
N_RA, N_DEC = T_RA + 3.0 / 3600 / np.cos(np.radians(T_DEC)), T_DEC + 2.65 / 3600


def _builtin(**kw):
    """A built-in target as obj._builtin_targets() gives it: a slug, no Gaia id or G, its own motion."""
    m = {"name": "Test dwarf", "slug": "testbd", "id": None, "ra": T_RA, "dec": T_DEC, "pmra": 0.0,
         "pmdec": 0.0, "parallax": 0.0, "g": None, "epoch_mjd": obj.GAIA_EPOCH_MJD, "dets": None}
    m.update(kw)
    return m


def _gaia_star(ra=N_RA, dec=N_DEC, **kw):
    s = {"id": "5756956644387831296", "ra": ra, "dec": dec, "pmra": 0.0, "pmdec": 0.0, "parallax": None, "g": 16.3}
    s.update(kw)
    return s


def test_blend_min_arcsec_is_lower_only_for_a_builtin_target_gaia_does_not_see():
    by_slug = {m["slug"]: m for m in obj._builtin_targets()}
    assert obj.blend_min_arcsec(by_slug["wise0855"]) == obj.BLEND_MIN_ARCSEC_BUILTIN == 3.5
    for slug in ("barnard", "epsindib", "rwaur", "rdra"):  # in Gaia: the 5" rule stays
        assert obj.blend_min_arcsec(by_slug[slug]) == obj.BLEND_MIN_ARCSEC == 5.0
    assert obj.blend_min_arcsec(None) == 5.0  # a typed position, nothing identified
    assert obj.blend_min_arcsec(_gaia_star(g=None)) == 5.0  # a Gaia star with no G: not built in
    assert obj.blend_min_arcsec(_builtin(parallax=None)) == 5.0  # no full astrometry of its own
    assert obj.blend_min_arcsec(_builtin(pmra=None)) == 5.0
    assert obj.blend_min_arcsec(_builtin(id="123")) == 5.0  # a built-in Gaia star


def _wcs():
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [T_RA, T_DEC]
    w.wcs.crpix = [21.3, 20.6]  # 1-based: the target at 0-based (20.3, 19.6), off the pixel centre
    w.wcs.cdelt = [-PIX / 3600, PIX / 3600]
    return w


def _two_sources(target_amp, neighbour_amp, det=1, noise=2.0, seed=0, n_ra=N_RA, n_dec=N_DEC):
    """A fake cutout (what point_photometry and spectrum_points read) with the target and its Gaia
    neighbour drawn with the joint fit's PSF, a flat sky of 5 and Gaussian noise; and the target's
    true flux in uJy."""
    w = _wcs()
    sigma, k = obj.BLEND_PSF[det]
    tx, ty = (float(v) for v in sky_to_pixel(w, T_RA, T_DEC))
    nx_, ny_ = (float(v) for v in sky_to_pixel(w, n_ra, n_dec))
    data = (5.0 + target_amp * obj._gauss_psf(N, N, tx, ty, sigma)
            + neighbour_amp * obj._gauss_psf(N, N, nx_, ny_, sigma))
    data = data + np.random.default_rng(seed).normal(0.0, noise, data.shape)
    l2 = SimpleNamespace(data=data.astype(np.float32), flags=np.zeros((N, N), np.int32),
                         variance=np.full((N, N), noise**2, np.float32), wcs=w, detector=det, mjd_avg=MJD3,
                         wave=True, wavelength=lambda x, y: 0.9)
    return l2, target_amp * k * APERTURE_CORRECTION * ujy_per_mjysr(w, tx, ty)


def test_the_joint_fit_recovers_a_faint_target_4_arcsec_from_a_bright_star():
    """Both positions fixed 0.65 px apart: the target's flux is unbiased and its error honest, with a
    neighbour 25x brighter (D1-D3), no target at all, or a neighbour of similar flux (D5-D6)."""
    det = 1
    w = _wcs()
    t = tuple(float(v) for v in sky_to_pixel(w, T_RA, T_DEC))
    n = tuple(float(v) for v in sky_to_pixel(w, N_RA, N_DEC))
    assert float(np.hypot(t[0] - n[0], t[1] - n[1])) * PIX == pytest.approx(4.0, abs=0.02)
    conv = ujy_per_mjysr(w, *t)
    for target_amp, neighbour_amp in ((40.0, 1000.0), (0.0, 1000.0), (300.0, 250.0)):
        pulls = []
        for seed in range(200):
            l2, truth = _two_sources(target_amp, neighbour_amp, det=det, seed=seed)
            flux, err = obj.blended_photometry(l2.data, l2.flags, l2.variance, *t, [n], det, conv=conv)
            pulls.append((flux - truth) / err)
        assert abs(np.mean(pulls)) < 0.2
        assert 0.85 < np.std(pulls) < 1.15


def test_point_photometry_fits_a_builtin_target_at_4_arcsec_and_drops_anything_else():
    l2, truth = _two_sources(40.0, 1000.0)
    x, y = (float(v) for v in sky_to_pixel(l2.wcs, T_RA, T_DEC))
    nb = obj.neighbours_at([_gaia_star()], _builtin(), MJD3, T_RA, T_DEC)
    assert [round(s, 1) for s, _, _ in nb] == [4.0]
    got = obj.point_photometry(l2, x, y, nb, False, obj.blend_min_arcsec(_builtin()))
    assert got is not None
    flux, err = got
    assert abs(flux - truth) < 3 * err
    assert err < truth / 3  # the 25x brighter star does not drown it
    # the same pixels for a target with no astrometry of its own: 4" is under 5", no point
    assert obj.point_photometry(l2, x, y, nb, False) is None
    assert obj.point_photometry(l2, x, y, nb, False, obj.blend_min_arcsec(None)) is None


def test_spectrum_points_brings_back_a_builtin_target_4_arcsec_from_a_gaia_star():
    """The WISE 0855 case end to end: a point for the built-in target, none for a Gaia-less source
    that isn't built in, and none when the star is closer than 3.5"."""
    l2, truth = _two_sources(40.0, 1000.0)
    im = SimpleNamespace(key="fakeD1", visit=3)
    dropped = Counter()
    pts = obj.spectrum_points([im], [l2], _builtin(), T_RA, T_DEC, stars=[_gaia_star()], dropped=dropped)
    assert len(pts) == 1 and dropped == Counter()
    assert pts[0]["visit"] == 3 and pts[0]["det"] == 1
    assert abs(pts[0]["flux_ujy"] - truth) < 3 * pts[0]["err_ujy"]

    not_builtin = {k: v for k, v in _builtin(id="99").items() if k != "slug"}  # a Gaia source with no G
    dropped = Counter()
    assert obj.spectrum_points([im], [l2], not_builtin, T_RA, T_DEC, stars=[_gaia_star()], dropped=dropped) == []
    assert dropped == Counter(other=1)

    # 3.0" away (east 2.0", north 2.24"): too close even for a built-in target
    c_ra, c_dec = T_RA + 2.0 / 3600 / np.cos(np.radians(T_DEC)), T_DEC + 2.24 / 3600
    l2c, _ = _two_sources(40.0, 1000.0, n_ra=c_ra, n_dec=c_dec)
    dropped = Counter()
    assert obj.spectrum_points([im], [l2c], _builtin(), T_RA, T_DEC, stars=[_gaia_star(c_ra, c_dec)],
                               dropped=dropped) == []
    assert dropped == Counter(other=1)


# --- the aperture's sky: a clipped median, not the annulus mean ------------------------------------

def _sky_case(seed: int, amp: float, contaminated: bool, noise: float = 0.03):
    """A 61 x 61 fake cutout, flat sky 0.45 MJy/sr + noise, at (30.3, 29.6) a source of `amp` (all its
    light inside r = 2 px); contaminated: a very bright star's halo, centred 16.5 px north (as Barnard's
    Star at its 2016.0 position, 16-18 px away), and a neighbour inside the 4-7 px annulus. Returns
    (l2, x, y, true flux in uJy)."""
    n, (x, y) = 61, (30.3, 29.6)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval, w.wcs.crpix = [269.45, 4.74], [31.0, 31.0]
    w.wcs.cdelt = [-PIX / 3600, PIX / 3600]
    sigma = obj.BLEND_PSF[2][0]
    yy, xx = np.mgrid[:n, :n]
    data = 0.45 + amp * obj._gauss_psf(n, n, x, y, sigma)
    if contaminated:
        data = data + 100.0 * (1.0 + (np.hypot(xx - x, yy - (y + 16.5)) / 1.5) ** 2) ** -1.5
        data = data + 8.0 * obj._gauss_psf(n, n, x + 5.2, y - 1.5, sigma)
    data = data + np.random.default_rng(seed).normal(0.0, noise, data.shape)
    l2 = SimpleNamespace(data=data.astype(np.float32), flags=np.zeros((n, n), np.int32),
                         variance=np.full((n, n), noise**2, np.float32), wcs=w, detector=2)
    return l2, x, y, amp * APERTURE_CORRECTION * ujy_per_mjysr(w, x, y)


def test_aperture_sky_ignores_a_bright_stars_halo_and_a_neighbour_in_the_annulus():
    """14 Nov site review: at Barnard's Star's 2016.0 position (`moved_away`) 58 of 61 points were
    5-12 sigma negative, the annulus mean pulled up by the star 16-18 px away. The clipped median
    gives unbiased fluxes and honest errors there, for an empty spot and for a faint source."""
    from skyshift_core import aperture_photometry

    for amp, contaminated in ((0.0, True), (3.0, True), (0.0, False), (3.0, False)):
        new, old = [], []
        for seed in range(100):
            l2, x, y, truth = _sky_case(seed, amp, contaminated)
            flux, err = obj.point_photometry(l2, x, y, [], False)
            new.append((flux - truth) / err)
            ph = aperture_photometry(l2.data, x, y, l2.wcs, flags=l2.flags, variance=l2.variance)  # annulus mean
            old.append((ph.flux_ujy[0] - truth) / ph.err_ujy[0])
        assert abs(np.mean(new)) < 0.3 and 0.8 < np.std(new) < 1.2, (amp, contaminated)
        if contaminated:
            assert np.mean(old) < -8  # what the annulus mean did: many sigma below the truth
        else:
            assert abs(np.mean(old)) < 0.3  # on a clean sky both agree


def test_local_sky_needs_good_pixels():
    l2, x, y, _ = _sky_case(0, 0.0, False)
    sky, err = obj.local_sky(l2.data, l2.flags, x, y)
    assert sky == pytest.approx(0.45, abs=0.01) and 0 < err < 0.01
    yy, xx = np.mgrid[:61, :61]
    l2.flags[np.hypot(xx - x, yy - y) > 3.5] |= 1 << 6  # NONFUNC: the annulus is masked, the aperture is not
    assert obj.local_sky(l2.data, l2.flags, x, y) is None
    assert obj.point_photometry(l2, x, y, [], False) is None
