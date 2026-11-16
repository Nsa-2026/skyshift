"""Static blink frames for the most promising candidates: web/public/data/cand/<id>/.

Run from server/ (it imports skyshift_server and skyshift_core, like scripts/precompute), after the
exporter (pipeline: `uv run skyshift-candidates`) has written web/public/data/candidates.json:

    cd server
    uv run python ../scripts/candidates/frames.py                  # mover=25,high_pm=20,spectral=15,shape=15
    uv run python ../scripts/candidates/frames.py --top mover=5,high_pm=0,spectral=0,shape=0 --dry-run
    uv run python ../scripts/candidates/frames.py --ab-top 300     # movers: the 300 best open A/B only
    uv run python ../scripts/candidates/frames.py --known-only           # only known[] again (MPC)
    uv run python ../scripts/candidates/frames.py --measured-only [--clock hours|months]  # only measured again

For each kind, the candidates with the highest `score` (any zone) get frames, and so does every mover
of grade A or B, whatever `--top` says (they are few, and /api/frames has no time parameter: a live
hours blink shows the visit with the most exposures, not always the mover's session). Movers of
grade C never get frames. With `--ab-top N` (the PC rebuild: 300) movers get frames only as the N
highest-scoring open A/B movers: hundreds of A/B movers at ~0.45 MB each would bloat git, and the Viewer
now loads a mover's whole visit live. The rest keep frames_url null and the site asks /api/frames. Per candidate
(shapes of contracts/api.md, data_url relative to the JSON):

    cand/<id>/frames.json       GET /api/frames for the candidate's clock and field
    cand/<id>/frames/*.bin      the frame data

- mover: hours clock, 20′ field (native pixels), each detector the mover was seen in (at most two),
  the exposures within HOURS_WINDOW_D of its mjd (its own session and the next ones), at most
  HOURS_MAX per detector: blink.exposure_epochs, gaia.cone and blink.known_for_epochs, the engine's
  own hours-clock code. (build_frames' hours clock shows the visit with the most exposures, which is
  not always the mover's.) The frames must show the mover's own exposures (`own_times`: mjd ± half
  its span, the span read from its `why`; one moment for two_detectors). A partial search (IRSA
  down: the server's fallback photo lists) can miss them: the search is asked once more without its
  caches, then the mover is skipped and logged (frames_url stays null). Older frames without them
  are rebuilt, or removed when the rebuild fails.
- high_pm, spectral, shape: months clock, 5′ field, the candidate's detector, every exposure of every
  visit: blink.build_frames(..., per_visit=None), as scripts/precompute does for the featured stars.

Known objects are MPC orbits only (server known.py, skyshift_core.known_index.known_near), never
SkyBoT. After a new MPC index, `--known-only` recomputes only known[] of every existing
cand/<id>/frames.json, at each epoch's mjd_mid and centre (epochs, Gaia and frame files untouched).

Then frames_url is set in candidates.json for every candidate whose cand/<id>/frames.json exists
(the exporter does the same when it runs again), and folders of candidates no longer chosen are removed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

from skyshift_server import blink, gaia, search
from skyshift_server.cache import Cache

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "web" / "public" / "data"
sys.path.insert(0, str(ROOT / "scripts" / "precompute"))
from precompute import clean, folder_bytes, write_json  # noqa: E402  (P2's writers: NaN -> null, compact JSON)

DEFAULT_TOP = "mover=25,high_pm=20,spectral=15,shape=15"
HOURS_FOV = 20.0
HOURS_WINDOW_D = 0.3   # days either side of the mover's mjd
HOURS_MAX = 10         # exposures per detector, the nearest in time
MONTHS_FOV = 5.0
MAX_DETS = 2
ALWAYS_GRADES = ("A", "B")   # movers of these grades always get frames; grade C never
OWN_TOL_D = 1.0 / 1440.0     # an exposure within 1 min of a mover's dot time is its own (cadence 2.1 min)
SESSION_D = 10.0 / 1440.0    # a session: exposures with gaps under 10 min
# The exporter's mover `why`: "... in 2 exposures 2.1 minutes apart" or "... in 3 exposures over 4.3 minutes".
SPAN_RE = re.compile(r"in \d+ exposures (?:([\d.]+) minutes apart|over ([\d.]+) minutes)")

log = logging.getLogger("cand-frames")


def choose(cands: list[dict], top: dict[str, int], ab_top: int | None = None) -> list[dict]:
    """The `top[kind]` highest-scoring candidates of each kind (ties: id), then every mover of grade A
    or B not chosen yet, whatever `top` says. Never a mover of grade C.
    ab_top (--ab-top): movers only as the ab_top highest-scoring open movers of grade A or B (status open
    or none; ties: id), `top`'s mover count ignored. The Viewer loads the other movers' visits live."""
    usable = [c for c in cands if c.get("det") is not None and not (c["kind"] == "mover" and c.get("grade") == "C")]
    out = []
    for kind, n in top.items():
        if ab_top is not None and kind == "mover":
            continue
        cs = [c for c in usable if c["kind"] == kind]
        cs.sort(key=lambda c: (-(c.get("score") or 0.0), c["id"]))
        out += cs[:n]
    have = {c["id"] for c in out}
    if ab_top is not None:
        opened = [c for c in usable if c["kind"] == "mover" and c.get("grade") in ALWAYS_GRADES
                  and c.get("status") in (None, "open")]
        opened.sort(key=lambda c: (-(c.get("score") or 0.0), c["id"]))
        return out + opened[:max(0, ab_top)]
    graded = [c for c in usable if c["kind"] == "mover" and c.get("grade") in ALWAYS_GRADES and c["id"] not in have]
    graded.sort(key=lambda c: (ALWAYS_GRADES.index(c["grade"]), -(c.get("score") or 0.0), c["id"]))
    return out + graded


def mover_dets(c: dict) -> list[int]:
    """The mover's detector first, then its simultaneous partner if the reasons say two detectors saw it."""
    d = int(c["det"])
    dets = [d]
    if "two_detectors" in c.get("reasons", []):
        dets.append(d + 3 if d <= 3 else d - 3)
    return dets[:MAX_DETS]


