# SkyShift cloud architecture

What runs where, what each script does with the photos, and what we keep. Owner: P1.
Interfaces stay in `contracts/`; this page explains the logic behind them.

## The rule for data

**We never copy NASA's photos.** Each SPHEREx photo is a 60 MB FITS file on NASA's public Amazon
copy (`s3://nasa-irsa-spherex`, read over HTTPS without a login). We stream only the parts we need
(all six parts, about 56 MB of a 60–70 MB file, read once), compute in memory, and keep only what we
computed: photo lists, dots, results and small cutouts. Reading from Amazon into Google Cloud costs
us nothing.

## Three jobs, three places

| Job | What it does | Where it runs | What it keeps | Owner |
|---|---|---|---|---|
| **Blinks** for the featured targets and for any coordinates | Small cutouts from IRSA → visit stacks (Months) and single exposures (Hours) → frame data, spectrum, motion | Featured: `scripts/precompute` on a laptop or `skyshift-test`. Any coordinates: Cloud Run `skyshift-api` | Featured: `web/public/data/<slug>/` on Firebase Hosting. Live: `cache/` in the bucket | P2 |
| **Validation** V1–V8 | Photo lists, Gaia, MOST; measures Barnard's Star, the error floor, known asteroids | Laptop or `skyshift-test` | `results/validation/`, then `web/public/data/validation.json` | P1 |
| **Hunt zone** (pilot, then S1) | Whole photos streamed from Amazon → every dot → which dots move → which movers are unknown | `skyshift-test` (pilot), `skyshift-worker` (hunt zone, after gate G2) | `dots/`, `results/<zone>/`, then `web/public/data/suspects.json` | P1 |

```
               NASA S3 (public)            IRSA (search, cutouts, MOST)     ESA Gaia · MPC orbits
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
| `photolists/zones/` | pipeline `photolist` | Photos covering the pilot and the hunt zone, with footprint, time, detector, S3 key, session and visit | Parquet | < 5 MB |
| `refcat/` | pipeline `refcat` | Gaia DR3, CatWISE2020, 2MASS for each zone, and the forced-photometry target list | Parquet | tens of MB |
| `dots/` | pipeline `dots` | One file per photo: every detected point, with photo metadata (contracts/dots.md) | Parquet, `dots/<obs_id>D<det>.parquet` | ~3–6.5 MB per photo |
| `forced/` | pipeline `dots` | One file per photo: brightness at every catalogue star, detected or not | Parquet | ~1–1.5 MB per photo |
| `cache/mpc/` | pipeline `known`, `mpc-orbits` | `orbits/`: dated MPC orbit snapshots (asteroids, comets); `<zone>/`: known asteroids and comets predicted in each photo | Parquet (orbits); JSON per session | small per session |
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
(Revised 14 Nov after an independent review that measured the real cadence: see "Why these numbers".)

1. **`photolist`**: every Level 2 photo whose footprint touches the zone (IRSA, DR1 and QR3, no
   `*_deep`), one row per photo (obs_id + detector) with its footprint, time and S3 key. A
   **session** is a run of the zone's exposure times with gaps under 10 minutes (one value per
   photo); visits come from `core.visits` on the same times.
2. **`dots`** (the heavy step, one process per core): for each photo, stream all six parts from
   Amazon once (skip photos whose astrometry flag `FINAST` isn't 0); mask bad pixels (SOURCE and
   OUTLIER are never masked); detect at `thresh=2.0` and keep dots with S/N ≥ 5; re-fit the
   centre of saturated dots on their wings. Per dot: position and its error, wavelength and
   bandwidth, brightness (aperture, 5 radii, and NASA's ePSF fit with its χ²), shape, peak, local
   background, zodiacal light, and the flag bits around and at the peak. Also **forced photometry**
   at every reference-catalogue star (`refcat`: Gaia, CatWISE), detected or not, and per-photo
   metadata (spacecraft position, astrometry and PSF quality, background and ZODI grids). Writes
   `forced/` then `dots/<obs_id>D<det>.parquet`. Nothing else from the photo is kept.
3. **`known`**: Minor Planet Center orbits (asteroids and comets) propagated with kete to every
   photo's MJD-AVG, seen from SPHEREx's own position in the IMAGE header. Orbit snapshots in
   `cache/mpc/orbits/` (refreshed from a laptop: the GCP machines can't reach the MPC); predictions
   per session in `cache/mpc/<zone>/`.
4. **`link`**, on dots inside the zone only:
   - **fixed**: dots within 4″ of each other in 3 or more sessions (stars, galaxies).
   - **mover**: a dot not within 6″ of a fixed source, paired inside one session with another
     dot no further than 3″ + 1″ per minute between the exposures, or with the simultaneous
     detector (D1+D4, D2+D5, D3+D6); and the position was covered by at least 3 sessions.
     Only fixed sources veto a mover; a stray dot elsewhere doesn't. Streaks (elongation > 3)
     never pair.
5. **`score`** (gate G2): known asteroids brighter than V 16 inside a photo (10 px from the edge),
   counted per (asteroid, session with 2 or more exposures) and per asteroid. **Recall** = the share
   found as movers within 12″. Also: the share with any dot there, V < 12 (saturated) apart from
   12–16, recall by magnitude (V5's chart), and a **control** with every prediction moved 2′
   (the chance-match rate). Counts are printed with every rate: the pilot holds only about 9
   known asteroids brighter than V 16. **G2 passes if recall at V < 16 is 50% or more.**
6. **`suspects`**: movers with no known object within 30″; no SOURCE bit at the peak; no ghost,
   crosstalk, halo or bloom bit; not within 6″ of a Gaia star or a CatWISE source; not within
   30″ of a bright fixed source. Ranked by evidence and signal-to-noise; exported in the
   `suspects.json` shape. P1 reviews them before they go to Firestore `candidates`.

### Why these numbers (measured 14 Nov)
- A spot is seen in sets of up to 4 exposures 2.15 min apart, with the pointing stepped by 11.8′
  each time: same detector, different pixels, next wavelength channel. A pilot position gets
  about 113 exposures in 38 sessions over two visits; 78.5% of sessions have 2 or more, so pairs
  inside a session really happen. D1+D4 etc. see the same sky at the same instant.
- `thresh=5` with a 3-pixel minimum area only finds sources at S/N ~22 on SPHEREx's sharp PSF;
  `thresh=2.0` reaches S/N ~9 with no false detections on an empty frame. A V 16 asteroid is
  about S/N 14–22 in D1–D4.
- 2MASS has 3,100 stars/deg² brighter than J 16.5 here, so a coarse 18″ veto box would throw
  away about 8% of asteroids; hence the 4″ fixed / 6″ exclusion.
- Pilot: 990 photos (352 exposures); hunt zone: 3,686. About 56 MB read per photo; about 4.5 MB stored per photo (pilot ~4.5 GB, hunt zone ~17 GB). Pilot `dots`
  about 10–20 minutes on 8 cores; hunt zone well under an hour on 64.

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
