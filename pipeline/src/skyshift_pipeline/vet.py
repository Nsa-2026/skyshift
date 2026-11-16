"""Vetting of the suspects: gates, grades and reasons (thresholds: config VET_*).

Runs in `suspects` on the movers left after the certain removals (a known body, a planet, a hot pixel that stayed on one
pixel while the sky moved), never before link's pairing, so known-asteroid recall is untouched. A gate never removes:
it grades C (kept, ranked last, with its reasons). Each gate looks at the suspect's own dots (link's dot lists in
movers.parquet) or at zone-level maps:

- hot_pixel     a dot on a detector pixel (±1 px) where free dots (freedots.parquet: every dot movers are paired from)
                at OTHER sky places (> 30″ from the dot) lie in >= 5 exposures, or every dot (>= 2) on such a pixel
                with >= 3 (hot_pixel_proven: 0 of 6,125 real asteroids). Repeats at one sky place are a source (a
                star, Europa), not the detector. Without ra/dec in freedots: the old map (movers' dots in >= 3
                exposures, free dots in >= 5). Hot pixels NASA's HOT bit misses pair up with another such pixel one to
                three exposure steps (~115 px in y) away and make "movers".
- same_pixel    dots of two exposures within 1 px of one detector pixel: only a session without the telescope's step
                gives it (ecl17 session 201), and there a hot pixel stays on one sky spot.
- whole_pixel   >= 2 dots on a whole pixel (x and y within 0.02 px): a centroid fallback, not a measurement.
- wide          every unsaturated dot (>= 2) >= 1.3× the detector's stellar size at its S/N, times the trail's growth at
                its own in-session rate (trail_factor). A warning: a comet's haze or two faint things blended; a reason
                only with a blend (suspects.mover_flaws, config VET_WIDE_WITH), never alone.
- cross_detector >= 2 exposures and no detector holds dots of two of them (D1 then D4: colour read as motion).
- fixed_source, glow, unseen   decided in suspects.mover_flaws (a fixed source or Gaia star at a mover that may not
                move, a broad glow near a G < 7 star, exposures that hold the track but not the mover) and passed in.
                Numbers for all of these: flaw_numbers (FLAW_COLUMNS); real vs rest shares: config, mover flaws.
- bad_column    a dot on a column (±0.5 px in x) holding >= 3 free dots of the same exposure and detector, and more
                than chance gives at that photo's free-dot density (Poisson tail <= 0.1%; ecliptic photos hold up to
                ~800 free dots: >= 3 then caught 12% of the real asteroids, now 0.3%).
- edge          a dot within 6 px of the detector edge (hunt's former #1 candidate sat at x 0.6–0.7, half off the frame).
- masked_peak   a mask bit (contracts/flags.md, saturation aside) at a dot's peak: TRANSIENT, PERSIST, SUR_ERROR, ...
- sharp_outlier a dot sharper than a star (√(a·b) < 0.7 × (D1–D3: 0.6 ×) the detector's stellar size at its S/N) with
                OUTLIER at the peak: cosmic-ray-like. OUTLIER alone is set on half the real asteroids' dots: never alone.
- very_sharp    a dot under 0.4 × the stellar size at S/N >= 15, with or without OUTLIER: no point source is that sharp.
- flux_ratio    the brightest dot > 4× the faintest within one detector (a hit with a faint partner).
- red_photo     every dot from a photo that qa.py graded red (results/<zone>/qa_photos.parquet; skipped without it).
- bloom, halo   NASA's BLOOM / HALO bit at every dot's peak (suspects.flag_warnings; in a dot's 3×3 only a warning).
- bright_star   a fixed source with S/N >= 100 within 15″ (to 30″ only a warning).
- gaia, catwise, 2mass, allwise   a catalogue source within 6″ at most 2 mag fainter than the suspect (AB; a fainter
                one is a chance neighbour: the catalogue_star warning only). AllWISE (S3) where CatWISE2020 is the zone's
                WISE catalogue too, or instead of it in a Gaia-only refcat.

Grades: C = fails a gate or is catalogued (kept, with its reasons); B = passes everything, motion not shown (most suspects
are 2 dots of one session: at S/N 7–9 the centroid error exceeds the motion between exposures); A = B plus motion
>= 5σ over >= 2 sessions: on a `tracklets` track (suspects.track_grades, after vet; vet itself gives only B and C); a
track faster than VET_A_FAST_ARCSEC_H (400″/h) also needs a recovered dot (not inside a star's group) or a detector
partner (D1+D4, D2+D5, D3+D6 in one exposure), else it stays B: fast 2-session tracks link single-detector artefacts.
Such a track also overrules the single-session separators (VET_TRACK_OVERRULES: fixed_source, unseen, wide, whole_pixel,
cross_detector, glow): grade A, reasons cleared, the checks stay warnings.
`near_source_arcsec` (a refcat or fixed source 6–10″ away: a possible blend or PSF wing) is a warning, not a gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import config as C
from . import log
from .store import Store, results_rel

L = log.get("vet")

DOT_LISTS = ("dot_obs", "dot_det", "dot_x", "dot_y", "dot_mjd", "dot_flux", "dot_snr", "dot_size", "dot_chi2", "dot_fpk")


def has_dot_lists(m: pd.DataFrame) -> bool:
    return all(c in m.columns for c in DOT_LISTS)


def explode(m: pd.DataFrame) -> pd.DataFrame:
    """One row per dot of each mover: i (row in m), obs, det, x, y, mjd, flux, snr, size, chi2, fpk."""
    cols = {"obs": "dot_obs", "det": "dot_det", "x": "dot_x", "y": "dot_y", "mjd": "dot_mjd", "flux": "dot_flux",
            "snr": "dot_snr", "size": "dot_size", "chi2": "dot_chi2", "fpk": "dot_fpk"}
    if len(m) == 0 or not has_dot_lists(m):
        return pd.DataFrame({"i": pd.Series(dtype=np.int64), **{k: pd.Series(dtype=float) for k in cols}})
    n = np.array([len(v) for v in m["dot_det"]], np.int64)
    out = {"i": np.repeat(np.arange(len(m), dtype=np.int64), n)}
    for k, c in cols.items():
        vals = [np.asarray(v) for v in m[c]]
        out[k] = np.concatenate(vals) if len(vals) else np.zeros(0)
    d = pd.DataFrame(out)
    d["obs"] = d["obs"].astype(str)
    d["det"] = d["det"].astype(np.int64)
    d["fpk"] = d["fpk"].astype(np.int64) & 0xFFFFFFFF
    for c in ("x", "y", "mjd", "flux", "snr", "size", "chi2"):
        d[c] = d[c].astype(np.float64)
    return d


def star_size(det: np.ndarray, snr: np.ndarray) -> np.ndarray:
    """The detector's stellar √(a·b) (arcsec) at that S/N (config VET_STAR_SIZE_ARCSEC); NaN for an unknown detector."""
    det = np.asarray(det, np.int64)
    b = np.clip(np.searchsorted(np.asarray(C.VET_STAR_SNR_EDGES), np.asarray(snr, np.float64), side="right") - 1,
                0, len(C.VET_STAR_SNR_EDGES) - 1)
    out = np.full(det.size, np.nan)
    for d, row in C.VET_STAR_SIZE_ARCSEC.items():
        m = det == d
        out[m] = np.asarray(row)[b[m]]
    return out


