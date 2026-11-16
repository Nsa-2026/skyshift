import numpy as np
import pandas as pd

from skyshift_pipeline import config as C
from skyshift_pipeline import suspects as S
from skyshift_pipeline.sky import offset_radec


def movers():
    rows = []
    for k, (ev, snr) in enumerate([("two_detectors", 50.0), ("two_exposures", 9.0), ("two_exposures", 20.0),
                                   ("two_exposures", 30.0), ("two_exposures", 40.0)]):
        rows.append(dict(ra=121.0 + 0.1 * k, dec=15.0, mjd=61000.3, det=1, evidence=ev, n_dots=2, snr=snr,
                         flux_ujy=100.0, flags=0, flags_peak_all=0, cell=k, session=3, visit=1))
    m = pd.DataFrame(rows)
    m.loc[3, "flags"] = 1 << 22  # GHOST
    return m


def test_source_bit_at_peak_is_a_warning():
    """NASA's SOURCE bit at every dot's peak no longer drops the mover: catalogue_star, source_mask_all_dots."""
    m = movers()
    m.loc[1, "flags_peak_all"] = 1 << 21
    s, st, w = S.select(m, pd.DataFrame(columns=["ra", "dec", "snr"]), pd.DataFrame(columns=["ra", "dec", "mjd", "session"]))
    assert len(s) == 5 and st["warn_source_bit"] == 1
    i = list(s["cell"]).index(1)
    assert "catalogue_star" in w.codes[i] and w.values[i]["catalogue_star"] == {"source_mask_all_dots": 1}


def test_select_removes_only_certain_known_and_warns_the_rest():
    m = movers()
    # a known asteroid 5″ from mover 4 at the mover's time (certain: our motion isn't measured), another 20″ from
    # mover 0 (known_nearby); a bright fixed source 10″ from mover 2; mover 3 has GHOST in its 3×3
    ra, dec = offset_radec(m.loc[4, "ra"], m.loc[4, "dec"], 5.0, 0.0)
    rb, db = offset_radec(m.loc[0, "ra"], m.loc[0, "dec"], 0.0, 20.0)
    pred = pd.DataFrame([dict(name="X", ra=ra, dec=dec, mjd=61000.3, dra_arcsec_h=30.0, ddec_arcsec_h=0.0, v=18.0, session=3),
                         dict(name="Y", ra=rb, dec=db, mjd=61000.3, dra_arcsec_h=0.0, ddec_arcsec_h=-40.0, v=19.5, session=3)])
    fixed = pd.DataFrame([dict(ra=m.loc[2, "ra"], dec=m.loc[2, "dec"] + 10 / 3600, snr=500.0, flux_ujy=50000.0)])
    s, st, w = S.select(m, fixed, pred)
    assert st == {"movers": 5, "cut_known": 1, "cut_known_certain_only": 0, "cut_planet": 0, "cut_hot_pixel": 0,
                  "warn_known_nearby": 1, "warn_flag_bloom": 0,
                  "warn_flag_halo": 0, "warn_flag_ghost": 1, "warn_flag_crosstalk": 0, "warn_bright_star": 1,
                  "warn_source_bit": 0}
    assert sorted(s["cell"]) == [0, 1, 2, 3]
    codes = dict(zip(s["cell"], w.codes))
    vals = dict(zip(s["cell"], w.values))
    assert codes == {0: ["known_nearby"], 1: [], 2: ["bright_star"], 3: ["flag_ghost"]}
    assert vals[0]["known_nearby"] == {"sep_arcsec": 20.0, "known_rate_arcsec_h": 40.0, "known_pa_deg": 180, "known_v_mag": 19.5}
    assert vals[2]["bright_star"] == {"sep_arcsec": 10.0, "star_snr": 500, "star_ab_mag": 12.2, "star_flux_ratio": 500.0}
    assert vals[3]["flag_ghost"] == {"dots_peak": 0, "n_dots": 2}
    assert list(s.loc[s["cell"] == 0, "nearest_known_name"]) == ["Y"]
    # the bright star at 10″ (within 15″) is also a reason; no bloom/halo at every dot's peak
    assert dict(zip(s["cell"], w.gates["bright_star"])) == {0: False, 1: False, 2: True, 3: False}
    assert not w.gates["bloom"].any() and not w.gates["halo"].any()
    r = S.rank(s)
    assert list(r["evidence"]) == ["two_exposures"] * 3 + ["two_detectors"]
    cols = S.warn_columns(w)
    import json
    assert cols["warn_codes"][0] == ["known_nearby"] and json.loads(cols["warn_values"][2])["bright_star"]["sep_arcsec"] == 10.0
    assert cols["warn_values"][1] == "{}"


