"""Pixel checks of candidate movers: what the result tables can't tell, measured in NASA's photos (agent "pixel").

Reads any candidates.json (contracts/api.md v3.23), measures every open mover in the photos and writes
<root>/checks/pixel.json, the check-producer format the exporter merges (`--checks-dir`):
  {"source": "pixel", "made", "candidates_file", "candidates": {id: {"checks": [...], "status", "known_name": null}}}

Checks (contract v3.23 codes; `hot_pixel`, `hot_pixel_proven`, `persistence_proven` and `one_photo` are proposed):
- partner_seen (ok) / one_detector (warn; strong when it is seen in D4 alone): forced photometry at its place in the
  photo the simultaneous partner detector took (D1+D4, D2+D5, D3+D6), against the known-asteroid colour range
  (core pixelcheck.KNOWN_RATIO). Never a cut: comet 306P is D6-only and very red.
- faint_on_path (ok found / warn absent): forced photometry where its straight-line path predicts it in the other
  exposures of its detector (other sessions, and its own session's exposures without a dot), only where the path's
  1σ error is under PATH_MAX_SIGMA_PX; a small search within 2σ, calibrated by the same search at random places.
- hot_pixel (strong, status flaw: every dot within ±1 px of one detector pixel while the sky moved; contract rule) /
  hot_pixel_proven (the dot's pixel is as bright in the exposures just before and after, which look at other sky).
- persistence (warn) / persistence_proven (strong): the same detector pixel saturated or ≥ 30× brighter in the
  exposures of that detector up to 30 min before (any field: found through the S3 listing).
- sharp: each dot's √(a·b) (the pipeline's dot size) against the stars of similar S/N in the same rows of its photo.
- one_photo: forced flux at each dot's sky place in the other exposures of its detector in the visit (other
  sessions): a steady faint source there (it may be a star just under the cut) or nothing (core pixelcheck.one_photo),
  with NASA's TRANSIENT bit next to the dot.

Pixels: small range reads of the public S3 copy (s3pixels.py): ~0.1 MB headers once per photo (cached), ~0.2–0.4 MB
of rows per measurement. Measurements are cached (JSON lines keyed by photo and pixel place), so a rerun on a new
export reads only what is new. Run from server/ (its environment has skyshift_core, pandas and astropy):

    cd server
    uv run python ../scripts/candidates/pixel_check.py --candidates ../web/public/data/candidates.json
    uv run python ../scripts/candidates/pixel_check.py --validate      # hit rates on known asteroids and junk

Selection (default `tocheck`): movers with status open (or none) that the site lists under "To check"
(web/src/discover/filters.ts setAsideOf: not grade C, and not motion_not_significant unless grade A). `open`: every
open mover. Results tables come from <root>/results/<zone>/suspects.parquet and photo lists from
<root>/photolists/zones/<zone>.parquet (bucket layout; `--fetch` copies missing ones with gsutil -n, logged).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
import threading
import warnings
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s3pixels as S3  # noqa: E402
from skyshift_core import detect  # noqa: E402
from skyshift_core import pixelcheck as P  # noqa: E402
from skyshift_core.flags import PERSIST  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("SKYSHIFT_DATA", Path.home() / "skyshift-data"))
GSUTIL = Path.home() / "google-cloud-sdk" / "bin" / "gsutil"

FWHM_ARCSEC = 7.53
STRIP_OWN = 16  # rows each side of an own dot: forced photometry, shape, and the stars to compare the shape with
STRIP = 8  # rows each side for a forced measurement (the 7 px annulus fits)
MAX_STRIP_ROWS = 64  # targets of one photo further apart than this are read as separate strips
COL_PAD = 72  # columns each side of a forced measurement (room for the random apertures of the empirical noise)
SEARCH_COL_PAD = 150  # more for a search (its chance windows)
N_STATIC = 6  # other exposures per (mover, detector) for the one-photo test, nearest in time, one per session first
STATIC_MIN_DT_H = 1.0  # only other sessions ...
STATIC_MIN_MOVE_ARCSEC = 15.0  # ... where the mover has left its place (rate × Δt), when its rate is known
PATH_MAX_H = 12.0  # path predictions up to this far in time
PATH_MAX_SIGMA_PX = 3.0  # ... while the prediction's 1σ error stays under 3 px (18″)
PATH_CHANCE_WINDOWS = 30  # random windows that calibrate a search's chance level (their 95th percentile S/N)
POS_FLOOR_ARCSEC = 0.43  # a single dot's position error floor against Gaia (CLAUDE.md), and ...
POS_K = 1.6 * 0.6  # ... 0.6 × FWHM / S/N for a centroid, × 1.6 (pos_err is 1.4–1.8× too small)
HISTORY_BEFORE = 2  # exposures of the same detector just before a dot (persistence) ...
HISTORY_AFTER = 1  # ... and just after (a hot pixel glows in both)
HOT_SKY_MOVED_PX = 4.0  # a neighbour photo tests the pixel only if the sky under it moved ≥ 4 px (25″)
HOT_FLUX_RANGE = (0.5, 2.5)  # a hot pixel glows about as much as the dot it made; 10× more is a star there
HOT_MIN_PHOTOS = 2  # ... in every one of at least this many neighbour photos (one before and one after at least)
DETECT_SNR = 5.0  # the dot catalogue's cut: a fixed source above it in one photo is a star, not "just under the cut"
STATIC_GUARD_RATIO = 0.5  # partner_seen is not claimed when the partner sees ≥ 0.5 × that light there at other times
CONTAM_SNR = -4.0  # a partner flux below −4σ: a star's wing or a masked core in the annulus, not a measurement
SHARP_STRONG = 0.55  # every dot under this × the stars: strong (validation: no known asteroid dot below it)
TRAIL_MIN_STRETCH = 1.25  # trail: only where a 113 s exposure stretches a point at least this much
EXPOSURE_S = 113.0
PSF_SIGMA_ARCSEC = 3.2  # the injection model's PSF sigma (pipeline inject: elong × √(1 + L²/12σ²))
SHARP_OK = 0.8  # every dot 0.8–1.3 × the stars: a point of light, ok
WIDE_RATIO = 1.3  # wide: dots at least this × the stars of their S/N (known asteroids: 95% of dots below; stars copied
#                   into the photos at S/N 6: median 1.03, q75 1.11). Every dot (≥ 2): strong; the median dot: warn
FIXED_CLEAR_ARCSEC = 20.0  # faint_on_path ignores predicted places this close to a fixed source found at a dot's place
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
LEVEL_ORDER = {"strong": 0, "warn": 1, "ok": 2}


# ---------------------------------------------------------------------------------------------------------------
# small helpers

def when(mjd: float) -> str:
    """'12 Dec 2025, 04:27 UTC' (the Viewer's plate date)."""
    d = dt.datetime(1858, 11, 17, tzinfo=dt.timezone.utc) + dt.timedelta(days=float(mjd))
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}, {d:%H:%M} UTC"


def hhmm(mjd: float) -> str:
    d = dt.datetime(1858, 11, 17, tzinfo=dt.timezone.utc) + dt.timedelta(days=float(mjd))
    return f"{d:%H:%M}"


def fnum(x: float, nd: int = 0) -> str:
    """1,795 / 0.4 / −12: plain numbers for texts."""
    if x is None or not np.isfinite(x):
        return "?"
    s = f"{x:,.{nd}f}"
    return s.replace("-", "−")


def ratio_txt(x: float) -> str:
    return fnum(x, 2) if abs(x) < 10 else fnum(x, 1)


def rnd(x, nd=1):
    """JSON number or None."""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return round(xf, nd) if np.isfinite(xf) else None


def _count_of(k: int, n: int, noun: str) -> str:
    """'Its dot', 'Both its dots', 'All 3 of its dots', '2 of its 3 dots' (the subject of a sentence)."""
    if k == n:
        return f"Its {noun}" if n == 1 else f"Both its {noun}s" if n == 2 else f"All {n} of its {noun}s"
    return f"{k} of its {n} {noun}s"


def check(code: str, level: str, text: str, look: str, values: dict | None) -> dict:
    vals = None if values is None else {k: (rnd(v, 3) if isinstance(v, float) else v) for k, v in values.items()}
    return {"code": code, "level": level, "text": text, "look": look, "values": vals}


