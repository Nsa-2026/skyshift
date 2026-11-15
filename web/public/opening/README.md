# Opening images

The opening ("The sky is never still", `web/src/intro/`) reads its images from
`manifest.json` in this folder. To swap an image, no code change:

1. Put the new file in this folder under a **new name** (e.g. `spherex-sky-2.webp`), so no
   browser keeps the old one from its cache.
2. Point the slot's `file` at it and update `alt`, `credit` and, if it changed, `kind`.
3. `cd web && npm test` (checks every slot: fields, file exists, size and shape), then build and
   deploy as usual.

If the manifest or a file is missing or broken, the opening falls back to what ships today:
`spherex-sky.webp` for the sky, and Earth and hills drawn in code. The browser console says why.

## Fields

| Field | Meaning |
|---|---|
| `id` | `sky`, `earth` or `horizon`. Other ids are ignored. |
| `kind` | `real` (a photo or data image: its credit shows as written) or `illustration` (drawn or generated: shown as "Illustration: …"). Anything else counts as illustration. |
| `file` | A file name in this folder (`.webp`, `.png`, `.jpg`, `.avif`), or `null`: no sky image, or Earth / hills drawn in code. |
| `alt` | One plain sentence saying what the image shows. Read by screen readers for the sky. |
| `aspect` | Width:height the code expects, e.g. `16:10`. Within 3%. |
| `min_px` | Smallest `[width, height]` in pixels that still looks sharp. |
| `credit` | Real image: the full credit line (`Sky: SPHEREx DR1 at 1.35 µm, NASA/JPL-Caltech/IPAC (IRSA).`). Illustration: what and how, no full stop (`Earth, made with Higgsfield`). A slot with a file and no credit is not shown. |
| `brief` | What the image must show, honestly. Read it before making one. |

## Slots

| Slot | Today | Shape | Where it shows |
|---|---|---|---|
| `sky` | `spherex-sky.webp`, real SPHEREx DR1 data | 16:10, ≥ 1600 × 1000 | Behind the whole opening: under the title, and through the telescope's lens at the end. |
| `earth` | Drawn in code | 1:1, ≥ 1600 × 1600 | Earth from space, the first descent. Cut to a circle; turns slowly; grows to about three screens wide before it fades. |
| `horizon` | Drawn in code | 3:1, ≥ 2400 × 800, transparent above the ridges | The ground Galileo stands on. Its middle row is the ground line. Spans the screen's width; crops at the sides on phones. |

Galileo and his telescope are not a slot: they are drawn as vectors in `web/src/opening/galileo.ts`
(camera in `web/src/intro/portrait.ts`), because the camera zooms 150–300× from the wide shot
into the objective lens, and no picture holds that. What the drawing follows: Galileo in Padua in
1609–10, a man of about 45 with a short reddish-brown beard and a receding hairline, in a
professor's long dark gown with a small plain white collar, no hat; a telescope of about a metre
(his surviving instruments of 1609–10 are 92 and 98 cm), slightly tapered, in red-brown leather
with gold tooling, objective at the wide far end. The hills carry a low, unlit skyline of Padua
(the campanile, the Palazzo della Ragione, the Basilica of Saint Anthony), drawn in `scene.ts`.

At the end the camera swings round to face the objective and the lens is cut out of the canvas:
the window shows the `sky` image behind it. Nothing is painted inside the window.

## Honesty

- The sky is always real SPHEREx data. Never a generated or painted sky, never added objects. The
  telescope's lens is a window cut through to it: no stars or objects are drawn in it.
- Generated or drawn art is labelled "Illustration" on screen; the code adds the word, so a
  manifest can't pass art off as data.
- The credit line stays on screen as long as the sky does: it fades only when the whole opening
  fades into the site. In the still version (reduced motion) it is always there.
- A real NASA photo of Earth (Blue Marble, DSCOVR EPIC; public domain) is better than generated
  art: mark it `real` and credit it.

## Credit line

Built from the slots on screen. Today:

> Sky: SPHEREx DR1 at 1.35 µm, NASA/JPL-Caltech/IPAC (IRSA). Illustration: Earth, planets and Galileo drawn in code.

With a generated Earth:

> Sky: SPHEREx DR1 at 1.35 µm, NASA/JPL-Caltech/IPAC (IRSA). Illustration: Earth, made with Higgsfield; planets and Galileo drawn in code.
