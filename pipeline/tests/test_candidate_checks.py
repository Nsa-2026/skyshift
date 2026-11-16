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
    assert c["values"] == {"dots_peak": 2, "n_dots": 3, "known_share": 0.68}
    assert CK.factor({"kind": "mover", "checks": [c]}) == 1.0


def test_halo_warn_and_every_peak_strong():
    m = _mover(flags=1 << 28, dots={"flags3": [1 << 28, 1 << 28, 0], "fpk": [1 << 28, 0, 0]})
    (c,) = CK.mover_checks(m)
    assert c["code"] == "flag_halo" and c["level"] == "warn"
    assert c["text"] == ("HALO (the glow around a bright star) is set around 2 of its 3 dots, at the peak of 1; known "
                         "asteroids carry it 22% of the time, but almost never at the peak.")
    assert c["values"] == {"dots_flagged": 2, "dots_peak": 1, "n_dots": 3, "known_share": 0.22}
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
    assert next(c for c in ch if c["code"] == "partner_seen")["text"].startswith(
        "D2 and D5 both saw it at the same moment in its one exposure: real light")
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
    slow = CK.mover_checks(_mover(pa=220.0, ref=ref))
    assert codes(slow) == {"odd_direction": "warn"} and slow[0]["text"].endswith("; none of them move this far off.")
    some = CK.mover_checks(_mover(pa=105.0 + 60.0, ref=_ref(spread=80.0)))
    assert some[0]["values"]["known_like_it"] == int((_ref(spread=80.0)[1]["dpa"] >= some[0]["values"]["dpa_deg"]).sum())
    on = _mover(rate=q + 40, rate_err=5.0, pa=220.0, ref=ref, track=(3, q + 40, 20.0), on_track=True)
    assert codes(CK.mover_checks(on)) == {"odd_direction": "warn", "fast": "warn", "seen_again": "ok",
                                          "chance_tracks": "ok"}
    # a rate without an error (a grade C row: link's in-session motion, no dots read) still gets fast / odd_direction
    noerr = CK.mover_checks(_mover(rate=q + 40, rate_err=None, pa=220.0, ref=ref))
    assert codes(noerr) == {"odd_direction": "strong", "fast": "warn"}
    assert next(c for c in noerr if c["code"] == "fast")["text"].startswith(f"Moves {q + 40:.0f}″ an hour; 99% of the")
    # motion not significant: no direction claim
    assert codes(CK.mover_checks(_mover(pa=220.0, ref=ref, no_motion=True))) == {"may_not_move": "warn"}
    # too few known asteroids in the zone: nothing to compare with
    assert codes(CK.mover_checks(_mover(rate=500.0, pa=220.0, ref=_ref(n=10)))) == {}


TRACK_INFO = {"tracklet_id": 86, "n_sessions": 4, "n_movers": 4, "n_recovered_sessions": 2, "span_h": 30.2,
              "rate_arcsec_h": 25.0, "pa_deg": 60.0, "rate_snr": 12.0, "session_rate_arcsec_h": 27.0,
              "session_pa_deg": 70.0, "zone_tracks": 217, "zone_recovered_dots": 641, "control_tracks": 0,
              "control_recovered_dots": 3}


def test_seen_again_and_chance_tracks():
    """A mover on a track was found again in other sessions: `seen_again` ok with the track and the zone's controls;
    chance_tracks (contract v3.24) ok when the shuffled control made no track, warn when it made some."""
    m = _mover(track=(4, 25.0, 12.0), on_track=True, control_tracks=0, track_info=TRACK_INFO)
    ch = CK.mover_checks(m)
    assert codes(ch) == {"seen_again": "ok", "chance_tracks": "ok"}
    c = next(x for x in ch if x["code"] == "seen_again")
    ct = next(x for x in ch if x["code"] == "chance_tracks")
    assert ct["text"].startswith("Followed over 4 sessions of its visit at 25.0″ an hour, 12σ; the zone's shuffled control")
    # v3.25 follow-up: the track's rate is track_rate_arcsec_h in all three track checks (rate_arcsec_h is ours)
    assert ct["values"] == {"sessions": 4, "track_rate_arcsec_h": 25.0, "control_tracks": 0}
    assert c["text"] == ("Seen again: followed over 4 sessions of its visit (4 movers, and dots found again along its "
                         "path in 2 more sessions) over 30 hours at 25.0″ an hour towards PA 60°, 12σ; within its own "
                         "session 27″ an hour towards PA 70°. In this zone the same search with the sessions shuffled "
                         "made no tracks (against 217 real ones) and tracks moved 2′ off found 3 chance dots along "
                         "their paths (against 641 on the real paths). A real object turns up again along one smooth "
                         "path; chance specks rarely do.")
    v = c["values"]
    assert (v["tracklet_id"], v["sessions"], v["n_recovered_sessions"], v["track_rate_arcsec_h"], v["track_pa_deg"],
            v["session_rate_arcsec_h"], v["control_tracks"], v["control_recovered_dots"]) == (86, 4, 2, 25.0, 60.0,
                                                                                              27.0, 0, 3)
    assert CK.factor({"kind": "mover", "grade": "A", "checks": [c]}) == 1.0
    m = _mover(track=(3, 25.0, 12.0), on_track=True, control_tracks=1)
    assert codes(CK.mover_checks(m)) == {"seen_again": "ok", "chance_tracks": "warn"}
    (bare,) = CK.mover_checks(_mover(track=(3, 25.0, 12.0), on_track=True, control_tracks=None))
    assert bare["text"] == ("Seen again: followed over 3 sessions of its visit at 25.0″ an hour, 12σ. A real object "
                            "turns up again along one smooth path; chance specks rarely do.")


