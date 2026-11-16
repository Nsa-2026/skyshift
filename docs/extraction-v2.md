# Extraction v2: what the one rerun of every photo must change and keep

Owner: P1 (agent "extraction", 16 Nov 2026). Code: `pipeline/src/skyshift_pipeline/dots.py`, `psf.py`, `config.py`
(`DOTS_V2`, `MF_*`, `HOLE_*`), `core/src/skyshift_core/glow.py`, `holes.py`; red forced targets `forced_sky.py`
(second pass, §12). Tests: `pipeline/tests/test_extraction_v2.py`, `test_forced_sky.py`, `core/tests/test_glow.py`,
`test_holes.py`, `test_detect.py`. Everything is behind one switch that is off: `SKYSHIFT_DOTS_V2=1` turns it on for every
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
| 9 | **Dark-hole mask** (second pass, §12.1): unflagged 1–4 px groups > 5σ below the sky set to NaN first | MWIR cold pixels NASA's COLD mask misses (300–1,270 per photo) made fake dots on the negative image and pulled apertures down | Negative-image dots per photo D4–D6 508–881 → 29–36; fluxes next to a hole +11–14%; no star or asteroid lost | 0.26 s per photo |
| 10 | **Matched-filter threshold per detector + block noise scale** (§12.2) | D1, D2 and MWIR made 5–20× D3's matched-filter noise dots (D2: its top rows); plane confusion | Matched-filter fakes per deg² 2–42 → 0.3–2.3, every detector at 5.0; Gaia found −0.1 to −0.5% (plane D4 −5.7%) | ~0.3 s |
| 11 | **Red forced targets** (prep, §12.5): AllWISE red, Gaia-less sources as a second sky-wide forced layer | J (D1–D3) for brown-dwarf and red candidates | +2.2k–4.8k targets per photo (+7–58%) | one build, ~30 min |
| 12 | **Centroid-fallback bit** (§12.7): sep_flag 0x800 when the windowed centroid failed or moved > 1 px | Vet guessed it from whole-pixel positions (2 such dots: 0.2% of real asteroids, 14% of the rest), which misses half | 0.6–2.2% of dots carry it | none |
| — | Not adopted: 2 × 2 sub-pixel kernel phases, a trail filter bank, a 3σ peaks file; a faster glow fit (§12.4: no gain) | Each found little and added 4–10× more noise peaks | see §3.3 | — |

Rerun cost (§8, §12.8): ~1.5–1.9× the sky run per photo; for all ~495,000 photos about 8,500–10,500 vCPU-hours on GCP
spot (~135–165 n2-highcpu-64 hours, ~$85–165 compute) and ~2.9 TB of dot files. Not feasible on this Mac (network).
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

**Forced photometry at catalogue positions**: the same method (forced/, Gaia G < 17.5, the sky list). The v2 rerun
rewrites each photo's forced/ file (17 Nov fix: before, an existing file was kept, so the hole mask and the red layer
reached only photos without one): it is measured on the hole-masked image and holds the red layer's targets (§12.5).
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
| Forced targets from a full catalogue (catalogue) | done as a prep step: the red layer `forced_sky --red` (AllWISE red Gaia-less sources), read by `dots` next to the Gaia layer (§12.5) |
| Photo's sky orientation (relink) | done: footer `sky_jacobian_deg_px` |
| Streak detections above ~900″/h (relink) | the matched filter already finds 16 px trails (3,100″/h) at 53–95%; a trail bank was measured and rejected (§3.3); a line search on stored dots and peaks is the way for longer streaks (not built) |
| Matched filter per detector, dark-hole mask, session coadds and maps (stacks) | §12.2, §12.1, §12.7 |

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
workers hide the read). After the second pass: **~62–76 vCPU-s** (the guard −5%; §12.8).

