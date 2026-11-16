"""known_check.py, the `known` check producer. Run from pipeline/ (kete, skyshift_core, skyshift_pipeline):
    cd pipeline && uv run pytest ../scripts/candidates/tests/test_known_check.py
The server environment (test_frames.py) skips this file."""

import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("skyshift_pipeline")
kete = pytest.importorskip("kete")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import known_check as KC  # noqa: E402
from skyshift_pipeline import config as C  # noqa: E402
from skyshift_pipeline import known_mpc as KM  # noqa: E402
from skyshift_pipeline.sky import angsep  # noqa: E402

# 306P/LINEAR as allcometels.json.gz has it (16 Nov 2026); cometels.json.gz has no 306P.
COMET_306P = {"Comet_num": 306, "Orbit_type": "P", "Year_of_perihelion": 2025, "Month_of_perihelion": 8,
              "Day_of_perihelion": 1.647, "Perihelion_dist": 1.272986, "e": 0.59251, "Peri": 0.9146, "Node": 341.339,
              "i": 8.3017, "Epoch_year": 2025, "Epoch_month": 11, "Epoch_day": 21, "H": 19.0, "G": 4.0,
              "Designation_and_name": "306P/LINEAR", "Ref": "MPC191621"}
# The ecl04 track (results/ecl04, D6): photo, MJD-AVG, the dot's ePSF position, SPHEREx's X_SC..VZ_SC from that photo.
DOTS = [
    ("2025W34_1B_0173_1", 60905.898690425, 44.2965196, 21.3271660, (3612.5738689701693, 4324.522544234395, 4188.398249673735, -1.834601091632781, -4.253971398559812, 5.947633190383698)),
    ("2025W34_1B_0173_2", 60905.900182694, 44.2975305, 21.3273317, (3342.1374933877682, 3736.180546191036, 4913.053705793008, -2.351514686932096, -4.853047016805792, 5.26936398450722)),
    ("2025W34_1B_0366_1", 60906.915644314, 44.9283431, 21.7536263, (3773.0530373797333, 5030.616986858165, 3128.4087874855345, -1.052968003656771, -3.3743422128990113, 6.657705459495825)),
    ("2025W34_1B_0366_2", 60906.91713656, 44.9294247, 21.7537259, (3601.590577740564, 4548.635064602925, 3954.5266134720937, -1.6011322775991035, -4.086332872913951, 6.129817367866545)),
    ("2025W34_1B_0366_3", 60906.918628828, 44.9307676, 21.7539285, (3363.41198377912, 3984.4615811252006, 4699.31132747773, -2.114807275972621, -4.715614189589239, 5.489894383785404)),
    ("2025W34_1B_0366_4", 60906.920121074, 44.9315733, 21.7541474, (3059.3246053536627, 3340.006283524497, 5360.210183674117, -2.5921895306869573, -5.259982620713075, 4.7402222710520645)),
    ("2025W34_1B_0451_1", 60907.392046652, 45.2219248, 21.9512434, (3638.3598206418874, 4777.52971754297, 3639.7790114635704, -1.358693098972083, -3.827871454587177, 6.350790905467092)),
    ("2025W34_1B_0451_2", 60907.393538932, 45.2230542, 21.9512775, (3428.8693309122136, 4239.643833113513, 4421.529098034925, -1.8840397515919298, -4.498136360285167, 5.74996648134641)),
]
SC = ("X_SC", "Y_SC", "Z_SC", "VX_SC", "VY_SC", "VZ_SC")
# the two grade A movers of candidates.json (origin/main, 15 Nov) on that track
MOVERS = [
    {"id": "ecl04-670520025038-87705962", "kind": "mover", "zone": "ecl04", "ra": 44.92999, "dec": 21.75381,
     "mjd": 60906.917883, "rate_arcsec_h": 104.9, "pa_deg": 76.4, "det": 6, "score": 0.174, "dots": DOTS[2:6]},
    {"id": "ecl04-667960024758-87704495", "kind": "mover", "zone": "ecl04", "ra": 44.297018, "dec": 21.3272,
     "mjd": 60905.899437, "rate_arcsec_h": 103.9, "pa_deg": 85.8, "det": 6, "score": 0.084, "dots": DOTS[0:2]},
]
CERES = pd.DataFrame({"desig": ["1"], "name": ["Ceres"], "peri_dist": [2.5465], "ecc": [0.0796], "incl": [10.588],
                      "lon_node": [80.25], "peri_arg": [73.3], "peri_time": [2461600.5], "epoch": [2461200.5],
                      "h_mag": [3.34], "g_phase": [0.12], "group_name": ["MBA"]})


