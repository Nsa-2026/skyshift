# scripts/validate: V1–V11 and validation.json

Data gathering (metadata), the checks we run here (V1, V4, V8 spectra) and the file the scoreboard reads
(`web/public/data/validation.json`, contracts/api.md). Whole photos are never downloaded: V1 and
V4 read small boxes from NASA's public S3 copy with HTTP range reads. Python 3.12, run with uv from
this folder (`pyproject.toml` + `uv.lock`). Archive state: 14 Nov 2026 (SPHEREx DR1 + QR3).

| script | what it does | output |
|---|---|---|
| `targets.py` | Gaia DR3 astrometry (epoch J2016.0) for the 5 challenge targets, from the ESA Gaia Archive TAP by `source_id`. WISE 0855−0714 is not in Gaia: published astrometry, `source: "literature"` (Luhman & Esplin 2016, AJ 152, 78, Tables 2 and 4). | `data/targets.json` (committed) |
| `photolists.py` | Every SPHEREx Level 2 image covering each target (IRSA SIA, `spherex_dr1` + `spherex_qr3`, searched at the position moved to 2025.8), and every image of comet 3I/ATLAS (IRSA's 3I/ATLAS table, matched to DR1 by SIA). Adds `mjd_mid`, `visit`, `session`, the object's position at `mjd_mid` and its distance from the image edge. | `data/photolists/<slug>.csv` + `summary.json` (committed, < 0.4 MB each); `gs://nasa-glazing-data/photolists/` |
| `most_asteroids.py` | IRSA MOST (`catalog=spherex`, `output_mode=Brief`, by number) for asteroids 1–60: every SPHEREx image and MOST's predicted position, V magnitude, distances. Sequential, 1.5 s apart, cached in `.cache/most/`, one retry, failures logged. | `data/most_1_60.csv` (gitignored, ~8 MB), `data/most_1_60_summary.json` (committed); `gs://nasa-glazing-data/results/` |
| `v1_targets.py` | V1: per target, visit and detector, a median stack of up to 5 photos (41 px boxes; every photo for WISE 0855) and a 5σ source within 2 px of the predicted position. | `data/v1_targets.json` |
| `v4_asteroids.py` | V4: asteroids 1–60 at MOST's predicted position in up to 5 photos each (81 px boxes, D2–D3), absent from a reference stack of the same sky 0.25–12 days away; control 3′ away. | `data/v4_asteroids.json`, `data/v4_photos.csv` |
| `v8_spectra.py` | V8 spectra half: every pilot star's 102-channel spectrum (pipeline `sources.parquet`) averaged over 2MASS Ks and WISE W1, against the catalogue magnitudes in `crossids.parquet`. | `data/v8_spectra.json` |
| `build_validation.py` | Collects V1–V11 into the contract's `validation.json`: V1/V4 from here, V2/V3 from each target's `object.json`, V5/V9/V10 from the pipeline's reports, V8 from `v8_spectra.json` + `data/demo_check.json` (`demo_check.py --json`; `--demo-check PATH` overrides), V11 from `scripts/science`. Missing inputs → `pending`. Status rules are small pure functions (`v1_status` … `v8_status`, tested in `tests/test_build_validation.py`); every note ends with the row keys. Also copies the zone's `suspects.json` when the pipeline wrote a real one in the contract shape. | `web/public/data/validation.json`, `web/public/data/suspects.json` |
| `_boxes.py` | Box reads from S3 (cached in `.cache/boxes/`), reference stacks resampled through the WCS, `source_near` (with the saturated-core fit). | |
| `_common.py` | Shared helpers: linear proper motion, detector from `energy_bandpassname`, the visit/session rule (re-exported from `skyshift_core.visits`), polygon edge distance, `gcloud storage cp`. | |

```
cd scripts/validate
uv run python targets.py                 # ~5 s
uv run python photolists.py              # ~1 min; --no-upload, --only barnard,3i-atlas
uv run python most_asteroids.py          # ~10 min cold (cached after); --first/--last, --no-upload
uv run python v1_targets.py              # ~650 boxes, ~5 min cold; --only barnard
uv run python v4_asteroids.py            # ~1,700 boxes, ~25 min cold from Europe; --first/--last/--per
uv run python v8_spectra.py              # pilot tables from the bucket (~30 MB, gcloud); --zone hunt later
uv run python build_validation.py --site-data ../../web/public/data --fetch-results pilot
                                         # V8 speed from data/demo_check.json (--demo-check PATH overrides)
uv run pytest                            # offline
```

`v4_asteroids.py` needs `data/most_1_60.csv` (`gcloud storage cp gs://nasa-glazing-data/results/most_1_60.csv data/`).

Uploads need `gcloud` logged in to project `nasa-glazing`.

## Photo-list columns

`slug, collection, obs_id, detector (1–6), energy_bandpassname, em_min_um, em_max_um, t_min, t_max,
mjd_mid ((t_min+t_max)/2), visit, session, obj_ra, obj_dec (target at mjd_mid: Gaia/literature
position + linear proper motion; for the comet NASA's comet_ra/comet_dec), obj_pos_source,
edge_px (6.15″ pixels from the image edge, > 0 inside), s_ra, s_dec, access_url (direct IBE FITS
URL), s3_bucket, s3_key, s3_region, cloud_access (raw JSON), access_estsize_kb, obs_publisher_did,
qr2_datalink (comet only)`. One row per image (exposure × detector); an (obs_id, detector) in both
DR1 and QR3 keeps DR1 (none overlapped).

Visit and session ids come from the contracts/api.md rule in `skyshift_core.visits` (core/), which
`_common.py` imports: one copy of the rule for the pipeline, the server and these scripts.

## Row counts (14 Nov 2026)

| slug | images | exposures | visits | sessions per visit | dates | detectors |
|---|---|---|---|---|---|---|
| barnard | 358 | 181 | 5 | 19, 5, 20, 4, 25 | 2025-08-15 → 2026-09-22 | D1–D6 |
| epsindib | 458 | 238 | 5 | 19, 5, 48, 21, 26 | 2025-04-24 → 2026-10-02 | D1–D6 |
| wise0855 | 258 | 129 | 3 | 5, 28, 28 | 2025-05-05 → 2026-05-14 | D1–D6 (DR1 only, no QR3) |
| rwaur | 335 | 168 | 3 | 20, 18, 19 | 2025-09-01 → 2026-09-16 | D1–D6 |
| rdra | 539 | 273 | 9 | 15, 12, 5, 16, 14, 8, 12, 5, 16 | 2025-05-16 → 2026-07-16 | D1–D6 |
| 3i-atlas | 259 | 133 | 2 | 30, 15 | 2025-08-01 → 2025-12-15 | D1–D6 |

Per-visit dates, exposures and detectors: `data/photolists/summary.json`.

**MOST, asteroids 1–60:** all 60 answered, all 60 have SPHEREx images: 18,448 images =
9,308 exposures (104–229 per asteroid, median 149), 14,199 DR1 + 4,249 QR3. V 6.98–15.18
(median 12.67); 43 asteroids have images at V 12–15 (11,776 images). Derived sky rates 0.5–72″/h,
median 32″/h. MOST clamps the window to 2025-04-11 → 2026-10-16. Brief mode has no rates: the
`rate_*` columns come from MOST's own positions in the nearest exposure (empty for 511 images).
Still to check: whether MOST's `ra_obj/dec_obj` is for the exposure start or midpoint (≤ 0.5″ at 30″/h).

## Things to know

- **Visit rule fixed (14 Nov).** The first rule cut seasons over 45 days into 30-day bins, which left
  4–16-exposure tail visits with one detector pair (Barnard's Star, Epsilon Indi B). The rule now
  cuts only above 75 days, into equal parts. Barnard's Star has 3 visits of 60–61 exposures, all six
  detectors; Epsilon Indi B 4; R Draconis 7 (smallest 18 exposures). Tables above are before the fix;
  `data/photolists/summary.json` has the current ones.
- **WISE 0855 has no QR3 images** yet (its next season is not released), and its first visit
  (May 2025) has only 3 D6 and 8 D5 images. The two full visits are Oct–Dec 2025 and Apr–May 2026.
- **R Draconis** (near the north ecliptic pole) is seen almost continuously and splits into 9
  visits; 9 of its images have the star within 5 px of the edge.
- **3I/ATLAS:** 258 of 259 images exist in DR1. `2025W32_2D_0091_2` D6 is in neither DR1 nor QR3;
  its row keeps the QR2 datalink and has no S3 key. The visit rule is meaningless for a moving
  object; use `session` (45 sessions) for the Hours clock.
- RW Aur A and B (1.4″ apart) are one blend at 6.15″ pixels; Epsilon Indi Ba+Bb likewise.
- J2016.0 is MJD 57388.5; contracts/api.md says 57388.0 (0.014″ for Barnard's Star; harmless).
- IRSA TAP `spherex.obscore` has empty `cloud_access` and null sizes; SIA has both. SIA lookup by
  `publisher_did` works but took ~130 s for two images, so the comet uses position + time SIA.

## Checks run here (14 Nov)

**V1, five test objects: 5 of 5 found where predicted, in 20 of 20 visits.** Median offsets
0.15–0.25 px per target. 85 of 86 expected stacks recovered; the one miss is WISE 0855's first-visit
D6 stack (3 photos). R Draconis 22 of 22 in all six detectors, after the masked-core fix below (it
missed all 4 D4 and 2 of 3 D5 stacks before). WISE 0855−0714 is found in D5–D6 in all three visits
(aperture S/N 6.9–11, 0.15–0.58 px), including its first visit's 8 D5 photos (S/N 9.5, 0.39 px). In D1–D4 a background star lies
1–2 px from WISE 0855; those stacks are not counted (WISE 0855 is expected only in D5–D6).

**V4, asteroids 1–60: all 60 re-found where MOST predicts them.** 246 of 300 photos (5 per
asteroid, D2–D3, V 7–15); median offset 0.13 px (0.77″). Control 3′ away: 2 of 300 photos (0.7%),
2 of 60 asteroids in any of their 5 photos. 1,736 boxes, ~30 min cold from Europe. Of the 54
photos not counted, 51 have a source within 3 px of the spot in the reference stack (a static star
there, so the detection can't be told apart) and 3 more have no detection.
"Absent from the stack" means nothing within 3 px, not 2: with 2 px the control hit 6 of 300, five
of them real static stars at 1.4–2.0 px in the photo that the resampled stack put just beyond 2 px
(S/N 12–97, no artefact flags).

**V8, spectra: SPHEREx fluxes agree with 2MASS Ks to 0.7% and WISE W1 to 2.6%.** Pilot stars of
10–13 mag matched one-to-one to Gaia, 2MASS and CatWISE, not saturated or variable: SPHEREx/Ks
0.993 (scatter 2.9%, 3,918 stars), SPHEREx/W1 1.026 (scatter 1.8%, 4,240 stars). This also checks
the MJy/sr → µJy conversion on thousands of stars. Fainter than 13 mag the scatter grows (4.5–12%)
and W1 drifts to 1.07 at 14–15 mag. These numbers come from the pilot tables regenerated after dots v4
(#52, bucket copies of 13:06–13:09Z). The first run gave Ks 1.010 and W1 0.994 on the same 45,189 sources
and the same star counts. Every magnitude bin moved by the same amount (Ks −1.7%, W1 +3.3%), so the
new tables changed the D3 and D4 fluxes, not the matching. `v8_spectra.py` skips tables that are
already in `.cache/results/<zone>/`: delete the local copies first when the bucket's are newer
(`gcloud storage ls -l`). When the hunt zone finishes, rerun `v8_spectra.py --zone hunt` and
`build_validation.py --fetch-results hunt`.

**V8, speed (14 Nov, live site): fail, the API saturated during the test.** `data/demo_check.json` is
`uv run scripts/demo_check.py --random 10 --json scripts/validate/data/demo_check.json`, run from the
repo root at 13:26–13:37Z (seed 297754). `build_validation.py` reads it without a flag. The next official
run, after the API speed-up is deployed, replaces the file. The 8 featured targets load from /data with the
first frame in 0.04–0.53 s. Only 2 of the 20 random /api/frames requests (10 coordinates × 2 clocks,
one at a time) answered: 28.7 s (months) and 8.1 s (hours), both for the last coordinate. The other 18
failed: 14 HTTP 429 "Rate exceeded." and 4 timeouts at 60 s. Only 1 of 10 /api/object requests
answered (17.8 s). An earlier run at 13:12Z (seed 849302) failed the same way: 2 of 20, 32.1 and
33.0 s. Cloud Run's request logs show the cause:
- A request still running after about 60 s reaches Cloud Run again 1–3 more times, about a minute later.
  `demo_check.py` doesn't retry, so the copies come from the hosting layer.
- Each copy keeps computing up to Cloud Run's 300 s timeout. With at most 2 instances × concurrency 8
  and CPU-bound work, the copies fill the service, and every other request gets an immediate 429.
- Another demo check (the run behind #69) and curl calls hit the API during both runs.
- Revision `skyshift-api-00002` went live at 13:35:08Z. After that, /api/frames took 7–28 s and
  /api/object 17–43 s; both successes above are from that revision.

What we learned:
- **Saturated cores bias sep's centroid by 1–2 px.** Barnard's Star in D6: sep put it 1.75 px
  from Gaia while Gaia field stars in the same box agreed to 0.03 px. The masked core leaves a
  ring, and a windowed centroid of a lopsided ring is off. `source_near(wings=True)` fits a
  Gaussian to the unmasked wings (13 × 13 px stamp started at the prediction, ±2 px bounds), or
  takes the centre of the masked core when too few wing pixels are left (Barnard D3–D4).
  With it, Barnard's Star is at 0.19 px median in all 18 stacks.
- **R Draconis saturates so hard in D4–D5 that sep sees only a rim around the masked core.** Its
  wings (10–13 px out) are 13× Barnard's in D4 and 26× in D5 (6–8× in D1–D3). The pipeline flags a
  round hole of TRANSIENT + SUR_ERROR + OVERFLOW (all mask bits; BLOOM on nearly every pixel, IMAGE
  NaN on the 17–38 px OVERFLOW core in D4) of 134–266 px in D4 (equivalent radius 6.5–9.2 px) and
  120–162 px in the missed D5 stacks (6.2–7.2 px), against 32–81 px (3.2–5.1 px) for Barnard's Star in
  D3–D4. 71–100% of the 13 × 13 fit stamp is masked; the Gaussian wing fit never leaves its start, even
  on a stamp scaled to the hole. The only sep detections are rim fragments (aperture S/N 12–108)
  0.7–1.1 px outside the hole in photos (up to 1.7 px in stacks), so 6.0–9.1 px from the prediction:
  beyond the old gate of radius + 4 = 6 px, and the saturated-core branch never ran ("none"). Not the
  cause: the box (hole and rim fit inside 41 px), the S/N gate, image edges (all used photos ≥ 25 px
  in), the stack's OVERFLOW/NONLINEAR union (27–54 px per D4 photo, inside the hole), or the prediction. `source_near` now also takes a detection within 2 px of the masked
  core; the position is then the core's centre, 0.05–0.57 px from the prediction in the six stacks that
  were missed (0.06–0.63 px in single photos). Nothing else in V1 or V4 changed. The core's centre is
  only geometric: a fit of the real PSF halo (ePSF or a power law, in skyshift_core) would be better.
- **Stack holes grew 1 px on one side (fixed).** `map_coordinates(order=1)` turned a pixel NaN
  when either neighbour it read was NaN, even at weight 0, so every masked pixel of every resampled
  photo spread to a neighbour: holes grew one pixel towards +x or −x, and pixels next to any flag
  became medians of fewer photos. R Draconis visit 3 D1 (one photo) gave 1.35 px from its stack and
  0.25 px from the photo itself. `reference_stack` now resamples the good pixels and a validity weight
  separately and masks only where bad pixels carry more than half the weight. Effects:
  - V1: R Dra visit 3 D1 1.355 → 0.251 px. WISE 0855 found in 2 more stacks (visit 1 D5, visit 3 D6;
    20 of 20 visits). The peak at the target is unchanged, but the stack's background rms drops 9–17%
    (0.0204 → 0.0170 and 0.0275 → 0.0250 MJy/sr) without the noisier few-photo pixels. Target medians:
    Barnard 0.191 → 0.177, Eps Ind B 0.154 → 0.153, WISE 0855 0.207 → 0.237 (two more stacks), RW Aur
    0.212 → 0.253, R Dra 0.266 → 0.191 px. Stacks now agree better with their own single photos
    (median stack-vs-photos disagreement: Barnard 0.154 → 0.089 px, R Dra 0.267 → 0.228, Eps Ind B
    0.049 → 0.042; RW Aur 0.096 → 0.112, rows move both ways). Two R Dra stack fits get worse
    (visit 3 D2 0.20 → 0.68 px, visit 5 D3 0.42 → 0.70 px, where the photos say 0.15 and 0.12), and visit 2
    D1 stays at 1.36 px (photos 0.13): the stack wing fit on a ring medianed from photos with different
    hole sizes. All still pass.
  - V4: 249 → 246 photos found, still 60 of 60 asteroids; control 3 → 2 photos (1.0% → 0.7%), 3 → 2
    asteroids; median offset unchanged. The 3 lost photos have a real static source within 3 px in the
    references that the old stack hid: a saturated star at asteroids 8 and 51 (OVERFLOW in 2–3 of 3
    references, detected 0.5–2.6 px from the spot, S/N 29–77; the old stack had 19 and 6 px with no
    photo left there), and a faint star at asteroid 42 (seen alone in 2 of 3 references, 2.1–2.2 px, S/N
    12–14; the old stack took a 2-photo mean there). The lost control hit (asteroid 34) is a faint
    static source at 1.5 px in the new stack, matching the photo's detection at 1.43 px.
- **5σ per pixel is too strict for faint targets.** WISE 0855 has aperture S/N 6–10 in D5–D6
  stacks but no pixel at 5σ. V1 uses sep at 2.5σ per pixel and aperture S/N ≥ 5.
- **A stack's mask keeps saturated pixels if any photo saturated** (else a less saturated photo
  leaks into the median core), but leaves other bits (cosmic rays) to the median: masking every
  bad pixel of 25 photos hid WISE 0855.
