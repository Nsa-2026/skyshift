"""Step 2, `dots`: every 5σ point in every photo, one Parquet per photo (contracts/dots.md).

Per photo: stream IMAGE, FLAGS, VARIANCE, ZODI, EPSF and WCS-WAVE from the public S3 copy (never
saved; the ePSF table is read once per worker and calibration file), core.detect with
thresh=DETECT_THRESH per pixel (bad-pixel mask of contracts/flags.md, SOURCE never masked), keep
aperture snr >= DOT_MIN_SNR; saturated dots (OVERFLOW/NONLINEAR under them) are re-centroided on their
wings (core.centroid method="fit"); then sky position, µJy, wavelength, bandwidth, the flag bits in the
3×3 pixels under the dot (`flags`) and at its peak pixel (`flags_peak`), the photo's session and visit,
the 6″ cell, the ZODI model, the background detection subtracted, the peak, the second-moment shape
(elong, a, b, theta), an ePSF fit (flux_psf_*, psf.py), an ePSF position fit (x_psf, y_psf, ra_psf, dec_psf,
pos_err_psf_arcsec, psf_pos_ok; config DOTS_PSF_POSITION; not for saturated dots), sep's flags and five
aperture fluxes (flux_ap_*).
Forced photometry: targets from the sky-wide list refcat/sky/forced_n32/ (forced_sky.py; the partitions
the photo's cone overlaps, no zone box) when it is complete, else from refcat/<zone>_forced.parquet inside
the zone box + 0.05° as before; every reference target in the photo (moved to MJD-AVG) gets the same ePSF fit at its fixed position and the r = 2 px aperture, detected
or not, in forced/<obs_id>D<det>.parquet, written before the dot file.
Every dot file carries the photo's metadata (header values, dot count, timings, zodi median,
astrometric offset against Gaia, PSF size, 32 × 32 background and ZODI grids) as Parquet key-value
metadata under `skyshift_photo` (JSON); `photo_meta(path)` reads it back.
Photos with FINAST != 0 (bad astrometry) get an empty dot file (with metadata) and no forced file.
Row order is stable (brightest aperture flux first, as core.detect returns it): link's members.parquet
points at rows by index.
sep's deblend_cont is the zone's (config.zone_dots): the sky run's DOTS_DEBLEND_CONT into dots/, the featured-target
zones' DOTS_FINE_DEBLEND_CONT into their own folder (dots_deblend1e-4/), recorded in each file's metadata (`detect`).
One process per core; a photo whose dot file exists is skipped; a photo that keeps failing is logged
and skipped. Each worker task is C.DOTS_TASK_PHOTOS photos (process_photos): while one photo is
measured, the next is read in a background thread (C.DOTS_PREFETCH), so a core doesn't wait for
the network. The files are the same either way.
"""

from __future__ import annotations

import copy
import dataclasses
import io
import json
import multiprocessing as mp
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyarrow as pa
import sep
from scipy.spatial import cKDTree
from skyshift_core import (SAT_BITS, background, build_mask, centroid, detect, open_level2, pixel_to_sky, propagate,
                           sky_to_pixel, ujy_per_mjysr)
from skyshift_core.detect import APERTURE_R_PX as DETECT_APERTURE_R_PX
from skyshift_core.flags import TRANSIENT
from skyshift_core.glow import fit_glow
from skyshift_core.holes import dark_holes
from skyshift_core.level2 import EPSF

from . import config as C
from . import forced_sky, log
from .psf import dot_positions, ext_pass, gather, mf_at, mf_pass, psf_photometry, residual_companions
from .sky import cell_of
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("dots")

DOT_COLUMNS = {
    "obs_id": "string", "detector": "int8", "mjd": "float64", "session": "int32", "visit": "int16",
    "ra": "float64", "dec": "float64", "pos_err_arcsec": "float32", "x": "float32", "y": "float32",
    "flux_ujy": "float32", "flux_err_ujy": "float32", "wavelength_um": "float32", "bandwidth_um": "float32",
    "elong": "float32", "snr": "float32", "flags": "int32", "flags_peak": "int32", "cell": "int64",
    # added 14 Nov (dots extras): NaN where the photo or the dot doesn't allow them
    "zodi_mjysr": "float32", "bkg_mjysr": "float32", "peak_mjysr": "float32",
    "a_arcsec": "float32", "b_arcsec": "float32", "theta_deg": "float32",
    "flux_psf_ujy": "float32", "flux_psf_err_ujy": "float32", "psf_chi2_nu": "float32",
    # added 14 Nov (dots v4): ePSF position fit (or the centroid where it didn't run), sep's flags
    "x_psf": "float32", "y_psf": "float32", "ra_psf": "float64", "dec_psf": "float64",
    "pos_err_psf_arcsec": "float32", "psf_pos_ok": "bool", "sep_flag": "int16",
}
# v4 columns a frame may lack (test frames, v3 tables): copied from these columns, or these values
DOT_V4_FALLBACK = {"x_psf": "x", "y_psf": "y", "ra_psf": "ra", "dec_psf": "dec", "pos_err_psf_arcsec": "pos_err_arcsec"}
DOT_V4_DEFAULT = {"psf_pos_ok": False, "sep_flag": 0}
# fixed_size_list<float32>[len(C.AP_RADII_PX)] columns, after the scalar ones (dots_arrow writes them)
DOT_LIST_COLUMNS = ("flux_ap_ujy", "flux_ap_err_ujy")
# extraction v2 (DetectSettings.v2; docs/extraction-v2.md): after the list columns, only in v2 files
DOT_V2_COLUMNS = {
    "snr_mf": "float32",            # ePSF-matched-filter S/N at the dot (largest over its 3 × 3 pixels)
    "n_transient": "int8",          # pixels with FLAGS TRANSIENT (bit 0) within DOTS_TRANSIENT_R_PX (3 px)
    "n_masked": "int8",             # masked pixels (contracts/flags.md mask) with centres within 2 px (the aperture)
    "partner_flux_ujy": "float32",  # r = 2 px aperture at the dot's sky position in the simultaneous partner detector
    "partner_err_ujy": "float32",   # (D1↔D4, D2↔D5, D3↔D6, same exposure); its 1σ × the partner's empirical factor
}


def empty_dots(v2: bool = False) -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype=t) for c, t in DOT_COLUMNS.items()})
    for c in DOT_LIST_COLUMNS:
        df[c] = pd.Series(dtype=object)
    if v2:
        for c, t in DOT_V2_COLUMNS.items():
            df[c] = pd.Series(dtype=t)
    return df


def as_dots(df: pd.DataFrame) -> pd.DataFrame:
    """Columns in contract order and types; missing float columns become NaN (e.g. test frames). A v2 table
    (any DOT_V2_COLUMNS present) keeps them after the list columns (missing partner columns NaN)."""
    df = df.copy()
    for c, t in DOT_COLUMNS.items():
        if c not in df and (c in DOT_V4_DEFAULT or DOT_V4_FALLBACK.get(c) in df):
            df[c] = DOT_V4_DEFAULT[c] if c in DOT_V4_DEFAULT else df[DOT_V4_FALLBACK[c]]
        if c not in df:
            if not t.startswith("float"):
                raise KeyError(f"dot column {c!r} missing")
            df[c] = np.nan
    out = df[list(DOT_COLUMNS)].astype(DOT_COLUMNS)
    n = len(C.AP_RADII_PX)
    for c in DOT_LIST_COLUMNS:
        out[c] = df[c] if c in df else pd.Series([np.full(n, np.nan, np.float32) for _ in range(len(df))],
                                                   index=df.index, dtype=object)
    if any(c in df for c in DOT_V2_COLUMNS):
        for c, t in DOT_V2_COLUMNS.items():
            if c in df:
                out[c] = df[c].astype(t)
            elif t.startswith("float"):
                out[c] = np.full(len(df), np.nan, t)
            else:
                out[c] = np.zeros(len(df), t)
    return out


def or3x3(flags: np.ndarray) -> np.ndarray:
    """OR of each pixel's 3×3 neighbourhood (the r = 1.5 px disk of core.flags_under), whole frame."""
    f = np.asarray(flags).astype(np.int32, copy=False)
    ny, nx = f.shape
    p = np.pad(f, 1)
    out = np.zeros_like(f)
    for dy in range(3):
        for dx in range(3):
            out |= p[dy:dy + ny, dx:dx + nx]
    return out


