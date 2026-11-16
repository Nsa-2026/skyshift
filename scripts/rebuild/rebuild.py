#!/usr/bin/env python3
"""Helpers for scripts/rebuild/tocheck.sh, the To check rebuild on a PC (docs/rebuild-on-pc.md).

Run with the pipeline's environment, from the repo root:
    uv run --project pipeline --frozen python scripts/rebuild/rebuild.py <command> [options]

Commands
    plan       list the bucket (no reads): the files each step mirrors, their sizes, the cost; manifest.tsv
    fetch      copy one zone's (or the shared) manifest files into the local mirror, no-clobber, and check sizes
    refcat-status  step a0's list: which mirrored refcats lack CatWISE/2MASS or stop short of REFCAT_GAIA_G_MAX
    refcat     step a0 for one zone: complete the mirror's refcat (AllWISE on AWS S3 first, Gaia from ESA; both free)
    upload-refcat  --upload-refcat: the completed refcats to the bucket (the old ones copied to a backup prefix first)
    estimate   after the suspects rerun: what the export will read from the bucket (GB, $) and the disk it needs
    export     run the exporter (skyshift-candidates) on an overlay: local mirror first, the bucket by column
               for the rest (small reads), never a write to the bucket
    combine    one file with the candidates of candidates.json and candidates-more/*.json (for the known check)
    report     step (f): counts, sizes, 306P, spot checks, before/after; exit 2 on a hard failure
    frames-check  step e: how many of the top --frames-top open A/B movers have static frames
    fill-todo  candidate folders that still need fill.py (hours frames, no fill.json, not tried yet)

The bucket is only listed (`gcloud storage ls`) and read (`gcloud storage cp` to the local disk, or by column through
gcsfs in `export`). Nothing here deletes anything in the bucket; only `upload-refcat` (asked for with a flag, after a
prompt) writes to it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUCKET = os.environ.get("SKYSHIFT_BUCKET", "gs://nasa-glazing-data").rstrip("/")
PRICE_PER_GB = 0.13          # Nearline retrieval ($0.01/GB) + internet egress from us-east4 (~$0.12/GB), 16 Nov

# What `skyshift-pipeline suspects` reads per zone (pipeline/src/skyshift_pipeline/suspects.py run, vet.py, known.py):
SUSPECTS_RESULTS = ("movers.parquet", "fixed.parquet", "freedots.parquet", "qa_photos.parquet", "tracklets.parquet",
                    "tracklet_dots.parquet", "summary.json")
# The bucket's own suspects tables, kept next to the new ones for the before/after report (never read by a step).
RENAMED = {"suspects.parquet": "suspects.bucket.parquet", "suspects.json": "suspects.bucket.json"}
WRITTEN_LOCALLY = {"summary.json"}  # rewritten by the suspects step: a size different from the bucket's is expected
# What the exporter reads from the bucket by column on top of the mirror (pipeline candidates.py): per zone ...
EXPORT_TABLES = ("sources.parquet", "members.parquet", "classes.parquet", "crossids.parquet", "fragments.parquet",
                 "photoz.parquet", "highpm_candidates.parquet", "shape_candidates.parquet")
# ... and the dot files (dots/<obs_id>D<det>.parquet): the `flags` column of the photos of every high_pm, spectral and
# shape candidate's dots (hunt: 3,225 of 3,692 photos) and of the grade A/B movers' dots (a few % of the photos, mostly
# the same files). Grade C movers are built from the suspects row alone and a mover's motion is link's in-session
# motion (suspects.parquet session_*, since 16 Nov), so no dot positions are read (exporter v3.24, PR #369).
# Bytes per file with a 16 KiB read block (export --block): the footer (~67 KB: Arrow schema + photo metadata) + the
# flags column + one block per read. Measured on a real dot file (2025W34_1B_0173_1D6, through fsspec's readahead
# cache): flags 0.66 MB of 0.73 MB with gcsfs' default 5 MiB block (= the whole file), 0.15 MB with 16 KiB; scaled to
# the mean dot file (~3.0 MB, ~22,000 dots).
FIXED_PHOTO_SHARE = 0.9
FLAGS_READ_MB = 0.12
POSITIONS_READ_MB = 0.55     # ra, dec, mjd, snr, pos_err: only for movers in suspects tables without session_* columns
DOT_FILE_MB = 3.0            # the sky run: 887 GB of dot files for ~296,000 photos
PHOTOLIST_ROW_BYTES = 117    # photolists/zones/<zone>.parquet bytes per photo (measured on 38 zones)
# On-disk export cache (export --cache): the column reads as written by pandas, per file
CACHE_POSITIONS_MB = 0.45
CACHE_FLAGS_MB = 0.012
GB = 1e9
FRAMES_TOP = 300             # static frames for the top 300 open A/B movers by score (the Viewer loads the rest live)
MOVER_FRAMES_MB = 0.45       # a mover's cand/<id>/ on main, 16 Nov: median 0.44 MB (~0.14 MB zlib in git), max 0.7 MB
CAND_WARN_MB = 250


def log(msg: str) -> None:
    print(f"[rebuild {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fail(msg: str, code: int = 1):
    print(f"\nSTOP: {msg}\n", file=sys.stderr, flush=True)
    raise SystemExit(code)


def money(gb: float) -> str:
    return f"${gb * PRICE_PER_GB:,.2f}"


# ------------------------------------------------------------------------------------------------ listing

def gls(pattern: str, cache_dir: Path | None = None, max_age_h: float = 12.0) -> list[tuple[str, int]]:
    """(url, bytes) of every object matching `pattern` (gcloud storage ls -l). Cached as text for max_age_h, so a
    dry run followed by the real run lists once. A pattern that matches nothing gives []."""
    f = None
    if cache_dir is not None:
        f = cache_dir / f"ls-{hashlib.sha1(pattern.encode()).hexdigest()[:12]}.txt"
        if f.exists() and time.time() - f.stat().st_mtime < max_age_h * 3600:
            return _parse_ls(f.read_text())
    r = subprocess.run(["gcloud", "storage", "ls", "-l", pattern], check=False, capture_output=True, text=True)
    if r.returncode != 0:
        err = r.stderr.strip()
        if "matched no objects" in err or "No URLs matched" in err or "not found" in err.lower():
            out = ""
        else:
            fail(f"listing {pattern} failed:\n{err[:800]}\nAre you logged in? Run: gcloud auth login")
    else:
        out = r.stdout
    if f is not None:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(out)
    return _parse_ls(out)


def _parse_ls(text: str) -> list[tuple[str, int]]:
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s+\S+\s+(gs://\S+)\s*$", line)
        if m:
            out.append((m.group(2), int(m.group(1))))
    return out


def rel_of(url: str) -> str:
    return url[len(BUCKET) + 1:]


# ------------------------------------------------------------------------------------------------ plan

def finished_zones(listing_dir: Path) -> list[str]:
    return sorted({rel_of(u).split("/")[1] for u, _ in gls(f"{BUCKET}/results/*/suspects.parquet", listing_dir)})


def photo_count(data: Path, zone: str, plist_bytes: int) -> tuple[int, bool]:
    """(photos in the zone, exact): rows of the local photo list, else its bucket size / PHOTOLIST_ROW_BYTES."""
    p = data / "photolists" / "zones" / f"{zone}.parquet"
    if p.exists():
        try:
            import pyarrow.parquet as pq
            return int(pq.ParquetFile(p).metadata.num_rows), True
        except Exception as e:  # noqa: BLE001
            log(f"photo list {p} unreadable ({e}): counting photos from its bucket size")
    return round(plist_bytes / PHOTOLIST_ROW_BYTES), False


def cmd_plan(a) -> int:
    data, work = Path(a.data), Path(a.work)
    ldir = work / "plan" / "listing"
    allz = finished_zones(ldir)
    if not allz:
        fail(f"no zone has results/<zone>/suspects.parquet under {BUCKET}")
    if a.zones:
        zones = [z.strip() for z in a.zones.split(",") if z.strip()]
        bad = [z for z in zones if z not in allz]
        if bad:
            fail(f"no results in the bucket for: {', '.join(bad)}. Zones with results: {' '.join(allz)}")
    else:
        zones = allz
    zset = set(zones)
    many = len(zones) > 8
    rows: list[tuple[str, str, str, str, int]] = []  # category, zone, url, local rel, bytes
    export_tables: dict[str, int] = defaultdict(int)
    export_files: list[tuple[str, int]] = []
    res = gls(f"{BUCKET}/results/**", ldir) if many else [x for z in zones for x in gls(f"{BUCKET}/results/{z}/*", ldir)]
    for url, size in res:
        parts = rel_of(url).split("/")
        if len(parts) != 3 or parts[1] not in zset:
            continue
        z, name = parts[1], parts[2]
        if name in SUSPECTS_RESULTS:
            rows.append(("results", z, url, f"results/{z}/{name}", size))
        elif name in RENAMED:
            rows.append(("results", z, url, f"results/{z}/{RENAMED[name]}", size))
        elif name in EXPORT_TABLES:
            export_tables[z] += size
            export_files.append((f"results/{z}/{name}", size))
    plist = {}
    for url, size in gls(f"{BUCKET}/photolists/zones/*.parquet", ldir):
        name = url.rsplit("/", 1)[1][:-len(".parquet")]
        if name in zset:
            plist[name] = size
            rows.append(("photolists", name, url, f"photolists/zones/{name}.parquet", size))
    for url, size in gls(f"{BUCKET}/refcat/*", ldir):
        name = url.rsplit("/", 1)[1]
        for z in zones:
            if name in (f"{z}.parquet", f"{z}_catalogs.json"):
                rows.append(("refcat", z, url, f"refcat/{name}", size))
    mpc = gls(f"{BUCKET}/cache/mpc/**", ldir) if many else (
        [x for z in zones for x in gls(f"{BUCKET}/cache/mpc/{z}/**", ldir)]
        + gls(f"{BUCKET}/cache/mpc/orbits/*", ldir) + gls(f"{BUCKET}/cache/mpc/known_index/*", ldir))
    newest: dict[str, tuple[str, int]] = {}
    for url, size in mpc:
        parts = rel_of(url).split("/")
        if parts[2] in zset:
            rows.append(("mpc", parts[2], url, rel_of(url), size))
        elif parts[2] in ("orbits", "known_index"):
            name = parts[-1]
            kind = name.split("_20")[0]  # mpcorb, cometels, known_index
            if kind not in newest or name > newest[kind][0].rsplit("/", 1)[1]:
                newest[kind] = (url, size)
    for kind in ("mpcorb", "cometels", "known_index"):
        if kind not in newest:
            fail(f"no {kind} snapshot under {BUCKET}/cache/mpc/: the known check and the frames need it")
        url, size = newest[kind]
        rows.append(("orbits", "*", url, rel_of(url), size))
    pl = gls(f"{BUCKET}/cache/planets/**", ldir) if many else [x for z in zones for x in gls(f"{BUCKET}/cache/planets/{z}/**", ldir)]
    for url, size in pl:
        parts = rel_of(url).split("/")
        if parts[2] in zset:
            rows.append(("planets", parts[2], url, rel_of(url), size))

    missing = [z for z in zones if not any(r[1] == z and r[3].endswith("/movers.parquet") for r in rows)]
    if missing:
        fail(f"results/<zone>/movers.parquet missing for {', '.join(missing)}: suspects can't run there")
    out = work / "plan"
    out.mkdir(parents=True, exist_ok=True)
    have = 0
    with open(out / "manifest.tsv", "w") as f:
        f.write("category\tzone\turl\tlocal\tbytes\thave\n")
        for cat, z, url, rel, size in rows:
            ok = _ok(data / rel, size)
            have += size if ok else 0
            f.write(f"{cat}\t{z}\t{url}\t{rel}\t{size}\t{int(ok)}\n")
    # zones, largest suspects input first (better packing of the parallel suspects step)
    zsize = Counter()
    for cat, z, _, _, size in rows:
        if z != "*":
            zsize[z] += size
    order = sorted(zones, key=lambda z: -zsize[z])
    (out / "zones.txt").write_text("\n".join(order) + "\n")
    (out / "all_zones.txt").write_text("\n".join(allz) + "\n")

    # ---- summary
    by = defaultdict(lambda: [0, 0, 0])  # files, bytes, bytes already local
    for cat, z, url, rel, size in rows:
        ok = _ok(data / rel, size)
        by[cat][0] += 1
        by[cat][1] += size
        by[cat][2] += size if ok else 0
    total = sum(v[1] for v in by.values())
    todo = total - have
    photos, exact = 0, True
    for z in zones:
        n, ex = photo_count(data, z, plist.get(z, 0))
        photos += n
        exact &= ex
    exp = export_estimate(photos, sum(export_tables.values()))
    desc = {"results": "results/<zone>/ movers, fixed, freedots, qa_photos, tracklets, tracklet_dots, summary "
                       "(+ the bucket's suspects.parquet/json kept as suspects.bucket.*)",
            "photolists": "photolists/zones/<zone>.parquet",
            "refcat": "refcat/<zone>.parquet + <zone>_catalogs.json (Gaia, CatWISE, 2MASS)",
            "mpc": "cache/mpc/<zone>/run_*.json (known asteroids and comets per session)",
            "planets": "cache/planets/<zone>/ (Horizons planets and moons; most zones have none)",
            "orbits": "cache/mpc/orbits newest mpcorb + cometels, cache/mpc/known_index newest .npz"}
    lines = [(f"Zones: {len(zones)} of {len(allz)} with results ({'all' if len(zones) == len(allz) else 'PARTIAL'}): "
             f"{' '.join(order)}"), "",
             "(a) Mirror (gcloud storage cp, no-clobber) into " + str(data),
             f"  {'what':<12} {'files':>7} {'GB':>8} {'here':>8} {'to get':>8} {'cost':>8}"]
    for cat in ("results", "photolists", "refcat", "mpc", "planets", "orbits"):
        n, b, h = by.get(cat, [0, 0, 0])
        lines.append(f"  {cat:<12} {n:>7} {b / GB:>8.2f} {h / GB:>8.2f} {(b - h) / GB:>8.2f} {money((b - h) / GB):>8}   {desc[cat]}")
    lines.append(f"  {'total':<12} {sum(v[0] for v in by.values()):>7} {total / GB:>8.2f} {have / GB:>8.2f} "
                 f"{todo / GB:>8.2f} {money(todo / GB):>8}")
    lines += ["", ("(c) Export: read from the bucket by column through the overlay (estimate; refined after (b) by "
                  "`rebuild.py estimate`)"),
              f"  photos in these zones: {photos:,}{'' if exact else ' (from the photo lists sizes)'}",
              f"  tables (sources, members, classes, crossids, fragments, photoz, high_pm, shape): {exp['tables_gb']:.1f} GB",
              f"  dot files, flags for the fixed kinds (~{FIXED_PHOTO_SHARE:.0%} of photos x {FLAGS_READ_MB} MB): {exp['flags_gb']:.1f} GB",
              "  dot files for the movers: grade A/B read their flags only (mostly files read above), grade C nothing,",
              "  no positions (the new suspects tables carry session_rate_arcsec_h)",
              (f"  total ~{exp['total_gb']:.0f} GB, {money(exp['total_gb'])} (range x0.6-x1.6); with gcsfs' default 5 MiB block it "
              f"would be ~{exp['default_block_gb']:.0f} GB ({money(exp['default_block_gb'])})"),
              f"  disk for the export cache (--cache, keeps reruns free): ~{exp['cache_gb']:.0f} GB",
              "",
              (f"Steps a and c: ~{todo / GB + exp['total_gb']:.0f} GB, ~{money(todo / GB + exp['total_gb'])}. "
              "Step m (mplan) and the optional k come on top. "
              "NASA S3 (pixels, AllWISE), IRSA, MPC and JPL are free.")]
    text = "\n".join(lines)
    (out / "summary.txt").write_text(text + "\n")
    (out / "plan.json").write_text(json.dumps(
        {"zones": order, "all_zones": allz, "partial": len(zones) != len(allz), "mirror_bytes": total,
         "mirror_have": have, "mirror_todo": todo, "photos": photos, "export": {**exp, "files": export_files}}, indent=1))
    print(text)
    return 0


def export_estimate(photos: int, tables_bytes: int, ab_files: int = 0, pos_files: int = 0) -> dict:
    """What the export reads from the bucket (GB). Flags: the fixed kinds' member dots in ~90% of the photos, plus the
    grade A/B movers' dot files (counted in full: most are among the first); positions: only movers of suspects tables
    without session_* columns (none after step b)."""
    flag_files = FIXED_PHOTO_SHARE * photos + ab_files
    flags = flag_files * FLAGS_READ_MB / 1e3
    pos = pos_files * POSITIONS_READ_MB / 1e3
    tables = tables_bytes / GB
    default = tables + (flag_files + pos_files) * DOT_FILE_MB / 1e3
    cache = tables * 1.3 + pos_files * CACHE_POSITIONS_MB / 1e3 + flag_files * CACHE_FLAGS_MB / 1e3
    return {"tables_gb": round(tables, 2), "flags_gb": round(flags, 1), "positions_gb": round(pos, 1),
            "total_gb": round(tables + flags + pos, 1), "default_block_gb": round(default, 0),
            "cache_gb": round(cache, 0), "ab_files": int(ab_files), "pos_files": int(pos_files), "photos": photos}


# ------------------------------------------------------------------------------------------------ fetch

def read_manifest(work: Path, name: str = "manifest.tsv") -> list[dict]:
    f = work / "plan" / name
    if not f.exists():
        fail(f"{f} is missing: run the plan first (tocheck.sh --dry-run, or step a)")
    lines = f.read_text().splitlines()
    head = lines[0].split("\t")
    return [dict(zip(head, ln.split("\t"))) for ln in lines[1:] if ln]


def downloads_log(data: Path, what: str, nbytes: int) -> None:
    with open(data / "DOWNLOADS.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} pc-rebuild {what} {nbytes / 1e6:.1f} MB\n")


def _ok(p: Path, size: int) -> bool:
    return p.exists() and (p.stat().st_size == size or p.name in WRITTEN_LOCALLY or _completed_here(p))


def _completed_here(p: Path) -> bool:
    """refcat/<zone>.parquet or its marker after step a0 changed them (it keeps the bucket's as <zone>.bucket.parquet
    first): a size other than the bucket's is expected, and step a must never copy the bucket's over them."""
    if p.parent.name != "refcat":
        return False
    zone = p.name[:-len("_catalogs.json")] if p.name.endswith("_catalogs.json") else p.name[:-len(".parquet")]
    return p.name in (f"{zone}.parquet", f"{zone}_catalogs.json") and (p.parent / f"{zone}.bucket.parquet").exists()


def cmd_fetch(a) -> int:
    data, work = Path(a.data), Path(a.work)
    want = [r for r in read_manifest(work, a.manifest) if r["zone"] == a.zone]
    if not want:
        fail(f"nothing in the manifest for zone {a.zone!r}")
    todo = [r for r in want if not _ok(data / r["local"], int(r["bytes"]))]
    if a.check:
        for r in todo:
            p = data / r["local"]
            print(f"{'wrong size' if p.exists() else 'missing'}\t{r['local']}\t{int(r['bytes']) / 1e6:.2f} MB")
        log(f"{a.zone}: {len(want) - len(todo)} of {len(want)} files here; {len(todo)} to get "
            f"({sum(int(r['bytes']) for r in todo) / 1e6:.1f} MB)")
        return 1 if todo else 0
    for r in todo:  # a local file of another size: the bucket's changed since (never one this rebuild writes)
        p = data / r["local"]
        if p.exists():
            log(f"{r['local']}: local size {p.stat().st_size} != bucket {r['bytes']}: copying it again")
            p.unlink()
    groups: dict[Path, list[str]] = defaultdict(list)
    single: list[tuple[str, Path]] = []
    for r in todo:
        dst = data / r["local"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.name == r["url"].rsplit("/", 1)[1]:
            groups[dst.parent].append(r["url"])
        else:
            single.append((r["url"], dst))
    nbytes = sum(int(r["bytes"]) for r in todo)
    t0 = time.time()
    for d, urls in groups.items():
        r = subprocess.run(["gcloud", "storage", "cp", "--no-clobber", "--quiet", "-I", str(d) + "/"],
                           check=False, input="\n".join(urls) + "\n", text=True)
        if r.returncode != 0:
            fail(f"gcloud storage cp into {d} failed (exit {r.returncode}); rerun the same command to resume")
    for url, dst in single:
        r = subprocess.run(["gcloud", "storage", "cp", "--no-clobber", "--quiet", url, str(dst)], check=False)
        if r.returncode != 0:
            fail(f"gcloud storage cp {url} failed (exit {r.returncode}); rerun the same command to resume")
    dt = time.time() - t0
    bad = [r["local"] for r in want if not _ok(data / r["local"], int(r["bytes"]))]
    if bad:
        fail(f"{len(bad)} files missing or of the wrong size after the copy: {bad[:5]}; rerun to resume")
    if todo:
        what = "shared orbits + index" if a.zone == "*" else ("step m " if a.manifest != "manifest.tsv" else "") + "zone " + a.zone
        downloads_log(data, f"{what} ({len(todo)} files)", nbytes)
        log(f"{a.zone}: {len(todo)} files, {nbytes / 1e6:.1f} MB in {dt:.0f} s ({nbytes / 1e6 / max(dt, 1e-3):.1f} MB/s)")
    else:
        log(f"{a.zone}: all {len(want)} files already here")
    return 0


# ------------------------------------------------------------------------------------------------ a0: refcat completion

# 64 zones' refcats are Gaia-only (the sky run's SKYSHIFT_REFCAT_CATALOGS=gaia; fix-catalogue). Step a0 completes them
# in the mirror before suspects runs, so its CatWISE / 2MASS vetoes and reasons exist: refcat.run with every catalogue
# and SKYSHIFT_REFCAT_S3_FIRST=1 (AllWISE and its 2MASS associations from AWS S3, free; Gaia from ESA, free). The rows
# a refcat already has are written back as its cached tiles first, so only what is missing is asked for. It follows
# config REFCAT_GAIA_G_MAX: a refcat whose faintest Gaia row is more than REFCAT_DEPTH_TOL brighter gets Gaia again.
REFCAT_DEPTH_TOL = 0.3
REFCAT_BACKUP = "refcat/before-pc-rebuild"   # --upload-refcat: the bucket's own refcat files are first copied here
NEARLINE_GB_MONTH = 0.010                    # $/GB/month (the bucket is Nearline since 15 Nov)
NEARLINE_READ_GB = 0.01                      # $/GB retrieval: the server-side backup copies


def refcat_need(made: list[str] | None, g_faintest: float | None, g_max: float, a0_g_max: float | None = None,
                tol: float = REFCAT_DEPTH_TOL) -> tuple[bool, bool]:
    """(Gaia must be fetched again, CatWISE + 2MASS must be added) for one zone's refcat. made: its marker's catalogues
    (None: no marker, i.e. built with every catalogue before 14 Nov); g_faintest: its faintest Gaia G; a0_g_max: the
    G limit step a0 already fetched Gaia with (marker `rebuild_a0`), which settles the depth whatever the rows say."""
    wise = made is not None and not {"gaia", "catwise", "2mass"} <= set(made)
    if a0_g_max is not None and a0_g_max >= g_max:
        return False, wise
    return g_faintest is None or g_faintest < g_max - tol, wise


def refcat_state(data: Path, zone: str) -> dict:
    """What the mirror's refcat/<zone>.parquet holds and what step a0 must do there (`todo`, `ok`, `missing`)."""
    import pandas as pd

    from skyshift_pipeline import config as C
    p, m = data / "refcat" / f"{zone}.parquet", data / "refcat" / f"{zone}_catalogs.json"
    if not p.exists():
        return {"zone": zone, "state": "missing"}
    marker = json.loads(m.read_text()) if m.exists() else None
    made = None if marker is None else list(marker.get("catalogs", []))
    a0 = (marker or {}).get("rebuild_a0") or {}
    df = pd.read_parquet(p, columns=["cat", "g_mag"])
    g = df.loc[df["cat"] == "gaia", "g_mag"].dropna()
    gf = round(float(g.max()), 3) if len(g) else None
    rows = {str(k): int(v) for k, v in df["cat"].value_counts().items()}
    gaia, wise = refcat_need(made, gf, C.REFCAT_GAIA_G_MAX, a0.get("gaia_g_max"))
    wise = wise or not (rows.get("catwise") or rows.get("2mass"))  # e.g. a Gaia-only refcat copied without its marker
    return {"zone": zone, "state": "todo" if gaia or wise else "ok", "gaia": gaia, "wise": wise, "made": made,
            "g_faintest": gf, "g_max": C.REFCAT_GAIA_G_MAX, "rows": rows,
            "bytes": p.stat().st_size, "done_here": bool(a0), "gspc": bool((marker or {}).get("gspc"))}