def pixel_exposures(q: pd.DataFrame, ref: pd.DataFrame, r_px: float = C.VET_HOT_PX) -> np.ndarray:
    """For each query dot (det, x, y): the number of distinct exposures (ref `obs`) with a ref dot on the same detector
    within r_px in both x and y (the query's own dot counts when it is in ref)."""
    out = np.zeros(len(q), np.int64)
    if len(q) == 0 or len(ref) == 0:
        return out
    ref = ref[np.isfinite(ref["x"].to_numpy(np.float64)) & np.isfinite(ref["y"].to_numpy(np.float64))]
    qd = q["det"].to_numpy(np.int64)
    qxy = np.c_[q["x"].to_numpy(np.float64), q["y"].to_numpy(np.float64)]
    rdet = ref["det"].to_numpy(np.int64)
    rcode = pd.factorize(ref["obs"].astype(str))[0]
    rxy = np.c_[ref["x"].to_numpy(np.float64), ref["y"].to_numpy(np.float64)]
    for d in np.unique(qd):
        qi = np.flatnonzero((qd == d) & np.isfinite(qxy).all(axis=1))
        ri = np.flatnonzero(rdet == d)
        if qi.size == 0 or ri.size == 0:
            continue
        tree = cKDTree(rxy[ri])
        for k, nb in zip(qi, tree.query_ball_point(qxy[qi], r_px + 1e-6, p=np.inf)):
            out[k] = np.unique(rcode[ri[nb]]).size if nb else 0
    return out


