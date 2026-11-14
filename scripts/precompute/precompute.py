"""Static twins of the API for the featured targets: web/public/data/<slug>/.

Run from server/ (it imports skyshift_server and skyshift_core):

    cd server
    uv run python ../scripts/precompute/precompute.py --only barnard --sets months:15 --dets 2
    uv run python ../scripts/precompute/precompute.py                 # everything (slow, cached)

Files per target (shapes of contracts/api.md; data_url is relative to the JSON):

    frames.json                  the target's own clock and field (a copy of one file below)
    frames_<clock>_<fov>.json    one per clock and field, epochs of every detector built
    frames/<c><fov>_d<det>_v<visit>.bin | frames/<c><fov>_d<det>_<obs_id>.bin
    object.json                  GET /api/object
    index.json                   which frames files exist: [{file, clock, fov, dets, epochs, bytes}]

The cache is the engine's (SKYSHIFT_CACHE, default ~/.cache/skyshift), so a second run only
downloads what is missing.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from skyshift_core import GAIA_EPOCH_MJD, propagate
from skyshift_server import blink, cutouts
from skyshift_server.cache import Cache

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web" / "public" / "data"
TARGETS_JSON = ROOT / "scripts" / "validate" / "data" / "targets.json"

CENTER_MJD = 61045.0  # middle of the SPHEREx data so far (Apr 2025 - Oct 2026)
# Frame centres fixed by hand (deg). Others: the catalogue position moved to CENTER_MJD.
CENTERS = {"barnard": (269.44620, 4.76883)}  # Barnard's Star around 2026.3

DEFAULT_SETS = ["months:5", "months:15", "hours:20", "hours:60"]
OWN_SET = "months:5"  # frames.json for the stars
# Detectors per set: every detector for the months clock, fewer for the heavier hours clock.
SET_DETS = {"months:5": [1, 2, 3, 4, 5, 6], "months:15": [1, 2, 3, 4, 5, 6], "hours:20": [2, 5], "hours:60": [2]}

log = logging.getLogger("precompute")


def star_targets() -> list[dict]:
    out = []
    for t in json.loads(TARGETS_JSON.read_text())["targets"]:
        if t["slug"] in CENTERS:
            ra, dec = CENTERS[t["slug"]]
        else:
            r, d = propagate(t["ra"], t["dec"], t["pmra"], t["pmdec"], CENTER_MJD,
                             epoch_mjd=t.get("ref_mjd", GAIA_EPOCH_MJD))
            ra, dec = round(float(r), 5), round(float(d), 5)
        out.append({"slug": t["slug"], "name": t["name"], "ra": ra, "dec": dec})
    return out


def write_json(path: Path, obj, compact: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, allow_nan=False, **({"separators": (",", ":")} if compact else {"indent": 1}))
    path.write_text(text + "\n")
    return len(text) + 1


def clean(o):
    """NaN/inf -> None (contract: a missing value is null), numpy scalars -> Python."""
    import numpy as np

    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o


def build_set(t: dict, set_name: str, dets: list[int], cache: Cache) -> dict | None:
    """One frames_<clock>_<fov>.json with the epochs of every detector in `dets`."""
    clock, fov_s = set_name.split(":")
    fov = float(fov_s)
    folder = OUT / t["slug"]
    merged: dict | None = None
    n_bytes = 0
    built = []
    for det in dets:
        t0 = time.time()
        try:
            fr = blink.build_frames(t["ra"], t["dec"], fov, clock, det, cache=cache)
        except Exception as e:  # noqa: BLE001 - one detector must not stop the rest
            log.warning("%s %s d%d failed: %s", t["slug"], set_name, det, e)
            continue
        for name, data in fr.blobs.items():
            p = folder / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            n_bytes += len(data)
        if merged is None:
            merged = fr.meta
        else:
            merged["epochs"] += fr.meta["epochs"]
            merged["known"] += fr.meta["known"]
        built.append(det)
        log.info("%s %s d%d: %d epochs in %.1f s", t["slug"], set_name, det, len(fr.meta["epochs"]), time.time() - t0)
    if merged is None:
        return None
    merged["epochs"].sort(key=lambda e: (e["mjd_mid"], e["det"]))
    file = f"frames_{clock}_{int(fov)}.json"
    n_bytes += write_json(folder / file, clean(merged), compact=True)
    return {"file": file, "clock": clock, "fov": int(fov), "dets": built,
            "epochs": len(merged["epochs"]), "bytes": n_bytes}


def folder_bytes(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated slugs (default: all)")
    ap.add_argument("--sets", help=f"comma-separated clock:fov (default: {','.join(DEFAULT_SETS)})")
    ap.add_argument("--dets", help="comma-separated detectors (default: per set)")
    ap.add_argument("--own", default=OWN_SET, help="the set copied to frames.json")
    ap.add_argument("--no-object", action="store_true", help="skip object.json")
    ap.add_argument("--cache", help="cache folder or gs:// (default: SKYSHIFT_CACHE or ~/.cache/skyshift)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    cache = Cache(a.cache)
    only = set(a.only.split(",")) if a.only else None
    sets = a.sets.split(",") if a.sets else DEFAULT_SETS
    for t in star_targets():
        if only and t["slug"] not in only:
            continue
        t0 = time.time()
        folder = OUT / t["slug"]
        index_path = folder / "index.json"
        index = {"slug": t["slug"], "name": t["name"], "center": {"ra": t["ra"], "dec": t["dec"]}, "frames": []}
        if index_path.exists():
            index["frames"] = json.loads(index_path.read_text()).get("frames", [])
        for s in sets:
            dets = [int(d) for d in a.dets.split(",")] if a.dets else SET_DETS[s]
            entry = build_set(t, s, dets, cache)
            if entry:
                index["frames"] = [f for f in index["frames"] if f["file"] != entry["file"]] + [entry]
        own = f"frames_{a.own.replace(':', '_')}.json"
        if not (folder / own).exists() and index["frames"]:
            own = index["frames"][0]["file"]
        if (folder / own).exists():
            shutil.copyfile(folder / own, folder / "frames.json")
            index["own"] = own
        if not a.no_object:
            try:
                write_json(folder / "object.json", clean(blink.build_object(t["ra"], t["dec"], cache=cache)), compact=True)
            except Exception as e:  # noqa: BLE001
                log.warning("%s object failed: %s", t["slug"], e)
        index["frames"].sort(key=lambda f: f["file"])
        write_json(index_path, index)
        log.info("%s done in %.0f s: %.2f MB on disk; downloads so far: %s", t["slug"], time.time() - t0,
                 folder_bytes(folder) / 1e6, cutouts.STATS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