def _zone_list(a) -> list[str]:
    if getattr(a, "zones_file", None):
        return [z for z in Path(a.zones_file).read_text().split() if z]
    return [z for z in (a.zones or "").split(",") if z]


def cmd_refcat_status(a) -> int:
    data = Path(a.data)
    rows = [refcat_state(data, z) for z in _zone_list(a)]
    todo = [r for r in rows if r["state"] == "todo"]
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))
    from skyshift_pipeline import config as C
    print(f"refcat (a0), config REFCAT_GAIA_G_MAX {C.REFCAT_GAIA_G_MAX}: {len(rows)} zones; "
          f"{sum(r['state'] == 'ok' for r in rows)} complete, {len(todo)} to complete "
          f"({sum(r['wise'] for r in todo)} without CatWISE/2MASS, {sum(r['gaia'] for r in todo)} with Gaia too shallow), "
          f"{sum(r['state'] == 'missing' for r in rows)} not mirrored yet")
    for r in todo if a.verbose else []:
        print(f"  {r['zone']:<12} {'+catwise,2mass ' if r['wise'] else ''}{'gaia again (faintest G ' + str(r['g_faintest']) + ')' if r['gaia'] else ''}")
    return 0


def seed_tiles(data: Path, zone, cat: str, rows) -> int:
    """refcat rows of one catalogue written back as its cached tiles (cache/refcat/<zone>/<cat>_<tile>.parquet), each row
    in the tile its position falls in (clipped to the box), so refcat.run keeps them and asks only for the rest."""
    import numpy as np

    from skyshift_pipeline import refcat
    from skyshift_pipeline.store import Store
    rows = rows.copy()
    for c in refcat.REFCAT_COLUMNS:
        if c not in rows:
            rows[c] = refcat._default(c, len(rows))
    rows = rows[list(refcat.REFCAT_COLUMNS)].astype(refcat.REFCAT_COLUMNS).reset_index(drop=True)
    ts = refcat.tiles(*refcat.query_box(zone))
    ra_e, de_e = sorted({t.ra0 for t in ts}), sorted({t.dec0 for t in ts})
    i = np.clip(np.searchsorted(ra_e, rows["ra"].to_numpy(float), side="right") - 1, 0, len(ra_e) - 1)
    j = np.clip(np.searchsorted(de_e, rows["dec"].to_numpy(float), side="right") - 1, 0, len(de_e) - 1)
    key = i * len(de_e) + j
    store, n = Store(str(data)), 0
    for t in ts:
        part = rows[key == ra_e.index(t.ra0) * len(de_e) + de_e.index(t.dec0)].reset_index(drop=True)
        store.write_parquet(refcat.tile_rel(zone.name, cat, t), part)
        n += len(part)
    if n != len(rows):
        fail(f"refcat {zone.name}: {len(rows)} {cat} rows but {n} written into tiles")
    return n