| where | how | time | money |
|---|---|---|---|
| GCP spot, us-east4, n2-highcpu-64 (S3 us-east-1 is next door) | 495,000 × 62–76 vCPU-s ≈ 8,500–10,500 vCPU-h ≈ 135–165 machine-hours | 1 machine: 5.6–6.9 days; 4 machines: ~1.4–1.7 days (CPU quota permitting) | ~$85–165 compute at $0.6–1.0/h spot; S3 reads free (open data); GCS +~2.9 TB (~$29–58/month) |
| Lead's PC (spec unknown; assume 16 Mac-class cores, 1 Gbit/s) | CPU: ~17.8 s per photo per core (v2 after §12, mean over detectors; idle-core timings of §12.4: 6.9–37 s) → ~3,200 photos/h; network: 56 MB per photo = 27.7 TB → 77 h at 100 MB/s | ~6.4 days CPU-bound if the line holds ≥ 0.5 Gbit/s; network-bound below that | electricity; then ~2.9 TB to upload to the bucket |
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

- Partner columns: checked on real pairs in §12.3.
- The trail line search for > 5,000″/h streaks (relink's request) is not built.
- 2 × 2 kernel phases: +16 points at aperture S/N 3–4 for 4× the noise peaks; revisit only with a better noise model.
- Timings are from a heavily shared Mac; a 20-photo timing on one idle GCP worker before the full run would pin
  the cost to ±10%.
- Cheaper v2: the guard is now 1.4–2.4× faster with the same drops; the glow fit (~15–25% on an idle core) did not
  get faster without changing results (§12.4).
- D2's top rows (y ≥ ~1975) make 22–77 first-pass fake dots per D2 photo (a background ramp): an edge rule in
  vet/suspects, no re-extraction (§12.2). D1 in the hunt photos: 58–100 first-pass negatives in the bottom ~330 rows.
- Session coadds and per-session matched-filter maps (stacks agent) are not in v2 (§12.7).
- The guard keeps only fine dots that the sky setting also finds within 6 px of saturation; the matched filter's
  peaks there go to the peaks file (why 1), so a real neighbour inside 6 px of a saturated core is not a dot.

## 12. 16 Nov, second pass (agent "v2finish")

The session-stacks study (docs/session-stacks.md) found three things wrong with v2 as merged: one matched-filter
threshold for all detectors (D1 ~21 noise dots per deg² at 5σ against D2/D3's 0–3 there), MWIR photos with hundreds of
unflagged dark holes that wrecked the negative-image control, and partner columns that had never met a real pair.
On full photos the picture is wider: every detector but D3 made 5–20× D3's matched-filter noise dots (table in §12.2). This pass fixes them, measures the speed-ups proposed in
§11, adds the red forced targets and settles the Gaia G < 21 question.

**Photos.** 36 full photos, all six detectors of six exposures: hunt 2025W43_2A_0389_1 and _2 (ecliptic, RA 128°),
ecl04 2025W34_1B_0173_1 and _2 (RA 54°), the Galactic plane 2025W38_2C_0423_4 (crowded: 30k forced Gaia targets per
photo) and high latitude 2026W21_2A_0049_4 (empty: 6.8k). Each also run negated (−IMAGE, every dot fake; the hole mask
and the matched filter's noise grid come from the positive photo, as a real run would compute them). Truth: isolated
Gaia DR3 G < 20 stars moved to the photo time (~313,000 star-photo pairs per detector) and 3,544 known-asteroid
positions (MPC orbits with kete from SPHEREx, hunt and ecl04). "Merged" below = v2 as merged in #370.

### 12.1 Dark holes: masked (new, `DOTS_V2_HOLES`, core `skyshift_core.holes`)

Unflagged single pixels far below the sky, mostly the same detector pixels week after week (cold pixels NASA's COLD
mask misses; two D4 photos of different weeks and fields share 100–250 of them, consecutive exposures 80%). They are not
tied to PERSIST or OUTLIER pixels (20% lie within 2 px of one, the field's share), so the stacks agent's first guess
(next to PERSIST/OUTLIER, ≥ 3 pixels) would have missed most. Rule: on IMAGE minus its background, an 8-connected
group of 1–4 pixels each more than 5σ (max(√VARIANCE, background rms)) below the sky is set to NaN before anything is
measured; larger low areas are background structure and stay. Real light is never below the sky, so the rule cannot
take a source's pixels; pure noise gives ~1 such pixel per photo.

| | D1 | D2 | D3 | D4 | D5 | D6 |
|---|---|---|---|---|---|---|
| holes per photo (5σ) | 41–138 | 2–144 | 8–50 | 676–1,267 | 301–845 | 431–817 |
| negative-image dots per photo, merged → now (all passes) | 171 → 61 | 195 → 67 | 27 → 4 | 881 → 29 | 508 → 36 | 508 → 30 |
| … of them first-pass (sep) dots | 50 → 36 | 50 → 50 | 1 → 0 | 368 → 0.5 | 68 → 11 | 166 → 2.5 |

7σ was tried first: it left two thirds of the matched-filter fakes, whose deepest pixel lay at −5 to −7σ (half of them
a single pixel). 5σ removes 71–94% of them in every detector. Masked pixels within 1.5 px of a Gaia star: at the
chance rate (the holes don't follow stars). Known asteroids: none lost (one V 19.7 sighting moved from matched-filter
S/N 4.51 to 4.49, below 5 either way). Gaia stars found: +6 to +31 per detector over 36 photos. Fluxes: S/N > 10 dots
with a hole in their aperture come out 11–14% brighter (the hole had pulled them down by that much, 2–3σ). Cost 0.26 s
CPU per photo. Footer `dark_holes` {n_holes, n_px, depth_min_sigma, config}; `n_masked` counts them per dot.

### 12.2 Matched-filter threshold per detector, and block normalisation

The matched filter's S/N map has its own noise level, measured from its lower half (median − 15.87th percentile:
sources are positive): `sigma_lo` per photo and per block of a 32 × 32 grid (footer `mf.sigma_grid`). In normal sky
it is 1.00–1.05 (faint sources, not noise). Where it exceeds 1.1 (D2's top rows, crowded plane fields: 0.2–80% of a
photo; plane D4 819 of 1,024 blocks) each peak's threshold is multiplied by its block's σ (`MF_PASS_NORMALISE` "block",
`MF_SIGMA_MIN_SCALE` 1.1). Scaling every block (min 1.0) cut the fakes another 25% but lost 10 of 267 known-asteroid
sightings at V 18–20 and 0.5–2.2% of the Gaia stars; above 1.1 it loses 2 sightings (both hunt D2) and 0.0–0.5% of the
Gaia stars outside the plane.

Then one target for every detector: the lowest threshold ≥ 5 whose negative images give ≤ 2.5 matched-filter dots per
deg² (~30 per photo, what v2 was tuned to on its dense D2 photo, §3.3). With the hole mask and the block scale every
detector meets it at 5.0, so `MF_PASS_SNR_DET` is 5.0 for all six (the per-detector switch stays for later tuning):

| matched-filter dots per deg², negative images (36 photos) | D1 | D2 | D3 | D4 | D5 | D6 |
|---|---|---|---|---|---|---|
| merged (5.0, no holes, no scale) | 9.9 | 11.7 | 2.1 | 42 | 36 | 28 |
| now, 5.0 | 2.1 | 1.2 | 0.3 | 2.3 | 2.0 | 2.2 |
| now, 5.25 / 5.5 | 0.7 / 0.4 | 0.9 / 0.8 | 0.1 / 0.0 | 1.4 / 1.1 | 1.1 / 0.8 | 1.3 / 1.0 |

| Gaia, merged → now (threshold 5.0) | D1 | D2 | D3 | D4 | D5 | D6 |
|---|---|---|---|---|---|---|
| G at 50% found | 18.27 → 18.26 | 18.74 → 18.73 | 18.72 → 18.71 | 17.78 → 17.73 | 16.19 → 16.19 | 15.25 → 15.25 |
| stars found | −0.5% | −0.5% | −0.1% | −1.5% (plane −5.7%, elsewhere −0.2%) | −0.1% | −0.1% |

Known asteroids, merged → now: V ≤ 18 99 of 140 → 99; V 18–19 49 → 49; V 19–20 39 → 38; V > 20 80 → 79 (chance
level). Each 0.25 above 5.0 costs ~0.04 mag of G50 and 2% of the stars (more in MWIR): not worth a few fakes per
photo when the first pass's own are 0–50 (below).

**What is left on the negative images** is no longer the matched filter but the first (sep) pass, in two places:
- **D2's top rows** (y ≥ ~1975 of every D2 photo, all four weeks and fields): a background ramp (hunt: 0.4 → 3.6
  MJy/sr over 60 rows, scatter 30× the noise; high latitude 0.29 → 0.75) that sep's 32 px background can't follow.
  98–100% of D2's first-pass negatives (22–77 per photo) and nearly all its matched-filter ones (hunt 0389_1: 69 of
  72 at a flat 5.0; the block scale removes 15) are there. No re-extraction needed to deal with it: x, y and the detector are in every dot, so vet/suspects can treat
  D2 y ≥ 1975 like the edge (proposed to the vet owner); the dots keep their row.
- **D1 in the hunt photos**: 58–100 first-pass negatives per photo in the bottom ~330 rows (other fields 10–21); not
  traced further.

### 12.3 Partner columns on real pairs

Four exposures (hunt 0389_1, ecl04 0173_1, plane 0423_4, high latitude 0049_4) and all six directions (D1→D4 …
D6→D3): 900 dots per direction (a third with partner S/N ≥ 20, the rest at random), ~21,000 spots. At each spot, on
the partner photo: the pixel agent's forced photometry (core `pixelcheck.forced_flux`: r = 2 px, annulus median
4–7 px, × 1.033, error from random apertures), and the partner photo's own dot when one lies within 1 px.

