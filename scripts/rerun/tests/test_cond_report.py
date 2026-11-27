"""scripts/rerun/cond_report.py on a tiny fixture: two photos' conditions files written by cond.table_bytes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("skyshift_pipeline")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cond_report as CR  # noqa: E402
from skyshift_pipeline import cond as CD  # noqa: E402


def _write(d: Path, photo: str, det: int, noise: list, gaia: list, dots: list, neg5: list, neg=True):
    import pyarrow as pa
    n = len(noise)
    cols = {c: np.zeros(n, t) for c, t in CD.COLUMNS.items()}
    cols.update(tile=np.arange(n, dtype=np.int16), ra=np.float32(10.0) + np.arange(n, dtype=np.float32) * 0.2,
                dec=np.zeros(n, np.float32), n_px=np.full(n, 10000, np.uint16), n_good=np.full(n, 10000, np.uint16),
                noise_mjysr=np.array(noise, np.float16), thresh_ujy=np.array(noise, np.float32) * 5000,
                n_gaia=np.array(gaia, np.uint16), n_dots=np.array(dots, np.uint16),
                n_dots_flagged=np.array(dots, np.uint16) // 2, n_neg_5=np.array(neg5, np.uint16),
                n_neg_4=np.array(neg5, np.uint16) * 3)
    t = pa.table({c: cols[c] for c in CD.COLUMNS}).replace_schema_metadata(
        {CD.META_KEY: json.dumps({"detector": det, "neg": {"snr": [4, 4.5, 5]} if neg else None})})
    (d / f"{photo}.parquet").write_bytes(CD.table_bytes(t))


def test_cond_report_groups_rates_and_gaia(tmp_path):
    d = tmp_path / "root" / CR.default_folder()
    d.mkdir(parents=True)
    # tile area: 10,000 px of 6.15″ = 0.0292 deg²; Gaia 10 -> 342 per deg² (sparse), 70 -> 2,394 (crowded)
    _write(d, "A_1D2", 2, [0.01, 0.02, 0.03], [10, 10, 70], [5, 5, 50], [1, 0, 2])
    _write(d, "B_1D2", 2, [0.01, 0.02, 0.03], [10, 10, 70], [7, 3, 30], [0, 1, 0])
    stars = pd.DataFrame({"photo": ["A_1D2", "A_1D2", "B_1D2", "B_1D2", "C_1D2"], "ra": [10.01, 10.39, 10.0, 10.41, 10.0],
                          "dec": [0, 0, 0, 0, 0], "g_mag": [15.0, 18.5, 15.5, 18.2, 15.0],
                          "after": [True, False, True, True, True]})
    sp = tmp_path / "stars.parquet"
    stars.to_parquet(sp)
    out = CR.main(["--root", str(tmp_path / "root"), "--stars", str(sp), "--out", str(tmp_path / "r.json")])
    assert out["photos"] == 2 and out["tiles"] == 6 and out["stars_matched"] == 4  # C_1D2 has no conditions file
    rep = pd.DataFrame(out["table"]).set_index(["noise_bin", "density_bin"])
    area = 10000 * (6.15 / 3600) ** 2
    assert rep.loc[("n1", "sparse"), "photos"] == 2 and rep.loc[("n1", "sparse"), "tiles"] == 2
    assert rep.loc[("n1", "sparse"), "dots_deg2"] == pytest.approx(12 / (2 * area), abs=0.1)
    assert rep.loc[("n3", "crowded"), "flagged_deg2"] == pytest.approx(40 / (2 * area), abs=0.1)
    assert rep.loc[("n3", "crowded"), "neg_5_deg2"] == pytest.approx(2 / (2 * area), abs=0.01)
    assert rep.loc[("n1", "sparse"), "thresh_ujy"] == pytest.approx(50.0, abs=0.1)
    # Gaia: tile 0 holds both bright stars (found 2 of 2), tile 2 both faint ones (1 of 2)
    assert rep.loc[("n1", "sparse"), "gaia_(14, 16]"] == "1.00 (2)"
    assert rep.loc[("n3", "crowded"), "gaia_(18, 19]"] == "0.50 (2)"
    assert json.loads((tmp_path / "r.json").read_text())["noise_edges_mjysr"]["2"]
