"""Known asteroids and comets near a position at given times, fast enough for a live request.

The pipeline (pipeline/known_mpc.py) moves all ~1.6 million MPC orbits with kete n-body physics: ~15 s per
call, too slow for /api/known and frames.json `known[]`. This module splits the work in two:

1. Coarse cut, numpy only (`candidates`). Every asteroid is moved with two-body (Kepler) physics from its
   MPC elements, in float32, all at once (~0.1 s per instant for 1.6 M orbits). An object is kept when it can
   be inside the field: its two-body position is within r + margin of the centre. The margin is per object:
   - `err_deg`: the largest two-body vs n-body difference seen from the Earth's centre over the index's
     time range, measured when the index is built (kete, every STEP_DAYS, NEO-like orbits every
     NEO_STEP_DAYS), times ERR_SAFETY;
   - OBSERVER_AU / Δ: SPHEREx is up to ~7,100 km from the Earth's centre, the Moon moves the Earth
     ~4,700 km from the Earth-Moon barycentre (whose approximate orbit is used here), plus that orbit's own
     error; Δ = the object's distance from the Earth (AU);
   - MARGIN0_DEG: light time (an apparent shift of at most v/c ≈ 0.01°) and float32 rounding.
   Times that are close together (within GROUP_DAYS) share one evaluation at the group's two ends: an
   object is kept when the arc between its two positions can reach the field (the nearer end within
   r + margin + half the arc + the bend of the path over the group, see `_group_cut`); each exact time
   is then checked on that short list. Objects closer than NEAR_AU at request time (close approaches),
   `always` objects (two-body error over MAX_ERR_DEG) and all comets skip the cut.
2. Exact positions with kete (`known_near`): the short list is moved with n-body physics to each time and
   checked against a cone of the requested radius, with light time, from the Earth's centre or from
   SPHEREx's own position (IMAGE header X_SC..VZ_SC), the same computation as the pipeline's.

The index is one .npz file (numpy only to read): the orbits (q, e, i, node, argp, peri_time, epoch, H, G),
the per-object margin, names, and the time range it was checked for. `build_index` needs kete and pandas
(`uv sync --extra known` in core/, or the pipeline's environment). Reading the index and the coarse cut
need numpy only; `known_near` needs kete (the `known` extra).

Times are MJD (UTC) at the API. kete and the MPC use TDB Julian dates; TT − UTC = 69.184 s since 2017 and
TDB − TT is under 2 ms.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

INDEX_VERSION = 1
GMS = 2.9591220828559115e-4          # Sun's GM, AU³/day² (kete's value)
OBLIQUITY = np.radians(84381.448 / 3600.0)  # J2000 ecliptic → equatorial (MPC and kete)
JD_MJD = 2400000.5
TT_UTC_DAYS = 69.184 / 86400.0       # 37 leap seconds + 32.184 s
AU_KM = 149_597_870.7

# Index time range: the SPHEREx mission so far (24 Apr 2025 – 5 Oct 2026) with room on both sides.
MJD_LO = 60736.0                     # 2025-03-01
MJD_HI = 61465.0                     # 2027-03-01
STEP_DAYS = 30.0                     # n-body check of every orbit every STEP_DAYS ...
NEO_STEP_DAYS = 2.0                  # ... and of NEO-like orbits (q < NEO_Q_AU) every NEO_STEP_DAYS
NEO_Q_AU = 1.3
ERR_SAFETY = 1.25                    # measured two-body error × this (between samples it can grow a little)
ERR_FLOOR_DEG = 0.005
MAX_ERR_DEG = 20.0                   # above this an object is `always` sent to kete (~100 orbits)
NEAR_AU = 0.2                        # at request time, objects nearer than this skip the coarse cut
OBSERVER_AU = 4.0e-4                 # SPHEREx 7,100 km + Moon 4,700 km + approximate Earth orbit (~3e-5 AU)
MARGIN0_DEG = 0.02
GROUP_DAYS = 16.0                    # times within this share one coarse evaluation
V_KEEP = 21.5                        # build: objects never brighter than this in the range are left out
V_MAX = 21.0                         # known_near: drop fainter objects (as the pipeline's MPC_V_MAX)


# ---------------------------------------------------------------- orbits and names

def orbit_arrays(asteroids: Any, comets: Any | None = None) -> dict[str, np.ndarray]:
    """Columns of the MPC snapshot tables (kete.mpc.fetch_known_orbit_data, fetch_known_comet_orbit_data;
    pandas DataFrames or dicts of arrays) → one dict of arrays, asteroids first, then comets.
    Names as the pipeline's object_table: the MPC name, else "(number)", else the designation; a name
    used twice gets " [designation]" appended."""
    parts = [(asteroids, False)] + ([(comets, True)] if comets is not None else [])
    cols: dict[str, list] = {k: [] for k in ("desig", "name", "q", "e", "inc", "node", "argp", "tp", "epoch",
                                             "h", "g", "comet")}
    for tab, is_comet in parts:
        n = len(tab["desig"])
        desig = np.array([str(d).strip() for d in np.asarray(tab["desig"], dtype=object)], dtype=object)
        raw = np.asarray(tab["name"], dtype=object) if "name" in tab else np.full(n, None, dtype=object)
        numbered = np.array([d.isdigit() for d in desig], bool)
        name = np.array([
            (str(r).strip() if isinstance(r, str) and r.strip() else (f"({d})" if num else d))
            for r, d, num in zip(raw, desig, numbered)], dtype=object)
        cols["desig"].append(desig)
        cols["name"].append(name)
        for k, src in (("q", "peri_dist"), ("e", "ecc"), ("inc", "incl"), ("node", "lon_node"),
                       ("argp", "peri_arg"), ("tp", "peri_time"), ("epoch", "epoch")):
            cols[k].append(np.asarray(tab[src], float))
        h = np.asarray(tab["h_mag"], float) if "h_mag" in tab else np.full(n, np.nan)
        g = np.asarray(tab["g_phase"], float) if "g_phase" in tab else np.full(n, np.nan)
        cols["h"].append(np.where(h > 90, np.nan, h))  # MPC writes 99.99 for "unknown"
        cols["g"].append(np.where(np.isfinite(g), g, 0.15))
        cols["comet"].append(np.full(n, is_comet))
    out = {k: np.concatenate(v) for k, v in cols.items()}
    name = out["name"]
    _, inv, counts = np.unique(name.astype(str), return_inverse=True, return_counts=True)
    dup = counts[inv] > 1
    name[dup] = [f"{a} [{d}]" for a, d in zip(name[dup], out["desig"][dup])]
    return out


def _pack_names(names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    enc = [str(s).encode("utf-8") for s in names]
    off = np.zeros(len(enc) + 1, np.int64)
    off[1:] = np.cumsum([len(b) for b in enc])
    return np.frombuffer(b"".join(enc), np.uint8).copy(), off


# ---------------------------------------------------------------- two-body, numpy

def _perifocal_basis(inc_deg, node_deg, argp_deg, dtype=np.float32) -> tuple[np.ndarray, np.ndarray]:
    i, o, w = (np.radians(np.asarray(x, float)) for x in (inc_deg, node_deg, argp_deg))
    co, so, cw, sw, ci, si = np.cos(o), np.sin(o), np.cos(w), np.sin(w), np.cos(i), np.sin(i)
    P = np.stack([co * cw - so * sw * ci, so * cw + co * sw * ci, sw * si])
    Q = np.stack([-co * sw - so * cw * ci, -so * sw + co * cw * ci, cw * si])
    return P.astype(dtype), Q.astype(dtype)


@dataclass
class Kepler:
    """Elliptic two-body orbits ready for fast evaluation (heliocentric, ecliptic J2000, AU)."""
    n: np.ndarray    # mean motion, rad/day (float64)
    tp: np.ndarray   # time of perihelion, JD TDB (float64)
    e: np.ndarray    # float32
    a: np.ndarray    # float32
    b: np.ndarray    # float32, a·sqrt(1 − e²)
    P: np.ndarray    # (3, N) float32
    Q: np.ndarray    # (3, N) float32

    @classmethod
    def from_elements(cls, q, e, inc, node, argp, tp, dtype=np.float32) -> "Kepler":
        q, e = np.asarray(q, float), np.asarray(e, float)
        a = q / (1.0 - e)
        P, Q = _perifocal_basis(inc, node, argp, dtype)
        return cls(n=np.sqrt(GMS / a**3), tp=np.asarray(tp, float), e=e.astype(dtype), a=a.astype(dtype),
                   b=(a * np.sqrt(1.0 - e * e)).astype(dtype), P=P, Q=Q)

    def take(self, rows: np.ndarray) -> "Kepler":
        return Kepler(self.n[rows], self.tp[rows], self.e[rows], self.a[rows], self.b[rows],
                      self.P[:, rows], self.Q[:, rows])

    def positions(self, jd_tdb, rows: np.ndarray | None = None) -> np.ndarray:
        """(3, N) heliocentric ecliptic positions at jd_tdb (a scalar, or one time per object)."""
        k = self if rows is None else self.take(rows)
        dt = k.e.dtype
        M = np.mod(k.n * (np.asarray(jd_tdb, float) - k.tp), 2.0 * np.pi)
        M = np.where(M > np.pi, M - 2.0 * np.pi, M).astype(dt)  # −π..π: Newton from E0 below always converges
        e = k.e
        E = np.where(e > 0.8, np.where(M >= 0, np.pi, -np.pi).astype(dt), M + e * np.sin(M)).astype(dt)
        for _ in range(12 if dt == np.float32 else 20):
            E -= (E - e * np.sin(E) - M) / (1.0 - e * np.cos(E))
        x, y = k.a * (np.cos(E) - e), k.b * np.sin(E)
        return x * k.P + y * k.Q


# Earth-Moon barycentre, Standish's approximate elements (J2000 ecliptic, 1800–2050; JPL "Approximate
# Positions of the Planets", Table 1): a, e, I, L, long. peri, node and their rates per century.
_EMB = dict(a=(1.00000261, 0.00000562), e=(0.01671123, -0.00004392), I=(-0.00001531, -0.01294668),
            L=(100.46457166, 35999.37244981), lp=(102.93768193, 0.32327364), node=(0.0, 0.0))


def earth_position(jd_tdb: float) -> np.ndarray:
    """Approximate heliocentric ecliptic J2000 position (AU) of the Earth-Moon barycentre; within ~3e-5 AU of
    the Earth's centre plus the Moon's pull (~3e-5 AU), see OBSERVER_AU."""
    T = (float(jd_tdb) - 2451545.0) / 36525.0
    el = {k: v[0] + v[1] * T for k, v in _EMB.items()}
    w = el["lp"] - el["node"]
    M = np.radians((el["L"] - el["lp"]) % 360.0)
    e = el["e"]
    E = M + e * np.sin(M)
    for _ in range(6):
        E -= (E - e * np.sin(E) - M) / (1.0 - e * np.cos(E))
    a = el["a"]
    P, Q = _perifocal_basis(el["I"], el["node"], w, np.float64)
    return a * (np.cos(E) - e) * P + a * np.sqrt(1 - e * e) * np.sin(E) * Q


def ecl_to_eq(v: np.ndarray) -> np.ndarray:
    """(3, …) ecliptic J2000 → equatorial (ICRS to well under an arcsec)."""
    c, s = np.cos(OBLIQUITY), np.sin(OBLIQUITY)
    return np.stack([v[0], c * v[1] - s * v[2], s * v[1] + c * v[2]])


def unit_radec(ra_deg: float, dec_deg: float) -> np.ndarray:
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def mjd_to_jd_tdb(mjd) -> np.ndarray:
    return np.asarray(mjd, float) + JD_MJD + TT_UTC_DAYS


# ---------------------------------------------------------------- the index

@dataclass
class KnownIndex:
    data: dict[str, np.ndarray]

    def __post_init__(self):
        d = self.data
        self.comet = d["comet"].astype(bool)
        self.ast_rows = np.flatnonzero(~self.comet)
        self.comet_rows = np.flatnonzero(self.comet)
        a = self.ast_rows
        self.kep = Kepler.from_elements(d["q"][a], d["e"][a], d["inc"][a], d["node"][a], d["argp"][a], d["tp"][a])
        self.err_rad = np.radians(d["err_deg"][a].astype(np.float32))
        self.always = d["err_deg"][a] > MAX_ERR_DEG
        self.mjd_lo, self.mjd_hi = float(d["mjd_lo"]), float(d["mjd_hi"])

    def __len__(self) -> int:
        return len(self.comet)

    def name(self, row: int) -> str:
        o = self.data["name_off"]
        return self.data["name_blob"][o[row]:o[row + 1]].tobytes().decode("utf-8")

    @property
    def meta(self) -> dict[str, Any]:
        d = self.data
        return {"version": int(d["version"]), "snapshot": str(d["snapshot"]), "built": str(d["built"]),
                "mjd_lo": self.mjd_lo, "mjd_hi": self.mjd_hi, "v_keep": float(d["v_keep"]),
                "n_asteroids": int(len(self.ast_rows)), "n_comets": int(len(self.comet_rows)),
                "n_always": int(self.always.sum())}


def save_index(path: str, data: Mapping[str, np.ndarray]) -> None:
    np.savez_compressed(path, **data)


def load_index(path) -> KnownIndex:
    """From a path or a binary file object (e.g. io.BytesIO of the bytes read from the bucket)."""
    with np.load(path, allow_pickle=False) as z:
        data = {k: z[k] for k in z.files}
    if int(data["version"]) != INDEX_VERSION:
        raise ValueError(f"known index version {int(data['version'])}, this code reads {INDEX_VERSION}")
    return KnownIndex(data)


# ---------------------------------------------------------------- coarse cut (numpy only)

def _geocentric(idx: KnownIndex, jd_tdb: float, rows: np.ndarray | None = None):
    """Unit vectors (3, N, equatorial), Δ and r (AU) of the asteroids (all, or `rows` of ast_rows order)."""
    helio = idx.kep.positions(jd_tdb, rows)
    geo = helio - earth_position(jd_tdb)[:, None].astype(helio.dtype)
    delta = np.sqrt((geo * geo).sum(0))
    r = np.sqrt((helio * helio).sum(0))
    return ecl_to_eq(geo / delta), delta, r


def _margin(idx: KnownIndex, rows, delta) -> np.ndarray:
    err = idx.err_rad if rows is None else idx.err_rad[rows]
    return err + OBSERVER_AU / delta + np.radians(MARGIN0_DEG)


def _groups(jds: np.ndarray) -> list[np.ndarray]:
    order = np.argsort(jds)
    out, cur = [], [order[0]]
    for k in order[1:]:
        if jds[k] - jds[cur[0]] > GROUP_DAYS:
            out.append(np.array(cur))
            cur = []
        cur.append(k)
    out.append(np.array(cur))
    return out


def _group_cut(idx: KnownIndex, c: np.ndarray, rad: float, ja: float, jb: float) -> np.ndarray:
    """Asteroids (positions in ast_rows) that can be within `rad` of c at some time in [ja, jb]."""
    ua, da, ra_ = _geocentric(idx, ja)
    cos_a = np.clip(c @ ua, -1.0, 1.0)
    if jb <= ja:
        keep = np.arccos(cos_a) < rad + _margin(idx, None, da)
        return np.flatnonzero(keep | (da < NEAR_AU) | idx.always)
    ub, db, rb = _geocentric(idx, jb)
    cos_b = np.clip(c @ ub, -1.0, 1.0)
    chord = np.arccos(np.clip((ua * ub).sum(0), -1.0, 1.0))
    dmin = np.minimum(da, db)
    # bend of the path away from the arc: |relative acceleration| s²/8 seen from dmin
    s = jb - ja
    acc = GMS * (1.0 / (0.9 * np.minimum(ra_, rb)) ** 2 + 1.04)
    bend = acc * s * s / 8.0 / dmin
    near = np.arccos(np.maximum(cos_a, cos_b))
    keep = near < rad + _margin(idx, None, dmin) + 0.5 * chord + bend
    return np.flatnonzero(keep | (dmin < NEAR_AU) | idx.always)


def candidates(idx: KnownIndex, ra: float, dec: float, radius_deg: float, mjds: Iterable[float]) -> np.ndarray:
    """Index rows (asteroids and every comet) that can be within radius_deg of (ra, dec) at one of `mjds`
    (UTC), seen from near the Earth. Complete within the index's time range by construction (margins above);
    a superset: kete decides."""
    jds = np.unique(mjd_to_jd_tdb(np.atleast_1d(np.asarray(list(mjds), float))))
    c = unit_radec(ra, dec).astype(np.float32)
    rad = np.radians(radius_deg)
    keep: list[np.ndarray] = []
    for g in _groups(jds):
        tj = jds[g]
        pos = _group_cut(idx, c, rad, float(tj.min()), float(tj.max()))
        if len(tj) == 1 and tj.max() == tj.min():
            keep.append(pos)
            continue
        # each exact time, on the short list only
        sub = np.zeros(len(pos), bool)
        for t in tj:
            u, d, _ = _geocentric(idx, float(t), pos)
            ok = np.arccos(np.clip(c @ u, -1.0, 1.0)) < rad + _margin(idx, pos, d)
            sub |= ok | (d < NEAR_AU) | idx.always[pos]
        keep.append(pos[sub])
    ast = np.unique(np.concatenate(keep)) if keep else np.zeros(0, np.int64)
    return np.concatenate([idx.ast_rows[ast], idx.comet_rows])


# ---------------------------------------------------------------- exact positions (kete)

def _kete():
    try:
        import kete
    except ImportError as e:  # pragma: no cover
        raise ImportError("known_near needs kete: `uv sync --extra known` in core/ (skyshift-core[known])") from e
    return kete


def kete_states(kete, idx_or_data, rows: np.ndarray) -> list:
    """kete States for index rows (named by row number), as kete.conversion.table_to_states."""
    d = getattr(idx_or_data, "data", idx_or_data)
    return [kete.CometElements(str(int(k)), float(d["epoch"][k]), float(d["e"][k]), float(d["inc"][k]),
                               float(d["q"][k]), float(d["argp"][k]), float(d["tp"][k]), float(d["node"][k])).state
            for k in rows]


SC_POS = ("X_SC", "Y_SC", "Z_SC")
SC_VEL = ("VX_SC", "VY_SC", "VZ_SC")


def observer_state(kete, mjd: float, sc: Mapping[str, float] | None = None):
    """kete State of the observer at mjd (UTC): the Earth's centre, or SPHEREx when `sc` holds the IMAGE
    header's X_SC..VZ_SC (geocentric equatorial km, km/s; as pipeline known_mpc.observer_state)."""
    jd = kete.Time.from_mjd(float(mjd), scaling="utc").jd
    earth = kete.spice.get_state("Earth", jd)
    if not sc or any(sc.get(k) is None for k in (*SC_POS, *SC_VEL)):
        return earth
    e_pos, e_vel = earth.pos.as_ecliptic, earth.vel.as_ecliptic
    sp = kete.Vector([sc[k] / AU_KM for k in SC_POS], kete.Frames.Equatorial).as_ecliptic
    sv = kete.Vector([sc[k] * 86400.0 / AU_KM for k in SC_VEL], kete.Frames.Equatorial).as_ecliptic
    pos = kete.Vector([e_pos.x + sp.x, e_pos.y + sp.y, e_pos.z + sp.z], kete.Frames.Ecliptic)
    vel = kete.Vector([e_vel.x + sv.x, e_vel.y + sv.y, e_vel.z + sv.z], kete.Frames.Ecliptic)
    return kete.State("SPHEREx", jd, pos, vel, kete.Frames.Ecliptic, 10)


