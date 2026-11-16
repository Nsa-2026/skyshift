"""The `changes` step (changes_step.py) on a small synthetic zone, and the site export (changes_export.py)."""

import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import changes_export as EX
from skyshift_pipeline import changes_step as ST
from skyshift_pipeline.store import Store

RNG = np.random.default_rng(11)
BOX = (120.5, 124.1, 14.4, 17.9)       # config.ZONES["pilot"]
N = 400                                 # sources 0..399, Gaia-matched when even
CHANGED = 8                             # source 8 fades to 0.7 in D2 and D3 in visit 2
OUT_ID, IN_ID, VAR_ID = 9001, 9002, 1000008   # forced-only Gaia stars: outside the box, inside it; source 8's Gaia id


def _lc(rows, sid, ref, origin, factor):
    for d in range(1, 7):
        for v in (1, 2):
            for k in range(4):
                for j in range(3):
                    mjd = 61000.0 + 180.0 * (v - 1) + 1.0 * k + j * 2.1 / 1440.0
                    f = factor.get(d, 1.0) if v == 2 else 1.0
                    rows.append((sid, ref, origin, mjd, v, d, f + RNG.normal(0, 0.02), 2.0, 100.0, True, True))


def _zone(root) -> Store:
    st = Store(str(root))
    rows = []
    for s in range(N):
        _lc(rows, s, "", "dot", {2: 0.7, 3: 0.7} if s == CHANGED else {})
    _lc(rows, -1, f"gaia:{OUT_ID}", "forced", {1: 1.5, 2: 1.5})
    _lc(rows, -1, f"gaia:{IN_ID}", "forced", {1: 1.5, 2: 1.5})
    pts = pd.DataFrame(rows, columns=ST.POINT_COLUMNS)
    st.write_parquet("results/pilot/lightcurves.parquet", pts)
    ra = np.linspace(121.0, 123.5, N)
    dec = np.full(N, 16.0) + RNG.uniform(-1, 1, N)
    gid = np.where(np.arange(N) % 2 == 0, 1_000_000 + np.arange(N), -1)
    xid = pd.DataFrame({c: np.nan for c in ST.XID_COLUMNS}, index=range(N))
    xid["source_id"] = np.arange(N)
    xid["ra"], xid["dec"], xid["gaia_ra"], xid["gaia_dec"] = ra, dec, ra, dec
    xid["gaia_id"] = gid
    xid["gaia_ref_id"] = [f"gaia:{g}" if g > 0 else "" for g in gid]
    xid["gaia_g"] = np.where(gid > 0, 15.0, np.nan)
    xid["gaia_n"] = np.where(gid > 0, 1, 0)
    xid["catwise_ref_id"] = ["" if g > 0 else f"catwise:J0800{i:02d}.00+160000.0" for i, g in enumerate(gid % 100)]
    xid["tmass_ref_id"] = ""
    st.write_parquet("results/pilot/crossids.parquet", xid)
    st.write_parquet("results/pilot/sources.parquet", pd.DataFrame({
        "source_id": np.arange(N), "ra": ra, "dec": dec, "detectors": np.int8(63), "saturated": False}))
    ref = pd.DataFrame({"ref_id": [f"gaia:{g}" for g in gid[gid > 0]] + [f"gaia:{IN_ID}"], "cat": "gaia",
                        "ra": np.r_[ra[gid > 0], 122.0], "dec": np.r_[dec[gid > 0], 15.0], "g_mag": 15.0})
    st.write_parquet("refcat/pilot.parquet", ref)
    st.write_parquet("results/pilot/orphans.parquet", pd.DataFrame({
        "source_id": [77777], "ra": [122.5], "dec": [16.5], "n_dots": [27], "n_sessions": [13], "mjd_mean": [61001.0],
        "visit": [1], "other_visit": [2], "cover_other": [112], "mjd_other": [61181.0], "snr_max": [61.0],
        "reason": [""], "rate": [0.0], "rate_err": [0.01], "known_name": [""]}))
    src = st.read_parquet("results/pilot/sources.parquet")
    st.write_parquet("results/pilot/sources.parquet", pd.concat([src, pd.DataFrame({
        "source_id": [77777], "ra": [122.5], "dec": [16.5], "detectors": [np.int8(0b11111)], "saturated": [False]})]))
    return st


