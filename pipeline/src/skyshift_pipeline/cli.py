"""skyshift-pipeline <step> --zone pilot|hunt [--root ROOT] [--workers N] [--limit N]

Steps: photolist, refcat, dots, known, link, spectra, crossid, calib, qa, classify, photoz, colours, lightcurves,
fragments, astrometry, highpm, orphans, tracklets, score, completeness, suspects, all (in that order).
ROOT is an fsspec root: gs://nasa-glazing-data (default) or a local folder.
Every step skips outputs that already exist, so a stopped run can simply be started again.
Dot files: dots/, or for the zones of config DOTS_FINE_ZONES (finer deblending) their own folder (config.zone_dots);
dots and link use the zone's folder, later steps the one link recorded in results/<zone>/summary.json.
`all` on a zone larger than TILE_AUTO_MIN_DEG2 (config) runs link … suspects per sky tile in parallel processes
and merges them into the same results/<zone>/ files (tiles.py); --tiles off|N and --tile-deg D override that.
completeness (synthetic movers in a 1° sub-area, inject.py) always runs once per zone, after the tiles; env
SKYSHIFT_COMPLETENESS=off skips it.

skyshift-pipeline mpc-orbits [--root ROOT] [--force]
  Laptop only, not part of `all`: new dated MPC orbit snapshots in cache/mpc/orbits/ (the GCP machines
  can't reach minorplanetcenter.net). Keeps a snapshot already made today unless --force.

skyshift-pipeline s3index [--root ROOT] [--collections dr1,qr3] [--workers 64] [--procs N] [--periods P1,P2*] [--force]
  Not part of `all`, no zone: the photo index from NASA's S3 copy (s3index.py), for when IRSA TAP is down. Lists every
  Level 2 file, reads its headers (--workers concurrent reads over --procs processes, default one per CPU) and writes
  photolists/s3index/<dr1|qr3>/<period>.parquet + .json, then _complete.json. Resumable: done periods are skipped
  (their failed files are tried again) unless --force. photolist falls back to it when TAP fails.

skyshift-pipeline sessionstack --zone ZONE [--root ROOT] [--workers N] [--limit N]
  Session stacks (coadd.py; docs/session-stacks.md): each session's photos of one detector added on one grid, searched
  with the ePSF-matched filter, every stacked source measured in each photo → session_stacks/<key>*.parquet (+ maps for
  config SESSION_STACK_MAP_ZONES), session_stacks/index/<zone>.parquet, results/<zone>/session_stack.json. `all` runs it
  right after dots only when SKYSHIFT_SESSION_STACK=1 (config SESSION_STACK), which also makes link use its rows.
  --limit N: the first N session × detector tasks. At most SESSION_STACK_WORKERS_MAX processes. With SKYSHIFT_DOTS_V2=1
  too, `dots` builds the stacks while it holds the photos (fused, each photo read once); this step then only does the
  ones the dots workers could not (an unreadable photo, --limit) and rewrites the index.

skyshift-pipeline stack --zone ZONE [--root ROOT] [--workers N] [--cache DIR] [--limit N]
  Not part of `all`: detect on per-visit stacks of the zone's photos, then a joint ePSF fit of every photo at all
  those sources (stack.py) → results/<zone>/stack_sources.parquet, stack_forced.parquet, stack.json. Reads the photos'
  pixels over the zone once into --cache (kept: a rerun reads nothing); --limit N: N photos per detector × visit.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import config as C
from . import log
from .store import Store, dots_dir, linked_dots_dir, results_rel, use_dots_dir

MOTION_STEPS = ("crossid", "calib", "colours", "lightcurves", "fragments", "astrometry", "highpm")  # from stored dots only
STEPS = ("photolist", "refcat", "dots", "known", "link", "spectra", "crossid", "calib", "qa", "classify", "photoz",
         "colours", "lightcurves", "fragments", "astrometry", "highpm", "orphans", "tracklets", "score", "completeness",
         "suspects")
ZONE_STEPS = ("completeness",)  # once per zone on its own sub-area, also in a tiled `all` (after the tiles)
TILED_STEPS = tuple(s for s in STEPS[STEPS.index("link"):] if s not in ZONE_STEPS)  # what `all` runs per tile (tiles.py)
TOOLS = ("mpc-orbits", "s3index")  # run by hand, never by `all`; no zone
EXTRA_STEPS = ("stack", "sessionstack")  # zone steps run by hand, never by `all` (stack: the featured zones, config
# STACK_ZONES); sessionstack also runs in `all` right after dots when config SESSION_STACK is on (steps_for)
L = log.get("cli")


def _run_step(step: str, store: Store, zone: C.Zone, args: argparse.Namespace) -> dict:
    if step == "photolist":
        from . import photolist
        return photolist.run(store, zone, force=args.force)
    if step == "refcat":
        from . import refcat
        # the combine (`all`) needs every catalogue whatever SKYSHIFT_REFCAT_CATALOGS says (that is for extraction prep)
        return refcat.run(store, zone, force=args.force, catalogs=refcat.FULL if args.step == "all" else None)
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
    if step == "qa":  # in `all` a failure is logged and the run goes on (lightcurves, suspects: no new grades)
        from . import qa
        return qa.run(store, zone, workers=args.workers) if args.step == "qa" else qa.run_safe(store, zone, workers=args.workers)
    if step == "classify":
        from . import classify
        return classify.run(store, zone, workers=args.workers)
    if step == "photoz":
        from . import photoz
        return photoz.run(store, zone)
    if step == "orphans":
        from . import orphans
        return orphans.run(store, zone, workers=args.workers)
    if step == "tracklets":
        from . import tracklets
        return tracklets.run(store, zone)
    if step == "score":
        from . import score
        return score.run(store, zone)
    if step == "suspects":
        from . import suspects
        return suspects.run(store, zone)
    if step == "stack":
        from . import stack
        return stack.run(store, zone, workers=args.workers, limit=args.limit, cache=args.cache)
    if step == "sessionstack":
        from . import coadd
        return coadd.run(store, zone, workers=args.workers, limit=args.limit)
    if step == "completeness":
        from . import inject
        try:
            return inject.run(store, zone, workers=args.workers)
        except Exception as e:  # noqa: BLE001  a report only: it never stops the zone's run
            L.exception("completeness %s failed; the zone's run goes on", zone.name)
            return {"error": repr(e)}
    raise ValueError(step)


def steps_for(step: str, from_step: str | None = None, session_stack: bool | None = None) -> tuple[str, ...]:
    """The steps one command runs: `all` (from from_step) in STEPS order, with sessionstack right after dots when
    config SESSION_STACK is on; any other step alone."""
    if step != "all":
        return (step,)
    steps = STEPS[STEPS.index(from_step):] if from_step else STEPS
    on = C.SESSION_STACK if session_stack is None else session_stack
    if on and "dots" in steps:
        i = steps.index("dots") + 1
        steps = (*steps[:i], "sessionstack", *steps[i:])
    return tuple(steps)


def step_dots_dir(store: Store, zone: C.Zone, step: str) -> str:
    """The dot-file folder a step of this zone reads: `dots` and `link` use the zone's detection setting
    (config.zone_dots); later steps the folder `link` recorded, so members.dot_row and the files always match
    (a zone linked before its re-extraction keeps reading dots/ until link runs again)."""
    if step in ("dots", "link"):
        return C.zone_dots(zone.name)[0]
    return linked_dots_dir(store, zone.name)


def ensure_full_refcat(store: Store, zone: C.Zone) -> dict | None:
    """`all --from <later step>`: a refcat built Gaia-only (extraction prep, its marker says so) gets the other
    catalogues first, so link, crossid, fragments, highpm, orphans and suspects never run on Gaia alone again."""
    from . import refcat
    if not refcat.built_partial(store, zone.name):
        return None
    L.info("=== refcat (%s): built with %s only; the combine needs every catalogue ===", zone.name,
           ",".join(refcat.catalogs_in(store, zone.name) or []))
    t0 = time.time()
    out = refcat.run(store, zone, catalogs=refcat.FULL)
    _update_summary(store, zone, "refcat", out, time.time() - t0)
    return out


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
    ap.add_argument("step", choices=(*STEPS, "all", *TOOLS, *EXTRA_STEPS))
    ap.add_argument("--zone", choices=sorted(C.ZONES), help="required for every step except mpc-orbits")
    ap.add_argument("--root", default="gs://nasa-glazing-data")
    ap.add_argument("--workers", type=int, default=None, help="default: one per CPU (s3index: 64 concurrent reads)")
    ap.add_argument("--limit", type=int, default=None, help="testing: dots does at most N photos, known at most N sessions")
    ap.add_argument("--force", action="store_true",
                    help="photolist, refcat, mpc-orbits, s3index: query the services again even if the files exist")
    ap.add_argument("--collections", default="dr1,qr3", help="s3index: S3 prefixes to index (dr1, qr3)")
    ap.add_argument("--periods", default=None, help="s3index: only these planning periods (comma list, globs allowed)")
    ap.add_argument("--procs", type=int, default=None, help="s3index: processes (default one per CPU)")
    ap.add_argument("--from", dest="from_step", choices=STEPS, default=None,
                    help="all: start at this step (e.g. --from link: combine again without photolist, refcat, dots, known)")
    ap.add_argument("--tiles", default=os.environ.get("SKYSHIFT_TILES", "auto"),
                    help="all: link … suspects per sky tile: auto (zones over config TILE_AUTO_MIN_DEG2 deg²), off, or about N "
                         "tiles (default: env SKYSHIFT_TILES, else auto)")
    ap.add_argument("--tile-deg", type=float, default=None, help="all: tile core side in degrees (tiles any zone)")
    ap.add_argument("--tile-dir", default=os.environ.get("SKYSHIFT_TILE_DIR") or None,
                    help="all, tiled: local work dir (default: env SKYSHIFT_TILE_DIR, else the system temp dir); removed after")
    ap.add_argument("--cache", default=None,
                    help="stack: local folder for the photos' pixel blocks (default: the temp dir; kept, so a rerun reads nothing)")
    ap.add_argument("--keep-tiles", action="store_true", help="all, tiled: keep the work dir (per-tile tables)")
    ap.add_argument("--zone-edge", choices=("border", "clip"), default="border" if C.TILE_ZONE_EDGE_MARGIN else "clip",
                    help="all, tiled: load the 2′ border outside the zone box too (default), or clip there as untiled runs do")
    args = ap.parse_args(argv)
    if args.step not in TOOLS and args.zone is None:
        ap.error(f"{args.step} needs --zone")
    if args.workers is None:
        args.workers = 64 if args.step == "s3index" else (os.cpu_count() or 1)
    log.setup()
    store = Store(args.root)
    if args.step == "s3index":
        from . import s3index
        cols = tuple(c.strip() for c in args.collections.split(",") if c.strip())
        periods = [p.strip() for p in args.periods.split(",") if p.strip()] if args.periods else None
        L.info("skyshift-pipeline s3index --root %s --collections %s --workers %d --procs %s --periods %s --force %s",
               store.root, ",".join(cols), args.workers, args.procs, periods, args.force)
        out = s3index.run(store, cols, workers=args.workers, procs=args.procs, periods=periods, force=args.force)
        return 0 if out["complete"] or periods else 1
    if args.step == "mpc-orbits":
        from . import known_mpc
        L.info("skyshift-pipeline mpc-orbits --root %s --force %s", store.root, args.force)
        L.info("mpc-orbits: %s", known_mpc.refresh_orbits(store, force=args.force))
        return 0
    zone = C.ZONES[args.zone]
    steps = steps_for(args.step, args.from_step)
    grid = None
    if args.step == "all":
        from . import tiles
        grid = tiles.choose(zone, args.tiles, args.tile_deg, args.workers)
        if grid and TILED_STEPS[0] not in steps:  # tiles run the whole chain from link; later starts run untiled
            L.info("all --from %s: after link, so untiled", args.from_step)
            grid = None
    L.info("skyshift-pipeline %s --zone %s --root %s --workers %d --limit %s%s", args.step, zone.name, store.root, args.workers,
           args.limit, f", tiled {grid[0]} × {grid[1]}" if grid else "")
    if args.step == "all" and "refcat" not in steps:
        ensure_full_refcat(store, zone)
    for step in steps:
        if grid and step in TILED_STEPS:
            if step == TILED_STEPS[0]:
                use_dots_dir(step_dots_dir(store, zone, TILED_STEPS[0]))  # the tiles run link first
                L.info("=== %s, tiled (%s, %s/) ===", " → ".join(TILED_STEPS), zone.name, dots_dir())
                out = tiles.run(store, zone, grid, args.workers, work_dir=args.tile_dir, keep_work=args.keep_tiles,
                                edge_margin=args.zone_edge == "border")
                if isinstance(out.get("link"), dict):
                    out["link"]["dots_dir"] = dots_dir()
                for k, v in out.items():
                    _update_summary(store, zone, k, v, v.pop("step_seconds", 0.0) if isinstance(v, dict) else 0.0)
            continue
        t0 = time.time()
        use_dots_dir(step_dots_dir(store, zone, step))
        L.info("=== %s (%s, %s/) ===", step, zone.name, dots_dir())
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
