"""Step 6, `suspects`: movers nobody has catalogued.

A mover becomes a suspect when:
- no known object (SkyBoT, any brightness) lies within KNOWN_ARCSEC of it in the same session;
- it has none of the GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK, HALO or BLOOM bits;
- no bright fixed source (snr >= BRIGHT_FIXED_SNR) lies within BRIGHT_FIXED_ARCSEC;
- no Gaia DR3 star lies within GAIA_ARCSEC (faint stars near our limit flicker in and out).
  Gaia is asked only around the remaining movers, in batches. If Gaia can't be reached the
  suspects are kept with gaia_checked = false and the summary says so.
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
    pm = pred["mjd"].to_numpy(float)
    pra, pdec = pred["ra"].to_numpy(float), pred["dec"].to_numpy(float)
    dra, ddec = pred["dra_arcsec_h"].to_numpy(float), pred["ddec_arcsec_h"].to_numpy(float)
    for i, (ra, dec, mjd) in enumerate(zip(movers["ra"], movers["dec"], movers["mjd"])):
        cand = tree.query_radius(ra, dec, search_arcsec)[0]
        if not cand:
            continue
        cand = np.asarray(cand)
        dt = mjd - pm[cand]
        ok = np.abs(dt) <= C.SAME_SESSION_DAYS
        if not ok.any():
            continue
        cand, dt = cand[ok], dt[ok]
        r2, d2 = offset_radec(pra[cand], pdec[cand], dra[cand] * dt * 24.0, ddec[cand] * dt * 24.0)
        out[i] = float((angsep(ra, dec, r2, d2) * 3600.0).min())
    return out


def near_bright_fixed(movers: pd.DataFrame, fixed: pd.DataFrame) -> np.ndarray:
    if len(movers) == 0 or len(fixed) == 0:
        return np.zeros(len(movers), dtype=bool)
    b = fixed[fixed["snr"] >= C.BRIGHT_FIXED_SNR]
    sep, _ = SkyTree(b["ra"].to_numpy(float), b["dec"].to_numpy(float)).nearest(movers["ra"].to_numpy(float), movers["dec"].to_numpy(float))
    return sep <= C.BRIGHT_FIXED_ARCSEC


def gaia_near(ra: np.ndarray, dec: np.ndarray, radius_arcsec: float = C.GAIA_ARCSEC) -> tuple[np.ndarray, str]:
    """True where a Gaia DR3 source lies within radius. Tries ESA's Gaia archive, then IRSA's
    copy (gaia_dr3_source). Returns (flags, source); source is "" if neither answered."""
    n = len(ra)
    hit = np.zeros(n, dtype=bool)
    if n == 0:
        return hit, "none needed"
    r_deg = radius_arcsec / 3600.0

    def adql(table: str, idx: np.ndarray) -> str:
        circles = " OR ".join(
            f"1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra[i]:.7f}, {dec[i]:.7f}, {r_deg:.7f}))" for i in idx)
        return f"SELECT ra, dec FROM {table} WHERE {circles}"

    def esa(q: str):
        from astroquery.gaia import Gaia
        return Gaia.launch_job(q).get_results()

    def irsa(q: str):
        from astroquery.ipac.irsa import Irsa
        return Irsa.query_tap(q).to_table()

    for name, table, fn in (("ESA Gaia", "gaiadr3.gaia_source", esa), ("IRSA", "gaia_dr3_source", irsa)):
        try:
            found_ra, found_dec = [], []
            for s in range(0, n, C.GAIA_BATCH):
                idx = np.arange(s, min(s + C.GAIA_BATCH, n))
                for attempt in range(1, C.NET_RETRIES + 1):
                    try:
                        t = fn(adql(table, idx))
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
            L.warning("Gaia via %s failed: %s", name, e)
    return hit, ""


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
    st.update({"cut_known": int(known.sum()), "cut_flags": int((~known & veto).sum()),
               "cut_bright_fixed": int((~known & ~veto & bright).sum())})
    return m[~known & ~veto & ~bright].reset_index(drop=True), st


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
    pred = load_predictions(store, photos)
    s, st = select(movers, fixed, pred)
    st["predictions"] = int(len(pred))
    gaia, source = gaia_near(s["ra"].to_numpy(float), s["dec"].to_numpy(float)) if len(s) else (np.zeros(0, bool), "none needed")
    st["gaia_source"] = source or "unreachable"
    st["cut_gaia"] = int(gaia.sum())
    s = s[~gaia].reset_index(drop=True)
    s["gaia_checked"] = bool(source)
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
    L.info("suspects %s: %s", zone.name, st)
    return st
