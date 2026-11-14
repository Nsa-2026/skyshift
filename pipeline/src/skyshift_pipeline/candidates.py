"""Export `candidates.json` (contracts/api.md, "candidates.json"): what the pipeline found, for people
to review on the site's Discover and Review pages.

    uv run skyshift-candidates                                   # every finished zone, hunt first
    uv run skyshift-candidates --zones hunt,pilot --out ../web/public/data/candidates.json

A zone is finished when results/<zone>/suspects.parquet exists (`suspects` is the pipeline's last step).
Four kinds:
- mover     results/<zone>/suspects.parquet (movers no catalogue explains), clock hours. The rate and
            direction come from the mover's own dots, re-read from dots/ for the exposures it was seen in.
- high_pm   results/<zone>/highpm_candidates.parquet, the `vetted` rows (highpm_leads), clock months: known
            Gaia stars whose motion we confirm, and new ones that passed the catalogue checks. Unvetted rows
            (Gaia / CatWISE motion not ours, chance-level pairs, ...) are fakes and are not listed.
- spectral  results/<zone>/classes.parquet: a band label (`label_*`) or brown_dwarf_like, and no Gaia star
            (or a Gaia parallax >= NEAR_PARALLAX_MAS), clock months; the SPECTRAL_CAP best by score.
            Labels first pass clean_labels (photoz.parquet, 15 Nov review): PAH 3.3 µm only from photoz's
            label_pah_33_z (at the galaxy's redshift) or at a good photo-z below PHOTOZ_PAH_Z_MAX; no ice on a good
            photo-z galaxy; a Gaia star with BP − RP >= LABEL_MDWARF_BP_RP keeps no LABEL_MDWARF_EXPECTED label (an
            M dwarf's own water vapour and CO). And a Gaia DR3 star (G < BLEND_MAX_G) within BLEND_ARCSEC at the
            source's epoch (refcat, pm-propagated) counts as a Gaia match: crossid's 2.5″ radius misses blends.
- shape     results/<zone>/shape_candidates.parquet (`python -m skyshift_pipeline.shape`), every row, clock months:
            stretched like two objects where Gaia and CatWISE list one (contracts/api.md v3.7). Absent file: none.
Zones are listed in the order given (hunt first). An object already listed from an earlier zone is not
listed again (the pilot lies inside the hunt zone and was redone with the same photos); the earlier entry's
`zones` gets the later zone (as suspects.json).
Movers carry three zone-level "check first" codes: outlier_flag, detector_excess, repeat_position.
Movers also carry the suspects' vetting (vet.py): `grade` (A/B/C; null for other kinds and ungraded zones) and the failed
gates as reason codes; grade C scores × MOVER_GRADE_C and the list puts movers in grade order, so C never leads.
Grade A (suspects.py: on a tracklets track over >= 2 sessions at >= 5σ) leads; its `why` names the track and it never
gets motion_not_significant (its own one-session dots may not show the motion; the track does).
`score` (0-1, higher = more promising) is defined per kind in mover_score, highpm_score and
spectral_score (formulas also in contracts/api.md). frames_url is set for candidates whose static frames
exist next to the output (cand/<id>/frames.json, written by scripts/candidates/frames.py).
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from skyshift_core import tangent_offset_arcsec
from skyshift_core.flags import BIT_NAMES, MASK_BITS, OUTLIER, RECORD_BITS

from . import config as C
from . import log
from .sky import SkyTree, angsep
from .spectra import NCH_DET, NDET
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("candidates")

KINDS = ("mover", "high_pm", "spectral", "shape")
CACHE_DIR: Path | None = None  # --cache: column reads kept on disk between runs
REPO =Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO / "web" / "public" / "data" / "candidates.json"

# --- selection --------------------------------------------------------------------------------
SPECTRAL_CAP = 300             # spectral: keep the most promising this many per zone
SPECTRAL_PAH_ONLY_CAP = 20     # ... of which rows labelled PAH 3.3 µm alone (galaxies; photoz's PAH at z: ~150 in the hunt)
NEAR_PARALLAX_MAS = 20.0       # spectral: a Gaia star this close (<= 50 pc) is kept despite the Gaia match
NEAR_PARALLAX_SNR = 5.0        # ... if its parallax is this significant
RED_J_M46_AB = C.BD_MIN_J_M46_AB             # label "red": J_AB - [4.6]_AB above this (classify's brown-dwarf cut)
RED_RATIO_MIN = 10 ** (0.4 * C.BD_MIN_J_M46_AB)  # the same cut as a D6 / D2 flux ratio (highpm red_ratio): 0.63
FLAG_SHARE_MIN = 0.25          # fixed sources: a record-only flag counts when set on this share of its dots
IMPLAUSIBLE_PM_MAS_YR = 12_000.0  # high_pm reason implausible_pm: Barnard's Star, the fastest known star, does 10.4″/yr
KNOWN_NEAR_ARCSEC = 120.0      # mover reason known_object_near
RATE_SIGMA_MIN = 3.0           # mover reason motion_not_significant: rate under this many sigma
MOVER_MATCH_RADIUS_ARCSEC = 15.0  # dots re-read around a mover's mean position
DEDUPE_ARCSEC = 4.0            # the same fixed source in two zones (link's friends-of-friends radius)
DEDUPE_MOVER_ARCSEC = 6.0      # the same mover in two zones: within 6″ and 2 minutes (suspects.json's merge)
DEDUPE_MOVER_DAYS = 2.0 / 1440.0
REPEAT_ARCSEC = 30.0           # mover reason repeat_position: another suspect of the zone this close, in another
REPEAT_MAX_RATE = 2.0          # session, at an implied rate under this (″/h): a fixed or very slow source
EXCESS_RATIO = 1.5             # mover reason detector_excess: in its (detector, visit) the zone's suspects per
EXCESS_MIN_SUSPECTS = 5        # mover are >= 1.5x the zone's average, with at least 5 suspects there
DEFAULT_DET = 2                # high_pm, spectral without a usable spectrum: contracts/api.md's default detector (v2)

# --- scores -------------------------------------------------------------------------------------
MOVER_SNR_SCALE = 15.0
MOVER_EVIDENCE = {"two_exposures": 1.0, "two_detectors": 0.6}
MOVER_TWO_DOTS = 0.85
MOVER_KNOWN_RANGE = (C.KNOWN_ARCSEC, 300.0)  # nearest known object: x0.5 at 30″ rising to x1 at 300″
MOVER_RECORD_FLAG = 0.7        # a record-only flag other than OUTLIER (ghost, halo, …)
MOVER_OUTLIER = 0.6            # outlier_flag
MOVER_EXCESS = 0.6             # detector_excess
MOVER_REPEAT = 0.3             # repeat_position
MOVER_BAD_PIXELS = 0.8
MOVER_NO_MOTION = 0.85
MOVER_GRADE_C = 0.1            # the suspects' vetting graded it C (a failed gate or a catalogue source within 6″)
MOVER_EDGE = 0.5               # a dot within VET_EDGE_PX of the detector edge (edge_dot), graded or not
GRADE_RANK = {"A": 0, "B": 1, "C": 2}  # list order inside each zone and kind; null (other kinds, ungraded) sorts with B
HIGHPM_SCORE_SCALE = 0.2       # quality = exp(-pipeline score / this); the pipeline score is lower = better
HIGHPM_REASON_WEIGHT = {"short_span": 0.5, "few_dots": 0.5, "bad_fit": 0.5, "over_pm_cap": 0.3,
                        "gaia_pm_disagrees": 0.4, "catwise_pm_disagrees": 0.4, "at_control_level": 0.3,
                        "implausible_pm": 0.01}  # implausible_pm: to the bottom of the list
HIGHPM_KNOWN_STAR = 0.6        # status "gaia" or "2mass" (highpm's deep check): a catalogued star
SPECTRAL_SIG_SCALE = 10.0
SPECTRAL_WEIGHT = {"brown_dwarf_like": 1.0, "ch4": 1.0, "h2o_ice": 0.8, "co2_ice": 0.8, "co_ice": 0.8,
                   "h2o_vapour": 0.6, "co_23": 0.4, "pah_33": 0.3}

# --- vocabulary (contracts/api.md) --------------------------------------------------------------
LABELS = ("red", "brown_dwarf_like", "ch4", "h2o_ice", "co2_ice", "co_ice", "h2o_vapour", "co_23", "pah_33")
MOLECULE_LABELS = LABELS[2:]
# band label -> the index columns behind it and their wavelengths (µm)
LABEL_BANDS = {
    "ch4": (("ch4_33", "3.3"), ("ch4_16", "1.6")),
    "h2o_ice": (("h2o_ice_305", "3.05"),),
    "co2_ice": (("co2_ice_427", "4.27"),),
    "co_ice": (("co_ice_467", "4.67"),),
    "h2o_vapour": (("h2o_14", "1.4"), ("h2o_19", "1.9")),
    "co_23": (("co_23", "2.3"),),
    "pah_33": (("pah_33", "3.3"),),
}
BD_BANDS = {"ch4_33": ("ch4", "3.3"), "ch4_16": ("ch4", "1.6"), "h2o_14": ("h2o_vapour", "1.4"),
            "h2o_19": ("h2o_vapour", "1.9")}
LABEL_TEXT = {"ch4": "methane", "h2o_ice": "water ice", "co2_ice": "CO₂ ice", "co_ice": "CO ice",
              "h2o_vapour": "water vapour", "co_23": "CO", "pah_33": "PAH emission"}
RECORD_FLAG_NAMES = {b: n.lower() for b, n in BIT_NAMES.items() if RECORD_BITS & (1 << b)}
REASONS = {
    "mover": ("two_exposures", "two_detectors", "known_object_near", "motion_not_significant", "bad_pixels_near",
              "outlier_flag", "detector_excess", "repeat_position", "gaia_unchecked", "catwise_unchecked",
              *C.VET_GATES),  # the suspects' failed vetting gates (vet.py, contracts/api.md suspects.json)
    "high_pm": ("pm_fit", "pm_pair", "vetted", "catwise_pm_agrees", "short_span", "few_dots", "bad_fit",
                "over_pm_cap", "gaia_pm_disagrees", "catwise_pm_disagrees", "at_control_level", "implausible_pm"),
    "spectral": ("no_gaia", "gaia_nearby", "class_star", "class_galaxy", "class_unsure", "variable"),
    "shape": ("elongated", "steady_pa", "one_visit", "one_catalogue_source", "class_galaxy"),
}
FIELDS = ("id", "kind", "ra", "dec", "mjd", "clock", "det", "zone", "zones", "score", "grade", "snr", "flux_ujy", "rate_arcsec_h",
          "pm_mas_yr", "pm_err_mas_yr", "pa_deg", "nearest_known_arcsec", "gaia_match", "catwise_match",
          "galactic_b", "labels", "reasons", "why", "flags", "frames_url")


# --- small helpers ------------------------------------------------------------------------------
def _num(x, nd: int):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def _clip01(x):
    return np.clip(np.asarray(x, np.float64), 0.0, 1.0)


def record_flag_names(flags: int) -> list[str]:
    """Names of the record-only bits (contracts/flags.md: OUTLIER, CROSSTALK, GHOST*, BLOOM, HALO) set in `flags`."""
    f = int(flags) & 0xFFFFFFFF
    return [n for b, n in sorted(RECORD_FLAG_NAMES.items()) if f & (1 << b)]


def galactic_b(ra, dec) -> np.ndarray:
    from astropy import units as u
    from astropy.coordinates import SkyCoord

    ra, dec = np.atleast_1d(np.asarray(ra, np.float64)), np.atleast_1d(np.asarray(dec, np.float64))
    if ra.size == 0:
        return np.zeros(0)
    return SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs").galactic.b.deg


def pa_deg(east, north) -> np.ndarray:
    """Position angle east of north, 0-360 degrees."""
    return np.mod(np.degrees(np.arctan2(np.asarray(east, np.float64), np.asarray(north, np.float64))), 360.0)


def _fmt_arcsec(x: float) -> str:
    return f"{x:.0f}″" if x >= 100 else f"{x:.1f}″" if x >= 10 else f"{x:.2f}″"


def _fmt_dist(arcsec: float) -> str:
    return f"{arcsec / 60.0:.1f}′" if arcsec >= 60 else f"{arcsec:.0f}″"


# --- reading the bucket ---------------------------------------------------------------------------
def read_columns(store: Store, rel: str, columns: list[str], ids: np.ndarray | None = None,
                 id_col: str = "source_id") -> pd.DataFrame:
    """Some columns of a parquet file, read with range requests (Store.read_parquet reads whole files).
    ids: keep only rows whose id_col is in it."""
    import hashlib

    import pyarrow.parquet as pq

    key = None
    if CACHE_DIR is not None:
        h = hashlib.sha1(f"{store.root}|{rel}|{','.join(columns)}".encode()).hexdigest()[:16]
        key = CACHE_DIR / f"{rel.replace('/', '__')}.{h}.parquet"
    if key is not None and key.exists():
        df = pd.read_parquet(key)
    else:
        with store.fs.open(store.path(rel), "rb") as f:
            pf = pq.ParquetFile(f)
            have = set(pf.schema_arrow.names)
            t = pf.read(columns=[c for c in columns if c in have])
        df = t.to_pandas()
        if key is not None:
            key.parent.mkdir(parents=True, exist_ok=True)
            tmp = key.with_suffix(f".tmp{id(df)}")
            t.to_pandas().to_parquet(tmp, index=False)
            tmp.replace(key)
    if ids is not None:
        df = df[df[id_col].isin(np.asarray(ids))]
    return df.reset_index(drop=True)


def finished_zones(store: Store) -> list[str]:
    """Zones with results/<zone>/suspects.parquet, largest box first (the hunt before the pilot inside it)."""
    zs = [z for z in C.ZONES.values() if store.exists(results_rel(z.name, "suspects.parquet"))]
    zs.sort(key=lambda z: -(z.ra_max - z.ra_min) * (z.dec_max - z.dec_min))
    return [z.name for z in zs]


def _pool_map(fn, items, workers: int):
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(fn, items))


def mover_dots(store: Store, susp: pd.DataFrame, workers: int = 32) -> pd.DataFrame:
    """Dots within MOVER_MATCH_RADIUS_ARCSEC of each suspect, from the photos it was seen in
    (its obs_ids x detectors). Columns: sid (suspect row), obs_id, detector, ra, dec, mjd, snr, pos_err_arcsec."""
    want: dict[tuple[str, int], list[int]] = {}
    for i, r in enumerate(susp.itertuples()):
        for o in str(r.obs_ids).split(","):
            for d in str(r.detectors).split(","):
                if o and d:
                    want.setdefault((o, int(d)), []).append(i)
    ra0, dec0 = susp["ra"].to_numpy(np.float64), susp["dec"].to_numpy(np.float64)

    def one(item):
        (o, d), rows = item
        try:
            df = read_columns(store, dots_rel(o, d), ["ra", "dec", "mjd", "snr", "pos_err_arcsec"])
        except FileNotFoundError:
            return None
        out = []
        for i in rows:
            sep = angsep(ra0[i], dec0[i], df["ra"].to_numpy(), df["dec"].to_numpy()) * 3600.0
            near = df[sep <= MOVER_MATCH_RADIUS_ARCSEC].assign(sid=i, obs_id=o, detector=d, sep=sep[sep <= MOVER_MATCH_RADIUS_ARCSEC])
            out.append(near)
        return pd.concat(out, ignore_index=True) if out else None

    parts = [p for p in _pool_map(one, list(want.items()), workers) if p is not None and len(p)]
    cols = ["sid", "obs_id", "detector", "ra", "dec", "mjd", "snr", "pos_err_arcsec", "sep"]
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
    return pd.concat(parts, ignore_index=True)[cols]


def match_mover(ra: float, dec: float, mjd: float, n_dots: int, near: pd.DataFrame, per_photo: int = 3) -> pd.DataFrame | None:
    """The mover's own dots: the choice of n_dots dots, at most one per photo, whose mean position and
    time equal the suspect's (link takes the plain mean). None if no choice matches."""
    if n_dots < 1 or len(near) < n_dots:
        return None
    near = near.reset_index(drop=True)
    groups = [g.nsmallest(per_photo, "sep").index.to_numpy() for _, g in near.groupby(["obs_id", "detector"], sort=True)]
    if len(groups) < n_dots:
        return None
    a_ra, a_dec, a_mjd = (near[c].to_numpy(np.float64) for c in ("ra", "dec", "mjd"))
    best, best_d = None, np.inf
    for photos in itertools.combinations(range(len(groups)), n_dots):
        for pick in itertools.product(*(groups[p] for p in photos)):
            k = np.asarray(pick)
            if abs(a_mjd[k].mean() - mjd) >= 1e-5:
                continue
            d = float(angsep(ra, dec, a_ra[k].mean(), a_dec[k].mean())) * 3600.0
            if d < best_d:
                best, best_d = k, d
    return near.iloc[best] if best is not None and best_d < 0.05 else None


