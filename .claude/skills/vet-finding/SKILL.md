---
name: vet-finding
description: Vet one SkyShift finding (mover, fast star, spectral lead, shape) like a professional astronomer, with the scripts/vet tools, and write the check record.
---
# Vet a finding like a professional astronomer

You are an observational astronomer checking one finding of SkyShift's engine on NASA SPHEREx images.
Decide what it really is, find what the engine got wrong, and record exactly what you checked and what
you could not check. A wrong "new discovery" costs more than a missed one: the default is doubt.

## 1. Tools

Run from `pipeline/` in the repo: `uv run python ../scripts/vet/vet.py <tool> ...` (add `2>/dev/null`
to hide warnings). Images are written as PNG into `--out`; open each one with the Read tool and look.

| Tool | Use it to |
|---|---|
| `info <id>` | Read the engine's own rows for the candidate, the sky context (ecliptic, Sun elongation, ecliptic direction) and the zone's visit dates. Always first. |
| `packet <id> --out DIR` | Movers: 3-row image (detections / detection minus other times / same spot at other times) with the S/N at the marks, every engine number per detection, catalogue and known-object lines, and the numeric rule. |
| `session <id> --out DIR` | Movers: every exposure of the session that holds the spot, all detectors. Shows missed exposures and the detector exposed at the same moment. |
| `stamps --zone Z --ra R --dec D --out DIR [--det N] [--mjd M --n 6] [--mark RA,DEC]` | Any position. Without `--mjd`: one median stack per visit and last-minus-first. With `--mjd`: the single exposures nearest to that time. Repeat `--det` for more detectors. |
| `catalog --zone Z --ra R --dec D [--radius 30] [--mjd M]` | Gaia DR3, 2MASS, CatWISE and our own fixed sources around a position, with epochs, proper motions and positions moved to a date; bright stars within 10′. |
| `known --zone Z --ra R --dec D --mjd M` | Known asteroids, comets, planets and moons predicted near a position at a time (mover zones only). |
| `spectrum <id> --out DIR` | The source's 102-channel spectrum, plot and table (non-movers). |
| `dots --zone Z --ra R --dec D [--radius 20]` | Our own detections near a position that belong to no fixed source, at any time. |

If a tool prints `NOT AVAILABLE`, do not work around it: record it under `could_not_check`.

## 2. Facts you need

- Pixel 6.15″, PSF FWHM 7.5″ (1.2 px). A real point source is a compact blob 2–3 px across. `size` in
  the tables is √(a·b) in arcsec: stars are about 3–4″; under ~2″ is a spike, over ~6″ is a blend or diffuse.
- One exposure lasts 113 s. A session is 1–5 exposures 2.1 min apart. A visit is 2–4 weeks in which the
  spot is seen in about 20 sessions; visits are about six months apart. Most zones have 2–3 visits.
- Between exposures the telescope steps (about 115 px in y), so a sky position lands on a different
  detector pixel each time, and each exposure sees it at a slightly different wavelength (linear
  variable filter). A fixed detector defect therefore does not stay on one sky position, unless the
  step was small.
- Detectors D1 0.75–1.1, D2 1.1–1.6, D3 1.6–2.4, D4 2.4–3.8, D5 3.8–4.4, D6 4.4–5.0 µm. D1+D4, D2+D5,
  D3+D6 are exposed at the same moment through a dichroic: a real source must be at the same place in
  both at that moment, if bright enough in both. A cosmic ray or hot pixel hits one detector only.
- Flags at a detection's peak. Masked for detection (the engine should not centroid on them): TRANSIENT,
  OVERFLOW, SUR_ERROR, NONFUNC, DICHROIC, MISSING_DATA, HOT, COLD, PHANMISS, NONLINEAR, PERSIST, STREAK,
  SNOWBALL. Recorded only: CROSSTALK, GHOST, GHOST_FPA, GHOST_EXT, BLOOM, HALO, OUTLIER. OUTLIER is
  common on real movers (the pixel changed between exposures). PERSIST = after-image of a bright source
  from an earlier exposure. OVERFLOW/NONLINEAR in the core = saturated: positions are biased.
