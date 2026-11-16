# The To check rebuild on your PC

What it does: reruns `suspects` for every zone with the 16 Nov rule (remove only the certain), exports a new
`candidates.json` (with `candidates-more/` for grade C), runs the check producers (known, catalogue, pixel), merges
them, builds static frames for the new grade A/B movers, checks the result and opens a PR with the data. The
orchestrator merges and deploys from main. Nothing runs on cloud machines; nothing in the bucket is changed or deleted
(the optional upload in (h) only adds three files).

One script does it: `scripts/rebuild/tocheck.sh`. It is resumable: after any stop, run the same command again.

| | |
|---|---|
| Download from the bucket | ~61 GB: ~10.5 GB mirrored (a) + ~50 GB read by column in the export (c) |
| Bucket cost | ~$9 (~$0.13 per GB for Nearline + internet egress, plus ~$1 for ~1 M read requests); range $6–13 |
| Free downloads | NASA S3 (photos for pixel checks, AllWISE), IRSA (catalogues, cutouts), MPC, JPL Horizons |
| Time on a 16-core PC | ~6–16 h, mostly unattended (IRSA's speed decides most of it) |
| Disk | ≥ 100 GB free |

## 1. Quick start

```bash
cd ~/skyshift && git switch main && git pull
scripts/rebuild/tocheck.sh --dry-run                 # lists the bucket, prints files, GB, $, commands; runs nothing
scripts/rebuild/tocheck.sh --zones ecl04             # optional test (~15 min, ~$0.20); publishes nothing
tmux new -s rebuild                                  # keeps it running if the terminal closes
scripts/rebuild/tocheck.sh --known-index             # the whole rebuild; it asks before each paid download
```

At the end it prints the PR link. Send the link to the orchestrator.

## 2. The machine

- Ubuntu 22.04 or 24.04, or Windows 10/11 with Ubuntu 24.04 in WSL2. Ubuntu 20.04 does not work: kete's wheels need
  glibc 2.35.
- 16 cores and 32 GB RAM are what the times below assume. 16 GB works with `--workers 2` (slower).
- ≥ 100 GB free on an SSD: mirror ~11 GB, export cache ~29 GB, AllWISE, known and pixel caches a few GB each, the repo
  with node_modules ~2 GB, the new frames.
- A line of ≥ 100 Mbit/s. The setup step measures NASA S3 and prints it.

## 3. Setup (once)

### 3.1 Windows only: WSL2

In PowerShell as administrator:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart, open "Ubuntu 24.04", make a user. Give WSL enough memory and cores: create `%UserProfile%\.wslconfig` with

```ini
[wsl2]
memory=24GB
processors=16
swap=8GB
```

then run `wsl --shutdown` in PowerShell and open Ubuntu again. Keep the repo and the data inside Linux (`~/skyshift`,
`~/skyshift-data`), never under `/mnt/c`: the export cache holds ~300,000 small files and `/mnt/c` is many times slower.
WSL's disk grows on its own, but drive C: needs the free space.

Everything below is the same on Ubuntu and in WSL2.

### 3.2 Packages

```bash
sudo apt-get update
sudo apt-get install -y git curl tmux ca-certificates gnupg wget python3
```

uv (Python and the locked packages; it installs Python 3.12 itself):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env        # or open a new terminal
uv --version
```

Node 22 (for `npm test` and `npm run build` of the site):

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version                 # v22.x
```

Google Cloud CLI:

```bash
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
  | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt-get update && sudo apt-get install -y google-cloud-cli
```

GitHub CLI:

```bash
sudo mkdir -p -m 755 /etc/apt/keyrings
wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt-get update && sudo apt-get install -y gh
```

### 3.3 Logins (you run these; nothing is stored in the repo)

```bash
gcloud auth login                          # in WSL, if no browser opens: gcloud auth login --no-launch-browser
gcloud auth application-default login      # the pipeline's own reads of the bucket use these credentials
gcloud config set project nasa-glazing
gh auth login                              # GitHub.com, HTTPS, log in with a browser
gh auth setup-git
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
```

### 3.4 The repo

```bash
git clone https://github.com/Nsa-2026/skyshift.git ~/skyshift
cd ~/skyshift
```

The data goes to `~/skyshift-data` (or set `SKYSHIFT_DATA`, or pass `--data DIR`).

### 3.5 Speed check

The setup step reads 32 MB of one SPHEREx photo from NASA S3 and prints the speed (also in
`~/skyshift-data/rebuild/speed.txt`). Step (a) prints the bucket's MB/s per zone. Under ~5 MB/s, the export (c) takes
several hours; it still works.

## 4. Running it

- `--dry-run` first. It lists the bucket (free) and prints what each step would download, the GB, the $, the
  commands and the times. It changes nothing.
- The script asks before each paid download (a and c). After a dry run you can add `--yes` for an unattended run.
- A partial run (`--zones a,b`) stops after the checks (f): its export would drop the other zones from the site. Its
  mirror, suspects and export cache are reused by the full run.
- After a stop it prints `STOPPED in step X`, the log and the cause. Fix the cause, run the same command again: finished
  steps are skipped (a stamp file per step under `~/skyshift-data/rebuild/run-<n>/stamps/`).
- `--redo STEP` runs a step again and every step after it (`--redo a` also lists the bucket again; the listing is kept
  12 h). `--only STEP` runs one step. After `git pull` brought new pipeline code, use `--redo b`.
- `--workers N`: zones at once in (b), frame jobs at once in (e). Default: half the cores, at most RAM / 4 GB.

Options: `scripts/rebuild/tocheck.sh --help`.

## 5. The steps

| step | what | reads | cost | time (16 cores) |
|---|---|---|---|---|
| setup | tools, logins, disk, NASA S3 speed, branch `data/tocheck-rebuild-<date>` from origin/main, `uv sync` | PyPI | free | 5–10 min |
| a | mirror the files `suspects` needs | 10.5 GB, 38,551 files from the bucket | $1.36 | 10–40 min |
| h | optional: the server's MPC index with every comet | MPC | free | 10–30 min |
| b | `suspects` for 62 zones | local; AllWISE from NASA S3 | free | 20–60 min |
| c | one export | ~50 GB from the bucket by column (range 30–80) | ~$6.6 + ~$1 requests | 0.5–2 h |
| d | known, catalogue, pixel checks; merge | MPC, IRSA, NASA S3 | free | 1–4 h |
| e | site files, frames, fill | IRSA cutouts | free | 2–8 h |
| f | checks; web tests and build | local | free | 10–20 min |
| g | commit, push, PR | GitHub | free | 5–15 min |

### (a) Mirror

`gcloud storage cp --no-clobber`, one zone at a time, 4 zones in parallel, every size checked against the listing. The
plan is in `~/skyshift-data/rebuild/plan/manifest.tsv` (category, zone, URL, local path, bytes). Measured by listing
on 16 Nov:

| what | files | GB | cost | files |
|---|---|---|---|---|
| results | 554 | 2.22 | $0.29 | results/\<zone\>/ movers, fixed, freedots, qa_photos, tracklets, tracklet_dots, summary.json, and the bucket's suspects.parquet/json saved as suspects.bucket.* |
| photolists | 62 | 0.03 | $0.00 | photolists/zones/\<zone\>.parquet |
| refcat | 122 | 2.81 | $0.36 | refcat/\<zone\>.parquet and \<zone\>_catalogs.json (Gaia, CatWISE, 2MASS) |
| mpc | 24,392 | 5.28 | $0.69 | cache/mpc/\<zone\>/run_*.json (MPC asteroids and comets at every session) |
| planets | 13,418 | 0.02 | $0.00 | cache/planets/\<zone\>/ (Horizons planets and moons) |
| orbits | 3 | 0.12 | $0.02 | newest cache/mpc/orbits/mpcorb_*, cometels_* and cache/mpc/known_index/*.npz |
| **total** | **38,551** | **10.48** | **$1.36** | |

Every download is logged in `~/skyshift-data/DOWNLOADS.log`.

### (b) Suspects for every zone

`skyshift-pipeline suspects --zone <zone> --root ~/skyshift-data`, `--workers` zones at once, largest first. It writes
only local files (results/\<zone\>/suspects.parquet, suspects.json, summary.json; the bucket's summary is kept as
summary.bucket.json). The 62 zones that have results in the bucket:

ecl00 ecl01 ecl02 ecl03 ecl04 ecl05 ecl06 ecl08 ecl09 ecl10 ecl10n1 ecl11 ecl11n1 ecl12n1 ecl12s1 ecl13 ecl13n1
ecl13s1 ecl14 ecl14n1 ecl14s1 ecl15 ecl15n1 ecl15s1 ecl16 ecl16n1 ecl16s1 ecl17 ecl17n1 ecl17s1 ecl18 ecl18n1 ecl18s1
ecl19 ecl19n1 ecl19s1 ecl20 ecl20n1 ecl21 ecl21n1 ecl22 ecl23 ecl24 ecl26s4 ecl27s4 ecl29 ecl30 ecl32 ecl32n1 ecl33
ecl33n1 ecl34 ecl34n1 ecl35 ecl36 hunt pilot t_barnard t_epsindib t_rdra t_rwaur t_wise0855

The dry run names them again from the bucket, so a zone added later is included.

### (c) One export

`skyshift-candidates` for all zones in one run, through an overlay: the local mirror first (the new suspects tables
win), everything else straight from the bucket, a few columns at a time (sources, members, classes, crossids,
fragments, photoz, high_pm, shape: 19.9 GB of tables; the dot files' `flags` for ~90% of 283,164 photos: ~31 GB).
Grade C movers are built from the suspects row alone; A/B movers read only their dots' flags.

Two things keep this small:

- The read block is 16 KiB (`FSSPEC_GS='{"block_size": 16384}'`). With gcsfs' default 5 MiB block, every one-column
  read pulls the whole 1–4.5 MB dot file: ~784 GB, ~$100.
- Every column read is cached in `~/skyshift-data/cache/export` (~29 GB). A rerun of (c) reads nothing from the bucket.

Before it starts, `rebuild.py estimate` counts the real numbers from the new suspects tables and asks.
Output: `run-<n>/export/candidates.json` and `run-<n>/export/candidates-more/<zone>.json`.

### (d) Check producers, then the merge

In this order, each with its own stamp:

1. `known_check.py`: every mover (main and more files) against MPC asteroids at any V and every comet (cometels +
   allcometels: 306P is in there), Horizons planets and moons; MPChecker for the top 30 open movers (only positions
   and times are sent). Builds its index once (~10 min), then cached in `cache/known_check`.
2. `catalogue_check.py`: IRSA catalogues at every candidate of candidates.json; our own magnitudes from classes.parquet
   by column (shares the export's cache).
3. `pixel_check.py`: To check movers measured in NASA's photos by small S3 range reads (~4 MB and a few seconds per new
   mover; cached in `cache/pixel`).
4. `skyshift-candidates --merge-only`: one call writes `run-<n>/final/candidates.json` and `final/candidates-more/`.
   A grade C mover the checks made known or flaw moves into candidates.json.

### (e) Site files, frames, fill

Copies `final/` into `web/public/data/` (candidates.json, candidates-more/), then `frames.py` (top of each kind and
every grade A/B mover, IRSA cutouts, the MPC known index from the mirror) and `fill.py` for the hours frames' edge
exposures, in chunks of 25. If more than 5% (+5) of the A/B movers got no frames, it stops: IRSA was slow; run the same
command again (built frames are kept).

### (f) Checks before publishing

`rebuild.py report` writes `run-<n>/reports/check.md` and stops on a hard failure:

- every file is valid JSON, ids are unique across candidates.json and the more files;
- file sizes: hard stop over 95 MB (GitHub refuses 100 MB), warning over 50 MB; cand/ over 150 MB is a warning to
  tell the orchestrator (every frame folder is committed);
- counts per kind, status and grade, the To check count, checks per code, before/after against origin/main;
- every producer's side file names only ids that exist;
- 306P/LINEAR is listed as known when ecl01–ecl04 are in the export (hard stop if not);
- every grade A/B mover has frames;
- spot checks: all grade A, the top 10 of To check, known, flaw, 5 random grade C, 5 random fixed.

Then `npm ci && npm test && npm run build` in web/.

Read `check.md` before (g). If something looks wrong, stop there and send check.md to the orchestrator.

### (g) Commit, push, PR

Commits `web/public/data/candidates.json`, `candidates-more/` and `cand/` on the branch
`data/tocheck-rebuild-<date>` (from origin/main), pushes it and opens a PR with check.md as its body. It never
merges and never deploys: the orchestrator merges and deploys main. `--no-pr` stops after the push.

### (h) Optional: the server's known index with every comet

`scripts/rebuild/tocheck.sh --known-index` fetches today's MPC orbits (`skyshift-pipeline mpc-orbits --root
~/skyshift-data --force`: mpcorb + cometels + allcometels) and builds `cache/mpc/known_index/known_index_<date>.npz`
locally, right after (a). Steps (d) and (e) then use it: the frames' known bodies then include every comet, 306P
too (the bucket's index of 14 Nov has 973 comets and no 306P). Local and free; recommended on the first full run.

`--upload-known-index` also copies the index and the two orbit snapshots (~115 MB) to
`gs://nasa-glazing-data/cache/mpc/` without overwriting anything. This is the only write to the bucket; uploading is
free and storing 115 MB costs well under a cent a month. Given after a finished run, it only uploads (no step runs
again). Do it only when the orchestrator asks: Cloud Run then needs
`SKYSHIFT_KNOWN_INDEX=mpc/known_index/known_index_<date>.npz`, which the script prints.

## 6. Known limits

- 64 zones' refcats are Gaia-only (the sky run's `=gaia`, pipeline/CLAUDE.md): there `suspects` gets no CatWISE or 2MASS
  reasons (AllWISE from S3 still counts). Completing them is a separate decision (fix-catalogue report). If the bucket's
  refcats change, run `tocheck.sh --redo a`.
- The bucket listing behind the plan is kept 12 h. A zone finished after it shows up with `--redo a`.
- Frames depend on IRSA. A slow IRSA stops (e); rerunning continues where it stopped.
- cand/ grows with every grade A/B mover and goes into git (main: 295 folders, 15 MB on 16 Nov).

## 7. Later: the photo rerun on the PC (NOT READY)

**Not ready to run.** It waits for agent "v2finish" (extraction v2's final settings and command) and for the session
stacks to be fused into the dots worker. Agent "sessionstack" finished on 16 Nov (PR #383, docs/session-stacks.md §7):
the `sessionstack` step exists, is off by default and still reads every photo itself, so run on its own it would read
the 27.7 TB a second time. Those PRs update this section with the final command and the measured CPU per photo.

What it is: one read of every SPHEREx photo (~495,000), with extraction v2 (`SKYSHIFT_DOTS_V2=1`, folder
`dots_deblend1e-4_cp3_g6_psf_glow_v2/` and `…_v2_peaks/`; docs/extraction-v2.md) and the session stacks
(`SKYSHIFT_SESSION_STACK=1`, folder `session_stacks/`; docs/session-stacks.md §7). Then link onward on the new folders.

| | |
|---|---|
| Read | 56 MB per photo (IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE) from NASA S3: ~27.7 TB, free (AWS open data). Dropping ZODI would save ~29% (open decision) |
| Line | 1 Gbit/s (~110 MB/s) reads it in ~70–77 h; ~0.5 Gbit/s sustained keeps 16 cores busy; at 100 Mbit/s it takes ~32 days |
| CPU | v2: ~18.7 s per photo per core → ~3,100 photos/h on 16 cores → ~6.5 days; session stacks ~420 core-h → ~26 h more: **~7.5 days** if the line keeps up |
| RAM | ~1.2 GB per session-stack worker (16 workers: ~20 GB) |
| Output | dots ~4.7–5.8 MB + peaks ~0.3 MB per photo: ~2.9 TB; session-stack tables for the sky ~1.2 TB (no maps; hunt maps ~15 GB): **~4.1 TB** |
| Disk | writing straight to the bucket: a few hundred GB of buffer; keeping a local copy (recommended, below): ≥ 5 TB |

Where the outputs go:

- **To the bucket** (`--root gs://nasa-glazing-data`, or a local root then `gcloud storage rsync` up). Uploading is
  free. ~1.4 M new objects (dots, peaks, ~3 stack files per session × detector) cost ~$14 in write requests on
  Nearline (~$7 on Standard). Storing 4.1 TB costs ~$41/month on Nearline, ~$82 on Standard.
- **Keep a local copy for the steps after it.** link and everything after read the dot files and stack rows again.
  Reading 4.1 TB back from the bucket to the PC would cost ~$530 (egress + Nearline retrieval); in GCP (us-east4) it
  costs only the retrieval (~$41 on Nearline, $0 on Standard).

For comparison (docs/extraction-v2.md §8, session-stacks.md §7): GCP spot n2-highcpu-64 next to S3, 1 machine
~7–8.5 days or 4 machines ~2 days, ~$100–200 compute.
