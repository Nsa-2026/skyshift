"""Find things that move, and say how likely each one is to be a fluke.

Pipeline
--------
1. Detections: positive peaks in the matched-filter S/N map of each residual frame
   (frame minus the spectral static-sky model).
2. Dual-focal-plane coincidence: SPHEREx images the same sky simultaneously through
   detector pairs (D1,D4), (D2,D5), (D3,D6). A real astrophysical source (moving or not)
   must leave signal at the same position in the partner exposure, while cosmic rays,
   snowballs, hot pixels and crosstalk do not. Detections are tagged with the partner's
   forced-photometry S/N.
3. Linking: every pair of detections at different times defines a linear motion. A
   hypothesis is scored by the summed S/N of all detections (other times) that lie on the
   same track within a tolerance. Tracks with >= min_points distinct epochs are kept.
4. Time-slide background (borrowed from gravitational-wave searches): the same linker is
   run on detection sets whose frames have been individually rotated by random angles
   about the field centre. Real motion is destroyed, but the detection statistics
   (number, S/N distribution, artefact rate) are preserved. The distribution of the best
   score per slide gives a false-alarm probability for every real candidate.
"""
import numpy as np
PARTNER = {1:4,2:5,3:6,4:1,5:2,6:3}
from .detect import snr_map, find_peaks, forced_flux

PARTNER = {1: 4, 2: 5, 3: 6, 4: 1, 5: 2, 6: 3}


def coincidence_filter(dets, min_partner_snr=2.0, keep_unpaired=True):
    out = []
    for d in dets:
        ps = d.get("partner_snr")
        if ps is None:
            if keep_unpaired:
                out.append(d)
        elif ps >= min_partner_snr:
            out.append(d)
    return out


def link(dets, times, vmax_pix_per_day=5.0, tol=1.2, min_points=3, min_dt=0.02, max_tracks=50, min_disp=2.0, max_dt=12.0):
    """Hough-like linking of detections on straight tracks.

    times: (T,) MJD of each frame. Returns tracks sorted by score.
    """
    if len(dets) < 2:
        return []
    t = np.array([times[d["t"]] for d in dets])
    x = np.array([d["x"] for d in dets]); y = np.array([d["y"] for d in dets])
    s = np.array([d["snr"] for d in dets]); fi = np.array([d["t"] for d in dets])
    order = np.argsort(-s)[:300]  # seed with the brightest detections
    tracks, seen = [], set()
    order = order[:150]
    for a_i, a in enumerate(order):
        rest = order[a_i + 1:]
        dts = t[rest] - t[a]
        dd = np.hypot(x[rest] - x[a], y[rest] - y[a])
        okb = (np.abs(dts) >= min_dt) & (np.abs(dts) <= max_dt) & (dd >= 1.0) & (dd <= vmax_pix_per_day * np.abs(dts))
        for b in rest[okb]:
            dt = t[b] - t[a]
            vx, vy = (x[b] - x[a]) / dt, (y[b] - y[a]) / dt
            v = np.hypot(vx, vy)
            if v > vmax_pix_per_day or v * abs(dt) < 1.0:   # must move > 1 px between the pair
                continue
            px = x[a] + vx * (t - t[a]); py = y[a] + vy * (t - t[a])
            on = (np.hypot(x - px, y - py) < tol) & (np.abs(t - t[a]) <= max_dt)
            # one detection per frame
            best = {}
            for i in np.nonzero(on)[0]:
                if fi[i] not in best or s[i] > s[best[fi[i]]]:
                    best[fi[i]] = i
            idx = np.array(sorted(best.values()))
            # distinct epochs (times separated by > min_dt)
            tt = np.sort(t[idx])
            n_ep = 1 + int((np.diff(tt) > min_dt).sum()) if len(tt) else 0
            if n_ep < min_points:
                continue
            sig = frozenset(idx.tolist())
            if sig in seen:
                continue
            seen.add(sig)
            # refit the line
            A = np.c_[np.ones(len(idx)), t[idx] - t[idx].mean()]
            W = s[idx]
            cx = np.linalg.lstsq(A * W[:, None] ** .5, x[idx] * W ** .5, rcond=None)[0]
            cy = np.linalg.lstsq(A * W[:, None] ** .5, y[idx] * W ** .5, rcond=None)[0]
            rms = float(np.sqrt(np.mean((x[idx] - (cx[0] + cx[1] * (t[idx] - t[idx].mean()))) ** 2 +
                                        (y[idx] - (cy[0] + cy[1] * (t[idx] - t[idx].mean()))) ** 2)))
            tt_all = t[idx] - t[idx].mean()
            sv = max(rms, 0.3) / np.sqrt(max((tt_all ** 2).sum(), 1e-9))
            v_fit = np.hypot(cx[1], cy[1])
            disp = v_fit * (t[idx].max() - t[idx].min())
            if disp < min_disp or v_fit < 5 * sv:      # static source / spectral-model residual
                continue
            tracks.append(dict(sig_v=float(v_fit / sv), disp_pix=float(disp), score=float(s[idx].sum()), n=len(idx), n_epochs=n_ep, members=idx.tolist(),
                               vx=float(cx[1]), vy=float(cy[1]), x0=float(cx[0]), y0=float(cy[0]),
                               t0=float(t[idx].mean()), rms=rms))
    tracks.sort(key=lambda r: -r["score"])
    # greedy de-duplication: drop tracks sharing most members with a better one
    final, used = [], set()
    for tr in tracks:
        m = set(tr["members"])
        if len(m & used) > 0.5 * len(m):
            continue
        final.append(tr); used |= m
        if len(final) >= max_tracks:
            break
    return final


