# Engine flaws found by vetting 100 findings (16 Nov 2026)

What the pipeline got wrong on the 100 findings of vetting round 1, merged from about 1,100 notes by four
checkers (Opus agents, Opus, Sonnet and Haiku through the API). Counts are findings of the Opus API run;
a finding can show several flaws. Nothing here is fixed yet: this is the list for the pipeline owner (P1).
The findings themselves are in [2026-11-16-vet-round1-findings.md](2026-11-16-vet-round1-findings.md).

## The result in one table

| Kind | Checked | Real | Wrong | Open |
|---|---:|---:|---:|---:|
| Movers | 64 | 0 | 63 (42 instrument artifacts, 21 fixed stars) | 1 |
| Fast stars | 16 | 11 (9 with a Gaia position but no Gaia motion, 2 known brown dwarfs) | 5 | 0 |
| Spectral leads | 15 | 7 | 8 (AGN or galaxies called brown-dwarf-like) | 0 |
| Shapes | 5 | 3 (the second object is real, and AllWISE lists it) | 2 | 0 |

Four cuts would have removed all 63 false movers: do not publish grade C (33), drop grade B rows whose own
reasons say `motion_not_significant` (23), never publish on `two_detectors` evidence alone (3), and a gate
for broad one-detector glows and repeated positions (5). The blind test (known asteroids with the
known-body list hidden) says how many real movers such cuts would cost; see the findings file.

## Flaws that create false findings, most damaging first

| # | Flaw | Kind | Findings | Where | Simplest fix | What the fix could lose |
|---|---|---|---:|---|---|---|
| 1 | Bad-pixel gates (`hot_pixel`, `bad_column`, `very_sharp`, `sharp_outlier`) only lower the grade to C; the row is still published. Typical case: two hot pixels one telescope step apart (115 or 230 px in y) land on one sky spot in consecutive exposures. One D4 pixel of ecl05 has detections in 251 of 887 exposures. | mover | 29 | suspects gates, candidates exporter | Do not publish grade C; better, build a hot-pixel map (pixels with detections in 3 or more exposures) and drop those dots before linking | A real asteroid crossing a hot pixel, for that exposure only |
| 2 | Motion that is not significant is published at grade B (`MOVER_STILL_SIGMA = None`) | mover | 31 | `link.vet_reason`, `config.py` | A 2–3σ cut only together with the fixed-source test of flaw 3 | Alone, about two thirds of real two-exposure asteroids (they move less than 3σ in 4 minutes) |
| 3 | A fixed source at the spot is not recognised. The centroid of a blend lands 6.2–9.4″ from our own fixed source, outside `FIXED_VETO_ARCSEC = 6`; signal of +5 to +70σ at the same spot in other sessions is never looked at. Movers sitting on a Gaia star pass too. | mover | 26 | link, suspects | Forced flux at the track in 2 or more other sessions above half the detection flux means fixed; veto a Gaia star (moved to the date) within 2″ when the motion is under 3σ | An asteroid passing over a faint star, if a plain larger radius is used instead |
| 4 | Coverage is undercounted: `cover_exposures` 0–2 and `missed_exposures` 0 while 4–8 exposures hold the track. The exposures near a fixed source or a blend are excluded from the count, so the "missed" test never fires. | mover | 21 | `MOVER_COVER_FIXED_SLACK_ARCSEC`, `MOVER_COVER_BLEND_ARCSEC` | Measure forced flux at the predicted track in the excluded exposures | The exclusions were added because 239 of 1,652 known asteroids were dropped where tracks merged into stars |
| 5 | Whole-pixel centroid fallbacks feed the rate | mover | 13 | dots → link | Flag those dots, give them an error of 0.3 px or more, or null the rate | Nothing |
| 6 | Broad glow in one detector (D5 or D6, 5.9–7.3″ wide) near a bright star (G 2.9–6.3 within 7.5′), grade B, no gate | mover | 11 | suspects gates | Reject when every dot is wider than about 1.3× a star and the paired detector is empty; grade C with a G < 7 star within 10′ | Comets |
| 7 | Sessions without a telescope step (ecl17 session 201): every hot pixel stays on one sky spot | mover | 9 | link | Reject pairs whose detector position moved less than 1 px | Only objects with no measurable motion in that pair |
| 8 | Satellite trails pass as "seen in two detectors at one moment" | mover | 4 | link | `two_detectors` evidence alone never publishes; add a streak test | Fast movers caught in one exposure |
| 9 | `repeat_position` and same-column detections are not vetoes (a D6 column glow in 3 sessions) | mover | 4 | suspects gates | Grade C | A slow real object seen a day later |
| 10 | Links across detectors in different exposures (D1 then D4): colour is read as motion | mover | 3 | link | Two-exposure pairs must use one detector | Nothing |
| 11 | "No catalogue lists this motion" rests on Gaia proper motions only. 9 real fast stars have a Gaia row with a position and no motion, 3–5″ back along the track; two brown dwarfs have AllWISE counterparts on the track. | fast star | 11 (claim wrong, object real) | `HIGHPM_DEEP_MATCH_ARCSEC`, cross-match | Back-track with our motion to Gaia 2016.0 and AllWISE 2010.5; say "Gaia has a position, no motion" | Nothing |
| 12 | The pair method joins two unrelated fixed sources 39–58″ apart into a 73–115″/yr "star". `implausible_pm` only lowers the score. | fast star | 4 | high_pm pair method, `IMPLAUSIBLE_PM_MAS_YR` | Drop rows tagged `implausible_pm`; require each end to be absent in the other visit | Nothing |
| 13 | A red AGN is read as a methane brown dwarf: the 3.3 µm "dip" is the low point between starlight and hot dust; there are no water bands | spectral | 8 | `classify.py` (`brown_dwarf_like`) | Require a water band; veto W2−W3 above about 2.5 | Y dwarfs with poor J data |
| 14 | Catalogued objects shown as leads: galaxies with an AllWISE extended flag, L dwarfs whose catalogue match is 6–16″ off because the catalogue position is not moved to our date | spectral | 6 | cross-match | Move catalogue positions by their proper motion; drop `no_gaia` as a reason for galaxies | Nothing |
| 15 | Shape: "the catalogues list one object" never reads AllWISE | shape | 4 | `shape.py` | Add AllWISE to the neighbour check | Nothing |

