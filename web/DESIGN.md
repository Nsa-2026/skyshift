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
| `#/` (and `#/sky/<state>`) | **Sky** (the home page): the whole SPHEREx sky as a zoomable globe, candidates as a heat map that resolves into single signals as you zoom, click one to preview its blink | `src/atlas` |
| `#/candidates` | **Candidates**: smart filters, candidate list, small sky map (was "Discover" at `#/`) | `src/discover` |
| `#/review` | **Review queue**: one candidate at a time, big plate, vote with keys 1–4, skip, progress; practice round first time | `src/review` |
| `#/c/<id>` | **Candidate**: plate of that spot with the candidate marked, the check assistant, measurements, votes and consensus, report and share | `src/review` (+ `src/assist`) |
| `#/explore` | **Blink**: blink any coordinates or a featured target (the original blink page) | `src/app` + blink, card, vote, sky |
| `#/community` | **Community**: what people have reviewed, consensus results, the list going to astronomers | `src/community` |
| `#/results` | **Results**: validation checks and what the pipeline found (`#/score` redirects here) | `src/score` |
| `#/learn` | **Learn**: short lessons with real SPHEREx examples, glossary, the practice round's teaching | `src/learn` |
| `#/tools` | **Tools and data**: downloads, batch coordinates, API, citation (for professionals) | `src/tools` |
| `#/about` | **About**: the challenge, method, data, limits, credits, FAQ | `src/community` |
Nav: Sky, Candidates, Review, Blink, Community, Results, Learn. Footer: About, Learn, Tools and data, Results.
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

## Sky: the home page (added 14 Nov, 16:00)
The first thing anyone sees is the sky itself, not a list. Aladin Lite 3.8.2 fills the page under the
header (min(100vh − header, 900 px) tall on desktop, 70vh on phone) on a `--sheet` mat. IRSA HiPS only.

**Semantic zoom: one gesture from the whole sky to a single blinking dot.**
| Field of view | What you see |
|---|---|
| > 60° (whole sky) | Globe. SPHEREx coverage as the base; candidates as a **heat map** of HEALPix cells (order 3, ~7°) filled in a single-hue ramp of `--pencil-red` by count; the searched zones outlined in ink with their names; a one-line count "412 candidates in 2 zones" |
| 8°–60° | Finer heat cells (order 5, ~1.8°); cells with ≥ 1 candidate show their count; featured targets labelled |
| 0.5°–8° | **Single signals**: each candidate a pencil mark by kind (fast mover: red open circle with a short tick pointing along its motion; moving star: red ring with a motion arrow scaled ×100 per yr; odd spectrum: violet diamond). Known Gaia stars off. Hover shows a one-line tooltip |
| < 0.5° | The same marks plus each mover's track (dots at each epoch) and Gaia rings on request |
Click a signal → a **preview drawer** (right on desktop, bottom sheet on phone): name/kind, `why`, the key
number, a **live mini blink** (the candidate's static frames alternating at 2 Hz, or a "Load the blink"
button when it would call /api), votes so far, and three actions: **Review it** (`#/c/<id>`),
**Blink here** (Explore at that spot), **Next nearby**.
Click empty sky → a small pin with "Blink here" (Explore) and the coordinates (copy).

**Controls (quiet row over the map, `.seg`):** Globe | Whole sky (SIN vs Mollweide projection) ·
Equatorial | Galactic · Layers ("Adjust": SPHEREx, 2MASS, AllWISE backgrounds; heat map on/off; zones;
Gaia rings) · kind chips (Fast movers, Moving stars, Odd spectra) · search box (same parser as Blink).
State lives in the hash: `#/sky/<ra>,<dec>,<fov>[,gal][,kinds]` so every view can be shared.
**Fallback without WebGL2 or tiles:** a static Aitoff SVG of the same heat map and signals (no background
image), with the same drawer. Never a blank page.
**First visit:** a three-step hint over the map (Zoom into a red patch → Click a signal → Review it),
dismissible, remembered in localStorage.

## Two audiences, one tool
- **Curious public and hobbyists:** plain words first, Learn lessons, glossary terms underlined with a
  dotted line (hover or tap for one sentence), the practice round, "What to look for" on every target.
- **Professionals (teachers, astronomers):** a **Details** switch (header, remembered) that shows the
  dense numbers: MJDs, rates with errors, flag bit names, pipeline scores, raw IDs. Tools and data page:
  CSV / JSON / VOTable downloads of any filtered list, batch coordinates (paste up to 200, get a table with
  blink links), API examples (curl, Python astroquery-style), citation (BibTeX) and acknowledgements.

## The check assistant (`src/assist`, used on `#/c/<id>`)
A short checklist that walks anyone through vetting one candidate, each step answered by the data where
it can be and by the person where it can't:
1. **Does it move in a straight line at a steady rate?** (from the measured positions; pass/caution)
2. **Is it already known?** Gaia / CatWISE match, nearest known asteroid (MPC/SkyBoT) and its distance.
3. **Could it be an artefact?** flags at its position (ghost, crosstalk, persistence, edge), in plain words.
4. **Is it the same object in every frame?** brightness steady within errors, same detector only.
5. **What does its colour say?** red / methane / water hints from the spectrum, when there is one.
6. **Your call:** vote mover / changing / junk / unsure.
Then **What next**: for consensus movers, a draft MPC observation report (ADES PSV, clearly marked
"draft, not submitted"); for moving stars, a Backyard Worlds forum post draft; for anything, a citation
and a permalink. Copy buttons, never auto-submit.
