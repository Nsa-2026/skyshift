# Featured targets: is the data right? (Sat 14 Nov 2026, 14:00–16:00)

Problems found by the web card agent and scripts/demo_check.py on web/public/data/<slug>/object.json.
Each was checked on the cached cutouts (all exposures, all detectors) against catalogue photometry
from IRSA: 2MASS PSC (`fp_psc`) and AllWISE (`allwise_p3as_psd`), Vega zero points J 1594, H 1024,
Ks 666.7, W1 309.5, W2 171.8 Jy. Barnard's Star's 2MASS row isn't in a cone at its moved position, so
J 5.24, H 4.83, Ks 4.52 (2MASS, Cutri et al. 2003) are used. Code: server/src/skyshift_server/obj.py
(`point_photometry` and the three functions it calls), tests: server/tests/test_spectrum_photometry.py.

## Summary

| Target | Was | Cause | Now |
|---|---|---|---|
| R Draconis | 0 spectrum points | r = 2 px aperture fully masked in every exposure (NaN + OVERFLOW core, SUR_ERROR/TRANSIENT around it); sep then gave NaN or raised on a fully masked sky annulus | 500 points from the PSF wings, all `saturated`, ±50% |
| Barnard's Star | 101 points (D1, D3, D5, D6), 100 saturated, 0.13–1.3 Jy | Aperture with its core masked: reads 4–10× low. D2 and D4 had none (aperture fully masked) | 353 points in all six detectors from the wings, all `saturated`, 5–20 Jy, ±50% |
| WISE 0855−0714 | ~1 mJy in D1–D3 | A Gaia DR3 star (G = 16.3) 11″ → 8″ → 4″ from it in visits 1 → 2 → 3, inside the 12.3″ aperture | Joint fit of both: D1–D4 0.00 ± 0.04 mJy (not detected), D5 0.33, D6 0.34–0.46 mJy. Visit 3 (4″) left out |
| Epsilon Indi B | "10 mJy, too bright for G = 18" | Nothing wrong: a T dwarf pair is very red. Matches 2MASS and AllWISE | Unchanged except 41 points with a masked but unsaturated core, now left out |
| Asteroids | `visit: 0` | precompute never numbered MOST's images | Visit numbers by core's rule over the asteroid's own exposure times (contract: visit number) |

## 1. R Draconis: empty spectrum

Every one of the 539 exposures has its r = 2 px aperture fully masked: a 3–20 px hole of NaN and
OVERFLOW pixels, then SUR_ERROR + TRANSIENT pixels out to r ≈ 4–5 px. `aperture_photometry` gave
NaN (sep flag APER_HASMASKED, nothing left to sum) in 444 and raised "background annulus does not
contain any valid pixels" in 83; 12 were at the cutout edge. spectrum_points dropped all of them.

## 2. Barnard's Star: saturated points read low

Same picture, smaller: 1–7 OVERFLOW/NaN pixels at the centre, SUR_ERROR + TRANSIENT on the rest of
the aperture. The aperture sum leaves them out, so the old values (D5 1.2 Jy, D6 0.85 Jy) were 4–7×
below AllWISE W2 (5.9 Jy), and in D2/D4 the whole aperture was masked (no points).
The `saturated` flag itself was right per point (OVERFLOW or NONLINEAR within 1.5 px); one D6
point was not flagged but had 11 of 20 aperture pixels masked and also read low.

### Wing photometry (new, server obj.wing_photometry)

The ePSF in the files only reaches ±3.2 px and is normalised inside that stamp, so it can't fit a
star whose usable pixels start at 3 px. Instead an empirical radial profile per detector:
pixel value / total flux at r = 1.75–4.5 px, the median over 30–55 isolated unsaturated stars of
50–150 mJy per detector in the five targets' cutouts (total = the usual r = 2 px aperture × 1.033).
Per pixel ×1e-4 at r = 2.25 / 3.25 / 4.5 px: D1 13 / 3.9 / 2.9, D2 16 / 5.1 / 2.6, D3 17 / 4.9 / 2.6,
D4 28 / 6.9 / 2.0, D5 36 / 8.8 / 2.9, D6 46 / 7.7 / 2.0. Beyond ~5 px the field stars are in the noise.

