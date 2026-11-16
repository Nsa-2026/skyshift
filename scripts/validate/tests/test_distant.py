"""Offline tests for distant.py: the site's distant.json from the pipeline step's output (contracts/api.md v3.6)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import distant  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "fixtures" / "api" / "distant.json"


def step_output(pairs=1, controls=(1, 4, 3, 0, 2, 2), poisson_p=0.78, skipped=None):
    """A small hand-made `results/<zone>/distant.json` in the orphans step's shape."""
    pair = {"source_id_a": 64904007030800, "source_id_b": 64940007013300, "visit_a": 1, "visit_b": 2,
            "ra_a": 123.334145, "dec_a": 18.174917, "mjd_a": 60975.2943, "ra_b": 123.069556, "dec_b": 18.234311,
            "mjd_b": 61144.9956, "sep_arcsec": 929.8, "dlon_arcsec": -929.7, "dlat_arcsec": 11.4, "distance_au": 404.1,
            "distance_range_au": [404.1, 445.9], "speed_au_yr": 0.364, "flux_ratio": 0.814, "n_common_det": 1,
            "n_dots_a": 3, "n_dots_b": 3, "snr_a": 6.55, "snr_b": 15.29, "chi2_drift": 2.2, "chi2_flux": 0.21, "dof": 4,
            "p": 0.6604, "known_a": None, "known_b": None, "score": 0.6604}
    return {
        "zone": "hunt", "created": "2026-11-14T17:35:15Z",
        "searched": {"rate_arcsec_h": [0.0, 1.0], "sep_arcsec": [300.0, 7200.0], "distance_grid_au": [40.0, 2000.0],
                     "depth_ab_snr5": {"D1": 17.26, "D2": 17.36}, "reach_au_at_d2_depth": {"neptune_p0.4": 283.0},
                     "pair_min_p": 0.01},
        "counts": {"single_visit_sources": 5275, "unmatched": 779, "unmatched_covered": 779, "orphans": 447,
                   "drift_fitted": 446, "drift_ok": 442,
                   "reasons": {"bright_fragment": 314, "zone_edge": 26, "saturated": 5}, "pairs_tested": 892,
                   "pairs": pairs, "open_orphans": 103, "known_orphans": 0},
        "control": {"n": len(controls), "passing_per_control": list(controls),
                    "expected_chance_pairs": round(sum(controls) / len(controls), 3), "tested_per_control": 873.8,
                    "poisson_p": poisson_p, "method": "shifted"},
        "pairs": [pair] * pairs, "open": [], "known": [], "skipped": skipped, "limits": ["a note"],
    }


INJECTION = {"zone": "hunt", "zone_box": {"ra": [120.5, 130.5], "dec": [14.4, 24.4]}, "method": "table-level",
             "distances": [{"distance_au": 70.0, "injected": 63, "recovered": 61, "recovered_frac": 0.968,
                            "distance_fit_median_au": 70.0, "distance_err_median_frac": 0.0}],
             "recovered_frac": 0.966, "min_cell_recovered_frac": 0.826, "distance_err_median_frac": 0.01}


def test_build_maps_the_step_output_onto_the_contract():
    site = distant.build(step_output(), INJECTION, "2026-11-14T18:00:00Z", "DR1")
    c = site["counts"]
    assert (c["uncatalogued"], c["covered"], c["orphans"], c["drift_ok"]) == (779, 779, 447, 442)
    assert c["dropped"] == {"bright_fragment": 314, "zone_edge": 26, "saturated": 5}
    assert (c["pairs_tested"], c["pairs"], c["open_orphans"], c["known_orphans"]) == (892, 1, 103, 0)
    p = site["pairs"][0]
    assert (p["distance_au"], p["distance_lo_au"], p["distance_hi_au"]) == (404.1, 404.1, 445.9)
    assert "distance_range_au" not in p and "score" not in p
    k = site["control"]
    assert k["n"] == 6 and k["expected_pairs"] == 2.0 and abs(k["spread_pairs"] - 1.414) < 0.001
    assert k["poisson_p"] == 0.78 and k["beyond_chance"] is False
    assert site["searched"]["distance_au"] == [70.0, 1000.0] and site["zone_box"]["ra"] == [120.5, 130.5]
    assert site["injection"]["distances"][0] == {"distance_au": 70.0, "injected": 63, "recovered": 61,
                                                 "recovered_frac": 0.968, "distance_fit_median_au": 70.0}
    assert site["limits"] == {"depth_ab_snr5": {"D1": 17.26, "D2": 17.36}, "reach_au": {"neptune_p0.4": 283.0},
                              "notes": ["a note"]}


def test_beyond_chance_and_no_injection():
    site = distant.build(step_output(pairs=6, controls=(0, 1, 0, 1), poisson_p=0.0004), None, "t", "DR1")
    assert site["control"]["beyond_chance"] is True and site["injection"] is None and site["zone_box"] is None


def test_same_keys_as_the_contract_fixture():
    site = distant.build(step_output(), INJECTION, "t", "DR1")
    fx = json.loads(FIXTURE.read_text())

    def keys(a, b, path=""):
        if isinstance(b, dict) and path not in ("counts.dropped", "limits.depth_ab_snr5", "limits.reach_au"):
            assert set(a) == set(b) - {"synthetic", "note"}, path
            for k in a:
                keys(a[k], b[k], f"{path}.{k}" if path else k)
        elif isinstance(b, list) and b and isinstance(b[0], dict):
            keys(a[0], b[0], path)

    keys(site, fx)


def test_write_site_file_uses_the_first_zone_with_a_valid_run(tmp_path):
    res = tmp_path / "results"
    (res / "pilot").mkdir(parents=True)
    (res / "pilot" / "distant.json").write_text(json.dumps(step_output()))
    (res / "hunt").mkdir()
    (res / "hunt" / "distant.json").write_text(json.dumps(step_output(skipped="too many orphans")))
    out = tmp_path / "distant.json"
    site = distant.write_site_file(res, ["hunt", "pilot"], out, "DR1", injection=tmp_path / "none.json")
    assert site is not None and json.loads(out.read_text())["counts"]["pairs"] == 1 and site["injection"] is None
    assert distant.write_site_file(res, ["nowhere"], tmp_path / "x.json", "DR1") is None
    assert not (tmp_path / "x.json").exists()


def test_committed_hunt_run_builds():
    """data/distant_hunt.json (a local run of the step on the 14 Nov hunt tables) gives the published numbers."""
    run = json.loads((distant.DATA / "distant_hunt.json").read_text())
    site = distant.build(run, None, "t", "DR1")
    assert site["counts"]["pairs_tested"] == 892 and site["counts"]["pairs"] == 1
    assert site["control"]["expected_pairs"] == 1.5 and site["control"]["beyond_chance"] is False


def test_searched_follows_the_steps_pair_window():
    """v3.27: a v3.26 run says what it searched (pair_window 40–1000 AU); the site file passes it on, not [70, 1000]."""
    run = step_output()
    pw = {"au": [40.0, 1000.0], "tol_arcsec": 30.0, "min_dt_days": 20.0, "perp_frac": 0.4, "rule": "parallax"}
    run["searched"] = {**run["searched"], "pair_window": pw, "session_stacks": False}
    s = distant.build(run, INJECTION, "t", "DR1")["searched"]
    assert s["distance_au"] == [40.0, 1000.0] and s["pair_window"] == pw and s["session_stacks"] is False
    assert distant.build(step_output(), INJECTION, "t", "DR1")["searched"]["distance_au"] == [70.0, 1000.0]
