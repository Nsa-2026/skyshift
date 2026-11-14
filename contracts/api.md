# contracts/api.md: endpoints, static twins, visits, Firestore (v2.1)

Owner: P1. Implemented by server/ and scripts/precompute/ (P2). Read by web/src/api (P3) and
web/src/blink, web/src/vote (P4). Version 2.1, 14 Nov 2026 (changes from v2 at the end).

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
              data_url, scale, zero }],
    gaia:[{ id, ra, dec, pmra, pmdec, parallax, g }],
    known:[{ name, epoch_id, ra, dec, v, source:"skybot"|"mpc"|"most"|"irsa-3i" }],
    data_release, archive_checked }
GET /api/object?ra=&dec=
→ { name|null, gaia|null, known_ucd|null,
    spectrum:[{ um, flux_ujy, err_ujy, det, mjd, visit, saturated }],
    measure:{ shift_arcsec, pa_deg, dt_yr, rate_arcsec_yr, sigma_arcsec,
              n_ref_stars, predicted_arcsec|null } }
GET /api/gaia?ra=&dec=&r=
GET /api/known?ra=&dec=&r=&mjd=
GET /api/resolve?q=        # any coordinate format, or a built-in name

# static twins, same shapes
/data/<slug>/frames.json   /data/<slug>/object.json
/data/validation.json      /data/suspects.json

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
| clock | `months` / `hours` | `months` | `months`: one median stack per visit. `hours`: single exposures in time order |
| fov | arcmin | 5 (months), 20 (hours) | Allowed: months 5 or 15; hours 20 or 60. Sampling as in the summary |
| det | 1–6 | 2 | Detector |

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
| epochs[].n_exposures | Exposures in the stack (1 for an exposure) |
| epochs[].flag_frac | Share of frame pixels hit by a mask bit (contracts/flags.md), 0–1 |
| epochs[].sat_frac | Share of frame pixels with OVERFLOW or NONLINEAR, 0–1 |
| epochs[].data_url, scale, zero | The frame data, see below |
| gaia | Gaia DR3 stars in the frame at epoch J2016.0 (MJD 57388.5). The browser moves each one to an epoch's `mjd_mid` with `pmra`, `pmdec`. `parallax` and `g` may be null |
| known | Known objects predicted at an epoch's `mjd_mid`; `epoch_id` is that epoch's `id`. `v` is the V magnitude, or null. `source`: `skybot` (IMCCE SkyBoT, from the Earth's centre), `mpc` (Minor Planet Center orbits propagated with kete, from SPHEREx's own position when the photo header gives it), `most` (IRSA MOST), `irsa-3i` (IRSA's 3I/ATLAS photo list) |
| data_release | Collections searched, e.g. `"DR1+QR3"` |
| archive_checked | Date of the IRSA search, `YYYY-MM-DD` |

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
| spectrum[] | Sorted by `um`. `flux_ujy`, `err_ujy` in µJy; `det` 1–6; `mjd` = the exposure's MJD-AVG; `visit` = visit number; `saturated` = OVERFLOW or NONLINEAR in the core |
| measure | Motion between the first and last visit stack, or null with fewer than two visits |
| measure.shift_arcsec, pa_deg | Size and direction of the shift; position angle east of north |
| measure.dt_yr | Time between the two visits' `mjd_mid`, years |
| measure.rate_arcsec_yr | shift_arcsec / dt_yr |
| measure.sigma_arcsec | 1σ error of the shift (V3): the 2-D RMS of the reference stars' residual shifts, after the median frame-to-frame offset is removed. 3 × sigma is the slowest motion we can see |
| measure.n_ref_stars | Quiet Gaia stars used (G 13–17, motion under 20 mas/yr) |
| measure.predicted_arcsec | Gaia's predicted shift over `dt_yr`, including parallax when Gaia has one (it moves Barnard's Star up to ~1″ between visits), or null without Gaia |

### Proposal: measurement source (awaiting P1)

Add `measure.source` to identify the images actually used by the motion fit. The object endpoint
chooses its detector and visit pair independently of the player's clock, detector and field.
Changing the player does not recompute the measurement. Never infer its source from the current
frames. This is an additive proposal; existing clients may ignore it.

```
source: { det, visits:[first,last], mjd:[first,last], n_exposures:[first,last],
          fov, pixel_arcsec, core_filled:[first,last] } | null
```

