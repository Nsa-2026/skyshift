"""Step `calib`: the zone's own position and flux corrections, learned from stored data, applied wherever dots are loaded.

Positions, from member dots (members.parquet → dot rows, via motion_io) of sources matched to exactly one
Gaia DR3 star (crossids: gaia_n == 1, parallax < CALIB_MAX_PARALLAX_MAS), unsaturated (flags_peak &
CALIB_SAT_BITS == 0), Gaia moved to each dot's MJD by its proper motion:
1. Pixel phase, per detector. sep's windowed centroid pulls a dot towards the centre of its pixel (6.15″
   pixels undersample the 7.5″ PSF). With φ = x − round(x) (0-based, centres on integers) the measured −
   true offset along x is Δx(φx) = Σ_k s_k sin(2πkφx) + c_k cos(2πkφx), k = 1..CALIB_FOURIER_K, same for y,
   in pixels, fitted on S/N ≥ CALIB_PHASE_MIN_SNR. Sky residuals (east, north ″) become pixels through each
   photo's Jacobian (affine fit of all its dots' tangent-plane positions on x, y). Corrected position =
   (x − Δx, y − Δy) mapped back with the same Jacobian. Saturated dots (centroided on the wings) are not
   phase-corrected. Cross terms Δx(φy) and an S/N dependence were checked (calib.json `check`): negligible.
2. Per-photo distortion. After the phase correction, each photo's residual against Gaia is fitted with a
   polynomial in u = (x − 1020)/1020, v = (y − 1020)/1020 (east and north separately, one clipped refit).
   The order (0 offset, 1 affine, 2 quadratic) is chosen per zone by cross-validation: stars split in two
   halves, a higher order only if the held-out scatter drops by > CALIB_PHOTO_MIN_GAIN. Photos with too few
   stars fall back to a lower order or none. This includes the constant offset astrometry removes today
   (astrometry's frame correction then finds ≈ 0). Coefficients: results/<zone>/calib_photos.parquet.
3. pos_err, per detector, on the final residuals: robust per-axis scatter in S/N bins fitted as
   σ² = (a/snr)² + floor², so pulls scatter ≈ 1 at every S/N. Saturated: σ ≥ their own scatter.

Fluxes, against each source's own spectrum (spectra.compute on the raw member dots) and catalogues:
4. Flux pixel phase, per detector, for flux_ujy (r = 2 px aperture) and flux_psf_ujy (ePSF fit) separately:
   ln(flux / the source's smooth spectrum at the dot's wavelength) = Σ_k a_k cos(2πkφx) + b_k cos(2πkφy)
   + c cos(2πφx) cos(2πφy) (+ a constant, not applied); flux × exp(−model). The reference is a quadratic in
   ln λ over the source's channels in that detector (the dot itself weighs ~1/50).
5. Detector scale (aperture correction): one factor per detector putting r = 2 px fluxes on a total-flux
   scale consistent across detectors. Weighted least squares on (a) catalogue anchors: 2MASS J, H, Ks and
   CatWISE W1, W2 (AB) for F–K stars with smooth spectra (J − Ks, |W1 − W2| cuts), the SED interpolated
   log-log between bands at each dot within ±CALIB_AP_WINDOW_UM of a band centre; median ln(catalogue / flux)
   per (band, detector); (b) stitching: source spectra on both sides of each detector boundary (1.1, 1.6,
   2.4, 3.8, 4.4 µm) extrapolated to it, median ln ratio; (c) a weak prior ln f = 0. flux_psf gets the same
   factor × the median aperture/PSF ratio, so both are total fluxes. Errors scale with their fluxes.

Writes results/<zone>/calib.json (tables, constants, before/after numbers, stars used, date) and
calib_photos.parquet. Dot files are never rewritten: their ra/dec/pos_err/fluxes stay raw; the combining steps
(motion_io.member_dots, spectra.load_members) return corrected values when calib.json exists and exactly the
dot files' values when it doesn't. `link` keeps raw positions (its 4″ friends-of-friends and 3″ pairing are
far above the ≤ 0.6″ corrections).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from skyshift_core import tangent_deproject, tangent_offset_arcsec
from skyshift_core.astrometry import DAYS_PER_JYEAR

from . import config as C
from . import log
from .store import Store, results_rel

L = log.get("calib")
SCHEMA = 2
PHOTOS_REL = "calib_photos.parquet"
NPOLY = 6  # 1, u, v, u², uv, v²
_LOADED: dict[tuple[str, str], tuple[str, dict | None]] = {}


# --- the model ---------------------------------------------------------------------------------
def phase_of(x) -> np.ndarray:
    """φ = x − round(x) in [−0.5, 0.5] (0-based pixel coordinates: pixel centres on integers)."""
    x = np.asarray(x, np.float64)
    return x - np.round(x)


def fourier(phi: np.ndarray, k_max: int) -> np.ndarray:
    """(n, 2k) design matrix: sin(2πkφ) for k = 1..k_max, then cos(2πkφ)."""
    w = 2.0 * np.pi * np.asarray(phi, np.float64)[:, None] * np.arange(1, k_max + 1)[None, :]
    return np.hstack([np.sin(w), np.cos(w)])


def shift_px(coef: dict | None, phi: np.ndarray) -> np.ndarray:
    """Δ(φ) in pixels from {"sin": [...], "cos": [...]} (0 without a model)."""
    if not coef:
        return np.zeros(np.shape(phi))
    s, c = np.asarray(coef["sin"], np.float64), np.asarray(coef["cos"], np.float64)
    return fourier(np.ravel(phi), len(s)) @ np.r_[s, c]


def poly_basis(x, y, order: int = 2) -> np.ndarray:
    """(n, NPOLY) [1, u, v, u², uv, v²] with u = (x − 1020)/1020, v = (y − 1020)/1020; columns above `order` = 0."""
    u = (np.asarray(x, np.float64) - 1020.0) / 1020.0
    v = (np.asarray(y, np.float64) - 1020.0) / 1020.0
    B = np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], axis=-1)
    B[..., (1, 3, NPOLY)[min(max(order, 0), 2)]:] = 0.0
    return B


def flux_basis(px: np.ndarray, py: np.ndarray, k_max: int) -> np.ndarray:
    """(n, 2k + 1): cos(2πkφx), k = 1..K; cos(2πkφy); cos(2πφx)·cos(2πφy). Zero mean over uniform phase."""
    k = np.arange(1, k_max + 1)[None, :]
    cx = np.cos(2 * np.pi * np.asarray(px, np.float64)[:, None] * k)
    cy = np.cos(2 * np.pi * np.asarray(py, np.float64)[:, None] * k)
    return np.hstack([cx, cy, (cx[:, 0] * cy[:, 0])[:, None]])


def photo_jacobian(x, y, ra, dec) -> np.ndarray:
    """(2, 2) arcsec per pixel: rows (east, north), columns (x, y); an affine fit of the dots' tangent-plane
    positions about their median on (x, y). NaN with too few dots or too small a spread."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(ra) & np.isfinite(dec)
    nan = np.full((2, 2), np.nan)
    if ok.sum() < C.CALIB_JAC_MIN_DOTS:
        return nan
    x, y, ra, dec = x[ok], y[ok], ra[ok], dec[ok]
    if x.size > 20_000:  # plenty for six numbers
        step = x.size // 20_000 + 1
        x, y, ra, dec = x[::step], y[::step], ra[::step], dec[::step]
    if np.std(x) < C.CALIB_JAC_MIN_SPREAD_PX or np.std(y) < C.CALIB_JAC_MIN_SPREAD_PX:
        return nan
    e, n = tangent_offset_arcsec(np.median(ra), np.median(dec), ra, dec)
    A = np.c_[x - x.mean(), y - y.mean(), np.ones_like(x)]
    ce = np.linalg.lstsq(A, e, rcond=None)[0]
    cn = np.linalg.lstsq(A, n, rcond=None)[0]
    J = np.array([[ce[0], ce[1]], [cn[0], cn[1]]])
    return J if np.isfinite(J).all() and abs(np.linalg.det(J)) > 1e-6 else nan


