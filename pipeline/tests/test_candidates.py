"""candidates.json export (contracts/api.md): scores, reasons, why, dedupe, and a whole build on a local store."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from skyshift_core import tangent_deproject

from skyshift_pipeline import candidate_checks as CK
from skyshift_pipeline import candidates as K
from skyshift_pipeline import config as C
from skyshift_pipeline.config import ZONES
from skyshift_pipeline.spectra import NCH_DET, NDET
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

TYPES = {"id": str, "kind": str, "ra": float, "dec": float, "mjd": float, "clock": str, "det": int, "zone": str,
         "zones": list, "gaia_match": bool, "catwise_match": bool, "galactic_b": float, "labels": list, "reasons": list,
         "why": str, "flags": list, "status": str, "checks": list}
NULLABLE = {"score": float, "snr": float, "flux_ujy": float, "rate_arcsec_h": float, "pm_mas_yr": float,
            "pm_err_mas_yr": float, "pa_deg": float, "nearest_known_arcsec": float, "frames_url": str, "known_name": str}


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
    """The base score (contracts/api.md): S/N term x evidence x known-object term x 0.1 for grade C; the cautions are the
    checks' factors now (test_candidate_checks)."""
    s = K.mover_score([20] * 5, ["two_exposures", "two_detectors", "two_exposures", "two_exposures", "two_exposures"],
                      [np.nan, np.nan, 30.0, 165.0, np.nan], ["B", "B", "B", "B", "C"])
    base = 1 - np.exp(-20 / 15)
    assert s == pytest.approx([base, base * 0.6, base * 0.5, base * 0.75, base * 0.1])
    assert K.mover_score([20], ["two_exposures"], [np.nan])[0] == pytest.approx(base)   # ungraded
    assert K.mover_score([1000], ["two_exposures"], [np.nan])[0] <= 1.0