def motion(dots: pd.DataFrame) -> tuple[float, float, float] | None:
    """(rate ″/h, its 1σ error, position angle east of north) from a weighted straight-line fit of
    position against time; None with fewer than two distinct times. Position errors are the dots' pos_err_arcsec ×
    POS_ERR_SCALE (raw pos_err is 1.4–1.8× too small against Gaia), 0.3″ × that where missing."""
    t = dots["mjd"].to_numpy(np.float64) * 24.0
    if len(np.unique(np.round(t, 6))) < 2:
        return None
    e, n = tangent_offset_arcsec(dots["ra"].mean(), dots["dec"].mean(), dots["ra"].to_numpy(), dots["dec"].to_numpy())
    err = dots["pos_err_arcsec"].to_numpy(np.float64) if "pos_err_arcsec" in dots else np.full(len(dots), 0.3)
    err = np.where(np.isfinite(err) & (err > 0), err, 0.3) * C.POS_ERR_SCALE
    w = 1.0 / err**2
    tm = np.sum(w * t) / np.sum(w)
    stt = np.sum(w * (t - tm) ** 2)
    ve = np.sum(w * (t - tm) * (e - np.sum(w * e) / np.sum(w))) / stt
    vn = np.sum(w * (t - tm) * (n - np.sum(w * n) / np.sum(w))) / stt
    sig = 1.0 / math.sqrt(stt)
    return float(math.hypot(ve, vn)), sig, float(pa_deg(ve, vn))


