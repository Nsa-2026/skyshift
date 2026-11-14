from skyshift_server.cache import Cache


def test_local_cache_roundtrip(tmp_path):
    c = Cache(str(tmp_path))
    assert c.get("a/b.bin") is None
    c.put("a/b.bin", b"\x01\x02")
    assert c.get("a/b.bin") == b"\x01\x02"
