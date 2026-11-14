"""Step 5, `spectra`: a 0.75–5.0 µm spectrum in 102 channels for every fixed source.

Each dot measures its source at one wavelength (SPHEREx's filter varies across the detector), and
the same star lands on a different wavelength in each photo. `link` lists the dots of each fixed
source in members.parquet; this step reads those dots back from dots/<obs_id>D<det>.parquet and
combines them.

Channels (ours, not NASA's official ones): 17 per detector, log-spaced over the detector's range
(config.SPECTRA_DETECTOR_UM). A dot goes to a channel of its own detector; outside the range it goes
to the nearest end channel.

Per (source, channel): dots with a MASK bit in their 3×3 flags are left out; then the median and
MAD scatter, clip dots further than SPECTRA_CLIP_SIGMA × max(dot error, scatter) AND further than
SPECTRA_CLIP_MIN_FRAC × |median| (gross outliers only, so a variable star keeps both states; channels
with at least SPECTRA_CLIP_MIN_N dots), repeat; the value is the inverse-variance weighted mean of the
kept dots, its error 1/sqrt(Σw) × sqrt(max(1, χ²/(n−1))).
Per source: median position and scatter/sqrt(n) error, counts, `saturated` (>= SPECTRA_SAT_MIN_DOTS
dots with OVERFLOW/NONLINEAR in their 3×3), `chi2_nu` of the kept dots against their channel values,
`visit_flux_ratio` (per detector: median of flux/channel value in the source's last visit over its
first visit; median over detectors), and `variable` (both indicators past their thresholds).
Everything is vectorised over all dots (sorts and bincounts); there is no loop per source.
"""

from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from skyshift_core import MASK_BITS, SAT_BITS

from . import calib
from . import config as C
from . import log
from .sky import angsep, cell_of
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("spectra")

NDET = 6
NCH_DET = C.SPECTRA_CHANNELS_PER_DETECTOR
NCH = NDET * NCH_DET
DOT_COLUMNS = ["ra", "dec", "flux_ujy", "flux_err_ujy", "wavelength_um", "flags"]
CALIB_COLUMNS = ["x", "y", "snr", "flags_peak", "pos_err_arcsec"]  # read too when calib.json exists
PSF_COLUMNS = ["ra_psf", "dec_psf", "psf_pos_ok"]  # dots v4: the ePSF position, used where psf_pos_ok


# --- channels --------------------------------------------------------------------------------
def channel_edges() -> np.ndarray:
    """(6, 18) channel edges in µm, log-spaced per detector."""
    return np.stack([np.geomspace(*C.SPECTRA_DETECTOR_UM[d], NCH_DET + 1) for d in range(1, NDET + 1)])


def channels() -> list[dict]:
    """The 102 channel definitions (written to channels.json)."""
    e = channel_edges()
    out = []
    for d in range(NDET):
        for i in range(NCH_DET):
            lo, hi = float(e[d, i]), float(e[d, i + 1])
            out.append({"channel": d * NCH_DET + i, "detector": d + 1, "index": i,
                        "um_lo": round(lo, 6), "um_hi": round(hi, 6), "um_centre": round(float(np.sqrt(lo * hi)), 6)})
    return out


def channel_of(detector, wavelength_um) -> np.ndarray:
    """Channel 0–101 of each dot; -1 for an unknown detector or a missing wavelength."""
    det = np.asarray(detector, np.int64)
    wl = np.asarray(wavelength_um, np.float64)
    e = channel_edges()
    out = np.full(det.shape, -1, np.int64)
    for d in range(1, NDET + 1):
        m = (det == d) & np.isfinite(wl)
        if m.any():
            i = np.clip(np.searchsorted(e[d - 1], wl[m], side="right") - 1, 0, NCH_DET - 1)
            out[m] = (d - 1) * NCH_DET + i
    return out


# --- vectorised group helpers ----------------------------------------------------------------
def group_median(codes: np.ndarray, vals: np.ndarray, n_groups: int) -> np.ndarray:
    """Median of vals per group code (0..n_groups-1); NaN for empty groups.
    pandas' groupby median is a counting sort by group plus a selection per group: O(n), unlike
    a full lexsort (6× faster on 6M dots)."""
    out = np.full(n_groups, np.nan)
    if codes.size == 0:
        return out
    m = pd.Series(np.asarray(vals, np.float64)).groupby(np.asarray(codes, np.int64), sort=False).median()
    out[m.index.to_numpy()] = m.to_numpy()
    return out


def count_distinct(codes: np.ndarray, x: np.ndarray, n_groups: int) -> np.ndarray:
    """Number of distinct x per group code."""
    k = np.unique((codes.astype(np.int64) << 32) | (np.asarray(x, np.int64) & 0xFFFFFFFF))
    return np.bincount(k >> 32, minlength=n_groups)


