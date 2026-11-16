"""Motion between visits (the months clock): position(t) = p0 + μ·(t − t0) + ϖ·P(t).

On the tangent plane about a source's position, per axis (east = RA × cos dec, north = Dec), arcsec:
    east(t)  = x0 + μ_α* · (t − t0) + ϖ · Pe(t)
    north(t) = y0 + μ_δ  · (t − t0) + ϖ · Pn(t)
t in Julian years, μ in ″/yr, ϖ in ″; (Pe, Pn) the parallax factors: the parallactic shift per unit parallax for the
observer's barycentric position at t (parallax_factors). The observer is the Earth (astropy's built-in ephemeris, no
network) plus SPHEREx's geocentric offset when the photo gives it. SPHEREx is in a low Earth orbit (~700 km up,
≤ ~7,100 km from the geocentre = 4.7e-5 AU), so leaving the offset out moves a star by at most ϖ × 4.7e-5: 0.03 mas for
Barnard's Star (ϖ 547 mas), against our ~0.1–0.4″ per photo. Earth's position is good enough.

Why it matters: SPHEREx sees a spot once every six months, and parallax pushes a nearby star one way, then back, every
six months. A straight line through two visits half a year apart measures μ + ϖ·k, k = (k_e, k_n) the parallax leak
(the regression of the parallax factors on time over the source's photos; hunt zone ≈ (−4.1, +1.0) per source, up to
−5.9 mas/yr per mas). So the fit chooses its mode per source (choose_mode):
- MODE_GAIA "pm+gaia_plx": a Gaia DR3 match with a parallax: ϖ is held at Gaia's (a prior with Gaia's error, floor
  GAIA_PLX_ERR_FLOOR_MAS) and p0, μ are fitted. The leak is gone; Gaia's parallax error adds |k|·σ_ϖ to the pm error.
- MODE_PLX "pm+plx": no Gaia parallax, ≥ PLX_MIN_VISITS visits and the parallax factors are not a straight line in time
  over them (the visits span both seasons: free share ≥ PLX_MIN_FREE): ϖ is fitted with p0, μ and reported with its
  error. The proper motion then rests on the same-season visits about a year apart, where parallax cancels.
- MODE_PM "pm": no parallax fitted and the leak is small (|k| ≤ LEAK_SMALL: visits in one season, a year apart, where the
  parallax factors are nearly equal): μ is clean of parallax.
- MODE_LEAK "pm_leak": no parallax fitted and the leak is large (two visits in opposite seasons): the fitted motion is
  μ + ϖ·k, and k says how much parallax may be in it (a check against a catalogue adds ϖ·k to the catalogue's pm).

Fit quality (visit_steadiness): each visit's weighted mean residual against the fit, with its error from the dots ×
POS_ERR_SCALE (pos_err is 1.4–1.8× too small against Gaia) and VISIT_FLOOR_ARCSEC in quadrature (errors shared by a
visit's photos), gives χ²_ν over the visits (2 per visit − the fitted motion parameters − 2 for p0). A source with
χ²_ν > UNSTEADY_CHI2_NU goes out and back instead of moving in a line: `unsteady` (blends, detector position,
saturation). It is not a proper-motion lead. Two visits leave no freedom (χ²_ν NaN): steadiness can't be told.

fit_motion is the plain one-source fit (no per-detector zero points); the pipeline's astrometry step uses the same
modes, prior, leak and steadiness test on top of its own batched fit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .astrometry import DAYS_PER_JYEAR, tangent_offset_arcsec

AU_KM = 149_597_870.7
POS_ERR_SCALE = 1.6            # per-dot pos_err is 1.4–1.8× too small against Gaia (hunt and pilot, 14 Nov)
VISIT_FLOOR_ARCSEC = 0.02      # added in quadrature to each visit's mean position error (errors a visit's photos share):
                               # Gaia stars G 12–14 in t_wise0855 sit 0.022″ (median) off their fit per visit
UNSTEADY_CHI2_NU = 8.0         # visit-level χ²_ν above this: no straight-line motion (out and back). By chance alone 0.03%
                               # (2 dof) to 0.5% (1 dof); t_wise0855 Gaia stars: 5.7% flagged, their |pm − Gaia| median
                               # 358 vs 111 mas/yr for the rest (16 Nov)
PLX_MIN_VISITS = 3             # parallax is fitted (no Gaia) only with this many visits ...
PLX_MIN_FREE = 0.2             # ... and when this share of the parallax factors' variance is not a line in time
LEAK_SMALL = 1.0               # mas/yr per mas: a pm fitted without parallax counts as clean below this |k|
GAIA_PLX_ERR_FLOOR_MAS = 0.02  # Gaia DR3 parallax systematics (zero point ~ −0.02 mas): floor of the prior's error

MODE_GAIA, MODE_PLX, MODE_PM, MODE_LEAK = "pm+gaia_plx", "pm+plx", "pm", "pm_leak"
MODES = (MODE_GAIA, MODE_PLX, MODE_PM, MODE_LEAK)
PARALLAX_MODES = (MODE_GAIA, MODE_PLX)  # the motion has the parallax taken out


def observer_xyz_au(mjd: ArrayLike, sc_km: ArrayLike | None = None) -> NDArray[np.float64]:
    """(n, 3) barycentric ICRS position of the observer in AU: the Earth (astropy's built-in ephemeris) + SPHEREx's
    geocentric offset sc_km (n, 3) in km where finite (else the Earth's own)."""
    from astropy.coordinates import get_body_barycentric
    from astropy.time import Time

    mjd = np.atleast_1d(np.asarray(mjd, np.float64))
    out = np.zeros((mjd.size, 3))
    fin = np.isfinite(mjd)
    if fin.any():
        u, inv = np.unique(np.round(mjd[fin], 6), return_inverse=True)
        pos = get_body_barycentric("earth", Time(u, format="mjd", scale="utc"))
        xyz = np.stack([pos.x.to_value("AU"), pos.y.to_value("AU"), pos.z.to_value("AU")], axis=1)
        out[fin] = xyz[inv.ravel()]
    if sc_km is not None:
        sc = np.asarray(sc_km, np.float64)
        out += np.where(np.isfinite(sc), sc, 0.0) / AU_KM
    return out


def parallax_factors(ra_deg: ArrayLike, dec_deg: ArrayLike, xyz_au: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """(Pe, Pn): parallactic shift (east, north) per unit parallax for an observer at xyz_au (n, 3)."""
    a, d = np.deg2rad(np.asarray(ra_deg, np.float64)), np.deg2rad(np.asarray(dec_deg, np.float64))
    xyz = np.atleast_2d(np.asarray(xyz_au, np.float64))
    X, Y, Z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    return X * np.sin(a) - Y * np.cos(a), X * np.cos(a) * np.sin(d) + Y * np.sin(a) * np.sin(d) - Z * np.cos(d)


def gaia_prior(parallax_mas: ArrayLike, parallax_err_mas: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """(value ″, weight 1/″²) of the parallax prior from Gaia DR3: Gaia's parallax with its error (floor
    GAIA_PLX_ERR_FLOOR_MAS). Weight 0 (no prior) where either is missing or the error is not positive."""
    p = np.asarray(parallax_mas, np.float64)
    e = np.asarray(parallax_err_mas, np.float64)
    ok = np.isfinite(p) & np.isfinite(e) & (e > 0)
    sig = np.hypot(np.where(ok, e, 1.0), GAIA_PLX_ERR_FLOOR_MAS) / 1000.0
    return np.where(ok, p / 1000.0, 0.0), np.where(ok, 1.0 / sig ** 2, 0.0)


def _demean(g: NDArray[np.int64], G: int, w: NDArray[np.float64], v: NDArray[np.float64]) -> NDArray[np.float64]:
    sw = np.maximum(np.bincount(g, w, G), 1e-300)
    return v - (np.bincount(g, w * v, G) / sw)[g]


def leak(s: ArrayLike, N: int, w: ArrayLike, t: ArrayLike, pe: ArrayLike, pn: ArrayLike,
         z: ArrayLike | None = None) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Per source (rows grouped by s in 0..N−1, weights w): (k_e, k_n, free).
    k: the parallax leak, mas/yr per mas: a straight-line fit without parallax measures μ + ϖ·k (the weighted regression
    of the parallax factors on t). free: the share of the parallax factors' weighted variance that is not a line in t
    (0: parallax and motion can't be told apart, two visits; ~1: visits in both seasons a year apart).
    z: the groups the positions are referred to (e.g. one zero point per source × detector; default s): t, pe, pn are
    demeaned within them, as the fit does."""
    s = np.asarray(s, np.int64)
    w = np.asarray(w, np.float64)
    g = s if z is None else np.asarray(z, np.int64)
    G = int(g.max()) + 1 if g.size else 0
    T, PE, PN = (_demean(g, G, w, np.asarray(v, np.float64)) for v in (t, pe, pn))
    stt = np.bincount(s, w * T * T, N)
    spp = np.bincount(s, w * (PE * PE + PN * PN), N)
    with np.errstate(invalid="ignore", divide="ignore"):
        ke = np.where(stt > 0, np.bincount(s, w * T * PE, N) / stt, 0.0)
        kn = np.where(stt > 0, np.bincount(s, w * T * PN, N) / stt, 0.0)
        free = np.where(spp > 0, np.clip(1.0 - (ke * ke + kn * kn) * stt / spp, 0.0, 1.0), 0.0)
    return ke, kn, free


def choose_mode(has_gaia_plx: ArrayLike, n_visits: ArrayLike, free: ArrayLike, k_e: ArrayLike, k_n: ArrayLike) -> NDArray[np.str_]:
    """The fit mode per source (module docstring): MODE_GAIA, MODE_PLX, MODE_PM or MODE_LEAK."""
    g = np.asarray(has_gaia_plx, bool)
    plx = ~g & (np.asarray(n_visits) >= PLX_MIN_VISITS) & (np.asarray(free, np.float64) >= PLX_MIN_FREE)
    small = np.hypot(np.asarray(k_e, np.float64), np.asarray(k_n, np.float64)) <= LEAK_SMALL
    return np.where(g, MODE_GAIA, np.where(plx, MODE_PLX, np.where(small, MODE_PM, MODE_LEAK)))


def visit_steadiness(s: ArrayLike, N: int, visit: ArrayLike, w: ArrayLike, re: ArrayLike, rn: ArrayLike,
                     n_motion: ArrayLike, scale: float = POS_ERR_SCALE,
                     floor: float = VISIT_FLOOR_ARCSEC) -> tuple[NDArray[np.float64], NDArray[np.int64], NDArray[np.float64]]:
    """Per source: (χ²_ν over its visits, degrees of freedom, wobble ″: the 1/error²-weighted RMS distance of the visits
    from the fit).
    Each visit's weighted mean residual (re, rn ″, rows with w > 0), error √(scale² / Σw + floor²) per axis; dof =
    2 × visits − 2 − n_motion (2 for μ, 3 with a fitted parallax; a Gaia prior counts as known). χ²_ν NaN where dof < 1."""
    s = np.asarray(s, np.int64)
    w = np.asarray(w, np.float64)
    used = w > 0
    key = (s.astype(np.int64) << 20) | (np.asarray(visit, np.int64) & 0xFFFFF)
    _, c = np.unique(np.where(used, key, -1), return_inverse=True)
    c = c.ravel()
    K = int(c.max()) + 1 if c.size else 0
    cs = np.full(K, -1, np.int64)
    cs[c[used]] = s[used]
    sw = np.bincount(c, w, K)
    ok = (cs >= 0) & (sw > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        me = np.bincount(c, w * np.asarray(re, np.float64), K) / sw
        mn = np.bincount(c, w * np.asarray(rn, np.float64), K) / sw
        var = scale * scale / sw + floor * floor
    cs_ok = cs[ok]
    chi = np.bincount(cs_ok, (me[ok] ** 2 + mn[ok] ** 2) / var[ok], N)
    nv = np.bincount(cs_ok, minlength=N)
    iv = 1.0 / var[ok]
    sum_iv = np.bincount(cs_ok, iv, N)
    dof = (2 * nv - 2 - np.asarray(n_motion, np.int64)).astype(np.int64)
    with np.errstate(invalid="ignore", divide="ignore"):
        chi2_nu = np.where(dof >= 1, chi / np.maximum(dof, 1), np.nan)
        wobble = np.where(dof >= 1, np.sqrt(chi / sum_iv), np.nan)
    return chi2_nu, dof, wobble


def unsteady(visit_chi2_nu: ArrayLike) -> NDArray[np.bool_]:
    """No straight-line motion: visit-level χ²_ν above UNSTEADY_CHI2_NU (NaN: can't tell, False)."""
    v = np.asarray(visit_chi2_nu, np.float64)
    with np.errstate(invalid="ignore"):
        return np.isfinite(v) & (v > UNSTEADY_CHI2_NU)


@dataclass
class MotionFit:
    """One source's motion (fit_motion). Angles in the units of the contracts: mas/yr, mas, ″; epoch MJD."""
    mode: str
    epoch_mjd: float
    ra: float
    dec: float
    pmra: float
    pmdec: float
    pmra_err: float
    pmdec_err: float
    parallax: float           # MODE_GAIA: the posterior (≈ Gaia's); MODE_PLX: ours; else NaN
    parallax_err: float
    k_e: float                # the parallax leak (mas/yr per mas) of a fit without parallax
    k_n: float
    free: float               # share of the parallax factors not a line in time
    n_visits: int
    chi2_nu: float            # over the dots (errors × POS_ERR_SCALE)
    visit_chi2_nu: float         # over the visits (visit_steadiness); NaN with < 1 degree of freedom
    visit_dof: int
    wobble_arcsec: float      # RMS distance of the visits from the fit (1/error²-weighted); NaN as visit_chi2_nu
    unsteady: bool

    @property
    def pm(self) -> float:
        return float(np.hypot(self.pmra, self.pmdec))


def fit_motion(mjd: ArrayLike, ra: ArrayLike, dec: ArrayLike, pos_err_arcsec: ArrayLike, visit: ArrayLike,
               gaia_parallax: float | None = None, gaia_parallax_err: float | None = None,
               sc_km: ArrayLike | None = None, scale: float = POS_ERR_SCALE) -> MotionFit:
    """One source's positions (one per photo: MJD, deg, ″ error, visit number) → its motion (module docstring).
    A plain weighted least-squares fit on the tangent plane about the median position, errors × `scale`."""
    mjd = np.asarray(mjd, np.float64)
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    err = np.asarray(pos_err_arcsec, np.float64) * scale
    vis = np.asarray(visit, np.int64)
    n = mjd.size
    ra0, dec0 = float(np.median(ra)), float(np.median(dec))
    east, north = tangent_offset_arcsec(ra0, dec0, ra, dec)
    pe, pn = parallax_factors(np.full(n, ra0), np.full(n, dec0), observer_xyz_au(mjd, sc_km))
    w = 1.0 / err ** 2
    t0 = float(np.sum(w * mjd) / np.sum(w))
    t = (mjd - t0) / DAYS_PER_JYEAR
    s = np.zeros(n, np.int64)
    ke, kn, free = (float(x[0]) for x in leak(s, 1, w, t, pe, pn))
    pv, pw = gaia_prior(np.nan if gaia_parallax is None else gaia_parallax,
                        np.nan if gaia_parallax_err is None else gaia_parallax_err)
    n_vis = int(np.unique(vis).size)
    mode = str(choose_mode([pw > 0], [n_vis], [free], [ke], [kn])[0])
    use_plx = mode in PARALLAX_MODES
    # rows: east then north; columns x0, y0, μα*, μδ (, ϖ)
    one, zero = np.ones(n), np.zeros(n)
    A = np.vstack([np.column_stack([one, zero, t, zero, pe]), np.column_stack([zero, one, zero, t, pn])])
    y = np.concatenate([east, north])
    W = np.concatenate([w, w])
    if not use_plx:
        A = A[:, :4]
    M = A.T @ (A * W[:, None])
    b = A.T @ (W * y)
    if mode == MODE_GAIA:
        M[4, 4] += float(pw)
        b[4] += float(pw * pv)
    cov = np.linalg.pinv(M)
    p = cov @ b
    r = y - A @ p
    re, rn = r[:n], r[n:]
    n_par = A.shape[1] - (1 if mode == MODE_GAIA else 0)
    chi2_nu = float(np.sum(W * r * r) / max(2 * n - n_par, 1))
    chi_v, dof_v, wob = visit_steadiness(s, 1, vis, w, re, rn, [3 if mode == MODE_PLX else 2], scale=1.0)
    from .astrometry import tangent_deproject
    ra_f, dec_f = tangent_deproject(ra0, dec0, p[0], p[1])
    plx, plx_e = (p[4] * 1000.0, np.sqrt(cov[4, 4]) * 1000.0) if use_plx else (np.nan, np.nan)
    return MotionFit(mode=mode, epoch_mjd=t0, ra=float(ra_f), dec=float(dec_f), pmra=float(p[2] * 1000.0),
                     pmdec=float(p[3] * 1000.0), pmra_err=float(np.sqrt(cov[2, 2]) * 1000.0),
                     pmdec_err=float(np.sqrt(cov[3, 3]) * 1000.0), parallax=float(plx), parallax_err=float(plx_e),
                     k_e=ke, k_n=kn, free=free, n_visits=n_vis, chi2_nu=chi2_nu, visit_chi2_nu=float(chi_v[0]),
                     visit_dof=int(dof_v[0]), wobble_arcsec=float(wob[0]),
                     unsteady=bool(unsteady(chi_v)[0]))
