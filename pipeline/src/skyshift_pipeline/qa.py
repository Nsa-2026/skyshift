"""Step `qa`: a quality grade per photo (green / amber / red, with reasons) from what dots and calib already wrote.

NASA's header QA doesn't separate the bad photos (14 Nov: every photo has FINAST 0 and L1/L2DQAFLG "Pass", the bad ones
too), so the grade comes from our own measurements, each against the zone's other photos of the same detector:
- dots per photo against the median of the same detector and visit (SAA passages: a D6 photo 1.2° from the SAA had
  4.6× the dots, OUTLIER at 57% of the peaks, 34 saturated streaks; D5 photos within 2° had 1.6–1.9×);
- the PSF of bright unsaturated dots (S/N > 50): median FWHM from second moments and median PSF/aperture flux ratio
  (visit-3 D6 artefacts, mostly a session's first exposure: FWHM 3.8–4.7″ against 9.5″, PSF/aperture 0.51 against 1.02);
- the share of those bright dots that are much sharper than the detector's stars, and how many of the sharp ones have
  OUTLIER at the peak (2026W19_2A_0152_1 D6: 52% sharp, 194 of 247 with OUTLIER). Needs dot columns: read when the dot
  files are on local disk (the tiled run's slim mirror, a local root; QA_SHAPES); never OUTLIER alone (MPC asteroids
  carry it on 52% of their dots);
- astrometry against Gaia: calib's per-photo scatter after its distortion fit (calib_photos.parquet; centroid and ePSF;
  hunt: 2 of 3,225 photos at 0.99″ and 0.74″ against a 0.16″ median, the same two light curves flag at −2.9σ and −3.6σ),
  else the dot file's own raw scatter against forced Gaia targets; the raw offset against Gaia from the dot file;
  photos with fewer than QA_MIN_GAIA matches are not checked (`astrometry_checked` false);
- SAA distance (header SGT_MIN_GCD2SAA) and saturated streaks (header `L2 SATSTRKS`).
Thresholds: config QA_*. Grade = the worst reason's level.

Reads only the dot files' Parquet footers (one ranged read each; the `skyshift_photo` metadata, contracts/dots.md) and
results/<zone>/calib_photos.parquet, plus 5 dot columns when they are local. Writes results/<zone>/qa_photos.parquet
(one row per photo of the zone's photo list that has a dot file: obs_id, detector, grade, reasons, then the metrics)
and qa.json (counts per grade and detector, the worst photos, the thresholds). `red_photos` gives lightcurves the photos
to leave out (photo_ok false).
"""

from __future__ import annotations

import io
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import config as C
from . import log
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("qa")

PHOTOS_REL = "qa_photos.parquet"
REPORT_REL = "qa.json"
OUTLIER_BIT = 1 << 19                 # contracts/flags.md
SAT_BITS = 0x00008002                 # OVERFLOW | NONLINEAR
SHAPE_COLUMNS = ["snr", "a_arcsec", "b_arcsec", "flags", "flags_peak"]
GRADES = ("green", "amber", "red")
COLUMNS = ["obs_id", "detector", "grade", "reasons", "visit", "session", "mjd", "status", "n_dots", "dots_ratio",
           "psf_n", "fwhm_arcsec", "fwhm_ratio", "psf_ap", "psf_ap_ratio", "bright_n", "sharp_frac", "sharp_outlier_frac",
           "gaia_n", "offset_arcsec", "offset_rel_arcsec", "sigma_arcsec", "calib_n_stars", "scatter_arcsec",
           "scatter_psf_arcsec", "scatter_ratio", "astrometry_checked", "astrometry_source", "saa_deg", "satstrks",
           "match_nstars"]


# --- reading ---------------------------------------------------------------------------------------------------
def read_meta(store: Store, rel: str, tail: int | None = None) -> dict | None:
    """The `skyshift_photo` metadata of a dot file from its Parquet footer: one ranged read of the file's last `tail`
    bytes (a second one when the footer is longer). None without metadata; FileNotFoundError without the file."""
    tail = int(tail or C.QA_FOOTER_TAIL_BYTES)
    p = store.path(rel)
    b = store.fs.cat_file(p, start=-tail)
    if len(b) < 12 or b[-4:] != b"PAR1":
        raise ValueError(f"{rel}: not a Parquet file")
    n = struct.unpack("<I", b[-8:-4])[0] + 8
    if n > len(b):
        b = store.fs.cat_file(p, start=-n)
    md = pq.read_metadata(io.BytesIO(b"PAR1" + b[-n:])).metadata or {}
    raw = md.get(C.PHOTO_META_KEY.encode())
    return None if raw is None else json.loads(raw)


