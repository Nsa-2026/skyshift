"""Step `classify`: star / galaxy / unsure, spectral indices and a brown-dwarf-like flag per source.

Inputs (results/<zone>/ unless noted); only sources.parquet is required:
  sources.parquet    the 102-channel spectra (`spectra`)
  members.parquet + dots/…  the member dots' shape columns, when the dot files have them (dots extras):
                     a_arcsec, b_arcsec, psf_chi2_nu, flux_psf_ujy, flux_ap<r>_ujy (any number of apertures)
  crossids.parquet   Gaia / CatWISE / 2MASS matches per source_id (wide: gaia_sep_arcsec, gaia_g, gaia_bp_rp,
                     gaia_parallax, gaia_parallax_error, …); without it, a nearest-Gaia match from refcat/<zone>.parquet
Output: classes.parquet (one row per source; schema in contracts/dots.md, meaning in pipeline/CLAUDE.md).

Shape: per detector, each dot's size √(a·b), PSF-fit χ²_ν, aperture/PSF flux ratio and large/small aperture
ratio are divided by the stellar locus at the dot's S/N (bins CLASSIFY_LOCUS_SNR_EDGES: a star's measured size is
~1.2× its S/N >= 20 value at S/N 8–12, which made faint stars "extended"; median over the bin's dots of Gaia stars
with a parallax when there are enough of them, else all its dots, else the S/N >= CLASSIFY_LOCUS_MIN_SNR locus);
per source the median over its dots with S/N >= CLASSIFY_SHAPE_MIN_SNR.
extended_score = mean of the available (ratio − 1) / tolerance terms: ~0 for a star, >= 2 clearly extended.
Class: Gaia parallax/error >= CLASSIFY_PLX_SNR_STAR → star; extended_score >= CLASSIFY_EXT_GALAXY → galaxy;
extended_score <= CLASSIFY_EXT_STAR with a Gaia match → star; everything else (including point-like without
Gaia: compact galaxies, quasars, brown dwarfs) → unsure.

Band labels (`label_*`, `labels`): an index's sig alone fires on the wrong objects (pilot: "water ice" on
M dwarfs and galaxies, "methane" on Gaia stars), so the web shows these booleans instead; band_labels()
has the guards, config LABEL_* the thresholds. Every label (and brown_dwarf_like's evidence) also needs its band
window measured in >= LABEL_MIN_VISITS visits (sources.parquet `visit_bits`; skipped for older tables without it):
one visit's glitch can't make a band.

brown_dwarf_like (16 Nov): water (h2o_14 or h2o_19) where an H2O index is measured well enough to show an L/T dwarf's
(water_testable), methane alone only where it isn't (a Y dwarf, faint at 1.4–1.9 µm); red, faint or absent in Gaia,
not a galaxy, not saturated; and not AGN-coloured in AllWISE (agn_veto, IRSA, the flagged rows only). `bd_veto` says
why a would-be brown dwarf isn't one (`no_water`, `agn_colours`), `allwise_w2_w3` the colour looked up.
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


def snr_bin(snr) -> np.ndarray:
    """The locus S/N bin of each dot: 0 .. len(CLASSIFY_LOCUS_SNR_EDGES) − 1 (below the first edge: −1)."""
    return np.searchsorted(np.asarray(C.CLASSIFY_LOCUS_SNR_EDGES, np.float64), np.asarray(snr, np.float64), side="right") - 1


def locus_values(d: pd.DataFrame | None, gaia_star_ids: np.ndarray | None = None) -> pd.DataFrame:
    """The dots that can define the stellar locus (S/N >= CLASSIFY_SHAPE_MIN_SNR, finite positive value): quantity,
    detector, value, gaia (a Gaia star with a parallax), b (snr_bin), bright (S/N >= CLASSIFY_LOCUS_MIN_SNR).
    tiles.py collects them from every tile for one zone-wide locus."""
    cols = {"q": pd.Series(dtype=str), "detector": pd.Series(dtype=np.int64), "v": pd.Series(dtype=np.float64),
            "gaia": pd.Series(dtype=bool), "b": pd.Series(dtype=np.int64), "bright": pd.Series(dtype=bool)}
    if d is None or not len(d):
        return pd.DataFrame(cols)
    raw = dot_shape_ratios(d)
    snr = d["snr"].to_numpy(np.float64) if "snr" in d else np.full(len(d), np.inf)
    det = d["detector"].to_numpy(np.int64)
    sid = d["source_id"].to_numpy(np.int64)
    is_gaia = np.isin(sid, gaia_star_ids) if gaia_star_ids is not None and len(gaia_star_ids) else np.zeros(len(d), bool)
    use = snr >= min(C.CLASSIFY_SHAPE_MIN_SNR, C.CLASSIFY_LOCUS_MIN_SNR)
    b = snr_bin(snr)
    bright = snr >= C.CLASSIFY_LOCUS_MIN_SNR
    parts = [pd.DataFrame(cols)]
    for k, v in raw.items():
        m = np.isfinite(v) & (v > 0) & use
        parts.append(pd.DataFrame({"q": k, "detector": det[m], "v": v[m], "gaia": is_gaia[m], "b": b[m], "bright": bright[m]}))
    return pd.concat(parts, ignore_index=True)


def _locus_median(g: pd.DataFrame) -> float | None:
    ref = g["v"].to_numpy(np.float64)[g["gaia"].to_numpy(bool)]
    if ref.size < C.CLASSIFY_LOCUS_MIN_DOTS:
        ref = g["v"].to_numpy(np.float64)
    return float(np.median(ref)) if ref.size else None


def stellar_locus(vals: pd.DataFrame) -> dict[tuple, float]:
    """(quantity, detector): the median over Gaia stars' bright dots (CLASSIFY_LOCUS_MIN_DOTS of them), else over all
    bright dots; (quantity, detector, S/N bin): the same over the bin's dots (bins with >= CLASSIFY_LOCUS_MIN_DOTS dots)."""
    out: dict[tuple, float] = {}
    if "bright" not in vals:  # older locus_values: bright dots only
        vals = vals.assign(bright=True, b=-1)
    for (k, dd), g in vals[vals["bright"].to_numpy(bool)].groupby(["q", "detector"], sort=True):
        v = _locus_median(g)
        if v is not None:
            out[(str(k), int(dd))] = v
    for (k, dd, b), g in vals[vals["b"].to_numpy(np.int64) >= 0].groupby(["q", "detector", "b"], sort=True):
        if len(g) >= C.CLASSIFY_LOCUS_MIN_DOTS:
            out[(str(k), int(dd), int(b))] = _locus_median(g)
    return out


