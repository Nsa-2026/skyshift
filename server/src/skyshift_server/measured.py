"""What SkyShift measured in a blink field: `measured` and `measured_reason` of /api/frames
(contracts/api.md v3.3, "measured").

gaia[] and known[] are catalogue predictions; this layer is what our pipeline (pipeline/,
contracts/dots.md) detected in the photos of the response. Read-only, from the data bucket
(SKYSHIFT_DATA_ROOT, default gs://nasa-glazing-data, or a local folder in tests):

    skyrun/progress.json              which sky zones are combined, and their stage
    results/<zone>/summary.json       the box and steps of zones not listed there (pilot, t_<slug>)
    results/<zone>/crossids.parquet   linked sources (source_id, ra, dec) and their Gaia match
    results/<zone>/movers.parquet     movers (one session) -> tracks
    results/<zone>/suspects.parquet   unexplained movers, with grade/reasons once vetted
    results/<zone>/astrometry.parquet fitted motions -> shifts (pm_snr >= 3 kept)
    results/<zone>/highpm_candidates.parquet  motions between visits -> months suspect/artifact
    dots/<obs_id>D<det>.parquet       the dots of each photo in the response (a few columns); a zone whose link
                                      read another folder (summary.json steps.link.dots_dir, e.g. dots_deblend1e-4/
                                      for the featured targets' finer deblending) is read from that folder

Zone tables are kept in memory (TABLES_KEEP zones, only the columns used: the hunt zone's take
~25 MB); dot files are read per request, columns only, then cut to the field.

load() fetches (run it while the cutouts download), assemble() classifies once the epochs and
known[] exist, build() does both. None of them raises: a failure gives (None, "unavailable").

Dots only (stage "extracted"): where no zone has combined results yet but the photos' dot files exist
(zones extracted overnight), the dots alone are returned: classed against gaia[] (2"), known[] (10")
and the flag rules; on the months clock each visit's dots are merged within 2" first. No tracks, no
shifts. SKYSHIFT_MEASURED_DOTS_ONLY=0 turns this off.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import numpy as np
from skyshift_core import MASK_BITS, SAT_BITS

log = logging.getLogger(__name__)

VERSION = "1"  # part of the frames cache key (app.py)
DATA_ROOT = os.environ.get("SKYSHIFT_DATA_ROOT") or "gs://nasa-glazing-data"

SOURCE_MATCH_ARCSEC = 4.0    # a dot belongs to a linked source within this (link's FoF radius)
KNOWN_DOT_ARCSEC = 10.0      # a lone dot this close to a known[] prediction of its epoch is that object
KNOWN_TRACK_ARCSEC = 30.0    # a mover this close to a known[] prediction is that object (the suspects rule)
NEAR_GAIA_ARCSEC = 6.0
ELONG_MAX = 3.0
MAX_PER_EPOCH = 250
# stage "extracted": the brightest this many dots an epoch, any class (~225 bytes each as compact JSON: a live
# hours answer of 6 epochs ~0.55 MB, a static 20-epoch set ~1.8 MB raw, ~0.2-0.4 MB gzipped)
EXTRACTED_MAX_PER_EPOCH = 400
SHIFT_MIN_SNR = 3.0
MAX_SHIFTS = 300
# Dot files read per visit epoch: every photo of the visit in the response (contracts/api.md: "the dots of that visit's
# photos in this response"), spread over the visit only beyond this. Live stacks hold 6 photos (blink.LIVE_PER_VISIT);
# the static ones every exposure of the visit (43 at most in web/public/data on 15 Nov). It was 12: a source dotted only
# in the skipped photos of a 19-photo visit had no detection although the plate (all 19) shows it.
MONTHS_MAX_PHOTOS_PER_VISIT = 64
ZONES_TTL_S = 300.0
# Zones not in skyrun/progress.json, looked for by name (results/<name>/summary.json) in case the folder
# listing misses them: the pilot, the test fields and the featured targets' zones (pipeline config.ZONES).
# SKYSHIFT_EXTRA_ZONES (comma list) adds more.
EXTRA_ZONES = ("pilot", "t_cygnus", "t_gc", "t_lmc", "t_smc", "t_nep", "t_sep", "t_ngp",
               "t_barnard", "t_epsindib", "t_wise0855", "t_rwaur", "t_rdra")
EXISTS_TTL_S = 600.0         # a dot file's presence (or absence) is remembered this long
DOTS_ONLY_STAR_ARCSEC = 2.0  # dots only: a dot this close to a gaia[] star (moved to its time) is that star
FOF_ARCSEC = 2.0             # dots only, months: a visit's dots are merged within this
TABLES_TTL_S = 1800.0
TABLES_KEEP = 3
IO_THREADS = 8

# Record-only bits that make a mover or a lone dot an artefact (contracts/flags.md). Not SOURCE (never
# masked, on most pixels) and not OUTLIER (52% of MPC-matched movers have it at the peak).
ART_BITS = {"crosstalk": 20, "ghost": 22, "ghost_fpa": 23, "ghost_ext": 24, "bloom": 26, "halo": 28}
ART_MASK = sum(1 << b for b in ART_BITS.values())
PEAK_MASK = MASK_BITS & ~SAT_BITS  # a mask bit at the peak, saturation aside
CLASSES = ("star", "known", "mover", "suspect", "artifact", "other")
_KEEP_FIRST = {"known", "mover", "suspect", "artifact"}
# v3.13: sources only the pipeline's visit stacks found (results/<zone>/stack_sources.parquet), months clock only
STACK_CLASS = "stack"
STACK_MIN_SNR = 5.0          # a stack source is drawn in a visit where its forced S/N over that visit is at least this
STACK_LINKED_ARCSEC = 3.0    # a stack source with a linked source this close is that source (pipeline STACK_SINGLE_MATCH_ARCSEC)
STACK_VISIT_TOL_DAYS = 20.0  # a stack visit (its photos' median MJD) belongs to the epoch whose span holds it, ± this

DOT_COLS = ["mjd", "ra", "dec", "pos_err_arcsec", "snr", "flux_ujy", "flags", "flags_peak", "elong"]
DOT_V4 = ["ra_psf", "dec_psf", "psf_pos_ok", "pos_err_psf_arcsec"]


def enabled() -> bool:
    return os.environ.get("SKYSHIFT_MEASURED", "1") != "0"


def dots_only_enabled() -> bool:
    return os.environ.get("SKYSHIFT_MEASURED_DOTS_ONLY", "1") != "0"


def stack_enabled(zone: str | None = None) -> bool:
    """v3.13 class `stack`: off until SKYSHIFT_MEASURED_STACK is set, because the site reads CLASS_INFO[class] and an
    unknown class would break its measured layer (web/src/blink/measured.ts); turn on once the site knows it.
    "1": every zone with a stack table; otherwise a list of zones ("+", "," or spaces between names): only those.
    15 Nov: t_rdra is left out, its Mira's glow gives a ring of uncatalogued stack pieces 30–60″ out, beyond the
    stack step's artifact zones (8″, 20–30″)."""
    v = os.environ.get("SKYSHIFT_MEASURED_STACK", "0").strip()
    if v in ("", "0"):
        return False
    if v == "1":
        return True
    names = {x for x in re.split(r"[+,\s]+", v) if x}
    return zone is None or zone in names


# --- storage ----------------------------------------------------------------------------------


def _gcs_client():
    from google.cloud import storage

    try:
        return storage.Client()
    except OSError:  # no project in the environment (a laptop): listing needs none, any will do
        return storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or "nasa-glazing")


def gcs_prefixes(bucket: str, prefix: str) -> list[str]:
    """Names of the "folders" right under gs://<bucket>/<prefix> (prefix ends with "/")."""
    it = _gcs_client().list_blobs(bucket, prefix=prefix, delimiter="/")
    for _ in it:  # the prefixes fill in as the pages are read
        pass
    return sorted(p[len(prefix):].strip("/") for p in it.prefixes if p[len(prefix):].strip("/"))


