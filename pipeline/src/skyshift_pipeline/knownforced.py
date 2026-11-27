"""Known-body forced photometry (27 Nov, agent "known-forced"; config KNOWN_*, env SKYSHIFT_KNOWN_FORCED=1, off by
default): while a photo is in memory, the dots worker measures every known asteroid, comet, planet and moon that the
known list (knownlist.py) puts on it, whatever its brightness, at its predicted place. One photo alone finds a known
asteroid only to V ~19; these rows can be added up over the 55–60 exposures of a visit afterwards, without reading a
photo again.

- The list: knownlist.py's rows for the photo (<KNOWN_LIST_DIR>/<kind>/<period>.parquet, only its own row groups are
  read), geocentric at the index's photo time; stamps.topocentric moves each row to MJD-AVG and to SPHEREx (IMAGE
  header X_SC.., VX_SC..; the Earth's centre when they are missing: flag GEOCENTRE).
- Per row (ROW_COLUMNS): the prediction (ra, dec, x, y, rates, trail length, V, sigma), the forced ePSF flux at the
  predicted place (psf.psf_photometry on the hole-masked IMAGE: exactly the forced file's measurement) and the r = 2 px
  aperture on the background-subtracted image; for a trail longer than KNOWN_SMEAR_MIN_PX in the exposure, also the
  flux of the ePSF averaged along the predicted trail (same fit: F and a constant); the best matched-filter peak
  (the ePSF kernel of matched_filter, on the background-subtracted image with every source in it) within the search
  radius clip(KNOWN_SEARCH_SIGMAS × sigma, floor, cap) (KNOWN_SEARCH_COMET_ARCSEC without a sigma): its S/N, flux and
  offset, and the same filter's S/N at the predicted pixel; the local conditions: the share of the ePSF's light on
  masked pixels, FLAGS (3 × 3 OR), the distance to the nearest saturated pixel, the glow model's pixels, the nearest
  dot and the nearest forced-list star (distance, flux), the background, noise and ZODI.
- `known_flag` (KNOWN_FLAGS) says why a row is doubtful; nothing is dropped. A prediction off the frame by less than
  its search radius keeps its row (OFF_EDGE, fluxes NaN, the peak search over the pixels that exist).
- Storage: <folder>_known/<obs_id>D<det>.parquet, one row per body in the photo; the photo's summary in the parquet
  key-value metadata META_KEY. Never raises: an error goes to the dot file's footer `known.error` and the photo keeps
  every other file as it is.
"""

from __future__ import annotations

import io
import json
import os
import time

import numpy as np
import pandas as pd

from . import config as C
from . import log

L = log.get("knownforced")
META_KEY = "skyshift_known"
VERSION = 1

KNOWN_FLAGS = {
    "off_edge": 0x1,       # the prediction is outside the frame or within KNOWN_EDGE_PX of its edge: no forced flux
    "masked": 0x2,         # >= 10% of the ePSF's light at the prediction falls on masked pixels
    "sat_core": 0x4,       # an OVERFLOW/NONLINEAR pixel in the 3 × 3 around the prediction (saturated: body or star)
    "sat_guard": 0x8,      # ... within KNOWN_SAT_GUARD_PX (a saturated star's wings)
    "glow": 0x10,          # on the glow model's pixels (a bright star's halo, modelled and subtracted for detection)
    "dot_near": 0x20,      # a dot within KNOWN_BLEND_PX: the body itself (detected) or a neighbour
    "blend_star": 0x40,    # a forced-list star (Gaia G < 17.5 or red AllWISE) within KNOWN_BLEND_PX: blend warning
    "smeared": 0x80,       # trail > KNOWN_SMEAR_MIN_PX: flux_smear_* measured
    "smear_capped": 0x100,  # trail > KNOWN_SMEAR_MAX_PX: the smeared model covers its central KNOWN_SMEAR_MAX_PX only
    "no_sigma": 0x200,     # no position uncertainty: the search radius is KNOWN_SEARCH_COMET_ARCSEC
    "sigma_large": 0x400,  # KNOWN_SEARCH_SIGMAS × sigma exceeds the cap: the body may be outside the search radius
    "geocentre": 0x800,    # no SPHEREx position in the header: predicted from the Earth's centre
    "psf_fail": 0x1000,    # inside the frame but the ePSF fit had too few unmasked pixels (flux NaN)
    "no_glow_map": 0x2000,  # the worker had no glow map for this photo (glow unknown)
}

