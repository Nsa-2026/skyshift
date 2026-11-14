# contracts/api.md: endpoints, static twins, visits, Firestore (v3.1)

Owner: P1. Implemented by server/ and scripts/precompute/ (P2). Read by web/src/api (P3) and
web/src/blink, web/src/vote (P4). Version 3, 14 Nov 2026 (changes from v2 at the end; v3: P1's answers to the P2 engine PR:
`dt_yr` from median times, per-epoch `center`, the static index and per-set files, live caps,
targets `dets`, `spectrum_reason`; v3.1: `/data/candidates.json` for the community review pages).

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
    data_release, archive_checked }
GET /api/object?ra=&dec=
→ { name|null, gaia|null, known_ucd|null,
    spectrum:[{ um, flux_ujy, err_ujy, det, mjd, visit, saturated }],
    spectrum_reason?,                                # why spectrum is empty, else null
    measure:{ shift_arcsec, pa_deg, dt_yr, rate_arcsec_yr, sigma_arcsec,
              n_ref_stars, predicted_arcsec|null } }
GET /api/gaia?ra=&dec=&r=
GET /api/known?ra=&dec=&r=&mjd=
GET /api/resolve?q=        # any coordinate format, or a built-in name

# static twins, same shapes
/data/<slug>/frames.json   /data/<slug>/object.json
/data/<slug>/index.json    /data/<slug>/frames_<clock>_<fov>.json
/data/validation.json      /data/suspects.json
/data/candidates.json      /data/cand/<id>/frames.json   # candidates to review

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
| measure | Motion between the first and last visit stack, or null with fewer than two visits. The detector is the one with the best unsaturated signal, limited to the target's `dets` when targets.json gives them (below) |
| measure.shift_arcsec, pa_deg | Size and direction of the shift; position angle east of north |
| measure.dt_yr | Time between the **median exposure times** (MJD-AVG) of the two visit stacks, years. Not `mjd_mid`: a per-pixel median of a slowly moving star sits where the star was at the median time (Barnard's Star, visit 3: `mjd_mid` is 14.6 days, 0.41″, after the median) |
| measure.rate_arcsec_yr | shift_arcsec / dt_yr |
| measure.sigma_arcsec | 1σ error of the shift (V3): the 2-D RMS of the reference stars' residual shifts, after the median frame-to-frame offset is removed. 3 × sigma is the slowest motion we can see |
| measure.n_ref_stars | Quiet Gaia stars used (G 13–17, motion under 20 mas/yr) |
| measure.predicted_arcsec | Gaia's predicted shift over `dt_yr`, including parallax when Gaia has one (it moves Barnard's Star up to ~1″ between visits), or null without Gaia |

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
              grade:"A"|"B"|"C", reasons:[<gate>…], near_source_arcsec|null }] }   # added 14 Nov
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
| grade | string | `C`: fails a gate or is catalogued (`reasons` says which); kept in the file, shown greyed with its reasons. `B`: passes every gate; its motion is not shown (most suspects are 2 dots of one session, and at S/N 7–9 the position error exceeds the motion between exposures): an "unexplained one-session source". `A`: B and motion ≥ 5σ over ≥ 2 sessions. A needs a multi-session linker the pipeline doesn't have yet: no suspect is graded A |
| reasons | string[] | The gates it fails, in this order (empty for A and B): `not_vetted` (no dot data to vet: an old movers table, or a dot without a pixel position), `hot_pixel` (a dot on a detector pixel, ±1 px, where the zone's movers have dots in ≥ 3 exposures or its free dots in ≥ 5: hot pixels NASA's HOT bit misses), `bad_column` (≥ 3 free dots of that exposure and detector within ±0.5 px in x), `edge` (a dot within 6 px of the detector edge), `masked_peak` (a mask bit of contracts/flags.md at a dot's peak, OVERFLOW/NONLINEAR aside), `sharp_outlier` (a dot sharper than a star: √(a·b) < 0.7 × the detector's stellar value at its S/N, with OUTLIER at the peak; OUTLIER alone never counts), `flux_ratio` (brightest dot > 4× the faintest within one detector), `red_photo` (every dot from a photo graded red by the photo QA), `2mass` (a 2MASS PSC source within 6″). Thresholds: pipeline config `VET_*` |
| near_source_arcsec | float, arcsec, or null | Nearest catalogue (Gaia, CatWISE, 2MASS) or fixed source within 10″: a possible blend or PSF wing. A warning, not a gate; null if none within 10″ |

