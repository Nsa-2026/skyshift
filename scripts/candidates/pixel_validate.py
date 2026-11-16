"""Hit rates of the pixel checks on known asteroids, on obvious junk and on faint fixed stars (pixel_check.py --validate).

Sets, from the local mirror (<root>/results, <root>/photolists, <root>/cache/mpc):
- real_mpc: hunt movers within 10″ of an MPC prediction of their session (cache/mpc/hunt/run_*.json, known step's
  output) whose rate agrees within 30%, faint ones first (median dot S/N ≤ 15, like the To-check movers).
- real_track: ecl04 movers on a tracklets track (3+ sessions; in the hunt every such track was a known asteroid),
  one-detector-side ones first (D5/D6 alone, D1–D3 alone), so the partner test meets real colours.
- junk: suspects of every local zone graded C for hot_pixel, very_sharp or sharp_outlier (vet's gates).
- stars: faint fixed sources of the hunt (S/N 5–8): the photo where the forced S/N is highest plays the "circled"
  photo, the others the rest of the visit (one_photo should say `steady`).
Writes <root>/cache/pixel/validation.json (not checks/: the exporter merges every file there) and prints the table.
"""

from __future__ import annotations

import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import pixel_check as PC
from skyshift_core import pixelcheck as P

N_MPC = 50
N_TRACK = 36
N_JUNK = 40
N_STARS = 30
SEED = 16


def mpc_matched(root: Path, tables: PC.Tables) -> list[PC.Mover]:
    mv = tables.rows("hunt", "movers")
    if mv is None:
        return []
    runs = {}
    for p in glob.glob(str(root / "cache" / "mpc" / "hunt" / "run_*.json")):
        j = json.loads(Path(p).read_text())
        runs[int(j["session"])] = j
    out = []
    for i, r in mv.iterrows():
        j = runs.get(int(r["session"]))
        if j is None or not np.isfinite(r["rate_arcsec_h"]):
            continue
        o = j["objects"]
        dt_h = (r["mjd"] - j["mjd"]) * 24.0
        ra = np.asarray(o["ra"]) + np.asarray(o["dra_arcsec_h"]) * dt_h / 3600.0 / np.cos(np.radians(r["dec"]))
        dec = np.asarray(o["dec"]) + np.asarray(o["ddec_arcsec_h"]) * dt_h / 3600.0
        e, n = PC.tangent(ra, dec, r["ra"], r["dec"])
        sep = np.hypot(e, n)
        k = int(np.argmin(sep))
        rate = float(np.hypot(o["dra_arcsec_h"][k], o["ddec_arcsec_h"][k]))
        if sep[k] <= 10.0 and abs(r["rate_arcsec_h"] - rate) <= 0.3 * max(rate, 10.0):
            out.append((float(np.median(r["dot_snr"])), i, r, o["name"][k]))
    rng = np.random.default_rng(SEED)
    faint = [q for q in out if q[0] <= 15]
    rng.shuffle(faint)
    pick = faint[:N_MPC]
    movers = []
    for _, i, r, name in pick:
        m = PC.Mover(f"hunt-mv{i}", "hunt", r)
        m.set, m.truth = "real_mpc", name
        movers.append(m)
    print(f"real_mpc: {len(out)} hunt movers matched to MPC, {len(faint)} with median dot S/N <= 15, {len(movers)} used")
    return movers


def track_movers(tables: PC.Tables) -> list[PC.Mover]:
    mv = tables.rows("ecl04", "movers")
    tr = tables.rows("ecl04", "tracklets")
    if mv is None or tr is None:
        return []
    tr = tr.reset_index(drop=True)
    on = set(tr[(tr.tracklet_id >= 0) & (tr.n_sessions >= 3)].mover_id.astype(str))
    ids = [f"ecl04-{int(c)}-{int(round(t * 1440))}" for c, t in zip(mv.cell, mv.mjd)]  # the movers' id rule
    rng = np.random.default_rng(SEED)
    rows = []
    for mid in [i for i, k in enumerate(ids) if k in on]:
        r = mv.iloc[mid]
        dets = {int(d) for d in r["dot_det"]}
        side = ("mwir" if dets <= {4, 5, 6} else "swir" if dets <= {1, 2, 3} else "both")
        rows.append((side, mid, r))
    pick = []
    for side, n in (("mwir", 18), ("swir", 9), ("both", 9)):
        q = [x for x in rows if x[0] == side]
        rng.shuffle(q)
        pick += q[:n]
    movers = []
    for side, mid, r in pick:
        m = PC.Mover(f"ecl04-mv{mid}", "ecl04", r)
        m.set, m.truth = "real_track", side
        movers.append(m)
    print(f"real_track: {len(rows)} ecl04 track movers, {len(movers)} used")
    return movers


