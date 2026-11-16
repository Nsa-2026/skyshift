"""scripts/candidates/pixel_check.py (no network): selection, geometry, history names and the checks' rules.

    cd server && uv run pytest ../scripts/candidates/tests
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pixel_check as PC  # noqa: E402
import s3pixels as S3  # noqa: E402

T0 = 61000.0
MIN = 1.0 / 1440.0


def cand(cid, grade="B", reasons=("two_exposures",), status=None, kind="mover"):
    c = {"id": cid, "kind": kind, "grade": grade, "reasons": list(reasons), "zone": "z"}
    if status:
        c["status"] = status
    return c


def test_select_movers_takes_open_a_and_b():
    doc = {"candidates": [cand("a"), cand("b", grade="C"), cand("c", reasons=("motion_not_significant",)),
                          cand("d", grade="A", reasons=("motion_not_significant",)), cand("e", status="known"),
                          cand("f", kind="spectral"), cand("g", status="flaw"), cand("h", grade=None),
                          cand("i", grade="A", status="open")]}
    # A or B whatever their reasons (motion_not_significant is a warning, not the old To-check rule); open only
    assert [c["id"] for c in PC.select_movers(doc, "ab")] == ["a", "c", "d", "i"]
    assert PC.select_movers(doc, "tocheck") == PC.select_movers(doc, "ab")
    assert [c["id"] for c in PC.select_movers(doc, "open")] == ["a", "b", "c", "d", "h", "i"]


def test_in_polygon_and_tangent():
    sq = "POLYGON ICRS 359.9 -0.1 0.1 -0.1 0.1 0.1 359.9 0.1"  # across RA 0
    assert PC.in_polygon(sq, 0.0, 0.0) and PC.in_polygon(sq, 359.95, 0.05)
    assert not PC.in_polygon(sq, 0.2, 0.0)
    assert PC.in_polygon(None, 1.0, 1.0)  # unknown footprint: don't exclude
    e, n = PC.tangent(10.0 + 1 / 3600.0, 60.0 + 2 / 3600.0, 10.0, 60.0)
    assert e == pytest.approx(0.5, rel=1e-3) and n == pytest.approx(2.0)
    ra, dec = PC.untangent(e, n, 10.0, 60.0)
    assert ra == pytest.approx(10.0 + 1 / 3600.0) and dec == pytest.approx(60.0 + 2 / 3600.0)


def test_motion_fit_rate_and_growing_error():
    t = np.array([-2.1, 0.0, 2.1]) / 60.0  # hours
    sig = np.full(3, 0.8)
    fit = PC.motion_fit(t, 100.0 * t, 0.0 * t, sig)
    pe, pn, cov = fit
    assert pe[1] == pytest.approx(100.0) and pn[1] == pytest.approx(0.0, abs=1e-9)
    s = lambda th: np.sqrt(np.array([1, th]) @ cov @ np.array([1, th]))  # noqa: E731
    assert s(0.0) < s(1.0) < s(10.0)
    assert PC.motion_fit(np.zeros(2), np.zeros(2), np.zeros(2), np.ones(2)) is None
    assert PC.pos_err(8.0) == pytest.approx(np.hypot(0.43, 0.96 * 7.53 / 8.0))


def row(dets, xs, ys, mjds, flux=500.0, snr=8.0, obs=None, rate=50.0):
    n = len(dets)
    obs = obs or [f"2025W34_1B_0366_{i + 1}" for i in range(n)]
    return pd.Series({"dot_obs": obs, "dot_det": dets, "dot_x": xs, "dot_y": ys, "dot_mjd": mjds,
                      "dot_flux": [flux] * n, "dot_snr": [snr] * n, "dot_size": [4.5] * n, "dot_fpk": [0] * n,
                      "rate_arcsec_h": rate, "evidence": "two_exposures"})


def test_dots_of_sorts_by_time():
    m = PC.Mover("x", "z", row([4, 4], [10.0, 11.0], [20.0, 135.0], [T0 + 2 * MIN, T0]))
    assert [d["mjd"] for d in m.dots] == [T0, T0 + 2 * MIN]
    assert m.rate == 50.0


class FakeReader:
    def __init__(self, keys):
        self.keys = keys

    def listing(self, prefix):
        return [k for k in self.keys if k.startswith(prefix)]


def test_neighbours_cross_into_the_previous_observation():
    d = "dr1/level2/2025W34_1B/l2b-v27-2026-228/4"
    name = lambda s, e: f"{d}/level2_2025W34_1B_{s:04d}_{e}D4_spx_l2b-v27-2026-228.fits"  # noqa: E731
    keys = [name(365, 1), name(365, 2), name(365, 3), name(366, 1), name(366, 2), name(366, 3)]
    nb = PC.neighbours(None, "2025W34_1B_0366_1", 4, name(366, 1), FakeReader(keys), 2, 1)
    assert [(o, k) for _, o, k in nb] == [("2025W34_1B_0365_3", -1), ("2025W34_1B_0365_2", -2), ("2025W34_1B_0366_2", 1)]
    assert nb[0][0] == S3.S3_HTTPS + name(365, 3)
    nb = PC.neighbours(None, "2025W34_1B_0366_3", 4, name(366, 3), FakeReader(keys), 1, 1)
    assert [(o, k) for _, o, k in nb] == [("2025W34_1B_0366_2", -1)]  # nothing after 0366_3 (0367 unlisted)
    assert S3.parse_obs("2025W34_1B_0366_3") == ("2025W34_1B", 366, 3)
    assert S3.parse_obs("bad") is None


def mover_with(dets, xs, ys, mjds, ras, decs, **kw):
    m = PC.Mover("m", "z", row(dets, xs, ys, mjds, **kw))
    for d, ra, dec in zip(m.dots, ras, decs):
        d["ra"], d["dec"] = ra, dec
        d["key"] = f"own|{d['obs']}|{d['det']}"
    return m


def own_res(m, size_ratio=1.0, flux=500.0, err=60.0, transient=0):
    return {d["key"]: dict(flux=flux, err=err, snr=flux / err, masked=False, size_ratio=size_ratio, n_stars=20,
                           size_arcsec=4.5 * size_ratio, elong=1.1, transient_px=transient, sat=False)
            for d in m.dots}


def codes(ev):
    return {(c["code"], c["level"]) for c in ev["checks"]}


def test_d4_alone_not_in_d1_is_strong_and_partner_seen_is_ok():
    mjds = [T0, T0 + 2.1 * MIN]
    m = mover_with([4, 4], [500.0, 501.0], [800.0, 685.0], mjds, [10.0, 10.0005], [5.0, 5.0])
    res = own_res(m)
    for i, d in enumerate(m.dots):
        it = dict(url=f"u{i}", ra=d["ra"], dec=d["dec"], dot=d, det=1, key=f"p{i}")
        m.plan["partner"].append(it)
        res[f"p{i}"] = dict(flux=10.0, err=80.0, snr=0.125, masked=False)
    ev = PC.evaluate(m, res)
    assert ("one_detector", "strong") in codes(ev)
    c = next(c for c in ev["checks"] if c["code"] == "one_detector")
    assert c["values"]["beyond_known"] is True and "D1 photographed the same spot" in c["text"]
    assert "D4" in c["look"] and "UTC" in c["look"]
    # the partner saw it: ok, never a cut
    for i in range(2):
        res[f"p{i}"] = dict(flux=900.0, err=80.0, snr=11.25, masked=False)
    ev = PC.evaluate(m, res, {"guard|4": dict(flux=20.0, err=40.0, snr=0.5, n=3)})
    assert ("partner_seen", "ok") in codes(ev) and ev["status"] is None
    # ... unless the same spot shows that light on other days too (a fixed star in D1)
    ev = PC.evaluate(m, res, {"guard|4": dict(flux=800.0, err=60.0, snr=13.0, n=3)})
    assert ("one_detector", "warn") in codes(ev) and ("partner_seen", "ok") not in codes(ev)


def test_d6_alone_red_is_only_a_warning():
    m = mover_with([6, 6], [500.0, 501.0], [800.0, 685.0], [T0, T0 + 2.1 * MIN], [10.0, 10.0005], [5.0, 5.0],
                   flux=1800.0)
    res = own_res(m, flux=1800.0)
    for i, d in enumerate(m.dots):
        m.plan["partner"].append(dict(url=f"u{i}", ra=d["ra"], dec=d["dec"], dot=d, det=3, key=f"p{i}"))
        res[f"p{i}"] = dict(flux=-50.0, err=60.0, snr=-0.8, masked=False)
    ev = PC.evaluate(m, res)
    assert ("one_detector", "warn") in codes(ev)


def test_same_pixel_while_the_sky_moved_is_a_flaw():
    m = mover_with([4, 4], [500.2, 500.6], [800.0, 800.4], [T0, T0 + 2.1 * MIN], [10.0, 10.2], [5.0, 5.0])
    ev = PC.evaluate(m, own_res(m))
    # certain: the contract's flaw rule needs a code ending in _proven (v3.24); hot_pixel stays the uncertain one
    assert ev["status"] == "flaw" and ("hot_pixel_proven", "strong") in codes(ev)
    assert not any(c["code"] == "hot_pixel" for c in ev["checks"])


def test_sharp_and_one_photo_steady():
    m = mover_with([2, 2], [500.0, 501.0], [800.0, 685.0], [T0, T0 + 2.1 * MIN], [10.0, 10.0005], [5.0, 5.0])
    res = own_res(m, size_ratio=0.45)
    for i, d in enumerate(m.dots):
        for j in range(6):
            k = f"s{i}{j}"
            m.plan["static"].append(dict(url=f"s{j}", ra=d["ra"], dec=d["dec"], dot=d, det=2, key=k))
            res[k] = dict(flux=250.0, err=90.0, snr=2.8, masked=False)
    ev = PC.evaluate(m, res)
    assert ("sharp", "strong") in codes(ev)
    assert ("one_photo", "strong") in codes(ev)
    one = next(c for c in ev["checks"] if c["code"] == "one_photo")
    assert one["values"]["steady_places"] == 2
    # "same place, same brightness": 500 ± 60 now against 250 ± 37 at the place on other days, 3.6σ apart
    v = one["values"]
    assert v["flux_now_ujy"] == 500.0 and round(v["flux_other_ujy"]) == 250 and round(v["other_ratio"], 2) == 0.5
    assert round(v["diff_snr"], 1) == round(250 / np.hypot(60, 90 / np.sqrt(6)), 1)
    assert ev["one_photo"]["verdict"] == "steady" and ev["one_photo"]["flux_now_ujy"] == 500.0
    # nothing at its places on other days: ok, with the same numbers
    for k in list(res):
        if k.startswith("s"):
            res[k] = dict(flux=5.0, err=90.0, snr=0.05, masked=False)
    ev = PC.evaluate(m, res)
    assert ("one_photo", "ok") in codes(ev)
    assert next(c for c in ev["checks"] if c["code"] == "one_photo")["values"]["flux_now_ujy"] == 500.0
    assert [c["level"] for c in ev["checks"]] == sorted((c["level"] for c in ev["checks"]),
                                                        key=PC.LEVEL_ORDER.get)


def test_an_asteroid_passing_a_star_is_brighter_not_steady():
    """Seraphina (ecl05-636180034101-87934733, a star 3″ away) and Erato (ecl05-641820031188-88225904, 8.8″): the
    others see the star at the place, but the dot holds about twice its light, many σ more: one_photo ok with the
    numbers, status open."""
    m = mover_with([5, 5], [500.0, 501.0], [800.0, 685.0], [T0, T0 + 2.1 * MIN], [10.0, 10.0005], [5.0, 5.0],
                   flux=6000.0)
    res = own_res(m, flux=6000.0, err=200.0)
    for i, d in enumerate(m.dots):
        for j in range(6):
            k = f"s{i}{j}"
            m.plan["static"].append(dict(url=f"s{j}", ra=d["ra"], dec=d["dec"], dot=d, det=5, key=k))
            res[k] = dict(flux=2900.0, err=150.0, snr=19.3, masked=False)
    ev = PC.evaluate(m, res)
    one = next(c for c in ev["checks"] if c["code"] == "one_photo")
    assert one["level"] == "ok" and ev["status"] is None and "2.1× as much" in one["text"]
    v = one["values"]
    assert v["brighter_places"] == 2 and round(v["other_ratio"], 2) == 0.48 and v["diff_snr"] > 10
    assert ev["one_photo"]["verdict"] == "brighter" and round(ev["one_photo"]["flux_other_ujy"]) == 2900
    # the star's light centred 1.4 px (8.8″) beside the place on other days (Erato): still brighter, not "neighbour"
    for it in m.plan["static"]:
        it["jac"] = [[1.0, 0.0], [0.0, 1.0]]
        res[it["key"]] = res[it["key"]] | {"dx": 1.4, "dy": 0.0}
    one = next(c for c in PC.evaluate(m, res)["checks"] if c["code"] == "one_photo")
    assert one["level"] == "ok" and one["values"]["brighter_places"] == 2
    # the star alone (the dot only 1.2× its light): its neighbour's light again (warn), and strong where it is centred
    # on the place
    for k in list(res):
        if k.startswith("s"):
            res[k] = res[k] | {"flux": 5000.0}
    assert ("one_photo", "warn") in codes(PC.evaluate(m, res))
    for k in list(res):
        if k.startswith("s"):
            res[k] = res[k] | {"dx": 0.0}
    assert ("one_photo", "strong") in codes(PC.evaluate(m, res))


def test_persistence_behind_a_saturated_star():
    m = mover_with([5, 5], [500.0, 501.0], [800.0, 685.0], [T0, T0 + 2.1 * MIN], [10.0, 10.0005], [5.0, 5.0])
    res = own_res(m)
    for i, d in enumerate(m.dots):
        m.plan["history"].append(dict(url=f"h{i}", obs="x", offset=-1, dot=d, key=f"h{i}", mjd=d["mjd"] - 2.1 * MIN))
        res[f"h{i}"] = dict(flux=90000.0, err=200.0, snr=450.0, masked=False, sat=True, persist=False)
    ev = PC.evaluate(m, res)
    assert ("persistence_proven", "strong") in codes(ev) and ev["status"] == "flaw"
    res["h1"] = dict(flux=30.0, err=200.0, snr=0.15, masked=False, sat=False, persist=False)
    ev = PC.evaluate(m, res)
    assert ("persistence", "warn") in codes(ev) and ev["status"] is None


def test_hot_pixel_needs_every_neighbour_of_other_sky_to_glow_like_the_dot():
    m = mover_with([1, 1], [500.0, 501.0], [800.0, 685.0], [T0, T0 + 2.1 * MIN], [10.0, 10.0005], [5.0, 5.0])
    res = own_res(m)
    for i, d in enumerate(m.dots):
        for j, off in enumerate((-1, -2, 1)):
            k = f"h{i}{j}"
            m.plan["history"].append(dict(url=k, obs="x", offset=off, dot=d, key=k, mjd=d["mjd"] + off * 2.1 * MIN,
                                          sky_px=115.0))
            res[k] = dict(flux=0.8 * d["flux"], err=60.0, snr=0.8 * d["flux"] / 60.0, masked=False, sat=False)
    ev = PC.evaluate(m, res)
    assert ("hot_pixel_proven", "strong") in codes(ev) and ev["status"] == "flaw"
    # one neighbour of dot 0 shows nothing there: dot 0 is not on a hot pixel, so neither proven nor flaw
    res["h00"] = dict(flux=-20.0, err=60.0, snr=-0.3, masked=False, sat=False)
    ev = PC.evaluate(m, res)
    assert ("hot_pixel", "warn") in codes(ev) and ev["status"] is None
    # a star 10x brighter than the dot at that pixel is not the pixel glowing
    res["h00"] = dict(flux=10 * m.dots[0]["flux"], err=60.0, snr=80.0, masked=False, sat=False)
    assert ("hot_pixel", "warn") in codes(PC.evaluate(m, res))
    # a neighbour of the same sky (pointing unchanged) tests nothing: dot 0 keeps only two, both glowing
    m.plan["history"][0]["sky_px"] = 0.5
    ev = PC.evaluate(m, res)
    assert ("hot_pixel_proven", "strong") in codes(ev)


def test_wide_dots_and_no_path_credit_next_to_a_fixed_source():
    m = mover_with([2, 2], [500.0, 501.0], [800.0, 685.0], [T0, T0 + 2.1 * MIN], [10.0, 10.0005], [5.0, 5.0])
    res = own_res(m, size_ratio=1.5)
    ev = PC.evaluate(m, res)
    # every dot wide: a warning only (a comet's haze or a blend), never strong; the text says what to look at
    assert ("wide", "warn") in codes(ev) and not any(c["code"] == "sharp" for c in ev["checks"])
    wide = next(c for c in ev["checks"] if c["code"] == "wide")
    assert "a comet's haze, or two faint things blended" in wide["text"] and "fuzzy head" in wide["look"]
    assert not any(c["level"] == "strong" for c in ev["checks"])
    one_wide = res | {m.dots[0]["key"]: res[m.dots[0]["key"]] | {"size_ratio": 1.0}}  # median 1.25: not wide
    assert not any(c["code"] == "wide" for c in PC.evaluate(m, one_wide)["checks"])
    # saturated dots don't count (their masked flat core leaves the wings: Eros measured 1.6–1.8×)
    sat = {k: v | {"sat": True} for k, v in res.items()}
    assert not any(c["code"] == "wide" for c in PC.evaluate(m, sat)["checks"])
    # a fast mover's own trail is allowed for: 1.5× at 900″/h (trail growth 1.65) is not wide
    m.rate = 900.0
    assert abs(PC.trail_factor(900.0) - 1.654) < 0.01 and PC.trail_factor(float("nan")) == 1.0
    assert not any(c["code"] == "wide" for c in PC.evaluate(m, res)["checks"])
    m.rate = float("nan")
    # light found at two predicted places of its path, 1″ from its first place
    for j in range(2):
        k = f"p{j}"
        m.plan["path"].append(dict(url=k, ra=10.0003, dec=5.0, det=2, mjd=T0 + (j + 2) * 2.1 * MIN, key=k, rho=1.0))
        res[k] = dict(best_snr=8.0, best_flux=400.0, best_err=50.0, best_dx=0.1, best_dy=0.1, chance95=3.0)
    assert ("faint_on_path", "ok") in codes(PC.evaluate(m, res))
    # but a fixed source sits at its places (one_photo steady): that light proves nothing
    for i, d in enumerate(m.dots):
        for j in range(6):
            k = f"s{i}{j}"
            m.plan["static"].append(dict(url=f"s{j}", ra=d["ra"], dec=d["dec"], dot=d, det=2, key=k))
            res[k] = dict(flux=300.0, err=60.0, snr=5.0, masked=False)
    ev = PC.evaluate(m, res)
    assert ("one_photo", "strong") in codes(ev) and not any(c["code"] == "faint_on_path" for c in ev["checks"])


def test_main_writes_the_producer_format(tmp_path, monkeypatch):
    root = tmp_path
    (root / "results" / "z").mkdir(parents=True)
    r = row([4, 4], [500.0, 501.0], [800.0, 685.0], [T0, T0 + 2.1 * MIN])
    df = pd.DataFrame([dict(r, id="m1")])
    df.to_parquet(root / "results" / "z" / "suspects.parquet")
    cj = tmp_path / "candidates.json"
    cj.write_text(json.dumps({"candidates": [cand("m1"), cand("m2")]}))
    fake = {"m1": {"checks": [PC.check("sharp", "ok", "t", "", None)], "status": None, "known_name": None,
                   "metrics": {}, "one_photo": None}}
    monkeypatch.setattr(PC, "run", lambda movers, tables, meas: {m.id: fake[m.id] for m in movers})
    assert PC.main(["--candidates", str(cj), "--root", str(root)]) == 0
    out = json.loads((root / "checks" / "pixel.json").read_text())
    assert out["source"] == "pixel" and out["candidates_file"] == str(cj.resolve())
    assert set(out["candidates"]) == {"m1"}  # only ids it measured
    assert out["candidates"]["m1"] == {"checks": [{"code": "sharp", "level": "ok", "text": "t", "look": "",
                                                   "values": None}], "status": None, "known_name": None}
