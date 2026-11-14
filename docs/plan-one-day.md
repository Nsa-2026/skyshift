# SkyShift in one day (Sat 14 Nov)

The plan v2 schedule spread the build over Saturday and a night shift, with the freeze on Sunday at 09:00.
This version **builds everything on Saturday and freezes at 20:00**. Sunday morning is only for the
video, slides, link checks and the submission. No night shift.
Scope, contracts and owners are unchanged; only the timing and the cuts change.

## Cuts
- **S2–S6 are cut**: fast Gaia stars, brown-dwarf list, hidden-neighbour drill, false-colour still, overlays.
  Pick one up only if a person is idle after 18:00.
- **S1 (hunt zone) stays only as machine time.** The pilot runs at 13:00 and the gate moves from
  18:00 to 14:30. If it passes, the 64-core worker runs alone for under an hour.
- The integration hour moves to 17:00. Numbers are frozen at 19:30.

## Saturday

| Time | P1 · lead, science, pipeline | P2 · server, deploys | P3 · sky, cards | P4 · blink, votes, pitch |
|---|---|---|---|---|
| 10:30–13:00 | Cloud set-up (bucket, machines, accounts). Pipeline code. Validation data. V2 and V3 numbers. | Barnard's Star blink from real data with `core/`: cutouts, visit rule, median stack; static files. | App shell, coordinate box, chips, Aladin with IRSA layers. | Blink player on fixtures: speed, stretch, invert, subtract, add. |
| **13:00 · M1** | *Barnard's Star blinks from real data on the public site.* Pilot (12 deg²) starts on `skyshift-test`. | Six detectors; WISE 0855, Epsilon Indi B. | Measure readout, Gaia distance. | Months/Hours switch, date legend, Gaia rings. |
| 14:00 | V4: known asteroids re-found (MOST), with a control. | RW Aurigae, R Draconis. Live `/api/frames`. | Spectrum chart, molecule marks. | Load the static frames; field-of-view picker. |
| **14:30 · G2** | *Gate: pilot recall at V < 16 is 50% or more.* Yes: hunt zone on `skyshift-worker` (runs alone). No: stop S1. | | | |
| **15:00 · G1** | *Gate: five targets blink; one motion measured.* | `/api/object`; spectra for the five. | Spectrum wired; "what to look for" text. | Hours clock on real frames. |
| 15:00–17:00 | Hunt-zone results → `suspects.json`. `validation.json` v1. | 3I/ATLAS movie; two asteroid movies from V4; deploy; cache. | Scoreboard page reads `validation.json`. | Flag-a-dot, candidate list, votes to Firestore, counts. |
| **17:00 · M2** | *Any coordinates work live; all featured targets are static; suspects are listed.* All: integration hour, every target on the public site, bug list. Dinner in turns. | | | |
| 18:00–19:30 | Bug fixes. README credits and AI section. Page text: data, validation, limits. | Bug fixes; static export of everything; deploy. | Phone width; copy pass. | CSV export; walk the storyboard on the real site. |
| **19:30** | *Numbers frozen.* | | | |
| **20:00 · freeze** | *Feature freeze.* Only fixes from here. | Last deploy 21:30. | Seven slides. | Record video takes. |
| 20:00–22:00 | Project page with working links. | Private-window link tests; offline copy on the pitch laptop. | Screen set-up and captions. | Cut to 29.0 s; check the length with a tool. |
| 21:30 | All, 15 min: pitch dry run. | | | |
| **22:00 · M4** | *First full submission saved.* Go home. | | | |

## Sunday
- 09:00 stand-up; everyone opens the submission in a private window. Fix only what's broken.
- 11:00 pitch rehearsal 1 · 13:00 pitch rehearsal 2, all four present.
- **14:30 final submission saved** (hard stop 14:45) · 15:00 local deadline · 15:30 live pitch.

If the submission itself must also be in on Saturday, the 22:00 save is the final one: do the
upload and the private-window check at 21:30 instead of the dry run.
