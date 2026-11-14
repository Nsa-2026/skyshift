"""Static twins of the API for the featured targets: web/public/data/<slug>/.

Run from server/ (it imports skyshift_server and skyshift_core):

    cd server
    uv run python ../scripts/precompute/precompute.py --only barnard --sets months:15 --dets 2
    uv run python ../scripts/precompute/precompute.py --only 3i-atlas,elpis,euphrosyne
    uv run python ../scripts/precompute/precompute.py                 # everything (slow, cached)
    uv run python ../scripts/precompute/precompute.py --sets none     # only object.json, every target (~2 min cached)

Targets: barnard, epsindib, wise0855, rwaur, rdra (scripts/validate/data/targets.json), then
comet 3i-atlas and the asteroids elpis (59) and euphrosyne (31), on the hours clock.

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
from collections import Counter
from pathlib import Path

from skyshift_core import GAIA_EPOCH_MJD, propagate
from skyshift_server import blink, cutouts, gaia, obj, search
from skyshift_server.cache import Cache
from skyshift_server.search import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web" / "public" / "data"
TARGETS_JSON = ROOT / "scripts" / "validate" / "data" / "targets.json"

CENTER_MJD = 61045.0  # middle of the SPHEREx data so far (Apr 2025 - Oct 2026)
# Frame centres fixed by hand (deg). Others: the catalogue position moved to CENTER_MJD.
CENTERS = {"barnard": (269.44620, 4.76883)}  # Barnard's Star around 2026.3

DEFAULT_SETS = ["months:5", "months:15", "hours:20", "hours:60"]
OWN_SET = "months:5"  # frames.json for the stars
# Detectors per set: every detector for the months clock, fewer for the heavier hours clock.
# The 15' months frames cost 3 MB of download per image from S3, so only Barnard's Star has all six.
SET_DETS = {"months:5": [1, 2, 3, 4, 5, 6], "months:15": [2, 5], "hours:20": [2, 5], "hours:60": [2]}
SET_DETS_BY_SLUG = {"barnard": {"months:15": [1, 2, 3, 4, 5, 6]}}

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
            fr = blink.build_frames(t["ra"], t["dec"], fov, clock, det, cache=cache, per_visit=None,
                                    hours_frames=None)
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


# --- moving objects: comet 3I/ATLAS and two asteroids ------------------------------------------
#
# Hours clock only. Positions per image come from NASA: IRSA's 3I/ATLAS table (already in
# scripts/validate/data/photolists/3i-atlas.csv, source "irsa-3i") and IRSA MOST for the asteroids
# (source "most"). The comet crosses 57' during its 19-hour sequence of 14 December 2025, so its frames
# FOLLOW it: every epoch has its own `center` (an addition to the contract) and the stars move.
# An asteroid moves a few arcmin in a day, so its frames keep one fixed centre and it moves.

COMET_CSV = ROOT / "scripts" / "validate" / "data" / "photolists" / "3i-atlas.csv"
COMET_MJD = (61023.0, 61024.1)  # the 14 Dec 2025 sequence (after perihelion), as in resolve.COMET
COMET_DETS = [1, 2, 3, 4, 5, 6]
# The two of MOST's asteroids 1-60 with the most images at V 11.5-14 (bright, not saturated):
# (59) Elpis 454 images at V 12.6-13.6, (31) Euphrosyne 436 at V 13.0-13.7 (MOST, 14 Nov 2026).
ASTEROIDS = {59: "elpis", 31: "euphrosyne"}
ASTEROID_V = (11.5, 14.0)
ASTEROID_DETS = [2, 5]
ASTEROID_WINDOW_D = 1.0
S3_ROOT = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/"


def _image(obs_id: str, det: int, t_min: float, t_max: float, access_url: str, s3_key: str,
           collection: str, visit: int = 0, session: int = 0) -> Image:
    return Image(collection, obs_id, det, t_min, t_max, (t_min + t_max) / 2.0, access_url,
                 S3_ROOT + s3_key if s3_key else None, visit, session)


def comet_rows() -> list[dict]:
    """Images of 3I/ATLAS with NASA's comet position per image."""
    import csv

    out = []
    for r in csv.DictReader(COMET_CSV.open()):
        if not r["s3_key"] and "/ibe/data/" not in r["access_url"]:
            continue  # one image is in neither DR1 nor QR3
        out.append({"image": _image(r["obs_id"], int(r["detector"]), float(r["t_min"]), float(r["t_max"]),
                                    r["access_url"], r["s3_key"], r["collection"], int(r["visit"]), int(r["session"])),
                    "ra": round(float(r["obj_ra"]), 5), "dec": round(float(r["obj_dec"]), 5), "v": None})
    return out


