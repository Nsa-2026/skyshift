# Scientific workbench release candidate

Prepared 14 November 2026 on `codex/science-production-integration`.
This is the current integration record; older handoff documents describe earlier local prototypes.

The user subsequently requested: retain the integration and do not deploy for now. No deployment should proceed until the user resumes it.

## Source and scope

- Started from upstream `8522cd7`, then incorporated the latest fetched main, `84b0f1c` (including the QA pipeline, honest UI copy, featured zones and measured-layer contract).
- Imported the user's scientific work from `9b1c775`, retaining its history. The original prototype and working copies were not changed.
- Preserved main's Lightbox UI, sky atlas, original blink observations, object measurements, review, live search, courses, votes and tools.
- Added the `Science` navigation entry at `#/explore/science`. Models, residuals and DR1 differences use the existing player, with an explicit derived-layer caption. The object card retains main's measurements.
- Included motion and cold-source checks, D3/D6 comparison, DR1 reference spectrum, synthetic W1/W2 and experimental halo light curves, spectral audio, sensitivity estimates, catalogue checks, cultural modules and exports.
- This release does not import the prototype's game or API inspector.

The change spans core (legacy analysis adapter), server, web, precompute scripts and container packaging. Repeated wavelengths now use the weighted-mean fallback when the polynomial normal matrix is rank deficient; a regression test also checks that full-rank spectra retain their polynomial fit. The first remote Linux CI run exposed a pre-existing race in spectra loader statistics: shared counter updates occasionally omitted a photo. Workers now return their own counts for serial aggregation. A barrier-based regression reproduces the loss before the fix (10 instead of 40) and passes after it. No new formal contract fields are introduced by this integration.

## Data and deployment boundaries

Five fields have versioned portable scientific products: Barnard's Star, Epsilon Indi B, WISE 0855−0714, RW Aurigae and R Draconis. They live in `web/public/science-data`, separate from main's `web/public/data`. Their published models, residuals and reference differences support the 5 arcminute Months selection, with the actual smaller footprint masked outside its coverage. API matching is limited to two arcseconds around each published centre. Analysis findings are detector-specific; unsupported selections are disclosed.

The container copies these products and export inputs, so production does not need native NPZ jobs, pickle caches or the original `spherex-tm` directory. Raw observations never use the legacy analysis cube. JSON and CSV exports are portable; published GIF exports use the analysis observation footprint and Months clock. Unsupported cloud ADES/MPC drafts and Hours GIF links are hidden.

Real NASA DR1 references were generated through `scripts/precompute/reference_products.py`. Reference spectrum points / difference frames: Barnard 9 / 18, Epsilon Indi B 100 / 22, WISE 0855 100 / 18, RW Aurigae 85 / 18, R Draconis 0 / 47. R Draconis has usable image coverage but no reliable reference aperture spectrum. Missing legacy search totals remain null. The existing analysis results were not re-labelled as fully flagged/calibrated measurements.

Cold-source false-alarm estimates remain uncalibrated. A detectable reference model is not a discovery or a Planet X exclusion. The UI states these limits explicitly.

Arbitrary-field background analysis is local-only by default. `SKYSHIFT_ENABLE_SCIENCE_JOBS=1` enables the single-process queue, reference generation and draft exports for a local server. It must remain off for the current multi-worker Cloud Run service until jobs have shared storage and a distributed queue. Existing main live observations continue to use the bounded canonical API.

## Verification

Locked Python environments and `npm ci` were used. Logs are under the ignored `.runtime/release` directory. The production TypeScript/Vite build succeeds; the existing bundle-size warning remains.

The full suites passed 1,313 tests: web 725, server 279, core 62, pipeline 195, scientific helpers 19 and validation utilities 33. Three optional network tests were skipped. Release tests specifically check that raw observations bypass legacy caches, all five scientific products work without native jobs, published image grids are not relabelled, exports remain portable, and hosted background jobs are disabled.

HTTP smoke checks passed for all five science/analysis APIs and model/residual/reference layers (25 groups), including every returned binary frame's size. Browser verification covered the five targets, scientific navigation, candidate checks, reference overlays, comparison, original object list and lessons, and a 390-pixel mobile viewport without horizontal overflow. Screenshots and UI checks are in `docs/previews/science-release`.

Live CatWISE and NEOWISE checks did not pass during this run: IRSA returned a proxy 502 and a 20-second timeout respectively. The API and UI report the failures rather than returning invented catalogue results. These services still need a successful cloud-side smoke check. NASA reference-image retrieval succeeded.

Docker is installed but its daemon was not running, so an actual Linux image build is still pending. Clean local dependency installation and tests do not substitute for that cloud build.

## Release status and next gate

No deployment or traffic change has been performed. The user completed Google Cloud login, but the active account was denied `run.services.get` on `nasa-glazing/skyshift-api`. Firebase CLI still reports no authorized account: the login helper stopped at the project-access check. A project-authorized deployment account or administrator-granted access is required. The current public site remains unchanged by this work; another team release occurred during verification and still had no Science workbench markers in its bundle.

Local production-like preview: `http://127.0.0.1:8092/?t=wise0855&clock=months&fov=5&det=6#/explore/science`. It has background jobs disabled and no native analysis cache. Local credentials and downloaded tools are ignored and excluded from Docker and Cloud Build uploads.

After a project-authorized Google account is available:

1. Capture the current Cloud Run revision/traffic and Firebase release/version for rollback. Recheck the production release has not changed during review.
2. Build the API image and deploy a tagged revision with no production traffic. Preserve service account, capacity and environment settings; keep `SKYSHIFT_ENABLE_SCIENCE_JOBS=0`.
3. Smoke-test the tagged API: health, canonical raw/object requests, all published science products, binary images, exports and external catalogue error handling. Validate a Firebase preview channel against that tagged API, not the old production API.
4. Only after these checks pass, promote the tested API revision and matching hosting build. Do not redeploy Firestore rules as part of this change.
5. Check the public science entry and an original sky URL. On failure, restore the captured API traffic and hosting version.

Deployment is now deferred at the user’s request. Project access, cloud build and preview verification remain outstanding for a future release.