def test_known_certain_place_alone_within_5_and_motion_beyond():
    """Within 5″ the place alone is certain, whatever our noisy motion says; 5–30″ only when our measured motion
    (motion_snr >= 3, or a line through >= 3 exposures) has the body's rate (50% or 3σ) and direction (45° or 3σ)."""
    m = pd.DataFrame({"rate_arcsec_h": [np.nan, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0],
                      "pa_deg": [np.nan, 270.0, 90.0, 100.0, 90.0, 90.0, 90.0],
                      "motion_snr": [np.nan, 10.0, np.nan, 10.0, 10.0, 1.0, 10.0], "n_exposures": [2, 2, 3, 2, 2, 2, 2]})
    near = pd.DataFrame({"sep": [8.0, 3.0, 20.0, 12.0, 12.0, 12.0, 31.0], "rate": [30.0, 60.0, 40.0, 33.0, 70.0, 30.0, 30.0],
                         "pa": [0.0, 90.0, 120.0, 130.0, 90.0, 90.0, 90.0]})
    # 0: 8″, no motion of ours -> known_nearby; 1: 3″ -> certain although rate and direction disagree; 2: 20″, a line
    # through 3 exposures at the body's rate (33% off) and direction (30° off) -> certain; 3: 12″, 30° off -> certain;
    # 4: rate 133% off -> not; 5: motion not measured -> not; 6: beyond 30″ -> not
    assert list(S.known_certain(m, near)) == [False, True, True, True, False, False, False]


def test_hot_pixel_is_removed_only_when_the_sky_moved():
    """Dots of two exposures on one pixel (±1 px) while the sky under them moved >= 3 px: a pixel fault, removed. The
    same pixel with the sky still (a slow mover), one exposure, or dots 2 px apart stay."""
    m = movers()
    dt = 2.1 / 1440  # two exposures 2.1 min apart
    m["rate_arcsec_h"] = [600.0, 600.0, 2.0, 600.0, 600.0]  # 21″ (3.4 px) of sky in 2.1 min; mover 2: 0.07″
    m["dot_obs"] = [["a", "b"]] * 4 + [["a", "a"]]
    m["dot_det"] = [[2, 2]] * 5
    m["dot_x"] = [[100.0, 100.6], [100.0, 102.5], [100.0, 100.4], [100.0, 100.0], [100.0, 100.0]]
    m["dot_y"] = [[50.0, 50.8], [50.0, 50.0], [50.0, 50.2], [50.0, 50.0], [50.0, 50.0]]
    m["dot_mjd"] = [[61000.3, 61000.3 + dt]] * 5
    for c in ("dot_flux", "dot_snr", "dot_size", "dot_chi2"):
        m[c] = [[1.0, 1.0]] * 5
    m["dot_fpk"] = [[0, 0]] * 5
    assert list(S.hot_pixel_certain(m)) == [True, False, False, True, False]
    s, st, _ = S.select(m, pd.DataFrame(columns=["ra", "dec", "snr"]), pd.DataFrame(columns=["ra", "dec", "mjd", "session"]))
    assert st["cut_hot_pixel"] == 2 and sorted(s["cell"]) == [1, 2, 4]


