# SkyShift

**Blink NASA's SPHEREx infrared sky, measure what moved, and check it against Gaia.**

SkyShift is built for the NASA Space Apps Challenge 2026, challenge "Planet X and SPHEREx".
Paste any sky coordinates and SkyShift blinks that spot of the SPHEREx sky on two clocks:

- **Months:** one stacked frame per visit, about six months apart. Nearby stars and brown dwarfs step across the field.
- **Hours:** single exposures inside one visit. Comets and asteroids cross the field.

Under every blink it measures how far the object moved and compares that with Gaia's prediction
and with known asteroid positions. It also builds a spectrum on the fly, coloured by visit.
Anyone can flag a dot and vote on whether it is real.

> **Status:** being built during the hackathon, 14–15 November 2026. Links to the live site and
> the video will be added here when they exist.

## Try these targets

Barnard's Star · Epsilon Indi B · WISE 0855−0714 · RW Aurigae · R Draconis, plus the interstellar
comet 3I/ATLAS and two numbered asteroids. The coordinate box accepts the challenge's own format,
for example `08h55m10.831s -7d14m42.53s`, as well as decimal degrees.

## How it works

```
IRSA image search ─► cutouts ─► stack and measure ─► frame data + JSON
  featured targets: scripts/precompute ─► static files on Firebase Hosting
  any coordinates:  Cloud Run API       ─► cached in Cloud Storage
ESA Gaia · SkyBoT · IRSA MOST · 3I/ATLAS table ─► same API, cached
Flags and votes ─► Firestore
```

Frames are sent to the browser as 16-bit data rather than pictures. The browser does the stretch,
invert, subtract and add, so a move of less than one pixel stays visible.

## Repository layout

| Folder | What it holds |
|---|---|
| `core/` | Shared science code: visit rule, flag masks, detection, centroids, shifts, photometry |
| `pipeline/` | The pilot run and the asteroid hunt zone on cloud machines |
| `server/` | FastAPI app on Cloud Run; builds blink frames for any coordinates |
| `web/` | Vite + TypeScript app: sky view, blink player, cards, votes |
| `scripts/` | Validation (`validate/`), static export of the featured targets (`precompute/`), checks |
| `contracts/` | The interfaces every part agrees on: `api.md`, `flags.md`, `dots.md` |
| `fixtures/` | Small examples used in tests; the API examples in `fixtures/api/` are synthetic and marked `"synthetic": true` |
| `infra/` | Cloud machine, Cloud Run and Firebase Hosting scripts |
| `docs/` | Planning notes and research written before and during the event |

## Plans

- [Concept plan](https://claude.ai/artifact/VECemCPhqjnSVDBnfqJyZd): what we build and why.
- [Dev plan](https://claude.ai/artifact/GhfDcQevT69fDKDY8hXrxS): architecture, who owns which folder,
  the contracts, setup and the hour-by-hour schedule.

Copies of both are in `docs/` (`skyshift-hackathon-plan.html`, `skyshift-dev-plan.html`).

## For developers

You need Git, Node 22, Python 3.12 and [uv](https://docs.astral.sh/uv/). Deploys also need the
Google Cloud CLI and the Firebase CLI.

- Python areas (`core/`, `pipeline/`, `server/`): `uv sync`, then `uv run pytest`.
- Web: `npm install`, then `npm test`.

Read `CLAUDE.md` and the `CLAUDE.md` in your area's folder before you start, and match
`contracts/` exactly. No secrets in the repo: Google access comes from `gcloud`; anything else
goes in `.env`, which Git ignores.

## Data and credits

- **NASA SPHEREx** Level 2 spectral images (DR1 and QR3) from NASA/IPAC IRSA: image search, cutouts,
  and the public copy at `s3://nasa-irsa-spherex`. Also the IRSA SPHEREx all-sky cube, the Moving
  Object Search Tool and IRSA's 3I/ATLAS table.
- **ESA Gaia DR3** through the ESA Gaia Archive.
- **IMCCE SkyBoT** for known solar-system objects.
- **2MASS** and **AllWISE** sky layers served by IRSA.
- **Aladin Lite 3.8.2** (CDS, GPL-3.0), loaded from jsDelivr at run time and not bundled.
- Modelled on WiseView and the Backyard Worlds: Planet 9 project.

<!-- TODO (P1, Sun 09:00): paste the SPHEREx acknowledgement and its two required citations word
     for word from IRSA's SPHEREx mission page, and the standard Gaia acknowledgement. -->

## Use of Super Intelligence (AI)

We used Claude (Anthropic) through Claude Code as a coding and research assistant. It wrote most
of the code from our instructions. We review it, run it and test it against known objects. It also
helped us read NASA documentation, plan the work and write text, including this README.
Commits made with it carry a `Co-Authored-By` line.

**Before and after kickoff.** Before 09:00 on Saturday 14 November 2026 we prepared only a cloud
account, planning notes (`docs/`) and repository configuration: the `CLAUDE.md` guides, the
interface contracts and the Claude Code settings. Claude helped write those too. All product
code was written after 09:00 on Saturday.

Every image, GIF and video we publish for this project is a screen recording of the running
tool on real SPHEREx data; none is AI-generated. The numbers come from our pipeline's output
files. Any object we plant in the frames for a demonstration is simulated and labelled as such.

## Licence

Apache-2.0, see [LICENSE](LICENSE). Outside libraries and data keep their own licences.
