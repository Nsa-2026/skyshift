"""End to end on a local root: synthetic photo list, dot files and known (MPC) cache -> link, score, suspects."""

import json

import numpy as np
import pandas as pd

from skyshift_pipeline import config as C
from skyshift_pipeline import suspects as S
from skyshift_pipeline.cli import main
from skyshift_pipeline.dots import as_dots
from skyshift_pipeline.known import OBJ_KEYS, run_rel
from skyshift_pipeline.photolist import add_sessions
from skyshift_pipeline.sky import cell_of
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

from conftest import t
from test_photolist import square

STAR = (122.30, 16.10)
KNOWN = (122.40, 16.20)     # a known asteroid, seen as a mover in session 2
NEW = (122.20, 16.25)       # an unknown mover in session 3


def dots_df(obs_id, det, mjd, pts):
    ra = np.array([p[0] for p in pts]); dec = np.array([p[1] for p in pts])
    n = len(pts)
    return pd.DataFrame({
        "obs_id": obs_id, "detector": det, "mjd": mjd, "session": 0, "visit": 0, "ra": ra, "dec": dec,
        "pos_err_arcsec": 0.3, "x": 1.0, "y": 1.0, "flux_ujy": 100.0, "flux_err_ujy": 5.0, "wavelength_um": 1.0,
        "bandwidth_um": 0.02, "elong": 1.1, "snr": np.full(n, 20.0), "flags": 0, "flags_peak": 0, "cell": cell_of(ra, dec),
    }).pipe(as_dots)


def test_link_score_suspects_end_to_end(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    zone = C.ZONES["pilot"]
    rows = []
    for s in range(5):
        for e in range(2):
            rows.append(dict(obs_id=f"S{s}E{e}", detector=1, mjd=t(s, e), s_region=square(122.3, 16.15),
                             collection="spherex_dr1", url="", s3_key="", size_bytes=1))
    photos = add_sessions(pd.DataFrame(rows))
    store.write_parquet(photolist_rel("pilot"), photos)
    for p in photos.itertuples():
        s, e = int(p.obs_id[1]), int(p.obs_id[3])
        pts = [STAR]
        if s == 2:
            pts.append((KNOWN[0] + e * 1e-4, KNOWN[1]))
        if s == 3:
            pts.append(NEW)
        store.write_parquet(dots_rel(p.obs_id, 1), dots_df(p.obs_id, 1, p.mjd, pts))
    for _, g in photos.groupby("session"):
        objs = {k: [] for k in OBJ_KEYS}
        per_photo = {"obs_id": [], "detector": [], "name": [], "ra": [], "dec": []}
        if int(g["obs_id"].iloc[0][1]) == 2:
            for k, v in zip(OBJ_KEYS, ["Known", 1234, KNOWN[0], KNOWN[1], 15.2, 0.0, 0.0, "MB"]):
                objs[k].append(v)
            for p in g.itertuples():
                for k, v in zip(per_photo, [p.obs_id, 1, "Known", KNOWN[0], KNOWN[1]]):
                    per_photo[k].append(v)
        store.write_json(run_rel(zone, g), {"mjd": float(g["mjd"].iloc[0]), "objects": objs, "per_photo": per_photo})

    monkeypatch.setattr(S, "gaia_near", lambda ra, dec, *a, **k: (np.zeros(len(ra), bool), "test"))
    monkeypatch.setattr(S, "catwise_near", lambda ra, dec, *a, **k: (np.zeros(len(ra), bool), "test"))
    for step in ("link", "spectra", "score", "suspects"):
        assert main([step, "--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]) == 0

    movers = store.read_parquet(results_rel("pilot", "movers.parquet"))
    fixed = store.read_parquet(results_rel("pilot", "fixed.parquet"))
    assert len(fixed) == 1 and len(movers) == 2
    assert set(movers["session"]) == {3, 4} and (movers["visit"] == 1).all()  # sessions 3 and 4 of 5
    rec = json.loads((tmp_path / "results/pilot/recall.json").read_text())
    assert rec["n_known"] == 1 and rec["recall"] == 1.0 and rec["g2_pass"] is True
    sus = json.loads((tmp_path / "results/pilot/suspects.json").read_text())
    assert sus["data_release"] == "DR1" and len(sus["suspects"]) == 1
    assert abs(sus["suspects"][0]["ra"] - NEW[0]) < 1e-5
    summary = json.loads((tmp_path / "results/pilot/summary.json").read_text())
    assert set(summary["steps"]) == {"link", "spectra", "score", "suspects"}
    src = store.read_parquet(results_rel("pilot", "sources.parquet"))
    assert len(src) == 1 and src["source_id"].iloc[0] == fixed["source_id"].iloc[0]
    assert src["n_dots"].iloc[0] == 10 and src["n_sessions"].iloc[0] == 5 and src["detectors"].iloc[0] == 1
    assert len(json.loads((tmp_path / "results/pilot/channels.json").read_text())) == 102
