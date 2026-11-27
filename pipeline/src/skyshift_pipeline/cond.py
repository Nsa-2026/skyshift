"""The conditions table of extraction v2 (27 Nov, agent "conditions"; config COND*).

Per photo, one row per COND_TILE_PX × COND_TILE_PX tile: what the search met there and what it found, written by the
dots worker next to the dots as <folder>_cond/<obs_id>D<det>.parquet. It reuses the arrays the worker already has (the
detection background and noise maps, VARIANCE, ZODI, FLAGS, the matched filter's Σ w·K² map and block sigma grid, the
glow model's footprint, the saturation guard's distance map, the forced targets, the dots, peaks, ext and streak
rows); the only new measurement is the negative-residual peak count (psf.mf_pass `neg_snr`), which reads the same
filter's S/N on −residual. Downstream it gives thresholds per condition group at a fixed false-alarm rate and how deep
each piece of sky was searched (`thresh_ujy`: the matched filter's point-source threshold in µJy as applied there).

Never changes a dot: dots.py calls `attach` after the photo's tables exist and `write` before the dot file; a failure
in either is caught, recorded in the dot file's footer `cond.error`, and the photo keeps its dots.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from skyshift_core import flags as F
from skyshift_core import pixel_to_sky

from . import config as C

VERSION = 1
META_KEY = "skyshift_cond"  # Parquet key-value metadata: the photo-level summary (JSON)

# FLAGS bits counted per tile: the mask list of contracts/flags.md, then two recorded bits tied to bright stars
MASK_COUNT_BITS = {"transient": F.TRANSIENT, "overflow": F.OVERFLOW, "sur_error": F.SUR_ERROR, "nonfunc": F.NONFUNC,
                   "dichroic": F.DICHROIC, "missing_data": F.MISSING_DATA, "hot": F.HOT, "cold": F.COLD,
                   "phanmiss": F.PHANMISS, "nonlinear": F.NONLINEAR, "persist": F.PERSIST, "streak": F.STREAK,
                   "snowball": F.SNOWBALL}
RECORD_COUNT_BITS = {"bloom": F.BLOOM, "halo": F.HALO}

# column -> dtype, in file order. Units: pixels 0-based, degrees, µm, MJy/sr (as IMAGE), µJy (point source, the dots'
# flux scale: µJy per MJy/sr in one pixel, no aperture correction). Counts: pixels or rows whose position is in the tile.
COLUMNS: dict[str, str] = {
    "tile": "int16",                             # ty × ntx + tx (ntx, nty, tile_px in the summary)
    "x": "float32", "y": "float32",              # tile centre (px)
    "ra": "float32", "dec": "float32",           # tile centre (deg)
    "wl_um": "float16", "bw_um": "float16",      # WCS-WAVE central wavelength and bandwidth at the centre
    "n_px": "uint16", "n_good": "uint16",        # pixels in the tile; not masked (detection's bad-pixel mask)
    "bkg_mjysr": "float16",                      # median of the detection background map (sep, box 32)
    "bkg_rms_mjysr": "float16",                  # median of that background's rms map
    "sky_sigma_mjysr": "float16",                # robust scatter of IMAGE − background on good pixels (median −
                                                 # 15.87th percentile: sources are positive)
    "zodi_mjysr": "float16",                     # median of NASA's ZODI model (not subtracted from IMAGE)
    "var_sigma_mjysr": "float16",                # median √VARIANCE
    "noise_mjysr": "float16",                    # median per-pixel 1σ detection uses: max(√VARIANCE, background rms)
    "psf_sigma_ujy": "float32",                  # median matched-filter 1σ of a point source: conv / √(Σ w·K²)
    "mf_sigma": "float16",                       # the S/N map's own sigma (footer mf.sigma_grid) over the tile, median
    "thresh_ujy": "float32",                     # median threshold applied: min_snr × block scale × psf_sigma_ujy
    **{f"n_{k}": "uint16" for k in MASK_COUNT_BITS},
    "n_sat": "uint16",                           # OVERFLOW or NONLINEAR
    "n_hole": "uint16",                          # bad without a mask bit: v2's dark holes (and non-finite IMAGE)
    **{f"n_{k}": "uint16" for k in RECORD_COUNT_BITS},
    "n_glow": "uint8",                           # glow-model stars whose reach touches the tile
    "n_glow_px": "uint16",                       # pixels the glow model changes (model ≠ 0 or noise factor > 1)
    "n_guard_px": "uint16",                      # pixels within the saturation guard (DETECT_SAT_GUARD_PX)
    "n_gaia": "uint16", "n_red": "uint16",       # forced targets: Gaia (G < 17.5), red AllWISE layer
    "gaia_ab_min": "float16",                    # brightest Gaia forced target, AB mag from its ePSF flux in this
                                                 # detector (G is not in the forced list; saturated stars read faint)
    "n_dots": "uint16", "n_dots_sep": "uint16", "n_dots_psf": "uint16", "n_dots_mf": "uint16",
    "n_dots_flagged": "uint16",                  # dot_flag != 0
    **{f"n_df_{k}": "uint16" for k in C.DOT_FLAGS},  # n_df_sat_core: the tile's saturated cores (one dot each)
    "n_peaks": "uint16", "n_ext": "uint16", "n_streaks": "uint16",
    "n_neg_4": "uint16", "n_neg_45": "uint16", "n_neg_5": "uint16",  # negative-residual peaks >= 4 / 4.5 / 5 × scale
}
NEG_COLUMNS = ("n_neg_4", "n_neg_45", "n_neg_5")
TOTALS = ("n_sat", "n_hole", "n_glow_px", "n_guard_px", "n_gaia", "n_red", "n_dots", "n_dots_sep", "n_dots_psf",
          "n_dots_mf", "n_dots_flagged", "n_df_sat_core", "n_peaks", "n_ext", "n_streaks") + NEG_COLUMNS


def cond_rel(obs_id: str, det: int, folder: str) -> str:
    """The conditions file of a v2 dot file: <folder>_cond/<obs_id>D<det>.parquet."""
    return f"{folder}_cond/{obs_id}D{int(det)}.parquet"


# --- tiles -----------------------------------------------------------------------------------------------------------

class Tiles:
    """The tile grid of an (ny, nx) photo: tiles of t px from (0, 0); the last row and column may be smaller."""

    def __init__(self, shape: tuple[int, int], t: int, step: int = 1):
        self.ny, self.nx = int(shape[0]), int(shape[1])
        self.t = int(t)
        self.step = int(step) if t % max(1, int(step)) == 0 else 1
        self.nty, self.ntx = -(-self.ny // self.t), -(-self.nx // self.t)
        self.n = self.nty * self.ntx

    def of(self, x, y) -> np.ndarray:
        """Tile index of each position (pixel centres at integers; clipped to the photo)."""
        ix = np.clip(np.floor(np.asarray(x, np.float64) + 0.5), 0, self.nx - 1).astype(np.int64) // self.t
        iy = np.clip(np.floor(np.asarray(y, np.float64) + 0.5), 0, self.ny - 1).astype(np.int64) // self.t
        return iy * self.ntx + ix

    def count(self, x, y, keep=None) -> np.ndarray:
        x, y = np.asarray(x), np.asarray(y)
        ok = np.isfinite(x) & np.isfinite(y)
        if keep is not None:
            ok &= np.asarray(keep, bool)
        return np.bincount(self.of(x[ok], y[ok]), minlength=self.n)[:self.n]

    def pixel_count(self, m: np.ndarray) -> np.ndarray:
        """Set pixels of a full-size boolean map per tile."""
        iy, ix = np.nonzero(m)
        return np.bincount((iy // self.t) * self.ntx + ix // self.t, minlength=self.n)[:self.n]

    def blocks(self, a: np.ndarray) -> np.ndarray:
        """(n, k) float32: each tile's sampled pixels (every step-th in x and y), NaN padding."""
        s = self.step
        tt = self.t // s
        v = np.asarray(a)[::s, ::s]
        P = np.full((self.nty * tt, self.ntx * tt), np.nan, np.float32)
        P[:v.shape[0], :v.shape[1]] = v
        return P.reshape(self.nty, tt, self.ntx, tt).swapaxes(1, 2).reshape(self.n, tt * tt)

    def centres(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(tx, ty, x centre, y centre, pixels) per tile."""
        k = np.arange(self.n)
        ty, tx = k // self.ntx, k % self.ntx
        x0, y0 = tx * self.t, ty * self.t
        x1, y1 = np.minimum(x0 + self.t, self.nx), np.minimum(y0 + self.t, self.ny)
        return tx, ty, (x0 + x1 - 1) / 2.0, (y0 + y1 - 1) / 2.0, (x1 - x0) * (y1 - y0)


def quantiles(B: np.ndarray, qs) -> list[np.ndarray]:
    """Per row of B (NaN = no value) the quantiles qs (linear interpolation), NaN for an empty row."""
    S = np.sort(B, axis=1)  # NaN last
    n = np.isfinite(S).sum(axis=1)
    rows = np.arange(S.shape[0])
    out = []
    for q in qs:
        pos = q * np.maximum(n - 1, 0)
        lo = np.floor(pos).astype(np.int64)
        hi = np.minimum(lo + 1, np.maximum(n - 1, 0))
        f = pos - lo
        v = S[rows, lo] * (1 - f) + S[rows, hi] * f
        out.append(np.where(n > 0, v, np.nan))
    return out


def tile_median(T: Tiles, a: np.ndarray | None, good: np.ndarray | None = None) -> np.ndarray:
    if a is None:
        return np.full(T.n, np.nan)
    a = np.asarray(a, np.float32)
    if good is not None:
        a = np.where(good, a, np.nan)
    return quantiles(T.blocks(a), [0.5])[0]


def block_scale_map(T: Tiles, shape: tuple[int, int], stats: dict | None) -> tuple[np.ndarray, np.ndarray]:
    """(sampled S/N-map sigma, sampled threshold scale) maps on the tile sampling: the footer's mf.sigma_grid expanded to
    pixels (psf.mf_sigma_map's edges) and the factor mf_pass puts on min_snr there (block mode: sigma where it exceeds
    MF_SIGMA_MIN_SCALE, else 1; photo mode is already in min_snr_used)."""
    ny, nx = shape
    ys, xs = np.arange(0, ny, T.step), np.arange(0, nx, T.step)
    sig, sc = block_values(shape, stats, ys[:, None], xs[None, :])
    return sig.astype(np.float32), sc.astype(np.float32)


def block_values(shape: tuple[int, int], stats: dict | None, iy, ix) -> tuple[np.ndarray, np.ndarray]:
    """(S/N-map sigma, threshold scale) at pixels (iy, ix) (broadcast): mf_pass's block lookup."""
    ny, nx = shape
    iy, ix = np.asarray(iy), np.asarray(ix)
    g = None if not stats else stats.get("sigma_grid")
    if g is None:
        one = np.ones(np.broadcast_shapes(iy.shape, ix.shape))
        return np.full_like(one, np.nan), one
    g = np.asarray(g, np.float64)
    n = g.shape[0]
    by = np.clip(np.searchsorted(np.round(np.linspace(0, ny, n + 1)).astype(int), iy, side="right") - 1, 0, n - 1)
    bx = np.clip(np.searchsorted(np.round(np.linspace(0, nx, n + 1)).astype(int), ix, side="right") - 1, 0, n - 1)
    sig = g[by, bx]
    if str(C.MF_PASS_NORMALISE) == "block":
        g1 = np.nan_to_num(g, nan=1.0)
        sc = np.where(g1 > C.MF_SIGMA_MIN_SCALE, g1, 1.0)[by, bx]
    else:
        sc = np.ones_like(sig)
    return sig, sc


# --- the table -------------------------------------------------------------------------------------------------------

def glow_region(gl) -> tuple[np.ndarray, np.ndarray]:
    """(pixels the glow model changes, its stars as x, y, reach): kept by dots.glow_dots for the table."""
    m = (gl.model != 0) | (gl.k > 1.0)
    st = gl.stars
    reach = np.maximum.reduce([np.asarray(st["r_glow"], np.float64), np.asarray(st["r_core"], np.float64),
                               np.full(len(st), C.GLOW_R_KEEP_PX)]) if len(st) else np.zeros(0)
    return m, np.c_[np.asarray(st["x"], np.float64), np.asarray(st["y"], np.float64), reach] if len(st) \
        else np.zeros((0, 3))


def circles_touching(T: Tiles, stars: np.ndarray) -> np.ndarray:
    """Per tile, the circles (x, y, r) that reach into it."""
    out = np.zeros(T.n, np.int64)
    if stars is None or not len(stars):
        return out
    k = np.arange(T.n)
    ty, tx = k // T.ntx, k % T.ntx
    x0, y0 = tx * T.t - 0.5, ty * T.t - 0.5
    x1, y1 = np.minimum(x0 + T.t, T.nx - 0.5), np.minimum(y0 + T.t, T.ny - 0.5)
    for i in range(0, len(stars), 256):
        s = stars[i:i + 256]
        dx = np.maximum(0, np.maximum(x0[None, :] - s[:, :1], s[:, :1] - x1[None, :]))
        dy = np.maximum(0, np.maximum(y0[None, :] - s[:, 1:2], s[:, 1:2] - y1[None, :]))
        out += ((dx * dx + dy * dy) <= (s[:, 2:3] ** 2)).sum(axis=0)
    return out


def _xy(df) -> tuple[np.ndarray, np.ndarray]:
    if df is None or not len(df):
        return np.zeros(0), np.zeros(0)
    return df["x"].to_numpy(np.float64), df["y"].to_numpy(np.float64)


def build(l2, ctx, extras: dict, dots, forced=None, peaks=None, ext=None, streaks=None,
          tile_px: int | None = None, obs_id: str = "", detector: int | None = None) -> pa.Table:
    """The conditions table of one measured photo (COLUMNS), photo summary under META_KEY. l2: the photo as measured
    (hole-masked); ctx: dots.PhotoContext; extras: dots_table's extras (mf_den, mf_stats, mf_neg, glow_px, glow_stars,
    guard_px when the worker had them); dots/forced/peaks/ext/streaks: the photo's tables (None: not made)."""
    t0 = time.process_time()
    shape = np.shape(l2.data)
    T = Tiles(shape, int(tile_px or C.COND_TILE_PX), C.COND_STEP)
    det = int(detector if detector is not None else getattr(l2, "detector", 0) or 0)
    tx, ty, xc, yc, npx = T.centres()
    col: dict[str, np.ndarray] = {"tile": np.arange(T.n), "x": xc, "y": yc, "n_px": npx}
    wcs = getattr(l2, "wcs", None)
    if wcs is not None:
        ra, dec = pixel_to_sky(wcs, xc, yc)
        col["ra"], col["dec"] = np.asarray(ra) % 360.0, dec
    else:
        col["ra"] = col["dec"] = np.full(T.n, np.nan)
    wave = getattr(l2, "wave", None)
    if wave is not None:
        col["wl_um"], col["bw_um"] = wave.wavelength(xc, yc), wave.bandwidth(xc, yc)
    else:
        col["wl_um"] = col["bw_um"] = np.full(T.n, np.nan)
    bad = np.asarray(ctx.bad, bool)
    good = ~bad
    col["n_good"] = npx - T.pixel_count(bad)
    # background, noise (smooth maps: medians of the sampled pixels)
    col["bkg_mjysr"] = tile_median(T, ctx.bkg_level)
    col["bkg_rms_mjysr"] = tile_median(T, ctx.bkg_rms)
    lo, med = quantiles(T.blocks(np.where(good, np.asarray(ctx.sub, np.float32), np.nan)), [0.158655254, 0.5])
    col["sky_sigma_mjysr"] = med - lo
    col["zodi_mjysr"] = tile_median(T, getattr(l2, "zodi", None))
    var = getattr(l2, "variance", None)
    if var is not None:
        v = np.asarray(var, np.float32)
        with np.errstate(invalid="ignore"):
            col["var_sigma_mjysr"] = tile_median(T, np.sqrt(np.where(np.isfinite(v) & (v > 0), v, np.nan)))
    else:
        col["var_sigma_mjysr"] = np.full(T.n, np.nan)
    col["noise_mjysr"] = tile_median(T, ctx.noise, good)
    # the matched filter's depth and threshold
    stats = extras.get("mf_stats") or {}
    min_snr = float(C.mf_pass_snr(det))
    used = float(stats.get("min_snr_used", min_snr))
    sig_s, scale_s = block_scale_map(T, shape, stats)
    den = extras.get("mf_den")
    if den is not None:
        d = np.asarray(den)[::T.step, ::T.step]
        with np.errstate(divide="ignore", invalid="ignore"):
            ps = np.where(d > 0, ctx.conv / np.sqrt(d), np.nan).astype(np.float32)
        col["psf_sigma_ujy"] = quantiles(_sampled_blocks(T, ps), [0.5])[0]
        col["thresh_ujy"] = quantiles(_sampled_blocks(T, ps * scale_s * used), [0.5])[0]
    else:
        col["psf_sigma_ujy"] = col["thresh_ujy"] = np.full(T.n, np.nan)
    col["mf_sigma"] = quantiles(_sampled_blocks(T, sig_s), [0.5])[0]
    # masked pixels by cause
    f = np.asarray(l2.flags).astype(np.int64) if getattr(l2, "flags", None) is not None else np.zeros(shape, np.int64)
    fy, fx = np.nonzero(f & (F.MASK_BITS | F.BLOOM | F.HALO))
    fv = f[fy, fx]
    ftile = (fy // T.t) * T.ntx + fx // T.t
    for k, b in {**MASK_COUNT_BITS, **RECORD_COUNT_BITS}.items():
        col[f"n_{k}"] = np.bincount(ftile[(fv & b) != 0], minlength=T.n)[:T.n]
    col["n_sat"] = np.bincount(ftile[(fv & F.SAT_BITS) != 0], minlength=T.n)[:T.n]
    col["n_hole"] = T.pixel_count(bad & ((f & F.MASK_BITS) == 0))
    # saturated cores, glow, guard
    dfl = dots["dot_flag"].to_numpy(np.int64) if dots is not None and "dot_flag" in dots else np.zeros(0, np.int64)
    dx, dy = _xy(dots)
    if dfl.size != dx.size:
        dfl = np.zeros(dx.size, np.int64)
    gpx = extras.get("glow_px")
    col["n_glow_px"] = T.pixel_count(gpx) if gpx is not None else np.zeros(T.n)
    col["n_glow"] = circles_touching(T, extras.get("glow_stars"))
    gu = extras.get("guard_px")
    col["n_guard_px"] = T.pixel_count(gu) if gu is not None else np.zeros(T.n)
    # forced targets
    if forced is not None and len(forced):
        red = forced["ref_id"].astype(str).str.startswith("allwise:").to_numpy()
        fxx, fyy = _xy(forced)
        col["n_gaia"], col["n_red"] = T.count(fxx, fyy, ~red), T.count(fxx, fyy, red)
        fl = forced["flux_psf_ujy"].to_numpy(np.float64)
        ok = ~red & np.isfinite(fl) & np.isfinite(fxx) & np.isfinite(fyy)
        mx = np.full(T.n, -np.inf)
        np.maximum.at(mx, T.of(fxx[ok], fyy[ok]), fl[ok])
        col["gaia_ab_min"] = np.where(np.isfinite(mx) & (mx > 0), 23.9 - 2.5 * np.log10(np.where(mx > 0, mx, 1.0)), np.nan)
    else:
        col["n_gaia"] = col["n_red"] = np.zeros(T.n)
        col["gaia_ab_min"] = np.full(T.n, np.nan)
    # what was found
    sf = dots["sep_flag"].to_numpy(np.int64) if dots is not None and "sep_flag" in dots else np.zeros(dx.size, np.int64)
    col["n_dots"] = T.count(dx, dy)
    col["n_dots_psf"] = T.count(dx, dy, (sf & C.SEP_FLAG_PSF_PASS) != 0)
    col["n_dots_mf"] = T.count(dx, dy, (sf & C.SEP_FLAG_MF_PASS) != 0)
    col["n_dots_sep"] = T.count(dx, dy, (sf & (C.SEP_FLAG_PSF_PASS | C.SEP_FLAG_MF_PASS)) == 0)
    col["n_dots_flagged"] = T.count(dx, dy, dfl != 0)
    for k, b in C.DOT_FLAGS.items():
        col[f"n_df_{k}"] = T.count(dx, dy, (dfl & b) != 0)
    col["n_peaks"] = T.count(*_xy(peaks))
    col["n_ext"] = T.count(*_xy(ext))
    col["n_streaks"] = T.count(*_xy(streaks))
    neg = extras.get("mf_neg")
    have_neg = neg is not None
    if have_neg:
        nix, niy, ns = np.asarray(neg["ix"]), np.asarray(neg["iy"]), np.asarray(neg["snr"], np.float64)
        _, sc = block_values(shape, stats, niy, nix)
        f_used = used / min_snr if min_snr > 0 else 1.0  # photo mode: its scale; block mode 1
        for c, thr in zip(NEG_COLUMNS, C.COND_NEG_SNR):
            col[c] = T.count(nix, niy, ns >= thr * sc * f_used)
    else:
        for c in NEG_COLUMNS:
            col[c] = np.zeros(T.n)
    # the table
    arrays, fields = [], []
    for c, typ in COLUMNS.items():
        v = np.asarray(col[c])
        if np.dtype(typ).kind == "u":
            v = np.clip(np.nan_to_num(v, nan=0), 0, np.iinfo(typ).max)
        elif typ == "float16":
            v = np.clip(v.astype(np.float64), -65504.0, 65504.0)
        arrays.append(pa.array(v.astype(typ)))
        fields.append(pa.field(c, pa.from_numpy_dtype(np.dtype(typ))))
    summary = photo_summary(col, T, obs_id, det, min_snr, used, ctx.conv, have_neg, extras)
    summary["cpu_s"] = round(time.process_time() - t0, 3)
    table = pa.Table.from_arrays(arrays, schema=pa.schema(fields))
    meta = json.dumps(summary, allow_nan=False, separators=(",", ":")).encode()
    return table.replace_schema_metadata({META_KEY.encode(): meta})


def _sampled_blocks(T: Tiles, a: np.ndarray) -> np.ndarray:
    """blocks() of a map already on the tile sampling (every step-th pixel)."""
    tt = T.t // T.step
    P = np.full((T.nty * tt, T.ntx * tt), np.nan, np.float32)
    P[:a.shape[0], :a.shape[1]] = a
    return P.reshape(T.nty, tt, T.ntx, tt).swapaxes(1, 2).reshape(T.n, tt * tt)


def _num(v) -> float | None:
    v = float(v)
    return round(v, 4) if np.isfinite(v) else None


def photo_summary(col: dict, T: Tiles, obs_id: str, det: int, min_snr: float, used: float, conv: float,
                  have_neg: bool, extras: dict) -> dict:
    """The photo-level summary (parquet metadata): grid, settings, medians over tiles, totals."""
    def med(c):
        v = np.asarray(col[c], np.float64)
        return _num(np.nanmedian(v)) if np.isfinite(v).any() else None
    tot = {c: int(np.nansum(np.asarray(col[c], np.float64))) for c in TOTALS}
    tot["n_bad"] = int(np.sum(col["n_px"]) - np.sum(col["n_good"]))
    return {
        "version": VERSION, "obs_id": obs_id, "detector": det, "tile_px": T.t, "step": T.step,
        "n_tiles": T.n, "ntx": T.ntx, "nty": T.nty, "shape": [T.ny, T.nx],
        "mf_min_snr": min_snr, "mf_min_snr_used": used, "mf_normalise": str(C.MF_PASS_NORMALISE),
        "mf_sigma_min_scale": C.MF_SIGMA_MIN_SCALE, "mf_low_snr": C.MF_DOT_LOW_SNR, "ujy_per_mjysr": _num(conv),
        "neg": ({"snr": list(C.COND_NEG_SNR), "columns": list(NEG_COLUMNS), "scaled": True} if have_neg else None),
        "median": {c: med(c) for c in ("bkg_mjysr", "bkg_rms_mjysr", "sky_sigma_mjysr", "zodi_mjysr",
                                       "var_sigma_mjysr", "noise_mjysr", "psf_sigma_ujy", "mf_sigma", "thresh_ujy",
                                       "wl_um")},
        "thresh_ujy_p10_p90": [_num(np.nanpercentile(col["thresh_ujy"], 10)) if np.isfinite(col["thresh_ujy"]).any()
                               else None,
                               _num(np.nanpercentile(col["thresh_ujy"], 90)) if np.isfinite(col["thresh_ujy"]).any()
                               else None],
        "total": tot,
        "inputs": {k: extras.get(k) is not None for k in ("mf_den", "mf_stats", "mf_neg", "glow_px", "guard_px")},
    }


def table_bytes(table: pa.Table) -> bytes:
    """Parquet bytes of a conditions table: zstd, byte-stream-split floats, delta-packed integers, no column
    statistics and no stored Arrow schema (the Parquet types say it all; on 256 rows the footer was a third of the
    file)."""
    enc = {f.name: ("BYTE_STREAM_SPLIT" if pa.types.is_floating(f.type) else "DELTA_BINARY_PACKED")
           for f in table.schema}
    md = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    table = table.replace_schema_metadata(None)
    out = pa.BufferOutputStream()
    # store_schema=False drops the table's key-value metadata, so the summary is added to the footer after the rows
    with pq.ParquetWriter(out, table.schema, compression="zstd", compression_level=9, use_dictionary=False,
                          column_encoding=enc, write_statistics=False, store_schema=False) as w:
        w.write_table(table)
        if md:
            w.add_key_value_metadata(md)
    return out.getvalue().to_pybytes()


# --- the dots worker's hooks -----------------------------------------------------------------------------------------

def attach(out, l2, ctx, extras: dict, forced, obs_id: str, detector: int, folder: str, st: dict) -> None:
    """dots._measure: build the photo's table into the Pending `out` (out.cond, out.cond_rel) and its footer entry
    (out.meta["cond"]). Never raises: an error goes to the footer's cond.error and st["cond_error"]."""
    t0 = time.process_time()
    info = {"file": f"{folder}_cond", "version": VERSION, "tile_px": int(C.COND_TILE_PX)}
    try:
        out.cond = build(l2, ctx, extras, out.df, forced, out.peaks, out.ext, out.streaks, obs_id=obs_id,
                         detector=detector)
        out.cond_rel = cond_rel(obs_id, detector, folder)
        info["n_tiles"] = int(out.cond.num_rows)
    except Exception as e:  # noqa: BLE001 - never costs the photo its dots
        out.cond = None
        info["error"] = f"{type(e).__name__}: {e}"[:200]
        st["cond_error"] = info["error"]
    st["cond_s"] = round(time.process_time() - t0, 3)
    out.meta["cond"] = info


def write(store, p, st: dict) -> None:
    """dots._finish, before the dot file: write p.cond. Never raises: an error goes to the footer (p.meta)."""
    if getattr(p, "cond", None) is None:
        return
    try:
        data = table_bytes(p.cond)
        store.write_bytes(p.cond_rel, data)
        st["cond_bytes"] = len(data)
        p.meta.setdefault("cond", {})["bytes"] = len(data)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"[:200]
        p.meta.setdefault("cond", {})["error"] = err
        st["cond_error"] = err


def read_summary(path) -> dict | None:
    """The photo summary of a conditions file."""
    md = pq.read_schema(path).metadata or {}
    v = md.get(META_KEY.encode())
    return json.loads(v) if v else None