def source_stats(store: Store, zone: str, ids: np.ndarray) -> pd.DataFrame:
    """Per source: best detector by S/N of its spectrum (channels' S/N added in quadrature), that S/N, the
    median channel flux there (µJy), saturated, variable. A source with no channel of positive, finite S/N in
    any detector has no best detector: det, snr and flux_ujy are None (hunt, 14 Nov: 65 high_pm rows had been
    written as D1, S/N 0, the argmax of six zeros)."""
    src = read_columns(store, results_rel(zone, "sources.parquet"),
                       ["source_id", "flux_ujy", "err_ujy", "saturated", "variable"], ids)
    if not len(src):
        return pd.DataFrame(columns=["source_id", "det", "snr", "flux_ujy", "saturated", "variable"])
    f = np.stack(src["flux_ujy"].to_numpy()).astype(np.float64).reshape(len(src), NDET, NCH_DET)
    e = np.stack(src["err_ujy"].to_numpy()).astype(np.float64).reshape(len(src), NDET, NCH_DET)
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.where(np.isfinite(f) & np.isfinite(e) & (e > 0), f / e, 0.0)
        snr = np.sqrt(np.sum(np.where(s > 0, s, 0.0) ** 2, axis=2))
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            flux = np.nanmedian(f, axis=2)
    best = np.argmax(snr, axis=1)
    k = np.arange(len(src))
    ok = snr[k, best] > 0
    det = pd.array(best + 1, dtype="Int64")
    det[~ok] = pd.NA
    return pd.DataFrame({"source_id": src["source_id"].to_numpy(np.int64), "det": det,
                         "snr": np.where(ok, snr[k, best], np.nan), "flux_ujy": np.where(ok, flux[k, best], np.nan),
                         "saturated": src["saturated"].to_numpy(bool),
                         "variable": src["variable"].to_numpy(bool) if "variable" in src else False})


def _opt(x, kind=float):
    """kind(x), or None for a missing value (None, NaN, pd.NA)."""
    return None if x is None or pd.isna(x) else kind(x)


def member_info(store: Store, zone: str, ids: np.ndarray, workers: int = 32) -> pd.DataFrame:
    """Per source: mean MJD of its dots (photo list) and the OR / share of record-only flag bits over its
    dots (dot files' `flags`, the 3×3 OR around each dot). Columns: source_id, mjd, n_dots, flag_<bit> share."""
    m = read_columns(store, results_rel(zone, "members.parquet"), ["source_id", "obs_id", "detector", "dot_row"], ids)
    p = read_columns(store, photolist_rel(zone), ["obs_id", "detector", "mjd"])
    p["detector"] = p["detector"].astype(np.int64)
    m["detector"] = m["detector"].astype(np.int64)
    m = m.merge(p, on=["obs_id", "detector"], how="left")
    files = m.groupby(["obs_id", "detector"], sort=False).indices

    def one(item):
        (o, d), idx = item
        try:
            fl = read_columns(store, dots_rel(o, int(d)), ["flags"])["flags"].to_numpy(np.int64) & 0xFFFFFFFF
        except FileNotFoundError:
            return idx, np.zeros(len(idx), np.int64)
        rows = m["dot_row"].to_numpy(np.int64)[idx]
        ok = rows < len(fl)
        out = np.zeros(len(idx), np.int64)
        out[ok] = fl[rows[ok]]
        return idx, out

    flags = np.zeros(len(m), np.int64)
    for idx, v in _pool_map(one, list(files.items()), workers):
        flags[idx] = v
    m["flags"] = flags
    g = m.groupby("source_id")
    out = pd.DataFrame({"mjd": g["mjd"].mean(), "n_dots": g.size()})
    for b in RECORD_FLAG_NAMES:
        out[f"flag_{b}"] = pd.Series(((m["flags"].to_numpy() >> b) & 1).astype(np.float64)).groupby(m["source_id"].to_numpy()).mean()
    return out.reset_index()


def fixed_flag_names(info_row: pd.Series | dict) -> list[str]:
    return [n for b, n in sorted(RECORD_FLAG_NAMES.items()) if (info_row.get(f"flag_{b}") or 0) >= FLAG_SHARE_MIN]


# --- movers ----------------------------------------------------------------------------------------
def mover_score(snr, n_dots, evidence, nearest_known, flags, no_motion, excess=None, repeat=None, edge=None) -> np.ndarray:
    """score = S/N term x evidence x known-object term x caution terms (contracts/api.md):
    (1 - exp(-snr / 15)) x {two_exposures 1, two_detectors 0.6} x (0.85 with only two dots)
    x (1 if no known object within 600″, else 0.5 rising linearly to 1 between 30″ and 300″)
    x 0.6 if OUTLIER x 0.7 if another record-only flag (ghost, halo, …) x 0.8 if a masked pixel touches a dot
    x 0.85 if the motion is not significant x 0.6 if detector_excess x 0.3 if repeat_position
    x 0.5 if a dot is within VET_EDGE_PX of the detector edge (edge_dot)."""
    snr = np.asarray(snr, np.float64)
    s = 1.0 - np.exp(-np.maximum(snr, 0.0) / MOVER_SNR_SCALE)
    s = s * np.array([MOVER_EVIDENCE.get(e, 0.5) for e in evidence])
    s = s * np.where(np.asarray(n_dots) <= 2, MOVER_TWO_DOTS, 1.0)
    d = np.asarray(nearest_known, np.float64)
    lo, hi = MOVER_KNOWN_RANGE
    k = np.where(np.isfinite(d), 0.5 + 0.5 * np.clip((d - lo) / (hi - lo), 0.0, 1.0), 1.0)
    f = np.asarray(flags, np.int64) & 0xFFFFFFFF
    s = s * k * np.where((f & OUTLIER) != 0, MOVER_OUTLIER, 1.0)
    s = s * np.where((f & RECORD_BITS & ~OUTLIER) != 0, MOVER_RECORD_FLAG, 1.0) * np.where((f & MASK_BITS) != 0, MOVER_BAD_PIXELS, 1.0)
    s = s * np.where(np.asarray(no_motion, bool), MOVER_NO_MOTION, 1.0)
    n = len(s)
    s = s * np.where(np.asarray(excess if excess is not None else [False] * n, bool), MOVER_EXCESS, 1.0)
    s = s * np.where(np.asarray(repeat if repeat is not None else [False] * n, bool), MOVER_REPEAT, 1.0)
    s = s * np.where(np.asarray(edge if edge is not None else [False] * n, bool), MOVER_EDGE, 1.0)
    return _clip01(s)


def edge_dot(r) -> bool:
    """A suspect with a dot within VET_EDGE_PX of the detector edge (its dot lists dot_x, dot_y; False without them):
    the vetting's `edge` gate, also for zones graded before it or not graded at all."""
    x, y = getattr(r, "dot_x", None), getattr(r, "dot_y", None)
    if x is None or y is None or isinstance(x, float) or isinstance(y, float):
        return False
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    if not x.size or x.size != y.size:
        return False
    hi = C.VET_DETECTOR_PX - 1 - C.VET_EDGE_PX
    ok = np.isfinite(x) & np.isfinite(y)
    return bool(np.any(ok & ((x < C.VET_EDGE_PX) | (x > hi) | (y < C.VET_EDGE_PX) | (y > hi))))


def excess_cells(susp: pd.DataFrame, mov: pd.DataFrame | None) -> dict[tuple[int, int], float]:
    """(detector, visit) cells where the zone's suspects per mover are >= EXCESS_RATIO x the zone's average, with
    at least EXCESS_MIN_SUSPECTS suspects: {(det, visit): ratio}. Cells by each mover's `det`.
    Hunt (14 Nov): D4 1.6x, D5 4.9x, D6 2.6x, all in visit 2 (D4 is 41% of the suspects, 28% of the movers)."""
    if mov is None or not len(mov) or not len(susp):
        return {}
    ns = susp.groupby([susp["det"].astype(int), susp["visit"].astype(int)]).size()
    nm = mov.groupby([mov["det"].astype(int), mov["visit"].astype(int)]).size()
    avg = len(susp) / len(mov)
    out = {}
    for cell, n in ns.items():
        m = int(nm.get(cell, 0))
        if n >= EXCESS_MIN_SUSPECTS and m > 0 and n / m >= EXCESS_RATIO * avg:
            out[(int(cell[0]), int(cell[1]))] = float(n / m / avg)
    return out