def _suspects(rows):
    base = {"n_dots": 3, "n_exposures": 3, "evidence": "two_exposures", "snr": 20.0, "flux_ujy": 500.0, "flags": 0,
            "obs_ids": "", "cell": 1, "nearest_known_arcsec": np.nan, "gaia_checked": True, "catwise_checked": True}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_excess_cells_and_repeats():
    # 10 movers per (detector, visit); suspects: 1 in D1 visit 1, 6 in D4 visit 2 (6x the average of 7/40)
    mov = pd.DataFrame([{"det": d, "visit": v} for d in (1, 4) for v in (1, 2) for _ in range(10)])
    rows = [{"id": "a", "det": 1, "visit": 1, "detectors": "1"}] + [
        {"id": f"b{i}", "det": 4, "visit": 2, "detectors": "4"} for i in range(6)]
    cells = K.excess_cells(pd.DataFrame(rows), mov)
    assert list(cells) == [(4, 2)] and cells[(4, 2)] == pytest.approx(6 / 10 / (7 / 40))
    assert K.is_excess([4], 2, cells) and not K.is_excess([1, 4], 2, cells) and not K.is_excess([4], 1, cells)
    assert K.excess_cells(pd.DataFrame(rows[:5]), mov) == {}           # under 5 suspects in the cell
    # the same place 3.4 days apart (a fixed source), a real mover 27″ away 1.6 hours later (17″/h), the same session
    s = _suspects([
        {"id": "p", "ra": 122.0, "dec": 17.0, "mjd": 61000.0, "session": 1, "det": 3, "visit": 1, "detectors": "3"},
        {"id": "q", "ra": 122.0, "dec": 17.0 + 7 / 3600, "mjd": 61003.4, "session": 9, "det": 3, "visit": 1, "detectors": "3"},
        {"id": "r", "ra": 123.0, "dec": 17.0, "mjd": 61000.0, "session": 1, "det": 3, "visit": 1, "detectors": "3"},
        {"id": "u", "ra": 123.0, "dec": 17.0 + 27 / 3600, "mjd": 61000.0 + 1.6 / 24, "session": 2, "det": 3, "visit": 1,
         "detectors": "3"},
        {"id": "v", "ra": 124.0, "dec": 17.0, "mjd": 61000.0, "session": 1, "det": 3, "visit": 1, "detectors": "3"},
        {"id": "w", "ra": 124.0, "dec": 17.0 + 5 / 3600, "mjd": 61000.001, "session": 1, "det": 3, "visit": 1, "detectors": "3"},
    ])
    reps = K.repeat_positions(s)
    assert set(reps) == {"p", "q"} and reps["p"] == pytest.approx((7.0, 3.4), rel=1e-3)
    out = {c["id"]: c for c in K.movers(s, "hunt", None, {}, reps)}
    assert out["p"]["reasons"] == ["two_exposures", "repeat_position"] and out["r"]["reasons"] == ["two_exposures"]
    assert out["p"]["why"].endswith(", but another unexplained mover was seen 7″ from here 3.4 days apart, "
                                    "so it may be a fixed source.")
    assert out["p"]["score"] == pytest.approx(out["r"]["score"] * 0.3)


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
    assert err == pytest.approx(np.sqrt(2) * 0.1 * C.POS_ERR_SCALE / (2.1 / 60.0), rel=1e-3)  # pos_err × 1.6
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
    t = K.high_pms(_hp(status="2mass"), "hunt", {})[0]  # highpm's deep check: 2MASS saw it on our track in 2000
    assert "2MASS saw a star" in t["why"] and not t["gaia_match"]
    assert t["score"] == pytest.approx(np.exp(-(1 / 29.3) / 0.2) * 0.6)
    p = K.high_pms(_hp(method="pair", source_id_b=9, sep_arcsec=40.0, vetted=False, reason="at_control_level"), "pilot", {})[0]
    assert p["id"] == "pilot-pm-7-9" and p["reasons"][0] == "pm_pair" and "by chance" in p["why"]
    # 40″ between visits half a year apart: 80″/yr, no star is that fast
    x = K.high_pms(_hp(method="pair", source_id_b=9, sep_arcsec=40.0, pm=80000.0, pmdec=80000.0, vetted=False,
                       reason="at_control_level"), "pilot", {})[0]
    assert x["reasons"] == ["pm_pair", "at_control_level", "implausible_pm"]
    assert x["score"] == pytest.approx(np.exp(-(1 / 29.3) / 0.2) * 0.3 * 0.01)
    assert x["why"] == ("Seen 40.0″ apart in two visits (80.0″ a year), with matching brightness, but no star moves "
                        "that fast (Barnard's Star, the fastest known, does 10.4″ a year): almost certainly two different sources.")


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
    a = [{"kind": "mover", "zone": "hunt", "ra": 121.0, "dec": 15.0, "mjd": 61000.0},
         {"kind": "high_pm", "zone": "hunt", "ra": 122.0, "dec": 15.0, "mjd": 0.0}]
    p = {"zone": "pilot"}
    b = [{**p, "kind": "mover", "ra": 121.0, "dec": 15.0 + 5 / 3600, "mjd": 61000.001},   # same mover (6″, 2 min)
         {**p, "kind": "mover", "ra": 121.0, "dec": 15.0, "mjd": 61000.0 + 3 / 1440},     # same place, 3 minutes later
         {**p, "kind": "high_pm", "ra": 122.0, "dec": 15.0 + 3 / 3600, "mjd": 5.0},        # same star
         {**p, "kind": "high_pm", "ra": 122.0, "dec": 15.0 + 5 / 3600, "mjd": 5.0},        # 5″ away: another star
         {**p, "kind": "spectral", "ra": 122.0, "dec": 15.0, "mjd": 5.0}]                  # another kind
    assert K.dedupe(a, b) == [b[1], b[3], b[4]]
    assert a[0]["zones"] == ["hunt", "pilot"] and a[1]["zones"] == ["hunt", "pilot"]


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
             "nearest_known_arcsec": np.nan, "gaia_checked": True, "catwise_checked": True, "session": 1, "visit": 1}]
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
    assert doc["counts"] == {"mover": 2, "high_pm": 1, "spectral": 2, "shape": 0}   # no shape_candidates.parquet: 0
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
    assert all(c["grade"] is None for c in cs if c["kind"] != "mover")  # every row has grade; null off movers
    m = cs[0]
    assert m["id"] == "hunt-1-2" and m["rate_arcsec_h"] == pytest.approx(36.0, abs=0.1) and m["pa_deg"] == 60.0
    assert m["zones"] == ["hunt", "pilot"] and m["reasons"] == ["two_exposures", "outlier_flag"]
    assert m["flags"] == ["outlier"] and m["frames_url"] == "cand/hunt-1-2/frames.json"
    assert m["why"] == ("Seen at different places in 2 exposures 2.1 minutes apart, moving about 36″ an hour; "
                        "no known asteroid, comet, planet or moon was predicted within 10′.")
    two = cs[1]
    assert two["rate_arcsec_h"] is None and two["reasons"] == ["two_detectors", "known_object_near", "outlier_flag"]
    assert two["frames_url"] is None and two["zones"] == ["hunt"]
    hp = next(c for c in cs if c["kind"] == "high_pm")
    assert hp["det"] == 6 and hp["snr"] == pytest.approx(50.0 * np.sqrt(NCH_DET), abs=0.1)
    assert hp["flags"] == ["outlier"]                  # set on both of its dots
    assert hp["zones"] == ["hunt", "pilot"]
    sp = next(c for c in cs if c["kind"] == "spectral")
    assert sp["id"] == "hunt-sp-100" and sp["mjd"] == pytest.approx(61000.0 + 1.05 / 1440, abs=1e-6)
    near = next(c for c in cs if c["id"] == "hunt-sp-101")
    assert near["reasons"] == ["gaia_nearby", "class_star", "variable"] and near["catwise_match"] and near["gaia_match"]
    text = K.dumps(doc)
    assert json.loads(text) == doc and text.count("\n") == len(cs) + 2
    small = K.sample(doc, per_kind=1)
    assert small["synthetic"] is False and small["counts"] == doc["counts"] and len(small["candidates"]) <= len(cs)