| 24 directions | result |
|---|---|
| partner columns finite | 91–98% of dots (the rest: partner spot off the photo or masked) |
| partner / the partner photo's own dot, S/N ≥ 20 | 0.9999–1.0003 in all 24: the same measurement as that photo's `flux_ujy` |
| forced / partner, S/N ≥ 20 | 1.024–1.031 (median 1.030): forced's × 1.033 aperture correction, less ~0.3% of the star's wing in its annulus (plane 1.024–1.029) |
| forced − 1.033 × partner, S/N < 20 | −20 to −78 µJy outside the plane, −130 to −520 µJy in it: the zero point. Forced takes a local annulus (neighbours' light in crowded fields); partner takes the photo's background map, like every dot |
| error, forced / partner: S/N < 5; 5–20; ≥ 20 | 0.78–1.23 (median 1.02); 0.73–1.18 (0.95); 0.23–0.64 (0.39). The partner error holds the source's own photon noise, the forced one only the sky's |
| blank spots (no own dot, partner S/N < 3): robust σ of flux / error | partner 0.55–1.15 (median 0.89), forced 0.53–1.21 (0.98): partner errors are right to ~10% generous outside the plane, 20–45% generous in it; never too small |

**Decision: keep the definition, no code change.** `partner_flux_ujy` / `partner_err_ujy` are the partner detector's
flux at the dot's position on the same zero point and scale as its own dots' `flux_ujy` (no 1.033). Multiply by 1.033
to compare with `pixelcheck`; at faint levels expect the zero-point offset above. The errors hold where the columns
matter (a faint partner: is anything there?) and err on the safe side in crowded fields.

### 12.4 Speed

Old and new code alternating in one process on the same inputs (process time, 3 repetitions, median; 6 full
photos). Separate runs on the shared Mac varied ±40% (an interleaved run-by-run benchmark could not tell the codes
apart), so only this same-process comparison is used.

| photo | glow fit, old / new (s) | saturation guard, old / new (s) | guard candidates / fine dots | same drops |
|---|---|---|---|---|
| plane D1 | 4.96 / 5.60 | 11.74 / 9.48 | 1,686 / 64,507 | yes (332) |
| plane D4 | 6.39 / 5.96 | 9.01 / 6.62 | 4,885 / 57,918 | yes (857) |
| hunt D2 | 4.01 / 4.61 | 3.87 / 1.99 | 647 / 31,056 | yes (73) |
| hunt D5 | 1.85 / 1.87 | 0.80 / 0.39 | 473 / 10,236 | yes (39) |
| ecl04 D4 | 5.04 / 5.84 | 3.43 / 2.36 | 1,967 / 27,561 | yes (188) |
| high latitude D1 | 2.74 / 2.76 | 1.14 / 0.48 | 227 / 11,353 | yes (24) |

- **Saturation guard: kept.** The sky setting's second sep run now measures only the objects whose peak lies within
  2.5 px of a guard candidate (core `detect(near=...)`); a measured position always ends within 1 px of its peak, so the
  rows within 1 px of a candidate, and the drops, are exactly the full run's (6 of 6 photos above; core test
  `test_detect_near_is_the_subset_of_the_full_call`). 1.4–2.4× faster: −0.4 to −2.4 s per photo.
