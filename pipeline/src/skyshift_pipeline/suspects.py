"""Step 6, `suspects`: movers nobody has catalogued.

Since 16 Nov only what is certain is removed (summary `cut_*`):
- a known body (MPC asteroids and comets, JPL Horizons planets and moons) in the same session: one with V < MPC_V_MAX
  or no V (known.brighter, position-based matching as before) within SUSPECT_KNOWN_CERTAIN_ARCSEC of the mover at its
  time, or within KNOWN_ARCSEC moving at our measured rate and in our direction (known_certain); one of any brightness
  only when known.certain_known is sure (within KNOWN_CERTAIN_ARCSEC at every dot's time, same rate and direction;
  summary `cut_known_certain_only` counts the movers only that rule removed). The exporter lists known bodies apart;
- a planet at its time (skyshift_core.planets, offline: Mars 60′, Jupiter 80′, Saturn 45′, Uranus 5′, Neptune 3′;
  the planet itself, its moons, listed or not, and its glare);
- a hot pixel: in one detector, every dot of >= 2 exposures on one pixel (±1 px) while the sky under it moved.
Everything else that used to drop a mover now stays on it as a warning (columns warn_codes, list of contracts/api.md
v3.23 check codes, and warn_values, a JSON object {code: {name: number}}; the exporter turns them into `checks`):
known_nearby (a known body within KNOWN_ARCSEC that isn't certainly it), flag_bloom / flag_halo / flag_ghost /
flag_crosstalk (NASA's record-only bits in a dot's 3×3), bright_star (a fixed source with snr >= BRIGHT_FIXED_SNR within
BRIGHT_FIXED_ARCSEC), catalogue_star (SOURCE at every dot's peak; a Gaia DR3, CatWISE2020, AllWISE or 2MASS source
within 6″, with their magnitudes), and the vetting's edge, persistence, sharp, flux_jump.
Catalogues: Gaia (G < 20), CatWISE2020 and 2MASS from refcat/<zone>.parquet (moved to the movers' times); only without
that file are the services asked. AllWISE (IRSA's HEALPix Parquet on AWS S3, only the partitions around the suspects,
cached under cache/allwise/k5) is the WISE catalogue of a Gaia-only refcat (SUSPECTS_ALLWISE) and a second one where
CatWISE2020 is (VET_ALLWISE). If a catalogue can't be reached the suspects are kept with gaia_checked / catwise_checked
= false and `provisional` says so.
Then the vetting gates (vet.py: hot pixel, bad column, detector edge, masked bit at the peak, sharp + OUTLIER, flux
ratio, red photo) and the catalogues within 6″ (reasons gaia, catwise, allwise, 2mass) and the warnings of
the strong warnings (w.gates: bloom, halo at every dot's peak, a bright star within 15″) grade every suspect: C fails one (kept, with `reasons`), B passes all (motion not shown). A = B and
on a track of `tracklets` (results/<zone>/tracklets.parquet, joined on the suspect id = its `mover_id`) with movers in
>= VET_A_MIN_SESSIONS sessions and rate_snr >= VET_MOTION_SIGMA: a candidate mover. Suspects on a track carry its
n_sessions, rate_arcsec_h, pa_deg, rate_sigma (rate_snr, tracklet_id in the parquet); without a readable
tracklets.parquet nothing changes.
Ranked by grade, then snr. Written as results/<zone>/suspects.parquet and results/<zone>/suspects.json in the
contracts/api.md suspects.json shape (+ grade, reasons, near_source_arcsec, the track's fields, warn_codes,
warn_values; zone-level vetoes, catalogues, provisional, grades, gates, warnings).
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
from skyshift_core.planets import near_planets

from . import config as C
from . import log
from . import vet as V
from .known import brighter, certain_known, load_predictions
from .sky import SkyTree, angsep, offset_radec
from .store import Store, photolist_rel, results_rel
from .tracklets import mover_ids

L = log.get("suspects")

TRACKLETS_NAME = "tracklets.parquet"
# tracklets.parquet columns a suspect on a track takes over (NaN / null when on no track); the JSON gets TRACK_JSON
TRACK_COLUMNS = ("tracklet_id", "n_sessions", "rate_arcsec_h", "pa_deg", "rate_sigma", "rate_snr")
TRACK_JSON = (("n_sessions", None), ("rate_arcsec_h", 2), ("pa_deg", 1), ("rate_sigma", 2))  # (field, decimals; None = int)


def nearest_known(movers: pd.DataFrame, pred: pd.DataFrame, search_arcsec: float = 600.0) -> pd.DataFrame:
    """The nearest known object in the same session, moved to the mover's time: sep (arcsec), name, rate (″/h), pa
    (degrees east of north), v (NaN / "" if none within search_arcsec). One row per mover."""
    n = len(movers)
    out = {"sep": np.full(n, np.nan), "name": np.array([""] * n, dtype=object), "rate": np.full(n, np.nan),
           "pa": np.full(n, np.nan), "v": np.full(n, np.nan)}
    if n == 0 or len(pred) == 0:
        return pd.DataFrame(out)
    tree = SkyTree(pred["ra"].to_numpy(float), pred["dec"].to_numpy(float))
    pm, ps = pred["mjd"].to_numpy(float), pred["session"].to_numpy(np.int64)
    pra, pdec = pred["ra"].to_numpy(float), pred["dec"].to_numpy(float)
    dra, ddec = pred["dra_arcsec_h"].to_numpy(float), pred["ddec_arcsec_h"].to_numpy(float)
    names = pred["name"].astype(str).to_numpy() if "name" in pred else np.array([""] * len(pred), dtype=object)
    vmag = pd.to_numeric(pred["v"], errors="coerce").to_numpy(float) if "v" in pred else np.full(len(pred), np.nan)
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
        sep = angsep(ra, dec, r2, d2) * 3600.0
        j = int(np.argmin(sep))
        k = int(cand[j])
        out["sep"][i] = float(sep[j])
        out["name"][i] = names[k]
        out["rate"][i] = float(np.hypot(dra[k], ddec[k]))
        out["pa"][i] = float(np.degrees(np.arctan2(dra[k], ddec[k])) % 360.0)
        out["v"][i] = vmag[k]
    return pd.DataFrame(out)


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


def catalog_seps(ra: np.ndarray, dec: np.ndarray, radius_arcsec: float, sources=GAIA_SOURCES) -> tuple[np.ndarray, str]:
    """Separation (arcsec) to the nearest catalogue source within radius (inf beyond). Tries each (name, table, kind) in
    turn. Returns (seps, name of the source used, "" if none answered)."""
    n = len(ra)
    sep = np.full(n, np.inf)
    if n == 0:
        return sep, "none needed"
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
                d, _ = SkyTree(np.array(found_ra), np.array(found_dec)).nearest(ra, dec)
                sep = np.where(np.isfinite(d) & (d <= radius_arcsec + 0.01), d, np.inf)
            return sep, name
        except Exception as e:  # noqa: BLE001
            L.warning("%s failed: %s", name, str(e)[:300])
    return sep, ""


REFCAT_COLUMNS = ("cat", "ra", "dec", "epoch_mjd", "pmra", "pmdec")
# magnitudes a catalogue_star warning reports (as catalogued: Gaia G, WISE W1/W2 and 2MASS J/K are Vega), when present
REFCAT_MAGS = {"gaia": ("g_mag",), "catwise": ("w1_mag", "w2_mag"), "tmass": ("j_mag", "k_mag")}


def load_refcat(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    """refcat/<zone>.parquet's cat, ra, dec, epoch_mjd, pmra, pmdec and the REFCAT_MAGS columns it has; None if the
    file is missing."""
    import io

    import pyarrow.parquet as pq

    from .refcat import refcat_rel
    rel = refcat_rel(zone.name)
    if not store.exists(rel):
        return None
    buf = io.BytesIO(store.read_bytes(rel))
    names = set(pq.read_schema(buf).names)
    mags = sorted({c for v in REFCAT_MAGS.values() for c in v} & names)
    buf.seek(0)
    return pd.read_parquet(buf, columns=[*REFCAT_COLUMNS, *mags])


def refcat_nearest(r: pd.DataFrame | None, ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray) -> dict[str, pd.DataFrame]:
    """Per catalogue in the refcat (gaia, catwise, tmass, ...), each moved by proper motion to the movers' median time
    (the zone spans ~6 months: < 0.3″ error at 1″/yr): the nearest entry's `sep` (arcsec, inf if none) and its
    REFCAT_MAGS (NaN where none). One row per position."""
    from .refcat import move_to_epoch
    out: dict[str, pd.DataFrame] = {}
    if r is None or len(ra) == 0:
        return out
    t = float(np.median(mjd))
    for cat, c in r.groupby("cat"):
        c = c.reset_index(drop=True)
        cra, cdec = move_to_epoch(c, t)
        sep, idx = SkyTree(np.asarray(cra, np.float64), np.asarray(cdec, np.float64)).nearest(ra, dec)
        ok = np.isfinite(sep) & (idx >= 0)
        j = np.where(ok, idx, 0)
        d = {"sep": np.where(ok, sep, np.inf)}
        for mag in REFCAT_MAGS.get(str(cat), ()):
            if mag in c:
                d[mag] = np.where(ok, c[mag].to_numpy(np.float64)[j], np.nan)
        out[str(cat)] = pd.DataFrame(d)
    return out


def refcat_seps(r: pd.DataFrame | None, ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray) -> dict[str, np.ndarray]:
    """Nearest separation (arcsec, inf if none) per catalogue in the refcat (refcat_nearest)."""
    return {k: v["sep"].to_numpy(np.float64) for k, v in refcat_nearest(r, ra, dec, mjd).items()}


def refcat_near(store: Store, zone: C.Zone, ra: np.ndarray, dec: np.ndarray,
                mjd: np.ndarray, ref: pd.DataFrame | None = None) -> tuple[np.ndarray, np.ndarray | None] | None:
    """(gaia hit, catwise hit) from refcat/<zone>.parquet (refcat_nearest within GAIA_ARCSEC / CATWISE_ARCSEC). None if
    the file is missing; catwise hit None when the file has no CatWISE rows (a Gaia-only refcat: nothing was checked).
    ref: the refcat when already loaded (load_refcat)."""
    r = ref if ref is not None else load_refcat(store, zone)
    if r is None:
        return None
    near = refcat_nearest(r, ra, dec, mjd) if len(ra) else {}
    has = set(r["cat"].astype(str))
    g = near["gaia"]["sep"].to_numpy() <= C.GAIA_ARCSEC + 0.01 if "gaia" in near else np.zeros(len(ra), bool)
    if "catwise" not in has:
        return g, None
    w = near["catwise"]["sep"].to_numpy() <= C.CATWISE_ARCSEC + 0.01 if "catwise" in near else np.zeros(len(ra), bool)
    return g, w


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
    """ALLWISE_COLUMNS (ra, dec, w1mpro, w2mpro) of one AllWISE partition straight from S3 (anonymous, columns
    projected)."""
    global _S3FS
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq
    if _S3FS is None:
        _S3FS = pafs.S3FileSystem(anonymous=True, region=C.ALLWISE_S3_REGION,
                                  connect_timeout=C.ALLWISE_TIMEOUT_S, request_timeout=C.ALLWISE_TIMEOUT_S)
    t = pq.read_table(f"{C.ALLWISE_S3_BUCKET}/{allwise_key(pix)}", filesystem=_S3FS, columns=list(C.ALLWISE_COLUMNS))
    return t.to_pandas()


def allwise_partition(store: Store, pix: int, reader=None) -> pd.DataFrame:
    """One partition's ALLWISE_COLUMNS: from the store's cache (ALLWISE_CACHE_DIR) when there, else S3 (then cached).
    A file cached before 16 Nov holds ra, dec only: used as it is (its magnitudes NaN: catalogue_star then lacks the
    AllWISE magnitudes), unless ALLWISE_REREAD_MAGS. A reader's missing magnitude column is NaN."""
    rel = f"{C.ALLWISE_CACHE_DIR}/p{int(pix):05d}.parquet"
    if C.ALLWISE_CACHE and store.exists(rel):
        df = store.read_parquet(rel)
        if all(c in df for c in C.ALLWISE_COLUMNS) or ("ra" in df and "dec" in df and not C.ALLWISE_REREAD_MAGS):
            return df.reindex(columns=list(C.ALLWISE_COLUMNS))
    reader = reader or _allwise_s3_read
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            df = reader(int(pix))
            break
        except Exception:  # noqa: BLE001
            if attempt == C.NET_RETRIES:
                raise
            time.sleep(3 * attempt)
    df = df.reindex(columns=list(C.ALLWISE_COLUMNS)).astype(np.float64).reset_index(drop=True)
    if C.ALLWISE_CACHE:
        try:
            store.write_parquet(rel, df)
        except Exception as e:  # noqa: BLE001  (a read-only root still gets the check)
            L.warning("AllWISE cache %s not written: %s", rel, e)
    return df