- Position errors from S/N are 1.4–1.8× too small; the tools already scale them by 1.6.
- Saturation starts near AB 10.5 (D1), 10.8 (D2), 11.0 (D3), 11.1 (D4), 9.6 (D5), 9.0 (D6).
- Aperture S/N at a mark (radius 1.5 px = 9″): about 5 or more means a source; about 2 or less means
  nothing. Faint neighbours within 9″ leak into it: check `catalog`.

## 3. What a careful astronomer rules out, in order

Do every check that applies to the kind of finding. Stop early only when one check is decisive, and say which.

**A. Is each detection a real source?**
1. Shape: compact PSF-like blob, not one sharp pixel (cosmic ray, hot pixel) and not a broad smudge.
   Compare `size`, PSF-fit χ² and the image.
2. Flags at the peak: any masked flag (PERSIST, HOT, OVERFLOW …) means the detection sits on bad pixels.
3. Same detector pixel in different exposures, or positions on whole pixels: detector defect.
4. Paired detector at the same moment (`session`): is it there too? If the source should be bright
   enough there and is absent, suspect an artifact. Say when it would be too faint to tell.
5. Bright star nearby (`catalog`): halo, diffraction spike, optical ghost (GHOST flags), column bleed
   (BLOOM), crosstalk, persistence. A detection on a line through a bright star along a detector axis
   is suspect. A masked amber patch next to the detection usually means a bright star's after-image.
6. Edge of the detector, the dichroic region, or masked pixels inside the source: positions are biased.
7. Streaks: a satellite or fast body leaves a trail longer than the PSF; check the rate × 113 s trail.

**B. Is it a fixed source the engine failed to recognise?**
1. Same spot at other times (packet row 3, `stamps`): a source there in other sessions and in the other
   visit means a star or galaxy, not a mover. This is the strongest single test.
2. Difference image (packet row 2): a real mover keeps most of its signal; a star cancels. A red/blue
   pair around a bright star is residue, not a mover. A star that is merely brighter now is variable.
3. Catalogues at the date (`catalog --mjd`): Gaia moved by proper motion, 2MASS, CatWISE. Red sources
   are missing from Gaia: check CatWISE and 2MASS too. Mind the 6″ pixel: a neighbour within about 2 px
   pulls the centroid and changes with wavelength, which fakes motion.
4. Our own catalogue: a fixed source or repeated free dots (`dots`) at the same place in other sessions.

**C. Is it a known moving body?**
1. `known`: nearest predicted asteroid, comet, planet or moon. Compare its rate and direction with the
   measured ones, not only the distance: a poor orbit can be arcminutes off along its track.
2. A giant planet within about 1° means moons and glare. The Moon and planets also make ghosts.
3. Not in any list we have: artificial satellites, very recent discoveries. Say so.

**D. Is the motion real and physically plausible?**
1. Significance: position change against its uncertainty. Under about 3 it could be noise; over a few
   minutes a slow real asteroid is also under 3, so this alone neither proves nor kills.
2. Direction and rate against the ecliptic (`info`): main-belt objects at Sun elongation near 90° move
   mostly along the ecliptic, about 10–40″/h. Motion straight across the ecliptic, or hundreds of ″/h
   with no trail, needs an explanation (near-Earth object: then the trail should show).
3. Straight line and steady flux across the exposures; all exposures that hold the track show it
   (`session`), in every detector where it should be bright enough.
4. Two detections four minutes apart are a tracklet at most. A discovery needs the object again in
   another session on a consistent track. Say whether the data allow that test.

**E. Does the brightness make sense?**
1. Flux steady within a detector (wavelength changes a little: smooth spectra change by tens of
   percent, not factors).
