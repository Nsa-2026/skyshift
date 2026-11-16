"""Offline tests of the vetting harness' pure helpers. Run: cd pipeline && uv run pytest ../scripts/vet/tests"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import vetlib as V  # noqa: E402


def test_numbers_verdict_static_mover_unclear():
    assert V.numbers_verdict({"det": [18, 19], "dif": [1, 1], "ref": [20, 18, 19, 21]}) == "static"
    assert V.numbers_verdict({"det": [140, 250], "dif": [145, 262], "ref": [1, 0, 2, 0]}) == "mover"
    # an asteroid crossing a faint star keeps most of its signal in the difference: still a mover
    assert V.numbers_verdict({"det": [304, 153], "dif": [260, 129], "ref": [34, 20, 46, 31]}) == "mover"
    assert V.numbers_verdict({"det": [9], "dif": [4], "ref": [3, 4, 6, 4]}) == "unclear"
    assert V.numbers_verdict({"det": [9], "dif": [], "ref": []}) == "unclear"  # no reference exposures


def test_source_ids_and_zone():
    assert V.source_ids("hunt-sp-66757007063700") == [66757007063700]
    assert V.source_ids("ecl34-pm-53109020936200-53104020935700") == [53109020936200, 53104020935700]
    assert V.zone_of("ecl15n1-pm-63516008931800") == "ecl15n1"


def test_grid_is_north_up_east_left():
    ra, dec = V.grid(120.0, 10.0)
    assert ra.shape == (V.N, V.N)
    assert dec[V.N - 1, V.C0] > dec[0, V.C0]          # row index grows to the north
    assert ra[V.C0, 0] > ra[V.C0, V.N - 1]            # column 0 is east
    gx, gy = V.to_grid(120.0, 10.0, ra[5, 7], dec[5, 7])
    assert abs(gx - 7) < 1e-3 and abs(gy - 5) < 1e-3  # to_grid inverts grid


def test_ap_snr_finds_a_source_and_reports_masked():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1.0, (V.N, V.N))
    a[V.C0, V.C0] += 40.0
    assert V.ap_snr(a, 1.0, [(V.C0, V.C0)]) > 10
    assert abs(V.ap_snr(a, 1.0, [(5, 5)])) < 4
    a[V.C0 - 2:V.C0 + 3, V.C0 - 2:V.C0 + 3] = np.nan
    assert np.isnan(V.ap_snr(a, 1.0, [(V.C0, V.C0)]))


def test_fmt_dt_units():
    assert V.fmt_dt(0.035) == "+2.1 min" and V.fmt_dt(-11.3) == "-11.3 h" and V.fmt_dt(24 * 172) == "+172 d"


def test_numbers_verdict_hot_pixel_on_a_fixed_star_is_not_a_mover():
    # round 1, ecl05-679040028059: other times at +27..+32, a pixel hit kept the difference high
    assert V.numbers_verdict({"det": [37, 89], "dif": [20, 54], "ref": [27, 32, 30, 29]}) == "unclear"


def _dot(n, det, x, y, mjd, snr=20.0, size=3.5, chi2=1.0):
    return {"n": n, "det": det, "x": x, "y": y, "mjd": mjd, "snr": snr, "size": size, "chi2": chi2, "obs": f"o{n}", "flux": 1.0, "fpk": 0}


def test_warnings_name_the_round_1_failure_modes():
    dots = [_dot(1, 4, 1361.0, 66.0, 61017.0052, size=2.0, chi2=12.0), _dot(2, 4, 1361.0, 66.0, 61017.0067)]
    rows = [{"mjd": 61017.0052, "det": 1, "dot": None, "snr": 0.2, "px": (1359.1, 77.3)},
            {"mjd": 61017.0052, "det": 4, "dot": dots[0], "snr": 63.0, "px": (1361.0, 66.1)},
            {"mjd": 61017.0067, "det": 1, "dot": None, "snr": 1.0, "px": (1359.2, 77.2)},
            {"mjd": 61017.0067, "det": 4, "dot": dots[1], "snr": 59.0, "px": (1361.0, 65.9)}]
    W = " ".join(V.warnings_for(None, dots, rows, {"ref": [3, 1, 0, -2], "_motion_sigma": 1.2}, None))
    for key in ("WHOLE-PIXEL POSITION", "SAME DETECTOR PIXEL", "NOT STAR-LIKE", "PAIRED DETECTOR EMPTY", "ONE DETECTOR ONLY",
                "NO TELESCOPE STEP", "STATIONARY"):
        assert key in W, key
    assert "SOURCE AT OTHER TIMES" not in W
    assert V.session_steps(rows) and max(V.session_steps(rows)) < 1


def test_warnings_gap_between_detections():
    dots = [_dot(1, 6, 100.3, 500.2, 61000.0000), _dot(2, 6, 100.4, 155.1, 61000.0045)]
    rows = [{"mjd": 61000.0000, "det": 6, "dot": dots[0], "snr": 20.0, "px": (100.3, 500.2)},
            {"mjd": 61000.0015, "det": 6, "dot": None, "snr": 0.5, "px": (100.3, 385.0)},
            {"mjd": 61000.0030, "det": 6, "dot": None, "snr": 1.0, "px": (100.3, 270.0)},
            {"mjd": 61000.0045, "det": 6, "dot": dots[1], "snr": 18.0, "px": (100.4, 155.1)}]
    W = " ".join(V.warnings_for(None, dots, rows, {"ref": [0, 1]}, None))
    assert "GAP BETWEEN DETECTIONS" in W and "NO TELESCOPE STEP" not in W


def test_rb_score_features_are_numbers_and_ignore_known_columns():
    import pandas as pd
    import rb_score
    mv = pd.DataFrame([{"id": "z-1-2", "snr": 20.0, "flux_ujy": 800.0, "n_dots": 3, "n_exposures": 3, "evidence": "two_exposures",
                        "dot_size": [3.5, 3.8, 3.6], "dot_chi2": [1.0, 2.0, 1.5], "dot_snr": [9.0, 12.0, 10.0], "dot_x": [100.3, 100.9, 500.0],
                        "dot_y": [200.2, 315.4, 430.1], "dot_det": [3, 3, 6], "fixed_sep": 25.0, "gaia_sep": float("nan"), "gaia_g": float("nan"),
                        "rate_arcsec_h": 30.0, "known_sep": 0.4, "known_name": "Ceres"}])
    fd = pd.DataFrame({"detector": [3, 3, 3, 1], "x": [100.2, 100.4, 99.9, 5.0], "y": [200.0, 200.3, 199.8, 5.0]})
    f = rb_score.mover_features(mv, fd)
    assert not any(c.startswith("known") for c in f.columns) and f.notna().all().all()
    r = f.iloc[0]
    assert r.pix_hist_max == 3 and r.pix_hist_min == 0 and r.has_pair == 1 and r.only_long == 0 and r.gaia_sep == 60 and r.motion_snr == -1


def _engine(tmp_path, monkeypatch, blind=False):
    import json

    import pandas as pd
    (tmp_path / "checks").mkdir()
    (tmp_path / "vet").mkdir()
    (tmp_path / "results" / "zz").mkdir(parents=True)
    px = {"candidates": {"zz-1-1": {"status": None, "known_name": None, "checks": [
        {"code": "partner_seen", "level": "ok", "text": "D2 also measured it.", "values": {}}],
        "one_photo": {"verdict": "brighter", "det": 5, "photos": 5, "places": 3, "flux_now_ujy": 6400.8, "flux_other_ujy": 1733.0,
                      "flux_other_err_ujy": 89.0, "ratio": 0.27, "diff_snr": 23.7}}}}
    json.dump(px, open(tmp_path / "checks" / "pixel.json", "w"))
    json.dump({"candidates": {"zz-2-2": {"checks": [{"code": "hot_pixel_proven", "level": "strong", "text": "A hot pixel."}], "status": "flaw"}}},
              open(tmp_path / "vet" / "pixel_blind.json", "w"))
    json.dump({"candidates": {"zz-1-1": {"checks": [{"code": "known_nearby", "level": "warn", "text": "The known asteroid Xyz was 71 arcsec off."}]}}},
              open(tmp_path / "checks" / "known.json", "w"))
    base = dict(visit=1, ra=10.0, dec=5.0, cell=1, n_movers=1, n_recovered_sessions=0, span_h=np.nan, rate_arcsec_h=np.nan, pa_deg=np.nan,
                rate_sigma=np.nan, rate_snr=np.nan, vra_arcsec_h=np.nan, vdec_arcsec_h=np.nan, fit_chi2_nu=np.nan, track_chi2_nu=np.nan,
                resid_arcsec=np.nan, fit_degree=0)
    on = dict(base, tracklet_id=7, n_sessions=3, n_movers=3, n_recovered_sessions=1, span_h=26.0, rate_arcsec_h=30.0, pa_deg=80.0, rate_sigma=0.1,
              rate_snr=300.0, track_chi2_nu=0.1, resid_arcsec=0.4)
    rows = [dict(base, mover_id="zz-1-1", session=4, mjd=60000.0, tracklet_id=-1, n_sessions=1)] + [
        dict(on, mover_id=f"zz-{k}-{k}", session=10 + k, mjd=60001.0 + k) for k in (2, 3, 4)]
    pd.DataFrame(rows).to_parquet(tmp_path / "results" / "zz" / "tracklets.parquet")
    pd.DataFrame([dict(tracklet_id=7, visit=1, session=20, detector=5, mjd=60008.0, snr=4.5, sep_arcsec=1.2)]).to_parquet(
        tmp_path / "results" / "zz" / "tracklet_dots.parquet")
    monkeypatch.setattr(V, "ENGINE", str(tmp_path))
    monkeypatch.setattr(V, "HIDE_KNOWN", blind)
    V._engine_checks.cache_clear()
    V._tracklets.cache_clear()


def test_engine_checks_show_numbers_rates_and_hide_known_bodies_when_blind(tmp_path, monkeypatch):
    _engine(tmp_path, monkeypatch)
    text = "\n".join(V.engine_check_lines("zz-1-1"))
    assert "now 6,401 µJy, at other times 1,733 ± 89 µJy" in text and "3.7×" in text and "Verdict: brighter" in text
    assert "does NOT count against the mover" in text                      # an asteroid passing a star
    assert "[fires on 67% of real movers, 5% of junk]" in text and "Xyz" in text
    assert "certain flaw" in "\n".join(V.engine_check_lines("zz-2-2"))     # checks made for a blind round are read from vet/
    assert "NOT AVAILABLE" in "\n".join(V.engine_check_lines("zz-9-9"))
    (tmp_path / "b").mkdir()
    _engine(tmp_path / "b", monkeypatch, blind=True)
    assert "Xyz" not in "\n".join(V.engine_check_lines("zz-1-1"))          # a known-body line would give a blind answer away
    monkeypatch.setattr(V, "ENGINE", "off")
    V._engine_checks.cache_clear()
    assert V.engine_check_lines("zz-1-1") == []


def test_track_lines_and_the_track_gate(tmp_path, monkeypatch):
    import api_round as A
    _engine(tmp_path, monkeypatch)
    assert V.tracklet_of("zz-1-1") == {} and "NONE" in V.tracklet_lines("zz-1-1")[0] and "no evidence against" in V.tracklet_lines("zz-1-1")[0]
    text = "\n".join(V.tracklet_lines("zz-3-3"))
    assert "ON A TRACK THROUGH 3 SESSIONS" in text and "<- this mover" in text and "Recovered below the mover cut in 1" in text
    assert "NOT AVAILABLE" in V.tracklet_lines("zz-99-99")[0] and V.tracklet_of("yy-1-1") is None
    assert A.sessions_on_track({"id": "zz-3-3", "kind": "mover"}) == 3 and A.sessions_on_track({"id": "zz-1-1", "kind": "mover"}) == 0
    drop = {"verdict": "artifact", "checks": [], "junk_proof": "one_pixel_spike_no_psf"}
    assert "track through 3 sessions" in A.contradiction(drop, "mover", ["packet"], 3)   # a drop against a track is asked about once
    assert A.contradiction(drop, "mover", ["packet"], 0) is None