def is_excess(dets: list[int], visit: int, cells: dict[tuple[int, int], float]) -> bool:
    """detector_excess: every detector the mover was seen in is an excess cell in its visit (a D1+D4 pair seen
    by D1 too is not)."""
    return bool(dets) and all((d, int(visit)) in cells for d in dets)


def repeat_positions(susp: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """repeat_position: suspects with another suspect of the zone within REPEAT_ARCSEC, in another session, at
    an implied rate (separation / time apart) under REPEAT_MAX_RATE: {id: (separation ″, days apart)} of the
    closest such partner. Hunt (14 Nov): 7 suspects at 3 places, days to months apart."""
    if len(susp) < 2:
        return {}
    ra, dec = susp["ra"].to_numpy(np.float64), susp["dec"].to_numpy(np.float64)
    mjd, sess, ids = susp["mjd"].to_numpy(np.float64), susp["session"].to_numpy(), susp["id"].astype(str).to_numpy()
    out: dict[str, tuple[float, float]] = {}
    for i, js in enumerate(SkyTree(ra, dec).query_radius(ra, dec, REPEAT_ARCSEC)):
        best = None
        for j in js:
            if j == i or sess[j] == sess[i]:
                continue
            sep = float(angsep(ra[i], dec[i], ra[j], dec[j])) * 3600.0
            dt_d = abs(mjd[j] - mjd[i])
            if dt_d > 0 and sep / (dt_d * 24.0) < REPEAT_MAX_RATE and (best is None or sep < best[0]):
                best = (sep, dt_d)
        if best is not None:
            out[ids[i]] = best
    return out


def _fmt_days(d: float) -> str:
    return f"{d * 24:.0f} hours" if d < 1.0 else f"{d:.1f} days"


def mover_track(r) -> tuple[int, float, float] | None:
    """(sessions, rate ″/h, rate / σ) of the tracklets track a grade A suspect is on (suspects.parquet's track columns);
    None for other grades or without them."""
    if getattr(r, "grade", None) != "A":
        return None
    n, rate, sig = getattr(r, "n_sessions", None), getattr(r, "rate_arcsec_h", None), getattr(r, "rate_sigma", None)
    if n is None or rate is None or pd.isna(n) or pd.isna(rate):
        return None
    snr = float(rate) / float(sig) if sig is not None and pd.notna(sig) and float(sig) > 0 else math.inf
    return int(n), float(rate), snr


VET_TEXT = {"not_vetted": "it could not be vetted (no data on its dots)", "hot_pixel": "a dot sits on a hot pixel",
            "bad_column": "a dot sits on a bad detector column", "edge": "a dot is at the detector's edge",
            "masked_peak": "a dot has a masked pixel at its peak",
            "sharp_outlier": "a dot is sharper than a star (a cosmic ray?)", "very_sharp": "a dot is sharper than any star",
            "flux_ratio": "its brightness jumps more than 4× in one detector",
            "red_photo": "a dot comes from a photo that failed our quality check",
            "2mass": "a 2MASS source lies within 6″", "allwise": "an AllWISE source lies within 6″"}


def vet_text(grade: str | None, vet_reasons: list[str]) -> str:
    """The vetting's verdict in words, put first in `why`: grade C with its failed gates; a gate found here on a mover the
    vetting passed or never saw (edge_dot) as a caution; "" otherwise."""
    if grade != "C" and not vet_reasons:
        return ""
    gates = [VET_TEXT.get(x, x.replace("_", " ")) for x in vet_reasons]
    head = "Failed the vetting, most likely an artefact or a catalogued source" if grade == "C" else "Possibly an artefact"
    return f"{head}: {'; '.join(gates)}. " if gates else f"{head}. "


def mover_why(evidence: str, n_exposures: int, span_min: float | None, dets: list[int], rate: float | None,
              no_motion: bool, nearest_known: float | None, repeat: tuple[float, float] | None = None,
              track: tuple[int, float, float] | None = None, grade: str | None = None,
              vet_reasons: list[str] | None = None) -> str:
    if evidence == "two_detectors":
        seen = f"Seen by detectors D{dets[0]} and D{dets[-1]} at the same moment, in one exposure only"
    else:
        span = (f" {span_min:.1f} minutes apart" if n_exposures == 2 else f" over {span_min:.1f} minutes") if span_min else ""
        seen = f"Seen at different places in {n_exposures} exposures{span}"
        if rate is not None:
            seen += (f", moving about {rate:.0f}″ an hour" if not no_motion
                     else f" (about {rate:.0f}″ an hour, not clearly more than its position errors)")
    if track is not None:
        n, trate, snr = track
        sig = f", {snr:.0f}σ" if math.isfinite(snr) else ""
        seen += f"; followed over {n} sessions of its visit at {trate:.1f}″ an hour{sig}"
    if nearest_known is None or not math.isfinite(nearest_known):
        known = "no known asteroid or comet was predicted within 10′"
    else:
        known = f"the nearest known asteroid or comet was predicted {_fmt_dist(nearest_known)} away"
    if repeat is not None:
        known += (f", but another unexplained mover was seen {_fmt_dist(repeat[0])} from here {_fmt_days(repeat[1])} "
                  "apart, so it may be a fixed source")
    return f"{vet_text(grade, list(vet_reasons or []))}{seen}; {known}."


def movers(susp: pd.DataFrame, zone: str, near: pd.DataFrame | None,
           cells: dict[tuple[int, int], float] | None = None, repeats: dict[str, tuple[float, float]] | None = None) -> list[dict]:
    """Mover candidates from suspects. `cells` (excess_cells) and `repeats` (repeat_positions) come from the
    whole zone, so they stay right when `susp` is a subset."""
    cells, repeats = cells or {}, repeats or {}
    out = []
    for i, r in enumerate(susp.itertuples()):
        dets = sorted(int(x) for x in str(r.detectors).split(",") if x)
        dots = None
        if near is not None and len(near):
            dots = match_mover(r.ra, r.dec, r.mjd, int(r.n_dots), near[near["sid"] == i])
        mo = motion(dots) if dots is not None else None
        rate, rate_err, pa = mo if mo else (None, None, None)
        track = mover_track(r)  # grade A: its motion is shown over sessions, whatever its own dots say
        no_motion = bool(track is None and mo is not None and rate < RATE_SIGMA_MIN * rate_err)
        span_min = float((dots["mjd"].max() - dots["mjd"].min()) * 1440.0) if dots is not None else None
        nk = float(r.nearest_known_arcsec) if pd.notna(r.nearest_known_arcsec) else None
        f = int(r.flags) & 0xFFFFFFFF
        reasons = [r.evidence]
        if nk is not None and nk <= KNOWN_NEAR_ARCSEC:
            reasons.append("known_object_near")
        if no_motion:
            reasons.append("motion_not_significant")
        if f & MASK_BITS:
            reasons.append("bad_pixels_near")
        if f & OUTLIER:
            reasons.append("outlier_flag")
        excess = is_excess(dets, int(getattr(r, "visit", 0) or 0), cells)
        if excess:
            reasons.append("detector_excess")
        rep_ = repeats.get(str(r.id))
        if rep_ is not None:
            reasons.append("repeat_position")
        if not getattr(r, "gaia_checked", True):
            reasons.append("gaia_unchecked")
        if not getattr(r, "catwise_checked", True):
            reasons.append("catwise_unchecked")
        grade = getattr(r, "grade", None)
        grade = grade if isinstance(grade, str) and grade else None
        vet_reasons = getattr(r, "reasons", None)
        vet_reasons = [str(x) for x in (vet_reasons if vet_reasons is not None and not isinstance(vet_reasons, float) else [])]
        edge = edge_dot(r)
        if edge and "edge" not in vet_reasons:
            vet_reasons.append("edge")
        reasons += vet_reasons
        score = float(mover_score([r.snr], [r.n_dots], [r.evidence], [nk if nk is not None else np.nan], [f], [no_motion],
                                  [excess], [rep_ is not None], [edge])[0]) * (MOVER_GRADE_C if grade == "C" else 1.0)
        out.append({
            "id": r.id, "kind": "mover", "ra": r.ra, "dec": r.dec, "mjd": r.mjd, "clock": "hours", "det": int(r.det),
            "zone": zone, "zones": [zone], "score": score, "grade": grade, "snr": r.snr, "flux_ujy": r.flux_ujy, "rate_arcsec_h": rate,
            "pm_mas_yr": None, "pm_err_mas_yr": None, "pa_deg": pa, "nearest_known_arcsec": nk,
            "gaia_match": False, "catwise_match": False, "labels": [], "reasons": reasons,
            "why": mover_why(r.evidence, int(r.n_exposures), span_min, dets, rate, no_motion, nk, rep_, track, grade,
                             vet_reasons),
            "flags": record_flag_names(f), "_dets": dets,
        })
    return out


# --- high proper motion ----------------------------------------------------------------------------
def highpm_leads(h: pd.DataFrame) -> pd.DataFrame:
    """The highpm rows worth a person's time: `vetted` only. The others are proven or likely fakes: a Gaia star
    whose own motion (with its parallax) is not ours, a CatWISE motion that isn't ours, pairs at the chance level,
    too few sessions, nothing to check an under-10σ motion against (highpm.py). Hunt, 14 Nov tables: 997 rows,
    748 of them gaia_pm_disagrees; Gaia still stars with Gaia hidden show what the unvetted rows are."""
    if not len(h) or "vetted" not in h:
        return h
    return h[h["vetted"].fillna(False).to_numpy(bool)].reset_index(drop=True)


def highpm_reasons(r) -> list[str]:
    out = ["pm_pair" if r.method == "pair" else "pm_fit"]
    if bool(r.vetted):
        out.append("vetted")
    if bool(getattr(r, "catwise_pm_agree", False)):
        out.append("catwise_pm_agrees")
    out += [x for x in str(r.reason or "").split(",") if x]
    if pd.notna(r.pm) and r.pm > IMPLAUSIBLE_PM_MAS_YR:
        out.append("implausible_pm")
    return out


def highpm_score(pipeline_score, reasons: list[list[str]], status) -> np.ndarray:
    """score = exp(-pipeline score / 0.2) x product of the vetting reasons' weights x 0.6 for a Gaia star.
    The pipeline score is lower = better: 1 / (pm / pm_err) for a fit, |ln flux ratio| + spectrum-shape rms
    for a pair. Weights: short_span, few_dots, bad_fit 0.5; over_pm_cap, at_control_level 0.3;
    gaia_pm_disagrees, catwise_pm_disagrees 0.4; implausible_pm 0.01 (always last)."""
    q = np.exp(-np.nan_to_num(np.asarray(pipeline_score, np.float64), nan=1.0) / HIGHPM_SCORE_SCALE)
    w = np.array([np.prod([HIGHPM_REASON_WEIGHT.get(x, 1.0) for x in rs]) if rs else 1.0 for rs in reasons])
    known = np.where(np.isin(np.asarray(status, dtype=object), ["gaia", "2mass"]), HIGHPM_KNOWN_STAR, 1.0)
    return _clip01(q * w * known)


def _pm_text(pm_mas_yr: float) -> str:
    x = pm_mas_yr / 1000.0
    return f"{x:.0f}″" if x >= 100 else f"{x:.1f}″" if x >= 10 else f"{x:.2f}″"


CAUTION_TEXT = {
    "at_control_level": "as many such pairs turn up by chance",
    "over_pm_cap": "it is faster than any known star",
    "bad_fit": "the positions don't fit a straight line well",
    "few_dots": "it has few detections",
    "short_span": "its visits are too close in time",
}


def highpm_why(r, reasons: list[str]) -> str:
    move = (f"Moves {_pm_text(r.pm)} a year between visits" if r.method == "fit"
            else f"Seen {_fmt_arcsec(float(r.sep_arcsec))} apart in two visits ({_pm_text(r.pm)} a year), with matching brightness")
    if "implausible_pm" in reasons:
        return (move + ", but no star moves that fast (Barnard's Star, the fastest known, does 10.4″ a year): "
                "almost certainly two different sources.")
    if "gaia_pm_disagrees" in reasons:
        tail = ", but Gaia lists a different motion for the star here — likely a mismatch, worth a look"
    elif "catwise_pm_disagrees" in reasons:
        tail = ", but CatWISE lists a different motion for the source here — likely a mismatch, worth a look"
    elif r.status == "gaia":
        tail = ("; Gaia lists this star with the same motion, a known nearby star that shows the method works"
                if "vetted" in reasons else "; Gaia lists a star here")
    elif r.status == "2mass":
        tail = "; 2MASS saw a star where this motion puts it in 2000, so it is a known star"
    else:
        tail = "; CatWISE lists the same motion" if "catwise_pm_agrees" in reasons else "; no catalogue lists this motion"
    cautions = [CAUTION_TEXT[x] for x in ("at_control_level", "over_pm_cap", "bad_fit", "few_dots", "short_span") if x in reasons]
    if cautions and "gaia_pm_disagrees" not in reasons and "catwise_pm_disagrees" not in reasons:
        tail += f", but {cautions[0]}"
    return move + tail + "."


def high_pms(h: pd.DataFrame, zone: str, cls_labels: dict[int, list[str]]) -> list[dict]:
    reasons = [highpm_reasons(r) for r in h.itertuples()]
    vet = [[x for x in rs if x in HIGHPM_REASON_WEIGHT] for rs in reasons]
    score = highpm_score(h["score"].to_numpy(), vet, h["status"].to_numpy()) if len(h) else np.zeros(0)
    out = []
    for r, rs, sc in zip(h.itertuples(), reasons, score):
        sid = int(r.source_id)
        cid = f"{zone}-pm-{sid}" if r.method != "pair" else f"{zone}-pm-{sid}-{int(r.source_id_b)}"
        labels = []
        if pd.notna(r.red_ratio) and r.red_ratio >= RED_RATIO_MIN:
            labels.append("red")
        labels += [x for x in cls_labels.get(sid, []) if x not in labels]
        out.append({
            "id": cid, "kind": "high_pm", "ra": r.ra, "dec": r.dec, "mjd": r.mjd, "clock": "months", "det": None,
            "zone": zone, "zones": [zone], "score": float(sc), "snr": None, "flux_ujy": None, "rate_arcsec_h": None,
            "pm_mas_yr": r.pm, "pm_err_mas_yr": r.pm_err, "pa_deg": float(pa_deg(r.pmra, r.pmdec)),
            "nearest_known_arcsec": None, "gaia_match": r.status == "gaia", "catwise_match": bool(str(r.catwise_name or "")),
            "labels": sorted(labels, key=LABELS.index), "reasons": rs, "why": highpm_why(r, rs), "flags": [],
            "_sources": [sid] + ([int(r.source_id_b)] if r.method == "pair" and r.source_id_b >= 0 else []),
        })
    return out


# --- spectral ----------------------------------------------------------------------------------------
def class_labels(c: pd.DataFrame) -> list[list[str]]:
    """Label list per classes row: molecule labels (label_*), brown_dwarf_like, red."""
    out = []
    lab = c["labels"].fillna("").astype(str).to_numpy() if "labels" in c else np.full(len(c), "")
    bd = c["brown_dwarf_like"].to_numpy(bool) if "brown_dwarf_like" in c else np.zeros(len(c), bool)
    with np.errstate(invalid="ignore"):
        red = (c["j_m46_ab"].to_numpy(np.float64) > RED_J_M46_AB) if "j_m46_ab" in c else np.zeros(len(c), bool)
    for s, b, rd in zip(lab, bd, red):
        ls = [x for x in s.split(",") if x in MOLECULE_LABELS]
        if b:
            ls.append("brown_dwarf_like")
        if rd:
            ls.append("red")
        out.append(sorted(set(ls), key=LABELS.index))
    return out


def _col(c: pd.DataFrame, name: str) -> np.ndarray:
    return c[name].to_numpy(np.float64) if name in c else np.full(len(c), np.nan)


def band_sigs(c: pd.DataFrame) -> dict[str, np.ndarray]:
    """Per label, the strongest sig of the bands behind it (NaN where the label is off)."""
    import warnings

    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows: the label is off there anyway
        for lab, bands in LABEL_BANDS.items():
            on = c[f"label_{lab}"].to_numpy(bool) if f"label_{lab}" in c else np.zeros(len(c), bool)
            sig = np.nanmax(np.stack([_col(c, f"{b}_sig") for b, _ in bands]), axis=0) if len(c) else np.zeros(0)
            out[lab] = np.where(on, sig, np.nan)
        bd = c["brown_dwarf_like"].to_numpy(bool) if "brown_dwarf_like" in c else np.zeros(len(c), bool)
        ev = c["bd_evidence"].fillna("").astype(str).to_numpy() if "bd_evidence" in c else np.full(len(c), "")
        sig = np.full(len(c), np.nan)
        for i in np.flatnonzero(bd):
            s = [_col(c.iloc[[i]], f"{b}_sig")[0] for b in ev[i].split(",") if b]
            sig[i] = np.nanmax(s) if s else np.nan
    out["brown_dwarf_like"] = np.where(bd, sig, np.nan)
    return out


def spectral_score(c: pd.DataFrame) -> np.ndarray:
    """score = max over the source's labels of weight x (1 - exp(-sig / 10)); weights: brown_dwarf_like and
    methane 1, ices 0.8, water vapour 0.6, CO 2.3 µm 0.4, PAH 0.3 (sig = the band's significance, σ)."""
    sigs = band_sigs(c)
    best = np.zeros(len(c))
    for lab, s in sigs.items():
        v = SPECTRAL_WEIGHT[lab] * (1.0 - np.exp(-np.nan_to_num(s, nan=0.0) / SPECTRAL_SIG_SCALE))
        best = np.maximum(best, np.where(np.isfinite(s), v, 0.0))
    return _clip01(best)


def clean_labels(c: pd.DataFrame, pz: pd.DataFrame | None) -> pd.DataFrame:
    """classes rows with the export's label rules applied to label_* and `labels` (module docstring). pz: photoz.parquet's
    source_id, z_best, good, label_pah_33_z (None: no photo-z for the zone, so no PAH label)."""
    from .classify import LABELS as CLASSIFY_LABELS

    c = c.copy()
    n = len(c)
    if not n:
        return c
    good, zb, pah_z = np.zeros(n, bool), np.full(n, np.nan), np.zeros(n, bool)
    if pz is not None and len(pz):
        p = pz.drop_duplicates("source_id").set_index("source_id").reindex(c["source_id"].to_numpy(np.int64))
        good = p["good"].fillna(False).to_numpy(bool)
        zb = p["z_best"].to_numpy(np.float64)
        if "label_pah_33_z" in p:
            pah_z = p["label_pah_33_z"].fillna(False).to_numpy(bool)

    def lab(name):
        return c[f"label_{name}"].fillna(False).to_numpy(bool) if f"label_{name}" in c else np.zeros(n, bool)

    with np.errstate(invalid="ignore"):
        c["label_pah_33"] = pah_z | (lab("pah_33") & good & (zb < C.PHOTOZ_PAH_Z_MAX))
        for ice in ("h2o_ice", "co2_ice", "co_ice"):
            c[f"label_{ice}"] = lab(ice) & ~good
        has_gaia = np.isfinite(_col(c, "gaia_sep_arcsec")) | np.isfinite(_col(c, "gaia_g"))
        mdwarf = has_gaia & (_col(c, "gaia_bp_rp") >= C.LABEL_MDWARF_BP_RP)
    for x in C.LABEL_MDWARF_EXPECTED:
        c[f"label_{x}"] = lab(x) & ~mdwarf
    on = np.stack([lab(x) for x in CLASSIFY_LABELS], 1)
    names = np.array(CLASSIFY_LABELS)
    c["labels"] = [",".join(names[b]) for b in on]
    return c


def gaia_blends(store: Store, zone: str, ids: np.ndarray) -> np.ndarray:
    """The source_ids (of `ids`) with a Gaia DR3 star brighter than BLEND_MAX_G within BLEND_ARCSEC at the source's
    epoch (crossids epoch_mjd, else the photo list's median): refcat/<zone>.parquet moved with its proper motion."""
    from .crossid import ref_offset_at

    ids = np.unique(np.asarray(ids, np.int64))
    rel = next((r.format(zone=zone) for r in (C.REFCAT_REL, C.REFCAT_FALLBACK_REL) if store.exists(r.format(zone=zone))), None)
    if not ids.size or rel is None:
        return np.zeros(0, np.int64)
    ref = read_columns(store, rel, ["cat", "ra", "dec", "epoch_mjd", "pmra", "pmdec", "g", "g_mag", "phot_g_mean_mag"])
    g = next((pd.to_numeric(ref[k], errors="coerce").to_numpy(np.float64) for k in ("g", "g_mag", "phot_g_mean_mag")
              if k in ref), np.full(len(ref), np.nan))
    cat = ref["cat"].astype(str).str.lower() if "cat" in ref else pd.Series("", index=ref.index)
    with np.errstate(invalid="ignore"):
        ref = ref[cat.str.contains("gaia").to_numpy() & (g < C.BLEND_MAX_G)].reset_index(drop=True)
    if not len(ref):
        return np.zeros(0, np.int64)
    for k in ("pmra", "pmdec"):
        ref[k] = pd.to_numeric(ref[k], errors="coerce") if k in ref else np.nan
    ref["epoch_mjd"] = pd.to_numeric(ref["epoch_mjd"], errors="coerce").fillna(57388.5) if "epoch_mjd" in ref else 57388.5
    pos = read_columns(store, results_rel(zone, "sources.parquet"), ["source_id", "ra", "dec"], ids)
    xrel = results_rel(zone, "crossids.parquet")
    ep = (read_columns(store, xrel, ["source_id", "epoch_mjd"], ids).drop_duplicates("source_id").set_index("source_id")["epoch_mjd"]
          if store.exists(xrel) else pd.Series(dtype=np.float64))
    mjd = ep.reindex(pos["source_id"].to_numpy(np.int64)).to_numpy(np.float64)
    if np.isnan(mjd).any():
        fill = float(np.nanmedian(read_columns(store, photolist_rel(zone), ["mjd"])["mjd"])) if store.exists(photolist_rel(zone)) else 61000.0
        mjd = np.where(np.isnan(mjd), fill, mjd)
    span = np.nanmax(np.abs(mjd[:, None] - np.array([np.nanmin(ref["epoch_mjd"]), np.nanmax(ref["epoch_mjd"])])[None])) / 365.25
    pm_max = float(np.nanmax(np.hypot(np.nan_to_num(ref["pmra"].to_numpy(np.float64)), np.nan_to_num(ref["pmdec"].to_numpy(np.float64))))) / 1000.0
    tree = SkyTree(ref["ra"].to_numpy(np.float64), ref["dec"].to_numpy(np.float64))
    near = tree.query_radius(pos["ra"].to_numpy(np.float64), pos["dec"].to_numpy(np.float64),
                             C.BLEND_ARCSEC + 1.0 + pm_max * float(span))
    out = []
    for i, js in enumerate(near):
        if not js:
            continue
        sub = ref.iloc[js]
        e, nn = ref_offset_at(float(pos["ra"].iloc[i]), float(pos["dec"].iloc[i]), sub, mjd[i], parallax=False)
        if (np.hypot(e, nn) <= C.BLEND_ARCSEC).any():
            out.append(int(pos["source_id"].iloc[i]))
    return np.asarray(out, np.int64)


def select_spectral(c: pd.DataFrame, cap: int = SPECTRAL_CAP, blended: np.ndarray | None = None) -> pd.DataFrame:
    """Labelled or brown_dwarf_like, and no Gaia star (or a near one); the `cap` best by score, at most
    SPECTRAL_PAH_ONLY_CAP of them labelled PAH alone (star-forming galaxies, not the hunt's target). blended: source_ids
    with a Gaia star within BLEND_ARCSEC (gaia_blends): they count as matched to it."""
    labelled = (c["labels"].fillna("").astype(str) != "").to_numpy() if "labels" in c else np.zeros(len(c), bool)
    if "brown_dwarf_like" in c:
        labelled = labelled | c["brown_dwarf_like"].to_numpy(bool)
    has_gaia = np.isfinite(_col(c, "gaia_sep_arcsec")) | np.isfinite(_col(c, "gaia_g"))
    if blended is not None and len(blended):
        has_gaia = has_gaia | c["source_id"].isin(blended).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        plx = _col(c, "gaia_parallax")
        near = (plx >= NEAR_PARALLAX_MAS) & (plx / _col(c, "gaia_parallax_error") >= NEAR_PARALLAX_SNR)
    s = c[labelled & (~has_gaia | near)].copy()
    s["_score"] = spectral_score(s)
    s = s.sort_values(["_score", "source_id"], ascending=[False, True])
    pah_only = (s["labels"].fillna("").astype(str) == "pah_33").to_numpy() if "labels" in s else np.zeros(len(s), bool)
    if "brown_dwarf_like" in s:
        pah_only = pah_only & ~s["brown_dwarf_like"].to_numpy(bool)
    over = pah_only & (np.cumsum(pah_only) > SPECTRAL_PAH_ONLY_CAP)
    return s[~over].head(cap).reset_index(drop=True)


def _band_phrase(lab: str, row) -> str:
    bands = LABEL_BANDS[lab]
    sig = [(um, row.get(f"{b}_sig", np.nan)) for b, um in bands]
    fired = [um for um, s in sig if s is not None and np.isfinite(s) and s >= C.INDEX_SIG] or [max(sig, key=lambda x: np.nan_to_num(x[1], nan=-1))[0]]
    return f"{LABEL_TEXT[lab]} at {'/'.join(fired)} µm"


def spectral_why(row: dict, labels: list[str]) -> str:
    mols = [x for x in labels if x in MOLECULE_LABELS]
    if not mols and row.get("brown_dwarf_like"):
        mols_txt = ", ".join(f"{LABEL_TEXT[BD_BANDS[b][0]]} at {BD_BANDS[b][1]} µm"
                             for b in str(row.get("bd_evidence") or "").split(",") if b in BD_BANDS) or "molecule bands"
    else:
        mols_txt = " and ".join(_band_phrase(x, row) for x in mols) or "molecule bands"
    sig = [row.get(f"{b}_sig") for lab in mols for b, _ in LABEL_BANDS[lab]]
    sig += [row.get(f"{b}_sig") for b in str(row.get("bd_evidence") or "").split(",") if b]
    sig = [s for s in sig if s is not None and np.isfinite(s)]
    sig_txt = f" ({max(sig):.0f}σ)" if sig else ""
    cls = row.get("class")
    where = "an extended source (likely a galaxy)" if cls == "galaxy" else "a point source"
    plx = row.get("gaia_parallax")
    gaia = (f" that Gaia puts {1000.0 / plx:.0f} pc away" if row.get("_near") and plx and np.isfinite(plx)
            else " with no Gaia star")
    text = f"{mols_txt[0].upper()}{mols_txt[1:]}{sig_txt} in {where}{gaia}"
    if "brown_dwarf_like" in labels:
        text += (", red, with little light in Gaia's band" if row.get("_near") else ", and red") + \
            ": what a brown dwarf would look like, a lead to check"
    elif "red" in labels and cls != "galaxy":
        text += ", with red colours"
    return text + "."


def spectrals(s: pd.DataFrame, zone: str) -> list[dict]:
    labels = class_labels(s)
    has_gaia = np.isfinite(_col(s, "gaia_sep_arcsec")) | np.isfinite(_col(s, "gaia_g"))
    out = []
    for i, (row, labs) in enumerate(zip(s.to_dict("records"), labels)):
        row["_near"] = bool(has_gaia[i])
        reasons = ["gaia_nearby" if has_gaia[i] else "no_gaia", f"class_{row.get('class', 'unsure')}"]
        out.append({
            "id": f"{zone}-sp-{int(row['source_id'])}", "kind": "spectral", "ra": None, "dec": None, "mjd": None,
            "clock": "months", "det": None, "zone": zone, "zones": [zone], "score": float(row["_score"]), "snr": None, "flux_ujy": None,
            "rate_arcsec_h": None, "pm_mas_yr": None, "pm_err_mas_yr": None, "pa_deg": None,
            "nearest_known_arcsec": None, "gaia_match": bool(has_gaia[i]), "catwise_match": False,
            "labels": labs, "reasons": reasons, "why": spectral_why(row, labs), "flags": [],
            "_sources": [int(row["source_id"])],
        })
    return out


# --- shape ---------------------------------------------------------------------------------------------
SHAPE_FILE = "shape_candidates.parquet"


def shape_candidates(store: Store, zone: str) -> list[dict]:
    """kind `shape`: the rows of results/<zone>/shape_candidates.parquet (written by
    `python -m skyshift_pipeline.shape`), already selected, scored and worded; [] when the file is missing."""
    rel = results_rel(zone, SHAPE_FILE)
    if not store.exists(rel):
        return []
    t = store.read_parquet(rel)
    out = []
    for r in t.to_dict("records"):
        split = lambda v: [x for x in str(v or "").split(",") if x]  # noqa: E731
        out.append({
            "id": f"{zone}-sh-{int(r['source_id'])}", "kind": "shape", "ra": float(r["ra"]), "dec": float(r["dec"]),
            "mjd": r.get("mjd"), "clock": "months", "det": int(r["det"]) if pd.notna(r.get("det")) else 2,
            "zone": zone, "zones": [zone], "score": float(r["score"]), "snr": r.get("snr"), "flux_ujy": r.get("flux_ujy"),
            "rate_arcsec_h": None, "pm_mas_yr": None, "pm_err_mas_yr": None, "pa_deg": r.get("pa_deg"),
            "nearest_known_arcsec": None, "gaia_match": bool(r.get("gaia_match")), "catwise_match": bool(r.get("catwise_match")),
            "labels": [], "reasons": [x for x in split(r.get("reasons")) if x in REASONS["shape"]], "why": str(r["why"]),
            "flags": split(r.get("flags")), "_sources": [int(r["source_id"])],
        })
    return out


# --- zones -------------------------------------------------------------------------------------------
CLASS_COLUMNS = ["source_id", "class", "labels", "brown_dwarf_like", "bd_evidence", "j_m46_ab", "gaia_sep_arcsec",
                 "gaia_g", "gaia_bp_rp", "gaia_parallax", "gaia_parallax_error",
                 *[f"label_{x}" for x in LABEL_BANDS],
                 *sorted({f"{b}_sig" for bands in LABEL_BANDS.values() for b, _ in bands})]


def zone_base(store: Store, zone: str, spectral_cap: int = SPECTRAL_CAP) -> dict:
    """Everything cheap for one zone: suspects, highpm rows, the spectral selection, the zone's data release."""
    susp = store.read_parquet(results_rel(zone, "suspects.parquet"))
    hrel = results_rel(zone, "highpm_candidates.parquet")
    h = highpm_leads(store.read_parquet(hrel)) if store.exists(hrel) else pd.DataFrame()
    crel = results_rel(zone, "classes.parquet")
    cls = read_columns(store, crel, CLASS_COLUMNS) if store.exists(crel) else pd.DataFrame(columns=CLASS_COLUMNS)
    prel = results_rel(zone, "photoz.parquet")
    pz = read_columns(store, prel, ["source_id", "z_best", "good", "label_pah_33_z"]) if store.exists(prel) else None
    cls = clean_labels(cls, pz)
    blended = np.zeros(0, np.int64)
    if len(cls):
        pool = ((cls["labels"].fillna("").astype(str) != "").to_numpy() | cls["brown_dwarf_like"].to_numpy(bool)) & \
            ~(np.isfinite(_col(cls, "gaia_sep_arcsec")) | np.isfinite(_col(cls, "gaia_g")))
        blended = gaia_blends(store, zone, cls["source_id"].to_numpy(np.int64)[pool])
        L.info("candidates %s: %d labelled no-Gaia sources, %d with a Gaia star within %.0f″ (counted as Gaia)",
               zone, int(pool.sum()), len(blended), C.BLEND_ARCSEC)
    spec = select_spectral(cls, spectral_cap, blended) if len(cls) else cls
    hp_ids = np.unique(np.r_[h["source_id"].to_numpy(np.int64), h["source_id_b"].to_numpy(np.int64)]) if len(h) else np.zeros(0, np.int64)
    sub = cls[cls["source_id"].isin(hp_ids)]
    cl = dict(zip(sub["source_id"].to_numpy(np.int64), class_labels(sub)))
    try:
        coll = sorted(set(read_columns(store, photolist_rel(zone), ["collection"])["collection"]))
    except Exception:  # noqa: BLE001
        coll = []
    mrel = results_rel(zone, "movers.parquet")
    mov = read_columns(store, mrel, ["det", "visit"]) if store.exists(mrel) and "visit" in susp else None
    cells = excess_cells(susp, mov) if mov is not None else {}
    reps = repeat_positions(susp) if "session" in susp else {}
    if cells or reps:
        L.info("candidates %s: excess cells %s; %d suspects at a repeated position", zone,
               {f"D{d} visit {v}": round(x, 2) for (d, v), x in sorted(cells.items())}, len(reps))
    return {"suspects": susp, "highpm": h, "spectral": spec, "class_labels": cl, "collections": coll,
            "cells": cells, "repeats": reps}


def dedupe(earlier: list[dict], later: list[dict]) -> list[dict]:
    """Drop from `later` what `earlier` already lists: same kind within DEDUPE_ARCSEC (movers: within
    DEDUPE_MOVER_ARCSEC and DEDUPE_MOVER_DAYS, as suspects.json). The earlier entry's `zones` gets the later zone."""
    if not earlier or not later:
        return later
    keep = []
    by_kind: dict[str, list[dict]] = {}
    for c in earlier:
        by_kind.setdefault(c["kind"], []).append(c)
    trees = {k: (SkyTree(np.array([c["ra"] for c in v]), np.array([c["dec"] for c in v])), v) for k, v in by_kind.items()}
    for c in later:
        t = trees.get(c["kind"])
        if t is None:
            keep.append(c)
            continue
        mover = c["kind"] == "mover"
        hits = t[0].query_radius(c["ra"], c["dec"], DEDUPE_MOVER_ARCSEC if mover else DEDUPE_ARCSEC)[0]
        if mover:
            hits = [j for j in hits if abs(t[1][j]["mjd"] - c["mjd"]) <= DEDUPE_MOVER_DAYS]
        if not hits:
            keep.append(c)
            continue
        for j in hits:
            zs = t[1][j].setdefault("zones", [t[1][j]["zone"]])
            if c["zone"] not in zs:
                zs.append(c["zone"])
    return keep


def fill_sources(store: Store, zone: str, cands: list[dict], workers: int) -> None:
    """high_pm and spectral: det, snr, flux_ujy (best detector of the spectrum), mjd and flags (member dots),
    catwise_match (spectral, crossids), ra/dec (spectral, sources) and the variable reason."""
    fixed = [c for c in cands if c["kind"] in ("high_pm", "spectral")]
    if not fixed:
        return
    ids = np.unique([s for c in fixed for s in c["_sources"]]).astype(np.int64)
    t0 = time.time()
    st = source_stats(store, zone, ids).set_index("source_id")
    info = member_info(store, zone, ids, workers).set_index("source_id")
    pos = read_columns(store, results_rel(zone, "sources.parquet"), ["source_id", "ra", "dec"], ids).set_index("source_id")
    xrel = results_rel(zone, "crossids.parquet")
    cw = (read_columns(store, xrel, ["source_id", "catwise_name"], ids).set_index("source_id")["catwise_name"]
          if store.exists(xrel) else pd.Series(dtype=str))
    L.info("candidates %s: %d sources' detectors, dots and flags in %.0f s", zone, len(ids), time.time() - t0)
    for c in fixed:
        sid = c["_sources"][0]
        if sid in st.index:
            r = st.loc[sid]
            c["det"], c["snr"], c["flux_ujy"] = _opt(r["det"], int), _opt(r["snr"]), _opt(r["flux_ujy"])
            if c["kind"] == "spectral" and bool(r["variable"]):
                c["reasons"].append("variable")
        names: list[str] = []
        for s in c["_sources"]:
            if s in info.index:
                names += [n for n in fixed_flag_names(info.loc[s].to_dict()) if n not in names]
        c["flags"] = sorted(names, key=list(RECORD_FLAG_NAMES.values()).index)
        if c["kind"] == "spectral":
            if sid in pos.index:
                c["ra"], c["dec"] = float(pos.loc[sid, "ra"]), float(pos.loc[sid, "dec"])
            if sid in info.index:
                c["mjd"] = float(info.loc[sid, "mjd"])
            c["catwise_match"] = bool(str(cw.get(sid, "") or "")) if len(cw) else False
        if c["det"] is None:  # no usable spectrum (or no sources row): contracts/api.md `det` is an int 1-6, never
            c["det"] = DEFAULT_DET  # null, so the site's default detector; snr and flux_ujy stay null


def build(store: Store, zones: list[str], frames_root: Path | None = None, workers: int = 32,
          spectral_cap: int = SPECTRAL_CAP) -> dict:
    """The candidates.json document for these zones, in this order."""
    kept: list[dict] = []
    collections: set[str] = set()
    used_zones = []
    for z in zones:
        t0 = time.time()
        b = zone_base(store, z, spectral_cap)
        collections.update(b["collections"])
        cands = movers(b["suspects"], z, None, b["cells"], b["repeats"]) + high_pms(b["highpm"], z, b["class_labels"]) + spectrals(b["spectral"], z)
        cands += shape_candidates(store, z)
        # spectral positions are needed for the dedupe: read them now (cheap)
        sp = [c for c in cands if c["kind"] == "spectral"]
        if sp:
            ids = np.array([c["_sources"][0] for c in sp], np.int64)
            spec_pos = read_columns(store, results_rel(z, "sources.parquet"), ["source_id", "ra", "dec"], ids).set_index("source_id")
            for c in sp:
                s = c["_sources"][0]
                if s in spec_pos.index:
                    c["ra"], c["dec"] = float(spec_pos.loc[s, "ra"]), float(spec_pos.loc[s, "dec"])
        n0 = len(cands)
        cands = dedupe(kept, [c for c in cands if c["ra"] is not None])
        L.info("candidates %s: %d found, %d already listed from an earlier zone", z, n0, n0 - len(cands))
        used_zones.append(z)
        if not cands:
            continue
        # the expensive parts, only for what is kept
        ms = [c for c in cands if c["kind"] == "mover"]
        if ms:
            keep_ids = {c["id"] for c in ms}
            susp = b["suspects"][b["suspects"]["id"].isin(keep_ids)].reset_index(drop=True)
            t1 = time.time()
            near = mover_dots(store, susp, workers)
            L.info("candidates %s: dots of %d movers in %.0f s", z, len(susp), time.time() - t1)
            full = {c["id"]: c for c in movers(susp, z, near, b["cells"], b["repeats"])}
            cands = [full.get(c["id"], c) if c["kind"] == "mover" else c for c in cands]
        fill_sources(store, z, cands, workers)
        kept += cands
        L.info("candidates %s: %d kept in %.0f s", z, len(cands), time.time() - t0)
    gb = galactic_b([c["ra"] for c in kept], [c["dec"] for c in kept])
    for c, b_ in zip(kept, gb):
        c["galactic_b"] = float(b_)
    order = {z: i for i, z in enumerate(used_zones)}
    kept.sort(key=lambda c: (order[c["zone"]], KINDS.index(c["kind"]), GRADE_RANK.get(c.get("grade"), 1), -c["score"], c["id"]))
    ids = [c["id"] for c in kept]
    if len(set(ids)) != len(ids):
        raise ValueError("candidate ids are not unique")
    for c in kept:
        c["frames_url"] = (f"cand/{c['id']}/frames.json"
                           if frames_root is not None and (frames_root / "cand" / c["id"] / "frames.json").exists() else None)
    release = "+".join(c.replace("spherex_", "").upper() for c in sorted(collections)) or "DR1+QR3"
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_release": release,
        "zones": used_zones,
        "counts": {k: sum(c["kind"] == k for c in kept) for k in KINDS},
        "candidates": [finish(c) for c in kept],
    }


