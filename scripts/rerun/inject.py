#!/usr/bin/env python3
"""Fake objects in real photos, through the real extraction-v2 dots worker: what it finds, what it loses and why.

Each photo is read from the photo cache into memory, fakes of known brightness, size and motion are added to IMAGE (and
their photon noise to IMAGE and VARIANCE, the way the pipeline's VARIANCE holds it), and the photo goes through the SAME
worker the rerun runs (dots.process_photos: extraction v2 with the session stacks fused, partner detectors, peaks /
ext / streak files). Nothing is written back to the cache. The worker's outputs (in a scratch root of this tool) are
then matched to the truth list.

Kinds of fakes (all from the photo's own ePSF at sub-pixel positions):
    point   the ePSF, S/N 2.5-40 (optimal, i.e. matched-filter, S/N for its own shape)
    fuzzy   the ePSF convolved with a Gaussian or a 1/rho coma of 1.5-12 x the PSF FWHM, with or without a tail,
            S/N 3-60 (optimal for its own shape)
    trail   the ePSF along a segment (streaks.trail_kernel): 150-6,000"/h over the exposure, S/N 4-60
    mover   one object per (session, partner pair), moving 0-50" between exposures (0-1,430"/h; 15% static),
            put in every exposure and both partner detectors (D1+D4, D2+D5, D3+D6: the same moment) at its position at
            each photo's MJD-AVG, as a trail of its rate x XPOSURE; flat or blackbody (1500 K, 5800 K) spectrum, so its
            flux follows the wavelength under it (the detector's spectral strips)
Fakes sit on a grid (default 128 px cells, +-16 px jitter: ~220 per photo, ~18 per deg^2) so they never touch each
other and change the background little; each has a control point (its cell's corner) matched the same way: the
chance level.

Instrumentation, results unchanged: dots' own functions are wrapped (photo_context, detect, fit_glow,
residual_companions, mf_pass, _measure) only to read what they return; the matched filter's S/N at each fake on the
residual it searched (psf.mf_at) and the background lost under each fake are computed on the side.

Run from the repo root:
    uv run --project pipeline python scripts/rerun/inject.py run --subset <subset.json> --out <dir> [--variant v]
        [--round r] [--grid 128] [--neg] [--no-stacks] [--workers 5]
    uv run --project pipeline python scripts/rerun/inject.py match --out <dir>
    uv run --project pipeline python scripts/rerun/inject.py report --out <dir> [...]
A subset file lists whole sessions: {"targets": [{"zone", "sessions": [..] | "obs": [..] | "pick": "largest",
"condition": "sparse", "detectors": [..] (optional), "max_exposures": n (optional)}]} (bench_targets.json works as it
is; so does any other targets file of that format, e.g. the wide sample's wide_targets.json). The conditions table
(p1/conditions: <folder>_cond/<obs_id>D<det>.parquet, one row per 128-px tile) joins on (obs_id, detector,
cond_tile); every fake also carries its 32 x 32 footer block (block_i, block_j: mf.sigma_grid, grids_v2).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("SKYSHIFT_DOTS_V2", "1")
os.environ.setdefault("SKYSHIFT_SESSION_STACK", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SKYSHIFT_DATA", Path.home() / "skyshift-data"))
OUT = DATA / "rerun" / "inject"

# --- fakes: kinds and ranges ---------------------------------------------------------------------------------------
GRID_PX = 128             # cell size; one fake per cell
JITTER_PX = 16            # +- jitter of a fake inside its cell
EDGE_PX = 12              # no fake centre closer to the photo edge
KIND_SHARE = {"point": 0.45, "fuzzy": 0.30, "trail": 0.25}
POINT_SNR = (2.5, 40.0)
FUZZY_SNR = (3.0, 60.0)
FUZZY_SIZES = (1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)   # x the PSF FWHM (1.22 px)
FUZZY_TAIL_MAX_SIZE = 6.0
FUZZY_TAIL_SHARE = 0.4    # of fuzzy fakes with a tail
TAIL_FLUX = 0.3           # share of a tailed fake's flux in its tail
TRAIL_RATE = (150.0, 6000.0)
TRAIL_SNR = (4.0, 60.0)
MOVERS_PER_GROUP = 24     # movers per (session, partner pair)
MOVER_STEP_ARCSEC = (0.5, 50.0)   # displacement between exposures (2.1 min)
MOVER_STATIC_SHARE = 0.15
MOVER_SNR = (2.0, 30.0)   # in the reference photo (middle exposure, SWIR detector of the pair)
MOVER_SPECTRA = (("flat", 0.5), ("bb1500", 0.25), ("bb5800", 0.25))   # bb1500: D4-D6 ~3x D1-D3 (f_nu)
MOVER_CLEAR_PX = 40       # static fakes keep this far from a mover
EXPOSURE_GAP_S = 126.0    # 2.1 min between exposures (only to quote the per-exposure step)

# --- matching ------------------------------------------------------------------------------------------------------
MATCH_PX = 1.0            # point: a dot / peak within this
BLEND_PX = 4.0            # another dot this close: a blend / crowding miss
ANGLE_DEG = 20.0          # trail vs streak row


def psf_fwhm_px() -> float:
    from skyshift_core.detect import PSF_SIGMA_PX
    return float(PSF_SIGMA_PX * 2.3548)


def match_radius(kind: str, size: float, length: float) -> float:
    """Match radius (px) of a fake: points 1 px; fuzzy max(1.5, half its FWHM), at most 3 px; trails and movers max(1,
    0.1 L)."""
    if kind == "fuzzy":
        return float(min(3.0, max(1.5, 0.5 * size * psf_fwhm_px())))
    if kind in ("trail", "mover"):
        return float(max(MATCH_PX, 0.1 * length))
    return MATCH_PX


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} inject: {msg}", flush=True)


def seed_of(*parts) -> int:
    return int(hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


# =====================================================================================================================
# 1. kernels: a unit-flux model of each fake on native pixels (fraction of the flux per pixel, sum ~1)
# =====================================================================================================================


def point_stamp(epsf, x: float, y: float, half: int = 4) -> tuple[np.ndarray, int, int]:
    """The ePSF at (x, y) on (2h+1)^2 pixels around the rounded position (exact spline, any sub-pixel phase)."""
    ix, iy = int(round(x)), int(round(y))
    P = epsf.stamps_at(np.array([x]), np.array([y]), np.array([ix]), np.array([iy]), half)[0][0]
    return np.clip(P, 0.0, None), ix, iy


def _table(epsf, x: float, y: float) -> tuple[np.ndarray, int]:
    z = int(epsf.zone(np.array([x]), np.array([y]))[0])
    return np.asarray(epsf.psf[z], np.float64), int(epsf.oversample[0])


def trail_stamp(epsf, x: float, y: float, length: float, theta: float) -> tuple[np.ndarray, int, int]:
    """streaks.trail_kernel (the streak agent's kernel: the zone's ePSF along a segment, uniform light) centred at (x,
    y); length 0 is the point stamp."""
    if length < 0.2:
        return point_stamp(epsf, x, y)
    from skyshift_pipeline import streaks as SK
    table, os_ = _table(epsf, x, y)
    ix, iy = int(round(x)), int(round(y))
    K = SK.trail_kernel(table, os_, float(length), float(theta), phase=(x - ix, y - iy), profile="uniform")
    return np.clip(K, 0.0, None), ix, iy


def fuzzy_weights(os_: int, size: float, profile: str, tail: bool, tail_pa: float) -> tuple[np.ndarray, int]:
    """Intrinsic light of a fuzzy fake on the ePSF's oversampled grid (os_ samples per pixel), sum 1, centred on its
    middle sample; returns (canvas, half in samples). size = x the PSF FWHM: gauss = a Gaussian of sigma PSF_SIGMA_PX *
    sqrt(size^2 - 1) (so ePSF x it is size x the FWHM wide, as psf.ext_kernel's scales); coma = surface brightness
    1/rho to R = size x FWHM (half its light within R/2 = the Gaussian's half-light radius). A tail: TAIL_FLUX of the
    light along a ray at tail_pa (radians, pixel frame), exp(-d / R) to 3R, smoothed by R/4."""
    from skyshift_core.detect import PSF_SIGMA_PX
    fwhm = PSF_SIGMA_PX * 2.3548
    if profile == "gauss":
        sg = PSF_SIGMA_PX * np.sqrt(max(size * size - 1.0, 1e-6))
        reach = 3.5 * sg
    else:
        R = size * fwhm
        reach = R
    tail_len = 3.0 * size * fwhm if tail else 0.0
    H = int(np.ceil((max(reach, tail_len) + 1.0) * os_))
    k = np.arange(-H, H + 1) / os_
    xx, yy = np.meshgrid(k, k)
    rr = np.hypot(xx, yy)
    if profile == "gauss":
        w = np.exp(-0.5 * rr * rr / sg ** 2)
    else:
        w = np.where(rr <= R, 1.0 / np.maximum(rr, 0.5 / os_), 0.0)
    w /= w.sum()
    if tail:
        R0 = size * fwhm
        d = np.linspace(0.0, 3.0 * R0, int(3.0 * R0 * os_) + 2)
        t = np.zeros_like(w)
        px = np.rint(d * np.cos(tail_pa) * os_).astype(int) + H
        py = np.rint(d * np.sin(tail_pa) * os_).astype(int) + H
        np.add.at(t, (py, px), np.exp(-d / R0))
        from scipy.ndimage import gaussian_filter
        t = gaussian_filter(t, max(1.0, R0 / 4.0 * os_))
        t /= t.sum()
        w = (1.0 - TAIL_FLUX) * w + TAIL_FLUX * t
    return w, H


def fuzzy_stamp(epsf, x: float, y: float, size: float, profile: str, tail: bool,
                tail_pa: float) -> tuple[np.ndarray, int, int]:
    """The intrinsic light (fuzzy_weights) convolved with the zone's ePSF table and sampled at the native pixel
    centres around (x, y) (the same construction as streaks.trail_kernel for a line), sum ~1."""
    from scipy.signal import fftconvolve
    table, os_ = _table(epsf, x, y)
    w, H = fuzzy_weights(os_, size, profile, tail, tail_pa)
    full = fftconvolve(w, table, mode="full") * float(os_ * os_)
    full[full < 0] = 0.0
    c = H + table.shape[0] // 2               # index of offset 0
    ix, iy = int(round(x)), int(round(y))
    phx, phy = int(round((x - ix) * os_)), int(round((y - iy) * os_))
    h = int(np.ceil(H / os_ + 3.3))
    idx = np.arange(-h, h + 1) * os_
    cy, cx = c - phy + idx, c - phx + idx      # sample at pixel centre - source position
    oky, okx = (cy >= 0) & (cy < full.shape[0]), (cx >= 0) & (cx < full.shape[1])
    out = np.zeros((2 * h + 1, 2 * h + 1))
    out[np.ix_(np.flatnonzero(oky), np.flatnonzero(okx))] = full[np.ix_(cy[oky], cx[okx])]
    return out, ix, iy


# =====================================================================================================================
# 2. the clean photo: noise, background, flags (what the pipeline itself computes first)
# =====================================================================================================================


@dataclass
class Clean:
    noise: np.ndarray       # max(sqrt(VARIANCE), background rms): the pipeline's per-pixel 1 sigma (ctx.noise)
    bkg: np.ndarray         # the detection background map (ctx.bkg_level) of the clean photo
    bad: np.ndarray
    dsat: np.ndarray        # px to the nearest OVERFLOW/NONLINEAR pixel
    conv: float             # µJy per MJy/sr in one pixel
    gain_k: float           # VARIANCE per unit signal (MJy/sr): photon noise of a source of signal s is k * s
    noise_median: float
    shape: tuple


def gain_k(l2, sub: np.ndarray, bad: np.ndarray) -> float:
    """VARIANCE = variance floor + k x signal (SPHEREx's VARIANCE holds the source's photon noise): k from the pixels
    > 30 sigma above the background (median of (VAR - its background map) / signal; measured 10-90% within +-8% on
    real D2 and D5 photos). 0 when there are too few such pixels."""
    from skyshift_core import background
    v = np.asarray(l2.variance, np.float64) if l2.variance is not None else None
    if v is None:
        return 0.0
    sel = (~bad) & np.isfinite(v) & (v > 0)
    if sel.sum() < 1000:
        return 0.0
    vb = background(np.where(sel, v, 0.0), ~sel, box=32).level
    hi = sel & (sub > 30.0 * np.sqrt(np.where(sel, v, 1.0)))
    if hi.sum() < 50:
        hi = sel & (sub > 10.0 * np.sqrt(np.where(sel, v, 1.0)))
    if hi.sum() < 50:
        return 0.0
    k = (v[hi] - vb[hi]) / sub[hi]
    k = k[np.isfinite(k)]
    return float(np.median(k)) if k.size else 0.0


def clean_photo(l2, orig: dict) -> Clean:
    """The pipeline's own first steps on the clean photo (dots.mask_dark_holes, dots.photo_context: the originals, not
    the probes), for the fakes' fluxes and the background-loss diagnostic."""
    from skyshift_pipeline import dots as D
    l2m, _, pre = D.mask_dark_holes(l2)
    ctx = orig["photo_context"](l2m, {}, pre=pre)
    noise = np.where(np.isfinite(ctx.noise) & (ctx.noise > 0) & ~ctx.bad, ctx.noise, np.inf)
    flags = np.asarray(l2.flags) if l2.flags is not None else np.zeros(ctx.sub.shape, np.int32)
    k = gain_k(l2, ctx.sub, ctx.bad)
    good = np.isfinite(noise)
    return Clean(noise=noise, bkg=np.asarray(ctx.bkg_level, np.float64), bad=np.asarray(ctx.bad, bool),
                 dsat=D.sat_distance(flags), conv=float(ctx.conv), gain_k=k,
                 noise_median=float(np.median(noise[good])) if good.any() else np.nan, shape=ctx.sub.shape)


def _patch(a: np.ndarray, ix: int, iy: int, h: int, fill=np.inf) -> np.ndarray:
    """a[iy-h:iy+h+1, ix-h:ix+h+1], padded with fill outside the array."""
    ny, nx = a.shape
    out = np.full((2 * h + 1, 2 * h + 1), fill, dtype=np.float64)
    y0, y1, x0, x1 = iy - h, iy + h + 1, ix - h, ix + h + 1
    sy0, sy1, sx0, sx1 = max(y0, 0), min(y1, ny), max(x0, 0), min(x1, nx)
    if sy0 < sy1 and sx0 < sx1:
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = a[sy0:sy1, sx0:sx1]
    return out


def stamp_snr(K: np.ndarray, ix: int, iy: int, cl: Clean, epsf, x: float, y: float, amp: float = 1.0) -> dict:
    """S/N of the model amp x K (image units x px): the optimal S/N (its own shape, inverse variance), the point
    (ePSF) filter's S/N at its centre, and the r = 2 px aperture's S/N (pixel centres; the dots' snr). The noise per
    pixel is the clean photo's plus the source's own photon noise (k x amp x K: VARIANCE holds it, so the pipeline's
    noise at a bright source is higher too)."""
    h = K.shape[0] // 2
    sig0 = _patch(cl.noise, ix, iy, h)
    var = sig0 ** 2 + max(cl.gain_k, 0.0) * amp * np.clip(K, 0.0, None)
    w = np.where(np.isfinite(var), 1.0 / var, 0.0)
    opt = amp * float(np.sqrt(np.sum(w * K * K)))
    P, _, _ = point_stamp(epsf, x, y, half=min(h, 4))
    hp = P.shape[0] // 2
    Kc = K[h - hp:h + hp + 1, h - hp:h + hp + 1] if h >= hp else np.pad(K, hp - h)
    wc = w[h - hp:h + hp + 1, h - hp:h + hp + 1] if h >= hp else np.pad(w, hp - h)
    den = float(np.sqrt(np.sum(wc * P * P)))
    pt = amp * float(np.sum(wc * P * Kc) / den) if den > 0 else 0.0
    k = np.arange(-h, h + 1)
    r2 = (k[None, :] + (ix - x)) ** 2 + (k[:, None] + (iy - y)) ** 2 <= 4.0
    a = r2 & np.isfinite(var)
    ap = amp * float(K[a].sum() / np.sqrt(np.sum(var[a]))) if a.any() else 0.0
    return {"opt": opt, "pt": pt, "ap": ap}


def amp_for(K: np.ndarray, ix: int, iy: int, cl: Clean, epsf, x: float, y: float, target: float) -> float:
    """The amplitude giving optimal S/N `target` with the source's own noise (secant steps in log amp vs log S/N:
    S/N grows as amp^1 when the sky's noise dominates, amp^0.5 when the source's does)."""
    unit = stamp_snr(K, ix, iy, cl, epsf, x, y, 1e-9)["opt"] / 1e-9   # per unit amplitude, no source noise
    if not unit > 0:
        return np.nan
    la, lt = np.log(target / unit), np.log(target)
    ls = np.log(max(stamp_snr(K, ix, iy, cl, epsf, x, y, np.exp(la))["opt"], 1e-300))
    beta = 1.0
    for _ in range(30):
        if abs(ls - lt) < 1e-7:
            break
        la_new = la + (lt - ls) / beta
        ls_new = np.log(max(stamp_snr(K, ix, iy, cl, epsf, x, y, np.exp(la_new))["opt"], 1e-300))
        if la_new != la:
            beta = float(np.clip((ls_new - ls) / (la_new - la), 0.3, 1.0))
        la, ls = la_new, ls_new
    return float(np.exp(la))


# =====================================================================================================================
# 3. the fakes of one photo
# =====================================================================================================================

FAKE_COLUMNS = ["fake_id", "kind", "obs_id", "detector", "round", "x", "y", "ra", "dec", "flux_ujy", "amp", "snr_opt",
                "snr_pt", "snr_ap", "snr_target", "size", "profile", "tail", "length_px", "theta_deg",
                "rate_arcsec_h", "pa_deg", "step_arcsec", "mover_id", "spectrum", "wavelength_um", "ctrl_x", "ctrl_y",
                "cell_i", "cell_j", "block_i", "block_j", "noise_rel", "bkg_mjysr", "dsat_px", "mask_r1", "mask_r2",
                "edge_px", "gain_k", "mjd"]


def block_of(x: np.ndarray, y: np.ndarray, shape: tuple, n: int = 32) -> tuple[np.ndarray, np.ndarray]:
    """32 x 32 block of each position, the footer grids' edges (round(linspace(0, size, n + 1)))."""
    ey = np.round(np.linspace(0, shape[0], n + 1)).astype(int)
    ex = np.round(np.linspace(0, shape[1], n + 1)).astype(int)
    bi = np.clip(np.searchsorted(ey, np.asarray(y), side="right") - 1, 0, n - 1)
    bj = np.clip(np.searchsorted(ex, np.asarray(x), side="right") - 1, 0, n - 1)
    return bi, bj


def _disc_frac(bad: np.ndarray, x: float, y: float, r: float) -> float:
    h = int(np.ceil(r)) + 1
    ix, iy = int(round(x)), int(round(y))
    b = _patch(bad.astype(np.float64), ix, iy, h, fill=1.0)
    k = np.arange(-h, h + 1)
    d = (k[None, :] + ix - x) ** 2 + (k[:, None] + iy - y) ** 2 <= r * r
    return float(b[d].mean()) if d.any() else 1.0


def _spectrum(name: str, lam_um) -> np.ndarray:
    """f_nu shape (any scale) of a fake's spectrum at wavelengths lam (µm): flat, or a blackbody (bb<T>)."""
    lam = np.asarray(lam_um, np.float64)
    if name == "flat" or not name.startswith("bb"):
        return np.ones_like(lam)
    T = float(name[2:])
    nu = 2.998e14 / lam
    with np.errstate(over="ignore"):
        return nu ** 3 / np.expm1(6.626e-34 * nu / (1.381e-23 * T))


@dataclass
class Fake:
    row: dict
    K: np.ndarray
    ix: int
    iy: int


def _loguni(rng, lo, hi) -> float:
    return float(np.exp(rng.uniform(np.log(lo), np.log(hi))))


def _finish(row: dict, K: np.ndarray, ix: int, iy: int, cl: Clean, epsf, l2, amp: float | None,
            snr_target: float | None) -> Fake | None:
    """Fill a fake's flux (from snr_target, or the given amp) and its local conditions; None when it has no unmasked
    pixel to be seen with."""
    from skyshift_core import pixel_to_sky
    if amp is None:
        amp = amp_for(K, ix, iy, cl, epsf, row["x"], row["y"], float(snr_target))
    if not (np.isfinite(amp) and amp > 0):
        return None
    s = stamp_snr(K, ix, iy, cl, epsf, row["x"], row["y"], amp)
    if s["opt"] <= 0:
        return None
    x, y = row["x"], row["y"]
    ra, dec = pixel_to_sky(l2.wcs, np.array([x]), np.array([y]))
    wl = float(l2.wave.wavelength(np.array([x]), np.array([y]))[0]) if getattr(l2, "wave", None) is not None else np.nan
    ny, nx = cl.shape
    nz = _patch(cl.noise, int(round(x)), int(round(y)), 2)
    nz = nz[np.isfinite(nz)]
    bi, bj = block_of(np.array([x]), np.array([y]), cl.shape)
    row.update({"ra": float(ra[0]) % 360.0, "dec": float(dec[0]), "amp": amp, "flux_ujy": amp * cl.conv,
                "snr_opt": s["opt"], "snr_pt": s["pt"], "snr_ap": s["ap"],
                "snr_target": np.nan if snr_target is None else float(snr_target), "wavelength_um": wl,
                "block_i": int(bi[0]), "block_j": int(bj[0]),
                "noise_rel": float(np.median(nz) / cl.noise_median) if nz.size and cl.noise_median > 0 else np.nan,
                "bkg_mjysr": float(cl.bkg[min(max(int(round(y)), 0), ny - 1), min(max(int(round(x)), 0), nx - 1)]),
                "dsat_px": float(min(cl.dsat[min(max(int(round(y)), 0), ny - 1), min(max(int(round(x)), 0), nx - 1)], 9999.0)),
                "mask_r1": _disc_frac(cl.bad, x, y, 1.0), "mask_r2": _disc_frac(cl.bad, x, y, 2.0),
                "edge_px": float(min(x, y, nx - 1 - x, ny - 1 - y)), "gain_k": cl.gain_k, "mjd": float(l2.mjd_avg)})
    return Fake(row, K, ix, iy)


def _base_row(kind: str, obs_id: str, det: int, rnd: int, k: int, x: float, y: float) -> dict:
    r = {c: np.nan for c in FAKE_COLUMNS}
    r.update({"fake_id": f"{obs_id}D{det}:{rnd}:{k}", "kind": kind, "obs_id": obs_id, "detector": int(det),
              "round": int(rnd), "x": float(x), "y": float(y), "size": 1.0, "profile": "", "tail": False,
              "length_px": 0.0, "theta_deg": 0.0, "mover_id": "", "spectrum": "flat"})
    return r


def static_fakes(l2, cl: Clean, obs_id: str, det: int, rnd: int, seed: int, grid: int = GRID_PX,
                 jitter: float = JITTER_PX, avoid: np.ndarray | None = None, kinds: dict | None = None,
                 start: int = 0) -> list[Fake]:
    """One fake per grid cell (kinds by KIND_SHARE), at the cell centre + jitter, none within MOVER_CLEAR_PX of the
    positions in `avoid` (movers in this photo); each with a control point at its cell's corner."""
    rng = np.random.default_rng(seed)
    kinds = KIND_SHARE if kinds is None else kinds
    names, p = list(kinds), np.array(list(kinds.values()), np.float64)
    p = p / p.sum()
    ny, nx = cl.shape
    xpose = float(l2.header.get("XPOSURE", 113.6)) if getattr(l2, "header", None) is not None else 113.6
    out: list[Fake] = []
    k = start
    ncy, ncx = max(1, ny // grid), max(1, nx // grid)
    offy, offx = (ny - ncy * grid) / 2.0, (nx - ncx * grid) / 2.0
    for ci in range(ncy):
        for cj in range(ncx):
            kind = names[int(rng.choice(len(names), p=p))]
            x = offx + (cj + 0.5) * grid + rng.uniform(-jitter, jitter)
            y = offy + (ci + 0.5) * grid + rng.uniform(-jitter, jitter)
            if min(x, y, nx - 1 - x, ny - 1 - y) < EDGE_PX:
                continue
            if avoid is not None and len(avoid) and np.min(np.hypot(avoid[:, 0] - x, avoid[:, 1] - y)) < MOVER_CLEAR_PX:
                continue
            row = _base_row(kind, obs_id, det, rnd, k, x, y)
            row.update({"cell_i": ci, "cell_j": cj, "ctrl_x": offx + (cj + 1.0) * grid,
                        "ctrl_y": offy + (ci + 1.0) * grid})
            if kind == "point":
                K, ix, iy = point_stamp(l2.epsf, x, y)
                snr = _loguni(rng, *POINT_SNR)
            elif kind == "fuzzy":
                size = float(rng.choice(FUZZY_SIZES))
                prof = "gauss" if rng.uniform() < 0.5 else "coma"
                tail = bool(size <= FUZZY_TAIL_MAX_SIZE and rng.uniform() < FUZZY_TAIL_SHARE)
                pa = rng.uniform(0, 2 * np.pi)
                K, ix, iy = fuzzy_stamp(l2.epsf, x, y, size, prof, tail, pa)
                row.update({"size": size, "profile": prof, "tail": tail, "theta_deg": float(np.degrees(pa)) % 360})
                snr = _loguni(rng, *FUZZY_SNR)
            else:
                rate = _loguni(rng, *TRAIL_RATE)
                L = rate * xpose / 3600.0 / 6.152
                th = rng.uniform(0, np.pi)
                K, ix, iy = trail_stamp(l2.epsf, x, y, L, th)
                row.update({"length_px": L, "theta_deg": float(np.degrees(th)), "rate_arcsec_h": rate})
                snr = _loguni(rng, *TRAIL_SNR)
            f = _finish(row, K, ix, iy, cl, l2.epsf, l2, None, snr)
            if f is not None:
                out.append(f)
            k += 1
    return out


@dataclass
class Movers:
    """Movers of one (session, partner pair): sky position at t_ref, velocity, flux at the reference wavelength."""
    df: pd.DataFrame        # mover_id, ra0, dec0, ve, vn ("/h), rate, pa, flux_ref_ujy, lam_ref, spectrum, snr_target
    t_ref: float


def make_movers(l2, cl: Clean, obs_id: str, det: int, rnd: int, seed: int, n: int = MOVERS_PER_GROUP,
                grid: int = GRID_PX) -> Movers:
    """n movers on the reference photo (the pair's SWIR detector, middle exposure): cells spread over the photo, a
    flux giving the target S/N there (optimal S/N of its trail in that photo), a direction, a rate."""
    from skyshift_core import pixel_to_sky
    rng = np.random.default_rng(seed)
    ny, nx = cl.shape
    xpose = float(l2.header.get("XPOSURE", 113.6))
    rows = []
    cells = [(i, j) for i in range(ny // grid) for j in range(nx // grid)]
    pick = rng.choice(len(cells), size=min(n, len(cells)), replace=False)
    offy, offx = (ny - (ny // grid) * grid) / 2.0, (nx - (nx // grid) * grid) / 2.0
    spec_names = [s for s, _ in MOVER_SPECTRA]
    spec_p = np.array([p for _, p in MOVER_SPECTRA])
    for m, c in enumerate(pick):
        ci, cj = cells[int(c)]
        x = offx + (cj + 0.5) * grid + rng.uniform(-JITTER_PX, JITTER_PX)
        y = offy + (ci + 0.5) * grid + rng.uniform(-JITTER_PX, JITTER_PX)
        step = 0.0 if rng.uniform() < MOVER_STATIC_SHARE else _loguni(rng, *MOVER_STEP_ARCSEC)
        rate = step / EXPOSURE_GAP_S * 3600.0
        pa = rng.uniform(0, 360.0)
        ve, vn = rate * np.sin(np.radians(pa)), rate * np.cos(np.radians(pa))
        ra, dec = pixel_to_sky(l2.wcs, np.array([x]), np.array([y]))
        L = rate * xpose / 3600.0 / 6.152
        th = _pixel_angle(l2, float(ra[0]), float(dec[0]), ve, vn, xpose)
        K, ix, iy = trail_stamp(l2.epsf, x, y, L, th)
        snr = _loguni(rng, *MOVER_SNR)
        lam = float(l2.wave.wavelength(np.array([x]), np.array([y]))[0])
        spec = spec_names[int(rng.choice(len(spec_names), p=spec_p / spec_p.sum()))]
        flux = amp_for(K, ix, iy, cl, l2.epsf, x, y, snr) * cl.conv
        rows.append({"mover_id": f"{obs_id}D{det}:{rnd}:m{m}", "ra0": float(ra[0]) % 360.0, "dec0": float(dec[0]),
                     "ve": ve, "vn": vn, "rate": rate, "pa": pa, "step_arcsec": step, "flux_ref_ujy": flux,
                     "lam_ref": lam, "spectrum": spec, "snr_target": snr})
    return Movers(pd.DataFrame(rows), float(l2.mjd_avg))


def _mover_sky(mv: Movers, mjd: float) -> tuple[np.ndarray, np.ndarray]:
    from skyshift_core import tangent_deproject
    df = mv.df
    dt_h = (mjd - mv.t_ref) * 24.0
    ra, dec = tangent_deproject(df["ra0"].to_numpy(np.float64), df["dec0"].to_numpy(np.float64),
                                df["ve"].to_numpy(np.float64) * dt_h, df["vn"].to_numpy(np.float64) * dt_h)
    return np.asarray(ra) % 360.0, np.asarray(dec)


def _pixel_angle(l2, ra: float, dec: float, ve: float, vn: float, xpose: float) -> float:
    """Pixel-frame angle (radians) of a sky motion (ve, vn "/h) at (ra, dec) in this photo."""
    from skyshift_core import sky_to_pixel
    if ve == 0 and vn == 0:
        return 0.0
    d = 30.0 / 3600.0
    n = np.hypot(ve, vn)
    x, y = sky_to_pixel(l2.wcs, np.array([ra, ra + ve / n * d / np.cos(np.radians(dec))]), np.array([dec, dec + vn / n * d]))
    return float(np.arctan2(y[1] - y[0], x[1] - x[0]))


def mover_fakes(l2, cl: Clean, mv: Movers, obs_id: str, det: int, rnd: int) -> list[Fake]:
    """The movers' images in this photo: position at MJD-AVG, a trail of rate x XPOSURE along the motion, flux from
    the spectrum at the wavelength under it."""
    from skyshift_core import sky_to_pixel
    if mv is None or not len(mv.df):
        return []
    xpose = float(l2.header.get("XPOSURE", 113.6))
    ra, dec = _mover_sky(mv, float(l2.mjd_avg))
    x, y = sky_to_pixel(l2.wcs, ra, dec)
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    ny, nx = cl.shape
    out = []
    for k, r in enumerate(mv.df.itertuples()):
        if not (np.isfinite(x[k]) and np.isfinite(y[k])) or min(x[k], y[k], nx - 1 - x[k], ny - 1 - y[k]) < EDGE_PX:
            continue
        L = r.rate * xpose / 3600.0 / 6.152
        th = _pixel_angle(l2, ra[k], dec[k], r.ve, r.vn, xpose)
        K, ix, iy = trail_stamp(l2.epsf, x[k], y[k], L, th)
        lam = float(l2.wave.wavelength(np.array([x[k]]), np.array([y[k]]))[0])
        flux = r.flux_ref_ujy * float(_spectrum(r.spectrum, lam) / _spectrum(r.spectrum, r.lam_ref))
        row = _base_row("mover", obs_id, det, rnd, 10000 + k, x[k], y[k])
        row.update({"length_px": L, "theta_deg": float(np.degrees(th)) % 180.0, "rate_arcsec_h": r.rate,
                    "pa_deg": r.pa, "step_arcsec": r.step_arcsec, "mover_id": r.mover_id, "spectrum": r.spectrum,
                    "snr_target": r.snr_target, "ctrl_x": x[k] + 0.5 * GRID_PX, "ctrl_y": y[k] + 0.5 * GRID_PX})
        f = _finish(row, K, ix, iy, cl, l2.epsf, l2, flux / cl.conv, None)
        if f is not None:
            f.row["snr_target"] = r.snr_target
            out.append(f)
    return out


def inject(l2, fakes: list[Fake], k: float, seed: int):
    """A shallow copy of l2 with the fakes added to IMAGE and their photon noise to IMAGE and VARIANCE (k x signal,
    the way SPHEREx's VARIANCE holds a source's noise). FLAGS, WCS, ePSF are the photo's own."""
    data = np.array(l2.data, dtype=np.float64, copy=True)
    model = np.zeros(data.shape)
    ny, nx = data.shape
    for f in fakes:
        h = f.K.shape[0] // 2
        y0, y1, x0, x1 = f.iy - h, f.iy + h + 1, f.ix - h, f.ix + h + 1
        sy0, sy1, sx0, sx1 = max(y0, 0), min(y1, ny), max(x0, 0), min(x1, nx)
        if sy0 >= sy1 or sx0 >= sx1:
            continue
        model[sy0:sy1, sx0:sx1] += f.row["amp"] * f.K[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0]
    out = copy.copy(l2)
    if k > 0:
        rng = np.random.default_rng(seed)
        on = model > 0
        data[on] += model[on] + rng.normal(0.0, 1.0, int(on.sum())) * np.sqrt(k * model[on])
        if l2.variance is not None:
            var = np.array(l2.variance, dtype=np.float64, copy=True)
            var[on] += k * model[on]
            out.variance = var.astype(np.asarray(l2.variance).dtype)
    else:
        data += model
    out.data = data.astype(np.asarray(l2.data).dtype)
    return out, model


# =====================================================================================================================
# 4. the probe: read what dots' own functions return (results unchanged) and the per-fake diagnostics
# =====================================================================================================================

PROBED = ("_measure", "photo_context", "detect", "fit_glow", "residual_companions", "mf_pass")


class Probe:
    """Wraps PROBED names in the dots module. Inside dots._measure of a photo with fakes it keeps, per photo: the
    first-pass detections, the second pass's, the glow stars, the matched filter's peaks / dots and its S/N at each
    fake on the residual it searched (psf.mf_at), the block scale, and the background the first photo_context
    subtracted under each fake. Every wrapper returns the original's result untouched (peak_floor, a settings
    variant, is the one exception: it lowers mf_pass's peaks-file floor)."""

    def __init__(self):
        self.orig: dict = {}
        self.cur: tuple | None = None
        self.fakes: dict = {}           # (obs_id, det) -> list[Fake]
        self.clean: dict = {}           # (obs_id, det) -> Clean
        self.diag: dict = {}            # (obs_id, det) -> DataFrame (fake_id + diagnostics)
        self.glow: dict = {}            # (obs_id, det) -> glow stars (x, y, r_core, r_glow, saturated)
        self.rows: dict = {}            # (obs_id, det) -> truth rows (kept after the photo is measured)
        self.cap: dict = {}
        self.peak_floor: float | None = None

    def install(self) -> None:
        from skyshift_pipeline import dots as D
        if self.orig:
            return
        for n in PROBED:
            self.orig[n] = getattr(D, n)
        o = self.orig

        def _measure(store, l2, obs_id, detector, *a, **kw):
            key = (str(obs_id), int(detector))
            self.cur, self.cap = key, {"ctx": [], "det1": [], "guard": [], "comp": None, "mf": None, "glow": None}
            try:
                return o["_measure"](store, l2, obs_id, detector, *a, **kw)
            finally:
                try:
                    self._close(key, l2)
                finally:
                    self.cur, self.cap = None, {}

        def photo_context(l2, timing=None, pre=None):
            ctx = o["photo_context"](l2, timing, pre)
            if self.cur is not None and self.fakes.get(self.cur):
                self.cap["ctx"].append(self._ctx_values(ctx))
            return ctx

        def detect(*a, **kw):
            r = o["detect"](*a, **kw)
            if self.cur is not None:
                self.cap["guard" if kw.get("near") is not None else "det1"].append(
                    np.c_[np.asarray(r["x"], np.float64), np.asarray(r["y"], np.float64), np.asarray(r["snr"], np.float64)])
            return r

        def fit_glow(*a, **kw):
            g = o["fit_glow"](*a, **kw)
            if self.cur is not None:
                s = g.stars
                self.cap["glow"] = pd.DataFrame({k: np.asarray(s[k]) for k in ("x", "y", "r_core", "r_glow", "saturated")})
            return g

        def residual_companions(*a, **kw):
            c = o["residual_companions"](*a, **kw)
            if self.cur is not None:
                self.cap["comp"] = np.c_[np.asarray(c.dots["x"], np.float64), np.asarray(c.dots["y"], np.float64)]
            return c

        def mf_pass(resid, noise, bad, epsf, dot_x, dot_y, **kw):
            if self.peak_floor is not None:
                kw["min_peak_snr"] = self.peak_floor
            r = o["mf_pass"](resid, noise, bad, epsf, dot_x, dot_y, **kw)
            if self.cur is not None:
                self._mf_values(r, resid, noise, bad, epsf)
            return r

        for n, f in (("_measure", _measure), ("photo_context", photo_context), ("detect", detect),
                     ("fit_glow", fit_glow), ("residual_companions", residual_companions), ("mf_pass", mf_pass)):
            setattr(D, n, f)

    def uninstall(self) -> None:
        from skyshift_pipeline import dots as D
        for n, f in self.orig.items():
            setattr(D, n, f)
        self.orig = {}

    # -- per photo values ------------------------------------------------------------------------------------------
    def _xy(self, key) -> tuple[np.ndarray, np.ndarray]:
        fk = self.fakes.get(key, [])
        return (np.array([f.row["x"] for f in fk], np.float64), np.array([f.row["y"] for f in fk], np.float64))

    def _ctx_values(self, ctx) -> dict:
        """Background and noise the pipeline used under each fake: the background-loss fraction (the share of the fake's
        flux its own-shape filter loses to the background map's rise under it, against the clean photo's map) and
        the detection noise / the clean noise at the fake."""
        fk = self.fakes[self.cur]
        cl = self.clean[self.cur]
        loss, nrel = np.full(len(fk), np.nan), np.full(len(fk), np.nan)
        ny, nx = cl.shape
        lvl = np.asarray(ctx.bkg_level, np.float64)
        for k, f in enumerate(fk):
            h = f.K.shape[0] // 2
            db = _patch(lvl, f.ix, f.iy, h, fill=0.0) - _patch(cl.bkg, f.ix, f.iy, h, fill=0.0)
            kk = float(np.sum(f.K * f.K))
            loss[k] = float(np.sum(f.K * db) / (f.row["amp"] * kk)) if kk > 0 and f.row["amp"] > 0 else np.nan
            iy, ix = min(max(f.iy, 0), ny - 1), min(max(f.ix, 0), nx - 1)
            n0 = cl.noise[iy, ix]
            nrel[k] = float(ctx.noise[iy, ix] / n0) if np.isfinite(n0) and n0 > 0 else np.nan
        return {"bkg_loss": loss, "noise_det_rel": nrel}

    def _mf_values(self, r, resid, noise, bad, epsf) -> None:
        from skyshift_pipeline.psf import mf_at
        pk = r.peaks
        cap = {"peaks": np.c_[np.asarray(pk["x"], np.float64), np.asarray(pk["y"], np.float64),
                              np.asarray(pk["snr_mf"], np.float64), np.asarray(pk["why"], np.float64)],
               "dots": np.c_[np.asarray(r.dots["x"], np.float64), np.asarray(r.dots["y"], np.float64)],
               "sigma_grid": (r.info.get("stats") or {}).get("sigma_grid")}
        if self.fakes.get(self.cur):
            x, y = self._xy(self.cur)
            cap["snr_truth"] = mf_at(resid, noise, bad, x, y, epsf)
        self.cap["mf"] = cap

    def _close(self, key: tuple, l2) -> None:
        """At the end of dots._measure: the per-fake diagnostics of this photo (into self.diag)."""
        from scipy.spatial import cKDTree
        from skyshift_pipeline import config as C
        if self.cap.get("glow") is not None:
            self.glow[key] = self.cap["glow"]
        fk = self.fakes.get(key)
        if not fk:
            return
        x, y = self._xy(key)
        out = pd.DataFrame({"fake_id": [f.row["fake_id"] for f in fk]})

        def nearest(arr):
            if arr is None or not len(arr):
                return np.full(len(x), np.inf), np.zeros(len(x), int)
            d, i = cKDTree(arr[:, :2]).query(np.c_[x, y], k=1)
            return d, i
        d1 = self.cap["det1"][0] if self.cap["det1"] else None   # the main detection (the first full call)
        d, i = nearest(d1)
        out["det1_d"] = d
        out["det1_snr"] = d1[i, 2] if d1 is not None and len(d1) else np.nan
        out["comp_d"] = nearest(self.cap.get("comp"))[0]
        mf = self.cap.get("mf")
        if mf is not None:
            out["mf_snr_truth"] = mf.get("snr_truth", np.full(len(x), np.nan))
            pk = mf["peaks"]
            d, i = nearest(pk)
            out["mfpeak_d"] = d
            out["mfpeak_snr"] = pk[i, 2] if len(pk) else np.nan
            out["mfpeak_why"] = pk[i, 3] if len(pk) else np.nan
            out["mfdot_d"] = nearest(mf["dots"])[0]
            g = mf.get("sigma_grid")
            if g is not None:
                g = np.asarray(g, np.float64)
                bi, bj = block_of(x, y, np.shape(l2.data), n=g.shape[0])
                out["mf_block_sigma"] = g[bi, bj]
        if self.cap["ctx"]:
            first, last = self.cap["ctx"][0], self.cap["ctx"][-1]
            out["bkg_loss"] = first["bkg_loss"]
            out["bkg_loss_det"] = last["bkg_loss"]
            out["noise_det_rel"] = last["noise_det_rel"]
        gl = self.cap.get("glow")
        if gl is not None and len(gl):
            gx, gy = gl["x"].to_numpy(np.float64), gl["y"].to_numpy(np.float64)
            dd = np.hypot(x[:, None] - gx[None, :], y[:, None] - gy[None, :])
            own_r = np.maximum(C.GLOW_SELF_PX, gl["r_core"].to_numpy(np.float64) + 1.5)
            j = np.argmin(dd, axis=1)
            out["glow_d"] = dd[np.arange(len(x)), j]
            out["glow_own"] = (dd <= own_r[None, :]).any(axis=1)
            out["in_glow"] = (dd <= np.maximum(gl["r_glow"].to_numpy(np.float64), own_r)[None, :]).any(axis=1)
        else:
            out["glow_d"], out["glow_own"], out["in_glow"] = np.inf, False, False
        self.diag[key] = out
        self.release(key)

    def release(self, key: tuple) -> None:
        """Keep a photo's truth rows, drop its maps and stamps (~130 MB a photo)."""
        fk = self.fakes.get(key)
        if fk:
            self.rows[key] = [f.row for f in fk]
        self.fakes[key] = []
        self.clean.pop(key, None)


# =====================================================================================================================
# 5. settings variants, the worker job (one session's partner pair through dots.process_photos)
# =====================================================================================================================

# settings applied to pixels before anything is stored (task 3); each is set in the worker before the run
VARIANTS: dict[str, dict] = {
    "default": {},
    "bkg16": {"bkg_box": 16}, "bkg64": {"bkg_box": 64}, "bkg128": {"bkg_box": 128},
    "deblend1e-3": {"deblend_cont": 1e-3}, "deblend1e-5": {"deblend_cont": 1e-5}, "clean1": {"clean_param": 1.0},
    "guard3": {"guard": 3.0}, "guard10": {"guard": 10.0},
    "peaks35": {"peak_floor": 3.5}, "peaks30": {"peak_floor": 3.0},
    "ext_off": {"ext": False}, "ext_wide": {"ext_scales": (1.5, 2.0, 3.0, 4.0, 6.0, 8.0)},
    "ext_2468": {"ext_scales": (2.0, 4.0, 6.0, 8.0)},
    "mf_phases2": {"mf_phases": 2},
}

_APPLIED: dict = {}
_MODE: dict = {}
PROBE = Probe()


def apply_variant(name: str) -> dict:
    """Set a variant's settings in this process (once; a process runs one variant)."""
    import dataclasses

    from skyshift_pipeline import config as C
    from skyshift_pipeline import dots as D
    if _APPLIED.get("name") == name:
        return _APPLIED
    if _APPLIED:
        raise RuntimeError(f"process already runs variant {_APPLIED['name']}")
    base = name.split("+")
    spec: dict = {}
    for b in base:
        spec.update(VARIANTS[b])
    s = C.DOTS_V2_SETTINGS
    if "deblend_cont" in spec:
        s = dataclasses.replace(s, deblend_cont=float(spec["deblend_cont"]))
    if "clean_param" in spec:
        s = dataclasses.replace(s, clean_param=float(spec["clean_param"]))
    if "guard" in spec:
        s = dataclasses.replace(s, sat_guard_px=float(spec["guard"]))
    C.DOTS_V2_SETTINGS = s
    if "bkg_box" in spec:
        bg, box = D.background, int(spec["bkg_box"])
        D.background = lambda d, m=None, box_=32, filter_size=3, **kw: bg(d, m, box=box, filter_size=filter_size)
    if "ext" in spec:
        C.EXT_PASS = bool(spec["ext"])
    if "ext_scales" in spec:
        C.EXT_SCALES = tuple(spec["ext_scales"])
    if "mf_phases" in spec:      # psf.mf_pass looks matched_filter / mf_peaks up at call time
        from skyshift_pipeline import psf as P
        n_ph, mf0, pk0 = int(spec["mf_phases"]), P.matched_filter, P.mf_peaks
        P.matched_filter = lambda *a, **kw: mf0(*a, **{"phases": n_ph, **kw})
        P.mf_peaks = lambda *a, **kw: pk0(*a, **{"phases": n_ph, **kw})
    if "peak_floor" in spec:
        PROBE.peak_floor = float(spec["peak_floor"])
    _APPLIED.update({"name": name, "spec": spec, "folder": C.zone_detect(None).folder})
    return _APPLIED


def _worker_init() -> None:
    from skyshift_pipeline import log as plog
    plog.setup()


def _refcat_of(rows: list[dict], mjd: float):
    """The fakes of a photo as forced-photometry targets (dots.RefCat at the photo's own epoch, no motion)."""
    from skyshift_pipeline import dots as D
    if not rows:
        return None
    df = pd.DataFrame({"ref_id": [r["fake_id"] for r in rows], "ra": [r["ra"] for r in rows],
                       "dec": [r["dec"] for r in rows], "epoch_mjd": mjd, "pmra": 0.0, "pmdec": 0.0})
    return D.RefCat.from_frame(df)


def cond_map(cl: Clean, cell: int = 8) -> dict:
    """Per 8 x 8 px cell of the clean photo: distance to saturation (cell centre), masked share, noise / the photo's
    median (the area side of the false-alarm rates per condition)."""
    ny, nx = cl.shape
    my, mx = ny // cell, nx // cell
    c = (np.arange(my) * cell + cell // 2), (np.arange(mx) * cell + cell // 2)
    dsat = np.minimum(cl.dsat[np.ix_(c[0], c[1])], 9999.0)
    bad = cl.bad[:my * cell, :mx * cell].reshape(my, cell, mx, cell).mean(axis=(1, 3))
    nz = np.where(np.isfinite(cl.noise), cl.noise, np.nan)[:my * cell, :mx * cell].reshape(my, cell, mx, cell)
    with np.errstate(all="ignore"):
        nrel = np.nanmedian(nz, axis=(1, 3)) / cl.noise_median
    return {"cell": cell, "dsat": dsat.astype(np.float32), "mask": bad.astype(np.float32),
            "noise_rel": nrel.astype(np.float32), "gain_k": cl.gain_k}


def run_job(job: dict) -> dict:
    """One (session, partner pair) of photos through dots.process_photos with fakes (or negated: job["neg"]); writes
    each photo's truth + diagnostics (<out>/truth/), its condition map (<out>/cond/); returns the worker's stats."""
    from skyshift_pipeline import coadd as CO
    from skyshift_pipeline import config as C
    from skyshift_pipeline import dots as D
    t_job = time.time()
    info = apply_variant(job["variant"])
    use_probe = bool(job.get("probe", True))
    if use_probe:
        PROBE.install()
    else:
        PROBE.uninstall()
    origs = PROBE.orig if PROBE.orig else {"photo_context": D.photo_context}
    truth_rows: dict = {}
    out = Path(job["out"])
    root = Path(job["root"])
    (out / "truth").mkdir(parents=True, exist_ok=True)
    (out / "cond").mkdir(parents=True, exist_ok=True)
    photos = job["photos"]
    url2key = {p["url"]: (str(p["obs_id"]), int(p["detector"])) for p in photos}
    rnd, neg, grid = int(job["round"]), bool(job.get("neg")), int(job.get("grid", GRID_PX))
    orig_load, orig_forced = D.load_photo, D.forced_reference
    C.SESSION_STACK = bool(job.get("stacks", True)) and not neg
    if neg:
        C.DOTS_V2_HOLES = False      # the positive photo's hole mask is applied before negating (below)
    timing: dict = {}

    # the movers of this group: from the reference photo (middle exposure of the pair's SWIR detector)
    movers = None
    if not neg and job.get("movers", True):
        sw = sorted([p for p in photos if int(p["detector"]) <= 3], key=lambda p: p["mjd"])
        if len(sw) >= 2:
            ref = sw[len(sw) // 2]
            l2r = orig_load(ref["url"])
            clr = clean_photo(l2r, origs)
            movers = make_movers(l2r, clr, str(ref["obs_id"]), int(ref["detector"]), rnd,
                                 seed_of(job["seed"], rnd, "movers", ref["obs_id"], ref["detector"]), grid=grid)
            del l2r, clr

    def load(url: str):
        l2 = orig_load(url)
        key = url2key.get(url)
        if key is None:
            return l2
        t0 = time.process_time()
        if neg:
            _, m, _ = D.raw_background_holes(l2)
            o = copy.copy(l2)
            data = -np.array(l2.data, dtype=np.float32, copy=True)
            data[m] = np.nan
            o.data = data
            timing[key] = time.process_time() - t0
            return o
        cl = clean_photo(l2, origs)
        np.savez_compressed(out / "cond" / f"{key[0]}D{key[1]}.npz", **cond_map(cl))
        mf = mover_fakes(l2, cl, movers, key[0], key[1], rnd) if movers is not None else []
        avoid = np.array([[f.row["x"], f.row["y"]] for f in mf]) if mf else None
        st = static_fakes(l2, cl, key[0], key[1], rnd, seed_of(job["seed"], rnd, key[0], key[1]), grid=grid,
                          jitter=float(job.get("jitter", JITTER_PX)), avoid=avoid)
        fk = st + mf
        truth_rows[key] = [f.row for f in fk]
        if use_probe:
            PROBE.fakes[key], PROBE.clean[key] = fk, cl
        l2i, _ = inject(l2, fk, cl.gain_k, seed_of(job["seed"], rnd, "noise", key[0], key[1]))
        timing[key] = time.process_time() - t0
        return l2i

    def forced_reference(store, l2, zone):
        rows = truth_rows.get((str(l2.obsid), int(l2.detector)), [])
        ref = _refcat_of(rows, float(l2.mjd_avg))
        return (ref, None, "inject") if ref is not None else (None, None, "")

    D.load_photo = load
    D.forced_reference = forced_reference
    root.mkdir(parents=True, exist_ok=True)
    plist = [(p["obs_id"], int(p["detector"]), p["url"], int(p["session"]), int(p["visit"])) for p in photos]
    tasks = CO.session_tasks(pd.DataFrame(photos)) if C.SESSION_STACK else None
    try:
        st = D.process_photos(str(root), plist, zone=None, prefetch=False, stacks=tasks)
    finally:
        D.load_photo, D.forced_reference = orig_load, orig_forced
    for key in list(PROBE.fakes):
        PROBE.release(key)
    for key in [k for k in url2key.values()]:
        rows = PROBE.rows.pop(key, None) or truth_rows.get(key)
        diag = PROBE.diag.pop(key, None)
        gl = PROBE.glow.pop(key, None)
        if gl is not None and not neg:
            gl.to_parquet(out / "cond" / f"{key[0]}D{key[1]}_glow.parquet")
        if rows:
            df = pd.DataFrame(rows)[FAKE_COLUMNS]
            if diag is not None:
                df = df.merge(diag, on="fake_id", how="left")
            df["inject_cpu_s"] = timing.get(key, np.nan)
            df.to_parquet(out / "truth" / f"{key[0]}D{key[1]}.parquet", index=False)
    if movers is not None:
        movers.df.assign(t_ref=movers.t_ref, session=int(photos[0]["session"])).to_parquet(
            out / "truth" / f"movers_{job['name']}.parquet", index=False)
    return {"job": job["name"], "stats": [{k: v for k, v in s.items() if not isinstance(v, (dict, list))} for s in st],
            "tasks": [{"key": t["key"], "detector": t["detector"], "obs_ids": t["obs_ids"], "mjd": t["mjd"]}
                      for t in (tasks or [])],
            "folder": info["folder"], "wall_s": round(time.time() - t_job, 1),
            "inject_cpu_s": {f"{k[0]}D{k[1]}": round(v, 2) for k, v in timing.items()}}


def _mode_guard(neg: bool) -> None:
    if _MODE.get("neg", neg) != neg:
        raise RuntimeError("a process runs either negative or positive jobs")
    _MODE["neg"] = neg


def run_job_safe(job: dict) -> dict:
    """run_job in a pool: never raises (the error goes into the result)."""
    import traceback
    try:
        _mode_guard(bool(job.get("neg")))
        return run_job(job)
    except Exception as e:  # noqa: BLE001
        return {"job": job["name"], "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-3000:]}


# =====================================================================================================================
# 6. subsets and jobs
# =====================================================================================================================


def subset_rows(subset: Path, cache: Path) -> list[tuple[str, pd.DataFrame, dict]]:
    """(zone, photo rows with a local url, target) per target of a subset / targets file (rerun.bench_photos), less
    the detectors not listed (`detectors`) and the exposures after the first `max_exposures` of each session; only
    photos in the photo cache."""
    sys.path.insert(0, str(HERE))
    import rerun as R
    targets = json.loads(Path(subset).read_text())["targets"]
    out = []
    for zone, rows, t in R.bench_photos(DATA, targets):
        rows = rows.copy()
        rows["url"] = [R.cached_url(r, cache) for r in rows.itertuples()]
        rows = rows[rows["url"].map(lambda u: not str(u).startswith("http"))]
        if t.get("detectors"):
            rows = rows[rows["detector"].astype(int).isin([int(d) for d in t["detectors"]])]
        if t.get("max_exposures"):
            keep = []
            for _, g in rows.groupby("session"):
                ids = g.sort_values("mjd")["obs_id"].drop_duplicates().tolist()[: int(t["max_exposures"])]
                keep.append(g[g["obs_id"].isin(ids)])
            rows = pd.concat(keep) if keep else rows.iloc[:0]
        if len(rows):
            out.append((zone, rows.sort_values(["mjd", "detector"]).reset_index(drop=True), t))
    return out


def make_jobs(subset: Path, cache: Path, run_dir: Path, variant: str, rnd: int, grid: int, seed: int, neg: bool,
              stacks: bool, movers: bool) -> list[dict]:
    jobs = []
    for zone, rows, t in subset_rows(subset, cache):
        cond = t.get("condition", zone)
        for (s, pair), g in rows.groupby([rows["session"].astype(int), (rows["detector"].astype(int) - 1) % 3]):
            photos = [{"obs_id": str(r.obs_id), "detector": int(r.detector), "url": str(r.url),
                       "session": int(r.session), "visit": int(r.visit), "mjd": float(r.mjd), "zone": zone,
                       "condition": cond} for r in g.itertuples()]
            jobs.append({"name": f"{zone}_s{s}_p{pair + 1}", "zone": zone, "condition": cond, "photos": photos,
                         "variant": variant, "round": rnd, "grid": grid, "seed": seed, "neg": neg, "stacks": stacks,
                         "movers": movers, "out": str(run_dir), "root": str(run_dir / "root")})
    return jobs


def cmd_run(a) -> None:
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed
    run_dir = Path(a.out) / "runs" / a.name
    run_dir.mkdir(parents=True, exist_ok=True)
    root = run_dir / "root"
    root.mkdir(exist_ok=True)
    jobs = make_jobs(Path(a.subset), Path(a.photo_cache), run_dir, a.variant, a.round, a.grid, a.seed, a.neg,
                     not a.no_stacks, not a.no_movers)
    done_dir = run_dir / "jobs"
    done_dir.mkdir(exist_ok=True)
    todo = [j for j in jobs if not (done_dir / f"{j['name']}.json").exists()]
    n_ph = sum(len(j["photos"]) for j in todo)
    log(f"run {a.name}: {len(jobs)} jobs ({len(todo)} to do, {n_ph} photos), variant {a.variant}, round {a.round}, "
        f"grid {a.grid}, neg {a.neg}, {a.workers} workers -> {run_dir}")
    (run_dir / "run.json").write_text(json.dumps({"name": a.name, "variant": a.variant, "round": a.round,
                                                  "grid": a.grid, "seed": a.seed, "neg": a.neg,
                                                  "stacks": not a.no_stacks, "subset": str(a.subset),
                                                  "jobs": [{k: v for k, v in j.items() if k != "photos"} | {
                                                      "photos": [p["obs_id"] + "D" + str(p["detector"])
                                                                 for p in j["photos"]]} for j in jobs]}, indent=1))
    if not todo:
        return
    t0 = time.time()
    todo.sort(key=lambda j: -len(j["photos"]))
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=a.workers, mp_context=ctx, initializer=_worker_init,
                             max_tasks_per_child=4) as ex:
        futs = {ex.submit(run_job_safe, j): j for j in todo}
        for k, f in enumerate(as_completed(futs), 1):
            r = f.result()
            j = futs[f]
            if r.get("error"):
                log(f"job {j['name']} FAILED: {r['error']}\n{r.get('trace', '')}")
                continue
            r["condition"], r["zone"] = j["condition"], j["zone"]
            (done_dir / f"{j['name']}.json").write_text(json.dumps(r, default=str))
            log(f"run {a.name}: {k}/{len(todo)} jobs, {j['name']} {len(j['photos'])} photos {r['wall_s']} s, "
                f"{time.time() - t0:.0f} s")


# =====================================================================================================================
# 7. matching: the worker's files against the truth (and, for negative runs, every row is a false alarm)
# =====================================================================================================================

PIX_ARCSEC = 6.152
CELL_DEG2 = lambda cell: (cell * PIX_ARCSEC / 3600.0) ** 2  # noqa: E731
DENS_EDGES = (1e3, 3e3, 1e4)        # dots (S/N >= 5) per deg^2 in a 64-px block: sparse .. crowded
SAT_EDGES = (20.0, 60.0)            # px to the nearest OVERFLOW/NONLINEAR pixel: near / mid / far
NOISE_EDGES = (1.2, 2.0)            # noise / the photo's median: flat sky .. a bright background (glow, nebula)
EDGE_STRIP_PX = 64                  # edge_bin 1: within this of the photo edge (D2's top-row ramp, D5's bottom rows)
STACK_MATCH_ARCSEC = 9.0
PEAK_WHY = {0: "peak_below", 1: "peak_guard", 2: "peak_mirror", 3: "peak_excluded"}
# streaks.py's why bits, in the order a miss is named by (the first set bit of this list)
STREAK_WHY_ORDER = ((16, "masked"), (32, "saturated"), (64, "edge"), (8, "dot"), (2, "lumpy"), (4, "round"),
                    (1, "point"), (256, "axis"), (128, "streak_flag"))
DOT_COLS = ["x", "y", "snr", "snr_mf", "flux_ujy", "sep_flag", "dot_flag", "flags"]


def _read(path: Path, cols: list[str] | None = None) -> pd.DataFrame | None:
    if not path.exists():
        return None
    import pyarrow.parquet as pq
    have = pq.read_schema(path).names
    use = [c for c in cols if c in have] if cols else None
    return pq.read_table(path, columns=use).to_pandas()


def photo_outputs(root: Path, folder: str, obs: str, det: int) -> dict:
    """The worker's files of one photo (None where absent): dots, peaks, ext, streaks, forced."""
    from skyshift_pipeline import dots as D
    from skyshift_pipeline import streaks as SK
    n = f"{obs}D{int(det)}.parquet"
    return {"dots": _read(root / folder / n, DOT_COLS),
            "peaks": _read(root / D.peaks_rel(obs, det, folder), ["x", "y", "snr_mf", "why", "flags"]),
            "ext": _read(root / D.ext_rel(obs, det, folder), ["x", "y", "snr_ext", "snr_point", "ext_scale",
                                                              "near_dot_px"]),
            "streaks": _read(root / SK.streaks_rel(obs, det, folder), ["x", "y", "x1", "y1", "x2", "y2", "length_px",
                                                                         "theta_deg", "snr", "snr_local", "why",
                                                                         "rate_arcsec_h"]),
            "forced": _read(root / D.forced_rel(obs, det), ["ref_id", "flux_psf_ujy", "flux_psf_err_ujy",
                                                            "flux_ap2_ujy", "flux_ap2_err_ujy", "flags"])}


def mf_scale_grid(root: Path, folder: str, obs: str, det: int) -> np.ndarray | None:
    """The matched filter's block threshold scale of one photo (footer mf.sigma_grid, 1 where <= MF_SIGMA_MIN_SCALE:
    a clean block), or None."""
    import pyarrow.parquet as pq
    from skyshift_pipeline import config as C
    path = root / folder / f"{obs}D{int(det)}.parquet"
    if not path.exists():
        return None
    try:
        md = pq.read_metadata(path).metadata or {}
        mf = json.loads(md.get(b"skyshift_photo", b"{}")).get("mf", {})
        g = mf.get("sigma_grid")
        if g is None:
            return None
        if isinstance(g, (list, tuple)) and len(g) == 2 and isinstance(g[0], (int, float)):
            g = g[1]
        g = np.nan_to_num(np.asarray(g, np.float64), nan=1.0)
        return np.where(g > float(C.MF_SIGMA_MIN_SCALE), g, 1.0)
    except Exception:   # noqa: BLE001 - an unreadable footer: no scale
        return None


def _scale_at(grid: np.ndarray | None, x: np.ndarray, y: np.ndarray, shape: tuple) -> np.ndarray:
    if grid is None:
        return np.full(len(np.atleast_1d(x)), np.nan)
    bi, bj = block_of(np.asarray(x), np.asarray(y), shape, grid.shape[0])
    return grid[bi, bj]


def _nearest(px: np.ndarray, py: np.ndarray, df: pd.DataFrame | None, k: int = 1):
    """Distance and row index of the k nearest rows of df (x, y) to each point; inf / -1 where df is empty."""
    from scipy.spatial import cKDTree
    n = len(px)
    if df is None or not len(df):
        return np.full((n, k), np.inf), np.full((n, k), -1)
    kk = min(k, len(df))
    d, i = cKDTree(df[["x", "y"]].to_numpy(np.float64)).query(np.c_[px, py], k=kk)
    d, i = d.reshape(n, kk), i.reshape(n, kk)
    if kk < k:
        d = np.c_[d, np.full((n, k - kk), np.inf)]
        i = np.c_[i, np.full((n, k - kk), -1)]
    return d, i


def seg_dist(px, py, x1, y1, x2, y2) -> np.ndarray:
    """Distance of points (px, py) to the segments (x1, y1)-(x2, y2) (broadcast)."""
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    with np.errstate(invalid="ignore", divide="ignore"):
        t = np.clip(np.where(L2 > 0, ((px - x1) * vx + (py - y1) * vy) / L2, 0.0), 0.0, 1.0)
    return np.hypot(px - (x1 + t * vx), py - (y1 + t * vy))


def _ang(a, b) -> np.ndarray:
    d = np.abs((np.asarray(a, np.float64) - np.asarray(b, np.float64)) % 180.0)
    return np.minimum(d, 180.0 - d)


def match_points(tr: pd.DataFrame, o: dict, x: np.ndarray, y: np.ndarray, prefix: str = "") -> pd.DataFrame:
    """Match the fakes of tr (kind, size, length_px, theta_deg) placed at (x, y) to one photo's outputs o: nearest dot,
    peak, ext row, streak, dots on the trail, and the found flags. prefix 'c_' for the control points."""
    n = len(tr)
    r = np.array([match_radius(k, s, L) for k, s, L in zip(tr["kind"], tr["size"], tr["length_px"])], np.float64)
    L = tr["length_px"].to_numpy(np.float64)
    th = np.radians(tr["theta_deg"].to_numpy(np.float64))
    hx, hy = 0.5 * L * np.cos(th), 0.5 * L * np.sin(th)
    out: dict = {}
    dots = o.get("dots")
    d, i = _nearest(x, y, dots, k=8)
    out["dot_d"] = d[:, 0]
    for c in ("snr", "snr_mf", "flux_ujy", "sep_flag", "dot_flag", "flags"):
        if dots is not None and c in dots and len(dots):
            v = dots[c].to_numpy()
            out[f"dot_{c}"] = np.where(i[:, 0] >= 0, v[np.clip(i[:, 0], 0, None)], np.nan if v.dtype.kind == "f" else -1)
        else:
            out[f"dot_{c}"] = np.full(n, np.nan)
    inside = d <= np.maximum(r, 0.5 * L + 1.0)[:, None]
    out["n_dots_in"] = inside.sum(axis=1)
    outside = np.where(d > r[:, None], d, np.inf)
    out["blend_d"] = outside.min(axis=1)
    pk = o.get("peaks")
    d, i = _nearest(x, y, pk)
    out["peak_d"] = d[:, 0]
    out["peak_snr"] = pk["snr_mf"].to_numpy(np.float64)[np.clip(i[:, 0], 0, None)] if pk is not None and len(pk) else np.nan
    out["peak_why"] = pk["why"].to_numpy(np.float64)[np.clip(i[:, 0], 0, None)] if pk is not None and len(pk) else np.nan
    ex = o.get("ext")
    d, i = _nearest(x, y, ex)
    out["ext_d"] = d[:, 0]
    if ex is not None and len(ex):
        j = np.clip(i[:, 0], 0, None)
        out["ext_snr"] = ex["snr_ext"].to_numpy(np.float64)[j]
        out["ext_scale"] = ex["ext_scale"].to_numpy(np.float64)[j]
    else:
        out["ext_snr"] = out["ext_scale"] = np.full(n, np.nan)
    # dots on the trail's segment (any length; a point: within r of its centre)
    on = np.zeros(n, bool)
    if dots is not None and len(dots):
        dd, ii = _nearest(x, y, dots, k=16)
        dxy = dots[["x", "y"]].to_numpy(np.float64)
        for k_ in range(dd.shape[1]):
            ok = ii[:, k_] >= 0
            j = np.clip(ii[:, k_], 0, None)
            sd = seg_dist(dxy[j, 0], dxy[j, 1], x - hx, y - hy, x + hx, y + hy)
            on |= ok & (sd <= np.maximum(1.0, np.minimum(r, 1.5)))
    out["dot_on_trail"] = on
    # streaks: centre within max(1.5, 0.25 L), angle within ANGLE_DEG (L >= 4 px), or the fake's centre on it
    sk = o.get("streaks")
    out["streak_d"] = np.full(n, np.inf)
    out["streak_ang"] = out["streak_snr"] = out["streak_why"] = out["streak_len"] = np.full(n, np.nan)
    if sk is not None and len(sk):
        sx, sy = sk["x"].to_numpy(np.float64), sk["y"].to_numpy(np.float64)
        dc = np.hypot(x[:, None] - sx[None, :], y[:, None] - sy[None, :])
        ds = seg_dist(x[:, None], y[:, None], sk["x1"].to_numpy(np.float64)[None, :],
                      sk["y1"].to_numpy(np.float64)[None, :], sk["x2"].to_numpy(np.float64)[None, :],
                      sk["y2"].to_numpy(np.float64)[None, :])
        ang = _ang(tr["theta_deg"].to_numpy(np.float64)[:, None], sk["theta_deg"].to_numpy(np.float64)[None, :])
        ok = ((dc <= np.maximum(1.5, 0.25 * L)[:, None]) | (ds <= 1.5)) & ((ang <= ANGLE_DEG) | (L[:, None] < 4.0))
        score = np.where(ok, dc, np.inf)
        j = np.argmin(score, axis=1)
        hit = np.isfinite(score[np.arange(n), j])
        out["streak_d"] = np.where(hit, dc[np.arange(n), j], np.inf)
        for c, v in (("streak_ang", ang[np.arange(n), j]), ("streak_snr", sk["snr_local"].to_numpy(np.float64)[j]),
                     ("streak_why", sk["why"].to_numpy(np.float64)[j]),
                     ("streak_len", sk["length_px"].to_numpy(np.float64)[j])):
            out[c] = np.where(hit, v, np.nan)
    df = pd.DataFrame(out)
    found_dot = df["dot_d"].to_numpy() <= r
    found_peak = df["peak_d"].to_numpy() <= r
    found_ext = df["ext_d"].to_numpy() <= r
    found_streak = np.isfinite(df["streak_d"].to_numpy())
    clean_streak = found_streak & (np.nan_to_num(df["streak_why"].to_numpy(), nan=1) == 0)
    kind = tr["kind"].to_numpy()
    trail = (kind == "trail") | (kind == "mover")
    df["r_match"] = r
    df["found_dot"] = found_dot
    df["found_peak"] = found_peak
    df["found_ext"] = found_ext
    df["found_streak"] = clean_streak
    df["found_streak_any"] = found_streak
    # the headline: what a later step can use (dots, and for fuzzy the ext rows, for trails the streak rows)
    df["found"] = found_dot | ((kind == "fuzzy") & found_ext) | (trail & clean_streak)
    df["found_any"] = df["found"] | found_peak | found_ext | (trail & (found_streak | on))
    df["dot_flagged"] = found_dot & (np.nan_to_num(df["dot_dot_flag"].to_numpy(np.float64), nan=0) > 0)
    return df.add_prefix(prefix)


def miss_cause(t: pd.DataFrame, guard_px: float) -> np.ndarray:
    """Why each fake that was not `found` was missed, first that applies (the order of the list in the report)."""
    n = len(t)
    cause = np.full(n, "", dtype=object)
    g = lambda c, fill=np.nan: (t[c].to_numpy(np.float64) if c in t else np.full(n, fill))  # noqa: E731
    found = t["found"].to_numpy(bool)
    kind = t["kind"].to_numpy()
    rules = [
        ("masked", g("mask_r1") >= 0.5),
        ("edge", g("edge_px") < 16),
        ("glow_own", t["glow_own"].fillna(False).to_numpy(bool) if "glow_own" in t else np.zeros(n, bool)),
        ("sat_guard", g("dsat_px") < guard_px),
        ("background", (kind == "fuzzy") & (g("bkg_loss_det") >= 0.25)),
    ]
    trail = ((kind == "trail") | (kind == "mover")) & (np.nan_to_num(g("length_px"), nan=0.0) >= 2.0)
    sk_any = t["found_streak_any"].to_numpy(bool) if "found_streak_any" in t else np.zeros(n, bool)
    sw = np.nan_to_num(g("streak_why"), nan=0).astype(np.int64)
    for bit, name in STREAK_WHY_ORDER:
        rules.append((f"streak_rejected:{name}", trail & sk_any & ((sw & bit) > 0)))
    on = t["dot_on_trail"].to_numpy(bool) if "dot_on_trail" in t else np.zeros(n, bool)
    rules.append(("pieces", trail & on))
    pk = t["found_peak"].to_numpy(bool)
    why = np.nan_to_num(g("peak_why"), nan=-1).astype(int)
    for code, name in PEAK_WHY.items():
        rules.append((name, pk & (why == code)))
    rules += [
        ("blend", g("blend_d") <= BLEND_PX),
        ("dropped", g("det1_d") <= g("r_match")),         # the first pass had it, a later step removed it
        ("faint", g("mf_snr_truth") < 4.0),              # the matched filter at the fake (on its residual) < the floor
        ("block_scale", (g("mf_snr_truth") >= 4.0) & (g("mf_snr_truth") < 5.0 * np.maximum(1.0, np.nan_to_num(
            g("mf_block_sigma"), nan=1.0)))),
        ("offset", g("dot_d") <= 2.5 * g("r_match")),
        ("streak_rejected", ((kind == "trail") | (kind == "mover")) & t["found_streak_any"].to_numpy(bool)),
        ("ext_only", t["found_ext"].to_numpy(bool)),
    ]
    left = ~found
    for name, m in rules:
        m = np.asarray(m, bool) & left & (cause == "")
        cause[m] = name
    cause[left & (cause == "")] = "other"
    return cause


def dens_map(dots: pd.DataFrame | None, fakes: pd.DataFrame | None, shape: tuple, n: int = 32) -> np.ndarray:
    """Dots with S/N >= 5 per deg^2 in each of the n x n footer blocks, less the dots on the fakes (within 3 px)."""
    out = np.zeros((n, n))
    if dots is None or not len(dots):
        return out
    d = dots[dots["snr"].to_numpy() >= 5.0] if "snr" in dots else dots
    x, y = d["x"].to_numpy(np.float64), d["y"].to_numpy(np.float64)
    if fakes is not None and len(fakes):
        dd, _ = _nearest(x, y, fakes[["x", "y"]])
        keep = dd[:, 0] > 3.0
        x, y = x[keep], y[keep]
    bi, bj = block_of(x, y, shape, n)
    np.add.at(out, (bi, bj), 1.0)
    area = (shape[0] / n * PIX_ARCSEC / 3600.0) * (shape[1] / n * PIX_ARCSEC / 3600.0)
    return out / area


def _bin(v, edges) -> np.ndarray:
    return np.searchsorted(np.asarray(edges, np.float64), np.nan_to_num(np.asarray(v, np.float64), nan=0.0),
                           side="right")


def local_bins(x: np.ndarray, y: np.ndarray, cond: dict, dens: np.ndarray, glow: pd.DataFrame | None,
               shape: tuple, scale: np.ndarray | None = None) -> pd.DataFrame:
    """Each position's local condition: dens (/deg^2), dsat, mask, noise_rel (8-px cell), in_glow, and their bins;
    edge_bin (within EDGE_STRIP_PX of the edge) and mf_clean (the matched filter's block scale is 1; scale: the
    photo's mf_scale_grid, else NaN / False)."""
    cell = int(cond["cell"])
    ci = np.clip((np.asarray(y) // cell).astype(int), 0, cond["dsat"].shape[0] - 1)
    cj = np.clip((np.asarray(x) // cell).astype(int), 0, cond["dsat"].shape[1] - 1)
    bi, bj = block_of(x, y, shape, dens.shape[0])
    out = pd.DataFrame({"dens": dens[bi, bj], "c_dsat": cond["dsat"][ci, cj], "c_mask": cond["mask"][ci, cj],
                        "c_noise_rel": cond["noise_rel"][ci, cj]})
    out["in_glow_c"] = False
    if glow is not None and len(glow):
        gx, gy, gr = (glow[c].to_numpy(np.float64) for c in ("x", "y", "r_glow"))
        from scipy.spatial import cKDTree
        t = cKDTree(np.c_[gx, gy])
        rmax = float(np.nanmax(gr)) if len(gr) else 0.0
        if rmax > 0:
            near = t.query_ball_point(np.c_[x, y], r=rmax)
            out["in_glow_c"] = [any(np.hypot(gx[j] - xx, gy[j] - yy) <= gr[j] for j in js)
                                for js, xx, yy in zip(near, x, y)]
    out["dens_bin"] = _bin(out["dens"], DENS_EDGES)
    out["sat_bin"] = 2 - _bin(out["c_dsat"], SAT_EDGES)          # 0 far, 1 mid, 2 near
    out["noise_bin"] = _bin(out["c_noise_rel"], NOISE_EDGES)
    xa, ya = np.asarray(x, np.float64), np.asarray(y, np.float64)
    e = np.minimum(np.minimum(xa, shape[1] - 1 - xa), np.minimum(ya, shape[0] - 1 - ya))
    out["edge_bin"] = (e < EDGE_STRIP_PX).astype(np.int8)
    sc = _scale_at(scale, xa, ya, shape)
    out["mf_scale"] = sc
    out["mf_clean"] = np.nan_to_num(sc, nan=np.inf) <= 1.0
    return out


def area_table(cond: dict, dens: np.ndarray, glow: pd.DataFrame | None, shape: tuple,
               scale: np.ndarray | None = None) -> pd.DataFrame:
    """deg^2 of one photo per (dens_bin, sat_bin, noise_bin, glow, edge_bin, mf_clean) over its 8-px cells with < half
    masked."""
    cell = int(cond["cell"])
    my, mx = cond["dsat"].shape
    yy, xx = np.meshgrid(np.arange(my) * cell + cell / 2.0, np.arange(mx) * cell + cell / 2.0, indexing="ij")
    b = local_bins(xx.ravel(), yy.ravel(), cond, dens, glow, shape, scale)
    b = b[b["c_mask"].to_numpy() < 0.5]
    a = b.groupby(["dens_bin", "sat_bin", "noise_bin", "in_glow_c", "edge_bin", "mf_clean"]).size().rename(
        "cells").reset_index()
    a["deg2"] = a["cells"] * CELL_DEG2(cell)
    return a


def _cond_of(cond_dir: Path, obs: str, det: int) -> tuple[dict | None, pd.DataFrame | None]:
    p = cond_dir / f"{obs}D{det}.npz"
    if not p.exists():
        return None, None
    z = np.load(p)
    cond = {k: z[k] for k in z.files}
    cond["cell"] = int(cond["cell"])
    g = cond_dir / f"{obs}D{det}_glow.parquet"
    return cond, (pd.read_parquet(g) if g.exists() else None)


SHAPE = (2040, 2040)
COND_TILE_PX = 128      # the conditions table's tile (p1/conditions, config COND_TILE_PX)


def match_photo(args: tuple) -> dict:
    """One photo of a positive run: truth x outputs -> match/<obs>D<det>.parquet (+ its density map in cond/)."""
    run_dir, folder, obs, det, guard_px, meta = args
    run_dir = Path(run_dir)
    root = run_dir / "root"
    tp = run_dir / "truth" / f"{obs}D{det}.parquet"
    if not tp.exists():
        return {"photo": f"{obs}D{det}", "n": 0}
    tr = pd.read_parquet(tp)
    o = photo_outputs(root, folder, obs, det)
    m = match_points(tr, o, tr["x"].to_numpy(np.float64), tr["y"].to_numpy(np.float64))
    cx = tr["ctrl_x"].to_numpy(np.float64)
    cy = tr["ctrl_y"].to_numpy(np.float64)
    ok = np.isfinite(cx) & np.isfinite(cy) & (cx >= EDGE_PX) & (cy >= EDGE_PX) & (cx < SHAPE[1] - EDGE_PX) & (
        cy < SHAPE[0] - EDGE_PX)
    if ok.any():
        # a control is valid when no fake lies within 24 px of it
        dd, _ = _nearest(cx, cy, tr[["x", "y"]])
        ok &= dd[:, 0] > 24.0
    c = match_points(tr, o, np.where(ok, cx, -1e4), np.where(ok, cy, -1e4), prefix="c_")
    c["c_valid"] = ok
    t = pd.concat([tr.reset_index(drop=True), m, c[["c_valid", "c_found", "c_found_any", "c_found_dot",
                                                    "c_found_peak", "c_found_ext", "c_found_streak", "c_peak_d",
                                                    "c_peak_snr", "c_r_match", "c_found_streak_any",
                                                    "c_dot_on_trail"]]], axis=1)
    fo = o.get("forced")
    if fo is not None and len(fo):
        fo = fo.drop_duplicates("ref_id").set_index("ref_id")
        j = fo.reindex(t["fake_id"])
        t["forced_flux"] = j["flux_psf_ujy"].to_numpy(np.float64)
        t["forced_err"] = j["flux_psf_err_ujy"].to_numpy(np.float64)
        t["forced_ap_flux"] = j["flux_ap2_ujy"].to_numpy(np.float64)
    else:
        t["forced_flux"] = t["forced_err"] = t["forced_ap_flux"] = np.nan
    t["cause"] = miss_cause(t, guard_px)
    cond, glow = _cond_of(run_dir / "cond", obs, det)
    dens = dens_map(o.get("dots"), tr, SHAPE)
    np.save(run_dir / "cond" / f"{obs}D{det}_dens.npy", dens.astype(np.float32))
    if cond is not None:
        lb = local_bins(t["x"].to_numpy(np.float64), t["y"].to_numpy(np.float64), cond, dens, glow, SHAPE,
                        mf_scale_grid(root, folder, obs, det))
        t = pd.concat([t, lb[["dens", "dens_bin", "sat_bin", "noise_bin", "in_glow_c", "edge_bin", "mf_scale",
                              "mf_clean"]]], axis=1)
    for k, v in meta.items():
        t[k] = v
    t["n_dots_photo"] = 0 if o.get("dots") is None else len(o["dots"])
    # the conditions table's tile (cond.py: COND_TILE_PX tiles from the origin, tile = ty * ntx + tx): join key
    # (obs_id, detector, cond_tile) to <folder>_cond/<obs_id>D<det>.parquet once the worker writes it
    tp_ = COND_TILE_PX
    ntx = -(-SHAPE[1] // tp_)
    t["cond_tile"] = ((t["y"].to_numpy() // tp_).astype(int) * ntx + (t["x"].to_numpy() // tp_).astype(int)
                      ).astype(np.int16)
    (run_dir / "match").mkdir(exist_ok=True)
    t.to_parquet(run_dir / "match" / f"{obs}D{det}.parquet", index=False)
    return {"photo": f"{obs}D{det}", "n": len(t)}


def neg_photo(args: tuple) -> dict:
    """One photo of a negative run: every dot / peak / ext / streak row is a false alarm; with its local condition
    (from the positive run's cond/: cond_dir) -> match/neg_<obs>D<det>.parquet and match/area_<obs>D<det>.parquet."""
    run_dir, folder, obs, det, cond_dir, meta = args
    run_dir, cond_dir = Path(run_dir), Path(cond_dir)
    o = photo_outputs(run_dir / "root", folder, obs, det)
    cond, glow = _cond_of(cond_dir, obs, det)
    dp = cond_dir / f"{obs}D{det}_dens.npy"
    if cond is None or not dp.exists():
        return {"photo": f"{obs}D{det}", "n": -1}
    dens = np.load(dp).astype(np.float64)
    rows = []
    for src, df, s_col in (("dot", o.get("dots"), "snr_mf"), ("peak", o.get("peaks"), "snr_mf"),
                           ("ext", o.get("ext"), "snr_ext"), ("streak", o.get("streaks"), "snr_local")):
        if df is None or not len(df):
            continue
        r = pd.DataFrame({"src": src, "x": df["x"].to_numpy(np.float64), "y": df["y"].to_numpy(np.float64)})
        s = df[s_col].to_numpy(np.float64) if s_col in df else np.full(len(df), np.nan)
        if src == "dot":
            r["snr_ap"] = df["snr"].to_numpy(np.float64)
            s = np.where(np.isfinite(s), s, r["snr_ap"].to_numpy())
            r["dot_flag"] = df["dot_flag"].to_numpy(np.int64) if "dot_flag" in df else 0
            r["sep_flag"] = df["sep_flag"].to_numpy(np.int64) if "sep_flag" in df else 0
        if src in ("peak", "streak"):
            r["why"] = df["why"].to_numpy(np.int64)
        if src == "ext":
            r["ext_scale"] = df["ext_scale"].to_numpy(np.int64)
        r["s"] = s
        rows.append(r)
    fa = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["src", "x", "y", "s"])
    # clean blocks as the real (positive) photo has them: the sky's property, as production would decide it
    scale = mf_scale_grid(cond_dir.parent / "root", folder, obs, det)
    if scale is None:
        scale = mf_scale_grid(run_dir / "root", folder, obs, det)
    if len(fa):
        lb = local_bins(fa["x"].to_numpy(np.float64), fa["y"].to_numpy(np.float64), cond, dens, glow, SHAPE, scale)
        fa = pd.concat([fa.reset_index(drop=True), lb], axis=1)
    for k, v in meta.items():
        fa[k] = v
    ar = area_table(cond, dens, glow, SHAPE, scale)
    for k, v in meta.items():
        ar[k] = v
    (run_dir / "match").mkdir(exist_ok=True)
    fa.to_parquet(run_dir / "match" / f"neg_{obs}D{det}.parquet", index=False)
    ar.to_parquet(run_dir / "match" / f"area_{obs}D{det}.parquet", index=False)
    return {"photo": f"{obs}D{det}", "n": len(fa)}


def match_stacks(run_dir: Path, jobs: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Session stacks against the fakes, through each stack's per-photo rows (x, y in that photo): each single-photo
    (static) fake's stack source (expected: glitch, one photo carries it), and each mover's stack source (its rows
    within max(1.5, 0.5 L) px of the mover in the photos; else the nearest source within STACK_MATCH_ARCSEC of its sky
    position at the source's time) with the measured rate."""
    from skyshift_core import tangent_deproject
    from skyshift_pipeline import coadd as CO
    root = run_dir / "root"
    srows, mrows = [], []
    for j in jobs:
        mv_path = run_dir / "truth" / f"movers_{j['job']}.parquet"
        mv = pd.read_parquet(mv_path) if mv_path.exists() else None
        for t in j.get("tasks", []):
            src = _read(root / CO.session_rel(t["key"]))
            ph = _read(root / CO.session_rel(t["key"], photos=True))
            if src is None:
                continue
            det = int(t["detector"])
            s = src.set_index("stack_row")
            votes: dict = {}
            for obs in t["obs_ids"]:
                tp = run_dir / "truth" / f"{obs}D{det}.parquet"
                if not tp.exists() or ph is None:
                    continue
                tr = pd.read_parquet(tp, columns=["fake_id", "kind", "x", "y", "snr_opt", "length_px", "mover_id"])
                p = ph[ph["obs_id"].astype(str) == str(obs)].reset_index(drop=True)
                d, i = _nearest(tr["x"].to_numpy(np.float64), tr["y"].to_numpy(np.float64), p)
                lim = np.maximum(1.5, 0.5 * tr["length_px"].to_numpy(np.float64))
                hit = d[:, 0] <= lim
                sr = p["stack_row"].to_numpy()[np.clip(i[:, 0], 0, None)] if len(p) else np.full(len(tr), -1)
                st = tr["kind"].to_numpy() != "mover"
                r = pd.DataFrame({"fake_id": tr["fake_id"], "kind": tr["kind"], "snr_opt": tr["snr_opt"],
                                  "key": t["key"], "detector": det, "n_photos": len(t["obs_ids"]),
                                  "stack_found": hit, "stack_d": d[:, 0]})[st]
                for c in ("glitch", "glitch_share", "snr_rest", "n_exp", "snr_mf", "snr", "motion_snr"):
                    v = s[c].reindex(sr).to_numpy() if c in s else np.full(len(tr), np.nan)
                    r[f"stack_{c}"] = np.where(hit, v, np.nan)[st]
                srows.append(r)
                for mid, h, row in zip(tr["mover_id"], hit, sr):
                    if mid and h:
                        votes.setdefault(mid, []).append(int(row))
            if mv is None or not len(mv) or not len(src):
                continue
            smjd = src["mjd"].to_numpy(np.float64)
            sra, sdec = src["ra"].to_numpy(np.float64), src["dec"].to_numpy(np.float64)
            for m in mv.itertuples():
                dt_h = (smjd - m.t_ref) * 24.0
                ra, dec = tangent_deproject(np.full(len(src), m.ra0), np.full(len(src), m.dec0), m.ve * dt_h,
                                            m.vn * dt_h)
                sep = np.hypot((np.asarray(ra) - sra) * np.cos(np.radians(sdec)), np.asarray(dec) - sdec) * 3600.0
                v = votes.get(m.mover_id, [])
                if v:
                    row_ = max(set(v), key=v.count)
                    k = int(np.flatnonzero(src["stack_row"].to_numpy() == row_)[0])
                    how, n_votes = "photos", v.count(row_)
                else:
                    k = int(np.argmin(sep))
                    how, n_votes = ("sky" if sep[k] <= STACK_MATCH_ARCSEC else ""), 0
                hit = bool(how)
                row = {"mover_id": m.mover_id, "key": t["key"], "detector": det, "n_photos": len(t["obs_ids"]),
                       "rate": m.rate, "step_arcsec": m.step_arcsec, "pa": m.pa, "spectrum": m.spectrum,
                       "snr_target": m.snr_target, "stack_found": hit, "stack_how": how, "stack_votes": n_votes,
                       "stack_sep": float(sep[k])}
                for c in ("glitch", "glitch_share", "snr_rest", "snr_mf", "snr", "n_exp", "rate_arcsec_h",
                          "rate_err_arcsec_h", "pa_deg", "motion_snr", "n_pos"):
                    row[f"stack_{c}"] = src[c].iloc[k] if (hit and c in src) else np.nan
                mrows.append(row)
    S = pd.concat(srows, ignore_index=True) if srows else pd.DataFrame()
    M = pd.DataFrame(mrows)
    return S, M


def _photo_meta(jobs: list[dict]) -> dict:
    """obs_id, det -> zone, condition, session, job (from the jobs' run.json entries)."""
    out = {}
    for j in jobs:
        for p in j["photos"]:
            obs, det = p.rsplit("D", 1)
            out[(obs, int(det))] = {"zone": j["zone"], "condition": j["condition"], "job": j["name"]}
    return out


def cmd_match(a) -> None:
    from concurrent.futures import ProcessPoolExecutor
    run_dir = Path(a.out) / "runs" / a.name
    run = json.loads((run_dir / "run.json").read_text())
    done = [json.loads(p.read_text()) for p in sorted((run_dir / "jobs").glob("*.json"))]
    if not done:
        log(f"match {a.name}: no finished jobs")
        return
    folder = done[0]["folder"]
    meta = _photo_meta(run["jobs"])
    ok_jobs = {d["job"] for d in done}
    photos = [(k, v) for k, v in meta.items() if v["job"] in ok_jobs]
    guard = guard_of(run["variant"])
    t0 = time.time()
    if getattr(a, "stacks_only", False):
        if not run["neg"] and run.get("stacks", True):
            S, M = match_stacks(run_dir, done)
            S.to_parquet(run_dir / "stack_static.parquet", index=False)
            M.to_parquet(run_dir / "stack_movers.parquet", index=False)
        log(f"match {a.name}: stacks only, {time.time() - t0:.0f} s")
        return
    if run["neg"]:
        cond_dir = Path(a.out) / "runs" / a.cond_run / "cond"
        args = [(str(run_dir), folder, o, d, str(cond_dir), m) for (o, d), m in photos]
        fn = neg_photo
    else:
        args = [(str(run_dir), folder, o, d, guard, m) for (o, d), m in photos]
        fn = match_photo
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        res = list(ex.map(fn, args, chunksize=2))
    bad = [r["photo"] for r in res if r["n"] < 0]
    if bad:
        log(f"match {a.name}: {len(bad)} photos without a condition map in {a.cond_run}: {bad[:5]}")
    mdir = run_dir / "match"
    if run["neg"]:
        fa = [pd.read_parquet(p) for p in sorted(mdir.glob("neg_*.parquet"))]
        ar = [pd.read_parquet(p) for p in sorted(mdir.glob("area_*.parquet"))]
        pd.concat(fa, ignore_index=True).to_parquet(run_dir / "neg.parquet", index=False)
        pd.concat(ar, ignore_index=True).to_parquet(run_dir / "area.parquet", index=False)
    else:
        parts = [pd.read_parquet(p) for p in sorted(mdir.glob("*.parquet"))
                 if not p.name.startswith(("neg_", "area_"))]
        pd.concat(parts, ignore_index=True).to_parquet(run_dir / "fakes.parquet", index=False)
        if run.get("stacks", True):
            S, M = match_stacks(run_dir, done)
            S.to_parquet(run_dir / "stack_static.parquet", index=False)
            M.to_parquet(run_dir / "stack_movers.parquet", index=False)
    cost = []
    for d in done:
        for s in d["stats"]:
            cost.append({k: s.get(k) for k in ("obs_id", "detector", "status", "n_dots", "n_forced", "n_peaks",
                                                 "n_ext", "n_streaks", "read_s", "detect_s", "forced_s", "write_s",
                                                 "bytes", "forced_bytes")} | {"job": d["job"],
                                                                              "condition": d.get("condition")})
    cost = pd.DataFrame(cost)
    root = run_dir / "root"
    if len(cost):
        from skyshift_pipeline import dots as D
        from skyshift_pipeline import streaks as SK

        def size(rel):
            p = root / rel
            return p.stat().st_size if p.exists() else 0
        cost["peaks_bytes"] = [size(D.peaks_rel(o, d, folder)) for o, d in zip(cost["obs_id"], cost["detector"])]
        cost["ext_bytes"] = [size(D.ext_rel(o, d, folder)) for o, d in zip(cost["obs_id"], cost["detector"])]
        cost["streak_bytes"] = [size(SK.streaks_rel(o, d, folder)) for o, d in zip(cost["obs_id"], cost["detector"])]
        cost["inject_cpu_s"] = [d_.get("inject_cpu_s", {}).get(f"{o}D{dd}", np.nan) for o, dd, d_ in zip(
            cost["obs_id"], cost["detector"], [next(x for x in done if x["job"] == j) for j in cost["job"]])]
        cost.to_parquet(run_dir / "cost.parquet", index=False)
    log(f"match {a.name}: {len(photos)} photos in {time.time() - t0:.0f} s")


def guard_of(variant: str) -> float:
    """The saturation guard (px) a variant runs with."""
    from skyshift_pipeline import config as C
    g = float(C.DETECT_SAT_GUARD_PX)
    for b in variant.split("+"):
        g = float(VARIANTS.get(b, {}).get("guard", g))
    return g


# =====================================================================================================================
# 8. report: recovery curves (S/N at 50 / 90%), miss causes, settings, false-alarm thresholds, cost
# =====================================================================================================================

STEP_EDGES = (-0.1, 0.01, 2, 6, 20, 60)
STEP_LABELS = ("0 (static)", "0–2", "2–6", "6–20", "20–50")
SNR_EDGES = (2.0, 3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0, 60.0)
FA_LEVELS = (1.0, 10.0, 100.0)     # false alarms per deg^2 per photo
SKY_PHOTOS = (856_377, 1_628_230)
DET_NAMES = {1: "D1", 2: "D2", 3: "D3", 4: "D4", 5: "D5", 6: "D6"}


def logistic_fit(snr: np.ndarray, found: np.ndarray, c: float = 0.0) -> dict:
    """Binomial fit of p(s) = c + (1 - c) A / (1 + exp(-(ln s - ln m) / w)) (c: the chance level, from the controls).
    Returns A (plateau), s50 and s90 (S/N where the chance-corrected recovery A / (1 + ...) reaches 0.5 / 0.9; NaN when
    the plateau stays below), n."""
    from scipy.optimize import minimize
    s = np.asarray(snr, np.float64)
    f = np.asarray(found, bool)
    ok = np.isfinite(s) & (s > 0)
    s, f = s[ok], f[ok]
    out = {"n": int(s.size), "A": np.nan, "s50": np.nan, "s90": np.nan, "w": np.nan}
    if s.size < 20 or f.all() or not f.any():
        return out
    c = float(np.clip(c, 0.0, 0.95))
    ls = np.log(s)

    def nll(p):
        with np.errstate(over="ignore"):
            A = 1.0 / (1.0 + np.exp(-p[0]))
            q = c + (1.0 - c) * A / (1.0 + np.exp(-(ls - p[1]) / np.exp(p[2])))
        q = np.clip(q, 1e-9, 1 - 1e-9)
        return -float(np.sum(np.where(f, np.log(q), np.log1p(-q))))
    best = None
    for m0 in (np.log(3.0), np.log(5.0), np.log(8.0), np.log(15.0)):
        r = minimize(nll, np.array([3.0, m0, np.log(0.15)]), method="Nelder-Mead",
                     options={"xatol": 1e-4, "fatol": 1e-6, "maxiter": 4000})
        if best is None or r.fun < best.fun:
            best = r
    A = 1.0 / (1.0 + np.exp(-best.x[0]))
    m, w = best.x[1], np.exp(best.x[2])
    out.update({"A": float(A), "w": float(w)})
    for q, key in ((0.5, "s50"), (0.9, "s90")):
        if A > q:
            out[key] = float(np.exp(m - w * np.log(A / q - 1.0)))
    return out


def fit_boot(snr, found, c, nboot: int = 0, seed: int = 1) -> dict:
    r = logistic_fit(snr, found, c)
    if nboot and r["n"] >= 40:
        rng = np.random.default_rng(seed)
        s, f = np.asarray(snr), np.asarray(found)
        b50, b90 = [], []
        for _ in range(nboot):
            i = rng.integers(0, len(s), len(s))
            q = logistic_fit(s[i], f[i], c)
            b50.append(q["s50"])
            b90.append(q["s90"])
        r["s50_lo"], r["s50_hi"] = (float(np.nanpercentile(b50, 16)), float(np.nanpercentile(b50, 84))) \
            if np.isfinite(b50).sum() > 5 else (np.nan, np.nan)
        r["s90_lo"], r["s90_hi"] = (float(np.nanpercentile(b90, 16)), float(np.nanpercentile(b90, 84))) \
            if np.isfinite(b90).sum() > 5 else (np.nan, np.nan)
    return r


def load_runs(out: Path, names: list[str], peak_floor: float, guard_px: float | None = None) -> pd.DataFrame:
    """The matched fakes of positive runs, with found_peak / found_any / cause recomputed at the analysis peak floor
    (the runs keep peaks from S/N 3; the worker writes from 4)."""
    parts = []
    for n in names:
        p = out / "runs" / n / "fakes.parquet"
        if not p.exists():
            log(f"report: {p} missing")
            continue
        f = pd.read_parquet(p)
        f["run"] = n
        parts.append(f)
    if not parts:
        return pd.DataFrame()
    F = pd.concat(parts, ignore_index=True)
    return refloor(F, peak_floor, guard_px)


def refloor(F: pd.DataFrame, peak_floor: float, guard_px: float | None = None) -> pd.DataFrame:
    F = F.copy()
    kind = F["kind"].to_numpy()
    trail = (kind == "trail") | (kind == "mover")
    F["found_peak"] = (F["peak_d"].to_numpy() <= F["r_match"].to_numpy()) & (
        np.nan_to_num(F["peak_snr"].to_numpy(np.float64), nan=0.0) >= peak_floor)
    F["found_any"] = F["found"] | F["found_peak"] | F["found_ext"] | (trail & (F["found_streak_any"] | F["dot_on_trail"]))
    if "c_peak_snr" in F:     # the controls at the same floor (chance level of found_any)
        F["c_found_peak"] = (F["c_peak_d"].to_numpy() <= F["c_r_match"].to_numpy()) & (
            np.nan_to_num(F["c_peak_snr"].to_numpy(np.float64), nan=0.0) >= peak_floor)
        F["c_found_any"] = F["c_found"] | F["c_found_peak"] | F["c_found_ext"] | (
            trail & (F["c_found_streak_any"] | F["c_dot_on_trail"]))
    F["cause"] = miss_cause(F, 6.0 if guard_px is None else guard_px)
    return F


def chance(F: pd.DataFrame, col: str = "c_found") -> float:
    v = F.loc[F["c_valid"].astype(bool), col] if "c_valid" in F else F[col]
    return float(v.mean()) if len(v) else 0.0


def _fmt(v, nd=1) -> str:
    if v is None or (isinstance(v, (float, np.floating)) and not np.isfinite(v)):
        return "–"
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        return f"{v:.{nd}f}"
    return str(v)


def md_table(rows: list[dict], cols: list[str], heads: list[str] | None = None) -> str:
    heads = heads or cols
    out = ["| " + " | ".join(heads) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join(r.get(c, "") if isinstance(r.get(c, ""), str) else _fmt(r.get(c))
                                     for c in cols) + " |")
    return "\n".join(out)


def curve_rows(F: pd.DataFrame, by: list[str], found: str = "found", snr: str = "snr_opt", nboot: int = 0,
               cols_extra: dict | None = None) -> list[dict]:
    rows = []
    if not len(F):
        return rows
    for key, g in F.groupby(by, observed=True, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        c = chance(g, "c_" + found) if "c_" + found in g else 0.0
        r = fit_boot(g[snr].to_numpy(), g[found].to_numpy(bool), c, nboot)
        row = {b: (str(k) if not isinstance(k, (int, np.integer)) else int(k)) for b, k in zip(by, key)}
        row.update({"n": r["n"], "chance": 100 * c, "plateau": 100 * r["A"], "s50": r["s50"], "s90": r["s90"]})
        if nboot:
            row["s50_ci"] = f"{_fmt(r.get('s50_lo'))}–{_fmt(r.get('s50_hi'))}"
            row["s90_ci"] = f"{_fmt(r.get('s90_lo'))}–{_fmt(r.get('s90_hi'))}"
        for lo, hi in ((4.0, 5.0), (5.0, 6.0), (6.0, 8.0), (8.0, 12.0), (12.0, 1e9)):
            m = (g[snr] >= lo) & (g[snr] < hi)
            p = g.loc[m, found].mean() if m.any() else np.nan
            row[f"f{lo:g}"] = 100 * (p - c) / (1 - c) if np.isfinite(p) and c < 1 else np.nan
        for k, fn in (cols_extra or {}).items():
            row[k] = fn(g)
        rows.append(row)
    return rows


BIN_COLS = ["f4", "f5", "f6", "f8", "f12"]
BIN_HEADS = ["4–5", "5–6", "6–8", "8–12", "≥ 12"]


def curve_md(rows, by, nboot=False, extra: list[str] | None = None, extra_heads: list[str] | None = None) -> str:
    cols = by + ["n", "chance", "plateau", "s50"] + (["s50_ci"] if nboot else []) + ["s90"] + (
        ["s90_ci"] if nboot else []) + BIN_COLS + (extra or [])
    heads = by + ["n", "chance %", "plateau %", "S/N 50%"] + (["±1σ"] if nboot else []) + ["S/N 90%"] + (
        ["±1σ"] if nboot else []) + [f"% {h}" for h in BIN_HEADS] + (extra_heads or extra or [])
    return md_table(rows, cols, heads)


def cause_md(F: pd.DataFrame, min_snr: float = 0.0) -> str:
    m = (~F["found"]) & (F["snr_opt"] >= min_snr)
    rows = []
    for kind, g in F[m].groupby("kind"):
        vc = g["cause"].value_counts()
        tot = int(m[F["kind"] == kind].sum())
        rows.append({"kind": kind, "missed": tot, "causes": ", ".join(f"{k} {100 * v / tot:.0f}%" for k, v in vc.items()
                                                                       if v / tot >= 0.01)})
    return md_table(rows, ["kind", "missed", "causes"], ["kind", f"missed (S/N ≥ {min_snr:g})", "causes, first that applies"])


def fa_rates(N: pd.DataFrame, A: pd.DataFrame, by: list[str], srcs: tuple, levels=FA_LEVELS,
             grid=np.arange(3.0, 15.01, 0.05)) -> list[dict]:
    """Per bin of `by`: false alarms (rows of srcs) per deg^2 per photo at or above each S/N of grid, and the S/N where
    that rate falls to each level (1, 10, 100 /deg^2/photo); masked cells are out of both counts and area."""
    rows = []
    N = N[(N["src"].isin(srcs)) & (N["c_mask"] < 0.5)] if len(N) else N
    for key, a in A.groupby(by, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        area = float(a["deg2"].sum())
        sel = np.ones(len(N), bool)
        for b, k in zip(by, key):
            sel &= (N[b] == k).to_numpy()
        s = np.sort(N.loc[sel, "s"].to_numpy(np.float64))
        if area <= 0:
            continue
        n_ge = len(s) - np.searchsorted(s, grid, side="left")
        rate = n_ge / area
        row = {b: (str(k) if not isinstance(k, (int, np.integer, bool, np.bool_)) else int(k)) for b, k in zip(by, key)}
        row["deg2"] = area
        row["photos"] = int(a["photo"].nunique()) if "photo" in a else np.nan
        for lv in levels:
            above = np.flatnonzero(rate > lv)
            if not len(above):
                row[f"s@{lv:g}"] = f"< {grid[0]:g}"          # already below at the grid's start
            elif above[-1] == len(grid) - 1:
                row[f"s@{lv:g}"] = f"> {grid[-1]:g}"         # never below in the grid
            else:
                row[f"s@{lv:g}"] = float(grid[above[-1] + 1])
        for t in (3.5, 4.0, 4.5, 5.0, 6.0):
            row[f"r{t:g}"] = float(rate[np.argmin(np.abs(grid - t))])
        rows.append(row)
    return rows


def fa_md(rows, by) -> str:
    cols = by + ["photos", "deg2", "s@1", "s@10", "s@100", "r3.5", "r4", "r4.5", "r5", "r6"]
    heads = by + ["photos", "deg²", "S/N at 1", "S/N at 10", "S/N at 100", "≥ 3.5", "≥ 4", "≥ 4.5", "≥ 5", "≥ 6"]
    return md_table(rows, cols, heads)


def neg_with_photo(out: Path, name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """neg rows and area rows with their photo and detector (from the per-photo match files)."""
    d = out / "runs" / name / "match"
    nn, aa = [], []
    for p in sorted(d.glob("neg_*.parquet")):
        ph = p.stem[4:]
        det = int(ph.rsplit("D", 1)[1])
        x = pd.read_parquet(p)
        x["photo"], x["detector"] = ph, det
        nn.append(x)
        a = pd.read_parquet(d / f"area_{ph}.parquet")
        a["photo"], a["detector"] = ph, det
        aa.append(a)
    N = pd.concat(nn, ignore_index=True) if nn else pd.DataFrame()
    A = pd.concat(aa, ignore_index=True) if aa else pd.DataFrame()
    if len(N):
        for c in ("dot_flag", "sep_flag", "why"):
            if c in N:
                N[c] = N[c].fillna(0).astype(np.int64)
        N["in_glow_c"] = N["in_glow_c"].astype(bool)
        if "edge_bin" not in N:
            N["edge_bin"] = 0
            N["mf_clean"] = False
        N["mf_clean"] = N["mf_clean"].fillna(False).astype(bool)
    if len(A):
        A["in_glow_c"] = A["in_glow_c"].astype(bool)
        if "edge_bin" not in A:
            A["edge_bin"] = 0
            A["mf_clean"] = False
        A["mf_clean"] = A["mf_clean"].fillna(False).astype(bool)
    return N, A


def cost_of(out: Path, names: list[str]) -> pd.DataFrame:
    parts = []
    for n in names:
        p = out / "runs" / n / "cost.parquet"
        if p.exists():
            c = pd.read_parquet(p)
            c["run"] = n
            parts.append(c[c["obs_id"].notna()])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def peak_rows_at(out: Path, name: str, floors=(3.0, 3.5, 4.0)) -> dict:
    """Peaks-file rows per photo at each floor, and bytes per row, from a positive run's peaks files (floor 3)."""
    from skyshift_pipeline import dots as D
    d = out / "runs" / name
    done = [json.loads(p.read_text()) for p in sorted((d / "jobs").glob("*.json"))]
    if not done:
        return {}
    folder = done[0]["folder"]
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows = {f: [] for f in floors}
    mb = {f: [] for f in floors}
    for j in done:
        for s in j["stats"]:
            if not s.get("obs_id"):
                continue
            p = d / "root" / D.peaks_rel(s["obs_id"], s["detector"], folder)
            if not p.exists():
                continue
            t = pq.read_table(p)
            snr = t.column("snr_mf").to_numpy()
            for f in floors:   # each floor's file as the worker writes it (dots.parquet_bytes: zstd, split floats)
                part = t.filter(pa.array(snr >= f))
                rows[f].append(part.num_rows)
                mb[f].append(len(D.parquet_bytes(part)) / 1e6)
    return {"rows_per_photo": {f: float(np.mean(v)) for f, v in rows.items() if v},
            "mb_per_photo": {f: float(np.mean(v)) for f, v in mb.items() if v},
            "bytes_per_row": float(np.sum(mb[floors[0]]) * 1e6 / max(1, np.sum(rows[floors[0]]))),
            "photos": len(rows[floors[0]])}


def cmd_report(a) -> None:
    spec = json.loads(Path(a.spec).read_text())
    out = Path(a.out)
    floor = float(spec.get("peak_floor", 4.0))
    md: list[str] = []
    res: dict = {"peak_floor": floor}
    H = lambda t: md.append(f"\n### {t}\n")  # noqa: E731

    # --- 1. recovery on the bench ---------------------------------------------------------------------------------
    F = load_runs(out, spec.get("main", []), floor)
    res["n_fakes"] = int(len(F))
    if len(F):
        res["photos"] = int(F[["obs_id", "detector"]].drop_duplicates().shape[0])
        H("Recovery by kind (bench, all detectors; S/N = optimal S/N for the fake's own shape)")
        rows = curve_rows(F, ["kind"], nboot=100)
        md.append(curve_md(rows, ["kind"], nboot=True))
        res["by_kind"] = rows
        H("Found anywhere (dots, peaks ≥ floor, ext, streak rows, dots on a trail)")
        rows = curve_rows(F, ["kind"], found="found_any")
        md.append(curve_md(rows, ["kind"]))
        res["by_kind_any"] = rows
        for kind in ("point", "fuzzy", "trail", "mover"):
            g = F[F["kind"] == kind]
            if not len(g):
                continue
            H(f"{kind}: by detector")
            rows = curve_rows(g, ["detector"])
            md.append(curve_md(rows, ["detector"]))
            res[f"{kind}_by_det"] = rows
            H(f"{kind}: by field (condition) and detector")
            rows = curve_rows(g, ["condition", "detector"])
            md.append(curve_md(rows, ["condition", "detector"]))
            res[f"{kind}_by_cond_det"] = rows
        P = F[F["kind"] == "point"]
        for b, name in (("dens_bin", "star density (dots S/N ≥ 5 per deg², 64-px block): 0 < 1k, 1 1k–3k, 2 3k–10k, 3 ≥ 10k"),
                        ("sat_bin", "distance to saturated pixels: 0 ≥ 60 px, 1 20–60, 2 < 20"),
                        ("noise_bin", "noise / photo median: 0 < 1.2, 1 1.2–2, 2 ≥ 2"),
                        ("in_glow_c", "inside a modelled bright star's glow radius")):
            if b in P:
                H(f"point: by {name}")
                rows = curve_rows(P, [b])
                md.append(curve_md(rows, [b]))
                res[f"point_by_{b}"] = rows
        Z = F[F["kind"] == "fuzzy"].copy()
        if len(Z):
            H("fuzzy: by size (× PSF FWHM), profile, tail; found = dot or ext row; ext share = found by ext only")
            Z["ext_only"] = Z["found_ext"] & ~Z["found_dot"]
            rows = curve_rows(Z, ["size", "profile"], cols_extra={
                "ext_share": lambda g: 100 * g.loc[g["found"], "ext_only"].mean() if g["found"].any() else np.nan,
                "bkg_loss": lambda g: float(np.nanmedian(g["bkg_loss_det"]))})
            md.append(curve_md(rows, ["size", "profile"], extra=["ext_share", "bkg_loss"],
                               extra_heads=["% by ext only", "median bkg loss"]))
            res["fuzzy_by_size"] = rows
            rows = curve_rows(Z, ["tail"])
            md.append(curve_md(rows, ["tail"]))
        T = F[F["kind"] == "trail"].copy()
        if len(T):
            H("trail: by rate (″/h); found = a clean streak row or a dot within max(1, 0.1 L) px of the centre")
            T["rate_bin"] = pd.cut(T["rate_arcsec_h"], [100, 300, 600, 1200, 2400, 7000],
                                   labels=["150–300", "300–600", "600–1200", "1200–2400", "2400–6000"])
            rows = curve_rows(T, ["rate_bin"], cols_extra={
                "streak_share": lambda g: 100 * g.loc[g["found"], "found_streak"].mean() if g["found"].any() else np.nan,
                "len_px": lambda g: float(np.median(g["length_px"]))})
            md.append(curve_md(rows, ["rate_bin"], extra=["len_px", "streak_share"],
                               extra_heads=["length px", "% with a streak row"]))
            res["trail_by_rate"] = rows
            rows = curve_rows(T, ["rate_bin"], found="found_streak")
            H("trail: the streak row alone")
            md.append(curve_md(rows, ["rate_bin"]))
            res["trail_streak_by_rate"] = rows
        M = F[F["kind"] == "mover"].copy()
        if len(M):
            H("mover (per photo): by step between exposures (″) and spectrum")
            M["step_bin"] = pd.cut(M["step_arcsec"], STEP_EDGES, labels=STEP_LABELS)
            rows = curve_rows(M, ["step_bin"])
            md.append(curve_md(rows, ["step_bin"]))
            res["mover_by_step"] = rows
            rows = curve_rows(M, ["spectrum", "detector"])
            md.append(curve_md(rows, ["spectrum", "detector"]))
            # per mover and session: exposures with a dot, both partner detectors
            g = M.groupby(["run", "mover_id"]).agg(n=("found", "size"), k=("found", "sum"),
                                                   dets=("detector", "nunique"),
                                                   dets_found=("detector", lambda s: s[M.loc[s.index, "found"]].nunique()),
                                                   snr=("snr_target", "first"), step=("step_arcsec", "first"))
            g["sb"] = pd.cut(g["snr"], [0, 3, 5, 8, 12, 30], labels=["< 3", "3–5", "5–8", "8–12", "12–30"])
            t = g.groupby("sb", observed=True).agg(movers=("n", "size"), photos=("n", "mean"), share=("k", "sum"), tot=("n", "sum"),
                                    ge2=("k", lambda s: (s >= 2).mean()),
                                    both=("dets_found", lambda s: (s >= 2).mean())).reset_index()
            t["share"] = 100 * t["share"] / t["tot"]
            t["ge2"] *= 100
            t["both"] *= 100
            H("movers per session (target S/N in the reference SWIR photo): photos with a dot, ≥ 2 exposures, both detectors")
            md.append(md_table(t.to_dict("records"), ["sb", "movers", "photos", "share", "ge2", "both"],
                               ["S/N (ref)", "movers", "photos each", "% photos found", "% ≥ 2 photos", "% both detectors"]))
            res["mover_sessions"] = t.to_dict("records")
        # stacks
        SS, SM = [], []
        for n in spec["main"]:
            for nm, L in (("stack_static", SS), ("stack_movers", SM)):
                p = out / "runs" / n / f"{nm}.parquet"
                if p.exists():
                    L.append(pd.read_parquet(p))
        if SS:
            S = pd.concat(SS, ignore_index=True)
            S["sb"] = pd.cut(S["snr_opt"], [0, 5, 8, 12, 20, 1e9], labels=["< 5", "5–8", "8–12", "12–20", "≥ 20"])
            if "stack_snr_rest" not in S:
                S["stack_snr_rest"] = np.nan
            t = S.groupby(["n_photos", "sb"], observed=True).agg(
                n=("stack_found", "size"), found=("stack_found", "mean"),
                glitch=("stack_glitch", lambda s: s.dropna().astype(bool).mean()),
                rest50=("stack_snr_rest", lambda s: s.dropna().median() if s.notna().any() else np.nan),
                rest90=("stack_snr_rest", lambda s: s.dropna().quantile(0.9) if s.notna().any() else np.nan),
                rest5=("stack_snr_rest", lambda s: (s.dropna() >= 5).mean() if s.notna().any() else np.nan)
            ).reset_index()
            t["found"] *= 100
            t["glitch"] *= 100
            t["rest5"] *= 100
            H("session stacks: one-photo fakes (should be absent or flagged glitch); snr_rest = S/N of the stack "
              "source without its strongest photo (glitch: < 2); ≥ 5: a real source under the fake")
            md.append(md_table(t.to_dict("records"), ["n_photos", "sb", "n", "found", "glitch", "rest50", "rest90",
                                                       "rest5"],
                               ["photos in session", "S/N in its photo", "fakes", "% a stack source",
                                "% of those glitch", "median snr_rest", "90% snr_rest", "% snr_rest ≥ 5"]))
            res["stack_static"] = t.to_dict("records")
            hit = S[S["stack_found"] & (S["snr_opt"] >= 8) & S["stack_snr_rest"].notna()].copy()
            if len(hit):
                hit["rest_bin"] = pd.cut(hit["stack_snr_rest"], [-1e9, 0, 2, 3, 5, 1e9],
                                         labels=["< 0", "0–2 (glitch)", "2–3", "3–5", "≥ 5"])
                rb = hit.groupby(["n_photos", "rest_bin"], observed=True).size().unstack(fill_value=0)
                rb = (100 * rb.div(rb.sum(axis=1), axis=0)).round(1)
                H("session stacks: one-photo fakes at S/N ≥ 8 that are a stack source, by snr_rest (% per row)")
                md.append(md_table([{"n_photos": int(k), **{str(c): float(v) for c, v in r.items()}}
                                    for k, r in rb.iterrows()], ["n_photos"] + [str(c) for c in rb.columns]))
                res["stack_static_rest"] = {str(k): {str(c): float(v) for c, v in r.items()} for k, r in rb.iterrows()}
        if SM:
            S = pd.concat(SM, ignore_index=True)
            S["step_bin"] = pd.cut(S["step_arcsec"], STEP_EDGES, labels=STEP_LABELS)
            S["sb"] = pd.cut(S["snr_target"], [0, 5, 10, 30], labels=["< 5", "5–10", "10–30"])
            S["rate_ok"] = (np.abs(S["stack_rate_arcsec_h"] - S["rate"]) <= np.maximum(
                3 * S["stack_rate_err_arcsec_h"].fillna(np.inf), 0.3 * S["rate"])) & S["stack_found"]
            t = S.groupby(["step_bin", "sb"], observed=True).agg(n=("stack_found", "size"),
                                                                 found=("stack_found", "mean"),
                                                                 rate_ok=("rate_ok", "mean"),
                                                                 msnr=("stack_motion_snr", "median")).reset_index()
            t["found"] *= 100
            t["rate_ok"] *= 100
            H("session stacks: movers (each detector's stack; rate ok = within 3σ or 30%)")
            md.append(md_table(t.to_dict("records"), ["step_bin", "sb", "n", "found", "rate_ok", "msnr"],
                               ["step ″", "S/N (ref)", "stacks", "% found", "% rate ok", "median motion S/N"]))
            res["stack_movers"] = t.to_dict("records")
            S["band"] = np.where(S["detector"] <= 3, "SWIR", "MWIR")
            st = S[S["step_arcsec"] <= 2]
            t = st.groupby(["band", "spectrum", "sb"], observed=True).agg(
                n=("stack_found", "size"), found=("stack_found", "mean"), rate_ok=("rate_ok", "mean"),
                glitch=("stack_glitch", lambda s: s.dropna().astype(bool).mean())).reset_index()
            for c in ("found", "rate_ok", "glitch"):
                t[c] *= 100
            H("session stacks: slow movers (≤ 2″ per exposure) by band and spectrum (S/N set in the SWIR reference "
              "photo: a 5800 K spectrum is far fainter in MWIR)")
            md.append(md_table(t.to_dict("records"), ["band", "spectrum", "sb", "n", "found", "rate_ok", "glitch"],
                               ["band", "spectrum", "S/N (ref)", "stacks", "% found", "% rate ok", "% glitch"]))
            res["stack_movers_band"] = t.to_dict("records")
        # which pass found the fake's dot, and its dot_flag bits
        Fd = F[F["found_dot"]].copy()
        if len(Fd):
            sf = np.nan_to_num(Fd["dot_sep_flag"].to_numpy(np.float64), nan=0).astype(np.int64)
            dfl = np.nan_to_num(Fd["dot_dot_flag"].to_numpy(np.float64), nan=0).astype(np.int64)
            Fd["pass"] = np.where(sf & 0x400, "mf", np.where(sf & 0x200, "psf2", "sep"))
            Fd["sb"] = pd.cut(Fd["snr_opt"], [0, 4.5, 5, 6, 8, 12, 1e9],
                              labels=["< 4.5", "4.5–5", "5–6", "6–8", "8–12", "≥ 12"])
            for bit, name in ((0x1, "guard"), (0x2, "mirror"), (0x4, "glow_piece"), (0x8, "low_snr"),
                              (0x10, "sat_core"), (0x40, "sat_core_dup")):
                Fd[name] = (dfl & bit) > 0
            t = Fd.groupby(["kind", "sb"], observed=True).agg(
                n=("pass", "size"), sep=("pass", lambda x: 100 * (x == "sep").mean()),
                psf2=("pass", lambda x: 100 * (x == "psf2").mean()), mf=("pass", lambda x: 100 * (x == "mf").mean()),
                low=("low_snr", "mean"), guard=("guard", "mean"), mirror=("mirror", "mean"),
                glow=("glow_piece", "mean")).reset_index()
            for c in ("low", "guard", "mirror", "glow"):
                t[c] *= 100
            H("Fakes found as a dot: which pass made the dot (sep first pass, ePSF second pass, matched filter), and its "
              "dot_flag bits (%)")
            md.append(md_table(t.to_dict("records"), ["kind", "sb", "n", "sep", "psf2", "mf", "low", "guard", "mirror",
                                                       "glow"],
                               ["kind", "S/N", "found as a dot", "% sep", "% ePSF 2nd", "% matched filter",
                                "% LOW_SNR 0x8", "% MF_GUARD 0x1", "% MIRROR 0x2", "% GLOW_PIECE 0x4"]))
            res["dot_pass"] = t.to_dict("records")
        H("Why fakes were missed (all S/N)")
        md.append(cause_md(F, 0.0))
        H("Why fakes were missed at S/N ≥ 7 (where they should be found)")
        md.append(cause_md(F, 7.0))
        hi = F[(~F["found"]) & (F["snr_opt"] >= 7)]
        res["causes_all"] = F.loc[~F["found"], "cause"].value_counts().to_dict()
        res["causes_ge7"] = hi["cause"].value_counts().to_dict()
        # photometry: forced flux of point fakes
        P = F[(F["kind"] == "point") & (F["blend_d"] > 5)]
        if len(P):
            pull = (P["forced_flux"] - P["flux_ujy"]) / P["forced_err"]
            res["forced"] = {"ratio_median": float(np.nanmedian(P["forced_flux"] / P["flux_ujy"])),
                             "pull_nmad": float(1.4826 * np.nanmedian(np.abs(pull - np.nanmedian(pull)))),
                             "dot_flux_ratio": float(np.nanmedian((P["dot_flux_ujy"] / P["flux_ujy"])[P["found_dot"]])),
                             "snr_mf_over_opt": float(np.nanmedian((P["dot_snr_mf"] / P["snr_opt"])[
                                 P["found_dot"] & (P["snr_opt"] > 10)]))}
            H("Photometry of isolated point fakes (no other dot within 5 px): forced ePSF flux / truth, its pull's NMAD, "
              "dot flux / truth, snr_mf / optimal S/N (S/N > 10)")
            md.append(md_table([{k: f"{v:.3f}" for k, v in res["forced"].items()}], list(res["forced"])))

    # --- 2. density -------------------------------------------------------------------------------------------------
    if spec.get("density"):
        H("Fake density: same photos, grid 256 / 128 / 90 px (fakes per photo ~56 / ~225 / ~450)")
        rows = []
        for grid, name in spec["density"].items():
            D_ = load_runs(out, [name], floor)
            if not len(D_):
                continue
            for kind in ("point", "fuzzy", "trail"):
                g = D_[D_["kind"] == kind]
                r = curve_rows(g, ["kind"])[0] if len(g) else {}
                r.update({"grid": grid, "per_photo": len(D_) / max(1, D_[["obs_id", "detector"]].drop_duplicates().shape[0])})
                rows.append(r)
        md.append(curve_md(rows, ["grid", "kind"], extra=["per_photo"], extra_heads=["fakes per photo"]))
        res["density"] = rows

    # --- 3. settings --------------------------------------------------------------------------------------------------
    if spec.get("variants"):
        H("Settings on the variant photos (first exposure of each bench session): recovery, false alarms, cost")
        rows = []
        base_cost = None
        Vs: dict = {}
        Ns: dict = {}
        for v, (pos, neg) in spec["variants"].items():
            V = load_runs(out, [pos], floor, guard_of(v))
            N, A = neg_with_photo(out, neg)
            c = cost_of(out, [pos])
            r = {"variant": v}
            for kind in ("point", "fuzzy", "trail"):
                g = V[V["kind"] == kind] if len(V) else V
                if len(g):
                    q = curve_rows(g, ["kind"])[0]
                    r[f"{kind}_s50"], r[f"{kind}_s90"], r[f"{kind}_f5"] = q["s50"], q["s90"], q["f5"]
                    r[f"{kind}_f4"] = q["f4"]
            if len(N) and len(A):
                area = float(A["deg2"].sum())     # cells < half masked (area_table)
                nph = A["photo"].nunique()
                d = N[(N["src"] == "dot") & (N["c_mask"] < 0.5)]
                r["fa_dots_photo"] = len(d) / nph
                r["fa_dots_deg2"] = len(d) / area
                r["fa_dots5_deg2"] = int((d["s"] >= 5).sum()) / area
                r["fa_ext_photo"] = int(((N["src"] == "ext") & (N["c_mask"] < 0.5)).sum()) / nph
                r["fa_streak_photo"] = int(((N["src"] == "streak") & (N["c_mask"] < 0.5)).sum()) / nph
                pk = N[(N["src"] == "peak") & (N["c_mask"] < 0.5)]
                r["fa_peaks4_photo"] = int((pk["s"] >= 4).sum()) / nph
            if len(c):
                r["detect_s"] = float(c["detect_s"].mean())
                r["mb_photo"] = float((c["bytes"] + c["forced_bytes"].fillna(0) + c["peaks_bytes"] + c["ext_bytes"]
                                       + c["streak_bytes"]).mean() / 1e6)
                if base_cost is None:
                    base_cost = (r["detect_s"], r["mb_photo"])
                r["d_cpu"] = r["detect_s"] - base_cost[0]
                r["d_mb"] = r["mb_photo"] - base_cost[1]
            rows.append(r)
            if len(V):
                Vs[v] = V
            if len(N):
                Ns[v] = (N, A)
        md.append(md_table(rows, ["variant", "point_s50", "point_s90", "point_f4", "point_f5", "fuzzy_s50", "fuzzy_f5",
                                  "trail_s50", "trail_f5", "fa_dots_photo", "fa_dots5_deg2", "fa_ext_photo",
                                  "fa_streak_photo", "fa_peaks4_photo", "detect_s", "d_cpu", "mb_photo", "d_mb"],
                           ["variant", "point S/N50", "point S/N90", "point % 4–5", "point % 5–6", "fuzzy S/N50",
                            "fuzzy % 5–6", "trail S/N50", "trail % 5–6", "neg dots /photo", "neg dots ≥5 /deg²",
                            "neg ext /photo", "neg streaks /photo", "neg peaks ≥4 /photo", "detect s", "Δ s", "MB/photo",
                            "Δ MB"]))
        res["variants"] = rows
        # paired: the same fakes (same seed, photos, positions) in every variant, against the first (default)
        base = next(iter(Vs), None)
        if base is not None:
            B = Vs[base].set_index(["fake_id", "obs_id", "detector"])
            groups = {
                "point S/N 4.5–7": lambda X: (X["kind"] == "point") & X["snr_opt"].between(4.5, 7),
                "point S/N ≥ 7": lambda X: (X["kind"] == "point") & (X["snr_opt"] >= 7),
                "point crowded (≥ 3k/deg²) S/N ≥ 5": lambda X: (X["kind"] == "point") & (X["dens_bin"] >= 2) & (
                    X["snr_opt"] >= 5),
                "point < 20 px from saturation S/N ≥ 5": lambda X: (X["kind"] == "point") & (X["sat_bin"] == 2) & (
                    X["snr_opt"] >= 5),
                "point noise ≥ 1.2× S/N ≥ 5": lambda X: (X["kind"] == "point") & (X["noise_bin"] >= 1) & (
                    X["snr_opt"] >= 5),
                "fuzzy ≤ 3× S/N ≥ 5": lambda X: (X["kind"] == "fuzzy") & (X["size"] <= 3) & (X["snr_opt"] >= 5),
                "fuzzy ≥ 6× S/N ≥ 5": lambda X: (X["kind"] == "fuzzy") & (X["size"] >= 6) & (X["snr_opt"] >= 5),
                "trail S/N ≥ 6": lambda X: (X["kind"] == "trail") & (X["snr_opt"] >= 6),
            }
            prow = []
            for v, V in Vs.items():
                X = V.set_index(["fake_id", "obs_id", "detector"])
                j = X.join(B[["found"]].rename(columns={"found": "found_base"}), how="inner")
                j = j.reset_index()
                r = {"variant": v, "paired": len(j)}
                for name, sel in groups.items():
                    g = j[sel(j)]
                    if not len(g):
                        continue
                    r[f"{name}|n"] = len(g)
                    r[f"{name}|found"] = 100 * g["found"].mean()
                    r[f"{name}|gain"] = int((g["found"] & ~g["found_base"]).sum())
                    r[f"{name}|lost"] = int((~g["found"] & g["found_base"]).sum())
                prow.append(r)
            H(f"Settings, paired on the same fakes: % found, and fakes gained / lost against `{base}`")
            cols, heads = ["variant"], ["variant"]
            for name in groups:
                cols += [f"{name}|found", f"{name}|gl"]
                heads += [f"{name} %", "+/−"]
            for r in prow:
                for name in groups:
                    if f"{name}|gain" in r:
                        r[f"{name}|gl"] = f"+{r[f'{name}|gain']}/−{r[f'{name}|lost']}"
            md.append(md_table(prow, cols, heads))
            md.append("fakes per group (first variant): " + ", ".join(
                f"{name} {prow[0].get(f'{name}|n', 0)}" for name in groups))
            res["variants_paired"] = prow
        # false alarms per variant without the edge strip, by condition
        if Ns:
            frows = []
            for v, (N, A) in Ns.items():
                Ni, Ai = N[(N["edge_bin"] == 0) & (N["c_mask"] < 0.5)], A[A["edge_bin"] == 0]
                r = {"variant": v}
                for lab, selN, selA in (("all", np.ones(len(Ni), bool), np.ones(len(Ai), bool)),
                                        ("crowded", (Ni["dens_bin"] >= 2).to_numpy(), (Ai["dens_bin"] >= 2).to_numpy()),
                                        ("near sat", (Ni["sat_bin"] == 2).to_numpy(), (Ai["sat_bin"] == 2).to_numpy()),
                                        ("noisy", (Ni["noise_bin"] >= 1).to_numpy(), (Ai["noise_bin"] >= 1).to_numpy())):
                    area = float(Ai.loc[selA, "deg2"].sum())
                    d = Ni[selN]
                    if area > 0:
                        r[f"{lab}|dots5"] = int(((d["src"] == "dot") & (d["s"] >= 5)).sum()) / area
                        r[f"{lab}|dots"] = int((d["src"] == "dot").sum()) / area
                        r[f"{lab}|pk4"] = int((d["src"].isin(["dot", "peak"]) & (d["s"] >= 4)).sum()) / area
                frows.append(r)
            H("Settings: negative-image false alarms per deg² per photo, edge strip out (dots S/N ≥ 5; all dots; dots + "
              "peaks ≥ 4)")
            cols, heads = ["variant"], ["variant"]
            for lab in ("all", "crowded", "near sat", "noisy"):
                cols += [f"{lab}|dots5", f"{lab}|dots", f"{lab}|pk4"]
                heads += [f"{lab} dots ≥ 5", f"{lab} dots", f"{lab} ≥ 4"]
            md.append(md_table(frows, cols, heads))
            res["variants_fa"] = frows

    # --- 4. false-alarm thresholds per condition ------------------------------------------------------------------
    if spec.get("neg"):
        N, A = neg_with_photo(out, spec["neg"])
        if len(N):
            N["condition"] = N["condition"].astype(str)
            res["fa"] = {}
            H(f"Negative images: the {EDGE_STRIP_PX}-px edge strip (D2's top-row ramp, D5's bottom rows) vs the rest, "
              "dots + peaks per deg² per photo")
            rows = fa_rates(N, A, ["detector", "edge_bin"], ("dot", "peak"))
            md.append(fa_md(rows, ["detector", "edge_bin"]))
            res["fa"]["edge"] = rows
            Ni, Ai = N[N["edge_bin"] == 0], A[A["edge_bin"] == 0]
            for srcs, label in ((("dot",), "dots (snr_mf)"), (("dot", "peak"), "dots + peaks (matched-filter S/N)")):
                H(f"False alarms on the negative bench images, without the edge strip: {label}, per deg² per photo")
                for by in (["condition", "detector"], ["dens_bin"], ["sat_bin"], ["noise_bin"], ["in_glow_c"],
                           ["mf_clean"]):
                    rows = fa_rates(Ni, Ai, by, srcs)
                    md.append(fa_md(rows, by))
                    md.append("")
                    res["fa"][f"{'+'.join(srcs)}|{'+'.join(by)}"] = rows
            H("Area shares of the bench photos (cells < half masked): clean matched-filter blocks, edge strip")
            tot = float(A["deg2"].sum())
            res["area_share"] = {"mf_clean": float(A.loc[A["mf_clean"], "deg2"].sum() / tot),
                                 "edge_strip": float(A.loc[A["edge_bin"] == 1, "deg2"].sum() / tot),
                                 "noise_bin2": float(A.loc[A["noise_bin"] == 2, "deg2"].sum() / tot),
                                 "dens_bin3": float(A.loc[A["dens_bin"] == 3, "deg2"].sum() / tot),
                                 "in_glow": float(A.loc[A["in_glow_c"], "deg2"].sum() / tot),
                                 "deg2_per_photo": tot / max(1, A["photo"].nunique())}
            md.append(json.dumps({k: round(v, 3) for k, v in res["area_share"].items()}))
            nph = A.groupby("detector")["photo"].nunique()
            for src in ("ext", "streak"):
                d = N[(N["src"] == src) & (N["c_mask"] < 0.5)]
                res[f"fa_{src}_per_photo"] = d.groupby("detector").size().reindex(nph.index, fill_value=0).div(
                    nph).round(1).to_dict()
            H("Negative-image ext and streak rows per photo, by detector")
            md.append(json.dumps({k: res[k] for k in ("fa_ext_per_photo", "fa_streak_per_photo")}))
    # --- 5. cost of a lower peaks floor -------------------------------------------------------------------------
    if spec.get("main"):
        pr = peak_rows_at(out, spec["main"][0])
        if pr:
            res["peaks_cost"] = pr
            rows = []
            Nn, An = neg_with_photo(out, spec["neg"]) if spec.get("neg") else (pd.DataFrame(), pd.DataFrame())
            for f, n in pr["rows_per_photo"].items():
                mb = pr["mb_per_photo"][f]
                r = {"floor": f"{f:g}", "rows": n, "mb": mb, "sky1": mb * SKY_PHOTOS[0] / 1e6,
                     "sky2": mb * SKY_PHOTOS[1] / 1e6}
                if len(Nn):
                    pk = Nn[Nn["src"].isin(["dot", "peak"]) & (Nn["s"] >= f)]
                    nph = max(1, An["photo"].nunique())
                    r["noise"] = len(pk) / nph
                    r["noise_inner"] = int((pk["edge_bin"] == 0).sum()) / nph
                rows.append(r)
            H("Peaks file per photo at each floor (bench, with fakes; each floor's file encoded as the worker writes "
              "it) and the whole sky; noise = −IMAGE dots + peaks at or above the floor per photo")
            show = [dict(r, mb=f"{r['mb']:.3f}", sky1=f"{r['sky1']:.2f}", sky2=f"{r['sky2']:.2f}") for r in rows]
            md.append(md_table(show, ["floor", "rows", "mb", "sky1", "sky2", "noise", "noise_inner"],
                               ["floor", "rows/photo", "MB/photo", f"TB, {SKY_PHOTOS[0]:,} photos",
                                f"TB, {SKY_PHOTOS[1]:,} photos", "noise rows/photo", "… without the edge strip"]))
            res["peaks_cost_rows"] = rows
        if len(F) and "c_peak_snr" in F:
            H("Found anywhere at a lower peaks floor (dots, peaks ≥ floor, ext, streaks; chance-corrected with the "
              "controls at the same floor), % per S/N bin")
            rows = []
            for fl in (3.0, 3.5, 4.0):
                G = refloor(F, fl)
                for kind in ("point", "mover", "fuzzy", "trail"):
                    g = G[G["kind"] == kind]
                    if not len(g):
                        continue
                    c0 = chance(g, "c_found_any")
                    r = {"floor": f"{fl:g}", "kind": kind, "chance": 100 * c0}
                    for lo, hi in ((3.0, 3.5), (3.5, 4.0), (4.0, 4.5), (4.5, 5.0), (5.0, 6.0), (6.0, 8.0)):
                        m = (g["snr_opt"] >= lo) & (g["snr_opt"] < hi)
                        p = g.loc[m, "found_any"].mean() if m.any() else np.nan
                        r[f"b{lo:g}"] = 100 * (p - c0) / (1 - c0) if np.isfinite(p) else np.nan
                    q = logistic_fit(g["snr_opt"].to_numpy(), g["found_any"].to_numpy(bool), c0)
                    r["s50"], r["s90"] = q["s50"], q["s90"]
                    rows.append(r)
            md.append(md_table(rows, ["floor", "kind", "chance", "b3", "b3.5", "b4", "b4.5", "b5", "b6", "s50", "s90"],
                               ["peaks floor", "kind", "chance %", "3–3.5", "3.5–4", "4–4.5", "4.5–5", "5–6", "6–8",
                                "S/N 50%", "S/N 90%"]))
            res["floor_gain"] = rows
        c = cost_of(out, spec["main"])
        if len(c):
            res["cost_main"] = {"detect_s": float(c["detect_s"].mean()), "inject_cpu_s": float(c["inject_cpu_s"].mean()),
                                "dots_mb": float(c["bytes"].mean() / 1e6)}
    # --- 6. hold-out ----------------------------------------------------------------------------------------------
    if spec.get("holdout"):
        HO = load_runs(out, spec["holdout"], floor)
        if len(HO):
            H("Hold-out (no tuning) next to the bench")
            rows = []
            for lab, X in (("bench", F), ("hold-out", HO)):
                for r in curve_rows(X, ["kind"]):
                    r["set"] = lab
                    rows.append(r)
            md.append(curve_md(rows, ["set", "kind"]))
            res["holdout"] = rows
            H("Hold-out: point by field and detector")
            md.append(curve_md(curve_rows(HO[HO["kind"] == "point"], ["condition", "detector"]), ["condition", "detector"]))
            H("Hold-out: why missed at S/N ≥ 7")
            md.append(cause_md(HO, 7.0))
        if spec.get("holdout_neg"):
            N, A = neg_with_photo(out, spec["holdout_neg"])
            if len(N):
                Ni, Ai = N[N["edge_bin"] == 0], A[A["edge_bin"] == 0]
                H("Hold-out negative images, without the edge strip: dots + peaks per deg² per photo; then dots alone")
                ho_fa = fa_rates(Ni, Ai, ["condition", "detector"], ("dot", "peak"))
                md.append(fa_md(ho_fa, ["condition", "detector"]))
                md.append(fa_md(fa_rates(Ni, Ai, ["condition", "detector"], ("dot",)), ["condition", "detector"]))
                md.append(fa_md(fa_rates(Ni, Ai, ["noise_bin"], ("dot", "peak")), ["noise_bin"]))
                res["holdout_fa"] = ho_fa
    text = "\n".join(md)
    dest = Path(a.md) if a.md else out / "report_tables.md"
    dest.write_text(text)
    (dest.with_suffix(".json")).write_text(json.dumps(res, indent=1, default=lambda o: o.item() if hasattr(o, "item")
                                                      else str(o)))
    log(f"report -> {dest}")


# =====================================================================================================================
# 9. command line
# =====================================================================================================================


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fakes into the photos of a subset, through the dots worker")
    r.add_argument("--subset", required=True)
    r.add_argument("--out", default=str(OUT))
    r.add_argument("--name", required=True, help="run name: <out>/runs/<name>/")
    r.add_argument("--variant", default="default", help="+-joined names of VARIANTS")
    r.add_argument("--round", type=int, default=1, help="a new round: new positions, fluxes, noise")
    r.add_argument("--grid", type=int, default=GRID_PX)
    r.add_argument("--seed", type=int, default=20261127)
    r.add_argument("--neg", action="store_true", help="the negative image (-IMAGE), no fakes: false alarms")
    r.add_argument("--no-stacks", action="store_true")
    r.add_argument("--no-movers", action="store_true")
    r.add_argument("--workers", type=int, default=5)
    r.add_argument("--photo-cache", default=str(DATA / "photo-cache"))
    m = sub.add_parser("match", help="match a run's outputs to its truth (or list a negative run's false alarms)")
    m.add_argument("--out", default=str(OUT))
    m.add_argument("--name", required=True)
    m.add_argument("--cond-run", default="", help="negative runs: the positive run whose cond/ maps to use")
    m.add_argument("--workers", type=int, default=5)
    m.add_argument("--stacks-only", action="store_true", help="redo only the session-stack tables")
    q = sub.add_parser("report", help="tables from matched runs (a spec file names each run's role)")
    q.add_argument("--out", default=str(OUT))
    q.add_argument("--spec", required=True)
    q.add_argument("--md", default="")
    a = p.parse_args(argv)
    if a.cmd == "run":
        if a.workers > 5:
            raise SystemExit("at most 5 worker processes")
        cmd_run(a)
    elif a.cmd == "match":
        cmd_match(a)
    else:
        cmd_report(a)


if __name__ == "__main__":
    main()