def test_fast_held():
    """A grade B track faster than 400″/h that suspects.track_grades kept at B (no recovered dot, no partner)."""
    m = _mover(track=(2, 520.0, 9.0), on_track=True, control_tracks=None, fast_held=(2, 520.0), ref=_ref(n=10))
    c = {x["code"]: x for x in CK.mover_checks(m)}
    assert c["fast_held"]["level"] == "warn"
    assert c["fast_held"]["values"] == {"sessions": 2, "track_rate_arcsec_h": 520.0, "limit_arcsec_h": 400.0}
    assert c["fast_held"]["text"].startswith("Followed over 2 sessions at 520″ an hour, faster than 400″ an hour, where "
                                             "tracks of 2 sessions also join up glitches of one detector")
    row = {"grade": "B", "tracklet_id": 3, "n_sessions": 2, "rate_arcsec_h": 520.0, "rate_sigma": 50.0, "rate_snr": 10.4}
    rows = pd.DataFrame([row, {**row, "rate_arcsec_h": 300.0}, {**row, "grade": "A"}, {**row, "rate_snr": 4.0},
                         {**row, "tracklet_id": -1}])
    assert [K.fast_held(r) for r in rows.itertuples()] == [(2, 520.0), None, None, None, None]


# --- brightness and place ---------------------------------------------------------------------------------------
def test_flux_jump():
    m = _mover(dots={"flux": [900.0, 300.0, 600.0]})
    (c,) = CK.mover_checks(m)
    assert (c["code"], c["level"]) == ("flux_jump", "warn")
    assert c["text"] == "Its D2 brightness changes 3.0× between exposures; known asteroids change less than 2.2× (98% of them)."
    m = _mover(dots={"flux": [900.0, 150.0, 600.0]}, gates=["flux_ratio"], grade="C")
    (c,) = CK.mover_checks(m)
    assert c["level"] == "strong" and c["text"].endswith("a cosmic-ray hit paired with a fainter speck looks like this.")
    assert CK.counted_elsewhere({"kind": "mover", "grade": "C", "reasons": ["flux_ratio"]}, c)  # grade C's x0.1 ...
    assert not CK.counted_elsewhere({"kind": "mover", "grade": "C", "reasons": ["edge"]}, c)     # ... for its own gates
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
    assert t["catalogue_star"] == "2MASS lists a source within 6″ of it: probably that star's own light, not a mover."
    cand = {"kind": "mover", "grade": "C", "checks": ch, "reasons": m["gates"]}
    assert CK.factor(cand) == 1.0                                            # all counted by grade C
    assert CK.factor({**cand, "grade": "B"}) == pytest.approx(0.3 ** 7)


def test_suspects_warnings():
    m = _mover(flags=1 << 28, dots={"flags3": [1 << 28, 0, 0]},
               warn={"flag_halo": {"star_sep_arcsec": 22.0}, "bright_star": {"sep_arcsec": 25.0},
                     "new_code": {"ratio": 1.5}})
    ch = CK.mover_checks(m)
    c = {x["code"]: x for x in ch}
    assert c["flag_halo"]["values"]["star_sep_arcsec"] == 22.0 and c["flag_halo"]["text"].startswith("HALO")
    assert c["bright_star"]["text"] == ("A bright fixed source lies 25″ away: its glow, ghosts and spikes can make dots; "
                                        "real asteroids have one within 30″ 1–7% of the time, the unexplained movers "
                                        "18–43%.")
    assert c["new_code"]["text"] == "Kept with a warning from the suspects step (new code: ratio 1.5)."
    assert all(x["level"] == "warn" for x in ch)