# Movers listed until 15 Nov that are a planet or a moon (JPL Horizons within 2″): Iapetus 4.6′ from Saturn (grade A),
# Uranus itself, Himalia 31′ from Jupiter
IAPETUS = {"ra": 356.404831, "dec": -4.126031, "mjd": 61022.064174}
URANUS = {"ra": 58.933291, "dec": 20.15587, "mjd": 60910.449783}
HIMALIA = {"ra": 115.551213, "dec": 20.945184, "mjd": 60967.981055}


def test_build_drops_movers_near_a_planet(tmp_path):
    """A suspects table written before the `planets` veto: the export leaves its planet and moon movers out."""
    store = Store(str(tmp_path / "bucket"))
    _zone_files(store, "hunt", 125.0, 20.0, with_extra_mover=False)
    s = store.read_parquet(results_rel("hunt", "suspects.parquet"))
    extra = [{**s.iloc[0].to_dict(), **pos, "id": f"hunt-{k}-{k}", "obs_ids": ""}
             for k, pos in enumerate((IAPETUS, URANUS, HIMALIA), start=7)]
    _write(store, results_rel("hunt", "suspects.parquet"), pd.concat([s, pd.DataFrame(extra)], ignore_index=True))
    doc = K.build(store, ["hunt"])
    assert [c["id"] for c in doc["candidates"] if c["kind"] == "mover"] == ["hunt-1-2"]
    assert doc["counts"]["mover"] == 1


def test_drop_planets_doc_changes_nothing_else():
    """--drop-planets on an existing export: only the movers near a planet go; counts follow; every other line of the
    file is byte for byte the same."""
    def cand(cid, kind, pos, **kw):
        c = {k: None for k in K.FIELDS}
        c.update({"id": cid, "kind": kind, **pos, "clock": "hours" if kind == "mover" else "months", "det": 2,
                  "zone": "ecl35", "zones": ["ecl35"], "score": 0.5, "gaia_match": False, "catwise_match": False,
                  "galactic_b": -60.123, "labels": [], "reasons": [], "why": "Seen …; ok.", "flags": [], **kw})
        return c
    far = {"ra": 122.250475, "dec": 20.897236, "mjd": 61142.911622}
    doc = {"generated": "2026-11-15T06:48:11Z", "data_release": "DR1+QR3", "zones": ["ecl35", "ecl05", "ecl11"],
           "counts": {"mover": 4, "high_pm": 1, "spectral": 0, "shape": 0},
           "candidates": [cand("ecl35-1-1", "mover", IAPETUS, grade="A", frames_url="cand/ecl35-1-1/frames.json"),
                          cand("ecl35-2-2", "mover", far, grade="B", snr=12.3, rate_arcsec_h=20.5),
                          cand("ecl05-3-3", "mover", URANUS, grade="C"),
                          cand("ecl11-4-4", "mover", HIMALIA, grade="A"),
                          cand("ecl05-pm-5", "high_pm", URANUS, pm_mas_yr=900.1)]}  # not a mover: kept
    before = K.dumps(doc)
    new, by = K.drop_planets_doc(json.loads(before))
    assert by == {"Jupiter": 1, "Saturn": 1, "Uranus": 1}
    assert [c["id"] for c in new["candidates"]] == ["ecl35-2-2", "ecl05-pm-5"]
    assert new["counts"] == {"mover": 1, "high_pm": 1, "spectral": 0, "shape": 0}
    after = K.dumps(new)
    old_lines, new_lines = before.splitlines(), after.splitlines()
    gone = {i for i, line in enumerate(old_lines) if '"ecl35-1-1"' in line or '"ecl05-3-3"' in line or '"ecl11-4-4"' in line}
    kept = [line.rstrip(",") for i, line in enumerate(old_lines) if i not in gone]
    assert [line.rstrip(",") for line in new_lines] == [
        kept[0].replace('"mover":4', '"mover":1'), *kept[1:]]
    assert K.drop_planets_doc(new)[1] == {}                          # idempotent


