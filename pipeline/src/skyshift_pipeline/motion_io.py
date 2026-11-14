"""Inputs shared by the motion-and-time steps (crossid, colours, lightcurves, astrometry, highpm).

Everything comes from stored tables; photos are never read again:
- member dots: members.parquet → rows of dots/<obs_id>D<det>.parquet (as `spectra` does), with
  session, visit and mjd from the zone's photo list. Columns the dot files don't have yet (PSF flux,
  spacecraft position) come back as NaN, so the steps run on today's files and on the pilot's.
- refcat/<zone>.parquet: Gaia DR3 / CatWISE / 2MASS in one table (ref_id, cat, ra, dec, epoch_mjd,
  pmra, pmdec, parallax, magnitudes). Missing → an empty table and a warning.
- forced/<obs_id>D<det>.parquet: forced photometry at catalogue positions, non-detections included.

`all` runs the five steps in one process: the member dots are read once and cached.
"""

from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .sky import angsep
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("motion")

# dot columns read back for the members (aliases: the first one present wins)
DOT_ALIASES: dict[str, tuple[str, ...]] = {
    "ra": ("ra",), "dec": ("dec",), "mjd": ("mjd",),
    "pos_err_arcsec": ("pos_err_arcsec",),
    "flux_ujy": ("flux_ujy",), "flux_err_ujy": ("flux_err_ujy",),
    "flux_psf_ujy": ("flux_psf_ujy", "psf_flux_ujy", "flux_psf"),
    "flux_psf_err_ujy": ("flux_psf_err_ujy", "psf_flux_err_ujy", "flux_psf_err"),
    "wavelength_um": ("wavelength_um",), "snr": ("snr",), "flags": ("flags",),
    "x_sc_km": ("X_SC", "x_sc", "x_sc_km"), "y_sc_km": ("Y_SC", "y_sc", "y_sc_km"), "z_sc_km": ("Z_SC", "z_sc", "z_sc_km"),
}
FLOAT32 = {"pos_err_arcsec", "flux_ujy", "flux_err_ujy", "flux_psf_ujy", "flux_psf_err_ujy", "wavelength_um", "snr",
           "x_sc_km", "y_sc_km", "z_sc_km"}

_CACHE: dict[tuple[str, str, str], tuple[pd.DataFrame, dict]] = {}


def pick(df: pd.DataFrame, names: tuple[str, ...] | list[str], default=np.nan) -> np.ndarray:
    """The first column of `names` present in df (case-insensitive), else `default` everywhere."""
    low = {c.lower(): c for c in df.columns}
    for n in names:
        if n in df.columns:
            return df[n].to_numpy()
        if n.lower() in low:
            return df[low[n.lower()]].to_numpy()
    return np.full(len(df), default)


def has_any(df: pd.DataFrame, names) -> bool:
    low = {c.lower() for c in df.columns}
    return any(n.lower() in low for n in names)


def spacecraft_km(parquet_bytes: bytes) -> tuple:
    """SPHEREx geocentric X_SC, Y_SC, Z_SC (km) from a dot file's `skyshift_photo` metadata (header);
    (None, None, None) when absent."""
    try:
        from .dots import photo_meta
        h = (photo_meta(parquet_bytes) or {}).get("header") or {}
        vals = tuple(h.get(k) for k in ("X_SC", "Y_SC", "Z_SC"))
        return tuple(float(v) if v is not None else None for v in vals)
    except Exception:  # noqa: BLE001  (older files, other schema)
        return (None, None, None)


def clear_cache() -> None:
    _CACHE.clear()


