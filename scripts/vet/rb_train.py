"""Rebuild the exporter's real-or-bogus model (pipeline/src/skyshift_pipeline/rb_model.json) from local tables.

Run from pipeline/ (no bucket reads; ~/skyshift-data holds the zones' results, photo lists, refcat and MPC caches):

    uv run python ../scripts/vet/rb_train.py prepare --work DIR      # as-if-unknown suspects + MPC labels per zone
    uv run --with scikit-learn python ../scripts/vet/rb_train.py train --work DIR [--out MODEL.json] [--report R.json]

prepare: a local root of symlinks (results/<zone>/{movers,fixed,freedots,tracklets,tracklet_dots,qa_photos},
photolists, refcat, cache/allwise), then the suspects step with NO known predictions (every mover kept, graded and
warned by today's code) -> DIR/<zone>_suspects.parquet; and each mover's nearest known body (MPC asteroids and comets
at any V, Horizons planets and moons; suspects.nearest_known) -> DIR/<zone>_known.parquet. Predictions come from the
vetting harness' pred.parquet (~/.cache/skyshift-vet/data/<zone>/) when there is one, else from ~/skyshift-data's
cache/mpc and cache/planets. 306P/LINEAR (missing from the MPC comet file of 16 Nov) is set on its ecl04 mover.

train: real = a known body within 5″, rest = none within 60″ (fix-wide.md, fix-moverflaws.md); in between unused. The
blind-round ids (rounds/r2/findings.json) never train or measure. Features: skyshift_pipeline.rb_score.features (the
suspects row only: no known-body column, rate or motion significance). Held-out quality: each zone scored by a model
trained on the other zones (leave one zone out), AUC per zone and pooled, AUC inside each grade, and real movers in the
top N of each zone's grade A / B list, against the exporter's existing score (as if unknown: S/N x evidence x the
checks' grade C term). Then one model on all zones -> --out, with the held-out numbers in its `trained`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HOME = Path.home()
DATA = HOME / "skyshift-data"
VET_DATA = Path(os.environ.get("SKYSHIFT_VET_DATA", HOME / ".cache/skyshift-vet/data"))
BLIND = HOME / ".cache/skyshift-vet/rounds/r2/findings.json"
ZONES = ("hunt", "ecl04", "ecl05", "ecl08", "ecl10", "ecl11", "ecl17")
REAL_ARCSEC, REST_ARCSEC = 5.0, 60.0
FIXES = {"ecl04-670520025038-87705962": ("306P/LINEAR", 1.0)}  # ecl04-discovery.md: MPC orbit 0.5-1.5″ off its dots
COMET = re.compile(r"^(\d+[PCDI]/|[PCDI]/|[PCDIA]/\d{4})")
REPO = Path(__file__).resolve().parents[2]
MODEL_OUT = REPO / "pipeline/src/skyshift_pipeline/rb_model.json"
PARAMS = dict(max_iter=120, learning_rate=0.08, max_leaf_nodes=8, min_samples_leaf=40, l2_regularization=1.0,
              random_state=0)
TOP_N = (10, 20, 50)


# --- prepare ---------------------------------------------------------------------------------------
def _link(dst: Path, src: Path) -> None:
    if src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(src)


def refcat_source(z: str) -> Path | None:
    """The zone's refcat on this machine: refcat/<zone>.parquet, the vetting harness' copy, or the exporter's cache."""
    for p in (DATA / f"refcat/{z}.parquet", VET_DATA / z / "refcat.parquet"):
        if p.exists():
            return p
    hits = sorted((DATA / "cache").glob(f"refcat__{z}.parquet.*.parquet"), key=lambda p: p.stat().st_size)
    return hits[-1] if hits else None


