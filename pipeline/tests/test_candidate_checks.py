"""candidates.json checks, status and known_name (contracts/api.md v3.23): every rule and text of candidate_checks, the
exporter's use of them (score factors, the two_detectors rate fix) and the side-file merge (--checks-dir, --merge-only)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import candidate_checks as CK
from skyshift_pipeline import candidates as K

T0 = 61000.0
STEP = 2.1 / 1440.0  # one exposure step (2.1 min)


def _mover(**kw) -> dict:
    """A clean B mover: 3 exposures in D2, moving 36″/h along the visit's direction, nothing flagged."""
    n = kw.pop("n", 3)
    det = kw.pop("det_list", [2] * n)
    dots = {"det": det, "x": [500.0 + 0 * i for i in range(n)], "y": [800.0 - 115.0 * i for i in range(n)],
            "mjd": [T0 + i * STEP for i in range(n)], "flux": [900.0] * n, "snr": [20.0] * n, "size": [3.8] * n,
            "fpk": [0] * n, "obs": [f"2025W40_1A_0001_{i + 1}" for i in range(n)], "flags3": [0] * n}
    dots.update(kw.pop("dots", {}))
    m = {"dots": dots, "flags": 0, "evidence": "two_exposures", "n_exposures": n, "det": det[0], "grade": "B",
         "gates": [], "rate": 36.0, "rate_err": 6.0, "pa": 100.0, "no_motion": False, "track": None, "on_track": False,
         "visit": 1, "nearest_known": None, "near_source": None, "repeat": None, "excess": None, "ref": _ref(),
         "control_tracks": 0, "partner_photos": {(o, p) for o in dots["obs"] for p in range(1, 7)}, "bright": None,
         "star_size": [3.8] * n, "warn": {}}
    m.update(kw)
    return m


def _ref(n=500, pa=100.0, spread=10.0, rate=30.0):
    rng = np.random.default_rng(1)
    v = np.ones(n, np.int64)
    rates = rng.uniform(5.0, rate * 2, n)
    pas = pa + rng.uniform(-spread, spread, n)
    return CK.zone_motion_ref(v, np.ones(n, bool), rates, pas)


def codes(checks):
    return {c["code"]: c["level"] for c in checks}


def test_check_shape_and_sorting():
    c = CK.check("fast", "warn", "t", "l", {"rate_arcsec_h": np.float32(36.04), "x": np.nan, "n": np.int64(3)})
    assert c == {"code": "fast", "level": "warn", "text": "t", "look": "l", "values": {"rate_arcsec_h": 36.04, "n": 3}}
    assert CK.check("a", "ok", "t")["values"] is None and CK.check("a", "ok", "t", "", {"x": np.nan})["values"] is None
    with pytest.raises(ValueError):
        CK.check("a", "bad", "t")
    s = CK.sort_checks([CK.check("a", "ok", ""), CK.check("b", "warn", ""), CK.check("c", "strong", ""),
                        CK.check("b", "strong", "dup")])
    assert [(c["code"], c["level"]) for c in s] == [("c", "strong"), ("b", "warn"), ("a", "ok")]


def test_clean_mover_has_no_doubts():
    ch = CK.mover_checks(_mover())
    assert codes(ch) == {}
    assert CK.factor({"kind": "mover", "grade": "B", "checks": ch}) == 1.0


def test_exposures_of_groups_within_5s():
    t = [T0, T0 + 0.002 / 86400, T0 + STEP, T0 + STEP + 4.0 / 86400, T0 + 2 * STEP]
    assert CK.exposures_of(t).tolist() == [0, 0, 1, 1, 2]


# --- the two_detectors rate bug (movers.md #14) -----------------------------------------------------------------
def test_motion_one_exposure_has_no_rate():
    """D1 and D4 of one exposure differ by milliseconds: until 16 Nov motion() fitted them as two times
    (0.4-10 million ″/h on the site). Now one exposure gives no rate; two exposures with a pair each fit normally."""
    d = pd.DataFrame({"ra": [125.0, 125.0 + 1.5 / 3600], "dec": [20.0, 20.0], "mjd": [T0, T0 + 0.003 / 86400],
                      "pos_err_arcsec": [0.3, 0.3]})
    assert K.motion(d) is None
    two = pd.DataFrame({"ra": [125.0, 125.0 + 0.5 / 3600, 125.0 + 1.0 / 3600, 125.0 + 1.5 / 3600],
                        "dec": [20.0] * 4, "mjd": [T0, T0 + 0.004 / 86400, T0 + STEP, T0 + STEP + 0.002 / 86400],
                        "pos_err_arcsec": [0.3] * 4})
    rate, _err, pa = K.motion(two)
    assert rate == pytest.approx(1.0 * np.cos(np.radians(20.0)) * 3600 / (2.1 * 60) * 1, rel=0.05)  # 1″ RA in 2.1 min
    assert rate < 100 and pa == pytest.approx(90.0, abs=1.0)


