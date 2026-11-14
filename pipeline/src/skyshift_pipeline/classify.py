"""Step `classify`: star / galaxy / unsure, spectral indices and a brown-dwarf-like flag per source.

Inputs (results/<zone>/ unless noted); only sources.parquet is required:
  sources.parquet    the 102-channel spectra (`spectra`)
  members.parquet + dots/…  the member dots' shape columns, when the dot files have them (dots extras):
                     a_arcsec, b_arcsec, psf_chi2_nu, flux_psf_ujy, flux_ap<r>_ujy (any number of apertures)
  crossids.parquet   Gaia / CatWISE / 2MASS matches per source_id (wide: gaia_sep_arcsec, gaia_g, gaia_bp_rp,
                     gaia_parallax, gaia_parallax_error, …); without it, a nearest-Gaia match from refcat/<zone>.parquet
Output: classes.parquet (one row per source; schema in contracts/dots.md, meaning in pipeline/CLAUDE.md).

Shape: per detector, each dot's size √(a·b), PSF-fit χ²_ν, aperture/PSF flux ratio and large/small aperture
ratio are divided by the stellar locus (median over dots with S/N >= CLASSIFY_LOCUS_MIN_SNR; Gaia stars with a
parallax when there are enough of them); per source the median over its dots with S/N >= CLASSIFY_SHAPE_MIN_SNR.
extended_score = mean of the available (ratio − 1) / tolerance terms: ~0 for a star, >= 2 clearly extended.
Class: Gaia parallax/error >= CLASSIFY_PLX_SNR_STAR → star; extended_score >= CLASSIFY_EXT_GALAXY → galaxy;
extended_score <= CLASSIFY_EXT_STAR with a Gaia match → star; everything else (including point-like without
Gaia: compact galaxies, quasars, brown dwarfs) → unsure.

Band labels (`label_*`, `labels`): an index's sig alone fires on the wrong objects (pilot: "water ice" on
M dwarfs and galaxies, "methane" on Gaia stars), so the web shows these booleans instead; band_labels()
has the guards, config LABEL_* the thresholds.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import config as C
from . import indices as IX
from . import log
from .sky import angsep
from .store import Store, dots_rel, results_rel

L = log.get("classify")

SHAPE_COLUMNS = ("a_arcsec", "b_arcsec", "psf_chi2_nu", "flux_psf_ujy")
AP_RE = re.compile(r"^flux_ap(\d+(?:[p.]\d+)?)_ujy$")


# --- shapes from the member dots -------------------------------------------------------------
def ap_radius(col: str) -> float:
    return float(AP_RE.match(col).group(1).replace("p", "."))


def load_shape_dots(store: Store, zone: C.Zone, workers: int = 16) -> pd.DataFrame | None:
    """Member dots with whatever shape columns their dot files have; None without members.parquet."""
    rel = results_rel(zone.name, "members.parquet")
    if not store.exists(rel):
        return None
    members = store.read_parquet(rel)
    groups = members.groupby(["obs_id", "detector"], observed=True, sort=True).indices
    sid_all = members["source_id"].to_numpy(np.int64)
    row_all = members["dot_row"].to_numpy(np.int64)

    def one(item):
        (obs_id, det), idx = item
        try:
            df = store.read_parquet(dots_rel(obs_id, int(det)))
        except FileNotFoundError:
            return None
        keep = [c for c in df.columns if c in SHAPE_COLUMNS or AP_RE.match(c) or c in ("snr", "flux_ujy")]
        rows = row_all[idx]
        ok = rows < len(df)
        part = df.iloc[rows[ok]][keep].reset_index(drop=True)
        part["source_id"] = sid_all[idx[ok]]
        part["detector"] = np.int8(det)
        return part

    with ThreadPoolExecutor(max_workers=max(4, min(workers, 32))) as ex:
        parts = [p for p in ex.map(one, groups.items()) if p is not None]
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True)


def dot_shape_ratios(d: pd.DataFrame) -> dict[str, np.ndarray]:
    """Raw per-dot shape quantities (before the locus): size, chi2, ap/psf, big/small aperture."""
    out = {}
    if "a_arcsec" in d and "b_arcsec" in d:
        with np.errstate(invalid="ignore"):
            out["size"] = np.sqrt(d["a_arcsec"].to_numpy(np.float64) * d["b_arcsec"].to_numpy(np.float64))
    if "psf_chi2_nu" in d:
        out["chi2"] = d["psf_chi2_nu"].to_numpy(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        if "flux_psf_ujy" in d and "flux_ujy" in d:
            psf = d["flux_psf_ujy"].to_numpy(np.float64)
            out["cog_psf"] = np.where(psf > 0, d["flux_ujy"].to_numpy(np.float64) / psf, np.nan)
        aps = sorted((c for c in d.columns if AP_RE.match(c)), key=ap_radius)
        if len(aps) >= 2:
            small = d[aps[0]].to_numpy(np.float64)
            out["cog_ap"] = np.where(small > 0, d[aps[-1]].to_numpy(np.float64) / small, np.nan)
    return out


def locus_values(d: pd.DataFrame | None, gaia_star_ids: np.ndarray | None = None) -> pd.DataFrame:
    """The dots that can define the stellar locus (S/N >= CLASSIFY_LOCUS_MIN_SNR, finite positive value): quantity,
    detector, value, gaia (a Gaia star with a parallax). tiles.py collects them from every tile for one zone-wide locus."""
    cols = {"q": pd.Series(dtype=str), "detector": pd.Series(dtype=np.int64), "v": pd.Series(dtype=np.float64),
            "gaia": pd.Series(dtype=bool)}
    if d is None or not len(d):
        return pd.DataFrame(cols)
    raw = dot_shape_ratios(d)
    snr = d["snr"].to_numpy(np.float64) if "snr" in d else np.full(len(d), np.inf)
    det = d["detector"].to_numpy(np.int64)
    sid = d["source_id"].to_numpy(np.int64)
    is_gaia = np.isin(sid, gaia_star_ids) if gaia_star_ids is not None and len(gaia_star_ids) else np.zeros(len(d), bool)
    bright = snr >= C.CLASSIFY_LOCUS_MIN_SNR
    parts = [pd.DataFrame(cols)]
    for k, v in raw.items():
        m = np.isfinite(v) & (v > 0) & bright
        parts.append(pd.DataFrame({"q": k, "detector": det[m], "v": v[m], "gaia": is_gaia[m]}))
    return pd.concat(parts, ignore_index=True)


def stellar_locus(vals: pd.DataFrame) -> dict[tuple[str, int], float]:
    """Per (quantity, detector): the median over Gaia stars' dots (CLASSIFY_LOCUS_MIN_DOTS of them), else over all."""
    out: dict[tuple[str, int], float] = {}
    for (k, dd), g in vals.groupby(["q", "detector"], sort=True):
        ref = g["v"].to_numpy(np.float64)[g["gaia"].to_numpy(bool)]
        if ref.size < C.CLASSIFY_LOCUS_MIN_DOTS:
            ref = g["v"].to_numpy(np.float64)
        if ref.size:
            out[(str(k), int(dd))] = float(np.median(ref))
    return out


