"""Optional API keys (contracts/api.md v3.14): create, use, look up, revoke, limits. Offline: local stores."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skyshift_server import apikeys
from skyshift_server import app as app_mod
from skyshift_server.cache import Cache

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "api"


@pytest.fixture
def store(tmp_path):
    s = apikeys.KeyStore(str(tmp_path / "keys"))
    app_mod.reset_keys(s)
    app_mod.reset_cache(Cache(str(tmp_path / "cache")))
    yield s
    app_mod.reset_keys(None)
    app_mod.reset_cache(None)


@pytest.fixture
def client(store):
    return TestClient(app_mod.app, raise_server_exceptions=False)


def _new(client, **body):
    r = client.post("/api/keys", json=body) if body else client.post("/api/keys")
    assert r.status_code == 201, r.text
    return r.json()


def test_create_shape_matches_fixture(client, store, tmp_path):
    out = _new(client, label="my script")
    fixture = json.loads((FIX / "keys_create.json").read_text())
    assert set(out) == set(fixture) - {"synthetic", "note"}
    assert apikeys.KEY_RE.match(out["key"]) and apikeys.KEY_RE.match(fixture["key"])
    assert out["id"] == apikeys.digest(out["key"])[:12]
    assert out["label"] == "my script" and out["header"] == "X-API-Key"
    # only the hash is stored, never the key
    files = list((tmp_path / "keys").iterdir())
    assert [f.name for f in files] == [apikeys.digest(out["key"]) + ".json"]
    assert out["key"] not in files[0].read_text()


def test_create_without_body_or_label(client):
    assert _new(client)["label"] is None
    assert _new(client, label="   ")["label"] is None


@pytest.mark.parametrize("body", ['{"label": 5}', '{"label": "' + "x" * 61 + '"}', "[1]", "not json",
                                  '{"label": "a\\nb"}'])
def test_create_bad_body(client, body):
    r = client.post("/api/keys", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_anonymous_unchanged(client):
    assert client.get("/api/resolve", params={"q": "10 20"}).status_code == 200


def test_key_works_on_any_endpoint_and_me(client):
    k = _new(client, label="a")
    assert client.get("/api/resolve", params={"q": "10 20"}, headers={"X-API-Key": k["key"]}).status_code == 200
    me = client.get("/api/keys/me", headers={"Authorization": "Bearer " + k["key"]})
    assert me.status_code == 200
    assert me.json() == {"id": k["id"], "label": "a", "created": k["created"]}
    assert me.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("key", ["sks_" + "A" * 32, "nope", ""])
def test_unknown_key_is_401_not_anonymous(client, key):
    r = client.get("/api/resolve", params={"q": "10 20"}, headers={"X-API-Key": key})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_me_needs_a_key(client):
    assert client.get("/api/keys/me").status_code == 401
    assert client.delete("/api/keys/me").status_code == 401


def test_revoke(client):
    k = _new(client)
    h = {"X-API-Key": k["key"]}
    r = client.delete("/api/keys/me", headers=h)
    assert r.json() == {"id": k["id"], "revoked": True}
    assert client.get("/api/keys/me", headers=h).status_code == 401
    # a fresh instance (empty memory) also refuses it
    app_mod.reset_keys(apikeys.KeyStore(app_mod.get_keys().cache.root))
    assert client.get("/api/keys/me", headers=h).status_code == 401


def test_key_limit(client, monkeypatch):
    monkeypatch.setenv("SKYSHIFT_KEY_LIMIT", "2")
    h = {"X-API-Key": _new(client)["key"]}
    assert client.get("/api/keys/me", headers=h).status_code == 200
    assert client.get("/api/keys/me", headers=h).status_code == 200
    r = client.get("/api/keys/me", headers=h)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) > 0
    assert client.get("/api/resolve", params={"q": "10 20"}).status_code == 200  # anonymous: no limit


def test_anon_limit_only_when_set_and_key_skips_it(client, monkeypatch):
    monkeypatch.setenv("SKYSHIFT_ANON_LIMIT", "1")
    k = _new(client)["key"]  # creating a key is not limited by the anonymous limit
    fwd = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    assert client.get("/api/resolve", params={"q": "10 20"}, headers=fwd).status_code == 200
    assert client.get("/api/resolve", params={"q": "10 20"}, headers=fwd).status_code == 429
    other = {"X-Forwarded-For": "198.51.100.1"}
    assert client.get("/api/resolve", params={"q": "10 20"}, headers=other).status_code == 200
    assert client.get("/api/resolve", params={"q": "10 20"}, headers={**fwd, "X-API-Key": k}).status_code == 200
    assert client.get("/api/health", headers=fwd).status_code == 200  # health is never limited


def test_create_limit_per_address(client):
    h = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(apikeys.CREATE_PER_HOUR):
        assert client.post("/api/keys", headers=h).status_code == 201
    r = client.post("/api/keys", headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert client.post("/api/keys", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 201


def test_store_outage_lets_key_holder_through(client, monkeypatch):
    def boom(_key):
        raise ConnectionError("gcs down")

    monkeypatch.setattr(app_mod.get_keys(), "lookup", boom)
    r = client.get("/api/resolve", params={"q": "10 20"}, headers={"X-API-Key": "sks_" + "B" * 32})
    assert r.status_code == 200


def test_limiter_window():
    lim = apikeys.RateLimiter()
    assert lim.hit("a", 1, now=0.0) == 0
    assert lim.hit("a", 1, now=10.0) == 3591
    assert lim.hit("a", 1, now=3600.5) == 0
    assert lim.hit("b", None, now=0.0) == 0


def test_store_root_follows_cache_bucket(monkeypatch):
    monkeypatch.delenv("SKYSHIFT_KEYS", raising=False)
    monkeypatch.setenv("SKYSHIFT_CACHE", "gs://nasa-glazing-data/cache")
    assert apikeys.store_root() == "gs://nasa-glazing-data/apikeys"
    monkeypatch.setenv("SKYSHIFT_KEYS", "/tmp/k")
    assert apikeys.store_root() == "/tmp/k"
