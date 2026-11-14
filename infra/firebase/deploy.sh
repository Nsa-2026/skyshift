#!/usr/bin/env bash
# Deploy the site (Firebase Hosting, site skyshift, https://skyshift.web.app) and the Firestore rules for nasa-glazing.
# Needs: `firebase login` once (firebase-tools), a built site (cd web && npm run build), and the
# Cloud Run service skyshift-api (infra/run/deploy.sh) for the /api/** rewrite.
#   infra/firebase/deploy.sh              # hosting + firestore rules
#   infra/firebase/deploy.sh hosting      # only the site
#   infra/firebase/deploy.sh firestore:rules  # only the rules
# Cost: Hosting and Firestore free tier for hackathon traffic.
# Roll back the site: Firebase console > Hosting > skyshift > release history > Roll back,
#   or: firebase hosting:clone skyshift:<previous-version> skyshift:live
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
only="${1:-hosting,firestore:rules}"

if [[ "$only" == *hosting* ]]; then
  [[ -f "$root/web/dist/index.html" ]] || { echo "web/dist is missing: cd web && npm run build" >&2; exit 1; }
  # Hosting serves infra/firebase/dist (inside the Firebase project folder): a fresh copy of web/dist.
  rm -rf "$here/dist"
  cp -R "$root/web/dist" "$here/dist"
fi
cd "$here"
firebase deploy --project nasa-glazing --only "$only"
