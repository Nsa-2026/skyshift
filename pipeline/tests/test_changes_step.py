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


def test_drop_points_keeps_small_reads_only(tmp_path):
    st = _zone(tmp_path / "root")
    st.local = False                    # read it like the bucket: column reads and a streamed copy of the points
    for keep, cache in ((True, tmp_path / "keep"), (False, tmp_path / "drop")):
        ST.run(st, "pilot", cache=cache, gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=False, keep_points=keep)
        names = [p.name for p in cache.rglob("*.parquet")]
        assert any(n.startswith("results__pilot__crossids") for n in names)     # the small reads stay either way
        assert any("lightcurves" in n for n in names) == keep
        assert not [p for p in cache.iterdir() if p.is_dir() and p.name.startswith("skyshift-changes-")]
    assert st.read_parquet("results/pilot/changes.parquet")["id"].str.startswith("pilot-ch-").all()


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


def test_star_chunks_give_the_same_changes(tmp_path):
    """Memory follows the chunk size, not the zone: tiny star chunks list the same changes, numbers and report."""
    st = _zone(tmp_path / "zone")
    reps, dfs = [], []
    for name, cp in (("one", 10**9), ("many", 500)):
        out = Store(str(tmp_path / name))
        rep = ST.run(st, "pilot", gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=True, chunk_points=cp, out=out)
        reps.append(rep["brightness"])
        dfs.append(out.read_parquet("results/pilot/changes.parquet"))
    assert reps[0]["chunks"] == 1 and reps[1]["chunks"] > 3
    pd.testing.assert_frame_equal(dfs[0], dfs[1])
    drop = {"chunks", "chunk_points"}
    assert {k: v for k, v in reps[0].items() if k not in drop} == {k: v for k, v in reps[1].items() if k not in drop}


# --- known bodies on a change (asteroids, comets, planets, moons from the `known` step's session files) ---------------
PARTIAL = 10                            # source 10 fades to 0.7 in D2 and D3 in sessions 0–2 of visit 2 only
ALLWISE = "allwise:J080000.00+150000.0"  # a red-layer forced row outside the box (RA 120.0°, Dec +15.0°)


def _mjd(v, k, j):
    return 61000.0 + 180.0 * (v - 1) + 1.0 * k + j * 2.1 / 1440.0


def _square(ra0, dec0, half):
    v = [(ra0 - half, dec0 - half), (ra0 + half, dec0 - half), (ra0 + half, dec0 + half), (ra0 - half, dec0 + half)]
    return "POLYGON ICRS " + " ".join(f"{a} {b}" for a, b in v)


def _zone_known(root, bodies=True) -> Store:
    """_zone plus a partial change (source 10), an AllWISE forced star, the photo list (2 visits × 4 sessions × 3
    exposures × 6 detectors, footprints over the box) and the `known` step's session files: an asteroid 6″ from source
    8 in one session of visit 2 (its change stays), a comet 5″ from source 10 in the three sessions that carry its
    change (it goes), a second asteroid 25″ from source 8 (too far)."""
    from skyshift_pipeline import config as C
    from skyshift_pipeline.known import run_rel

    st = _zone(root)
    pts = st.read_parquet("results/pilot/lightcurves.parquet")
    k_of = np.round(pts["mjd"].to_numpy() - 61000.0 - 180.0 * (pts["visit"].to_numpy() - 1)).astype(int)
    m = ((pts["source_id"].to_numpy() == PARTIAL) & (pts["visit"].to_numpy() == 2) & (k_of <= 2)
         & pts["detector"].isin([2, 3]).to_numpy())
    pts.loc[m, "rel_flux"] *= 0.7
    rows = []
    _lc(rows, -1, ALLWISE, "forced", {1: 1.5, 2: 1.5})
    pts = pd.concat([pts, pd.DataFrame(rows, columns=ST.POINT_COLUMNS)], ignore_index=True)
    st.write_parquet("results/pilot/lightcurves.parquet", pts)
    ph = pd.DataFrame([(f"V{v}K{k}E{j}", d, _mjd(v, k, j), (v - 1) * 4 + k, v, _square(122.3, 16.15, 2.5))
                       for v in (1, 2) for k in range(4) for j in range(3) for d in range(1, 7)],
                      columns=ST.PHOTO_COLUMNS)
    st.write_parquet("photolists/zones/pilot.parquet", ph)
    if not bodies:
        return st
    xid = st.read_parquet("results/pilot/crossids.parquet").set_index("source_id")
    z = C.ZONES["pilot"]
    near = {(2, 1): [("Ceres", 1, "MBA", 7.0, PARTIAL - 2, 6.0)], (2, 2): [("Pallas", 2, "MBA", 8.0, PARTIAL - 2, 25.0)]}
    for k in range(3):
        near.setdefault((2, k), []).append(("C/2025 X1", None, "Comet C", None, PARTIAL, 5.0))
    for (v, s), g in ph.groupby(["visit", "session"], sort=True):
        objs = {c: [] for c in ("name", "number", "ra", "dec", "v", "dra_arcsec_h", "ddec_arcsec_h", "class")}
        per = {c: [] for c in ("obs_id", "detector", "name", "ra", "dec")}
        for name, num, cls, vmag, sid, east in near.get((v, s - 4 * (v - 1)), []):
            ra = xid.at[sid, "ra"] + east / 3600.0 / np.cos(np.radians(xid.at[sid, "dec"]))
            for c, x in zip(objs, (name, num, ra, xid.at[sid, "dec"], vmag, 0.0, 0.0, cls)):
                objs[c].append(x)
            for r in g[g["detector"] == 2].itertuples():
                for c, x in zip(per, (r.obs_id, 2, name, ra, xid.at[sid, "dec"])):
                    per[c].append(x)
        st.write_json(run_rel(z, g, "mpc"), {"zone": "pilot", "session": int(s), "objects": objs, "per_photo": per})
    return st


