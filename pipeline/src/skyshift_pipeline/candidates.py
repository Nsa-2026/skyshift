"""Export `candidates.json` (contracts/api.md, "candidates.json"): what the pipeline found, for people
to review on the site's Discover and Review pages.

    uv run skyshift-candidates                                   # every finished zone, hunt first
    uv run skyshift-candidates --zones hunt,pilot --out ../web/public/data/candidates.json

A zone is finished when results/<zone>/suspects.parquet exists (`suspects` is the pipeline's last step).
Four kinds:
- mover     results/<zone>/suspects.parquet (movers no catalogue explains), clock hours. The rate and
            direction come from the mover's own dots, re-read from dots/ for the exposures it was seen in.
            A mover near a planet at its time (skyshift_core.planets: the planet, its moons, its glare) is a known
            body and is not listed: the suspects' `planets` veto, applied here too for tables written before it.
            `--drop-planets <candidates.json>` applies only that to an existing export (drop_planet_movers).
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
high_pm, spectral and shape (results/<zone>/fragments.parquet, step `fragments`, when present): a source tagged
bright_fragment (a piece of a saturated star's ring) or `duplicate` (a second source of one star) is never listed; a
`blend` (two Gaia stars within 8″) gets that reason.
Zones are listed in the order given (hunt first). An object already listed from an earlier zone is not
listed again (the pilot lies inside the hunt zone and was redone with the same photos); the earlier entry's
`zones` gets the later zone (as suspects.json).
Movers also carry the suspects' vetting (vet.py): `grade` (A/B/C; null for other kinds and ungraded zones) and the failed
gates as reason codes; grade C scores × MOVER_GRADE_C and the list puts movers in grade order, so C never leads.
Every candidate has `status` (open, or flaw when a strong `*_proven` check holds; known only from a side file),
`known_name` and `checks` (contracts/api.md v3.23; rules, texts and measured reference rates in candidate_checks.py,
built from the tables already read: the dots' flags, detectors, motion against the visit's known asteroids, flux,
place, vet gates; for the fixed kinds highpm/fragments/classes columns). Checks rank, they never hide: the kind's base
score × CK.STRONG_FACTOR per strong check × CK.WARN_FACTOR per warn (checks already counted by grade C, the known term
or a high_pm reason excepted). `--checks-dir DIR` merges the producers' side files (DIR/*.json) during the export,
`--merge-only IN.json --checks-dir DIR --out OUT.json` into an existing file (candidate_checks.merge_side_files).
Grade A (suspects.py: on a tracklets track over >= 2 sessions at >= 5σ) leads; its `why` names the track and it never
gets motion_not_significant (its own one-session dots may not show the motion; the track does).
`score` (0-1, higher = more promising) is defined per kind in mover_score, highpm_score and
spectral_score (formulas also in contracts/api.md). Movers also get `rb_score` (rb_score.py: a learned real-or-bogus
score from the suspects row; null for slow and wide movers), and as the last step (after any side files) the open
movers of each tier (status, grade, a strong check, motion_not_significant) take that tier's scores in rb_score order;
exempt ones keep theirs (rb_score.reorder: ranking only, no grade or status changes, nothing removed). frames_url is set for candidates whose static frames
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
from skyshift_core.motion import PARALLAX_MODES
from skyshift_core.planets import near_planets

from . import candidate_checks as CK
from . import rb_score as RB
from . import config as C
from . import log
from .sky import SkyTree, angsep
from .spectra import NCH_DET, NDET
from .store import Store, dots_rel, linked_dots_dir, photolist_rel, results_rel, use_dots_dir

L = log.get("candidates")

KINDS = ("mover", "high_pm", "spectral", "shape")
CACHE_DIR: Path | None = None  # --cache: column reads kept on disk between runs
SLIM_READS = True  # members.parquet: only the pages holding the candidates' rows (parquet_rows); --full-reads: off
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
MOVER_KNOWN_RANGE = (C.KNOWN_ARCSEC, 300.0)  # nearest known object: x0.5 at 30″ rising to x1 at 300″
MOVER_GRADE_C = 0.1            # the suspects' vetting graded it C (a failed gate or a catalogue source within 6″)
# From 16 Nov the other caution factors are the checks' (candidate_checks: strong x0.3, warn x0.95). Gone: OUTLIER x0.6
# (OUTLIER marks 68% of known asteroids, 28% of the list: a sign for), detector_excess x0.6 (a zone statistic; it hit
# 306P, the list's only real object), a masked pixel in a 3x3 x0.8 (known asteroids 23-29%, the list 16-35%: no sign
# either way), two dots x0.85, motion under 3σ x0.85, another record flag x0.7, edge x0.5 (now two_photos,
# may_not_move, flag_*, edge checks)
GRADE_RANK = {"A": 0, "B": 1, "C": 2}  # list order inside each zone and kind; null (other kinds, ungraded) sorts with B
HIGHPM_SCORE_SCALE = 0.2       # quality = exp(-pipeline score / this); the pipeline score is lower = better
HIGHPM_REASON_WEIGHT = {"short_span": 0.5, "few_dots": 0.5, "bad_fit": 0.5, "unsteady": 0.3, "over_pm_cap": 0.3,
                        "gaia_pm_disagrees": 0.4, "catwise_pm_disagrees": 0.4, "at_control_level": 0.3,
                        "implausible_pm": 0.01}  # implausible_pm: to the bottom of the list
HIGHPM_KNOWN_STAR = 0.6        # status "gaia", "2mass" or "allwise" (highpm's deep check): a catalogued star
HIGHPM_KNOWN_STATUS = ("gaia", "2mass", "allwise")
HIGHPM_GAIA_MATCH = ("gaia_pm", "gaia_position")  # counterparts that are Gaia's entry for this object (#388)
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
    "high_pm": ("pm_fit", "pm_pair", "vetted", "catwise_pm_agrees", "parallax_fit", "short_span", "few_dots", "bad_fit",
                "unsteady", "over_pm_cap", "gaia_pm_disagrees", "catwise_pm_disagrees", "at_control_level", "implausible_pm",
                "blend", "gaia_still", "allwise_still"),
    "spectral": ("no_gaia", "gaia_nearby", "class_star", "class_galaxy", "class_unsure", "variable", "blend"),
    "shape": ("elongated", "steady_pa", "one_visit", "one_catalogue_source", "catalogue_neighbour", "class_galaxy", "blend"),
}
FIELDS = ("id", "kind", "ra", "dec", "mjd", "clock", "det", "zone", "zones", "score", "rb_score", "grade", "snr", "flux_ujy", "rate_arcsec_h",
          "pm_mas_yr", "pm_err_mas_yr", "pa_deg", "nearest_known_arcsec", "gaia_match", "catwise_match",
          "galactic_b", "labels", "reasons", "why", "flags", "frames_url", "status", "known_name", "checks")  # v3.23 (+ rb_score)


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
                 id_col: str = "source_id", by_pages: bool = False) -> pd.DataFrame:
    """Some columns of a parquet file, read with range requests (Store.read_parquet reads whole files).
    ids: keep only rows whose id_col is in it. by_pages (with ids, SLIM_READS): read only the pages that hold those
    rows (parquet_rows; members.parquet), unless the whole columns are already in the cache; same result."""
    import hashlib

    key = skey = None
    if CACHE_DIR is not None:
        h = hashlib.sha1(f"{store.root}|{rel}|{','.join(columns)}".encode()).hexdigest()[:16]
        key = CACHE_DIR / f"{rel.replace('/', '__')}.{h}.parquet"
        if by_pages and ids is not None:
            hi = hashlib.sha1(np.unique(np.asarray(ids, np.int64)).tobytes()).hexdigest()[:16]
            skey = CACHE_DIR / f"{rel.replace('/', '__')}.{h}.rows-{hi}.parquet"
    df = None
    if key is not None and key.exists():
        df = pd.read_parquet(key)
    elif skey is not None and skey.exists():
        df = pd.read_parquet(skey)
    elif by_pages and ids is not None and SLIM_READS:
        from . import parquet_rows

        df = parquet_rows.read_rows(store.fs, store.path(rel), columns, ids, id_col)
        if df is not None and skey is not None:
            skey.parent.mkdir(parents=True, exist_ok=True)
            tmp = skey.with_suffix(f".tmp{id(df)}")
            df.to_parquet(tmp, index=False)
            tmp.replace(skey)
    if df is None:
        from . import parquet_rows

        t = parquet_rows.read_table(store.fs, store.path(rel), columns)  # exact ranges: no 5 MiB read-ahead
        df = t.to_pandas()
        if key is not None:
            key.parent.mkdir(parents=True, exist_ok=True)
            tmp = key.with_suffix(f".tmp{id(df)}")
            df.to_parquet(tmp, index=False)
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


def mover_dots(store: Store, susp: pd.DataFrame, workers: int = 32, only: np.ndarray | None = None) -> pd.DataFrame:
    """Dots within MOVER_MATCH_RADIUS_ARCSEC of each suspect, from the photos it was seen in
    (its obs_ids x detectors). Columns: sid (suspect row), obs_id, detector, ra, dec, mjd, snr, pos_err_arcsec.
    only: the rows to read for (bool per row; sid stays the row in susp)."""
    want: dict[tuple[str, int], list[int]] = {}
    for i, r in enumerate(susp.itertuples()):
        if only is not None and not only[i]:
            continue
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


def mover_dot_flags(store: Store, susp: pd.DataFrame, workers: int = 32) -> list[np.ndarray | None]:
    """Per suspect, its dots' 3×3 `flags` (the dot files' column at the dot lists' dot_row), in dot-list order; None
    where the lists or a dot file are missing. A column read of its own: the shared cache of the other reads stays."""
    if not len(susp) or not all(c in susp for c in ("dot_obs", "dot_det", "dot_row")):
        return [None] * len(susp)
    files = sorted({(str(o), int(d)) for os_, ds in zip(susp["dot_obs"], susp["dot_det"]) for o, d in zip(os_, ds)})

    def one(f):
        try:
            return f, read_columns(store, dots_rel(*f), ["flags"])["flags"].to_numpy(np.int64) & 0xFFFFFFFF
        except FileNotFoundError:
            return f, None

    col = dict(_pool_map(one, files, workers))
    out: list[np.ndarray | None] = []
    for os_, ds, rows in zip(susp["dot_obs"], susp["dot_det"], susp["dot_row"]):
        v = []
        for o, d, k in zip(os_, ds, rows):
            fl = col.get((str(o), int(d)))
            if fl is None or k is None or not 0 <= int(k) < len(fl):
                v = None
                break
            v.append(int(fl[int(k)]))
        out.append(np.asarray(v, np.int64) if v is not None else None)
    return out


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
    """(rate ″/h, its 1σ error, position angle east of north) of the dots, by link's own rule (link.track_fit): one
    position per exposure (the mean of its dots), an unweighted least-squares line in time; None with fewer than two
    exposures. So the rate shown is the movers table's for the same dots (until 16 Nov a fit weighted by pos_err gave
    other numbers on noisy dots: ecl10-692050057552-87780103 28.8″/h on the site, 11.1″/h in movers.parquet). Times
    within 5 s are one exposure (D1-D6 of one exposure share MJD-AVG to milliseconds; until 16 Nov a D1+D4 pair's
    millisecond gap gave 0.4-10 million ″/h). The error propagates the dots' pos_err_arcsec × POS_ERR_SCALE (raw
    pos_err is 1.4–1.8× too small against Gaia; 0.3″ × that where missing) through that line."""
    exp = CK.exposures_of(dots["mjd"].to_numpy(np.float64))
    ue, inv = np.unique(exp, return_inverse=True)
    if ue.size < 2:
        return None
    e, n = tangent_offset_arcsec(dots["ra"].mean(), dots["dec"].mean(), dots["ra"].to_numpy(), dots["dec"].to_numpy())
    err = dots["pos_err_arcsec"].to_numpy(np.float64) if "pos_err_arcsec" in dots else np.full(len(dots), 0.3)
    err = np.where(np.isfinite(err) & (err > 0), err, 0.3) * C.POS_ERR_SCALE
    cnt = np.bincount(inv).astype(np.float64)
    te, ee, ne = (np.bincount(inv, v) / cnt for v in (dots["mjd"].to_numpy(np.float64) * 24.0, e, n))
    se = np.sqrt(np.bincount(inv, err**2)) / cnt   # σ of each exposure's mean position, per axis
    dt = te - te.mean()
    stt = float(np.sum(dt**2))
    if stt <= 0:
        return None
    ve, vn = float(np.sum(dt * (ee - ee.mean())) / stt), float(np.sum(dt * (ne - ne.mean())) / stt)
    sig = float(math.sqrt(np.sum(dt**2 * se**2)) / stt)
    return float(math.hypot(ve, vn)), sig, float(pa_deg(ve, vn))


def source_stats(store: Store, zone: str, ids: np.ndarray) -> pd.DataFrame:
    """Per source: best detector by S/N of its spectrum (channels' S/N added in quadrature), that S/N, the
    median channel flux there (µJy), saturated, variable. A source with no channel of positive, finite S/N in
    any detector has no best detector: det, snr and flux_ujy are None (hunt, 14 Nov: 65 high_pm rows had been
    written as D1, S/N 0, the argmax of six zeros)."""
    src = read_columns(store, results_rel(zone, "sources.parquet"),
                       ["source_id", "flux_ujy", "err_ujy", "saturated", "variable"], ids)
    if not len(src):
        return pd.DataFrame(columns=["source_id", "det", "snr", "flux_ujy", "saturated", "variable", "n_channels"])
    f = np.stack(src["flux_ujy"].to_numpy()).astype(np.float64).reshape(len(src), NDET, NCH_DET)
    e = np.stack(src["err_ujy"].to_numpy()).astype(np.float64).reshape(len(src), NDET, NCH_DET)
    measured = (np.isfinite(f) & np.isfinite(e) & (e > 0)).reshape(len(src), -1).sum(axis=1)
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
                         "variable": src["variable"].to_numpy(bool) if "variable" in src else False,
                         "n_channels": measured.astype(np.int64)})


def _opt(x, kind=float):
    """kind(x), or None for a missing value (None, NaN, pd.NA)."""
    return None if x is None or pd.isna(x) else kind(x)


def member_info(store: Store, zone: str, ids: np.ndarray, workers: int = 32) -> pd.DataFrame:
    """Per source: mean MJD of its dots (photo list) and the OR / share of record-only flag bits over its
    dots (dot files' `flags`, the 3×3 OR around each dot). Columns: source_id, mjd, n_dots, flag_<bit> share."""
    m = read_columns(store, results_rel(zone, "members.parquet"), ["source_id", "obs_id", "detector", "dot_row"], ids,
                     by_pages=True)
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
def mover_score(snr, evidence, nearest_known, grade=None) -> np.ndarray:
    """The mover's base score (contracts/api.md; the checks' factors come on top, candidate_checks.factor):
    (1 - exp(-snr / 15)) x {two_exposures 1, two_detectors 0.6}
    x (1 if no known object within 600″, else 0.5 rising linearly to 1 between 30″ and 300″) x 0.1 for grade C."""
    snr = np.asarray(snr, np.float64)
    s = 1.0 - np.exp(-np.maximum(snr, 0.0) / MOVER_SNR_SCALE)
    s = s * np.array([MOVER_EVIDENCE.get(e, 0.5) for e in evidence])
    d = np.asarray(nearest_known, np.float64)
    lo, hi = MOVER_KNOWN_RANGE
    s = s * np.where(np.isfinite(d), 0.5 + 0.5 * np.clip((d - lo) / (hi - lo), 0.0, 1.0), 1.0)
    if grade is not None:
        s = s * np.where(np.asarray(grade, dtype=object) == "C", MOVER_GRADE_C, 1.0)
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
            "2mass": "a 2MASS source lies within 6″", "allwise": "an AllWISE source lies within 6″",
            # the suspects' strong warnings (16 Nov), when its tables carry them
            "gaia": "a Gaia DR3 source lies within 6″", "catwise": "a CatWISE2020 source lies within 6″",
            "bloom": "BLOOM is set at every dot's peak", "halo": "HALO is set at every dot's peak",
            "bright_star": "a bright fixed source lies within 15″",
            # the mover flaws (16 Nov, #385)
            "same_pixel": "dots of two exposures sit on one detector pixel",
            "whole_pixel": "its dots sit exactly on whole pixels (no measured centre)",
            "wide": "every dot is wider than a star", "cross_detector": "it changes detector between exposures",
            # wide as a reason since #394: only together with a blend (config VET_WIDE_WITH; warn_values wide.with_blend)
            "wide_blend": "its dots are wider than a star and two Gaia stars sit either side of it (their blend)",
            "fixed_source": "a fixed source or Gaia star is at its place", "glow": "it is glow from a very bright star",
            "unseen": "exposures that photographed its path don't show it"}


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
              vet_reasons: list[str] | None = None, known: str | None = None,
              overruled_by_track: list[str] | None = None) -> str:
    """overruled_by_track: the reasons its grade-A track outweighed (suspects' track_overruled; they stay warnings)."""
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
    if overruled_by_track:
        seen += (", a track that outweighs what alone would fail the vetting ("
                 + "; ".join(VET_TEXT.get(x, x.replace("_", " ")) for x in overruled_by_track) + "; see the warnings)")
    name = f" ({known})" if known else ""
    if nearest_known is None or not math.isfinite(nearest_known):
        known = "no known asteroid, comet, planet or moon was predicted within 10′"
    else:
        known = f"the nearest known asteroid, comet, planet or moon{name} was predicted {_fmt_dist(nearest_known)} away"
    if repeat is not None:
        known += (f", but another unexplained mover was seen {_fmt_dist(repeat[0])} from here {_fmt_days(repeat[1])} "
                  "apart, so it may be a fixed source")
    return f"{vet_text(grade, list(vet_reasons or []))}{seen}; {known}."


