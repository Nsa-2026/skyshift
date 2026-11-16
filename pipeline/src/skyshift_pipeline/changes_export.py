"""Export web/public/data/changes.json and changes.atom: what changed about known stars, for the site's Changes list
and its public feed.

    uv run python -m skyshift_pipeline.changes_export                       # every zone with changes.parquet
    uv run python -m skyshift_pipeline.changes_export --zones hunt,pilot --root gs://nasa-glazing-data \
        --out ../web/public/data/changes.json --feed ../web/public/data/changes.atom

Reads results/<zone>/changes.parquet and changes_report.json (written by changes_step.py) of every zone that has
them, largest zone first. A star found in two zones (the pilot lies inside the hunt) is listed once, from the first,
with both in `zones`. Order in changes.json: unexpected changes first, then the stars Gaia DR3 already lists as
variable, each by `score`. The feed holds one entry per change, newest visit first; entry ids are stable between
exports, so a feed reader shows only what is new. Nothing here goes online.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .changes import DET_RANGE
from .store import Store, results_rel

L = log.get("changes_export")

REPO = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO / "web" / "public" / "data" / "changes.json"
SITE = "https://skyshift.web.app"
FEED_NAME = "changes.atom"
TAG = "tag:skyshift.web.app,2026-11-16:changes/"   # entry ids: stable, never reused
KINDS = ("brightness", "colour", "appeared", "vanished")
SIGMA_SCALE = 10.0       # score: 1 − e^(−|σ|/10) for brightness and colour (5σ 0.39, 10σ 0.63, 20σ 0.86)
SNR_SCALE = 40.0         # ... 1 − e^(−S/N/40) for appeared / vanished (S/N 20 0.39, 60 0.78)
COLOUR_WEIGHT = 0.8      # a colour change alone: a little below a brightness change of the same σ
STRONG, WARN = 0.3, 0.95  # per check, as candidates.json's score (contracts/api.md v3.24)
VIEWER_FOV = 5           # arcmin, the months clock's close field


def finished_zones(store: Store) -> list[str]:
    zs = [z for z in C.ZONES.values() if store.exists(results_rel(z.name, "changes.parquet"))]
    zs.sort(key=lambda z: -(z.ra_max - z.ra_min) * (z.dec_max - z.dec_min))
    return [z.name for z in zs]


def _num(x, nd: int):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def mjd_iso(mjd: float, date_only: bool = False) -> str | None:
    if mjd is None or not np.isfinite(mjd):
        return None
    t = pd.Timestamp("1858-11-17", tz="UTC") + pd.Timedelta(days=float(mjd))
    return t.strftime("%Y-%m-%d") if date_only else t.strftime("%Y-%m-%dT%H:%M:%SZ")


def headline(r) -> str:
    """A few words for the row and the feed title: "27% fainter in D5 (3.8–4.4 µm)"."""
    if r["kind"] == "brightness":
        pct = 100.0 * float(r["change_frac"])
        det = int(r["detector"])
        word = "brighter" if pct > 0 else "fainter"
        if pct < 0 and float(r["rel_after"]) < 0.2:
            return f"faded below detection in D{det} ({DET_RANGE[det]})"
        return f"{abs(pct):.0f}% {word} in D{det} ({DET_RANGE[det]})"
    if r["kind"] == "colour":
        c = float(r["colour_change"])
        return f"became {'redder' if c > 0 else 'bluer'}: {100 * c:+.0f}% from D1 to D6"
    if r["kind"] == "appeared":
        return f"appeared in visit {int(r['visit_after'])}"
    return f"gone in visit {int(r['visit_after'])}"


def score(r, checks: list[dict]) -> float:
    if r["kind"] in ("appeared", "vanished"):
        base = 1.0 - math.exp(-max(float(r["snr"]), 0.0) / SNR_SCALE)
    elif r["kind"] == "colour":
        base = COLOUR_WEIGHT * (1.0 - math.exp(-abs(float(r["colour_sigma"])) / SIGMA_SCALE))
    else:
        base = 1.0 - math.exp(-abs(float(r["sigma"])) / SIGMA_SCALE)
    for c in checks:
        base *= STRONG if c["level"] == "strong" else WARN if c["level"] == "warn" else 1.0
    return round(base, 3)


def viewer_url(e: dict, site: str = SITE) -> str:
    """The Viewer on the star: months clock, its detector."""
    return f"{site}/?ra={e['ra']:.5f}&dec={e['dec']:.5f}&clock=months&fov={VIEWER_FOV}&det={e['det']}#/explore"


def entry(r) -> dict:
    checks = json.loads(r["checks"] or "[]")
    gv = int(r["gaia_variable"])
    gid = int(r["gaia_id"])
    sid = int(r["source_id"])
    kind = str(r["kind"])
    others = json.loads(r["others"] or "[]")
    star = kind in ("brightness", "colour")
    return {
        "id": str(r["id"]), "kind": kind, "zone": str(r["zone"]), "zones": [str(r["zone"])],
        "name": str(r["name"]), "gaia_id": gid if gid > 0 else None, "ref_id": str(r["ref_id"]) or None,
        "source_id": sid if sid >= 0 else None, "ra": _num(r["ra"], 6), "dec": _num(r["dec"], 6),
        "g_mag": _num(r["g_mag"], 2), "det": int(r["detector"]), "clock": "months",
        "measured": ("own" if r["population"] == "source" else "forced") if star else "own",
        "visits_before": [int(v) for v in r["visits_before"]], "visit_after": int(r["visit_after"]),
        "mjd_before": _num(r["mjd_before"], 3), "mjd_after": _num(r["mjd_after"], 3),
        "date_before": mjd_iso(r["mjd_before"], True), "date_after": mjd_iso(r["mjd_after"], True),
        "rel_before": _num(r["rel_before"], 4) if star else None, "rel_after": _num(r["rel_after"], 4) if star else None,
        "change_frac": _num(r["change_frac"], 4) if star else None, "sigma": _num(r["sigma"], 2) if star else None,
        "other_detectors": others if star else [],
        "colour_change": _num(r["colour_change"], 4), "colour_sigma": _num(r["colour_sigma"], 2),
        "snr": _num(r["snr"], 1), "n_dots": int(r["n_dots"]) or None, "n_sessions": int(r["n_sessions"]) or None,
        "detectors": [int(d) for d in r["detectors"]],
        "gaia_variable": True if gv == 1 else False if gv == 0 else None,
        "gaia_class": str(r["gaia_class"]) or None,
        "headline": headline(r), "text": str(r["text"]), "look": str(r["look"]), "checks": checks,
        "score": score(r, checks),
    }


def identity(e: dict) -> str:
    """The same star (or source) in two zones: its Gaia id, catalogue entry, or sky-wide source id."""
    if e["gaia_id"]:
        return f"gaia:{e['gaia_id']}"
    if e["ref_id"]:
        return e["ref_id"]
    return f"src:{e['source_id']}"


def order_key(e: dict):
    """Unexpected changes first, then the stars Gaia already lists as variable; each by score."""
    return (e["gaia_variable"] is True, -e["score"], e["id"])


def build(store: Store, zones: list[str]) -> dict:
    out: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    used, info, coll = [], [], set()
    for z in zones:
        rel = results_rel(z, "changes.parquet")
        if not store.exists(rel):
            L.warning("changes %s: no changes.parquet, skipped", z)
            continue
        df = store.read_parquet(rel)
        try:
            rep = store.read_json(results_rel(z, "changes_report.json"))
        except (FileNotFoundError, ValueError):
            rep = {}
        used.append(z)
        coll.update(rep.get("collections") or [])
        b = rep.get("brightness") or {}
        null = b.get("null") or {}
        info.append({"zone": z, "stars": b.get("stars"), "visits": b.get("visits"), "listed": int(len(df)),
                     "false_alarms_per_1000": null.get("per_1000"),
                     "skipped": b.get("skipped")})
        n_dup = 0
        for r in df.to_dict("records"):
            e = entry(r)
            k = (e["kind"], identity(e))
            if k in seen:
                if z not in seen[k]["zones"]:
                    seen[k]["zones"].append(z)
                n_dup += 1
                continue
            seen[k] = e
            out.append(e)
        L.info("changes %s: %d rows, %d already listed from an earlier zone", z, len(df), n_dup)
    out.sort(key=order_key)
    if len({e["id"] for e in out}) != len(out):
        raise ValueError("change ids are not unique")
    release = "+".join(c.replace("spherex_", "").upper() for c in sorted(coll)) or "DR1+QR3"
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_release": release,
        "zones": used,
        "watched": info,
        "counts": {k: sum(e["kind"] == k for e in out) for k in KINDS},
        "gaia_variable": sum(e["gaia_variable"] is True for e in out),
        "feed": FEED_NAME,
        "changes": out,
    }


def dumps(doc: dict) -> str:
    """Compact JSON, one change per line (small diffs)."""
    head = {k: v for k, v in doc.items() if k != "changes"}
    lines = [json.dumps(c, ensure_ascii=False, separators=(",", ":"), allow_nan=False) for c in doc["changes"]]
    text = json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1]
    return text + ',"changes":[\n' + ",\n".join(lines) + "\n]}\n"


def atom(doc: dict, site: str = SITE) -> str:
    """The Atom 1.0 feed: one entry per change, newest visit first."""
    site = site.rstrip("/")
    items = sorted(doc["changes"], key=lambda e: (-(e["mjd_after"] or 0.0), order_key(e)))
    x = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">',
         "  <title>SkyShift: what changed in known stars</title>",
         "  <subtitle>Stars and sources whose brightness or colour changed between NASA SPHEREx visits six months "
         "apart, measured by SkyShift. Each entry says what changed, by how much, and what to look at.</subtitle>",
         f'  <link rel="self" type="application/atom+xml" href={quoteattr(f"{site}/data/{FEED_NAME}")}/>',
         f'  <link rel="alternate" type="text/html" href={quoteattr(f"{site}/#/changes")}/>',
         f"  <id>{escape(f'{site}/data/{FEED_NAME}')}</id>",
         f"  <updated>{doc['generated']}</updated>",
         "  <author><name>SkyShift</name></author>",
         "  <generator>skyshift_pipeline.changes_export</generator>"]
    for e in items:
        page = f"{site}/#/changes/{e['id']}"
        view = viewer_url(e, site)
        gv = " Gaia already lists it as variable." if e["gaia_variable"] else ""
        summary = f"{e['text']}{gv} Look: {e['look']}"
        checks = "".join(f"<li><b>{escape(c['level'])}</b>: {escape(c['text'])}"
                         + (f" <i>Look: {escape(c['look'])}</i>" if c.get("look") else "") + "</li>" for c in e["checks"])
        html = (f"<p>{escape(e['text'])}</p>" + (f"<p><b>{escape(gv.strip())}</b></p>" if gv else "")
                + f"<p>Look: {escape(e['look'])}</p>" + (f"<ul>{checks}</ul>" if checks else "")
                + f'<p><a href={quoteattr(view)}>Open the Viewer on it</a> (months clock, D{e["det"]}).</p>')
        cats = [e["kind"]] + (["gaia-variable"] if e["gaia_variable"] else [])
        x += ["  <entry>",
              f"    <title>{escape(e['name'])}: {escape(e['headline'])}</title>",
              f"    <id>{escape(TAG + e['id'])}</id>",
              f"    <updated>{mjd_iso(e['mjd_after']) or doc['generated']}</updated>",
              f'    <link rel="alternate" type="text/html" href={quoteattr(page)}/>',
              f'    <link rel="related" type="text/html" href={quoteattr(view)}/>',
              *[f"    <category term={quoteattr(c)}/>" for c in cats],
              f"    <summary type=\"text\">{escape(summary)}</summary>",
              f"    <content type=\"html\">{escape(html)}</content>",
              "  </entry>"]
    x.append("</feed>")
    return "\n".join(x) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skyshift_pipeline.changes_export", description=__doc__.split("\n")[0])
    ap.add_argument("--zones", help="comma-separated, in order (default: every zone with changes.parquet, largest first)")
    ap.add_argument("--root", default="gs://nasa-glazing-data")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--feed", help="the Atom feed (default: changes.atom next to --out)")
    ap.add_argument("--site", default=SITE, help="the site's address, for the feed's links")
    a = ap.parse_args(argv)
    log.setup()
    store = Store(a.root)
    zones = a.zones.split(",") if a.zones else finished_zones(store)
    doc = build(store, zones)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(doc))
    feed = Path(a.feed) if a.feed else out.with_name(FEED_NAME)
    feed.write_text(atom(doc, a.site))
    L.info("wrote %s (%s, zones %s) and %s", out, doc["counts"], doc["zones"], feed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
