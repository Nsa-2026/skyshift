#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Demo check: do the featured targets and a few random coordinates load on the site?

    uv run scripts/demo_check.py                                  # https://skyshift.web.app
    uv run scripts/demo_check.py --base http://localhost:8080     # another host
    uv run scripts/demo_check.py --base http://localhost:8001 --api-base http://localhost:8000
    uv run scripts/demo_check.py --random 10 --seed 1 --json out.json   # input for V8

Static part, for every featured target: GET /data/<slug>/frames.json and object.json, then every
frame file frames.json points to. Checks the shapes in contracts/api.md, that each frame is
2·nx·ny bytes of uint16, and times the first frame (frames.json + one frame, as the site loads it).
--all-sets also checks every frame set listed in /data/<slug>/index.json.

Live part, for --random N points picked uniformly on the sphere: GET /api/frames (months and
hours clock), the first frame, then the rest, and GET /api/object.

Time to first frame = frames.json request + the first frame file (the earliest D2 epoch, else
the earliest epoch). Static targets over 2 s and live ones over 15 s are flagged SLOW.
median_first_frame_s (for V8) is the median over the random /api/frames requests, a failed
request counting as slower than any success (null when half or more failed).

Exit status: 0 if everything loaded, 1 if anything failed (SLOW alone is not a failure).
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import re
import statistics
import sys
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlencode, urljoin

import httpx

DEFAULT_BASE = "https://skyshift.web.app"
# Featured targets (CLAUDE.md). web/src/data/targets.json wins when it exists.
TARGETS = ["barnard", "epsindib", "wise0855", "rwaur", "rdra", "3i-atlas", "elpis", "euphrosyne"]
TARGETS_JSON = Path(__file__).resolve().parents[1] / "web" / "src" / "data" / "targets.json"

SLOW_STATIC_S = 2.0
SLOW_LIVE_S = 15.0
FIRST_DET = 2  # the site opens on D2 (contracts/api.md, det default)

FRAMES_KEYS = ("clock", "center", "pixel_arcsec", "size", "epochs", "gaia", "known",
               "data_release", "archive_checked")
EPOCH_KEYS = ("id", "kind", "mjd_mid", "mjd_start", "mjd_end", "det", "um_min", "um_max",
              "n_exposures", "flag_frac", "sat_frac", "data_url", "scale", "zero")
OBJECT_KEYS = ("name", "gaia", "known_ucd", "spectrum", "measure")
SPECTRUM_KEYS = ("um", "flux_ujy", "err_ujy", "det", "mjd", "visit", "saturated")
MEASURE_KEYS = ("shift_arcsec", "pa_deg", "dt_yr", "rate_arcsec_yr", "sigma_arcsec",
                "n_ref_stars", "predicted_arcsec")
KIND = {"months": "visit", "hours": "exposure"}


class Fail(Exception):
    def __init__(self, message: str, url: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.url, self.status = url, status


def first_lines(text: str, n: int = 3, width: int = 300) -> str:
    m = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    if m:  # an HTML error page: its title says more than its first lines
        return f"HTML page {m.group(1).strip()!r}"[:width]
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()][:n]
    return " | ".join(lines)[:width]


def fetch(client: httpx.Client, url: str, timeout: float) -> tuple[bytes, str, str]:
    """GET url → (body, content type, final url). Raises Fail on any error or non-200."""
    if url.startswith("data:"):
        head, _, payload = url.partition(",")
        body = base64.b64decode(payload) if head.endswith(";base64") else unquote_to_bytes(payload)
        return body, head[5:].split(";")[0] or "text/plain", url
    try:
        r = client.get(url, timeout=timeout)
    except httpx.TimeoutException:
        raise Fail(f"timeout after {timeout:g} s", url) from None
    except httpx.HTTPError as e:
        raise Fail(f"{type(e).__name__}: {e}", url) from None
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200:
        detail = first_lines(r.text)
        try:
            err = r.json().get("error") or {}
            if isinstance(err, dict) and err.get("message"):
                detail = f"{err.get('code')}: {err.get('message')}"
        except (ValueError, AttributeError):
            pass
        raise Fail(f"HTTP {r.status_code}: {detail}", url, r.status_code)
    return r.content, ctype, str(r.url)


