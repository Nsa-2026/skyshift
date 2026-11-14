# Scientific workbench release candidate

Prepared 14 November 2026 on `codex/science-production-integration`.
This is the current integration record; older handoff documents describe earlier local prototypes.

The user resumed publication on 14 November 2026, requesting the three workspace views and guided game be pushed. Code publication is authorized; production release still requires a project-authorized deployment account and the preview checks below.

## Source and scope

- Started from upstream `8522cd7`, incorporated `84b0f1c`, and subsequently merged `6ad0394` (including the measured image overlays, candidate vetting, moved-away target navigation, updated pipeline and MCP server). Merge resolutions retain both the canonical measured-layer cache version and the separate derived science view key, both server dependencies, and the card's navigation and scientific sections.
- Imported the user's scientific work from `9b1c775`, retaining its history. The original prototype and working copies were not changed.
- Preserved main's Lightbox UI, sky atlas, original blink observations, object measurements, review, live search, courses, votes and tools.
- Replaced the long Science disclosure with three task-based workspace views: Explore, Analyze and Research. They share one player and canonical measurements. The view switcher uses task names without audience labels or numbered ranks. The home page is the Sky globe (Explore → Sky map, a project decision at integration); Explore → Watch opens the player and every target link.
- Included motion and cold-source checks, D3/D6 comparison, DR1 reference spectrum, synthetic W1/W2 and experimental halo light curves, spectral audio, sensitivity estimates, catalogue checks, cultural modules and exports.
- The retained preview uses English throughout, including scientific messages, accessibility labels, historical sky charts, guest-star captions and planet events. English cultural displays use the existing English names and records; scientific data and the bilingual source records are preserved.
- Guided practice is now integrated at `#/explore/play` using the same player and actual prepared SPHEREx observations. It replaces the proposed schematic with real images; the prototype's separate game and API inspector are not imported.

The change spans core (legacy analysis adapter), server, web, precompute scripts and container packaging. Repeated wavelengths now use the weighted-mean fallback when the polynomial normal matrix is rank deficient; a regression test also checks that full-rank spectra retain their polynomial fit. The first remote Linux CI run exposed a pre-existing race in spectra loader statistics: shared counter updates occasionally omitted a photo. Workers now return their own counts for serial aggregation. A barrier-based regression reproduces the loss before the fix (10 instead of 40) and passes after it. No new formal contract fields are introduced by this integration.

## Task-based workspace views

The latest navigation revision makes Explore, Analyze and Research the global first
level. The second row contains only functions from the selected area: Explore has
Watch/Sky map/Practice/Learn/Community; Analyze has Measurements/Candidates/Review;
Research has Data & methods/Live search/Results/Tools & API. The separate Workspace
menu item and its nested view switcher are removed. Observation section tabs remain
inside Measurements and Data & methods. Canonical Analyze and Research links now
start at `#/analyze` and `#/research`; old nested and shared feature URLs still resolve
to the same page controllers, with filters, candidate IDs and sky coordinates retained.

| View | Route | Reading surface |
| --- | --- | --- |
| Explore | `#/explore` | Original observations, a short target/motion summary, quality warnings and a guided next step. Advanced player controls and dense charts stay out of this view. |
| Guided practice | `#/explore/play` | An Explore activity: Elpis, Barnard's Star and RW Aurigae, with observation choices, hints, image stepping/differences, explanatory feedback and a recap. |
| Analyze → Measurements | `#/analyze/<section>` | Motion, Spectrum, Light curve, Compare bands, Cross-match or Signals; one section at a time beside the same player. |
| Research → Data & methods | `#/research/<section>` | Data/provenance, Processing, Validation, Sensitivity or Export. Local-only computation remains gated by actual server capability. |

Switching views keeps the target, detector, clock, current frame, highlighted position and the last section used in each view. A derived layer retains its explicit caption and a Show observations action. Changing a target clears stale results. Data and methods are shared rather than refitted for each audience; simplified views retain uncertainty and quality disclosures.

The former `#/explore/science` link opens Research → Processing. Historical asterisms, guest-star images and planet events now live at `#/learn/stories`, linked from Learn. They load only when opened and return to the same workspace observation. Stories do not affect scientific scoring.

Browser checks exercised all sections, the real model layer, source metadata, a paused second exposure retained across all three views and Stories, and the 390px layout. Current screenshots are in `docs/previews/workspace`; earlier science-release screenshots document the previous UI.

Guided practice opens from the Explore heading. On mobile the task precedes the image, followed by controls, choices and feedback. Answers remain disabled until at least two real frame arrays have loaded for the expected target, detector, clock and field; selecting another field requires returning to the practice target. Progress is stored only in this browser and restored as an ordered list of valid answers. It never writes practice answers to Firestore. Completing the examples marks the shared review practice complete and offers real candidate review or Analyze. No audience ranks, forced unlocks, timed scores or discovery claims are added. Browser checks covered uncertainty feedback, image differences, all three examples, reload/resume, completion, transition into Analyze/Research and a 390px layout without horizontal overflow. Desktop and mobile captures are `practice-desktop.jpg` and `practice-mobile.jpg`.

## Data and deployment boundaries

Five fields have versioned portable scientific products: Barnard's Star, Epsilon Indi B, WISE 0855−0714, RW Aurigae and R Draconis. They live in `web/public/science-data`, separate from main's `web/public/data`. Their published models, residuals and reference differences support the 5 arcminute Months selection, with the actual smaller footprint masked outside its coverage. API matching is limited to two arcseconds around each published centre. Analysis findings are detector-specific; unsupported selections are disclosed.

