"""Shape review fixes of 16 Nov (vetting round 1): AllWISE and faint Gaia neighbours (IRSA) make the reason
catalogue_neighbour, a visit's offset error has a floor (companion_move_sigma 6–10 on fixed companions), and two
catalogued objects closer than the fit's search block the split (hunt-sh-64104007282900's mirrored companion)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from astropy.table import Table
from skyshift_core import tangent_deproject

from skyshift_pipeline import shape as S
from skyshift_pipeline import shape_fit as SF

from test_shape_fit import gaussian_epsf, make_stamps


def at(ra0, dec0, sep, pa):
    """(ra, dec) sep ″ from (ra0, dec0) at position angle pa (deg east of north)."""
    e, n = sep * math.sin(math.radians(pa)), sep * math.cos(math.radians(pa))
    r, d = tangent_deproject(ra0, dec0, e, n)
    return float(r), float(d)


def entries(ra0, dec0, rows):
    """refcat rows: (cat, sep″, pa°, mag)."""
    out = []
    for k, (cat, sep, pa, mag) in enumerate(rows):
        r, d = at(ra0, dec0, sep, pa)
        out.append({"ref_id": f"{cat}:{k}", "cat": cat, "ra": r, "dec": d, "epoch_mjd": 57388.5, "pmra": np.nan,
                    "pmdec": np.nan, "g_mag": mag if cat == "gaia" else np.nan,
                    "w1_mag": mag if cat in ("allwise", "catwise") else np.nan, "k_mag": mag if cat == "2mass" else np.nan})
    return pd.DataFrame(out)


def test_two_catalogued_objects_closer_than_the_search_block_the_split():
    ra0, dec0, mjd = 126.82206, 16.84069, 61000.0
    # hunt-sh-64104007282900: Gaia G 18.93 2.27″ PA 122 and G 20.34 0.39″ PA 325 (2.6″ apart, 1.4 mag)
    ref = entries(ra0, dec0, [("gaia", 2.27, 122, 18.93), ("gaia", 0.39, 325, 20.34), ("allwise", 0.63, 324, 13.18),
                              ("allwise", 5.44, 135, 14.70)])
    assert SF.close_second(ref, ra0, dec0, mjd) == "Gaia G 18.9 and G 20.3 2.6″ apart"
    # without the faint star (refcat's G < 20) nothing blocks: the AllWISE pair is 6″ apart
    assert SF.close_second(ref[ref["g_mag"].fillna(0) < 20], ra0, dec0, mjd) == ""
    # hunt-sh-64117007464500: a G 21.1 1.4″ from the G 17.5 star: 3.6 mag fainter, < 4% of the light, no mirror
    ref2 = entries(ra0, dec0, [("gaia", 1.02, 210, 17.54), ("gaia", 0.45, 60, 21.10), ("allwise", 1.55, 270, 12.99),
                               ("allwise", 3.48, 84, 13.59)])
    assert SF.close_second(ref2, ra0, dec0, mjd) == ""
    # hunt-sh-65949006863900: 2MASS resolves the pair 2.7″ apart (AllWISE's 4.6″ is outside the blind zone)
    ref3 = entries(ra0, dec0, [("allwise", 2.37, 183, 13.44), ("allwise", 2.19, 9, 13.438), ("2mass", 0.77, 199, 13.675),
                               ("2mass", 1.96, 7, 13.366)])
    assert SF.close_second(ref3, ra0, dec0, mjd) == "2MASS K 13.4 and K 13.7 2.7″ apart"
    assert SF.close_second(ref3[ref3["cat"] == "allwise"], ra0, dec0, mjd) == ""


@pytest.fixture(scope="module")
def epsf():
    return gaussian_epsf()


def test_a_blocked_fit_claims_no_companion(epsf):
    st = make_stamps(epsf, {1: (3.0, -10.0), 2: (3.0, -10.0)}, {1: 0.25, 4: 0.5})
    f = SF.fit_source(st, {1: epsf, 4: epsf}, np.zeros((0, 2)), 7, visits=False)
    assert f.split
    f.blocked = "Gaia G 18.9 and G 20.3 2.6″ apart"
    assert not f.split
    row = SF.result_row(f)
    assert row["companion_split"] is False and row["companion_blocked"] == f.blocked
    w = SF._why_with("Stretched.", row)
    assert w == ("Stretched. Fit: the catalogues list two objects here (Gaia G 18.9 and G 20.3 2.6″ apart), closer "
                 "together than the fit can separate (4.5″), so it claims no companion.")
    assert "companion_blocked" in SF.empty_columns(pd.DataFrame({"source_id": [1]}))


def test_a_fixed_companion_half_an_arcsec_off_between_visits_does_not_move(epsf):
    """Hunt's fixed, catalogued companions moved 0.30–0.67″ between visits against curvature errors of 0.03–0.06″:
    move_sigma 6–10 with moves false. The floor makes it 1–2σ, and a real mover still moves."""
    st = make_stamps(epsf, {1: (3.0, -10.0), 2: (3.4, -10.3)}, {1: 0.4, 4: 0.4}, seed=5, n_per_det=20, noise=0.01)
    f = SF.fit_source(st, {1: epsf, 4: epsf}, np.zeros((0, 2)), 9)
    errs = [r["err"] for r in f.visits]
    assert all(e >= SF.FIT_VISIT_FLOOR_ARCSEC for e in errs) and all(e < 0.3 for e in errs)
    assert f.move_arcsec == pytest.approx(0.5, abs=0.15)
    assert f.move_sigma < SF.FIT_MOVE_SIGMA and f.moves is False
    assert f.move_sigma == pytest.approx(f.move_arcsec / math.hypot(*errs))
    # the hunt's values: 0.43″ at 10.4σ (curvature errors) → about 1.2σ
    s0 = 0.4272 / 10.384
    assert 0.4272 / math.sqrt(s0 ** 2 + 2 * SF.FIT_VISIT_FLOOR_ARCSEC ** 2) == pytest.approx(1.2, abs=0.05)


def fake_irsa(answers, calls=None):
    """answers: {position row: [(cat, sep″, pa°, mag)]}; answers both of irsa_catalogues' queries."""
    def tap(adql, uploads):
        u = uploads["u"]
        cat = "allwise" if "allwise_p3as_psd" in adql else "gaia"
        if calls is not None:
            calls.append((cat, len(u)))
        rows = [(i, *at(float(u["ra"][i]), float(u["dec"][i]), sep, pa), mag, k)
                for i, lst in answers.items() for k, (c, sep, pa, mag) in enumerate(lst) if c == cat]
        if cat == "allwise":
            return Table({"i": [r[0] for r in rows], "designation": [f"J{r[0]}_{r[4]}" for r in rows],
                          "ra": [r[1] for r in rows], "dec": [r[2] for r in rows], "w1mpro": [r[3] for r in rows],
                          "w2mpro": [r[3] - 0.1 for r in rows], "w3mpro": [12.0] * len(rows), "w3snr": [2.0] * len(rows),
                          "ext_flg": [0] * len(rows)})
        return Table({"i": [r[0] for r in rows], "source_id": [1000 + 10 * r[0] + r[4] for r in rows],
                      "ra": [r[1] for r in rows], "dec": [r[2] for r in rows], "pmra": [np.nan] * len(rows),
                      "pmdec": [np.nan] * len(rows), "phot_g_mean_mag": [r[3] for r in rows]})
    return tap