def fetch_json(client: httpx.Client, url: str, timeout: float) -> tuple[dict, str]:
    body, ctype, final = fetch(client, url, timeout)
    if "html" in ctype:
        raise Fail(f"got HTML, not JSON ({len(body)} bytes): missing file served as the page?", url, 200)
    try:
        obj = json.loads(body)
    except ValueError as e:
        raise Fail(f"not JSON: {e}", url, 200) from None
    if not isinstance(obj, dict):
        raise Fail(f"JSON is a {type(obj).__name__}, not an object", url, 200)
    return obj, final


def is_num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check_frames_meta(meta: dict, clock: str | None) -> list[str]:
    """Problems with a frames.json / /api/frames response (contracts/api.md), empty if none."""
    p: list[str] = []
    if meta.get("synthetic"):
        p.append("synthetic fixture response (engine not ready?)")
    missing = [k for k in FRAMES_KEYS if k not in meta]
    if missing:
        p.append(f"missing keys {missing}")
    if meta.get("clock") not in KIND:
        p.append(f"clock {meta.get('clock')!r}")
    elif clock and meta["clock"] != clock:
        p.append(f"clock {meta['clock']!r}, asked {clock!r}")
    c = meta.get("center")
    if not (isinstance(c, dict) and is_num(c.get("ra")) and is_num(c.get("dec"))):
        p.append("center is not {ra, dec}")
    if not (is_num(meta.get("pixel_arcsec")) and meta["pixel_arcsec"] > 0):
        p.append(f"pixel_arcsec {meta.get('pixel_arcsec')!r}")
    size = meta.get("size")
    if not (isinstance(size, list) and len(size) == 2
            and all(isinstance(n, int) and 0 < n <= 300 for n in size)):
        p.append(f"size {size!r} is not [nx, ny] with 1-300 each")
    for k in ("gaia", "known"):
        if k in meta and not isinstance(meta[k], list):
            p.append(f"{k} is not a list")
    epochs = meta.get("epochs")
    if not isinstance(epochs, list):
        p.append("epochs is not a list")
        return p
    if not epochs:
        p.append("no epochs")
    kind = KIND.get(meta.get("clock"))
    for i, e in enumerate(epochs):
        if not isinstance(e, dict):
            p.append(f"epochs[{i}] is not an object")
            break
        miss = [k for k in EPOCH_KEYS if k not in e]
        if miss:
            p.append(f"epochs[{i}] ({e.get('id')}) missing {miss}")
            break
        bad = [k for k in ("mjd_mid", "scale", "zero") if not is_num(e[k])]
        if bad or not isinstance(e["data_url"], str) or e["det"] not in range(1, 7):
            p.append(f"epochs[{i}] ({e.get('id')}) bad {bad or 'det/data_url'}")
            break
        if kind and e["kind"] != kind:
            p.append(f"epochs[{i}] kind {e['kind']!r}, expected {kind!r}")
            break
    else:
        ids = [e["id"] for e in epochs]
        if len(set(ids)) != len(ids):
            p.append("epoch ids are not unique")
        mjds = [e["mjd_mid"] for e in epochs]
        if mjds != sorted(mjds):
            p.append("epochs not sorted by mjd_mid")
    return p


def check_object_meta(obj: dict) -> tuple[list[str], list[str]]:
    """(problems, warnings) for object.json / /api/object."""
    p: list[str] = []
    w: list[str] = []
    if obj.get("synthetic"):
        p.append("synthetic fixture response (engine not ready?)")
    missing = [k for k in OBJECT_KEYS if k not in obj]
    if missing:
        p.append(f"missing keys {missing}")
    spec = obj.get("spectrum")
    if not isinstance(spec, list):
        p.append("spectrum is not a list")
    else:
        if not spec:
            w.append("empty spectrum")
        for i, s in enumerate(spec):
            miss = [k for k in SPECTRUM_KEYS if not isinstance(s, dict) or k not in s]
            if miss:
                p.append(f"spectrum[{i}] missing {miss}")
                break
        else:
            ums = [s["um"] for s in spec]
            if not all(is_num(u) for u in ums) or ums != sorted(ums):
                p.append("spectrum not sorted by um")
    m = obj.get("measure")
    if m is not None:
        miss = [k for k in MEASURE_KEYS if not isinstance(m, dict) or k not in m]
        if miss:
            p.append(f"measure missing {miss}")
    return p, w


def frame_url(e: dict, json_url: str) -> str:
    return e["data_url"] if e["data_url"].startswith("data:") else urljoin(json_url, e["data_url"])