class Store:
    """The data bucket (gs://...) or a local folder, read with pyarrow."""

    def __init__(self, root: str | None = None) -> None:
        import pyarrow.fs as pafs

        self.root = root or DATA_ROOT
        if self.root.startswith("gs://"):
            self.fs = pafs.GcsFileSystem(retry_time_limit=timedelta(seconds=10))
            self.base = self.root[5:].rstrip("/")
        else:
            self.fs = pafs.LocalFileSystem()
            self.base = os.path.abspath(self.root)

    def path(self, key: str) -> str:
        return f"{self.base}/{key}"

    def read_json(self, key: str) -> Any | None:
        try:
            with self.fs.open_input_stream(self.path(key)) as f:
                return json.loads(f.read())
        except FileNotFoundError:
            return None

    def exists(self, key: str) -> bool:
        import pyarrow.fs as pafs

        return self.fs.get_file_info(self.path(key)).type != pafs.FileType.NotFound

    def list_dirs(self, key: str) -> list[str]:
        """Folder names directly under `key`. On GCS a "folder" is only a name prefix and pyarrow's
        listing shows none (14 Nov: results/ gave just its two loose files), so gs:// roots ask the
        storage client for prefixes with a "/" delimiter."""
        import pyarrow.fs as pafs

        if self.root.startswith("gs://"):
            try:
                return gcs_prefixes(self.base, f"{key.strip('/')}/")
            except Exception as e:  # noqa: BLE001 - fall back to pyarrow (and EXTRA_ZONES)
                log.warning("measured: GCS prefix listing of %s failed: %r", key, e)
        try:
            infos = self.fs.get_file_info(pafs.FileSelector(self.path(key), recursive=False))
        except FileNotFoundError:
            return []
        return sorted(i.base_name for i in infos if i.type == pafs.FileType.Directory)


    def read_table(self, key: str, columns: list[str], optional: list[str] = ()) -> dict[str, np.ndarray] | None:
        """Columns of a Parquet file as numpy arrays (lists stay Python lists); None if missing.
        `optional` columns are read when the file has them."""
        import pyarrow.parquet as pq

        try:
            f = self.fs.open_input_file(self.path(key))
        except FileNotFoundError:
            return None
        with f:
            pf = pq.ParquetFile(f)
            names = set(pf.schema_arrow.names)
            lacking = [c for c in columns if c not in names]
            if lacking:
                raise ValueError(f"{key} has no column {lacking}")
            table = pf.read(columns=list(columns) + [c for c in optional if c in names])
        out = {}
        for name in table.column_names:
            col = table.column(name)
            if col.type.num_fields or str(col.type).startswith(("list", "large_list", "fixed_size_list")):
                out[name] = col.to_pylist()
            elif str(col.type) in ("string", "large_string"):
                out[name] = np.asarray(col.to_pylist(), dtype=object)
            else:
                out[name] = col.to_numpy(zero_copy_only=False)
        return out


_stores: dict[str, Store] = {}
_stores_lock = threading.Lock()


def store(root: str | None = None) -> Store:
    root = root or DATA_ROOT
    with _stores_lock:
        if root not in _stores:
            _stores[root] = Store(root)
        return _stores[root]


# --- zones ------------------------------------------------------------------------------------


@dataclass
class Zone:
    name: str
    ra: tuple[float, float]
    dec: tuple[float, float]
    usable: bool  # results are combined
    stage: str  # "final" | "provisional"
    listed: bool  # in skyrun/progress.json
    dots_dir: str = "dots"  # the folder of dot files its link read (summary.json steps.link.dots_dir)

    def contains(self, ra: float, dec: float) -> bool:
        if not self.dec[0] <= dec <= self.dec[1]:
            return False
        return any(self.ra[0] <= r <= self.ra[1] for r in (ra, ra + 360.0, ra - 360.0))

    @property
    def area(self) -> float:
        return (self.ra[1] - self.ra[0]) * (self.dec[1] - self.dec[0])

    def rank(self) -> tuple:
        """A target zone t_<slug> first, then the sky zones of progress.json (final first), then others."""
        return (0 if self.name.startswith("t_") else 1 if self.listed else 2,
                0 if self.stage == "final" else 1, self.area, self.name)

    def box(self) -> dict:
        return {"ra": [self.ra[0], self.ra[1]], "dec": [self.dec[0], self.dec[1]]}


def _zone_from_summary(name: str, s: dict) -> Zone | None:
    z = s.get("zone") or {}
    if not z.get("ra") or not z.get("dec"):
        return None
    steps = s.get("steps") or {}
    link = steps.get("link") if isinstance(steps.get("link"), dict) else {}
    return Zone(name, (float(z["ra"][0]), float(z["ra"][1])), (float(z["dec"][0]), float(z["dec"][1])),
                usable="link" in steps and "crossid" in steps,
                stage="final" if "calib" in steps else "provisional", listed=False,
                dots_dir=str(link.get("dots_dir") or "dots"))


_zones_cache: dict[str, tuple[float, list[Zone]]] = {}
_zones_lock = threading.Lock()


def zones(st: Store) -> list[Zone]:
    """Every zone we know of: progress.json's, plus results/<name>/ folders not listed there."""
    now = time.monotonic()
    with _zones_lock:
        hit = _zones_cache.get(st.root)
        if hit and now - hit[0] < ZONES_TTL_S:
            return hit[1]
    out: list[Zone] = []
    progress = st.read_json("skyrun/progress.json") or {}
    for z in progress.get("zones") or []:
        try:
            out.append(Zone(str(z["name"]), (float(z["ra"][0]), float(z["ra"][1])),
                            (float(z["dec"][0]), float(z["dec"][1])),
                            usable=z.get("state") == "combined" or bool(z.get("combined_at")),
                            stage="final" if (z.get("stage") == "final" or z.get("final") is True) else "provisional",
                            listed=True))
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    listed = {z.name for z in out}
    try:
        found = st.list_dirs("results")
    except Exception as e:  # noqa: BLE001 - the names below still get looked for
        log.warning("measured: listing results/ failed: %r", e)
        found = []
    extra = [n.strip() for n in os.environ.get("SKYSHIFT_EXTRA_ZONES", "").split(",") if n.strip()]
    names = [n for n in dict.fromkeys([*found, *EXTRA_ZONES, *extra]) if n not in listed]

    def summary(name: str) -> Zone | None:
        try:
            s = st.read_json(f"results/{name}/summary.json")
        except Exception as e:  # noqa: BLE001 - one unreadable summary skips that zone only
            log.warning("measured: results/%s/summary.json unreadable: %r", name, e)
            return None
        return _zone_from_summary(name, s) if isinstance(s, dict) else None

    out += [z for z in _io.map(summary, names) if z is not None]  # in parallel: ~15 small reads
    with _zones_lock:
        _zones_cache[st.root] = (now, out)
    return out


def find_zone(ra: float, dec: float, *, root: str | None = None, prefer: str | None = None) -> tuple[Zone | None, str | None]:
    """(zone, None) for the best combined zone covering (ra, dec), else (None, reason code).
    prefer: use this zone if it has results (precompute's --zone)."""
    st = store(root)
    if prefer:
        s = st.read_json(f"results/{prefer}/summary.json")
        z = _zone_from_summary(prefer, s) if isinstance(s, dict) else None
        listed = [x for x in zones(st) if x.name == prefer]
        if listed:
            z = listed[0]
        if z is None:
            return None, "not_processed"
        return (z, None) if z.usable else (None, "processing")
    covering = [z for z in zones(st) if z.contains(ra, dec)]
    usable = sorted((z for z in covering if z.usable), key=Zone.rank)
    if not usable:
        return None, "processing" if covering else "not_processed"
    return usable[0], None


def covering_zone(ra: float, dec: float, *, root: str | None = None) -> Zone | None:
    """The best zone covering (ra, dec) whatever its state (for dots only: the zone being processed)."""
    zs = sorted((z for z in zones(store(root)) if z.contains(ra, dec)), key=Zone.rank)
    return zs[0] if zs else None


def reset() -> None:
    """For tests: forget cached zones, tables, stores and dot-file presence."""
    with _zones_lock:
        _zones_cache.clear()
    with _exists_lock:
        _exists.clear()
    with _tables_lock:
        _tables.clear()
    with _stores_lock:
        _stores.clear()


# --- zone tables ------------------------------------------------------------------------------


@dataclass
class Tables:
    zone: Zone
    # linked sources, sorted by dec
    src_id: np.ndarray
    src_ra: np.ndarray
    src_dec: np.ndarray
    src_gaia: np.ndarray  # int64, -1 = none
    # movers
    mv: dict[str, Any]
    suspects: dict[tuple[int, int], tuple[str, str | None, list[str]]]
    # astrometry rows with pm_snr >= SHIFT_MIN_SNR
    ast: dict[str, np.ndarray]
    highpm: dict[int, tuple[str, list[str]]]  # source_id -> ("suspect" | "artifact", reasons)
    # results/<zone>/fragments.parquet (v3.11), tagged rows only: source_id -> (tag, parent_gaia_id, blend_gaia_ids);
    # None when the zone has no such file (then nothing changes)
    frag: dict[int, tuple[str, str | None, list[str] | None]] | None = None
    # results/<zone>/shape_candidates.parquet (v3.12), rows the two-source fit split: source_id -> the companion
    # (_load_companions); None without the file or its fit columns (then nothing changes)
    companions: dict[int, dict] | None = None
    # results/<zone>/stack_sources.parquet (v3.13), the rows drawn as class stack or artifact; None without the file
    stack: dict[str, Any] | None = None
    loaded: float = field(default_factory=time.monotonic)

    def nbytes(self) -> int:
        n = sum(a.nbytes for a in (self.src_id, self.src_ra, self.src_dec, self.src_gaia))
        n += sum(a.nbytes for a in self.ast.values())
        n += sum(a.nbytes for a in (self.stack or {}).values() if isinstance(a, np.ndarray))
        return n + 200 * len(self.mv.get("ra", ())) + 150 * len(self.frag or ())


def _mkey(cell: int, mjd: float) -> tuple[int, int]:
    return int(cell), int(round(float(mjd) * 86400.0))


def _split(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, np.ndarray)):
        return [str(x) for x in v if x is not None and str(x) != ""]
    return [p.strip() for p in str(v).split(",") if p.strip()]