def _meta_or_status(store: Store, rel: str) -> tuple[dict | None, str]:
    try:
        m = read_meta(store, rel)
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:  # noqa: BLE001
        L.warning("qa: %s footer unreadable: %s", rel, e)
        return None, "unreadable"
    if m is None:
        return None, "no_meta"
    return m, str(m.get("status") or "done")


def shape_counts(store: Store, rel: str, fwhm_ref: float) -> tuple[int, int, int]:
    """(bright, sharp, sharp with OUTLIER at the peak) among a photo's unsaturated S/N > CALIB_PSF_MIN_SNR dots;
    sharp = FWHM from second moments < QA_SHARP_FWHM_FRAC × the detector's stellar FWHM."""
    src = store.path(rel) if store.local else store.fs.open(store.path(rel), "rb")
    try:
        t = pq.read_table(src, columns=SHAPE_COLUMNS, filters=[("snr", ">", float(C.CALIB_PSF_MIN_SNR))])
    finally:
        if not store.local:
            src.close()
    a = t.column("a_arcsec").to_numpy(zero_copy_only=False).astype(np.float64)
    b = t.column("b_arcsec").to_numpy(zero_copy_only=False).astype(np.float64)
    fl = t.column("flags").to_numpy(zero_copy_only=False).astype(np.int64)
    pk = t.column("flags_peak").to_numpy(zero_copy_only=False).astype(np.int64)
    ok = ((fl & SAT_BITS) == 0) & np.isfinite(a) & (b > 0)
    with np.errstate(invalid="ignore"):
        sharp = ok & (2.3548 * np.sqrt(a * b) < C.QA_SHARP_FWHM_FRAC * fwhm_ref)
    return int(ok.sum()), int(sharp.sum()), int((sharp & ((pk & OUTLIER_BIT) != 0)).sum())


