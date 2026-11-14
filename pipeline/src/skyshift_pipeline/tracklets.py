"""Step `tracklets` (after link, before score): one moving object followed across the sessions of a visit.

link's movers live inside one session (2-5 exposures over a few minutes): at S/N 7-9 the centroid error (~1″ per axis)
is larger than a main-belt asteroid's motion between exposures, so a mover alone can't show that it moves. Across
sessions it moves hundreds of arcsec (sessions of one visit are 1.6-54 h apart). This step (zone level, on the merged
movers.parquet):

1. links movers of different sessions of one visit into tracks:
   - seeds: two movers of two sessions at most TRACK_SEED_MAX_GAP_H apart, implied rate within
     TRACK_RATE_MIN..MAX_ARCSEC_H;
   - each seed's path (the chord through its two movers plus the visit's acceleration, below) is tested in the sessions
     up to TRACK_TRIPLET_WINDOW_H outside its two times: the mover closest to the prediction within the match radius
     (NSIGMA × prediction σ + curvature allowance ½ a_tol |(t-ta)(t-tb)|, TRACK_R_MIN..MAX_ARCSEC) joins it;
   - seeds with the most members first, a seed whose movers are taken is skipped: the members grow session by session
     (nearest in time first, across gaps up to TRACK_MAX_GAP_H) with local fits (members within TRACK_FIT_WINDOW_H,
     linear plus the visit's acceleration; quadratic with >= 4 members over >= TRACK_QUAD_MIN_SPAN_H), then members
     whose leave-one-out prediction misses them by more than the match radius are dropped; a track needs movers in
     >= TRACK_MIN_SESSIONS sessions.
   The visit's acceleration: main-belt asteroids seen from Earth's orbit share one apparent acceleration (the hunt's
   known asteroids: 0.028″/h², 90% within 0.0075″/h² of the visit's median vector, 99% within 0.016). A first pass
   with TRACK_ACCEL_ARCSEC_H2 as an isotropic allowance finds the long tracks (>= TRACK_ACCEL_MIN_SESSIONS sessions
   over >= TRACK_ACCEL_MIN_SPAN_H); their median acceleration vector (needs TRACK_ACCEL_MIN_TRACKS) is then the model
   and TRACK_ACCEL_TOL_ARCSEC_H2 the allowance in the second, final pass: 3-session chance tracks drop ~10×.
   Positions are on a plane tangent at the track's median position (arcsec, x east, y north); a mover's per-axis error
   is sqrt((TRACK_POS_K × PSF σ / snr)² + floor²) / sqrt(n_dots) with TRACK_SYS_ARCSEC added (SPHEREx's orbital
   parallax, model).
2. per mover on a track: the local fit's rate (″/h), position angle (east of north), rate σ, chi2_nu, residual.
3. recovery: at each other session of the visit within TRACK_RECOVER_EXT_H of the track, the predicted position at each
   unpaired dot's own time (link's free_dots.parquet: not in a fixed group, not within FIXED_VETO_ARCSEC of one,
   elong <= MOVER_MAX_ELONG) within TRACK_RECOVER_R_MIN..MAX_ARCSEC: those dots are recovered detections.
4. control: the same linking with every session's movers shifted by its own random 0.5-1.5° offset (fixed seed), so no
   real track survives: the chance-track count at this density, in the summary.

Writes results/<zone>/tracklets.parquet (one row per mover; tracklet_id -1 when on no track) and
results/<zone>/tracklet_dots.parquet (recovered dots). score counts a known sighting found when a mover OR a
recovered dot matches it. Visit labels come from the photo list; tracks never cross visits.
"""

from __future__ import annotations

import time
from typing import NamedTuple

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import config as C
from . import log
from .sky import chord, radec_to_xyz
from .store import Store, photolist_rel, results_rel

L = log.get("tracklets")
RAD_ARCSEC = 206264.80624709636
TRACK_COLUMNS = ["mover_id", "session", "visit", "mjd", "ra", "dec", "cell", "tracklet_id", "n_sessions", "n_movers",
                 "n_recovered_sessions", "span_h", "rate_arcsec_h", "pa_deg", "rate_sigma", "rate_snr", "vra_arcsec_h",
                 "vdec_arcsec_h", "fit_chi2_nu", "track_chi2_nu", "resid_arcsec", "fit_degree"]
DOT_COLUMNS = ["tracklet_id", "visit", "session", "obs_id", "detector", "dot_row", "ra", "dec", "mjd", "snr", "flux_ujy",
               "flags", "flags_peak", "sep_arcsec", "r_arcsec", "pred_ra", "pred_dec"]
FREE_DOTS = "free_dots.parquet"


class Accel(NamedTuple):
    """The visit's apparent acceleration (″/h², east and north) and the allowance around it."""
    ax: float = 0.0
    ay: float = 0.0
    tol: float = C.TRACK_ACCEL_ARCSEC_H2


