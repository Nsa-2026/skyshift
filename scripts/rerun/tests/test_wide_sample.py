"""Tests for scripts/rerun/wide_sample.py and rerun.zone_box (offline: no S3, no TAP, no photos)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("skyshift_pipeline")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rerun as R  # noqa: E402
import wide_sample as W  # noqa: E402


def test_ecliptic_poles_and_equinox():
    lon, lat = W.ecliptic([270.0, 90.0, 0.0], [66.5607, -66.5607, 0.0])
    assert lat[0] == pytest.approx(90.0, abs=1e-3)
    assert lat[1] == pytest.approx(-90.0, abs=1e-3)
    assert (lon[2], lat[2]) == (pytest.approx(0.0, abs=1e-9), pytest.approx(0.0, abs=1e-9))


def test_ecliptic_round_trip():
    for lon, lat in ((60.0, 0.0), (200.0, 30.0), (330.0, -45.0), (90.0, 80.0)):
        ra, dec = W.from_ecliptic(lon, lat)
        lo, la = W.ecliptic([ra], [dec])
        assert lo[0] == pytest.approx(lon, abs=1e-6)
        assert la[0] == pytest.approx(lat, abs=1e-6)


def test_sun_longitude_at_j2000_and_the_march_equinox():
    assert W.sun_lon([51544.5])[0] == pytest.approx(280.37, abs=0.05)
    # March equinox 2025: 2025-03-20 09:01 UTC = MJD 60754.376
    assert (W.sun_lon([60754.376])[0] + 180) % 360 - 180 == pytest.approx(0.0, abs=0.05)


def test_elongation():
    assert W.elongation([90.0], [0.0], [0.0])[0] == pytest.approx(90.0)
    assert W.elongation([10.0], [90.0], [0.0])[0] == pytest.approx(90.0)
    assert W.elongation([30.0], [0.0], [0.0])[0] == pytest.approx(30.0)


def test_depth_counts_neighbours_within_the_radius():
    # 10 centres at one spot, 5 at 1° from it, 3 at 10°: the spot sees 15, the far ones only themselves
    ra = np.r_[np.full(10, 100.0), np.full(5, 101.0), np.full(3, 110.0)]
    dec = np.zeros(len(ra))
    d = W.depth_counts(ra, dec, nside=64, radius=1.75)
    assert d[0] == 15 and d[10] == 15
    assert d[-1] == 3


def test_box_for_stays_inside_ra_0_360():
    b = W.box_for(0.1, 10.0, 1.0)
    assert b[0] > 0 and b[1] - b[0] == pytest.approx(1.0 / np.cos(np.deg2rad(10.5)), abs=1e-3)
    b = W.box_for(359.9, -20.0, 1.0)
    assert b[1] < 360.0
    assert all(isinstance(x, float) for x in b)


def _sessions(rows):
    return pd.DataFrame(rows, columns=["session", "photos", "detectors", "mjd", "visit", "elong"])


def test_pick_sessions_spreads_over_visits_and_skips_too_big_sessions():
    st = _sessions([(s, 8, 2, 60800 + 30 * v, v, 90.0) for v in range(1, 9) for s in (10 * v, 10 * v + 1)]
                   + [(99, 40, 6, 60800, 1, 90.0)])
    out = W.pick_sessions(st, target=24, max_sessions=6, max_photos=20)
    assert 99 not in out
    assert len(out) == 3
    visits = sorted({int(st.set_index("session").loc[s, "visit"]) for s in out})
    assert visits[0] == 1 and visits[-1] == 8  # first and last visit: the widest time spread


def test_pick_sessions_elong_takes_the_lowest_elongation():
    st = _sessions([(1, 4, 2, 60800, 1, 95.0), (2, 4, 2, 60801, 1, 82.0), (3, 4, 2, 60990, 2, 83.0),
                    (4, 4, 2, 60991, 2, 99.0)])
    assert W.pick_sessions(st, target=8, how="elong") == [2, 3]


def test_gaia_density_uses_the_irsa_count_where_the_forced_list_is_capped(tmp_path):
    h = W.healpix(32)
    rows = np.full(h.npix, 1000)
    rows[5] = 8221
    d = tmp_path / "refcat" / "sky" / "forced_n32"
    d.mkdir(parents=True)
    (d / "_complete.json").write_text(json.dumps({"nside": 32, "cap_per_partition": 8221,
                                                  "rows_per_partition": rows.tolist()}))
    import astropy.units as u

    lon, lat = h.healpix_to_lonlat([5, 6])
    ra, dec = lon.deg, lat.deg
    f, capped, best = W.gaia_density(tmp_path, ra, dec)
    assert capped.tolist() == [True, False]
    assert np.isnan(best[0]) and best[1] == pytest.approx(1000 / h.pixel_area.to(u.deg ** 2).value)
    W.out_dir(tmp_path)
    pd.DataFrame({"pix": [5], "ra": [ra[0]], "dec": [dec[0]], "n": [1000], "per_deg2": [9000.0]}).to_parquet(
        tmp_path / W.OUT / "gaia_capped_counts.parquet")
    f, capped, best = W.gaia_density(tmp_path, ra, dec)
    assert best[0] == pytest.approx(9000.0)


def test_region_names():
    df = pd.DataFrame({"gal_l": [0.5, 100.0, 100.0, 200.0, 280.47], "gal_b": [-3.0, 1.0, 60.0, 30.0, -32.89],
                       "ecl_lat": [-5.0, 10.0, 20.0, 88.0, -85.5], "s_ra": [266.0, 300.0, 180.0, 270.0, 80.894],
                       "s_dec": [-29.0, 40.0, 30.0, 66.0, -69.756]})
    assert W.region(df).tolist() == ["bulge", "plane", "highlat", "nep", "lmc"]


def test_install_copies_new_files_only(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "w_x"
    src.mkdir()
    (src / "a.json").write_text("new")
    (src / "b.json").write_text("new")
    dest.mkdir()
    (dest / "a.json").write_text("old")
    assert W.install(src, dest) == 1
    assert (dest / "a.json").read_text() == "old" and (dest / "b.json").read_text() == "new"
    with pytest.raises(AssertionError):
        W.install(src, tmp_path / "ecl04")


def test_zone_box_from_config_or_the_target():
    assert R.zone_box("hunt").ra_min == 120.5
    z = R.zone_box("w_x", {"zone": "w_x", "box": [10, 11, -5, -4]})
    assert (z.name, z.ra_min, z.ra_max, z.dec_min, z.dec_max) == ("w_x", 10.0, 11.0, -5.0, -4.0)
    assert R.zone_box("w_y", {"zone": "w_y"}) is None


def test_wide_targets_file_is_a_bench_targets_file():
    p = Path(W.WIDE_TARGETS)
    if not p.exists():
        pytest.skip("wide_targets.json not built")
    d = json.loads(p.read_text())
    assert d["targets"]
    for t in d["targets"]:
        assert t["zone"].startswith("w_") and t["sessions"] and t["why"]
        assert len(t["box"]) == 4 and t["box"][0] < t["box"][1] and t["box"][2] < t["box"][3]
