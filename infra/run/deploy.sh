#!/usr/bin/env bash
# Deploy the SkyShift API (server/) to Cloud Run: service skyshift-api, project nasa-glazing, us-east4.
#
# Run from anywhere; it cds to the repo root, because the root Dockerfile needs core/ and server/.
#   infra/run/deploy.sh                # build with Cloud Build from source, deploy a new revision
# The build runs as the default compute service account, which was granted roles/run.builder on
# 14 Nov 2026 so that `gcloud run deploy --source` can build.
#
# Capacity (14 Nov, after the speed work): a live build is network-bound (range reads from S3,
# about 0.05 s of CPU per cutout), answers inside its 40 s budget (server/src/skyshift_server/app.py)
# and identical requests share one build. Two uvicorn worker processes (Dockerfile) use both
# vCPUs; 8 requests per instance, most of them cheap /api/data frame reads (an hours blink asks
# for 20-48 of them at once). A client that leaves stops its build (app._shared).
# Up to 8 instances (64 requests at once) so a room of judges never queues (Cloud Run answered 429
# "no available instance" with 2 instances when each /api/object took 200+ s).
# Startup CPU boost: the first request on a cold instance (about 6 s to start) is not slowed down.
# min-instances 0: idle costs nothing. min-instances 1 would remove the cold start for about
# $0.80/day (2 vCPU + 2 GiB idle); the cold start is not the main cost of a live request, so 0.
# SKYSHIFT_PIXELS=s3first: cutouts from NASA's S3 copy with direct range reads (fast, never
# busy); IRSA's cutout service only for an image S3 has no copy of.
#
# Cost: 0 when idle (min-instances 0; you pay per request-second while a request runs, up to
#   8 instances x 2 vCPU x 2 GiB). Plus a few cents of Cloud Build and Artifact Registry storage
#   per deploy. Cutouts and frames are cached in gs://nasa-glazing-data/cache/ (pennies).
# Stop it costing anything: nothing to stop (it scales to zero). To take it offline:
#   gcloud run services delete skyshift-api --region us-east4 --project nasa-glazing
# Roll back to the previous revision:
#   gcloud run revisions list --service skyshift-api --region us-east4 --project nasa-glazing
#   gcloud run services update-traffic skyshift-api --region us-east4 --project nasa-glazing \
#       --to-revisions <previous-revision>=100
# Check it:  curl "$(gcloud run services describe skyshift-api --region us-east4 \
#                --project nasa-glazing --format 'value(status.url)')/api/health"
# Logs:      gcloud run services logs read skyshift-api --region us-east4 --project nasa-glazing
# Where the time went: one "timing ..." line per live build:
#   gcloud logging read 'resource.labels.service_name="skyshift-api" AND textPayload:"timing"' \
#       --project nasa-glazing --freshness=1h --format='value(textPayload)'
set -euo pipefail
cd "$(dirname "$0")/../.."

gcloud run deploy skyshift-api \
  --project nasa-glazing \
  --source . \
  --region us-east4 \
  --service-account skyshift-api@nasa-glazing.iam.gserviceaccount.com \
  --min-instances 0 \
  --max-instances 8 \
  --memory 2Gi \
  --cpu 2 \
  --cpu-boost \
  --timeout 300 \
  --concurrency 8 \
  --allow-unauthenticated \
  --set-env-vars SKYSHIFT_CACHE=gs://nasa-glazing-data/cache,SKYSHIFT_PIXELS=s3first