def test_values_keys_keep_one_meaning():
    """v3.27: the exporter's numbers and the suspects' never share a key with two meanings in one check, and suspects
    tables written before v3.27 are read under the new names."""
    m = _mover(excess=(4, 2, 1.6), near_source=7.5, bright=(8.9, 48.0), dots={"flux": [900.0, 100.0, 600.0]},
               warn={"bright_star": {"sep_arcsec": 25.0, "star_snr": 300},
                     "catalogue_star": {"sep_arcsec": 4.2, "gaia_sep_arcsec": 4.2, "fainter_mag": 3.1},
                     "flux_jump": {"ratio": 9.0},                          # before v3.27
                     "unseen": {"exposures": 2, "cover": 4},               # before v3.27
                     "wide": {"ratio_med": 1.5, "ratio_min": 1.4, "dots": 3, "limit": 1.3, "trail": 1.2},
                     "persistence": {"dots_at_peak": 1, "n_dots": 3}})
    v = {x["code"]: x["values"] for x in CK.mover_checks(m)}
    assert v["bright_star"]["gaia_g_mag"] == 8.9 and v["bright_star"]["gaia_sep_arcsec"] == 48.0
    assert v["bright_star"]["sep_arcsec"] == 25.0                            # the suspects' fixed source
    assert v["catalogue_star"]["near_source_arcsec"] == 7.5 and v["catalogue_star"]["sep_arcsec"] == 4.2
    assert v["detector_excess"]["excess_ratio"] == 1.6 and "ratio" not in v["detector_excess"]
    assert v["flux_jump"]["flux_ratio_max"] == 9.0 and "ratio" not in v["flux_jump"]
    assert v["unseen"] == {"unseen_exposures": 2, "cover_exposures": 4}
    assert v["wide"] == {"size_ratio_med": 1.5, "size_ratio_min": 1.4, "wide_dots": 3, "limit_ratio": 1.3,
                         "trail_factor": 1.2}
    assert v["persistence"] == {"dots_peak": 1, "n_dots": 3}
    hb = CK.highpm_checks({"reasons": [], "blend": (4.1, 0.8, 2), "gaia_match": False})
    assert hb[0]["code"] == "blend" and hb[0]["values"] == {"apart_arcsec": 4.1, "dg_mag": 0.8, "gaia_stars": 2}


def test_suspects_new_tables():
    """The suspects' 16 Nov tables: former vetoes kept as warn_codes/warn_values, their strong forms as grade C reasons."""
    w = {"catalogue_star": {"sep_arcsec": 5.6, "gaia_sep_arcsec": 5.6, "gaia_g_mag": 16.73, "mover_ab_mag": 14.45,
                            "fainter_mag": 2.3},
         "known_nearby": {"sep_arcsec": 25.1, "known_rate_arcsec_h": 41.0, "known_pa_deg": 111, "known_v_mag": 19.4},
         "persistence": {"dots_at_peak": 1, "n_dots": 3}, "flag_bloom": {"dots_at_peak": 0, "n_dots": 3},
         "bright_star": {"sep_arcsec": 25.1, "star_snr": 384, "star_ab_mag": 10.0, "star_flux_ratio": 1.7}}
    m = _mover(warn=w, near_source=7.5, nearest_known=25.1)
    ch = CK.mover_checks(m)
    c = {x["code"]: x for x in ch}
    assert codes(ch) == {"catalogue_star": "warn", "known_nearby": "warn", "persistence": "warn", "flag_bloom": "warn",
                         "bright_star": "warn"}
    assert c["catalogue_star"]["text"] == ("Gaia DR3 lists a source 5.6″ away, 2.3 mag fainter than the mover: real "
                                           "asteroids pass such fainter neighbours often, but the dot may also be that "
                                           "source.")
    assert c["catalogue_star"]["values"]["sep_arcsec"] == 5.6 and c["catalogue_star"]["values"]["gaia_g_mag"] == 16.73
    assert c["known_nearby"]["text"] == ("A known body at V 19.4 was predicted 25″ away, moving 41″ an hour towards PA "
                                         "111°; this one moves 36″ an hour towards PA 100° within its session: the "
                                         "motions agree, so it is probably that body a little off its predicted place.")
    assert c["persistence"]["text"].startswith("PERSIST (an afterimage of a bright star seen earlier on the same pixels) "
                                               "is set at the peak of 1 of its 3 dots")
    assert c["flag_bloom"]["text"] == ("BLOOM (charge spilling out of a saturated star) is set around at least one of its "
                                       "dots.")
    assert c["bright_star"]["text"].startswith("A bright fixed source (AB 10.0, S/N 384) lies 25″ away")
    src = CK.mover_checks(_mover(warn={"catalogue_star": {"source_mask_all_dots": 1}}))
    assert src[0]["text"] == ("NASA's SOURCE mask covers the peak of every one of its dots (its own source list puts "
                              "something here).")
    # grade C reasons from the strong forms: catalogue within 6″ and within 2 mag, bloom/halo at every peak, bright 15″
    g = CK.mover_checks(_mover(grade="C", gates=["gaia", "halo", "bright_star"],
                               warn={"catalogue_star": {**w["catalogue_star"], "fainter_mag": 0.8},
                                     "bright_star": {"sep_arcsec": 11.0, "star_snr": 900}}))
    c = {x["code"]: x for x in g}
    assert codes(g) == {"catalogue_star": "strong", "flag_halo": "strong", "bright_star": "strong"}
    assert c["catalogue_star"]["text"] == ("Gaia DR3 lists a source 5.6″ away, only 0.8 mag fainter than the mover: "
                                           "probably that star's own light, not a mover (a catalogue source this close "
                                           "and this bright: real asteroids 0.2–2%, the unexplained movers 14–64%).")
    assert c["flag_halo"]["text"].startswith("HALO (the glow around a bright star) is set at the peak of every one of its")
    assert c["bright_star"]["text"].startswith("A bright fixed source (S/N ≥ 100) lies 11″ away")
    assert CK.factor({"kind": "mover", "grade": "C", "reasons": ["gaia", "halo", "bright_star"], "checks": g}) == 1.0


