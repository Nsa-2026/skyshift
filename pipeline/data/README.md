# pipeline/data: small reference data

## eazy_fsps_qsf12_v3_rest_0p3_5p2um.csv (photoz templates, 147 KB)
- What: the 12 EAZY `tweak_fsps_QSF_12_v3` galaxy templates (FSPS stellar populations with nebular lines and
  dust emission, PAH 3.3 µm included), as F_ν in the rest frame, 0.30–5.20 µm, bin-averaged to R = 500
  (d ln λ = 0.002), each normalised to mean F_ν = 1 over 1.0–2.0 µm. Enough for z = 0–1 on our 0.75–5.0 µm channels.
- Source: https://github.com/gbrammer/eazy-photoz/tree/master/templates/fsps_full (`tweak_fsps_QSF_12_v3_001..012.dat`,
  downloaded 14 Nov 2026), resampled with `make_eazy_templates.py` in this folder.
- Licence: the eazy-photoz repository carries no licence file; the templates are the default set of eazy-py
  (MIT, https://github.com/gbrammer/eazy-py) and are public research data. Cite them when showing photo-z results:
  - Brammer, van Dokkum & Coppi 2008, ApJ 686, 1503 (EAZY)
  - Conroy, Gunn & White 2009, ApJ 699, 486; Conroy & Gunn 2010, ApJ 712, 833 (FSPS)