def gaia_star_ids(gaia: pd.DataFrame, sids: np.ndarray) -> np.ndarray:
    """Sources whose Gaia parallax is significant (CLASSIFY_PLX_SNR_STAR): the stars the locus prefers."""
    plx_snr = gaia["gaia_parallax"].to_numpy() / gaia["gaia_parallax_error"].to_numpy()
    with np.errstate(invalid="ignore"):
        return sids[plx_snr >= C.CLASSIFY_PLX_SNR_STAR]


def shape_features(d: pd.DataFrame | None, sources: np.ndarray, gaia_star_ids: np.ndarray | None = None,
                   locus: dict[tuple[str, int], float] | None = None) -> pd.DataFrame:
    """Per source: size_ratio, psf_chi2_ratio, cog_ratio (each / the stellar locus), n_shape_dots, extended_score.
    locus: stellar_locus fitted elsewhere (the whole zone, tiles.py); else from these dots."""
    N = sources.size
    out = pd.DataFrame({"source_id": sources})
    for c in ("size_ratio", "psf_chi2_ratio", "cog_ratio", "extended_score"):
        out[c] = np.full(N, np.nan, np.float32)
    out["n_shape_dots"] = np.zeros(N, np.int16)
    if d is None or not len(d):
        return out
    raw = dot_shape_ratios(d)
    if not raw:
        return out
    snr = d["snr"].to_numpy(np.float64) if "snr" in d else np.full(len(d), np.inf)
    det = d["detector"].to_numpy(np.int64)
    sid = d["source_id"].to_numpy(np.int64)
    pos = np.searchsorted(sources, sid)
    pos = np.clip(pos, 0, max(N - 1, 0))
    known = (N > 0) & (sources[pos] == sid)
    vote = known & (snr >= C.CLASSIFY_SHAPE_MIN_SNR)
    if locus is None:
        locus = stellar_locus(locus_values(d, gaia_star_ids))

    norm = {}
    for k, v in raw.items():
        r = np.full(v.size, np.nan)
        for dd in np.unique(det):
            if (k, int(dd)) not in locus:
                continue
            m = (det == dd) & np.isfinite(v) & (v > 0)
            r[m] = v[m] / locus[(k, int(dd))]
        norm[k] = r

    def per_source(r):
        m = vote & np.isfinite(r)
        if not m.any():
            return np.full(N, np.nan)
        s = pd.Series(r[m]).groupby(pos[m]).median()
        o = np.full(N, np.nan)
        o[s.index.to_numpy()] = s.to_numpy()
        return o

    size = per_source(norm["size"]) if "size" in norm else np.full(N, np.nan)
    chi2 = per_source(norm["chi2"]) if "chi2" in norm else np.full(N, np.nan)
    cogs = [per_source(norm[k]) for k in ("cog_ap", "cog_psf") if k in norm]
    cog = cogs[0] if cogs else np.full(N, np.nan)
    if len(cogs) == 2:
        cog = np.where(np.isfinite(cogs[0]), cogs[0], cogs[1])
    with np.errstate(invalid="ignore", divide="ignore"):
        terms = np.stack([(size - 1) / C.CLASSIFY_SIZE_TOL, (cog - 1) / C.CLASSIFY_COG_TOL,
                          np.log(chi2) / np.log(C.CLASSIFY_CHI2_FACTOR)])
        have = np.isfinite(terms)
        ext = np.where(have.any(0), np.nansum(np.where(have, terms, 0), 0) / np.maximum(have.sum(0), 1), np.nan)
    nd = np.bincount(pos[vote], minlength=N) if N else np.zeros(0, np.int64)
    out["size_ratio"] = size.astype(np.float32)
    out["psf_chi2_ratio"] = chi2.astype(np.float32)
    out["cog_ratio"] = cog.astype(np.float32)
    out["extended_score"] = ext.astype(np.float32)
    out["n_shape_dots"] = np.minimum(nd, np.iinfo(np.int16).max).astype(np.int16)
    return out


