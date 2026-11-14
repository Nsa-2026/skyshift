"""SkyShift API (contracts/api.md) on Cloud Run. Run locally:

    uv run uvicorn skyshift_server.app:app --reload

Endpoints: /api/frames, /api/object, /api/gaia, /api/known, /api/resolve, /api/health, and
/api/data/<key>/<name> for the frame bytes that a live /api/frames response points to.

- The engine (blink, gaia, known) is called in FastAPI's threadpool (plain `def` routes).
- While an engine function is missing or raises NotImplementedError, the route returns its
  example from fixtures/api/ ("synthetic": true), never cached, with `Cache-Control: no-store`.
- Real responses are cached whole by request in `Cache` (SKYSHIFT_CACHE; gs://nasa-glazing-data/cache
  on Cloud Run): api/<endpoint>/<key>.json, and frames/<key>/frames.json + frames/<key>/<blob>.
  Bump ENGINE_VERSION to invalidate everything after an engine change. Frames and /api/known keys
  also hold the known-object index's name (known.INDEX_KEY): a new MPC index means new entries.

Live builds (frames, object) must answer inside Firebase Hosting's 60 s limit for rewrites:
- Each build gets a Budget of LIVE_BUDGET_S (budget.py). What did not arrive in time is left out
  and the answer is partial: real data, sent with `Cache-Control: no-store`, NOT cached as the
  answer (its frame bytes go under a one-off key), so the next request, which finds the downloaded
  pieces in the cache, builds the whole thing.
- The route waits at most HARD_LIMIT_S for its build; past that it answers 503 "upstream" (the
  build goes on in the background and caches what it gets).
- Identical requests share one build (in this instance): a second click, or a retry, waits for
  the first instead of starting the same downloads again.
- One log line per build: "timing <what> total=... search=... cutout=... zodi=... gaia=..."
  (budget.Budget.summary), so the Cloud Run logs say where the time went.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import math
import os
import re
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import known as known_mod
from . import measured as measured_mod
from . import resolve as resolve_mod
from .budget import Budget, BudgetError
from .cache import Cache
from .errors import ApiError, NotFoundError, UpstreamError, bad_request

log = logging.getLogger("skyshift.api")

VERSION = "0.1.0"
ENGINE_VERSION = "4"  # part of every cache key
# Part of the /api/object key only. Answers cached before the measurement's detection guard
# (core.measure_shift, 14 Nov) gave shifts for spots with nothing on them (RA 125.5, Dec 19.4:
# "30.15" in 0.45 years", two noise peaks); a new rule must not serve them again. Frames keep theirs.
# detect-guard-2 (contracts/api.md v3.2): answers carry measure_reason and measure_hint.
OBJECT_RULE = "detect-guard-2"
FOV_ALLOWED = {"months": (5.0, 15.0), "hours": (20.0, 60.0)}
FOV_DEFAULT = {"months": 5.0, "hours": 20.0}
DET_DEFAULT = 2
R_MAX_ARCMIN = 60.0          # covers the corners of the 60' hours field (half-diagonal 42.4')
MJD_RANGE = (33282.0, 88069.0)  # 1900-2100

CACHE_REAL = "public, max-age=3600"
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_NONE = "no-store"

# Firebase Hosting drops a rewrite to Cloud Run after 60 s; a cold instance takes ~6 s to start.
LIVE_BUDGET_S = float(os.environ.get("SKYSHIFT_BUDGET_S", "40"))  # the engine's deadline
HARD_LIMIT_S = float(os.environ.get("SKYSHIFT_HARD_LIMIT_S", "48"))  # the route waits no longer
RETRY_AFTER_S = 20


def _repo_root() -> Path:
    return Path(os.environ.get("SKYSHIFT_REPO_ROOT") or Path(__file__).resolve().parents[3])


def fixtures_dir() -> Path:
    return Path(os.environ.get("SKYSHIFT_FIXTURES") or _repo_root() / "fixtures" / "api")


# ---------------------------------------------------------------- app and errors

def warm_up() -> None:
    """Import the heavy modules and open the cache before the first request (Cloud Run runs this
    during startup, with startup CPU boost), so a cold instance's first build does not pay ~3 s."""
    try:
        import astropy.wcs  # noqa: F401
        from astroquery.ipac.irsa import Irsa  # noqa: F401

        from . import blink, obj  # noqa: F401
        get_cache()
    except Exception:  # noqa: BLE001 - never block startup
        log.exception("warm-up failed")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    if os.environ.get("SKYSHIFT_WARM_UP", "1") != "0":
        warm_up()
        known_mod.warm_up_in_background(get_cache())  # MPC index + kete, in a thread
    yield


