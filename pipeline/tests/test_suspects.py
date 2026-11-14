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
    assert st == {"movers": 5, "cut_known": 1, "cut_flags": 1, "cut_bright_fixed": 1, "cut_source_bit": 0}
    assert sorted(s["cell"]) == [0, 1]
    r = S.rank(s)
    assert list(r["evidence"]) == ["two_exposures", "two_detectors"]
    assert np.isnan(r["nearest_known_arcsec"]).all()


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