# --- Gaia ------------------------------------------------------------------------------------
GAIA_COLS = {  # our name: accepted input names (first found wins)
    "gaia_sep_arcsec": ("gaia_sep_arcsec", "gaia_sep", "sep_gaia_arcsec"),
    "gaia_g": ("gaia_g", "gaia_phot_g_mean_mag", "phot_g_mean_mag", "g_mag", "gmag", "g"),
    "gaia_bp_rp": ("gaia_bp_rp", "bp_rp"),
    "gaia_parallax": ("gaia_parallax", "parallax", "plx"),
    "gaia_parallax_error": ("gaia_parallax_error", "parallax_error", "plx_err", "e_plx"),
}


def _pick(df: pd.DataFrame, names) -> np.ndarray | None:
    for n in names:
        if n in df.columns:
            return pd.to_numeric(df[n], errors="coerce").to_numpy(np.float64)
    return None


def gaia_from_crossids(cx: pd.DataFrame) -> pd.DataFrame | None:
    """Wide crossids (one row per source_id, gaia_* columns) -> our Gaia columns; None if it has no Gaia."""
    if "source_id" not in cx:
        return None
    if "catalog" in cx.columns:  # long format: keep the Gaia rows, nearest first
        cx = cx[cx["catalog"].astype(str).str.lower().str.contains("gaia")]
        sep = _pick(cx, ("sep_arcsec", "gaia_sep_arcsec", "sep"))
        if sep is not None:
            cx = cx.assign(_s=sep).sort_values("_s").drop(columns="_s")
        cx = cx.drop_duplicates("source_id")
        cx = cx.rename(columns={"sep_arcsec": "gaia_sep_arcsec"})
    out = pd.DataFrame({"source_id": cx["source_id"].to_numpy(np.int64)})
    found = 0
    for ours, names in GAIA_COLS.items():
        v = _pick(cx, names)
        found += v is not None
        out[ours] = v if v is not None else np.nan
    if found == 0:
        return None
    # a row without a separation or a G is "no match"
    if out["gaia_sep_arcsec"].isna().all() and out["gaia_g"].isna().all():
        return None
    return out


