# SkyShift UI: "Lightbox"

The design every area follows. Tokens live in `src/styles/tokens.css`; shared controls in `src/styles/base.css`.

## Idea
Pluto was found by blinking two photographic plates on a lightbox. SkyShift is that lightbox for
NASA's SPHEREx sky. The page is cool glass-white. Frames are shown as **plate negatives** by
default (dark stars on light glass), which is how astronomers hunt faint movers; "Positive"
shows the usual bright-on-dark view. Marks on the plate are **grease pencil**: red for the target,
flagged dots and our measurement, blue for stars Gaia already knows, violet for predicted
asteroids and comets. The six detector colours (`--d1`..`--d6`) mean wavelength and nothing else.

## One bold thing
The plate: as large as the viewport allows, with its time strip directly under it. Everything
else is quiet ink on glass, separated by space rather than boxes.

## Rules
- **Hierarchy over chrome.** No box around every section. Use space (`--s6`, `--s7`) and, rarely,
  a single `--hair` rule. `.panel` (sheet) only where content must sit on a surface (the plate's
  mat, a chart, a table).
- **Controls.** Primary choices use `.seg` (one capsule, options inside, `aria-pressed`).
  Actions use `.btn` (quiet), `.btn-primary` (one per view at most) or `.btn-ghost`.
  Secondary settings live behind a disclosure ("Adjust"), closed by default.
- **Type.** Archivo only. Width axis carries the voice: `wdth 125` wordmark, `wdth 100` body,
  `wdth 85–92` for dense numbers and labels. Sentence case. No all-caps labels, no eyebrow labels,
  no monospace, no "A · B · C" meta strings: write a short sentence or use a definition list.
- **Numbers.** Tabular figures. Units always: ″, ″/yr, µm, µJy, MJy/sr, pc. The measured shift is
  the largest number on the page (`--step-3`, `wdth 85`, `--pencil-red`).
- **Plate drawing.** Negative by default: high values → `--ink`, sky → `--glass`. No-data pixels
  (raw 0) are drawn as a flat `--sheet-2` with a fine diagonal hatch, never a checkerboard.
  Saturated cores in negative read as solid ink (a burnt plate), which is honest and looks right.
  Difference mode: `--pencil-blue` for light that left, `--pencil-red` for light that arrived.
- **Pencil marks.** 1.5 px strokes, slightly translucent (0.85), round caps. Gaia rings thin and
  blue; the target a red open reticle with a gap; known objects violet diamonds; a flagged dot a
  red circle with a short tick.
- **Copy.** Plain, specific, active. Say what to do next in errors. One sentence explains each
  mode the first time it matters ("Each visit minus the first. Red: light arrived. Blue: light left.").
- **Accessibility floor.** Visible focus (`--focus`), keyboard paths for everything, colour never
  the only cue, `prefers-reduced-motion` respected, 375 px phone width works without page scroll.
- **Motion.** One moment only: the blink itself. No entrance animations or hover flourishes.

## Layout
```
SkyShift                 [ Paste coordinates …                ] [Blink]     Blink  Scoreboard
Blink NASA's SPHEREx…
Try  Barnard's Star  Eps Indi B  WISE 0855  RW Aur  R Dra  3I/ATLAS  Elpis  Euphrosyne
┌──────────────────────── plate (square) ───────────┐   Barnard's Star
│ 18 Mar 2026 · visit 2 of 3 · D2 1.1–1.6 µm   (label printed on the plate edge, small)
│                                                    │   10.83″ ± 0.36″
│              negative frame + pencil marks         │   moved between visits 1 and 3
│                                          N         │   Gaia predicts 10.27″: they agree.
│ 1′ ─────                               E┘          │
└────────────────────────────────────────────────────┘   Distance  1.83 pc …
 ▶ ┃━━●━━━━━━━━━━━━━━━━━━●━━━━━━━━━━━━━━━●━━  time strip   Spectrum (chart)
 (Months|Hours) (D1 D2 D3 D4 D5 D6 ramp) (Blink|Difference|Stack)  Adjust ▾   What to look for
──────────────────────────────────────────────────────────────────────────────────────────────
Flag what moved (votes)                      Sky around this spot (Aladin, opens on request)
```
The plate's square edge is `min(available column width, 100vh − 220px)` so the time strip is
visible without scrolling at 1440×900 and 1280×720.
Phone: find → names (scroll sideways) → plate full width → strip and primary controls → card →
votes → sky (collapsed).

