"""Run a vetting round through the Claude API: one finding per conversation.

Run from pipeline/ with the key in the environment (never in the repo):
  uv run --quiet --with anthropic python ../scripts/vet/api_round.py <round dir> --model claude-haiku-5-5 --effort high
      [--name NAME] [--limit N] [--ids ID ...] [--workers 6] [--max-rounds 40] [--per-agent 1] [--reverse]

The tools run on this machine: --tool-workers N keeps N copies of vet.py loaded (a call then costs what the tool
itself needs, not 5-40 s of start-up) and caps how many tools run at once. Finished tool calls are cached on disk
and reused by later runs with the same tools and data, so repeating a round costs almost no local work.

--routes routes.json (from route.py) sends the findings marked hard to --hard-model at --hard-effort and the rest
to --model at --effort; an "easy real" that the easy checker drops is checked again by the hard one.

One finding per conversation by default. --per-agent N hands N findings (same kind and zone together) to one
conversation, one after another; --reverse gives the same groups in reverse order, to measure what the earlier
findings in a conversation do to the later ones.

The model gets the vet-finding skill as its system prompt and the vet.py tools as tool calls (same names and
arguments as the command line; pictures come back inside the tool result). It ends by calling submit_record
with the check record of the skill's section 5.

Writes <round>/api/<name>/:
  rec/<id>.json          one check record per finding (a rerun skips the ids that are already there)
  records.json           all of them (round_report.py --run <name> and compare_runs.py read this)
  transcripts/<id>.json  the whole conversation (batch_<n>.json with --per-agent): what the model saw, thought (summary), called and answered
  img/<id>/...           the pictures it was shown
  usage.json             tokens, cost, tool calls and seconds per finding, and the totals

No fallback model is set: a refusal is recorded as a failure, so that every record is the named model's own.
"""
from __future__ import annotations

import argparse
import base64
import functools
import glob
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SKILL = os.path.join(REPO, ".claude", "skills", "vet-finding", "SKILL.md")
VET = os.path.join(HERE, "vet.py")
PIPELINE = os.path.join(REPO, "pipeline")

# $ per million tokens: input, output, cache read, cache write (5 minutes). Checked 16 Nov 2026.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

API_NOTES = """# How to work in this session

You have no shell and no file system. Every tool in the table of section 1 is a function you call with the
same name and the same arguments (`--zone Z` becomes `zone`, `<id>` becomes `id`). There is no `--out`: a
tool that draws a picture returns the picture with its text, and you look at it there. Give Dec as a plain
number. You may call several tools in one step when they do not depend on each other.

You are given one finding at a time. Vet it as the skill says, then call `submit_record` once with its check
record of section 5. Nothing you write outside `submit_record` is kept (the list of tools you used is added for you). Section 6 does not apply here; instead put what
the tools could not do, or showed in a confusing or wrong way, in the record's `harness_feedback`.

"""

_ID = {"id": {"type": "string", "description": "The finding's id, exactly as given."}}
_POS = {
    "zone": {"type": "string", "description": "Zone name, e.g. hunt or ecl17 (the first part of the id)."},
    "ra": {"type": "number", "description": "Right ascension in degrees."},
    "dec": {"type": "number", "description": "Declination in degrees (negative south)."},
}
_MJD = {"type": "number", "description": "Time as MJD."}

