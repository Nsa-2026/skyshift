"""completeness (inject.py): synthetic movers injected into stored dots and relinked."""

import math

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline import inject
from skyshift_pipeline.dots import as_dots
from skyshift_pipeline.photolist import add_sessions
from skyshift_pipeline.sky import cell_of
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

from test_photolist import square

ZONE = C.Zone("tinj", 120.5, 121.5, 15.0, 16.0)
T0 = 61000.0


def make_root(path, n_stars=150, sessions_per_visit=8, exposures=3, seed=5) -> Store:
    """Two visits 180 days apart, `sessions_per_visit` sessions a day apart, `exposures` exposures 2.1 min apart,
    detectors 1 and 4 on one footprint covering the zone; fixed stars with S/N 8–300 (noise 50 / 100 µJy)."""
    rng = np.random.default_rng(seed)
    store = Store(str(path))
    rows = []
    for v in range(2):
        for s in range(sessions_per_visit):
            start = T0 + 180.0 * v + 1.0 * s + 0.3 * rng.uniform()
            for e in range(exposures):
                mjd = start + e * 2.1 / 1440
                for det in (1, 4):
                    rows.append(dict(obs_id=f"V{v}S{s}E{e}", detector=det, mjd=mjd, s_region=square(121.0, 15.5),
                                     collection="spherex_dr1", url="", s3_key="", size_bytes=1))
    photos = add_sessions(pd.DataFrame(rows))
    store.write_parquet(photolist_rel(ZONE.name), photos)
    sra = rng.uniform(120.6, 121.4, n_stars)
    sdec = rng.uniform(15.1, 15.9, n_stars)
    ssnr = np.exp(rng.uniform(math.log(8), math.log(300), n_stars))
    for p in photos.itertuples():
        noise = 50.0 if p.detector == 1 else 100.0
        snr = ssnr * (1 + 0.1 * rng.normal(size=n_stars))
        ra = sra + rng.normal(0, 0.3, n_stars) / 3600 / math.cos(math.radians(15.5))
        dec = sdec + rng.normal(0, 0.3, n_stars) / 3600
        df = pd.DataFrame({"obs_id": p.obs_id, "detector": p.detector, "mjd": p.mjd, "session": p.session, "visit": p.visit,
                           "ra": ra, "dec": dec, "pos_err_arcsec": 0.3, "x": 1.0, "y": 1.0, "flux_ujy": snr * noise,
                           "flux_err_ujy": noise, "wavelength_um": 1.0, "bandwidth_um": 0.02, "elong": 1.1 + 0.1 * rng.uniform(size=n_stars),
                           "snr": snr, "flags": 0, "flags_peak": 0, "cell": cell_of(ra, dec)})
        store.write_parquet(dots_rel(p.obs_id, p.detector), as_dots(df))
    return store


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    return make_root(tmp_path / "root")


def test_sub_area_is_centred_and_clipped():
    box, inner = inject.sub_area(C.ZONES["hunt"], 1.0, 120.0)
    assert box.centre == pytest.approx(C.ZONES["hunt"].centre)
    assert (box.dec_max - box.dec_min) == pytest.approx(1.0)
    assert (box.ra_max - box.ra_min) * math.cos(math.radians(19.4)) == pytest.approx(1.0, rel=1e-3)
    assert inner.dec_min == pytest.approx(box.dec_min + 120 / 3600)
    small = C.Zone("s", 10.0, 10.2, 0.0, 0.2)
    box, inner = inject.sub_area(small, 1.0, 120.0)
    assert (box.ra_min, box.ra_max, box.dec_min, box.dec_max) == (10.0, 10.2, 0.0, 0.2)  # the zone itself
    assert inner.dec_max - inner.dec_min == pytest.approx(0.2 - 2 * 120 / 3600)


def test_detection_model_from_counts():
    """A power law dN/dS ∝ S^-2.5 thinned by a known efficiency below S/N 8: the model recovers it."""
    rng = np.random.default_rng(3)
    s = 5.0 * (1 - rng.uniform(size=400_000)) ** (-1 / 1.5)
    eff = np.clip((s - 5.0) / 3.0, 0, 1)
    s = s[rng.uniform(size=s.size) < eff]
    m = inject.detection_model(s)
    assert m["source"] == "dot counts" and m["slope"] == pytest.approx(2.5, abs=0.1)
    p = inject.p_detect(m, np.array([4.9, 5.5, 6.5, 7.5, 9.0, 20.0]))
    assert p[0] == 0.0 and p[-1] == 1.0 and p[-2] > 0.95
    assert p[1] == pytest.approx(1 / 6, abs=0.07) and p[2] == pytest.approx(0.5, abs=0.07) and p[3] == pytest.approx(5 / 6, abs=0.07)
    few = inject.detection_model(np.array([6.0, 7.0, 20.0]))
    assert few["source"].startswith("logistic")
    assert inject.p_detect(few, np.array([C.INJECT_DET_SNR50]))[0] == pytest.approx(0.5, abs=0.05)


