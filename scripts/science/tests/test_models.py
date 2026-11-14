"""Reflected light, NEATM and Haser against textbook values (offline)."""

import numpy as np
import pytest

import models as M


def test_hg_phase_zero_and_monotonic():
    assert M.hg_phase(0.0, 0.15) == pytest.approx(1.0)
    p = M.hg_phase(np.array([0, 10, 20, 30]), 0.15)
    assert np.all(np.diff(p) < 0)
    # Bowell et al. 1989 by hand: tan(10 deg) = 0.1763, Phi1 = exp(-3.33 * 0.1763^0.63) = 0.3276,
    # Phi2 = exp(-1.87 * 0.1763^1.22) = 0.7984, Phi = 0.85 Phi1 + 0.15 Phi2 = 0.398 -> 1.00 mag
    assert -2.5 * np.log10(M.hg_phase(20.0, 0.15)) == pytest.approx(1.00, abs=0.01)


def test_diameter_albedo_relation():
    # Fowler & Chillemi (1992): H = 5.65, pv = 0.0717 -> D ~ 410 km (Hygiea, IRAS 407 km)
    assert M.diameter_from_h(5.65, 0.0717) == pytest.approx(366.0 * 1.0, rel=0.15)
    D = M.diameter_from_h(10.0, 0.2)
    assert M.pv_from_d(10.0, D) == pytest.approx(0.2)
    assert M.diameter_from_h(15.0, 0.25) == pytest.approx(2.658, rel=1e-3)  # 1329/0.5*10^-3


def test_solar_spectrum_v_band():
    # The Sun's V-band flux density at 1 au gives V ~ -26.76 (AB ~ Vega in V)
    vab = -2.5 * np.log10(M.solar_ujy(0.551) * 1e-6 / 3631.0)
    assert vab == pytest.approx(-26.76, abs=0.08)
    # integrated over 0.4-5.5 um the table holds most of the 1361 W/m^2 (~ 88 %)
    um = np.linspace(0.4, 5.5, 4000)
    frac = np.trapezoid(M.solar_flam(um), um) / M.S_SUN
    assert 0.80 < frac < 0.95


@pytest.mark.parametrize("H,G,r,dl,al", [(5.65, 0.15, 3.0, 2.5, 18.0), (12.0, 0.25, 1.5, 0.8, 40.0)])
def test_reflected_reproduces_v_magnitude(H, G, r, dl, al):
    """Reflected flux at V with pv D^2 fixed by H gives back V = H + 5 log(r Delta) - 2.5 log Phi."""
    pv = 0.1
    D = M.diameter_from_h(H, pv)
    f = M.reflected_ujy(0.551, D, pv, r, dl, al, G)
    v = M.V_SUN - 2.5 * np.log10(f / M.solar_ujy(0.551))
    assert v == pytest.approx(M.v_mag(H, G, r, dl, al), abs=0.01)


def test_subsolar_temperature_textbook():
    # black body at 1 au, A = 0, eta = 1, eps = 1: (S / sigma)^(1/4) = 393.6 K
    assert M.subsolar_temperature(1.0, 0.0, 1.0, 1.0) == pytest.approx(393.6, abs=0.3)
    # scales as r^-1/2 and (eta eps)^-1/4
    assert M.subsolar_temperature(4.0, 0.0, 1.0, 1.0) == pytest.approx(196.8, abs=0.2)


def test_neatm_energy_balance():
    """NEATM at alpha = 0: integrated over wavelength, the flux equals the analytic
    (2/3) eps sigma T_ss^4 R^2 / Delta^2 (Harris 1998), and summed over the sky the emitted power
    equals the absorbed power when eta = 1."""
    D, pv, eta, r, dl = 100.0, 0.1, 1.0, 2.5, 1.5
    um = np.geomspace(0.8, 2000, 4000)
    f = M.neatm_ujy(um, D, pv, eta, r, dl, 0.0)
    bol = np.trapezoid(M.ujy_to_flam(f, um), um)
    assert bol == pytest.approx(M.neatm_bolometric_w_m2(D, pv, eta, r, dl), rel=2e-3)
    # absorbed power pi R^2 (1-A) S / r^2 = emitted eps sigma T_ss^4 pi R^2 (eta = 1)
    A = M.phase_integral(0.15) * pv
    T = M.subsolar_temperature(r, A, 1.0)
    assert M.EMISSIVITY * M.SIGMA_SB * T**4 == pytest.approx((1 - A) * M.S_SUN / r**2, rel=1e-9)