flux = median over pixels at r = 2–4.75 px of (pixel − sky) / profile(r), sky = median at 7–11 px.
Pixels used: finite, no mask bit except TRANSIENT and SUR_ERROR (bright stars set these on their own
wings; the values follow the profile, see the grids below), and not touching the NaN/OVERFLOW/NONLINEAR
hole. This departs from contracts/flags.md's mask list for these two bits, only here, only for a core
that is already saturated or masked. Without them R Draconis keeps too few pixels (D4–D6 none).

Barnard's Star, D2, one exposure (MJy/sr; S = SUR_ERROR, T = TRANSIENT, O = OVERFLOW):

```
   9.5   14.6T  24.2ST 109.6ST 1875.9O    nanOS 160.4ST  18.3ST   5.8
   8.4T  14.0T  28.8ST 399.4ST    nanOS   nanOS 600.9ST  22.0ST   6.3
   6.9   10.2T  16.5ST  84.2ST  665.1ST 726.9ST 101.1ST   9.9ST   5.3
```

Check against catalogues (median over all exposures, Jy):

| | D1 0.75–1.1 | D2 1.1–1.6 | D3 1.6–2.4 | D4 2.4–3.8 | D5 3.8–4.4 | D6 4.4–5.0 |
|---|---|---|---|---|---|---|
| Barnard old (aperture) | 0.15 (1 pt) | – | 0.13 (1 pt) | – | 1.2 | 0.85 |
| Barnard wings | 13 | 13–18 | 20 | 17–18 | 6.3–6.6 | 5.3–5.9 |
| Catalogue | I 4.9 … J 12.8 | J 12.8 | H 11.9, Ks 10.4 | Ks 10.4, W1 5.6 | W1 5.6, W2 5.9 | W2 5.9 |

So the wings are right to ~10–25% in D2, D5, D6, and 1.7–2× high in D3–D4. Moving the outer radius
from 4.75 to 3.5 px changes results by ~25%. Hence `err_ujy` = statistical ⊕ 50% of the flux, and
the points keep `saturated: true` (contract: OVERFLOW or NONLINEAR in the core).
Within one detector the systematic is common to all visits, so visit-to-visit changes (light
curve) are much better than 50%.

R Draconis (Mira; 2MASS 1999 single epoch J 45, H 58, Ks 71 Jy; WISE W2 153 Jy, saturated in WISE):
wings give D2 59–102, D3 71–178, D5 91–121, D6 103–190 Jy, falling steadily over the visits in every
detector (D3 178 → 168 → 115 → 71 Jy; D6 190 → 158 → 128 → 103 Jy): the pulsation is visible.

RW Aurigae (variable T Tauri star, deep dimmings since 2010) also moved to the wings in 303 of 321
points: D2 0.16–0.23, D3 0.39–0.46, D4 1.3–2.1, D5 1.0–2.1, D6 1.9–3.8 Jy (old aperture 0.07–0.98 Jy;
2MASS J 0.71, H 0.92, Ks 1.04; AllWISE W1 0.98, W2 1.39 Jy). Its 18 unsaturated D1 points are ~1.3×
the old values (masked pixels no longer left inside the aperture). D4–D6 fall from visit 1 to 3.

The wings are used only when the core is saturated (OVERFLOW or NONLINEAR within 1.5 px). A core
masked for another reason (a cosmic ray, a bad pixel) on an unsaturated source drops the point: a
wing estimate there carries ±50%, so the card's 2σ test would draw a clear detection as an upper limit.
The card (web/src/card/format.ts `isUpperLimit`) now also treats a saturated point with positive flux
as a detection, never as an upper limit; before that, every wing point was drawn as a limit and the
light curves of Barnard's Star, RW Aurigae and R Draconis were empty.

## 3. WISE 0855−0714: light in D1–D3

The aperture flux rose visit by visit in D1–D3 (D1 0.53 → 0.94 → 1.07 mJy) while a 250 K brown dwarf
should be dark there (2MASS-era J ≈ 25). Gaia DR3 has a G = 16.3 star on its path: 11.1″ away in
visit 1, 7.4–8.3″ in visit 2 and 3.9–4.1″ in visit 3 (WISE 0855 moves 8.1″/yr). The position used
was right (moved from J2016 with proper motion and parallax); the aperture (r = 12.3″) simply
contained the star.

Fix (obj.blended_photometry): when a Gaia star (other than the target, and for a Gaia target not more
than 4 mag fainter) is 5–15″ away at that exposure's time, fit the target and the star(s) together
at their known positions: pixel-integrated Gaussians (σ = 0.38 px D1–D3, 0.46 D4–D5, 0.50 D6, fitted
to 120–450 isolated stars at S/N > 50) plus a flat sky, weighted by VARIANCE, scaled to the aperture
flux (×1.09–1.11 per detector, ×1.033). Closer than 5″ the two can't be separated: for a target Gaia
doesn't see (WISE 0855) the point is left out; a Gaia target's close companion stays in as one blend
(RW Aur A+B, 1.4″, already noted in targets.json).

