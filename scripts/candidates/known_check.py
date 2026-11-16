"""Check producer `known`: which known asteroid, comet, planet or moon is at each mover's dots (contracts/api.md v3.23).

Run from pipeline/ (it needs kete, skyshift_core and skyshift_pipeline):
    uv run python ../scripts/candidates/known_check.py --candidates ../web/public/data/candidates.json \
        [--root /Users/raducucu/skyshift-data] [--cache <root>/cache/known_check] [--out <root>/checks/known.json] \
        [--mpchecker 30 --mpchecker-out <path>]

Every mover of the file, at each of its dots' times (results/<zone>/suspects.parquet `dot_mjd`, `dot_obs`; the mean time
alone when the zone's table lacks the id), seen from SPHEREx (IMAGE header X_SC..VZ_SC of the dot's photo, read once over
HTTPS from the photo list's url and cached; the Earth's centre when a header can't be read):
- MPC asteroids (newest cache/mpc/orbits/mpcorb_<date>.parquet, all ~1.6 million, NO brightness cut) and comets (the
  newest cometels snapshot, completed with allcometels.json.gz when it predates 16 Nov: cometels.json.gz lacks every comet
  the MPC hasn't moved to its current epoch, 306P/LINEAR among them). skyshift_core.known_index finds the orbits that can
  be within reach (two-body cut with measured per-orbit margins; its index is built once over the candidates' time span
  and cached; the server's index of the same mpcorb snapshot, <root>/cache/mpc/known_index/, gives the asteroids ever
  brighter than V 21.5, so only the fainter ones and the comets are built), kete moves those with n-body physics and
  light time to each dot's time: positions and rates.
- JPL Horizons, only for planets and their moons, only for movers within a planet system's reach (SYSTEMS below:
  Jupiter's outer moons wander ~3° from it; the exporter already drops movers within 80′ of Jupiter); geocentric tables
  (≤ 2.4″ from SPHEREx at Jupiter and beyond).
The mover's place at a dot's time is its straight line (candidates.json ra, dec at mjd, rate_arcsec_h along pa_deg): the
dots' own mean and fitted motion, so a dot is at most its position error off it.

Each mover gets one `known_nearby` check:
- status `known` + known_name when a body is CERTAIN by the contract's rule (known.judge: within 10″ at every dot's time,
  rate within 20% and direction within 15° when both move), level `strong`;
- level `strong`, status open, when a body is within ON_DOTS_ARCSEC (2″) at every one of >= 2 dot times but its
  motion misses the rule (a 2-dot mover's rate from dots 2 minutes apart is good to only ~±30″/h);
- level `warn` when the nearest body within 2′ (config KNOWN_NEARBY_ARCSEC) is not certain;
- level `ok` when no known body was predicted within 2′ at any of its times.
high_pm: a high_pm source is one fixed source in >= 3 sessions of each visit (or two such sources, 3–120″ apart, six
months apart). A known body moves far more between visits, so none can be one (never status known). Only a slow distant
body (< HIGHPM_SLOW_ARCSEC_H: Centaurs and TNOs) could sit still long enough to be mistaken for one visit's source:
those (perihelion > SLOW_Q_AU, two-body, numpy) within 10″ of the source at the candidate's `mjd` give a `known_nearby`
warn. High_pm candidates without one are not listed (nothing measured worth showing).

Writes {"source": "known", "made", "candidates_file", "stats", "candidates": {id: {checks, status, known_name}}}.
Rerunnable and idempotent: orbit snapshots, the index, headers, Horizons tables and every candidate's predictions are
cached in --cache (keyed by the orbit snapshots and the candidate's place and times).

--mpchecker N: also asks the MPC's own MPChecker (geocentric, V < 25, 5′) for the N highest-scoring movers still open
and writes how it agrees with ours (only a time and a position are sent; one request at a time, MPC_PAUSE_S apart).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from skyshift_core import known_index as KI
from skyshift_core import planets as PL
from skyshift_pipeline import config as C
from skyshift_pipeline import known as K
from skyshift_pipeline import known_mpc as KM
from skyshift_pipeline.store import Store, photolist_rel, results_rel

SOURCE = "known"
DEFAULT_ROOT = "/Users/raducucu/skyshift-data"
NEARBY = C.KNOWN_NEARBY_ARCSEC
CHUNK_DAYS = 2.0            # movers within this many days share one kete run
CHUNK_MAX = 400             # ... at most this many movers
HEADER_WORKERS = 8
INDEX_MARGIN_DAYS = 3.0     # the index covers the candidates' times ± this ...
INDEX_BLOCK_DAYS = 30.0     # ... widened to whole blocks of this from known_index.MJD_LO (a stable cache key)
INDEX_PART_ROWS = 250_000   # asteroids per build part (~0.4 GB each)
HIGHPM_SLOW_ARCSEC_H = 1.0  # high_pm: only a body slower than this could sit on a fixed source for a session
HIGHPM_NEAR_ARCSEC = 10.0
ON_DOTS_ARCSEC = 2.0        # a body this close at every one of >= 2 dot times is on our dots (level strong, status open
                            # when the motion misses the certain rule: a few-minute arc measures a rough motion)
SLOW_Q_AU = 8.0             # high_pm: orbits with a perihelion beyond this (the only bodies that can move < 1″/h)
MPC_PAUSE_S = 8.0
MPCHECK_URL = "https://minorplanetcenter.net/cgi-bin/mpcheck.cgi"
MPCHECK_RADIUS_ARCMIN = 5
# Planet systems for Horizons: the planet, its moons SPHEREx can see (V ≲ 19), and how far from the planet to look (deg).
SYSTEMS = {
    "Mars": (499, [(401, "Phobos"), (402, "Deimos")], 0.1),
    "Jupiter": (599, [(501, "Io"), (502, "Europa"), (503, "Ganymede"), (504, "Callisto"), (505, "Amalthea"), (506, "Himalia"),
                      (507, "Elara"), (508, "Pasiphae"), (509, "Sinope"), (510, "Lysithea"), (511, "Carme"), (512, "Ananke"),
                      (513, "Leda")], 3.2),
    "Saturn": (699, [(601, "Mimas"), (602, "Enceladus"), (603, "Tethys"), (604, "Dione"), (605, "Rhea"), (606, "Titan"),
                     (607, "Hyperion"), (608, "Iapetus"), (609, "Phoebe"), (610, "Janus"), (611, "Epimetheus")], 1.0),
    "Uranus": (799, [(701, "Ariel"), (702, "Umbriel"), (703, "Titania"), (704, "Oberon"), (705, "Miranda"), (716, "Caliban"),
                     (717, "Sycorax")], 0.3),
    "Neptune": (899, [(801, "Triton"), (802, "Nereid")], 0.25),
}


def log(msg: str) -> None:
    print(f"[known_check {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _sha(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False))
    tmp.replace(path)


def _r(x, nd=1):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


# ---------------------------------------------------------------- inputs

def mover_times(store: Store, movers: list[dict]) -> dict[str, dict]:
    """Per mover id: times (sorted unique dot MJDs), obs (the photo of each time), n_dots, from_dots (False: the mean
    time only, the zone's suspects table lacks the id)."""
    out: dict[str, dict] = {}
    by_zone: dict[str, list[dict]] = {}
    for c in movers:
        by_zone.setdefault(c["zone"], []).append(c)
    for zone, cs in by_zone.items():
        try:
            s = store.read_parquet(results_rel(zone, "suspects.parquet"))
            s = s.set_index("id")
        except Exception as e:  # noqa: BLE001
            log(f"{zone}: no suspects table ({str(e)[:80]}): mean times only")
            s = None
        for c in cs:
            if s is not None and c["id"] in s.index:
                r = s.loc[c["id"]]
                t = np.asarray(r["dot_mjd"], np.float64)
                obs = np.asarray(r["dot_obs"], dtype=object)
                u, first = np.unique(np.round(t, 7), return_index=True)
                out[c["id"]] = {"times": t[first].tolist(), "obs": obs[first].tolist(), "n_dots": int(len(t)),
                                "from_dots": True}
            else:
                out[c["id"]] = {"times": [float(c["mjd"])], "obs": [None], "n_dots": None, "from_dots": False}
    return out


SC_KEYS = (*KM.SC_POS, *KM.SC_VEL)
HEADER_BYTES = 12_288   # DR1: the IMAGE header starts at 2,880 B and holds X_SC..VZ_SC at ~7.7–8.2 KB (END at 26.4 KB)
_http = threading.local()


def header_sc(raw: bytes) -> dict:
    """X_SC..VZ_SC from the first IMAGE extension header found in `raw` (the file's first bytes); keys missing when the
    bytes stop before them."""
    out, inside = {}, False
    for i in range(0, len(raw) - 79, 80):
        card = raw[i:i + 80].decode("ascii", "replace")
        key = card[:8].strip()
        if key == "XTENSION":
            if inside:
                break
            inside = "IMAGE" in card[10:30]
        elif inside and key == "END":
            break
        elif inside and key in SC_KEYS and card[8:10] == "= " and key not in out:
            out[key] = KM._num(card[10:].split("/")[0].strip())
    return out


def read_sc(url: str) -> dict:
    """SPHEREx's place from one ranged read of the photo's first HEADER_BYTES (one round trip instead of
    known_mpc.read_observer's size query and 256 KB block); that full reader when the bytes fall short."""
    import requests

    s = getattr(_http, "s", None)
    if s is None:
        s = _http.s = requests.Session()
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            r = s.get(url, headers={"Range": f"bytes=0-{HEADER_BYTES - 1}"}, timeout=60)
            r.raise_for_status()
            got = header_sc(r.content)
            if all(got.get(k) is not None for k in SC_KEYS):
                return got
            break
        except Exception:  # noqa: BLE001
            time.sleep(2 * attempt)
    return KM.read_observer(url)


def pipeline_observers(store: Store, want: dict[str, str]) -> dict[str, dict]:
    """SPHEREx's place per exposure from the pipeline's own known step (cache/mpc/<zone>/run_*.json `observers`, the
    same IMAGE header keys) where those files are already in the root: no read at all."""
    out: dict[str, dict] = {}
    for zone in sorted(set(want.values())):
        d = Path(store.root) / "cache" / "mpc" / zone
        if not d.is_dir():
            continue
        for f in d.glob("run_*.json"):
            try:
                obs = json.loads(f.read_text()).get("observers") or {}
            except (OSError, ValueError):
                continue
            for o, v in obs.items():
                if o in want and v.get("observer") == "spacecraft" and all(v.get(k) is not None for k in SC_KEYS):
                    out[o] = {"mjd_avg": v.get("mjd_avg"), **{k: v[k] for k in SC_KEYS}}
    return out


def observers(store: Store, cache: Path, want: dict[str, str]) -> dict[str, dict]:
    """obs_id (exposure) -> X_SC..VZ_SC: the pipeline's session caches where present, else the photo's IMAGE header
    (read_sc), cached in observers.parquet (saved every few hundred reads, so an interrupted run keeps its work)."""
    f = cache / "observers.parquet"
    cols = ["obs_id", "mjd_avg", *SC_KEYS]
    have = pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=cols)
    done = set(have["obs_id"])
    todo = sorted(o for o in want if o not in done)
    if todo:
        cache.mkdir(parents=True, exist_ok=True)
        seed = pipeline_observers(store, {o: want[o] for o in todo})
        if seed:
            have = pd.concat([have, pd.DataFrame([{"obs_id": o, **v} for o, v in seed.items()]).reindex(columns=cols)],
                             ignore_index=True)
            have.to_parquet(f, index=False)
            todo = [o for o in todo if o not in seed]
        urls: dict[str, str] = {}
        for zone in sorted(set(want[o] for o in todo)):
            p = store.read_parquet(photolist_rel(zone))
            urls.update(dict(zip(p["obs_id"], p["url"])))
        log(f"IMAGE headers: {len(todo)} photos to read ({len(done)} cached, {len(seed)} from the pipeline's known step)")
        t0 = time.time()

        def one(o):
            u = urls.get(o)
            if not u:
                return {"obs_id": o}
            try:
                return {"obs_id": o, **read_sc(u)}
            except Exception as e:  # noqa: BLE001
                log(f"no header for {o}: {str(e)[:80]}")
                return {"obs_id": o}

        step = 300
        with ThreadPoolExecutor(HEADER_WORKERS) as ex:
            for i in range(0, len(todo), step):
                rows = list(ex.map(one, todo[i:i + step]))
                have = pd.concat([have, pd.DataFrame(rows).reindex(columns=cols)], ignore_index=True)
                have.to_parquet(f, index=False)
                log(f"IMAGE headers: {min(i + step, len(todo))}/{len(todo)} in {time.time() - t0:.0f} s")
    out = {}
    for r in have.to_dict("records"):
        ok = all(r.get(k) is not None and np.isfinite(r.get(k)) for k in (*KM.SC_POS, *KM.SC_VEL))
        out[r["obs_id"]] = {k: float(r[k]) for k in (*KM.SC_POS, *KM.SC_VEL)} if ok else None
    return out


