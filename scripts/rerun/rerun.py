#!/usr/bin/env python3
"""The one combined photo rerun on the lead's PC: extraction v2 + session stacks + forced photometry (Gaia + red
layer), each photo streamed once from NASA's public S3 (free) into memory, never saved to disk.

Run from the repo root (it needs the pipeline's packages):
    uv run --project pipeline python scripts/rerun/rerun.py <command> [options]

Commands
    plan    dry run, writes nothing but rerun/plan.json: photos, download, sessions, extra reads caused by
            overlapping zones, CPU days, disk per output, free disk. Uses the benchmark's numbers when
            rerun-bench/bench.json exists, else the docs' estimates (marked "estimate").
    bench   run the benchmark sessions of scripts/rerun/bench_targets.json into <data>/rerun-bench/ (its own root:
            the full run doesn't mix with it), then report seconds per photo, RAM per worker, MB per photo for
            dots, peaks, forced, stack tables and maps, and the target list for the checks (<root>/bench.json).
    fetch   local copies of the bench photos (<data>/photo-cache/, ~65 MB each, free from NASA's S3); `bench
            --photo-cache <dir>` then reads them, so a rerun after a fix costs CPU only.
    run     the full run, zone by zone (`skyshift-pipeline dots` per zone, one subprocess each). Resumable: a photo
            whose dot file is a whole parquet file and a stack whose file is are skipped (a 0-byte or cut-short file
            left by a crash is redone: Store.complete_parquet; writes fsync before their rename, the same for `bench`
            and its photos). Before each zone it checks the free disk
            against the zone's estimate; while a zone runs, a watchdog stops it if the disk falls under --min-free-gb.

Data root: --data (default $SKYSHIFT_DATA or ~/skyshift-data). It must hold photolists/s3index/ (the photo index,
`skyshift-pipeline s3index`), photolists/zones/<zone>.parquet and refcat/sky/forced_n32/ + forced_red_n32/ (both
with _complete.json: forced photometry reads the sky lists; `python -m skyshift_pipeline.forced_sky [--red]`).
Switches set here for every process: SKYSHIFT_DOTS_V2=1 SKYSHIFT_SESSION_STACK=1 (docs/extraction-v2.md,
docs/session-stacks.md §8). Nothing is read from or written to gs:// by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("SKYSHIFT_DOTS_V2", "1")
os.environ.setdefault("SKYSHIFT_SESSION_STACK", "1")

import multiprocessing as mp  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_DATA = Path(os.environ.get("SKYSHIFT_DATA", Path.home() / "skyshift-data"))
TARGETS = HERE / "bench_targets.json"

# The docs' numbers, used by `plan` until a benchmark exists (docs/extraction-v2.md §3.4, §12.8; session-stacks.md §7).
ESTIMATE = {
    "worker_s_per_photo": 21.7,       # v2 dots 18.7 s per photo per core (Mac M5, extraction-v2 §3.5) + fused stack ~3 s
    "read_mb_per_photo": 65.7,        # the whole file (mean size_bytes of the index): every HDU is read
    "mb_per_photo": {"dots": 4.7, "peaks": 0.32, "forced": 1.2, "stack_tables": 2.5, "stack_maps": 4.0},
    "ram_gb_per_worker": 1.7,
}
SKY_PREFIXES = ("ecl",)               # the 332 ecliptic zones; + hunt = the 333 zones of the sky run
TEST_ZONE_PREFIXES = ("w_",)          # test-only zones (the wide sample's w_<name>, 27 Nov): never in "all" (their photos
#                                       are other zones' photos too: a whole-sky plan would count and read them twice);
#                                       named one by one they run as asked


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} rerun: {msg}", flush=True)


# --- the photo index ----------------------------------------------------------------------------------------------


def latest_versions(idx: pd.DataFrame) -> pd.DataFrame:
    """One row per (obs_id, detector): the highest l2b version (a reprocessed file is a separate index row); ties keep
    dr1 (config.COLLECTIONS order)."""
    v = idx["s3_key"].str.extract(r"l2b-v(\d+)-", expand=False).astype(float).fillna(-1)
    pref = (idx["collection"] != "spherex_dr1").astype(int) if "collection" in idx else 0
    out = idx.assign(_v=v, _p=pref).sort_values(["_v", "_p"], ascending=[False, True])
    return out.drop_duplicates(["obs_id", "detector"], keep="first").drop(columns=["_v", "_p"]).reset_index(drop=True)


def load_index(data: Path) -> pd.DataFrame:
    fs = sorted((data / "photolists" / "s3index").glob("*/*.parquet"))
    if not fs:
        raise SystemExit(f"no photo index in {data}/photolists/s3index/: run `skyshift-pipeline s3index --root {data}`")
    cols = ["obs_id", "detector", "collection", "s3_key", "size_bytes", "mjd"]
    return latest_versions(pd.concat([pd.read_parquet(f, columns=cols) for f in fs], ignore_index=True))


def sky_zone_names(data: Path, which: str) -> list[str]:
    """Zones in run order. "sky": hunt, then the ecliptic zones nearest the ecliptic first (row 0, ±1, …), column by
    column, so neighbouring zones (which share ~half their photos) follow each other. "all": every zone with a list
    except the test zones (TEST_ZONE_PREFIXES, e.g. the wide sample's w_*). Otherwise a comma list (test zones allowed
    by name)."""
    have = sorted(p.stem for p in (data / "photolists" / "zones").glob("*.parquet"))
    if which == "all":
        return [z for z in have if not z.startswith(TEST_ZONE_PREFIXES)]
    if which != "sky":
        names = [z.strip() for z in which.split(",") if z.strip()]
        missing = [z for z in names if z not in have]
        if missing:
            raise SystemExit(f"no photo list for {missing} in {data}/photolists/zones/")
        return names

    def row(z: str) -> int:
        s = z[5:]
        return 0 if not s else (int(s[1:]) if s[0] == "n" else -int(s[1:]))

    ecl = [z for z in have if z.startswith(SKY_PREFIXES)]
    ecl.sort(key=lambda z: (abs(row(z)), row(z) < 0, int(z[3:5]), z))
    return (["hunt"] if "hunt" in have else []) + ecl


def zone_photos(data: Path, zone: str) -> pd.DataFrame:
    return pd.read_parquet(data / "photolists" / "zones" / f"{zone}.parquet")


def zone_box(zone: str, target: dict | None = None):
    """The zone's RA/Dec box: config's zone, else the target's `box` [ra_min, ra_max, dec_min, dec_max] (the wide
    sample's zones w_<name>, wide_targets.json), else None (score_bench then counts the whole photo)."""
    from skyshift_pipeline import config as C

    if zone in C.ZONES:
        return C.ZONES[zone]
    box = (target or {}).get("box")
    return C.Zone(zone, *(float(x) for x in box)) if box else None


