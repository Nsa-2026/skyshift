#!/bin/bash
# skyrun.sh EXTRACT_WORKERS COMBINE_SLOTS ZONE... : the overnight sky run on one machine (14-15 Nov). Run from pipeline/.
#
# Extraction (skipped when EXTRACT_WORKERS is 0), zones in the given order: photo list + Gaia-only refcat (the next zone
# is prepared in the background while this one extracts), then `dots`. A zone that already has an `extracted` marker is
# skipped; an `extract_failed` one is tried again; a zone whose photo list can't be fetched (IRSA down) is put back and
# retried in up to 3 later passes (after 2 misses in a row the loop waits 10 min). No new zone starts after SKYRUN_EXTRACT_UNTIL (UTC, default
# 2026-11-15T05:00:00Z = 06:00 CET). Markers in gs://nasa-glazing-data/skyrun/<state>/<zone>.txt
# ("machine UTC [detail] sha=<commit>"):
#   extracting -> extracted | extract_failed
# Combining, only when COMBINE_SLOTS > 0: every extracted zone from ANY machine, oldest first, up to COMBINE_SLOTS at a
# time, `all` with SKYRUN_COMBINE_WORKERS workers (default 8). Once this machine's extraction has ended (or with
# EXTRACT_WORKERS 0), up to SKYRUN_COMBINE_SLOTS_AFTER at a time (default COMBINE_SLOTS): extraction's memory is free then.
# CPU priority: combines at nice SKYRUN_COMBINE_NICE (default 19), dots at nice SKYRUN_DOTS_NICE (default 0). For
# combines that keep pace with extraction, swap them (COMBINE_NICE=0, DOTS_NICE=19) and give dots about every core:
# the combines take what they need and extraction soaks up the rest, so no core idles either way. SKYRUN_STAGE picks the pass:
#   provisional (default): markers in skyrun/<state>/       (zone edges, no calib)
#   final: + calib borrowed from the hunt zone when a zone lacks its own anchors, AllWISE veto in suspects (auto: only
#   when the refcat has no CatWISE), markers in skyrun/final/<state>/
#   Both: the prep's refcat is Gaia-only (fast start); each combine's `all` adds CatWISE2020 + 2MASS (AllWISE from S3
#   when IRSA's TAP fails) before link reads it.
#   combining -> combined | combine_failed
# SKYRUN_COMBINE_ONLY="z1 z2 ...": combine only these zones (rehearsal, morning priorities).
# Every step after `known` recomputes, so a final combine of a provisionally combined zone redoes link..suspects;
# finished photos and known sessions are reused.
# Time limits: prep 15+20 min, dots SKYRUN_DOTS_TIMEOUT (5h), a combine SKYRUN_COMBINE_TIMEOUT (3h). Each combine's log goes to skyrun/logs/<stage>/<zone>.log.
# A restarted machine (spot preemption) runs this again from the top: finished steps skip, markers are rewritten.
# Several machines may combine: a zone is claimed atomically (combining/<zone>.txt created only if absent).
set -u
W=$1; K=$2; shift 2; Z=("$@")
B=gs://nasa-glazing-data/skyrun
STAGE=${SKYRUN_STAGE:-provisional}
CW=${SKYRUN_COMBINE_WORKERS:-8}
KX=${SKYRUN_COMBINE_SLOTS_AFTER:-$K}
CNICE=${SKYRUN_COMBINE_NICE:-19}
DNICE=${SKYRUN_DOTS_NICE:-0}
UNTIL=$(date -u -d "${SKYRUN_EXTRACT_UNTIL:-2026-11-15T05:00:00Z}" +%s)
# Gaia-only refcat for the extraction prep ONLY (prep below). It was exported here for the whole script until 16 Nov,
# so every combine's `all` skipped its refcat too and 64 zones (59 ecliptic, 5 featured targets) were combined
# without CatWISE and 2MASS.
# The pipeline's `all` now builds every catalogue whatever the environment says (refcat.FULL).
case $STAGE in
  provisional) CB=$B ;;
  final) CB=$B/final
         export SKYSHIFT_CALIB_BORROW=${SKYSHIFT_CALIB_BORROW:-auto} SKYSHIFT_CALIB_REF=${SKYSHIFT_CALIB_REF:-hunt} \
                SKYSHIFT_SUSPECTS_ALLWISE=${SKYSHIFT_SUSPECTS_ALLWISE:-auto} ;;
  *) echo "SKYRUN unknown SKYRUN_STAGE=$STAGE (provisional or final)"; exit 2 ;;