def gaia_from_refcat(rc: pd.DataFrame, src: pd.DataFrame) -> pd.DataFrame | None:
    """Nearest Gaia row of the zone's reference catalogue within CLASSIFY_GAIA_MATCH_ARCSEC."""
    from scipy.spatial import cKDTree

    if "catalog" in rc.columns:
        rc = rc[rc["catalog"].astype(str).str.lower().str.contains("gaia")]
    g = _pick(rc, GAIA_COLS["gaia_g"])
    ra = _pick(rc, ("gaia_ra", "ra"))
    dec = _pick(rc, ("gaia_dec", "dec"))
    if g is None or ra is None or dec is None:
        return None
    ok = np.isfinite(g) & np.isfinite(ra) & np.isfinite(dec)
    rc = rc[ok]
    if not len(rc) or not len(src):
        return None

    def xyz(r, d):
        r, d = np.deg2rad(r), np.deg2rad(d)
        return np.column_stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])

    tree = cKDTree(xyz(ra[ok], dec[ok]))
    chord = 2 * np.sin(np.deg2rad(C.CLASSIFY_GAIA_MATCH_ARCSEC / 3600) / 2)
    dist, j = tree.query(xyz(src["ra"].to_numpy(), src["dec"].to_numpy()), distance_upper_bound=chord)
    m = np.isfinite(dist)
    out = pd.DataFrame({"source_id": src["source_id"].to_numpy(np.int64)[m]})
    out["gaia_sep_arcsec"] = angsep(src["ra"].to_numpy()[m], src["dec"].to_numpy()[m], ra[ok][j[m]], dec[ok][j[m]]) * 3600
    for ours, names in GAIA_COLS.items():
        if ours == "gaia_sep_arcsec":
            continue
        v = _pick(rc, names)
        out[ours] = v[j[m]] if v is not None else np.nan
    return out