def test_known_nearby_uses_session_motion_and_name():
    """Known bodies are compared on the in-session motion (the suspects' own numbers, else session_rate_arcsec_h /
    session_pa_deg), never a track's chord; the suspects' nearest_known_name names the body; values under the v3.24
    follow-up's keys (rate_arcsec_h, pa_deg: ours; body_rate_arcsec_h, body_pa_deg: the body's)."""
    w = {"known_nearby": {"sep_arcsec": 16.8, "known_rate_arcsec_h": 44.5, "known_pa_deg": 126, "known_v_mag": 20.5,
                          "rate_arcsec_h": 59.4, "pa_deg": 356}}
    m = _mover(warn=w, nearest_known=16.8, known_name="(64868)", rate=107.0, pa=54.0, session_rate=98.0, session_pa=80.0)
    c = {x["code"]: x for x in CK.mover_checks(m)}["known_nearby"]
    assert c["text"] == ("A known body, (64868) at V 20.5, was predicted 17″ away, moving 44″ an hour towards PA 126°; "
                         "this one moves 59″ an hour towards PA 356° within its session: a different motion, so probably "
                         "not that body.")
    v = c["values"]
    assert (v["name"], v["v"], v["body_rate_arcsec_h"], v["body_pa_deg"], v["rate_arcsec_h"], v["pa_deg"]) == \
        ("(64868)", 20.5, 44.5, 126, 59.4, 356)
    assert v["rate_ratio"] == pytest.approx(59.4 / 44.5, abs=1e-3) and v["dpa_deg"] == 130.0
    assert "known_rate_arcsec_h" not in v and "known_v_mag" not in v
    # older warn_values without our motion: the row's session motion, not the exporter's rate
    old = {"known_nearby": {k: x for k, x in w["known_nearby"].items() if k not in ("rate_arcsec_h", "pa_deg")}}
    c = {x["code"]: x for x in CK.mover_checks({**m, "warn": old})}["known_nearby"]
    assert c["values"]["rate_arcsec_h"] == 98.0 and "moves 98″ an hour towards PA 80° within its session" in c["text"]
    # 2MASS Ks under the contract's key
    tm = CK.mover_checks(_mover(warn={"catalogue_star": {"sep_arcsec": 4.0, "tmass_sep_arcsec": 4.0, "tmass_k_mag": 13.1}}))
    assert tm[0]["values"]["tmass_ks_mag"] == 13.1 and "tmass_k_mag" not in tm[0]["values"]
    # the suspects could not measure ours (null): no comparison
    unmeasured = {"known_nearby": {**w["known_nearby"], "rate_arcsec_h": None, "pa_deg": None}}
    c = {x["code"]: x for x in CK.mover_checks({**m, "warn": unmeasured})}["known_nearby"]
    assert c["text"].endswith("its own motion could not be compared; if it moves alike, it is that body a little off its "
                              "place.")
    # the exporter's own known_nearby (no suspects warning) names the body too
    (own,) = CK.mover_checks(_mover(nearest_known=45.0, known_name="Sadanori"))
    assert own["text"].startswith("A known asteroid, comet, planet or moon, Sadanori, was predicted 45″ away")
    assert own["values"] == {"sep_arcsec": 45.0, "name": "Sadanori"}


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
    pair = CK.highpm_checks({"reasons": ["pm_pair", "vetted"], "pm": 2500.0, "method": "pair", "n_common_det": 1})
    assert codes(pair) == {"one_detector_shift": "warn"}
    assert pair[0]["text"].startswith("The two visits' sources share only one detector, so the match of their "
                                      "brightness rests on one detector's dots")
    assert codes(CK.highpm_checks({"reasons": ["pm_pair"], "pm": 2500.0, "method": "pair", "n_common_det": 3})) == {}
    # a Gaia star whose motion we confirm: the blend may pull our value, it doesn't make the motion
    g = CK.highpm_checks({**f, "gaia_match": True, "fit_mode": "pm+gaia_plx", "saturated": False})
    assert codes(g) == {"blend": "warn"} and g[0]["text"].endswith("(Gaia's own motion is the one to trust).")


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
    no = CK.spectral_checks({"labels": ["h2o_vapour"], "catalogs": ["Gaia DR3", "CatWISE2020", "2MASS"]})
    assert no[0]["text"] == "Gaia DR3, CatWISE2020 and 2MASS were searched; CatWISE2020 has no entry within 3″ of it."


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