# --- plan: reads, overlap, time, disk -----------------------------------------------------------------------------


def simulate_reads(zone_lists: list[tuple[str, pd.DataFrame]], session_tasks) -> dict:
    """Photo reads of a zone-by-zone run with one shared dots folder: a zone reads its photos not yet done, plus the
    done photos that one of its NEW session-stack tasks needs (a session cut differently by two zones gets another
    key, so its photos are read again for that stack: dots.process_photos `measure=False`).
    Returns per-zone and total counts; session_tasks = coadd.session_tasks."""
    done: set = set()
    keys: set = set()
    per_zone = []
    for zone, ph in zone_lists:
        ids = list(zip(ph["obs_id"].astype(str), ph["detector"].astype(int)))
        new = {p for p in ids if p not in done}
        tasks = session_tasks(ph)
        pend = [t for t in tasks if t["key"] not in keys]
        need = {(str(o), int(t["detector"])) for t in pend for o in t["obs_ids"]}
        reread = need & done
        per_zone.append({"zone": zone, "photos": len(set(ids)), "new": len(new), "reread_for_stacks": len(reread),
                         "stack_tasks_new": len(pend)})
        done |= set(ids)
        keys |= {t["key"] for t in tasks}
    tot_new = sum(z["new"] for z in per_zone)
    tot_re = sum(z["reread_for_stacks"] for z in per_zone)
    return {"unique_photos": tot_new, "rereads": tot_re, "reads": tot_new + tot_re,
            "reread_share": round(tot_re / max(1, tot_new), 4), "stack_tasks": len(keys), "zones": per_zone}