def test_flag_warnings_count_dots_at_peak():
    m = movers()
    m["dot_fpk"] = [np.array([0, 0], np.int32)] * len(m)
    m.loc[0, "flags"] = (1 << 26) | (1 << 28)
    m.at[0, "dot_fpk"] = np.array([1 << 26, 0], np.int32)
    m.loc[1, "flags"] = 1 << 20
    w = S.Warnings(len(m))
    out = S.flag_warnings(m, w)
    assert out == {"flag_bloom": 1, "flag_halo": 1, "flag_ghost": 1, "flag_crosstalk": 1}
    assert w.codes[0] == ["flag_bloom", "flag_halo"] and w.values[0]["flag_bloom"] == {"dots_peak": 1, "n_dots": 2}
    assert w.values[0]["flag_halo"] == {"dots_peak": 0, "n_dots": 2} and w.codes[1] == ["flag_crosstalk"]
    assert w.codes[3] == ["flag_ghost"]
    # a reason only where the bit is at every dot's peak (flags_peak_all): BLOOM on mover 2's two peaks
    m.loc[2, "flags"] = 1 << 26
    m.loc[2, "flags_peak_all"] = 1 << 26
    w = S.Warnings(len(m))
    S.flag_warnings(m, w)
    assert list(w.gates["bloom"]) == [False, False, True, False, False] and not w.gates["halo"].any()
    assert list(w.take(np.array([False, False, True, True, False])).gates["bloom"]) == [True, False]


def test_catalogue_warnings_carry_separations_and_magnitudes():
    cats = {"gaia": pd.DataFrame({"sep": [2.04, np.inf, 7.0], "g_mag": [18.73, np.nan, 15.0]}),
            "allwise": pd.DataFrame({"sep": [np.inf, 5.5, np.inf], "w1_mag": [np.nan, 14.2, np.nan], "w2_mag": [np.nan, 13.9, np.nan]})}
    w = S.Warnings(3)
    out = S.catalogue_warnings(cats, np.array([1000.0, 250.0, 100.0]), w)
    assert out == {"gaia": 1, "allwise": 1}
    assert w.values[0]["catalogue_star"] == {"sep_arcsec": 2.0, "gaia_sep_arcsec": 2.0, "gaia_g_mag": 18.73, "mover_ab_mag": 16.4,
                                             "fainter_mag": 2.3}
    # W1 14.2 = AB 16.9, W2 13.9 = AB 17.24: the brighter, W1, is 1.0 mag brighter than the mover (AB 17.91)
    assert w.values[1]["catalogue_star"] == {"sep_arcsec": 5.5, "allwise_sep_arcsec": 5.5, "allwise_w1_mag": 14.2,
                                             "allwise_w2_mag": 13.9, "mover_ab_mag": 17.91, "fainter_mag": -1.0}
    assert w.codes[2] == []  # 7″: beyond the radius
    # reasons: only a source at most 2 mag fainter than the mover (the Gaia star 2.3 mag fainter is a chance neighbour)
    r = S.catalogue_reasons(cats, np.array([1000.0, 250.0, 100.0]))
    assert list(r["gaia"]) == [False, False, False] and list(r["allwise"]) == [False, True, False]
    # without a magnitude a source within the radius stays a reason
    r = S.catalogue_reasons({"gaia": pd.DataFrame({"sep": [2.0]})}, np.array([1000.0]))
    assert list(r["gaia"]) == [True]


def test_planet_and_moon_movers_are_known():
    """Movers of the 15 Nov sky run that MPC orbits don't explain: Iapetus 4.6′ from Saturn (ecl35, grade A), Uranus
    itself (ecl05), Himalia 31′ from Jupiter (ecl11); and a hunt mover far from every planet."""
    rows = [(356.404831, -4.126031, 61022.064174), (58.933291, 20.15587, 60910.449783),
            (115.551213, 20.945184, 60967.981055), (122.250475, 20.897236, 61142.911622)]
    m = pd.DataFrame([dict(ra=ra, dec=dec, mjd=mjd, det=2, evidence="two_exposures", n_dots=2, snr=50.0, flux_ujy=900.0,
                           flags=0, flags_peak_all=0, cell=k, session=k, visit=1) for k, (ra, dec, mjd) in enumerate(rows)])
    assert list(S.near_planet_names(m)) == ["Saturn", "Uranus", "Jupiter", ""]
    s, st, _ = S.select(m, pd.DataFrame(columns=["ra", "dec", "snr"]), pd.DataFrame(columns=["ra", "dec", "mjd", "session"]))
    assert st["cut_planet"] == 3 and st["cut_known"] == 0
    assert list(s["cell"]) == [3]


