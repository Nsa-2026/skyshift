#!/usr/bin/env bash
# Step `stack` (pipeline stack.py) on the five featured zones, one after another (P1). On skyshift-test, after the
# zones' last link/crossid (stack's `new` compares with results/<zone>/sources.parquet):
#   infra/worker/vm.sh run test 'infra/worker/stack_featured.sh' [REF]
# ZONES and WORKERS override the defaults. Each zone's cutout cache (/tmp/stack-<zone>, ~2.5 GB for Barnard) is removed
# once the zone is done (the disk is 30 GB); a failed zone keeps it, so a rerun reads nothing again. The first failure
# stops the run. Always ends by writing gs://nasa-glazing-data/skyrun/final/stack_featured.txt: machine, UTC time,
# exit code, commit (like skyrun.sh's markers).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
B=gs://nasa-glazing-data
ZONES=${ZONES:-t_barnard t_rwaur t_rdra t_wise0855 t_epsindib}
WORKERS=${WORKERS:-8}
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
ME=$(curl -fsS -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name 2>/dev/null || hostname)
marker() { echo "$ME $(date -u +%FT%TZ) exit=$1 sha=$SHA" | gcloud storage cp -q - "$B/skyrun/final/stack_featured.txt" >/dev/null 2>&1 || true; }
trap 'marker $?' EXIT
cd pipeline || exit 1
for z in $ZONES; do
  echo "=== stack $z $(date -u +%FT%TZ)"
  uv run skyshift-pipeline stack --zone "$z" --root "$B" --workers "$WORKERS" --cache "/tmp/stack-$z" || exit 1
  rm -rf "/tmp/stack-$z"
done
echo "=== stack featured done $(date -u +%FT%TZ)"
