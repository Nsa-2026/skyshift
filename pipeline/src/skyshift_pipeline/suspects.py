"""Step 6, `suspects`: movers nobody has catalogued.

A mover becomes a suspect when:
- no known object (MPC, any brightness) lies within KNOWN_ARCSEC of it in the same session;
- it has none of the GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK, HALO or BLOOM bits;
- no bright fixed source (snr >= BRIGHT_FIXED_SNR) lies within BRIGHT_FIXED_ARCSEC;
- the SOURCE bit is not set at the peak pixel of every one of its dots (SOURCE marks catalogue
  stars and compact galaxies to AB ~19);
- no Gaia DR3 star within GAIA_ARCSEC and no CatWISE2020 source within CATWISE_ARCSEC (faint
  sources near our limit flicker in and out). Checked locally against refcat/<zone>.parquet
  (Gaia G < 20 and all of CatWISE2020 over the zone, both moved to the movers' median time).
  Only without that file are the services asked, around the remaining movers in batches. If a
  catalogue can't be reached the suspects are kept with gaia_checked / catwise_checked = false
  and the summary says so.
  A Gaia-only refcat (SKYSHIFT_REFCAT_CATALOGS=gaia: no CatWISE rows) gets the WISE veto from AllWISE
  instead, read from IRSA's HEALPix Parquet on AWS S3: only the partitions around the remaining movers
  (config SUSPECTS_ALLWISE, ALLWISE_*). catwise_checked then means "checked against AllWISE" and the
  summary's catwise_source says which catalogue it was.
Then the vetting gates (vet.py: hot pixel, bad column, detector edge, masked bit at the peak, sharp + OUTLIER, flux
ratio, red photo, 2MASS within 6″, AllWISE within 6″ where CatWISE was the veto) grade every suspect: C fails one
(kept, with `reasons`), B passes all (motion not shown). A = B and on a track of `tracklets` (results/<zone>/
tracklets.parquet, joined on the suspect id = its `mover_id`) with movers in >= VET_A_MIN_SESSIONS sessions and
rate_snr >= VET_MOTION_SIGMA: a candidate mover. Suspects on a track carry its n_sessions, rate_arcsec_h, pa_deg,
rate_sigma (rate_snr, tracklet_id in the parquet); without a readable tracklets.parquet nothing changes.
Ranked by grade, then snr. Written as results/<zone>/suspects.parquet and results/<zone>/suspects.json in the
contracts/api.md suspects.json shape (+ grade, reasons, near_source_arcsec, the track's fields; zone-level vetoes,
provisional, grades).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import config as C
from . import log
from . import vet as V
from .known import load_predictions
from .sky import SkyTree, angsep, offset_radec
from .store import Store, photolist_rel, results_rel
from .tracklets import mover_ids

L = log.get("suspects")

TRACKLETS_NAME = "tracklets.parquet"
# tracklets.parquet columns a suspect on a track takes over (NaN / null when on no track); the JSON gets TRACK_JSON
TRACK_COLUMNS = ("tracklet_id", "n_sessions", "rate_arcsec_h", "pa_deg", "rate_sigma", "rate_snr")
TRACK_JSON = (("n_sessions", None), ("rate_arcsec_h", 2), ("pa_deg", 1), ("rate_sigma", 2))  # (field, decimals; None = int)


def nearest_known_arcsec(movers: pd.DataFrame, pred: pd.DataFrame, search_arcsec: float = 600.0) -> np.ndarray:
    """Distance to the nearest known object in the same session, moved to the mover's time
    (NaN if none within search_arcsec)."""
    out = np.full(len(movers), np.nan)
    if len(movers) == 0 or len(pred) == 0:
        return out
    tree = SkyTree(pred["ra"].to_numpy(float), pred["dec"].to_numpy(float))
    pm, ps = pred["mjd"].to_numpy(float), pred["session"].to_numpy(np.int64)
    pra, pdec = pred["ra"].to_numpy(float), pred["dec"].to_numpy(float)
    dra, ddec = pred["dra_arcsec_h"].to_numpy(float), pred["ddec_arcsec_h"].to_numpy(float)
    for i, (ra, dec, mjd, sess) in enumerate(zip(movers["ra"], movers["dec"], movers["mjd"], movers["session"])):
        cand = tree.query_radius(ra, dec, search_arcsec)[0]
        if not cand:
            continue
        cand = np.asarray(cand)
        cand = cand[ps[cand] == int(sess)]
        if cand.size == 0:
            continue
        dt_h = (mjd - pm[cand]) * 24.0
        r2, d2 = offset_radec(pra[cand], pdec[cand], dra[cand] * dt_h, ddec[cand] * dt_h)
        out[i] = float((angsep(ra, dec, r2, d2) * 3600.0).min())
    return out


def near_bright_fixed(movers: pd.DataFrame, fixed: pd.DataFrame) -> np.ndarray:
    if len(movers) == 0 or len(fixed) == 0:
        return np.zeros(len(movers), dtype=bool)
    b = fixed[fixed["snr"] >= C.BRIGHT_FIXED_SNR]
    sep, _ = SkyTree(b["ra"].to_numpy(float), b["dec"].to_numpy(float)).nearest(movers["ra"].to_numpy(float), movers["dec"].to_numpy(float))
    return sep <= C.BRIGHT_FIXED_ARCSEC


def _esa(q: str):
    from astroquery.gaia import Gaia
    return Gaia.launch_job(q).get_results()


IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP"
UPLOAD_BATCH = 5000


def _irsa_upload(table: str, ra: np.ndarray, dec: np.ndarray, r_deg: float):
    """IRSA doesn't accept OR'd CONTAINS; it does accept a TAP_UPLOAD crossmatch."""
    import pyvo
    from astropy.table import Table

    up = Table({"idx": np.arange(len(ra)), "ra": ra, "dec": dec})
    q = (f"SELECT c.ra, c.dec FROM {table} AS c, TAP_UPLOAD.pts AS u WHERE "
         f"CONTAINS(POINT('ICRS', c.ra, c.dec), CIRCLE('ICRS', u.ra, u.dec, {r_deg:.7f}))=1")
    return pyvo.dal.TAPService(IRSA_TAP).run_sync(q, uploads={"pts": up}).to_table()


# (name, table, kind): "or" = batches of OR'd circles (ESA Gaia), "upload" = TAP_UPLOAD crossmatch (IRSA)
GAIA_SOURCES = (("ESA Gaia", "gaiadr3.gaia_source", "or"), ("IRSA Gaia", "gaia_dr3_source", "upload"))
CATWISE_SOURCES = (("IRSA CatWISE2020", "catwise_2020", "upload"),)


def catalog_near(ra: np.ndarray, dec: np.ndarray, radius_arcsec: float, sources=GAIA_SOURCES) -> tuple[np.ndarray, str]:
    """True where a catalogue source lies within radius. Tries each (name, table, kind) in turn.
    Returns (flags, name of the source used, "" if none answered)."""
    n = len(ra)
    hit = np.zeros(n, dtype=bool)
    if n == 0:
        return hit, "none needed"
    r_deg = radius_arcsec / 3600.0

    def adql(table: str, idx: np.ndarray) -> str:
        circles = " OR ".join(
            f"1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra[i]:.7f}, {dec[i]:.7f}, {r_deg:.7f}))" for i in idx)
        return f"SELECT ra, dec FROM {table} WHERE {circles}"

    for name, table, kind in sources:
        try:
            found_ra, found_dec = [], []
            batch = C.CATALOG_BATCH if kind == "or" else UPLOAD_BATCH
            for s in range(0, n, batch):
                idx = np.arange(s, min(s + batch, n))
                for attempt in range(1, C.NET_RETRIES + 1):
                    try:
                        if kind == "or":
                            t = _esa(adql(table, idx))
                        else:
                            t = _irsa_upload(table, ra[idx], dec[idx], r_deg)
                        break
                    except Exception:  # noqa: BLE001
                        if attempt == C.NET_RETRIES:
                            raise
                        time.sleep(5 * attempt)
                found_ra.extend(np.asarray(t["ra"], float))
                found_dec.extend(np.asarray(t["dec"], float))
            if found_ra:
                sep, _ = SkyTree(np.array(found_ra), np.array(found_dec)).nearest(ra, dec)
                hit = sep <= radius_arcsec + 0.01
            return hit, name
        except Exception as e:  # noqa: BLE001
            L.warning("%s failed: %s", name, str(e)[:300])
    return hit, ""


def load_refcat(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    """refcat/<zone>.parquet's cat, ra, dec, epoch_mjd, pmra, pmdec; None if the file is missing."""
    from .refcat import refcat_rel
    rel = refcat_rel(zone.name)
    if not store.exists(rel):
        return None
    return store.read_parquet(rel, columns=["cat", "ra", "dec", "epoch_mjd", "pmra", "pmdec"])