def newest(store: Store, prefix: str) -> str:
    names = sorted(n for n in store.listdir(C.MPC_ORBITS_DIR) if n.startswith(prefix + "_") and n.endswith(".parquet"))
    if not names:
        raise SystemExit(f"no {C.MPC_ORBITS_DIR}/{prefix}_<date>.parquet under {store.root}")
    return names[-1]


def orbits(store: Store, cache: Path) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """(asteroids, comets, label): the newest snapshots; a cometels snapshot without allcometels' comets is completed
    here with allcometels.json.gz (fetched once, cached as allcometels_<date>.parquet in the cache folder)."""
    a, c = newest(store, "mpcorb"), newest(store, "cometels")
    ast = store.read_parquet(f"{C.MPC_ORBITS_DIR}/{a}")
    com = store.read_parquet(f"{C.MPC_ORBITS_DIR}/{c}")
    label = f"{a} + {c}"
    if KM.ORBIT_FILE not in com.columns:
        have = sorted(cache.glob("allcometels_*.parquet"))
        if have:
            every = pd.read_parquet(have[-1])
        else:
            every = KM._fetch_all_comets()
            cache.mkdir(parents=True, exist_ok=True)
            have = [cache / f"allcometels_{time.strftime('%Y-%m-%d', time.gmtime())}.parquet"]
            every.to_parquet(have[-1], index=False)
        com = KM.merge_comets(com, every)
        label += f" + {have[-1].name}"
    ast["desig"] = ast["desig"].astype(str)
    log(f"orbits: {label}: {len(ast)} asteroids, {len(com)} comets "
        f"({int((com.get(KM.ORBIT_FILE, pd.Series(dtype=str)) == 'allcometels').sum())} from allcometels)")
    return ast, com, label


