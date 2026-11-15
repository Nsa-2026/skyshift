"""scripts/precompute helpers (no network)."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "precompute"))
import precompute  # noqa: E402
from skyshift_server import known  # noqa: E402
from test_measured import root  # noqa: E402,F401 - the synthetic data root fixture


def _row(det: int, mjd: float, ra: float = 10.0, dec: float = 5.0):
    return {"image": precompute._image(f"X_{mjd}", det, mjd - 0.0007, mjd + 0.0007, "https://irsa.example/a.fits",
                                       "dr1/a.fits", "spherex_dr1"), "ra": ra, "dec": dec, "v": 11.0}


def test_clean_makes_strict_json():
    out = precompute.clean({"a": float("nan"), "b": [np.float32(1.5), np.int64(3), float("inf")], "c": None})
    assert out == {"a": None, "b": [1.5, 3, None], "c": None}


def test_star_targets_are_centred_near_the_middle_of_the_data():
    t = {x["slug"]: x for x in precompute.star_targets()}
    assert set(t) == {"barnard", "epsindib", "wise0855", "rwaur", "rdra"}
    assert (t["barnard"]["ra"], t["barnard"]["dec"]) == (269.4462, 4.76883)
    # Epsilon Indi B moves 4.7"/yr: about 47" from its 2016.0 Gaia position
    assert 40 < abs(t["epsindib"]["dec"] - -56.79381206688221) * 3600 * 1.9 < 55
    assert t["rdra"]["dec"] == pytest.approx(66.755, abs=1e-3)


def test_densest_window_and_image():
    rows = [_row(2, 60900.0 + i * 0.07) for i in range(5)] + [_row(2, 60950.0 + i * 0.07) for i in range(9)]
    rows += [_row(5, 60900.0 + i * 0.01) for i in range(20)]
    win = precompute.densest_window(rows, 2, 1.0)
    assert len(win) == 9 and win[0]["image"].mjd == pytest.approx(60950.0)
    im = win[0]["image"]
    assert im.det == 2 and im.s3_url == "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/dr1/a.fits"
    assert precompute.densest_window(rows, 3, 1.0) == []


def _rwaur_images():
    """RW Aurigae's exposures (search of 14 Nov) in D2 and D5: D2 has 19 in visit 2 and 18 in visit 3,
    D5 19 and 19, so the hours clock's own rule picks visit 2 for D2 and visit 3 for D5."""
    from skyshift_server import search

    out = []
    for det, counts in ((2, {1: 18, 2: 19, 3: 18}), (5, {1: 18, 2: 19, 3: 19})):
        for v, n in counts.items():
            out += [search.Image("spherex_dr1", f"V{v}_{k}", det, 0.0, 0.0, 60920.0 + 180 * v + k, "", None,
                                 visit=v) for k in range(n)]
    return out


def test_hours_files_show_one_visit_for_every_detector(tmp_path, monkeypatch):
    """D2+D5 are exposed at the same moment: an hours file shows the same visit in both (14 Nov site
    review: RW Aurigae's frames_hours_20.json had D2 from visit 2 and D5 from visit 3)."""
    from skyshift_server import blink, search

    images = _rwaur_images()
    assert blink.best_hours_visit([im for im in images if im.det == 2]) == 2
    assert blink.best_hours_visit([im for im in images if im.det == 5]) == 3  # the bug, per detector
    monkeypatch.setattr(search, "search", lambda ra, dec, cache=None: search.SearchResult(ra, dec, "2026-11-14", images))
    t = {"slug": "rwaur", "name": "RW Aurigae", "ra": 76.95609, "dec": 30.40113}
    assert precompute.set_visit(t, "hours", [2, 5], cache=None) == 2
    assert precompute.set_visit(t, "hours", [5, 2], cache=None) == 3  # the file's first detector leads
    assert precompute.set_visit(t, "hours", [1, 2], cache=None) == 2  # ... the first one seen here
    assert precompute.set_visit(t, "months", [2, 5], cache=None) is None  # every visit

    calls = []

    def fake_build_frames(ra, dec, fov, clock, det, *, visit=None, **kw):
        calls.append((clock, det, visit))
        ep = {"id": f"d{det}-V{visit}_0", "mjd_mid": 61000.0 + det, "det": det}
        return blink.Frames({"clock": clock, "center": {"ra": ra, "dec": dec}, "epochs": [ep], "known": [],
                             "gaia": []}, {}, [], {ep["id"]: []})

    monkeypatch.setattr(blink, "build_frames", fake_build_frames)
    monkeypatch.setattr(precompute, "OUT", tmp_path)
    entry = precompute.build_set(t, "hours:20", [2, 5], cache=None)
    assert calls == [("hours", 2, 2), ("hours", 5, 2)] and entry["dets"] == [2, 5]
    calls.clear()
    precompute.build_set(t, "months:5", [2, 5], cache=None)
    assert calls == [("months", 2, None), ("months", 5, None)]


def test_comet_rows_come_from_the_irsa_table():
    rows = precompute.comet_rows()
    assert len(rows) == 258  # 259 images, one in neither DR1 nor QR3
    assert {r["image"].det for r in rows} == {1, 2, 3, 4, 5, 6}
    seq = [r for r in rows if precompute.COMET_MJD[0] < r["image"].mjd < precompute.COMET_MJD[1]]
    assert 30 < len(seq) < 80 and all(r["image"].visit == 2 for r in seq)