# --- the computation -------------------------------------------------------------------------
def compute(d: pd.DataFrame) -> pd.DataFrame:
    """Member dots -> one row per source. `d` needs source_id, detector, session, visit, ra, dec,
    flux_ujy, flux_err_ujy, wavelength_um, flags. The list columns hold (102,) arrays."""
    sid_u, s = np.unique(d["source_id"].to_numpy(np.int64), return_inverse=True)
    s = s.ravel()
    N = sid_u.size
    det = d["detector"].to_numpy(np.int64)
    ra = d["ra"].to_numpy(np.float64)
    dec = d["dec"].to_numpy(np.float64)
    flags = d["flags"].to_numpy(np.int64) & 0xFFFFFFFF
    n_dots = np.bincount(s, minlength=N)

    # position
    ra_m = group_median(s, ra, N)
    dec_m = group_median(s, dec, N)
    dx = ((ra - ra_m[s] + 180.0) % 360.0 - 180.0) * np.cos(np.deg2rad(dec_m[s])) * 3600.0
    dy = (dec - dec_m[s]) * 3600.0
    var = np.zeros(N)
    for z in (dx, dy):
        sz, szz = np.bincount(s, z, N), np.bincount(s, z * z, N)
        var += (szz - sz * sz / n_dots) / np.maximum(n_dots - 1, 1)
    del dx, dy
    pos_err = np.maximum(np.sqrt(np.maximum(var, 0.0) / 2.0 / n_dots), C.SPECTRA_POS_ERR_FLOOR_ARCSEC)

    n_sessions = count_distinct(s, d["session"].to_numpy(), N)
    n_visits = count_distinct(s, d["visit"].to_numpy(), N)
    kd = np.unique(s.astype(np.int64) * 8 + det)
    detectors = np.bincount(kd // 8, weights=(1 << np.clip(kd % 8 - 1, 0, 7)).astype(np.float64), minlength=N)
    saturated = np.bincount(s, weights=((flags & SAT_BITS) != 0).astype(np.float64), minlength=N) >= C.SPECTRA_SAT_MIN_DOTS

    # spectrum: (source, channel) groups of the good dots
    f = d["flux_ujy"].to_numpy(np.float64)
    e = d["flux_err_ujy"].to_numpy(np.float64)
    ch = channel_of(det, d["wavelength_um"].to_numpy())
    good = ((flags & MASK_BITS) == 0) & np.isfinite(f) & np.isfinite(e) & (e > 0) & (ch >= 0)
    gi = np.flatnonzero(good)
    uk, g = np.unique(s[gi].astype(np.int64) * NCH + ch[gi], return_inverse=True)
    g = g.ravel()
    G = uk.size
    fg, eg = f[gi], e[gi]
    kept = np.ones(gi.size, bool)
    for _ in range(C.SPECTRA_CLIP_ITER):
        med = group_median(g[kept], fg[kept], G)
        res = np.abs(fg - med[g])
        mad = 1.4826 * group_median(g[kept], res[kept], G)
        nk = np.bincount(g[kept], minlength=G)
        new = ((res <= C.SPECTRA_CLIP_SIGMA * np.maximum(eg, mad[g])) | (res <= C.SPECTRA_CLIP_MIN_FRAC * np.abs(med[g]))
               | (nk[g] < C.SPECTRA_CLIP_MIN_N))
        if np.array_equal(new, kept):
            break
        kept = new
    w = np.where(kept, 1.0 / (eg * eg), 0.0)
    sw = np.bincount(g, w, G)
    mu = np.bincount(g, w * fg, G) / sw
    chi = np.bincount(g, w * (fg - mu[g]) ** 2, G)
    nk = np.bincount(g[kept], minlength=G)
    err = np.sqrt(1.0 / sw) * np.sqrt(np.maximum(1.0, chi / np.maximum(nk - 1, 1)))

    gs, gc = uk // NCH, uk % NCH
    flux = np.full((N, NCH), np.nan, np.float32)
    ferr = np.full((N, NCH), np.nan, np.float32)
    nch = np.zeros((N, NCH), np.int16)
    flux[gs, gc] = mu
    ferr[gs, gc] = err
    nch[gs, gc] = np.minimum(nk, np.iinfo(np.int16).max)

    # variability
    chi_s = np.bincount(gs, chi, N)
    dof_s = np.bincount(gs, nk, N) - np.bincount(gs, (nk > 0).astype(np.float64), N)
    with np.errstate(invalid="ignore", divide="ignore"):
        chi2_nu = np.where(dof_s > 0, chi_s / dof_s, np.nan)
    ratio = np.full(N, np.nan)
    kk = kept & (mu[g] > 0)
    if kk.any():
        r = fg[kk] / mu[g[kk]]
        ss, dd = s[gi][kk].astype(np.int64), det[gi][kk]
        vv = d["visit"].to_numpy(np.int64)[gi][kk]
        V = int(vv.max()) + 1
        u3, c3 = np.unique((ss * 8 + dd) * V + vv, return_inverse=True)
        m3 = group_median(c3.ravel(), r, u3.size)
        sd = u3 // V
        first = np.r_[0, np.flatnonzero(np.diff(sd)) + 1]
        last = np.r_[first[1:] - 1, sd.size - 1]
        ok = (last > first) & (m3[first] > 0)
        rat = m3[last[ok]] / m3[first[ok]]
        ratio = group_median(sd[first[ok]] // 8, rat, N)
    with np.errstate(invalid="ignore"):
        variable = (chi2_nu > C.SPECTRA_VARIABLE_CHI2_NU) & (np.abs(np.log(ratio)) > np.log(C.SPECTRA_VARIABLE_RATIO))

    return pd.DataFrame({
        "source_id": sid_u,
        "ra": ra_m,
        "dec": dec_m,
        "pos_err_arcsec": pos_err.astype(np.float32),
        "n_dots": n_dots.astype(np.int32),
        "n_sessions": n_sessions.astype(np.int16),
        "n_visits": n_visits.astype(np.int16),
        "detectors": detectors.astype(np.int8),
        "cell": cell_of(ra_m, dec_m),
        "saturated": saturated,
        "flux_ujy": list(flux),
        "err_ujy": list(ferr),
        "n": list(nch),
        "chi2_nu": chi2_nu.astype(np.float32),
        "visit_flux_ratio": ratio.astype(np.float32),
        "variable": variable,
    })


def to_arrow(src: pd.DataFrame) -> pa.Table:
    """sources DataFrame -> Arrow table with fixed_size_list<…>[102] spectrum columns."""
    cols, fields = [], []
    for c in src.columns:
        if c in ("flux_ujy", "err_ujy", "n"):
            typ = pa.int16() if c == "n" else pa.float32()
            flat = np.concatenate(src[c].to_list()) if len(src) else np.zeros(0)
            arr = pa.FixedSizeListArray.from_arrays(pa.array(flat.astype(typ.to_pandas_dtype())), NCH)
        else:
            arr = pa.array(src[c].to_numpy())
        cols.append(arr)
        fields.append(pa.field(c, arr.type))
    return pa.Table.from_arrays(cols, schema=pa.schema(fields))


# --- I/O -------------------------------------------------------------------------------------
def load_members(store: Store, zone: C.Zone, workers: int = 16) -> tuple[pd.DataFrame, dict]:
    """The member dots of every fixed source, read back from the dot files via members.parquet. Positions: the ePSF
    fit's where calib.use_psf (dots v4), else the windowed centroid's; each with calib.json's corrections when present."""
    members = store.read_parquet(results_rel(zone.name, "members.parquet"))
    photos = store.read_parquet(photolist_rel(zone.name), columns=["obs_id", "detector", "session", "visit"])
    sv = {(str(o), int(dt)): (int(se), int(vi)) for o, dt, se, vi in photos.itertuples(index=False)}
    groups = members.groupby(["obs_id", "detector"], observed=True, sort=True).indices
    sid_all = members["source_id"].to_numpy(np.int64)
    row_all = members["dot_row"].to_numpy(np.int64)
    bad_rows = [0]
    cal = calib.load(store, zone)  # results/<zone>/calib.json from an earlier run: corrected positions
    corrected = [0]
    n_psf = [0]

    def one(item):
        (obs_id, det), idx = item
        raw = store.read_bytes(dots_rel(obs_id, int(det)))
        have = set(pq.ParquetFile(io.BytesIO(raw)).schema_arrow.names)
        calibrate = cal is not None and set(CALIB_COLUMNS) <= have  # a dot file without x, y: positions stay raw
        v4 = set(PSF_COLUMNS) <= have
        want = [*DOT_COLUMNS, *(CALIB_COLUMNS if calibrate else []), *(PSF_COLUMNS if v4 else [])]
        df = pd.read_parquet(io.BytesIO(raw), columns=want)
        rows = row_all[idx]
        ok = rows < len(df)
        bad_rows[0] += int((~ok).sum())
        part = df.iloc[rows[ok]].reset_index(drop=True)
        dd = np.full(len(part), int(det))
        psf = calib.use_psf(cal, dd, part["psf_pos_ok"].to_numpy(bool) if v4 else None,
                            part["ra_psf"] if v4 else None, part["dec_psf"] if v4 else None,
                            part["snr"] if (v4 and calibrate) else None)
        if calibrate:
            J = calib.photo_jacobian(df["x"], df["y"], df["ra"], df["dec"])
            ce, cn = calib.photo_coefs(cal, [obs_id], [int(det)])
            pe, pn = calib.poly_offsets(ce[0], cn[0], part["x"], part["y"])
            ra, dec, _, okc = calib.apply(cal, dd, part["x"], part["y"], part["ra"], part["dec"],
                                          part["snr"], part["flags_peak"], part["pos_err_arcsec"], J, pe, pn)
            if psf.any():  # ePSF positions: their own distortion, no phase term
                ce, cn = calib.photo_coefs(cal, [obs_id], [int(det)], "psf")
                pe, pn = calib.poly_offsets(ce[0], cn[0], part["x"].to_numpy()[psf], part["y"].to_numpy()[psf])
                ra[psf], dec[psf], _ = calib.apply_psf(cal, dd[psf], part["ra_psf"].to_numpy()[psf],
                                                       part["dec_psf"].to_numpy()[psf], part["snr"].to_numpy()[psf],
                                                       np.full(int(psf.sum()), np.nan), pe, pn)
                okc[psf] = False
            f = calib.flux_factor(cal, dd, part["x"], part["y"], "ap")
            part = part[DOT_COLUMNS].assign(ra=ra, dec=dec, flux_ujy=(part["flux_ujy"] * f).astype(np.float32),
                                            flux_err_ujy=(part["flux_err_ujy"] * f).astype(np.float32))
            corrected[0] += int(okc.sum())
        elif psf.any():
            part = part[DOT_COLUMNS].assign(ra=np.where(psf, part["ra_psf"], part["ra"]),
                                            dec=np.where(psf, part["dec_psf"], part["dec"]))
        else:
            part = part[DOT_COLUMNS]
        n_psf[0] += int(psf.sum())
        se, vi = sv.get((str(obs_id), int(det)), (-1, -1))
        part["source_id"] = sid_all[idx[ok]]
        part["detector"] = np.int8(det)
        part["session"] = np.int32(se)
        part["visit"] = np.int16(vi)
        return part

    with ThreadPoolExecutor(max_workers=max(4, min(workers, 32))) as ex:
        parts = list(ex.map(one, groups.items()))
    cols = ["source_id", "detector", "session", "visit", *DOT_COLUMNS]
    d = pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
    st = {"member_dots": int(len(members)), "member_rows_missing": bad_rows[0],
          "calib_applied": cal is not None, "calib_phase_corrected_dots": corrected[0], "pos_psf_dots": n_psf[0]}

    # a dot file rewritten after `link` would make dot_row point elsewhere: drop far members
    fixed = store.read_parquet(results_rel(zone.name, "fixed.parquet"), columns=["source_id", "ra", "dec"])
    fx = fixed.set_index("source_id")
    pos = fx.index.get_indexer(d["source_id"].to_numpy())
    known = pos >= 0
    sep = np.full(len(d), np.inf)
    sep[known] = angsep(d["ra"].to_numpy()[known], d["dec"].to_numpy()[known],
                        fx["ra"].to_numpy()[pos[known]], fx["dec"].to_numpy()[pos[known]]) * 3600.0
    near = sep <= C.SPECTRA_MEMBER_MAX_ARCSEC
    st["member_dots_stale"] = int((~near).sum())
    if st["member_dots_stale"]:
        L.warning("spectra %s: %d member dots are > %.0f″ from their source (dot files rewritten after link?): dropped",
                  zone.name, st["member_dots_stale"], C.SPECTRA_MEMBER_MAX_ARCSEC)
    return d[near].reset_index(drop=True), st


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    d, st = load_members(store, zone, workers)
    t1 = time.time()
    L.info("spectra %s: %d member dots loaded in %.0f s", zone.name, len(d), t1 - t0)
    src = compute(d)
    t2 = time.time()
    nbytes = store.write_table(results_rel(zone.name, "sources.parquet"), to_arrow(src))
    store.write_json(results_rel(zone.name, "channels.json"), channels())
    st.update({
        "sources": int(len(src)),
        "dots_in_spectra": int(np.sum([x.sum() for x in src["n"]])) if len(src) else 0,
        "saturated": int(src["saturated"].sum()),
        "variable": int(src["variable"].sum()),
        "sources_bytes": int(nbytes),
        "load_s": round(t1 - t0, 1),
        "compute_s": round(t2 - t1, 1),
        "wall_s": round(time.time() - t0, 1),
    })
    L.info("spectra %s: %s", zone.name, st)
    return st
