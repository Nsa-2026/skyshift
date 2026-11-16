"""The `changes` step: what changed about known stars between visits → results/<zone>/changes.parquet.

    uv run python -m skyshift_pipeline.changes_step --zones pilot,t_rdra --root gs://nasa-glazing-data \
        --cache /path/to/cache [--out-root <dir>] [--no-gaia]

One row per listed change (CHANGE_COLUMNS), from stored tables only (never photos), with the measurements, one plain
sentence (`text`), what to look at (`look`) and v3.23-style `checks` (JSON). The library and the measured false-alarm
rates are in changes.py and docs/watch-changes.md; the site's file is changes_export.py's.

Kinds:
- `brightness`: a star's brightness in one detector, the latest visit against the earlier one (two visits) or all
  earlier ones together, beyond its own scatter, confirmed by a second detector and surviving the drop of each visit's
  most deviant session (changes.star_brightness, survives_session_drop).
- `colour`: a colour change (changes.star_colour) of a star with no brightness listing; on a listed brightness change
  it is the check `colour_changed` instead.
- `appeared` / `vanished`: an uncatalogued static source seen in one visit only, bright enough and covered by the
  other visit (changes.new_sources, >= CHANGES_NEW_MIN_SESSIONS sessions).

Who is listed (brightness, colour): stars with their own SPHEREx source, and catalogue stars measured only by forced
photometry OUTSIDE the zone box; no Gaia star >= 2.5 mag brighter within 20″. Every star the pipeline measured is
watched; following one on the site only decides who gets told. Stars Gaia DR3 already lists as variable (its
phot_variable_flag or a variability class) are listed with `gaia_variable` and the check `known_variable`; the site
ranks them below the unexpected changes.

Gaia DR3 (positions of forced-only stars beyond the zone's catalogue, G, variability flag and class, neighbours
beyond the catalogue) comes from ESA's archive by primary key only (GaiaLookup, cached in --cache); a failure leaves
`gaia_variable` unknown and adds the check `neighbours_unchecked` where it mattered. Nothing else goes online.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import changes as CH
from . import config as C
from . import log
from .store import Store, photolist_rel, results_rel

L = log.get("changes")

# --- what is read ------------------------------------------------------------------------------------------------
# column lists equal to the watch prototype's (docs/watch-changes.md), so the shared --cache serves both
POINT_COLUMNS = ["source_id", "ref_id", "origin", "mjd", "visit", "detector", "rel_flux", "flux_err_cal_ujy",
                 "model_ujy", "photo_ok", "detected"]
XID_COLUMNS = ("source_id,ra,dec,epoch_mjd,gaia_ref_id,gaia_id,gaia_sep_arcsec,gaia_n,gaia_ra,gaia_dec,gaia_epoch_mjd,"
               "gaia_g,gaia_bp_rp,gaia_pmra,gaia_pmdec,gaia_parallax,gaia_parallax_error,catwise_ref_id,"
               "catwise_sep_arcsec,w1,w2,catwise_pmra,catwise_pmdec,tmass_ref_id,tmass_sep_arcsec,j,h,k").split(",")
SRC_COLUMNS = ("source_id,ra,dec,pos_err_arcsec,n_dots,n_sessions,n_visits,detectors,saturated,visit_bits,chi2_nu,"
               "visit_flux_ratio,variable").split(",")
REFCAT_COLUMNS = ["ref_id", "cat", "ra", "dec", "g_mag"]
FRAG_COLUMNS = ["source_id", "tag", "n_gaia_8"]
ORPHAN_COLUMNS = ["source_id", "ra", "dec", "n_dots", "n_sessions", "mjd_mean", "visit", "other_visit", "cover_other",
                  "mjd_other", "snr_max", "reason", "rate", "rate_err", "known_name"]

CHANGES_REFCAT_MARGIN_DEG = 0.1          # refcat/<zone>.parquet covers the zone box + this (refcat.py)
CHANGES_NEW_MIN_SESSIONS = 3             # appeared / vanished: seen in this many sessions of its visit
CHANGES_GAIA_NEAR_ARCSEC = 6.0           # ... a Gaia star this close: probably its dots split off (check gaia_near)
CHANGES_GHOST_ARCSEC = 360.0             # ... a star this bright this close can throw a ghost (check ghost_possible)
CHANGES_GHOST_G = 11.0
CHANGES_GAIA_NEIGHBOUR_G = 18.0          # GaiaLookup.around: neighbours this bright (a 2.5 mag brighter one of a G 20.5)
CHANGES_CHUNK_POINTS = 1_000_000         # star chunks of about this many points (t_rwaur: 1.5 GB peak at 1 M, 2.5 GB at 3 M)
CHANGES_OTHERS_Z = 2.0                   # other detectors named in the text from this many sigma

# changes.parquet: one row per listed change
CHANGE_COLUMNS: dict[str, str] = {
    "id": "string", "zone": "string", "kind": "string", "star": "int64", "source_id": "int64", "ref_id": "string",
    "gaia_id": "int64", "name": "string", "ra": "float64", "dec": "float64", "g_mag": "float32",
    "population": "string", "origin": "string", "detector": "int8", "visits_before": "object", "visit_after": "int16",
    "mjd_before": "float64", "mjd_after": "float64", "rel_before": "float32", "rel_after": "float32",
    "change_frac": "float32", "sigma": "float32", "agree": "int8", "disagree": "int8", "others": "string",
    "colour_change": "float32", "colour_sigma": "float32", "colour_detectors": "int8", "gaia_variable": "int8",
    "gaia_class": "string", "survives_session_drop": "bool", "nb_sep_arcsec": "float32", "nb_dmag": "float32",
    "neighbours_checked": "bool", "snr": "float32", "n_dots": "int32", "n_sessions": "int32", "n_detectors": "int8",
    "detectors": "object", "text": "string", "look": "string", "values": "string", "checks": "string",
}


def empty_changes() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object" if t in ("object", "string") else t) for c, t in CHANGE_COLUMNS.items()})


# --- reading ------------------------------------------------------------------------------------------------------
class Reader:
    """Some columns of a file in the store (range reads), kept in `cache` between runs under the exporter's key
    (candidates.read_columns: sha1 of root|rel|columns). None when the file doesn't exist."""

    def __init__(self, store: Store, cache: Path | None = None, keep_points: bool = True):
        self.store, self.cache = store, cache
        self.keep_points = keep_points      # False: local() copies go to a temporary folder that cleanup() removes
        self.bytes_read = 0

    def key(self, rel: str, columns: list[str]) -> Path | None:
        if self.cache is None:
            return None
        h = hashlib.sha1(f"{self.store.root}|{rel}|{','.join(columns)}".encode()).hexdigest()[:16]
        return self.cache / f"{rel.replace('/', '__')}.{h}.parquet"

    def cached_superset(self, rel: str, columns: list[str]) -> Path | None:
        """A cached read of this file (any key) that holds every one of these columns."""
        import pyarrow.parquet as pq

        if self.cache is None:
            return None
        for p in sorted(self.cache.glob(f"{rel.replace('/', '__')}.*.parquet")):
            try:
                if set(columns) <= set(pq.read_schema(p).names):
                    return p
            except Exception:  # noqa: BLE001  a half-written file of another process
                continue
        return None

    def __call__(self, rel: str, columns: list[str], categorical: bool = False) -> pd.DataFrame | None:
        import pyarrow.parquet as pq

        key = self.key(rel, columns)
        if key is not None and key.exists():
            t = pq.read_table(key)
        elif (sup := self.cached_superset(rel, columns)) is not None:
            t = pq.read_table(sup, columns=columns)
        else:
            if not self.store.exists(rel):
                return None
            with self.store.fs.open(self.store.path(rel), "rb") as f:
                pf = pq.ParquetFile(f)
                have = [c for c in columns if c in set(pf.schema_arrow.names)]
                t = pf.read(columns=have)
                md = pf.metadata
                for i in range(md.num_row_groups):
                    rg = md.row_group(i)
                    self.bytes_read += sum(rg.column(j).total_compressed_size for j in range(rg.num_columns)
                                           if rg.column(j).path_in_schema.split(".")[0] in have)
            L.info("read %s: %d columns, %.1f MB so far from %s", rel, len(have), self.bytes_read / 1e6, self.store.root)
            if key is not None:
                key.parent.mkdir(parents=True, exist_ok=True)
                tmp = key.with_suffix(f".tmp{id(t)}")
                pq.write_table(t, tmp)
                tmp.replace(key)
        return t.to_pandas(strings_to_categorical=categorical)

    def local(self, rel: str, columns: list[str]) -> Path | None:
        """A local parquet file holding these columns of rel (and maybe more): the cache's, or a copy streamed row
        group by row group (bounded memory) into the cache, or into a temporary folder that cleanup() removes.
        None when the file doesn't exist."""
        import tempfile

        import pyarrow.parquet as pq

        key = self.key(rel, columns)
        if key is not None and key.exists():
            return key
        if (sup := self.cached_superset(rel, columns)) is not None:
            return sup
        if self.store.local and self.store.exists(rel):
            return Path(self.store.path(rel))
        if not self.store.exists(rel):
            return None
        if key is None or not self.keep_points:
            if self.cache is not None:
                self.cache.mkdir(parents=True, exist_ok=True)
            self._tmp = getattr(self, "_tmp", None) or tempfile.mkdtemp(prefix="skyshift-changes-", dir=self.cache)
            key = Path(self._tmp) / f"{rel.replace('/', '__')}.parquet"
        key.parent.mkdir(parents=True, exist_ok=True)
        tmp = key.with_suffix(".part")
        with self.store.fs.open(self.store.path(rel), "rb") as f:
            pf = pq.ParquetFile(f)
            have = [c for c in columns if c in set(pf.schema_arrow.names)]
            writer = None
            try:
                for i in range(pf.num_row_groups):
                    t = pf.read_row_group(i, columns=have)
                    rg = pf.metadata.row_group(i)
                    self.bytes_read += sum(rg.column(j).total_compressed_size for j in range(rg.num_columns)
                                           if rg.column(j).path_in_schema.split(".")[0] in have)
                    if writer is None:
                        writer = pq.ParquetWriter(tmp, t.schema, compression="zstd")
                    writer.write_table(t)
            finally:
                if writer is not None:
                    writer.close()
        if not tmp.exists():
            return None
        tmp.replace(key)
        L.info("read %s: %d columns, %.1f MB so far from %s", rel, len(have), self.bytes_read / 1e6, self.store.root)
        return key

    def cleanup(self) -> None:
        import shutil

        if getattr(self, "_tmp", None):
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    def json(self, rel: str):
        try:
            return self.store.read_json(rel)
        except (FileNotFoundError, ValueError):
            return None


