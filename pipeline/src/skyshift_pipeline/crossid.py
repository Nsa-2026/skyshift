"""Step `crossid`: every fixed source ↔ Gaia DR3, CatWISE2020 and 2MASS (refcat/<zone>.parquet).

Each source has a mean epoch: the mean MJD of the photos its dots come from (members + photo list,
no dot files read). Catalogue positions are moved to that epoch with their proper motion (Gaia: and
parallax) before matching, so Barnard's Star at 10″/yr matches as well as a still star. Per catalogue:
candidates within CROSSID_ARCSEC + CROSSID_SLACK_ARCSEC of the positions moved to the zone's median epoch,
then each pair is redone at the source's own epoch and the nearest within CROSSID_ARCSEC[cat] wins;
`<cat>_n` counts the catalogue entries within that radius (> 1 = crowded, the match may be the wrong one).
2MASS has no motion of its own (epoch ~1999): the source is moved back to each 2MASS entry's epoch with the
proper motion of its Gaia match (else its CatWISE match), so a star at 0.5″/yr still finds its 2MASS entry 13″ away.

Writes results/<zone>/crossids.parquet, one row per source (contracts/dots.md, Derived tables).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from skyshift_core import parallax_offset_arcsec, tangent_offset_arcsec
from skyshift_core.astrometry import DAYS_PER_JYEAR

from . import config as C
from . import log
from .motion_io import gaia_numeric_id, load_refcat
from .sky import SkyTree
from .store import Store, photolist_rel, results_rel

L = log.get("crossid")


def source_epochs(store: Store, zone: C.Zone, source_id: np.ndarray) -> np.ndarray:
    """Mean MJD of each source's photos (from members.parquet and the photo list)."""
    members = store.read_parquet(results_rel(zone.name, "members.parquet"), columns=["source_id", "obs_id", "detector"])
    photos = store.read_parquet(photolist_rel(zone.name), columns=["obs_id", "detector", "mjd"])
    key = pd.MultiIndex.from_arrays([photos["obs_id"].astype(str), photos["detector"].astype(np.int64)])
    pos = key.get_indexer(pd.MultiIndex.from_arrays([members["obs_id"].astype(str), members["detector"].astype(np.int64)]))
    ok = pos >= 0
    mjd = pd.Series(photos["mjd"].to_numpy(np.float64)[pos[ok]]).groupby(members["source_id"].to_numpy()[ok]).mean()
    out = mjd.reindex(source_id).to_numpy(np.float64)
    if np.isnan(out).any():
        fill = float(np.nanmedian(photos["mjd"])) if len(photos) else np.nan
        out = np.where(np.isnan(out), fill, out)
    return out


def ref_offset_at(ra0, dec0, ref: pd.DataFrame, mjd, parallax: bool) -> tuple[np.ndarray, np.ndarray]:
    """Tangent-plane offset (east, north, arcsec) about (ra0, dec0) of each catalogue entry moved to
    `mjd` (pm × Δt from its epoch_mjd, + parallax if asked and known). All arrays aligned."""
    e, n = tangent_offset_arcsec(ra0, dec0, ref["ra"].to_numpy(), ref["dec"].to_numpy())
    dt = (np.asarray(mjd, np.float64) - ref["epoch_mjd"].to_numpy()) / DAYS_PER_JYEAR
    dt = np.nan_to_num(dt)
    e = e + np.nan_to_num(ref["pmra"].to_numpy()) * dt / 1000.0
    n = n + np.nan_to_num(ref["pmdec"].to_numpy()) * dt / 1000.0
    if parallax:
        plx = np.nan_to_num(ref["parallax"].to_numpy())
        if np.any(plx != 0):
            pe, pn = parallax_offset_arcsec(ref["ra"].to_numpy(), ref["dec"].to_numpy(), plx, mjd)
            e, n = e + pe, n + pn
    return e, n


