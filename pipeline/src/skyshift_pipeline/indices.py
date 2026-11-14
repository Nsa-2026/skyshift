"""Spectral indices and synthetic magnitudes from the 102-channel spectra (sources.parquet).

Nothing here reads a photo: the input is the (N, 102) flux_ujy / err_ujy arrays written by `spectra`.

A window [a, b] µm is the mean of the channels it overlaps, each weighted by the fraction of the channel
inside the window (NaN channels left out); its error is sqrt(Σ w² σ²) / Σ w, its wavelength the
weighted log-mean of the channel centres. A window with less than INDEX_MIN_COVER of its channel weight
measured is NaN.

An index (config.INDEX_WINDOWS) compares a band window with a local continuum from one or two
neighbouring windows: a power law through the two (exact for any power-law spectrum; linear in λ when
a continuum window is ≤ 0 or below INDEX_LOG_CONT_MIN_SNR), or flat for one window, evaluated at the
band's wavelength.
    absorption: depth  = 1 − F_band / F_cont,  sig = (F_cont − F_band) / sqrt(σ_band² + σ_cont²)
    emission:   excess = F_band / F_cont − 1,  sig = (F_band − F_cont) / sqrt(σ_band² + σ_cont²)
`scale` (= 1 + z, per source) shifts every window: photoz uses it for PAH 3.3 µm at z_best.
"""

from __future__ import annotations

import numpy as np

from . import config as C
from .spectra import channels

AB_ZP_UJY = 23.9  # AB magnitude = 23.9 − 2.5 log10(F / µJy)


def channel_arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(lo, hi, centre) of the 102 channels in µm."""
    ch = channels()
    return (np.array([c["um_lo"] for c in ch]), np.array([c["um_hi"] for c in ch]),
            np.array([c["um_centre"] for c in ch]))


LO, HI, CENTRE = channel_arrays()


def window_weights(a, b, scale=None) -> np.ndarray:
    """(1 or N, 102) fraction of each channel inside [a·scale, b·scale]."""
    s = np.ones((1, 1)) if scale is None else np.asarray(scale, np.float64).reshape(-1, 1)
    A, B = a * s, b * s
    return np.clip(np.minimum(HI, B) - np.maximum(LO, A), 0.0, None) / (HI - LO)


def window_mean(flux: np.ndarray, err: np.ndarray, a: float, b: float, scale=None,
                min_cover: float = C.INDEX_MIN_COVER) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mean flux, its error and its wavelength (µm) in [a, b] for each spectrum; NaN if under-covered."""
    flux = np.atleast_2d(flux)
    err = np.atleast_2d(err)
    w = window_weights(a, b, scale)
    have = np.isfinite(flux) & np.isfinite(err) & (err > 0)
    wf = np.where(have, w, 0.0)
    sw = wf.sum(1)
    tot = w.sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        f = np.where(have, flux, 0.0)
        e = np.where(have, err, 0.0)
        mean = (wf * f).sum(1) / sw
        sig = np.sqrt((wf * wf * e * e).sum(1)) / sw
        lam = np.exp((wf * np.log(CENTRE)).sum(1) / sw)
        ok = (sw > 0) & (sw >= min_cover * tot)
    nan = np.full(mean.shape, np.nan)
    return np.where(ok, mean, nan), np.where(ok, sig, nan), np.where(ok, lam, nan)


def continuum(f1, e1, l1, f2, e2, l2, lam):
    """Continuum at `lam` from two windows: power law if both are positive and significant, else linear."""
    with np.errstate(invalid="ignore", divide="ignore"):
        t_log = np.log(lam / l1) / np.log(l2 / l1)
        use_log = (f1 > 0) & (f2 > 0) & (f1 >= C.INDEX_LOG_CONT_MIN_SNR * e1) & (f2 >= C.INDEX_LOG_CONT_MIN_SNR * e2)
        lf1, lf2 = np.log(np.where(use_log, f1, 1.0)), np.log(np.where(use_log, f2, 1.0))
        fc_log = np.exp(lf1 + t_log * (lf2 - lf1))
        ec_log = fc_log * np.sqrt(((1 - t_log) * e1 / f1) ** 2 + (t_log * e2 / f2) ** 2)
        t_lin = (lam - l1) / (l2 - l1)
        fc_lin = f1 + t_lin * (f2 - f1)
        ec_lin = np.sqrt(((1 - t_lin) * e1) ** 2 + (t_lin * e2) ** 2)
    return np.where(use_log, fc_log, fc_lin), np.where(use_log, ec_log, ec_lin)


def index(flux, err, kind: str, band, cont, scale=None) -> tuple[np.ndarray, np.ndarray]:
    """(depth or excess, significance) of one index for each spectrum."""
    fb, eb, lb = window_mean(flux, err, *band, scale=scale)
    ws = [window_mean(flux, err, *w, scale=scale) for w in cont]
    if len(ws) == 1:
        fc, ec = ws[0][0], ws[0][1]
    else:
        fc, ec = continuum(*ws[0], *ws[1], lb)
    with np.errstate(invalid="ignore", divide="ignore"):
        tot = np.sqrt(eb * eb + ec * ec)
        if kind == "abs":
            val = 1.0 - fb / fc
            sig = (fc - fb) / tot
        else:
            val = fb / fc - 1.0
            sig = (fb - fc) / tot
        val = np.where(fc > 0, val, np.nan)
    return val.astype(np.float32), sig.astype(np.float32)


def all_indices(flux, err, windows: dict | None = None, scale=None) -> dict[str, np.ndarray]:
    """{<name>_depth | <name>_excess, <name>_sig} for every index in config.INDEX_WINDOWS."""
    out = {}
    for name, (kind, band, cont) in (windows or C.INDEX_WINDOWS).items():
        v, s = index(flux, err, kind, band, cont, scale)
        out[f"{name}_{'depth' if kind == 'abs' else 'excess'}"] = v
        out[f"{name}_sig"] = s
    return out


def ab_mag(f_ujy) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(np.asarray(f_ujy) > 0, AB_ZP_UJY - 2.5 * np.log10(f_ujy), np.nan).astype(np.float32)


def magnitudes(flux, err) -> dict[str, np.ndarray]:
    """{mag_ab_<name>} over config.MAG_WINDOWS (NaN when the window is not covered or ≤ 0)."""
    return {f"mag_ab_{k}": ab_mag(window_mean(flux, err, a, b)[0]) for k, (a, b) in C.MAG_WINDOWS.items()}


def stack(col) -> np.ndarray:
    """A list column of (102,) arrays -> (N, 102) float64."""
    vals = list(col)
    if not vals:
        return np.zeros((0, len(CENTRE)))
    return np.stack([np.asarray(v, np.float64) for v in vals])