def refcat_seps(r: pd.DataFrame | None, ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray) -> dict[str, np.ndarray]:
    """Nearest separation (arcsec, inf if none) per catalogue in the refcat (gaia, catwise, tmass, ...), each moved by
    proper motion to the movers' median time."""
    from .refcat import move_to_epoch
    out: dict[str, np.ndarray] = {}
    if r is None or len(ra) == 0:
        return out
    t = float(np.median(mjd))
    for cat, c in r.groupby("cat"):
        cra, cdec = move_to_epoch(c, t)
        sep, _ = SkyTree(np.asarray(cra, np.float64), np.asarray(cdec, np.float64)).nearest(ra, dec)
        out[str(cat)] = np.where(np.isfinite(sep), sep, np.inf)
    return out


def refcat_near(store: Store, zone: C.Zone, ra: np.ndarray, dec: np.ndarray,
                mjd: np.ndarray, ref: pd.DataFrame | None = None) -> tuple[np.ndarray, np.ndarray | None] | None:
    """(gaia hit, catwise hit) from refcat/<zone>.parquet, each catalogue moved by proper motion to the
    movers' median time (the zone spans ~6 months: < 0.3″ error at 1″/yr). None if the file is missing;
    catwise hit None when the file has no CatWISE rows (a Gaia-only refcat: nothing was checked).
    ref: the refcat when already loaded (load_refcat)."""
    from .refcat import move_to_epoch
    r = ref if ref is not None else load_refcat(store, zone)
    if r is None:
        return None
    t = float(np.median(mjd)) if len(mjd) else 0.0
    hits = []
    for cat, arcsec in (("gaia", C.GAIA_ARCSEC), ("catwise", C.CATWISE_ARCSEC)):
        c = r[r["cat"] == cat]
        if len(c) == 0 and cat == "catwise":
            hits.append(None)
            continue
        if len(c) == 0 or len(ra) == 0:
            hits.append(np.zeros(len(ra), bool))
            continue
        cra, cdec = move_to_epoch(c, t)
        sep, _ = SkyTree(cra, cdec).nearest(ra, dec)
        hits.append(sep <= arcsec + 0.01)
    return hits[0], hits[1]


