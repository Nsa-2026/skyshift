# scripts/science: solar-system science from SPHEREx cutouts (P1)

Asteroid sizes and albedos, and the gas of comet 3I/ATLAS, from small IRSA cutouts only (never
whole photos). Python 3.12, its own uv project, `skyshift-core` through a path source.

```
cd scripts/science
gcloud storage cp gs://nasa-glazing-data/results/most_1_60.csv .cache/   # MOST table, asteroids 1-60
uv run python asteroids.py all    # fetch [irsa|s3] [numbers] [detectors], measure, fit; ~20 min cold
# e.g. uv run python asteroids.py fetch s3 22,24 3,4,5,6  (8 in flight, cached in .cache/cutouts/)
uv run python comet.py all        # fetch [s3|irsa], measure, fit; ~3 min cold (258 cutouts)
uv run pytest                     # offline
```

| file | what it does |
|---|---|
| `cutouts.py` | IRSA cutout service (`<image URL>?center=RA,DEC&size=DEG`), or HTTP range reads of the same box from the public S3 copy (`fetch_s3_region`: same file layout and CRPIX shifts, ~0.5 MB per 23 px box, used when IRSA answered in ~20 s per cutout). At most 8 requests in flight, cached in `.cache/cutouts/`. A cutout holds all six parts and EPSF alone is 3.9 MB whatever the box, so the response is streamed and cut after PRIMARY, IMAGE, FLAGS, VARIANCE (~65 kB for 23 × 23 px). WCS-WAVE is one calibration file per detector (`spectral_wcs_<det>_v2.2`): identical in DR1 and QR3 files of different weeks (checked for all six detectors), so it is fetched once per detector and put back by `open_cutout`. |
| `phot.py` | Centroid within 2 px of the prediction (`skyshift_core.centroid`), sigma-clipped median background in an annulus, `skyshift_core.aperture_photometry` in µJy. A point is dropped when any mask bit (including OVERFLOW/NONLINEAR = saturation) falls within 2 px. |
| `models.py` | CALSPEC solar spectrum, H-G phase function, reflected light, NEATM, band excess, Haser. |
| `asteroids.py` | Asteroid D, pv, η → `data/asteroid_sizes.json`. |
| `comet.py` | 3I/ATLAS apertures, spectrum, CO2 / H2O / CO / C–H bands → `data/comet_3i_gas.json`. |
| `data/solar_spectrum.csv` | CALSPEC `sun_mod_001` (Kurucz model) rebinned to R ≈ 200, 0.4–5.5 µm, scaled to 1361 W m⁻² at 1 au. |