def column_dots(q: pd.DataFrame, free: pd.DataFrame, r_px: float = C.VET_COLUMN_PX) -> np.ndarray:
    """For each query dot (obs, det, x): free dots of the same exposure and detector within r_px in x."""
    out = np.zeros(len(q), np.int64)
    if len(q) == 0 or len(free) == 0:
        return out
    groups = {k: np.sort(g["x"].to_numpy(np.float64)) for k, g in free.groupby([free["obs"].astype(str), free["det"].astype(np.int64)])}
    for k, (o, d, x) in enumerate(zip(q["obs"].astype(str), q["det"].to_numpy(np.int64), q["x"].to_numpy(np.float64))):
        xs = groups.get((o, int(d)))
        if xs is None or not np.isfinite(x):
            continue
        out[k] = int(np.searchsorted(xs, x + r_px + 1e-6, side="right") - np.searchsorted(xs, x - r_px - 1e-6, side="left"))
    return out


def column_need(q: pd.DataFrame, free: pd.DataFrame, r_px: float = C.VET_COLUMN_PX) -> np.ndarray:
    """For each query dot: the free dots (itself included) a bad column needs in its photo: at least VET_COLUMN_MIN_DOTS,
    and more where the photo's free dots are so dense that chance alone fills a column: the smallest count whose
    Poisson tail at that photo's density (free dots × 2·r_px / detector width) is <= VET_COLUMN_CHANCE_P."""
    from scipy.stats import poisson
    need = np.full(len(q), C.VET_COLUMN_MIN_DOTS, np.int64)
    if len(q) == 0 or len(free) == 0:
        return need
    nfree = free.groupby([free["obs"].astype(str), free["det"].astype(np.int64)]).size()
    n = pd.Series(list(zip(q["obs"].astype(str), q["det"].astype(np.int64)))).map(nfree).fillna(0).to_numpy(np.float64)
    lam = np.maximum(n - 1.0, 0.0) * (2.0 * r_px) / C.VET_DETECTOR_PX  # other free dots expected in the column
    others = poisson.isf(C.VET_COLUMN_CHANCE_P, lam) + 1.0  # P(N >= others) <= VET_COLUMN_CHANCE_P
    return np.maximum(need, np.nan_to_num(others, nan=0.0).astype(np.int64) + 1)