def get_frame(client: httpx.Client, url: str, nbytes: int, timeout: float) -> float:
    """Fetch one frame, check it decodes to nx·ny uint16. Returns the no-data (raw 0) share."""
    body, ctype, _ = fetch(client, url, timeout)
    if "html" in ctype:
        raise Fail(f"got HTML ({len(body)} bytes), not frame data: missing file?", url, 200)
    if len(body) != nbytes:
        raise Fail(f"{len(body)} bytes, expected {nbytes} (2·nx·ny)", url, 200)
    raw = array("H")
    raw.frombytes(body)
    if sys.byteorder == "big":
        raw.byteswap()
    return raw.count(0) / len(raw)


def pick_first(epochs: list[dict]) -> int:
    """Index of the frame the site draws first: the earliest D2 epoch, else the earliest."""
    for i, e in enumerate(epochs):
        if e.get("det") == FIRST_DET:
            return i
    return 0


def describe_epochs(epochs: list[dict], clock: str | None) -> tuple[str, dict[int, int]]:
    per_det: dict[int, int] = {}
    for e in epochs:
        det = e["det"] if isinstance(e.get("det"), int) else 0
        per_det[det] = per_det.get(det, 0) + 1
    if not per_det:
        return "0", per_det
    d = FIRST_DET if FIRST_DET in per_det else min(per_det)
    label = "visits" if clock == "months" else "exp"
    text = f"{per_det[d]} {label} D{d}"
    if len(per_det) > 1:
        text += f" ({len(per_det)} det)"
    return text, dict(sorted(per_det.items()))


def check_frame_set(client: httpx.Client, url: str, clock: str | None, json_timeout: float,
                    frame_timeout: float, workers: int) -> dict:
    """frames.json (or /api/frames) + every frame it points to."""
    out: dict = {"url": url, "ok": False, "clock": clock, "json_s": None, "first_frame_s": None,
                 "all_frames_s": None, "n_frames": 0, "n_frames_ok": 0, "epochs": None,
                 "per_det": {}, "nodata_frames": 0, "problems": [], "error": None, "status": None}
    t0 = time.perf_counter()
    try:
        meta, final = fetch_json(client, url, json_timeout)
    except Fail as f:
        out.update(error=str(f), status=f.status, url=f.url or url)
        return out
    out["json_s"] = round(time.perf_counter() - t0, 3)
    out["status"] = 200
    out["clock"] = meta.get("clock", clock)
    problems = check_frames_meta(meta, clock)
    epochs = meta.get("epochs") if isinstance(meta.get("epochs"), list) else []
    epochs = [e for e in epochs if isinstance(e, dict) and isinstance(e.get("data_url"), str)]
    out["n_frames"] = len(epochs)
    out["epochs"], out["per_det"] = describe_epochs(epochs, out["clock"])
    size = meta.get("size")
    if not epochs or not (isinstance(size, list) and len(size) == 2
                          and all(isinstance(n, int) and n > 0 for n in size)):
        out["problems"] = problems
        out["error"] = problems[0] if problems else "nothing to fetch"
        return out
    nbytes = 2 * int(size[0]) * int(size[1])
    failures: list[Fail] = []
    nodata = 0

    def one(e: dict) -> float | Fail:
        try:
            return get_frame(client, frame_url(e, final), nbytes, frame_timeout)
        except Fail as f:
            return f

    i0 = pick_first(epochs)
    first = one(epochs[i0])
    out["first_frame_s"] = round(time.perf_counter() - t0, 3) if not isinstance(first, Fail) else None
    rest = [e for i, e in enumerate(epochs) if i != i0]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = [first, *pool.map(one, rest)]
    out["all_frames_s"] = round(time.perf_counter() - t0, 3)
    for r in results:
        if isinstance(r, Fail):
            failures.append(r)
        elif r >= 1.0:
            nodata += 1
    out["n_frames_ok"] = len(results) - len(failures)
    out["nodata_frames"] = nodata
    if failures:
        f = failures[0]
        problems.append(f"{len(failures)} frame(s) failed, first: {f} ({f.url})")
        out["status"] = f.status
    out["problems"] = problems
    out["ok"] = not problems
    out["error"] = problems[0] if problems else None
    return out