def own_times(c: dict) -> list[float] | None:
    """MJDs of the mover's own exposures, which its frames must show: its mjd for two_detectors (one
    moment), mjd ± span/2 otherwise (mjd is the mean of its dots; the exporter writes the span in
    `why`). None when the span can't be read: then an exposure on each side of mjd is enough."""
    if "two_detectors" in c.get("reasons", []):
        return [c["mjd"]]
    m = SPAN_RE.search(c.get("why") or "")
    if not m:
        return None
    half = float(m.group(1) or m.group(2)) / 1440.0 / 2.0
    return [c["mjd"] - half, c["mjd"] + half]


def missing_own(c: dict, epochs: list[dict]) -> str | None:
    """None when `epochs` show the mover's own exposures, else what is missing (for the log)."""
    t = [e["mjd_mid"] for e in epochs]
    want = own_times(c)
    if want is None:
        before = any(-SESSION_D <= x - c["mjd"] < 0 for x in t)
        after = any(0 < x - c["mjd"] <= SESSION_D for x in t)
        return None if before and after else f"no exposure on each side of its mjd in its session ({len(t)} epochs)"
    lost = [w for w in want if not any(abs(x - w) <= OWN_TOL_D for x in t)]
    if not lost:
        return None
    return "none at " + ", ".join(f"{(w - c['mjd']) * 1440.0:+.2f} min" for w in lost) + f" from its mjd ({len(t)} epochs)"


def hours_images(found, det: int, mjd: float) -> list:
    near = [im for im in found.of_detector(det) if abs(im.mjd - mjd) <= HOURS_WINDOW_D]
    near.sort(key=lambda im: abs(im.mjd - mjd))
    return sorted(near[:HOURS_MAX], key=lambda im: im.mjd)