# --- Gaia DR3 by primary key -----------------------------------------------------------------------------------------
ESA_TAP = "https://gea.esac.esa.int/tap-server/tap"
GAIA_LEVEL12_SHIFT = 35                  # source_id // 2^35 = the nested HEALPix index at level 12 (Gaia DR3)


class GaiaLookup:
    """Gaia DR3 from ESA's archive, sync TAP, conditions on the primary key only (an id list, or source_id ranges of
    level-12 HEALPix pixels: answered in seconds). Each query's answer is cached in `cache` (gaia_<sha1>.parquet).
    tap: a function adql → DataFrame (tests); None asks ESA. A failure returns None and is kept in `errors`."""

    def __init__(self, cache: Path | None = None, tap=None, enabled: bool = True):
        self.cache, self.tap, self.enabled = cache, tap, enabled
        self.errors: list[str] = []
        self.queries = 0

    def query(self, adql: str) -> pd.DataFrame | None:
        if not self.enabled:
            return None
        key = None
        if self.cache is not None:
            key = self.cache / f"gaia_{hashlib.sha1(adql.encode()).hexdigest()[:16]}.parquet"
            if key.exists():
                return pd.read_parquet(key)
        try:
            self.queries += 1
            if self.tap is not None:
                df = self.tap(adql)
            else:
                import pyvo
                df = pyvo.dal.TAPService(ESA_TAP).run_sync(adql, maxrec=500_000).to_table().to_pandas()
        except Exception as e:  # noqa: BLE001  the archive down or slow: the step says what it could not check
            self.errors.append(f"{type(e).__name__}: {str(e)[:160]}")
            return None
        df.columns = [str(c).lower() for c in df.columns]
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: v.decode() if isinstance(v, bytes) else v)
        if key is not None:
            key.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(key, index=False)
        return df

    def stars(self, ids) -> pd.DataFrame | None:
        """source_id, ra, dec (epoch 2016.0), g_mag, variable (phot_variable_flag VARIABLE), gaia_class (the
        classifier's best class, "" none). None when any query failed."""
        ids = sorted({int(i) for i in ids if int(i) > 0})
        out = []
        for i in range(0, len(ids), 400):
            chunk = ",".join(str(x) for x in ids[i:i + 400])
            df = self.query("SELECT g.source_id, g.ra, g.dec, g.phot_g_mean_mag, g.phot_variable_flag, v.best_class_name "
                            "FROM gaiadr3.gaia_source AS g LEFT OUTER JOIN gaiadr3.vari_classifier_result AS v "
                            f"ON v.source_id = g.source_id WHERE g.source_id IN ({chunk})")
            if df is None:
                return None
            out.append(df)
        if not out:
            return pd.DataFrame({"source_id": pd.Series(dtype="int64"), "ra": [], "dec": [], "g_mag": [],
                                 "variable": pd.Series(dtype=bool), "gaia_class": pd.Series(dtype=object)})
        df = pd.concat(out, ignore_index=True)
        return pd.DataFrame({
            "source_id": df["source_id"].astype(np.int64), "ra": df["ra"].astype(float), "dec": df["dec"].astype(float),
            "g_mag": pd.to_numeric(df["phot_g_mean_mag"], errors="coerce"),
            "variable": df["phot_variable_flag"].astype(str).str.strip().eq("VARIABLE"),
            "gaia_class": df["best_class_name"].fillna("").astype(str).str.strip().replace({"None": "", "nan": ""}),
        }).drop_duplicates("source_id")

    def around(self, ra, dec, g_max: float = CHANGES_GAIA_NEIGHBOUR_G) -> pd.DataFrame | None:
        """Gaia stars brighter than g_max in each position's level-12 HEALPix pixel and its 8 neighbours (pixels of
        ~52″: every star within 20″), as refcat rows (cat, ra, dec, g_mag). None when a query failed."""
        from astropy import units as u
        from astropy_healpix import HEALPix

        ra, dec = np.atleast_1d(np.asarray(ra, float)), np.atleast_1d(np.asarray(dec, float))
        if not ra.size:
            return pd.DataFrame({"cat": [], "ra": [], "dec": [], "g_mag": []})
        hp = HEALPix(nside=4096, order="nested")
        pix = hp.lonlat_to_healpix(ra * u.deg, dec * u.deg)
        nb = hp.neighbours(pix)
        allpix = np.unique(np.r_[pix, nb.ravel()])
        allpix = allpix[allpix >= 0]
        out = []
        for i in range(0, allpix.size, 150):
            conds = " OR ".join(f"source_id BETWEEN {int(p) << GAIA_LEVEL12_SHIFT} AND {((int(p) + 1) << GAIA_LEVEL12_SHIFT) - 1}"
                                for p in allpix[i:i + 150])
            df = self.query(f"SELECT ra, dec, phot_g_mean_mag FROM gaiadr3.gaia_source WHERE phot_g_mean_mag < {g_max} "
                            f"AND ({conds})")
            if df is None:
                return None
            out.append(df)
        df = pd.concat(out, ignore_index=True)
        return pd.DataFrame({"cat": "gaia", "ra": df["ra"].astype(float), "dec": df["dec"].astype(float),
                             "g_mag": pd.to_numeric(df["phot_g_mean_mag"], errors="coerce")})


