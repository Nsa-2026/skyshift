#!/usr/bin/env bash
# The To check rebuild on the lead's PC (Ubuntu 22.04+, or Ubuntu in WSL2 on Windows). Plain guide: docs/rebuild-on-pc.md.
#
#   scripts/rebuild/tocheck.sh --dry-run            # what it would download and run, sizes and cost; changes nothing
#   scripts/rebuild/tocheck.sh                      # everything; after a stop, run the same command again
#   scripts/rebuild/tocheck.sh --zones ecl04,hunt   # a test on a few zones: publishes nothing (no frames, no PR)
#
# Steps. A finished step leaves a stamp file and is skipped on the next run; a step that runs again clears the stamps
# of the steps after it.
#   setup  tools and logins checked, disk, NASA S3 speed, branch data/tocheck-rebuild-<date> from origin/main, uv sync
#   a      mirror exactly the bucket files `suspects` needs (gcloud storage cp --no-clobber) into $SKYSHIFT_DATA
#   a0     complete the mirror's Gaia-only refcats (64 zones) before suspects: CatWISE/2MASS roles from AllWISE on AWS S3,
#          Gaia from ESA to config REFCAT_GAIA_G_MAX (both free); --upload-refcat also puts them in the bucket (the old
#          ones copied to refcat/before-pc-rebuild/ first)
#   h      only with --known-index: MPC orbits + every comet, the server's known index built locally (d and e use it);
#          --upload-known-index also copies it to the bucket (the ONLY bucket write, ~115 MB, never overwrites;
#          after a finished run it uploads and changes nothing else)
#   b      `skyshift-pipeline suspects` for every zone (16 Nov rule: remove only the certain), --workers zones at once
#   m      PR #388: crossid -> classify -> astrometry -> highpm -> shape per zone, untiled, on the mirror (one zone at a
#          time; each zone's tables and dot files copied first, dot files deleted after the last zone needing them).
#          Must finish before the export (c)
#   c      one export (skyshift-candidates): the mirror first, the rest of the bucket by column (small reads)
#   d      check producers known (all movers), catalogue, pixel (To check movers only), then the merge
#   e      web/public/data: candidates.json (+ candidates-more/), static frames and fill for the top 300 open A/B movers
#   k      only with --with-changes (PR #386): the changes step per zone, each in its own process, then changes.json/.atom
#   f      checks before publishing (counts, sizes, 306P, spot checks, before/after), web tests and build
#   g      commit web/public/data on the branch, push, open a PR with gh (the orchestrator merges and deploys main)
#
# Never deletes anything in the bucket; writes to it only with --upload-known-index (h) or --upload-refcat (a0), after a prompt.
# shellcheck disable=SC2329  # the step functions are called through run() and the ERR trap
set -Eeuo pipefail
shopt -s nullglob
if [ "${BASH_VERSINFO[0]}" -lt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -lt 3 ]; }; then
  echo "tocheck.sh needs bash 4.3 or newer (this is $BASH_VERSION): Ubuntu's bash is fine" >&2; exit 64
fi

usage() {
  awk 'NR > 1 && /^#/ && !/shellcheck/ {sub(/^# ?/, ""); print; next} NR > 1 {exit}' "$0"
  cat <<'EOF'

Options
  --dry-run               list the bucket and print the plan (files, GB, $, commands, time); download and run nothing
  --zones a,b,c           only these zones (default: every zone with results/<zone>/suspects.parquet in the bucket).
                          A partial run stops after the checks (f): its export would drop the other zones from the site
  --allow-partial         let a partial run write web/public/data, build frames and open the PR anyway
  --workers N             zones at once in step b, frames at once in e (default: half the cores, at most RAM/4 GB)
  --data DIR              local mirror and work folder (default $SKYSHIFT_DATA or ~/skyshift-data)
  --redo STEP             run STEP again (a, a0, h, b, m, c, d, e, k, f, g) and every step after it; --redo a lists the bucket again
  --only STEP             run only STEP (the steps before it must be done)
  --known-index           step h: rebuild the server's MPC known index with every comet (local; frames use it)
  --upload-known-index    also copy the index and orbit snapshots to the bucket (implies --known-index)
  --upload-refcat         after a0: the completed refcats to the bucket (~9-13 GB, ~$0.10-0.13/month; asks first)
  --frames-top N          static frames for the N best open A/B movers by score (default 300; ~0.45 MB each in git)
  --with-changes          step k: changes in known stars (PR #386 + its chunking follow-up; ~57 GB, ~$7; off by default)
  --pixel-args "..."      extra arguments for pixel_check.py (e.g. "--limit 20" for a test)
  --export-args "..."     extra arguments for skyshift-candidates (e.g. a flag the split needs)
  --no-pr                 step g commits and pushes but opens no PR
  --offline-export        tests only: the export reads the mirror and its cache, never the bucket
  --yes                   don't ask before the downloads that cost money (for an unattended run after a dry run)
EOF
}

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA="${SKYSHIFT_DATA:-$HOME/skyshift-data}"
BUCKET="${SKYSHIFT_BUCKET:-gs://nasa-glazing-data}"
DRY=0; ZONES=""; WORKERS=""; REDO=""; ONLY=""; KNOWN_INDEX=0; UPLOAD_INDEX=0; ALLOW_PARTIAL=0; NO_PR=0; YES=0
PIXEL_ARGS=""; EXPORT_ARGS=""; OFFLINE_EXPORT=0; UPLOAD_REFCAT=0; FRAMES_TOP=300; WITH_CHANGES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --zones) ZONES="$2"; shift ;;
    --zones=*) ZONES="${1#*=}" ;;
    --workers) WORKERS="$2"; shift ;;
    --workers=*) WORKERS="${1#*=}" ;;
    --data) DATA="$2"; shift ;;
    --data=*) DATA="${1#*=}" ;;
    --redo) REDO="$2"; shift ;;
    --only) ONLY="$2"; shift ;;
    --known-index) KNOWN_INDEX=1 ;;
    --upload-known-index) UPLOAD_INDEX=1; KNOWN_INDEX=1 ;;
    --upload-refcat) UPLOAD_REFCAT=1 ;;
    --with-changes) WITH_CHANGES=1 ;;
    --frames-top) FRAMES_TOP="$2"; shift ;;
    --frames-top=*) FRAMES_TOP="${1#*=}" ;;
    --allow-partial) ALLOW_PARTIAL=1 ;;
    --pixel-args) PIXEL_ARGS="$2"; shift ;;
    --export-args) EXPORT_ARGS="$2"; shift ;;
    --no-pr) NO_PR=1 ;;
    --offline-export) OFFLINE_EXPORT=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 64 ;;
  esac
  shift
done