# name -> (description, properties, required). Kept in step with vet.py's parser by tests/test_api_round.py.
TOOLS = {
    "info": ("The engine's rows for the finding, the position to use, sky context (ecliptic, Sun elongation, ecliptic "
             "direction), visit dates. Fast stars: the Gaia offset test. Always call this first.", {**_ID}, ["id"]),
    "packet": ("Movers only. A 3-row picture (detections / detection minus other times / same spot at other times), every "
               "engine number per detection with its wavelength, a table of every exposure of the session with the S/N at "
               "the track, the numeric rule, automatic warnings, catalogue and known-object lines.", {**_ID}, ["id"]),
    "session": ("Movers only. The pictures behind the packet's session table: every exposure of the session over the spot, "
                "all detectors.", {**_ID, "max": {"type": "integer", "description": "Most rows to draw (default 18)."}}, ["id"]),
    "stamps": ("Pictures of any position. Without mjd: one median stack per visit and last-minus-first. With mjd: the n "
               "single exposures nearest to that time.",
               {**_POS, "det": {"type": "array", "items": {"type": "integer"}, "description": "Detectors 1-6 to show (default: a few)."},
                "mjd": _MJD, "n": {"type": "integer", "description": "Number of single exposures with mjd (default 6)."},
                "mark": {"type": "array", "items": {"type": "string"}, "description": "Extra positions to mark, each 'RA,DEC' in degrees."}},
               ["zone", "ra", "dec"]),
    "catalog": ("Gaia DR3 (and 2MASS, CatWISE where the zone has them), AllWISE with its 2MASS match, our fixed sources and "
                "bright stars within 10 arcmin around a position. It says which catalogues it could read. With pm: where the "
                "star was in 2016, 2010 and 1999, the nearest catalogue source to each spot, and a control spot.",
                {**_POS, "radius": {"type": "number", "description": "Search radius in arcsec (default 30)."}, "mjd": _MJD,
                 "pm": {"type": "string", "description": "Claimed proper motion 'pmra,pmdec' in mas/yr."}},
                ["zone", "ra", "dec"]),
    "known": ("Known asteroids, comets, planets and moons predicted near a position at a time (mover zones only).",
              {**_POS, "mjd": _MJD, "radius": {"type": "number", "description": "Search radius in arcsec (default 600)."}},
              ["zone", "ra", "dec", "mjd"]),
    "spectrum": ("The source's 102-channel spectrum: plot, table, band ratios with errors, channels that stand out, synthetic "
                 "J/H/K/W1/W2 and colours.",
                 {**_ID, "which": {"type": "integer", "description": "1 or 2: which source of a two-source id (fast-star pairs)."}}, ["id"]),
    "dots": ("Our detections near a position that belong to no fixed source, at any time; flags a shared detector column.",
             {**_POS, "radius": {"type": "number", "description": "Search radius in arcsec (default 20)."}}, ["zone", "ra", "dec"]),
    "pixel": ("History of a detector pixel and its column over the whole zone: hot pixels, glows, bad columns.",
              {"zone": _POS["zone"], "det": {"type": "integer", "description": "Detector 1-6."},
               "x": {"type": "number", "description": "Detector x in pixels."}, "y": {"type": "number", "description": "Detector y in pixels."}},
              ["zone", "det", "x", "y"]),
    "track": ("Movers only. Where the fitted track puts it in the other sessions, whether a photo holds that spot, what we "
              "have there.", {**_ID, "days": {"type": "number", "description": "How far in time to follow it (default 3)."},
                              "max": {"type": "integer", "description": "Most sessions to list (default 12)."}}, ["id"]),
    "movers": ("Our movers of that session near a position.",
               {**_POS, "mjd": _MJD, "radius": {"type": "number", "description": "Search radius in arcsec (default 120)."}},
               ["zone", "ra", "dec", "mjd"]),
}
DRAWS = {"packet", "session", "stamps", "spectrum"}  # these take --out and return pictures

DROP_VERDICTS = ("static_source", "artifact", "no_source", "engine_claim_wrong")
VERDICTS = ["real_mover_known", "real_mover_unknown", "static_source", "artifact", "no_source", "real_known", "real_other",
            "engine_claim_wrong", "unsure"]
_S = {"type": "string"}
# what counts as proof that a mover is junk (the skill's section 4); doubt alone is not on this list
PROOFS = ["none", "hot_pixel_seen_at_other_sky_positions", "same_detector_pixel_without_telescope_step", "same_brightness_at_same_spot_at_other_times",
          "trail_across_the_stamp", "one_pixel_spike_no_psf", "glow_or_ghost_tied_to_a_bright_star_or_detector_column", "nothing_at_the_position",
          "not_applicable"]
RECORD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": VERDICTS},
        "confidence": {"type": "number", "description": "0 to 1."},
        "interest": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "what_it_is": {"type": "string", "description": "One sentence a non-specialist can read."},
        "checks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"check": _S, "tool": _S, "saw": {"type": "string", "description": "What the tool showed, with numbers."},
                           "conclusion": {"type": "string", "description": "supports | against | neutral: what it means for the claim."}},
            "required": ["check", "tool", "saw", "conclusion"]}},
        "could_not_check": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"what": _S, "why": _S, "would_need": _S}, "required": ["what", "why", "would_need"]}},
        "engine_problems": {"type": "array", "items": _S},
        "decisive": {"type": "string", "description": "The one check that settled it."},
        "junk_proof": {"type": "string", "enum": PROOFS,
                       "description": "Movers only: the measured proof that it is NOT a real mover (section 4 of the skill). 'none' when "
                                      "you have doubts but no proof (then the verdict is unsure or real_*); 'not_applicable' for other kinds."},
        "harness_feedback": {"type": "string", "description": "Tool output that was missing, confusing or wrong; checks the tools cannot do. Empty if none."},
    },
    "required": ["verdict", "confidence", "interest", "what_it_is", "checks", "could_not_check", "engine_problems", "decisive",
                 "junk_proof", "harness_feedback"],
}


