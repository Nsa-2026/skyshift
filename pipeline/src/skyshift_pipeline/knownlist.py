"""Known-body lists (27 Nov, agent "known-forced"): for every photo of the sky, every known asteroid, comet, planet and
moon whose position at the photo's time falls on it or near it, whatever its brightness. The dots worker measures a
forced flux at each of these places (knownforced.py, env SKYSHIFT_KNOWN_FORCED=1). Built once on the PC before the
rerun; nothing here reads a photo or a bucket.

    uv run --project pipeline python -m skyshift_pipeline.knownlist --index ~/skyshift-data/photolists/s3index \\
        --mpcorb <mpcorb_<date>.parquet> [--mpcorb-dat MPCORB.DAT.gz] --comet-orbits <dir with cometels/allcometels> \\
        --out <list dir> [--kinds asteroids,comets,planets] [--workers 3] [--periods a,b] [--photos obs_ids.txt]

Layout: <out>/<kind>/<period>.parquet (kinds asteroids, comets, planets; one file per planning period of the photo
index), rows sorted by (obs_id, detector) in row groups of KNOWN_LIST_ROW_GROUP rows, so that the worker reads only
its photo's row groups (pyarrow filters on the row-group statistics); then <out>/<kind>/_complete.json (counts, CPU
seconds, bytes) when every period of the index is done. With --photos (a file of exposure obs_ids) only those
exposures' photos are listed: a test list, never marked complete.

- Asteroids: the MPC's orbits (mpcorb_<date>.parquet, the known step's kete snapshot: MPC mpcorb_extended), moved with
  kete's n-body propagation (light time included) to the period's first photo, then one kete fov_state_check over
  every photo's footprint pushed out KNOWN_LIST_PAD_DEG, seen from the Earth's centre at the index's photo time (mid
  of t_min, t_max). V from H and G (MPC_G_DEFAULT when missing) with the IAU H-G law (hg_mag; kete's hg_apparent_mag
  values). No magnitude cut: an object with no H is kept with V NaN.
  Position uncertainty: the orbit files give none directly. MPCORB.DAT (--mpcorb-dat) gives the MPC's uncertainty
  parameter U (0–9: the in-longitude runoff in 10 years, U_RUNOFF_ARCSEC) and the date of the last observation; the
  list's sigma_arcsec is the rough growth runoff(U) × |t − last obs| / 10 yr (at least runoff(U) / 100), NaN without U.
- Comets: stamplist.period_rows (cometels + allcometels, the same kete call), no magnitude cut.
- Planets and moons: JPL Horizons only (known_planets' bodies, config PLANET_SYSTEMS). A coarse geocentric table of
  each planet (PLANETS_COARSE_STEP_H) over the index's time range finds the photos whose centre comes within
  radius_deg + KNOWN_PLANET_NEAR_DEG of it; each body then gets one table (PLANETS_STEP_MIN steps, astrometric RA/Dec,
  rates, APmag, Δ) per period with such photos, interpolated to each photo's time and kept within KNOWN_LIST_PAD_DEG
  of its footprint.

Rows are geocentric (astrometric, light time to the Earth's centre) at mjd_ref: SPHEREx is up to ~7,100 km from the
Earth's centre, 9.8″/Δ(AU) of parallax, which the worker removes with the photo's own X_SC.. at MJD-AVG
(stamps.topocentric). A body closer than ~0.027 AU can be pushed more than the 0.1° pad off its photo: such rows can
be missing (rare: the bench has none).
"""

from __future__ import annotations

import argparse
import gzip
import json
import resource
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .stamplist import AU_KM, DEG_DAY_TO_ARCSEC_H, period_of  # noqa: F401 (AU_KM re-exported for knownforced)

L = log.get("knownlist")
KINDS = ("asteroids", "comets", "planets")
MJD_TO_JD = 2400000.5