def test_merge_known_body_and_gate_levels():
    """v3.24: a producer's known_body replaces the exporter's known_nearby (and adds no factor); a producer's check of a
    mover's own failed gate (hot_pixel, sharp) replaces the gate's only when at least as strong."""
    own_kn = CK.check("known_nearby", "warn", "exporter's", "", {"sep_arcsec": 12.0})
    gate = CK.check("hot_pixel", "strong", "gate", "")
    sharp = CK.check("sharp", "strong", "gate sharp", "")
    c = _cand("m", grade="C", reasons=["hot_pixel", "sharp_outlier"], checks=[own_kn, gate, sharp], score=0.05)
    known = {"checks": [{"code": "known_body", "level": "strong", "text": "it is (1) Ceres", "look": "",
                         "values": {"name": "(1) Ceres"}}], "status": "known", "known_name": "(1) Ceres"}
    pixel = {"checks": [{"code": "hot_pixel", "level": "warn", "text": "pixel's", "look": "", "values": None},
                        {"code": "sharp", "level": "strong", "text": "pixel's sharp", "look": "", "values": None}]}
    CK.merge_one(c, [("known", known), ("pixel", pixel)])
    by = {x["code"]: x for x in c["checks"]}
    assert set(by) == {"known_body", "hot_pixel", "sharp"} and (c["status"], c["known_name"]) == ("known", "(1) Ceres")
    assert by["hot_pixel"]["text"] == "gate" and by["sharp"]["text"] == "pixel's sharp"
    assert c["score"] == pytest.approx(0.05)
    # not a gate of this mover: the producer's measurement replaces the exporter's check whatever its level
    b = _cand("b", checks=[CK.check("hot_pixel", "strong", "exporter's", "")], score=0.3)
    CK.merge_one(b, [("pixel", pixel)])
    assert {x["code"]: x["text"] for x in b["checks"]}["hot_pixel"] == "pixel's"
    assert b["score"] == pytest.approx(0.3 / 0.3 * 0.95 * 0.3)


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


# --- grade C movers in per-zone more files ------------------------------------------------------------------------
def test_split_more():
    """candidates.json keeps A/B movers, every other kind and known/flaw entries; open grade C movers go to
    candidates-more/<zone>.json, indexed by `more`; counts count what the main file lists."""
    cs = [_cand("h-a", grade="A"), _cand("h-b"), _cand("h-c1", grade="C", frames_url="cand/h-c1/frames.json"),
          _cand("h-c2", grade="C", status="known", known_name="(1) Ceres"), _cand("h-c3", grade="C", status="flaw"),
          _cand("e-c1", grade="C", zone="ecl04"), _cand("h-hp", kind="high_pm")]
    doc = {"generated": "g", "data_release": "DR1", "zones": ["hunt", "ecl04"], "counts": {}, "candidates": cs}
    main, files = K.split_more(doc)
    assert [c["id"] for c in main["candidates"]] == ["h-a", "h-b", "h-c2", "h-c3", "h-hp"]
    assert main["counts"] == {"mover": 4, "high_pm": 1, "spectral": 0, "shape": 0}
    assert main["more"] == [{"zone": "hunt", "kind": "mover", "grade": "C", "count": 1, "url": "candidates-more/hunt.json"},
                            {"zone": "ecl04", "kind": "mover", "grade": "C", "count": 1,
                             "url": "candidates-more/ecl04.json"}]
    assert list(main)[:5] == ["generated", "data_release", "zones", "counts", "more"]
    assert files["hunt"] == {"generated": "g", "data_release": "DR1", "zone": "hunt", "kind": "mover", "grade": "C",
                             "count": 1, "candidates": [{**cs[2], "frames_url": None}]}   # static frames: main file only
    assert json.loads(K.dumps(files["ecl04"]))["candidates"] == [cs[5]]


