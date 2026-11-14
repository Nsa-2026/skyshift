#!/bin/bash
# SkyShift cloud machines (P1). Run from anywhere; needs gcloud logged in to project nasa-glazing.
#
#   infra/worker/vm.sh create test|worker|od|od2 create the machine; it boots, installs, then stops itself
#   infra/worker/vm.sh run test|worker 'JOB' [REF]
#                                                start JOB (a shell command run from the repo root) on
#                                                the code at REF (default main); the machine stops itself
#                                                when the job ends. AUTOSTOP=false keeps it running.
#   infra/worker/vm.sh log test|worker [N]       last N lines of the job output (default 60)
#   infra/worker/vm.sh status                    machines, state, last job result
#   infra/worker/vm.sh stop test|worker          stop now (a pipeline job resumes on the next run)
#
# test   = skyshift-test, e2-standard-8, normal: pilot, validation, precompute (about EUR 0.30/h)
# worker = skyshift-worker, n2-highcpu-64, spot: hunt zone only, after gate G2 (about EUR 0.60-1/h)
# od/od2 = skyshift-od, skyshift-od2: on-demand machines for the sky run (spot quota is 96 CPUs, on-demand 128
#          for the project). Type from MACHINE (default n2-custom-80-163840; N2 custom stops at 80 CPUs)
# A stopped machine costs only its 30 GB disk (cents a day). To delete one (after the final
# submission): gcloud compute instances delete skyshift-test --zone us-east4-a
set -euo pipefail

PROJECT=nasa-glazing
ZONE=us-east4-a
SA=skyshift-worker@$PROJECT.iam.gserviceaccount.com
BUCKET=gs://nasa-glazing-data
HERE=$(cd "$(dirname "$0")" && pwd)
G=${GCLOUD:-gcloud}
command -v "$G" >/dev/null || G=$HOME/google-cloud-sdk/bin/gcloud

vm_name() {
  case "${1:-}" in
    test) echo skyshift-test ;;
    worker) echo skyshift-worker ;;
    od|od2) echo skyshift-$1 ;;
    *) echo "say 'test', 'worker', 'od' or 'od2'" >&2; exit 2 ;;
  esac
}

state() { "$G" compute instances describe "$1" --project=$PROJECT --zone=$ZONE --format='value(status)' 2>/dev/null || echo MISSING; }

create() {
  local vm; vm=$(vm_name "$1")
  local args=(--project=$PROJECT --zone=$ZONE
    --image-family=debian-12 --image-project=debian-cloud
    --boot-disk-size=30GB --boot-disk-type=pd-balanced
    --service-account=$SA --scopes=cloud-platform
    --metadata-from-file=startup-script="$HERE/startup.sh"
    --metadata=skyshift-ref=main,skyshift-autostop=true
    --labels=app=skyshift,owner=p1)
  if [ "$1" = test ]; then
    args+=(--machine-type=e2-standard-8)
  elif [ "$1" = od ] || [ "$1" = od2 ]; then
    args+=(--machine-type=${MACHINE:-n2-custom-80-163840})
  else
    args+=(--machine-type=n2-highcpu-64 --provisioning-model=SPOT --instance-termination-action=STOP)
  fi
  "$G" compute instances create "$vm" "${args[@]}"
  echo "$vm is installing; it stops itself when done. Check: $0 log $1"
}

run() {
  local vm; vm=$(vm_name "$1")
  local job=${2:?give the job, e.g. 'cd pipeline && uv run skyshift-pipeline all --zone pilot'}
  local ref=${3:-main} autostop=${AUTOSTOP:-true}
  local s; s=$(state "$vm")
  if [ "$s" != TERMINATED ]; then
    echo "$vm is $s. Wait until it stops (it does so after its job), or: $0 stop $1" >&2
    exit 1
  fi
  local f; f=$(mktemp)
  printf '%s' "$job" > "$f"
  "$G" compute instances add-metadata "$vm" --project=$PROJECT --zone=$ZONE \
    --metadata-from-file=skyshift-job="$f",startup-script="$HERE/startup.sh" \
    --metadata=skyshift-ref="$ref",skyshift-autostop="$autostop" >/dev/null
  rm -f "$f"
  "$G" compute instances start "$vm" --project=$PROJECT --zone=$ZONE
  echo "$vm started on $ref: $job"
  echo "Follow it: $0 log $1   ·   stop it: $0 stop $1"
}

log() {
  # Running machine: its serial console. Stopped machine: the last log copied to the bucket.
  local vm; vm=$(vm_name "$1")
  local n=${2:-60}
  if [ "$(state "$vm")" = RUNNING ]; then
    "$G" compute instances get-serial-port-output "$vm" --project=$PROJECT --zone=$ZONE 2>/dev/null \
      | grep -a 'Metadata key("startup-script")' | sed -E 's/^.*command\("[^"]*"\): //' | tail -n "$n"
  else
    local last; last=$("$G" storage ls "$BUCKET/logs/$vm/" 2>/dev/null | grep '\.log$' | sort | tail -n 1)
    [ -n "$last" ] && { echo "($vm is stopped; last log: $last)"; "$G" storage cat "$last" | tail -n "$n"; }
  fi
}

status() {
  "$G" compute instances list --project=$PROJECT --filter='labels.app=skyshift' \
    --format='table(name,machineType.basename(),scheduling.provisioningModel,status)'
  for vm in skyshift-test skyshift-worker skyshift-od skyshift-od2; do
    local s; s=$("$G" storage cat "$BUCKET/logs/$vm/latest-status.txt" 2>/dev/null || true)
    [ -n "$s" ] && echo "$vm last job: $s"
  done
  return 0
}

stop() {
  local vm; vm=$(vm_name "$1")
  "$G" compute instances stop "$vm" --project=$PROJECT --zone=$ZONE
}

cmd=${1:-}
shift || true
case "$cmd" in
  create) create "$@" ;;
  run) run "$@" ;;
  log) log "$@" ;;
  status) status ;;
  stop) stop "$@" ;;
  *) sed -n '2,17p' "$0"; exit 2 ;;
esac