def tool_defs() -> list[dict]:
    defs = [{"name": n, "description": d, "input_schema": {"type": "object", "properties": p, "required": r}}
            for n, (d, p, r) in TOOLS.items()]
    defs.append({"name": "submit_record", "strict": True, "input_schema": RECORD_SCHEMA,
                 "description": "Submit the check record for this finding (section 5 of the skill). Call it once, when you are done."})
    return defs


BLIND_NOTE = """In this run the list of known asteroids, comets, planets and moons is switched off, and so are the engine's
grade and the site's row: `known` answers NOT AVAILABLE and that is expected. Decide from the images, the numbers
and the catalogues whether the finding is a real moving object. A real mover gets `real_mover_unknown` here; do
not hold the missing known-body check against it.

"""


def system_prompt(hide_known: bool = False) -> str:
    text = open(SKILL).read()
    if text.startswith("---"):
        text = text.split("---", 2)[2].lstrip()
    return API_NOTES + (BLIND_NOTE if hide_known else "") + text


def argv_for(tool: str, inp: dict, out_dir: str | None) -> list[str]:
    """The vet.py command line for one tool call. Raises ValueError on a missing or unknown argument."""
    _, props, required = TOOLS[tool]
    missing = [k for k in required if inp.get(k) is None]
    unknown = [k for k in inp if k not in props]
    if missing or unknown:
        raise ValueError(f"missing arguments {missing}, unknown arguments {unknown}; this tool takes {sorted(props)}")
    argv = [tool]
    if "id" in props:
        argv.append(str(inp["id"]))
    for k, v in inp.items():
        if k == "id" or v is None:
            continue
        items = v if isinstance(v, list) else [v]
        if k == "mark":  # 'RA,DEC' pairs; models also send one flat string or a flat list of numbers
            nums = [x.strip() for it in items for x in str(it).replace(";", ",").split(",") if x.strip()]
            if len(nums) % 2:
                raise ValueError("mark needs pairs 'RA,DEC' in degrees; an odd number of values was given")
            items = [f"{nums[i]},{nums[i + 1]}" for i in range(0, len(nums), 2)]
        for item in items:
            argv.append(f"--{k}={item}")
    if tool in DRAWS:
        argv.append(f"--out={out_dir}")
    return argv


MAX_TEXT = 60000
SERVER = os.path.join(HERE, "toolserver.py")