def _comets() -> pd.DataFrame:
    return KM.merge_comets(KM.comet_table([]), KM.comet_table([COMET_306P]))


def _items():
    return [{"id": m["id"], "ra": m["ra"], "dec": m["dec"], "mjd": m["mjd"], "rate": m["rate_arcsec_h"], "pa": m["pa_deg"],
             "times": [d[1] for d in m["dots"]], "obs": [d[0] for d in m["dots"]], "from_dots": True} for m in MOVERS]


@pytest.fixture(scope="module")
def idx(tmp_path_factory):
    return KC.index(tmp_path_factory.mktemp("idx"), CERES.copy(), _comets(), "test", 60905.0, 60908.0)


def test_306P_is_predicted_on_every_ecl04_dot_and_both_movers_are_known(idx):
    obs_sc = {d[0]: dict(zip(SC, d[4])) for d in DOTS}
    items = _items()
    hits = KC.predict_mpc(idx, items, obs_sc)
    names, comet = KC.index_names(idx), idx.comet
    for m, it in zip(MOVERS, items):
        h = [x for x in hits[m["id"]] if names[x["row"]] == "306P/LINEAR"]
        assert sorted(x["k"] for x in h) == list(range(len(m["dots"])))
        for x in h:  # the MPC orbit from SPHEREx lands on the measured dots (ecl04-discovery.md: 0.1–1.1″)
            d = m["dots"][x["k"]]
            assert angsep(x["ra"], x["dec"], d[2], d[3]) * 3600 < 2.0
            assert 95 < x["rate"] < 106 and 80 < x["pa"] < 87  # seen from SPHEREx: PA 82–85°, not the track's 54°
        bs = KC.bodies(it, hits[m["id"]], names, comet)
        e = KC.mover_check(m, it, bs, 1, 1)
        assert e["status"] == "known" and e["known_name"] == "306P/LINEAR"
        ch = e["checks"][0]
        assert ch["code"] == "known_nearby" and ch["level"] == "strong" and "comet 306P/LINEAR" in ch["text"]
        assert ch["values"]["sep_max_arcsec"] < 2.0 and ch["values"]["body"] == "comet"


def test_geocentre_alone_still_finds_306P_but_further_off(idx):
    hits = KC.predict_mpc(idx, _items()[:1], {})  # no headers: the Earth's centre
    names = KC.index_names(idx)
    seps = [angsep(x["ra"], x["dec"], MOVERS[0]["dots"][x["k"]][2], MOVERS[0]["dots"][x["k"]][3]) * 3600
            for x in hits[MOVERS[0]["id"]] if names[x["row"]] == "306P/LINEAR"]
    assert len(seps) == 4 and max(seps) > 2.5  # SPHEREx's parallax: 3–6″ at 0.67 AU (why headers are read)


def _body(name="2019 XY1", kind="asteroid", seps=(30.0, 32.0), rate=40.0, pa=100.0, v=22.4, certain=False, ratio=1.3, dpa=20.0):
    s = np.array(seps, float)
    return {"name": name, "kind": kind, "seps": s, "rate": rate, "pa": pa, "v": v, "east": 20.0, "north": 20.0,
            "certain": certain, "sep_max": float(s.max()) if np.isfinite(s).all() else math.inf,
            "sep_min": float(np.nanmin(s)), "rate_ratio": ratio, "dpa": dpa}


def test_mover_check_levels_and_texts():
    c = {"id": "x", "det": 2, "rate_arcsec_h": 31.0, "pa_deg": 80.0}
    it = {"times": [1.0, 1.001], "from_dots": True}
    ok = KC.mover_check(c, it, [], 1_580_000, 2215)
    assert ok["status"] is None and ok["checks"][0]["level"] == "ok" and "within 2′" in ok["checks"][0]["text"]
    warn = KC.mover_check(c, it, [_body()], 1, 1)["checks"][0]
    assert warn["level"] == "warn" and "north-east" in warn["text"] and "Not a certain match" in warn["text"]
    assert "D2" in warn["look"] and warn["values"]["name"] == "2019 XY1" and warn["values"]["sep_arcsec"] == 30.0
    sure = KC.mover_check(c, it, [_body(seps=(1.0, 2.0), rate=32.0, pa=82.0, certain=True, ratio=1.03, dpa=2.0)], 1, 1)
    assert sure["status"] == "known" and sure["known_name"] == "2019 XY1" and sure["checks"][0]["level"] == "strong"
    moon = KC.mover_check(c, it, [_body(name="Carme", kind="moon", seps=(3.0, 3.0), certain=True)], 1, 1)
    assert moon["known_name"] == "Carme" and "the moon Carme (JPL Horizons" in moon["checks"][0]["text"]


