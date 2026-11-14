"""Step `refcat`: the reference catalogue of a zone (Gaia DR3, CatWISE2020, 2MASS PSC).

Over the zone box + REFCAT_MARGIN_DEG, in REFCAT_TILE_DEG tiles, one TAP query per
(catalogue, tile) (async at IRSA, sync at ESA), REFCAT_WORKERS at a time, NET_RETRIES tries each. Every finished tile is cached
as cache/refcat/<zone>/<cat>_<tile>.parquet, so a rerun only asks for the tiles still missing.

- Gaia DR3, G < REFCAT_GAIA_G_MAX: ESA's gaiadr3.gaia_source, IRSA's gaia_dr3_source if ESA fails.
  Positions at epoch 2016.0 (MJD 57388.5).
- CatWISE2020 (IRSA catwise_2020): ra_pm/dec_pm at MJD 57170 (the motion fit) where present,
  else ra/dec at meanobsmjd. pmra/pmdec come in arcsec/yr (pmra includes cos dec): stored in mas/yr.
- 2MASS PSC (IRSA fp_psc): epoch from jdate; no proper motion.

Writes
    refcat/<zone>.parquet          every row, unified (REFCAT_COLUMNS), NaN where not applicable
    refcat/<zone>_forced.parquet   the forced-photometry target list read by `dots` (FORCED_COLUMNS):
                                   Gaia G < FORCED_GAIA_G_MAX, plus CatWISE W1 < FORCED_CATWISE_W1_MAX and
                                   W1 - W2 >= FORCED_CATWISE_W1_W2_MIN with no Gaia source (any G in the refcat) within FORCED_DEDUP_ARCSEC.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .sky import SkyTree
from .store import Store

L = log.get("refcat")

ESA_TAP = "https://gea.esac.esa.int/tap-server/tap"
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP"

GAIA_EPOCH_MJD = 57388.5      # J2016.0
CATWISE_PM_EPOCH_MJD = 57170.0  # ra_pm/dec_pm epoch (2015.405)
JD_TO_MJD = 2400000.5
DAYS_PER_YEAR = 365.25

REFCAT_COLUMNS: dict[str, str] = {
    "ref_id": "string", "cat": "string",
    "ra": "float64", "dec": "float64", "epoch_mjd": "float64",
    "pmra": "float64", "pmdec": "float64", "parallax": "float64",
    "pmra_err": "float64", "pmdec_err": "float64", "parallax_err": "float64",
    "g_mag": "float32", "bp_mag": "float32", "rp_mag": "float32", "ruwe": "float32",
    "galaxy_or_qso": "bool",
    "w1_mag": "float32", "w1_err": "float32", "w2_mag": "float32", "w2_err": "float32",
    "j_mag": "float32", "j_err": "float32", "h_mag": "float32", "h_err": "float32",
    "k_mag": "float32", "k_err": "float32", "ph_qual": "string",
}

FORCED_COLUMNS: dict[str, str] = {
    "ref_id": "string", "ra": "float64", "dec": "float64", "epoch_mjd": "float64",
    "pmra": "float64", "pmdec": "float64", "parallax": "float64",
}

GAIA_SELECT = ("source_id, ra, dec, pmra, pmra_error, pmdec, pmdec_error, parallax, parallax_error, "
               "phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag, ruwe, in_qso_candidates, in_galaxy_candidates")
CATWISE_SELECT = ("source_name, ra, dec, ra_pm, dec_pm, meanobsmjd, w1mpro, w1sigmpro, w2mpro, w2sigmpro, "
                  "pmra, pmdec, sigpmra, sigpmdec")
TMASS_SELECT = "designation, ra, dec, j_m, j_msigcom, h_m, h_msigcom, k_m, k_msigcom, ph_qual, jdate"


# --- tiles -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Tile:
    ra0: float
    ra1: float
    dec0: float
    dec1: float

    @property
    def key(self) -> str:
        return f"{self.ra0:08.4f}_{self.dec0:+08.4f}"

    def where(self) -> str:
        return f"ra >= {self.ra0:.6f} AND ra < {self.ra1:.6f} AND dec >= {self.dec0:.6f} AND dec < {self.dec1:.6f}"


def query_box(zone: C.Zone, margin_deg: float = C.REFCAT_MARGIN_DEG) -> tuple[float, float, float, float]:
    """The zone box grown by margin_deg on the sky (the RA margin widened by 1/cos dec at the far edge)."""
    dec0, dec1 = zone.dec_min - margin_deg, zone.dec_max + margin_deg
    cosd = np.cos(np.deg2rad(min(max(abs(dec0), abs(dec1)), 89.0)))
    return zone.ra_min - margin_deg / cosd, zone.ra_max + margin_deg / cosd, dec0, dec1


def tiles(ra0: float, ra1: float, dec0: float, dec1: float, step: float = C.REFCAT_TILE_DEG) -> list[Tile]:
    """Half-open step × step tiles covering [ra0, ra1) × [dec0, dec1); the last row/column is cut to the box."""
    def edges(a, b):
        n = max(1, int(np.ceil((b - a) / step - 1e-9)))
        e = [round(a + i * step, 6) for i in range(n)] + [b]
        return list(zip(e[:-1], e[1:]))

    return [Tile(r0, r1, d0, d1) for d0, d1 in edges(dec0, dec1) for r0, r1 in edges(ra0, ra1)]


# --- parsing (astropy Table or DataFrame from TAP -> unified DataFrame) ------------------------
def _col(df: pd.DataFrame, name: str, scale: float = 1.0) -> np.ndarray:
    if name not in df:
        return np.full(len(df), np.nan)
    return pd.to_numeric(df[name], errors="coerce").to_numpy(np.float64) * scale


def _df(t) -> pd.DataFrame:
    if isinstance(t, pd.DataFrame):
        df = t.copy()
    else:
        df = t.to_pandas() if hasattr(t, "to_pandas") else pd.DataFrame(t)
    df.columns = [c.lower() for c in df.columns]
    return df.reset_index(drop=True)


def _bool(df: pd.DataFrame, name: str) -> np.ndarray:
    if name not in df:
        return np.zeros(len(df), bool)
    v = df[name]
    if v.dtype == object:
        v = v.map(lambda x: str(x).strip().lower() in ("true", "t", "1"))
    return v.fillna(False).astype(bool).to_numpy()


def _finish(out: dict, n: int) -> pd.DataFrame:
    df = pd.DataFrame({c: out.get(c, _default(c, n)) for c in REFCAT_COLUMNS})
    return df.astype(REFCAT_COLUMNS)


def _default(col: str, n: int):
    kind = REFCAT_COLUMNS[col]
    if kind == "bool":
        return np.zeros(n, bool)
    if kind == "string":
        return [""] * n
    return np.full(n, np.nan)


def parse_gaia(t) -> pd.DataFrame:
    df = _df(t)
    n = len(df)
    sid = df["source_id"].astype("int64").astype(str)
    return _finish({
        "ref_id": ("gaia:" + sid).tolist(), "cat": ["gaia"] * n,
        "ra": _col(df, "ra"), "dec": _col(df, "dec"), "epoch_mjd": np.full(n, GAIA_EPOCH_MJD),
        "pmra": _col(df, "pmra"), "pmdec": _col(df, "pmdec"), "parallax": _col(df, "parallax"),
        "pmra_err": _col(df, "pmra_error"), "pmdec_err": _col(df, "pmdec_error"), "parallax_err": _col(df, "parallax_error"),
        "g_mag": _col(df, "phot_g_mean_mag"), "bp_mag": _col(df, "phot_bp_mean_mag"), "rp_mag": _col(df, "phot_rp_mean_mag"),
        "ruwe": _col(df, "ruwe"),
        "galaxy_or_qso": _bool(df, "in_qso_candidates") | _bool(df, "in_galaxy_candidates"),
    }, n)


def parse_catwise(t) -> pd.DataFrame:
    df = _df(t)
    n = len(df)
    ra_pm, dec_pm = _col(df, "ra_pm"), _col(df, "dec_pm")
    has_pm = np.isfinite(ra_pm) & np.isfinite(dec_pm)
    mas = 1000.0  # CatWISE motions are in arcsec/yr
    return _finish({
        "ref_id": ("catwise:" + df["source_name"].astype(str).str.strip()).tolist(), "cat": ["catwise"] * n,
        "ra": np.where(has_pm, ra_pm, _col(df, "ra")), "dec": np.where(has_pm, dec_pm, _col(df, "dec")),
        "epoch_mjd": np.where(has_pm, CATWISE_PM_EPOCH_MJD, _col(df, "meanobsmjd")),
        "pmra": _col(df, "pmra", mas), "pmdec": _col(df, "pmdec", mas),
        "pmra_err": _col(df, "sigpmra", mas), "pmdec_err": _col(df, "sigpmdec", mas),
        "w1_mag": _col(df, "w1mpro"), "w1_err": _col(df, "w1sigmpro"),
        "w2_mag": _col(df, "w2mpro"), "w2_err": _col(df, "w2sigmpro"),
    }, n)


def parse_2mass(t) -> pd.DataFrame:
    df = _df(t)
    n = len(df)
    return _finish({
        "ref_id": ("2mass:" + df["designation"].astype(str).str.strip()).tolist(), "cat": ["2mass"] * n,
        "ra": _col(df, "ra"), "dec": _col(df, "dec"), "epoch_mjd": _col(df, "jdate") - JD_TO_MJD,
        "j_mag": _col(df, "j_m"), "j_err": _col(df, "j_msigcom"),
        "h_mag": _col(df, "h_m"), "h_err": _col(df, "h_msigcom"),
        "k_mag": _col(df, "k_m"), "k_err": _col(df, "k_msigcom"),
        "ph_qual": df["ph_qual"].astype(str).str.strip().tolist() if "ph_qual" in df else [""] * n,
    }, n)


# --- queries -------------------------------------------------------------------------------------
# catalogue -> [(service name, TAP url, ADQL template with {where}), ...] tried in order
SOURCES: dict[str, list[tuple[str, str, str]]] = {
    "gaia": [
        ("ESA Gaia", ESA_TAP, f"SELECT {GAIA_SELECT} FROM gaiadr3.gaia_source WHERE {{where}} AND phot_g_mean_mag < {{gmax}}"),
        ("IRSA Gaia", IRSA_TAP, f"SELECT {GAIA_SELECT} FROM gaia_dr3_source WHERE {{where}} AND phot_g_mean_mag < {{gmax}}"),
    ],
    "catwise": [("IRSA CatWISE2020", IRSA_TAP, f"SELECT {CATWISE_SELECT} FROM catwise_2020 WHERE {{where}}")],
    "2mass": [("IRSA 2MASS PSC", IRSA_TAP, f"SELECT {TMASS_SELECT} FROM fp_psc WHERE {{where}}")],
}
PARSERS = {"gaia": parse_gaia, "catwise": parse_catwise, "2mass": parse_2mass}


def _tap(url: str, adql: str):
    """Async TAP at IRSA. ESA's async jobs sat queued for minutes (14 Nov) while its sync endpoint
    answered a 6,000-row 1° tile in 2 s, so ESA is asked synchronously (tiles stay far below its limit)."""
    import pyvo

    svc = pyvo.dal.TAPService(url)
    run = svc.run_sync if url == ESA_TAP else svc.run_async
    return run(adql, maxrec=C.REFCAT_MAXREC).to_table()


def fetch_tile(cat: str, tile: Tile, tap=_tap) -> tuple[pd.DataFrame, str]:
    """One (catalogue, tile): each service in turn, NET_RETRIES tries each. Raises if all fail."""
    last = None
    for name, url, tmpl in SOURCES[cat]:
        adql = tmpl.format(where=tile.where(), gmax=C.REFCAT_GAIA_G_MAX)
        for attempt in range(1, C.NET_RETRIES + 1):
            try:
                t = tap(url, adql)
                if len(t) >= C.REFCAT_MAXREC:
                    raise RuntimeError(f"{len(t)} rows hit maxrec: use smaller tiles")
                return PARSERS[cat](t), name
            except Exception as e:  # noqa: BLE001
                last = e
                L.warning("%s %s try %d: %s", name, tile.key, attempt, str(e)[:200])
                if attempt < C.NET_RETRIES:
                    time.sleep(5 * attempt)
    raise RuntimeError(f"{cat} tile {tile.key}: every service failed: {last}")


def tile_rel(zone: str, cat: str, tile: Tile) -> str:
    return f"cache/refcat/{zone}/{cat}_{tile.key}.parquet"


def refcat_rel(zone: str, forced: bool = False) -> str:
    return f"refcat/{zone}{'_forced' if forced else ''}.parquet"


def fetch_all(store: Store, zone: C.Zone, workers: int = C.REFCAT_WORKERS, tap=_tap) -> tuple[dict[str, pd.DataFrame], dict]:
    box = query_box(zone)
    ts = tiles(*box)
    jobs = [(cat, t) for cat in SOURCES for t in ts]
    todo = [(c, t) for c, t in jobs if not store.exists(tile_rel(zone.name, c, t))]
    L.info("refcat %s: box ra %.3f-%.3f dec %.3f-%.3f, %d tiles x %d catalogues, %d to fetch",
           zone.name, *box, len(ts), len(SOURCES), len(todo))
    seconds = {c: 0.0 for c in SOURCES}
    services: dict[str, set] = {c: set() for c in SOURCES}
    failed = []

    def job(cat, t):
        t0 = time.time()
        df, name = fetch_tile(cat, t, tap)
        store.write_parquet(tile_rel(zone.name, cat, t), df)
        return cat, t, name, len(df), time.time() - t0

    with ThreadPoolExecutor(max(1, workers)) as ex:
        futs = {ex.submit(job, c, t): (c, t) for c, t in todo}
        for i, f in enumerate(as_completed(futs), 1):
            c, t = futs[f]
            try:
                cat, _, name, n, dt = f.result()
                seconds[cat] += dt
                services[cat].add(name)
                L.info("refcat %s [%d/%d] %s %s: %d rows in %.1f s (%s)", zone.name, i, len(todo), cat, t.key, n, dt, name)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{c}_{t.key}")
                L.error("refcat %s %s %s failed: %s", zone.name, c, t.key, str(e)[:300])
    if failed:
        raise RuntimeError(f"refcat {zone.name}: {len(failed)} tiles failed ({failed[:5]}...); rerun to retry them")
    out = {c: pd.concat([store.read_parquet(tile_rel(zone.name, c, t)) for t in ts], ignore_index=True).astype(REFCAT_COLUMNS)
           for c in SOURCES}
    st = {"tiles": len(ts), "fetched": len(todo),
          "query_seconds": {c: round(s, 1) for c, s in seconds.items()},
          "services": {c: sorted(s) for c, s in services.items()}}
    return out, st


# --- forced list ---------------------------------------------------------------------------------
def move_to_epoch(df: pd.DataFrame, epoch_mjd: float) -> tuple[np.ndarray, np.ndarray]:
    """Linear proper motion only (no parallax); NaN pm = no motion."""
    dt_yr = (epoch_mjd - df["epoch_mjd"].to_numpy(np.float64)) / DAYS_PER_YEAR
    pmra = np.nan_to_num(df["pmra"].to_numpy(np.float64))
    pmdec = np.nan_to_num(df["pmdec"].to_numpy(np.float64))
    dec = df["dec"].to_numpy(np.float64) + pmdec * dt_yr / 3.6e6
    cosd = np.cos(np.deg2rad(df["dec"].to_numpy(np.float64)))
    ra = df["ra"].to_numpy(np.float64) + pmra * dt_yr / 3.6e6 / np.maximum(cosd, 1e-6)
    return ra, dec


def catwise_without_gaia(catwise: pd.DataFrame, gaia: pd.DataFrame, arcsec: float = C.FORCED_DEDUP_ARCSEC) -> np.ndarray:
    """True where a CatWISE row has no Gaia source within `arcsec` (Gaia moved to CatWISE's epoch)."""
    if len(catwise) == 0:
        return np.zeros(0, bool)
    if len(gaia) == 0:
        return np.ones(len(catwise), bool)
    gra, gdec = move_to_epoch(gaia, CATWISE_PM_EPOCH_MJD)
    sep, _ = SkyTree(gra, gdec).nearest(catwise["ra"].to_numpy(float), catwise["dec"].to_numpy(float))
    return ~(sep <= arcsec)


def forced_list(gaia: pd.DataFrame, catwise: pd.DataFrame,
                g_max: float = C.FORCED_GAIA_G_MAX, w1_max: float = C.FORCED_CATWISE_W1_MAX,
                dedup_arcsec: float = C.FORCED_DEDUP_ARCSEC, w1_w2_min: float = C.FORCED_CATWISE_W1_W2_MIN) -> pd.DataFrame:
    g = gaia[gaia["g_mag"] < g_max]
    w = catwise[(catwise["w1_mag"] < w1_max) & (catwise["w1_mag"] - catwise["w2_mag"] >= w1_w2_min)]
    w = w[catwise_without_gaia(w, gaia, dedup_arcsec)]
    out = pd.concat([g, w], ignore_index=True)[list(FORCED_COLUMNS)]
    return out.astype(FORCED_COLUMNS).reset_index(drop=True)


def per_photo_counts(ra: np.ndarray, dec: np.ndarray, zone: C.Zone, side: float = C.PHOTO_SIDE_DEG,
                     step: float = 0.25) -> dict:
    """Targets in a side × side photo-sized box, for every box centre on a `step` grid whose box lies
    inside the zone (only the zone centre if none fits). Returns mean (from the zone density) and max."""
    inz = zone.contains(ra, dec)
    cos_c = np.cos(np.deg2rad(zone.centre[1]))
    area = (zone.ra_max - zone.ra_min) * cos_c * (zone.dec_max - zone.dec_min)
    half = side / 2.0
    dec_c = np.arange(zone.dec_min + half, zone.dec_max - half + 1e-9, step)
    if dec_c.size == 0:
        dec_c = np.array([zone.centre[1]])
    counts = []
    for dc in dec_c:
        hr = half / np.cos(np.deg2rad(dc))
        ra_c = np.arange(zone.ra_min + hr, zone.ra_max - hr + 1e-9, step)
        if ra_c.size == 0:
            ra_c = np.array([zone.centre[0]])
        ind = np.abs(dec - dc) < half
        for rc in ra_c:
            counts.append(int((ind & (np.abs(ra - rc) < hr)).sum()))
    return {"per_deg2": round(float(inz.sum() / area), 1), "mean_per_photo": int(round(inz.sum() / area * side * side)),
            "max_per_photo": int(max(counts)), "boxes": len(counts)}


# --- step ----------------------------------------------------------------------------------------
def run(store: Store, zone: C.Zone, workers: int = C.REFCAT_WORKERS, tap=_tap) -> dict:
    t0 = time.time()
    cats, st = fetch_all(store, zone, workers, tap)
    allrows = pd.concat([cats["gaia"], cats["catwise"], cats["2mass"]], ignore_index=True).astype(REFCAT_COLUMNS)
    allrows = allrows.drop_duplicates("ref_id").reset_index(drop=True)
    size = store.write_parquet(refcat_rel(zone.name), allrows)
    forced = forced_list(cats["gaia"], cats["catwise"])
    fsize = store.write_parquet(refcat_rel(zone.name, forced=True), forced)
    n_cw = int(forced["ref_id"].str.startswith("catwise:").sum())
    photo = per_photo_counts(forced["ra"].to_numpy(float), forced["dec"].to_numpy(float), zone)
    st.update({
        "rows": {c: int((allrows["cat"] == c).sum()) for c in SOURCES},
        "refcat_bytes": size, "forced_rows": int(len(forced)), "forced_gaia": int(len(forced) - n_cw),
        "forced_catwise_only": n_cw, "forced_bytes": fsize, "forced_per_photo": photo,
        "limits": {"gaia_g_max": C.FORCED_GAIA_G_MAX, "catwise_w1_max": C.FORCED_CATWISE_W1_MAX,
                   "catwise_w1_w2_min": C.FORCED_CATWISE_W1_W2_MIN, "dedup_arcsec": C.FORCED_DEDUP_ARCSEC},
        "wall_s": round(time.time() - t0, 1),
    })
    if photo["max_per_photo"] > C.FORCED_MAX_PER_PHOTO:
        L.warning("refcat %s: %d forced targets in the densest photo-sized box (> %d): tighten the limits in config.py",
                  zone.name, photo["max_per_photo"], C.FORCED_MAX_PER_PHOTO)
    L.info("refcat %s: %s", zone.name, st)
    return st
