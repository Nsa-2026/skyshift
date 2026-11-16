"""skyshift_core.motion: the motion model's modes, the parallax leak and steadiness, on synthetic sources (no network)."""

import numpy as np
import pytest

from skyshift_core import motion as M
from skyshift_core.astrometry import propagate, tangent_deproject

RA, DEC = 266.9, 4.7       # near the ecliptic at RA ~ 18 h: a large parallax ellipse in RA
T0 = 61000.0               # MJD
GAIA = 57388.5


def source(rng, pmra, pmdec, plx, visits, err=0.1, per_visit=40, shift=None):
    """Positions of one star: `per_visit` photos over 20 days per visit start (MJD), ″ errors `err`.
    shift {visit index: (e, n) ″}: that visit sits off the line (out and back)."""
    mjd, ra, dec, vis = [], [], [], []
    for v, t in enumerate(visits):
        m = t + np.sort(rng.uniform(0, 20, per_visit))
        r, d = propagate(np.full(per_visit, RA), np.full(per_visit, DEC), pmra, pmdec, m, epoch_mjd=GAIA,
                         parallax=np.full(per_visit, plx))
        e, n = rng.normal(0, err, (2, per_visit))
        if shift and v in shift:
            e, n = e + shift[v][0], n + shift[v][1]
        r, d = tangent_deproject(r, d, e, n)
        mjd.append(m), ra.append(r), dec.append(d), vis.append(np.full(per_visit, v + 1))
    return [np.concatenate(x) for x in (mjd, ra, dec, vis)] + [np.full(per_visit * len(visits), err)]


THREE = (T0, T0 + 182.6, T0 + 365.25)   # both seasons, a year in all
TWO_OPP = (T0, T0 + 182.6)              # opposite seasons: parallax and motion can't be told apart
TWO_SAME = (T0, T0 + 365.25)            # one season a year apart: parallax cancels


def fit(rng, pmra, pmdec, plx, visits, gaia=None, **kw):
    mjd, ra, dec, vis, err = source(rng, pmra, pmdec, plx, visits, **kw)
    # errors given honestly: no extra scale (fit_motion's default scale is for the dot files' pos_err)
    return M.fit_motion(mjd, ra, dec, err, vis, *(gaia or (None, None)), scale=1.0)


def test_steady_mover_three_visits_fits_parallax_and_motion():
    r = fit(np.random.default_rng(1), 800.0, -450.0, 120.0, THREE)
    assert r.mode == M.MODE_PLX and r.n_visits == 3 and r.free > 0.5
    assert abs(r.pmra - 800) < 4 * r.pmra_err and abs(r.pmdec + 450) < 4 * r.pmdec_err and r.pmra_err < 30
    assert abs(r.parallax - 120) < 4 * r.parallax_err
    assert not r.unsteady and r.visit_dof == 1 and r.visit_chi2_nu < M.UNSTEADY_CHI2_NU


def test_pure_parallax_star_leaks_into_pm_with_two_visits():
    """ϖ = 0.5″, no motion: two visits six months apart give a fake motion ϖ·k; Gaia's parallax takes it out."""
    rng = np.random.default_rng(2)
    leak = fit(rng, 0.0, 0.0, 500.0, TWO_OPP)
    assert leak.mode == M.MODE_LEAK and leak.free < 0.1 and np.hypot(leak.k_e, leak.k_n) > M.LEAK_SMALL
    assert abs(leak.pmra - 500.0 * leak.k_e) < 4 * leak.pmra_err + 20 and leak.pm > 1000  # ~ −2 ″/yr in RA here
    gaia = fit(rng, 0.0, 0.0, 500.0, TWO_OPP, gaia=(500.0, 0.1))
    assert gaia.mode == M.MODE_GAIA and gaia.pm < 4 * max(gaia.pmra_err, gaia.pmdec_err)
    assert abs(gaia.parallax - 500.0) < 1.0  # held at Gaia's
    # Gaia's parallax error carries into the pm error as |k|·σ_ϖ
    loose = fit(rng, 0.0, 0.0, 500.0, TWO_OPP, gaia=(500.0, 5.0))
    assert loose.pmra_err > abs(loose.k_e) * 5.0 * 0.9
    # three visits over both seasons, no Gaia: ϖ is measured and the motion is clean
    own = fit(rng, 0.0, 0.0, 500.0, THREE)
    assert own.mode == M.MODE_PLX and abs(own.parallax - 500) < 4 * own.parallax_err and own.pm < 4 * own.pmra_err + 10


