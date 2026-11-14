# SkyShift cloud architecture

What runs where, what each script does with the photos, and what we keep. Owner: P1.
Interfaces stay in `contracts/`; this page explains the logic behind them.

## The rule for data

**We never copy NASA's photos.** Each SPHEREx photo is a 60 MB FITS file on NASA's public Amazon
copy (`s3://nasa-irsa-spherex`, read over HTTPS without a login). We stream only the parts we need
(IMAGE + FLAGS + WCS-WAVE, plus VARIANCE for error bars), compute in memory, and keep only what we
computed: photo lists, dots, results and small cutouts. Reading from Amazon into Google Cloud costs
us nothing.

## Three jobs, three places

| Job | What it does | Where it runs | What it keeps | Owner |
|---|---|---|---|---|
| **Blinks** for the featured targets and for any coordinates | Small cutouts from IRSA → visit stacks (Months) and single exposures (Hours) → frame data, spectrum, motion | Featured: `scripts/precompute` on a laptop or `skyshift-test`. Any coordinates: Cloud Run `skyshift-api` | Featured: `web/public/data/<slug>/` on Firebase Hosting. Live: `cache/` in the bucket | P2 |
| **Validation** V1–V8 | Photo lists, Gaia, MOST; measures Barnard's Star, the error floor, known asteroids | Laptop or `skyshift-test` | `results/validation/`, then `web/public/data/validation.json` | P1 |
| **Hunt zone** (pilot, then S1) | Whole photos streamed from Amazon → every dot → which dots move → which movers are unknown | `skyshift-test` (pilot), `skyshift-worker` (hunt zone, after gate G2) | `dots/`, `results/<zone>/`, then `web/public/data/suspects.json` | P1 |

```
               NASA S3 (public)            IRSA (search, cutouts, MOST)     ESA Gaia · IMCCE SkyBoT
                     │                                │                              │
   whole photos ─────┤                    cutouts ────┤                              │
                     ▼                                ▼                              │
   skyshift-test / skyshift-worker        scripts/precompute · Cloud Run API ◄───────┤
   (pipeline/: dots → movers → suspects)  (server/: build_blink)                     │
                     │                                │                              │
                     ▼                                ▼                              │
   gs://nasa-glazing-data:  photolists/  dots/  results/  cache/  logs/ ◄────────────┘
                     │                                │
                     ▼                                ▼
   web/public/data/ (suspects.json, validation.json, <slug>/)  ──►  Firebase Hosting (static, first)
   Flags and votes from the browser ──► Firestore (candidates, votes)
```

## The bucket: `gs://nasa-glazing-data` (us-east4)

| Prefix | Written by | Holds | Format | Size (estimate) |
|---|---|---|---|---|
| `photolists/targets/` | scripts/validate | IRSA search results per featured target | Parquet + CSV | < 1 MB each |
| `photolists/zones/` | pipeline `photolist` | Photos covering the pilot and the hunt zone, with footprint, time, detector, S3 key, session and visit per 0.25° tile | Parquet | < 5 MB |
| `dots/` | pipeline `dots` | One file per photo: every detected point (contracts/dots.md) | Parquet, `dots/<obs_id>D<det>.parquet` | ~1 MB per photo, a few GB for the hunt zone |
| `cache/skybot/` | pipeline `known` | Known asteroids predicted in each photo | JSON per photo | small |
| `cache/` (rest) | server | Live API cutouts and frames | as the API needs | grows with use |
| `results/<zone>/` | pipeline `link`, `score`, `suspects` | `fixed`, `movers`, `suspects` tables, `recall.json`, `summary.json` | Parquet + JSON | < 100 MB |
| `results/validation/` | scripts/validate | V1–V8 outputs | JSON, Parquet | small |
| `logs/<machine>/` | the machines | One log per job run, `latest-status.txt` | text | small |

A file's existence means its step is done, so every step can be stopped and restarted and
it skips what's finished. There's no job list and no locks. The machines never write to Git:
P1 copies `suspects.json` and `validation.json` into `web/public/data/` through a normal PR.

## The hunt-zone pipeline, step by step

Zones: **hunt** = RA 120.5–130.5, Dec +14.4 to +24.4 (100 deg² on the ecliptic, where asteroids
are densest). **pilot** = RA 120.5–124.1, Dec +14.4 to +17.9 (12 deg², its corner).

Why it works: SPHEREx looks at a spot in **sessions** (1–5 photos 2 minutes apart) spread over a
2–4 week **visit**. A star is in the same place in every session. An asteroid moves a few
arcminutes between sessions but only about 1″ within one. A cosmic ray or glitch is in one photo
only. So a real mover shows up twice in the same place within one session, and nowhere there in
the other sessions.

Run as `uv run skyshift-pipeline <step> --zone pilot|hunt` from `pipeline/`; `all` runs them in
order. Each step reads the previous step's files from the bucket (or from `--root <local dir>`).