def build_mover(c: dict, cache: Cache, refresh: bool = False) -> tuple[dict, dict[str, bytes]] | None:
    ra, dec, mjd = c["ra"], c["dec"], c["mjd"]
    found = search.search(ra, dec, cache=cache, refresh=refresh)
    epochs, blobs, grid = [], {}, None
    for det in mover_dets(c):
        ims = hours_images(found, det, mjd)
        if not ims:
            continue
        eps, bl, g = blink.exposure_epochs(ims, ra, dec, HOURS_FOV, cache, f"h{int(HOURS_FOV)}")
        epochs += eps
        blobs.update(bl)
        grid = grid or g
    if not epochs:
        return None
    epochs.sort(key=lambda e: (e["mjd_mid"], e["det"]))
    try:
        stars = gaia.cone(ra, dec, HOURS_FOV / 2.0 * 2**0.5, cache=cache)
    except Exception as e:  # noqa: BLE001
        log.warning("%s: Gaia failed: %s", c["id"], e)
        stars = []
    known_ = blink.known_for_epochs(epochs, ra, dec, HOURS_FOV, cache)
    return blink.frames_meta("hours", grid, epochs, stars, known_, found.archive_checked), blobs


def build_mover_checked(c: dict, cache: Cache) -> tuple[dict, dict[str, bytes]] | None:
    """build_mover, kept only when the frames show the mover's own exposures: else the search is asked
    once more without its caches (during an IRSA outage the fallback search is partial), then None."""
    for attempt in (0, 1):
        res = build_mover(c, cache, refresh=attempt > 0)
        miss = missing_own(c, res[0]["epochs"]) if res else "no frames"
        if miss is None:
            return res
        log.warning("%s: frames lack the mover's own exposures: %s; %s", c["id"], miss,
                    "searching again" if attempt == 0 else "skipped, frames_url stays null")
    return None


def build_months(c: dict, cache: Cache) -> tuple[dict, dict[str, bytes]] | None:
    fr = blink.build_frames(c["ra"], c["dec"], MONTHS_FOV, "months", int(c["det"]), cache=cache, per_visit=None)
    return fr.meta, fr.blobs


def build_one(c: dict, cache: Cache, out: Path) -> bool:
    t0 = time.time()
    try:
        res = build_mover_checked(c, cache) if c["kind"] == "mover" else build_months(c, cache)
    except Exception as e:  # noqa: BLE001 - one candidate must not stop the rest
        log.warning("%s failed: %s", c["id"], e)
        return False
    if res is None:
        log.warning("%s: no frames", c["id"])
        return False
    meta, blobs = res
    folder = out / "cand" / c["id"]
    if folder.exists():
        shutil.rmtree(folder)
    for name, data in blobs.items():
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    write_json(folder / "frames.json", clean(meta), compact=True)
    log.info("%s (%s, score %s): %d epochs, %.2f MB in %.0f s", c["id"], c["kind"], c.get("score"),
             len(meta["epochs"]), folder_bytes(folder) / 1e6, time.time() - t0)
    return True


def mpc_index_ok(cache: Cache) -> bool:
    """The MPC index loads from this cache. Without it every frames file would get known[] = [] (blink logs and goes
    on), so the static build stops instead: copy gs://nasa-glazing-data/cache/<known.INDEX_KEY> into the local cache
    or pass --cache gs://nasa-glazing-data/cache."""
    from skyshift_server import known
    try:
        known.mpc_index(cache)
        return True
    except Exception as e:  # noqa: BLE001 - UpstreamError, or a read failure
        log.error("MPC index unavailable (%s): known[] would be empty in every frames file; stopping", e)
        return False


def needs_build(c: dict, out: Path) -> bool:
    """No frames yet, frames of another detector than the candidate's (a re-export can change its best detector,
    and the site then asks the live API for the missing one), or a mover's frames without its own exposures (built
    from a partial search)."""
    p = out / "cand" / c["id"] / "frames.json"
    if not p.exists():
        return True
    eps = json.loads(p.read_text())["epochs"]
    dets = {int(e["det"]) for e in eps if e.get("det") is not None}
    if dets and c.get("det") is not None and int(c["det"]) not in dets:
        return True
    return c["kind"] == "mover" and missing_own(c, eps) is not None


