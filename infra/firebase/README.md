# infra/firebase (P2)

Firebase Hosting for the site and Firestore rules, project `nasa-glazing`.

| file | what |
|---|---|
| `firebase.json` | Hosting site `skyshift` (https://skyshift.web.app): serves `dist/` (a copy of `web/dist`, made by `deploy.sh`), rewrites `/api/**` to Cloud Run `skyshift-api` (us-east4), SPA fallback to `/index.html`, cache headers (`/data/**/*.bin`: 1 day in browsers, 1 year on the CDN, which is purged on every deploy) |
| `firestore.rules` | `candidates` and `votes`: read and create only, exact fields, types and values from contracts/api.md |
| `.firebaserc` | default project `nasa-glazing` |
| `deploy.sh` | copies `web/dist` here and runs `firebase deploy` |

## Deploy (you run these; the agents never deploy)

```sh
npm install -g firebase-tools          # once
firebase login                          # once, in a browser
# site skyshift (https://skyshift.web.app) already exists (created 14 Nov)
infra/run/deploy.sh                     # the API first, so the /api/** rewrite has a target
(cd web && npm ci && npm run build)
infra/firebase/deploy.sh                # hosting + firestore rules
```

`infra/firebase/deploy.sh hosting` or `infra/firebase/deploy.sh firestore:rules` deploys one part.
Firestore must already exist in us-east4 (it does, per CLAUDE.md).

## Rules: what was checked (by reading; no emulator test)

- `candidates` create: exactly `ra, dec, mjd, clock, source, created`; `ra` number 0–360, `dec` −90–90,
  `mjd` number 60000–80000, `clock` `months`|`hours`, `source` must be `user` (pipeline suspects
  are written with the Admin SDK, which bypasses rules, so browsers cannot pose as the pipeline),
  `created == request.time` (the browser must send `serverTimestamp()`; contracts/api.md v2.1).
  Deploy the rules together with a site that sends `created`, or flagging is refused.
- `votes` create: exactly `candidate_id, answer, ts, voter`; `candidate_id` string 1–128 chars,
  `answer` `mover`|`changing`|`junk`|`unsure`, `ts == request.time` (the browser must send
  `serverTimestamp()`), `voter` a random id `[A-Za-z0-9_-]{8,64}` (never a name or email).
- Reads open on both; update and delete denied; every other collection closed.
- Votes don't check that the candidate exists (pipeline suspects may reach the site before
  Firestore); add `exists(/databases/$(database)/documents/candidates/$(request.resource.data.candidate_id))`
  if they always will be there.