def load_gaia(store: Store, zone: C.Zone, src: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Gaia columns aligned with src (NaN where unmatched) and where they came from."""
    base = pd.DataFrame({"source_id": src["source_id"].to_numpy(np.int64)})
    g, origin = None, "none"
    rel = results_rel(zone.name, "crossids.parquet")
    if store.exists(rel):
        g = gaia_from_crossids(store.read_parquet(rel))
        origin = "crossids" if g is not None else origin
    if g is None and store.exists(f"refcat/{zone.name}.parquet"):
        g = gaia_from_refcat(store.read_parquet(f"refcat/{zone.name}.parquet"), src)
        origin = "refcat" if g is not None else origin
    if g is None:
        for c in GAIA_COLS:
            base[c] = np.nan
        return base, origin
    g = g.drop_duplicates("source_id")
    out = base.merge(g, on="source_id", how="left")
    return out, origin


# --- classification --------------------------------------------------------------------------
def classify_frame(src: pd.DataFrame, shape: pd.DataFrame, gaia: pd.DataFrame) -> pd.DataFrame:
    """All arrays aligned on src's rows. Returns classes (one row per source)."""
    flux, err = IX.stack(src["flux_ujy"]), IX.stack(src["err_ujy"])
    out = pd.DataFrame({"source_id": src["source_id"].to_numpy(np.int64)})
    mags = IX.magnitudes(flux, err)
    for k, v in mags.items():
        out[k] = v
    with np.errstate(invalid="ignore"):
        out["j_m46_ab"] = (mags["mag_ab_j"] - mags["mag_ab_m46"]).astype(np.float32)
    for k, v in IX.all_indices(flux, err).items():
        out[k] = v
    for c in ("size_ratio", "psf_chi2_ratio", "cog_ratio", "n_shape_dots", "extended_score"):
        out[c] = shape[c].to_numpy()
    for c in GAIA_COLS:
        out[c] = gaia[c].to_numpy(np.float64).astype(np.float32)

    ext = out["extended_score"].to_numpy(np.float64)
    gmag = out["gaia_g"].to_numpy(np.float64)
    has_gaia = np.isfinite(out["gaia_sep_arcsec"].to_numpy(np.float64)) | np.isfinite(gmag)
    with np.errstate(invalid="ignore", divide="ignore"):
        plx_snr = out["gaia_parallax"].to_numpy(np.float64) / out["gaia_parallax_error"].to_numpy(np.float64)
    out["gaia_parallax_snr"] = plx_snr.astype(np.float32)
    star_plx = plx_snr >= C.CLASSIFY_PLX_SNR_STAR
    cls = np.full(len(out), "unsure", dtype=object)
    cls[(ext >= C.CLASSIFY_EXT_GALAXY) & ~star_plx] = "galaxy"
    cls[(ext <= C.CLASSIFY_EXT_STAR) & has_gaia] = "star"
    cls[star_plx] = "star"
    out["class"] = cls

    # brown-dwarf-like
    def det(name, min_depth):
        v, s = out[f"{name}_depth"].to_numpy(np.float64), out[f"{name}_sig"].to_numpy(np.float64)
        with np.errstate(invalid="ignore"):
            return (s >= C.INDEX_SIG) & (v >= min_depth)

    ev = {
        "ch4_33": det("ch4_33", C.BD_CH4_33_MIN_DEPTH),
        "ch4_16": det("ch4_16", C.BD_CH4_16_MIN_DEPTH),
        "h2o_14": det("h2o_14", C.BD_H2O_MIN_DEPTH),
        "h2o_19": det("h2o_19", C.BD_H2O_MIN_DEPTH),
    }
    methane_or_water = ev["ch4_33"] | ev["ch4_16"] | ev["h2o_14"] | ev["h2o_19"]
    with np.errstate(invalid="ignore"):
        red = out["j_m46_ab"].to_numpy(np.float64) > C.BD_MIN_J_M46_AB
        faint_gaia = ~has_gaia | (gmag > C.BD_GAIA_FAINT_G) | (gmag - out["mag_ab_j"].to_numpy(np.float64) > C.BD_MIN_G_J_AB)
    sat = src["saturated"].to_numpy(bool) if "saturated" in src else np.zeros(len(out), bool)
    out["brown_dwarf_like"] = methane_or_water & red & faint_gaia & (cls != "galaxy") & ~sat
    names = np.array(list(ev))
    bits = np.stack([ev[k] for k in names], 1)
    out["bd_evidence"] = [",".join(names[b]) for b in bits]
    lab = band_labels(out, sat)
    for k, v in lab.items():
        out[k] = v
    lnames = np.array(LABELS)
    lbits = np.stack([lab[f"label_{k}"] for k in LABELS], 1)
    out["labels"] = [",".join(lnames[b]) for b in lbits]
    return out


LABELS = ("h2o_ice", "co2_ice", "co_ice", "ch4", "h2o_vapour", "co_23", "pah_33")


def band_labels(out: pd.DataFrame, saturated: np.ndarray) -> dict[str, np.ndarray]:
    """{label_<name>: bool} from the index columns, class and Gaia columns of a classes frame.

    Every label: the band at sig >= INDEX_SIG with depth (excess) >= LABEL_MIN_DEPTH, source not saturated.
    - ices (H2O 3.05, CO2 4.27, CO 4.67 µm): not a galaxy, point-like; no Gaia or Gaia BP − RP <
      LABEL_ICE_MAX_BP_RP (cool M dwarfs' water vapour absorbs at 2.7–3.2 µm); no water vapour at 1.4 / 1.9 µm
      (sig < LABEL_SUPPORT_SIG); reddened by the cloud (J − [4.6] AB >= LABEL_ICE_MIN_J_M46); CO ice also
      without the CO 2.3 µm bandhead (cool-star CO gas lands at 4.67 µm too).
    - methane (CH4 3.3 or 1.6 µm): only T/Y brown dwarfs, invisible to Gaia: no Gaia or G > LABEL_CH4_MIN_G,
      not a galaxy, red (J − [4.6] AB > BD_MIN_J_M46_AB), and a second band agreeing (the other CH4 band
      or H2O 1.4 / 1.9 µm at sig >= LABEL_SUPPORT_SIG).
    - water vapour (H2O 1.4 or 1.9 µm, in M/L dwarfs and cool giants) and the CO 2.3 µm bandhead: not a galaxy.
    - PAH 3.3 µm emission: not a star (galaxy or unsure).
    """
    def col(name):
        return out[name].to_numpy(np.float64) if name in out else np.full(len(out), np.nan)

    def fires(name):
        kind = C.INDEX_WINDOWS[name][0]
        v = col(f"{name}_{'depth' if kind == 'abs' else 'excess'}")
        with np.errstate(invalid="ignore"):
            return (col(f"{name}_sig") >= C.INDEX_SIG) & (v >= C.LABEL_MIN_DEPTH[name]) & ~saturated

    def sig(name, at=C.LABEL_SUPPORT_SIG):
        with np.errstate(invalid="ignore"):
            return col(f"{name}_sig") >= at

    cls = out["class"].to_numpy()
    galaxy, star = cls == "galaxy", cls == "star"
    g, bp_rp, jm46, ext = col("gaia_g"), col("gaia_bp_rp"), col("j_m46_ab"), col("extended_score")
    has_gaia = np.isfinite(col("gaia_sep_arcsec")) | np.isfinite(g)
    vapour_any = sig("h2o_14") | sig("h2o_19")
    with np.errstate(invalid="ignore"):
        point = ~(ext > C.LABEL_ICE_MAX_EXT)
        not_cool = ~has_gaia | (bp_rp < C.LABEL_ICE_MAX_BP_RP)
        ice_ok = ~galaxy & point & not_cool & ~vapour_any & (jm46 >= C.LABEL_ICE_MIN_J_M46)
        no_gaia_or_faint = ~has_gaia | (g > C.LABEL_CH4_MIN_G)
        red = jm46 > C.BD_MIN_J_M46_AB
    ch4_ok = no_gaia_or_faint & ~galaxy & red
    return {
        "label_h2o_ice": fires("h2o_ice_305") & ice_ok,
        "label_co2_ice": fires("co2_ice_427") & ice_ok,
        "label_co_ice": fires("co_ice_467") & ice_ok & ~sig("co_23"),
        "label_ch4": ch4_ok & ((fires("ch4_33") & (sig("ch4_16") | vapour_any))
                               | (fires("ch4_16") & (sig("ch4_33") | vapour_any))),
        "label_h2o_vapour": (fires("h2o_14") | fires("h2o_19")) & ~galaxy,
        "label_co_23": fires("co_23") & ~galaxy,
        "label_pah_33": fires("pah_33") & ~star,
    }


def run(store: Store, zone: C.Zone, workers: int = 16, locus: dict | None = None,
        dots: pd.DataFrame | None = None) -> dict:
    """locus: a zone-wide stellar_locus (tiles.py); dots: load_shape_dots' result when already loaded."""
    t0 = time.time()
    src = store.read_parquet(results_rel(zone.name, "sources.parquet"))
    src = src.sort_values("source_id").reset_index(drop=True)
    sids = src["source_id"].to_numpy(np.int64)
    gaia, origin = load_gaia(store, zone, src)
    gaia_stars = gaia_star_ids(gaia, sids)
    t1 = time.time()
    if dots is None:
        dots = load_shape_dots(store, zone, workers)
    shape_cols = sorted(set(dot_shape_ratios(dots)) if dots is not None else [])
    t2 = time.time()
    shape = shape_features(dots, sids, gaia_stars, locus)
    out = classify_frame(src, shape, gaia)
    t3 = time.time()
    nbytes = store.write_parquet(results_rel(zone.name, "classes.parquet"), out)
    vc = out["class"].value_counts()
    st = {
        "sources": int(len(out)),
        "star": int(vc.get("star", 0)), "galaxy": int(vc.get("galaxy", 0)), "unsure": int(vc.get("unsure", 0)),
        "brown_dwarf_like": int(out["brown_dwarf_like"].sum()),
        "gaia_from": origin, "gaia_matched": int(np.isfinite(out["gaia_g"]).sum()),
        "shape_inputs": shape_cols, "shape_dots": 0 if dots is None else int(len(dots)),
        **{f"n_{k[:-4]}": int((out[k] >= C.INDEX_SIG).sum()) for k in out.columns if k.endswith("_sig")},
        **{k: int(out[k].sum()) for k in out.columns if k.startswith("label_")},
        "classes_bytes": int(nbytes),
        "load_s": round(t2 - t0, 1), "compute_s": round(t3 - t2, 1), "wall_s": round(time.time() - t0, 1),
    }
    L.info("classify %s: %s", zone.name, st)
    return st