def test_drop_planets_cli(tmp_path):
    path = tmp_path / "candidates.json"
    doc = {"generated": "g", "data_release": "DR1", "zones": ["ecl05"], "counts": {"mover": 1, "high_pm": 0, "spectral": 0,
                                                                                  "shape": 0},
           "candidates": [{**{k: None for k in K.FIELDS}, "id": "ecl05-3-3", "kind": "mover", **URANUS}]}
    path.write_text(K.dumps(doc))
    assert K.main(["--drop-planets", str(path)]) == 0
    assert json.loads(path.read_text())["candidates"] == [] and json.loads(path.read_text())["counts"]["mover"] == 0


def test_source_without_a_usable_spectrum(tmp_path):
    """No channel with positive, finite S/N in any detector: det, snr and flux_ujy are None in source_stats (not D1,
    S/N 0 from the argmax of six zeros); the export writes snr and flux_ujy null and the default detector."""
    store = Store(str(tmp_path / "bucket"))
    _zone_files(store, "hunt", 125.0, 20.0, with_extra_mover=False)
    n = NDET * NCH_DET
    good = np.full(n, 10.0)
    good[5 * NCH_DET:] = 50.0                                         # brightest in D6
    flux = np.stack([good, np.full(n, np.nan), np.full(n, -5.0), np.zeros(n), np.full(n, 5.0)])
    err = np.stack([np.ones(n), np.ones(n), np.ones(n), np.ones(n), np.zeros(n)])
    _write(store, results_rel("hunt", "sources.parquet"), pd.DataFrame({
        # 100 is the zone's high_pm source: all NaN; 101 negative, 102 zero, 103 flux with zero errors; 99 the control
        "source_id": [99, 100, 101, 102, 103], "ra": [125.3, 125.1, 125.2, 125.3, 125.4], "dec": [20.0] * 5,
        "flux_ujy": list(flux), "err_ujy": list(err), "saturated": [False] * 5, "variable": [False] * 5}))
    st = K.source_stats(store, "hunt", np.array([99, 100, 101, 102, 103])).set_index("source_id")
    assert st.loc[99, "det"] == 6 and st.loc[99, "snr"] == pytest.approx(50.0 * np.sqrt(NCH_DET))
    assert st.loc[99, "flux_ujy"] == 50.0
    for sid in (100, 101, 102, 103):
        assert pd.isna(st.loc[sid, "det"]) and np.isnan(st.loc[sid, "snr"]) and np.isnan(st.loc[sid, "flux_ujy"]), sid
    doc = K.build(store, ["hunt"])
    hp = next(c for c in doc["candidates"] if c["kind"] == "high_pm")
    assert hp["id"] == "hunt-pm-100"
    assert hp["snr"] is None and hp["flux_ujy"] is None and hp["det"] == K.DEFAULT_DET
    assert json.loads(K.dumps(doc)) == doc                            # no NaN reaches the file


def test_zone_boxes_order():
    # the hunt box contains the pilot box: hunt first
    h, p = ZONES["hunt"], ZONES["pilot"]
    assert h.ra_min <= p.ra_min and h.ra_max >= p.ra_max and h.dec_min <= p.dec_min and h.dec_max >= p.dec_max