def _get(d: dict | None, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _num(v) -> float:
    try:
        return float(v) if v is not None else np.nan
    except (TypeError, ValueError):
        return np.nan


def footer_rows(photos: pd.DataFrame, metas: list[tuple[dict | None, str]]) -> pd.DataFrame:
    """One row per photo: the photo list's keys and the footer's numbers (NaN where absent)."""
    rows = []
    for (o, det, visit, sess, mjd), (m, status) in zip(photos[["obs_id", "detector", "visit", "session", "mjd"]].itertuples(index=False), metas):
        h = _get(m, "header") or {}
        cal = _get(m, "calibration") or {}
        av = cal.get("astrometry_vs_gaia") or {}
        pm = cal.get("psf_moments") or {}
        pa = cal.get("psf_over_aperture_flux") or {}
        hm = _num(h.get("MJD-AVG"))
        sra, sdec = _num(av.get("sigma_ra_arcsec")), _num(av.get("sigma_dec_arcsec"))
        rows.append({
            "obs_id": str(o), "detector": int(det), "visit": int(visit), "session": int(sess),
            "mjd": hm if np.isfinite(hm) else float(mjd), "status": status,
            "n_dots": _num(m.get("n_dots")) if m else np.nan,
            "psf_n": _num(pm.get("n")), "fwhm_arcsec": _num(pm.get("fwhm_arcsec")),
            "psf_ap_n": _num(pa.get("n")), "psf_ap": _num(pa.get("median")),
            "gaia_n": _num(av.get("n")), "dra": _num(av.get("dra_cosdec_arcsec")), "ddec": _num(av.get("ddec_arcsec")),
            "sigma_arcsec": float(np.sqrt((sra ** 2 + sdec ** 2) / 2)),
            "saa_deg": _num(h.get("SGT_MIN_GCD2SAA")), "satstrks": _num(h.get("L2 SATSTRKS")),
            "match_nstars": _num(h.get("MATCH_NSTARS")), "finast": _num(h.get("FINAST")),
        })
    return pd.DataFrame(rows)


# --- metrics and grades --------------------------------------------------------------------------------------
def _ref(values: pd.Series, groups: pd.Series, ok: pd.Series, default: dict | float | None = None) -> np.ndarray:
    """Per row: the median of `values` over the ok rows of its group (≥ QA_MIN_PHOTOS of them), else `default`."""
    v = values.where(ok)
    med = v.groupby(groups).median()
    cnt = v.groupby(groups).count()
    med = med.where(cnt >= C.QA_MIN_PHOTOS)
    out = groups.map(med).to_numpy(np.float64)
    if default is not None:
        fb = groups.map(default).to_numpy(np.float64) if isinstance(default, dict) else np.full(len(groups), float(default))
        out = np.where(np.isfinite(out), out, fb)
    return out


def metrics(f: pd.DataFrame, calib_photos: pd.DataFrame | None) -> pd.DataFrame:
    """The ratios against the zone's other photos of the detector (dots: of the detector and visit) and the
    astrometry check (calib first, then the dot file's own)."""
    m = f.copy()
    det = m["detector"]
    done = m["status"] == "done"
    # dots per photo vs detector-and-visit median (detector median when the visit has too few photos)
    nd = m["n_dots"].astype(np.float64)
    dv = det.astype(str) + "_" + m["visit"].astype(str)
    ref_dv = _ref(nd, dv, done & (nd > 0))
    ref_d = _ref(nd, det, done & (nd > 0))
    with np.errstate(invalid="ignore", divide="ignore"):
        m["dots_ratio"] = np.where(done, nd / np.where(np.isfinite(ref_dv), ref_dv, ref_d), np.nan)
    # PSF size and PSF/aperture flux of bright dots vs the detector's
    psf_ok = done & (m["psf_n"] >= C.QA_MIN_BRIGHT)
    m["fwhm_ref"] = _ref(m["fwhm_arcsec"], det, psf_ok, C.QA_FWHM_REF_ARCSEC)
    m["fwhm_ratio"] = np.where(psf_ok, m["fwhm_arcsec"] / m["fwhm_ref"], np.nan)
    ap_ok = done & (m["psf_ap_n"] >= C.QA_MIN_BRIGHT)
    m["psf_ap_ratio"] = np.where(ap_ok, m["psf_ap"] / _ref(m["psf_ap"], det, ap_ok, C.QA_PSF_AP_REF), np.nan)
    # astrometry: raw offset (minus the detector's median offset) and scatter from the dot file
    g_ok = done & (m["gaia_n"] >= C.QA_MIN_GAIA)
    m["offset_arcsec"] = np.hypot(m["dra"], m["ddec"])
    m["offset_rel_arcsec"] = np.where(g_ok, np.hypot(m["dra"] - _ref(m["dra"], det, g_ok, 0.0),
                                                     m["ddec"] - _ref(m["ddec"], det, g_ok, 0.0)), np.nan)
    sig_ratio = m["sigma_arcsec"] / _ref(m["sigma_arcsec"], det, g_ok)
    # calib's scatter after its per-photo distortion fit (centroid and ePSF), the worse of the two ratios
    m["calib_n_stars"] = np.nan
    m["scatter_arcsec"] = np.nan
    m["scatter_psf_arcsec"] = np.nan
    if calib_photos is not None and len(calib_photos):
        cp = calib_photos.assign(obs_id=calib_photos["obs_id"].astype(str), detector=calib_photos["detector"].astype(int))
        cols = {"n_stars": "calib_n_stars", "scatter_corrected": "scatter_arcsec"}
        if "psf_scatter_corrected" in cp:
            cols["psf_scatter_corrected"] = "scatter_psf_arcsec"
        cp = cp[["obs_id", "detector", *cols]].rename(columns=cols).drop_duplicates(["obs_id", "detector"])
        m = m.drop(columns=list(cols.values())).merge(cp, on=["obs_id", "detector"], how="left")
        if "scatter_psf_arcsec" not in m:
            m["scatter_psf_arcsec"] = np.nan
        det = m["detector"]
        done = m["status"] == "done"
    c_ok = done & (m["calib_n_stars"] >= C.QA_MIN_GAIA)
    with np.errstate(invalid="ignore"):
        r_c = m["scatter_arcsec"] / _ref(m["scatter_arcsec"], det, c_ok & np.isfinite(m["scatter_arcsec"]))
        r_p = m["scatter_psf_arcsec"] / _ref(m["scatter_psf_arcsec"], det, c_ok & np.isfinite(m["scatter_psf_arcsec"]))
    r_cal = np.fmax(r_c.to_numpy(np.float64), r_p.to_numpy(np.float64))
    use_cal = c_ok.to_numpy() & np.isfinite(r_cal)
    use_own = ~use_cal & g_ok.to_numpy() & np.isfinite(sig_ratio.to_numpy(np.float64))
    m["scatter_ratio"] = np.where(use_cal, r_cal, np.where(use_own, sig_ratio, np.nan))
    m["astrometry_checked"] = use_cal | use_own
    m["astrometry_source"] = np.where(use_cal, "calib", np.where(use_own, "dots", ""))
    # the absolute scatter a red grade also needs: calib's (worse of the two), else the dot file's raw one
    m["_scatter_abs"] = np.where(use_cal, np.fmax(m["scatter_arcsec"], m["scatter_psf_arcsec"]),
                                 np.where(use_own, m["sigma_arcsec"], np.nan))
    return m


def grade(m: pd.DataFrame) -> pd.DataFrame:
    """grade (green | amber | red) and reasons (comma-separated, red ones first) from the metrics."""
    n = len(m)
    red: list[list[str]] = [[] for _ in range(n)]
    amb: list[list[str]] = [[] for _ in range(n)]

    def add(mask, name, to):
        for i in np.flatnonzero(np.asarray(mask, bool)):
            to[i].append(name)

    def col(c):
        return m[c].to_numpy(np.float64) if c in m else np.full(n, np.nan)

    with np.errstate(invalid="ignore"):
        st = m["status"].to_numpy(str)
        add(st == "done_finast", "finast", red)
        add(np.isin(st, ("no_meta", "unreadable")), "no_meta", amb)
        saa = col("saa_deg") < C.QA_SAA_DEG
        dr = col("dots_ratio")
        hi = np.where(saa, C.QA_DOTS_RATIO_RED_SAA, C.QA_DOTS_RATIO_RED)
        add(dr >= hi, "dots_excess", red)
        add((dr >= C.QA_DOTS_RATIO_AMBER) & (dr < hi), "dots_excess", amb)
        add(dr <= C.QA_DOTS_RATIO_LOW, "dots_low", amb)
        add(saa, "saa", amb)
        add(col("satstrks") >= C.QA_SATSTRKS_AMBER, "satstreaks", amb)
        fw = col("fwhm_ratio")
        add(fw < C.QA_FWHM_RATIO_RED[0], "fwhm_low", red)
        add((fw >= C.QA_FWHM_RATIO_RED[0]) & (fw < C.QA_FWHM_RATIO_AMBER[0]), "fwhm_low", amb)
        add(fw > C.QA_FWHM_RATIO_RED[1], "fwhm_high", red)
        add((fw <= C.QA_FWHM_RATIO_RED[1]) & (fw > C.QA_FWHM_RATIO_AMBER[1]), "fwhm_high", amb)
        pa = col("psf_ap_ratio")
        add(pa < C.QA_PSF_AP_RATIO_RED[0], "psf_ap_low", red)
        add((pa >= C.QA_PSF_AP_RATIO_RED[0]) & (pa < C.QA_PSF_AP_RATIO_AMBER[0]), "psf_ap_low", amb)
        add(pa > C.QA_PSF_AP_RATIO_RED[1], "psf_ap_high", red)
        add((pa <= C.QA_PSF_AP_RATIO_RED[1]) & (pa > C.QA_PSF_AP_RATIO_AMBER[1]), "psf_ap_high", amb)
        bn, sf, so = col("bright_n"), col("sharp_frac"), col("sharp_outlier_frac")
        enough = bn >= C.QA_MIN_BRIGHT
        art = enough & (sf >= C.QA_SHARP_FRAC_RED) & (so >= C.QA_SHARP_OUTLIER_MIN)
        add(art, "sharp_artefacts", red)
        add(enough & ~art & (sf >= C.QA_SHARP_FRAC_AMBER), "sharp_artefacts", amb)
        sr, sa = col("scatter_ratio"), col("_scatter_abs")
        bad_sc = (sr >= C.QA_SCATTER_RATIO_RED) & (sa >= C.QA_SCATTER_MIN_RED_ARCSEC)
        add(bad_sc, "astrometry_scatter", red)
        add(~bad_sc & (sr >= C.QA_SCATTER_RATIO_AMBER), "astrometry_scatter", amb)
        off = col("offset_rel_arcsec")
        add(off >= C.QA_OFFSET_RED_ARCSEC, "astrometry_offset", red)
        add((off >= C.QA_OFFSET_AMBER_ARCSEC) & (off < C.QA_OFFSET_RED_ARCSEC), "astrometry_offset", amb)
    out = m.copy()
    out["grade"] = ["red" if r else ("amber" if a else "green") for r, a in zip(red, amb)]
    out["reasons"] = [",".join(dict.fromkeys(r + a)) for r, a in zip(red, amb)]
    return out


def table(g: pd.DataFrame) -> pd.DataFrame:
    """The qa_photos.parquet columns and types."""
    t = g.reindex(columns=COLUMNS)
    t["obs_id"] = t["obs_id"].astype(str)
    t["detector"] = t["detector"].astype(np.int64)
    for c in ("grade", "reasons", "status", "astrometry_source"):
        t[c] = t[c].fillna("").astype(str)
    t["visit"] = t["visit"].astype(np.int16)
    t["session"] = t["session"].astype(np.int32)
    t["astrometry_checked"] = t["astrometry_checked"].fillna(False).astype(bool)
    for c in COLUMNS:
        if c not in ("obs_id", "detector", "grade", "reasons", "status", "astrometry_source", "visit", "session",
                     "astrometry_checked", "mjd"):
            t[c] = t[c].astype(np.float32)
    return t.sort_values(["detector", "mjd", "obs_id"]).reset_index(drop=True)


def report(t: pd.DataFrame, zone: C.Zone, extra: dict) -> dict:
    per = {}
    for d, g in t.groupby("detector"):
        per[f"D{int(d)}"] = {k: int((g["grade"] == k).sum()) for k in GRADES}
    reasons: dict[str, dict[str, int]] = {}
    for gr in ("red", "amber"):
        s = t.loc[t["grade"] == gr, "reasons"].str.split(",").explode()
        reasons[gr] = {k: int(v) for k, v in s[s != ""].value_counts().items()}
    sev = t.assign(_o=t["grade"].map({"red": 0, "amber": 1, "green": 2}))
    worst = sev[sev["_o"] < 2].sort_values(["_o", "scatter_ratio", "dots_ratio"], ascending=[True, False, False])
    cols = ["obs_id", "detector", "grade", "reasons", "visit", "n_dots", "dots_ratio", "fwhm_ratio", "psf_ap_ratio",
            "sharp_frac", "scatter_ratio", "scatter_arcsec", "offset_rel_arcsec", "saa_deg", "satstrks"]
    worst_rows = json.loads(worst[cols].head(C.QA_REPORT_WORST).to_json(orient="records", double_precision=4))
    n = len(t)
    counts = {k: int((t["grade"] == k).sum()) for k in GRADES}
    return {"zone": zone.name, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "photos": n,
            "counts": counts, "red_share": round(counts["red"] / n, 4) if n else None,
            "per_detector": per, "reasons": reasons,
            "astrometry_checked": int(t["astrometry_checked"].sum()),
            "astrometry_source": {k: int(v) for k, v in t["astrometry_source"].replace("", "none").value_counts().items()},
            "worst": worst_rows, "thresholds": thresholds(), **extra}


def thresholds() -> dict:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(C).items()
            if k.startswith("QA_") and not k.startswith("QA_REPORT") and not callable(v)}