ROW_COLUMNS = {
    "obs_id": "str", "detector": "int8", "mjd": "float64",
    "kind": "str", "name": "str", "number": "int32", "cls": "str",
    "ra": "float64", "dec": "float64",                  # predicted at MJD-AVG, seen from SPHEREx (deg)
    "x": "float32", "y": "float32",                      # 0-based pixel of (ra, dec)
    "edge_px": "float32",                                # distance to the nearest frame edge (px; < 0 outside)
    "v_pred": "float32",                                 # V (asteroids H-G, planets/moons Horizons, comets MPC total mag)
    "rate_ra_arcsec_h": "float32", "rate_dec_arcsec_h": "float32",  # seen from SPHEREx (dRA·cos dec)
    "rate_arcsec_h": "float32", "pa_deg": "float32",
    "trail_px": "float32",                               # motion over the exposure (KNOWN_EXPOSURE_S or XPOSURE), px
    "sigma_arcsec": "float32", "search_arcsec": "float32",
    "delta_au": "float32", "r_au": "float32", "phase_deg": "float32", "parallax_arcsec": "float32",
    "wavelength_um": "float32", "bandwidth_um": "float32",
    "flux_psf_ujy": "float32", "flux_psf_err_ujy": "float32", "psf_chi2_nu": "float32", "psf_npix": "int16",
    "flux_ap2_ujy": "float32", "flux_ap2_err_ujy": "float32",
    "flux_smear_ujy": "float32", "flux_smear_err_ujy": "float32", "smear_chi2_nu": "float32",
    "snr_mf_pred": "float32",                            # matched-filter S/N at the predicted pixel
    "peak_snr": "float32", "peak_flux_ujy": "float32",   # best matched-filter peak within search_arcsec
    "peak_dra_arcsec": "float32", "peak_ddec_arcsec": "float32", "peak_sep_arcsec": "float32",
    "masked_frac": "float32",                            # share of the ePSF's light on masked pixels
    "flags": "int32",                                    # FLAGS, 3 × 3 OR at the predicted pixel
    "sat_dist_px": "float32",                            # nearest OVERFLOW/NONLINEAR pixel (NaN: none within 10 px)
    "dot_sep_arcsec": "float32", "dot_flux_ujy": "float32", "dot_snr": "float32",  # nearest dot (NaN: none in 10 px)
    "star_sep_arcsec": "float32", "star_flux_ujy": "float32",                       # nearest forced-list star
    "n_stars_3px": "int16",                              # forced-list stars within 3 px
    "bkg_mjysr": "float32", "noise_mjysr": "float32", "zodi_mjysr": "float32",
    "known_flag": "int32", "list_row": "int32",
}


# ---------------------------------------------------------------- the list

def list_root(store) -> str | None:
    d = C.KNOWN_LIST_DIR
    if not d:
        return None
    if os.path.isabs(d):
        return d if os.path.isdir(d) else None
    return d if store.exists(d) else None


def _local(store, rel: str) -> str | None:
    if os.path.isabs(rel):
        return rel if os.path.exists(rel) else None
    root = getattr(store, "root", "")
    if root and not str(root).startswith(("gs://", "s3://")):
        p = os.path.join(str(root), rel)
        return p if os.path.exists(p) else None
    return None


def photo_rows(store, obs_id: str, detector: int) -> tuple[pd.DataFrame | None, dict]:
    """The known list's rows of one photo (every kind), the list's info; (None, info) when there is no list."""
    from .knownlist import KNOWN_LIST_COLUMNS, read_photo
    from .stamplist import period_of
    root = list_root(store)
    if root is None:
        return None, {"list": "none"}
    period = period_of(obs_id)
    parts, info = [], {"list": root, "period": period, "kinds": {}}
    for kind in C.KNOWN_LIST_KINDS:
        rel = f"{root}/{kind}/{period}.parquet"
        path = _local(store, rel)
        if path is None:
            if store.exists(rel):  # a remote store: read the whole file (bytes) and filter
                import pyarrow.parquet as pq
                t = pq.read_table(io.BytesIO(store.read_bytes(rel)),
                                  filters=[("obs_id", "==", str(obs_id)), ("detector", "==", int(detector))])
                rows = t.to_pandas()
            else:
                info["kinds"][kind] = "missing"
                continue
        else:
            rows = read_photo(path, obs_id, detector)
        info["kinds"][kind] = int(len(rows))
        if len(rows):
            parts.append(rows)
    if not parts:
        return pd.DataFrame(columns=list(KNOWN_LIST_COLUMNS)), info
    out = pd.concat(parts, ignore_index=True)
    out["list_row"] = np.arange(len(out), dtype=np.int32)
    return out, info


# ---------------------------------------------------------------- measurements

