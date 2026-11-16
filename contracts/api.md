# contracts/api.md: endpoints, static twins, visits, Firestore (v3.24)

Owner: P1. Implemented by server/ and scripts/precompute/ (P2). Read by web/src/api (P3) and
web/src/blink, web/src/vote (P4). Version 3, 14 Nov 2026 (changes from v2 at the end; v3: P1's answers to the P2 engine PR:
`dt_yr` from median times, per-epoch `center`, the static index and per-set files, live caps,
targets `dets`, `spectrum_reason`; v3.1: `/data/candidates.json` for the community review pages;
v3.2: `measure_reason` and `measure_hint`, why `/api/object` gives no shift; v3.3: the measured layer;
v3.4: suspects.json track fields and grade A; v3.5: high_pm lists vetted rows only;
v3.6: `/data/distant.json`, the distant-planet search; v3.7: kind shape in candidates.json;
v3.8: the science workspace endpoints and `/science-data/<slug>/`, supplemental;
v3.9: `measure.source`, which detector, visits and exposures the shift was measured in;
v3.10: stricter spectral and shape candidate rules, `steady_pa` = both detector arrays agree;
v3.11: bright-star fragments and blends: candidates' `blend` reason, distant.json `dropped.blend`, the measured
layer's `bright_fragment` and `blend` reasons with `parent_gaia_id` and `blend_gaia_ids`;
v3.12: shape candidates' two-source fit: the measured layer's `split_by_fit` companion, `why` names it;
v3.13: measured class `stack`, sources seen only on the visit stacks;
v3.14: optional API keys, `POST /api/keys`, `GET`/`DELETE /api/keys/me`, error codes `unauthorized` and `rate_limited`;
v3.15: validation.json V6 and V7 are run (15 Nov): units `slope` and `objects`;
v3.16: votes can be changed or taken back: answer `withdrawn`, each voter's latest vote counts;
v3.17: planets and their large moons (JPL Horizons) count as known bodies for movers;
v3.18: a mover near a planet is known whatever it is (moon, glare): the suspects' veto `planets`, no such mover in
candidates.json, measured class `known` named `Saturn` or `a moon of Saturn`;
v3.19: optional `mjd` on `/api/frames` for the hours clock: the visit and the whole session of a find;
v3.20: optional `/data/cand/<id>/fill.json`, exposures that only fill the plate's empty edge;
v3.21: months `shifts[]` say whether the motion is steady: `visits`, `chi2_nu`, `wobble_arcsec`, `steady`;
v3.22: high_pm reasons `parallax_fit` and `unsteady`, from the pipeline's motion model;
v3.23: candidates' `status`, `known_name` and `checks` (what can't be ruled out, and what to look at); `/api/frames` hours `page`;
v3.24: the 16 Nov fix round: checks set the score (× 0.3 strong, × 0.95 warn) in place of the old mover factors, every
check code's levels and `values`, check producers' side files, the certain-known rule and every MPC comet, the science
workspace's analysis grid; suspects removed only when certain, the rest kept with `warn_codes` and `warn_values`; grade C
movers in `/data/candidates-more/<zone>.json`, listed in candidates.json `more`).

Static first: the site reads `/data/<slug>/` before it calls `/api/`. The static files have the
same shapes as the API, so if front and back don't meet, the static format is the contract.
The API lives at the site's own `/api` (Firebase Hosting rewrites `/api/**` to the Cloud Run
service `skyshift-api` in us-east4), so the web app uses relative URLs.

Examples of every shape: `fixtures/api/` (synthetic, see fixtures/CLAUDE.md).
Lines marked **proposal** are not in the dev plan yet; P1 confirms or changes them.

## Summary

```
GET /api/frames?ra=&dec=&fov=&clock=months|hours&det=1..6&mjd=   # mjd: v3.19, optional, hours only
→ { clock, center:{ra,dec}, pixel_arcsec, size:[nx,ny],
    epochs:[{ id, kind:"visit"|"exposure", mjd_mid, mjd_start, mjd_end, det,
              um_min, um_max, n_exposures, flag_frac, sat_frac,
              data_url, scale, zero, center? }],      # center: moving targets only
    gaia:[{ id, ra, dec, pmra, pmdec, parallax, g }],
    known:[{ name, epoch_id, ra, dec, v, source:"mpc"|"most"|"irsa-3i" }],
    data_release, archive_checked,
    measured?:{ zone, stage, clock, counts, detections[], tracks[], shifts[], … }|null,
    measured_reason? }                                # v3.3: what our pipeline measured
GET /api/object?ra=&dec=
→ { name|null, gaia|null, known_ucd|null,
    spectrum:[{ um, flux_ujy, err_ujy, det, mjd, visit, saturated }],
    spectrum_reason?,                                # why spectrum is empty, else null
    measure:{ shift_arcsec, pa_deg, dt_yr, rate_arcsec_yr, sigma_arcsec,
              n_ref_stars, predicted_arcsec|null,
              source?:{ det, visits, mjd, n_exposures, fov, pixel_arcsec, core_filled }|null },
    measure_reason?,                                 # why measure is null, else null
    measure_hint?:{ name, ra, dec, mjd } }           # moved_away only: where the star is
GET /api/gaia?ra=&dec=&r=
GET /api/known?ra=&dec=&r=&mjd=
GET /api/resolve?q=        # any coordinate format, or a built-in name

# v3.8 science workspace: supplemental, not the scored pipeline (below)
GET  /api/frames?…&view=raw|model|resid|refdiff
GET  /api/science?ra=&dec=          GET /api/analysis?ra=&dec=&det=
GET  /api/science/capabilities      GET /api/enrichment?ra=&dec=&kind=
GET  /api/export?ra=&dec=&det=&clock=&format=
POST /api/jobs?ra=&dec=&size=       GET /api/jobs/<id>      # off unless SKYSHIFT_ENABLE_SCIENCE_JOBS=1
POST /api/reference?ra=&dec=

# v3.14 optional API keys: every endpoint still works without one
POST   /api/keys         { label? }  → { key, id, label, created, header:"X-API-Key" }   # shown once
GET    /api/keys/me      X-API-Key   → { id, label, created }
DELETE /api/keys/me      X-API-Key   → { id, revoked:true }

# static twins, same shapes
/data/<slug>/frames.json   /data/<slug>/object.json
/data/<slug>/index.json    /data/<slug>/frames_<clock>_<fov>.json
/data/validation.json      /data/suspects.json
/data/candidates.json      /data/cand/<id>/frames.json   # candidates to review
/data/candidates-more/<zone>.json                        # v3.24: a zone's grade C movers ("Less likely")
/data/distant.json                                       # v3.6: the distant-planet search
/science-data/<slug>/…                                   # v3.8: science products, five featured fields

# frames are data, not pictures
# 16-bit arrays, at most 300 px a side; value = raw × scale + zero (MJy/sr)
# sampling is fixed per field; pixel_arcsec reports it:
#   months  5′  3× oversampled   2.05″/px   146 px
#   months 15′  2× oversampled   3.08″/px   293 px
#   hours  20′  native pixel     6.15″/px   195 px
#   hours  60′  binned 2×        12.3″/px   293 px
# the browser does stretch, invert, subtract and add
```

## Conventions

- Positions: ICRS degrees. Field size `fov` and search radius `r`: arcmin. Shifts and errors: arcsec.
  Wavelength: µm. Time: MJD (UTC). Flux: µJy. Frame values: MJy/sr.
  Gaia fields keep Gaia's units: `pmra` (already × cos dec), `pmdec` in mas/yr, `parallax` in mas.
- Field names are snake_case. A missing value is `null`, never omitted.
- Gaia ids are strings (JavaScript loses precision above 2^53).
- Errors: HTTP 4xx/5xx with `{ "error": { "code": "bad_request" | "not_found" | "upstream" | "internal" | "unauthorized" | "rate_limited", "message": "..." } }`.
  `unauthorized` (401) and `rate_limited` (429, with `Retry-After` in seconds) come only from API keys (v3.14).
- Examples in fixtures/api/ carry `"synthetic": true` and a `note`. Real responses leave both out.

## Time, detector, visits and sessions

```
# time of an exposure: MJD-AVG (UTC midpoint)
# from search results: (t_min + t_max) / 2
sort the exposure times at one position
new visit where the gap exceeds 60 days
cut any visit longer than 75 days into n = ceil(days / 30) equal parts
  # happens near the ecliptic poles
session = run of exposures with gaps under 10 minutes

# why not fixed bins: at RA 125.5 the visit runs to
# 6 May 2026; a 1 May cut-off would split it
```

- MJD-AVG is confirmed (Explanatory Supplement v2.1, p. 77, and the first real file). MJD-BEG and
  MJD-END give the exposure's start and end. Never use `DATE` (file creation time).
- Detector 1–6 from TAP `energy_bandpassname` ("SPHEREx-D2") or the IMAGE header `DETECTOR`.
  Never parse file names.
- Visits are numbered 1, 2, … from the earliest at that position. This replaces the v1 pass rule,
  which is dropped: fixed date bins cut real visits.
- Implemented once, in core/ (`visits(mjds)`, `sessions(mjds)`). Everyone imports it.

## GET /api/frames

Blink frames for one spot on one clock and one detector.

| param | type | default | meaning |
|---|---|---|---|
| ra, dec | float, deg | required | Centre |
| clock | `months` / `hours` | `months` | `months`: one median stack per visit. `hours`: single exposures of one visit in time order |
| fov | arcmin | 5 (months), 20 (hours) | Allowed: months 5 or 15; hours 20 or 60. Sampling as in the summary |
| det | 1–6 | 2 | Detector |
| mjd | float, MJD (UTC) | none | v3.19, `clock=hours` only. A time, e.g. a mover's find (candidates.json `mjd`): the visit that holds it, with the session nearest it whole. Without it: as before. Rules below |
| view | `raw` / `model` / `resid` / `refdiff` | `raw` | v3.8, science workspace only. Not `raw`: a derived layer (below), same response shape |
| page | int ≥ 0 | 0 | v3.23, `clock=hours`, live only. 0: as before. k ≥ 1: more exposures of the same visit and `det` (with the same `mjd`, the same visit), at most 6 a page, none already in an earlier page, spread over the visit's sessions not yet shown (whole sessions, full-field ones first). Same grid as page 0. Rules below |

**Live caps.** A cold `/api/frames` call must fit in Cloud Run's 60 s, so the live API uses fewer
exposures than the static files. The static twins (scripts/precompute) use all of them.

| clock | live `/api/frames` | static `/data/<slug>/` |
|---|---|---|
| months | at most **6 exposures per visit** in each stack, spread evenly over the visit's exposures that cover at least 90% of the field (from the search footprints; when too few, those plus the ones that fill most of the still-empty field) | every exposure of the visit |
| hours | at most **6 exposures**: the first 2 of 3 sessions spread over the visit (its first and last session included; sessions whose exposures cover at least 90% of the field preferred, then sessions with 2 or more exposures). With `mjd` (v3.19): the session nearest `mjd` whole, the rest as before; at most 6, or that session plus 2 when it has 5 or more | every exposure of the visit, up to 48 (whole sessions from the visit's start). No `mjd` |

The hours clock shows one visit: the one with the most exposures of that detector covering at least half the
field (from the search footprints; then the most covering 90%, then the most exposures, the latest on a tie).
An exposure covering under half the field is left out when its session has better ones. Pixels with raw 0 are
no data (the field's edge in that photo), never sky. `n_exposures` always reports the exposures really in the frame: a capped months stack of
a 19-exposure visit says 6; an hours frame says 1.

**The hours clock at a time (`mjd`, v3.19).** Live answers only (the static twins have no `mjd`; the
site reads them first). With `mjd` the hours clock shows the visit that holds that time instead of the one
with the most exposures:

- Visit: the visit of `det`'s exposure nearest `mjd` (search times). When `mjd` lies inside a visit,
  that is the visit; when it lies outside every visit at this position, the nearest one is shown
  (compare `epochs[].mjd_mid` with `mjd` to tell).
- Session: that exposure's session, the session nearest `mjd`, is kept **whole**: every exposure of
  `det` in it, in time order, whatever its coverage (every photo the search returns holds the centre).
  A mover's `mjd` is the middle of the exposures it was found in, so they are in it.
- The rest: filled from the visit's other sessions by the rule above (the first 2 of sessions spread over
  the visit, full-field sessions preferred), at most 6 exposures in all, or the session plus 2 when it
  has 5 or more (sessions have 1–5). The session's exposures are asked for first. All of them start
  at once (at most 7, one wave of downloads), so a slow archive can still leave any frame out: such an
  answer is partial and not cached, as always, and asking again finds the downloaded pieces.
- `det`: the visit and the session are `det`'s own at this position. D1+D4, D2+D5 and D3+D6 are exposed
  together, so a paired detector gives the same session. `fov`: changes only which of the other sessions'
  exposures are preferred (coverage), never the session nearest `mjd`.
- `measured` is built from the photos of this response as always, so a mover found at `mjd` has its
  dots, and its track, in it.
- Cache: `mjd`, rounded to 10⁻⁶ day, is part of the request's cache key. Requests without `mjd` keep
  their key and their answer.
- Errors (400 `bad_request`): `mjd` with `clock=months` or a `view` other than `raw`; `mjd` not a finite
  number, or outside the SPHEREx era, MJD 60745 (launch, 11 Mar 2025) to 64328 (1 Jan 2035).
- The response shape is unchanged.

Response fields:

| field | meaning |
|---|---|
| clock | `months` or `hours`, as asked |
| center | `{ra, dec}` of the frame centre. The centre sits at pixel ((nx−1)/2, (ny−1)/2) |
| pixel_arcsec | Pixel size of every frame in this response (tangent plane at `center`) |
| size | `[nx, ny]`, the same for every epoch, each at most 300 |
| epochs | Sorted by `mjd_mid`. One per visit (months) or per exposure (hours) |
| epochs[].id | Unique in the response. Months `d<det>-v<visit>` (e.g. `d2-v1`); hours `d<det>-<obs_id>` |
| epochs[].kind | `visit` (months) or `exposure` (hours) |
| epochs[].mjd_mid | Visit: middle of `mjd_start` and `mjd_end`. Exposure: MJD-AVG |
| epochs[].mjd_start, mjd_end | Visit: MJD-AVG of its first and last exposure. Exposure: MJD-BEG and MJD-END |
| epochs[].det | Detector 1–6. Never blink different detectors as motion |
| epochs[].um_min, um_max | Wavelength range seen by the frame's pixels, from WCS-WAVE |
| epochs[].n_exposures | Exposures really in the stack, after the live cap (1 for an exposure) |
| epochs[].flag_frac | Share of frame pixels hit by a mask bit (contracts/flags.md), 0–1 |
| epochs[].sat_frac | Share of frame pixels with OVERFLOW or NONLINEAR, 0–1 |
| epochs[].data_url, scale, zero | The frame data, see below |
| epochs[].center | **Optional**, moving targets only (e.g. 3I/ATLAS): `{ra, dec}` of this epoch's own frame centre, when the frames follow the object. The frame's centre pixel and its tangent plane are at this position instead of the top-level `center`. Absent for fixed fields |
| gaia | Gaia DR3 stars in the frame at epoch J2016.0 (MJD 57388.5). The browser moves each one to an epoch's `mjd_mid` with `pmra`, `pmdec`. `parallax` and `g` may be null |
| known | Known objects predicted at an epoch's `mjd_mid`; `epoch_id` is that epoch's `id`. `v` is the V magnitude, or null. `source`: `mpc` (Minor Planet Center orbits propagated with kete, from SPHEREx's own position when the photo header gives it), `most` (IRSA MOST), `irsa-3i` (IRSA's 3I/ATLAS photo list). MPC comets (v3.24): the pipeline and the check producers use the MPC's cometels.json.gz plus the allcometels.json.gz comets it lacks (epoch from 2015 on: 2,215 comets on 16 Nov; cometels alone drops every comet the MPC hasn't moved to its current epoch, 306P/LINEAR among them). The live index behind `known[]` and `/api/known` has them once P2 rebuilds it (until then 973 comets, no 306P) |
| data_release | Collections searched, e.g. `"DR1+QR3"` |
| archive_checked | Date of the IRSA search, `YYYY-MM-DD` |
| more | v3.23, live hours only: true when a further `page` has exposures of this visit. Absent otherwise (read as false) |
| visit_exposures | v3.23, live hours only: exposures of `det` in the shown visit that hold the centre (all pages together). Absent otherwise (read as null) |
| measured | **Optional** (v3.3). What SkyShift's own pipeline measured in this field, below; `null` when nothing is measured here (`measured_reason` says why). Files and answers without it: not computed, read as `null` |
| measured_reason | **Optional** (v3.3). Why `measured` is null, a code below; `null` when `measured` is set |

### measured: what SkyShift measured (optional, v3.3)

`gaia[]` and `known[]` are catalogue **predictions**. `measured` is what our pipeline (pipeline/,
contracts/dots.md) **detected** in the photos of this response: the dots of each photo, the sources
it linked, the movers it paired, and the motions it fitted. Built by the server from
`gs://nasa-glazing-data/results/<zone>/` and the zone's dot files (read-only; `dots/`, or the folder its link read:
contracts/dots.md "Folders"), and by scripts/precompute for the
static twins (`--measured-only` rewrites only `measured` and `measured_reason`).

```
measured: { zone, stage, zone_box:{ra:[min,max], dec:[min,max]}, clock,
            photos, photos_missing, truncated,
            counts:{ star, known, mover, suspect, artifact, other, stack? },   # stack: v3.13, optional
            detections:[{ epoch_id, ra, dec, snr, flux_ujy, n_dots, class,
                          source_id|null, gaia_id|null, known_name|null, track_id|null,
                          grade|null, reasons:[code…],
                          parent_gaia_id?|null, blend_gaia_ids?|null }],    # v3.11, optional
            tracks:[{ id, class, suspect_id|null, known_name|null, evidence,
                      dots:[{ ra, dec, mjd, epoch_id }] }],
            shifts:[{ source_id, gaia_id|null, class, ra, dec, mjd_from, mjd_to, dt_yr,
                      dra_arcsec, ddec_arcsec, shift_arcsec, err_arcsec, pa_deg,
                      pmra, pmdec, pm_err, pm_snr,
                      visits?:[{ epoch_id, det, mjd, ra, dec, err_arcsec }],     # v3.21, optional
                      chi2_nu?|null, wobble_arcsec?|null, steady?|null }] } | null
measured_reason: "not_processed" | "processing" | "no_dots" | "unavailable" | null
```

