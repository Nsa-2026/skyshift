"""scripts/precompute/signal_checks.py (no network: catalogues and known bodies are stubbed). Runs from pipeline/:

    cd pipeline && uv run pytest ../scripts/precompute/tests

The rules are the Viewer's (web/src/product/vetting.ts); these cases mirror web/src/product/vetting.test.ts so both
give the same verdicts and sentences.
"""

import json
import math
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import signal_checks as S  # noqa: E402

SCIENCE = S.SCIENCE


def read(slug: str, name: str) -> dict:
    return json.loads((SCIENCE / slug / f"{name}.json").read_text())


BARNARD, FRAMES = read("barnard", "analysis"), read("barnard", "science")["frames"]
EPS, EPS_FRAMES = read("epsindib", "analysis"), read("epsindib", "science")["frames"]
FAR = {"gaia": {"id": "1", "ra": 0, "dec": 0, "pmra": 0, "pmdec": 0, "parallax": None, "g": 15},
       "catwise": {"name": "J000000.00+000000.0", "ra": 0, "dec": 0, "w1mpro": 15, "w2mpro": 15, "pmra": 0, "pmdec": 0}}
# Eps Indi B as Gaia DR3 lists it (the live /api/gaia answer of 16 Nov).
EPS_INDI_B = {"id": "6412596012146801152", "ra": 331.07645259656397, "dec": -56.79381206688221, "pmra": 3981.977,
              "pmdec": -2466.832, "parallax": 270.658, "g": 18.031}


def answered(**over) -> dict:
    cats = {"gaia": [FAR["gaia"]], "catwise": [FAR["catwise"]], "mpc": S.session_bodies([])}
    cats.update(over)
    return cats


def fast_track() -> dict:
    return S.signal_track("fast", BARNARD["fast_movers"][0], FRAMES, 2)


def along(t: dict, name: str = "306P/LINEAR", east: float = 0, north: float = 0) -> list[dict]:
    """Session predictions that follow the track's sightings (as /api/enrichment?kind=asteroids gives them)."""
    return [{"name": name, "v": 18.5, "visit": 3, "mjd": s["mjd"], "dec": s["dec"] + north / 3600,
             "ra": s["ra"] + east / 3600 / math.cos(math.radians(s["dec"]))} for s in t["sightings"]]


# ---- JavaScript's number words ----------------------------------------------------------------------------------------
def test_numbers_are_written_as_the_browser_writes_them():
    assert S.to_fixed(0.125, 2) == "0.13"       # an exact tie: away from zero, like toFixed
    assert S.to_fixed(1.005, 2) == "1.00"       # 1.00499999…: the binary value decides, like toFixed
    assert S.to_fixed(2.5, 0) == "3" and S.to_fixed(-0.05, 1) == "-0.1"
    assert (S.js_round(2.5), S.js_round(-2.5), S.js_round(0.49)) == (3, -2, 0)
    assert S.date_label(60000) == "2023-02-25"
    assert S.arcsec(4.96) == "5.0″" and S.arcsec(12.5) == "13″"
    assert S.rates(1 / 24, 0.5 / 24) == ("1.0″/day", "0.5″/day")
    assert S.rates(0.5 / 24, 0.4 / 24) == ("182.63″/yr", "146.10″/yr")


# ---- where and when -----------------------------------------------------------------------------------------------------
def test_a_fast_mover_is_seen_at_each_exposure_time_and_place():
    t = fast_track()
    c = BARNARD["fast_movers"][0]
    assert [s["mjd"] for s in t["sightings"]] == [FRAMES[d["t"]]["mjd"] for d in c["detections"]]
    assert 225 < t["motion"]["pa_deg"] < 270                       # x west, y north: west-southwest
    assert t["motion"]["arcsec_h"] * 24 == pytest.approx(c["speed_arcsec_day"], abs=1e-6)
    assert t["sightings"][0]["ra"] > t["sightings"][2]["ra"]


