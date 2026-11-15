"""The measured layer's planet rule (contracts/api.md v3.18): a mover near a planet at its time is class `known`, named
"Saturn" or "a moon of Saturn" (skyshift_core.planets), even when an old suspects table lists it. Real geometry: Iapetus
4.7′ from Saturn on 2026-01-05 (ecl35-515240213288-87871772, a grade A suspect of the 15 Nov sky run, identified with
JPL Horizons). Offline: a small synthetic data root, as tests/test_measured.py."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from skyshift_core.planets import positions
from skyshift_server import measured as M

MJD = 61022.064174
T1, T2 = MJD - 0.00075, MJD + 0.00075   # two exposures 2.16 min apart
IAPETUS = (356.404831, -4.126031)
SATURN = tuple(float(x[0]) for x in positions(MJD)["Saturn"])
COS = math.cos(math.radians(IAPETUS[1]))


def off(dx: float, dy: float, ra: float, dec: float) -> tuple[float, float]:
    return ra + dx / 3600.0 / math.cos(math.radians(dec)), dec + dy / 3600.0


ASTEROID = off(-120, 150, *SATURN)  # 3.2′ from Saturn: a known[] prediction (MPC) matches it


def _write(path: Path, cols: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols), path)


def _dots(path: Path, mjd: float, pts: list[tuple[float, float]]) -> None:
    n = len(pts)
    _write(path, {"obs_id": ["x"] * n, "detector": np.full(n, 2, np.int8), "mjd": np.full(n, mjd),
                  "ra": np.array([p[0] for p in pts]), "dec": np.array([p[1] for p in pts]),
                  "pos_err_arcsec": np.full(n, 0.3, np.float32), "snr": np.full(n, 50.0, np.float32),
                  "flux_ujy": np.full(n, 900.0, np.float32), "flags": np.zeros(n, np.int32),
                  "flags_peak": np.zeros(n, np.int32), "elong": np.full(n, 1.1, np.float32)})


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYSHIFT_MEASURED", "1")
    M.reset()
    r = tmp_path / "bucket"
    (r / "skyrun").mkdir(parents=True)
    (r / "skyrun" / "progress.json").write_text(json.dumps({"zones": [
        {"name": "ecl35", "ra": [354.0, 359.0], "dec": [-6.0, -2.0], "state": "combined", "final": True, "stage": "final"}]}))
    z = r / "results" / "ecl35"
    star = off(200, -200, *IAPETUS)
    _write(z / "crossids.parquet", {"source_id": np.array([111], np.int64), "ra": np.array([star[0]]),
                                    "dec": np.array([star[1]]), "gaia_id": np.array([-1], np.int64)})
    movers = [IAPETUS, SATURN, ASTEROID]
    _write(z / "movers.parquet", {
        "ra": np.array([p[0] for p in movers]), "dec": np.array([p[1] for p in movers]), "mjd": np.full(3, MJD),
        "session": np.ones(3, np.int32), "evidence": ["two_exposures"] * 3, "flags": np.zeros(3, np.int32),
        "flags_peak_all": np.zeros(3, np.int32), "obs_ids": ["OBS_1,OBS_2"] * 3, "detectors": ["2"] * 3,
        "cell": np.array([7001, 7002, 7003], np.int64)})
    # a suspects table written before the `planets` veto lists Iapetus (grade A) and the asteroid
    _write(z / "suspects.parquet", {"id": ["ecl35-7001-87871772", "ecl35-7003-87871772"],
                                    "cell": np.array([7001, 7003], np.int64), "mjd": np.full(2, MJD),
                                    "grade": ["A", "B"], "reasons": [[], []]})
    _dots(r / "dots" / "OBS_1D2.parquet", T1, [off(-1, 0, *p) for p in movers] + [star])
    _dots(r / "dots" / "OBS_2D2.parquet", T2, [off(1, 0, *p) for p in movers] + [star])
    return str(r)


def _meta(known=()):
    epochs = [{"id": "d2-OBS_1", "kind": "exposure", "mjd_mid": T1, "det": 2},
              {"id": "d2-OBS_2", "kind": "exposure", "mjd_mid": T2, "det": 2}]
    return {"clock": "hours", "center": {"ra": IAPETUS[0], "dec": IAPETUS[1]}, "pixel_arcsec": 6.15,
            "size": [195, 195], "epochs": epochs, "gaia": [], "known": list(known)}


def test_planet_and_moon_tracks_are_known(root):
    known = [{"name": "(4321) Rock", "epoch_id": "d2-OBS_1", "ra": off(3, 0, *ASTEROID)[0], "dec": ASTEROID[1],
              "v": 17.0, "source": "mpc"}]
    m, reason = M.build(_meta(known), root=root)
    assert reason is None and m["zone"] == "ecl35"
    by_cell = {t["id"].split("-")[1].lstrip("m"): t for t in m["tracks"]}
    iap, sat, rock = by_cell["7001"], by_cell["7002"], by_cell["7003"]
    assert iap["class"] == "known" and iap["known_name"] == "a moon of Saturn"
    assert iap["suspect_id"] is None and iap["id"] == "ecl35-m7001-87871772"
    assert sat["class"] == "known" and sat["known_name"] == "Saturn"
    assert rock["class"] == "known" and rock["known_name"] == "(4321) Rock"  # an MPC match names it
    dots = [d for d in m["detections"] if d["track_id"] == iap["id"]]
    assert len(dots) == 2 and all(d["class"] == "known" and d["known_name"] == "a moon of Saturn" and d["grade"] is None
                                  for d in dots)
    assert m["counts"]["suspect"] == 0 and m["counts"]["known"] == 6
    json.dumps(m, allow_nan=False)


def test_planet_check_failure_keeps_the_classes(root, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no ephemeris")
    monkeypatch.setattr(M, "near_planets", boom)
    m, reason = M.build(_meta(), root=root)
    assert reason is None
    by_cell = {t["id"].split("-")[1].lstrip("m"): t for t in m["tracks"]}
    assert by_cell["7001"]["class"] == "suspect" and by_cell["7002"]["class"] == "mover"