def test_bodies_take_the_contract_rule_per_body(idx):
    it = {"ra": 10.0, "dec": 0.0, "mjd": 1000.0, "rate": 36.0, "pa": 90.0, "times": [1000.0, 1000.0 + 1 / 24], "obs": [None, None]}
    # body A sits on the line at both times with the same motion; body B is near only at the first time
    a = [{"k": 0, "name": "A", "cls": "moon", "ra": 10.0, "dec": 0.0, "rate": 36.0, "pa": 90.0, "v": 17.0},
         {"k": 1, "name": "A", "cls": "moon", "ra": 10.01, "dec": 0.0, "rate": 36.0, "pa": 90.0, "v": 17.0},
         {"k": 0, "name": "B", "cls": "moon", "ra": 10.0, "dec": 0.005, "rate": 36.0, "pa": 90.0, "v": 17.0}]
    bs = KC.bodies(it, a, np.array([], dtype=object), np.array([], bool))
    assert [b["name"] for b in bs] == ["A", "B"] and bs[0]["certain"] and not bs[1]["certain"]
    assert bs[0]["sep_max"] < 1.0 and math.isinf(bs[1]["sep_max"]) and bs[1]["sep_min"] == pytest.approx(18.0, abs=0.1)


def test_highpm_only_slow_bodies_close_to_the_source():
    c = {"id": "h", "mjd": 61000.0, "det": 4}
    fast = _body(seps=(5.0,), rate=30.0)
    slow_far = _body(seps=(40.0,), rate=0.3)
    slow = _body(name="2003 VB12", seps=(4.0,), rate=0.3)
    assert KC.highpm_check(c, [fast, slow_far]) is None
    e = KC.highpm_check(c, [fast, slow])
    assert e["checks"][0]["level"] == "warn" and e["checks"][0]["values"]["name"] == "2003 VB12" and e["status"] is None


def test_slow_bodies_put_a_distant_body_where_kete_does(tmp_path, monkeypatch):
    """A TNO-like orbit (q 38 AU): the numpy two-body place at a high_pm's time agrees with kete's n-body one."""
    tno = pd.DataFrame({"desig": ["136199"], "name": ["Eris-like"], "peri_dist": [38.2], "ecc": [0.437], "incl": [44.04],
                        "lon_node": [35.95], "peri_arg": [151.64], "peri_time": [2545000.5], "epoch": [2461200.5],
                        "h_mag": [-1.2], "g_phase": [0.15], "group_name": ["TNO"]})
    ix = KC.index(tmp_path, pd.concat([CERES, tno], ignore_index=True), _comets(), "slow", 61000.0, 61002.0)
    names = KC.index_names(ix)
    row = int(np.flatnonzero(names == "Eris-like")[0])
    mjd = 61001.0
    from skyshift_core.known_index import exact
    got = exact(kete, ix, np.array([row]), 0.0, 0.0, 180.0, [mjd])
    assert len(got) == 1
    c = {"id": "h", "ra": got[0]["ra"], "dec": got[0]["dec"], "mjd": mjd}
    hits = KC.slow_bodies(ix, [c], names)["h"]
    assert [h["name"] for h in hits] == ["Eris-like"]
    assert angsep(hits[0]["ra"], hits[0]["dec"], c["ra"], c["dec"]) * 3600 < 3.0
    assert 1.0 < hits[0]["rate"] < 1.5  # near opposition here (SPHEREx looks ~90° from the Sun, where it is slower)
    monkeypatch.setattr(KC, "HIGHPM_SLOW_ARCSEC_H", 1.5)
    e = KC.highpm_check({**c, "det": 4}, KC.bodies({**c, "rate": None, "pa": None, "times": [mjd], "obs": [None]},
                                                     hits, names, ix.comet))
    assert e is not None and e["checks"][0]["values"]["name"] == "Eris-like"
    assert not KC.slow_bodies(ix, [{**c, "dec": c["dec"] + 0.1}], names)["h"]  # 6′ away: nothing


SNIPPET = """<pre>
 Object designation         R.A.      Decl.     V       Offsets     Motion/hr   Orbit  Further observations?
                           h  m  s     &#176;  '  "        R.A.   Decl.  R.A.  Decl.        Comment (Elong/Decl/V at date 1)
 306P/LINEAR             02 59 43.5 +21 45 20  18.1   0.1E   0.1N    86+    62+  cmt   (r =   1.29 AU)
(480640) 2015 NR12       02 59 37.3 +21 41 47  21.5   1.4W   3.4S    23+     3+   15o  None needed at this time.
         2022 WV24       02 58 58.0 +21 38 44  23.4  10.5W   6.5S     0-     0+   28d  TNO
</pre>"""


