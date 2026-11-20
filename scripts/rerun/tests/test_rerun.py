"""Tests for scripts/rerun/rerun.py (offline: synthetic photo lists, no S3, no photos)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("skyshift_pipeline")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rerun as R  # noqa: E402
from skyshift_pipeline import coadd  # noqa: E402


def _idx(rows):
    return pd.DataFrame(rows, columns=["obs_id", "detector", "collection", "s3_key", "size_bytes", "mjd"])


def test_latest_versions_keeps_the_highest_l2b_version():
    idx = _idx([
        ("A_1", 1, "spherex_dr1", "dr1/level2/P/l2b-v27-2026-1/1/a.fits", 1, 1.0),
        ("A_1", 1, "spherex_dr1", "dr1/level2/P/l2b-v28-2026-9/1/a.fits", 2, 1.0),
        ("A_1", 4, "spherex_dr1", "dr1/level2/P/l2b-v27-2026-1/4/a.fits", 3, 1.0),
    ])
    out = R.latest_versions(idx)
    assert len(out) == 2
    assert out.set_index("detector").loc[1, "size_bytes"] == 2


def test_latest_versions_prefers_dr1_on_a_tie():
    idx = _idx([
        ("A_1", 1, "spherex_qr3", "qr3/level2/P/l2b-v27-x/1/a.fits", 9, 1.0),
        ("A_1", 1, "spherex_dr1", "dr1/level2/P/l2b-v27-x/1/a.fits", 1, 1.0),
    ])
    assert R.latest_versions(idx)["collection"].tolist() == ["spherex_dr1"]


def _zone(obs_det_session):
    return pd.DataFrame([{"obs_id": o, "detector": d, "session": s, "visit": 1, "mjd": 60000 + i / 1440, "url": ""}
                         for i, (o, d, s) in enumerate(obs_det_session)])


def test_simulate_reads_counts_photos_once_and_rereads_for_a_session_cut_differently():
    # Zone A holds exposures 1-3 of one session, zone B exposures 2-4 of it (the same session cut differently): B's
    # stack has another key, so its photos 2 and 3 (done in A) are read again for it; photo 4 is new.
    a = _zone([("S_1", 1, 1), ("S_2", 1, 1), ("S_3", 1, 1)])
    b = _zone([("S_2", 1, 7), ("S_3", 1, 7), ("S_4", 1, 7)])
    r = R.simulate_reads([("A", a), ("B", b)], coadd.session_tasks)
    assert r["unique_photos"] == 4
    assert r["rereads"] == 2
    assert r["reads"] == 6
    assert r["stack_tasks"] == 2


def test_simulate_reads_no_reread_when_the_zones_hold_the_same_session():
    a = _zone([("S_1", 1, 1), ("S_2", 1, 1)])
    b = _zone([("S_1", 1, 4), ("S_2", 1, 4)])
    r = R.simulate_reads([("A", a), ("B", b)], coadd.session_tasks)
    assert (r["unique_photos"], r["rereads"], r["stack_tasks"]) == (2, 0, 1)


def test_sky_zone_order_hunt_first_then_nearest_the_ecliptic(tmp_path):
    z = tmp_path / "photolists" / "zones"
    z.mkdir(parents=True)
    for n in ("ecl01s1", "ecl00n1", "ecl01", "hunt", "ecl00", "t_nep", "ecl00n2"):
        (z / f"{n}.parquet").write_bytes(b"")
    assert R.sky_zone_names(tmp_path, "sky") == ["hunt", "ecl00", "ecl01", "ecl00n1", "ecl01s1", "ecl00n2"]
    assert R.sky_zone_names(tmp_path, "t_nep,hunt") == ["t_nep", "hunt"]
    with pytest.raises(SystemExit):
        R.sky_zone_names(tmp_path, "nope")


def test_parallel_workers_counts_smt_threads_at_a_fraction():
    assert R.parallel_workers(8, 12, 0.25) == 8
    assert R.parallel_workers(20, 12, 0.25) == 14


def test_bench_photos_takes_whole_sessions(tmp_path):
    z = tmp_path / "photolists" / "zones"
    z.mkdir(parents=True)
    ph = _zone([("2025W01_1A_0001_1", 1, 1), ("2025W01_1A_0001_2", 1, 1), ("2025W01_1A_0002_1", 1, 2),
                ("2025W01_1A_0003_1", 4, 3), ("2025W01_1A_0003_2", 4, 3), ("2025W01_1A_0003_3", 4, 3)])
    ph.to_parquet(z / "zz.parquet")
    sel = R.bench_photos(tmp_path, [{"zone": "zz", "obs": ["2025W01_1A_0001"]}, {"zone": "zz", "pick": "largest"},
                                    {"zone": "zz", "sessions": [2]}])
    assert [len(r) for _, r, _ in sel] == [2, 3, 1]


def test_score_helpers_shift_box_and_finite():
    import numpy as np

    import score_bench as S
    from skyshift_pipeline import config as C
    from skyshift_pipeline.sky import angsep

    ra2, dec2 = S.shifted(np.array([10.0]), np.array([60.0]))
    assert abs(float(angsep(10.0, 60.0, ra2[0], dec2[0])) * 3600 - S.SHIFT_ARCSEC) < 0.5
    z = C.Zone("z", 10.0, 20.0, -5.0, 5.0)
    assert S.in_box(z, np.array([15.0, 10.001, 25.0]), np.array([0.0, 0.0, 0.0])).tolist() == [True, False, False]
    df = pd.DataFrame({"ra": [1.0, np.nan, 3.0], "dec": [0.0, 0.0, np.inf]})
    assert S.finite(df)["ra"].tolist() == [1.0]


def test_bench_resume_redoes_zero_byte_dot_files_left_by_a_crash(tmp_path):
    """The 18 Nov bench was cut off by a crash: 0-byte dot / peaks / streak / forced files stayed and a resumed bench
    skipped them (any dot file that existed counted as done). bench's worker call (dots.process_photos) now measures
    such a photo again; `plan` counts only whole dot files as done (Store.complete_parquets)."""
    from skyshift_pipeline import config as C
    from skyshift_pipeline import dots
    from skyshift_pipeline.store import Store, dots_rel

    cutout = Path(__file__).resolve().parents[3] / "fixtures" / "fits" / "cutout_2025W18_1B_0074_2D1.fits"
    folder = C.zone_dots(None)[0]
    p = tmp_path / dots_rel("2025W18_1B_0074_2", 1, folder)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")
    (p.parent / "2025W18_1B_0074_3D1.parquet").write_bytes(b"")
    photo = [("2025W18_1B_0074_2", 1, str(cutout), 7, 2)]
    st = dots.process_photos(str(tmp_path), photo, None, False, [])
    assert [s["status"] for s in st] == ["done"] and st[0].get("incomplete_replaced")
    assert p.stat().st_size > 0 and len(pd.read_parquet(p)) == st[0]["n_dots"]
    assert [s["status"] for s in dots.process_photos(str(tmp_path), photo, None, False, [])] == ["skipped"]
    assert Store(str(tmp_path)).complete_parquets(folder) == {"2025W18_1B_0074_2D1.parquet"}