def _load_tables(st: Store, zone: Zone) -> Tables:
    t0 = time.perf_counter()
    base = f"results/{zone.name}"
    src = st.read_table(f"{base}/crossids.parquet", ["source_id", "ra", "dec"], ["gaia_id"])
    if src is None:
        src = st.read_table(f"{base}/fixed.parquet", ["source_id", "ra", "dec"])
    if src is None:
        raise FileNotFoundError(f"{base}: no crossids or fixed table")
    order = np.argsort(src["dec"], kind="stable")
    gaia = src.get("gaia_id")
    gaia = (np.full(len(order), -1, np.int64) if gaia is None
            else np.nan_to_num(np.asarray(gaia, float), nan=-1).astype(np.int64)[order]
            if np.asarray(gaia).dtype.kind == "f" else np.asarray(gaia, np.int64)[order])

    mv = st.read_table(f"{base}/movers.parquet",
                       ["ra", "dec", "mjd", "evidence", "flags", "obs_ids", "detectors", "cell"],
                       ["dot_obs", "dot_det", "dot_row", "dot_mjd"]) or {}
    if mv:
        mv["obs_set"] = [set(_split(s)) for s in mv["obs_ids"]]
        mv["det_set"] = [{int(d) for d in _split(s)} for s in mv["detectors"]]
        del mv["obs_ids"], mv["detectors"]

    suspects: dict[tuple[int, int], tuple[str, str | None, list[str]]] = {}
    sp = st.read_table(f"{base}/suspects.parquet", ["id", "cell", "mjd"], ["grade", "reasons"])
    if sp:
        for i in range(len(sp["id"])):
            grade = sp["grade"][i] if "grade" in sp else None
            grade = grade if grade in ("A", "B", "C") else None
            reasons = _split(sp["reasons"][i]) if "reasons" in sp else []
            suspects[_mkey(sp["cell"][i], sp["mjd"][i])] = (str(sp["id"][i]), grade, reasons)

    ast: dict[str, np.ndarray] = {}
    a = st.read_table(f"{base}/astrometry.parquet",
                      ["source_id", "ra", "dec", "epoch_mjd", "pmra", "pmdec", "pmra_err", "pmdec_err", "pm_snr"])
    if a:
        keep = np.nan_to_num(np.asarray(a["pm_snr"], float), nan=0.0) >= SHIFT_MIN_SNR
        ast = {k: np.asarray(v)[keep] for k, v in a.items()}

    highpm: dict[int, tuple[str, list[str]]] = {}
    h = st.read_table(f"{base}/highpm_candidates.parquet", ["source_id"],
                      ["source_id_b", "status", "vetted", "reason"])
    if h:
        n = len(h["source_id"])
        for i in range(n):
            vetted = bool(h["vetted"][i]) if "vetted" in h else False
            status = str(h["status"][i]) if "status" in h else ""
            reasons = _split(h["reason"][i]) if "reason" in h else []
            ids = [int(h["source_id"][i])]
            if "source_id_b" in h and int(h["source_id_b"][i]) >= 0:
                ids.append(int(h["source_id_b"][i]))
            cls = "suspect" if (vetted and status == "new") else None if vetted else "artifact"
            for sid in ids:
                if cls == "suspect" or (cls == "artifact" and sid not in highpm):
                    highpm[sid] = (cls, reasons if cls == "artifact" else [])
    tables = Tables(zone, np.asarray(src["source_id"], np.int64)[order], np.asarray(src["ra"], float)[order],
                    np.asarray(src["dec"], float)[order], gaia, mv, suspects, ast, highpm,
                    _load_fragments(st, base), _load_companions(st, base),
                    stack=_load_stack(st, base, np.asarray(src["ra"], float), np.asarray(src["dec"], float))
                    if stack_enabled(zone.name) else None)
    log.info("measured: zone %s tables loaded in %.1f s (%d sources, %d movers, %d suspects, %d shifts, %.1f MB)",
             zone.name, time.perf_counter() - t0, len(order), len(mv.get("ra", ())), len(suspects),
             len(ast.get("source_id", ())), tables.nbytes() / 1e6)
    return tables


def _load_fragments(st: Store, base: str) -> dict[int, tuple[str, str | None, list[str] | None]] | None:
    """The pipeline's `fragments` tags (contracts/dots.md): bright_fragment, duplicate and blend rows, or None without
    the file (or unreadable: the layer then reads as before). A duplicate (a second source of one star) is read as a
    bright_fragment whose parent is that star, so the site draws the star once (no new API reason)."""
    try:
        f = st.read_table(f"{base}/fragments.parquet", ["source_id", "tag"], ["parent_gaia_id", "blend_ids"])
    except Exception:  # noqa: BLE001
        log.warning("measured: %s/fragments.parquet unreadable; no fragment tags", base, exc_info=True)
        return None
    if f is None:
        return None
    out: dict[int, tuple[str, str | None, list[str] | None]] = {}
    for i, tag in enumerate(f["tag"]):
        if tag in ("bright_fragment", "duplicate"):
            p = int(f["parent_gaia_id"][i]) if "parent_gaia_id" in f else -1
            out[int(f["source_id"][i])] = ("bright_fragment", str(p) if p >= 0 else None, None)
        elif tag == "blend":
            ids = [str(int(x)) for x in (f["blend_ids"][i] or [])] if "blend_ids" in f else []
            out[int(f["source_id"][i])] = (tag, None, ids or None)
    return out


def _load_stack(st: Store, base: str, src_ra: np.ndarray, src_dec: np.ndarray) -> dict[str, Any] | None:
    """The stack sources the measured layer draws (contracts/dots.md stack_sources, api.md v3.13): no linked source of
    this zone within STACK_LINKED_ARCSEC (the table's own source_id -1, checked again against the linked sources read
    now, in case link ran again after stack), not transient. Per-visit S/N, flux and position as (n, visits) arrays,
    sorted by dec. None without the file, or when it can't be read (the layer then reads as before)."""
    try:
        t = st.read_table(f"{base}/stack_sources.parquet",
                          ["ra", "dec", "source_id", "transient", "artifact_zone", "n_photos", "visits", "mjd_visit",
                           "snr_visit", "flux_visit_ujy"],
                          ["gaia_ref_id", "visits_free", "ra_visit", "dec_visit"])
    except Exception:  # noqa: BLE001
        log.warning("measured: %s/stack_sources.parquet unreadable; no stack class", base, exc_info=True)
        return None
    if t is None:
        return None
    keep = (np.asarray(t["source_id"], np.int64) < 0) & ~np.asarray(t["transient"], bool)
    keep[keep] = ~_near_any(np.asarray(t["ra"], float)[keep], np.asarray(t["dec"], float)[keep], src_ra, src_dec,
                            STACK_LINKED_ARCSEC)
    rows = np.nonzero(keep)[0]
    rows = rows[np.argsort(np.asarray(t["dec"], float)[rows], kind="stable")]
    visits = [int(v) for v in (t["visits"][0] if len(t["visits"]) else [])]
    mjd = np.asarray(t["mjd_visit"][0] if len(t["mjd_visit"]) else [], float)
    col = {v: j for j, v in enumerate(visits)}
    n, k = len(rows), len(visits)
    snr, flux = np.full((n, k), np.nan), np.full((n, k), np.nan)
    rav, decv = np.full((n, k), np.nan), np.full((n, k), np.nan)
    free = all(c in t for c in ("visits_free", "ra_visit", "dec_visit"))
    for a, i in enumerate(rows):
        if len(t["snr_visit"][i]) == k:
            snr[a] = np.asarray(t["snr_visit"][i], float)
            flux[a] = np.asarray(t["flux_visit_ujy"][i], float)
        if free:
            for v, r, d in zip(t["visits_free"][i] or [], t["ra_visit"][i] or [], t["dec_visit"][i] or []):
                if int(v) in col:
                    rav[a, col[int(v)]], decv[a, col[int(v)]] = r, d
    gaia = np.full(n, None, object)
    if "gaia_ref_id" in t:
        g = np.asarray(t["gaia_ref_id"], object)[rows]
        gaia = np.array([str(x).removeprefix("gaia:") if x else None for x in g], object)
    return {"ra": np.asarray(t["ra"], float)[rows], "dec": np.asarray(t["dec"], float)[rows],
            "artifact": np.asarray(t["artifact_zone"], bool)[rows], "n_photos": np.asarray(t["n_photos"], int)[rows],
            "gaia": gaia, "mjd": mjd, "snr": snr, "flux": flux, "ra_v": rav, "dec_v": decv}


def _near_any(ra: np.ndarray, dec: np.ndarray, ref_ra: np.ndarray, ref_dec: np.ndarray, r_arcsec: float) -> np.ndarray:
    """True where a reference point lies within r_arcsec (unit vectors: any part of the sky)."""
    from scipy.spatial import cKDTree

    if not len(ra) or not len(ref_ra):
        return np.zeros(len(ra), bool)

    def unit(a, d):
        a, d = np.radians(a), np.radians(d)
        return np.column_stack([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)])

    chord = 2.0 * math.sin(math.radians(r_arcsec / 7200.0))
    dist, _ = cKDTree(unit(ref_ra, ref_dec)).query(unit(ra, dec), distance_upper_bound=chord)
    return np.isfinite(dist)