# --- flags ------------------------------------------------------------------------------------------------------
def test_outlier_is_a_sign_for_it():
    m = _mover(dots={"fpk": [1 << 19, 1 << 19, 0]})
    (c,) = CK.mover_checks(m)
    assert c["code"] == "flag_outlier" and c["level"] == "ok"
    assert c["text"] == ("NASA's change check (OUTLIER) marks the peak of 2 of its 3 dots, as it does for most moving "
                         "objects: 68% of known asteroids have it on a dot (the list: 28%).")
    assert c["values"] == {"dots_peak": 2, "dots": 3, "known_share": 0.68}
    assert CK.factor({"kind": "mover", "checks": [c]}) == 1.0


def test_halo_warn_and_every_peak_strong():
    m = _mover(flags=1 << 28, dots={"flags3": [1 << 28, 1 << 28, 0], "fpk": [1 << 28, 0, 0]})
    (c,) = CK.mover_checks(m)
    assert c["code"] == "flag_halo" and c["level"] == "warn"
    assert c["text"] == ("HALO (the glow around a bright star) is set around 2 of its 3 dots, at the peak of 1; known "
                         "asteroids carry it 22% of the time, but almost never at the peak.")
    assert c["values"] == {"dots_flagged": 2, "dots_peak": 1, "dots": 3, "known_share": 0.22}
    assert "bright star nearby" in c["look"] and c["look"].startswith("Blink D2")
    every = CK.mover_checks(_mover(flags=1 << 22, dots={"flags3": [1 << 22] * 3, "fpk": [1 << 22] * 3}))
    assert codes(every) == {"flag_ghost": "strong"}
    assert every[0]["text"].startswith("GHOST (an optical reflection of a bright star) is set at the peak of every one "
                                       "of its 3 dots;")
    # no 3x3 per dot (dot files not read): the OR says "at least one"
    m = _mover(flags=1 << 26, dots={"flags3": None})
    assert CK.mover_checks(m)[0]["text"].startswith("BLOOM (charge spilling out of a saturated star) is set around at "
                                                    "least one of its dots;")


# --- detectors ----------------------------------------------------------------------------------------------------
def test_one_detector_d4_is_strong():
    m = _mover(det_list=[4, 4, 4])
    (c,) = CK.mover_checks(m)
    assert (c["code"], c["level"]) == ("one_detector", "strong")
    assert c["text"] == ("Seen only in D4 (3 exposures); D1 photographed the same spot at the same moments (3 exposures) "
                         "and has no dot there. Known asteroids are seen in D4 alone 1 in 312 times (0.3%), camera "
                         "glitches often are (35% of the list): an asteroid bright at 2.4–3.8 µm is brighter still at "
                         "0.75–1.1 µm.")
    assert c["look"].startswith("Switch the Viewer between D4 and D1 at the same exposure")
    assert c["values"]["partner_photos"] == 3 and c["values"]["detector"] == 4 and c["values"]["partner"] == 1
    # the partner photo is missing: can't be told, only a warn
    m = _mover(det_list=[4, 4, 4], partner_photos=set())
    (c,) = CK.mover_checks(m)
    assert c["level"] == "warn" and "has no photo of the spot here" in c["text"]


def test_one_detector_mwir_warn_swir_none():
    (c,) = CK.mover_checks(_mover(det_list=[6, 6, 6]))
    assert (c["code"], c["level"]) == ("one_detector", "warn")
    assert "Real asteroids do this too when they glow with heat (7% of known asteroids)" in c["text"]
    assert codes(CK.mover_checks(_mover(det_list=[3, 3, 3]))) == {"one_detector": "warn"}
    assert codes(CK.mover_checks(_mover(det_list=[2, 2, 2]))) == {}       # D2 alone: the commonest real pattern
    assert codes(CK.mover_checks(_mover(det_list=[1, 1, 1]))) == {}
    # dots near the frame edge: the partner (offset ~12 px) may not cover them
    m = _mover(det_list=[5, 5, 5], dots={"x": [8.0, 8.0, 8.0]})
    assert CK.mover_checks(m)[0]["values"]["partner_photos"] == 0


