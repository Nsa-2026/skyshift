<!--
Project page, section "How we know it works" (P4 pastes it in; P1 keeps the numbers right).
Every number comes from web/public/data/validation.json (generated 2026-11-14) or scripts/validate/README.md.
If validation.json changes, update this file from it. Add the scoreboard screenshot where marked.
Fits pitch/project-page.md under "## Detailed project description" (### headings, as there), in place of
or next to "### Measured results"; its "#### Limits of these checks" covers only the checks.
-->

### How we know it works

SkyShift measures positions, motions and brightness on NASA's SPHEREx images. We checked each against sources that already know the answer: ESA's Gaia star catalogue, NASA/IRSA's asteroid predictions, the 2MASS and WISE infrared surveys, SDSS galaxy redshifts and published asteroid sizes. Where we could, a control repeats the test where the right answer is "nothing", to count chance hits. Targets were set before running; results are from real SPHEREx data (DR1 + QR3, 14 Nov 2026). ″ = arcsecond; mas = 0.001″; px = one 6.15″ SPHEREx pixel. Eight of eleven checks pass; V8 is half done; V6 and V7 were cut.

| Check | Compared against | Result | Control or comparison |
|---|---|---|---|
| V1 Five test objects found | Gaia DR3 positions moved to each visit's date (WISE 0855: published astrometry) | 5 of 5 objects, in 19 of 20 visits (83 of 86 image stacks; the 3 misses are WISE 0855's); median offset 0.21 px (1.3″) | Pass rule, set before the run: a 5σ source (5× the noise) within 2 px |
| V2 Barnard's Star's motion | Gaia proper motion and parallax over the same dates | 10.83″ ± 0.36″ in 0.986 yr; Gaia predicts 10.27″ (+5.5%, direction 2.2° off) | Two near-stationary stars: 0.31″ ± 0.50″ and 0.04″ ± 0.36″ (Gaia: 0.03″, 0.02″) |
| V3 Position error floor | 344 Gaia stars that barely move (G 13–17), same images | 0.36″ visit to visit: motions above 1.1″/yr show at 3σ in one year | 0.36–0.90″ across the five fields |
| V4 Asteroids re-found where predicted | IRSA's Moving Object Search Tool (MOST), asteroids 1–60 | 60 of 60, in 249 of 300 photos; median offset 0.13 px (0.77″) | Same test 3′ away: 3 of 300 photos (1.0%) |
| V5 Asteroids found blind (pilot) | Predicted positions of every known asteroid in the field | 83 of 90 known-asteroid sessions brighter than V 16 (92%), 8 of 8 objects | Every prediction moved 2′: 0% |
| V6 Fast stars | — | Cut for the one-day build, not run | — |
| V7 Brown dwarfs | — | Cut for the one-day build, not run | — |
| V8 Brightness and speed | 2MASS Ks and WISE W1, stars of 10–13 mag (pilot) | SPHEREx ÷ Ks 1.010 (scatter 3.0%, 3,918 stars); ÷ W1 0.994 (1.8%, 4,240 stars). Speed: pending | Targets: within 20%; first frame under 15 s |
| V9 Star motions vs Gaia (pilot) | Gaia DR3 proper motions, 32,446 stars | Bias 3 (RA) and −6 (Dec) mas/yr; scatter 147 and 145 mas/yr (about 0.15″/yr per star) | Pull scatter 0.98 and 0.96 (1 = error bars the right size) |
| V10 Galaxy distances (pilot) | SDSS DR17 spectroscopic redshifts | 1,211 galaxies brighter than K_AB 17: σ_NMAD 0.021, 2.2% outliers | All 1,415 matched galaxies: 0.024, 4.2% outliers |
| V11 Asteroid sizes | Published IRAS/NEOWISE/AKARI sizes (JPL Small-Body Database) | 11 asteroids: diameter 1.06× published (scatter 14%); albedo 0.71× (scatter 26%) | Per asteroid: diameter 0.76–1.41× |

<!-- Screenshot: the scoreboard (web/public/data/validation.json as the site shows it). -->

**Barnard's Star moves as Gaia says.** Between its first and third visits our tool measured 10.83″ ± 0.36″ toward position angle 357.8° (north = 0°); Gaia predicts 10.27″ toward 355.6°. The ± 0.36″ is V3: how far 344 non-moving Gaia stars in the same images appear to shift. Epsilon Indi B: 6.05″ against 6.49″ (−6.7%). RW Aurigae and R Draconis, which barely move, show no false motion.

**60 of 60 asteroids where NASA predicted them.** A photo counts only if a detection lies within 2 px of MOST's prediction and is absent from a stack of the same sky taken 0.25–12 days apart, so a fixed star can't count. Of the 51 photos not counted, 48 had a fixed star within 3 px, where the test cannot decide.

**Blind search.** In the 12 deg² pilot zone the tool links shifting detections into "movers" on its own, without being told where asteroids are. Movers within 12″ of a known asteroid's predicted position: 92% at V < 16 (visible magnitude; larger is fainter), 83% at V 16–17, 40% at V 17–18; 0% for the shifted control.

**Brightness and error bars.** SPHEREx fluxes agree with 2MASS Ks to 1% and WISE W1 to 0.6% for 10–13 mag stars; fainter, the scatter grows to 6–12%. In V9 the pull (difference from Gaia ÷ our stated error) scatters by 0.98 and 0.96: our proper-motion error bars are the right size.

**Galaxies and asteroid sizes.** Redshifts fitted to the 102-channel spectra match SDSS to a typical 0.021 in Δz/(1+z) for bright galaxies. A thermal model (NEATM) fitted to reflected sunlight and heat glow gives diameters 1.06× published; our albedos run low (0.71×).

#### Limits of these checks

- **Saturation.** Barnard's Star, RW Aurigae and R Draconis saturate in their cores; we fit the unsaturated wings (without that, Barnard's Star in D6 sat 1.75 px off). In D4–D5 R Draconis's masked core reaches 6–9 px in radius; there we take the core's centre. Ceres saturates, so it is not among V11's 11 asteroids.
- **WISE 0855−0714** is detected only in D5–D6 (3.8–5.0 µm), in its two full visits (Oct–Dec 2025, Apr–May 2026); the first has 8 D5 and 3 D6 photos. In D1–D4 a background star lies 1–2 px away.
- **Per-photo position errors** straight from detection are 1.4–1.8× too small against Gaia; we scale them before use.
- **Blends.** RW Aurigae A+B (1.4″ apart) and Epsilon Indi Ba+Bb each appear as one source.
- **Pilot only.** V5, V8 brightness, V9 and V10 come from the 12 deg² pilot; the 100 deg² hunt zone is not finished.
- **V8 speed** (first frame under 15 s) is pending: not yet run on the deployed site.

**Reproduce it.** In `scripts/validate/` (see its README): `uv run python v1_targets.py`, `v4_asteroids.py`, `v8_spectra.py`, then `build_validation.py` writes `web/public/data/validation.json`.
