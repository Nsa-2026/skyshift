# To do

Open items found while reviewing PRs #127–#149 (14 Nov). Tick an item in the PR that fixes it, or delete it.
Grouped by owner (CLAUDE.md, "Areas and owners").

## P1: pipeline/

- [ ] **Candidates exporter writes `snr` 0 and `det` 1 when a source has no usable spectrum.**
  - 65 of the 1,333 published objects: all `high_pm`, hunt zone, scores 0–0.16. All 65 have `flux_ujy` null and `det` 1.
  - Cause: `source_stats` in `pipeline/src/skyshift_pipeline/candidates.py` sets every missing channel's S/N to 0.
    The summed S/N is then 0, `argmax` over six zeros picks D1, and the median flux of nothing is written as null.
  - On the site, Discover shows "S/N 0.0" (`web/src/discover/index.ts`, the check is `snr != null`).
    Touching the S/N slider hides all 65 without saying so: its log scale starts at 0.001.
  - Fix at the source, not in the web: when no channel has a positive S/N, write `snr`, `det` and `flux_ujy` as null
    (contracts/api.md already allows null). Add a test. The site changes only after the re-export below.
- [ ] **Re-export `candidates.json` with the vetting grades (#135).** 0 of the 1,333 published rows have `grade` yet.
  The hunt zone has to go through link, suspects and candidates again. It reads from the bucket: check the cost first.
  Also carries the `snr` fix above.
- [ ] **`p1/tracklets` before it merges:** it conflicts with main in `link.py` and `tiles.py`. #135 added
  `freedots.parquet` and the branch adds a near-identical `free_dots.parquet`; keep one.
- [ ] `build_validation.merge_suspects` drops the zone-level `vetoes` and `provisional` (#135, "Not done").
- [ ] The orphans results (`distant.json`, #132) are not on the site. That needs a contracts/api.md entry first.

## P2: server/, scripts/precompute/

- [ ] Redeploy Cloud Run with #142 (measured layer). Nobody has checked whether it is deployed.
- [ ] The measured layer is empty for the featured targets until their `t_<slug>` results exist. Then run
  `precompute.py --measured-only`.
- [ ] WISE 0855 visit 3 is missing from the spectrum and the brightness chart: a Gaia star is 3.9–4.3″ away then (#143).
  Lowering `BLEND_MIN_ARCSEC` to about 3.5″ for built-in targets Gaia doesn't see, then
  `precompute.py --only wise0855 --sets none`, would bring it back. P2's call.

## P3: web/

- [ ] Show the suspects' grades in Discover. `Candidate` in `web/src/api/types.ts` has no `grade` field yet
  (`candidates.json` gains it at the re-export above).

## Branches without a PR

- [ ] `codex/frontend-unified`: about 128 commits behind main, 10 conflicts, including `server/.../app.py` and
  `blink.py`. It also writes into `server/`, outside a web area.
- [ ] `alexandra` (colour layers): one conflict, in `web/src/app/app.ts`.