def tangent(ra, dec, ra0, dec0):
    """(east, north) offsets in arcsec of (ra, dec) from (ra0, dec0) (small fields)."""
    dra = (np.asarray(ra) - ra0 + 180.0) % 360.0 - 180.0
    return dra * np.cos(np.radians(dec0)) * 3600.0, (np.asarray(dec) - dec0) * 3600.0


def untangent(e, n, ra0, dec0):
    return ra0 + np.asarray(e) / 3600.0 / np.cos(np.radians(dec0)), dec0 + np.asarray(n) / 3600.0


def in_polygon(s_region: str | None, ra: float, dec: float) -> bool:
    """Is (ra, dec) inside the footprint 'POLYGON ICRS ra dec ...' (tangent plane at the point)? True if unknown."""
    if not s_region or not isinstance(s_region, str):
        return True
    try:
        v = np.array(s_region.split()[2:], float).reshape(-1, 2)
    except ValueError:
        return True
    x, y = tangent(v[:, 0], v[:, 1], ra, dec)
    inside = False
    n = len(x)
    for i in range(n):
        j = (i - 1) % n
        if (y[i] > 0) != (y[j] > 0) and 0 < (x[j] - x[i]) * (0 - y[i]) / (y[j] - y[i]) + x[i]:
            inside = not inside
    return inside


def pos_err(snr: float) -> float:
    return float(math.hypot(POS_FLOOR_ARCSEC, POS_K * FWHM_ARCSEC / max(float(snr), 1.0)))


def motion_fit(t_h: np.ndarray, e: np.ndarray, n: np.ndarray, sig: np.ndarray):
    """Weighted straight line per axis: (p0_e, v_e, p0_n, v_n, cov 2×2 of (p0, v)) with t in hours from the mean.
    None with fewer than two distinct times."""
    if np.ptp(t_h) < 1.0 / 60.0:
        return None
    w = 1.0 / sig ** 2
    A = np.stack([np.ones_like(t_h), t_h], axis=1)
    N = A.T @ (A * w[:, None])
    cov = np.linalg.inv(N)
    pe = cov @ (A.T @ (w * e))
    pn = cov @ (A.T @ (w * n))
    return pe, pn, cov


# ---------------------------------------------------------------------------------------------------------------
# inputs

def select_movers(doc: dict, select: str) -> list[dict]:
    out = []
    for c in doc["candidates"]:
        if c.get("kind") != "mover" or (c.get("status") or "open") != "open":
            continue
        if select == "tocheck":
            g = c.get("grade")
            if g == "C" or (g != "A" and "motion_not_significant" in (c.get("reasons") or [])):
                continue
        out.append(c)
    return out


def log_download(what: str, size: str) -> None:
    line = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} pixel {what} {size}\n"
    with open(DATA_ROOT / "DOWNLOADS.log", "a") as f:
        f.write(line)


def fetch(root: Path, rel: str) -> bool:
    """gsutil cp -n gs://nasa-glazing-data/<rel> into root/<rel> (logged). True if the file is there afterwards."""
    dst = root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(GSUTIL), "-q", "cp", "-n", f"gs://nasa-glazing-data/{rel}", str(dst)], capture_output=True)
    if dst.exists():
        log_download(f"gs://nasa-glazing-data/{rel}", f"{dst.stat().st_size / 1e6:.2f} MB")
    return r.returncode == 0 and dst.exists()


class Tables:
    """Per-zone suspects rows and photo lists, read once."""

    def __init__(self, root: Path, allow_fetch: bool):
        self.root = root
        self.allow_fetch = allow_fetch
        self._rows: dict[str, pd.DataFrame] = {}
        self._pl: dict[str, pd.DataFrame] = {}

    def _path(self, rel: str) -> Path | None:
        p = self.root / rel
        if p.exists():
            return p
        if self.allow_fetch and fetch(self.root, rel):
            return p
        return None

    def rows(self, zone: str, table: str = "suspects") -> pd.DataFrame | None:
        k = f"{zone}/{table}"
        if k not in self._rows:
            p = self._path(f"results/{zone}/{table}.parquet")
            self._rows[k] = None if p is None else pd.read_parquet(p)
            if self._rows[k] is not None and "id" in self._rows[k]:
                self._rows[k] = self._rows[k].set_index("id", drop=False)
        return self._rows[k]

    def photos(self, zone: str) -> pd.DataFrame | None:
        if zone not in self._pl:
            p = self._path(f"photolists/zones/{zone}.parquet")
            pl = None
            if p is not None:
                pl = pd.read_parquet(p, columns=["obs_id", "detector", "url", "mjd", "session", "visit", "s_region",
                                                  "s3_key"])
                pl["obs_id"] = pl.obs_id.astype(str)
                pl["detector"] = pl.detector.astype(int)
            self._pl[zone] = pl
        return self._pl[zone]


def dots_of(row) -> list[dict]:
    """The mover's dots, time order, from a suspects/movers row's dot lists."""
    n = len(row["dot_x"])

    def col(name, default=np.nan):
        v = row.get(name) if hasattr(row, "get") else row[name]
        return list(v) if v is not None and len(v) == n else [default] * n

    out = []
    for o, d, x, y, t, f, s, z, fp in zip(col("dot_obs"), col("dot_det"), col("dot_x"), col("dot_y"), col("dot_mjd"),
                                          col("dot_flux"), col("dot_snr"), col("dot_size"), col("dot_fpk", 0)):
        out.append(dict(obs=str(o), det=int(d), x=float(x), y=float(y), mjd=float(t), flux=float(f), snr=float(s),
                        size=float(z), fpk=int(fp) & 0xFFFFFFFF))
    return sorted(out, key=lambda q: (q["mjd"], q["det"]))


# ---------------------------------------------------------------------------------------------------------------
# measurements

class Cache:
    """Measurement results, one JSON line each: {"k": key, "v": result}. Thread-safe append."""

    def __init__(self, path: Path):
        self.path = path
        self.mem: dict[str, dict] = {}
        self.lock = threading.Lock()
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    j = json.loads(line)
                    self.mem[j["k"]] = j["v"]
                except (ValueError, KeyError):
                    pass

    def get(self, k: str) -> dict | None:
        return self.mem.get(k)

    def put(self, k: str, v: dict) -> None:
        with self.lock:
            self.mem[k] = v
            with open(self.path, "a") as f:
                f.write(json.dumps({"k": k, "v": v}, default=_json_default) + "\n")


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def _clean(d: dict) -> dict:
    return json.loads(json.dumps(d, default=_json_default).replace("NaN", "null"))


MODE_VERSION = {"own": "own", "forced": "forced2", "pix": "pix", "search": "search"}  # bump to re-measure a mode


def mkey(url: str, mode: str, x: float, y: float, rho: float = 0.0) -> str:
    return f"{S3.Reader.key(url)}|{MODE_VERSION[mode]}|{x:.2f}|{y:.2f}|{rho:.2f}"


def measure_strip(data: np.ndarray, flg: np.ndarray, y0: int, targets: list[tuple], seed: int, x0: int = 0
                  ) -> dict[str, dict]:
    """Every target (key, mode, x, y, rho) of one strip whose first row is y0 and first column x0 (frame pixels)."""
    out: dict[str, dict] = {}
    pts = [(t[2] - x0, t[3] - y0) for t in targets]
    forced = P.forced_flux(data, [p[0] for p in pts], [p[1] for p in pts], flags=flg, seed=seed)
    stars = None
    for (key, mode, x, y, rho), (xs, ys), fo in zip(targets, pts, forced):
        res = dict(flux=fo.flux_ujy, err=fo.err_ujy, snr=fo.snr, masked=fo.masked, n_noise=fo.n_noise,
                   zero=fo.zero_ujy, dx=fo.dx, dy=fo.dy)
        if mode == "own":
            size, el, th = P.moment_shape(data, [xs], [ys], flags=flg)
            res.update(size_arcsec=float(size[0] * P.PIXEL_ARCSEC), elong=float(el[0]), theta=float(th[0]),
                       transient_px=P.transient_pixels(flg, xs, ys), sat=P.saturated_near(flg, xs, ys))
            if stars is None:
                stars = _stars(data, flg, seed)
            rat, n = P.size_ratio(float(size[0]), fo.snr, stars[0], stars[1])
            if not np.isfinite(rat):  # few stars at that S/N in these rows: a wider S/N window
                rat, n = P.size_ratio(float(size[0]), fo.snr, stars[0], stars[1], factor=4.0)
            res.update(size_ratio=rat, n_stars=n)
        elif mode == "pix":
            yy, xx = np.ogrid[:flg.shape[0], :flg.shape[1]]
            near = (xx - xs) ** 2 + (yy - ys) ** 2 <= P.PERSIST_R_PX ** 2
            res.update(sat=P.saturated_near(flg, xs, ys), persist=bool(((flg[near] & PERSIST) != 0).any()),
                       peak=float(np.nanmax(np.where(near, data, -np.inf))) * P.UJY_PER_MJYSR)
        elif mode == "search":
            res.update(_search(data, flg, xs, ys, rho, seed))
        out[key] = _clean(res)
    return out


