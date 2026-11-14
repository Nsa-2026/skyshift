# contracts/api.md: endpoints, static twins, visits, Firestore (v3.11)

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
layer's `bright_fragment` and `blend` reasons with `parent_gaia_id` and `blend_gaia_ids`).

Static first: the site reads `/data/<slug>/` before it calls `/api/`. The static files have the
same shapes as the API, so if front and back don't meet, the static format is the contract.
The API lives at the site's own `/api` (Firebase Hosting rewrites `/api/**` to the Cloud Run
service `skyshift-api` in us-east4), so the web app uses relative URLs.

Examples of every shape: `fixtures/api/` (synthetic, see fixtures/CLAUDE.md).
Lines marked **proposal** are not in the dev plan yet; P1 confirms or changes them.

## Summary

```
GET /api/frames?ra=&dec=&fov=&clock=months|hours&det=1..6
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

# static twins, same shapes
/data/<slug>/frames.json   /data/<slug>/object.json
/data/<slug>/index.json    /data/<slug>/frames_<clock>_<fov>.json
/data/validation.json      /data/suspects.json
/data/candidates.json      /data/cand/<id>/frames.json   # candidates to review
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
- Errors: HTTP 4xx/5xx with `{ "error": { "code": "bad_request" | "not_found" | "upstream" | "internal", "message": "..." } }`.
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
| view | `raw` / `model` / `resid` / `refdiff` | `raw` | v3.8, science workspace only. Not `raw`: a derived layer (below), same response shape |

**Live caps.** A cold `/api/frames` call must fit in Cloud Run's 60 s, so the live API uses fewer
exposures than the static files. The static twins (scripts/precompute) use all of them.

| clock | live `/api/frames` | static `/data/<slug>/` |
|---|---|---|
| months | at most **6 exposures per visit** in each stack, spread evenly over the visit (first and last included) | every exposure of the visit |
| hours | at most **6 exposures**: the first 2 of 3 sessions spread over the visit (its first and last session included; sessions with 2 or more exposures preferred) | every exposure of the visit, up to 48 (whole sessions from the visit's start) |

The hours clock shows one visit: the one with the most exposures of that detector (the latest on
a tie). `n_exposures` always reports the exposures really in the frame: a capped months stack of
a 19-exposure visit says 6; an hours frame says 1.

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
| known | Known objects predicted at an epoch's `mjd_mid`; `epoch_id` is that epoch's `id`. `v` is the V magnitude, or null. `source`: `mpc` (Minor Planet Center orbits propagated with kete, from SPHEREx's own position when the photo header gives it), `most` (IRSA MOST), `irsa-3i` (IRSA's 3I/ATLAS photo list) |
| data_release | Collections searched, e.g. `"DR1+QR3"` |
| archive_checked | Date of the IRSA search, `YYYY-MM-DD` |
| measured | **Optional** (v3.3). What SkyShift's own pipeline measured in this field, below; `null` when nothing is measured here (`measured_reason` says why). Files and answers without it: not computed, read as `null` |
| measured_reason | **Optional** (v3.3). Why `measured` is null, a code below; `null` when `measured` is set |

### measured: what SkyShift measured (optional, v3.3)

`gaia[]` and `known[]` are catalogue **predictions**. `measured` is what our pipeline (pipeline/,
contracts/dots.md) **detected** in the photos of this response: the dots of each photo, the sources
it linked, the movers it paired, and the motions it fitted. Built by the server from
`gs://nasa-glazing-data/results/<zone>/` and `dots/` (read-only), and by scripts/precompute for the
static twins (`--measured-only` rewrites only `measured` and `measured_reason`).