# --- who each star is --------------------------------------------------------------------------------------------
def catwise_position(name: str) -> tuple[float, float]:
    """RA, Dec (deg) from a CatWISE designation "J081628.26+142456.2" (NaN when it isn't one)."""
    m = re.search(r"J(\d\d)(\d\d)(\d\d(?:\.\d+)?)([+-])(\d\d)(\d\d)(\d\d(?:\.\d+)?)", name)
    if not m:
        return float("nan"), float("nan")
    h, mi, se, sg, d, dm, ds = m.groups()
    ra = 15.0 * (int(h) + int(mi) / 60.0 + float(se) / 3600.0)
    dec = (1 if sg == "+" else -1) * (int(d) + int(dm) / 60.0 + float(ds) / 3600.0)
    return ra, dec


def ref_slug(gaia_id: int, ref_id: str, source_id: int) -> str:
    """The id's last part: gaia<N>, catwise<name>, 2mass<name> or src<source_id> (URL-safe characters only)."""
    if gaia_id is not None and int(gaia_id) > 0:
        return f"gaia{int(gaia_id)}"
    ref = str(ref_id or "")
    if ":" in ref:
        cat, name = ref.split(":", 1)
        return re.sub(r"[^A-Za-z0-9._+-]", "", cat + name)
    return f"src{int(source_id)}"


def display_name(gaia_id: int, ref_id: str, source_id: int) -> str:
    if gaia_id is not None and int(gaia_id) > 0:
        return f"Gaia DR3 {int(gaia_id)}"
    ref = str(ref_id or "")
    for pre, label in (("gaia:", "Gaia DR3"), ("catwise:", "CatWISE"), ("2mass:", "2MASS"), ("allwise:", "AllWISE")):
        if ref.startswith(pre):
            return f"{label} {ref[len(pre):]}"
    return f"SPHEREx source {int(source_id)}"


def star_info(stars: pd.DataFrame, zone: C.Zone, xid: pd.DataFrame | None, src: pd.DataFrame | None,
              frag: pd.DataFrame | None, refcat: pd.DataFrame | None, gaia: GaiaLookup,
              sky_positions=None) -> pd.DataFrame:
    """Per star (columns star, source_id, ref_id): gaia_id, cat_ref (its catalogue entry), name, ra, dec, g_mag,
    population (changes.population; `unknown` without a position), nb_sep / nb_dmag / neighbours_checked (the
    brightest-relative Gaia star within CHANGES_NEIGHBOUR_ARCSEC), gaia_variable (1 / 0 / −1 unknown), gaia_class,
    sign_blend, sign_saturated. sky_positions: gaia ids → DataFrame(source_id, ra, dec), the last resort for
    forced-only stars (the sky-wide forced list)."""
    st = stars[["star", "source_id", "ref_id"]].drop_duplicates("star").reset_index(drop=True)
    n = len(st)
    sid = st["source_id"].to_numpy(np.int64)
    ref = st["ref_id"].fillna("").astype(str).to_numpy()
    out = pd.DataFrame({"star": st["star"].to_numpy(np.int64), "source_id": sid, "ref_id": ref})
    ra, dec, g = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    nra, nde = np.full(n, np.nan), np.full(n, np.nan)
    gid = np.full(n, -1, np.int64)
    cat_ref = np.where(sid < 0, ref, "").astype(object)
    blend, sat = np.zeros(n, bool), np.zeros(n, bool)
    own = sid >= 0
    if xid is not None and own.any():
        x = xid.drop_duplicates("source_id").set_index("source_id")
        xs = x.reindex(sid[own])
        gid[own] = xs["gaia_id"].fillna(-1).to_numpy(np.int64)
        g[own] = xs["gaia_g"].to_numpy(np.float64)
        ra[own], dec[own] = xs["ra"].to_numpy(np.float64), xs["dec"].to_numpy(np.float64)
        cw = xs["catwise_ref_id"].fillna("").astype(str).to_numpy() if "catwise_ref_id" in xs else np.full(own.sum(), "")
        tm = xs["tmass_ref_id"].fillna("").astype(str).to_numpy() if "tmass_ref_id" in xs else np.full(own.sum(), "")
        gr = xs["gaia_ref_id"].fillna("").astype(str).to_numpy()
        # the Gaia place for the neighbour search: the star's own catalogue row then sits at 0″ and is skipped
        gra, gde = xs["gaia_ra"].to_numpy(np.float64), xs["gaia_dec"].to_numpy(np.float64)
        nra[own] = np.where(np.isfinite(gra), gra, np.nan)
        nde[own] = np.where(np.isfinite(gde), gde, np.nan)
        cat_ref[own] = np.where(gr != "", gr, np.where(cw != "", cw, tm))
        blend[own] |= xs["gaia_n"].fillna(0).to_numpy() >= 2
    if src is not None and own.any():
        s = src.drop_duplicates("source_id").set_index("source_id").reindex(sid[own])
        has = np.isfinite(s["ra"].to_numpy(np.float64))
        ra[own] = np.where(has, s["ra"].to_numpy(np.float64), ra[own])
        dec[own] = np.where(has, s["dec"].to_numpy(np.float64), dec[own])
        if "saturated" in s:
            sat[own] |= s["saturated"].fillna(False).to_numpy(bool)
    if frag is not None and len(frag) and own.any():
        f = frag.drop_duplicates("source_id").set_index("source_id").reindex(sid[own])
        tag = f["tag"].fillna("").astype(str).to_numpy()
        blend[own] |= (tag == "blend") | (f["n_gaia_8"].fillna(0).to_numpy() >= 2)
        sat[own] |= tag == "saturated_star"
    forced_gaia = (~own) & np.char.startswith(ref.astype(str), "gaia:")
    gid[forced_gaia] = [int(r.split(":", 1)[1]) for r in ref[forced_gaia]]
    # forced-only positions: the zone's catalogue, then Gaia, then the sky-wide forced list; CatWISE from its name
    if refcat is not None and (~own).any() and "ref_id" in refcat:
        rc = refcat.drop_duplicates("ref_id").set_index("ref_id")
        rr = rc.reindex(ref[~own])
        ra[~own], dec[~own] = rr["ra"].to_numpy(np.float64), rr["dec"].to_numpy(np.float64)
        g[~own] = rr["g_mag"].to_numpy(np.float64)
    info = gaia.stars(gid[gid > 0]) if (gid > 0).any() else None
    var = np.where(gid > 0, -1, 0).astype(np.int8)          # −1: a Gaia star whose variability is unknown
    cls = np.full(n, "", object)
    if info is not None:
        gi = info.set_index("source_id").reindex(gid)
        known = gid > 0
        var[known] = np.where(gi["variable"].fillna(False).to_numpy(bool)[known] |
                              (gi["gaia_class"].fillna("").astype(str).to_numpy()[known] != ""), 1, 0)
        cls[known] = gi["gaia_class"].fillna("").astype(str).to_numpy()[known]
        miss = forced_gaia & ~np.isfinite(ra)
        ra[miss], dec[miss] = gi["ra"].to_numpy(np.float64)[miss], gi["dec"].to_numpy(np.float64)[miss]
        nog = ~np.isfinite(g) & known
        g[nog] = gi["g_mag"].to_numpy(np.float64)[nog]
    miss = forced_gaia & ~np.isfinite(ra)
    if miss.any() and sky_positions is not None:
        sp = sky_positions(gid[miss])
        if sp is not None and len(sp):
            sp = sp.drop_duplicates("source_id").set_index("source_id").reindex(gid[miss])
            ra[miss], dec[miss] = sp["ra"].to_numpy(np.float64), sp["dec"].to_numpy(np.float64)
    cw_only = (~own) & np.char.startswith(ref.astype(str), "catwise:") & ~np.isfinite(ra)
    for i in np.flatnonzero(cw_only):
        ra[i], dec[i] = catwise_position(ref[i])
    box = (zone.ra_min, zone.ra_max, zone.dec_min, zone.dec_max)
    pop = CH.population(sid, ref, ra, dec, box)
    pop = np.where(own | np.isfinite(ra), pop, "unknown")
    # bright neighbours: the zone's catalogue inside its coverage, Gaia around the others
    nra, nde = np.where(np.isfinite(nra), nra, ra), np.where(np.isfinite(nde), nde, dec)
    nb_sep, nb_dm = np.full(n, np.nan), np.full(n, np.nan)
    checked = np.zeros(n, bool)
    have = np.isfinite(ra) & np.isfinite(dec)
    m = CHANGES_REFCAT_MARGIN_DEG - CH.CHANGES_NEIGHBOUR_ARCSEC / 3600.0
    cosd = np.cos(np.radians(np.clip(np.nan_to_num(dec), -89.9, 89.9)))
    inside = have & (dec >= zone.dec_min - m) & (dec <= zone.dec_max + m) & (
        ra >= zone.ra_min - m / cosd) & (ra <= zone.ra_max + m / cosd)
    if refcat is not None and inside.any():
        nb_sep[inside], nb_dm[inside] = CH.bright_neighbours(nra[inside], nde[inside], g[inside], refcat)
        checked[inside] = True
    outside = have & ~inside
    if outside.any():
        around = gaia.around(nra[outside], nde[outside])
        if around is not None:
            nb_sep[outside], nb_dm[outside] = CH.bright_neighbours(nra[outside], nde[outside], g[outside], around)
            checked[outside] = True
    names = [display_name(gid[i], cat_ref[i], sid[i]) for i in range(n)]
    slugs = [ref_slug(gid[i], cat_ref[i], sid[i]) for i in range(n)]
    out = out.assign(gaia_id=gid, cat_ref=cat_ref, name=names, slug=slugs, ra=ra, dec=dec, g_mag=g, population=pop,
                     nb_sep=nb_sep, nb_dmag=nb_dm, neighbours_checked=checked, gaia_variable=var, gaia_class=cls,
                     sign_blend=blend, sign_saturated=sat)
    return out