def exact(kete, idx: KnownIndex | Mapping[str, np.ndarray], rows: np.ndarray, ra: float, dec: float, radius_deg: float,
          mjds: Sequence[float], observers: Sequence[Mapping[str, float] | None] | None = None) -> list[dict]:
    """kete n-body + light time for `rows`: one cone of radius_deg per mjd. Rows of
    {k (mjd position), row, ra, dec, v (None without H)}."""
    if not len(rows):
        return []
    obs = [observer_state(kete, m, observers[k] if observers else None) for k, m in enumerate(mjds)]
    states = kete.propagate_n_body(kete_states(kete, idx, rows), min(o.jd for o in obs), suppress_errors=True)
    states = [s for s in states if s.is_finite]
    centre = kete.Vector.from_ra_dec(float(ra), float(dec))
    fovs = [kete.ConeFOV(centre, float(radius_deg), o) for o in obs]
    by_jd = {round(o.jd, 9): k for k, o in enumerate(obs)}
    d = getattr(idx, "data", idx)
    h, g = d["h"], d["g"]
    out = []
    for ss in kete.fov_state_check(states, fovs):
        if not len(ss.states):
            continue
        k = by_jd[round(ss.fov.observer.jd, 9)]
        rd = np.asarray(ss.ra_dec_with_rates, float).reshape(-1, 4)
        sun2obs = ss.fov.observer.pos
        for st, (sra, sdec, _, _) in zip(ss.states, rd):
            row = int(st.desig)
            hh = float(h[row])
            v = float(kete.flux.hg_apparent_mag(st.pos, sun2obs, hh, float(g[row]))) if np.isfinite(hh) else None
            out.append({"k": k, "row": row, "ra": float(sra) % 360.0, "dec": float(sdec), "v": v})
    return out


