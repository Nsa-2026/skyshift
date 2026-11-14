"""Shape review (shape.py): sky ellipticity and PA, Jacobians, the per-source test, catalogue neighbours."""

from __future__ import annotations

import numpy as np
import pandas as pd

from skyshift_pipeline import shape as S


def test_sky_ellipticity_pa():
    # x points north, y east: an ellipse along x lies along PA 0
    e1, e2 = S.sky_ellipticity([2.0], [1.0], [0.0], np.array([[0.0, 1.0], [1.0, 0.0]]))
    assert e1[0] > 0.5 and abs(e2[0]) < 1e-9 and S.pa_of(e1, e2)[0] == 0.0
    # x points east: along PA 90
    e1, e2 = S.sky_ellipticity([2.0], [1.0], [0.0], np.eye(2))
    assert abs(S.pa_of(e1, e2)[0] - 90.0) < 1e-9
    # the detector turned 180°: the same axis on the sky
    a = S.sky_ellipticity([2.0], [1.0], [30.0], np.eye(2))
    b = S.sky_ellipticity([2.0], [1.0], [30.0], -np.eye(2))
    assert np.allclose(a, b)
    # a round dot has no ellipticity
    assert np.allclose(S.sky_ellipticity([1.5], [1.5], [12.0], np.eye(2)), 0.0)
    assert S.pa_diff(179.0, 1.0) == 2.0 and S.pa_diff(10.0, 100.0) == 90.0


def test_photo_jacobians():
    rng = np.random.default_rng(1)
    x, y = rng.uniform(0, 2040, 200), rng.uniform(0, 2040, 200)
    J = np.array([[-6.1, 0.4], [0.5, 6.1]])
    east, north = J @ np.vstack([x, y]) + 3.0
    out = S.photo_jacobians(np.zeros(200, np.int64), x, y, east, north)
    assert np.allclose(out[0], J, atol=1e-6)


def _dots(e, pa, n_per_visit, visits=(1, 2), seed=0, sigma=0.05):
    rng = np.random.default_rng(seed)
    rows = []
    for v in visits:
        r1 = e * np.cos(np.deg2rad(2 * pa)) + rng.normal(0, sigma, n_per_visit)
        r2 = e * np.sin(np.deg2rad(2 * pa)) + rng.normal(0, sigma, n_per_visit)
        rows.append(pd.DataFrame({"source_id": 7, "r1": r1, "r2": r2, "sigma": sigma, "visit": v}))
    return pd.concat(rows, ignore_index=True)


def test_source_shapes_steady_and_noise():
    s = S.source_shapes(_dots(0.20, 40.0, 25))
    r = s.iloc[0]
    assert r["sig"] > S.SHAPE_SIG_MIN and abs(r["pa_deg"] - 40.0) < 5 and bool(r["steady_pa"]) and S.fires(s)[0]
    noise = S.source_shapes(_dots(0.0, 0.0, 25, seed=3))
    assert noise.iloc[0]["sig"] < 4 and not S.fires(noise)[0]


def test_source_shapes_one_visit():
    d = pd.concat([_dots(0.10, 120.0, 25, visits=(1,), seed=1), _dots(0.0, 0.0, 25, visits=(2,), seed=2)], ignore_index=True)
    s = S.source_shapes(d)
    assert bool(s.iloc[0]["one_visit"]) and not bool(s.iloc[0]["steady_pa"])


def test_source_shapes_pa_disagrees():
    d = pd.concat([_dots(0.10, 20.0, 25, visits=(1,), seed=1), _dots(0.10, 110.0, 25, visits=(2,), seed=2)], ignore_index=True)
    s = S.source_shapes(d)
    assert not S.fires(s)[0]


def test_residuals_take_the_local_reference():
    d = pd.DataFrame({"photo": [0] * 20, "x": [100.0] * 10 + [1900.0] * 10, "y": [100.0] * 20,
                      "e1": [0.05] * 9 + [0.2] + [-0.05] * 10, "e2": [0.0] * 20})
    is_ref = np.array([True] * 9 + [False] + [True] * 10)
    r = S.residuals(d, is_ref)
    assert abs(r["r1"].iloc[9] - 0.15) < 1e-9 and abs(r["r1"].iloc[15]) < 1e-9


