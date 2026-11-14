"""skyshift-pipeline <step> --zone pilot|hunt [--root ROOT] [--workers N] [--limit N]

Steps: photolist, refcat, dots, known, link, spectra, crossid, calib, classify, photoz, colours, lightcurves,
astrometry, highpm, orphans, score, suspects, all (in that order).
ROOT is an fsspec root: gs://nasa-glazing-data (default) or a local folder.
Every step skips outputs that already exist, so a stopped run can simply be started again.
`all` on a zone larger than TILE_AUTO_MIN_DEG2 (config) runs link … suspects per sky tile in parallel processes
and merges them into the same results/<zone>/ files (tiles.py); --tiles off|N and --tile-deg D override that.

skyshift-pipeline mpc-orbits [--root ROOT] [--force]
  Laptop only, not part of `all`: new dated MPC orbit snapshots in cache/mpc/orbits/ (the GCP machines
  can't reach minorplanetcenter.net). Keeps a snapshot already made today unless --force.
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
         "colours", "lightcurves", "astrometry", "highpm", "orphans", "score", "suspects")
TILED_STEPS = STEPS[STEPS.index("link"):]  # what `all` runs per tile (tiles.py) on a large zone
TOOLS = ("mpc-orbits",)  # run by hand, never by `all`; no zone
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
    if step == "orphans":
        from . import orphans
        return orphans.run(store, zone, workers=args.workers)
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
    ap.add_argument("step", choices=(*STEPS, "all", *TOOLS))
    ap.add_argument("--zone", choices=sorted(C.ZONES), help="required for every step except mpc-orbits")
    ap.add_argument("--root", default="gs://nasa-glazing-data")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--limit", type=int, default=None, help="testing: dots does at most N photos, known at most N sessions")
    ap.add_argument("--force", action="store_true",
                    help="photolist, refcat, mpc-orbits: query the services again even if the files exist")
    ap.add_argument("--from", dest="from_step", choices=STEPS, default=None,
                    help="all: start at this step (e.g. --from link: combine again without photolist, refcat, dots, known)")
    ap.add_argument("--tiles", default=os.environ.get("SKYSHIFT_TILES", "auto"),
                    help="all: link … suspects per sky tile: auto (zones over config TILE_AUTO_MIN_DEG2 deg²), off, or about N "
                         "tiles (default: env SKYSHIFT_TILES, else auto)")
    ap.add_argument("--tile-deg", type=float, default=None, help="all: tile core side in degrees (tiles any zone)")
    ap.add_argument("--tile-dir", default=os.environ.get("SKYSHIFT_TILE_DIR") or None,
                    help="all, tiled: local work dir (default: env SKYSHIFT_TILE_DIR, else the system temp dir); removed after")
    ap.add_argument("--keep-tiles", action="store_true", help="all, tiled: keep the work dir (per-tile tables)")
    ap.add_argument("--zone-edge", choices=("border", "clip"), default="border" if C.TILE_ZONE_EDGE_MARGIN else "clip",
                    help="all, tiled: load the 2′ border outside the zone box too (default), or clip there as untiled runs do")
    args = ap.parse_args(argv)
    if args.step not in TOOLS and args.zone is None:
        ap.error(f"{args.step} needs --zone")
    log.setup()
    store = Store(args.root)
    if args.step == "mpc-orbits":
        from . import known_mpc
        L.info("skyshift-pipeline mpc-orbits --root %s --force %s", store.root, args.force)
        L.info("mpc-orbits: %s", known_mpc.refresh_orbits(store, force=args.force))
        return 0
    zone = C.ZONES[args.zone]
    steps = (STEPS[STEPS.index(args.from_step):] if args.from_step else STEPS) if args.step == "all" else (args.step,)
    grid = None
    if args.step == "all":
        from . import tiles
        grid = tiles.choose(zone, args.tiles, args.tile_deg, args.workers)
        if grid and TILED_STEPS[0] not in steps:  # tiles run the whole chain from link; later starts run untiled
            L.info("all --from %s: after link, so untiled", args.from_step)
            grid = None
    L.info("skyshift-pipeline %s --zone %s --root %s --workers %d --limit %s%s", args.step, zone.name, store.root, args.workers,
           args.limit, f", tiled {grid[0]} × {grid[1]}" if grid else "")
    for step in steps:
        if grid and step in TILED_STEPS:
            if step == TILED_STEPS[0]:
                L.info("=== %s, tiled (%s) ===", " → ".join(TILED_STEPS), zone.name)
                out = tiles.run(store, zone, grid, args.workers, work_dir=args.tile_dir, keep_work=args.keep_tiles,
                                edge_margin=args.zone_edge == "border")
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
