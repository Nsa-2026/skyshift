"""API shell tests: contract shapes (fixture fallback while the engine is missing), validation,
error format, the frame-bytes route, whole-response caching. Offline: the engine is faked."""

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from skyshift_server import app as app_mod
from skyshift_server import blink
from skyshift_server.cache import Cache
from skyshift_server.errors import NotFoundError, UpstreamError

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "api"

EPOCH_KEYS = {"id", "kind", "mjd_mid", "mjd_start", "mjd_end", "det", "um_min", "um_max",
              "n_exposures", "flag_frac", "sat_frac", "data_url", "scale", "zero"}
FRAMES_KEYS = {"clock", "center", "pixel_arcsec", "size", "epochs", "gaia", "known",
               "data_release", "archive_checked"}
GAIA_KEYS = {"id", "ra", "dec", "pmra", "pmdec", "parallax", "g"}
KNOWN_KEYS = {"name", "epoch_id", "ra", "dec", "v", "source"}


@pytest.fixture
def cache(tmp_path):
    c = Cache(str(tmp_path / "cache"))
    app_mod.reset_cache(c)
    yield c
    app_mod.reset_cache(None)


@pytest.fixture
def client(cache):
    return TestClient(app_mod.app, raise_server_exceptions=False)


def _fake_module(monkeypatch, name, **funcs):
    mod = types.ModuleType(f"skyshift_server.{name}")
    for k, v in funcs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"skyshift_server.{name}", mod)
    return mod