| field | type | meaning |
|---|---|---|
| zone | string | Pipeline zone the results come from (`hunt`, `ecl13`, `t_barnard`, …). When several cover the centre: a target zone `t_<slug>` first, then a sky zone listed in `skyrun/progress.json` (final before provisional), then the others (e.g. `pilot`) |
| stage | `"provisional"` \| `"final"` \| `"extracted"` | `final`: the zone's final pass (calibration, Gaia and WISE checks). `provisional`: overnight results, checked against Gaia only; suspect lists are several times longer than the final ones. From `skyrun/progress.json`; for zones not listed there, `final` when `results/<zone>/calib.json` exists. `extracted`: the photos' dot files exist but no zone has combined them yet, so `detections` are the dots alone (on the months clock a visit's dots merged within 2″, groups of 2 or more when 3 or more photos were read), classed `star` (within 2″ of a `gaia[]` star moved to the epoch), `known` (hours: within 10″ of a `known[]` prediction of the epoch), `artifact` (the flag rules) or `other`, with `source_id` null, no `tracks` or `shifts`, and `zone`/`zone_box` the zone being processed there or null. Its cap: the 400 brightest (S/N) of every class an epoch, so the stars stay for a comparison with Gaia |
| zone_box | object, deg | The zone's box. Outside it nothing is linked: dots there are `other` |
| clock | string | `months` or `hours`, as the response |
| photos, photos_missing | int | Photos of this response whose dot file was read / had no dot file (not in the zone's photo list, or not extracted yet) |
| truncated | bool | true when `detections` was capped (at most 250 per epoch: every `known`, `mover`, `suspect`, `artifact` first, then `star` and `other` by S/N) |
| counts | {class: int} | Detections per class, all six keys (and `stack` from v3.13, where the zone has a `stack_sources` table), **before** the cap |
| detections | [object] | Below. Sorted by `epoch_id`, then `snr` descending |
| tracks | [object] | Hours clock: the pipeline's movers with at least one dot in this response. Months: `[]` |
| shifts | [object] | Months clock: fitted motions, below. Hours: `[]` |

**detections[]**: one per dot (hours) or per linked source and visit (months).

| field | type | meaning |
|---|---|---|
| epoch_id | string | The `epochs[].id` it was measured in. Hours: the exposure. Months: the visit stack, from the dots of that visit's photos **in this response** (live: the capped stack) |
| ra, dec | float, deg | Measured position, ICRS, raw (no `calib` corrections, ≤ 0.2″). Hours: the dot's (`ra_psf`, `dec_psf` where `psf_pos_ok`, else `ra`, `dec`). Months: the 1/pos_err²-weighted mean of the source's dots in that visit, so a fast star sits where it was in each visit |
| snr | float | Hours: the dot's S/N. Months: √Σ snr² of those dots |
| flux_ujy | float, µJy | Hours: the dot's `flux_ujy` (r = 2 px aperture, raw; × 1.033 for the total). Months: median of those dots' `flux_ujy` (they span the detector's wavelengths) |
| n_dots | int | Dots behind it (hours: 1) |
| class | string | `star` \| `known` \| `mover` \| `suspect` \| `artifact` \| `other` \| `stack` (v3.13), rules below |
| source_id | int or null | The pipeline's linked source (contracts/dots.md `source_id`, below 2⁵³). Months: always set, except a `split_by_fit` companion (v3.12, below) and a `stack` source (v3.13): null. Hours: the source within 4″, else null |
| gaia_id | string or null | Gaia DR3 source_id of the source's crossid match (`results/<zone>/crossids.parquet`), a string like `gaia[].id`. It is in `gaia[]` when that list goes deep enough |
| known_name | string or null | `known[].name` of the entry with the same `epoch_id` it matched (class `known`); or (v3.18) for a mover near a planet that no `known[]` entry matched, the planet's name (`Mars`, `Jupiter`, `Saturn`, `Uranus`, `Neptune`) within its disc and the ephemeris error (Mars 30″, Jupiter and Saturn 45″, Uranus 15″, Neptune 10″), else `a moon of <planet>` (e.g. `a moon of Saturn`; near Jupiter it may be its glare). Show it as given |
| track_id | string or null | `tracks[].id` of the mover it belongs to |
| grade | `"A"` \| `"B"` \| `"C"` or null | Suspects only, from the zone's suspects table when it is graded (vetting); null otherwise |
| reasons | [string] | `suspect`: the suspects table's `reasons` passed through **as they are** (the gates it fails, the codes of suspects.json, e.g. `hot_pixel`, `bad_column`, `edge`, `masked_peak`, `sharp_outlier`, `flux_ratio`, `red_photo`, `2mass`; new codes from the vetting may appear: show them as given), `[]` if none or ungraded. `artifact`: why, codes below. `mover`: `near_gaia` when a `gaia[]` star is within 6″. `blend` (v3.11), on any class but `artifact`: its linked source is tagged `blend` by the pipeline's `fragments` step (two or more Gaia DR3 stars, G < 18, within 8″: one detection, several stars); the class stays. `split_by_fit` (v3.12, months clock, class `other`): a second object merged into a shape candidate's source that the pipeline's two-source fit separated (contracts/dots.md `shape_candidates`, `companion_split`). One detection per visit epoch in which the candidate's source has one, at the fitted position (that visit's own fit when it has one, else the overall fit), `source_id`, `gaia_id` null, `flux_ujy` = the source's detection × the companion's flux ratio in the epoch's detector (the source's own detection keeps the blended flux), `snr` = the companion's S/N in that detector over the fit's photos, `n_dots` 0 (it has no dots of its own: detection gave one per photo). Draw it as its own circle. Else `[]` |
| parent_gaia_id | string or null | **Optional** (v3.11). Class `artifact` with reason `bright_fragment`: Gaia DR3 source_id (a string like `gaia_id`) of the bright star whose ring this is a piece of (`fragments` step: the brightest Gaia star with G < 12 within 60″). All fragments of one star share it: draw that star once, not each piece. Null otherwise or when no such star is listed. Older responses lack the field: read as null |
| blend_gaia_ids | [string] or null | **Optional** (v3.11). Reason `blend`: the Gaia DR3 source_ids (strings, nearest first) of the stars merged into this detection. Null otherwise. Older responses lack the field: read as null |

**class**, first rule that applies:

| class | hours clock (a dot) | months clock (a linked source in one visit) |
|---|---|---|
| suspect | In a pipeline mover listed in `results/<zone>/suspects.parquet` | A `highpm_candidates` fit, `vetted`, status `new` (moves between visits, no Gaia star) |
| known | In a mover with a `known[]` prediction of the same epoch within 30″ (the suspects rule); or (v3.18) in a mover near a planet at its time (the suspects' `planets` veto, below), even one a suspects table written before that veto lists (this rule comes before `suspect`); or the nearest dot within 10″ of a `known[]` prediction of its epoch, not within 4″ of a linked source | Not used |
| artifact | In a mover with GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK, HALO or BLOOM in its dots' 3×3 flags; or a lone dot (no source, no mover) with a mask bit at its peak (OVERFLOW/NONLINEAR aside), one of those record-only bits, or `elong` > 3; or (v3.11) within 4″ of a linked source tagged `bright_fragment` | A `highpm_candidates` row that is not `vetted`, unless the source is a Gaia star; or (v3.11) a source tagged `bright_fragment` ; or (v3.13) a stack source with `artifact_zone` (reason `stack_near_bright`) |
| mover | In any other pipeline mover (paired inside one session or by two detectors at one moment, but vetted out of the suspects, e.g. near a catalogued star) | Not used |
| star | Within 4″ of a linked source with a Gaia DR3 match | A linked source with a Gaia DR3 match |
| other | Everything else: a linked source without Gaia (galaxy, faint or red star), or a lone dot | Everything else; and (v3.12) a second object the two-source fit split off a shape candidate's source (reason `split_by_fit`, below) |
| stack | Not used | (v3.13) A source the pipeline's `stack` step found on the visit stacks (`results/<zone>/stack_sources.parquet`, contracts/dots.md) with no single-photo linked source within 3″ (`source_id` −1), not `transient` and not `artifact_zone` (those are class `artifact`, reason `stack_near_bright`): fainter than the single-photo limit, the second star of a close pair, or a star in a bright star's glow. One detection per visit epoch (the stack visit whose `mjd_visit` lies within the epoch's `mjd_start`–`mjd_end`, ± 20 days) in which its forced S/N over that visit's photos (all detectors) is ≥ 5: `ra`, `dec` its free-fit position in that visit (else the stack position), `snr` that S/N, `flux_ujy` that visit's inverse-variance mean flux (all detectors, so mixed wavelengths), `n_dots` the photos measured, `source_id` null, `gaia_id` its Gaia DR3 match within 2″ (else null), `reasons` `[]`. Drawn as "seen in the stack only": not a single-photo detection |

**artifact reasons**: `ghost`, `ghost_fpa`, `ghost_ext`, `crosstalk`, `halo`, `bloom` (that bit in the
3×3), `masked_peak` (a mask bit at the peak), `elongated` (`elong` > 3), and on the months clock the `highpm_candidates` `reason` codes (`short_span`, `few_dots`,
`bad_fit`, `over_pm_cap`, `gaia_pm_disagrees`, `catwise_pm_disagrees`, `at_control_level`, and from 14 Nov
evening `few_sessions`, `unchecked`, `gaia_slow`; v3.11 `fragment`; v3.13 `stack_near_bright`: a stack source with no catalogue entry within 8″ of a much brighter source or 20–30″ from it, where the ePSF fit leaves pieces), and (v3.11, both clocks) `bright_fragment`: a piece
of a saturated star's ring (the pipeline's `fragments` step, `results/<zone>/fragments.parquet`: BLOOM on most of its
dots and no Gaia DR3 or 2MASS entry within 3″), with `parent_gaia_id`.
Deliberately **not** artefact rules: SOURCE (bit 21, never masked: set on ~99% of pixels in crowded fields and ~50%
in the hunt zone, real asteroids sit on it too) and OUTLIER (bit 19: 52% of MPC-matched movers have it at the peak).

**tracks[]** (hours clock): one per pipeline mover (`results/<zone>/movers.parquet`).

| field | type | meaning |
|---|---|---|
| id | string | Unique in the response. The suspect's id (suspects.json `id`) for a suspect, else `<zone>-m<cell>-<minute>` with the mover's `cell` and round(mjd × 1440). Opaque |
| class | string | `suspect`, `known`, `artifact` or `mover`, as its dots' class |
| suspect_id | string or null | suspects.json / candidates.json `id` for a suspect, else null |
| known_name | string or null | `known[].name` it matched (class `known`), or (v3.18) the planet or `a moon of <planet>`, as in `detections` |
| evidence | string | `two_exposures` or `two_detectors`, as the pipeline found it |
| dots | [object] | Its dots in this response, **in time order**: `ra`, `dec` (deg), `mjd` (the photo's MJD-AVG), `epoch_id`. Draw the arrow from the first to the last. Dots in photos outside this response are not listed, so a track may have one dot (e.g. its partner detector is not in the file) |

**shifts[]** (months clock): sources in the field whose astrometric fit
(`results/<zone>/astrometry.parquet`) has `pm_snr` ≥ 3, at most 300 (highest `pm_snr` first).
Proper motion only (parallax not included: ≤ 1″ for Barnard's Star; `/api/object` `measure` has the
full measurement). v3.21: each shift is checked against its own visits in the response (computed before the
per-epoch cap of `detections`): it is a motion only when `steady` is not false.

| field | type | meaning |
|---|---|---|
| source_id, gaia_id, class | | As in `detections` (class `star`, `suspect`, `artifact` or `other`) |
| ra, dec | float, deg | The fit's position moved to `mjd_from`: where the arrow starts |
| mjd_from, mjd_to | float, MJD | The first and last visit epochs' `mjd_mid` in this response (all detectors) |
| dt_yr | float, yr | (mjd_to − mjd_from) / 365.25 |
| dra_arcsec, ddec_arcsec | float, arcsec | Shift over `dt_yr`: east (already × cos dec) and north. The arrow ends at (ra, dec) + this |
| shift_arcsec, err_arcsec | float, arcsec | Length of the shift and its 1σ error per axis (pm error × dt_yr) |
| pa_deg | float, deg | Direction, east of north, 0–360 |
| pmra, pmdec, pm_err, pm_snr | float, mas/yr | The fit (pmra × cos dec), its 1σ error per axis, and pm / error |
| visits | [object] | **v3.21.** The source's position in each visit epoch of this response where it has a detection, in time order (then by detector): `epoch_id`, `det`, `mjd` (the epoch's `mjd_mid`), `ra`, `dec` (deg, as its detection), `err_arcsec` (1σ per axis: the dots' raw pos_err model over the visit, √((1.4 × 3.2″ / snr)² + 0.25″² / n_dots) with the detection's `snr` and `n_dots`, × 1.6 because raw pos_err is 1.4–1.8× too small against Gaia). Compare positions within one detector only (colour offsets) |
| chi2_nu | float or null | **v3.21.** χ²_ν of `visits` about the reported motion: a straight line of slope (pmra, pmdec) with one position zero point per detector; detectors with one visit here are left out; a Gaia star's parallax (`gaia[]`) is taken out of the positions first. Degrees of freedom 2 × (visits − detectors). null: no detector has two visits here |
| wobble_arcsec | float or null | **v3.21.** RMS distance (″, 1/err²-weighted, corrected for the zero points) of `visits` from that line: how far the position goes back and forth between visits. null as `chi2_nu` |
| steady | bool or null | **v3.21.** true: a steady motion, `chi2_nu` ≤ 3, or the motion still ≥ 5σ with the errors × √`chi2_nu` (a fast star whose saturated core scatters, e.g. Barnard's Star). false: its position wobbles between visits by about `wobble_arcsec`, within what SPHEREx can measure: not a motion; show no arrow and do not describe a step between two visits as one. null: not tested (as `chi2_nu`); read as before |

**measured_reason**

| code | meaning |
|---|---|
| not_processed | No pipeline zone covers this position yet |
| processing | A zone covers it, but its results are not combined yet (`skyrun/progress.json`) |
| no_dots | The zone has results, but none of this response's photos has a dot file |
| unavailable | The results could not be read in time. Live answers with it are not cached: asking again helps |

Example (hours clock, abridged):

```json
"measured": {
  "zone": "hunt", "stage": "final", "zone_box": {"ra": [120.5, 130.5], "dec": [14.4, 24.4]},
  "clock": "hours", "photos": 6, "photos_missing": 0, "truncated": false,
  "counts": {"star": 210, "known": 2, "mover": 0, "suspect": 1, "artifact": 3, "other": 95},
  "detections": [
    {"epoch_id": "d2-2025W42_2A_0213_1", "ra": 123.93382, "dec": 17.41191, "snr": 22.7, "flux_ujy": 996.6,
     "n_dots": 1, "class": "suspect", "source_id": null, "gaia_id": null, "known_name": null,
     "track_id": "hunt-644470070952-87790280", "grade": "B", "reasons": []},
    {"epoch_id": "d2-2025W42_2A_0213_1", "ra": 123.95012, "dec": 17.40233, "snr": 310.2, "flux_ujy": 52310.0,
     "n_dots": 1, "class": "star", "source_id": 64447007096001, "gaia_id": "654331674772691712",
     "known_name": null, "track_id": null, "grade": null, "reasons": []}
  ],
  "tracks": [
    {"id": "hunt-644470070952-87790280", "class": "suspect", "suspect_id": "hunt-644470070952-87790280",
     "known_name": null, "evidence": "two_exposures",
     "dots": [{"ra": 123.93382, "dec": 17.41191, "mjd": 60965.46572, "epoch_id": "d2-2025W42_2A_0213_1"},
              {"ra": 123.93391, "dec": 17.41195, "mjd": 60965.46723, "epoch_id": "d2-2025W42_2A_0213_2"}]}
  ],
  "shifts": []
},
"measured_reason": null
```

### Frame data (binary layout)

Confirmed 14 Nov: server/ writes it (stack.py), web/src/api/client.ts reads it, the fixtures use it.

- `data_url` points to `nx × ny` raw **little-endian uint16** values (`2·nx·ny` bytes, no header),
  row by row. The first row is the **north** edge and the first value of a row is the **east**
  edge: north up, east left, ready to draw on a canvas.
- `value = raw × scale + zero`, in MJy/sr.
- `raw = 0` means no data (masked in every input exposure). Real values use 1–65535.
- `data_url` is resolved against the URL of the JSON that contains it:
  `new URL(epoch.data_url, jsonUrl)`. Static files use a relative path next to `frames.json`;
  the API may return an absolute path. A `data:` URL is also allowed.
- Content type `application/octet-stream`. Decode with `new Uint16Array(await res.arrayBuffer())`
  (all supported browsers are little-endian).

## GET /api/object

The object nearest a position (search radius 6″).

| param | type | required |
|---|---|---|
| ra, dec | float, deg | yes |

| field | meaning |
|---|---|
| name | Built-in name or Gaia/known name, else null |
| gaia | One entry shaped like `frames.gaia[]`, or null. The card shows Gaia's distance (1000 / parallax pc) |
| known_ucd | Known ultracool dwarf, or null. **Proposal:** `{ name, spt, ref }` |
| spectrum[] | Sorted by `um`. `flux_ujy`, `err_ujy` in µJy; `det` 1–6; `mjd` = the exposure's MJD-AVG; `visit` = visit number; `saturated` = OVERFLOW or NONLINEAR in the core. An exposure whose aperture or background annulus has no good pixels gives no point (no lower limits) |
| spectrum_reason | **Optional.** Why `spectrum` is empty: `"saturated"` (a star saturated so deeply that no exposure had a usable aperture and background annulus, e.g. R Draconis) or `"no_data"`; `null` when there are points. Files without it: read as `null` |
| measure | Motion between the first and last visit stack, or null with fewer than two visits or when no single source stands out clearly in both stacks (`measure_reason` says why). The detector is the one with the best unsaturated signal, limited to the target's `dets` when targets.json gives them (below) |
| measure.shift_arcsec, pa_deg | Size and direction of the shift; position angle east of north |
| measure.dt_yr | Time between the **median exposure times** (MJD-AVG) of the two visit stacks (`source.mjd`), years. Not `mjd_mid`: a per-pixel median of a slowly moving star sits where the star was at the median time (Barnard's Star, visit 3: `mjd_mid` is 14.6 days, 0.41″, after the median) |
| measure.rate_arcsec_yr | shift_arcsec / dt_yr |
| measure.sigma_arcsec | 1σ error of the shift (V3): the 2-D RMS of the reference stars' residual shifts, after the median frame-to-frame offset is removed. 3 × sigma is the slowest motion we can see |
| measure.n_ref_stars | Quiet Gaia stars used (G 13–17, motion under 20 mas/yr) |
| measure.predicted_arcsec | Gaia's predicted shift over `dt_yr`, including parallax when Gaia has one (it moves Barnard's Star up to ~1″ between visits), or null without Gaia |
| measure.source | **Optional** (v3.9). What the shift was measured on, below. The server picks its own detector and visit pair, whatever the player shows: changing the player's detector, clock or field doesn't change `measure`. Files and answers without it, and `null`: unknown. Readers then say less (no detector, the visits as "first and last") and never take the detector or visits from the frames on screen |
| measure.source.det | Detector 1–6 of both stacks |
| measure.source.visits | `[first, last]`: the two visit numbers compared, as in the frames' epoch ids (`d<det>-v<visit>`). Not always 1 and the last visit: a visit with fewer than 3 exposures in that detector is skipped |
| measure.source.mjd | `[first, last]`: the median MJD-AVG (UTC) of each stack's exposures, the times the fit uses. `dt_yr` = (mjd[1] − mjd[0]) / 365.25. They can differ from the epochs' `mjd_mid` (above) |
| measure.source.n_exposures | `[first, last]`: exposures in each stack. A live answer may use fewer than the visit has (the months live cap above: at most 6 per visit) |
| measure.source.fov, pixel_arcsec | The measurement's own grid: field in arcmin (15) and sampling in arcsec per pixel (6.15, native). Not the player's field |
| measure.source.core_filled | `[first, last]`: true when the star had a hole in that stack (saturated in every exposure) and a round profile fitted to its wings filled it before the centroid (Barnard's Star). Real pixels are never changed |
| measure_reason | **Optional.** Why `measure` is null: one of the codes below. When it is set, `measure` is null; with a measure it is null. Files and answers without it: read as `null` (the site then gives its general sentence) |
| measure_hint | **Optional.** `{ name, ra, dec, mjd }` with `measure_reason` `moved_away`, else null: the fast star that was catalogued here and its position (degrees) at `mjd`, the middle of SPHEREx's first and last exposures at this position, so the site can open it where it is. `name`: the built-in name or `Gaia DR3 <id>` |

| measure_reason | plain meaning |
|---|---|
| few_visits | SPHEREx has not seen this spot in two visits with at least 3 exposures each (in the detectors it can be measured in), so there is nothing to compare |
| not_detected | No source stands out clearly here (PSF-matched S/N ≥ 5) in the first or the last visit stack |
| edge | Sky is missing within 4 px of the position in the first or the last visit stack (the edge of the area covered) |
| different_sources | A source stands out in both stacks, but the two are too far apart to be one: more than max(7.53″, 12″/yr × Δt) + 3σ apart, or that far from the catalogue's predicted motion |
| no_time | A live answer ran out of time before the measurement. Such answers are not cached: asking again helps |
| moved_away | A fast star was catalogued here, but it had moved away by SPHEREx's visits: `measure_hint` says where it is. The star: a built-in target or a Gaia DR3 star with a proper motion, whose track between J1990.0 and its catalogue epoch (Gaia: 2016.0) passes within 6″ (so Gaia DR3 and DR2, SIMBAD's J2000.0, 2MASS and Hipparcos positions all count) of the position asked and stays more than 7.53″ (one PSF FWHM) from it through SPHEREx's first to last exposure there. Example: Barnard's Star's Gaia position (269.4485, 4.7394); SPHEREx sees the star 100–111″ north of it. It wins over the other codes. A measure of another source at the position asked is kept (no reason, no hint) |

## GET /api/gaia, GET /api/known

| endpoint | params | response |
|---|---|---|
| `/api/gaia` | ra, dec (deg), r (arcmin) | `{ gaia:[ same entries as frames.gaia ] }` |
| `/api/known` | ra, dec (deg), r (arcmin), mjd | `{ mjd, known:[ same entries as frames.known, with epoch_id null ] }` |

## GET /api/resolve

`q` is any coordinate format (`08h55m10.831s -7d14m42.53s`, colon or space sexagesimal, decimal
degrees) or a built-in name. No SIMBAD (CDS is down).

`{ q, name|null, ra, dec, slug|null, source:"coords"|"builtin" }`.
`slug` is set for featured targets, so the site can load `/data/<slug>/`. Built-in positions are
moved to the present for fast stars. 400 `bad_request` if `q` can't be parsed; 404 `not_found`
for an unknown name.

## API keys (v3.14, optional)

Every endpoint works without a key, as before; the site itself never sends one. A key names the script
or person behind a request, so its use shows in the logs, a per-key limit can apply, and a key can be
revoked without blocking anyone else.

- `POST /api/keys` with an optional JSON body `{ "label": "my script" }` (at most 60 printable characters;
  missing or empty = null) → 201 `{ key, id, label, created, header: "X-API-Key" }`, `Cache-Control: no-store`.
  `key` is `sks_` + 32 URL-safe characters and is shown only in this answer; the server keeps only its
  SHA-256. `id` is the first 12 hex digits of that hash, safe to show and log. `created` is ISO 8601 UTC.
  At most 10 new keys per hour from one address (per server instance): then 429 `rate_limited`.
- Send the key as the header `X-API-Key: sks_…` (or `Authorization: Bearer sks_…`) on any `/api/` request.
  No header: anonymous, unchanged. An unknown or revoked key: 401 `unauthorized`, never silently anonymous.
- A valid key may make `SKYSHIFT_KEY_LIMIT` requests per hour (default 1000, counted per server instance);
  past it 429 `rate_limited` with `Retry-After`. Anonymous requests have no limit unless the server sets
  `SKYSHIFT_ANON_LIMIT` (per address per hour; unset = none).
- `GET /api/keys/me` → `{ id, label, created }` for the key sent. `DELETE /api/keys/me` revokes it
  → `{ id, revoked: true }`. Both 401 without a valid key. A revoked key may still pass on another
  server instance for up to 5 minutes (each instance remembers a checked key that long).
- Answers served from the hosting CDN's cache never reach the server, so they are neither checked nor counted.
- Keys live in `gs://nasa-glazing-data/apikeys/<sha256>.json` (`SKYSHIFT_KEYS` overrides the place).

## Static twins

| path | written by | shape |
|---|---|---|
| `/data/<slug>/frames_<clock>_<fov>.json` + frame files | scripts/precompute (P2) | `GET /api/frames` for one clock and field (e.g. `frames_hours_20.json`), with the epochs of **several detectors** in one file, sorted by `mjd_mid` then `det`; the client filters by `det`. Frame files: `frames/<c><fov>_d<det>_v<visit>.bin` (months), `frames/<c><fov>_d<det>_<obs_id>.bin` (hours) |
| `/data/<slug>/frames.json` | scripts/precompute (P2) | A byte-for-byte copy of the target's own set, the file named by `index.json` `own` |
| `/data/<slug>/index.json` | scripts/precompute (P2) | Which sets exist, below |
| `/data/<slug>/object.json` | scripts/precompute (P2) | `GET /api/object` |
| `/data/validation.json` | scripts/validate (P1) | below |
| `/data/suspects.json` | pipeline export (P1) | below |
| `/data/candidates.json` | pipeline exporter `skyshift-candidates` (P1) | below |
| `/data/candidates-more/<zone>.json` | pipeline exporter `skyshift-candidates` (P1) | v3.24: one zone's grade C movers, candidates.json `more` (below) |
| `/data/cand/<id>/frames.json` + frame files | scripts/candidates/frames.py (P1, with the P2 engine) | `GET /api/frames` for one candidate (its clock, field and detectors); frame files `frames/<c><fov>_d<det>_…bin` as above. Only for candidates whose `frames_url` is set |
| `/data/cand/<id>/fill.json` + frame files | scripts/candidates/fill.py (P1, with the P2 engine) | **Optional** (v3.20). Same shape as that candidate's `frames.json` (hours clock, same field and grid), holding only exposures that fill its empty edge: never blinked, never measured. Frame files `frames/f<fov>_d<det>_…bin`. Missing (404) for most candidates |
| `/data/distant.json` | scripts/validate (P1), from the pipeline's `orphans` step | below (v3.6) |

In the repo these live in `web/public/data/`; Vite copies them to `web/dist/data/`.

### index.json

```
{ slug, name, center:{ra,dec}, own, follows_object?,
  frames:[{ file, clock, fov, dets:[det…], epochs, bytes }] }
```

`own` is the set copied to `frames.json`. `frames[]` lists every `frames_<clock>_<fov>.json` in
the folder, sorted by `file`: `dets` the detectors in it, `epochs` its number of epochs (all
detectors), `bytes` the JSON plus its frame files. `follows_object` (moving targets only) is true
when the frames follow the object, each epoch with its own `center`.

### targets.json: `dets` (optional)

A featured target may carry `dets: [det…]`, the detectors in which it can be measured (WISE
0855−0714, a 250 K brown dwarf invisible short of 3 µm: `[5, 6]`). `/api/object` then picks the
`measure` detector among these only. Absent: the server's own list (`obj.MEASURE_DETECTORS`),
else any detector. The server reads the targets from `scripts/validate/data/targets.json`
(`SKYSHIFT_TARGETS` overrides it); `web/src/data/targets.json` may carry the same field.

### validation.json

```
{ generated, data_release,
  checks:[{ id:"V1".."V11", claim, status:"pass"|"partial"|"fail"|"pending",
            headline, value|null, unit|null, expected, note|null, rows:[{…}] }] }
```

`headline` is the text the scoreboard prints (a poor number is still printed). `rows` holds one
flat object per item (a target, an asteroid, a star); P1 lists its keys per check in the note.
Written by scripts/validate/build_validation.py. Eleven checks, as in the plans
(docs/skyshift-hackathon-plan.html, "How we know it works"). V6 and V7 belonged to work cut on 14 Nov
(S2, S3) and were run on 15 Nov with V2's measurement (scripts/validate/v6_v7_motion.py, v3.15); V9–V11 were
added on 14 Nov. The file lists them in this order:

| id | claim | unit of `value` | expected |
|---|---|---|---|
| V1 | Five test objects recovered | targets | 5 of 5 |
| V2 | Motion matches Gaia | arcsec | Barnard's Star within 15% and 10° of Gaia |
| V3 | Position error floor | arcsec | ~0.25″, 10 or more reference stars |
| V4 | Asteroids re-found, targeted | asteroids | N of 60; median offset under 1 px; control under 5% |
| V5 | Asteroids re-found, blind (S1) | recall | Recall at V < 16 ≥ 50% (gate G2); control near 0 |
| V6 | Fast stars (S2) | slope | Measured against Gaia's predicted shift for the 40 fastest Gaia stars with G > 12: slope near 1 |
| V7 | Brown dwarfs (S3) | objects | A count: brown dwarfs faster than 1″/yr shifting the predicted way (≥ 3σ, within 30°) |
| V8 | Spectra and speed | ratio | Spectra within 20% of 2MASS Ks / WISE W1; first frame under 15 s |
| V9 | Astrometry matches Gaia | mas/yr | Proper-motion bias near 0; pull scatter near 1 |
| V10 | Galaxy distances match SDSS | dz/(1+z) | σ_NMAD ~0.02 for bright galaxies (AB < 17) |
| V11 | Asteroid sizes match published values | ratio | Diameter ratio near 1 |

A reader shows every check the file holds, in file order; an id missing from the file counts as `pending`.

### suspects.json

```
{ generated, data_release, zone,
  vetoes:[…], provisional, grades:{A,B,C}, gates:{<gate>:n},          # added 14 Nov (vetting)
  catalogues?:[…], warnings?:{<code>:n},                               # optional, v3.24
  suspects:[{ id, ra, dec, mjd, clock:"hours", det, evidence:"two_exposures"|"two_detectors",
              n_dots, snr, flux_ujy, cell, nearest_known_arcsec|null,
              grade:"A"|"B"|"C", reasons:[<gate>…], near_source_arcsec|null,      # added 14 Nov
              n_sessions?|null, rate_arcsec_h?|null, pa_deg?|null, rate_sigma?|null,   # optional, 14 Nov (tracks)
              warn_codes?:[<code>…], warn_values?:{<code>:{<name>:number}} }] }        # optional, v3.24
```

Movers that are not certainly something known (contracts/dots.md `suspects`). v3.24 (16 Nov): only the certain are
removed: a known body (`mpc` below; MPC asteroids and comets, from v3.17 Horizons' planets and moons), a planet, its
moons or its glare (`planets`), and a hot pixel (in one detector, the dots of ≥ 2 exposures within ±1 px of their median
pixel while the sky under them moved ≥ 3 px; named in no `vetoes`: summary.json `steps.suspects.cut_hot_pixel`).
Everything else the older rules removed is kept, with `warn_codes` and `warn_values` or as grade C with a reason (below:
kept with warnings). Before v3.24 the catalogue vetoes and NASA's SOURCE, GHOST, CROSSTALK, HALO and BLOOM bits removed
a mover too. Written by the pipeline's
`suspects` step (`results/<zone>/suspects.json`, copied to `web/public/data/suspects.json`).
They are also written to Firestore `candidates` with `source: "pipeline"`.
Ranked by `grade` (A, B, C), then `snr` (highest first). A file without `grade` (written before 14 Nov evening) is
ungraded: show its suspects as unvetted, not as B.

| field | type | meaning |
|---|---|---|
| zone | string | Pipeline zone, `pilot` or `hunt` |
| vetoes | string[] | Zone-level checks every listed suspect passed. v3.24: `mpc`: no known body brighter than V 21 (or without V) within 5″ of it at its time; none within 30″ moving at its in-session rate (within 50% or 3σ of ours) and in its direction (within 45° or 3σ) where our motion is measured (link's `motion_snr` ≥ 3, or ≥ 3 exposures); and none of any brightness certainly it (within 10″ at every dot's time and place, rate within 20%, direction within 15°: the `status` `known` rule of candidates.json). Within 5″ the place alone decides: a motion from 2–6 exposures is too noisy to overrule it (a 20% / 15° test would keep 32% of ecl04's 1,911 movers within 5″ of an MPC prediction). `planets` (v3.18: not near a planet at its time, below). The catalogues are no longer vetoes (`catalogues`). Older files: `mpc` = no known body (V < 21 or no V) within 30″ by place alone, and `gaia` (no Gaia DR3 within 6″), `catwise` (CatWISE2020, 6″) or `allwise` (AllWISE, 6″: zones with a Gaia-only refcat) here; a catalogue missing from them was not checked |
| provisional | bool | true when Gaia or every WISE catalogue could not be checked (e.g. a Gaia-only refcat with AllWISE unreachable): very red WISE-only sources may still be listed without a catalogue reason or `catalogue_star`. Say so next to the zone's suspects |
| catalogues | string[], **optional** | v3.24. The catalogues every suspect was checked against within 6″, in this order: `gaia` (Gaia DR3, G < 20), `catwise` (CatWISE2020), `allwise` (AllWISE), `2mass` (2MASS PSC). A catalogue missing here was not checked. A source within 6″ is the grade C reason of that name when it is at most 2 mag fainter than the suspect (`reasons`), else the warning `catalogue_star`; never a removal. Absent in older files: their catalogues are in `vetoes` |
| warnings | {code: int}, **optional** | v3.24. Per code of `warn_codes`, the suspects carrying it (a suspect can carry several). Absent in older files: none |
| grades | object | Count of suspects per grade, `{ "A": n, "B": n, "C": n }` |
| gates | object | Per gate name (below), the suspects failing it; a suspect can fail several |
| id | string | Unique in the file. The pipeline writes `<zone>-<cell>-<minute>` (minute = round(mjd × 1440)); treat it as opaque |
| ra, dec | float, deg | Mean position of the mover's dots |
| mjd | float, MJD | Mean MJD-AVG of the mover's dots |
| det | int 1–6 | Detector of its highest-S/N dot |
| evidence | string | `two_exposures` (paired inside one session) or `two_detectors` (D1+D4, D2+D5, D3+D6 at the same moment) |
| n_dots, snr, flux_ujy | int, float, float µJy | Dots in the mover, their highest S/N and median brightness |
| cell | integer, no unit | The 6″ sky cell of (`ra`, `dec`), exactly contracts/dots.md's `cell` (`band × 10 000 000 + col`). An id for grouping, not a quantity: neighbouring cells are not neighbouring numbers. At most ~1.08 × 10¹², so a JSON number is exact in JavaScript (below 2⁵³); never a string |
| nearest_known_arcsec | float, arcsec, or null | Distance to the nearest known object predicted in the same session, moved to `mjd`; null if none within 600″. v3.24: of the known objects brighter than V 21 or without V (the pipeline's cache keeps them to V 23, `MPC_V_STORE`, for the certain rule only). Suspects have none within 5″; one 5–30″ away that does not move like it gives `known_nearby` in `warn_codes` (its name: suspects.parquet `nearest_known_name`). Older files: none within 30″ |
| grade | string | `C`: fails a gate or is catalogued (v3.24: a catalogue source within 6″ at most 2 mag fainter than it; `reasons` says which); kept in the file, shown greyed with its reasons. `B`: passes every gate; its motion is not shown (most suspects are 2 dots of one session, and at S/N 7–9 the position error exceeds the motion between exposures): an "unexplained one-session source". `A`: candidate mover: on a track over ≥ 2 sessions with motion ≥ 5σ, no catalogue reason and no known body certainly it. That is a B whose mover the pipeline's `tracklets` step linked into a track (movers of ≥ 3 sessions of one visit, or from 16 Nov a fast track of 2 sessions, contracts/dots.md `tracklets`) with movers in ≥ 2 sessions and rate / rate σ ≥ 5; `n_sessions`, `rate_arcsec_h`, `pa_deg`, `rate_sigma` below give the track. v3.24: a track faster than 400″/h makes A only with a recovered dot of the track (tracklet_dots.parquet) that is not `in_fixed`, or a suspect of the track with a detector partner (D1+D4, D2+D5, D3+D6 in one exposure); else it stays B (summary.json `steps.suspects.vetting.tracklets.fast_held`: hunt's one fast track was a D6-only artefact). Show A first, as the most promising. A file written without tracks has no A. 16 Nov (hunt, ecl04, ecl08, ecl11): real asteroids taken as unknown are 88–91% A or B, the rest (no known body within 60″, mostly junk) 2.0–5.7% |
| reasons | string[] | The gates it fails, in this order (empty for A and B): `not_vetted` (no dot data to vet: an old movers table, or a dot without a pixel position), `hot_pixel` (a dot on a detector pixel, ±1 px, where the zone's movers have dots in ≥ 3 exposures or its free dots in ≥ 5: hot pixels NASA's HOT bit misses), `bad_column` (≥ 3 free dots of that exposure and detector within ±0.5 px in x; v3.24: and more than chance gives at that photo's free-dot density, Poisson tail ≤ 0.1%), `edge` (a dot within 6 px of the detector edge), `masked_peak` (a mask bit of contracts/flags.md at a dot's peak, OVERFLOW/NONLINEAR aside), `sharp_outlier` (a dot sharper than a star: √(a·b) < 0.6 × (D1–D3) or 0.7 × (D4–D6) the detector's stellar value at its S/N, with OUTLIER at the peak; OUTLIER alone never counts), `very_sharp` (√(a·b) < 0.4 × the stellar value at S/N ≥ 15, OUTLIER or not), `flux_ratio` (brightest dot > 4× the faintest within one detector), `red_photo` (every dot from a photo graded red by the photo QA, qa_photos.parquet; amber never counts), v3.24: `bloom` (BLOOM at every dot's peak; in a 3×3 only, the warning `flag_bloom`), `halo` (HALO at every dot's peak; in a 3×3 only, `flag_halo`), `bright_star` (a fixed source with S/N ≥ 100 within 15″; to 30″, the warning), `gaia` (a Gaia DR3 star, G < 20, within 6″ at most 2 mag fainter than the suspect), `catwise` (CatWISE2020, the same test in W1/W2), `2mass` (a 2MASS PSC source within 6″; v3.24 at most 2 mag fainter, J/Ks), `allwise` (an AllWISE source within 6″, v3.24 at most 2 mag fainter: the WISE catalogue of a Gaia-only refcat and a second one where CatWISE2020 is; a failed read only skips it). "At most 2 mag fainter": the catalogue's brightest band in AB (Vega + 0 G, 2.699 W1, 3.339 W2, 0.91 J, 1.85 Ks) minus the suspect's AB magnitude from `flux_ujy`; a source without a magnitude counts. A fainter one is a chance neighbour (real asteroids' median 2.5–4.1 mag fainter, junk on a star 1.1–1.6): the warning `catalogue_star` only. Thresholds: pipeline config `VET_*`, `SUSPECT_*`. On 150 MPC-matched hunt movers (real asteroids) 141 pass every gate (edge 4, masked_peak 6); 16 Nov, four zones: `edge` and `masked_peak` still grade 1.4–2.4% and 3.5–4.3% of real asteroids C |
| near_source_arcsec | float, arcsec, or null | Nearest catalogue (Gaia, CatWISE, 2MASS) or fixed source within 10″: a possible blend or PSF wing. A warning, not a gate; null if none within 10″ |
| n_sessions | int or null, **optional** | The track this suspect is on (pipeline `tracklets`, joined on `id`): sessions of its visit with a mover on the track (≥ 3 on any track). null when the suspect is on no track. The four track fields are absent from a file written without tracks: read absent as null. Present on any suspect on a track, whatever its grade; A also needs rate / rate_sigma ≥ 5 and no failed gate. Added 14 Nov |
| rate_arcsec_h | float, ″/h, or null, **optional** | The track's apparent rate at this suspect's time (local fit over the track's movers); null off a track |
| pa_deg | float, degrees, or null, **optional** | The track's direction of motion, east of north, 0–360; null off a track |
| rate_sigma | float, ″/h, or null, **optional** | 1σ error of `rate_arcsec_h` (scaled up by √χ²_ν of the fit when > 1); null off a track |
| warn_codes | string[], **optional** | v3.24. What can't be ruled out, as candidates.json check codes, in this order when present: `known_nearby` (a known body, V < 21 or no V, within 30″ that is not certainly it), `flag_bloom`, `flag_halo`, `flag_ghost` (GHOST, GHOST_FPA or GHOST_EXT), `flag_crosstalk` (the bit in any dot's 3×3), `bright_star` (a fixed source with S/N ≥ 100 within 30″), `catalogue_star` (a catalogue source within 6″ of any brightness, or NASA's SOURCE bit at every dot's peak), `edge` (gate `edge`), `persistence` (PERSIST at a dot's peak), `sharp` (gate `sharp_outlier` or `very_sharp`), `flux_jump` (gate `flux_ratio`). [] when none. The exporter makes each a check (candidates.json). Absent: read as [] |
| warn_values | {code: {name: number}}, **optional** | v3.24. The numbers of each code in `warn_codes` (a name is left out when unknown; a JSON string in suspects.parquet). `known_nearby`: `sep_arcsec`, `known_rate_arcsec_h`, `known_pa_deg`, `known_v_mag` (the body's), `rate_arcsec_h`, `pa_deg` (ours, in session, when measured). `flag_*`: `dots_at_peak` (dots with the bit at their peak), `n_dots`. `bright_star`: `sep_arcsec`, `star_snr`, `star_ab_mag`, `star_flux_ratio` (the star's flux ÷ the suspect's). `catalogue_star`: `sep_arcsec` (the nearest), `source_mask_all_dots` (1: SOURCE at every dot's peak), per catalogue `gaia_sep_arcsec`, `gaia_g_mag`, `catwise_sep_arcsec`, `catwise_w1_mag`, `catwise_w2_mag`, `allwise_sep_arcsec`, `allwise_w1_mag`, `allwise_w2_mag`, `tmass_sep_arcsec`, `tmass_j_mag`, `tmass_k_mag` (Vega, as catalogued), `mover_ab_mag`, `fainter_mag` (AB: the brightest catalogued band minus the suspect; > 2: a chance neighbour, not a reason). `edge`: `edge_px`. `persistence`: `dots_at_peak`, `n_dots`. `sharp`: `size_ratio` (the sharpest dot ÷ a star's size at its S/N), `outlier_at_peak` (1: gate `sharp_outlier`). `flux_jump`: `ratio`. Absent: read as {} |

**Planets (v3.18).** Besides the 30″ match to Horizons' planets and named moons (v3.17, part of `mpc`'s known
check), a mover is known (veto `planets`) when it lies within a planet's radius at its time: Mars 60′, Jupiter 80′,
Saturn 45′, Uranus 5′, Neptune 3′ from the planet's geocentric astrometric position (skyshift_core.planets:
astropy's built-in ephemeris, within 20″ of JPL Horizons, no network). The radii hold the moons SPHEREx sees at
their farthest (Callisto, Himalia, Elara; Iapetus, Phoebe; Oberon; Triton), moons no list names (Carme), and the
planet's glare. 15 Nov: 131 of the 1,885 movers then listed in candidates.json, 54 of its 56 grade A (Iapetus,
Oberon, Himalia, Uranus, Neptune, Phoebe, Titan, the Galilean moons, …; 18 of the 131 are no listed body: Jupiter's
glare, grades B and C). Files written before v3.18 lack `planets` in `vetoes`: their suspects may include such movers.

**Kept with warnings (v3.24, 16 Nov).** The project's rule: remove only what is certain, keep everything else as a
warning that ranks lower. Until 16 Nov `suspects` removed 34–45% of the real asteroids (movers within 5″ of an MPC
prediction, taken as if unknown) before anyone saw them; now all are kept, 88–91% as A or B. No catalogue removes a
suspect at any radius: even a source within 2″ and at most 2 mag fainter hits 0.1–0.5% of real asteroids in AllWISE;
4″ would halve the real share but lose 3–6.5 points of the rest. The lists grow 10–40× (hunt 23 → 751 suspects,
ecl04 65 → 773, ecl08 62 → 2,543, ecl11 330 → 5,377; 97–99% of the added rows grade C): candidates.json lists the
zones' open grade C movers apart (`more`, below). 2MASS is checked from v3.24 on: before, the refcat's label `2mass`
was looked up as `tmass`, so no suspect was ever checked against 2MASS (hunt: 228 suspects gain `2mass`, 1 B → C).
suspects.parquet (contracts/dots.md) also keeps `nearest_known_name` and link's in-session motion
(`session_rate_arcsec_h`, `session_pa_deg`): on a track `rate_arcsec_h` and `pa_deg` are the track's (306P/LINEAR:
107″/h at 54° over its track, 98–105″/h at 76–86° within one session), so match a known body on the in-session pair.
Files written before v3.24 have no `catalogues`, `warnings`, `warn_codes` or `warn_values`: their vetoes removed the
rest.

### candidates.json

What the pipeline found, for people to review (web/DESIGN.md, "Product"). Written by the pipeline's
exporter (`cd pipeline && uv run skyshift-candidates`, pipeline/src/skyshift_pipeline/candidates.py) from
`results/<zone>/` of every finished zone; static frames for the most promising by
scripts/candidates/frames.py. Type `CandidatesResponse` in web/src/api/types.ts. Example (a real sample):
fixtures/api/candidates_sample.json (fixtures/api/candidates.json is an older hand-made pilot sample that
web tests still use; it predates `zones` and the high_pm id rule).

```
{ generated, data_release, zones:[zone…], counts:{ mover, high_pm, spectral, shape },
  more:[{ zone, kind:"mover", grade:"C", count, url }],                                      # v3.24
  candidates:[{ id, kind:"mover"|"high_pm"|"spectral"|"shape", ra, dec, mjd, clock:"hours"|"months", det, zone, zones,
                score, snr, flux_ujy, rate_arcsec_h, pm_mas_yr, pm_err_mas_yr, pa_deg,
                nearest_known_arcsec, gaia_match, catwise_match, galactic_b,
                labels:[label…], reasons:[code…], why, flags:[flag…], frames_url|null,
                status, known_name, checks:[{ code, level, text, look, values }] }] }      # v3.23
```

| field | type | meaning |
|---|---|---|
| generated | string | Time of the export, ISO 8601 UTC (`2026-11-14T15:20:00Z`) |
| data_release | string | Collections the zones' photos came from, e.g. `"DR1+QR3"` |
| zones | [string] | Zones searched, in the order used: the larger first (`["hunt", "pilot"]`). A zone appears even when all its objects were already listed from an earlier one |
| counts | {kind: int} | Candidates of each kind in the file (every kind's key, 0 if none; `shape` added in v3.7: older files lack it, read it as 0). v3.24: this file only; a kind's total is `counts[kind]` plus the `count` of its `more` entries |
| candidates | [object] | Sorted by zone (in `zones` order), then kind (mover, high_pm, spectral, shape), then `grade` (A, B or null, C; added 14 Nov), then `score` descending |
| more | [object] | v3.24. The Less likely files (below): one `{ zone, kind, grade, count, url }` per zone that has any, in `zones` order. `kind` `mover`, `grade` `C` (the only ones today); `count` = that file's `candidates` length; `url` = `candidates-more/<zone>.json`, resolved against the URL of candidates.json like `frames_url`. Older files lack it: read as [] (every candidate is in this file). **Not yet in types.ts** (proposal: `more?: { zone: string; kind: "mover"; grade: "C"; count: number; url: string }[]`) |

Each candidate: every field is present; a missing value is `null`, never omitted.

| field | type | meaning |
|---|---|---|
| id | string | Unique in the file and stable across runs; Firestore `votes.candidate_id`. Treat as opaque. mover: the pipeline's suspect id `<zone>-<cell>-<minute>` (suspects.json); high_pm: `<zone>-pm-<source_id>` (fit) or `<zone>-pm-<source_id>-<source_id_b>` (pair); spectral: `<zone>-sp-<source_id>`; shape: `<zone>-sh-<source_id>`. `source_id` is link's id from the source's median position (contracts/dots.md) |
| kind | string | `mover` "Fast mover": moves within hours (an uncatalogued asteroid or comet?). `high_pm` "Moving star": moves between visits (a nearby star or brown dwarf?). `spectral` "Odd spectrum": molecule bands worth a look. `shape` "One or two?": its image is stretched like two objects, but Gaia DR3 and CatWISE2020 list only one (a moving object passing a star, or an unknown companion?); v3.7 |
| ra, dec | float, deg | mover: mean of its dots (as suspects.json). high_pm: the pipeline's position: fit, at the fit's epoch; pair, the first visit's source. spectral, shape: the source's median position |
| mjd | float, MJD | mover: mean MJD-AVG of its dots. high_pm: fit, the astrometric fit's epoch; pair, the first visit's mean time. spectral: mean MJD-AVG of the source's dots. shape: mean MJD-AVG of the dots the shape was measured on |
| clock | string | `hours` for mover, `months` for high_pm, spectral and shape: the clock to blink it on |
| det | int 1–6 | mover: detector of its highest-S/N dot. high_pm, spectral, shape: the detector where the source's spectrum has the highest S/N (pair: the first visit's source) |
| zone | string | Pipeline zone it came from, one of `zones` |
| zones | [string] | Every zone that found it, `zone` first: `["hunt", "pilot"]` for an object the pilot found too (as suspects.json's `zones`). **Not yet in types.ts** (proposal: `zones?: string[]`); readers that don't know it ignore it |
| score | float 0–1 | How promising: **higher = more promising**, in 0..1, sorted descending. Defined per kind below; where the pipeline's own score is lower-is-better (high_pm) the exporter inverts it (e^(−s/0.2)). Never null in the export (the type allows it) |
| snr | float or null | mover: its highest dot S/N. high_pm, spectral, shape: S/N of the spectrum in `det` (its 17 channels' S/N added in quadrature) |
| flux_ujy | float µJy or null | mover: median of its dots. high_pm, spectral, shape: median of the spectrum's 17 channels in `det` |
| rate_arcsec_h | float ″/h or null | mover: apparent rate from a weighted straight-line fit of its dots' positions against time (dot positions re-read from dots/). null for `two_detectors` (one moment) and other kinds |
| pm_mas_yr, pm_err_mas_yr | float mas/yr or null | high_pm only: total proper motion and its 1σ error (the pipeline's `pm`, `pm_err`) |
| pa_deg | float deg or null | Direction of motion, east of north, 0–360: high_pm from (pmra, pmdec); mover from its dots (null without a rate). null for spectral. shape: not a motion but the axis of the stretch on the sky, east of north, **0–180** (where the second object would sit, on either side) |
| nearest_known_arcsec | float ″ or null | mover only: as suspects.json, the nearest known body predicted in the same session, moved to `mjd`: an asteroid or comet (MPC orbits) or, from v3.17, a planet or large moon (JPL Horizons); **null = none within 600″**; v3.24: of the bodies brighter than V 21 or without V, as suspects.json (fainter ones: the `known` check producer's `known_nearby`). Always null for high_pm and spectral (not checked) |
| gaia_match | bool | A Gaia DR3 source sits here (its motion may still disagree: see `reasons`). mover: v3.24 true when the suspects' reason `gaia` is set (a Gaia DR3 star within 6″ at most 2 mag fainter); a fainter one is the check `catalogue_star`; before v3.24 false (suspects had no Gaia star within 6″; see `gaia_unchecked`). high_pm: the pipeline's `status` is `gaia` (either side matched). spectral: classify's Gaia match. shape: a Gaia DR3 star within 6″ is the one listed object |
| catwise_match | bool | A CatWISE2020 source sits here. mover: v3.24 true when the suspects' reason `catwise` is set (as `gaia_match`); before v3.24 false (none within 6″). high_pm: a CatWISE counterpart on either side. spectral: crossids has one. shape: a CatWISE2020 source within 6″ (the listed object, or its infrared entry) |
| galactic_b | float deg | Galactic latitude (astropy, ICRS to Galactic). \|b\| < 10 is the crowded Milky Way plane |
| labels | [string] | Filter labels, vocabulary below; order as listed there. [] for movers and shape |
| grade | string or null | Movers: the suspects' vetting grade, `A`, `B` or `C` (suspects.json `grade`; A = a candidate mover on a track over ≥ 2 sessions at ≥ 5σ, its `why` names the track and it never gets `motion_not_significant`; C = a failed gate or (v3.24) a catalogue source within 6″ at most 2 mag fainter, its gates are in `reasons`). null for high_pm, spectral and shape, and for zones run before the vetting. Grade C movers come after the zone's other movers and score × 0.1; v3.24: those with `status` `open` are in the zone's `more` file, not here. Added 14 Nov |
| reasons | [string] | Codes, vocabulary below: why it was listed and what to be careful about |
| why | string | One plain English sentence: why it is on the list (examples below) |
| flags | [string] | Artefact flags near it (contracts/flags.md record-only bits), lower case: `outlier`, `crosstalk`, `ghost`, `ghost_fpa`, `ghost_ext`, `bloom`, `halo`. mover: any of its dots' 3×3 `flags`; high_pm, spectral: set on ≥ 25% of the source's dots (either source of a pair); shape: [] (dots with these flags are not used, and a source with them on ≥ 25% of its dots is not listed) |
| frames_url | string or null | Static blink frames, resolved against the URL of candidates.json like `data_url` (`new URL(frames_url, jsonUrl)`): `cand/<id>/frames.json`, a `GET /api/frames` response whose `data_url`s resolve against that file. null: call `/api/frames?ra=&dec=&clock=&fov=&det=` live |
| status | string | v3.23. `open`: to check. `known`: certainly a known body: an MPC asteroid or comet, or a JPL Horizons planet or moon, predicted within 10″ of every dot's time and place (movers) with its rate within 20% and direction within 15° when both have one; its name in `known_name`. `flaw`: certainly the instrument: every dot of a mover on the same detector pixel (±1 px) although the sky moved under it (a hot pixel), or another flaw named in `checks` with level `strong` and code ending `_proven`. Nothing is deleted for a status: the site lists `known` and `flaw` apart from `open`. Older files lack it: read as `open`. v3.24: a check producer's side file can set `known` (with `known_name`) or `flaw`; precedence known > flaw > open (section Side files) |
| known_name | string or null | v3.23. `status` `known`: the body's MPC designation or name, e.g. `306P/LINEAR`, `(161055) 2002 JV46`, `Iapetus`. Else null |
| checks | [object] | v3.23. What could make it something else, measured, each with exactly what to look at. Sorted `strong`, `warn`, then `ok`; [] when none. Older files lack it: read as []. Fields below |

**checks** (v3.23): one object per thing to check.

| field | type | meaning |
|---|---|---|
| code | string | What is checked, from the vocabulary below; new codes may appear: show `text` and `look` as given |
| level | string | `strong`: a measured sign it is not new or not real (it lowers `score`; nothing is hidden for it). `warn`: can't be ruled out, look. `ok`: a measured sign for it (e.g. the partner detector saw it too) |
| text | string | What was measured, one plain sentence with the numbers: "D3 measured 76 ± 41 µJy at its place, against 1,795 in D6 (ratio 0.04; known asteroids here: 0.03–3)" |
| look | string | Exactly what a person should look at in the Viewer, one plain sentence, or "" |
| values | object or null | The numbers in `text`, keyed by name (units in the key: `flux_ujy`, `ratio`, `sep_arcsec`, `rate_arcsec_h`, `photos`…), for filters; null when none |

Codes (v3.23; the exporter's tests hold the rules and texts). mover: `known_nearby` (a known body near it,
with its rate and direction against ours), `one_detector` (the simultaneous partner photographed the spot and
measured little: its flux or limit against the known-asteroid range; D4 alone is `strong`), `partner_seen`
(`ok`), `fast`, `odd_direction` (against the visit's common asteroid motion), `two_photos` (no straight-line
check), `may_not_move`, `flux_jump`, `bright_star`, `flag_outlier`, `flag_crosstalk`, `flag_ghost`, `flag_bloom`,
`flag_halo`, `persistence`, `sharp` (narrower than the stars around it), `trail`, `repeat_position`, `edge`,
`catalogue_star` (a catalogue source within 6″ that may or may not be it), `detector_excess`, `chance_tracks`
(the zone's shuffled-session control), `faint_on_path` (signal below the 5σ cut on its predicted path: `ok` when
found). high_pm: `gaia_disagrees`, `parallax_leak`, `few_dots`, `saturated`, `blend`, `impossible_motion`,
`one_detector_shift`. spectral: `catalogues_checked` (which catalogues were searched), `pah_only`,
`behind_cloud`, `few_channels`, `bright_neighbour`, `blend`, `no_j`. shape: `blend`, `bright_neighbour`, `galaxy`.

**Codes and levels** (v3.24). Made by the exporter (pipeline candidates.py, candidate_checks.py) from the tables it
reads, or by a check producer (`known`, `pixel`, `catalogue`: scripts/candidates/<source>_check.py, merged from side
files, below). Where both make a code, the producer's check replaces the exporter's. `values` keys carry their unit
(`_arcsec`, `_ujy`, `_arcsec_h`, `_deg`, `_mag` Vega as catalogued, `_px`); a key a reader doesn't know: ignore it.
Reference shares below are known asteroids (4,646 track movers, hunt + ecl04 + ecl08 + ecl11) against the list.
The suspects' `warn_codes` (v3.24, suspects.json) become `warn` checks of the same code with their `warn_values` as
`values`; where the exporter makes that code too, its level stays and the suspects' numbers join its `values` (for
`catalogue_star`, `bright_star` and `known_nearby` their words replace a `warn`'s text).

| code | kind | by | levels: rule | values |
|---|---|---|---|---|
| known_nearby | mover | exporter | `warn`: a known body (V < 21 or no V) predicted within 2′ | `sep_arcsec` (and the suspects' `known_rate_arcsec_h`, `known_pa_deg`, `known_v_mag`, `rate_arcsec_h`, `pa_deg`) |
| known_nearby | mover | known | `strong` with `status` known and `known_name`: certain by the `status` rule, any brightness, seen from SPHEREx at each dot's time (MPC asteroids at any V, every MPC comet, Horizons planets and moons). `strong`, status open: a body within 2″ at every one of ≥ 2 dot times whose motion misses the rule (two dots 2 minutes apart measure a motion only to about ±30″/h). `warn`: the nearest body within 2′, not certain. `ok`: none within 2′ | `name`, `body` (`asteroid`, `comet`, `planet`, `moon`), `v`, `sep_arcsec` (nearest at any dot time), `sep_max_arcsec` (farthest), `rate_arcsec_h`, `pa_deg` (the body's), `rate_ratio`, `dpa_deg`, `our_rate_arcsec_h`, `our_pa_deg`, `times`, `from_dots` |
| known_nearby | high_pm | known | `warn`: a slow distant body (perihelion > 8 AU, < 1″/h) within 10″ at `mjd`. Never `known`: a fixed source is not a moving body | as above |
| flag_outlier | mover | exporter | `ok`: OUTLIER at the peak of a dot (68% of known asteroids, 28% of the list): a sign it moved, never a doubt | `dots_peak`, `dots`, `known_share` |
| flag_crosstalk, flag_ghost, flag_bloom, flag_halo | mover | exporter | `warn`: the bit (GHOST: any of GHOST, GHOST_FPA, GHOST_EXT) in a dot's 3×3 or at a peak. `strong`: at every dot's peak (the suspects' reasons `bloom`, `halo`) | `dots_flagged`, `dots_peak`, `dots`, `known_share` (the suspects' `dots_at_peak`, `n_dots`) |
| one_detector | mover | exporter | Seen in one detector, never with its simultaneous partner. D4 alone `strong` (known 0.3%, the list 35%), `warn` when no D1 photo of that moment covers the spot; D3, D5 or D6 alone `warn`; D1 or D2 alone no check (12% of known asteroids are D2-only) | `detector`, `partner`, `exposures`, `partner_photos`, `known_share`, `list_share` |
| one_detector | mover | pixel | Forced photometry at its place in the partner's photo of the same moment finds under 3σ: `strong` when seen in D4 alone, else `warn` (`beyond_known`: the 3σ bound puts its colour outside every known asteroid's). Also `warn` when the partner's light there is a fixed source (as much on other days) | `partner_flux_ujy`, `partner_err_ujy`, `flux_ujy`, `bound`, `bound_kind` (`lower`, `upper`), `beyond_known`, `photos`, `known_lo`, `known_hi`; or `fixed_flux_ujy` |
| partner_seen | mover | exporter, pixel | `ok`. exporter: both detectors of a pair have a dot at the same moment. pixel: both dots, or the partner's forced flux ≥ 3σ at its place, with the colour against the known-asteroid range (never a cut: 306P is D6-only and very red) | exporter `exposures_both`, `exposures`; pixel `ratio`, `photos`, `known_lo`, `known_hi`, `partner_flux_ujy`, `partner_err_ujy`, `flux_ujy` |
| two_photos | mover | exporter | `warn`: seen in 2 exposures and on no track, or `two_detectors` in one exposure (no straight-line check) | `exposures`, `minutes` |
| may_not_move | mover | exporter | `warn`: its own motion under 3σ | `rate_arcsec_h`, `rate_err_arcsec_h`, `sigma` |
| chance_tracks | mover | exporter | On a track: `ok` when the zone's shuffled control made no track (summary.json `steps.tracklets.control`), else `warn` | `sessions`, `rate_arcsec_h`, `control_tracks` |
| fast | mover | exporter | `warn`: faster than 99% of the visit's known asteroids on tracks (≥ 30 of them, else the zone's) | `rate_arcsec_h`, `rate_err_arcsec_h`, `known_quantile_arcsec_h`, `known_n`, `known_faster` |
| odd_direction | mover | exporter | Over 45° from the visit's median known-asteroid direction: `strong` when also `fast`, no known asteroid moves like it and it is on no track; else `warn` | `pa_deg`, `dpa_deg`, `visit_pa_deg`, `known_n`, `known_like_it` |
| flux_jump | mover | exporter | Brightest / faintest dot in one detector > 2.2 (98.4% of known asteroids below): `warn`; > 4 or the gate `flux_ratio`: `strong` | `ratio`, `detector`, `known_max_ratio` |
| repeat_position | mover | exporter | `strong` (the reason, below) | `sep_arcsec`, `days`, `implied_rate_arcsec_h` |
| detector_excess | mover | exporter | `warn` (the reason, below): a zone statistic, nothing about the object | `detector`, `visit`, `ratio` |
| catalogue_star | mover | exporter | `strong`: the vetting reasons `gaia`, `catwise`, `2mass`, `allwise`. `warn`: a catalogued or fixed source within 10″ (`near_source_arcsec`) | `catalogs`, `sep_arcsec` (and the suspects' numbers when its table has them) |
| catalogue_star | mover | catalogue | `warn`: the nearest catalogue source within 6″ (Gaia DR3 at any G, CatWISE2020, AllWISE, 2MASS PSC and XSC, moved to the candidate's time), with the chance that a random spot of the zone has one that close (from the zone's density in the 6–10″ ring around its candidates; near the ecliptic CatWISE alone gives 39% within 6″) | `catalogue`, `sep_arcsec`, the catalogue's own magnitudes (`g_mag`, `bp_rp`; `w1_mag`, `w2_mag`, `w3_mag`; `j_mag`, `h_mag`, `ks_mag`; XSC `ks_mag`, `r_arcsec`), `n_catalogues`, `chance` (0–1, this close), `chance_6arcsec` (0–1, within 6″) |
| catalogue_star | shape | exporter | `strong`: the two-source fit's companion is catalogued | `catalogue` |
| bright_star | mover | exporter | `strong`: the vetting reason `bright_star` (a fixed source of S/N ≥ 100 within 15″). `warn`: a Gaia G < 10 star within 120″ | `g_mag`, `sep_arcsec` (or the suspects' `star_snr`, `star_ab_mag`, `star_flux_ratio`) |
| hot_pixel | mover | exporter | `strong`: the vetting gate (suspects.json `hot_pixel`) | |
| hot_pixel | mover | pixel | `strong` with status `flaw`: every dot (≥ 2) within ±1 px of one detector pixel while the sky moved ≥ 6″ (the `status` rule). `warn`: the pixel under some, not all, dots glows as for `hot_pixel_proven` | `det`, `dx_px`, `dy_px`, `sky_arcsec`, `dots`; or as `hot_pixel_proven` |
| hot_pixel_proven | mover | exporter, pixel | `strong`, status `flaw`. exporter: every dot on one detector within 1 px of their median in x and y, over ≥ 2 exposures. pixel: the detector pixel under every dot (≥ 2) glows at ≥ 3σ and 0.5–2.5 × the dot's flux in every neighbouring exposure of that detector where the sky under it moved ≥ 4 px (at least 2: one before, one after) | exporter `detector`, `exposures`; pixel `dots`, `photos`, `flux_ujy`, `pixel_flux_ujy` |
| persistence | mover | exporter, pixel | `warn`. exporter: the suspects' warning, PERSIST at a dot's peak. pixel: some dots sit where a star saturated the same pixels (OVERFLOW/NONLINEAR within 1.5 px) in an exposure of that detector up to 30 min earlier | exporter `dots_at_peak`, `n_dots`; pixel `dots`, `flux_ujy`, `prev_flux_ujy`, `minutes`, `saturated` |
| persistence_proven | mover | pixel | `strong`, status `flaw`: every dot does | as `persistence` |
| sharp | mover | exporter | `strong`: the gate `sharp_outlier` or `very_sharp` | `size_arcsec`, `star_size_arcsec`, `ratio` |
| sharp | mover | pixel | Dots narrower than 0.7 × the stars of similar S/N (within ×/÷ 2) in the same photo: `strong` when every dot (≥ 2) is under 0.55 ×, else `warn`; `ok` when every dot is 0.8–1.3 × | `size_ratio_min`, `size_ratio_max`, `dots`, `sharp_dots`, `size_arcsec` |
| wide | mover | pixel | Dots ≥ 1.3 × as wide (√(a·b), the pipeline's dot size) as the stars of similar S/N (within ×/÷ 2) in the same photo (known asteroids: 0.8–1.3 ×): `strong` when every dot (≥ 2) is, else `warn` when the median dot is | `size_ratio_min`, `size_ratio_max`, `dots`, `wide_dots`, `size_arcsec` |
| one_photo | mover | pixel | Forced flux at each dot's sky place in up to 6 other exposures of its detector in the visit, from sessions ≥ 1 h away where the mover has moved ≥ 15″. `strong`: every place holds a fixed source (together ≥ 3σ and ≥ 0.3 × the dot, light centred < 0.8 px away: a star just under the cut, not a mover). `warn`: some places do, or a neighbour's light spills in (centroid ≥ 0.8 px away). `ok`: nothing there, to a 3σ limit below 0.5 × the dot | `places`, `steady_places` or `neighbour_places`, `photos`, `fixed_flux_ujy`, `fixed_err_ujy`, `ratio`, `offset_arcsec`, `limit_ratio` |
| faint_on_path | mover | pixel | Forced photometry where its straight line predicts it in other exposures of its detector (1σ under 3 px): `ok` when found (≥ 3σ, 0.25–4 × its flux) in ≥ 2; `warn` when absent where it should read ≥ 5σ | `photos`, `found`, `best_snr`, `flux_ujy`, `path_flux_ujy` |
| trail | mover | pixel | Only where a 113 s exposure stretches a point ≥ 1.25 × (≳ 270″/h): `ok` when the dots' median elong ≥ the expected stretch − 0.15, else `warn` | `rate_arcsec_h`, `expected_elong`, `elong_min`, `elong_max` |
| edge, bad_column, masked_peak, red_photo | mover | exporter | `strong`: the vetting gate (`edge` also when the exporter finds a dot within 6 px of the edge itself) | `edge`: `edge_px`, `dots`; `masked_peak`: `bits` |
| not_vetted | mover | exporter | `warn`: the vetting could not run (no dot positions) | |
| impossible_motion, gaia_disagrees | high_pm | exporter | `strong` (with `implausible_pm`, `gaia_pm_disagrees`) | `pm_arcsec_yr`, `fastest_star_arcsec_yr` |
| parallax_leak, few_dots, saturated | high_pm | exporter | `warn`. `parallax_leak`: a fit without parallax (`fit_mode` `pm` or `pm_leak`) | |
| one_detector_shift | high_pm | exporter | `warn`: a pair whose two sources share ≤ 1 detector, so their brightness match rests on one detector or none | |
| blend | high_pm, spectral, shape | exporter | Two or more Gaia stars within 8″ (`fragments`). high_pm: `strong` for a new motion, `warn` on a Gaia star whose motion we confirm; spectral `warn`; shape `strong` | |
| catalogues_checked | high_pm | catalogue | No Gaia match only. `strong`: a catalogue entry where its motion puts it at that catalogue's epoch (a catalogued star, not new), or one at today's place when the motion should have moved it ≥ 4″ (a still star: the motion is likely fake). `warn`: neither | `pm_mas_yr`; with an entry its `catalogue`, `sep_arcsec`, magnitudes (as `catalogue_star`) and `epoch_year` or `expected_shift_arcsec` |
| catalogues_checked | spectral | exporter, catalogue | exporter: `ok` when the zone's refcat holds Gaia, CatWISE2020 and 2MASS (the marker, contracts/dots.md refcat), else `warn` naming the missing ones. catalogue (IRSA, within 3″; XSC wider): `strong` when an ordinary catalogued galaxy or star explains the label (2MASS XSC, AllWISE extended, a Gaia galaxy candidate, galaxy or quasar WISE colours, a Gaia parallax star beyond 50 pc or too luminous for its parallax, catalogue colours failing our ice rule's reddening test); `ok` when the colours agree (L/T colours, an ultracool absolute G, a star reddened behind a cloud); `warn` otherwise (also nothing catalogued, or several sources blended in one SPHEREx pixel) | exporter `catalogs`, `missing`; catalogue `<cat>_<key>` for its entry (`gaia_`, `catwise_`, `allwise_`, `tmass_` + `sep_arcsec`, `g_mag`, `bp_rp`, `w1_mag`, `w2_mag`, `w3_mag`, `j_mag`, `h_mag`, `ks_mag`), `xsc_sep_arcsec`, colours `w1_w2`, `w2_w3`, `j_ks`, `j_h`, `h_ks`, `label_group`, with a parallax `gaia_parallax_mas`, `gaia_parallax_error_mas`, `distance_pc`, `abs_g`; a blend `blend_n`, `blend_catalogue`; nothing catalogued `ab_det` |
| no_j | spectral | catalogue | `brown_dwarf_like` / `ch4`: 2MASS J (or its limit) against our 4.6 µm magnitude: bluer than an ordinary star's J − [4.6] `strong`, between that and an L0's `warn`, else `ok`; no 2MASS source: `ok` when 2MASS would have seen an L dwarf, `warn` when too shallow | `j_mag`, `j_minus_m46`, `sep_arcsec` |
| pah_only, behind_cloud, few_channels | spectral | exporter | `warn`. `few_channels`: under 51 of the 102 channels measured | `sigma`; `ice_neighbours`; `channels`, `of` |
| bright_neighbour | spectral, shape | exporter, catalogue | `warn`: a bright star near it (exporter: `fragments`' bright star within 120″; catalogue: a star 3 mag brighter within 30″, or a saturating star, G < 10 or Ks < 8, within 90″) | `band`, `mag`, `sep_arcsec`; catalogue: the star's `catalogue`, `sep_arcsec`, magnitudes, `neighbour_mag`, `neighbour_band` |
| galaxy | shape | exporter, catalogue | exporter `warn`: our classifier calls it extended. catalogue: 2MASS XSC within 10″ `strong`, WISE extended or a Gaia galaxy candidate `warn`, else `ok` | |
| status_conflict | any | merge | `warn`: two producers disagree (known vs flaw, or two names) | `names`; or `known_name`, `flaw` |

Code clashes to know (v3.24): `hot_pixel` means the vetting gate (exporter) or a pixel measurement; a producer's
`hot_pixel` or `sharp` replaces the exporter's gate check of the same code, so a grade C gate can show as the pixel
measurement's level. `known_nearby` `rate_arcsec_h` and `pa_deg` are the body's in the `known` producer's values and
ours in the suspects' (the body's there: `known_rate_arcsec_h`, `known_pa_deg`). 2MASS Ks is `ks_mag` in the
`catalogue` producer's values and `tmass_k_mag` in the suspects'. Same-pixel hot pixels are `hot_pixel_proven` from the exporter and `hot_pixel` `strong` with status
`flaw` from the pixel producer: either way the candidate is a `flaw`.

**Side files** (v3.24). A check producer writes `<dir>/<source>.json`:
`{ source, made (ISO 8601 UTC), candidates_file, candidates:{ <id>:{ checks:[…], status:"known"|"flaw"|null,
known_name|null } } }`, only the ids it measured, `status` only when certain by the `status` rule. The exporter merges
every `*.json` in the folder: `skyshift-candidates --checks-dir DIR` during the export, or `--merge-only IN.json
--checks-dir DIR --out OUT.json` into an existing file (reads no zone). Per candidate: a producer's check replaces the
exporter's of the same code (between producers the stronger level wins, then the first source by name); status known >
flaw > open, and a `strong` check whose code ends in `_proven` makes it `flaw` unless a producer says `known`; a known
vs flaw disagreement, or two names, adds `status_conflict` (`warn`); then the score is re-weighed by the checks' factor
and the list re-sorted. Ids change when the list changes: the producers run again on the final export. A side file of
ids not in the file is skipped (counted). v3.24: a side file may name ids of candidates.json or of its `more` files;
the merge covers both (`--merge-only` reads the `more` files next to IN.json by their `url` and writes them next to
OUT.json), then splits them again (below).

**Less likely movers** (v3.24, `more`). The v3.24 suspects keep 10–40× more grade C movers (about 9,000 more in hunt,
ecl04, ecl08 and ecl11; ~100,000 expected over all zones), too many for one file (candidates.json was 6.9 MB on 16
Nov). They are listed apart, never deleted:
- candidates.json holds the grade A and B movers, every other kind, and every candidate whose `status` is `known` or
  `flaw` (grade C ones too).
- Each zone's grade C movers with `status` `open` are in `/data/candidates-more/<zone>.json`
  (web/public/data/candidates-more/), `{ generated, data_release, zone, kind:"mover", grade:"C", count,
  candidates:[…] }`: the same candidate objects as candidates.json (every field: `score`, `reasons`, `status`,
  `known_name`, `checks`, …), sorted by `score` descending; `generated` and `data_release` as candidates.json's;
  `frames_url` null (static frames go to candidates.json's best). A zone with none has no file and no `more` entry.
- An id is in exactly one file. The zones' dedup (Which objects: movers within 6″ and 2 minutes) runs over every mover
  before the split.
- The split is made after the checks are merged: an entry that becomes `known` or `flaw` moves into candidates.json;
  then `counts` and `more[].count` are counted again.
- Producers: `known` and `catalogue` may run on the `more` files; `pixel` runs on grade A and B movers only (about 4 MB
  of photo reads per mover), so a `more` entry has no pixel check: absent means not measured, not passed.
- The site loads a zone's file only when someone opens "Less likely". Firestore `votes.candidate_id` may be an id of
  either file. A reader that ignores `more` sees candidates.json alone (before v3.24 these movers were not listed at
  all).

**Static frames** (`/data/cand/<id>/`, scripts/candidates/frames.py) exist for the highest-scoring of
each kind (default 25 movers, 20 high_pm, 15 spectral; under 150 MB in all). mover: hours clock, 20′
field, `det` (and its simultaneous partner for `two_detectors`), the exposures within 0.3 days of `mjd`
(its own session and the next ones), at most 10 per detector, nearest in time first. high_pm and
spectral: months clock, 5′ field, `det`, every exposure of every visit (as the featured targets).
Several detectors may share one file (sorted by `mjd_mid`, then `det`); the client filters by `det`.

**Which objects.** mover: every row of `results/<zone>/suspects.parquet` (suspects.json's rules), except (v3.18)
a mover near a planet at its time (the `planets` veto, applied by the exporter too for tables written before it);
v3.24: grade C movers with `status` `open` go to the zone's `more` file. high_pm:
the `vetted` rows of `highpm_candidates.parquet` (from 14 Nov evening; before, every row). Unvetted rows are
fakes or not movers: a Gaia or CatWISE motion that isn't ours, chance-level pairs, too few sessions, nothing to
check against, or a known slow Gaia star (contracts/dots.md, highpm_candidates).
spectral: sources of `classes.parquet` with a band label or `brown_dwarf_like`, and either no Gaia match or
a Gaia parallax ≥ 20 mas at ≥ 5σ (closer than 50 pc); the 300 highest-scoring per zone. From v3.10 the exporter
first cleans the labels: `pah_33` only from photoz's PAH at the galaxy's redshift (`label_pah_33_z`) or a good
photo-z below 0.02; no ice label on a galaxy with a good photo-z; no `h2o_vapour` or `co_23` on a Gaia star with
BP − RP ≥ 1.8 (an M dwarf, where they are expected). A Gaia DR3 star (G < 20) within 6″ at the source's epoch
counts as a Gaia match (a blend), so `no_gaia` means none within 6″. At most 20 rows labelled `pah_33` alone. An object already
listed from an earlier zone is not listed again, and the earlier entry's `zones` gets the later zone: movers
within 6″ and 2 minutes (suspects.json's merge), high_pm and spectral within 4″. The pilot lies inside the
hunt zone, which was run on the same photos: the hunt re-found all 27 pilot movers (the same ids as
suspects.json), so the pilot adds only objects the hunt didn't list.
shape (v3.7): every row of `results/<zone>/shape_candidates.parquet`, written by
`python -m skyshift_pipeline.shape` (pipeline/src/skyshift_pipeline/shape.py; its report `shape_report.json`
next to it); a zone without the file has none. A fixed source is listed when its dots, compared with single
Gaia stars in the same photo and region of the detector, are stretched along one axis on the sky at ≥ 20σ
(the short- and long-wavelength detectors along the same axis: `steady_pa`, or one visit only: `one_visit`),
Gaia DR3 (G < 20), CatWISE2020 and 2MASS list exactly one object within 15″ (any other entry there explains the
stretch; 2MASS at its own epoch) and no entry within 35″ less than 2.5 mag fainter than it (the moments'
background annulus reaches 34″), the catalogued neighbours within 20″, weighted by flux ratio and the share of
their light inside the moments' window, predict along its axis less than a quarter of the stretch (v3.10), it is
not saturated, not within 60″ of a G < 9 star, not flagged as an artefact on ≥ 25% of its dots, and it is not a
galaxy: not classify's galaxy, no good photo-z, and with no Gaia star not extended (v3.10); at most the 150 most significant per zone. Within 4″ deduped like high_pm and
spectral. shape_report.json has the rule and its checks. Hunt (14 Nov, 38% of the photos): isolated single
Gaia stars fire at 0.17% (10 of 5,803), Gaia pairs 3–10″ apart at 21% (383 of 1,809), along the pair's axis
to 2° (median); 49 listed, of which about 23 would be expected from the single stars' rate. Single stars
that fire mostly have faint uncatalogued neighbours (at 6σ: 4% of them, and 77% of the pairs), so a shape
candidate is a lead for a human, not a detection. From v3.12 the pipeline fits each listed source's pixels with one ePSF and with two (contracts/dots.md `shape_candidates`): where two fit clearly better, `why` ends with e.g. "Fit: a two-object fit puts a second object 12″ south (PA 183°) of it, 1.7 mag fainter at 0.93 µm and 1.5 mag fainter at 4.7 µm, not in Gaia, CatWISE or 2MASS, and it stays put between visits (two objects fit better than one by Δχ² 37,613)." ("catalogued there as 2MASS K 13.4" when a catalogue entry sits within 3″ of it; "a second object next to it that moved 17″ between visits (something passing by?)" when it moves); where it doesn't, "Fit: two objects don't fit clearly better than one here." No field changes.

Nothing is dropped for being doubtful (except high_pm, above): doubts become `reasons` and lower the `score`. The "check first"
codes (show them as cautions): `known_object_near`, `motion_not_significant`, `bad_pixels_near`,
`detector_excess`, `repeat_position`, `gaia_unchecked`, `catwise_unchecked` and the vetting gates (mover; v3.24:
`outlier_flag` is no longer one, OUTLIER at a dot's peak is a sign it moved);
`short_span`, `few_dots`, `bad_fit`, `unsteady`, `over_pm_cap`, `gaia_pm_disagrees`, `catwise_pm_disagrees`,
`at_control_level`, `implausible_pm` (high_pm); `one_visit`, `class_galaxy` (shape); `blend` (high_pm, spectral,
shape; v3.11). From v3.11 a high_pm, spectral or shape candidate whose source (either side of a pair) the pipeline's
`fragments` step tagged `bright_fragment` is not listed at all: it is a piece of a saturated star's ring.

**score** (0–1; the formulas are in the exporter and its tests):

| kind | score |
|---|---|
| mover | v3.24: (1 − e^(−snr/15)) × evidence (`two_exposures` 1, `two_detectors` 0.6) × known term (1 with no known object within 600″, else 0.5 rising linearly to 1 between 30″ and 300″) × 0.1 with `grade` C × the checks' factor (below). Before v3.24 also × 0.85 with only two dots, × 0.6 with `outlier_flag`, × 0.7 with another record-only flag, × 0.8 with a masked pixel in a dot's 3×3, × 0.85 when the motion is not significant (the dots' pos_err × 1.6), × 0.6 with `detector_excess`, × 0.3 with `repeat_position`, × 0.5 with a dot within 6 px of the detector edge: these are checks now |
| high_pm | e^(−s/0.2) × the vetting reasons' weights × 0.6 for a Gaia star (`status` gaia). s is the pipeline's own score, lower = better: 1/(pm/pm_err) for a fit, \|ln flux ratio\| + spectrum-shape rms for a pair. Weights: `short_span`, `few_dots`, `bad_fit` 0.5; `gaia_pm_disagrees`, `catwise_pm_disagrees` 0.4; `over_pm_cap`, `at_control_level`, `unsteady` (v3.22) 0.3; `implausible_pm` 0.01 (so these sort last). The 0.6 also applies to `status` 2mass (highpm's deep check). v3.24: × the checks' factor |
| spectral | max over its labels of weight × (1 − e^(−σ/10)), σ = the band's significance. Weights: `brown_dwarf_like`, `ch4` 1; `h2o_ice`, `co2_ice`, `co_ice` 0.8; `h2o_vapour` 0.6; `co_23` 0.4; `pah_33` 0.3. v3.24: × the checks' factor |
| shape | (1 − e^(−s/15)) × 0.8 with `one_visit`; s = significance of the stretch (\|mean excess ellipticity\| / its error; Rayleigh, P(> s) = e^(−s²/2) for noise). v3.24: × the checks' factor |

**The checks' factor** (v3.24, every kind): × 0.3 per `strong` check, × 0.95 per `warn`, × 1 per `ok`, so checks rank and
never hide (two strong clues, × 0.09, rank a mover with grade C's × 0.1). A check the kind's own formula already counts
adds nothing: mover `known_nearby` (the known term) and, on a grade C mover, the checks of its own failed gates
(`hot_pixel`, `bad_column`, `edge`, `masked_peak`, `sharp` for `sharp_outlier`/`very_sharp`, `flux_jump` for
`flux_ratio`, `red_photo`, `not_vetted`, `catalogue_star` for `gaia`/`catwise`/`2mass`/`allwise`, `flag_bloom`,
`flag_halo`, `bright_star` for `bloom`, `halo`, `bright_star`); high_pm `impossible_motion` with `implausible_pm`,
`few_dots` with `few_dots`, `gaia_disagrees` with `gaia_pm_disagrees`. Why the old mover factors went (16 Nov, hunt +
ecl04 + ecl08 + ecl11: 4,646 movers on tracks, nearly all known asteroids, against the 480 listed suspects): OUTLIER
marks a dot's peak on 68% of known asteroids and 28% of the list (a sign it moved, not a doubt); a masked pixel in a
3×3 is as common on known asteroids (23–29%) as on the list (16–35%); `detector_excess` is a zone-level rate, nothing
about the object. Comet 306P/LINEAR's two grade A movers (ecl04): 0.174 → 0.414 and 0.084 → 0.234. A side-file merge
re-weighs the score by the change in the factor and re-sorts the list. Grade order is unchanged. Scores in older
files keep their old factors: read them as written.

**labels**

| label | kinds | meaning |
|---|---|---|
| red | high_pm, spectral | Redder than J − [4.6] = −0.5 AB (about J − W2 > 1.9 Vega; classify's brown-dwarf colour cut). high_pm: the pipeline's D6/D2 flux ratio `red_ratio` ≥ 0.63; spectral: `j_m46_ab` > −0.5 |
| brown_dwarf_like | high_pm, spectral | classify's `brown_dwarf_like`: methane or water seen, red, missing or very faint in Gaia. A lead, not a discovery |
| ch4 | high_pm, spectral | Methane at 3.3 or 1.6 µm (T and Y brown dwarfs) |
| h2o_ice, co2_ice, co_ice | high_pm, spectral | Ices at 3.05, 4.27, 4.67 µm: a dark cloud in front of a background star |
| h2o_vapour | high_pm, spectral | Water vapour at 1.4 / 1.9 µm (M and L dwarfs, cool giants) |
| co_23 | high_pm, spectral | The CO bandhead at 2.3 µm (cool giants and dwarfs) |
| pah_33 | high_pm, spectral | PAH emission at 3.3 µm (star-forming galaxies) |

The molecule labels are classify's `label_*` (pipeline/CLAUDE.md, "Spectral science"); on a high_pm
candidate they are those of its (first) source.

**reasons**

| code | kind | plain meaning |
|---|---|---|
| two_exposures | mover | Seen at different places in two or more exposures of one session |
| two_detectors | mover | Seen by two detectors at the same moment (D1+D4, D2+D5, D3+D6), in one exposure only |
| known_object_near | mover | A known asteroid, comet, planet or moon was predicted within 2′ at that time: it may be that object, slightly off its prediction |
| motion_not_significant | mover | The motion is under 3σ of its position errors: it may not move at all |
| bad_pixels_near | mover | A masked pixel (cosmic ray, hot or dead pixel, persistence…) touches one of its dots |
| outlier_flag | mover | The OUTLIER flag (bit 19, recorded, not masked) is set on its pixels. Hunt: 92 of 228 suspects (40%), almost all in D4–D6. v3.24: informational, not a caution and no score factor: OUTLIER marks a dot's peak on 68% of known asteroids (check `flag_outlier`, `ok`) |
| detector_excess | mover | Seen only in detectors where, in that visit, the zone has at least 1.5× its usual share of unexplained movers per mover (≥ 5 suspects in the cell): likely artefacts. Hunt: D4, D5, D6 in visit 2 (D4 is 41% of the suspects but 28% of the movers); 108 suspects. A D1+D4 pair seen by D1 too doesn't count. v3.24: no score factor (check `detector_excess`, `warn`) |
| repeat_position | mover | Another unexplained mover of the zone was seen within 30″ in another session, at under 2″ an hour apart: probably a fixed or very slow source, not a fast mover. Hunt: 7 suspects at 3 places, 1 to 169 days apart (one is the brightest suspect, S/N 290 in D1). v3.24: its × 0.3 comes from the check `repeat_position` (`strong`) |
| gaia_unchecked, catwise_unchecked | mover | Gaia / CatWISE could not be checked when the pipeline ran |
| not_vetted, hot_pixel, bad_column, edge, masked_peak, sharp_outlier, very_sharp, flux_ratio, red_photo, 2mass, allwise | mover | The suspects' failed vetting gates (suspects.json `reasons`, meanings there): the mover is grade C, most likely an artefact or a catalogued source |
| pm_fit | high_pm | Motion fitted over all its detections in every visit |
| pm_pair | high_pm | Two sources each seen in one visit only, paired across visits (too fast to stay linked) |
| vetted | high_pm | Passed every pipeline check |
| catwise_pm_agrees | high_pm | CatWISE lists the same motion |
| parallax_fit | high_pm | v3.22. The motion was fitted with the star's parallax taken out: Gaia's parallax (its `gaia_match`), or our own from visits in both seasons. Without it, two visits half a year apart can show a nearby star's parallax as extra motion |
| short_span | high_pm | Its visits are too close in time for a motion |
| few_dots | high_pm | Few detections (fewer than 20 dots) |
| bad_fit | high_pm | The positions don't fit a straight line (blend or bad fit) |
| unsteady | high_pm | v3.22. Its visits go out and back instead of along a line (needs three or more visits): a measurement problem (blend, detector position, saturation), not a motion. Such rows are never `vetted`, so in practice not exported |
| over_pm_cap | high_pm | Faster than any known star (> 10.5″/yr) |
| gaia_pm_disagrees | high_pm | Gaia lists a different motion for the star here: likely a mismatch |
| catwise_pm_disagrees | high_pm | CatWISE lists a different motion for the source here: likely a mismatch |
| at_control_level | high_pm | As many pairs at this separation turn up by chance (the control) |
| few_sessions | high_pm | Seen in fewer than 3 sessions in one of its visits (pipeline row only: not exported) |
| unchecked | high_pm | No Gaia or CatWISE motion to check it against, and under 10σ or saturated (pipeline row only: not exported) |
| gaia_slow | high_pm | Gaia agrees, but predicts under 150 mas/yr: a known slow star (pipeline row only: not exported) |
| implausible_pm | high_pm | Faster than 12″/yr between visits: no star moves that fast (Barnard's Star, the fastest known, does 10.4″/yr), so it is almost certainly two different sources. In practice every pair (3–120″ apart about half a year apart; hunt 32–269″/yr) and the fits above 12″/yr; none is `vetted`. Hunt: 82 of 997 (55 pairs, 27 fits); pilot: 15 of 167 (12 pairs, 3 fits) |
| no_gaia | spectral | No Gaia DR3 source here (from v3.10: none with G < 20 within 6″) |
| gaia_nearby | spectral | Gaia has it, with a parallax of 20 mas or more: closer than 50 pc |
| class_star, class_galaxy, class_unsure | spectral | classify's `class` (unsure: point-like without a Gaia star) |
| variable | spectral | Its brightness changes between visits (sources `variable`) |
| elongated | shape | Its image is more stretched than single Gaia stars of the same S/N in the same photo and region of the detector (always set) |
| steady_pa | shape | The short-wavelength (D1–D3) and long-wavelength (D4–D6) detectors each show the stretch along the same axis on the sky (within 30°): two arrays with their own optics agree, which a PSF or detector effect would not (v3.10; before: two visits agreed) |
| one_visit | shape | Only one visit shows the stretch; the others don't: something may have passed by, or that visit is noisier |
| one_catalogue_source | shape | Gaia DR3 and CatWISE2020 list a single object within 15″ (always set): a second object would be new |
| class_galaxy | shape | (also spectral) classify calls it a galaxy; galaxies are extended by nature. Not listed by the current rule; kept in the vocabulary |
| blend | high_pm, spectral, shape | v3.11. Two or more Gaia DR3 stars (G < 18) within 8″ of its source (the pipeline's `fragments` step): the detection may be two stars merged by the 7.5″ PSF, so a shift, colour or stretch may come from the pair. A high_pm row with a blend on either side also counts as a Gaia star (`status` gaia) |

**why**, examples (from the hunt export): "Seen at different places in 2 exposures 4.3 minutes apart,
moving about 55″ an hour; no known asteroid, comet, planet or moon was predicted within 10′." · "Seen at different
places in 3 exposures over 4.3 minutes, moving about 55″ an hour; the nearest known asteroid, comet, planet or moon was
predicted 2.1′ away, but another unexplained mover was seen 3″ from here 168.6 days apart, so it may be a
fixed source." · "Moves 1.10″ a year between visits; Gaia lists this star with the same motion, a known
nearby star that shows the method works." · "Moves 0.95″ a year between visits, but CatWISE lists a
different motion for the source here — likely a mismatch, worth a look." · "Seen 78.4″ apart in two visits
(171″ a year), with matching brightness, but no star moves that fast (Barnard's Star, the fastest known,
does 10.4″ a year): almost certainly two different sources." · "Water vapour at 1.4/1.9 µm and CO at
2.3 µm (23σ) in a point source with no Gaia star, and red: what a brown dwarf would look like, a lead to
check." · shape: "Looks like two objects here but the catalogues list one: the image is stretched along PA 37°
(8.0σ over 40 detections), the same way in both visits; worth a look."

### distant.json (v3.6)

The distant-planet search, for the "How we know it works" page (`#/results`). A planet 70–1000 AU away drifts only
0.5–0.002″/h inside a visit, so the pipeline links it into a fixed source seen in **one visit only**; six months later
it sits 390″ (1000 AU) to 4,800″ (70 AU) away along the ecliptic. The pipeline step `orphans` (pipeline/CLAUDE.md,
"Distant planets") lists such sources without a catalogue entry ("orphans"), pairs those of two visits when one
distance explains both positions and both measured drifts, and repeats the pairing on shifted copies to count chance
pairs. Written by scripts/validate (P1) from the step's `results/<zone>/distant.json` and the table-level injection
(scripts/validate/distant_injection.py). One zone, the headline zone (`hunt`). A missing file means not published:
the page leaves the section out or says it is pending.

```
{ generated, data_release, zone, zone_box:{ra:[min,max], dec:[min,max]}|null, run_created,
  searched:{ distance_au:[min,max], sep_arcsec:[min,max], rate_arcsec_h:[min,max], pair_min_p },
  counts:{ single_visit_sources, uncatalogued, covered, orphans,
           dropped:{ bright_fragment, zone_edge, saturated, blend? }, drift_ok, pairs_tested, pairs,
           known_orphans, open_orphans },
  pairs:[{ source_id_a, source_id_b, visit_a, visit_b, ra_a, dec_a, mjd_a, ra_b, dec_b, mjd_b,
           sep_arcsec, dlon_arcsec, dlat_arcsec, distance_au, distance_lo_au, distance_hi_au,
           flux_ratio, n_common_det, n_dots_a, n_dots_b, snr_a, snr_b, p, known_a|null, known_b|null }],
  control:{ n, passing_per_control:[int…], expected_pairs, spread_pairs, poisson_p, beyond_chance, method },
  injection:{ method, distances:[{ distance_au, injected, recovered, recovered_frac,
                                   distance_fit_median_au|null }],
              recovered_frac, min_cell_recovered_frac, distance_err_median_frac } | null,
  limits:{ depth_ab_snr5:{ "D1".."D6": AB mag }, reach_au:{ <planet>_p<albedo>: AU }, notes:[string] } }
```

| field | type | meaning |
|---|---|---|
| generated | string | Time the site file was written, ISO 8601 UTC |
| data_release | string | Collections searched, as validation.json |
| zone | string | Pipeline zone searched (`hunt`) |
| zone_box | object, deg, or null | The zone's box (pipeline config), as `measured.zone_box`; null when unknown |
| run_created | string | Time the `orphans` step ran (its `created`), ISO 8601 UTC |
| searched.distance_au | [float, float], AU | Distances the search is built for: 70–1000 |
| searched.sep_arcsec | [float, float], arcsec | Separations between the two visits' sources that were paired: 300–7,200 (a retrograde orbit at 70 AU lands 6,900″ away) |
| searched.rate_arcsec_h | [float, float], ″/h | Drift inside one visit accepted for an orphan: 0–1 (faster movers are the suspects' route) |
| searched.pair_min_p | float | A pair passes when the χ² of its drifts and fluxes has p ≥ this (0.01) |
| counts.single_visit_sources | int | Linked sources seen in one visit only |
| counts.uncatalogued | int | Of those, with no Gaia DR3, CatWISE2020 or 2MASS entry within the cross-match radius |
| counts.covered | int | Of those, whose spot the other visit photographed (≥ 6 photos), so the absence is real |
| counts.orphans | int | Of those, kept after the drops below |
| counts.dropped | {reason: int} | Covered sources dropped, per reason: `bright_fragment` (a source with S/N ≥ 100 within 15″, or (v3.11) tagged `bright_fragment` by the pipeline's `fragments` step: a piece of a bright star), `zone_edge` (within 3′ of the zone's edge), `saturated`, `blend` (v3.11, `fragments` step: two or more Gaia stars within 8″; older files lack it: read as 0). A source can have several reasons, so these may add up to more than `covered − orphans` |
| counts.drift_ok | int | Orphans whose drift inside their visit was measured (≥ 3 dots over ≥ 6 h, rate error ≤ 0.1″/h) and fits `searched.rate_arcsec_h`: the ones paired |
| counts.pairs_tested | int | Pairs of `drift_ok` orphans of two visits ≥ 60 days apart, `searched.sep_arcsec` apart, mostly along the ecliptic |
| counts.pairs | int | Pairs that passed: one distance and a bound orbit explain both positions and both drifts, and the brightness agrees in a shared detector. Listed in `pairs` (at most 300) |
| counts.known_orphans | int | Orphans matched to a known slow body (MPC orbits): a recovered distant object, not dropped |
| counts.open_orphans | int | `drift_ok` orphans whose partner could lie outside the zone (part of their 70–1000 AU track leaves the zone): not fully searched; no cross-zone pass yet |
| pairs[] | object | One passing pair, highest `p` first. `_a` is the earlier visit's source, `_b` the later one's |
| pairs[].source_id_a, source_id_b | int | The pipeline's linked sources (contracts/dots.md `source_id`, below 2⁵³) |
| pairs[].visit_a, visit_b | int | Visit numbers |
| pairs[].ra_a, dec_a, ra_b, dec_b | float, deg | Fitted positions at `mjd_a`, `mjd_b` (raw dots, no calib) |
| pairs[].mjd_a, mjd_b | float, MJD | Weighted mean time of each source's dots |
| pairs[].sep_arcsec, dlon_arcsec, dlat_arcsec | float, arcsec | B − A: total, along ecliptic longitude, along latitude |
| pairs[].distance_au | float, AU | The implied distance from the Sun (best on the model's grid) |
| pairs[].distance_lo_au, distance_hi_au | float, AU | Its range (χ² within 4 of the best) |
| pairs[].flux_ratio | float | Flux B / A, geometric mean over the detectors measured in both |
| pairs[].n_common_det | int | Detectors measured in both visits |
| pairs[].n_dots_a, n_dots_b | int | Dots in each drift fit |
| pairs[].snr_a, snr_b | float or null | Each source's highest dot S/N |
| pairs[].p | float | χ² probability of the pair (drifts + fluxes) |
| pairs[].known_a, known_b | string or null | MPC name of a known body matched to that side, else null |
| control.n | int | Control runs: the later visit's orphans shifted together by 25–75% of the box in RA and Dec, then paired the same way (no real pair survives a shift) |
| control.passing_per_control | [int] | Passing pairs in each control run |
| control.expected_pairs | float | Their mean: the number of passing pairs chance alone gives |
| control.spread_pairs | float | Their standard deviation |
| control.poisson_p | float or null | Probability of `counts.pairs` or more passing pairs from chance alone (Poisson, mean (Σ + 1)/n over the controls); 1 when no pair passed; null without controls |
| control.beyond_chance | bool | true when `poisson_p` < 0.01: more passing pairs than chance explains. false: nothing beyond chance |
| control.method | string | One sentence |
| injection | object or null | How often the pairing finds a planet that both visits saw as an orphan. null: not run |
| injection.method | string | One sentence (table-level: synthetic planets at the zone's real cadence and the fit's error model, added to the real orphans, paired by the step's code; detection, linking and catalogue cuts are not tested) |
| injection.distances[] | object | Per injected distance, every orbit direction and dots-per-visit setting together: `distance_au` (AU), `injected`, `recovered` (its two halves formed a passing pair), `recovered_frac` (0–1), `distance_fit_median_au` (median implied distance of the recovered, AU; null if none) |
| injection.recovered_frac | float, 0–1 | Recovered share of every injected planet together |
| injection.min_cell_recovered_frac | float, 0–1 | Lowest recovered share of a single (distance, dots per visit) setting: the worst case |
| injection.distance_err_median_frac | float | Median \|implied / true distance − 1\| of the recovered planets |
| limits.depth_ab_snr5 | {"D<det>": float}, AB mag | Single-exposure depth: median AB magnitude of the orphans' own dots at S/N 5–6, per detector with enough dots (no stacking) |
| limits.reach_au | {key: float}, AU | Farthest distance at which a planet is as bright as `depth_ab_snr5.D2`. Keys `<planet>_p<albedo>`: `neptune` (3.88 Earth radii), `super_earth_2re` (2), `earth` (1), geometric albedo 0.1 or 0.4 (V = H + 5 log₁₀(r·Δ) at quadrature, J_AB ≈ V − 0.3) |
| limits.notes | [string] | The step's own limits, as it wrote them (technical) |

The page shows what was searched, the result against chance (`counts.pairs` vs `control.expected_pairs` ±
`spread_pairs`, `poisson_p`), the injection by distance and the limits. A passing pair at chance level is not a
candidate: say so (`beyond_chance` false).

## Science workspace (v3.8, supplemental)

The workspace's Analyze and Research views (web/src/product, web/src/card/science.ts). Experimental and
**not part of the scored pipeline**: nothing here feeds candidates.json, suspects.json, validation.json or
the votes. Results are labelled unvetted; a detectable model is not a discovery or a Planet X exclusion.
Units as in Conventions, except proper motions here in **arcsec/yr** (fields say so: `pm…_arcsec_yr`,
`target_motion` and `astrometry.fit` in arcsec/yr, `units` given). Errors use the usual error JSON.

**Static first.** Five featured fields have versioned products in `/science-data/<slug>/` (in the repo
`web/public/science-data/`; slugs `barnard`, `epsindib`, `wise0855`, `rwaur`, `rdra`). The site reads them
before the API; the API serves the same files when a request is within 2″ of a field's `center`, and
copies them into its image. Elsewhere the analysis endpoints answer 404 unless a local job built a cube.

| file | shape |
|---|---|
| `frames.json` + `frames/d<det>-v<visit>.bin` | `GET /api/frames`, months 5′ (146 px, 2.05″/px), all six detectors in one file. `um_min`, `um_max`, `flag_frac`, `sat_frac` are null (the analysis cube keeps no FLAGS or WCS-WAVE) |
| `model.json`, `resid.json`, `refdiff.json` + `views/<view>/frames/d<det>-v<visit>.bin` | The same grid for the derived layers: static-sky model, observation − model, observation − DR1 all-sky reference at each frame's wavelength. MJy/sr. Pixels outside the analysis footprint (4′) are raw 0 |
| `science.json` | `GET /api/science` |
| `analysis.json` | `GET /api/analysis` for one detector (its `detector`: 6 for wise0855, else 2) |
| `object.json` | `GET /api/object` from the analysis cube (`measure` null) |

### GET /api/frames?view=

`model`, `resid`, `refdiff`: only a published field, `clock=months`, `fov=5`; otherwise 404 `not_found`
(no relabelled grids, no other fields, clocks or sizes). `raw` (default) is the usual engine.

### GET /api/science?ra=&dec=

`{ version, source:{ kind, coverage_arcmin, n_frames, available_exposures|null, flags_complete, limitations[] },
target_motion:{ method, pmra, pmdec, epmra, epmdec, p_false? }|null, astrometry:{ points[], fit, … },
lightcurve:[{ mjd, dmag, lam, window }], bands:[{ visit, mjd, W1|null, W2|null }],
reference:{ spectrum:[{ um, flux_ujy }], error|null, source }, sensitivity:{ limits:[{ um, flux_ujy_5sigma,
ab_mag_5sigma, n_frames }], rmin[], p9[], wise0855_twin_reach_pc, note }, frames:[{ mjd, det, um, visit,
quality, flags }] }`. `W1`/`W2`: `{ flux_ujy, err_ujy, vega_mag, n }`, synthesised from the SPHEREx spectrum
of the visit. `lightcurve` is experimental. 404 off a published field without a local cube.

### GET /api/analysis?ra=&dec=&det=1..6 (default 6)

`{ version, detector, n_slides, source:{ kind, n_frames, n_visits, coverage_arcmin, native_pixel_arcsec,
flags_complete, … }, warnings:[string], slow_movers[], fast_movers[], cold_candidates[] }`. Every
candidate has `ra`, `dec`, `status` (`unvetted`, `single_visit`, `persistent`, `artifact_warning`) and
`p_false` (an empirical time-slide false-alarm rate over `n_slides` backgrounds, **not** a probability that
the object is false; null for cold candidates: uncalibrated). Slow movers add `pm_arcsec_yr`,
`pmra_arcsec_yr`, `pmdec_arcsec_yr`, `mjd_ref`, `n_visits`; fast movers `speed_arcsec_day`. Pixel fields
(`x`, `y`, …) are on the analysis grid. A detector without a published analysis: 404.

The analysis grid (v3.24, written down from the published files; no change): native 6.15″ pixels, +x west and +y
north, so `pmra_arcsec_yr` = −`pm_x` × 6.15 and `pmdec_arcsec_yr` = `pm_y` × 6.15 (`pm_x`, `pm_y` in px/yr), and a
pixel (x, y) lies (−(x − x₀) × 6.15″ east, (y − y₀) × 6.15″ north) of the candidate's `ra`, `dec` at its (x₀, y₀).
Slow movers: `x`, `y` at `mjd_ref` and `points[{visit, x, y}]`, one per visit (a visit's time: the middle of its
detector's first and last exposure in `science.json` `frames`). Fast movers: `x0`, `y0` at `t0` (MJD), `vx`, `vy` in
px/day (`speed_arcsec_day` = √(vx² + vy²) × 6.15), `detections[{x, y, t, snr, partner_t?, partner_snr?}]`. Cold
candidates: `x`, `y` and `detections[{x, y, t, visit, snr_long, snr_short, flags}]`. `t` (and `partner_t`) index
`science.json` `frames[]`: the detection's time is `frames[t].mjd`. The Signals list (web/src/product/vetting.ts)
reads these to match each signal against Gaia, CatWISE and MPC orbits at its own times.

### GET /api/science/capabilities

`{ analysis_jobs: bool }`, `Cache-Control: no-store`. False unless `SKYSHIFT_ENABLE_SCIENCE_JOBS=1`.

### GET /api/enrichment?ra=&dec=&kind=

On-demand catalogue checks of a published field (or a local cube). `kind`:

| kind | source | answer |
|---|---|---|
| `catwise` | NASA/IPAC IRSA TAP, CatWISE2020 | `{ source, sources:[{ name, ra, dec, w1mpro, w2mpro, pmra, pmdec, candidate?, separation_arcsec?, pm_difference_arcsec_yr? }], note }`; CatWISE `pmra`, `pmdec` in arcsec/yr. **Proposal** (v3.24, P2): `epoch_mjd` 57170.0, CatWISE2020's position epoch, so readers don't hard-code it; not sent yet: read absent as 57170.0 |
| `relay` | NASA/IPAC IRSA TAP, NEOWISE single exposures | `{ source, points:[{ mjd, W1?, W1_err?, W2?, W2_err?, n?, source }], error:null }`, Vega mag, NEOWISE visits then SPHEREx synthetic |
| `comovers` | ESA Gaia archive TAP, DR3 | `{ source, candidates:[ Gaia rows + pm_chi, sep_arcsec, distance_pc, projected_sep_au ], luminosity|null }`; without a target motion `{ source, candidates:[], note }` |
| `asteroids` | Minor Planet Center orbits with kete (as `/api/known`) | `{ source, objects:[{ name, ra, dec, v, visit, mjd }], errors:[{ visit, mjd, error }], note }`, one prediction per observing session. MPC asteroids and comets only: planets and moons are not checked here. **Proposal** (v3.24, P2, optional): add the planets and their large moons (JPL Horizons, as skyshift_core.planets) |

A service that fails or times out answers **502 `upstream`** with a message naming it ("No catalogue
results are shown"); results are never invented or partly filled in. Only complete answers are cached
(an `asteroids` answer with `errors` is not). 400 for an unknown `kind`; 404 outside a published field.

### GET /api/export?ra=&dec=&det=2&clock=months&format=

A download (`Content-Disposition: attachment`). Published fields: `json` (science.json), `csv` (the
`frames[]` table: `mjd, det, um, visit, quality, flags`), `gif` (the months blink of `det`, labelled
frames). Other fields with static twins under `/data/<slug>/`: `json` (`{ frames, object }`), `csv`
(epochs) and `gif` of the first set with that clock and detector. `ades` and `mpc80` are **drafts** from
a local job's fast-mover tracks only (unverified astrometry, no observatory code; never submit as is);
elsewhere 404. Unknown format 400.

### POST /api/jobs?ra=&dec=&size=5|15|20|60, GET /api/jobs/<id>, POST /api/reference?ra=&dec=

Background analysis of a new field (IRSA cutouts, sky model, candidates) and the DR1 reference cube.
**Off by default**, and off on Cloud Run: jobs live in one process's memory and local disk, which a
multi-instance service does not share. `SKYSHIFT_ENABLE_SCIENCE_JOBS=1` turns them on for a local
single-process server only (scripts/run-local.sh).

- Off: `POST /api/jobs` answers 503 `upstream` ("New-field analysis is not enabled here") before
  checking the values (a missing `ra` or `dec` is still 400). `POST /api/reference` returns the
  published `science.json` within 2″ of a published field, otherwise 404 `not_found`.
- `POST /api/jobs` (on) → `{ id, ra, dec, size, state:"queued"|"running"|"done"|"error", progress:[string],
  error|null }`; at most 3 queued or running, else 429. `GET /api/jobs/<id>` → the same; an unknown or
  malformed id is 404 (on or off).

## Firestore: flags and votes (no API endpoint)

| collection | fields |
|---|---|
| candidates | `ra, dec, mjd, clock, source: "user" \| "pipeline", created` |
| votes | `candidate_id, answer: mover \| changing \| junk \| unsure \| withdrawn, ts, voter` |

- Rules are **create-only**: anyone may read and create; nobody may update or delete. Each create
  must have exactly these fields, with the right types and allowed values.
- `ra`, `dec` in degrees, `mjd` of the frame that was clicked, `clock` `months` or `hours`.
- Candidates of kind `shape` (v3.7) ask "One object or two?" with the same four stored answers, so the
  rules don't change: `mover` is shown as "Two objects", `changing` as "Changes", `junk` as "One object",
  `unsure` as "Not sure". Consensus (≥ 5 votes, 70% mover or changing) reads as "two objects or changing".
- `candidate_id` is a Firestore `candidates` document id or a candidates.json `id` (v3.24: or a `more` file's; at most
  128 characters, as the rules require).
- `ts` is set with `serverTimestamp()`. `voter` is a random id kept in the browser, never a
  name or email.
- Changing or taking back a vote (v3.16): a new `votes` document with the new answer, or with
  `withdrawn` to take the vote back. Nothing is updated or deleted, so the history stays. Per
  candidate, only each voter's latest document (largest `ts`) counts: its answer, or nothing when it is
  `withdrawn`. A document whose `ts` is not set yet (a write still pending) is the latest. Every reader
  that counts votes (web/src/vote, web/src/community, mcp/) applies this rule before counting or
  computing consensus. `withdrawn` is never shown as an answer.
- Voter ids are anonymous and readable, so the rules can't stop a later document written in another
  voter's name, just as they can't stop new ids voting again. Counts are a community signal, not a
  secure ballot.
- `created` (candidates) is set with `serverTimestamp()`; the rules require
  `created == request.time`. Lists of candidates sort newest first by `created`. Candidates made
  before v2.1 have no `created`: sort them last. Read it as a Firestore Timestamp (`toMillis()`).
- Pipeline suspects are written server-side (Admin SDK, which bypasses the rules) with
  `source: "pipeline"` and `created` = the server time (`SERVER_TIMESTAMP`).
- Browsers that send `created` are refused by the v2 rules, and browsers that don't are refused by
  the v2.1 rules. So the rules change (branch `p1/rules-created`) lands in the same PR as the
  web/src/vote change (web/src/vote/model.test.ts checks that both list the same fields), and the
  rules and the site deploy together (`infra/firebase/deploy.sh`, both parts). Until then the
  deployed rules are v2 (no `created`).

## Changes in v3.2 (14 Nov 2026)

- `/api/object` and `object.json`: optional `measure_reason` (why `measure` is null: `few_visits`,
  `not_detected`, `edge`, `different_sources`, `no_time`, `moved_away`) and `measure_hint` (with
  `moved_away`: the fast star's name and its position at the middle of SPHEREx's visits). Additive:
  no existing field changes, and files without them (the static `object.json` files) read as null.
- `measure` row: null also when the detection guard (core.measure_shift, #121) finds no single
  source clearly seen in both visit stacks.

## Changes in v3.1 (14 Nov 2026)

- New static file `/data/candidates.json` (type `CandidatesResponse` in web/src/api/types.ts) and
  `/data/cand/<id>/frames.json`: what the pipeline found, for the Discover, Review and Candidate pages.
- Firestore `votes.candidate_id` may be a candidates.json `id`.
- Each candidate has `zones` (every zone that found it), not yet in types.ts.

## Changes from v2 (14 Nov 2026)

- suspects.json: `cell` is an integer (the 6″ cell of contracts/dots.md), field table added.
- Firestore `candidates`: new field `created` = `serverTimestamp()` (rules and web follow in one PR).
- validation.json: the eleven checks V1–V11 listed with their units.
- "Proposal" removed where server/, scripts/ and web/src/api/types.ts already implement the shape:
  `det` default 2, epoch ids, the frame data layout and raw 0 = no data, `/api/gaia`, `/api/known`,
  `/api/resolve`, several detectors in a static frames file, validation.json, suspects.json.
  Still open: `known_ucd` (the server returns null so far).
- Known objects (14 Nov, P1): MPC only, no SkyBoT. `known[].source` is `"mpc"` | `"most"` | `"irsa-3i"`;
  asteroids and comets come from Minor Planet Center orbits computed with kete.

## Changes in v3.3 (14 Nov 2026): the measured layer

- `/api/frames` and the static `frames*.json`: optional `measured` (what our pipeline detected in the
  photos of the response: per-epoch `detections` with a `class`, mover `tracks`, months-clock `shifts`,
  the zone and its stage) and `measured_reason` (why `measured` is null). Additive: no existing field
  changes; files and answers without them read as not computed. Class names: `star`, `known`, `mover`,
  `suspect`, `artifact`, `other` (`known` as in `known[]`).

## Changes in v3.4 (14 Nov 2026): tracks and grade A

- suspects.json: optional `n_sessions`, `rate_arcsec_h`, `pa_deg`, `rate_sigma` per suspect, from the
  pipeline's `tracklets` step (movers linked across the sessions of one visit; contracts/dots.md
  `tracklets`). null when the suspect is on no track; absent from files written without tracks (read
  absent as null). Additive: no existing field changes.
- Grade `A` is now given: a candidate mover, on a track over ≥ 2 sessions with motion ≥ 5σ, no
  catalogue or known match (grade B plus the track). It sorts first in suspects.json and, through
  `grade`, in candidates.json, where an A's `why` names the track.
- contracts/dots.md: `nonfixed_dots`, `tracklets`, `tracklet_dots`, and `recall` (recall.json): `recall`
  now counts a known sighting found by a mover or a dot recovered on a track; `n_found_movers`,
  `recall_movers` and `control_recall_movers` are the movers-alone numbers.

## Changes in v3.5 (14 Nov 2026): high_pm lists vetted rows only

- candidates.json: high_pm lists only the `vetted` rows of highpm_candidates.parquet. On the hunt zone
  this removes the 714 rows Gaia proves wrong (997 → about 130 rows, nearly all Gaia-confirmed movers).
  The high_pm caution codes stay valid in `reasons` but no longer appear in practice. No field changes.
- contracts/dots.md: astrometry leaves out masked-peak and second-in-photo dots, fits a zero point per
  detector and uses session-robust pm errors; highpm compares with Gaia and CatWISE including the
  parallax leak, selects at 4.5σ, adds `few_sessions`, `unchecked`, `gaia_slow` and `fp_control`.

## Changes in v3.6 (14 Nov 2026): the distant-planet search

- New static file `/data/distant.json` (scripts/validate, P1): the pipeline's `orphans` step for one zone (what was
  searched, passing pairs with their implied distance, the chance controls, the table-level injection by distance,
  the depth and reach). Type `DistantResponse` in web/src/api/types.ts. Example: fixtures/api/distant.json.
  Additive: no existing file or field changes.

## Changes in v3.7 (14 Nov 2026): shape candidates

- candidates.json: new kind `shape` ("One or two?"; id `<zone>-sh-<source_id>`, clock `months`) from
  `results/<zone>/shape_candidates.parquet`, `counts.shape`, reason codes `elongated`, `steady_pa`,
  `one_visit`, `one_catalogue_source` (and `class_galaxy`), `pa_deg` = the stretch's axis (0–180) for shape.
  Additive: the other kinds and every field are unchanged; files without `shape` read as 0 of them.
- Firestore votes: shape candidates use the same four answers with other labels (above); rules unchanged.

## Changes in v3.8 (14 Nov 2026): science workspace endpoints

- Supplemental, from the workspace integration (credit: 春。 and Codex): `/api/frames` param `view`,
  `GET /api/science`, `GET /api/analysis`, `GET /api/science/capabilities`, `GET /api/enrichment`,
  `GET /api/export`, `POST /api/jobs`, `GET /api/jobs/<id>`, `POST /api/reference`, and the static
  `/science-data/<slug>/` products. Background jobs off unless `SKYSHIFT_ENABLE_SCIENCE_JOBS=1` (never on
  Cloud Run). Additive: no existing endpoint, file or field changes; nothing here is scored.
- A missing engine now answers 404; the fixture examples (`"synthetic": true`) are served only with
  `SKYSHIFT_ALLOW_SYNTHETIC=1` (tests). Every engine is real, so no live answer changes.

## Changes in v3.9 (14 Nov 2026): where the measured shift comes from

- `/api/object` and `object.json`: optional `measure.source = { det, visits:[first,last], mjd:[first,last],
  n_exposures:[first,last], fov, pixel_arcsec, core_filled:[first,last] }`, the detector, visit pair,
  median times and exposure counts of the two stacks the shift was measured on. Additive: no existing
  field or number changes. Files and answers without it (the static `object.json` files until the next
  precompute, cached live answers) read as `null`: unknown, never guessed from the player's frames.
  Proposed by 春。 (PR #66), adapted to v3.8.

## Changes in v3.10 (15 Nov 2026): spectral and shape candidate rules

- candidates.json spectral: the exporter cleans the labels (PAH from photoz at the galaxy's redshift, no ice on
  a good-photo-z galaxy, no water vapour or CO 2.3 µm on Gaia M dwarfs), a Gaia G < 20 star within 6″ counts as a
  Gaia match, and at most 20 PAH-only rows per zone. No field or code changes; fewer rows (hunt: 97 → about 9).
- shape: `steady_pa` now means both detector arrays (D1–D3, D4–D6) show the stretch along the same axis; 2MASS
  entries and flux-weighted neighbours to 20″ explain a stretch; galaxies (good photo-z, or extended with no Gaia
  star) are not listed. No field or code changes; fewer rows (hunt: 49 → 5).

## Changes in v3.11 (14 Nov 2026): bright-star fragments and blends

The pipeline's new `fragments` step (contracts/dots.md, `fragments`)
tags each linked source `bright_fragment` (a piece of a saturated star's ring), `saturated_star`, `blend`
(two or more Gaia stars within 8″) or nothing. One big star must not read as many small ones, and two blended
stars must not read as one plain star. All additive:
- candidates.json: new reason `blend` (high_pm, spectral, shape); high_pm, spectral and shape candidates whose
  source is a `bright_fragment` are no longer listed.
- distant.json: `counts.dropped.blend`; `bright_fragment` also counts the step's tag. Older files lack `blend`.
- measured layer (`/api/frames`, frames files): artifact reason `bright_fragment` on both clocks with the new
  optional detection field `parent_gaia_id`; reason `blend` on a source's detections of any other class (the
  class stays) with the new optional field `blend_gaia_ids`. Both fields are null when they don't apply;
  older responses lack them (read as null). Readers: draw one mark for a fragment's parent star and strike
  the fragments through; label a blend as more than one star.
- Without `results/<zone>/fragments.parquet` (a zone the step hasn't run on) nothing changes.

## Changes in v3.12 (15 Nov 2026): shape candidates' two-source fit

At 1–2 SPHEREx pixels a second object merged into a star gives one dot per photo, so it had no position, flux or
circle of its own. The pipeline now fits every shape candidate's pixels with one ePSF and with two (contracts/dots.md
`shape_candidates`, new columns). All additive:
- candidates.json: a shape candidate's `why` ends with the fit in plain words (where the second object sits, how much
  fainter it is per wavelength, whether it moved between visits, Δχ²). No new field or reason code.
- measured layer (months clock): a separated second object is its own detection, class `other`, reason
  `split_by_fit`, `source_id` and `gaia_id` null, `n_dots` 0. Readers that show reasons as given need no change;
  suggested words: "a second object a two-object fit split off this source".
- Without the new columns (a shape_candidates.parquet written before the fit) nothing changes.

## Changes in v3.13 (15 Nov 2026): the measured class `stack`

The pipeline's new `stack` step (contracts/dots.md, `stack_sources`, `stack_forced`) detects on per-visit stacks
of a zone's photos (2″ grid, matched filter, a second pass on the residual) and fits every photo at all those
positions at once. Additive:
- measured layer (`/api/frames`, frames files), months clock only: class `stack` for stack sources with no
  single-photo linked source within 3″ (rules above); uncatalogued ones in a much brighter source's misfit core
  (< 8″) or at the ePSF's edge (20–30″) are class `artifact` with the new reason `stack_near_bright`; and the
  `counts` key `stack` where the zone has a
  `stack_sources` table. Zones without it, the hours clock and older responses: no such class or key (read a
  missing key as 0). Readers that don't know the class may draw it as `other`.

## Changes in v3.14 (15 Nov 2026): optional API keys

Additive. `POST /api/keys`, `GET /api/keys/me` and `DELETE /api/keys/me` (section "API keys"), the request
header `X-API-Key` (or `Authorization: Bearer`), and two error codes: `unauthorized` (401, a key that is
unknown or revoked) and `rate_limited` (429). Requests without a key are unchanged; readers that never send
a key need no change.

## Changes in v3.15 (15 Nov 2026): V6 and V7 are run

validation.json's V6 (fast stars) and V7 (brown dwarfs), cut on 14 Nov, now carry results, with the plan's tests
(scripts/validate/v6_v7_motion.py: V2's measurement on each object). V6: `value` is the slope of measured against
Gaia's predicted shift (unit `slope`), over the 40 fastest Gaia stars with G > 12. V7: `value` is the number of
brown dwarfs faster than 1″/yr that shift the predicted way (unit `objects`): measured shift ≥ 3σ and within 30°
of the prediction. Rows: one per object (keys listed in the note). The shape is unchanged; a reader that shows
every check in file order needs no change. A missing input still gives `pending`.

## Changes in v3.16 (15 Nov 2026): change or take back a vote

- Firestore `votes.answer` may also be `withdrawn`: it takes back this voter's vote on the candidate.
- Each voter's latest vote per candidate counts (largest `ts`, a pending one is the latest); a latest
  `withdrawn` counts as no vote. Before v3.16 a second vote from the same voter could only be a stray;
  readers that kept the earliest now keep the latest.
- The rules allow the fifth answer; they stay create-only. The rules change lands in the same PR as the
  web/src/vote change, and both deploy together (`infra/firebase/deploy.sh`, rules then hosting).
- mcp/ counts each voter's latest vote the same way.

## Changes in v3.17 (15 Nov 2026): planets and moons are known bodies

- The pipeline's known-object check (suspects' 30″ cut, candidates.json `nearest_known_arcsec`, reason
  `known_object_near`) now also uses the planets and their large moons from JPL Horizons, next to MPC asteroids
  and comets. MPC orbits hold no moons: before this, Iapetus, Titan, Rhea, Hyperion, Titania and Uranus itself
  were listed as unknown movers. A mover within 30″ of a predicted moon is no longer a candidate.
- `why` says "no known asteroid, comet, planet or moon was predicted within 10′" (and "the nearest known asteroid,
  comet, planet or moon was predicted … away"). No field, type or unit changes; `known[].source` in frames is unchanged.

## Changes in v3.18 (15 Nov 2026): a mover near a planet is known, whatever it is

- v3.17 drops a mover within 30″ of a Horizons prediction; that needs the zone's `known` step rerun (network) and
  misses moons outside its list (Carme) and the planet's glare. From v3.18 a mover within a planet's radius at its
  time (Mars 60′, Jupiter 80′, Saturn 45′, Uranus 5′, Neptune 3′: the planet, its moons, its glare;
  skyshift_core.planets, offline) is known too: suspects.json drops it and lists the veto `planets` (after `mpc`),
  candidates.json doesn't list it (the exporter applies it to older tables too), and the measured layer classes its
  track and dots `known` with `known_name` the planet (`Saturn`) or `a moon of Saturn`, even when an older suspects
  table lists it.
- Shapes unchanged; `known_name` gains values that are not `known[]` names, so show it as text. Readers that map
  veto codes to words get one more code (`planets`). MPC stays the only source of asteroids and comets (no SkyBoT).

## Changes in v3.19 (16 Nov 2026): the hours clock at a time

- `GET /api/frames` takes an optional `mjd` (MJD, UTC) with `clock=hours`: the visit that holds that time,
  the session nearest it whole, the rest of the live cap from the visit's other sessions (section "The hours
  clock at a time"). At most 6 exposures, or that session plus 2 when it has 5.
- Why: without a time the live hours clock shows the visit with the most exposures and the first 2 exposures
  of 3 sessions, so a mover candidate without static frames was mostly shown without its own exposures
  (9 of 10 sampled grade A/B movers, 15 Nov), and its track could not appear in `measured`.
- Additive: requests without `mjd` are unchanged, and so are their cache keys. No response field changes,
  so fixtures/api/ is unchanged. The review pages send a mover's `mjd` (web/src/review).

## Changes in v3.20 (16 Nov 2026): fill exposures for a candidate's empty edge

Additive. A candidate's static hours frames are its own exposures, often of one pointing: when it sat near a
detector's edge they share one empty corner. `/data/cand/<id>/fill.json` (optional, same shape as its
`frames.json`: same clock, field, grid and `det`s) lists other exposures of the same visit whose footprints
cover that corner, chosen by scripts/candidates/fill.py. The site paints them only where the candidate's own
photo has no data, drawn as filled; they are not epochs of the blink and carry no `gaia`, `known` or
`measured` (empty lists, `measured` absent). Readers that don't know the file need no change; without it the
site may ask `/api/frames` (with the candidate's `mjd`) for the same purpose.

## Changes in v3.21 (16 Nov 2026): steady motion or a wobble

Additive, months clock only. A proper motion is a straight line at steady speed; parallax adds an out-and-back
every six months of at most 0.55″ (Barnard's Star). On the five featured fields about 1 in 7 sources seen in 3 or
more visits of one detector goes more than 1″ out and comes back (e.g. (0, 0) → (−1.15, +0.17) → (−0.34, +0.50)″):
measurement scatter (blends whose brightest star changes with wavelength, the position on the detector,
saturation), not motion. The pipeline's fit can still give such a source `pm_snr` ≥ 3, and the site described the
last step between two visits as the motion.
- `shifts[]` gain `visits` (the per-visit positions with a 1σ error), `chi2_nu` and `wobble_arcsec` (the visits
  about the reported motion's straight line) and `steady` (section "shifts[]"). Thresholds: χ²_ν ≤ 3 (2 degrees of
  freedom: 5% of honest cases above it; the pipeline's fake-pm tail had median χ²_ν 3.1 against 0.94 for its core),
  or ≥ 5σ with the errors × √χ²_ν. Per-visit errors × 1.6 (the Gaia check of pos_err).
- `steady: false` shifts stay in the list (not dropped) and in `pm_snr` order; readers show no arrow for them and
  say the position wobbles by about `wobble_arcsec`. Shifts without the fields (older static files, or `steady`
  null) read as before.
- server measured.py `VERSION` 3 (cache key). The static twins get the fields from the files' own detections
  (scripts/precompute `--steady-only`, no data bucket needed). fixtures/api/ has no shifts, so it is unchanged.

## Changes in v3.22 (16 Nov 2026): the motion model's high_pm reasons

Additive. The pipeline's astrometry now fits position(t) = p0 + μ·(t − t0) + ϖ·P(t) (contracts/dots.md `astrometry`:
Gaia's parallax as a prior, our own with visits in both seasons, else none and the leak k recorded) and calls a source
`unsteady` when its visits go out and back instead of along a line. candidates.json high_pm `reasons` gain
`parallax_fit` (the motion has the parallax taken out; informational, weight 1) and `unsteady` (a "check first" code,
weight 0.3; never on an exported row today, since unsteady rows are not vetted). `pm_mas_yr` of a high_pm fit is now
free of the six-month parallax leak wherever Gaia has a parallax. No field changes; readers that ignore unknown reason
codes need no change.

## Changes in v3.23 (16 Nov 2026): what to check, certain matches listed apart, more hours photos

- candidates.json: every candidate gains `status` (`open`, `known`, `flaw`), `known_name` and `checks`
  (section candidates.json). Policy: nothing is removed unless it is certain; a certain known body or flaw is
  listed apart, everything else stays and `strong` checks lower its `score`. Additive: readers that don't know
  the fields ignore them (older files: `open`, null, []).
- `GET /api/frames` takes `page` with `clock=hours` (live only): more exposures of the same visit, 6 a page, so
  the site can show the first answer at once and load the rest behind it. The response gains `more` (bool: a
  further page has exposures) and `visit_exposures` (int: exposures of `det` in the shown visit); both absent
  in static twins and older answers (read as false and null). Page 0 is unchanged and keeps its cache key.

## Changes in v3.24 (16 Nov 2026): the 16 Nov fix round

What the fix round's PRs proposed (#366–#372 and #374; the exporter, #369, was still open when this was written), and
one design decision (the Less likely files), in one place. No field is removed or renamed, and every addition reads
safely when absent.

- candidates.json `score` (#369): each kind's base score × 0.3 per `strong` check × 0.95 per `warn` (a check the
  kind's formula already counts excepted; section score). The mover's caution factors are gone (two dots × 0.85,
  `outlier_flag` × 0.6, another record-only flag × 0.7, a masked pixel in a dot's 3×3 × 0.8, motion not significant
  × 0.85, `detector_excess` × 0.6, `repeat_position` × 0.3, `edge` × 0.5): checks carry them now. OUTLIER at a dot's
  peak marks 68% of known asteroids and 28% of the list, so `outlier_flag` also leaves the "check first" list (the
  reason stays, informational). 306P/LINEAR's two grade A movers: 0.174 → 0.414 and 0.084 → 0.234. Grade order is
  unchanged. Older files: scores as written.
- checks (#369, #372, #368, #371): every code's levels, rule and `values` keys (table "Codes and levels"). New codes:
  `hot_pixel_proven`, `persistence_proven` (both `strong`, status `flaw`), `hot_pixel` (also a pixel measurement),
  `wide`, `one_photo`, `status_conflict`, the vetting gates as codes (`bad_column`, `masked_peak`, `red_photo`,
  `not_vetted`), high_pm `catalogues_checked` and `known_nearby`. `flag_outlier` is `ok`. `catalogue_star` values gain
  `chance` and `chance_6arcsec`. Readers already show unknown codes' `text` and `look` as given (v3.23): no change needed.
- Side files (#369): check producers' `<dir>/<source>.json`, merged by the exporter (`--checks-dir`, `--merge-only`);
  a producer's check replaces the exporter's of the same code; status known > flaw > open (section Side files).
- Known bodies (#371): position-based matching (the suspects' `mpc` veto, `nearest_known_arcsec`) uses bodies
  brighter than V 21 or without V, and a body of any brightness that certainly is the mover removes it too (the
  `status` known rule; the pipeline's cache now keeps V ≤ 23). #374 then narrows the place cut (next item). Comets: the MPC's cometels plus allcometels, 973 → 2,215 (306P/LINEAR
  was missing); `known[]` and `/api/known` get them when P2 rebuilds the server's index. The `known` producer: 10
  movers `known`, all 306P/LINEAR (both ecl04 grade A).
- Catalogues (#368): every zone's refcat holds Gaia DR3, CatWISE2020 and 2MASS, and its marker records the service per
  catalogue (contracts/dots.md refcat). The 64 zones combined against Gaia alone before 16 Nov say so in their marker
  until rebuilt.
- Science workspace (#366): GET /api/analysis's grid orientation, pixel fields and `t` (an index into
  `science.json` `frames`) written down, no change. Proposals for P2, not sent yet: CatWISE `epoch_mjd` (absent: read
  as 57170.0) and planets and moons in `kind=asteroids` (absent: not checked).
- contracts/dots.md (16 Nov, same PR): the movers' fast pairing rule and link's shuffled control (#367), `in_fixed`
  rows in nonfixed_dots and tracklet_dots, fast tracks, tracklets' control counts; the refcat marker's `services`
  (#368); the suspects' certain-known cut (#371); extraction v2, off until the rerun (#370: folder
  `dots_deblend1e-4_cp3_g6_psf_glow_v2/`, `sep_flag` 0x400, columns `snr_mf`, `n_transient`, `n_masked`,
  `partner_flux_ujy`, `partner_err_ujy`, the peaks file, photo metadata); the glow model's core fix. contracts/flags.md:
  OUTLIER's open question answered (record, never mask).
- Older files read as: candidates.json without these checks, as v3.23 says (no `checks`: []; no `status`: `open`);
  no `more`: [] (every candidate in candidates.json); suspects.json without `catalogues`, `warnings`, `warn_codes`,
  `warn_values`: no warnings, catalogue vetoes in `vetoes`; tables without `in_fixed`: false; dot files without the v2 columns: as before (no matched-filter dots, no partner
  flux); summaries without `control` or `fast_tracks`: not measured.
- Suspects (#374): only the certain are removed: a known body within 5″ by place, or within 30″ moving at our rate
  (50% or 3σ) and direction (45° or 3σ), or certain at any brightness; a planet, moon or glare; a hot pixel. The rest
  is kept, with `warn_codes` and `warn_values` (suspects.json, per suspect) or as grade C with the new reasons `bloom`,
  `halo`, `bright_star`, `gaia`, `catwise` (a catalogue source within 6″ at most 2 mag fainter; `2mass` and `allwise`
  take the same test). suspects.json gains `catalogues` and `warnings`; `vetoes` is `mpc`, `planets`. Grade A above
  400″/h needs a recovered dot or a detector partner (`fast_held`). 2MASS is checked at last (the refcat's `2mass`
  label). `bad_column` is chance-aware. candidates.json: a mover's `gaia_match` (`catwise_match`) is true with the
  reason `gaia` (`catwise`). suspects.parquet: `warn_codes`, `warn_values`, `nearest_known_name`,
  `session_rate_arcsec_h`, `session_pa_deg`, `gate_bloom`, `gate_halo`, `gate_bright_star`, `gate_gaia`,
  `gate_catwise` (contracts/dots.md).
- Less likely movers (P1's design decision, 16 Nov): #374 keeps about 9,000 more grade C movers in four zones (~100,000
  expected over all zones). Each zone's open grade C movers go to `/data/candidates-more/<zone>.json` (same candidate
  objects), indexed by candidates.json `more: [{ zone, kind, grade, count, url }]`; candidates.json keeps grade A and
  B movers, every other kind, and known and flaw entries; the site loads a zone's file only when "Less likely" is
  opened. Producers `known` and `catalogue` may run on these files, `pixel` on grade A and B movers only (section
  Less likely movers).
- fixtures/api/ is unchanged: no fixture holds candidate checks, `more` or suspects' warnings (as in v3.23).