def _list(v) -> list:
    """A dot-list cell as a list ([] for a missing value)."""
    if v is None or isinstance(v, float):
        return []
    return list(v)


def suspect_warnings(r) -> dict[str, dict]:
    """The suspects' `warn_codes` (list<string>) and `warn_values` (JSON {code: {name: number}}) of one row, as
    {code: values}; {} when the columns are absent (tables written before the suspects kept former vetoes)."""
    codes = _list(getattr(r, "warn_codes", None))
    if not codes:
        return {}
    raw = getattr(r, "warn_values", None)
    try:
        vals = json.loads(raw) if isinstance(raw, str) and raw else (raw if isinstance(raw, dict) else {})
    except ValueError:
        vals = {}
    return {str(c): (vals.get(str(c)) if isinstance(vals.get(str(c)), dict) else {}) for c in codes}


def suspect_levels(r) -> dict[str, str]:
    """The suspects' `warn_levels` (JSON {code: "strong"}, #385) of one row; {} when absent (every code a warning)."""
    raw = getattr(r, "warn_levels", None)
    try:
        lv = json.loads(raw) if isinstance(raw, str) and raw else (raw if isinstance(raw, dict) else {})
    except ValueError:
        lv = {}
    return {str(k): str(v) for k, v in lv.items()} if isinstance(lv, dict) else {}


