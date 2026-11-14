"""Static blink frames for the most promising candidates: web/public/data/cand/<id>/.

Run from server/ (it imports skyshift_server and skyshift_core, like scripts/precompute), after the
exporter (pipeline: `uv run skyshift-candidates`) has written web/public/data/candidates.json:

    cd server
    uv run python ../scripts/candidates/frames.py                        # mover=25,high_pm=20,spectral=15
    uv run python ../scripts/candidates/frames.py --top mover=5,high_pm=0,spectral=0 --dry-run

For each kind, the candidates with the highest `score` (any zone) get frames; the rest keep
frames_url null and the site asks /api/frames. Per candidate (shapes of contracts/api.md, data_url
relative to the JSON):

    cand/<id>/frames.json       GET /api/frames for the candidate's clock and field
    cand/<id>/frames/*.bin      the frame data

- mover: hours clock, 20′ field (native pixels), each detector the mover was seen in (at most two),
  the exposures within HOURS_WINDOW_D of its mjd (its own session and the next ones), at most
  HOURS_MAX per detector: blink.exposure_epochs, gaia.cone and blink.known_for_epochs, the engine's
  own hours-clock code. (build_frames' hours clock shows the visit with the most exposures, which is
  not always the mover's.)
- high_pm, spectral: months clock, 5′ field, the candidate's detector, every exposure of every visit:
  blink.build_frames(..., per_visit=None), as scripts/precompute does for the featured stars.

Then frames_url is set in candidates.json for every candidate whose cand/<id>/frames.json exists
(the exporter does the same when it runs again), and folders of candidates no longer chosen are removed.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skyshift_server import blink, gaia, search
from skyshift_server.cache import Cache

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "web" / "public" / "data"
sys.path.insert(0, str(ROOT / "scripts" / "precompute"))
from precompute import clean, folder_bytes, write_json  # noqa: E402  (P2's writers: NaN -> null, compact JSON)

DEFAULT_TOP = "mover=25,high_pm=20,spectral=15"
HOURS_FOV = 20.0
HOURS_WINDOW_D = 0.3   # days either side of the mover's mjd
HOURS_MAX = 10         # exposures per detector, the nearest in time
MONTHS_FOV = 5.0
MAX_DETS = 2

log = logging.getLogger("cand-frames")


def choose(cands: list[dict], top: dict[str, int]) -> list[dict]:
    """The `top[kind]` highest-scoring candidates of each kind (ties: id)."""
    out = []
    for kind, n in top.items():
        cs = [c for c in cands if c["kind"] == kind and c.get("det") is not None]
        cs.sort(key=lambda c: (-(c.get("score") or 0.0), c["id"]))
        out += cs[:n]
    return out


def mover_dets(c: dict) -> list[int]:
    """The mover's detector first, then its simultaneous partner if the reasons say two detectors saw it."""
    d = int(c["det"])
    dets = [d]
    if "two_detectors" in c.get("reasons", []):
        dets.append(d + 3 if d <= 3 else d - 3)
    return dets[:MAX_DETS]


def hours_images(found, det: int, mjd: float) -> list:
    near = [im for im in found.of_detector(det) if abs(im.mjd - mjd) <= HOURS_WINDOW_D]
    near.sort(key=lambda im: abs(im.mjd - mjd))
    return sorted(near[:HOURS_MAX], key=lambda im: im.mjd)


def build_mover(c: dict, cache: Cache) -> tuple[dict, dict[str, bytes]] | None:
    ra, dec, mjd = c["ra"], c["dec"], c["mjd"]
    found = search.search(ra, dec, cache=cache)
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


def build_months(c: dict, cache: Cache) -> tuple[dict, dict[str, bytes]] | None:
    fr = blink.build_frames(c["ra"], c["dec"], MONTHS_FOV, "months", int(c["det"]), cache=cache, per_visit=None)
    return fr.meta, fr.blobs


def build_one(c: dict, cache: Cache, out: Path) -> bool:
    t0 = time.time()
    try:
        res = build_mover(c, cache) if c["kind"] == "mover" else build_months(c, cache)
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


def dumps(doc: dict) -> str:
    """The exporter's layout: compact JSON, one candidate per line."""
    head = {k: v for k, v in doc.items() if k != "candidates"}
    lines = [json.dumps(c, ensure_ascii=False, separators=(",", ":"), allow_nan=False) for c in doc["candidates"]]
    return json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"candidates":[\n' + ",\n".join(lines) + "\n]}\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--candidates", default=str(DATA / "candidates.json"))
    ap.add_argument("--top", default=DEFAULT_TOP, help=f"kind=n,... (default {DEFAULT_TOP})")
    ap.add_argument("--rebuild", action="store_true", help="rebuild frames that already exist")
    ap.add_argument("--parallel", type=int, default=3, help="candidates built at once")
    ap.add_argument("--dry-run", action="store_true", help="list the chosen candidates and stop")
    ap.add_argument("--cache", help="cache folder or gs:// (default: SKYSHIFT_CACHE or ~/.cache/skyshift)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    path = Path(a.candidates)
    out = path.parent
    doc = json.loads(path.read_text())
    top = {k: int(v) for k, v in (x.split("=") for x in a.top.split(",") if x)}
    chosen = choose(doc["candidates"], top)
    for c in chosen:
        log.info("chosen: %s %s score %s det %s", c["kind"], c["id"], c["score"], c["det"])
    if a.dry_run:
        return 0
    todo = [c for c in chosen if a.rebuild or not (out / "cand" / c["id"] / "frames.json").exists()]
    log.info("%d chosen, %d to build", len(chosen), len(todo))
    cache = Cache(a.cache)
    with ThreadPoolExecutor(max(1, a.parallel)) as ex:
        list(ex.map(lambda c: build_one(c, cache, out), todo))
    keep = {c["id"] for c in chosen}
    cand_dir = out / "cand"
    if cand_dir.exists():
        for p in cand_dir.iterdir():
            if p.is_dir() and p.name not in keep:
                shutil.rmtree(p)
                log.info("removed %s (no longer chosen)", p.name)
    n = 0
    for c in doc["candidates"]:
        ok = (cand_dir / c["id"] / "frames.json").exists()
        c["frames_url"] = f"cand/{c['id']}/frames.json" if ok else None
        n += ok
    path.write_text(dumps(doc))
    log.info("frames_url set on %d candidates; cand/ holds %.1f MB", n, folder_bytes(cand_dir) / 1e6 if cand_dir.exists() else 0.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
