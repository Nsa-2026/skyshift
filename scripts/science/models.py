"""Physical models: solar spectrum, H-G phase function, reflected light, NEATM, band excess, Haser.

Units: wavelength in um, flux density f_nu in uJy, distances r and delta in au (diameters in km),
angles in degrees unless named *_rad. Pure numpy; no network.

- Solar spectrum: data/solar_spectrum.csv, STScI CALSPEC sun_mod_001 (Kurucz model, Bohlin 2014)
  rebinned to R ~ 200 between 0.4 and 5.5 um and scaled to 1361 W/m^2 at 1 au (`make_solar_table`).
- H-G system: Bowell et al. 1989. D = 1329 km / sqrt(pv) * 10^(-H/5) (Fowler & Chillemi 1992;
  assumes V_sun = -26.76). Phase integral q = 0.290 + 0.684 G. Bond albedo A = q pv.
- NEATM: Harris 1998. T(lat, lon) = T_ss (cos lat cos lon)^(1/4) on the day side, zero at night;
  T_ss = [(1 - A) S / (eta eps sigma r^2)]^(1/4). The flux is integrated over the part of the
  sphere that is both lit and seen at phase angle alpha.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray

HERE = Path(__file__).resolve().parent
SOLAR_CSV = HERE / "data" / "solar_spectrum.csv"

H_PLANCK = 6.62607015e-34
C_LIGHT = 2.99792458e8
K_B = 1.380649e-23
SIGMA_SB = 5.670374419e-8
AU_KM = 1.495978707e8
S_SUN = 1361.0  # W m^-2 at 1 au (Kopp & Lean 2011)
V_SUN = -26.76
D_H_CONST = 1329.0  # km
EMISSIVITY = 0.9
UJY = 1e-32  # W m^-2 Hz^-1 per uJy

F = NDArray[np.float64]


# --- Sun -----------------------------------------------------------------------------------

def make_solar_table(src_fits: str | Path, out_csv: str | Path = SOLAR_CSV) -> None:
    """Rebin CALSPEC sun_mod_001 (FLAM) to R ~ 200 over 0.4-5.5 um, scaled to S_SUN at 1 au."""
    from astropy.io import fits

    with fits.open(src_fits) as h:
        w = np.asarray(h[1].data["WAVELENGTH"], dtype=np.float64) / 1e4  # um
        f = np.asarray(h[1].data["FLUX"], dtype=np.float64)
    order = np.argsort(w)
    w, f = w[order], f[order]
    total = np.trapezoid(f, w)
    f = f * S_SUN / total  # W m^-2 um^-1 at 1 au
    edges = np.exp(np.arange(np.log(0.4), np.log(5.5), 1 / 200.0))
    mids, vals = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        s = (w >= a) & (w < b)
        if s.sum() >= 2:
            mids.append(np.sqrt(a * b))
            vals.append(np.trapezoid(f[s], w[s]) / (w[s][-1] - w[s][0]))
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w") as fh:
        fh.write("# CALSPEC sun_mod_001 rebinned to R~200, scaled to 1361 W/m2 at 1 au\n")
        fh.write("um,flam_w_m2_um\n")
        for m, v in zip(mids, vals):
            fh.write(f"{m:.5f},{v:.6e}\n")


_SOLAR: tuple[F, F] | None = None


def solar_flam(um: ArrayLike) -> F:
    """Solar f_lambda at 1 au, W m^-2 um^-1."""
    global _SOLAR
    if _SOLAR is None:
        d = np.loadtxt(SOLAR_CSV, delimiter=",", comments="#", skiprows=2)
        _SOLAR = (d[:, 0], d[:, 1])
    w, f = _SOLAR
    return np.exp(np.interp(np.log(np.asarray(um, dtype=np.float64)), np.log(w), np.log(f)))


def flam_to_ujy(flam_w_m2_um: ArrayLike, um: ArrayLike) -> F:
    """W m^-2 um^-1 -> uJy."""
    lam_m = np.asarray(um, dtype=np.float64) * 1e-6
    return np.asarray(flam_w_m2_um) * 1e6 * lam_m**2 / C_LIGHT / UJY


def ujy_to_flam(ujy: ArrayLike, um: ArrayLike) -> F:
    lam_m = np.asarray(um, dtype=np.float64) * 1e-6
    return np.asarray(ujy) * UJY * C_LIGHT / lam_m**2 / 1e6


def solar_ujy(um: ArrayLike) -> F:
    """Solar f_nu at 1 au, uJy."""
    return flam_to_ujy(solar_flam(um), um)


def planck_ujy_sr(um: ArrayLike, T: ArrayLike) -> F:
    """B_nu(T) in uJy per steradian."""
    lam = np.asarray(um, dtype=np.float64) * 1e-6
    nu = C_LIGHT / lam
    x = H_PLANCK * nu / (K_B * np.asarray(T, dtype=np.float64))
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        b = 2 * H_PLANCK * nu**3 / C_LIGHT**2 / np.expm1(x)
    return np.where(np.isfinite(b), b, 0.0) / UJY


# --- asteroid reflected light ----------------------------------------------------------------

def hg_phase(alpha_deg: ArrayLike, G: float) -> F:
    """Bowell et al. (1989) H-G phase function, 1 at alpha = 0."""
    t = np.tan(np.deg2rad(np.asarray(alpha_deg, dtype=np.float64)) / 2)
    p1 = np.exp(-3.33 * t**0.63)
    p2 = np.exp(-1.87 * t**1.22)
    return (1 - G) * p1 + G * p2


def diameter_from_h(H: float, pv: ArrayLike) -> F:
    return D_H_CONST / np.sqrt(np.asarray(pv, dtype=np.float64)) * 10 ** (-H / 5)


def pv_from_d(H: float, D_km: ArrayLike) -> F:
    return (D_H_CONST / np.asarray(D_km, dtype=np.float64)) ** 2 * 10 ** (-0.4 * H)


def phase_integral(G: float) -> float:
    return 0.290 + 0.684 * G


def reflected_ujy(um: ArrayLike, D_km: float, p_lambda: ArrayLike, r_au: ArrayLike, delta_au: ArrayLike,
                  alpha_deg: ArrayLike, G: float) -> F:
    """Sunlight reflected by a sphere of geometric albedo p_lambda (at that wavelength), uJy."""
    rad = D_km / 2 / AU_KM  # au
    return (solar_ujy(um) / np.asarray(r_au) ** 2 * np.asarray(p_lambda) * hg_phase(alpha_deg, G)
            * (rad / np.asarray(delta_au)) ** 2)


def v_mag(H: float, G: float, r_au: ArrayLike, delta_au: ArrayLike, alpha_deg: ArrayLike) -> F:
    return H + 5 * np.log10(np.asarray(r_au) * np.asarray(delta_au)) - 2.5 * np.log10(hg_phase(alpha_deg, G))


# --- NEATM -----------------------------------------------------------------------------------

def subsolar_temperature(r_au: ArrayLike, A_bond: float, eta: float, eps: float = EMISSIVITY) -> F:
    return ((1 - A_bond) * S_SUN / (eta * eps * SIGMA_SB * np.asarray(r_au, dtype=np.float64) ** 2)) ** 0.25


_NG = 48
_GX, _GW = np.polynomial.legendre.leggauss(_NG)


def neatm_ujy(um: ArrayLike, D_km: float, pv: float, eta: float, r_au: ArrayLike, delta_au: ArrayLike,
              alpha_deg: ArrayLike, G: float = 0.15, eps: float = EMISSIVITY) -> F:
    """NEATM thermal flux density (uJy) of a sphere. um, r, delta, alpha broadcast (1-D)."""
    um, r, dl, al = np.broadcast_arrays(*(np.atleast_1d(np.asarray(v, dtype=np.float64))
                                          for v in (um, r_au, delta_au, alpha_deg)))
    A = phase_integral(G) * pv
    Tss = subsolar_temperature(r, A, eta, eps)  # (n,)
    a = np.deg2rad(np.abs(al))
    # latitude theta in [-pi/2, pi/2]; longitude phi in [a - pi/2, pi/2] (lit and seen)
    th = _GX * np.pi / 2  # (g,)
    wth = _GW * np.pi / 2
    lo = a - np.pi / 2
    hi = np.full_like(a, np.pi / 2)
    ph = (hi - lo)[:, None] / 2 * _GX[None, :] + (hi + lo)[:, None] / 2  # (n, g)
    wph = (hi - lo)[:, None] / 2 * _GW[None, :]
    cth = np.cos(th)[None, None, :]  # (1,1,g)
    cph = np.clip(np.cos(ph), 0, None)[:, :, None]  # (n,g,1)
    T = Tss[:, None, None] * (cth * cph) ** 0.25  # (n, g, g)
    B = planck_ujy_sr(um[:, None, None], T)
    vis = np.cos(ph - a[:, None])[:, :, None]
    integ = (B * cth**2 * vis * wph[:, :, None] * wth[None, None, :]).sum(axis=(1, 2))
    rad = D_km / 2 / AU_KM
    return eps * (rad / dl) ** 2 * integ


def neatm_bolometric_w_m2(D_km: float, pv: float, eta: float, r_au: float, delta_au: float,
                          G: float = 0.15, eps: float = EMISSIVITY) -> float:
    """Bolometric NEATM flux at alpha = 0, analytic: (2/3) eps sigma Tss^4 R^2 / (pi Delta^2) * pi."""
    A = phase_integral(G) * pv
    Tss = float(subsolar_temperature(r_au, A, eta, eps))
    R = D_km * 1e3 / 2
    dl = delta_au * AU_KM * 1e3
    return 2.0 / 3.0 * eps * SIGMA_SB * Tss**4 * R**2 / dl**2


# --- band excess ---------------------------------------------------------------------------

def band_excess(um: ArrayLike, flam: ArrayLike, err: ArrayLike, band: tuple[float, float],
                sides: list[tuple[float, float]], order: int = 1, clip: float = 3.0) -> dict:
    """Integrated excess (W m^-2) in `band` above a polynomial continuum fitted to the `sides` windows.

    flam and err in W m^-2 um^-1, one value per sample at the channel centre `um` (any spacing).
    Each sample is the spectrum seen through its channel, so a line of flux F adds F / bw to the
    channels within ~bw of it: integrating the residual over the channel-centre wavelength gives F
    whatever the channel width. The integral is a trapezoid over the sorted samples, pinned to 0 at
    the band edges.
    Continuum: weighted polynomial with iterative `clip`-sigma rejection (stars crossing the aperture,
    cosmic rays). Errors: each sample's error plus the extra scatter seen in the side windows
    (jitter: stars, activity changes), propagated through the trapezoid weights, plus the continuum
    error over the band.
    """
    um = np.asarray(um, dtype=np.float64)
    y = np.asarray(flam, dtype=np.float64)
    e = np.asarray(err, dtype=np.float64)
    ok = np.isfinite(um) & np.isfinite(y) & np.isfinite(e) & (e > 0)
    side = np.zeros(um.shape, bool)
    for a, b in sides:
        side |= (um >= a) & (um <= b)
    side &= ok
    inb = (um >= band[0]) & (um <= band[1]) & ok
    out: dict = {"band_um": list(band), "n_band": int(inb.sum()), "n_cont": int(side.sum())}
    if inb.sum() < 1 or side.sum() < order + 2:
        out.update(flux_w_m2=None, err_w_m2=None, snr=None)
        return out
    x0 = float(np.mean(band))
    use = side.copy()
    jitter = 0.0
    for _ in range(10):
        A = np.vander(um[use] - x0, order + 1)
        w = 1 / np.sqrt(e[use] ** 2 + jitter**2)
        coef, *_ = np.linalg.lstsq(A * w[:, None], y[use] * w, rcond=None)
        res_all = y - np.vander(um - x0, order + 1) @ coef
        r = res_all[use]
        mad = 1.4826 * float(np.median(np.abs(r - np.median(r))))
        jitter = float(np.sqrt(max(mad**2 - np.median(e[use] ** 2), 0.0)))
        new = side & (np.abs(res_all) <= clip * np.sqrt(e**2 + jitter**2))
        if new.sum() < order + 2 or np.array_equal(new, use):
            break
        use = new
    A = np.vander(um[use] - x0, order + 1)
    w = 1 / np.sqrt(e[use] ** 2 + jitter**2)
    cov = np.linalg.inv((A * w[:, None]).T @ (A * w[:, None]))
    # trapezoid over the band, pinned to zero residual at both edges
    o = np.argsort(um[inb])
    xb = np.concatenate([[band[0]], um[inb][o], [band[1]]])
    rb = np.concatenate([[0.0], res_all[inb][o], [0.0]])
    eb = np.concatenate([[0.0], np.sqrt(e[inb][o] ** 2 + jitter**2), [0.0]])
    wt = np.zeros(xb.size)
    wt[1:-1] = (xb[2:] - xb[:-2]) / 2
    flux = float(np.sum(wt * rb))
    err_meas = float(np.sqrt(np.sum((wt * eb) ** 2)))
    width = band[1] - band[0]
    Am = np.mean(np.vander(np.linspace(band[0], band[1], 21) - x0, order + 1), axis=0)
    err_cont = float(np.sqrt(Am @ cov @ Am)) * width
    ferr = float(np.hypot(err_meas, err_cont))
    cont_mean = float(Am @ coef)
    out.update(flux_w_m2=flux, err_w_m2=ferr, snr=flux / ferr if ferr > 0 else None,
               continuum_flam=cont_mean, n_cont_used=int(use.sum()), jitter_flam=jitter,
               peak_residual_flam=float(np.max(res_all[inb])), max_gap_um=float(np.max(np.diff(xb))))
    return out


# --- Haser ---------------------------------------------------------------------------------

def haser_n_aperture(Q: float, v_km_s: float, L_km: float, rho_km: float, n: int = 400) -> float:
    """Number of parent molecules inside a circular aperture of projected radius rho (Haser 1957).

    Column density N(p) = Q / (2 pi v p) * int_0^{pi/2} exp(-p / (L cos t)) dt, integrated over the
    aperture: N_ap = (Q / v) int_0^rho int_0^{pi/2} exp(-p / (L cos t)) dt dp.
    For rho << L this is Q pi rho / (2 v).
    """
    t, wt = np.polynomial.legendre.leggauss(n)
    t = (t + 1) * np.pi / 4
    wt = wt * np.pi / 4
    p, wp = np.polynomial.legendre.leggauss(n)
    p = (p + 1) * rho_km / 2
    wp = wp * rho_km / 2
    inner = np.exp(-p[:, None] / (L_km * np.cos(t)[None, :])) @ wt
    return float(Q / v_km_s * np.sum(inner * wp))


def production_rate(line_w_m2: float, g1_au: float, um: float, r_au: float, delta_au: float,
                    rho_km: float, v_km_s: float, tau1_s: float,
                    annulus_km: tuple[float, float] | None = None) -> dict:
    """Q (molecules/s) from an integrated line flux with a fluorescence g-factor and a Haser parent.

    g = g1 / r^2 (photons/s/molecule), tau = tau1 r^2, L = v tau. N_ap = 4 pi Delta^2 F / (g h nu).
    annulus_km: if the background came from an annulus (r_in, r_out) that also holds gas, the
    measured N_ap is N(rho) - rho^2 / (r_out^2 - r_in^2) * (N(r_out) - N(r_in)); the same Haser
    model gives that expected count per unit Q.
    """
    g = g1_au / r_au**2
    hnu = H_PLANCK * C_LIGHT / (um * 1e-6)
    dl_m = delta_au * AU_KM * 1e3
    n_ap = 4 * np.pi * dl_m**2 * line_w_m2 / (g * hnu)
    L = v_km_s * tau1_s * r_au**2
    per_q = haser_n_aperture(1.0, v_km_s, L, rho_km)
    lost = 0.0
    if annulus_km is not None:
        a, b = annulus_km
        ann = haser_n_aperture(1.0, v_km_s, L, b) - haser_n_aperture(1.0, v_km_s, L, a)
        sub = rho_km**2 / (b**2 - a**2) * ann
        lost = sub / per_q
        per_q -= sub
    return {"n_aperture": float(n_ap), "Q_per_s": float(n_ap / per_q), "g": g, "L_km": L,
            "v_km_s": v_km_s, "rho_km": rho_km, "annulus_fraction_lost": float(lost)}