def _months_stack(ep: dict, era: float, edec: float, half: float, S: dict[str, Any]) -> list[dict]:
    """v3.13: the stack sources in this months epoch's square, from the stack visit its time span holds, where their
    forced S/N over that visit is >= STACK_MIN_SNR: class stack, or artifact (reason stack_near_bright) in a bright
    source's artifact zone. Position: the visit's free fit, else the stack position."""
    if not len(S["mjd"]) or ep.get("mjd_mid") is None:
        return []
    lo = float(ep.get("mjd_start") or ep["mjd_mid"]) - STACK_VISIT_TOL_DAYS
    hi = float(ep.get("mjd_end") or ep["mjd_mid"]) + STACK_VISIT_TOL_DAYS
    inside = np.nonzero((S["mjd"] >= lo) & (S["mjd"] <= hi))[0]
    if not inside.size:
        return []
    j = int(inside[np.argmin(np.abs(S["mjd"][inside] - float(ep["mjd_mid"])))])
    a, b = np.searchsorted(S["dec"], [edec - half - 1.0 / 60.0, edec + half + 1.0 / 60.0])
    idx = np.arange(a, b)
    ra = np.where(np.isfinite(S["ra_v"][idx, j]), S["ra_v"][idx, j], S["ra"][idx])
    dec = np.where(np.isfinite(S["dec_v"][idx, j]), S["dec_v"][idx, j], S["dec"][idx])
    ok = _in_square(ra, dec, era, edec, half) & (np.nan_to_num(S["snr"][idx, j], nan=-1.0) >= STACK_MIN_SNR)
    out = []
    for q in np.nonzero(ok)[0]:
        i = idx[q]
        art = bool(S["artifact"][i])
        out.append(_det(ep["id"], float(ra[q]) % 360.0, float(dec[q]), float(S["snr"][i, j]), float(S["flux"][i, j]),
                        int(S["n_photos"][i]), "artifact" if art else STACK_CLASS, gaia_id=S["gaia"][i],
                        reasons=["stack_near_bright"] if art else []))
    return out


def _apply_fragments(dets: list[dict], shifts: list[dict], frag: dict) -> None:
    """v3.11: a linked source tagged bright_fragment is an `artifact` (+ reason `bright_fragment`, `parent_gaia_id`);
    one tagged blend keeps its class and gains the reason `blend` and `blend_gaia_ids` (not on an artifact). Every
    detection gets both fields (null when they don't apply). Suspects and known objects keep their class."""
    for d in dets:
        d.setdefault("parent_gaia_id", None)
        d.setdefault("blend_gaia_ids", None)
        sid = d.get("source_id")
        tag, parent, blend = frag.get(int(sid), (None, None, None)) if sid is not None else (None, None, None)
        if tag == "bright_fragment" and d["class"] in ("star", "other", "artifact"):
            why = list(d["reasons"]) if d["class"] == "artifact" else []  # e.g. a high-pm row's codes stay
            d["class"], d["reasons"], d["parent_gaia_id"] = "artifact", why + ["bright_fragment"], parent
        elif tag == "blend" and d["class"] != "artifact":
            if "blend" not in d["reasons"]:
                d["reasons"] = list(d["reasons"]) + ["blend"]
            d["blend_gaia_ids"] = blend
    for s in shifts:
        if frag.get(int(s["source_id"]), ("",))[0] == "bright_fragment" and s["class"] in ("star", "other"):
            s["class"] = "artifact"


COMPANION_COLS = ["companion_split", "companion_ra", "companion_dec", "companion_flux_ratio",
                  *[f"companion_flux_ratio_d{d}" for d in range(1, 7)], *[f"companion_snr_d{d}" for d in range(1, 7)],
                  "companion_visit_mjd", "companion_visit_ra", "companion_visit_dec"]
COMPANION_VISIT_DAYS = 30.0  # a visit's own fit is used for an epoch whose span it falls in, ± this


def _load_companions(st: Store, base: str) -> dict[int, dict] | None:
    """v3.12: the shape candidates' second objects the two-source fit split off (shape_candidates.parquet rows with
    companion_split), by the source they were merged into; None without the file or its fit columns."""
    try:
        t = st.read_table(f"{base}/shape_candidates.parquet", ["source_id"], COMPANION_COLS)
    except Exception:  # noqa: BLE001
        log.warning("measured: %s/shape_candidates.parquet unreadable; no fitted companions", base, exc_info=True)
        return None
    if t is None or "companion_split" not in t or "companion_ra" not in t:
        return None
    out: dict[int, dict] = {}

    def f(name: str, i: int) -> float:
        v = t[name][i] if name in t else None
        return float(v) if v is not None and np.isfinite(float(v)) else float("nan")

    def lst(name: str, i: int) -> list:
        return list((t[name][i] if name in t else None) or [])

    for i, sid in enumerate(t["source_id"]):
        split = t["companion_split"][i]
        if split is None or not bool(split):
            continue
        ra, dec = f("companion_ra", i), f("companion_dec", i)
        if not (math.isfinite(ra) and math.isfinite(dec)):
            continue
        vis = [(float(m), float(a), float(b)) for m, a, b in
               zip(lst("companion_visit_mjd", i), lst("companion_visit_ra", i), lst("companion_visit_dec", i))]
        out[int(sid)] = {"ra": ra, "dec": dec, "visits": vis, "ratio": f("companion_flux_ratio", i),
                         "ratio_d": {d: f(f"companion_flux_ratio_d{d}", i) for d in range(1, 7)},
                         "snr_d": {d: f(f"companion_snr_d{d}", i) for d in range(1, 7)}}
    return out


def _companion_dets(dets: list[dict], epochs: list[dict], comp: dict[int, dict]) -> list[dict]:
    """v3.12: one `other` detection (reason split_by_fit, no source, no dots) per visit epoch for each fitted companion
    whose source has a detection there: at that visit's own fitted position when the epoch's span holds it, else the
    overall fit; flux = the source's detection × the companion's ratio in the epoch's detector (overall ratio if none)."""
    by_id = {e["id"]: e for e in epochs}
    out = []
    for d in dets:
        sid = d.get("source_id")
        c = comp.get(int(sid)) if sid is not None else None
        if c is None:
            continue
        ep = by_id.get(d["epoch_id"], {})
        ra, dec = c["ra"], c["dec"]
        lo = float(ep.get("mjd_start") or ep.get("mjd_mid") or float("nan")) - COMPANION_VISIT_DAYS
        hi = float(ep.get("mjd_end") or ep.get("mjd_mid") or float("nan")) + COMPANION_VISIT_DAYS
        inside = [v for v in c["visits"] if lo <= v[0] <= hi]
        if inside:
            mid = float(ep.get("mjd_mid") or inside[0][0])
            _, ra, dec = min(inside, key=lambda v: abs(v[0] - mid))
        det = int(ep["det"]) if ep.get("det") is not None else None
        ratio = c["ratio_d"].get(det, float("nan")) if det else float("nan")
        if not math.isfinite(ratio):
            ratio = c["ratio"]
        snr = c["snr_d"].get(det, float("nan")) if det else float("nan")
        if not math.isfinite(snr):
            snr = max([v for v in c["snr_d"].values() if math.isfinite(v)], default=0.0)
        if d.get("flux_ujy") is None or not math.isfinite(ratio):
            continue
        flux = d["flux_ujy"] * ratio
        out.append(_det(d["epoch_id"], ra % 360.0, dec, snr, flux, 0, "other", reasons=["split_by_fit"]))
    return out


_tables: OrderedDict[tuple, Tables] = OrderedDict()
_tables_lock = threading.Lock()
_zone_locks: dict[tuple, threading.Lock] = defaultdict(threading.Lock)


def tables_for(st: Store, zone: Zone) -> Tables:
    key = (st.root, zone.name, zone.stage)
    with _tables_lock:
        t = _tables.get(key)
        if t is not None and time.monotonic() - t.loaded < TABLES_TTL_S:
            _tables.move_to_end(key)
            return t
        lock = _zone_locks[key]
    with lock:  # one load per zone, even when several requests start together
        with _tables_lock:
            t = _tables.get(key)
            if t is not None and time.monotonic() - t.loaded < TABLES_TTL_S:
                return t
        t = _load_tables(st, zone)
        with _tables_lock:
            _tables[key] = t
            _tables.move_to_end(key)
            while len(_tables) > TABLES_KEEP:
                _tables.popitem(last=False)
        return t


# --- dots -------------------------------------------------------------------------------------


@dataclass
class Dots:
    """The dots of one photo near the field. Positions: ePSF where psf_pos_ok, else the centroid."""

    mjd: float
    row: np.ndarray
    ra: np.ndarray
    dec: np.ndarray
    pos_err: np.ndarray
    snr: np.ndarray
    flux: np.ndarray
    flags: np.ndarray
    fpk: np.ndarray
    elong: np.ndarray


def _sep_mask(ra: np.ndarray, dec: np.ndarray, ra0: float, dec0: float, r_deg: float) -> np.ndarray:
    dx = ((ra - ra0 + 180.0) % 360.0 - 180.0) * np.cos(np.radians(dec))
    return dx * dx + (dec - dec0) ** 2 <= r_deg * r_deg