def test_movers_carry_the_suspects_grade():
    """grade and the failed gates come from suspects.parquet; grade C scores x MOVER_GRADE_C; ungraded zones give null."""
    s = _suspects([{"id": "a", "ra": 125.0, "dec": 20.0, "mjd": 61000.0, "det": 3, "detectors": "3", "visit": 1, "session": 1,
                    "grade": "B", "reasons": np.array([], dtype=object)},
                   {"id": "b", "ra": 125.1, "dec": 20.0, "mjd": 61000.0, "det": 3, "detectors": "3", "visit": 1, "session": 1,
                    "grade": "C", "reasons": np.array(["hot_pixel", "edge"], dtype=object)}])
    a, b = K.movers(s, "hunt", None)
    assert a["grade"] == "B" and b["grade"] == "C" and b["reasons"][-2:] == ["hot_pixel", "edge"]
    assert b["score"] == pytest.approx(a["score"] * K.MOVER_GRADE_C)
    assert set(b["reasons"]) <= set(K.REASONS["mover"])
    old = K.movers(s.drop(columns=["grade", "reasons"]), "hunt", None)
    assert old[0]["grade"] is None and old[1]["score"] == pytest.approx(a["score"])


def test_grade_a_movers_name_their_track(monkeypatch):
    """grade A (suspects.py: on a tracklets track): kept as A, `why` names the track, and its own two dots' weak motion
    gives no motion_not_significant; a B with the same dots does get it."""
    row = {"ra": 125.0, "dec": 20.0, "mjd": 61000.0, "det": 3, "detectors": "3", "visit": 1, "session": 1,
           "reasons": np.array([], dtype=object)}
    s = _suspects([{**row, "id": "a", "grade": "A"}, {**row, "id": "b", "ra": 125.1, "grade": "B"}])
    s["n_sessions"] = pd.array([3, None], dtype="Int16")  # as suspects.parquet: null off a track
    s["rate_arcsec_h"], s["pa_deg"], s["rate_sigma"] = [25.0, np.nan], [80.0, np.nan], [0.5, np.nan]
    monkeypatch.setattr(K, "match_mover", lambda *a, **k: pd.DataFrame({"mjd": [61000.0, 61000.0 + 2.1 / 1440]}))
    monkeypatch.setattr(K, "motion", lambda d: (1.0, 1.0, 45.0))  # 1″/h ± 1″/h: not significant on its own
    a, b = K.movers(s, "hunt", pd.DataFrame({"sid": [0, 1]}))
    assert a["grade"] == "A" and "motion_not_significant" not in a["reasons"]
    assert "followed over 3 sessions of its visit at 25.0″ an hour, 50σ" in a["why"]
    assert b["grade"] == "B" and "motion_not_significant" in b["reasons"] and "followed" not in b["why"]
    assert a["score"] == pytest.approx(b["score"] / CK.WARN_FACTOR)  # b: may_not_move (warn)
    assert a["rate_arcsec_h"] == 1.0 and a["pa_deg"] == 45.0  # contract: from its own dots
    assert K.mover_track(next(s.drop(columns=["n_sessions"]).itertuples())) is None  # no track columns: as before


def test_shape_candidates(tmp_path):
    store = Store(str(tmp_path / "bucket"))
    assert K.shape_candidates(store, "hunt") == []                     # no file: no shape candidates
    store.write_parquet(results_rel("hunt", K.SHAPE_FILE), pd.DataFrame({
        "source_id": [42], "ra": [125.5], "dec": [20.1], "mjd": [60800.5], "det": [3], "snr": [85.2], "flux_ujy": [310.0],
        "score": [0.41], "sig": [8.0], "excess": [0.07], "pa_deg": [37.2], "n_dots": [40], "steady_pa": [True],
        "one_visit": [False], "gaia_match": [True], "gaia_g": [15.1], "ruwe": [1.0], "catwise_match": [True],
        "reasons": ["elongated,steady_pa,one_catalogue_source,not_a_code"],
        "why": ["Looks like two objects here but the catalogues list one: worth a look."], "flags": [""]}))
    (c,) = K.shape_candidates(store, "hunt")
    assert c["id"] == "hunt-sh-42" and c["kind"] == "shape" and c["clock"] == "months" and c["det"] == 3
    assert c["reasons"] == ["elongated", "steady_pa", "one_catalogue_source"] and c["flags"] == [] and c["labels"] == []
    c["galactic_b"] = 30.0
    out = K.finish(c)
    assert list(out) == list(K.FIELDS) and out["pa_deg"] == 37.2 and out["gaia_match"] is True and out["rate_arcsec_h"] is None


