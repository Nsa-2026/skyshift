from skyshift_server.cache import Cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier


def test_local_cache_roundtrip(tmp_path):
    c = Cache(str(tmp_path))
    assert c.get("a/b.bin") is None
    c.put("a/b.bin", b"\x01\x02")
    assert c.get("a/b.bin") == b"\x01\x02"


def test_concurrent_writers_publish_complete_values(tmp_path, monkeypatch):
    cache = Cache(str(tmp_path))
    barrier = Barrier(2)
    replace = Path.replace

    def simultaneous_replace(path, target):
        barrier.wait(timeout=5)
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", simultaneous_replace)
    payloads = [b"a" * 100_000, b"b" * 100_000]
    with ThreadPoolExecutor(2) as workers:
        list(workers.map(lambda data: cache.put("same.bin", data), payloads))
    assert cache.get("same.bin") in payloads
    assert not list(tmp_path.glob("*.tmp"))