def _stars(data, flg, seed):
    """(size px, forced S/N) of the point sources detected in a strip: the stars to compare a dot's shape with.
    Sources with a mask bit in the core, OVERFLOW/NONLINEAR, or elongation > 2 (blends) are left out."""
    try:
        det = detect(data, flg, thresh=3.0, minarea=3, box=32)
    except Exception:  # noqa: BLE001 - sep on an odd strip
        return np.zeros(0), np.zeros(0)
    if len(det) == 0:
        return np.zeros(0), np.zeros(0)
    fo = P.forced_flux(data, det["x"], det["y"], flags=flg, seed=seed + 1)
    size, el, _ = P.moment_shape(data, det["x"], det["y"], flags=flg)
    snr = np.array([f.snr for f in fo])
    ok = np.array([not f.masked for f in fo]) & (el <= 2.0)
    ok &= np.array([not P.saturated_near(flg, x, y) for x, y in zip(det["x"], det["y"])])
    return size[ok], snr[ok]


def _search(data, flg, x, y, rho, seed):
    """Best forced S/N on a 0.5 px grid within rho px of (x, y), and the 95th percentile of the same search at
    PATH_CHANCE_WINDOWS random centres of the strip (the chance level of that search here)."""
    def grid(cx, cy):
        k = np.arange(-rho, rho + 1e-9, 0.5)
        gx, gy = np.meshgrid(k, k)
        m = gx ** 2 + gy ** 2 <= rho ** 2 + 1e-9
        return cx + gx[m], cy + gy[m]

    gx, gy = grid(x, y)
    fo = P.forced_flux(data, gx, gy, flags=flg, seed=seed)
    snr = np.array([f.snr if not f.masked else np.nan for f in fo])
    if not np.isfinite(snr).any():
        return dict(best_snr=np.nan, best_flux=np.nan, best_dx=np.nan, best_dy=np.nan, chance95=np.nan)
    i = int(np.nanargmax(snr))
    err = fo[i].err_ujy
    rng = np.random.default_rng(seed + 7)
    ny, nx = data.shape
    mx = []
    for _ in range(PATH_CHANCE_WINDOWS):
        cx = rng.uniform(10 + rho, nx - 11 - rho)
        if abs(cx - x) < 3 * rho + 8:
            continue
        rx, ry = grid(cx, y)
        f2 = P.forced_flux(data, rx, ry, flags=flg, seed=seed)
        s2 = np.array([f.snr if not f.masked else np.nan for f in f2])
        if np.isfinite(s2).any():
            mx.append(np.nanmax(s2))
    return dict(best_snr=float(snr[i]), best_flux=float(fo[i].flux_ujy), best_err=float(err),
                best_dx=float(gx[i] - x), best_dy=float(gy[i] - y),
                chance95=float(np.percentile(mx, 95)) if len(mx) >= 10 else np.nan)


class Measurer:
    """Plans and runs the pixel reads: layouts first (headers → WCS, MJD), then strips grouped per photo."""

    def __init__(self, cache_dir: Path, workers: int):
        self.reader = S3.Reader(cache_dir)
        self.cache = Cache(cache_dir / "measure.jsonl")
        self.workers = workers
        self.errors: list[str] = []

    def layouts(self, urls) -> dict[str, S3.Layout | None]:
        urls = sorted(set(u for u in urls if u))

        def one(u):
            try:
                return u, self.reader.layout(u)
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"layout {S3.Reader.key(u)}: {e!r}"[:200])
                return u, None

        with ThreadPoolExecutor(self.workers) as ex:
            return dict(ex.map(one, urls))

    def run(self, targets: dict[str, list[tuple]]) -> dict[str, dict]:
        """targets: url -> [(key, mode, x, y, rho)]. Returns key -> result (cached ones without reading)."""
        todo = {}
        out = {}
        for u, ts in targets.items():
            need = []
            for t in ts:
                v = self.cache.get(t[0])
                if v is None:
                    need.append(t)
                else:
                    out[t[0]] = v
            if need:
                todo[u] = need
        jobs = []
        for u, ts in todo.items():
            ts = sorted(set(ts), key=lambda t: t[3])
            group: list[tuple] = []
            for t in ts:
                if group:
                    lo = min(q[3] - self._half(q) for q in group + [t])
                    hi = max(q[3] + self._half(q) for q in group + [t])
                    if hi - lo > MAX_STRIP_ROWS:
                        jobs.append((u, group))
                        group = []
                group.append(t)
            if group:
                jobs.append((u, group))

        def one(job):
            u, group = job
            lo = min(t[3] - self._half(t) for t in group)
            hi = max(t[3] + self._half(t) for t in group)
            yc = 0.5 * (lo + hi)
            half = int(math.ceil(0.5 * (hi - lo)))
            if any(t[1] == "own" for t in group):
                c0 = c1 = None  # full width: the stars to compare a dot's shape with
            else:
                pad = COL_PAD + (SEARCH_COL_PAD if any(t[1] == "search" for t in group) else 0)
                c0 = int(min(t[2] for t in group)) - pad
                c1 = int(max(t[2] for t in group)) + pad + 1
            try:
                data, flg, y0, x0 = self.reader.strip(u, yc, half, c0, c1)
                seed = zlib.crc32(S3.Reader.key(u).encode())
                res = measure_strip(data, flg, y0, group, seed, x0)
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"strip {S3.Reader.key(u)}: {e!r}"[:200])
                return {}
            for k, v in res.items():
                self.cache.put(k, v)
            return res

        done = 0
        with ThreadPoolExecutor(self.workers) as ex:
            for res in ex.map(one, jobs):
                out.update(res)
                done += 1
                if done % 200 == 0:
                    print(f"  strips {done}/{len(jobs)}, {S3.BYTES[0] / 1e6:.0f} MB read", flush=True)
        return out

    @staticmethod
    def _half(t) -> int:
        return STRIP_OWN if t[1] == "own" else STRIP + int(math.ceil(t[4]))


# ---------------------------------------------------------------------------------------------------------------
# planning per mover

class Mover:
    def __init__(self, cid: str, zone: str, row, cand: dict | None = None):
        self.id = cid
        self.zone = zone
        self.cand = cand or {}
        self.dots = dots_of(row)
        self.rate = _f(row.get("rate_arcsec_h") if hasattr(row, "get") else None)
        self.evidence = str(row.get("evidence", "")) if hasattr(row, "get") else ""
        self.plan: dict[str, list] = defaultdict(list)  # purpose -> [(url, key, info)]
        self.fit = None
        self.t0 = float(np.mean([d["mjd"] for d in self.dots])) if self.dots else np.nan


def _f(x) -> float:
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except (TypeError, ValueError):
        return np.nan


def neighbours(pl: pd.DataFrame, obs: str, det: int, own_key: str | None, reader: S3.Reader, before: int, after: int
               ) -> list[tuple[str, str, int]]:
    """URLs of the exposures of detector `det` just before / after `obs` in time (by observation and exposure
    number in the planning period, any field): [(url, obs_id, offset)] with offset −1, −2 … / +1."""
    p = S3.parse_obs(obs)
    if p is None or own_key is None:
        return []
    pp, seq, exp = p
    d = os.path.dirname(own_key)

    def exposures(sq: int) -> list[tuple[int, str]]:
        out = []
        for k in reader.listing(f"{d}/level2_{pp}_{sq:04d}_"):
            parts = os.path.basename(k).removeprefix("level2_").split("_")
            if len(parts) < 4:
                continue
            m = S3.OBS_RE.match("_".join(parts[:3]) + "_" + parts[3].split("D")[0])
            if m and int(m["seq"]) == sq and parts[3].endswith(f"D{det}"):
                out.append((int(m["exp"]), S3.S3_HTTPS + k))
        return sorted(out)

    res = []
    cur = exposures(seq)
    # before
    seq_b, list_b = seq, [x for x in cur if x[0] < exp]
    k = 0
    while k < before:
        if not list_b:
            seq_b -= 1
            if seq_b < 0 or seq - seq_b > 3:
                break
            list_b = exposures(seq_b)
            continue
        e, u = list_b.pop()
        k += 1
        res.append((u, f"{pp}_{seq_b:04d}_{e}", -k))
    seq_a, list_a = seq, [x for x in cur if x[0] > exp]
    k = 0
    while k < after:
        if not list_a:
            seq_a += 1
            if seq_a - seq > 3:
                break
            list_a = exposures(seq_a)
            continue
        e, u = list_a.pop(0)
        k += 1
        res.append((u, f"{pp}_{seq_a:04d}_{e}", k))
    return res


