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
                         flux_ujy=100.0, flags=0, cell=k, session=3, visit=1))
    m = pd.DataFrame(rows)
    m.loc[3, "flags"] = 1 << 22  # GHOST
    return m


def test_select_and_rank():
    m = movers()
    # a known asteroid 10″ from mover 4 at the mover's time
    ra, dec = offset_radec(m.loc[4, "ra"], m.loc[4, "dec"], 10.0, 0.0)
    pred = pd.DataFrame([dict(name="X", ra=ra, dec=dec, mjd=61000.3, dra_arcsec_h=0.0, ddec_arcsec_h=0.0, v=18.0)])
    fixed = pd.DataFrame([dict(ra=m.loc[2, "ra"], dec=m.loc[2, "dec"] + 10 / 3600, snr=500.0)])
    s, st = S.select(m, fixed, pred)
    assert st == {"movers": 5, "cut_known": 1, "cut_flags": 1, "cut_bright_fixed": 1}
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