def read_dots(st: Store, key: str, ra0: float, dec0: float, r_deg: float, folder: str = "dots") -> Dots | None:
    t = st.read_table(f"{folder}/{key}.parquet", DOT_COLS, DOT_V4)
    if t is None:
        return None
    ra, dec = np.asarray(t["ra"], float), np.asarray(t["dec"], float)
    pe = np.asarray(t["pos_err_arcsec"], float)
    if "psf_pos_ok" in t and "ra_psf" in t:
        ok = np.asarray(t["psf_pos_ok"], bool)
        ra = np.where(ok, np.asarray(t["ra_psf"], float), ra)
        dec = np.where(ok, np.asarray(t["dec_psf"], float), dec)
        if "pos_err_psf_arcsec" in t:
            pe = np.where(ok, np.asarray(t["pos_err_psf_arcsec"], float), pe)
    sel = np.nonzero(_sep_mask(ra, dec, ra0, dec0, r_deg))[0]
    mjd = np.asarray(t["mjd"], float)
    return Dots(mjd=float(mjd[0]) if len(mjd) else float("nan"), row=sel.astype(np.int32), ra=ra[sel], dec=dec[sel],
                pos_err=np.nan_to_num(pe[sel], nan=1.0), snr=np.asarray(t["snr"], float)[sel],
                flux=np.asarray(t["flux_ujy"], float)[sel], flags=np.asarray(t["flags"], np.int64)[sel],
                fpk=np.asarray(t["flags_peak"], np.int64)[sel], elong=np.asarray(t["elong"], float)[sel])


_io = ThreadPoolExecutor(IO_THREADS, thread_name_prefix="measured-io")
_exists: dict[tuple[str, str], tuple[float, bool]] = {}
_exists_lock = threading.Lock()


def _known_presence(st: Store, key: str) -> bool | None:
    with _exists_lock:
        hit = _exists.get((st.root, key))
    return hit[1] if hit and time.monotonic() - hit[0] < EXISTS_TTL_S else None


def _remember(st: Store, key: str, present: bool) -> None:
    with _exists_lock:
        if len(_exists) > 100_000:
            _exists.clear()
        _exists[(st.root, key)] = (time.monotonic(), present)


def dot_exists(st: Store, key: str) -> bool:
    """Whether dots/<key>.parquet exists (one metadata request, remembered EXISTS_TTL_S)."""
    known = _known_presence(st, key)
    if known is None:
        known = st.exists(f"dots/{key}.parquet")
        _remember(st, key, known)
    return known


def _read_dots_remembered(st: Store, key: str, ra: float, dec: float, r_deg: float, folder: str = "dots") -> Dots | None:
    mkey = key if folder == "dots" else f"{folder}/{key}"  # presence is remembered per folder
    if _known_presence(st, mkey) is False:
        return None  # missing a moment ago: no new request
    d = read_dots(st, key, ra, dec, r_deg, folder)
    _remember(st, mkey, d is not None)
    return d


@dataclass
class Loaded:
    zone: Zone | None = None
    reason: str | None = None  # measured_reason when there is nothing to assemble
    tables: Tables | None = None
    dots: dict[str, Dots | None] = field(default_factory=dict)
    error: bool = False  # a failure (not "nothing here"): live answers are then not cached
    dots_only: bool = False  # no combined zone here: the dots alone (stage "extracted")
    covering: Zone | None = None  # dots only: the zone being processed here, if any


def load(ra: float, dec: float, radius_deg: float, keys: list[str], *, zone: str | None = None,
         root: str | None = None) -> Loaded:
    """Zone, its tables and the dots of `keys` (photo keys <obs_id>D<det>) within radius_deg of (ra, dec).
    Never raises."""
    try:
        st = store(root)
        z, reason = find_zone(ra, dec, root=root, prefer=zone)
        if z is None and not dots_only_enabled():
            return Loaded(reason=reason)
        folder = z.dots_dir if z is not None else "dots"  # the folder the zone's link read
        dots_f = {k: _io.submit(_read_dots_remembered, st, k, ra, dec, radius_deg, folder) for k in dict.fromkeys(keys)}
        tables = tables_for(st, z) if z is not None else None
        dots: dict[str, Dots | None] = {}
        for k, f in dots_f.items():
            try:
                dots[k] = f.result()
            except Exception as e:  # noqa: BLE001 - one unreadable photo counts as missing
                log.warning("measured: %s/%s.parquet unreadable: %r", folder, k, e)
                dots[k] = None
        if z is None:
            return Loaded(reason=reason, dots=dots, dots_only=True, covering=covering_zone(ra, dec, root=root))
        return Loaded(zone=z, tables=tables, dots=dots)
    except Exception:  # noqa: BLE001 - the frames never fail because of this layer
        log.exception("measured: load failed at (%s, %s)", ra, dec)
        return Loaded(reason="unavailable", error=True)


# --- assembling -------------------------------------------------------------------------------


def _r(v: float, n: int) -> float | None:
    return round(float(v), n) if v is not None and math.isfinite(float(v)) else None


def _xy(ra, dec, ra0: float, dec0: float) -> np.ndarray:
    """Local offsets in arcsec (east, north), each point with its own cos(dec)."""
    ra, dec = np.asarray(ra, float), np.asarray(dec, float)
    return np.column_stack((((ra - ra0 + 180.0) % 360.0 - 180.0) * np.cos(np.radians(dec)) * 3600.0,
                            (dec - dec0) * 3600.0))


def _in_square(ra, dec, ra0: float, dec0: float, half_deg: float) -> np.ndarray:
    xy = _xy(ra, dec, ra0, dec0) / 3600.0
    return (np.abs(xy[:, 0]) <= half_deg) & (np.abs(xy[:, 1]) <= half_deg)


def _key_of_exposure(epoch_id: str) -> str | None:
    """'d2-2025W42_2A_0213_1' -> '2025W42_2A_0213_1D2'."""
    head, _, obs = epoch_id.partition("-")
    if not obs or not head.startswith("d") or not head[1:].isdigit():
        return None
    return f"{obs}D{int(head[1:])}"


def _spread(keys: list[str], n: int) -> list[str]:
    if len(keys) <= n:
        return keys
    idx = np.unique(np.rint(np.linspace(0, len(keys) - 1, n)).astype(int))
    return [keys[i] for i in idx]


def _art_reasons(flags: int, fpk: int, elong: float, lone: bool) -> list[str]:
    out = [name for name, bit in ART_BITS.items() if flags & (1 << bit)]
    if lone and fpk & PEAK_MASK:
        out.append("masked_peak")
    if lone and math.isfinite(elong) and elong > ELONG_MAX:
        out.append("elongated")
    return out


def _gaia_at(g: dict, mjd: float) -> tuple[float, float]:
    dt = (mjd - 57388.5) / 365.25
    pmra, pmdec = g.get("pmra") or 0.0, g.get("pmdec") or 0.0
    dec = g["dec"] + pmdec * dt / 3.6e6
    return g["ra"] + pmra * dt / 3.6e6 / max(math.cos(math.radians(g["dec"])), 1e-6), dec


def _det(epoch: str, ra, dec, snr, flux, n_dots, cls, source_id=None, gaia_id=None, known_name=None,
         track_id=None, grade=None, reasons=()) -> dict:
    return {"epoch_id": epoch, "ra": _r(ra, 6), "dec": _r(dec, 6), "snr": _r(snr, 1), "flux_ujy": _r(flux, 1),
            "n_dots": int(n_dots), "class": cls, "source_id": None if source_id is None else int(source_id),
            "gaia_id": gaia_id, "known_name": known_name, "track_id": track_id, "grade": grade,
            "reasons": list(reasons)}


class _Field:
    """Linked sources near the field, with a KD-tree on local arcsec offsets."""

    def __init__(self, T: Tables, ra0: float, dec0: float, r_deg: float) -> None:
        from scipy.spatial import cKDTree

        lo, hi = np.searchsorted(T.src_dec, [dec0 - r_deg, dec0 + r_deg])
        idx = np.arange(lo, hi)
        idx = idx[_sep_mask(T.src_ra[idx], T.src_dec[idx], ra0, dec0, r_deg)]
        self.ra0, self.dec0, self.r_deg = ra0, dec0, r_deg
        self.id, self.ra, self.dec, self.gaia = T.src_id[idx], T.src_ra[idx], T.src_dec[idx], T.src_gaia[idx]
        self.tree = cKDTree(_xy(self.ra, self.dec, ra0, dec0)) if len(idx) else None

    def match(self, ra, dec, r_arcsec: float) -> np.ndarray:
        """Index of the nearest source within r_arcsec of each point, -1 if none."""
        n = len(ra)
        if self.tree is None or n == 0:
            return np.full(n, -1, int)
        d, i = self.tree.query(_xy(ra, dec, self.ra0, self.dec0), distance_upper_bound=r_arcsec)
        return np.where(np.isfinite(d), i, -1)

    def gaia_id(self, i: int) -> str | None:
        return str(int(self.gaia[i])) if i >= 0 and self.gaia[i] >= 0 else None


def _months_class(T: Tables, sid: int, has_gaia: bool) -> tuple[str, list[str]]:
    hp = T.highpm.get(int(sid))
    if hp and hp[0] == "suspect":
        return "suspect", []
    if has_gaia:
        return "star", []
    if hp and hp[0] == "artifact":
        return "artifact", list(hp[1])
    return "other", []


