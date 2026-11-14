# Live known objects from MPC orbits (14 Nov 2026)

`/api/known` and `frames.json` `known[]` still ask SkyBoT. The pipeline (PR #30) moves all MPC orbits with kete
instead, but that takes ~15 s per call (2 s to build 1.6 M states, 13 s to propagate): too slow for a live
request whose first frame must come in under 15 s. This note gives a way to do it in well under a second,
measured on this laptop, and what P2 has to do to use it.

Code: `core/src/skyshift_core/known_index.py`, tests `core/tests/test_known_index.py`.

## Design

Two stages. The first is cheap and only has to be complete; the second is exact.

1. **Coarse cut, numpy only (`candidates`).** Every asteroid's MPC elements are evaluated with two-body
   (Kepler) motion, vectorised in float32: ~0.1 s for all of them at one instant. An object is kept when its
   two-body position is within r + margin of the field centre, seen from an approximate Earth (Standish's
   Earth-Moon barycentre elements, no SPICE). The margin is per object:
   - `err_deg`: the largest two-body vs kete n-body angle seen from the Earth's centre over the index's time
     range, measured at build time (every orbit every 30 days, orbits with q < 1.3 AU every 2 days), × 1.25 + 0.005°;
   - 4e-4 AU / Δ: SPHEREx up to ~7,100 km from the Earth's centre, the Moon's pull on the Earth (~4,700 km),
     the approximate Earth orbit (1.3e-4 AU worst case over the range); Δ = distance from the Earth;
   - 0.02°: light time (an apparent shift of at most v/c ≈ 0.01°) and float32 rounding.

   Times within 16 days of each other share one evaluation at the group's two ends: an object is kept when
   the nearer end is within r + margin + half the arc between its two positions + the bend of its path
   (|relative acceleration| · s² / 8 / Δ). Each exact time is then checked on that short list only. So a
   months-clock request (3 visits) costs 3 full evaluations and an hours-clock request (60 exposures in one
   visit) 2–4. Objects nearer than 0.2 AU at request time (close approaches), the ~100 `always` objects
   (two-body error over 20°) and all 973 comets skip the cut. (A first version also sent every object that
   comes within 0.05 AU of the Earth in the range, ~2,000 NEOs, and everything over 2°: 3,240 objects, which
   made kete the bottleneck at 0.4–1.9 s per request; the 0.2 AU rule and the per-object margin already cover them.)
2. **Exact, kete (`known_near`).** The candidates (comets included) are built as kete states from the same
   elements, moved with n-body physics to the first time, and checked with `kete.fov_state_check` against
   one cone of radius r per time, with light time, from the Earth's centre or from SPHEREx's own position
   (IMAGE header X_SC..VZ_SC). V from kete's H-G; fainter than V 21 dropped (the pipeline's MPC_V_MAX); comets
   have no V and are kept. This is the pipeline's computation, only on a short list.

**Index** = one `.npz` (numpy only to read): elements (q, e, i, node, argp, peri_time, epoch; float64), H, G,
`err_deg`, `always`, comet flag, names (UTF-8 blob + offsets), the MJD range it was checked for and the
snapshot's name. Names follow the pipeline's `object_table` (MPC name, else "(number)", else designation;
duplicates get " [designation]").
The build drops asteroids that are never brighter than V 21.5 in the range (unknown H is kept) and the ones
kete loses (13 Earth impactors, which kete propagates to NaN).

