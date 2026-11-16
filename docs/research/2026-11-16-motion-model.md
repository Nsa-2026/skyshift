# Motion model, steps 2–3: proper motion from same-season pairs, parallax only with a Gaia prior

Proposal, 16 Nov 2026. Step 1 (server and web, no pipeline rerun) is in contracts/api.md v3.21: every months
shift is checked against its own visits and a wobble is shown as a wobble (`steady`, `wobble_arcsec`). Steps 2–3
fix the cause in the pipeline. Nothing here has been run.

## The problem in numbers

- Proper motion is a straight line at steady speed. Parallax adds an out-and-back every six months: at most
  0.55″ (Barnard's Star), under 0.01″ for an ordinary star.
- One visit's position is good to about 0.4–0.8″ for a typical source (6.15″ pixels). Raw `pos_err_arcsec` is
  1.4–1.8× too small against Gaia; the pm errors of the core are honest (pull 0.97) but have a fake tail of high
  χ²_ν (median 3.1 against 0.94).
- On the five featured fields about 1 in 7 sources seen in ≥ 3 visits of one detector goes more than 1″ out and
  comes back (Barnard 35 of 204, RW Aur 31 of 222, Eps Indi B 25 of 107): blends whose brightest star changes
  with wavelength, the position on the detector (the 180° roll between visits), saturation.
- Today `astrometry` fits position + pm (+ a free parallax from 3 visits, config `ASTRO_PARALLAX_MIN_VISITS`) to
  every member dot. With visits six months apart a free parallax is almost degenerate with an out-and-back
  wobble: it absorbs it, `chi2_nu` stays low, and the left-over pm can be a fake (highpm.py already corrects for
  the "parallax leak" k_e, k_n of two-visit fits). Its `chi2_nu` is per dot, so it is dominated by the scatter
  inside a visit, not by the visit-to-visit wobble that matters.

## Step 2: proper motion from same-season visit pairs

SPHEREx sees a spot at the same solar elongation every six months, so visits k and k + 2 (about a year apart) see
it from nearly the same place in the Earth's orbit: parallax cancels (left: ϖ × the change in the parallax factor
over the few days' difference in date, ~0.1–0.2 ϖ for 5–10 days), and so does any offset fixed to the roll angle
(the spacecraft is oriented the same way, so a blend or pixel-map offset lands on the same side).

Per source × detector:
1. Visit means: the 1/σ²-weighted mean of the member dots of each visit (σ = calib's `pos_err_rel_arcsec`), its
   error from the dots' scatter, never below the formal error × 1.6 (session-clustered, as the current sandwich).
2. Same-season pairs: (k, k + 2), (k + 1, k + 3), … in the same detector. pm_pair = (pos_{k+2} − pos_k) / Δt, with
   the residual parallax ϖ (P(t_{k+2}) − P(t_k)) removed when a Gaia parallax exists (step 3), otherwise kept in
   the error budget as 0.2 × (an upper bound on ϖ: 10 mas for stars without Gaia, see below).
3. pm = the weighted mean of the pairs over detectors (and over pairs when a source has 4+ visits); its error
   from the pairs' errors, × √χ²_ν of the pairs about their mean when > 1 (detectors that disagree).
4. Visit-to-visit check: χ²_ν and RMS (`wobble`) of all visit means about the line (pm, one zero point per
   detector, parallax from step 3), as step 1 does on the server. A source whose opposite-season visits leave
   the line is a wobble, whatever its pair pm says.

Sources with only two visits (six months apart) get no pair: pm is left empty (NaN) rather than mixed with
parallax and roll offsets; `highpm` then needs a second season, which DR1 + QR3 have for most of the sky.

## Step 3: parallax only with a Gaia prior

- Gaia match (crossids `gaia_id`, refcat `parallax`): fit ϖ with a Gaussian prior N(ϖ_Gaia, σ_Gaia). SPHEREx
  cannot measure ϖ better than Gaia; the prior keeps the parallax term from soaking up a wobble, and the
  posterior ϖ is a check (a value pulled far from the prior flags a mismatch or a blend).
- No Gaia match: ϖ = 0 in the fit, and the out-and-back test is reported separately as `plx_like_mas`: the
  amplitude of an out-and-back along the parallax ellipse (projection of the opposite-season residuals onto the
  parallax factors), with its error. Planet X (or a nearby brown dwarf with no Gaia entry) shows up there, as a
  consistent out-and-back in every detector along the parallax direction; a blend wobble does not follow the
  ellipse and differs between detectors. `highpm` can rank on it as a second signal.

## Pipeline changes (another agent writes the code)

- `pipeline/src/skyshift_pipeline/astrometry.py`: `fit()` gains a visit-mean stage and the pair estimator
  above (config `ASTRO_MOTION_MODEL = "pairs"`, the current dot fit stays behind `"dots"` for comparison); the
  parallax prior from crossids/refcat (`ASTRO_PARALLAX_PRIOR = "gaia"`, free parallax off). Frame, saturated and
  pixel-map offsets stay as they are (applied to the dots before the visit means).
- `highpm.py`: `bad_fit` from the visit χ²_ν (`visit_chi2_nu > HIGHPM_FIT_MAX_CHI2_NU`) instead of the dot χ²_ν;
  the parallax-leak correction (k_e, k_n) goes away for pair fits (parallax cancels by construction);
  `short_span` = no same-season pair.
- `astrometry_report.json`: pm vs Gaia for both models (bias, scatter, pull, by S/N and G), the share of Gaia
  stars called a wobble (should be ≤ 5% at χ²_ν 3), and Barnard's Star / Eps Indi B as named checks.

## Contract changes

- contracts/dots.md `astrometry` (new columns; old readers ignore them): `model` (`pairs` | `dots`), `n_pairs`
  int16, `visit_chi2_nu` float32, `wobble_arcsec` float32 (RMS of the visit means about the line), `steady` bool,
  `parallax_prior` (`gaia` | `none`), `plx_like_mas`, `plx_like_err_mas` float32. `parallax`/`parallax_err` keep
  their meaning (the posterior with the Gaia prior; NaN without Gaia).
- contracts/api.md: `shifts[]` keep the v3.21 fields; the server takes `steady`, `chi2_nu` (from
  `visit_chi2_nu`) and `wobble_arcsec` from astrometry.parquet when the zone has them, and computes them from the
  response's visits (step 1) only for older zones. `measure` of `/api/object` can name the model. One minor
  version, after the pipeline PR's numbers are in.

## Rerun: what, how long, what it costs (estimates)

Only the motion steps from stored dots (`MOTION_STEPS` minus the ones that do not change): `astrometry`,
`highpm`, then `suspects` and the candidate export. No photo is read again; no `dots`, `link`, `calib`.

| zone | member dots | estimate on skyshift-test (e2-standard-8, €0.30/h) | cost |
|---|---|---|---|
| hunt (100 deg², 33.4 M dots, 303 k sources) | 33.4 M | astrometry 15–30 min (two fits, frame-corrected and raw, with 3 pixel-map iterations; the visit means make the solve smaller, not larger), highpm + suspects 5–10 min | about €0.10–0.20 |
| one 10° × 10° ecliptic zone | similar to hunt | 20–40 min | €0.10–0.20 |
| a featured target zone (t_barnard …) | ~1 M | 1–3 min | < €0.02 |

Reads are bucket-to-VM in us-east4 (no egress charge); the outputs are a few tens of MB per zone. The whole
combined sky run is (number of combined zones) × 20–40 min on one e2-standard-8, or about an hour on the
n2-highcpu-64 spot worker running zones in parallel (memory, not CPU, limits how many at once: measure the hunt
zone's peak RSS first, the step logs it). Then `infra/run/deploy.sh` (measured `VERSION` bump) and
`precompute.py --measured-only` for the featured fields (reads their dots from the bucket).

Checks before switching the default: on the hunt zone, the pull of SPHEREx − Gaia pm for Gaia stars (expect
~1.0, today 0.97 in the core), the share of `steady: false` among Gaia stars with a Gaia pm < 20 mas/yr
(expect ≤ 5%), Barnard's Star and Eps Indi B within 3σ of Gaia, and the number of vetted high_pm candidates
before and after (most of today's are fakes).