class ToolPool:
    """Workers that keep vet.py loaded (toolserver.py). A call takes a free worker, so at most `n` tools run at once.
    Starting Python and the libraries for every call cost 5-40 s each; a loaded worker answers in about the time the
    tool itself needs. A worker that hangs or dies is replaced and the call gets a TOOL ERROR."""

    def __init__(self, n: int, log_path: str | None = None):
        import queue
        self.idle: "queue.Queue" = queue.Queue()
        self.log = open(log_path, "a") if log_path else subprocess.DEVNULL
        self.all: list = []
        self.lock = threading.Lock()
        threads = [threading.Thread(target=self._add) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _spawn(self):
        p = subprocess.Popen([sys.executable, SERVER], cwd=PIPELINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                             text=True, bufsize=1)
        if not json.loads(p.stdout.readline() or "{}").get("ready"):
            raise RuntimeError("a tool worker did not start; see the tools log")
        with self.lock:
            self.all.append(p)
        return p

    def _add(self):
        self.idle.put(self._spawn())

    def call(self, argv: list[str], timeout: float = 420.0) -> tuple[str, str, int]:
        """(output, error stream, exit code); exit code 3 = the worker hung or died."""
        p = self.idle.get()
        box: dict = {}

        def talk():
            try:
                p.stdin.write(json.dumps({"argv": argv}) + "\n"); p.stdin.flush()
                box["line"] = p.stdout.readline()
            except Exception as e:  # noqa: BLE001
                box["err"] = repr(e)

        t = threading.Thread(target=talk, daemon=True)
        t.start(); t.join(timeout)
        if t.is_alive() or not box.get("line"):
            p.kill()
            try:
                self._add()
            except Exception:  # noqa: BLE001
                pass
            why = f"did not finish in {timeout / 60:.0f} minutes" if t.is_alive() else "stopped unexpectedly"
            return "", f"the tool worker {why}", 3
        self.idle.put(p)
        r = json.loads(box["line"])
        return r["out"], r["err"], int(r["code"])

    def close(self):
        with self.lock:
            for p in self.all:
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass


POOL: ToolPool | None = None  # set by main(); None = start vet.py for every call (slow, no state)
CACHE_DIR: str | None = None  # set by main(): finished tool calls are kept here and reused by later runs


@functools.lru_cache(maxsize=1)
def _tools_version() -> str:
    """Changes whenever the tools, the data folder or the blind switch change, so a cached answer is never stale.
    Read once per run: the loaded workers keep the tools they started with, even if the files change meanwhile."""
    import hashlib
    h = hashlib.sha1()
    for f in ("vet.py", "vetlib.py"):
        h.update(open(os.path.join(HERE, f), "rb").read())
    h.update(os.environ.get("SKYSHIFT_VET_DATA", "").encode() + os.environ.get("SKYSHIFT_VET_HIDE_KNOWN", "").encode())
    eng = os.environ.get("SKYSHIFT_VET_ENGINE", "~/skyshift-data")  # the engine's check files and track tables: a new file is new data
    if eng.strip().lower() not in ("off", ""):
        import glob
        root = os.path.expanduser(eng)
        for f in sorted(glob.glob(os.path.join(root, "checks", "*.json")) + glob.glob(os.path.join(root, "vet", "*.json"))
                        + glob.glob(os.path.join(root, "results", "*", "tracklet*.parquet"))):
            st = os.stat(f)
            h.update(f"{f}:{st.st_size}:{int(st.st_mtime)}".encode())
    else:
        h.update(b"engine-off")
    return h.hexdigest()[:12]


def _cache_path(argv: list[str]) -> str | None:
    if CACHE_DIR is None:
        return None
    import hashlib
    key = [x for x in argv if not x.startswith("--out=")]
    return os.path.join(CACHE_DIR, _tools_version(), hashlib.sha1(json.dumps(key).encode()).hexdigest()[:20])


def run_tool(tool: str, inp: dict, out_dir: str) -> tuple[str, list[str], bool]:
    """Run one tool. Returns (text, pictures, is_error)."""
    if tool not in TOOLS:
        return f"TOOL ERROR: there is no tool named {tool}.", [], True
    try:
        argv = argv_for(tool, inp or {}, out_dir)
    except ValueError as e:
        return f"TOOL ERROR: {e}", [], True
    os.makedirs(out_dir, exist_ok=True)
    cp = _cache_path(argv)
    if cp and os.path.exists(os.path.join(cp, "done.json")):  # the same call was answered before: same tools, same data
        import shutil
        d = json.load(open(os.path.join(cp, "done.json")))
        for f in d["pics"]:
            shutil.copy(os.path.join(cp, f), os.path.join(out_dir, f))
        return d["text"], [os.path.join(out_dir, f) for f in d["pics"]], d["code"] != 0
    if POOL is not None:
        out, err, code = POOL.call(argv)
    else:
        try:
            p = subprocess.run([sys.executable, VET, *argv], cwd=PIPELINE, capture_output=True, text=True, timeout=420)
            out, err, code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired:
            out, err, code = "", "the tool did not finish in 7 minutes", 3
    text = out.strip()
    if code not in (0, 1, 2) or (code != 0 and not text):
        text += f"\nTOOL ERROR (exit {code}): {err.strip()[-600:]}. Record this under could_not_check."
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n[output cut here: {len(text) - MAX_TEXT} more characters were not shown]"
    pics = sorted(glob.glob(os.path.join(out_dir, "*.png")))
    for pth in pics:  # the model has no file system: a path means nothing to it
        text = text.replace(pth, os.path.basename(pth) + " (shown below)")
    text = text or "(the tool printed nothing)"
    if cp and code in (0, 1):  # answers and "not available" are facts about the data; errors and timeouts are not kept
        import shutil
        tmp = cp + f".tmp{os.getpid()}_{threading.get_ident()}"
        os.makedirs(tmp, exist_ok=True)
        for pth in pics:
            shutil.copy(pth, os.path.join(tmp, os.path.basename(pth)))
        json.dump({"text": text, "pics": [os.path.basename(x) for x in pics], "code": code, "argv": argv}, open(os.path.join(tmp, "done.json"), "w"))
        try:
            os.rename(tmp, cp)
        except OSError:  # another conversation stored the same call first
            shutil.rmtree(tmp, ignore_errors=True)
    return text, pics, code != 0


def result_block(tool_use_id: str, text: str, pics: list[str], is_error: bool) -> dict:
    content = [{"type": "text", "text": text}]
    for pth in pics:
        with open(pth, "rb") as f:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                        "data": base64.standard_b64encode(f.read()).decode()}})
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}


