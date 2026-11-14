import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline import indices as IX
from skyshift_pipeline import photoz as PZ


@pytest.fixture(scope="module")
def grid():
    um, tmpl, names = PZ.load_templates()
    z = PZ.z_grid()
    return um, tmpl, names, z, PZ.model_grid(z, um, tmpl)


def test_templates_and_grid(grid):
    um, tmpl, names, z, M = grid
    assert len(names) == 12 and tmpl.shape == (12, um.size) and np.all(tmpl > 0)
    assert z[0] == 0 and z[-1] == 1 and len(z) == 201
    assert M.shape == (201, 12, 102)
    # at z = 0 a channel's model is the template's mean over the channel
    k = 60
    m = (um > IX.LO[k]) & (um < IX.HI[k])
    assert M[0, 3, k] == pytest.approx(tmpl[3, m].mean(), rel=0.02)
    # redshift moves features red: the 1.6 µm stellar bump peaks later at higher z
    bump = lambda zi: IX.CENTRE[17 + np.argmax(M[zi, 0, 17:68])]  # noqa: E731  (D2–D4, 1.1–3.8 µm)
    assert bump(0) < bump(100) < bump(200)


def make_galaxies(M, z, rng, n=300, snr=10.0, frac_missing=0.2):
    zi = rng.integers(2, len(z) - 2, n)
    ti = rng.integers(0, M.shape[1], n)
    model = M[zi, ti] * rng.uniform(50, 500, n)[:, None]
    # noise: S/N `snr` at the median channel plus a floor; some channels missing
    err = np.median(model, 1, keepdims=True) / snr + 0.02 * model
    flux = model + rng.normal(size=model.shape) * err
    miss = rng.random(model.shape) < frac_missing
    flux[miss] = np.nan
    err[miss] = np.nan
    return flux, err, z[zi]


def test_photoz_recovers_template_redshifts(grid, rng):
    _, _, _, z, M = grid
    flux, err, zt = make_galaxies(M, z, rng)
    out = PZ.fit(flux, err, M, z)
    dz = (out["z_best"].to_numpy() - zt) / (1 + zt)
    nmad = 1.4826 * np.median(np.abs(dz - np.median(dz)))
    assert nmad < 0.02, nmad
    assert np.mean(np.abs(dz) > C.PHOTOZ_OUTLIER) < 0.05
    # the 68% interval holds the truth about 68% of the time (grid step slack)
    inside = (zt >= out["z_lo"] - C.PHOTOZ_Z_STEP) & (zt <= out["z_hi"] + C.PHOTOZ_Z_STEP)
    assert inside.mean() > 0.6
    assert out["good"].mean() > 0.7 and (out["n_channels"] > 60).all()
    assert out["template"].between(1, 12).all()


def test_photoz_low_snr_is_flagged(grid, rng):
    _, _, _, z, M = grid
    flux, err, zt = make_galaxies(M, z, rng, n=200, snr=0.7)
    out = PZ.fit(flux, err, M, z)
    good = out["good"].to_numpy()
    assert good.mean() < 0.5
    width = (out["z_hi"] - out["z_lo"]).to_numpy()
    assert np.median(width) > 0.05


def test_too_few_channels_not_fitted(grid):
    _, _, _, z, M = grid
    f = M[50, 2][None] * 100
    e = f * 0.05
    f[:, C.PHOTOZ_MIN_CHANNELS - 1:] = np.nan
    out = PZ.fit(f, e, M, z)
    assert np.isnan(out["z_best"].iloc[0]) and not out["good"].iloc[0] and out["template"].iloc[0] == 0


def test_validation_stats():
    pz = pd.DataFrame({"source_id": [1, 2, 3, 4], "z_best": [0.1, 0.2, 0.5, np.nan], "good": [True, True, False, False],
                       "mag_ab_k": [15.5, 16.5, 18.0, 15.0]})
    src = pd.DataFrame({"source_id": [1, 2, 3, 4], "ra": [122.0, 122.1, 122.2, 122.3], "dec": [16.0] * 4})
    spec = pd.DataFrame({"ra": [122.0, 122.1 + 1 / 3600, 122.2, 122.3, 125.0], "dec": [16.0] * 5,
                         "z": [0.1, 0.21, 0.2, 0.1, 0.3]})
    rep = PZ.validate(pz, src, spec)
    assert rep["sdss_galaxies_in_zone"] == 5
    assert rep["all"]["n"] == 3 and rep["all"]["outlier_frac"] == pytest.approx(1 / 3)
    assert rep["good"]["n"] == 2 and rep["k_ab_lt_17"]["n"] == 2
    assert rep["by_k_ab"]["17..18"]["n"] == 0 and rep["by_k_ab"]["18..99"]["n"] == 1


@pytest.mark.network
def test_sdss_query_live(tmp_path):
    from skyshift_pipeline.store import Store

    zone = C.Zone("tiny", 121.2, 121.4, 16.8, 17.1)
    spec = PZ.sdss_specz(Store(str(tmp_path)), zone)
    assert len(spec) > 0 and spec["z"].between(0, 2).all()


def test_good_needs_z_and_pah_label_at_the_redshift(grid):
    _, _, _, z, M = grid
    f = np.stack([M[0, 5], M[20, 5], M[20, 5]]) * 300.0  # z = 0 and z = 0.1 (twice)
    band = np.array(C.INDEX_WINDOWS["pah_33"][1]) * 1.1
    pah = (IX.HI > band[0]) & (IX.LO < band[1])
    f[1:, pah] *= 1.6  # PAH 3.3 µm emission moved to 3.6 µm
    e = 0.02 * f
    src = pd.DataFrame({"source_id": [1, 2, 3], "flux_ujy": list(f), "err_ujy": list(e),
                        "visit_bits": [np.full(102, 3, np.int16), np.full(102, 3, np.int16), np.full(102, 1, np.int16)]})
    out = PZ.photoz_frame(src)
    assert out["z_best"].iloc[0] <= C.PHOTOZ_GOOD_MIN_Z and not out["good"].iloc[0]  # z ≈ 0: never good
    assert abs(out["z_best"].iloc[1] - 0.1) < 0.02 and out["good"].iloc[1]
    assert out["pah_33_sig_z"].iloc[1] >= C.INDEX_SIG
    # the label needs the moved band in two visits: source 3 has the same spectrum from one visit
    assert out["label_pah_33_z"].tolist() == [False, True, False]