def _sep_deg(ra1, dec1, ra2, dec2) -> float:
    return float(np.degrees(np.arccos(np.clip(unit_radec(ra1, dec1) @ unit_radec(ra2, dec2), -1.0, 1.0))))


def known_near(ra: float, dec: float, radius_deg: float, mjds: Sequence[float], *, index: KnownIndex,
               epoch_ids: Sequence[Any] | None = None,
               observers: Sequence[Mapping[str, float] | None] | None = None,
               v_max: float | None = V_MAX, stats: dict | None = None) -> list[dict[str, Any]]:
    """Known asteroids and comets within radius_deg of (ra, dec) at each of `mjds` (UTC), from MPC orbits
    moved with kete. Entries in the contracts/api.md `known[]` shape:
        {name, epoch_id, ra, dec, v, source: "mpc"}
    `epoch_id` is epoch_ids[k] for mjds[k] (None without epoch_ids, as /api/known). Sorted by time,
    then nearest first. `observers[k]`: the IMAGE header's X_SC..VZ_SC for mjds[k], else the Earth's
    centre. Objects fainter than v_max are dropped; comets (no V) are kept. `stats`, if given, gets the
    counts and timings."""
    kete = _kete()
    mjds = [float(m) for m in mjds]
    if not mjds:
        return []
    if min(mjds) < index.mjd_lo or max(mjds) > index.mjd_hi:
        raise ValueError(f"MJD outside the known index's range {index.mjd_lo:.0f}–{index.mjd_hi:.0f}: rebuild it")
    if v_max is not None and v_max > float(index.data["v_keep"]):
        raise ValueError(f"v_max {v_max} is fainter than the index keeps (V < {float(index.data['v_keep'])})")
    # Equal times are computed once and their objects given to every epoch at that time (D1+D4, D2+D5,
    # D3+D6 are exposed at the same moment; `exact` keys its cones by time, so a repeated time would
    # hand all its objects to one of the epochs only).
    members: dict[float, list[int]] = {}
    for k, m in enumerate(mjds):
        members.setdefault(round(m, 8), []).append(k)
    firsts = [ks[0] for ks in members.values()]
    times = [mjds[k] for k in firsts]
    obs = [observers[k] for k in firsts] if observers else None
    t0 = time.perf_counter()
    rows = candidates(index, ra, dec, radius_deg, times)
    t1 = time.perf_counter()
    found = exact(kete, index, rows, ra, dec, radius_deg, times, obs)
    t2 = time.perf_counter()
    found = [f for f in found if v_max is None or f["v"] is None or f["v"] <= v_max]
    for f in found:
        f["sep"] = _sep_deg(ra, dec, f["ra"], f["dec"])
    found.sort(key=lambda f: (times[f["k"]], f["sep"]))
    if stats is not None:
        stats.update({"candidates": int(len(rows)), "found": len(found), "coarse_s": round(t1 - t0, 3),
                      "kete_s": round(t2 - t1, 3)})
    by_time: dict[int, list[dict]] = {}
    for f in found:
        by_time.setdefault(f["k"], []).append(f)
    order = sorted(range(len(times)), key=lambda u: times[u])
    return [{"name": index.name(f["row"]), "epoch_id": epoch_ids[k] if epoch_ids is not None else None,
             "ra": round(f["ra"], 6), "dec": round(f["dec"], 6),
             "v": None if f["v"] is None else round(f["v"], 2), "source": "mpc"}
            for u in order for k in members[round(times[u], 8)] for f in by_time.get(u, [])]


