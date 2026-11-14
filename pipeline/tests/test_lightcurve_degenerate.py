import numpy as np

from skyshift_pipeline.lightcurves import poly_model


def test_degenerate_wavelengths_do_not_break_other_groups():
    x = np.r_[np.full(8, np.log(1.25)), np.tile(np.log([1.2, 1.3]), 4), np.linspace(-.2, .2, 8)]
    group = np.repeat(np.arange(3), 8)
    flux = np.r_[np.arange(8) + 100., np.arange(8) + 200., 300 + 2*x[16:] + 4*x[16:]**2]
    weight = np.linspace(1., 3., 24)
    result = poly_model(group, 4, x, flux, weight)
    for g in (0, 1):
        selected = group == g
        np.testing.assert_allclose(result[selected], np.average(flux[selected], weights=weight[selected]))
    np.testing.assert_allclose(result[16:], flux[16:], rtol=1e-10)