app = FastAPI(title="SkyShift API", version=VERSION, docs_url="/api/docs",
              openapi_url="/api/openapi.json", redoc_url=None, lifespan=_lifespan)

_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def reset_cache(cache: Cache | None = None) -> None:
    """For tests: use another cache (None = build from SKYSHIFT_CACHE on next use)."""
    global _cache
    _cache = cache


def _error(status: int, code: str, message: str) -> JSONResponse:
    headers = {"Cache-Control": CACHE_NONE}
    if status == 503:
        headers["Retry-After"] = str(RETRY_AFTER_S)
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                        headers=headers)


@app.exception_handler(ApiError)
async def _api_error(_: Request, e: ApiError) -> JSONResponse:
    return _error(e.status, e.code, e.message)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, e: RequestValidationError) -> JSONResponse:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()) if x != "query")
        parts.append(f"{loc}: {err.get('msg')}")
    return _error(400, "bad_request", "; ".join(parts) or "bad request")


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, e: StarletteHTTPException) -> JSONResponse:
    code = "not_found" if e.status_code == 404 else "bad_request" if e.status_code < 500 else "internal"
    return _error(e.status_code, code, str(e.detail))


@app.exception_handler(Exception)
async def _internal_error(_: Request, e: Exception) -> JSONResponse:
    log.exception("internal error")
    return _error(500, "internal", f"{type(e).__name__}: internal error")


# ---------------------------------------------------------------- helpers

def _upstream_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [ConnectionError, TimeoutError]
    for mod, name in (("httpx", "HTTPError"), ("requests", "RequestException"),
                      ("pyvo.dal.exceptions", "DALServiceError"),
                      ("astroquery.exceptions", "RemoteServiceError"),
                      ("astroquery.exceptions", "TimeoutError"),
                      ("google.api_core.exceptions", "ServiceUnavailable"),
                      ("botocore.exceptions", "EndpointConnectionError")):
        try:
            types.append(getattr(importlib.import_module(mod), name))
        except (ImportError, AttributeError):
            pass
    return tuple(types)


_UPSTREAM = _upstream_types()

_NOT_READY = object()


def _engine(module: str, func: str) -> Callable[..., Any] | None:
    """skyshift_server.<module>.<func>, or None while the engine has not written it."""
    full = f"skyshift_server.{module}"
    try:
        mod = importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name == full:
            return None
        raise
    return getattr(mod, func, None)


def _call(module: str, func: str, *args: Any, **kwargs: Any) -> Any:
    """Call an engine function; _NOT_READY if missing or NotImplementedError; map failures."""
    fn = _engine(module, func)
    if fn is None:
        return _NOT_READY
    try:
        return fn(*args, cache=get_cache(), **kwargs)
    except NotImplementedError:
        return _NOT_READY
    except ApiError:
        raise
    except _UPSTREAM as e:
        log.warning("upstream failure in %s.%s: %r", module, func, e)
        raise UpstreamError(f"{type(e).__name__}: {e}"[:300]) from e


_builds = ThreadPoolExecutor(32, thread_name_prefix="build")
_puts = ThreadPoolExecutor(16, thread_name_prefix="put")


class _Job:
    """One live build and the requests waiting for it."""

    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.waiters = 0
        self.future: Future | None = None