def parallel_workers(workers: int, cores: int, smt_gain: float) -> float:
    """Workers' worth of single-core speed: each up to the physical cores, then SMT threads add smt_gain each (the
    benchmark's worker_s_per_photo is measured with at most one worker per core)."""
    return float(min(workers, cores) + max(0, workers - cores) * smt_gain)


def free_gb(path: Path) -> float:
    p = path
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def numbers(data: Path) -> tuple[dict, str]:
    """The benchmark's per-photo numbers if rerun-bench/bench.json exists, else ESTIMATE."""
    b = data / "rerun-bench" / "bench.json"
    if b.exists():
        r = json.loads(b.read_text())
        if r.get("per_photo"):
            return r["per_photo"], f"benchmark {r.get('finished', '')}"
    return ESTIMATE, "estimate (docs; no benchmark yet)"


def cmd_plan(a) -> dict:
    from skyshift_pipeline import coadd

    data = Path(a.data)
    idx = load_index(data)
    zones = sky_zone_names(data, a.zones)
    lists = [(z, zone_photos(data, z)) for z in zones]
    sim = simulate_reads(lists, coadd.session_tasks)
    in_zones = pd.concat([ph[["obs_id", "detector"]] for _, ph in lists]).drop_duplicates()
    sel = idx.merge(in_zones, on=["obs_id", "detector"])
    num, src = numbers(data)
    mb = num["mb_per_photo"]
    maps_mb = mb.get("stack_maps", 0.0) if a.maps else 0.0
    out_mb = sum(v for k, v in mb.items() if k != "stack_maps") + maps_mb
    from skyshift_pipeline import config as C

    folder = C.zone_dots(None)[0]
    from skyshift_pipeline.store import Store

    done = len(Store(str(a.root)).complete_parquets(folder))  # 0-byte / cut-short files (a crash) are redone
    todo = max(0, sim["unique_photos"] - done)
    cpu = num["worker_s_per_photo"]
    eff = parallel_workers(a.workers, a.cores, a.smt_gain)
    days_cpu = (todo + sim["rereads"]) * cpu / eff / 86400
    read_mb = float(sel["size_bytes"].mean()) / 1e6 if len(sel) else num["read_mb_per_photo"]
    read_tb = (todo + sim["rereads"]) * read_mb / 1e6
    days_net = read_tb * 1e12 / (a.net_mbps * 1e6) / 86400
    disk_tb = todo * out_mb / 1e6
    plan = {
        "made": time.strftime("%Y-%m-%dT%H:%M:%S"), "numbers_from": src, "zones": len(zones),
        "index_photos": len(idx), "index_tb": round(idx["size_bytes"].sum() / 1e12, 1),
        "index_last_mjd": round(float(idx["mjd"].max()), 2),
        "photos_in_zones": int(len(sel)), "zone_rows": int(sum(len(ph) for _, ph in lists)),
        "already_done_locally": done, "photos_to_do": todo,
        "rereads_for_stacks": sim["rereads"], "reread_share": sim["reread_share"], "stack_tasks": sim["stack_tasks"],
        "download_tb": round(read_tb, 1), "days_network": round(days_net, 1),
        "workers": a.workers, "worker_s_per_photo": round(cpu, 1), "parallel_workers": round(eff, 1),
        "days_cpu": round(days_cpu, 1),
        "days": round(max(days_cpu, days_net), 1),
        "mb_per_photo": {**mb, "stack_maps": maps_mb}, "maps": bool(a.maps),
        "disk_tb_needed": round(disk_tb, 2), "disk_free_tb": round(free_gb(Path(a.root)) / 1e3, 2),
        "ram_gb": round(a.workers * num.get("ram_gb_per_worker", ESTIMATE["ram_gb_per_worker"]), 1),
        "per_zone": sim["zones"],
    }
    plan["fits"] = plan["disk_tb_needed"] <= plan["disk_free_tb"] - a.min_free_gb / 1e3
    (data / "rerun").mkdir(parents=True, exist_ok=True)
    (data / "rerun" / "plan.json").write_text(json.dumps(plan, indent=1))
    show = {k: v for k, v in plan.items() if k != "per_zone"}
    for k, v in show.items():
        print(f"  {k:24s} {v}")
    print(f"  (per zone: {data}/rerun/plan.json)")
    return plan


