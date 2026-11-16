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


def test_source_bit_at_peak_drops():
    m = movers()
    m.loc[1, "flags_peak_all"] = 1 << 21
    s, st = S.select(m, pd.DataFrame(columns=["ra", "dec", "snr"]), pd.DataFrame(columns=["ra", "dec", "mjd", "session"]))
    assert st["cut_source_bit"] == 1 and 1 not in set(s["cell"])


def test_select_and_rank():
    m = movers()
    # a known asteroid 10″ from mover 4 at the mover's time
    ra, dec = offset_radec(m.loc[4, "ra"], m.loc[4, "dec"], 10.0, 0.0)
    pred = pd.DataFrame([dict(name="X", ra=ra, dec=dec, mjd=61000.3, dra_arcsec_h=0.0, ddec_arcsec_h=0.0, v=18.0, session=3)])
    fixed = pd.DataFrame([dict(ra=m.loc[2, "ra"], dec=m.loc[2, "dec"] + 10 / 3600, snr=500.0)])
    s, st = S.select(m, fixed, pred)
    assert st == {"movers": 5, "cut_known": 1, "cut_known_certain_only": 0, "cut_planet": 0, "cut_flags": 1, "cut_bright_fixed": 1,
                  "cut_source_bit": 0}
    assert sorted(s["cell"]) == [0, 1]
    r = S.rank(s)
    assert list(r["evidence"]) == ["two_exposures", "two_detectors"]
    assert np.isnan(r["nearest_known_arcsec"]).all()


def test_planet_and_moon_movers_are_known():
    """Movers of the 15 Nov sky run that MPC orbits don't explain: Iapetus 4.6′ from Saturn (ecl35, grade A), Uranus
    itself (ecl05), Himalia 31′ from Jupiter (ecl11); and a hunt mover far from every planet."""
    rows = [(356.404831, -4.126031, 61022.064174), (58.933291, 20.15587, 60910.449783),
            (115.551213, 20.945184, 60967.981055), (122.250475, 20.897236, 61142.911622)]
    m = pd.DataFrame([dict(ra=ra, dec=dec, mjd=mjd, det=2, evidence="two_exposures", n_dots=2, snr=50.0, flux_ujy=900.0,
                           flags=0, flags_peak_all=0, cell=k, session=k, visit=1) for k, (ra, dec, mjd) in enumerate(rows)])
    assert list(S.near_planet_names(m)) == ["Saturn", "Uranus", "Jupiter", ""]
    s, st = S.select(m, pd.DataFrame(columns=["ra", "dec", "snr"]), pd.DataFrame(columns=["ra", "dec", "mjd", "session"]))
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

    monkeypatch.setattr(S, "gaia_near", boom)
    monkeypatch.setattr(S, "catwise_near", boom)
    g, w = S.refcat_near(store, z, m["ra"].to_numpy(), m["dec"].to_numpy(), m["mjd"].to_numpy())
    assert list(g) == [True, False, True, False, False] and list(w) == [False, True, False, False, False]
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


def test_wise_veto_uses_allwise_for_a_gaia_only_refcat_and_caches_partitions(tmp_path, monkeypatch):
    from skyshift_pipeline.forced_sky import pixel_of
    from skyshift_pipeline.store import Store
    z = C.ZONES["pilot"]
    store = Store(str(tmp_path))
    m = movers()
    ra, dec = m["ra"].to_numpy(), m["dec"].to_numpy()
    a1 = offset_radec(ra[1], dec[1], 0.0, 4.0)   # 4″ from mover 1: vetoed
    a3 = offset_radec(ra[3], dec[3], 8.0, 0.0)   # 8″ from mover 3: not
    cat = pd.DataFrame({"ra": [a1[0], a3[0]], "dec": [a1[1], a3[1]]})
    cpix = pixel_of(cat["ra"], cat["dec"], C.ALLWISE_NSIDE)
    calls = []

    def reader(pix):
        calls.append(pix)
        return cat[cpix == pix].assign(w1mpro=12.0)

    monkeypatch.setattr(C, "SUSPECTS_ALLWISE", "auto")
    hit, name, st = S.wise_veto(store, z, None, True, ra, dec, reader=reader)
    assert list(hit) == [False, True, False, False, False] and name.startswith("AllWISE")
    assert st["partitions"] == len(calls) == len(S.allwise_pixels(ra, dec, C.ALLWISE_ARCSEC)) and st["rows"] == 2
    # cached at the store root: a rerun reads nothing from S3
    hit2, _, _ = S.wise_veto(store, z, None, True, ra, dec, reader=lambda p: (_ for _ in ()).throw(AssertionError()))
    assert list(hit2) == list(hit)
    # CatWISE rows in the refcat win; AllWISE off leaves a Gaia-only refcat unchecked
    w = np.array([True, False, False, False, False])
    assert S.wise_veto(store, z, w, True, ra, dec, reader=reader)[:2] == (w, "refcat/pilot.parquet (CatWISE2020)")
    monkeypatch.setattr(C, "SUSPECTS_ALLWISE", "off")
    hit3, name3, _ = S.wise_veto(store, z, None, True, ra, dec, reader=reader)
    assert name3 == "" and not hit3.any()


def test_allwise_failure_keeps_suspects_unchecked(tmp_path, monkeypatch):
    from skyshift_pipeline.store import Store
    monkeypatch.setattr(C, "SUSPECTS_ALLWISE", "auto")
    monkeypatch.setattr(C, "NET_RETRIES", 1)
    m = movers()

    def boom(pix):
        raise OSError("no network")

    hit, name, st = S.wise_veto(Store(str(tmp_path)), C.ZONES["pilot"], None, True, m["ra"].to_numpy(), m["dec"].to_numpy(), reader=boom)
    assert name == "" and not hit.any()