The container copies these products and export inputs, so production does not need native NPZ jobs, pickle caches or the original `spherex-tm` directory. Raw observations never use the legacy analysis cube. JSON and CSV exports are portable; published GIF exports use the analysis observation footprint and Months clock. Unsupported cloud ADES/MPC drafts and Hours GIF links are hidden.

Real NASA DR1 references were generated through `scripts/precompute/reference_products.py`. Reference spectrum points / difference frames: Barnard 9 / 18, Epsilon Indi B 100 / 22, WISE 0855 100 / 18, RW Aurigae 85 / 18, R Draconis 0 / 47. R Draconis has usable image coverage but no reliable reference aperture spectrum. Missing legacy search totals remain null. The existing analysis results were not re-labelled as fully flagged/calibrated measurements.

Cold-source false-alarm estimates remain uncalibrated. A detectable reference model is not a discovery or a Planet X exclusion. The UI states these limits explicitly.

Arbitrary-field background analysis is local-only by default. `SKYSHIFT_ENABLE_SCIENCE_JOBS=1` enables the single-process queue, reference generation and draft exports for a local server. It must remain off for the current multi-worker Cloud Run service until jobs have shared storage and a distributed queue. Existing main live observations continue to use the bounded canonical API.

## Verification

The area-first navigation revision incorporated main through `ae150dc` and passed
1,654 tests: web 853, server 312, core 61, pipeline 292, MCP 77, scientific helpers 19,
and validation utilities 40; eight optional tests were skipped. TypeScript and the
production build passed. Browser checks verified all three area menus, unchanged
Barnard target/frame state across observation views, candidate filter/detail/return,
Tools under Research, and Practice under Explore. All three areas fit 320px without
horizontal overflow; the practice layout was also checked at 390px. Captures and the
check record are in `docs/previews/workspace/layer-first-*`.

The resumed release merged through `6ad0394` and passed 1,606 tests locally: web 835, server 312, core 61, pipeline 267, MCP 77, scientific helpers 19 and validation utilities 35. Eight optional tests were skipped. The production TypeScript/Vite build passed. New regressions cover a wrong-target/unfinished-image answer gate, uncertainty feedback, local progress/resume and completion, corrupt saved scores, and entry/exit through the shared workspace player. CI now runs the production web build as well as its tests.

Locked Python environments and `npm ci` were used. Logs are under the ignored `.runtime/release` directory. The production TypeScript/Vite build succeeds; the existing bundle-size warning remains.

The full suites passed 1,319 tests: web 731, server 279, core 62, pipeline 195, scientific helpers 19 and validation utilities 33. Three optional network tests were skipped. The workspace UI revision reran all 731 web tests and the production build. Integration checks verify that view/section changes and hash-history navigation retain the current observation, metadata and selected signal without reloading the player; Explore does not request extended science products. English chart text and historical/event descriptions remain covered. Release tests specifically check that raw observations bypass legacy caches, all five scientific products work without native jobs, published image grids are not relabelled, exports remain portable, and hosted background jobs are disabled.

HTTP smoke checks passed for all five science/analysis APIs and model/residual/reference layers (25 groups), including every returned binary frame's size. Browser verification covered the five targets, scientific navigation, candidate checks, reference overlays, comparison, original object list and lessons, and a 390-pixel mobile viewport without horizontal overflow. Screenshots and UI checks are in `docs/previews/science-release`.

Live CatWISE and NEOWISE checks did not pass during this run: IRSA returned a proxy 502 and a 20-second timeout respectively. The API and UI report the failures rather than returning invented catalogue results. These services still need a successful cloud-side smoke check. NASA reference-image retrieval succeeded.

Docker is installed but its daemon was not running, so an actual Linux image build is still pending. Clean local dependency installation and tests do not substitute for that cloud build.

## Release status and next gate

No deployment or traffic change has been performed. The user completed Google Cloud login, but the active account was denied `run.services.get` on `nasa-glazing/skyshift-api`. Firebase CLI still reports no authorized account: the login helper stopped at the project-access check. A project-authorized deployment account or administrator-granted access is required. The current public site remains unchanged by this work; another team release occurred during verification and still had no Science workbench markers in its bundle.

Local production-like preview: `http://127.0.0.1:8092/?t=epsindib&clock=months&fov=5&det=2#/explore`. It has background jobs disabled and no native analysis cache. Local credentials and downloaded tools are ignored and excluded from Docker and Cloud Build uploads.

After a project-authorized Google account is available:

1. Capture the current Cloud Run revision/traffic and Firebase release/version for rollback. Recheck the production release has not changed during review.
2. Build the API image and deploy a tagged revision with no production traffic. Preserve service account, capacity and environment settings; keep `SKYSHIFT_ENABLE_SCIENCE_JOBS=0`.
3. Smoke-test the tagged API: health, canonical raw/object requests, all published science products, binary images, exports and external catalogue error handling. Validate a Firebase preview channel against that tagged API, not the old production API.
4. Only after these checks pass, promote the tested API revision and matching hosting build. Do not redeploy Firestore rules as part of this change.
5. Check the public science entry and an original sky URL. On failure, restore the captured API traffic and hosting version.

Publication is requested again. Project access, cloud build and matching hosted preview verification remain outstanding; do not treat a source push as a verified production release. The current account still receives `run.services.get` denied and Firebase Hosting reports that login is required.
