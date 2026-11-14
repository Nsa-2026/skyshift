# All-sky test of `dots` v4 on seven sky types (14 Nov)

**Question.** Does the extraction (dot table v4 with the ePSF position fit, PR #52) hold up on sky types the
pilot and hunt zones never saw? Those zones are at RA 120–130°, Dec 14–24°: b ≈ +25°, on the ecliptic.

**How.** Seven test zones of 0.5° × 0.5° on the sky in `config.ZONES` (`t_*`). Every 3.5° photo covers the whole
box. `photolist` and `refcat` ran from a laptop, writing to the bucket. `dots --limit 10 --workers 4` ran on
skyshift-test (e2-standard-8). Only 3 workers were busy, because 10 photos make 3 tasks of up to 4. The CPU
contention was therefore lower than in the pilot, which ran 8 workers on 4 physical cores. `--limit` now spreads
the photos over detectors and dates (below). Independent checks used each field's refcat box (zone + 0.1°), so
they cover about 4% of each photo. Astrometry: dots with S/N ≥ 30, unsaturated, nearest Gaia DR3 within 1.5″
(moved by proper motion to the photo's MJD), no Gaia star within 4″ that is brighter than G + 2, and each photo's
median offset removed. Flux: unsaturated stars with S/N > 20 matched to isolated 2MASS AAA stars. The reference is
2MASS J/H/Ks in AB (Vega → AB: +0.91, +1.39, +1.85), interpolated log-log to each dot's wavelength (1.15–2.28 µm).
Machine time: 13:31:47–14:03:56 UTC, 32.2 min, **€0.145** at €0.27/h.

## Results per field

Compute = detect + forced (s per photo, median and max). RSS = peak memory of one worker process, sampled every 3 s.

| Field | Photos in list (detectors), visits | Dots/photo median (max) | Compute s/photo | of which wings recentroid / ePSF position (s) | Peak RSS (GB) | Failed | Saturated share | sep blended | psf_pos_ok (unsaturated) |
|---|---|---|---|---|---|---|---|---|---|
| Pilot (reference, v3, 8 workers) | 990 (D1–6), 2 | 21k | 11.3 | – | – | 0 | – | 5–26% | 98–99% |
| N Galactic pole (192.86, +27.13) | 516 (D1–6), 3 | 14.5k (18.2k) | 7.3 (9.6) | 1.3 / 1.1 | 1.00 | 0/10 | 0.9% | 9.9% | 98.7% |
| N ecliptic pole (270, +66.56) | 976 (**D2, D5 only**), 17 | 23.3k (36.7k) | 13.5 (25.0) | 2.2 / 1.8 | 1.06 | 0/10 | 0.9% | 16.9% | 97.8% |
| S ecliptic pole (90, −66.56) | 1,257 (**D2, D5 only**), 18 | 39.0k (61.1k) | 24.9 (50.7) | 2.4 / 3.2 | 1.10 | 0/10 | 0.6% | 30.9% | 98.2% |
| SMC (13.2, −72.8) | 795 (D1–6), 4 | 40.6k (50.4k) | 26.7 (34.4) | 3.0 / 3.3 | 1.16 | 0/10 | 0.7% | 37.1% | 98.4% |
| LMC bar (80.9, −69.8) | 1,600 (D1–6), 18 | 60.6k (70.4k) | 49.7 (66.7) | 5.4 / 5.1 | 1.17 | 0/10 | 0.8% | 57.5% | 98.4% |
| Cygnus (305, +40) | 544 (D1–6), 3 | 59.9k (67.0k) | 64.6 (83.3) | 19.8 / 5.0 | 1.21 | 0/10 | 3.4% | 65.2% | 97.9% |
| Galactic centre (266, −29) | 348 (D1–6), 3 | 58.1k (69.0k) | **207.7 (422.7)** | **171.1** / 4.6 | 1.15 | **1/10** | **14.6%** | 47.9% | 96.3% |

| Field | Per-axis scatter vs Gaia (RA/Dec ″): centroid → ePSF | Pull of pos_err_psf | Largest per-photo offset (ePSF, ″) | Aperture / 2MASS | PSF / 2MASS | Forced targets per deg² → per full photo |
|---|---|---|---|---|---|---|
| Pilot | 0.44–0.59 (all S/N ≥ 10) → 0.10–0.18 (S/N 50–300) | 0.8–1.2 | ±0.16 (σ photo-to-photo) | J 0.995, H 1.037, Ks 1.022 | 1.01–1.06 | 2,110 (G < 17.5) |
| NGP | 0.38/0.46 → **0.15/0.13** (n 782) | 1.00/0.89 | 0.18 | 1.02 (H 1.04, Ks 0.98) | 1.05 | 756 → 9.3k |
| NEP | 0.43/0.48 → **0.15/0.14** (633) | 0.98/0.95 | 0.29 | 1.005 (J 1.00, H 1.06) | 1.02 | 1.9k → 23.6k |
| SEP | 0.51/0.53 → **0.18/0.18** (3,184) | 1.15/1.23 | 0.11 | 1.08 (J 1.05) | 1.03 | 5.7k → 69k |
| SMC | 0.49/0.43 → **0.18/0.19** (2,671) | 1.14/1.25 | 0.13 | 1.11 (J 1.05, H 1.14, Ks 1.09) | 1.04 | 40k → **492k** |
| LMC | 0.45/0.46 → **0.19/0.18** (1,905) | 1.21/1.14 | 0.16 | 1.13 (J 1.05, H 1.17, Ks 1.12) | 0.97 | 72k → **879k** |
| Cygnus | 0.41/0.50 → **0.17/0.18** (4,139) | 1.15/1.18 | 0.33 | 1.06 (J 1.06, Ks 1.06) | 0.99 | 10.4k → **128k** |
| Galactic centre | 0.43/0.62 → **0.17/0.17** (1,793, 97% D1) | 1.14/1.14 | 0.21 | 1.06 (only 12 stars) | 0.95 | 22k → **275k** |

Other checks:
- `sep_flag`: deblend overflow (4) was 0 everywhere. Truncated was 0.4–0.7%, singular 0.2–1.0%. Dots with
  elong > 3 were 0.1–0.8%. Nothing absurd: crowded fields give ~3× the pilot's dots per photo, with dot files
  up to 9 MB.
- SOURCE at the peak: 98–100% of dots in every field. This is the same as the pilot (six pilot dot files:
  98.1–100%). SOURCE is set wherever there is a source, so `suspects`' SOURCE_PHOTO_MAX_FRAC = 0.9 rule ignores
  almost every photo, in the pilot too.
- FINAST ≠ 0: none. ZODI median 0.11–0.22 MJy/sr. The background median is 0.14–0.59 MJy/sr, but 8.8 in the
  Galactic centre.
- Ghost/halo/crosstalk bits are on the dots as designed; no field showed a burst of them.
- Forced photometry is cheap: about 16 µs per target (21.6k targets in 0.35 s). Forced targets per photo were
  only those inside the 0.5° zone, at most 22.6k.
- Memory did not grow with crowding: 1.00–1.21 GB per worker everywhere. The machine total was 3.4–3.9 GB with
  4 workers.

## What broke or looks risky for the whole-sky run

1. **Fixed in this PR: a photo fails when one dot's aperture background annulus is fully masked.** Galactic centre
   2025W35_1A_0323_4D2 failed with `ValueError: The background annulus does not contain any valid pixels, for the
   object at index 0`. Object 0 is the brightest dot: a bright star whose OVERFLOW mask reaches beyond 8 px.
   `sep.sum_circle(..., bkgann=...)` then raises for the whole call, and the photo is skipped on every rerun. Any
   photo with such a star does the same across the sky. `dots._sum_circle_bkgann` now drops the object that sep
   names, retries, and leaves NaN in that dot's `flux_ap_*`. A test is added. I re-ran the failed photo locally with
   the fix: see the PR.
2. **Galactic bulge and plane: the saturated-dot wings fit dominates the time.** In the Galactic centre OVERFLOW
   covers wide areas: 243k pixels (5.8%) on 2026W36_1A_0067_1D4, against ~0.07% in the pilot. Half of that photo's
   45k dots have OVERFLOW under them at S/N ~11 and peak ~32 MJy/sr. These are not saturated stars.
   `recentroid_saturated` fits each one in Python (~16 ms per dot): 353 s for that photo, 171 s median, 208 s per
   photo in all (pilot: 11 s). In Cygnus it is 20 s of 65 s. *Proposed fix:* wings-fit only dots with the bits at
   the **peak** and S/N ≥ ~50, capped at the brightest ~2,000 per photo. The others keep the windowed centroid, and
   flags/flags_peak still mark them. This is a science change (contracts/dots.md says "saturated dots re-centroided
   on the wings"), so it is not done here.
3. **The forced list explodes at low latitude and in the Magellanic Clouds.** At the current limits (Gaia
   G < 17.5 plus red CatWISE), a full 3.5° photo would get 69k targets (SEP), 128k (Cygnus), 275k (Galactic
   centre), 492k (SMC) or 879k (LMC). Only the two high-latitude boxes stay under FORCED_MAX_PER_PHOTO = 30k (NGP
   9k, NEP 24k). Time is not the problem (~14 s for 879k). Memory is: psf_photometry builds (n, 7, 7) float64
   arrays, ~345 MB each at 879k targets, several of them, on top of the ~1.2 GB worker. That would break a
   1 GB/vCPU machine. *Proposed fix:* a per-tile density cap in `refcat.forced_list`: the G limit per 1° tile is
   min(17.5, G of the 2,400th brightest star per deg²), so a photo stays under ~30k. Or skip forced photometry in
   tiles above a density.
4. **Memory on n2-highcpu (1 GB per vCPU).** The peak per worker is 1.0–1.2 GB in every sky type, with two photos
   in memory during prefetch. 60 workers on 64 GB is too tight if peaks line up. Use ≤ 48–50 workers per 64 GB,
   or set DOTS_PREFETCH = False (one photo per worker) to fit more.
5. **Time per photo scales with dots (~0.6–0.8 ms per dot) plus the wings fit.** High latitude: 7–14 s. Ecliptic
   poles, SMC and LMC: 25–50 s. Cygnus: 65 s. Galactic centre: 208 s. Budget the plane and bulge at 3–20× the
   high-latitude cost per photo (Galactic centre ~4× after fix 2).
6. **Ecliptic poles see only D2 and D5.** Every DR1/QR3 photo touching the NEP and SEP boxes is D2 or D5
   (NEP 487 + 489, SEP 636 + 621). Sources there get spectra at 1.1–1.6 and 3.8–4.4 µm only: 34 of 102
   channels. The radius of that area wasn't measured; the pole box is 0.5°. Photo counts are moderate, not huge:
   ~1,000–1,300 per 0.5° box, 17–18 visits after the 30-day cut. The LMC, 3.5° from the SEP, has all six
   detectors and 18 visits.
7. **Catalogue services are the whole-sky bottleneck.** ESA Gaia sync answered 1° tiles in 4–60 s (234k rows in
   61 s in the LMC). IRSA CatWISE/2MASS took 60–360 s per tile with 7 zones in parallel. One IRSA async job (SEP
   CatWISE) hung over 16 min with no timeout; restarted, it finished in 50 s. *Proposed fixes:* a per-query
   timeout (~300 s) in `refcat._tap` so the retry loop can work. For the whole sky (~41k 1° tiles × 2 IRSA
   catalogues), get the catalogues in bulk, not by TAP tile. Photolist TAP took ~150 s per zone.
8. **REFCAT_MAXREC (2M) was not hit.** The largest tile was 234k Gaia rows. But Gaia G < 20 reaches ~1M/deg²
   in the LMC bar (996k/deg² in the zone), so a full 1° tile there is ~1M rows, half the limit. The densest bulge
   windows may come close. The Galactic centre itself has only 44k/deg² (extinction) but 158k/deg² in 2MASS.
9. **The aperture flux reads high in crowded fields.** Aperture / 2MASS is 1.00–1.02 at high latitude, but
   1.06–1.13 in Cygnus, the SEP, SMC and LMC, with 9–15% scatter (pilot 7%). The ePSF flux stays at 0.97–1.04.
   Unresolved neighbours fill the r = 2 px aperture. Prefer flux_psf_ujy where `sep_flag & 1` is set or the
   field is crowded; calib's per-zone aperture factor absorbs the mean but not the scatter.
10. **The ePSF positions hold everywhere.** psf_pos_ok is 96–99% of unsaturated dots, and the scatter vs Gaia is
    0.13–0.19″ per axis against 0.38–0.62″ for the centroid. The pull of pos_err_psf_arcsec is 0.9–1.0 at high
    latitude but 1.14–1.25 in crowded fields. The 0.13″ floor is too small there: a ~0.17″ floor, or ×1.2 where
    blended, would bring the pulls to 1.
11. Minor: psf.py:103–104 warns `invalid value encountered` in crowded fields. The NaN stamps end as NaN fluxes,
    as intended. Dots log progress per task of 4 photos, so a slow Galactic centre task shows nothing for 9 min.

## Change to `--limit`

`dots --limit N` used to take the first N photos in (mjd, detector) order: two exposures of one visit.
`dots.spread_limit` now gives each detector a round-robin quota and spreads its picks evenly over time. Each
detector is shifted so they don't all pick the same exposures. Example, NGP with 10: D1–D6 over all 3 visits.
The ecliptic poles get 10 visits of their 18.

Data: photolists/zones/t_*.parquet, refcat/t_*.parquet, cache/refcat/t_*/, dots/ and forced/ for the 69 photos
done, results/t_*/ (dots_runs/*.json has per-photo stats). The independent check (match to Gaia/2MASS from refcat,
per-photo metadata) was a one-off script outside the repo, modelled on the pilot check.
