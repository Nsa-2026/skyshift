# scripts/vet: check one finding like an astronomer

Tools for a person or an AI agent to vet a SkyShift finding (mover, fast star, spectral lead, shape) against the
actual photos, the catalogues and the known bodies, and the reverse check (known bodies we should have seen).
The checklist and the record format are in `.claude/skills/vet-finding/SKILL.md`.

Run everything from `pipeline/` (its environment has all that is needed):

    uv run python ../scripts/vet/prep_zone.py <zone> [--lite]     # once per zone: local data (read only on the bucket)
    uv run python ../scripts/vet/vet.py -h                        # info, packet, session, stamps, catalog, known,
                                                                  # spectrum, dots, movers, pixel, track
    python3 ../scripts/vet/round_report.py <round dir>            # collect the agents' records, score the controls
    uv run python ../scripts/vet/make_round.py <round dir>        # pick a round of findings, write agent batches
    uv run python ../scripts/vet/known_check.py <zone> --out DIR  # known bodies: seen or missed, and why
    uv run pytest ../scripts/vet/tests

- Local data: env `SKYSHIFT_VET_DATA`, default `~/.cache/skyshift-vet/data` (`<zone>/*.parquet`, `candidates.json`).
  `--lite` skips the movers and the known-body predictions (4–6 minutes per zone); enough for fast stars,
  spectral leads and shapes.
- Images are never stored: every stamp is cut from the photo on NASA's public S3 copy (about 1 s each), so any
  position of any prepared zone can be looked at. All panels share one grid: 41 × 41 pixels of 6.15″, north up.
- The numeric rule (`vetlib.numbers_verdict`): aperture S/N at the mover's marks in its detections, in the
  detection minus the same spot at other times, and at other times. A source at other times with a weak
  difference is a fixed star. On 60 labelled movers it kept 30 of 30 real ones and dropped 23 of 30 stars.
- Nothing here writes to the bucket, the site or the pipeline's results.
- Round 1 (16 Nov 2026, 11 Opus agents, 100 findings + 12 controls): controls 12/12; no real unknown mover among 64
  candidates; 8 fast stars confirmed against Gaia's 2016 positions. Its feedback is built in: automatic warnings and the
  whole-session table in `packet`, AllWISE and the back-track in `catalog`, `pixel`, `track`, band ratios in `spectrum`,
  and tool failures printed on standard output so they are never read as "nothing found".
