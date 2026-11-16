"""Shared code of the vetting harness (scripts/vet): local data, aligned stamps from NASA's S3 copy, the mover packet.

Data: a local folder (env SKYSHIFT_VET_DATA, default ~/.cache/skyshift-vet/data) filled by prep_zone.py:
  <zone>/photos.parquet  refcat.parquet  fixed.parquet  movers.parquet  pred.parquet  freedots.parquet
         highpm.parquet  shape.parquet  sources.parquet (candidates' rows only)  channels.json
  candidates.json (the site's list)
Images are never stored: every stamp is cut from the photo's FITS on S3 with range reads (about 1 s each).

Every panel of every image uses one grid: N x N pixels of 6.15", north up, east left, centred on the position asked
for, so anything fixed on the sky sits on the same pixel in every panel.
"""
from __future__ import annotations

import functools
import json
import math
import os
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from astropy.io import fits
from scipy.ndimage import map_coordinates, median_filter

from skyshift_core.flags import BIT_NAMES, MASK_BITS
from skyshift_core.level2 import _FSSPEC_KWARGS, _celestial_wcs
from skyshift_pipeline.link import dot_sigma_arcsec
from skyshift_pipeline.refcat import move_to_epoch
from skyshift_pipeline.sky import Footprint, angsep, offset_radec

DATA = Path(os.environ.get("SKYSHIFT_VET_DATA", "~/.cache/skyshift-vet/data")).expanduser()
# Blind tests: every known-body line is left out, so a checker must judge a mover from the images and numbers alone.
HIDE_KNOWN = os.environ.get("SKYSHIFT_VET_HIDE_KNOWN") == "1"
N, SCALE = 41, 6.15
C0 = N // 2
MAX_DET_PANELS, N_SAME_VISIT, N_OTHER_VISIT = 6, 2, 2
MIN_GAP_H = 6.0          # reference sessions at least this far from the mover's session ...
MIN_SHIFT_ARCSEC = 40.0  # ... and far enough for the mover to have moved this much at its fitted rate (slow moons)
MASKED = "#9c6b00"       # amber: pixels masked by NASA's flags


# ------------------------------------------------------------------ local data

@functools.lru_cache(maxsize=64)
def table(zone: str, name: str) -> pd.DataFrame | None:
    p = DATA / zone / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else None


@functools.lru_cache(maxsize=1)
def candidates() -> dict:
    p = DATA / "candidates.json"
    return {c["id"]: c for c in json.load(open(p))["candidates"]} if p.exists() else {}


def zone_of(cand_id: str) -> str:
    return cand_id.split("-")[0]


def source_ids(cand_id: str) -> list[int]:
    """The engine source ids in a non-mover candidate id: <zone>-<pm|sp|sh>-<source_id>[-<source_id_b>]."""
    return [int(x) for x in cand_id.split("-")[2:] if x.isdigit()]


def mover_row(cand_id: str):
    mv = table(zone_of(cand_id), "movers")
    if mv is None:
        return None
    r = mv[mv.id == cand_id]
    return None if r.empty else r.iloc[0]


# ------------------------------------------------------------------ geometry and stamps

def grid(ra0: float, dec0: float):
    jj, ii = np.meshgrid(np.arange(N), np.arange(N))
    return offset_radec(ra0, dec0, (C0 - jj) * SCALE, (ii - C0) * SCALE)  # row 0 = south (drawn origin lower)


def to_grid(ra0, dec0, ra, dec):
    e = ((np.asarray(ra) - ra0 + 180.0) % 360.0 - 180.0) * math.cos(math.radians(dec0)) * 3600.0
    n = (np.asarray(dec) - dec0) * 3600.0
    return C0 - e / SCALE, C0 + n / SCALE  # (col, row)


def offsets_arcsec(ra0, dec0, ra, dec):
    e = ((np.asarray(ra, float) - ra0 + 180.0) % 360.0 - 180.0) * math.cos(math.radians(dec0)) * 3600.0
    return e, (np.asarray(dec, float) - dec0) * 3600.0


def read_stamp(url: str, gra, gdec, dots_xy=None, tries: int = 3) -> dict:
    """Resample one photo onto the grid. dots_xy: [(x, y)] full-photo pixels (0-based) -> RA/Dec with its WCS.
    Returns data (masked pixels NaN), flags, dots, mjd, centre pixel in the photo; or {"error": ...}."""
    last = None
    for a in range(tries):
        try:
            with fits.open(url, use_fsspec=True, fsspec_kwargs=_FSSPEC_KWARGS, lazy_load_hdus=True, memmap=False) as h:
                img = h["IMAGE"]
                hdr = img.header
                w = _celestial_wcs(hdr)
                nx, ny = int(hdr["NAXIS1"]), int(hdr["NAXIS2"])
                px, py = w.world_to_pixel_values(gra.ravel(), gdec.ravel())
                x0, x1 = int(max(0, math.floor(np.nanmin(px)) - 2)), int(min(nx, math.ceil(np.nanmax(px)) + 3))
                y0, y1 = int(max(0, math.floor(np.nanmin(py)) - 2)), int(min(ny, math.ceil(np.nanmax(py)) + 3))
                dots = [tuple(float(v) for v in w.pixel_to_world_values(x, y)) for x, y in (dots_xy or [])]
                cpx = (float(px[N * C0 + C0]), float(py[N * C0 + C0]))
                if x1 - x0 < 2 or y1 - y0 < 2:
                    return {"data": np.full((N, N), np.nan), "flags": np.zeros((N, N), np.int64), "dots": dots,
                            "mjd": float(hdr.get("MJD-AVG", np.nan)), "centre_px": cpx}
                d = np.asarray(img.section[y0:y1, x0:x1], np.float32)
                f = np.asarray(h["FLAGS"].section[y0:y1, x0:x1]).astype(np.int64)
                mjd = float(hdr.get("MJD-AVG", np.nan))
            co = np.vstack([py - y0, px - x0])
            data = map_coordinates(d.astype(np.float64), co, order=1, cval=np.nan).reshape(N, N)
            flg = map_coordinates(f.astype(np.float64), co, order=0, cval=0).reshape(N, N).astype(np.int64)
            out = ((px < -0.5) | (px > nx - 0.5) | (py < -0.5) | (py > ny - 0.5)).reshape(N, N)
            data[out | ((flg & MASK_BITS) != 0)] = np.nan
            return {"data": data, "flags": flg, "dots": dots, "mjd": mjd, "centre_px": cpx}
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (a + 1))
    return {"error": repr(last)[:200]}