def search_radius(sigma) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(radius ″, no_sigma, sigma_large) for each sigma (NaN: unknown)."""
    s = np.asarray(sigma, np.float64)
    known = np.isfinite(s) & (s >= 0)
    r = np.where(known, np.clip(C.KNOWN_SEARCH_SIGMAS * np.where(known, s, 0.0), C.KNOWN_SEARCH_FLOOR_ARCSEC,
                                C.KNOWN_SEARCH_CAP_ARCSEC), C.KNOWN_SEARCH_COMET_ARCSEC)
    return r, ~known, known & (C.KNOWN_SEARCH_SIGMAS * np.where(known, s, 0.0) > C.KNOWN_SEARCH_CAP_ARCSEC)


def fit_models(data: np.ndarray, P: np.ndarray, ix: np.ndarray, iy: np.ndarray, noise: np.ndarray, bad: np.ndarray,
               half: int, model_frac: float = C.PSF_MODEL_FRAC, min_pix: int = C.PSF_FIT_MIN_PIX):
    """psf._fit_chunk's fit (F × P + B, inverse variance, the model-shape error added) for given model stamps P
    (N, 2h+1, 2h+1) on pixels around (ix, iy): (flux, flux_err, chi2_nu, npix), flux in the image's units summed."""
    from .psf import gather
    D, inside = gather(data, ix, iy, half)
    S, _ = gather(noise, ix, iy, half)
    good = inside & np.isfinite(D) & np.isfinite(S) & (S > 0) & ~gather(bad, ix, iy, half, fill=None)[0]
    D = np.where(good, D, 0.0).astype(np.float64)
    var0 = np.where(good, S.astype(np.float64) ** 2, 1.0)
    n = good.sum(axis=(1, 2))

    def solve(w):
        Spp, Sp, S1 = (w * P * P).sum(axis=(1, 2)), (w * P).sum(axis=(1, 2)), w.sum(axis=(1, 2))
        Spd, Sd = (w * P * D).sum(axis=(1, 2)), (w * D).sum(axis=(1, 2))
        det = Spp * S1 - Sp * Sp
        with np.errstate(divide="ignore", invalid="ignore"):
            return (S1 * Spd - Sp * Sd) / det, (Spp * Sd - Sp * Spd) / det, S1 / det, det

    w = np.where(good, 1.0 / var0, 0.0)
    F, B, varF, det = solve(w)
    if model_frac > 0:
        Fp = np.where(np.isfinite(F), np.maximum(F, 0.0), 0.0)[:, None, None]
        w = np.where(good, 1.0 / (var0 + (model_frac * Fp * P) ** 2), 0.0)
        F, B, varF, det = solve(w)
    r = D - F[:, None, None] * P - B[:, None, None]
    chi2 = (w * r * r).sum(axis=(1, 2))
    ok = (n >= min_pix) & (det > 0) & np.isfinite(F)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (np.where(ok, F, np.nan), np.where(ok, np.sqrt(varF), np.nan),
                np.where(ok, chi2 / np.maximum(n - 2, 1), np.nan), n)


def smeared_fluxes(l2, ctx, x, y, x1, y1, x2, y2):
    """ePSF averaged along the segment (x1, y1)–(x2, y2) (capped at KNOWN_SMEAR_MAX_PX around (x, y)), fitted at its
    centre: (flux µJy, err µJy, chi2_nu, capped). One object at a time (the stamp grows with the trail)."""
    n = len(x)
    f, e, c = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    capped = np.zeros(n, bool)
    for k in range(n):
        dx, dy = x2[k] - x1[k], y2[k] - y1[k]
        L = float(np.hypot(dx, dy))
        if not np.isfinite(L):
            continue
        if L > C.KNOWN_SMEAR_MAX_PX:
            s = C.KNOWN_SMEAR_MAX_PX / L
            dx, dy, L, capped[k] = dx * s, dy * s, C.KNOWN_SMEAR_MAX_PX, True
        m = max(2, int(np.ceil(L / C.KNOWN_SMEAR_STEP_PX)) + 1)
        t = np.linspace(-0.5, 0.5, m)
        xs, ys = x[k] + t * dx, y[k] + t * dy
        half = C.PSF_FIT_HALF_PX + int(np.ceil(L / 2.0))
        ix, iy = int(np.rint(x[k])), int(np.rint(y[k]))
        P = l2.epsf.stamps_at(xs, ys, np.full(m, ix), np.full(m, iy), half)[0].mean(axis=0)[None]
        F, E, X, _ = fit_models(np.asarray(l2.data), P, np.array([ix]), np.array([iy]), ctx.noise, ctx.bad, half)
        f[k], e[k], c[k] = F[0] * ctx.conv, E[0] * ctx.conv, X[0]
    return f, e, c, capped


