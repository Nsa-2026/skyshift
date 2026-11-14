#!/usr/bin/env bash
# Deploy the SkyShift API (server/) to Cloud Run: service skyshift-api, project nasa-glazing, us-east4.
#
# Run from anywhere; it cds to the repo root, because the root Dockerfile needs core/ and server/.
#   infra/run/deploy.sh                # build with Cloud Build from source, deploy a new revision
#
# Cost: 0 when idle (min-instances 0; you pay per request-second while a request runs, up to
#   2 instances x 2 vCPU x 2 GiB). Plus a few cents of Cloud Build and Artifact Registry storage
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
set -euo pipefail
cd "$(dirname "$0")/../.."

gcloud run deploy skyshift-api \
  --project nasa-glazing \
  --source . \
  --region us-east4 \
  --service-account skyshift-api@nasa-glazing.iam.gserviceaccount.com \
  --min-instances 0 \
  --max-instances 2 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 8 \
  --allow-unauthenticated \
  --set-env-vars SKYSHIFT_CACHE=gs://nasa-glazing-data/cache