def load_free(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    """freedots.parquet as obs, det, x, y and, where the file has them, ra, dec (None if link didn't write it)."""
    from .link import FREEDOTS_NAME
    rel = results_rel(zone.name, FREEDOTS_NAME)
    if not store.exists(rel):
        return None
    try:
        f = store.read_parquet(rel, columns=["obs_id", "detector", "x", "y", "ra", "dec"])
    except Exception:  # noqa: BLE001  (an older file without ra/dec: the hot-pixel map then counts every exposure)
        f = store.read_parquet(rel, columns=["obs_id", "detector", "x", "y"])
    out = pd.DataFrame({"obs": f["obs_id"].astype(str).to_numpy(), "det": f["detector"].to_numpy(np.int64),
                        "x": f["x"].to_numpy(np.float64), "y": f["y"].to_numpy(np.float64)})
    if "ra" in f and "dec" in f:
        out["ra"], out["dec"] = f["ra"].to_numpy(np.float64), f["dec"].to_numpy(np.float64)
    return out


def away_exposures(d: pd.DataFrame, free: pd.DataFrame, ra: np.ndarray, dec: np.ndarray, r_px: float = C.VET_HOT_PX,
                   away_arcsec: float = C.VET_HOT_AWAY_ARCSEC) -> np.ndarray:
    """For each dot of d (explode's rows; ra, dec: its mover's sky place, per dot): the distinct exposures of free dots on
    the same detector pixel (±r_px in x and y) at sky places more than away_arcsec from it. The same pixel lit while
    looking at other sky is the detector; repeats at the same sky place are a source (a star, Europa)."""
    from .sky import angsep
    out = np.zeros(len(d), np.int64)
    if len(d) == 0 or free is None or len(free) == 0 or "ra" not in free:
        return out
    ok = np.isfinite(free["x"].to_numpy(np.float64)) & np.isfinite(free["y"].to_numpy(np.float64))
    fdet = free["det"].to_numpy(np.int64)[ok]
    fxy = np.c_[free["x"].to_numpy(np.float64)[ok], free["y"].to_numpy(np.float64)[ok]]
    fra, fdec = free["ra"].to_numpy(np.float64)[ok], free["dec"].to_numpy(np.float64)[ok]
    fobs = pd.factorize(free["obs"].astype(str).to_numpy()[ok])[0]
    qd = d["det"].to_numpy(np.int64)
    qxy = np.c_[d["x"].to_numpy(np.float64), d["y"].to_numpy(np.float64)]
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    for det in np.unique(qd):
        qi = np.flatnonzero((qd == det) & np.isfinite(qxy).all(axis=1))
        ri = np.flatnonzero(fdet == det)
        if qi.size == 0 or ri.size == 0:
            continue
        tree = cKDTree(fxy[ri])
        for k, nb in zip(qi, tree.query_ball_point(qxy[qi], r_px + 1e-6, p=np.inf)):
            if not nb:
                continue
            nb = ri[np.asarray(nb)]
            far = angsep(ra[k], dec[k], fra[nb], fdec[nb]) * 3600.0 > away_arcsec
            out[k] = np.unique(fobs[nb[far]]).size
    return out


def whole_pixel(x, y) -> np.ndarray:
    """True where a dot's x and y both lie within VET_WHOLE_PX of a whole pixel: a centroid fallback, not a measurement."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    return (np.abs(x - np.round(x)) < C.VET_WHOLE_PX) & (np.abs(y - np.round(y)) < C.VET_WHOLE_PX)


def dot_sigma(snr, whole=None) -> np.ndarray:
    """A dot's 1σ position error (″): link.dot_sigma_arcsec from its S/N, with a whole-pixel dot's rounding (0.3 px,
    LINK_WHOLE_PIXEL_SIGMA_PX) added in quadrature."""
    from .link import dot_sigma_arcsec
    sig = dot_sigma_arcsec(snr)
    if whole is None:
        return sig
    return np.where(np.asarray(whole, bool), np.hypot(sig, C.LINK_WHOLE_PIXEL_SIGMA_PX * C.PIXEL_ARCSEC), sig)


FLAW_COLUMNS = ("hot_exposures_max", "hot_exposures_min", "whole_dots", "wide_ratio_med", "wide_ratio_min", "wide_dots",
                "wide_trail", "same_pixel_px", "cross_detector", "motion_sigma")


def trail_factor(rate_arcsec_h) -> np.ndarray:
    """How much a dot's √(a·b) grows when the source trails rate × VET_EXPOSURE_S during one exposure: a Gaussian PSF
    (σ = PSF_SIGMA_ARCSEC) smeared over L has variance σ² + L²/12 along the motion, σ² across, so √(a·b) grows by
    (1 + L²/12σ²)^¼ (elong a/b by its square root). 1 where the rate is unknown. 150″/h: 1.04; 400″/h: 1.23; 900″/h: 1.65."""
    r = np.nan_to_num(np.asarray(rate_arcsec_h, np.float64), nan=0.0)
    L = np.abs(r) * C.VET_EXPOSURE_S / 3600.0
    return (1.0 + L * L / (12.0 * C.PSF_SIGMA_ARCSEC ** 2)) ** 0.25


def flaw_numbers(s: pd.DataFrame, free: pd.DataFrame | None) -> pd.DataFrame:
    """Per suspect (row of s, from its dot lists): the numbers of the mover-flaw separators (config, mover flaws).
    hot_exposures_max / _min: over its dots, the free dots' exposures on the dot's pixel at other sky places
    (away_exposures; NaN without free dots with ra/dec); whole_dots: dots on a whole pixel; wide_ratio_med / _min: the
    median and smallest size / (stellar size × wide_trail) of its unsaturated dots, wide_dots how many those are,
    wide_trail the trail's growth at its in-session rate (session_rate_arcsec_h, else rate_arcsec_h); same_pixel_px: the
    smallest pixel distance (max of |Δx|, |Δy|) between dots of two exposures on one detector (NaN: none);
    cross_detector: >= 2 exposures and no detector with dots of two of them; motion_sigma: link's rate (rate_arcsec_h)
    over its 1σ from the dots' times and S/N (weighted line fit; whole-pixel dots as in dot_sigma), NaN with one
    exposure or no rate."""
    n = len(s)
    out = pd.DataFrame({"hot_exposures_max": np.full(n, np.nan), "hot_exposures_min": np.full(n, np.nan),
                        "whole_dots": np.zeros(n, np.int64), "wide_ratio_med": np.full(n, np.nan),
                        "wide_ratio_min": np.full(n, np.nan), "wide_dots": np.zeros(n, np.int64),
                        "wide_trail": np.ones(n), "same_pixel_px": np.full(n, np.nan), "cross_detector": np.zeros(n, bool),
                        "motion_sigma": np.full(n, np.nan)})
    if n == 0 or not has_dot_lists(s):
        return out
    s = s.reset_index(drop=True)
    d = explode(s)
    if len(d) == 0:
        return out
    i = d["i"].to_numpy()
    idx = pd.RangeIndex(n)
    if free is not None and "ra" in free:
        away = away_exposures(d, free, s["ra"].to_numpy(np.float64)[i], s["dec"].to_numpy(np.float64)[i])
        a = pd.Series(away).groupby(i)
        out["hot_exposures_max"] = a.max().reindex(idx).to_numpy(np.float64)
        out["hot_exposures_min"] = a.min().reindex(idx).to_numpy(np.float64)
    whole = whole_pixel(d["x"], d["y"])
    out["whole_dots"] = pd.Series(whole).groupby(i).sum().reindex(idx).fillna(0).astype(np.int64).to_numpy()
    rc = "session_rate_arcsec_h" if "session_rate_arcsec_h" in s else "rate_arcsec_h"
    trail = trail_factor(pd.to_numeric(s[rc], errors="coerce").to_numpy(np.float64)) if rc in s else np.ones(n)
    out["wide_trail"] = trail
    ratio = d["size"].to_numpy(np.float64) / (star_size(d["det"].to_numpy(), d["snr"].to_numpy()) * trail[i])
    unsat = (d["fpk"].to_numpy(np.int64) & C.VET_SAT_BITS) == 0
    r = pd.Series(np.where(unsat & np.isfinite(ratio), ratio, np.nan)).groupby(i)
    out["wide_ratio_med"] = r.median().reindex(idx).to_numpy(np.float64)
    out["wide_ratio_min"] = r.min().reindex(idx).to_numpy(np.float64)
    out["wide_dots"] = r.count().reindex(idx).fillna(0).astype(np.int64).to_numpy()
    x, y, obs, det = d["x"].to_numpy(np.float64), d["y"].to_numpy(np.float64), d["obs"].to_numpy(), d["det"].to_numpy()
    sp = np.full(n, np.nan)
    cross = np.zeros(n, bool)
    for k, rows in pd.Series(np.arange(len(d))).groupby(i).groups.items():
        rows = np.asarray(rows)
        if np.unique(obs[rows]).size < 2:
            continue
        per = []
        for dd in np.unique(det[rows]):
            rr = rows[det[rows] == dd]
            per.append(np.unique(obs[rr]).size)
            for a_ in range(rr.size):
                for b_ in range(a_ + 1, rr.size):
                    if obs[rr[a_]] == obs[rr[b_]]:
                        continue
                    dpx = max(abs(x[rr[a_]] - x[rr[b_]]), abs(y[rr[a_]] - y[rr[b_]]))
                    if np.isfinite(dpx) and (np.isnan(sp[k]) or dpx < sp[k]):
                        sp[k] = dpx
        cross[k] = max(per) < 2
    out["same_pixel_px"] = sp
    out["cross_detector"] = cross
    out["motion_sigma"] = motion_sigma(s, d, whole)
    return out


def motion_sigma(s: pd.DataFrame, d: pd.DataFrame | None = None, whole: np.ndarray | None = None) -> np.ndarray:
    """Per suspect: link's rate (rate_arcsec_h) over its 1σ: one position per exposure (the mean of its dots, σ from
    dot_sigma), a weighted straight line in time, the per-axis error of its slope. NaN with < 2 exposures or no rate."""
    n = len(s)
    out = np.full(n, np.nan)
    if n == 0 or "rate_arcsec_h" not in s or not has_dot_lists(s):
        return out
    if d is None:
        d = explode(s.reset_index(drop=True))
        whole = whole_pixel(d["x"], d["y"])
    rate = pd.to_numeric(s["rate_arcsec_h"], errors="coerce").to_numpy(np.float64)
    sig = dot_sigma(d["snr"].to_numpy(np.float64), whole)
    e = pd.DataFrame({"i": d["i"].to_numpy(), "t": np.round(d["mjd"].to_numpy(np.float64) * 86400.0), "h": d["mjd"].to_numpy(np.float64) * 24.0,
                      "v": sig ** 2})
    g = e.groupby(["i", "t"]).agg(h=("h", "mean"), v=("v", "sum"), k=("v", "size")).reset_index()
    g["w"] = g["k"] ** 2 / g["v"]  # 1 / σ² of an exposure's mean position
    for k, gg in g.groupby("i"):
        if len(gg) < 2 or not np.isfinite(rate[k]):
            continue
        w, h = gg["w"].to_numpy(), gg["h"].to_numpy()
        hm = np.sum(w * h) / np.sum(w)
        sxx = np.sum(w * (h - hm) ** 2)
        if sxx > 0:
            out[k] = rate[k] * np.sqrt(sxx)
    return out


def load_red(store: Store, zone: C.Zone) -> set | None:
    """{(obs_id, detector)} of the photos qa.py graded red; None if results/<zone>/qa_photos.parquet doesn't exist."""
    rel = results_rel(zone.name, C.VET_QA_NAME)
    if not store.exists(rel):
        return None
    try:
        q = store.read_parquet(rel, columns=["obs_id", "detector", "grade"])
    except Exception as e:  # noqa: BLE001  (a malformed QA file must not stop the suspects)
        L.warning("vet %s: %s unreadable, red_photo gate skipped: %s", zone.name, rel, e)
        return None
    r = q[q["grade"].astype(str) == "red"]
    return set(zip(r["obs_id"].astype(str), r["detector"].astype(int)))