_inflight: dict[str, _Job] = {}
_inflight_lock = threading.Lock()


class ClientGone(Exception):
    """The client went away while its build ran."""


async def _shared(request: Request, key: str, name: str, fn: Callable[[Budget], Any]) -> Any:
    """Run fn(budget) once for all identical requests in flight (a retry or a second click
    joins the running build) and wait at most HARD_LIMIT_S for it: past that, 503 upstream.
    When the last waiting client has gone (disconnected, or answered 503), the build's budget is
    stopped: it starts no more downloads, and those in flight finish into the cache. So an
    abandoned request never holds the instance for long."""
    with _inflight_lock:
        job = _inflight.get(key)
        if job is None:
            job = _Job(Budget(LIVE_BUDGET_S, name))

            def run(job: _Job = job) -> Any:
                try:
                    return fn(job.budget)
                finally:
                    with _inflight_lock:
                        if _inflight.get(key) is job:
                            del _inflight[key]

            job.future = _builds.submit(run)
            _inflight[key] = job
        job.waiters += 1
    loop = asyncio.get_running_loop()
    until = loop.time() + HARD_LIMIT_S
    fut = asyncio.wrap_future(job.future)
    done = False
    try:
        while True:
            left = until - loop.time()
            if left <= 0:
                raise BudgetError(f"the SPHEREx archive is slow right now: no answer in {HARD_LIMIT_S:.0f} s. "
                                  "Try again in a minute: the pieces downloaded so far are kept")
            finished, _ = await asyncio.wait({fut}, timeout=min(1.0, left))
            if finished:
                done = True
                return fut.result()
            if await request.is_disconnected():
                raise ClientGone()
    finally:
        with _inflight_lock:
            job.waiters -= 1
            last = job.waiters == 0
        if last and not done and not job.future.done():
            job.budget.stop("nobody is waiting any more")


@app.exception_handler(ClientGone)
async def _client_gone(_: Request, e: ClientGone) -> Response:
    return Response(status_code=499)  # nginx's "client closed request"; nobody reads it