### candidates.json

What the pipeline found, for people to review (web/DESIGN.md, "Product"). Written by the pipeline's
exporter (`cd pipeline && uv run skyshift-candidates`, pipeline/src/skyshift_pipeline/candidates.py) from
`results/<zone>/` of every finished zone; static frames for the most promising by
scripts/candidates/frames.py. Type `CandidatesResponse` in web/src/api/types.ts. Example (a real sample):
fixtures/api/candidates_sample.json (fixtures/api/candidates.json is an older hand-made pilot sample that
web tests still use; it predates `zones` and the high_pm id rule).

```
{ generated, data_release, zones:[zone…], counts:{ mover, high_pm, spectral },
  candidates:[{ id, kind:"mover"|"high_pm"|"spectral", ra, dec, mjd, clock:"hours"|"months", det, zone, zones,
                score, snr, flux_ujy, rate_arcsec_h, pm_mas_yr, pm_err_mas_yr, pa_deg,
                nearest_known_arcsec, gaia_match, catwise_match, galactic_b,
                labels:[label…], reasons:[code…], why, flags:[flag…], frames_url|null }] }
```

| field | type | meaning |
|---|---|---|
| generated | string | Time of the export, ISO 8601 UTC (`2026-11-14T15:20:00Z`) |
| data_release | string | Collections the zones' photos came from, e.g. `"DR1+QR3"` |
| zones | [string] | Zones searched, in the order used: the larger first (`["hunt", "pilot"]`). A zone appears even when all its objects were already listed from an earlier one |
| counts | {kind: int} | Candidates of each kind in the file (all three keys, 0 if none) |
| candidates | [object] | Sorted by zone (in `zones` order), then kind (mover, high_pm, spectral), then `score` descending |

Each candidate: every field is present; a missing value is `null`, never omitted.

