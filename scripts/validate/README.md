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
| `build_validation.py` | Collects V1–V11 into the contract's `validation.json`: V1/V4 from here, V2/V3 from each target's `object.json`, V5/V9/V10 from the pipeline's reports, V8 from `demo_check.py --json`, V11 from `scripts/science`. Missing inputs → `pending`. | `web/public/data/validation.json` |
| `_boxes.py` | Box reads from S3 (cached in `.cache/boxes/`), reference stacks resampled through the WCS, `source_near` (with the saturated-core fit). | |
| `_common.py` | Shared helpers: linear proper motion, detector from `energy_bandpassname`, the visit/session rule (local copy), polygon edge distance, `gcloud storage cp`. | |

```
cd scripts/validate
uv run python targets.py                 # ~5 s
uv run python photolists.py              # ~1 min; --no-upload, --only barnard,3i-atlas
uv run python most_asteroids.py          # ~10 min cold (cached after); --first/--last, --no-upload
uv run python v1_targets.py              # ~650 boxes, ~5 min cold; --only barnard
uv run python v4_asteroids.py            # ~1,700 boxes, ~25 min cold from Europe; --first/--last/--per
uv run python v8_spectra.py              # pilot tables from the bucket (~30 MB, gcloud); --zone hunt later
uv run python build_validation.py --site-data ../../web/public/data --fetch-results pilot \
    --demo-check /path/to/demo_check.json
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

Visit and session ids come from a small local copy of the contracts/api.md rule in `_common.py`,
same semantics as `skyshift_core.visits`. **Replace it with `skyshift_core.visits` after p1/core
merges.**

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

**V1, five test objects: 5 of 5 found where predicted, in 19 of 20 visits.** Median offsets
0.15–0.31 px per target. 77 of 86 expected stacks recovered; R Draconis misses 6 of 22, all in
D4 (4) and D5 (2), while D1–D3 find it in the same visits (not yet looked into). WISE 0855−0714 is found in D5–D6 in its two full visits (aperture
S/N 9–10, 0.2–0.6 px); its first visit has only 8 D5 and 3 D6 photos. In D1–D4 a background star lies
1–2 px from WISE 0855; those stacks are not counted (WISE 0855 is expected only in D5–D6).

**V4, asteroids 1–60: all 60 re-found where MOST predicts them.** 274 of 300 photos (5 per
asteroid, D2–D3, V 7–15); median offset 0.13 px (0.77″). Control 3′ away: 6 of 300 photos (2.0%),
5 of 60 asteroids in any of their 5 photos. 1,736 boxes, ~30 min cold from Europe. Of the 26
photos not counted, 22 have a source in the reference stack at the asteroid's spot (a static star
there, so the detection can't be told apart) and 4 have no detection. The control hits are all in
D3, four of six in 2026 W09–W12 (not yet looked into).

**V8, spectra: SPHEREx fluxes agree with 2MASS Ks to 1% and WISE W1 to 0.6%.** Pilot stars of
10–13 mag matched one-to-one to Gaia, 2MASS and CatWISE, not saturated or variable: SPHEREx/Ks
1.010 (scatter 3.0%, 3,918 stars), SPHEREx/W1 0.994 (scatter 1.8%, 4,240 stars). This also checks
the MJy/sr → µJy conversion on thousands of stars. Fainter than 13 mag the scatter grows (6–12%)
and W1 drifts to 1.04 at 14–15 mag. The speed half (first frame < 15 s) waits for the deployed
site: `uv run scripts/demo_check.py --random 10 --json demo.json`, then
`build_validation.py --demo-check demo.json`. When the hunt zone finishes, rerun `v8_spectra.py
--zone hunt` and `build_validation.py --fetch-results hunt`.

What we learned:
- **Saturated cores bias sep's centroid by 1–2 px.** Barnard's Star in D6: sep put it 1.75 px
  from Gaia while Gaia field stars in the same box agreed to 0.03 px. The masked core leaves a
  ring, and a windowed centroid of a lopsided ring is off. `source_near(wings=True)` fits a
  Gaussian to the unmasked wings (13 × 13 px stamp started at the prediction, ±2 px bounds), or
  takes the centre of the masked core when too few wing pixels are left (Barnard D3–D4).
  With it, Barnard's Star is at 0.19 px median in all 18 stacks.
- **5σ per pixel is too strict for faint targets.** WISE 0855 has aperture S/N 6–10 in D5–D6
  stacks but no pixel at 5σ. V1 uses sep at 2.5σ per pixel and aperture S/N ≥ 5.
- **A stack's mask keeps saturated pixels if any photo saturated** (else a less saturated photo
  leaks into the median core), but leaves other bits (cosmic rays) to the median: masking every
  bad pixel of 25 photos hid WISE 0855.