def shape_rows():
    return pd.DataFrame({"source_id": np.array([1, 2, 3], np.int64), "ra": [126.82206, 129.69437, 123.29886],
                         "dec": [16.84069, 16.62601, 23.52474], "mjd": [61000.0] * 3, "sig": [27.0, 36.5, 27.5],
                         "excess": [0.05] * 3, "pa_deg": [135.8, 1.8, 176.3], "n_dots": [200] * 3, "steady_pa": [True] * 3,
                         "one_visit": [False] * 3, "gaia_g": [18.93, 16.91, 16.48], "ruwe": [1.0] * 3,
                         "catwise_match": [True] * 3, "class": ["star"] * 3})


def test_allwise_and_faint_gaia_neighbours_make_the_reason_catalogue_neighbour():
    calls = []
    tap = fake_irsa({0: [("allwise", 0.63, 324, 13.18), ("allwise", 5.44, 135, 14.70), ("gaia", 2.27, 122, 18.93),
                         ("gaia", 0.39, 325, 20.34)],
                     1: [("allwise", 1.16, 356, 13.10), ("allwise", 10.2, 180, 14.73), ("gaia", 1.05, 0, 16.91)],
                     2: [("allwise", 0.17, 28, 12.82), ("allwise", 16.5, 90, 15.0), ("gaia", 0.14, 293, 16.48)]}, calls)
    c = shape_rows()
    extra = S.irsa_catalogues(c["ra"], c["dec"], tap=tap)
    assert sorted(calls) == [("allwise", 3), ("gaia", 3)] and set(extra["cat"]) == {"allwise", "gaia"}
    nb = S.catalogue_neighbours(c, extra)
    # row 0: the brightest within 6″ is the object's own (G 18.9, W1 13.2); G 20.3 0.4″ and W1 14.7 5.4″ are others
    assert nb["catalogue_neighbour"].tolist()[0] == ("AllWISE W1 14.7 5.4″ south-east, along the stretch; "
                                                     "Gaia G 20.3 0.4″ north-west, along the stretch")
    assert nb["catalogue_neighbours"].tolist() == [2, 1, 0] and nb["catalogue_checked"].all()
    assert nb["catalogue_neighbour_sep_arcsec"].iloc[0] == pytest.approx(0.39, abs=0.01)
    assert nb["catalogue_neighbour"].iloc[1] == "AllWISE W1 14.7 10.2″ south, along the stretch"
    assert nb["catalogue_neighbour"].iloc[2] == ""  # 16.5″: beyond SHAPE_NEIGHBOUR_ARCSEC
    out = S.candidate_table(nb, pd.DataFrame({"source_id": np.array([1, 2, 3], np.int64), "det": 2, "snr": 50.0,
                                              "flux_ujy": 100.0}))
    assert out["reasons"].tolist()[1] == "elongated,steady_pa,catalogue_neighbour"
    assert out["reasons"].tolist()[2] == "elongated,steady_pa,one_catalogue_source"
    base = S.shape_score(out["sig"], out["one_visit"])
    assert np.allclose(out["score"], base * np.array([S.SHAPE_NEIGHBOUR_WEIGHT, S.SHAPE_NEIGHBOUR_WEIGHT, 1.0]))
    assert out["why"].iloc[1] == ("The image is stretched along PA 2° (36.5σ over 200 detections), the same way in the "
                                  "short- and long-wavelength detectors; but the catalogues list a second object here: "
                                  "AllWISE W1 14.7 10.2″ south, along the stretch, which may be all it is.")
    assert out["why"].iloc[2].startswith("Looks like two objects here but the catalogues list one")
    # the fit gets the new entries once (refcat's own Gaia stars are not doubled)
    ref = pd.DataFrame({"ref_id": ["gaia:1012"], "cat": ["gaia"], "ra": [129.69437], "dec": [16.62601]})
    aug = S.with_irsa(ref, extra)
    assert len(aug) == len(extra) and (aug["ref_id"] == "gaia:1012").sum() == 1


def test_irsa_down_keeps_the_old_claim():
    with pytest.raises(ConnectionError):  # conftest: IRSA fails offline
        S.irsa_catalogues([126.8], [16.8])
    nb = S.catalogue_neighbours(shape_rows(), None)
    assert not nb["catalogue_checked"].any() and (nb["catalogue_neighbour"] == "").all()
    out = S.candidate_table(nb, pd.DataFrame({"source_id": np.array([1, 2, 3], np.int64), "det": 2, "snr": 50.0,
                                              "flux_ujy": 100.0}))
    assert all(r.endswith("one_catalogue_source") for r in out["reasons"])
    assert S.with_irsa(pd.DataFrame({"ref_id": ["gaia:1"]}), None).shape == (1, 1)