def test_known_body_passed_warns_or_drops_the_change(tmp_path):
    plain = _zone_known(tmp_path / "plain", bodies=False)
    ST.run(plain, "pilot", gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=False)
    before = set(plain.read_parquet("results/pilot/changes.parquet")["id"])
    assert {f"pilot-ch-gaia{VAR_ID}", f"pilot-ch-gaia{1_000_000 + PARTIAL}"} <= before

    st = _zone_known(tmp_path / "known")
    rep = ST.run(st, "pilot", gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=False)
    by = st.read_parquet("results/pilot/changes.parquet").set_index("id")
    assert set(by.index) == before - {f"pilot-ch-gaia{1_000_000 + PARTIAL}"}       # the comet carried source 10's change
    kb = rep["brightness"]["known_bodies"]
    assert rep["dropped_known_body"] == 1 and kb["dropped"] == 1 and kb["warned"] == 1 and kb["checked"]
    d = kb["dropped_changes"][0]
    assert d["id"] == f"pilot-ch-gaia{1_000_000 + PARTIAL}" and d["body"] == "C/2025 X1" and d["body_kind"] == "comet"
    assert d["sessions_left_out"] == 3 and d["sep_arcsec"] == pytest.approx(5.0, abs=0.1)
    assert kb["files_read"]["mpc"] == 8 and kb["files_missing"]["planets"] == 8
    c = next(x for x in json.loads(by.loc[f"pilot-ch-gaia{VAR_ID}", "checks"]) if x["code"] == "known_body_passed")
    assert c["level"] == "warn" and c["values"]["stays"] and c["values"]["sessions_left_out"] == 1
    assert c["text"].startswith("A known asteroid, (1) Ceres (V 7.0), passed 6″ from the star on 2026-05-21 00:00 UTC")
    assert "Without the session it passed in, the D" in c["text"] and "change stays" in c["text"]
    assert c["look"] == ("Viewer, hours clock, D2: blink the session of 2026-05-21 00:00 UTC; see whether a moving dot "
                         "crosses the star.")
    assert c["values"]["passages"] == 1 and c["values"]["utc"] == "2026-05-21 00:00 UTC"     # Pallas at 25″ is no passage
    others = [x for i, r in by.iterrows() if i != f"pilot-ch-gaia{VAR_ID}" for x in json.loads(r["checks"])]
    assert not any(x["code"] == "known_body_passed" for x in others)


def test_known_check_off_or_without_files_changes_nothing(tmp_path):
    st = _zone_known(tmp_path, bodies=False)
    for kw in ({}, {"check_known": False}):
        rep = ST.run(st, "pilot", gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=False, **kw)
        kb = rep["brightness"]["known_bodies"]
        assert rep["dropped_known_body"] == 0 and kb["warned"] == 0 and not kb["checked"]
        if kw:
            assert kb["why"].startswith("not asked")
        else:                               # no session files: said so, not "checked"
            assert kb["files_missing"] == {"mpc": 8, "planets": 8} and "no session files" in kb["why"]


def test_known_files_from_a_remote_root_are_read_once(tmp_path):
    st = _zone_known(tmp_path / "root")
    st.local = False                    # as the bucket: the session files are kept in the cache
    reps = [ST.run(st, "pilot", cache=tmp_path / "cache", gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=False)
            for _ in range(2)]
    kbs = [r["brightness"]["known_bodies"] for r in reps]
    assert kbs[0]["bytes_read"] > 0 and kbs[1]["bytes_read"] == 0
    assert kbs[0]["dropped_changes"] == kbs[1]["dropped_changes"] and reps[1]["dropped_known_body"] == 1
    assert len(list((tmp_path / "cache" / "known" / "cache" / "mpc" / "pilot").glob("run_*.json"))) == 8


