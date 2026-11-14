import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline.photolist import add_sessions, clean, query_intersects, s3_key_from_uri, touches_zone

KEY = "dr1/level2/2025W17_4B/l2b-v27-2026-196/1/level2_2025W17_4B_0001_1D1_spx_l2b-v27-2026-196.fits"


def square(ra, dec, half=1.75):
    c = half / np.cos(np.deg2rad(dec))
    pts = [(ra - c, dec - half), (ra + c, dec - half), (ra + c, dec + half), (ra - c, dec + half), (ra - c, dec - half)]
    return "POLYGON ICRS " + " ".join(f"{a} {b}" for a, b in pts)


def raw(rows):
    base = dict(t_min=0.0, t_max=0.0, s_ra=0.0, s_dec=0.0, s_region="", uri="", size_bytes=1)
    return pd.DataFrame([{**base, **r} for r in rows])


def test_s3_key():
    assert s3_key_from_uri("ibe/data/spherex/" + KEY) == KEY
    assert s3_key_from_uri("ibe/data/spherex/qr2/level2/x.fits") == ""
    assert s3_key_from_uri("") == ""


def test_clean_dedupes_dr1_first_and_counts_missing_keys():
    df, st = clean(raw([
        dict(obs_id="A", collection="spherex_qr3", bandpass="SPHEREx-D2", uri="ibe/data/spherex/qr3/k2.fits", t_min=1, t_max=3),
        dict(obs_id="A", collection="spherex_dr1", bandpass="SPHEREx-D2", uri="ibe/data/spherex/dr1/k1.fits", t_min=1, t_max=3),
        dict(obs_id="B", collection="spherex_dr1", bandpass="SPHEREx-D5", uri=""),
        dict(obs_id="C", collection="spherex_dr1_deep", bandpass="SPHEREx-D1", uri="ibe/data/spherex/dr1/k3.fits"),
    ]))
    assert list(df["obs_id"]) == ["A"]
    assert df["collection"].iloc[0] == "spherex_dr1" and df["s3_key"].iloc[0] == "dr1/k1.fits"
    assert df["detector"].iloc[0] == 2 and df["mjd"].iloc[0] == 2.0
    assert df["url"].iloc[0] == C.S3_HTTPS + "/dr1/k1.fits"
    assert st["no_s3_key"] == 1 and st["duplicates_removed"] == 1


def test_zone_sessions_and_visits():
    # 3 sessions in visit 1 (2 exposures 2.1 min apart each), 2 sessions six months later
    times = []
    for k in range(3):
        times += [60000 + k * 0.5, 60000 + k * 0.5 + 2.1 / 1440]
    times += [60180.0, 60180.5]
    photos = pd.DataFrame({"obs_id": [f"E{k}" for k in range(len(times))], "detector": 1, "mjd": times})
    p = add_sessions(photos.sample(frac=1, random_state=1))
    assert list(p["session"]) == [1, 1, 2, 2, 3, 3, 4, 5]
    assert list(p["visit"]) == [1] * 6 + [2] * 2
    assert p["session"].dtype == np.int32 and p["visit"].dtype == np.int16


def test_touches_zone():
    photos = pd.DataFrame({"s_region": [square(122.3, 16.15), square(140.0, 16.0), square(119.0, 13.0)]})
    assert list(touches_zone(photos, C.ZONES["pilot"])) == [True, False, True]


def test_queries_name_the_zone():
    q = query_intersects(C.ZONES["pilot"])
    assert "INTERSECTS(p.poly, POLYGON('ICRS',120.5,14.4,124.1,14.4,124.1,17.9,120.5,17.9)) = 1" in q
    assert "spherex_dr1" in q and "spherex_qr3" in q