def member_dots(store: Store, zone: C.Zone, workers: int = 16) -> tuple[pd.DataFrame, dict]:
    """One row per member dot: source_id, obs_id, detector, session, visit, photo (row in the photo
    list) + every DOT_ALIASES column (NaN where the dot files lack it). Cached per (root, zone)."""
    # a new members.parquet (link rerun) invalidates the cache
    info = store.fs.info(store.path(results_rel(zone.name, "members.parquet")))
    stamp = f"{info.get('size')}-{info.get('mtime', info.get('updated', info.get('generation')))}"
    key = (store.root, zone.name, stamp)
    if key in _CACHE:
        return _CACHE[key]
    members = store.read_parquet(results_rel(zone.name, "members.parquet"))
    photos = store.read_parquet(photolist_rel(zone.name))
    photos = photos.reset_index(drop=True)
    pidx = {(str(o), int(d)): i for i, (o, d) in enumerate(zip(photos["obs_id"], photos["detector"]))}
    groups = members.groupby(["obs_id", "detector"], observed=True, sort=True).indices
    sid_all = members["source_id"].to_numpy(np.int64)
    row_all = members["dot_row"].to_numpy(np.int64)
    counts = {"rows_missing": 0, "files_missing": 0}

    def one(item):
        (obs_id, det), idx = item
        try:
            raw = store.read_bytes(dots_rel(obs_id, int(det)))
        except FileNotFoundError:
            counts["files_missing"] += 1
            return None
        df = pd.read_parquet(io.BytesIO(raw))
        sc = spacecraft_km(raw)
        rows = row_all[idx]
        ok = rows < len(df)
        counts["rows_missing"] += int((~ok).sum())
        df = df.iloc[rows[ok]].reset_index(drop=True)
        out = {k: pick(df, v) for k, v in DOT_ALIASES.items()}
        part = pd.DataFrame(out)
        p = pidx.get((str(obs_id), int(det)), -1)
        part.insert(0, "source_id", sid_all[idx[ok]])
        part.insert(1, "obs_id", str(obs_id))
        part.insert(2, "detector", np.int8(det))
        part.insert(3, "photo", np.int32(p))
        for k, v in zip(("x_sc_km", "y_sc_km", "z_sc_km"), sc):
            if v is not None and not np.isfinite(part[k].to_numpy(np.float64)).any():
                part[k] = float(v)
        return part

    with ThreadPoolExecutor(max_workers=max(4, min(workers, 32))) as ex:
        parts = [p for p in ex.map(one, groups.items()) if p is not None]
    cols = ["source_id", "obs_id", "detector", "photo", *DOT_ALIASES]
    if parts:
        d = pd.concat(parts, ignore_index=True)
    else:
        d = pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
        d["source_id"] = d["source_id"].astype(np.int64)
        d["photo"] = d["photo"].astype(np.int32)
    p = d["photo"].to_numpy(np.int64)
    known = p >= 0
    d["session"] = np.where(known, photos["session"].to_numpy(np.int64)[np.where(known, p, 0)], -1).astype(np.int32) if len(photos) else np.int32(-1)
    d["visit"] = np.where(known, photos["visit"].to_numpy(np.int64)[np.where(known, p, 0)], -1).astype(np.int16) if len(photos) else np.int16(-1)
    # mjd: the dot file's, else the photo list's
    mjd = d["mjd"].to_numpy(np.float64)
    if len(photos):
        pm = photos["mjd"].to_numpy(np.float64)[np.where(known, p, 0)]
        d["mjd"] = np.where(np.isfinite(mjd), mjd, np.where(known, pm, np.nan))
    # spacecraft position: the dot file's, else the photo list's (if it has it)
    for k in ("x_sc_km", "y_sc_km", "z_sc_km"):
        v = d[k].to_numpy(np.float64)
        if len(photos) and not np.isfinite(v).any() and has_any(photos, DOT_ALIASES[k]):
            d[k] = pick(photos, DOT_ALIASES[k]).astype(np.float64)[np.where(known, p, 0)]
    for c in FLOAT32:
        d[c] = d[c].astype(np.float32)
    d["flags"] = pd.to_numeric(d["flags"], errors="coerce").fillna(0).astype(np.int64)

    # dots far from their source (a dot file rewritten after link) are stale: drop
    st = {"member_dots": int(len(members)), "member_files_missing": counts["files_missing"],
          "member_rows_missing": counts["rows_missing"]}
    if store.exists(results_rel(zone.name, "fixed.parquet")) and len(d):
        fx = store.read_parquet(results_rel(zone.name, "fixed.parquet"), columns=["source_id", "ra", "dec"]).set_index("source_id")
        pos = fx.index.get_indexer(d["source_id"].to_numpy())
        ok = pos >= 0
        sep = np.full(len(d), np.inf)
        sep[ok] = angsep(d["ra"].to_numpy()[ok], d["dec"].to_numpy()[ok], fx["ra"].to_numpy()[pos[ok]], fx["dec"].to_numpy()[pos[ok]]) * 3600
        near = sep <= C.SPECTRA_MEMBER_MAX_ARCSEC
        st["member_dots_stale"] = int((~near).sum())
        d = d[near].reset_index(drop=True)
    d = d.sort_values(["source_id", "mjd", "detector"], kind="stable").reset_index(drop=True)
    st["member_dots_used"] = int(len(d))
    _CACHE.clear()
    _CACHE[key] = (d, st)
    return d, st