def plan_movers(movers: list[Mover], tables: Tables, meas: Measurer) -> None:
    """Fill each mover's dots with url, ra, dec; and its plan of measurements (targets keyed per photo)."""
    # 1. own photos -> WCS -> sky positions of the dots
    urlmap = {}
    for m in movers:
        pl = tables.photos(m.zone)
        if pl is None:
            continue
        key = {(o, d): (u, k) for o, d, u, k in zip(pl.obs_id, pl.detector, pl.url, pl.s3_key)}
        urlmap[m.zone] = key
        for d in m.dots:
            d["url"], d["s3_key"] = key.get((d["obs"], d["det"]), (None, None))
    lays = meas.layouts(d["url"] for m in movers for d in m.dots)
    for m in movers:
        for d in m.dots:
            lay = lays.get(d.get("url"))
            if lay is None:
                d["ra"] = d["dec"] = np.nan
                continue
            w = lay.wcs()
            d["ra"], d["dec"] = (float(v) for v in w.all_pix2world([[d["x"], d["y"]]], 0)[0])
            d["mjd_hdr"] = lay.mjd()
    # 2. other photos
    need_urls = set()
    for m in movers:
        pl = tables.photos(m.zone)
        if pl is None or not m.dots:
            continue
        good = [d for d in m.dots if np.isfinite(d.get("ra", np.nan))]
        if not good:
            continue
        ra0, dec0 = good[0]["ra"], good[0]["dec"]
        own = {(d["obs"], d["det"]) for d in m.dots}
        own_obs = {d["obs"] for d in m.dots}
        # motion fit (sky, arcsec, hours from t0)
        if len({round(d["mjd"], 6) for d in good}) >= 2:
            t = np.array([(d["mjd"] - m.t0) * 24 for d in good])
            e, n = tangent([d["ra"] for d in good], [d["dec"] for d in good], ra0, dec0)
            sig = np.array([pos_err(d["snr"]) for d in good])
            m.fit = motion_fit(t, e, n, sig)
            if m.fit is not None:
                m.rate_fit = float(math.hypot(m.fit[0][1], m.fit[1][1]))
        m.ref = (ra0, dec0)
        sess = pl.drop_duplicates(["obs_id", "detector"]).set_index(["obs_id", "detector"])
        own_rows = [sess.loc[(d["obs"], d["det"])] for d in m.dots if (d["obs"], d["det"]) in sess.index]
        own_sessions = {int(r["session"]) for r in own_rows}
        visit = int(own_rows[0]["visit"]) if own_rows else None
        # partner
        by_obs = defaultdict(set)
        for d in m.dots:
            by_obs[d["obs"]].add(d["det"])
        for d in good:
            pdet = P.PAIR[d["det"]]
            if pdet in by_obs[d["obs"]]:
                continue
            u = urlmap[m.zone].get((d["obs"], pdet), (None, None))[0]
            if u:
                m.plan["partner"].append(dict(url=u, ra=d["ra"], dec=d["dec"], dot=d, det=pdet))
                need_urls.add(u)
        # static (one-photo) and path, per detector of the mover
        for det in sorted({d["det"] for d in good}):
            dd = [d for d in good if d["det"] == det]
            mra = float(np.mean([d["ra"] for d in dd]))
            mdec = float(np.mean([d["dec"] for d in dd]))
            cand = pl[(pl.detector == det)]
            if visit is not None:
                cand = cand[cand.visit == visit]
            cand = cand[~cand.obs_id.isin(own_obs)]
            dt_h = (cand.mjd - m.t0) * 24.0
            rate = m.rate if np.isfinite(m.rate) else getattr(m, "rate_fit", np.nan)
            st = cand[(dt_h.abs() >= STATIC_MIN_DT_H) & ~cand.session.isin(own_sessions)].copy()
            if np.isfinite(rate):
                st = st[(rate * ((st.mjd - m.t0) * 24.0).abs()) >= STATIC_MIN_MOVE_ARCSEC]
            st["adt"] = ((st.mjd - m.t0) * 24.0).abs()
            st = st.sort_values("adt")
            st = st[[in_polygon(s, mra, mdec) for s in st.s_region]]
            first = st.drop_duplicates("session")
            pick = pd.concat([first, st.drop(first.index)]).head(N_STATIC)
            for r in pick.itertuples():
                for d in dd:
                    m.plan["static"].append(dict(url=r.url, ra=d["ra"], dec=d["dec"], dot=d, det=det, mjd=r.mjd))
                need_urls.add(r.url)
            # path: predicted places within PATH_MAX_H where the 1σ error stays under PATH_MAX_SIGMA_PX
            if m.fit is not None:
                pe, pn, cov = m.fit
                pth = cand.copy()
                own_sess_det = pl[(pl.detector == det) & pl.session.isin(own_sessions) & ~pl.obs_id.isin(own_obs)]
                pth = pd.concat([pth[((pth.mjd - m.t0) * 24.0).abs() <= PATH_MAX_H], own_sess_det]).drop_duplicates(
                    ["obs_id", "detector"])
                for r in pth.itertuples():
                    th = (r.mjd - m.t0) * 24.0
                    a = np.array([1.0, th])
                    s = math.sqrt(float(a @ cov @ a))
                    if s / P.PIXEL_ARCSEC > PATH_MAX_SIGMA_PX:
                        continue
                    pra, pdec = untangent(pe[0] + pe[1] * th, pn[0] + pn[1] * th, *m.ref)
                    if not in_polygon(r.s_region, float(pra), float(pdec)):
                        continue
                    m.plan["path"].append(dict(url=r.url, ra=float(pra), dec=float(pdec), det=det, mjd=r.mjd,
                                               sigma_arcsec=s, rho=max(1.0, 2.0 * s / P.PIXEL_ARCSEC)))
                    need_urls.add(r.url)
    # pixel history (same detector pixel just before / after, any field): S3 listings, in parallel
    jobs = [(m, d) for m in movers for d in m.dots if d.get("url") and tables.photos(m.zone) is not None]

    def hist(job):
        m, d = job
        try:
            return job, neighbours(tables.photos(m.zone), d["obs"], d["det"], d.get("s3_key"), meas.reader,
                                   HISTORY_BEFORE, HISTORY_AFTER)
        except Exception as e:  # noqa: BLE001
            meas.errors.append(f"listing {d['obs']}: {e!r}"[:200])
            return job, []

    with ThreadPoolExecutor(meas.workers) as ex:
        for (m, d), nb in ex.map(hist, jobs):
            for u, obs, off in nb:
                m.plan["history"].append(dict(url=u, obs=obs, offset=off, dot=d))
                need_urls.add(u)
    lays.update(meas.layouts(need_urls - set(lays)))
    meas.lays = lays