def test_same_season_pair_cancels_parallax():
    r = fit(np.random.default_rng(3), 300.0, 0.0, 500.0, TWO_SAME)
    assert r.mode == M.MODE_PM and np.hypot(r.k_e, r.k_n) <= M.LEAK_SMALL
    assert abs(r.pmra - 300) < 4 * r.pmra_err + 500 * M.LEAK_SMALL and abs(r.pmra - 300) < 60


def test_out_and_back_is_unsteady_but_a_line_is_not():
    rng = np.random.default_rng(4)
    back = fit(rng, 0.0, 0.0, 0.0, THREE, gaia=(0.0, 0.1), shift={1: (1.2, 0.0)})  # the middle visit 1.2″ off
    assert back.mode == M.MODE_GAIA and back.unsteady and back.visit_chi2_nu > M.UNSTEADY_CHI2_NU
    assert back.wobble_arcsec > 0.3
    line = fit(rng, 0.0, 0.0, 0.0, THREE, gaia=(0.0, 0.1), shift={1: (0.6, 0.0), 2: (1.2, 0.0)})  # 1.2″/yr: real motion
    assert not line.unsteady and abs(line.pmra - 1200) < 4 * line.pmra_err + 30
    # no Gaia: the free parallax can't absorb an out-and-back along a direction the parallax ellipse doesn't take
    free = fit(rng, 0.0, 0.0, 0.0, THREE, shift={1: (0.0, 1.5)})
    assert free.mode == M.MODE_PLX and free.unsteady


def test_two_visits_cannot_tell_steadiness():
    r = fit(np.random.default_rng(5), 0.0, 0.0, 0.0, TWO_OPP, gaia=(0.0, 0.1), shift={1: (1.5, 0.0)})
    assert r.visit_dof == 0 and np.isnan(r.visit_chi2_nu) and not r.unsteady
    assert r.pmra > 1000  # a 1.5″ jump in half a year is a motion as far as two visits can tell


def test_batched_leak_matches_and_modes():
    rng = np.random.default_rng(6)
    mjd, ra, dec, vis, err = source(rng, 0.0, 0.0, 0.0, TWO_OPP)
    t = (mjd - mjd.mean()) / 365.25
    pe, pn = M.parallax_factors(np.full(mjd.size, RA), np.full(mjd.size, DEC), M.observer_xyz_au(mjd))
    s = np.r_[np.zeros(mjd.size, np.int64), np.ones(mjd.size, np.int64)]
    ke, kn, free = M.leak(s, 2, np.ones(2 * mjd.size), np.r_[t, t], np.r_[pe, pe], np.r_[pn, pn])
    assert ke[0] == pytest.approx(ke[1]) and free[0] < 0.1 and np.hypot(ke[0], kn[0]) > M.LEAK_SMALL
    one = M.fit_motion(mjd, ra, dec, err, vis, scale=1.0)
    assert one.k_e == pytest.approx(ke[0], rel=1e-6)
    modes = M.choose_mode([True, False, False, False], [2, 3, 3, 2], [0.0, 0.9, 0.05, 0.0], [5, 0, 5, 0.3], [0, 0, 0, 0.2])
    assert list(modes) == [M.MODE_GAIA, M.MODE_PLX, M.MODE_LEAK, M.MODE_PM]
    v, w = M.gaia_prior([10.0, np.nan, 3.0], [0.1, 0.1, 0.0])
    assert w[0] > 0 and w[1] == 0 and w[2] == 0 and v[0] == pytest.approx(0.01)


def test_earth_position_is_good_enough_for_spherex():
    """SPHEREx is ≤ ~7,100 km from the geocentre: its parallax factors differ from the Earth's by < 5e-5."""
    mjd = np.array([T0, T0 + 91.0])
    earth = M.parallax_factors(np.full(2, RA), np.full(2, DEC), M.observer_xyz_au(mjd))
    sc = M.parallax_factors(np.full(2, RA), np.full(2, DEC), M.observer_xyz_au(mjd, np.array([[7100.0, 0, 0], [0, 7100.0, 0]])))
    d = np.hypot(earth[0] - sc[0], earth[1] - sc[1])
    assert (d < 5e-5).all() and (d > 1e-5).any()
    assert 547.0 * d.max() < 0.03  # mas, Barnard's Star