def crossmatch(ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray, ref: pd.DataFrame, radius_arcsec: float,
               parallax: bool = False, src_pm: tuple[np.ndarray, np.ndarray] | None = None
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Nearest catalogue entry of each position at its own epoch: (row in ref or -1, separation arcsec
    or NaN, number within radius). src_pm: the sources' own (pmra, pmdec) in mas/yr (NaN = none) for a
    catalogue without motion (2MASS): each pair is compared at the catalogue entry's epoch instead."""
    n = len(ra)
    idx = np.full(n, -1, np.int64)
    sep = np.full(n, np.nan)
    cnt = np.zeros(n, np.int32)
    if n == 0 or len(ref) == 0:
        return idx, sep, cnt
    from skyshift_core import tangent_deproject
    t_med = float(np.nanmedian(mjd))
    e, nn = ref_offset_at(ref["ra"].to_numpy(), ref["dec"].to_numpy(), ref, t_med, parallax=False)
    rra, rdec = tangent_deproject(ref["ra"].to_numpy(), ref["dec"].to_numpy(), e, nn)
    # the slack covers the motion between the median epoch and each source's epoch
    span_yr = float(np.nanmax(np.abs(mjd - t_med))) / DAYS_PER_JYEAR if n else 0.0
    pm_max = float(np.nanmax(np.hypot(np.nan_to_num(ref["pmra"].to_numpy()), np.nan_to_num(ref["pmdec"].to_numpy())))) / 1000.0
    rq = radius_arcsec + C.CROSSID_SLACK_ARCSEC + pm_max * span_yr + (1.0 if parallax else 0.0)
    qra, qdec = ra, dec
    if src_pm is not None:  # query from the source's position at the catalogue's median epoch
        spra, spdec = np.nan_to_num(np.asarray(src_pm[0], np.float64)), np.nan_to_num(np.asarray(src_pm[1], np.float64))
        rep = ref["epoch_mjd"].to_numpy(np.float64)
        t_ref = float(np.nanmedian(rep)) if np.isfinite(rep).any() else t_med
        dt0 = (mjd - t_ref) / DAYS_PER_JYEAR
        qra, qdec = tangent_deproject(ra, dec, -spra * dt0 / 1000.0, -spdec * dt0 / 1000.0)
        spread = float(np.nanmax(np.abs(rep - t_ref))) / DAYS_PER_JYEAR if np.isfinite(rep).any() else 0.0
        rq += float(np.max(np.hypot(spra, spdec))) / 1000.0 * (spread + span_yr) if n else 0.0
    lists = SkyTree(rra, rdec).query_radius(qra, qdec, rq)
    lens = np.fromiter((len(x) for x in lists), np.int64, n)
    if lens.sum() == 0:
        return idx, sep, cnt
    si = np.repeat(np.arange(n), lens)
    ri = np.fromiter((j for x in lists for j in x), np.int64, int(lens.sum()))
    sub = ref.iloc[ri].reset_index(drop=True)
    pe, pn = ref_offset_at(ra[si], dec[si], sub, mjd[si], parallax=parallax)
    if src_pm is not None:  # the source at the entry's epoch: offset + its own pm × (source epoch − entry epoch)
        dts = np.nan_to_num((mjd[si] - sub["epoch_mjd"].to_numpy(np.float64)) / DAYS_PER_JYEAR)
        pe, pn = pe + spra[si] * dts / 1000.0, pn + spdec[si] * dts / 1000.0
    d = np.hypot(pe, pn)
    ok = d <= radius_arcsec
    si, ri, d = si[ok], ri[ok], d[ok]
    if si.size == 0:
        return idx, sep, cnt
    cnt[:] = np.bincount(si, minlength=n)
    order = np.lexsort((d, si))
    first = order[np.r_[True, np.diff(si[order]) != 0]]
    idx[si[first]] = ri[first]
    sep[si[first]] = d[first]
    return idx, sep, cnt


def compute(src: pd.DataFrame, epochs: np.ndarray, refcat: pd.DataFrame) -> pd.DataFrame:
    """sources (source_id, ra, dec) + their mean epochs + normalised refcat → crossids table."""
    ra, dec = src["ra"].to_numpy(np.float64), src["dec"].to_numpy(np.float64)
    out = pd.DataFrame({"source_id": src["source_id"].to_numpy(np.int64), "ra": ra, "dec": dec,
                        "epoch_mjd": epochs.astype(np.float64)})
    cats = refcat["cat"].to_numpy()

    def take(ref, i, col, dtype=np.float64, fill=np.nan):
        v = np.full(len(i), fill, dtype=dtype if dtype is not str else object)
        m = i >= 0
        if m.any():
            v[m] = ref[col].to_numpy()[i[m]]
        return v

    g = refcat[cats == "gaia"].reset_index(drop=True)
    i, s, k = crossmatch(ra, dec, epochs, g, C.CROSSID_ARCSEC["gaia"], parallax=True)
    gid = take(g, i, "ref_id", str, "")
    out["gaia_ref_id"] = gid.astype(str)
    out["gaia_id"] = np.where(i >= 0, gaia_numeric_id(gid), -1).astype(np.int64)
    out["gaia_sep_arcsec"] = s.astype(np.float32)
    out["gaia_n"] = k.astype(np.int16)
    for c, name in (("ra", "gaia_ra"), ("dec", "gaia_dec"), ("epoch_mjd", "gaia_epoch_mjd")):
        out[name] = take(g, i, c)
    for c, name in (("g", "gaia_g"), ("bp_rp", "gaia_bp_rp"), ("pmra", "gaia_pmra"), ("pmdec", "gaia_pmdec"),
                    ("parallax", "gaia_parallax"), ("parallax_error", "gaia_parallax_error"),
                    ("z_ab", "gaia_z_ab"), ("y_ab", "gaia_y_ab")):
        out[name] = take(g, i, c).astype(np.float32)

    w = refcat[cats == "catwise"].reset_index(drop=True)
    i, s, k = crossmatch(ra, dec, epochs, w, C.CROSSID_ARCSEC["catwise"])
    out["catwise_ref_id"] = take(w, i, "ref_id", str, "").astype(str)
    nm = take(w, i, "name", str, "").astype(str)
    out["catwise_name"] = np.where((nm == "") | (nm == "nan"), out["catwise_ref_id"], nm)
    out["catwise_sep_arcsec"] = s.astype(np.float32)
    out["catwise_n"] = k.astype(np.int16)
    for c, name in (("w1", "w1"), ("w2", "w2"), ("pmra", "catwise_pmra"), ("pmdec", "catwise_pmdec")):
        out[name] = take(w, i, c).astype(np.float32)

    t = refcat[cats == "tmass"].reset_index(drop=True)
    has_g = out["gaia_ref_id"].to_numpy() != ""
    spm = tuple(np.where(has_g, out[f"gaia_{c}"].to_numpy(np.float64), out[f"catwise_{c}"].to_numpy(np.float64))
                for c in ("pmra", "pmdec"))
    i, s, k = crossmatch(ra, dec, epochs, t, C.CROSSID_ARCSEC["tmass"], src_pm=spm)
    out["tmass_ref_id"] = take(t, i, "ref_id", str, "").astype(str)
    nm = take(t, i, "name", str, "").astype(str)
    out["tmass_name"] = np.where((nm == "") | (nm == "nan"), out["tmass_ref_id"], nm)
    out["tmass_sep_arcsec"] = s.astype(np.float32)
    out["tmass_n"] = k.astype(np.int16)
    for c in ("j", "h", "k"):
        out[c] = take(t, i, c).astype(np.float32)
    return out


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    src = store.read_parquet(results_rel(zone.name, "sources.parquet"), columns=["source_id", "ra", "dec"])
    epochs = source_epochs(store, zone, src["source_id"].to_numpy(np.int64))
    refcat = load_refcat(store, zone)
    out = compute(src, epochs, refcat)
    store.write_parquet(results_rel(zone.name, "crossids.parquet"), out)
    from .refcat import FULL, built_partial, catalogs_in
    made = catalogs_in(store, zone.name)
    if built_partial(store, zone.name):  # 16 Nov: 64 combined zones were matched to Gaia alone without a word
        L.error("crossid %s: refcat/%s.parquet holds %s only (no %s): catwise_*/tmass_* stay empty; run `refcat` with every "
                "catalogue (cli `all` does), then crossid onward", zone.name, zone.name, ",".join(made),
                ",".join(c for c in FULL if c not in made))
    st = {"sources": int(len(out)), "refcat_rows": int(len(refcat)),
          "refcat_catalogs": made if made is not None else list(FULL), "refcat_complete": not built_partial(store, zone.name),
          "refcat_per_cat": {k: int((refcat["cat"] == k).sum()) for k in ("gaia", "catwise", "tmass")},
          "matched_gaia": int((out["gaia_ref_id"] != "").sum()),
          "matched_catwise": int((out["catwise_ref_id"] != "").sum()),
          "matched_tmass": int((out["tmass_ref_id"] != "").sum()),
          "gaia_crowded": int((out["gaia_n"] > 1).sum()),
          "median_gaia_sep_arcsec": float(np.nanmedian(out["gaia_sep_arcsec"])) if (out["gaia_ref_id"] != "").any() else None,
          "wall_s": round(time.time() - t0, 1)}
    L.info("crossid %s: %s", zone.name, st)
    return st