def drop_without_own(cands: list[dict], cand_dir: Path) -> None:
    """Remove every mover's frames that don't show its own exposures, so it gets no frames_url (here
    and when the exporter runs again)."""
    for c in cands:
        p = cand_dir / c["id"] / "frames.json"
        if c["kind"] != "mover" or not p.exists():
            continue
        miss = missing_own(c, json.loads(p.read_text())["epochs"])
        if miss is not None:
            shutil.rmtree(p.parent)
            log.warning("%s: removed frames without the mover's own exposures (%s)", c["id"], miss)


def fov_of(meta: dict) -> float:
    """The field a frames.json was built with: HOURS_FOV for the hours clock, MONTHS_FOV for months."""
    fov = HOURS_FOV if meta["clock"] == "hours" else MONTHS_FOV
    built = meta["size"][0] * meta["pixel_arcsec"] / 60.0
    if abs(built - fov) > 0.1:
        raise ValueError(f"{meta['clock']} frames span {built:.2f}', expected {fov}'")
    return fov


def refresh_known(cand_dir: Path, cache: Cache) -> None:
    """Only `known` again in every cand/<id>/frames.json (MPC, server known.py), at each epoch's
    mjd_mid and centre; everything else stays byte for byte. Every file is computed before any is
    written, and a failure stops: nothing half-done is written."""
    from skyshift_server import known

    new: dict[Path, dict] = {}
    for path in sorted(cand_dir.glob("*/frames.json")):
        meta = json.loads(path.read_text())
        r = fov_of(meta) / 2.0 * 2**0.5
        by_centre: dict[tuple[float, float], list[dict]] = {}
        for ep in meta["epochs"]:
            c = ep.get("center") or meta["center"]
            by_centre.setdefault((c["ra"], c["dec"]), []).append(ep)
        found: list[dict] = []
        for (ra, dec), eps in by_centre.items():
            found += known.near(ra, dec, r, [e["mjd_mid"] for e in eps], epoch_ids=[e["id"] for e in eps], cache=cache)
        log.info("%s: known %d -> %d", path.parent.name, len(meta["known"]), len(found))
        meta["known"] = found
        new[path] = meta
    for path, meta in new.items():
        write_json(path, clean(meta), compact=True)
    log.info("known[] recomputed in %d frames files", len(new))


def refresh_measured(cand_dir: Path, cands: list[dict], cache: Cache, clock: str | None = None) -> None:
    """Only `measured` and `measured_reason` again in every cand/<id>/frames.json (server measured.py,
    results/<zone>/ and dots/), with the candidate's zone; `clock` ("hours" or "months"): only the files of
    that clock. Every file is computed before any is written; a read failure ("unavailable") stops and
    nothing is written. The detections are written as measured.py gives them: the contract's cap (250 an
    epoch, measured.MAX_PER_EPOCH) and no tighter one, so every star the plate shows keeps its circle.

        cd server && uv run python ../scripts/candidates/frames.py --measured-only [--clock hours]
    """
    from precompute import months_keys
    from skyshift_server import measured

    zone_of = {c["id"]: c.get("zone") for c in cands}
    new: dict[Path, dict] = {}
    for path in sorted(cand_dir.glob("*/frames.json")):
        meta = json.loads(path.read_text())
        if clock and meta["clock"] != clock:
            continue
        keys = months_keys(meta, cache) if meta["clock"] == "months" else {}
        m, why = measured.build(meta, keys, zone=zone_of.get(path.parent.name))
        if why == "unavailable":
            raise RuntimeError(f"{path.parent.name}: the measured layer could not be read; nothing written")
        meta["measured"], meta["measured_reason"] = m, why
        new[path] = meta
        log.info("%s: measured %s", path.parent.name,
                 f"zone {m['zone']} ({m['stage']}): {m['counts']}, {len(m['tracks'])} tracks, {len(m['shifts'])} shifts, "
                 f"{m['photos']} photos ({m['photos_missing']} missing)" if m else why)
    for path, meta in new.items():
        write_json(path, clean(meta), compact=True)
    log.info("measured recomputed in %d frames files", len(new))