def test_a_slow_mover_is_seen_once_per_visit_at_the_middle_of_its_detectors_exposures():
    t = S.signal_track("slow", EPS["slow_movers"][0], EPS_FRAMES, 2)
    assert len(t["sightings"]) == 4
    v1 = [f["mjd"] for f in EPS_FRAMES if f["det"] == 2 and f["visit"] == 1]
    assert t["sightings"][0]["mjd"] == pytest.approx((min(v1) + max(v1)) / 2, abs=1e-6)
    assert "t" not in t["sightings"][0]                           # a visit's mid-time is no exposure


def test_without_times_a_mover_falls_back_and_a_cold_source_is_not_timed():
    c = BARNARD["fast_movers"][0]
    assert S.signal_track("fast", c, None, 2)["sightings"] == [{"mjd": c["t0"], "ra": c["ra"], "dec": c["dec"]}]
    assert S.signal_track("cold", {"ra": 1, "dec": 1, "x": 1, "y": 1, "detections": [{"x": 1, "y": 1, "t": 0}]}, None, 2) is None


# ---- the contract's rule for a known body ------------------------------------------------------------------------------
def test_a_body_within_10_arcsec_at_every_sighting_moving_our_way_is_certain():
    t = fast_track()
    v = S.vet_signal("fast", t, answered(mpc=S.session_bodies(along(t, east=4, north=-3))), 2)
    assert (v["status"], v["known_name"], v["known_kind"]) == ("known", "306P/LINEAR", "body")
    assert v["checks"][0]["code"] == "known_body" and v["checks"][0]["level"] == "strong"
    assert v["checks"][0]["text"].startswith("MPC orbits put 306P/LINEAR (V 18.5) within 5.0″ of it at all 3 sightings, moving ")
    assert S.category(v) == "body"
    # Contract v3.25's values keys: the nearest and farthest, the body's motion and ours.
    vals = v["checks"][0]["values"]
    assert list(vals) == ["sep_arcsec", "max_sep_arcsec", "sightings", "body_rate_arcsec_h", "body_pa_deg", "rate_arcsec_h", "pa_deg"]
    assert vals["sep_arcsec"] == pytest.approx(5, abs=0.05) and vals["max_sep_arcsec"] == pytest.approx(5, abs=0.05)
    assert vals["rate_arcsec_h"] == pytest.approx(t["motion"]["arcsec_h"], abs=1e-3) and vals["pa_deg"] == S.js_round(t["motion"]["pa_deg"])


def test_a_body_on_its_place_moving_another_way_is_only_not_ruled_out():
    east = 1 / 3600
    t = {"sightings": [{"mjd": 61000 + m / 1440, "ra": 10 + 30 * m / 60 * east, "dec": 0} for m in (0, 2, 4)],
         "motion": {"arcsec_h": 30, "pa_deg": 90}}
    rows = [{"name": "(4) Vesta", "v": 7, "visit": 1, "mjd": 61000 + 2 / 1440, "ra": t["sightings"][1]["ra"], "dec": 0},
            {"name": "(4) Vesta", "v": 7, "visit": 1, "mjd": 61000 + 2 / 1440 + 1, "ra": t["sightings"][1]["ra"], "dec": 30 * 24 / 3600}]
    v = S.vet_signal("fast", t, answered(mpc=S.session_bodies(rows)), 2)
    line = next(c for c in v["checks"] if c["code"] == "known_nearby")
    assert v["status"] == "open" and line["level"] == "warn"
    assert "within 30″ at all 3 sightings, but moving 30.0″/h toward PA 0° (ours 30.0″/h toward PA 90°)" in line["text"]
    assert line["values"] == {"sep_arcsec": 0, "sightings": 3, "body_rate_arcsec_h": 30, "body_pa_deg": 0,
                              "rate_arcsec_h": 30, "pa_deg": 90}
    same =[rows[0], {**rows[1], "ra": t["sightings"][1]["ra"] + 30 * 24 * east, "dec": 0}]
    assert S.vet_signal("fast", t, answered(mpc=S.session_bodies(same)), 2)["status"] == "known"


