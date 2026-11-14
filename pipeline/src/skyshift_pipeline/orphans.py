"""Step `orphans`: a route by which a distant planet (70–1000 AU) could be found, with chance controls.

Why: SPHEREx looks at ~90° from the Sun, so a planet at 70–1000 AU drifts only 0.5–0.03″/h inside a visit (Earth's
reflex) and link's 4″ friends-of-friends chains it into a "fixed" source seen in ONE visit (at 70–100 AU in a few
fragments). Six months later Earth is on the other side of the Sun: the planet sits 4,800″ (70 AU) to 390″
(1000 AU) away along ecliptic longitude (2 AU parallax baseline, less its own orbital motion). highpm pairs only
3–120″ and caps its fit far below these rates, so no other step looks at such sources.

1. Orphans (orphans.parquet, one row per candidate, `reason` empty = kept): single-visit sources
   (highpm.source_visits) with no Gaia / CatWISE / 2MASS entry within crossid's radius (crossids `<cat>_n`), whose
   position another visit covered with >= ORPHANS_MIN_COVER_PHOTOS photos (the absence is real). Reasons:
   bright_fragment (another source with S/N >= ORPHANS_BRIGHT_SNR within ORPHANS_BRIGHT_ARCSEC), zone_edge (within
   ORPHANS_EDGE_ARCSEC of the zone box's edge), saturated.
2. Drift (kept orphans): a weighted straight line east/north vs time through the member dots (dot files read once,
   only the columns needed; ePSF positions where psf_pos_ok; raw, calib not applied), per-dot error
   √((ORPHANS_POS_ERR_SCALE · pos_err)² + ORPHANS_POS_FLOOR_ARCSEC²), errors × √χ²_ν when above 1, one refit without
   dots beyond ORPHANS_CLIP_SIGMA. `drift_ok`: rate within ORPHANS_DRIFT_NSIGMA of ORPHANS_RATE_MIN..MAX ″/h and
   rate error <= ORPHANS_DRIFT_MAX_ERR.
3. Pairs (drift_ok orphans of two visits >= ORPHANS_MIN_DT_DAYS apart, ORPHANS_SEP_MIN..MAX_ARCSEC apart). Model, for
   each heliocentric distance r of a log grid (ORPHANS_R_GRID_AU): the two observed directions from Earth (barycentric
   ephemeris; SPHEREx's 1e-4 AU offset is ignored) put the object at P_A and P_B with |P| = r; its velocity
   V = (P_B − P_A) / Δt must be bound (|V| <= ORPHANS_VMAX_FACTOR × 2π/√r AU/yr), and the sky drifts it predicts at
   both times (V minus Earth's velocity, across the line of sight, over the distance) must match the two measured
   drifts: χ² of 4 terms, minimised over r (best r = the implied distance; r_lo..r_hi where χ² <= min + 4). So the
   separation fixes r through the parallax, the drifts fix it through Earth's reflex, and the bound-orbit cut keeps
   the A→B direction along ecliptic longitude within the planet's own possible motion. Fluxes: per detector measured
   in both (sources' 102 channels, median per detector), (ln ratio)² / (σ_a² + σ_b² + ORPHANS_FLUX_LN_FLOOR²).
   p = χ²-survival of drift + flux terms (dof 3 + common detectors); passing pairs have p >= ORPHANS_PAIR_MIN_P and
   >= ORPHANS_MIN_COMMON_DET common detectors.
4. Controls: the same pairing after shifting the later visits' orphans cyclically within the orphans' box by
   ORPHANS_CONTROL_N random offsets (ORPHANS_CONTROL_SHIFT of the box in RA and in Dec; drifts, times and Earth
   positions kept): their mean number of passing pairs is the chance expectation; `poisson_p` = P(>= real | control).
5. Zone edge: for each drift_ok orphan, the partner's track (parallax only, r over ORPHANS_TRACK_AU) at the time the
   other visit covered the orphan's spot; `track_in_zone` = its share inside the zone box less the edge margin. A
   partner outside can't be found in this zone: distant.json lists those orphans as `open` with the track ends
   (70 and 1000 AU) for a later cross-zone pass.

Writes results/<zone>/orphans.parquet and results/<zone>/distant.json. Never fails the run: an error is logged and
returned in the step stats (the overnight `all` must reach score and suspects).
"""

from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from skyshift_core import tangent_deproject, tangent_offset_arcsec

from . import config as C
from . import log
from .highpm import detector_fluxes, source_visits
from .sky import Footprint, SkyTree, chord, radec_to_xyz
from .spectra import NCH_DET, NDET
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("orphans")

ARCSEC_PER_RAD = 206264.80624709636
HOURS_PER_YR = 365.25 * 24.0
RAD_YR_TO_ARCSEC_H = ARCSEC_PER_RAD / HOURS_PER_YR
EPS = np.deg2rad(C.ECLIPTIC_OBLIQUITY_DEG)
ECL_POLE = np.array([0.0, -np.sin(EPS), np.cos(EPS)])  # ICRS unit vector of the north ecliptic pole
DOT_COLUMNS = ("mjd", "ra", "dec", "pos_err_arcsec", "snr", "flux_ujy", "ra_psf", "dec_psf", "pos_err_psf_arcsec",
               "psf_pos_ok")
REASONS = ("bright_fragment", "zone_edge", "saturated")
PLANETS = {"neptune": 3.88, "super_earth_2re": 2.0, "earth": 1.0}  # radius in Earth radii (reach table)
EARTH_RADIUS_KM = 6371.0


# --- geometry ------------------------------------------------------------------------------------------------------
def unit(ra, dec) -> np.ndarray:
    return radec_to_xyz(ra, dec)


def east_north(ra, dec) -> tuple[np.ndarray, np.ndarray]:
    a, d = np.deg2rad(np.asarray(ra, np.float64)), np.deg2rad(np.asarray(dec, np.float64))
    e = np.stack([-np.sin(a), np.cos(a), np.zeros_like(a)], -1)
    n = np.stack([-np.sin(d) * np.cos(a), -np.sin(d) * np.sin(a), np.cos(d)], -1)
    return e, n