ROW_KEYS = ("q", "e", "inc", "node", "argp", "tp", "epoch", "h", "g", "comet", "err_deg", "always")


def index_span(mjd_lo: float, mjd_hi: float) -> tuple[int, int]:
    """The index's time range: the candidates' times ± INDEX_MARGIN_DAYS, widened to whole INDEX_BLOCK_DAYS blocks from
    known_index.MJD_LO so a slightly different export reuses the cached index."""
    b, z = INDEX_BLOCK_DAYS, KI.MJD_LO
    return int(z + b * math.floor((mjd_lo - INDEX_MARGIN_DAYS - z) / b)), int(z + b * math.ceil((mjd_hi + INDEX_MARGIN_DAYS - z) / b))


def base_index(path: str | None, local_root: str, ast_file: str, lo: float, hi: float) -> KI.KnownIndex | None:
    """The server's index (asteroids ever brighter than V 21.5 + kete's comets; core known_index main; in the bucket at
    cache/mpc/known_index/) when it was built from the same mpcorb snapshot and covers [lo, hi]: its asteroids' measured
    margins are reused as they are. `path`, else the newest <local_root>/cache/mpc/known_index/known_index_*.npz."""
    cands = [Path(path)] if path else sorted(Path(local_root, "cache/mpc/known_index").glob("known_index_*.npz"))
    for p in reversed(cands):
        try:
            b = KI.load_index(str(p))
        except (OSError, ValueError) as e:
            log(f"base index {p}: unreadable ({e})")
            continue
        if b.meta["snapshot"].split(" + ")[0] == ast_file and b.mjd_lo <= lo and b.mjd_hi >= hi:
            return b
        log(f"base index {p.name}: {b.meta['snapshot']} {b.mjd_lo:.0f}–{b.mjd_hi:.0f} does not fit {ast_file} {lo}–{hi}")
    return None