# ---------------------------------------------------------------- build (kete + pandas)

def hg_mag(h, g, helio: np.ndarray, geo: np.ndarray) -> np.ndarray:
    """V from H, G (Bowell et al. 1989) for (3, N) heliocentric and observer-centred positions."""
    r = np.sqrt((helio * helio).sum(0))
    d = np.sqrt((geo * geo).sum(0))
    cos_a = np.clip((helio * geo).sum(0) / (r * d), -1.0, 1.0)
    t = np.tan(np.arccos(cos_a) / 2.0)
    phi1, phi2 = np.exp(-3.33 * t ** 0.63), np.exp(-1.87 * t ** 1.22)
    return h + 5.0 * np.log10(r * d) - 2.5 * np.log10((1.0 - g) * phi1 + g * phi2)


def _kete_pos(states) -> np.ndarray:
    return np.array([s.pos for s in states], float).T


def build_index(asteroids: Any, comets: Any | None = None, *, snapshot: str = "", mjd_lo: float = MJD_LO,
                mjd_hi: float = MJD_HI, step_days: float = STEP_DAYS, neo_step_days: float = NEO_STEP_DAYS,
                v_keep: float | None = V_KEEP, log=print) -> dict[str, np.ndarray]:
    """The index arrays (save with save_index). For every asteroid, kete n-body positions every step_days
    (NEO-like orbits every neo_step_days) over [mjd_lo, mjd_hi] are compared with this module's two-body
    positions, seen from the Earth's centre: the largest angle is the object's err_deg. Also measured:
    the closest approach to the Earth and the brightest V; objects never brighter than v_keep are left out
    (unknown H: kept), objects that hit a planet (NaN) too. Comets are all kept, never cut."""
    kete = _kete()
    el = orbit_arrays(asteroids, comets)
    n_all = len(el["q"])
    ast = np.flatnonzero(~el["comet"])
    t_start = time.time()
    states = kete_states(kete, el, ast)
    log(f"{len(states)} asteroid states in {time.time() - t_start:.0f} s")
    kep = Kepler.from_elements(el["q"][ast], el["e"][ast], el["inc"][ast], el["node"][ast], el["argp"][ast],
                               el["tp"][ast], dtype=np.float64)
    h, g = el["h"][ast], el["g"][ast]
    err = np.zeros(len(ast))
    dmin = np.full(len(ast), np.inf)
    vmin = np.full(len(ast), np.inf)
    bad = np.zeros(len(ast), bool)

    def check(sel: np.ndarray, sts: list, mjd: float):
        jd = float(mjd_to_jd_tdb(mjd))
        earth = np.array(kete.spice.get_state("Earth", jd).pos, float)[:, None]
        p_nb = _kete_pos(sts)
        p_2b = kep.positions(jd, sel)
        g_nb, g_2b = p_nb - earth, p_2b - earth
        d_nb = np.sqrt((g_nb * g_nb).sum(0))
        cosang = (g_nb * g_2b).sum(0) / (d_nb * np.sqrt((g_2b * g_2b).sum(0)))
        ang = np.degrees(np.arccos(np.clip(cosang, -1.0, 1.0)))
        nan = ~np.isfinite(ang)
        bad[sel[nan]] = True
        ang = np.where(nan, 0.0, ang)
        err[sel] = np.maximum(err[sel], ang)
        dmin[sel] = np.fmin(dmin[sel], d_nb)
        vmin[sel] = np.fmin(vmin[sel], hg_mag(h[sel], g[sel], p_nb, g_nb))

    def sweep(sel: np.ndarray, step: float, label: str):
        t_sweep = time.time()
        times = np.arange(mjd_lo, mjd_hi + step / 2, step)
        mid = float(np.searchsorted(times, 61200.0))  # start near the MPC epoch (2026-06-10), go both ways
        sub = [states[k] for k in sel]
        for direction in (range(int(mid), len(times)), range(int(mid) - 1, -1, -1)):
            cur = sub
            for i in direction:
                cur = kete.propagate_n_body(cur, float(mjd_to_jd_tdb(times[i])), suppress_errors=True,
                                            suppress_impact_errors=True)
                check(sel, cur, float(times[i]))
        log(f"{label}: {len(sel)} orbits × {len(times)} times in {time.time() - t_sweep:.0f} s")

    sweep(np.arange(len(ast)), step_days, "all asteroids")
    neo = np.flatnonzero(el["q"][ast] < NEO_Q_AU)
    if neo_step_days and len(neo):
        sweep(neo, neo_step_days, f"NEO-like (q < {NEO_Q_AU} AU)")

    keep_ast = ~bad
    if v_keep is not None:
        keep_ast &= ~(vmin > v_keep) | ~np.isfinite(h)  # unknown H is kept (np.fmin skipped its NaN V)
    err_deg = err * ERR_SAFETY + ERR_FLOOR_DEG
    always = err_deg > MAX_ERR_DEG
    rows = np.sort(np.concatenate([ast[keep_ast], np.flatnonzero(el["comet"])]))
    full_err = np.full(n_all, np.nan, np.float32)
    full_err[ast] = err_deg
    full_always = np.zeros(n_all, bool)
    full_always[ast] = always
    blob, off = _pack_names(el["name"][rows])
    data = {
        "version": np.array(INDEX_VERSION), "snapshot": np.array(snapshot),
        "built": np.array(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        "mjd_lo": np.array(float(mjd_lo)), "mjd_hi": np.array(float(mjd_hi)),
        "v_keep": np.array(np.inf if v_keep is None else float(v_keep)),
        **{k: el[k][rows].astype(float) for k in ("q", "e", "inc", "node", "argp", "tp", "epoch")},
        "h": el["h"][rows].astype(np.float32), "g": el["g"][rows].astype(np.float32),
        "comet": el["comet"][rows], "err_deg": full_err[rows], "always": full_always[rows],
        "name_blob": blob, "name_off": off,
    }
    log(f"index: {len(rows)} rows ({int(keep_ast.sum())} asteroids of {len(ast)}, {int(el['comet'].sum())} comets), "
        f"{int(bad.sum())} lost (impact/NaN), {int(always[keep_ast].sum())} always; err_deg p50 "
        f"{np.median(err_deg[keep_ast]):.4f} p99 {np.percentile(err_deg[keep_ast], 99):.3f} "
        f"p99.9 {np.percentile(err_deg[keep_ast], 99.9):.3f}; {time.time() - t_start:.0f} s")
    return data


def main(argv: Sequence[str] | None = None) -> None:
    """python -m skyshift_core.known_index <mpcorb.parquet> <cometels.parquet> <out.npz> [--sample N]"""
    import argparse
    import os

    import pandas as pd

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("mpcorb")
    ap.add_argument("cometels")
    ap.add_argument("out")
    ap.add_argument("--sample", type=int, default=0, help="every Nth asteroid only (tests, timing)")
    ap.add_argument("--step-days", type=float, default=STEP_DAYS)
    ap.add_argument("--neo-step-days", type=float, default=NEO_STEP_DAYS)
    a = ap.parse_args(argv)
    ast = pd.read_parquet(a.mpcorb)
    if a.sample > 1:
        ast = ast.iloc[:: a.sample].reset_index(drop=True)
    com = pd.read_parquet(a.cometels)
    snap = f"{os.path.basename(a.mpcorb)} + {os.path.basename(a.cometels)}"
    data = build_index(ast, com, snapshot=snap, step_days=a.step_days, neo_step_days=a.neo_step_days)
    save_index(a.out, data)
    print(f"wrote {a.out}: {os.path.getsize(a.out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