# --- reference catalogue ---------------------------------------------------------------------
REFCAT_COLUMNS = ["ref_id", "cat", "ra", "dec", "epoch_mjd", "pmra", "pmdec", "parallax", "parallax_error"]
MAG_ALIASES: dict[str, tuple[str, ...]] = {
    "g": ("g", "phot_g_mean_mag", "gmag", "g_mag"),
    "bp": ("bp", "phot_bp_mean_mag", "bp_mag"), "rp": ("rp", "phot_rp_mean_mag", "rp_mag"),
    "bp_rp": ("bp_rp",),
    "w1": ("w1", "w1mpro", "w1_mag", "w1mpro_pm"), "w2": ("w2", "w2mpro", "w2_mag", "w2mpro_pm"),
    "j": ("j", "j_m", "jmag", "j_mag"), "h": ("h", "h_m", "hmag", "h_mag"), "k": ("k", "ks", "k_m", "kmag", "ks_mag", "k_mag"),
    "name": ("name", "source_name", "designation"),
}
CAT_NAMES = {"gaia": ("gaia",), "catwise": ("catwise", "cwise", "wise"), "tmass": ("2mass", "tmass", "twomass")}


def catalogue_of(cat: np.ndarray) -> np.ndarray:
    """refcat `cat` value → 'gaia' | 'catwise' | 'tmass' | '' (substring, case-insensitive)."""
    s = pd.Series(cat).astype(str).str.lower()
    out = np.full(len(s), "", dtype=object)
    for k, keys in CAT_NAMES.items():
        m = np.zeros(len(s), bool)
        for key in keys:
            m |= s.str.contains(key, regex=False).to_numpy()
        out[m & (out == "")] = k
    return out


def load_refcat(store: Store, zone: C.Zone) -> pd.DataFrame:
    """refcat/<zone>.parquet normalised: REFCAT_COLUMNS (ref_id as string, cat as gaia|catwise|tmass,
    pm in mas/yr with pmra × cos dec, parallax mas, epoch_mjd; NaN where unknown) + MAG_ALIASES keys."""
    rel = C.REFCAT_REL.format(zone=zone.name)
    if not store.exists(rel) and store.exists(C.REFCAT_FALLBACK_REL.format(zone=zone.name)):
        rel = C.REFCAT_FALLBACK_REL.format(zone=zone.name)
    if not store.exists(rel):
        L.warning("%s missing: no cross-identification against Gaia / CatWISE / 2MASS", rel)
        cols = {c: pd.Series(dtype="float64") for c in [*REFCAT_COLUMNS, *MAG_ALIASES]}
        cols["ref_id"] = pd.Series(dtype=object)
        cols["cat"] = pd.Series(dtype=object)
        cols["name"] = pd.Series(dtype=object)
        return pd.DataFrame(cols)
    r = store.read_parquet(rel)
    ref_id = pd.Series(pick(r, ("ref_id", "source_id", "id"), "")).astype(str).to_numpy()
    cat = pd.Series(pick(r, ("cat", "catalog", "catalogue"), "")).fillna("").astype(str).to_numpy()
    cat = np.where(cat == "", ref_id, cat)  # ref_id carries the catalogue as a prefix: "gaia:…", "catwise:…"
    out = pd.DataFrame({
        "ref_id": ref_id,
        "cat": catalogue_of(cat),
        "ra": pick(r, ("ra",)).astype(np.float64), "dec": pick(r, ("dec",)).astype(np.float64),
        "epoch_mjd": pick(r, ("epoch_mjd", "ref_epoch_mjd")).astype(np.float64),
        "pmra": pick(r, ("pmra",)).astype(np.float64), "pmdec": pick(r, ("pmdec",)).astype(np.float64),
        "parallax": pick(r, ("parallax", "plx")).astype(np.float64),
        "parallax_error": pick(r, ("parallax_error", "parallax_err", "e_plx")).astype(np.float64),
    })
    for k, al in MAG_ALIASES.items():
        out[k] = pick(r, al, "" if k == "name" else np.nan)
        if k != "name":
            out[k] = pd.to_numeric(out[k], errors="coerce").astype(np.float64)
    bad = ~np.isfinite(out["bp_rp"].to_numpy())
    out.loc[bad, "bp_rp"] = (out["bp"] - out["rp"])[bad]
    # Gaia's epoch when the table doesn't say (J2016.0)
    from skyshift_core import GAIA_EPOCH_MJD
    miss = ~np.isfinite(out["epoch_mjd"].to_numpy()) & (out["cat"].to_numpy() == "gaia")
    out.loc[miss, "epoch_mjd"] = GAIA_EPOCH_MJD
    return out