def cmd_refcat(a) -> int:
    """Step a0 for one zone: complete the mirror's refcat (every catalogue, Gaia to config REFCAT_GAIA_G_MAX)."""
    import shutil

    os.environ["SKYSHIFT_REFCAT_S3_FIRST"] = "1"  # before the pipeline's config is imported
    import pandas as pd

    from skyshift_pipeline import config as C
    from skyshift_pipeline import log as plog
    from skyshift_pipeline import refcat
    from skyshift_pipeline.store import Store
    plog.setup()
    C.REFCAT_S3_FIRST = True
    data, z = Path(a.data), a.zone
    if z not in C.ZONES:
        fail(f"unknown zone {z!r} (pipeline config ZONES)")
    zone = C.ZONES[z]
    st = refcat_state(data, z)
    if st["state"] == "missing":
        fail(f"{data}/refcat/{z}.parquet is not in the mirror: run step a first")
    if st["state"] == "ok":
        print(json.dumps({"zone": z, "changed": False, "rows": st["rows"], "g_faintest": st["g_faintest"]}))
        return 0
    rdir = data / "refcat"
    if not st["done_here"]:  # the bucket's own files, kept once (to compare, or to put back with a plain copy)
        for src, dst in ((f"{z}.parquet", f"{z}.bucket.parquet"), (f"{z}_catalogs.json", f"{z}_catalogs.bucket.json")):
            if (rdir / src).exists() and not (rdir / dst).exists():
                shutil.copy2(rdir / src, rdir / dst)
    old = pd.read_parquet(rdir / f"{z}.parquet")
    tdir = data / "cache" / "refcat" / z
    tdir.mkdir(parents=True, exist_ok=True)
    rec, want = tdir / "a0_gaia.txt", f"G<{C.REFCAT_GAIA_G_MAX}"
    if st["gaia"]:
        if not rec.exists() or rec.read_text().strip() != want:  # tiles of another depth (the tile key ignores G)
            for f in tdir.glob("gaia_*.parquet"):
                f.unlink()
            rec.write_text(want + "\n")
    else:
        seed_tiles(data, zone, "gaia", old[old["cat"] == "gaia"])
        rec.write_text("seeded from the refcat\n")
    if not st["wise"]:
        for cat in ("catwise", "2mass"):
            seed_tiles(data, zone, cat, old[old["cat"] == cat])
    del old
    log(f"refcat {z}: {'Gaia to ' + want + ' from ESA' if st['gaia'] else 'Gaia kept'}; "
        f"{'CatWISE + 2MASS from AllWISE on S3' if st['wise'] else 'CatWISE + 2MASS kept'} (before: {st['rows']})")
    t0 = time.time()
    out = refcat.run(Store(str(data)), zone, workers=a.workers, catalogs=refcat.FULL, force=True)
    marker = json.loads((rdir / f"{z}_catalogs.json").read_text())
    if not st["gaia"]:
        marker["gspc"] = st["gspc"]  # Gaia rows kept as they were: so is their GSPC state
    marker["rebuild_a0"] = {
        "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": "scripts/rebuild/rebuild.py refcat",
        "gaia": "fetched" if st["gaia"] else "kept", "gaia_g_max": C.REFCAT_GAIA_G_MAX if st["gaia"] else None,
        "catwise_2mass": "fetched, AllWISE on S3 first" if st["wise"] else "kept",
        "before": {"catalogs": st["made"], "rows": st["rows"], "g_faintest": st["g_faintest"]}, "rows": out.get("rows")}
    (rdir / f"{z}_catalogs.json").write_text(json.dumps(marker, indent=1))
    after = refcat_state(data, z)
    print(json.dumps({"zone": z, "changed": True, "rows": after["rows"], "g_faintest": after["g_faintest"],
                      "state": after["state"], "seconds": round(time.time() - t0)}))
    if after["state"] != "ok":
        fail(f"refcat {z} is still incomplete after the run: {after}")
    return 0