def test_grade_c_movers_from_the_table_alone(tmp_path, monkeypatch):
    """A whole export: grade C movers read no dot file (rate from link's in-session motion), go to the more file;
    --merge-only merges side files into the more files too, and a C mover made known moves to candidates.json."""
    from test_candidates import _write, _zone_files

    from skyshift_pipeline.store import Store, results_rel

    store = Store(str(tmp_path / "bucket"))
    _zone_files(store, "hunt", 125.0, 20.0, with_extra_mover=True)
    s = store.read_parquet(results_rel("hunt", "suspects.parquet"))
    s["grade"], s["reasons"] = ["B", "C"], [[], ["gaia"]]
    s["evidence"] = "two_exposures"
    s["session_rate_arcsec_h"], s["session_pa_deg"] = [np.nan, 55.0], [np.nan, 200.0]  # B: no in-session motion
    _write(store, results_rel("hunt", "suspects.parquet"), s)
    seen = []
    real = K.mover_dots

    def spy(st, susp, w=32, only=None):
        seen.append([i for i, k in zip(susp["id"], only if only is not None else [True] * len(susp)) if k])
        return real(st, susp, w, only)

    monkeypatch.setattr(K, "mover_dots", spy)
    doc = K.build(store, ["hunt"])
    assert seen == [["hunt-1-2"]]           # B without link's motion: its dots' positions; the grade C row's never
    c = next(c for c in doc["candidates"] if c["id"] == "hunt-5-6")
    assert (c["grade"], c["rate_arcsec_h"], c["pa_deg"], c["gaia_match"], c["catwise_match"]) == ("C", 55.0, 200.0,
                                                                                                 True, False)
    b = next(c for c in doc["candidates"] if c["id"] == "hunt-1-2")
    assert b["rate_arcsec_h"] == pytest.approx(36.0, abs=0.1) and not b["gaia_match"]
    # with link's in-session motion in the table, a B mover reads no positions either (its dot flags only)
    s2 = s.assign(session_rate_arcsec_h=[36.4, 55.0], session_pa_deg=[61.0, 200.0])
    _write(store, results_rel("hunt", "suspects.parquet"), s2)
    seen.clear()
    b2 = next(c for c in K.build(store, ["hunt"])["candidates"] if c["id"] == "hunt-1-2")
    assert seen == [] and (b2["rate_arcsec_h"], b2["pa_deg"]) == (36.4, 61.0)
    _write(store, results_rel("hunt", "suspects.parquet"), s)
    out = tmp_path / "data" / "candidates.json"
    sizes = K.write_export(doc, out)
    assert set(sizes) == {"candidates.json", "candidates-more/hunt.json"}
    main = json.loads(out.read_text())
    assert [c["id"] for c in main["candidates"] if c["kind"] == "mover"] == ["hunt-1-2"] and main["counts"]["mover"] == 1
    assert main["more"] == [{"zone": "hunt", "kind": "mover", "grade": "C", "count": 1,
                             "url": "candidates-more/hunt.json"}]
    more = json.loads((out.parent / "candidates-more" / "hunt.json").read_text())
    assert [x["id"] for x in more["candidates"]] == ["hunt-5-6"] and list(more["candidates"][0]) == list(K.FIELDS)
    # a producer's check on a more-file candidate is merged there
    sides = tmp_path / "checks"
    sides.mkdir()
    _side(sides, "pixel", {"hunt-5-6": {"checks": [{"code": "one_detector", "level": "strong", "text": "D4 alone",
                                                    "look": "D1", "values": {"ratio": 1.9}}]}})
    assert K.main(["--merge-only", str(out), "--checks-dir", str(sides), "--out", str(out)]) == 0
    more = json.loads((out.parent / "candidates-more" / "hunt.json").read_text())
    assert next(x for x in more["candidates"][0]["checks"] if x["code"] == "one_detector")["text"] == "D4 alone"
    assert json.loads(out.read_text())["more"][0]["count"] == 1
    # made known: it moves to candidates.json and the zone's more file goes
    _side(sides, "known", {"hunt-5-6": {"checks": [], "status": "known", "known_name": "(1) Ceres"}})
    assert K.main(["--merge-only", str(out), "--checks-dir", str(sides), "--out", str(out)]) == 0
    main = json.loads(out.read_text())
    assert {c["id"] for c in main["candidates"] if c["kind"] == "mover"} == {"hunt-1-2", "hunt-5-6"}
    assert main["more"] == [] and not (out.parent / "candidates-more" / "hunt.json").exists()


# --- 16 Nov: the suspects' mover flaws (#385) ------------------------------------------------------------------------
FLAWS = {
    "hot_pixel": {"exposures_max": 4, "exposures_min": 1, "n_dots": 3, "away_arcsec": 30.0},
    "hot_pixel_proven": {"exposures_min": 3, "n_dots": 3, "away_arcsec": 30.0},
    "same_pixel": {"px": 0.4, "limit_px": 1.0},
    "whole_pixel": {"dots": 2, "n_dots": 3, "sigma_px": 0.3},
    "wide": {"ratio_med": 1.62, "ratio_min": 1.41, "dots": 3, "limit": 1.3},
    "cross_detector": {"exposures": 2, "n_dots": 2},
    "fixed_source": {"sep_arcsec": 4.2, "flux_ratio": 1.3, "fixed_snr": 55, "gaia_sep_arcsec": None, "gaia_g_mag": None,
                     "motion_sigma": 1.1},
    "glow": {"star_g_mag": 5.2, "star_sep_arcsec": 312, "ratio_med": 1.7},
    "unseen": {"exposures": 2, "cover": 4},
    "may_not_move": {"sigma": 1.8, "rate_arcsec_h": 20.0, "rate_err_arcsec_h": 11.1},
    "blend": {"sep1_arcsec": 6.0, "sep2_arcsec": 7.1, "apart_arcsec": 12.8, "g1_mag": 17.2, "g2_mag": 18.0},
}