def phase_offsets(cal: dict, det, x, y) -> tuple[np.ndarray, np.ndarray]:
    """(Δx, Δy) in pixels per dot from calib.json's phase tables (0 for detectors without one)."""
    det = np.asarray(det, np.int64)
    dx, dy = np.zeros(det.shape), np.zeros(det.shape)
    px, py = phase_of(x), phase_of(y)
    for k, tab in (cal.get("phase", {}).get("detectors") or {}).items():
        m = (det == int(k)) & np.isfinite(px) & np.isfinite(py)
        if m.any():
            dx[m] = shift_px(tab.get("x"), px[m])
            dy[m] = shift_px(tab.get("y"), py[m])
    return dx, dy


def pos_err_model(cal: dict, det, snr, saturated, pos_err) -> np.ndarray:
    """Calibrated 1σ per axis (″): sqrt((a/snr)² + floor²) per detector; saturated dots at least their
    detector's measured scatter; the raw value for detectors without a model or a NaN S/N."""
    det = np.asarray(det, np.int64)
    snr = np.asarray(snr, np.float64)
    out = np.asarray(pos_err, np.float64).copy()
    sat = np.asarray(saturated, bool)
    for k, m_ in (cal.get("pos_err", {}).get("detectors") or {}).items():
        if m_.get("a_arcsec") is None:
            continue
        m = (det == int(k)) & np.isfinite(snr) & (snr > 0)
        out[m] = np.hypot(m_["a_arcsec"] / snr[m], m_["floor_arcsec"])
        if m_.get("saturated_arcsec"):
            ms = m & sat
            out[ms] = np.maximum(out[ms], m_["saturated_arcsec"])
    return out


def photo_coefs(cal: dict, obs_id, detector) -> tuple[np.ndarray, np.ndarray]:
    """Per-photo distortion coefficients (len, NPOLY) east and north for the given photos (NaN rows: none)."""
    obs_id = np.asarray(obs_id).astype(str)
    detector = np.asarray(detector, np.int64)
    ce = np.full((obs_id.size, NPOLY), np.nan)
    cn = np.full((obs_id.size, NPOLY), np.nan)
    ph = cal.get("_photos")
    if ph is None or not len(ph) or not obs_id.size:
        return ce, cn
    key = pd.MultiIndex.from_arrays([ph["obs_id"].astype(str), ph["detector"].astype(np.int64)])
    pos = key.get_indexer(pd.MultiIndex.from_arrays([obs_id, detector]))
    ok = pos >= 0
    ce[ok] = ph[[f"ce{j}" for j in range(NPOLY)]].to_numpy(np.float64)[pos[ok]]
    cn[ok] = ph[[f"cn{j}" for j in range(NPOLY)]].to_numpy(np.float64)[pos[ok]]
    return ce, cn


def poly_offsets(ce: np.ndarray, cn: np.ndarray, x, y) -> tuple[np.ndarray, np.ndarray]:
    """Distortion (east, north ″) per dot; ce, cn (n, NPOLY) per dot or (NPOLY,) for all. NaN → 0."""
    B = poly_basis(x, y, 2)
    ce, cn = np.nan_to_num(np.asarray(ce, np.float64)), np.nan_to_num(np.asarray(cn, np.float64))
    return (B * ce).sum(-1), (B * cn).sum(-1)


def apply(cal: dict, det, x, y, ra, dec, snr, flags_peak, pos_err, J, poly_e=None, poly_n=None):
    """Corrected (ra, dec, pos_err_arcsec, phase_corrected) for dots of any photos. J: (n, 2, 2) per dot, or one
    (2, 2) for all; NaN J = no phase correction for that dot. poly_e, poly_n: the photo distortion at each dot
    (″, from poly_offsets), subtracted from every dot. Vectorised."""
    ra = np.asarray(ra, np.float64)
    dec = np.asarray(dec, np.float64)
    J = np.asarray(J, np.float64)
    sat = (np.nan_to_num(np.asarray(flags_peak, np.float64)).astype(np.int64) & C.CALIB_SAT_BITS) != 0
    dx, dy = phase_offsets(cal, det, x, y)
    de = J[..., 0, 0] * dx + J[..., 0, 1] * dy
    dn = J[..., 1, 0] * dx + J[..., 1, 1] * dy
    ok = np.isfinite(de) & np.isfinite(dn) & ~sat & ((dx != 0) | (dy != 0))
    de, dn = np.where(ok, de, 0.0), np.where(ok, dn, 0.0)
    if poly_e is not None:
        de = de + np.nan_to_num(np.asarray(poly_e, np.float64))
        dn = dn + np.nan_to_num(np.asarray(poly_n, np.float64))
    mv = (de != 0) | (dn != 0)
    ra2, dec2 = ra.copy(), dec.copy()
    if mv.any():
        ra2[mv], dec2[mv] = tangent_deproject(ra[mv], dec[mv], -de[mv], -dn[mv])
    err = pos_err_model(cal, det, snr, sat, pos_err)
    return ra2, dec2, err, ok


def flux_factor(cal: dict, det, x, y, kind: str = "ap") -> np.ndarray:
    """Multiplicative flux correction per dot: detector scale × exp(−phase model). kind 'ap' (flux_ujy,
    flux_err_ujy) or 'psf' (flux_psf_ujy, flux_psf_err_ujy). 1 where nothing was fitted."""
    det = np.asarray(det, np.int64)
    out = np.ones(det.shape)
    px, py = phase_of(x), phase_of(y)
    fin = np.isfinite(px) & np.isfinite(py)
    for k, tabs in (cal.get("flux_phase", {}).get("detectors") or {}).items():
        t = tabs.get(kind)
        m = (det == int(k)) & fin
        if t and m.any():
            out[m] *= np.exp(-(flux_basis(px[m], py[m], len(t["cos_x"])) @ np.r_[t["cos_x"], t["cos_y"], t["cross"]]))
    for k, f in ((cal.get("aperture") or {}).get(f"factor_{kind}") or {}).items():
        if f is not None:
            out[det == int(k)] *= f
    return out


def load(store: Store, zone: C.Zone) -> dict | None:
    """results/<zone>/calib.json (+ calib_photos.parquet as `_photos`), or None when the step hasn't run.
    Cached on calib.json's stamp (it is written last)."""
    rel = results_rel(zone.name, C.CALIB_REL)
    if not store.exists(rel):
        return None
    info = store.fs.info(store.path(rel))
    stamp = f"{info.get('size')}-{info.get('mtime', info.get('updated', info.get('generation')))}"
    key = (store.root, zone.name)
    if key in _LOADED and _LOADED[key][0] == stamp:
        return _LOADED[key][1]
    try:
        cal = store.read_json(rel)
    except Exception as e:  # noqa: BLE001
        L.warning("%s unreadable (%s): dots stay raw", rel, e)
        cal = None
    if cal is not None and cal.get("schema") != SCHEMA:
        L.warning("%s has schema %s, expected %s: dots stay raw (rerun calib)", rel, cal.get("schema"), SCHEMA)
        cal = None
    if cal is not None:
        prel = results_rel(zone.name, PHOTOS_REL)
        cal["_photos"] = store.read_parquet(prel) if store.exists(prel) else None
    _LOADED[key] = (stamp, cal)
    return cal