ALLWISE_NAME = f"AllWISE (s3://{C.ALLWISE_S3_BUCKET}/{C.ALLWISE_S3_PREFIX}, epoch ~2010.5)"


def allwise_nearest(store: Store, ra: np.ndarray, dec: np.ndarray, radius_arcsec: float = C.ALLWISE_ARCSEC,
                    reader=None) -> tuple[pd.DataFrame, str, dict]:
    """The nearest AllWISE source within radius (positions as catalogued, ~2010.5): `sep` (arcsec, inf beyond), w1_mag,
    w2_mag (w1mpro, w2mpro). Reads only the partitions the circles touch. Returns (table, source name or "" if S3
    failed, timing/size stats)."""
    from concurrent.futures import ThreadPoolExecutor
    n = len(ra)
    st: dict = {"partitions": 0, "rows": 0, "wall_s": 0.0}
    empty = pd.DataFrame({"sep": np.full(n, np.inf), "w1_mag": np.full(n, np.nan), "w2_mag": np.full(n, np.nan)})
    if n == 0:
        return empty, "none needed", st
    t0 = time.time()
    pix = allwise_pixels(ra, dec, radius_arcsec)
    try:
        with ThreadPoolExecutor(max_workers=C.ALLWISE_WORKERS) as ex:
            parts = list(ex.map(lambda p: allwise_partition(store, int(p), reader), pix))
    except Exception as e:  # noqa: BLE001
        L.warning("AllWISE on S3 failed: %s", str(e)[:300])
        st["wall_s"] = round(time.time() - t0, 1)
        return empty, "", st
    cat = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(C.ALLWISE_COLUMNS))
    out = empty
    if len(cat):
        sep, idx = SkyTree(cat["ra"].to_numpy(np.float64), cat["dec"].to_numpy(np.float64)).nearest(
            np.asarray(ra, np.float64), np.asarray(dec, np.float64))
        ok = np.isfinite(sep) & (idx >= 0) & (sep <= radius_arcsec + 0.01)
        j = np.where(ok, idx, 0)
        out = pd.DataFrame({"sep": np.where(ok, sep, np.inf),
                            "w1_mag": np.where(ok, cat["w1mpro"].to_numpy(np.float64)[j], np.nan) if "w1mpro" in cat else np.nan,
                            "w2_mag": np.where(ok, cat["w2mpro"].to_numpy(np.float64)[j], np.nan) if "w2mpro" in cat else np.nan})
    st.update({"partitions": int(len(pix)), "rows": int(len(cat)), "wall_s": round(time.time() - t0, 1)})
    return out, ALLWISE_NAME, st