def time_slides(dets, times, shape, n_slides=200, seed=0, **link_kw):
    """Background distribution of the best linking score under random per-frame rotations."""
    rng = np.random.default_rng(seed)
    cy, cx = (shape[0] - 1) / 2, (shape[1] - 1) / 2
    frames = sorted(set(d["t"] for d in dets))
    best = []
    for _ in range(n_slides):
        ang = {f: rng.uniform(0, 2 * np.pi) for f in frames}
        c = {f: np.cos(a) for f, a in ang.items()}; s = {f: np.sin(a) for f, a in ang.items()}
        sl = []
        for d in dets:
            dx, dy = d["x"] - cx, d["y"] - cy
            f = d["t"]
            nx, ny = cx + c[f] * dx - s[f] * dy, cy + s[f] * dx + c[f] * dy
            if 0 <= nx < shape[1] and 0 <= ny < shape[0]:
                sl.append(dict(d, x=nx, y=ny))
        tr = link(sl, times, **link_kw)
        best.append(tr[0]["score"] if tr else 0.0)
    return np.array(best)


# Cold-source screening lives in skyshift_core.candidates, not this legacy module.


# ---------------------------------------------------------------- slow movers (across visits)
def epoch_catalogs(img, var, epoch, fwhms, weights=None, snr_min=8):
    """PSF-fitted source list in the inverse-variance stack of each survey visit."""
    from .detect import psf_fit_position
    cats, stacks = [], []
    w = np.ones(len(img)) if weights is None else np.asarray(weights)
    for k in sorted(set(epoch.tolist())):
        sel = epoch == k
        ok = np.isfinite(img[sel]) & np.isfinite(var[sel]) & (var[sel] > 0)
        iv = np.where(ok, w[sel][:, None, None] / np.where(ok, var[sel], 1), 0)
        st = (np.where(ok, img[sel], 0) * iv).sum(0) / np.maximum(iv.sum(0), 1e-30)
        sv = 1 / np.maximum(iv.sum(0), 1e-30)
        st[iv.sum(0) == 0] = np.nan
        fw = float(np.median(fwhms[sel]))
        s, _, _ = snr_map(st, sv, fw)
        cat = []
        for p in find_peaks(s, snr_min, border=3):
            f = psf_fit_position(st, sv, p["x"], p["y"], fw, box=4)
            if f and f["ex"] < 0.5 and f["ey"] < 0.5 and np.hypot(f["x"] - p["x"], f["y"] - p["y"]) < 2:
                cat.append(dict(x=f["x"], y=f["y"], ex=max(f["ex"], 0.05), ey=max(f["ey"], 0.05), snr=p["snr"], amp=f["amp"]))
        cats.append(cat)
        stacks.append((st, sv))
    return cats, stacks