| field | type and meaning |
|---|---|
| det | Integer 1–6, the single detector used for both measurement stacks |
| visits | Two increasing visit numbers from the position's canonical visit grouping |
| mjd | Two increasing UTC MJDs actually passed to the fit: median MJD-AVG of each stack's usable exposures |
| n_exposures | Two positive integers: exposures actually included in those stacks |
| fov | Measurement grid's nominal field size in arcmin, independent of the displayed field |
| pixel_arcsec | Measurement grid's sampling in arcsec per pixel |
| core_filled | Two booleans: whether a model filled the saturated core in each measurement image |

For a sourced measurement, `dt_yr = (source.mjd[1] - source.mjd[0]) / 365.25` (within rounding).
This clarifies the `dt_yr` row above: the fit uses stack median exposure times, which can differ
from the extrema midpoint used by `frames.epochs[].mjd_mid`.

New API results and newly exported static twins include `source` whenever `measure` is non-null.
If provenance is unavailable, use `source: null`; older cached/static results may omit the key.
Readers treat omission and null as unknown, display that limitation, and keep the measurement
available without guessing the detector, visits or dates. `measure: null` remains unchanged.
This proposal does not require recomputing existing static measurements or changing their numbers.

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
| `/data/<slug>/frames.json` + frame files | scripts/precompute (P2) | `GET /api/frames` for the target's own clock and field. It may hold epochs for several detectors; the client filters by `det` |
| `/data/<slug>/object.json` | scripts/precompute (P2) | `GET /api/object` |
| `/data/validation.json` | scripts/validate (P1) | below |
| `/data/suspects.json` | pipeline export (P1) | below |

In the repo these live in `web/public/data/`; Vite copies them to `web/dist/data/`.

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
  suspects:[{ id, ra, dec, mjd, clock:"hours", det, evidence:"two_exposures"|"two_detectors",
              n_dots, snr, flux_ujy, cell, nearest_known_arcsec|null }] }
```

Movers with no known-object match, SkyBoT or MPC (contracts/dots.md). Written by the pipeline's
`suspects` step (`results/<zone>/suspects.json`, copied to `web/public/data/suspects.json`).
They are also written to Firestore `candidates` with `source: "pipeline"`.

| field | type | meaning |
|---|---|---|
| zone | string | Pipeline zone, `pilot` or `hunt` |
| id | string | Unique in the file. The pipeline writes `<zone>-<cell>-<minute>` (minute = round(mjd × 1440)); treat it as opaque |
| ra, dec | float, deg | Mean position of the mover's dots |
| mjd | float, MJD | Mean MJD-AVG of the mover's dots |
| det | int 1–6 | Detector of its highest-S/N dot |
| evidence | string | `two_exposures` (paired inside one session) or `two_detectors` (D1+D4, D2+D5, D3+D6 at the same moment) |
| n_dots, snr, flux_ujy | int, float, float µJy | Dots in the mover, their highest S/N and median brightness |
| cell | integer, no unit | The 6″ sky cell of (`ra`, `dec`), exactly contracts/dots.md's `cell` (`band × 10 000 000 + col`). An id for grouping, not a quantity: neighbouring cells are not neighbouring numbers. At most ~1.08 × 10¹², so a JSON number is exact in JavaScript (below 2⁵³); never a string |
| nearest_known_arcsec | float, arcsec, or null | Distance to the nearest known object predicted in the same session, moved to `mjd`; null if none within 600″. Suspects have none within 30″ |

## Firestore: flags and votes (no API endpoint)

| collection | fields |
|---|---|
| candidates | `ra, dec, mjd, clock, source: "user" \| "pipeline", created` |
| votes | `candidate_id, answer: mover \| changing \| junk \| unsure, ts, voter` |

- Rules are **create-only**: anyone may read and create; nobody may update or delete. Each create
  must have exactly these fields, with the right types and allowed values.
- `ra`, `dec` in degrees, `mjd` of the frame that was clicked, `clock` `months` or `hours`.
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

## Changes from v2 (14 Nov 2026)

- suspects.json: `cell` is an integer (the 6″ cell of contracts/dots.md), field table added.
- Firestore `candidates`: new field `created` = `serverTimestamp()` (rules and web follow in one PR).
- validation.json: the eleven checks V1–V11 listed with their units.
- "Proposal" removed where server/, scripts/ and web/src/api/types.ts already implement the shape:
  `det` default 2, epoch ids, the frame data layout and raw 0 = no data, `/api/gaia`, `/api/known`,
  `/api/resolve`, several detectors in a static frames file, validation.json, suspects.json.
  Still open: `known_ucd` (the server returns null so far).