# --- AllWISE on AWS S3 (Gaia-only refcats) ------------------------------------------------------
def allwise_pixels(ra: np.ndarray, dec: np.ndarray, radius_arcsec: float, nside: int = C.ALLWISE_NSIDE) -> np.ndarray:
    """Sorted nested HEALPix pixels (the AllWISE partitions) that the circles of radius_arcsec around the positions
    touch: each centre and 8 points at 1.5 × the radius around it (6″ circles in 1.8° pixels)."""
    import astropy.units as u
    from astropy_healpix import HEALPix

    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    if ra.size == 0:
        return np.zeros(0, np.int64)
    r = 1.5 * radius_arcsec
    pr, pd_ = [ra], [dec]
    for a in np.deg2rad(np.arange(0, 360, 45)):
        r2, d2 = offset_radec(ra, dec, r * np.sin(a), r * np.cos(a))
        pr.append(np.asarray(r2, np.float64))
        pd_.append(np.asarray(d2, np.float64))
    lon = np.mod(np.concatenate(pr), 360.0)
    lat = np.clip(np.concatenate(pd_), -90.0, 90.0)
    h = HEALPix(nside=nside, order="nested")
    return np.unique(np.asarray(h.lonlat_to_healpix(lon * u.deg, lat * u.deg), np.int64))


def allwise_key(pix: int, nside: int = C.ALLWISE_NSIDE) -> str:
    """S3 key of one AllWISE partition (Hive layout: healpix_k0 = the base pixel, healpix_k5 = the pixel)."""
    return f"{C.ALLWISE_S3_PREFIX}/healpix_k0={int(pix) // (nside * nside)}/healpix_k5={int(pix)}/part0.snappy.parquet"


_S3FS = None