# --- bench --------------------------------------------------------------------------------------------------------


def bench_photos(data: Path, targets: list[dict]) -> list[tuple[str, pd.DataFrame, dict]]:
    """(zone, rows, target) per target: every detector of the target's whole sessions in its zone. A target names
    `sessions` (zone session numbers) or `obs` (observation-number prefixes like 2025W34_1B_0173: their sessions)."""
    out = []
    for t in targets:
        ph = zone_photos(data, t["zone"])
        if t.get("obs"):
            pre = ph["obs_id"].astype(str).str.rsplit("_", n=1).str[0]
            sess = set(ph.loc[pre.isin(t["obs"]), "session"].astype(int))
        elif t.get("pick") == "largest":
            sess = {int(ph.groupby("session").size().idxmax())}
        else:
            sess = {int(s) for s in t.get("sessions", [])}
        rows = ph[ph["session"].astype(int).isin(sess)].sort_values(["mjd", "detector"]).reset_index(drop=True)
        if rows.empty:
            log(f"bench: target {t.get('why', t)} matched no photos in {t['zone']}")
            continue
        out.append((t["zone"], rows, t))
    return out


def _child_rss() -> dict[int, int]:
    """RSS bytes of this process's children (the pool's workers), from /proc."""
    me, out = os.getpid(), {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            if ppid != me:
                continue
            with open(f"/proc/{d}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        out[int(d)] = int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            continue
    return out


class RamWatch(threading.Thread):
    def __init__(self, every: float = 2.0):
        super().__init__(daemon=True)
        self.every, self.stop = every, threading.Event()
        self.max_child, self.max_total, self.samples = 0, 0, []

    def run(self) -> None:
        while not self.stop.is_set():
            r = _child_rss()
            if r:
                self.max_child = max(self.max_child, max(r.values()))
                self.max_total = max(self.max_total, sum(r.values()))
                self.samples.append(sum(r.values()) / max(1, len(r)))
            self.stop.wait(self.every)


def _worker_init(map_zones: tuple) -> None:
    from skyshift_pipeline import config as C
    from skyshift_pipeline import log as plog

    plog.setup()
    C.SESSION_STACK_MAP_ZONES = tuple(map_zones)  # bench: maps for every bench zone, to measure their size


def bench_root(data: Path, root: Path) -> None:
    """The bench root: its own output folders, the data root's photo lists and sky forced lists linked in."""
    root.mkdir(parents=True, exist_ok=True)
    for rel in ("photolists", "refcat", "stamplists", "knownlists"):  # stamplists, knownlists: the lists, when built
        link = root / rel
        optional = rel in ("stamplists", "knownlists")
        if not link.exists() and not link.is_symlink() and (not optional or (data / rel).exists()):
            link.symlink_to(data / rel)


def cached_url(r, cache: Path | None) -> str:
    """The photo's local copy in the photo cache when it is there (fetch), else its S3 URL."""
    if cache is not None:
        p = cache / Path(str(r.s3_key)).name
        if p.exists():
            return str(p)
    return str(r.url)


def _fetch_one(url: str, dest: Path, size: int) -> tuple[str, int]:
    import requests

    if dest.exists() and (not size or dest.stat().st_size == size):
        return "have", 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, 4):
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 22):
                        f.write(chunk)
            if size and tmp.stat().st_size != size:
                raise OSError(f"size {tmp.stat().st_size} != {size}")
            tmp.replace(dest)
            return "got", dest.stat().st_size
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                return f"failed: {e}"[:200], 0
            time.sleep(5 * attempt)
    return "failed", 0