def cmd_upload_refcat(a) -> int:
    """--upload-refcat: the refcats step a0 completed, to the bucket. The bucket's own files of those zones are first
    copied (server side, no-clobber) to REFCAT_BACKUP, then replaced. Nothing else is written; nothing is deleted."""
    data, work = Path(a.data), Path(a.work)
    done_f = work / "refcat_uploaded.tsv"
    done = dict(ln.split("\t")[:2] for ln in done_f.read_text().splitlines() if ln) if done_f.exists() else {}
    todo = []
    for z in _zone_list(a):
        m = data / "refcat" / f"{z}_catalogs.json"
        if not m.exists() or not json.loads(m.read_text()).get("rebuild_a0"):
            continue
        files = [data / "refcat" / f"{z}.parquet", data / "refcat" / f"{z}_forced.parquet", m]
        if not all(f.exists() for f in files):
            fail(f"refcat {z}: {[str(f) for f in files if not f.exists()]} missing; rerun step a0 (--redo a0)")
        sig = ",".join(str(f.stat().st_size) for f in files)
        if done.get(z) != sig:
            todo.append((z, files, sig))
    if not todo:
        log("--upload-refcat: nothing new to upload")
        return 0
    have = dict(gls(f"{BUCKET}/refcat/*"))
    nb = sum(f.stat().st_size for _, fs, _ in todo for f in fs)
    old = sum(have.get(f"{BUCKET}/refcat/{f.name}", 0) for _, fs, _ in todo for f in fs)
    print(f"\n--upload-refcat: {len(todo)} zones' completed refcats, {nb / GB:.2f} GB to {BUCKET}/refcat/ "
          f"(refcat/<zone>.parquet, _forced.parquet, _catalogs.json; upload traffic is free).\n"
          f"  First the bucket's own {old / GB:.2f} GB of those files are copied, server side, no-clobber, to "
          f"{BUCKET}/{REFCAT_BACKUP}/ (~${old / GB * NEARLINE_READ_GB:.2f} Nearline retrieval), then replaced.\n"
          f"  Storage: +{nb / GB:.1f} GB (the backups keep the old {old / GB:.1f} GB) = ~${nb / GB * NEARLINE_GB_MONTH:.2f}"
          f"/month on Nearline (30 days minimum); ~{len(todo) * 6} operations, under $0.01.")
    if not a.yes:
        try:
            ans = input("Upload? [y/N] ")
        except EOFError:
            ans = ""
        if ans.strip().lower() not in ("y", "yes"):
            fail("nothing uploaded (your answer)")
    for z, files, sig in todo:
        src = [f"{BUCKET}/refcat/{f.name}" for f in files if f"{BUCKET}/refcat/{f.name}" in have]
        if src:
            r = subprocess.run(["gcloud", "storage", "cp", "--no-clobber", *src, f"{BUCKET}/{REFCAT_BACKUP}/"], check=False)
            if r.returncode != 0:
                fail(f"refcat {z}: the backup copy failed (exit {r.returncode}); nothing of {z} was replaced")
        r = subprocess.run(["gcloud", "storage", "cp", *map(str, files), f"{BUCKET}/refcat/"], check=False)
        if r.returncode != 0:
            fail(f"refcat {z}: the upload failed (exit {r.returncode}); rerun to resume")
        with open(done_f, "a") as f:
            f.write(f"{z}\t{sig}\t{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        with open(data / "DOWNLOADS.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} pc-rebuild UPLOAD refcat {z} "
                    f"{sum(x.stat().st_size for x in files) / 1e6:.1f} MB (old files kept in {REFCAT_BACKUP}/)\n")
        log(f"refcat {z}: uploaded")
    log(f"--upload-refcat: {len(todo)} zones uploaded; the old files are in {BUCKET}/{REFCAT_BACKUP}/")
    return 0


# ------------------------------------------------------------------------------------------------ m: the PR #388 chain

# PR #388 (16 Nov) changed crossid (2MASS borrowed-motion radius), classify (brown_dwarf_like water, AllWISE AGN veto),
# astrometry (per-visit positions, astrometry_visits.parquet), highpm (deep counterpart check, highpm_visits.parquet) and
# shape (IRSA neighbours, offset floor, blocked splits). Their tables only change after a per-zone rerun of this chain,
# before the export (c). Step m runs it untiled per zone on the local mirror (single steps never tile; the sky run tiled
# `all` only), the zone's inputs copied first, the outputs written locally, never to the bucket.
MOTION_CHAIN = ("crossid", "classify", "astrometry", "highpm", "shape")
# What the chain reads from results/<zone>/ besides the mirror of (a) (fixed, summary, photo list) and its own outputs
# (crossids → classify, astrometry, highpm; classes → shape; astrometry + astrometry_visits → highpm):
#   sources   crossid (ra, dec), classify (every column), highpm (+ flux_ujy spectra), shape
#   members   crossid, classify, astrometry, highpm, shape (Store.read_parquet reads the whole file, whatever the columns)
#   calib.json, calib_photos.parquet   astrometry (motion_io.member_dots: calibrated positions)
#   fragments highpm, shape;  photoz  shape
MOTION_TABLES = ("sources.parquet", "members.parquet", "calib.json", "calib_photos.parquet", "fragments.parquet",
                 "photoz.parquet")
# ... and the zone's dot files, whole: classify (load_shape_dots: whole file) and astrometry (motion_io.member_dots_raw:
# whole file, every row for each photo's Jacobian) read every member photo's; shape reads 8 columns of 38% of them.
MOTION_OUTPUTS = {"crossid": ("crossids.parquet",), "classify": ("classes.parquet",),
                  "astrometry": ("astrometry.parquet", "astrometry_visits.parquet", "astrometry_report.json"),
                  "highpm": ("highpm_candidates.parquet", "highpm_visits.parquet", "highpm_report.json"),
                  "shape": ("shape_candidates.parquet", "shape_report.json", "shape_fit_report.json")}
# Live queries (positions only, free): classify IRSA AllWISE (brown_dwarf_like rows); highpm ESA Gaia DR3 TAP + IRSA
# CatWISE2020 and AllWISE (its candidates' tracks); shape IRSA AllWISE + Gaia DR3 (listed rows) and NASA's S3 photos
# (range reads, the two-source fit of the listed rows).
MOTION_QUERIES = ("classify: IRSA TAP AllWISE (brown_dwarf_like rows' positions)",
                  "highpm: ESA Gaia DR3 TAP + IRSA TAP CatWISE2020 and AllWISE (candidates' positions and tracks)",
                  "shape: IRSA TAP AllWISE + Gaia DR3 (listed rows' positions); NASA S3 range reads of their photos")


