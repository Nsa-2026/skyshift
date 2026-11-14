"""tools_reference: featured_targets, validation, resources and prompts (offline site from conftest)."""
import asyncio
import json
import re

import httpx
import pytest

from skyshift_mcp import server, tools_reference as ref
from skyshift_mcp.client import SkyShiftError

from conftest import PUBLIC, ROOT, make_offline, network

DATA = ROOT / "mcp" / "src" / "skyshift_mcp" / "data"


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- bundled copies stay in sync

@pytest.mark.parametrize("bundled, source", [
    ("targets.json", "web/src/data/targets.json"),
    ("contracts/api.md", "contracts/api.md"),
    ("contracts/flags.md", "contracts/flags.md"),
])
def test_bundled_copy_matches_repo(bundled, source):
    # A warning, not a failure: a contract or targets PR must not break mcp's CI. A checkout reads
    # the repo file anyway (_data_text); only an installed package uses the copy.
    if (DATA / bundled).read_bytes() != (ROOT / source).read_bytes():
        import warnings
        warnings.warn(f"mcp's copy of {source} is stale: cp {source} mcp/src/skyshift_mcp/data/{bundled}")


def test_checkout_reads_repo_file():
    assert ref._data_text("contracts", "api.md") == (ROOT / "contracts" / "api.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- featured_targets

def test_featured_targets_all(offline):
    out = json.loads(run(ref.featured_targets()))
    slugs = [t["slug"] for t in out["targets"]]
    assert out["count"] == len(slugs) == 8
    assert {"barnard", "epsindib", "wise0855", "rwaur", "rdra", "3i-atlas"} <= set(slugs)
    b = out["targets"][0]
    for k in ("slug", "name", "kind", "ra", "dec", "clock", "fov", "det", "look_for", "expected"):
        assert k in b
    assert b["page"].endswith("#/explore") and "t=barnard" in b["page"]
    assert b["static_data"].endswith("/data/barnard/index.json")


def test_featured_targets_filter(offline):
    out = json.loads(run(ref.featured_targets(target="atlas")))
    assert [t["slug"] for t in out["targets"]] == ["3i-atlas"]
    assert json.loads(run(ref.featured_targets(target="Barnard's Star")))["count"] == 1
    with pytest.raises(SkyShiftError, match="Barnard"):
        run(ref.featured_targets(target="Vega"))


def test_featured_targets_have_static_data():
    for t in ref.targets():
        assert (PUBLIC / "data" / t["slug"] / "index.json").is_file(), t["slug"]


# ---------------------------------------------------------------- validation

def test_validation_summary(offline):
    out = json.loads(run(ref.validation()))
    real = json.loads((PUBLIC / "data" / "validation.json").read_text())
    assert out["generated"] == real["generated"]
    ids = [c["id"] for c in out["checks"]]
    assert ids[:len(real["checks"])] == [c["id"] for c in real["checks"]]
    assert set(ref.CHECK_IDS) <= set(ids)
    for c in out["checks"]:
        assert c["status"] in ("pass", "partial", "fail", "pending")
        assert c["means"]
        assert "note" not in c and isinstance(c.get("rows", 0), int)
    assert sum(out["status_counts"].values()) == len(out["checks"])
    v2 = next(c for c in out["checks"] if c["id"] == "V2")
    assert v2["unit"] == "arcsec" and v2["headline"]


def test_validation_detail(offline):
    out = json.loads(run(ref.validation(check_id="v2")))["check"]
    assert out["id"] == "V2" and out["note"] and out["rows"]
    assert out["rows_total"] == len(out["rows"])
    v4 = json.loads(run(ref.validation(check_id="4", max_rows=3)))["check"]
    assert v4["id"] == "V4" and len(v4["rows"]) == 3 and v4["rows_total"] > 3
    with pytest.raises(SkyShiftError, match="V1"):
        run(ref.validation(check_id="V99"))


def test_validation_missing_check_is_pending(monkeypatch):
    real = json.loads((PUBLIC / "data" / "validation.json").read_text())
    real["checks"] = [c for c in real["checks"] if c["id"] != "V11"]

    def extra(request):
        if request.url.path == "/data/validation.json":
            return httpx.Response(200, json=real)
        return None

    make_offline(monkeypatch, extra)
    out = json.loads(run(ref.validation()))
    v11 = next(c for c in out["checks"] if c["id"] == "V11")
    assert v11["status"] == "pending" and v11["in_file"] is False
    detail = json.loads(run(ref.validation(check_id="V11")))["check"]
    assert detail["status"] == "pending"


# ---------------------------------------------------------------- resources and prompts

def test_facts_cover_the_basics():
    f = ref.facts()
    for s in ("D1 0.75–1.1", "D6 4.4–5.0", "6.15″", "7.53″", "60 days", "75 days",
              "D1+D4", "µJy", "grade", "Never call one a new planet"):
        assert s in f, s


PROMPT_ARGS = {
    "explain_candidate": {"candidate_id": "hunt-664250069368-87801613"},
    "triage_movers": {"limit": "3"},
    "check_my_target": {"target": "Barnard's Star"},
}
OPTIONAL_TOOLS = {"spectrum"}   # another work stream adds it; prompts mention it conditionally
CALL = re.compile(r"\b([a-z_]+)\(([^()]*)\)")


def _session_results():
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        async with create_connected_server_and_client_session(server.app._mcp_server) as s:
            tools = {t.name: t for t in (await s.list_tools()).tools}
            res = {str(r.uri): r for r in (await s.list_resources()).resources}
            prompts = {p.name: p for p in (await s.list_prompts()).prompts}
            texts = {}
            for name, args in PROMPT_ARGS.items():
                got = await s.get_prompt(name, args)
                texts[name] = "\n".join(m.content.text for m in got.messages)
            read = {u: (await s.read_resource(u)).contents[0].text for u in res}
            val = await s.call_tool("validation", {"check_id": "V1"})
            return tools, res, prompts, texts, read, val

    return run(go())


def test_protocol_lists_resources_prompts_and_tools(offline):
    tools, res, prompts, texts, read, val = _session_results()
    assert {"featured_targets", "validation"} <= set(tools)
    assert {"skyshift://facts", "skyshift://contracts/api", "skyshift://contracts/flags"} <= set(res)
    assert read["skyshift://contracts/api"] == (ROOT / "contracts" / "api.md").read_text()
    assert read["skyshift://contracts/flags"] == (ROOT / "contracts" / "flags.md").read_text()
    assert read["skyshift://facts"] == ref.FACTS
    assert set(PROMPT_ARGS) <= set(prompts)
    assert [a.name for a in prompts["explain_candidate"].arguments] == ["candidate_id"]
    assert not val.isError and json.loads(val.content[0].text)["check"]["id"] == "V1"
    assert "hunt-664250069368-87801613" in texts["explain_candidate"]
    assert 'limit=9' in texts["triage_movers"]
    assert "Barnard's Star" in texts["check_my_target"]


def test_prompts_name_only_real_tools_and_params(offline):
    tools, _, _, texts, _, _ = _session_results()
    for name, text in texts.items():
        calls = CALL.findall(text)
        assert calls, name
        for tool, args in calls:
            if tool in OPTIONAL_TOOLS:
                continue
            assert tool in tools, f"{name} names a tool that doesn't exist: {tool}"
            params = set(tools[tool].inputSchema.get("properties", {}))
            for p in re.findall(r"\b([a-z_]+)=", args):
                assert p in params, f"{name}: {tool}() has no parameter {p}"
        for word in re.findall(r"`([a-z_]+)`", text):   # a backticked word: a tool or a contract field
            assert word in tools or word in OPTIONAL_TOOLS or word in API_WORDS, \
                f"{name}: `{word}` is neither a tool nor a contracts/api.md field"
    assert "get_candidate" in texts["explain_candidate"] and "known_objects" in texts["explain_candidate"]
    assert "spectrum" in texts["explain_candidate"]
    assert "top_candidates" in texts["triage_movers"]
    for t in ("measure", "blink", "candidates_near", "validation"):
        assert t + "(" in texts["check_my_target"], t


API_WORDS = set(re.findall(r"\b[a-z_]+\b", (ROOT / "contracts" / "api.md").read_text()))


@network
def test_live_validation():
    server._client = None

    async def go():   # one event loop: the live client's connections belong to it
        return await ref.validation(), await ref.validation(check_id="V2")

    summary, v2 = map(json.loads, run(go()))
    assert summary["checks"] and {c["id"] for c in summary["checks"]} >= set(ref.CHECK_IDS)
    assert v2["check"]["rows"]
