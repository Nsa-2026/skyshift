"""'Planet X exclusion meter': how big / how far an unseen planet could hide in this field.

From the measured noise of the per-visit stacks we get a 5-sigma point-source limit in
several wavelength windows. A distant planet is seen in reflected sunlight:

    F_nu(lambda) = F_sun,nu(lambda, 1 AU) * p * (R / Delta)^2 / r^2        (phase ~ 0)

with r ~ Delta = d (AU) for a far object, so the smallest detectable radius is

    R_min(d) = d^2 * sqrt(F_lim / (p * F_sun,nu)).

We report R_min vs distance, and whether reference Planet Nine models (Brown & Batygin 2021:
~6 M_earth, V ~ 21-23) would have been detected here. Self-luminous cold objects are scaled
from WISE 0855 (W2 = 13.9 at 2.28 pc). The thermal 4-5 um excess predicted for P9 by
Fortney et al. (2016) would only make detection easier, so the reflected-light limit is
conservative.
"""
import numpy as np

AU = 1.495978707e11
R_SUN = 6.957e8
R_EARTH_AU = 6.371e6 / AU
C = 2.998e8
H = 6.626e-34
K = 1.381e-23


def sun_fnu_1au_mjy(lam_um, T=5772.0):
    lam = lam_um * 1e-6
    B = 2 * H * C ** 2 / lam ** 5 / (np.exp(H * C / (lam * K * T)) - 1)       # W/m2/m/sr
    F_lam = np.pi * B * (R_SUN / AU) ** 2                                       # W/m2/m
    F_nu = F_lam * lam ** 2 / C                                                 # W/m2/Hz
    return F_nu / 1e-29                                                          # mJy


def depth_per_window(resid_stack, var_stack, fwhm_pix, omega_sr=(6.15 / 206265) ** 2):
    """5-sigma point source limit (mJy) from a residual stack's noise."""
    s = fwhm_pix / 2.3548
    neff = 4 * np.pi * s ** 2                      # effective number of pixels for PSF photometry
    sig_pix = np.sqrt(np.nanmedian(var_stack))
    emp = np.nanstd(resid_stack[np.isfinite(resid_stack)]) if np.isfinite(resid_stack).any() else sig_pix
    sig_pix = max(sig_pix, emp)                    # use the larger of formal / empirical noise
    return 5 * sig_pix * np.sqrt(neff) * omega_sr * 1e9


def exclusion(limits, albedos=(0.3, 0.5), distances=(50, 100, 200, 300, 400, 500, 700, 1000, 1500)):
    """limits: list of dict(lam, flim_mjy). Returns R_min (Earth radii) vs distance."""
    out = []
    for d in distances:
        row = dict(d_au=d)
        for p in albedos:
            best = np.inf
            for L in limits:
                fs = sun_fnu_1au_mjy(L["lam"])
                R = d ** 2 * np.sqrt(L["flim_mjy"] / (p * fs)) / R_EARTH_AU
                best = min(best, R)
            row[f"Rmin_earth_p{p}"] = float(best)
        out.append(row)
    return out


def ab_mag(f_mjy):
    return -2.5 * np.log10(f_mjy * 1e-3 / 3631)


def p9_check(limits, V_range=(21.0, 23.0)):
    """Would a Brown & Batygin-like P9 (solar colours, V given) be detected in any window?"""
    res = []
    for V in np.arange(V_range[0], V_range[1] + 0.01, 0.5):
        # V(AB) ~ V(Vega) - 0.02; with solar colours the reflected F_nu scales like the Sun's
        f_v = 3631e3 * 10 ** (-0.4 * V)                              # mJy at 0.55 um
        det = []
        for L in limits:
            f = f_v * sun_fnu_1au_mjy(L["lam"]) / sun_fnu_1au_mjy(0.55)
            det.append(f / L["flim_mjy"] * 5)
        res.append(dict(V=float(V), best_snr=float(max(det)) if det else None))
    return res


def brown_dwarf_reach(limits, ref_w2_mjy=0.47, ref_d_pc=2.28, lam_window=(4.4, 5.0)):
    """Distance out to which a WISE 0855 twin would be detected at 5 sigma."""
    L = [l for l in limits if lam_window[0] <= l["lam"] <= lam_window[1]]
    if not L:
        return None
    f = min(l["flim_mjy"] for l in L)
    return float(ref_d_pc * np.sqrt(ref_w2_mjy / f))