# One row per (photo, body). Positions geocentric (astrometric) at mjd_ref, as stamplist.LIST_COLUMNS.
KNOWN_LIST_COLUMNS = {
    "obs_id": "str", "detector": "int8",
    "kind": "str",                 # asteroid | comet | planet | moon
    "name": "str",                 # MPC name, "(number)", the designation; Horizons' body name
    "number": "int32",             # MPC number (-1: none)
    "cls": "str",                  # MPC orbit class (MBA, Apollo, ...; comets "Comet P" ...), planet | moon
    "mjd_ref": "float64",          # UTC MJD of ra/dec (the photo index's mid-time)
    "ra": "float64", "dec": "float64",  # deg, ICRS, geocentric at mjd_ref
    "dra_arcsec_h": "float32", "ddec_arcsec_h": "float32",  # geocentric on-sky rates (dRA·cos dec), ″/h
    "delta_au": "float64",         # geocentric distance
    "r_au": "float32",             # heliocentric distance (NaN for Horizons rows)
    "phase_deg": "float32",        # Sun-body-Earth angle (NaN for Horizons rows)
    "v_pred": "float32",           # V: asteroids H-G, planets/moons Horizons APmag, comets the MPC total-magnitude law
    "sigma_arcsec": "float32",     # rough position uncertainty (asteroids from U; NaN: unknown)
    "u_param": "int8",             # MPC uncertainty parameter 0–9 (-1: unknown)
    "edge_deg": "float32",         # signed distance to the footprint edge (> 0 inside), geocentric position
    "h": "float32", "g": "float32",
}
# U -> upper bound of the 10-year in-longitude runoff (MPC's definition), arcsec
U_RUNOFF_ARCSEC = np.array([1.0, 4.4, 19.6, 84.0, 384.0, 1680.0, 7560.0, 33120.0, 147600.0, 648000.0])
HG_A = (3.33, 1.87)
HG_B = (0.63, 1.22)


def empty_list() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in KNOWN_LIST_COLUMNS.items()})


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    out = df[list(KNOWN_LIST_COLUMNS)].copy()
    return out.astype({c: t for c, t in KNOWN_LIST_COLUMNS.items() if t != "str"})


# ---------------------------------------------------------------- magnitudes, uncertainty