def pixel_of(x: np.ndarray, y: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    ix = np.clip(np.rint(x).astype(np.int64), 0, shape[1] - 1)
    iy = np.clip(np.rint(y).astype(np.int64), 0, shape[0] - 1)
    return ix, iy


def peak_pixel(data: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Brightest pixel in the 3×3 around each dot's pixel."""
    ny, nx = data.shape
    ix, iy = pixel_of(x, y, data.shape)
    best = np.full(ix.size, -np.inf)
    bx, by = ix.copy(), iy.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            jx, jy = np.clip(ix + dx, 0, nx - 1), np.clip(iy + dy, 0, ny - 1)
            v = np.nan_to_num(data[jy, jx].astype(np.float64), nan=-np.inf)
            better = v > best
            best = np.where(better, v, best)
            bx, by = np.where(better, jx, bx), np.where(better, jy, by)
    return bx, by


def recentroid_saturated(data: np.ndarray, flags: np.ndarray, x: np.ndarray, y: np.ndarray,
                         sat: np.ndarray, half: int = C.SAT_RECENTROID_HALF_PX) -> tuple[np.ndarray, np.ndarray, int]:
    """Wings fit (core.centroid method="fit") on a stamp for the dots flagged saturated."""
    x, y = x.copy(), y.copy()
    ny, nx = data.shape
    n = 0
    for k in np.flatnonzero(sat):
        ix, iy = int(round(x[k])), int(round(y[k]))
        x0, y0 = max(ix - half, 0), max(iy - half, 0)
        x1, y1 = min(ix + half + 1, nx), min(iy + half + 1, ny)
        if x1 - x0 < 9 or y1 - y0 < 9:
            continue
        try:
            c = centroid(data[y0:y1, x0:x1], x[k] - x0, y[k] - y0, flags=flags[y0:y1, x0:x1],
                         method="fit", box=min(x1 - x0, y1 - y0) // 2)
        except Exception:  # noqa: BLE001 - keep the original centroid
            c = None
        if c is not None:
            x[k], y[k] = c[0] + x0, c[1] + y0
            n += 1
    return x, y, n


def pos_err_arcsec(snr: np.ndarray) -> np.ndarray:
    """contracts/dots.md: sqrt((1.4 × 3.2″ / snr)² + 0.25″²); NaN where snr <= 0."""
    snr = np.asarray(snr, np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        e = np.sqrt((C.POS_ERR_K * C.PSF_SIGMA_ARCSEC / snr) ** 2 + C.POS_ERR_FLOOR_ARCSEC**2)
    return np.where(snr > 0, e, np.nan)


def shape_moments(data: np.ndarray, bad: np.ndarray, x: np.ndarray, y: np.ndarray,
                  r: float = C.ELONG_R_PX, bkg: tuple[float, float] = C.ELONG_BKG_PX,
                  chunk: int = 20000) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Shape of each dot from intensity-weighted second moments: good pixels within r px of (x, y),
    minus the median of a local annulus, negative values clipped to 0. core.detect does not return
    sep's a and b, so this stands in for them.
    Returns (elong, a_px, b_px, theta_deg): a, b = √ of the moment matrix's eigenvalues (px; for an
    undersampled star a ≈ b ≈ 0.6 px), elong = a / b (capped at 99), theta = major axis angle in
    pixel coordinates, from +x towards +y, in (−90, 90]. NaN near the edge (stamp off the array),
    with fewer than 5 annulus pixels, or no positive signal. Vectorised over dots."""
    ny, nx = data.shape
    h = int(np.ceil(bkg[1]))
    k = np.arange(-h, h + 1)
    out = [np.full(x.size, np.nan) for _ in range(4)]
    for s0 in range(0, x.size, chunk):
        sl = slice(s0, s0 + chunk)
        xs, ys = np.asarray(x[sl], np.float64), np.asarray(y[sl], np.float64)
        ix, iy = np.rint(xs).astype(np.int64), np.rint(ys).astype(np.int64)
        inside = (ix - h >= 0) & (iy - h >= 0) & (ix + h + 1 <= nx) & (iy + h + 1 <= ny)
        yy = np.clip(iy[:, None, None] + k[None, :, None], 0, ny - 1)
        xx = np.clip(ix[:, None, None] + k[None, None, :], 0, nx - 1)
        st = data[yy, xx].astype(np.float64)
        good = ~bad[yy, xx] & np.isfinite(st)
        dx = k[None, None, :] - (xs - ix)[:, None, None]
        dy = k[None, :, None] - (ys - iy)[:, None, None]
        rr = dx * dx + dy * dy
        ann = good & (rr >= bkg[0] ** 2) & (rr <= bkg[1] ** 2)
        n_ann = ann.reshape(len(xs), -1).sum(axis=1)
        srt = np.sort(np.where(ann, st, np.inf).reshape(len(xs), -1), axis=1)
        lo, hi = np.maximum((n_ann - 1) // 2, 0), np.maximum(n_ann // 2, 0)
        rows = np.arange(len(xs))
        med = 0.5 * (srt[rows, lo] + srt[rows, hi])
        w = np.where(good & (rr <= r * r), st - med[:, None, None], 0.0)
        w = np.clip(np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
        tot = w.sum(axis=(1, 2))
        ok = inside & (n_ann >= 5) & (tot > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            cxx = (w * dx * dx).sum(axis=(1, 2)) / tot
            cyy = (w * dy * dy).sum(axis=(1, 2)) / tot
            cxy = (w * dx * dy).sum(axis=(1, 2)) / tot
            tr, det = cxx + cyy, cxx * cyy - cxy * cxy
            disc = np.sqrt(np.maximum(tr * tr / 4.0 - det, 0.0))
            l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
            el = np.where(l2 > 1e-6, np.sqrt(l1 / l2), 99.0)
            th = np.degrees(0.5 * np.arctan2(2.0 * cxy, cxx - cyy))
        th = np.where(th <= -90.0, th + 180.0, th)
        out[0][sl] = np.where(ok, np.minimum(el, 99.0), np.nan)
        out[1][sl] = np.where(ok, np.sqrt(np.maximum(l1, 0.0)), np.nan)
        out[2][sl] = np.where(ok, np.sqrt(np.maximum(l2, 0.0)), np.nan)
        out[3][sl] = np.where(ok, th, np.nan)
    return out[0], out[1], out[2], out[3]


def elongation(data: np.ndarray, bad: np.ndarray, x: np.ndarray, y: np.ndarray,
               r: float = C.ELONG_R_PX, bkg: tuple[float, float] = C.ELONG_BKG_PX) -> np.ndarray:
    """a/b of each dot (shape_moments): ~1 for a point source, large for trails and streaks."""
    return shape_moments(data, bad, x, y, r, bkg)[0]


@dataclass
class PhotoContext:
    """Per-photo maps shared by the dot table and forced photometry (computed once)."""
    bad: np.ndarray        # bad-pixel mask (contracts/flags.md)
    f3: np.ndarray         # 3×3 OR of FLAGS
    bkg_level: np.ndarray  # background map core.detect subtracts, MJy/sr
    bkg_rms: np.ndarray
    noise: np.ndarray      # per-pixel 1σ: max(√VARIANCE, background rms) (config PSF_NOISE)
    conv: float            # µJy per MJy/sr in one pixel
    sub: np.ndarray        # IMAGE minus the background map, bad pixels 0: what core.detect measures on
    timing: dict
    background: object = None   # the core.detect Background these maps come from (passed back to detect)
    _noise0: np.ndarray | None = None

    @property
    def noise0(self) -> np.ndarray:
        """ctx.noise with non-finite values 0, C-contiguous (sep's err= for apertures)."""
        if self._noise0 is None:
            self._noise0 = np.ascontiguousarray(np.where(np.isfinite(self.noise), self.noise, 0.0))
        return self._noise0


def detection_background(l2) -> tuple[np.ndarray, np.ndarray]:
    """(level, rms) maps of the background core.detect subtracts: same mask, same sep settings."""
    b, _, _ = _detection_background(l2)
    return b.level, b.rms


def _detection_background(l2):
    """(Background, bad-pixel mask, IMAGE as float64 with bad pixels 0): exactly what core.detect
    computes first, so photo_context can hand the Background to detect instead of recomputing it."""
    m = build_mask(l2.data, l2.flags)
    d = np.where(m, 0.0, np.asarray(l2.data, dtype=np.float64))
    return background(d, m, box=32), m, d


def psf_noise(l2, bkg_rms: np.ndarray) -> np.ndarray:
    """Per-pixel 1σ for the ePSF fit (config PSF_NOISE): max(√VARIANCE, background rms) or √VARIANCE."""
    if l2.variance is None:
        return np.asarray(bkg_rms, np.float64)
    v = np.asarray(l2.variance, np.float64)
    sd = np.sqrt(np.where(np.isfinite(v) & (v > 0), v, np.nan))
    if C.PSF_NOISE == "variance":
        return sd
    return np.fmax(sd, bkg_rms)


def photo_context(l2, timing: dict | None = None, pre: tuple | None = None) -> PhotoContext:
    """The per-photo maps; `pre` = _detection_background(l2) when the caller already has it (mask_dark_holes)."""
    t0 = time.perf_counter()
    b, bad, masked = _detection_background(l2) if pre is None else pre
    level, rms = b.level, b.rms
    sub = masked - level
    sub[bad] = 0.0
    ctx = PhotoContext(bad=bad, f3=or3x3(l2.flags), bkg_level=level, bkg_rms=rms,
                       noise=psf_noise(l2, rms), conv=ujy_per_mjysr(l2.wcs), sub=np.ascontiguousarray(sub),
                       timing={} if timing is None else timing, background=b)
    ctx.timing["context_s"] = time.perf_counter() - t0
    return ctx


def raw_background_holes(l2) -> tuple[tuple, np.ndarray, dict]:
    """(detection background of the raw photo as _detection_background returns it, the dark-hole mask, its counts):
    what mask_dark_holes computes first. The fused dots worker computes it once per photo and hands it to both the
    dots (mask_dark_holes `shared`) and the session stack (coadd.prepare `shared`): same values, one computation."""
    pre = _detection_background(l2)
    b, bad, masked = pre
    sub = masked - b.level
    sub[bad] = 0.0
    m, info = dark_holes(sub, psf_noise(l2, b.rms), bad, l2.flags, sigma=C.HOLE_SIGMA, min_px=C.HOLE_MIN_PX,
                         max_px=C.HOLE_MAX_PX, near_bits=C.HOLE_NEAR_BITS, near_px=C.HOLE_NEAR_PX,
                         grow_px=C.HOLE_GROW_PX)
    return pre, m, info


def mask_dark_holes(l2, timing: dict | None = None, shared: tuple | None = None) -> tuple[object, dict, tuple | None]:
    """Extraction v2's dark-hole mask (16 Nov, second pass; core skyshift_core.holes, config HOLE_*): unflagged
    pixels far below the sky (mostly cold pixels NASA's COLD mask misses) set to NaN in a shallow copy of l2, so that every
    later step treats them as bad pixels (build_mask masks non-finite values). Returns (l2 or its copy, the footer's
    `dark_holes` counts, the detection background to reuse when nothing was masked, else None). shared: what
    raw_background_holes(l2) returned (the fused worker; its time is then in timing `shared_s`)."""
    t0 = time.perf_counter()
    pre, m, info = raw_background_holes(l2) if shared is None else (shared[0], shared[1], dict(shared[2]))
    info["config"] = {"sigma": C.HOLE_SIGMA, "min_px": C.HOLE_MIN_PX, "max_px": C.HOLE_MAX_PX,
                      "near_bits": int(C.HOLE_NEAR_BITS), "near_px": C.HOLE_NEAR_PX, "grow_px": C.HOLE_GROW_PX}
    if timing is not None:
        timing["holes_s"] = time.perf_counter() - t0
    if not m.any():
        return l2, info, pre
    out = copy.copy(l2)
    data = np.array(l2.data, copy=True)
    data[m] = np.nan
    out.data = data
    return out, info, None


def aperture_fluxes(l2, ctx: PhotoContext, x: np.ndarray, y: np.ndarray,
                    radii=C.AP_RADII_PX, annulus=C.AP_BKG_ANNULUS_PX) -> tuple[np.ndarray, np.ndarray]:
    """(N, len(radii)) fluxes and errors in µJy: sep circular apertures on IMAGE (bad pixels out),
    local background = mean of the annulus (r_in, r_out) px, noise = ctx.noise."""
    d = np.ascontiguousarray(np.where(ctx.bad, 0.0, np.asarray(l2.data, dtype=np.float64)))
    err = ctx.noise0
    m = np.ascontiguousarray(ctx.bad)
    f = np.full((x.size, len(radii)), np.nan)
    e = np.full((x.size, len(radii)), np.nan)
    if x.size == 0:
        return f, e
    for k, r in enumerate(radii):
        f[:, k], e[:, k] = _sum_circle_bkgann(d, x, y, float(r), err, m, tuple(annulus))
    return f * ctx.conv, e * ctx.conv


def _sum_circle_bkgann(d, x, y, r, err, m, annulus) -> tuple[np.ndarray, np.ndarray]:
    """sep.sum_circle with a background annulus; NaN for a dot whose annulus is entirely masked.
    sep raises ValueError for the whole call when one object's annulus has no valid pixel (all-sky test,
    14 Nov: a bright star near the Galactic centre with its OVERFLOW/NONLINEAR mask beyond 8 px failed the
    photo; 28 such dots on it). After the first failure, dots with less than one unmasked pixel in the annulus
    are left out in one go; any left that sep still names (one index per call) are left out one by one."""
    ok = np.ones(x.size, bool)
    fk, ek = np.full(x.size, np.nan), np.full(x.size, np.nan)
    screened = False
    for _ in range(x.size):
        sel = np.flatnonzero(ok)
        if sel.size == 0:
            break
        try:
            fs, es, _ = sep.sum_circle(d, x[sel], y[sel], r, err=err, mask=m, bkgann=annulus, subpix=5)
        except ValueError as exc:
            hit = re.search(r"background annulus does not contain any valid pixels, for the object at index (\d+)", str(exc))
            if not hit:
                raise
            ok[sel[int(hit.group(1))]] = False
            if not screened:
                screened = True
                valid = np.ascontiguousarray((~m).astype(np.float64))
                area, _, _ = sep.sum_circann(valid, x, y, annulus[0], annulus[1], subpix=1)
                ok &= np.asarray(area) >= 1.0
            continue
        fk[sel], ek[sel] = fs, es
        break
    return fk, ek


GUARD_MATCH_PX = 1.0  # saturation guard: a fine dot with a sky-setting dot within this distance is no piece


def sat_distance(flags) -> np.ndarray:
    """Distance (px) from each pixel to the nearest OVERFLOW/NONLINEAR pixel (inf when there is none)."""
    from scipy.ndimage import distance_transform_edt
    f = np.asarray(flags)
    sat = (f.astype(np.int64) & SAT_BITS) != 0
    if not sat.any():
        return np.full(f.shape, np.inf)
    return distance_transform_edt(~sat)


def _at(a: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    ix, iy = pixel_of(np.asarray(x, np.float64), np.asarray(y, np.float64), a.shape)
    return a[iy, ix]


def dots_table(l2, obs_id: str, detector: int, session: int = 0, visit: int = 0,
               thresh: float = C.DETECT_THRESH, min_snr: float = C.DOT_MIN_SNR,
               timing: dict | None = None, ctx: PhotoContext | None = None,
               deblend_cont: float = C.DOTS_DEBLEND_CONT, settings: C.DetectSettings | None = None,
               counts: dict | None = None, extras: dict | None = None) -> pd.DataFrame:
    """The dot table of one opened Level 2 photo (or cutout). Uses l2.zodi and l2.epsf when present
    (else those columns are NaN). `timing`, if given, receives seconds per stage. deblend_cont: sep's
    (config.zone_dots gives the zone's); `settings` (config.zone_detect), when given, replaces it with every setting:
    fine detection v2 (contracts/dots.md) adds sep clean_param, the saturation guard and the ePSF second pass, whose
    dots (sep_flag SEP_FLAG_PSF_PASS) are measured on the residual; the glow model (settings.glow, 16 Nov) detects on
    IMAGE minus the bright stars' glow with the glow's noise (glow_dots). `counts`, if given, receives n_guard_dropped,
    n_psf_pass and the glow's counts. Extraction v2 (settings.v2) adds the matched-filter pass (its dots: sep_flag
    SEP_FLAG_MF_PASS, measured on the residual), the v2 columns (DOT_V2_COLUMNS; partner columns NaN here) and puts
    the peaks and the depth map in `extras` ("peaks", "mf_den") when given."""
    timing = {} if timing is None else timing
    counts = {} if counts is None else counts
    s = settings if settings is not None else C.DetectSettings(deblend_cont)
    if s.glow and getattr(l2, "flags", None) is not None:
        return glow_dots(l2, obs_id, detector, session, visit, thresh, min_snr, timing, ctx, s, counts, extras)
    det, comp, ctx = _detections(l2, thresh, min_snr, timing, ctx, s, counts, extras)
    if det is None:
        return empty_dots(s.v2)
    return _measure_all(l2, det, comp, ctx, obs_id, detector, session, visit, timing, v2=s.v2)


def _detections(l2, thresh: float, min_snr: float, timing: dict, ctx: PhotoContext | None, s: C.DetectSettings,
                counts: dict, extras: dict | None = None) -> tuple[np.ndarray | None, tuple | None, PhotoContext | None]:
    """core.detect (+ the saturation guard and the ePSF second pass of fine detection v2) on l2.data.
    Returns (detections, (second-pass dots, their subtracted model) or None, the photo context); detections None
    when there is nothing at all."""
    t0 = time.perf_counter()
    det = detect(l2.data, l2.flags, variance=l2.variance, thresh=thresh, deblend_cont=s.deblend_cont,
                 clean_param=s.clean_param, bkg=None if ctx is None else ctx.background)
    det = det[det["snr"] >= min_snr]  # keeps detect's order (brightest first): members.parquet relies on it
    timing["detect_s"] = time.perf_counter() - t0
    if len(det) == 0 and not s.v2:  # v2: the matched-filter pass still runs (a photo with no first-pass dot)
        return None, None, ctx
    if ctx is None:
        ctx = photo_context(l2, timing)
    comp = None
    if s.sat_guard_px > 0 or s.psf_pass:
        t0 = time.perf_counter()
        flags = np.asarray(l2.flags) if l2.flags is not None else np.zeros(np.shape(l2.data), np.int32)
        dsat = sat_distance(flags)
        if s.sat_guard_px > 0 and (s.deblend_cont != C.DOTS_DEBLEND_CONT or s.clean_param != 1.0):
            # dots the sky run's setting doesn't also find, close to saturated pixels: pieces of a saturated star's wings.
            # Only the candidates (within sat_guard_px) need the test, so the sky setting's objects are measured only
            # where their peak is within GUARD_MATCH_PX + 1 px of one (detect `near`: the same rows, 16 Nov)
            cand = _at(dsat, det["x"], det["y"]) < s.sat_guard_px
            new = np.ones(len(det), bool)
            if cand.any():
                ref = detect(l2.data, l2.flags, variance=l2.variance, thresh=thresh, deblend_cont=C.DOTS_DEBLEND_CONT,
                             bkg=ctx.background, near=(det["x"][cand], det["y"][cand], GUARD_MATCH_PX + 1.5))
                ref = ref[ref["snr"] >= min_snr]
                if len(ref):
                    d, _ = cKDTree(np.c_[ref["x"], ref["y"]]).query(np.c_[det["x"], det["y"]], k=1)
                    new = d > GUARD_MATCH_PX
            drop = new & cand
            det = det[~drop]
            counts["n_guard_dropped"] = int(drop.sum())
        timing["guard_s"] = time.perf_counter() - t0
        if s.psf_pass and getattr(l2, "epsf", None) is not None and len(det):
            t0 = time.perf_counter()
            ix, iy = pixel_of(det["x"].astype(np.float64), det["y"].astype(np.float64), ctx.f3.shape)
            sat1 = (ctx.f3[iy, ix] & SAT_BITS) != 0
            comp = residual_companions(ctx.sub, ctx.noise0, ctx.bad, det["x"], det["y"], det["snr"], sat1, l2.epsf,
                                       thresh=thresh, min_snr=min_snr)
            cd = comp.dots
            if s.sat_guard_px > 0 and len(cd):
                cd = cd[_at(dsat, cd["x"], cd["y"]) >= s.sat_guard_px]
            cd = cd.copy()
            cd["sep_flag"] = cd["sep_flag"] | C.SEP_FLAG_PSF_PASS
            comp = (cd, comp.model)
            counts["n_psf_pass"] = int(len(cd))
            timing["psf_pass_s"] = time.perf_counter() - t0
        elif s.psf_pass:
            counts["n_psf_pass"] = 0
    if s.v2:
        comp = _mf_pass(l2, det, comp, ctx, s, counts, extras, timing,
                        dsat=dsat if (s.sat_guard_px > 0 or s.psf_pass) else None)
        if C.EXT_PASS and extras is not None:  # its rows go to their own file (ext_table), never into the dots
            _ext_pass(l2, det, comp, ctx, s, counts, extras, timing,
                      dsat=dsat if (s.sat_guard_px > 0 or s.psf_pass) else None)
    if len(det) == 0 and not (comp is not None and len(comp[0])):
        return None, None, ctx
    return det, comp, ctx


def _mf_pass(l2, det: np.ndarray, comp: tuple | None, ctx: PhotoContext, s: C.DetectSettings, counts: dict,
             extras: dict | None, timing: dict, dsat: np.ndarray | None = None) -> tuple | None:
    """Extraction v2's matched-filter pass (psf.mf_pass) on the second pass's residual: its dots join the second
    pass's (sep_flag SEP_FLAG_MF_PASS, measured on the residual); its peaks and depth map go to `extras`."""
    t0 = time.perf_counter()
    shape = ctx.sub.shape
    model = comp[1] if comp is not None else None
    resid = ctx.sub if model is None else np.where(ctx.bad, 0.0, ctx.sub - model)
    noise = ctx.noise0 if model is None else np.sqrt(ctx.noise0 ** 2 + (C.PSF_PASS_MODEL_FRAC * model) ** 2)
    xs = [np.asarray(det["x"], np.float64)] + ([np.asarray(comp[0]["x"], np.float64)] if comp is not None else [])
    ys = [np.asarray(det["y"], np.float64)] + ([np.asarray(comp[0]["y"], np.float64)] if comp is not None else [])
    flags = np.asarray(l2.flags) if l2.flags is not None else np.zeros(shape, np.int32)
    if s.sat_guard_px > 0 and dsat is None:
        dsat = sat_distance(flags)
    elif s.sat_guard_px <= 0:
        dsat = None
    prim_x = prim_y = None
    if model is not None and len(det):
        ix, iy = pixel_of(det["x"].astype(np.float64), det["y"].astype(np.float64), shape)
        use = (det["snr"] >= C.PSF_PASS_PRIMARY_SNR) & ((ctx.f3[iy, ix] & SAT_BITS) == 0)
        prim_x, prim_y = det["x"][use].astype(np.float64), det["y"][use].astype(np.float64)
    mp = mf_pass(resid, noise, ctx.bad, getattr(l2, "epsf", None), np.concatenate(xs), np.concatenate(ys),
                 primary_x=prim_x, primary_y=prim_y, sat_dist=dsat, sat_guard_px=s.sat_guard_px,
                 min_snr=C.mf_pass_snr(getattr(l2, "detector", None)), normalise=C.MF_PASS_NORMALISE)
    md = mp.dots.copy()
    md["sep_flag"] = md["sep_flag"] | C.SEP_FLAG_MF_PASS
    info = dict(mp.info)
    stats = info.pop("stats", None)
    counts.update(info)
    if extras is not None:
        extras["mf_stats"] = stats
        extras["peaks"] = mp.peaks
        extras["mf_den"] = mp.den
    if comp is None:
        comp = (md, np.zeros(shape)) if len(md) else None
    elif len(md):
        comp = (np.concatenate([comp[0], md]), comp[1])
    timing["mf_pass_s"] = time.perf_counter() - t0
    return comp


def _ext_pass(l2, det: np.ndarray, comp: tuple | None, ctx: PhotoContext, s: C.DetectSettings, counts: dict,
              extras: dict, timing: dict, dsat: np.ndarray | None = None) -> None:
    """The extended-source pass (psf.ext_pass, config EXT_*) after the matched-filter pass, on the same residual with
    every dot not in its model subtracted too. Its detections go to extras["ext"] (the ext file, ext_table), its maps'
    noise to extras["ext_stats"] (footer `ext`); the dots are not changed."""
    t0 = time.perf_counter()
    shape = ctx.sub.shape
    model = comp[1] if comp is not None else None
    resid = ctx.sub if model is None else np.where(ctx.bad, 0.0, ctx.sub - model)
    noise = ctx.noise0 if model is None else np.sqrt(ctx.noise0 ** 2 + (C.PSF_PASS_MODEL_FRAC * model) ** 2)
    dx, dy = np.asarray(det["x"], np.float64), np.asarray(det["y"], np.float64)
    in_model = np.zeros(len(det), bool)
    if len(det):  # unsaturated S/N >= PSF_PASS_PRIMARY_SNR dots are in the second pass's model when it ran; saturated
        ix, iy = pixel_of(dx, dy, shape)  # ones are never subtracted (the saturation guard keeps the pass away)
        sat = (ctx.f3[iy, ix] & SAT_BITS) != 0
        in_model = sat | ((det["snr"] >= C.PSF_PASS_PRIMARY_SNR) if model is not None and model.any() else False)
    cx = np.asarray(comp[0]["x"], np.float64) if comp is not None else np.zeros(0)
    cy = np.asarray(comp[0]["y"], np.float64) if comp is not None else np.zeros(0)
    if s.sat_guard_px > 0 and dsat is None:
        dsat = sat_distance(np.asarray(l2.flags) if l2.flags is not None else np.zeros(shape, np.int32))
    ep = ext_pass(resid, noise, ctx.bad, getattr(l2, "epsf", None), np.r_[dx, cx], np.r_[dy, cy],
                  sub_x=np.r_[dx[~in_model], cx], sub_y=np.r_[dy[~in_model], cy],
                  sat_dist=dsat if s.sat_guard_px > 0 else None, sat_guard_px=s.sat_guard_px, scales=C.EXT_SCALES,
                  min_snr=C.EXT_PASS_SNR, ratio=C.EXT_RATIO, dedup_px=C.EXT_DEDUP_PX, normalise=C.EXT_NORMALISE)
    info = dict(ep.info)
    counts.update({k: v for k, v in info.items() if not isinstance(v, dict)})
    extras["ext"] = {"x": ep.dots["x"].astype(np.float64), "y": ep.dots["y"].astype(np.float64),
                     "flux": ep.dots["flux"], "flux_err": ep.dots["flux_err"], "snr_ext": ep.snr_ext,
                     "snr_point": ep.snr_point, "scale": ep.scale, "flux_ext": ep.flux_ext}
    extras["ext_stats"] = {k: v for k, v in info.items() if isinstance(v, dict)}
    timing["ext_pass_s"] = time.perf_counter() - t0


def _measure_all(l2, det: np.ndarray, comp: tuple | None, ctx: PhotoContext, obs_id: str, detector: int, session: int,
                 visit: int, timing: dict, l2_own=None, ctx_own: PhotoContext | None = None,
                 own: np.ndarray | None = None, v2: bool = False) -> pd.DataFrame:
    """The dot rows of detections `det` on l2 (and the second pass's on the residual), brightest flux_ujy first.
    `own` (glow): those rows are measured on l2_own with ctx_own instead (a bright star's own dot on IMAGE)."""
    if own is not None and own.any():
        parts = [_measure_dots(l2_own, det[own], ctx_own, obs_id, detector, session, visit, {}, v2)]
        if (~own).any():
            parts.append(_measure_dots(l2, det[~own], ctx, obs_id, detector, session, visit, timing, v2))
        df = pd.concat(parts, ignore_index=True)
    elif len(det) or comp is None or not len(comp[0]):
        df = _measure_dots(l2, det, ctx, obs_id, detector, session, visit, timing, v2)
    else:
        df = None  # v2: matched-filter dots only
    if comp is not None and len(comp[0]):
        # measured on the residual: IMAGE minus the subtracted ePSF models of the brighter dots
        t0 = time.perf_counter()
        l2r = copy.copy(l2)
        l2r.data = (np.asarray(l2.data, np.float64) - comp[1]).astype(np.asarray(l2.data).dtype)
        dfc = _measure_dots(l2r, comp[0], ctx, obs_id, detector, session, visit, {}, v2)
        df = dfc if df is None else pd.concat([df, dfc], ignore_index=True)
        timing["psf_pass_measure_s"] = time.perf_counter() - t0
    if own is not None or (comp is not None and len(comp[0])):
        df = df.iloc[np.argsort(-df["flux_ujy"].to_numpy(np.float64), kind="stable")].reset_index(drop=True)
    return df


def glow_image(l2, ctx: PhotoContext, glow) -> object:
    """A shallow copy of l2 for detection: IMAGE minus the bright stars' glow outside their own pixels
    (core.glow.Glow.outside) and VARIANCE = (noise x k)^2 where the glow's noise factor k > 1 (noise = ctx.noise:
    max(sqrt(VARIANCE), background rms), so detect's max(sqrt(VARIANCE), rms) is noise x k there)."""
    model, k = glow.outside(C.GLOW_R_KEEP_PX)
    out = copy.copy(l2)
    data = np.asarray(l2.data)
    out.data = (data.astype(np.float64) - model).astype(data.dtype)
    up = k > 1.0
    nk = np.square(np.where(np.isfinite(ctx.noise), ctx.noise, 0.0) * k)
    if l2.variance is not None:
        var = np.asarray(l2.variance)
        out.variance = np.where(up, nk, var.astype(np.float64)).astype(var.dtype)
    else:
        out.variance = np.where(up, nk, 0.0).astype(np.float32)
    return out


def glow_dots(l2, obs_id: str, detector: int, session: int, visit: int, thresh: float, min_snr: float, timing: dict,
              ctx: PhotoContext | None, s: C.DetectSettings, counts: dict, extras: dict | None = None) -> pd.DataFrame:
    """dots_table with the glow model (contracts/dots.md "Glow model"): core.glow.fit_glow models every saturated
    core and every peak >= GLOW_MIN_PEAK_SNR; detection (and the guard and second pass) runs on IMAGE minus the glow
    outside each star's own pixels, with the noise scaled by the glow's local scatter. Each modelled star's own dot
    (the detection nearest it within max(GLOW_SELF_PX, core radius + 1.5 px)) is measured on IMAGE as before; other
    detections that close are pieces of it and dropped; the rest are measured on the glow-free image."""
    t0 = time.perf_counter()
    ctx0 = ctx if ctx is not None else photo_context(l2, timing)
    gl = fit_glow(l2.data, l2.flags, ctx0.noise, epsf=getattr(l2, "epsf", None), r_max=C.GLOW_R_MAX_PX,
                  min_peak_snr=C.GLOW_MIN_PEAK_SNR, max_stars=C.GLOW_MAX_STARS)
    timing["glow_s"] = time.perf_counter() - t0
    st = gl.stars
    counts["n_glow_stars"] = int(len(st))
    counts["n_glow_saturated"] = int(st["saturated"].sum()) if len(st) else 0
    counts["n_glow_extended"] = int((st["r_glow"] > 0).sum()) if len(st) else 0
    s0 = dataclasses.replace(s, glow=False)
    if not len(st):
        counts["n_glow_pieces"] = 0
        det, comp, ctx0 = _detections(l2, thresh, min_snr, timing, ctx0, s0, counts, extras)
        return empty_dots(s.v2) if det is None else _measure_all(l2, det, comp, ctx0, obs_id, detector, session, visit,
                                                                timing, v2=s.v2)
    l2g = glow_image(l2, ctx0, gl)
    ctxg = photo_context(l2g, {})
    det, comp, ctxg = _detections(l2g, thresh, min_snr, timing, ctxg, s0, counts, extras)
    ext = (extras or {}).get("ext")
    if ext is not None and len(ext["x"]):  # extended detections within a modelled star's reach are pieces of it
        keep = gl.near(ext["x"], ext["y"], C.GLOW_SELF_PX) < 0
        extras["ext"] = {k: np.asarray(v)[keep] for k, v in ext.items()}
        counts["n_ext_glow_pieces"] = int((~keep).sum())
    if det is None:
        counts["n_glow_pieces"] = 0
        return empty_dots(s.v2)
    own, piece = gl.own(det["x"], det["y"], C.GLOW_SELF_PX)
    n_piece = int(piece.sum())
    det, own = det[~piece], own[~piece]
    if comp is not None and len(comp[0]):
        cd = comp[0]
        cpiece = gl.near(cd["x"], cd["y"], C.GLOW_SELF_PX) >= 0
        n_piece += int(cpiece.sum())
        comp = (cd[~cpiece], comp[1])
    counts["n_glow_pieces"] = n_piece
    if len(det) == 0 and not (comp is not None and len(comp[0])):
        return empty_dots(s.v2)
    if own.any():
        # the star's own aperture flux on IMAGE (detect's r = 2 px aperture on IMAGE minus the background, as without
        # the glow model), not on IMAGE minus the glow
        det = det.copy()
        f, fe, _ = sep.sum_circle(np.ascontiguousarray(ctx0.sub, np.float64), det["x"][own].astype(np.float64),
                                  det["y"][own].astype(np.float64), DETECT_APERTURE_R_PX, err=ctx0.noise0,
                                  mask=np.ascontiguousarray(ctx0.bad), subpix=5)
        det["flux"][own], det["flux_err"][own] = f, fe
        with np.errstate(divide="ignore", invalid="ignore"):
            det["snr"][own] = np.where(fe > 0, f / fe, 0.0)
    return _measure_all(l2g, det, comp, ctxg, obs_id, detector, session, visit, timing, l2_own=l2, ctx_own=ctx0, own=own,
                        v2=s.v2)


def _measure_dots(l2, det: np.ndarray, ctx: PhotoContext, obs_id: str, detector: int, session: int, visit: int,
                  timing: dict, v2: bool = False) -> pd.DataFrame:
    """Everything dots_table measures for detections `det` (core.detect's dtype) on l2.data; v2 adds the
    DOT_V2_COLUMNS (partner columns NaN: process_photos fills them)."""
    data = np.asarray(l2.data)
    f3 = ctx.f3
    x, y = det["x"].astype(np.float64), det["y"].astype(np.float64)
    ix, iy = pixel_of(x, y, data.shape)
    sat = (f3[iy, ix] & SAT_BITS) != 0
    t0 = time.perf_counter()
    x, y, _ = recentroid_saturated(data, np.asarray(l2.flags), x, y, sat)
    timing["recentroid_s"] = time.perf_counter() - t0
    ix, iy = pixel_of(x, y, data.shape)
    px, py = peak_pixel(data, x, y)
    ra, dec = pixel_to_sky(l2.wcs, x, y)
    ra = ra % 360.0
    conv = ctx.conv
    if l2.wave is not None:
        wl, bw = l2.wave.wavelength(x, y), l2.wave.bandwidth(x, y)
    else:
        wl = bw = np.full(x.size, np.nan)
    t0 = time.perf_counter()
    elong, a_px, b_px, theta = shape_moments(data, ctx.bad, x, y)
    timing["shape_s"] = time.perf_counter() - t0
    zodi = np.asarray(l2.zodi)[iy, ix] if getattr(l2, "zodi", None) is not None else np.full(x.size, np.nan)
    t0 = time.perf_counter()
    psf_flux = psf_err = psf_chi2 = np.full(x.size, np.nan)
    if getattr(l2, "epsf", None) is not None:
        ph = psf_photometry(data, x, y, l2.epsf, noise=ctx.noise, bad=ctx.bad)
        psf_flux, psf_err, psf_chi2 = ph.flux * conv, ph.flux_err * conv, ph.chi2_nu
    timing["psf_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    pos_err = pos_err_arcsec(det["snr"])
    x_psf, y_psf, pos_err_psf, psf_ok = x, y, pos_err, np.zeros(x.size, bool)
    if C.DOTS_PSF_POSITION and getattr(l2, "epsf", None) is not None:  # saturated dots keep the wings centroid
        fit = ~sat & (det["snr"] >= C.PSF_POS_MIN_SNR)
        if v2:  # matched-filter dots (aperture S/N may be < 5) get the ePSF position too
            fit |= ~sat & ((det["sep_flag"] & C.SEP_FLAG_MF_PASS) != 0)
        x_psf, y_psf, pos_err_psf, psf_ok = dot_positions(data, x, y, l2.epsf, noise=ctx.noise, bad=ctx.bad, pos_err=pos_err,
                                                          fit=fit)
    ra_psf, dec_psf = pixel_to_sky(l2.wcs, x_psf, y_psf)
    timing["psf_pos_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    apf, ape = aperture_fluxes(l2, ctx, x, y)
    timing["apertures_s"] = time.perf_counter() - t0
    df = pd.DataFrame({
        "obs_id": obs_id,
        "detector": detector,
        "mjd": l2.mjd_avg,
        "session": session,
        "visit": visit,
        "ra": ra,
        "dec": dec,
        "pos_err_arcsec": pos_err,
        "x": x,
        "y": y,
        "flux_ujy": det["flux"] * conv,
        "flux_err_ujy": det["flux_err"] * conv,
        "wavelength_um": wl,
        "bandwidth_um": bw,
        "elong": elong,
        "snr": det["snr"],
        "flags": f3[iy, ix],
        "flags_peak": np.asarray(l2.flags).astype(np.int32)[py, px],
        "cell": cell_of(ra, dec),
        "zodi_mjysr": zodi,
        "bkg_mjysr": ctx.bkg_level[iy, ix],
        "peak_mjysr": det["peak"],
        "a_arcsec": a_px * C.PIXEL_ARCSEC,
        "b_arcsec": b_px * C.PIXEL_ARCSEC,
        "theta_deg": theta,
        "flux_psf_ujy": psf_flux,
        "flux_psf_err_ujy": psf_err,
        "psf_chi2_nu": psf_chi2,
        "x_psf": x_psf,
        "y_psf": y_psf,
        "ra_psf": ra_psf % 360.0,
        "dec_psf": dec_psf,
        "pos_err_psf_arcsec": pos_err_psf,
        "psf_pos_ok": psf_ok,
        "sep_flag": det["sep_flag"],
    }).astype(DOT_COLUMNS)
    df["flux_ap_ujy"] = list(apf.astype(np.float32))
    df["flux_ap_err_ujy"] = list(ape.astype(np.float32))
    if v2:
        t0 = time.perf_counter()
        for c, v in v2_columns(l2, ctx, x, y).items():
            df[c] = v
        timing["v2_columns_s"] = time.perf_counter() - t0
    return df


def _disc_count(mask: np.ndarray, x: np.ndarray, y: np.ndarray, r: float) -> np.ndarray:
    """Pixels of a boolean map whose centres lie within r px of each (x, y)."""
    h = int(np.ceil(r)) + 1
    ix, iy = np.rint(x).astype(np.int64), np.rint(y).astype(np.int64)
    v, inside = gather(np.asarray(mask, bool), ix, iy, h, fill=None)
    k = np.arange(-h, h + 1)
    dx = (ix - x)[:, None, None] + k[None, None, :]
    dy = (iy - y)[:, None, None] + k[None, :, None]
    near = (dx ** 2 + dy ** 2) <= r * r
    return (v & inside & near).sum(axis=(1, 2))


def v2_columns(l2, ctx: PhotoContext, x: np.ndarray, y: np.ndarray) -> dict:
    """snr_mf, n_transient, n_masked of dots at (x, y) on l2.data (extraction v2); partner columns NaN."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    n = x.size
    out = {"snr_mf": np.zeros(n, np.float32), "n_transient": np.zeros(n, np.int8), "n_masked": np.zeros(n, np.int8),
           "partner_flux_ujy": np.full(n, np.nan, np.float32), "partner_err_ujy": np.full(n, np.nan, np.float32)}
    if n == 0:
        return out
    sub = np.where(ctx.bad, 0.0, np.asarray(l2.data, np.float64) - ctx.bkg_level)
    out["snr_mf"] = mf_at(sub, ctx.noise0, ctx.bad, x, y, getattr(l2, "epsf", None)).astype(np.float32)
    if getattr(l2, "flags", None) is not None:
        tr = (np.asarray(l2.flags).astype(np.int64) & TRANSIENT) != 0
        out["n_transient"] = np.minimum(_disc_count(tr, x, y, C.DOTS_TRANSIENT_R_PX), 127).astype(np.int8)
    out["n_masked"] = np.minimum(_disc_count(ctx.bad, x, y, DETECT_APERTURE_R_PX), 127).astype(np.int8)
    return out


# --- forced photometry at reference-catalogue positions ------------------------------------------
FORCED_COLUMNS = {
    "obs_id": "string", "detector": "int8", "mjd": "float64", "ref_id": "string",
    "ra": "float64", "dec": "float64", "x": "float32", "y": "float32",
    "flux_psf_ujy": "float32", "flux_psf_err_ujy": "float32", "psf_chi2_nu": "float32",
    "flux_ap2_ujy": "float32", "flux_ap2_err_ujy": "float32",
    "wavelength_um": "float32", "bandwidth_um": "float32", "zodi_mjysr": "float32", "bkg_mjysr": "float32",
    "flags": "int32",
}


def forced_rel(obs_id: str, det: int) -> str:
    return f"forced/{obs_id}D{int(det)}.parquet"


def refcat_rel(zone: str) -> str:
    return f"refcat/{zone}_forced.parquet"


def empty_forced() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in FORCED_COLUMNS.items()})


@dataclass
class RefCat:
    ref_id: np.ndarray
    ra: np.ndarray
    dec: np.ndarray
    epoch_mjd: np.ndarray
    pmra: np.ndarray   # mas/yr incl. cos dec; NaN -> 0
    pmdec: np.ndarray
    xyz: np.ndarray    # unit vectors, for the cone pre-filter

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> "RefCat":
        ra = df["ra"].to_numpy(np.float64)
        dec = df["dec"].to_numpy(np.float64)
        return cls(df["ref_id"].astype(str).to_numpy(), ra, dec, df["epoch_mjd"].to_numpy(np.float64),
                   np.nan_to_num(df["pmra"].to_numpy(np.float64)) if "pmra" in df else np.zeros(ra.size),
                   np.nan_to_num(df["pmdec"].to_numpy(np.float64)) if "pmdec" in df else np.zeros(ra.size),
                   _unit(ra, dec))


def _unit(ra, dec) -> np.ndarray:
    r, d = np.deg2rad(ra), np.deg2rad(dec)
    return np.stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)], axis=-1)


def photo_cone(l2) -> tuple[float, float, float]:
    """(ra, dec, radius) in degrees of a cone holding the whole photo: centre pixel, half diagonal + 0.1°."""
    ny, nx = l2.data.shape
    cra, cdec = pixel_to_sky(l2.wcs, np.array([(nx - 1) / 2.0]), np.array([(ny - 1) / 2.0]))
    return float(cra[0]), float(cdec[0]), float(np.hypot(nx, ny) / 2.0 * C.PIXEL_ARCSEC / 3600.0 + 0.1)


def forced_targets(l2, ref: RefCat, zone: C.Zone | None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Reference targets in this photo: moved to MJD-AVG (proper motion only), inside the frame by
    >= FORCED_EDGE_PX and (zone list only, zone not None) inside the zone box + FORCED_ZONE_MARGIN_DEG.
    Returns (index, ra, dec, x, y)."""
    ny, nx = l2.data.shape
    cra, cdec, half_diag = photo_cone(l2)
    near = np.flatnonzero(ref.xyz @ _unit(cra, cdec) > np.cos(np.deg2rad(half_diag)))
    ra = np.empty(near.size)
    dec = np.empty(near.size)
    for ep in np.unique(ref.epoch_mjd[near]):
        g = ref.epoch_mjd[near] == ep
        k = near[g]
        ra[g], dec[g] = propagate(ref.ra[k], ref.dec[k], ref.pmra[k], ref.pmdec[k], l2.mjd_avg, epoch_mjd=float(ep))
    ra %= 360.0
    keep = np.ones(near.size, bool)
    if zone is not None:
        m = C.FORCED_ZONE_MARGIN_DEG
        keep &= (ra >= zone.ra_min - m) & (ra < zone.ra_max + m) & (dec >= zone.dec_min - m) & (dec < zone.dec_max + m)
    x = np.full(near.size, np.nan)
    y = np.full(near.size, np.nan)
    if keep.any():
        x[keep], y[keep] = sky_to_pixel(l2.wcs, ra[keep], dec[keep])
    e = C.FORCED_EDGE_PX
    keep &= np.isfinite(x) & np.isfinite(y) & (x >= e) & (x <= nx - 1 - e) & (y >= e) & (y <= ny - 1 - e)
    return near[keep], ra[keep], dec[keep], x[keep], y[keep]


def forced_table(l2, ctx: PhotoContext, obs_id: str, detector: int, ref: RefCat, zone: C.Zone | None) -> pd.DataFrame:
    """ePSF fit at the fixed predicted position + the r = 2 px aperture (as flux_ujy of a dot) for
    every reference target in the photo, detected or not (negative fluxes kept)."""
    idx, ra, dec, x, y = forced_targets(l2, ref, zone)
    if idx.size == 0:
        return empty_forced()
    if len(idx) > C.FORCED_WARN_TARGETS:
        L.warning("%sD%d: %d forced targets (budget %d)", obs_id, detector, len(idx), C.FORCED_WARN_TARGETS)
    n = idx.size
    psf_flux = psf_err = psf_chi2 = np.full(n, np.nan)
    if getattr(l2, "epsf", None) is not None:
        ph = psf_photometry(np.asarray(l2.data), x, y, l2.epsf, noise=ctx.noise, bad=ctx.bad)
        psf_flux, psf_err, psf_chi2 = ph.flux * ctx.conv, ph.flux_err * ctx.conv, ph.chi2_nu
    f2, e2, _ = sep.sum_circle(ctx.sub, x, y, 2.0, err=ctx.noise0, mask=np.ascontiguousarray(ctx.bad), subpix=5)
    ix, iy = pixel_of(x, y, l2.data.shape)
    if l2.wave is not None:
        wl, bw = l2.wave.wavelength(x, y), l2.wave.bandwidth(x, y)
    else:
        wl = bw = np.full(n, np.nan)
    zodi = np.asarray(l2.zodi)[iy, ix] if getattr(l2, "zodi", None) is not None else np.full(n, np.nan)
    return pd.DataFrame({
        "obs_id": obs_id, "detector": detector, "mjd": l2.mjd_avg, "ref_id": ref.ref_id[idx],
        "ra": ra, "dec": dec, "x": x, "y": y,
        "flux_psf_ujy": psf_flux, "flux_psf_err_ujy": psf_err, "psf_chi2_nu": psf_chi2,
        "flux_ap2_ujy": f2 * ctx.conv, "flux_ap2_err_ujy": e2 * ctx.conv,
        "wavelength_um": wl, "bandwidth_um": bw, "zodi_mjysr": zodi, "bkg_mjysr": ctx.bkg_level[iy, ix],
        "flags": ctx.f3[iy, ix],
    }).astype(FORCED_COLUMNS)


# --- photo metadata -----------------------------------------------------------------------------
def _jsonable(v):
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return float(v) if np.isfinite(v) else None
    if v is None:
        return None
    return str(v).strip()


def header_meta(header) -> dict:
    out = {k: _jsonable(header.get(k)) for k in C.PHOTO_META_HEADER_KEYS}
    for k in header.keys():
        if k.startswith(C.PHOTO_META_HEADER_PREFIXES):
            out[k] = _jsonable(header[k])
    return out


def epsf_calib_id(header) -> str | None:
    """The ePSF calibration file id from the IMAGE header HISTORY ('[CALIB] epsf <id>')."""
    for v in header.get("HISTORY", []):
        s = str(v).strip()
        if s.startswith("[CALIB] epsf "):
            return s.split()[-1]
    return None


def block_means(a: np.ndarray | None, n: int = C.PHOTO_META_GRID) -> list | None:
    """n × n block means of a 2-D map (NaN ignored), rows = y; block edges round(linspace(0, size, n + 1))."""
    if a is None:
        return None
    a = np.asarray(a, np.float64)
    ey = np.round(np.linspace(0, a.shape[0], n + 1)).astype(int)[:-1]
    ex = np.round(np.linspace(0, a.shape[1], n + 1)).astype(int)[:-1]
    ok = np.isfinite(a)
    s = np.add.reduceat(np.add.reduceat(np.where(ok, a, 0.0), ey, axis=0), ex, axis=1)
    c = np.add.reduceat(np.add.reduceat(ok.astype(np.float64), ey, axis=0), ex, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = s / c
    return [[None if not np.isfinite(v) else float(f"{v:.5g}") for v in row] for row in m]


def _robust_sigma(v: np.ndarray) -> float:
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size else float("nan")


def calibration_meta(dots: pd.DataFrame, forced: pd.DataFrame | None) -> dict:
    """Astrometric offset of the dots against forced Gaia targets, and the PSF size and ellipticity
    from the second moments of bright unsaturated dots."""
    out: dict = {}
    if forced is not None and len(forced) and len(dots):
        g = forced[forced["ref_id"].str.startswith("gaia:")]
        d = dots[dots["snr"] > C.CALIB_ASTROM_MIN_SNR]
        if len(g) and len(d):
            gx = _unit(g["ra"].to_numpy(), g["dec"].to_numpy())
            dx = _unit(d["ra"].to_numpy(), d["dec"].to_numpy())
            dist, j = cKDTree(gx).query(dx, distance_upper_bound=np.deg2rad(C.CALIB_ASTROM_MATCH_ARCSEC / 3600.0))
            m = np.isfinite(dist)
            dra = ((d["ra"].to_numpy()[m] - g["ra"].to_numpy()[j[m]] + 180.0) % 360.0 - 180.0) \
                * np.cos(np.deg2rad(d["dec"].to_numpy()[m])) * 3600.0
            ddec = (d["dec"].to_numpy()[m] - g["dec"].to_numpy()[j[m]]) * 3600.0
            out["astrometry_vs_gaia"] = {
                "n": int(m.sum()),
                "dra_cosdec_arcsec": float(np.median(dra)) if m.any() else None,
                "ddec_arcsec": float(np.median(ddec)) if m.any() else None,
                "sigma_ra_arcsec": _robust_sigma(dra) if m.any() else None,
                "sigma_dec_arcsec": _robust_sigma(ddec) if m.any() else None,
                "min_snr": C.CALIB_ASTROM_MIN_SNR, "match_arcsec": C.CALIB_ASTROM_MATCH_ARCSEC,
            }
    if len(dots):
        s = dots[(dots["snr"] > C.CALIB_PSF_MIN_SNR) & ((dots["flags"] & SAT_BITS) == 0)
                 & np.isfinite(dots["a_arcsec"]) & (dots["b_arcsec"] > 0)]
        if len(s):
            a, b = s["a_arcsec"].to_numpy(np.float64), s["b_arcsec"].to_numpy(np.float64)
            out["psf_moments"] = {
                "n": int(len(s)),
                "fwhm_arcsec": float(np.median(2.3548 * np.sqrt(a * b))),
                "ellipticity": float(np.median(1.0 - b / a)),
                "theta_deg": float(np.median(s["theta_deg"])),
                "min_snr": C.CALIB_PSF_MIN_SNR,
            }
        r = (dots["flux_psf_ujy"] / dots["flux_ujy"]).to_numpy(np.float64)
        sel = (dots["snr"] > C.CALIB_PSF_MIN_SNR).to_numpy() & ((dots["flags"] & SAT_BITS) == 0).to_numpy() & np.isfinite(r)
        if sel.any():
            out["psf_over_aperture_flux"] = {"n": int(sel.sum()), "median": float(np.median(r[sel])),
                                             "min_snr": C.CALIB_PSF_MIN_SNR}
    return out


def detect_meta(deblend_cont: float = C.DOTS_DEBLEND_CONT, dots_dir: str = C.DOTS_DIR,
                settings: C.DetectSettings | None = None, counts: dict | None = None,
                detector: int | None = None) -> dict:
    """The detection settings a dot file was made with (photo metadata `detect`): files made with another
    setting live in another folder, and this says which setting a file has. Fine detection v2 settings (clean_param,
    saturation guard, ePSF second pass) add clean_param, sat_guard_px, psf_pass and the counts n_guard_dropped,
    n_psf_pass; the glow model adds glow, glow_config and n_glow_stars, n_glow_saturated, n_glow_extended,
    n_glow_pieces."""
    out = {"thresh": C.DETECT_THRESH, "min_snr": C.DOT_MIN_SNR, "deblend_cont": float(deblend_cont), "dots_dir": dots_dir}
    if settings is not None and settings.v2:
        out["v2"] = True
        out["mf_pass_snr"] = C.mf_pass_snr(detector)
        out["dark_holes"] = bool(C.DOTS_V2_HOLES)
    if settings is not None and settings.glow:
        out["glow"] = True
        out["glow_config"] = {"r_max_px": C.GLOW_R_MAX_PX, "min_peak_snr": C.GLOW_MIN_PEAK_SNR,
                              "max_stars": C.GLOW_MAX_STARS, "self_px": C.GLOW_SELF_PX, "r_keep_px": C.GLOW_R_KEEP_PX}
    if settings is not None and (settings.clean_param != 1.0 or settings.sat_guard_px > 0 or settings.psf_pass
                                 or settings.glow):
        out.update({"clean_param": float(settings.clean_param), "sat_guard_px": float(settings.sat_guard_px),
                    "psf_pass": bool(settings.psf_pass)})
        if settings.psf_pass:
            out["psf_pass_config"] = {"primary_snr": C.PSF_PASS_PRIMARY_SNR, "r_px": C.PSF_PASS_R_PX,
                                      "model_frac": C.PSF_PASS_MODEL_FRAC, "sat_r_px": C.PSF_PASS_SAT_R_PX,
                                      "mirror_frac": C.PSF_PASS_MIRROR_FRAC, "mirror_sigma": C.PSF_PASS_MIRROR_SIGMA,
                                      "sep_flag": C.SEP_FLAG_PSF_PASS}
        out.update({k: int(v) for k, v in (counts or {}).items()})
    return out


def photo_meta_dict(l2, obs_id: str, detector: int, st: dict, ctx: PhotoContext | None,
                    dots: pd.DataFrame, forced: pd.DataFrame | None, settings: dict | None = None) -> dict:
    zodi = getattr(l2, "zodi", None)
    meta = {
        "schema": 1,
        "obs_id": obs_id, "detector": int(detector), "status": st.get("status") or "done",
        "pipeline_version": _jsonable(l2.primary_header.get("VERSION")) if hasattr(l2, "primary_header") else None,
        "epsf_calib": epsf_calib_id(l2.header),
        "n_dots": int(len(dots)),
        "n_forced": None if forced is None else int(len(forced)),
        "detect": settings if settings is not None else detect_meta(),
        "read_s": st.get("read_s"), "detect_s": st.get("detect_s"), "forced_s": st.get("forced_s"),
        "stages_s": {k: round(float(v), 3) for k, v in (ctx.timing if ctx else {}).items()},
        "zodi_median_mjysr": float(np.nanmedian(zodi)) if zodi is not None else None,
        "bkg_median_mjysr": float(np.median(ctx.bkg_level)) if ctx is not None else None,
        "ujy_per_mjysr": float(ctx.conv) if ctx is not None else None,
        "aperture_radii_px": list(C.AP_RADII_PX), "aperture_bkg_annulus_px": list(C.AP_BKG_ANNULUS_PX),
        "psf_fit": {"half_px": C.PSF_FIT_HALF_PX, "model_frac": C.PSF_MODEL_FRAC, "noise": C.PSF_NOISE,
                    "position": bool(C.DOTS_PSF_POSITION and getattr(l2, "epsf", None) is not None),
                    "pos_min_snr": C.PSF_POS_MIN_SNR, "pos_err_floor_arcsec": C.PSF_POS_ERR_FLOOR_ARCSEC},
        "header": header_meta(l2.header),
        "calibration": calibration_meta(dots, forced),
        "grids": {"n": C.PHOTO_META_GRID, "unit": "MJy/sr",
                  "bkg_mjysr": block_means(ctx.bkg_level) if ctx is not None else None,
                  "zodi_mjysr": block_means(zodi)},
    }
    return _clean(meta)


def _clean(v):
    """JSON-safe copy: numpy scalars to Python, NaN/inf to None."""
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, str) or v is None:
        return v
    return _jsonable(v)


def dots_arrow(df: pd.DataFrame, meta: dict | None = None):
    """The dot table as a pyarrow Table: list columns as fixed_size_list<float32>[len(AP_RADII_PX)],
    photo metadata under C.PHOTO_META_KEY."""
    df = as_dots(df)
    scal = df[list(DOT_COLUMNS)]
    t = pa.Table.from_pandas(scal, preserve_index=False)
    n = len(C.AP_RADII_PX)
    for c in DOT_LIST_COLUMNS:
        flat = np.stack(df[c].to_numpy()).astype(np.float32).ravel() if len(df) else np.zeros(0, np.float32)
        t = t.append_column(c, pa.FixedSizeListArray.from_arrays(pa.array(flat, pa.float32()), n))
    for c, typ in DOT_V2_COLUMNS.items():
        if c in df:
            t = t.append_column(c, pa.array(df[c].to_numpy(typ), pa.from_numpy_dtype(np.dtype(typ))))
    md = dict(t.schema.metadata or {})
    if meta is not None:
        md[C.PHOTO_META_KEY.encode()] = json.dumps(_clean(meta), allow_nan=False).encode()
    return t.replace_schema_metadata(md)


def parquet_bytes(table) -> bytes:
    """Parquet bytes for a dot or forced table: zstd, byte-stream-split floats, no dictionary for
    numbers (unique noisy floats: 10.5 -> 6.5 MB on the Cygnus photo's dot file)."""
    import pyarrow.parquet as pq

    def is_float(t) -> bool:
        return pa.types.is_floating(t) or (pa.types.is_fixed_size_list(t) and pa.types.is_floating(t.value_type))

    floats = [f.name for f in table.schema if is_float(f.type)]
    strings = [f.name for f in table.schema if pa.types.is_string(f.type) or pa.types.is_large_string(f.type)]
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd", compression_level=3, use_dictionary=strings,
                   use_byte_stream_split=floats or False)
    return buf.getvalue()


def _write(store: Store, rel: str, table) -> int:
    data = parquet_bytes(table)
    store.write_bytes(rel, data)
    return len(data)


def photo_meta(src) -> dict | None:
    """The `skyshift_photo` metadata of a dot file: a local path, an fsspec URL (gs://...), bytes or
    a file object. None if the file has none."""
    import fsspec
    import pyarrow.parquet as pq

    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    if isinstance(src, (str, os.PathLike)):
        with fsspec.open(str(src), "rb") as f:
            md = pq.read_schema(f).metadata
    else:
        md = pq.read_schema(src).metadata
    raw = (md or {}).get(C.PHOTO_META_KEY.encode())
    return None if raw is None else json.loads(raw)


def bad_finast(value) -> bool:
    """IMAGE header FINAST != 0 means the astrometric solution failed (missing key = fine)."""
    if value is None or str(value).strip() == "":
        return False
    try:
        return int(value) != C.FINAST_OK
    except (TypeError, ValueError):
        return True


# --- worker ------------------------------------------------------------------------------------
_STORE: Store | None = None
_EPSF_CACHE: dict[tuple[int, str], EPSF] = {}
_REFCAT: dict[tuple[str, str], RefCat | None] = {}


def _store(root: str) -> Store:
    global _STORE
    if _STORE is None or _STORE.root != root.rstrip("/"):
        _STORE = Store(root)
    return _STORE


_SKY: dict[str, "forced_sky.SkyList"] = {}
_SKY_RED: dict[str, "forced_sky.SkyList"] = {}
_SKY_REF: dict[tuple, RefCat] = {}  # the last photo's partition set -> RefCat (neighbouring photos share it)


def sky_list(store: Store) -> "forced_sky.SkyList | None":
    """The sky-wide forced list of this store if it is complete (its manifest exists) and
    C.FORCED_SKY_MODE isn't "off"; checked once per process."""
    if C.FORCED_SKY_MODE == "off":
        return None
    if store.root not in _SKY:
        _SKY[store.root] = forced_sky.SkyList(store)
        if _SKY[store.root].available:
            L.info("dots: forced targets from the sky list %s/%s", store.root, C.FORCED_SKY_DIR)
    sk = _SKY[store.root]
    return sk if sk.available else None


def sky_red_list(store: Store) -> "forced_sky.SkyList | None":
    """The sky-wide red layer (forced_sky.build_red) of this store if it is complete and C.FORCED_SKY_RED_MODE isn't
    "off"; checked once per process."""
    if C.FORCED_SKY_RED_MODE == "off":
        return None
    if store.root not in _SKY_RED:
        _SKY_RED[store.root] = forced_sky.SkyList(store, C.FORCED_SKY_RED_DIR)
        if _SKY_RED[store.root].available:
            L.info("dots: red forced targets from %s/%s", store.root, C.FORCED_SKY_RED_DIR)
    sk = _SKY_RED[store.root]
    return sk if sk.available else None


def _sky_refcat(sk: "forced_sky.SkyList", l2, red: "forced_sky.SkyList | None" = None) -> RefCat:
    """RefCat of the partitions this photo's cone overlaps (photo_cone + FORCED_SKY_PAD_DEG): the Gaia layer, then
    the red layer's rows when it is given."""
    ra, dec, r = photo_cone(l2)
    pix = tuple(int(p) for p in forced_sky.partitions_for_cone(ra, dec, r, sk.nside))
    key = (sk.store.root, pix, red is not None)
    if key not in _SKY_REF:
        _SKY_REF.clear()
        df = sk.frame(pix)
        if red is not None:
            df = pd.concat([df, red.frame(pix)], ignore_index=True)
        _SKY_REF[key] = RefCat.from_frame(df)
    return _SKY_REF[key]


def forced_reference(store: Store, l2, zone: str | None) -> tuple[RefCat | None, C.Zone | None, str]:
    """Where a photo's forced targets come from: the sky list for its whole footprint (no zone box) when it is
    complete, else refcat/<zone>_forced.parquet limited to the zone box as before. Returns (ref, zone box, origin)."""
    sk = sky_list(store)
    if sk is not None:
        red = sky_red_list(store)
        return _sky_refcat(sk, l2, red), None, "sky+red" if red is not None else "sky"
    if not zone:
        return None, None, ""
    return _refcat(store, zone), C.ZONES.get(zone), "zone"


def _refcat(store: Store, zone: str) -> RefCat | None:
    key = (store.root, zone)
    if key not in _REFCAT:
        rel = refcat_rel(zone)
        if store.exists(rel):
            _REFCAT[key] = RefCat.from_frame(store.read_parquet(rel))
        else:
            L.warning("dots: %s/%s missing: no forced photometry", store.root, rel)
            _REFCAT[key] = None
    return _REFCAT[key]


def load_photo(url: str):
    """open_level2 with VARIANCE, ZODI (config) and the ePSF (read once per worker per detector and
    ePSF calibration file: it is the same table in every photo that names that file)."""
    want = C.DOTS_READ_EPSF and not _EPSF_CACHE  # the first photo of a worker reads it with the rest
    l2 = open_level2(url, variance=True, zodi=C.DOTS_READ_ZODI, epsf=want)
    if not C.DOTS_READ_EPSF:
        return l2
    cid = epsf_calib_id(l2.header)
    key = (int(l2.detector), cid) if cid else None
    if l2.epsf is not None:
        if key:
            _EPSF_CACHE[key] = l2.epsf
    elif key and key in _EPSF_CACHE:
        l2.epsf = _EPSF_CACHE[key]
    else:
        l2.epsf = open_level2(url, region=(0, 1, 0, 1), epsf=True).epsf  # small reads + the table
        if key and l2.epsf is not None:
            _EPSF_CACHE[key] = l2.epsf
    return l2


def _new_stats(obs_id: str, detector: int) -> dict:
    return {"obs_id": obs_id, "detector": int(detector), "status": "", "n_dots": 0, "n_forced": 0, "read_s": 0.0,
            "detect_s": 0.0, "forced_s": 0.0, "write_s": 0.0, "bytes": 0, "forced_bytes": 0, "error": ""}


def _already_done(store: Store, rel: str, st: dict) -> bool:
    """True (status "skipped") if the dot file exists; an exists-check error is noted and ignored."""
    try:
        if store.exists(rel):
            st["status"] = "skipped"
            return True
    except Exception as e:  # noqa: BLE001
        st["error"] = f"exists check: {e}"
    return False


def _load_retrying(url: str, st: dict):
    """load_photo with C.NET_RETRIES attempts. Returns the photo, or None with st status "failed"."""
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            t0 = time.time()
            l2 = load_photo(url)
            st["read_s"] = round(time.time() - t0, 2)
            return l2
        except Exception as e:  # noqa: BLE001 - network or decode errors: retry, then skip
            st["error"] = f"{type(e).__name__}: {e}"[:300]
            if attempt == C.NET_RETRIES:
                st["status"] = "failed"
                return None
            time.sleep(5 * attempt)
    return None


@dataclass
class Pending:
    """A measured photo not yet written (extraction v2 waits for its partner detector to fill partner_*)."""
    rel: str
    df: pd.DataFrame
    meta: dict
    st: dict
    partner: "PartnerImage | None" = None
    peaks: pd.DataFrame | None = None
    peaks_rel: str | None = None
    ext: pd.DataFrame | None = None
    ext_rel: str | None = None


def _measure(store: Store, l2, obs_id: str, detector: int, session: int, visit: int, zone: str | None,
             st: dict, shared: tuple | None = None) -> Pending | None:
    """Everything after the read but the dot file's write: dots, forced photometry (written), metadata; v2 also the
    peaks table and the partner image. None (st status "failed") on an error; never raises. shared: the raw photo's
    raw_background_holes (the fused worker computed it for the session stack too)."""
    settings = C.zone_detect(zone)
    ddir, deblend = settings.folder, settings.deblend_cont
    counts: dict = {}
    extras: dict = {}
    rel = dots_rel(obs_id, detector, ddir)
    try:
        if int(l2.detector) != int(detector):
            raise ValueError(f"header DETECTOR {l2.detector} != photo list {detector}")
        t0 = time.time()
        finast = l2.header.get("FINAST")
        ctx = None
        forced = None
        if bad_finast(finast):
            df = empty_dots(settings.v2)  # bad astrometry: an empty file marks the photo done
            st["finast"] = str(finast)
        else:
            timing: dict = {}
            pre = None
            if settings.v2 and C.DOTS_V2_HOLES and getattr(l2, "flags", None) is not None:
                l2, holes, pre = mask_dark_holes(l2, timing, shared=shared)
                extras["dark_holes"] = holes
            ctx = photo_context(l2, timing, pre=pre)
            df = dots_table(l2, obs_id, detector, session, visit, timing=timing, ctx=ctx, settings=settings, counts=counts,
                            extras=extras)
        st["detect_s"] = round(time.time() - t0, 2)
        st["n_dots"] = int(len(df))
        ref, zobj, origin = forced_reference(store, l2, zone) if ctx is not None else (None, None, "")
        if ref is not None:
            st["forced_from"] = origin
            t0 = time.time()
            forced = forced_table(l2, ctx, obs_id, detector, ref, zobj)
            st["forced_s"] = round(time.time() - t0, 2)
            st["n_forced"] = int(len(forced))
            frel = forced_rel(obs_id, detector)
            exists = ddir != C.DOTS_DIR and store.exists(frel)
            if exists and not settings.v2:
                st["forced_kept"] = True  # never overwritten from a re-extraction into another dots folder
            else:
                # v2 (the one rerun of every photo) rewrites it: forced photometry on the hole-masked image, with the
                # red layer's targets (docs/extraction-v2.md §12.5); keeping the sky run's file would leave both out
                if exists:
                    st["forced_replaced"] = True
                ft = pa.Table.from_pandas(forced, preserve_index=False)
                st["forced_bytes"] = _retry(lambda: _write(store, frel, ft))
        st["status"] = "done_finast" if "finast" in st else "done"
        meta = photo_meta_dict(l2, obs_id, detector, st, ctx, df, forced,
                               detect_meta(deblend, ddir, settings, counts, detector))
        out = Pending(rel, df, meta, st)
        if settings.v2:
            t0 = time.time()
            out.partner = None if ctx is None else partner_image(l2, ctx)
            meta.update(v2_meta(l2, ctx, extras))
            if ctx is not None:
                out.peaks = peaks_table(l2, ctx, extras.get("peaks"), df)
                out.peaks_rel = peaks_rel(obs_id, detector, ddir)
                if C.EXT_PASS:
                    out.ext = ext_table(l2, ctx, extras.get("ext"), df)
                    out.ext_rel = ext_rel(obs_id, detector, ddir)
            st["v2_s"] = round(time.time() - t0, 2)
        return out
    except Exception as e:  # noqa: BLE001
        st["status"] = "failed"
        st["error"] = f"{type(e).__name__}: {e}"[:300]
        return None


def _finish(store: Store, p: Pending | None, st: dict) -> dict:
    """Write a measured photo: the peaks and ext files (v2), then the dot file (last: a dot file = photo fully done)."""
    if p is None:
        return st
    try:
        if p.peaks is not None:
            pt = pa.Table.from_pandas(p.peaks, preserve_index=False)
            st["peaks_bytes"] = _retry(lambda: _write(store, p.peaks_rel, pt))
            st["n_peaks"] = int(len(p.peaks))
        if p.ext is not None:
            et = pa.Table.from_pandas(p.ext, preserve_index=False)
            st["ext_bytes"] = _retry(lambda: _write(store, p.ext_rel, et))
            st["n_ext"] = int(len(p.ext))
        if "partner_flux_ujy" in p.df:
            p.meta["n_partner"] = int(np.isfinite(p.df["partner_flux_ujy"].to_numpy(np.float64)).sum())
        table = dots_arrow(p.df, p.meta)
        t0 = time.time()
        st["bytes"] = _retry(lambda: _write(store, p.rel, table))
        st["write_s"] = round(time.time() - t0, 2)
        st["error"] = ""
    except Exception as e:  # noqa: BLE001
        st["status"] = "failed"
        st["error"] = f"{type(e).__name__}: {e}"[:300]
    return st


def _measure_and_write(store: Store, l2, obs_id: str, detector: int, session: int, visit: int,
                       zone: str | None, st: dict) -> dict:
    """Everything after the read: dots, forced photometry, metadata, files. Never raises.
    The zone's detection setting and folder come from config.zone_dots. In a folder other than the sky run's,
    an existing forced/ file is kept (forced photometry doesn't depend on deblending; other zones read it), except under
    extraction v2, which rewrites it (dark-hole mask, red layer; docs/extraction-v2.md §12.5)."""
    return _finish(store, _measure(store, l2, obs_id, detector, session, visit, zone, st), st)


# --- extraction v2: peaks file, photo metadata, partner detector -------------------------------------------------------

PEAK_COLUMNS = {"x": "float32", "y": "float32", "ra": "float64", "dec": "float64", "snr_mf": "float32",
                "flux_mf_ujy": "float32", "flags": "int32", "n_transient": "int8", "why": "int8"}


def peaks_rel(obs_id: str, det: int, folder: str) -> str:
    """The peaks file of a v2 dot file: <folder>_peaks/<obs_id>D<det>.parquet."""
    return f"{folder}_peaks/{obs_id}D{int(det)}.parquet"


def peaks_table(l2, ctx: PhotoContext, peaks: dict | None, dots: pd.DataFrame | None = None) -> pd.DataFrame:
    """The matched filter's other peaks (psf.mf_pass): position, S/N, flux, flags (3 × 3 OR), TRANSIENT count, why;
    none within MF_PASS_DEDUP_PX of a final dot (dots move a little when measured)."""
    empty = pd.DataFrame({c: pd.Series(dtype=t) for c, t in PEAK_COLUMNS.items()})
    if not peaks or len(peaks["x"]) == 0:
        return empty
    x, y = np.asarray(peaks["x"], np.float64), np.asarray(peaks["y"], np.float64)
    ix, iy = np.asarray(peaks["ix"]), np.asarray(peaks["iy"])
    keep = np.ones(x.size, bool)
    if dots is not None and len(dots):
        d, _ = cKDTree(dots[["x", "y"]].to_numpy(np.float64)).query(np.c_[x, y], k=1)
        keep = d > C.MF_PASS_DEDUP_PX
    if not keep.any():
        return empty
    x, y, ix, iy = x[keep], y[keep], ix[keep], iy[keep]
    peaks = {k: np.asarray(v)[keep] for k, v in peaks.items()}
    ra, dec = pixel_to_sky(l2.wcs, x, y)
    ntr = np.zeros(x.size, np.int8)
    if getattr(l2, "flags", None) is not None:
        tr = (np.asarray(l2.flags).astype(np.int64) & TRANSIENT) != 0
        ntr = np.minimum(_disc_count(tr, x, y, C.DOTS_TRANSIENT_R_PX), 127).astype(np.int8)
    return pd.DataFrame({"x": x, "y": y, "ra": np.asarray(ra) % 360.0, "dec": dec, "snr_mf": peaks["snr_mf"],
                         "flux_mf_ujy": np.asarray(peaks["flux"]) * ctx.conv, "flags": ctx.f3[iy, ix],
                         "n_transient": ntr, "why": peaks["why"]}).astype(PEAK_COLUMNS)


# The extended pass's file (18 Nov, agent "comets"; psf.ext_pass): every diffuse detection the wide filters see better
# than the point filter, > EXT_DEDUP_PX from every final dot. Not dots: on 80 bench photos ~2,400 per photo, 71% with no
# catalogue source within 3″ (unresolved faint stars, confusion, galaxies, comae), 0.39 per deg² on negative images.
EXT_COLUMNS = {"x": "float32", "y": "float32", "ra": "float64", "dec": "float64",
               "snr_ext": "float32",       # S/N of the winning wide filter (ext_scale × the PSF FWHM)
               "snr_point": "float32",     # the point (ePSF-matched) filter's S/N there, same residual: snr_ext / snr_point
                                           # is ~0.76 (2×) / 0.42 (4×) for a star, 1.25 / 2.4 for a source that wide
               "ext_scale": "int8",        # 2 or 4
               "flux_ext_ujy": "float32",  # total flux if the source has the wide kernel's shape
               "flux_ujy": "float32", "flux_err_ujy": "float32",  # r = 2 px aperture on the point-free residual
               "flags": "int32",           # FLAGS, 3 × 3 OR at the peak pixel
               "near_dot_px": "float32"}   # distance to the nearest dot (a coma next to a star: small)


def ext_rel(obs_id: str, det: int, folder: str) -> str:
    """The extended pass's file of a v2 dot file: <folder>_ext/<obs_id>D<det>.parquet."""
    return f"{folder}_ext/{obs_id}D{int(det)}.parquet"


def ext_table(l2, ctx: PhotoContext, ext: dict | None, dots: pd.DataFrame | None = None) -> pd.DataFrame:
    """The extended pass's detections (EXT_COLUMNS), none within EXT_DEDUP_PX of a final dot."""
    empty = pd.DataFrame({c: pd.Series(dtype=t) for c, t in EXT_COLUMNS.items()})
    if not ext or len(ext["x"]) == 0:
        return empty
    x, y = np.asarray(ext["x"], np.float64), np.asarray(ext["y"], np.float64)
    near = np.full(x.size, np.inf)
    if dots is not None and len(dots):
        near, _ = cKDTree(dots[["x", "y"]].to_numpy(np.float64)).query(np.c_[x, y], k=1)
    keep = near > C.EXT_DEDUP_PX
    if not keep.any():
        return empty
    e = {k: np.asarray(v)[keep] for k, v in ext.items()}
    x, y, near = x[keep], y[keep], near[keep]
    ix, iy = pixel_of(x, y, ctx.f3.shape)
    ra, dec = pixel_to_sky(l2.wcs, x, y)
    return pd.DataFrame({"x": x, "y": y, "ra": np.asarray(ra) % 360.0, "dec": dec, "snr_ext": e["snr_ext"],
                         "snr_point": e["snr_point"], "ext_scale": e["scale"], "flux_ext_ujy": e["flux_ext"] * ctx.conv,
                         "flux_ujy": e["flux"] * ctx.conv, "flux_err_ujy": e["flux_err"] * ctx.conv,
                         "flags": ctx.f3[iy, ix], "near_dot_px": np.where(np.isfinite(near), near, np.nan)}
                        ).astype(EXT_COLUMNS)


@dataclass
class PartnerImage:
    """What forced photometry in this photo needs, kept while its partner detector is measured."""
    sub: np.ndarray        # IMAGE minus the background, bad pixels 0 (float32)
    bad: np.ndarray
    noise0: np.ndarray     # float32
    wcs: object
    conv: float
    factor: float          # empirical / formal aperture noise (empirical_noise)


def empirical_noise(ctx: PhotoContext, n: int = C.EMPIRICAL_NOISE_N, seed: int = C.EMPIRICAL_NOISE_SEED,
                    r: float = DETECT_APERTURE_R_PX) -> dict:
    """The r = 2 px aperture's real scatter: 1.4826 × MAD of the aperture sum (background map subtracted, as a dot's
    flux_ujy) at n random places (seeded; >= 7 px inside, no masked pixel within 1 px), in µJy, and its ratio to the
    median formal error there (VARIANCE / background rms; the pixel agent measured 2–3× in crowded fields)."""
    ny, nx = ctx.sub.shape
    if nx < 30 or ny < 30:
        return {"n": 0, "sigma_ujy": None, "factor": None}
    rng = np.random.default_rng(seed)
    x = rng.uniform(7, nx - 8, 4 * n)
    y = rng.uniform(7, ny - 8, 4 * n)
    near_bad = _disc_count(ctx.bad, x, y, 1.0) > 0
    x, y = x[~near_bad][:n], y[~near_bad][:n]
    if x.size < 20:
        return {"n": int(x.size), "sigma_ujy": None, "factor": None}
    f, e, _ = sep.sum_circle(np.ascontiguousarray(ctx.sub, np.float64), x, y, r, err=ctx.noise0,
                             mask=np.ascontiguousarray(ctx.bad), subpix=5)
    sig = _robust_sigma(np.asarray(f) * ctx.conv)
    med = float(np.median(np.asarray(e) * ctx.conv))
    return {"n": int(x.size), "r_px": r, "sigma_ujy": sig, "formal_median_ujy": med,
            "factor": sig / med if med > 0 else None}


def partner_image(l2, ctx: PhotoContext) -> PartnerImage:
    emp = empirical_noise(ctx)
    return PartnerImage(sub=ctx.sub.astype(np.float32), bad=np.asarray(ctx.bad, bool),
                        noise0=ctx.noise0.astype(np.float32), wcs=l2.wcs, conv=float(ctx.conv),
                        factor=float(emp["factor"]) if emp.get("factor") else 1.0)


def partner_fluxes(df: pd.DataFrame, part: PartnerImage) -> tuple[np.ndarray, np.ndarray]:
    """partner_flux_ujy, partner_err_ujy of dots `df` in the partner photo: r = 2 px aperture (background map
    subtracted, bad pixels out: the same measurement as a dot's flux_ujy there) at each dot's sky position (ra_psf/
    dec_psf where psf_pos_ok, else ra/dec); err = its formal 1σ × the partner's empirical factor. NaN outside the
    partner photo (2 px margin) or with a masked pixel within 1 px of the position."""
    n = len(df)
    f, e = np.full(n, np.nan), np.full(n, np.nan)
    if n == 0:
        return f, e
    ok = df["psf_pos_ok"].to_numpy(bool) if "psf_pos_ok" in df else np.zeros(n, bool)
    ra = np.where(ok, df["ra_psf"].to_numpy(np.float64), df["ra"].to_numpy(np.float64))
    dec = np.where(ok, df["dec_psf"].to_numpy(np.float64), df["dec"].to_numpy(np.float64))
    x, y = sky_to_pixel(part.wcs, ra, dec)
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    ny, nx = part.sub.shape
    ins = np.isfinite(x) & np.isfinite(y) & (x >= 2) & (x <= nx - 3) & (y >= 2) & (y <= ny - 3)
    if not ins.any():
        return f, e
    k = np.flatnonzero(ins)
    xs, ys = x[k], y[k]
    fs, es, _ = sep.sum_circle(np.ascontiguousarray(part.sub, np.float64), xs, ys, DETECT_APERTURE_R_PX,
                               err=np.ascontiguousarray(part.noise0, np.float64), mask=part.bad, subpix=5)
    good = _disc_count(part.bad, xs, ys, 1.0) == 0
    f[k] = np.where(good, fs * part.conv, np.nan)
    e[k] = np.where(good, es * part.conv * part.factor, np.nan)
    return f, e


def fill_partners(a: Pending, b: Pending) -> None:
    """Fill each photo's partner_* columns from the other (D1↔D4, D2↔D5, D3↔D6 of one exposure)."""
    for p, q in ((a, b), (b, a)):
        if q.partner is not None and len(p.df) and "partner_flux_ujy" in p.df:
            f, e = partner_fluxes(p.df, q.partner)
            p.df["partner_flux_ujy"], p.df["partner_err_ujy"] = f.astype(np.float32), e.astype(np.float32)
            p.meta["partner"] = {"detector": int(q.meta.get("detector", 0)), "obs_id": q.meta.get("obs_id")}


def sat_cores(flags, max_n: int = C.SAT_CORES_MAX) -> list:
    """[[x, y, n_px], ...] of OVERFLOW/NONLINEAR clumps (8-connected; centre = mean pixel), largest first."""
    from scipy import ndimage
    if flags is None:
        return []
    sat = (np.asarray(flags).astype(np.int64) & SAT_BITS) != 0
    if not sat.any():
        return []
    lab, n = ndimage.label(sat, structure=np.ones((3, 3), bool))
    idx = np.arange(1, n + 1)
    size = np.asarray(ndimage.sum(sat, lab, idx))
    cy, cx = np.asarray(ndimage.center_of_mass(sat, lab, idx)).T
    o = np.argsort(-size, kind="stable")[:max_n]
    return [[round(float(cx[i]), 1), round(float(cy[i]), 1), int(size[i])] for i in o]


def sky_jacobian(l2) -> list | None:
    """[[dE/dx, dE/dy], [dN/dx, dN/dy]] in degrees per pixel at the photo centre (E = ΔRA·cos Dec, N = ΔDec): the
    local CD matrix, MWIR x flip included. A dot's long axis theta_deg (pixel frame, from +x towards +y) points to
    the sky position angle atan2(E, N) of J · (cos θ, sin θ)."""
    wcs = getattr(l2, "wcs", None)
    if wcs is None:
        return None
    ny, nx = np.shape(l2.data)
    x0, y0 = (nx - 1) / 2.0, (ny - 1) / 2.0
    ra, dec = pixel_to_sky(wcs, np.array([x0, x0 + 1.0, x0]), np.array([y0, y0, y0 + 1.0]))
    c = np.cos(np.radians(dec[0]))
    de = ((np.asarray(ra[1:]) - ra[0] + 180.0) % 360.0 - 180.0) * c
    dn = np.asarray(dec[1:]) - dec[0]
    return [[float(de[0]), float(de[1])], [float(dn[0]), float(dn[1])]]


def v2_meta(l2, ctx: PhotoContext | None, extras: dict) -> dict:
    """Photo metadata of extraction v2: noise and 5σ point-source depth grids, the empirical aperture noise, the
    saturated cores, the sky orientation, the matched filter's settings."""
    out: dict = {"extraction": 2,
                 "mf": {"min_snr": C.mf_pass_snr(getattr(l2, "detector", None)), "peak_snr": C.MF_PEAK_SNR, "dedup_px": C.MF_PASS_DEDUP_PX,
                        "kernel_half_px": C.MF_KERNEL_HALF, "sep_flag": C.SEP_FLAG_MF_PASS,
                        "normalise": str(C.MF_PASS_NORMALISE)}}
    if extras.get("mf_stats"):
        st = dict(extras["mf_stats"])
        g = st.pop("sigma_grid", None)
        out["mf"].update(st)
        if g is not None:
            out["mf"]["sigma_grid"] = [[None if not np.isfinite(v) else float(f"{v:.4g}") for v in row] for row in g]
    if C.EXT_PASS:
        out["ext"] = {"scales": list(C.EXT_SCALES), "min_snr": C.EXT_PASS_SNR, "ratio": C.EXT_RATIO,
                      "dedup_px": C.EXT_DEDUP_PX, "normalise": str(C.EXT_NORMALISE), "file": "<folder>_ext",
                      **(extras.get("ext_stats") or {})}
    out["sat_cores"] = sat_cores(getattr(l2, "flags", None))
    out["sky_jacobian_deg_px"] = sky_jacobian(l2)
    if extras.get("dark_holes") is not None:
        out["dark_holes"] = extras["dark_holes"]
    if ctx is None:
        return out
    out["aperture_noise"] = empirical_noise(ctx)
    grids = {"n": C.PHOTO_META_GRID, "noise_mjysr": block_means(ctx.noise)}
    den = extras.get("mf_den")
    if den is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            sig = np.where(den > 0, 1.0 / np.sqrt(den), np.nan) * ctx.conv
        grids["depth5_ujy"] = block_means(5.0 * sig)
        out["depth5_ujy_median"] = float(np.nanmedian(5.0 * sig)) if np.isfinite(sig).any() else None
    out["grids_v2"] = grids
    return out


def process_photo(root: str, obs_id: str, detector: int, url: str, session: int = 0, visit: int = 0,
                  zone: str | None = None) -> dict:
    """Do one photo: forced/<...>.parquet (if the zone has a refcat), then dots/<...>.parquet (the zone's
    folder, config.zone_dots) with the photo metadata. Returns a stats dict; never raises."""
    store = _store(root)
    st = _new_stats(obs_id, detector)
    if _already_done(store, dots_rel(obs_id, detector, C.zone_dots(zone)[0]), st):
        return st
    l2 = _load_retrying(url, st)
    if l2 is None:
        return st
    return _measure_and_write(store, l2, obs_id, detector, session, visit, zone, st)


def exposure_groups(todo: list, n: int) -> list[list]:
    """Worker tasks of at least n photos (rows with .obs_id, in run order) that never split an exposure."""
    groups: list[list] = []
    cur: list = []
    for r in todo:
        if len(cur) >= n and r.obs_id != cur[-1].obs_id:
            groups.append(cur)
            cur = []
        cur.append(r)
    if cur:
        groups.append(cur)
    return groups


def _pair_order(jobs: list) -> list:
    """Extraction v2: each exposure's photos with partner detectors next to each other (D1, D4, D2, D5, D3, D6), the
    exposures in their given order."""
    first = {}
    for k, j in enumerate(jobs):
        first.setdefault(j[0], k)
    return sorted(jobs, key=lambda j: (first[j[0]], (j[1] - 1) % 3, j[1]))


def session_groups(todo: list, n: int) -> list[list]:
    """Worker tasks of at least n photos (rows with .session, in run order) that never split a session: the fused
    worker (extraction v2 + session stacks) holds a session's photos of a detector until it stacks them."""
    groups: list[list] = []
    cur: list = []
    for r in todo:
        if len(cur) >= n and int(r.session) != int(cur[-1].session):
            groups.append(cur)
            cur = []
        cur.append(r)
    if cur:
        groups.append(cur)
    return groups


def _stack_order(jobs: list) -> list:
    """Fused worker: per session, the partner pair D1+D4 of every exposure, then D2+D5, then D3+D6 (partners stay next
    to each other, so the v2 pairing is unchanged), so a detector's photos of a session come together and only two
    detectors' prepared photos are held at once."""
    first_s, first_o = {}, {}
    for k, j in enumerate(jobs):
        first_s.setdefault(j[3], k)
        first_o.setdefault(j[0], k)
    return sorted(jobs, key=lambda j: (first_s[j[3]], (j[1] - 1) % 3, first_o[j[0]], j[1]))


def fused_stacks(settings: C.DetectSettings) -> bool:
    """The dots worker builds the session stacks itself: extraction v2 and SESSION_STACK both on."""
    return bool(settings.v2 and C.SESSION_STACK)


def process_photos(root: str, photos: list[tuple], zone: str | None = None,
                   prefetch: bool | None = None, stacks: list[dict] | None = None) -> list[dict]:
    """process_photo for several photos (obs_id, detector, url, session, visit) in one worker, in order.
    With prefetch (default C.DOTS_PREFETCH), photo k + 1 is read in a background thread while photo k
    is measured, so the core doesn't sit idle waiting for the network. Same functions per photo, so
    the files are the same as process_photo's; at most two photos are in memory at once.
    Extraction v2 (config DOTS_V2): partner detectors of one exposure are measured one after the other and the first
    is written after the second, each with partner_flux_ujy/partner_err_ujy from the other (a partner not in this
    task, already done or failed leaves them NaN); one partner image (~50 MB) waits at most.
    Session stacks fused (`stacks`: coadd.session_tasks of these photos not yet written; only with fused_stacks): each
    photo of such a task is also prepared for its stack while it is in memory (a photo whose dot file exists is read
    for the stack only), and the stack is written when its last photo is in (coadd.Stacker): no photo is read twice.
    The photos then go session by session, D1+D4 / D2+D5 / D3+D6 (_stack_order). The stacks' stats come after the
    photos' with "stack": True.
    Returns one stats dict per photo (in processing order: v2 puts partners side by side); never raises."""
    prefetch = C.DOTS_PREFETCH if prefetch is None else prefetch
    store = _store(root)
    settings = C.zone_detect(zone)
    ddir = settings.folder
    fused = bool(stacks) and fused_stacks(settings)
    want: set = set()
    if fused:
        want = {(str(o), int(t["detector"])) for t in stacks for o in t["obs_ids"]}
    out: list[dict] = []
    jobs = []  # (obs_id, detector, url, session, visit, st, measure)
    for obs_id, detector, url, session, visit in photos:
        st = _new_stats(obs_id, detector)
        if _already_done(store, dots_rel(obs_id, detector, ddir), st):
            if (str(obs_id), int(detector)) in want:
                jobs.append((obs_id, int(detector), url, int(session), int(visit), st, False))  # read for its stack
            else:
                out.append(st)
        else:
            jobs.append((obs_id, int(detector), url, int(session), int(visit), st, True))
    stacker = None
    if fused:
        from .coadd import Stacker
        stacker = Stacker(store, stacks, {(str(j[0]), int(j[1])) for j in jobs},
                          maps=zone in C.SESSION_STACK_MAP_ZONES)
        jobs = _stack_order(jobs)
    elif settings.v2:
        jobs = _pair_order(jobs)
    held: list = []  # [(obs_id, detector, Pending)] waiting for its partner (v2)

    def stack_part(k: int, l2) -> tuple | None:
        """Fused: prepare the photo for its session stack (before its dots: the stack's input is the raw photo);
        returns the shared background and hole mask for its dots."""
        obs_id, detector, st = jobs[k][0], jobs[k][1], jobs[k][5]
        if stacker is None or not stacker.wants(obs_id, detector):
            return None
        if l2 is None:
            stacker.fail(obs_id, detector, f"read: {st.get('error', '')}"[:300])
            return None
        t0, c0 = time.time(), time.process_time()
        shared = None
        try:
            if int(l2.detector) == int(detector) and C.DOTS_V2_HOLES and getattr(l2, "flags", None) is not None \
                    and not bad_finast(l2.header.get("FINAST")):
                shared = raw_background_holes(l2)
        except Exception:  # noqa: BLE001 - the dots and the stack then compute it themselves
            shared = None
        st["shared_s"] = round(time.process_time() - c0, 2)
        c1 = time.process_time()
        if int(l2.detector) != int(detector):
            stacker.fail(obs_id, detector, f"header DETECTOR {l2.detector} != photo list {detector}")
        else:
            stacker.add(obs_id, detector, l2, shared=shared, read_s=st.get("read_s"))
        st["stack_cpu_s"] = round(time.process_time() - c1, 2)  # prepare (+ the stack when this photo completed it)
        st["stack_wall_s"] = round(time.time() - t0, 2)
        st["stack_held"] = stacker.held()
        return shared

    def handle(k: int, l2) -> None:
        obs_id, detector, url, session, visit, st, measure = jobs[k]
        shared = stack_part(k, l2)
        if not measure:  # read for its session stack only: the dot file is there
            if st["status"] == "failed":
                st["status"] = "skipped"
            out.append(st)
            return
        if not settings.v2:
            out.append(st if l2 is None else _measure_and_write(store, l2, obs_id, detector, session, visit, zone, st))
            return
        p = None if l2 is None else _measure(store, l2, obs_id, detector, session, visit, zone, st, shared=shared)
        partner = C.PARTNER_DETECTOR.get(detector)
        if held and held[0][0] == obs_id and held[0][1] == partner:
            _, _, q = held.pop()
            if p is not None and q is not None:
                fill_partners(q, p)
            out.append(_finish(store, q, q.st) if q is not None else jobs[k - 1][5])
            out.append(_finish(store, p, st))
            return
        while held:  # a held photo whose partner didn't come next
            _, _, q = held.pop()
            out.append(_finish(store, q, q.st))
        nxt = jobs[k + 1] if k + 1 < len(jobs) else None
        if p is not None and nxt is not None and nxt[0] == obs_id and nxt[1] == partner and nxt[6]:
            held.append((obs_id, detector, p))
        else:
            out.append(_finish(store, p, st))

    if not prefetch or len(jobs) < 2:
        for k, j in enumerate(jobs):
            handle(k, _load_retrying(j[2], j[5]))
    else:
        with ThreadPoolExecutor(max_workers=1) as reader:
            nxt = reader.submit(_load_retrying, jobs[0][2], jobs[0][5])
            for k, j in enumerate(jobs):
                st = j[5]
                t0 = time.time()
                l2 = nxt.result()  # _load_retrying never raises
                st["wait_s"] = round(time.time() - t0, 2)  # time this core waited for the read
                nxt = reader.submit(_load_retrying, jobs[k + 1][2], jobs[k + 1][5]) if k + 1 < len(jobs) else None
                handle(k, l2)
                del l2
    while held:
        _, _, q = held.pop()
        out.append(_finish(store, q, q.st))
    if stacker is not None:
        out += [{**r, "stack": True} for r in stacker.close()]
    return out


def _retry(fn):
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if attempt == C.NET_RETRIES:
                raise
            time.sleep(5 * attempt)


def spread_limit(todo: list, limit: int | None) -> list:
    """At most `limit` photos of `todo` (rows with .detector, in (mjd, detector) order), spread over the
    detectors (round-robin quota) and, within a detector, evenly over time (each detector shifted so they
    don't all pick the same exposures). The first `limit` rows were all one visit and ~2 exposures."""
    if limit is None or len(todo) <= limit:
        return list(todo)
    by_det: dict[int, list[int]] = {}
    for i, r in enumerate(todo):
        by_det.setdefault(int(r.detector), []).append(i)
    dets = sorted(by_det)
    quota = {d: 0 for d in dets}
    left = limit
    while left > 0:
        for d in dets:
            if left > 0 and quota[d] < len(by_det[d]):
                quota[d] += 1
                left -= 1
    keep = []
    for rank, d in enumerate(dets):
        idx, q = by_det[d], quota[d]
        shift = (rank + 0.5) / len(dets)
        keep += [idx[min(len(idx) - 1, int((k + shift) * len(idx) / q))] for k in range(q)]
    return [todo[i] for i in sorted(set(keep))]


def session_limit(todo: list, limit: int | None) -> list:
    """Fused run: at most about `limit` photos of `todo` as whole sessions (a stack needs all of its photos), the
    sessions spread evenly over the list."""
    if limit is None or len(todo) <= limit:
        return list(todo)
    by_s: dict[int, list] = {}
    for r in todo:
        by_s.setdefault(int(r.session), []).append(r)
    sessions = list(by_s)
    keep: list[int] = []
    n = 0
    step = max(1, len(sessions) // max(1, int(np.ceil(limit * len(sessions) / len(todo)))))
    for i in range(0, len(sessions), step):
        if n >= limit:
            break
        keep.append(sessions[i])
        n += len(by_s[sessions[i]])
    ks = set(keep)
    return [r for r in todo if int(r.session) in ks]


def run(store: Store, zone: C.Zone, workers: int, limit: int | None = None) -> dict:
    t_start = time.time()
    photos = store.read_parquet(photolist_rel(zone.name)).sort_values(["mjd", "detector"]).reset_index(drop=True)
    ddir, deblend = C.zone_dots(zone.name)
    done = {n for n in store.listdir(ddir) if n.endswith(".parquet")}
    fused = fused_stacks(C.zone_detect(zone.name))
    tasks: list[dict] = []
    pending: list[dict] = []
    if fused:  # session stacks built here (docs/session-stacks.md §8): photos of a stack not yet written are read too
        from . import coadd
        tasks = coadd.session_tasks(photos)
        have = {n for n in store.listdir(C.SESSION_STACK_DIR) if n.endswith(".parquet")}
        pending = [t for t in tasks if f"{t['key']}.parquet" not in have]
    need = {(str(o), int(t["detector"])) for t in pending for o in t["obs_ids"]}
    n_done_before = sum(f"{r.obs_id}D{int(r.detector)}.parquet" in done for r in photos.itertuples())
    todo = [r for r in photos.itertuples() if f"{r.obs_id}D{int(r.detector)}.parquet" not in done
            or (str(r.obs_id), int(r.detector)) in need]
    todo = session_limit(todo, limit) if fused else spread_limit(todo, limit)
    L.info("dots %s: %d photos, %d already done in %s/ (deblend_cont %g), %d to do now, %d workers%s", zone.name,
           len(photos), n_done_before, ddir, deblend, len(todo), workers,
           f", session stacks fused: {len(pending)} of {len(tasks)} to build" if fused else "")
    stats: list[dict] = []
    stack_stats: list[dict] = []
    if todo:
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=max(1, workers), mp_context=ctx, initializer=log.setup) as ex:
            n = max(1, C.DOTS_TASK_PHOTOS)
            groups = [todo[i:i + n] for i in range(0, len(todo), n)]
            if fused:
                groups = session_groups(todo, n)  # a session's photos in one task (its stacks, partner_* columns)
            elif C.zone_detect(zone.name).v2:
                groups = exposure_groups(todo, n)  # partner detectors in one task (partner_* columns)

            def group_stacks(g) -> list[dict] | None:
                if not fused:
                    return None
                ss = {int(r.session) for r in g}
                return [t for t in pending if int(t["session"]) in ss]

            futs = [ex.submit(process_photos, store.root,
                              [(r.obs_id, int(r.detector), r.url, int(r.session), int(r.visit)) for r in g],
                              zone.name, None, group_stacks(g)) for g in groups]
            k = 0
            for f in as_completed(futs):
                for st in f.result():
                    if st.get("stack"):
                        stack_stats.append(st)
                        if st["status"] != "done":
                            L.warning("session stack %s: %s %s", st["key"], st["status"], st.get("error", ""))
                        continue
                    k += 1
                    stats.append(st)
                    if st["status"] == "failed":
                        L.warning("[%d/%d] %sD%d FAILED: %s", k, len(todo), st["obs_id"], st["detector"], st["error"])
                    else:
                        L.info("[%d/%d] %sD%d %s: %d dots, %d forced, read %.1f s, waited %.1f s, detect %.1f s, "
                               "forced %.1f s, write %.1f s, %.0f KB", k, len(todo), st["obs_id"], st["detector"],
                               st["status"], st["n_dots"], st["n_forced"], st["read_s"], st.get("wait_s", st["read_s"]),
                               st["detect_s"], st["forced_s"], st["write_s"], st["bytes"] / 1e3)
    done_now = [s for s in stats if s["status"] == "done"]
    finast = [s for s in stats if s["status"] == "done_finast"]
    failed = [s for s in stats if s["status"] == "failed"]
    wall = time.time() - t_start
    out = {
        "photos": int(len(photos)), "done_before": n_done_before, "done_now": len(done_now),
        "skipped_finast": len(finast),
        "failed": len(failed), "failed_photos": [f"{s['obs_id']}D{s['detector']}" for s in failed][:50],
        "remaining": int(len(photos) - n_done_before - len(done_now) - len(finast)),
        "wall_s": round(wall, 1), "workers": workers, "dots_dir": ddir, "deblend_cont": deblend,
        "detect_settings": dataclasses.asdict(C.zone_detect(zone.name)),
        "forced_kept": sum(bool(s.get("forced_kept")) for s in stats),
        "forced_replaced": sum(bool(s.get("forced_replaced")) for s in stats),
    }
    if done_now:
        a = pd.DataFrame(done_now)
        out.update({
            "dots_per_photo_median": float(a["n_dots"].median()),
            "dots_total_now": int(a["n_dots"].sum()),
            "read_s_median": float(a["read_s"].median()),
            "wait_s_median": float(a["wait_s"].median()) if "wait_s" in a else None,  # core idle for the read
            "detect_s_median": float(a["detect_s"].median()),
            "forced_s_median": float(a["forced_s"].median()),
            "forced_per_photo_median": float(a["n_forced"].median()),
            "kb_per_photo_median": round(float(a["bytes"].median()) / 1e3, 1),
            "forced_kb_per_photo_median": round(float(a["forced_bytes"].median()) / 1e3, 1),
            "photos_per_minute": round(len(done_now) / wall * 60.0, 1),
        })
    if fused:
        from . import coadd
        todo_keys = {t["key"] for t in pending}
        exists = [{"key": t["key"], "status": "exists"} for t in tasks if t["key"] not in todo_keys]
        out["session_stacks"] = coadd.write_index_report(store, zone, tasks, stack_stats + exists, t_start, fused=True)
        stats_s = [s for s in stats if "stack_cpu_s" in s]
        if stats_s:
            a = pd.DataFrame(stats_s)
            out["session_stacks"]["per_photo_cpu_s_median"] = {
                "shared_background_holes": float(a["shared_s"].median()),
                "stack_prepare_and_stack": float(a["stack_cpu_s"].median()),
                "stack_mean_per_photo": round(float(a["stack_cpu_s"].mean()), 2)}
    if stats:
        store.write_json(results_rel(zone.name, f"dots_runs/{time.strftime('%Y%m%dT%H%M%S')}.json"),
                         {"summary": out, "photos": stats, "session_stacks": stack_stats})
    L.info("dots %s: %s", zone.name, {k: v for k, v in out.items() if k != "failed_photos"})
    return out


def cpu_count() -> int:
    return os.cpu_count() or 1
