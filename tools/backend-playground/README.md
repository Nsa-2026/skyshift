# Temporary backend playground

A disposable frontend to exercise SkyShift's current API and frame contracts. Everything lives
in this directory. Delete `tools/backend-playground/` to remove it; it does not own the production
`web/`, change backend endpoints, or add frontend dependencies. This feature PR intentionally adds
a developer tool outside the P3/P4 production areas. No cloud deployment is performed.

## Start

From the repository root (Python 3.9+ for the UI server):

```sh
python3 tools/backend-playground/serve.py
# Open http://127.0.0.1:4173
```

The UI works with the explicitly labelled synthetic fixtures even without a backend.
To test the real FastAPI shell, start it separately:

```sh
cd server
uv sync --frozen
uv run uvicorn skyshift_server.app:app --host 127.0.0.1 --port 8081
```

To connect to another backend, pass an HTTP(S) origin to the UI server:

```sh
python3 tools/backend-playground/serve.py --api-origin http://127.0.0.1:8081
```

The dev server binds to loopback only and forwards only GET `/api/*` to that fixed origin.
No CORS changes, credentials, database writes, or production server changes are needed.
The UI server is a local development helper, not a production hosting service.

## Optional: inspect real static data on the engine branch

```sh
git fetch origin
python3 tools/backend-playground/serve.py --static-ref origin/p2/engine
```

Select **Static → API**, Barnard's Star, and Load observation. The server reads committed
`web/public/data/` directly from that Git ref without changing branches or copying data into
`main`. It pins the ref to a commit at startup: restart after fetching new data. Supports the
engine branch's `index.json` files (clock + field selection), then standard `frames.json`,
then `/api/frames` as fallback. Synthetic fixtures stay in `fixtures/api/`, never public/data.

## What to test

- Live API: resolve → frames → preload binary arrays → object / spectrum. Select a target,
  clock, field and detector, then Load observation. Only one detector plays at a time.
- Blink, step, speed, shared stretch, invert, masked-aware subtract and add; hover pixel values.
- Object motion, Gaia prediction, reference count and per-visit spectrum with error bars.
  The app labels synthetic backend responses; fixtures are not scientific measurements.
- Under the hood: individual health/resolve/frames/object/gaia/known requests, HTTP status,
  latency, raw JSON, binary sizes, copy and session export. MJD is UTC; angles are degrees,
  field/radius arcmin, motion arcsec/year, spectrum µJy, image values MJy/sr.
- Run API checks verifies health, built-in name resolution and invalid-coordinate rejection.
  It does not certify science accuracy or a production deployment.
- Changing detector/field requires Load observation; changing clock also updates valid field choices.
- Fixture mode ignores target input intentionally: Months is the Barnard-like synthetic field,
  Hours is the comet-like fixture. Missing object measurements remain empty.

## Checks

```sh
node --test tools/backend-playground/model.test.mjs
python3 tools/backend-playground/serve.py --help
```

Tests cover endianness, quantization, missing pixels, detector isolation, frame ordering,
masked-aware arithmetic, static selection and MJD conversion. No npm install or build is needed.
Formal frontend developers can discard this entire folder when their app is ready.
