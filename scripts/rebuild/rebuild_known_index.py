#!/usr/bin/env python3
"""Rebuild the API server's known-object index (skyshift_core.known_index) with the full comet list.

Run from pipeline/ (needs pandas, kete, skyshift_core, skyshift_pipeline):
    cd pipeline
    uv run python ../scripts/rebuild/rebuild_known_index.py --dry-run
    uv run python ../scripts/rebuild/rebuild_known_index.py --verify

What this does (local only; NEVER reads or writes gs://nasa-glazing-data or runs deploy.sh):
1. Reads the newest local MPC snapshots under --root (~/skyshift-data):
   - cache/mpc/orbits/mpcorb_<date>.parquet (~1.58M asteroids)
   - cache/mpc/orbits/cometels_<date>.parquet, completed with cache/known_check/allcometels_<date>.parquet
     (or cache/mpc/orbits/allcometels_<date>.parquet) via skyshift_pipeline.known_mpc.merge_comets -> all comets (2,215).
2. Reuses the measured asteroid margins/cuts (err_deg, always, V < 21.5) from a matching known_index_<date>.npz built
   from the same mpcorb snapshot when present and collision-free (replace_comets; <1 s), else runs full build_index
   (~10-12 min). Use --full-sweep to force a fresh asteroid sweep regardless.
3. Writes <root>/cache/mpc/known_index/known_index_<date>.npz atomically, then optionally verifies queries including
   306P/LINEAR locally (--verify). Prints exact upload/deploy commands and cloud cost estimates for explicit approval.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from skyshift_core import known_index as KI

BUCKET = "gs://nasa-glazing-data"
ORBIT_SUBDIR = Path("cache/mpc/orbits")
INDEX_SUBDIR = Path("cache/mpc/known_index")
ALLCOMET_DIRS = (Path("cache/mpc/orbits"), Path("cache/known_check"))
_DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})\.(?:parquet|npz)$")


def _newest(folders: list[Path], prefix: str, suffix: str) -> Path | None:
    hits: list[tuple[str, Path]] = []
    for d in folders:
        if not d.is_dir():
            continue
        for p in d.glob(f"{prefix}_*.{suffix}"):
            if p.name.endswith(".tmp.npz"):
                continue
            m = _DATE_RE.search(p.name)
            hits.append((m.group(1) if m else p.name, p))
    return max(hits, key=lambda kv: (kv[0], kv[1].stat().st_mtime))[1] if hits else None


def _file_date(p: Path | None) -> str | None:
    if p is None:
        return None
    m = _DATE_RE.search(p.name)
    return m.group(1) if m else None


def _load_merged_comets(cometels: Path, allcometels: Path | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    from skyshift_pipeline import known_mpc as KM

    cur = pd.read_parquet(cometels)
    if allcometels is None:
        return cur, cur
    every = pd.read_parquet(allcometels)
    return cur, KM.merge_comets(cur, every)


def _matching_base(root: Path, mpcorb: Path) -> Path | None:
    idx_dir = root / INDEX_SUBDIR
    if not idx_dir.is_dir():
        return None
    candidates = sorted(
        (p for p in idx_dir.glob("known_index_*.npz") if not p.name.endswith(".tmp.npz")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in candidates:
        try:
            idx = KI.load_index(str(p))
            if str(idx.meta["snapshot"]).startswith(f"{mpcorb.name} +"):
                return p
        except Exception:  # noqa: BLE001
            continue
    return None


def _estimate_cost(size_bytes: int) -> dict[str, Any]:
    gb = size_bytes / 1e9
    return {
        "local_build_usd": 0.0,
        "gcs_upload_class_a_usd": 0.000005,
        "gcs_storage_per_month_usd": round(gb * 0.020, 6),
        "gcs_egress_per_cold_instance_usd": round(gb * 0.13, 6),
        "gcs_egress_8_instances_max_usd": round(8 * gb * 0.13, 5),
        "cloud_build_deploy_note": "~$0.01-$0.05 Cloud Build + Artifact Registry; $0/day Cloud Run idle (min-instances=0)",
    }


def plan(
    root: Path,
    *,
    mpcorb: Path | None = None,
    cometels: Path | None = None,
    allcometels: Path | None = None,
    base_index: Path | None = None,
    out: Path | None = None,
    date_str: str | None = None,
    full_sweep: bool = False,
) -> dict[str, Any]:
    mpcorb = mpcorb or _newest([root / ORBIT_SUBDIR], "mpcorb", "parquet")
    cometels = cometels or _newest([root / ORBIT_SUBDIR], "cometels", "parquet")
    if mpcorb is None or cometels is None:
        raise FileNotFoundError(f"missing mpcorb or cometels parquet under {root / ORBIT_SUBDIR}")
    if allcometels is None:
        allcometels = _newest([root / d for d in ALLCOMET_DIRS], "allcometels", "parquet")
    if not full_sweep and base_index is None:
        base_index = _matching_base(root, mpcorb)

    ast = pd.read_parquet(mpcorb)
    cur_com, merged_com = _load_merged_comets(cometels, allcometels)
    n_cur_com, n_merged_com = len(cur_com), len(merged_com)
    base_meta = None
    reuse_ok = False
    if base_index is not None and not full_sweep:
        idx = KI.load_index(str(base_index))
        base_meta = idx.meta
        # confirm asteroid names don't collide with any merged comet name
        com_arr = KI._table_arrays(merged_com, True)
        com_names = KI._disambiguate_names(com_arr["name"], com_arr["desig"])
        ast_names = KI._table_arrays(ast, False)["name"].astype(str)
        reuse_ok = not (set(com_names.astype(str)) & set(ast_names))

    dates = [d for d in (_file_date(mpcorb), _file_date(cometels), _file_date(allcometels)) if d]
    out_date = date_str or (max(dates) if dates else dt.date.today().isoformat())
    out_path = out or (root / INDEX_SUBDIR / f"known_index_{out_date}.npz")
    if base_index is not None and out_path.resolve() == base_index.resolve() and not date_str and not out:
        out_path = root / INDEX_SUBDIR / f"known_index_{dt.date.today().isoformat()}.npz"

    reads = [mpcorb, cometels] + ([allcometels] if allcometels else []) + ([base_index] if reuse_ok and base_index else [])
    read_entries = [{"path": str(p), "bytes": int(p.stat().st_size), "mb": round(p.stat().st_size / 1e6, 3), "location": "local"}
                    for p in reads]
    base_bytes = int(base_index.stat().st_size) if base_index is not None else 41_000_000
    est_bytes = base_bytes + max(0, n_merged_com - (base_meta["n_comets"] if base_meta else n_cur_com)) * 120
    key = f"mpc/known_index/{out_path.name}"
    return {
        "mode": "reuse_asteroids + replace_comets" if reuse_ok else "full_sweep (build_index)",
        "reads": read_entries,
        "local_read_mb": round(sum(r["bytes"] for r in read_entries) / 1e6, 3),
        "cloud_read_bytes": 0,
        "write_local": {"path": str(out_path), "estimated_bytes": est_bytes, "estimated_mb": round(est_bytes / 1e6, 2),
                        "key": key},
        "counts": {
            "mpcorb_asteroids": int(len(ast)),
            "kept_asteroids": int(base_meta["n_asteroids"]) if base_meta else None,
            "comets_before": int(base_meta["n_comets"]) if base_meta else int(n_cur_com),
            "comets_after": int(n_merged_com),
            "comets_added": int(n_merged_com) - (int(base_meta["n_comets"]) if base_meta else int(n_cur_com)),
            "has_306P_before": bool("306P/LINEAR" in set(cur_com["desig"].astype(str).str.strip())),
            "has_306P_after": bool("306P/LINEAR" in set(merged_com["desig"].astype(str).str.strip())),
        },
        "inputs": {
            "mpcorb": str(mpcorb),
            "cometels": str(cometels),
            "allcometels": str(allcometels) if allcometels else None,
            "base_index": str(base_index) if reuse_ok and base_index else None,
        },
        "cost_usd": _estimate_cost(est_bytes),
        "gated_cloud_commands": {
            "upload_index": f"gcloud storage cp --no-clobber {out_path} {BUCKET}/cache/{key}",
            "upload_allcometels": (f"gcloud storage cp --no-clobber {allcometels} {BUCKET}/cache/mpc/orbits/"
                                   if allcometels else None),
            "deploy_cloud_run": f"SKYSHIFT_KNOWN_INDEX={key} infra/run/deploy.sh",
        },
    }


def run_build(info: dict[str, Any]) -> dict[str, Any]:
    inp = info["inputs"]
    ast = pd.read_parquet(inp["mpcorb"])
    _, com = _load_merged_comets(Path(inp["cometels"]), Path(inp["allcometels"]) if inp["allcometels"] else None)
    snap_parts = [Path(inp["mpcorb"]).name, Path(inp["cometels"]).name]
    if inp["allcometels"]:
        snap_parts.append(Path(inp["allcometels"]).name)
    snap = " + ".join(snap_parts)

    if inp["base_index"] is not None:
        base = KI.load_index(inp["base_index"])
        data = KI.replace_comets(base, com, snapshot=snap, asteroids=ast)
    else:
        data = KI.build_index(ast, com, snapshot=snap)

    out_path = Path(info["write_local"]["path"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + ".tmp.npz")
    KI.save_index(str(tmp), data)
    tmp.replace(out_path)
    actual_bytes = int(out_path.stat().st_size)
    loaded = KI.load_index(str(out_path))
    return {
        "out": str(out_path),
        "key": info["write_local"]["key"],
        "bytes": actual_bytes,
        "mb": round(actual_bytes / 1e6, 2),
        "meta": loaded.meta,
        "cost_usd": _estimate_cost(actual_bytes),
    }


def verify_index(npz_path: Path) -> dict[str, Any]:
    kete = KI._kete()
    idx = KI.load_index(str(npz_path))
    obs = KI.observer_state(kete, 61000.0)
    # Verify representative comets (both cometels and allcometels-only) and asteroids resolve & propagate
    checks: dict[str, Any] = {}
    for name in ("Ceres", "306P/LINEAR", "3I/ATLAS", "2P/Encke"):
        rows = [k for k in range(len(idx)) if idx.name(k) == name]
        assert rows, f"{name!r} missing from {npz_path}"
        st = kete.propagate_n_body(KI.kete_states(kete, idx, np.array([rows[0]])), obs.jd, suppress_errors=True)
        seen = kete.fov_state_check([s for s in st if s.is_finite], [kete.OmniDirectionalFOV(obs)])
        ra, dec = np.asarray(seen[0].ra_dec_with_rates, float).reshape(-1, 4)[0, :2]
        cone = KI.known_near(float(ra) % 360.0, float(dec), 0.1, [61000.0], index=idx)
        assert any(c["name"] == name for c in cone), f"{name!r} not found by known_near at MJD 61000.0"
        checks[name] = {"row": rows[0], "ra": round(float(ra) % 360.0, 5), "dec": round(float(dec), 5), "cone_hits": len(cone)}
    return {"meta": idx.meta, "checks": checks}


def _print_plan(info: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(info, indent=2))
        return
    c = info["counts"]
    w = info["write_local"]
    cost = info["cost_usd"]
    print(f"Mode: {info['mode']}")
    print("Reads (local only; 0 bytes from gs://):")
    for r in info["reads"]:
        print(f"  - {r['path']} ({r['mb']:.3f} MB)")
    print(f"Writes (local only): {w['path']} (~{w['estimated_mb']:.2f} MB est.)")
    print(f"Objects: asteroids kept={c['kept_asteroids']} (of {c['mpcorb_asteroids']:,}), "
          f"comets={c['comets_before']} -> {c['comets_after']} (+{c['comets_added']}), "
          f"306P={c['has_306P_before']} -> {c['has_306P_after']}")
    print(f"Local cost: ${cost['local_build_usd']:.2f}")
    print("Gated cloud actions (NOT run; require explicit approval):")
    print(f"  1. {info['gated_cloud_commands']['upload_index']}")
    if info["gated_cloud_commands"]["upload_allcometels"]:
        print(f"  2. {info['gated_cloud_commands']['upload_allcometels']}")
    print(f"  3. {info['gated_cloud_commands']['deploy_cloud_run']}")
    print(f"Estimated cloud cost if uploaded/deployed: upload write ~${cost['gcs_upload_class_a_usd']:.6f}, "
          f"storage ~${cost['gcs_storage_per_month_usd']:.4f}/mo, "
          f"cold-start read ~${cost['gcs_egress_per_cold_instance_usd']:.4f}/instance "
          f"(max 8 instances ~${cost['gcs_egress_8_instances_max_usd']:.4f}); {cost['cloud_build_deploy_note']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild server known_index .npz with full MPC comet list")
    ap.add_argument("--root", type=Path, default=Path.home() / "skyshift-data")
    ap.add_argument("--mpcorb", type=Path, default=None)
    ap.add_argument("--cometels", type=Path, default=None)
    ap.add_argument("--allcometels", type=Path, default=None)
    ap.add_argument("--base-index", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD tag for output known_index_<date>.npz")
    ap.add_argument("--full-sweep", action="store_true", help="Re-propagate all asteroids instead of reusing matching base index")
    ap.add_argument("--dry-run", action="store_true", help="Print reads, writes, size and cost without building")
    ap.add_argument("--verify", action="store_true", help="Load built .npz and verify known_near finds 306P/LINEAR")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    info = plan(
        a.root, mpcorb=a.mpcorb, cometels=a.cometels, allcometels=a.allcometels,
        base_index=a.base_index, out=a.out, date_str=a.date, full_sweep=a.full_sweep,
    )
    if a.dry_run:
        _print_plan(info, as_json=a.json)
        return 0
    built = run_build(info)
    if a.verify:
        built["verify"] = verify_index(Path(built["out"]))
    if a.json:
        print(json.dumps({"plan": info, "built": built}, indent=2))
    else:
        _print_plan(info, as_json=False)
        print(f"Built: {built['out']} ({built['bytes']:,} B / {built['mb']:.2f} MB), meta={built['meta']}")
        if "verify" in built:
            print("Verified:", built["verify"]["checks"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