def hg_mag(h, g, r_au, delta_au, phase_deg) -> np.ndarray:
    """IAU H-G apparent magnitude (Bowell et al. 1989), vectorised; NaN beyond 160° phase (as kete)."""
    h, g = np.asarray(h, float), np.asarray(g, float)
    a = np.radians(np.asarray(phase_deg, float))
    t = np.tan(a / 2.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        p1 = np.exp(-HG_A[0] * t ** HG_B[0])
        p2 = np.exp(-HG_A[1] * t ** HG_B[1])
        m = h + 5.0 * np.log10(np.asarray(r_au, float) * np.asarray(delta_au, float)) - 2.5 * np.log10((1 - g) * p1 + g * p2)
    return np.where(np.degrees(a) > 160.0, np.nan, m)


def read_mpcorb_dat(path: Path) -> pd.DataFrame:
    """MPCORB.DAT(.gz) -> desig (unpacked, as the orbit snapshot's `desig`), u_param (-1 unknown), last_obs_mjd."""
    import kete
    packed, u, last = [], [], []
    op = gzip.open if str(path).endswith(".gz") else open
    with op(path, "rt", errors="replace") as f:
        started = False
        for ln in f:
            if not started:
                started = ln.startswith("-----")
                continue
            if len(ln) < 160:
                continue
            packed.append(ln[0:7].strip())
            c = ln[105:106]
            u.append(int(c) if c.isdigit() else -1)
            d = ln[194:202].strip()
            last.append(d if d.isdigit() and len(d) == 8 else "")
    des = []
    for p in packed:
        try:
            d = kete.mpc.unpack_designation(p)
        except Exception:  # noqa: BLE001
            d = p
        des.append(str(d))
    lm = pd.to_datetime(pd.Series(last), format="%Y%m%d", errors="coerce")
    mjd = (lm - pd.Timestamp("1858-11-17")).dt.total_seconds() / 86400.0
    return pd.DataFrame({"desig": des, "u_param": np.asarray(u, np.int8), "last_obs_mjd": mjd.to_numpy(float)})


def sigma_from_u(u, last_obs_mjd, mjd) -> np.ndarray:
    """Rough position uncertainty (″): runoff(U) × |mjd − last obs| / 10 yr, at least runoff(U) / 100. NaN without U."""
    u = np.asarray(u, np.int64)
    ok = (u >= 0) & (u <= 9)
    run = np.where(ok, U_RUNOFF_ARCSEC[np.clip(u, 0, 9)], np.nan)
    dt = np.abs(np.asarray(mjd, float) - np.asarray(last_obs_mjd, float)) / 3652.5
    s = run * np.where(np.isfinite(dt), dt, 1.0)
    return np.fmax(s, run / 100.0)


# ---------------------------------------------------------------- orbits

def asteroid_orbits(snapshot: Path, mpcorb_dat: Path | None = None) -> pd.DataFrame:
    """The orbit snapshot (kete's table) with name, number, h, g, class (known_mpc.object_table) and u_param,
    last_obs_mjd (from MPCORB.DAT when given; -1 / NaN otherwise). Row k is kete state desig str(k)."""
    from . import known_mpc as KM
    orb = pd.read_parquet(snapshot)
    orb["desig"] = orb["desig"].astype(str)
    orb = orb.reset_index(drop=True)
    objs = KM.object_table(orb)
    for c in ("name", "number", "h", "g", "class"):
        orb[f"o_{c}"] = objs[c].to_numpy()
    orb["u_param"], orb["last_obs_mjd"] = np.int8(-1), np.nan
    if mpcorb_dat is not None:
        u = read_mpcorb_dat(Path(mpcorb_dat)).drop_duplicates("desig")
        m = orb[["desig"]].merge(u, on="desig", how="left")
        orb["u_param"] = m["u_param"].fillna(-1).astype(np.int8).to_numpy()
        orb["last_obs_mjd"] = m["last_obs_mjd"].to_numpy(float)
    return orb


# ---------------------------------------------------------------- the kete check

def _vec(v) -> np.ndarray:
    return np.array([v.x, v.y, v.z], np.float64)


PREFILTER_DEG = 4.0            # a test list (--photos): states within this of a photo's centre at the period start ...
PREFILTER_DEG_PER_DAY = 1.0    # ... + this × the photos' time span, ...
PREFILTER_NEAR_AU = 0.5        # ... and every state closer to the Earth than this, go to the fov check


def near_states(kete, states: list, photos: pd.DataFrame, earth) -> list:
    """The states that can reach one of these photos during their time span (a test list over a few fields: kete's
    check then moves a few % of the orbits instead of all). Same rows as without the filter (test_known_forced)."""
    pos = np.array([_vec(s.pos.as_equatorial) for s in states], np.float64)  # Sun-centred, equatorial
    rel = pos - _vec(earth.pos.as_equatorial)
    dist = np.linalg.norm(rel, axis=1)
    u = rel / dist[:, None]
    span = float(photos["mjd"].max() - photos["mjd"].min())
    r = np.radians(PREFILTER_DEG + PREFILTER_DEG_PER_DAY * span)
    keep = dist < PREFILTER_NEAR_AU
    ra, dec = np.radians(photos["s_ra"].to_numpy(float)), np.radians(photos["s_dec"].to_numpy(float))
    cv = np.c_[np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)]
    for k in range(0, len(cv), 64):
        keep |= (u @ cv[k:k + 64].T > np.cos(r)).any(axis=1)
    return [s for s, k in zip(states, keep) if k]