```
measured: { zone, stage, zone_box:{ra:[min,max], dec:[min,max]}, clock,
            photos, photos_missing, truncated,
            counts:{ star, known, mover, suspect, artifact, other },
            detections:[{ epoch_id, ra, dec, snr, flux_ujy, n_dots, class,
                          source_id|null, gaia_id|null, known_name|null, track_id|null,
                          grade|null, reasons:[code…],
                          parent_gaia_id?|null, blend_gaia_ids?|null }],    # v3.11, optional
            tracks:[{ id, class, suspect_id|null, known_name|null, evidence,
                      dots:[{ ra, dec, mjd, epoch_id }] }],
            shifts:[{ source_id, gaia_id|null, class, ra, dec, mjd_from, mjd_to, dt_yr,
                      dra_arcsec, ddec_arcsec, shift_arcsec, err_arcsec, pa_deg,
                      pmra, pmdec, pm_err, pm_snr }] } | null
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
| counts | {class: int} | Detections per class, all six keys, **before** the cap |
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
| class | string | `star` \| `known` \| `mover` \| `suspect` \| `artifact` \| `other`, rules below |
| source_id | int or null | The pipeline's linked source (contracts/dots.md `source_id`, below 2⁵³). Months: always set. Hours: the source within 4″, else null |
| gaia_id | string or null | Gaia DR3 source_id of the source's crossid match (`results/<zone>/crossids.parquet`), a string like `gaia[].id`. It is in `gaia[]` when that list goes deep enough |
| known_name | string or null | `known[].name` of the entry with the same `epoch_id` it matched (class `known`) |
| track_id | string or null | `tracks[].id` of the mover it belongs to |
| grade | `"A"` \| `"B"` \| `"C"` or null | Suspects only, from the zone's suspects table when it is graded (vetting); null otherwise |
| reasons | [string] | `suspect`: the suspects table's `reasons` passed through **as they are** (the gates it fails, the codes of suspects.json, e.g. `hot_pixel`, `bad_column`, `edge`, `masked_peak`, `sharp_outlier`, `flux_ratio`, `red_photo`, `2mass`; new codes from the vetting may appear: show them as given), `[]` if none or ungraded. `artifact`: why, codes below. `mover`: `near_gaia` when a `gaia[]` star is within 6″. `blend` (v3.11), on any class but `artifact`: its linked source is tagged `blend` by the pipeline's `fragments` step (two or more Gaia DR3 stars, G < 18, within 8″: one detection, several stars); the class stays. Else `[]` |
| parent_gaia_id | string or null | **Optional** (v3.11). Class `artifact` with reason `bright_fragment`: Gaia DR3 source_id (a string like `gaia_id`) of the bright star whose ring this is a piece of (`fragments` step: the brightest Gaia star with G < 12 within 60″). All fragments of one star share it: draw that star once, not each piece. Null otherwise or when no such star is listed. Older responses lack the field: read as null |
| blend_gaia_ids | [string] or null | **Optional** (v3.11). Reason `blend`: the Gaia DR3 source_ids (strings, nearest first) of the stars merged into this detection. Null otherwise. Older responses lack the field: read as null |

**class**, first rule that applies:

| class | hours clock (a dot) | months clock (a linked source in one visit) |
|---|---|---|
| suspect | In a pipeline mover listed in `results/<zone>/suspects.parquet` | A `highpm_candidates` fit, `vetted`, status `new` (moves between visits, no Gaia star) |
| known | In a mover with a `known[]` prediction of the same epoch within 30″ (the suspects rule); or the nearest dot within 10″ of a `known[]` prediction of its epoch, not within 4″ of a linked source | Not used |
| artifact | In a mover with GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK, HALO or BLOOM in its dots' 3×3 flags; or a lone dot (no source, no mover) with a mask bit at its peak (OVERFLOW/NONLINEAR aside), one of those record-only bits, or `elong` > 3; or (v3.11) within 4″ of a linked source tagged `bright_fragment` | A `highpm_candidates` row that is not `vetted`, unless the source is a Gaia star; or (v3.11) a source tagged `bright_fragment` |
| mover | In any other pipeline mover (paired inside one session or by two detectors at one moment, but vetted out of the suspects, e.g. near a catalogued star) | Not used |
| star | Within 4″ of a linked source with a Gaia DR3 match | A linked source with a Gaia DR3 match |
| other | Everything else: a linked source without Gaia (galaxy, faint or red star), or a lone dot | Everything else |

**artifact reasons**: `ghost`, `ghost_fpa`, `ghost_ext`, `crosstalk`, `halo`, `bloom` (that bit in the
3×3), `masked_peak` (a mask bit at the peak), `elongated` (`elong` > 3), and on the months clock the `highpm_candidates` `reason` codes (`short_span`, `few_dots`,
`bad_fit`, `over_pm_cap`, `gaia_pm_disagrees`, `catwise_pm_disagrees`, `at_control_level`, and from 14 Nov
evening `few_sessions`, `unchecked`, `gaia_slow`; v3.11 `fragment`), and (v3.11, both clocks) `bright_fragment`: a piece
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
| known_name | string or null | `known[].name` it matched (class `known`) |
| evidence | string | `two_exposures` or `two_detectors`, as the pipeline found it |
| dots | [object] | Its dots in this response, **in time order**: `ra`, `dec` (deg), `mjd` (the photo's MJD-AVG), `epoch_id`. Draw the arrow from the first to the last. Dots in photos outside this response are not listed, so a track may have one dot (e.g. its partner detector is not in the file) |

**shifts[]** (months clock): sources in the field whose astrometric fit
(`results/<zone>/astrometry.parquet`) has `pm_snr` ≥ 3, at most 300 (highest `pm_snr` first).
Proper motion only (parallax not included: ≤ 1″ for Barnard's Star; `/api/object` `measure` has the
full measurement).

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
| `/data/cand/<id>/frames.json` + frame files | scripts/candidates/frames.py (P1, with the P2 engine) | `GET /api/frames` for one candidate (its clock, field and detectors); frame files `frames/<c><fov>_d<det>_…bin` as above. Only for candidates whose `frames_url` is set |
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
(docs/skyshift-hackathon-plan.html, "How we know it works"). V6 and V7 belonged to cut work
(S2, S3) and stay `pending`; V9–V11 were added on 14 Nov. The file lists them in this order:

| id | claim | unit of `value` | expected |
|---|---|---|---|
| V1 | Five test objects recovered | targets | 5 of 5 |
| V2 | Motion matches Gaia | arcsec | Barnard's Star within 15% and 10° of Gaia |
| V3 | Position error floor | arcsec | ~0.25″, 10 or more reference stars |
| V4 | Asteroids re-found, targeted | asteroids | N of 60; median offset under 1 px; control under 5% |
| V5 | Asteroids re-found, blind (S1) | recall | Recall at V < 16 ≥ 50% (gate G2); control near 0 |
| V6 | Fast stars (S2) | null | Cut: `pending` |
| V7 | Brown dwarfs (S3) | null | Cut: `pending` |
| V8 | Spectra and speed | ratio | Spectra within 20% of 2MASS Ks / WISE W1; first frame under 15 s |
| V9 | Astrometry matches Gaia | mas/yr | Proper-motion bias near 0; pull scatter near 1 |
| V10 | Galaxy distances match SDSS | dz/(1+z) | σ_NMAD ~0.02 for bright galaxies (AB < 17) |
| V11 | Asteroid sizes match published values | ratio | Diameter ratio near 1 |

A reader shows every check the file holds, in file order; an id missing from the file counts as `pending`.

### suspects.json

```
{ generated, data_release, zone,
  vetoes:[…], provisional, grades:{A,B,C}, gates:{<gate>:n},          # added 14 Nov (vetting)
  suspects:[{ id, ra, dec, mjd, clock:"hours", det, evidence:"two_exposures"|"two_detectors",
              n_dots, snr, flux_ujy, cell, nearest_known_arcsec|null,
              grade:"A"|"B"|"C", reasons:[<gate>…], near_source_arcsec|null,      # added 14 Nov
              n_sessions?|null, rate_arcsec_h?|null, pa_deg?|null, rate_sigma?|null }] }   # optional, 14 Nov (tracks)
