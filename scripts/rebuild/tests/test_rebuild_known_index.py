"""Tests for scripts/rebuild/rebuild_known_index.py (offline, synthetic snapshots)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from skyshift_core import known_index as KI

pytest.importorskip("skyshift_pipeline")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rebuild_known_index as RKI  # noqa: E402
from skyshift_pipeline import known_mpc as KM  # noqa: E402


def _comet_row(name: str, num: int | None = None, peri=(2025, 8, 1.647), epoch=(2025, 11, 21), q=1.272986) -> dict:
    r = {"Orbit_type": "P" if num else "C", "Year_of_perihelion": peri[0], "Month_of_perihelion": peri[1],
         "Day_of_perihelion": peri[2], "Perihelion_dist": q, "e": 0.59251, "Peri": 0.9146, "Node": 341.339, "i": 8.3017,
         "Designation_and_name": name, "H": 19.0, "G": 4.0}
    if num:
        r["Comet_num"] = num
    if epoch:
        r["Epoch_year"], r["Epoch_month"], r["Epoch_day"] = epoch
    return r


def test_dry_run_and_build_with_full_comets(tmp_path):
    pytest.importorskip("kete")
    orbits = tmp_path / "cache" / "mpc" / "orbits"
    kchk = tmp_path / "cache" / "known_check"
    idx_dir = tmp_path / "cache" / "mpc" / "known_index"
    orbits.mkdir(parents=True)
    kchk.mkdir(parents=True)
    idx_dir.mkdir(parents=True)

    ast = pd.DataFrame({
        "desig": ["1"], "name": ["Ceres"], "h_mag": [3.34], "g_phase": [0.15],
        "peri_dist": [2.5451594], "ecc": [0.0796923], "incl": [10.58803],
        "lon_node": [80.24863], "peri_arg": [73.2942], "peri_time": [2461599.84154],
        "epoch": [2461200.5],
    })
    cur = KM.comet_table([
        _comet_row("2P/Encke", 2, epoch=(2026, 11, 15)),
        _comet_row("3I/ATLAS", peri=(2025, 10, 29.5), epoch=(2026, 11, 15), q=1.35),
    ])
    every = KM.comet_table([
        _comet_row("2P/Encke", 2, epoch=(2025, 11, 21), q=0.33),
        _comet_row("306P/LINEAR", 306),
    ])
    mp = orbits / "mpcorb_2026-11-14.parquet"
    cp = orbits / "cometels_2026-11-14.parquet"
    ap = kchk / "allcometels_2026-11-16.parquet"
    ast.to_parquet(mp)
    cur.to_parquet(cp)
    every.to_parquet(ap)

    base = KI.build_index(ast, cur, snapshot=f"{mp.name} + {cp.name}", mjd_lo=60736.0, mjd_hi=61465.0,
                          step_days=30.0, neo_step_days=10.0, log=lambda *_: None)
    KI.save_index(str(idx_dir / "known_index_2026-11-14.npz"), base)

    info = RKI.plan(tmp_path)
    assert info["mode"] == "reuse_asteroids + replace_comets"
    assert info["cloud_read_bytes"] == 0
    assert info["counts"]["comets_before"] == 2 and info["counts"]["comets_after"] == 3
    assert info["counts"]["has_306P_before"] is False and info["counts"]["has_306P_after"] is True
    assert info["write_local"]["key"] == "mpc/known_index/known_index_2026-11-16.npz"
    # No env override: the next step is known.py's default key, then a plain deploy.sh.
    assert "SKYSHIFT_KNOWN_INDEX" not in info["gated_cloud_commands"]["deploy_cloud_run"]
    assert "known_index_2026-11-16.npz" in info["code_steps"]["set_default_key"]
    assert "--known-only" in info["code_steps"]["refresh_static_known"]

    assert RKI.main(["--root", str(tmp_path), "--dry-run", "--json"]) == 0
    assert not (idx_dir / "known_index_2026-11-16.npz").exists()  # dry-run writes nothing

    built = RKI.run_build(info)
    out = Path(built["out"])
    assert out.exists() and built["meta"]["n_asteroids"] == 1 and built["meta"]["n_comets"] == 3
    v = RKI.verify_index(out)
    assert set(v["checks"]) == {"Ceres", "306P/LINEAR", "3I/ATLAS", "2P/Encke"}
