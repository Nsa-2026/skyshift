"""Known-body forced photometry on the test photos (27 Nov, agent "known-forced"): the same code the dots worker runs
with SKYSHIFT_KNOWN_FORCED=1 (knownforced.measure), applied to the bench, hold-out and wide-sample photos from the
photo cache without re-running the whole extraction, plus the controls and the stacking report.

    R="uv run --project pipeline python scripts/rerun/known_forced.py"
    $R photos --out photos.parquet                         # bench + hold-out + wide photos (zone, session, visit, β)
    $R obsids --photos photos.parquet --out obs_ids.txt    # exposure ids for knownlist --photos
    $R measure --photos photos.parquet --list <known list dir> --out <root> [--dots-root bench-full,holdout-full]
               [--workers 3]                                # <root>/known/, known_c45/, known_c90/, known_else/, stats
    $R report --photos photos.parquet --root <root> --out report.json

measure, per photo: the photo from the cache, extraction v2's dark-hole mask and photo context (dots.mask_dark_holes,
photo_context: the worker's arrays), the glow model (for the glow flag), the photo's dots and forced rows from an
existing combined run (--dots-root) or, without one, forced rows computed from the sky lists (no dots: dot columns
NaN). Then knownforced.measure on the list rows (known/), on the same rows moved 45″ and 90″ east (known_c45/,
known_c90/: chance), and at the places other test photos of the same zone and detector, more than a day apart, put
their known bodies (known_else/: the body is elsewhere by then). CPU seconds of each part per photo in stats.parquet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "RAYON_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SKYSHIFT_DATA", Path.home() / "skyshift-data"))
CONTROLS = {"known": 0.0, "known_c45": 45.0, "known_c90": 90.0}
ELSE_MIN_DAYS = 1.0
ELSE_MAX_PER_PHOTO = 400


def ecl_lat(ra, dec) -> np.ndarray:
    e = np.radians(23.4392911)
    a, d = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    return np.degrees(np.arcsin(np.sin(d) * np.cos(e) - np.cos(d) * np.sin(e) * np.sin(a)))


# ---------------------------------------------------------------- photos

def cmd_photos(a) -> None:
    sys.path.insert(0, str(HERE))
    import rerun as RR
    parts = []
    for name, f in (("bench", HERE / "bench_targets.json"), ("holdout", HERE / "holdout_targets.json")):
        for zone, rows, t in RR.bench_photos(DATA, json.loads(Path(f).read_text())["targets"]):
            parts.append(rows.assign(zone=zone, set=name))
    w = pd.read_parquet(DATA / "rerun/wide-sample/sample.parquet")
    parts.append(w.assign(set="wide"))
    cols = ["set", "zone", "obs_id", "detector", "mjd", "session", "visit", "s_ra", "s_dec", "s_region", "s3_key"]
    ph = pd.concat([p[[c for c in cols if c in p]] for p in parts], ignore_index=True)
    ph = ph.drop_duplicates(["obs_id", "detector"]).reset_index(drop=True)
    ph["ecl_lat"] = ecl_lat(ph["s_ra"], ph["s_dec"])
    cache = DATA / "photo-cache"
    ph["path"] = [str(cache / Path(k).name) for k in ph["s3_key"]]
    ph["cached"] = [Path(p).exists() for p in ph["path"]]
    ph.to_parquet(a.out)
    print(ph.groupby("set").agg(photos=("obs_id", "size"), cached=("cached", "sum")).to_string())


def cmd_obsids(a) -> None:
    ph = pd.read_parquet(a.photos)
    ph = ph[ph["cached"]]
    Path(a.out).write_text("\n".join(sorted(set(ph["obs_id"]))) + "\n")
    print(len(set(ph["obs_id"])), "exposures")


# ---------------------------------------------------------------- measure

_W: dict = {}


def _init(list_dir: str, out: str, dots_roots: list[str], else_rows_path: str) -> None:
    os.environ["SKYSHIFT_KNOWN_LIST"] = list_dir
    from skyshift_pipeline import config as C
    C.KNOWN_LIST_DIR = list_dir
    _W.update({"out": Path(out), "dots_roots": [Path(d) for d in dots_roots if d], "else": None,
               "else_path": else_rows_path})


def _else_rows():
    if _W["else"] is None:
        _W["else"] = pd.read_parquet(_W["else_path"]) if _W["else_path"] and Path(_W["else_path"]).exists() \
            else pd.DataFrame()
    return _W["else"]


def _find(name: str) -> Path | None:
    for r in _W["dots_roots"]:
        p = r / name
        if p.exists():
            return p
    return None


_SIDE = ("_ext", "_peaks", "_streaks", "_cond", "_stamps", "_known")


def _find_dots(fn: str) -> Path | None:
    """The photo's dot file in a dots root: the v2 dots folder (dots_*, not a side file's folder)."""
    for r in _W["dots_roots"]:
        for d in sorted(r.glob("dots_*")):
            if d.is_dir() and not d.name.endswith(_SIDE) and (d / fn).exists():
                return d / fn
    return None


def _write(df, meta, rel: Path) -> int:
    from skyshift_pipeline import knownforced as KF
    b = KF.table_bytes(df, meta)
    p = _W["out"] / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    tmp.write_bytes(b)
    tmp.replace(p)
    return len(b)


def measure_photo(r: dict) -> dict:
    from skyshift_core.glow import fit_glow

    from skyshift_pipeline import config as C
    from skyshift_pipeline import dots as D
    from skyshift_pipeline import knownforced as KF
    from skyshift_pipeline.cond import glow_region
    from skyshift_pipeline.sky import Footprint, offset_radec
    from skyshift_pipeline.store import Store

    obs, det = str(r["obs_id"]), int(r["detector"])
    st = {"obs_id": obs, "detector": det, "zone": r["zone"], "set": r["set"], "ecl_lat": float(r["ecl_lat"])}
    out = _W["out"]
    fn = f"{obs}D{det}.parquet"
    if (out / "known" / fn).exists() and (out / "known_else" / fn).exists():
        st["status"] = "skipped"
        return st
    try:
        c0 = time.process_time()
        l2 = D.load_photo(r["path"])
        st["read_s"] = time.process_time() - c0
        if D.bad_finast(l2.header.get("FINAST")):
            st["status"] = "finast"
            return st
        c0 = time.process_time()
        l2, _, pre = D.mask_dark_holes(l2)
        ctx = D.photo_context(l2, pre=pre)
        st["context_s"] = time.process_time() - c0
        c0 = time.process_time()
        gl = fit_glow(l2.data, l2.flags, ctx.noise, epsf=getattr(l2, "epsf", None), r_max=C.GLOW_R_MAX_PX,
                      min_peak_snr=C.GLOW_MIN_PEAK_SNR, max_stars=C.GLOW_MAX_STARS)
        extras = {}
        extras["glow_px"], extras["glow_stars"] = glow_region(gl)
        st["glow_s"] = time.process_time() - c0
        dp = _find_dots(fn)
        dots = pd.read_parquet(dp, columns=["x", "y", "flux_ujy", "flux_psf_ujy", "snr"]) if dp else None
        fp = _find(f"forced/{fn}")
        c0 = time.process_time()
        if fp:
            forced = pd.read_parquet(fp, columns=["x", "y", "flux_psf_ujy", "ref_id"])
            st["forced_from"] = "file"
        else:
            ref, zobj, origin = D.forced_reference(Store(str(DATA)), l2, None)
            forced = D.forced_table(l2, ctx, obs, det, ref, zobj) if ref is not None else None
            st["forced_from"] = origin
        st["forced_s"] = time.process_time() - c0
        st["dots_from"] = "file" if dp else "none"
        st["n_dots"] = -1 if dots is None else int(len(dots))
        st["n_forced"] = -1 if forced is None else int(len(forced))
        store = Store(str(DATA))
        c0 = time.process_time()
        rows, info = KF.photo_rows(store, obs, det)
        st["list_s"] = time.process_time() - c0
        st["list_kinds"] = json.dumps(info.get("kinds"))
        if rows is None:
            st["status"] = "no_list"
            return st
        for kind, shift in CONTROLS.items():
            rr = rows.copy()
            if shift:
                rr["ra"], rr["dec"] = offset_radec(rr["ra"].to_numpy(float), rr["dec"].to_numpy(float), shift, 0.0)
            c0 = time.process_time()
            df, mi = KF.measure(l2, ctx, extras, rr, dots, forced)
            st[f"{kind}_s"] = time.process_time() - c0
            st[f"{kind}_rows"] = int(len(df))
            meta = {**info, **mi, "control_shift_arcsec": shift}
            st[f"{kind}_bytes"] = _write(df, meta, Path(kind) / fn)
            if kind == "known":
                st.update({"rows_list": mi.get("rows_list"), "off_photo": mi.get("off_photo"),
                           "smeared": mi.get("smeared"), "off_edge": mi.get("off_edge")})
        # elsewhere: other test photos' bodies (same zone, detector, > 1 day apart) at their places, here
        er = _else_rows()
        if len(er):
            e = er[(er["zone"] == r["zone"]) & (er["detector"] == det) & (np.abs(er["mjd_ref"] - float(r["mjd"]))
                                                                         > ELSE_MIN_DAYS)]
            if len(e):
                fpr = Footprint.from_s_region(r["s_region"])
                e = e[fpr.edge_distance_deg(e["ra"].to_numpy(float), e["dec"].to_numpy(float)) > 0]
            e = e.drop_duplicates("name").head(ELSE_MAX_PER_PHOTO).copy()
            if len(e):  # a fixed place: no motion, no parallax, at this photo's time
                e["dra_arcsec_h"], e["ddec_arcsec_h"], e["delta_au"] = 0.0, 0.0, np.nan
                e["mjd_ref"] = float(r["mjd"])
                e["list_row"] = np.arange(len(e), dtype=np.int32)
            df, mi = KF.measure(l2, ctx, extras, e if len(e) else rows.iloc[:0], dots, forced)
        else:
            df, mi = KF.measure(l2, ctx, extras, rows.iloc[:0], dots, forced)
        _write(df, {**mi, "control": "elsewhere"}, Path("known_else") / fn)
        st["else_rows"] = int(len(df))
        st["status"] = "done"
    except Exception as ex:  # noqa: BLE001
        import traceback
        st["status"] = "failed"
        st["error"] = f"{type(ex).__name__}: {ex}"[:300] + traceback.format_exc()[-600:]
    return st


def cmd_measure(a) -> None:
    ph = pd.read_parquet(a.photos)
    ph = ph[ph["cached"]]
    if a.sets:
        ph = ph[ph["set"].isin(a.sets.split(","))]
    if a.limit:
        ph = ph.head(a.limit)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    else_path = out / "else_rows.parquet"
    if not else_path.exists():  # every test photo's list rows (geocentric) for the elsewhere control
        from skyshift_pipeline.knownlist import KNOWN_LIST_COLUMNS
        parts = []
        for kind in ("asteroids", "comets", "planets"):
            for f in sorted(Path(a.list).glob(f"{kind}/*.parquet")):
                t = pd.read_parquet(f, columns=["obs_id", "detector", "kind", "name", "mjd_ref", "ra", "dec",
                                                "v_pred", "sigma_arcsec"])
                parts.append(t[t["obs_id"].isin(set(ph["obs_id"]))])
        er = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(KNOWN_LIST_COLUMNS))
        zone = ph.drop_duplicates(["obs_id", "detector"]).set_index(["obs_id", "detector"])["zone"]
        er["zone"] = [zone.get((o, int(d)), "") for o, d in zip(er["obs_id"], er["detector"])]
        for c, v in (("number", -1), ("cls", ""), ("dra_arcsec_h", 0.0), ("ddec_arcsec_h", 0.0), ("delta_au", np.nan),
                     ("r_au", np.nan), ("phase_deg", np.nan), ("u_param", -1), ("edge_deg", np.nan), ("h", np.nan),
                     ("g", np.nan)):
            er[c] = v
        er.to_parquet(else_path)
    rows = ph.to_dict("records")
    t0 = time.time()
    res = []
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init,
                             initargs=(str(Path(a.list).resolve()), str(out),
                                       [str(Path(d).expanduser()) for d in a.dots_root.split(",") if d],
                                       str(else_path))) as ex:
        futs = [ex.submit(measure_photo, r) for r in rows]
        for k, f in enumerate(as_completed(futs), 1):
            res.append(f.result())
            if k % 50 == 0 or k == len(futs):
                print(f"{k}/{len(futs)} photos, {time.time() - t0:.0f} s", flush=True)
    st = pd.DataFrame(res)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    st.to_parquet(out / f"stats_{stamp}.parquet")
    print(st["status"].value_counts().to_string())
    if "error" in st:
        print(st.loc[st["status"] == "failed", "error"].head(5).to_string())


# ---------------------------------------------------------------- static-sky template

TMPL_MAX_V = 22.5            # bodies whose place gets a template
TMPL_MIN_DAYS = 1.0          # template photos at least this far in time from the body's photo
TMPL_MIN_AWAY_ARCSEC = 30.0  # and the body that far from its place by then (rate × |Δt|)


def template_rows(root: Path, ph: pd.DataFrame) -> pd.DataFrame:
    """Every known/ row of a body (V < TMPL_MAX_V, on the frame) with its place seen from SPHEREx in its photo: the
    places to measure again, fixed, in the other photos (same zone and detector) when the body is elsewhere."""
    cols = ["obs_id", "detector", "kind", "name", "mjd", "ra", "dec", "v_pred", "rate_arcsec_h", "known_flag"]
    d = pd.concat([pd.read_parquet(f, columns=cols) for f in sorted((root / "known").glob("*.parquet"))],
                  ignore_index=True)
    d["detector"] = d["detector"].astype(int)
    d = d[(d["v_pred"] < TMPL_MAX_V) & ((d["known_flag"] & 0x1) == 0)].reset_index(drop=True)
    z = ph.drop_duplicates(["obs_id", "detector"]).set_index(["obs_id", "detector"])["zone"]
    d["zone"] = [z.get((o, int(x)), "") for o, x in zip(d["obs_id"], d["detector"])]
    return d.rename(columns={"obs_id": "src_obs", "mjd": "src_mjd"}).assign(tmpl_id=np.arange(len(d)))


def template_photo(r: dict) -> dict:
    """One photo: every template place of other photos' bodies (same zone, detector, > 1 day, the body > 30″ away),
    measured fixed (no motion, no parallax) with knownforced.measure; known_tmpl/<photo>, list_row = tmpl_id."""
    from skyshift_pipeline import dots as D
    from skyshift_pipeline import knownforced as KF
    from skyshift_pipeline.sky import Footprint

    obs, det = str(r["obs_id"]), int(r["detector"])
    st = {"obs_id": obs, "detector": det, "zone": r["zone"], "set": r["set"]}
    fn = f"{obs}D{det}.parquet"
    if (_W["out"] / "known_tmpl" / fn).exists():
        st["status"] = "skipped"
        return st
    try:
        t = _else_rows()
        e = t[(t["zone"] == r["zone"]) & (t["detector"] == det) &
              (np.abs(t["src_mjd"] - float(r["mjd"])) > TMPL_MIN_DAYS)]
        e = e[e["rate_arcsec_h"].abs() * np.abs(e["src_mjd"] - float(r["mjd"])) * 24.0 > TMPL_MIN_AWAY_ARCSEC]
        if len(e):
            fpr = Footprint.from_s_region(r["s_region"])
            e = e[fpr.edge_distance_deg(e["ra"].to_numpy(float), e["dec"].to_numpy(float)) > 0]
        st["tmpl_rows"] = int(len(e))
        if not len(e):
            st["status"] = "none"
            return st
        c0 = time.process_time()
        l2 = D.load_photo(r["path"])
        if D.bad_finast(l2.header.get("FINAST")):
            st["status"] = "finast"
            return st
        l2, _, pre = D.mask_dark_holes(l2)
        ctx = D.photo_context(l2, pre=pre)
        dp, fp = _find_dots(fn), _find(f"forced/{fn}")
        dots = pd.read_parquet(dp, columns=["x", "y", "flux_ujy", "flux_psf_ujy", "snr"]) if dp else None
        forced = pd.read_parquet(fp, columns=["x", "y", "flux_psf_ujy", "ref_id"]) if fp else None
        rows = pd.DataFrame({
            "obs_id": obs, "detector": det, "kind": e["kind"].to_numpy(), "name": e["name"].to_numpy(), "number": -1,
            "cls": "", "mjd_ref": float(r["mjd"]), "ra": e["ra"].to_numpy(float), "dec": e["dec"].to_numpy(float),
            "dra_arcsec_h": 0.0, "ddec_arcsec_h": 0.0, "delta_au": np.nan, "r_au": np.nan, "phase_deg": np.nan,
            "v_pred": e["v_pred"].to_numpy(float), "sigma_arcsec": 0.1, "u_param": -1, "edge_deg": np.nan,
            "h": np.nan, "g": np.nan, "list_row": e["tmpl_id"].to_numpy(np.int64)})
        parts = []
        for k in range(0, len(rows), 15000):
            df, _ = KF.measure(l2, ctx, {}, rows.iloc[k:k + 15000], dots, forced)
            parts.append(df)
        df = pd.concat(parts, ignore_index=True)
        st["tmpl_s"] = time.process_time() - c0
        _write(df, {"control": "template"}, Path("known_tmpl") / fn)
        st["status"] = "done"
    except Exception as ex:  # noqa: BLE001
        import traceback
        st["status"] = "failed"
        st["error"] = f"{type(ex).__name__}: {ex}"[:300] + traceback.format_exc()[-600:]
    return st


def cmd_template(a) -> None:
    ph = pd.read_parquet(a.photos)
    ph = ph[ph["cached"]]
    out = Path(a.out)
    tp = out / "tmpl_rows.parquet"
    if not tp.exists():
        template_rows(out, ph).to_parquet(tp)
    rows = ph.to_dict("records")
    t0, res = time.time(), []
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init,
                             initargs=("", str(out), [str(Path(d).expanduser()) for d in a.dots_root.split(",") if d],
                                       str(tp))) as ex:
        futs = [ex.submit(template_photo, r) for r in rows]
        for k, f in enumerate(as_completed(futs), 1):
            res.append(f.result())
            if k % 200 == 0 or k == len(futs):
                print(f"{k}/{len(futs)} photos, {time.time() - t0:.0f} s", flush=True)
    st = pd.DataFrame(res)
    st.to_parquet(out / f"tmpl_stats_{time.strftime('%Y%m%dT%H%M%S')}.parquet")
    print(st["status"].value_counts().to_string())
    if "error" in st:
        print(st.loc[st["status"] == "failed", "error"].head(5).to_string())


# ---------------------------------------------------------------- report

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("photos")
    s.add_argument("--out", required=True)
    s = sub.add_parser("obsids")
    s.add_argument("--photos", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("measure")
    s.add_argument("--photos", required=True)
    s.add_argument("--list", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--dots-root", default="")
    s.add_argument("--workers", type=int, default=3)
    s.add_argument("--sets", default="")
    s.add_argument("--limit", type=int, default=0)
    s = sub.add_parser("template")
    s.add_argument("--photos", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--dots-root", default="")
    s.add_argument("--workers", type=int, default=3)
    s = sub.add_parser("report")
    s.add_argument("--photos", required=True)
    s.add_argument("--root", required=True)
    s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "report":
        sys.path.insert(0, str(HERE))
        import known_forced_report as KR
        KR.report(a)
        return
    {"photos": cmd_photos, "obsids": cmd_obsids, "measure": cmd_measure, "template": cmd_template}[a.cmd](a)


if __name__ == "__main__":
    main()