```

Movers with no known-object match from MPC (contracts/dots.md). Written by the pipeline's
`suspects` step (`results/<zone>/suspects.json`, copied to `web/public/data/suspects.json`).
They are also written to Firestore `candidates` with `source: "pipeline"`.
Ranked by `grade` (A, B, C), then `snr` (highest first). A file without `grade` (written before 14 Nov evening) is
ungraded: show its suspects as unvetted, not as B.

| field | type | meaning |
|---|---|---|
| zone | string | Pipeline zone, `pilot` or `hunt` |
| vetoes | string[] | Zone-level checks every listed suspect passed: `mpc` (no known object within 30″), `gaia` (no Gaia DR3 within 6″), and `catwise` (CatWISE2020, 6″) or `allwise` (AllWISE, 6″: zones with a Gaia-only refcat). A catalogue missing here was not checked |
| provisional | bool | true when Gaia or every WISE catalogue could not be checked (e.g. a Gaia-only refcat with AllWISE unreachable): very red WISE-only sources may still be listed. Say so next to the zone's suspects |
| grades | object | Count of suspects per grade, `{ "A": n, "B": n, "C": n }` |
| gates | object | Per gate name (below), the suspects failing it; a suspect can fail several |
| id | string | Unique in the file. The pipeline writes `<zone>-<cell>-<minute>` (minute = round(mjd × 1440)); treat it as opaque |
| ra, dec | float, deg | Mean position of the mover's dots |
| mjd | float, MJD | Mean MJD-AVG of the mover's dots |
| det | int 1–6 | Detector of its highest-S/N dot |
| evidence | string | `two_exposures` (paired inside one session) or `two_detectors` (D1+D4, D2+D5, D3+D6 at the same moment) |
| n_dots, snr, flux_ujy | int, float, float µJy | Dots in the mover, their highest S/N and median brightness |
| cell | integer, no unit | The 6″ sky cell of (`ra`, `dec`), exactly contracts/dots.md's `cell` (`band × 10 000 000 + col`). An id for grouping, not a quantity: neighbouring cells are not neighbouring numbers. At most ~1.08 × 10¹², so a JSON number is exact in JavaScript (below 2⁵³); never a string |
| nearest_known_arcsec | float, arcsec, or null | Distance to the nearest known object predicted in the same session, moved to `mjd`; null if none within 600″. Suspects have none within 30″ |
| grade | string | `C`: fails a gate or is catalogued (`reasons` says which); kept in the file, shown greyed with its reasons. `B`: passes every gate; its motion is not shown (most suspects are 2 dots of one session, and at S/N 7–9 the position error exceeds the motion between exposures): an "unexplained one-session source". `A`: candidate mover: on a track over ≥ 2 sessions with motion ≥ 5σ, no catalogue or known match. That is a B whose mover the pipeline's `tracklets` step linked into a track (movers of ≥ 3 sessions of one visit, contracts/dots.md `tracklets`) with movers in ≥ 2 sessions and rate / rate σ ≥ 5; `n_sessions`, `rate_arcsec_h`, `pa_deg`, `rate_sigma` below give the track. Show A first, as the most promising. A file written without tracks has no A |
| reasons | string[] | The gates it fails, in this order (empty for A and B): `not_vetted` (no dot data to vet: an old movers table, or a dot without a pixel position), `hot_pixel` (a dot on a detector pixel, ±1 px, where the zone's movers have dots in ≥ 3 exposures or its free dots in ≥ 5: hot pixels NASA's HOT bit misses), `bad_column` (≥ 3 free dots of that exposure and detector within ±0.5 px in x), `edge` (a dot within 6 px of the detector edge), `masked_peak` (a mask bit of contracts/flags.md at a dot's peak, OVERFLOW/NONLINEAR aside), `sharp_outlier` (a dot sharper than a star: √(a·b) < 0.6 × (D1–D3) or 0.7 × (D4–D6) the detector's stellar value at its S/N, with OUTLIER at the peak; OUTLIER alone never counts), `very_sharp` (√(a·b) < 0.4 × the stellar value at S/N ≥ 15, OUTLIER or not), `flux_ratio` (brightest dot > 4× the faintest within one detector), `red_photo` (every dot from a photo graded red by the photo QA, qa_photos.parquet; amber never counts), `2mass` (a 2MASS PSC source within 6″), `allwise` (an AllWISE source within 6″; checked where CatWISE2020 was the zone's WISE veto). Thresholds: pipeline config `VET_*`. On 150 MPC-matched hunt movers (real asteroids) 141 pass every gate (edge 4, masked_peak 6) |
| near_source_arcsec | float, arcsec, or null | Nearest catalogue (Gaia, CatWISE, 2MASS) or fixed source within 10″: a possible blend or PSF wing. A warning, not a gate; null if none within 10″ |
| n_sessions | int or null, **optional** | The track this suspect is on (pipeline `tracklets`, joined on `id`): sessions of its visit with a mover on the track (≥ 3 on any track). null when the suspect is on no track. The four track fields are absent from a file written without tracks: read absent as null. Present on any suspect on a track, whatever its grade; A also needs rate / rate_sigma ≥ 5 and no failed gate. Added 14 Nov |
| rate_arcsec_h | float, ″/h, or null, **optional** | The track's apparent rate at this suspect's time (local fit over the track's movers); null off a track |
| pa_deg | float, degrees, or null, **optional** | The track's direction of motion, east of north, 0–360; null off a track |
| rate_sigma | float, ″/h, or null, **optional** | 1σ error of `rate_arcsec_h` (scaled up by √χ²_ν of the fit when > 1); null off a track |

### candidates.json

What the pipeline found, for people to review (web/DESIGN.md, "Product"). Written by the pipeline's
exporter (`cd pipeline && uv run skyshift-candidates`, pipeline/src/skyshift_pipeline/candidates.py) from
`results/<zone>/` of every finished zone; static frames for the most promising by
scripts/candidates/frames.py. Type `CandidatesResponse` in web/src/api/types.ts. Example (a real sample):
fixtures/api/candidates_sample.json (fixtures/api/candidates.json is an older hand-made pilot sample that
web tests still use; it predates `zones` and the high_pm id rule).

```
{ generated, data_release, zones:[zone…], counts:{ mover, high_pm, spectral, shape },
  candidates:[{ id, kind:"mover"|"high_pm"|"spectral"|"shape", ra, dec, mjd, clock:"hours"|"months", det, zone, zones,
                score, snr, flux_ujy, rate_arcsec_h, pm_mas_yr, pm_err_mas_yr, pa_deg,
                nearest_known_arcsec, gaia_match, catwise_match, galactic_b,
                labels:[label…], reasons:[code…], why, flags:[flag…], frames_url|null }] }
