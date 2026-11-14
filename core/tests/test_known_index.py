"""known_index: two-body maths, names, index round trip and the coarse cut's completeness (numpy only);
build_index + known_near against kete when kete is installed (`uv sync --extra known`)."""

import numpy as np
import pytest

from skyshift_core import known_index as K

# Four real MPC rows (mpcorb_2026-11-14.parquet) and one comet (cometels_2026-11-14.parquet).
ASTEROIDS = {
    "desig": ["1", "4", "433", "443104"],
    "name": ["Ceres", "Vesta", "Eros", None],
    "h_mag": [3.34, 3.25, 10.39, 24.16],
    "g_phase": [0.15, 0.15, 0.15, 0.15],
    "peri_dist": [2.5451594, 2.148362, 1.1332333, 0.833089],
    "ecc": [0.0796923, 0.0902037, 0.222878, 0.20326],
    "incl": [10.58803, 7.14393, 10.82855, 6.99344],
    "lon_node": [80.24863, 103.70135, 304.26797, 268.33087],
    "peri_arg": [73.2942, 151.46856, 178.91814, 265.76995],
    "peri_time": [2461599.8415399976, 2460901.587189988, 2461088.8135000137, 2461009.15599],
    "epoch": [2461200.5] * 4,
}
COMETS = {
    "desig": ["C/2025 R2 "], "peri_dist": [0.504275], "ecc": [0.994144], "incl": [4.4729],
    "lon_node": [335.3156], "peri_arg": [308.3723], "peri_time": [2460931.295599982], "epoch": [2461357.5],
}


def test_kepler_solves_high_eccentricity():
    k = K.Kepler.from_elements([0.1, 1.0, 2.5], [0.995, 0.3, 0.0], [10, 0, 0], [0, 0, 0], [0, 0, 0],
                               [2461000.5] * 3, dtype=np.float64)
    for dt in np.linspace(-500, 500, 41):
        p = k.positions(2461000.5 + dt)
        r = np.sqrt((p * p).sum(0))
        a, e = np.array([0.1, 1.0, 2.5]) / (1 - np.array([0.995, 0.3, 0.0])), np.array([0.995, 0.3, 0.0])
        assert np.all(r >= a * (1 - e) - 1e-9) and np.all(r <= a * (1 + e) + 1e-9)
    assert np.allclose(k.positions(2461000.5)[:, 0], [0.1, 0.0, 0.0], atol=1e-12)  # at perihelion, on x


def test_float32_close_to_float64():
    rng = np.random.default_rng(3)
    n = 2000
    args = (rng.uniform(1.5, 3.0, n), rng.uniform(0, 0.4, n), rng.uniform(0, 30, n), rng.uniform(0, 360, n),
            rng.uniform(0, 360, n), rng.uniform(2459000, 2462000, n))
    p64 = K.Kepler.from_elements(*args, dtype=np.float64).positions(2461000.5)
    p32 = K.Kepler.from_elements(*args, dtype=np.float32).positions(2461000.5)
    assert np.abs(p64 - p32).max() < 2e-6  # AU: < 0.4″ at 1 AU, far inside the margins


def test_earth_position_about_one_au():
    for mjd in (60736.0, 61000.0, 61465.0):
        r = np.linalg.norm(K.earth_position(float(K.mjd_to_jd_tdb(mjd))))
        assert 0.98 < r < 1.02


def test_orbit_arrays_names():
    ast = {**{k: v[:3] + v[:1] for k, v in ASTEROIDS.items()}}
    ast["desig"] = ["1", "4", "433", "K99X01A"]  # a second "Ceres"
    el = K.orbit_arrays(ast, COMETS)
    assert list(el["name"]) == ["Ceres [1]", "Vesta", "Eros", "Ceres [K99X01A]", "C/2025 R2"]
    assert list(el["comet"]) == [False] * 4 + [True]
    assert np.isnan(el["h"][-1]) and el["g"][-1] == 0.15
    el = K.orbit_arrays({**ASTEROIDS, "name": [None, "", "Eros", None]})
    assert list(el["name"]) == ["(1)", "(4)", "Eros", "(443104)"]


def _synthetic_index(n=4000, seed=1, err_deg=0.0) -> K.KnownIndex:
    """Random main-belt and NEO-like orbits, every margin set by hand (no kete)."""
    rng = np.random.default_rng(seed)
    neo = rng.random(n) < 0.1
    q = np.where(neo, rng.uniform(0.8, 1.3, n), rng.uniform(1.8, 3.2, n))
    e = np.where(neo, rng.uniform(0.1, 0.6, n), rng.uniform(0.0, 0.3, n))
    names = [f"obj{k}" for k in range(n)] + ["comet"]
    blob, off = K._pack_names(names)
    data = {
        "version": np.array(K.INDEX_VERSION), "snapshot": np.array("synthetic"), "built": np.array("now"),
        "mjd_lo": np.array(K.MJD_LO), "mjd_hi": np.array(K.MJD_HI), "v_keep": np.array(K.V_KEEP),
        "q": np.r_[q, 0.5], "e": np.r_[e, 0.99], "inc": np.r_[rng.uniform(0, 25, n), 4.0],
        "node": np.r_[rng.uniform(0, 360, n), 0.0], "argp": np.r_[rng.uniform(0, 360, n), 0.0],
        "tp": np.r_[rng.uniform(2459500, 2462500, n), 2461000.0], "epoch": np.full(n + 1, 2461200.5),
        "h": np.r_[np.full(n, 15.0), np.nan].astype(np.float32), "g": np.full(n + 1, 0.15, np.float32),
        "comet": np.r_[np.zeros(n, bool), True], "err_deg": np.r_[np.full(n, err_deg), np.nan].astype(np.float32),
        "always": np.zeros(n + 1, bool), "name_blob": blob, "name_off": off,
    }
    return K.KnownIndex(data)