def gaia_numeric_id(ref_id: np.ndarray) -> np.ndarray:
    """Gaia DR3 source_id (int64) from a ref_id string like '4472832130942575872' or 'gaia:447...'; -1 if none."""
    out = np.full(len(ref_id), -1, np.int64)
    for i, s in enumerate(ref_id):
        m = re.search(r"(\d{1,19})$", str(s))
        if m and int(m.group(1)) < 2**63:
            out[i] = int(m.group(1))
    return out


# --- forced photometry -----------------------------------------------------------------------
FORCED_ALIASES: dict[str, tuple[str, ...]] = {
    "ref_id": ("ref_id", "source_id", "id"), "mjd": ("mjd",), "detector": ("detector",),
    "flux_ujy": ("flux_psf_ujy", "flux_ujy", "flux"), "flux_err_ujy": ("flux_psf_err_ujy", "err", "flux_err_ujy", "flux_err"),
    "wavelength_um": ("wavelength_um",), "detected": ("detected",), "flags": ("flags",),
}


def load_forced(store: Store, zone: C.Zone, workers: int = 16) -> pd.DataFrame:
    """Every forced/<obs_id>D<det>.parquet of the zone's photos, normalised (FORCED_ALIASES keys + obs_id,
    session, visit). Empty when the step hasn't run."""
    photos = store.read_parquet(photolist_rel(zone.name)).reset_index(drop=True)
    have = set(store.listdir(C.FORCED_DIR))
    cols = [*FORCED_ALIASES, "obs_id", "session", "visit"]
    if not len(photos):
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
    todo = [r for r in photos.itertuples() if f"{r.obs_id}D{int(r.detector)}.parquet" in have]
    if not todo:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})

    def one(r):
        f = store.read_parquet(f"{C.FORCED_DIR}/{r.obs_id}D{int(r.detector)}.parquet")
        out = pd.DataFrame({k: pick(f, v) for k, v in FORCED_ALIASES.items()})
        out["ref_id"] = pd.Series(out["ref_id"]).astype(str).to_numpy()
        det = pd.to_numeric(out["detector"], errors="coerce").to_numpy()
        out["detector"] = np.where(np.isfinite(det), det, r.detector).astype(np.int8)
        mjd = pd.to_numeric(out["mjd"], errors="coerce").to_numpy(np.float64)
        out["mjd"] = np.where(np.isfinite(mjd), mjd, r.mjd)
        out["obs_id"] = str(r.obs_id)
        out["session"] = np.int32(r.session)
        out["visit"] = np.int16(r.visit)
        return out

    with ThreadPoolExecutor(max_workers=max(4, min(workers, 32))) as ex:
        parts = list(ex.map(one, todo))
    f = pd.concat(parts, ignore_index=True)
    f["flux_ujy"] = pd.to_numeric(f["flux_ujy"], errors="coerce").astype(np.float32)
    f["flux_err_ujy"] = pd.to_numeric(f["flux_err_ujy"], errors="coerce").astype(np.float32)
    f["wavelength_um"] = pd.to_numeric(f["wavelength_um"], errors="coerce").astype(np.float32)
    f["flags"] = pd.to_numeric(f["flags"], errors="coerce").fillna(0).astype(np.int64)
    det = f["detected"]
    if det.isna().all():
        with np.errstate(invalid="ignore", divide="ignore"):
            f["detected"] = (f["flux_ujy"] / f["flux_err_ujy"]).to_numpy() >= C.LC_DETECTED_SNR
    else:
        f["detected"] = det.fillna(False).astype(bool)
    return f[cols]