def test_a_body_near_one_sighting_is_something_to_look_at_and_a_far_one_is_ignored():
    t = fast_track()
    v = S.vet_signal("fast", t, answered(mpc=S.session_bodies(along(t, "(265513)", east=20)[1:2])), 2)
    line = next(c for c in v["checks"] if c["code"] == "known_nearby")
    assert line["level"] == "warn" and S.category(v) == "near"
    assert line["text"].startswith("MPC orbits put (265513) (V 18.5) 20″ from it on 20")
    assert ", within 30″ at 1 of its 3 sightings" in line["text"] and line["look"].startswith("Blink Hours on D2 around ")
    far = S.vet_signal("fast", t, answered(mpc=S.session_bodies(along(t, "(1)", east=45))), 2)
    assert not any(c["code"] == "known_nearby" for c in far["checks"]) and S.category(far) == "clear"


# ---- catalogue stars ----------------------------------------------------------------------------------------------------
def test_eps_indi_b_is_the_slow_mover_a_gaia_star_moving_the_same_way():
    t = S.signal_track("slow", EPS["slow_movers"][0], EPS_FRAMES, 2)
    v = S.vet_signal("slow", t, answered(gaia=[EPS_INDI_B, FAR["gaia"]]), 2)
    assert (v["status"], v["known_kind"], v["known_name"]) == ("known", "star", "Gaia DR3 6412596012146801152")
    assert "of it at all 4 sightings, moving 4.68″/yr toward PA 122° (ours 4.53″/yr toward PA 125°)" in v["checks"][0]["text"]
    assert v["checks"][0]["code"] == "known_star"
    vals = v["checks"][0]["values"]
    assert list(vals) == ["sep_arcsec", "max_sep_arcsec", "star_rate_arcsec_yr", "star_pa_deg", "rate_arcsec_yr", "pa_deg"]
    assert vals["sep_arcsec"] <= vals["max_sep_arcsec"] < 1
    assert (round(vals["star_rate_arcsec_yr"], 2), vals["star_pa_deg"], round(vals["rate_arcsec_yr"], 2), vals["pa_deg"]) == (4.68, 122, 4.53, 125)


def test_a_star_on_it_moving_another_way_is_not_ruled_out():
    t = S.signal_track("slow", EPS["slow_movers"][0], EPS_FRAMES, 2)
    still = {**EPS_INDI_B, "pmra": 0, "pmdec": 0, **S.gaia_at(EPS_INDI_B, 61062.8)}
    v = S.vet_signal("slow", t, answered(gaia=[still]), 2)
    assert v["status"] == "open" and v["checks"][0]["code"] == "catalogue_star"
    assert "moving 0.00″/yr toward PA 0° (ours 4.53″/yr" in v["checks"][0]["text"]
    assert v["checks"][0]["values"]["catalogue"] == "gaia"


def test_stars_are_matched_at_the_signals_time_and_catwise_at_its_catalogue_place_too():
    t = {"sightings": [{"mjd": 61000, "ra": 10, "dec": 0}], "motion": None, "still": True}
    years = (61000 - 57388.5) / 365.25
    mover = {"id": "m", "ra": 10 - 1e-3 * years * 2000 / 3600, "dec": 0, "pmra": 2000, "pmdec": 0, "parallax": None, "g": 12}
    assert S.vet_signal("cold", t, answered(gaia=[mover]), 6)["checks"][0]["text"].startswith("Gaia DR3 m (G 12.0) sits 0.0″ from it")
    assert S.vet_signal("cold", t, answered(gaia=[{**mover, "id": "s", "pmra": 0}]), 6)["found"]["gaia"] is False
    noisy = {"name": "J004000.00+000000.0", "ra": 10, "dec": 0, "w1mpro": 17, "w2mpro": 16.5, "pmra": 1, "pmdec": 0}
    v = S.vet_signal("cold", t, answered(catwise=[noisy]), 6)
    assert "CatWISE2020 J004000.00+000000.0 (W1 17.0, W2 16.5) sits 0.0″" in v["checks"][0]["text"]
    assert "catalogue epoch 2015.4" in v["checks"][0]["text"] and S.category(v) == "catwise"


def test_a_cold_source_is_matched_at_the_mean_of_its_sightings():
    t = {"sightings": [{"mjd": 61000, "ra": 10 - 8 / 3600, "dec": 0}, {"mjd": 61200, "ra": 10 + 8 / 3600, "dec": 0}],
         "motion": None, "still": True}
    v = S.vet_signal("cold", t, answered(gaia=[{**FAR["gaia"], "ra": 10, "dec": 0}]), 6)
    assert "sits 0.0″ from its mean place" in v["checks"][0]["text"]