# --- the step ----------------------------------------------------------------------------------------------------
def use_shapes(dots_store: Store) -> bool:
    mode = str(C.QA_SHAPES).lower()
    return mode == "on" or (mode == "auto" and dots_store.local)


def compute(store: Store, zone: C.Zone, workers: int = 16, dots_store: Store | None = None) -> tuple[pd.DataFrame, dict]:
    """(qa table, stats). Footers (and dot columns) from `dots_store` (default `store`: the tiled run passes its local
    slim mirror), the photo list and calib_photos.parquet from `store`."""
    t0 = time.time()
    ds = dots_store or store
    photos = store.read_parquet(photolist_rel(zone.name), columns=["obs_id", "detector", "mjd", "visit", "session"])
    photos = photos.drop_duplicates(["obs_id", "detector"]).reset_index(drop=True)
    rels = [dots_rel(o, d) for o, d in zip(photos["obs_id"], photos["detector"])]
    threads = max(1, min(C.QA_THREADS, 4 * int(workers or 1)))
    with ThreadPoolExecutor(threads) as ex:
        metas = list(ex.map(lambda r: _meta_or_status(ds, r), rels))
    t_foot = time.time() - t0
    f = footer_rows(photos, metas)
    have = (f["status"] != "missing").to_numpy()
    missing = int((~have).sum())
    f, rels = f[have].reset_index(drop=True), [r for r, h in zip(rels, have) if h]
    cprel = results_rel(zone.name, "calib_photos.parquet")
    cp = store.read_parquet(cprel) if store.exists(cprel) else None
    m = metrics(f, cp)
    # dot shapes (sharp artefacts) where the files are local
    m["bright_n"] = np.nan
    m["sharp_frac"] = np.nan
    m["sharp_outlier_frac"] = np.nan
    shapes = use_shapes(ds) and len(m)
    t1 = time.time()
    if shapes:
        todo = np.flatnonzero((m["status"] == "done").to_numpy() & (m["psf_n"].fillna(0) >= 1).to_numpy())

        def one(i):
            try:
                return shape_counts(ds, rels[i], float(m["fwhm_ref"].iat[i]))
            except Exception as e:  # noqa: BLE001
                L.warning("qa: %s dot columns unreadable: %s", rels[i], e)
                return (0, 0, 0)

        with ThreadPoolExecutor(threads) as ex:
            res = list(ex.map(one, todo))
        if len(todo):
            r = np.array(res, np.float64).reshape(-1, 3)
            m.loc[todo, "bright_n"] = r[:, 0]
            with np.errstate(invalid="ignore", divide="ignore"):
                m.loc[todo, "sharp_frac"] = np.where(r[:, 0] > 0, r[:, 1] / r[:, 0], np.nan)
                m.loc[todo, "sharp_outlier_frac"] = np.where(r[:, 1] > 0, r[:, 2] / r[:, 1], np.nan)
    g = grade(m)
    t = table(g)
    st = {"photos": int(len(photos)), "graded": int(len(t)), "missing_dot_files": missing,
          "counts": {k: int((t["grade"] == k).sum()) for k in GRADES},
          "red_share": round(float((t["grade"] == "red").mean()), 4) if len(t) else None,
          "calib_photos": cp is not None, "shapes": bool(shapes), "footer_s": round(t_foot, 1),
          "shapes_s": round(time.time() - t1, 1) if shapes else 0.0, "dots_root": ds.root}
    return t, st


