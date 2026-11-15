"""object.json for every suspect and every candidate with static frames: web/public/data/cand/<id>/object.json.

The To check Viewer reads it before /api/object (static first), so a suspect's spectrum shows at
once instead of after the 40-70 s live measurement. It is the live API's own answer, saved as is.

    python3 scripts/precompute/suspect_objects.py                  # missing files only
    python3 scripts/precompute/suspect_objects.py --force --jobs 8
    python3 scripts/precompute/suspect_objects.py --api http://localhost:8080

Who: the suspects in web/public/data/suspects.json plus the candidates in candidates.json with a
frames_url. The position is the candidate's ra/dec, the same spot the Viewer opens on.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="https://skyshift.web.app")
    p.add_argument("--jobs", type=int, default=10)
    p.add_argument("--force", action="store_true")
    a = p.parse_args()

    cands = json.loads((DATA / "candidates.json").read_text())["candidates"]
    suspects = {s["id"] for s in json.loads((DATA / "suspects.json").read_text())["suspects"]}
    todo = []
    for c in cands:
        if not c.get("frames_url") and c["id"] not in suspects:
            continue
        out = DATA / "cand" / c["id"] / "object.json"
        if out.exists() and not a.force:
            continue
        todo.append((c, out))
    print(f"{len(todo)} to fetch", flush=True)

    failed = 0
    with ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(fetch, a.api, c["ra"], c["dec"]): (c, out) for c, out in todo}
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
