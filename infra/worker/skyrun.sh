#!/bin/bash
# skyrun.sh EXTRACT_WORKERS COMBINE_SLOTS ZONE... : the overnight sky run on one machine (14 Nov). Run from pipeline/.
#
# Extraction, zones in the given order: photo list + Gaia-only refcat (the next zone is prepared in the background
# while this one extracts), then `dots`. Markers in gs://nasa-glazing-data/skyrun/<state>/<zone>.txt (machine, UTC):
#   extracting -> extracted | extract_failed
# Combining, only when COMBINE_SLOTS > 0: every extracted zone from ANY machine, oldest first, up to COMBINE_SLOTS at a
# time at nice 19: `all` with the Gaia-only refcat (provisional: zone edges, no CatWISE yet). Markers:
#   combining -> combined | combine_failed
# A restarted machine (spot preemption) runs this again from the top: finished steps skip, markers are rewritten.
# Several machines may combine: a zone is claimed atomically (combining/<zone>.txt created only if absent).
set -u
W=$1; K=$2; shift 2; Z=("$@")
B=gs://nasa-glazing-data/skyrun
export SKYSHIFT_REFCAT_CATALOGS=gaia
ME=$(curl -fsS -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name 2>/dev/null || hostname)

mark() { echo "$ME $(date -u +%FT%TZ) ${3:-}" | gcloud storage cp -q - "$B/$1/$2.txt" >/dev/null 2>&1 || true; }
# time limits: refcat/photolist queries have no HTTP timeout, and a hung prep would block the loop at `wait`
prep() { timeout 900 uv run skyshift-pipeline photolist --zone "$1" && timeout 1200 uv run skyshift-pipeline refcat --zone "$1"; }

extract() {
  local i z n p
  for i in "${!Z[@]}"; do
    z=${Z[$i]}; n=${Z[$((i+1))]:-}
    prep "$z" > "/tmp/prep_$z.log" 2>&1 || prep "$z" > "/tmp/prep_$z.log" 2>&1 \
      || { echo "SKYRUN prep failed $z"; tail -5 "/tmp/prep_$z.log"; }
    p=""
    if [ -n "$n" ]; then ( prep "$n" > "/tmp/prep_$n.log" 2>&1 ) & p=$!; fi
    mark extracting "$z"
    if uv run skyshift-pipeline dots --zone "$z" --workers "$W"; then
      mark extracted "$z"; echo "SKYRUN done $z $(date -u +%H:%M)"
    else
      mark extract_failed "$z"; echo "SKYRUN dots failed $z"
    fi
    gcloud storage rm -q "$B/extracting/$z.txt" >/dev/null 2>&1 || true
    if [ -n "$p" ]; then wait "$p"; fi
  done
  echo "SKYRUN extraction finished $(date -u +%H:%M)"
}

names() { gcloud storage ls "$B/$1/" 2>/dev/null | sed 's|.*/||; s|\.txt$||'; }
# claim a zone for combining: creating combining/<zone>.txt only if it doesn't exist (atomic across machines)
claim() { echo "$ME $(date -u +%FT%TZ)" | gcloud storage cp -q --if-generation-match=0 - "$B/combining/$1.txt" >/dev/null 2>&1; }
# after a restart: our own stale claims go, so those zones can be combined again
for f in $(gcloud storage ls "$B/combining/" 2>/dev/null); do
  gcloud storage cat "$f" 2>/dev/null | grep -q "^$ME " && gcloud storage rm -q "$f" >/dev/null 2>&1
done

combine_loop() {
  declare -A running=()
  local idle=0 z rc ext finished
  while true; do
    for z in "${!running[@]}"; do
      if ! kill -0 "${running[$z]}" 2>/dev/null; then
        wait "${running[$z]}"; rc=$?
        if [ "$rc" -eq 0 ]; then mark combined "$z"; echo "SKYRUN combined $z $(date -u +%H:%M)"
        else mark combine_failed "$z" "exit=$rc"; echo "SKYRUN combine failed $z exit=$rc"; tail -5 "/tmp/comb_$z.log"; fi
        gcloud storage rm -q "$B/combining/$z.txt" >/dev/null 2>&1 || true
        unset "running[$z]"
      fi
    done
    # extracted zones, oldest marker first, not yet combined or failed
    ext=$(gcloud storage ls -l "$B/extracted/" 2>/dev/null | grep -v '^TOTAL' | sort -k2 | awk '{print $3}' | sed 's|.*/||; s|\.txt$||')
    finished=$(names combined; names combine_failed; names combining)
    for z in $ext; do
      [ "${#running[@]}" -ge "$K" ] && break
      [ -n "${running[$z]:-}" ] && continue
      printf '%s\n' "$finished" | grep -qx "$z" && continue
      claim "$z" || continue
      ( nice -n 19 uv run skyshift-pipeline all --zone "$z" --workers 8 > "/tmp/comb_$z.log" 2>&1 ) &
      running[$z]=$!
      echo "SKYRUN combining $z $(date -u +%H:%M)"
    done
    if ! kill -0 "$EXTRACTOR" 2>/dev/null && [ "${#running[@]}" -eq 0 ]; then
      idle=$((idle + 1)); [ "$idle" -ge 30 ] && break   # 30 min with nothing to do after our extraction ended
    else
      idle=0
    fi
    sleep 60
  done
  echo "SKYRUN combining loop finished $(date -u +%H:%M)"
}

extract &
EXTRACTOR=$!
if [ "$K" -gt 0 ]; then combine_loop; fi
wait "$EXTRACTOR"
