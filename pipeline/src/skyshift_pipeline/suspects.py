"""Step 6, `suspects`: movers nobody has catalogued.

A mover becomes a suspect when:
- no known object (SkyBoT, any brightness) lies within KNOWN_ARCSEC of it in the same session;
- it has none of the GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK, HALO or BLOOM bits;
- no bright fixed source (snr >= BRIGHT_FIXED_SNR) lies within BRIGHT_FIXED_ARCSEC;
- the SOURCE bit is not set at the peak pixel of every one of its dots (SOURCE marks catalogue
  stars and compact galaxies to AB ~19);
- no Gaia DR3 star within GAIA_ARCSEC and no CatWISE2020 source within CATWISE_ARCSEC (faint
  sources near our limit flicker in and out). Both are asked only around the remaining movers,
  in batches. If a catalogue can't be reached the suspects are kept with gaia_checked /
  catwise_checked = false and the summary says so.
Ranked by evidence (two_exposures first), then snr. Written as results/<zone>/suspects.parquet
and results/<zone>/suspects.json in the contracts/api.md suspects.json shape.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .known import load_predictions
from .sky import SkyTree, angsep, offset_radec
from .store import Store, photolist_rel, results_rel

L = log.get("suspects")


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


def gaia_near(ra, dec):
    return catalog_near(ra, dec, C.GAIA_ARCSEC, GAIA_SOURCES)


def catwise_near(ra, dec):
    return catalog_near(ra, dec, C.CATWISE_ARCSEC, CATWISE_SOURCES)


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
    if len(s) == 0:
        return s
    s = s.assign(_ev=s["evidence"].map(C.EVIDENCE_RANK).fillna(9))
    return s.sort_values(["_ev", "snr"], ascending=[True, False]).drop(columns="_ev").reset_index(drop=True)


def to_json(s: pd.DataFrame, zone: C.Zone, data_release: str) -> dict:
    def num(x, nd):
        return None if x is None or not np.isfinite(x) else round(float(x), nd)

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_release": data_release,
        "zone": zone.name,
        "suspects": [{
            "id": r.id, "ra": round(float(r.ra), 6), "dec": round(float(r.dec), 6), "mjd": round(float(r.mjd), 6),
            "clock": "hours", "det": int(r.det), "evidence": r.evidence, "n_dots": int(r.n_dots),
            "snr": round(float(r.snr), 1), "flux_ujy": round(float(r.flux_ujy), 1), "cell": int(r.cell),
            "nearest_known_arcsec": num(r.nearest_known_arcsec, 1),
        } for r in s.itertuples()],
    }


def run(store: Store, zone: C.Zone) -> dict:
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    movers = store.read_parquet(results_rel(zone.name, "movers.parquet"))
    fixed = store.read_parquet(results_rel(zone.name, "fixed.parquet"))
    pred = load_predictions(store, photos, zone)
    s, st = select(movers, fixed, pred)
    st["predictions"] = int(len(pred))
    gaia, source = gaia_near(s["ra"].to_numpy(float), s["dec"].to_numpy(float)) if len(s) else (np.zeros(0, bool), "none needed")
    st["gaia_source"] = source or "unreachable"
    st["cut_gaia"] = int(gaia.sum())
    s = s[~gaia].reset_index(drop=True)
    s["gaia_checked"] = bool(source)
    wise, wsource = catwise_near(s["ra"].to_numpy(float), s["dec"].to_numpy(float)) if len(s) else (np.zeros(0, bool), "none needed")
    st["catwise_source"] = wsource or "unreachable"
    st["cut_catwise"] = int(wise.sum())
    s = s[~wise].reset_index(drop=True)
    s["catwise_checked"] = bool(wsource)
    s = rank(s)
    if len(s):
        s.insert(0, "id", [f"{zone.name}-{int(c)}-{int(round(m * 1440))}" for c, m in zip(s["cell"], s["mjd"])])
    else:
        s["id"] = pd.Series(dtype=str)
    collections = sorted(set(photos["collection"]))
    release = "+".join(c.replace("spherex_", "").upper() for c in collections)
    store.write_parquet(results_rel(zone.name, "suspects.parquet"), s)
    store.write_json(results_rel(zone.name, "suspects.json"), to_json(s, zone, release))
    st.update({"suspects": int(len(s)), "data_release": release, "wall_s": round(time.time() - t0, 1)})
    if not source:
        L.warning("suspects %s: Gaia unreachable; suspects are NOT checked against Gaia (gaia_checked=false)", zone.name)
    if not wsource:
        L.warning("suspects %s: CatWISE unreachable; suspects are NOT checked against CatWISE (catwise_checked=false)", zone.name)
    L.info("suspects %s: %s", zone.name, st)
    return st
