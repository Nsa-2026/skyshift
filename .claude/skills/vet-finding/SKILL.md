---
name: vet-finding
description: Vet one SkyShift finding (mover, fast star, spectral lead, shape) like a professional astronomer, with the scripts/vet tools, and write the check record.
---
# Vet a finding like a professional astronomer

You are an observational astronomer checking one finding of SkyShift's engine on NASA SPHEREx images.
Decide what it really is, find what the engine got wrong, and record exactly what you checked and what
you could not check. A wrong "new discovery" costs more than a missed one: the default is doubt.

Round 1 (100 findings, 16 Nov 2026, checked four times over) is the baseline: of 64 mover candidates
none was a real unknown object (42 instrument artifacts, 21 fixed stars, 1 open); 11 of 16 fast stars
were real (9 with a Gaia position but no Gaia motion, 2 known brown dwarfs); 8 of 15
"brown-dwarf-like" leads were AGN or galaxies. Real movers do exist in the data: thousands of known
asteroids pass every test below, so do not treat "artifact" as the expected answer.

## 1. Tools

Run from `pipeline/` in the repo: `uv run --quiet python ../scripts/vet/vet.py <tool> ...`.
Do not hide error output. Images are written as PNG into `--out`; open each with the Read tool and look.
Take coordinates from `info` ("Position to use …"), never from the digits of an id. Write a negative
Dec as `--dec=-1.23`.