def slow_movers(cats, t_ep, r_max=8.0, sys_floor=0.08, min_sig=5.0, min_pm_pix_yr=0.4, plx_fac=None, max_plx_pix=1.5):
    """Link the same source across >=3 visits with a straight line; report significant motion.

    For each source in the first visit with any counterpart, try every combination of the
    nearest candidates (within r_max) in the other visits, keep the best chi2 straight line.
    """
    E = len(cats)
    if E < 2:
        return []
    t = np.asarray(t_ep, float)
    out = []
    used = [set() for _ in range(E)]
    # anchor on each source of each visit
    for a in range(E):
        for i, s in enumerate(cats[a]):
            if i in used[a]:
                continue
            # candidates per other visit
            cand = []
            for b in range(E):
                if b == a:
                    cand.append([(i, s)])
                    continue
                c = [(j, q) for j, q in enumerate(cats[b]) if j not in used[b] and np.hypot(q["x"] - s["x"], q["y"] - s["y"]) < r_max * max(1, abs(t[b] - t[a]) / 365.25 * 2)]
                cand.append(sorted(c, key=lambda jq: np.hypot(jq[1]["x"] - s["x"], jq[1]["y"] - s["y"]))[:3] or [None])
            best = None
            import itertools
            for combo in itertools.product(*cand):
                pts = [(e, jq) for e, jq in enumerate(combo) if jq is not None]
                if len(pts) < min(3, E):
                    continue
                tt = np.array([t[e] for e, _ in pts]) / 365.25
                X = np.array([jq[1]["x"] for _, jq in pts]); Y = np.array([jq[1]["y"] for _, jq in pts])
                SX = np.hypot([jq[1]["ex"] for _, jq in pts], sys_floor); SY = np.hypot([jq[1]["ey"] for _, jq in pts], sys_floor)
                ee = [e for e, _ in pts]
                if plx_fac is not None and len(pts) >= 3:
                    # joint fit x0, y0, mux, muy, plx (pixels): parallax shifts both axes
                    n = len(pts)
                    A = np.zeros((2 * n, 5))
                    A[:n, 0] = 1; A[:n, 2] = tt - tt.mean(); A[:n, 4] = plx_fac[0][ee]
                    A[n:, 1] = 1; A[n:, 3] = tt - tt.mean(); A[n:, 4] = plx_fac[1][ee]
                    b = np.r_[X, Y]; sb = np.r_[SX, SY]
                    p5, c5 = _wls(A, b, sb)
                    if abs(p5[4]) > max_plx_pix:
                        continue
                    chi2 = float((((b - A @ p5) / sb) ** 2).sum())
                    dof = max(1, 2 * n - 5)
                    px, py = np.array([p5[0], p5[2]]), np.array([p5[1], p5[3]])
                    cxx = np.array([[c5[0, 0], 0], [0, c5[2, 2]]]); cyy = np.array([[c5[1, 1], 0], [0, c5[3, 3]]])
                    plx = float(p5[4]); eplx = float(np.sqrt(c5[4, 4]))
                else:
                    A = np.c_[np.ones_like(tt), tt - tt.mean()]
                    px, cxx = _wls(A, X, SX); py, cyy = _wls(A, Y, SY)
                    chi2 = float((((X - A @ px) / SX) ** 2).sum() + (((Y - A @ py) / SY) ** 2).sum())
                    dof = max(1, 2 * len(pts) - 4)
                    plx = eplx = None
                if best is None or chi2 / dof < best[0]:
                    best = (chi2 / dof, pts, px, py, cxx, cyy, plx, eplx)
            if best is None:
                continue
            chi2r, pts, px, py, cxx, cyy, plx, eplx = best
            pm = np.hypot(px[1], py[1])
            epm = np.sqrt((px[1] ** 2 * cxx[1, 1] + py[1] ** 2 * cyy[1, 1]) / max(pm ** 2, 1e-12))
            sig = pm / max(epm, 1e-9)
            if chi2r < 5 and sig >= min_sig and pm >= min_pm_pix_yr:
                for e, (j, _) in pts:
                    used[e].add(j)
                out.append(dict(x=float(px[0]), y=float(py[0]), pm_x=float(px[1]), pm_y=float(py[1]), pm_pix_yr=float(pm),
                                pm_sig=float(sig), chi2r=chi2r, n_visits=len(pts), plx_arcsec=plx, eplx_arcsec=eplx,
                                snr=float(np.mean([jq[1]["snr"] for _, jq in pts])),
                                points=[dict(visit=e, x=jq[1]["x"], y=jq[1]["y"]) for e, jq in pts]))
    return sorted(out, key=lambda r: -r["pm_sig"])


