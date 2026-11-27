"""The known-forced report (scripts/rerun/known_forced.py report): depth of stacked forced fluxes of known asteroids,
the controls, the flux check, the named cases, crowding and cost. Everything from <root>/known*/ files and the photo
table; nothing is tuned on the hold-out or the wide sample (the detector template comes from the bench only)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

V_BINS = [-99, 15, 16, 17, 18, 19, 20, 21, 22, 23, 99]
V_LABELS = ["<15", "15–16", "16–17", "17–18", "18–19", "19–20", "20–21", "21–22", "22–23", ">23"]
BETA_BINS = [0, 5, 15, 30, 45, 60, 80, 90.01]
BETA_LABELS = ["0–5", "5–15", "15–30", "30–45", "45–60", "60–80", "80–90"]
AREA_DEG2 = (2040 * 6.152 / 3600.0) ** 2
DOUBT = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x40 | 0x1000  # off_edge masked sat_core sat_guard glow blend_star psf_fail
NAMED = ["98P", "135P", "3I", "306P", "Titan", "Rhea", "Iapetus", "Dione", "Tethys", "Enceladus", "Mimas", "Hyperion",
         "Janus", "Epimetheus", "Phoebe", "Saturn", "Triton", "Neptune", "Galatea"]


def load(root: Path, kind: str, photos: pd.DataFrame) -> pd.DataFrame:
    files = sorted((root / kind).glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    ph = photos[["obs_id", "detector", "set", "zone", "session", "visit", "ecl_lat"]].copy()
    ph["detector"] = ph["detector"].astype(int)
    df["detector"] = df["detector"].astype(int)
    df = df.merge(ph, on=["obs_id", "detector"], how="left")
    smear = (df["known_flag"].to_numpy() & 0x80) != 0
    df["f"] = np.where(smear, df["flux_smear_ujy"], df["flux_psf_ujy"])
    df["e"] = np.where(smear, df["flux_smear_err_ujy"], df["flux_psf_err_ujy"])
    df["snr"] = df["f"] / df["e"]
    df["ok"] = np.isfinite(df["snr"]) & (df["e"] > 0)
    df["clean"] = df["ok"] & ((df["known_flag"].to_numpy() & DOUBT) == 0)
    df["vbin"] = pd.cut(df["v_pred"], V_BINS, labels=V_LABELS, right=False)
    return df


def template(bench: pd.DataFrame) -> dict[int, float]:
    """Per detector, the median flux (µJy) of a V = 15 asteroid: bright, clean bench asteroids scaled by 10^0.4(V−15)."""
    b = bench[(bench["kind"] == "asteroid") & bench["clean"] & (bench["v_pred"] < 17.5) & (bench["snr"] > 10)]
    return {int(d): float(np.median(g["f"] * 10 ** (0.4 * (g["v_pred"] - 15.0)))) for d, g in b.groupby("detector")}


def stacks(df: pd.DataFrame, keys: list[str], tmpl: dict[int, float]) -> pd.DataFrame:
    """Inverse-variance stacks of the amplitude A (flux / template(detector); A = 10^−0.4(V−15) expected) over keys:
    A, σ_A, S/N, n, predicted S/N from V."""
    d = df[df["ok"]].copy()
    t = d["detector"].map(tmpl).to_numpy(float)
    w = (t / d["e"].to_numpy(float)) ** 2
    d["wa"] = w * d["f"].to_numpy(float) / t
    d["w"] = w
    g = d.groupby(keys, observed=True)
    out = pd.DataFrame({"sw": g["w"].sum(), "swa": g["wa"].sum(), "n": g.size(), "v": g["v_pred"].median(),
                        "ndet": g["detector"].nunique()})
    out["A"] = out["swa"] / out["sw"]
    out["sA"] = 1.0 / np.sqrt(out["sw"])
    out["snr"] = out["A"] / out["sA"]
    out["snr_pred"] = 10 ** (-0.4 * (out["v"] - 15.0)) / out["sA"]
    out["vbin"] = pd.cut(out["v"], V_BINS, labels=V_LABELS, right=False)
    return out.reset_index()


def per_det_stacks(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    d = df[df["ok"]].copy()
    d["w"] = 1.0 / d["e"] ** 2
    d["wf"] = d["w"] * d["f"]
    g = d.groupby(keys + ["detector"], observed=True)
    out = pd.DataFrame({"f": g["wf"].sum() / g["w"].sum(), "e": 1 / np.sqrt(g["w"].sum()), "n": g.size(),
                        "v": g["v_pred"].median()})
    out["snr"] = out["f"] / out["e"]
    out["vbin"] = pd.cut(out["v"], V_BINS, labels=V_LABELS, right=False)
    return out.reset_index()


def vtable(s: pd.DataFrame, col: str = "snr") -> list[dict]:
    rows = []
    for b, g in s.groupby("vbin", observed=True):
        x = g[col].to_numpy(float)
        x = x[np.isfinite(x)]
        if not len(x):
            continue
        r = {"V": str(b), "n": int(len(x)), "median": round(float(np.median(x)), 2), "gt3": round(float((x > 3).mean()), 3),
             "gt5": round(float((x > 5).mean()), 3)}
        if "snr_pred" in g:
            r["pred_median"] = round(float(np.median(g["snr_pred"])), 2)
            r["meas_over_pred"] = round(float(np.median(g["snr"]) / np.median(g["snr_pred"])), 2) \
                if np.median(g["snr_pred"]) > 0 else None
            r["n_photos_median"] = int(np.median(g["n"]))
        rows.append(r)
    return rows


def v_at(s: pd.DataFrame, level: float = 5.0) -> dict:
    """V where the median S/N and the share above `level` cross (linear in V over 0.25-mag bins)."""
    out = {}
    s = s[np.isfinite(s["v"]) & np.isfinite(s["snr"])]
    edges = np.arange(14.0, 24.01, 0.25)
    mids, med, share = [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        g = s[(s["v"] >= a) & (s["v"] < b)]
        if len(g) >= 5:
            mids.append((a + b) / 2)
            med.append(np.median(g["snr"]))
            share.append((g["snr"] > level).mean())
    mids, med, share = map(np.asarray, (mids, med, share))

    def cross(y, thr):
        for k in range(1, len(y)):
            if y[k - 1] >= thr > y[k]:
                return round(float(mids[k - 1] + (y[k - 1] - thr) / (y[k - 1] - y[k]) * (mids[k] - mids[k - 1])), 2)
        return None

    out["median_snr_crosses"] = cross(med, level)
    out["share_crosses_50pct"] = cross(share, 0.5)
    return out


def pulls(s: pd.DataFrame) -> dict:
    x = s["snr"].to_numpy(float)
    x = x[np.isfinite(x)]
    if not len(x):
        return {"n": 0}
    mad = 1.4826 * np.median(np.abs(x - np.median(x)))
    return {"n": int(len(x)), "mean": round(float(x.mean()), 3), "median": round(float(np.median(x)), 3),
            "std": round(float(x.std()), 3), "robust_std": round(float(mad), 3),
            "gt3": round(float((x > 3).mean()), 4), "gt5": round(float((x > 5).mean()), 4),
            "lt_m3": round(float((x < -3).mean()), 4), "n_gt5": int((x > 5).sum())}


def position_check(known: pd.DataFrame, photos: pd.DataFrame) -> dict:
    import glob
    import os
    data = Path(os.environ.get("SKYSHIFT_DATA", Path.home() / "skyshift-data"))
    parts = []
    for z in sorted(set(known["zone"].dropna())):
        for src in ("mpc", "planets"):
            for f in glob.glob(str(data / "cache" / src / z / "run_*.json")):
                pp = json.load(open(f)).get("per_photo") or {}
                if pp and pp.get("obs_id"):
                    parts.append(pd.DataFrame(pp).assign(src=src))
    if not parts:
        return {}
    c = pd.concat(parts, ignore_index=True).drop_duplicates(["obs_id", "detector", "name"])
    c["detector"] = c["detector"].astype(int)
    m = known.merge(c, on=["obs_id", "detector", "name"], suffixes=("", "_cache"))
    from skyshift_pipeline.sky import angsep
    m["sep"] = angsep(m["ra"], m["dec"], m["ra_cache"], m["dec_cache"]) * 3600.0
    out = {"matched": int(len(m)), "known_rows_in_cached_zones": int(known["zone"].isin(set(m["zone"])).sum())}
    for k, g in m.groupby("kind"):
        s = g["sep"].to_numpy(float)
        out[k] = {"n": int(len(s)), "median": round(float(np.median(s)), 3), "p90": round(float(np.percentile(s, 90)), 3),
                  "p99": round(float(np.percentile(s, 99)), 3), "max": round(float(s.max()), 2)}
    return out


TMPL_BAD = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000  # off_edge masked sat_core sat_guard psf_fail (a star, glow: static, kept)


def template_diff(root: Path, known: pd.DataFrame, tmpl: dict[int, float]) -> dict:
    """Static-sky template: each body's place (seen from SPHEREx in its photo) measured fixed in the other test photos
    of its zone and detector > 1 day away, the body > 30″ off (known_forced.py template). Forced − template flux
    (point ePSF both; trailed rows left out) stacked like the plain fluxes; the null: one template photo − the mean
    of the others at the same place, stacked over the same keys."""
    tr = root / "tmpl_rows.parquet"
    files = sorted((root / "known_tmpl").glob("*.parquet"))
    if not tr.exists() or not files:
        return {}
    rows = pd.read_parquet(tr, columns=["tmpl_id", "src_obs", "detector", "name"])
    m = pd.concat([pd.read_parquet(f, columns=["list_row", "flux_psf_ujy", "flux_psf_err_ujy", "known_flag"])
                   for f in files], ignore_index=True)
    m = m[np.isfinite(m["flux_psf_ujy"]) & (m["flux_psf_err_ujy"] > 0) & ((m["known_flag"] & TMPL_BAD) == 0)].copy()
    m["w"] = 1.0 / m["flux_psf_err_ujy"] ** 2
    m["wf"] = m["w"] * m["flux_psf_ujy"]
    m = m.sort_values(["list_row", "flux_psf_err_ujy"])
    g = m.groupby("list_row")
    t = pd.DataFrame({"t_sw": g["w"].sum(), "t_swf": g["wf"].sum(), "t_n": g.size(),
                      "j_f": g["flux_psf_ujy"].first(), "j_e": g["flux_psf_err_ujy"].first()})
    t["t_f"] = t["t_swf"] / t["t_sw"]
    t["t_e"] = 1.0 / np.sqrt(t["t_sw"])
    jw = 1.0 / t["j_e"] ** 2
    rest_sw = t["t_sw"] - jw
    with np.errstate(divide="ignore", invalid="ignore"):
        t["r_f"] = (t["t_swf"] - jw * t["j_f"]) / rest_sw
        t["r_e"] = 1.0 / np.sqrt(rest_sw)
    t = t.join(rows.set_index("tmpl_id"), how="inner")
    t["detector"] = t["detector"].astype(int)
    k = known[(known["kind"] == "asteroid") & ((known["known_flag"].to_numpy() & 0x80) == 0)].copy()
    k = k.merge(t.reset_index(drop=True), left_on=["obs_id", "detector", "name"], right_on=["src_obs", "detector", "name"],
                how="inner")
    srcok = np.isfinite(k["flux_psf_ujy"]) & (k["flux_psf_err_ujy"] > 0) & ((k["known_flag"].to_numpy() & TMPL_BAD) == 0)
    k = k[srcok].copy()
    out = {"template_rows_measured": int(len(m)), "places_with_template": int(len(t)),
           "known_rows_with_template": int(len(k)),
           "template_photos_per_place_median": float(np.median(t["t_n"])) if len(t) else None}
    d = k.copy()
    d["f"] = d["flux_psf_ujy"] - d["t_f"]
    d["e"] = np.sqrt(d["flux_psf_err_ujy"] ** 2 + d["t_e"] ** 2)
    d["snr"] = d["f"] / d["e"]
    d["ok"] = np.isfinite(d["snr"])
    plain = k.copy()
    plain["f"], plain["e"] = plain["flux_psf_ujy"], plain["flux_psf_err_ujy"]
    plain["snr"], plain["ok"] = plain["f"] / plain["e"], True
    nul = k[k["t_n"] >= 2].copy()
    nul["f"] = nul["j_f"] - nul["r_f"]
    nul["e"] = np.sqrt(nul["j_e"] ** 2 + nul["r_e"] ** 2)
    nul["snr"] = nul["f"] / nul["e"]
    nul["ok"] = np.isfinite(nul["snr"])
    for setname in ("bench", "holdout", "wide", "all"):
        dd = d if setname == "all" else d[d["set"] == setname]
        pp = plain if setname == "all" else plain[plain["set"] == setname]
        nn = nul if setname == "all" else nul[nul["set"] == setname]
        if not len(dd):
            continue
        r = {"rows": int(len(dd))}
        for lab, x in (("diff", dd), ("plain_same_rows", pp), ("null", nn)):
            if not len(x):
                continue
            single = x[x["ok"]].assign(v=x["v_pred"])
            vis = stacks(x, ["name", "zone", "visit"], tmpl)
            ses = stacks(x, ["name", "zone", "session"], tmpl)
            r[lab] = {"single_by_v": vtable(single), "visit_by_v": vtable(vis), "single_v_at_snr5": v_at(single),
                      "session_v_at_snr5": v_at(ses), "visit_v_at_snr5": v_at(vis), "visit_v_at_snr3": v_at(vis, 3.0),
                      "single_pull_v_gt_23": pulls(x[x["ok"] & (x["v_pred"] >= 23)]),
                      "visit_pull_v_gt_23": pulls(vis[vis["v"] >= 23]),
                      "single_pull": pulls(x[x["ok"]]), "visit_pull": pulls(vis)}
            if lab == "null":
                vis["nbin"] = pd.cut(vis["n"], [0, 3, 10, 30, 1000])
                r[lab]["visit_by_n"] = {str(b): pulls(gg) for b, gg in vis.groupby("nbin", observed=True)}
        # a full visit: each body-visit stack's S/N scaled by √(N / n) and divided by the null's error scale
        # (robust σ of the null's visit stacks with n > 3: the template photos are shared within a stack)
        nv = stacks(nn, ["name", "zone", "visit"], tmpl) if len(nn) else pd.DataFrame()
        k = pulls(nv[nv["n"] > 3]).get("robust_std") if len(nv) else None
        k = float(k) if k else 1.0
        vis = stacks(dd, ["name", "zone", "visit"], tmpl)
        r["error_scale_k"] = round(k, 3)
        r["projected"] = {}
        for N in (37, 100):
            pv = vis.assign(snr=vis["snr"] * np.sqrt(N / vis["n"]) / k)
            r["projected"][str(N)] = {"v_at_snr5": v_at(pv), "v_at_snr3": v_at(pv, 3.0), "by_v": vtable(pv)}
        out[setname] = r
    return out


def report(a) -> None:
    root, photos = Path(a.root), pd.read_parquet(a.photos)
    R: dict = {"root": str(root)}
    known = load(root, "known", photos)
    ctrl = {k: load(root, k, photos) for k in ("known_c45", "known_c90", "known_else")}
    ast = known[known["kind"] == "asteroid"]
    tmpl = template(ast[ast["set"] == "bench"])
    R["template_ujy_at_v15"] = {str(k): round(v, 1) for k, v in tmpl.items()}
    sel = {"all": ast, "clean": ast[ast["clean"]]}
    for setname in ("bench", "holdout", "wide"):
        out = {}
        for sname, d in sel.items():
            d = d[d["set"] == setname]
            single = d[d["ok"]].assign(v=d["v_pred"])
            ses = stacks(d, ["name", "zone", "session"], tmpl)
            vis = stacks(d, ["name", "zone", "visit"], tmpl)
            vdet = per_det_stacks(d, ["name", "zone", "visit"])
            out[sname] = {
                "rows": int(len(d)), "single_by_v": vtable(single), "session_by_v": vtable(ses),
                "visit_by_v": vtable(vis),
                "visit_by_v_per_detector": {int(k): vtable(g) for k, g in vdet.groupby("detector")},
                "single_v_at_snr5": v_at(single), "session_v_at_snr5": v_at(ses), "visit_v_at_snr5": v_at(vis),
                "visit_v_at_snr3": v_at(vis, 3.0),
                "visit_per_detector_v_at_snr5": {int(k): v_at(g) for k, g in vdet.groupby("detector")},
            }
        R[f"depth_{setname}"] = out
    # 1/√n: the stacks' formal errors against the single photos' and the control's scatter by n
    c45 = ctrl["known_c45"]
    c45a = c45[c45["kind"] == "asteroid"] if len(c45) else c45
    R["controls"] = {}
    for k, c in ctrl.items():
        if not len(c):
            continue
        ca = c[c["kind"] == "asteroid"]
        out = {}
        for sname, d in (("all", ca), ("clean", ca[ca["clean"]])):
            out[sname] = {"single": pulls(d[d["ok"]]),
                          "session": pulls(stacks(d, ["name", "zone", "session"], tmpl)),
                          "visit": pulls(stacks(d, ["name", "zone", "visit"], tmpl)),
                          "visit_per_detector": pulls(per_det_stacks(d, ["name", "zone", "visit"]))}
            vs = stacks(d, ["name", "zone", "visit"], tmpl)
            vs["nbin"] = pd.cut(vs["n"], [0, 3, 10, 30, 60, 1000])
            out[sname]["visit_by_n"] = {str(b): pulls(g) for b, g in vs.groupby("nbin", observed=True)}
            if "set" in d:
                out[sname]["visit_by_set"] = {s: pulls(stacks(g, ["name", "zone", "visit"], tmpl))
                                              for s, g in d.groupby("set")}
        R["controls"][k] = out
    # flux check: bright asteroids that are dots
    b = ast[ast["ok"] & (ast["dot_sep_arcsec"] < 2.0) & (ast["dot_snr"] > 20) &
            ((ast["known_flag"].to_numpy() & (DOUBT & ~0x1)) == 0) & ((ast["known_flag"].to_numpy() & 0x80) == 0)]
    r = (b["flux_psf_ujy"] / b["dot_flux_ujy"]).to_numpy(float)
    r = r[np.isfinite(r)]
    R["flux_check"] = {"n": int(len(r)), "median_forced_over_dot": round(float(np.median(r)), 4) if len(r) else None,
                       "robust_scatter": round(float(1.4826 * np.median(np.abs(r - np.median(r)))), 4) if len(r) else None,
                       "by_detector": {int(dd): round(float(np.median(g["flux_psf_ujy"] / g["dot_flux_ujy"])), 4)
                                       for dd, g in b.groupby("detector")}}
    # named cases
    named = {}
    for nm in NAMED:
        d = known[known["name"].str.contains(nm, regex=False)]
        if not len(d):
            continue
        for full, g in d.groupby("name"):
            gs = stacks(g, ["zone", "visit"], tmpl)
            gd = per_det_stacks(g, ["zone", "visit"])
            gss = per_det_stacks(g, ["zone", "session"])
            named[full] = {
                "kind": str(g["kind"].iloc[0]), "photos": int(len(g)), "measured": int(g["ok"].sum()),
                "v_pred": round(float(np.nanmedian(g["v_pred"])), 2) if np.isfinite(g["v_pred"]).any() else None,
                "rate_arcsec_h": round(float(np.median(g["rate_arcsec_h"])), 1),
                "single_snr_median": round(float(np.nanmedian(g["snr"])), 2) if g["ok"].any() else None,
                "single_snr_max": round(float(np.nanmax(g["snr"])), 2) if g["ok"].any() else None,
                "session_det_snr_best": round(float(gss["snr"].max()), 2) if len(gss) else None,
                "visit_combined_snr": [round(float(x), 2) for x in gs["snr"]],
                "visit_det_snr": {f"v{int(r.visit)}D{int(r.detector)}": round(float(r.snr), 2) for r in gd.itertuples()},
                "flags_any": int(np.bitwise_or.reduce(g["known_flag"].to_numpy())),
                "share_doubtful": round(float(((g["known_flag"].to_numpy() & DOUBT) != 0).mean()), 2),
                "peak_snr_median": round(float(np.nanmedian(g["peak_snr"])), 2),
                "peak_sep_median": round(float(np.nanmedian(g["peak_sep_arcsec"])), 2),
                "peaks_off_pred": int(((g["peak_snr"] > 5) & (g["peak_sep_arcsec"] > 6.0)).sum()),
                "peaks_off_pred_seps": [round(float(x), 1) for x in g.loc[(g["peak_snr"] > 5) &
                                                                           (g["peak_sep_arcsec"] > 6.0),
                                                                           "peak_sep_arcsec"].head(12)],
                "search_arcsec": float(g["search_arcsec"].median()),
                "dot_sep_median": round(float(np.nanmedian(g["dot_sep_arcsec"])), 2) if np.isfinite(g["dot_sep_arcsec"]).any() else None,
            }
    fast = known[(known["rate_arcsec_h"] > 150) & (known["kind"] == "asteroid")]
    fast_rows = []
    for full, g in fast.groupby("name"):
        gs = stacks(g, ["zone", "session"], tmpl)
        fast_rows.append({"name": full, "set": str(g["set"].iloc[0]), "photos": int(len(g)),
                          "v": round(float(np.nanmedian(g["v_pred"])), 2),
                          "rate": round(float(np.median(g["rate_arcsec_h"])), 0),
                          "trail_px": round(float(np.median(g["trail_px"])), 1),
                          "single_snr_median": round(float(np.nanmedian(g["snr"])), 2) if g["ok"].any() else None,
                          "point_snr_median": round(float(np.nanmedian(g["flux_psf_ujy"] / g["flux_psf_err_ujy"])), 2),
                          "session_snr": [round(float(x), 1) for x in gs["snr"]],
                          "peak_snr_median": round(float(np.nanmedian(g["peak_snr"])), 2)})
    R["named"] = named
    R["fast"] = sorted(fast_rows, key=lambda r: r["v"])
    # crowding: forced-list stars per deg² (the photo's forced rows; wide sample: computed from the sky lists)
    st = pd.concat([pd.read_parquet(f) for f in sorted(root.glob("stats_*.parquet"))], ignore_index=True)
    st = st[st["status"] == "done"].drop_duplicates(["obs_id", "detector"], keep="last")
    st["dens"] = st["n_forced"] / AREA_DEG2
    dens = st.set_index(["obs_id", "detector"])["dens"]
    dbins = [0, 500, 1500, 3000, 6000, 1e9]
    dlab = ["<500", "500–1.5k", "1.5k–3k", "3k–6k", ">6k"]
    crowd = {}
    for k, d in (("known", ast), ("known_c45", c45a)):
        if not len(d):
            continue
        d = d.copy()
        d["dens"] = [dens.get((o, int(x)), np.nan) for o, x in zip(d["obs_id"], d["detector"])]
        d["dbin"] = pd.cut(d["dens"], dbins, labels=dlab)
        out = {}
        for b2, g in d.groupby("dbin", observed=True):
            bl = (g["known_flag"].to_numpy() & 0x40) != 0
            out[str(b2)] = {"rows": int(len(g)), "blend_star_share": round(float(bl.mean()), 4),
                            "dot_near_share": round(float(((g["known_flag"].to_numpy() & 0x20) != 0).mean()), 4)}
            if k == "known_c45":
                out[str(b2)].update({
                    "single_pull_all": pulls(g[g["ok"]]), "single_pull_blended": pulls(g[g["ok"] & bl]),
                    "single_pull_unblended": pulls(g[g["ok"] & ~bl]),
                    "visit_pull_all": pulls(stacks(g, ["name", "zone", "visit"], tmpl)),
                    "visit_pull_unblended": pulls(stacks(g[~bl], ["name", "zone", "visit"], tmpl)),
                    "visit_pull_clean": pulls(stacks(g[g["clean"]], ["name", "zone", "visit"], tmpl))})
        crowd[k] = out
    R["crowding"] = crowd
    # cost per photo by ecliptic latitude
    st["beta"] = st["ecl_lat"].abs()
    st["bbin"] = pd.cut(st["beta"], BETA_BINS, labels=BETA_LABELS, right=False)
    st["known_cpu_s"] = st["list_s"] + st["known_s"]
    cost = {}
    for b2, g in st.groupby("bbin", observed=True):
        cost[str(b2)] = {"photos": int(len(g)), "rows_mean": round(float(g["known_rows"].mean()), 1),
                         "rows_p90": int(np.percentile(g["known_rows"], 90)), "rows_max": int(g["known_rows"].max()),
                         "kb_mean": round(float(g["known_bytes"].mean() / 1e3), 1),
                         "cpu_s_mean": round(float(g["known_cpu_s"].mean()), 3),
                         "cpu_s_p90": round(float(np.percentile(g["known_cpu_s"], 90)), 3),
                         "list_s_mean": round(float(g["list_s"].mean()), 3)}
    R["cost_by_beta"] = cost
    R["cost_all"] = {"photos": int(len(st)), "rows_mean": round(float(st["known_rows"].mean()), 1),
                     "kb_mean": round(float(st["known_bytes"].mean() / 1e3), 1),
                     "cpu_s_mean": round(float(st["known_cpu_s"].mean()), 3),
                     "context_s_mean": round(float(st["context_s"].mean()), 2)}
    # positions: the list moved to SPHEREx in the worker against the known step's cache (kete from SPHEREx per photo)
    R["position_check"] = position_check(known, photos)
    # V cut: what each cut would keep
    vc = {}
    for cut in (20, 21, 22, 23, 24, 99):
        keep = ~(ast["v_pred"] >= cut)
        vc[str(cut)] = {"rows_share": round(float(keep.mean()), 3)}
    R["v_cut_rows_share"] = vc
    R["template"] = template_diff(root, known, tmpl)
    Path(a.out).write_text(json.dumps(R, indent=1, default=str))
    print(json.dumps({k: R[k] for k in ("template_ujy_at_v15", "flux_check", "cost_all")}, indent=1))