def _allwise_s3_read(pix: int) -> pd.DataFrame:
    """ra, dec of one AllWISE partition straight from S3 (anonymous, columns projected)."""
    global _S3FS
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq
    if _S3FS is None:
        _S3FS = pafs.S3FileSystem(anonymous=True, region=C.ALLWISE_S3_REGION,
                                  connect_timeout=C.ALLWISE_TIMEOUT_S, request_timeout=C.ALLWISE_TIMEOUT_S)
    t = pq.read_table(f"{C.ALLWISE_S3_BUCKET}/{allwise_key(pix)}", filesystem=_S3FS, columns=list(C.ALLWISE_COLUMNS))
    return t.to_pandas()


def allwise_partition(store: Store, pix: int, reader=None) -> pd.DataFrame:
    """One partition's ra, dec: from the store's cache (ALLWISE_CACHE_DIR) when there, else S3 (then cached)."""
    rel = f"{C.ALLWISE_CACHE_DIR}/p{int(pix):05d}.parquet"
    if C.ALLWISE_CACHE and store.exists(rel):
        return store.read_parquet(rel)
    reader = reader or _allwise_s3_read
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            df = reader(int(pix))
            break
        except Exception:  # noqa: BLE001
            if attempt == C.NET_RETRIES:
                raise
            time.sleep(3 * attempt)
    df = df[list(C.ALLWISE_COLUMNS)].astype(np.float64).reset_index(drop=True)
    if C.ALLWISE_CACHE:
        try:
            store.write_parquet(rel, df)
        except Exception as e:  # noqa: BLE001  (a read-only root still gets the veto)
            L.warning("AllWISE cache %s not written: %s", rel, e)
    return df


def allwise_near(store: Store, ra: np.ndarray, dec: np.ndarray, radius_arcsec: float = C.ALLWISE_ARCSEC,
                 reader=None) -> tuple[np.ndarray, str, dict]:
    """True where an AllWISE source lies within radius (positions as catalogued, ~2010.5). Reads only the partitions
    the circles touch. Returns (hits, source name or "" if S3 failed, timing/size stats)."""
    from concurrent.futures import ThreadPoolExecutor
    n = len(ra)
    st: dict = {"partitions": 0, "rows": 0, "wall_s": 0.0}
    if n == 0:
        return np.zeros(0, bool), "none needed", st
    t0 = time.time()
    pix = allwise_pixels(ra, dec, radius_arcsec)
    try:
        with ThreadPoolExecutor(max_workers=C.ALLWISE_WORKERS) as ex:
            parts = list(ex.map(lambda p: allwise_partition(store, int(p), reader), pix))
    except Exception as e:  # noqa: BLE001
        L.warning("AllWISE on S3 failed: %s", str(e)[:300])
        st["wall_s"] = round(time.time() - t0, 1)
        return np.zeros(n, bool), "", st
    cat = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(C.ALLWISE_COLUMNS))
    hit = np.zeros(n, bool)
    if len(cat):
        sep, _ = SkyTree(cat["ra"].to_numpy(np.float64), cat["dec"].to_numpy(np.float64)).nearest(
            np.asarray(ra, np.float64), np.asarray(dec, np.float64))
        hit = sep <= radius_arcsec + 0.01
    st.update({"partitions": int(len(pix)), "rows": int(len(cat)), "wall_s": round(time.time() - t0, 1)})
    return hit, f"AllWISE (s3://{C.ALLWISE_S3_BUCKET}/{C.ALLWISE_S3_PREFIX}, epoch ~2010.5)", st


def gaia_near(ra, dec):
    return catalog_near(ra, dec, C.GAIA_ARCSEC, GAIA_SOURCES)


def catwise_near(ra, dec):
    return catalog_near(ra, dec, C.CATWISE_ARCSEC, CATWISE_SOURCES)


def wise_veto(store: Store, zone: C.Zone, refcat_hit: np.ndarray | None, have_refcat: bool,
              ra: np.ndarray, dec: np.ndarray, reader=None) -> tuple[np.ndarray, str, dict]:
    """The WISE veto of the remaining movers: (hits, source name or "" if nothing was checked, AllWISE stats).
    CatWISE2020 from the refcat when it has CatWISE rows (refcat_hit); else AllWISE on S3 (SUSPECTS_ALLWISE "auto"
    with a refcat, "always" also without one); without a refcat the CatWISE TAP service, as before."""
    none_needed = (np.zeros(0, bool), "none needed", {})
    if refcat_hit is not None:
        return refcat_hit, f"refcat/{zone.name}.parquet (CatWISE2020)", {}
    if C.SUSPECTS_ALLWISE != "off" and (have_refcat or C.SUSPECTS_ALLWISE == "always"):
        hit, name, st = allwise_near(store, ra, dec, reader=reader)
        if name or have_refcat:
            return hit, name, st
    if have_refcat:  # Gaia-only refcat, AllWISE off: nothing checked
        return np.zeros(len(ra), bool), "", {}
    if len(ra) == 0:
        return none_needed
    hit, name = catwise_near(ra, dec)
    return hit, name, {}