def fov_rows(photos: pd.DataFrame, states: list, pad_deg: float, keep_states: dict | None = None,
             prefilter: bool = False) -> pd.DataFrame:
    """One kete fov_state_check from the Earth's centre at each photo's mjd over every photo's padded footprint.
    photos: obs_id, detector, mjd, s_region (one row per photo, any order; row k is FOV k). Rows: k (photo row), row
    (state desig as int), ra, dec, dra/ddec (″/h), delta_au, r_au, phase_deg. prefilter (a test list over a few
    fields; photos need s_ra, s_dec): only the states near_states keeps are checked."""
    import kete

    from .known_mpc import padded_corners
    jd = {o: kete.Time.from_mjd(float(m), scaling="utc").jd for o, m in photos.groupby("obs_id")["mjd"].first().items()}
    earth = {o: kete.spice.get_state("Earth", j) for o, j in jd.items()}
    states = kete.propagate_n_body(states, min(jd.values()))
    if keep_states is not None:  # the caller carries them to its next period (a few days on, not from the epoch)
        keep_states["states"] = states
    if prefilter:
        states = near_states(kete, states, photos, earth[min(jd, key=jd.get)])
    fovs = []
    for k, r in enumerate(photos.itertuples()):
        corners = [kete.Vector.from_ra_dec(a, d) for a, d in padded_corners(r.s_region, pad_deg)]
        fovs.append(kete.fov.SpherexCmos(corners, earth[r.obs_id], str(k), str(int(r.detector))))
    parts = []
    for ss in kete.fov_state_check(states, fovs):
        if not len(ss.states):
            continue
        k = int(ss.fov.uri)
        obs = _vec(ss.fov.observer.pos)
        rows = np.array([int(s.desig) for s in ss.states])
        rd = np.asarray(ss.ra_dec_with_rates, float).reshape(-1, 4)
        pos = np.array([_vec(s.pos) for s in ss.states])
        rel = pos - obs
        delta = np.linalg.norm(rel, axis=1)
        r_au = np.linalg.norm(pos, axis=1)
        with np.errstate(invalid="ignore"):
            cosph = np.einsum("ij,ij->i", -pos, -rel) / (r_au * delta)
        parts.append(pd.DataFrame({"k": k, "row": rows, "ra": rd[:, 0] % 360.0, "dec": rd[:, 1],
                                   "dra_arcsec_h": rd[:, 2] * DEG_DAY_TO_ARCSEC_H,
                                   "ddec_arcsec_h": rd[:, 3] * DEG_DAY_TO_ARCSEC_H, "delta_au": delta, "r_au": r_au,
                                   "phase_deg": np.degrees(np.arccos(np.clip(cosph, -1, 1)))}))
    if not parts:
        return pd.DataFrame(columns=["k", "row", "ra", "dec", "dra_arcsec_h", "ddec_arcsec_h", "delta_au", "r_au",
                                     "phase_deg"])
    return pd.concat(parts, ignore_index=True)


