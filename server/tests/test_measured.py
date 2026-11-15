"""The measured layer (contracts/api.md v3.3) on a small synthetic data root: progress.json, one
zone's result tables and a few dot files, all written here with pyarrow. Offline."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from skyshift_server import measured as M

RA0, DEC0 = 10.0, 20.0
T1, T2 = 60000.0, 60000.0015  # two exposures 2.16 min apart (one session)
T3 = 60180.0                  # the next visit
GHOST, SOURCE, OUTLIER, HOT = 1 << 22, 1 << 21, 1 << 19, 1 << 10
COS = math.cos(math.radians(DEC0))


def off(dx_arcsec: float, dy_arcsec: float, ra: float = RA0, dec: float = DEC0) -> tuple[float, float]:
    return ra + dx_arcsec / 3600.0 / math.cos(math.radians(dec)), dec + dy_arcsec / 3600.0


S1 = off(60, 60)     # Gaia star
S2 = off(-60, -60)   # linked source, no Gaia (a high-pm candidate that failed vetting)
S3 = off(120, -30)   # fast source, no Gaia: a vetted new high-pm fit
MOVER_A = off(100, -100)      # suspect, two exposures
MOVER_C = off(-150, 150)      # ghost-flagged mover
MOVER_K = off(200, 0)         # a known asteroid, paired by the pipeline
LONE_KNOWN = off(-200, 0)     # a lone dot on a known prediction
LONE_GHOST = off(0, 200)
LONE_SOURCE_OUTLIER = off(0, -200)  # SOURCE at the peak and OUTLIER: never an artefact by themselves
FAR = off(5000, 0)            # outside the 20' frame


def _write(path: Path, cols: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols), path)


def _dots(path: Path, mjd: float, pts: list[tuple[tuple[float, float], float, int, int]], v4: bool = False) -> None:
    """pts: ((ra, dec), snr, flags, flags_peak)."""
    n = len(pts)
    cols = {
        "obs_id": ["x"] * n, "detector": np.full(n, 2, np.int8), "mjd": np.full(n, mjd),
        "ra": np.array([p[0][0] for p in pts]), "dec": np.array([p[0][1] for p in pts]),
        "pos_err_arcsec": np.full(n, 0.3, np.float32), "snr": np.array([p[1] for p in pts], np.float32),
        "flux_ujy": np.array([10.0 * p[1] for p in pts], np.float32),
        "flags": np.array([p[2] for p in pts], np.int32), "flags_peak": np.array([p[3] for p in pts], np.int32),
        "elong": np.full(n, 1.1, np.float32),
    }
    if v4:  # ePSF positions 0.5" east of the centroid
        cols["ra_psf"] = cols["ra"] + 0.5 / 3600.0 / COS
        cols["dec_psf"] = cols["dec"]
        cols["psf_pos_ok"] = np.ones(n, bool)
        cols["pos_err_psf_arcsec"] = np.full(n, 0.2, np.float32)
    _write(path, cols)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYSHIFT_MEASURED", "1")
    M.reset()
    r = tmp_path / "bucket"
    (r / "skyrun").mkdir(parents=True)
    (r / "skyrun" / "progress.json").write_text(json.dumps({"zones": [
        {"name": "zz", "ra": [9.0, 11.0], "dec": [19.0, 21.0], "state": "combined", "final": True, "stage": "final"},
        {"name": "busy", "ra": [30.0, 40.0], "dec": [0.0, 10.0], "state": "extracting", "final": False,
         "stage": "provisional", "combined_at": None},
    ]}))
    z = r / "results" / "zz"
    ids = [111, 222, 333]
    _write(z / "crossids.parquet", {
        "source_id": np.array(ids, np.int64), "ra": np.array([S1[0], S2[0], S3[0]]),
        "dec": np.array([S1[1], S2[1], S3[1]]), "gaia_id": np.array([4242424242424242424, -1, -1], np.int64)})
    a_mjd = (T1 + T2) / 2
    _write(z / "movers.parquet", {
        "ra": np.array([MOVER_A[0], MOVER_C[0], MOVER_K[0]]), "dec": np.array([MOVER_A[1], MOVER_C[1], MOVER_K[1]]),
        "mjd": np.array([a_mjd, a_mjd, a_mjd]), "session": np.array([1, 1, 1], np.int32),
        "evidence": ["two_exposures"] * 3, "flags": np.array([OUTLIER, GHOST, 0], np.int32),
        "flags_peak_all": np.array([SOURCE, 0, 0], np.int32), "obs_ids": ["OBS_1,OBS_2"] * 3,
        "detectors": ["2"] * 3, "cell": np.array([5001, 5002, 5003], np.int64)})
    _write(z / "suspects.parquet", {
        "id": ["zz-5001-86400002"], "cell": np.array([5001], np.int64), "mjd": np.array([a_mjd]),
        "grade": ["C"], "reasons": [["hot_pixel", "edge"]]})
    _write(z / "astrometry.parquet", {
        "source_id": np.array([333, 111], np.int64), "ra": np.array([S3[0], S1[0]]), "dec": np.array([S3[1], S1[1]]),
        "epoch_mjd": np.array([T1, T1]), "pmra": np.array([4000.0, 5.0], np.float32),
        "pmdec": np.array([-3000.0, 1.0], np.float32), "pmra_err": np.array([200.0, 50.0], np.float32),
        "pmdec_err": np.array([200.0, 50.0], np.float32), "pm_snr": np.array([25.0, 0.1], np.float32)})
    _write(z / "highpm_candidates.parquet", {
        "method": ["fit", "fit"], "status": ["new", "new"], "source_id": np.array([333, 222], np.int64),
        "source_id_b": np.array([-1, -1], np.int64), "vetted": np.array([True, False]), "reason": ["", "bad_fit,few_dots"]})
    # dots: two exposures of one session, and one of the next visit
    d = r / "dots"
    _dots(d / "OBS_1D2.parquet", T1, [
        (off(0.5, 0, *S1), 300.0, 0, 0), (off(0, 0.5, *S2), 50.0, 0, 0), (S3, 80.0, 0, 0),
        (off(-1, 0, *MOVER_A), 20.0, OUTLIER, SOURCE), (off(-1, 0, *MOVER_C), 15.0, GHOST, 0),
        (off(-1, 0, *MOVER_K), 30.0, 0, 0), (LONE_KNOWN, 12.0, 0, 0), (LONE_GHOST, 9.0, GHOST, 0),
        (LONE_SOURCE_OUTLIER, 8.0, OUTLIER, SOURCE), (FAR, 99.0, 0, 0)])
    _dots(d / "OBS_2D2.parquet", T2, [
        (off(0.5, 0, *S1), 310.0, 0, 0), (S3, 70.0, 0, 0), (off(1, 0, *MOVER_A), 18.0, 0, 0),
        (off(1, 0, *MOVER_C), 14.0, GHOST, 0), (off(1, 0, *MOVER_K), 29.0, 0, 0), (off(0.2, 0, *LONE_KNOWN), 11.0, 0, 0)])
    _dots(d / "OBS_3D2.parquet", T3, [(off(0, 0, *S1), 305.0, 0, 0), (off(2, -1.5, *S3), 75.0, 0, 0)], v4=True)
    return str(r)


def _hours_meta(known=()):
    epochs = [{"id": "d2-OBS_1", "kind": "exposure", "mjd_mid": T1, "det": 2},
              {"id": "d2-OBS_2", "kind": "exposure", "mjd_mid": T2, "det": 2}]
    return {"clock": "hours", "center": {"ra": RA0, "dec": DEC0}, "pixel_arcsec": 6.15, "size": [195, 195],
            "epochs": epochs, "gaia": [], "known": list(known)}


def _months_meta():
    epochs = [{"id": "d2-v1", "kind": "visit", "mjd_mid": T1, "det": 2},
              {"id": "d2-v2", "kind": "visit", "mjd_mid": T3, "det": 2}]
    return {"clock": "months", "center": {"ra": RA0, "dec": DEC0}, "pixel_arcsec": 3.08, "size": [293, 293],
            "epochs": epochs, "gaia": [], "known": []}


KNOWN = [{"name": "(12345)", "epoch_id": "d2-OBS_1", "ra": off(3, 0, *LONE_KNOWN)[0], "dec": LONE_KNOWN[1],
          "v": 18.0, "source": "mpc"},
         {"name": "Ceres", "epoch_id": "d2-OBS_1", "ra": off(15, 0, *MOVER_K)[0], "dec": MOVER_K[1],
          "v": 9.0, "source": "mpc"}]


def _find(m, epoch, pos, tol=3.0):
    for d in m["detections"]:
        if d["epoch_id"] == epoch and math.hypot((d["ra"] - pos[0]) * COS * 3600, (d["dec"] - pos[1]) * 3600) < tol:
            return d
    raise AssertionError(f"no detection near {pos} in {epoch}")


def test_hours_classes_tracks_and_ids(root):
    m, reason = M.build(_hours_meta(KNOWN), root=root)
    assert reason is None
    assert (m["zone"], m["stage"], m["clock"]) == ("zz", "final", "hours")
    assert m["zone_box"] == {"ra": [9.0, 11.0], "dec": [19.0, 21.0]}
    assert (m["photos"], m["photos_missing"], m["truncated"]) == (2, 0, False)
    assert m["shifts"] == []
    star = _find(m, "d2-OBS_1", S1)
    assert star["class"] == "star" and star["gaia_id"] == "4242424242424242424" and star["source_id"] == 111
    assert _find(m, "d2-OBS_1", S2)["class"] == "other" and _find(m, "d2-OBS_1", S2)["gaia_id"] is None
    sus = _find(m, "d2-OBS_1", MOVER_A)
    # SOURCE at every peak and OUTLIER never make an artefact; the suspect's reasons pass through
    assert sus["class"] == "suspect" and sus["track_id"] == "zz-5001-86400002"
    assert sus["grade"] == "C" and sus["reasons"] == ["hot_pixel", "edge"]
    ghost_mover = _find(m, "d2-OBS_2", MOVER_C)
    assert ghost_mover["class"] == "artifact" and ghost_mover["reasons"] == ["ghost"] and ghost_mover["track_id"]
    k = _find(m, "d2-OBS_1", MOVER_K)
    assert k["class"] == "known" and k["known_name"] == "Ceres"
    lone = _find(m, "d2-OBS_1", LONE_KNOWN)
    assert lone["class"] == "known" and lone["known_name"] == "(12345)" and lone["track_id"] is None
    # no known[] prediction for the second exposure: the same object's lone dot there is "other"
    assert _find(m, "d2-OBS_2", LONE_KNOWN)["class"] == "other"
    assert _find(m, "d2-OBS_1", LONE_GHOST)["reasons"] == ["ghost"]
    so = _find(m, "d2-OBS_1", LONE_SOURCE_OUTLIER)
    assert so["class"] == "other" and so["reasons"] == []
    assert not any(abs(d["ra"] - FAR[0]) < 0.01 for d in m["detections"])  # outside the frame
    by_id = {t["id"]: t for t in m["tracks"]}
    assert set(by_id) == {"zz-5001-86400002", "zz-m5002-86400001", "zz-m5003-86400001"}
    t = by_id["zz-5001-86400002"]
    assert t["class"] == "suspect" and t["suspect_id"] == t["id"] and t["evidence"] == "two_exposures"
    assert [d["epoch_id"] for d in t["dots"]] == ["d2-OBS_1", "d2-OBS_2"]
    assert t["dots"][0]["mjd"] < t["dots"][1]["mjd"]
    assert by_id["zz-m5003-86400001"]["class"] == "known" and by_id["zz-m5003-86400001"]["known_name"] == "Ceres"
    assert by_id["zz-m5002-86400001"]["class"] == "artifact"
    assert sum(m["counts"].values()) == len(m["detections"]) == 15
    assert set(m["counts"]) == set(M.CLASSES)
    assert m["counts"]["suspect"] == 2 and m["counts"]["known"] == 3 and m["counts"]["star"] == 2
    json.dumps(m, allow_nan=False)  # strict JSON


def test_hours_without_known_a_paired_mover_stays_a_mover(root):
    m, _ = M.build(_hours_meta(), root=root)
    t = {t["id"]: t for t in m["tracks"]}["zz-m5003-86400001"]
    assert t["class"] == "mover" and t["known_name"] is None


def test_months_per_visit_positions_classes_and_shifts(root):
    keys = {"d2-v1": ["OBS_1D2", "OBS_2D2"], "d2-v2": ["OBS_3D2", "OBS_9D2"]}  # OBS_9: no dot file
    m, reason = M.build(_months_meta(), keys, root=root)
    assert reason is None and m["clock"] == "months" and m["tracks"] == []
    assert (m["photos"], m["photos_missing"]) == (3, 1)
    v1 = {d["source_id"]: d for d in m["detections"] if d["epoch_id"] == "d2-v1"}
    v2 = {d["source_id"]: d for d in m["detections"] if d["epoch_id"] == "d2-v2"}
    assert set(v1) == {111, 222, 333} and set(v2) == {111, 333}
    assert v1[111]["class"] == "star" and v1[111]["n_dots"] == 2
    assert v1[111]["snr"] == pytest.approx(math.hypot(300, 310), abs=0.1)
    assert v1[222]["class"] == "artifact" and v1[222]["reasons"] == ["bad_fit", "few_dots"]
    assert v1[333]["class"] == "suspect"
    # visit 2 is a v4 file: its ePSF position (0.5" east) is used; the fast source moved 2" E, 1.5" S
    dx = (v2[333]["ra"] - v1[333]["ra"]) * COS * 3600
    dy = (v2[333]["dec"] - v1[333]["dec"]) * 3600
    assert dx == pytest.approx(2.5, abs=0.02) and dy == pytest.approx(-1.5, abs=0.02)
    assert len(m["shifts"]) == 1  # pm_snr 0.1 is left out
    s = m["shifts"][0]
    dt = (T3 - T1) / 365.25
    assert s["source_id"] == 333 and s["class"] == "suspect" and s["gaia_id"] is None
    assert s["dt_yr"] == pytest.approx(dt, abs=1e-4) and (s["mjd_from"], s["mjd_to"]) == (T1, T3)
    assert s["dra_arcsec"] == pytest.approx(4.0 * dt, abs=1e-3) and s["ddec_arcsec"] == pytest.approx(-3.0 * dt, abs=1e-3)
    assert s["shift_arcsec"] == pytest.approx(5.0 * dt, abs=1e-3) and s["err_arcsec"] == pytest.approx(0.2 * dt, abs=1e-3)
    assert s["pa_deg"] == pytest.approx(math.degrees(math.atan2(4, -3)) % 360, abs=0.1)
    assert (s["ra"], s["dec"]) == (pytest.approx(S3[0], abs=1e-6), pytest.approx(S3[1], abs=1e-6))


def test_cap_keeps_the_interesting_classes_and_counts_everything(root, monkeypatch):
    monkeypatch.setattr(M, "MAX_PER_EPOCH", 3)
    m, _ = M.build(_hours_meta(KNOWN), root=root)
    assert m["truncated"] is True and sum(m["counts"].values()) == 15
    first = [d for d in m["detections"] if d["epoch_id"] == "d2-OBS_1"]
    assert len(first) == 3 and all(d["class"] not in ("star", "other") for d in first)


def test_zone_choice_and_reasons(root, tmp_path):
    t = Path(root) / "results" / "t_tiny"
    t.mkdir(parents=True)
    (t / "summary.json").write_text(json.dumps({"zone": {"name": "t_tiny", "ra": [9.9, 10.1], "dec": [19.9, 20.1]},
                                                "steps": {"link": {}, "crossid": {}}}))
    (Path(root) / "results" / "pilotish").mkdir()
    (Path(root) / "results" / "pilotish" / "summary.json").write_text(json.dumps(
        {"zone": {"name": "pilotish", "ra": [9.5, 10.5], "dec": [19.5, 20.5]}, "steps": {"link": {}, "crossid": {}, "calib": {}}}))
    z, why = M.find_zone(10.0, 20.0, root=root)
    assert z.name == "t_tiny" and z.stage == "provisional" and why is None  # target zone first
    z, _ = M.find_zone(10.3, 20.3, root=root)
    assert z.name == "zz"  # a progress.json zone before an unlisted one
    assert M.find_zone(50.0, 50.0, root=root) == (None, "not_processed")
    assert M.find_zone(35.0, 5.0, root=root) == (None, "processing")
    assert M.find_zone(0, 0, root=root, prefer="zz")[0].name == "zz"
    assert M.find_zone(0, 0, root=root, prefer="nope") == (None, "not_processed")
    # the layer carries the reason
    meta = _hours_meta()
    meta["center"] = {"ra": 50.0, "dec": 50.0}
    meta["epochs"] = [{"id": "d2-OBS_404", "kind": "exposure", "mjd_mid": T1, "det": 2}]  # no dot file either
    assert M.build(meta, root=root) == (None, "not_processed")
    meta["center"] = {"ra": 35.0, "dec": 5.0}
    assert M.build(meta, root=root) == (None, "processing")
    monkeypatch_env = pytest.MonkeyPatch()
    monkeypatch_env.setenv("SKYSHIFT_MEASURED_DOTS_ONLY", "0")
    try:  # dots only off: photos with dot files outside a combined zone say why, as before
        meta["epochs"] = _hours_meta()["epochs"]
        meta["center"] = {"ra": 50.0, "dec": 50.0}
        assert M.build(meta, root=root) == (None, "not_processed")
    finally:
        monkeypatch_env.undo()


def test_zones_found_when_the_listing_shows_no_folders(root, monkeypatch):
    """GCS through pyarrow lists no "folders" under results/: zones not in progress.json are then
    still found by name (EXTRA_ZONES, SKYSHIFT_EXTRA_ZONES) from their summary.json."""
    for name, box in (("t_barnard", [9.95, 10.05]), ("t_other", [9.97, 10.03])):
        z = Path(root) / "results" / name
        z.mkdir(parents=True)
        (z / "summary.json").write_text(json.dumps({"zone": {"name": name, "ra": box, "dec": [19.95, 20.05]},
                                                    "steps": {"link": {}, "crossid": {}}}))
    monkeypatch.setattr(M.Store, "list_dirs", lambda self, key: [])
    assert M.find_zone(10.0, 20.0, root=root)[0].name == "t_barnard"  # in EXTRA_ZONES
    M.reset()
    monkeypatch.setenv("SKYSHIFT_EXTRA_ZONES", "t_other, nope")
    assert M.find_zone(10.0, 20.0, root=root)[0].name == "t_other"  # the smaller target zone
    M.reset()

    def broken(self, key):
        raise OSError("listing down")

    monkeypatch.setattr(M.Store, "list_dirs", broken)
    assert M.find_zone(10.0, 20.0, root=root)[0].name == "t_other"


def test_gcs_prefixes_reads_the_delimiter_listing(monkeypatch):
    class Pages:
        prefixes: set = set()

        def __iter__(self):  # like the storage iterator: prefixes fill in while pages are read
            Pages.prefixes = {"results/hunt/", "results/t_barnard/", "results/"}
            yield from ()

    class Client:
        def list_blobs(self, bucket, prefix, delimiter):
            assert (bucket, prefix, delimiter) == ("nasa-glazing-data", "results/", "/")
            return Pages()

    monkeypatch.setattr(M, "_gcs_client", lambda: Client())
    assert M.gcs_prefixes("nasa-glazing-data", "results/") == ["hunt", "t_barnard"]


@pytest.fixture
def extracted(root):
    """The same photos, but the zone is only extracted: dot files, no results/zz/."""
    import shutil

    progress = Path(root) / "skyrun" / "progress.json"
    p = json.loads(progress.read_text())
    p["zones"][0].update(state="extracted", stage="provisional", final=False, combined_at=None)
    progress.write_text(json.dumps(p))
    shutil.rmtree(Path(root) / "results" / "zz")
    M.reset()
    return root


GAIA = [{"id": "4242424242424242424", "ra": S1[0] + 0.5 / 3600 / COS, "dec": S1[1], "pmra": 0.0, "pmdec": 0.0,
         "parallax": None, "g": 12.0},
        {"id": "999", "ra": off(30, 0, *S2)[0], "dec": S2[1], "pmra": None, "pmdec": None, "parallax": None, "g": 18.0}]


def test_dots_only_hours_extracted(extracted):
    meta = _hours_meta(KNOWN)
    meta["gaia"] = GAIA
    m, reason = M.build(meta, root=extracted)
    assert reason is None
    assert (m["zone"], m["stage"], m["zone_box"]) == ("zz", "extracted", {"ra": [9.0, 11.0], "dec": [19.0, 21.0]})
    assert m["tracks"] == [] and m["shifts"] == [] and (m["photos"], m["photos_missing"]) == (2, 0)
    star = _find(m, "d2-OBS_1", S1)
    assert star["class"] == "star" and star["gaia_id"] == "4242424242424242424" and star["source_id"] is None
    assert _find(m, "d2-OBS_1", S2)["class"] == "other"  # its Gaia neighbour is 30" away
    lone = _find(m, "d2-OBS_1", LONE_KNOWN)
    assert lone["class"] == "known" and lone["known_name"] == "(12345)"
    assert _find(m, "d2-OBS_1", MOVER_K)["class"] == "other"  # Ceres predicted 16" away: beyond 10"
    assert _find(m, "d2-OBS_1", LONE_GHOST)["class"] == "artifact"
    ghosted = _find(m, "d2-OBS_2", MOVER_C)
    assert ghosted["class"] == "artifact" and ghosted["reasons"] == ["ghost"]
    # SOURCE at the peak and OUTLIER are not artefacts
    assert _find(m, "d2-OBS_1", LONE_SOURCE_OUTLIER)["class"] == "other"
    assert _find(m, "d2-OBS_1", MOVER_A)["class"] == "other"
    assert all(d["n_dots"] == 1 and d["track_id"] is None for d in m["detections"])
    assert sum(m["counts"].values()) == len(m["detections"]) == 15
    json.dumps(m, allow_nan=False)


def test_dots_only_cap_keeps_the_brightest_of_every_class(extracted, monkeypatch):
    monkeypatch.setattr(M, "EXTRACTED_MAX_PER_EPOCH", 3)
    meta = _hours_meta(KNOWN)
    meta["gaia"] = GAIA
    m, _ = M.build(meta, root=extracted)
    assert m["truncated"] is True and sum(m["counts"].values()) == 15  # counts before the cap
    first = [d for d in m["detections"] if d["epoch_id"] == "d2-OBS_1"]
    assert [d["snr"] for d in first] == [300.0, 80.0, 50.0]  # the star stays; faint artefacts go
    assert first[0]["class"] == "star"
    monkeypatch.setattr(M, "EXTRACTED_MAX_PER_EPOCH", 400)
    m, _ = M.build(meta, root=extracted)
    assert m["truncated"] is False and len(m["detections"]) == 15


def test_dots_only_months_merges_a_visit_within_2_arcsec(extracted):
    meta = _months_meta()
    meta["gaia"] = GAIA
    keys = {"d2-v1": ["OBS_1D2", "OBS_2D2"], "d2-v2": ["OBS_3D2"]}
    m, reason = M.build(meta, keys, root=extracted)
    assert reason is None and m["stage"] == "extracted" and m["tracks"] == [] and m["shifts"] == []
    s1 = _find(m, "d2-v1", S1)
    assert s1["class"] == "star" and s1["n_dots"] == 2 and s1["snr"] == pytest.approx(math.hypot(300, 310), abs=0.1)
    assert _find(m, "d2-v1", off(0.5, 0, *S1), tol=0.05)  # the weighted mean of its two dots
    assert _find(m, "d2-v1", S3)["n_dots"] == 2 and _find(m, "d2-v2", S1)["n_dots"] == 1
    # a ghost seen twice stays an artefact when every dot of the group is flagged
    assert _find(m, "d2-v1", MOVER_C)["class"] == "artifact"


def test_dots_only_no_dot_files_keeps_the_reason_and_stale_sees_new_dots(extracted):
    meta = _hours_meta()
    meta["epochs"] = [{"id": "d2-OBS_404", "kind": "exposure", "mjd_mid": T1, "det": 2}]
    assert M.build(meta, root=extracted) == (None, "processing")
    hit = {"clock": "hours", "center": {"ra": RA0, "dec": DEC0}, "epochs": meta["epochs"],
           "measured": None, "measured_reason": "processing"}
    assert not M.stale(hit, root=extracted)
    assert M.stale({**hit, "epochs": _hours_meta()["epochs"]}, root=extracted)  # these photos have dots now
    done = {**hit, "measured": {"zone": "zz", "stage": "extracted"}, "measured_reason": None}
    assert not M.stale(done, root=extracted)


def test_no_dots_and_failures_never_raise(root):
    meta = _hours_meta()
    meta["epochs"] = [{"id": "d2-OBS_404", "kind": "exposure", "mjd_mid": T1, "det": 2}]
    assert M.build(meta, root=root) == (None, "no_dots")
    (Path(root) / "results" / "zz" / "crossids.parquet").write_bytes(b"not parquet")
    M.reset()
    loaded = M.load(RA0, DEC0, 0.3, ["OBS_1D2"], root=root)
    assert loaded.error and loaded.reason == "unavailable"
    assert M.build(_hours_meta(), root=root) == (None, "unavailable")
    assert M.assemble(M.Loaded(reason="unavailable", error=True), _hours_meta()) == (None, "unavailable")


def test_stale(root):
    hit = {"center": {"ra": RA0, "dec": DEC0}, "measured": None, "measured_reason": "not_processed"}
    assert M.stale(hit, root=root)  # a zone has results here now
    assert not M.stale({**hit, "center": {"ra": 50.0, "dec": 50.0}}, root=root)
    assert not M.stale({**hit, "measured_reason": "no_dots"}, root=root)
    assert not M.stale({**hit, "measured": {"zone": "zz", "stage": "final"}, "measured_reason": None}, root=root)
    assert M.stale({**hit, "measured": {"zone": "zz", "stage": "provisional"}, "measured_reason": None}, root=root)


def test_photo_keys_and_exposure_ids():
    assert M._key_of_exposure("d2-2025W42_2A_0213_1") == "2025W42_2A_0213_1D2"
    assert M._key_of_exposure("d2-v1") == "v1D2" and M._key_of_exposure("x") is None
    meta = _months_meta()
    # every photo of a visit (a static stack holds 18-43), spread over it only beyond the cap
    keys = M.photo_keys({"d2-v1": [f"O{i}D2" for i in range(43)], "d2-v2": ["P1D2"]}, meta)
    assert keys == [f"O{i}D2" for i in range(43)] + ["P1D2"]
    n = M.MONTHS_MAX_PHOTOS_PER_VISIT
    keys = M.photo_keys({"d2-v1": [f"O{i}D2" for i in range(3 * n)], "d2-v2": ["P1D2"]}, meta)
    assert len(keys) == n + 1 and keys[0] == "O0D2" and f"O{3 * n - 1}D2" in keys


def test_months_reads_every_photo_of_the_visit(root):
    """A faint source dotted in 1 of a visit's 19 photos is a detection of that visit (the plate stacks all 19). With 12
    dot files a visit (spread), photo 1 was never read and the source had no circle."""
    d = Path(root) / "dots"
    keys = [f"V{i:02d}D2" for i in range(19)]
    for i, k in enumerate(keys):
        pts = [(off(0.3, 0, *S1), 200.0, 0, 0)]
        if i == 1:
            pts.append((off(0, 0.4, *S2), 5.5, 0, 0))
        _dots(d / f"{k}.parquet", T1 + i * 0.1, pts)
    meta = {**_months_meta(), "epochs": [{"id": "d2-v1", "kind": "visit", "mjd_mid": T1 + 0.9, "det": 2}]}
    m, reason = M.build(meta, {"d2-v1": keys}, root=root)
    assert reason is None and (m["photos"], m["photos_missing"]) == (19, 0)
    got = {x["source_id"]: x for x in m["detections"]}
    assert got[111]["n_dots"] == 19 and got[111]["class"] == "star"
    assert got[222]["n_dots"] == 1 and got[222]["snr"] == pytest.approx(5.5, abs=0.05)
    assert "V01D2" not in M._spread(keys, 12)  # what the old cap of 12 read


def test_fragments_tags_v3_11(root):
    """results/<zone>/fragments.parquet (contracts v3.11): a bright_fragment becomes an artifact naming its parent star,
    a blend keeps its class and names its Gaia stars; without the file nothing changes (no new fields)."""
    m0, _ = M.build(_hours_meta(KNOWN), root=root)
    assert all("parent_gaia_id" not in d and "blend_gaia_ids" not in d for d in m0["detections"])
    _write(Path(root) / "results" / "zz" / "fragments.parquet", {
        "source_id": np.array([111, 222, 333], np.int64), "tag": ["blend", "bright_fragment", ""],
        "parent_gaia_id": np.array([-1, 4242424242424242424, -1], np.int64),
        "blend_ids": pa.array([[4242424242424242424, 7], [], []], pa.list_(pa.int64()))})
    M.reset()
    m, reason = M.build(_hours_meta(KNOWN), root=root)
    assert reason is None
    frag = _find(m, "d2-OBS_1", S2)
    assert frag["class"] == "artifact" and frag["reasons"] == ["bright_fragment"] and frag["source_id"] == 222
    assert frag["parent_gaia_id"] == "4242424242424242424" and frag["blend_gaia_ids"] is None
    star = _find(m, "d2-OBS_1", S1)
    assert star["class"] == "star" and star["reasons"] == ["blend"]
    assert star["blend_gaia_ids"] == ["4242424242424242424", "7"] and star["parent_gaia_id"] is None
    sus = _find(m, "d2-OBS_1", MOVER_A)  # not a linked source: unchanged, the fields null
    assert sus["class"] == "suspect" and sus["parent_gaia_id"] is None and sus["blend_gaia_ids"] is None
    assert m["counts"]["artifact"] == m0["counts"]["artifact"] + 1 and m["counts"]["other"] == m0["counts"]["other"] - 1
    json.dumps(m, allow_nan=False)
    keys = {"d2-v1": ["OBS_1D2", "OBS_2D2"], "d2-v2": ["OBS_3D2"]}
    mm, _ = M.build(_months_meta(), keys, root=root)
    v1 = {d["source_id"]: d for d in mm["detections"] if d["epoch_id"] == "d2-v1"}
    assert v1[222]["class"] == "artifact" and v1[222]["reasons"] == ["bad_fit", "few_dots", "bright_fragment"]
    assert v1[222]["parent_gaia_id"] == "4242424242424242424"
    assert v1[111]["class"] == "star" and v1[111]["reasons"] == ["blend"]
    assert v1[333]["class"] == "suspect" and v1[333]["reasons"] == [] and v1[333]["blend_gaia_ids"] is None


def test_duplicate_reads_as_a_piece_of_its_star(root):
    """A `duplicate` (a second source of one star, contracts/dots.md `fragments`) is drawn like a bright_fragment: an
    artifact whose parent_gaia_id is that star, so the site draws one circle for the star (no new API reason)."""
    _write(Path(root) / "results" / "zz" / "fragments.parquet", {
        "source_id": np.array([111, 222], np.int64), "tag": ["", "duplicate"],
        "duplicate_of": np.array([-1, 111], np.int64), "parent_gaia_id": np.array([-1, 4242424242424242424], np.int64),
        "blend_ids": pa.array([[], []], pa.list_(pa.int64()))})
    M.reset()
    m, reason = M.build(_hours_meta(KNOWN), root=root)
    assert reason is None
    dup = _find(m, "d2-OBS_1", S2)
    assert dup["class"] == "artifact" and dup["reasons"] == ["bright_fragment"] and dup["source_id"] == 222
    assert dup["parent_gaia_id"] == "4242424242424242424"
    assert _find(m, "d2-OBS_1", S1)["class"] == "star"
    keys = {"d2-v1": ["OBS_1D2", "OBS_2D2"], "d2-v2": ["OBS_3D2"]}
    mm, _ = M.build(_months_meta(), keys, root=root)
    v1 = {d["source_id"]: d for d in mm["detections"] if d["epoch_id"] == "d2-v1"}
    assert v1[222]["class"] == "artifact" and v1[222]["reasons"][-1] == "bright_fragment"
    assert v1[222]["parent_gaia_id"] == "4242424242424242424"


def test_zone_reads_the_dots_folder_its_link_read(root):
    """A target zone re-extracted with finer deblending (pipeline config DOTS_FINE_ZONES) is read from the folder its
    link recorded (summary.json steps.link.dots_dir), never from dots/; a zone without the entry reads dots/."""
    import shutil

    r = Path(root)
    shutil.copytree(r / "results" / "zz", r / "results" / "t_zz")
    steps = {"link": {"dots_dir": "dots_deblend1e-4"}, "crossid": {}, "calib": {}}
    (r / "results" / "t_zz" / "summary.json").write_text(json.dumps({"zone": {"ra": [9.5, 10.5], "dec": [19.5, 20.5]},
                                                                        "steps": steps}))
    neighbour = off(-30, 40)  # split off a bright star's glow: only in the zone's own folder
    fine = r / "dots_deblend1e-4"
    _dots(fine / "OBS_1D2.parquet", T1, [(off(0.5, 0, *S1), 300.0, 0, 0), (neighbour, 40.0, 0, 0)])
    _dots(fine / "OBS_2D2.parquet", T2, [(off(0.5, 0, *S1), 310.0, 0, 0), (off(0.3, 0, *neighbour), 41.0, 0, 0)])
    M.reset()
    m, reason = M.build(_hours_meta(), root=root)
    assert reason is None and m["zone"] == "t_zz"
    assert m["photos"] == 2 and m["photos_missing"] == 0
    assert _find(m, "d2-OBS_1", neighbour) and _find(m, "d2-OBS_2", S1)["class"] == "star"
    assert len(m["detections"]) == 4  # dots/ (10 and 6 dots) is not read for this zone
    m2, _ = M.build(_hours_meta(), zone="zz", root=root)
    assert m2["zone"] == "zz" and len(m2["detections"]) == 15
    assert M._zone_from_summary("old", {"zone": {"ra": [0, 1], "dec": [0, 1]}, "steps": {"link": {}}}).dots_dir == "dots"


def _stack_sources(root: str) -> dict:
    """results/zz/stack_sources.parquet (contracts/dots.md, api.md v3.13): two visits at T1 and T3."""
    new = off(30, 30)      # stack only: S/N 8 in visit 0, 3 in visit 1; a free-fit position in visit 0 (0.4" E)
    glow = off(65, 60)     # stack only, in a bright source's artifact zone
    # relinked: the table says no single-photo source, but the zone's linked sources now have one 1.5″ away
    pts = {"new": new, "glow": glow, "linked": S1, "moving": off(-30, 30), "far": FAR, "relinked": off(1.5, 0, *S2)}
    nan = float("nan")
    _write(Path(root) / "results" / "zz" / "stack_sources.parquet", {
        "stack_id": np.arange(6, dtype=np.int64),
        "ra": np.array([p[0] for p in pts.values()]), "dec": np.array([p[1] for p in pts.values()]),
        "source_id": np.array([-1, -1, 111, -1, -1, -1], np.int64),
        "transient": np.array([False, False, False, True, False, False]),
        "artifact_zone": np.array([False, True, False, False, False, False]),
        "n_photos": np.array([40, 40, 40, 40, 40, 40], np.int32),
        "visits": pa.array([[0, 1]] * 6, pa.list_(pa.int16())),
        "mjd_visit": pa.array([[T1 + 0.5, T3 - 0.5]] * 6, pa.list_(pa.float64())),
        "snr_visit": pa.array([[8.0, 3.0], [6.0, 7.0], [300.0, 300.0], [20.0, nan], [50.0, 50.0], [9.0, 9.0]],
                              pa.list_(pa.float32())),
        "flux_visit_ujy": pa.array([[12.0, 5.0], [10.0, 11.0], [900.0, 900.0], [30.0, nan], [80.0, 80.0], [9.0, 9.0]],
                                   pa.list_(pa.float32())),
        "visits_free": pa.array([[0], [], [0, 1], [], [], []], pa.list_(pa.int16())),
        "ra_visit": pa.array([[off(0.4, 0, *new)[0]], [], [S1[0], S1[0]], [], [], []], pa.list_(pa.float64())),
        "dec_visit": pa.array([[new[1]], [], [S1[1], S1[1]], [], [], []], pa.list_(pa.float64())),
        "gaia_ref_id": ["gaia:77", "", "gaia:4242424242424242424", "", "", ""],
    })
    return pts


def test_stack_class_v3_13(root, monkeypatch):
    """results/<zone>/stack_sources.parquet: a stack-only source is class stack in the visits where its forced S/N is
    >= 5 (at its free-fit position there), one in a bright source's artifact zone is an artifact (stack_near_bright);
    linked (by the table or by the zone's linked sources now), transient and far ones are not drawn. counts.stack only
    on the months clock with the table, and only with SKYSHIFT_MEASURED_STACK=1 (until the site knows the class)."""
    keys = {"d2-v1": ["OBS_1D2", "OBS_2D2"], "d2-v2": ["OBS_3D2"]}
    m0, _ = M.build(_months_meta(), keys, root=root)
    assert "stack" not in m0["counts"]
    cached = {"center": {"ra": RA0, "dec": DEC0}, "clock": "months", "measured": m0, "measured_reason": None}
    assert not M.stale(cached, root=root)
    pts = _stack_sources(root)
    M.reset()
    off_, _ = M.build(_months_meta(), keys, root=root)  # the switch is off by default: nothing changes
    assert off_ == m0 and not M.stale(cached, root=root)
    monkeypatch.setenv("SKYSHIFT_MEASURED_STACK", "1")
    M.reset()
    assert M.stale(cached, root=root)  # built before the table: rebuilt
    m, reason = M.build(_months_meta(), keys, root=root)
    assert reason is None and not M.stale({**cached, "measured": m}, root=root)
    st = [d for d in m["detections"] if d["class"] == "stack"]
    assert [d["epoch_id"] for d in st] == ["d2-v1"]
    d = st[0]
    assert (d["snr"], d["flux_ujy"], d["n_dots"], d["source_id"], d["gaia_id"], d["reasons"]) == (
        8.0, 12.0, 40, None, "77", [])
    assert _find(m, "d2-v1", off(0.4, 0, *pts["new"]), tol=0.05) is d
    art = [d for d in m["detections"] if "stack_near_bright" in d["reasons"]]
    assert sorted(d["epoch_id"] for d in art) == ["d2-v1", "d2-v2"] and {d["class"] for d in art} == {"artifact"}
    assert m["counts"]["stack"] == 1 and m["counts"]["artifact"] == m0["counts"]["artifact"] + 2
    assert sum(m["counts"].values()) == sum(m0["counts"].values()) + 3
    json.dumps(m, allow_nan=False)
    h, _ = M.build(_hours_meta(), root=root)
    assert "stack" not in h["counts"] and all(d["class"] != "stack" for d in h["detections"])


def test_shape_fit_companion_is_its_own_detection_v3_12(root):
    """results/<zone>/shape_candidates.parquet with the two-source fit (contracts v3.12): a split companion is drawn
    on the months clock as its own `other` detection (reason split_by_fit, no source, no dots) beside its source in
    every visit, at that visit's own fitted position; flux = the source's × the ratio in the epoch's detector. A row the
    fit didn't split, the hours clock, and a file without the fit columns add nothing."""
    keys = {"d2-v1": ["OBS_1D2", "OBS_2D2"], "d2-v2": ["OBS_3D2"]}
    m0, _ = M.build(_months_meta(), keys, root=root)
    z = Path(root) / "results" / "zz"
    _write(z / "shape_candidates.parquet", {"source_id": np.array([111, 222], np.int64), "why": ["a", "b"]})  # pre-fit
    M.reset()
    m1, _ = M.build(_months_meta(), keys, root=root)
    assert m1["detections"] == m0["detections"]
    c1, c2 = off(0, -10, *S1), off(0.5, -10, *S1)  # visit 1, visit 2 (0.5" apart: it stays put)
    nan = float("nan")
    _write(z / "shape_candidates.parquet", {
        "source_id": np.array([111, 222], np.int64), "companion_split": np.array([True, False]),
        "companion_ra": np.array([off(0.25, -10, *S1)[0], S2[0]]), "companion_dec": np.array([c1[1], S2[1]]),
        "companion_flux_ratio": np.array([0.3, 0.1]),
        **{f"companion_flux_ratio_d{d}": np.array([0.25 if d == 2 else nan, 0.1]) for d in range(1, 7)},
        **{f"companion_snr_d{d}": np.array([30.0 if d == 2 else nan, 2.0]) for d in range(1, 7)},
        "companion_visit_mjd": pa.array([[T1 + 1, T3 - 1], []], pa.list_(pa.float64())),
        "companion_visit_ra": pa.array([[c1[0], c2[0]], []], pa.list_(pa.float64())),
        "companion_visit_dec": pa.array([[c1[1], c2[1]], []], pa.list_(pa.float64()))})
    M.reset()
    m, reason = M.build(_months_meta(), keys, root=root)
    assert reason is None
    comp = [d for d in m["detections"] if "split_by_fit" in d["reasons"]]
    assert len(comp) == 2 and {d["epoch_id"] for d in comp} == {"d2-v1", "d2-v2"}
    star = {d["epoch_id"]: d for d in m["detections"] if d["source_id"] == 111}
    for d, pos in zip(sorted(comp, key=lambda d: d["epoch_id"]), (c1, c2)):
        assert d["class"] == "other" and d["source_id"] is None and d["gaia_id"] is None and d["n_dots"] == 0
        assert _find(m, d["epoch_id"], pos, tol=0.05) is d
        assert d["flux_ujy"] == pytest.approx(star[d["epoch_id"]]["flux_ujy"] * 0.25, rel=1e-3) and d["snr"] == 30.0
    assert m["counts"]["other"] == m0["counts"]["other"] + 2
    json.dumps(m, allow_nan=False)
    mh, _ = M.build(_hours_meta(), root=root)
    assert not any("split_by_fit" in d["reasons"] for d in mh["detections"])


def test_stack_enabled_per_zone(monkeypatch):
    """SKYSHIFT_MEASURED_STACK: unset/0 off, 1 every zone, else only the zones listed ("+", "," or spaces)."""
    from skyshift_server import measured as M
    monkeypatch.delenv("SKYSHIFT_MEASURED_STACK", raising=False)
    assert not M.stack_enabled("t_barnard")
    monkeypatch.setenv("SKYSHIFT_MEASURED_STACK", "1")
    assert M.stack_enabled("t_rdra") and M.stack_enabled("t_barnard")
    monkeypatch.setenv("SKYSHIFT_MEASURED_STACK", "t_barnard+t_rwaur+t_epsindib+t_wise0855")
    assert M.stack_enabled("t_barnard") and M.stack_enabled("t_wise0855")
    assert not M.stack_enabled("t_rdra") and not M.stack_enabled("hunt")
