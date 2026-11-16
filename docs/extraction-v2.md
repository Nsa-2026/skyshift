# Extraction v2: what the one rerun of every photo must change and keep

Owner: P1 (agent "extraction", 16 Nov 2026). Code: `pipeline/src/skyshift_pipeline/dots.py`, `psf.py`, `config.py`
(`DOTS_V2`, `MF_*`), `core/src/skyshift_core/glow.py`. Tests: `pipeline/tests/test_extraction_v2.py`,
`core/tests/test_glow.py`. Everything is behind one switch that is off: `SKYSHIFT_DOTS_V2=1` turns it on for every
zone. Nothing that exists changes until then.

The lead's rule: before the next photo extraction, find every problem so it never runs twice. The sky run read
~296,000 photos (~3,400 CPU-hours, dots 887 GB); ~495,000 exist now. This page lists what v2 changes, why, what it
gained on real photos, what it costs, and what later steps get from it.

## 1. The decision in one table

| # | Change | Why | Measured gain (real photos, this Mac) | Cost |
|---|---|---|---|---|
| 1 | **Fine detection v2 for every photo** (deblend 1e-4, clean 3, saturation guard 6 px, ePSF second pass; until now only the 5 featured zones) | Pairs and stars in a bright star's wings | Isolated Gaia stars at aperture S/N 5–5.5: 33% → 58%; Gaia pairs 12–15″ split 0% → 75%; known asteroids V 17–18: 16% → 32% | ~2× the sky setting's detection CPU (with the glow model: 2.7×) |
| 2 | **Matched-filter pass** (new, `MF_PASS_SNR` 5): the ePSF-matched filter on the second pass's residual; each peak ≥ 5σ with no dot within 1 px becomes a dot (sep_flag 0x400) | sep's per-pixel threshold + aperture S/N ≥ 5 loses ~⅓ of the S/N 5–7 stars; the matched filter is the optimal point-source test | Gaia S/N 5–5.5: 58% → 85%; S/N 4.5–5: 17% → 80%; known asteroids V 17–18: 32% → 53%, V 18–19: 10% → 22%; pairs 12–15″ 75% → 95% | +35% CPU on top of #1 + #3; dots +40% |
| 3 | **Glow model on** (already in the fine zones) | Without it the matched filter finds 3–4.5× the field's rate of non-catalogue dots 30–180″ from saturated stars | Non-Gaia dots 90–180″ from a saturated star: 0.83 → 0.29 per arcmin² (field 0.18) | ~6 s CPU per photo (a third of v2's) |
| 4 | **Glow bug fixed** (core/glow.py): a masked streak or column touching one saturated pixel was taken as a 135 px "core" | Every dot within ~840″ of it was dropped as a "piece" | Galatea (V 13.9, S/N 137) lost with the glow model, kept after the fix | none |
| 5 | **New columns per dot**: `snr_mf`, `n_transient`, `n_masked`, `partner_flux_ujy`, `partner_err_ujy` | Asked for by the vetting, one-photo and partner tests | Partner test free (today ~0.15 MB of S3 reads per mover exposure) | +12 B per dot (Parquet) |
| 6 | **Peaks file** (`<folder>_peaks/`): every other matched-filter peak ≥ 4σ (S/N 4–5, and ≥ 5 peaks the guard, glow or mirror test refused) | Lets later steps ask "was there something here at 4σ?" without pixels | 306P's D3 image (S/N 4.4) is there and nowhere else | ~0.32 MB per photo |
| 7 | **Photo metadata**: empirical aperture noise, 5σ depth grid (32 × 32), saturated cores, sky orientation (CD matrix), matched-filter settings; SPHEREx's position is already there | Forced tests need a believable σ; persistence and trail-direction tests become lookups | VARIANCE underrates the r = 2 px aperture's scatter 1.66× (dense D2 photo) | ~40 KB per photo |
| 8 | **Partner detectors processed together** (D1↔D4, D2↔D5, D3↔D6 of one exposure: `process_photos` pairs them) | Fills #5's partner columns without a second read | — | none (memory: one held photo per worker) |
| — | Not adopted: 2 × 2 sub-pixel kernel phases, a trail filter bank, a 3σ peaks file | Each found little and added 4–10× more noise peaks | see §3.3 | — |