def run(store: Store, zone: C.Zone, workers: int = 16, dots_store: Store | None = None) -> dict:
    t0 = time.time()
    t, st = compute(store, zone, workers, dots_store)
    store.write_parquet(results_rel(zone.name, PHOTOS_REL), t)
    rep = report(t, zone, {"missing_dot_files": st["missing_dot_files"], "calib_photos": st["calib_photos"],
                           "shapes": st["shapes"]})
    store.write_json(results_rel(zone.name, REPORT_REL), rep)
    st["reasons_red"] = rep["reasons"]["red"]
    st["wall_s"] = round(time.time() - t0, 1)
    L.info("qa %s: %s", zone.name, st)
    return st


def run_safe(store: Store, zone: C.Zone, workers: int = 16, dots_store: Store | None = None) -> dict:
    """run, but a failure is logged and returned as {"error": ...} (in `all` and the tiled run: grades are an extra)."""
    try:
        return run(store, zone, workers, dots_store)
    except Exception as e:  # noqa: BLE001
        L.exception("qa %s failed; going on without new grades", zone.name)
        return {"error": repr(e)}


def red_photos(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    """results/<zone>/qa_photos.parquet's red photos (obs_id, detector, mjd), or None when qa hasn't run."""
    rel = results_rel(zone.name, PHOTOS_REL)
    try:
        if not C.QA_LIGHTCURVES or not store.exists(rel):
            return None
        t = store.read_parquet(rel, columns=["obs_id", "detector", "grade", "mjd"])
    except Exception as e:  # noqa: BLE001
        L.warning("qa %s: %s unreadable (%s): no photos left out", zone.name, rel, e)
        return None
    return t.loc[t["grade"] == "red", ["obs_id", "detector", "mjd"]].reset_index(drop=True)