def dumps(doc: dict) -> str:
    """The exporter's layout: compact JSON, one candidate per line."""
    head = {k: v for k, v in doc.items() if k != "candidates"}
    lines = [json.dumps(c, ensure_ascii=False, separators=(",", ":"), allow_nan=False) for c in doc["candidates"]]
    return json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"candidates":[\n' + ",\n".join(lines) + "\n]}\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--candidates", default=str(DATA / "candidates.json"))
    ap.add_argument("--top", default=DEFAULT_TOP, help=f"kind=n,... (default {DEFAULT_TOP})")
    ap.add_argument("--ab-top", type=int, default=None,
                    help="movers: only the N highest-scoring open grade A/B movers (default: every A/B mover and "
                         "--top's movers); the Viewer loads the others' visits live")
    ap.add_argument("--rebuild", action="store_true", help="rebuild frames that already exist")
    ap.add_argument("--parallel", type=int, default=6, help="candidates built at once")
    ap.add_argument("--deadline", type=float, default=45.0, help="minutes; then finish with what was built")
    ap.add_argument("--dry-run", action="store_true", help="list the chosen candidates and stop")
    ap.add_argument("--known-only", action="store_true",
                    help="recompute only known[] of the existing cand/<id>/frames.json (MPC) and stop")
    ap.add_argument("--measured-only", action="store_true",
                    help="recompute only measured of the existing cand/<id>/frames.json (results/, dots/) and stop")
    ap.add_argument("--clock", choices=("hours", "months"),
                    help="with --measured-only: only the frames files of this clock")
    ap.add_argument("--cache", help="cache folder or gs:// (default: SKYSHIFT_CACHE or ~/.cache/skyshift)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    path = Path(a.candidates)
    out = path.parent
    if not (a.dry_run or a.measured_only) and not mpc_index_ok(Cache(a.cache)):
        return 2
    if a.known_only:
        refresh_known(out / "cand", Cache(a.cache))
        return 0
    if a.measured_only:
        refresh_measured(out / "cand", json.loads(path.read_text())["candidates"], Cache(a.cache), a.clock)
        return 0
    doc = json.loads(path.read_text())
    top = {k: int(v) for k, v in (x.split("=") for x in a.top.split(",") if x)}
    chosen = choose(doc["candidates"], top, a.ab_top)
    for c in chosen:
        log.info("chosen: %s %s score %s det %s", c["kind"], c["id"], c["score"], c["det"])
    if a.dry_run:
        return 0
    todo = [c for c in chosen if a.rebuild or needs_build(c, out)]
    log.info("%d chosen, %d to build", len(chosen), len(todo))
    cache = Cache(a.cache)
    # Some upstream reads can hang without a timeout: stop waiting after --deadline minutes, keep
    # what was built (frames.json is written last, so a half-written folder gets no frames_url).
    ex = ThreadPoolExecutor(max(1, a.parallel))
    futs = {ex.submit(build_one, c, cache, out): c["id"] for c in todo}
    try:
        for _ in as_completed(futs, timeout=a.deadline * 60.0):
            pass
    except FuturesTimeout:
        left = [i for f, i in futs.items() if not f.done()]
        log.warning("deadline: %d still building, left without static frames: %s", len(left), ", ".join(left))
    ex.shutdown(wait=False, cancel_futures=True)
    keep = {c["id"] for c in chosen}
    cand_dir = out / "cand"
    if cand_dir.exists():
        for p in cand_dir.iterdir():
            if p.is_dir() and p.name not in keep:
                shutil.rmtree(p)
                log.info("removed %s (no longer chosen)", p.name)
        drop_without_own(chosen, cand_dir)
    n = 0
    for c in doc["candidates"]:
        ok = (cand_dir / c["id"] / "frames.json").exists()
        c["frames_url"] = f"cand/{c['id']}/frames.json" if ok else None
        n += ok
    path.write_text(dumps(doc))
    log.info("frames_url set on %d candidates; cand/ holds %.1f MB", n, folder_bytes(cand_dir) / 1e6 if cand_dir.exists() else 0.0)
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # don't wait for builds still hanging after the deadline