Rerun cost (§8): ~1.6–2× the sky run per photo; for all ~495,000 photos about 9,000–11,000 vCPU-hours on GCP spot
(~140–180 n2-highcpu-64 hours, ~$90–180 compute) and ~2.9 TB of dot files. Not feasible on this Mac (network).
Possible on a PC with ≥ 1 Gbit/s and ≥ 16 cores in ~5–7 days.

## 2. How it was measured

- **Full photo** 2025W34_1B_0173_1D2 (ecl04, D2, crowded: 25,729 sky-setting dots), and its negative (−IMAGE: every
  dot there is a noise or artefact dot).
- **68 bands** of 256 rows (12.6% of a photo each) from NASA's S3 (anonymous range reads, ~436 MB): ecl04 sessions
  0173, 0366, 0451 (D2, D3, D5, D6) and hunt sessions 0389, 0390 (D1–D6); 2–4 exposures per session, so each dot
  can be looked for in the session's other exposures. Placed where known asteroids and comet 306P pass.
- **Truth**: Gaia DR3 (G < 20, ecl04 refcat, moved to each photo's time); isolated = no Gaia star within 12.3″ (2 px),
  forced r = 2 px aperture S/N at the Gaia position as the brightness scale. Known asteroids: MPC orbits with kete from
  SPHEREx's own position (the `known` cache files), 2,332 positions; a "detection" = a dot within 1 px (chance level
  1.3% for the sky setting, 2.2% for v2, from the dot density). 306P/LINEAR: its measured track (ecl04 discovery report).
- **Variants**, each run on the same pixels: `sky` (the sky run: deblend 0.005), `sky_glow`, `fine_glow` (today's fine
  zones), `v2_noglow`, `v2` (= `DOTS_V2_SETTINGS`).
- **Speed**: process CPU time. The Mac was shared by up to nine agents (load 25–118), and macOS moves processes
  between fast and slow cores, so single timings vary ±40%: ratios between variants on the same pixels are what to
  trust, and the sums over 48 bands average that out (§3.5).
- Scripts (not in the repo; in the session scratchpad `ex2/`): `variants.py`, `exp1`–`exp12.py`, `cpu_bands.py`.

## 3. Answers

### 3.1 Does TRANSIENT mask real fast movers? No.

NASA's cosmic-ray flag (TRANSIENT, bit 0) is in our detection mask. If it also flagged fast asteroids, masking it
would delete them.

- Full photo, 24 known asteroids V < 19 moving 20–60″/h: none has TRANSIENT or SUR_ERROR within 1.5 px.
- Bands, all 2,332 known-asteroid positions: TRANSIENT within 1.5 px at 79 (3.4%), SUR_ERROR at 58, any mask bit
  at 130 (5.6%). That is the background rate (TRANSIENT covers 0.75% of pixels; a 1.5 px disc has 7 pixels → 5%).
- The fastest in the bands, (24475) at 179″/h: TRANSIENT at 1 of 8 positions (that one also SUR_ERROR).
- 306P (105″/h, D6, 8 dots): no TRANSIENT, while 9–11% of all D6 dots have TRANSIENT in their 3 × 3.
- OUTLIER sits at 540 of 2,332 asteroid positions (23%). It is recorded, never masked (contracts/flags.md); keep it so.

Decision: keep TRANSIENT masked. Record `n_transient` (TRANSIENT pixels within 3 px) on every dot and peak. The
pixel agent asked for it, then found it a weak test on its own (TRANSIENT within 3 px of 12% of real asteroids' dots
vs 21% of junk dots, hunt) and withdrew the request. It stays: it costs under 1 B per dot (mostly zeros), it cannot be
made later without the pixels, and a 1.75× likelihood ratio still adds to other evidence. Nothing must use it as a
veto.

### 3.2 Deblending close pairs

Gaia pairs where both stars have joint-fit S/N ≥ 10 (full photo):