def overruled(r) -> list[str]:
    """The reasons a grade-A track overruled on this suspect (suspects.track_grades, column track_overruled: its reasons
    were cleared, the gate_<name> columns keep what fired), in VET_TRACK_OVERRULES order; [] otherwise."""
    if not bool(getattr(r, "track_overruled", False) or False):
        return []
    return [k for k in C.VET_TRACK_OVERRULES if bool(getattr(r, f"gate_{k}", False) or False)]


def on_track(r) -> tuple[int, float, float] | None:
    """(sessions, rate ″/h, rate / σ) of the tracklets track any suspect is on (tracklet_id >= 0); None off a track."""
    tid = getattr(r, "tracklet_id", None)
    if tid is None or pd.isna(tid) or int(tid) < 0:
        return None
    n, rate, sig = getattr(r, "n_sessions", None), getattr(r, "rate_arcsec_h", None), getattr(r, "rate_sigma", None)
    if n is None or rate is None or pd.isna(n) or pd.isna(rate):
        return None
    snr = float(rate) / float(sig) if sig is not None and pd.notna(sig) and float(sig) > 0 else math.inf
    return int(n), float(rate), snr


def _fin(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def table_motion(r) -> tuple[float | None, float | None]:
    """(rate ″/h, PA °) of a suspect's own session from its row: session_rate_arcsec_h / session_pa_deg (link's motion,
    which suspects keeps there since 16 Nov because a track's chord goes in rate_arcsec_h / pa_deg); link's
    rate_arcsec_h / pa_deg in a table without track columns; else (None, None). Never a track's chord."""
    if hasattr(r, "session_rate_arcsec_h"):
        return _fin(r.session_rate_arcsec_h), _fin(getattr(r, "session_pa_deg", None))
    if not hasattr(r, "tracklet_id"):
        return _fin(getattr(r, "rate_arcsec_h", None)), _fin(getattr(r, "pa_deg", None))
    return None, None


def table_rate_err(r) -> float | None:
    """1σ error (″/h) of link's in-session rate from the row's dot lists (dot_mjd, dot_snr): each dot's position σ from
    its S/N (link.dot_sigma_arcsec: contracts/dots.md pos_err × LINK_POS_ERR_SCALE), one mean position per exposure,
    the unweighted line of motion() and link; None under two exposures or without the lists."""
    from .link import dot_sigma_arcsec

    t = np.asarray(_list(getattr(r, "dot_mjd", None)), np.float64)
    snr = np.asarray(_list(getattr(r, "dot_snr", None)), np.float64)
    if t.size < 2 or t.size != snr.size or not np.isfinite(t).all():
        return None
    ue, inv = np.unique(CK.exposures_of(t), return_inverse=True)
    if ue.size < 2:
        return None
    cnt = np.bincount(inv).astype(np.float64)
    te = np.bincount(inv, t * 24.0) / cnt
    se = np.sqrt(np.bincount(inv, dot_sigma_arcsec(snr) ** 2)) / cnt
    dt = te - te.mean()
    stt = float(np.sum(dt**2))
    return float(math.sqrt(np.sum(dt**2 * se**2)) / stt) if stt > 0 else None


def needs_positions(susp: pd.DataFrame) -> np.ndarray:
    """Rows whose motion must come from their dots' positions (dot files' ra, dec, ...): those without link's
    in-session motion in the table (table_motion; tables written before 16 Nov's session_* columns)."""
    return np.array([table_motion(r)[0] is None for r in susp.itertuples()], bool)


def fast_held(r) -> tuple[int, float] | None:
    """(sessions, track rate ″/h) of a grade B suspect that suspects.track_grades held back from A (stats `fast_held`):
    on a track of >= VET_A_MIN_SESSIONS sessions at >= VET_MOTION_SIGMA, faster than VET_A_FAST_ARCSEC_H, with no
    recovered dot and no detector partner on the track (the only way such a B misses A); None otherwise."""
    if getattr(r, "grade", None) != "B":
        return None
    tr = on_track(r)
    if tr is None:
        return None
    n, rate, snr = tr
    rs = _fin(getattr(r, "rate_snr", None))
    snr = rs if rs is not None else snr
    if n >= C.VET_A_MIN_SESSIONS and snr >= C.VET_MOTION_SIGMA and rate > C.VET_A_FAST_ARCSEC_H:
        return n, rate
    return None


def known_name(r) -> str | None:
    """The suspects' nearest_known_name (the body nearest_known_arcsec measures to); None without one."""
    v = getattr(r, "nearest_known_name", None)
    return str(v) if isinstance(v, str) and v.strip() else None


def mover_facts(r, dots: pd.DataFrame | None, mo, no_motion: bool, track, ctx: dict, flags3,
                gates: list[str], repeat, excess) -> dict:
    """The inputs of candidate_checks.mover_checks for one suspect row."""
    from .vet import star_size

    lists = {k: _list(getattr(r, f"dot_{k}", None)) for k in ("det", "x", "y", "mjd", "flux", "snr", "size", "fpk", "obs")}
    n = len(lists["det"])
    dl = {k: v for k, v in lists.items() if len(v) == n} if n else {}
    if n:
        dl["flags3"] = flags3
    elif dots is not None and len(dots) and {"detector", "mjd"} <= set(dots.columns):  # no dot lists (old tables):
        dl = {"det": dots["detector"].astype(int).tolist(), "mjd": dots["mjd"].tolist()}  # the re-read dots' times
    rate, err, pa = mo if mo else (None, None, None)
    tr = on_track(r)
    visit = int(getattr(r, "visit", 0) or 0)
    nk = getattr(r, "nearest_known_arcsec", None)
    ns = getattr(r, "near_source_arcsec", None)
    bright = None
    bt = ctx.get("bright")
    if bt is not None:
        tree, g = bt
        sep, j = tree.nearest(float(r.ra), float(r.dec))
        if j[0] >= 0 and sep[0] <= CK.BRIGHT_STAR_ARCSEC:
            bright = (float(g[j[0]]), float(sep[0]))
    s_rate, s_pa = table_motion(r)
    return {
        "dots": dl, "flags": int(r.flags), "evidence": r.evidence, "n_exposures": int(r.n_exposures), "det": int(r.det),
        "grade": getattr(r, "grade", None), "gates": gates, "rate": rate, "rate_err": err, "pa": pa,
        "session_rate": s_rate, "session_pa": s_pa, "fast_held": fast_held(r), "track_info": track_info(r, ctx),
        "no_motion": no_motion, "track": tr, "on_track": tr is not None, "visit": visit,
        "nearest_known": float(nk) if nk is not None and pd.notna(nk) else None, "known_name": known_name(r),
        "near_source": float(ns) if ns is not None and pd.notna(ns) else None,
        "repeat": repeat, "excess": excess, "ref": ctx.get("motion_ref"), "control_tracks": ctx.get("control_tracks"),
        "partner_photos": ctx.get("photos"), "bright": bright,
        "star_size": star_size(np.asarray(dl["det"], np.int64), np.asarray(dl["snr"], np.float64))
        if "snr" in dl and "det" in dl and n else None,
        "warn": suspect_warnings(r), "warn_levels": suspect_levels(r),
    }


def movers(susp: pd.DataFrame, zone: str, near: pd.DataFrame | None,
           cells: dict[tuple[int, int], float] | None = None, repeats: dict[str, tuple[float, float]] | None = None,
           ctx: dict | None = None, flags3: list | None = None) -> list[dict]:
    """Mover candidates from suspects. `cells` (excess_cells) and `repeats` (repeat_positions) come from the
    whole zone, so they stay right when `susp` is a subset. ctx: the zone's facts for the checks (zone_base: motion_ref,
    control_tracks, photos, bright); flags3: mover_dot_flags of susp (None: not read)."""
    cells, repeats, ctx = cells or {}, repeats or {}, ctx or {}
    rbs = mover_rb(susp)
    out = []
    for i, r in enumerate(susp.itertuples()):
        dets = sorted(int(x) for x in str(r.detectors).split(",") if x)
        dots = None
        if near is not None and len(near):
            dots = match_mover(r.ra, r.dec, r.mjd, int(r.n_dots), near[near["sid"] == i])
        mo = motion(dots) if dots is not None else None
        rate, rate_err, pa = mo if mo else (None, None, None)
        if mo is None:  # no dots read (the row has link's in-session motion, or grade C) or none matched
            rate, pa = table_motion(r)
            rate_err = table_rate_err(r) if rate is not None else None
        track = mover_track(r)  # grade A: its motion is shown over sessions, whatever its own dots say
        no_motion = bool(track is None and rate is not None and rate_err and rate < RATE_SIGMA_MIN * rate_err)
        if dots is not None:
            span_min = float((dots["mjd"].max() - dots["mjd"].min()) * 1440.0)
        else:
            tl = np.asarray(_list(getattr(r, "dot_mjd", None)), np.float64)
            span_min = float(np.ptp(tl) * 1440.0) if tl.size >= 2 and np.isfinite(tl).all() else None
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
        visit = int(getattr(r, "visit", 0) or 0)
        excess = is_excess(dets, visit, cells)
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
        base = float(mover_score([r.snr], [r.evidence], [nk if nk is not None else np.nan], [grade])[0])
        cell = None
        if excess:
            d0 = int(r.det) if (int(r.det), visit) in cells else dets[0]
            cell = (d0, visit, cells.get((d0, visit), 0.0))
        facts = mover_facts(r, dots, (rate, rate_err, pa) if rate is not None else None, no_motion, track, ctx,
                            flags3[i] if flags3 is not None else None, vet_reasons, rep_, cell)
        tr_ = on_track(r)  # a track's rate beats two exposures' (slow: rb_score null, the existing order)
        slow = RB.is_slow(tr_[1], tr_[1] / tr_[2]) if tr_ and tr_[2] > 0 else \
            RB.is_slow(rate, rate_err, int(r.n_exposures))
        wide = "wide" in vet_reasons or "wide" in facts["warn"]
        why_reasons = vet_reasons
        if "wide" in vet_reasons and (facts["warn"].get("wide") or {}).get("with_blend"):
            why_reasons = ["wide_blend" if x == "wide" else x for x in vet_reasons]
        c = {
            "id": r.id, "kind": "mover", "ra": r.ra, "dec": r.dec, "mjd": r.mjd, "clock": "hours", "det": int(r.det),
            "zone": zone, "zones": [zone], "score": base, "grade": grade, "snr": r.snr,
            "rb_score": None if slow or wide or not np.isfinite(rbs[i]) else float(rbs[i]), "flux_ujy": r.flux_ujy, "rate_arcsec_h": rate,
            "pm_mas_yr": None, "pm_err_mas_yr": None, "pa_deg": pa, "nearest_known_arcsec": nk,
            # a catalogue source within 6″ and at most 2 mag fainter (the suspects' reasons gaia, catwise): its own light
            "gaia_match": "gaia" in vet_reasons, "catwise_match": "catwise" in vet_reasons, "labels": [], "reasons": reasons,
            "why": mover_why(r.evidence, int(r.n_exposures), span_min, dets, rate, no_motion, nk, rep_, track, grade,
                             why_reasons, known_name(r), overruled(r)),
            "flags": record_flag_names(f), "_dets": dets, "_base": base,
            "checks": CK.mover_checks(facts),
        }
        set_status(c)
        out.append(c)
    return out


def mover_rb(susp: pd.DataFrame) -> np.ndarray:
    """rb_score per suspects row (rb_score.score_rows; NaN without dot lists). A missing or stale model file gives NaN
    for all, with a warning: the list then keeps the existing order."""
    try:
        return RB.score_rows(susp)
    except (OSError, ValueError, KeyError) as e:
        L.warning("candidates: no rb_score (%s): movers keep the existing order", e)
        return np.full(len(susp), np.nan)


def set_status(c: dict) -> dict:
    """status from the exporter's own checks (flaw for a strong `_proven` one, else open), known_name null, and the score
    = the kind's base score × the checks' factors."""
    c["status"] = CK.status_of(c.get("checks") or [])
    c["known_name"] = None
    c["score"] = float(_clip01(c["_base"] * CK.factor(c)))
    return c


def drop_planet_movers(cands: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Movers near a planet at their time are known bodies, not candidates: the planet itself, one of its moons or its
    glare (skyshift_core.planets; MPC orbits hold none of them). The suspects' `planets` veto, applied again for
    suspects tables written before it. Returns the other candidates, in order, and {planet: movers dropped}."""
    idx = [i for i, c in enumerate(cands) if c["kind"] == "mover"]
    if not idx:
        return cands, {}

    def val(c, k):
        return np.nan if c.get(k) is None else float(c[k])

    names, _ = near_planets([val(cands[i], "ra") for i in idx], [val(cands[i], "dec") for i in idx],
                            [val(cands[i], "mjd") for i in idx])
    drop = {i for i, n in zip(idx, names) if n}
    by: dict[str, int] = {}
    for n in names:
        if n:
            by[n] = by.get(n, 0) + 1
    return [c for i, c in enumerate(cands) if i not in drop], dict(sorted(by.items()))


def drop_planets_doc(doc: dict) -> tuple[dict, dict[str, int]]:
    """An existing candidates.json without its movers near a planet (drop_planet_movers), counts updated; every other
    field and candidate as it was."""
    kept, by = drop_planet_movers(doc["candidates"])
    counts = {k: sum(c["kind"] == k for c in kept) for k in doc.get("counts") or KINDS}
    return {**doc, "counts": counts, "candidates": kept}, by


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
    if str(getattr(r, "fit_mode", "") or "") in PARALLAX_MODES:  # the fit took parallax out (Gaia's or its own)
        out.append("parallax_fit")
    out += [x for x in str(r.reason or "").split(",") if x]
    if pd.notna(r.pm) and r.pm > IMPLAUSIBLE_PM_MAS_YR:
        out.append("implausible_pm")
    return out


def highpm_score(pipeline_score, reasons: list[list[str]], status) -> np.ndarray:
    """score = exp(-pipeline score / 0.2) x product of the vetting reasons' weights x 0.6 for a Gaia star.
    The pipeline score is lower = better: 1 / (pm / pm_err) for a fit, |ln flux ratio| + spectrum-shape rms
    for a pair. Weights: short_span, few_dots, bad_fit 0.5; over_pm_cap, at_control_level 0.3;
    gaia_pm_disagrees, catwise_pm_disagrees 0.4; implausible_pm 0.01 (always last). A known star: status in
    HIGHPM_KNOWN_STATUS."""
    q = np.exp(-np.nan_to_num(np.asarray(pipeline_score, np.float64), nan=1.0) / HIGHPM_SCORE_SCALE)
    w = np.array([np.prod([HIGHPM_REASON_WEIGHT.get(x, 1.0) for x in rs]) if rs else 1.0 for rs in reasons])
    known = np.where(np.isin(np.asarray(status, dtype=object), HIGHPM_KNOWN_STATUS), HIGHPM_KNOWN_STAR, 1.0)
    return _clip01(q * w * known)


def _pm_text(pm_mas_yr: float) -> str:
    x = pm_mas_yr / 1000.0
    return f"{x:.0f}″" if x >= 100 else f"{x:.1f}″" if x >= 10 else f"{x:.2f}″"


CAUTION_TEXT = {
    "at_control_level": "as many such pairs turn up by chance",
    "over_pm_cap": "it is faster than any known star",
    "bad_fit": "the positions don't fit a straight line well",
    "unsteady": "its visits go out and back instead of along a line",
    "few_dots": "it has few detections",
    "short_span": "its visits are too close in time",
}


REF_CATALOG_NAMES = {"gaia": "Gaia DR3", "catwise": "CatWISE2020", "2mass": "2MASS", "allwise": "AllWISE"}


def counterpart_name(ref_id) -> str:
    """A refcat / deep-check ref_id in words: gaia:123 → Gaia DR3 123, 2mass:08251958+2115521 → 2MASS J08251958+2115521,
    allwise:J082519.58+211552.1 → AllWISE J082519.58+211552.1; a 2MASS key from AllWISE (2mass:aw…) → 2MASS."""
    s = str(ref_id or "").strip()
    cat, _, rest = s.partition(":")
    if not rest:
        return s
    if cat == "2mass":
        if rest.startswith("aw"):
            return "2MASS"
        rest = ("J" + rest) if rest[:1].isdigit() else rest
    return f"{REF_CATALOG_NAMES.get(cat, cat)} {rest}"


def _year(mjd) -> str | None:
    """The calendar year of an MJD (Gaia DR3's 57388.0 → 2016, AllWISE's 55400 → 2010)."""
    import datetime as dt

    v = _fin(mjd)
    return str((dt.datetime(1858, 11, 17) + dt.timedelta(days=v)).year) if v is not None else None


def counterpart_tail(r, vetted: bool) -> str | None:
    """The why's catalogue clause from highpm's counterpart (#388): which entry lists the object, its id
    (counterpart_id) and how far it is from our track at the entry's epoch (counterpart_sep_arcsec,
    counterpart_epoch_mjd); for the still kinds, how far it is from where the object is now
    (counterpart_offset_now_arcsec, v3.27; older tables: no number); None without a counterpart."""
    cp = str(getattr(r, "counterpart", "") or "")
    if not cp:
        return None
    who = counterpart_name(getattr(r, "counterpart_id", ""))
    sep = _fin(getattr(r, "counterpart_sep_arcsec", None))
    yr = _year(getattr(r, "counterpart_epoch_mjd", None))
    off = f"{sep:.1f}″ from " if sep is not None else "at "
    then = f" in {yr}" if yr else ""
    away = f" ({sep:.1f}″ away)" if sep is not None else ""
    now = _fin(getattr(r, "counterpart_offset_now_arcsec", None))
    now_away = f" ({now:.1f}″ away)" if now is not None else ""
    if cp == "gaia_pm":
        return (f"; {who or 'Gaia'}{away} lists this star with the same motion, a known nearby star that shows the "
                "method works" if vetted else f"; {who or 'Gaia'}{away} lists a star here")
    if cp == "gaia_position":
        return (f"; {who or 'Gaia'} lists it without a motion, {off}where this motion puts it{then}: a known source "
                "whose motion Gaia doesn't give")
    if cp == "gaia_crossid":
        return f"; {who or 'Gaia'}{away} lists a source here without a motion, not this motion"
    if cp == "gaia_still":
        return (f"; {who or 'Gaia'} lists a source without a motion where this one is now{now_away}, while this motion puts "
                "it elsewhere in 2016: probably a still star, not a mover")
    if cp == "catwise_pm":
        return f"; {who or 'CatWISE'}{away} lists the same motion"
    if cp == "tmass_position":
        return f"; {who or '2MASS'} saw a star {off}where this motion puts it{then}, so it is a known star"
    if cp == "allwise_position":
        return f"; {who or 'AllWISE'} saw it {off}where this motion puts it{then}, so it is a known source"
    if cp == "allwise_still":
        return (f"; {who or 'AllWISE'} saw a source where this one is now{now_away}{then}, while this motion puts it "
                "elsewhere then: probably a still source, not a mover")
    return None


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
    elif hasattr(r, "counterpart"):          # highpm tables from 16 Nov (#388): what lists the object
        tail = counterpart_tail(r, "vetted" in reasons) or (
            "; CatWISE lists the same motion" if "catwise_pm_agrees" in reasons else "; no catalogue lists this motion")
    elif r.status == "gaia":
        tail = ("; Gaia lists this star with the same motion, a known nearby star that shows the method works"
                if "vetted" in reasons else "; Gaia lists a star here")
    elif r.status == "2mass":
        tail = "; 2MASS saw a star where this motion puts it in 2000, so it is a known star"
    else:
        tail = "; CatWISE lists the same motion" if "catwise_pm_agrees" in reasons else "; no catalogue lists this motion"
    cautions = [CAUTION_TEXT[x] for x in ("at_control_level", "over_pm_cap", "unsteady", "bad_fit", "few_dots", "short_span")
                if x in reasons]
    if cautions and "gaia_pm_disagrees" not in reasons and "catwise_pm_disagrees" not in reasons:
        tail += f", but {cautions[0]}"
    return move + tail + "."


def highpm_gaia_match(r) -> bool:
    """Gaia lists this object: counterpart gaia_pm or gaia_position (#388; a gaia_crossid or gaia_still entry is a
    source without our motion); tables before it: status gaia."""
    if hasattr(r, "counterpart"):
        return str(r.counterpart or "") in HIGHPM_GAIA_MATCH
    return r.status == "gaia"


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
            "nearest_known_arcsec": None, "gaia_match": highpm_gaia_match(r), "catwise_match": bool(str(r.catwise_name or "")),
            "labels": sorted(labels, key=LABELS.index), "reasons": rs, "why": highpm_why(r, rs), "flags": [],
            "_sources": [sid] + ([int(r.source_id_b)] if r.method == "pair" and r.source_id_b >= 0 else []),
            "_method": r.method, "_fit_mode": str(getattr(r, "fit_mode", "") or ""),
            "_counterpart": str(getattr(r, "counterpart", "") or ""),
            "_n_common_det": (int(r.n_common_det) if r.method == "pair" and pd.notna(getattr(r, "n_common_det", None))
                              else None),
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


REFCAT_COLUMNS = ["cat", "ra", "dec", "epoch_mjd", "pmra", "pmdec", "g", "g_mag", "phot_g_mean_mag"]
_REFCAT: dict[tuple[str, str], pd.DataFrame | None] = {}
CATALOG_NAMES = {"gaia": "Gaia DR3", "catwise": "CatWISE2020", "tmass": "2MASS", "2mass": "2MASS"}


def refcat_rows(store: Store, zone: str) -> pd.DataFrame | None:
    """refcat/<zone>.parquet (else the forced list) columns REFCAT_COLUMNS, read once per zone and run; None without
    one."""
    key = (str(store.root), zone)
    if key not in _REFCAT:
        rel = next((r.format(zone=zone) for r in (C.REFCAT_REL, C.REFCAT_FALLBACK_REL) if store.exists(r.format(zone=zone))), None)
        _REFCAT[key] = read_columns(store, rel, REFCAT_COLUMNS) if rel is not None else None
    return _REFCAT[key]


def ref_g(ref: pd.DataFrame) -> np.ndarray:
    return next((pd.to_numeric(ref[k], errors="coerce").to_numpy(np.float64) for k in ("g", "g_mag", "phot_g_mean_mag")
                 if k in ref), np.full(len(ref), np.nan))


def zone_catalogs(store: Store, zone: str) -> list[str]:
    """The catalogues the zone's reference catalogue really holds, by name (Gaia DR3, CatWISE2020, 2MASS): the marker
    refcat/<zone>_catalogs.json when written, else the refcat's `cat` values (all 60 ecliptic zones: Gaia only; 16 Nov
    audit). [] when unknown."""
    names: list[str] = []
    try:
        m = store.read_json(f"refcat/{zone}_catalogs.json")
        names = [CATALOG_NAMES.get(str(x).lower(), str(x)) for x in (m.get("catalogs") or [])]
    except (FileNotFoundError, ValueError, OSError, AttributeError):
        ref = refcat_rows(store, zone)
        if ref is not None and "cat" in ref:
            cats = {str(x).lower() for x in pd.unique(ref["cat"].astype(str))}
            names = [v for k, v in CATALOG_NAMES.items() if any(c.startswith(k) for c in cats)]
    order = ["Gaia DR3", "CatWISE2020", "2MASS"]
    return sorted(set(names), key=lambda x: order.index(x) if x in order else len(order))


def gaia_blends(store: Store, zone: str, ids: np.ndarray) -> np.ndarray:
    """The source_ids (of `ids`) with a Gaia DR3 star brighter than BLEND_MAX_G within BLEND_ARCSEC at the source's
    epoch (crossids epoch_mjd, else the photo list's median): refcat/<zone>.parquet moved with its proper motion."""
    from .crossid import ref_offset_at

    ids = np.unique(np.asarray(ids, np.int64))
    ref = refcat_rows(store, zone) if ids.size else None
    if ref is None:
        return np.zeros(0, np.int64)
    g = ref_g(ref)
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
    veto = str(row.get("bd_veto") or "")   # classify (#388): why a would-be brown dwarf isn't one
    if veto == "agn_colours":
        w23 = _fin(row.get("allwise_w2_w3"))
        text += (", but its AllWISE colours are an active galaxy's" + (f" (W2 − W3 = {w23:.1f})" if w23 is not None else "")
                 + ", so not a brown dwarf")
    elif veto == "no_water":
        text += ", but no water shows where its spectrum could show it, so not a brown dwarf"
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
            "_sources": [int(row["source_id"])], "_pah_sig": _opt(row.get("pah_33_sig")),
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
            "_companion": str(r.get("companion_catalog") or "") if _opt(r.get("companion_split"), bool) else "",
        })
    return out