def zone_dots_dir(data: Path, zone: str) -> tuple[str, bool]:
    """(the dot-file folder the zone's link read, exact): results/<zone>/summary.json steps.link.dots_dir in the mirror,
    else the pipeline config's folder for the zone."""
    for name in ("summary.bucket.json", "summary.json"):
        p = data / "results" / zone / name
        if p.exists():
            try:
                d = (json.loads(p.read_text()).get("steps", {}).get("link") or {}).get("dots_dir")
            except ValueError:
                d = None
            if d:
                return str(d).strip("/"), True
    try:
        from skyshift_pipeline import config as C
        return C.zone_dots(zone)[0].strip("/"), False
    except Exception:  # noqa: BLE001  no pipeline env (a dry run before setup)
        return "dots", False


def motion_zone_order(zones: list[str]) -> list[str]:
    """Zones along the sky (RA of the box centre, then Dec), so zones that share photos run one after the other and
    the shared dot files leave the disk sooner."""
    try:
        from skyshift_pipeline import config as C
        key = {z: ((C.ZONES[z].ra_min + C.ZONES[z].ra_max) / 2, (C.ZONES[z].dec_min + C.ZONES[z].dec_max) / 2)
               for z in zones if z in C.ZONES}
    except Exception:  # noqa: BLE001
        key = {}
    return sorted(zones, key=lambda z: (*key.get(z, (999.0, 0.0)), z))


def motion_disk_peak(order: list[str], files: dict[str, set[str]], size: dict[str, int]) -> int:
    """Largest bytes of dot files on disk at once when the zones run in `order`, one at a time, each file copied before
    its first zone and deleted after its last."""
    last = {}
    for i, z in enumerate(order):
        for f in files.get(z, ()):
            last[f] = i
    have: set[str] = set()
    peak = 0
    for i, z in enumerate(order):
        have |= files.get(z, set())
        peak = max(peak, sum(size[f] for f in have))
        have = {f for f in have if last[f] > i}
    return peak


def cmd_mplan(a) -> int:
    """Step m's plan: per zone the tables and dot files it copies from the bucket (exact when the zone's photo list and
    summary are mirrored, else an estimate from the folder's mean file size), GB, $, the disk peak; motion_manifest.tsv."""
    data, work = Path(a.data), Path(a.work)
    ldir = work / "plan" / "listing"
    zones = motion_zone_order(_zone_list(a))
    res = {}
    for url, size in gls(f"{BUCKET}/results/**", ldir):
        parts = rel_of(url).split("/")
        if len(parts) == 3 and parts[2] in MOTION_TABLES:
            res[(parts[1], parts[2])] = (url, size)
    folders: dict[str, dict[str, int]] = {}
    rows: list[tuple[str, str, str, str, int]] = []
    files: dict[str, set[str]] = {}
    size: dict[str, int] = {}
    per: dict[str, dict] = {}
    for z in zones:
        ddir, exact_dir = zone_dots_dir(data, z)
        if ddir not in folders:
            folders[ddir] = {rel_of(u).split("/", 1)[1]: s for u, s in gls(f"{BUCKET}/{ddir}/*", ldir)}
        listing = folders[ddir]
        mean = (sum(listing.values()) / len(listing)) if listing else DOT_FILE_MB * 1e6
        t = {"tables_bytes": 0, "dots_files": 0, "dots_bytes": 0, "dots_missing": 0, "dots_dir": ddir, "exact": False,
             "dir_from": "summary" if exact_dir else "config"}
        for name in MOTION_TABLES:
            if (z, name) in res:
                url, s = res[(z, name)]
                rows.append(("mtables", z, url, f"results/{z}/{name}", s))
                t["tables_bytes"] += s
        pl = data / "photolists" / "zones" / f"{z}.parquet"
        if pl.exists():
            import pandas as pd
            p = pd.read_parquet(pl, columns=["obs_id", "detector"]).drop_duplicates()
            names = {f"{o}D{int(d)}.parquet" for o, d in zip(p["obs_id"].astype(str), p["detector"])}
            files[z] = set()
            for n in sorted(names):
                if n not in listing:
                    t["dots_missing"] += 1
                    continue
                rel = f"{ddir}/{n}"
                rows.append(("mdots", z, f"{BUCKET}/{rel}", rel, listing[n]))
                files[z].add(rel)
                size[rel] = listing[n]
                t["dots_files"] += 1
                t["dots_bytes"] += listing[n]
            t["exact"] = True
        else:
            n, _ = photo_count(data, z, 0)
            n = n or 0
            t["dots_files"], t["dots_bytes"] = n, int(n * mean)
        per[z] = t
    out = work / "plan"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "motion_manifest.tsv", "w") as f:
        f.write("category\tzone\turl\tlocal\tbytes\thave\n")
        for cat, z, url, rel, s in rows:
            f.write(f"{cat}\t{z}\t{url}\t{rel}\t{s}\t{int(_ok(data / rel, s))}\n")
    uniq = sum(size.values()) + sum(t["dots_bytes"] for z, t in per.items() if z not in files)
    tables = sum(t["tables_bytes"] for t in per.values())
    have = sum(s for cat, z, url, rel, s in {r[3]: r for r in rows}.values() if _ok(data / rel, s))
    peak = motion_disk_peak(zones, files, size) if files else 0
    est = [z for z in zones if not per[z]["exact"]]
    total = tables + uniq
    plan = {"zones": zones, "per_zone": per, "tables_bytes": tables, "dots_bytes_unique": uniq, "have_bytes": have,
            "total_bytes": total, "todo_bytes": max(0, total - have), "dots_peak_bytes": peak, "estimated_zones": est,
            "queries": list(MOTION_QUERIES), "chain": list(MOTION_CHAIN)}
    (out / "motion_plan.json").write_text(json.dumps(plan, indent=1))
    print(f"(m) {' -> '.join(MOTION_CHAIN)} per zone, untiled, on the local mirror (PR #388); one zone at a time.")
    print("  Reads from the bucket (gcloud storage cp, no-clobber), per zone:")
    print(f"    results/<zone>/ {', '.join(MOTION_TABLES)} (kept: the export then reads them locally)")
    print("    every photo's dot file <dots dir>/<obs_id>D<det>.parquet, whole (classify's shape dots and astrometry's member")
    print("    dots read whole files); deleted after the last zone that needs it")
    print("    already mirrored by (a): fixed.parquet, summary.json, photolists/zones/<zone>.parquet, refcat (a0)")
    print(f"  {'zone':<10} {'dots dir':<28} {'dot files':>9} {'dots GB':>8} {'tables GB':>9}")
    for z in zones:
        t = per[z]
        print(f"  {z:<10} {t['dots_dir']:<28} {t['dots_files']:>9,} {t['dots_bytes'] / GB:>8.2f} {t['tables_bytes'] / GB:>9.2f}"
              f"{'' if t['exact'] else '  (estimate)'}{'' if t['dir_from'] == 'summary' else '  (folder from config)'}"
              f"{'  ' + str(t['dots_missing']) + ' photos without a dot file' if t['dots_missing'] else ''}")
    print(f"  total: tables {tables / GB:.1f} GB + dot files {uniq / GB:.1f} GB (each copied once; shared by zones: "
          f"{sum(t['dots_bytes'] for t in per.values()) / GB:.1f} GB counted per zone) = {total / GB:.1f} GB, "
          f"{money(total / GB)} at ${PRICE_PER_GB}/GB; already here {have / GB:.1f} GB")
    if peak:
        print(f"  disk: dot files at most {peak / GB:.0f} GB at once (zones in sky order); tables {tables / GB:.0f} GB kept")
    if est:
        print(f"  estimated (photo list not mirrored yet): {' '.join(est)}; exact after step a")
    print("  Live queries (positions only, free): " + "; ".join(MOTION_QUERIES))
    pj = out / "plan.json"
    if pj.exists():  # the whole run with (m): its tables are local for the export, which then reads only dot-file flags
        base = json.loads(pj.read_text())
        a_gb, m_gb, c_gb = base["mirror_todo"] / GB, plan["todo_bytes"] / GB, base["export"]["flags_gb"]
        print(f"  Download in all with (m): (a) {a_gb:.1f} + (m) {m_gb:.1f} + (c) ~{c_gb:.0f} GB (dot-file flags; the tables "
              f"are local after m) = ~{a_gb + m_gb + c_gb:.0f} GB, ~{money(a_gb + m_gb + c_gb)}; optional (k) +57 GB, ~$7")
    return 0


def cmd_motion(a) -> int:
    """Step m, one step of the chain for one zone: the pipeline's own code on an overlay (the mirror first, the bucket
    read only for anything not copied; every write into the mirror)."""
    store = make_overlay(a.data, a.bucket, a.block, a.offline, write_local=True)
    t0 = time.time()
    if a.step == "shape":
        from skyshift_pipeline import shape
        from skyshift_pipeline import store as pstore
        pstore.Store = lambda root: store  # shape.main builds its Store from --root: hand it the overlay
        out = Path(a.data) / "results" / a.zone
        rc = int(shape.main(["--zone", a.zone, "--root", a.bucket, "--out", str(out), "--workers", str(a.workers)]) or 0)
    else:
        from skyshift_pipeline import cli
        cli.Store = lambda root: store  # cli.main builds its Store from --root: hand it the overlay
        rc = int(cli.main([a.step, "--zone", a.zone, "--root", a.bucket, "--workers", str(a.workers)]) or 0)
    fs = store.fs
    log(f"m {a.zone} {a.step}: exit {rc} in {time.time() - t0:.0f} s; bucket reads beyond the mirror: {fs.remote_files} files, "
        f"{fs.remote_bytes / 1e6:.1f} MB whole + {fs.remote_opens} opened by column{': ' + ', '.join(fs.remote_names[:5]) if fs.remote_names else ''}")
    missing = [n for n in MOTION_OUTPUTS[a.step] if not (Path(a.data) / "results" / a.zone / n).exists()]
    if rc == 0 and missing and a.step != "shape":
        fail(f"m {a.zone} {a.step}: {missing} not written")
    return rc