def cmd_fetch(a) -> None:
    """Local copies of the bench photos (from NASA's S3, free) so bench reruns don't wait on the network."""
    from concurrent.futures import ThreadPoolExecutor

    data, cache = Path(a.data), Path(a.photo_cache)
    cache.mkdir(parents=True, exist_ok=True)
    sel = bench_photos(data, json.loads(Path(a.targets).read_text())["targets"])
    rows = pd.concat([r for _, r, _ in sel]).drop_duplicates(["obs_id", "detector"])
    need = [(str(r.url), cache / Path(str(r.s3_key)).name, int(r.size_bytes or 0)) for r in rows.itertuples()]
    gb = sum(n for _, d, n in need if not d.exists()) / 1e9
    log(f"fetch: {len(need)} photos, {gb:.1f} GB to get, free {free_gb(cache):.0f} GB -> {cache}")
    if gb * 1.1 > free_gb(cache) - a.min_free_gb:
        raise SystemExit("not enough free disk")
    t0, got, bad = time.time(), 0, []
    with ThreadPoolExecutor(max_workers=a.threads) as ex:
        for k, (st, n) in enumerate(ex.map(lambda x: _fetch_one(*x), need), 1):
            got += n
            if st.startswith("failed"):
                bad.append(st)
            if k % 20 == 0 or k == len(need):
                log(f"fetch: {k}/{len(need)}, {got / 1e9:.1f} GB in {time.time() - t0:.0f} s "
                    f"({got / max(1e-9, time.time() - t0) / 1e6:.1f} MB/s), failed {len(bad)}")
    if bad:
        log(f"fetch: {len(bad)} failed, e.g. {bad[:3]}; run again to retry")