def sky_forced_positions(reader: Reader):
    """gaia ids → their rows of the sky-wide forced list (refcat/sky/forced_n32/p<pixel>.parquet: ref_id, ra, dec)."""
    from .forced_sky import partition_rel, pixel_of_source_id

    def get(ids) -> pd.DataFrame | None:
        ids = np.asarray(ids, np.int64)
        parts = []
        for pix in np.unique(pixel_of_source_id(ids)):
            t = reader(partition_rel(int(pix)), ["ref_id", "ra", "dec"])
            if t is not None and len(t):
                parts.append(t)
        if not parts:
            return None
        t = pd.concat(parts, ignore_index=True)
        t = t[t["ref_id"].astype(str).str.startswith("gaia:")]
        return pd.DataFrame({"source_id": t["ref_id"].astype(str).str[5:].astype(np.int64), "ra": t["ra"], "dec": t["dec"]})
    return get


# --- brightness and colour -----------------------------------------------------------------------------------------
def reference_stars(vt: pd.DataFrame, xid: pd.DataFrame | None) -> np.ndarray:
    """Star keys of the Gaia-matched light curves (a source crossid matched to Gaia, or a forced Gaia target)."""
    m = np.array(vt["ref_id"].astype(str).str.startswith("gaia:").to_numpy(), dtype=bool)
    if xid is not None and len(xid):
        gsrc = xid.loc[xid["gaia_ref_id"].fillna("").astype(str) != "", "source_id"].to_numpy(np.int64)
        m |= np.isin(vt["source_id"].to_numpy(np.int64), gsrc)
    return np.unique(vt["star"].to_numpy(np.int64)[m])


def _measure(vt: pd.DataFrame, before: dict, after: dict, refs: np.ndarray, floors: dict | None = None):
    pr = CH.pairs(vt, before, after)
    if floors is None:
        floors = CH.fit_floor(pr, refs)
    pr = CH.apply_floor(pr, floors)
    best = CH.best_per_detector(pr)
    return best, CH.star_brightness(best), CH.star_colour(best), floors


def null_counts(vth: pd.DataFrame, xid: pd.DataFrame | None, floors: dict, visits: list[int]) -> list[dict]:
    """The halves null on one star chunk (zero points already applied): per visit, the same rules between the two
    halves of its sessions, with the between-visit floors."""
    refs = reference_stars(vth, xid)
    out = []
    for v in visits:
        best, sb, sc, _ = _measure(vth, {"visit": v, "half": 0}, {"visit": v, "half": 1}, refs, floors)
        own = sb["source_id"].to_numpy(np.int64) >= 0
        out.append({"visit": int(v), "stars": int(len(sb)), "alerts": int(sb["alert"].sum()),
                    "colour_alerts": int(sc["colour_alert"].sum()), "stars_own_source": int(own.sum()),
                    "alerts_own_source": int(sb["alert"].to_numpy()[own].sum())})
    return out


def null_summary(per_chunk: list[list[dict]]) -> dict:
    """The halves null (docs/watch-changes.md) summed over the star chunks. Alerts per 1,000 stars: the false-alarm
    rate (a few fast real variables included)."""
    keys = ["stars", "alerts", "colour_alerts", "stars_own_source", "alerts_own_source"]
    per: dict[int, dict] = {}
    for rows in per_chunk:
        for r in rows:
            p = per.setdefault(r["visit"], {"visit": r["visit"], **{k: 0 for k in keys}})
            for k in keys:
                p[k] += r[k]
    per_visit = [per[v] for v in sorted(per)]
    tot = {k: sum(p[k] for p in per_visit) for k in keys}
    if not per_visit:
        return {"stars": 0}
    tot["per_1000"] = round(1000.0 * tot["alerts"] / max(tot["stars"], 1), 3)
    tot["per_1000_own_source"] = round(1000.0 * tot["alerts_own_source"] / max(tot["stars_own_source"], 1), 3)
    return {**tot, "per_visit": per_visit}