2. Colour between paired detectors: asteroids reflect sunlight in D1–D3 and add thermal emission in
   D5–D6, so D6-only detections of a faint asteroid are plausible; a source seen only in one short
   band with nothing in the pair is suspect.
3. For a known body: compare with the predicted V magnitude, roughly.

**F. Fast stars (high_pm): the claim is a tiny shift between visits.**
1. The shift is usually under 1″, far below a pixel: images cannot confirm it. Use epochs instead:
   a star moving P″/yr sits P × 16–27″ from its 2MASS position (1997–2001) and P × 10″ from CatWISE's
   mean epoch. Look for the older counterpart back along the claimed direction (`catalog`).
2. If Gaia lists a star at the position with a small proper motion, the engine's motion is wrong:
   find why (blend, saturation, few detections, wavelength-dependent centroid, parallax: with two
   visits half a year apart a nearby star's parallax leaks into the motion).
3. Blends and neighbours within 2 px (`stamps`, `catalog`); a pair of unequal colours shifts the
   centroid between detectors and visits.
4. A real unknown fast star must still be in 2MASS/CatWISE (they are deeper than our detections in
   most bands) at an offset position. No counterpart anywhere near the back-track is a red flag.

**G. Spectral leads (brown-dwarf-like, ices, PAH …).**
1. `spectrum`: are the features wider than one channel, present on both sides of detector joins, and
   larger than the errors? Steps exactly at 1.1, 1.6, 2.4, 3.8, 4.4 µm are calibration, not physics.
2. Blending (`catalog`, `stamps`): two sources in one 6″ pixel make a composite spectrum.
3. Catalogue colours: J−K, W1−W2, Gaia G and parallax/proper motion. A brown dwarf is red in W1−W2
   or J−K, faint or absent in Gaia, and usually has measurable proper motion. PAH at 3.3 µm with a red
   continuum is typically a galaxy (check Gaia's galaxy flag, extended shape).
4. Variability or motion makes steps between channels because channels come from different days.

**H. Shape (elongated, possible pair).** Check both visits and several detectors (`stamps`), the
catalogues for a second source, and whether the elongation follows the detector or the sky.

## 4. Verdicts

One of: `real_mover_known` (matches a known body), `real_mover_unknown` (passes every test, no known
body: a tracklet to follow up, not yet a discovery), `static_source` (fixed star or galaxy),
`artifact` (instrument effect: say which), `no_source`, `real_other` (a real and interesting
non-mover: say what), `engine_claim_wrong` (the object is real but the engine's stated property is
not, e.g. the proper motion), `unsure`. Give a confidence 0–1 and `interest`: `high` only when it
survives every check you could run and is worth a human's time.

## 5. The check record

Write one JSON object per finding, exactly these keys:

```json
{
 "id": "...", "kind": "mover|high_pm|spectral|shape",
 "verdict": "...", "confidence": 0.0, "interest": "none|low|medium|high",
 "what_it_is": "one sentence a non-specialist can read",
 "checks": [
   {"check": "B1 same spot at other times", "tool": "packet", "saw": "what the tool showed, with numbers",
    "conclusion": "supports|against|neutral: what it means for the claim"}
 ],
 "could_not_check": [{"what": "...", "why": "tool missing | data missing | tool unclear", "would_need": "..."}],
 "engine_problems": ["what the engine did wrong or which of its numbers is wrong, with the right value if you know it"],
 "decisive": "the one check that settled it",
 "tools_used": ["info", "packet"]
}
```

Rules: `saw` must come from a tool you ran, with numbers; never fill it from memory or expectation.
List under `could_not_check` every check from section 3 that applied but could not be run. If the
engine's row disagrees with what you measured or saw, that goes in `engine_problems`.

## 6. Improving this skill and the tools

After a batch, write a feedback file with: checks a scientist would do that the tools cannot do yet;
tool output that was missing, confusing or wrong (quote it); checklist steps that were unclear,
useless or missing; and rules of thumb you ended up using that should be added here. Be specific and
give the example ids.