def test_json_shape():
    s = S.rank(movers()).assign(nearest_known_arcsec=np.nan)
    s.insert(0, "id", [f"pilot-{c}" for c in s["cell"]])
    j = S.to_json(s, C.ZONES["pilot"], "DR1")
    assert set(j) == {"generated", "data_release", "zone", "suspects"}
    keys = {"id", "ra", "dec", "mjd", "clock", "det", "evidence", "n_dots", "snr", "flux_ujy", "cell", "nearest_known_arcsec"}
    assert all(set(x) == keys for x in j["suspects"])
    assert j["suspects"][0]["clock"] == "hours" and j["suspects"][0]["nearest_known_arcsec"] is None


def test_refcat_used_instead_of_services(tmp_path, monkeypatch):
    from skyshift_pipeline.store import Store
    z = C.ZONES["pilot"]
    store = Store(str(tmp_path))
    m = movers()
    # Gaia star 2″ from mover 0 at J2016 with no motion; CatWISE source 3″ from mover 1; a fast Gaia star that has
    # moved onto mover 2 by the movers' time (1″/yr in Dec over ~13.8 yr)
    g0 = offset_radec(m.loc[0, "ra"], m.loc[0, "dec"], 2.0, 0.0)
    c1 = offset_radec(m.loc[1, "ra"], m.loc[1, "dec"], 0.0, 3.0)
    dt_yr = (61000.3 - 57388.5) / 365.25
    g2 = (m.loc[2, "ra"], m.loc[2, "dec"] - 1000.0 * dt_yr / 3.6e6)
    ref = pd.DataFrame([
        dict(cat="gaia", ra=g0[0], dec=g0[1], epoch_mjd=57388.5, pmra=0.0, pmdec=0.0),
        dict(cat="catwise", ra=c1[0], dec=c1[1], epoch_mjd=57170.0, pmra=np.nan, pmdec=np.nan),
        dict(cat="gaia", ra=g2[0], dec=g2[1], epoch_mjd=57388.5, pmra=0.0, pmdec=1000.0),
    ])
    store.write_parquet("refcat/pilot.parquet", ref)

    def boom(*a, **k):
        raise AssertionError("services must not be asked when refcat exists")

    monkeypatch.setattr(S, "gaia_seps", boom)
    monkeypatch.setattr(S, "catwise_seps", boom)
    g, w = S.refcat_near(store, z, m["ra"].to_numpy(), m["dec"].to_numpy(), m["mjd"].to_numpy())
    assert list(g) == [True, False, True, False, False] and list(w) == [False, True, False, False, False]
    monkeypatch.setattr(C, "VET_ALLWISE", False)
    out, info = S.catalogue_matches(store, z, m["ra"].to_numpy(), m["dec"].to_numpy(), m["mjd"].to_numpy(), S.load_refcat(store, z))
    hits = S.catalogue_hits(out)
    assert list(hits["gaia"]) == list(g) and list(hits["catwise"]) == list(w) and info["wise"] == "catwise"
    assert S.refcat_near(Store(str(tmp_path / "empty")), z, m["ra"].to_numpy(), m["dec"].to_numpy(), m["mjd"].to_numpy()) is None


def test_gaia_only_refcat_has_no_catwise_answer(tmp_path):
    from skyshift_pipeline.store import Store
    z = C.ZONES["pilot"]
    store = Store(str(tmp_path))
    m = movers()
    store.write_parquet("refcat/pilot.parquet", pd.DataFrame([dict(cat="gaia", ra=m.loc[0, "ra"], dec=m.loc[0, "dec"],
                                                                   epoch_mjd=57388.5, pmra=0.0, pmdec=0.0)]))
    g, w = S.refcat_near(store, z, m["ra"].to_numpy(), m["dec"].to_numpy(), m["mjd"].to_numpy())
    assert list(g) == [True, False, False, False, False] and w is None


def test_allwise_partitions_cover_the_veto_circle():
    from skyshift_pipeline.forced_sky import pixel_of
    # a point 3″ inside a partition boundary: its 6″ circle reaches the neighbour, which must be read too
    ra0, dec0 = 122.0, 16.0
    p0 = int(pixel_of([ra0], [dec0], C.ALLWISE_NSIDE)[0])
    for step in np.linspace(0, 7200, 2401):  # walk east 1° in 3″ steps until the pixel changes
        r, d = offset_radec(ra0, dec0, step, 0.0)
        if int(pixel_of([r], [d], C.ALLWISE_NSIDE)[0]) != p0:
            break
    r_in, d_in = offset_radec(ra0, dec0, step - 3.0, 0.0)
    pix = S.allwise_pixels(np.array([r_in]), np.array([d_in]), 6.0)
    assert p0 in pix and int(pixel_of([r], [d], C.ALLWISE_NSIDE)[0]) in pix
    assert S.allwise_key(4620) == f"{C.ALLWISE_S3_PREFIX}/healpix_k0=4/healpix_k5=4620/part0.snappy.parquet"