def gaia_star_ids(gaia: pd.DataFrame, sids: np.ndarray) -> np.ndarray:
    """Sources whose Gaia parallax is significant (CLASSIFY_PLX_SNR_STAR): the stars the locus prefers."""
    plx_snr = gaia["gaia_parallax"].to_numpy() / gaia["gaia_parallax_error"].to_numpy()
    with np.errstate(invalid="ignore"):
        return sids[plx_snr >= C.CLASSIFY_PLX_SNR_STAR]


def shape_features(d: pd.DataFrame | None, sources: np.ndarray, gaia_star_ids: np.ndarray | None = None,
                   locus: dict[tuple, float] | None = None) -> pd.DataFrame:
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
    sb = snr_bin(snr)
    for k, v in raw.items():
        r = np.full(v.size, np.nan)
        for dd in np.unique(det):
            if (k, int(dd)) not in locus:
                continue
            m = (det == dd) & np.isfinite(v) & (v > 0)
            div = np.full(v.size, locus[(k, int(dd))])
            for b in np.unique(sb[m]):
                if (k, int(dd), int(b)) in locus:
                    div[sb == b] = locus[(k, int(dd), int(b))]
            r[m] = v[m] / div[m]
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


PHOT_COLS = ("j", "h", "k", "w1", "w2")  # crossids: 2MASS J, H, Ks and CatWISE W1, W2 (Vega)