def dot_gates(d: pd.DataFrame, movers_dots: pd.DataFrame, free: pd.DataFrame | None, red: set | None,
              hot: bool = True) -> pd.DataFrame:
    """Per-dot gate booleans for the suspects' dots d (explode's rows). hot False: no hot_pixel here (vet decides it
    from flaw_numbers when the free dots have sky places)."""
    g = pd.DataFrame(index=d.index)
    x, y = d["x"].to_numpy(np.float64), d["y"].to_numpy(np.float64)
    hi = C.VET_DETECTOR_PX - 1 - C.VET_EDGE_PX
    g["edge"] = (x < C.VET_EDGE_PX) | (x > hi) | (y < C.VET_EDGE_PX) | (y > hi)
    h = pixel_exposures(d, movers_dots) >= C.VET_HOT_MIN_EXP_MOVERS if hot else np.zeros(len(d), bool)
    if free is not None:
        if hot:
            h |= pixel_exposures(d, free) >= C.VET_HOT_MIN_EXP_FREE
        g["bad_column"] = column_dots(d, free) >= column_need(d, free)
    else:
        g["bad_column"] = False
    g["hot_pixel"] = h
    fpk = d["fpk"].to_numpy(np.int64)
    g["masked_peak"] = (fpk & C.VET_PEAK_BITS) != 0
    size, star = d["size"].to_numpy(np.float64), star_size(d["det"].to_numpy(), d["snr"].to_numpy())
    frac = np.array([C.VET_SHARP_FRAC.get(int(k), 0.7) for k in d["det"].to_numpy()], np.float64)
    g["sharp_outlier"] = (size < frac * star) & ((fpk & C.VET_OUTLIER_BIT) != 0)
    g["very_sharp"] = (size < C.VET_VERY_SHARP_FRAC * star) & (d["snr"].to_numpy(np.float64) >= C.VET_VERY_SHARP_MIN_SNR)
    g["red"] = (np.array([(o, int(k)) in red for o, k in zip(d["obs"], d["det"])], bool)
                if red is not None and len(d) else np.zeros(len(d), bool))
    return g