def _edges(photos: pd.DataFrame, k: np.ndarray, ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    from .sky import Footprint
    edge = np.empty(len(k))
    for kk, idx in pd.Series(np.arange(len(k))).groupby(k).indices.items():
        fp = Footprint.from_s_region(photos["s_region"].iloc[int(kk)])
        edge[idx] = fp.edge_distance_deg(ra[idx], dec[idx])
    return edge


def asteroid_rows(photos: pd.DataFrame, orb: pd.DataFrame, states: list | None = None,
                  pad_deg: float = C.KNOWN_LIST_PAD_DEG, keep_states: dict | None = None,
                  prefilter: bool = False) -> pd.DataFrame:
    """The asteroid rows of photos (obs_id, detector, mjd, s_region). states: kete states of every orbit row (any
    epoch); keep_states (a dict) gets them moved to these photos' first time."""
    from . import known_mpc as KM
    photos = photos.drop_duplicates(["obs_id", "detector"]).sort_values("mjd").reset_index(drop=True)
    if photos.empty or orb.empty:
        return empty_list()
    states = KM.build_states(orb[[c for c in orb.columns if not c.startswith("o_") and c not in
                                  ("u_param", "last_obs_mjd")]]) if states is None else states
    s = fov_rows(photos, states, pad_deg, keep_states, prefilter=prefilter)
    if s.empty:
        return empty_list()
    o = orb.iloc[s["row"].to_numpy()]
    ph = photos.iloc[s["k"].to_numpy()]
    h, g = o["o_h"].to_numpy(float), o["o_g"].to_numpy(float)
    mjd = ph["mjd"].to_numpy(np.float64)
    num = pd.to_numeric(pd.Series(o["o_number"].to_numpy(object)), errors="coerce").fillna(-1).to_numpy(np.int64)
    out = pd.DataFrame({
        "obs_id": ph["obs_id"].to_numpy(object), "detector": ph["detector"].to_numpy(), "kind": "asteroid",
        "name": o["o_name"].to_numpy(object), "number": num, "cls": o["o_class"].to_numpy(object), "mjd_ref": mjd,
        "ra": s["ra"].to_numpy(), "dec": s["dec"].to_numpy(), "dra_arcsec_h": s["dra_arcsec_h"].to_numpy(),
        "ddec_arcsec_h": s["ddec_arcsec_h"].to_numpy(), "delta_au": s["delta_au"].to_numpy(),
        "r_au": s["r_au"].to_numpy(), "phase_deg": s["phase_deg"].to_numpy(),
        "v_pred": hg_mag(h, g, s["r_au"], s["delta_au"], s["phase_deg"]),
        "sigma_arcsec": sigma_from_u(o["u_param"].to_numpy(), o["last_obs_mjd"].to_numpy(float), mjd),
        "u_param": o["u_param"].to_numpy(np.int8), "edge_deg": _edges(photos, s["k"].to_numpy(), s["ra"].to_numpy(),
                                                                       s["dec"].to_numpy()),
        "h": h, "g": g,
    })
    return _typed(out)


def comet_rows(photos: pd.DataFrame, orb_dir: Path, pad_deg: float = C.KNOWN_LIST_PAD_DEG) -> pd.DataFrame:
    """stamplist.period_rows (every comet, no magnitude cut) in this list's columns."""
    from . import stamplist as SL
    orb = SL.comet_orbits(Path(orb_dir))
    r = SL.period_rows(photos, orb, pad_deg=pad_deg, mag_max=99.0)
    if r.empty:
        return empty_list()
    out = pd.DataFrame({
        "obs_id": r["obs_id"], "detector": r["detector"], "kind": "comet", "name": r["name"], "number": -1,
        "cls": "comet", "mjd_ref": r["mjd_ref"], "ra": r["ra"], "dec": r["dec"], "dra_arcsec_h": r["dra_arcsec_h"],
        "ddec_arcsec_h": r["ddec_arcsec_h"], "delta_au": r["delta_au"], "r_au": r["r_au"], "phase_deg": r["phase_deg"],
        "v_pred": r["mag_pred"], "sigma_arcsec": np.nan, "u_param": -1, "edge_deg": r["edge_deg"], "h": r["h"],
        "g": r["g"],
    })
    return _typed(out)


# ---------------------------------------------------------------- planets and moons (JPL Horizons)

def horizons_table(naif: int, jd0: float, jd1: float, step: str) -> pd.DataFrame:
    """One body's geocentric table (QUANTITIES 1,3,9,20: astrometric RA/Dec, rates, APmag, Δ): jd, ra, dec,
    dra_arcsec_h, ddec_arcsec_h, v, delta_au."""
    import requests

    from .known_planets import HORIZONS_URL, _num
    params = {"format": "json", "COMMAND": f"'{naif}'", "OBJ_DATA": "NO", "MAKE_EPHEM": "YES", "EPHEM_TYPE": "OBSERVER",
              "CENTER": "'500@399'", "START_TIME": f"'JD {jd0:.6f}'", "STOP_TIME": f"'JD {jd1:.6f}'",
              "STEP_SIZE": f"'{step}'", "QUANTITIES": "'1,3,9,20'", "CSV_FORMAT": "YES", "ANG_FORMAT": "DEG",
              "CAL_FORMAT": "JD", "EXTRA_PREC": "YES"}
    last = None
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            res = requests.get(HORIZONS_URL, params=params, timeout=120)
            res.raise_for_status()
            text = res.json()["result"]
            if "$$SOE" not in text:
                raise ValueError(text.strip()[:300])
            head, body = text.split("$$SOE", 1)
            body = body.split("$$EOE", 1)[0]
            names = [h.strip() for h in [ln for ln in head.splitlines() if ln.startswith("Date")][-1].split(",")]
            col = {k: names.index(next(n for n in names if n.startswith(p)))
                   for k, p in (("jd", "Date"), ("ra", "R.A."), ("dec", "DEC"), ("dra_arcsec_h", "dRA*cosD"),
                                ("ddec_arcsec_h", "d(DEC)/dt"), ("v", "APmag"), ("delta_au", "delta"))}
            rows = [[_num(f[col[k]]) for k in col] for f in
                    ([x.strip() for x in ln.split(",")] for ln in body.strip().splitlines())]
            return pd.DataFrame(rows, columns=list(col))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * attempt)
    raise RuntimeError(f"Horizons {naif} failed after {C.NET_RETRIES} tries: {last}")


def _interp(eph: pd.DataFrame, mjd) -> dict:
    jd = np.asarray(mjd, float) + MJD_TO_JD
    t = eph["jd"].to_numpy(float)
    ra = np.degrees(np.unwrap(np.radians(eph["ra"].to_numpy(float))))
    out = {"ra": np.interp(jd, t, ra) % 360.0}
    for k in ("dec", "dra_arcsec_h", "ddec_arcsec_h", "v", "delta_au"):
        out[k] = np.interp(jd, t, eph[k].to_numpy(float)) if k in eph else np.full(jd.shape, np.nan)
    return out


