"""The band-excess estimator on a synthetic SPHEREx-like spectrum (offline)."""

import numpy as np
import pytest

import models as M


def synth(line_flux, rng, n=400, noise=0.01, um0=4.26, R=112.0):
    um = np.sort(rng.uniform(3.9, 4.6, n))
    cont = 1e-15 * (1 + 0.3 * (um - 4.2))  # W m^-2 um^-1
    bw = um / R
    # a narrow line seen through top-hat channels of width bw
    line = np.where(np.abs(um - um0) < bw / 2, line_flux / bw, 0.0)
    err = noise * cont
    y = cont + line + rng.normal(0, err)
    return um, y, err


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_recovers_line_flux(seed):
    rng = np.random.default_rng(seed)
    F = 2e-17  # W m^-2
    um, y, e = synth(F, rng)
    out = M.band_excess(um, y, e, (4.21, 4.31), [(3.95, 4.15), (4.36, 4.55)])
    assert out["flux_w_m2"] == pytest.approx(F, rel=0.12)
    assert out["snr"] > 5


def test_no_line_is_not_significant():
    snrs = []
    for seed in range(20):
        rng = np.random.default_rng(100 + seed)
        um, y, e = synth(0.0, rng)
        snrs.append(M.band_excess(um, y, e, (4.21, 4.31), [(3.95, 4.15), (4.36, 4.55)])["snr"])
    snrs = np.array(snrs)
    assert np.abs(np.mean(snrs)) < 1.0
    assert np.std(snrs) < 1.6


def test_too_few_points():
    out = M.band_excess([4.0, 4.26], [1.0, 2.0], [0.1, 0.1], (4.21, 4.31), [(3.95, 4.15)])
    assert out["flux_w_m2"] is None