GAIA_ONLY = pd.DataFrame([dict(cat="gaia", ra=200.0, dec=-40.0, epoch_mjd=57388.5, pmra=0.0, pmdec=0.0)])


def test_catalogue_matches_use_allwise_for_a_gaia_only_refcat_and_cache_partitions(tmp_path, monkeypatch):
    from skyshift_pipeline.forced_sky import pixel_of
    from skyshift_pipeline.store import Store
    z = C.ZONES["pilot"]
    store = Store(str(tmp_path))
    m = movers()
    ra, dec, mjd = m["ra"].to_numpy(), m["dec"].to_numpy(), m["mjd"].to_numpy()
    a1 = offset_radec(ra[1], dec[1], 0.0, 4.0)   # 4″ from mover 1: a match
    a3 = offset_radec(ra[3], dec[3], 8.0, 0.0)   # 8″ from mover 3: not
    cat = pd.DataFrame({"ra": [a1[0], a3[0]], "dec": [a1[1], a3[1]]})
    cpix = pixel_of(cat["ra"], cat["dec"], C.ALLWISE_NSIDE)
    calls = []

    def reader(pix):
        calls.append(pix)
        return cat[cpix == pix].assign(w1mpro=12.0)

    monkeypatch.setattr(C, "SUSPECTS_ALLWISE", "auto")
    out, info = S.catalogue_matches(store, z, ra, dec, mjd, GAIA_ONLY, allwise_reader=reader)
    hit = S.catalogue_hits(out)["allwise"]
    assert list(hit) == [False, True, False, False, False] and info["wise"] == "allwise"
    assert info["catwise_source"].startswith("AllWISE") and out["allwise"]["w1_mag"][1] == 12.0
    st = info["allwise"]
    assert st["partitions"] == len(calls) == len(S.allwise_pixels(ra, dec, C.ALLWISE_ARCSEC)) and st["rows"] == 2
    # cached at the store root: a rerun reads nothing from S3
    out2, _ = S.catalogue_matches(store, z, ra, dec, mjd, GAIA_ONLY, allwise_reader=lambda p: (_ for _ in ()).throw(AssertionError()))
    assert list(S.catalogue_hits(out2)["allwise"]) == list(hit)
    # CatWISE rows in the refcat are the WISE catalogue (AllWISE a second one unless VET_ALLWISE is off) ...
    monkeypatch.setattr(C, "VET_ALLWISE", False)
    ref = pd.concat([GAIA_ONLY, GAIA_ONLY.assign(cat="catwise")], ignore_index=True)
    out3, info3 = S.catalogue_matches(store, z, ra, dec, mjd, ref, allwise_reader=reader)
    assert info3["wise"] == "catwise" and sorted(out3) == ["catwise", "gaia"]
    # ... and AllWISE off leaves a Gaia-only refcat without a WISE catalogue
    monkeypatch.setattr(C, "SUSPECTS_ALLWISE", "off")
    out4, info4 = S.catalogue_matches(store, z, ra, dec, mjd, GAIA_ONLY, allwise_reader=reader)
    assert info4["wise"] == "" and info4["catwise_source"] == "" and sorted(out4) == ["gaia"]


def test_allwise_failure_keeps_suspects_unchecked(tmp_path, monkeypatch):
    from skyshift_pipeline.store import Store
    monkeypatch.setattr(C, "SUSPECTS_ALLWISE", "auto")
    monkeypatch.setattr(C, "NET_RETRIES", 1)
    m = movers()

    def boom(pix):
        raise OSError("no network")

    out, info = S.catalogue_matches(Store(str(tmp_path)), C.ZONES["pilot"], m["ra"].to_numpy(), m["dec"].to_numpy(),
                                    m["mjd"].to_numpy(), GAIA_ONLY, allwise_reader=boom)
    assert info["wise"] == "" and "allwise" not in out and info["allwise"]["source"] == "unreachable"
