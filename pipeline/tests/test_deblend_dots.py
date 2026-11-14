"""Finer deblending for the featured-target zones (15 Nov): their own dot-file folder, the setting in each file's
metadata, existing dots/ and forced/ files never touched, and every later step reading the folder link read."""

import json

import numpy as np
import pandas as pd
import pytest

import skyshift_pipeline.config as C
from skyshift_core import detect
from skyshift_pipeline import dots as D
from skyshift_pipeline.cli import main, step_dots_dir
from skyshift_pipeline.photolist import add_sessions
from skyshift_pipeline.store import Store, dots_dir, dots_rel, linked_dots_dir, photolist_rel, results_rel, use_dots_dir

from conftest import t
from test_cli import STAR, dots_df
from test_dots_extras import OBS, synthetic_mef, write_refcat
from test_photolist import square

FEATURED = ("t_rdra", "t_barnard", "t_rwaur", "t_epsindib", "t_wise0855")


def test_zone_settings_and_folder_names():
    assert C.DOTS_DEBLEND_CONT == 0.005 and C.DOTS_FINE_DEBLEND_CONT == 1e-4
    for z in FEATURED:
        assert C.zone_dots(z) == ("dots_deblend1e-4", 1e-4)
    for z in ("hunt", "pilot", "t_nep", "ecl26n3", "ecl07n1", None):  # the sky run is unchanged
        assert C.zone_dots(z) == ("dots", 0.005)
    assert C.dots_dir_for(0.005) == "dots"
    assert C.dots_dir_for(5e-5) == "dots_deblend5e-5"
    assert C.dots_dir_for(2.5e-4) == "dots_deblend2.5e-4"
    assert C.dots_dir_for(0.001) == "dots_deblend1e-3"


def test_fine_setting_finds_a_star_in_a_bright_stars_glow():
    """A faint star 18 px from a bright one with a wide halo holds < 0.1% of the merged blob's light: the sky run's
    setting keeps it inside the bright star, the featured zones' splits it off (R Dra D4 on real photos)."""
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:101, 0:101].astype(float)
    img = 5000.0 / (1 + (np.hypot(xx - 50, yy - 50) / 2.0) ** 2) ** 1.5
    img += 20.0 * np.exp(-((xx - 68) ** 2 + (yy - 50) ** 2) / 2.0)
    img += rng.normal(0, 1.0, img.shape)
    found = {}
    for zone in ("hunt", "t_rdra"):
        d = detect(img, None, variance=np.ones_like(img), thresh=C.DETECT_THRESH, box=128,
                   deblend_cont=C.zone_dots(zone)[1])
        near = np.hypot(d["x"] - 68, d["y"] - 50) < 1.5
        found[zone] = bool(near.any()) and float(d["snr"][near].max()) > C.DOT_MIN_SNR
    assert found == {"hunt": False, "t_rdra": True}


def test_dots_table_passes_the_setting(monkeypatch):
    seen = []

    def fake_detect(*a, **k):
        seen.append(k.get("deblend_cont"))
        return np.zeros(0, dtype=[("x", "f8"), ("y", "f8"), ("snr", "f8")])

    monkeypatch.setattr(D, "detect", fake_detect)
    l2 = type("L2", (), {"data": np.zeros((4, 4)), "flags": None, "variance": None})()
    D.dots_table(l2, "X", 1)
    D.dots_table(l2, "X", 1, deblend_cont=1e-4)
    assert seen == [0.005, 1e-4]


@pytest.fixture
def fine_zone(monkeypatch):
    monkeypatch.setitem(C.ZONES, "cygtest", C.Zone("cygtest", 303.0, 305.0, 41.0, 42.5))
    D._EPSF_CACHE.clear()
    D._REFCAT.clear()
    yield "cygtest"
    D._EPSF_CACHE.clear()
    D._REFCAT.clear()


def test_fine_zone_writes_its_own_folder_and_never_touches_existing_files(tmp_path, fine_zone, monkeypatch):
    src = synthetic_mef(tmp_path)
    root = tmp_path / "root"
    store = Store(str(root))
    write_refcat(store, fine_zone)
    # the sky run's extraction first (dots/ and forced/), as on the bucket
    st0 = D.process_photo(str(root), OBS, 1, str(src), 3, 1, fine_zone)
    assert st0["status"] == "done"
    old_dots = (root / dots_rel(OBS, 1, "dots")).read_bytes()
    old_forced = (root / D.forced_rel(OBS, 1)).read_bytes()
    assert D.photo_meta(root / dots_rel(OBS, 1, "dots"))["detect"] == {
        "thresh": C.DETECT_THRESH, "min_snr": C.DOT_MIN_SNR, "deblend_cont": 0.005, "dots_dir": "dots"}
    # then the zone switches to the fine setting: a new file in its own folder; dots/ and forced/ unchanged
    monkeypatch.setattr(C, "DOTS_FINE_ZONES", (fine_zone,))
    st = D.process_photos(str(root), [(OBS, 1, str(src), 3, 1)], fine_zone)[0]
    assert st["status"] == "done" and st.get("forced_kept") is True and st["forced_bytes"] == 0
    new = root / dots_rel(OBS, 1, "dots_deblend1e-4")
    meta = D.photo_meta(new)
    assert meta["detect"]["deblend_cont"] == 1e-4 and meta["detect"]["dots_dir"] == "dots_deblend1e-4"
    assert meta["n_forced"] > 100 and meta["calibration"]["astrometry_vs_gaia"]["n"] >= 10
    assert (root / dots_rel(OBS, 1, "dots")).read_bytes() == old_dots
    assert (root / D.forced_rel(OBS, 1)).read_bytes() == old_forced
    a, b = pd.read_parquet(root / dots_rel(OBS, 1, "dots")), pd.read_parquet(new)
    assert list(a.columns) == list(b.columns) and len(b) >= len(a)
    assert (np.diff(b["flux_ujy"].to_numpy()) <= 0).all()  # same row order rule: brightest first
    # restart: skipped in the zone's folder
    assert D.process_photo(str(root), OBS, 1, str(src), 3, 1, fine_zone)["status"] == "skipped"