PERSIST_BIT = 1 << 17


def dot_numbers(s: pd.DataFrame) -> pd.DataFrame:
    """The numbers behind some gates, per suspect (row of s, NaN without dot lists): edge_px (the smallest distance of
    a dot to the detector edge, px), persist_dots (dots with PERSIST at the peak: a bright star's leftover image),
    size_ratio (the smallest dot √(a·b) / the detector's stellar value at its S/N), flux_ratio (brightest / faintest
    dot within one detector, the largest)."""
    n = len(s)
    out = pd.DataFrame({"edge_px": np.full(n, np.nan), "persist_dots": np.zeros(n, np.int64),
                        "size_ratio": np.full(n, np.nan), "flux_ratio": np.full(n, np.nan)})
    if n == 0 or not has_dot_lists(s):
        return out
    d = explode(s.reset_index(drop=True))
    if len(d) == 0:
        return out
    i = d["i"].to_numpy()
    x, y = d["x"].to_numpy(np.float64), d["y"].to_numpy(np.float64)
    hi = C.VET_DETECTOR_PX - 1
    e = np.minimum(np.minimum(x, y), np.minimum(hi - x, hi - y))
    out["edge_px"] = pd.Series(e).groupby(i).min().reindex(range(n)).to_numpy()
    out["persist_dots"] = pd.Series((d["fpk"].to_numpy(np.int64) & PERSIST_BIT) != 0).groupby(i).sum().reindex(
        range(n)).fillna(0).astype(np.int64).to_numpy()
    ratio = d["size"].to_numpy(np.float64) / star_size(d["det"].to_numpy(), d["snr"].to_numpy())
    out["size_ratio"] = pd.Series(ratio).groupby(i).min().reindex(range(n)).to_numpy()
    f = d.groupby(["i", "det"])["flux"].agg(["max", "min"])
    fr = (f["max"] / f["min"].where(f["min"] > 0)).groupby(level="i").max()
    out["flux_ratio"] = fr.reindex(range(n)).to_numpy()
    return out