def planet_near(photos: pd.DataFrame, coarse: dict[str, pd.DataFrame], near_deg: float = C.KNOWN_PLANET_NEAR_DEG
                ) -> dict[str, np.ndarray]:
    """planet -> bool mask of photos whose centre (s_ra, s_dec) is within radius_deg + near_deg of the planet."""
    from .sky import angsep
    out = {}
    for planet, eph in coarse.items():
        p = _interp(eph, photos["mjd"].to_numpy(float))
        sep = angsep(photos["s_ra"].to_numpy(float), photos["s_dec"].to_numpy(float), p["ra"], p["dec"])
        out[planet] = np.asarray(sep <= C.PLANET_SYSTEMS[planet]["radius_deg"] + near_deg)
    return out


def planet_rows(photos: pd.DataFrame, coarse: dict[str, pd.DataFrame], fetch=horizons_table,
                pad_deg: float = C.KNOWN_LIST_PAD_DEG, stats: dict | None = None) -> pd.DataFrame:
    """Planet and moon rows of one period's photos (obs_id, detector, mjd, s_region, s_ra, s_dec). One Horizons
    table per body over the near photos' time span."""
    from .sky import Footprint
    photos = photos.drop_duplicates(["obs_id", "detector"]).reset_index(drop=True)
    parts = []
    stats = {} if stats is None else stats
    stats.setdefault("horizons_queries", 0)
    for planet, near in planet_near(photos, coarse).items():
        if not near.any():
            continue
        g = photos[near]
        pad = C.PLANETS_STEP_MIN / 1440.0
        j0, j1 = float(g["mjd"].min()) - pad + MJD_TO_JD, float(g["mjd"].max()) + pad + MJD_TO_JD
        sysd = C.PLANET_SYSTEMS[planet]
        fps = [Footprint.from_s_region(s) for s in g["s_region"]]
        for naif, name, cls in [(sysd["naif"], planet, "planet"), *[(n, nm, "moon") for n, nm in sysd["moons"]]]:
            eph = fetch(naif, j0, j1, f"{C.PLANETS_STEP_MIN}m")
            stats["horizons_queries"] += 1
            p = _interp(eph, g["mjd"].to_numpy(float))
            edge = np.array([fp.edge_distance_deg(np.array([a]), np.array([d]))[0]
                             for fp, a, d in zip(fps, p["ra"], p["dec"])])
            keep = np.isfinite(p["ra"]) & (edge >= -pad_deg)
            if not keep.any():
                continue
            parts.append(pd.DataFrame({
                "obs_id": g["obs_id"].to_numpy(object)[keep], "detector": g["detector"].to_numpy()[keep],
                "kind": cls, "name": name, "number": -1, "cls": cls, "mjd_ref": g["mjd"].to_numpy(float)[keep],
                "ra": p["ra"][keep], "dec": p["dec"][keep], "dra_arcsec_h": p["dra_arcsec_h"][keep],
                "ddec_arcsec_h": p["ddec_arcsec_h"][keep], "delta_au": p["delta_au"][keep], "r_au": np.nan,
                "phase_deg": np.nan, "v_pred": p["v"][keep], "sigma_arcsec": np.nan, "u_param": -1,
                "edge_deg": edge[keep], "h": np.nan, "g": np.nan}))
    return _typed(pd.concat(parts, ignore_index=True)) if parts else empty_list()


def coarse_tables(mjd0: float, mjd1: float, fetch=horizons_table) -> dict[str, pd.DataFrame]:
    return {p: fetch(s["naif"], mjd0 - 1 + MJD_TO_JD, mjd1 + 1 + MJD_TO_JD, f"{C.PLANETS_COARSE_STEP_H}h")
            for p, s in C.PLANET_SYSTEMS.items()}


# ---------------------------------------------------------------- writing, reading

def write_list(rows: pd.DataFrame, dest: Path) -> int:
    """Sorted by (obs_id, detector), row groups of KNOWN_LIST_ROW_GROUP rows, zstd; atomic. Returns bytes."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows = rows.sort_values(["obs_id", "detector", "kind", "name"]).reset_index(drop=True)
    t = pa.Table.from_pandas(_typed(rows) if len(rows) else empty_list(), preserve_index=False)
    tmp = dest.with_suffix(".parquet.part")
    floats = [f.name for f in t.schema if pa.types.is_floating(f.type)]
    pq.write_table(t, tmp, compression="zstd", compression_level=9, row_group_size=C.KNOWN_LIST_ROW_GROUP,
                   use_dictionary=["obs_id", "kind", "name", "cls"], use_byte_stream_split=floats or False,
                   write_statistics=["obs_id", "detector"])
    tmp.replace(dest)
    return dest.stat().st_size


def read_photo(path: str, obs_id: str, detector: int) -> pd.DataFrame:
    """One photo's rows of a list file: only the row groups whose obs_id statistics can hold it are read."""
    import pyarrow.parquet as pq
    t = pq.read_table(path, filters=[("obs_id", "==", str(obs_id)), ("detector", "==", int(detector))])
    return t.to_pandas()