# --- learning: positions -----------------------------------------------------------------------
def rob(v: np.ndarray) -> float:
    v = np.asarray(v, np.float64)
    v = v[np.isfinite(v)]
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size else float("nan")


def rob2(e: np.ndarray, n: np.ndarray) -> float:
    """Per-axis robust scatter of two axes combined: √((σe² + σn²)/2)."""
    return float(np.sqrt((rob(e) ** 2 + rob(n) ** 2) / 2))


def gaia_residuals(d: pd.DataFrame, crossids: pd.DataFrame) -> pd.DataFrame:
    """Member dots of single-Gaia-match sources with (east, north) = dot − Gaia at the dot's MJD (″)."""
    x = crossids.set_index("source_id")
    x = x[(x["gaia_ref_id"].astype(str) != "") & (x["gaia_n"] == 1) & np.isfinite(x["gaia_ra"].to_numpy(np.float64))
          & ~(x["gaia_parallax"].to_numpy(np.float64) > C.CALIB_MAX_PARALLAX_MAS)]
    pos = x.index.get_indexer(d["source_id"].to_numpy(np.int64))
    m = (pos >= 0) & (d["photo"].to_numpy() >= 0) & np.isfinite(d["snr"].to_numpy(np.float64))
    s = d[m].reset_index(drop=True)
    g = x.iloc[pos[m]]
    e, n = tangent_offset_arcsec(g["gaia_ra"].to_numpy(np.float64), g["gaia_dec"].to_numpy(np.float64),
                                 s["ra"].to_numpy(np.float64), s["dec"].to_numpy(np.float64))
    dt = (s["mjd"].to_numpy(np.float64) - g["gaia_epoch_mjd"].to_numpy(np.float64)) / DAYS_PER_JYEAR
    s["e"] = e - np.nan_to_num(g["gaia_pmra"].to_numpy(np.float64)) * np.nan_to_num(dt) / 1000.0
    s["n"] = n - np.nan_to_num(g["gaia_pmdec"].to_numpy(np.float64)) * np.nan_to_num(dt) / 1000.0
    s["sat"] = (s["flags_peak"].fillna(0).to_numpy().astype(np.int64) & C.CALIB_SAT_BITS) != 0
    return s


def remove_photo_offsets(photo: np.ndarray, e: np.ndarray, n: np.ndarray, use: np.ndarray, n_photos: int):
    """(e, n) minus each photo's median over `use` dots; NaN where the photo has < CALIB_FRAME_MIN_STARS."""
    from .spectra import group_median
    cnt = np.bincount(photo[use], minlength=n_photos)
    oe = group_median(photo[use], e[use], n_photos)
    on = group_median(photo[use], n[use], n_photos)
    bad = cnt < C.CALIB_FRAME_MIN_STARS
    oe[bad] = np.nan
    on[bad] = np.nan
    return e - oe[photo], n - on[photo]


def _wlsq(A: np.ndarray, y: np.ndarray, w: np.ndarray) -> np.ndarray:
    sw = np.sqrt(w)
    return np.linalg.lstsq(A * sw[:, None], y * sw, rcond=None)[0]


