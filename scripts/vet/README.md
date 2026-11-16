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
    uv run --with anthropic python ../scripts/vet/api_round.py <round dir> --model claude-haiku-5-5 --effort high
                                                                  # the same round through the Claude API (key in the
                                                                  # environment): skill as system prompt, tools as tool calls
    python3 ../scripts/vet/compare_runs.py <round dir> [--main RUN] [--doc OUT.md]   # runs against each other and the
                                                                  # controls; --doc writes the findings file
    uv run python ../scripts/vet/make_scenarios.py <round dir> [--junk-from <round>]   # a blind round: real known bodies of hard
                                                                  # kinds + certain junk; run it with api_round.py --hide-known
    python3 ../scripts/vet/triage.py <round dir> --runs RUN ...   # piles: likely real / open / likely junk (nothing deleted)
    uv run python ../scripts/vet/calibrate_warnings.py            # how often each packet warning fires on real movers
    uv run --with scikit-learn python ../scripts/vet/rb_score.py  # a learned real-or-bogus score from the tables (prototype)
    uv run python ../scripts/vet/route.py <round dir> --scores scores.parquet   # easy or hard, before any model runs;
                                                                  # then api_round.py --routes <round>/routes.json --hard-effort high
    uv run --with anthropic pytest ../scripts/vet/tests

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
- Round 2 (16 Nov 2026, the same 112 findings through the API, one finding per conversation): Opus $9.27, Sonnet $2.93,
  controls 12/12 for Opus, Sonnet and Haiku. After the tool and skill changes Sonnet lands on the same side as Opus for
  105-108 of 112 (97 before). Two identical Sonnet runs agree on 105 of 112: that is the chance level. Ten findings per
  conversation changes nothing beyond chance and costs 12-20% more: keep one per conversation. Haiku at high effort makes
  four times the tool calls and is no better: dropped.
- Blind round (62 movers, the known-body lines hidden, `--hide-known`): 44 real known bodies of hard kinds + 18 certain
  junk. Sonnet first dropped 9 of 44 real ones, mostly on what the tools showed: a 9" aperture that counts a neighbour's
  light, a hot-pixel test that fired on real bodies, warnings worded from junk only. Now a mover is dropped only on a
  measured proof named in `junk_proof` (skill section 4; the runner refuses a drop without one): Sonnet medium drops 2,
  leaves 10 open and removes 17 of 18 junk; Sonnet high on the wrongly dropped ones drops none.
- The policy that follows: nothing is deleted. `triage.py` sorts movers into likely real / open / likely junk; a mover
  reaches "likely junk" only when every checker drops it with a named proof and confidence 0.8 or more.
- Tools run on this machine: `api_round.py` keeps `--tool-workers` copies of vet.py loaded (toolserver.py) and caches
  finished tool calls under `~/.cache/skyshift-vet/toolcache`, keyed by the tools' content and the blind switch.
- Engine flaws found: docs/research/2026-11-16-engine-flaws-from-vetting.md. Every finding of round 1:
  docs/research/2026-11-16-vet-round1-findings.md.
- Routing (`route.py`, `api_round.py --routes`): the learned score and the image-free warnings sort movers into easy
  junk, easy real and hard before any model runs; easy ones go to Sonnet medium, hard ones to Sonnet high. On 119
  movers with a known answer Sonnet medium made no wrong drop in the easy groups, and both real movers it wrongly
  dropped were hard. Blind round: routed gives the same result as high for all (39 of 44 real kept, 5 open, none
  dropped, 18 of 18 junk removed). On the junk-heavy mix of the To check list (56 of 64 easy) routed costs about $0.041
  a mover against $0.076 for high everywhere. Sonnet high alone is the best single checker measured (better than Opus
  medium: 39/5/0 against 35/9/0).