def test_partner_seen_is_ok():
    m = _mover(n=5, det_list=[2, 5, 2, 5, 2], dots={"mjd": [T0, T0 + 0.001 / 86400, T0 + STEP, T0 + STEP, T0 + 2 * STEP],
                                                  "obs": ["o1", "o1", "o2", "o2", "o3"]})
    (c,) = CK.mover_checks(m)
    assert (c["code"], c["level"]) == ("partner_seen", "ok")
    assert c["text"].startswith("D2 and D5 both saw it at the same moment in 2 of its 3 exposures: real light")
    assert c["values"] == {"exposures_both": 2, "exposures": 3}


# --- motion -------------------------------------------------------------------------------------------------------
def test_two_photos_and_may_not_move():
    m = _mover(n=2, rate=20.0, rate_err=10.0, no_motion=True)
    ch = CK.mover_checks(m)
    assert codes(ch) == {"two_photos": "warn", "may_not_move": "warn"}
    tp = next(c for c in ch if c["code"] == "two_photos")
    assert tp["text"].startswith("Seen in only 2 exposures, 2.1 minutes apart: two unrelated specks can line up by chance")
    mm = next(c for c in ch if c["code"] == "may_not_move")
    assert mm["text"].startswith("Its dots moved about 20 ± 10″ an hour (2.0σ), within their position errors")
    assert mm["values"]["sigma"] == 2.0
    one = _mover(n=2, det_list=[2, 5], evidence="two_detectors", rate=None, rate_err=None, pa=None,
                 dots={"mjd": [T0, T0 + 0.001 / 86400]})
    ch = CK.mover_checks(one)
    assert codes(ch) == {"two_photos": "warn", "partner_seen": "ok"}
    assert next(c for c in ch if c["code"] == "two_photos")["text"].startswith("Seen in one exposure only")


def test_fast_and_odd_direction():
    ref = _ref()
    q = ref[1]["rate_q"]
    fast = _mover(rate=q + 40, rate_err=5.0, ref=ref)
    ch = CK.mover_checks(fast)
    assert codes(ch) == {"fast": "warn"}
    assert ch[0]["text"].startswith(f"Moves {q + 40:.0f} ± 5″ an hour; 99% of the 500 known asteroids followed in this "
                                    f"visit move slower than {q:.0f}″ an hour")
    # fast and 120° off the visit's direction: no known asteroid moves like it -> strong
    odd = _mover(rate=q + 40, rate_err=5.0, pa=220.0, ref=ref)
    ch = CK.mover_checks(odd)
    assert codes(ch) == {"odd_direction": "strong", "fast": "warn"}
    od = ch[0]
    assert od["text"].startswith("Moves towards PA 220°, 120° away from the common direction of this visit's asteroids "
                                 "(PA 100°, the median of 500 known ones); none of them move this fast and this far off.")
    assert od["values"]["known_like_it"] == 0
    # slow and off: warn; on a track (a chance pair can't make a track): warn
    assert codes(CK.mover_checks(_mover(pa=220.0, ref=ref))) == {"odd_direction": "warn"}
    on = _mover(rate=q + 40, rate_err=5.0, pa=220.0, ref=ref, track=(3, q + 40, 20.0), on_track=True)
    assert codes(CK.mover_checks(on)) == {"odd_direction": "warn", "fast": "warn", "chance_tracks": "ok"}
    # motion not significant: no direction claim
    assert codes(CK.mover_checks(_mover(pa=220.0, ref=ref, no_motion=True))) == {"may_not_move": "warn"}
    # too few known asteroids in the zone: nothing to compare with
    assert codes(CK.mover_checks(_mover(rate=500.0, pa=220.0, ref=_ref(n=10)))) == {}