STEPS=(a a0 h b m c d e k f g)
case "$FRAMES_TOP" in ''|*[!0-9]*) echo "--frames-top: a number" >&2; exit 64 ;; esac
case " ${STEPS[*]} " in *" ${REDO:-a} "*) ;; *) echo "--redo: one of ${STEPS[*]}" >&2; exit 64 ;; esac
case " ${STEPS[*]} " in *" ${ONLY:-a} "*) ;; *) echo "--only: one of ${STEPS[*]}" >&2; exit 64 ;; esac

mkdir -p "$DATA"
DATA="$(cd "$DATA" && pwd)"
WORK="$DATA/rebuild"
ZSTAMPS="$WORK/zones"            # a.<zone>, a0.<zone> (the G limit), b.<zone>, m.<zone>[.<step>]: per zone, every run
PIPE_PY="$REPO/pipeline/.venv/bin/python"
SERVER_PY="$REPO/server/.venv/bin/python"
export FSSPEC_GS='{"block_size": 16384}'  # gcsfs reads by column with small blocks (catalogue_check's own magnitudes)
export PYTHONUNBUFFERED=1
mkdir -p "$WORK/plan" "$ZSTAMPS"

CUR="start"; LOGF=""
on_err() {
  local rc=$? line=$1
  echo >&2
  echo "STOPPED in step $CUR (exit $rc, tocheck.sh line $line)." >&2
  if [ -n "$LOGF" ] && [ -f "$LOGF" ]; then echo "Log: $LOGF; its last lines:" >&2; tail -n 15 "$LOGF" >&2; fi
  echo "Fix the cause, then run the same command again: finished steps are skipped." >&2
  exit "$rc"
}
trap 'on_err $LINENO' ERR
die() { echo >&2; echo "STOP ($CUR): $*" >&2; exit 1; }
say() { echo "[$(date -u +%H:%M:%S)] $*"; }
step_log() { CUR="$1"; LOGF="$RUN_LOGS/$1.log"; mkdir -p "$RUN_LOGS"; say "=== step $1: $2 (log $LOGF)" | tee -a "$LOGF"; }
confirm() {
  [ "$YES" = 1 ] && return 0
  local ans
  read -r -p "$1 [y/N] " ans || die "no answer (not a terminal?): rerun with --yes after checking --dry-run"
  case "$ans" in y|Y|yes|YES) ;; *) die "stopped at your answer; nothing more was done" ;; esac
}
plan_py() { if [ -x "$PIPE_PY" ]; then "$PIPE_PY" "$REPO/scripts/rebuild/rebuild.py" "$@"; else python3 "$REPO/scripts/rebuild/rebuild.py" "$@"; fi; }
rb() { "$PIPE_PY" "$REPO/scripts/rebuild/rebuild.py" "$@"; }

default_workers() {
  local cores mem
  cores=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
  mem=$(awk '/MemTotal/ {print int($2/1048576)}' /proc/meminfo 2>/dev/null || true)
  [ -n "$mem" ] || mem=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 17179869184) / 1073741824 ))
  local w=$(( cores / 2 )); [ $(( mem / 4 )) -lt "$w" ] && w=$(( mem / 4 )); [ "$w" -ge 1 ] || w=1
  echo "$w"
}
[ -n "$WORKERS" ] || WORKERS=$(default_workers)
CORES=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
free_gb() { df -Pk "$DATA" | awk 'NR==2 {print int($4/1048576)}'; }