| separation | n | sky setting | fine v2 (today's fine zones) | v2 |
|---|---|---|---|---|
| 3–8″ | 18 | 0% | 0% | 0% |
| 8–10″ | 13 | 0% | 23% | 31% |
| 10–12″ | 14 | 0% | 21% | 21% |
| 12–15″ | 20 | 0% | 75% | 95% |
| 15–18″ | 39 | 0% | 67% | 82% |

On the hunt photos (43 photos, 15 Nov): pairs 2–3 px (12–18″) 9% → 81% with fine v2. Pairs under ~1.3 px (8″) are
not split by any single-photo detection; the session/pair stacks and the two-source fit (shape_fit.py) are the route
there.

False splits: extra dots within 15″ of single isolated S/N ≥ 20 Gaia stars (452 stars), against the same count at
random places: sky 0.009 (control 0.116), fine 0.013 (0.14), v2 0.027 (0.19). Each setting stays at a seventh of
its own chance rate or less: no measurable false splits.

### 3.3 Threshold, matched filter, trails and false dots

**Completeness.** Isolated Gaia stars found within 0.75 px, by forced aperture S/N (bands, ecl04, 73,987 star-photo
pairs):

| aperture S/N | n | sky | sky_glow | fine_glow | v2 |
|---|---|---|---|---|---|
| 3–4 | 4,778 | 0.1% | 0.1% | 0.6% | 42.8% |
| 4–4.5 | 2,082 | 0.3% | 0.8% | 3.0% | 68.3% |
| 4.5–5 | 1,860 | 5.6% | 7.7% | 17.2% | 79.5% |
| 5–5.5 | 1,750 | 32.7% | 34.9% | 58.0% | 84.7% |
| 5.5–6 | 1,549 | 47.2% | 49.0% | 74.4% | 87.3% |
| 6–7 | 2,739 | 58% | 61% | 81% | 90% |
| 7–10 | 5,874 | 75% | 77% | 88% | 92.4% |
| 10–20 | 8,998 | 80.6% | 81.9% | 91.6% | 94.3% |
| > 20 | 12,461 | 88.0% | 89.1% | 94.2% | 95.5% |

Why the matched filter: sep thresholds each pixel at 2σ and then we keep aperture S/N ≥ 5; the ePSF-matched filter
weighs every pixel by the star's shape and its noise, so the same star has a higher S/N (matched-filter S/N /
aperture S/N: 1.58 at aperture S/N 5–7, 1.49 at 7–10, 1.35 at 10–20). Its 5σ point-source depth on the dense D2 photo
is 154 µJy (AB 18.4) against AB ~17.5 for the aperture cut.

**Known asteroids** (bands; a dot within 1 px of the MPC/kete position; chance 1.3–2.2%):

| V | n | sky | sky_glow | fine_glow | v2_noglow | v2 |
|---|---|---|---|---|---|---|
| ≤ 16 | 53 | 92.5% | 92.5% | 96.2% | 98.1% | 98.1% |
| 16–17 | 52 | 51.9% | 51.9% | 69.2% | 73.1% | 73.1% |
| 17–18 | 73 | 16.4% | 17.8% | 31.5% | 54.8% | 53.4% |
| 18–19 | 274 | 7.7% | 7.3% | 9.5% | 21.5% | 21.5% |
| 19–20 | 508 | 1.8% | 1.8% | 2.4% | 4.7% | 4.7% (chance level) |

(Glow columns with the fixed glow.py; before the fix sky_glow had 90.6% and v2 96.2% at V ≤ 16: Galatea, §4.)
The fastest asteroid in the bands, (24475) at 179″/h, V 18.3: 0 of 8 positions with the sky setting, 1 with
fine_glow, 4 with v2. Comet 306P (105″/h): its D6 image is found in all 8 photos by every setting; its D3 image by
none: in the one photo where it is bright enough (matched filter 9.1 at the predicted place) it sits 2 px from a
star's dot (S/N 11), and v2's peaks file has it at S/N 4.4, 0.3 px from the predicted place.