def _visit_mjd(vt: pd.DataFrame, stars: np.ndarray) -> dict[tuple, float]:
    """(star, detector, origin, visit) → that visit's mid time, for these stars."""
    x = vt[np.isin(vt["star"].to_numpy(np.int64), stars) & (vt["half"].to_numpy() == 0)]
    return {(int(r.star), int(r.detector), str(r.origin), int(r.visit)): float(r.mjd_mid) for r in x.itertuples(index=False)}


def _f(x, nd=4):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


class Points:
    """lightcurves.parquet's points from a local copy, read in batches with a filter (a file written as one row group
    never decodes whole): one star chunk, one detector or some stars."""

    def __init__(self, path: Path, columns: list[str] = POINT_COLUMNS):
        self.path, self.columns = Path(path), list(columns)

    BATCH = 1_000_000

    def read(self, keep=None) -> pd.DataFrame:
        """The rows where keep(batch) (a boolean Arrow array) is true, scanned a million rows at a time.
        keep None: every row."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(self.path)
        cols = [c for c in self.columns if c in set(pf.schema_arrow.names)]
        parts, schema = [], None
        for b in pf.iter_batches(batch_size=self.BATCH, columns=cols):
            schema = schema or b.schema
            if keep is not None:
                b = b.filter(keep(b))
            if b.num_rows:
                parts.append(b)
        if schema is None:
            schema = pa.schema([pf.schema_arrow.field(c) for c in cols])
        t = pa.Table.from_batches(parts, schema=schema) if parts else schema.empty_table()
        return t.to_pandas(strings_to_categorical=True, self_destruct=True)

    def detector(self, d: int) -> pd.DataFrame:
        import pyarrow.compute as pc

        return self.read(lambda b: pc.equal(b.column("detector"), int(d)))

    def of(self, source_ids, ref_ids) -> pd.DataFrame:
        """The points of these light curves (sources by id, catalogue stars by ref_id)."""
        import pyarrow as pa
        import pyarrow.compute as pc

        sids = pa.array(sorted(int(x) for x in source_ids), pa.int64())
        refs = pa.array(sorted(str(x) for x in ref_ids), pa.string())

        def keep(b):
            m = pc.is_in(b.column("source_id"), value_set=sids)
            r = b.column("ref_id")
            if pa.types.is_dictionary(r.type):
                r = r.cast(pa.string())
            return pc.or_(m, pc.fill_null(pc.is_in(r.cast(pa.string()), value_set=refs), False))
        return self.read(keep)


def star_chunks(path: Path, chunk_points: int, workdir: Path) -> list[Path]:
    """Split the points into ceil(rows / chunk_points) files by star (changes.star_keys modulo the count), so every
    light curve of a star sits in one file and each file holds ~chunk_points points. One chunk: the file itself.
    Reads one batch at a time; the parts take about the file's size on disk."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    k = max(1, math.ceil(pf.metadata.num_rows / max(int(chunk_points), 1)))
    if k == 1:
        return [Path(path)]
    cols = [c for c in POINT_COLUMNS if c in set(pf.schema_arrow.names)]
    paths = [Path(workdir) / f"stars_{i:04d}.parquet" for i in range(k)]
    writers: list = [None] * k
    try:
        for b in pf.iter_batches(batch_size=Points.BATCH, columns=cols):
            b = pa.RecordBatch.from_arrays([a.cast(a.type.value_type) if pa.types.is_dictionary(a.type) else a
                                            for a in b.columns], names=b.schema.names)   # one plain schema for all parts
            sid = b.column("source_id").to_numpy(zero_copy_only=False)
            ref = b.column("ref_id").to_numpy(zero_copy_only=False)
            part = (CH.star_keys(sid, ref).view(np.uint64) % np.uint64(k)).astype(np.int64)
            for i in np.unique(part):
                sub = b.filter(pa.array(part == i))
                if writers[i] is None:
                    writers[i] = pq.ParquetWriter(paths[i], sub.schema, compression="zstd")
                writers[i].write_table(pa.Table.from_batches([sub]))
    finally:
        for w in writers:
            if w is not None:
                w.close()
    return [p for p, w in zip(paths, writers) if w is not None]


