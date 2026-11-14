# Pilot and hunt zone: status at 14:22 CET (13:22 UTC), Sat 14 Nov

Read-only check: `gcloud compute instances list`, the machine logs in `gs://nasa-glazing-data/logs/`,
`results/`, `dots/`, `forced/`, `refcat/`, and the worker's serial console. No machine was started,
stopped or created. Times are UTC as in the logs; local (CET) = UTC + 1. The plan's "pilot ~13:45,
G2 14:30" are CET, i.e. 12:45 and 13:30 UTC.

## Short answer

- **The pilot ran, completely, twice.** G2 **passes**: recall at V < 16 is **92.2% (83 of 90
  object-sessions), 8 of 8 asteroids, control 0%** (MPC/kete run, `results/pilot/recall.json`).
- **The hunt zone is already running.** Its dots finished at 12:38 UTC (3,692 photos in 10.4 min on
  the 64-core spot worker); the remaining steps (`all --zone hunt`) started at 13:00 UTC on
  `skyshift-worker`, now **n2-highmem-16 spot, RUNNING**, and were in `calib` at 13:22 UTC.
- Nothing from the dev plan still has to be started by hand. Next steps are below.

## Machines (13:22 UTC)

| machine | type | state | last job |
|---|---|---|---|
| skyshift-test | e2-standard-8 | TERMINATED (stopped itself) | 13:01–13:12 `all --zone pilot`, exit 0, commit 1477f8e |
| skyshift-worker | **n2-highmem-16**, spot | **RUNNING** since 13:00 UTC | `known --zone hunt && all --zone hunt --workers 16`, commit 1477f8e, autostop on |

The worker's type differs from the plan and `infra/worker/vm.sh` (n2-highcpu-64): it ran the hunt
dots as 64 cores (log: `cores=64`) and was changed to 16 cores with more memory for the table steps
(log: `cores=16`). `vm.sh create worker` would still make an n2-highcpu-64.

## What ran (from logs/)

| UTC | machine | job | result |
|---|---|---|---|
| 11:44–12:11 | test | pilot: `known` (SkyBoT) beside `dots`, then `all` | dots 990/990 photos in 23.3 min, 0 failed, 21.1 M dots (median 25,141 per photo, read 1.1 s, detect 11 s); every step up to `score`. The bucket copy of the log stops in `score` (12:10:46); the dev plan records this run's G2: 63 of 68 object-sessions (93%), 7 of 7 objects, control 0% |
| 12:28–12:38 | worker (64 cores) | `dots --zone hunt --workers 60` | 2,704 new photos (988 were the pilot's), 0 failed, 48.0 M dots, 259 photos/min, exit 0 |
| 12:53–12:55 | worker | `known --zone hunt && all --zone hunt` | **exit 1**: MPC download timed out from GCP (`mpcorb_extended.json.gz, 0 bytes`), see pipeline/CLAUDE.md |
| 13:00– | worker (16 cores) | same job, after the MPC snapshot was made from a laptop (`cache/mpc/orbits/*_2026-11-14.parquet`, 1,580,451 asteroids + 973 comets) | known 13:00–13:03; link 33.4 M dots → 303,056 fixed, **6,143 movers** (5,818 two-exposure, 325 two-detector); spectra 303,041 sources; crossid; **calib running since 13:07** (pilot calib took 95 s for 5 M dots; no OOM on the console) |
| 13:01–13:12 | test | `all --zone pilot` again (MPC known, commit 1477f8e) | exit 0, every step; G2 PASS, 27 suspects |

## Pilot results (results/pilot/, 13:12 UTC)

Recall (`recall.json`; unit = one known object in one session with ≥ 2 exposures covering it,
match 12″, control = predictions moved 2′):

| V | n | found | recall | control |
|---|---|---|---|---|
| < 12 (saturated) | 18 | 17 | 94% | 0% |
| 12–14 | 5 | 5 | 100% | 0% |
| 14–15 | 28 | 25 | 89% | 0% |
| 15–16 | 39 | 36 | 92% | 0% |
| **< 16 (G2)** | **90** | **83** | **92.2%** | **0%** |
| 16–17 | 70 | 58 | 83% | 0% |
| 17–18 | 149 | 59 | 40% | 0% |

Per asteroid: 8 of 8 found. G2 threshold 50%: **pass**.

Other pilot numbers: 990 photos (352 exposures × detectors, DR1 only, 108 sessions, 2 visits);
5.0 M dots in the zone, 45,190 fixed sources, 873 movers → **27 suspects** (cuts: known 244,
artefact flags 152, bright fixed 69, Gaia 161, CatWISE 220). highpm: 167 candidates, 8 vetted,
0 new. Tables: 24 files, 321 MiB (lightcurves.parquet 263 MB is most of it).

## Bucket (13:20 UTC)

| prefix | files | size |
|---|---|---|
| dots/ | 3,694 (3,692 hunt photos + 2 pilot photos outside the hunt list) | 7.72 GB |
| forced/ | 3,694 | 1.85 GB |
| refcat/ | pilot, pilot_forced, hunt, hunt_forced | 189 MB |
| results/pilot/ | 24 | 321 MiB |
| results/hunt/ | 7 so far (fixed, members, movers, sources, crossids, channels, summary) | 268 MiB |

## Things to know

- `web/public/data/validation.json` V5 still shows the SkyBoT run (63 of 68, 93%, 7 objects). The
  MPC run gives 83 of 90 (92.2%), 8 objects. Rebuild it after the hunt (below).
- `web/public/data/suspects.json` doesn't exist yet. `results/pilot/suspects.json` (27 suspects)
  is ready now if the site needs something before the hunt ends.
- The dev plan says the pilot and G2 use SkyBoT and the hunt switches to MPC only after a ≥ 95%
  agreement check. The hunt runs on MPC (the default); the MPC pilot recall (92.2%) is as good as
  SkyBoT's (93%). The agreement check itself is not in the logs.
- `spectra` drops member dots "> 10″ from their source (dot files rewritten after link?)": 23,195
  in the pilot (0.5%), 135,947 in the hunt (0.4%). The same count appears in the first pilot run,
  so it is not a rewrite; worth a look (large FoF groups?).
- Spot worker: autostop is on, so it stops when the job ends. If it is reclaimed, it stops
  rather than deletes; rerun the same job and every step skips what exists.

## Next commands

Nothing from the plan is waiting to start. Watch the hunt:

```
infra/worker/vm.sh log worker
infra/worker/vm.sh status
```

If the hunt job fails or the spot machine is reclaimed (dev plan, "Gate G2": `vm.sh run worker '… all --zone hunt'`):

```
infra/worker/vm.sh run worker 'cd pipeline && uv sync -q && uv run skyshift-pipeline all --zone hunt --workers 16'
```

When `results/hunt/suspects.json` exists (job end in `logs/skyshift-worker/latest-status.txt`):

```
gcloud storage cp gs://nasa-glazing-data/results/hunt/suspects.json web/public/data/suspects.json
cd scripts/validate && uv run python build_validation.py --site-data ../../web/public/data --fetch-results pilot
```

(`--fetch-results hunt` once `results/hunt/recall.json`, `astrometry_report.json` and
`photoz_report.json` exist, if V5, V9 and V10 should quote the hunt zone instead of the pilot.)