def test_tracks_follow_the_rate_and_stay_in_footprints():
    photos = pd.DataFrame({"obs_id": ["A", "B", "C"], "detector": [1, 1, 1], "mjd": [T0, T0 + 1 / 24, T0 + 2 / 24],
                           "session": [1, 2, 3], "visit": [1, 1, 1],
                           "s_region": [square(121.0, 15.5), square(121.0, 15.5), square(130.0, 15.5)]})
    from skyshift_pipeline.sky import Footprint
    fps = [Footprint.from_s_region(s) for s in photos["s_region"]]
    obj = pd.DataFrame({"oid": [7], "ra0": [121.0], "dec0": [15.5], "t0": [T0], "rate": [36.0], "pa_deg": [0.0],
                        "vra": [0.0], "vdec": [36.0], "snr": [20.0]})
    box, _ = inject.sub_area(ZONE, 1.0, 120.0)
    tr = inject.tracks(obj, photos, fps, np.array([True, True, True]), box)
    assert tr["photo"].tolist() == [0, 1]  # photo C's footprint is elsewhere
    assert (tr["dec"].to_numpy() - 15.5) * 3600 == pytest.approx([0.0, 36.0])


def test_completeness_end_to_end(root, monkeypatch):
    monkeypatch.setattr(C, "INJECT_MWIR_SNR_FACTOR", 1.0)
    out = inject.run(root, ZONE, workers=2, rounds=2, n_objects=150, side_deg=0.8)
    rep = root.read_json(results_rel(ZONE.name, "completeness.json"))
    assert out["units"] == rep["injected"]["units"] > 500
    assert rep["sub_area"]["photos_with_dots"] == 96 and rep["sub_area"]["sessions"] == 16 and rep["sub_area"]["visits"] == 2
    assert set(rep["noise_ujy"]) == {str(d) for d in range(1, 7)} and rep["noise_ujy"]["1"] == pytest.approx(50.0)
    assert rep["mag_ab_at_snr_edges"]["1"][0] == pytest.approx(23.9 - 2.5 * math.log10(5 * 50.0), abs=0.01)
    by = {(b["rate_min"], b["rate_max"]): b for b in rep["by_rate"]}
    slow = [b for (lo, hi), b in by.items() if hi <= 0.05 and b["n_units"]]
    fast = [b for (lo, hi), b in by.items() if lo >= 4.64 and b["n_units"]]
    assert slow and fast
    # slow movers chain into fixed sources (one per visit, 180 days apart); fast ones come out as movers
    assert np.mean([b["fixed"] for b in slow]) > 0.8 and max(b["mover"] for b in slow) < 0.1
    assert np.mean([b["fixed_single_visit"] for b in slow if b["fixed_single_visit"] is not None]) > 0.7
    assert np.mean([b[f"mover_snr_ge_{C.INJECT_PLATEAU_SNR:g}"] for b in fast if b["n_units_snr_ge"]]) > 0.85
    assert rep["control_mover"] < 0.05
    assert len(rep["grid"]["mover"]) == len(C.INJECT_RATE_EDGES) - 1 and len(rep["grid"]["mover"][0]) == len(C.INJECT_SNR_EDGES) - 1
    assert rep["limits"]["rate_50"] is not None and rep["limits"]["rate_50"][0] <= 1.0 and rep["limits"]["rate_50"][1] >= 100.0
    assert "″/h" in rep["headline"]
    for k in inject.OUTCOMES:
        assert k in rep["overall"]
    assert sum(rep["overall"].values()) == pytest.approx(1.0, abs=1e-3)
    # deterministic for a zone
    again = inject.run(root, ZONE, workers=2, rounds=2, n_objects=150, side_deg=0.8)
    assert again["units"] == out["units"] and again["overall_mover"] == out["overall_mover"]


def test_injection_leaves_the_stored_results_alone(root):
    from skyshift_pipeline import link
    link.run(root, ZONE, 2)
    before = root.read_parquet(results_rel(ZONE.name, "movers.parquet"))
    inject.run(root, ZONE, workers=2, rounds=1, n_objects=50, side_deg=0.5)
    after = root.read_parquet(results_rel(ZONE.name, "movers.parquet"))
    pd.testing.assert_frame_equal(before, after)


