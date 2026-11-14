"""The site's /data/distant.json (contracts/api.md v3.6): the distant-planet search, from the pipeline's `orphans` step.

Inputs, per zone (the first of --zones that has a run):
  run        <results>/<zone>/distant.json (the step's output, fetched by build_validation --fetch-results once the
             bucket has it), else data/distant_<zone>.json (a committed copy of a local run of the step)
  injection  data/distant_injection_<zone>.json (distant_injection.py); optional, `injection` is null without it

  uv run python distant.py                       # hunt; writes ../../web/public/data/distant.json
  uv run python distant.py --run PATH --injection PATH --out PATH

build_validation.py calls write_site_file() too, so a full rebuild refreshes it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

from _common import CACHE, DATA, write_json

HERE = Path(__file__).resolve().parent
SITE_DATA = HERE.parent.parent / "web" / "public" / "data"
OUT = SITE_DATA / "distant.json"
BEYOND_CHANCE_P = 0.01  # contracts/api.md: beyond_chance when poisson_p < this
DESIGN_AU = [70.0, 1000.0]  # the distances the step is built for (pipeline config ORPHANS_TRACK_AU)
PAIR_KEYS = ("source_id_a", "source_id_b", "visit_a", "visit_b", "ra_a", "dec_a", "mjd_a", "ra_b", "dec_b", "mjd_b",
             "sep_arcsec", "dlon_arcsec", "dlat_arcsec", "distance_au")
PAIR_MORE = ("flux_ratio", "n_common_det", "n_dots_a", "n_dots_b", "snr_a", "snr_b", "p", "known_a", "known_b")


def _num(x, n=3):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, n) if math.isfinite(v) else None


def run_path(results: Path | None, zone: str) -> Path | None:
    """The step's distant.json for `zone`: the fetched bucket copy first, else the committed local run."""
    for p in ((results / zone / "distant.json") if results else None, DATA / f"distant_{zone}.json"):
        if p is not None and p.exists():
            return p
    return None


def control_block(c: dict) -> dict:
    """The step's controls with their spread; poisson_p as the step computed it (Poisson, mean (sum + 1) / n)."""
    counts = [int(x) for x in (c.get("passing_per_control") or [])]
    n = len(counts)
    mean = sum(counts) / n if n else None
    sd = math.sqrt(sum((x - mean) ** 2 for x in counts) / (n - 1)) if n > 1 else None
    p = c.get("poisson_p")
    return {"n": n, "passing_per_control": counts,
            "expected_pairs": _num(c.get("expected_chance_pairs", mean)), "spread_pairs": _num(sd),
            "poisson_p": _num(p, 4), "beyond_chance": bool(p is not None and p < BEYOND_CHANCE_P),
            "method": c.get("method") or "the later visit's orphans shifted cyclically within their box"}


def injection_block(inj: dict | None) -> dict | None:
    if not inj or not inj.get("distances"):
        return None
    rows = [{"distance_au": _num(d["distance_au"], 1), "injected": int(d["injected"]), "recovered": int(d["recovered"]),
             "recovered_frac": _num(d.get("recovered_frac")),
             "distance_fit_median_au": _num(d.get("distance_fit_median_au"), 1)} for d in inj["distances"]]
    return {"method": inj.get("method", ""), "distances": rows,
            "recovered_frac": _num(inj.get("recovered_frac")),
            "min_cell_recovered_frac": _num(inj.get("min_cell_recovered_frac")),
            "distance_err_median_frac": _num(inj.get("distance_err_median_frac"))}


def build(run: dict, injection: dict | None, generated: str, data_release: str) -> dict:
    """The contract's distant.json from the step's distant.json and the injection summary."""
    s, c = run.get("searched") or {}, run.get("counts") or {}
    pairs = []
    for p in run.get("pairs") or []:
        lo, hi = (p.get("distance_range_au") or [None, None])[:2]
        pairs.append({**{k: p.get(k) for k in PAIR_KEYS}, "distance_lo_au": lo, "distance_hi_au": hi,
                      **{k: p.get(k) for k in PAIR_MORE}})
    pairs.sort(key=lambda p: -(p.get("p") or 0.0))
    n_pairs = int(c.get("pairs") or 0)
    box = (injection or {}).get("zone_box")
    return {
        "generated": generated, "data_release": data_release, "zone": run.get("zone"), "zone_box": box,
        "run_created": run.get("created"),
        "searched": {"distance_au": list(DESIGN_AU), "sep_arcsec": s.get("sep_arcsec"),
                     "rate_arcsec_h": s.get("rate_arcsec_h"), "pair_min_p": s.get("pair_min_p")},
        "counts": {"single_visit_sources": c.get("single_visit_sources"), "uncatalogued": c.get("unmatched"),
                   "covered": c.get("unmatched_covered"), "orphans": c.get("orphans"),
                   "dropped": c.get("reasons") or {}, "drift_ok": c.get("drift_ok"), "pairs_tested": c.get("pairs_tested"),
                   "pairs": n_pairs, "known_orphans": c.get("known_orphans", 0), "open_orphans": c.get("open_orphans")},
        "pairs": pairs,
        "control": control_block(run.get("control") or {}),
        "injection": injection_block(injection),
        "limits": {"depth_ab_snr5": s.get("depth_ab_snr5") or {}, "reach_au": s.get("reach_au_at_d2_depth") or {},
                   "notes": list(run.get("limits") or [])},
    }


def valid_run(d: dict | None) -> bool:
    """The step's distant.json, not skipped and with controls (else there is no result to show)."""
    return bool(d) and isinstance(d.get("counts"), dict) and isinstance(d.get("control"), dict) and not d.get("skipped") \
        and not d.get("synthetic")


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_site_file(results: Path | None, zones: list[str], out: Path, data_release: str,
                    run: Path | None = None, injection: Path | None = None, generated: str | None = None) -> dict | None:
    """Build and write the site file from the first zone with a run; None (nothing written) without one."""
    for z in zones:
        rp = run or run_path(results, z)
        d = json.loads(rp.read_text()) if rp and rp.exists() else None
        if not valid_run(d):
            continue
        ip = injection or DATA / f"distant_injection_{d.get('zone') or z}.json"
        inj = json.loads(ip.read_text()) if ip.exists() else None
        site = build(d, inj, generated or now(), data_release)
        write_json(out, site)
        return site
    return None


def main() -> None:
    from build_validation import DATA_RELEASE, ZONES, as_zones
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=CACHE / "results")
    ap.add_argument("--zones", default=",".join(ZONES))
    ap.add_argument("--run", type=Path, help="the step's distant.json (default: see above)")
    ap.add_argument("--injection", type=Path, help="default data/distant_injection_<zone>.json")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    site = write_site_file(a.results, as_zones(a.zones), a.out, DATA_RELEASE, a.run, a.injection)
    if site is None:
        raise SystemExit("no distant.json run found (data/distant_<zone>.json or <results>/<zone>/distant.json)")
    c, k = site["counts"], site["control"]
    print(f"-> {a.out}: {site['zone']}: {c['orphans']} orphans, {c['pairs_tested']} pairs tested, {c['pairs']} passing; "
          f"chance {k['expected_pairs']} +- {k['spread_pairs']} (Poisson p {k['poisson_p']})")


if __name__ == "__main__":
    main()