# ---- honest answers -----------------------------------------------------------------------------------------------------
STILL = {"sightings": [{"mjd": 61000, "ra": 10, "dec": 0}], "motion": None, "still": True}


def test_sign_for_only_from_catalogues_that_answered():
    v = S.vet_signal("cold", STILL, answered(), 6)
    assert v["checks"] == [{"code": "catalogues_checked", "level": "ok", "look": "", "values": None,
                            "text": "Gaia DR3, CatWISE2020 and MPC orbits checked: nothing in Gaia or CatWISE within 6″, "
                                    "and no known asteroid (V ≤ 21) or comet predicted within 30″"}]
    failed = S.vet_signal("cold", STILL, answered(catwise=None), 6)
    assert next(c for c in failed["checks"] if c["level"] == "warn")["text"] == "Not checked against CatWISE2020: the service did not answer"
    assert S.category(failed) == "partial"
    empty = S.vet_signal("cold", STILL, answered(catwise=[]), 6)
    assert next(c for c in empty["checks"] if c["code"] == "catalogue_empty")["level"] == "warn"
    assert "CatWISE" not in next(c for c in empty["checks"] if c["level"] == "ok")["text"]
    outside = S.vet_signal("cold", STILL, answered(mpc=S.session_bodies([], [61000.004])), 6)
    assert next(c for c in outside["checks"] if c["code"] == "catalogue_unchecked")["text"] == \
        "MPC orbits were not checked at its sighting: the known-object index doesn't reach that time"


# ---- this step's known bodies: predicted at each sighting, from SPHEREx -------------------------------------------------
def at_sightings(t: dict, name: str, east: float = 0.0, source: str = "MPC orbits", v: float | None = 17.9) -> list[list[dict]]:
    m = t["motion"]
    return [[{"name": name, "ra": s["ra"] + east / 3600 / math.cos(math.radians(s["dec"])), "dec": s["dec"],
              "rate": m["arcsec_h"] * 1.05, "pa": m["pa_deg"] + 3, "v": v, "source": source}] for s in t["sightings"]]


def test_a_comet_predicted_on_every_sighting_is_named_with_every_body_covered():
    t = fast_track()
    v = S.vet_signal("fast", t, answered(mpc=S.sighting_bodies(at_sightings(t, "306P/LINEAR", 2.0), [False] * 3)), 2,
                     S.ALL_BODIES)
    assert (v["status"], v["known_name"]) == ("known", "306P/LINEAR")
    assert v["checks"][0]["text"].startswith("MPC orbits put 306P/LINEAR (V 17.9) within 2.0″ of it at all 3 sightings, moving ")
    moon = S.vet_signal("fast", t, answered(mpc=S.sighting_bodies(at_sightings(t, "Iapetus", 25.0, "JPL Horizons", None), [False] * 3)),
                        2, S.ALL_BODIES)
    assert moon["status"] == "open"
    assert next(c for c in moon["checks"] if c["code"] == "known_nearby")["text"].startswith("JPL Horizons puts Iapetus 25″ from it on ")
    clear = S.vet_signal("fast", t, answered(mpc=S.sighting_bodies([[], [], []], [False] * 3)), 2, S.ALL_BODIES)
    assert clear["checks"][-1]["text"].endswith(
        "no known asteroid (at any brightness), comet, planet or moon predicted within 30″ at its times")
    outside = S.vet_signal("fast", t, answered(mpc=S.sighting_bodies([[], [], []], [False, True, True])), 2, S.ALL_BODIES)
    assert next(c for c in outside["checks"] if c["code"] == "catalogue_unchecked")["text"].startswith(
        "MPC orbits were not checked at 2 of its 3 sightings")