def test_parse_mpcheck_and_name_keys():
    rows = KC.parse_mpcheck(SNIPPET)
    assert [r["name"] for r in rows] == ["306P/LINEAR", "(480640) 2015 NR12", "2022 WV24"]
    assert rows[1]["east"] == pytest.approx(-84.0) and rows[1]["north"] == pytest.approx(-204.0)
    assert rows[0]["rate_e"] == 86 and rows[2]["rate_e"] == 0 and rows[1]["v"] == 21.5
    assert "(480640)" in KC._keys("(480640) 2015 NR12") and "Franzthaler" in KC._keys("(6212) Franzthaler")
    assert KC._sexa(44.92999, 21.75381) == ("02 59 43.20", "+21 45 13.7")


def _root(tmp: Path) -> Path:
    """A tiny shared-data folder: the two movers' suspects rows, a photo list, orbit snapshots (cometels without
    306P, as on 14 Nov), and cached headers and allcometels so nothing is fetched."""
    root = tmp / "root"
    (root / "results" / "ecl04").mkdir(parents=True)
    pd.DataFrame({"id": [m["id"] for m in MOVERS], "dot_mjd": [[d[1] for d in m["dots"]] for m in MOVERS],
                  "dot_obs": [[d[0] for d in m["dots"]] for m in MOVERS]}).to_parquet(root / "results/ecl04/suspects.parquet")
    (root / "photolists" / "zones").mkdir(parents=True)
    pd.DataFrame({"obs_id": [d[0] for d in DOTS], "url": ["https://example.invalid/x.fits"] * len(DOTS)}).to_parquet(
        root / "photolists/zones/ecl04.parquet")
    orb = root / C.MPC_ORBITS_DIR
    orb.mkdir(parents=True)
    CERES.to_parquet(orb / "mpcorb_2026-11-14.parquet")
    KM.comet_table([]).to_parquet(orb / "cometels_2026-11-14.parquet")  # kete's file: no 306P, no orbit_file column
    cache = root / "cache" / "known_check"
    cache.mkdir(parents=True)
    pd.DataFrame([{"obs_id": d[0], "mjd_avg": d[1], **dict(zip(SC, d[4]))} for d in DOTS]).to_parquet(cache / "observers.parquet")
    KM.comet_table([COMET_306P]).to_parquet(cache / "allcometels_2026-11-16.parquet")
    return root


def test_main_writes_the_producer_file_and_is_idempotent(tmp_path):
    root = _root(tmp_path)
    cf = tmp_path / "candidates.json"
    hp = {"id": "ecl04-pm-1", "kind": "high_pm", "zone": "ecl04", "ra": 50.0, "dec": 20.0, "mjd": 60906.0, "det": 4,
          "score": 0.5, "rate_arcsec_h": None, "pa_deg": None}
    cands = [{k: v for k, v in m.items() if k != "dots"} for m in MOVERS] + [hp]
    cf.write_text(json.dumps({"candidates": cands}))
    out = tmp_path / "known.json"
    assert KC.main(["--candidates", str(cf), "--root", str(root), "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["source"] == "known" and doc["candidates_file"] == str(cf.resolve()) and doc["made"].endswith("Z")
    assert set(doc["candidates"]) == {m["id"] for m in MOVERS}  # the high_pm has nothing worth a check
    for m in MOVERS:
        e = doc["candidates"][m["id"]]
        assert e["status"] == "known" and e["known_name"] == "306P/LINEAR"
        assert set(e["checks"][0]) == {"code", "level", "text", "look", "values"}
    assert doc["stats"]["known"] == 2 and doc["stats"]["movers_from_dots"] == 2 and doc["stats"]["photos_geocentre"] == 0
    first = doc["candidates"]
    assert KC.main(["--candidates", str(cf), "--root", str(root), "--out", str(out)]) == 0  # all from the cache
    assert json.loads(out.read_text())["candidates"] == first


@pytest.mark.skipif(os.environ.get("SKYSHIFT_NETWORK") != "1", reason="network test: set SKYSHIFT_NETWORK=1")
def test_live_allcometels_has_306P_and_cometels_does_not():
    every = KM._fetch_all_comets()
    current = KM.comet_table(kete.mpc.download_json(C.MPC_COMETS_URL, force_download=True))
    assert "306P/LINEAR" in set(every["desig"].str.strip()) and "306P/LINEAR" not in set(current["desig"].str.strip())
    m = KM.merge_comets(current, every)
    assert "306P/LINEAR" in set(m["desig"].str.strip()) and m["desig"].str.strip().is_unique
