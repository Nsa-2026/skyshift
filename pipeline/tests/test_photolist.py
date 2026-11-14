import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline.photolist import clean, s3_key_from_uri, tile_map

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


def test_tile_map_sessions_and_visits():
    zone = C.ZONES["pilot"]
    # 3 sessions in visit 1 (2 exposures each), 2 sessions six months later, all covering the zone centre
    times = []
    for s in range(3):
        times += [60000 + s * 0.5, 60000 + s * 0.5 + 2.1 / 1440]
    for s in range(2):
        times += [60180 + s * 0.5]
    rows = []
    for k, tm in enumerate(times):
        rows.append(dict(obs_id=f"E{k:02d}", detector=1, mjd=tm, s_region=square(122.3, 16.15)))
    # one photo far away: not kept
    rows.append(dict(obs_id="FAR", detector=1, mjd=60000.0, s_region=square(140.0, 16.0)))
    photos = pd.DataFrame(rows)
    kept, tm, tiles = tile_map(photos, zone)
    assert "FAR" not in set(kept["obs_id"]) and len(kept) == len(times)
    centre = tiles.iloc[(np.abs(tiles["ra"] - 122.3) + np.abs(tiles["dec"] - 16.15)).argmin()]
    assert centre["n_photos"] == len(times)
    assert centre["n_sessions"] == 5 and centre["n_visits"] == 2
    g = tm[tm["tile"] == centre["tile"]].sort_values("mjd")
    assert list(g["session"]) == [1, 1, 2, 2, 3, 3, 4, 5]
    assert list(g["visit"]) == [1] * 6 + [2] * 2
    assert tm["session"].dtype == np.int32 and tm["visit"].dtype == np.int16