def mf_search(ctx, epsf, x, y, radius_px):
    """The matched filter's S/N (psf.matched_filter's kernel and weights, phase 0) on every pixel within radius_px of
    each (x, y), on ctx.sub (background-subtracted, every source in it): (S/N at the rounded prediction, best S/N,
    its x, y (parabolic sub-pixel offset), its flux in image units)."""
    from .psf import _weights, _zone_grid, gather, mf_kernel
    n = len(x)
    out = [np.full(n, np.nan) for _ in range(5)]
    if n == 0:
        return out
    h = C.MF_KERNEL_HALF
    R = int(np.ceil(np.nanmax(radius_px))) + 1
    w, sw = _weights(ctx.sub, ctx.noise, ctx.bad)
    ny, nx = w.shape
    ix = np.rint(x).astype(np.int64)
    iy = np.rint(y).astype(np.int64)
    rb, cb, zg = _zone_grid(epsf, (ny, nx))
    zone = zg[np.searchsorted(rb, np.clip(iy, 0, ny - 1), side="right") - 1,
              np.searchsorted(cb, np.clip(ix, 0, nx - 1), side="right") - 1]
    Ks = {int(z): mf_kernel(epsf, int(z), h, (0.0, 0.0)) for z in np.unique(zone)}
    K = np.stack([Ks[int(z)] for z in zone])
    S, _ = gather(sw, ix, iy, R + h, fill=0.0)
    W, _ = gather(w, ix, iy, R + h, fill=0.0)
    from numpy.lib.stride_tricks import sliding_window_view as swv
    m = 2 * h + 1
    num = np.einsum("nabij,nij->nab", swv(S, (m, m), axis=(1, 2)), K)
    den = np.einsum("nabij,nij->nab", swv(W, (m, m), axis=(1, 2)), K * K)
    with np.errstate(divide="ignore", invalid="ignore"):
        snr = np.where(den > 0, num / np.sqrt(den), np.nan)
        flux = np.where(den > 0, num / den, np.nan)
    g = np.arange(-R, R + 1)
    gy, gx = np.meshgrid(g, g, indexing="ij")
    # the grid is centred on the rounded pixel; the circle on the prediction itself
    ox, oy = (x - ix)[:, None, None], (y - iy)[:, None, None]
    inside = np.hypot(gx[None] - ox, gy[None] - oy) <= np.asarray(radius_px)[:, None, None]
    inside &= ((iy[:, None, None] + gy[None]) >= 0) & ((iy[:, None, None] + gy[None]) < ny) & \
        ((ix[:, None, None] + gx[None]) >= 0) & ((ix[:, None, None] + gx[None]) < nx)
    s_in = np.where(inside & np.isfinite(snr), snr, -np.inf)
    out[0] = np.where(np.isfinite(snr[:, R, R]), snr[:, R, R], np.nan)
    flat = s_in.reshape(n, -1)
    j = np.argmax(flat, axis=1)
    best = flat[np.arange(n), j]
    by, bx = np.unravel_index(j, s_in.shape[1:])
    ok = np.isfinite(best)

    def off(sm, s0, sp):
        """Sub-pixel offset of a peak from three samples: a Gaussian through them where all three are > 0 (the
        S/N map's peak is ~Gaussian; a parabola pulls 0.3 px to ~0.16), else a parabola; within ±0.5 px."""
        with np.errstate(divide="ignore", invalid="ignore"):
            pos = (sm > 0) & (s0 > 0) & (sp > 0)
            lm, l0, lp = (np.log(np.where(pos, v, 1.0)) for v in (sm, s0, sp))
            dl = lm - 2.0 * l0 + lp
            og = np.where(pos & (dl < 0), 0.5 * (lm - lp) / dl, np.nan)
            d = sm - 2.0 * s0 + sp
            op = np.where(np.isfinite(d) & (d < 0), 0.5 * (sm - sp) / d, 0.0)
        o = np.where(np.isfinite(og), og, op)
        return np.clip(np.nan_to_num(o), -0.5, 0.5)

    # the neighbours from the whole S/N map, not the search circle: a peak on the circle's rim keeps its offset
    idx = np.arange(n)
    pad = np.pad(snr, ((0, 0), (1, 1), (1, 1)), constant_values=np.nan)
    s0 = pad[idx, by + 1, bx + 1]
    sx = off(pad[idx, by + 1, bx], s0, pad[idx, by + 1, bx + 2])
    sy = off(pad[idx, by, bx + 1], s0, pad[idx, by + 2, bx + 1])
    out[1] = np.where(ok, best, np.nan)
    out[2] = np.where(ok, ix + (bx - R) + sx, np.nan)
    out[3] = np.where(ok, iy + (by - R) + sy, np.nan)
    out[4] = np.where(ok, flux[idx, by, bx], np.nan)
    return out


