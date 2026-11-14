# SkyShift UI: scroll opening and observatory

The entry and Explore dashboard now follow the latest user-supplied light dashboard screenshot:
white/gray panels, dark text, rounded controls and red accents above real SPHEREx sky. The
original dark observation canvas and scientific controls remain inside the player. Other routes
retain the preceding dark observatory baseline. The integrated features remain in one application
on `codex/frontend-unified`.

## Current opening (14 November 2026)

`#/` is a scroll-controlled space → Earth → ground → goddess profile → pupil → real sky sequence.
The character is the user-supplied `src/opening/goddess.ts`, drawn with Canvas paths: black skin,
an Egyptian dress, headdress and jewellery, looking up in profile. `src/intro/portrait.ts` adapts
its centimetre coordinates to the existing camera; head and pupil anchors come from that model.
No other part of the separate opening prototype is loaded by this integration. There are no
video files or AI-generated visual assets.
The same live dashboard emerges at the end and becomes interactive at `#/explore`.

Entry/Explore-only styles live in `src/intro/intro.css`: persistent search/navigation, five target
cards, the existing player and real candidate signals. The light shell leaves the sky visible
between panels. On phones panels stack, target cards scroll horizontally and the original
Details controls remain accessible. Deep links, returning visitors and reduced-motion users
bypass the opening; “重看开场” is available on the dashboard.

See [scroll-intro.md](../docs/scroll-intro.md) for phase boundaries, provenance, tests and actual
browser captures. The older atlas specification below describes the atlas route and product
direction, not a new atlas implementation delivered by this opening change.

## Preserved observatory visual system

- Near-black sky (`#070809`), graphite floating panels (`#111214`), thin grey borders, white and grey text. Shared tokens are in `src/styles/tokens.css`.
- System sans-serif, tabular numbers, small uppercase inspector labels. Controls are quiet rectangular segments with 6 px corners and a visible selected state.
- The sky fills the viewport. A target card floats at the upper left, Details at the right, and playback along the bottom. The observation is the visual focus.
- The spectrum sits directly below its heading, followed by its colour controls and explanation. All charts keep measured values, units, saturation flags and accessible readouts.
- Colour remains meaningful in difference images and scientific marks. The interface itself is mostly neutral.

## Explore layout and integration

`src/app/observatory.ts` rehomes the existing live components; it does not clone players or object cards. `observatory.css` supplies the layout.

- **Target card:** actual name, coordinates, detector range and measured motion where available. In the entry dashboard its Workspace action focuses the visible header search; navigation is in the header.
- **View:** Normal / Difference / Stack, brightness, inversion, catalogue/moving-object/target/guides overlays, D1-D6 and the shared spectrum and measurement card.
- **Sampling & display:** Months / Hours, field size, Fill window / Fit full field, linear / asinh stretch, sky subtraction, difference smoothing, comparison reference, following, zoom and speed.
- **Analysis:** original observations, model, residual and DR1 difference in the same player; findings, calibration, paired bands, reference spectrum, light curves, sensitivity, catalogues, cultural sky and exports.
- **Sky & votes:** the original flagging, voting and contextual sky components. Picking the main image opens this pane.
- **Bottom transport:** target, play/pause, previous/next observation, real date timeline, playback speed, zoom/centre and Details toggle.

The canvas uses one uniform scale for both axes. Fill window crops the footprint without stretching the sky; Fit full field reveals its whole extent. Pixel-to-sky mapping, picks, marks and the scale bar use the same transform. Actual observations are retained: the screenshot is a style reference, not a source of stars, measurements or catalogue claims.

On phones the target card stays at the top, the transport uses two rows, and Details starts closed. Opening it shows an internally scrolling inspector between the card and transport. Tab navigation supports arrow keys; Escape closes an open panel and restores focus. Leaving the Analysis pane stops its audio; leaving Explore stops playback. Other pages share the dark tokens.

Keep explicit no-data and loading states, detector-specific time comparisons, legacy measurement provenance warnings, saturation and model limitations. Never substitute synthetic examples for real observations. Retain shape/text cues in addition to colour, visible keyboard focus, reduced-motion support and usable phone widths.

## Product: a community discovery tool (added 14 Nov, 15:30)
SkyShift exists so that many people can find new things in SPHEREx: asteroids and comets no one
has catalogued, nearby stars and brown dwarfs that move, sources with odd spectra. The pipeline
proposes candidates; people blink them, vote, and together decide what goes to astronomers.
Professional (numbers with units, honest limits, citations), and welcoming (plain words, a
practice round, nothing requires an account).

### Pages (hash routes; old links keep working)
| Route | Page | Owner folder |
|---|---|---|
| `#/` | **Opening**: scroll journey and live dashboard reveal; repeat/deep-linked/reduced-motion visits go to Explore | `src/intro` |
| `#/sky` and `#/sky/<state>` | **Sky**: existing atlas route; detailed atlas behavior below is the product specification | `src/atlas` |
| `#/candidates` | **Candidates**: smart filters, candidate list, small sky map (was "Discover" at `#/`) | `src/discover` |
| `#/review` | **Review queue**: one candidate at a time, big plate, vote with keys 1–4, skip, progress; practice round first time | `src/review` |
| `#/c/<id>` | **Candidate**: plate of that spot with the candidate marked, the check assistant, measurements, votes and consensus, report and share | `src/review` (+ `src/assist`) |
| `#/explore` | **Dashboard**: light search/target/candidate panels over real sky, existing observatory player with Details and transport | `src/intro` + `src/app` + blink, card, vote, sky |
| `#/live` | **Live search**: tonight's pipeline run along the ecliptic, zone by zone (map, counters, machines, time left), provisional results per zone | `src/live` |
| `#/community` | **Community**: what people have reviewed, consensus results, the list going to astronomers | `src/community` |
| `#/results` | **Results**: validation checks and what the pipeline found (`#/score` redirects here) | `src/score` |
| `#/learn` | **Learn**: short lessons with real SPHEREx examples, glossary, the practice round's teaching | `src/learn` |
| `#/tools` | **Tools and data**: downloads, batch coordinates, API, citation (for professionals) | `src/tools` |
| `#/about` | **About**: the challenge, method, data, limits, credits, FAQ | `src/community` |
Nav: Sky, Candidates, Review, Blink, Live search, Community, Results, Learn, Tools and data, About, Play. Explore and other routes retain visible header navigation. Play (`#/play`, `src/play`) contains the map hunt, tracking, collection, birthday stars and cultural stories.
Deep links `?target=<slug>`, `?t=<slug>` and `?ra=&dec=` open Explore.

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

## Sky atlas specification (added 14 Nov, 16:00; now a separate route)
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
2. **Is it already known?** Gaia / CatWISE match, nearest known asteroid or comet (MPC orbits with kete) and its distance.
3. **Could it be an artefact?** flags at its position (ghost, crosstalk, persistence, edge), in plain words.
4. **Is it the same object in every frame?** brightness steady within errors, same detector only.
5. **What does its colour say?** red / methane / water hints from the spectrum, when there is one.
6. **Your call:** vote mover / changing / junk / unsure.
Then **What next**: for consensus movers, a draft MPC observation report (ADES PSV, clearly marked
"draft, not submitted"); for moving stars, a Backyard Worlds forum post draft; for anything, a citation
and a permalink. Copy buttons, never auto-submit.
