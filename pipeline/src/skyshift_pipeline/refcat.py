"""Step `refcat`: the reference catalogue of a zone (Gaia DR3, CatWISE2020, 2MASS PSC).

Over the zone box + REFCAT_MARGIN_DEG, in REFCAT_TILE_DEG tiles, one TAP query per
(catalogue, tile) (async at IRSA, sync at ESA), REFCAT_WORKERS at a time, NET_RETRIES tries each. Every finished tile is cached
as cache/refcat/<zone>/<cat>_<tile>.parquet, so a rerun only asks for the tiles still missing.

- Gaia DR3, G < REFCAT_GAIA_G_MAX: ESA's gaiadr3.gaia_source, IRSA's gaia_dr3_source if ESA fails.
  Positions at epoch 2016.0 (MJD 57388.5).
- CatWISE2020 (IRSA catwise_2020): ra_pm/dec_pm at MJD 57170 (the motion fit) where present,
  else ra/dec at meanobsmjd. pmra/pmdec come in arcsec/yr (pmra includes cos dec): stored in mas/yr.
- 2MASS PSC (IRSA fp_psc): epoch from jdate; no proper motion.

Gaia-only refcats (16 Nov finding): the sky run exported SKYSHIFT_REFCAT_CATALOGS=gaia for its whole life (infra/worker/
skyrun.sh), so the combine's `all` also ran refcat partially, found both files there and skipped: 64 combined zones
(59 ecliptic, 5 featured targets) were combined against Gaia alone (crossids catwise_n = tmass_n = 0). The env var now only limits a standalone `refcat`
run (extraction prep); `all` always asks for every catalogue (FULL; with `--from` a later step too, cli ensure_complete),
so a zone prepared Gaia-only gets CatWISE + 2MASS added from the cached Gaia tiles before link and crossid read it.
When IRSA's TAP fails for CatWISE2020 (or 2MASS) after its retries, the tile comes from AllWISE on AWS S3 (IRSA's
HEALPix Parquet, anonymous): its W1/W2 rows as the WISE catalogue (ref_id `allwise:…`, cat `catwise`, no motion) and
its 2MASS associations (J/H/Ks and position via r_2mass/pa_2mass) as 2MASS (ref_id `2mass:aw<tmass_key>`). The marker
records which service filled each catalogue (`services`). REFCAT_S3_FIRST (env SKYSHIFT_REFCAT_S3_FIRST=1) asks S3 first,
for a many-zone rerun: IRSA's CatWISE took 60–444 s per 1° tile, an AllWISE partition (~3.4 deg²) ~33 s.

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
    "z_ab": "float32", "y_ab": "float32",
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
# Gaia DR3 synthetic photometry (XP spectra → SDSS z, PS1 y; G < 17.65 only): an anchor for D1's flux scale (calib)
GSPC_SELECT = "p.z_sdss_mag, p.z_sdss_flag, p.y_ps1_mag, p.y_ps1_flag"
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
        # GSPC, AB (SDSS z_AB = z + 0.02); NaN without it (IRSA fallback, G > 17.65) or when its flag isn't 1
        "z_ab": np.where(_col(df, "z_sdss_flag") == 1, _col(df, "z_sdss_mag") + 0.02, np.nan),
        "y_ab": np.where(_col(df, "y_ps1_flag") == 1, _col(df, "y_ps1_mag"), np.nan),
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
        ("ESA Gaia", ESA_TAP, f"SELECT {', '.join('g.' + c.strip() for c in GAIA_SELECT.split(','))}, {GSPC_SELECT} "
                              "FROM gaiadr3.gaia_source AS g LEFT OUTER JOIN gaiadr3.synthetic_photometry_gspc AS p "
                              "ON p.source_id = g.source_id WHERE {where} AND g.phot_g_mean_mag < {gmax}"),
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


# --- when IRSA's TAP fails (or first, REFCAT_S3_FIRST): AllWISE and its 2MASS associations from AWS S3 ---
S3_NAMES = {"catwise": "AllWISE on S3", "2mass": "2MASS via AllWISE on S3"}
ALLWISE_TILE_COLUMNS = ("designation", "ra", "dec", "w1mpro", "w1sigmpro", "w2mpro", "w2sigmpro",
                        "tmass_key", "r_2mass", "pa_2mass", "j_m_2mass", "j_msig_2mass", "h_m_2mass", "h_msig_2mass",
                        "k_m_2mass", "k_msig_2mass")
TMASS_EPOCH_MJD = 51300.0  # 2MASS survey middle (1997–2001): the epoch of positions rebuilt from AllWISE's association
_PARTS: dict[int, pd.DataFrame] = {}  # partitions read in this process (neighbouring tiles share them)
_PARTS_MAX = 48  # ~7 MB each at high latitude: the oldest go first


def tile_pixels(tile: Tile, nside: int = C.ALLWISE_NSIDE, step: float = 0.02) -> np.ndarray:
    """Nested HEALPix pixels (AllWISE's order-5 partitions, ~1.8°) under the tile: sampled on a `step` grid."""
    import astropy.units as u
    from astropy_healpix import HEALPix

    ra = np.linspace(tile.ra0, tile.ra1, max(2, int(np.ceil((tile.ra1 - tile.ra0) / step)) + 1))
    dec = np.linspace(tile.dec0, tile.dec1, max(2, int(np.ceil((tile.dec1 - tile.dec0) / step)) + 1))
    lon, lat = np.meshgrid(np.mod(ra, 360.0), np.clip(dec, -90.0, 90.0))
    h = HEALPix(nside=nside, order="nested")
    return np.unique(np.asarray(h.lonlat_to_healpix(lon.ravel() * u.deg, lat.ravel() * u.deg), np.int64))


def _allwise_s3_partition(pix: int) -> pd.DataFrame:
    """One AllWISE partition (ALLWISE_TILE_COLUMNS) from IRSA's HEALPix Parquet on S3 (anonymous)."""
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq

    from .suspects import allwise_key
    if pix not in _PARTS:
        fs = pafs.S3FileSystem(anonymous=True, region=C.ALLWISE_S3_REGION, connect_timeout=C.ALLWISE_TIMEOUT_S,
                               request_timeout=C.ALLWISE_TIMEOUT_S)
        t = pq.read_table(f"{C.ALLWISE_S3_BUCKET}/{allwise_key(pix)}", filesystem=fs, columns=list(ALLWISE_TILE_COLUMNS))
        while len(_PARTS) >= _PARTS_MAX:
            _PARTS.pop(next(iter(_PARTS)), None)
        _PARTS[pix] = t.to_pandas()
    return _PARTS[pix]


def allwise_tile(tile: Tile, reader=_allwise_s3_partition) -> pd.DataFrame:
    """AllWISE rows inside the tile (half-open box, as the TAP `where`)."""
    parts = [reader(int(p)) for p in tile_pixels(tile)]
    df = _df(pd.concat(parts, ignore_index=True)) if parts else pd.DataFrame(columns=list(ALLWISE_TILE_COLUMNS))
    ra, dec = _col(df, "ra"), _col(df, "dec")
    return df[(ra >= tile.ra0) & (ra < tile.ra1) & (dec >= tile.dec0) & (dec < tile.dec1)].reset_index(drop=True)


def parse_allwise(t) -> pd.DataFrame:
    """AllWISE as the WISE catalogue (role `catwise`): ref_id allwise:<designation>, positions at ~2010.5, no motion
    (AllWISE's own motions are not used anywhere)."""
    df = _df(t)
    n = len(df)
    return _finish({
        "ref_id": ("allwise:" + df["designation"].astype(str).str.strip()).tolist(), "cat": ["catwise"] * n,
        "ra": _col(df, "ra"), "dec": _col(df, "dec"), "epoch_mjd": np.full(n, C.ALLWISE_EPOCH_MJD),
        "w1_mag": _col(df, "w1mpro"), "w1_err": _col(df, "w1sigmpro"),
        "w2_mag": _col(df, "w2mpro"), "w2_err": _col(df, "w2sigmpro"),
    }, n)


def parse_allwise_2mass(t) -> pd.DataFrame:
    """The 2MASS PSC sources AllWISE associated (within 3″): J/H/Ks as given, position = the WISE position moved
    r_2mass along pa_2mass (from the WISE source to the 2MASS one), epoch TMASS_EPOCH_MJD; ref_id 2mass:aw<tmass_key>."""
    df = _df(t)
    df = df[np.isfinite(_col(df, "tmass_key")) & np.isfinite(_col(df, "r_2mass"))].reset_index(drop=True)
    n = len(df)
    from skyshift_core import tangent_deproject
    r, pa = _col(df, "r_2mass"), np.deg2rad(_col(df, "pa_2mass"))
    ra, dec = tangent_deproject(_col(df, "ra"), _col(df, "dec"), r * np.sin(pa), r * np.cos(pa))
    return _finish({
        "ref_id": ["2mass:aw" + str(int(k)) for k in _col(df, "tmass_key")], "cat": ["2mass"] * n,
        "ra": np.asarray(ra, np.float64), "dec": np.asarray(dec, np.float64), "epoch_mjd": np.full(n, TMASS_EPOCH_MJD),
        "j_mag": _col(df, "j_m_2mass"), "j_err": _col(df, "j_msig_2mass"),
        "h_mag": _col(df, "h_m_2mass"), "h_err": _col(df, "h_msig_2mass"),
        "k_mag": _col(df, "k_m_2mass"), "k_err": _col(df, "k_msig_2mass"),
    }, n)


S3_PARSERS = {"catwise": parse_allwise, "2mass": parse_allwise_2mass}


def _from_s3(cat: str, tile: Tile, s3) -> tuple[pd.DataFrame, str]:
    """CatWISE's or 2MASS's role for one tile from AllWISE on S3, NET_RETRIES tries."""
    last = None
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            return S3_PARSERS[cat](allwise_tile(tile, s3)), S3_NAMES[cat]
        except Exception as e:  # noqa: BLE001
            last = e
            L.warning("%s %s try %d: %s", S3_NAMES[cat], tile.key, attempt, str(e)[:200])
            if attempt < C.NET_RETRIES:
                time.sleep(3 * attempt)
    raise RuntimeError(f"{S3_NAMES[cat]} {tile.key}: {last}")


def fetch_tile(cat: str, tile: Tile, tap=None, s3=None) -> tuple[pd.DataFrame, str]:
    """One (catalogue, tile): each service in turn, NET_RETRIES tries each; for CatWISE and 2MASS then AllWISE on
    S3 (config REFCAT_S3_FALLBACK), or AllWISE on S3 first (REFCAT_S3_FIRST). Raises if all fail.
    tap/s3 None: the real services (s3: the partition reader)."""
    tap = tap or _tap
    s3 = s3 or _allwise_s3_partition
    last = None
    s3_ok = cat in S3_PARSERS and (C.REFCAT_S3_FALLBACK or C.REFCAT_S3_FIRST)
    if s3_ok and C.REFCAT_S3_FIRST:
        try:
            return _from_s3(cat, tile, s3)
        except Exception as e:  # noqa: BLE001
            last = e
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
    if s3_ok and not C.REFCAT_S3_FIRST:
        try:
            df, name = _from_s3(cat, tile, s3)
            L.warning("refcat %s %s: IRSA TAP failed, from %s", cat, tile.key, name)
            return df, name
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{cat} tile {tile.key}: every service failed: {last}")


def tile_rel(zone: str, cat: str, tile: Tile) -> str:
    return f"cache/refcat/{zone}/{cat}_{tile.key}.parquet"


def refcat_rel(zone: str, forced: bool = False) -> str:
    return f"refcat/{zone}{'_forced' if forced else ''}.parquet"


def catalogs_rel(zone: str) -> str:
    return f"refcat/{zone}_catalogs.json"


def gspc_in(store: Store, zone: str) -> bool:
    """True if refcat/<zone>.parquet's Gaia rows carry the GSPC columns (z_ab, y_ab; marker key `gspc`, 15 Nov)."""
    rel = catalogs_rel(zone)
    return bool(store.read_json(rel).get("gspc")) if store.exists(rel) else False


def tile_has_gspc(store: Store, rel: str) -> bool:
    """A cached Gaia tile written with the GSPC join (columns z_ab, y_ab; values may be NaN, e.g. from IRSA)."""
    import io

    import pyarrow.parquet as pq
    return {"z_ab", "y_ab"} <= set(pq.read_schema(io.BytesIO(store.read_bytes(rel))).names)


def _read_tile(store: Store, rel: str) -> pd.DataFrame:
    """A cached tile in today's REFCAT_COLUMNS (columns a tile cached by older code lacks: NaN / defaults)."""
    df = store.read_parquet(rel)
    for c in REFCAT_COLUMNS:
        if c not in df:
            df[c] = _default(c, len(df))
    return df[list(REFCAT_COLUMNS)]


def catalogs_in(store: Store, zone: str) -> list[str] | None:
    """The catalogues refcat/<zone>.parquet was built from (None: no marker, i.e. built before 14 Nov 15:00 with all)."""
    rel = catalogs_rel(zone)
    return list(store.read_json(rel).get("catalogs", [])) if store.exists(rel) else None


def fetch_all(store: Store, zone: C.Zone, workers: int = C.REFCAT_WORKERS, tap=None,
              catalogs: tuple[str, ...] | None = None, s3=None) -> tuple[dict[str, pd.DataFrame], dict]:
    box = query_box(zone)
    ts = tiles(*box)
    want = C.REFCAT_CATALOGS if catalogs is None else catalogs
    cats = [c for c in SOURCES if c in want]
    jobs = [(cat, t) for cat in cats for t in ts]
    todo = [(c, t) for c, t in jobs if not store.exists(tile_rel(zone.name, c, t))]
    # Gaia tiles cached before the GSPC join (15 Nov; z_ab, y_ab): fetched again, best effort (a failure keeps the old tile)
    upgrade = [(c, t) for c, t in jobs if c == "gaia" and C.REFCAT_GSPC_UPGRADE and (c, t) not in todo
               and not tile_has_gspc(store, tile_rel(zone.name, c, t))]
    L.info("refcat %s: box ra %.3f-%.3f dec %.3f-%.3f, %d tiles x %d catalogues (%s), %d to fetch, %d Gaia tiles to upgrade (GSPC)",
           zone.name, *box, len(ts), len(cats), ",".join(cats), len(todo), len(upgrade))
    seconds = {c: 0.0 for c in SOURCES}
    services: dict[str, set] = {c: set() for c in SOURCES}
    failed, kept_old = [], []

    def job(cat, t):
        t0 = time.time()
        df, name = fetch_tile(cat, t, tap, s3)
        store.write_parquet(tile_rel(zone.name, cat, t), df)
        return cat, t, name, len(df), time.time() - t0

    with ThreadPoolExecutor(max(1, workers)) as ex:
        futs = {ex.submit(job, c, t): (c, t) for c, t in todo + upgrade}
        for i, f in enumerate(as_completed(futs), 1):
            c, t = futs[f]
            try:
                cat, _, name, n, dt = f.result()
                seconds[cat] += dt
                services[cat].add(name)
                L.info("refcat %s [%d/%d] %s %s: %d rows in %.1f s (%s)", zone.name, i, len(futs), cat, t.key, n, dt, name)
            except Exception as e:  # noqa: BLE001
                if (c, t) in upgrade:  # the old tile is still there and complete apart from z/y
                    kept_old.append(f"{c}_{t.key}")
                    L.warning("refcat %s %s %s: GSPC upgrade failed, old tile kept (D1 anchors fewer): %s", zone.name, c, t.key, str(e)[:200])
                    continue
                failed.append(f"{c}_{t.key}")
                L.error("refcat %s %s %s failed: %s", zone.name, c, t.key, str(e)[:300])
    if failed:
        raise RuntimeError(f"refcat {zone.name}: {len(failed)} tiles failed ({failed[:5]}...); rerun to retry them")
    out = {c: (pd.concat([_read_tile(store, tile_rel(zone.name, c, t)) for t in ts], ignore_index=True) if c in cats
               else pd.DataFrame({k: pd.Series(dtype=v) for k, v in REFCAT_COLUMNS.items()})).astype(REFCAT_COLUMNS)
           for c in SOURCES}
    st = {"tiles": len(ts), "fetched": len(todo), "gspc_upgraded": len(upgrade) - len(kept_old), "gspc_kept_old": len(kept_old),
          "gspc": "gaia" in cats and not kept_old and all(tile_has_gspc(store, tile_rel(zone.name, "gaia", t)) for t in ts),
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
FULL: tuple[str, ...] = tuple(SOURCES)  # what the combining steps (link, crossid, … suspects) need


def built_partial(store: Store, zone: str) -> bool:
    """refcat/<zone>_catalogs.json says the zone's refcat lacks a catalogue (a Gaia-only extraction prep). No marker
    (built before partial runs existed, or no refcat at all): False."""
    made = catalogs_in(store, zone)
    return made is not None and not set(FULL) <= set(made)


def run(store: Store, zone: C.Zone, workers: int = C.REFCAT_WORKERS, tap=None, force: bool = False,
        catalogs: tuple[str, ...] | None = None, s3=None) -> dict:
    """catalogs: None = config REFCAT_CATALOGS (env SKYSHIFT_REFCAT_CATALOGS; "gaia" for a fast extraction prep);
    FULL for the combine (cli `all`), whatever the environment says."""
    want = C.REFCAT_CATALOGS if catalogs is None else tuple(catalogs)
    partial = bool(set(SOURCES) - set(want))  # e.g. SKYSHIFT_REFCAT_CATALOGS=gaia
    have = store.exists(refcat_rel(zone.name)) and store.exists(refcat_rel(zone.name, forced=True))
    made = catalogs_in(store, zone.name)  # None: written before the marker existed (all catalogues)
    gspc_ok = gspc_in(store, zone.name) or not C.REFCAT_GSPC_UPGRADE
    if not force and have and (partial or made is None or set(SOURCES) <= set(made)) and (gspc_ok or partial):
        L.info("refcat %s: both files exist (catalogues %s), skipping (delete them or pass force=True to rebuild)",
               zone.name, ",".join(made) if made else "all")
        return {"skipped": True, "fetched": 0, "catalogs": made or list(FULL)}
    if made is not None and not partial and not set(SOURCES) <= set(made):
        L.info("refcat %s: built with %s only; adding %s (cached tiles reused)", zone.name, ",".join(made),
               ",".join(c for c in SOURCES if c not in made))
    t0 = time.time()
    cats, st = fetch_all(store, zone, workers, tap, want, s3)
    allrows = pd.concat([cats["gaia"], cats["catwise"], cats["2mass"]], ignore_index=True).astype(REFCAT_COLUMNS)
    allrows = allrows.drop_duplicates("ref_id").reset_index(drop=True)
    size = store.write_parquet(refcat_rel(zone.name), allrows)
    store.write_json(catalogs_rel(zone.name), {"catalogs": [c for c in SOURCES if c in want], "gspc": st["gspc"],
                                               "services": st["services"]})
    if partial:
        L.warning("refcat %s: catalogues %s only (extraction prep; the combine's `all` adds the rest, reusing the "
                  "cached tiles)", zone.name, ",".join(want))
    forced = forced_list(cats["gaia"], cats["catwise"])
    fsize = store.write_parquet(refcat_rel(zone.name, forced=True), forced)
    n_cw = int(forced["ref_id"].str.startswith("catwise:").sum())
    photo = per_photo_counts(forced["ra"].to_numpy(float), forced["dec"].to_numpy(float), zone)
    st.update({
        "rows": {c: int(len(cats[c])) for c in SOURCES}, "catalogues": list(want), "catalogs": [c for c in SOURCES if c in want],
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