def gaia_seps(ra, dec):
    return catalog_seps(ra, dec, C.GAIA_ARCSEC, GAIA_SOURCES)


def catwise_seps(ra, dec):
    return catalog_seps(ra, dec, C.CATWISE_ARCSEC, CATWISE_SOURCES)


def catalogue_matches(store: Store, zone: C.Zone, ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray,
                      ref: pd.DataFrame | None, allwise_reader=None) -> tuple[dict[str, pd.DataFrame], dict]:
    """The catalogue sources near each position, for the catalogue reasons and the catalogue_star warning:
    {"gaia" | "catwise" | "allwise" | "2mass": table of `sep` (arcsec, inf if none near) and magnitudes}, only the
    catalogues that were checked. Gaia and CatWISE2020 (and 2MASS) from the refcat; without a refcat the TAP services.
    AllWISE (S3) is the WISE catalogue of a Gaia-only refcat (SUSPECTS_ALLWISE) and a second one where CatWISE2020 is
    (VET_ALLWISE). info: gaia_source, catwise_source (the WISE catalogue's name, "" when none answered), allwise stats."""
    n = len(ra)
    out: dict[str, pd.DataFrame] = {}
    info: dict = {}
    near = refcat_nearest(ref, ra, dec, mjd) if ref is not None and n else {}
    has = set(ref["cat"].astype(str)) if ref is not None else set()
    inf = pd.DataFrame({"sep": np.full(n, np.inf)})
    if ref is not None:
        out["gaia"] = near.get("gaia", inf)
        info["gaia_source"] = f"refcat/{zone.name}.parquet (Gaia DR3 G < {C.REFCAT_GAIA_G_MAX:g})"
    else:
        sep, name = gaia_seps(ra, dec) if n else (np.zeros(0), "none needed")
        info["gaia_source"] = name
        if name:
            out["gaia"] = pd.DataFrame({"sep": sep})
    wise = ""
    if "catwise" in has:
        out["catwise"] = near.get("catwise", inf)
        info["catwise_source"] = f"refcat/{zone.name}.parquet (CatWISE2020)"
        wise = "catwise"
    allwise_on = (C.VET_ALLWISE if wise else C.SUSPECTS_ALLWISE != "off" and (ref is not None or C.SUSPECTS_ALLWISE == "always"))
    if allwise_on:
        t, name, ast = allwise_nearest(store, ra, dec, C.ALLWISE_ARCSEC, reader=allwise_reader)
        info["allwise"] = {**ast, "source": name or "unreachable"}
        if name:
            out["allwise"] = t
            if not wise:
                info["catwise_source"] = name
                wise = "allwise"
    if not wise:
        if ref is None:
            sep, name = catwise_seps(ra, dec) if n else (np.zeros(0), "none needed")
            info["catwise_source"] = name
            if name:
                out["catwise"] = pd.DataFrame({"sep": sep})
                wise = "catwise"
        else:
            info["catwise_source"] = ""
    info["wise"] = wise
    if "tmass" in near:
        out["2mass"] = near["tmass"]
    return out, info