def covering(photos: pd.DataFrame, ra: float, dec: float, det: int | None = None, whole_grid: bool = True) -> pd.DataFrame:
    """Photos whose footprint holds the whole grid around (ra, dec); whole_grid=False: the spot itself, 3 px inside
    (stamps then show the part outside the photo as no data)."""
    p = photos if det is None else photos[photos.detector == det]
    p = p[angsep(ra, dec, p.s_ra.to_numpy(float), p.s_dec.to_numpy(float)) < 3.0]
    half = (N * SCALE / 2 + 30) / 3600.0 if whole_grid else 3 * SCALE / 3600.0
    ok = np.array([Footprint.from_s_region(s).edge_distance_deg(ra, dec) > half for s in p.s_region], bool) if len(p) else []
    return p[ok].copy()


_WAVE: dict = {}


def wavelength_um(photos: pd.DataFrame, det: int, x: float, y: float) -> float:
    """Central wavelength (um) seen by pixel (x, y) of a detector. The filter is fixed to the detector, so one photo's
    WCS-WAVE table serves every exposure; cached in <data>/_wave/D<det>.pkl. NaN when it cannot be read."""
    import pickle
    if det not in _WAVE:
        p = DATA / "_wave" / f"D{det}.pkl"
        try:
            if p.exists():
                _WAVE[det] = pickle.load(open(p, "rb"))
            else:
                from skyshift_core.level2 import WaveTable
                url = photos[photos.detector == det].url.iloc[0]
                with fits.open(url, use_fsspec=True, fsspec_kwargs=_FSSPEC_KWARGS, lazy_load_hdus=True, memmap=False) as h:
                    _WAVE[det] = WaveTable.from_hdus(h["IMAGE"].header, h["WCS-WAVE"].data)
                p.parent.mkdir(parents=True, exist_ok=True)
                pickle.dump(_WAVE[det], open(p, "wb"))
        except Exception:  # noqa: BLE001
            _WAVE[det] = None
    w = _WAVE[det]
    try:
        return float(w.wavelength(float(x), float(y))) if w is not None and np.isfinite(x) and np.isfinite(y) else float("nan")
    except Exception:  # noqa: BLE001
        return float("nan")


PAIRED = {1: 4, 4: 1, 2: 5, 5: 2, 3: 6, 6: 3}  # detectors exposed at the same moment


def bgsub(a):
    """Remove a smooth background (11-px median filter): detector-edge gradients go, compact sources stay."""
    if not np.isfinite(a).any():
        return a
    f = np.where(np.isfinite(a), a, np.nanmedian(a))
    return a - median_filter(f, size=11, mode="nearest")


def sig(a):
    v = a[np.isfinite(a)]
    return 1.4826 * np.median(np.abs(v - np.median(v))) if v.size > 20 else np.nan


def ap_snr(a, s, pts, r: float = 1.5):
    """Aperture S/N (radius r px) at grid positions; the largest one. nan when masked there."""
    if not np.isfinite(s) or s <= 0:
        return np.nan
    yy, xx = np.indices(a.shape)
    best = np.nan
    for gx, gy in pts:
        m = (xx - gx) ** 2 + (yy - gy) ** 2 <= r ** 2
        v = a[m]
        if m.sum() == 0 or np.isfinite(v).sum() < 0.6 * m.sum():
            continue
        val = np.nansum(v) / (s * math.sqrt(np.isfinite(v).sum()))
        best = val if not np.isfinite(best) else max(best, val)
    return best


def ring_snr(a, s, centre, pt, n: int = 8) -> list[float]:
    """Aperture S/N at the other n-1 spots at the same distance from the centre as pt, evenly round the circle.
    Tells a companion from the star's own halo at that radius."""
    dx, dy = pt[0] - centre[0], pt[1] - centre[1]
    rad, ang = math.hypot(dx, dy), math.atan2(dy, dx)
    vals = [ap_snr(a, s, [(centre[0] + rad * math.cos(ang + 2 * math.pi * k / n), centre[1] + rad * math.sin(ang + 2 * math.pi * k / n))])
            for k in range(1, n)]
    return [float(v) for v in vals if np.isfinite(v)]


def fs(x):
    return "masked" if not np.isfinite(x) else f"{x:+.0f}σ"


def ticks(ax, gx, gy, col, lw=1.2, label=None, alpha=1.0):
    """Four short ticks around a position; the centre stays visible."""
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ax.plot([gx + 1.8 * dx, gx + 3.6 * dx], [gy + 1.8 * dy, gy + 3.6 * dy], color=col, lw=lw, alpha=alpha,
                solid_capstyle="butt")
    if label is not None:
        ax.text(gx + 3.0, gy + 3.0, str(label), color=col, fontsize=9, weight="bold")


def flag_names(bits: int) -> list[str]:
    return [n for b, n in BIT_NAMES.items() if bits & (1 << b) and n not in ("SOURCE", "FULLSAMPLE")]


def fmt_dt(hours: float) -> str:
    a = abs(hours)
    s = "+" if hours >= 0 else "-"
    return f"{s}{a * 60:.1f} min" if a < 2 else f"{s}{a:.1f} h" if a < 48 else f"{s}{a / 24:.0f} d"


def grey(ax, a, sigma, hi):
    cg = matplotlib.colormaps["gray"].copy(); cg.set_bad(MASKED)
    ax.imshow(np.arcsinh(a / (3 * sigma)), origin="lower", cmap=cg, vmin=np.arcsinh(-1), vmax=np.arcsinh(hi), interpolation="nearest")


def diverging(ax, a, sigma):
    cd = matplotlib.colormaps["RdBu_r"].copy(); cd.set_bad(MASKED)
    ax.imshow(a / sigma, origin="lower", cmap=cd, vmin=-8, vmax=8, interpolation="nearest")


def stretch_hi(imgs, sigma):
    v = np.concatenate([a[np.isfinite(a)] for a in imgs if np.isfinite(a).any()] or [np.zeros(1)])
    return max(10.0, float(np.nanpercentile(v, 99.7)) / (3 * sigma))


# ------------------------------------------------------------------ the mover packet