def fit_axis(phi: np.ndarray, r: np.ndarray, sig: np.ndarray, k_max: int, extra: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Weighted Fourier fit of residual r (px) on phase φ (+ optional extra columns), one clipped refit.
    Returns (coefficients [const, sin…, cos…, extra…], kept mask)."""
    A = np.c_[np.ones_like(phi), fourier(phi, k_max)]
    if extra is not None:
        A = np.c_[A, extra]
    return clipped_fit(A, r, sig)


def clipped_fit(A: np.ndarray, r: np.ndarray, sig: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    w = 1.0 / np.maximum(sig, 1e-3) ** 2
    keep = np.isfinite(r) & np.isfinite(w) & np.isfinite(A).all(axis=1)
    coef = np.zeros(A.shape[1])
    for _ in range(2):
        if keep.sum() <= A.shape[1]:
            return np.full(A.shape[1], np.nan), keep
        coef = _wlsq(A[keep], r[keep], w[keep])
        pull = (r - A @ coef) / np.maximum(sig, 1e-3)
        keep = keep & (np.abs(pull - np.median(pull[keep])) <= C.CALIB_CLIP_SIGMA * rob(pull[keep]))
    return coef, keep


def binned_median(phi: np.ndarray, r: np.ndarray, nb: int, lo: float = -0.5, hi: float = 0.5) -> list:
    edges = np.linspace(lo, hi, nb + 1)
    b = np.clip(np.digitize(phi, edges) - 1, 0, nb - 1)
    return [round(float(np.median(r[b == i])), 4) if (b == i).sum() >= 5 else None for i in range(nb)]


def fit_pos_err(sig_bins: list[dict]) -> tuple[float | None, float | None]:
    """σ² = a² · ⟨1/snr²⟩ + floor² over the S/N bins (weights n / σ⁴), both ≥ 0."""
    b = [x for x in sig_bins if x["n"] >= 30 and np.isfinite(x["scatter_corrected"])]
    if len(b) < 2:
        return None, None
    X = np.array([x["inv_snr2"] for x in b])
    Y = np.array([x["scatter_corrected"] ** 2 for x in b])
    W = np.array([x["n"] / x["scatter_corrected"] ** 4 for x in b])
    a2, f2 = _wlsq(np.c_[X, np.ones_like(X)], Y, W)
    if f2 < 0:
        a2, f2 = float(np.sum(W * X * Y) / np.sum(W * X * X)), 0.0
    if a2 < 0:
        a2, f2 = 0.0, float(np.sum(W * Y) / np.sum(W))
    return float(np.sqrt(a2)), float(np.sqrt(f2))


def fit_phase_tables(det, px, py, rx, ry, sig_px, use, sid, checks: dict | None = None) -> dict:
    """Per-detector Fourier tables of the pixel-phase bias (rx, ry: residuals in px with the model added back)."""
    K = C.CALIB_FOURIER_K
    tables: dict[str, dict] = {}
    grid = np.linspace(-0.5, 0.5, 101)
    mids = (np.arange(C.CALIB_PHASE_BINS) + 0.5) / C.CALIB_PHASE_BINS - 0.5
    for k in range(1, 7):
        m = (det == k) & use & np.isfinite(rx) & np.isfinite(ry)  # `use` carries the S/N cut
        if m.sum() < C.CALIB_MIN_STARS:
            continue
        cx, kx = fit_axis(px[m], rx[m], sig_px[m], K)
        cy, ky = fit_axis(py[m], ry[m], sig_px[m], K)
        if not (np.isfinite(cx).all() and np.isfinite(cy).all()):
            continue
        tab = {"x": {"sin": [round(float(v), 5) for v in cx[1:K + 1]], "cos": [round(float(v), 5) for v in cx[K + 1:]]},
               "y": {"sin": [round(float(v), 5) for v in cy[1:K + 1]], "cos": [round(float(v), 5) for v in cy[K + 1:]]},
               "const_px": [round(float(cx[0]), 4), round(float(cy[0]), 4)],
               "n_dots": int(m.sum()), "n_stars": int(np.unique(sid[m]).size),
               "n_clipped": [int(m.sum() - kx.sum()), int(m.sum() - ky.sum())],
               "rms_px": [round(float(np.std(shift_px({"sin": cx[1:K + 1], "cos": cx[K + 1:]}, grid))), 4),
                          round(float(np.std(shift_px({"sin": cy[1:K + 1], "cos": cy[K + 1:]}, grid))), 4)]}
        if checks is not None:
            ax = np.c_[np.ones(m.sum()), fourier(px[m], K)]
            ay = np.c_[np.ones(m.sum()), fourier(py[m], K)]
            cxx, _ = fit_axis(px[m], rx[m], sig_px[m], K, extra=fourier(py[m], K))
            cyy, _ = fit_axis(py[m], ry[m], sig_px[m], K, extra=fourier(px[m], K))
            rows = []
            bins = C.CALIB_CHECK_SNR_BINS
            for lo, hi in zip(bins[:-1], bins[1:]):
                mb = (det == k) & checks["_use"] & np.isfinite(rx) & np.isfinite(ry) & (checks["_snr"] >= lo) & (checks["_snr"] < hi)
                row = {"snr": [lo, hi if hi < 1e8 else None], "n": int(mb.sum())}
                if mb.sum() >= C.CALIB_MIN_STARS:
                    bx, _ = fit_axis(px[mb], rx[mb], sig_px[mb], K)
                    by, _ = fit_axis(py[mb], ry[mb], sig_px[mb], K)
                    row.update({"rms_x_px": round(float(np.std(fourier(grid, K) @ bx[1:])), 4),
                                "rms_y_px": round(float(np.std(fourier(grid, K) @ by[1:])), 4)})
                rows.append(row)
            tab["check"] = {
                "measured_x_px": binned_median(px[m], rx[m], C.CALIB_PHASE_BINS),
                "model_x_px": [round(float(v), 4) for v in shift_px(tab["x"], mids)],
                "measured_y_px": binned_median(py[m], ry[m], C.CALIB_PHASE_BINS),
                "model_y_px": [round(float(v), 4) for v in shift_px(tab["y"], mids)],
                "cross_terms": {
                    "scatter_px_without": [round(rob(rx[m] - ax @ cx), 4), round(rob(ry[m] - ay @ cy), 4)],
                    "scatter_px_with": [round(rob(rx[m] - np.c_[ax, fourier(py[m], K)] @ cxx), 4),
                                        round(rob(ry[m] - np.c_[ay, fourier(px[m], K)] @ cyy), 4)],
                    "amplitude_px": [round(float(np.std(fourier(py[m], K) @ cxx[2 * K + 1:])), 4),
                                     round(float(np.std(fourier(px[m], K) @ cyy[2 * K + 1:])), 4)]},
                "by_snr": rows}
        tables[str(k)] = tab
    return tables


def _half(sid: np.ndarray) -> np.ndarray:
    """Deterministic 50/50 split of sources (source ids are cell × 100 + rank: hash them)."""
    h = (np.asarray(sid, np.int64).astype(np.uint64) * np.uint64(0x9E3779B97F4A7C15)) >> np.uint64(40)
    return (h & np.uint64(1)).astype(bool)


def fit_photo_poly(photo, x, y, e, n, sig, use, order: int, n_photos: int):
    """Per-photo polynomial (east, north ″) on (x, y); lower order where stars are few.
    Returns CE, CN (P, NPOLY) with NaN rows for photos without a fit, and the order used per photo (−1 none)."""
    CE = np.full((n_photos, NPOLY), np.nan)
    CN = np.full((n_photos, NPOLY), np.nan)
    used = np.full(n_photos, -1, np.int8)
    rows = np.flatnonzero(use)
    idx = pd.Series(rows).groupby(photo[use]).indices
    for p, ii in idx.items():
        ii = rows[ii]
        o = order
        while o >= 0 and ii.size < C.CALIB_PHOTO_MIN_STARS[o]:
            o -= 1
        if o < 0:
            continue
        A = poly_basis(x[ii], y[ii], o)[:, : (1, 3, 6)[o]]
        ce, _ = clipped_fit(A, e[ii], sig[ii])
        cn, _ = clipped_fit(A, n[ii], sig[ii])
        if not (np.isfinite(ce).all() and np.isfinite(cn).all()):
            continue
        CE[p] = 0.0
        CN[p] = 0.0
        CE[p, : ce.size] = ce
        CN[p, : cn.size] = cn
        used[p] = o
    return CE, CN, used


def choose_photo_order(photo, x, y, e, n, sig, use, sid, n_photos: int) -> tuple[int, list[dict]]:
    """Cross-validated per-photo polynomial order: fit on half the stars, robust scatter on the other half."""
    half = _half(sid)
    rows = []
    min_n = 2 * C.CALIB_PHOTO_MIN_STARS[C.CALIB_PHOTO_MAX_ORDER]
    cnt = np.bincount(photo[use], minlength=n_photos)
    ok_photo = cnt >= min_n  # the same photos for every order
    sel = use & ok_photo[photo]
    for o in range(C.CALIB_PHOTO_MAX_ORDER + 1):
        CE, CN, used = fit_photo_poly(photo, x, y, e, n, sig, sel & half, o, n_photos)
        te = sel & ~half & (used[photo] == o)
        pe, pn = poly_offsets(CE[photo[te]], CN[photo[te]], x[te], y[te])
        rows.append({"order": o, "photos": int((used == o).sum()), "dots": int(te.sum()),
                     "heldout_scatter_east_north": [round(rob(e[te] - pe), 4), round(rob(n[te] - pn), 4)],
                     "heldout_scatter": round(rob2(e[te] - pe, n[te] - pn), 4)})
    best = 0
    for o in range(1, len(rows)):
        if rows[o]["dots"] and rows[o]["heldout_scatter"] < rows[best]["heldout_scatter"] * (1 - C.CALIB_PHOTO_MIN_GAIN):
            best = o
    return best, rows


# --- learning: fluxes --------------------------------------------------------------------------
def reference_spectra(spec: pd.DataFrame) -> tuple[pd.Index, np.ndarray, np.ndarray]:
    """Per source and detector a quadratic in ln λ through ln flux of its channels (spectra.compute output).
    Returns (source_id index, coefficients (N, 6, 3), centre ln λ per detector (6,)); NaN where < N channels."""
    from .spectra import channels
    cen = np.log([c["um_centre"] for c in channels()])
    F = np.stack(spec["flux_ujy"].to_numpy()).astype(np.float64) if len(spec) else np.zeros((0, 102))
    E = np.stack(spec["err_ujy"].to_numpy()).astype(np.float64) if len(spec) else np.zeros((0, 102))
    N = len(spec)
    coef = np.full((N, 6, 3), np.nan)
    lm = np.array([cen[k * 17:(k + 1) * 17].mean() for k in range(6)])
    for k in range(6):
        sl = slice(k * 17, (k + 1) * 17)
        f, e, ll = F[:, sl], E[:, sl], cen[sl] - lm[k]
        ok = np.isfinite(f) & (f > 0) & np.isfinite(e) & (e > 0)
        y = np.log(np.where(ok, f, 1.0))
        w = np.where(ok, np.minimum((np.where(ok, f, 0) / np.where(ok, e, 1)) ** 2, 2500.0), 0.0)  # ≤ 2% per channel
        A = np.stack([np.ones_like(ll), ll, ll * ll], 1)
        M = np.einsum("nc,ci,cj->nij", w, A, A)
        b = np.einsum("nc,ci,nc->ni", w, A, y)
        good = ok.sum(1) >= C.CALIB_FLUX_REF_MIN_CHANNELS
        if good.any():
            coef[good, k] = np.linalg.solve(M[good], b[good][..., None])[..., 0]
    return pd.Index(spec["source_id"].to_numpy(np.int64)), coef, lm


def reference_at(ref, source_id, det, wl) -> np.ndarray:
    idx, coef, lm = ref
    pos = idx.get_indexer(np.asarray(source_id, np.int64))
    det = np.asarray(det, np.int64)
    out = np.full(pos.size, np.nan)
    ok = (pos >= 0) & (det >= 1) & (det <= 6)
    c = coef[pos[ok], det[ok] - 1]
    ll = np.log(np.asarray(wl, np.float64)[ok]) - lm[det[ok] - 1]
    out[ok] = np.exp(c[:, 0] + c[:, 1] * ll + c[:, 2] * ll * ll)
    return out


def fit_flux_phase(det, px, py, r: np.ndarray, use: np.ndarray) -> dict:
    """Per detector: ln(flux/reference) = const + flux_basis(φx, φy) @ coef. r = that log ratio per dot."""
    K = C.CALIB_FLUX_K
    out: dict[str, dict] = {}
    rad_edges = [0, 0.15, 0.3, 0.45, 0.55, 0.71]
    for k in range(1, 7):
        m = (det == k) & use & np.isfinite(r)
        if m.sum() < C.CALIB_MIN_STARS:
            continue
        A = np.c_[np.ones(m.sum()), flux_basis(px[m], py[m], K)]
        sig = np.full(m.sum(), max(rob(r[m]), 1e-3))
        c, keep = clipped_fit(A, r[m], sig)
        if not np.isfinite(c).all():
            continue
        model = A[:, 1:] @ c[1:]
        rad = np.hypot(px[m], py[m])
        b = np.clip(np.digitize(rad, rad_edges) - 1, 0, len(rad_edges) - 2)
        out[str(k)] = {"cos_x": [round(float(v), 5) for v in c[1:K + 1]], "cos_y": [round(float(v), 5) for v in c[K + 1:2 * K + 1]],
                       "cross": round(float(c[-1]), 5), "const": round(float(c[0]), 4), "n_dots": int(m.sum()),
                       "rms": round(float(np.std(model)), 4),
                       "radial_phase_edges": rad_edges,
                       "ratio_by_radial_phase_before": [round(float(np.exp(np.median(r[m][b == i]))), 4) if (b == i).sum() >= 10 else None for i in range(len(rad_edges) - 1)],
                       "ratio_by_radial_phase_after": [round(float(np.exp(np.median((r[m] - model)[b == i]))), 4) if (b == i).sum() >= 10 else None for i in range(len(rad_edges) - 1)],
                       "scatter_before": round(rob(r[m][keep]), 4), "scatter_after": round(rob((r[m] - model)[keep]), 4)}
    return out


def catalogue_sed(crossids: pd.DataFrame) -> tuple[pd.Index, np.ndarray, np.ndarray]:
    """Stars with smooth near-IR spectra (F–K by J − Ks, no W1 − W2 excess, single matches): ln AB flux (µJy)
    at the band centres (N, nbands) and ln λ of the bands."""
    x = crossids
    need = {"j", "k", "w1", "w2", "tmass_n", "catwise_n"}
    if not need <= set(x.columns):
        return pd.Index([], dtype=np.int64), np.zeros((0, len(C.CALIB_AP_BANDS))), np.zeros(len(C.CALIB_AP_BANDS))
    jk = x["j"] - x["k"]
    w12 = x["w1"] - x["w2"]
    m = ((x.get("gaia_n", 1) <= 1) & (x["tmass_n"] == 1) & (x["catwise_n"] == 1)
         & (jk > C.CALIB_AP_STAR_JK[0]) & (jk < C.CALIB_AP_STAR_JK[1]) & (w12.abs() < C.CALIB_AP_STAR_W12_MAX)
         & (x["j"] > C.CALIB_AP_STAR_J[0]) & (x["j"] < C.CALIB_AP_STAR_J[1]) & (x["w1"] > C.CALIB_AP_STAR_W1_MIN))
    x = x[m.to_numpy()]
    bands = C.CALIB_AP_BANDS
    mags = np.stack([x[col].to_numpy(np.float64) + off for (_, col, off) in bands.values()], 1)
    lnf = (23.9 - mags) / 2.5 * np.log(10.0)
    ok = np.isfinite(lnf).all(1)
    return (pd.Index(x["source_id"].to_numpy(np.int64)[ok]), lnf[ok],
            np.log([c for (c, _, _) in bands.values()]))


def anchors(sed, source_id, det, wl, flux, flux_psf, use) -> list[dict]:
    """Median ln(catalogue / flux) per (band, detector) for dots within the window of a band centre."""
    idx, lnf, lam = sed
    pos = idx.get_indexer(np.asarray(source_id, np.int64))
    m = use & (pos >= 0) & np.isfinite(flux) & (flux > 0)
    out = []
    if not m.any():
        return out
    ll = np.log(np.asarray(wl, np.float64)[m])
    i = np.clip(np.searchsorted(lam, ll) - 1, 0, lam.size - 2)
    t = (ll - lam[i]) / (lam[i + 1] - lam[i])
    p = pos[m]
    cat = lnf[p, i] * (1 - t) + lnf[p, i + 1] * t
    r = cat - np.log(flux[m])
    with np.errstate(invalid="ignore", divide="ignore"):
        rp = cat - np.log(flux_psf[m])
    wlm, dm, sid = np.asarray(wl)[m], np.asarray(det)[m], np.asarray(source_id)[m]
    for name, (c, _, _) in C.CALIB_AP_BANDS.items():
        near = np.abs(wlm - c) < C.CALIB_AP_WINDOW_UM
        for k in range(1, 7):
            q = near & (dm == k)
            if q.sum() >= C.CALIB_AP_MIN_DOTS:
                out.append({"band": name, "detector": k, "n": int(q.sum()), "n_stars": int(np.unique(sid[q]).size),
                            "ln_ratio": round(float(np.median(r[q])), 4),
                            "ratio_ap": round(float(np.exp(np.median(r[q]))), 4),
                            "ratio_psf": round(float(np.exp(np.nanmedian(rp[q]))), 4) if np.isfinite(rp[q]).any() else None})
    return out


def stitches(spec: pd.DataFrame) -> list[dict]:
    """At each detector boundary: median over smooth (non-variable, unsaturated) sources of ln(f_upper / f_lower),
    each side's last/first CALIB_AP_STITCH_CHANNELS channels extrapolated linearly in ln λ to the boundary."""
    from .spectra import channels
    if not len(spec):
        return []
    keep = ~spec["variable"].to_numpy(bool) & ~spec["saturated"].to_numpy(bool)
    F = np.stack(spec["flux_ujy"].to_numpy()[keep]).astype(np.float64) if keep.any() else np.zeros((0, 102))
    cen = np.log([c["um_centre"] for c in channels()])
    nc = C.CALIB_AP_STITCH_CHANNELS
    out = []
    for b in range(5):
        lo = np.arange((b + 1) * 17 - nc, (b + 1) * 17)
        hi = np.arange((b + 1) * 17, (b + 1) * 17 + nc)
        lb = 0.5 * (cen[lo[-1]] + cen[hi[0]])
        with np.errstate(invalid="ignore", divide="ignore"):
            fl, fh = np.log(F[:, lo]), np.log(F[:, hi])
        ok = np.isfinite(fl).all(1) & np.isfinite(fh).all(1)
        if ok.sum() < C.CALIB_MIN_STARS:
            out.append({"boundary": [b + 1, b + 2], "n": int(ok.sum())})
            continue
        ext = []
        for f, ch in ((fl, lo), (fh, hi)):
            A = np.c_[np.ones(nc), cen[ch] - lb]
            ext.append(np.linalg.lstsq(A, f[ok].T, rcond=None)[0][0])
        dlt = ext[1] - ext[0]
        out.append({"boundary": [b + 1, b + 2], "um": round(float(np.exp(lb)), 3), "n": int(ok.sum()),
                    "ln_upper_over_lower": round(float(np.median(dlt)), 4), "scatter": round(rob(dlt), 4)})
    return out


def solve_factors(anc: list[dict], sti: list[dict]) -> tuple[np.ndarray, list[dict]]:
    """Weighted least squares for ln f_d (d = 1..6): anchors ln f_d = ln(cat/flux); stitches
    ln f_{d+1} − ln f_d = −ln(f_upper/f_lower); prior ln f_d = 0. Returns ln f and each constraint's residual."""
    rows, rhs, sig, what = [], [], [], []
    for a in anc:
        r = np.zeros(6)
        r[a["detector"] - 1] = 1
        rows.append(r), rhs.append(a["ln_ratio"]), sig.append(C.CALIB_AP_ANCHOR_SIGMA), what.append(("anchor", a))
    for s in sti:
        if "ln_upper_over_lower" not in s:
            continue
        r = np.zeros(6)
        r[s["boundary"][1] - 1], r[s["boundary"][0] - 1] = 1, -1
        rows.append(r), rhs.append(-s["ln_upper_over_lower"]), sig.append(C.CALIB_AP_STITCH_SIGMA), what.append(("stitch", s))
    for k in range(6):
        r = np.zeros(6)
        r[k] = 1
        rows.append(r), rhs.append(0.0), sig.append(C.CALIB_AP_PRIOR_SIGMA), what.append(("prior", {"detector": k + 1}))
    A, y, w = np.array(rows), np.array(rhs), 1.0 / np.array(sig) ** 2
    lnf = _wlsq(A, y, w)
    res = []
    for (kind, item), a_, y_ in zip(what, A, y):
        if kind == "prior":
            continue
        res.append({"kind": kind, **({"band": item["band"], "detector": item["detector"]} if kind == "anchor"
                                     else {"boundary": item["boundary"]}),
                    "residual": round(float(a_ @ lnf - y_), 4)})
    return lnf, res


# --- the step ----------------------------------------------------------------------------------
def fit(d: pd.DataFrame, jac: np.ndarray, crossids: pd.DataFrame, spec: pd.DataFrame | None = None) -> tuple[dict, pd.DataFrame]:
    """Member dots (motion_io.member_dots_raw: + x, y, flags_peak, photo, flux_psf_ujy) and the photos' Jacobians
    (P, 2, 2) → (calib.json dictionary without zone/date, per-photo distortion table). `spec`: the sources'
    spectra from the same raw dots (spectra.compute); computed here when None."""
    s = gaia_residuals(d, crossids)
    P = max(len(jac), int(d["photo"].max()) + 1 if len(d) else 0)
    jac = np.concatenate([jac, np.full((P - len(jac), 2, 2), np.nan)]) if len(jac) < P else jac
    photo = s["photo"].to_numpy(np.int64)
    J = jac[photo].astype(np.float64)
    hasJ = np.isfinite(J).all(axis=(1, 2))
    Ji = np.full_like(J, np.nan)
    Ji[hasJ] = np.linalg.inv(J[hasJ])
    snr = s["snr"].to_numpy(np.float64)
    det = s["detector"].to_numpy(np.int64)
    sat = s["sat"].to_numpy(bool)
    sid = s["source_id"].to_numpy(np.int64)
    xs, ys = s["x"].to_numpy(np.float64), s["y"].to_numpy(np.float64)
    e0, n0 = s["e"].to_numpy(np.float64), s["n"].to_numpy(np.float64)
    px, py = phase_of(xs), phase_of(ys)
    scale = np.sqrt(np.abs(np.linalg.det(J)))  # ″ per pixel
    good = ~sat & (snr >= C.CALIB_ERR_MIN_SNR)
    frame_use = good & (snr >= C.CALIB_FRAME_MIN_SNR)
    hi = good & hasJ & (snr >= C.CALIB_PHASE_MIN_SNR)
    raw_e, raw_n = remove_photo_offsets(photo, e0, n0, frame_use, P)
    sig_raw = s["pos_err_arcsec"].to_numpy(np.float64)
    sig_px = sig_raw / np.where(scale > 0, scale, np.nan)
    sig_fit = np.hypot(5.0 / snr, 0.15)  # ″, roughly the calibrated error: weights only

    def corrected(tables):
        dx, dy = phase_offsets({"phase": {"detectors": tables}}, det, xs, ys)
        de = np.where(hasJ & ~sat, J[:, 0, 0] * dx + J[:, 0, 1] * dy, 0.0)
        dn = np.where(hasJ & ~sat, J[:, 1, 0] * dx + J[:, 1, 1] * dy, 0.0)
        return e0 - de, n0 - dn, dx, dy

    def to_px(e, n, dx, dy):
        return Ji[:, 0, 0] * e + Ji[:, 0, 1] * n + dx, Ji[:, 1, 0] * e + Ji[:, 1, 1] * n + dy

    # pass 1: phase on the offset-removed residuals
    rx, ry = to_px(raw_e, raw_n, 0.0, 0.0)
    tables = fit_phase_tables(det, px, py, rx, ry, sig_px, hi, sid)
    e1, n1, dx, dy = corrected(tables)
    # per-photo distortion: order by cross-validation, then fit
    order, cv = choose_photo_order(photo, xs, ys, e1, n1, sig_fit, frame_use, sid, P)
    CE, CN, used = fit_photo_poly(photo, xs, ys, e1, n1, sig_fit, frame_use, order, P)
    pe, pn = poly_offsets(CE[photo], CN[photo], xs, ys)
    # pass 2: phase on the distortion-corrected residuals (photos without a fit: offset removed)
    nofit = ~np.isfinite(CE[photo]).all(1)
    re_, rn_ = e1 - pe, n1 - pn
    oe, on = remove_photo_offsets(photo, e1, n1, frame_use, P)
    re_, rn_ = np.where(nofit, oe, re_), np.where(nofit, on, rn_)
    rx, ry = to_px(re_, rn_, dx, dy)
    tables = fit_phase_tables(det, px, py, rx, ry, sig_px, hi, sid, checks={"_snr": snr, "_use": good & hasJ})
    e1, n1, dx, dy = corrected(tables)
    CE, CN, used = fit_photo_poly(photo, xs, ys, e1, n1, sig_fit, frame_use, order, P)
    pe, pn = poly_offsets(CE[photo], CN[photo], xs, ys)
    nofit = ~np.isfinite(CE[photo]).all(1)
    oe, on = remove_photo_offsets(photo, e1, n1, frame_use, P)
    ce, cn = np.where(nofit, oe, e1 - pe), np.where(nofit, on, n1 - pn)  # final residuals
    phase_only_e, phase_only_n = oe, on

    # pos_err on the final residuals
    perr: dict[str, dict] = {}
    summary: dict[str, dict] = {}
    bins = C.CALIB_ERR_SNR_BINS
    for k in range(1, 7):
        mk = (det == k) & good & np.isfinite(ce) & np.isfinite(cn) & np.isfinite(raw_e)
        if mk.sum() < C.CALIB_MIN_STARS:
            continue
        rows = []
        for lo, hi_ in zip(bins[:-1], bins[1:]):
            m = mk & (snr >= lo) & (snr < hi_)
            row = {"snr": [lo, hi_ if hi_ < 1e8 else None], "n": int(m.sum()), "scatter_corrected": float("nan")}
            if m.sum() >= 30:
                row.update({"snr_median": round(float(np.median(snr[m])), 1), "inv_snr2": float(np.mean(1.0 / snr[m] ** 2)),
                            "scatter_raw": rob2(raw_e[m], raw_n[m]), "scatter_corrected": rob2(ce[m], cn[m]),
                            "pos_err_raw_median": float(np.median(sig_raw[m]))})
            rows.append(row)
        a, f = fit_pos_err(rows)
        if a is None:
            continue
        model = {"a_arcsec": round(a, 4), "floor_arcsec": round(f, 4)}
        ms = (det == k) & sat & np.isfinite(raw_e)
        model["n_saturated"] = int(ms.sum())
        if ms.sum() >= 30:  # saturated: no phase correction; distortion-corrected scatter against Gaia
            model["saturated_arcsec"] = round(rob2(ce[ms], cn[ms]), 4)
        sig_cal = np.hypot(a / snr, f)
        for row in rows:
            m = mk & (snr >= row["snr"][0]) & (snr < (row["snr"][1] or np.inf))
            if row["n"] >= 30:
                row["pull_raw"] = round(rob2(raw_e[m] / sig_raw[m], raw_n[m] / sig_raw[m]), 3)
                row["pull_corrected"] = round(rob2(ce[m] / sig_cal[m], cn[m] / sig_cal[m]), 3)
                row["pos_err_calibrated"] = round(float(np.median(sig_cal[m])), 4)
                for c in ("scatter_raw", "scatter_corrected", "pos_err_raw_median"):
                    row[c] = round(row[c], 4)
                row.pop("inv_snr2")
        model["bins"] = rows
        perr[str(k)] = model
        h = mk & (snr >= C.CALIB_PHASE_MIN_SNR)
        summary[str(k)] = {
            "n_dots": int(mk.sum()), "n_stars": int(np.unique(sid[mk]).size),
            f"snr_ge_{int(C.CALIB_PHASE_MIN_SNR)}": {
                "n": int(h.sum()),
                "scatter_raw_east_north": [round(rob(raw_e[h]), 4), round(rob(raw_n[h]), 4)],
                "scatter_phase_east_north": [round(rob(phase_only_e[h]), 4), round(rob(phase_only_n[h]), 4)],
                "scatter_corrected_east_north": [round(rob(ce[h]), 4), round(rob(cn[h]), 4)]},
            "pull_raw": round(rob2(raw_e[mk] / sig_raw[mk], raw_n[mk] / sig_raw[mk]), 3),
            "pull_corrected": round(rob2(ce[mk] / sig_cal[mk], cn[mk] / sig_cal[mk]), 3)}

    # per-photo table
    keys = d.drop_duplicates("photo")[["photo", "obs_id", "detector"]]
    keys = keys[(keys["photo"] >= 0) & (used[keys["photo"].to_numpy()] >= 0)].sort_values("photo")
    pp = keys["photo"].to_numpy(np.int64)
    photos = pd.DataFrame({"obs_id": keys["obs_id"].astype(str).to_numpy(), "detector": keys["detector"].to_numpy(np.int8),
                           "order": used[pp], "n_stars": np.bincount(photo[frame_use], minlength=P)[pp].astype(np.int32)})
    for j in range(NPOLY):
        photos[f"ce{j}"] = CE[pp, j]
        photos[f"cn{j}"] = CN[pp, j]
    for name, (ee, nn) in (("phase", (phase_only_e, phase_only_n)), ("corrected", (ce, cn))):
        h = good & (snr >= C.CALIB_FRAME_MIN_SNR) & np.isfinite(ee)
        g = pd.DataFrame({"p": photo[h], "e": ee[h], "n": nn[h]}).groupby("p")
        photos[f"scatter_{name}"] = g.apply(lambda z: rob2(z["e"].to_numpy(), z["n"].to_numpy()), include_groups=False).reindex(pp).to_numpy(np.float32)
    distortion = {
        "model": "east, north (″) = Σ c_j · [1, u, v, u², uv, v²]_j, u = (x − 1020)/1020, v = (y − 1020)/1020; subtracted",
        "order": order, "cross_validation": cv, "file": PHOTOS_REL,
        "photos_by_order": {str(o): int((used == o).sum()) for o in range(-1, C.CALIB_PHOTO_MAX_ORDER + 1)},
        "offset_median_arcsec": round(float(np.nanmedian(np.hypot(CE[used >= 0, 0], CN[used >= 0, 0]))), 4) if (used >= 0).any() else None,
        "scatter_photo_median_phase_only": round(float(np.nanmedian(photos["scatter_phase"])), 4) if len(photos) else None,
        "scatter_photo_median_corrected": round(float(np.nanmedian(photos["scatter_corrected"])), 4) if len(photos) else None}

    # fluxes
    flux_phase, aperture = fit_fluxes(d, crossids, spec)

    return {
        "schema": SCHEMA,
        "phase": {"model": "measured − true = Σ_k s_k sin(2πkφ) + c_k cos(2πkφ), k = 1..K, pixels; φ = x − round(x), 0-based",
                  "apply": "x' = x − Δx(φx), y' = y − Δy(φy), mapped to the sky with the photo's Jacobian; saturated dots not corrected",
                  "k_max": C.CALIB_FOURIER_K, "min_snr": C.CALIB_PHASE_MIN_SNR, "detectors": tables},
        "distortion": distortion,
        "pos_err": {"model": "pos_err_arcsec = sqrt((a / snr)² + floor²), per axis; saturated: max(model, saturated_arcsec)",
                    "min_snr": C.CALIB_ERR_MIN_SNR, "detectors": perr},
        "flux_phase": flux_phase,
        "aperture": aperture,
        "summary": summary,
        "selection": {"dots_matched": int(len(s)), "dots_with_jacobian": int(hasJ.sum()),
                      "dots_used": int((good & np.isfinite(ce)).sum()), "saturated_dots": int(sat.sum()),
                      "stars": int(np.unique(sid[good & np.isfinite(ce)]).size),
                      "rules": "crossids gaia_n == 1, parallax < %g mas; flags_peak & 0x%X == 0; photo fits from dots with S/N ≥ %g"
                               % (C.CALIB_MAX_PARALLAX_MAS, C.CALIB_SAT_BITS, C.CALIB_FRAME_MIN_SNR)},
    }, photos


def fit_fluxes(d: pd.DataFrame, crossids: pd.DataFrame, spec: pd.DataFrame | None) -> tuple[dict, dict]:
    """Flux pixel phase (ap and psf) and the per-detector scale. Empty dicts when there is too little to fit."""
    from .spectra import compute
    if spec is None:
        spec = compute(d) if len(d) else pd.DataFrame()
    fp: dict = {"model": "ln(flux / source spectrum) = const + Σ_k a_k cos(2πkφx) + b_k cos(2πkφy) + c cos(2πφx)cos(2πφy); flux × exp(−(model − const))",
                "k_max": C.CALIB_FLUX_K, "min_snr": C.CALIB_FLUX_MIN_SNR, "detectors": {}}
    ap: dict = {"model": "flux_ujy × factor_ap[d]; flux_psf_ujy × factor_psf[d] (after the flux phase correction); errors alike"}
    if not len(spec) or not len(d):
        return fp, ap
    ref = reference_spectra(spec)
    sid = d["source_id"].to_numpy(np.int64)
    det = d["detector"].to_numpy(np.int64)
    wl = d["wavelength_um"].to_numpy(np.float64)
    x, y = d["x"].to_numpy(np.float64), d["y"].to_numpy(np.float64)
    px, py = phase_of(x), phase_of(y)
    snr = d["snr"].to_numpy(np.float64)
    sat = (d["flags_peak"].to_numpy().astype(np.int64) & C.CALIB_SAT_BITS) != 0
    var = pd.Series(spec["variable"].to_numpy(bool), index=ref[0]).reindex(sid).fillna(True).to_numpy(bool)
    fl = d["flux_ujy"].to_numpy(np.float64)
    fpsf = d["flux_psf_ujy"].to_numpy(np.float64) if "flux_psf_ujy" in d else np.full(len(d), np.nan)
    use = ~sat & ~var & (snr >= C.CALIB_FLUX_MIN_SNR)
    refv = np.full(len(d), np.nan)
    refv[use] = reference_at(ref, sid[use], det[use], wl[use])
    with np.errstate(invalid="ignore", divide="ignore"):
        r_ap, r_psf = np.log(fl / refv), np.log(fpsf / refv)
    for name, r in (("ap", r_ap), ("psf", r_psf)):
        fin = use & np.isfinite(r)
        if fin.sum() < C.CALIB_MIN_STARS:
            continue
        ok = fin & (np.abs(r - np.median(r[fin])) < 0.3)
        for k, t in fit_flux_phase(det, px, py, r, ok).items():
            fp["detectors"].setdefault(k, {})[name] = t
    cal_tmp = {"flux_phase": fp}
    fap = fl * flux_factor(cal_tmp, det, x, y, "ap")
    fps = fpsf * flux_factor(cal_tmp, det, x, y, "psf")
    # detector scale
    sed = catalogue_sed(crossids)
    anc = anchors(sed, sid, det, wl, fap, fps, ~sat & (snr >= C.CALIB_AP_MIN_SNR))
    sti = stitches(spec)
    ap.update({"anchors": anc, "stitches": sti, "stars_with_sed": int(len(sed[0]))})
    if not anc:
        return fp, ap
    lnf, res = solve_factors(anc, sti)
    constrained = sorted({a["detector"] for a in anc} | {b for s_ in sti if "ln_upper_over_lower" in s_ for b in s_["boundary"]})
    # a detector nothing constrains: the mean of its constrained neighbours in the same arm (SWIR 1–3, MWIR 4–6), else 1
    interp = []
    for k in range(1, 7):
        if k in constrained:
            continue
        arm = range(1, 4) if k <= 3 else range(4, 7)
        nb = [j for j in (k - 1, k + 1) if j in arm and j in constrained]
        lnf[k - 1] = float(np.mean([lnf[j - 1] for j in nb])) if nb else 0.0
        interp.append(k)
    ap["factor_ap"] = {str(k + 1): round(float(np.exp(lnf[k])), 4) for k in range(6)}
    ap["residuals"] = res
    ap["constrained"] = constrained
    ap["interpolated"] = interp
    psf_over = {}
    for k in range(1, 7):
        m = (det == k) & ~sat & (snr >= C.CALIB_FLUX_MIN_SNR) & np.isfinite(fps) & (fps > 0) & np.isfinite(fap)
        psf_over[str(k)] = round(float(np.exp(np.median(np.log(fap[m] / fps[m])))), 4) if m.sum() >= C.CALIB_MIN_STARS else None
    ap["ap_over_psf"] = psf_over
    ap["factor_psf"] = {k: (round(ap["factor_ap"][k] * v, 4) if v is not None else None) for k, v in psf_over.items()}
    # after: the anchors again with the factors applied
    ap["anchors_after"] = [{"band": a["band"], "detector": a["detector"], "ratio_ap": round(float(np.exp(a["ln_ratio"] - lnf[a["detector"] - 1])), 4)}
                           for a in anc]
    return fp, ap


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    from .motion_io import member_dots_raw
    t0 = time.time()
    xid_rel = results_rel(zone.name, "crossids.parquet")
    if not store.exists(xid_rel):
        L.warning("calib %s: no crossids.parquet: nothing to learn from, dots stay raw", zone.name)
        return {"calib": "skipped: no crossids"}
    d, st, jac = member_dots_raw(store, zone, workers)
    cal, photos = fit(d, jac, store.read_parquet(xid_rel))
    cal.update({"zone": zone.name, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "photos_with_jacobian": int(np.isfinite(jac).all(axis=(1, 2)).sum()), "photos": int(len(jac))})
    if not cal["phase"]["detectors"] and not cal["pos_err"]["detectors"]:
        L.warning("calib %s: too few Gaia-matched dots (%d): no calib.json, dots stay raw", zone.name, cal["selection"]["dots_used"])
        return {"calib": "skipped: too few stars", **cal["selection"]}
    store.write_parquet(results_rel(zone.name, PHOTOS_REL), photos)
    store.write_json(results_rel(zone.name, C.CALIB_REL), cal)  # last: its existence means calib is done
    out = {"detectors_phase": sorted(cal["phase"]["detectors"]), "detectors_pos_err": sorted(cal["pos_err"]["detectors"]),
           "distortion_order": cal["distortion"]["order"], "photos_distortion": int(len(photos)),
           "factor_ap": cal["aperture"].get("factor_ap"), "factor_psf": cal["aperture"].get("factor_psf"),
           **cal["selection"], "wall_s": round(time.time() - t0, 1)}
    for k, v in cal["summary"].items():
        hs = v[f"snr_ge_{int(C.CALIB_PHASE_MIN_SNR)}"]
        pe = cal["pos_err"]["detectors"][k]
        L.info("calib %s D%s: S/N≥%g scatter E/N %.3f/%.3f″ → phase %.3f/%.3f″ → + distortion %.3f/%.3f″; pull %.2f → %.2f; "
               "pos_err = √((%.2f/snr)² + %.3f²)", zone.name, k, C.CALIB_PHASE_MIN_SNR, *hs["scatter_raw_east_north"],
               *hs["scatter_phase_east_north"], *hs["scatter_corrected_east_north"], v["pull_raw"], v["pull_corrected"],
               pe["a_arcsec"], pe["floor_arcsec"])
        out[f"D{k}"] = {"scatter_raw": hs["scatter_raw_east_north"], "scatter_corrected": hs["scatter_corrected_east_north"],
                        "pull_raw": v["pull_raw"], "pull_corrected": v["pull_corrected"]}
    L.info("calib %s: %s", zone.name, {k: v for k, v in out.items() if not k.startswith("D")})
    return out