def cmd_mclean(a) -> int:
    """Step m: delete the mirrored dot files no zone still to run needs (zones done: <stamps>/m.<zone>)."""
    data, work, stamps = Path(a.data), Path(a.work), Path(a.stamps)
    need: dict[str, set[str]] = defaultdict(set)
    for r in read_manifest(work, "motion_manifest.tsv"):
        if r["category"] == "mdots":
            need[r["local"]].add(r["zone"])
    n = nb = 0
    for rel, zs in need.items():
        if all((stamps / f"m.{z}").exists() for z in zs):
            p = data / rel
            if p.exists():
                nb += p.stat().st_size
                p.unlink()
                n += 1
    log(f"m: {n} dot files no zone still needs deleted ({nb / GB:.2f} GB)")
    return 0


# ------------------------------------------------------------------------------------------------ estimate

def mover_files(data: Path, zone: str) -> tuple[int, int, int, dict]:
    """From the new suspects table: (dot files whose flags the export reads: grade A/B movers' dot lists; dot files
    whose positions it reads: every mover's photos, only when the table has no session_rate_arcsec_h; movers; grades)."""
    import pandas as pd
    import pyarrow.parquet as pq
    p = data / "results" / zone / "suspects.parquet"
    have = set(pq.ParquetFile(p).schema_arrow.names)
    cols = ["obs_ids", "detectors", "dot_obs", "dot_det", "grade"]
    s = pd.read_parquet(p, columns=[c for c in cols if c in have])
    ab = s[s["grade"].isin(["A", "B"])] if "grade" in s else s
    flag_files = set()
    if "dot_obs" in ab:
        for os_, ds in zip(ab["dot_obs"], ab["dot_det"]):
            for o, d in zip(os_, ds):
                flag_files.add((str(o), int(d)))
    pos_files = set()
    if "session_rate_arcsec_h" not in have:
        for o, d in zip(s.get("obs_ids", []), s.get("detectors", [])):
            for oo in str(o).split(","):
                for dd in str(d).split(","):
                    if oo and dd:
                        pos_files.add((oo, int(dd)))
    grades = s["grade"].value_counts().to_dict() if "grade" in s else {}
    return len(flag_files), len(pos_files), len(s), grades


def cmd_estimate(a) -> int:
    data, work = Path(a.data), Path(a.work)
    plan = json.loads((work / "plan" / "plan.json").read_text())
    zones = plan["zones"]
    nab = npos = nmov = photos = 0
    grades = Counter()
    old = []
    for z in zones:
        f, q, m, g = mover_files(data, z)
        nab, npos, nmov = nab + f, npos + q, nmov + m
        old += [z] if q else []
        grades.update(g)
        photos += photo_count(data, z, 0)[0]
    files = plan["export"].get("files")
    # tables already in the mirror (step m copies sources, members, fragments, photoz and writes crossids, classes,
    # highpm_candidates, shape_candidates) are read locally
    tables = (sum(s for rel, s in files if not (data / rel).exists()) if files is not None
              else int(plan["export"]["tables_gb"] * GB))
    exp = export_estimate(photos, tables, nab, npos)
    free = _free_gb(data)
    print(f"Suspects after (b): {nmov:,} movers in {len(zones)} zones; grades {dict(sorted(grades.items()))}")
    print(f"Export (c) reads from the bucket by column: tables {exp['tables_gb']:.1f} GB + flags (~{FIXED_PHOTO_SHARE:.0%} "
          f"of {photos:,} photos, {nab:,} A/B movers' dot files) {exp['flags_gb']:.1f} GB + positions "
          f"{exp['positions_gb']:.1f} GB = ~{exp['total_gb']:.0f} GB, ~{money(exp['total_gb'])} (x0.6-x1.6)")
    if old:
        print(f"Note: no session_rate_arcsec_h in {' '.join(old)}: step b did not rewrite them (old pipeline code?); "
              f"their movers' dot positions are read ({npos:,} files)")
    print(f"Disk: the export cache needs ~{exp['cache_gb']:.0f} GB; {free:.0f} GB free under {data}")
    (work / "plan" / "export_estimate.json").write_text(json.dumps(exp, indent=1))
    if free < exp["cache_gb"] + 10:
        fail(f"not enough disk: the export cache needs ~{exp['cache_gb']:.0f} GB (+10 GB margin), {free:.0f} GB free")
    return 0


def _free_gb(p: Path) -> float:
    st = os.statvfs(p)
    return st.f_bavail * st.f_frsize / GB


# ------------------------------------------------------------------------------------------------ export overlay

class _OverlayFS:
    """The few filesystem calls the pipeline makes on Store.fs, routed per file: the local mirror when the file is
    there, else the bucket (read only). Paths are the bucket's (Store.path)."""

    def __init__(self, store):
        self.s = store
        self.remote_files = self.remote_bytes = self.remote_opens = 0  # what was read beyond the mirror (step m logs it)
        self.remote_names: list[str] = []

    def _note(self, path: str, nbytes: int | None) -> None:
        if nbytes is None:
            self.remote_opens += 1
        else:
            self.remote_files += 1
            self.remote_bytes += nbytes
        if len(self.remote_names) < 20:
            self.remote_names.append(str(path).rsplit("/", 2)[-2] + "/" + str(path).rsplit("/", 1)[-1])

    def _route(self, path: str):
        lp = self.s.local_of_path(path)
        if lp is not None:
            return None, lp
        if self.s.offline:
            raise FileNotFoundError(f"offline: {path} is not in the local mirror")
        return self.s.remote, path

    def open(self, path, mode="rb", **kw):
        if "r" not in mode or "+" in mode:
            raise PermissionError(f"the rebuild never writes to the bucket ({path})")
        fs, p = self._route(path)
        if fs is None:
            return open(p, mode)
        self._note(p, None)
        return fs.open(p, mode, **kw)

    def cat_file(self, path, start=None, end=None, **kw):
        fs, p = self._route(path)
        if fs is not None:
            out = fs.cat_file(p, start=start, end=end, **kw)
            self._note(p, len(out))
            return out
        with open(p, "rb") as f:
            size = os.path.getsize(p)
            s = 0 if start is None else (start if start >= 0 else max(0, size + start))
            e = size if end is None else (end if end >= 0 else size + end)
            f.seek(s)
            return f.read(max(0, e - s))

    def exists(self, path, **kw):
        if self.s.local_of_path(path) is not None:
            return True
        return False if self.s.offline else self.s.remote.exists(path)

    def info(self, path, **kw):
        lp = self.s.local_of_path(path)
        if lp is not None:
            return {"name": path, "size": os.path.getsize(lp), "type": "file", "mtime": os.path.getmtime(lp)}
        if self.s.offline:
            raise FileNotFoundError(path)
        return self.s.remote.info(path)

    def size(self, path):
        return self.info(path)["size"]

    def ls(self, path, detail=False, **kw):
        names = set()
        lp = self.s.local_of_path(path, dirs=True)
        if lp is not None and os.path.isdir(lp):
            names |= {f"{path.rstrip('/')}/{n}" for n in os.listdir(lp)}
        if not self.s.offline:
            try:
                names |= set(self.s.remote.ls(path, detail=False))
            except FileNotFoundError:
                pass
        if not names:
            raise FileNotFoundError(path)
        return sorted(names)

    def __getattr__(self, name):
        if name in ("pipe_file", "put_file", "put", "rm", "rm_file", "mv", "copy", "makedirs", "mkdir", "touch",
                    "write_bytes", "write_text", "pipe"):
            raise PermissionError(f"the rebuild never writes to the bucket (fs.{name})")
        raise AttributeError(f"overlay: fs.{name} is not supported (scripts/rebuild/rebuild.py _OverlayFS)")