def _err(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


# ---------------------------------------------------------------- fixture fallback

@pytest.fixture
def no_engine(monkeypatch):
    """Engine absent: blink raises NotImplementedError, gaia/known modules don't exist."""
    monkeypatch.setenv('SKYSHIFT_ALLOW_SYNTHETIC', '1')
    def not_ready(*args, **kwargs):
        raise NotImplementedError
    monkeypatch.setattr(blink, 'build_frames', not_ready)
    monkeypatch.setattr(blink, 'build_object', not_ready)
    for m in ("gaia", "known"):
        monkeypatch.delitem(sys.modules, f"skyshift_server.{m}", raising=False)
    real = app_mod.importlib.import_module

    def fake_import(name, *a, **k):
        if name in ("skyshift_server.gaia", "skyshift_server.known"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real(name, *a, **k)

    monkeypatch.setattr(app_mod.importlib, "import_module", fake_import)


@pytest.mark.parametrize("clock,fixture", [("months", "frames_barnard.json"), ("hours", "frames_comet.json")])
def test_frames_fixture_fallback(client, no_engine, clock, fixture):
    r = client.get("/api/frames", params={"ra": 269.446, "dec": 4.77, "clock": clock})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["synthetic"] is True
    assert FRAMES_KEYS <= set(body)
    nx, ny = body["size"]
    for ep in body["epochs"]:
        assert set(ep) == EPOCH_KEYS
        d = client.get(ep["data_url"])
        assert d.status_code == 200
        assert d.headers["content-type"] == "application/octet-stream"
        assert len(d.content) == 2 * nx * ny
    for g in body["gaia"]:
        assert set(g) == GAIA_KEYS and isinstance(g["id"], str)
    for k in body["known"]:
        assert set(k) == KNOWN_KEYS


def test_object_fixture_fallback(client, no_engine):
    r = client.get("/api/object", params={"ra": 269.446, "dec": 4.77})
    assert r.status_code == 200
    body = r.json()
    assert body["synthetic"] is True
    assert {"name", "gaia", "known_ucd", "spectrum", "measure"} <= set(body)
    assert set(body["measure"]) == {"shift_arcsec", "pa_deg", "dt_yr", "rate_arcsec_yr",
                                    "sigma_arcsec", "n_ref_stars", "predicted_arcsec"}


def test_gaia_and_known_fixture_fallback(client, no_engine):
    g = client.get("/api/gaia", params={"ra": 269.446, "dec": 4.77, "r": 3}).json()
    assert g["synthetic"] is True and all(set(s) == GAIA_KEYS for s in g["gaia"])
    k = client.get("/api/known", params={"ra": 166.8, "dec": 5.3, "r": 10, "mjd": 61023.3}).json()
    assert k["synthetic"] is True and isinstance(k["mjd"], float)
    assert all(set(x) == KNOWN_KEYS and x["epoch_id"] is None for x in k["known"])


def test_fixture_data_route_is_confined(client):
    assert client.get("/api/data/fixture/frames/barnard/d2-v1.bin").status_code == 200
    for bad in ("/api/data/fixture/frames_barnard.json", "/api/data/fixture/../../CLAUDE.md",
                "/api/data/fixture/frames/%2e%2e/%2e%2e/frames_barnard.json",
                "/api/data/fixture/frames/barnard/nope.bin"):
        _err(client.get(bad), 404, "not_found")


# ---------------------------------------------------------------- real engine (faked)

def _frames_result(n=3, nx=4, ny=3):
    epochs, blobs = [], {}
    for i in range(1, n + 1):
        name = f"frames/m5_d2_v{i}.bin"
        epochs.append({"id": f"d2-v{i}", "kind": "visit", "mjd_mid": np.float64(60900.0 + 180 * i),
                       "mjd_start": 60890.0 + 180 * i, "mjd_end": 60910.0 + 180 * i, "det": 2,
                       "um_min": 1.1, "um_max": 1.6, "n_exposures": np.int64(19), "flag_frac": 0.01,
                       "sat_frac": float("nan"), "data_url": name, "scale": 0.01, "zero": -1.0})
        blobs[name] = np.full(nx * ny, i, "<u2").tobytes()
    meta = {"clock": "months", "center": {"ra": 10.0, "dec": 20.0}, "pixel_arcsec": 2.05,
            "size": [nx, ny], "epochs": epochs,
            "gaia": [{"id": "123", "ra": 10.0, "dec": 20.0, "pmra": 1.0, "pmdec": 2.0,
                      "parallax": None, "g": 15.0}],
            "known": [], "data_release": "DR1+QR3", "archive_checked": "2026-11-14"}
    return blink.Frames(meta=meta, blobs=blobs)


def test_frames_real_cached_and_served(client, cache, monkeypatch):
    calls = []

    def build_frames(ra, dec, fov, clock, det, *, cache=None):
        calls.append((ra, dec, fov, clock, det))
        assert cache is not None
        return _frames_result()

    monkeypatch.setattr(blink, "build_frames", build_frames)
    p = {"ra": 10, "dec": 20, "fov": 5, "clock": "months", "det": 2}
    r = client.get("/api/frames", params=p)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"].startswith("public")
    body = r.json()
    assert "synthetic" not in body and calls == [(10.0, 20.0, 5.0, "months", 2)]
    assert body["epochs"][0]["sat_frac"] is None          # NaN -> null, key kept
    assert body["gaia"][0]["parallax"] is None
    for i, ep in enumerate(body["epochs"], 1):
        assert ep["data_url"].startswith("/api/data/") and ep["data_url"].endswith(f"m5_d2_v{i}.bin")
        d = client.get(ep["data_url"])
        assert d.status_code == 200 and d.headers["content-type"] == "application/octet-stream"
        assert "immutable" in d.headers["cache-control"]
        assert np.frombuffer(d.content, "<u2").tolist() == [i] * 12

    # second identical request: from the cache, engine not called
    r2 = client.get("/api/frames", params={**p, "ra": 10.00000001})
    assert r2.json() == body and len(calls) == 1
    # another detector: new key
    client.get("/api/frames", params={**p, "det": 3})
    assert len(calls) == 2


def test_frames_defaults(client, monkeypatch):
    seen = []
    monkeypatch.setattr(blink, "build_frames", lambda *a, cache=None: seen.append(a) or _frames_result())
    client.get("/api/frames", params={"ra": 1, "dec": 2})
    client.get("/api/frames", params={"ra": 1, "dec": 2, "clock": "hours"})
    assert seen == [(1.0, 2.0, 5.0, "months", 2), (1.0, 2.0, 20.0, "hours", 2)]


def test_data_route_unknown_key(client):
    _err(client.get("/api/data/" + "0" * 32 + "/frames/x.bin"), 404, "not_found")
    _err(client.get("/api/data/notahash/frames/x.bin"), 404, "not_found")


def test_object_real_and_cached(client, monkeypatch):
    n = []
    obj = {"name": None, "gaia": None, "known_ucd": None, "spectrum": [], "measure": None}
    monkeypatch.setattr(blink, "build_object", lambda ra, dec, cache=None: n.append(1) or obj)
    for _ in range(2):
        r = client.get("/api/object", params={"ra": 5, "dec": 5})
        assert r.status_code == 200 and r.json() == obj
    assert len(n) == 1


def test_gaia_and_known_real(client, monkeypatch):
    _fake_module(monkeypatch, "gaia", cone=lambda ra, dec, r, cache=None: [
        {"id": 4472832130942575872, "ra": ra, "dec": dec, "pmra": 1.0, "pmdec": 2.0,
         "parallax": None, "g": np.float32(12.5)}])
    _fake_module(monkeypatch, "known", cone=lambda ra, dec, r, mjd, cache=None: [
        {"name": "(1) Ceres", "epoch_id": "x", "ra": ra, "dec": dec, "v": None, "source": "skybot"}])
    g = client.get("/api/gaia", params={"ra": 1, "dec": 2, "r": 5}).json()
    assert g == {"gaia": [{"id": "4472832130942575872", "ra": 1.0, "dec": 2.0, "pmra": 1.0,
                           "pmdec": 2.0, "parallax": None, "g": 12.5}]}
    k = client.get("/api/known", params={"ra": 1, "dec": 2, "r": 5, "mjd": 61000.5}).json()
    assert k == {"mjd": 61000.5, "known": [{"name": "(1) Ceres", "epoch_id": None, "ra": 1.0,
                                            "dec": 2.0, "v": None, "source": "skybot"}]}


def test_upstream_and_not_found_errors(client, monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectTimeout("irsa timed out")

    monkeypatch.setattr(blink, "build_frames", boom)
    _err(client.get("/api/frames", params={"ra": 1, "dec": 2}), 502, "upstream")

    def up(*a, **k):
        raise UpstreamError("IRSA is down")

    monkeypatch.setattr(blink, "build_object", up)
    _err(client.get("/api/object", params={"ra": 1, "dec": 2}), 502, "upstream")

    def nf(*a, **k):
        raise NotFoundError("no SPHEREx coverage here")

    monkeypatch.setattr(blink, "build_frames", nf)
    _err(client.get("/api/frames", params={"ra": 1, "dec": 3}), 404, "not_found")

    def bug(*a, **k):
        raise KeyError("oops")

    monkeypatch.setattr(blink, "build_frames", bug)
    _err(client.get("/api/frames", params={"ra": 1, "dec": 4}), 500, "internal")


def test_errors_are_not_cached(client, monkeypatch):
    n = []

    def flaky(*a, cache=None):
        n.append(1)
        if len(n) == 1:
            raise UpstreamError("first call fails")
        return _frames_result()

    monkeypatch.setattr(blink, "build_frames", flaky)
    assert client.get("/api/frames", params={"ra": 7, "dec": 7}).status_code == 502
    assert client.get("/api/frames", params={"ra": 7, "dec": 7}).status_code == 200


# ---------------------------------------------------------------- validation

@pytest.mark.parametrize("params", [
    {},                                               # ra, dec missing
    {"ra": 1},
    {"ra": "abc", "dec": 1},
    {"ra": -1, "dec": 0}, {"ra": 360.1, "dec": 0}, {"ra": 1, "dec": 90.5}, {"ra": 1, "dec": -91},
    {"ra": "nan", "dec": 0}, {"ra": 1, "dec": "inf"},
    {"ra": 1, "dec": 1, "clock": "days"},
    {"ra": 1, "dec": 1, "fov": 10}, {"ra": 1, "dec": 1, "fov": 20},
    {"ra": 1, "dec": 1, "clock": "hours", "fov": 5}, {"ra": 1, "dec": 1, "clock": "hours", "fov": 15},
    {"ra": 1, "dec": 1, "det": 0}, {"ra": 1, "dec": 1, "det": 7}, {"ra": 1, "dec": 1, "det": 2.5},
])
def test_frames_validation(client, params):
    _err(client.get("/api/frames", params=params), 400, "bad_request")


@pytest.mark.parametrize("path,params", [
    ("/api/object", {"ra": 1}), ("/api/object", {"ra": 400, "dec": 0}),
    ("/api/gaia", {"ra": 1, "dec": 1}), ("/api/gaia", {"ra": 1, "dec": 1, "r": 0}),
    ("/api/gaia", {"ra": 1, "dec": 1, "r": -2}), ("/api/gaia", {"ra": 1, "dec": 1, "r": 61}),
    ("/api/known", {"ra": 1, "dec": 1, "r": 5}), ("/api/known", {"ra": 1, "dec": 1, "r": 5, "mjd": 1}),
    ("/api/known", {"ra": 1, "dec": 1, "r": 100, "mjd": 61000}),
    ("/api/resolve", {}), ("/api/resolve", {"q": "400 0"}),
])
def test_other_validation(client, path, params):
    _err(client.get(path, params=params), 400, "bad_request")


@pytest.mark.parametrize("fov,clock", [(5, "months"), (15, "months"), (20, "hours"), (60, "hours"), ("15.0", "months")])
def test_frames_allowed_fov(client, monkeypatch, fov, clock):
    monkeypatch.setattr(blink, "build_frames", lambda *a, cache=None: _frames_result())
    assert client.get("/api/frames", params={"ra": 1, "dec": 1, "fov": fov, "clock": clock}).status_code == 200


def test_ra_360_and_poles_ok(client, monkeypatch):
    seen = []
    monkeypatch.setattr(blink, "build_object", lambda ra, dec, cache=None: seen.append((ra, dec)) or {})
    assert client.get("/api/object", params={"ra": 360, "dec": 90}).status_code == 200
    assert client.get("/api/object", params={"ra": 0, "dec": -90}).status_code == 200
    assert seen == [(0.0, 90.0), (0.0, -90.0)]


# ---------------------------------------------------------------- resolve, health, 404

def test_resolve_endpoint(client):
    r = client.get("/api/resolve", params={"q": "Barnard's Star"})
    assert r.status_code == 200
    fx = json.loads((FIX / "resolve_barnard.json").read_text())
    body = r.json()
    assert set(body) == {"q", "name", "ra", "dec", "slug", "source"}
    assert {k: body[k] for k in ("q", "name", "slug", "source")} == {k: fx[k] for k in ("q", "name", "slug", "source")}
    assert body["dec"] == pytest.approx(fx["dec"], abs=0.001)
    c = client.get("/api/resolve", params={"q": "08h55m10.831s -7d14m42.53s"}).json()
    assert c["source"] == "coords" and c["name"] is None and c["slug"] is None
    _err(client.get("/api/resolve", params={"q": "Proxima Centauri"}), 404, "not_found")


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["cache"] == "local"


def test_unknown_route_is_contract_404(client):
    _err(client.get("/api/nope"), 404, "not_found")
    _err(client.post("/api/frames"), 405, "bad_request")
