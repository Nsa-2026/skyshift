"""The red forced layer (forced_sky --red: AllWISE red sources with no Gaia target, ref_id `allwise:…`, source_id −1
in lightcurves until 16 Nov) tied to sources by position in crossid (link_red, allwise_ref_id), its forced rows then
in the sources' light curves, and classify's J − [4.6] from its J-window forced photometry when the spectrum has none."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from skyshift_core import MASK_BITS
from skyshift_core.flags import OVERFLOW

from skyshift_pipeline import classify as CL
from skyshift_pipeline import config as C
from skyshift_pipeline import crossid as X
from skyshift_pipeline import forced_sky as FS
from skyshift_pipeline import indices as IX
from skyshift_pipeline import lightcurves as LC
from skyshift_pipeline.store import Store

from test_classify import no_gaia, sources_frame, t_dwarf_spectrum

DEC0 = 16.1


def east(ra, arcsec):
    return ra + arcsec / 3600.0 / np.cos(np.deg2rad(DEC0))


def crossids(n=4):
    ra = 122.3 + 0.01 * np.arange(n)
    return pd.DataFrame({"source_id": np.arange(10, 10 + n, dtype=np.int64), "ra": ra, "dec": np.full(n, DEC0),
                         "epoch_mjd": 60973.0})


def red_rows():
    ra = 122.3 + 0.01 * np.arange(4)
    return pd.DataFrame({
        "ref_id": ["allwise:J1", "allwise:J2", "allwise:J2b", "allwise:J3", "gaia:7"],
        # J1 1.5″ from source 10; J2 2.0″ and J2b 1.0″ from source 11 (J2b wins); J3 4″ from 12 (too far); a Gaia row
        "ra": [east(ra[0], 1.5), east(ra[1], 2.0), east(ra[1], -1.0), east(ra[2], 4.0), ra[3]],
        "dec": np.full(5, DEC0), "epoch_mjd": 55377.0, "pmra": np.nan, "pmdec": np.nan, "parallax": np.nan})


def test_link_red_nearest_target_within_3_arcsec_and_only_allwise_rows():
    out = X.link_red(crossids(), red_rows())
    assert out["allwise_ref_id"].tolist() == ["allwise:J1", "allwise:J2b", "", ""]
    assert out["allwise_sep_arcsec"].iloc[0] == pytest.approx(1.5, abs=0.01)
    assert out["allwise_sep_arcsec"].iloc[1] == pytest.approx(1.0, abs=0.01)
    assert np.isnan(out["allwise_sep_arcsec"].iloc[2:]).all()
    none = X.link_red(crossids(), None)
    assert (none["allwise_ref_id"] == "").all() and none["allwise_sep_arcsec"].isna().all()


def test_a_target_between_two_sources_goes_to_the_nearer_one_only():
    x = crossids(2)
    x["ra"] = [122.3, east(122.3, 3.0)]
    red = pd.DataFrame({"ref_id": ["allwise:M"], "ra": [east(122.3, 1.2)], "dec": [DEC0]})
    out = X.link_red(x, red)
    assert out["allwise_ref_id"].tolist() == ["allwise:M", ""]


def test_red_layer_reads_the_partitions_around_the_sources(tmp_path):
    store = Store(str(tmp_path))
    assert X.red_layer(store, np.array([122.3]), np.array([DEC0])) is None  # not built
    rows = red_rows()
    pix = int(FS.pixel_of(np.array([122.3]), np.array([DEC0]))[0])
    store.write_parquet(FS.partition_rel(pix, C.FORCED_SKY_RED_DIR), rows)
    store.write_json(FS.manifest_rel(C.FORCED_SKY_RED_DIR), {"nside": C.FORCED_SKY_NSIDE})
    got = X.red_layer(store, np.array([122.3]), np.array([DEC0]))
    assert len(got) == len(rows) and set(got["ref_id"]) == set(rows["ref_id"])


def forced_points(refs, wl=1.25, flux=10.0, err=1.0, det=2, n=4):
    rows = []
    for r in refs:
        for k in range(n):
            rows.append({"ref_id": r, "mjd": 60973.0 + k, "visit": 1, "detector": det, "wavelength_um": wl,
                         "flux_ujy": flux, "flux_err_ujy": err, "detected": flux / err >= 5, "flags": 0})
    return pd.DataFrame(rows)


def test_lightcurves_tie_red_rows_through_allwise_ref_id():
    x = X.link_red(crossids(), red_rows())
    f = forced_points(["allwise:J1", "allwise:J3", "allwise:J2b"])
    p = LC.points_from_forced(f, x)
    sid = dict(zip(p["ref_id"], p["source_id"]))
    assert sid == {"allwise:J1": 10, "allwise:J3": -1, "allwise:J2b": 11}
    # crossids written before allwise_ref_id: catalogue-only curves, as before
    old = LC.points_from_forced(f, x.drop(columns=["allwise_ref_id", "allwise_sep_arcsec"]))
    assert (old["source_id"] == -1).all()


def test_forced_j_table_measured_limit_and_window():
    links = pd.DataFrame({"source_id": [1, 2, 3], "allwise_ref_id": ["allwise:A", "allwise:B", "allwise:C"]})
    f = pd.concat([forced_points(["allwise:A"], flux=10.0, err=1.0),       # S/N 20 over 4 points: measured
                   forced_points(["allwise:B"], flux=0.3, err=1.0),        # S/N 0.6: a limit
                   forced_points(["allwise:C"], wl=1.45, flux=50.0),      # outside both windows (J 1.15–1.33 µm)
                   forced_points(["allwise:A"], flux=1e6, err=1.0).assign(flags=OVERFLOW),  # masked rows
                   forced_points(["allwise:A", "allwise:B"], wl=4.6, det=6, flux=40.0, err=2.0)],  # [4.6]
                  ignore_index=True)
    assert OVERFLOW & MASK_BITS
    t = CL.forced_j_table(f, links).set_index("source_id")
    assert set(t.index) == {1, 2, 3}
    assert t.loc[1, "j_ab"] == pytest.approx(23.9 - 2.5 * np.log10(10.0), abs=1e-3) and not t.loc[1, "j_limit"]
    assert t.loc[1, "j_n"] == 4 and t.loc[1, "j_snr"] == pytest.approx(20.0, rel=1e-3)
    lim = 0.3 + C.CLASSIFY_FORCED_J_LIMIT_SIGMA * 0.5  # σ of the mean of 4 = 0.5
    assert t.loc[2, "j_limit"] and t.loc[2, "j_ab"] == pytest.approx(23.9 - 2.5 * np.log10(lim), abs=1e-3)
    assert t.loc[1, "m46_ab"] == pytest.approx(23.9 - 2.5 * np.log10(40.0), abs=1e-3) and t.loc[2, "m46_n"] == 4
    assert np.isnan(t.loc[3, "j_ab"]) and np.isnan(t.loc[3, "m46_ab"]) and t.loc[3, "j_n"] == 0
    assert not t.loc[3, "j_limit"]


def fj_frame(sids, j, j_limit, m46=np.nan, m46_limit=False):
    n = len(sids)
    return pd.DataFrame({"source_id": sids, "j_ab": j, "j_limit": j_limit, "j_snr": 5.0, "j_n": 4,
                         "m46_ab": np.broadcast_to(m46, n).astype(float), "m46_limit": np.broadcast_to(m46_limit, n),
                         "m46_snr": 5.0, "m46_n": 4})


def test_classify_takes_j_from_the_red_layer_only_when_the_spectrum_has_none():
    td = t_dwarf_spectrum()
    mwir_only = np.where(IX.CENTRE < 2.4, np.nan, td)  # no D1–D3 dot: a red object seen in D4–D6 only
    sids = np.array([1, 2, 3, 4], np.int64)
    src = pd.concat([sources_frame([1, 2, 3], mwir_only), sources_frame([4], td)], ignore_index=True)
    shape = CL.shape_features(None, sids)
    base = CL.classify_frame(src, shape, no_gaia(sids))
    assert base["j_m46_ab"].isna().tolist() == [True, True, True, False]
    assert base["j_m46_from"].tolist() == ["", "", "", "spectrum"]
    assert not base["brown_dwarf_like"].iloc[:3].any()  # not provably red without J
    m46 = float(base["mag_ab_m46"].iloc[0])
    fj = fj_frame([1, 2, 3, 4], [m46 + 2.0, m46 + 1.0, m46 - 2.5, 5.0], [False, True, False, False], m46=10.0)
    out = CL.classify_frame(src, shape, no_gaia(sids), fj=fj)
    assert out["j_m46_from"].tolist() == ["forced", "forced_limit", "forced", "spectrum"]
    # the spectrum's [4.6] wins over the forced one (10.0 here), and the spectrum's J is never replaced
    assert out["j_m46_ab"].iloc[:3].tolist() == pytest.approx([2.0, 1.0, -2.5], abs=1e-4)
    assert out["j_m46_ab"].iloc[3] == pytest.approx(base["j_m46_ab"].iloc[3])
    # red (J − [4.6] > −0.5, a limit being a lower bound) + methane where water can't be seen: brown_dwarf_like
    assert out["brown_dwarf_like"].tolist()[:3] == [True, True, False]


def test_classify_takes_46_from_the_red_layer_when_the_spectrum_lacks_it_too():
    td = t_dwarf_spectrum()
    no_46 = np.where((IX.CENTRE < 2.4) | (IX.CENTRE > 4.4), np.nan, td)  # no J, no 4.4–4.8 µm coverage
    sids = np.array([1, 2, 3], np.int64)
    src = sources_frame([1, 2, 3], no_46)
    shape = CL.shape_features(None, sids)
    assert CL.classify_frame(src, shape, no_gaia(sids))["j_m46_ab"].isna().all()
    fj = fj_frame(sids, [18.0, 18.0, 18.0], [False, True, False], m46=[15.0, 15.0, 15.0], m46_limit=[False, False, True])
    out = CL.classify_frame(src, shape, no_gaia(sids), fj=fj)
    assert out["j_m46_ab"].iloc[:2].tolist() == pytest.approx([3.0, 3.0], abs=1e-4)
    assert out["j_m46_from"].tolist() == ["forced", "forced_limit", ""]  # a [4.6] limit gives no colour