def merge_index(parts: list[dict], snapshot: str, lo: float, hi: float) -> dict:
    """One index from several (rows concatenated; names re-packed)."""
    names = []
    for p in parts:
        o, b = p["name_off"], p["name_blob"]
        names += [b[o[i]:o[i + 1]].tobytes().decode("utf-8") for i in range(len(o) - 1)]
    blob, off = KI._pack_names(names)
    return {"version": np.array(KI.INDEX_VERSION), "snapshot": np.array(snapshot),
            "built": np.array(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            "mjd_lo": np.array(float(lo)), "mjd_hi": np.array(float(hi)), "v_keep": np.array(np.inf),
            **{k: np.concatenate([np.asarray(p[k]) for p in parts]) for k in ROW_KEYS}, "name_blob": blob, "name_off": off}


def index(cache: Path, ast: pd.DataFrame, com: pd.DataFrame, label: str, mjd_lo: float, mjd_hi: float,
          base_path: str | None = None, local_root: str = DEFAULT_ROOT) -> KI.KnownIndex:
    """skyshift_core.known_index with no brightness cut over the candidates' span, built once and cached. With the
    server's index of the same mpcorb snapshot (base_index), only what it leaves out is built: asteroids never brighter
    than V 21.5 and every comet (the merged list), in parts of INDEX_PART_ROWS (memory; each part is cached, so an
    interrupted build resumes)."""
    lo, hi = index_span(mjd_lo, mjd_hi)
    f = cache / f"index_{_sha(label, lo, hi, KI.INDEX_VERSION)}.npz"
    if f.exists():
        return KI.load_index(str(f))
    cache.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    base = base_index(base_path, local_root, label.split(" + ")[0], lo, hi)
    parts: list[dict] = []
    if base is not None:
        b = base.data
        keep = ~b["comet"].astype(bool)
        bn = index_names(base)
        parts.append({**{k: b[k][keep] for k in ROW_KEYS}, **dict(zip(("name_blob", "name_off"), KI._pack_names(bn[keep])))})
        names = KI.orbit_arrays(ast)["name"]
        rest = ast[~np.isin(names.astype(str), bn[keep].astype(str))].reset_index(drop=True)
        if len(rest) == 0:
            rest = ast.iloc[:1]  # build_index needs one asteroid beside the comets (a duplicate row is harmless)
        log(f"base index {base.meta['snapshot']} ({base.meta['built']}): {int(keep.sum())} asteroids reused; "
            f"building {len(rest)} fainter asteroids + {len(com)} comets for MJD {lo}–{hi}")
    else:
        rest = ast
        log(f"building the known index (all V) for {len(rest)} asteroids + {len(com)} comets, MJD {lo}–{hi}: "
            f"~10–40 min (unloaded laptop), once")
    for k, i in enumerate(range(0, max(len(rest), 1), INDEX_PART_ROWS)):
        pf = cache / f"{f.stem}_part{k}.npz"
        if not pf.exists():
            data = KI.build_index(rest.iloc[i:i + INDEX_PART_ROWS], com if k == 0 else None, snapshot=label,
                                  mjd_lo=lo, mjd_hi=hi, v_keep=None, log=log)
            KI.save_index(str(pf.with_suffix(".tmp.npz")), data)
            pf.with_suffix(".tmp.npz").replace(pf)
        with np.load(pf, allow_pickle=False) as z:
            parts.append({k2: z[k2] for k2 in (*ROW_KEYS, "name_blob", "name_off")})
        log(f"index part {k}: {len(parts[-1]['q'])} rows ({time.time() - t0:.0f} s so far)")
    KI.save_index(str(f.with_suffix(".tmp.npz")), merge_index(parts, label, lo, hi))
    f.with_suffix(".tmp.npz").replace(f)
    for pf in cache.glob(f"{f.stem}_part*.npz"):
        pf.unlink()
    log(f"index built in {time.time() - t0:.0f} s: {f.name}")
    return KI.load_index(str(f))


def index_names(idx: KI.KnownIndex) -> np.ndarray:
    o, b = idx.data["name_off"], idx.data["name_blob"]
    return np.array([b[o[i]:o[i + 1]].tobytes().decode("utf-8") for i in range(len(o) - 1)], dtype=object)


# ---------------------------------------------------------------- MPC predictions (kete)

def chunk_rows(idx: KI.KnownIndex, items: list[dict]) -> np.ndarray:
    """Index rows that can be within reach of any item at any of its times: skyshift_core.known_index's coarse cut
    (`_group_cut`: two-body positions at the chunk's first and last time, the per-orbit margin, half the arc between
    them and its bend; near or `always` orbits and every comet pass), evaluated once for the whole chunk and tested
    against each item's centre. A superset: kete decides."""
    jds = KI.mjd_to_jd_tdb([t for it in items for t in it["times"]])
    ja, jb = float(jds.min()), float(jds.max())
    ua, da, ra_ = KI._geocentric(idx, ja)
    ub, db, rb = KI._geocentric(idx, jb)
    chord = np.arccos(np.clip((ua * ub).sum(0), -1.0, 1.0))
    dmin = np.minimum(da, db)
    acc = KI.GMS * (1.0 / (0.9 * np.minimum(ra_, rb)) ** 2 + 1.04)
    base = KI._margin(idx, None, dmin) + 0.5 * chord + acc * (jb - ja) ** 2 / 8.0 / dmin
    keep = (dmin < KI.NEAR_AU) | idx.always
    for it in items:
        span_h = (max(it["times"]) - min(it["times"])) * 24.0
        rate = it["rate"] if it["rate"] is not None and np.isfinite(it["rate"]) else 0.0
        rad = np.radians((NEARBY + rate * span_h / 2.0 + 5.0) / 3600.0)
        c = KI.unit_radec(it["ra"], it["dec"]).astype(np.float32)
        keep |= np.arccos(np.clip(np.maximum(c @ ua, c @ ub), -1.0, 1.0)) < rad + base
    return np.concatenate([idx.ast_rows[np.flatnonzero(keep)], idx.comet_rows])


def predict_mpc(idx: KI.KnownIndex, items: list[dict], obs_sc: dict[str, dict | None]) -> dict[str, list[dict]]:
    """items: {id, ra, dec, rate, pa, mjd, times, obs}. For each, the MPC bodies within NEARBY of its place at each time:
    {id: [{k, row, ra, dec, rate, pa, v}]}. One kete run per chunk of nearby times."""
    import kete

    out: dict[str, list[dict]] = {it["id"]: [] for it in items}
    if not items:
        return out
    items = sorted(items, key=lambda it: it["times"][0])
    chunks, cur = [], [items[0]]
    for it in items[1:]:
        if it["times"][0] - cur[0]["times"][0] > CHUNK_DAYS or len(cur) >= CHUNK_MAX:
            chunks.append(cur)
            cur = []
        cur.append(it)
    chunks.append(cur)
    h, g = idx.data["h"], idx.data["g"]
    for n, ch in enumerate(chunks):
        t0 = time.time()
        fovs, keys = [], {}
        rows = chunk_rows(idx, ch)
        obs_states = {}
        for it in ch:
            ra, dec = K.dot_places(it["ra"], it["dec"], it["mjd"], it["rate"], it["pa"], it["times"])
            for k, (t, o) in enumerate(zip(it["times"], it["obs"])):
                key = (round(t, 8), o)
                if key not in obs_states:
                    obs_states[key] = KI.observer_state(kete, t, obs_sc.get(o) if o else None)
                ob = obs_states[key]
                fovs.append(kete.ConeFOV(kete.Vector.from_ra_dec(float(ra[k]), float(dec[k])), NEARBY / 3600.0, ob))
                keys[(round(ob.jd, 9), round(float(ra[k]), 7), round(float(dec[k]), 7))] = (it["id"], k)
        states = kete.propagate_n_body(KI.kete_states(kete, idx, rows), min(o.jd for o in obs_states.values()),
                                       suppress_errors=True)
        states = [s for s in states if s.is_finite]
        hits = 0
        for ss in kete.fov_state_check(states, fovs):
            if not len(ss.states):
                continue
            p = ss.fov.pointing
            pra, pdec = (float(x) for x in (p.ra, p.dec))
            key = keys.get((round(ss.fov.observer.jd, 9), round(pra, 7), round(pdec, 7)))
            if key is None:  # pointing round trip off in the last digit: nearest centre of that time
                cand = [(abs(kk[1] - pra) + abs(kk[2] - pdec), v) for kk, v in keys.items()
                        if kk[0] == round(ss.fov.observer.jd, 9)]
                key = min(cand)[1]
            cid, k = key
            rd = np.asarray(ss.ra_dec_with_rates, float).reshape(-1, 4)
            sun2obs = ss.fov.observer.pos
            for st, (sra, sdec, dra, ddec) in zip(ss.states, rd):
                row = int(st.desig)
                hh = float(h[row])
                v = float(kete.flux.hg_apparent_mag(st.pos, sun2obs, hh, float(g[row]))) if np.isfinite(hh) else None
                rate, pa = K.rate_pa(dra * 150.0, ddec * 150.0)  # deg/day -> ″/h
                out[cid].append({"k": k, "row": row, "ra": float(sra) % 360.0, "dec": float(sdec), "rate": float(rate),
                                 "pa": float(pa), "v": v})
                hits += 1
        log(f"chunk {n + 1}/{len(chunks)}: {len(ch)} movers, {len(rows)} orbits to kete, {hits} hits, {time.time() - t0:.0f} s")
    return out


# ---------------------------------------------------------------- planets and moons (Horizons)

def horizons_at(naif: int, jds: list[float], cache: Path) -> pd.DataFrame:
    """One body's geocentric positions and rates at the given JDs (UT), cached."""
    import requests
    from skyshift_pipeline.known_planets import HORIZONS_URL, parse_table

    jds = sorted(set(round(j, 6) for j in jds))
    f = cache / "horizons" / f"{naif}_{_sha(*jds)}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    tab = []
    for i in range(0, len(jds), 200):
        params = {"format": "json", "COMMAND": f"'{naif}'", "OBJ_DATA": "NO", "MAKE_EPHEM": "YES",
                  "EPHEM_TYPE": "OBSERVER", "CENTER": "'500@399'", "TLIST_TYPE": "JD",
                  "TLIST": " ".join(f"'{j:.6f}'" for j in jds[i:i + 200]), "QUANTITIES": "'1,3,9'", "CSV_FORMAT": "YES",
                  "ANG_FORMAT": "DEG", "CAL_FORMAT": "JD", "EXTRA_PREC": "YES"}
        r = requests.get(HORIZONS_URL, params=params, timeout=120)
        r.raise_for_status()
        tab.append(parse_table(r.json()["result"]))
        time.sleep(1.0)
    t = pd.concat(tab, ignore_index=True)
    f.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(f, index=False)
    return t


def predict_planets(items: list[dict], cache: Path) -> tuple[dict[str, list[dict]], dict]:
    """Planets and moons within NEARBY of a mover's places, for movers within a system's reach (SYSTEMS)."""
    out: dict[str, list[dict]] = {it["id"]: [] for it in items}
    stats = {"movers_near_a_planet": 0, "bodies_queried": 0}
    if not items:
        return out, stats
    mjd = np.array([it["mjd"] for it in items])
    pos = PL.positions(mjd)
    near: dict[str, list[int]] = {}
    for name, (naif, moons, reach) in SYSTEMS.items():
        pra, pdec = pos[name]
        sep = PL._sep_arcsec(np.array([it["ra"] for it in items]), np.array([it["dec"] for it in items]), pra, pdec) / 3600.0
        idx = np.flatnonzero(sep <= reach)
        if len(idx):
            near[name] = idx.tolist()
    stats["movers_near_a_planet"] = int(len({i for v in near.values() for i in v}))
    for name, ids in near.items():
        naif, moons, _ = SYSTEMS[name]
        jds = [t + K.MJD_TO_JD for i in ids for t in items[i]["times"]]
        for body_naif, body in [(naif, name), *moons]:
            tab = horizons_at(body_naif, jds, cache)
            stats["bodies_queried"] += 1
            for i in ids:
                it = items[i]
                ra, dec = K.dot_places(it["ra"], it["dec"], it["mjd"], it["rate"], it["pa"], it["times"])
                for k, t in enumerate(it["times"]):
                    j = int(np.argmin(np.abs(tab["jd"].to_numpy() - (t + K.MJD_TO_JD))))
                    r = tab.iloc[j]
                    if abs(r["jd"] - (t + K.MJD_TO_JD)) > 1e-4:
                        continue
                    if float(PL._sep_arcsec(ra[k], dec[k], r["ra"], r["dec"])) <= NEARBY:
                        rate, pa = K.rate_pa(r["dra_arcsec_h"], r["ddec_arcsec_h"])
                        out[it["id"]].append({"k": k, "name": body, "cls": "planet" if body == name else "moon",
                                              "ra": float(r["ra"]), "dec": float(r["dec"]), "rate": float(rate),
                                              "pa": float(pa), "v": _r(r["v"], 2)})
    return out, stats


# ---------------------------------------------------------------- verdicts and texts

def _kind(name: str, cls: str | None) -> str:
    if cls in ("planet", "moon"):
        return cls
    if re.match(r"^(\d+[PDCXI]|[PDCXI])[/-]", name) or (cls or "").startswith("Comet"):
        return "comet"
    return "asteroid"


def full_names(ast: pd.DataFrame) -> dict[str, str]:
    """Named numbered asteroids as the MPC writes them: {"Franzthaler": "(6212) Franzthaler"}. The pipeline's names
    (known_mpc.object_table) are the name alone, "(161055)" for a numbered one without a name, else the designation."""
    if "name" not in ast:
        return {}
    num = ast["desig"].astype(str).str.strip()
    nm = ast["name"].astype(object).where(ast["name"].notna(), "").astype(str).str.strip()
    ok = num.str.fullmatch(r"\d+").fillna(False).to_numpy(bool) & (nm != "").to_numpy()
    return dict(zip(nm[ok], "(" + num[ok] + ") " + nm[ok]))


def bodies(it: dict, hits: list[dict], names: np.ndarray, comet: np.ndarray, full: dict | None = None) -> list[dict]:
    """Group hits by body; each body's separation at every time (NaN where not within NEARBY), its mean rate and
    direction, V, and the contract's verdict (known.judge). full: full_names()."""
    full = full or {}
    ra, dec = K.dot_places(it["ra"], it["dec"], it["mjd"], it["rate"], it["pa"], it["times"])
    by: dict[str, list[dict]] = {}
    for h in hits:
        key = h.get("name") or names[h["row"]]
        key = full.get(key, key)
        by.setdefault(key, []).append(h)
    out = []
    for name, hs in by.items():
        seps = np.full(len(it["times"]), np.nan)
        for h in hs:
            seps[h["k"]] = float(PL._sep_arcsec(ra[h["k"]], dec[h["k"]], h["ra"], h["dec"]))
        e = np.mean([h["rate"] * math.sin(math.radians(h["pa"])) for h in hs])
        n = np.mean([h["rate"] * math.cos(math.radians(h["pa"])) for h in hs])
        kr, kp = K.rate_pa(e, n)
        j = K.judge(seps, it["rate"], it["pa"], float(kr), float(kp))
        h0 = min(hs, key=lambda h: seps[h["k"]])
        cls = h0.get("cls") or ("comet" if "row" in h0 and comet[h0["row"]] else None)
        vs = [h["v"] for h in hs if h.get("v") is not None]
        # where the body is, seen from our mover, at its nearest time: offset east and north (″)
        de = (h0["ra"] - ra[h0["k"]] + 540.0) % 360.0 - 180.0
        out.append({"name": name, "kind": _kind(name, cls), "seps": seps, "rate": float(kr), "pa": float(kp),
                    "v": min(vs) if vs else None, "east": de * 3600.0 * math.cos(math.radians(dec[h0["k"]])),
                    "north": (h0["dec"] - dec[h0["k"]]) * 3600.0, **j})
    out.sort(key=lambda b: (not b["certain"], b["sep_max"] if b["certain"] else b["sep_min"]))
    return out


def _dir(east: float, north: float) -> str:
    a = math.degrees(math.atan2(east, north)) % 360.0
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((a + 22.5) // 45) % 8]


def _what(b: dict) -> str:
    k = b["kind"]
    v = f", V {b['v']:.1f}" if b.get("v") is not None else ""
    if k == "comet":
        return f"comet {b['name']} (MPC orbit{v})"
    if k in ("planet", "moon"):
        return f"{'the planet' if k == 'planet' else 'the moon'} {b['name']} (JPL Horizons{v})"
    return f"asteroid {b['name']} (MPC orbit{v})"


def mover_check(c: dict, it: dict, bs: list[dict], n_ast: int, n_com: int) -> dict:
    """The candidate's entry: {checks: [known_nearby], status, known_name}."""
    nt = len(it["times"])
    when = f"its {nt} dot times" if it["from_dots"] and nt > 1 else ("its dot time" if it["from_dots"] else "its mean time")
    det = c.get("det")
    ours = (f"ours moves {c['rate_arcsec_h']:.0f}″ an hour toward PA {c['pa_deg']:.0f}°"
            if c.get("rate_arcsec_h") is not None and c.get("pa_deg") is not None else "ours has no measured motion")
    base = {"our_rate_arcsec_h": c.get("rate_arcsec_h"), "our_pa_deg": c.get("pa_deg"), "times": nt,
            "from_dots": it["from_dots"]}
    if not bs or not np.isfinite(bs[0]["sep_min"]):
        return {"checks": [{
            "code": "known_nearby", "level": "ok",
            "text": (f"No known asteroid, comet, planet or moon was predicted within 2′ of it at {when} "
                     f"(MPC orbits of {n_ast:,} asteroids at any brightness and {n_com:,} comets, seen from SPHEREx)."),
            "look": "", "values": {**base, "sep_arcsec": None}}], "status": None, "known_name": None}
    b = bs[0]
    close = [x for x in bs if np.isfinite(x["sep_max"]) and x["sep_max"] <= ON_DOTS_ARCSEC]
    on_dots = not b["certain"] and it["from_dots"] and nt >= 2 and bool(close)
    if on_dots:
        b = min(close, key=lambda x: x["sep_max"])
    vals = {**base, "name": b["name"], "body": b["kind"], "v": _r(b["v"], 1),
            "sep_arcsec": _r(b["sep_min"]), "sep_max_arcsec": _r(b["sep_max"]) if np.isfinite(b["sep_max"]) else None,
            "rate_arcsec_h": _r(b["rate"]), "pa_deg": _r(b["pa"], 0),
            "rate_ratio": _r(b["rate_ratio"], 2), "dpa_deg": _r(b["dpa"], 0)}
    theirs = f"moving {b['rate']:.0f}″ an hour toward PA {b['pa']:.0f}°"
    if b["certain"]:
        rng = (f"{np.nanmin(b['seps']):.1f}–{b['sep_max']:.1f}″ from each of its {nt} dot times" if nt > 1
               else f"{b['sep_max']:.1f}″ from it")
        return {"checks": [{
            "code": "known_nearby", "level": "strong",
            "text": f"The known {_what(b)} was predicted {rng}, {theirs}; {ours}: it is that {b['kind']}.",
            "look": (f"Nothing new here: on the hours clock in D{det} it moves exactly like {b['name']}, so it is that "
                     f"known {b['kind']}."),
            "values": vals}], "status": "known", "known_name": b["name"]}
    where = _dir(b["east"], b["north"])
    off = f"{b['sep_min']:.1f}″" if b["sep_min"] < 10 else f"{b['sep_min']:.0f}″"
    why = []
    if not np.isfinite(b["sep_max"]):
        why.append("it is not within 2′ at every dot time")
    elif b["sep_max"] > C.KNOWN_CERTAIN_ARCSEC:
        why.append(f"it is {b['sep_max']:.0f}″ off at the farthest dot")
    if b["rate_ratio"] is not None and abs(b["rate_ratio"] - 1.0) > C.KNOWN_RATE_TOL:
        why.append("the speeds differ")
    if b["dpa"] is not None and b["dpa"] > C.KNOWN_PA_TOL_DEG:
        why.append(f"the directions differ by {b['dpa']:.0f}°")
    if on_dots:
        span = (max(it["times"]) - min(it["times"])) * 1440.0
        return {"checks": [{
            "code": "known_nearby", "level": "strong",
            "text": (f"The known {_what(b)} was predicted {np.nanmin(b['seps']):.1f}–{b['sep_max']:.1f}″ from each of its "
                     f"{nt} dot times, {theirs}; {ours}. Its place fits our dots; only the motion misses the certain rule "
                     f"({', '.join(why) or 'see the numbers'}), and dots {span:.0f} minutes apart measure a motion roughly."),
            "look": (f"On the hours clock in D{det}, step through its exposures: very likely the known {b['kind']} "
                     f"{b['name']}; a second moving dot near its path would mean ours is something else."),
            "values": vals}], "status": None, "known_name": None}
    return {"checks": [{
        "code": "known_nearby", "level": "warn",
        "text": (f"The known {_what(b)} was predicted {off} to the {where} of it, {theirs}; {ours}. "
                 f"Not a certain match: {', '.join(why) or 'see the numbers'}."),
        "look": (f"On the hours clock in D{det}, step through its exposures and look {off} to the {where}: "
                 f"if a second moving dot runs there, that is {b['name']} and ours is something else; if not, ours may be "
                 f"{b['name']} slightly off its predicted place."),
        "values": vals}], "status": None, "known_name": None}


def slow_bodies(idx: KI.KnownIndex, cands: list[dict], names: np.ndarray) -> dict[str, list[dict]]:
    """high_pm: the known bodies that move slower than HIGHPM_SLOW_ARCSEC_H, near each source at its `mjd`. Only orbits
    with perihelion beyond SLOW_Q_AU can be that slow (from SPHEREx, ~90° from the Sun, a body at r AU moves roughly
    30/r ″/h: Jupiter's Trojans ~3″/h, Centaurs and TNOs under 1″/h), and over a year their two-body positions are good
    to a few arcsec, so this is numpy only (skyshift_core.known_index's Kepler and approximate Earth, light time once).
    Geocentric: SPHEREx is at most 7,100 km away, under 0.1″ at 10 AU. {id: [hits as bodies() takes them, k = 0]}."""
    d = idx.data
    sel = np.flatnonzero((d["q"] > SLOW_Q_AU) & (d["e"] < 1.0))
    out: dict[str, list[dict]] = {c["id"]: [] for c in cands}
    if not len(sel) or not cands:
        return out
    kep = KI.Kepler.from_elements(d["q"][sel], d["e"][sel], d["inc"][sel], d["node"][sel], d["argp"][sel], d["tp"][sel],
                                  dtype=np.float64)
    h, g = d["h"][sel].astype(float), d["g"][sel].astype(float)

    def sky(jd):
        earth = KI.earth_position(jd)[:, None]
        geo = kep.positions(jd) - earth
        lt = np.sqrt((geo * geo).sum(0)) / 173.1446326846693
        helio = kep.positions(jd - lt)
        geo = helio - earth
        u = KI.ecl_to_eq(geo / np.sqrt((geo * geo).sum(0)))
        return u, helio, geo

    for c in cands:
        jd = float(KI.mjd_to_jd_tdb(c["mjd"]))
        u0, helio, geo = sky(jd)
        cvec = KI.unit_radec(c["ra"], c["dec"])
        sep = np.degrees(np.arccos(np.clip(cvec @ u0, -1.0, 1.0))) * 3600.0
        near = np.flatnonzero(sep <= NEARBY)
        if not len(near):
            continue
        u1, _, _ = sky(jd + 1.0 / 24.0)
        for j in near:
            ra0, dec0 = np.degrees(np.arctan2(u0[1, j], u0[0, j])) % 360.0, np.degrees(np.arcsin(u0[2, j]))
            ra1, dec1 = np.degrees(np.arctan2(u1[1, j], u1[0, j])) % 360.0, np.degrees(np.arcsin(u1[2, j]))
            e = ((ra1 - ra0 + 540.0) % 360.0 - 180.0) * 3600.0 * math.cos(math.radians(dec0))
            rate, pa = K.rate_pa(e, (dec1 - dec0) * 3600.0)
            v = float(KI.hg_mag(h[j], g[j], helio[:, j:j + 1], geo[:, j:j + 1])[0]) if np.isfinite(h[j]) else None
            out[c["id"]].append({"k": 0, "name": names[sel[j]], "cls": None, "ra": float(ra0), "dec": float(dec0),
                                 "rate": float(rate), "pa": float(pa), "v": v})
    return out


def highpm_items(cands: list[dict]) -> list[dict]:
    return [{"id": c["id"], "ra": c["ra"], "dec": c["dec"], "rate": None, "pa": None, "mjd": c["mjd"],
             "times": [c["mjd"]], "obs": [None], "from_dots": False} for c in cands]


def highpm_check(c: dict, bs: list[dict]) -> dict | None:
    slow = [b for b in bs if b["rate"] < HIGHPM_SLOW_ARCSEC_H and b["sep_min"] <= HIGHPM_NEAR_ARCSEC]
    if not slow:
        return None
    b = slow[0]
    return {"checks": [{
        "code": "known_nearby", "level": "warn",
        "text": (f"The known slow {_what(b)} was predicted {b['sep_min']:.1f}″ from it on MJD {c['mjd']:.1f}, moving only "
                 f"{b['rate']:.2f}″ an hour: one visit's position may be that body."),
        "look": f"On the months clock in D{c.get('det')}, compare the visits: a dot that is there in one visit only is the body.",
        "values": {"name": b["name"], "body": b["kind"], "sep_arcsec": _r(b["sep_min"]), "rate_arcsec_h": _r(b["rate"], 2),
                   "v": _r(b["v"], 1)}}], "status": None, "known_name": None}


# ---------------------------------------------------------------- MPChecker cross-check

def _sexa(ra: float, dec: float) -> tuple[str, str]:
    h = ra / 15.0
    hh, mm = int(h), int((h - int(h)) * 60)
    ss = ((h - hh) * 60 - mm) * 60
    sg = "+" if dec >= 0 else "-"
    d = abs(dec)
    dd, dm = int(d), int((d - int(d)) * 60)
    ds = ((d - dd) * 60 - dm) * 60
    return f"{hh:02d} {mm:02d} {ss:05.2f}", f"{sg}{dd:02d} {dm:02d} {ds:04.1f}"


def mpcheck(ra: float, dec: float, mjd: float, cache: Path) -> str:
    """MPChecker's answer (HTML) for one time and place, geocentric (code 500), V < 25, cached."""
    import requests

    f = cache / "mpchecker" / f"{_sha(round(ra, 5), round(dec, 5), round(mjd, 5))}.html"
    if f.exists():
        return f.read_text()
    from astropy.time import Time

    t = Time(mjd, format="mjd", scale="utc").datetime
    day = t.day + (t.hour + (t.minute + (t.second + t.microsecond / 1e6) / 60.0) / 60.0) / 24.0
    sra, sdec = _sexa(ra, dec)
    params = {"year": f"{t.year}", "month": f"{t.month:02d}", "day": f"{day:.5f}", "which": "pos", "ra": sra,
              "decl": sdec, "TextArea": "", "radius": str(MPCHECK_RADIUS_ARCMIN), "limit": "25.0", "oc": "500",
              "sort": "d", "mot": "h", "tmot": "s", "pdes": "u", "needed": "f", "ps": "n", "type": "p"}
    r = requests.get(MPCHECK_URL, params=params, timeout=180, headers={"User-Agent": "python-requests (orbit check)"})
    r.raise_for_status()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(r.text)
    time.sleep(MPC_PAUSE_S)
    return r.text


_LINE = re.compile(r"^(?P<name>.{1,25}?)\s+(?P<rah>\d\d) (?P<ram>\d\d) (?P<ras>[\d.]+)\s+(?P<dec>[+-]\d\d \d\d [\d.]+)\s+"
                   r"(?P<v>[\d.]+|\s*)\s+(?P<oe>[\d.]+)(?P<ew>[EW])\s+(?P<on>[\d.]+)(?P<ns>[NS])\s+(?P<mra>\d+)(?P<sra>[+-])\s+"
                   r"(?P<mdec>\d+)(?P<sdec>[+-])")


def parse_mpcheck(html: str) -> list[dict]:
    """MPChecker's table rows: name, offset east/north (″), V, motion east/north (″/h)."""
    text = re.sub(r"<[^>]*>", "", html).replace("&#176;", " ")
    out = []
    for ln in text.splitlines():
        m = _LINE.match(ln.rstrip())
        if not m:
            continue
        d = m.groupdict()
        e = float(d["oe"]) * 60.0 * (1 if d["ew"] == "E" else -1)
        n = float(d["on"]) * 60.0 * (1 if d["ns"] == "N" else -1)
        out.append({"name": d["name"].strip(), "east": e, "north": n, "sep": math.hypot(e, n),
                    "v": float(d["v"]) if d["v"].strip() else None,
                    "rate_e": float(d["mra"]) * (1 if d["sra"] == "+" else -1),
                    "rate_n": float(d["mdec"]) * (1 if d["sdec"] == "+" else -1)})
    return out


def _keys(mpc_name: str) -> set[str]:
    """MPChecker's '(6212) Franzthaler', '(480640) 2015 NR12', '306P/LINEAR' → names ours may carry."""
    s = {mpc_name}
    m = re.match(r"^\((\d+)\)\s*(.*)$", mpc_name)
    if m:
        s |= {f"({m.group(1)})", m.group(2).strip()}
    return s


def cross_check(cands: list[dict], items: dict[str, dict], body_lists: dict[str, list[dict]], cache: Path, n: int) -> dict:
    """The n highest-scoring open movers against MPChecker. Ours: bodies within 2′ at the time nearest the mover's mean
    time with V < 25 or no V (MPChecker's limit). Agreement per mover: the same bodies within 90″ (a body 90–150″ out
    in one list may fall just outside the other's 2′: SPHEREx sits up to 7,100 km from the Earth's centre)."""
    top = sorted([c for c in cands if c["kind"] == "mover"], key=lambda c: -c["score"])[:n]
    rows = []
    for c in top:
        try:
            got = parse_mpcheck(mpcheck(c["ra"], c["dec"], c["mjd"], cache))
        except Exception as e:  # noqa: BLE001
            rows.append({"id": c["id"], "error": str(e)[:200]})
            continue
        bs = body_lists.get(c["id"], [])
        ours = {b["name"]: b for b in bs if b["kind"] not in ("planet", "moon") and (b["v"] is None or b["v"] < 25.0)}
        theirs = {g["name"]: g for g in got}
        o_in = {k for k, b in ours.items() if np.nanmin(b["seps"]) <= 90.0}
        t_in = {k for k, g in theirs.items() if g["sep"] <= 90.0}
        matched, only_ours, only_theirs, dsep = [], [], [], []
        for k in t_in:
            hit = next((o for o in ours if o in _keys(k)), None)
            if hit is not None:
                matched.append(k)
                dsep.append(abs(float(np.nanmin(ours[hit]["seps"])) - theirs[k]["sep"]))
            elif theirs[k]["sep"] <= 90.0:
                only_theirs.append(k)
        for o in o_in:
            if not any(o in _keys(k) for k in theirs if theirs[k]["sep"] <= 150.0):
                only_ours.append(o)
        rows.append({"id": c["id"], "score": c["score"], "mjd": c["mjd"], "mpchecker_within_90": sorted(t_in),
                     "ours_within_90": sorted(o_in), "matched": sorted(matched), "only_mpchecker": sorted(only_theirs),
                     "only_ours": sorted(only_ours), "sep_diff_arcsec": [round(x, 1) for x in dsep],
                     "agree": not only_theirs and not only_ours})
        log(f"MPChecker {c['id']}: theirs {sorted(t_in)} ours {sorted(o_in)}")
    ok = [r for r in rows if "error" not in r]
    return {"movers": len(rows), "answered": len(ok), "agree": sum(r["agree"] for r in ok),
            "bodies_matched": sum(len(r["matched"]) for r in ok),
            "only_mpchecker": sum(len(r["only_mpchecker"]) for r in ok), "only_ours": sum(len(r["only_ours"]) for r in ok),
            "rows": rows}


# ---------------------------------------------------------------- main

def items_for(cands: list[dict], times: dict[str, dict]) -> list[dict]:
    out = []
    for c in cands:
        t = times[c["id"]]
        out.append({"id": c["id"], "ra": float(c["ra"]), "dec": float(c["dec"]), "mjd": float(c["mjd"]),
                    "rate": None if c.get("rate_arcsec_h") is None else float(c["rate_arcsec_h"]),
                    "pa": None if c.get("pa_deg") is None else float(c["pa_deg"]),
                    "times": t["times"], "obs": t["obs"], "from_dots": t["from_dots"]})
    return out


def cached_predict(cache: Path, label: str, items: list[dict], fn, tag: str) -> dict[str, list[dict]]:
    """fn(items) for the items whose (place, motion, times, orbit snapshot) isn't in the cache file yet."""
    f = cache / f"pred_{tag}_{_sha(label)}.json"
    have = json.loads(f.read_text()) if f.exists() else {}
    key = {it["id"]: _sha(it["ra"], it["dec"], it["mjd"], it["rate"], it["pa"], it["times"], it["obs"]) for it in items}
    todo = [it for it in items if have.get(it["id"], {}).get("key") != key[it["id"]]]
    if todo:
        got = fn(todo)
        for it in todo:
            have[it["id"]] = {"key": key[it["id"]], "hits": got.get(it["id"], [])}
        _write_json(f, have)
    return {it["id"]: have[it["id"]]["hits"] for it in items}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="local folder or gs:// with results/, photolists/, cache/mpc/orbits/")
    ap.add_argument("--cache", default=None, help="default <root>/cache/known_check (local)")
    ap.add_argument("--out", default=None, help="default <root>/checks/known.json")
    ap.add_argument("--no-highpm", action="store_true")
    ap.add_argument("--mpchecker", type=int, default=0, help="cross-check the N highest-scoring open movers with MPChecker")
    ap.add_argument("--mpchecker-out", default=None)
    ap.add_argument("--base-index", default=None, help="the server's known index to reuse (default: the newest "
                    "<root>/cache/mpc/known_index/known_index_*.npz of the same mpcorb snapshot, if any)")
    a = ap.parse_args(argv)
    root = a.root
    local_root = DEFAULT_ROOT if root.startswith("gs://") else root
    cache = Path(a.cache or f"{local_root}/cache/known_check")
    out_path = Path(a.out or f"{local_root}/checks/known.json")
    store = Store(root)
    doc = json.loads(Path(a.candidates).read_text())
    cands = doc["candidates"]
    movers = [c for c in cands if c["kind"] == "mover"]
    hpm = [] if a.no_highpm else [c for c in cands if c["kind"] == "high_pm"]
    log(f"{a.candidates}: {len(movers)} movers, {len(hpm)} high_pm")

    times = mover_times(store, movers)
    want = {o: c["zone"] for c in movers for o in times[c["id"]]["obs"] if o}
    obs_sc = observers(store, cache, want)
    items = items_for(movers, times)
    hitems = highpm_items(hpm)
    all_t = [t for it in items + hitems for t in it["times"]]
    ast, com, label = orbits(store, cache)
    idx = index(cache, ast, com, label, min(all_t), max(all_t), a.base_index, local_root)
    names, comet = index_names(idx), idx.comet
    n_ast, n_com = int(len(idx.ast_rows)), int(len(idx.comet_rows))
    full = full_names(ast)
    del ast, com

    t0 = time.time()
    mpc = cached_predict(cache, f"{label}|{idx.meta['built']}", items, lambda its: predict_mpc(idx, its, obs_sc), "mover")
    pl, pl_stats = predict_planets(items, cache)
    log(f"movers predicted in {time.time() - t0:.0f} s")
    out: dict[str, dict] = {}
    body_lists: dict[str, list[dict]] = {}
    for c, it in zip(movers, items):
        bs = bodies(it, mpc[c["id"]] + pl[c["id"]], names, comet, full)
        body_lists[c["id"]] = bs
        out[c["id"]] = mover_check(c, it, bs, n_ast, n_com)
    hp_n = 0
    if hitems:
        t0 = time.time()
        hp = slow_bodies(idx, hpm, names)
        for c, it in zip(hpm, hitems):
            e = highpm_check(c, bodies(it, hp[c["id"]], names, comet, full))
            if e is not None:
                out[c["id"]] = e
                hp_n += 1
        log(f"high_pm predicted in {time.time() - t0:.0f} s")

    known = {k: v for k, v in out.items() if v["status"] == "known"}
    levels = pd.Series([v["checks"][0]["level"] for k, v in out.items() if k in times]).value_counts().to_dict()
    stats = {"orbits": label, "index_built": idx.meta["built"], "asteroids": n_ast, "comets": n_com,
             "movers": len(movers), "movers_from_dots": int(sum(t["from_dots"] for t in times.values())),
             "photos_geocentre": int(sum(1 for o in want if obs_sc.get(o) is None)),
             "known": len(known), "levels": levels, "high_pm": len(hpm), "high_pm_with_check": hp_n, **pl_stats,
             "known_names": sorted({v["known_name"] for v in known.values()})}
    res = {"source": SOURCE, "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "candidates_file": str(Path(a.candidates).resolve()), "stats": stats, "candidates": out}
    _write_json(out_path, res)
    log(f"wrote {out_path}: {json.dumps(stats)[:600]}")
    if a.mpchecker:
        open_movers = [c for c in movers if out[c["id"]]["status"] != "known"]
        rep = cross_check(open_movers, {it["id"]: it for it in items}, body_lists, cache, a.mpchecker)
        mp_out = Path(a.mpchecker_out or out_path.with_name("known_mpchecker.json"))
        _write_json(mp_out, rep)
        log(f"MPChecker: {rep['answered']}/{rep['movers']} answered, {rep['agree']} agree; wrote {mp_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