def test_no_session_left_out_keeps_every_change(tmp_path, monkeypatch):
    """listing_without with nothing left out gives the listed changes back: a passage in a session that doesn't exist
    warns on every listed star and drops none."""
    st = _zone_known(tmp_path)
    fake = {"name": "(9) Metis", "kind": "asteroid", "v": 9.0, "session": -1, "mjd": 61180.0, "sep_arcsec": 3.0,
            "radius_arcsec": ST.CHANGES_KNOWN_ARCSEC, "detector": 2, "obs_id": "X"}
    monkeypatch.setattr(ST, "passages", lambda stars, pred: {int(s): [fake] for s in stars["star"]})
    rep = ST.run(st, "pilot", gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=False)
    kb = rep["brightness"]["known_bodies"]
    listed = rep["brightness"]["listed_brightness"] + rep["brightness"]["listed_colour"]
    assert kb["dropped"] == 0 and kb["warned"] == listed >= 3
    df = st.read_parquet("results/pilot/changes.parquet")
    for r in df[df["kind"] == "brightness"].itertuples():
        c = next(x for x in json.loads(r.checks) if x["code"] == "known_body_passed")
        assert c["values"]["points_left_out"] == 0 and c["values"]["sigma_without"] == pytest.approx(r.sigma, abs=0.01)


def test_passages_closest_in_the_exposure_and_planet_reach():
    stars = pd.DataFrame({"star": [1], "ra": [100.0], "dec": [0.0]})
    pred = pd.DataFrame({
        "name": ["Fast", "Slow", "Jupiter", "Io"], "number": [123, None, None, None],
        "class": ["NEO", "MBA", "planet", "moon"], "v": [16.0, 20.0, None, 5.0],
        # Fast: 30″ west at mid-exposure, 2,000″/h east: it crosses the star within the exposure (±56.5 s: ±31″)
        "dra_arcsec_h": [2000.0, 0.0, 10.0, 30.0], "ddec_arcsec_h": [0.0, 0.0, 0.0, 0.0],
        "ra": [100.0 - 30 / 3600, 100.0 + 20 / 3600, 100.0 + 0.5, 100.0 + 0.5], "dec": [0.0, 0.0, 0.0, 0.0],
        "obs_id": "O", "detector": [1, 1, 2, 2], "mjd": 61000.0, "session": [4, 4, 4, 4]})
    got = {p["name"]: p for p in ST.passages(stars, pred)[1]}
    assert set(got) == {"(123) Fast", "Jupiter"}          # Slow at 20″ and Io at 30′ are too far; Jupiter's reach is 80′
    f = got["(123) Fast"]
    assert f["sep_arcsec"] < 0.5 and f["kind"] == "asteroid"
    assert f["mjd"] == pytest.approx(61000.0 + 54.0 / 86400, abs=1e-5)
    assert got["Jupiter"]["kind"] == "planet" and got["Jupiter"]["radius_arcsec"] == 80 * 60.0


def test_names_of_bodies_and_catalogue_rows():
    from skyshift_pipeline import changes as CH

    assert ST.body_name("Jucunda", 948) == "(948) Jucunda" and ST.body_name("(31351)", 31351) == "(31351)"
    assert ST.body_name("C/2025 R2", None) == "C/2025 R2"
    assert ST.body_kind("Comet P", "509P/Catalina") == "comet" and ST.body_kind("moon", "Titan") == "moon"
    assert ST.body_kind("MBA", "Jucunda") == "asteroid" and ST.body_kind(None, "306P/LINEAR") == "comet"
    row = pd.Series({"ref_id": ALLWISE, "gaia_id": -1, "source_id": -1})
    assert CH.name_of(row) == "AllWISE J080000.00+150000.0"
    assert CH.name_of(pd.Series({"ref_id": "", "gaia_id": -1, "source_id": 7})) == "source 7"
    assert ST.display_name(-1, ALLWISE, -1) == "AllWISE J080000.00+150000.0"
    assert ST.ref_slug(-1, ALLWISE, -1) == "allwiseJ080000.00+150000.0"
    assert CH.mjd_utc(61105.22981) == "2026-03-06 05:31 UTC" and CH.mjd_utc(float("nan")) == "?"


def test_allwise_forced_row_is_listed_by_its_designation(tmp_path):
    st = _zone_known(tmp_path, bodies=False)
    ST.run(st, "pilot", gaia=ST.GaiaLookup(None, tap=FakeGaia()), with_null=False)
    by = st.read_parquet("results/pilot/changes.parquet").set_index("id")
    a = by.loc["pilot-ch-allwiseJ080000.00+150000.0"]
    assert a["name"] == "AllWISE J080000.00+150000.0" and a["population"] == "forced_outside"
    assert a["ra"] == pytest.approx(120.0) and a["dec"] == pytest.approx(15.0) and a["ref_id"] == ALLWISE
    assert a["text"].startswith("AllWISE J080000.00+150000.0 went from")