def flux_ratio_fail(d: pd.DataFrame) -> pd.Series:
    """Per suspect (index i): brightest > VET_FLUX_RATIO_MAX × faintest dot within one detector."""
    if len(d) == 0:
        return pd.Series(dtype=bool)
    f = d.groupby(["i", "det"])["flux"].agg(["max", "min", "size"])
    bad = (f["size"] >= 2) & ((f["min"] <= 0) | (f["max"] > C.VET_FLUX_RATIO_MAX * f["min"]))
    return bad.groupby(level="i").any()


def flaw_gates(f: pd.DataFrame, n_dots: np.ndarray) -> dict[str, np.ndarray]:
    """The reasons flaw_numbers decides (config, mover flaws): hot_pixel (a dot's pixel lit at other sky places in >=
    VET_HOT_GATE_EXP exposures, or every dot's in >= VET_HOT_PROVEN_EXP: hot_pixel_proven), same_pixel, whole_pixel,
    cross_detector. Not wide: a broad dot alone is a comet as often as junk (wide_every; suspects.mover_flaws makes it a
    reason only with a blend)."""
    hmax = f["hot_exposures_max"].to_numpy(np.float64)
    nd = np.asarray(n_dots, np.int64)
    return {"hot_pixel": (np.nan_to_num(hmax) >= C.VET_HOT_GATE_EXP) | hot_proven(f, nd),
            "same_pixel": np.nan_to_num(f["same_pixel_px"].to_numpy(np.float64), nan=np.inf) <= C.VET_SAME_PIXEL_PX,
            "whole_pixel": f["whole_dots"].to_numpy(np.int64) >= C.VET_WHOLE_GATE_DOTS,
            "cross_detector": f["cross_detector"].to_numpy(bool)}


def wide_every(f: pd.DataFrame) -> np.ndarray:
    """Every unsaturated dot (>= 2) at least VET_WIDE_RATIO × a star of its S/N (trail included): flaw_numbers' columns;
    False where they are missing."""
    if "wide_dots" not in f or "wide_ratio_min" not in f:
        return np.zeros(len(f), bool)
    return (f["wide_dots"].to_numpy(np.int64) >= 2) & (np.nan_to_num(f["wide_ratio_min"].to_numpy(np.float64))
                                                      >= C.VET_WIDE_RATIO)


