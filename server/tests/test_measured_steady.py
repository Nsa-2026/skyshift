"""Steady motion or a wobble (contracts/api.md v3.21): measured.mark_steady on synthetic sources and on the featured
fields' static files. Offline."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from skyshift_server import measured as M

DATA = Path(__file__).resolve().parents[2] / "web" / "public" / "data"
RA0, DEC0 = 150.0, 10.0
COS = math.cos(math.radians(DEC0))
T0 = 61000.0
HALF_YR = 182.625


def _at(east: float, north: float) -> tuple[float, float]:
    return RA0 + east / 3600.0 / COS, DEC0 + north / 3600.0


def _case(points, pm=(0.0, 0.0), sid=7, gaia_id=None, snr=40.0, n_dots=10):
    """points: (det, t_days, east, north). One shift, its detections and epochs."""
    epochs, dets = [], []
    for k, (det, t, e, n) in enumerate(points):
        eid = f"d{det}-v{k}"
        epochs.append({"id": eid, "kind": "visit", "mjd_mid": T0 + t, "det": det})
        ra, dec = _at(e, n)
        dets.append({"epoch_id": eid, "ra": ra, "dec": dec, "snr": snr, "n_dots": n_dots, "class": "star",
                     "source_id": sid})
    span = max(p[1] for p in points) / 365.25
    shift = {"source_id": sid, "gaia_id": gaia_id, "class": "star", "ra": RA0, "dec": DEC0, "mjd_from": T0,
             "mjd_to": T0 + max(p[1] for p in points), "dt_yr": span, "pmra": pm[0], "pmdec": pm[1],
             "shift_arcsec": math.hypot(*pm) * span / 1000.0, "pm_snr": 5.0}
    return shift, dets, epochs


def test_visit_error_is_the_raw_model_times_1_6():
    assert M.visit_error_arcsec(40.0, 10) == pytest.approx(1.6 * math.hypot(4.48 / 40.0, 0.25 / math.sqrt(10)))
    assert M.visit_error_arcsec(None, None) > 7  # nothing known: a large error, never a crash
    assert M.POS_ERR_SCALE == 1.6 and M.STEADY_MAX_CHI2_NU == 3.0


def test_out_and_back_is_a_wobble_not_a_motion():
    # the site owner's example: (0, 0) -> (-1.15, +0.17) -> (-0.34, +0.50)", one detector, six months apart; the
    # pipeline's fit called it a motion of (-0.34, +0.50)"/yr
    s, dets, eps = _case([(2, 0, 0.0, 0.0), (2, HALF_YR, -1.15, 0.17), (2, 2 * HALF_YR, -0.34, 0.50)],
                         pm=(-340.0, 500.0))
    M.mark_steady([s], dets, eps)
    assert s["steady"] is False
    assert s["chi2_nu"] > M.STEADY_MAX_CHI2_NU
    assert 0.5 < s["wobble_arcsec"] < 1.2
    assert [v["epoch_id"] for v in s["visits"]] == ["d2-v0", "d2-v1", "d2-v2"]  # time order
    assert all(v["err_arcsec"] == pytest.approx(M.visit_error_arcsec(40.0, 10), abs=1e-3) for v in s["visits"])
    json.dumps(s, allow_nan=False)


def test_a_straight_line_is_steady():
    # 3"/yr east, 2"/yr south, seen by two detectors with a 0.4" colour offset between them and 0.05" of noise
    pm = (3000.0, -2000.0)
    pts = []
    for det, off in ((2, 0.0), (5, 0.4)):
        for k, noise in enumerate((0.05, -0.05, 0.03)):
            t = k * HALF_YR
            pts.append((det, t, off + pm[0] / 1000 * t / 365.25 + noise, pm[1] / 1000 * t / 365.25 - noise))
    s, dets, eps = _case(pts, pm=pm)
    M.mark_steady([s], dets, eps)
    assert s["steady"] is True and s["chi2_nu"] < 1 and s["wobble_arcsec"] < 0.1
    assert len(s["visits"]) == 6


def test_reported_motion_the_visits_do_not_show_is_not_steady():
    # two visits a year apart at the same place, while the fit says 5"/yr
    s, dets, eps = _case([(2, 0, 0.0, 0.0), (2, 2 * HALF_YR, 0.05, -0.05)], pm=(4000.0, -3000.0))
    M.mark_steady([s], dets, eps)
    assert s["steady"] is False and s["wobble_arcsec"] > 2


def test_fast_star_with_a_scattering_core_stays_steady():
    # Barnard's-Star-like: 10"/yr north, bright (small errors), 0.5" of scatter that a saturated core gives
    pm = (0.0, 10000.0)
    pts = [(5, 0, 0.0, 0.0), (5, HALF_YR, 0.5, 5.0 - 0.4), (6, 10, 0.0, 0.27), (6, HALF_YR + 10, -0.5, 5.27 + 0.4)]
    s, dets, eps = _case(pts, pm=pm, snr=1200.0, n_dots=11)
    M.mark_steady([s], dets, eps)
    assert s["chi2_nu"] > M.STEADY_MAX_CHI2_NU  # the core scatters beyond its error ...
    assert s["steady"] is True                  # ... but the motion is far larger than the scatter


def test_gaia_parallax_is_not_a_wobble():
    from skyshift_core import parallax_offset_arcsec

    plx = 547.0  # Barnard's Star, no proper motion here: only the parallax moves it
    t = [0.0, HALF_YR, 2 * HALF_YR]
    pe, pn = parallax_offset_arcsec([RA0] * 3, [DEC0] * 3, [plx] * 3, [T0 + x for x in t])
    pts = [(2, x, float(e), float(n)) for x, e, n in zip(t, pe, pn)]
    s, dets, eps = _case(pts, gaia_id="99", snr=500.0, n_dots=12)
    M.mark_steady([s], dets, eps, gaia=[{"id": "99", "ra": RA0, "dec": DEC0, "parallax": plx}])
    assert s["steady"] is True and s["wobble_arcsec"] < 0.01
    s2, dets2, eps2 = _case(pts, gaia_id="99", snr=500.0, n_dots=12)
    M.mark_steady([s2], dets2, eps2)  # no gaia[] parallax: the out-and-back is a wobble
    assert s2["steady"] is False


def test_untested_without_two_visits_in_one_detector():
    s, dets, eps = _case([(1, 0, 0.0, 0.0), (4, HALF_YR, 1.0, 0.0)], pm=(2000.0, 0.0))
    M.mark_steady([s], dets, eps)
    assert s["steady"] is None and s["chi2_nu"] is None and s["wobble_arcsec"] is None and len(s["visits"]) == 2
    lone = {"source_id": 9, "ra": RA0, "dec": DEC0, "mjd_from": T0, "pmra": 1.0, "pmdec": 1.0}
    M.mark_steady([lone], dets, eps)
    assert lone["visits"] == [] and lone["steady"] is None


# --- the featured fields' static files (web/public/data/<slug>/frames_months_5.json, all detectors, uncapped) ----

def _featured(slug: str) -> tuple[dict, list[dict]]:
    path = DATA / slug / "frames_months_5.json"
    if not path.exists():
        pytest.skip(f"{path} not in this checkout")
    meta = json.loads(path.read_text())
    m = meta["measured"]
    shifts = copy.deepcopy(m["shifts"])
    for s in shifts:  # recompute from scratch
        for k in ("visits", "chi2_nu", "wobble_arcsec", "steady"):
            s.pop(k, None)
    M.mark_steady(shifts, m["detections"], meta["epochs"], meta.get("gaia"))
    return meta, shifts


def test_featured_fields(capsys):
    counts = {}
    for slug in ("barnard", "epsindib", "wise0855", "rwaur", "rdra"):
        _, shifts = _featured(slug)
        counts[slug] = (len(shifts), sum(s["steady"] is True for s in shifts), sum(s["steady"] is False for s in shifts))
    with capsys.disabled():
        print("\nfeatured months 5' shifts (all, steady, unsteady):", counts)
    assert counts == {"barnard": (3, 3, 0), "epsindib": (4, 3, 1), "wise0855": (2, 1, 1), "rwaur": (1, 1, 0),
                      "rdra": (2, 1, 1)}


def test_barnard_and_eps_indi_b_stay_clear_steady_motions():
    _, shifts = _featured("barnard")
    b = next(s for s in shifts if s["gaia_id"] == "4472832130942575872")
    assert b["steady"] is True and b["shift_arcsec"] > 9
    _, shifts = _featured("epsindib")
    e = max(shifts, key=lambda s: s["pm_snr"])
    assert e["steady"] is True and e["chi2_nu"] < 1 and e["shift_arcsec"] > 6


def test_featured_wobbles_are_not_motions():
    _, shifts = _featured("wise0855")
    w = next(s for s in shifts if s["source_id"] == 49649007959800)  # a "5.2 arcsec" fit, detectors disagree
    assert w["steady"] is False and w["wobble_arcsec"] > 1
    _, shifts = _featured("rdra")
    r = next(s for s in shifts if s["source_id"] == 94054005873700)
    assert r["steady"] is False and r["wobble_arcsec"] > 1