def cost_usd(model: str, u: dict) -> float:
    pin, pout, pread, pwrite = PRICES.get(model, (0, 0, 0, 0))
    return (u["input"] * pin + u["output"] * pout + u["cache_read"] * pread + u["cache_write"] * pwrite) / 1e6


def _plain(messages: list) -> list:
    """The conversation as plain JSON, pictures replaced by a marker."""
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c}); continue
        blocks = []
        for b in c:
            b = b if isinstance(b, dict) else b.to_dict()
            if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                b = {**b, "content": [x if x.get("type") != "image" else {"type": "image", "data": "(picture, see img/)"} for x in b["content"]]}
            b.pop("signature", None)
            blocks.append(b)
        out.append({"role": m["role"], "content": blocks})
    return out


def contradiction(rec: dict, kind: str, tools: list[str] = (), on_track: int = 0) -> str | None:
    """A record whose verdict goes against its own checks (round 2: a 'static_source' whose text said 'motion confirmed')."""
    v, checks = rec.get("verdict"), rec.get("checks") or []
    sup = sum(str(c.get("conclusion", "")).lower().startswith("support") for c in checks)
    agn = sum(str(c.get("conclusion", "")).lower().startswith("against") for c in checks)
    drop = v in ("static_source", "artifact", "no_source", "engine_claim_wrong")
    keep = v in ("real_mover_known", "real_mover_unknown", "real_known", "real_other")
    why = None
    if kind == "mover" and drop and rec.get("junk_proof") in (None, "none", "not_applicable"):
        return ("Not recorded yet: a mover is dropped only on proof (section 4 of the skill), and junk_proof says there is none. "
                "Doubt alone (motion not significant, one detector, seen once, near a star, stationary, odd shape) is not proof: "
                "use unsure, or name the measured proof in junk_proof, then submit again.")
    if kind == "mover" and drop and on_track >= 2:
        return (f"Not recorded yet: the engine has this mover on a track through {on_track} sessions (`info`), and its shuffled control makes no "
                "such track. A fixed star or an artifact does not line up across independent sessions: look at one or two of the listed sessions "
                "with `stamps`, then submit. If you looked and the other sessions hold nothing real, say so in `decisive` and submit unchanged.")
    if kind == "mover" and v in ("real_mover_unknown", "unsure") and not ({"session", "stamps"} <= set(tools)):
        return ("Not recorded yet: a mover gets real_mover_unknown or unsure only after you have looked at `session` (every exposure, all "
                "detectors) and `stamps` (the same spot in the other visits), and run the check each packet warning names (section 3). "
                "Run what is missing, then submit.")
    if drop and sup >= 3 and agn == 0:
        why = f"the verdict is {v}, but all {sup} of your check conclusions say 'supports' and none says 'against'"
    elif keep and agn >= 3 and sup == 0:
        why = f"the verdict is {v}, but all {agn} of your check conclusions say 'against' and none says 'supports'"
    elif v in ("real_mover_known", "real_mover_unknown") and kind != "mover":
        why = f"{v} is for findings of kind mover; this one is {kind}"
    elif v == "static_source" and kind in ("spectral", "shape"):
        why = f"static_source is for motion claims; a {kind} finding whose claim is false is engine_claim_wrong, and one whose claim holds is real_known or real_other"
    return None if why is None else (f"Not recorded yet: {why}. 'supports' and 'against' refer to the engine's claim. Check the verdict against "
                                     f"section 4 of the skill and submit again; if the record is what you mean, submit it unchanged.")


def sessions_on_track(f: dict) -> int:
    """Sessions on the engine's track that holds this mover (0: none, or no track table)."""
    if f.get("kind") != "mover":
        return 0
    try:
        import vetlib
        tr = vetlib.tracklet_of(f["id"])
        return int(tr["row"].n_sessions) if tr else 0
    except Exception:  # noqa: BLE001  (the gate is an extra: a missing table must not stop a round)
        return 0


