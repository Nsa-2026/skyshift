# To do

Open items, grouped by owner (CLAUDE.md, "Areas and owners"). Tick or delete an item in the PR that fixes it.
Updated 14 Nov, 18:10. Done since the first list: the exporter's `snr` 0 (#173), merge_suspects' zone fields (#174),
the distant-planet search on the results page (#170, #177), WISE 0855 visit 3 (#164), grades on every page (#161),
tracklets on main with one free-dot table, Cloud Run serving the measured layer (revision 00010), the flaky
test_calib psf test (a thread pool's shared `+=` counter lost a photo: spectra 82959cc, motion_io and orphans after).

## Needs a VM run (not before the 10:00 deadline: every CPU goes to the sky run)

- [ ] **Re-export `candidates.json` with the vetting grades.** 0 of the 1,333 published rows have `grade` yet.
  The hunt's movers lack the dot lists and `freedots.parquet`, so vetting would mark them all `not_vetted`.
  - Run `skyshift-pipeline all --zone hunt` (tiled) on main. That reruns link, tracklets, qa, spectra, calib (~15 min),
    highpm, score and suspects. Then run `skyshift-candidates`, `frames.py` and build_validation.
  - Reads about 9–10 GB: the hunt's 3,692 photos' dot files and forced/, plus results tables and refcat.
    Run it in us-east4 next to the bucket (no egress). skyshift-worker on 16 workers took about 37 min last time;
    skyshift-test takes about 1–1.5 h.
- [ ] **Measured layer for the featured targets.** It stays empty until their `t_<slug>` zones are processed
  (`results/t_wise0855/` holds only a summary.json). Then run `precompute.py --measured-only`.

## P1: pipeline/

- [ ] `det` has no null in the contract, so a source with no usable spectrum is exported with the fallback D2
  (`DEFAULT_DET`, #173). If that misleads, make `det` nullable: a contract PR plus web `Candidate.det`.

## P2: server/

- [ ] Redeploy Cloud Run with #164. Until then the live API keeps the 5″ blend rule, and WISE 0855's visit 3 is back
  only in the static files.
- [ ] **P1/P2: class a blend of Gaia stars as `star`, not `other`.** The 4″ crossid misses a source whose centroid
  falls between two Gaia stars, so it reads `other` ("Source Gaia doesn't list") while its own `blend_gaia_ids` name
  them (RW Aurigae months, visit 1: a G 17.8 pair 9″ apart). The blink now draws such a source as those Gaia stars
  (display only, `web/src/blink/layout.ts` `sourceGaia`: the server's blend list, or a G ≤ 18 star within 3.75″).
  The contract's class rule (contracts/api.md, `measured`) and the exporter should do the same, so the counts,
  the list and the MCP server agree with the plate.

## Branches without a PR to main

- [ ] `codex/frontend-unified`: about 215 commits behind main, and it changes `server/`, outside a web area.
  Its owner should merge main into it before anything else.
- [ ] `alexandra` (colour layers): #158 merges main into it, with its one conflict in `web/src/app/app.ts` resolved.
  It's waiting for the branch owner.