# ---------------------------------------------------------------- the whole index

def index_periods(index_dir: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for f in sorted(Path(index_dir).glob("*/*.parquet")):
        out.setdefault(f.stem, []).append(f)
    return out


def _cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


_ORB: dict = {}


def _chunk_job(periods: list[str], files: dict[str, list[str]], kinds: list[str], out_dir: str, opts: dict) -> list[dict]:
    """Several periods in time order in one process: the asteroids' kete states are moved from one period to the
    next (days) instead of from the orbit epoch each time (~45 s wall on 3 threads saved per period)."""
    return [_period_job(p, files[p], kinds, out_dir, opts) for p in periods]


def _period_job(period: str, files: list[str], kinds: list[str], out_dir: str, opts: dict) -> dict:
    t0, c0 = time.time(), _cpu()
    cols = ["obs_id", "detector", "mjd", "s_region", "s_ra", "s_dec"]
    photos = pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True)
    if opts.get("photos"):
        keep = set(opts["photos"])
        photos = photos[photos["obs_id"].isin(keep)]
    photos = photos.drop_duplicates(["obs_id", "detector"]).sort_values("mjd").reset_index(drop=True)
    res = {"period": period, "photos": int(len(photos)), "kinds": {}}
    for kind in kinds:
        k0, kc = time.time(), _cpu()
        st: dict = {}
        if photos.empty:
            rows = empty_list()
        elif kind == "asteroids":
            from . import known_mpc as KM
            key = ("ast", opts["mpcorb"], opts.get("mpcorb_dat"))
            if _ORB.get("key") != key:
                orb = asteroid_orbits(Path(opts["mpcorb"]), opts.get("mpcorb_dat") and Path(opts["mpcorb_dat"]))
                cols = [c for c in orb.columns if not c.startswith("o_") and c not in ("u_param", "last_obs_mjd")]
                _ORB.clear()
                _ORB.update({"key": key, "orb": orb, "states": KM.build_states(orb[cols])})
            keep: dict = {}
            rows = asteroid_rows(photos, _ORB["orb"], states=_ORB["states"], pad_deg=opts["pad_deg"], keep_states=keep,
                                 prefilter=bool(opts.get("photos")))
            if "states" in keep:
                _ORB["states"] = keep["states"]
        elif kind == "comets":
            rows = comet_rows(photos, Path(opts["comet_orbits"]), pad_deg=opts["pad_deg"])
        elif kind == "planets":
            coarse = {p: pd.DataFrame(v) for p, v in opts["coarse"].items()}
            rows = planet_rows(photos, coarse, pad_deg=opts["pad_deg"], stats=st)
        else:
            raise ValueError(kind)
        d = Path(out_dir) / kind
        d.mkdir(parents=True, exist_ok=True)
        nbytes = write_list(rows, d / f"{period}.parquet")
        inside = rows["edge_deg"].to_numpy() > 0 if len(rows) else np.zeros(0, bool)
        res["kinds"][kind] = {"rows": int(len(rows)), "rows_inside": int(inside.sum()), "bytes": nbytes,
                              "wall_s": round(time.time() - k0, 1), "cpu_s": round(_cpu() - kc, 1), **st}
    res["wall_s"], res["cpu_s"] = round(time.time() - t0, 1), round(_cpu() - c0, 1)
    return res