def hot_proven(f: pd.DataFrame, n_dots: np.ndarray) -> np.ndarray:
    """Every dot (>= 2) on a pixel lit at other sky places in >= VET_HOT_PROVEN_EXP exposures: real 0 of 6,125."""
    return (np.nan_to_num(f["hot_exposures_min"].to_numpy(np.float64)) >= C.VET_HOT_PROVEN_EXP) & (np.asarray(n_dots) >= 2)


def vet(s: pd.DataFrame, movers: pd.DataFrame, free: pd.DataFrame | None, red: set | None,
        catalogued: dict[str, np.ndarray] | None = None, flaws: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Gates on the suspects s (rows of movers with the dot lists). Returns s with grade, reasons (list of gate names in
    VET_GATES order), one boolean column per gate (gate_<name>) and the FLAW_COLUMNS (flaw_numbers; flaws: already
    computed for s), and stats: per gate the suspects failing it, the suspects whose first reason it is, and the grade
    counts. catalogued: the reasons decided outside, per suspect: the catalogues (VET_CATALOGS: a source within 6″ at
    most 2 mag fainter; a catalogue left out was not checked), the strong warnings (bloom, halo, bright_star) and the
    suspects' mover flaws (fixed_source, glow, unseen, wide: only with a blend). hot_pixel: flaw_gates where the free dots have sky places, else
    the old map (movers' dots in >= 3 exposures, free dots in >= 5, the same sky place included)."""
    s = s.copy().reset_index(drop=True)
    n = len(s)
    fail = {k: np.zeros(n, bool) for k in C.VET_GATES}
    st: dict = {"suspects_in": int(n), "dot_lists": bool(has_dot_lists(s)), "free_dots": None if free is None else int(len(free)),
                "qa_photos": red is not None}
    f = flaws.reset_index(drop=True) if flaws is not None else flaw_numbers(s, free)
    away = free is not None and "ra" in free
    st["hot_pixel_map"] = "other_sky" if away else "every_exposure"
    if n and not has_dot_lists(s):
        fail["not_vetted"][:] = True
    elif n:
        d = explode(s)
        nan_xy = ~(np.isfinite(d["x"].to_numpy()) & np.isfinite(d["y"].to_numpy()))
        md = explode(movers) if has_dot_lists(movers) and not away else d
        g = dot_gates(d, md, free, red, hot=not away)
        i = d["i"].to_numpy()
        for name, col in (("hot_pixel", "hot_pixel"), ("bad_column", "bad_column"), ("edge", "edge"),
                          ("masked_peak", "masked_peak"), ("sharp_outlier", "sharp_outlier"), ("very_sharp", "very_sharp")):
            fail[name] = np.bincount(i, weights=g[col].to_numpy(float), minlength=n) > 0
        nd = np.bincount(i, minlength=n)
        for name, hit in flaw_gates(f, nd).items():
            if name == "hot_pixel" and not away:
                continue
            fail[name] = hit
        fail["not_vetted"] = np.bincount(i, weights=nan_xy.astype(float), minlength=n) > 0  # no pixel position for a dot
        fr = flux_ratio_fail(d)
        fail["flux_ratio"][fr.index[fr.to_numpy(bool)].to_numpy(np.int64)] = True
        if red is not None:
            nd = np.bincount(i, minlength=n)
            fail["red_photo"] = (np.bincount(i, weights=g["red"].to_numpy(float), minlength=n) == nd) & (nd > 0)
        st["dots"] = int(len(d))
        st["movers_dots"] = int(len(md))
    for k, hit in (catalogued or {}).items():
        if n and k in fail:
            fail[k] = np.asarray(hit, bool)
    reasons = [[k for k in C.VET_GATES if fail[k][j]] for j in range(n)]
    s["grade"] = ["C" if r else "B" for r in reasons]
    s["reasons"] = reasons
    for k in C.VET_GATES:
        s[f"gate_{k}"] = fail[k]
    for k in FLAW_COLUMNS:
        s[k] = f[k].to_numpy()
    st["gates"] = {k: int(fail[k].sum()) for k in C.VET_GATES}
    st["first_reason"] = {k: int(sum(1 for r in reasons if r and r[0] == k)) for k in C.VET_GATES}
    st["grades"] = {g_: int(sum(1 for x in s["grade"] if x == g_)) for g_ in ("A", "B", "C")}
    return s, st
