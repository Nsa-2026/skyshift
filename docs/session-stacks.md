# Session coadds, detector pairs and shift-and-stack: what they gain, what they cost

Agent "stacks", 16 Nov. Branch p1/session-stacks. Prototype: `pipeline/src/skyshift_pipeline/coadd.py` (not a CLI
step) with `pipeline/tests/test_coadd.py`. Every number below is measured on real hunt-zone photos from NASA's S3
unless it says "estimate". Scripts and raw outputs stayed in the agent's scratch folder; the method is described here
so it can be repeated.

Sections 1–6 are the study. Section 7 (agent "sessionstack", 16 Nov evening, branch p1/session-stack-step) is the
step built from it (`skyshift-pipeline sessionstack`, off unless `SKYSHIFT_SESSION_STACK=1`), link's use of its rows,
the orphans changes, and what they measured on real hunt sessions.


## In plain words

1. **Coadding a session's exposures works as theory says.** 70% of the times a spot is seen, it is seen in 4
   exposures of one session (2.1 min apart; the pointing steps 11.8′ = 114.6 px along the detector's y axis, the LVF
   wavelength axis, so 94% of the sky overlaps). Adding 2 / 3 / 4 exposures gives S/N × 1.4–1.5 / 1.7–1.9 / 1.9–2.1
   in SWIR (√N = 1.41 / 1.73 / 2.0): **+0.4 / +0.6 / +0.73 mag** in the 5σ limit (MWIR a bit more). Gaia stars' 50%
   completeness point in D1 moves **+0.49 / +0.73 / +0.92 mag** at the same false-alarm rate.
2. **The detector itself is the other big gain, and the cheapest.** On a single photo, the ePSF matched filter
   reaches Gaia G50 0.85 mag deeper than the sky run's dots in D1 and 1.47 mag deeper in D2 (the same strips, the real
   dot files; sep re-run on the strips reproduces those dot files within 0.1 mag). In D2/D3 its negative-image false
   alarms stay at 0–3 per deg² at S/N 5; in D1 (21/deg²) the gain at an equal 10/deg² is still +0.71. Single-photo
   matched filter + 4-exposure coadd: **+1.8–1.9 mag** over today's dots (D1, D4, D5). The matched filter costs well
   under a second per photo.
3. **MWIR photos have dark holes** that sep partly sees too: 6–27 per deg² deeper than −20σ (70–330 per photo),
   unflagged, next to PERSIST or OUTLIER pixels. Until they are masked, MWIR gains nothing at equal false alarms.
4. **The simultaneous pairs (D1+D4, D2+D5, D3+D6) add no depth for stars.** They are simultaneous (same MJD-AVG to
   0.04 s, same sky within 63–76″), but stars have 0.5–0.6 of the SWIR S/N in D4 and only 0.09–0.18 in D5/D6: the
   pair sum is no deeper than the SWIR photo alone, the χ² combination equal, both at 3–10× the false alarms. Keep the
   pair as the glitch test (partner forced flux), not as a detection image.
5. **Main-belt asteroids survive a static session coadd.** At 18–47″/h they trail 2–5″ in a 4-exposure session
   (PSF FWHM 7.5″). The static coadd gives them S/N × 1.6–2.1 over one photo (Spitzweg V 18.1: 5.0 → 10.4;
   (23303) V 18.1: 6.4 → 12.1); tracking along the true motion adds 0–6%. The peak sits at the asteroid's mid-session
   position. Of 9 asteroids V < 18.3 in the strips, the sky-run detector found 3 in at least 2 of 4 photos; the
   static coadd finds all 9 and the 2 at V 18.3–19.0.
6. **No step finds anything fainter than one exposure today, at any rate.** The distant-planet route (orphans) works
   on single-exposure dots (5σ at AB 17.3 in D1); matched filter + session coadds would deepen it by ~1.5 mag in SWIR,
   a visit-long shift-and-stack along physical orbits by a further ~1.3 mag after paying for its trials.