## Wrong numbers that create no false finding

- Rates: the site row and the mover table disagree (28.8 against 11.1″/h on `ecl10-692050057552-87780103`);
  a `two_detectors` row shows 4,795,728″/h (time difference near zero: it should be empty).
- Fast stars: parallax leaks into two-visit motions (0.69 against 0.37″/yr on `ecl11-pm-66005006577800`);
  `gaia_match` is true with no Gaia source within 30″ (`ecl34-pm-48750020658200-48749020657300`); the
  pipeline score is 0.03–0.06 where the site shows 0.76–0.84.
- Shapes: `companion_moves` is false while `companion_move_sigma` is 6–10; one companion direction looks
  flipped by 180° (`hunt-sh-64104007282900`: 310° against AllWISE's 134°).
- `known_object_near` is set for an asteroid 100″ away with a different rate (`hunt-675100072098-87810126`).

## Complaints that are not engine flaws

- `evidence: two_exposures` on a track with four detections: the name means "two or more exposures".
- "`hot_pixel` fired but no flag at the peak": the gate counts repeated detections on a pixel, it is not a
  FLAGS bit.
- The engine's methane index reports 8–17σ where the checkers measure a ratio of 0.85–1.03: the two use
  different continua. Check the index definition before trusting either.

## From the reverse check (known bodies we should have seen)

Well-placed known asteroids are found 80–99% of the time for V 10–16 and 24–60% for V 16–18. Rates from two
exposures are wrong by more than half in about 1 of 5. Saturated bright asteroids (Nysa, Massalia, Ceres)
are detected in every exposure and never linked.