def test_clean_labels_pah_ice_and_m_dwarfs():
    c = _classes()
    c["gaia_bp_rp"] = [np.nan, 2.9, 1.0, np.nan, np.nan]   # 2: an M dwarf at 25 pc with water vapour
    c.loc[4, ["labels", "label_h2o_ice"]] = ["h2o_ice", True]
    pz = pd.DataFrame({"source_id": [4, 5], "z_best": [0.015, 0.2], "good": [True, True], "label_pah_33_z": [False, False]})
    out = K.clean_labels(c, pz)
    assert out["labels"].tolist() == ["ch4", "", "co_23", "pah_33", ""]  # vapour expected; ice on a galaxy at z 0.2
    pz.loc[0, "z_best"] = 0.1  # PAH at z ≈ 0 in a galaxy at z = 0.1: wrong place
    assert K.clean_labels(c, pz)["labels"].tolist()[3] == ""
    pz.loc[0, "label_pah_33_z"] = True  # photoz found it at the galaxy's redshift
    assert K.clean_labels(c, pz)["labels"].tolist()[3] == "pah_33"
    assert K.clean_labels(c, None)["labels"].tolist()[3] == ""  # no photo-z: no PAH
    s = K.select_spectral(out)
    assert s["source_id"].tolist() == [1, 4]  # 2 lost its only label; 5 its ice


def test_gaia_blend_counts_as_gaia(tmp_path):
    store = Store(str(tmp_path))
    mjd = 61000.0
    src = pd.DataFrame({"source_id": [1, 2, 3], "ra": [125.0, 125.1, 125.2], "dec": [20.0, 20.0, 20.0]})
    _write(store, results_rel("hunt", "sources.parquet"), src)
    _write(store, results_rel("hunt", "crossids.parquet"), src[["source_id"]].assign(epoch_mjd=mjd))
    dt = (mjd - 57388.5) / 365.25
    ra_pm = 125.1 - 500.0 * dt / 3.6e6 / np.cos(np.deg2rad(20.0)) + 4.0 / 3600 / np.cos(np.deg2rad(20.0))
    _write(store, "refcat/hunt.parquet", pd.DataFrame({
        "ref_id": ["a", "b", "c", "d"], "cat": ["gaia", "gaia", "gaia", "catwise"],
        "ra": [125.0 + 4.5 / 3600 / np.cos(np.deg2rad(20.0)), ra_pm, 125.2 + 3 / 3600, 125.2],
        "dec": [20.0, 20.0, 20.0, 20.0], "epoch_mjd": [57388.5] * 3 + [57170.0],
        "pmra": [0.0, 500.0, 0.0, 0.0], "pmdec": [0.0] * 4, "g_mag": [18.0, 19.0, 20.5, np.nan]}))
    # 1: a G 18 star 4.5″ away; 2: a fast star 4″ away at the source's epoch (4.8″ + 7.6″ at 2016); 3: G 20.5 only
    assert K.gaia_blends(store, "hunt", np.array([1, 2, 3])).tolist() == [1, 2]


def test_pah_only_rows_are_capped():
    n = K.SPECTRAL_PAH_ONLY_CAP + 5
    c = pd.DataFrame({"source_id": np.arange(n + 1), "labels": ["pah_33"] * n + ["h2o_vapour"],
                      "brown_dwarf_like": [False] * (n + 1), "label_pah_33": [True] * n + [False],
                      "label_h2o_vapour": [False] * n + [True], "gaia_g": np.nan, "gaia_sep_arcsec": np.nan})
    s = K.select_spectral(c)
    assert (s["labels"] == "pah_33").sum() == K.SPECTRAL_PAH_ONLY_CAP and (s["labels"] == "h2o_vapour").sum() == 1


def test_motion_significance_uses_scaled_errors():
    """36″/h over 2.1 min with 0.25″ raw pos_err: 3.6σ on the raw errors, 2.2σ once they are × 1.6 (not significant)."""
    d = _mover_dots()
    d["pos_err_arcsec"] = 0.25
    rate, err, _ = K.motion(d)
    assert rate / (err / C.POS_ERR_SCALE) > K.RATE_SIGMA_MIN > rate / err