def finish(c: dict) -> dict:
    """The contract's fields in order, rounded; NaN -> null."""
    nd = {"ra": 6, "dec": 6, "mjd": 6, "score": 3, "snr": 1, "flux_ujy": 1, "rate_arcsec_h": 1, "pm_mas_yr": 1,
          "pm_err_mas_yr": 1, "pa_deg": 1, "nearest_known_arcsec": 1, "galactic_b": 3}
    out = {}
    for k in FIELDS:
        v = c.get(k)
        if k in nd:
            v = _num(v, nd[k])
        elif k == "det":
            v = int(v) if v is not None else None
        elif k in ("gaia_match", "catwise_match"):
            v = bool(v)
        elif k == "zones":
            v = list(v or [c["zone"]])
        out[k] = v
    return out


def dumps(doc: dict) -> str:
    """Compact JSON, one candidate per line (small diffs, small file)."""
    head = {k: v for k, v in doc.items() if k != "candidates"}
    lines = [json.dumps(c, ensure_ascii=False, separators=(",", ":"), allow_nan=False) for c in doc["candidates"]]
    text = json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1]
    return text + ',"candidates":[\n' + ",\n".join(lines) + "\n]}\n"


def sample(doc: dict, per_kind: int = 8) -> dict:
    """A small real sample for fixtures/api/candidates.json: the best per_kind of each kind per zone,
    plus the lowest-scoring of each (so the UI sees both ends)."""
    picked, seen = [], set()
    for z in doc["zones"]:
        for k in KINDS:
            cs = [c for c in doc["candidates"] if c["zone"] == z and c["kind"] == k]
            for c in cs[:per_kind] + cs[-1:]:
                if c["id"] not in seen:
                    seen.add(c["id"])
                    picked.append(c)
    return {"synthetic": False,
            "note": ("A real sample of web/public/data/candidates.json (pipeline/src/skyshift_pipeline/candidates.py, "
                     f"--fixture): the {per_kind} best and the last candidate of each kind per zone. counts are the full "
                     "file's. frames_url resolves only next to the real file (web/public/data/cand/<id>/)."),
            **{k: v for k, v in doc.items() if k != "candidates"}, "candidates": picked}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skyshift-candidates", description=__doc__.split("\n")[0])
    ap.add_argument("--zones", help="comma-separated, in order (default: every finished zone, largest first)")
    ap.add_argument("--root", default="gs://nasa-glazing-data")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--fixture", help="also write a small sample here (fixtures/api/candidates.json)")
    ap.add_argument("--spectral-cap", type=int, default=SPECTRAL_CAP)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--cache", help="folder that keeps the column reads (dot files, sources) between runs")
    a = ap.parse_args(argv)
    log.setup()
    global CACHE_DIR
    CACHE_DIR = Path(a.cache) if a.cache else None
    store = Store(a.root)
    zones = a.zones.split(",") if a.zones else finished_zones(store)
    if not zones:
        L.error("no finished zone under %s", store.root)
        return 1
    out = Path(a.out)
    doc = build(store, zones, out.parent, a.workers, a.spectral_cap)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(doc))
    L.info("wrote %s: %s, zones %s, %.2f MB", out, doc["counts"], doc["zones"], out.stat().st_size / 1e6)
    if a.fixture:
        Path(a.fixture).write_text(json.dumps(sample(doc), ensure_ascii=False, indent=1, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