def make_overlay(local_root: str, bucket: str, block: int, offline: bool, write_local: bool = False):
    """The overlay store; write_local (step m): writes go into the mirror (local_root/<rel>), never to the bucket."""
    from skyshift_pipeline.store import Store

    class OverlayStore(Store):
        """Store with the bucket's root (so the exporter's column cache keys are the bucket's) that reads each file from
        the local mirror when it is there (the new suspects tables, the mirrored inputs), else from the bucket by column
        with a small read block (gcsfs' default 5 MiB block fetches a whole 3 MB dot file for one column)."""

        def __init__(self):  # Store.__init__ is not called: no default filesystem
            import fsspec
            self.root = bucket.rstrip("/")
            self.local_base = os.path.abspath(local_root)
            self.offline = offline
            self.remote, base = (None, self.root.split("://", 1)[1]) if offline else fsspec.core.url_to_fs(self.root, block_size=block)
            self.base = base.rstrip("/")
            self.local = False
            self.fs = _OverlayFS(self)

        def local_of_path(self, path: str, dirs: bool = False) -> str | None:
            p = str(path)
            for pre in (f"{self.root}/", f"{self.base}/"):
                if p.startswith(pre):
                    p = p[len(pre):]
                    break
            else:
                return None
            lp = os.path.join(self.local_base, p)
            return lp if (os.path.isfile(lp) or (dirs and os.path.isdir(lp))) else None

        def exists(self, rel: str) -> bool:
            return self.fs.exists(self.path(rel))

        def read_bytes(self, rel: str) -> bytes:
            return self.fs.cat_file(self.path(rel))

        def write_bytes(self, rel: str, data: bytes) -> None:
            if not write_local:
                raise PermissionError(f"the rebuild never writes to the bucket ({rel})")
            p = os.path.join(self.local_base, rel.lstrip("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = f"{p}.tmp-{os.getpid()}"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, p)

        def listdir(self, rel: str) -> list[str]:
            try:
                return [os.path.basename(x.rstrip("/")) for x in self.fs.ls(self.path(rel))]
            except FileNotFoundError:
                return []

    return OverlayStore()


def cmd_export(a, extra: list[str]) -> int:
    from skyshift_pipeline import candidates as X
    from skyshift_pipeline import config as C

    store = make_overlay(a.data, a.bucket, a.block, a.offline)
    argv = ["--root", a.bucket, "--out", a.out, "--workers", str(a.workers)]
    if a.cache:
        argv += ["--cache", a.cache]
    if a.zones:
        want = [z for z in a.zones.split(",") if z]
        # the exporter's own order (finished_zones: largest box first, the hunt before the pilot inside it)
        order = sorted(want, key=lambda z: -((C.ZONES[z].ra_max - C.ZONES[z].ra_min) * (C.ZONES[z].dec_max - C.ZONES[z].dec_min))
                       if z in C.ZONES else 0)
        argv += ["--zones", ",".join(order)]
    argv += extra
    X.Store = lambda root: store  # the exporter's main() builds its Store from --root: hand it the overlay
    log(f"skyshift-candidates {' '.join(argv)} (overlay: {a.data} first, then {a.bucket}{' OFFLINE' if a.offline else ''}, "
        f"read block {a.block} B)")
    return int(X.main(argv) or 0)


# ------------------------------------------------------------------------------------------------ combine

def more_files(folder: Path | None) -> list[Path]:
    return sorted(folder.glob("*.json")) if folder and folder.is_dir() else []


def cmd_combine(a) -> int:
    main = json.loads(Path(a.main).read_text())
    cands = list(main["candidates"])
    for p in more_files(Path(a.more) if a.more else None):
        cands += json.loads(p.read_text()).get("candidates", [])
    doc = {k: v for k, v in main.items() if k != "candidates"}
    doc["candidates"] = cands
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    log(f"{a.out}: {len(cands)} candidates ({len(main['candidates'])} from {a.main})")
    return 0


# ------------------------------------------------------------------------------------------------ report (f)

def _load(p: Path) -> dict:
    def bad_const(x):
        raise ValueError(f"{p}: {x} is not valid JSON")
    return json.loads(p.read_text(), parse_constant=bad_const)


def to_check(c: dict) -> bool:
    """The site's "To check" (web/src/discover/filters.ts setAsideOf, as pixel_check.select_movers reads it)."""
    return (c["kind"] == "mover" and (c.get("status") or "open") == "open" and c.get("grade") != "C"
            and ("motion_not_significant" not in (c.get("reasons") or []) or c.get("grade") == "A"))


def cmd_report(a) -> int:
    d = Path(a.data_dir)
    hard, warn, out = [], [], []
    main_p = d / "candidates.json"
    if not main_p.exists():
        fail(f"{main_p} is missing")
    main = _load(main_p)
    files = [main_p] + more_files(d / "candidates-more")
    cands, where = [], {}
    for p in files:
        doc = main if p == main_p else _load(p)
        for c in doc.get("candidates", []):
            cands.append(c)
            if c["id"] in where:
                hard.append(f"id {c['id']} is in {where[c['id']]} and {p.name}")
            where[c["id"]] = p.name
    out.append(f"# To check rebuild: checks before publishing ({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())})\n")
    out.append(f"Folder: {d}. Zones in candidates.json: {len(main.get('zones', []))} ({' '.join(main.get('zones', []))}).\n")

    # sizes
    out.append("## Files\n")
    out.append("| file | MB |\n|---|---|")
    for p in files:
        mb = p.stat().st_size / 1e6
        out.append(f"| {p.relative_to(d)} | {mb:.1f} |")
        if mb > 95:
            hard.append(f"{p.name} is {mb:.0f} MB: GitHub refuses files over 100 MB (the exporter's split must keep each file smaller)")
        elif mb > 50:
            warn.append(f"{p.name} is {mb:.0f} MB (GitHub warns over 50 MB; the site loads it whole)")
    cand_dir = d / "cand"
    if cand_dir.is_dir():
        nb = sum(f.stat().st_size for f in cand_dir.rglob("*") if f.is_file())
        nd = sum(1 for x in cand_dir.iterdir() if x.is_dir())
        out.append(f"| cand/ ({nd} folders) | {nb / 1e6:.1f} |")
        big = [f for f in cand_dir.rglob("*") if f.is_file() and f.stat().st_size > 95e6]
        if big:
            hard.append(f"files over 95 MB in cand/: {[str(f) for f in big[:3]]}")
        if nb > CAND_WARN_MB * 1e6:  # every file is committed to git (zlib about a third of it)
            warn.append(f"cand/ holds {nb / 1e6:.0f} MB in {nd} folders, all committed to git: tell the orchestrator before "
                        f"merging (expected ~{MOVER_FRAMES_MB} MB per mover with frames, --frames-top of them)")
    out.append("")

    # well-formed
    need = ("id", "kind", "ra", "dec", "zone", "score", "status", "checks")
    malformed = [c.get("id") for c in cands if any(k not in c for k in need)]
    if malformed:
        hard.append(f"{len(malformed)} candidates lack one of {need}: {malformed[:5]}")
    st_bad = [c["id"] for c in cands if c.get("status") not in ("open", "known", "flaw")]
    if st_bad:
        hard.append(f"{len(st_bad)} candidates with a status other than open/known/flaw: {st_bad[:5]}")
    kn_bad = [c["id"] for c in cands if c.get("status") == "known" and not c.get("known_name")]
    if kn_bad:
        hard.append(f"{len(kn_bad)} known candidates without known_name: {kn_bad[:5]}")
    if not cands:
        hard.append("no candidates at all")

    # counts
    kinds = Counter(c["kind"] for c in cands)
    out.append("## Counts\n")
    out.append("| kind | all | open | known | flaw | grade A | grade B | grade C | in candidates.json |\n|---|---|---|---|---|---|---|---|---|")
    for k in ("mover", "high_pm", "spectral", "shape"):
        cs = [c for c in cands if c["kind"] == k]
        st = Counter(c.get("status") for c in cs)
        gr = Counter(c.get("grade") for c in cs)
        inmain = sum(1 for c in cs if where[c["id"]] == "candidates.json")
        out.append(f"| {k} | {len(cs)} | {st['open']} | {st['known']} | {st['flaw']} | {gr['A']} | {gr['B']} | {gr['C']} | {inmain} |")
    tc = [c for c in cands if to_check(c)]
    out.append(f"\nTo check (site rule: open movers, not grade C, motion significant unless A): **{len(tc)}**.\n")
    for k in ("mover",):
        if kinds[k] == 0:
            warn.append("no movers at all")
    zc = defaultdict(Counter)
    for c in cands:
        if c["kind"] == "mover":
            zc[c["zone"]][c.get("grade") or "-"] += 1
    out.append("Movers per zone (A/B/C): " + ", ".join(f"{z} {v['A']}/{v['B']}/{v['C']}" for z, v in sorted(zc.items())) + "\n")

    # checks
    lv = Counter((ch["code"], ch["level"]) for c in cands for ch in (c.get("checks") or []))
    codes = sorted({k[0] for k in lv}, key=lambda x: -sum(v for (k, _), v in lv.items() if k == x))
    out.append("## Checks (code: strong / warn / ok)\n")
    out.append(", ".join(f"{k} {lv[(k, 'strong')]}/{lv[(k, 'warn')]}/{lv[(k, 'ok')]}" for k in codes[:40]) + "\n")
    nochk = sum(1 for c in cands if not c.get("checks"))
    if nochk:
        warn.append(f"{nochk} candidates have no checks at all")

    # producers' side files: ids measured but not in any file (the export they read is not this one)
    if a.checks_dir and Path(a.checks_dir).is_dir():
        out.append("## Check producers\n")
        for p in sorted(Path(a.checks_dir).glob("*.json")):
            s = json.loads(p.read_text())
            ids = s.get("candidates") or {}
            lost = [i for i in ids if i not in where]
            stc = Counter((v or {}).get("status") for v in ids.values())
            out.append(f"- {p.name}: {len(ids)} candidates measured, status known {stc['known']}, flaw {stc['flaw']}; "
                       f"not in the final files: {len(lost)}; made {s.get('made')}")
            if lost:
                hard.append(f"{p.name}: {len(lost)} ids not in the final files (it measured another export): {lost[:3]}")
        out.append("")

    # 306P
    out.append("## 306P/LINEAR (missing from the old comet list; must be listed as known)\n")
    p306 = [c for c in cands if "306P" in str(c.get("known_name") or "")]
    for c in p306:
        out.append(f"- {c['id']} grade {c.get('grade')} status {c.get('status')} ({where[c['id']]})")
    zones = set(main.get("zones", [])) | {c["zone"] for c in cands}
    if not p306:
        if zones & {"ecl01", "ecl02", "ecl03", "ecl04"}:
            hard.append("306P/LINEAR is not listed as known, but ecl01-ecl04 are in the export (expected ~10 movers, "
                        "2 of them grade A in ecl04): the known check did not run or did not merge")
        else:
            out.append("- (none expected: ecl01-ecl04 are not in this export)")
    out.append("")

    # frames
    ab = [c for c in cands if c["kind"] == "mover" and c.get("grade") in ("A", "B")]
    top = frames_top(cands, a.frames_top)
    nof = [c["id"] for c in top if not c.get("frames_url")]
    out.append(f"## Static frames\n\n{len(top) - len(nof)} of the top {len(top)} open grade A/B movers by score have "
               f"frames_url (--frames-top {a.frames_top}); {sum(1 for c in ab if c.get('frames_url'))} of all {len(ab)} "
               f"A/B movers. The others open their visit live in the Viewer.\n")
    if nof and a.expect_frames:
        (warn if len(nof) <= max(5, 0.05 * len(top)) else hard).append(
            f"{len(nof)} of the top {len(top)} open A/B movers without static frames (rerun the frames step): {nof[:5]}")

    # spot checks
    out.append("## Spot checks (open in the Viewer: https://skyshift.web.app, or locally)\n")
    rnd = random.Random(16)
    def line(c):
        chk = ", ".join(f"{ch['code']}:{ch['level']}" for ch in (c.get("checks") or [])[:6])
        return (f"- `{c['id']}` {c['kind']} grade {c.get('grade')} status {c.get('status')} score {c.get('score')}"
                f"{' known ' + c['known_name'] if c.get('known_name') else ''}: {chk}")
    ga = [c for c in cands if c["kind"] == "mover" and c.get("grade") == "A"]
    out.append(f"Every grade A mover ({len(ga)}):\n")
    out += [line(c) for c in sorted(ga, key=lambda c: -(c.get("score") or 0))[:40]]
    out.append("\nTop 10 To check:\n")
    out += [line(c) for c in sorted(tc, key=lambda c: -(c.get("score") or 0))[:10]]
    kn = [c for c in cands if c.get("status") == "known"]
    out.append(f"\nKnown ({len(kn)}), first 10:\n")
    out += [line(c) for c in kn[:10]]
    fl = [c for c in cands if c.get("status") == "flaw"]
    out.append(f"\nFlaw ({len(fl)}), first 10:\n")
    out += [line(c) for c in fl[:10]]
    gc = [c for c in cands if c["kind"] == "mover" and c.get("grade") == "C"]
    out.append("\n5 random grade C movers:\n")
    out += [line(c) for c in rnd.sample(gc, min(5, len(gc)))]
    fx = [c for c in cands if c["kind"] != "mover"]
    out.append("\n5 random high_pm / spectral / shape:\n")
    out += [line(c) for c in rnd.sample(fx, min(5, len(fx)))]
    out.append("")

    # before / after
    if a.previous:
        prev = None
        if Path(a.previous).exists():
            prev = _load(Path(a.previous))
        else:
            r = subprocess.run(["git", "-C", str(REPO), "show", f"{a.previous}:web/public/data/candidates.json"],
                               check=False, capture_output=True, text=True)
            if r.returncode == 0:
                prev = json.loads(r.stdout)
        if prev is not None:
            pc = prev["candidates"]
            out.append(f"## Before ({a.previous}) → after\n")
            out.append("| | before | after |\n|---|---|---|")
            out.append(f"| zones | {len(prev.get('zones', []))} | {len(zones)} |")
            for k in ("mover", "high_pm", "spectral", "shape"):
                out.append(f"| {k} | {sum(c['kind'] == k for c in pc)} | {kinds[k]} |")
            for g in ("A", "B", "C"):
                out.append(f"| movers grade {g} | {sum(c['kind'] == 'mover' and c.get('grade') == g for c in pc)} | "
                           f"{sum(c['kind'] == 'mover' and c.get('grade') == g for c in cands)} |")
            out.append(f"| To check | {sum(to_check(c) for c in pc)} | {len(tc)} |")
            old_ab = {c["id"] for c in pc if c["kind"] == "mover" and c.get("grade") in ("A", "B")}
            new_ab = {c["id"] for c in ab}
            out.append(f"\nGrade A/B movers: {len(old_ab & new_ab)} kept, {len(old_ab - new_ab)} gone or now C/known, "
                       f"{len(new_ab - old_ab)} new.\n")
            gone = sorted(old_ab - new_ab)[:10]
            if gone:
                out.append("First gone ids (check a few: known now? grade C now?): " + ", ".join(
                    f"`{i}` ({where.get(i, 'not listed')})" for i in gone) + "\n")
    out.append("## Result\n")
    out += [f"- HARD: {h}" for h in hard] or ["- no hard failure"]
    out += [f"- warn: {w}" for w in warn]
    text = "\n".join(out) + "\n"
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)
    print(text)
    if hard:
        fail(f"{len(hard)} hard failure(s) above; nothing is published. Report: {a.out}", 2)
    return 0


# ------------------------------------------------------------------------------------------------ fill-todo

def frames_top(cands: list[dict], n: int) -> list[dict]:
    """The movers that get static frames (frames.py --ab-top n): the n highest-scoring open movers of grade A or B
    (status open or none; ties: id). The same rule as frames.py's choose()."""
    ab = [c for c in cands if c["kind"] == "mover" and c.get("grade") in ("A", "B") and c.get("det") is not None
          and c.get("status") in (None, "open")]
    ab.sort(key=lambda c: (-(c.get("score") or 0.0), c["id"]))
    return ab[:n]


def cmd_frames_check(a) -> int:
    """Step e: '<missing> <top>' for the top --top open A/B movers of candidates.json (frames_url null = missing)."""
    cands = _load(Path(a.data_dir) / "candidates.json")["candidates"]
    top = frames_top(cands, a.top)
    print(f"{sum(1 for c in top if not c.get('frames_url'))} {len(top)}")
    return 0


def cmd_fill_todo(a) -> int:
    d = Path(a.data_dir) / "cand"
    done = set(Path(a.done).read_text().split()) if a.done and Path(a.done).exists() else set()
    ids = []
    for f in sorted(d.glob("*/frames.json")):
        if (f.parent / "fill.json").exists() or f.parent.name in done:
            continue
        try:
            clock = json.loads(f.read_text()).get("clock")
        except ValueError:
            continue
        if clock == "hours":
            ids.append(f.parent.name)
    print("\n".join(ids))
    return 0


# ------------------------------------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--data", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--zones", default="")
    p = sub.add_parser("fetch")
    p.add_argument("--data", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--zone", required=True, help="a zone, or * for the shared orbit snapshots and index")
    p.add_argument("--check", action="store_true", help="copy nothing: list what is missing (exit 1 if anything)")
    p.add_argument("--manifest", default="manifest.tsv", help="plan/<name>: manifest.tsv (a), motion_manifest.tsv (m)")
    p = sub.add_parser("estimate")
    p.add_argument("--data", required=True)
    p.add_argument("--work", required=True)
    p = sub.add_parser("export")
    p.add_argument("--data", required=True, help="the local mirror (read first)")
    p.add_argument("--bucket", default=BUCKET)
    p.add_argument("--out", required=True)
    p.add_argument("--cache", default=None)
    p.add_argument("--zones", default="")
    p.add_argument("--workers", type=int, default=32)
    p.add_argument("--block", type=int, default=16384, help="gcsfs read block (bytes)")
    p.add_argument("--offline", action="store_true", help="never touch the bucket (tests): only the mirror and --cache")
    p = sub.add_parser("combine")
    p.add_argument("--main", required=True)
    p.add_argument("--more", default=None)
    p.add_argument("--out", required=True)
    p = sub.add_parser("report")
    p.add_argument("--data-dir", required=True, help="folder with candidates.json (+ candidates-more/, cand/)")
    p.add_argument("--checks-dir", default=None)
    p.add_argument("--previous", default="origin/main", help="a git ref or a candidates.json path")
    p.add_argument("--expect-frames", action="store_true", help="stop when many of the top movers have no frames")
    p.add_argument("--frames-top", type=int, default=FRAMES_TOP, help=f"movers with static frames (default {FRAMES_TOP})")
    p.add_argument("--out", default=None)
    p = sub.add_parser("mplan")
    p.add_argument("--data", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--zones", default="")
    p.add_argument("--zones-file", default=None)
    p = sub.add_parser("motion")
    p.add_argument("--data", required=True)
    p.add_argument("--zone", required=True)
    p.add_argument("--step", required=True, choices=MOTION_CHAIN)
    p.add_argument("--bucket", default=BUCKET)
    p.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    p.add_argument("--block", type=int, default=16384)
    p.add_argument("--offline", action="store_true", help="never touch the bucket (tests)")
    p = sub.add_parser("mclean")
    p.add_argument("--data", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--stamps", required=True, help="the folder of the zone stamps m.<zone>")
    p = sub.add_parser("frames-check")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--top", type=int, default=FRAMES_TOP)
    p = sub.add_parser("refcat-status")
    p.add_argument("--data", required=True)
    p.add_argument("--zones", default="")
    p.add_argument("--zones-file", default=None)
    p.add_argument("--json", default=None)
    p.add_argument("--verbose", action="store_true")
    p = sub.add_parser("refcat")
    p.add_argument("--data", required=True)
    p.add_argument("--zone", required=True)
    p.add_argument("--workers", type=int, default=8, help="tiles asked for at once (refcat REFCAT_WORKERS)")
    p = sub.add_parser("upload-refcat")
    p.add_argument("--data", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--zones", default="")
    p.add_argument("--zones-file", default=None)
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("fill-todo")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--done", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "export":
        return cmd_export(a, extra)
    return {"plan": cmd_plan, "fetch": cmd_fetch, "estimate": cmd_estimate, "combine": cmd_combine,
            "report": cmd_report, "fill-todo": cmd_fill_todo, "frames-check": cmd_frames_check,
            "refcat-status": cmd_refcat_status, "mplan": cmd_mplan, "motion": cmd_motion, "mclean": cmd_mclean, "refcat": cmd_refcat, "upload-refcat": cmd_upload_refcat}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
