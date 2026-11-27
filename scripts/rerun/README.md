# The photo rerun on the PC (scripts/rerun)

One read of every SPHEREx photo in the sky zones with extraction v2, the session stacks fused into the same worker
and forced photometry with the red layer (docs/extraction-v2.md, docs/session-stacks.md §8). Each photo is streamed
from NASA's public S3 (free) into memory and never saved. Nothing here reads or writes gs://.

```bash
cd ~/skyshift
R="uv run --project pipeline python scripts/rerun/rerun.py"
$R plan --workers 20                 # dry run: photos, download, extra reads, days, disk; writes ~/skyshift-data/rerun/plan.json
$R bench --workers 12                # the benchmark sessions (bench_targets.json) into ~/skyshift-data/rerun-bench/
$R plan --workers 20                 # again: now with the benchmark's numbers
$R run --workers 20                  # the full run, zone by zone; run it again to resume
```

Before `bench` and `run`, the data root (`~/skyshift-data`, or `--data`) needs:

| what | how | cost |
|---|---|---|
| `photolists/s3index/` and `photolists/zones/` | copied from the bucket (`gcloud storage rsync -r gs://nasa-glazing-data/photolists/ …`, 381 MB, ~$0.05), or `skyshift-pipeline s3index --root ~/skyshift-data` | free from NASA's S3 |
| `refcat/sky/forced_n32/` (Gaia G < 17.5, 3.9 GB) | copied from the bucket (~$0.51), or `python -m skyshift_pipeline.forced_sky --root ~/skyshift-data` | free from ESA |
| `refcat/sky/forced_red_n32/` (AllWISE red layer) | `python -m skyshift_pipeline.forced_sky --red --root ~/skyshift-data --workers 32` | free from AllWISE on S3 |

`SKYSHIFT_FORCED_SKY_RED=off` runs a bench without the red layer (a smoke test only).

`score_bench.py` (after a bench) compares the new extraction with the old one of the same photos (`~/skyshift-data/before/`)
and with known things: isolated Gaia stars by G and detector, MPC asteroids and comets by V (`cache/mpc/<zone>/`),
306P, 3I/ATLAS, Galatea and the featured targets (nearest dot, peak, stack row, forced row), dots with nothing
catalogued under them, and a 45″-shifted control for chance matches. Writes `<root>/score.json`.

What `bench` reports (`rerun-bench/bench.json`, from every bench run so far): a worker's seconds per photo (read wait,
detection, forced, writing, its share of the stack), RAM per worker, MB per photo for dots, peaks, forced, stack tables
and session maps (maps are written for every bench zone so they can be sized), and the per-detector split. `plan`
uses those numbers once they exist.

## Measured on 17 Nov 2026 (index of 14 Nov)

- NASA's S3 holds 1,628,230 Level 2 photos (106 TB). The 333 sky zones (hunt + 332 ecliptic zones, ±45° of the
  ecliptic) touch 856,377 of them (63 TB to read with the extra reads below); the other 771,853 lie near the ecliptic
  poles (median |β| 76°). Docs before 17 Nov said ~495,000.
- Every photo is in ~2 zones. Dot files are shared, so a photo is measured once, but a session cut differently by two
  zones gets two stack keys: 121,735 photos (14%) are read a second time for a stack.
- Smoke test (R Draconis, 8 photos, 2 workers): detection 6.5 s per photo; the read took 32 s on the PC's Wi-Fi
  (~2–4 MB/s per download, ~10 MB/s for any number in parallel). The run is network-bound until the PC is on Ethernet.

## Comet stamps (27 Nov, agent "comet-stamps")

The worker cuts a small patch (IMAGE, VARIANCE, FLAGS, ZODI, its background, the point-free residual, wavelength,
bandwidth) around every known comet in each photo, so faint comets can be stacked along their path later without a
second read (pipeline/src/skyshift_pipeline/stamps.py; one file per photo with comets, `<v2 folder>_stamps/`). The list
of comets per photo is built once on the PC, before the run, from the MPC's comet files (free) with kete:

```bash
mkdir -p ~/skyshift-data/stamplists/orbits && cd ~/skyshift-data/stamplists/orbits
curl -O https://minorplanetcenter.net/Extended_Files/cometels.json.gz -O https://minorplanetcenter.net/Extended_Files/allcometels.json.gz
cd ~/skyshift && uv run --project pipeline python -m skyshift_pipeline.stamplist --index ~/skyshift-data/photolists/s3index \
    --orbits ~/skyshift-data/stamplists/orbits --out ~/skyshift-data/stamplists --workers 3   # ~2 min, 1.1 M rows
```

`bench` links `<data>/stamplists` into its root when it exists; `run` roots find it as `stamplists/` (or set
`SKYSHIFT_STAMP_LIST=<folder>`). `SKYSHIFT_STAMPS=0` turns the stamps off. Stack one comet's stamps:
`uv run --project pipeline python scripts/rerun/stack_stamps.py --root <root> --name "306P/LINEAR" [--frame star]`.

## inject.py: fake objects through the real worker (27 Nov)

How much the extraction finds, where and why it loses things, and which settings cannot be fixed after the one read
of every photo. Fakes of known flux, size and motion go into real photos from the photo cache (in memory; the cache is
never written), with their photon noise in IMAGE and VARIANCE, and the photos run through the same worker the rerun
runs (`dots.process_photos`, extraction v2 with the fused session stacks; imported, never copied). Its dots, peaks,
ext, streak, forced and session-stack files are matched to the truth list.

- Kinds: point (the photo's ePSF, sub-pixel), fuzzy (ePSF ⊗ Gaussian or coma, 1.5–12 × FWHM, tails), trails
  (150–6,000″/h, streaks.trail_kernel), movers (0–50″ between exposures, every exposure of a session and both partner
  detectors, flat / 1500 K / 5800 K spectra). S/N is the optimal (matched-filter) S/N for the fake's own shape,
  including its own photon noise.
- One fake per 128-px cell (±16 px jitter, ~225 per photo); `--grid 256 / 90` checks that recovery does not depend on
  the density. Each cell's corner is a control point matched the same way (the chance level).
- `--neg` runs the same photos as −IMAGE (the positive photo's dark-hole mask, no fakes): every row is a false alarm.
- `--variant a+b` changes one setting in the worker processes (`VARIANTS`: background mesh, deblend, clean, guard, ext
  scales, matched-filter phases, peaks floor) for a run of its own.
- Every fake keeps its local conditions (noise, background, distance to saturation, mask, edge, star density, glow,
  the footer block and the conditions table's 128-px tile `cond_tile`) and, when missed, the first cause that applies.

```bash
I="uv run --project pipeline python scripts/rerun/inject.py"
$I run --subset ~/skyshift-data/rerun/inject/bench.json --name main_r1 --variant peaks30 --workers 5
$I run --subset ~/skyshift-data/rerun/inject/bench.json --name neg_main --variant peaks30 --neg --workers 5
$I match --name main_r1 && $I match --name neg_main --cond-run main_r1
$I report --spec ~/skyshift-data/rerun/inject/spec.json --md ~/skyshift-data/rerun/inject/report_tables.md
```

A subset file is any targets file of bench_targets.json's format (`detectors`, `max_exposures` optional), e.g. the
wide sample's wide_targets.json. Outputs go to `~/skyshift-data/rerun/inject/runs/<name>/`; at most 5 workers (the
tool refuses more). Results and recommendations: `~/skyshift-data/rerun/reports/inject.md`.
The worker's peaks floor is env `SKYSHIFT_MF_PEAK_SNR` (config MF_PEAK_SNR, default 3.0 on p1/integrate-fixes
from 27 Nov, as the report recommends). It only adds `why` 0 rows to the peaks file: +0.42 MB per photo against
floor 4, and points found anywhere at 50% go from S/N 4.0 to 3.0.
Tests: `uv run --project pipeline pytest -q scripts/rerun/tests`.