## Product: a community discovery tool (added 14 Nov, 15:30)
SkyShift exists so that many people can find new things in SPHEREx: asteroids and comets no one
has catalogued, nearby stars and brown dwarfs that move, sources with odd spectra. The pipeline
proposes candidates; people blink them, vote, and together decide what goes to astronomers.
Professional (numbers with units, honest limits, citations), and welcoming (plain words, a
practice round, nothing requires an account).

### Pages (hash routes; old links keep working)
| Route | Page | Owner folder |
|---|---|---|
| `#/` | **Discover**: intro band (one sentence, live counts, Start reviewing), smart filters, candidate list, sky map | `src/discover` |
| `#/review` | **Review queue**: one candidate at a time, big plate, vote with keys 1–4, skip, progress; practice round first time | `src/review` |
| `#/c/<id>` | **Candidate**: plate of that spot with the candidate marked, why it's listed, cautions, measurements, votes and consensus, share/embed, next steps | `src/review` |
| `#/explore` | **Explore**: blink any coordinates or a featured target (the original blink page) | `src/app` + blink, card, vote, sky |
| `#/results` | **Results**: validation checks and what the pipeline found (`#/score` redirects here) | `src/score` |
| `#/community` | **Community**: what people have reviewed, consensus results, the list going to astronomers, how it works | `src/community` |
| `#/about` | **About**: the challenge, method, data, limits, credits, FAQ | `src/community` |
Deep links `?t=<slug>` and `?ra=&dec=` open Explore.

### Candidates (data: `/data/candidates.json`, type `CandidatesResponse` in `src/api/types.ts`)
Three kinds, each with its own colour-free icon and plain name:
- **mover** "Fast mover": moves within hours; could be an uncatalogued asteroid or comet. Clock: Hours.
- **high_pm** "Moving star": moves between visits; could be a nearby star or brown dwarf. Clock: Months.
- **spectral** "Odd spectrum": molecule bands or colours worth a look (methane, water ice, CO₂).
Each has `why` (one sentence), `reasons` (codes → plain-language chips), `score` (pipeline promise),
`flags` (artefact cautions) and `frames_url` (static blink) or null (live API).

### Smart filters (Discover)
- **Presets** first, as a short row of choices: "Most promising", "Needs your vote" (fewest votes),
  "Possible brown dwarfs" (high_pm, red or methane, no Gaia match), "Unknown fast movers" (mover,
  no known object within 60″), "Community says real" (consensus mover/changing).
- **Facets**: kind; zone; detector; labels (molecules, red); evidence; caution flags (hide artefacts);
  catalogue match (none / Gaia / CatWISE); review state (unreviewed / in review / consensus).
- **Ranges**: score, S/N, brightness (µJy), motion (″/h or mas/yr), motion significance (pm/err),
  distance to nearest known object, galactic latitude |b| (hide the crowded plane).
- **Sort**: most promising, fewest votes, most votes, brightest, fastest, newest.
- Every filter lives in the URL (shareable, embeddable). Counts update live ("38 of 194").
- The sky map (Aitoff, galactic or equatorial) shows the same filtered set; hover links to the list.

### Votes and consensus (Firestore `votes`, candidate_id = the candidate's id)
Answers: mover / changing / junk / unsure. A candidate reaches **consensus** with ≥ 5 votes and
≥ 70 % agreeing on one answer. Consensus movers and changing sources form the **list for
astronomers** (Community page, CSV and JSON export, with the Backyard Worlds forum and the MPC as
next steps). No accounts: a random voter id in the browser; never names or emails.
Pages import vote logic from `src/vote` (store, model) and never edit it.