def check_object(client: httpx.Client, url: str, timeout: float) -> dict:
    out: dict = {"url": url, "ok": False, "s": None, "name": None, "spectrum_n": None,
                 "measure": None, "problems": [], "warnings": [], "error": None, "status": None}
    t0 = time.perf_counter()
    try:
        obj, _ = fetch_json(client, url, timeout)
    except Fail as f:
        out.update(error=str(f), status=f.status, url=f.url or url)
        return out
    out["s"] = round(time.perf_counter() - t0, 3)
    out["status"] = 200
    problems, warnings = check_object_meta(obj)
    spec = obj.get("spectrum")
    out.update(name=obj.get("name"), spectrum_n=len(spec) if isinstance(spec, list) else None,
               measure=isinstance(obj.get("measure"), dict), problems=problems, warnings=warnings,
               ok=not problems, error=problems[0] if problems else None)
    return out


def load_targets(arg: str | None) -> list[str]:
    if arg is not None:
        return [s.strip() for s in arg.split(",") if s.strip()]
    if TARGETS_JSON.exists():
        try:
            data = json.loads(TARGETS_JSON.read_text())
            items = data.get("targets", []) if isinstance(data, dict) else data
            slugs = [t["slug"] for t in items if isinstance(t, dict) and t.get("slug")]
            if slugs:
                return slugs
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    return list(TARGETS)


def random_points(n: int, rng: random.Random) -> list[tuple[float, float]]:
    """Uniform on the sphere: RA uniform, sin(Dec) uniform."""
    return [(round(rng.uniform(0.0, 360.0), 5), round(math.degrees(math.asin(rng.uniform(-1.0, 1.0))), 5))
            for _ in range(n)]


def median_first_frame(rows: list[dict]) -> float | None:
    if not rows:
        return None
    m = statistics.median([r["first_frame_s"] if r["ok"] and r["first_frame_s"] is not None
                           else math.inf for r in rows])
    return round(m, 3) if math.isfinite(m) else None


