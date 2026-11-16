"""Fill exposures for a candidate's empty edge: web/public/data/cand/<id>/fill.json (contracts/api.md v3.20).

A candidate's static hours frames (frames.py) are its own exposures, often of one pointing: when it sat
near a detector's edge they share one empty corner, and they cannot fill each other. This picks other
exposures of the same visit and detector whose footprints (the search's s_region, coverage.py) cover
that corner, best first, and saves them next to frames.json in the same shape. The site paints them
only where the candidate's own photo has no data, drawn as filled; they are never blinked or measured.

Runs on a laptop (IRSA search and cutouts, no VMs). It waits for IRSA's search (SKYSHIFT_SEARCH_FALLBACK=0):
the fallback's saved photo lists carry no footprints. Run from server/, like frames.py:

    cd server
    uv run python ../scripts/candidates/fill.py              # every static hours candidate with an edge
    uv run python ../scripts/candidates/fill.py --dry-run    # what it would pick, nothing written
    uv run python ../scripts/candidates/fill.py --id ecl35-...

Per candidate and detector: the empty part is the frame pixels with no data (raw 0) in all of its own
exposures (the site already fills one photo's gap from the others). If that is under MIN_EMPTY of the frame, nothing is done. Else, from the same visit's other
exposures (within VISIT_D of the candidate's own), greedily the one whose footprint adds the most of the
field not yet covered (own exposures count as covering), nearest in time on a tie, up to MAX_FILL, until
the gain is under MIN_GAIN. Their cutouts are made by the engine (blink.exposure_epochs, prefix f<fov>).
A candidate with nothing to add keeps no fill.json (an old one is removed).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from skyshift_server import blink, coverage, search
from skyshift_server.cache import Cache

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "web" / "public" / "data"
sys.path.insert(0, str(ROOT / "scripts" / "precompute"))
sys.path.insert(0, str(ROOT / "scripts" / "candidates"))
from precompute import clean, folder_bytes, write_json  # noqa: E402
from frames import fov_of  # noqa: E402

MIN_EMPTY = 0.005  # share of the frame with no data in every own exposure below which nothing is filled
VISIT_D = 45.0     # days from the candidate's own exposures: the same visit (visits are ~6 months apart)
MAX_FILL = 4       # fill exposures per detector
MIN_GAIN = 0.002   # share of the field an exposure must add to be taken

log = logging.getLogger("cand-fill")


def empty_share(folder: Path, epochs: list[dict], det: int) -> float:
    """Share of the frame with no data (raw 0) in every own exposure of `det`: what they cannot fill
    from each other (the site fills a photo's gap from its other photos already)."""
    acc = None
    for e in epochs:
        if e["det"] != det:
            continue
        p = folder / e["data_url"]
        if not p.exists():
            continue
        z = np.fromfile(p, dtype="<u2") == 0
        acc = z if acc is None else (acc & z)
    return 0.0 if acc is None else float(acc.mean())


def pick(found: search.SearchResult, det: int, own_ids: set[str], ra: float, dec: float, fov: float,
         ) -> tuple[list[search.Image], float, float]:
    """Fill exposures for `det`, and the field share covered before and after them (footprints)."""
    ims = found.of_detector(det)
    own = [im for im in ims if f"d{im.det}-{im.obs_id}" in own_ids]
    if not own or any(not im.s_region for im in own):
        return [], 0.0, 0.0  # no footprints (a saved search without them): nothing to choose by
    t0 = float(np.median([im.mjd for im in own]))
    rest = [im for im in ims if im not in own and abs(im.mjd - t0) <= VISIT_D and im.s_region]
    cover = coverage.for_frames(own + rest, ra, dec, "hours", fov)
    before = cover.union(own)
    have, chosen = list(own), []
    while rest and len(chosen) < MAX_FILL:
        best = max(rest, key=lambda im: (cover.gain(have, im), -abs(im.mjd - t0)))
        if cover.gain(have, best) < MIN_GAIN:
            break
        chosen.append(best)
        have.append(best)
        rest.remove(best)
    return sorted(chosen, key=lambda im: im.mjd), before, cover.union(have)


def build(folder: Path, cache: Cache, dry: bool) -> str:
    meta = json.loads((folder / "frames.json").read_text())
    if meta["clock"] != "hours":
        return "months"
    fov = fov_of(meta)
    ra, dec = meta["center"]["ra"], meta["center"]["dec"]
    own_ids = {e["id"] for e in meta["epochs"]}
    dets = sorted({e["det"] for e in meta["epochs"]})
    want = {d: empty_share(folder, meta["epochs"], d) for d in dets}
    want = {d: s for d, s in want.items() if s >= MIN_EMPTY}
    out = folder / "fill.json"
    if not want:
        return "no edge"
    found = search.search(ra, dec, cache=cache)
    epochs, blobs, grid, notes = [], {}, None, []
    for det, share in want.items():
        ims, before, after = pick(found, det, own_ids, ra, dec, fov)
        notes.append(f"D{det} empty {share:.1%}, footprints {before:.1%} -> {after:.1%} with {len(ims)}")
        if not ims or dry:
            continue
        eps, bl, g = blink.exposure_epochs(ims, ra, dec, fov, cache, f"f{int(fov)}")
        epochs += eps
        blobs.update(bl)
        grid = grid or g
    if dry:
        return "; ".join(notes)
    for old in folder.glob("frames/f*_d*.bin"):
        old.unlink()
    if not epochs:
        out.unlink(missing_ok=True)
        return "; ".join(notes) + " (nothing to add)"
    for name, data in blobs.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    epochs.sort(key=lambda e: (e["mjd_mid"], e["det"]))
    write_json(out, clean(blink.frames_meta("hours", grid, epochs, [], [], found.archive_checked)), compact=True)
    return "; ".join(notes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--id", action="append", help="only these candidates")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path, default=DATA)
    a = ap.parse_args()
    # Wait for IRSA's own search (it carries the footprints): the fallback's saved lists have none.
    os.environ.setdefault("SKYSHIFT_SEARCH_FALLBACK", "0")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cache = Cache()
    folders = sorted(p.parent for p in (a.out / "cand").glob("*/frames.json"))
    if a.id:
        folders = [f for f in folders if f.name in set(a.id)]
    for f in folders:
        t = time.time()
        try:
            note = build(f, cache, a.dry_run)
        except Exception as e:  # noqa: BLE001 - one candidate must not stop the rest
            log.warning("%s failed: %s", f.name, e)
            continue
        if note != "months":
            log.info("%s: %s (%.0f s, folder %.2f MB)", f.name, note, time.time() - t, folder_bytes(f) / 1e6)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