# --- geometry ---------------------------------------------------------------------------------------------------
def tan_xy(ra0, dec0, ra, dec) -> tuple[np.ndarray, np.ndarray]:
    """Gnomonic projection (arcsec, x east, y north) on the plane tangent at (ra0, dec0); broadcasts."""
    a0, d0, a, d = (np.deg2rad(np.asarray(v, np.float64)) for v in (ra0, dec0, ra, dec))
    da = a - a0
    cosc = np.sin(d0) * np.sin(d) + np.cos(d0) * np.cos(d) * np.cos(da)
    x = np.cos(d) * np.sin(da) / cosc
    y = (np.cos(d0) * np.sin(d) - np.sin(d0) * np.cos(d) * np.cos(da)) / cosc
    return x * RAD_ARCSEC, y * RAD_ARCSEC


def tan_radec(ra0, dec0, x, y) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of tan_xy."""
    x, y = np.asarray(x, np.float64) / RAD_ARCSEC, np.asarray(y, np.float64) / RAD_ARCSEC
    d0 = np.deg2rad(np.asarray(dec0, np.float64))
    den = np.cos(d0) - y * np.sin(d0)
    ra = np.rad2deg(np.deg2rad(np.asarray(ra0, np.float64)) + np.arctan2(x, den)) % 360.0
    dec = np.rad2deg(np.arctan2(np.sin(d0) + y * np.cos(d0), np.hypot(x, den)))
    return ra, dec


def mover_sigma(snr, n_dots) -> np.ndarray:
    """Per-axis position error of a mover (arcsec), systematic included."""
    snr = np.maximum(np.asarray(snr, np.float64), 1.0)
    s = np.sqrt((C.TRACK_POS_K * C.PSF_SIGMA_ARCSEC / snr) ** 2 + C.TRACK_POS_FLOOR_ARCSEC ** 2)
    s = s / np.sqrt(np.maximum(np.asarray(n_dots, np.float64), 1.0))
    return np.sqrt(s ** 2 + C.TRACK_SYS_ARCSEC ** 2)


# --- local fits -------------------------------------------------------------------------------------------------
class Fit:
    """Weighted fit of positions vs time about t0 (arcsec, hours): x(t) = Σ cx[k] τ^k (+ ½ ax τ² when linear), τ = t - t0."""

    __slots__ = ("t0", "cx", "cy", "cov", "deg", "n", "t_lo", "t_hi", "chi2", "dof", "ax", "ay", "tol")

    def at(self, t) -> tuple[np.ndarray, np.ndarray]:
        tau = np.atleast_1d(np.asarray(t, np.float64) - self.t0)
        V = np.vander(tau, self.deg + 1, increasing=True)
        q = 0.5 * tau ** 2
        return V @ self.cx + self.ax * q, V @ self.cy + self.ay * q

    def sigma_at(self, t) -> np.ndarray:
        """Per-axis 1σ of the fitted position at t (from the members' errors)."""
        tau = np.atleast_1d(np.asarray(t, np.float64) - self.t0)
        V = np.vander(tau, self.deg + 1, increasing=True)
        return np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", V, self.cov, V), 0.0))

    def allowance(self, t) -> np.ndarray:
        """How far the true path may leave the fit at t (curvature a linear fit can't follow, jerk for a quadratic)."""
        t = np.atleast_1d(np.asarray(t, np.float64))
        if self.deg <= 1:
            return 0.5 * self.tol * np.abs((t - self.t_lo) * (t - self.t_hi))
        far = np.maximum(np.abs(t - self.t_lo), np.abs(t - self.t_hi))
        return C.TRACK_JERK_ARCSEC_H3 * far ** 3 / 6.0

    @property
    def rate(self) -> float:
        return float(np.hypot(self.cx[1], self.cy[1]))


def local_fit(t: np.ndarray, x: np.ndarray, y: np.ndarray, s: np.ndarray, t0: float, acc: Accel = Accel(),
              window: float = C.TRACK_FIT_WINDOW_H, exclude: int = -1, quad: bool = True) -> Fit:
    """Fit of the members within `window` hours of t0 (at least the 2 nearest in time), `exclude` left out."""
    dt = t - t0
    ok = np.ones(t.size, bool)
    if exclude >= 0:
        ok[exclude] = False
    use = ok & (np.abs(dt) <= window)
    if use.sum() < 2:
        cand = np.flatnonzero(ok)
        use = np.zeros(t.size, bool)
        use[cand[np.argsort(np.abs(dt[cand]), kind="stable")[:2]]] = True
    tt, xx, yy, ss = dt[use], x[use], y[use], s[use]
    n = tt.size
    span = float(tt.max() - tt.min()) if n else 0.0
    deg = 2 if (quad and n >= 4 and span >= C.TRACK_QUAD_MIN_SPAN_H) else 1
    f = Fit()
    f.t0, f.deg, f.n, f.tol = float(t0), deg, n, acc.tol
    f.ax, f.ay = (acc.ax, acc.ay) if deg == 1 else (0.0, 0.0)
    f.t_lo, f.t_hi = float(tt.min() + t0), float(tt.max() + t0)
    # curvature the model can't follow, as extra error on every member
    half = span / 2.0
    extra = 0.5 * acc.tol * half ** 2 if deg == 1 else C.TRACK_JERK_ARCSEC_H3 * half ** 3 / 6.0
    w = 1.0 / (ss ** 2 + extra ** 2)
    q = 0.5 * tt ** 2
    xx, yy = xx - f.ax * q, yy - f.ay * q
    A = np.vander(tt, deg + 1, increasing=True)
    M = A.T @ (A * w[:, None])
    try:
        cov = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        cov = np.linalg.pinv(M)
    f.cov = cov
    f.cx = cov @ (A.T @ (w * xx))
    f.cy = cov @ (A.T @ (w * yy))
    rx, ry = xx - A @ f.cx, yy - A @ f.cy
    f.chi2 = float(np.sum(w * (rx ** 2 + ry ** 2)))
    f.dof = 2 * n - 2 * (deg + 1)
    return f


def match_radius(sig_pred, sig_obs, allow, r_min: float, r_max: float) -> np.ndarray:
    return np.minimum(r_max, np.maximum(r_min, C.TRACK_NSIGMA * np.sqrt(sig_pred ** 2 + sig_obs ** 2)) + allow)


# --- linking ----------------------------------------------------------------------------------------------------
class _Visit:
    """The movers of one visit by session, with a KD-tree per session."""

    def __init__(self, t: np.ndarray, ra: np.ndarray, dec: np.ndarray, sig: np.ndarray, sess: np.ndarray, acc: Accel):
        self.t, self.ra, self.dec, self.sig, self.acc = t, ra, dec, sig, acc
        codes, inv = np.unique(sess, return_inverse=True)
        T = np.bincount(inv, weights=t) / np.bincount(inv)
        order = np.argsort(T, kind="stable")
        rank = np.empty_like(order)
        rank[order] = np.arange(order.size)
        self.k = rank[inv]                      # session index of each mover, by time
        self.T = T[order]
        self.idx = [np.flatnonzero(self.k == k) for k in range(self.T.size)]
        xyz = radec_to_xyz(ra, dec).reshape(-1, 3)
        self.trees = [cKDTree(xyz[i]) for i in self.idx]
        self.half = np.array([(t[i].max() - t[i].min()) / 2.0 for i in self.idx])

    def seeds(self) -> tuple[np.ndarray, np.ndarray]:
        A, B = [], []
        for ka in range(self.T.size):
            for kb in range(ka + 1, self.T.size):
                dT = self.T[kb] - self.T[ka]
                if dT > C.TRACK_SEED_MAX_GAP_H:
                    break
                r = C.TRACK_RATE_MAX_ARCSEC_H * (dT + self.half[ka] + self.half[kb]) + C.TRACK_R_MAX_ARCSEC
                for ia, lst in enumerate(self.trees[ka].query_ball_tree(self.trees[kb], chord(r))):
                    if lst:
                        A.extend([self.idx[ka][ia]] * len(lst))
                        B.extend(self.idx[kb][lst].tolist())
        a, b = np.asarray(A, np.int64), np.asarray(B, np.int64)
        if a.size == 0:
            return a, b
        dt = self.t[b] - self.t[a]
        x, y = tan_xy(self.ra[a], self.dec[a], self.ra[b], self.dec[b])
        rate = np.hypot(x, y) / np.where(dt > 0, dt, np.nan)
        ok = (dt > 0) & (rate >= C.TRACK_RATE_MIN_ARCSEC_H) & (rate <= C.TRACK_RATE_MAX_ARCSEC_H)
        return a[ok], b[ok]

    def triplets(self, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Each seed's path in the other sessions near it: (seed, mover, normalised distance) of the closest mover
        within the match radius per seed and session."""
        S, M, Z = [], [], []
        if a.size == 0:
            return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0)
        t, ra, dec, sig, acc = self.t, self.ra, self.dec, self.sig, self.acc
        ta, tb = t[a], t[b]
        xb, yb = tan_xy(ra[a], dec[a], ra[b], dec[b])
        vx, vy = xb / (tb - ta), yb / (tb - ta)
        rate = np.hypot(vx, vy)
        ka, kb = self.k[a], self.k[b]
        W = C.TRACK_TRIPLET_WINDOW_H

        def path(s_i, tc):  # chord through a and b plus the visit's acceleration
            q = 0.5 * (tc - ta[s_i]) * (tc - tb[s_i])
            return vx[s_i] * (tc - ta[s_i]) + acc.ax * q, vy[s_i] * (tc - ta[s_i]) + acc.ay * q

        for kc in range(self.T.size):
            Tc = self.T[kc]
            sel = np.flatnonzero((Tc >= ta - W) & (Tc <= tb + W) & (ka != kc) & (kb != kc))
            if sel.size == 0:
                continue
            px, py = path(sel, Tc)
            pra, pdec = tan_radec(ra[a[sel]], dec[a[sel]], px, py)
            n_c = self.idx[kc].size
            kk = min(3, n_c)
            reach = C.TRACK_R_MAX_ARCSEC + rate[sel] * (self.half[kc] + 0.05) + 1.0
            dist, nb = self.trees[kc].query(radec_to_xyz(pra, pdec).reshape(-1, 3), k=kk,
                                            distance_upper_bound=chord(float(reach.max())))
            dist, nb = dist.reshape(sel.size, kk), nb.reshape(sel.size, kk)
            for j in range(kk):
                v = nb[:, j] < n_c
                if not v.any():
                    continue
                s_i = sel[v]
                c = self.idx[kc][nb[v, j]]
                cx, cy = tan_xy(ra[a[s_i]], dec[a[s_i]], ra[c], dec[c])
                ex, ey = path(s_i, t[c])
                sep = np.hypot(cx - ex, cy - ey)
                f = (t[c] - ta[s_i]) / (tb[s_i] - ta[s_i])
                sp = np.sqrt((sig[a[s_i]] * (1 - f)) ** 2 + (sig[b[s_i]] * f) ** 2)
                allow = 0.5 * acc.tol * np.abs((t[c] - ta[s_i]) * (t[c] - tb[s_i]))
                r = match_radius(sp, sig[c], allow, C.TRACK_R_MIN_ARCSEC, C.TRACK_R_MAX_ARCSEC)
                ok = sep <= r
                S.append(s_i[ok])
                M.append(c[ok])
                Z.append(sep[ok] / r[ok])
        s = np.concatenate(S) if S else np.zeros(0, np.int64)
        if s.size == 0:
            return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0)
        m, z = np.concatenate(M), np.concatenate(Z)
        # the closest per (seed, session)
        o = np.lexsort((z, self.k[m], s))
        s, m, z = s[o], m[o], z[o]
        first = np.r_[True, (np.diff(s) != 0) | (np.diff(self.k[m]) != 0)]
        return s[first], m[first], z[first]


class Track:
    """Members (mover indices of a _Visit) and their positions on the plane tangent at the track's centre."""

    def __init__(self, V: _Visit, mem: list[int]):
        self.V = V
        self.mem = sorted(set(int(i) for i in mem), key=lambda i: V.t[i])
        self.centre()

    def centre(self) -> None:
        m = np.asarray(self.mem)
        self.ra0, self.dec0 = float(np.median(self.V.ra[m])), float(np.median(self.V.dec[m]))
        self._xy()

    def _xy(self) -> None:
        m = np.asarray(self.mem)
        self.x, self.y = tan_xy(self.ra0, self.dec0, self.V.ra[m], self.V.dec[m])
        self.t, self.s = self.V.t[m], self.V.sig[m]

    def add(self, i: int) -> None:
        self.mem = sorted([*self.mem, int(i)], key=lambda j: self.V.t[j])
        self._xy()

    def drop(self, pos: int) -> None:
        self.mem = [j for q, j in enumerate(self.mem) if q != pos]
        self.centre()

    def fit(self, t0: float, exclude: int = -1) -> Fit:
        return local_fit(self.t, self.x, self.y, self.s, t0, self.V.acc, exclude=exclude)

    def sessions(self) -> set[int]:
        return {int(self.V.k[i]) for i in self.mem}

    def predict_session(self, kc: int, used: np.ndarray) -> int:
        """The free mover of session kc closest to the track's prediction within the match radius, or -1."""
        V = self.V
        f = self.fit(V.T[kc])
        px, py = f.at(V.T[kc])
        pra, pdec = tan_radec(self.ra0, self.dec0, px, py)
        reach = C.TRACK_R_MAX_ARCSEC + f.rate * (V.half[kc] + 0.05) + 1.0
        cand = V.idx[kc][V.trees[kc].query_ball_point(radec_to_xyz(pra, pdec).reshape(3), chord(reach))]
        cand = cand[~used[cand]]
        if cand.size == 0:
            return -1
        cx, cy = tan_xy(self.ra0, self.dec0, V.ra[cand], V.dec[cand])
        ex, ey = f.at(V.t[cand])
        sep = np.hypot(cx - ex, cy - ey)
        r = match_radius(f.sigma_at(V.t[cand]), V.sig[cand], f.allowance(V.t[cand]), C.TRACK_R_MIN_ARCSEC, C.TRACK_R_MAX_ARCSEC)
        ok = sep <= r
        if not ok.any():
            return -1
        return int(cand[int(np.argmin(np.where(ok, sep / r, np.inf)))])

    def grow(self, used: np.ndarray) -> None:
        V = self.V
        tried = self.sessions()
        while True:
            todo = np.array([k for k in range(V.T.size) if k not in tried], np.int64)
            if not todo.size:
                return
            gap = np.abs(V.T[todo][:, None] - self.t[None, :]).min(axis=1)
            q = int(np.argmin(gap))
            if gap[q] > C.TRACK_MAX_GAP_H:
                return
            kc = int(todo[q])
            tried.add(kc)
            i = self.predict_session(kc, used)
            if i >= 0:
                self.add(i)

    def loo_z(self) -> np.ndarray:
        """Leave-one-out miss of every member over its match radius."""
        z = np.zeros(len(self.mem))
        for q in range(len(self.mem)):
            f = self.fit(self.t[q], exclude=q)
            px, py = f.at(self.t[q])
            sep = float(np.hypot(px[0] - self.x[q], py[0] - self.y[q]))
            r = match_radius(f.sigma_at(self.t[q]), self.s[q:q + 1], f.allowance(self.t[q]), C.TRACK_R_MIN_ARCSEC,
                             C.TRACK_R_MAX_ARCSEC)
            z[q] = sep / float(r[0])
        return z

    def prune(self) -> None:
        while len(self.mem) >= 3:
            z = self.loo_z()
            q = int(np.argmax(z))
            if z[q] <= 1.0:
                return
            self.drop(q)


def link_visit(t: np.ndarray, ra: np.ndarray, dec: np.ndarray, sig: np.ndarray, sess: np.ndarray,
               acc: Accel = Accel()) -> tuple[np.ndarray, dict]:
    """Track label per mover of one visit (-1: on no track; labels 0.. in order of acceptance) and counts."""
    lab = np.full(t.size, -1, np.int64)
    st = {"seeds": 0, "seeds_with_third": 0, "tracks": 0}
    if t.size < C.TRACK_MIN_SESSIONS:
        return lab, st
    V = _Visit(t, ra, dec, sig, sess, acc)
    a, b = V.seeds()
    st["seeds"] = int(a.size)
    s, m, z = V.triplets(a, b)
    if s.size == 0:
        return lab, st
    cnt = np.bincount(s, minlength=a.size)
    zsum = np.bincount(s, weights=z, minlength=a.size)
    cand = np.flatnonzero(cnt >= max(1, C.TRACK_MIN_SESSIONS - 2))
    st["seeds_with_third"] = int(cand.size)
    order = cand[np.lexsort((zsum[cand] / cnt[cand], -cnt[cand]))]
    starts = np.r_[0, np.flatnonzero(np.diff(s)) + 1]
    span = dict(zip(s[starts].tolist(), zip(starts.tolist(), np.r_[starts[1:], s.size].tolist())))
    used = np.zeros(t.size, bool)
    n = 0
    for sd in order:
        if used[a[sd]] or used[b[sd]]:
            continue
        lo, hi = span[int(sd)]
        extra = [int(x) for x in m[lo:hi] if not used[x]]
        tr = Track(V, [int(a[sd]), int(b[sd]), *extra])
        tr.prune()
        if len(tr.sessions()) < 2:
            continue
        tr.grow(used)
        tr.prune()
        ns = len(tr.sessions())
        if ns < C.TRACK_MIN_SESSIONS or (ns == C.TRACK_MIN_SESSIONS and np.ptp(tr.t) > C.TRACK_SHORT_MAX_SPAN_H):
            continue
        rate = tr.fit(float(np.median(tr.t))).rate
        if not (C.TRACK_RATE_MIN_ARCSEC_H <= rate <= C.TRACK_RATE_MAX_ARCSEC_H):
            continue
        idx = np.asarray(tr.mem)
        used[idx] = True
        lab[idx] = n
        n += 1
    st["tracks"] = n
    return lab, st


def visit_accel(t: np.ndarray, ra: np.ndarray, dec: np.ndarray, sig: np.ndarray, lab: np.ndarray) -> tuple[Accel, dict]:
    """The median acceleration vector of the long tracks (first pass), as the visit's model; Accel() when too few."""
    a = []
    for tid in np.unique(lab[lab >= 0]):
        i = np.flatnonzero(lab == tid)
        if i.size < C.TRACK_ACCEL_MIN_SESSIONS or np.ptp(t[i]) < C.TRACK_ACCEL_MIN_SPAN_H:
            continue
        ra0, dec0 = float(np.median(ra[i])), float(np.median(dec[i]))
        x, y = tan_xy(ra0, dec0, ra[i], dec[i])
        f = local_fit(t[i], x, y, sig[i], float(np.median(t[i])), Accel(), window=C.TRACK_ACCEL_MIN_SPAN_H)
        if f.deg == 2:
            a.append((2.0 * f.cx[2], 2.0 * f.cy[2]))
    st = {"accel_tracks": len(a)}
    if len(a) < C.TRACK_ACCEL_MIN_TRACKS:
        return Accel(), st
    ax, ay = np.median(np.asarray(a), axis=0)
    st.update({"accel_arcsec_h2": [round(float(ax), 5), round(float(ay), 5)]})
    return Accel(float(ax), float(ay), C.TRACK_ACCEL_TOL_ARCSEC_H2), st


# --- per-mover motion -------------------------------------------------------------------------------------------
def track_motion(t, ra, dec, sig, acc: Accel = Accel()) -> dict:
    """Per member: rate, PA, rate σ, local chi2_nu and residual, fit degree; plus the track's chi2_nu (leave-one-out)."""
    ra0, dec0 = float(np.median(ra)), float(np.median(dec))
    x, y = tan_xy(ra0, dec0, ra, dec)
    n = t.size
    out = {k: np.full(n, np.nan) for k in ("vx", "vy", "rate_sigma", "fit_chi2_nu", "resid")}
    out["deg"] = np.zeros(n, np.int8)
    chi2, dof = 0.0, 0
    for q in range(n):
        f = local_fit(t, x, y, sig, t[q], acc)
        out["vx"][q], out["vy"][q] = f.cx[1], f.cy[1]
        scale = np.sqrt(max(1.0, f.chi2 / f.dof)) if f.dof > 0 else 1.0
        out["rate_sigma"][q] = float(np.sqrt(f.cov[1, 1])) * scale
        out["fit_chi2_nu"][q] = f.chi2 / f.dof if f.dof > 0 else np.nan
        px, py = f.at(t[q])
        out["resid"][q] = float(np.hypot(px[0] - x[q], py[0] - y[q]))
        out["deg"][q] = f.deg
        g = local_fit(t, x, y, sig, t[q], acc, exclude=q)
        gx, gy = g.at(t[q])
        e2 = g.sigma_at(t[q])[0] ** 2 + sig[q] ** 2 + g.allowance(t[q])[0] ** 2
        chi2 += float(((gx[0] - x[q]) ** 2 + (gy[0] - y[q]) ** 2) / e2)
        dof += 2
    out["track_chi2_nu"] = chi2 / dof if dof else np.nan
    return out


# --- recovery ---------------------------------------------------------------------------------------------------
def load_free_dots(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    rel = results_rel(zone.name, FREE_DOTS)
    if not store.exists(rel):
        return None
    fd = store.read_parquet(rel)
    if not C.TRACK_RECOVER_NEAR_FIXED and "near_fixed" in fd:
        fd = fd[~fd["near_fixed"].to_numpy(bool)]
    if "elong" in fd:
        fd = fd[~(fd["elong"].to_numpy(np.float64) > C.MOVER_MAX_ELONG)]
    return fd.reset_index(drop=True)


def recover(tracks: list[dict], fd: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """Unpaired dots at each track's predicted positions in the other sessions of its visit.
    tracks: {id, visit, t (h), ra, dec, sig, sess, acc} per track; fd needs `_t` (h); sessions: session, visit, t, half (h)."""
    empty = pd.DataFrame({c: pd.Series(dtype="float64") for c in DOT_COLUMNS})
    if not tracks or fd is None or not len(fd):
        return empty
    t_fd = fd["_t"].to_numpy(np.float64)
    fra, fdec = fd["ra"].to_numpy(np.float64), fd["dec"].to_numpy(np.float64)
    fsnr = fd["snr"].to_numpy(np.float64)
    fs = fd["session"].to_numpy(np.int64)
    o = np.argsort(fs, kind="stable")
    u, st = np.unique(fs[o], return_index=True)
    groups = {int(k): o[a:b] for k, a, b in zip(u, st, np.r_[st[1:], o.size])}
    trees: dict[int, cKDTree] = {}
    s_vis = sessions["visit"].to_numpy(np.int64)
    s_t, s_half, s_id = sessions["t"].to_numpy(np.float64), sessions["half"].to_numpy(np.float64), sessions["session"].to_numpy(np.int64)
    keep_cols = [k for k in ("session", "obs_id", "detector", "dot_row", "ra", "dec", "mjd", "snr", "flux_ujy", "flags",
                             "flags_peak") if k in fd]
    hits: list[tuple] = []
    for tr in tracks:
        t, sig, acc = tr["t"], tr["sig"], tr["acc"]
        ra0, dec0 = float(np.median(tr["ra"])), float(np.median(tr["dec"]))
        x, y = tan_xy(ra0, dec0, tr["ra"], tr["dec"])
        own = set(int(s) for s in tr["sess"])
        lo, hi = t.min() - C.TRACK_RECOVER_EXT_H, t.max() + C.TRACK_RECOVER_EXT_H
        for j in np.flatnonzero((s_vis == tr["visit"]) & (s_t >= lo) & (s_t <= hi)):
            sid = int(s_id[j])
            if sid in own or sid not in groups:
                continue
            f = local_fit(t, x, y, sig, s_t[j], acc)
            px, py = f.at(s_t[j])
            pra, pdec = tan_radec(ra0, dec0, px, py)
            if sid not in trees:
                trees[sid] = cKDTree(radec_to_xyz(fra[groups[sid]], fdec[groups[sid]]).reshape(-1, 3))
            reach = C.TRACK_RECOVER_R_MAX_ARCSEC + f.rate * (s_half[j] + 0.05) + 1.0
            hit = trees[sid].query_ball_point(radec_to_xyz(pra, pdec).reshape(3), chord(reach))
            if not hit:
                continue
            c = groups[sid][np.asarray(hit, np.int64)]
            cx, cy = tan_xy(ra0, dec0, fra[c], fdec[c])
            ex, ey = f.at(t_fd[c])
            sep = np.hypot(cx - ex, cy - ey)
            r = match_radius(f.sigma_at(t_fd[c]), mover_sigma(fsnr[c], 1), f.allowance(t_fd[c]),
                             C.TRACK_RECOVER_R_MIN_ARCSEC, C.TRACK_RECOVER_R_MAX_ARCSEC)
            ok = sep <= r
            if ok.any():
                pr, pd_ = tan_radec(ra0, dec0, ex[ok], ey[ok])
                hits.append((tr["id"], tr["visit"], c[ok], sep[ok], r[ok], pr, pd_))
    if not hits:
        return empty
    rows = np.concatenate([h[2] for h in hits])
    out = fd.iloc[rows][keep_cols].reset_index(drop=True)
    out.insert(0, "tracklet_id", np.concatenate([np.full(h[2].size, h[0], np.int64) for h in hits]))
    out.insert(1, "visit", np.concatenate([np.full(h[2].size, h[1], np.int16) for h in hits]))
    out["sep_arcsec"] = np.concatenate([h[3] for h in hits]).astype(np.float32)
    out["r_arcsec"] = np.concatenate([h[4] for h in hits]).astype(np.float32)
    out["pred_ra"] = np.concatenate([h[5] for h in hits])
    out["pred_dec"] = np.concatenate([h[6] for h in hits])
    for c in DOT_COLUMNS:
        if c not in out:
            out[c] = np.nan
    return out[DOT_COLUMNS].sort_values(["tracklet_id", "mjd", "ra", "dec"], kind="stable").reset_index(drop=True)


# --- the step ---------------------------------------------------------------------------------------------------
def mover_ids(zone: C.Zone, movers: pd.DataFrame) -> np.ndarray:
    """The id suspects gives a mover: <zone>-<cell>-<round(mjd × 1440)>."""
    return np.array([f"{zone.name}-{int(c)}-{int(round(m * 1440))}" for c, m in zip(movers["cell"], movers["mjd"])], dtype=object)


def control_positions(movers: pd.DataFrame, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """The control's positions: each session's movers moved by its own random 0.5-1.5° offset in RA and Dec (random
    signs), so no real track survives and the density stays."""
    ra, dec = movers["ra"].to_numpy(np.float64), movers["dec"].to_numpy(np.float64)
    sess = movers["session"].to_numpy(np.int64)
    rng = np.random.default_rng(seed)
    u = np.unique(sess)
    off = rng.uniform(0.5, 1.5, (u.size, 2)) * rng.choice([-1.0, 1.0], (u.size, 2))
    o = off[np.searchsorted(u, sess)]
    dec = np.clip(dec + o[:, 1], -89.0, 89.0)
    return (ra + o[:, 0] / np.cos(np.deg2rad(dec))) % 360.0, dec


def link_all(movers: pd.DataFrame, t0: float, seed: int | None = None,
             accels: dict[int, Accel] | None = None) -> tuple[np.ndarray, dict, dict[int, Accel]]:
    """Track label per mover over all visits (zone-unique, -1 = none), counts, and each visit's acceleration model.
    Two passes per visit (Accel() → visit_accel) unless `accels` gives the model. seed: the control (control_positions)."""
    lab = np.full(len(movers), -1, np.int64)
    st: dict = {"seeds": 0, "seeds_with_third": 0, "tracks": 0, "visits": {}}
    acc_out: dict[int, Accel] = {}
    if not len(movers):
        return lab, st, acc_out
    t = (movers["mjd"].to_numpy(np.float64) - t0) * 24.0
    ra, dec = movers["ra"].to_numpy(np.float64), movers["dec"].to_numpy(np.float64)
    if seed is not None:
        ra, dec = control_positions(movers, seed)
    sess = movers["session"].to_numpy(np.int64)
    sig = mover_sigma(movers["snr"].to_numpy(np.float64), movers["n_dots"].to_numpy(np.float64))
    vis = movers["visit"].to_numpy(np.int64)
    base = 0
    for v in np.unique(vis):
        i = np.flatnonzero(vis == v)
        if accels is not None:
            acc, ast = accels.get(int(v), Accel()), {}
        else:
            lv, _ = link_visit(t[i], ra[i], dec[i], sig[i], sess[i], Accel())
            acc, ast = visit_accel(t[i], ra[i], dec[i], sig[i], lv)
        acc_out[int(v)] = acc
        lv, s = link_visit(t[i], ra[i], dec[i], sig[i], sess[i], acc)
        lab[i] = np.where(lv >= 0, lv + base, -1)
        base += s["tracks"]
        st["visits"][str(int(v))] = {**s, **ast}
        for k in ("seeds", "seeds_with_third", "tracks"):
            st[k] += s[k]
    return lab, st, acc_out


def build(movers: pd.DataFrame, photos: pd.DataFrame, zone: C.Zone, fd: pd.DataFrame | None,
          control: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """(tracklets table, recovered dots, stats) from the zone's movers, photo list and free dots."""
    if not len(movers):  # link writes a column-less table when a zone has no candidates
        movers = pd.DataFrame({c: pd.Series(dtype=t) for c, t in (("ra", "f8"), ("dec", "f8"), ("mjd", "f8"), ("session", "i4"),
                                                                     ("visit", "i2"), ("n_dots", "i4"), ("snr", "f8"), ("cell", "i8"))})
    movers = movers.sort_values(["visit", "session", "mjd", "ra", "dec"], kind="stable").reset_index(drop=True)
    t0 = float(np.floor(photos["mjd"].min())) if len(photos) else 0.0
    lab, st, accels = link_all(movers, t0)
    n = len(movers)
    out = pd.DataFrame({
        "mover_id": mover_ids(zone, movers) if n else np.zeros(0, dtype=object),
        "session": movers["session"].to_numpy(np.int32), "visit": movers["visit"].to_numpy(np.int16),
        "mjd": movers["mjd"].to_numpy(np.float64), "ra": movers["ra"].to_numpy(np.float64),
        "dec": movers["dec"].to_numpy(np.float64), "cell": movers["cell"].to_numpy(np.int64), "tracklet_id": lab})
    out["n_sessions"] = np.where(lab >= 0, 0, 1).astype(np.int16)
    out["n_movers"] = np.where(lab >= 0, 0, 1).astype(np.int16)
    out["n_recovered_sessions"] = np.zeros(n, np.int16)
    fcols = ("span_h", "rate_arcsec_h", "pa_deg", "rate_sigma", "rate_snr", "vra_arcsec_h", "vdec_arcsec_h",
             "fit_chi2_nu", "track_chi2_nu", "resid_arcsec")
    F = {c: np.full(n, np.nan) for c in fcols}
    deg = np.zeros(n, np.int8)
    t_h = (movers["mjd"].to_numpy(np.float64) - t0) * 24.0
    sig = mover_sigma(movers["snr"].to_numpy(np.float64), movers["n_dots"].to_numpy(np.float64)) if n else np.zeros(0)
    ra, dec, sess, vis = out["ra"].to_numpy(), out["dec"].to_numpy(), out["session"].to_numpy(), out["visit"].to_numpy()
    nsess, nmov = out["n_sessions"].to_numpy().copy(), out["n_movers"].to_numpy().copy()
    tracks = []
    on = np.flatnonzero(lab >= 0)
    for idx in (np.split(on[np.argsort(lab[on], kind="stable")], np.flatnonzero(np.diff(np.sort(lab[on]))) + 1) if on.size else []):
        idx = idx[np.argsort(t_h[idx], kind="stable")]
        acc = accels.get(int(vis[idx[0]]), Accel())
        m = track_motion(t_h[idx], ra[idx], dec[idx], sig[idx], acc)
        rate = np.hypot(m["vx"], m["vy"])
        nsess[idx], nmov[idx] = np.unique(sess[idx]).size, idx.size
        F["span_h"][idx] = t_h[idx].max() - t_h[idx].min()
        F["rate_arcsec_h"][idx] = rate
        F["pa_deg"][idx] = np.rad2deg(np.arctan2(m["vx"], m["vy"])) % 360.0
        F["rate_sigma"][idx] = m["rate_sigma"]
        F["rate_snr"][idx] = rate / m["rate_sigma"]
        F["vra_arcsec_h"][idx], F["vdec_arcsec_h"][idx] = m["vx"], m["vy"]
        F["fit_chi2_nu"][idx], F["resid_arcsec"][idx] = m["fit_chi2_nu"], m["resid"]
        F["track_chi2_nu"][idx] = m["track_chi2_nu"]
        deg[idx] = m["deg"]
        tracks.append({"id": int(lab[idx[0]]), "visit": int(vis[idx[0]]), "t": t_h[idx], "sig": sig[idx],
                       "ra": ra[idx], "dec": dec[idx], "sess": sess[idx], "acc": acc})
    out["n_sessions"], out["n_movers"] = nsess.astype(np.int16), nmov.astype(np.int16)
    for c in fcols:
        out[c] = F[c].astype(np.float32)
    out["fit_degree"] = deg
    # recovery
    rec = pd.DataFrame({c: pd.Series(dtype="float64") for c in DOT_COLUMNS})
    if C.TRACK_RECOVER and fd is not None and tracks:
        g = photos.groupby("session")
        sessions = pd.DataFrame({"visit": g["visit"].first(), "t": (g["mjd"].mean() - t0) * 24.0,
                                 "half": (g["mjd"].max() - g["mjd"].min()) * 12.0}).reset_index()
        rec = recover(tracks, fd.assign(_t=(fd["mjd"].to_numpy(np.float64) - t0) * 24.0), sessions)
        if len(rec):
            nrs = rec.groupby("tracklet_id")["session"].nunique()
            out["n_recovered_sessions"] = np.where(lab >= 0, nrs.reindex(lab).fillna(0).to_numpy(), 0).astype(np.int16)
    per = out[lab >= 0].groupby("tracklet_id")["n_sessions"].first()
    st.update({"movers": int(n), "movers_on_tracklets": int((lab >= 0).sum()),
               "tracklets_by_sessions": {str(k): int(v) for k, v in per.value_counts().sort_index().items()},
               "free_dots": None if fd is None else int(len(fd)), "recovered_dots": int(len(rec)),
               "recovered_sessions": int(rec.groupby(["tracklet_id", "session"]).ngroups) if len(rec) else 0,
               "tracklets_with_recovery": int(rec["tracklet_id"].nunique()) if len(rec) else 0})
    if control and n:
        cl, cs, _ = link_all(movers, t0, seed=C.CONTROL_SEED, accels=accels)
        ns = pd.Series(sess[cl >= 0]).groupby(cl[cl >= 0]).nunique()
        st["control"] = {"tracks": int(cs["tracks"]), "movers_on_tracks": int((cl >= 0).sum()),
                         "by_sessions": {str(k): int(v) for k, v in ns.value_counts().sort_index().items()}}
    return out[TRACK_COLUMNS], rec, st


def run(store: Store, zone: C.Zone) -> dict:
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name), columns=["obs_id", "detector", "mjd", "session", "visit"])
    movers = store.read_parquet(results_rel(zone.name, "movers.parquet"))
    fd = load_free_dots(store, zone) if C.TRACK_RECOVER else None
    if C.TRACK_RECOVER and fd is None:
        L.warning("tracklets %s: no %s (link from before tracklets): tracks only, no recovered dots", zone.name, FREE_DOTS)
    tr, rec, st = build(movers, photos, zone, fd)
    store.write_parquet(results_rel(zone.name, "tracklets.parquet"), tr)
    store.write_parquet(results_rel(zone.name, "tracklet_dots.parquet"), rec)
    st["wall_s"] = round(time.time() - t0, 1)
    L.info("tracklets %s: %s", zone.name, st)
    return st