def cmd_bench(a) -> dict:
    from skyshift_pipeline import coadd, dots
    from skyshift_pipeline import config as C

    data, root = Path(a.data), Path(a.root)
    need = [C.FORCED_SKY_DIR] + ([] if os.environ.get("SKYSHIFT_FORCED_SKY_RED") == "off" else [C.FORCED_SKY_RED_DIR])
    for d in need if not a.dry_run else []:  # SKYSHIFT_FORCED_SKY_RED=off: a smoke test without the red layer
        if not (data / d / "_complete.json").exists():
            raise SystemExit(f"{data}/{d}/_complete.json missing: build it first (forced_sky{' --red' if 'red' in d else ''})")
    bench_root(data, root)
    targets = json.loads(Path(a.targets).read_text())["targets"]
    sel = bench_photos(data, targets)
    zones = tuple(sorted({z for z, _, _ in sel}))
    jobs = []  # (zone, rows of one session, its stack tasks)
    for zone, rows, t in sel:
        tasks = coadd.session_tasks(rows)
        for s, g in rows.groupby("session", sort=False):
            jobs.append((zone, g, [x for x in tasks if int(x["session"]) == int(s)], t.get("why", "")))
    n_photos = sum(len(g) for _, g, _, _ in jobs)
    log(f"bench: {len(sel)} targets, {len(jobs)} sessions, {n_photos} photos, {a.workers} workers -> {root}")
    if a.dry_run:
        return {"sessions": len(jobs), "photos": n_photos}
    folder = C.zone_dots(None)[0]
    ram = RamWatch()
    ram.start()
    t0 = time.time()
    stats: list[dict] = []
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=a.workers, mp_context=ctx, initializer=_worker_init,
                             initargs=(zones,)) as ex:
        cache = Path(a.photo_cache) if a.photo_cache else None
        futs = {ex.submit(dots.process_photos, str(root),
                          [(r.obs_id, int(r.detector), cached_url(r, cache), int(r.session), int(r.visit))
                           for r in g.itertuples()],
                          zone, None, tasks): (zone, why) for zone, g, tasks, why in jobs}
        for f in as_completed(futs):
            zone, why = futs[f]
            for st in f.result():
                stats.append({**st, "zone": zone, "why": why})
            done = sum(1 for s in stats if not s.get("stack"))
            log(f"bench: {done}/{n_photos} photos, {time.time() - t0:.0f} s")
    wall = time.time() - t0
    ram.stop.set()
    size_of = {(str(r.obs_id), int(r.detector)): int(r.size_bytes) for _, rows, _ in sel for r in rows.itertuples()}
    runs = root / "bench_runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    ph = pd.DataFrame([{**s, "size_bytes": size_of.get((str(s["obs_id"]), int(s["detector"])))}
                       for s in stats if not s.get("stack")])
    stk = pd.DataFrame([s for s in stats if s.get("stack")])
    if len(ph):
        ph.astype({"error": str}).to_parquet(runs / f"{stamp}_photos.parquet")
    if len(stk):
        stk.drop(columns=[c for c in stk if stk[c].map(lambda v: isinstance(v, (dict, list))).any()]) \
           .to_parquet(runs / f"{stamp}_stacks.parquet")
    (runs / f"{stamp}_run.json").write_text(json.dumps({
        "workers": a.workers, "photo_cache": bool(a.photo_cache), "wall_s": round(wall, 1), "photos": int(len(ph)),
        "photos_done_now": int(ph["status"].isin(["done", "done_finast"]).sum()) if len(ph) else 0,
        "ram_gb_max_worker": round(ram.max_child / 1e9, 2), "ram_gb_max_total": round(ram.max_total / 1e9, 1),
        "targets": [{"zone": z, "why": t.get("why", ""), "photos": int(len(r))} for z, r, t in sel]}, indent=1))
    rep = bench_report(data, root, folder)
    log(f"bench: {json.dumps({k: v for k, v in rep.items() if k not in ('by_detector', 'runs')}, default=str)}")
    return rep