def test_mover_flaw_words_and_levels():
    """Every new suspects code has its own words (never the generic fallback), the level comes from warn_levels, and
    unseen's keys get names of their own (`exposures` means the mover's own exposures elsewhere)."""
    strong = {k: "strong" for k in ("hot_pixel_proven", "same_pixel", "whole_pixel", "wide", "cross_detector",
                                    "fixed_source", "glow", "unseen")}
    ch = CK.mover_checks(_mover(warn=FLAWS, warn_levels=strong))
    c = {x["code"]: x for x in ch}
    assert codes(ch) == {**{k: "warn" for k in FLAWS}, **strong}
    assert not any(x["text"].startswith("Kept with a warning from the suspects step") for x in ch)
    assert CK.status_of(ch) == "flaw"                                  # the suspects' hot_pixel_proven, strong
    assert c["hot_pixel_proven"]["text"].startswith("Every one of its 3 dots sits on a detector pixel that also holds "
                                                    "dots at other places on the sky in 3 or more exposures")
    assert c["hot_pixel"]["text"].startswith("One of its dots sits on a detector pixel that also holds dots at other "
                                             "places on the sky (more than 30″ away) in 4 exposures: perhaps a hot pixel")
    assert "(0.40 px apart)" in c["same_pixel"]["text"]
    assert c["whole_pixel"]["text"].startswith("2 of its 3 dots sit exactly on a whole pixel")
    assert c["wide"]["text"].startswith("Its dots are 1.41–1.62× wider than a star of its brightness: a comet's haze, or two faint things blended.")
    assert c["cross_detector"]["text"].startswith("Its 2 exposures never put two dots in the same detector")
    assert c["fixed_source"]["text"].startswith("A fixed source (S/N 55) lies 4.2″ away, 1.3× as bright as this dot, and "
                                                "its own motion is only 1.1σ")
    assert "5.2′ from a G 5.2 star" in c["glow"]["text"]
    assert c["unseen"]["text"].startswith("2 of the 4 exposures of its session that photographed its path do not show it")
    assert c["unseen"]["values"] == {"unseen_exposures": 2, "cover_exposures": 4}
    assert c["may_not_move"]["text"].startswith("Its motion within the session, 20 ± 11″ an hour, is only 1.8σ")
    assert c["blend"]["text"].startswith("It sits between two Gaia stars (G 17.2 and 18.0) 12.8″ apart")
    # the same codes from a table without warn_levels: warnings, status open
    old = CK.mover_checks(_mover(warn=FLAWS))
    assert all(x["level"] == "warn" for x in old) and CK.status_of(old) == "open"


def test_mover_flaws_are_gate_codes():
    """The new reasons are vetting gates: on a grade C the C factor stands for their strong checks (and for
    hot_pixel_proven under hot_pixel); a B pays them."""
    gates = ["hot_pixel", "same_pixel", "whole_pixel", "wide", "cross_detector", "fixed_source", "glow", "unseen"]
    strong = {k: "strong" for k in gates + ["hot_pixel_proven"]}
    w = {k: FLAWS[k] for k in gates + ["hot_pixel_proven"]}
    ch = CK.mover_checks(_mover(grade="C", gates=gates, warn=w, warn_levels=strong))
    assert all(x["level"] == "strong" for x in ch) and len(ch) == len(strong)
    cand = {"kind": "mover", "grade": "C", "reasons": gates, "checks": ch}
    assert CK.factor(cand) == 1.0 and CK.gate_codes(cand) == set(gates)
    assert CK.factor({**cand, "grade": "B"}) == pytest.approx(CK.STRONG_FACTOR ** len(strong))


def test_d2_ramp_edge_warning_says_what_to_check():
    """The suspects' `edge` warning with ramp values (D2's last rows): a warning (× 0.95), never a grade; a real edge
    dot (the exporter's own strong edge) keeps its words and takes the ramp numbers."""
    ramp = {"ramp_dots": 1, "ramp_y": 2013, "ramp_from_y": 1975.0, "ramp_detector": 2}
    m = _mover(dots={"y": [2013.4, 1898.4, 1783.4]}, warn={"edge": ramp})
    ch = CK.mover_checks(m)
    assert codes(ch) == {"edge": "warn"}
    c = ch[0]
    assert c["text"].startswith("Its motion rests on a D2 dot in row 2013, inside D2's last 65 rows (y ≥ 1975), where a "
                                "background ramp along the detector's edge makes fake dots")
    assert "real asteroids 0.4%, the unexplained movers 0.3%" in c["text"]
    assert c["look"].startswith("On the hours clock, open D2 at the exposure where the dot sits near the photo's edge")
    assert c["values"] == ramp
    assert CK.factor({"kind": "mover", "grade": "B", "checks": ch, "reasons": []}) == pytest.approx(0.95)
    two = CK.mover_checks(_mover(dots={"y": [2001.4, 2034.4, 1900.4]}, warn={"edge": {**ramp, "ramp_dots": 2, "ramp_y": 2001}}))
    (e,) = [x for x in two if x["code"] == "edge"]
    assert e["level"] == "strong" and "px from the D2 detector's edge" in e["text"] and e["values"]["ramp_dots"] == 2