def test_catalogue_context_and_one_object():
    ra0, dec0 = 150.0, 2.0
    d = 1 / 3600
    ref = pd.DataFrame({
        "cat": ["gaia", "catwise", "catwise", "gaia", "gaia", "catwise"],
        #        A primary, its CatWISE, faint CatWISE, B primary, B's bright neighbour 6″, C IR-only
        "ra": [ra0, ra0 + 1 * d, ra0 + 8 * d, ra0 + 0.1, ra0 + 0.1 + 6 * d, ra0 + 0.2],
        "dec": [dec0, dec0, dec0, dec0, dec0, dec0],
        "g_mag": [15.0, np.nan, np.nan, 15.0, 15.5, np.nan],
        "w1_mag": [np.nan, 13.0, 17.5, np.nan, np.nan, 12.0],
        "ruwe": [1.0, np.nan, np.nan, 1.0, 1.0, np.nan], "galaxy_or_qso": [False] * 6,
    })
    ctx = S.catalogue_context(np.array([ra0, ra0 + 0.1, ra0 + 0.2, ra0 + 0.3]), np.full(4, dec0), ref)
    # A: its faint CatWISE neighbour at 8″ counts (any catalogued neighbour explains a stretch); B: a Gaia neighbour;
    # C: one IR-only object; D: nothing listed
    assert list(S.one_object(ctx)) == [False, False, True, False]
    ref2 = ref.drop(index=2)
    assert S.one_object(S.catalogue_context(np.array([ra0]), np.array([dec0]), ref2))[0]   # A without it: one object
    assert abs(ctx["pair_sep_arcsec"].iloc[1] - 6.0) < 0.1 and abs(ctx["pair_pa_deg"].iloc[1] - 90.0) < 0.5


def test_bright_neighbour_beyond_15_arcsec_explains_it():
    d = 1 / 3600
    ref = pd.DataFrame({"cat": ["gaia", "gaia", "gaia"], "ra": [150.0, 150.0, 150.3], "dec": [2.0, 2.0 + 20 * d, 2.0],
                        "g_mag": [15.0, 16.0, 15.0], "w1_mag": [np.nan] * 3, "ruwe": [1.0] * 3, "galaxy_or_qso": [False] * 3})
    ctx = S.catalogue_context(np.array([150.0, 150.3]), np.array([2.0, 2.0]), ref)
    assert ctx["n_wide_explain"].tolist() == [1, 0] and list(S.one_object(ctx)) == [False, True]
    faint = ref.assign(g_mag=[15.0, 18.0, 15.0])                       # 3 mag fainter at 20″: doesn't explain it
    assert S.one_object(S.catalogue_context(np.array([150.0]), np.array([2.0]), faint))[0]


def test_merge_into_is_a_pure_addition(tmp_path):
    import json

    from skyshift_pipeline import candidates as K

    base = {k: None for k in K.FIELDS}
    mover = {**base, "id": "hunt-1-2", "kind": "mover", "ra": 125.0, "dec": 20.0, "zone": "hunt", "zones": ["hunt"], "score": 0.5,
             "clock": "hours", "det": 1, "mjd": 60800.0, "gaia_match": False, "catwise_match": False, "galactic_b": 30.0,
             "labels": [], "reasons": [], "why": "x", "flags": []}
    spec = {**mover, "id": "hunt-sp-9", "kind": "spectral", "clock": "months", "score": 0.9}
    pilot = {**mover, "id": "pilot-1-3", "zone": "pilot", "zones": ["pilot"]}
    doc = {"generated": "g", "data_release": "DR1+QR3", "zones": ["hunt", "pilot"],
           "counts": {"mover": 2, "high_pm": 0, "spectral": 1}, "candidates": [mover, spec, pilot]}
    js = tmp_path / "candidates.json"
    js.write_text(K.dumps(doc))
    rows = tmp_path / "shape_candidates.parquet"
    pd.DataFrame({"source_id": [42, 43], "ra": [125.5, 125.6], "dec": [20.1, 20.2], "mjd": [60800.5, 60801.0], "det": [3, 2],
                  "snr": [85.2, 40.0], "flux_ujy": [310.0, 90.0], "score": [0.3, 0.6], "pa_deg": [37.2, 100.0],
                  "gaia_match": [True, False], "catwise_match": [True, True], "reasons": ["elongated,steady_pa,one_catalogue_source"] * 2,
                  "why": ["w1", "w2"], "flags": ["", ""]}).to_parquet(rows)
    counts = S.merge_into(js, rows, "hunt")
    assert counts == {"mover": 2, "high_pm": 0, "spectral": 1, "shape": 2}
    out = json.loads(js.read_text())
    ids = [c["id"] for c in out["candidates"]]
    assert ids == ["hunt-1-2", "hunt-sp-9", "hunt-sh-43", "hunt-sh-42", "pilot-1-3"]   # zone, kind, score order
    assert out["candidates"][0] == json.loads(json.dumps(mover)) and out["candidates"][-1] == json.loads(json.dumps(pilot))
    assert all(list(c) == list(K.FIELDS) for c in out["candidates"])
    assert S.merge_into(js, rows, "hunt")["shape"] == 2                  # running it again replaces, never doubles


def test_score_reasons_why():
    sc = S.shape_score([15.0, 15.0], [False, True])
    assert abs(sc[0] - (1 - np.exp(-1))) < 1e-9 and abs(sc[1] - 0.8 * sc[0]) < 1e-9
    row = {"pa_deg": 37.2, "sig": 8.04, "n_dots": 40, "steady_pa": True, "one_visit": False, "gaia_g": 15.0, "class": "star"}
    assert S.shape_reasons(row) == ["elongated", "steady_pa", "one_catalogue_source"]
    w = S.shape_why(row)
    assert w.startswith("Looks like two objects here but the catalogues list one") and "PA 37°" in w and "both visits" in w