# --- zones -------------------------------------------------------------------------------------------
CLASS_COLUMNS = ["source_id", "class", "labels", "brown_dwarf_like", "bd_evidence", "bd_veto", "allwise_w2_w3", "j_m46_ab",
                 "gaia_sep_arcsec", "gaia_g", "gaia_bp_rp", "gaia_parallax", "gaia_parallax_error",
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
    galaxies = (set(cls.loc[cls["class"].astype(str) == "galaxy", "source_id"].astype(np.int64))
                if len(cls) and "class" in cls else set())
    return {"suspects": susp, "highpm": h, "spectral": spec, "class_labels": cl, "collections": coll,
            "cells": cells, "repeats": reps, "galaxies": galaxies}


TRACK_INFO_COLUMNS = ["mover_id", "tracklet_id", "n_sessions", "n_movers", "n_recovered_sessions", "span_h"]


def zone_controls(steps: dict) -> dict | None:
    """The zone's chance yardsticks for a track (summary.json steps): its real tracks, recovered dots and movers
    (zone_*), tracklets' shuffled control (control_*: tracks, fast_tracks, movers_on_tracks; recovered_dots when every
    track was also moved 2′, #367) and link's shuffled-exposure control (link_control_*: movers, two_exposures, fast;
    #367). Keys present only when measured."""
    tr, lk = steps.get("tracklets") or {}, steps.get("link") or {}
    out: dict = {}
    for k, v in (("zone_tracks", tr.get("tracks")), ("zone_recovered_dots", tr.get("recovered_dots")),
                 ("zone_movers", lk.get("movers"))):
        if isinstance(v, (int, float)):
            out[k] = v
    ctl = tr.get("control")
    if isinstance(ctl, dict):
        for k in ("tracks", "fast_tracks", "movers_on_tracks", "recovered_dots"):
            if isinstance(ctl.get(k), (int, float)):
                out[f"control_{k}"] = ctl[k]
    lc = lk.get("control")
    if isinstance(lc, dict) and "error" not in lc:
        for k in ("movers", "two_exposures", "fast"):
            if isinstance(lc.get(k), (int, float)):
                out[f"link_control_{k}"] = lc[k]
    return out or None


def track_info(r, ctx: dict) -> dict | None:
    """A suspect's tracklets track (tracklet_id >= 0) for the `seen_again` check: tracklet_id, n_sessions, n_movers,
    n_recovered_sessions, span_h (tracklets.parquet via ctx tracks), the track's rate_arcsec_h / pa_deg / rate_snr,
    link's in-session motion (session_rate_arcsec_h / session_pa_deg) and the zone's controls (zone_controls);
    None off a track."""
    tr = on_track(r)
    if tr is None:
        return None
    s_rate, s_pa = table_motion(r)
    out = {"tracklet_id": int(r.tracklet_id), "n_sessions": tr[0], "rate_arcsec_h": tr[1],
           "pa_deg": _fin(getattr(r, "pa_deg", None)), "rate_snr": _fin(getattr(r, "rate_snr", None)),
           "session_rate_arcsec_h": s_rate, "session_pa_deg": s_pa}
    tt = ctx.get("tracks")
    if tt is not None and str(r.id) in tt.index:
        row = tt.loc[str(r.id)]
        for k in ("n_movers", "n_recovered_sessions"):
            v = _fin(row.get(k))
            out[k] = int(v) if v is not None else None
        out["span_h"] = _fin(row.get("span_h"))
    return {**out, **(ctx.get("controls") or {})}


def mover_context(store: Store, zone: str) -> dict:
    """The zone's facts the mover checks need (candidate_checks.mover_checks): motion_ref (its known asteroids' rates
    and directions per visit, tracklets.parquet rows on a track), control_tracks (summary.json's shuffled control),
    photos ((obs_id, detector) of the photo list: was the partner detector's photo taken), bright (Gaia stars brighter
    than BRIGHT_STAR_G from the refcat). Each is None when its file is missing."""
    ctx: dict = {"motion_ref": None, "control_tracks": None, "photos": None, "bright": None, "tracks": None,
                 "controls": None}
    trel = results_rel(zone, "tracklets.parquet")
    try:
        if store.exists(trel):
            t = read_columns(store, trel, ["visit", "tracklet_id", "rate_arcsec_h", "pa_deg"])
            if len(t):
                ctx["motion_ref"] = CK.zone_motion_ref(t["visit"].to_numpy(), t["tracklet_id"].to_numpy() >= 0,
                                                       t["rate_arcsec_h"].to_numpy(), t["pa_deg"].to_numpy())
            tt = read_columns(store, trel, TRACK_INFO_COLUMNS)
            tt = tt[tt["tracklet_id"].to_numpy() >= 0]
            ctx["tracks"] = tt.drop_duplicates("mover_id").set_index("mover_id") if len(tt) else None
    except Exception as e:  # noqa: BLE001  (a malformed table only drops these checks)
        L.warning("candidates %s: tracklets unreadable (%s): no fast / odd_direction / seen_again values", zone, e)
    try:
        steps = store.read_json(results_rel(zone, "summary.json")).get("steps", {})
        ctx["controls"] = zone_controls(steps)
        ctl = (steps.get("tracklets") or {}).get("control")
        ctx["control_tracks"] = int(ctl["tracks"]) if isinstance(ctl, dict) and "tracks" in ctl else None
    except (FileNotFoundError, ValueError, OSError, AttributeError, TypeError):
        pass
    if store.exists(photolist_rel(zone)):
        p = read_columns(store, photolist_rel(zone), ["obs_id", "detector", "mjd"])
        ctx["photos"] = set(zip(p["obs_id"].astype(str), p["detector"].astype(np.int64)))
    ref = refcat_rows(store, zone)
    if ref is not None and len(ref):
        g = ref_g(ref)
        cat = ref["cat"].astype(str).str.lower() if "cat" in ref else pd.Series("gaia", index=ref.index)
        with np.errstate(invalid="ignore"):
            keep = cat.str.contains("gaia").to_numpy() & (g < CK.BRIGHT_STAR_G)
        ctx["bright"] = (SkyTree(ref["ra"].to_numpy(np.float64)[keep], ref["dec"].to_numpy(np.float64)[keep]), g[keep])
    return ctx


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
            c["_saturated"] = any(bool(st.loc[s, "saturated"]) for s in c["_sources"] if s in st.index)
            c["_n_channels"] = _opt(r.get("n_channels"), int)
        if sid in info.index:
            c["_n_dots"] = int(sum(int(info.loc[s, "n_dots"]) for s in c["_sources"] if s in info.index))
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


FRAGMENT_EXTRA = ["source_id", "tag", "n_gaia_8", "blend_sep_arcsec", "blend_dg", "bright_dist_arcsec", "bright_mag",
                  "bright_band"]


def fixed_facts(store: Store, zone: str, cands: list[dict], galaxies: set) -> None:
    """high_pm, spectral and shape: the zone-level facts their checks need, kept on each candidate (`_blend`, `_bright`,
    `_catalogs`, `_galaxy`): fragments.parquet's blend and bright-star columns, the zone's catalogues, classify's class.
    The checks themselves come after every zone (fixed_checks), when the ice groups across zones are known."""
    fixed = [c for c in cands if c["kind"] in ("high_pm", "spectral", "shape")]
    if not fixed:
        return
    rel = results_rel(zone, "fragments.parquet")
    fr = None
    if store.exists(rel):
        try:
            fr = read_columns(store, rel, FRAGMENT_EXTRA).drop_duplicates("source_id").set_index("source_id")
        except Exception as e:  # noqa: BLE001  (an old fragments file without these columns: no blend/bright checks)
            L.warning("candidates %s: fragments columns unreadable (%s)", zone, e)
    cats = zone_catalogs(store, zone)
    for c in fixed:
        c["_catalogs"] = cats
        c["_galaxy"] = any(s in galaxies for s in c["_sources"]) or "class_galaxy" in c["reasons"]
        if fr is None:
            continue
        for s in c["_sources"]:
            if s not in fr.index:
                continue
            r = fr.loc[s]
            if str(r.get("tag") or "") == "blend" and "_blend" not in c:
                c["_blend"] = (_opt(r.get("blend_sep_arcsec")), _opt(r.get("blend_dg")), _opt(r.get("n_gaia_8"), int))
            dist_ = _opt(r.get("bright_dist_arcsec"))
            if dist_ is not None and str(r.get("bright_band") or "") and ("_bright" not in c or dist_ < c["_bright"][2]):
                c["_bright"] = (str(r["bright_band"]), float(r["bright_mag"]), dist_)


def ice_neighbours(cands: list[dict]) -> None:
    """Spectral candidates with an ice label: how many other ice-labelled ones lie within CK.ICE_GROUP_DEG (`_ice_n`)."""
    ice = [c for c in cands if c["kind"] == "spectral" and any(x.endswith("_ice") for x in c["labels"])]
    if not ice:
        return
    tree = SkyTree(np.array([c["ra"] for c in ice]), np.array([c["dec"] for c in ice]))
    for c, hits in zip(ice, tree.query_radius(np.array([c["ra"] for c in ice]), np.array([c["dec"] for c in ice]),
                                              CK.ICE_GROUP_DEG * 3600.0)):
        c["_ice_n"] = len(hits) - 1


def fixed_checks(c: dict) -> list[dict]:
    """The exporter's checks of a high_pm, spectral or shape candidate (candidate_checks), from its kept facts."""
    if c["kind"] == "high_pm":
        return CK.highpm_checks({"reasons": c["reasons"], "pm": c.get("pm_mas_yr"), "method": c.get("_method"),
                                 "fit_mode": c.get("_fit_mode"), "saturated": c.get("_saturated"),
                                 "n_dots": c.get("_n_dots"), "blend": c.get("_blend"), "det": c.get("det"),
                                 "gaia_match": c.get("gaia_match"), "n_common_det": c.get("_n_common_det"),
                                 "counterpart": c.get("_counterpart")})
    if c["kind"] == "spectral":
        return CK.spectral_checks({"labels": c["labels"], "pah_sig": c.get("_pah_sig"), "ice_neighbours": c.get("_ice_n"),
                                   "n_channels": c.get("_n_channels"), "bright": c.get("_bright"),
                                   "blend": c.get("_blend"), "catalogs": c.get("_catalogs"),
                                   "catwise_match": c.get("catwise_match"), "det": c.get("det")})
    if c["kind"] == "shape":
        return CK.shape_checks({"reasons": c["reasons"], "galaxy": c.get("_galaxy"), "bright": c.get("_bright"),
                                "blend": c.get("_blend"), "companion_catalog": c.get("_companion"), "det": c.get("det")})
    return c.get("checks") or []


def sort_key(order: dict[str, int]):
    return lambda c: (order.get(c["zone"], len(order)), KINDS.index(c["kind"]), GRADE_RANK.get(c.get("grade"), 1),
                      -(c["score"] or 0.0), c["id"])


def build(store: Store, zones: list[str], frames_root: Path | None = None, workers: int = 32,
          spectral_cap: int = SPECTRAL_CAP, checks_dir: Path | None = None) -> dict:
    """The candidates.json document for these zones, in this order. checks_dir: the check producers' side files
    (candidate_checks.merge_side_files), merged before the final sort."""
    kept: list[dict] = []
    collections: set[str] = set()
    used_zones = []
    for z in zones:
        t0 = time.time()
        use_dots_dir(linked_dots_dir(store, z))  # the zone's dot files: the folder its link read (members.dot_row)
        b = zone_base(store, z, spectral_cap)
        collections.update(b["collections"])
        cands = movers(b["suspects"], z, None, b["cells"], b["repeats"]) + high_pms(b["highpm"], z, b["class_labels"]) + spectrals(b["spectral"], z)
        cands, planets = drop_planet_movers(cands)
        if planets:
            L.info("candidates %s: %d movers near a planet (the planet, its moons, its glare) not listed: %s", z,
                   sum(planets.values()), planets)
        cands += shape_candidates(store, z)
        from . import fragments  # results/<zone>/fragments.parquet: bright_fragment, duplicate never listed; blend: a reason
        tags = fragments.load(store, z, read=lambda rel, columns: read_columns(store, rel, columns))
        cands, fst = fragments.apply_candidates(cands, tags)
        if fst["applied"]:
            L.info("candidates %s: fragments tags: %s", z, fst)
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
            more = more_rows(susp)  # grade C: the suspects row alone, never their dot files
            sd, sm = susp[~more].reset_index(drop=True), susp[more].reset_index(drop=True)
            need = needs_positions(sd)  # A/B: positions only where the table lacks link's in-session motion
            t1 = time.time()
            near = mover_dots(store, sd, workers, need) if need.any() else None
            flags3 = mover_dot_flags(store, sd, workers) if len(sd) else None  # the dots' 3×3 flags (a small column)
            ctx = mover_context(store, z)
            L.info("candidates %s: %d grade A/B movers (dot flags; positions for %d without an in-session motion) in "
                   "%.0f s; %d grade C from the table alone", z, len(sd), int(need.sum()), time.time() - t1, len(sm))
            full = {c["id"]: c for c in movers(sd, z, near, b["cells"], b["repeats"], ctx, flags3)
                    + movers(sm, z, None, b["cells"], b["repeats"], ctx, None)}
            cands = [full.get(c["id"], c) if c["kind"] == "mover" else c for c in cands]
        fill_sources(store, z, cands, workers)
        fixed_facts(store, z, cands, b.get("galaxies") or set())
        _REFCAT.pop((str(store.root), z), None)
        kept += cands
        L.info("candidates %s: %d kept in %.0f s", z, len(cands), time.time() - t0)
    gb = galactic_b([c["ra"] for c in kept], [c["dec"] for c in kept])
    for c, b_ in zip(kept, gb):
        c["galactic_b"] = float(b_)
    ice_neighbours(kept)
    for c in kept:
        if c["kind"] != "mover":
            c["_base"] = c["score"]
            c["checks"] = fixed_checks(c)
            set_status(c)
    if checks_dir is not None:
        L.info("candidates: side files merged from %s: %s", checks_dir,
               CK.merge_side_files(kept, CK.load_side_files(checks_dir)))
    L.info("candidates: rb_score order inside each tier: %s", RB.reorder(kept))
    order = {z: i for i, z in enumerate(used_zones)}
    kept.sort(key=sort_key(order))
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


MORE_DIR = "candidates-more"   # next to candidates.json: <out dir>/candidates-more/<zone>.json
MORE_GRADES = ("C",)           # movers of these grades go to the more files (unless status known or flaw)


def more_rows(susp: pd.DataFrame) -> np.ndarray:
    """Suspects rows whose movers go to the more files (grade C): built from the row alone, no dot file read."""
    if "grade" not in susp or not len(susp):
        return np.zeros(len(susp), bool)
    return susp["grade"].astype(str).isin(MORE_GRADES).to_numpy()


def in_more(c: dict) -> bool:
    """A listed candidate that goes to its zone's more file: a grade C mover still open (a known or flaw one stays in
    candidates.json, where the site shows what was resolved)."""
    return c["kind"] == "mover" and c.get("grade") in MORE_GRADES and c.get("status", "open") == "open"


def split_more(doc: dict) -> tuple[dict, dict[str, dict]]:
    """candidates.json and its more files from one full document (contracts/api.md v3.24 "Less likely movers"): grade C
    open movers go to {zone: more doc} (the same candidate objects in list order, i.e. by score; frames_url null); the
    main file keeps the rest (known and flaw ones of any grade too), its `counts` count what it lists and `more`
    indexes the files ({zone, kind, grade, count, url}, url relative to candidates.json; zones in `zones` order; a zone
    with none has neither). The zones' dedup ran before (build), so an id is in exactly one file."""
    head = {k: v for k, v in doc.items() if k not in ("candidates", "more", "counts")}
    main, more = [], {}
    for c in doc["candidates"]:
        if in_more(c):
            more.setdefault(c["zone"], []).append({**c, "frames_url": None})  # static frames: candidates.json's best
        else:
            main.append(c)
    order = list(doc.get("zones") or [])
    zones = [z for z in order if z in more] + sorted(z for z in more if z not in order)
    index = [{"zone": z, "kind": "mover", "grade": "C", "count": len(more[z]), "url": f"{MORE_DIR}/{z}.json"}
             for z in zones]
    out = {**head, "counts": {k: sum(c["kind"] == k for c in main) for k in KINDS}, "more": index, "candidates": main}
    files = {z: {"generated": doc.get("generated"), "data_release": doc.get("data_release"), "zone": z, "kind": "mover",
                 "grade": "C", "count": len(more[z]), "candidates": more[z]} for z in zones}
    return out, files


def load_more(doc: dict, folder: Path) -> list[dict]:
    """The candidates of an export's more files (doc['more'] urls, relative to folder: candidates.json's own); a
    missing file is skipped with a warning (its candidates are then gone from a rewrite)."""
    out = []
    for e in doc.get("more") or []:
        p = folder / str(e.get("url") or f"{MORE_DIR}/{e.get('zone')}.json")
        if not p.exists():
            L.warning("candidates: more file %s missing: its %s candidates are not merged", p, e.get("count"))
            continue
        out += json.loads(p.read_text())["candidates"]
    return out


def write_export(doc: dict, out: Path) -> dict[str, float]:
    """Writes the full document as out (candidates.json) and out.parent/candidates-more/<zone>.json (split_more); a
    more file of one of the doc's zones that no longer has any is removed. Returns {file name: MB}."""
    main, files = split_more(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(main))
    sizes = {out.name: out.stat().st_size / 1e6}
    folder = out.parent / MORE_DIR
    if files:
        folder.mkdir(parents=True, exist_ok=True)
    for z, d in files.items():
        p = folder / f"{z}.json"
        p.write_text(dumps(d))
        sizes[f"{MORE_DIR}/{p.name}"] = p.stat().st_size / 1e6
    for z in doc.get("zones") or []:
        p = folder / f"{z}.json"
        if z not in files and p.exists():
            p.unlink()
    return sizes


def finish(c: dict) -> dict:
    """The contract's fields in order, rounded; NaN -> null."""
    nd = {"ra": 6, "dec": 6, "mjd": 6, "score": 3, "rb_score": 3, "snr": 1, "flux_ujy": 1, "rate_arcsec_h": 1, "pm_mas_yr": 1,
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
        elif k == "status":
            v = v if v in CK.STATUSES else "open"
        elif k == "checks":
            v = [CK.clean_check(x) for x in CK.sort_checks(list(v or []))]
        out[k] = v
    return out


def merge_only_doc(doc: dict, checks_dir: Path, more: list[dict] | None = None) -> tuple[dict, dict]:
    """An existing candidates.json (plus `more`: its more files' candidates, load_more) with the side files of
    checks_dir merged (candidate_checks.merge_side_files): older files gain status open, known_name null and checks [];
    scores re-weighed by the change in their checks' factors; re-sorted (contract order); every other field as it was.
    Returns the full document (write_export splits it again: a grade C mover made known or flaw moves to the main file)."""
    cands = [dict(c) for c in list(doc["candidates"]) + list(more or [])]
    for c in cands:
        c.setdefault("status", "open")
        c.setdefault("known_name", None)
        c["checks"] = [dict(x) for x in (c.get("checks") or [])]
    st = CK.merge_side_files(cands, CK.load_side_files(checks_dir))
    st["rb_score"] = RB.reorder(cands)  # after the merge: a check may have moved a mover to another tier
    order = {z: i for i, z in enumerate(doc.get("zones") or [])}
    cands.sort(key=sort_key(order))
    head = {k: v for k, v in doc.items() if k != "more"}
    return {**head, "counts": {k: sum(c["kind"] == k for c in cands) for k in KINDS},
            "candidates": [finish(c) for c in cands]}, st


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
    ap.add_argument("--full-reads", action="store_true",
                    help="read members.parquet's whole columns, not only the pages holding the candidates' rows")
    ap.add_argument("--drop-planets", metavar="JSON",
                    help="only remove the movers near a planet from this existing candidates.json, in place (counts "
                         "updated, nothing else changed); reads no zone")
    ap.add_argument("--checks-dir", help="the check producers' side files (<dir>/*.json, contracts/api.md v3.23 checks): "
                                         "merged into the export (a producer's check replaces the exporter's of the same code)")
    ap.add_argument("--merge-only", metavar="JSON",
                    help="re-apply --checks-dir to this existing candidates.json without reading any zone; writes --out")
    a = ap.parse_args(argv)
    log.setup()
    if a.merge_only:
        if not a.checks_dir:
            ap.error("--merge-only needs --checks-dir")
        src = Path(a.merge_only)
        doc = json.loads(src.read_text())
        doc, st = merge_only_doc(doc, Path(a.checks_dir), load_more(doc, src.parent))
        out = Path(a.out)
        sizes = write_export(doc, out)
        L.info("merged %s into %s (and its more files) -> %s: %s; status %s; files %s", a.checks_dir, src, out, st,
               {s: sum(c["status"] == s for c in doc["candidates"]) for s in CK.STATUSES},
               {k: round(v, 2) for k, v in sizes.items()})
        return 0
    if a.drop_planets:
        path = Path(a.drop_planets)
        doc, by = drop_planets_doc(json.loads(path.read_text()))
        path.write_text(dumps(doc))
        L.info("dropped %d movers near a planet from %s: %s; counts now %s", sum(by.values()), path, by, doc["counts"])
        return 0
    global CACHE_DIR, SLIM_READS
    CACHE_DIR = Path(a.cache) if a.cache else None
    SLIM_READS = not a.full_reads
    store = Store(a.root)
    zones = a.zones.split(",") if a.zones else finished_zones(store)
    if not zones:
        L.error("no finished zone under %s", store.root)
        return 1
    out = Path(a.out)
    doc = build(store, zones, out.parent, a.workers, a.spectral_cap, Path(a.checks_dir) if a.checks_dir else None)
    sizes = write_export(doc, out)
    L.info("wrote %s: %s, zones %s, %.2f MB; more files (grade C movers) %d, %.2f MB", out, doc["counts"], doc["zones"],
           sizes[out.name], len(sizes) - 1, sum(v for k, v in sizes.items() if k != out.name))
    from .parquet_rows import STATS
    L.info("parquet reads (not from --cache): %d files by columns, %d by pages (their whole columns %.1f MB); %.1f MB "
           "in %d range requests", STATS["tables"], STATS["files"], STATS["full_bytes"] / 1e6, STATS["bytes"] / 1e6,
           STATS["requests"])
    if a.fixture:
        Path(a.fixture).write_text(json.dumps(sample(split_more(doc)[0]), ensure_ascii=False, indent=1,
                                              allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