Of the 129 asteroid positions where the matched filter reads S/N > 10 but v2 has no dot within 1 px, 122 sit 1–3 px
from a star's dot (the asteroid on a star; not separable in one photo) and 7 have no dot within 3 px (not checked
further).

**False dots.** On the negative image (every dot a fake): sky 21, fine_glow 37, v2 65 per photo (37 from the
first passes, 28 from the matched-filter pass), against 43,395 v2 dots on the positive (0.15%). Matched-filter
dots on the positive: 40.8% are Gaia stars (chance 1.3%; first-pass S/N 5–7 dots: 61.6%), the rest are fainter
non-Gaia sources (CatWISE-only, galaxies) or noise; 58.6% are seen again within 1 px in another exposure of the
same session and detector (chance 2.9%), against 54.7% for the sky setting's own S/N 5–7 dots. So the new dots are
real at least as often as today's faintest.

| matched-filter threshold | noise peaks per photo (negative image) |
|---|---|
| 4.0 | 161 |
| 4.5 | 64–69 |
| **5.0 (chosen)** | 28–31 |
| 5.5 | 18 |
| 6.0 | 11 |

**Kernel phases.** Kernels for 2 × 2 sub-pixel positions: Gaia at aperture S/N 3–4 64% → 80%, everything else the
same or lower, noise peaks ≥ 5: 30 → 131 per photo, 2.6× the time. Off (`MF_PHASES = 1`). (With phases, a 5 × 5
kernel gave the same as 7 × 7.)

**Trails (fast movers).** ePSF trails injected into the full photo at random angles, 256 per case (trail S/N for
its own trail kernel; L = length in px in one 113 s exposure; 2 px ≈ 390″/h, 16 px ≈ 3,100″/h):

| L (px) | trail S/N | sep (aperture ≥ 5) | matched filter ≥ 5 | trail filter bank ≥ 6 |
|---|---|---|---|---|
| 0 | 7 / 10 / 15 | 11 / 20 / 64% | 49 / 68 / 84% | 15 / 23 / 45% |
| 2 | 7 / 10 / 15 | 25 / 65 / 82% | 56 / 79 / 92% | 21 / 31 / 41% |
| 4 | 7 / 10 / 15 | 32 / 66 / 83% | 45 / 79 / 88% | 33 / 37 / 52% |
| 8 | 7 / 10 / 15 | 34 / 54 / 79% | 39 / 72 / 90% | 45 / 52 / 63% |
| 16 | 7 / 10 / 15 | 45 / 55 / 82% | 53 / 66 / 95% | 60 / 69 / 80% |

The point-source matched filter finds trails up to 16 px better than sep at every S/N. A bank of 50 trail kernels
(2–16 px × angles) gains only at L ≥ 8 and S/N 7 (+7 points), loses elsewhere, and even at its 6σ threshold it has
241 noise peaks per photo (the point filter: 11 at 6σ, 31 at 5σ): not adopted. Streaks longer than ~25 px (> 5,000″/h) are a line search on the stored
dots and peaks (a chain of dots along one line in one photo), which needs no new pixels; not built here.

### 3.4 What every dot keeps, and what else is stored

Already in every dot (sky run): position (centroid and ePSF fit, errors), aperture flux at 5 radii (1–4.5 px: a curve
of growth = sharpness), ePSF flux and χ² (a cosmic ray is sharper than the ePSF), peak, local background, ZODI, a, b,
θ (trail length and angle in pixels), elong, `flags` (3 × 3 OR, recorded bits included) and `flags_peak` (bits at
the peak: OVERFLOW/NONLINEAR there = saturated), sep's flags.

v2 adds (`DOT_V2_COLUMNS`, after the list columns, only in v2 files):