# catalogue reason -> (radius, prefix of its numbers in catalogue_star's warn_values, magnitude columns)
CATALOGUE_WARN = {"gaia": ("GAIA_ARCSEC", "gaia", ("g_mag",)), "catwise": ("CATWISE_ARCSEC", "catwise", ("w1_mag", "w2_mag")),
                  "allwise": ("ALLWISE_ARCSEC", "allwise", ("w1_mag", "w2_mag")),
                  "2mass": ("VET_CATALOG_ARCSEC", "tmass", ("j_mag", "k_mag"))}


def catalogue_hits(cats: dict[str, pd.DataFrame]) -> dict[str, np.ndarray]:
    """Per checked catalogue: a source within its radius (CATALOGUE_WARN)."""
    return {k: t["sep"].to_numpy(np.float64) <= getattr(C, CATALOGUE_WARN[k][0]) + 0.01 for k, t in cats.items()}


def catalogue_fainter(cats: dict[str, pd.DataFrame], flux_ujy: np.ndarray) -> dict[str, np.ndarray]:
    """Per checked catalogue: how much fainter (mag, AB) its nearest source is than the mover, in the catalogue's
    brightest band (Vega + SUSPECT_VEGA_TO_AB); NaN without a magnitude or a flux."""
    mab = ab_mag(flux_ujy)
    out = {}
    for k, t in cats.items():
        best = np.full(len(t), np.nan)
        for mg in CATALOGUE_WARN[k][2]:
            if mg in t:
                best = np.fmin(best, t[mg].to_numpy(np.float64) + C.SUSPECT_VEGA_TO_AB[mg])
        out[k] = best - mab
    return out


def catalogue_reasons(cats: dict[str, pd.DataFrame], flux_ujy: np.ndarray) -> dict[str, np.ndarray]:
    """Per checked catalogue: catalogued (a vetting reason, grade C) = a source within its radius that is not more than
    SUSPECT_CATALOGUE_FAINTER_MAG fainter than the mover (or of unknown brightness). A much fainter one is a chance
    neighbour (catalogue_star warning only)."""
    hits, fz = catalogue_hits(cats), catalogue_fainter(cats, flux_ujy)
    return {k: hits[k] & ~(np.nan_to_num(fz[k], nan=-np.inf) > C.SUSPECT_CATALOGUE_FAINTER_MAG) for k in cats}