def junk(root: Path, tables: PC.Tables) -> list[PC.Mover]:
    pool = []
    for p in sorted(glob.glob(str(root / "results" / "*" / "suspects.parquet"))):
        zone = Path(p).parent.name
        if tables.photos(zone) is None:
            continue
        s = tables.rows(zone)
        g = s[(s.grade == "C") & (s.gate_hot_pixel | s.gate_very_sharp | s.gate_sharp_outlier)]
        for cid, r in g.iterrows():
            why = [k for k in ("gate_hot_pixel", "gate_very_sharp", "gate_sharp_outlier") if r[k]]
            pool.append((zone, cid, r, ",".join(w.removeprefix("gate_") for w in why)))
    rng = np.random.default_rng(SEED)
    # sharp ones first (fewer), then hot pixels
    sharp = [q for q in pool if "sharp" in q[3]]
    hot = [q for q in pool if "sharp" not in q[3]]
    rng.shuffle(sharp)
    rng.shuffle(hot)
    pick = sharp[: N_JUNK // 2] + hot[: N_JUNK - min(len(sharp), N_JUNK // 2)]
    movers = []
    for zone, cid, r, why in pick:
        m = PC.Mover(cid, zone, r)
        m.set, m.truth = "junk", why
        movers.append(m)
    print(f"junk: {len(pool)} gated grade C suspects ({len(sharp)} sharp), {len(movers)} used")
    return movers


def star_test(root: Path, tables: PC.Tables, meas: PC.Measurer) -> dict:
    """one_photo on faint fixed hunt sources: the photo with the highest forced S/N is 'circled'."""
    f = tables.rows("hunt", "fixed")
    pl = tables.photos("hunt")
    if f is None or pl is None:
        return {}
    f = f.reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    faint = f[(f.snr >= 5) & (f.snr <= 8)]
    pick = faint.iloc[rng.permutation(len(faint))[: N_STARS]]
    plan = []
    for r in pick.itertuples():
        cand = pl[pl.detector == 2]
        cand = cand[[PC.in_polygon(s, r.ra, r.dec) for s in cand.s_region]]
        cand = cand.drop_duplicates("session")
        if len(cand) < 4:
            continue
        cand = cand.iloc[rng.permutation(len(cand))[:7]]
        for u in cand.url:
            plan.append((r.source_id, r.snr, u, r.ra, r.dec))
    lays = meas.layouts(u for _, _, u, _, _ in plan)
    tg = defaultdict(list)
    keys = []
    for sid, snr, u, ra, dec in plan:
        lay = lays.get(u)
        if lay is None:
            continue
        x, y = (float(v) for v in lay.wcs().all_world2pix([[ra, dec]], 0)[0])
        if not (8 <= x < lay.nx - 8 and 8 <= y < lay.ny - 8):
            continue
        k = PC.mkey(u, "forced", x, y)
        tg[u].append((k, "forced", x, y, 0.0))
        keys.append((sid, k))
    res = meas.run(tg)
    by = defaultdict(list)
    for sid, k in keys:
        r = res.get(k)
        if r and not r.get("masked") and r.get("err"):
            by[sid].append(r)
    verdicts = Counter()
    for sid, rs in by.items():
        if len(rs) < 3:
            continue
        rs = sorted(rs, key=lambda r: -r["snr"])
        o = rs[0]
        v = P.one_photo(o["flux"], o["err"], [r["flux"] for r in rs[1:]], [r["err"] for r in rs[1:]])
        verdicts[v["verdict"]] += 1
    return dict(verdicts)


def rates(results: dict[str, dict], movers: list[PC.Mover]) -> dict:
    by_set = defaultdict(list)
    for m in movers:
        by_set[m.set].append(m)
    out = {}
    for s, ms in by_set.items():
        n = len(ms)
        codes = Counter()
        dots = defaultdict(list)
        for m in ms:
            r = results[m.id]
            for c in r["checks"]:
                codes[f"{c['code']}:{c['level']}"] += 1
            if r["status"]:
                codes[f"status:{r['status']}"] += 1
            mt = r["metrics"]
            dots["size_ratio"] += [x for x in mt.get("size_ratio", []) if x is not None]
            dots["one_photo"] += [v["verdict"] for v in mt.get("one_photo", [])]
            dots["transient"] += [int((v.get("transient_px") or 0) >= 1) for v in mt.get("one_photo", [])]
            for k, v in mt.items():
                if k.startswith("partner_D"):
                    dots["partner"].append(f"D{v['det']}:{v['verdict']}")
                    if v["verdict"] == "seen":
                        dots["partner_ratio"].append((v["det"], v["ratio"]))
                    elif np.isfinite(v.get("bound") or np.nan):
                        dots["partner_bound"].append((v["det"], v["bound"]))
            if "path" in mt:
                dots["path"].append(mt["path"]["verdict"])
        sr = np.array(dots["size_ratio"], float)
        out[s] = dict(
            movers=n,
            checks={k: f"{v} ({100 * v / n:.0f}%)" for k, v in sorted(codes.items())},
            size_ratio_q=dict(zip(("q01", "q05", "q25", "q50", "q95"),
                                  [round(float(x), 2) for x in np.quantile(sr, [.01, .05, .25, .5, .95])])) if sr.size else {},
            size_ratio_dots=int(sr.size),
            dots_below_0_7=int((sr < 0.7).sum()), dots_below_0_55=int((sr < 0.55).sum()),
            one_photo=dict(Counter(dots["one_photo"])),
            transient_dots=f"{sum(dots['transient'])} of {len(dots['transient'])}",
            partner=dict(Counter(dots["partner"])),
            partner_ratio=sorted((d, round(x, 2)) for d, x in dots["partner_ratio"]),
            partner_bound=sorted((d, round(x, 2)) for d, x in dots["partner_bound"]),
            path=dict(Counter(dots["path"])),
        )
    return out


def main(root: Path, tables: PC.Tables, meas: PC.Measurer) -> int:
    movers = mpc_matched(root, tables) + track_movers(tables) + junk(root, tables)
    results = PC.run(movers, tables, meas)
    out = rates(results, movers)
    out["stars"] = star_test(root, tables, meas)
    path = root / "cache" / "pixel" / "validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    detail = {m.id: dict(set=m.set, truth=m.truth, checks=results[m.id]["checks"], metrics=results[m.id]["metrics"])
              for m in movers}
    path.write_text(json.dumps(dict(rates=out, detail=detail), indent=1, default=PC._json_default))
    print(json.dumps(out, indent=1, default=PC._json_default))
    print(f"wrote {path}; read {PC.S3.BYTES[0] / 1e6:.0f} MB in {PC.S3.REQUESTS[0]} requests; {len(meas.errors)} errors")
    return 0