esac
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
ME=$(curl -fsS -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name 2>/dev/null || hostname)
echo "SKYRUN start $ME sha=$SHA stage=$STAGE extract_workers=$W combine_slots=$K after_extraction=$KX combine_workers=$CW nice_combine=$CNICE nice_dots=$DNICE zones=${#Z[@]}"

# Right after a boot `gcloud storage` can fail for minutes (seen on the worker, 14 Nov 19:59-20:05): every read then
# looked empty, so extracted zones were redone, our stale claims stayed and present photo lists were "missing".
# Wait until the bucket answers, and retry a failed read; "matched no objects" (an empty folder) is an answer.
bucket_ok() { gcloud storage ls "$B/" >/dev/null 2>&1; }
for i in $(seq 1 40); do bucket_ok && break; echo "SKYRUN bucket not answering yet ($i)"; sleep 15; done

# mark ROOT STATE ZONE [DETAIL]
mark() { echo "$ME $(date -u +%FT%TZ) ${4:+$4 }sha=$SHA" | gcloud storage cp -q - "$1/$2/$3.txt" >/dev/null 2>&1 || true; }
names() {
  local i out
  for i in 1 2 3 4 5; do
    if out=$(gcloud storage ls "$1/$2/" 2>&1); then printf '%s\n' "$out" | sed 's|.*/||; s|\.txt$||'; return 0; fi
    printf '%s' "$out" | grep -q "matched no objects" && return 0
    sleep 15
  done
  echo "SKYRUN cannot list $1/$2/" >&2; return 1
}
# time limits: refcat/photolist queries have no HTTP timeout, and a hung prep would block the loop at `wait`
prep() { timeout 900 uv run skyshift-pipeline photolist --zone "$1" && SKYSHIFT_REFCAT_CATALOGS=gaia timeout 1200 uv run skyshift-pipeline refcat --zone "$1"; }

# missing only when the bucket answers and the file isn't there; a failed call is retried
has_photolist() {
  local i
  for i in 1 2 3 4 5; do
    gcloud storage ls "gs://nasa-glazing-data/photolists/zones/$1.parquet" >/dev/null 2>&1 && return 0
    bucket_ok && return 1
    sleep 15
  done
  return 1
}

extract() {
  local i z n p done_ todo=() deferred=() pass fails=0
  done_=$(names "$B" extracted)
  for z in "${Z[@]}"; do printf '%s\n' "$done_" | grep -qx "$z" || todo+=("$z"); done
  echo "SKYRUN extraction: ${#todo[@]} of ${#Z[@]} zones left"
  for pass in 1 2 3 4; do
  [ "$pass" -gt 1 ] && { [ "${#deferred[@]}" -eq 0 ] && break; todo=("${deferred[@]}"); deferred=();
                         echo "SKYRUN retrying ${#todo[@]} deferred zones (pass $pass)"; }
  for i in "${!todo[@]}"; do
    if [ "$(date -u +%s)" -ge "$UNTIL" ]; then echo "SKYRUN extraction stops: past ${SKYRUN_EXTRACT_UNTIL:-05:00Z}"; break 2; fi
    z=${todo[$i]}; n=${todo[$((i+1))]:-}
    prep "$z" > "/tmp/prep_$z.log" 2>&1 || prep "$z" > "/tmp/prep_$z.log" 2>&1 \
      || { echo "SKYRUN prep failed $z"; tail -5 "/tmp/prep_$z.log"; }
    # no photo list (IRSA down): don't fail the zone, put it back for a later pass; after 2 in a row wait 10 min
    if ! has_photolist "$z"; then
      echo "SKYRUN no photo list for $z (IRSA?): deferred"; deferred+=("$z"); fails=$((fails + 1))
      [ "$fails" -ge 2 ] && sleep 600
      continue
    fi
    fails=0
    p=""
    if [ -n "$n" ]; then ( prep "$n" > "/tmp/prep_$n.log" 2>&1 ) & p=$!; fi
    mark "$B" extracting "$z"
    if timeout "${SKYRUN_DOTS_TIMEOUT:-5h}" nice -n "$DNICE" uv run skyshift-pipeline dots --zone "$z" --workers "$W"; then
      mark "$B" extracted "$z"; gcloud storage rm -q "$B/extract_failed/$z.txt" >/dev/null 2>&1 || true
      echo "SKYRUN done $z $(date -u +%H:%M)"
    else
      mark "$B" extract_failed "$z" "exit=$?"; echo "SKYRUN dots failed $z"
    fi
    gcloud storage rm -q "$B/extracting/$z.txt" >/dev/null 2>&1 || true
    if [ -n "$p" ]; then wait "$p"; fi
  done
  done
  echo "SKYRUN extraction finished $(date -u +%H:%M) (${#deferred[@]} zones still without a photo list)"
}