- **Glow fit: no gain, not kept.** Computing every ring and sector median at once (grouped sorts, identical results)
  measured the same as the loop: 1.8–6.4 s per photo either way. The profiler had inflated the loop's cost (Python
  calls). The fit is ~15–25% of v2 on an idle core, not a third (that was wall time on the Mac at load 25–118). Faster
  means fitting fewer stars, which changes results: not done.
- **Identity.** With the hole mask and the block scale off, the new code wrote the same dots, peaks and footers as the
  merged code on 36 of 36 photos (new footer keys aside; this run was before sep_flag 0x800, §12.7, which adds a bit
  to 0.6–2.2% of dots and moves nothing).
- **New costs.** Hole mask 0.26 s per photo (median), the S/N map's noise statistics and grid ~0.3 s.

v2 per photo, process time on an idle core of this Mac (M5), the six photos above: merged 6.8–39.0 s (mean 20.8),
now ~6.9–37.2 s (mean ~19.8): −5%, from the guard. The crowded plane photos cost 2× the ecliptic ones.

### 12.5 Forced targets: the red layer (catalogue agent's request)

`forced_sky --red` builds `refcat/sky/forced_red_n32/p<pixel>.parquet` + `_complete.json`, next to the Gaia layer and
on its partitions (AllWISE's own healpix_k5 = nside 32 nested, read anonymously from NASA's S3, us-west-2, columns
only): AllWISE sources with W1 < 16, W1 − W2 ≥ 0.4, W2 S/N ≥ 3 and no forced Gaia target within 2″ (the catalogue
agent's rule; brown-dwarf and red-object candidates), reddest first, ≤ 6,000 per partition, ref_id `allwise:<designation>`,
epoch 2010.5 (AllWISE's), no proper motion. `dots` reads it with the Gaia layer once its manifest exists (forced origin
`sky+red`; `SKYSHIFT_FORCED_SKY_RED=off` turns it off). Why AllWISE, not CatWISE: it is on S3 in the same partitions;
CatWISE is only at IRSA's TAP (hours per zone, 14–15 Nov).

Measured on the 54 partitions of the six test exposures: 52,756 rows (median 990 per partition, max 2,076, none at the
cap), 30 KB per partition (the Gaia layer: 306 KB), 1.6 s per partition with 3 threads (S3 reads; 3.3 s CPU for all
54). Forced targets per photo: Gaia 6.8k–29.7k, red +2.2k–4.8k: +7% (plane), +16–25% (ecliptic), +58% (high
latitude). Whole sky: 12,288 partitions, ~23 GB of AllWISE column reads (free), ~12 M rows, ~0.4 GB; ~30 min with 32
threads on a cloud machine (~6 h with 3 on a laptop). Forced photometry per target is negligible (§3.5), the forced
files grow by the same 7–58%. Build it before the rerun. Under DOTS_V2, `dots` rewrites an existing forced/ file
(stats `forced_replaced`); without v2 a re-extraction into another dots folder keeps it (`forced_kept`).

**Not yet used downstream.** lightcurves ties a forced row to a source through crossids' Gaia, CatWISE and 2MASS
ref_ids, so `allwise:` rows stay catalogue-only light curves (source_id −1) and `changes.name_of` calls them
"source N". For classify's J − [4.6] they must be tied to the source by position (the forced row's ra, dec at the
photo's time, within 3″) or by an AllWISE crossid: proposed to the lightcurves / crossid owner. The photometry is
in the forced files either way, so the rerun need not wait for it.

**Used downstream (16 Nov night).** crossid ties each `allwise:` target to the nearest source within 3″
(`allwise_ref_id`, `allwise_sep_arcsec`; the layer's partitions around the zone's sources), lightcurves takes those
forced rows into the source's light curves, and classify takes J and [4.6] from them where the source's spectrum has
none (`j_m46_from`). On the four test exposures' discs (one zone each): 1,672 of 3,866 red targets link (43%; median
0.33–0.69″); 1,324 of those have neither J nor [4.6] in their spectrum, 55 only [4.6], 33 already both.
`changes.name_of` still has no `allwise:` branch.