def most_rows(number: int) -> tuple[str, list[dict]]:
    """(name, images of asteroid `number` with MOST's predicted position), via scripts/validate."""
    sys.path.insert(0, str(ROOT / "scripts" / "validate"))
    import most_asteroids  # noqa: PLC0415 - P1's MOST client and parser (cached in scripts/validate/.cache)

    log_: list[str] = []
    text = most_asteroids.fetch(number, log_)
    if text is None:
        raise RuntimeError(f"MOST failed for asteroid {number}: {log_}")
    name, rows = most_asteroids.parse(number, text)
    out = []
    for r in rows:
        out.append({"image": _image(r["obs_id"], r["detector"], r["mjd_start"], r["mjd_end"], r["access_url"],
                                    r["s3_key"], "spherex_" + r["release"]),
                    "ra": round(r["ra_obj"], 5), "dec": round(r["dec_obj"], 5), "v": r["vmag"]})
    # Visit and session numbers by core's rule over the asteroid's own exposure times (contracts/api.md:
    # `visit` = visit number, 1-based), as search.number_visits does for a fixed position.
    search.number_visits(sorted((r["image"] for r in out), key=lambda im: im.mjd))
    return name, out


def densest_window(rows: list[dict], det: int, days: float) -> list[dict]:
    """The rows of one detector inside the `days`-long window that holds the most exposures."""
    rs = sorted((r for r in rows if r["image"].det == det), key=lambda r: r["image"].mjd)
    best: list[dict] = []
    for i, r in enumerate(rs):
        w = [q for q in rs[i:] if q["image"].mjd - r["image"].mjd <= days]
        if len(w) > len(best):
            best = w
    return best