def catalogue_warnings(cats: dict[str, pd.DataFrame], flux_ujy: np.ndarray, w: Warnings) -> dict[str, int]:
    """catalogue_star where any checked catalogue has a source within its radius: sep_arcsec (the nearest), per
    catalogue <cat>_sep_arcsec and its magnitudes as catalogued (Vega), the mover's own mover_ab_mag, and fainter_mag:
    how much fainter than the mover the brightest of them is (AB; > SUSPECT_CATALOGUE_FAINTER_MAG: a chance neighbour,
    not a reason)."""
    hits, fz = catalogue_hits(cats), catalogue_fainter(cats, flux_ujy)
    mab = ab_mag(flux_ujy)
    for i in range(len(flux_ujy)):
        v: dict = {}
        best, faint = np.inf, np.nan
        for k, t in cats.items():
            if not hits[k][i]:
                continue
            _, pre, mags = CATALOGUE_WARN[k]
            sep = float(t["sep"].iloc[i])
            best = min(best, sep)
            faint = np.fmin(faint, fz[k][i])
            v[f"{pre}_sep_arcsec"] = _num(sep, 1)
            for mg in mags:
                if mg in t:
                    v[f"{pre}_{mg}"] = _num(t[mg].iloc[i], 2)
        if v:
            w.add(i, "catalogue_star", {"sep_arcsec": _num(best, 1), **v, "mover_ab_mag": _num(mab[i], 2),
                                        "fainter_mag": _num(faint, 1)})
    return {k: int(h.sum()) for k, h in hits.items()}


def near_planet_names(movers: pd.DataFrame) -> np.ndarray:
    """The planet each mover lies near at its time ("" for none): the planet itself, one of its moons or its glare
    (skyshift_core.planets: MPC orbits hold no planets or moons)."""
    if len(movers) == 0:
        return np.zeros(0, dtype=object)
    return near_planets(movers["ra"].to_numpy(np.float64), movers["dec"].to_numpy(np.float64),
                        movers["mjd"].to_numpy(np.float64))[0]