def jsonable(x: Any) -> Any:
    """Plain JSON: numpy scalars/arrays to Python, NaN/inf to null, keys kept (null never omitted)."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, bool) or x is None or isinstance(x, (str, int)):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if hasattr(x, "tolist"):  # numpy scalar or array
        return jsonable(x.tolist())
    if hasattr(x, "item"):
        return jsonable(x.item())
    return x


def _json(content: Any, cache_control: str = CACHE_REAL) -> JSONResponse:
    return JSONResponse(jsonable(content), headers={"Cache-Control": cache_control})


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((fixtures_dir() / name).read_text())


def _synthetic(content: dict[str, Any]) -> JSONResponse:
    return _json(content, CACHE_NONE)


def _key(endpoint: str, **params: Any) -> str:
    material = json.dumps({"v": ENGINE_VERSION, "endpoint": endpoint, **params}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def _cache_get_json(key: str) -> Any | None:
    try:
        raw = get_cache().get(key)
    except Exception:  # a cache outage must not break the API
        log.exception("cache get failed: %s", key)
        return None
    return json.loads(raw) if raw is not None else None


def _cache_put_json(key: str, content: Any) -> None:
    try:
        get_cache().put(key, json.dumps(jsonable(content)).encode(), "application/json")
    except Exception:
        log.exception("cache put failed: %s", key)


def _finite(name: str, v: float) -> float:
    if not math.isfinite(v):
        raise bad_request(f"{name} must be a finite number")
    return v


def _check_pos(ra: float, dec: float) -> tuple[float, float]:
    _finite("ra", ra), _finite("dec", dec)
    if not 0 <= ra <= 360:
        raise bad_request("ra must be 0-360 degrees")
    if not -90 <= dec <= 90:
        raise bad_request("dec must be -90 to 90 degrees")
    return round(ra % 360.0, 7), round(dec, 7)


def _check_r(r: float) -> float:
    _finite("r", r)
    if not 0 < r <= R_MAX_ARCMIN:
        raise bad_request(f"r must be over 0 and at most {R_MAX_ARCMIN:g} arcmin")
    return round(r, 4)


# ---------------------------------------------------------------- routes

@app.get("/api/health")
def health() -> JSONResponse:
    root = get_cache().root
    return _json({"status": "ok", "version": VERSION, "engine_version": ENGINE_VERSION,
                  "cache": "gcs" if root.startswith("gs://") else "local"}, CACHE_NONE)


@app.get("/api/resolve")
def resolve(q: str = "") -> JSONResponse:
    return _json(resolve_mod.resolve(q), CACHE_REAL)


@app.get("/api/frames")
async def frames(request: Request, ra: float, dec: float, fov: float | None = None, clock: str = "months",
                 det: int = DET_DEFAULT) -> JSONResponse:
    ra, dec = _check_pos(ra, dec)
    if clock not in FOV_ALLOWED:
        raise bad_request("clock must be months or hours")
    fov = FOV_DEFAULT[clock] if fov is None else _finite("fov", fov)
    if fov not in FOV_ALLOWED[clock]:
        allowed = " or ".join(f"{f:g}" for f in FOV_ALLOWED[clock])
        raise bad_request(f"fov for clock={clock} must be {allowed} arcmin")
    if not 1 <= det <= 6:
        raise bad_request("det must be 1-6")

    key = _key("frames", ra=ra, dec=dec, fov=fov, clock=clock, det=det, known=known_mod.INDEX_KEY,
               measured=measured_mod.VERSION)
    cached = await run_in_threadpool(_cache_get_json, f"frames/{key}/frames.json")
    # a cached answer is rebuilt (its cutouts are cached) when a zone has since got results here
    if cached is not None and not await run_in_threadpool(_measured_stale, cached):
        return _json(cached)
    content, cache_control = await _shared(
        request, f"frames/{key}", f"frames {clock} {fov:g}' D{det} ({ra}, {dec})",
        lambda budget: _build_frames(key, ra, dec, fov, clock, det, budget))
    return _json(content, cache_control)


def _measured_stale(cached: dict) -> bool:
    try:
        return measured_mod.stale(cached)
    except Exception:  # noqa: BLE001 - never fail a cached answer over this
        log.exception("measured stale check failed")
        return False


def _build_frames(key: str, ra: float, dec: float, fov: float, clock: str, det: int,
                  budget: Budget) -> tuple[dict, str]:
    """(response JSON, Cache-Control) of a live frames build; caches a complete one whole."""
    try:
        result = _call("blink", "build_frames", ra, dec, fov, clock, det, budget=budget)
    except ApiError as e:
        budget.cut(f"error {e.status}: {e.message[:120]}")
        raise
    finally:
        budget.log()
    if result is _NOT_READY:
        meta = _fixture("frames_barnard.json" if clock == "months" else "frames_comet.json")
        for ep in meta.get("epochs", []):
            ep["data_url"] = f"/api/data/fixture/{ep['data_url']}"
        return meta, CACHE_NONE

    partial = bool(getattr(result, "partial", None))
    data_key = uuid.uuid4().hex if partial else key  # a partial answer's bytes never get reused
    meta = jsonable(result.meta)
    for ep in meta.get("epochs", []):
        name = ep.get("data_url")
        if not name or name.startswith(("data:", "/", "http:", "https:")):
            continue  # already resolvable
        _check_blob_name(name)
        if name not in result.blobs:
            raise ApiError(500, "internal", f"engine returned no bytes for {name}")
        ep["data_url"] = f"/api/data/{data_key}/{name}"
    cache = get_cache()
    for name in result.blobs:
        _check_blob_name(name)
    # blobs first (in parallel): frames.json existing means all is there
    list(_puts.map(lambda kv: cache.put(f"frames/{data_key}/{kv[0]}", kv[1]), result.blobs.items()))
    if partial:
        return meta, CACHE_NONE
    _cache_put_json(f"frames/{key}/frames.json", meta)
    return meta, CACHE_REAL


_BLOB_NAME = re.compile(r"^[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*$")


def _check_blob_name(name: str) -> None:
    if not _BLOB_NAME.match(name) or any(p in ("", ".", "..") for p in name.split("/")):
        raise NotFoundError("no such frame")


@app.get("/api/data/{key}/{name:path}")
def data(key: str, name: str) -> Response:
    _check_blob_name(name)
    if key == "fixture":
        base = fixtures_dir().resolve()
        path = (base / name).resolve()
        if base not in path.parents or not path.is_file() or path.suffix != ".bin":
            raise NotFoundError("no such frame")
        return Response(path.read_bytes(), media_type="application/octet-stream",
                        headers={"Cache-Control": CACHE_NONE})
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise NotFoundError("no such frame")
    raw = get_cache().get(f"frames/{key}/{name}")
    if raw is None:
        raise NotFoundError("no such frame (expired from the cache?): ask /api/frames again")
    return Response(raw, media_type="application/octet-stream",
                    headers={"Cache-Control": CACHE_IMMUTABLE})


@app.get("/api/object")
async def object_(request: Request, ra: float, dec: float) -> JSONResponse:
    ra, dec = _check_pos(ra, dec)
    key = f"api/object/{_key('object', ra=ra, dec=dec, rule=OBJECT_RULE)}.json"
    cached = await run_in_threadpool(_cache_get_json, key)
    if cached is not None:
        return _json(cached)
    content, cache_control = await _shared(request, key, f"object ({ra}, {dec})",
                                           lambda budget: _build_object(key, ra, dec, budget))
    return _json(content, cache_control)


def _build_object(key: str, ra: float, dec: float, budget: Budget) -> tuple[dict, str]:
    try:
        result = _call("blink", "build_object", ra, dec, budget=budget)
    except ApiError as e:
        budget.cut(f"error {e.status}: {e.message[:120]}")
        raise
    finally:
        budget.log()
    if result is _NOT_READY:
        return _fixture("object_barnard.json"), CACHE_NONE
    if budget.partial:
        return jsonable(result), CACHE_NONE
    _cache_put_json(key, result)
    return jsonable(result), CACHE_REAL


@app.get("/api/gaia")
def gaia(ra: float, dec: float, r: float) -> JSONResponse:
    ra, dec = _check_pos(ra, dec)
    r = _check_r(r)
    key = f"api/gaia/{_key('gaia', ra=ra, dec=dec, r=r)}.json"
    cached = _cache_get_json(key)
    if cached is not None:
        return _json(cached)
    result = _call("gaia", "cone", ra, dec, r)
    if result is _NOT_READY:
        return _synthetic(_fixture("gaia_barnard.json"))
    content = {"gaia": [{**s, "id": str(s["id"])} for s in result]}
    _cache_put_json(key, content)
    return _json(content)


@app.get("/api/known")
def known(ra: float, dec: float, r: float, mjd: float) -> JSONResponse:
    ra, dec = _check_pos(ra, dec)
    r = _check_r(r)
    _finite("mjd", mjd)
    if not MJD_RANGE[0] <= mjd <= MJD_RANGE[1]:
        raise bad_request("mjd must be between 33282 (1900) and 88069 (2100)")
    mjd = round(mjd, 6)
    key = f"api/known/{_key('known', ra=ra, dec=dec, r=r, mjd=mjd, known=known_mod.INDEX_KEY)}.json"
    cached = _cache_get_json(key)
    if cached is not None:
        return _json(cached)
    result = _call("known", "cone", ra, dec, r, mjd)
    if result is _NOT_READY:
        return _synthetic(_fixture("known_comet.json"))
    content = {"mjd": mjd, "known": [{**k, "epoch_id": None} for k in result]}
    _cache_put_json(key, content)
    return _json(content)
