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