def prepare(work: Path, zones: list[str]) -> None:
    from skyshift_pipeline import config as C
    from skyshift_pipeline import suspects as SU
    from skyshift_pipeline.known import load_predictions
    from skyshift_pipeline.store import Store, photolist_rel
    from skyshift_pipeline.suspects import nearest_known
    from skyshift_pipeline.tracklets import mover_ids

    root = work / "root"
    for z in zones:
        for f in ("movers", "fixed", "freedots", "tracklets", "tracklet_dots", "qa_photos"):
            _link(root / f"results/{z}/{f}.parquet", DATA / f"results/{z}/{f}.parquet")
        _link(root / f"photolists/zones/{z}.parquet", DATA / f"photolists/zones/{z}.parquet")
        rc = refcat_source(z)
        if rc is not None:
            _link(root / f"refcat/{z}.parquet", rc)
    _link(root / "cache/allwise", DATA / "cache/allwise")
    st = Store(str(root))
    empty = pd.DataFrame({c: pd.Series(dtype=float) for c in ("ra", "dec", "mjd", "dra_arcsec_h", "ddec_arcsec_h", "v")})
    empty["session"] = pd.Series(dtype="int64")
    empty["name"] = pd.Series(dtype=str)
    for z in zones:
        t0 = time.time()
        s = SU.run(st, C.ZONES[z], pred=empty)
        shutil.copy(root / f"results/{z}/suspects.parquet", work / f"{z}_suspects.parquet")
        print(z, "suspects", s.get("suspects"), s.get("vetting", {}).get("grades"), f"{time.time() - t0:.0f} s", flush=True)
        out = work / f"{z}_known.parquet"
        if out.exists():
            continue
        m = pd.read_parquet(DATA / f"results/{z}/movers.parquet").reset_index(drop=True)
        vp = VET_DATA / z / "pred.parquet"
        if vp.exists():
            pred = pd.read_parquet(vp)
        else:
            ds = Store(str(DATA))
            pred = load_predictions(ds, ds.read_parquet(photolist_rel(z)), C.ZONES[z])
        k = nearest_known(m, pred)
        k.insert(0, "id", list(mover_ids(C.ZONES[z], m)))
        k.to_parquet(out)
        print(z, "labels", len(k), "within 5″", int((k["sep"] <= REAL_ARCSEC).sum()), f"{time.time() - t0:.0f} s", flush=True)


# --- data ------------------------------------------------------------------------------------------
def blind_ids() -> set[str]:
    return {f["id"] for f in json.loads(BLIND.read_text())} if BLIND.exists() else set()