7. **Recommendation for the one combined rerun (extraction v2, docs/extraction-v2.md):** (1) keep v2's matched-filter
   pass (already there, #370) but set its threshold per detector (D1 has ~10× D2's noise peaks), (2) a MWIR dark-hole
   mask, (3) session coadds per detector with per-photo forced measurement, (4) store per-session matched-filter maps
   for the hunt zone, so shift-and-stack can run later without re-reading photos. Details in §6.

## How it was measured

- **Regions.** 12 strips of full detector rows (120 native rows × the zone's width, 0.15–0.44 deg² of common sky each,
  4.26 deg² in all, 18,584 Gaia stars), each through a known asteroid, in 12 hunt sessions with 4 exposures: 4 strips
  each for D1/D4, D2/D5 and D3/D6. Per strip: the SWIR detector and its partner, 4 exposures each (8 photos, 96 in
  all), read by byte-range requests from NASA S3 (IMAGE rows + FLAGS tiles + the ePSF, ~2.3 MB per photo strip;
  18–26 MB per region in 1.3–5 min at this network's 0.1–0.3 MB/s).
- **Photo preparation** as core does it: `build_mask` (contracts/flags.md), background mesh 32 px, noise = the
  background rms (no VARIANCE read).
- **Maps.** `coadd.accumulate` resamples each photo onto one grid (2.05″ = ⅓ native pixel, in the first exposure's
  frame) with inverse-variance bilinear weights; `coadd.detection_map` is stack.py's matched filter
  (num = K ⋆ wD, den = K² ⋆ w, S/N = num/√den, normalised by the map's own robust σ). K = the detector's ePSF. Maps:
  every single photo, every run of 2, 3 and 4 consecutive exposures, pair sum (S/N_a + S/N_b)/√2 and pair χ²
  (Σ max(S/N, 0)², turned into a Gaussian-equivalent S/N) for each exposure and for the 4-exposure coadds.
- **Same sky for all maps**: only grid pixels covered by all 8 photos, 6 grid px inside.
- **Depth**: the 5σ limit = 5 × the matched-filter flux error (median over the strip, 889.9 µJy per MJy/sr·px);
  Gaia completeness = share of Gaia DR3 stars (G 12–20, moved to the session's epoch) with a peak ≥ threshold within
  4″, minus the same share at positions 30″ away (chance), per 0.5 mag of G; G50 = where that crosses 50%. Gaia's
  G < 20 cut means a G50 beyond ~19.8 can't be measured ("> 19.8").
- **False alarms** = peaks of the *negated* map (noise and dark artefacts; for χ² maps the χ² of the negated maps),
  per deg², median over maps. Negative peaks deeper than −20σ are counted apart as "holes". To compare at equal false
  alarms, each map's threshold is set where its negative peaks reach 10 (or 3) per deg².
- **Today's detector**: core `detect` (sep, thresh 2, deblend 0.005, aperture S/N ≥ 5) on each photo strip, and on
  the negated strip for its false alarms. Checked against the sky run's real dot files for the 40 photos that have a
  local copy (5 strips): Gaia G50 17.63 vs 17.59 (D1), 17.77 vs 17.75 (D2), 17.31 vs 17.26 (D4), 15.40 vs 15.29 (D5);
  dots per strip within 2–9%. So "sep" below stands for the sky run's dots.
- **Asteroids**: MPC orbits via kete (the hunt's cache, positions per photo from SPHEREx), S/N within 4″ of the
  prediction in each single photo, in the static 4-exposure coadd, and in a coadd shifted along the predicted motion.

## 1. Depth: session coadds and pairs vs single photos

All 12 strips. Each cell: 5σ limit (AB) / Gaia G50 at S/N 5 / negative-image false alarms ≥ 5σ per deg² (median over
maps; "+h" = holes deeper than −20σ).

| detector | today (sep) | matched filter, 1 photo | coadd N = 2 | N = 3 | N = 4 |
|---|---|---|---|---|---|
| D1 | – / 17.65 / 0 | 17.96 / 18.51 / 21.5 | 18.36 / 19.11 / 39 | 18.58 / 19.39 / 39 | 18.72 / 19.52 / 34 |
| D2 | – / 17.79 / 0 | 18.01 / 19.23 / 2.3 | 18.39 / 19.67 / 0 | 18.59 / > 19.8 / 0 | 18.72 / > 19.8 / 0 |
| D3 | – / 18.00 / 0 | 18.10 / 19.22 / 0 | 18.51 / 19.76 / 2.8 | 18.70 / 19.81 / 2.6 | 18.81 / 19.83 / 1.2 |
| D4 | – / 17.23 / 28 +8h | 17.95 / 17.79 / 111 +27h | 18.50 / 18.81 / 161 | 18.64 / 19.00 / 174 | 18.73 / 19.13 / 190 +65h |
| D5 | – / 15.38 / 2.4 +2h | 17.13 / 15.92 / 33 +6h | 17.71 / 16.71 / 40 | 17.91 / 16.97 / 36 | 18.04 / 17.13 / 33 +13h |
| D6 | – / 14.66 / 11 +3h | 16.76 / 15.17 / 51 +17h | 17.34 / 15.89 / 76 | 17.54 / 16.13 / 86 | 17.68 / 16.30 / 94 +40h |

(G50 is in Gaia G, a visible band: stars are faint at 4–5 µm, so MWIR G50s are low; the 5σ limits are the
detectors' own AB.)

At equal false alarms (D1, the only SWIR detector with enough negatives to set a threshold; D2/D3 stay below
3/deg² even at S/N 4.5, so their S/N-5 column above is already at sep's false-alarm level):

| D1 G50 | today (sep) | 1 photo | N = 2 | N = 3 | N = 4 |
|---|---|---|---|---|---|
| at 10 false alarms / deg² | 17.65 | 18.36 (5.6σ) | 18.85 (6.2σ) | 19.09 (6.3σ) | 19.28 (6.3σ) |
| at 3 false alarms / deg² | 17.65 | 18.19 (6.4σ) | 18.56 (7.7σ) | 18.87 (7.5σ) | 18.96 (7.9σ) |

S/N on the same Gaia stars (single-photo S/N 8–30), median ratio coadd / single for N = 2 / 3 / 4: D1 1.54 / 1.89 /
2.14, D2 1.44 / 1.70 / 1.91, D3 1.54 / 1.82 / 2.02, D4 1.78 / 2.03 / 2.21, D5 1.77 / 2.10 / 2.35, D6 1.61 / 1.94 /
2.20. MWIR / SWIR S/N on the same stars: D4/D1 0.49–0.64, D5/D2 0.13–0.18, D6/D3 0.09–0.12.

Measured against the sky run's own dot files (5 strips, 40 photos), Gaia G50: D1 17.59 → 18.44 (1 photo) → 19.45
(N = 4); D2 17.75 → 19.22 → > 19.8 (completeness at G 19.25: 0.11 → 0.49 → 0.70); D4 17.26 → 17.79 → 19.03;
D5 15.29 → 15.94 → 17.15.

What the tables say:

- **Coadds:** each step in N is worth what √N promises (0.38 / 0.60 / 0.75 mag) in SWIR's 5σ limit: +0.38–0.41 /
  +0.58–0.62 / +0.71–0.76. MWIR gains more (+0.55–0.58 / +0.69–0.78 / +0.78–0.92) because one MWIR photo carries
  non-Gaussian junk that averages down. In Gaia completeness at S/N 5: D1 +0.60 / +0.88 / +1.01; at equal false
  alarms +0.49 / +0.73 / +0.92 (10/deg²) and +0.37 / +0.68 / +0.77 (3/deg²): the false-alarm threshold creeps up
  with N, because coadds have heavier negative tails (resampling, coverage edges; one strip had a 2-exposure coadd
  with a 120″ band of negative bias where one exposure had a masked patch: the extraction needs stack.py's coverage
  rule or per-photo weights that fall smoothly to a hole).
- **Matched filter vs today's dots on one photo:** +0.85 (D1), +1.47 (D2), +1.22 (D3, vs sep on the strips) in G50
  at S/N 5. sep's thresh-2 pixel cut plus an r = 2 px aperture (12.6 pixels of noise for an undersampled PSF) throws
  away most sources between matched-filter S/N 5 and 9. In D2/D3 that comes at no false-alarm cost (0–3 negatives per
  deg²); in D1, where the matched filter has 21 per deg², the gain at an equal 10/deg² is +0.71 and at 3/deg² +0.54.
- **MWIR has dark holes.** Single MWIR photos show 6 (D5) to 27 (D4) negative peaks per deg² deeper than −20σ
  (70–330 per photo; sep sees 2–8 per deg²): pixels at −0.7 to −16 MJy/sr against a 0.2 MJy/sr sky, mostly unflagged
  and next to PERSIST (17) or OUTLIER (19) pixels (over-corrected persistence of an earlier bright star, by the
  look). They also raise the noise-like negatives (D4 111/deg² ≥ 5σ). They don't make false *positive* sources, but
  they defeat any negative-image false-alarm control and bias photometry where they sit; at an equal 10/deg² the
  MWIR matched filter is no better than sep (D4 16.11 vs 16.65, D5 15.37 vs 15.38, D6 14.15 vs 14.60) although the
  4-exposure coadd still gains (D4 17.21, D5 16.14, D6 14.77). Growing the PERSIST mask by 3 px removed 1 of 2 holes
  in a test photo: not enough; they need their own mask (≥ 3 adjacent pixels below sky − 10σ, grown 2 px).
- **Glitches (positive only).** In single photos, uncatalogued S/N ≥ 5 peaks that are not a piece of a brighter source
  and are *not* seen again in the session's other exposures: D1 24/deg², D4 177/deg² (one strip). That is the real
  per-photo false-positive rate of single-photo detection (≈ 290 and 2,150 per full photo), and it is what a session
  coadd removes when it requires a source in ≥ 2 exposures (and what a coadd without outlier rejection keeps: a
  strong cosmic ray in one exposure survives at half strength in an N = 4 sum).
- **Uncatalogued peaks are mostly real.** ~47% of single-photo matched-filter peaks are > 4″ from any
  Gaia/CatWISE/2MASS source, but of those that aren't a piece of a brighter source (no brighter peak within 12″)
  98% (D1) and 83% (D4) repeat in the session's other exposures (one strip). They are blends, wing pieces of bright
  stars (stack.py's glow/residual machinery handles those) and sources fainter than the catalogues; they are not a
  false-alarm measure.
- **Pairs:** Gaia G50 at S/N 5 for the single-exposure pair sum / χ² vs the SWIR photo alone: D1+D4 18.59 / 18.72 vs
  18.51 (false alarms 81 / 135 vs 21.5 per deg²), D2+D5 18.75 / 19.17 vs 19.23 (13 / 35 vs 2.3), D3+D6 18.81 / 19.18
  vs 19.22 (29 / 53 vs 0). The sum's S/N on stars is 0.98–1.08 of D1 alone for D1+D4 and ~0.75 for the others; χ²
  ~1.0. A source with equal S/N in both photos would gain √2 (+0.37 mag) in the sum. The pair's value is the glitch
  test: a real source is in both photos of the same instant.

## 2. Movers in session coadds

84 known asteroids (MPC orbits via kete) lie in the strips, V 15.9–21.0, 15–72″/h. For each: S/N per single photo
(mean of 4), in the static 4-exposure coadd at the mean predicted position, offset of the static peak from that
position, and the coadd tracked along the predicted motion; "sep" = in how many of the 4 photos today's detector has a
dot within 4″.

| asteroid | V | rate ″/h | trail in session ″ | single S/N | sep | static coadd S/N | offset ″ | tracked S/N |
|---|---|---|---|---|---|---|---|---|
| Zelima | 15.9 | 26.9 | 2.8 | 46.3 | 2/4 | 88.7 | 0.44 | 93.4 |
| Aetolia | 16.0 | 19.6 | 2.1 | 47.0 | 4/4 | 95.2 | 1.10 | 95.2 |
| Klare | 16.5 | 47.0 | 4.9 | 30.3 | 0/4 | 48.3 | 0.75 | 50.9 |
| Sarabhai | 17.7 | 41.0 | 4.3 | 8.5 | 0/4 | 15.5 | 0.23 | 16.4 |
| (6038) | 17.8 | 28.6 | 3.0 | 12.3 | 4/4 | 22.6 | 3.4 | 23.2 |
| Spitzweg | 18.1 | 36.9 | 3.9 | 5.0 | 0/4 | 10.4 | 0.02 | 11.5 |
| (23303) | 18.1 | 28.1 | 3.0 | 6.4 | 0/4 | 12.1 | 0.74 | 12.6 |
| (6967) | 18.1 | 63.2 | 6.6 | 13.3 | 0/4 | 20.0 | (star 10″ away) | 24.7 |
| Siljan | 18.3 | 33.5 | 3.6 | 5.6 | 0/4 | 9.5 | 0.54 | 9.5 |
| (6693) | 18.9 | 24.3 | 2.5 | 3.1 | 0/4 | 5.8 | – | 5.8 |
| Keller | 19.1 | 45.5 | 4.8 | 2.7 | 0/4 | 3.8 | 2.2 | 4.6 |
| (44476) | 19.5 | 36.2 | 3.8 | 1.7 | 0/4 | 4.5 | 1.6 | 5.3 |

By V: < 18.3: 9 asteroids, single-photo matched-filter S/N ≥ 5 for 8, a sep dot in ≥ 2 of 4 photos for 3, static
coadd S/N ≥ 5 for all 9; 18.3–19.0: 2, static ≥ 5 for both; 19.0–19.5: 9, none ≥ 5 static (median 3.8), 1 tracked;
19.5–20: 12, median static 3.1. (Fainter ones sit on field stars too often to count.)

- Every asteroid at 18–47″/h is found in the static coadd at 1.6–2.1× its single-photo S/N (√4 = 2); tracking adds
  0–6% at those rates and 23% for (6967) at 63″/h (6.6″ trail). The coadd turns V ≈ 18 main-belt asteroids from
  borderline single-photo dots (which sep mostly misses) into S/N 10–25 sources; its limit for them is V ≈ 19.
- **Where measured:** the static peak sits at the mean of the exposure positions, i.e. the asteroid at the session's
  mid-time (0.02–1.1″ off at S/N ≥ 10, one 3.4″). A coadd source therefore carries the session's mean MJD, and link
  can treat it as a 1-session "dot" whose shape is stretched along the motion (the per-exposure positions come from
  forced fits in each photo).
- Trail loss: a 2–5″ trail on a 7.5″ PSF costs 0–6%; 6.6″ costs ~20%; at 150″/h (16″) more than half. Above ~60″/h a
  session coadd needs rate trials; the number is small: (rate × 6.4 min / 3″ tolerance)² ≈ 5–30 rate trials for
  60–150″/h, i.e. 5–30 extra maps per session.
- The pair coadd (SWIR + MWIR tracked) is no better than the SWIR detector alone for these asteroids (they are blue in
  SPHEREx's bands too).

## 3. Rate gap: which step finds a body at which rate

SPHEREx looks ~90° from the Sun, so Earth's motion is mostly along the line of sight: distant bodies move slowly and
their rate swings through zero within a visit. Rates below are from the visit geometry at real photo times (Earth +
SPHEREx ephemeris, bound orbits, hunt zone at ecliptic latitude 0°; NGP field 30°; NEP 90°):

| distance | rate ″/h, hunt (ecliptic) | NGP (30°) | NEP (90°) | drift over a visit ″ | visit-to-visit ″ |
|---|---|---|---|---|---|
| 40 AU | 0.003–2.3 | | | 39–970 | 7,800–12,600 |
| 70 AU | 0.002–1.3 | | | 3–525 | 4,800–6,900 |
| 150 AU | 0.001–0.49 | 0.48–0.68 | 0.97–1.0 | 10–140 (ecl.), 220–410 (30°), 200–670 (90°) | 2,600–2,700 (ecl., 30°), 570–1,340 (90°) |
| 300 AU | 0.0–0.25 | 0.24–0.34 | 0.48–0.50 | 5–72 (ecl.), 110–210 (30°), 100–340 (90°) | 1,300–1,360; 290–670 at 90° |
| 1000 AU | 0.0–0.07 | 0.07–0.10 | 0.15 | 1.5–22 (ecl.), 33–62 (30°), 31–101 (90°) | 390–410; 86–200 at 90° |

So a Planet X at 300–1000 AU moves 0–0.5″/h, a 40 AU TNO 0–2.3″/h (not 2–4″/h as at opposition). Inside a session
(6.4 min) every body slower than 2″/h moves < 0.2″: no in-session motion is measurable for any of them.

Which step finds a body, at what depth (hunt zone; link completeness from the hunt's injection test,
results/hunt/completeness.json; orphans from results/hunt/summary.json):

| rate ″/h | bodies | step today | how it is found | depth today | gap / note |
|---|---|---|---|---|---|
| > 900 | NEOs close to Earth | none | 113 s trails > 28″ break into pieces or fail elong ≤ 3 | – | **gap**: streak detection (relink's extraction need) |
| 150–900 | NEOs, fast MBAs | link fast triplets (relink PR #367) | 3 dots on a line in one session | single dots; ~20% at 600–900″/h | trails > 16″: a session coadd can't help |
| 2.2–150 | MBAs, Hildas, Trojans | link movers → tracklets | 2+ dots of one session within 3″ + 1″/min Δt | single exposure: 50% at AB 17.1 (D1) … 16.1 (D6); 94–97% found at S/N ≥ 20 | a static session coadd finds them < 60″/h, S/N × 1.6–2.1 (§2); the matched filter alone +0.85 mag (D1) |
| 1–2.2 | 30–50 AU TNOs, slow Centaurs | link movers (sessions don't chain within 4″) | the mover rule at near-zero in-session rate | 83% at S/N ≥ 20 | orphans stops at 1″/h drift |
| 0.46–1 | 40–70 AU TNOs | link movers 65%, the rest fixed in one visit | | 65% as movers | **gap (partial)**: a third is neither a clean mover nor a clean fixed source |
| 0.02–0.46 | 70–1000 AU (Planet X), slow TNOs | link chains them into a fixed source seen in one visit (87–91%) → orphans pairs two visits | drift fit + bound orbit at 70–1000 AU + flux match, shifted control, injection | dots at S/N ≥ 5: 5σ at AB 17.26 (D1), 17.36 (D2), 17.51 (D3), 17.71 (D4), 16.22 (D6) | depth = one exposure |
| < 0.02 | > 1000 AU, stars' proper motion | highpm (3–120″ between visits), stack (featured targets only) | | single exposure (highpm), visit stack (5 targets) | |

**Orphans (the existing Planet X search across visits)**, hunt: 6,339 single-visit sources → 1,288 not in Gaia,
CatWISE or 2MASS → 480 orphans after bright-fragment, edge, saturated and blend cuts → 404 with an acceptable drift
→ 709 pairs tested → 1 passing, against 0.92 expected by chance (50 shifted controls, p = 0.61): nothing above chance.
Its depth is that of one exposure, because an orphan is a chain of single-photo dots (≥ 3 dots over ≥ 6 h). The
brightness a planet needs scales as d⁴ (reflected light), so depth Δm reaches 10^(Δm/10) times further. A
Neptune-size body (albedo 0.4) at the current orphan depth (D1 5σ 17.26) sits near 290 AU (distant.json's reach);
with matched-filter dots (5σ 17.96, +0.7) ~340 AU; with 4-exposure session coadds (18.72, +1.46) ~400 AU; with a
visit-long shift-and-stack (~19.3, +2.1, §4) ~470 AU (estimates from the d⁴ law).

**Gaps, named:**

1. **Depth is one exposure everywhere.** No step uses more than one photo's photons for detection, except `stack` on
   five featured targets. Every slow route (orphans, highpm, link's fixed sources) inherits the single-photo limit.
2. **1–2″/h is caught only by link's mover rule at 83%**, and 0.46–1″/h at 65%: the rest are neither movers nor
   chained fixed sources; orphans accepts drift ≤ 1″/h only. Widening ORPHANS_RATE_MAX to 2.5″/h (40 AU: up to 2.3″/h)
   would close this for TNOs; it lets more junk drift in, which the pair step's bound-orbit and flux tests must hold.
3. **Orphans misses distant bodies near the ecliptic poles.** There consecutive visits are ~30 days apart and a 1000 AU
   body moves only 86–200″ between them, 290–670″ at 300 AU: below ORPHANS_SEP_MIN_ARCSEC (300″), and pairs need
   ORPHANS_MIN_DT_DAYS ≥ 60. Proposal (orphans is not my area): scale the separation window by the visits' parallax
   baseline (predict it from Earth's positions at both times for 70 and 1000 AU, as the pair model already does)
   instead of fixed 300–7,200″, and allow pairs ≥ 20 days apart.
4. **No cross-zone pass.** 70 AU moves 1.3–1.9° between visits; partners outside the zone are listed as `open`
   (90 in the hunt) but nothing pairs them yet.
5. **> 900″/h** (NEOs): no step.

**Where session coadds and shift-and-stack feed in:**

- Session coadds feed **link** with deeper "dots": a coadd source is one more dot with the session's mean time and
  `n_exp` exposures. Fixed-source chaining, orphans and highpm need no change beyond reading these rows; orphans'
  drift fit gets one point per session instead of one per photo (fewer, more precise points).
- Shift-and-stack along physical orbits produces **per-visit candidates with a distance and an own motion** already
  fitted (the trial that peaked). These are exactly orphans' input after its drift fit. The pair step can then take
  them directly: each candidate's trial (d, own motion) predicts where it is in the other visit to a few arcsec, so
  the pairing window shrinks from 300–7,200″ to tens of arcsec and chance pairs fall by ~10³: a per-visit threshold
  of ~6σ suffices, because the second visit confirms.

## 4. Digital tracking (shift-and-stack over a visit)

**Gain.** A spot is covered per visit by 19 photos per detector (hunt median: 58 exposures in 20 sessions,
2.9 exposures per session; 18–21 photos per detector). Coadding along the right path gives √19 = 4.4 in S/N,
+1.6 mag before trials, if the photos' noise is independent and static sources are removed. Measured on known
asteroids within one session: a tracked 4-exposure coadd reaches 1.9–2.3× the single S/N (√4 = 2).

**What the path looks like.** Over a visit the motion is not a line: Earth's and SPHEREx's motion curve it by
3.6–335″ from a straight line (hunt, 1000–40 AU); a parabola leaves 0.01–1.7″ in the hunt (up to 7″ at 150 AU in
Barnard's Star's field). A grid of linear rates is the wrong basis; the right one is physical: a heliocentric
distance d plus the body's own velocity (bound orbit), with the parallax computed from Earth + SPHEREx at each
photo's time (`coadd.parallax_offsets`, `coadd.distance_trials`).

**Trial grid** (`coadd.distance_trials`: distance steps so the parallax path changes by < tol, own-motion lattice
with step tol up to the escape speed). Per spot (the photos that really cover one spot in one visit; 30 random hunt
spots × 2 visits, spans 6–41 days, median 12) and, as an upper bound, for the whole hunt-zone visit (21 and 45 days):

| distances | per spot, tol 2″: median (mean, max) | per spot, tol 1″: median | whole zone visit 1 / 2, tol 2″ |
|---|---|---|---|
| 150–1000 AU | 335 (2,149; 15,204) | 1,884 | 781 / 18,209 |
| 70–1000 AU | 4,910 (41,832; 297,099) | 34,859 | 14,106 / 360,589 |
| 40–1000 AU | 41,567 (377,860; 2.7 M) | 309,000 | 139,237 / 3.3 M |
| linear 0.05–0.5″/h (wrong shape, for comparison) | | | 6,203 / 28,188 |

The count grows roughly as the visit span cubed. Featured fields at 30–90° latitude have longer visits (18–55 days):
150–1000 AU at tol 2″ needs 1,900–61,000 trials there. tol 2″ is enough: the PSF FWHM is 7.5″, so a 2″ miss loses
≤ 9% S/N.

**False alarms.** For Gaussian noise the number of peaks above u per deg² of one matched-filter map is
≈ 4.0 × 10⁴ · u · e^(−u²/2) (PSF correlation length 4.5″). For 0.01 false candidates per deg² per visit (≈ 1 per
hunt zone): one map needs 5.8σ; 2,000 trials need 7.0σ, 18,000 need 7.35σ: the trials cost 0.2–0.25 mag of the 1.6
(counting every trial as independent; neighbouring trials overlap, so this is pessimistic). Real maps have
heavier tails (above: D1 21 negatives/deg² at 5σ, D4 111, where Gaussian noise gives 0.7), so the controls must be measured,
not computed:

- threshold from the trial count, checked on time-scrambled stacks (the same photos with their times shuffled
  between sessions: no real mover adds up, the noise and junk do);
- the same search on negated maps (dark junk) and with the other visit's static coadd subtracted first (template
  subtraction: removes all fixed sources; the body has moved 400–12,600″ between visits, so it doesn't subtract
  itself);
- per-candidate consistency: no single photo may carry more than half the S/N² (stack.py's STACK_TRANSIENT_SHARE),
  and the S/N must grow along the path (first and second half each ≥ 3σ);
- injections of synthetic bodies along physical paths at known magnitudes (completeness and the depth claim);
- the second visit: a candidate's (d, own motion) predicts the other visit's position; orphans' pair step and its
  shifted control do the rest.

Net depth estimate: one photo's matched-filter 5σ (D1 18.0 AB) + 1.6 (√19) − 0.25 (trials) ≈ **19.3 AB at a 0.01/deg²
false-alarm rate per visit**, before losses from template subtraction (√2 if the template is as deep as the visit;
~10% if the template is the other visit plus earlier ones). That is ~2 mag deeper than today's orphans (17.3) and
~0.6 mag deeper than session coadds alone.

**Compute** (measured: 28 ms CPU per trial per deg² summing 21 session maps at 3.08″ with integer shifts, numpy, one
core of this Mac; the shift-and-add is memory-bound):

CPU = mean trials per spot-visit × 2 visits × area × 28 ms (hunt per-spot means; the zone-wide counts give the upper
bound in brackets):

| search | hunt (94 deg², 2 visits) | whole sky (41,253 deg², 2 visits; estimate, mean ~5,000–10,000 trials) |
|---|---|---|
| 150–1000 AU, tol 2″ | **3 core-h** (14) | ~3,000–6,400 core-h |
| 70–1000 AU, tol 2″ | **61 core-h** (274) | ~20× that: 60,000–130,000 core-h |
| 40–1000 AU, tol 2″ | 550 core-h (2,500) | not feasible this way |

The sky numbers need a hierarchical search (coarse trials at 4× the tolerance on 4× binned maps, refine only peaks
≥ 4σ: ~10–30× fewer full-resolution trials) or a GPU (the shift-and-add is memory-bound and ~100× faster on one),
not more CPUs.

**Storage.** The search needs each session's matched-filter numerator and denominator on a common sky grid. At
native 6.15″ in float16: 16.6 MB per photo, or ~19 MB per session and detector (a 4-exposure session covers 1.15
frames). Hunt: ~1,270 session-detectors → **24 GB** (12 GB for SWIR only, where reflected sunlight is brightest);
whole sky: ~171,000 → **3.2 TB** (8 TB if kept per photo). At 3.08″ (what the bench used) × 4. The photos themselves
are ~56 MB each to read (hunt 207 GB, sky 28 TB), so maps must be written during the one read of the combined
rerun, not re-derived later.

## 5. Cost and time

Per-photo CPU, measured on this Mac (one core, M-series) on 120-row strips and scaled by pixel count to a full
2040 × 2040 photo:

| step | CPU per photo |
|---|---|
| sky run's dots today (allsky test, high latitude; dense fields 25–208 s) | 7–14 s |
| sep detection alone (on the strip, scaled) | ~2.7 s |
| single-photo matched-filter map at native resolution | ~0.5 s (estimate from the map cost per Mpx) |
| resample one photo onto a session grid (2.05″) | 1.2–1.4 s |
| session matched-filter map (2.05″, ~44 Mpx for 4 exposures; per photo) | ~1.5 s (at 3.08″: ~0.7 s) |
| pair map (two detectors on one grid; per photo) | ~1 s |
| shift-and-stack, per trial per deg² (21 session maps) | 28 ms |

Coadds and pairs only add CPU to the combined rerun: the photos are read once anyway. Reading is what limits a home
machine: 56 MB per photo is 207 GB for the hunt and 28 TB for the sky; this Mac's link gave 0.1–0.4 MB/s from S3
today (the hunt would take a week); a VM in us-east4 reading from us-east-1 is far faster (estimate ~50–100 MB/s,
not measured here).

| job | CPU | this Mac (10 cores; 2 workers while shared) | lead's PC (16 cores) | Google spot n2-highcpu-64 (us-east4, €0.60–1.00/h; 1 vCPU ≈ 0.6 Mac core) |
|---|---|---|---|---|
| (a) matched filter + session coadds, hunt (3,692 photos), CPU only | ~3.5 core-h | ~2 h | ~15 min | ~6 min, ~€0.10 |
| (a) the same, whole sky (495k photos) | ~450 core-h | – | ~30 h CPU, but 28 TB to read | ~12 VM-h ≈ €7–12, on top of the rerun's own ≥ 1,400–2,000 core-h |
| (b) pair maps, whole sky | ~140 core-h | – | ~9 h | ~4 VM-h ≈ €3–4 |
| (4) tracking 150–1000 AU, hunt | 3 core-h | ~1.5 h | ~12 min | ~€0.05–0.10 |
| (4) tracking 70–1000 AU, hunt | 61 core-h | ~30 h | ~4 h | ~1.6 VM-h ≈ €1–2 |
| (4) tracking 40–1000 AU, hunt | 550 core-h | – | ~34 h | ~14 VM-h ≈ €9–15 |
| (4) tracking 150–1000 AU, whole sky | 3,000–6,400 core-h | – | 8–17 days | 80–170 VM-h ≈ €50–170; hierarchical ~€5–15 |
| session maps stored | | | | hunt 24 GB (pennies); sky 3.2 TB ≈ €30–65/month (Nearline–Standard) |

No VM was started for this; the cloud rows are estimates from the measured per-photo times above.

## 6. Recommendation for the one combined rerun (extraction v2)

In order of gain per cost:

1. **Matched-filter (ePSF) detection on every single photo.** Extraction v2 (#370, behind `SKYSHIFT_DOTS_V2`) already
   adds it: a matched-filter pass on the residual, `MF_PASS_SNR` 5, new dots flagged `sep_flag` 0x400 with `snr_mf`.
   These strips agree with its gain independently: Gaia G50 +0.85 (D1) to +1.47 (D2) over the sky run's dots at S/N 5
   (+0.71 in D1 at equal false alarms), ~0.5 s per photo, no new reads. What they add for v2: a fixed 5σ is right
   for D2/D3 (0–3 negatives per deg², as v2's 28–31 per D2 photo) but D1 has ~21 per deg² at 5σ (~250 per photo);
   5.6σ gives 10 per deg². Set the threshold per detector, or per photo from that photo's own negative peaks, and
   higher in MWIR until the dark holes are masked. On these sparse strips the matched-filter map has ~3–4× the sky
   run's sources at ≥ 5σ (7,300–9,400 vs 1,900–2,300 per deg² in SWIR); v2 measured +40% dots on a crowded D2 photo,
   after its finer sep passes. Plan dot storage for somewhere in between.
2. **Mask MWIR dark holes** (unflagged pixels far below the sky next to PERSIST (17) / OUTLIER (19) pixels: 70–330
   holes deeper than −20σ per MWIR photo). Without it neither the matched filter nor any coadd has a usable false-alarm control in D4–D6.
3. **Session coadds per detector** (2–5 exposures of one session, one worker holds the session's photos):
   detection on the coadd's matched-filter map, then a forced ePSF measurement of every coadd source in each photo
   (the per-exposure positions/fluxes link needs), keeping the coadd row with the session's mean MJD, `n_exp`, and
   the share of S/N² in the strongest photo (> 0.5 = one-photo glitch, not a source). 5σ +0.4 / +0.6 / +0.73 mag
   for N = 2 / 3 / 4 (Gaia G50 in D1 +0.49 / +0.73 / +0.92 at equal false alarms); ~3 s per photo. Static coadds are enough for everything < 60″/h; add 5–30 rate trials for
   60–150″/h only if link wants them.
4. **Write each session's matched-filter num/den maps** (float16, native grid) for the hunt zone (24 GB; 12 GB SWIR
   only) — and for the sky only if digital tracking is going to be run (3.2 TB). This is what makes shift-and-stack
   possible later without re-reading 28 TB.
5. **Pairs: no detection image.** The partner's forced flux per dot (v2's `partner_flux_ujy`, partners processed
   together) is enough: it is the glitch test.
6. **After the rerun, not in it:** shift-and-stack over each hunt visit for 150–1000 AU (3 core-h; then 70–1000 AU,
   61 core-h, a few hours on the lead's PC), with the controls of section 4, feeding orphans' pair step with
   (d, own motion) per candidate. The prototype functions (`coadd.distance_trials`, `parallax_offsets`,
   `accumulate` with shifts, `combine_maps`) are the pieces; the step itself is not written.

Changes others own (proposed only, not made here):

- **link / tracklets (agent "relink")**: accept session-coadd rows as dots (mean MJD, `n_exp`, elongation along the
  motion), count them as one session; for fast pairs, the coadd's elongated peak gives the motion direction in one
  row.
- **orphans**: ORPHANS_RATE_MAX 1.0 → 2.5″/h (40 AU TNOs); separation window from the parallax baseline of the two
  visits instead of a fixed 300–7,200″ and pairs ≥ 20 days apart (ecliptic poles); a cross-zone pass for `open`
  orphans; accept tracked per-visit candidates (d, own motion) as orphans with a narrow predicted partner window.
- **contracts**: a dot row from a session coadd needs `n_exp` (int8), `mjd` = session mean, and `glitch_share`
  (float16) — a contract change for P1.

## 7. The session-stack step, built and measured (agent "sessionstack", 16 Nov)

### In plain words

1. **The step exists and is off by default.** `skyshift-pipeline sessionstack --zone <z>` stacks every session of
   2+ photos per detector, detects on the stack, then measures each stacked source in every single photo. `all` runs
   it right after `dots` only with `SKYSHIFT_SESSION_STACK=1`; with the switch off no other step reads its files and
   every output is the same as before (tests: link and orphans run twice, off then on).
2. **It sees 1.2–1.9 mag deeper than today's dots** (Gaia G50 per detector, two real hunt sessions of 4 photos, at
   10 false sources per deg²): D2 19.5 and D3 19.4 instead of 17.8 and 17.6. That is 0.6–1.4 mag more than the
   single-photo matched filter (extraction v2's new pass) at the same false-alarm rate.
3. **Known asteroids:** of 109 isolated SWIR sightings with V < 18.5, the stack finds 83 (76%), the single-photo
   matched filter in 2+ photos 63 (58%), today's dots in 2+ photos 37 (34%). At V 18–18.5 in D1: 8 of 17 vs none.
4. **Moving things stay linkable.** Every stacked source is measured in each photo, so a fast mover keeps its
   per-photo positions (rate within 5% of MPC's, median), and a cosmic-ray hit carried by one photo is flagged, not
   hidden (glitch). Positions come from ePSF fits in the photos: 0.12–0.15″ rms against Gaia.
5. **link and orphans accept the stack rows** (only with the switch on): a source too faint for any single photo
   becomes a fixed source from its stack rows, and orphans can pair it. Orphans also gets the two changes the study
   asked for: rate limit 1 → 2.5″/h (40 AU bodies: 0 → 63% recovered) and a pairing window from the parallax between
   the two visits (ecliptic poles, visits a month apart: 0–1% → 60–100% recovered at 70–1000 AU). The chance level
   stays at ~1 passing pair (0.92 → 0.98 expected on the hunt's real orphans).
6. **Cost:** 6–17 s of CPU per session and detector on one Mac core (~3 core-hours for the hunt, ~420 for the sky).
   Run as a separate step it reads every photo again (197 GB for the hunt: ~17 h on this Mac's link); fused into the
   rerun's dots worker it reads nothing extra.

### What runs

`coadd.run` (step `sessionstack`; config `SESSION_STACK_*`):

1. **Tasks:** per (session, detector) with ≥ `SESSION_STACK_MIN_EXP` (2) photos. Hunt: 915 tasks over 3,519 of its
   3,692 photos (674 of 4 photos; 173 one-photo sessions are skipped).
2. **Per photo:** extraction v2's background (dots.photo_context), the glow model subtracted (stack.glow_cut),
   MWIR dark holes masked (`dark_holes`: the study's §6 item 2), saturated cores noted.
3. **Common frame** = the middle photo's pixel grid. The other photos enter per 256-px tile by an integer pixel shift,
   their sub-pixel phase going into the ePSF kernel (no resampling of the data, so the noise stays honest):
   num = Σ w·d·K, den = Σ w·K² (inverse variance w, matched-filter kernel K). A pixel counts where ≥ 2 photos cover it.
4. **Detection:** S/N = num/√den over its robust σ, v2's local significance around saturated holes and bright peaks,
   hole rims merged into one peak. Threshold per session × detector: the level at which the map's own negative peaks
   (hole rims excluded) give 10 per deg² (`SESSION_STACK_FA_PER_DEG2`), at least 5σ, at most 8σ.
5. **Per-photo measurement** of every stacked source: ePSF flux and position (psf.psf_photometry, psf_position).
   From those: forced S/N over the photos (`snr`), the share of S/N² in the strongest photo (`glitch_share`;
   `glitch` when the other photos together are below 2σ), a weighted line through the photo positions (`rate_arcsec_h`,
   `pa_deg`, `motion_snr`), and the line's mid-session point as the position (`ra_psf`, `dec_psf`, `pos_err_arcsec`).
6. **Writes** `session_stacks/<key>.parquet` (one row per stacked source; metadata footer: threshold, false alarms,
   depth, timing), `<key>_photos.parquet` (one row per source and photo), `<key>_moving.parquet` (per-photo sky
   positions of the moving ones: what link reads), `session_stacks/index/<zone>.parquet` (key ↔ obs_id, so tiles and
   other zones find the stacks), `results/<zone>/session_stack.json`; for `SESSION_STACK_MAP_ZONES` (hunt) also
   `session_stacks_maps/<key>.npz` (matched-filter flux and error, float16; coverage, int8) for shift-and-stack later.
   A session already written is skipped.

**link** (`link.with_stack_rows`, `coadd.load_stack_rows`): the stack rows of the zone's photos, less glitches, rows
near saturation and rows with a real dot of the same session and detector within 3″ (that dot is the source already),
join the 4″ friends-of-friends as one dot per session (session-mean MJD, photo/exposure/dot_row −1). A fixed source
needs ≥ 3 sessions as before, so a faint star seen only in stacks becomes a fixed source; its rows go to
`results/<zone>/stack_members.parquet`; `members.parquet` keeps real dots only. A stacked source that moves (≥ 5σ,
≥ 1″/h) and joins no fixed group gives its per-photo positions as pseudo-dots (dot_row −1) to the mover pairing;
the shuffled-exposure control sees them too (`control.with_stack_dots`). Stats under `steps.link.session_stack`.

**orphans**: `ORPHANS_RATE_MAX` 1 → 2.5″/h; the fixed 300–7,200″ / ≥ 60-day pairing replaced by `pair_window`
(Earth's parallax between the two times at 1000 and 40 AU, less / plus a bound orbit's own motion, ± 30″; pairs
≥ 20 days apart), and the ecliptic-latitude cut replaced by a cut along Earth's parallax direction (forward, the part
across ≤ 0.4 × separation: at the poles the parallax points any way in ecliptic coordinates). With the switch on, a
fixed source made only of stack rows is a candidate (catalogue test against refcat/<zone>.parquet, since crossid
never saw it), its drift is fitted on its stack rows, and its fluxes come from them.

### Measured: two hunt sessions × 6 detectors

Sessions 72 and 81 of the hunt (2025W43, 4 photos each, 12.8 deg² per stack), the sky run's own dot files of those
photos for "today", Gaia DR3 and the MPC predictions of the `known` cache.

Depth, Gaia G50 (stack / single-photo matched filter at the stack's false-alarm rate / single-photo matched filter at
5σ / today's dots):

| | session 72 | session 81 |
|---|---|---|
| D1 | 18.75 / 18.14 / 18.45 / 17.52 | 18.80 / 17.99 / 18.12 / 16.95 |
| D2 | 19.47 / 18.62 / 18.96 / 17.85 | 19.55 / 18.81 / 19.01 / 17.84 |
| D3 | 19.30 / 18.44 / 18.94 / 17.55 | 19.45 / 18.55 / 18.91 / 17.67 |
| D4 | 18.01 / 17.28 / 18.18 / 17.05 | 17.45 / 16.66 / 17.67 / 16.67 |
| D5 | 16.41 / 15.20 / 15.95 / 15.28 | 16.60 / 15.18 / 15.90 / 15.19 |
| D6 | 15.02 / 14.21 / 14.92 / 14.34 | 15.00 / 14.16 / 14.87 / 14.31 |

False sources and thresholds (per deg²; negatives = the map's negative peaks, the false-alarm estimate):

| | D1 | D2 | D3 | D4 | D5 | D6 |
|---|---|---|---|---|---|---|
| threshold (σ) | 5.7–6.0 | 5.0 | 5.0–5.7 | 8 (cap) | 5.3–5.9 | 7.7–8 |
| negatives at the threshold | 10 | 5–6 | 9–10 | 26–27 | 10 | 10 |
| negatives at 5σ | 16–21 | 5–6 | 9–13 | 46–48 | 13–16 | 18–19 |
| sources at the threshold | 2,450–2,500 | 3,900–4,200 | 4,800–4,900 | 2,240–2,340 | 1,400–1,450 | 580–670 |
| glitches flagged | 44–49 | 67–83 | 41–57 | 11–12 | 16–30 | 11–15 |
| moving ≥ 5σ and ≥ 1″/h | 11–12 | 19–23 | 33–35 | 12–13 | 3–5 | 3–5 |
| 5σ depth searched (AB) | 18.6–18.7 | 18.6–18.7 | 18.8 | 18.8 | 18.0 | 17.5 |

D4 keeps 26 negatives per deg² even at the 8σ cap: dark holes the mask does not catch yet.

Known asteroids (SWIR, MPC V < 18.5, no Gaia star G < 19 within 8″, matched within 4″): stack 83 of 109 (76%),
single-photo matched filter in ≥ 2 photos 63 (58%), today's dots in ≥ 2 photos 37 (34%). D1 by V: < 17 10/10 vs
9/10 (dots); 17–18 14/18 vs 7/18 (single-photo matched filter 12); 18–18.5 8/17 vs 0 (3); 18.5–19 5/27 vs 0. The misses
are faint or sit where the photos only partly overlap. No found asteroid is flagged as a glitch; 51 of the 204
found sightings move at ≥ 5σ inside the session, stack rate / MPC rate median 1.05 (10–90%: 0.86–1.34).

Positions (Gaia stars S/N 20–300): the photos' ePSF fits 0.12–0.15″ rms, median offset ≤ 0.07″; the map peak alone
0.7–0.88″ (a parabola on native pixels; 0.44″ bias even without noise, measured on synthetic stars). Static
stars with a false ≥ 5σ motion and ≥ 1″/h: 35 of 20,852 Gaia stars in D1 (0.17%), 0.39% in D3.

What link would get (session 72; rows after the glitch, near-saturation and 3″ dedup cuts, per deg²): D1 604, D2
1,726, D3 1,581, D4 308, D5 479, D6 105; 97–99% of them have a catalogue entry within 3″ (faint real stars). Moving
rows for the pseudo-dot route (all, of them known / catalogued / other): D1 23 (1/16/6), D2 72 (0/55/17), D3 110
(1/84/25), D4 10 (0/5/5), D5 10 (0/6/4), D6 3 (0/2/1). So the pseudo-dot route adds few movers: faint asteroids
rarely show a 5σ motion within one session. Their gain is the stack row itself, one per session, which tracklets'
recovery could use (not built: below).

Time and size on this Mac (one core per task, 2 workers): 14–17 s per SWIR session × detector, ~15 s D4, 6–8 s
D5/D6, of which preparing the 4 photos (background, glow, holes) ~10 s, the stack 2.4 s, detection 0.9 s, the
per-photo measurement 2.5 s; ~1.2 GB per worker. Reading the photos from S3 here: 35–50 s each at ~3 MB/s with 2 in
parallel. Per session × detector: tables ~10 MB (sources 4–7 MB, per-photo rows ~5.5 MB, moving ~50 KB), maps ~16 MB.

### Measured: orphans before → after (table-level injection, the shifted control)

Bodies at heliocentric distance r with a random bound own motion, seen at real session times of the zone's photo
list, 3–20 sessions per visit, positions ± 0.3″, drift fitted per visit; "before" = the old module and limits (rate
≤ 1″/h, 300–7,200″, ≥ 60 days), "after" = this branch. Recovered = the pair passes.

| r (AU) | hunt (visits 1 + 2, ~172 days) before → after | ecliptic pole (t_nep, consecutive visits, ~29 days) before → after |
|---|---|---|
| 40 | 0% → 63% | 0% → 0% |
| 70 | 78% → 78% | 0% → 60% |
| 100 | 75% → 75% | 0% → 72% |
| 150 | 79% → 79% | 1% → 68% |
| 300 | 92% → 92% | 0% → 83% |
| 600 | 100% → 100% | 0% → 93% |
| 1000 | 100% → 100% | 0% → 100% |

150 bodies per distance in the hunt, 100 at the pole. Median separation at the pole: 2,500″ (40 AU) to 99″ (1000 AU);
in the hunt 10,150″ to 408″. At the pole a 40 AU body drifts ~3.7″/h (Earth's whole velocity is across the line of
sight there), above the new 2.5″/h. In the hunt 25% of the 40 AU bodies fail the pair model's drift χ² (all are
inside the window): a visit-long straight line misfits Earth's turning reflex at 0.1–0.7″/h with the 0.005″/h
floor; starting the distance grid at 30 AU instead of 40 changed 59% → 62% only.

Chance level on the hunt's real orphans (14 Nov tables, 404 drift_ok both ways, 50 shifted controls): before 709
pairs tested, 1 passing vs 0.92 expected; after 839 tested, 1 passing vs 0.98 expected.

### Scale (CPU measured here, the rest estimates)

| job | CPU | this Mac (2 workers) | 16-core PC | GCP spot n2-highcpu-64, us-east4 |
|---|---|---|---|---|
| hunt, 915 session × detector tasks, 3,519 photos | ~3 core-h | ~1.6 h CPU; ~17 h if it reads the photos itself (197 GB at ~3 MB/s) | ~12 min CPU (+ the read: ~17 h at 3 MB/s, ~1 h at 50 MB/s); 16 × 1.2 GB RAM | ~7 min CPU (~45 workers fit its 64 GB); reading 197 GB from us-east-1 ~35–70 min; ~€0.6–1.2, ~€0.1 fused |
| whole sky (~495k photos, ~123k tasks) | ~420 core-h | – | ~26 h CPU; 28 TB read: only inside the rerun | ~16 VM-h ≈ €10–16 of CPU on top of the rerun; the read is the rerun's own |
| stored | | | | hunt tables ~9 GB + maps ~15 GB; sky tables ~1.2 TB (≈ €12–25/month Nearline–Standard), no maps |

### Where it sits in the combined rerun

Order: photolist → refcat → **dots (v2)** → **sessionstack** → known → link (stack rows) → … → orphans (stack-only
candidates) → …. Run separately, `sessionstack` reads every photo a second time; the real plan is to fuse it into the
dots worker: group each worker's photos by session and detector (the rerun reads all six HDUs once), run `prepare` on
the photo already in memory, and stack when the session's last photo is done. That is one extraction change
(`extraction-needs`: dots/ owner); the step's code takes prepared cuts (`stack_session(cuts, task)`), so it needs no
change for that. Turn the switch on for link and orphans only after the stacks of the zone exist.

### Round 2 (16 Nov evening): orphans limits, stack rows in tracklets' recovery

**orphans, drift limit per position** (`orphans.rate_max_at`, column `rate_max` in orphans.parquet): drift_ok takes
`ORPHANS_RATE_MAX` (2.5″/h) or, where it is more, what a bound body at the window's near distance (40 AU) can drift
at that spot and time: (Earth's velocity across the line of sight + the bound orbit's speed) / 39 AU. That is
~4.6″/h at the ecliptic poles (a 40 AU body drifts ~3.7″/h there) and 2.5 on the hunt's ecliptic. distant.json
`searched.rate_arcsec_h` gives the zone's largest limit.

**orphans, drift floor that grows with the rate** (`orphans.drift_sigma`, `ORPHANS_DRIFT_FLOOR_REL` 0.2): each drift
component's error in the pair model is the fit's error, 0.005″/h and 0.2 × the measured rate in quadrature. A straight
line through a 5–17 day visit misses Earth's turning reflex (its direction turns ~1°/day) in proportion to the drift;
with the fixed floor alone 25% of the hunt's 40 AU bodies failed the drift χ².

Measured as in round 1 (table-level injection, the same bodies for both; before = main after #383):

| r (AU) | hunt (visits 1 + 2) before → after | ecliptic pole (t_nep, consecutive visits ~29 days) before → after |
|---|---|---|
| 40 | 61% → 89% | 0% → 96% |
| 70 | 77% → 98% | 66% → 99% |
| 100 | 81% → 98% | 67% → 100% |
| 150 | 79% → 98% | 78% → 100% |
| 300 | 91% → 99% | 81% → 100% |
| 600 | 98% → 100% | 95% → 99% |
| 1000 | 100% → 100% | 97% → 97% |

150 bodies per distance in the hunt, 100 at the pole. Chance level (50 shifted controls on real orphans): hunt 839
pairs tested, 1 passing, 0.98 → 1.02 expected; t_rdra (a pole zone at ecliptic latitude ~81°, 7 visits; t_nep has no
link run) 99 pairs tested, 0 passing, 0 → 0 expected. The floor was scanned at 0, 0.03, 0.05, 0.1, 0.15, 0.2 and 0.3:
the hunt's 40 AU recovery rises 61 → 63 → 73 → 84 → 86 → 89 → 91%, the hunt's control stays 0.98 up to 0.1, then
1.00, 1.02 and 1.20 expected passing pairs; 0.2 is the last step before the control moves. scripts/validate's
distant_injection uses the same per-position limit.

**tracklets, stack rows in the recovery** (switch on): link writes every stack row in no fixed group into
nonfixed_dots.parquet (obs_id "", dot_row −1, the session-mean mjd, `stack_key` and `stack_row`; the real dots get ""
and −1; `near_fixed` as for dots). tracklets recovers them like dots at the track's predicted position, but only
where the track moves less than `TRACK_RECOVER_STACK_SMEAR_ARCSEC` (7.5″, one PSF FWHM) over the session's photos:
a stack aligns the static sky, so a faster object is smeared in it (4 photos span 6.3 min: up to ~70″/h; the fast
tracks never take stack rows, so suspects' fast-track rule is unchanged). tracklet_dots.parquet then has
`stack_key` / `stack_row` too; score counts a known sighting found by a recovered stack row like any recovered dot.
Stats: `steps.tracklets.nonfixed_stack_rows`, `recovered_stack_rows`, `control.recovered_stack_rows`;
`steps.link.session_stack.nonfixed_stack_rows(_near_fixed)`. Switched off, both tables and stats are as before.

Measured on 8 real hunt sessions of visit 2 (203, 211, 225, 229, 234, 238, 242, 250: the sessions with the most
known asteroids missed next to a track of the same object), SWIR only (21 stacks of 3–4 photos, 83 photos from S3).
Stack rows as link would get them (`coadd.load_stack_rows` with the sky run's dot files: 317k rows after the glitch,
near-saturation and 3″ dedup cuts). Their fixed-group membership, which link decides with every session's stacks, is
approximated: within 4″ of a hunt fixed source (130k), of a Gaia star (185k) or repeated in ≥ 3 of the 8 sessions
(378); 1,827 stay free. Tracks: today's tracklets code on the hunt's movers. Known asteroids: the 15 Nov hunt run's
recall_units (movers + recovered real dots) before; plus a recovered stack row within 12″ after.

| known units in the 8 sessions | n | found before → after | score's control (moved 2′) |
|---|---|---|---|
| V < 16 | 37 | 35 → 36 (94.6% → 97.3%) | 0 → 0 |
| V 16–17 | 16 | 12 → 14 (75% → 88%) | 0 → 0 |
| V 17–18 | 111 | 44 → 47 (40% → 42%) | 0 → 0 |
| all V < 18 | 164 | 91 → 97 (55.5% → 59.1%) | 0 → 0 |

tracklets: 7 stack rows recovered on tracks (6 tracks), all of them known asteroids; the control (every track moved
2′) recovered 0. The 6 new units had no dot at all (5) or only a star's dot (1). Of the 57 units missed next to a
track of the same object: 6 recovered; 19 have a real dot within 3″ in that session and detector (so no stack row:
that dot exists but recovery did not take it, a real-dot gap, not a stack one); 16 are not in the stack within 6″;
10 are free stack rows a track did not reach; 4 sit within 4″ of a Gaia star (the membership proxy); 2 are flagged
glitch or near saturation. A whole-zone measurement needs the combined rerun (every session stacked, link deciding
membership).

## 8. Fused into the dots worker (agent "v2-stack-fuse", 16 Nov night)

### In plain words

With `SKYSHIFT_DOTS_V2=1` and `SKYSHIFT_SESSION_STACK=1` the extraction-v2 dots worker now builds the session
stacks itself, from the photos it already holds. Each photo is read once for both. The files are the same, byte for
byte in their tables, as dots v2 followed by the separate `sessionstack` step (measured on a real hunt session below).
With either switch off nothing changes: the worker and its order are as before.

### What runs

- **Tasks** (dots.run): with both switches the zone's photos go to the workers session by session (`session_groups`:
  a task never splits a session; at least `DOTS_TASK_PHOTOS` photos). Each task gets the session stacks of its sessions
  that are not written yet (`coadd.session_tasks`, a key's sources file missing).
- **Order inside a task** (`_stack_order`): per session, the partner pair D1+D4 of every exposure, then D2+D5, then
  D3+D6. Partners stay next to each other, so v2's partner columns are filled as before. Only two detectors' prepared
  photos are held at a time.
- **Per photo** (dots.process_photos, `coadd.Stacker`): the raw photo's detection background and dark-hole mask
  (`dots.raw_background_holes`: core `holes.dark_holes` with the HOLE_* settings, #392/#393) are computed once and
  handed to both the dots (`mask_dark_holes(shared=)`) and the stack (`coadd.prepare(shared=)`: same values, one
  computation). The photo is prepared for its stack (background, holes, glow) before its dots are measured.
- **When the last photo of a (session, detector) is in**, the stack is made and written (`coadd.write_task`, the same
  code `run_task` uses), and its prepared photos are freed. The stacks' metadata says `fused_in_dots: true`.
- **Photos whose dot file already exists** but whose stack is missing (a resumed run) are read for the stack only:
  their dot files are not touched.
- **A photo that cannot be read** drops its stack (status `failed`); a stack whose photos are not all in the task (e.g.
  `--limit`) is `incomplete`. Neither is written: the `sessionstack` step, which `all` still runs after `dots`, makes
  them later, reading those photos again. On a fused run that step reads nothing for the stacks already there and keeps
  the report's per-detector figures.
- **Outputs**: dots.run writes `session_stacks/index/<zone>.parquet` and `results/<zone>/session_stack.json`
  (`fused_in_dots`, `prepare_cpu_s_median`) itself; its summary gets `session_stacks` with the per-photo CPU added,
  and `dots_runs/<time>.json` lists every stack's stats. `--limit` picks whole sessions (`session_limit`).

### Measured: hunt session 72, D1 + D4 (4 exposures, 8 full photos from NASA's S3)

Same photos (local copies), one process each, this Mac:

| | separate (dots v2, then `sessionstack`) | fused |
|---|---|---|
| photo reads | 16 (8 + 8 again) | 8 |
| dot files, peaks files (16) | | identical tables and footers (timings aside) |
| stack files (6 tables, 2 maps) | | identical tables, metadata and maps (timings aside) |
| stacks | D1 34,138 sources at 5.36σ; D4 44,231 at 5.0σ | the same |
| CPU, whole run | 103.2 s | 102.0 s |
| CPU for the stacks | 38.0 s in the step (18.6 + 19.4, with a local re-read of each photo) | 36.7 s inside dots |
| peak memory | 1.78 GB | 1.88 GB |

- **Time per photo added to the dots worker: 4.6 s of CPU** (36.7 s for 8 photos): the shared background and holes
  0.12 s, the stack preparation (background, holes, glow) ~2.3 s per photo, the stack and the per-photo measurement
  ~9 s per (session, detector) of 4 photos. The dots themselves took 7.9 s per photo here (v2, no forced list), so on
  this Mac fused dots cost ~1.6× dots v2 alone in CPU.
- **What it saves**: the separate step's second read of every photo (56 MB; ~10.7 s per photo on a GCP worker, 35–50 s
  on this Mac; the hunt's 197 GB) and its background.
- **D4's false peaks are gone**: with core `holes.dark_holes` (#393) the D4 stack's threshold is 5.0σ, at 0.0 negative
  peaks per deg² (the old −10σ rule: the 8σ cap and 26 per deg², §7).
- **Memory**: the worker holds at most two detectors' prepared photos of one session (~50 MB each, 5 exposures: up
  to ~0.5 GB), plus the stack's own ~1.2 GB while it is made. On a spot n2-highcpu-64 (64 GB, 1 GB per vCPU) that is
  more than one worker per vCPU can have: run ~28–32 workers there, or use an n2-standard / n2-highmem machine.

### Cost in the rerun

Per photo +4.6 s of CPU on a Mac core, about +6–9 vCPU-s on a GCP n2 worker (dots v2 is ~62–76 vCPU-s per photo,
docs/extraction-v2.md §12.8): +8–14%. Whole sky (~495,000 photos): ~630 Mac-core-hours, ~800–1,200 vCPU-h,
~$8–20 of spot compute, and no extra reads. The CPU measured on more sessions (§7: 6–17 s per session × detector)
agrees.

## What is left

- **Fuse into the dots worker**: done (§8); the separate step stays for what the worker could not stack.
- **D4 dark holes**: with core `holes.dark_holes` (#393) the D4 stack of hunt session 72 reaches 5.0σ at 0 negatives per
  deg² (§8); one session only, check a crowded field.
- **Stack rows in tracklets' recovery** and the **orphans limits**: done in round 2 (above); measured on 8 hunt
  sessions in SWIR only, with fixed-group membership approximated. The combined rerun measures them on whole zones.
- **Whole-sky storage**: the per-photo rows are half the 1.2 TB; keep only moving sources' rows if that is too much.
- **Contract text** for session_stacks/ and stack_members.parquet is proposed in the PR, not in contracts/ yet.
- The study's points below still hold where not done here.
- The matched filter's depth is compared with the sky run's dots (real dot files on 5 strips; sep re-run on all 12),
  not with a matched-filter *extraction* (ePSF fits and forced photometry of its extra sources): positions and fluxes
  of the faint extra sources are not validated here.
- The strips are 0.15–0.44 deg² each, 4.26 deg² in all, at ecliptic latitude ~0 in Cancer; crowded fields (Galactic
  plane) will have more blends and higher false-alarm thresholds.
- MWIR dark holes: the mask rule above is a proposal; measure it on full photos.
- No injection test of shift-and-stack; the depth claim for tracking is √N minus a computed trial cost.
- The prototype resamples with bilinear splats onto a 2″ grid (holes between a photo's pixel centres; one strip's
  coverage edge produced a negative band): the extraction should coadd natively (shift each photo's matched-filter
  map by its sub-pixel WCS offset) or use stack.py's coverage rule.
- Glitch rates (single-exposure positive peaks) come from one strip only.
