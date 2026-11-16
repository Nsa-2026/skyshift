"""The API runner's tool list must stay in step with vet.py's command line. Offline: no API call, no data."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import api_round as A  # noqa: E402
import vet  # noqa: E402

SAMPLE = {"id": "hunt-1-2", "zone": "hunt", "ra": 126.5, "dec": -1.25, "mjd": 61000.5, "radius": 30, "pm": "-100,50",
          "det": [2, 5], "n": 4, "mark": ["126.5,-1.25", "126.6,-1.2"], "max": 6, "which": 2, "days": 2.0, "x": 10.5, "y": 20.0}


def test_every_tool_and_argument_parses():
    sub = vet.build_parser()._subparsers._group_actions[0].choices
    assert set(A.TOOLS) == set(sub), "a tool was added to vet.py or api_round.py only"
    for name, (_, props, required) in A.TOOLS.items():
        assert set(required) <= set(props)
        argv = A.argv_for(name, {k: SAMPLE[k] for k in props}, "/tmp/x")
        a = vet.build_parser().parse_args(argv)
        assert a.tool == name
        if "dec" in props:
            assert a.dec == -1.25  # a negative Dec must survive
        if name == "stamps":
            assert a.det == [2, 5] and len(a.mark) == 2
        cli = {x.dest for x in sub[name]._actions if x.dest not in ("help", "out", "blind")}
        assert cli == set(props), f"{name}: command line {sorted(cli)} against tool {sorted(props)}"


def test_bad_arguments_are_refused():
    for inp in ({"zone": "hunt", "ra": 1.0}, {"zone": "hunt", "ra": 1.0, "dec": 2.0, "colour": "red"}):
        try:
            A.argv_for("dots", inp, None)
        except ValueError:
            continue
        raise AssertionError(inp)


def test_record_schema_is_strict_and_prompt_has_the_skill():
    def walk(s):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if s.get("type") == "array":
            walk(s["items"])
    walk(A.RECORD_SCHEMA)
    p = A.system_prompt()
    assert "What a careful astronomer rules out" in p and not p.startswith("---")
    assert A.cost_usd("claude-haiku-5-5", {"input": 1e6, "output": 1e6, "cache_read": 0, "cache_write": 0}) == 0.6


def test_batches_group_by_kind_and_zone_and_reverse_keeps_the_groups():
    todo = [{"id": f"{z}-{k}-{i}", "kind": k} for i in range(3) for z in ("hunt", "ecl05") for k in ("mover", "spectral")]
    one = A.batches(todo, 1)
    assert [len(g) for g in one] == [1] * 12 and [g[0] for g in one] == todo
    fwd, rev = A.batches(todo, 3), A.batches(todo, 3, reverse=True)
    assert [len(g) for g in fwd] == [3, 3, 3, 3]
    for g, r in zip(fwd, rev):
        assert len({(f["kind"], f["id"].split("-")[0]) for f in g}) == 1  # one kind and zone per agent here
        assert r == g[::-1]


def test_submit_gate_catches_a_verdict_against_its_own_checks():
    chk = [{"conclusion": "supports: x"}] * 3
    assert A.contradiction({"verdict": "static_source", "checks": chk}, "high_pm")
    assert A.contradiction({"verdict": "real_other", "checks": chk}, "high_pm") is None
    assert A.contradiction({"verdict": "static_source", "checks": []}, "spectral")          # property claims are never static_source
    assert A.contradiction({"verdict": "real_mover_unknown", "checks": []}, "high_pm")     # mover verdicts are for movers
    assert A.contradiction({"verdict": "real_mover_unknown", "checks": chk}, "mover", ["info", "packet"])  # session and stamps first
    assert A.contradiction({"verdict": "real_mover_unknown", "checks": chk}, "mover", ["packet", "session", "stamps"]) is None
    assert A.contradiction({"verdict": "artifact", "checks": [], "junk_proof": "one_pixel_spike_no_psf"}, "mover", ["packet"]) is None
    assert A.contradiction({"verdict": "artifact", "checks": [], "junk_proof": "none"}, "mover", ["packet"])  # a drop needs proof
    assert A.contradiction({"verdict": "static_source", "checks": []}, "mover", ["packet"])


def test_mark_pairs_are_rebuilt_from_a_flat_string():
    argv = A.argv_for("stamps", {"zone": "hunt", "ra": 1.0, "dec": 2.0, "mark": "148.1,14.9,148.2,14.8"}, "/tmp/x")
    assert argv.count("--mark=148.1,14.9") == 1 and argv.count("--mark=148.2,14.8") == 1


def test_tool_cache_key_ignores_the_output_folder_and_follows_the_blind_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("SKYSHIFT_VET_HIDE_KNOWN", raising=False)
    A._tools_version.cache_clear()
    a = A._cache_path(["packet", "x", "--out=/a/b"]); b = A._cache_path(["packet", "x", "--out=/c/d"])
    assert a == b and a != A._cache_path(["packet", "y", "--out=/a/b"])
    monkeypatch.setenv("SKYSHIFT_VET_HIDE_KNOWN", "1")
    A._tools_version.cache_clear()  # read once per run
    assert A._cache_path(["packet", "x", "--out=/a/b"]) != a
    monkeypatch.setattr(A, "CACHE_DIR", None)
    assert A._cache_path(["packet", "x"]) is None


def test_route_tiers():
    import route
    hot = ["HOT PIXEL: detection #1 sits on ...", "NOT STAR-LIKE: ..."]
    assert route.tier(0.01, hot, 2, [4], "two_exposures", 40.0) == "easy junk"
    assert route.tier(0.01, [], 2, [4], "two_exposures", 40.0) == "hard"                 # low score, nothing explains it
    assert route.tier(0.99, [], 4, [1, 4], "two_exposures", 30.0) == "easy real"
    assert route.tier(0.99, ["FIXED SOURCE 8.8 arcsec away ..."], 4, [1, 4], "two_exposures", 30.0) == "hard"   # a mover beside a star
    assert route.tier(0.99, [], 4, [5], "two_exposures", 30.0) == "hard"                 # one long-wave detector
    assert route.tier(0.99, [], 4, [1, 4], "two_exposures", 3.0) == "hard"               # slow body
    assert route.tier(0.99, [], 1, [3, 6], "two_detectors", float("nan")) == "hard"
    assert route.tier(None, [], 4, [1, 4], "two_exposures", 30.0) == "hard" and route.tier(float("nan"), hot, 4, [1], "x", 30.0) == "hard"
    assert route.tier(0.5, hot, 4, [1, 4], "two_exposures", 30.0) == "hard"              # score and warnings disagree


def test_publish_checks_rules():
    import publish_checks as P
    real = {"verdict": "real_other", "confidence": 0.85, "interest": "medium", "what_it_is": "A star.", "decisive": "Gaia."}
    fake = {"verdict": "artifact", "confidence": 0.9, "junk_proof": "hot_pixel_seen_at_other_sky_positions", "what_it_is": "Hot.", "decisive": "Pixel."}
    noproof = dict(fake, junk_proof="none")
    assert P.review("high_pm", [("a", real), ("b", real)])["level"] == "ok"
    c = P.review("mover", [("a", fake), ("b", dict(fake, junk_proof=None))])
    assert c["code"] == "ai_fake_proven" and "hot pixel" in c["text"] and c["look"]          # two sure drops, a named proof
    assert P.review("mover", [("a", noproof), ("b", fake)])["code"] == "ai_review"         # the latest names no proof: not proven
    assert P.review("mover", [("a", fake), ("b", dict(fake, confidence=0.6))])["level"] == "strong"  # one sure drop only
    assert P.review("mover", [("a", fake), ("b", fake), ("c", {"verdict": "real_mover_unknown"})])["level"] == "warn"  # a checker keeps it
    wrong = {"verdict": "engine_claim_wrong", "confidence": 0.9}
    assert P.review("high_pm", [("a", wrong), ("b", wrong)])["level"] == "warn"            # a moving star with a wrong value is no fake
    assert P.review("spectral", [("a", wrong), ("b", wrong)])["code"] == "ai_fake_proven"
    assert P.catalogued({"x": {"checks": [{"code": "catalogues_checked", "level": "strong", "text": "CatWISE lists it."}]}}, "x")
