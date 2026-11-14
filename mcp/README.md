# SkyShift MCP server

Lets an AI assistant (Claude Code, Claude Desktop, Cursor, any MCP client) use SkyShift:
browse the pipeline's candidates, look at blink frames, measure motion and check known objects.
It only reads the public site (https://skyshift.web.app): the static files first, then `/api/`.
It needs no account or key and costs nothing in the cloud.

## Add it

Claude Code, from the repository root:

```sh
claude mcp add skyshift -- uv run --project mcp skyshift-mcp
```

Claude Desktop (`claude_desktop_config.json`), with the absolute path of `mcp/`:

```json
{ "mcpServers": { "skyshift": {
    "command": "uv", "args": ["run", "--project", "/path/to/skyshift/mcp", "skyshift-mcp"] } } }
```

Another site (a local server, a preview channel): set `SKYSHIFT_BASE`, e.g.
`SKYSHIFT_BASE=http://127.0.0.1:8081`. Streamable HTTP instead of stdio:
`uv run --project mcp skyshift-mcp --http --port 8765` (serves `/mcp` on 127.0.0.1).

## Tools

| tool | reads | gives |
|---|---|---|
| `top_candidates` | `/data/candidates.json` | Best candidates, filtered (kind, grade, score, S/N, detector, zone, Gaia match, flags, distance to known objects) and ranked; `sort=rate` ranks movers by ″/h, then high_pm by mas/yr; `notes` says when a file has no grades |
| `candidates_near` | `/data/candidates.json` | Cone search around a name or position, nearest first |
| `get_candidate` | `/data/candidates.json` | One candidate's full record, its frames URL and a link to its page |
| `blink` | `/data/cand/<id>/`, `/data/<slug>/`, else `/api/frames` | One PNG: the epochs in time order (same stretch) and a last − first difference, with marks: Gaia DR3 stars moved to each date (cyan circles), known asteroids and comets (green diamonds), the pipeline's detections by class, and a legend. A featured target opens on its own detector (D6 for WISE 0855−0714); an hours-clock candidate on the frames nearest its find, with `find_session` (nearest frame, and a warning when live frames miss its session). `zoom_arcmin` crops in; `animate` adds a looping GIF. Plus the epochs' dates, flags, known objects and the measured layer |
| `spectrum` | `/data/<slug>/object.json`, else `/api/object` | The SPHEREx spectrum as a PNG: flux against wavelength by detector, error bars, saturated points hollow, the classifier's molecule bands shaded (H2O, CH4, CO, ices, PAH); `visit`, `scale` |
| `measure` | `/data/<slug>/object.json`, else `/api/object` | Shift, position angle, rate ″/yr with sigma, Gaia's prediction, spectrum points |
| `measured_dots` | the `measured` layer of the frames (static, else live) | The pipeline's own detections in a field by class (star, known, mover, suspect, artifact, other), hours-clock tracks, months-clock fitted motions |
| `known_objects` | `/api/known` | Asteroids and comets predicted near a position at an MJD (MPC orbits) |
| `gaia_stars` | `/api/gaia` | Gaia DR3 stars near a position, brightest first |
| `batch_check` | `/api/resolve`, `/data/candidates.json` | Up to 50 names or positions: featured field, candidates nearby, a site link; `measure` for the first 5 |
| `top_voted`, `votes_for` | Firestore `votes` (public, read-only) | Community votes per candidate (mover, changing, junk, unsure) and distinct voters; voter ids are never returned |
| `featured_targets` | bundled `targets.json` | The featured targets: what to look for, what to expect, links |
| `validation` | `/data/validation.json` | The checks V1–V11 in plain words, or one check in full |
| `resolve` | `/api/resolve`, else bundled `targets.json` | A name or coordinate string → ICRS degrees; featured names (the asteroids too, e.g. `Elpis`) without case |

Units follow contracts/api.md: degrees, arcmin for fields and radii, arcsec for shifts, mas/yr,
MJD (UTC), µJy, µm.

## Resources and prompts

Resources: `skyshift://facts` (one page: the two clocks, detectors, pixel and PSF, the visit rule,
units, what candidate kinds, grades and reasons mean, honest limits), `skyshift://contracts/api`
and `skyshift://contracts/flags`. Run from a checkout they read the repo's files; an installed
package uses the copies in `src/skyshift_mcp/data/`.

Prompts (in Claude Code: `/skyshift:<name>`):

| prompt | does |
|---|---|
| `explain_candidate(candidate_id)` | Record → zoomed blink → known asteroids → motion → spectrum, then one verdict: likely artefact, known object, known star motion, or worth a human look. Never claims a discovery |
| `triage_movers(limit)` | The best unflagged movers, each explained briefly, ranked for a human vote |
| `check_my_target(target)` | Your own object: measurement, months blink, candidates nearby, how far to trust it |

## Try it

- "What are the five best fast movers SkyShift found that no known asteroid explains? Show me the
  best one."
- "Blink Barnard's Star on the months clock and tell me which way it moved."
- "Show me the spectrum of WISE 0855−0714. Which molecule bands do you see?"
- "Is there any candidate within 20′ of RA 123.6, Dec 20.7?"
- "How do we know SkyShift's measurements are right?"
- `/skyshift:explain_candidate hunt-648380072434-87805628`

## Tests

```sh
cd mcp && uv run pytest              # offline, uses fixtures/api/
SKYSHIFT_NETWORK=1 uv run pytest     # also calls the live site
```
