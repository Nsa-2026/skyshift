#!/bin/bash
# SkyShift machine start-up script (P1). Google runs it as root at every boot of
# skyshift-test and skyshift-worker. It:
#   1. installs Git and uv once;
#   2. checks out the repo at metadata `skyshift-ref` (default main; a branch name works);
#   3. runs metadata `skyshift-job`, a shell command run from the repo root. Output goes to the
#      serial console (Cloud Logging) and to gs://nasa-glazing-data/logs/<machine>/ every minute;
#   4. stops the machine when the job ends, unless metadata `skyshift-autostop` is "false".
# Set the metadata with infra/worker/vm.sh, not by hand.
set -uo pipefail

MD=http://metadata.google.internal/computeMetadata/v1/instance
md() { curl -fsS -H "Metadata-Flavor: Google" "$MD/attributes/$1" 2>/dev/null || true; }

NAME=$(curl -fsS -H "Metadata-Flavor: Google" "$MD/name")
REF=$(md skyshift-ref); REF=${REF:-main}
JOB=$(md skyshift-job)
AUTOSTOP=$(md skyshift-autostop); AUTOSTOP=${AUTOSTOP:-true}
BUCKET=gs://nasa-glazing-data
REPO=https://github.com/cucuradu/skyshift.git
UV_VERSION=0.13.8
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOGDIR=/var/log/skyshift
LOG=$LOGDIR/$STAMP.log

mkdir -p "$LOGDIR" /var/cache/uv
export HOME=/root UV_CACHE_DIR=/var/cache/uv PATH=/usr/local/bin:$PATH
export PYTHONUNBUFFERED=1

say() { echo "[skyshift $(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }
upload_log() { gcloud storage cp -q "$LOG" "$BUCKET/logs/$NAME/$STAMP.log" >/dev/null 2>&1 || true; }
finish() {
  echo "$STAMP exit=$1 ref=$REF commit=${COMMIT:-none} job=${JOB:-none}" \
    | gcloud storage cp -q - "$BUCKET/logs/$NAME/latest-status.txt" >/dev/null 2>&1 || true
  upload_log
  if [ "$AUTOSTOP" = "true" ]; then say "stopping the machine"; upload_log; shutdown -h now; fi
  exit 0
}

say "boot: machine=$NAME ref=$REF autostop=$AUTOSTOP job=${JOB:-<none>} cores=$(nproc)"

# 1. Tools (once per disk)
if ! command -v git >/dev/null; then
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -yqq git >/dev/null \
    || { say "ERROR: could not install git"; finish 10; }
fi
if [ "$(uv --version 2>/dev/null | cut -d' ' -f2)" != "$UV_VERSION" ]; then
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" \
    | tar -xz -C /usr/local/bin --strip-components=1 \
    || { say "ERROR: could not install uv $UV_VERSION"; finish 11; }
fi
say "tools: $(git --version), uv $(uv --version | cut -d' ' -f2)"

# 2. Code: the public repo at REF (branch, tag or commit)
if [ ! -d /opt/skyshift/.git ]; then
  git clone -q "$REPO" /opt/skyshift || { say "ERROR: git clone failed"; finish 12; }
fi
cd /opt/skyshift || finish 13
git fetch -q --prune origin
if ! git checkout -q --force --detach "origin/$REF" 2>/dev/null && ! git checkout -q --force --detach "$REF"; then
  say "ERROR: no such ref: $REF"; finish 14
fi
COMMIT=$(git rev-parse --short HEAD)
say "code: $REF at $COMMIT"

# 3. The job
if [ -z "$JOB" ]; then
  say "no job set; nothing to run"
  finish 0
fi
( while sleep 60; do upload_log; done ) &
UPLOADER=$!
say "job start: $JOB"
bash -c "$JOB" 2>&1 | tee -a "$LOG"
STATUS=${PIPESTATUS[0]}
kill "$UPLOADER" 2>/dev/null
say "job end: exit $STATUS"
finish "$STATUS"