def load_photometry(store: Store, zone: C.Zone, src: pd.DataFrame) -> pd.DataFrame:
    """2MASS and CatWISE magnitudes aligned with src (NaN where unmatched or without crossids.parquet)."""
    out = pd.DataFrame({"source_id": src["source_id"].to_numpy(np.int64)})
    rel = results_rel(zone.name, "crossids.parquet")
    x = store.read_parquet(rel) if store.exists(rel) else pd.DataFrame()
    have = [c for c in PHOT_COLS if c in x.columns]
    if have and "source_id" in x:
        out = out.merge(x[["source_id", *have]].drop_duplicates("source_id"), on="source_id", how="left")
    for c in PHOT_COLS:
        if c not in have:
            out[c] = np.nan
    return out


def vega_colours(out: pd.DataFrame, phot: pd.DataFrame | None) -> dict[str, np.ndarray]:
    """J − H, H − K, W1 − W2 (Vega) per source: the catalogues' (crossids) where both magnitudes exist, else from
    our own spectrum's windows (mag_ab_* + config AB_TO_VEGA)."""
    def col(df, c):
        return df[c].to_numpy(np.float64) if df is not None and c in df else np.full(len(out), np.nan)

    o = {b: col(out, f"mag_ab_{b}") + C.AB_TO_VEGA[b] for b in ("j", "h", "k")}
    with np.errstate(invalid="ignore"):
        syn = {"jh": o["j"] - o["h"], "hk": o["h"] - o["k"],
               "w1w2": col(out, "mag_ab_m34") - col(out, "mag_ab_m46") + C.AB_TO_VEGA["w1_w2"]}
        cat = {"jh": col(phot, "j") - col(phot, "h"), "hk": col(phot, "h") - col(phot, "k"),
               "w1w2": col(phot, "w1") - col(phot, "w2")}
    return {k: np.where(np.isfinite(cat[k]), cat[k], syn[k]) for k in syn}


# --- classification --------------------------------------------------------------------------
def classify_frame(src: pd.DataFrame, shape: pd.DataFrame, gaia: pd.DataFrame,
                   phot: pd.DataFrame | None = None) -> pd.DataFrame:
    """All arrays aligned on src's rows (phot: load_photometry, optional). Returns classes (one row per source)."""
    flux, err = IX.stack(src["flux_ujy"]), IX.stack(src["err_ujy"])
    visits = None
    if "visit_bits" in src and len(src):
        visits = IX.band_visits(np.stack([np.asarray(v, np.int64) for v in src["visit_bits"]]))
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
        seen = visits[name] >= C.LABEL_MIN_VISITS if visits is not None else True
        with np.errstate(invalid="ignore"):
            return (s >= C.INDEX_SIG) & (v >= min_depth) & seen

    ev = {
        "ch4_33": det("ch4_33", C.BD_CH4_33_MIN_DEPTH),
        "ch4_16": det("ch4_16", C.BD_CH4_16_MIN_DEPTH),
        "h2o_14": det("h2o_14", C.BD_H2O_MIN_DEPTH),
        "h2o_19": det("h2o_19", C.BD_H2O_MIN_DEPTH),
    }
    water = ev["h2o_14"] | ev["h2o_19"]
    methane = ev["ch4_33"] | ev["ch4_16"]
    testable = water_testable(out)
    with np.errstate(invalid="ignore"):
        red = out["j_m46_ab"].to_numpy(np.float64) > C.BD_MIN_J_M46_AB
        faint_gaia = ~has_gaia | (gmag > C.BD_GAIA_FAINT_G) | (gmag - out["mag_ab_j"].to_numpy(np.float64) > C.BD_MIN_G_J_AB)
    sat = src["saturated"].to_numpy(bool) if "saturated" in src else np.zeros(len(out), bool)
    rest = red & faint_gaia & (cls != "galaxy") & ~sat
    # water where it can be seen (BD_WATER_TESTABLE_ERR); methane alone only where it can't (16 Nov: 136 methane-only
    # labels, all galaxies or AGN; every ultracool dwarf with a Gaia parallax showed both water bands)
    out["brown_dwarf_like"] = (water | (methane & ~testable)) & rest
    out["bd_veto"] = np.where(methane & ~water & testable & rest, "no_water", "")
    out["allwise_w2_w3"] = np.full(len(out), np.nan, np.float32)  # agn_veto fills it for the brown_dwarf_like rows
    names = np.array(list(ev))
    bits = np.stack([ev[k] for k in names], 1)
    out["bd_evidence"] = [",".join(names[b]) for b in bits]
    lab = band_labels(out, sat, visits, vega_colours(out, phot))
    for k, v in lab.items():
        out[k] = v
    lnames = np.array(LABELS)
    lbits = np.stack([lab[f"label_{k}"] for k in LABELS], 1)
    out["labels"] = [",".join(lnames[b]) for b in lbits]
    return out