def test_chance_tracks():
    m = _mover(track=(4, 25.0, 12.0), on_track=True, control_tracks=0)
    (c,) = CK.mover_checks(m)
    assert (c["code"], c["level"]) == ("chance_tracks", "ok")
    assert c["text"].startswith("Followed over 4 sessions of its visit at 25.0″ an hour, 12σ; the zone's shuffled control")
    m = _mover(track=(3, 25.0, 12.0), on_track=True, control_tracks=1)
    assert codes(CK.mover_checks(m)) == {"chance_tracks": "warn"}
    assert codes(CK.mover_checks(_mover(track=(3, 25.0, 12.0), on_track=True, control_tracks=None))) == {}


# --- brightness and place ---------------------------------------------------------------------------------------
def test_flux_jump():
    m = _mover(dots={"flux": [900.0, 300.0, 600.0]})
    (c,) = CK.mover_checks(m)
    assert (c["code"], c["level"]) == ("flux_jump", "warn")
    assert c["text"] == "Its D2 brightness changes 3.0× between exposures; known asteroids change less than 2.2× (98% of them)."
    m = _mover(dots={"flux": [900.0, 150.0, 600.0]}, gates=["flux_ratio"], grade="C")
    (c,) = CK.mover_checks(m)
    assert c["level"] == "strong" and c["text"].endswith("a cosmic-ray hit paired with a fainter speck looks like this.")
    assert CK.counted_elsewhere({"kind": "mover", "grade": "C"}, c)            # grade C's x0.1 stands for it
    assert codes(CK.mover_checks(_mover(dots={"flux": [900.0, 500.0, 600.0]}))) == {}


def test_repeat_excess_known_catalogue_bright():
    m = _mover(repeat=(7.0, 3.4), excess=(4, 2, 1.6), nearest_known=45.0, near_source=7.5, bright=(8.9, 48.0))
    ch = CK.mover_checks(m)
    assert codes(ch) == {"repeat_position": "strong", "known_nearby": "warn", "detector_excess": "warn",
                         "catalogue_star": "warn", "bright_star": "warn"}
    t = {c["code"]: c["text"] for c in ch}
    assert t["repeat_position"] == ("Another unexplained mover was seen 7″ from here 3.4 days apart (an implied 0.09″ an "
                                    "hour): probably a source that stays put (a star or galaxy), not a fast mover.")
    assert t["detector_excess"].startswith("In visit 2 D4 has 1.6× this zone's usual share of unexplained movers")
    assert t["known_nearby"].startswith("A known asteroid, comet, planet or moon was predicted 45″ away at that time")
    assert t["catalogue_star"] == "A catalogued or fixed source lies 7.5″ away: the dot may be its wing or a blend with it."
    assert t["bright_star"].startswith("A G 8.9 star lies 48″ away")
    cand = {"kind": "mover", "grade": "B", "checks": ch}
    assert CK.factor(cand) == pytest.approx(0.3 * 0.95 ** 3)                  # known_nearby: the known term counts it


def test_hot_pixel_proven_makes_a_flaw():
    m = _mover(dots={"x": [1021.2, 1021.5, 1020.9], "y": [640.1, 640.6, 639.8]})
    ch = CK.mover_checks(m)
    assert codes(ch) == {"hot_pixel_proven": "strong"}
    assert ch[0]["text"].startswith("All 3 of its dots sit on the same D2 pixel (within 1 px) in 3 exposures")
    assert CK.status_of(ch) == "flaw" and CK.status_of([]) == "open"


def test_gate_checks():
    m = _mover(grade="C", gates=["hot_pixel", "bad_column", "masked_peak", "very_sharp", "red_photo", "2mass", "edge"],
               dots={"x": [3.0, 500.0, 500.0], "fpk": [1 << 17, 0, 0], "size": [1.2, 3.8, 3.8], "snr": [20.0] * 3},
               star_size=[3.7, 3.7, 3.7])
    ch = CK.mover_checks(m)
    assert codes(ch) == {"hot_pixel": "strong", "bad_column": "strong", "edge": "strong", "masked_peak": "strong",
                         "sharp": "strong", "red_photo": "strong", "catalogue_star": "strong"}
    t = {c["code"]: c["text"] for c in ch}
    assert t["edge"] == "Its only dot sits 3.0 px from the D2 detector's edge, where stars cut off by the frame make fake dots." \
        or t["edge"].startswith("1 of its 3 dots sits 3.0 px from the D2 detector's edge")
    assert "PERSIST (an afterimage of a bright star)" in t["masked_peak"]
    assert t["sharp"].startswith("Its sharpest dot (D2, S/N 20) is 1.2″ across, 0.32× a star's size at that S/N")
    assert t["catalogue_star"].startswith("A 2MASS source lies within 6″ of it")
    cand = {"kind": "mover", "grade": "C", "checks": ch}
    assert CK.factor(cand) == 1.0                                            # all counted by grade C
    assert CK.factor({**cand, "grade": "B"}) == pytest.approx(0.3 ** 7)


