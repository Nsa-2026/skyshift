"""skyshift-pipeline <step> --zone pilot|hunt [--root ROOT] [--workers N] [--limit N]

Steps: photolist, refcat, dots, known, link, spectra, crossid, calib, classify, photoz, colours, lightcurves,
astrometry, highpm, score, suspects, all (in that order).
ROOT is an fsspec root: gs://nasa-glazing-data (default) or a local folder.
Every step skips outputs that already exist, so a stopped run can simply be started again.
`all` on a zone larger than TILE_AUTO_MIN_DEG2 (config) runs link … suspects per sky tile in parallel processes
and merges them into the same results/<zone>/ files (tiles.py); --tiles off|N and --tile-deg D override that.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import config as C
from . import log
from .store import Store, results_rel

MOTION_STEPS = ("crossid", "calib", "colours", "lightcurves", "astrometry", "highpm")  # from stored dots only
STEPS = ("photolist", "refcat", "dots", "known", "link", "spectra", "crossid", "calib", "classify", "photoz",
         "colours", "lightcurves", "astrometry", "highpm", "score", "suspects")
TILED_STEPS = STEPS[STEPS.index("link"):]  # what `all` runs per tile (tiles.py) on a large zone
L = log.get("cli")


def _run_step(step: str, store: Store, zone: C.Zone, args: argparse.Namespace) -> dict:
    if step == "photolist":
        from . import photolist
        return photolist.run(store, zone, force=args.force)
    if step == "refcat":
        from . import refcat
        return refcat.run(store, zone, force=args.force)
    if step == "dots":
        from . import dots
        return dots.run(store, zone, workers=args.workers, limit=args.limit)
    if step == "known":
        from . import known
        return known.run(store, zone, limit=args.limit)
    if step == "link":
        from . import link
        return link.run(store, zone, workers=args.workers)
    if step == "spectra":
        from . import spectra
        return spectra.run(store, zone, workers=args.workers)
    if step in MOTION_STEPS:
        import importlib
        return importlib.import_module(f".{step}", __package__).run(store, zone, workers=args.workers)
    if step == "classify":
        from . import classify
        return classify.run(store, zone, workers=args.workers)
    if step == "photoz":
        from . import photoz
        return photoz.run(store, zone)
    if step == "score":
        from . import score
        return score.run(store, zone)
    if step == "suspects":
        from . import suspects
        return suspects.run(store, zone)
    raise ValueError(step)


def _update_summary(store: Store, zone: C.Zone, step: str, out: dict, seconds: float) -> None:
    rel = results_rel(zone.name, "summary.json")
    try:
        summary = store.read_json(rel) if store.exists(rel) else {}
    except Exception:  # noqa: BLE001
        summary = {}
    summary.setdefault("zone", {"name": zone.name, "ra": [zone.ra_min, zone.ra_max], "dec": [zone.dec_min, zone.dec_max]})
    summary.setdefault("steps", {})
    summary["steps"][step] = {**out, "step_seconds": round(seconds, 1), "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    store.write_json(rel, summary)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skyshift-pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=(*STEPS, "all"))
    ap.add_argument("--zone", required=True, choices=sorted(C.ZONES))
    ap.add_argument("--root", default="gs://nasa-glazing-data")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--limit", type=int, default=None, help="testing: dots does at most N photos, known at most N sessions")
    ap.add_argument("--force", action="store_true", help="photolist, refcat: query the services again even if the files exist")
    ap.add_argument("--known-source", choices=C.KNOWN_SOURCES, default=C.KNOWN_SOURCE,
                    help="known, score, suspects: MPC orbits with kete (default) or the SkyBoT service")
    ap.add_argument("--tiles", default="auto",
                    help="all: link … suspects per sky tile: auto (zones over config TILE_AUTO_MIN_DEG2 deg²), off, or about N tiles")
    ap.add_argument("--tile-deg", type=float, default=None, help="all: tile core side in degrees (tiles any zone)")
    ap.add_argument("--tile-dir", default=None, help="all, tiled: local work dir (default: the system temp dir); removed after")
    ap.add_argument("--keep-tiles", action="store_true", help="all, tiled: keep the work dir (per-tile tables)")
    args = ap.parse_args(argv)
    C.KNOWN_SOURCE = args.known_source
    os.environ["SKYSHIFT_KNOWN_SOURCE"] = args.known_source  # worker processes read config afresh
    log.setup()
    store = Store(args.root)
    zone = C.ZONES[args.zone]
    steps = STEPS if args.step == "all" else (args.step,)
    tile_deg = None
    if args.step == "all":
        from . import tiles
        tile_deg = tiles.choose(zone, args.tiles, args.tile_deg, args.workers)
    L.info("skyshift-pipeline %s --zone %s --root %s --workers %d --limit %s%s", args.step, zone.name, store.root, args.workers,
           args.limit, f" --tile-deg {tile_deg:.3g}" if tile_deg else "")
    for step in steps:
        if tile_deg and step in TILED_STEPS:
            if step == TILED_STEPS[0]:
                L.info("=== %s, tiled (%s) ===", " → ".join(TILED_STEPS), zone.name)
                out = tiles.run(store, zone, tile_deg, args.workers, work_dir=args.tile_dir, keep_work=args.keep_tiles)
                for k, v in out.items():
                    _update_summary(store, zone, k, v, v.pop("step_seconds", 0.0) if isinstance(v, dict) else 0.0)
            continue
        t0 = time.time()
        L.info("=== %s (%s) ===", step, zone.name)
        out = _run_step(step, store, zone, args)
        dt = time.time() - t0
        _update_summary(store, zone, step, out, dt)
        L.info("=== %s done in %.1f s ===", step, dt)
        if args.step == "all" and step == "calib" and store.exists(results_rel(zone.name, C.CALIB_REL)):
            # sources.parquet feeds classify and photoz: rebuild it from the calibrated dots
            t0 = time.time()
            L.info("=== spectra again, with calib (%s) ===", zone.name)
            out = _run_step("spectra", store, zone, args)
            _update_summary(store, zone, "spectra", out, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