def pick_refs(photos: pd.DataFrame, mv, det: int) -> pd.DataFrame:
    """Reference exposures of detector `det` holding the whole grid: other sessions of the mover's visit (far enough
    in time for the mover to have left: MIN_GAP_H and MIN_SHIFT_ARCSEC at its rate) and the nearest other visit.
    Twice as many as shown, so masked ones can be replaced."""
    p = covering(photos[photos.session != mv.session], mv.ra, mv.dec, det)
    if p.empty:
        return p
    p["dt_h"] = (p.mjd - mv.mjd) * 24.0
    one = p.sort_values("mjd").groupby("session").apply(lambda g: g.iloc[len(g) // 2], include_groups=False).reset_index()
    rate = _g(mv, "rate_arcsec_h")
    gap = max(MIN_GAP_H, MIN_SHIFT_ARCSEC / rate) if np.isfinite(rate) and rate > 0 else MIN_GAP_H
    same = one[(one.visit == mv.visit) & (one.dt_h.abs() >= gap)].sort_values("dt_h", key=np.abs).head(2 * (N_SAME_VISIT + N_OTHER_VISIT))
    other = one[one.visit != mv.visit]
    if len(other):
        v = other.assign(a=other.dt_h.abs()).sort_values("a").visit.iloc[0]
        other = other[other.visit == v].sort_values("dt_h", key=np.abs).head(2 * N_OTHER_VISIT)
    return pd.concat([same, other]).sort_values("mjd")


def _g(mv, k):
    v = mv.get(k, np.nan) if hasattr(mv, "get") else getattr(mv, k, np.nan)
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def mover_dots(mv) -> list[dict]:
    dots = [dict(obs=o, det=int(d), x=float(x), y=float(y), mjd=float(m), flux=float(f), snr=float(s), size=float(z),
                 chi2=float(c), fpk=int(k))
            for o, d, x, y, m, f, s, z, c, k in zip(mv.dot_obs, mv.dot_det, mv.dot_x, mv.dot_y, mv.dot_mjd, mv.dot_flux,
                                                     mv.dot_snr, mv.dot_size, mv.dot_chi2, mv.dot_fpk)]
    dots.sort(key=lambda d: (d["mjd"], d["det"]))
    for i, d in enumerate(dots):
        d["n"] = i + 1
    return dots


def numbers_verdict(measured: dict) -> str:
    """The numeric rule on the aperture S/N at the marks (medians over panels): 'static' when a source is there at
    other times and the difference keeps less than half of the signal; 'mover' when the difference keeps at least
    half and other times are empty; else 'unclear'. On 60 labelled movers: 30/30 real kept, 23/30 stars dropped."""
    r1 = [v for v in measured.get("det", []) if np.isfinite(v)]
    r2 = [v for v in measured.get("dif", []) if np.isfinite(v)]
    r3 = [v for v in measured.get("ref", []) if np.isfinite(v)]
    if len(r3) < 2 or not r1 or not r2:
        return "unclear"
    m1, m2, m3 = float(np.median(r1)), float(np.median(r2)), float(np.median(r3))
    if m3 >= 5 and m2 < 0.5 * m1:
        return "static"
    # round 1: a hot pixel on a fixed star keeps the difference high; a source at other times nearly as bright as the
    # faintest detection is not a clean mover
    if m2 >= 5 and m2 >= 0.5 * m1 and m3 < 0.5 * m1 and not (m3 >= 5 and m3 >= 0.6 * min(r1)):
        return "mover"
    return "unclear"


def session_rows(mv, photos: pd.DataFrame, pool=None, max_rows: int = 18) -> list[dict]:
    """Every exposure of the mover's session whose photo holds the spot (all detectors, time order): the aperture S/N
    at the fitted track position, the engine detection if any, the detector pixel of the spot and its wavelength."""
    p = covering(photos[photos.session == mv.session], mv.ra, mv.dec, whole_grid=False).sort_values(["mjd", "detector"])
    dots = mover_dots(mv)
    have = {(d["obs"], d["det"]): d for d in dots}
    if len(p) > max_rows:
        own = p[[(o, d) in have for o, d in zip(p.obs_id, p.detector)]]
        rest = p.drop(own.index)
        keep = set(own.detector) | {PAIRED[d] for d in own.detector}
        rest = rest[rest.detector.isin(keep)] if len(own) else rest
        p = pd.concat([own, rest.head(max(0, max_rows - len(own)))]).sort_values(["mjd", "detector"])
    gra, gdec = grid(mv.ra, mv.dec)
    run = pool.map if pool is not None else map
    stamps = list(run(lambda r: read_stamp(r.url, gra, gdec, [(have[(r.obs_id, r.detector)]["x"], have[(r.obs_id, r.detector)]["y"])]
                                           if (r.obs_id, r.detector) in have else None), p.itertuples()))
    rate, pa = _g(mv, "rate_arcsec_h"), _g(mv, "pa_deg")
    vx = rate * math.sin(math.radians(pa)) if np.isfinite(rate) else 0.0
    vy = rate * math.cos(math.radians(pa)) if np.isfinite(rate) else 0.0
    rows = []
    for r, s in zip(p.itertuples(), stamps):
        im = bgsub(s["data"]) if "data" in s else np.full((N, N), np.nan)
        dt_h = (r.mjd - float(mv.mjd)) * 24
        gx, gy = C0 - vx * dt_h / SCALE, C0 + vy * dt_h / SCALE
        cp = s.get("centre_px", (np.nan, np.nan))
        d = have.get((r.obs_id, r.detector))
        dot_g = to_grid(mv.ra, mv.dec, s["dots"][0][0], s["dots"][0][1]) if d and s.get("dots") else None
        rows.append({"mjd": float(r.mjd), "det": int(r.detector), "obs": r.obs_id, "dot": d, "img": im, "track_g": (gx, gy),
                     "dot_g": dot_g, "snr": ap_snr(im, sig(im), [(gx, gy)]), "px": cp,
                     "um": wavelength_um(photos, int(r.detector), cp[0], cp[1]), "error": s.get("error")})
    return rows


def session_steps(rows: list[dict]) -> list[float]:
    """Shift of the spot on the detector (px) between consecutive exposures, per detector."""
    out = []
    for det in {r["det"] for r in rows}:
        rr = sorted([r for r in rows if r["det"] == det], key=lambda r: r["mjd"])
        out += [math.hypot(a["px"][0] - b["px"][0], a["px"][1] - b["px"][1]) for a, b in zip(rr, rr[1:])
                if np.isfinite(a["px"][0]) and np.isfinite(b["px"][0])]
    return out


def warnings_for(mv, dots, rows, measured, ctx) -> list[str]:
    """Automatic warnings from the numbers (round 1: these settled most movers at a glance)."""
    W = []
    ex = {}
    for d in dots:
        ex.setdefault(round(d["mjd"] * 1440, 1), []).append(d)
    for d in dots:
        if abs(d["x"] - round(d["x"])) < 0.02 and abs(d["y"] - round(d["y"])) < 0.02:
            W.append(f"WHOLE-PIXEL POSITION: detection #{d['n']} is at pixel {d['x']:.1f},{d['y']:.1f} exactly: the centroid fell back to "
                     f"the peak pixel (one-pixel spike or a failed fit).")
    seen = set()
    for a in dots:
        for b in dots:
            if a["n"] < b["n"] and a["det"] == b["det"] and abs(a["mjd"] - b["mjd"]) > 1e-4 and \
                    abs(a["x"] - b["x"]) < 0.6 and abs(a["y"] - b["y"]) < 0.6 and (a["n"], b["n"]) not in seen:
                seen.add((a["n"], b["n"]))
                W.append(f"SAME DETECTOR PIXEL: detections #{a['n']} and #{b['n']} sit on D{a['det']} pixel {a['x']:.1f},{a['y']:.1f} in different "
                         f"exposures: the telescope did not step between them, so a hot pixel stays on one sky spot and looks like a source.")
    fd = (ctx or {}).get("freedots")
    if fd is not None:
        for d in dots:
            f = fd[(fd.detector == d["det"]) & (np.abs(fd.x - d["x"]) <= 1.5) & (np.abs(fd.y - d["y"]) <= 1.5)]
            nexp, allexp = f.obs_id.nunique(), fd[fd.detector == d["det"]].obs_id.nunique()
            if nexp >= 3:
                W.append(f"HOT PIXEL: detection #{d['n']} sits on D{d['det']} pixel {d['x']:.0f},{d['y']:.0f}, which has unassigned detections in "
                         f"{nexp} of {allexp} exposures of this zone (a sky source lands on a different pixel each time): a detector defect, "
                         f"not a source. A fixed star under it does not make it real.")
    sharp = [d for d in dots if d["size"] < 2.2 or d["chi2"] > 10]
    if sharp:
        W.append("NOT STAR-LIKE: " + ", ".join(f"#{d['n']} (size {d['size']:.1f}\", PSF-fit χ² {d['chi2']:.0f})" for d in sharp) +
                 ": sharper than a star (size < 2.2\") or a bad PSF fit (χ² > 10); in round 1 these were artifacts every time.")
    broad = [d for d in dots if d["size"] > 5.8]
    if broad:
        W.append("BROAD: " + ", ".join(f"#{d['n']} ({d['size']:.1f}\")" for d in broad) + ": wider than a star: a blend, a diffuse glow or a streak.")
    if rows:
        own_dets = {d["det"] for d in dots}
        t = sorted({round(r["mjd"] * 1440, 1) for r in rows})
        tdet = sorted(ex)
        gaps = [r for r in rows if r["det"] in own_dets and r["dot"] is None and tdet and tdet[0] < round(r["mjd"] * 1440, 1) < tdet[-1]]
        if gaps:
            W.append("GAP BETWEEN DETECTIONS: " + "; ".join(f"D{r['det']} at {fmt_dt((r['mjd'] - dots[0]['mjd']) * 24)} shows {fs(r['snr'])} at the track"
                                                           for r in gaps) + " with no engine detection: a real source does not vanish in the middle.")
        miss = [r for r in rows if r["det"] in own_dets and r["dot"] is None and np.isfinite(r["snr"]) and r["snr"] < 3]
        if miss and not gaps:
            W.append(f"NOT SEEN in {len(miss)} exposure(s) of the same detector(s) in this session (S/N < 3 at the track).")
        paired_sn = [r["snr"] for d in dots for r in rows if r["det"] == PAIRED[d["det"]] and abs(r["mjd"] - d["mjd"]) < 2e-4 and np.isfinite(r["snr"])]
        # a neighbour within the 9" aperture puts its own light at the track: that is not the mover seen in the paired detector
        leak = None
        fx0 = (ctx or {}).get("fixed")
        if fx0 is not None:
            s0 = angsep(mv.ra, mv.dec, fx0.ra.to_numpy(float), fx0.dec.to_numpy(float)) * 3600
            leak = float(s0.min()) if len(s0) and s0.min() <= 12 else None
        paired_seen = bool(paired_sn) and float(np.median(paired_sn)) >= 4 and leak is None
        if bool(paired_sn) and float(np.median(paired_sn)) >= 4 and leak is not None:
            W.append(f"PAIRED DETECTOR SIGNAL IS A NEIGHBOUR'S: the paired detector shows a median S/N of {np.median(paired_sn):.0f} at the track, "
                     f"but a fixed source sits {leak:.1f}\" away, inside the 9\" aperture: that light is the neighbour's, not evidence for the mover.")
        for d in dots:
            pr = [r for r in rows if r["det"] == PAIRED[d["det"]] and abs(r["mjd"] - d["mjd"]) < 2e-4]
            if pr and np.isfinite(pr[0]["snr"]) and pr[0]["snr"] < 3 and d["snr"] > 15 and not paired_seen:
                W.append(f"PAIRED DETECTOR EMPTY: #{d['n']} is S/N {d['snr']:.0f} in D{d['det']} but D{pr[0]['det']}, exposed at the same moment, "
                         f"shows {fs(pr[0]['snr'])} at the spot (cosmic rays and hot pixels hit one detector only; a very red or blue source can do this too).")
        if len(own_dets) == 1 and paired_seen:
            W.append(f"PAIRED DETECTOR SEES IT: the engine has detections in D{next(iter(own_dets))} only, but the paired detector shows a median "
                     f"S/N of {np.median(paired_sn):.0f} at the track at the same moments: the light is in both, so it is on the sky and not a "
                     f"one-detector defect (a real mover does this; so does a fixed star).")
        elif len(own_dets) == 1 and not any(r["dot"] is not None and r["det"] != next(iter(own_dets)) for r in rows):
            W.append(f"ONE DETECTOR ONLY: every detection is in D{next(iter(own_dets))}. D6-only is common for real asteroids (heat glow); "
                     f"D4-only or D5-only with nothing in D1/D2 was junk in round 1.")
        steps = session_steps(rows)
        if steps and max(steps) < 3:
            W.append("NO TELESCOPE STEP in this session: the spot stays on the same detector pixels, so detector defects cannot be told from the sky here.")
    if len(ex) >= 2 and measured.get("_motion_sigma") is not None and measured["_motion_sigma"] < 2:
        W.append(f"STATIONARY within the errors: the positions agree to {measured['_motion_sigma']:.1f}σ over the session.")
    r3 = [v for v in measured.get("ref", []) if np.isfinite(v)]
    if len(r3) >= 2 and np.median(r3) >= 5:
        W.append(f"SOURCE AT OTHER TIMES: median S/N {np.median(r3):.0f} at the marks in other sessions and visits: a fixed source is here.")
    if ctx:
        fx, ref = ctx.get("fixed"), ctx.get("refcat")
        if fx is not None:
            sep = angsep(mv.ra, mv.dec, fx.ra.to_numpy(float), fx.dec.to_numpy(float)) * 3600
            near = np.sort(sep[sep <= 12])
            if len(near) >= 1 and near[0] <= 9.5:
                W.append(f"FIXED SOURCE {near[0]:.1f}\" away (1.5 px): the engine's veto radius is 6\"; in round 1 movers 6-9\" from a fixed source were that source.")
        if ref is not None:
            b = ref[(ref["cat"] == "gaia") & (ref.g_mag < 7.5) & (np.abs(ref.dec - mv.dec) < 600 / 3600.0)]
            if len(b):
                bs = angsep(mv.ra, mv.dec, b.ra.to_numpy(float), b.dec.to_numpy(float)) * 3600
                j = int(np.argmin(bs))
                if bs[j] <= 600:
                    W.append(f"BRIGHT STAR G={b.g_mag.iloc[j]:.1f} at {bs[j] / 60:.1f}': a broad detection, or one seen in a single detector, this close "
                             f"to such a star is scattered light, a ghost or a diffraction spike until shown otherwise (rule A6).")
            g = ref[ref["cat"] == "gaia"]
            g = g[np.abs(g.dec - mv.dec) < 15 / 3600.0]
            if len(g):
                gra, gdec = move_to_epoch(g, float(mv.mjd))
                e, n = offsets_arcsec(mv.ra, mv.dec, np.asarray(gra), np.asarray(gdec))
                k = np.hypot(e, n) <= 12
                if k.sum() >= 2:
                    ee, nn = e[k], n[k]
                    for i in range(len(ee)):
                        for j in range(i + 1, len(ee)):
                            if math.hypot(ee[i] + ee[j], nn[i] + nn[j]) < 4 and math.hypot(ee[i] - ee[j], nn[i] - nn[j]) > 10:
                                W.append(f"BETWEEN TWO STARS: Gaia stars {math.hypot(ee[i], nn[i]):.1f}\" and {math.hypot(ee[j], nn[j]):.1f}\" away on "
                                         f"opposite sides: an unresolved blend measured as its own source.")
                                break
                        else:
                            continue
                        break
    return W


def build_packet(mv, photos: pd.DataFrame, out_png: str, mode: str = "full", ctx: dict | None = None, pool=None,
                 session: bool = True) -> dict:
    """One PNG + text for a mover row (movers.parquet). ctx: refcat, pred, fixed, grade, why (full mode).
    Rows: 1 detections; 2 detection minus the median of other times; 3 the same spot at other times."""
    gra, gdec = grid(mv.ra, mv.dec)
    pidx = photos.set_index(["obs_id", "detector"])
    dots = mover_dots(mv)
    t0 = dots[0]["mjd"]
    keys = list(dict.fromkeys((d["obs"], d["det"]) for d in dots))
    if len(keys) > MAX_DET_PANELS:
        keys = [keys[i] for i in np.linspace(0, len(keys) - 1, MAX_DET_PANELS).round().astype(int)]
    jobs = [("det", k, pidx.loc[k, "url"], [(d["x"], d["y"]) for d in dots if (d["obs"], d["det"]) == k]) for k in keys]
    dets = list(dict.fromkeys([int(mv.det)] + [k[1] for k in keys]))
    refs_by = {d: pick_refs(photos, mv, d) for d in dets}
    for d in dets:
        jobs += [("ref", (r.obs_id, d), pidx.loc[(r.obs_id, d), "url"], []) for r in refs_by[d].itertuples()]
    run = pool.map if pool is not None else map
    stamps = list(run(lambda j: read_stamp(j[2], gra, gdec, j[3]), jobs))
    for j, s in zip(jobs, stamps):
        if j[0] == "det" and "dots" in s:
            for d, (ra, dec) in zip([d for d in dots if (d["obs"], d["det"]) == j[1]], s["dots"]):
                d["ra"], d["dec"] = ra, dec
    for d in dots:
        if "ra" in d:
            d["gx"], d["gy"] = to_grid(mv.ra, mv.dec, d["ra"], d["dec"])
    det_p = [(j, s) for j, s in zip(jobs, stamps) if j[0] == "det"]

    def usable(s):
        return np.isfinite(s["data"][C0 - 5:C0 + 6, C0 - 5:C0 + 6]).mean() >= 0.7

    ref_all = []
    for d in dets:
        vis = refs_by[d].set_index("obs_id")["visit"] if len(refs_by[d]) else {}
        ok = [(j, s) for j, s in zip(jobs, stamps) if j[0] == "ref" and j[1][1] == d and "data" in s and usable(s)]
        other = [js for js in ok if vis.get(js[0][1][0]) != mv.visit][:N_OTHER_VISIT]
        same = [js for js in ok if vis.get(js[0][1][0]) == mv.visit]
        ref_all += other + same[:N_SAME_VISIT + N_OTHER_VISIT - len(other)]
    ref_all.sort(key=lambda js: js[1].get("mjd", 0.0))
    ref_p = [(j, s) for j, s in ref_all if j[1][1] == int(mv.det)]
    errors = [s["error"] for s in stamps if "error" in s]

    dimg = [bgsub(s["data"]) if "data" in s else np.full((N, N), np.nan) for _, s in det_p]
    rimg = [bgsub(s["data"]) for _, s in ref_p]
    refmed_by = {}
    for d in dets:
        r_d = [bgsub(s["data"]) for j, s in ref_all if j[1][1] == d]
        refmed_by[d] = np.nanmedian(np.stack(r_d), axis=0) if len(r_d) >= 2 else None
    sigmas = [sig(a) for a in dimg + rimg]
    sigma = float(np.nanmedian(sigmas)) if np.isfinite(sigmas).any() else 1.0
    hi = stretch_hi(dimg + rimg, sigma)
    ncol = max(len(det_p), len(ref_p), 1)
    fig, axes = plt.subplots(3, ncol, figsize=(2.3 * ncol + 0.5, 7.6), squeeze=False)

    def marks(ax, own_key):
        for d in dots:
            if "gx" not in d:
                continue
            own = own_key is not None and (d["obs"], d["det"]) == own_key
            ticks(ax, d["gx"], d["gy"], "#ffd400" if own else "#33e0ff", 1.6 if own else 0.9, d["n"] if own else None,
                  1.0 if own else 0.8)

    allpts = [(d["gx"], d["gy"]) for d in dots if "gx" in d]
    measured = {"det": [], "dif": [], "ref": []}
    for ax in axes.ravel():
        ax.set_axis_off()
    for c, (((_, key, _, _), _s), a) in enumerate(zip(det_p, dimg)):
        mjd = [d["mjd"] for d in dots if (d["obs"], d["det"]) == key][0]
        n = ",".join(str(d["n"]) for d in dots if (d["obs"], d["det"]) == key)
        own_pts = [(d["gx"], d["gy"]) for d in dots if (d["obs"], d["det"]) == key and "gx" in d]
        v1 = ap_snr(a, sig(a), own_pts); measured["det"].append(v1)
        grey(axes[0, c], a, sigma, hi); marks(axes[0, c], key)
        axes[0, c].set_title(f"#{n}  {fmt_dt((mjd - t0) * 24)}  D{key[1]}\nat mark {fs(v1)}", fontsize=9)
        refmed = refmed_by.get(key[1])
        if refmed is not None:
            dif = a - refmed
            sd = sig(dif) if np.isfinite(sig(dif)) else sigma
            v2 = ap_snr(dif, sd, own_pts); measured["dif"].append(v2)
            diverging(axes[1, c], dif, sd); marks(axes[1, c], key)
            axes[1, c].set_title(f"#{n} minus other times\nat mark {fs(v2)}", fontsize=9)
        else:
            axes[1, c].text(0.5, 0.5, "no reference\nexposures found", ha="center", va="center", fontsize=7)
    ref_dt = []
    for c, ((_, key, _, _), s) in enumerate(ref_p):
        a = rimg[c]
        v3 = ap_snr(a, sig(a), allpts); measured["ref"].append(v3)
        grey(axes[2, c], a, sigma, hi); marks(axes[2, c], None)
        dt_h = (s.get("mjd", np.nan) - t0) * 24
        ref_dt.append(dt_h)
        same = refs_by[int(mv.det)].set_index("obs_id").loc[key[0], "visit"] == mv.visit
        axes[2, c].set_title(f"{fmt_dt(dt_h)}  {'same visit' if same else 'other visit'}\nat marks {fs(v3)}", fontsize=9)
    fig.text(0.003, 0.83, "1 DETECTIONS", rotation=90, fontsize=8, va="center")
    fig.text(0.003, 0.50, "2 DETECTION − OTHER TIMES", rotation=90, fontsize=8, va="center")
    fig.text(0.003, 0.17, "3 SAME SPOT, OTHER TIMES", rotation=90, fontsize=8, va="center")
    fig.subplots_adjust(left=0.035, right=0.995, top=0.93, bottom=0.01, wspace=0.06, hspace=0.28)
    fig.savefig(out_png, dpi=100, facecolor="white")
    plt.close(fig)
    rows = []
    if session:
        try:
            rows = session_rows(mv, photos, pool)
        except Exception as e:  # noqa: BLE001
            errors.append(f"session rows: {e!r}"[:200])
    for d in dots:
        d["um"] = wavelength_um(photos, d["det"], d["x"], d["y"])
    return {"text": packet_text(mv, dots, ref_dt, mode, ctx, measured, keys, rows), "measured": measured,
            "numbers": numbers_verdict(measured), "errors": errors, "dots": dots, "session_rows": rows,
            "n_det_panels": len(det_p), "n_ref_panels": len(ref_p)}


def packet_text(mv, dots, ref_dt, mode, ctx, measured, keys, rows=None) -> str:
    span_h = (dots[-1]["mjd"] - dots[0]["mjd"]) * 24
    L = []
    dl = sorted({d["det"] for d in dots})
    L.append(f"{int(mv.n_dots)} detections in {int(mv.n_exposures)} exposures over {span_h * 60:.1f} minutes (one observing session), "
             f"detector(s) {', '.join(f'D{x}' for x in dl)}. Row 3 shows detector D{int(mv.det)}.")
    hidden = [d["n"] for d in dots if (d["obs"], d["det"]) not in set(keys)]
    if hidden:
        L.append(f"Detections {', '.join(map(str, hidden))} are listed below but not shown in the image (at most {MAX_DET_PANELS} panels).")
    rate = _g(mv, "rate_arcsec_h")
    if np.isfinite(rate):
        L.append(f"Claimed motion: {rate:.1f}\"/h toward position angle {_g(mv, 'pa_deg'):.0f}° (east of north), i.e. "
                 f"{rate * span_h:.1f}\" = {rate * span_h / SCALE:.2f} pixels between the first and last detection; "
                 f"trail within one 113 s exposure {rate * 113 / 3600:.2f}\".")
    else:
        L.append("Claimed motion: no rate fitted (detections at one moment in two detectors).")
    ex = {}
    for d in dots:
        if "ra" in d:
            ex.setdefault(round(d["mjd"] * 1440, 1), []).append(d)
    if len(ex) >= 2:
        k = sorted(ex)

        def mean_pos(g):
            e, n = offsets_arcsec(mv.ra, mv.dec, [d["ra"] for d in g], [d["dec"] for d in g])
            s = math.sqrt(sum(float(dot_sigma_arcsec(d["snr"])) ** 2 for d in g)) / len(g)
            return float(np.mean(e)), float(np.mean(n)), s
        e1, n1, s1 = mean_pos(ex[k[0]]); e2, n2, s2 = mean_pos(ex[k[-1]])
        moved = math.hypot(e2 - e1, n2 - n1)
        measured["_motion_sigma"] = moved / math.hypot(s1, s2)
        L.append(f"Position change, first to last exposure: {moved:.1f}\" = {moved / SCALE:.2f} px; that is {moved / math.hypot(s1, s2):.1f} times its "
                 f"uncertainty (below ~3 the change could be noise; a slow real asteroid can also be below 3 over a few minutes).")
    if np.isfinite(_g(mv, "line_resid_arcsec")):
        L.append(f"Largest distance of a detection from the fitted straight line: {_g(mv, 'line_resid_arcsec'):.1f}\".")
    if np.isfinite(_g(mv, "flux_ratio")):
        L.append(f"Brightness spread within one detector: max/min flux {_g(mv, 'flux_ratio'):.2f}.")
    if np.isfinite(_g(mv, "cover_exposures")) and _g(mv, "cover_exposures") > 0:
        L.append(f"Exposures of this session where the engine expected the track to be detectable: {int(_g(mv, 'cover_exposures'))}; "
                 f"of these, with no detection: {int(_g(mv, 'missed_exposures'))}.")
    if np.isfinite(_g(mv, "cover_sessions")):
        L.append(f"Sessions that imaged this spot in total: {int(_g(mv, 'cover_sessions'))}.")
    L.append("")
    L.append("Detections (numbers match the image). size = sqrt(a*b) in arcsec (stars: about 3-4\" at good S/N; the PSF FWHM is 7.5\" = 1.2 px):")
    L.append(" #  time      det  λ µm   pixel x,y in its photo   offset from centre E\",N\"   flux µJy   S/N   size \"  PSF-fit χ²  pixel flags at the peak")
    for d in dots:
        off = ""
        if "ra" in d:
            e, n = offsets_arcsec(mv.ra, mv.dec, d["ra"], d["dec"])
            off = f"{float(e):+6.1f},{float(n):+6.1f}"
        L.append(f"{d['n']:2d}  {fmt_dt((d['mjd'] - dots[0]['mjd']) * 24):>9s}  D{d['det']}  {d.get('um', float('nan')):5.3f}  {d['x']:7.1f},{d['y']:7.1f}"
                 f"          {off:>15s}        {d['flux']:8.0f}  {d['snr']:5.1f}  {d['size']:5.2f}   {d['chi2']:7.2f}     "
                 f"{', '.join(flag_names(d['fpk'])) or '-'}")
    if rows:
        L.append("")
        L.append("Every exposure of this session over the spot (S/N in a 1.5 px aperture at the fitted track position; λ = wavelength there):")
        L.append("  time       det  λ µm   engine detection     S/N at track   pixel x,y of the spot")
        for r in rows:
            d = r["dot"]
            L.append(f"  {fmt_dt((r['mjd'] - dots[0]['mjd']) * 24):>9s}  D{r['det']}  {r['um']:5.3f}  "
                     f"{('#' + str(d['n']) + ' S/N ' + format(d['snr'], '.0f')) if d else 'none':18s}  {fs(r['snr']):>8s}       "
                     f"{r['px'][0]:7.1f},{r['px'][1]:7.1f}{'  READ ERROR' if r.get('error') else ''}")
    L.append("")
    f = lambda vs: ", ".join("masked" if not np.isfinite(v) else f"{v:+.0f}" for v in vs)  # noqa: E731
    L.append("Measured at the marks (aperture S/N, radius 1.5 px = 9\"; also in each panel title):")
    L.append("  row 1 detections: " + f(measured["det"]))
    L.append("  row 2 detection - other times: " + (f(measured["dif"]) or "no reference"))
    L.append("  row 3 other times: " + (f(measured["ref"]) or "none") + "   (times: " + ", ".join(fmt_dt(x) for x in ref_dt) + ")")
    L.append(f"  numeric rule on these: {numbers_verdict(measured)}  (static = a source is there at other times and the difference keeps "
             f"< half the signal; mover = difference keeps >= half and other times are empty; the rule cannot see artifacts: "
             f"in round 1 every candidate it called mover was a hot pixel, a streak or a glow)")
    W = warnings_for(mv, dots, rows or [], measured, ctx if mode == "full" else None)
    L.append("")
    if any(w.split(":")[0] in ("HOT PIXEL", "SAME DETECTOR PIXEL", "NOT STAR-LIKE", "WHOLE-PIXEL POSITION", "SOURCE AT OTHER TIMES") for w in W):
        L.append("  (the numeric rule only compares brightness now and at other times: a warning below about the detector or a fixed "
                 "source overrules it)")
    L.append("AUTOMATIC WARNINGS: " + ("none: no automatic test fired, which is NOT a pass; look at session and stamps before deciding" if not W else ""))
    L += [f"  ! {w}" for w in W]
    if mode == "full" and ctx:
        L.append("")
        L += context_lines(mv.ra, mv.dec, float(mv.mjd), int(mv.session) if "session" in mv else None, ctx)
    return "\n".join(L)


def context_lines(ra, dec, mjd, session, ctx) -> list[str]:
    """Catalogue and known-object context around a position (the short form used in packets)."""
    L = []
    if ctx.get("grade") and not HIDE_KNOWN:
        L.append(f"Engine grade: {ctx['grade']}. Engine notes: {ctx.get('why', '')}")
    ref = ctx.get("refcat")
    if ref is not None:
        g = ref[ref["cat"] == "gaia"]
        gra, gdec = move_to_epoch(g, mjd)
        sep = angsep(ra, dec, np.asarray(gra), np.asarray(gdec)) * 3600
        k = [i for i in np.argsort(sep)[:5] if sep[i] <= 30]
        e, n = offsets_arcsec(ra, dec, np.asarray(gra)[k], np.asarray(gdec)[k]) if k else ([], [])
        rows = [f"{sep[i]:.1f}\" (E{e[j]:+.0f}\" N{n[j]:+.0f}\", G={g.g_mag.iloc[i]:.1f})" for j, i in enumerate(k)]
        L.append("Gaia stars within 30\" of the centre (moved to this date): " + (", ".join(rows) or "none"))
    fx = ctx.get("fixed")
    if fx is not None:
        sep = angsep(ra, dec, fx.ra.to_numpy(float), fx.dec.to_numpy(float)) * 3600
        k = [i for i in np.argsort(sep)[:3] if sep[i] <= 30]
        rows = [f"{sep[i]:.1f}\" (S/N {fx.snr.iloc[i]:.0f}, seen in {int(fx.n_sessions.iloc[i])} sessions)" for i in k]
        L.append("Our own catalogue of fixed sources within 30\": " + (", ".join(rows) or "none"))
    pred = None if HIDE_KNOWN else ctx.get("pred")
    if pred is not None and session is not None:
        L.append("Known asteroids, comets, planets and moons predicted within 10' at this time: " +
                 ("; ".join(f"{r['name']} ({r['class']}, V={r['v']:.1f}, {r['rate']:.0f}\"/h PA {r['pa']:.0f}°) {r['sep']:.0f}\" away"
                            for r in known_near(pred, ra, dec, mjd, session)[:3]) or "none"))
    return L


_S3 = None
AW_COLS = ["designation", "ra", "dec", "w1mpro", "w1sigmpro", "w2mpro", "w2sigmpro", "w3mpro", "j_m_2mass", "h_m_2mass",
           "k_m_2mass", "r_2mass", "pa_2mass", "ext_flg", "cc_flags"]


def allwise_near(ra: float, dec: float, radius_arcsec: float) -> pd.DataFrame | None:
    """AllWISE sources (epoch about 2010.5, Vega magnitudes, with their 2MASS match within 3") around a position, from
    NASA's public S3 copy; each sky tile (1.8 deg) is cached in <data>/_allwise/. None when S3 cannot be read."""
    global _S3
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq
    from skyshift_pipeline import config as C
    from skyshift_pipeline.suspects import allwise_key, allwise_pixels
    try:
        parts = []
        for pix in allwise_pixels(np.array([ra]), np.array([dec]), radius_arcsec):
            p = DATA / "_allwise" / f"p{int(pix):05d}.parquet"
            if not p.exists():
                if _S3 is None:
                    _S3 = pafs.S3FileSystem(anonymous=True, region=C.ALLWISE_S3_REGION, connect_timeout=60, request_timeout=120)
                t = pq.read_table(f"{C.ALLWISE_S3_BUCKET}/{allwise_key(int(pix))}", filesystem=_S3, columns=AW_COLS)
                p.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(t, p)
            parts.append(pd.read_parquet(p))
        c = pd.concat(parts, ignore_index=True)
    except Exception:  # noqa: BLE001
        return None
    c = c[np.abs(c.dec - dec) < radius_arcsec / 3600.0]
    sep = angsep(ra, dec, c.ra.to_numpy(float), c.dec.to_numpy(float)) * 3600
    return c.assign(sep=sep)[sep <= radius_arcsec].sort_values("sep")


def gaia_offset_test(zone: str, ra: float, dec: float, mjd: float, pmra: float, pmdec: float) -> str:
    """For a claimed proper motion (mas/yr): is there a Gaia DR3 star where this one was in 2016.0? Compares the offset
    from the nearest Gaia 2016 position with motion x years (round 1: 8 fast stars confirmed this way; their Gaia rows
    have no proper motion, which is why the engine's Gaia match by position and motion failed)."""
    ref = table(zone, "refcat")
    if ref is None:
        return "Gaia offset test: NOT AVAILABLE (no local reference catalogue)."
    g = ref[(ref["cat"] == "gaia") & (np.abs(ref.dec - dec) < 60 / 3600.0)]
    pm = math.hypot(pmra, pmdec) / 1000.0
    yrs = (mjd - 57388.5) / 365.25
    if not len(g) or pm <= 0:
        return f"Gaia offset test: no Gaia star within 60\" (a star moving {pm:.2f}\"/yr was {pm * yrs:.1f}\" away in 2016.0)."
    sep = angsep(ra, dec, g.ra.to_numpy(float), g.dec.to_numpy(float)) * 3600
    out = []
    for j in np.argsort(sep)[:3]:
        if sep[j] > max(30.0, 2.5 * pm * yrs):
            break
        q = g.iloc[int(j)]
        e, n = offsets_arcsec(q.ra, q.dec, ra, dec)  # from the Gaia 2016.0 position to ours
        size, opa = float(math.hypot(e, n)), float(math.degrees(math.atan2(e, n)) % 360)
        pa = math.degrees(math.atan2(pmra, pmdec)) % 360
        has = "no proper motion in Gaia DR3 (position-only solution)" if pd.isna(q.pmra) else f"Gaia pm ({q.pmra:+.0f}, {q.pmdec:+.0f}) mas/yr"
        ratio, dpa = size / yrs / pm, (opa - pa + 180) % 360 - 180
        if 0.7 <= ratio <= 1.4 and abs(dpa) <= 25:
            word = "MATCH: this is the star, 2016 position"
        elif size < 1.0 and not pd.isna(q.pmra) and math.hypot(q.pmra, q.pmdec) < 0.3 * pm * 1000:
            word = "REFUTES: a Gaia star with a small motion sits at our position"
        else:
            word = "unrelated star (wrong distance or direction): ignore"
        out.append(f"  {word}. Gaia G={q.g_mag:.1f}, {has}: our position is {size:.1f}\" from its 2016.0 position toward PA {opa:.0f}°; "
                   f"that is {size / yrs:.2f}\"/yr over {yrs:.1f} yr. Claimed {pm:.2f}\"/yr toward PA {pa:.0f}°: "
                   f"size ratio {ratio:.2f}, direction difference {dpa:+.0f}°.")
    cpm = common_pm(ref, ra, dec, pmra, pmdec)
    if not out:
        return (f"Gaia offset test: no Gaia star within {max(30.0, 2.5 * pm * yrs):.0f}\" (a star moving {pm:.2f}\"/yr was {pm * yrs:.1f}\" away "
                f"in 2016.0). Gaia cannot test this claim: very red or faint objects are missing from Gaia. Use catalog with pm for the "
                f"AllWISE 2010 and 2MASS 1999 epochs." + ("\n" + cpm if cpm else ""))
    if not any("MATCH" in o or "REFUTES" in o for o in out):
        out.append("  No Gaia star matches or refutes the claim: Gaia cannot test it. Use catalog with pm for the AllWISE and 2MASS epochs.")
    return ("Gaia offset test (a real fast star sits motion x years from its Gaia 2016.0 position: ratio near 1 and direction "
            "difference near 0 confirm the motion; a Gaia star AT our position with a small motion refutes it):\n" + "\n".join(out)
            + ("\n" + cpm if cpm else ""))


def common_pm(ref: pd.DataFrame, ra: float, dec: float, pmra: float, pmdec: float, radius_arcsec: float = 900.0) -> str:
    """A Gaia star within 15' that moves like the claim (a wide companion's primary): strong evidence that the motion is real."""
    pm = math.hypot(pmra, pmdec)
    if pm < 100:
        return ""
    g = ref[(ref["cat"] == "gaia") & (np.abs(ref.dec - dec) < radius_arcsec / 3600.0) & ref.pmra.notna()]
    g = g[np.hypot(g.pmra - pmra, g.pmdec - pmdec) < max(30.0, 0.08 * pm)]
    if not len(g):
        return ""
    sep = angsep(ra, dec, g.ra.to_numpy(float), g.dec.to_numpy(float)) * 3600
    k = [i for i in np.argsort(sep) if 3 < sep[i] <= radius_arcsec][:2]
    if not k:
        return ""
    return "COMMON PROPER MOTION: " + "; ".join(
        f"Gaia G={g.g_mag.iloc[i]:.1f} star {sep[i]:.0f}\" away moves ({g.pmra.iloc[i]:+.0f}, {g.pmdec.iloc[i]:+.0f}) mas/yr, the claim is "
        f"({pmra:+.0f}, {pmdec:+.0f})" for i in k) + ": a star with the same motion this close is almost never chance; the finding is its companion."


def known_near(pred: pd.DataFrame, ra, dec, mjd, session, radius_arcsec: float = 600.0) -> list[dict]:
    """Known bodies of that session moved to mjd, within the radius, nearest first, one row per body."""
    p = pred[pred.session == session]
    if p.empty:
        return []
    dt = (mjd - p.mjd.to_numpy(float)) * 24
    r2, d2 = offset_radec(p.ra.to_numpy(float), p.dec.to_numpy(float), p.dra_arcsec_h.to_numpy(float) * dt,
                          p.ddec_arcsec_h.to_numpy(float) * dt)
    sep = angsep(ra, dec, r2, d2) * 3600
    out, seen = [], set()
    for i in np.argsort(sep):
        if sep[i] > radius_arcsec:
            break
        nm = p.name.iloc[i]
        if nm in seen:
            continue
        seen.add(nm)
        vx, vy = float(p.dra_arcsec_h.iloc[i]), float(p.ddec_arcsec_h.iloc[i])
        out.append({"name": nm, "class": p["class"].iloc[i], "v": float(p.v.iloc[i]), "sep": float(sep[i]),
                    "rate": math.hypot(vx, vy), "pa": math.degrees(math.atan2(vx, vy)) % 360.0,
                    "ra": float(r2[i]), "dec": float(d2[i])})
    return out