def _zp_inputs(vt: pd.DataFrame, xid) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A chunk's share of the zero points: its reference rows' keys and means, and every key it has."""
    k = CH.zp_keys(vt)
    isref = np.isin(vt["star"].to_numpy(np.int64), reference_stars(vt, xid))
    return k[isref], vt["mean"].to_numpy(np.float64)[isref], np.unique(k)


def _zp_table(parts: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> dict:
    if not parts:
        return {}
    keys = np.concatenate([p[0] for p in parts] + [p[2] for p in parts])
    nref = sum(p[0].size for p in parts)
    means = np.concatenate([p[1] for p in parts] + [np.full(keys.size - nref, np.nan)])
    return CH.zero_point_table(keys, means, np.arange(keys.size) < nref)


def brightness_changes(zone: str, pts: Points, xid, src, frag, refcat, gaia: GaiaLookup, sky_positions=None,
                       with_null: bool = True, chunk_points: int = CHANGES_CHUNK_POINTS, workdir: Path | None = None) -> tuple[list[dict], dict]:
    """Rows of kind brightness and colour for one zone's light-curve points, and the report. The points are split by
    star into chunks of ~chunk_points (star_chunks); zero points and floors come from every chunk (exact: the same
    numbers as one chunk), each chunk is measured alone, so memory follows chunk_points, not the zone."""
    import shutil
    import tempfile

    if workdir is not None:
        Path(workdir).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="skyshift-changes-chunks-", dir=workdir))
    try:
        return _brightness_changes(zone, pts, xid, src, frag, refcat, gaia, sky_positions, with_null, chunk_points, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _brightness_changes(zone, pts, xid, src, frag, refcat, gaia, sky_positions, with_null, chunk_points, tmp):
    z = C.ZONES[zone]
    t0 = time.time()
    chunks = star_chunks(pts.path, chunk_points, tmp)
    # 1. per star chunk: visit tables (kept on disk) and the zero points' inputs
    zin, zin_h, vt_files, vth_files = [], [], [], []
    n_vt, visit_set = 0, set()
    for i, path in enumerate(chunks):
        p = Points(path).read()
        vt = CH.visit_table(p)
        vth = CH.visit_table(p, halves=True) if with_null else None
        del p
        if len(vt):
            zin.append(_zp_inputs(vt, xid))
            visit_set |= {int(v) for v in np.unique(vt["visit"].to_numpy())}
            n_vt += len(vt)
            vt_files.append(tmp / f"vt_{i:04d}.pkl")
            vt.to_pickle(vt_files[-1])
        if vth is not None and len(vth):
            zin_h.append(_zp_inputs(vth, xid))
            vth_files.append(tmp / f"vth_{i:04d}.pkl")
            vth.to_pickle(vth_files[-1])
        del vt, vth
        if path != pts.path:
            Path(path).unlink(missing_ok=True)
    rep: dict = {"light_curve_visits": int(n_vt), "chunks": len(chunks), "chunk_points": int(chunk_points)}
    L.info("changes %s: %d light-curve visits in %d star chunks (%.0f s)", zone, n_vt, len(chunks), time.time() - t0)
    if not n_vt:
        return [], {**rep, "skipped": "no usable points"}
    table = _zp_table(zin)
    zps = CH.zero_point_rows(table)
    del zin
    visits = sorted(visit_set)
    rep.update({"visits": visits, "zero_points": zps})
    if len(visits) < 2:
        return [], {**rep, "skipped": "one visit"}
    last = visits[-1]
    before_visits = visits[:-1]
    before = {"visit": visits[0]} if len(visits) == 2 else {"visits": before_visits}
    after = {"visit": last}

    def chunk_vt(f):
        return CH.apply_zero_points(pd.read_pickle(f), table)

    # 2. the floors from every chunk's reference light curves
    fl = []
    for f in vt_files:
        vt = chunk_vt(f)
        pr = CH.pairs(vt, before, after)
        m = np.isin(pr["star"].to_numpy(np.int64), reference_stars(vt, xid))
        fl.append(pd.DataFrame({"star": np.zeros(int(m.sum()), np.int64), "detector": pr["detector"].to_numpy()[m],
                                "origin": pr["origin"].astype(str).to_numpy()[m], "d": pr["d"].to_numpy(np.float64)[m],
                                "sd": pr["sd"].to_numpy(np.float64)[m]}))
    floors = CH.fit_floor(pd.concat(fl, ignore_index=True), np.zeros(1, np.int64))
    del fl
    # 3. each chunk measured alone; only the candidates are kept
    cands, bests, scs, vmjd = [], [], [], {}
    tot = {"stars": 0, "stars_own_source": 0, "alerts": 0, "colour_alerts": 0}
    for f in vt_files:
        vt = chunk_vt(f)
        best, sb, sc, _ = _measure(vt, before, after, reference_stars(vt, xid), floors)
        tot["stars"] += int(len(sb))
        tot["stars_own_source"] += int((sb["source_id"].to_numpy(np.int64) >= 0).sum())
        tot["alerts"] += int(sb["alert"].sum())
        tot["colour_alerts"] += int(sc["colour_alert"].sum())
        sca = sc.loc[sc["colour_alert"].to_numpy(bool)]
        c = sb[sb["alert"].to_numpy(bool) | sb["star"].isin(sca["star"]).to_numpy()]
        cands.append(c)
        bests.append(best[best["star"].isin(c["star"]).to_numpy()])
        scs.append(sca)
        vmjd.update(_visit_mjd(vt, c["star"].to_numpy(np.int64)))
    rep.update({"before": before_visits, "after": last, "floors": floors, **tot})
    L.info("changes %s: %d stars, %d brightness and %d colour alerts (%.0f s)", zone, tot["stars"], tot["alerts"],
           tot["colour_alerts"], time.time() - t0)
    if vth_files:
        table_h = _zp_table(zin_h)
        rep["null"] = null_summary([null_counts(CH.apply_zero_points(pd.read_pickle(f), table_h), xid, floors, visits)
                                    for f in vth_files])
        L.info("changes %s: null %s per 1,000 (%.0f s)", zone, rep["null"].get("per_1000"), time.time() - t0)
    del zin_h
    cand = pd.concat(cands, ignore_index=True).sort_values("star", kind="stable").reset_index(drop=True)
    best = pd.concat(bests, ignore_index=True)
    sc_alert = pd.concat(scs, ignore_index=True).set_index("star")
    if not len(cand):
        return [], {**rep, "listed": 0}
    info = star_info(cand, z, xid, src, frag, refcat, gaia, sky_positions).set_index("star")
    cand = cand.join(info.drop(columns=["source_id", "ref_id"]), on="star")
    # the one-session test on the brightness alerts (their points only)
    cand["visit_a"] = [visits[0] if len(visits) == 2 else list(before_visits)] * len(cand)
    cand["visit_b"] = last
    cand["survives_session_drop"] = False
    al = cand[cand["alert"].to_numpy(bool)]
    if len(al):
        sids = set(al.loc[al["source_id"] >= 0, "source_id"].astype(np.int64))
        refs_forced = set(al.loc[al["source_id"] < 0, "ref_id"].astype(str))
        ok = CH.survives_session_drop(pts.of(sids, refs_forced), al, floors, zps)
        cand.loc[al.index, "survives_session_drop"] = ok
    listable = cand["population"].isin(["source", "forced_outside"]).to_numpy() & ~(
        np.nan_to_num(cand["nb_dmag"].to_numpy(np.float64), nan=99.0) <= CH.CHANGES_NEIGHBOUR_DMAG)
    is_b = cand["alert"].to_numpy(bool) & cand["survives_session_drop"].to_numpy(bool) & listable
    is_c = cand["star"].isin(sc_alert.index).to_numpy() & listable & ~is_b
    pops = cand["population"].value_counts().to_dict()
    rep.update({
        "alerts_by_population": {str(k): int(v) for k, v in cand[cand["alert"]]["population"].value_counts().items()},
        "candidates_by_population": {str(k): int(v) for k, v in pops.items()},
        "alerts_bright_neighbour": int((cand["alert"].to_numpy(bool) & (np.nan_to_num(cand["nb_dmag"].to_numpy(np.float64), nan=99.0)
                                        <= CH.CHANGES_NEIGHBOUR_DMAG)).sum()),
        "alerts_one_session": int((cand["alert"].to_numpy(bool) & ~cand["survives_session_drop"].to_numpy(bool)).sum()),
        "neighbours_unchecked": int((~cand["neighbours_checked"].to_numpy(bool)).sum()),
        "listed_brightness": int(is_b.sum()), "listed_colour": int(is_c.sum()),
    })
    rows = []
    for i in np.flatnonzero(is_b | is_c):
        r = cand.iloc[i]
        star = int(r["star"])
        others = best[(best["star"] == star) & (best["detector"] != r["detector"])]
        others = others[np.abs(others["z"].to_numpy()) >= CHANGES_OTHERS_Z].sort_values("detector")
        oth = [(int(o.detector), float(o.frac), float(o.z)) for o in others.itertuples(index=False)]
        col = sc_alert.loc[star] if star in sc_alert.index else None
        rr = r.copy()
        rr["mjd_mid_a"] = next((vmjd[k] for v in reversed(before_visits)       # the latest earlier visit it has
                                if (k := (star, int(r["detector"]), str(r["origin"]), v)) in vmjd), np.nan)
        rr["mjd_mid_b"] = vmjd.get((star, int(r["detector"]), str(r["origin"]), last), r.get("mjd_mid_b", np.nan))
        det = int(r["detector"])
        if is_b[i]:
            kind = "brightness"
            text, look, values = CH.brightness_text(rr, oth)
            checks = CH.brightness_checks(rr)
            if col is not None:
                ct, cl, cv = CH.colour_text(col, r["name"], rr["visit_a"], last)
                checks = CH._sorted_checks(checks + [{"code": "colour_changed", "level": "ok", "text": ct, "look": cl,
                                                      "values": cv}])
        else:
            kind = "colour"
            text, look, values = CH.colour_text(col, r["name"], rr["visit_a"], last)
            checks = CH._sorted_checks(CH.common_checks(rr, det))
        values = {**values, "other_detectors": [{"det": d, "change_frac": round(f, 4), "sigma": round(zz, 2)} for d, f, zz in oth]}
        rows.append({
            "id": f"{zone}-ch-{r['slug']}", "zone": zone, "kind": kind, "star": star,
            "source_id": int(r["source_id"]), "ref_id": str(r["cat_ref"] or ""), "gaia_id": int(r["gaia_id"]),
            "name": r["name"], "ra": float(r["ra"]), "dec": float(r["dec"]), "g_mag": r["g_mag"],
            "population": r["population"], "origin": r["origin"], "detector": det,
            "visits_before": [int(v) for v in before_visits], "visit_after": last,
            "mjd_before": rr["mjd_mid_a"], "mjd_after": rr["mjd_mid_b"],
            "rel_before": r["mean_a"], "rel_after": r["mean_b"], "change_frac": r["frac"], "sigma": r["z"],
            "agree": int(r["agree"]), "disagree": int(r["disagree"]),
            "others": json.dumps(values["other_detectors"]),
            "colour_change": float(col["d1_d6_change"]) if col is not None else np.nan,
            "colour_sigma": float(col["colour_z"]) if col is not None else np.nan,
            "colour_detectors": int(col["n_det_colour"]) if col is not None else 0,
            "gaia_variable": int(r["gaia_variable"]), "gaia_class": str(r["gaia_class"] or ""),
            "survives_session_drop": bool(r["survives_session_drop"]), "nb_sep_arcsec": r["nb_sep"],
            "nb_dmag": r["nb_dmag"], "neighbours_checked": bool(r["neighbours_checked"]), "snr": np.nan,
            "n_dots": 0, "n_sessions": int(r.get("n_sessions_b", 0) or 0), "n_detectors": int(r.get("n_det", 0) or 0),
            "detectors": [det] + [d for d, _, _ in oth], "text": text, "look": look,
            "values": json.dumps(values, default=_json_num), "checks": json.dumps(checks, default=_json_num),
        })
    rep["listed"] = len(rows)
    rep["listed_gaia_variable"] = int(sum(r["gaia_variable"] == 1 for r in rows))
    return rows, rep


def _json_num(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


# --- appeared and vanished -------------------------------------------------------------------------------------------
def _dets_text(bits: int) -> tuple[list[int], str]:
    ds = [d for d in range(1, 7) if int(bits) >> (d - 1) & 1]
    if not ds:
        return [], "?"
    if ds == list(range(ds[0], ds[-1] + 1)) and len(ds) > 2:
        return ds, f"D{ds[0]}–D{ds[-1]}"
    return ds, ", ".join(f"D{d}" for d in ds)


def new_source_text(r) -> tuple[str, str, dict]:
    ds, dtxt = _dets_text(int(r.detectors_bits))
    v, ov = int(r.visit), int(r.other_visit)
    seen = (f"{int(r.n_dots)} photos over {int(r.n_sessions)} sessions saw it in {dtxt} at S/N {float(r.snr_max):.0f}")
    cover = (f"although {int(r.cover_other)} of visit {ov}'s photos covered the spot" if np.isfinite(r.cover_other)
             else "although that visit covered the spot")
    where = f"RA {float(r.ra):.5f}°, Dec {float(r.dec):+.5f}°"
    if r.kind == "appeared":
        text = (f"A source with no Gaia, CatWISE or 2MASS entry ({where}) appeared in visit {v} ({CH.mjd_date(r.mjd_mean)}): "
                f"{seen}; visit {ov} ({CH.mjd_date(r.mjd_other)}) did not see it, {cover}.")
    else:
        text = (f"A source with no Gaia, CatWISE or 2MASS entry ({where}) was there in visit {v} ({CH.mjd_date(r.mjd_mean)}): "
                f"{seen}; it was gone in visit {ov} ({CH.mjd_date(r.mjd_other)}), {cover}.")
    det = ds[0] if ds else 2
    look = (f"Viewer, months clock, D{det}: blink visit {min(v, ov)} against visit {max(v, ov)}; it should be there in "
            f"visit {v} only, while the stars around it stay the same.")
    values = {"visit": v, "other_visit": ov, "snr": round(float(r.snr_max), 1), "dots": int(r.n_dots),
              "sessions": int(r.n_sessions), "photos_other_visit": int(r.cover_other) if np.isfinite(r.cover_other) else None,
              "detectors": ds}
    return text, look, values


def new_source_checks(r, refcat: pd.DataFrame | None) -> list[dict]:
    """gaia_near (a Gaia star within 6″: probably its dots split off), ghost_possible (a G < 11 star within 6′: the
    180° roll between visits moves its ghosts)."""
    out = []
    if refcat is None or not len(refcat):
        return out
    gg = refcat[refcat["cat"].astype(str) == "gaia"] if "cat" in refcat else refcat
    if not len(gg):
        return out
    cosd = math.cos(math.radians(float(r.dec)))
    de = (gg["ra"].to_numpy(np.float64) - float(r.ra)) * cosd * 3600.0
    dn = (gg["dec"].to_numpy(np.float64) - float(r.dec)) * 3600.0
    sep = np.hypot(de, dn)
    gm = gg["g_mag"].to_numpy(np.float64)
    near = sep <= CHANGES_GAIA_NEAR_ARCSEC
    det = _dets_text(int(r.detectors_bits))[0][:1] or [2]
    if near.any():
        j = int(np.flatnonzero(near)[np.argmin(sep[near])])
        out.append({"code": "gaia_near", "level": "warn",
                    "text": f"A Gaia star (G {gm[j]:.1f}) sits {sep[j]:.1f}″ away: this may be that star's own dots split "
                            "off into a source of their own.",
                    "look": f"Viewer, months clock, D{det[0]}: is the Gaia star's marker on the same spot in both visits?",
                    "values": {"sep_arcsec": round(float(sep[j]), 1), "g_mag": _f(gm[j], 2)}})
    bright = (sep <= CHANGES_GHOST_ARCSEC) & (gm < CHANGES_GHOST_G)
    if bright.any():
        j = int(np.flatnonzero(bright)[np.argmin(gm[bright])])
        out.append({"code": "ghost_possible", "level": "warn",
                    "text": f"A bright star (G {gm[j]:.1f}) sits {sep[j]:.0f}″ away; SPHEREx ghosts of bright stars land "
                            "elsewhere when the telescope turns between visits.",
                    "look": f"Viewer, months clock, D{det[0]}, wide field: does a ghost of that star sit on this spot in one "
                            "visit only?",
                    "values": {"sep_arcsec": round(float(sep[j]), 0), "g_mag": _f(gm[j], 2)}})
    return out


def new_source_changes(zone: str, orph: pd.DataFrame | None, src: pd.DataFrame | None, refcat: pd.DataFrame | None,
                       last_visit: int | None) -> tuple[list[dict], dict]:
    if orph is None or not len(orph):
        return [], {"orphans": 0 if orph is not None else None}
    o = orph.drop_duplicates("source_id")
    last = int(last_visit) if last_visit is not None else int(np.nanmax(np.r_[o["visit"].to_numpy(), o["other_visit"].to_numpy()]))
    snr = o.set_index("source_id")["snr_max"]
    dets = src.drop_duplicates("source_id").set_index("source_id")["detectors"] if src is not None else None
    n = CH.new_sources(o, last, snr, dets)
    n = n[n["n_sessions"].to_numpy() >= CHANGES_NEW_MIN_SESSIONS].sort_values("snr", ascending=False)
    rep = {"orphans": int(len(o)), "clean": int((o["reason"].fillna("").astype(str) == "").sum()),
           "appeared": int((n["kind"] == "appeared").sum()), "vanished": int((n["kind"] == "vanished").sum()),
           "last_visit": last}
    rows = []
    bits = dets.reindex(n["source_id"].to_numpy()).fillna(0).to_numpy(np.int64) if dets is not None else np.zeros(len(n), np.int64)
    for k, r in enumerate(n.assign(detectors_bits=bits).itertuples(index=False)):
        text, look, values = new_source_text(r)
        checks = CH._sorted_checks(new_source_checks(r, refcat))
        sid = int(r.source_id)
        rows.append({
            "id": f"{zone}-ch-src{sid}", "zone": zone, "kind": str(r.kind), "star": sid, "source_id": sid, "ref_id": "",
            "gaia_id": -1, "name": f"SPHEREx source {sid}", "ra": float(r.ra), "dec": float(r.dec), "g_mag": np.nan,
            "population": "source", "origin": "dot", "detector": values["detectors"][0] if values["detectors"] else 2,
            "visits_before": [min(int(r.visit), int(r.other_visit))], "visit_after": max(int(r.visit), int(r.other_visit)),
            "mjd_before": float(min(r.mjd_mean, r.mjd_other)), "mjd_after": float(max(r.mjd_mean, r.mjd_other)),
            "rel_before": np.nan, "rel_after": np.nan, "change_frac": np.nan, "sigma": np.nan, "agree": 0,
            "disagree": 0, "others": "[]", "colour_change": np.nan, "colour_sigma": np.nan, "colour_detectors": 0,
            "gaia_variable": 0, "gaia_class": "", "survives_session_drop": True, "nb_sep_arcsec": np.nan,
            "nb_dmag": np.nan, "neighbours_checked": True, "snr": float(r.snr_max), "n_dots": int(r.n_dots),
            "n_sessions": int(r.n_sessions), "n_detectors": len(values["detectors"]), "detectors": values["detectors"],
            "text": text, "look": look, "values": json.dumps(values, default=_json_num),
            "checks": json.dumps(checks, default=_json_num),
        })
    return rows, rep


# --- the step --------------------------------------------------------------------------------------------------------
def run(store: Store, zone: str, *, cache: Path | None = None, out: Store | None = None, gaia: GaiaLookup | None = None,
        with_null: bool = True, keep_points: bool = True, chunk_points: int = CHANGES_CHUNK_POINTS) -> dict:
    """results/<zone>/changes.parquet (CHANGE_COLUMNS) and changes_report.json, written to `out` (default the store).
    A zone without lightcurves.parquet gets appeared / vanished only (the report says so). keep_points False: the
    light-curve copy is deleted after the zone (the small reads and Gaia's answers stay in `cache`)."""
    t0 = time.time()
    rd = Reader(store, cache, keep_points)
    gaia = gaia or GaiaLookup(cache / "gaia" if cache else None)
    rep: dict = {"zone": zone, "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "thresholds": {k: v for k, v in vars(CH).items() if k.startswith("CHANGES_")} |
                 {k: v for k, v in globals().items() if k.startswith("CHANGES_")}}
    xid = rd(results_rel(zone, "crossids.parquet"), XID_COLUMNS)
    src = rd(results_rel(zone, "sources.parquet"), SRC_COLUMNS)
    frag = rd(results_rel(zone, "fragments.parquet"), FRAG_COLUMNS)
    refcat = rd(f"refcat/{zone}.parquet", REFCAT_COLUMNS)
    rows: list[dict] = []
    last = None
    lp = rd.local(results_rel(zone, "lightcurves.parquet"), POINT_COLUMNS)
    if lp is None:
        rep["brightness"] = {"skipped": "no lightcurves.parquet"}
    else:
        b, brep = brightness_changes(zone, Points(lp), xid, src, frag, refcat, gaia, sky_forced_positions(rd), with_null,
                                     chunk_points=chunk_points, workdir=cache)
        rows += b
        rep["brightness"] = brep
        last = (brep.get("visits") or [None])[-1]
    rd.cleanup()
    if last is None:
        s = rd.json(results_rel(zone, "summary.json")) or {}
        last = ((s.get("steps") or {}).get("photolist") or {}).get("visits")
    orph = rd(results_rel(zone, "orphans.parquet"), ORPHAN_COLUMNS)
    nrows, nrep = new_source_changes(zone, orph, src, refcat, last)
    rows += nrows
    rep["new"] = nrep
    df = pd.DataFrame(rows) if rows else empty_changes()
    for c, t in CHANGE_COLUMNS.items():
        if c not in df:
            df[c] = pd.Series(dtype=t if t not in ("string", "object") else object)
        elif t not in ("string", "object"):
            df[c] = df[c].astype(t)
    df = df[list(CHANGE_COLUMNS)]
    if df["id"].duplicated().any():
        raise ValueError(f"changes {zone}: ids are not unique")
    try:
        coll = rd(photolist_rel(zone), ["collection"])
        rep["collections"] = sorted(set(coll["collection"].astype(str))) if coll is not None else []
    except Exception:  # noqa: BLE001  the data release is a label only
        rep["collections"] = []
    rep.update({"rows": int(len(df)), "kinds": {str(k): int(v) for k, v in df["kind"].value_counts().items()},
                "gaia_queries": gaia.queries, "gaia_errors": gaia.errors[:5], "bytes_read": rd.bytes_read,
                "seconds": round(time.time() - t0, 1)})
    o = out or store
    o.write_parquet(results_rel(zone, "changes.parquet"), df)
    o.write_json(results_rel(zone, "changes_report.json"), rep)
    L.info("changes %s: %d rows %s in %.0f s", zone, len(df), rep["kinds"], time.time() - t0)
    return rep


def zones_with_lightcurves(store: Store) -> list[str]:
    """Every configured zone with results/<zone>/lightcurves.parquet, largest box first."""
    zs = [z for z in C.ZONES.values() if store.exists(results_rel(z.name, "lightcurves.parquet"))]
    zs.sort(key=lambda z: -(z.ra_max - z.ra_min) * (z.dec_max - z.dec_min))
    return [z.name for z in zs]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skyshift_pipeline.changes_step", description=__doc__.split("\n")[0])
    ap.add_argument("--zones", required=True, help="comma-separated zones, or `all` (every zone with lightcurves)")
    ap.add_argument("--skip-done", action="store_true", help="skip zones that already have changes.parquet")
    ap.add_argument("--root", default="gs://nasa-glazing-data")
    ap.add_argument("--cache", help="folder that keeps the column reads and Gaia answers between runs")
    ap.add_argument("--drop-points", action="store_true",
                    help="delete each zone's light-curve copy when it is done (~0.6 GB per ecliptic zone otherwise kept)")
    ap.add_argument("--out-root", help="write results/<zone>/changes.* under this root instead of --root")
    ap.add_argument("--no-gaia", action="store_true", help="never ask ESA's archive (variability unknown)")
    ap.add_argument("--chunk-points", type=int, default=CHANGES_CHUNK_POINTS,
                    help="light-curve points per star chunk (memory follows this, not the zone; default %(default)s)")
    ap.add_argument("--no-null", action="store_true", help="skip the halves null (the report's false-alarm rate)")
    a = ap.parse_args(argv)
    log.setup()
    store = Store(a.root)
    out = Store(a.out_root) if a.out_root else None
    cache = Path(a.cache) if a.cache else None
    bad = 0
    zones = zones_with_lightcurves(store) if a.zones == "all" else a.zones.split(",")
    for z in zones:
        if a.skip_done and (out or store).exists(results_rel(z, "changes.parquet")):
            L.info("changes %s: changes.parquet exists, skipped", z)
            continue
        try:
            run(store, z, cache=cache, out=out, gaia=GaiaLookup(cache / "gaia" if cache else None, enabled=not a.no_gaia),
                with_null=not a.no_null, keep_points=not a.drop_points, chunk_points=a.chunk_points)
        except Exception:  # noqa: BLE001  one zone's failure doesn't stop the others
            L.exception("changes %s failed", z)
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
