<!--
Project page, section "How we know it works" (P4 pastes it in; P1 keeps the numbers right).
Every number comes from web/public/data/validation.json (generated 2026-11-14) or scripts/validate/README.md.
If validation.json changes, update this file from it. Add the scoreboard screenshot where marked.
Fits pitch/project-page.md under "## Detailed project description" (### headings, as there), in place of
or next to "### Measured results"; its "#### Limits of these checks" covers only the checks.
-->

### How we know it works

SkyShift measures positions, motions and brightness on NASA's SPHEREx images. We checked each against sources that know the answer: ESA's Gaia star catalogue, NASA/IRSA's asteroid predictions, Minor Planet Center orbits, the 2MASS and WISE infrared surveys, SDSS galaxy redshifts and published asteroid sizes. Where we could, a control repeats the test where the right answer is "nothing", to count chance hits. Targets were set before running; results are from real SPHEREx data (DR1 + QR3, 14 Nov 2026). ″ = arcsecond; mas = 0.001″; px = one 6.15″ SPHEREx pixel. All eleven pass; V6 and V7 ran on 15 Nov.

| Check | Compared against | Result | Control or comparison |
|---|---|---|---|
| V1 Five test objects found | Gaia DR3 positions moved to each visit's date (WISE 0855: published astrometry) | 5 of 5 objects, in 20 of 20 visits (85 of 86 image stacks; the one miss is WISE 0855's first-visit D6 stack); median offset 0.19 px (1.2″) | Pass rule, set before the run: a 5σ source (5× the noise) within 2 px |
| V2 Barnard's Star's motion | Gaia proper motion and parallax over the same dates | 10.83″ ± 0.36″ in 0.986 yr; Gaia predicts 10.27″ (+5.5%, direction 2.2° off, 1.6σ) | All 5 targets within 3σ of prediction; two near-stationary stars: 0.31″ ± 0.50″ and 0.04″ ± 0.36″ (Gaia: 0.03″, 0.02″) |
| V3 Position error floor | 344 Gaia stars that barely move (G 13–17), same images | 0.36″ visit to visit: motions above 1.1″/yr show at 3σ in one year | 0.36–0.90″ across the five fields |
| V4 Asteroids re-found where predicted | IRSA's Moving Object Search Tool (MOST), asteroids 1–60 | 60 of 60, in 246 of 300 photos; median offset 0.13 px (0.77″) | Same test 3′ away: 2 of 300 photos (0.7%) |
| V5 Asteroids found blind | Every known asteroid's position from Minor Planet Center orbits | Hunt zone: 413 of 457 known-asteroid sessions brighter than V 16 (90%), 30 of 30 objects. Pilot (gate G2): 83 of 90 (92%), 8 of 8 | Every prediction moved 2′: 0% in both zones |
| V6 Fast stars | Gaia proper motion and parallax of the 40 fastest stars fainter than G 12 | 40 of 40 measured: slope 0.943 ± 0.029 of measured against predicted shift, RMS 0.6″; 39 of 40 within 3σ | Without the 6 blended with a star as bright or brighter: slope 0.972 ± 0.019 |
| V7 Brown dwarfs | Gaia motions of 24 ultracool dwarfs faster than 1″/yr, plus WISE 0855's published astrometry | 8 of the 10 predicted to move at least 3σ went the predicted way (within 30°); 23 of 24 measured agree within 3σ | The other 14 move too little in one year to show a direction. The 3σ rule was set after the first run, which counted all 24: 9 of 24 |
| V8 Brightness and speed | 2MASS Ks and WISE W1, stars of 10–13 mag | Hunt zone: SPHEREx ÷ Ks 0.993 (scatter 2.9%, 25,696 stars); ÷ W1 1.029 (1.9%, 27,860 stars). Pilot: Ks 0.993, W1 1.026. Speed: median 7.9 s to the first frame over 20 live requests, all answered | Targets: within 20%; first frame under 15 s |
| V9 Star motions vs Gaia | Gaia DR3 proper motions, 211,326 stars (hunt zone) | Bias 2 (RA) and −7 (Dec) mas/yr; scatter 146 and 144 mas/yr (about 0.15″/yr per star). Pilot, 32,446 stars: bias 3 and −6, scatter 147 and 145 | Pull scatter 0.97 and 0.96; pilot 0.98 and 0.96 (1 = error bars the right size) |
| V10 Galaxy distances | SDSS DR17 spectroscopic redshifts | Hunt zone: 8,552 galaxies brighter than K_AB 17: σ_NMAD 0.023, 2.7% outliers. Pilot, 1,211: 0.021, 2.2% | All 10,328 matched galaxies: 0.026, 5.6% outliers (pilot, 1,415: 0.024, 4.2%) |
| V11 Asteroid sizes | Published IRAS/NEOWISE/AKARI sizes (JPL Small-Body Database) | 11 asteroids: diameter 1.06× published (scatter 14%); albedo 0.71× (scatter 26%) | Per asteroid: diameter 0.76–1.41× |