def targets_of(movers: list[Mover], meas: Measurer) -> dict[str, list[tuple]]:
    """url -> [(key, mode, x, y, rho)] for every planned measurement; keys stored on the plan items."""
    tg: dict[str, list[tuple]] = defaultdict(list)
    for m in movers:
        for d in m.dots:
            if d.get("url") and d["url"] in meas.lays and meas.lays[d["url"]] is not None:
                d["key"] = mkey(d["url"], "own", d["x"], d["y"])
                tg[d["url"]].append((d["key"], "own", d["x"], d["y"], 0.0))
        for purpose, items in m.plan.items():
            for it in items:
                lay = meas.lays.get(it["url"])
                if lay is None:
                    continue
                if purpose == "history":
                    x, y = it["dot"]["x"], it["dot"]["y"]
                    mode, rho = "pix", 0.0
                    it["mjd"] = lay.mjd()
                    # how far the sky under this pixel moved: only a photo of other sky tests the pixel itself
                    ra, dec = it["dot"].get("ra", np.nan), it["dot"].get("dec", np.nan)
                    if np.isfinite(ra) and np.isfinite(dec):
                        try:
                            sx, sy = (float(v) for v in lay.wcs().all_world2pix([[ra, dec]], 0)[0])
                            it["sky_px"] = math.hypot(sx - x, sy - y)
                        except Exception:  # noqa: BLE001  (SIP diverges far outside the photo: other sky for sure)
                            it["sky_px"] = 9999.0
                else:
                    x, y = (float(v) for v in lay.wcs().all_world2pix([[it["ra"], it["dec"]]], 0)[0])
                    mode, rho = ("search", it["rho"]) if purpose == "path" else ("forced", 0.0)
                    it["mjd"] = lay.mjd()
                if not (8 <= x < lay.nx - 8 and 8 <= y < lay.ny - 8):
                    continue
                it["x"], it["y"] = x, y
                if purpose == "static":  # pixel offsets -> (east, north) in pixels of 6.152″: one frame for all photos
                    it["jac"] = (lay.wcs().pixel_scale_matrix * 3600.0 / P.PIXEL_ARCSEC).tolist()
                it["key"] = mkey(it["url"], mode, x, y, rho)
                tg[it["url"]].append((it["key"], mode, x, y, rho))
    return tg


# ---------------------------------------------------------------------------------------------------------------
# checks