def test_wide_reason_says_graded_c_for_wide_and_blend():
    """#394: `wide` is a grade C reason only together with a blend (VET_WIDE_WITH). The check says it was graded C for the
    two together, with the pair's numbers from the blend warning; the C factor stands for it (GATE_CODES). Wide with a
    blend but no reason (a grade-A track overruled it) and wide alone keep the warning's words; a table from before #394
    (wide alone a reason, no with_blend) says that was the old rule."""
    wide = {**FLAWS["wide"], "with_blend": 1}
    m = _mover(grade="C", gates=["wide"], warn={"wide": wide, "blend": FLAWS["blend"]},
               warn_levels={"wide": "strong"})
    ch = CK.mover_checks(m)
    (w,) = [x for x in ch if x["code"] == "wide"]
    assert w["level"] == "strong" and w["text"].startswith(
        "Graded C for its width together with a blend: its dots are 1.41–1.62× wider than a star of its brightness, and "
        "it sits between two Gaia stars (G 17.2 and 18.0) 12.8″ apart: most likely their merged light, not a moving object")
    assert "none of 41 real asteroids or 49 comet movers" in w["text"] and "months clock" in w["look"]
    assert w["values"]["with_blend"] == 1
    cand = {"kind": "mover", "grade": "C", "reasons": ["wide"], "checks": ch}
    assert CK.gate_codes(cand) == {"wide"} and CK.counted_elsewhere(cand, w)
    # the same numbers on a grade-A track that overruled the reason: a warning in the warning's own words
    (a,) = [x for x in CK.mover_checks(_mover(grade="A", warn={"wide": wide, "blend": FLAWS["blend"]})) if x["code"] == "wide"]
    assert a["level"] == "warn" and a["text"].startswith("Its dots are 1.41–1.62× wider than a star of its brightness: a "
                                                          "comet's haze") and "Two Gaia stars sit either side" in a["text"]
    # wide alone (a comet's haze as often as a blend): the warning, no blend sentence
    (b,) = [x for x in CK.mover_checks(_mover(warn={"wide": FLAWS["wide"]})) if x["code"] == "wide"]
    assert b["level"] == "warn" and "Graded C" not in b["text"] and "Two Gaia stars" not in b["text"]
    # a suspects table from before #394: wide alone was a reason
    (o,) = [x for x in CK.mover_checks(_mover(grade="C", gates=["wide"], warn={"wide": FLAWS["wide"]},
                                              warn_levels={"wide": "strong"})) if x["code"] == "wide"]
    assert o["level"] == "strong" and o["text"].startswith("Graded C for its width: its dots are 1.41–1.62× wider") \
        and "the vetting's rule before 16 Nov evening" in o["text"]


def test_hot_pixel_gate_words_follow_the_rule():
    """The gate's words: the 16 Nov rule (other sky places, its exposure count) when the suspects' values say so; the old
    map's words for older tables; a stronger suspects level lifts an exporter warning."""
    new = CK.mover_checks(_mover(grade="C", gates=["hot_pixel"], warn={"hot_pixel": {**FLAWS["hot_pixel"], "exposures_max": 7}},
                                 warn_levels={"hot_pixel": "strong"}))
    (h,) = [x for x in new if x["code"] == "hot_pixel"]
    assert h["level"] == "strong" and "(more than 30″ away) in 7 exposures: a hot pixel NASA's map misses" in h["text"]
    assert h["values"]["exposures_max"] == 7
    old = CK.mover_checks(_mover(grade="C", gates=["hot_pixel"]))
    assert old[0]["text"].startswith("One of its dots sits on a detector pixel where the zone's other photos also have dots")
    # may_not_move: the exporter's own fit stays in the values (its text quotes it); a strong suspects level would lift it
    mm = CK.mover_checks(_mover(no_motion=True, rate=10.0, rate_err=8.0, warn={"may_not_move": FLAWS["may_not_move"]}))
    (c,) = [x for x in mm if x["code"] == "may_not_move"]
    assert c["values"]["rate_arcsec_h"] == 10.0 and c["values"]["sigma"] == pytest.approx(1.25)
    lifted = CK.mover_checks(_mover(no_motion=True, rate=10.0, rate_err=8.0, warn={"may_not_move": FLAWS["may_not_move"]},
                                    warn_levels={"may_not_move": "strong"}))
    assert [x["level"] for x in lifted if x["code"] == "may_not_move"] == ["strong"]