def _sat_dist(flags: np.ndarray, ix: np.ndarray, iy: np.ndarray, half: int = 10) -> np.ndarray:
    from skyshift_core import SAT_BITS

    from .psf import gather
    if len(ix) == 0:
        return np.zeros(0)
    F, inside = gather(np.asarray(flags), ix, iy, half, fill=None)
    sat = inside & ((F.astype(np.int64) & int(SAT_BITS)) != 0)
    g = np.arange(-half, half + 1)
    d = np.hypot(*np.meshgrid(g, g, indexing="ij"))
    dd = np.where(sat, d[None], np.inf).min(axis=(1, 2))
    return np.where(np.isfinite(dd), dd, np.nan)


def _nearest(tx, ty, x, y, r_px):
    """(distance px, index) of the nearest (tx, ty) within r_px of each (x, y); NaN / -1 when none."""
    from scipy.spatial import cKDTree
    n = len(x)
    if len(tx) == 0 or n == 0:
        return np.full(n, np.nan), np.full(n, -1, np.int64)
    ok = np.isfinite(tx) & np.isfinite(ty)
    keep = np.flatnonzero(ok)
    if keep.size == 0:
        return np.full(n, np.nan), np.full(n, -1, np.int64)
    tree = cKDTree(np.c_[tx[keep], ty[keep]])
    d, j = tree.query(np.c_[x, y], distance_upper_bound=r_px)
    found = np.isfinite(d)
    return np.where(found, d, np.nan), np.where(found, keep[np.minimum(j, keep.size - 1)], -1)


def _count_within(tx, ty, x, y, r_px) -> np.ndarray:
    from scipy.spatial import cKDTree
    ok = np.isfinite(tx) & np.isfinite(ty)
    if not ok.any() or len(x) == 0:
        return np.zeros(len(x), np.int64)
    tree = cKDTree(np.c_[tx[ok], ty[ok]])
    return np.array([len(v) for v in tree.query_ball_point(np.c_[x, y], r_px)], np.int64)