def fmt_s(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f} s"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"site serving /data (default {DEFAULT_BASE})")
    ap.add_argument("--api-base", help="host serving /api, if not --base (e.g. http://localhost:8000)")
    ap.add_argument("--targets", help="comma-separated slugs (default: web/src/data/targets.json, else the 8 featured)")
    ap.add_argument("--no-targets", action="store_true", help="skip the static /data part")
    ap.add_argument("--all-sets", action="store_true", help="also check every set in /data/<slug>/index.json")
    ap.add_argument("--random", type=int, default=3, metavar="N", help="random coordinates through /api (default 3; 0 skips)")
    ap.add_argument("--seed", type=int, help="seed for the random coordinates (default: new each run, printed)")
    ap.add_argument("--clock", choices=["months", "hours", "both"], default="both", help="clocks for the random coordinates (default both)")
    ap.add_argument("--api-timeout", type=float, default=60.0, help="seconds for /api/frames and /api/object (default 60)")
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds for static files and frame files (default 30)")
    ap.add_argument("--workers", type=int, default=8, help="parallel frame downloads (default 8)")
    ap.add_argument("--json", metavar="PATH", help="write a machine-readable summary here")
    a = ap.parse_args(argv)

    base = a.base.rstrip("/") + "/"
    api_base = (a.api_base or a.base).rstrip("/") + "/"
    seed = a.seed if a.seed is not None else random.SystemRandom().randrange(1_000_000)
    clocks = ["months", "hours"] if a.clock == "both" else [a.clock]
    slugs = [] if a.no_targets else load_targets(a.targets)

    targets: list[dict] = []
    rand_rows: list[dict] = []
    rand_objects: list[dict] = []
    with httpx.Client(follow_redirects=True, headers={"User-Agent": "skyshift-demo-check/1"}) as client:
        for slug in slugs:
            log(f"/data/{slug}/ ...")
            fs = check_frame_set(client, urljoin(base, f"data/{slug}/frames.json"), None,
                                 a.timeout, a.timeout, a.workers)
            ob = check_object(client, urljoin(base, f"data/{slug}/object.json"), a.timeout)
            sets: list[dict] = []
            if a.all_sets:
                try:
                    idx, idx_url = fetch_json(client, urljoin(base, f"data/{slug}/index.json"), a.timeout)
                    for entry in idx.get("frames") or []:
                        r = check_frame_set(client, urljoin(idx_url, entry["file"]), entry.get("clock"),
                                            a.timeout, a.timeout, a.workers)
                        if entry.get("epochs") is not None and entry["epochs"] != r["n_frames"]:
                            r["problems"].append(f"index says {entry['epochs']} epochs, file has {r['n_frames']}")
                            r["ok"], r["error"] = False, r["problems"][0]
                        sets.append({"file": entry["file"], **{k: r[k] for k in (
                            "ok", "clock", "first_frame_s", "all_frames_s", "n_frames", "n_frames_ok",
                            "epochs", "error", "url", "status")}})
                except Fail as f:
                    sets.append({"file": "index.json", "ok": False, "error": str(f), "url": f.url, "status": f.status})
            errors = [e for e in (fs["error"], ob["error"]) if e]
            errors += [f"{s['file']}: {s['error']}" for s in sets if not s["ok"]]
            warnings = list(ob["warnings"])
            if fs["nodata_frames"]:
                warnings.append(f"{fs['nodata_frames']} frame(s) all no-data")
            if fs["first_frame_s"] is not None and fs["first_frame_s"] > SLOW_STATIC_S:
                warnings.append(f"SLOW: first frame over {SLOW_STATIC_S:g} s")
            targets.append({
                "slug": slug, "name": ob["name"], "source": "/data", "ok": fs["ok"] and ob["ok"] and all(s["ok"] for s in sets),
                "clock": fs["clock"], "first_frame_s": fs["first_frame_s"], "all_frames_s": fs["all_frames_s"],
                "n_frames": fs["n_frames"], "n_frames_ok": fs["n_frames_ok"], "epochs": fs["epochs"],
                "per_det": fs["per_det"], "frames_ok": fs["ok"], "card_ok": ob["ok"],
                "spectrum_n": ob["spectrum_n"], "measure": ob["measure"], "sets": sets if a.all_sets else None,
                "warnings": warnings, "error": "; ".join(errors) or None,
                "failures": [{"url": x["url"], "status": x["status"], "error": x["error"]}
                             for x in (fs, ob, *sets) if not x["ok"]],
            })

        if a.random > 0:
            log(f"random coordinates: {a.random}, seed {seed}, clocks {'+'.join(clocks)}, via {api_base}api/")
        for ra, dec in random_points(max(0, a.random), random.Random(seed)):
            for clock in clocks:
                q = urlencode({"ra": ra, "dec": dec, "clock": clock})
                log(f"/api/frames?{q} ...")
                r = check_frame_set(client, urljoin(api_base, f"api/frames?{q}"), clock,
                                    a.api_timeout, a.timeout, a.workers)
                warnings = []
                if r["first_frame_s"] is not None and r["first_frame_s"] > SLOW_LIVE_S:
                    warnings.append(f"SLOW: first frame over {SLOW_LIVE_S:g} s")
                if r["nodata_frames"]:
                    warnings.append(f"{r['nodata_frames']} frame(s) all no-data")
                rand_rows.append({"ra": ra, "dec": dec, "clock": clock, "ok": r["ok"],
                                  "first_frame_s": r["first_frame_s"], "json_s": r["json_s"],
                                  "all_frames_s": r["all_frames_s"], "n_frames": r["n_frames"],
                                  "n_frames_ok": r["n_frames_ok"], "epochs": r["epochs"],
                                  "warnings": warnings, "error": r["error"], "url": r["url"], "status": r["status"]})
            q = urlencode({"ra": ra, "dec": dec})
            log(f"/api/object?{q} ...")
            ob = check_object(client, urljoin(api_base, f"api/object?{q}"), a.api_timeout)
            rand_objects.append({"ra": ra, "dec": dec, "ok": ob["ok"], "s": ob["s"], "name": ob["name"],
                                 "spectrum_n": ob["spectrum_n"], "measure": ob["measure"],
                                 "warnings": ob["warnings"], "error": ob["error"], "url": ob["url"],
                                 "status": ob["status"]})

    median = median_first_frame(rand_rows)
    by_clock = {c: median_first_frame([r for r in rand_rows if r["clock"] == c]) for c in clocks} if rand_rows else {}
    ok = (all(t["ok"] for t in targets) and all(r["ok"] for r in rand_rows)
          and all(o["ok"] for o in rand_objects))

    # ------------------------------------------------------------ report
    print(f"SkyShift demo check · /data from {base} · /api from {api_base}")
    if targets:
        print(f"\n{'target':<11} {'src':<6} {'clock':<6} {'frames':>7} {'card':<4} {'spectrum':>8} "
              f"{'epochs':<22} {'1st frame':>9} {'all':>8}  notes")
        for t in targets:
            frames = f"{t['n_frames_ok']}/{t['n_frames']}" if t["n_frames"] else "FAIL"
            spec = "-" if t["spectrum_n"] is None else f"{t['spectrum_n']} pts"
            notes = ("OK" if t["ok"] else "FAIL") + "".join(f"; {w}" for w in t["warnings"])
            print(f"{t['slug']:<11} {'/data':<6} {t['clock'] or '-':<6} {frames:>7} "
                  f"{'ok' if t['card_ok'] else 'FAIL':<4} {spec:>8} {t['epochs'] or '-':<22} "
                  f"{fmt_s(t['first_frame_s']):>9} {fmt_s(t['all_frames_s']):>8}  {notes}")
            for s in t["sets"] or []:
                print(f"{'':<11} {'':<6} {s.get('clock') or '-':<6} "
                      f"{(str(s.get('n_frames_ok')) + '/' + str(s.get('n_frames'))) if s.get('n_frames') else 'FAIL':>7} "
                      f"{'':<4} {'':>8} {s.get('epochs') or s['file']:<22} {fmt_s(s.get('first_frame_s')):>9} "
                      f"{fmt_s(s.get('all_frames_s')):>8}  {s['file']} {'OK' if s['ok'] else 'FAIL'}")
    if rand_rows or rand_objects:
        print(f"\n{'ra':>10} {'dec':>9} {'src':<6} {'clock':<6} {'frames':>7} {'epochs':<22} "
              f"{'1st frame':>9} {'all':>8}  notes   (seed {seed})")
        for r in rand_rows:
            frames = f"{r['n_frames_ok']}/{r['n_frames']}" if r["n_frames"] else "FAIL"
            notes = ("OK" if r["ok"] else "FAIL") + "".join(f"; {w}" for w in r["warnings"])
            print(f"{r['ra']:>10.4f} {r['dec']:>9.4f} {'/api':<6} {r['clock']:<6} {frames:>7} "
                  f"{r['epochs'] or '-':<22} {fmt_s(r['first_frame_s']):>9} {fmt_s(r['all_frames_s']):>8}  {notes}")
        for o in rand_objects:
            card = (f"{o['name'] or 'no name'}, {o['spectrum_n']} pts" if o["spectrum_n"] is not None
                    else "-")
            print(f"{o['ra']:>10.4f} {o['dec']:>9.4f} {'/api':<6} {'object':<6} {'':>7} {card[:22]:<22} "
                  f"{fmt_s(o['s']):>9} {'':>8}  {'OK' if o['ok'] else 'FAIL'}"
                  + "".join(f"; {w}" for w in o["warnings"]))
        n_ok = sum(r["ok"] for r in rand_rows)
        print(f"\nmedian time to first frame over {len(rand_rows)} random /api/frames requests "
              f"({n_ok} ok): {fmt_s(median)}"
              + "".join(f" · {c} {fmt_s(m)}" for c, m in by_clock.items()))

    failures = [(f"/data/{t['slug']}", x) for t in targets for x in t["failures"]]
    failures += [(f"/api/frames {r['clock']} {r['ra']},{r['dec']}", r) for r in rand_rows if not r["ok"]]
    failures += [(f"/api/object {o['ra']},{o['dec']}", o) for o in rand_objects if not o["ok"]]
    if failures:
        print("\nFailures:")
        for what, x in failures:
            err = str(x["error"])
            if not err.startswith("HTTP "):
                err = f"HTTP {x['status']}: {err}" if x["status"] else f"no response: {err}"
            print(f"- {what}: {x['url']}\n  {err}")
    print(f"\n{'ALL OK' if ok else 'FAILED'}")

    if a.json:
        summary = {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "base": base.rstrip("/"), "api_base": api_base.rstrip("/"), "ok": ok,
            "seed": seed if a.random > 0 else None,
            "targets": targets, "random": rand_rows, "random_objects": rand_objects,
            "n_random": len(rand_rows), "n_random_ok": sum(r["ok"] for r in rand_rows),
            "median_first_frame_s": median, "median_first_frame_s_by_clock": by_clock,
            "median_rule": "median over the random /api/frames requests of frames.json + first frame "
                           "time; a failed request counts as slower than any success (null if half or more failed)",
            "thresholds_s": {"static": SLOW_STATIC_S, "live": SLOW_LIVE_S},
        }
        Path(a.json).write_text(json.dumps(summary, indent=1) + "\n")
        log(f"wrote {a.json}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