| Tool | Use it to |
|---|---|
| `info <id>` | The engine's rows, the position to use, sky context (ecliptic, Sun elongation, ecliptic direction), visit dates. Fast stars: the Gaia offset test. Always first. |
| `packet <id> --out DIR` | Movers: 3-row image (detections / detection minus other times / same spot at other times), every engine number per detection with its wavelength, a table of **every exposure of the session** with the S/N at the track, the numeric rule, **automatic warnings**, catalogue and known-object lines. |
| `session <id> --out DIR` | Movers: the images behind that session table, all detectors. |
| `stamps --zone Z --ra R --dec=D --out DIR [--det N] [--mjd M --n 6] [--mark RA,DEC]` | Any position. Without `--mjd`: one median stack per visit and last-minus-first. With `--mjd`: the single exposures nearest to that time. Repeat `--det`. |
| `catalog --zone Z --ra R --dec=D [--radius 30] [--mjd M] [--pm PMRA,PMDEC]` | Gaia DR3 (and 2MASS, CatWISE where the zone has them), AllWISE with its 2MASS match (always, from NASA's S3), our fixed sources, bright stars within 10′. It says which catalogues it could read. `--pm` (mas/yr): where the star was in 2016, 2010 and 1999, the nearest catalogue source to each spot, and a control spot. |
| `known --zone Z --ra R --dec=D --mjd M` | Known asteroids, comets, planets and moons predicted near a position (mover zones only). |
| `spectrum <id> --out DIR` | 102-channel spectrum: plot, table, band ratios with errors, channels that stand out, synthetic J/H/K/W1/W2 and colours. |
| `dots --zone Z --ra R --dec=D [--radius 20]` | Our detections near a position that belong to no fixed source, any time; flags a shared detector column. |
| `pixel --zone Z --det N --x X --y Y` | History of a detector pixel and its column over the whole zone: hot pixels, glows, bad columns. |
| `track <id> [--days 3]` | Movers: where the fitted track puts it in the other sessions, whether a photo holds that spot, what we have there. |
| `movers --zone Z --ra R --dec=D --mjd M` | Our movers of that session near a position. |

Which tools fit which kind:
- mover: `info`, `packet` (always), then `session`, `stamps`, `pixel`, `dots`, `track`, `known`, `movers`, `catalog`.
- high_pm (fast star): `info` (Gaia offset test, per-visit positions), `catalog` with `--pm` and `--mjd`, `spectrum`, `stamps`.
- spectral: `info`, `spectrum`, `catalog`, `stamps`.
- shape: `info` (the fitted companion against the catalogues), `stamps` with `--mark` at the companion, `catalog`.
`packet`, `session`, `track`, `movers` are for movers only and `spectrum` is for fixed sources only. A tool
that does not apply to the kind is not a missing check: do not call it and do not list it.

`NOT AVAILABLE` or `TOOL ERROR` on a tool that does apply means the check was not done: record it under
`could_not_check`, never as "nothing found". "Not measurable" is not "absent". An empty catalogue list
only counts for the catalogues the tool says it read.

## 2. Facts you need

- Pixel 6.15″, PSF FWHM 7.5″ (1.2 px). A real point source is a compact blob 2–3 px across. `size` in
  the tables is √(a·b) in arcsec: stars are about 3–4″; under ~2.2″ is a one-pixel spike; over ~5.8″
  is a blend, a diffuse glow or a streak.
- One exposure lasts 113 s. A session is 1–5 exposures 2.1 min apart. A visit is 2–4 weeks in which the
  spot is seen in about 20 sessions; visits are about six months apart. Most zones have 2–3 visits.
- Between exposures the telescope normally steps about 115 px in y, so a sky position lands on a
  different detector pixel each time and is seen at a different wavelength (the filter is fixed to the
  detector; the tools print the wavelength). **Some sessions do not step**: then a hot pixel stays on
  one sky spot and looks exactly like a source that is there now and gone at other times.
- Detectors D1 0.75–1.1, D2 1.1–1.6, D3 1.6–2.4, D4 2.4–3.8, D5 3.8–4.4, D6 4.4–5.0 µm. D1+D4, D2+D5,
  D3+D6 are exposed at the same moment: a real source is at the same place in both, if bright enough
  in both. A cosmic ray or hot pixel hits one detector only.
- Flags at a detection's peak. Masked for detection: TRANSIENT, OVERFLOW, SUR_ERROR, NONFUNC, DICHROIC,
  MISSING_DATA, HOT, COLD, PHANMISS, NONLINEAR, PERSIST, STREAK, SNOWBALL. Recorded only: CROSSTALK,
  GHOST, GHOST_FPA, GHOST_EXT, BLOOM, HALO, OUTLIER. OUTLIER is on most real movers (the pixel changed
  between exposures): never count it against a mover. PERSIST = after-image of a bright source.
  OVERFLOW/NONLINEAR in the core = saturated: positions are biased.
- Position errors from S/N are 1.4–1.8× too small; the tools scale them by 1.6.
- Saturation starts near AB 10.5 (D1), 10.8 (D2), 11.0 (D3), 11.1 (D4), 9.6 (D5), 9.0 (D6).
- Aperture S/N at a mark (radius 1.5 px = 9″): about 5 or more is a source; about 2 or less is nothing.
  Neighbours within 9″ leak into it.
- Catalogue depth: Gaia lists every ordinary star brighter than about G 20; a star with J < 15 away
  from the Galactic plane that "has no Gaia match" is a failed cross-match until shown otherwise.
  Many zones' reference files hold Gaia only; AllWISE (2010.5) is always read from NASA's S3.
- Engine numbers known to be unreliable: `cover_exposures` / `missed_exposures` (trust the session
  table instead), rates from two exposures (wrong by more than half in 1 of 5), rates of
  "two detectors at one moment" movers (millions of ″/h: meaningless).

## 3. What a careful astronomer rules out, in order

Do every check that applies. Stop early only when one check is decisive, and say which. For movers,
read the packet's AUTOMATIC WARNINGS first: each names a failure mode below. "No warnings" is not a
pass: the warnings cover the common failures only. Before a mover gets `unsure` or a `real_*` verdict,
look at `session` and `stamps`, and run the check each warning names.

**A. Is each detection a real source?**
1. Shape: compact PSF-like blob. Size under 2.2″ or PSF-fit χ² over 10, or a position on a whole
   pixel (x.0, y.0), is a spike: in round 1 an artifact every time.
2. Flags at the peak: any masked flag means the detection sits on bad pixels.
3. Hot pixels. The packet warns `HOT PIXEL` when a detection's detector pixel has detections in three or
   more exposures of the zone. Two hot pixels one telescope step apart (about 115 or 230 px in y) land on
   the same sky spot in consecutive exposures and look like a source seen twice. When all detections are
   in one detector, or two share a pixel, run `pixel` on each.
4. Paired detector at the same moment (session table): a source at S/N > 15 in D4 or D5 with nothing in
   D1 or D2 is a spike or hot pixel. D6-only is normal for faint asteroids (heat glow) and for very
   red objects.
5. The exposures between the detections: a pair from exposures 1 and 4 with nothing in 2 and 3 is two
   unrelated hits. Check this before anything else for two-exposure pairs.
6. Bright star nearby (`catalog`): halo, diffraction spike, ghost, column bleed, persistence. A diffuse
   detection in one detector only, with a star of G < 7 within about 8′, is scattered light.
7. Detector column: `dots` and `pixel` show when the detections share one detector column over
   several sessions (a glow or defect fixed to the detector).
8. Streaks: a satellite leaves a straight trail across the whole stamp in one exposure, in both paired
   detectors. "Seen in two detectors at the same moment" alone is not mover evidence.
9. Edge of the detector, masked pixels inside the source, the first 20 px of a detector edge.

**B. Is it a fixed source the engine failed to recognise?**
1. Same spot at other times (packet row 3): a source there in other sessions and in the other visit is
   a star or galaxy. The strongest single test. A pair that moved less than 1× its error with +3 to
   +8σ at the spot at other times is a faint fixed source.
2. Difference image (row 2): a real mover keeps most of its signal; a star cancels. A red/blue pair
   around a bright star is residue. A hot pixel on a fixed star keeps the difference high: when other
   times show a source, it is the source.
3. Catalogues at the date (`catalog --mjd`), including AllWISE. A fixed source of ours 6–9.5″ away, or
   two Gaia stars on opposite sides 6–10″ away (a blend measured as its own source), explains the
   "mover".
4. `dots`: repeated unassigned detections at the same place in other sessions.

**C. Is it a known moving body?**
1. `known`: nearest predicted asteroid, comet, planet or moon. Compare rate and direction, not only
   distance. When the predicted motion over the session is below the measurement errors, compare the
   position with the orbit, not the rate.
2. A giant planet within about 1° means moons and glare.
3. Not in any list we have: artificial satellites, very recent discoveries, comets missing from the
   orbit file, asteroids fainter than V 21. Say so.

**D. Is the motion real and physically plausible?**
1. Significance: under about 3 it could be noise; a slow real asteroid is also under 3 in a few
   minutes, so this alone neither proves nor kills.
2. Direction against the ecliptic (`info`): main-belt objects near elongation 90° move mostly prograde
   along the ecliptic at 10–40″/h. Retrograde, or straight across the ecliptic, or over 100″/h without
   a trail (rate × 113 s), is a red flag.
3. Straight line, steady flux, seen in every exposure that holds the track.
4. A second session (`track`): two detections minutes apart are a tracklet at most. If a photo holds
   the predicted spot within a small circle, look there with `stamps`. If the rate is too uncertain to
   follow, say so under `could_not_check`.
5. A single-session source at 8σ per exposure that is only 2–3σ in the visit stack is transient
   (mover, variable, or ghost): it needs the other tests.

**E. Does the brightness make sense?**
1. Flux steady within a detector (wavelength changes a little: tens of percent, not factors).
2. Colour between paired detectors: asteroids reflect sunlight in D1–D3 and glow in D5–D6. A source
   seen in D4 only with weak D1 is too red for reflected sunlight.
3. For a known body: compare with the predicted V magnitude, roughly.

**F. Fast stars (high_pm): the claim is a shift far below a pixel.** Images cannot confirm it; epochs can.
0. A fast star looks fixed in our images: 2″/yr is a third of a pixel per year. Every fast star also
   has a row in our fixed-source list, seen in many sessions; its position error is the error of the
   mean, not a limit on motion. The fixed-source row, same-spot stamps and a quiet difference image
   never count against a proper-motion claim. Only positions at different epochs decide: the per-visit
   positions in `info`, and the older catalogues.
1. `info` prints the Gaia offset test: a real fast star sits (motion × years) from its Gaia 2016.0
   position, along the motion. Size ratio near 1 and direction difference near 0 confirm it. A Gaia
   star at our position with a small proper motion refutes it. Gaia rows with **no proper motion**
   (position-only solutions) are exactly where the engine's match fails and our motion is new
   information.
2. `catalog --pm PMRA,PMDEC --mjd M`: counterparts at the back-tracked 2016, 2010 (AllWISE) and 1999
   spots, none at the control spot. Two epochs agreeing is strong confirmation. An object too red for
   Gaia (W1−W2 above 1, or no light below 1 µm) has no Gaia epoch: then one AllWISE counterpart within
   1″ of the back-tracked spot, with the brightness the spectrum predicts and a printed chance under 1%,
   confirms the motion. `COMMON PROPER MOTION` (a Gaia star nearby with the same motion) confirms it too
   and makes the finding that star's companion, which is then almost certainly catalogued.
3. Pair-method candidates (two source ids): the engine matched two different sources, one per visit.
   `stamps --mark` both ends: both present in every visit means two fixed sources, not motion. Any
   claim over 10″/yr is this until proven otherwise.
4. With only two visits half a year apart, parallax leaks into the motion (nearby stars: up to a
   factor 2, along the ecliptic). Three visits are much safer.
5. Known or new? A real fast star brighter than about J 15 is almost always in older proper-motion
   catalogues. We have no name lookup: say "motion confirmed; novelty not checked" unless a tool
   settles it. Names from your own memory must be marked as memory.

**G. Spectral leads.** Use `spectrum`'s band ratios, then `catalog` (AllWISE colours), then `stamps`.
1. A brown dwarf needs the water bands (1.4 and 1.9 µm ratios below about 0.8) AND a faint blue end.
   A late-M or L dwarf has water bands and CO but no methane dip; a T dwarf adds a 3.3 µm trough with
   continuum on both sides and a 4.1 µm peak.
2. A dip near 3.3 µm on a smooth red continuum with no water bands is a galaxy or AGN: the "methane"
   is the low point between starlight and warm dust. W1−W2 > 0.8 with a smooth spectrum is an AGN;
   extended flag > 0 is a galaxy.
3. Channels that stand out alone are emission lines only if they rest on 2 or more detections; check
   Paα 1.875, Brγ 2.166, PAH 3.29, Brα 4.052 µm, possibly redshifted.
4. Steps exactly at 1.1, 1.6, 2.4, 3.8, 4.4 µm are calibration. A per-visit brightness swing on a
   sharply peaked spectrum comes from which wavelengths each visit sampled.
5. Blending: two sources in one 6″ pixel make a composite spectrum.

**H. Shape (elongated, possible pair).** `info` prints the fitted second object and every catalogue
source within 4″ of it, AllWISE included: a catalogue source there means the companion is real and
already listed. Compare separations between catalogue rows, never a row's offset from the star with
the companion's separation. In `stamps --mark` at the companion, the ring values tell a companion from
the star's halo: a companion stands above the ring median by 5 or more in every visit; a control mark
must be at least 2 px (12″) from the centre and from any catalogue source. Check both visits and several
detectors (`stamps`) and the catalogues for a second source. A companion closer than about 1 px cannot be resolved by eye. The sky
rolls by about 180° between visits, so a steady position angle does not prove a real pair. A
companion bright enough to matter at 5–12″ should be in Gaia or AllWISE; if it is not, suspect the
PSF model or a bright star's halo along that direction.

## 4. Verdicts

Choose the verdict by the engine's claim, not by how interesting the object is:

| Kind | The claim holds | The claim is false |
|---|---|---|
| mover | `real_mover_known` (matches a known body) or `real_mover_unknown` (passes every test, no known body: a tracklet to follow up, not yet a discovery) | `static_source` (a fixed star or galaxy), `artifact` (instrument effect: say which), `no_source` |
| high_pm | `real_known` when a catalogue we read lists that motion (a Gaia or CatWISE proper motion, or a primary with the same motion); `real_other` when the motion is confirmed and no catalogue we read lists it ("motion new; novelty not checked") | `static_source` when it does not move (pair method: two fixed sources); `engine_claim_wrong` when it moves but the value is wrong by more than about half |
| spectral, shape | `real_known` when any catalogue we read (Gaia, 2MASS, AllWISE, CatWISE) lists the object (for a shape: the second object); otherwise `real_other` | `engine_claim_wrong`, whatever the object is instead (an AGN called a brown dwarf; no second object). Never `static_source`. |

`real_mover_*` are for movers only. A claim that is half right ("catalogues list one object" when AllWISE
lists two) takes the "holds" verdict, with the wrong half under `engine_problems`. `unsure` when the
deciding check could not be run. Say what the object is in `what_it_is`. Give a confidence 0–1 and
`interest`: `high` only when it survives every check you could run and is worth a human's time.

## 5. The check record

One JSON object per finding, exactly these keys:

```json
{
 "id": "...", "kind": "mover|high_pm|spectral|shape",
 "verdict": "...", "confidence": 0.0, "interest": "none|low|medium|high",
 "what_it_is": "one sentence a non-specialist can read",
 "checks": [
   {"check": "B1 same spot at other times", "tool": "packet", "saw": "what the tool showed, with numbers",
    "conclusion": "supports|against|neutral: what it means for the claim"}
 ],
 "could_not_check": [{"what": "...", "why": "tool missing | data missing | tool error", "would_need": "..."}],
 "engine_problems": ["what the engine did wrong or which of its numbers is wrong, with the right value if you know it"],
 "decisive": "the one check that settled it",
 "tools_used": ["info", "packet"]
}
```

Rules: `saw` must come from a tool you ran, with numbers; never fill it from memory or expectation.
Anything you know from memory (an object's name, a typical colour) goes in `what_it_is` marked
"from memory". List under `could_not_check` every check from section 3 that applied but could not be
run. If the engine's row disagrees with what you measured or saw, that goes in `engine_problems`.

## 6. Improving this skill and the tools

After a batch, write a feedback file with: checks a scientist would do that the tools cannot do yet;
tool output that was missing, confusing or wrong (quote it); checklist steps that were unclear,
useless or missing; rules of thumb you ended up using that should be added here. Be specific and give
the example ids.