def build_moving(slug: str, name: str, rows: list[dict], dets: list[int], source: str, cache: Cache,
                 *, follow: bool, fov: float = 20.0, center: tuple[float, float] | None = None,
                 spectrum_rows: list[dict] | None = None, object_only: bool = False) -> None:
    """frames (hours clock) + object.json for a solar-system object.
    rows: [{image, ra, dec, v}] already limited to the exposures to show.
    object_only: rebuild only object.json (--sets none)."""
    folder = OUT / slug
    t0 = time.time()
    if center is None:
        mid = sorted(rows, key=lambda r: r["image"].mjd)[len(rows) // 2]
        center = (mid["ra"], mid["dec"])
    if object_only:
        n = write_moving_object(folder, name, spectrum_rows if spectrum_rows is not None else rows, center, cache)
        log.info("%s object.json in %.0f s: %d spectrum points", slug, time.time() - t0, n)
        return
    epochs, known_, n_bytes, built = [], [], 0, []
    grid = None
    for det in dets:
        rs = sorted((r for r in rows if r["image"].det == det), key=lambda r: r["image"].mjd)
        if not rs:
            continue
        eps, blobs, grid = blink.exposure_epochs(
            [r["image"] for r in rs], center[0], center[1], fov, cache, f"h{int(fov)}",
            centers=[(r["ra"], r["dec"]) for r in rs] if follow else None)
        for fname, data in blobs.items():
            p = folder / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            n_bytes += len(data)
        by_id = {f"d{det}-{r['image'].obs_id}": r for r in rs}
        for e in eps:
            r = by_id[e["id"]]
            known_.append({"name": name, "epoch_id": e["id"], "ra": r["ra"], "dec": r["dec"], "v": r["v"],
                           "source": source})
        epochs += eps
        built.append(det)
        log.info("%s hours:%d d%d: %d epochs", slug, int(fov), det, len(eps))
    if not epochs or grid is None:
        log.warning("%s: no frames", slug)
        return
    epochs.sort(key=lambda e: (e["mjd_mid"], e["det"]))
    # Gaia: around the whole track
    import numpy as np
    from skyshift_core import tangent_offset_arcsec

    reach = max(float(np.hypot(*tangent_offset_arcsec(center[0], center[1], r["ra"], r["dec"]))) for r in rows) / 60.0
    try:
        stars = gaia.cone(center[0], center[1], round((reach if follow else 0.0) + fov / 2.0 * 2**0.5, 2), cache=cache)
    except Exception as e:  # noqa: BLE001
        log.warning("%s: Gaia failed: %s", slug, e)
        stars = []
    meta = blink.frames_meta("hours", grid, epochs, stars, known_, search.today())
    file = f"frames_hours_{int(fov)}.json"
    n_bytes += write_json(folder / file, clean(meta), compact=True)
    shutil.copyfile(folder / file, folder / "frames.json")

    n_points = write_moving_object(folder, name, spectrum_rows if spectrum_rows is not None else rows, center, cache)
    write_json(folder / "index.json", {
        "slug": slug, "name": name, "center": {"ra": center[0], "dec": center[1]}, "own": file,
        "follows_object": follow,
        "frames": [{"file": file, "clock": "hours", "fov": int(fov), "dets": built, "epochs": len(epochs), "bytes": n_bytes}]})
    log.info("%s done in %.0f s: %.2f MB on disk, %d spectrum points; downloads so far: %s", slug, time.time() - t0,
             folder_bytes(folder) / 1e6, n_points, cutouts.STATS)


def write_moving_object(folder: Path, name: str, srows: list[dict], center: tuple[float, float], cache: Cache) -> int:
    """object.json for a solar-system object: the spectrum at NASA's position in every image given
    (visit = the visit number of core's rule over the object's own exposure times); no
    visit-to-visit measure. Returns the number of spectrum points."""
    images = [r["image"] for r in srows]
    pos = [(r["ra"], r["dec"]) for r in srows]
    l2s = cutouts.fetch_many(images, center[0], center[1], cutouts.tier_for_field(5), cache, centers=pos, variance=True)
    dropped: Counter = Counter()
    spectrum = obj.spectrum_points(images, l2s, None, center[0], center[1], positions=pos, dropped=dropped)
    write_json(folder / "object.json", clean({"name": name, "gaia": None, "known_ucd": None, "spectrum": spectrum,
                                              "spectrum_reason": obj.spectrum_reason(spectrum, dropped),
                                              "measure": None}), compact=True)
    return len(spectrum)


def build_comet(cache: Cache, object_only: bool = False) -> None:
    rows = comet_rows()
    show = [r for r in rows if COMET_MJD[0] < r["image"].mjd < COMET_MJD[1]]
    build_moving("3i-atlas", "3I/ATLAS", show, COMET_DETS, "irsa-3i", cache, follow=True, spectrum_rows=rows,
                 object_only=object_only)


def build_asteroid(number: int, slug: str, cache: Cache, object_only: bool = False) -> None:
    import numpy as np

    name, rows = most_rows(number)
    label = "(%d) %s" % (number, name.split("(")[0].replace(str(number), "", 1).strip() or slug.title())
    bright = [r for r in rows if r["v"] is not None and ASTEROID_V[0] <= r["v"] <= ASTEROID_V[1]]
    win = densest_window(bright, ASTEROID_DETS[0], ASTEROID_WINDOW_D)
    if not win:
        log.warning("%s: no images", slug)
        return
    t_a, t_b = win[0]["image"].mjd, win[-1]["image"].mjd
    show = [r for r in rows if t_a - 1e-6 <= r["image"].mjd <= t_b + 1e-6]
    center = (round(float(np.median([r["ra"] for r in win])), 5), round(float(np.median([r["dec"] for r in win])), 5))
    build_moving(slug, label, show, ASTEROID_DETS, "most", cache, follow=False, center=center,
                 spectrum_rows=[r for r in rows if abs(r["image"].mjd - (t_a + t_b) / 2) <= 15.0],
                 object_only=object_only)


def refresh_known(slug: str, cache: Cache) -> None:
    """Only `known` again in every frames_<clock>_<fov>.json of a star target (MPC, server known.py),
    at each epoch's mjd_mid and centre; epochs, Gaia and frame files stay as they are. index.json
    `bytes` and frames.json (the copy of `own`) follow. A failure stops: nothing half-done is written."""
    from skyshift_server import known

    folder = OUT / slug
    index_path = folder / "index.json"
    index = json.loads(index_path.read_text())
    for entry in index["frames"]:
        path = folder / entry["file"]
        meta = json.loads(path.read_text())
        r = entry["fov"] / 2.0 * 2**0.5
        by_centre: dict[tuple[float, float], list[dict]] = {}
        for ep in meta["epochs"]:
            c = ep.get("center") or meta["center"]
            by_centre.setdefault((c["ra"], c["dec"]), []).append(ep)
        found: list[dict] = []
        for (ra, dec), eps in by_centre.items():
            found += known.near(ra, dec, r, [e["mjd_mid"] for e in eps], epoch_ids=[e["id"] for e in eps], cache=cache)
        before = len(meta["known"])
        meta["known"] = found
        old = path.stat().st_size
        entry["bytes"] += write_json(path, clean(meta), compact=True) - old
        log.info("%s %s: known %d -> %d", slug, entry["file"], before, len(found))
    if index.get("own"):
        shutil.copyfile(folder / index["own"], folder / "frames.json")
    write_json(index_path, index)


def folder_bytes(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated slugs (default: all)")
    ap.add_argument("--sets", help=f"comma-separated clock:fov, or 'none' to rebuild only object.json "
                                   f"(default: {','.join(DEFAULT_SETS)})")
    ap.add_argument("--dets", help="comma-separated detectors (default: per set)")
    ap.add_argument("--own", default=OWN_SET, help="the set copied to frames.json")
    ap.add_argument("--no-object", action="store_true", help="skip object.json")
    ap.add_argument("--known-only", action="store_true",
                    help="star targets: recompute only known[] of the existing frames files (MPC)")
    ap.add_argument("--cache", help="cache folder or gs:// (default: SKYSHIFT_CACHE or ~/.cache/skyshift)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    cache = Cache(a.cache)
    only = set(a.only.split(",")) if a.only else None
    sets = [] if a.sets == "none" else a.sets.split(",") if a.sets else DEFAULT_SETS
    for t in star_targets():
        if only and t["slug"] not in only:
            continue
        if a.known_only:
            refresh_known(t["slug"], cache)
            continue
        t0 = time.time()
        folder = OUT / t["slug"]
        index_path = folder / "index.json"
        index = {"slug": t["slug"], "name": t["name"], "center": {"ra": t["ra"], "dec": t["dec"]}, "frames": []}
        if index_path.exists():
            index["frames"] = json.loads(index_path.read_text()).get("frames", [])
        for s in sets:
            dets = ([int(d) for d in a.dets.split(",")] if a.dets
                    else SET_DETS_BY_SLUG.get(t["slug"], {}).get(s, SET_DETS[s]))
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
    if a.known_only:
        return 0  # moving targets: known[] is NASA's own position list (irsa-3i, most), not MPC
    if not only or "3i-atlas" in only:
        build_comet(cache, object_only=not sets)
    for number, slug in ASTEROIDS.items():
        if not only or slug in only:
            build_asteroid(number, slug, cache, object_only=not sets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