| field | type | meaning |
|---|---|---|
| id | string | Unique in the file and stable across runs; Firestore `votes.candidate_id`. Treat as opaque. mover: the pipeline's suspect id `<zone>-<cell>-<minute>` (suspects.json); high_pm: `<zone>-pm-<source_id>` (fit) or `<zone>-pm-<source_id>-<source_id_b>` (pair); spectral: `<zone>-sp-<source_id>`. `source_id` is link's id from the source's median position (contracts/dots.md) |
| kind | string | `mover` "Fast mover": moves within hours (an uncatalogued asteroid or comet?). `high_pm` "Moving star": moves between visits (a nearby star or brown dwarf?). `spectral` "Odd spectrum": molecule bands worth a look |
| ra, dec | float, deg | mover: mean of its dots (as suspects.json). high_pm: the pipeline's position: fit, at the fit's epoch; pair, the first visit's source. spectral: the source's median position |
| mjd | float, MJD | mover: mean MJD-AVG of its dots. high_pm: fit, the astrometric fit's epoch; pair, the first visit's mean time. spectral: mean MJD-AVG of the source's dots |
| clock | string | `hours` for mover, `months` for high_pm and spectral: the clock to blink it on |
| det | int 1–6 | mover: detector of its highest-S/N dot. high_pm, spectral: the detector where the source's spectrum has the highest S/N (pair: the first visit's source) |
| zone | string | Pipeline zone it came from, one of `zones` |
| zones | [string] | Every zone that found it, `zone` first: `["hunt", "pilot"]` for an object the pilot found too (as suspects.json's `zones`). **Not yet in types.ts** (proposal: `zones?: string[]`); readers that don't know it ignore it |
| score | float 0–1 | How promising: **higher = more promising**, in 0..1, sorted descending. Defined per kind below; where the pipeline's own score is lower-is-better (high_pm) the exporter inverts it (e^(−s/0.2)). Never null in the export (the type allows it) |
| snr | float or null | mover: its highest dot S/N. high_pm, spectral: S/N of the spectrum in `det` (its 17 channels' S/N added in quadrature) |
| flux_ujy | float µJy or null | mover: median of its dots. high_pm, spectral: median of the spectrum's 17 channels in `det` |
| rate_arcsec_h | float ″/h or null | mover: apparent rate from a weighted straight-line fit of its dots' positions against time (dot positions re-read from dots/). null for `two_detectors` (one moment) and other kinds |
| pm_mas_yr, pm_err_mas_yr | float mas/yr or null | high_pm only: total proper motion and its 1σ error (the pipeline's `pm`, `pm_err`) |
| pa_deg | float deg or null | Direction of motion, east of north, 0–360: high_pm from (pmra, pmdec); mover from its dots (null without a rate). null for spectral |
| nearest_known_arcsec | float ″ or null | mover only: as suspects.json, the nearest known asteroid or comet predicted in the same session, moved to `mjd`; **null = none within 600″**. Always null for high_pm and spectral (not checked) |
| gaia_match | bool | A Gaia DR3 source sits here (its motion may still disagree: see `reasons`). mover: false (suspects have no Gaia star within 6″; see `gaia_unchecked`). high_pm: the pipeline's `status` is `gaia` (either side matched). spectral: classify's Gaia match |
| catwise_match | bool | A CatWISE2020 source sits here. mover: false (none within 6″). high_pm: a CatWISE counterpart on either side. spectral: crossids has one |
| galactic_b | float deg | Galactic latitude (astropy, ICRS to Galactic). \|b\| < 10 is the crowded Milky Way plane |
| labels | [string] | Filter labels, vocabulary below; order as listed there. [] for movers |
| reasons | [string] | Codes, vocabulary below: why it was listed and what to be careful about |
| why | string | One plain English sentence: why it is on the list (examples below) |
| flags | [string] | Artefact flags near it (contracts/flags.md record-only bits), lower case: `outlier`, `crosstalk`, `ghost`, `ghost_fpa`, `ghost_ext`, `bloom`, `halo`. mover: any of its dots' 3×3 `flags`; high_pm, spectral: set on ≥ 25% of the source's dots (either source of a pair) |
| frames_url | string or null | Static blink frames, resolved against the URL of candidates.json like `data_url` (`new URL(frames_url, jsonUrl)`): `cand/<id>/frames.json`, a `GET /api/frames` response whose `data_url`s resolve against that file. null: call `/api/frames?ra=&dec=&clock=&fov=&det=` live |

**Static frames** (`/data/cand/<id>/`, scripts/candidates/frames.py) exist for the highest-scoring of
each kind (default 25 movers, 20 high_pm, 15 spectral; under 150 MB in all). mover: hours clock, 20′
field, `det` (and its simultaneous partner for `two_detectors`), the exposures within 0.3 days of `mjd`
(its own session and the next ones), at most 10 per detector, nearest in time first. high_pm and
spectral: months clock, 5′ field, `det`, every exposure of every visit (as the featured targets).
Several detectors may share one file (sorted by `mjd_mid`, then `det`); the client filters by `det`.

**Which objects.** mover: every row of `results/<zone>/suspects.parquet` (suspects.json's rules). high_pm:
every row of `highpm_candidates.parquet`, vetted or not; the pipeline's vetting reasons become `reasons`.
spectral: sources of `classes.parquet` with a band label or `brown_dwarf_like`, and either no Gaia match or
a Gaia parallax ≥ 20 mas at ≥ 5σ (closer than 50 pc); the 300 highest-scoring per zone. An object already
listed from an earlier zone is not listed again, and the earlier entry's `zones` gets the later zone: movers
within 6″ and 2 minutes (suspects.json's merge), high_pm and spectral within 4″. The pilot lies inside the
hunt zone, which was run on the same photos: the hunt re-found all 27 pilot movers (the same ids as
suspects.json), so the pilot adds only objects the hunt didn't list.

Nothing is dropped for being doubtful: doubts become `reasons` and lower the `score`. The "check first"
codes (show them as cautions): `known_object_near`, `motion_not_significant`, `bad_pixels_near`,
`outlier_flag`, `detector_excess`, `repeat_position`, `gaia_unchecked`, `catwise_unchecked` (mover);
`short_span`, `few_dots`, `bad_fit`, `over_pm_cap`, `gaia_pm_disagrees`, `catwise_pm_disagrees`,
`at_control_level`, `implausible_pm` (high_pm).

**score** (0–1; the formulas are in the exporter and its tests):

| kind | score |
|---|---|
| mover | (1 − e^(−snr/15)) × evidence (`two_exposures` 1, `two_detectors` 0.6) × 0.85 with only two dots × known term (1 with no known object within 600″, else 0.5 rising linearly to 1 between 30″ and 300″) × 0.6 with `outlier_flag` × 0.7 with another record-only flag × 0.8 with a masked pixel in a dot's 3×3 × 0.85 when the motion is not significant × 0.6 with `detector_excess` × 0.3 with `repeat_position` |
| high_pm | e^(−s/0.2) × the vetting reasons' weights × 0.6 for a Gaia star (`status` gaia). s is the pipeline's own score, lower = better: 1/(pm/pm_err) for a fit, \|ln flux ratio\| + spectrum-shape rms for a pair. Weights: `short_span`, `few_dots`, `bad_fit` 0.5; `gaia_pm_disagrees`, `catwise_pm_disagrees` 0.4; `over_pm_cap`, `at_control_level` 0.3; `implausible_pm` 0.01 (so these sort last) |
| spectral | max over its labels of weight × (1 − e^(−σ/10)), σ = the band's significance. Weights: `brown_dwarf_like`, `ch4` 1; `h2o_ice`, `co2_ice`, `co_ice` 0.8; `h2o_vapour` 0.6; `co_23` 0.4; `pah_33` 0.3 |

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
| implausible_pm | high_pm | Faster than 12″/yr between visits: no star moves that fast (Barnard's Star, the fastest known, does 10.4″/yr), so it is almost certainly two different sources. In practice every pair (3–120″ apart about half a year apart; hunt 32–269″/yr) and the fits above 12″/yr; none is `vetted`. Hunt: 82 of 997 (55 pairs, 27 fits); pilot: 15 of 167 (12 pairs, 3 fits) |
| no_gaia | spectral | No Gaia DR3 source here |
| gaia_nearby | spectral | Gaia has it, with a parallax of 20 mas or more: closer than 50 pc |
| class_star, class_galaxy, class_unsure | spectral | classify's `class` (unsure: point-like without a Gaia star) |
| variable | spectral | Its brightness changes between visits (sources `variable`) |

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
check."

## Firestore: flags and votes (no API endpoint)

| collection | fields |
|---|---|
| candidates | `ra, dec, mjd, clock, source: "user" \| "pipeline", created` |
| votes | `candidate_id, answer: mover \| changing \| junk \| unsure, ts, voter` |

- Rules are **create-only**: anyone may read and create; nobody may update or delete. Each create
  must have exactly these fields, with the right types and allowed values.
- `ra`, `dec` in degrees, `mjd` of the frame that was clicked, `clock` `months` or `hours`.
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