1. **`photolist`**: ask IRSA (TAP, `spherex.obscore`) for every Level 2 photo whose footprint
   touches the zone, DR1 and QR3, no `*_deep`. One row per photo (obs_id + detector), duplicates
   removed (DR1 preferred). Cut the zone into 0.25° tiles; for each tile, take the times of
   the photos covering its centre and run the visit rule (`core.sessions`, `core.visits`), so
   every (tile, photo) pair gets its session and visit. Also count how many sessions cover each
   tile.
2. **`dots`** (the heavy step, one process per core): for each photo, stream IMAGE, FLAGS,
   VARIANCE and WCS-WAVE from Amazon; mask bad pixels (contracts/flags.md: SOURCE is never
   masked); detect every point at 5σ (`core.detect`); turn each into sky position, wavelength,
   brightness in µJy and the flag bits under it; add session and visit from its tile and its
   6″ sky `cell`. Write `dots/<obs_id>D<det>.parquet`. Nothing else from the photo is kept.
3. **`known`**: ask SkyBoT which numbered and named asteroids and comets were inside each photo
   at its time (`-loc=500`, a few requests at a time, cached in `cache/skybot/`).
4. **`link`**: group all dots by cell, merging each cell with its 8 neighbours so an edge doesn't
   split a source.
   - **fixed**: seen in 3 or more sessions (stars, galaxies).
   - **mover**: seen in exactly one session there, *and* either in 2 photos of that session
     or in 2 detectors exposed at the same moment (D1+D4, D2+D5, D3+D6); *and* the tile was
     covered by at least 3 sessions, so "absent elsewhere" means something.
   - Single dots with neither are junk and are dropped.
5. **`score`** (gate G2): take the known asteroids brighter than V 16 that fell inside a photo
   (at least 10 px from the edge). **Recall** = the share with a mover within 12″ in that session.
   Also report the share with any dot there (detection versus linking), recall by magnitude
   (V5's chart), and a **control**: the same match with every predicted position moved 2′ in a
   random direction, which gives the chance-match rate. Writes `results/<zone>/recall.json`.
   **G2 passes if pilot recall at V < 16 is 50% or more.**
6. **`suspects`**: movers with no known object within 30″, no GHOST, CROSSTALK, HALO or BLOOM bit,
   not within 6″ of a Gaia star (faint stars near our limit can flicker in and out) and not within
   30″ of a bright fixed star. Ranked by evidence and signal-to-noise. Exported in the
   `suspects.json` shape (contracts/api.md); P1 reviews them before they go to Firestore
   `candidates` with `source: "pipeline"`.

Time estimate (to be measured on the pilot): about 5–10 s per photo, mostly download. Pilot
(~230 photos) about 5 minutes on 8 cores; hunt zone (~3,400 photos) under 30 minutes on 64.

## Machines

Both run Debian 12 with a 30 GB disk, as service account `skyshift-worker`, and use the same
start-up script, `infra/worker/startup.sh`. At every boot it:
1. installs Git and uv 0.13.8 once;
2. checks out the repo at metadata `skyshift-ref` (default `main`; a branch works too);
3. runs metadata `skyshift-job`, a shell command from the repo root, printing to the serial console
   (Cloud Logging) and copying the log to `logs/<machine>/` every minute;
4. **stops the machine when the job ends** (unless `skyshift-autostop` is `false`). A stopped
   machine costs only its disk.

| Machine | Type | Use | Cost while running |
|---|---|---|---|
| `skyshift-test` | e2-standard-8, normal | Pilot, validation, precompute, anything heavy | about €0.30 an hour |
| `skyshift-worker` | n2-highcpu-64, **spot** | Hunt zone only, after G2 | about €0.60–1 an hour (spot price varies) |

A spot machine can be taken back by Google at any time. It then stops; `run` again and it
carries on, skipping finished photos.

```
infra/worker/vm.sh create test|worker        # create; it boots, installs, then stops itself
infra/worker/vm.sh run test 'JOB' [ref]      # start a job; the machine stops when it's done
infra/worker/vm.sh log test                  # the last lines of the job log
infra/worker/vm.sh status                    # machines and their state
infra/worker/vm.sh stop test                 # stop now; the job resumes on the next run
```
Example: `infra/worker/vm.sh run test 'cd pipeline && uv run skyshift-pipeline all --zone pilot'`.

## Service accounts

| Account | Used by | Roles |
|---|---|---|
| `skyshift-worker@nasa-glazing.iam.gserviceaccount.com` | both machines | Storage Object Admin on the bucket only; Logs Writer; Monitoring Metric Writer |
| `skyshift-api@nasa-glazing.iam.gserviceaccount.com` | Cloud Run `skyshift-api` (P2: deploy with `--service-account`) | Storage Object Admin on the bucket only; Cloud Datastore User (Firestore); Logs Writer |

No keys are created or downloaded; the machines and Cloud Run get credentials from Google.

## Cost and clean-up

Pilot and validation on the test machine: a few hours, about €1–2. Hunt zone: under an hour of
spot, about €1. Storage: a few GB, cents. Within the plan's €15 of the €264 credit.
After the final submission: delete both machines. On Monday: delete `dots/` and `cache/`.