<!-- Screenshot: the scoreboard (web/public/data/validation.json as the site shows it). -->

**Barnard's Star moves as Gaia says.** Between its first and third visits our tool measured 10.83″ ± 0.36″; Gaia predicts 10.27″. The ± 0.36″ is V3: how far 344 non-moving Gaia stars in the same images appear to shift. All five targets agree with their predictions within 3σ (three error bars); Barnard's Star is furthest, at 1.6σ. RW Aurigae and R Draconis, which barely move, show no false motion.

**60 of 60 asteroids where NASA predicted them.** A photo counts only if a detection lies within 2 px of MOST's prediction and is absent from a stack of the same sky taken 0.25–12 days apart, so a fixed star can't count. Of the 54 photos not counted, 51 had a fixed star within 3 px, where the test cannot decide.

**Blind search.** In the 100 deg² hunt zone (3,692 photos) the tool finds "movers" without being told where asteroids are. Movers within 12″ of a known asteroid's position, predicted from Minor Planet Center orbits: 90% at V < 16 (visible magnitude; larger is fainter), 83% at V 16–17, 42% at V 17–18; 0% for the shifted control. The 12 deg² pilot zone inside it gives 92%, 83% and 40%.

**Brightness and error bars.** In the hunt zone SPHEREx fluxes agree with 2MASS Ks to 0.8% and WISE W1 to 2.9% for 10–13 mag stars; fainter, the scatter grows to 4–10%. In V9 the pull (difference from Gaia ÷ our stated error) scatters by 0.97 and 0.96 over 211,326 stars: our proper-motion error bars are the right size.

**Galaxies and asteroid sizes.** Redshifts fitted to the 102-channel spectra match SDSS to a typical 0.023 in Δz/(1+z) for 8,552 bright galaxies (0.021 in the pilot). A thermal model (NEATM) fitted to reflected sunlight and heat glow gives diameters 1.06× published; our albedos run low (0.71×).

#### Limits of these checks

- **Saturation.** Barnard's Star, RW Aurigae and R Draconis saturate in their cores; we fit the unsaturated wings (without that, Barnard's Star in D6 sat 1.75 px off). In D4–D5 R Draconis's masked core reaches 6–9 px in radius; there we take the core's centre. Ceres saturates and is left out of V11.
- **WISE 0855−0714** counts only in D5–D6 (3.8–5.0 µm). It is found in all 3 visits, in 5 of 6 stacks; the miss is its first visit's D6 stack of 3 photos. In D1–D4 a background star lies 1–2 px away, so those stacks are not counted.
- **Per-photo position errors** straight from detection are 1.4–1.8× too small against Gaia; we scale them before use.
- **One patch of sky.** V5, V8 brightness, V9 and V10 come from the 100 deg² hunt zone (RA 120.5–130.5°, Dec +14.4° to +24.4°, 3,692 photos in 2 visits). The 12 deg² pilot zone (990 photos) lies inside it and decided gate G2 before the hunt ran; its numbers, shown alongside, agree. Elsewhere in the sky only V1–V4 and V11 were run.
- **V8 speed, measured once.** The first live test (13:37 UTC) failed: two test runs hit the API at once while it was being sped up and most requests were refused. After the fix, one agreed run (13:54 UTC, 10 random positions on both clocks) answered all 20 requests: median 7.9 s to the first frame, slowest 16.7 s. Featured targets, served as static files, load in 0.04–0.53 s.

**Reproduce it.** In `scripts/validate/` (see its README): `uv run python v1_targets.py`, `v4_asteroids.py`, `v8_spectra.py`, then `build_validation.py` writes `web/public/data/validation.json`.
