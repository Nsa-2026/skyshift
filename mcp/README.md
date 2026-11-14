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
| `top_candidates` | `/data/candidates.json` | Best candidates, filtered (kind, grade, score, S/N, detector, zone, Gaia match, flags, distance to known objects) and ranked |
| `candidates_near` | `/data/candidates.json` | Cone search around a name or position, nearest first |
| `get_candidate` | `/data/candidates.json` | One candidate's full record, its frames URL and a link to its page |
| `blink` | `/data/cand/<id>/`, `/data/<slug>/`, else `/api/frames` | One PNG: the epochs in time order (same stretch) and a last − first difference, plus the epochs' dates, flags, known objects and the measured layer |
| `measure` | `/data/<slug>/object.json`, else `/api/object` | Shift, position angle, rate ″/yr with sigma, Gaia's prediction, spectrum |
| `known_objects` | `/api/known` | Asteroids and comets predicted near a position at an MJD (MPC orbits) |
| `gaia_stars` | `/api/gaia` | Gaia DR3 stars near a position, brightest first |
| `resolve` | `/api/resolve` | A name or coordinate string → ICRS degrees |

Units follow contracts/api.md: degrees, arcmin for fields and radii, arcsec for shifts, mas/yr,
MJD (UTC), µJy, µm.

## Try it

- "What are the five best fast movers SkyShift found that no known asteroid explains? Show me the
  best one."
- "Blink Barnard's Star on the months clock and tell me which way it moved."
- "Is there any candidate within 20′ of RA 123.6, Dec 20.7?"

## Tests

```sh
cd mcp && uv run pytest              # offline, uses fixtures/api/
SKYSHIFT_NETWORK=1 uv run pytest     # also calls the live site
```
