"""object.json for every suspect and every candidate with static frames: web/public/data/cand/<id>/object.json.

The To check Viewer reads it before /api/object (static first), so a suspect's spectrum shows at
once instead of after the 40-70 s live measurement. It is the live API's own answer, saved as is.

    python3 scripts/precompute/suspect_objects.py                  # missing files only
    python3 scripts/precompute/suspect_objects.py --force --jobs 8
    python3 scripts/precompute/suspect_objects.py --api http://localhost:8080
    python3 scripts/precompute/suspect_objects.py --top 300     # also the first 300 of the To check queue

The live API answers within its 40 s budget, so for most spots the answer is partial (no Gaia,
measure_reason "no_time", part of the spectrum). --full builds each spot here with the engine and
no time limit instead, replacing every partial file (suspects first, then candidates with frames,
then the queue). Run it from server/ (it imports skyshift_server, like precompute.py):

    cd server && uv run python ../scripts/precompute/suspect_objects.py --full --top 300 --jobs 10

Who: the suspects in web/public/data/suspects.json plus the candidates in candidates.json with a
frames_url; with --top N, also the first N of the To check queue (web/src/review/model.ts
queueOrder before any votes: no grade C, grade A first, then score, S/N and id). The position is
the candidate's ra/dec, the same spot the Viewer opens on.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "web" / "public" / "data"


def fetch(api: str, ra: float, dec: float, tries: int = 3) -> dict:
    url = f"{api}/api/object?ra={ra}&dec={dec}"
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))
    raise RuntimeError("unreachable")


def _partial(path: Path) -> bool:
    try:
        o = json.loads(path.read_text())
    except Exception:  # noqa: BLE001 - missing or broken: build it
        return True
    return o.get("measure_reason") == "no_time"


def _gaia_up() -> bool:
    try:
        urllib.request.urlopen("https://gea.esac.esa.int/tap-server/tap/availability", timeout=15)
        return True
    except Exception:  # noqa: BLE001
        return False


def build_full(ra: float, dec: float, gaia_up: bool) -> dict:
    """GET /api/object built here with no time limit (precompute.py's way)."""
    import os
    # No live request waiting: give IRSA's search time (the engine's 20 s live default counts a slow
    # answer as IRSA down and falls back to partial photo lists).
    os.environ.setdefault("SKYSHIFT_SIA_WAIT_S", "180")
    from precompute import clean
    from skyshift_server import blink, gaia
    from skyshift_server.budget import Budget
    from skyshift_server.cache import Cache
    from skyshift_server.errors import UpstreamError

    if not gaia_up:  # ESA unreachable: fail at once instead of a 90 s connect timeout per query
        def _down(*_a, **_k):
            raise UpstreamError("Gaia archive unreachable")
        gaia.cone = _down
    import os
    cache = Cache(os.environ.get("SKYSHIFT_CACHE", str(Path.home() / ".cache" / "skyshift")))
    budget = Budget(None)  # no time limit, but it records what was left out (e.g. IRSA down: fallback photos)
    out = clean(blink.build_object(ra, dec, cache=cache, budget=budget))
    cut = [r for r in budget.partial if "gaia" not in r.lower()]
    if cut:
        raise RuntimeError("partial: " + "; ".join(cut))
    return out


def _desc(v: object) -> tuple[int, float]:
    """Sort key for 'high to low, missing last' (byPromise in web/src/review/model.ts)."""
    return (1, 0.0) if not isinstance(v, (int, float)) else (0, -float(v))


def queue_top(cands: list[dict], n: int) -> list[dict]:
    """The To check queue's first n candidates before any votes."""
    q = [c for c in cands if c.get("grade") != "C"]
    q.sort(key=lambda c: (0 if c.get("grade") == "A" else 1, _desc(c.get("score")), _desc(c.get("snr")), c["id"]))
    return q[:n]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="https://skyshift.web.app")
    p.add_argument("--jobs", type=int, default=10)
    p.add_argument("--force", action="store_true")
    p.add_argument("--top", type=int, default=0, help="also the first N of the To check queue")
    p.add_argument("--full", action="store_true", help="build here without the live time limit; replace partial files")
    a = p.parse_args()

    cands = json.loads((DATA / "candidates.json").read_text())["candidates"]
    suspects = {s["id"] for s in json.loads((DATA / "suspects.json").read_text())["suspects"]}
    top = {c["id"] for c in queue_top(cands, a.top)}
    todo = []
    for c in cands:
        if not c.get("frames_url") and c["id"] not in suspects and c["id"] not in top:
            continue
        out = DATA / "cand" / c["id"] / "object.json"
        if out.exists() and not a.force and not (a.full and _partial(out)):
            continue
        todo.append((c, out))
    # Suspects first, then candidates with frames, then the queue.
    todo.sort(key=lambda t: (t[0]["id"] not in suspects, not t[0].get("frames_url")))
    print(f"{len(todo)} to {'build' if a.full else 'fetch'}", flush=True)

    failed = 0
    if a.full:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        up = _gaia_up()
        print(f"Gaia archive {'up' if up else 'DOWN: built without Gaia'}", flush=True)
        ex = ProcessPoolExecutor(a.jobs)
        submit = lambda c: ex.submit(build_full, c["ra"], c["dec"], up)  # noqa: E731
    else:
        ex = ThreadPoolExecutor(a.jobs)
        submit = lambda c: ex.submit(fetch, a.api, c["ra"], c["dec"])  # noqa: E731
    with ex:
        futs = {submit(c): (c, out) for c, out in todo}
        for f in as_completed(futs):
            c, out = futs[f]
            try:
                obj = f.result()
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(obj, separators=(",", ":")))
            except Exception as e:  # noqa: BLE001 - report and go on
                failed += 1
                print(f"FAIL {c['id']}: {e}", flush=True)
                continue
            print(f"ok {c['id']} {len(obj.get('spectrum') or [])} points", flush=True)
    print(f"done, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