def load(work: Path, zones: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(features, info) for every mover of the zones; info: zone, id, label (1 real, 0 rest, -1 unused), grade,
    known name, exempt (slow, wide or matched known comet), existing score."""
    from skyshift_pipeline import candidates as K
    from skyshift_pipeline import known_comets as KC
    from skyshift_pipeline import rb_score as RB
    from skyshift_pipeline.candidates import mover_score
    from skyshift_pipeline.store import Store

    ds = Store(str(DATA)) if DATA.exists() else None
    orbits = KC.load_orbits(ds) if ds is not None else None
    F, I = [], []
    for z in zones:
        s = pd.read_parquet(work / f"{z}_suspects.parquet").reset_index(drop=True)
        k = pd.read_parquet(work / f"{z}_known.parquet").set_index("id")
        sep = k["sep"].reindex(s["id"]).to_numpy(np.float64, copy=True)
        name = k["name"].reindex(s["id"]).fillna("").astype(str).to_numpy(object, copy=True)
        for i, (nm, sp) in FIXES.items():
            j = np.flatnonzero(s["id"].to_numpy() == i)
            name[j], sep[j] = nm, sp
        lab = np.where(sep <= REAL_ARCSEC, 1, np.where(~(sep <= REST_ARCSEC), 0, -1))
        wide = np.array([("wide" in list(w if w is not None else [])) or ("wide" in list(r if r is not None else []))
                         for w, r in zip(s["warn_codes"], s["reasons"])])
        cm_ids = KC.zone_comets(ds, z, s, orbits)[0] if (ds is not None and orbits is not None) else {}
        comet_match = s["id"].astype(str).isin(cm_ids).to_numpy()
        comet = ((lab == 1) & np.array([bool(COMET.match(n)) for n in name])) | comet_match
        rate, slow = np.full(len(s), np.nan), np.zeros(len(s), bool)
        for j, r in enumerate(s.itertuples()):  # the exporter's rule (candidates.movers): the track's rate, else link's
            tr = K.on_track(r)
            rt = K.table_motion(r)[0]
            rate[j] = tr[1] if tr else (rt if rt is not None else np.nan)
            slow[j] = RB.is_slow(tr[1], tr[1] / tr[2]) if tr and tr[2] > 0 else \
                RB.is_slow(rt, K.table_rate_err(r) if rt is not None else None, int(r.n_exposures))
        grade = s["grade"].astype(str).to_numpy()
        base = mover_score(s["snr"], s["evidence"], np.full(len(s), np.nan), grade)
        F.append(RB.features(s))
        I.append(pd.DataFrame({"zone": z, "id": s["id"], "label": lab, "grade": grade, "name": name, "sep": sep,
                               "slow": slow, "wide": wide, "comet_match": comet_match,
                               "exempt": slow | wide | comet_match, "rate": rate, "base": base, "comet": comet}))
    F, I = pd.concat(F, ignore_index=True), pd.concat(I, ignore_index=True)
    I["blind"] = I["id"].isin(blind_ids())
    return F, I


# --- export of the trees ----------------------------------------------------------------------------
def export_trees(m, features: list[str]) -> dict:
    """sklearn HistGradientBoostingClassifier (binary) -> plain JSON lists, checked against predict_proba by train."""
    trees = []
    for (pred,) in m._predictors:
        nd = pred.nodes
        trees.append({"feature": nd["feature_idx"].astype(int).tolist(),
                      "threshold": [float(x) for x in nd["num_threshold"]],
                      "left": nd["left"].astype(int).tolist(), "right": nd["right"].astype(int).tolist(),
                      "missing_left": nd["missing_go_to_left"].astype(bool).tolist(),
                      "leaf": nd["is_leaf"].astype(bool).tolist(),
                      "value": [round(float(x), 6) for x in nd["value"]]})
    return {"version": 1, "kind": "boosted_trees", "features": list(features),
            "baseline": float(np.ravel(m._baseline_prediction)[0]), "trees": trees}


# --- measures --------------------------------------------------------------------------------------
def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score
    y = np.asarray(y)
    return round(float(roc_auc_score(y, p)), 4) if 0 < y.sum() < len(y) else None


def top_real(info: pd.DataFrame, col: str) -> dict:
    """Over the held-out zones, inside each zone's grade (exempt movers left out, as the exporter does): of the top N
    by `col`, how many are real; summed over zones, per grade."""
    out = {}
    for grade in ("A", "B", "C"):
        t0 = info[(info.grade == grade) & ~info.exempt & (info.label >= 0)]
        out[grade] = {}
        for n in TOP_N:
            real = junk = 0
            for _, t in t0.groupby("zone"):
                top = t.sort_values([col, "id"], ascending=[False, True]).head(n)
                real += int((top.label == 1).sum())
                junk += int((top.label == 0).sum())
            out[grade][f"top{n}"] = {"real": real, "rest": junk}
    return out


def ranks(info: pd.DataFrame, ids) -> list[dict]:
    """Rank (1 = first) of each id inside its zone's grade among the non-exempt movers, by the existing score and by
    rb (held out): where it would sit before and after."""
    out = []
    for i in ids:
        r = info[info.id == i].iloc[0]
        g = info[(info.zone == r.zone) & (info.grade == r.grade) & ~info.exempt]
        nb = int((g.base > r.base).sum()) + 1
        nr = int((g.rb > r.rb).sum()) + 1
        out.append({"id": i, "name": r["name"], "grade": r.grade, "of": len(g), "rank_existing": nb, "rank_rb": nr,
                    "rb": round(float(r.rb), 3), "exempt": bool(r.exempt)})
    return out


def train(work: Path, zones: list[str], out: Path, report: Path | None) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier

    from skyshift_pipeline import rb_score as RB

    F, I = load(work, zones)
    cols = list(RB.FEATURES)
    X = F[cols].to_numpy(np.float64)
    use = (I.label >= 0).to_numpy() & ~I.blind.to_numpy()
    y = I.label.to_numpy()
    held = np.full(len(I), np.nan)
    per_zone = {}
    for z in zones:
        tr, te = use & (I.zone != z).to_numpy(), (I.zone == z).to_numpy()
        m = HistGradientBoostingClassifier(**PARAMS).fit(X[tr], y[tr])
        held[te] = m.predict_proba(X[te])[:, 1]
        t = te & use
        n = t & ~I.exempt.to_numpy()
        per_zone[z] = {"real": int((y[t] == 1).sum()), "rest": int((y[t] == 0).sum()), "auc_rb": auc(y[t], held[t]),
                       "auc_existing": auc(y[t], I.base.to_numpy()[t]),
                       "not_exempt": {"real": int((y[n] == 1).sum()), "rest": int((y[n] == 0).sum()),
                                      "auc_rb": auc(y[n], held[n]), "auc_existing": auc(y[n], I.base.to_numpy()[n])}}
        ne = per_zone[z]["not_exempt"]
        print(f"held out {z}: AUC {per_zone[z]['auc_rb']} (existing score {per_zone[z]['auc_existing']}), "
              f"{per_zone[z]['real']} real, {per_zone[z]['rest']} rest; not exempt: AUC {ne['auc_rb']} "
              f"({ne['auc_existing']}), {ne['real']} real, {ne['rest']} rest", flush=True)
    I["rb"] = held
    ev = I[use].copy()
    ne = ev[~ev.exempt]
    res = {"zones": zones, "label": f"real: a known body within {REAL_ARCSEC:g}″; rest: none within {REST_ARCSEC:g}″",
           "blind_ids_left_out": int(I.blind.sum()), "movers": int(len(I)), "used": int(use.sum()),
           "real": int((ev.label == 1).sum()), "rest": int((ev.label == 0).sum()),
           "exempt": {"slow": int(ev.slow.sum()), "wide": int(ev.wide.sum()),
                      "comet": int(ev.comet_match.sum()), "either": int(ev.exempt.sum()),
                      "real_slow": int((ev.slow & (ev.label == 1)).sum()), "comets": int(ev.comet.sum()),
                      "comets_exempt": int((ev.comet & ev.exempt).sum())},
           "per_zone": per_zone,
           "pooled_not_exempt": {"auc_rb": auc(ne.label, ne.rb), "auc_existing": auc(ne.label, ne.base)},
           "by_grade_not_exempt": {g: {"real": int((t.label == 1).sum()), "rest": int((t.label == 0).sum()),
                                       "auc_rb": auc(t.label, t.rb), "auc_existing": auc(t.label, t.base)}
                                   for g, t in ne.groupby("grade")},
           "top_real_rb": top_real(ev, "rb"), "top_real_existing": top_real(ev, "base")}
    real = ne.rb[ne.label == 1]
    for keep in (0.99, 0.95):
        thr = float(np.quantile(real, 1 - keep))
        res[f"rest_below_line_keeping_{int(keep * 100)}pct_real"] = round(float((ne.rb[ne.label == 0] < thr).mean()), 3)
    cm = ev[ev.comet]
    res["comets"] = {"movers": int(len(cm)), "exempt": int(cm.exempt.sum()),
                     "exempt_comet": int((cm.comet_match).sum()),
                     "exempt_wide": int(cm.wide.sum()), "exempt_slow": int(cm.slow.sum())}
    res["comets_not_exempt"] = ranks(ev, cm[~cm.exempt].id)
    final = HistGradientBoostingClassifier(**PARAMS).fit(X[use], y[use])
    model = export_trees(final, cols)
    p_ours = RB.predict(model, X)
    p_sk = final.predict_proba(X)[:, 1]
    res["export_max_abs_diff"] = float(np.max(np.abs(p_ours - p_sk)))
    if res["export_max_abs_diff"] > 1e-4:
        raise SystemExit(f"exported trees disagree with sklearn: {res['export_max_abs_diff']}")
    model["trained"] = {"script": "scripts/vet/rb_train.py", "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "params": PARAMS, **{k: res[k] for k in ("zones", "label", "real", "rest", "blind_ids_left_out")},
                        "held_out": {"per_zone": per_zone, "pooled_not_exempt": res["pooled_not_exempt"],
                                     "by_grade_not_exempt": res["by_grade_not_exempt"]}}
    out.write_text(json.dumps(model, separators=(",", ":")) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "comets_not_exempt"}, indent=1))
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} kB, {len(model['trees'])} trees)")
    if report:
        report.write_text(json.dumps(res, indent=1))
        I[["zone", "id", "label", "grade", "name", "sep", "slow", "wide", "exempt", "rate", "base", "rb", "blind"]] \
            .to_parquet(report.with_suffix(".parquet"))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=("prepare", "train"))
    ap.add_argument("--work", required=True, help="folder for the as-if-unknown suspects tables and labels")
    ap.add_argument("--zones", default=",".join(ZONES))
    ap.add_argument("--out", default=str(MODEL_OUT))
    ap.add_argument("--report", help="held-out numbers as JSON (and the held-out scores next to it as .parquet)")
    a = ap.parse_args(argv)
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    zones = a.zones.split(",")
    if a.step == "prepare":
        prepare(work, zones)
    else:
        train(work, zones, Path(a.out), Path(a.report) if a.report else None)


if __name__ == "__main__":
    sys.exit(main())
