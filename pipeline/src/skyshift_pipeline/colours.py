"""Step `colours`: simultaneous colours of fixed sources, immune to variability.

D1+D4, D2+D5 and D3+D6 are exposed at the same moment (same obs_id). For each fixed source and each
pair, every photo in which both detectors caught it gives one flux ratio f_b / f_a; a star that
brightens between photos changes both sides together, so the ratio doesn't care. Per (source, pair):
the median ratio over photos, and the AB colour m_a − m_b = 2.5 log10(f_b / f_a) (positive when
the longer wavelength is brighter in AB, i.e. red). Error: the larger of the robust scatter of the
per-photo colours / √n (n ≥ 3) and the flux errors propagated / √n. Each side is at its own
wavelength inside its detector; the median wavelengths are reported (um_a, um_b).
Dots with MASK bits in their 3×3 are left out; several dots of one source in one photo are averaged.

Writes results/<zone>/colours.parquet: one row per (source, pair).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from skyshift_core import MASK_BITS

from . import config as C
from . import log
from .motion_io import member_dots
from .spectra import group_median
from .store import Store, results_rel

L = log.get("colours")

COLUMNS = ["source_id", "pair", "det_a", "det_b", "n", "ratio", "ratio_err", "colour_ab", "colour_err", "um_a", "um_b"]


def compute(d: pd.DataFrame) -> pd.DataFrame:
    """Member dots (source_id, obs_id, detector, flux_ujy, flux_err_ujy, wavelength_um, flags) → colours."""
    f = d["flux_ujy"].to_numpy(np.float64)
    e = d["flux_err_ujy"].to_numpy(np.float64)
    good = (((d["flags"].to_numpy(np.int64) & 0xFFFFFFFF) & MASK_BITS) == 0) & np.isfinite(f) & (f > 0) & np.isfinite(e) & (e > 0)
    g = pd.DataFrame({"source_id": d["source_id"].to_numpy(np.int64)[good],
                      "obs": pd.Categorical(d["obs_id"].astype(str).to_numpy()[good]).codes.astype(np.int64),
                      "det": d["detector"].to_numpy(np.int64)[good], "f": f[good], "e2": e[good] ** 2,
                      "um": d["wavelength_um"].to_numpy(np.float64)[good]})
    # one value per (source, photo, detector)
    g = g.groupby(["source_id", "obs", "det"], sort=False).agg(f=("f", "mean"), e2=("e2", "mean"), um=("um", "mean"), k=("f", "size")).reset_index()
    g["e2"] = g["e2"] / g["k"]
    parts = []
    for a, b in C.SIMULTANEOUS_PAIRS:
        A = g[g["det"] == a]
        B = g[g["det"] == b]
        j = A.merge(B, on=["source_id", "obs"], suffixes=("_a", "_b"))
        if len(j) == 0:
            continue
        r = j["f_b"].to_numpy() / j["f_a"].to_numpy()
        rel_err = np.sqrt(j["e2_a"].to_numpy() / j["f_a"].to_numpy() ** 2 + j["e2_b"].to_numpy() / j["f_b"].to_numpy() ** 2)
        c = 2.5 * np.log10(r)
        sid_u, s = np.unique(j["source_id"].to_numpy(np.int64), return_inverse=True)
        s = s.ravel()
        N = sid_u.size
        n = np.bincount(s, minlength=N)
        med_r = group_median(s, r, N)
        med_c = 2.5 * np.log10(med_r)
        mad = 1.4826 * group_median(s, np.abs(c - med_c[s]), N)
        # the median's error ≈ 1.2533 σ / √n
        scat_err = np.where(n >= 3, 1.2533 * mad / np.sqrt(n), 0.0)
        prop_err = 2.5 / np.log(10) * np.sqrt(np.bincount(s, rel_err ** 2, N) / n) / np.sqrt(n)
        cerr = np.maximum(scat_err, prop_err)
        keep = n >= C.COLOUR_MIN_PAIRS
        parts.append(pd.DataFrame({
            "source_id": sid_u, "pair": f"D{a}D{b}", "det_a": np.int8(a), "det_b": np.int8(b), "n": n.astype(np.int32),
            "ratio": med_r.astype(np.float32), "ratio_err": (med_r * cerr * np.log(10) / 2.5).astype(np.float32),
            "colour_ab": med_c.astype(np.float32), "colour_err": cerr.astype(np.float32),
            "um_a": group_median(s, j["um_a"].to_numpy(), N).astype(np.float32),
            "um_b": group_median(s, j["um_b"].to_numpy(), N).astype(np.float32),
        })[keep])
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS})
    return pd.concat(parts, ignore_index=True).sort_values(["source_id", "det_a"]).reset_index(drop=True)[COLUMNS]


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    d, st = member_dots(store, zone, workers)
    out = compute(d)
    store.write_parquet(results_rel(zone.name, "colours.parquet"), out)
    st = {**st, "rows": int(len(out)), "sources": int(out["source_id"].nunique()) if len(out) else 0,
          "per_pair": {p: int((out["pair"] == p).sum()) for p in ("D1D4", "D2D5", "D3D6")},
          "wall_s": round(time.time() - t0, 1)}
    L.info("colours %s: %s", zone.name, st)
    return st
