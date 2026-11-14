# infra/CLAUDE.md

Cloud scripts for Google Cloud project nasa-glazing (us-east4, zone us-east4-a).
- worker/    P1. skyshift-test (e2-standard-8) for most work; skyshift-worker (n2-highcpu-64 spot)
             for the hunt zone, only after gate G2. Reads photos from s3://nasa-irsa-spherex.
- run/       P2. Cloud Run service skyshift-api, 0–2 instances. Deploy from the repo root with
             --source . (the root Dockerfile copies core/ and server/).
- firebase/  P2. Hosting (second site, e.g. skyshift-sky), public dir web/dist, rewrite /api/** to
             skyshift-api. Firestore rules for candidates and votes: create-only (contracts/api.md).

- Bucket gs://nasa-glazing-data: photolists/, dots/, results/, cache/.
- Never commit keys or service-account files. Credentials come from gcloud.
- Every script that starts a machine says how to stop it. Stop machines when idle; delete both after
  the final submission; delete dots/ on Monday.