class FakeGaia:
    """ESA's archive: positions and variability by id, no neighbours; records every query."""

    def __init__(self):
        self.adql = []

    def __call__(self, adql):
        self.adql.append(adql)
        if "vari_classifier_result" in adql:
            return pd.DataFrame({"source_id": [OUT_ID, VAR_ID], "ra": [119.0, 121.0], "dec": [15.0, 16.0],
                                 "phot_g_mean_mag": [13.0, 15.0], "phot_variable_flag": ["NOT_AVAILABLE", "VARIABLE"],
                                 "best_class_name": [None, "ECL"]})
        return pd.DataFrame({"ra": [], "dec": [], "phot_g_mean_mag": []})


def test_run_lists_changes_with_checks(tmp_path):
    st = _zone(tmp_path)
    fake = FakeGaia()
    rep = ST.run(st, "pilot", gaia=ST.GaiaLookup(None, tap=fake), with_null=True)
    df = st.read_parquet("results/pilot/changes.parquet")
    assert list(df.columns) == list(ST.CHANGE_COLUMNS)
    by = df.set_index("id")
    # the changed source, the forced-only star outside the box, the vanished source; never the forced one inside
    assert set(by.index) == {f"pilot-ch-gaia{VAR_ID}", f"pilot-ch-gaia{OUT_ID}", "pilot-ch-src77777"}
    s = by.loc[f"pilot-ch-gaia{VAR_ID}"]
    assert s["kind"] == "brightness" and s["detector"] in (2, 3) and s["change_frac"] == pytest.approx(-0.3, abs=0.03)
    assert s["gaia_variable"] == 1 and s["gaia_class"] == "ECL" and s["population"] == "source"
    assert s["text"].startswith(f"Gaia DR3 {VAR_ID} went from 1.0") and "% fainter" in s["text"]
    codes = {c["code"]: c for c in json.loads(s["checks"])}
    assert codes["known_variable"]["text"].startswith("Gaia already lists it as variable (DR3 class ECL: eclipsing binary)")
    assert "other_detectors_agree" in codes
    o = by.loc[f"pilot-ch-gaia{OUT_ID}"]
    assert o["population"] == "forced_outside" and o["ra"] == pytest.approx(119.0) and o["gaia_variable"] == 0
    assert o["neighbours_checked"]          # Gaia was asked around it (nothing there)
    v = by.loc["pilot-ch-src77777"]
    assert v["kind"] == "vanished" and "gone in visit 2" in v["text"] and "112 of visit 2's photos" in v["text"]
    assert rep["brightness"]["alerts_by_population"].get("forced_inside") == 1
    assert rep["brightness"]["null"]["stars"] > 0 and rep["new"]["vanished"] == 1
    assert any("source_id BETWEEN" in q for q in fake.adql)        # neighbours by primary-key ranges only


def test_gaia_down_leaves_variability_unknown_and_says_so(tmp_path):
    st = _zone(tmp_path)

    def down(adql):
        raise ConnectionError("archive down")
    gaia = ST.GaiaLookup(None, tap=down)
    rep = ST.run(st, "pilot", gaia=gaia, with_null=False)
    by = st.read_parquet("results/pilot/changes.parquet").set_index("id")
    s = by.loc[f"pilot-ch-gaia{VAR_ID}"]
    assert s["gaia_variable"] == -1                    # unknown, not "not variable"
    assert f"pilot-ch-gaia{OUT_ID}" not in by.index    # no position without Gaia: not listed
    assert rep["gaia_errors"] and rep["brightness"]["candidates_by_population"].get("unknown") == 1


def test_cli_all_zones_and_skip_done(tmp_path):
    st = _zone(tmp_path)
    assert ST.zones_with_lightcurves(st) == ["pilot"]
    args = ["--zones", "all", "--root", str(tmp_path), "--no-gaia", "--no-null"]
    assert ST.main(args) == 0
    out = tmp_path / "results" / "pilot" / "changes.parquet"
    first = out.stat().st_mtime_ns
    assert ST.main(args + ["--skip-done"]) == 0
    assert out.stat().st_mtime_ns == first                  # done zones are not redone