def test_index_round_trip(tmp_path):
    idx = _synthetic_index(50)
    K.save_index(str(tmp_path / "i.npz"), idx.data)
    back = K.load_index(str(tmp_path / "i.npz"))
    assert back.meta == idx.meta and back.name(7) == "obj7" and back.name(50) == "comet"
    assert np.array_equal(back.data["q"], idx.data["q"])


def _truth_rows(idx, ra, dec, rad_deg, mjds):
    """Asteroids within rad of the centre at an exact time, float64 two-body, exact Earth-Moon barycentre."""
    c = K.unit_radec(ra, dec)
    kep = K.Kepler.from_elements(*(idx.data[k][idx.ast_rows] for k in ("q", "e", "inc", "node", "argp", "tp")),
                                 dtype=np.float64)
    hit = np.zeros(len(idx.ast_rows), bool)
    for jd in K.mjd_to_jd_tdb(mjds):
        geo = kep.positions(float(jd)) - K.earth_position(float(jd))[:, None]
        u = K.ecl_to_eq(geo / np.sqrt((geo * geo).sum(0)))
        hit |= np.degrees(np.arccos(np.clip(c @ u, -1, 1))) < rad_deg
    return set(idx.ast_rows[hit])


@pytest.mark.parametrize("kind", ["one", "months", "hours"])
def test_candidates_complete(kind):
    """Every object inside the field at an exact time is a candidate, for single times, visits six months
    apart and ~60 times across a 20-day visit (the coarse cut's grouping and bend margin); comets always."""
    idx = _synthetic_index()
    rng = np.random.default_rng({"one": 5, "months": 6, "hours": 7}[kind])
    n_hits = 0
    for _ in range(25):
        k = int(rng.integers(len(idx.ast_rows)))
        t = float(rng.uniform(60800, 61100))
        # centre the field near a real object's position, so fields are not empty
        jd = float(K.mjd_to_jd_tdb(t))
        geo = idx.kep.positions(jd, np.array([k])).astype(float)[:, 0] - K.earth_position(jd)
        u = K.ecl_to_eq(geo / np.linalg.norm(geo))
        ra, dec = float(np.degrees(np.arctan2(u[1], u[0])) % 360), float(np.degrees(np.arcsin(u[2])))
        mjds = {"one": [t], "months": [t, t + 182.6, t + 365.2],
                "hours": sorted(t + rng.uniform(0, 20, 60))}[kind]
        rad = float(rng.choice([5, 15, 60])) / 60.0
        cand = set(K.candidates(idx, ra, dec, rad, mjds))
        truth = _truth_rows(idx, ra, dec, rad, mjds)
        assert truth <= cand, f"missed {sorted(truth - cand)[:5]}"
        assert set(idx.comet_rows) <= cand
        assert len(cand) < 0.2 * len(idx)  # and it is a real cut
        n_hits += len(truth)
    assert n_hits >= 25


# ---------------------------------------------------------------- with kete

def test_build_and_known_near_match_kete():
    kete = pytest.importorskip("kete")
    data = K.build_index(ASTEROIDS, COMETS, snapshot="test", mjd_lo=61000.0, mjd_hi=61060.0, step_days=30.0,
                         neo_step_days=10.0, log=lambda *_: None)
    idx = K.KnownIndex(data)
    assert idx.name(0) == "Ceres" and int(idx.comet.sum()) == 1
    nan_h = K.build_index({**ASTEROIDS, "h_mag": [3.34, 3.25, 10.39, np.nan]}, None, mjd_lo=61000.0,
                          mjd_hi=61030.0, step_days=30.0, neo_step_days=0, log=lambda *_: None)
    assert len(nan_h["q"]) == 4  # unknown H: kept, never cut by V
    assert "(443104)" not in [idx.name(k) for k in range(len(idx))]  # H 24.2: never brighter than V 21.5
    assert float(np.nanmax(data["err_deg"])) < 0.5  # main belt and Eros: two-body ≈ n-body
    mjd = 61030.0
    jd = kete.Time.from_mjd(mjd, scaling="utc").jd
    st = kete.propagate_n_body(K.kete_states(kete, idx, np.array([0])), jd)[0]
    earth = kete.spice.get_state("Earth", jd)
    vec = (st.pos - earth.pos).as_equatorial
    out = K.known_near(vec.ra, vec.dec, 0.2, [mjd], index=idx, epoch_ids=["e1"])
    ceres = [o for o in out if o["name"] == "Ceres"]
    assert len(ceres) == 1 and ceres[0]["epoch_id"] == "e1" and ceres[0]["source"] == "mpc"
    assert set(ceres[0]) == {"name", "epoch_id", "ra", "dec", "v", "source"}
    # light time moves it by well under a pixel against the geometric position
    assert K._sep_deg(ceres[0]["ra"], ceres[0]["dec"], vec.ra, vec.dec) * 3600 < 60
    assert 6.0 < ceres[0]["v"] < 10.0
    assert K.known_near(vec.ra + 5, vec.dec, 0.2, [mjd], index=idx) == []
    with pytest.raises(ValueError):
        K.known_near(vec.ra, vec.dec, 0.2, [61100.0], index=idx)