### 12.6 Gaia G < 21 in refcat (proposal: 704 of 1,161 "no Gaia" spectral candidates have Gaia G 20–21.9)

Measured on four 1° tiles (hunt, ecl04, plane, bulge): rows +44–68%, ESA TAP time the same except the densest tile
(12 → 20 s). Chance Gaia matches grow with it: at 6″ (suspects' veto, fragments) ecliptic 6.4–7.8% → 9.2–11.1%,
plane 35% → 47%, high latitude 2.6% → 4.1%; at 3″ 1.6–2.0% → 2.4–2.9%. **Decision: REFCAT_GAIA_G_MAX stays 20 in this
PR.** Dots and the forced list do not depend on it (forced is G < 17.5 + red), so it is not a reason to rerun anything;
it can be raised whenever the zones' refcats are rebuilt, together with a G < 20 cut in the consumers whose radius
would otherwise veto more by chance (suspects 6″, fragments 3″, link 2.5″, orphans): those are other owners' steps.

### 12.7 extraction-needs.md, all of it

| request | status |
|---|---|
| partner forced flux, empirical noise, saturated cores, sky orientation (pixel, relink) | in v2 (§5); partner checked on real pairs (§12.3) |
| `n_transient` (pixel; withdrawn) | kept, never a veto (§5) |
| SPHEREx position per photo (known) | in every footer since 14 Nov |
| forced targets from a full catalogue (catalogue) | the red layer (§12.5) |
| streaks > 900″/h (relink) | not built: a line search over stored dots and peaks, no pixels needed (§3.3) |
| matched filter per detector, normalised by the map's σ (stacks) | done: block σ grid + per-detector thresholds (§12.2). "Per photo from its own negative peaks" can't be done in one pass (the negative image is a second run); the block grid is its stand-in |
| MWIR dark-hole mask (stacks) | done, a different rule: single pixels, not next to PERSIST/OUTLIER (§12.1); count in the footer, per dot in `n_masked` |
| session coadds per detector; session matched-filter maps for the hunt (stacks) | not in this PR: they change what a worker holds (a session's 2–5 exposures of a detector, not an exposure's six photos). The step exists on main (#383, `coadd`, behind `SKYSHIFT_SESSION_STACK`); sessionstack asks to run it inside the dots worker so each photo is read once. That is a worker change for the lead and the sessionstack agent, not a dots change: v2's per-photo files don't depend on it. **Done (16 Nov night)**: with both switches the v2 dots worker builds them, each photo read once, files identical to the separate step (docs/session-stacks.md §8) |
| D4 dark holes not all caught (sessionstack: 26 negative peaks per deg² at 8σ in the session D4 map) | that is the session stack's own rule (`coadd.dark_holes`: ≥ 3 px below −10σ, the stacks agent's first guess), which misses the single −5 to −7σ pixels. The v2 rule (§12.1) takes D4's matched-filter fakes 42 → 2.3 per deg² at 5σ on full photos. Proposed to sessionstack: `coadd.prepare` calls core `skyshift_core.holes.dark_holes` with the `HOLE_*` settings (their file, not changed here) |
| a flag when a dot's centroid fell back (moverflaws) | done: sep_flag bit **0x800** (core `SEP_FLAG_CENTROID_FALLBACK`), set by core `measure_objects` on every dot from now on (v2 and not, all passes). Real photos: 0.6–0.96% of dots (plane D1 2.2%), 1.2–5.4% at S/N 5–7. Half of the fallbacks are not on a whole pixel (sep's barycentre) and half of the whole-pixel dots are not fallbacks, so vet's guess and the bit differ |
| freedots ra/dec; stack rows as tracklets' input (moverflaws, sessionstack) | nothing for the extraction |

### 12.8 Rerun cost after this pass

Per photo per worker on GCP (§8): read ~10.7 s (unchanged), detection ~3.7 × the sky run's ~3–6 s minus the guard's
5% → ~31–32 s per photo per worker → **~62–76 vCPU-s per photo** (was 65–80). All ~495,000 photos: ~8,500–10,500
vCPU-hours ≈ 135–165 n2-highcpu-64 hours, ~$85–165 spot compute, plus the red layer's one-off build (~30 min on one
machine, free S3 reads). Dots and peaks bytes: −0.9% dots against merged v2 (fewer matched-filter fakes and
structured-block dots), so ~2.9 TB stands. The 20-photo timing on an idle spot worker before the full run is still the
way to pin it to ±10%.