def file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def bench_report(data: Path, root: Path, folder: str) -> dict:
    """<root>/bench.json from every bench run so far (<root>/bench_runs/): per photo measured in a run (its last
    'done' row), the time a worker spent on it, and the exact bytes of its own files; stacks by their keys."""
    from skyshift_pipeline import config as C

    runs = root / "bench_runs"
    ph = pd.concat([pd.read_parquet(f) for f in sorted(runs.glob("*_photos.parquet"))], ignore_index=True)
    ok = ph[ph["status"].isin(["done", "done_finast"])].drop_duplicates(["obs_id", "detector"], keep="last")
    sk_files = sorted(runs.glob("*_stacks.parquet"))
    stk = pd.concat([pd.read_parquet(f) for f in sk_files], ignore_index=True) if sk_files else pd.DataFrame()
    sk_ok = stk[stk["status"] == "done"].drop_duplicates("key", keep="last") if len(stk) else stk
    meta = [json.loads(f.read_text()) for f in sorted(runs.glob("*_run.json"))]
    n = max(1, len(ok))
    per = pd.DataFrame({
        "dots": [file_size(root / folder / f"{o}D{d}.parquet") for o, d in zip(ok["obs_id"], ok["detector"])],
        "peaks": [file_size(root / f"{folder}_peaks" / f"{o}D{d}.parquet") for o, d in zip(ok["obs_id"], ok["detector"])],
        "forced": [file_size(root / C.FORCED_DIR / f"{o}D{d}.parquet") for o, d in zip(ok["obs_id"], ok["detector"])],
        **{side: [file_size(root / f"{folder}_{side}" / f"{o}D{d}.parquet") for o, d in zip(ok["obs_id"], ok["detector"])]
           for side in ("ext", "streaks", "cond", "stamps")},  # ext, streak (18 Nov), conditions and stamp files (27 Nov)
        "detector": ok["detector"].astype(int).values})
    sd, md = root / C.SESSION_STACK_DIR, root / C.SESSION_STACK_MAP_DIR
    keys = list(sk_ok["key"]) if len(sk_ok) else []
    stack_tab = {s: sum(file_size(sd / f"{k}{s}.parquet") for k in keys) for s in ("", "_photos", "_moving")}
    maps = sum(file_size(md / f"{k}.npz") for k in keys)
    mb = {k: round(float(per[k].sum()) / n / 1e6, 3) for k in ("dots", "peaks", "forced", "ext", "streaks", "cond", "stamps")}
    mb["stack_tables"] = round(sum(stack_tab.values()) / n / 1e6, 3)
    mb["stack_maps"] = round(maps / n / 1e6, 3)
    # A worker's time per photo: waiting for its read (prefetch hides most of it), measuring, writing, and its share
    # of the session stack. A bench has about one session per worker, so wall / photos would mostly show the
    # longest session, not the throughput.
    parts = [k for k in ("wait_s", "detect_s", "forced_s", "write_s", "stack_wall_s") if k in ok]
    worker_s = float(ok[parts].fillna(0).sum(axis=1).mean()) if len(ok) else float("nan")
    read_mb = float(ok["size_bytes"].mean()) / 1e6 if ok["size_bytes"].notna().any() else ESTIMATE["read_mb_per_photo"]
    rep = {
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "photos_ok": int(len(ok)),
        "failed": int((ph.drop_duplicates(["obs_id", "detector"], keep="last")["status"] == "failed").sum()),
        "stacks_done": int(len(sk_ok)), "stacks_not_done": int((stk["status"] != "done").sum()) if len(stk) else 0,
        "per_photo": {
            "worker_s_per_photo": round(worker_s, 2), "read_mb_per_photo": round(read_mb, 1),
            "mb_per_photo": mb, "ram_gb_per_worker": max((m["ram_gb_max_worker"] for m in meta), default=None),
        },
        "worker_s_split": {k: round(float(ok[k].fillna(0).mean()), 2) for k in parts},
        "medians_s": {k: round(float(ok[k].median()), 2) for k in ("read_s", "wait_s", "detect_s", "forced_s",
                                                                 "write_s") if k in ok},
        "dots_per_photo_median": float(ok["n_dots"].median()),
        "forced_per_photo_median": float(ok["n_forced"].median()),
        "forced_replaced": int(ok["forced_replaced"].fillna(False).astype(bool).sum()) if "forced_replaced" in ok else None,
        "stack_mb_split": {("sources" if s == "" else s[1:]): round(v / n / 1e6, 3) for s, v in stack_tab.items()},
        "map_mb_per_session_detector": round(maps / max(1, len(keys)) / 1e6, 2),
        "by_detector": (ok.assign(kb_dots=per["dots"].values / 1e3)
                        .groupby("detector").agg(n=("n_dots", "size"), dots=("n_dots", "median"),
                                                 detect_s=("detect_s", "median"), kb_dots=("kb_dots", "median"))
                        .round(1).reset_index().to_dict("records")),
        "runs": meta,
    }
    (root / "bench.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep


# --- run ------------------------------------------------------------------------------------------------------------


def cmd_run(a) -> None:
    data, root = Path(a.data), Path(a.root)
    plan_p = data / "rerun" / "plan.json"
    if not plan_p.exists():
        raise SystemExit("run `plan` first (it sizes every zone)")
    plan = json.loads(plan_p.read_text())
    out_mb = sum(plan["mb_per_photo"].values())
    zones = sky_zone_names(data, a.zones)
    per_zone = {z["zone"]: z for z in plan["per_zone"]}
    logs = data / "rerun" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SKYSHIFT_DOTS_V2": "1", "SKYSHIFT_SESSION_STACK": "1"}
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    for zone in zones:
        marker = data / "rerun" / "zones" / f"{zone}.done.json"
        if marker.exists():
            continue
        need_gb = per_zone.get(zone, {}).get("new", 0) * out_mb / 1e3
        free = free_gb(root)
        if free - need_gb * 1.2 < a.min_free_gb:
            log(f"STOP before {zone}: free {free:.0f} GB, zone needs ~{need_gb:.0f} GB, floor {a.min_free_gb} GB")
            return
        log(f"{zone}: ~{per_zone.get(zone, {}).get('new', '?')} new photos, ~{need_gb:.0f} GB, free {free:.0f} GB")
        cmd = [uv, "run", "--project", str(REPO / "pipeline"), "skyshift-pipeline", "dots", "--zone", zone,
               "--root", str(root), "--workers", str(a.workers)]
        with open(logs / f"{zone}.log", "a") as lf:
            p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            while p.poll() is None:
                time.sleep(30)
                if free_gb(root) < a.min_free_gb:
                    log(f"STOP in {zone}: free disk under {a.min_free_gb} GB; rerun `run` to resume after making room")
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait()
                    return
        if p.returncode != 0:
            log(f"{zone} exited {p.returncode}: see {logs}/{zone}.log; run again to resume")
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"zone": zone, "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                      "free_gb_after": round(free_gb(root), 1)}))
    log("all zones done")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "bench", "run", "fetch"):
        s = sub.add_parser(name)
        s.add_argument("--data", default=str(DEFAULT_DATA))
        s.add_argument("--root", default=None, help="output root (plan/run: the data root; bench: <data>/rerun-bench)")
        s.add_argument("--workers", type=int, default=20)
        s.add_argument("--min-free-gb", type=float, default=100.0)
        if name in ("plan", "run"):
            s.add_argument("--zones", default="sky", help="sky (333 zones), all, or a comma list")
        if name == "plan":
            s.add_argument("--maps", action="store_true", help="count session maps for every zone")
            s.add_argument("--cores", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="physical cores")
            s.add_argument("--smt-gain", type=float, default=0.25, help="a second thread on a core adds this much")
            s.add_argument("--net-mbps", type=float, default=300.0, help="sustained download, MB/s")
        if name in ("bench", "fetch"):
            s.add_argument("--photo-cache", default=None if name == "bench" else str(DEFAULT_DATA / "photo-cache"),
                           help="folder of local photo copies (fetch writes them; bench reads them when present)")
        if name == "fetch":
            s.add_argument("--targets", default=str(TARGETS))
            s.add_argument("--threads", type=int, default=4)
        if name == "bench":
            s.add_argument("--targets", default=str(TARGETS))
            s.add_argument("--dry-run", action="store_true")
            s.add_argument("--slim", action="store_true",
                           help="lossless slim parquet writers (env SKYSHIFT_PARQUET_SLIM=1, config PARQUET_SLIM; off by default)")
    a = ap.parse_args(argv)
    if getattr(a, "slim", False):  # the spawned workers inherit it; set before any of them starts
        os.environ["SKYSHIFT_PARQUET_SLIM"] = "1"
        from skyshift_pipeline import config as _C
        _C.PARQUET_SLIM = True
    if a.root is None:
        a.root = str(Path(a.data) / "rerun-bench") if a.cmd == "bench" else a.data
    {"plan": cmd_plan, "bench": cmd_bench, "run": cmd_run, "fetch": cmd_fetch}[a.cmd](a)


if __name__ == "__main__":
    main()
