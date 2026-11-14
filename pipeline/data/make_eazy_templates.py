"""Resample the EAZY tweak_fsps_QSF_12_v3 templates for photoz.py: F_nu, log grid 0.30-5.20 um (R=500), bin-averaged.

Usage: python make_eazy_templates.py <folder with tweak_fsps_QSF_12_v3_001..012.dat> <output.csv>
The .dat files: https://github.com/gbrammer/eazy-photoz/tree/master/templates/fsps_full
"""
import sys
import numpy as np
src, out = sys.argv[1], sys.argv[2]
step = 0.002
edges = np.exp(np.arange(np.log(0.30), np.log(5.20) + step, step))
centre = np.sqrt(edges[:-1] * edges[1:])
cols = []
for i in range(1, 13):
    d = np.loadtxt(f"{src}/tweak_fsps_QSF_12_v3_{i:03d}.dat")
    um = d[:, 0] / 1e4
    fnu = d[:, 1] * um**2  # F_lambda * lambda^2 ∝ F_nu
    # bin average on a fine linear interpolation (handles both dense and sparse input)
    fine = np.exp(np.linspace(np.log(edges[0]), np.log(edges[-1]), (len(edges) - 1) * 20 + 1))
    ff = np.interp(fine, um, fnu)
    fc = np.sqrt(fine[:-1] * fine[1:])
    fv = 0.5 * (ff[:-1] + ff[1:])
    idx = np.searchsorted(edges, fc, side="right") - 1
    b = np.bincount(idx, fv, len(centre)) / np.bincount(idx, minlength=len(centre))
    norm = b[(centre > 1.0) & (centre < 2.0)].mean()
    cols.append(b / norm)
M = np.column_stack([centre] + cols)
hdr = ("# EAZY tweak_fsps_QSF_12_v3 templates 001-012 (Brammer, van Dokkum & Coppi 2008; FSPS, Conroy, Gunn & White 2009),\n"
       "# from github.com/gbrammer/eazy-photoz templates/fsps_full (the template set eazy-py, MIT, uses by default).\n"
       "# Made by pipeline/data/make_eazy_templates.py: bin-averaged F_nu on a log grid 0.30-5.20 um, d(ln lambda) = 0.002 (R = 500),\n"
       "# each template normalised to mean F_nu = 1 over 1.0-2.0 um (rest frame). Columns: rest wavelength um, t01..t12.\n"
       "um," + ",".join(f"t{i:02d}" for i in range(1, 13)))
np.savetxt(out, M, delimiter=",", fmt=["%.6f"] + ["%.5g"] * 12, header=hdr, comments="")
print(M.shape)