def evaluate(m: Mover, res: dict[str, dict], extra: dict[str, dict] | None = None) -> dict:
    """The checks of one mover from its measurements: {"checks": [...], "status", "known_name", "metrics"}."""
    checks: list[dict] = []
    metrics: dict = {}
    dots = [d for d in m.dots if d.get("key") in res]
    own = {id(d): res[d["key"]] for d in dots}
    dets = sorted({d["det"] for d in m.dots})
    span = f"{when(min(d['mjd'] for d in m.dots))}" if m.dots else ""
    status = None

    # --- hot pixel (contract rule) ---
    good = [d for d in m.dots if np.isfinite(d.get("ra", np.nan))]
    if len(good) >= 2:
        sp = P.same_pixel([d["det"] for d in good], [d["x"] for d in good], [d["y"] for d in good],
                          [d["ra"] for d in good], [d["dec"] for d in good])
        metrics["same_pixel"] = sp
        if sp["flaw"]:
            dd = next(iter(sp["per_det"]))
            p = sp["per_det"][dd]
            checks.append(check(
                "hot_pixel", "strong",
                f"All {p['n']} of its D{dd} dots sit on the same detector pixel (within {fnum(max(p['dx'], p['dy']), 1)} px)"
                f" although the sky under them moved {fnum(p['sky_arcsec'])}″: a hot pixel, not an object.",
                f"In the Viewer's hours clock on D{dd}, step through its exposures: the dot stays at the same place on "
                f"the photo while the stars jump with the pointing.",
                dict(det=dd, dx_px=p["dx"], dy_px=p["dy"], sky_arcsec=p["sky_arcsec"], dots=p["n"])))
            status = "flaw"

    # --- partner detector ---
    both = []
    by_obs = defaultdict(dict)
    for d in m.dots:
        by_obs[d["obs"]][d["det"]] = d
    for o, dd in by_obs.items():
        for d1 in (1, 2, 3):
            if d1 in dd and d1 + 3 in dd and dd[d1]["flux"] > 0:
                both.append((d1 + 3, dd[d1 + 3]["flux"] / dd[d1]["flux"], dd[d1]["mjd"]))
    single = defaultdict(list)
    for it in m.plan.get("partner", []):
        r = res.get(it.get("key"))
        if r is None or r.get("masked") or r.get("flux") is None or r.get("err") in (None, 0):
            continue
        if r["snr"] is not None and r["snr"] < CONTAM_SNR:
            continue
        single[it["dot"]["det"]].append((r["flux"], r["err"], it["dot"]))
    metrics["partner_both"] = len(both)
    if both:
        mdet = both[0][0]
        rat = float(np.median([b[1] for b in both]))
        lo, hi = P.KNOWN_RATIO[mdet]
        inr = lo <= rat <= hi
        checks.append(check(
            "partner_seen", "ok",
            f"D{mdet - 3} and D{mdet} both caught it at the same moment in {len(both)} exposure{'s' if len(both) > 1 else ''}"
            f" (D{mdet}/D{mdet - 3} {ratio_txt(rat)}; known asteroids {fnum(lo, 2)}–{fnum(hi, 1)}"
            f"{'' if inr else ', so its colour is unusual'}): real light, not a glitch of one detector.",
            f"In the Viewer's hours clock, switch between D{mdet - 3} and D{mdet} at {when(both[0][2])}: the dot is in both.",
            dict(ratio=rat, photos=len(both), known_lo=lo, known_hi=hi)))
    for det, rows in sorted(single.items()):
        if both:
            break
        f, e, n = P.combine([r[0] for r in rows], [r[1] for r in rows])
        own_flux = float(np.median([r[2]["flux"] for r in rows]))
        pc = P.partner_colour(det, own_flux, f, e)
        metrics[f"partner_D{det}"] = _clean(pc)
        pdet = P.PAIR[det]
        t_look = when(rows[0][2]["mjd"])
        if pc["verdict"] == "seen":
            guard = (extra or {}).get(f"guard|{det}")
            if guard and guard.get("n", 0) >= 2 and guard["flux"] >= STATIC_GUARD_RATIO * f and guard["snr"] >= 3:
                checks.append(check(
                    "one_detector", "warn",
                    f"D{pdet} shows {fnum(f)} ± {fnum(e)} µJy at its place at the same moment, but about as much "
                    f"({fnum(guard['flux'])} ± {fnum(guard['err'])} µJy) at that spot on other days: that is a fixed source "
                    f"there, so the colour test can't tell whether D{pdet} saw it.",
                    f"In the Viewer's hours clock, switch to D{pdet} at {t_look}: something sits at the circle in every photo.",
                    dict(partner_flux_ujy=f, partner_err_ujy=e, fixed_flux_ujy=guard["flux"], photos=n)))
                continue
            inr = pc.get("in_range")
            checks.append(check(
                "partner_seen", "ok",
                f"D{pdet} also measured {fnum(f)} ± {fnum(e)} µJy ({fnum(pc['snr'], 1)}σ) at its place at the same moment"
                f" against {fnum(own_flux)} µJy in D{det} (D{max(det, pdet)}/D{min(det, pdet)} {ratio_txt(pc['ratio'])};"
                f" known asteroids {fnum(pc['known_lo'], 2)}–{fnum(pc['known_hi'], 1)}): real light, not a glitch of one detector.",
                f"In the Viewer's hours clock, switch to D{pdet} at {t_look}: a faint dot at the circle.",
                dict(partner_flux_ujy=f, partner_err_ujy=e, flux_ujy=own_flux, ratio=pc["ratio"], photos=n,
                     known_lo=pc["known_lo"], known_hi=pc["known_hi"])))
        elif pc["verdict"] in ("red", "blue", "normal"):
            alone_d4 = det == 4 and all(d["det"] == 4 for d in m.dots)
            level = "strong" if alone_d4 else "warn"
            hi_ = max(det, pdet)
            lo_ = min(det, pdet)
            bound = pc["bound"]
            if pc["bound_kind"] == "lower":
                rel = (f"D{hi_}/D{lo_} ≥ {ratio_txt(bound)} (3σ), "
                       + ("redder than every known asteroid we measured" if pc["verdict"] == "red" else "inside")
                       + f" (known {fnum(pc['known_lo'], 2)}–{fnum(pc['known_hi'], 1)})")
            else:
                rel = (f"D{hi_}/D{lo_} ≤ {ratio_txt(bound)} (3σ), "
                       + ("bluer than every known asteroid we measured" if pc["verdict"] == "blue" else "inside")
                       + f" (known {fnum(pc['known_lo'], 2)}–{fnum(pc['known_hi'], 1)})")
            tail = (" Seen in D4 alone, which real asteroids almost never are (1 in 200) but D4 glitches often are."
                    if alone_d4 else "")
            checks.append(check(
                "one_detector", level,
                f"D{pdet} photographed the same spot at the same moment and measured {fnum(f)} ± {fnum(e)} µJy against "
                f"{fnum(own_flux)} µJy in D{det}: {rel}.{tail}",
                f"In the Viewer's hours clock, switch between D{det} and D{pdet} at {t_look}: the dot is in D{det} only.",
                dict(partner_flux_ujy=f, partner_err_ujy=e, flux_ujy=own_flux, bound=bound, bound_kind=pc["bound_kind"],
                     photos=n, known_lo=pc["known_lo"], known_hi=pc["known_hi"],
                     beyond_known=pc["verdict"] in ("red", "blue"))))

    # --- sharp / trail ---
    sr = [(d, own[id(d)]) for d in dots if own[id(d)].get("size_ratio") is not None]
    if sr:
        ratios = [r["size_ratio"] for _, r in sr]
        metrics["size_ratio"] = ratios
        worst_d, worst = min(sr, key=lambda q: q[1]["size_ratio"])
        n_sharp = sum(1 for x in ratios if x < P.SHARP_RATIO)
        vals = dict(size_ratio_min=float(min(ratios)), size_ratio_max=float(max(ratios)), dots=len(ratios),
                    sharp_dots=n_sharp, size_arcsec=worst["size_arcsec"])
        if n_sharp:
            level = "strong" if all(x < SHARP_STRONG for x in ratios) and len(ratios) >= 2 else "warn"
            checks.append(check(
                "sharp", level,
                f"{n_sharp} of its {len(ratios)} dots {'is' if n_sharp == 1 else 'are'} narrower than the stars of similar"
                f" brightness in the same photo (down to {fnum(min(ratios), 2)}× their width, {fnum(worst['size_arcsec'], 1)}″):"
                f" a cosmic-ray hit is that sharp, a point of light is not.",
                f"In the Viewer's hours clock on D{worst_d['det']}, exposure of {when(worst_d['mjd'])}: compare the dot with"
                f" the stars around it; a one-pixel spike is a hit.",
                vals))
        elif min(ratios) >= SHARP_OK and max(ratios) < WIDE_RATIO:
            checks.append(check(
                "sharp", "ok",
                f"Its {len(ratios)} dot{'s are' if len(ratios) > 1 else ' is'} as wide as the stars of similar brightness in"
                f" the same photos ({fnum(min(ratios), 2)}–{fnum(max(ratios), 2)}×): a point of light, not a cosmic-ray hit.",
                "", vals))
        n_wide = sum(1 for x in ratios if x >= WIDE_RATIO)
        if n_wide and (n_wide == len(ratios) or float(np.median(ratios)) >= WIDE_RATIO):
            level = "strong" if n_wide == len(ratios) and len(ratios) >= 2 else "warn"
            widest_d, widest = max(sr, key=lambda q: q[1]["size_ratio"])
            checks.append(check(
                "wide", level,
                f"{_count_of(n_wide, len(ratios), 'dot')} {'is' if n_wide == 1 else 'are'}"
                f" {fnum(min(x for x in ratios if x >= WIDE_RATIO), 2)}–{fnum(max(ratios), 2)}× as wide as the stars of the"
                f" same brightness in the same photos, while known asteroids' dots measure 0.8–1.3×: a smudge, not a sharp"
                f" moving point (noise or two faint things blended, unless it is a comet's haze).",
                f"In the Viewer's hours clock on D{widest_d['det']}, exposure of {when(widest_d['mjd'])}: compare the dot with"
                f" stars of similar brightness around it; it is softer and broader.",
                dict(size_ratio_min=float(min(ratios)), size_ratio_max=float(max(ratios)), dots=len(ratios),
                     wide_dots=n_wide, size_arcsec=widest["size_arcsec"])))
    # trail: only where a 113 s exposure would stretch a point by >= TRAIL_MIN_STRETCH (rate >~ 270″/h); below that the
    # stretch (1.07 at 146″/h, the fastest pair link makes) is smaller than the scatter of a faint dot's elongation
    rate = m.rate if np.isfinite(m.rate) else getattr(m, "rate_fit", np.nan)
    el = [own[id(d)].get("elong") for d in dots if own[id(d)].get("elong") is not None]
    metrics["elong"] = el
    if np.isfinite(rate) and el:
        exp_len = rate * EXPOSURE_S / 3600.0
        exp_el = math.sqrt(1 + exp_len ** 2 / (12 * PSF_SIGMA_ARCSEC ** 2))
        if exp_el >= TRAIL_MIN_STRETCH:
            ok = float(np.median(el)) >= exp_el - 0.15
            checks.append(check(
                "trail", "ok" if ok else "warn",
                f"At {fnum(rate)}″ an hour it should smear over {fnum(exp_len, 1)}″ in one exposure (stretch about"
                f" {fnum(exp_el, 2)}); its dots are stretched {fnum(min(el), 2)}–{fnum(max(el), 2)}"
                f"{'' if ok else ', less than a moving object that fast would be'}.",
                "In the Viewer's hours clock, look at the dot's shape: a fast object is a short streak along its arrow.",
                dict(rate_arcsec_h=rate, expected_elong=exp_el, elong_min=min(el), elong_max=max(el))))

    # --- one photo (static test at each dot's place) and TRANSIENT ---
    st_by_dot = defaultdict(list)
    for it in m.plan.get("static", []):
        r = res.get(it.get("key"))
        if r is None or r.get("masked") or r.get("err") in (None, 0) or r.get("flux") is None:
            continue
        r = dict(r)
        if it.get("jac") is not None and r.get("dx") is not None and r.get("dy") is not None:
            r["en"] = (np.asarray(it["jac"]) @ np.array([r["dx"], r["dy"]])).tolist()
        st_by_dot[id(it["dot"])].append(r)
    verdicts = []
    moved = len({round(d["mjd"], 5) for d in m.dots}) >= 2
    for d in dots:
        o = own[id(d)]
        oth = st_by_dot.get(id(d), [])
        v = P.one_photo(o.get("flux") or np.nan, o.get("err") or np.nan, [r["flux"] for r in oth], [r["err"] for r in oth],
                        size_ratio=o.get("size_ratio") if o.get("size_ratio") is not None else np.nan,
                        transient_px=o.get("transient_px"), moved=moved if moved else None,
                        other_offset=[r.get("en", [np.nan, np.nan]) for r in oth])
        verdicts.append((d, v))
    metrics["one_photo"] = [_clean({k: v[k] for k in ("verdict", "n_other", "other_snr", "ratio", "limit_ratio",
                                                       "transient_px", "offset_px")}) for _, v in verdicts]
    if verdicts:
        steady = [(d, v) for d, v in verdicts if v["verdict"] == "steady"]
        tested = [(d, v) for d, v in verdicts if v["verdict"] not in ("too_few",)]
        if steady:
            d, v = max(steady, key=lambda q: q[1]["other_snr"])
            level = "strong" if len(steady) == len(verdicts) else "warn"
            per_photo = v["other_snr"] / math.sqrt(max(v["n_other"], 1))  # the S/N one photo of it has
            bright = per_photo >= DETECT_SNR
            checks.append(check(
                "one_photo", level,
                f"{'Each of its places' if level == 'strong' else f'{len(steady)} of its {len(verdicts)} places'} holds a"
                f"{'' if bright else ' faint'} fixed source: {v['n_other']} other D{d['det']} exposures of other days measure"
                f" {fnum(v['other_flux_ujy'])} ± {fnum(v['other_err_ujy'])} µJy ({fnum(v['other_snr'], 1)}σ) there,"
                f" {fnum(v['ratio'], 2)}× the circled dot: "
                + ("that source is bright enough to be seen in each photo, so the dot is most likely it, not something"
                   " moving." if bright else "a star just under the cut can make such a dot without anything moving."),
                f"In the Viewer's hours clock on D{d['det']}, open other days of the visit at this place: something faint stays"
                f" at the circle.",
                dict(places=len(verdicts), steady_places=len(steady), photos=v["n_other"], fixed_flux_ujy=v["other_flux_ujy"],
                     fixed_err_ujy=v["other_err_ujy"], ratio=v["ratio"])))
        elif any(v["verdict"] == "neighbour" for _, v in verdicts):
            nb = [(d, v) for d, v in verdicts if v["verdict"] == "neighbour"]
            d, v = max(nb, key=lambda q: q[1]["other_snr"])
            checks.append(check(
                "one_photo", "warn",
                f"Light from something next to it spills into {'its places' if len(nb) > 1 else 'its place'} on other days:"
                f" {v['n_other']} other D{d['det']} exposures measure {fnum(v['other_flux_ujy'])} ± {fnum(v['other_err_ujy'])}"
                f" µJy there, centred {fnum(v['offset_px'] * P.PIXEL_ARCSEC, 1)}″ away ({fnum(v['ratio'], 2)}× the circled"
                f" dot): check that the dot isn't part of that neighbour.",
                f"In the Viewer's hours clock on D{d['det']}, compare the circle with other days of the visit: a source sits"
                f" just beside it.",
                dict(places=len(verdicts), neighbour_places=len(nb), photos=v["n_other"], fixed_flux_ujy=v["other_flux_ujy"],
                     fixed_err_ujy=v["other_err_ujy"], offset_arcsec=v["offset_px"] * P.PIXEL_ARCSEC, ratio=v["ratio"])))
        elif tested:
            deep = [(d, v) for d, v in tested if v["verdict"] in ("flash", "moved", "cosmic_ray")]
            if deep:
                d, v = min(deep, key=lambda q: q[1]["limit_ratio"])
                n_ph = sum(v_["n_other"] for _, v_ in deep)
                checks.append(check(
                    "one_photo", "ok",
                    f"Nothing sits at its place{'s' if len(deep) > 1 else ''} on other days: {n_ph} other"
                    f" D{'/D'.join(str(x) for x in sorted({q['det'] for q, _ in deep}))} exposures measure"
                    f" {fnum(v['other_flux_ujy'])} ± {fnum(v['other_err_ujy'])} µJy there against {fnum(v['own_flux_ujy'])} µJy"
                    f" in its own photo, so it is not a faint fixed star.",
                    "",
                    dict(places=len(verdicts), photos=n_ph, fixed_flux_ujy=v["other_flux_ujy"],
                         fixed_err_ujy=v["other_err_ujy"], limit_ratio=v["limit_ratio"])))

    # --- faint on the path ---
    pth = []
    fixed = [d for d, v in verdicts if v["verdict"] in ("steady", "neighbour") and np.isfinite(d.get("ra", np.nan))]
    for it in m.plan.get("path", []):
        r = res.get(it.get("key"))
        if r is None or r.get("best_snr") is None or r.get("best_err") in (None, 0):
            continue
        # a fixed source sits at one of its places (one_photo): light found near it on the path proves nothing
        if any(float(np.hypot(*tangent(it["ra"], it["dec"], d["ra"], d["dec"]))) < FIXED_CLEAR_ARCSEC for d in fixed):
            continue
        pth.append((it, r))
    if pth:
        own_flux = float(np.median([d["flux"] for d in m.dots]))
        ps = P.path_summary(own_flux, [r["best_flux"] for _, r in pth], [r["best_err"] for _, r in pth],
                            [r.get("chance95") if r.get("chance95") is not None else np.nan for _, r in pth])
        metrics["path"] = _clean(ps)
        dpath = pth[0][0]["det"]
        times = sorted(it["mjd"] for it, _ in pth)
        if ps["verdict"] == "found":
            found = [(it, r) for it, r in pth if r["best_snr"] >= max(3.0, r.get("chance95") or 3.0)
                     and P.PATH_SEEN_RATIO[0] * own_flux <= r["best_flux"] <= P.PATH_SEEN_RATIO[1] * own_flux]
            it, r = max(found, key=lambda q: q[1]["best_snr"])
            checks.append(check(
                "faint_on_path", "ok",
                f"Found below the detection cut where its path predicts it in {ps['n_found']} of {ps['n']} other D{dpath}"
                f" exposures (best {fnum(r['best_snr'], 1)}σ, {fnum(r['best_flux'])} µJy against {fnum(own_flux)} µJy).",
                f"In the Viewer's hours clock on D{dpath}, the exposure of {when(it['mjd'])}: a faint smudge"
                f" {fnum(math.hypot(r['best_dx'], r['best_dy']) * P.PIXEL_ARCSEC)}″ from where the arrow points.",
                dict(photos=ps["n"], found=ps["n_found"], best_snr=r["best_snr"], flux_ujy=own_flux)))
        elif ps["verdict"] == "absent":
            checks.append(check(
                "faint_on_path", "warn",
                f"Not found where its path predicts it in {ps['n_should']} other D{dpath} exposures that should show it"
                f" (together {fnum(ps['should_flux_ujy'])} ± {fnum(ps['should_err_ujy'])} µJy against {fnum(own_flux)} µJy"
                f" in its own photos).",
                f"In the Viewer's hours clock on D{dpath}, the exposures of {when(times[0])} to {hhmm(times[-1])}:"
                f" nothing on the arrow's line.",
                dict(photos=ps["n_should"], found=0, flux_ujy=own_flux, path_flux_ujy=ps["should_flux_ujy"],
                     path_err_ujy=ps["should_err_ujy"])))

    # --- pixel history: persistence and hot pixel ---
    hist = defaultdict(list)
    for it in m.plan.get("history", []):
        r = res.get(it.get("key"))
        if r is None:
            continue
        hist[id(it["dot"])].append((it, r))
    pers_dots, hot_dots, hist_dots = [], [], 0
    for d in m.dots:
        h = hist.get(id(d), [])
        if not h:
            continue
        hist_dots += 1
        metrics.setdefault("history", []).append(
            dict(flux=d["flux"], pix=[_clean(dict(
                off=it["offset"], sky_px=it.get("sky_px"), flux=r.get("flux"), snr=r.get("snr"), peak=r.get("peak"), sat=r.get("sat"),
                masked=r.get("masked"))) for it, r in h]))
        before = [(it, r) for it, r in h if it["offset"] < 0 and np.isfinite(it.get("mjd", np.nan))
                  and 0 < (d["mjd"] - it["mjd"]) * 1440.0 <= P.PERSIST_WINDOW_MIN]
        after = [(it, r) for it, r in h if it["offset"] > 0]
        if before:
            pr = P.persistence(d["flux"], [r.get("flux") if r.get("flux") is not None else np.nan for _, r in before],
                               [bool(r.get("sat")) for _, r in before], [bool(r.get("persist")) for _, r in before])
            if pr["saturated"]:
                pers_dots.append((d, pr, before))

        # hot pixel: the same detector pixel glows like the dot in EVERY neighbour photo that looks at other sky
        # (validation: "any one before and after" fired on 14% of real asteroids, chance stars in crowded fields)
        valid = [(it, r) for it, r in h if r.get("flux") is not None and r.get("snr") is not None and not r.get("masked")
                 and (it.get("sky_px") is None or it["sky_px"] >= HOT_SKY_MOVED_PX)]
        glow = [(it, r) for it, r in valid if r["snr"] >= 3
                and HOT_FLUX_RANGE[0] * d["flux"] <= r["flux"] <= HOT_FLUX_RANGE[1] * d["flux"]]
        if (len(valid) >= HOT_MIN_PHOTOS and len(glow) == len(valid) and any(it["offset"] < 0 for it, _ in glow)
                and any(it["offset"] > 0 for it, _ in glow)):
            hot_dots.append((d, [r for _, r in glow]))
    metrics["history_dots"] = hist_dots
    if hot_dots:
        d, g = hot_dots[0]
        level = "strong" if len(hot_dots) == len(m.dots) and len(m.dots) >= 2 else "warn"
        code = "hot_pixel_proven" if level == "strong" else "hot_pixel"
        n_ph = len(g)
        checks.append(check(
            code, level,
            f"The detector pixel under {'every dot' if level == 'strong' else f'{len(hot_dots)} of its {len(m.dots)} dots'} glows"
            f" in each of the {n_ph} D{d['det']} exposures just before and after too ({fnum(min(r['flux'] for r in g))}–"
            f"{fnum(max(r['flux'] for r in g))} µJy against {fnum(d['flux'])} µJy in the dot), although those photos look at"
            f" other sky: a hot pixel.",
            f"In the Viewer's hours clock on D{d['det']}, step to the exposures before and after {when(d['mjd'])}: a dot"
            f" stays at the same place on the photo while the stars jump.",
            dict(dots=len(hot_dots), photos=n_ph, flux_ujy=d["flux"], pixel_flux_ujy=float(np.median([r["flux"] for r in g])))))
        if level == "strong":
            status = "flaw"
    if pers_dots:
        d, pr, before = pers_dots[0]
        sat_all = len(pers_dots) == len(m.dots) and all(p[1]["saturated"] for p in pers_dots)
        level = "strong" if sat_all else "warn"
        code = "persistence_proven" if sat_all else "persistence"
        dt_min = min((d["mjd"] - it["mjd"]) * 1440.0 for it, _ in before)
        what = (f"a star saturated the same D{d['det']} pixels" if pr["saturated"] else
                f"a source {fnum(pr['max_prev_flux'] / max(d['flux'], 1.0))}× brighter ({fnum(pr['max_prev_flux'])} µJy) lit"
                f" the same D{d['det']} pixels")
        who = ("Every dot sits" if sat_all else f"{len(pers_dots)} of its {len(m.dots)} dots sit" if len(pers_dots) > 1
               else f"1 of its {len(m.dots)} dots sits")
        checks.append(check(
            code, level,
            f"{who} where {what} {fnum(dt_min, 0)} minutes earlier, in the exposure before: the dot may be its leftover"
            f" image (persistence).",
            f"In the Viewer's hours clock on D{d['det']}, step to the exposure just before {when(d['mjd'])}: a bright star"
            f" sat on the photo where the dot later appears.",
            dict(dots=len(pers_dots), flux_ujy=d["flux"], prev_flux_ujy=pr["max_prev_flux"], minutes=dt_min,
                 saturated=pr["saturated"])))
        if sat_all:
            status = "flaw"

    checks.sort(key=lambda c: LEVEL_ORDER[c["level"]])
    # one check per code: keep the strongest (the exporter merges by code)
    seen, uniq = set(), []
    for c in checks:
        if c["code"] not in seen:
            seen.add(c["code"])
            uniq.append(c)
    uniq.sort(key=lambda c: LEVEL_ORDER[c["level"]])
    return {"checks": uniq, "status": status, "known_name": None, "metrics": metrics, "span": span, "dets": dets}