# --- warnings: what can't be ruled out, kept on the suspect with its numbers (contracts/api.md v3.23 check codes) -----
def ab_mag(flux_ujy) -> np.ndarray:
    """AB magnitude of a flux in µJy (NaN for flux <= 0)."""
    f = np.asarray(flux_ujy, np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(f > 0, 23.9 - 2.5 * np.log10(np.where(f > 0, f, 1.0)), np.nan)


def _num(v, nd: int = 2):
    """A finite float rounded (int when nd == 0), else None (left out of warn_values)."""
    if v is None:
        return None
    v = float(v)
    if not np.isfinite(v):
        return None
    return int(round(v)) if nd == 0 else round(v, nd)


class Warnings:
    """Per row: warn codes (in order of adding, each once) and their numbers {code: {name: number}}; gates: the vetting
    reasons some warnings carry when they are strong ({reason: bool per row}: bloom, halo, bright_star)."""

    def __init__(self, n: int):
        self.n = n
        self.codes: list[list[str]] = [[] for _ in range(n)]
        self.values: list[dict] = [{} for _ in range(n)]
        self.gates: dict[str, np.ndarray] = {}

    def gate(self, reason: str, hit: np.ndarray) -> None:
        self.gates[reason] = self.gates.get(reason, np.zeros(self.n, bool)) | np.asarray(hit, bool)

    def add(self, i: int, code: str, values: dict | None = None) -> None:
        if code not in self.codes[i]:
            self.codes[i].append(code)
        v = self.values[i].setdefault(code, {})
        for k, x in (values or {}).items():
            if isinstance(x, (bool, np.bool_)):
                x = int(x)
            if x is not None and (not isinstance(x, float) or np.isfinite(x)):
                v[k] = x

    def take(self, keep: np.ndarray) -> "Warnings":
        idx = np.flatnonzero(keep)
        w = Warnings(len(idx))
        w.codes = [self.codes[i] for i in idx]
        w.values = [self.values[i] for i in idx]
        w.gates = {k: v[idx] for k, v in self.gates.items()}
        return w

    def count(self, code: str) -> int:
        return int(sum(code in c for c in self.codes))


def warn_columns(w: Warnings) -> dict:
    """The parquet columns: warn_codes (list of strings) and warn_values (JSON object string, {code: {name: number}})."""
    return {"warn_codes": [list(c) for c in w.codes],
            "warn_values": [json.dumps({k: v[k] for k in c}, separators=(",", ":"), sort_keys=False)
                            for c, v in zip(w.codes, w.values)]}


def mover_motion(m: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(rate ″/h, pa deg, measured) from link's own fit of the mover's dots; measured = motion_snr >=
    SUSPECT_KNOWN_MOTION_SNR (two exposures) or a straight-line fit of >= 3 exposures with a finite rate."""
    n = len(m)
    rate = m["rate_arcsec_h"].to_numpy(np.float64) if "rate_arcsec_h" in m else np.full(n, np.nan)
    pa = m["pa_deg"].to_numpy(np.float64) if "pa_deg" in m else np.full(n, np.nan)
    msnr = m["motion_snr"].to_numpy(np.float64) if "motion_snr" in m else np.full(n, np.nan)
    nexp = m["n_exposures"].to_numpy(np.float64) if "n_exposures" in m else np.full(n, np.nan)
    measured = np.isfinite(rate) & np.isfinite(pa) & (
        (np.nan_to_num(msnr, nan=0.0) >= C.SUSPECT_KNOWN_MOTION_SNR) | (~np.isfinite(msnr) & (np.nan_to_num(nexp) >= 3)))
    return rate, pa, measured


def known_certain(m: pd.DataFrame, near: pd.DataFrame) -> np.ndarray:
    """True where the nearest known body certainly is the mover: within SUSPECT_KNOWN_CERTAIN_ARCSEC at the mover's time
    (place alone: our motion from 2–4 exposures over minutes scatters too much to overrule it), or within KNOWN_ARCSEC
    with our motion measured (mover_motion) at its rate (SUSPECT_KNOWN_RATE_TOL or 3σ of ours) and in its direction
    (SUSPECT_KNOWN_PA_TOL_DEG or 3σ): a body whose predicted place is off but which moves like the mover."""
    sep = np.nan_to_num(near["sep"].to_numpy(np.float64), nan=np.inf)
    rate, pa, measured = mover_motion(m)
    krate, kpa = near["rate"].to_numpy(np.float64), near["pa"].to_numpy(np.float64)
    msnr = m["motion_snr"].to_numpy(np.float64) if "motion_snr" in m else np.full(len(m), np.nan)
    sig = np.where(np.isfinite(msnr) & (msnr > 0), rate / np.where(msnr > 0, msnr, 1.0), 0.0)  # our rate's 1σ
    rate_ok = np.abs(rate - krate) <= np.maximum(C.SUSPECT_KNOWN_RATE_TOL * krate, 3.0 * sig)
    pa_sig = np.degrees(np.where(rate > 0, sig / np.where(rate > 0, rate, 1.0), 0.0))
    dpa = np.abs((pa - kpa + 180.0) % 360.0 - 180.0)
    pa_ok = dpa <= np.maximum(C.SUSPECT_KNOWN_PA_TOL_DEG, 3.0 * pa_sig)
    moves_alike = measured & np.isfinite(krate) & (krate > 0) & rate_ok & pa_ok
    return (sep <= C.SUSPECT_KNOWN_CERTAIN_ARCSEC) | ((sep <= C.KNOWN_ARCSEC) & moves_alike)


def bright_fixed(movers: pd.DataFrame, fixed: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(separation ″, S/N, flux µJy) of the nearest fixed source with snr >= BRIGHT_FIXED_SNR (inf / NaN if none)."""
    n = len(movers)
    if n == 0 or len(fixed) == 0:
        return np.full(n, np.inf), np.full(n, np.nan), np.full(n, np.nan)
    b = fixed[fixed["snr"] >= C.BRIGHT_FIXED_SNR]
    sep, idx = SkyTree(b["ra"].to_numpy(float), b["dec"].to_numpy(float)).nearest(movers["ra"].to_numpy(float),
                                                                                  movers["dec"].to_numpy(float))
    ok = np.isfinite(sep) & (idx >= 0)
    j = np.where(ok, idx, 0)
    snr = np.where(ok, b["snr"].to_numpy(float)[j], np.nan) if len(b) else np.full(n, np.nan)
    flux = (np.where(ok, b["flux_ujy"].to_numpy(float)[j], np.nan) if len(b) and "flux_ujy" in b else np.full(n, np.nan))
    return np.where(ok, sep, np.inf), snr, flux


def hot_pixel_certain(m: pd.DataFrame) -> np.ndarray:
    """True where, in one detector, the mover's dots of >= 2 exposures all sit on one pixel (within SUSPECT_HOT_PIXEL_PX
    of their median in x and y) while the sky under them moved >= SUSPECT_HOT_SKY_PX pixels (link's fitted rate × the
    time between them): a pixel fault, not a source on the sky. Movers without dot lists or a rate: False."""
    out = np.zeros(len(m), bool)
    d = V.explode(m)
    if not len(d) or "rate_arcsec_h" not in m:
        return out
    g = d.groupby(["i", "det"])
    dev = np.maximum((d["x"] - g["x"].transform("median")).abs(), (d["y"] - g["y"].transform("median")).abs())
    a = d.assign(dev=dev).groupby(["i", "det"]).agg(n=("obs", "nunique"), dev=("dev", "max"),
                                                    t0=("mjd", "min"), t1=("mjd", "max")).reset_index()
    rate = m["rate_arcsec_h"].to_numpy(np.float64)[a["i"].to_numpy()]
    sky_px = np.nan_to_num(rate * (a["t1"] - a["t0"]).to_numpy() * 24.0 / C.PIXEL_ARCSEC, nan=0.0)
    hit = (a["n"].to_numpy() >= 2) & (a["dev"].to_numpy() <= C.SUSPECT_HOT_PIXEL_PX) & (sky_px >= C.SUSPECT_HOT_SKY_PX)
    out[a["i"].to_numpy()[hit]] = True
    return out


def flag_warnings(m: pd.DataFrame, w: Warnings) -> dict:
    """flag_bloom, flag_halo, flag_ghost (GHOST, GHOST_FPA, GHOST_EXT), flag_crosstalk: NASA's record-only bit in the
    3×3 of any of the mover's dots (`flags`). Values: dots_at_peak (dots with the bit at their peak pixel), n_dots.
    The bits of SUSPECT_PEAK_ALL_GATES at every dot's peak (`flags_peak_all`) are also vetting reasons (w.gates: bloom,
    halo). Returns {code: movers}."""
    f = m["flags"].to_numpy(np.int64) & 0xFFFFFFFF
    fpk = (list(m["dot_fpk"]) if "dot_fpk" in m else [[]] * len(m))
    nd = m["n_dots"].to_numpy(np.int64) if "n_dots" in m else np.zeros(len(m), np.int64)
    fpa = m["flags_peak_all"].to_numpy(np.int64) & 0xFFFFFFFF if "flags_peak_all" in m else np.zeros(len(m), np.int64)
    out = {}
    for code, bits in C.SUSPECT_FLAG_WARN.items():
        hit = np.flatnonzero((f & bits) != 0)
        for i in hit:
            pk = np.asarray(fpk[i], np.int64) & 0xFFFFFFFF if fpk[i] is not None else np.zeros(0, np.int64)
            w.add(int(i), code, {"dots_at_peak": int(((pk & bits) != 0).sum()), "n_dots": int(nd[i])})
        out[code] = int(hit.size)
    for reason, bit in C.SUSPECT_PEAK_ALL_GATES.items():
        w.gate(reason, (fpa & bit) != 0)
    return out


def select(movers: pd.DataFrame, fixed: pd.DataFrame, pred: pd.DataFrame) -> tuple[pd.DataFrame, dict, Warnings]:
    """Removes only what is certain: a known body (known_certain on bodies with V < MPC_V_MAX or no V: place, and
    rate and direction where measured; known.certain_known on any V), a planet, its moons or its glare, and a hot pixel (hot_pixel_certain: one pixel while the sky moved). Every other
    former cut stays as a warning: known_nearby (a known body within KNOWN_ARCSEC that isn't certainly it), the flag_*
    bits, bright_star (a fixed source with snr >= BRIGHT_FIXED_SNR within BRIGHT_FIXED_ARCSEC) and catalogue_star
    (NASA's SOURCE bit at every dot's peak). Returns the survivors with nearest_known_arcsec and nearest_known_name,
    counts and their warnings (row by row)."""
    m = movers.copy()
    st = {"movers": int(len(m))}
    if len(m) == 0:
        m["nearest_known_arcsec"] = pd.Series(dtype=float)
        m["nearest_known_name"] = pd.Series(dtype=str)
        return m, st, Warnings(0)
    near = nearest_known(m, brighter(pred))  # V < MPC_V_MAX or no V: position-based matching, as before 16 Nov
    m["nearest_known_arcsec"] = near["sep"].to_numpy()
    m["nearest_known_name"] = near["name"].to_numpy()
    place = known_certain(m, near)
    sure = certain_known(m, pred)["known_name"].to_numpy(object) != ""  # any V: place, rate and direction (known.py)
    known = place | sure
    planet = near_planet_names(m) != ""
    hot = hot_pixel_certain(m)
    gone = known | planet | hot
    st.update({"cut_known": int(known.sum()), "cut_known_certain_only": int((sure & ~place).sum()),
               "cut_planet": int((~known & planet).sum()), "cut_hot_pixel": int((~known & ~planet & hot).sum())})
    keep = ~gone
    m = m[keep].reset_index(drop=True)
    near = near[keep].reset_index(drop=True)
    w = Warnings(len(m))
    rate, pa, measured = mover_motion(m)
    for i in np.flatnonzero(np.nan_to_num(near["sep"].to_numpy(np.float64), nan=np.inf) <= C.KNOWN_ARCSEC):
        w.add(int(i), "known_nearby", {"sep_arcsec": _num(near["sep"][i], 1), "known_rate_arcsec_h": _num(near["rate"][i], 1),
                                       "known_pa_deg": _num(near["pa"][i], 0), "known_v_mag": _num(near["v"][i], 1),
                                       "rate_arcsec_h": _num(rate[i], 1) if measured[i] else None,
                                       "pa_deg": _num(pa[i], 0) if measured[i] else None})
    st["warn_known_nearby"] = w.count("known_nearby")
    for code, k in flag_warnings(m, w).items():
        st[f"warn_{code}"] = k
    bsep, bsnr, bflux = bright_fixed(m, fixed)
    mflux = m["flux_ujy"].to_numpy(np.float64) if "flux_ujy" in m else np.full(len(m), np.nan)
    for i in np.flatnonzero(bsep <= C.BRIGHT_FIXED_ARCSEC):
        w.add(int(i), "bright_star", {"sep_arcsec": _num(bsep[i], 1), "star_snr": _num(bsnr[i], 0),
                                      "star_ab_mag": _num(ab_mag(bflux[i]), 1),
                                      "star_flux_ratio": _num(bflux[i] / mflux[i] if mflux[i] > 0 else np.nan, 1)})
    st["warn_bright_star"] = w.count("bright_star")
    w.gate("bright_star", bsep <= C.SUSPECT_BRIGHT_GATE_ARCSEC)
    fpk = m["flags_peak_all"].to_numpy(np.int64) if "flags_peak_all" in m else np.zeros(len(m), np.int64)
    src = np.flatnonzero((fpk & C.SOURCE_BIT) != 0)
    for i in src:
        w.add(int(i), "catalogue_star", {"source_mask_all_dots": 1})
    st["warn_source_bit"] = int(src.size)
    return m, st, w


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
    warned = "warn_codes" in s.columns and "warn_values" in s.columns
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
            **({"warn_codes": [str(x) for x in r.warn_codes], "warn_values": json.loads(r.warn_values)} if warned else {}),
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


def gate_warnings(s: pd.DataFrame, w: Warnings) -> None:
    """Warnings with numbers for the vetting gates that have a check code (contracts/api.md v3.23): edge (edge_px),
    persistence (PERSIST at a dot's peak, one of masked_peak's bits: dots_at_peak), sharp (sharp_outlier or very_sharp:
    the smallest size_ratio to a star, outlier_at_peak), flux_jump (flux_ratio: the largest ratio within a detector).
    The other gates (hot_pixel, bad_column, masked_peak, red_photo, not_vetted) are in `reasons` only."""
    if not len(s):
        return
    num = V.dot_numbers(s)
    nd = s["n_dots"].to_numpy(np.int64) if "n_dots" in s else np.zeros(len(s), np.int64)

    def gate(k):
        return s[f"gate_{k}"].to_numpy(bool) if f"gate_{k}" in s else np.zeros(len(s), bool)

    for i in np.flatnonzero(gate("edge")):
        w.add(int(i), "edge", {"edge_px": _num(num["edge_px"][i], 1)})
    for i in np.flatnonzero(num["persist_dots"].to_numpy() > 0):
        w.add(int(i), "persistence", {"dots_at_peak": int(num["persist_dots"][i]), "n_dots": int(nd[i])})
    so, vs = gate("sharp_outlier"), gate("very_sharp")
    for i in np.flatnonzero(so | vs):
        w.add(int(i), "sharp", {"size_ratio": _num(num["size_ratio"][i], 2), "outlier_at_peak": int(so[i])})
    for i in np.flatnonzero(gate("flux_ratio")):
        w.add(int(i), "flux_jump", {"ratio": _num(num["flux_ratio"][i], 1)})


def grade(store: Store, zone: C.Zone, s: pd.DataFrame, movers: pd.DataFrame, fixed: pd.DataFrame,
          ref: pd.DataFrame | None, cats: dict[str, pd.DataFrame] | None = None,
          w: Warnings | None = None) -> tuple[pd.DataFrame, dict]:
    """vet.vet on the suspects s with the catalogue reasons (cats: catalogue_matches' tables; a catalogue left out was
    not checked) and the warnings' gates (w.gates: bloom, halo, bright_star), near_source_arcsec from
    the refcat and fixed sources, then grade A from tracklets (track_grades). w (row by row with s) gains the gates'
    warnings (gate_warnings)."""
    t0 = time.time()
    w = w if w is not None else Warnings(len(s))
    ra, dec, mjd = s["ra"].to_numpy(np.float64), s["dec"].to_numpy(np.float64), s["mjd"].to_numpy(np.float64)
    seps = refcat_seps(ref, ra, dec, mjd)
    flux = s["flux_ujy"].to_numpy(np.float64) if len(s) else np.zeros(0)
    extra = {k: v for k, v in catalogue_reasons(cats or {}, flux).items() if k in C.VET_GATES}
    for reason, hit in w.gates.items():
        if reason in C.VET_GATES and len(hit) == len(s):
            extra[reason] = hit
    free = V.load_free(store, zone) if len(s) else None
    red = V.load_red(store, zone)
    s, st = V.vet(s, movers, free, red, extra)
    gate_warnings(s, w)
    s["near_source_arcsec"] = near_source(seps, fixed, ra, dec)
    tr = load_tracklets(store, zone) if len(s) else None
    s, st["tracklets"] = track_grades(s, mover_ids(zone, s) if len(s) else [], tr)
    st["grades"] = {g_: int((s["grade"] == g_).sum()) if len(s) else 0 for g_ in ("A", "B", "C")}
    st["catalogs_checked"] = sorted(k for k in (cats or {}) if k in C.VET_GATES)
    st["near_source"] = int(np.isfinite(s["near_source_arcsec"].to_numpy(np.float64)).sum())
    st["wall_s"] = round(time.time() - t0, 1)
    return s, st


def run(store: Store, zone: C.Zone, pred: pd.DataFrame | None = None, allwise_reader=None) -> dict:
    """pred: known.load_predictions' result when already loaded (tiles.py loads it once, in parallel)."""
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    movers = store.read_parquet(results_rel(zone.name, "movers.parquet"))
    fixed = store.read_parquet(results_rel(zone.name, "fixed.parquet"))
    if pred is None:
        pred = load_predictions(store, photos, zone)
    s, st, w = select(movers, fixed, pred)
    st["predictions"] = int(len(pred))
    ref = load_refcat(store, zone)
    ra, dec, mjd = s["ra"].to_numpy(np.float64), s["dec"].to_numpy(np.float64), s["mjd"].to_numpy(np.float64)
    cats, info = catalogue_matches(store, zone, ra, dec, mjd, ref, allwise_reader=allwise_reader)
    source, wsource = info.get("gaia_source", ""), info.get("catwise_source", "")
    st["gaia_source"] = source or "unreachable"
    st["catwise_source"] = wsource or "unreachable"
    if "allwise" in info:
        st["allwise"] = info["allwise"]
    st["warn_catalogue"] = catalogue_warnings(cats, s["flux_ujy"].to_numpy(np.float64) if len(s) else np.zeros(0), w)
    s["gaia_checked"] = bool(source)
    s["catwise_checked"] = bool(info.get("wise"))
    s, vst = grade(store, zone, s, movers, fixed, ref, cats, w)
    st["vetting"] = vst
    st["warnings"] = {c: w.count(c) for c in sorted({c for cs in w.codes for c in cs})}
    vetting = {"vetoes": ["mpc", "planets"],
               "catalogues": [k for k in ("gaia", "catwise", "allwise", "2mass") if k in cats],
               # Gaia or every WISE catalogue unreachable: red WISE-only sources may be listed without a catalogue_star
               "provisional": not (source and info.get("wise")), "grades": vst["grades"], "gates": vst["gates"],
               "warnings": st["warnings"]}
    for k, v in warn_columns(w).items():
        s[k] = v
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