def vet_batch(client, findings: list[dict], a, run_dir: str, batch_no: int = 0) -> list[dict]:
    """One conversation for one or several findings, handed over one after another.

    Returns one record per finding (with _usage); a finding without a record has verdict None and an error.
    """
    import anthropic

    n = len(findings)

    def ask(k: int) -> str:
        f = findings[k]
        if n == 1:
            return f"Finding to vet: id `{f['id']}`, kind `{f['kind']}`. Vet it and submit the check record."
        return f"Finding {k + 1} of {n}: id `{f['id']}`, kind `{f['kind']}`. Vet it and submit its check record."

    def fresh() -> dict:
        return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "requests": 0, "tool_calls": 0, "pictures": 0, "t0": time.time()}

    model, effort = a.plan.get(findings[0]["id"], (a.model, a.effort)) if n == 1 else (a.model, a.effort)
    intro = "" if n == 1 else (f"You will get {n} findings, one after another. Judge each on its own evidence and submit its record; "
                               f"the next one arrives after that.\n\n")
    messages = [{"role": "user", "content": intro + ask(0)}]
    out: list[dict] = []
    k, usage, tools_used, nudges, error, asked_again = 0, fresh(), [], 0, None, False

    def close(record, err):
        """Store finding k's record (or its failure) and reset the per-finding counters."""
        nonlocal k, usage, tools_used, nudges
        f = findings[k]
        usage["seconds"] = round(time.time() - usage.pop("t0"), 1)
        usage["usd"] = round(cost_usd(model, usage), 5)
        rec = {"id": f["id"], "kind": f["kind"], **(record or {"verdict": None, "error": err}), "tools_used": sorted(set(tools_used)),
               "_tool_calls": tools_used, "_usage": usage, "_model": model, "_effort": effort, "_tier": a.routes.get(f["id"]),
               "_batch": batch_no, "_position": k + 1, "_per_agent": n}
        if record is not None:
            json.dump(rec, open(os.path.join(run_dir, "rec", f"{f['id']}.json"), "w"), indent=1)
        out.append(rec)
        a.report(rec)
        k, usage, tools_used, nudges = k + 1, fresh(), [], 0

    while k < n:
        try:
            resp = client.messages.create(
                model=model, max_tokens=16000,
                system=[{"type": "text", "text": a.system, "cache_control": {"type": "ephemeral"}}],
                tools=a.tools, messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": effort},
                cache_control={"type": "ephemeral"},
            )
        except anthropic.RateLimitError as e:
            time.sleep(min(90, int(e.response.headers.get("retry-after", "30")) + 5)); continue
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                time.sleep(20); continue
            error = f"API error {e.status_code}: {e.message}"; break
        except anthropic.APIConnectionError:
            time.sleep(20); continue
        u = resp.usage
        usage["input"] += u.input_tokens; usage["output"] += u.output_tokens; usage["requests"] += 1
        usage["cache_read"] += u.cache_read_input_tokens or 0; usage["cache_write"] += u.cache_creation_input_tokens or 0
        if resp.stop_reason == "refusal":
            error = "the model refused"; break
        if resp.stop_reason == "max_tokens":
            error = "the answer was cut off at max_tokens"; break
        messages.append({"role": "assistant", "content": resp.content})
        calls = [b for b in resp.content if b.type == "tool_use"]
        sub = next((b for b in calls if b.name == "submit_record"), None)
        if sub is not None:
            doubt = None if asked_again else contradiction(dict(sub.input), findings[k]["kind"], tools_used, sessions_on_track(findings[k]))
            if doubt:  # ask once; the same record submitted again is accepted
                asked_again = True
                messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "is_error": c is sub,
                                                             "content": doubt if c is sub else "Not run."} for c in calls]})
                continue
            asked_again = False
            close(dict(sub.input), None)
            if k < n:  # answer every call of this step, then hand over the next finding
                results = [{"type": "tool_result", "tool_use_id": c.id,
                            "content": "Recorded." if c is sub else "Not run: the record was already submitted."} for c in calls]
                messages.append({"role": "user", "content": results + [{"type": "text", "text": ask(k)}]})
            continue
        if not calls:
            nudges += 1
            if nudges > 2:
                error = "the model stopped without submitting a record"; break
            messages.append({"role": "user", "content": "Nothing is kept unless you call submit_record. Finish the checks you still need, then call it."})
            continue
        fid = findings[k]["id"]
        results = []
        with ThreadPoolExecutor(4) as ex:
            futs = [ex.submit(run_tool, c.name, c.input, os.path.join(run_dir, "img", fid, f"{usage['tool_calls'] + i:02d}_{c.name}"))
                    for i, c in enumerate(calls)]
            for c, f in zip(calls, futs):
                text, pics, is_err = f.result()
                results.append(result_block(c.id, text, pics, is_err))
                usage["pictures"] += len(pics)
                tools_used.append(c.name)
        usage["tool_calls"] += len(calls)
        if usage["requests"] == a.max_rounds - 5:
            results.append({"type": "text", "text": "You have 5 steps left for this finding. Finish and call submit_record."})
        if usage["requests"] >= a.max_rounds:
            close(None, f"no record after {a.max_rounds} steps")
            if k < n:
                results.append({"type": "text", "text": f"No record came for `{fid}` in {a.max_rounds} steps; it is closed. " + ask(k)})
        messages.append({"role": "user", "content": results})
    while k < n:  # the conversation broke: what is left has no record
        close(None, error or "the conversation ended early")
    os.makedirs(os.path.join(run_dir, "transcripts"), exist_ok=True)
    name = findings[0]["id"] if n == 1 else f"batch_{batch_no:03d}"
    json.dump({"ids": [f["id"] for f in findings], "model": model, "effort": effort, "error": error, "messages": _plain(messages)},
              open(os.path.join(run_dir, "transcripts", f"{name}.json"), "w"), indent=1, default=str)
    return out