LABELS = ("h2o_ice", "co2_ice", "co_ice", "ch4", "h2o_vapour", "co_23", "pah_33")


def water_testable(out: pd.DataFrame) -> np.ndarray:
    """An H2O index (h2o_14 or h2o_19) is measured well enough to see an L/T dwarf's water: its depth error
    (|depth / sig|) <= BD_WATER_TESTABLE_ERR. h2o_14: band 1.34–1.45 µm = D2 channels 26–29, continuum 21–24 (D2) and
    32–35 (the D2/D3 join at 1.6 µm); h2o_19: band 1.84–1.95 µm = D3 channels 40–42, continuum 32–35 and 44–46."""
    errs = []
    for b in ("h2o_14", "h2o_19"):
        d = out[f"{b}_depth"].to_numpy(np.float64)
        s = out[f"{b}_sig"].to_numpy(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            errs.append(np.where(np.abs(s) > 0, np.abs(d / s), np.nan))
    with np.errstate(invalid="ignore"):
        return np.fmin(*errs) <= C.BD_WATER_TESTABLE_ERR


def allwise_tap(adql: str, uploads: dict):
    """IRSA (crossid.irsa_tap); tests replace it (conftest: offline)."""
    from .crossid import irsa_tap
    return irsa_tap(adql, uploads, C.HIGHPM_DEEP_TIMEOUT_S)


def agn_veto(out: pd.DataFrame, src: pd.DataFrame, tap=None) -> tuple[pd.DataFrame, dict]:
    """brown_dwarf_like rows whose nearest AllWISE entry (within BD_ALLWISE_ARCSEC; IRSA, one query) has an AGN's or a
    dusty galaxy's colours: W2 − W3 >= BD_AGN_W2_W3 with W3 S/N >= BD_AGN_W3_SNR and W1 − W2 < BD_AGN_MAX_W1_W2 (late T and
    Y dwarfs are W1 − W2 > 2) → not brown_dwarf_like, bd_veto `agn_colours`. allwise_w2_w3 is set for every looked-up row.
    A failed query vetoes nothing (stats checked false)."""
    st = {"checked": True, "rows": 0, "vetoed": 0}
    if not C.BD_AGN_VETO:
        return out, {**st, "checked": False}
    k = np.flatnonzero(out["brown_dwarf_like"].to_numpy(bool))
    st["rows"] = int(k.size)
    if not k.size:
        return out, st
    from .crossid import allwise_near
    pos = src.set_index("source_id").reindex(out["source_id"].to_numpy(np.int64)[k])
    try:
        aw = allwise_near(pos["ra"].to_numpy(np.float64), pos["dec"].to_numpy(np.float64), C.BD_ALLWISE_ARCSEC,
                          tap or allwise_tap)
    except Exception as e:  # noqa: BLE001 - the network: no veto, say so
        L.warning("classify: AllWISE colours for %d brown_dwarf_like rows failed (%r): no AGN veto", k.size, e)
        return out, {**st, "checked": False, "error": repr(e)[:200]}
    if not len(aw):
        return out, st
    aw = aw.sort_values("sep_arcsec").drop_duplicates("i")
    num = lambda c: pd.to_numeric(aw[c], errors="coerce").to_numpy(np.float64)  # noqa: E731
    w12, w23, snr3 = num("w1mpro") - num("w2mpro"), num("w2mpro") - num("w3mpro"), num("w3snr")
    with np.errstate(invalid="ignore"):
        veto = (w23 >= C.BD_AGN_W2_W3) & (snr3 >= C.BD_AGN_W3_SNR) & ~(w12 >= C.BD_AGN_MAX_W1_W2)
    rows = k[aw["i"].to_numpy(np.int64)]
    out = out.copy()
    v = out["allwise_w2_w3"].to_numpy(np.float32).copy()
    v[rows] = w23
    out["allwise_w2_w3"] = v
    bd = out["brown_dwarf_like"].to_numpy(bool).copy()
    bd[rows[veto]] = False
    out["brown_dwarf_like"] = bd
    bv = out["bd_veto"].to_numpy(object).copy()
    bv[rows[veto]] = "agn_colours"
    out["bd_veto"] = bv.astype(str)
    st["vetoed"] = int(veto.sum())
    return out, st


def band_labels(out: pd.DataFrame, saturated: np.ndarray, visits: dict[str, np.ndarray] | None = None,
                colours: dict[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    """{label_<name>: bool} from the index columns, class and Gaia columns of a classes frame.
    visits: IX.band_visits of the sources (None: not checked); colours: vega_colours (None: no ice).

    Every label: the band at sig >= INDEX_SIG with depth (excess) >= LABEL_MIN_DEPTH, its band window measured
    in >= LABEL_MIN_VISITS visits, source not saturated.
    - ices (H2O 3.05, CO2 4.27, CO 4.67 µm): not a galaxy, point-like; no Gaia or Gaia BP − RP <
      LABEL_ICE_MAX_BP_RP (cool M dwarfs' water vapour absorbs at 2.7–3.2 µm); no water vapour at 1.4 / 1.9 µm
      (sig < LABEL_SUPPORT_SIG); reddened by the cloud (J − [4.6] AB >= LABEL_ICE_MIN_J_M46) and a reddened
      star's colours (Vega H − K >= LABEL_ICE_MIN_HK, J − H >= LABEL_ICE_MIN_JH, on or above the reddening line
      LABEL_ICE_REDDENING, W1 − W2 < LABEL_ICE_MAX_W1W2); CO ice also without the CO 2.3 µm bandhead (cool-star
      CO gas lands at 4.67 µm too). candidates.py also drops ice on a good photo-z galaxy (photoz runs later).
    - methane (CH4 3.3 or 1.6 µm): only T/Y brown dwarfs, invisible to Gaia: no Gaia or G > LABEL_CH4_MIN_G,
      not a galaxy, red (J − [4.6] AB > BD_MIN_J_M46_AB), and a second band agreeing (the other CH4 band
      or H2O 1.4 / 1.9 µm at sig >= LABEL_SUPPORT_SIG).
    - water vapour (H2O 1.4 or 1.9 µm, in M/L dwarfs and cool giants) and the CO 2.3 µm bandhead: not a galaxy.
    - PAH 3.3 µm emission (at z ≈ 0): not a star (galaxy or unsure), and no water-ice dip (sig < LABEL_SUPPORT_SIG:
      ice depresses PAH's 3.00–3.15 µm continuum window, so ice and PAH never share a source). photoz repeats it at
      the galaxy's redshift (label_pah_33_z); candidates.py lists that one.
    """
    def col(name):
        return out[name].to_numpy(np.float64) if name in out else np.full(len(out), np.nan)

    def fires(name):
        kind = C.INDEX_WINDOWS[name][0]
        v = col(f"{name}_{'depth' if kind == 'abs' else 'excess'}")
        seen = visits[name] >= C.LABEL_MIN_VISITS if visits is not None else True
        with np.errstate(invalid="ignore"):
            return (col(f"{name}_sig") >= C.INDEX_SIG) & (v >= C.LABEL_MIN_DEPTH[name]) & ~saturated & seen

    def sig(name, at=C.LABEL_SUPPORT_SIG):
        with np.errstate(invalid="ignore"):
            return col(f"{name}_sig") >= at

    cls = out["class"].to_numpy()
    galaxy, star = cls == "galaxy", cls == "star"
    g, bp_rp, jm46, ext = col("gaia_g"), col("gaia_bp_rp"), col("j_m46_ab"), col("extended_score")
    has_gaia = np.isfinite(col("gaia_sep_arcsec")) | np.isfinite(g)
    vapour_any = sig("h2o_14") | sig("h2o_19")
    nan = np.full(len(out), np.nan)
    jh, hk, w1w2 = ((colours or {}).get(k, nan) for k in ("jh", "hk", "w1w2"))
    slope, icpt = C.LABEL_ICE_REDDENING
    with np.errstate(invalid="ignore"):
        point = ~(ext > C.LABEL_ICE_MAX_EXT)
        not_cool = ~has_gaia | (bp_rp < C.LABEL_ICE_MAX_BP_RP)
        reddened = ((hk >= C.LABEL_ICE_MIN_HK) & (jh >= C.LABEL_ICE_MIN_JH) & (jh >= slope * hk + icpt)
                    & ~(w1w2 >= C.LABEL_ICE_MAX_W1W2))
        ice_ok = ~galaxy & point & not_cool & ~vapour_any & (jm46 >= C.LABEL_ICE_MIN_J_M46) & reddened
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
        "label_pah_33": fires("pah_33") & ~star & ~sig("h2o_ice_305"),
    }


def run(store: Store, zone: C.Zone, workers: int = 16, locus: dict | None = None,
        dots: pd.DataFrame | None = None) -> dict:
    """locus: a zone-wide stellar_locus (tiles.py); dots: load_shape_dots' result when already loaded."""
    t0 = time.time()
    src = store.read_parquet(results_rel(zone.name, "sources.parquet"))
    src = src.sort_values("source_id").reset_index(drop=True)
    sids = src["source_id"].to_numpy(np.int64)
    gaia, origin = load_gaia(store, zone, src)
    phot = load_photometry(store, zone, src)
    gaia_stars = gaia_star_ids(gaia, sids)
    t1 = time.time()
    if dots is None:
        dots = load_shape_dots(store, zone, workers)
    shape_cols = sorted(set(dot_shape_ratios(dots)) if dots is not None else [])
    t2 = time.time()
    shape = shape_features(dots, sids, gaia_stars, locus)
    out = classify_frame(src, shape, gaia, phot)
    out, veto_st = agn_veto(out, src)
    t3 = time.time()
    nbytes = store.write_parquet(results_rel(zone.name, "classes.parquet"), out)
    vc = out["class"].value_counts()
    st = {
        "sources": int(len(out)),
        "star": int(vc.get("star", 0)), "galaxy": int(vc.get("galaxy", 0)), "unsure": int(vc.get("unsure", 0)),
        "brown_dwarf_like": int(out["brown_dwarf_like"].sum()),
        "bd_no_water": int((out["bd_veto"] == "no_water").sum()), "bd_agn_veto": veto_st,
        "gaia_from": origin, "gaia_matched": int(np.isfinite(out["gaia_g"]).sum()),
        "shape_inputs": shape_cols, "shape_dots": 0 if dots is None else int(len(dots)),
        **{f"n_{k[:-4]}": int((out[k] >= C.INDEX_SIG).sum()) for k in out.columns if k.endswith("_sig")},
        **{k: int(out[k].sum()) for k in out.columns if k.startswith("label_")},
        "classes_bytes": int(nbytes),
        "load_s": round(t2 - t0, 1), "compute_s": round(t3 - t2, 1), "wall_s": round(time.time() - t0, 1),
    }
    L.info("classify %s: %s", zone.name, st)
    return st