def measure(l2, ctx, extras: dict, rows: pd.DataFrame, dots: pd.DataFrame | None, forced: pd.DataFrame | None
            ) -> tuple[pd.DataFrame, dict]:
    """The known table of one photo (ROW_COLUMNS) and counts."""
    from skyshift_core import SAT_BITS, sky_to_pixel

    from .psf import psf_photometry
    from .sky import angsep, offset_radec
    from .stamps import spacecraft, topocentric

    info = {"rows_list": int(len(rows)), "off_photo": 0, "capped": 0}
    if rows is None or rows.empty:
        return empty_table(), info
    if len(rows) > C.KNOWN_MAX_PER_PHOTO:
        v = pd.to_numeric(rows["v_pred"], errors="coerce").fillna(99.0)
        rows = rows.iloc[np.argsort(v.to_numpy(), kind="stable")[:C.KNOWN_MAX_PER_PHOTO]]
        info["capped"] = int(info["rows_list"] - len(rows))
    rows = rows.reset_index(drop=True)
    sc_p, sc_v = spacecraft(l2.header)
    mjd = float(l2.mjd_avg)
    ra, dec, rra, rdec, par = topocentric(rows["ra"], rows["dec"], rows["dra_arcsec_h"], rows["ddec_arcsec_h"],
                                          rows["delta_au"], rows["mjd_ref"], mjd, sc_p, sc_v)
    data = np.asarray(l2.data)
    ny, nx = data.shape
    radius, no_sigma, sig_large = search_radius(rows["sigma_arcsec"].to_numpy(np.float64))
    r_px = radius / C.PIXEL_ARCSEC
    from skyshift_core import pixel_to_sky
    cra, cdec = pixel_to_sky(l2.wcs, [(nx - 1) / 2.0], [(ny - 1) / 2.0])
    near = angsep(cra[0], cdec[0], ra, dec) * 3600.0 <= (np.hypot(nx, ny) / 2.0 + r_px + 2) * C.PIXEL_ARCSEC * 1.2
    x, y = np.full(len(ra), np.nan), np.full(len(ra), np.nan)
    if near.any():
        x[near], y[near] = sky_to_pixel(l2.wcs, ra[near], dec[near])
    edge = np.minimum.reduce([x, y, nx - 1 - x, ny - 1 - y])
    keep = np.isfinite(edge) & (edge >= -r_px)
    info["off_photo"] = int((~keep).sum())
    sel = np.flatnonzero(keep)
    rows, ra, dec, rra, rdec, par = rows.iloc[sel].reset_index(drop=True), ra[sel], dec[sel], rra[sel], rdec[sel], par[sel]
    x, y, edge, radius, r_px = x[sel], y[sel], edge[sel], radius[sel], r_px[sel]
    no_sigma, sig_large = no_sigma[sel], sig_large[sel]
    n = len(rows)
    if n == 0:
        return empty_table(), info
    flag = np.zeros(n, np.int64)
    flag |= np.where(no_sigma, KNOWN_FLAGS["no_sigma"], 0) | np.where(sig_large, KNOWN_FLAGS["sigma_large"], 0)
    if sc_p is None:
        flag |= KNOWN_FLAGS["geocentre"]
    inner = edge >= C.KNOWN_EDGE_PX
    flag |= np.where(~inner, KNOWN_FLAGS["off_edge"], 0)
    ii = np.flatnonzero(inner)
    ixc = np.clip(np.rint(x).astype(np.int64), 0, nx - 1)
    iyc = np.clip(np.rint(y).astype(np.int64), 0, ny - 1)

    # forced ePSF flux and r = 2 px aperture (forced_table's measurement)
    fpsf, epsf_, chi, npx = (np.full(n, np.nan) for _ in range(4))
    f2, e2 = np.full(n, np.nan), np.full(n, np.nan)
    mfrac = np.full(n, np.nan)
    epsf = getattr(l2, "epsf", None)
    if ii.size and epsf is not None:
        ph = psf_photometry(data, x[ii], y[ii], epsf, noise=ctx.noise, bad=ctx.bad)
        fpsf[ii], epsf_[ii], chi[ii], npx[ii] = ph.flux * ctx.conv, ph.flux_err * ctx.conv, ph.chi2_nu, ph.npix
        P, pix, piy = epsf.stamps(x[ii], y[ii], C.PSF_FIT_HALF_PX)
        from .psf import gather
        B, ins = gather(ctx.bad, pix, piy, C.PSF_FIT_HALF_PX, fill=None)
        Bm = B | ~ins
        with np.errstate(invalid="ignore", divide="ignore"):
            mfrac[ii] = (P * Bm).sum(axis=(1, 2)) / P.sum(axis=(1, 2))
        flag[ii] |= np.where(~np.isfinite(fpsf[ii]), KNOWN_FLAGS["psf_fail"], 0)
    if ii.size:
        import sep
        a, b, _ = sep.sum_circle(ctx.sub, x[ii], y[ii], 2.0, err=ctx.noise0, mask=np.ascontiguousarray(ctx.bad), subpix=5)
        f2[ii], e2[ii] = a * ctx.conv, b * ctx.conv
    flag |= np.where(mfrac >= 0.1, KNOWN_FLAGS["masked"], 0)

    # motion over the exposure, smeared fit
    rate = np.hypot(rra, rdec)
    pa = np.degrees(np.arctan2(rra, rdec)) % 360.0
    try:
        texp = float(l2.header.get("XPOSURE", C.KNOWN_EXPOSURE_S))
    except (TypeError, ValueError):
        texp = C.KNOWN_EXPOSURE_S
    half_h = texp / 3600.0 / 2.0
    ra1, de1 = offset_radec(ra, dec, -rra * half_h, -rdec * half_h)
    ra2, de2 = offset_radec(ra, dec, rra * half_h, rdec * half_h)
    trail = np.full(n, np.nan)
    smear = np.flatnonzero(inner & (rate * texp / 3600.0 / C.PIXEL_ARCSEC > C.KNOWN_SMEAR_MIN_PX * 0.9))
    x1 = y1 = x2 = y2 = np.full(n, np.nan)
    if smear.size:
        x1, y1, x2, y2 = (np.full(n, np.nan) for _ in range(4))
        x1[smear], y1[smear] = sky_to_pixel(l2.wcs, ra1[smear], de1[smear])
        x2[smear], y2[smear] = sky_to_pixel(l2.wcs, ra2[smear], de2[smear])
        trail[smear] = np.hypot(x2[smear] - x1[smear], y2[smear] - y1[smear])
    trail = np.where(np.isfinite(trail), trail, rate * texp / 3600.0 / C.PIXEL_ARCSEC)
    fs, es, cs = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    do = smear[trail[smear] > C.KNOWN_SMEAR_MIN_PX] if smear.size else smear
    if do.size and epsf is not None:
        a, b, c, capped = smeared_fluxes(l2, ctx, x[do], y[do], x1[do], y1[do], x2[do], y2[do])
        fs[do], es[do], cs[do] = a, b, c
        flag[do] |= KNOWN_FLAGS["smeared"] | np.where(capped, KNOWN_FLAGS["smear_capped"], 0)

    # matched-filter peak within the search radius
    snr0, pk, pkx, pky, pkf = (np.full(n, np.nan) for _ in range(5))
    if epsf is not None:
        snr0, pk, pkx, pky, pkf = mf_search(ctx, epsf, x, y, r_px)
    pdra, pddec, psep = (np.full(n, np.nan) for _ in range(3))
    okp = np.isfinite(pkx)
    if okp.any():
        from skyshift_core import pixel_to_sky as p2s
        from skyshift_core.astrometry import tangent_offset_arcsec
        pr, pd_ = p2s(l2.wcs, pkx[okp], pky[okp])
        e_, n_ = tangent_offset_arcsec(ra[okp], dec[okp], pr, pd_)
        pdra[okp], pddec[okp] = e_, n_
        psep[okp] = np.hypot(e_, n_)

    # conditions
    f3 = np.where(edge >= 0, np.asarray(ctx.f3)[iyc, ixc], 0).astype(np.int64)
    flag |= np.where((f3 & int(SAT_BITS)) != 0, KNOWN_FLAGS["sat_core"], 0)
    sdist = _sat_dist(l2.flags, ixc, iyc)
    flag |= np.where(sdist <= C.KNOWN_SAT_GUARD_PX, KNOWN_FLAGS["sat_guard"], 0)
    gpx = extras.get("glow_px") if extras else None
    if gpx is not None:
        flag |= np.where((edge >= 0) & np.asarray(gpx)[iyc, ixc], KNOWN_FLAGS["glow"], 0)
    else:
        flag |= KNOWN_FLAGS["no_glow_map"]
    dsep = dfl = dsn = np.full(n, np.nan)
    if dots is not None and len(dots):
        d, j = _nearest(dots["x"].to_numpy(np.float64), dots["y"].to_numpy(np.float64), x, y, C.KNOWN_NEAR_SEARCH_PX)
        has = j >= 0
        dsep = d * C.PIXEL_ARCSEC
        fcol = "flux_psf_ujy" if "flux_psf_ujy" in dots else "flux_ujy"
        dfl = np.where(has, dots[fcol].to_numpy(np.float64)[np.maximum(j, 0)], np.nan)
        dsn = np.where(has, dots["snr"].to_numpy(np.float64)[np.maximum(j, 0)], np.nan) if "snr" in dots else \
            np.full(n, np.nan)
        flag |= np.where(d <= C.KNOWN_BLEND_PX, KNOWN_FLAGS["dot_near"], 0)
    ssep = sfl = np.full(n, np.nan)
    n3 = np.zeros(n, np.int64)
    if forced is not None and len(forced):
        fx, fy = forced["x"].to_numpy(np.float64), forced["y"].to_numpy(np.float64)
        d, j = _nearest(fx, fy, x, y, C.KNOWN_NEAR_SEARCH_PX)
        ssep = d * C.PIXEL_ARCSEC
        sfl = np.where(j >= 0, forced["flux_psf_ujy"].to_numpy(np.float64)[np.maximum(j, 0)], np.nan)
        n3 = _count_within(fx, fy, x, y, 3.0)
        flag |= np.where(d <= C.KNOWN_BLEND_PX, KNOWN_FLAGS["blend_star"], 0)
    inb = edge >= 0
    if l2.wave is not None:
        wl, bw = np.full(n, np.nan), np.full(n, np.nan)
        if inb.any():
            wl[inb], bw[inb] = l2.wave.wavelength(x[inb], y[inb]), l2.wave.bandwidth(x[inb], y[inb])
    else:
        wl = bw = np.full(n, np.nan)
    zodi = np.where(inb, np.asarray(l2.zodi)[iyc, ixc], np.nan) if getattr(l2, "zodi", None) is not None else \
        np.full(n, np.nan)
    num = pd.to_numeric(rows.get("number", pd.Series(-1, index=rows.index)), errors="coerce").fillna(-1)
    df = pd.DataFrame({
        "obs_id": str(l2.obsid or ""), "detector": int(l2.detector), "mjd": mjd,
        "kind": rows["kind"].astype(str).to_numpy(object), "name": rows["name"].astype(str).to_numpy(object),
        "number": num.to_numpy(np.int64), "cls": rows.get("cls", pd.Series("", index=rows.index)).astype(str).to_numpy(object),
        "ra": ra, "dec": dec, "x": x, "y": y, "edge_px": edge, "v_pred": rows["v_pred"].to_numpy(np.float64),
        "rate_ra_arcsec_h": rra, "rate_dec_arcsec_h": rdec, "rate_arcsec_h": rate, "pa_deg": pa, "trail_px": trail,
        "sigma_arcsec": rows["sigma_arcsec"].to_numpy(np.float64), "search_arcsec": radius,
        "delta_au": rows["delta_au"].to_numpy(np.float64), "r_au": rows["r_au"].to_numpy(np.float64),
        "phase_deg": rows["phase_deg"].to_numpy(np.float64), "parallax_arcsec": par,
        "wavelength_um": wl, "bandwidth_um": bw,
        "flux_psf_ujy": fpsf, "flux_psf_err_ujy": epsf_, "psf_chi2_nu": chi, "psf_npix": np.nan_to_num(npx),
        "flux_ap2_ujy": f2, "flux_ap2_err_ujy": e2,
        "flux_smear_ujy": fs, "flux_smear_err_ujy": es, "smear_chi2_nu": cs,
        "snr_mf_pred": snr0, "peak_snr": pk, "peak_flux_ujy": pkf * ctx.conv,
        "peak_dra_arcsec": pdra, "peak_ddec_arcsec": pddec, "peak_sep_arcsec": psep,
        "masked_frac": mfrac, "flags": f3, "sat_dist_px": sdist,
        "dot_sep_arcsec": dsep, "dot_flux_ujy": dfl, "dot_snr": dsn,
        "star_sep_arcsec": ssep, "star_flux_ujy": sfl, "n_stars_3px": n3,
        "bkg_mjysr": np.where(inb, ctx.bkg_level[iyc, ixc], np.nan),
        "noise_mjysr": np.where(inb, ctx.noise[iyc, ixc], np.nan), "zodi_mjysr": zodi,
        "known_flag": flag, "list_row": rows["list_row"].to_numpy(np.int64),
    })
    info.update({"rows": int(n), "off_edge": int((~inner).sum()), "smeared": int(do.size)})
    return df.astype({c: t for c, t in ROW_COLUMNS.items() if t != "str"}), info