# ------------------------------------------------------------------------------------------------ checks before anything
need_tools() {
  local miss=()
  for t in git uv gcloud curl; do command -v "$t" >/dev/null || miss+=("$t"); done
  [ "$DRY" = 1 ] || for t in gh node npm; do command -v "$t" >/dev/null || miss+=("$t"); done
  [ ${#miss[@]} -eq 0 ] || die "missing tools: ${miss[*]} (setup in docs/rebuild-on-pc.md, section 3)"
  local acct
  acct=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1)
  [ -n "$acct" ] || die "gcloud is not logged in: run  gcloud auth login"
  say "gcloud account: $acct"
  if [ "$DRY" = 0 ]; then
    gcloud auth application-default print-access-token >/dev/null 2>&1 \
      || die "no application-default login (the export reads the bucket through it): run  gcloud auth application-default login"
    if [ "$NO_PR" = 0 ]; then gh auth status >/dev/null 2>&1 || die "gh is not logged in: run  gh auth login"; fi
  fi
}

code_ready() {  # the merged code the rebuild needs (main must have these PRs)
  local x
  for x in '--checks-dir' 'MORE_DIR'; do
    grep -q -- "$x" "$REPO/pipeline/src/skyshift_pipeline/candidates.py" \
      || die "this checkout's exporter has no --checks-dir or candidates-more split (v3.24): update main (PR #369)"
  done
  grep -q 'warn_codes' "$REPO/pipeline/src/skyshift_pipeline/suspects.py" \
    || die "this checkout's suspects step is older than PR #374 (remove only the certain): update main"
  grep -q 'allcometels' "$REPO/scripts/candidates/known_check.py" \
    || die "scripts/candidates/known_check.py is older than PR #371 (every comet): update main"
  [ -f "$REPO/scripts/candidates/pixel_check.py" ] || die "scripts/candidates/pixel_check.py missing: update main (PR #372)"
  grep -q 'REFCAT_S3_FIRST' "$REPO/pipeline/src/skyshift_pipeline/config.py" \
    || die "this checkout's refcat has no AllWISE-on-S3 path (the catalogue fix, refcat.FULL): update main"
  grep -q -- '--ab-top' "$REPO/scripts/candidates/frames.py" || die "scripts/candidates/frames.py has no --ab-top: update main"
}

# ------------------------------------------------------------------------------------------------ plan (also the dry run)
do_plan() {
  CUR="plan"
  plan_py plan --data "$DATA" --work "$WORK" --zones "$ZONES"
  mapfile -t ZLIST < "$WORK/plan/zones.txt"
  PARTIAL=$(python3 -c "import json,sys; print(int(json.load(open(sys.argv[1]))['partial']))" "$WORK/plan/plan.json")
  local key
  key=$(printf '%s\n' "${ZLIST[@]}" | sort | cksum | cut -d' ' -f1)
  RUN="$WORK/run-$key"; RUN_STAMPS="$RUN/stamps"; RUN_LOGS="$RUN/logs"
  mkdir -p "$RUN_STAMPS" "$RUN_LOGS" "$RUN/checks" "$RUN/reports"
  printf '%s\n' "${ZLIST[@]}" > "$RUN/zones.txt"
  PUBLISH=1; { [ "$PARTIAL" = 1 ] && [ "$ALLOW_PARTIAL" = 0 ]; } && PUBLISH=0
  return 0
}

print_dry_run() {
  local fp=$(( WORKERS < 8 ? WORKERS : 8 ))
  cat <<EOF

Would run (with --workers $WORKERS, data in $DATA, work in $RUN):
  setup  uv sync --frozen in pipeline/ and server/; branch data/tocheck-rebuild-<date> from origin/main$([ "$PUBLISH" = 0 ] && echo " (not on a partial run)")
  a      gcloud storage cp --no-clobber of the manifest above ($WORK/plan/manifest.tsv), one zone at a time, sizes checked
  a0     rebuild.py refcat --zone <zone> (2 zones at once, SKYSHIFT_REFCAT_S3_FIRST=1): the zones whose refcat lacks
         CatWISE/2MASS or stops short of config REFCAT_GAIA_G_MAX; AllWISE from AWS S3, Gaia from ESA (free), ~0.4 GB disk each
EOF
  if [ -x "$PIPE_PY" ]; then
    { rb refcat-status --data "$DATA" --zones-file "$RUN/zones.txt" || echo "(refcat-status failed: see above)"; } | sed 's/^/         /'
  else
    echo "         (which zones: known after setup and a; 16 Nov: 64 Gaia-only zones, hunt and pilot complete)"
  fi
  if [ "$UPLOAD_REFCAT" = 1 ]; then
    echo "         --upload-refcat: then refcat/<zone>.parquet, _forced, _catalogs.json of those zones to $BUCKET/refcat/"
    echo "         (~9-13 GB, ~\$0.10-0.13/month Nearline; the old files copied first to $BUCKET/refcat/before-pc-rebuild/; asks)"
  fi
  if [ "$KNOWN_INDEX" = 1 ]; then
    echo "  h      skyshift-pipeline mpc-orbits --root $DATA --force; python -m skyshift_core.known_index <mpcorb> <cometels> <npz>$([ "$UPLOAD_INDEX" = 1 ] && echo "; upload ~115 MB to $BUCKET/cache/mpc/")"
  fi
  cat <<EOF
  b      for each of ${#ZLIST[@]} zones, $WORKERS at once: skyshift-pipeline suspects --zone <zone> --root $DATA
  m      for each zone, one at a time: rebuild.py fetch --manifest motion_manifest.tsv; rebuild.py motion --step
         crossid, classify, astrometry, highpm, shape (the pipeline's code on the mirror, writes local); rebuild.py mclean
EOF
  { plan_py mplan --data "$DATA" --work "$WORK" --zones-file "$RUN/zones.txt" \
      || echo "(mplan failed: see above; measured 16 Nov: ~502 GB of dot files + ~17 GB of tables, ~\$67)"; } | sed 's/^/         /'
  cat <<EOF
  c      rebuild.py export (skyshift-candidates on the overlay) --cache $DATA/cache/export --out $RUN/export/candidates.json
  d      known_check.py (all movers, --mpchecker 30), catalogue_check.py (--root $BUCKET), pixel_check.py (To check movers)
         skyshift-candidates --merge-only <export> --checks-dir $RUN/checks --out $RUN/final/candidates.json (+ candidates-more/)
EOF
  if [ "$PUBLISH" = 1 ]; then
    cat <<EOF
  e      copy into web/public/data; frames.py --ab-top $FRAMES_TOP --parallel $fp --deadline 900; fill.py for new hours frames
         (static frames for the top $FRAMES_TOP open A/B movers by score: ~$(( FRAMES_TOP * 45 / 100 )) MB in cand/, ~$(( FRAMES_TOP * 14 / 100 )) MB in git)
EOF
    if [ "$WITH_CHANGES" = 1 ]; then
      echo "  k      changes_step --zones <zone> --skip-done --drop-points (one process per zone), then changes_export"
      echo "         -> web/public/data/changes.json + changes.atom. Reads ~57 GB of Nearline light curves (11 columns), ~\$7"
      [ -f "$REPO/pipeline/src/skyshift_pipeline/changes_step.py" ] \
        || echo "         NOT READY in this checkout: needs PR #386 merged (and its chunking follow-up)"
    else
      echo "  k      off (add --with-changes): changes in known stars, ~57 GB of Nearline light curves = ~\$7 (the user decides)"
    fi
    cat <<EOF
  f      rebuild.py report (counts, sizes, 306P, spot checks, before/after origin/main); cd web && npm ci && npm test && npm run build
  g      git commit web/public/data; git push; gh pr create$([ "$NO_PR" = 1 ] && echo " (skipped: --no-pr)")
EOF
  else
    echo "  e, g   skipped: partial run (--zones). f checks $RUN/final instead (add --allow-partial to publish anyway)"
    echo "  f      rebuild.py report on $RUN/final"
  fi
  cat <<EOF

Time on a 16-core PC with ~100-1000 Mbit/s (estimates; docs/rebuild-on-pc.md, section 3):
  a 10-40 min | a0 1-3 h (3-8 h if Gaia must go deeper) | h 10-30 min | b 20-60 min
  m ~520 GB to copy (1.3 h at 1 Gbit/s, 13 h at 100 Mbit/s) + the five steps, one zone at a time (not measured: ~0.5-2 days)
  c 0.5-2 h (download-bound) | d 1-4 h | e 1-4 h (IRSA-bound) | k (--with-changes) ~1.5 h + 57 GB | f 10-20 min | g 5-15 min
Free disk now: $(free_gb) GB under $DATA (a full run needs ~200 GB: mirror ~11 GB, refcats ~25 GB, step m's tables ~17 GB and
dot files at their peak ~35 GB, export cache ~29 GB, frames, the repo).
Nothing was downloaded or run.
EOF
}

# ------------------------------------------------------------------------------------------------ stamps
stamp_done() { [ -f "$RUN_STAMPS/$1" ]; }
stamp() { date -u +%FT%TZ > "$RUN_STAMPS/$1"; }
clear_after() {  # a step ran again: the steps after it must too
  local s=$1 after=0 x
  for x in "${STEPS[@]}"; do
    if [ "$after" = 1 ]; then rm -f "$RUN_STAMPS/$x" "$RUN_STAMPS/$x".*; fi
    [ "$x" = "$s" ] && after=1
  done
  return 0
}
apply_redo() {
  [ -n "$REDO" ] || return 0
  rm -f "$RUN_STAMPS/$REDO" "$RUN_STAMPS/$REDO".*
  case "$REDO" in a|a0|b|m) rm -f "$ZSTAMPS/$REDO".* ;; esac
  case "$REDO" in h) clear_after c ;; a0) clear_after h ;; *) clear_after "$REDO" ;; esac
  say "--redo $REDO: its stamps and those of the later steps removed"
}
want_step() {  # $1: step letter
  if [ -n "$ONLY" ]; then [ "$1" = "$ONLY" ]; return; fi
  return 0
}