def test_neatm_scaling_and_phase():
    um = np.array([4.6, 12.0])
    f1 = M.neatm_ujy(um, 100.0, 0.1, 1.0, 2.5, 1.5, 10.0)
    f2 = M.neatm_ujy(um, 200.0, 0.1, 1.0, 2.5, 1.5, 10.0)
    assert np.allclose(f2 / f1, 4.0)
    f3 = M.neatm_ujy(um, 100.0, 0.1, 1.0, 2.5, 1.5, 40.0)
    assert np.all(f3 < f1)  # part of the hot day side turns away
    # higher eta -> cooler -> much less Wien-tail flux at 4.6 um
    assert M.neatm_ujy(4.6, 100.0, 0.1, 1.5, 2.5, 1.5, 10.0)[0] < 0.5 * f1[0]


def test_neatm_rayleigh_jeans_limit():
    """At alpha = 0 and very long wavelength B_nu = 2 nu^2 k T / c^2, so the NEATM integral is analytic:
    F = eps R^2 / Delta^2 * 2 nu^2 k T_ss / c^2 * I(9/4) * I(5/4), I(n) = int cos^n = sqrt(pi) G((n+1)/2) / G(n/2+1)."""
    from math import gamma, pi, sqrt

    def I(n):
        return sqrt(pi) * gamma((n + 1) / 2) / gamma(n / 2 + 1)

    D, pv, eta, r, dl, um = 1.0, 0.1, 1.0, 1.1, 0.2, 3e4  # 3 cm
    T = M.subsolar_temperature(r, M.phase_integral(0.15) * pv, eta)
    nu = M.C_LIGHT / (um * 1e-6)
    R = D / 2 / M.AU_KM
    expect = M.EMISSIVITY * (R / dl) ** 2 * 2 * nu**2 * M.K_B * T / M.C_LIGHT**2 * I(9 / 4) * I(5 / 4) / M.UJY
    assert M.neatm_ujy(um, D, pv, eta, r, dl, 0.0)[0] == pytest.approx(expect, rel=2e-3)


def test_haser_limits():
    Q, v = 1e27, 0.5
    # rho << L: N = Q pi rho / (2 v)
    n = M.haser_n_aperture(Q, v, 1e7, 1e3)
    assert n == pytest.approx(Q * np.pi * 1e3 / (2 * v), rel=2e-3)
    # rho >> L: every molecule is inside, N = Q tau = Q L / v
    n = M.haser_n_aperture(Q, v, 1e3, 1e6, n=800)
    assert n == pytest.approx(Q * 1e3 / v, rel=2e-2)


def test_production_rate_roundtrip():
    """A Haser coma of known Q gives a line flux; production_rate inverts it."""
    Q, um, g1, tau1, r, dl, rho = 1e27, 4.26, 2.6e-3, 5e5, 3.0, 2.5, 2e4
    v = 0.85 / np.sqrt(r)
    L = v * tau1 * r**2
    n_ap = M.haser_n_aperture(Q, v, L, rho)
    hnu = M.H_PLANCK * M.C_LIGHT / (um * 1e-6)
    F = n_ap * (g1 / r**2) * hnu / (4 * np.pi * (dl * M.AU_KM * 1e3) ** 2)
    out = M.production_rate(F, g1, um, r, dl, rho, v, tau1)
    assert out["Q_per_s"] == pytest.approx(Q, rel=1e-6)
    out2 = M.production_rate(F, g1, um, r, dl, rho, v, tau1, annulus_km=(6e4, 9e4))
    assert out2["Q_per_s"] > out["Q_per_s"]  # gas in the background annulus is added back