| column | type | what | who uses it |
|---|---|---|---|
| `snr_mf` | float32 | matched-filter S/N at the dot (largest over its 3 × 3) | completeness, vetting (stars: snr_mf / snr ≈ 1.6 at S/N 5–7, 1.35 at 10–20, 0.85 above 100; much lower = not point-like) |
| `n_transient` | int8 | TRANSIENT pixels within 3 px | weak evidence only (12% of real asteroid dots, 21% of junk; §3.1); never a veto |
| `n_masked` | int8 | masked pixels within 2 px (the aperture) | flux trust; "unknown" vs "absent" |
| `partner_flux_ujy`, `partner_err_ujy` | float32 | r = 2 px aperture at the dot's sky position (ePSF position where fitted) in the simultaneous partner detector, same measurement as the dot's own `flux_ujy` (background map, no aperture correction); error × the partner's empirical noise factor; NaN outside it or on masked pixels | partner test (artefacts.md): a dot with nothing in the partner at the same moment is a glitch or a colour, never motion |

Photo metadata (v2 keys in the footer): `extraction` 2; `aperture_noise` (1.4826 × MAD of the r = 2 px aperture at
400 random clean places: σ in µJy and its ratio to VARIANCE's, e.g. 124 µJy, ×1.66); `grids_v2` (32 × 32 noise and 5σ
point-source depth in µJy) and `depth5_ujy_median`; `sat_cores` ([x, y, pixels] of every OVERFLOW/NONLINEAR clump,
largest first, ≤ 1,000: persistence = a saturated star on the same pixels in the previous exposure);
`sky_jacobian_deg_px` (the local CD matrix at the photo centre, MWIR flip included: θ → sky position angle for
link's trail test); `mf` (thresholds); `partner` (which photo filled the partner columns), `n_partner`. SPHEREx's
position (X_SC … VZ_SC with MJD-AVG) and the flag counts are in every footer already since 14 Nov.

**Peaks file** `<folder>_peaks/<obs_id>D<det>.parquet`: x, y, ra, dec, snr_mf, flux_mf_ujy, flags, n_transient,
why (0 = S/N 4–5, 1 = refused by the saturation guard, 2 = refused by the mirror test beside a subtracted star,
3 = a glow star's own pixels). Dense D2 photo: 13,857 peaks (8,846 at S/N 4–5), 0.36 MB; on the negative image 99
(97 at 4–5): the S/N 4–5 peaks are ~1% noise. Matched-filter-only peaks: at 4σ 31% are Gaia stars (chance 1.2%).

**3σ instead of 4σ?** Gaussian noise gives ~40× more peaks above 3σ than above 4σ (161 → thousands per photo), so
the file would be mostly noise; the depth grid already answers "how bright could it have been here" anywhere. Not
adopted.

**Forced photometry at catalogue positions**: kept as is (forced/, Gaia G < 17.5, the sky list), unchanged by v2.
Add CatWISE's red Gaia-less sources to the forced list in the prep (catalogue agent; §5).

**Size per photo** (Parquet as written):

| | dots | per dot | peaks | metadata |
|---|---|---|---|---|
| sky setting, dense D2 | 3.16 MB (25,729) | 123 B | — | ~3 KB |
| v2, dense D2 | 5.84 MB (43,395) | 135 B | 0.36 MB | ~40 KB |
| sky setting, 68-band mean, scaled to a photo | 2.64 MB | | | |
| v2, 68-band mean, scaled to a photo | 4.71 MB | | 0.32 MB | |

So v2 ≈ 1.9× the sky run's bytes per photo: 887 GB → ~1.7 TB for the 296,000 done, ~2.9 TB for all ~495,000.
Most of it is more dots (+70%), not the new columns (+12 B per dot).

### 3.5 Speed: where the time goes

CPU seconds per photo on this Mac (process time, M5; summed over the 48 bands that have it for every variant, all six
detectors and both fields, scaled to a 2040-row photo):

| | sky | sky_glow | fine_glow | v2_noglow | v2 |
|---|---|---|---|---|---|
| dots, mean over detectors | 5.1 | 11.2 | 13.9 | 12.1 | 18.7 |

So v2 costs ~3.7× the sky setting's detection CPU: fine detection with the matched-filter pass and the v2 columns
×2.4 (v2_noglow 12.1 vs 5.1), the glow model +6.6 s on top. The cost follows the dot count: the crowded D2/D3 photos
cost ~2.5–3× the D5/D6 ones in every variant. Full dense D2 photo, end to end from a local file (best of 2): read and
decompress 0.1–0.2 s, dots 5.2 (sky) vs 15.9 (v2), forced photometry (2,505 targets) 0.03 s, metadata + peaks +
Parquet 0.1 vs 0.5 s.

v2's stages (wall seconds per photo, all 68 bands): glow fit 10.1, detection 4.6, the saturation guard's second sep
run 4.4, ePSF second pass 3.9, matched-filter pass 3.2, measuring the residual's dots 3.1, ePSF positions 2.2, v2
photo metadata 1.2, apertures 0.8, the rest < 0.6 each. The glow fit is a third of v2: a faster fit is the first
place to save. The guard's second sep run (the sky setting, only to compare) is the next: 4.4 s.

On GCP (dot footers of the sky run, ecl04 D6): read 10.7 s (HTTPS, 56 MB), detection 2.8 s, forced 0.4 s per photo
per worker, wall. Reading dominated the sky run; v2 multiplies the detection part only (§8).

## 4. Glow model: on, with the fix

The glow model (merged 16 Nov, #358) subtracts each saturated or very bright star's wings before detection.

Near saturated stars (ecl04 bands, Gaia box; rings around OVERFLOW/NONLINEAR clumps ≥ 3 px; glow.py fixed):

| ring | non-Gaia dots per arcmin²: sky / sky_glow / v2_noglow / v2 | isolated Gaia completeness (S/N 5–20): sky / sky_glow / v2_noglow / v2 |
|---|---|---|
| 0–30″ | 0.87 / 0.49 / 0.87 / 0.49 | — |
| 30–90″ | 0.07 / 0.07 / 0.66 / 0.21 | 25% / 33% / 55% / 50% (n = 84) |
| 90–180″ | 0.04 / 0.08 / 0.83 / 0.29 | 36% / 50% / 78% / 73% (n = 386) |
| 180–300″ | 0.06 / 0.07 / 0.29 / 0.25 | 55% / 62% / 83% / 84% (n = 817) |
| > 300″ (field) | 0.05 / 0.05 / 0.18 / 0.18 | 71% / 73% / 93% / 93% |

The full dense photo (11 saturated stars, no Gaia there) says the same in dots per arcmin² (field: sky 0.59, v2
0.99): 30–90″ sky 0.25, fine_glow 0.40, v2_noglow 1.47, v2 0.52; 90–180″ sky 0.19, fine_glow 0.65, v2_noglow 2.21,
v2 0.98.

- For v2 the glow model is needed: without it the matched filter turns the wings into 3–4.5× the field's rate of
  non-catalogue dots out to 180″; with it they are near field level (0.21–0.29 vs 0.18), and completeness stays
  within the counting noise (−5 points at 90–180″, n = 386).
- For the sky setting it helps too: completeness near bright stars +7 to +14 points, and half the fakes within 30″.
- Gaia completeness in the field and known-asteroid recall: unchanged by the glow model (both settings, after the fix).
- Time: +6–6.6 s CPU per photo (the fit of up to 400 stars): a third of v2's CPU.

**DOTS_SKY_GLOW**: v2 has the glow model built in, so the rerun gets it whatever DOTS_SKY_GLOW says. Leave
DOTS_SKY_GLOW false: it only matters for new photos extracted with the old sky setting, and nothing should be
extracted that way any more. If a sky-setting run happens anyway, turn it on.

**Review of glow.py.**
1. **Bug, fixed** (`bright_stars`): a saturated "core" was every masked pixel 8-connected to an OVERFLOW/NONLINEAR
   pixel. A satellite streak (STREAK-masked, 1,085 pixels) crossing one OVERFLOW pixel became a core of radius 135 px,
   and `Glow.near`/`own` then dropped every other detection within 137 px as a piece of it. Galatea (V 13.9, S/N 137,
   2025W43_2A_0389_2D6) was lost that way. Now only masked pixels within `GLOW_CORE_REACH_PX` (6 px) of a saturated
   pixel belong to the core. Test: `test_a_masked_streak_through_a_saturated_pixel_is_not_a_huge_core` (fails on the
   old rule). It also changes `stack`'s glow, for the better, and only photos where a mask blob touching a saturated
   pixel reaches > 6 px beyond it (already written `_glow` files of the fine zones keep the old rule). Bands after
   the fix: Galatea found again (v2 at V ≤ 16: 96.2% → 98.1%; sky_glow 90.6% → 92.5%, equal to no glow); Gaia
   completeness and the rings unchanged (±0.5 points).
2. The core centre is the mean of the blob's pixels, so masked pixels on one side (a bad column, TRANSIENT) still pull
   it by up to the reach; the ePSF part refits the centre only where the wings constrain it. Small; not changed.
3. `own`: the detection nearest a modelled star counts as the star's own even when the star itself was not detected
   (masked core, no detection): a real neighbour within the core radius + 1.5 px is then measured on IMAGE as if it
   were the star. Rare (it must sit inside the masked rim); not changed.
4. Own dots are measured with an empty timing dict, so the per-stage timings under-report a little. Cosmetic.

## 5. What other agents asked for (extraction-needs.md)

| request (agent) | status in v2 |
|---|---|
| Partner forced flux per dot (pixel) | done: `partner_flux_ujy`, `partner_err_ujy`; partners processed together |
| Empirical aperture noise per photo (pixel) | done: footer `aperture_noise`, plus the 32 × 32 noise grid |
| TRANSIENT pixels next to each dot (pixel; withdrawn 16 Nov evening: weak test) | kept anyway, < 1 B per dot: `n_transient` (dots and peaks), never a veto (§3.1) |
| Saturated cores per photo (pixel) | done: footer `sat_cores` |
| SPHEREx's position per photo (known) | already in every footer since 14 Nov (X_SC … VZ_SC, MJD-AVG; PHOTO_META_HEADER_KEYS): `known` and known_check.py can read footers instead of headers today |
| Forced targets from a full catalogue (catalogue) | prep step, not dots code: run `refcat` / `forced_sky` with CatWISE red Gaia-less sources before the rerun |
| Photo's sky orientation (relink) | done: footer `sky_jacobian_deg_px` |
| Streak detections above ~900″/h (relink) | the matched filter already finds 16 px trails (3,100″/h) at 53–95%; a trail bank was measured and rejected (§3.3); a line search on stored dots and peaks is the way for longer streaks (not built) |

## 6. What later steps gain

- **Catalogue (link, crossid)**: ~25 points more completeness at S/N 5–6, ~65 at 4.5–5: faint stars stop flickering
  between visits (catalogue.md #1: today 555 ecl04 Gaia sources seen in one visit only, 5,336 in 2 of 3), fewer false
  "appeared/vanished", more stars split at 12–18″ (#2).
- **Movers**: known asteroids V 17–18 found 16% → 53% per photo, V 18–19 8% → 22%: more tracklets, longer tracks,
  more recall for score and completeness; fast movers to 3,100″/h found more often.
- **Vetting and To check**: the partner, one-photo and persistence tests become table lookups (no S3 reads per
  mover); `snr_mf` and the empirical σ make each check's numbers honest.
- **Classify / brown dwarfs**: the depth grid gives "fainter than X" limits anywhere; the peaks file gives 4σ
  evidence (306P's D3 image is there).
- **Fakes near bright stars**: the glow model + guard keep the wings near field level; fragments.py has less to clean.

## 7. Storage

Dots: ~4.7–5.8 MB per photo (vs 2.6–3.2), peaks ~0.3 MB, forced unchanged (~1–1.5 MB). All ~495,000 photos: ~2.9 TB
dots + peaks (~$58/month on Standard, ~$29 on Nearline). New folder `dots_deblend1e-4_cp3_g6_psf_glow_v2/` and
`…_v2_peaks/`; nothing existing is overwritten.

## 8. Time and cost to rerun all ~495,000 photos

Per photo the sky run cost ~41 vCPU-s on GCP (3,400 CPU-hours / 296,000 photos). Per worker, a photo took ~10.7 s
to read (HTTPS, 56 MB) and ~3–6 s to detect (2.8 s in the sparse D6 footers; D6 is the cheapest detector on this Mac
too, 2.4 of a 5.1 s mean), so ~17 s, i.e. ~2.4 vCPU per worker. v2 multiplies the detection part by ~3.7 and leaves
the read alone: ~33 s per photo per worker → **~65–80 vCPU-s per photo** (1.6–2× the sky run; the low end if more
workers hide the read).

| where | how | time | money |
|---|---|---|---|
| GCP spot, us-east4, n2-highcpu-64 (S3 us-east-1 is next door) | 495,000 × 65–80 vCPU-s ≈ 9,000–11,000 vCPU-h ≈ 140–180 machine-hours | 1 machine: 6–7.5 days; 4 machines: ~1.5–2 days (CPU quota permitting) | ~$90–180 compute at $0.6–1.0/h spot; S3 reads free (open data); GCS +~2.9 TB (~$29–58/month) |
| Lead's PC (spec unknown; assume 16 Mac-class cores, 1 Gbit/s) | CPU: 18.7 s per photo per core (v2, mean over detectors) → ~3,100 photos/h; network: 56 MB per photo = 27.7 TB → 77 h at 100 MB/s | ~6.5 days CPU-bound if the line holds ≥ 0.5 Gbit/s; network-bound below that | electricity; then ~2.9 TB to upload to the bucket |
| This Mac (4 processes, shared) | CPU ≤ 770 photos/h; network measured 0.15–4 MB/s = ≤ 260 photos/h | ≥ 80 days | not feasible |

Before the full run, time 20 photos on one idle spot worker (all six detectors): it pins the cost to ±10%.

Reading less would help everywhere: ZODI (~16 MB of the 56) is read for one column (`zodi_mjysr`) and the footer's
ZODI grid; dropping it saves ~29% of the bytes (a separate decision: the column would be NaN in v2 files).

## 9. Session and pair stacks

Agent "stacks" (branch p1/session-stacks) measures session coadds, simultaneous-pair detection images (D1+D4, D2+D5,
D3+D6) and shift-and-stack for slow movers: see docs/session-stacks.md. Not measured here. The order of the one
combined rerun is decided from both documents; v2's dots and peaks are per photo and do not depend on the stacks.

## 10. How to run it

`SKYSHIFT_DOTS_V2=1 uv run skyshift-pipeline dots --zone <zone> --root gs://nasa-glazing-data --workers N`
(config.DOTS_V2 → every zone uses `DOTS_V2_SETTINGS`; folder `dots_deblend1e-4_cp3_g6_psf_glow_v2/`). The worker
pairs D1↔D4, D2↔D5, D3↔D6 of one exposure (`exposure_groups`, `process_photos`): the first of a pair is held until
its partner is measured, then both are written with each other's partner columns; a photo whose partner fails is
written with NaN partner columns. Before it: refcat with CatWISE (forced list). After it: link onward with the new
folder (members.dot_row points into the files it was made from).

Contract text for contracts/dots.md is proposed in the PR (v2 columns, sep_flag 0x400, the peaks file, the v2
metadata keys, the folder).

## 11. Open

- Partner columns were checked with tests (one exposure loaded as both partners), not on a real D1+D4 pair.
- The trail line search for > 5,000″/h streaks (relink's request) is not built.
- 2 × 2 kernel phases: +16 points at aperture S/N 3–4 for 4× the noise peaks; revisit only with a better noise model.
- Timings are from a heavily shared Mac; a 20-photo timing on one idle GCP worker before the full run would pin
  the cost to ±10%.
- Cheaper v2: the glow fit is a third of the CPU and the guard's extra sep run an eighth; both can be made faster
  without changing results (fit only stars whose glow reaches beyond the ePSF; reuse the first pass's background).
- The guard keeps only fine dots that the sky setting also finds within 6 px of saturation; the matched filter's
  peaks there go to the peaks file (why 1), so a real neighbour inside 6 px of a saturated core is not a dot.