# claim a zone for combining: creating combining/<zone>.txt only if it doesn't exist (atomic across machines)
claim() { echo "$ME $(date -u +%FT%TZ) sha=$SHA" | gcloud storage cp -q --if-generation-match=0 - "$CB/combining/$1.txt" >/dev/null 2>&1; }
# after a restart: our own stale claims go, so those zones can be combined again
for f in $(gcloud storage ls "$CB/combining/" 2>/dev/null); do
  gcloud storage cat "$f" 2>/dev/null | grep -q "^$ME " && gcloud storage rm -q "$f" >/dev/null 2>&1
done

combine_loop() {
  declare -A running=()
  local idle=0 z rc ext finished slots
  while true; do
    for z in "${!running[@]}"; do
      if ! kill -0 "${running[$z]}" 2>/dev/null; then
        wait "${running[$z]}"; rc=$?
        if [ "$rc" -eq 0 ]; then mark "$CB" combined "$z"; echo "SKYRUN combined $z $(date -u +%H:%M)"
        else mark "$CB" combine_failed "$z" "exit=$rc"; echo "SKYRUN combine failed $z exit=$rc"; tail -5 "/tmp/comb_$z.log"; fi
        gcloud storage cp -q "/tmp/comb_$z.log" "$B/logs/$STAGE/$z.log" >/dev/null 2>&1 || true
        gcloud storage rm -q "$CB/combining/$z.txt" >/dev/null 2>&1 || true
        unset "running[$z]"
      fi
    done
    # extracted zones, oldest marker first, not yet combined, failed or claimed in this stage
    ext=$(gcloud storage ls -l "$B/extracted/" 2>/dev/null | grep -v '^TOTAL' | sort -k2 | awk '{print $3}' | sed 's|.*/||; s|\.txt$||')
    finished=$(names "$CB" combined; names "$CB" combine_failed; names "$CB" combining)
    slots=$K; kill -0 "$EXTRACTOR" 2>/dev/null || slots=$KX   # extraction over: its cores and memory go to combining
    for z in $ext; do
      [ "${#running[@]}" -ge "$slots" ] && break
      if [ -n "${SKYRUN_COMBINE_ONLY:-}" ]; then printf '%s\n' $SKYRUN_COMBINE_ONLY | grep -qx "$z" || continue; fi
      [ -n "${running[$z]:-}" ] && continue
      printf '%s\n' "$finished" | grep -qx "$z" && continue
      claim "$z" || continue
      ( timeout "${SKYRUN_COMBINE_TIMEOUT:-3h}" nice -n "$CNICE" uv run skyshift-pipeline all --zone "$z" --workers "$CW" > "/tmp/comb_$z.log" 2>&1 ) &
      running[$z]=$!
      echo "SKYRUN combining $z $(date -u +%H:%M) stage=$STAGE"
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

if [ "$W" -gt 0 ]; then extract & else true & fi
EXTRACTOR=$!
if [ "$K" -gt 0 ] || [ "$KX" -gt 0 ]; then combine_loop; fi
wait "$EXTRACTOR"