# ---------------------------------------------------------------------------------------------------------------
# driver

def guard_targets(movers: list[Mover], res: dict, tables: Tables, meas: Measurer) -> dict[str, dict]:
    """Second pass for partner_seen: the partner detector at the same sky place on other days. Returns per mover id
    {"guard|<det>": {flux, err, snr, n}}."""
    extra_plan = []
    for m in movers:
        pl = tables.photos(m.zone)
        single = defaultdict(list)
        for it in m.plan.get("partner", []):
            r = res.get(it.get("key"))
            if r and not r.get("masked") and r.get("snr") is not None and r["snr"] >= 2.5:
                single[it["dot"]["det"]].append(it)
        for det, its in single.items():
            pdet = P.PAIR[det]
            ra, dec = its[0]["ra"], its[0]["dec"]
            own_obs = {d["obs"] for d in m.dots}
            cand = pl[(pl.detector == pdet) & ~pl.obs_id.isin(own_obs)].copy()
            cand["adt"] = ((cand.mjd - m.t0) * 24.0).abs()
            cand = cand[cand.adt >= STATIC_MIN_DT_H].sort_values("adt")
            cand = cand[[in_polygon(s, ra, dec) for s in cand.s_region]].drop_duplicates("session").head(4)
            for r in cand.itertuples():
                extra_plan.append((m, det, r.url, ra, dec))
    lays = meas.layouts(u for _, _, u, _, _ in extra_plan)
    tg = defaultdict(list)
    keys = []
    for m, det, u, ra, dec in extra_plan:
        lay = lays.get(u)
        if lay is None:
            continue
        x, y = (float(v) for v in lay.wcs().all_world2pix([[ra, dec]], 0)[0])
        if not (8 <= x < lay.nx - 8 and 8 <= y < lay.ny - 8):
            continue
        k = mkey(u, "forced", x, y)
        tg[u].append((k, "forced", x, y, 0.0))
        keys.append((m.id, det, k))
    r2 = meas.run(tg)
    acc = defaultdict(list)
    for mid, det, k in keys:
        r = r2.get(k)
        if r and not r.get("masked") and r.get("err"):
            acc[(mid, det)].append(r)
    out = defaultdict(dict)
    for (mid, det), rs in acc.items():
        f, e, n = P.combine([r["flux"] for r in rs], [r["err"] for r in rs])
        out[mid][f"guard|{det}"] = dict(flux=f, err=e, snr=f / e if e else np.nan, n=n)
    return out