def test_catwise_position_and_slugs():
    ra, dec = ST.catwise_position("catwise:J081628.26+142456.2")
    assert ra == pytest.approx(124.11775, abs=1e-5) and dec == pytest.approx(14.41561, abs=1e-5)
    assert ST.ref_slug(42, "", 5) == "gaia42"
    assert ST.ref_slug(-1, "catwise:J081628.26+142456.2", -1) == "catwiseJ081628.26+142456.2"
    assert ST.ref_slug(-1, "", 63782006953200) == "src63782006953200"
    assert ST.display_name(-1, "2mass:J1+2", 7) == "2MASS J1+2"


def test_points_read_one_detector_and_some_stars(tmp_path):
    p = pd.DataFrame({"source_id": [1, 1, 2, -1], "ref_id": ["", "", "", "gaia:5"], "origin": "dot",
                      "mjd": 1.0, "visit": 1, "detector": [1, 2, 1, 1], "rel_flux": 1.0, "flux_err_cal_ujy": 1.0,
                      "model_ujy": 1.0, "photo_ok": True, "detected": True})
    p.to_parquet(tmp_path / "p.parquet")
    pts = ST.Points(tmp_path / "p.parquet")
    assert pts.detector(1)["source_id"].tolist() == [1, 2, -1]
    assert sorted(pts.of({2}, {"gaia:5"})["source_id"].tolist()) == [-1, 2]
    assert len(pts.of([], [])) == 0


def test_export_order_dedupe_and_feed(tmp_path):
    st = _zone(tmp_path)
    ST.run(st, "pilot", gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=False)
    # the same table as a second, larger zone: each change listed once, from the larger zone
    st.write_parquet("results/hunt/changes.parquet", st.read_parquet("results/pilot/changes.parquet").assign(
        zone="hunt", id=lambda d: d["id"].str.replace("pilot-", "hunt-")))
    zones = EX.finished_zones(st)
    assert zones == ["hunt", "pilot"]
    doc = EX.build(st, zones)
    ids = [e["id"] for e in doc["changes"]]
    assert len(ids) == 3 and all(i.startswith("hunt-") for i in ids)
    assert all(e["zones"] == ["hunt", "pilot"] for e in doc["changes"])
    # the star Gaia already lists as variable comes after the unexpected ones
    assert doc["changes"][-1]["gaia_variable"] is True and doc["gaia_variable"] == 1
    assert doc["counts"] == {"brightness": 2, "colour": 0, "appeared": 0, "vanished": 1}
    e = next(x for x in doc["changes"] if x["kind"] == "brightness" and x["gaia_variable"])
    assert e["headline"][:2] in ("29", "30", "31") and "% fainter in D" in e["headline"]
    assert e["det"] in (2, 3) and e["clock"] == "months"
    assert 0 < e["score"] < 1 and e["date_after"] == "2026-05-21"
    json.loads(EX.dumps(doc))                         # valid JSON, no NaN
    feed = ET.fromstring(EX.atom(doc, "https://example.org"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = feed.findall("a:entry", ns)
    assert len(entries) == 3
    assert entries[0].find("a:id", ns).text.startswith(EX.TAG)
    links = [ln.get("href") for ln in entries[0].findall("a:link", ns)]
    assert any("#/changes/hunt-ch-" in h for h in links) and any("clock=months" in h for h in links)
    updated = [x.find("a:updated", ns).text for x in entries]
    assert updated == sorted(updated, reverse=True)   # newest visit first


def test_headline_words():
    base = {"kind": "brightness", "change_frac": 8.76, "detector": 2, "rel_after": 1.77}
    assert EX.headline(base) == "876% brighter in D2 (1.1–1.6 µm)"
    assert EX.headline({**base, "change_frac": -0.9, "rel_after": 0.1}).startswith("faded below detection in D2")
    assert EX.headline({"kind": "colour", "colour_change": 0.23}) == "became redder: +23% from D1 to D6"
    assert EX.headline({"kind": "appeared", "visit_after": 3}) == "appeared in visit 3"