def assemble(loaded: Loaded, meta: dict, epoch_keys: dict[str, list[str]] | None = None) -> tuple[dict | None, str | None]:
    """(measured, measured_reason) for a frames response `meta` (epochs, known, gaia in place).
    epoch_keys: the photo keys (<obs_id>D<det>) of each epoch; hours epochs default to their id.
    Never raises."""
    if loaded.dots_only:
        try:
            return _assemble_dots(loaded, meta, epoch_keys or {})
        except Exception:  # noqa: BLE001
            log.exception("measured: dots-only assemble failed")
            loaded.error = True
            return None, "unavailable"
    if loaded.zone is None or loaded.tables is None:
        return None, loaded.reason or "unavailable"
    try:
        return _assemble(loaded, meta, epoch_keys or {})
    except Exception:  # noqa: BLE001
        log.exception("measured: assemble failed")
        loaded.error = True
        return None, "unavailable"


def _assemble(loaded: Loaded, meta: dict, epoch_keys: dict[str, list[str]]) -> tuple[dict | None, str | None]:
    T, Z = loaded.tables, loaded.zone
    clock = meta["clock"]
    c0 = meta["center"]
    ra0, dec0 = float(c0["ra"]), float(c0["dec"])
    half = meta["size"][0] * float(meta["pixel_arcsec"]) / 2.0 / 3600.0
    epochs = meta.get("epochs") or []
    reach = half * math.sqrt(2.0) + 1.0 / 60.0
    for ep in epochs:  # frames that follow an object: reach every epoch's own centre
        c = ep.get("center")
        if c:
            reach = max(reach, float(np.hypot(*(_xy([c["ra"]], [c["dec"]], ra0, dec0)[0] / 3600.0)))
                        + half * math.sqrt(2.0) + 1.0 / 60.0)
    F = _Field(T, ra0, dec0, reach)

    photos = _photos_of(meta, epoch_keys)
    all_keys = list(dict.fromkeys(k for ks in photos.values() for k in ks))
    have = [k for k in all_keys if loaded.dots.get(k) is not None]
    if not have:
        return None, "no_dots"

    dets: list[dict] = []
    tracks: list[dict] = []
    if clock == "hours":
        dets, tracks = _hours(loaded, meta, epochs, photos, F, T, Z)
    else:
        dets = _months(loaded, meta, epochs, photos, F, T)
    shifts = _shifts(meta, epochs, F, T) if clock == "months" else []
    if clock == "months" and T.companions:
        dets += _companion_dets(dets, epochs, T.companions)
    if T.frag is not None:
        _apply_fragments(dets, shifts, T.frag)
    classes = CLASSES + (STACK_CLASS,) if clock == "months" and T.stack is not None else CLASSES
    return _finish(dets, tracks, shifts, zone=Z.name, stage=Z.stage, zone_box=Z.box(), clock=clock,
                   n_have=len(have), n_all=len(all_keys), classes=classes), None


def _photos_of(meta: dict, epoch_keys: dict[str, list[str]]) -> dict[str, list[str]]:
    """Photo keys per epoch: as given (a visit: all of them up to MONTHS_MAX_PHOTOS_PER_VISIT, then spread over
    it), hours epochs from their id."""
    photos: dict[str, list[str]] = {}
    for ep in meta.get("epochs") or []:
        keys = epoch_keys.get(ep["id"])
        if keys is None and ep.get("kind") == "exposure":
            k = _key_of_exposure(ep["id"])
            keys = [k] if k else []
        keys = list(keys or [])
        if ep.get("kind") == "visit":
            keys = _spread(keys, MONTHS_MAX_PHOTOS_PER_VISIT)
        photos[ep["id"]] = keys
    return photos


def _finish(dets: list[dict], tracks: list[dict], shifts: list[dict], *, zone: str | None, stage: str,
            zone_box: dict | None, clock: str, n_have: int, n_all: int, classes: tuple[str, ...] = CLASSES) -> dict:
    """The `measured` object: counts before the cap, sorted by epoch id then S/N. Cap per epoch:
    MAX_PER_EPOCH with the non-star classes first; stage "extracted": the EXTRACTED_MAX_PER_EPOCH
    brightest (S/N) of every class, so the stars to compare with Gaia stay."""
    counts = {c: 0 for c in classes}
    for d in dets:
        counts[d["class"]] += 1
    by_epoch: dict[str, list[dict]] = defaultdict(list)
    for d in dets:
        by_epoch[d["epoch_id"]].append(d)
    kept, truncated = [], False
    by_snr = stage == "extracted"
    cap = EXTRACTED_MAX_PER_EPOCH if by_snr else MAX_PER_EPOCH
    for eid in sorted(by_epoch):
        part = sorted(by_epoch[eid], key=lambda d: (False if by_snr else d["class"] not in _KEEP_FIRST,
                                                    -(d["snr"] or 0.0)))
        if len(part) > cap:
            truncated = True
            part = part[:cap]
        kept += sorted(part, key=lambda d: -(d["snr"] or 0.0))
    return {
        "zone": zone, "stage": stage, "zone_box": zone_box, "clock": clock,
        "photos": n_have, "photos_missing": n_all - n_have, "truncated": truncated,
        "counts": counts, "detections": kept, "tracks": tracks, "shifts": shifts,
    }


def _nearest(pts_ra, pts_dec, ref_ra, ref_dec, ra0: float, dec0: float, r_arcsec: float) -> np.ndarray:
    """Index of the nearest reference point within r_arcsec of each point, -1 if none."""
    from scipy.spatial import cKDTree

    n = len(pts_ra)
    if n == 0 or len(ref_ra) == 0:
        return np.full(n, -1, int)
    d, i = cKDTree(_xy(ref_ra, ref_dec, ra0, dec0)).query(_xy(pts_ra, pts_dec, ra0, dec0),
                                                          distance_upper_bound=r_arcsec)
    return np.where(np.isfinite(d), i, -1)