| WISE 0855, mJy | D1 | D2 | D3 | D4 | D5 | D6 |
|---|---|---|---|---|---|---|
| old, visit 1/2/3 | 0.53/0.94/1.07 | 0.50/0.80/0.87 | 0.27/0.50/0.51 | 0.11/0.22/0.26 | 0.59/0.41/0.49 | 0.36/0.63/0.52 |
| new, visit 1/2 | 0.03/0.02 | −0.02/0.01 | 0.04/−0.01 | 0.00/0.03 | 0.33/0.33 | 0.34/0.46 |
| typical error | 0.04 | 0.03–0.04 | 0.03 | 0.03 | 0.09 | 0.15 |

D1–D4 are now consistent with zero (the site draws them as upper limits); D5–D6 are 2–4σ detections
per exposure, close to AllWISE W2 0.57 mJy and Spitzer [4.5] ≈ 0.5 mJy. Points: 256 → 138 (98 in
visit 3 left out for the 4″ star, 22 with a masked core on a faint source left out).
The motion measurement was already restricted to D5–D6 (MEASURE_DETECTORS) and is unchanged.

## 4. Epsilon Indi B: correct

No Gaia star within 20″ in any visit. 2MASS (pair): J 27.5, H 30.8, Ks 21.9 mJy. Our spectrum peaks at
41 mJy at 1.27 µm, 38 at 1.6 µm, 30 at 2.1 µm, with the water (1.35–1.45, 1.8–1.95 µm) and methane
(2.2+, 3.3 µm) troughs between: band averages match 2MASS. AllWISE W1 10.72 (16 mJy), W2 9.49 (27 mJy)
against our D4 9–14 mJy at 2.5–3.4 µm rising to 46 at 3.8 µm, D5 50–73 mJy at 3.8–4.1 µm and D6 15–30
mJy at 4.4–5.0 µm: consistent. (The brief's W1 ≈ 9.5 and W2 ≈ 7.9 are not AllWISE's values.) A T dwarf
pair at 3.6 pc is ~10⁴× brighter at 1–4 µm than at G, so 20–50 mJy for G = 18 is expected.
The earlier "visit 1 is different" was wavelength sampling (D1 visit 1 has 3 points, all at 1.07–1.09 µm,
the Y-band peak). Change: 41 points whose core had a masked pixel (cosmic ray, bad pixel) are left
out (the aperture read them low); 425 → 416 points against the first version of this fix.
Side effect: pick_detector ranks detectors by the spectrum's S/N, so the motion measurement moved to
another detector: 6.05″ ± 0.42″ (4.27″/yr) → 6.12″ ± 0.49″ (4.35″/yr); Gaia predicts 6.49″.
Barnard's Star, WISE 0855 and R Draconis keep the same measurement.

## 5. Asteroid `visit`

contracts/api.md: `visit` = visit number. Elpis and Euphrosyne had 0 because precompute built their
images from MOST rows without numbering them. Now `search.number_visits` (core's visit rule) runs over
each asteroid's own exposure times, as for a fixed position: Euphrosyne's 30-day spectrum window is
visit 1, Elpis's is visit 3 (its third season in SPHEREx). 3I/ATLAS already had visits 1–2 from NASA's
table.

## What remains

- Wing fluxes are good to a factor ~2 at worst (D3–D4 high on Barnard's Star); they carry ±50% and
  `saturated: true`. The card now says "measured on the wings, ±50%" (changed by the lead).
- `spectrum_reason` (contract v3) is null for every featured target now; R Draconis was "saturated".
  An exposure left out still counts as "saturated" or "other" for it.
- A brighter calibrator than the 50–150 mJy field stars (e.g. Epsilon Indi A, K ≈ 2, in the 15′ D2/D5
  frames) would fix the profile beyond 3 px and the D3–D4 offset.
- The blend fit uses a Gaussian PSF; a star brighter than the target 5–8″ away still leaves a few
  % of its flux in the target's fit.
- Live `/api/object` uses the same code. Extra time per call is small: one pass over the Gaia cone to
  find stars whose track comes within 18″ of the target (2 propagations a star), then only those.