def test_fine_zone_writes_forced_when_missing(tmp_path, fine_zone, monkeypatch):
    src = synthetic_mef(tmp_path)
    root = tmp_path / "root"
    write_refcat(Store(str(root)), fine_zone)
    monkeypatch.setattr(C, "DOTS_FINE_ZONES", (fine_zone,))
    st = D.process_photo(str(root), OBS, 1, str(src), 3, 1, fine_zone)
    assert st["status"] == "done" and not st.get("forced_kept") and st["forced_bytes"] > 0
    assert (root / D.forced_rel(OBS, 1)).exists() and (root / dots_rel(OBS, 1, "dots_deblend1e-4")).exists()
    assert not (root / dots_rel(OBS, 1, "dots")).exists()


def test_dots_dir_follows_link(tmp_path):
    store = Store(str(tmp_path))
    assert dots_dir() == "dots" and dots_rel("A", 2) == "dots/AD2.parquet"
    use_dots_dir("dots_deblend1e-4")
    assert dots_rel("A", 2) == "dots_deblend1e-4/AD2.parquet"
    zone = C.ZONES["t_rdra"]
    assert linked_dots_dir(store, "t_rdra") == "dots"  # never linked
    store.write_json(results_rel("t_rdra", "summary.json"), {"steps": {"link": {"fixed": 3}}})
    assert linked_dots_dir(store, "t_rdra") == "dots"  # linked before 15 Nov: dots/
    assert step_dots_dir(store, zone, "dots") == step_dots_dir(store, zone, "link") == "dots_deblend1e-4"
    assert step_dots_dir(store, zone, "spectra") == "dots"  # link hasn't read the new folder yet
    store.write_json(results_rel("t_rdra", "summary.json"), {"steps": {"link": {"dots_dir": "dots_deblend1e-4"}}})
    assert step_dots_dir(store, zone, "spectra") == step_dots_dir(store, zone, "suspects") == "dots_deblend1e-4"
    assert step_dots_dir(store, C.ZONES["hunt"], "link") == "dots"


def test_steps_after_link_read_the_folder_link_read(tmp_path, monkeypatch):
    """A fine zone whose dots/ files differ from its own folder's: link, spectra and later steps all use its folder
    (members.dot_row points into it), and link records it in summary.json."""
    monkeypatch.setattr(C, "DOTS_FINE_ZONES", ("pilot",))
    folder = C.zone_dots("pilot")[0]
    store = Store(str(tmp_path))
    rows = [dict(obs_id=f"S{s}E{e}", detector=1, mjd=t(s, e), s_region=square(122.3, 16.15), collection="spherex_dr1",
                 url="", s3_key="", size_bytes=1) for s in range(5) for e in range(2)]
    photos = add_sessions(pd.DataFrame(rows))
    store.write_parquet(photolist_rel("pilot"), photos)
    other = (122.25, 16.12)
    for p in photos.itertuples():
        # the zone's folder: the star after a neighbour (row 1); dots/: the star alone (row 0), as the sky run had it
        store.write_parquet(dots_rel(p.obs_id, 1, folder), dots_df(p.obs_id, 1, p.mjd, [other, STAR]))
        store.write_parquet(dots_rel(p.obs_id, 1, "dots"), dots_df(p.obs_id, 1, p.mjd, [STAR]))
    for step in ("link", "spectra"):
        assert main([step, "--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]) == 0
    summary = json.loads((tmp_path / "results/pilot/summary.json").read_text())
    assert summary["steps"]["link"]["dots_dir"] == folder
    members = store.read_parquet(results_rel("pilot", "members.parquet"))
    src = store.read_parquet(results_rel("pilot", "sources.parquet"))
    assert len(src) == 2 and (src["n_dots"] == 10).all()
    star = members[members["source_id"] == src.loc[np.argmin(np.abs(src["ra"] - STAR[0])), "source_id"]]
    assert (star["dot_row"] == 1).all()  # rows of the zone's folder, not of dots/
    assert abs(src["ra"].to_numpy() - STAR[0]).min() < 1e-6 and abs(src["ra"].to_numpy() - other[0]).min() < 1e-6