```

| field | type | meaning |
|---|---|---|
| generated | string | Time of the export, ISO 8601 UTC (`2026-11-14T15:20:00Z`) |
| data_release | string | Collections the zones' photos came from, e.g. `"DR1+QR3"` |
| zones | [string] | Zones searched, in the order used: the larger first (`["hunt", "pilot"]`). A zone appears even when all its objects were already listed from an earlier one |
| counts | {kind: int} | Candidates of each kind in the file (every kind's key, 0 if none; `shape` added in v3.7: older files lack it, read it as 0) |
| candidates | [object] | Sorted by zone (in `zones` order), then kind (mover, high_pm, spectral, shape), then `grade` (A, B or null, C; added 14 Nov), then `score` descending |

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
| nearest_known_arcsec | float ″ or null | mover only: as suspects.json, the nearest known asteroid or comet predicted in the same session, moved to `mjd`; **null = none within 600″**. Always null for high_pm and spectral (not checked) |
| gaia_match | bool | A Gaia DR3 source sits here (its motion may still disagree: see `reasons`). mover: false (suspects have no Gaia star within 6″; see `gaia_unchecked`). high_pm: the pipeline's `status` is `gaia` (either side matched). spectral: classify's Gaia match. shape: a Gaia DR3 star within 6″ is the one listed object |
| catwise_match | bool | A CatWISE2020 source sits here. mover: false (none within 6″). high_pm: a CatWISE counterpart on either side. spectral: crossids has one. shape: a CatWISE2020 source within 6″ (the listed object, or its infrared entry) |
| galactic_b | float deg | Galactic latitude (astropy, ICRS to Galactic). \|b\| < 10 is the crowded Milky Way plane |
| labels | [string] | Filter labels, vocabulary below; order as listed there. [] for movers and shape |
| grade | string or null | Movers: the suspects' vetting grade, `A`, `B` or `C` (suspects.json `grade`; A = a candidate mover on a track over ≥ 2 sessions at ≥ 5σ, its `why` names the track and it never gets `motion_not_significant`; C = a failed gate or a catalogue source within 6″, its gates are in `reasons`). null for high_pm, spectral and shape, and for zones run before the vetting. Grade C movers come after the zone's other movers and score × 0.1. Added 14 Nov |
| reasons | [string] | Codes, vocabulary below: why it was listed and what to be careful about |
| why | string | One plain English sentence: why it is on the list (examples below) |
| flags | [string] | Artefact flags near it (contracts/flags.md record-only bits), lower case: `outlier`, `crosstalk`, `ghost`, `ghost_fpa`, `ghost_ext`, `bloom`, `halo`. mover: any of its dots' 3×3 `flags`; high_pm, spectral: set on ≥ 25% of the source's dots (either source of a pair); shape: [] (dots with these flags are not used, and a source with them on ≥ 25% of its dots is not listed) |
| frames_url | string or null | Static blink frames, resolved against the URL of candidates.json like `data_url` (`new URL(frames_url, jsonUrl)`): `cand/<id>/frames.json`, a `GET /api/frames` response whose `data_url`s resolve against that file. null: call `/api/frames?ra=&dec=&clock=&fov=&det=` live |

**Static frames** (`/data/cand/<id>/`, scripts/candidates/frames.py) exist for the highest-scoring of
each kind (default 25 movers, 20 high_pm, 15 spectral; under 150 MB in all). mover: hours clock, 20′
field, `det` (and its simultaneous partner for `two_detectors`), the exposures within 0.3 days of `mjd`
(its own session and the next ones), at most 10 per detector, nearest in time first. high_pm and
spectral: months clock, 5′ field, `det`, every exposure of every visit (as the featured targets).
Several detectors may share one file (sorted by `mjd_mid`, then `det`); the client filters by `det`.

**Which objects.** mover: every row of `results/<zone>/suspects.parquet` (suspects.json's rules). high_pm:
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
candidate is a lead for a human, not a detection.

Nothing is dropped for being doubtful (except high_pm, above): doubts become `reasons` and lower the `score`. The "check first"
codes (show them as cautions): `known_object_near`, `motion_not_significant`, `bad_pixels_near`,
`outlier_flag`, `detector_excess`, `repeat_position`, `gaia_unchecked`, `catwise_unchecked` and the vetting gates (mover);
`short_span`, `few_dots`, `bad_fit`, `over_pm_cap`, `gaia_pm_disagrees`, `catwise_pm_disagrees`,
`at_control_level`, `implausible_pm` (high_pm); `one_visit`, `class_galaxy` (shape); `blend` (high_pm, spectral,
shape; v3.11). From v3.11 a high_pm, spectral or shape candidate whose source (either side of a pair) the pipeline's
`fragments` step tagged `bright_fragment` is not listed at all: it is a piece of a saturated star's ring.

**score** (0–1; the formulas are in the exporter and its tests):

| kind | score |
|---|---|
| mover | (1 − e^(−snr/15)) × evidence (`two_exposures` 1, `two_detectors` 0.6) × 0.85 with only two dots × known term (1 with no known object within 600″, else 0.5 rising linearly to 1 between 30″ and 300″) × 0.6 with `outlier_flag` × 0.7 with another record-only flag × 0.8 with a masked pixel in a dot's 3×3 × 0.85 when the motion is not significant × 0.6 with `detector_excess` × 0.3 with `repeat_position` × 0.5 with a dot within 6 px of the detector edge (reason `edge`, added by the exporter too when the vetting didn't run) × 0.1 with `grade` C. "Motion not significant" uses the dots' pos_err × 1.6 |
| high_pm | e^(−s/0.2) × the vetting reasons' weights × 0.6 for a Gaia star (`status` gaia). s is the pipeline's own score, lower = better: 1/(pm/pm_err) for a fit, \|ln flux ratio\| + spectrum-shape rms for a pair. Weights: `short_span`, `few_dots`, `bad_fit` 0.5; `gaia_pm_disagrees`, `catwise_pm_disagrees` 0.4; `over_pm_cap`, `at_control_level` 0.3; `implausible_pm` 0.01 (so these sort last) |
| spectral | max over its labels of weight × (1 − e^(−σ/10)), σ = the band's significance. Weights: `brown_dwarf_like`, `ch4` 1; `h2o_ice`, `co2_ice`, `co_ice` 0.8; `h2o_vapour` 0.6; `co_23` 0.4; `pah_33` 0.3 |
| shape | (1 − e^(−s/15)) × 0.8 with `one_visit`; s = significance of the stretch (\|mean excess ellipticity\| / its error; Rayleigh, P(> s) = e^(−s²/2) for noise) |

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
| known_object_near | mover | A known asteroid or comet was predicted within 2′ at that time: it may be that object, slightly off its prediction |
| motion_not_significant | mover | The motion is under 3σ of its position errors: it may not move at all |
| bad_pixels_near | mover | A masked pixel (cosmic ray, hot or dead pixel, persistence…) touches one of its dots |
| outlier_flag | mover | The OUTLIER flag (bit 19, recorded, not masked) is set on its pixels. Hunt: 92 of 228 suspects (40%), almost all in D4–D6 |
| detector_excess | mover | Seen only in detectors where, in that visit, the zone has at least 1.5× its usual share of unexplained movers per mover (≥ 5 suspects in the cell): likely artefacts. Hunt: D4, D5, D6 in visit 2 (D4 is 41% of the suspects but 28% of the movers); 108 suspects. A D1+D4 pair seen by D1 too doesn't count |
| repeat_position | mover | Another unexplained mover of the zone was seen within 30″ in another session, at under 2″ an hour apart: probably a fixed or very slow source, not a fast mover. Hunt: 7 suspects at 3 places, 1 to 169 days apart (one is the brightest suspect, S/N 290 in D1) |
| gaia_unchecked, catwise_unchecked | mover | Gaia / CatWISE could not be checked when the pipeline ran |
| not_vetted, hot_pixel, bad_column, edge, masked_peak, sharp_outlier, very_sharp, flux_ratio, red_photo, 2mass, allwise | mover | The suspects' failed vetting gates (suspects.json `reasons`, meanings there): the mover is grade C, most likely an artefact or a catalogued source |
| pm_fit | high_pm | Motion fitted over all its detections in every visit |
| pm_pair | high_pm | Two sources each seen in one visit only, paired across visits (too fast to stay linked) |
| vetted | high_pm | Passed every pipeline check |
| catwise_pm_agrees | high_pm | CatWISE lists the same motion |
| short_span | high_pm | Its visits are too close in time for a motion |
| few_dots | high_pm | Few detections (fewer than 20 dots) |
| bad_fit | high_pm | The positions don't fit a straight line (blend or bad fit) |
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
moving about 55″ an hour; no known asteroid or comet was predicted within 10′." · "Seen at different
places in 3 exposures over 4.3 minutes, moving about 55″ an hour; the nearest known asteroid or comet was
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

### GET /api/science/capabilities

`{ analysis_jobs: bool }`, `Cache-Control: no-store`. False unless `SKYSHIFT_ENABLE_SCIENCE_JOBS=1`.

### GET /api/enrichment?ra=&dec=&kind=

On-demand catalogue checks of a published field (or a local cube). `kind`:

| kind | source | answer |
|---|---|---|
| `catwise` | NASA/IPAC IRSA TAP, CatWISE2020 | `{ source, sources:[{ name, ra, dec, w1mpro, w2mpro, pmra, pmdec, candidate?, separation_arcsec?, pm_difference_arcsec_yr? }], note }`; CatWISE `pmra`, `pmdec` in arcsec/yr |
| `relay` | NASA/IPAC IRSA TAP, NEOWISE single exposures | `{ source, points:[{ mjd, W1?, W1_err?, W2?, W2_err?, n?, source }], error:null }`, Vega mag, NEOWISE visits then SPHEREx synthetic |
| `comovers` | ESA Gaia archive TAP, DR3 | `{ source, candidates:[ Gaia rows + pm_chi, sep_arcsec, distance_pc, projected_sep_au ], luminosity|null }`; without a target motion `{ source, candidates:[], note }` |
| `asteroids` | Minor Planet Center orbits with kete (as `/api/known`) | `{ source, objects:[{ name, ra, dec, v, visit, mjd }], errors:[{ visit, mjd, error }], note }`, one prediction per observing session |

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
| votes | `candidate_id, answer: mover \| changing \| junk \| unsure, ts, voter` |

- Rules are **create-only**: anyone may read and create; nobody may update or delete. Each create
  must have exactly these fields, with the right types and allowed values.
- `ra`, `dec` in degrees, `mjd` of the frame that was clicked, `clock` `months` or `hours`.
- Candidates of kind `shape` (v3.7) ask "One object or two?" with the same four stored answers, so the
  rules don't change: `mover` is shown as "Two objects", `changing` as "Changes", `junk` as "One object",
  `unsure` as "Not sure". Consensus (≥ 5 votes, 70% mover or changing) reads as "two objects or changing".
- `candidate_id` is a Firestore `candidates` document id or a candidates.json `id` (at most 128 characters,
  as the rules require).
- `ts` is set with `serverTimestamp()`. `voter` is a random id kept in the browser, never a
  name or email.
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