def _wls(A, y, s):
    Aw, yw = A / s[:, None], y / s
    p, *_ = np.linalg.lstsq(Aw, yw, rcond=None)
    cov = np.linalg.pinv(Aw.T @ Aw)
    return p, cov


def slow_mover_background(cats, t_ep, shape, n_slides=200, seed=1, **kw):
    """Time-slide background for slow movers: shift each later visit's catalogue by an
    independent random toroidal offset; count how many 'significant movers' appear by chance."""
    rng = np.random.default_rng(seed)
    cy, cx = (shape[0] - 1) / 2, (shape[1] - 1) / 2
    counts, best = [], []
    for _ in range(n_slides):
        sl = []
        for k, cat in enumerate(cats):
            if k == 0:
                sl.append(cat); continue
            # toroidal shift by >= 5 px: destroys real matches, keeps source density & errors
            while True:
                dx, dy = rng.uniform(0, shape[1]), rng.uniform(0, shape[0])
                if min(dx, shape[1] - dx) > 5 or min(dy, shape[0] - dy) > 5:
                    break
            sl.append([dict(q, x=(q["x"] + dx) % shape[1], y=(q["y"] + dy) % shape[0]) for q in cat])
        m = slow_movers(sl, t_ep, **kw)
        counts.append(len(m)); best.append(max([r["pm_sig"] for r in m], default=0))
    return np.array(counts), np.array(best)




def bright_star_mask(model, var, img, sat_frac=0.2, snr_cut=200, grow=3):
    """Pixels near very bright or saturated static sources, where residuals are dominated by
    PSF-model mismatch rather than by real changes."""
    from scipy.ndimage import binary_dilation
    med_model = np.nanmedian(model, 0)
    med_sig = np.sqrt(np.nanmedian(var, 0))
    masked = np.mean(~np.isfinite(img), 0)
    m = (masked > sat_frac) | (med_model / med_sig > snr_cut)
    return binary_dilation(m, iterations=grow)


def detect_all(resid, var, fwhms, thresh=4.5, model=None, img=None, min_ratio=0.3):
    """Positive residual peaks. A peak must carry a substantial fraction of the local flux
    (resid / static model >= min_ratio) and lie away from bright/saturated stars."""
    excl = bright_star_mask(model, var, img) if model is not None else None
    dets = []
    for t in range(resid.shape[0]):
        s, f, e = snr_map(resid[t], var[t], fwhms[t])
        for p in find_peaks(s, thresh):
            xi, yi = int(round(p["x"])), int(round(p["y"]))
            if excl is not None and excl[yi, xi]:
                continue
            if model is not None:
                mv = model[t, yi, xi]
                rv = resid[t, yi, xi]
                if np.isfinite(mv) and mv > 0 and rv / (mv + 3 * np.sqrt(var[t, yi, xi])) < min_ratio:
                    continue
            p["t"] = t
            dets.append(p)
    return dets


def tag_partner(dets, resid, var, meta, fwhms):
    """Forced S/N at the same position in the simultaneous partner-detector frame."""
    key = {(m.get("obs"), m.get("expo"), m["det"], m.get("ver")): i for i, m in enumerate(meta)}
    for d in dets:
        m = meta[d["t"]]
        j = key.get((m.get("obs"), m.get("expo"), PARTNER[m["det"]], m.get("ver")))
        if j is None:
            d["partner_snr"] = None
            continue
        f, e = forced_flux(resid[j], var[j], d["x"], d["y"], fwhms[j])
        d["partner_snr"] = None if not np.isfinite(f) else f / e
        d["partner_t"] = j
    return dets