def batches(todo: list[dict], per_agent: int, reverse: bool = False) -> list[list[dict]]:
    """Findings grouped for one agent each: same kind and zone together, the file's order inside."""
    if per_agent <= 1:
        return [[f] for f in todo]
    order = sorted(range(len(todo)), key=lambda i: (todo[i]["kind"], todo[i]["id"].split("-")[0], i))
    groups = [[todo[i] for i in order[j: j + per_agent]] for j in range(0, len(order), per_agent)]
    return [g[::-1] for g in groups] if reverse else groups


def collect(run_dir: str, model: str) -> dict:
    """Write records.json and usage.json from rec/. With routes, usage.json also splits the cost by tier."""
    recs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(run_dir, "rec", "*.json")))]
    json.dump(recs, open(os.path.join(run_dir, "records.json"), "w"), indent=1)
    tot = {k: sum(r["_usage"][k] for r in recs) for k in ("input", "output", "cache_read", "cache_write", "requests", "tool_calls", "pictures", "seconds", "usd")}
    tot["findings"] = len(recs)
    tiers: dict = {}
    for r in recs:
        t = tiers.setdefault(f"{r.get('_tier') or 'all'} ({r.get('_model', model)} {r.get('_effort', '')})", {"findings": 0, "usd": 0.0})
        t["findings"] += 1; t["usd"] = round(t["usd"] + r["_usage"]["usd"], 4)
    json.dump({"model": model, "total": tot, "by_tier": tiers, "per_finding": {r["id"]: r["_usage"] for r in recs}},
              open(os.path.join(run_dir, "usage.json"), "w"), indent=1)
    return tot


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("round"); ap.add_argument("--model", required=True); ap.add_argument("--effort", default="medium")
    ap.add_argument("--name"); ap.add_argument("--limit", type=int); ap.add_argument("--ids", nargs="*")
    ap.add_argument("--workers", type=int, default=6); ap.add_argument("--max-rounds", type=int, default=40)
    ap.add_argument("--per-agent", type=int, default=1, help="findings handed to one conversation, one after another")
    ap.add_argument("--reverse", action="store_true", help="with --per-agent: the same groups in reverse order")
    ap.add_argument("--hide-known", action="store_true", help="blind test: no known-body list, no engine grade, no site row")
    ap.add_argument("--routes", help="routes.json from route.py: findings marked hard go to --hard-model / --hard-effort")
    ap.add_argument("--hard-model"); ap.add_argument("--hard-effort", default="high")
    ap.add_argument("--tool-workers", type=int, default=6, help="tool workers that stay loaded (0 = start vet.py for every call)")
    ap.add_argument("--no-tool-cache", action="store_true", help="do not reuse tool answers of earlier runs (kept in <data>/../toolcache)")
    a = ap.parse_args()
    import anthropic

    name = a.name or f"{a.model.replace('claude-', '')}_{a.effort}" + (f"_x{a.per_agent}" if a.per_agent > 1 else "")
    run_dir = os.path.join(a.round, "api", name)
    os.makedirs(os.path.join(run_dir, "rec"), exist_ok=True)
    F = json.load(open(os.path.join(a.round, "findings.json")))
    todo = [{"id": f["id"], "kind": f["kind"]} for f in F]  # the hidden answers never leave findings.json
    if a.ids:
        todo = [f for f in todo if f["id"] in set(a.ids)]
    if a.limit:
        todo = todo[: a.limit]
    groups = batches(todo, a.per_agent, a.reverse)  # grouped before the done ones are left out, so a rerun keeps the groups
    groups = [g for g in ([f for f in g if not os.path.exists(os.path.join(run_dir, "rec", f"{f['id']}.json"))] for g in groups) if g]
    n_todo = sum(len(g) for g in groups)
    a.routes = json.load(open(a.routes)) if a.routes else {}
    hard = (a.hard_model or a.model, a.hard_effort)
    a.plan = {i: hard for i, t in a.routes.items() if t == "hard"}  # everything else runs on --model / --effort
    if a.routes and a.per_agent > 1:
        ap.error("--routes needs one finding per agent")
    if a.hide_known:
        os.environ["SKYSHIFT_VET_HIDE_KNOWN"] = "1"  # the tools run as child processes and read it
    a.system, a.tools = system_prompt(a.hide_known), tool_defs()
    client = anthropic.Anthropic(max_retries=6, timeout=900.0)
    global POOL, CACHE_DIR
    if not a.no_tool_cache:
        CACHE_DIR = os.path.join(os.path.dirname(os.path.expanduser(os.environ.get("SKYSHIFT_VET_DATA", "~/.cache/skyshift-vet/data"))), "toolcache")
    if a.tool_workers > 0 and n_todo:
        t0 = time.time()
        POOL = ToolPool(a.tool_workers, os.path.join(run_dir, "tools.log"))
        print(f"{a.tool_workers} tool workers loaded in {time.time() - t0:.0f} s", flush=True)
    print(f"{name}: {n_todo} findings to vet with {a.model} at effort {a.effort}, {a.per_agent} per agent, {a.workers} agents at a time"
          + (f"; {sum(f['id'] in a.plan for g in groups for f in g)} hard ones go to {hard[0]} at effort {hard[1]}" if a.routes else ""), flush=True)
    lock, done, res = threading.Lock(), [0], []

    def report(r):
        with lock:
            done[0] += 1; res.append(r)
            u = r["_usage"]
            print(f"  {done[0]:3d}/{n_todo}  {r['id']}  {r.get('verdict') or 'FAILED: ' + str(r.get('error'))}  "
                  f"{u['requests']} requests, {u['tool_calls']} tool calls, {u['seconds']:.0f} s, ${u['usd']:.3f}", flush=True)

    a.report = report

    def work(job):
        no, g = job
        try:
            out = vet_batch(client, g, a, run_dir, no)
            fid = g[0]["id"]
            # an "easy real" that the easy checker drops is a surprise: the hard checker looks again, and its record stands
            if len(g) == 1 and a.routes.get(fid) == "easy real" and out and out[0].get("verdict") in DROP_VERDICTS and fid not in a.plan:
                os.makedirs(os.path.join(run_dir, "rec_first"), exist_ok=True)
                os.replace(os.path.join(run_dir, "rec", f"{fid}.json"), os.path.join(run_dir, "rec_first", f"{fid}.json"))
                a.plan[fid] = hard
                vet_batch(client, g, a, run_dir, no)
        except Exception as e:  # noqa: BLE001
            print(f"  agent {no} broke: {repr(e)[:300]}", flush=True)

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, enumerate(groups)))
    if POOL is not None:
        POOL.close()
    tot = collect(run_dir, a.model)
    failed = [r["id"] for r in res if not r.get("verdict")]
    print(f"{name}: {tot['findings']} records in {run_dir}; cost ${tot['usd']:.2f} "
          f"(input {tot['input']:,}, cache read {tot['cache_read']:,}, cache write {tot['cache_write']:,}, output {tot['output']:,} tokens); "
          f"{tot['tool_calls']} tool calls; failed: {failed or 'none'}")
    if a.routes:
        for k, v in json.load(open(os.path.join(run_dir, "usage.json")))["by_tier"].items():
            print(f"  {k}: {v['findings']} findings, ${v['usd']:.2f} (${v['usd'] / max(1, v['findings']):.3f} each)")


if __name__ == "__main__":
    main()