def run(movers: list[Mover], tables: Tables, meas: Measurer) -> dict[str, dict]:
    print(f"planning {len(movers)} movers", flush=True)
    plan_movers(movers, tables, meas)
    tg = targets_of(movers, meas)
    n_t = sum(len(v) for v in tg.values())
    print(f"measuring {n_t} places in {len(tg)} photos", flush=True)
    res = meas.run(tg)
    extra = guard_targets(movers, res, tables, meas)
    return {m.id: evaluate(m, res, extra.get(m.id)) for m in movers}


def summary(results: dict[str, dict]) -> dict:
    cnt = defaultdict(lambda: defaultdict(int))
    for r in results.values():
        for c in r["checks"]:
            cnt[c["code"]][c["level"]] += 1
    st = defaultdict(int)
    for r in results.values():
        st[r["status"] or "open"] += 1
    return {"movers": len(results), "with_checks": sum(1 for r in results.values() if r["checks"]),
            "codes": {k: dict(v) for k, v in sorted(cnt.items())}, "status": dict(st)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", default=str(ROOT / "web" / "public" / "data" / "candidates.json"))
    ap.add_argument("--root", default=str(DATA_ROOT), help="local mirror of the bucket layout (results/, photolists/)")
    ap.add_argument("--out", default=None, help="default <root>/checks/pixel.json")
    ap.add_argument("--select", choices=("tocheck", "open"), default="tocheck")
    ap.add_argument("--id", action="append", help="only these candidate ids")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=48, help="download threads (one process)")
    ap.add_argument("--fetch", action="store_true", help="gsutil cp -n missing suspects tables and photo lists")
    ap.add_argument("--metrics", default=None, help="also write per-mover metrics here (JSON)")
    ap.add_argument("--validate", action="store_true", help="run the tests on known asteroids and junk (validate.py)")
    a = ap.parse_args(argv)
    root = Path(a.root)
    cache_dir = root / "cache" / "pixel"
    cache_dir.mkdir(parents=True, exist_ok=True)
    meas = Measurer(cache_dir, a.workers)
    tables = Tables(root, a.fetch)
    if a.validate:
        import pixel_validate
        return pixel_validate.main(root, tables, meas)
    doc = json.loads(Path(a.candidates).read_text())
    cands = select_movers(doc, a.select)
    if a.id:
        cands = [c for c in cands if c["id"] in set(a.id)]
    if a.limit:
        cands = cands[: a.limit]
    movers, missing = [], []
    for c in cands:
        rows = tables.rows(c["zone"])
        if rows is None or c["id"] not in rows.index:
            missing.append(c["id"])
            continue
        movers.append(Mover(c["id"], c["zone"], rows.loc[c["id"]], c))
    if missing:
        print(f"{len(missing)} movers without a suspects row locally (use --fetch): {missing[:5]}…", flush=True)
    results = run(movers, tables, meas)
    out = Path(a.out) if a.out else root / "checks" / "pixel.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc_out = {"source": "pixel", "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "candidates_file": str(Path(a.candidates).resolve()),
               "candidates": {k: {"checks": v["checks"], "status": v["status"], "known_name": None}
                              for k, v in results.items()}}
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc_out, ensure_ascii=False, indent=1, default=_json_default))
    tmp.replace(out)
    if a.metrics:
        Path(a.metrics).write_text(json.dumps({k: {"metrics": v["metrics"], "checks": v["checks"], "status": v["status"]}
                                               for k, v in results.items()}, default=_json_default, indent=1))
    s = summary(results)
    print(json.dumps(s, indent=1))
    print(f"wrote {out}; read {S3.BYTES[0] / 1e6:.0f} MB in {S3.REQUESTS[0]} requests; {len(meas.errors)} errors",
          flush=True)
    for e in meas.errors[:10]:
        print("  ", e)
    return 0


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    sys.exit(main())