def _fof(ra: np.ndarray, dec: np.ndarray, ra0: float, dec0: float, r_arcsec: float) -> np.ndarray:
    """Friends-of-friends group label of each point (linking length r_arcsec)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    n = len(ra)
    if n == 0:
        return np.zeros(0, int)
    pairs = cKDTree(_xy(ra, dec, ra0, dec0)).query_pairs(r_arcsec, output_type="ndarray")
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    return connected_components(graph, directed=False)[1]


def _assemble_dots(loaded: Loaded, meta: dict, epoch_keys: dict[str, list[str]]) -> tuple[dict | None, str | None]:
    """Stage "extracted": the dots alone, where no zone has combined them (module docstring)."""
    clock = meta["clock"]
    photos = _photos_of(meta, epoch_keys)
    all_keys = list(dict.fromkeys(k for ks in photos.values() for k in ks))
    have = [k for k in all_keys if loaded.dots.get(k) is not None]
    if not have:
        return None, loaded.reason or "not_processed"
    gaia = [g for g in meta.get("gaia") or [] if g.get("ra") is not None and g.get("dec") is not None]
    known_by_epoch: dict[str, list[dict]] = defaultdict(list)
    for k in meta.get("known") or []:
        if k.get("epoch_id") is not None and k.get("ra") is not None:
            known_by_epoch[k["epoch_id"]].append(k)
    out: list[dict] = []
    for ep in meta.get("epochs") or []:
        keys = [k for k in photos.get(ep["id"], []) if loaded.dots.get(k) is not None]
        if not keys:
            continue
        era, edec, half = _epoch_square(ep, meta)
        parts = []
        for k in keys if ep.get("kind") == "visit" else keys[:1]:
            d = loaded.dots[k]
            sel = np.nonzero(_in_square(d.ra, d.dec, era, edec, half))[0]
            why = [_art_reasons(int(d.flags[j]), int(d.fpk[j]), float(d.elong[j]), lone=True) for j in sel]
            parts.append((d.ra[sel], d.dec[sel], d.pos_err[sel], d.snr[sel], d.flux[sel], why,
                          d.mjd if math.isfinite(d.mjd) else float(ep.get("mjd_mid") or 0.0)))
        if not parts:
            continue
        ra = np.concatenate([p[0] for p in parts])
        dec = np.concatenate([p[1] for p in parts])
        snr = np.concatenate([p[3] for p in parts])
        flux = np.concatenate([p[4] for p in parts])
        why = [w for p in parts for w in p[5]]
        if ep.get("kind") == "visit":  # merge the visit's dots within FOF_ARCSEC
            pe = np.concatenate([p[2] for p in parts])
            lab = _fof(ra, dec, era, edec, FOF_ARCSEC)
            n = int(lab.max()) + 1 if len(lab) else 0
            cnt = np.bincount(lab, None, n)
            keep = np.nonzero(cnt >= (2 if len(keys) >= 3 else 1))[0]  # a lone dot in a visit: noise or a mover
            xy = _xy(ra, dec, era, edec)
            w = 1.0 / np.maximum(pe, 0.01) ** 2
            sw = np.bincount(lab, w, n)
            gx, gy = np.bincount(lab, w * xy[:, 0], n) / np.maximum(sw, 1e-12), np.bincount(lab, w * xy[:, 1], n) / np.maximum(sw, 1e-12)
            gdec = edec + gy / 3600.0
            gra = (era + gx / 3600.0 / np.maximum(np.cos(np.radians(gdec)), 1e-6)) % 360.0
            gsnr = np.sqrt(np.bincount(lab, snr * snr, n))
            order = np.argsort(lab, kind="stable")
            members = np.split(order, np.cumsum(cnt)[:-1])
            pts = []
            for gi in keep:
                idx = members[gi]
                flagged = [why[i] for i in idx]
                reasons = list(dict.fromkeys(r for f in flagged for r in f)) if all(flagged) else []
                pts.append((gra[gi], gdec[gi], gsnr[gi], float(np.median(flux[idx])), int(cnt[gi]), reasons))
            mjd = float(ep.get("mjd_mid") or parts[0][6])
        else:
            pts = [(ra[i], dec[i], snr[i], flux[i], 1, why[i]) for i in range(len(ra))]
            mjd = parts[0][6]
        if not pts:
            continue
        pra = np.array([p[0] for p in pts], float)
        pdec = np.array([p[1] for p in pts], float)
        moved = [_gaia_at(g, mjd) for g in gaia]
        star = _nearest(pra, pdec, [m[0] for m in moved], [m[1] for m in moved], era, edec, DOTS_ONLY_STAR_ARCSEC)
        claimed: dict[int, str] = {}
        if ep.get("kind") != "visit":
            free = np.nonzero(star < 0)[0]
            for k in known_by_epoch.get(ep["id"], ()):
                cand = np.array([c for c in free if int(c) not in claimed], int)
                if not len(cand):
                    break
                xy = _xy(pra[cand], pdec[cand], float(k["ra"]), float(k["dec"]))
                dist = np.hypot(xy[:, 0], xy[:, 1])
                b = int(np.argmin(dist))
                if dist[b] <= KNOWN_DOT_ARCSEC:
                    claimed[int(cand[b])] = str(k["name"])
        for i, (pr, pd, ps, pf, pn, reasons) in enumerate(pts):
            common = (ep["id"], pr, pd, ps, pf, pn)
            if star[i] >= 0:
                gid = gaia[int(star[i])].get("id")
                out.append(_det(*common, "star", gaia_id=None if gid is None else str(gid)))
            elif i in claimed:
                out.append(_det(*common, "known", known_name=claimed[i]))
            elif reasons:
                out.append(_det(*common, "artifact", reasons=reasons))
            else:
                out.append(_det(*common, "other"))
    cz = loaded.covering
    return _finish(out, [], [], zone=cz.name if cz else None, stage="extracted", zone_box=cz.box() if cz else None,
                   clock=clock, n_have=len(have), n_all=len(all_keys)), None


def _epoch_square(ep: dict, meta: dict) -> tuple[float, float, float]:
    c = ep.get("center") or meta["center"]
    return float(c["ra"]), float(c["dec"]), meta["size"][0] * float(meta["pixel_arcsec"]) / 2.0 / 3600.0


def _hours(loaded: Loaded, meta: dict, epochs: list[dict], photos: dict[str, list[str]], F: _Field,
           T: Tables, Z: Zone) -> tuple[list[dict], list[dict]]:
    known_by_epoch: dict[str, list[dict]] = defaultdict(list)
    for k in meta.get("known") or []:
        if k.get("epoch_id") is not None and k.get("ra") is not None:
            known_by_epoch[k["epoch_id"]].append(k)
    mv = T.mv
    n_mv = len(mv.get("ra", ()))
    near = (np.nonzero(_sep_mask(np.asarray(mv["ra"], float), np.asarray(mv["dec"], float), F.ra0, F.dec0, F.r_deg))[0]
            if n_mv else np.zeros(0, int))  # movers near the field
    has_lists = "dot_obs" in mv and "dot_row" in mv

    per_ep = []  # (ep, dots-in-square, source idx, track idx per dot)
    for ep in epochs:
        keys = photos.get(ep["id"]) or []
        d = loaded.dots.get(keys[0]) if keys else None
        if d is None:
            continue
        era, edec, half = _epoch_square(ep, meta)
        sel = np.nonzero(_in_square(d.ra, d.dec, era, edec, half))[0]
        src = F.match(d.ra[sel], d.dec[sel], SOURCE_MATCH_ARCSEC)
        per_ep.append((ep, keys[0], d, sel, src, np.full(len(sel), -1, int)))

    # 1. the movers' dots in this response
    track_dots: dict[int, list[tuple[int, int]]] = defaultdict(list)  # mover -> [(per_ep index, local dot)]
    for pi, (ep, key, d, sel, src, trk) in enumerate(per_ep):
        obs, _, det_s = key.rpartition("D")
        det = int(det_s)
        for m in near:
            if obs not in mv["obs_set"][m] or det not in mv["det_set"][m]:
                continue
            if has_lists and mv["dot_obs"][m]:
                rows = [int(r) for o, dd, r in zip(mv["dot_obs"][m], mv["dot_det"][m], mv["dot_row"][m])
                        if o == obs and int(dd) == det and r is not None and int(r) >= 0]
                for r in rows:
                    j = np.nonzero(d.row[sel] == r)[0]
                    if len(j) and trk[j[0]] < 0:
                        trk[j[0]] = m
                        track_dots[m].append((pi, int(j[0])))
                continue
            # older movers tables: the nearest free dot within what the pairing rule allows
            free = np.nonzero((src < 0) & (trk < 0))[0]
            if not len(free):
                continue
            dt_min = abs(d.mjd - float(mv["mjd"][m])) * 1440.0
            r_max = 3.0 + 1.0 * dt_min + 2.0
            xy = _xy(d.ra[sel][free], d.dec[sel][free], float(mv["ra"][m]), float(mv["dec"][m]))
            dist = np.hypot(xy[:, 0], xy[:, 1])
            b = int(np.argmin(dist))
            if dist[b] <= r_max:
                trk[free[b]] = m
                track_dots[m].append((pi, int(free[b])))

    # 2. classify each mover with dots here
    track_info: dict[int, dict] = {}
    gaia = meta.get("gaia") or []
    for m, members in track_dots.items():
        members.sort(key=lambda t: per_ep[t[0]][2].mjd)
        cell, mjd = int(mv["cell"][m]), float(mv["mjd"][m])
        sus = T.suspects.get(_mkey(cell, mjd))
        known_name, best = None, KNOWN_TRACK_ARCSEC
        for pi, j in members:
            ep, key, d, sel, src, trk = per_ep[pi]
            for k in known_by_epoch.get(ep["id"], ()):
                xy = _xy([d.ra[sel][j]], [d.dec[sel][j]], float(k["ra"]), float(k["dec"]))[0]
                dist = float(np.hypot(*xy))
                if dist <= best:
                    best, known_name = dist, str(k["name"])
        flags = int(mv["flags"][m])
        reasons: list[str] = []
        grade = None
        if sus is not None:
            cls, tid, grade, reasons = "suspect", sus[0], sus[1], list(sus[2])
        else:
            tid = f"{Z.name}-m{cell}-{int(round(mjd * 1440.0))}"
            if known_name is not None:
                cls = "known"
            elif flags & ART_MASK:
                cls, reasons = "artifact", _art_reasons(flags, 0, float("nan"), lone=False)
            else:
                cls = "mover"
                for g in gaia:
                    try:
                        gra, gdec = _gaia_at(g, mjd)
                    except (KeyError, TypeError):
                        continue
                    if float(np.hypot(*_xy([mv["ra"][m]], [mv["dec"][m]], gra, gdec)[0])) <= NEAR_GAIA_ARCSEC:
                        reasons = ["near_gaia"]
                        break
        track_info[m] = {"cls": cls, "id": tid, "grade": grade, "reasons": reasons,
                         "known": known_name if cls == "known" else None}
    tracks: list[dict] = []
    for m, members in track_dots.items():
        info = track_info[m]
        tracks.append({
            "id": info["id"], "class": info["cls"],
            "suspect_id": info["id"] if info["cls"] == "suspect" else None,
            "known_name": info["known"], "evidence": str(mv["evidence"][m]),
            "dots": [{"ra": _r(per_ep[pi][2].ra[per_ep[pi][3]][j], 6),
                      "dec": _r(per_ep[pi][2].dec[per_ep[pi][3]][j], 6),
                      "mjd": _r(per_ep[pi][2].mjd, 6), "epoch_id": per_ep[pi][0]["id"]} for pi, j in members],
        })
    tracks.sort(key=lambda t: (t["dots"][0]["mjd"] or 0.0, t["id"]))

    # 3. every dot
    out: list[dict] = []
    for ep, key, d, sel, src, trk in per_ep:
        ra, dec = d.ra[sel], d.dec[sel]
        claimed: dict[int, str] = {}
        for k in known_by_epoch.get(ep["id"], ()):
            cand = np.nonzero((src < 0) & (trk < 0))[0]
            cand = np.array([c for c in cand if c not in claimed], int)
            if not len(cand):
                continue
            xy = _xy(ra[cand], dec[cand], float(k["ra"]), float(k["dec"]))
            dist = np.hypot(xy[:, 0], xy[:, 1])
            b = int(np.argmin(dist))
            if dist[b] <= KNOWN_DOT_ARCSEC:
                claimed[int(cand[b])] = str(k["name"])
        for j in range(len(sel)):
            common = (ep["id"], ra[j], dec[j], d.snr[sel][j], d.flux[sel][j], 1)
            if trk[j] >= 0:
                info = track_info[int(trk[j])]
                out.append(_det(*common, info["cls"], known_name=info["known"], track_id=info["id"],
                                grade=info["grade"], reasons=info["reasons"]))
            elif j in claimed:
                out.append(_det(*common, "known", known_name=claimed[j]))
            elif src[j] >= 0:
                s = int(src[j])
                gid = F.gaia_id(s)
                out.append(_det(*common, "star" if gid else "other", source_id=int(F.id[s]), gaia_id=gid))
            else:
                why = _art_reasons(int(d.flags[sel][j]), int(d.fpk[sel][j]), float(d.elong[sel][j]), lone=True)
                out.append(_det(*common, "artifact" if why else "other", reasons=why))
    return out, tracks


def _months(loaded: Loaded, meta: dict, epochs: list[dict], photos: dict[str, list[str]], F: _Field,
            T: Tables) -> list[dict]:
    out: list[dict] = []
    for ep in epochs:
        era, edec, half = _epoch_square(ep, meta)
        parts = []
        for key in photos.get(ep["id"]) or []:
            d = loaded.dots.get(key)
            if d is None or not len(d.ra):
                continue
            src = F.match(d.ra, d.dec, SOURCE_MATCH_ARCSEC)
            ok = (src >= 0) & _in_square(d.ra, d.dec, era, edec, half)
            if ok.any():
                parts.append((src[ok], d.ra[ok], d.dec[ok], d.pos_err[ok], d.snr[ok], d.flux[ok]))
        if not parts:
            continue
        src, ra, dec, pe, snr, flux = (np.concatenate(c) for c in zip(*parts))
        sra, sdec = F.ra[src], F.dec[src]
        cosd = np.cos(np.radians(sdec))
        dx = ((ra - sra + 180.0) % 360.0 - 180.0) * cosd * 3600.0
        dy = (dec - sdec) * 3600.0
        w = 1.0 / np.maximum(pe, 0.01) ** 2
        n = len(F.ra)
        sw = np.bincount(src, w, n)
        mx = np.bincount(src, w * dx, n)
        my = np.bincount(src, w * dy, n)
        s2 = np.bincount(src, snr * snr, n)
        cnt = np.bincount(src, None, n)
        order = np.argsort(src, kind="stable")
        groups = np.split(order, np.cumsum(np.bincount(src, None, n))[:-1])
        for s in np.nonzero(cnt)[0]:
            ex, ey = mx[s] / sw[s], my[s] / sw[s]
            rra = F.ra[s] + ex / 3600.0 / max(math.cos(math.radians(F.dec[s])), 1e-6)
            rdec = F.dec[s] + ey / 3600.0
            gid = F.gaia_id(int(s))
            cls, reasons = _months_class(T, int(F.id[s]), gid is not None)
            out.append(_det(ep["id"], rra % 360.0, rdec, math.sqrt(s2[s]), float(np.median(flux[groups[s]])),
                            int(cnt[s]), cls, source_id=int(F.id[s]), gaia_id=gid, reasons=reasons))
    if T.stack is not None:  # v3.13, in the epochs that have photos here
        for ep in epochs:
            if photos.get(ep["id"]):
                out += _months_stack(ep, *_epoch_square(ep, meta), T.stack)
    return out


def _shifts(meta: dict, epochs: list[dict], F: _Field, T: Tables) -> list[dict]:
    mids = [float(e["mjd_mid"]) for e in epochs if e.get("kind") == "visit" and e.get("mjd_mid") is not None]
    a = T.ast
    if len(mids) < 2 or not len(a.get("source_id", ())):
        return []
    mjd_from, mjd_to = min(mids), max(mids)
    dt = (mjd_to - mjd_from) / 365.25
    if dt <= 0:
        return []
    half = meta["size"][0] * float(meta["pixel_arcsec"]) / 2.0 / 3600.0
    inside = np.nonzero(_in_square(a["ra"], a["dec"], F.ra0, F.dec0, half))[0]
    inside = inside[np.argsort(-np.asarray(a["pm_snr"], float)[inside], kind="stable")][:MAX_SHIFTS]
    gaia_of = {int(F.id[i]): F.gaia_id(i) for i in range(len(F.id))}
    out = []
    for i in inside:
        sid = int(a["source_id"][i])
        pmra, pmdec = float(a["pmra"][i]), float(a["pmdec"][i])
        err = max(float(a["pmra_err"][i]), float(a["pmdec_err"][i]))
        d0 = (mjd_from - float(a["epoch_mjd"][i])) / 365.25
        dec = float(a["dec"][i]) + pmdec * d0 / 3.6e6
        ra = float(a["ra"][i]) + pmra * d0 / 3.6e6 / max(math.cos(math.radians(float(a["dec"][i]))), 1e-6)
        dra, ddec = pmra * dt / 1000.0, pmdec * dt / 1000.0
        gid = gaia_of.get(sid)
        cls, _ = _months_class(T, sid, gid is not None)
        out.append({
            "source_id": sid, "gaia_id": gid, "class": cls, "ra": _r(ra % 360.0, 6), "dec": _r(dec, 6),
            "mjd_from": _r(mjd_from, 6), "mjd_to": _r(mjd_to, 6), "dt_yr": _r(dt, 4),
            "dra_arcsec": _r(dra, 3), "ddec_arcsec": _r(ddec, 3), "shift_arcsec": _r(math.hypot(dra, ddec), 3),
            "err_arcsec": _r(err * dt / 1000.0, 3), "pa_deg": _r(math.degrees(math.atan2(pmra, pmdec)) % 360.0, 1),
            "pmra": _r(pmra, 1), "pmdec": _r(pmdec, 1), "pm_err": _r(err, 1), "pm_snr": _r(float(a["pm_snr"][i]), 1),
        })
    return out


# --- one call ---------------------------------------------------------------------------------


def field_radius_deg(meta: dict) -> float:
    """What load() must cover for this response: the field's half-diagonal plus 1', from the centre,
    out to the farthest epoch centre of frames that follow an object."""
    c = meta["center"]
    half = meta["size"][0] * float(meta["pixel_arcsec"]) / 2.0 / 3600.0
    reach = 0.0
    for ep in meta.get("epochs") or []:
        e = ep.get("center")
        if e:
            reach = max(reach, float(np.hypot(*(_xy([e["ra"]], [e["dec"]], c["ra"], c["dec"])[0] / 3600.0))))
    return reach + half * math.sqrt(2.0) + 1.0 / 60.0


def photo_keys(epoch_keys: dict[str, list[str]], meta: dict) -> list[str]:
    keys = []
    for ep in meta.get("epochs") or []:
        ks = epoch_keys.get(ep["id"])
        if ks is None and ep.get("kind") == "exposure":
            k = _key_of_exposure(ep["id"])
            ks = [k] if k else []
        if ep.get("kind") == "visit":
            ks = _spread(list(ks or []), MONTHS_MAX_PHOTOS_PER_VISIT)
        keys += list(ks or [])
    return list(dict.fromkeys(keys))


def build(meta: dict, epoch_keys: dict[str, list[str]] | None = None, *, zone: str | None = None,
          root: str | None = None) -> tuple[dict | None, str | None]:
    """(measured, measured_reason) for a finished frames response. Never raises."""
    try:
        epoch_keys = epoch_keys or {}
        c = meta["center"]
        loaded = load(float(c["ra"]), float(c["dec"]), field_radius_deg(meta), photo_keys(epoch_keys, meta),
                      zone=zone, root=root)
        return assemble(loaded, meta, epoch_keys)
    except Exception:  # noqa: BLE001
        log.exception("measured: build failed")
        return None, "unavailable"


def stale(cached: dict, *, root: str | None = None) -> bool:
    """True when a cached frames answer's measured layer is out of date: a zone now has results here,
    or other ones (a final stage, a target zone). Cheap: zones are kept in memory for ZONES_TTL_S."""
    if not enabled():
        return False
    if "measured" not in cached:
        return False  # built with the layer off; answers from before it have other cache keys
    m, reason = cached.get("measured"), cached.get("measured_reason")
    if m is None and reason not in ("not_processed", "processing"):
        return False  # no_dots, unavailable: rebuilding wouldn't change it
    try:
        c = cached["center"]
        z, _ = find_zone(float(c["ra"]), float(c["dec"]), root=root)
    except Exception:  # noqa: BLE001
        return False
    if m is None:
        if z is not None:
            return True
        if dots_only_enabled() and cached.get("clock") == "hours":  # dots extracted here since?
            keys = [k for k in (_key_of_exposure(str(e.get("id", ""))) for e in (cached.get("epochs") or [])[:2]) if k]
            try:
                st = store(root)
                return any(dot_exists(st, k) for k in keys)
            except Exception:  # noqa: BLE001
                return False
        return False
    if z is None:
        return False
    if (z.name, z.stage) != (m.get("zone"), m.get("stage")):
        return True
    # v3.13: a months answer built before the zone's stack table existed
    return (stack_enabled(z.name) and cached.get("clock") == "months" and STACK_CLASS not in (m.get("counts") or {})
            and stack_exists(z.name, root))


def stack_exists(zone: str, root: str | None = None) -> bool:
    """Whether results/<zone>/stack_sources.parquet exists (one metadata request, remembered EXISTS_TTL_S)."""
    try:
        st = store(root)
        key = f"results/{zone}/stack_sources.parquet"
        known = _known_presence(st, key)
        if known is None:
            known = st.exists(key)
            _remember(st, key, known)
        return known
    except Exception:  # noqa: BLE001
        return False