def build(index_dir: Path, out: Path, kinds: list[str], mpcorb: Path | None, mpcorb_dat: Path | None,
          comet_orbits: Path | None, workers: int = 3, periods: list[str] | None = None,
          photos: list[str] | None = None, pad_deg: float = C.KNOWN_LIST_PAD_DEG, force: bool = False) -> dict:
    """Every period's list into out/<kind>/ (a period whose files all exist is skipped unless force); the summary
    (per period: rows, bytes, CPU s) to out/_build_<time>.json; _complete.json per kind when every index period is
    done and no --photos filter was given."""
    out = Path(out)
    allp = index_periods(index_dir)
    todo = sorted(periods or allp)
    jobs = [p for p in todo if force or not all((out / k / f"{p}.parquet").exists() for k in kinds)]
    opts = {"mpcorb": str(mpcorb) if mpcorb else None, "mpcorb_dat": str(mpcorb_dat) if mpcorb_dat else None,
            "comet_orbits": str(comet_orbits) if comet_orbits else None, "pad_deg": pad_deg, "photos": photos}
    t0, c0 = time.time(), _cpu()
    if "planets" in kinds:
        idx = pd.concat([pd.read_parquet(f, columns=["mjd"]) for p in jobs for f in allp[p]], ignore_index=True)
        coarse = coarse_tables(float(idx["mjd"].min()), float(idx["mjd"].max())) if len(idx) else {}
        opts["coarse"] = {p: t.to_dict(orient="list") for p, t in coarse.items()}
    L.info("known list: kinds %s, %d periods (%d to do), %d workers", kinds, len(todo), len(jobs), workers)
    res = []
    start = {p: float(min(pd.read_parquet(f, columns=["mjd"])["mjd"].min() for f in allp[p])) for p in jobs}
    jobs = sorted(jobs, key=start.get)
    nw = max(1, min(workers, len(jobs)))
    chunks = [list(c) for c in np.array_split(np.array(jobs, dtype=object), nw) if len(c)]
    with ProcessPoolExecutor(max_workers=nw) as ex:
        futs = [ex.submit(_chunk_job, c, {p: [str(f) for f in allp[p]] for p in c}, kinds, str(out), opts)
                for c in chunks]
        for f in futs:
            for r in f.result():
                res.append(r)
                L.info("known list %s: %d photos, %s, %.0f s", r["period"], r["photos"],
                       {k: v["rows"] for k, v in r["kinds"].items()}, r["wall_s"])
    ch = resource.getrusage(resource.RUSAGE_CHILDREN)
    summary = {"kinds": kinds, "pad_deg": pad_deg, "mpcorb": opts["mpcorb"], "mpcorb_dat": opts["mpcorb_dat"],
               "comet_orbits": opts["comet_orbits"], "photos_filter": len(photos) if photos else None,
               "periods": res, "wall_s": round(time.time() - t0, 1),
               "cpu_s_main": round(_cpu() - c0, 1), "cpu_s_children_total": round(ch.ru_utime + ch.ru_stime, 1),
               "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"_build_{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.json").write_text(json.dumps(summary, indent=1))
    if not photos:
        for k in kinds:
            if set(allp) <= {p.stem for p in (out / k).glob("*.parquet")}:
                (out / k / "_complete.json").write_text(json.dumps({k2: v for k2, v in summary.items()
                                                                    if k2 != "periods"}, indent=1))
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--index", required=True, help="photolists/s3index/ (the photo index)")
    ap.add_argument("--out", required=True, help="the known list folder (<kind>/ go in it)")
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--mpcorb", help="the known step's MPC orbit snapshot mpcorb_<date>.parquet")
    ap.add_argument("--mpcorb-dat", help="MPCORB.DAT.gz (U parameter and last observation; optional)")
    ap.add_argument("--comet-orbits", help="folder with the MPC's cometels.json.gz and allcometels.json.gz")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--periods", default="", help="comma list (default: every period of the index)")
    ap.add_argument("--photos", default="", help="file with one exposure obs_id per line: list only those (a test)")
    ap.add_argument("--pad-deg", type=float, default=C.KNOWN_LIST_PAD_DEG)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    log.setup()
    photos = [s.strip() for s in Path(a.photos).expanduser().read_text().split() if s.strip()] if a.photos else None
    ex = lambda p: Path(p).expanduser() if p else None  # noqa: E731
    s = build(Path(a.index).expanduser(), Path(a.out).expanduser(), [k for k in a.kinds.split(",") if k],
              ex(a.mpcorb), ex(a.mpcorb_dat), ex(a.comet_orbits), a.workers,
              [p for p in a.periods.split(",") if p] or None, photos, a.pad_deg, a.force)
    print(json.dumps({k: v for k, v in s.items() if k != "periods"}, default=str))


if __name__ == "__main__":
    main()