# ------------------------------------------------------------------------------------------------ setup
step_setup() {
  CUR="setup"; LOGF="$RUN_LOGS/setup.log"
  code_ready
  if [ "$PUBLISH" = 1 ]; then
    local br cur
    br=$(cat "$WORK/branch" 2>/dev/null || echo "data/tocheck-rebuild-$(date -u +%Y%m%d)")
    cur=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
    if [ "$cur" != "$br" ]; then
      [ -z "$(git -C "$REPO" status --porcelain)" ] \
        || die "the repo has uncommitted changes; commit them elsewhere first (the rebuild writes web/public/data on its own branch $br)"
      git -C "$REPO" fetch origin main
      git -C "$REPO" diff --quiet HEAD origin/main -- scripts/rebuild \
        || die "this checkout's scripts/rebuild differs from origin/main: run  git pull  on main, then this command again"
      if git -C "$REPO" rev-parse --verify --quiet "refs/heads/$br" >/dev/null; then
        git -C "$REPO" switch "$br"
      else
        git -C "$REPO" switch -c "$br" origin/main
      fi
      code_ready
    fi
    echo "$br" > "$WORK/branch"
    say "branch $br (from origin/main $(git -C "$REPO" rev-parse --short origin/main))"
  fi
  if [ ! -f "$WORK/setup.uv" ] || [ ! -x "$PIPE_PY" ] || [ ! -x "$SERVER_PY" ]; then
    say "uv sync (pipeline, server): Python 3.12 and the locked packages"
    (cd "$REPO/pipeline" && uv sync --frozen) 2>&1 | tee -a "$LOGF"
    (cd "$REPO/server" && uv sync --frozen) 2>&1 | tee -a "$LOGF"
    date -u +%FT%TZ > "$WORK/setup.uv"
  fi
  if [ ! -f "$WORK/speed.txt" ]; then
    local url="https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/dr1/level2/2025W30_2B/l2b-v27-2026-221/1/level2_2025W30_2B_0635_1D1_spx_l2b-v27-2026-221.fits"
    local bps
    bps=$(curl -s -r 0-33554431 -o /dev/null -w '%{speed_download}' "$url" || echo 0)
    echo "NASA S3 (free): $(awk -v b="$bps" 'BEGIN{printf "%.1f MB/s", b/1e6}') on a 32 MB read, $(date -u +%FT%TZ)" > "$WORK/speed.txt"
  fi
  say "$(cat "$WORK/speed.txt")"
  return 0
}