def empty_table() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in ROW_COLUMNS.items()})


# ---------------------------------------------------------------- the worker's hooks

def known_rel(obs_id: str, det: int, folder: str) -> str:
    """<folder>_known/<obs_id>D<det>.parquet"""
    return f"{folder}_known/{obs_id}D{int(det)}.parquet"


def table_bytes(df: pd.DataFrame, meta: dict) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq
    t = pa.Table.from_pandas(df, preserve_index=False)
    t = t.replace_schema_metadata({META_KEY: json.dumps(meta, default=_json)})
    floats = [f.name for f in t.schema if pa.types.is_floating(f.type)]
    buf = io.BytesIO()
    pq.write_table(t, buf, compression="zstd", compression_level=max(9, C.PARQUET_SLIM_LEVEL if C.PARQUET_SLIM else 9),
                   use_dictionary=["obs_id", "kind", "name", "cls"], use_byte_stream_split=floats or False,
                   write_statistics=["kind", "name", "v_pred", "known_flag"])  # the schema keeps META_KEY
    return buf.getvalue()


def _json(v):
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v) if np.isfinite(v) else None
    if isinstance(v, np.ndarray):
        return v.tolist()
    return str(v)


def attach(store, pending, l2, ctx, extras: dict, forced, obs_id: str, detector: int, folder: str, st: dict) -> None:
    """dots._measure, after every other table exists: the photo's known table into pending.known / known_rel and the
    footer key `known` into pending.meta. Never raises and never changes another table; no list -> no file."""
    t0 = time.process_time()
    try:
        rows, info = photo_rows(store, obs_id, detector)
        if rows is None:
            pending.meta["known"] = info
            return
        df, mi = measure(l2, ctx, extras, rows, pending.df, forced)
        info.update(mi)
        info.update({"version": VERSION, "file": f"{folder}_known"})
        pending.known = df
        pending.known_rel = known_rel(obs_id, detector, folder)
        pending.meta["known"] = info
        st["n_known"] = int(len(df))
    except Exception as e:  # noqa: BLE001 - never costs the photo anything else
        pending.known = None
        pending.meta["known"] = {"error": f"{type(e).__name__}: {e}"[:300]}
        st["known_error"] = pending.meta["known"]["error"]
    st["known_s"] = round(time.process_time() - t0, 3)


def write(store, pending, st: dict) -> None:
    """dots._finish, before the dot file: the known file (also with 0 rows: the photo was checked). Never raises."""
    if getattr(pending, "known", None) is None:
        return
    try:
        meta = {k: v for k, v in pending.meta.get("known", {}).items()}
        meta.update({"obs_id": str(pending.meta.get("obs_id", "")), "flags": KNOWN_FLAGS,
                     "search": [C.KNOWN_SEARCH_FLOOR_ARCSEC, C.KNOWN_SEARCH_CAP_ARCSEC, C.KNOWN_SEARCH_SIGMAS,
                                C.KNOWN_SEARCH_COMET_ARCSEC]})
        data = table_bytes(pending.known, meta)
        store.write_bytes(pending.known_rel, data)
        st["known_bytes"] = len(data)
    except Exception as e:  # noqa: BLE001
        err = f"write: {type(e).__name__}: {e}"[:300]
        pending.meta.setdefault("known", {})["error"] = err
        st["known_error"] = err


def read_known(path) -> tuple[pd.DataFrame, dict]:
    import pyarrow.parquet as pq
    t = pq.read_table(path)
    md = (t.schema.metadata or {}).get(META_KEY.encode())
    return t.to_pandas(), (json.loads(md) if md else {})
