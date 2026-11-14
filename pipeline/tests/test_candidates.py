"""candidates.json export (contracts/api.md): scores, reasons, why, dedupe, and a whole build on a local store."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from skyshift_core import tangent_deproject

from skyshift_pipeline import candidates as K
from skyshift_pipeline.config import ZONES
from skyshift_pipeline.spectra import NCH_DET, NDET
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

TYPES = {"id": str, "kind": str, "ra": float, "dec": float, "mjd": float, "clock": str, "det": int, "zone": str,
         "gaia_match": bool, "catwise_match": bool, "galactic_b": float, "labels": list, "reasons": list,
         "why": str, "flags": list}
NULLABLE = {"score": float, "snr": float, "flux_ujy": float, "rate_arcsec_h": float, "pm_mas_yr": float,
            "pm_err_mas_yr": float, "pa_deg": float, "nearest_known_arcsec": float, "frames_url": str}


def test_record_flag_names():
    assert K.record_flag_names(0) == []
    assert K.record_flag_names((1 << 21) | (1 << 0)) == []          # SOURCE and TRANSIENT are not record-only
    assert K.record_flag_names((1 << 19) | (1 << 28) | (1 << 22)) == ["outlier", "ghost", "halo"]
    assert set(K.RECORD_FLAG_NAMES.values()) == {"outlier", "crosstalk", "ghost", "ghost_fpa", "ghost_ext", "bloom", "halo"}


def test_galactic_b_and_pa():
    b = K.galactic_b([266.405, 192.859], [-28.936, 27.128])   # Galactic centre, north Galactic pole
    assert abs(b[0]) < 0.1 and abs(b[1] - 90.0) < 0.01
    assert K.pa_deg(0.0, 1.0) == 0.0 and K.pa_deg(1.0, 0.0) == 90.0 and K.pa_deg(0.0, -1.0) == 180.0


def test_mover_score():
    s = K.mover_score([20, 20, 20, 20, 20], [3, 3, 2, 3, 3], ["two_exposures"] * 3 + ["two_detectors", "two_exposures"],
                      [np.nan, np.nan, np.nan, np.nan, 30.0], [0, 0, 0, 0, 0], [False] * 5)
    base = 1 - np.exp(-20 / 15)
    assert s[0] == pytest.approx(base)
    assert s[2] == pytest.approx(base * 0.85)          # only two dots
    assert s[3] == pytest.approx(base * 0.6)           # two detectors at one moment
    assert s[4] == pytest.approx(base * 0.5)           # a known object predicted 30″ away
    flagged = K.mover_score([20, 20, 20], [3] * 3, ["two_exposures"] * 3, [np.nan] * 3,
                            [1 << 22, 1 << 0, 0], [False, False, True])
    assert flagged == pytest.approx([base * 0.7, base * 0.8, base * 0.85])
    assert K.mover_score([1000], [5], ["two_exposures"], [np.nan], [0], [False])[0] <= 1.0


def _mover_dots(rate_arcsec_h=36.0, pa=60.0, n=2, step_min=2.1, ra0=125.0, dec0=20.0, t0=61000.0):
    t = t0 + np.arange(n) * step_min / 1440.0
    tc = t - t.mean()
    e = rate_arcsec_h * np.sin(np.radians(pa)) * tc * 24.0
    nn = rate_arcsec_h * np.cos(np.radians(pa)) * tc * 24.0
    ra, dec = tangent_deproject(ra0, dec0, e, nn)
    return pd.DataFrame({"obs_id": [f"2026W01_1A_0001_{i + 1}" for i in range(n)], "detector": 3, "ra": ra, "dec": dec,
                         "mjd": t, "snr": 30.0, "pos_err_arcsec": 0.1})


def test_motion_and_match():
    d = _mover_dots()
    rate, err, pa = K.motion(d)
    assert rate == pytest.approx(36.0, rel=1e-3) and pa == pytest.approx(60.0, abs=0.1)
    assert err == pytest.approx(np.sqrt(2) * 0.1 / (2.1 / 60.0), rel=1e-3)
    assert K.motion(d.iloc[:1]) is None
    # the mover's dots among neighbours: one decoy per photo
    decoy = d.copy()
    decoy["ra"] += 8.0 / 3600.0
    near = pd.concat([d, decoy], ignore_index=True)
    near["sep"] = K.angsep(d["ra"].mean(), d["dec"].mean(), near["ra"], near["dec"]) * 3600.0
    m = K.match_mover(float(d["ra"].mean()), float(d["dec"].mean()), float(d["mjd"].mean()), 2, near)
    assert m is not None and np.allclose(np.sort(m["ra"].to_numpy()), np.sort(d["ra"].to_numpy()))
    assert K.match_mover(float(d["ra"].mean()) + 1.0 / 3600, float(d["dec"].mean()), float(d["mjd"].mean()), 2, near) is None


def _hp(**kw):
    row = {"method": "fit", "status": "new", "source_id": 7, "source_id_b": -1, "ra": 125.0, "dec": 20.0, "mjd": 61050.0,
           "sep_arcsec": np.nan, "pmra": 0.0, "pmdec": 8780.0, "pm": 8780.0, "pm_err": 300.0, "red_ratio": 2.0,
           "catwise_name": "", "catwise_pm_agree": False, "score": 1 / 29.3, "vetted": True, "reason": ""}
    row.update(kw)
    return pd.DataFrame([row])


def test_highpm_rows():
    c = K.high_pms(_hp(), "hunt", {7: ["ch4"]})[0]
    assert c["id"] == "hunt-pm-7" and c["clock"] == "months" and c["pa_deg"] == 0.0
    assert c["labels"] == ["red", "ch4"] and c["reasons"] == ["pm_fit", "vetted"]
    assert c["why"] == "Moves 8.78″ a year between visits; no catalogue lists this motion."
    assert c["score"] == pytest.approx(np.exp(-(1 / 29.3) / 0.2))
    g = K.high_pms(_hp(status="gaia", vetted=False, reason="gaia_pm_disagrees,catwise_pm_disagrees",
                       catwise_name="J0820", red_ratio=0.3), "hunt", {})[0]
    assert g["gaia_match"] and g["catwise_match"] and g["labels"] == []
    assert g["reasons"] == ["pm_fit", "gaia_pm_disagrees", "catwise_pm_disagrees"]
    assert "Gaia lists a different motion for the star here" in g["why"]
    assert g["score"] == pytest.approx(np.exp(-(1 / 29.3) / 0.2) * 0.4 * 0.4 * 0.6)
    p = K.high_pms(_hp(method="pair", source_id_b=9, sep_arcsec=40.0, vetted=False, reason="at_control_level"), "pilot", {})[0]
    assert p["id"] == "pilot-pm-7-9" and p["reasons"][0] == "pm_pair" and "by chance" in p["why"]


def _classes(n=5):
    c = pd.DataFrame({
        "source_id": np.arange(1, 6, dtype=np.int64), "class": ["unsure", "star", "star", "galaxy", "unsure"],
        "labels": ["ch4", "h2o_vapour", "co_23", "pah_33", ""], "brown_dwarf_like": [True, False, False, False, False],
        "bd_evidence": ["ch4_33", "", "", "", ""], "j_m46_ab": [1.0, -1.4, -1.8, 0.2, 0.0],
        "gaia_sep_arcsec": [np.nan, 0.3, 0.2, np.nan, np.nan], "gaia_g": [np.nan, 14.0, 12.0, np.nan, np.nan],
        "gaia_parallax": [np.nan, 40.0, 1.0, np.nan, np.nan], "gaia_parallax_error": [np.nan, 0.5, 0.05, np.nan, np.nan],
    })
    for lab in K.LABEL_BANDS:
        c[f"label_{lab}"] = c["labels"].str.contains(lab)
    for bands in K.LABEL_BANDS.values():
        for b, _ in bands:
            c[f"{b}_sig"] = np.nan
    c.loc[0, "ch4_33_sig"], c.loc[1, "h2o_14_sig"], c.loc[1, "h2o_19_sig"] = 12.0, 9.0, 6.0
    c.loc[2, "co_23_sig"], c.loc[3, "pah_33_sig"] = 8.0, 5.5
    return c.head(n).copy()


def test_spectral_selection_and_why():
    s = K.select_spectral(_classes())
    # 1: no Gaia, methane; 2: Gaia at 25 pc (kept); 3: a distant Gaia star (dropped); 4: no Gaia; 5: unlabelled
    assert s["source_id"].tolist() == [1, 2, 4]
    assert s["_score"].tolist() == pytest.approx([1 - np.exp(-1.2), 0.6 * (1 - np.exp(-0.9)), 0.3 * (1 - np.exp(-0.55))])
    assert len(K.select_spectral(_classes(), cap=2)) == 2
    rows = K.spectrals(s, "hunt")
    assert [r["labels"] for r in rows] == [["red", "brown_dwarf_like", "ch4"], ["h2o_vapour"], ["red", "pah_33"]]
    assert rows[0]["reasons"] == ["no_gaia", "class_unsure"] and rows[1]["reasons"] == ["gaia_nearby", "class_star"]
    assert rows[0]["why"].startswith("Methane at 3.3 µm (12σ) in a point source with no Gaia star")
    assert "what a brown dwarf would look like" in rows[0]["why"]
    assert rows[1]["why"] == "Water vapour at 1.4/1.9 µm (9σ) in a point source that Gaia puts 25 pc away."
    assert "galaxy" in rows[2]["why"] and rows[1]["gaia_match"] and not rows[0]["gaia_match"]


def test_dedupe():
    a = [{"kind": "mover", "ra": 121.0, "dec": 15.0, "mjd": 61000.0}, {"kind": "high_pm", "ra": 122.0, "dec": 15.0, "mjd": 0.0}]
    b = [{"kind": "mover", "ra": 121.0, "dec": 15.0 + 1 / 3600, "mjd": 61000.001},   # same mover
         {"kind": "mover", "ra": 121.0, "dec": 15.0, "mjd": 61001.0},                # same place, another day
         {"kind": "high_pm", "ra": 122.0, "dec": 15.0 + 3 / 3600, "mjd": 5.0},        # same star
         {"kind": "spectral", "ra": 122.0, "dec": 15.0, "mjd": 5.0}]                  # another kind
    assert K.dedupe(a, b) == b[1:2] + b[3:]


# --- a whole build on a local store ----------------------------------------------------------------
def _write(store: Store, rel: str, df: pd.DataFrame) -> None:
    store.write_parquet(rel, df)


def _zone_files(store: Store, zone: str, ra0: float, dec0: float, with_extra_mover: bool) -> None:
    d = _mover_dots(ra0=ra0, dec0=dec0)
    d["obs_id"] = [f"{zone}x_1", f"{zone}x_2"]
    for (o, det), g in d.groupby(["obs_id", "detector"]):
        other = g.copy()
        other["ra"] += 0.01
        _write(store, dots_rel(o, det), pd.concat([other, g], ignore_index=True)
               .assign(flags=[0, (1 << 19) | (1 << 21)])[["ra", "dec", "mjd", "snr", "pos_err_arcsec", "flags"]])
    rows = [{"id": f"{zone}-1-2", "ra": d["ra"].mean(), "dec": d["dec"].mean(), "mjd": d["mjd"].mean(), "n_dots": 2,
             "n_exposures": 2, "detectors": "3", "det": 3, "evidence": "two_exposures", "snr": 30.0, "flux_ujy": 900.0,
             "flags": (1 << 19) | (1 << 21), "obs_ids": ",".join(d["obs_id"]), "cell": 1,
             "nearest_known_arcsec": np.nan, "gaia_checked": True, "catwise_checked": True}]
    if with_extra_mover:
        rows.append({**rows[0], "id": f"{zone}-5-6", "dec": rows[0]["dec"] + 0.5, "nearest_known_arcsec": 90.0,
                     "evidence": "two_detectors", "detectors": "2,5", "det": 2, "snr": 8.0})
    _write(store, results_rel(zone, "suspects.parquet"), pd.DataFrame(rows))
    sid = 100 if zone == "hunt" else 200
    h = _hp(source_id=sid, ra=ra0 + 0.1, dec=dec0)
    _write(store, results_rel(zone, "highpm_candidates.parquet"), h)
    c = _classes(2)
    c["source_id"] = [sid, sid + 1]
    _write(store, results_rel(zone, "classes.parquet"), c)
    spec = np.full((2, NDET * NCH_DET), 10.0)
    spec[:, 5 * NCH_DET:] = 50.0       # brightest in D6
    _write(store, results_rel(zone, "sources.parquet"), pd.DataFrame({
        "source_id": [sid, sid + 1], "ra": [ra0 + 0.1, ra0 + 0.2], "dec": [dec0, dec0],
        "flux_ujy": list(spec), "err_ujy": list(np.ones_like(spec)), "saturated": [False, False], "variable": [False, True]}))
    _write(store, results_rel(zone, "members.parquet"), pd.DataFrame({
        "source_id": [sid, sid, sid + 1], "obs_id": [f"{zone}x_1", f"{zone}x_2", f"{zone}x_1"], "detector": [3, 3, 3],
        "dot_row": [1, 1, 0]}))
    _write(store, results_rel(zone, "crossids.parquet"), pd.DataFrame({"source_id": [sid + 1], "catwise_name": ["J1"]}))
    _write(store, photolist_rel(zone), pd.DataFrame({"obs_id": [f"{zone}x_1", f"{zone}x_2"], "detector": [3, 3],
                                                    "mjd": d["mjd"].to_numpy(), "collection": ["spherex_dr1", "spherex_qr3"]}))


def test_build_end_to_end(tmp_path):
    store = Store(str(tmp_path / "bucket"))
    _zone_files(store, "hunt", 125.0, 20.0, with_extra_mover=True)
    _zone_files(store, "pilot", 125.0, 20.0, with_extra_mover=False)   # the same objects again
    assert K.finished_zones(store) == ["hunt", "pilot"]
    out = tmp_path / "data"
    (out / "cand" / "hunt-1-2").mkdir(parents=True)
    (out / "cand" / "hunt-1-2" / "frames.json").write_text("{}")
    doc = K.build(store, ["hunt", "pilot"], out)
    assert doc["zones"] == ["hunt", "pilot"]              # both searched, hunt first ...
    assert {c["zone"] for c in doc["candidates"]} == {"hunt"}   # ... and the pilot's objects all listed from the hunt
    assert doc["data_release"] == "DR1+QR3"
    assert doc["counts"] == {"mover": 2, "high_pm": 1, "spectral": 2}
    cs = doc["candidates"]
    for c in cs:
        assert list(c) == list(K.FIELDS)
        for k, t in TYPES.items():
            assert isinstance(c[k], t), (k, c[k])
        for k, t in NULLABLE.items():
            assert c[k] is None or isinstance(c[k], t), (k, c[k])
        assert set(c["reasons"]) <= set(K.REASONS[c["kind"]])
        assert set(c["labels"]) <= set(K.LABELS)
        assert 0.0 <= c["score"] <= 1.0
    m = cs[0]
    assert m["id"] == "hunt-1-2" and m["rate_arcsec_h"] == pytest.approx(36.0, abs=0.1) and m["pa_deg"] == 60.0
    assert m["flags"] == ["outlier"] and m["frames_url"] == "cand/hunt-1-2/frames.json"
    assert m["why"] == ("Seen at different places in 2 exposures 2.1 minutes apart, moving about 36″ an hour; "
                        "no known asteroid or comet was predicted within 10′.")
    two = cs[1]
    assert two["rate_arcsec_h"] is None and two["reasons"] == ["two_detectors", "known_object_near"]
    assert two["frames_url"] is None
    hp = next(c for c in cs if c["kind"] == "high_pm")
    assert hp["det"] == 6 and hp["snr"] == pytest.approx(50.0 * np.sqrt(NCH_DET), abs=0.1)
    assert hp["flags"] == ["outlier"]                  # set on both of its dots
    sp = next(c for c in cs if c["kind"] == "spectral")
    assert sp["id"] == "hunt-sp-100" and sp["mjd"] == pytest.approx(61000.0 + 1.05 / 1440, abs=1e-6)
    near = next(c for c in cs if c["id"] == "hunt-sp-101")
    assert near["reasons"] == ["gaia_nearby", "class_star", "variable"] and near["catwise_match"] and near["gaia_match"]
    text = K.dumps(doc)
    assert json.loads(text) == doc and text.count("\n") == len(cs) + 2
    small = K.sample(doc, per_kind=1)
    assert small["synthetic"] is False and small["counts"] == doc["counts"] and len(small["candidates"]) <= len(cs)


def test_zone_boxes_order():
    # the hunt box contains the pilot box: hunt first
    h, p = ZONES["hunt"], ZONES["pilot"]
    assert h.ra_min <= p.ra_min and h.ra_max >= p.ra_max and h.dec_min <= p.dec_min and h.dec_max >= p.dec_max