# ---- a run over the published files -----------------------------------------------------------------------------------
class FakeKnown:
    """known_check stand-in: 306P on Barnard's first fast mover, nothing elsewhere."""

    def __init__(self):
        self.loaded = None

    def exposures(self, slug, center, frames):
        return [{"obs_id": f"{slug}-{i}", "url": f"https://example.invalid/{i}"} for i in range(len(frames))]

    def positions(self, urls):
        return {o: None for o in urls}

    def load(self, lo, hi):
        self.loaded = (lo, hi)

    def covers(self, mjd):
        return True

    def predict(self, sightings, obs_sc):
        t = fast_track()
        out = []
        for s in sightings:
            if s["id"].startswith("barnard/analysis.json/fast_movers/0/"):
                k = int(s["id"].rsplit("/", 1)[1])
                out.append(at_sightings(t, "306P/LINEAR", 2.0)[k])
            else:
                out.append([])
        return out

    def about(self):
        return "MPC orbits of 3 asteroids at any brightness and 1 comet (test)"


def published(tmp_path: Path, slugs=("barnard", "epsindib")) -> Path:
    for slug in slugs:
        (tmp_path / slug).mkdir()
        for name in ("analysis", "science", "frames"):
            shutil.copy(SCIENCE / slug / f"{name}.json", tmp_path / slug / f"{name}.json")
    return tmp_path


def catalogues(slug, center, coverage):
    return {"gaia": [EPS_INDI_B, FAR["gaia"]] if slug == "epsindib" else [FAR["gaia"]], "catwise": [FAR["catwise"]]}


def test_the_run_writes_every_signals_checks_into_the_published_files(tmp_path):
    root = published(tmp_path)
    report = S.run(["barnard", "epsindib"], root, tmp_path / "cache", FakeKnown(), catalogues=catalogues)
    for slug in ("barnard", "epsindib"):
        a = json.loads((root / slug / "analysis.json").read_text())
        before = read(slug, "analysis")
        assert a["catalogue_checks"]["sources"]["mpc"] == "MPC orbits of 3 asteroids at any brightness and 1 comet (test)"
        assert not any(S.STALE_WARNING in w for w in a["warnings"])
        for key, _ in S.KINDS:
            assert len(a[key]) == len(before[key])
            for c, old in zip(a[key], before[key]):
                assert c["status"] == old["status"]                    # the analysis's own status stays
                assert c["check_status"] in ("open", "known") and isinstance(c["checks"], list) and c["checks"]
                assert (c["known_name"] is not None) == (c["check_status"] == "known")
                ranks = [S.LEVEL_RANK[x["level"]] for x in c["checks"]]
                assert ranks == sorted(ranks)
    b = json.loads((root / "barnard" / "analysis.json").read_text())
    assert (b["fast_movers"][0]["check_status"], b["fast_movers"][0]["known_name"]) == ("known", "306P/LINEAR")
    e = json.loads((root / "epsindib" / "analysis.json").read_text())
    assert (e["slow_movers"][0]["check_status"], e["slow_movers"][0]["known_name"]) == ("known", "Gaia DR3 6412596012146801152")
    assert report["fields"]["epsindib/analysis.json"]["known"] == ["Gaia DR3 6412596012146801152"]
    # Rerunnable: the same answers leave the files as they are (the time they were made too).
    first = {slug: (root / slug / "analysis.json").read_bytes() for slug in ("barnard", "epsindib")}
    again = S.run(["barnard", "epsindib"], root, tmp_path / "cache", FakeKnown(), catalogues=catalogues)
    assert all(f["unchanged"] for f in again["fields"].values())
    assert first == {slug: (root / slug / "analysis.json").read_bytes() for slug in ("barnard", "epsindib")}


def test_nothing_is_written_when_a_catalogue_does_not_answer(tmp_path):
    root = published(tmp_path, ("barnard",))
    before = (root / "barnard" / "analysis.json").read_bytes()

    def down(slug, center, coverage):
        raise RuntimeError("IRSA did not answer")
    with pytest.raises(RuntimeError):
        S.run(["barnard"], root, tmp_path / "cache", FakeKnown(), catalogues=down)
    assert (root / "barnard" / "analysis.json").read_bytes() == before


def test_a_dry_run_writes_nothing(tmp_path):
    root = published(tmp_path, ("barnard",))
    before = (root / "barnard" / "analysis.json").read_bytes()
    report = S.run(["barnard"], root, tmp_path / "cache", FakeKnown(), dry_run=True, catalogues=catalogues)
    assert report["fields"]["barnard/analysis.json"]["known"] == ["306P/LINEAR"]
    assert (root / "barnard" / "analysis.json").read_bytes() == before