**Range**: MJD 60736–61465 (1 Mar 2025 – 1 Mar 2027), the mission so far (24 Apr 2025 – 5 Oct 2026) with
room on both sides. Outside it `known_near` raises ValueError (the margins weren't checked there).

### Why not a sky grid of positions every N days

The suggested index (each object's geocentric RA/Dec every N days, cut with a motion margin) needs at least
4 bytes per object per epoch: 1.58 M × 77 epochs × 4 B ≈ 490 MB for N = 7, ≈ 230 MB for N = 15, and
interpolating between epochs is off by up to ~6′ for main-belt objects at N = 7 (Earth's orbital curvature
over the interval) and by degrees for NEOs. Keeping the elements and measuring each orbit's two-body error
once gives a 1-epoch index (41 MB) and a tighter cut, at ~0.1 s of numpy per evaluation.

### Where the code lives and why

`core/` (`skyshift_core.known_index`): the server imports core/ and not pipeline/ (pipeline pulls gcsfs,
pyvo, astroquery, pandas and its CLI). Reading the index and the coarse cut need numpy only; kete is the
optional extra `known` (`skyshift-core[known]`, kete==3.3.0) used by `known_near` and `build_index`, so the
server image doesn't change until P2 opts in. `observer_state` is a copy of the pipeline's (pipeline/known_mpc.py
is unchanged; it can import core's later).

## Numbers (this laptop: Apple M5, 10 cores; kete 3.3.0; snapshot mpcorb + cometels 2026-11-14)

The laptop was also running other work (memory compressed, swapping), so treat times as ±50%.
Cloud Run has 2 vCPU: expect the kete part to take up to ~2× longer there.

**Index** (`known_index_2026-11-14.npz`, `np.savez_compressed`)
| | |
|---|---|
| size on disk | **41.0 MB** (912,666 rows: 911,693 asteroids of 1,580,451 + 973 comets) |
| left out | 668,745 asteroids never brighter than V 21.5 from Mar 2025 to Mar 2027; 13 lost by kete (Earth impactors) |
| build time | **642 s** on the laptop (n-body every 30 days for all: 433 s; q < 1.3 AU every 2 days: 204 s); peak RSS 1.9 GB |
| `err_deg` (two-body vs n-body × 1.25 + 0.005°) | median 0.022°, p99 0.13°, p99.9 2.8°; 1,207 over 2°, 100 over 20° (`always`) |
| approximate Earth (Standish EMB) vs kete's Earth | ≤ 1.3e-4 AU over the range (margin allows 4e-4 AU in total) |

**Request latency** (`known_near`, warm, Earth's centre, V ≤ 21; 3 fields per row: two near the ecliptic,
one anywhere; random dates in the mission). "one" = /api/known; "months" = 3 visits half a year apart;
"hours" = 63 exposures over a 20-day visit (the static hours set; the live API caps hours at 6 exposures).
| radius | times | latency (s) | candidates sent to kete | objects returned |
|---|---|---|---|---|
| 5′ | 1 | 0.15 0.20 0.23 | 1,627 1,698 1,761 | 0 1 0 |
| 5′ | 3 | 0.96 0.76 0.92 | 2,703 2,567 2,546 | 2 1 0 |
| 5′ | 63 | 1.17 0.71 0.61 | 2,129 2,041 1,851 | 39 18 3 |
| 15′ | 1 | 0.21 0.25 0.23 | 1,588 1,617 1,516 | 1 2 4 |
| 15′ | 3 | 0.88 1.33 0.86 | 2,831 3,182 2,516 | 14 14 0 |
| 15′ | 63 | 0.85 1.77 0.99 | 2,118 2,176 1,862 | 297 473 12 |
| 60′ | 1 | 0.31 0.41 0.24 | 2,133 2,988 2,036 | 75 171 84 |
| 60′ | 3 | 0.79 0.64 0.55 | 3,516 4,183 2,786 | 172 240 22 |
| 60′ | 63 | 0.57 0.48 0.37 | 3,271 3,358 1,868 | 4,583 4,133 51 |

The coarse cut is ~0.07 s per evaluation of the 912k orbits; the rest is kete on the candidates, most of
which are the 973 comets, the 100 `always` orbits and objects within 0.2 AU of the Earth. Frames radii are
fov/2·√2: 3.5′ (months 5′), 10.6′ (months 15′), 14.1′ (hours 20′), 42′ (hours 60′), inside this table.

**Completeness and accuracy** (truth = kete over the *whole* snapshot, 1,581,424 orbits, no V pre-cut and
no coarse cut, the same cones and times, V ≤ 21 or no H):
- 27 fields (the latency table), 603 cones: **10,412 of 10,414** object-times found, no extra objects,
  positions identical to the full computation (median 0.0000″, max 0.005″). Truth took 206 s of kete.
- The 2 misses (2010 GR163, 2010 LV37, both in one 60′ field) are asteroids with no H (2-day arcs from 2010):
  the build's V cut dropped all 285 orbits without H because `np.fmin` skipped their NaN V. Fixed after this
  run (`build_index` keeps unknown H, as documented; test added); the measured index file predates the fix,
  so the index to upload must be rebuilt with the command in "Refresh" (+285 rows at most).
- Targeted: a 5′ field centred on an object's kete position at a random mission time (plus times 12 h
  before and 15 min after, as an hours request), `v_max=None`: found **160/160** — 40 of the 2,000 orbits
  with the largest two-body error, 40 NEO-like (q < 1.3 AU), 40 with errors 0.25–20°, 40 at random.
  Median latency 0.26–0.37 s, max 0.79 s.
- Unit tests (`core/tests/test_known_index.py`): the coarse cut never misses an object of 4,000 synthetic
  main-belt and NEO orbits that is inside 5′/15′/60′ fields at one time, three visits or 60 exposures
  (75 fields), and Ceres comes out where kete puts it.

**Cold start** (one instance, first request)
| step | time |
|---|---|
| `import kete` with its kernels present | 1.1–2.1 s |
| `import kete` with an **empty** kernel cache (downloads ~104 MB from NAIF, GitHub, JPL) | **474 s** here: bake the kernels into the image |
| `load_index` from local disk (41 MB, decompress + Kepler set-up) | 0.26–0.35 s |
| reading 41 MB from gs://nasa-glazing-data in us-east4 | ~1 s (estimate, not measured) |

**Image size** with the `known` extra: kete 39 MB, pandas 53 MB, matplotlib 28 MB (+ its few deps), SPICE
kernels 104 MB; astropy, scipy, numpy and requests are already in the server image. About +230 MB, plus
41 MB if the index is baked in rather than read from the bucket.
**Memory**: the index arrays ~80 MB and float32 Kepler set-up ~40 MB per instance, plus ~50 MB of
temporaries during a cut: fine in 2 GiB.

### Not done / next
- kete runs once over all of a request's candidates, so a months request propagates every candidate across
  the whole year between its visits. Running `exact` per time group (per visit) with only that group's
  candidates should cut months requests to roughly the single-time cost; not measured.
- Elliptic comets could go through the coarse cut too (with a measured error); all 973 are sent to kete now.
- Positions from SPHEREx (`observers`) are wired but only tested through the pipeline's copy of the same code.
- Not checked against SkyBoT here (the pipeline compared kete with SkyBoT and JPL Horizons, PR #30).

## For P2: wiring it into server/

Nothing below is done yet: server/ is P2's area and nothing was uploaded to the bucket.

1. **Index in the bucket** (P1 or P2, from a laptop with gcloud; the file is built by the command in
   "Refresh" below):
   ```
   gcloud storage cp known_index_2026-11-14.npz gs://nasa-glazing-data/cache/mpc/known_index/known_index_2026-11-14.npz
   ```
   Path: `gs://nasa-glazing-data/cache/mpc/known_index/known_index_<snapshot date>.npz`, next to the orbit
   snapshots in `cache/mpc/orbits/`. Under SKYSHIFT_CACHE that is the key `mpc/known_index/known_index_<date>.npz`.
2. **Dependency** (`server/pyproject.toml`): `"skyshift-core[known]"` instead of `"skyshift-core"`, then
   `uv lock`. Even without that, server/uv.lock needs `uv lock` once: core's metadata gained the extra
   (`provides-extras = ["known"]`), a 2-line change; `uv sync --frozen` in the Dockerfile ignores it.
3. **Kernels in the image** (root `Dockerfile`, after the second `uv sync`): kete downloads ~100 MB of SPICE
   kernels (DE440s, Earth orientation, five big asteroids, WISE/Spitzer/SPHEREx) on first use, from NAIF,
   GitHub and JPL; that took 474 s here. Do it at build time:
   ```
   ENV KETE_CACHE_DIR=/app/kete/
   RUN mkdir -p /app/kete && /app/server/.venv/bin/python -c "import kete; kete.spice.get_state('Earth', 2461000.5)"
   ```
   (before the `chown`, so the `app` user can read it). Keep all files: kete re-downloads any that are missing.
4. **Code** (`server/src/skyshift_server/known.py`), roughly:
   ```python
   import io, functools
   from skyshift_core import known_index
   MPC_INDEX_KEY = "mpc/known_index/known_index_2026-11-14.npz"   # or env SKYSHIFT_KNOWN_INDEX

   @functools.lru_cache(1)
   def mpc_index() -> known_index.KnownIndex:
       raw = Cache().get(MPC_INDEX_KEY)            # 41 MB from the bucket, once per instance
       if raw is None:
           raise UpstreamError("known index missing")
       return known_index.load_index(io.BytesIO(raw))

   def cone_mpc(ra, dec, r_arcmin, mjds, epoch_ids=None):
       return known_index.known_near(ra, dec, r_arcmin / 60.0, mjds, index=mpc_index(), epoch_ids=epoch_ids)
   ```
   - `blink.known_for_epochs`: one `cone_mpc` call per distinct epoch centre with all of that centre's
     `mjd_mid`s and `id`s (not one call per epoch: the coarse cut shares work across times), r = fov/2·√2 as now.
     On any exception (index missing, MJD outside the index's range → ValueError) fall back to SkyBoT.
   - `/api/known`: `cone_mpc(ra, dec, r, [mjd])`; entries already have `epoch_id: null`.
   - Entries are exactly `{name, epoch_id, ra, dec, v, source:"mpc"}`, nearest first per epoch, V ≤ 21
     (`v_max=None` keeps everything the index holds, V < 21.5). Names follow the pipeline ("Ceres", "(12345)",
     "2024 AB1"), not SkyBoT's "(1) Ceres".
   - Optional: pass `observers=[{X_SC, …, VZ_SC}]` per time from the IMAGE header when a cutout is open, to
     predict from SPHEREx instead of the Earth's centre (≤ 10″ for main-belt objects, more for NEOs).
   - Warm-up: call `mpc_index()` and one `cone_mpc` in a background thread at startup (FastAPI lifespan),
     so the first user doesn't pay the cold start.
5. **Image size and cold start**: see the numbers above. Cloud Run has 2 GiB / 2 vCPU per instance; the index
   and the coarse cut's temporaries fit (~170 MB).

### Refresh

Rebuild after each new orbit snapshot (`skyshift-pipeline mpc-orbits`, from a laptop: pipeline/CLAUDE.md)
and before the range ends (1 Mar 2027; extend `MJD_HI`). A stale index still works: it just doesn't know
objects discovered after its snapshot. On a laptop (≈ 11 min here, 1.9 GB RAM):
```
cd pipeline && uv sync
gcloud storage cp gs://nasa-glazing-data/cache/mpc/orbits/mpcorb_<date>.parquet gs://nasa-glazing-data/cache/mpc/orbits/cometels_<date>.parquet /tmp/
PYTHONPATH=../core/src:src uv run python -m skyshift_core.known_index /tmp/mpcorb_<date>.parquet /tmp/cometels_<date>.parquet /tmp/known_index_<date>.npz
gcloud storage cp /tmp/known_index_<date>.npz gs://nasa-glazing-data/cache/mpc/known_index/
```
then point `MPC_INDEX_KEY` at the new file (a new name, so running instances keep a consistent file).