def test_suspects_warnings():
    m = _mover(flags=1 << 28, dots={"flags3": [1 << 28, 0, 0]},
               warn={"flag_halo": {"star_sep_arcsec": 22.0}, "bright_star": {"sep_arcsec": 25.0},
                     "new_code": {"ratio": 1.5}})
    ch = CK.mover_checks(m)
    c = {x["code"]: x for x in ch}
    assert c["flag_halo"]["values"]["star_sep_arcsec"] == 22.0 and c["flag_halo"]["text"].startswith("HALO")
    assert c["bright_star"]["text"] == "A bright source lies 25″ away (formerly a veto): its glow and ghosts can make dots."
    assert c["new_code"]["text"] == "Kept with a warning from the suspects step (new code: ratio 1.5)."
    assert all(x["level"] == "warn" for x in ch)


def test_suspect_warning_columns():
    row = pd.DataFrame({"warn_codes": [["catalogue_star", "flag_halo"], []],
                        "warn_values": ['{"catalogue_star": {"sep_arcsec": 4.2}}', ""]})
    r0, r1 = row.itertuples()
    assert K.suspect_warnings(r0) == {"catalogue_star": {"sep_arcsec": 4.2}, "flag_halo": {}}
    assert K.suspect_warnings(r1) == {}
    assert K.suspect_warnings(next(pd.DataFrame({"x": [1]}).itertuples())) == {}   # columns absent


# --- high_pm, spectral, shape -------------------------------------------------------------------------------------
def test_highpm_checks():
    h = {"reasons": ["pm_pair", "implausible_pm"], "pm": 80000.0, "method": "pair", "det": 3}
    (c,) = CK.highpm_checks(h)
    assert (c["code"], c["level"]) == ("impossible_motion", "strong")
    assert c["text"].startswith("Moves 80.0″ a year; no star moves faster than 10.4″ a year (Barnard's Star)")
    assert CK.factor({"kind": "high_pm", "reasons": h["reasons"], "checks": [c]}) == 1.0  # implausible_pm x0.01 counts it
    f = {"reasons": ["pm_fit", "vetted"], "pm": 900.0, "method": "fit", "fit_mode": "pm_leak", "saturated": True,
         "blend": (5.2, 0.4, 2), "det": 2}
    ch = CK.highpm_checks(f)
    assert codes(ch) == {"blend": "strong", "parallax_leak": "warn", "saturated": "warn"}
    b = ch[0]
    assert b["text"] == ("Two Gaia stars lie within 8″ of it (5.2″ apart, 0.4 mag different): the 7.5″ image may merge "
                         "them, so a shift between visits may be the pair, not motion.")
    assert codes(CK.highpm_checks({**f, "fit_mode": "pm+gaia_plx", "saturated": False, "blend": None})) == {}


def test_spectral_checks():
    s = {"labels": ["red", "pah_33"], "pah_sig": 7.2, "n_channels": 40, "bright": ("G", 8.4, 75.0),
         "blend": (4.0, 1.0, 2), "catalogs": ["Gaia DR3"], "det": 4}
    ch = CK.spectral_checks(s)
    assert codes(ch) == {"pah_only": "warn", "few_channels": "warn", "bright_neighbour": "warn", "blend": "warn",
                         "catalogues_checked": "warn"}
    t = {c["code"]: c["text"] for c in ch}
    assert t["pah_only"].startswith("Only PAH emission at 3.3 µm (7σ), the glow of dust in star-forming galaxies")
    assert t["few_channels"] == "Its spectrum has values in only 40 of the 102 channels, so a band may rest on few points."
    assert t["bright_neighbour"] == "A G 8.4 star lies 1.2′ away: its glow can add light to some channels."
    assert t["catalogues_checked"] == ("Only Gaia DR3 was searched here: this zone's reference catalogue lacks "
                                       "CatWISE2020 and 2MASS, so an infrared catalogue may still list it.")
    ice = CK.spectral_checks({"labels": ["h2o_ice", "co2_ice"], "ice_neighbours": 41, "n_channels": 90,
                              "catalogs": ["Gaia DR3", "CatWISE2020", "2MASS"], "catwise_match": True})
    assert codes(ice) == {"behind_cloud": "warn", "catalogues_checked": "ok"}
    assert ice[0]["text"] == ("Water ice and CO₂ ice absorption: starlight passing through a cold dust cloud, not the star "
                              "itself; 41 other ice detections lie within 1°, a cloud in front of many stars.")
    assert ice[1]["text"] == "Gaia DR3, CatWISE2020 and 2MASS were searched; CatWISE2020 lists it."