def xyz_to_radec(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = v / np.linalg.norm(v, axis=-1, keepdims=True)
    return np.rad2deg(np.arctan2(v[..., 1], v[..., 0])) % 360.0, np.rad2deg(np.arcsin(np.clip(v[..., 2], -1, 1)))


def ecliptic_axes(ra, dec) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(λe, λn, βe, βn): the +longitude and +latitude directions at (ra, dec) in east/north components."""
    u = unit(ra, dec)
    lam = np.cross(np.broadcast_to(ECL_POLE, u.shape), u)
    lam /= np.maximum(np.linalg.norm(lam, axis=-1, keepdims=True), 1e-12)
    beta = np.cross(u, lam)
    e, n = east_north(ra, dec)
    return (lam * e).sum(-1), (lam * n).sum(-1), (beta * e).sum(-1), (beta * n).sum(-1)


def ecliptic_offset(ra_a, dec_a, ra_b, dec_b) -> tuple[np.ndarray, np.ndarray]:
    """B − A in arcsec along ecliptic longitude and latitude (tangent plane at A)."""
    de, dn = tangent_offset_arcsec(ra_a, dec_a, ra_b, dec_b)
    le, ln, be, bn = ecliptic_axes(ra_a, dec_a)
    return de * le + dn * ln, de * be + dn * bn


def earth_posvel(mjd) -> tuple[np.ndarray, np.ndarray]:
    """Earth's barycentric ICRS position (AU) and velocity (AU/yr) at each mjd (UTC)."""
    from astropy.coordinates import get_body_barycentric_posvel
    from astropy.time import Time
    mjd = np.asarray(mjd, np.float64)
    u, inv = np.unique(np.round(mjd, 5), return_inverse=True)
    pos, vel = get_body_barycentric_posvel("earth", Time(u, format="mjd", scale="utc"))
    p = np.stack([pos.x.to_value("AU"), pos.y.to_value("AU"), pos.z.to_value("AU")], -1)
    v = np.stack([vel.x.to_value("AU/d"), vel.y.to_value("AU/d"), vel.z.to_value("AU/d")], -1) * 365.25
    return p[inv.ravel()], v[inv.ravel()]


def range_at(E: np.ndarray, u: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Distance from the observer at E along u to heliocentric radius r. E, u: (..., 3); r broadcasts."""
    b = (E * u).sum(-1)
    return -b + np.sqrt(np.maximum(b * b - (E * E).sum(-1) + r * r, 0.0))


def r_grid() -> np.ndarray:
    lo, hi, n = C.ORPHANS_R_GRID_AU
    return np.geomspace(lo, hi, int(n))


def vmax_au_yr(r) -> np.ndarray:
    return C.ORPHANS_VMAX_FACTOR * 2.0 * np.pi / np.sqrt(r)


def predicted_drift(P: np.ndarray, V: np.ndarray, E: np.ndarray, VE: np.ndarray, u: np.ndarray,
                    e: np.ndarray, n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sky drift (east, north ″/h) of an object at P moving with V, seen from E moving with VE (u its direction)."""
    rel = P - E
    dist = np.linalg.norm(rel, axis=-1)
    w = V - VE
    perp = w - u * (w * u).sum(-1, keepdims=True)
    mu = perp / dist[..., None] * RAD_YR_TO_ARCSEC_H
    return (mu * e).sum(-1), (mu * n).sum(-1)


def pair_model(a: dict, b: dict) -> dict:
    """The bound-orbit pair model (module doc, 3) for paired arrays a, b with keys ra, dec, mjd, E, VE, ve, vn, se, sn
    (positions at mjd, Earth position/velocity at mjd, measured drift east/north ″/h and its errors).
    Returns chi2 (min over r), r_best, r_lo, r_hi, bound (any bound r), v_au_yr at r_best, pred drifts at r_best."""
    R = r_grid()
    ua, ub = unit(a["ra"], a["dec"]), unit(b["ra"], b["dec"])
    ea, na = east_north(a["ra"], a["dec"])
    eb, nb = east_north(b["ra"], b["dec"])
    dt = (b["mjd"] - a["mjd"]) / 365.25
    Ea, Eb = a["E"][:, None, :], b["E"][:, None, :]
    dA = range_at(Ea, ua[:, None, :], R[None, :])
    dB = range_at(Eb, ub[:, None, :], R[None, :])
    Pa = Ea + dA[..., None] * ua[:, None, :]
    Pb = Eb + dB[..., None] * ub[:, None, :]
    V = (Pb - Pa) / dt[:, None, None]
    speed = np.linalg.norm(V, axis=-1)
    bound = speed <= vmax_au_yr(R)[None, :]
    pae, pan = predicted_drift(Pa, V, Ea, a["VE"][:, None, :], ua[:, None, :], ea[:, None, :], na[:, None, :])
    pbe, pbn = predicted_drift(Pb, V, Eb, b["VE"][:, None, :], ub[:, None, :], eb[:, None, :], nb[:, None, :])
    chi = (((pae - a["ve"][:, None]) / a["se"][:, None]) ** 2 + ((pan - a["vn"][:, None]) / a["sn"][:, None]) ** 2
           + ((pbe - b["ve"][:, None]) / b["se"][:, None]) ** 2 + ((pbn - b["vn"][:, None]) / b["sn"][:, None]) ** 2)
    chi = np.where(bound, chi, np.inf)
    k = np.argmin(chi, axis=1)
    rows = np.arange(len(k))
    cmin = chi[rows, k]
    within = chi <= (cmin + 4.0)[:, None]
    r_lo = np.where(within.any(1), R[np.argmax(within, axis=1)], np.nan)
    r_hi = np.where(within.any(1), R[len(R) - 1 - np.argmax(within[:, ::-1], axis=1)], np.nan)
    fin = np.isfinite(cmin)
    return {"chi2": cmin, "r_best": np.where(fin, R[k], np.nan), "r_lo": np.where(fin, r_lo, np.nan),
            "r_hi": np.where(fin, r_hi, np.nan), "bound": bound.any(1), "v_au_yr": speed[rows, k],
            "pred_a": (pae[rows, k], pan[rows, k]), "pred_b": (pbe[rows, k], pbn[rows, k])}


def partner_track(ra, dec, mjd, mjd_other, r_au) -> tuple[np.ndarray, np.ndarray]:
    """Where a heliocentrically still object at each r (AU) seen at (ra, dec, mjd) appears at mjd_other: (N, R)."""
    E, _ = earth_posvel(mjd)
    E2, _ = earth_posvel(mjd_other)
    u = unit(ra, dec)[:, None, :]
    d = range_at(E[:, None, :], u, np.asarray(r_au)[None, :])
    P = E[:, None, :] + d[..., None] * u
    return xyz_to_radec(P - E2[:, None, :])


# --- 1. orphans ----------------------------------------------------------------------------------------------------
def edge_distance_arcsec(ra, dec, zone: C.Zone) -> np.ndarray:
    """Signed distance (″, small-angle) to the zone box's edge: > 0 inside, < 0 outside."""
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    cosd = np.cos(np.deg2rad(dec))
    return np.minimum.reduce([(ra - zone.ra_min) * cosd, (zone.ra_max - ra) * cosd, dec - zone.dec_min,
                              zone.dec_max - dec]) * 3600.0


def unmatched(xid: pd.DataFrame | None, sid: np.ndarray) -> np.ndarray:
    """True where no Gaia / CatWISE / 2MASS entry lies within crossid's radius (`<cat>_n`, else the ref ids)."""
    if xid is None or not len(xid):
        return np.ones(len(sid), bool)
    x = xid.set_index("source_id").reindex(sid)
    hit = np.zeros(len(sid), bool)
    for cat in ("gaia", "catwise", "tmass"):
        if f"{cat}_n" in x:
            hit |= np.nan_to_num(x[f"{cat}_n"].to_numpy(np.float64)) > 0
        if f"{cat}_ref_id" in x:
            hit |= x[f"{cat}_ref_id"].fillna("").astype(str).to_numpy() != ""
    return ~hit


def other_cover(ra, dec, visit, photos: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per position: the other visit that covered it most (photo count), that count, and those photos' mean mjd."""
    n = len(ra)
    vis_u = np.unique(photos["visit"].to_numpy(np.int64))
    cnt = np.zeros((len(vis_u), n), np.int64)
    tsum = np.zeros((len(vis_u), n))
    if n:
        tree = SkyTree(ra, dec)
        for k, (s_region, v, mjd) in enumerate(zip(photos["s_region"], photos["visit"], photos["mjd"])):
            fp = Footprint.from_s_region(s_region)
            near = tree.tree.query_ball_point(unit(fp.ra0, fp.dec0), chord(fp.radius_deg() * 3600.0 + 1.0))
            if not near:
                continue
            near = np.asarray(near, np.int64)
            inside = near[fp.contains(ra[near], dec[near])]
            j = int(np.searchsorted(vis_u, int(v)))
            cnt[j, inside] += 1
            tsum[j, inside] += float(mjd)
    own = vis_u[:, None] == np.asarray(visit, np.int64)[None, :]
    c = np.where(own, -1, cnt)
    best = np.argmax(c, axis=0) if n else np.zeros(0, np.int64)
    cols = np.arange(n)
    count = np.maximum(c[best, cols], 0) if n else np.zeros(0, np.int64)
    with np.errstate(invalid="ignore", divide="ignore"):
        tmean = np.where(count > 0, tsum[best, cols] / np.maximum(count, 1), np.nan) if n else np.zeros(0)
    return (vis_u[best] if n else np.zeros(0, np.int64)), count, tmean


def bright_neighbour(ra, dec, sid, fixed: pd.DataFrame) -> np.ndarray:
    """True where another source with max dot S/N >= ORPHANS_BRIGHT_SNR lies within ORPHANS_BRIGHT_ARCSEC."""
    b = fixed[fixed["snr"].to_numpy(np.float64) >= C.ORPHANS_BRIGHT_SNR]
    out = np.zeros(len(ra), bool)
    if not len(b) or not len(ra):
        return out
    bsid = b["source_id"].to_numpy(np.int64)
    for i, near in enumerate(SkyTree(b["ra"].to_numpy(), b["dec"].to_numpy()).query_radius(ra, dec, C.ORPHANS_BRIGHT_ARCSEC)):
        out[i] = any(bsid[j] != sid[i] for j in near)
    return out


# --- 2. drift ------------------------------------------------------------------------------------------------------
def member_positions(store: Store, zone: C.Zone, sids: np.ndarray, workers: int = 16) -> tuple[pd.DataFrame, dict]:
    """The member dots of `sids` with DOT_COLUMNS (projected column reads), detector, session, visit."""
    m = store.read_parquet(results_rel(zone.name, "members.parquet"))
    m = m[np.isin(m["source_id"].to_numpy(np.int64), np.asarray(sids, np.int64))]
    photos = store.read_parquet(photolist_rel(zone.name), columns=["obs_id", "detector", "mjd", "session", "visit"])
    pkey = {(str(o), int(d)): i for i, (o, d) in enumerate(zip(photos["obs_id"], photos["detector"]))}
    groups = m.groupby(["obs_id", "detector"], observed=True, sort=True).indices
    sid_all = m["source_id"].to_numpy(np.int64)
    row_all = m["dot_row"].to_numpy(np.int64)
    st = {"files": len(groups), "files_missing": 0, "rows_missing": 0}

    def one(item):
        (obs_id, det), idx = item
        rel = dots_rel(str(obs_id), int(det))
        try:
            import pyarrow.parquet as pq
            with store.fs.open(store.path(rel), "rb") as f:
                pf = pq.ParquetFile(f)
                cols = [c for c in DOT_COLUMNS if c in pf.schema_arrow.names]
                df = pf.read(columns=cols).to_pandas()
        except FileNotFoundError:
            st["files_missing"] += 1
            return None
        except Exception:
            try:
                df = pd.read_parquet(io.BytesIO(store.read_bytes(rel)))
            except FileNotFoundError:
                st["files_missing"] += 1
                return None
        rows = row_all[idx]
        ok = rows < len(df)
        st["rows_missing"] += int((~ok).sum())
        df = df.iloc[rows[ok]].reset_index(drop=True)
        out = pd.DataFrame({c: (df[c].to_numpy() if c in df else np.full(len(df), np.nan)) for c in DOT_COLUMNS})
        out["psf_pos_ok"] = (df["psf_pos_ok"].fillna(False).to_numpy(bool) if "psf_pos_ok" in df
                             else np.zeros(len(df), bool))
        out.insert(0, "source_id", sid_all[idx[ok]])
        out["detector"] = np.int8(det)
        p = pkey.get((str(obs_id), int(det)), -1)
        out["session"] = int(photos["session"].iloc[p]) if p >= 0 else -1
        out["visit"] = int(photos["visit"].iloc[p]) if p >= 0 else -1
        if p >= 0:
            mj = out["mjd"].to_numpy(np.float64)
            out["mjd"] = np.where(np.isfinite(mj), mj, float(photos["mjd"].iloc[p]))
        return out

    if groups:
        with ThreadPoolExecutor(max_workers=max(4, min(workers, 32))) as ex:
            parts = [p for p in ex.map(one, groups.items()) if p is not None]
    else:
        parts = []
    if not parts:
        return pd.DataFrame(columns=["source_id", *DOT_COLUMNS, "detector", "session", "visit"]), st
    d = pd.concat(parts, ignore_index=True)
    psf = d["psf_pos_ok"].to_numpy(bool) & np.isfinite(d["ra_psf"].to_numpy(np.float64))
    d["ra"] = np.where(psf, d["ra_psf"].to_numpy(np.float64), d["ra"].to_numpy(np.float64))
    d["dec"] = np.where(psf, d["dec_psf"].to_numpy(np.float64), d["dec"].to_numpy(np.float64))
    d["pos_err_arcsec"] = np.where(psf, d["pos_err_psf_arcsec"].to_numpy(np.float64), d["pos_err_arcsec"].to_numpy(np.float64))
    st["psf_positions"] = int(psf.sum())
    st["dots"] = int(len(d))
    return d, st


def fit_drift(d: pd.DataFrame) -> pd.DataFrame:
    """Per source: weighted line east/north vs time through its dots (module doc, 2). d: source_id, ra, dec, mjd,
    pos_err_arcsec [, session]. Positions at the weighted mean time `mjd`; drift in ″/h."""
    cols = ["source_id", "ra", "dec", "mjd", "drift_e", "drift_n", "drift_e_err", "drift_n_err", "rate", "rate_err",
            "drift_pa_deg", "drift_pa_ecl_deg", "chi2_nu", "n_fit", "n_dots_read", "n_sessions_fit", "span_h", "fit_ok"]
    if not len(d):
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
    sid_u, s = np.unique(d["source_id"].to_numpy(np.int64), return_inverse=True)
    N = len(sid_u)
    ra, dec, mjd = (d[c].to_numpy(np.float64) for c in ("ra", "dec", "mjd"))
    pe = d["pos_err_arcsec"].to_numpy(np.float64)
    pe = np.where(np.isfinite(pe) & (pe > 0), pe, 1.0)
    err = np.hypot(C.ORPHANS_POS_ERR_SCALE * pe, C.ORPHANS_POS_FLOOR_ARCSEC)
    good = np.isfinite(ra) & np.isfinite(dec) & np.isfinite(mjd)
    ra0 = pd.Series(np.where(good, ra, np.nan)).groupby(s).median().reindex(range(N)).to_numpy()
    dec0 = pd.Series(np.where(good, dec, np.nan)).groupby(s).median().reindex(range(N)).to_numpy()
    e, n = tangent_offset_arcsec(ra0[s], dec0[s], ra, dec)
    e, n = np.where(good, e, 0.0), np.where(good, n, 0.0)
    w = np.where(good, 1.0 / err ** 2, 0.0)
    keep = good.copy()
    for it in range(2):
        wk = np.where(keep, w, 0.0)
        S = np.bincount(s, wk, N)
        tm = np.bincount(s, wk * np.where(good, mjd, 0.0), N) / np.maximum(S, 1e-300)
        t = np.where(good, (mjd - tm[s]) * 24.0, 0.0)
        Stt = np.bincount(s, wk * t * t, N)
        with np.errstate(invalid="ignore", divide="ignore"):
            ae, an = np.bincount(s, wk * e, N) / S, np.bincount(s, wk * n, N) / S
            be, bn = np.bincount(s, wk * t * e, N) / Stt, np.bincount(s, wk * t * n, N) / Stt
        re = e - (ae[s] + be[s] * t)
        rn = n - (an[s] + bn[s] * t)
        nk = np.bincount(s, keep, N)
        dof = 2 * nk - 4
        with np.errstate(invalid="ignore", divide="ignore"):
            chi2_nu = np.bincount(s, wk * (re * re + rn * rn), N) / np.maximum(dof, 1)
        infl = np.where(dof > 0, np.sqrt(np.maximum(1.0, np.nan_to_num(chi2_nu, nan=1.0))), 1.0)
        if it == 0:
            keep = good & (np.maximum(np.abs(re), np.abs(rn)) <= C.ORPHANS_CLIP_SIGMA * err * infl[s])
    tk = np.where(keep, mjd, np.nan)
    span = (pd.Series(tk).groupby(s).max() - pd.Series(tk).groupby(s).min()).reindex(range(N)).to_numpy() * 24.0
    with np.errstate(invalid="ignore", divide="ignore"):
        sb = infl / np.sqrt(Stt)
    if "session" in d:
        ses = pd.DataFrame({"s": s[keep], "x": d["session"].to_numpy()[keep]}).drop_duplicates().groupby("s").size()
        nses = ses.reindex(range(N)).fillna(0).to_numpy(np.int64)
    else:
        nses = nk
    ok = (nk >= C.ORPHANS_MIN_DOTS) & (np.nan_to_num(span) >= C.ORPHANS_MIN_SPAN_H) & np.isfinite(be) & np.isfinite(sb) & (Stt > 0)
    rate = np.hypot(be, bn)
    rfit, dfit = tangent_deproject(ra0, dec0, np.nan_to_num(ae), np.nan_to_num(an))
    le, ln, bce, bcn = ecliptic_axes(rfit, dfit)
    pa = np.rad2deg(np.arctan2(be, bn)) % 360.0  # east of north
    pa_ecl = np.rad2deg(np.arctan2(be * bce + bn * bcn, be * le + bn * ln))  # 0 = +longitude, ±180 = −longitude
    out = pd.DataFrame({
        "source_id": sid_u, "ra": rfit, "dec": dfit, "mjd": tm,
        "drift_e": be.astype(np.float32), "drift_n": bn.astype(np.float32),
        "drift_e_err": sb.astype(np.float32), "drift_n_err": sb.astype(np.float32),
        "rate": rate.astype(np.float32), "rate_err": sb.astype(np.float32),
        "drift_pa_deg": pa.astype(np.float32), "drift_pa_ecl_deg": pa_ecl.astype(np.float32),
        "chi2_nu": chi2_nu.astype(np.float32), "n_fit": nk.astype(np.int32),
        "n_dots_read": np.bincount(s, minlength=N).astype(np.int32), "n_sessions_fit": nses.astype(np.int32),
        "span_h": span.astype(np.float32), "fit_ok": ok,
    })
    return out[cols]


def drift_ok(rate, err) -> np.ndarray:
    rate, err = np.asarray(rate, np.float64), np.asarray(err, np.float64)
    k = C.ORPHANS_DRIFT_NSIGMA
    with np.errstate(invalid="ignore"):
        return (np.isfinite(rate) & np.isfinite(err) & (err <= C.ORPHANS_DRIFT_MAX_ERR)
                & (rate + k * err >= C.ORPHANS_RATE_MIN) & (rate - k * err <= C.ORPHANS_RATE_MAX))


# --- 3, 4. pairs and controls --------------------------------------------------------------------------------------
def detector_flux_errors(spec: np.ndarray, err: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """(N, 6) detector flux (median of its channels) and its relative error (NaN where unknown)."""
    f = detector_fluxes(spec)
    if err is None:
        return f, np.full(f.shape, np.nan)
    import warnings
    e = err.reshape(len(err), NDET, NCH_DET)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        nfin = np.isfinite(spec.reshape(len(spec), NDET, NCH_DET)).sum(-1)
        ed = 1.25 * np.nanmedian(e, axis=2) / np.sqrt(np.maximum(nfin, 1))
        rel = ed / f
    return f, np.where(np.isfinite(rel) & (f > 0), rel, np.nan)


def candidate_pairs(ra, dec, visit, mjd) -> tuple[np.ndarray, np.ndarray]:
    """Index pairs (i earlier, j later) of different visits >= ORPHANS_MIN_DT_DAYS apart, separation
    ORPHANS_SEP_MIN..MAX, A→B latitude offset <= 0.4 × separation (generous: a bound orbit at 70 AU reaches 0.33)."""
    if len(ra) < 2:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    tree = SkyTree(ra, dec)
    pr = tree.tree.query_pairs(chord(C.ORPHANS_SEP_MAX_ARCSEC), output_type="ndarray")
    if not len(pr):
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    i, j = pr[:, 0], pr[:, 1]
    sw = mjd[i] > mjd[j]
    i, j = np.where(sw, j, i), np.where(sw, i, j)
    keep = (visit[i] != visit[j]) & ((mjd[j] - mjd[i]) >= C.ORPHANS_MIN_DT_DAYS)
    i, j = i[keep], j[keep]
    dl, db = ecliptic_offset(ra[i], dec[i], ra[j], dec[j])
    sep = np.hypot(dl, db)
    keep = (sep >= C.ORPHANS_SEP_MIN_ARCSEC) & (sep <= C.ORPHANS_SEP_MAX_ARCSEC) & (np.abs(db) <= 0.4 * sep)
    return i[keep], j[keep]


def score_pairs(o: pd.DataFrame, i: np.ndarray, j: np.ndarray, flux: np.ndarray, flux_rel: np.ndarray,
                chunk: int = 4000) -> pd.DataFrame:
    """Model χ² + flux χ² for index pairs into o (columns ra, dec, mjd, drift_e/n(_err), E, VE as arrays)."""
    from scipy.stats import chi2 as chi2_dist
    if not len(i):
        return pd.DataFrame()
    parts = []
    E, VE = np.stack(o["_E"].to_numpy()), np.stack(o["_VE"].to_numpy())
    cols = {c: o[c].to_numpy(np.float64) for c in ("ra", "dec", "mjd", "drift_e", "drift_n", "drift_e_err", "drift_n_err")}
    for k0 in range(0, len(i), chunk):
        ii, jj = i[k0:k0 + chunk], j[k0:k0 + chunk]
        side = lambda x: {"ra": cols["ra"][x], "dec": cols["dec"][x], "mjd": cols["mjd"][x], "E": E[x], "VE": VE[x],
                          "ve": cols["drift_e"][x], "vn": cols["drift_n"][x],
                          "se": np.hypot(cols["drift_e_err"][x], C.ORPHANS_DRIFT_FLOOR),
                          "sn": np.hypot(cols["drift_n_err"][x], C.ORPHANS_DRIFT_FLOOR)}
        m = pair_model(side(ii), side(jj))
        parts.append(pd.DataFrame({"i": ii, "j": jj, "chi2_drift": m["chi2"], "distance_au": m["r_best"],
                                   "distance_lo_au": m["r_lo"], "distance_hi_au": m["r_hi"], "bound": m["bound"],
                                   "speed_au_yr": m["v_au_yr"], "pred_a_e": m["pred_a"][0], "pred_a_n": m["pred_a"][1],
                                   "pred_b_e": m["pred_b"][0], "pred_b_n": m["pred_b"][1]}))
    p = pd.concat(parts, ignore_index=True)
    fa, fb = flux[p["i"].to_numpy()], flux[p["j"].to_numpy()]
    sa, sb = flux_rel[p["i"].to_numpy()], flux_rel[p["j"].to_numpy()]
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.log(fb / fa)
        var = np.nan_to_num(sa ** 2, nan=0.0) + np.nan_to_num(sb ** 2, nan=0.0) + C.ORPHANS_FLUX_LN_FLOOR ** 2
    common = np.isfinite(lr) & (fa > 0) & (fb > 0)
    p["n_common_det"] = common.sum(1).astype(np.int8)
    p["chi2_flux"] = np.where(common, lr ** 2 / var, 0.0).sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        p["flux_ratio"] = np.exp(np.where(common, lr, 0.0).sum(1) / np.maximum(p["n_common_det"], 1))
    p["dof"] = 3 + p["n_common_det"].astype(np.int64)
    chi = p["chi2_drift"].to_numpy() + p["chi2_flux"].to_numpy()
    p["chi2"] = chi
    p["p"] = np.where(np.isfinite(chi), chi2_dist.sf(np.where(np.isfinite(chi), chi, 0.0), p["dof"].to_numpy()), 0.0)
    p["passes"] = (p["bound"].to_numpy() & (p["p"].to_numpy() >= C.ORPHANS_PAIR_MIN_P)
                   & (p["n_common_det"].to_numpy() >= C.ORPHANS_MIN_COMMON_DET))
    return p


def control_shift(ra, dec, later: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """The later visits' positions moved cyclically within the box of all positions (RA unwrapped about the median)."""
    ra = np.asarray(ra, np.float64).copy()
    dec = np.asarray(dec, np.float64).copy()
    a = np.deg2rad(ra)
    ref = np.rad2deg(np.arctan2(np.sin(a).mean(), np.cos(a).mean()))  # circular mean: a zone may cross RA 0
    ru = (ra - ref + 180.0) % 360.0 - 180.0
    lo_r, hi_r = ru.min() - 1e-6, ru.max() + 1e-6
    lo_d, hi_d = dec.min() - 1e-6, dec.max() + 1e-6
    a, b = C.ORPHANS_CONTROL_SHIFT
    sr, sd = rng.uniform(a, b) * (hi_r - lo_r), rng.uniform(a, b) * (hi_d - lo_d)
    ru[later] = lo_r + (ru[later] - lo_r + sr) % (hi_r - lo_r)
    dec[later] = lo_d + (dec[later] - lo_d + sd) % (hi_d - lo_d)
    return (ru + ref) % 360.0, dec


def pairs_and_controls(o: pd.DataFrame, flux: np.ndarray, flux_rel: np.ndarray, n_controls: int | None = None) -> tuple[pd.DataFrame, list[int], dict]:
    """Real pairs (all scored, `passes` marks the kept ones) and the number of passing pairs in each control."""
    ra, dec = o["ra"].to_numpy(np.float64), o["dec"].to_numpy(np.float64)
    vis, mjd = o["visit"].to_numpy(np.int64), o["mjd"].to_numpy(np.float64)
    i, j = candidate_pairs(ra, dec, vis, mjd)
    real = score_pairs(o, i, j, flux, flux_rel)
    vu = np.unique(vis)
    later = vis != (vu.min() if len(vu) else 0)
    n = C.ORPHANS_CONTROL_N if n_controls is None else n_controls
    if len(i):
        n = int(min(n, max(5, C.ORPHANS_MAX_PAIR_EVALS // len(i))))
    rng = np.random.default_rng(C.ORPHANS_CONTROL_SEED)
    counts, tried = [], []
    for _ in range(n if later.any() and (~later).any() else 0):
        r2, d2 = control_shift(ra, dec, later, rng)
        c = o.copy()
        c["ra"], c["dec"] = r2, d2
        ci, cj = candidate_pairs(r2, d2, vis, mjd)
        cp = score_pairs(c, ci, cj, flux, flux_rel)
        counts.append(int(cp["passes"].sum()) if len(cp) else 0)
        tried.append(int(len(ci)))
    return real, counts, {"real_tested": int(len(i)), "control_tested_mean": round(float(np.mean(tried)), 1) if tried else 0.0}


# --- depth and reach -----------------------------------------------------------------------------------------------
def depth_ab(dots: pd.DataFrame) -> dict:
    """Per detector: median AB of dots with 5 <= S/N < 6 (the single-exposure 5σ depth these orphans reach)."""
    out = {}
    if not len(dots):
        return out
    snr, f = dots["snr"].to_numpy(np.float64), dots["flux_ujy"].to_numpy(np.float64)
    det = dots["detector"].to_numpy(np.int64)
    for k in range(1, NDET + 1):
        m = (det == k) & (snr >= 5) & (snr < 6) & (f > 0)
        if m.sum() >= 5:
            out[f"D{k}"] = round(float(np.median(23.9 - 2.5 * np.log10(f[m]))), 2)
    return out


def reach_au(depth_j_ab: float | None) -> dict:
    """Farthest distance (AU) at which a planet of each size and albedo is as bright as `depth_j_ab` in J (AB):
    V = H + 5 log10(r Δ) with Δ ≈ r at quadrature, J_AB ≈ V − 0.3 (solar colours), H from size and albedo."""
    if depth_j_ab is None or not np.isfinite(depth_j_ab):
        return {}
    out = {}
    for name, rad in PLANETS.items():
        for pv in (0.1, 0.4):
            H = 5 * np.log10(1329.0 / (2 * rad * EARTH_RADIUS_KM * np.sqrt(pv)))
            out[f"{name}_p{pv}"] = round(float(10 ** ((depth_j_ab + 0.3 - H) / 10.0)), 0)
    return out


# --- the step ------------------------------------------------------------------------------------------------------
def compute(store: Store, zone: C.Zone, workers: int = 16) -> tuple[pd.DataFrame, dict]:
    """Steps 1, 2 and 5 of the module doc: every candidate with its reason, drift and partner track."""
    t0 = time.time()
    st: dict = {"zone": zone.name}
    sraw = store.read_bytes(results_rel(zone.name, "sources.parquet"))
    import pyarrow.parquet as pq
    names = pq.ParquetFile(io.BytesIO(sraw)).schema_arrow.names
    scal = [c for c in ("source_id", "ra", "dec", "n_dots", "n_sessions", "saturated") if c in names]
    src = pq.read_table(io.BytesIO(sraw), columns=scal).to_pandas()
    sv = source_visits(store, zone)
    src = src.merge(sv.rename(columns={"mjd": "mjd_mean"}), on="source_id", how="left")
    src["visit"] = src["visit"].fillna(-1).astype(np.int64)
    single = src[src["visit"] >= 0].reset_index(drop=True)
    st["single_visit_sources"] = int(len(single))
    xrel = results_rel(zone.name, "crossids.parquet")
    xid = store.read_parquet(xrel) if store.exists(xrel) else None
    if xid is None:
        L.warning("orphans %s: no crossids.parquet; every single-visit source counts as unmatched", zone.name)
    st["crossids"] = xid is not None
    sid = single["source_id"].to_numpy(np.int64)
    um = unmatched(xid, sid)
    cand = single[um].reset_index(drop=True)
    st["unmatched"] = int(len(cand))
    photos = store.read_parquet(photolist_rel(zone.name), columns=["s_region", "visit", "mjd"])
    ra, dec = cand["ra"].to_numpy(np.float64), cand["dec"].to_numpy(np.float64)
    ov, ocount, otime = other_cover(ra, dec, cand["visit"].to_numpy(np.int64), photos)
    cand["other_visit"], cand["cover_other"], cand["mjd_other"] = ov.astype(np.int16), ocount.astype(np.int32), otime
    cand = cand[cand["cover_other"] >= C.ORPHANS_MIN_COVER_PHOTOS].reset_index(drop=True)
    st["unmatched_covered"] = int(len(cand))
    ra, dec, csid = cand["ra"].to_numpy(np.float64), cand["dec"].to_numpy(np.float64), cand["source_id"].to_numpy(np.int64)
    fixed = store.read_parquet(results_rel(zone.name, "fixed.parquet"), columns=["source_id", "ra", "dec", "snr"])
    snr = fixed.set_index("source_id")["snr"].reindex(csid).to_numpy(np.float64)
    cand["snr_max"] = snr.astype(np.float32)
    cand["edge_arcsec"] = edge_distance_arcsec(ra, dec, zone).astype(np.float32)
    bad = {"bright_fragment": bright_neighbour(ra, dec, csid, fixed),
           "zone_edge": cand["edge_arcsec"].to_numpy() < C.ORPHANS_EDGE_ARCSEC,
           "saturated": cand["saturated"].fillna(False).to_numpy(bool) if "saturated" in cand else np.zeros(len(cand), bool)}
    bits = np.stack([bad[k] for k in REASONS], 1) if len(cand) else np.zeros((0, len(REASONS)), bool)
    cand["reason"] = [",".join(np.array(REASONS)[b]) for b in bits]
    clean = cand["reason"].to_numpy() == ""
    st["orphans"] = int(clean.sum())
    st["reasons"] = {r: int(bad[r].sum()) for r in REASONS}
    # 2. drift from the clean orphans' member dots
    t1 = time.time()
    dots, dst = member_positions(store, zone, csid[clean], workers)
    if len(dots):
        cpos = cand.set_index("source_id")[["ra", "dec"]]
        q = cpos.reindex(dots["source_id"].to_numpy(np.int64))
        from .sky import angsep
        far = angsep(dots["ra"].to_numpy(np.float64), dots["dec"].to_numpy(np.float64), q["ra"].to_numpy(), q["dec"].to_numpy()) * 3600
        dst["stale_dots"] = int((~(far <= C.ORPHANS_MEMBER_MAX_ARCSEC)).sum())
        dots = dots[far <= C.ORPHANS_MEMBER_MAX_ARCSEC].reset_index(drop=True)
    st["dots"] = {**dst, "read_s": round(time.time() - t1, 1)}
    fit = fit_drift(dots[dots["visit"].to_numpy(np.int64) == pd.Series(cand.set_index("source_id")["visit"]).reindex(
        dots["source_id"].to_numpy(np.int64)).to_numpy()] if len(dots) else dots)
    out = cand.merge(fit.rename(columns={"ra": "ra_fit", "dec": "dec_fit", "mjd": "mjd_fit"}), on="source_id", how="left")
    out["fit_ok"] = out["fit_ok"].fillna(False).astype(bool)
    out["drift_ok"] = out["fit_ok"].to_numpy() & drift_ok(out["rate"], out["rate_err"]) & (out["reason"].to_numpy() == "")
    st["drift_fitted"] = int(out["fit_ok"].sum())
    st["drift_ok"] = int(out["drift_ok"].sum())
    st["depth_ab_snr5"] = depth_ab(dots)
    # 5. partner track for the zone-edge check
    lo, hi, n = C.ORPHANS_TRACK_AU
    R = np.geomspace(lo, hi, int(n))
    out["track_in_zone"] = np.float32(np.nan)
    for c in ("track_ra_near", "track_dec_near", "track_ra_far", "track_dec_far"):
        out[c] = np.nan
    ok = out["drift_ok"].to_numpy()
    if ok.any():
        tr, td = partner_track(out.loc[ok, "ra_fit"].to_numpy(), out.loc[ok, "dec_fit"].to_numpy(),
                               out.loc[ok, "mjd_fit"].to_numpy(), out.loc[ok, "mjd_other"].to_numpy(), R)
        inside = edge_distance_arcsec(tr, td, zone) >= C.ORPHANS_EDGE_ARCSEC
        out.loc[ok, "track_in_zone"] = inside.mean(1).astype(np.float32)
        out.loc[ok, "track_ra_near"], out.loc[ok, "track_dec_near"] = tr[:, 0], td[:, 0]
        out.loc[ok, "track_ra_far"], out.loc[ok, "track_dec_far"] = tr[:, -1], td[:, -1]
    st["fit_s"] = round(time.time() - t1, 1)
    st["wall_s_orphans"] = round(time.time() - t0, 1)
    return out, st


def known_match(out: pd.DataFrame, pred: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per fitted orphan: the nearest MPC object (known.load_predictions rows) that moves like it (on-sky rate within
    ORPHANS_KNOWN_RATE_TOL ″/h + 3σ of the drift) and sits within ORPHANS_KNOWN_ARCSEC of the orphan moved by its drift
    to the prediction's time (|Δt| <= ORPHANS_KNOWN_DAYS). (name, V, separation ″); '' / NaN where none. A match is a
    recovered known body (a TNO, an asteroid near its stationary point), not a reason to drop the orphan."""
    n = len(out)
    name, v, sep = np.full(n, "", dtype=object), np.full(n, np.nan), np.full(n, np.nan)
    if pred is None or not len(pred) or not n:
        return name, v, sep
    pr = pd.to_numeric(pred["dra_arcsec_h"], errors="coerce").to_numpy(np.float64)
    pd_ = pd.to_numeric(pred["ddec_arcsec_h"], errors="coerce").to_numpy(np.float64)
    slow = np.hypot(np.nan_to_num(pr), np.nan_to_num(pd_)) <= C.ORPHANS_RATE_MAX + C.ORPHANS_KNOWN_RATE_TOL
    p = pred[slow].reset_index(drop=True)
    pr, pd_ = pr[slow], pd_[slow]
    fit = out["fit_ok"].to_numpy(bool) & np.isfinite(out["ra_fit"].to_numpy(np.float64))
    if not len(p) or not fit.any():
        return name, v, sep
    from .sky import angsep
    pra, pdec, pmjd = (p[c].to_numpy(np.float64) for c in ("ra", "dec", "mjd"))
    tree = SkyTree(pra, pdec)
    idx = np.flatnonzero(fit)
    reach = C.ORPHANS_KNOWN_ARCSEC + (C.ORPHANS_RATE_MAX + C.ORPHANS_KNOWN_RATE_TOL) * C.ORPHANS_KNOWN_DAYS * 24.0
    g = {c: out[c].to_numpy(np.float64) for c in ("ra_fit", "dec_fit", "mjd_fit", "drift_e", "drift_n", "rate_err")}
    for k, js in zip(idx, tree.query_radius(g["ra_fit"][idx], g["dec_fit"][idx], reach)):
        if not js:
            continue
        js = np.asarray(js, np.int64)
        dt = (pmjd[js] - g["mjd_fit"][k]) * 24.0
        tol = C.ORPHANS_KNOWN_RATE_TOL + 3.0 * np.nan_to_num(g["rate_err"][k], nan=1.0)
        okj = (np.abs(dt) <= C.ORPHANS_KNOWN_DAYS * 24.0) & (np.hypot(pr[js] - g["drift_e"][k], pd_[js] - g["drift_n"][k]) <= tol)
        js, dt = js[okj], dt[okj]
        if not len(js):
            continue
        r2, d2 = tangent_deproject(g["ra_fit"][k], g["dec_fit"][k], g["drift_e"][k] * dt, g["drift_n"][k] * dt)
        s = angsep(r2, d2, pra[js], pdec[js]) * 3600.0
        m = int(np.argmin(s))
        if s[m] <= C.ORPHANS_KNOWN_ARCSEC:
            name[k], sep[k] = str(p["name"].iloc[js[m]]), float(s[m])
            v[k] = pd.to_numeric(p["v"].iloc[js[m]], errors="coerce")
    return name, v, sep


def load_known(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    """known.load_predictions for the zone; None (and a warning) when it fails."""
    try:
        from .known import load_predictions
        photos = store.read_parquet(photolist_rel(zone.name))
        return load_predictions(store, photos, zone)
    except Exception as e:  # noqa: BLE001
        L.warning("orphans %s: no known-object predictions (%r)", zone.name, e)
        return None


def run(store: Store, zone: C.Zone, workers: int = 16, pred: pd.DataFrame | None = None) -> dict:
    """The step (never raises: the error goes into the stats, so `all` goes on to score and suspects).
    pred: known.load_predictions' result when already loaded (tiles.py); else loaded here."""
    try:
        return _run(store, zone, workers, pred)
    except Exception as e:
        L.exception("orphans %s failed", zone.name)
        return {"error": repr(e)[:500]}


def _run(store: Store, zone: C.Zone, workers: int = 16, pred: pd.DataFrame | None = None) -> dict:
    t0 = time.time()
    out, st = compute(store, zone, workers)
    if pred is None and len(out):
        pred = load_known(store, zone)
    kn, kv, ks = known_match(out, pred)
    out["known_name"], out["known_v"], out["known_sep_arcsec"] = kn.astype(str), kv.astype(np.float32), ks.astype(np.float32)
    st["known_predictions"] = None if pred is None else int(len(pred))
    ok = out["drift_ok"].to_numpy(bool) if len(out) else np.zeros(0, bool)
    o = out[ok].reset_index(drop=True).copy()
    o["ra"], o["dec"], o["mjd"] = o["ra_fit"], o["dec_fit"], o["mjd_fit"]
    pairs, controls, pst = pd.DataFrame(), [], {"real_tested": 0, "control_tested_mean": 0.0}
    skipped = None
    if len(o) > C.ORPHANS_MAX_PAIRING:
        skipped = f"{len(o)} drift_ok orphans > ORPHANS_MAX_PAIRING ({C.ORPHANS_MAX_PAIRING}): pairing skipped"
        L.warning("orphans %s: %s", zone.name, skipped)
    elif len(o) >= 2:
        E, VE = earth_posvel(o["mjd"].to_numpy(np.float64))
        o["_E"], o["_VE"] = list(E), list(VE)
        sraw = store.read_bytes(results_rel(zone.name, "sources.parquet"))
        import pyarrow.parquet as pq
        names = pq.ParquetFile(io.BytesIO(sraw)).schema_arrow.names
        cols = ["source_id", "flux_ujy", *(["err_ujy"] if "err_ujy" in names else [])]
        sp = pq.read_table(io.BytesIO(sraw), columns=cols,
                           filters=[("source_id", "in", o["source_id"].astype(np.int64).tolist())]).to_pandas()
        sp = sp.set_index("source_id").reindex(o["source_id"].to_numpy(np.int64))
        spec = np.stack([np.asarray(x, np.float64) if x is not None else np.full(NDET * NCH_DET, np.nan) for x in sp["flux_ujy"]])
        err = (np.stack([np.asarray(x, np.float64) if x is not None else np.full(NDET * NCH_DET, np.nan) for x in sp["err_ujy"]])
               if "err_ujy" in sp else None)
        flux, frel = detector_flux_errors(spec, err)
        for k in range(NDET):
            o[f"flux_d{k + 1}_ujy"] = flux[:, k].astype(np.float32)
        pairs, controls, pst = pairs_and_controls(o, flux, frel)
    passing = pairs[pairs["passes"]].sort_values("p", ascending=False) if len(pairs) else pairs
    n_pass = int(len(passing))
    lam = (float(np.sum(controls)) + 1.0) / max(len(controls), 1) if controls else float("nan")
    from scipy.stats import poisson
    pois = float(poisson.sf(n_pass - 1, lam)) if (controls and n_pass > 0) else (1.0 if controls else None)
    # orphans.parquet: every candidate (reason empty = kept), with drift, track and pair flags
    in_pair = set()
    if n_pass:
        in_pair = set(o["source_id"].to_numpy()[passing["i"].to_numpy()]) | set(o["source_id"].to_numpy()[passing["j"].to_numpy()])
    out["in_pair"] = out["source_id"].isin(in_pair)
    keep_cols = [c for c in out.columns if not c.startswith("_")]
    store.write_parquet(results_rel(zone.name, "orphans.parquet"), out[keep_cols])
    # distant.json
    rows = []
    for r in passing.head(C.ORPHANS_REPORT_MAX).itertuples():
        a, b = o.iloc[int(r.i)], o.iloc[int(r.j)]
        dl, db = ecliptic_offset(np.array([a.ra]), np.array([a.dec]), np.array([b.ra]), np.array([b.dec]))
        rows.append({
            "source_id_a": int(a.source_id), "source_id_b": int(b.source_id), "visit_a": int(a.visit), "visit_b": int(b.visit),
            "ra_a": round(float(a.ra), 6), "dec_a": round(float(a.dec), 6), "mjd_a": round(float(a.mjd), 4),
            "ra_b": round(float(b.ra), 6), "dec_b": round(float(b.dec), 6), "mjd_b": round(float(b.mjd), 4),
            "sep_arcsec": round(float(np.hypot(dl[0], db[0])), 1), "dlon_arcsec": round(float(dl[0]), 1),
            "dlat_arcsec": round(float(db[0]), 1),
            "distance_au": round(float(r.distance_au), 1), "distance_range_au": [round(float(r.distance_lo_au), 1),
                                                                                round(float(r.distance_hi_au), 1)],
            "speed_au_yr": round(float(r.speed_au_yr), 3),
            "drift_a_arcsec_h": [round(float(a.drift_e), 4), round(float(a.drift_n), 4), round(float(a.rate_err), 4)],
            "drift_b_arcsec_h": [round(float(b.drift_e), 4), round(float(b.drift_n), 4), round(float(b.rate_err), 4)],
            "pred_a_arcsec_h": [round(float(r.pred_a_e), 4), round(float(r.pred_a_n), 4)],
            "pred_b_arcsec_h": [round(float(r.pred_b_e), 4), round(float(r.pred_b_n), 4)],
            "flux_a_ujy": {f"D{k}": _r(a[f"flux_d{k}_ujy"]) for k in range(1, NDET + 1)},
            "flux_b_ujy": {f"D{k}": _r(b[f"flux_d{k}_ujy"]) for k in range(1, NDET + 1)},
            "flux_ratio": _r(r.flux_ratio, 3), "n_common_det": int(r.n_common_det),
            "n_dots_a": int(a.n_fit), "n_dots_b": int(b.n_fit), "snr_a": _r(a.snr_max), "snr_b": _r(b.snr_max),
            "chi2_drift": _r(r.chi2_drift, 2), "chi2_flux": _r(r.chi2_flux, 2), "dof": int(r.dof), "p": _r(r.p, 4),
            "known_a": a.known_name or None, "known_b": b.known_name or None, "score": _r(r.p, 4)})
    known = out[out["known_name"].to_numpy() != ""]
    opened = out[out["drift_ok"].to_numpy(bool) & (out["track_in_zone"].to_numpy(np.float64) < 1.0)]
    depth = st.get("depth_ab_snr5", {})
    rep = {
        "zone": zone.name, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "searched": {
            "what": "single-visit sources without Gaia/CatWISE/2MASS whose spot the other visit covered, paired across visits",
            "rate_arcsec_h": [C.ORPHANS_RATE_MIN, C.ORPHANS_RATE_MAX],
            "sep_arcsec": [C.ORPHANS_SEP_MIN_ARCSEC, C.ORPHANS_SEP_MAX_ARCSEC],
            "distance_grid_au": [C.ORPHANS_R_GRID_AU[0], C.ORPHANS_R_GRID_AU[1]],
            "orbit": f"bound: speed <= {C.ORPHANS_VMAX_FACTOR} x circular",
            "depth_ab_snr5": depth,
            "reach_au_at_d2_depth": reach_au(depth.get("D2")),
            "pair_min_p": C.ORPHANS_PAIR_MIN_P},
        "counts": {k: st.get(k) for k in ("single_visit_sources", "unmatched", "unmatched_covered", "orphans",
                                          "drift_fitted", "drift_ok")} | {"reasons": st.get("reasons"),
                                                                         "pairs_tested": pst["real_tested"],
                                                                         "pairs": n_pass,
                                                                         "open_orphans": int(len(opened)),
                                                                         "known_orphans": int(len(known))},
        "control": {"n": len(controls), "passing_per_control": controls,
                    "expected_chance_pairs": round(float(np.mean(controls)), 3) if controls else None,
                    "tested_per_control": pst["control_tested_mean"], "poisson_p": pois,
                    "method": "later visits' orphans shifted cyclically by 25–75% of the box in RA and Dec"},
        "pairs": rows,
        "open": [{"source_id": int(x.source_id), "visit": int(x.visit), "ra": round(float(x.ra_fit), 6),
                  "dec": round(float(x.dec_fit), 6), "mjd": round(float(x.mjd_fit), 4), "mjd_other": round(float(x.mjd_other), 4),
                  "rate_arcsec_h": _r(x.rate, 4), "track_in_zone": _r(x.track_in_zone, 2),
                  "track_70au": [round(float(x.track_ra_near), 5), round(float(x.track_dec_near), 5)],
                  "track_1000au": [round(float(x.track_ra_far), 5), round(float(x.track_dec_far), 5)]}
                 for x in opened.head(C.ORPHANS_REPORT_MAX).itertuples()],
        "known": [{"source_id": int(x.source_id), "name": x.known_name, "v": _r(x.known_v), "sep_arcsec": _r(x.known_sep_arcsec, 1),
                   "visit": int(x.visit), "reason": x.reason, "drift_ok": bool(x.drift_ok), "in_pair": bool(x.in_pair),
                   "rate_arcsec_h": _r(x.rate, 4)} for x in known.head(C.ORPHANS_REPORT_MAX).itertuples()],
        "known_predictions": st.get("known_predictions"),
        "skipped": skipped,
        "limits": [
            "within-visit drift up to 1 arcsec/h: faster objects are movers (score/suspects); below ~0.3 arcsec/h a planet chains "
            "into one fixed source per visit, at 0.3-1 arcsec/h into fragments that need >= 3 sessions each",
            "the object must be detected (S/N >= 5) in >= 3 sessions of each visit: single-exposure depth, no stacking",
            "the partner of an orphan near the zone edge may lie in the next zone (see `open`); no cross-zone pass yet",
            "a catalogue entry within crossid's radius hides the object (CatWISE ~35k/deg2: ~8% of the sky near the ecliptic)",
            "positions are raw dots (no calib); errors scaled and floored (ORPHANS_POS_ERR_SCALE, ORPHANS_POS_FLOOR_ARCSEC)"],
    }
    store.write_json(results_rel(zone.name, "distant.json"), rep)
    summary = {**{k: v for k, v in st.items() if k not in ("zone",)}, "pairs_tested": pst["real_tested"], "pairs": n_pass,
               "control_n": len(controls), "control_expected": rep["control"]["expected_chance_pairs"],
               "control_poisson_p": pois, "open_orphans": int(len(opened)), "known_orphans": int(len(known)), "skipped": skipped,
               "wall_s": round(time.time() - t0, 1)}
    L.info("orphans %s: %s", zone.name, summary)
    return summary


def _r(x, nd: int = 2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if np.isfinite(v) else None