def test_off_switch(root, monkeypatch):
    monkeypatch.setattr(C, "COMPLETENESS", False)
    assert inject.run(root, ZONE) == {"skipped": "SKYSHIFT_COMPLETENESS=off"}
    assert not root.exists(results_rel(ZONE.name, "completeness.json"))


def test_no_dots_writes_an_error(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    photos = add_sessions(pd.DataFrame([dict(obs_id="A", detector=1, mjd=T0, s_region=square(121.0, 15.5))]))
    store.write_parquet(photolist_rel(ZONE.name), photos)
    out = inject.run(store, ZONE, rounds=1, n_objects=10)
    assert out["error"] == "no dots in the sub-area"
    assert store.read_json(results_rel(ZONE.name, "completeness.json"))["error"]


def test_cli_registers_completeness_once_per_zone(root):
    from skyshift_pipeline import cli
    assert cli.STEPS.index("completeness") == cli.STEPS.index("score") + 1
    assert "completeness" not in cli.TILED_STEPS and "suspects" in cli.TILED_STEPS
    assert cli.main(["completeness", "--zone", ZONE.name, "--root", root.root, "--workers", "2"]) == 0
    summ = root.read_json(results_rel(ZONE.name, "summary.json"))["steps"]["completeness"]
    assert summ["units"] > 0 and "headline" in summ and summ["step_seconds"] >= 0


def test_a_completeness_failure_never_stops_the_run(root, monkeypatch):
    from skyshift_pipeline import cli

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(inject, "run", boom)
    assert cli.main(["completeness", "--zone", ZONE.name, "--root", root.root, "--workers", "2"]) == 0
    assert "synthetic failure" in root.read_json(results_rel(ZONE.name, "summary.json"))["steps"]["completeness"]["error"]


def test_no_units_when_sessions_have_one_exposure(tmp_path, monkeypatch):
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    store = make_root(tmp_path / "one", n_stars=30, sessions_per_visit=4, exposures=1)
    out = inject.run(store, ZONE, workers=2, rounds=1, n_objects=30, side_deg=0.5)
    assert out["units"] == 0 and out["overall_mover"] is None and out["headline"].startswith("not enough")
    assert store.exists(results_rel(ZONE.name, "completeness.json"))


def test_vs_recall_reads_the_zone_recall(root):
    root.write_json(results_rel(ZONE.name, "recall.json"), {"recall": 0.9, "n_known": 100, "v_max": 16.0, "dot_share": 0.97})
    inject.run(root, ZONE, workers=2, rounds=1, n_objects=200, side_deg=0.8)
    v = root.read_json(results_rel(ZONE.name, "completeness.json"))["vs_recall"]
    assert v["recall"] == 0.9 and v["n_units"] > 0 and v["ratio"] == pytest.approx(0.9 / v["synthetic"], abs=1e-3)


def test_vs_recall_compares_movers_alone_when_recall_json_has_them(tmp_path):
    """recall.json `recall` also counts tracklets' recovered dots; the synthetic side is movers only, so vs_recall uses
    `recall_movers` when present and the old `recall` otherwise."""
    store = Store(str(tmp_path))
    u = pd.DataFrame({"rate": [20.0] * 4 + [200.0], "snr": [50.0] * 5, "outcome": ["mover"] * 3 + ["fixed", "mover"]})
    rel = results_rel(ZONE.name, "recall.json")
    store.write_json(rel, {"recall": 0.947, "recall_movers": 0.904, "n_known": 457, "v_max": 16.0, "dot_share": 0.97})
    v = inject.vs_recall(store, ZONE, u)
    assert v["synthetic"] == 0.75 and v["n_units"] == 4
    assert v["recall"] == 0.904 and v["recall_field"] == "recall_movers" and v["recall_with_tracks"] == 0.947
    assert v["ratio"] == pytest.approx(0.904 / 0.75, abs=1e-4) and v["recall_n"] == 457
    store.write_json(rel, {"recall": 0.9, "n_known": 100, "v_max": 16.0})  # before tracklets: recall was movers alone
    v = inject.vs_recall(store, ZONE, u)
    assert v["recall"] == 0.9 and v["recall_field"] == "recall" and "recall_with_tracks" not in v
    assert inject.vs_recall(Store(str(tmp_path / "none")), ZONE, u) == {
        "rates_arcsec_h": list(C.INJECT_RECALL_RATES), "snr_min": C.INJECT_PLATEAU_SNR, "n_units": 4, "synthetic": 0.75}