def test_shape_checks():
    ch = CK.shape_checks({"companion_catalog": "2MASS K 13.4", "galaxy": True, "blend": None, "bright": None, "det": 3})
    assert codes(ch) == {"catalogue_star": "strong", "galaxy": "warn"}
    assert ch[0]["text"].startswith("The second object the two-source fit found is catalogued (2MASS K 13.4)")
    assert codes(CK.shape_checks({"blend": (6.0, 2.0, 2), "bright": ("W1", 6.5, 30.0)})) == {
        "blend": "strong", "bright_neighbour": "warn"}


# --- side files ---------------------------------------------------------------------------------------------------
def _cand(cid, kind="mover", score=0.5, checks=None, grade="B", zone="hunt", **kw):
    c = {k: None for k in K.FIELDS}
    c.update({"id": cid, "kind": kind, "ra": 125.0, "dec": 20.0, "mjd": T0, "clock": "hours", "det": 3, "zone": zone,
              "zones": [zone], "score": score, "grade": grade if kind == "mover" else None, "gaia_match": False,
              "catwise_match": False, "galactic_b": 30.0, "labels": [], "reasons": ["two_exposures"], "why": "w",
              "flags": [], "status": "open", "known_name": None, "checks": checks or []})
    c.update(kw)
    return c


def _side(tmp_path, name, cands):
    (tmp_path / f"{name}.json").write_text(json.dumps({"source": name, "made": "2026-11-16T18:00:00Z",
                                                       "candidates_file": "x.json", "candidates": cands}))


def test_merge_rules(tmp_path):
    own = CK.check("one_detector", "strong", "exporter's", "", {"detector": 4})
    cs = [_cand("a", checks=[own], score=0.3), _cand("b"), _cand("c"), _cand("d"), _cand("e", score=0.6)]
    pixel = {"checks": [{"code": "one_detector", "level": "warn", "text": "pixel's: D1 measured 76 ± 41 µJy",
                         "look": "look at D1", "values": {"flux_ujy": 76.0}},
                        {"code": "partner_seen", "level": "ok", "text": "seen", "look": "", "values": None}],
             "status": None, "known_name": None}
    _side(tmp_path, "pixel", {"a": pixel,
                              "c": {"checks": [{"code": "hot_pixel_proven", "level": "strong", "text": "same pixel",
                                                "look": "", "values": None}], "status": None},
                              "d": {"checks": [], "status": "flaw"},
                              "zz": {"checks": [], "status": "flaw"}})
    _side(tmp_path, "known", {"b": {"checks": [{"code": "known_nearby", "level": "strong", "text": "306P", "look": "",
                                                "values": None}], "status": "known", "known_name": "306P/LINEAR"},
                              "d": {"checks": [], "status": "known", "known_name": "(161055) 2002 JV46"}})
    st = CK.merge_side_files(cs, CK.load_side_files(tmp_path))
    assert st == {"known": {"merged": 2, "not_found": 0}, "pixel": {"merged": 3, "not_found": 1}}
    a, b, c, d, e = cs
    assert [x["code"] for x in a["checks"]] == ["one_detector", "partner_seen"]
    assert a["checks"][0]["text"].startswith("pixel's") and a["checks"][0]["level"] == "warn"
    assert a["score"] == pytest.approx(0.3 * 0.95 / 0.3)                     # strong -> warn: x 0.95 / 0.3
    assert (b["status"], b["known_name"]) == ("known", "306P/LINEAR") and b["score"] == pytest.approx(0.5)
    assert c["status"] == "flaw" and c["score"] == pytest.approx(0.5 * 0.3)  # a strong `_proven` check
    assert (d["status"], d["known_name"]) == ("known", "(161055) 2002 JV46")  # known > flaw ...
    conflict = [x for x in d["checks"] if x["code"] == "status_conflict"]
    assert len(conflict) == 1 and conflict[0]["level"] == "warn"            # ... and the conflict is shown
    assert conflict[0]["text"] == "known names it (161055) 2002 JV46, but pixel says it is an instrument flaw: look at both."
    assert e["status"] == "open" and e["checks"] == [] and e["score"] == 0.6  # untouched
    again = CK.merge_side_files(cs, CK.load_side_files(tmp_path))              # idempotent on the checks
    assert [x["code"] for x in a["checks"]] == ["one_detector", "partner_seen"] and again["pixel"]["merged"] == 3