def select(movers: pd.DataFrame, fixed: pd.DataFrame, pred: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Every cut except Gaia. Returns the survivors with nearest_known_arcsec, and counts."""
    m = movers.copy()
    st = {"movers": int(len(m))}
    if len(m) == 0:
        return m, st
    m["nearest_known_arcsec"] = nearest_known_arcsec(m, pred)
    known = m["nearest_known_arcsec"] <= C.KNOWN_ARCSEC
    veto = (m["flags"].to_numpy(np.int64) & C.SUSPECT_VETO_BITS) != 0
    bright = near_bright_fixed(m, fixed)
    fpk = m["flags_peak_all"].to_numpy(np.int64) if "flags_peak_all" in m else np.zeros(len(m), np.int64)
    source = (fpk & C.SOURCE_BIT) != 0
    st.update({"cut_known": int(known.sum()), "cut_flags": int((~known & veto).sum()),
               "cut_bright_fixed": int((~known & ~veto & bright).sum()),
               "cut_source_bit": int((~known & ~veto & ~bright & source).sum())})
    return m[~known & ~veto & ~bright & ~source].reset_index(drop=True), st


def rank(s: pd.DataFrame) -> pd.DataFrame:
    """By grade (A, B, C) when graded, else by evidence (two_exposures first); then snr, highest first."""
    if len(s) == 0:
        return s
    if "grade" in s:
        s = s.assign(_ev=s["grade"].map(C.VET_GRADE_RANK).fillna(9))
    else:
        s = s.assign(_ev=s["evidence"].map(C.EVIDENCE_RANK).fillna(9))
    return s.sort_values(["_ev", "snr"], ascending=[True, False], kind="stable").drop(columns="_ev").reset_index(drop=True)


def to_json(s: pd.DataFrame, zone: C.Zone, data_release: str, vetting: dict | None = None) -> dict:
    """vetting: the zone-level fields (vetoes, provisional, grades, gates) when the suspects were graded."""
    def num(x, nd):
        return None if x is None or not np.isfinite(x) else round(float(x), nd)

    def track(r) -> dict:
        """The track's fields (null when on no track); {} when the suspects weren't joined to tracklets.parquet."""
        if "tracklet_id" not in s.columns:
            return {}
        on = int(r.tracklet_id) >= 0
        out = {}
        for k, nd in TRACK_JSON:
            v = getattr(r, k)
            v = float(v) if on and pd.notna(v) else np.nan
            out[k] = (int(v) if np.isfinite(v) else None) if nd is None else num(v, nd)
        return out

    graded = "grade" in s.columns
    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_release": data_release,
        "zone": zone.name,
        **(vetting or {}),
        "suspects": [{
            "id": r.id, "ra": round(float(r.ra), 6), "dec": round(float(r.dec), 6), "mjd": round(float(r.mjd), 6),
            "clock": "hours", "det": int(r.det), "evidence": r.evidence, "n_dots": int(r.n_dots),
            "snr": round(float(r.snr), 1), "flux_ujy": round(float(r.flux_ujy), 1), "cell": int(r.cell),
            "nearest_known_arcsec": num(r.nearest_known_arcsec, 1),
            **({"grade": r.grade, "reasons": [str(x) for x in r.reasons],
                "near_source_arcsec": num(getattr(r, "near_source_arcsec", np.nan), 1)} if graded else {}),
            **track(r),
        } for r in s.itertuples()],
    }
    return out


def near_source(seps: dict[str, np.ndarray], fixed: pd.DataFrame, ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    """Nearest refcat (any catalogue) or fixed source within VET_NEAR_SOURCE_ARCSEC (arcsec; NaN beyond)."""
    best = np.full(len(ra), np.inf)
    for v in seps.values():
        best = np.minimum(best, v)
    if len(fixed) and len(ra):
        sep, _ = SkyTree(fixed["ra"].to_numpy(np.float64), fixed["dec"].to_numpy(np.float64)).nearest(ra, dec)
        best = np.minimum(best, np.where(np.isfinite(sep), sep, np.inf))
    return np.where(best <= C.VET_NEAR_SOURCE_ARCSEC, best, np.nan)


def load_tracklets(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    """tracklets.parquet's mover_id and TRACK_COLUMNS, one row per mover_id (a row on a track wins). None when the file
    is missing or can't be read (then no suspect is graded A)."""
    rel = results_rel(zone.name, TRACKLETS_NAME)
    try:
        if not store.exists(rel):
            return None
        t = store.read_parquet(rel, columns=["mover_id", *TRACK_COLUMNS])
        t = t.assign(mover_id=t["mover_id"].astype(str),
                     tracklet_id=pd.to_numeric(t["tracklet_id"]).fillna(-1).astype(np.int64))
        return t.sort_values("tracklet_id", ascending=False, kind="stable").drop_duplicates("mover_id").reset_index(drop=True)
    except Exception as e:  # noqa: BLE001  (a malformed tracklets file must not stop the suspects)
        L.warning("suspects %s: %s unreadable, no grade A: %s", zone.name, rel, e)
        return None


def track_grades(s: pd.DataFrame, ids, tr: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    """Joins each graded suspect (ids: its suspect id, row by row) to its tracklets row (tr: load_tracklets) and sets
    grade A where the grade is B, tracklet_id >= 0, n_sessions >= VET_A_MIN_SESSIONS and rate_snr >= VET_MOTION_SIGMA.
    Adds TRACK_COLUMNS: tracklet_id (-1 = on no track), the others NaN / null off a track. tr None: s unchanged."""
    if tr is None or not len(s):
        return s, {"source": None if tr is None else TRACKLETS_NAME, "on_track": 0, "graded_a": 0}
    j = pd.DataFrame({"mover_id": [str(x) for x in ids]}).merge(tr, on="mover_id", how="left")
    s = s.copy()
    tid = j["tracklet_id"].fillna(-1).astype(np.int64).to_numpy()
    on = tid >= 0
    s["tracklet_id"] = tid
    nsess = pd.to_numeric(j["n_sessions"]).to_numpy(np.float64)
    s["n_sessions"] = pd.array(np.where(on & np.isfinite(nsess), nsess, np.nan), dtype="Int16")
    for c in ("rate_arcsec_h", "pa_deg", "rate_sigma", "rate_snr"):
        s[c] = np.where(on, pd.to_numeric(j[c]).to_numpy(np.float64), np.nan)
    a = ((s["grade"].to_numpy() == "B") & on & (np.nan_to_num(nsess, nan=0.0) >= C.VET_A_MIN_SESSIONS)
         & (np.nan_to_num(s["rate_snr"].to_numpy(np.float64), nan=-np.inf) >= C.VET_MOTION_SIGMA))
    s.loc[a, "grade"] = "A"
    return s, {"source": TRACKLETS_NAME, "on_track": int(on.sum()), "graded_a": int(a.sum())}


def grade(store: Store, zone: C.Zone, s: pd.DataFrame, movers: pd.DataFrame, fixed: pd.DataFrame,
          ref: pd.DataFrame | None, wise_source: str = "", allwise_reader=None) -> tuple[pd.DataFrame, dict]:
    """vet.vet on the suspects s, with the catalogue checks (2MASS from the refcat; AllWISE where CatWISE was the WISE
    veto: wise_source names it), near_source_arcsec from the refcat and fixed sources, then grade A from tracklets
    (track_grades)."""
    t0 = time.time()
    ra, dec, mjd = s["ra"].to_numpy(np.float64), s["dec"].to_numpy(np.float64), s["mjd"].to_numpy(np.float64)
    seps = refcat_seps(ref, ra, dec, mjd)
    cats: dict[str, np.ndarray] = {}
    if "tmass" in seps:
        cats["2mass"] = seps["tmass"] <= C.VET_CATALOG_ARCSEC + 0.01
    ast: dict = {}
    if C.VET_ALLWISE and "CatWISE" in wise_source and len(s):
        hit, name, ast = allwise_near(store, ra, dec, C.VET_CATALOG_ARCSEC, reader=allwise_reader)
        ast = {**ast, "source": name or "unreachable"}
        if name:
            cats["allwise"] = hit
    free = V.load_free(store, zone) if len(s) else None
    red = V.load_red(store, zone)
    s, st = V.vet(s, movers, free, red, cats)
    s["near_source_arcsec"] = near_source(seps, fixed, ra, dec)
    tr = load_tracklets(store, zone) if len(s) else None
    s, st["tracklets"] = track_grades(s, mover_ids(zone, s) if len(s) else [], tr)
    st["grades"] = {g_: int((s["grade"] == g_).sum()) if len(s) else 0 for g_ in ("A", "B", "C")}
    st["catalogs_checked"] = sorted(cats)
    if ast:
        st["allwise"] = ast
    st["near_source"] = int(np.isfinite(s["near_source_arcsec"].to_numpy(np.float64)).sum())
    st["wall_s"] = round(time.time() - t0, 1)
    return s, st


def run(store: Store, zone: C.Zone, pred: pd.DataFrame | None = None) -> dict:
    """pred: known.load_predictions' result when already loaded (tiles.py loads it once, in parallel)."""
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    movers = store.read_parquet(results_rel(zone.name, "movers.parquet"))
    fixed = store.read_parquet(results_rel(zone.name, "fixed.parquet"))
    if pred is None:
        pred = load_predictions(store, photos, zone)
    s, st = select(movers, fixed, pred)
    st["predictions"] = int(len(pred))
    ref = load_refcat(store, zone)
    local = refcat_near(store, zone, s["ra"].to_numpy(float), s["dec"].to_numpy(float),
                        s["mjd"].to_numpy(float), ref=ref) if len(s) and ref is not None else None
    if local is not None:
        gaia, source = local[0], f"refcat/{zone.name}.parquet (Gaia DR3 G < {C.REFCAT_GAIA_G_MAX:g})"
    else:
        gaia, source = gaia_near(s["ra"].to_numpy(float), s["dec"].to_numpy(float)) if len(s) else (np.zeros(0, bool), "none needed")
    st["gaia_source"] = source or "unreachable"
    st["cut_gaia"] = int(gaia.sum())
    s = s[~gaia].reset_index(drop=True)
    s["gaia_checked"] = bool(source)
    wise, wsource, wst = wise_veto(store, zone, local[1][~gaia] if local is not None and local[1] is not None else None,
                                   local is not None, s["ra"].to_numpy(float), s["dec"].to_numpy(float))
    if wst:
        st["allwise"] = wst
    st["catwise_source"] = wsource or "unreachable"
    st["cut_catwise"] = int(wise.sum())
    s = s[~wise].reset_index(drop=True)
    s["catwise_checked"] = bool(wsource)
    s, vst = grade(store, zone, s, movers, fixed, ref, wsource)
    st["vetting"] = vst
    wcat = "catwise" if "CatWISE" in wsource else "allwise" if "AllWISE" in wsource else ""
    vetting = {"vetoes": ["mpc", *(["gaia"] if source else []), *([wcat] if wcat else [])],
               # Gaia-only refcat with no WISE veto (or Gaia unreachable): red WISE-only sources may still be in the list
               "provisional": not (source and wsource), "grades": vst["grades"], "gates": vst["gates"]}
    s = rank(s)
    if len(s):
        s.insert(0, "id", list(mover_ids(zone, s)))  # = tracklets.parquet's mover_id
    else:
        s["id"] = pd.Series(dtype=str)
    collections = sorted(set(photos["collection"]))
    release = "+".join(c.replace("spherex_", "").upper() for c in collections)
    store.write_parquet(results_rel(zone.name, "suspects.parquet"), s)
    store.write_json(results_rel(zone.name, "suspects.json"), to_json(s, zone, release, vetting))
    st.update({"suspects": int(len(s)), "data_release": release, "wall_s": round(time.time() - t0, 1)})
    if not source:
        L.warning("suspects %s: Gaia unreachable; suspects are NOT checked against Gaia (gaia_checked=false)", zone.name)
    if not wsource:
        L.warning("suspects %s: no WISE catalogue checked (CatWISE/AllWISE unreachable or off); catwise_checked=false", zone.name)
    L.info("suspects %s: %s", zone.name, st)
    return st