# ------------------------------------------------------------------------------------------------ a: mirror
step_a() {
  step_log a "mirror the bucket files suspects needs"
  local todo_gb
  todo_gb=$(python3 -c "import json,sys; print(round(json.load(open(sys.argv[1]))['mirror_todo']/1e9, 2))" "$WORK/plan/plan.json")
  if [ "$(python3 -c "print(int($todo_gb > 0))")" = 1 ]; then
    [ "$(free_gb)" -gt "$(python3 -c "print(int($todo_gb + 20))")" ] || die "not enough disk: $todo_gb GB to download + 20 GB margin, $(free_gb) GB free"
    confirm "Download $todo_gb GB from the bucket (~\$$(python3 -c "print(round($todo_gb * 0.13, 2))"))?"
  fi
  rb fetch --data "$DATA" --work "$WORK" --zone '*' 2>&1 | tee -a "$LOGF"
  local z pids=() n=0 failed=0
  for z in "${ZLIST[@]}"; do
    [ -f "$ZSTAMPS/a.$z" ] && continue
    ( trap - ERR; rb fetch --data "$DATA" --work "$WORK" --zone "$z" > "$RUN_LOGS/a.$z.log" 2>&1 && date -u +%FT%TZ > "$ZSTAMPS/a.$z" ) &
    pids+=($!); n=$((n + 1))
    if [ "$n" -ge 4 ]; then wait -n || failed=1; n=$((n - 1)); [ "$failed" = 0 ] || break; fi
  done
  while [ "$n" -gt 0 ]; do wait -n || failed=1; n=$((n - 1)); done
  local miss=()
  for z in "${ZLIST[@]}"; do [ -f "$ZSTAMPS/a.$z" ] || miss+=("$z"); done
  if [ ${#miss[@]} -gt 0 ]; then
    for z in "${miss[@]}"; do [ -f "$RUN_LOGS/a.$z.log" ] && { echo "--- $z"; tail -n 5 "$RUN_LOGS/a.$z.log"; }; done >&2
    die "the copy failed for ${#miss[@]} zone(s): ${miss[*]} (logs $RUN_LOGS/a.<zone>.log); rerun to resume"
  fi
  cat "$RUN_LOGS"/a.*.log >> "$LOGF" 2>/dev/null || true
  say "a: every file of ${#ZLIST[@]} zones is here" | tee -a "$LOGF"
}

# ------------------------------------------------------------------------------------------------ a0: refcat completion
refcat_gmax() { (cd "$REPO/pipeline" && "$PIPE_PY" -c 'from skyshift_pipeline import config as C; print(C.REFCAT_GAIA_G_MAX)'); }
step_a0() {
  local gmax z n=0 failed=0 par
  gmax=$(refcat_gmax)
  step_log a0 "complete the refcats: CatWISE/2MASS from AllWISE on AWS S3, Gaia to G < $gmax (config REFCAT_GAIA_G_MAX)"
  rb refcat-status --data "$DATA" --zones-file "$RUN/zones.txt" --verbose 2>&1 | tee -a "$LOGF"
  [ "$(free_gb)" -gt $(( ${#ZLIST[@]} / 2 + 10 )) ] || die "not enough disk for the refcats: ~0.4 GB per zone + 10 GB, $(free_gb) GB free"
  par=$(( WORKERS < 2 ? WORKERS : 2 ))   # 8 tiles in flight each; an AllWISE partition can take ~1 GB of RAM
  for z in "${ZLIST[@]}"; do
    [ "$(cat "$ZSTAMPS/a0.$z" 2>/dev/null)" = "$gmax" ] && continue
    say "refcat $z" | tee -a "$LOGF"
    ( trap - ERR
      SKYSHIFT_REFCAT_S3_FIRST=1 rb refcat --data "$DATA" --zone "$z" > "$RUN_LOGS/a0.$z.log" 2>&1 || exit 1
      if grep -q '"changed": true' "$RUN_LOGS/a0.$z.log"; then   # suspects and the m chain (crossid) run again there
        rm -f "$ZSTAMPS/b.$z" "$ZSTAMPS/m.$z" "$ZSTAMPS/m.$z".*
      fi
      echo "$gmax" > "$ZSTAMPS/a0.$z" ) &
    n=$((n + 1))
    if [ "$n" -ge "$par" ]; then wait -n || failed=1; n=$((n - 1)); [ "$failed" = 0 ] || break; fi
  done
  while [ "$n" -gt 0 ]; do wait -n || failed=1; n=$((n - 1)); done
  local miss=()
  for z in "${ZLIST[@]}"; do [ "$(cat "$ZSTAMPS/a0.$z" 2>/dev/null)" = "$gmax" ] || miss+=("$z"); done
  if [ ${#miss[@]} -gt 0 ]; then
    for z in "${miss[@]}"; do [ -f "$RUN_LOGS/a0.$z.log" ] && { echo "--- $z"; tail -n 8 "$RUN_LOGS/a0.$z.log"; }; done >&2
    die "refcat completion failed for ${#miss[@]} zone(s): ${miss[*]} (logs $RUN_LOGS/a0.<zone>.log; AWS S3 or ESA busy?): rerun to resume"
  fi
  grep -h '"changed"' "$RUN_LOGS"/a0.*.log >> "$LOGF" 2>/dev/null || true
  echo "$gmax" > "$RUN_STAMPS/a0.gmax"
  say "a0: every refcat of ${#ZLIST[@]} zones has Gaia (G < $gmax), CatWISE and 2MASS" | tee -a "$LOGF"
}
upload_refcat() {  # --upload-refcat: the completed refcats to the bucket; the old files are copied to a backup first
  CUR="a0.upload"; LOGF="$RUN_LOGS/a0.log"
  stamp_done a0 || die "--upload-refcat: step a0 has not finished"
  local y=(); [ "$YES" = 1 ] && y=(--yes)
  rb upload-refcat --data "$DATA" --work "$WORK" --zones-file "$RUN/zones.txt" "${y[@]}" 2>&1 | tee -a "$LOGF"
  return 0
}

# ------------------------------------------------------------------------------------------------ h: known index (optional)
newest() { local f=("$@"); [ ${#f[@]} -gt 0 ] || return 0; printf '%s\n' "${f[@]}" | sort | tail -n 1; }
step_h() {
  step_log h "the server's MPC known index with every comet"
  (cd "$REPO/pipeline" && "$REPO/pipeline/.venv/bin/skyshift-pipeline" mpc-orbits --root "$DATA" --force) 2>&1 | tee -a "$LOGF"
  local ast com d out
  ast=$(newest "$DATA"/cache/mpc/orbits/mpcorb_*.parquet)
  com=$(newest "$DATA"/cache/mpc/orbits/cometels_*.parquet)
  [ -n "$ast" ] && [ -n "$com" ] || die "mpc-orbits wrote no snapshot under $DATA/cache/mpc/orbits"
  d=$(basename "$ast" .parquet); d=${d#mpcorb_}
  out="$DATA/cache/mpc/known_index/known_index_$d.npz"
  mkdir -p "$(dirname "$out")"
  if [ ! -f "$out" ]; then
    (cd "$REPO/pipeline" && "$PIPE_PY" -m skyshift_core.known_index "$ast" "$com" "$out.tmp.npz" && mv "$out.tmp.npz" "$out") 2>&1 | tee -a "$LOGF"
  fi
  say "index $out ($(du -m "$out" | cut -f1) MB)" | tee -a "$LOGF"
}
upload_index() {  # --upload-known-index: the only bucket write; no-clobber, so running it again changes nothing
  CUR="h.upload"; LOGF="$RUN_LOGS/h.log"
  local ast com out
  ast=$(newest "$DATA"/cache/mpc/orbits/mpcorb_*.parquet)
  com=$(newest "$DATA"/cache/mpc/orbits/cometels_*.parquet)
  out=$(newest "$DATA"/cache/mpc/known_index/known_index_*.npz)
  [ -n "$ast" ] && [ -n "$com" ] && [ -n "$out" ] || die "nothing to upload: run with --known-index first"
  confirm "Upload $(basename "$out"), $(basename "$ast"), $(basename "$com") (~$(du -cm "$out" "$ast" "$com" | tail -n 1 | cut -f1) MB) to $BUCKET/cache/mpc/ (no overwrite)?"
  gcloud storage cp --no-clobber "$out" "$BUCKET/cache/mpc/known_index/" 2>&1 | tee -a "$LOGF"
  gcloud storage cp --no-clobber "$ast" "$com" "$BUCKET/cache/mpc/orbits/" 2>&1 | tee -a "$LOGF"
  echo "$(date -u +%FT%TZ) pc-rebuild UPLOAD $(basename "$out") $(basename "$ast") $(basename "$com")" >> "$DATA/DOWNLOADS.log"
  say "uploaded. Tell the orchestrator: Cloud Run needs SKYSHIFT_KNOWN_INDEX=mpc/known_index/$(basename "$out")" | tee -a "$LOGF"
  return 0
}
known_index_key() {
  local f
  f=$(newest "$DATA"/cache/mpc/known_index/known_index_*.npz)
  [ -n "$f" ] || die "no known index under $DATA/cache/mpc/known_index (step a copies the server's)"
  echo "mpc/known_index/$(basename "$f")"
}

# ------------------------------------------------------------------------------------------------ b: suspects
step_b() {
  step_log b "suspects for ${#ZLIST[@]} zones, $WORKERS at once"
  local z n=0 failed=0 ran=0
  for z in "${ZLIST[@]}"; do
    [ -f "$ZSTAMPS/a.$z" ] || die "zone $z is not mirrored (step a)"
  done
  for z in "${ZLIST[@]}"; do
    [ -f "$ZSTAMPS/b.$z" ] && continue
    [ -f "$DATA/results/$z/summary.bucket.json" ] || cp "$DATA/results/$z/summary.json" "$DATA/results/$z/summary.bucket.json"
    say "suspects $z" | tee -a "$LOGF"
    ran=1
    ( trap - ERR; cd "$REPO/pipeline" && "$REPO/pipeline/.venv/bin/skyshift-pipeline" suspects --zone "$z" --root "$DATA" \
        > "$RUN_LOGS/b.$z.log" 2>&1 && date -u +%FT%TZ > "$ZSTAMPS/b.$z" ) &
    n=$((n + 1))
    if [ "$n" -ge "$WORKERS" ]; then wait -n || failed=1; n=$((n - 1)); [ "$failed" = 0 ] || break; fi
  done
  while [ "$n" -gt 0 ]; do wait -n || failed=1; n=$((n - 1)); done
  [ "$ran" = 1 ] && clear_after b
  local miss=()
  for z in "${ZLIST[@]}"; do [ -f "$ZSTAMPS/b.$z" ] || miss+=("$z"); done
  if [ ${#miss[@]} -gt 0 ]; then
    for z in "${miss[@]}"; do [ -f "$RUN_LOGS/b.$z.log" ] && { echo "--- $z"; tail -n 8 "$RUN_LOGS/b.$z.log"; }; done >&2
    die "suspects failed or did not run for: ${miss[*]} (logs $RUN_LOGS/b.<zone>.log)"
  fi
  grep -h "skyshift.suspects: suspects" "$RUN_LOGS"/b.*.log 2>/dev/null | cut -c1-300 >> "$LOGF" || true
  say "b: suspects done for ${#ZLIST[@]} zones" | tee -a "$LOGF"
}

# ------------------------------------------------------------------------------------------------ m: the PR #388 chain
step_m() {
  step_log m "crossid -> classify -> astrometry -> highpm -> shape per zone (PR #388), untiled, on the mirror"
  rb mplan --data "$DATA" --work "$WORK" --zones-file "$RUN/zones.txt" 2>&1 | tee -a "$LOGF"
  local mp="$WORK/plan/motion_plan.json" gb need order=() z s
  gb=$(python3 -c "import json,sys; print(round(json.load(open(sys.argv[1]))['todo_bytes']/1e9, 1))" "$mp")
  need=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(int((d['dots_peak_bytes'] + d['tables_bytes'])/1e9) + 10)" "$mp")
  [ "$(free_gb)" -gt "$need" ] || die "not enough disk for step m: ~$need GB (dot files at their peak + tables + 10), $(free_gb) GB free"
  if [ "$(python3 -c "print(int($gb > 0))")" = 1 ]; then
    confirm "Step m copies $gb GB from the bucket (~\$$(python3 -c "print(round($gb * 0.13, 2))")): the zones' dot files once, and their tables. Go?"
  fi
  mapfile -t order < <(python3 -c "import json,sys; print('\n'.join(json.load(open(sys.argv[1]))['zones']))" "$mp")
  for z in "${order[@]}"; do   # one zone at a time: an untiled astrometry holds the zone's member dots in memory
    [ -f "$ZSTAMPS/m.$z" ] && continue
    say "m $z: copy its tables and dot files" | tee -a "$LOGF"
    rb fetch --data "$DATA" --work "$WORK" --manifest motion_manifest.tsv --zone "$z" >> "$RUN_LOGS/m.$z.log" 2>&1 \
      || die "the copy failed for $z (log $RUN_LOGS/m.$z.log); rerun to resume"
    for s in crossid classify astrometry highpm shape; do
      [ -f "$ZSTAMPS/m.$z.$s" ] && continue
      say "m $z: $s" | tee -a "$LOGF"
      rb motion --data "$DATA" --zone "$z" --step "$s" --workers "$CORES" >> "$RUN_LOGS/m.$z.log" 2>&1 \
        || die "$s failed for $z (log $RUN_LOGS/m.$z.log); rerun: it resumes at $s"
      date -u +%FT%TZ > "$ZSTAMPS/m.$z.$s"
    done
    date -u +%FT%TZ > "$ZSTAMPS/m.$z"
    rb mclean --data "$DATA" --work "$WORK" --stamps "$ZSTAMPS" >> "$RUN_LOGS/m.$z.log" 2>&1 || true
  done
  grep -h 'bucket reads beyond' "$RUN_LOGS"/m.*.log >> "$LOGF" 2>/dev/null || true
  say "m: the chain ran for ${#order[@]} zones; the export reads their new tables from the mirror" | tee -a "$LOGF"
}

# ------------------------------------------------------------------------------------------------ c: export
step_c() {
  step_log c "one export from the mirror + the bucket by column"
  rb estimate --data "$DATA" --work "$WORK" 2>&1 | tee -a "$LOGF"
  local gb
  gb=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['total_gb'])" "$WORK/plan/export_estimate.json")
  confirm "The export reads ~$gb GB from the bucket (~\$$(python3 -c "print(round($gb * 0.13, 2))")); reruns read from the cache. Go?"
  mkdir -p "$DATA/cache/export"
  rm -f "$DATA"/cache/export/results__*__suspects.*      # never a cached column read of an old suspects table ...
  local t                                                 # ... nor of a table step m wrote again
  for t in crossids classes astrometry astrometry_visits highpm_candidates highpm_visits shape_candidates; do
    rm -f "$DATA"/cache/export/results__*__"$t".parquet.*
  done
  rm -rf "$RUN/export"; mkdir -p "$RUN/export"
  local zl off=()
  zl=$(IFS=,; echo "${ZLIST[*]}")
  [ "$OFFLINE_EXPORT" = 0 ] || off=(--offline)
  # shellcheck disable=SC2086  # EXPORT_ARGS: words on purpose
  (cd "$REPO/pipeline" && "$PIPE_PY" "$REPO/scripts/rebuild/rebuild.py" export --data "$DATA" --bucket "$BUCKET" \
      --cache "$DATA/cache/export" --out "$RUN/export/candidates.json" --workers 32 --zones "$zl" \
      "${off[@]}" -- $EXPORT_ARGS) 2>&1 | tee -a "$LOGF"
  [ -s "$RUN/export/candidates.json" ] || die "the export wrote no $RUN/export/candidates.json"
  say "c: $(du -h "$RUN/export/candidates.json" | cut -f1) candidates.json$([ -d "$RUN/export/candidates-more" ] && echo ", candidates-more/ $(du -sh "$RUN/export/candidates-more" | cut -f1)")" | tee -a "$LOGF"
}

# ------------------------------------------------------------------------------------------------ d: producers + merge
step_d() {
  step_log d "check producers known, catalogue, pixel; merge"
  local exp="$RUN/export/candidates.json" more="$RUN/export/candidates-more" kin
  kin="$exp"
  if [ -d "$more" ] && compgen -G "$more/*.json" >/dev/null; then
    rb combine --main "$exp" --more "$more" --out "$RUN/export/all.json" 2>&1 | tee -a "$LOGF"
    kin="$RUN/export/all.json"
  fi
  if ! stamp_done d.known; then
    say "known_check (every mover, MPC + Horizons; MPChecker for the top 30)" | tee -a "$LOGF"
    (cd "$REPO/pipeline" && "$PIPE_PY" ../scripts/candidates/known_check.py --candidates "$kin" --root "$DATA" \
        --cache "$DATA/cache/known_check" --out "$RUN/checks/known.json" --mpchecker 30 \
        --mpchecker-out "$RUN/reports/known_mpchecker.json") 2>&1 | tee -a "$LOGF"
    stamp d.known; rm -f "$RUN_STAMPS/d.merge"
  fi
  if ! stamp_done d.catalogue; then
    say "catalogue_check (IRSA catalogues at every candidate of candidates.json)" | tee -a "$LOGF"
    (cd "$REPO/pipeline" && "$PIPE_PY" ../scripts/candidates/catalogue_check.py --candidates "$exp" \
        --out "$RUN/checks/catalogue.json" --cache "$DATA/cache/catalogue" --root "$BUCKET" \
        --col-cache "$DATA/cache/export") 2>&1 | tee -a "$LOGF"
    stamp d.catalogue; rm -f "$RUN_STAMPS/d.merge"
  fi
  if ! stamp_done d.pixel; then
    say "pixel_check (To check movers, NASA's photos by small S3 reads)" | tee -a "$LOGF"
    # shellcheck disable=SC2086  # PIXEL_ARGS: words on purpose
    (cd "$REPO/server" && SKYSHIFT_DATA="$DATA" "$SERVER_PY" ../scripts/candidates/pixel_check.py --candidates "$exp" \
        --root "$DATA" --out "$RUN/checks/pixel.json" $PIXEL_ARGS) 2>&1 | tee -a "$LOGF"
    stamp d.pixel; rm -f "$RUN_STAMPS/d.merge"
  fi
  if ! stamp_done d.merge; then
    rm -rf "$RUN/final"; mkdir -p "$RUN/final"
    # One call: --merge-only reads the export's more files (candidates-more/ next to it) and writes final/candidates.json
    # plus final/candidates-more/; a grade C mover the checks made known or flaw moves into candidates.json.
    (cd "$REPO/pipeline" && "$PIPE_PY" -m skyshift_pipeline.candidates --merge-only "$exp" --checks-dir "$RUN/checks" \
        --out "$RUN/final/candidates.json") 2>&1 | tee -a "$LOGF"
    [ -s "$RUN/final/candidates.json" ] || die "the merge wrote no $RUN/final/candidates.json"
    stamp d.merge
  fi
  say "d: $RUN/final ready" | tee -a "$LOGF"
}

# ------------------------------------------------------------------------------------------------ e: site files, frames, fill
step_e() {
  step_log e "web/public/data, static frames and fill"
  local dst="$REPO/web/public/data" key fp
  [ "$(git -C "$REPO" rev-parse --abbrev-ref HEAD)" = "$(cat "$WORK/branch")" ] || die "not on the rebuild branch $(cat "$WORK/branch")"
  if ! stamp_done e.copy; then
    cp "$RUN/final/candidates.json" "$dst/candidates.json"
    rm -rf "$dst/candidates-more"
    [ -d "$RUN/final/candidates-more" ] && cp -R "$RUN/final/candidates-more" "$dst/candidates-more"
    stamp e.copy; rm -f "$RUN_STAMPS/e.frames" "$RUN_STAMPS/e.fill"
  fi
  key=$(known_index_key)
  fp=$(( WORKERS < 8 ? WORKERS : 8 ))
  if ! stamp_done e.frames; then
    say "frames.py (top of each other kind + the $FRAMES_TOP best open A/B movers; MPC index $key)" | tee -a "$LOGF"
    (cd "$REPO/server" && SKYSHIFT_CACHE="$DATA/cache" SKYSHIFT_KNOWN_INDEX="$key" "$SERVER_PY" ../scripts/candidates/frames.py \
        --candidates "$dst/candidates.json" --ab-top "$FRAMES_TOP" --parallel "$fp" --deadline 900) 2>&1 | tee -a "$LOGF"
    local nmiss ntop
    read -r nmiss ntop <<< "$(rb frames-check --data-dir "$dst" --top "$FRAMES_TOP")"
    say "frames: $(( ntop - nmiss )) of the top $ntop open A/B movers have static frames; cand/ $(du -sh "$dst/cand" | cut -f1)" | tee -a "$LOGF"
    [ "$nmiss" -le $(( ntop / 20 + 5 )) ] || die "$nmiss of the top $ntop A/B movers got no frames (IRSA slow or down?): rerun the same command (built frames are kept)"
    stamp e.frames
  fi
  if ! stamp_done e.fill; then
    local ids=() chunk=() id n=0 failed=0
    mapfile -t ids < <(rb fill-todo --data-dir "$dst" --done "$RUN/fill_done.txt")
    say "fill.py for ${#ids[@]} hours frames (edge exposures)" | tee -a "$LOGF"
    run_fill() {  # ids...
      local args=() i
      for i in "$@"; do args+=(--id "$i"); done
      (cd "$REPO/server" && SKYSHIFT_CACHE="$DATA/cache" "$SERVER_PY" ../scripts/candidates/fill.py --out "$dst" "${args[@]}") \
        >> "$RUN_LOGS/e.fill.log" 2>&1 && printf '%s\n' "$@" >> "$RUN/fill_done.txt"
    }
    for id in "${ids[@]}"; do
      chunk+=("$id")
      if [ ${#chunk[@]} -ge 25 ]; then
        ( trap - ERR; run_fill "${chunk[@]}" ) & n=$((n + 1)); chunk=()
        if [ "$n" -ge 3 ]; then wait -n || failed=1; n=$((n - 1)); fi
      fi
    done
    if [ ${#chunk[@]} -gt 0 ]; then ( trap - ERR; run_fill "${chunk[@]}" ) & n=$((n + 1)); fi
    while [ "$n" -gt 0 ]; do wait -n || failed=1; n=$((n - 1)); done
    [ "$failed" = 0 ] || die "fill.py failed on some chunks (log $RUN_LOGS/e.fill.log); rerun to retry them"
    stamp e.fill
  fi
  say "e: web/public/data ready ($(du -sh "$dst/cand" | cut -f1) of frames)" | tee -a "$LOGF"
}

# ------------------------------------------------------------------------------------------------ k: changes in known stars
step_k() {   # only with --with-changes; the lines of PR #386's description
  step_log k "changes in known stars: per zone, then web/public/data/changes.json and changes.atom"
  [ -f "$REPO/pipeline/src/skyshift_pipeline/changes_step.py" ] \
    || die "--with-changes: step k needs PR #386 (the changes step) merged, and its chunking follow-up: update main"
  confirm "The changes step reads ~57 GB of light curves from the bucket (~\$7); a stopped run resumes. Go?"
  local z failed=()
  mkdir -p "$DATA/cache/changes"
  for z in "${ZLIST[@]}"; do   # one process per zone: a zone that runs out of memory stops only itself
    (cd "$REPO/pipeline" && "$PIPE_PY" -m skyshift_pipeline.changes_step --zones "$z" --skip-done --drop-points \
        --root "$BUCKET" --out-root "$DATA" --cache "$DATA/cache/changes") 2>&1 | tee -a "$LOGF" || failed+=("$z")
  done
  [ ${#failed[@]} -eq 0 ] || say "k: no changes for ${failed[*]} (see $LOGF); the other zones are listed" | tee -a "$LOGF"
  (cd "$REPO/pipeline" && "$PIPE_PY" -m skyshift_pipeline.changes_export --root "$DATA" \
      --out "$REPO/web/public/data/changes.json") 2>&1 | tee -a "$LOGF"
  [ -s "$REPO/web/public/data/changes.json" ] || die "the changes export wrote no web/public/data/changes.json"
}

# ------------------------------------------------------------------------------------------------ f: checks
step_f() {
  step_log f "checks before publishing"
  local dir="$RUN/final" frames=()
  if [ "$PUBLISH" = 1 ]; then dir="$REPO/web/public/data"; frames=(--expect-frames); fi
  frames+=(--frames-top "$FRAMES_TOP")
  git -C "$REPO" fetch origin main --quiet || true
  rb report --data-dir "$dir" --checks-dir "$RUN/checks" --previous origin/main "${frames[@]}" --out "$RUN/reports/check.md" 2>&1 | tee -a "$LOGF"
  if [ "$PUBLISH" = 1 ]; then
    (cd "$REPO/web" && npm ci && npm test && npm run build) 2>&1 | tee -a "$LOGF"
    say "web build: $(du -sh "$REPO/web/dist" | cut -f1) in web/dist" | tee -a "$LOGF"
  fi
  say "f: report $RUN/reports/check.md" | tee -a "$LOGF"
}

# ------------------------------------------------------------------------------------------------ g: commit, push, PR
step_g() {
  step_log g "commit, push, PR"
  local br; br=$(cat "$WORK/branch")
  [ "$(git -C "$REPO" rev-parse --abbrev-ref HEAD)" = "$br" ] || die "not on the rebuild branch $br"
  git -C "$REPO" add -A web/public/data/candidates.json web/public/data/cand
  [ -d "$REPO/web/public/data/candidates-more" ] && git -C "$REPO" add -A web/public/data/candidates-more
  if [ "$WITH_CHANGES" = 1 ]; then
    local f; for f in changes.json changes.atom; do
      [ -f "$REPO/web/public/data/$f" ] && git -C "$REPO" add "web/public/data/$f"
    done
  fi
  local stat; stat=$(git -C "$REPO" diff --cached --shortstat)
  if [ -n "$stat" ]; then
    local msg="$RUN/reports/commit.txt"
    {
      echo "data: To check rebuilt on the PC (${#ZLIST[@]} zones; suspects 16 Nov rule; known, catalogue, pixel checks)"
      echo
      python3 - "$REPO/web/public/data/candidates.json" <<'EOF'
import collections, json, sys
d = json.load(open(sys.argv[1]))
c = d["candidates"]
k = collections.Counter(x["kind"] for x in c)
g = collections.Counter(x.get("grade") for x in c if x["kind"] == "mover")
s = collections.Counter(x.get("status") for x in c)
print(f"candidates.json: {dict(k)}; movers A/B/C {g['A']}/{g['B']}/{g['C']}; status {dict(s)}")
EOF
      echo "Built with scripts/rebuild/tocheck.sh on main $(git -C "$REPO" merge-base HEAD origin/main | cut -c1-7); report in the PR."
      echo
      echo "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    } > "$msg"
    git -C "$REPO" commit -q -F "$msg"
    say "committed: $stat" | tee -a "$LOGF"
  else
    say "nothing new to commit" | tee -a "$LOGF"
  fi
  git -C "$REPO" push -u origin "$br" 2>&1 | tee -a "$LOGF"
  if [ "$NO_PR" = 1 ]; then say "g: pushed $br (no PR: --no-pr)"; return 0; fi
  if (cd "$REPO" && gh pr view "$br" >/dev/null 2>&1); then
    say "g: PR already open: $(cd "$REPO" && gh pr view "$br" --json url -q .url)" | tee -a "$LOGF"
    return 0
  fi
  local body="$RUN/reports/pr.md"
  {
    echo "The To check list rebuilt on the lead's PC with \`scripts/rebuild/tocheck.sh\` (docs/rebuild-on-pc.md)."
    echo
    echo "- Zones: ${#ZLIST[@]} ($(IFS=' '; echo "${ZLIST[*]}"))"
    echo "- Steps: refcats completed where Gaia-only (AllWISE/2MASS from AWS S3, Gaia to G < $(refcat_gmax)), suspects rerun per zone (remove only the certain), one export, check producers known / catalogue / pixel merged, static frames and fill for the top $FRAMES_TOP open A/B movers (the Viewer loads the rest live)."
    echo "- Built on origin/main $(git -C "$REPO" merge-base HEAD origin/main | cut -c1-7). Downloads logged in ~/skyshift-data/DOWNLOADS.log."
    echo "- For the orchestrator: merge, then deploy main (CLAUDE.md: pull, build, infra/firebase/deploy.sh hosting) and name the deployed commit here."
    echo
    head -c 55000 "$RUN/reports/check.md"
    echo
    echo "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
  } > "$body"
  (cd "$REPO" && gh pr create --base main --head "$br" --title "data: To check rebuilt on the PC (${#ZLIST[@]} zones)" \
      --body-file "$body") 2>&1 | tee -a "$LOGF"
}

# ------------------------------------------------------------------------------------------------ main
run() {  # step letter, function
  local s=$1 f=$2
  want_step "$s" || return 0
  if stamp_done "$s"; then say "step $s: done before ($(cat "$RUN_STAMPS/$s")), skipped"; return 0; fi
  "$f"
  stamp "$s"
  case "$s" in
    b) ;;                  # step_b clears the later steps itself when a zone ran again
    h) clear_after c ;;    # the index feeds d (known_check's base index) and e (frames), not b or c
    a0) clear_after h ;;   # the refcats feed b (and so everything after it), not h
    *) clear_after "$s" ;;
  esac
  return 0
}
main_flow() {
  CUR="checks"
  need_tools
  if [ "$REDO" = a ]; then rm -rf "$WORK/plan/listing"; fi   # list the bucket again (the listing is kept 12 h)
  do_plan
  if [ "$DRY" = 1 ]; then print_dry_run; return 0; fi
  apply_redo
  step_setup
  run a step_a
  if stamp_done a0 && want_step a0 && [ "$(cat "$RUN_STAMPS/a0.gmax" 2>/dev/null)" != "$(refcat_gmax)" ]; then
    rm -f "$RUN_STAMPS/a0"; say "config REFCAT_GAIA_G_MAX changed since step a0: a0 runs again"
  fi
  run a0 step_a0
  if [ "$UPLOAD_REFCAT" = 1 ] && want_step a0; then upload_refcat; fi
  if [ "$KNOWN_INDEX" = 1 ]; then run h step_h; fi
  if [ "$UPLOAD_INDEX" = 1 ] && want_step h; then upload_index; fi
  run b step_b
  run m step_m
  run c step_c
  run d step_d
  if [ "$PUBLISH" = 1 ]; then run e step_e
  elif want_step e; then say "step e skipped: partial run (--zones); add --allow-partial to publish it"; fi
  if [ "$WITH_CHANGES" = 1 ]; then
    if [ "$PUBLISH" = 1 ]; then run k step_k
    elif want_step k; then say "step k skipped: partial run (--zones)"; fi
  fi
  run f step_f
  if [ "$PUBLISH" = 1 ]; then run g step_g
  elif want_step g; then say "step g skipped: partial run; the result is in $RUN/final, the report in $RUN/reports/check.md"; fi
  CUR="end"
  if [ -n "$ONLY" ]; then say "Step $ONLY done. Logs: $RUN_LOGS"; else say "All done. Report: $RUN/reports/check.md"; fi
  return 0
}
# One line: bash has read it whole before it runs, so a script file changed by the branch switch can't derail it.
main_flow; exit $?