def test_merge_producers_disagree_and_bad_files(tmp_path):
    cs = [_cand("a")]
    _side(tmp_path, "aa", {"a": {"checks": [{"code": "x", "level": "warn", "text": "aa"}]}})
    _side(tmp_path, "bb", {"a": {"checks": [{"code": "x", "level": "strong", "text": "bb"},
                                            {"code": "y", "level": "nonsense", "text": "dropped"}]}})
    (tmp_path / "broken.json").write_text("{not json")
    st = CK.merge_side_files(cs, CK.load_side_files(tmp_path))
    assert "error" in st["broken"]
    assert [(x["code"], x["text"]) for x in cs[0]["checks"]] == [("x", "bb")]   # the stronger level wins


def test_build_has_checks_and_merges(tmp_path, monkeypatch):
    """A whole export: every candidate has status, known_name, checks; --checks-dir merges at export time."""
    from test_candidates import _zone_files

    from skyshift_pipeline.store import Store

    store = Store(str(tmp_path / "bucket"))
    _zone_files(store, "hunt", 125.0, 20.0, with_extra_mover=True)
    doc = K.build(store, ["hunt"])
    for c in doc["candidates"]:
        assert c["status"] == "open" and c["known_name"] is None and isinstance(c["checks"], list)
        assert list(c)[-3:] == ["status", "known_name", "checks"]
        for x in c["checks"]:
            assert set(x) == {"code", "level", "text", "look", "values"} and x["level"] in CK.LEVELS
    m = doc["candidates"][0]
    assert m["id"] == "hunt-1-2" and {x["code"] for x in m["checks"]} >= {"two_photos"}
    sides = tmp_path / "checks"
    sides.mkdir()
    _side(sides, "known", {"hunt-1-2": {"checks": [], "status": "known", "known_name": "(1) Ceres"}})
    doc2 = K.build(store, ["hunt"], checks_dir=sides)
    m2 = next(c for c in doc2["candidates"] if c["id"] == "hunt-1-2")
    assert (m2["status"], m2["known_name"]) == ("known", "(1) Ceres")
    assert json.loads(K.dumps(doc2)) == doc2


def test_merge_only_cli(tmp_path):
    """--merge-only on an existing file (also one written before v3.23): fields added, side files merged, re-sorted."""
    old = [_cand("hunt-1", score=0.8), _cand("hunt-2", score=0.4)]
    for c in old:
        del c["status"], c["known_name"], c["checks"]
    doc = {"generated": "g", "data_release": "DR1", "zones": ["hunt"], "counts": {"mover": 2, "high_pm": 0, "spectral": 0,
                                                                                  "shape": 0}, "candidates": old}
    src = tmp_path / "in.json"
    src.write_text(json.dumps(doc))
    sides = tmp_path / "checks"
    sides.mkdir()
    _side(sides, "pixel", {"hunt-1": {"checks": [{"code": "one_detector", "level": "strong", "text": "D4 alone",
                                                  "look": "D1", "values": {"ratio": 1.9}}]}})
    out = tmp_path / "out.json"
    assert K.main(["--merge-only", str(src), "--checks-dir", str(sides), "--out", str(out)]) == 0
    new = json.loads(out.read_text())
    assert [c["id"] for c in new["candidates"]] == ["hunt-2", "hunt-1"]       # 0.8 x 0.3 = 0.24 < 0.4
    h1 = new["candidates"][1]
    assert h1["score"] == pytest.approx(0.24) and h1["checks"][0]["values"] == {"ratio": 1.9}
    assert new["candidates"][0]["status"] == "open" and new["candidates"][0]["checks"] == []
    assert new["counts"] == doc["counts"] and list(h1) == list(K.FIELDS)