Aperture: asteroid fluxes use the 2 px radius with the core's 1.033 aperture correction
(`skyshift_core.photometry.aperture_correction(2)`, from NASA's ePSF). The 3 px flux is also
measured; the median 3 px / 2 px ratio per detector is in the results below as a check.

## 1. Asteroid diameter and albedo (`asteroids.py` → `data/asteroid_sizes.json`)

**Data.** 12 asteroids from MOST's 1–60 list: Ceres plus 11 with V ≥ 11.4, 250–460 images each and
mixed taxonomy (C, B, Ch, S, L, X). Cutouts of 23 × 23 px (2.4′) at MOST's predicted position; D3–D6
for most (the fit uses ≥ 2.2 µm), D1–D6 for the first ones fetched. 4,000 cutouts, ~210 MB in all
(IRSA cutout service, and range reads of the public S3 copy for the same box when IRSA was slow;
both give identical pixels). Centroid within 1.5 px of MOST; 2 px aperture × 1.033; points with any
mask bit within 2 px are dropped (saturation included). 1,800+ points kept.

**Geometry.** JPL Horizons, geocentre (500), interpolated from a 12 h ephemeris; H and G from
Horizons. SPHEREx is in low Earth orbit, < 10⁻⁴ au from the geocentre: negligible for main-belt
objects. It agrees with MOST's own r, Δ and phase (max differences in the JSON).

**Model.** Flux = reflected sunlight + NEATM.
- Reflected: CALSPEC solar model / r² × p(λ) Φ_HG(α) (D/2Δ)², with pv D² fixed by H
  (D = 1329 km / √pv × 10^(−H/5)) and p(λ)/pv linear in λ (2 free parameters). So reflected light
  does not depend on D; D comes from the thermal tail.
- Thermal: NEATM, ε = 0.9, Bond albedo q pv, q = 0.290 + 0.684 G.
- Fit 2.2–5.05 µm without 2.65–3.45 µm (hydration, organics); 5 % error floor (lightcurves,
  calibration); 4σ clipping.
- **η.** SPHEREx sees an asteroid near quadrature: the morning side in one visit, the warmer
  afternoon side six months later. With one η for all visits the D5–D6 points split into two
  groups ~30 % apart (Kalliope). So each visit gets its own η (free fit: one D) or its own D (η = 1).
  Rule: adopt the free fit when every visit's η has σ < 0.15 and lies in 0.6–2.0. Otherwise use
  η = 1.0, the NEOWISE/IRAS/AKARI average for large main-belt asteroids, and combine the visit Ds
  as the mean of ln D. The error then includes half the visit-to-visit spread. 3.8–5 µm is the
  Wien tail, where D and η trade off, so the free η runs to its bound (0.4) for the S/X types.
- Ceres: 208 of 326 images saturated, 63 more without a centroid (masked core). Only 14 points
  survive, so there is no fit. A PSF-wing fit would be needed.

| asteroid | type | points | r (au) | fit | η per visit | D (km) | published D | ratio | pv | published pv | 3 µm depth vs S/X control |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 Astraea | S | 252 | 2.79–3.06 | η=1 | 1.00 | 123 ± 10 | 107 (NEOWISE) | 1.15 | 0.193 ± 0.030 | 0.274 | +0.02 ± 0.05 |
| 10 Hygiea | C | 148 | 3.36–3.46 | free | 0.88/0.84 | 392 ± 53 | 407 (IRAS) | 0.96 | 0.063 ± 0.017 | 0.072 | +0.06 ± 0.04 |
| 12 Victoria | L | 166 | 2.04–2.51 | free | 0.80/1.02 | 106 ± 16 | 115 (NEOWISE) | 0.92 | 0.188 ± 0.056 | 0.163 | −0.05 ± 0.05 |
| 16 Psyche | X | 125 | 2.58–2.78 | η=1 | 1.00 | 230 ± 45 | 222 (Shepard+ 2021) | 1.04 | 0.111 ± 0.043 | 0.120 | −0.01 ± 0.05 |
| 21 Lutetia | Xk | 176 | 2.15–2.83 | η=1 | 1.00 | 138 ± 6 | 98 (Rosetta) | 1.41 | 0.095 ± 0.009 | 0.190 | −0.06 ± 0.05 |
| 22 Kalliope | X | 194 | 2.63–2.91 | η=1 | 1.00 | 184 ± 25 | 168 (NEOWISE) | 1.10 | 0.100 ± 0.027 | 0.166 | +0.14 ± 0.05 |
| 24 Themis | B | 201 | 2.81–3.29 | free | 0.87/0.87/0.89 | 150 ± 17 | 198 (SBDB) | 0.76 | 0.103 ± 0.023 | 0.067 | +0.04 ± 0.04 |
| 41 Daphne | Ch | 199 | 2.24–2.78 | free | 1.09/0.95 | 204 ± 36 | 206 (NEOWISE) | 0.99 | 0.039 ± 0.014 | 0.059 | +0.07 ± 0.05 |
| 51 Nemausa | Ch | 159 | 2.29–2.43 | free | 1.14/1.09 | 168 ± 31 | 138 (NEOWISE) | 1.22 | 0.051 ± 0.019 | 0.100 | **+0.25 ± 0.06** |
| 52 Europa | C | 161 | 2.81–2.99 | free | 0.91/0.88 | 348 ± 30 | 304 (NEOWISE) | 1.15 | 0.032 ± 0.005 | 0.057 | +0.00 ± 0.04 |
| 59 Elpis | B | 270 | 2.46–2.68 | free | 0.98/0.94/1.06 | 174 ± 10 | 165 (NEOWISE) | 1.06 | 0.034 ± 0.004 | 0.044 | +0.05 ± 0.04 |

Published values: JPL SBDB `phys_par`, which adopts one source per object (reference in the JSON).

**Ours against published (11 asteroids).**

| | median | 16–84 % | scatter (MAD) | rms of log10 |
|---|---|---|---|---|
| D, adopted | **1.06** | 0.95–1.18 | 14 % | 0.071 |
| pv, adopted | **0.71** | 0.54–1.01 | 26 % | 0.190 |
| D, η = 1 for all | 1.07 | 1.01–1.29 | 12 % | 0.072 |
| D, free η for all | 0.92 | 0.59–1.09 | 33 % | 0.157 |

- **D within ~15 %.** That is good for a model fitted only to the Wien tail.
- **The worst case is Lutetia** (+41 %): an M/Xk type, with η fixed at 1.
- **pv comes out ~30 % low.**
  - About 12 % comes from D being 6 % high (pv ∝ D⁻²).
  - The rest is probably mostly H. We use Horizons' current H. The published albedos were derived
    with the H of their time, and a change of 0.2–0.3 mag in H is 20–30 % in pv. Not checked
    object by object.

**3 µm band.** Every asteroid shows an apparent 5–10 % dip at 2.9–3.2 µm, including the S and X
types. So the absolute depth carries a shared systematic: the solar model, the D4 calibration, or
the linear continuum. We therefore measure depth against the S/X/L control (5 asteroids, mean
0.047 ± 0.033).
- **51 Nemausa (Ch) has a 3 µm band at 4.6σ**, depth 0.25. Ch types are the hydrated (phyllosilicate)
  asteroids.
- **22 Kalliope shows +0.14 (2.8σ).**
- **Hygiea, Themis, Daphne and Elpis** are +0.04 to +0.07 (1.2–1.7σ): suggestive of their known
  3 µm features, but not significant here.
- **Ceres**, the textbook 3.06 µm case, saturates in D4.

**Aperture check.** Median 3 px / 2 px flux (2 px already × 1.033): D3 0.995, D4 1.014, D5 1.013,
D6 1.026. So the 1.033 correction holds to ~1–3 % in D3–D6 (D6 is a little more extended).

**Saturation (for CLAUDE.md "not yet verified").** These are the brightest unsaturated asteroid
points, with no mask bit within 2 px:
- AB 10.5 in D3;
- AB 10.9 in D4;
- AB 9.6 in D5;
- AB 8.6 in D6.

Psyche, Victoria and Ceres lose 10–64 % of their points to OVERFLOW/NONLINEAR in the core.

## 2. Comet 3I/ATLAS gas (`comet.py` → `data/comet_3i_gas.json`)

**Data.** Every image in IRSA's 3I/ATLAS table: 258 cutouts of 35 × 35 px (3.6′), centred on NASA's
comet position. 236 points were kept; 21 had a masked pixel within 2 px and 1 was saturated.
- Visit 1 is 1–15 Aug 2025 (r = 3.22 au, Δ = 2.71 au, α = 17°).
- Visit 2 is 8–15 Dec 2025 (r = 2.14 au, Δ = 1.81 au, α = 27°).

Photometry:
- Apertures of 1, 2, 3 and 5 px, i.e. 6–31″ (12,000–60,000 km in August).
- Background: sigma-clipped median in a 12–17 px annulus.
- Masked pixels beyond 2 px are filled with the median at the same radius.
- Each point is scaled to the visit's median r, Δ by (r/r₀)²(Δ/Δ₀).

**Band excess.** For each band:
1. Fit a straight continuum to the side windows, rejecting outliers at 3σ (stars cross the
   aperture: the August field is the crowded Ophiuchus/bulge direction).
2. Integrate the residual with a trapezoid over channel-centre wavelength. This gives the line flux
   whatever the channel width.
3. Errors: measurement noise plus the side-window jitter, plus the continuum error.
   `err_total_w_m2` adds 15 % for the sparse channel sampling.

**Production rates.**
- Column: N = 4πΔ²F / (g hν), with g = g₁/r².
- Haser parent model with v = 0.85 r^−½ km/s and L = v τ₁ r². The gas that the background annulus
  removes is added back with the same model (3–16 %).
- g₁ (Crovisier 1997, the values Lisse et al. used): CO2 2.6e-3, CO 2.6e-4, H2O 2.9e-4.
- τ₁ (Huebner et al. 1992, quiet Sun): 5.0e5, 1.3e6 and 8.3e4 s.
- Systematics are about a factor of 2 (g, τ, v, and coma shape).

| visit | band | flux, 2 px (W m⁻²) | significance | Q, 2 px (s⁻¹) | Q, 5 px (s⁻¹) | SPHEREx team (Lisse et al.) |
|---|---|---|---|---|---|---|
| Aug 2025 | CO2 4.26 µm | 5.4e-16 | 54σ | **1.3e27** | 1.4e27 | 1.6e27 ± 10 % [B]; 9.4e26 [A] |
| Aug 2025 | H2O 2.7 µm | 3.8e-17 | 2.3σ | 5.3e26 (< 1.2e27, 3σ) | not detected | 3.2e26 ± 20 % [B] |
| Aug 2025 | CO 4.67 µm | 7.8e-18 | 1.4σ | 2.0e26 (< 6e26, 3σ) | 2.8e26 | 1.0e26 ± 25 %, tentative [B] |
| Aug 2025 | C–H 3.2–3.6 µm | 7.0e-18 | 0.3σ | – | – | none reported |
| Dec 2025 | CO2 | 4.4e-15 | 48σ | **3.8e27** | 4.0e27 | 3.0e27 ± 0.45 [C] |
| Dec 2025 | CO | 6.9e-16 | 41σ | **6.5e27** | 6.8e27 | 7.6e27 ± 1.5 [C] |
| Dec 2025 | H2O | 2.3e-15 | 11σ | **1.2e28** | 1.4e28 | 1.4e28 ± 0.28 [C] |
| Dec 2025 | C–H (organics) | 5.5e-16 | 19σ | (no g-factor) | – | 2.0e27 with g = 3e-4 [C] |

[A] Lisse et al. 2025, RNAAS 9, 242 (arXiv:2508.15469). [B] Lisse et al. 2025/26, ApJL
(arXiv:2512.07318). [C] Lisse et al. 2026, arXiv:2601.06759 (preliminary).

**Findings.**
- **CO2 dominates in August**, as the SPHEREx team found.
- **Water and CO are not detected in August.** Our ~2σ H2O is consistent with their 3.2e26.
- **December after perihelion:** CO2 rose ~3×, and H2O and CO by > 10× against our August 3σ
  limits (~40× and ~65× against the team's August values). A strong 3.2–3.6 µm C–H band appears.
- **Every December rate agrees with the team's to within 25 %.** That is closer than our factor-2
  systematics. We use the same g-factors and the same 2 px aperture, so the agreement checks the
  photometry and the band extraction, not the gas model.
- **Q rises from the 1 px to the 5 px aperture** (CO2 in August: 9.9e26 → 1.4e27). Either the coma
  is more extended than a Haser parent at 0.48 km/s, or the 2 px PSF core under-represents it.

## Caveats
- **Asteroid fit**
  - The thermal fit sees only the Wien tail (3.8–5 µm), so D and η are degenerate. Adopted
    D values use a stated rule; both fits are in the JSON.
  - The reflectance is assumed linear in λ from 2.2 to 5 µm. Its extrapolation sets how much of the
    D5–D6 flux is thermal.
  - Lightcurves are not modelled (5 % floor only).
  - pv depends on H from Horizons (no uncertainty available).
- **Ceres:** no fit, because of saturation.
- **3 µm band:** the absolute depths share a ~5 % systematic, so only depths relative to the S/X
  control are meaningful.
- **Comet**
  - August photometry is in a dense star field. Stars are rejected only in the continuum
    windows; a star inside a band window adds to the band flux (the H2O and CO limits are the
    most affected).
  - The December visit has only 83 points, i.e. 4–6 channel samples per band (hence the 15 %
    sampling term).
  - The 12–17 px background annulus still holds gas, because the CO2 coma reaches ≥ 3′. The
    Haser correction assumes a spherical parent coma.
  - Fluxes are scaled to a common r, Δ within each visit, assuming steady activity. The comet
    brightened during both visits.
- **No contract or core changes.** server/src/skyshift_server/cutouts.py did not exist on main, so
  `cutouts.py` has its own fetcher.