def test_refresh_known_rewrites_only_known(tmp_path, monkeypatch):
    import json

    folder = tmp_path / "star"
    frames = {"clock": "months", "center": {"ra": 10.0, "dec": 5.0}, "pixel_arcsec": 2.05, "size": [146, 146],
              "epochs": [{"id": "d2-v1", "mjd_mid": 60900.5, "det": 2}, {"id": "d2-v2", "mjd_mid": 61090.5, "det": 2}],
              "gaia": [], "known": [{"name": "Old entry", "epoch_id": "d2-v1", "ra": 10.0, "dec": 5.0, "v": 7.0,
                                     "source": "mpc"}], "data_release": "DR1+QR3", "archive_checked": "2026-11-14"}
    n = precompute.write_json(folder / "frames_months_5.json", frames, compact=True)
    precompute.write_json(folder / "index.json", {"slug": "star", "frames": [
        {"file": "frames_months_5.json", "clock": "months", "fov": 5, "dets": [2], "epochs": 2, "bytes": n + 1000}],
        "own": "frames_months_5.json"})
    calls = []

    def fake_near(ra, dec, r, mjds, *, epoch_ids=None, cache=None):
        calls.append((ra, dec, r, list(mjds), list(epoch_ids)))
        return [{"name": "Vesta", "epoch_id": epoch_ids[1], "ra": ra, "dec": dec, "v": 7.1, "source": "mpc"}]

    monkeypatch.setattr(precompute, "OUT", tmp_path)
    monkeypatch.setattr(known, "near", fake_near)
    precompute.refresh_known("star", cache=None)
    assert calls == [(10.0, 5.0, pytest.approx(5 / 2 * 2**0.5), [60900.5, 61090.5], ["d2-v1", "d2-v2"])]
    out = json.loads((folder / "frames_months_5.json").read_text())
    assert out == {**frames, "known": [{"name": "Vesta", "epoch_id": "d2-v2", "ra": 10.0, "dec": 5.0, "v": 7.1,
                                        "source": "mpc"}]}
    assert (folder / "frames.json").read_bytes() == (folder / "frames_months_5.json").read_bytes()
    entry = json.loads((folder / "index.json").read_text())["frames"][0]
    assert entry["bytes"] == (folder / "frames_months_5.json").stat().st_size + 1000  # frame files unchanged


def test_refresh_measured_rewrites_only_measured(tmp_path, monkeypatch, root):  # noqa: F811 - pytest fixture
    """--measured-only on the synthetic zone of test_measured.py: hours keys from the epoch ids, months
    keys from the search (every exposure of the visit); the rest of each file stays as it was."""
    import json

    from skyshift_server import measured, search
    from test_measured import T1, T3, _hours_meta, _months_meta

    monkeypatch.setattr(measured, "DATA_ROOT", root)
    folder = tmp_path / "star"
    files = {"frames_hours_20.json": {**_hours_meta(), "data_release": "DR1+QR3", "archive_checked": "2026-11-14"},
             "frames_months_15.json": {**_months_meta(), "data_release": "DR1+QR3", "archive_checked": "2026-11-14",
                                       "measured": None, "measured_reason": "not_processed"}}
    entries = []
    for name, meta in files.items():
        n = precompute.write_json(folder / name, meta, compact=True)
        entries.append({"file": name, "clock": meta["clock"], "fov": 20 if "hours" in name else 15, "dets": [2],
                        "epochs": 2, "bytes": n + 500})
    precompute.write_json(folder / "index.json", {"slug": "star", "frames": entries, "own": "frames_months_15.json"})

    def img(obs, mjd, visit, det=2):
        return search.Image("spherex_dr1", obs, det, mjd, mjd, mjd, "", None, visit=visit)

    images = [img("OBS_1", T1, 1), img("OBS_2", T1 + 0.0015, 1), img("OBS_3", T3, 2), img("OBS_3", T3, 2, det=5)]
    monkeypatch.setattr(search, "search", lambda ra, dec, cache=None: search.SearchResult(ra, dec, "2026-11-14", images))
    monkeypatch.setattr(precompute, "OUT", tmp_path)
    precompute.refresh_measured("star", cache=None)
    hours = json.loads((folder / "frames_hours_20.json").read_text())
    months = json.loads((folder / "frames_months_15.json").read_text())
    for name, out in (("frames_hours_20.json", hours), ("frames_months_15.json", months)):
        assert {k: v for k, v in out.items() if not k.startswith("measured")} == \
            {k: v for k, v in files[name].items() if not k.startswith("measured")}
        assert out["measured_reason"] is None and out["measured"]["zone"] == "zz"
    assert hours["measured"]["clock"] == "hours" and len(hours["measured"]["tracks"]) == 3
    assert months["measured"]["photos"] == 3 and len(months["measured"]["shifts"]) == 1
    assert (folder / "frames.json").read_bytes() == (folder / "frames_months_15.json").read_bytes()
    index = json.loads((folder / "index.json").read_text())
    assert [e["bytes"] for e in index["frames"]] == [(folder / e["file"]).stat().st_size + 500 for e in index["frames"]]

    # a zone that is still running: the files say so, and a read failure writes nothing
    monkeypatch.setattr(measured, "DATA_ROOT", str(tmp_path / "empty"))
    measured.reset()
    precompute.refresh_measured("star", cache=None)
    assert json.loads((folder / "frames_hours_20.json").read_text())["measured_reason"] == "not_processed"
    monkeypatch.setattr(measured, "build", lambda *a, **k: (None, "unavailable"))
    before = (folder / "frames_hours_20.json").read_bytes()
    with pytest.raises(RuntimeError):
        precompute.refresh_measured("star", cache=None)
    assert (folder / "frames_hours_20.json").read_bytes() == before