def test_graded_c_movers_say_why_first():
    """A grade C mover's `why` starts with the vetting's verdict in words; B and ungraded ones read as before."""
    row = {"ra": 125.0, "dec": 20.0, "mjd": 61000.0, "det": 3, "detectors": "3", "visit": 1, "session": 1}
    s = _suspects([{**row, "id": "b", "grade": "B", "reasons": np.array([], dtype=object)},
                   {**row, "id": "c", "ra": 125.1, "grade": "C", "reasons": np.array(["hot_pixel", "2mass", "new_gate"], dtype=object)},
                   {**row, "id": "d", "ra": 125.2, "grade": "C", "reasons": np.array([], dtype=object)}])
    b, c, d = K.movers(s, "hunt", None)
    assert not b["why"].startswith("Failed")
    assert c["why"].startswith("Failed the vetting, most likely an artefact or a catalogued source: a dot sits on a hot "
                               "pixel; a 2MASS source lies within 6″; new gate. Seen at different places")
    assert d["why"].startswith("Failed the vetting, most likely an artefact or a catalogued source. Seen")
    assert c["why"].endswith(b["why"])


def test_edge_dot_lowers_the_score():
    """A dot within VET_EDGE_PX of the detector edge: reason edge (once) and the strong check `edge` (score ×
    STRONG_FACTOR); on a grade C mover the C factor stands for its gates (no second factor)."""
    row = {"ra": 125.0, "dec": 20.0, "mjd": 61000.0, "det": 3, "detectors": "3", "visit": 1, "session": 1}
    s = _suspects([{**row, "id": "in", "dot_x": [500.0, 520.0], "dot_y": [800.0, 800.0]},
                   {**row, "id": "x0", "ra": 125.1, "dot_x": [3.0, 20.0], "dot_y": [800.0, 800.0]},
                   {**row, "id": "y1", "ra": 125.2, "dot_x": [500.0, 520.0], "dot_y": [800.0, 2036.0]},
                   {**row, "id": "nan", "ra": 125.3, "dot_x": [np.nan, np.nan], "dot_y": [np.nan, np.nan]}])
    a, x0, y1, nn = K.movers(s, "hunt", None)
    assert "edge" not in a["reasons"] and "edge" not in nn["reasons"] and nn["score"] == pytest.approx(a["score"])
    for m in (x0, y1):
        assert m["reasons"].count("edge") == 1 and m["score"] == pytest.approx(a["score"] * CK.STRONG_FACTOR)
        assert [c["code"] for c in m["checks"]] == ["edge"] and m["checks"][0]["level"] == "strong"
        assert m["why"].startswith("Possibly an artefact: a dot is at the detector's edge. Seen")  # ungraded: no verdict
    s["grade"] = ["B", "C", "B", "B"]
    s["reasons"] = [np.array([], dtype=object), np.array(["edge"], dtype=object), np.array([], dtype=object),
                    np.array([], dtype=object)]
    a, x0, _, _ = K.movers(s, "hunt", None)
    assert x0["reasons"].count("edge") == 1  # the vetting's own edge gate: not listed twice
    assert x0["score"] == pytest.approx(a["score"] * K.MOVER_GRADE_C)
    assert K.edge_dot(next(s.drop(columns=["dot_x", "dot_y"]).itertuples())) is False  # no dot lists: no penalty


def test_highpm_parallax_fit_and_unsteady_reasons():
    c = K.high_pms(_hp(fit_mode="pm+gaia_plx"), "hunt", {})[0]
    assert c["reasons"] == ["pm_fit", "vetted", "parallax_fit"] and set(c["reasons"]) <= set(K.REASONS["high_pm"])
    assert K.high_pms(_hp(fit_mode="pm_leak"), "hunt", {})[0]["reasons"] == ["pm_fit", "vetted"]
    u = K.high_pms(_hp(vetted=False, reason="unsteady", fit_mode="pm+plx"), "hunt", {})[0]
    assert u["reasons"] == ["pm_fit", "parallax_fit", "unsteady"] and "out and back" in u["why"]
    assert u["score"] == pytest.approx(np.exp(-(1 / 29.3) / 0.2) * 0.3)
