"""Asteroid diameter, albedo and beaming from SPHEREx 0.75-5.0 um spectra (NEATM + reflected light).

    uv run python asteroids.py fetch      # cutouts for the chosen asteroids (cached)
    uv run python asteroids.py measure    # photometry -> .cache/asteroid_points.json
    uv run python asteroids.py fit        # Horizons + SBDB + fits -> data/asteroid_sizes.json
    uv run python asteroids.py all

Input: IRSA MOST table for asteroids 1-60 (gs://nasa-glazing-data/results/most_1_60.csv, copied to
.cache/most_1_60.csv): every SPHEREx image of each asteroid and its predicted position.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

import cutouts as C
import models as M
from phot import measure

HERE = Path(__file__).resolve().parent
MOST_CSV = C.CACHE / "most_1_60.csv"
POINTS_JSON = C.CACHE / "asteroid_points.json"
GEOM_JSON = C.CACHE / "asteroid_horizons.json"
OUT_JSON = HERE / "data" / "asteroid_sizes.json"
SIZE_DEG = 0.04  # 2.4' = 23 px: aperture 2 px, background annulus 4-9 px

# Ceres (saturates in the short bands: those points are dropped by the flags), and 11 asteroids of
# V >= 11.4 with 250-460 images, mixed taxonomy (C, S, M) and published IRAS/NEOWISE/AKARI sizes.
CHOSEN = [1, 5, 10, 12, 16, 21, 22, 24, 41, 51, 52, 59]

FIT_WINDOW = (2.2, 5.05)        # um: reflected continuum + thermal tail
HYDRATION = (2.65, 3.45)        # excluded from the continuum fit (OH/H2O 2.7-3.1 um, organics 3.4)
BAND_3UM = (2.9, 3.2)           # Ceres' 3.06 um ammoniated-phyllosilicate band, Themis/Hygiea's 3.1
ERR_FLOOR = 0.05                # fractional, added in quadrature: rotation (lightcurve) + calibration
ETA_FIXED = 1.0


def load_most() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    with open(MOST_CSV) as fh:
        for r in csv.DictReader(fh):
            n = int(r["number"])
            if n in CHOSEN:
                out[n].append(r)
    return out


def job(r: dict) -> tuple[str, Path]:
    u = C.cutout_url(r["access_url"], float(r["ra_obj"]), float(r["dec_obj"]), SIZE_DEG)
    return u, C._cache_path(u, tag=f"a{int(r['number']):03d}_")


def cmd_fetch() -> None:
    most = load_most()
    jobs = [job(r) for n in CHOSEN for r in most[n]]
    print(f"{len(jobs)} cutouts for {len(CHOSEN)} asteroids")
    res = C.fetch_many(jobs)
    bad = [u for u, v in res.items() if isinstance(v, Exception)]
    print(f"done: {len(res) - len(bad)} ok, {len(bad)} failed")
    for u in bad[:10]:
        print("  failed:", u, res[u])


def cmd_measure() -> None:
    most = load_most()
    out = {}
    for n in CHOSEN:
        pts, why = [], defaultdict(int)
        for r in most[n]:
            u, p = job(r)
            if not p.exists():
                why["not fetched"] += 1
                continue
            try:
                l2 = C.open_cutout(p)
            except Exception as e:  # noqa: BLE001
                why[f"open: {type(e).__name__}"] += 1
                continue
            pt = measure(l2, float(r["ra_obj"]), float(r["dec_obj"]), radii=(2.0, 3.0))
            if not pt.ok:
                why[pt.why.split(" ")[0] if pt.why.startswith("centroid") else pt.why] += 1
                continue
            pts.append({
                "um": round(pt.um, 4), "bw": round(pt.bw_um, 4),
                "flux": round(pt.flux[2.0], 2), "err": round(pt.err[2.0], 2),
                "flux_r3": round(pt.flux[3.0], 2),
                "mjd": round(l2.mjd_avg, 6), "det": l2.detector, "obsid": l2.obsid,
                "off_px": round(pt.offset_px, 2), "rec_flags": pt.record_flags,
                "most_r": float(r["sun_dist"]), "most_delta": float(r["geo_dist"]),
                "most_alpha": float(r["phase"]), "vmag": float(r["vmag"]),
            })
        out[n] = {"name": most[n][0]["name"], "images": len(most[n]), "points": pts, "dropped": dict(why)}
        print(f"{most[n][0]['name']}: {len(pts)}/{len(most[n])} points; dropped {dict(why)}", flush=True)
    POINTS_JSON.write_text(json.dumps(out))


# --- Horizons / SBDB --------------------------------------------------------------------------

def horizons_geometry(n: int, mjds: np.ndarray) -> dict:
    """r, delta, alpha (geocentre, location 500) interpolated from a 0.5-day ephemeris, plus H, G."""
    from astroquery.jplhorizons import Horizons

    lo, hi = float(mjds.min()) - 1, float(mjds.max()) + 1
    from astropy.time import Time

    t0, t1 = Time(lo, format="mjd").iso[:16], Time(hi, format="mjd").iso[:16]
    for attempt in range(3):
        try:
            e = Horizons(id=str(n), id_type="smallbody", location="500",
                         epochs={"start": t0, "stop": t1, "step": "12h"}).ephemerides(quantities="19,20,24")
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(5)
    jd = np.asarray(e["datetime_jd"], dtype=float) - 2400000.5
    return {
        "H": float(e["H"][0]), "G": float(e["G"][0]) if np.isfinite(float(e["G"][0])) else 0.15,
        "r": np.interp(mjds, jd, np.asarray(e["r"], float)).tolist(),
        "delta": np.interp(mjds, jd, np.asarray(e["delta"], float)).tolist(),
        "alpha": np.interp(mjds, jd, np.asarray(e["alpha"], float)).tolist(),
    }


def sbdb_physical(n: int) -> dict:
    from astroquery.jplsbdb import SBDB

    s = SBDB.query(str(n), phys=True)
    p = s.get("phys_par", {}) or {}

    def num(v):
        if v is None:
            return None
        try:
            return float(getattr(v, "value", v))
        except (TypeError, ValueError):
            return None

    return {
        "D_km": num(p.get("diameter")), "D_err_km": num(p.get("diameter_sig")), "D_ref": p.get("diameter_ref"),
        "pv": num(p.get("albedo")), "pv_err": num(p.get("albedo_sig")), "pv_ref": p.get("albedo_ref"),
        "H_sbdb": num(p.get("H")), "spec_T": p.get("spec_T"), "spec_B": p.get("spec_B"),
        "rot_per_h": num(p.get("rot_per")),
    }


# --- fit --------------------------------------------------------------------------------------

def model_parts(theta: np.ndarray, um: np.ndarray, r: np.ndarray, dl: np.ndarray, al: np.ndarray,
                H: float, G: float, eta_fixed: float | None) -> tuple[np.ndarray, np.ndarray, float, float]:
    """(reflected, thermal, D, eta). theta = [a, b, lnD] (+ eta if free).

    Reflected: geometric albedo at lambda p(lambda) = pv * R(lambda), R linear in lambda
    (R(3 um) = a, slope b per um). pv * D^2 is fixed by H, so the reflected light does not depend on D.
    """
    a, b, lnD = theta[:3]
    eta = theta[3] if eta_fixed is None else eta_fixed
    D = float(np.exp(lnD))
    pv = float(M.pv_from_d(H, D))
    D_h1 = float(M.diameter_from_h(H, 1.0))  # D for pv = 1: p(lambda) D^2 = R(lambda) D_h1^2
    R = a + b * (um - 3.0)
    refl = M.reflected_ujy(um, D_h1, R, r, dl, al, G)
    th = M.neatm_ujy(um, D, pv, eta, r, dl, al, G)
    return refl, th, D, eta


def fit_one(pts: list[dict], geom: dict, eta_fixed: float | None) -> dict:
    um = np.array([p["um"] for p in pts])
    f = np.array([p["flux"] for p in pts])
    e = np.array([p["err"] for p in pts])
    r, dl, al = (np.asarray(geom[k]) for k in ("r", "delta", "alpha"))
    H, G = geom["H"], geom["G"]
    sel = (um >= FIT_WINDOW[0]) & (um <= FIT_WINDOW[1]) & ~((um > HYDRATION[0]) & (um < HYDRATION[1]))
    sel &= (f > 0) & (e > 0)
    et = np.hypot(e, ERR_FLOOR * np.abs(f))
    # start: D from H with pv = 0.1
    th0 = [0.12, 0.0, np.log(float(M.diameter_from_h(H, 0.1)))] + ([1.0] if eta_fixed is None else [])
    lo = [0.0, -1.0, np.log(1.0)] + ([0.4] if eta_fixed is None else [])
    hi = [2.0, 1.0, np.log(2000.0)] + ([3.5] if eta_fixed is None else [])
    use = sel.copy()
    res = None
    for _ in range(5):
        def resid(t):
            rf, tm, *_ = model_parts(t, um[use], r[use], dl[use], al[use], H, G, eta_fixed)
            return (f[use] - rf - tm) / et[use]

        res = least_squares(resid, th0, bounds=(lo, hi), x_scale="jac")
        th0 = res.x
        rf, tm, *_ = model_parts(res.x, um, r, dl, al, H, G, eta_fixed)
        z = (f - rf - tm) / et
        mad = 1.4826 * np.median(np.abs(z[use] - np.median(z[use])))
        new = sel & (np.abs(z) < max(4.0 * mad, 4.0))
        if np.array_equal(new, use):
            break
        use = new
    J = res.jac
    dof = max(int(use.sum()) - len(res.x), 1)
    chi2r = float(np.sum(res.fun**2) / dof)
    try:
        cov = np.linalg.inv(J.T @ J) * max(chi2r, 1.0)
        sig = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        sig = np.full(len(res.x), np.nan)
    rf, tm, D, eta = model_parts(res.x, um, r, dl, al, H, G, eta_fixed)
    pv = float(M.pv_from_d(H, D))
    out = {
        "D_km": D, "D_err_km": float(D * sig[2]), "pv": pv, "pv_err": float(2 * pv * sig[2]),
        "eta": float(eta), "eta_err": float(sig[3]) if eta_fixed is None else None, "eta_fixed": eta_fixed is not None,
        "R3um": float(res.x[0]), "R_slope_per_um": float(res.x[1]),
        "n_fit": int(use.sum()), "n_clipped": int(sel.sum() - use.sum()), "chi2_red": chi2r,
        "thermal_frac_4p7um": None,
    }
    near = (um > 4.5) & use
    if near.any():
        out["thermal_frac_4p7um"] = float(np.median(tm[near] / (rf[near] + tm[near])))
    # 3 um band: observed / continuum model (reflected + thermal) in BAND_3UM
    inb = (um >= BAND_3UM[0]) & (um <= BAND_3UM[1]) & (f > 0)
    if inb.sum() >= 3:
        ratio = f[inb] / (rf[inb] + tm[inb])
        w = 1 / (et[inb] / (rf[inb] + tm[inb])) ** 2
        mean = float(np.sum(ratio * w) / np.sum(w))
        scat = float(np.sqrt(np.sum(w * (ratio - mean) ** 2) / max(inb.sum() - 1, 1) / np.sum(w)))
        err = max(scat, float(np.sqrt(1 / np.sum(w))))
        out["band_3um"] = {"um": list(BAND_3UM), "n": int(inb.sum()), "depth": 1 - mean, "depth_err": err,
                           "snr": (1 - mean) / err}
    else:
        out["band_3um"] = None
    out["_model"] = (rf, tm, use)
    return out


def cmd_fit() -> None:
    data = json.loads(POINTS_JSON.read_text())
    geo_cache = json.loads(GEOM_JSON.read_text()) if GEOM_JSON.exists() else {}
    results = []
    for n in CHOSEN:
        d = data[str(n)]
        pts = d["points"]
        if len(pts) < 20:
            print(f"{d['name']}: only {len(pts)} points, skipped")
            continue
        mjds = np.array([p["mjd"] for p in pts])
        key = str(n)
        if key not in geo_cache or len(geo_cache[key]["r"]) != len(pts):
            g = horizons_geometry(n, mjds)
            g["sbdb"] = sbdb_physical(n)
            geo_cache[key] = g
            GEOM_JSON.write_text(json.dumps(geo_cache))
        g = geo_cache[key]
        free = fit_one(pts, g, None)
        fixed = fit_one(pts, g, ETA_FIXED)
        eta_ok = (free["eta_err"] is not None and np.isfinite(free["eta_err"]) and free["eta_err"] < 0.3
                  and 0.5 < free["eta"] < 2.5)
        best = free if eta_ok else fixed
        rf, tm, use = best.pop("_model")
        for x in (free, fixed):
            x.pop("_model", None)
        pub = g["sbdb"]
        # geometry check against MOST's own r, delta, phase
        dr = float(np.max(np.abs(np.array(g["r"]) - np.array([p["most_r"] for p in pts]))))
        dd = float(np.max(np.abs(np.array(g["delta"]) - np.array([p["most_delta"] for p in pts]))))
        da = float(np.max(np.abs(np.array(g["alpha"]) - np.array([p["most_alpha"] for p in pts]))))
        dets = sorted({p["det"] for p in pts})
        res = {
            "number": n, "name": d["name"], "H": g["H"], "G": g["G"],
            "images": d["images"], "n_points": len(pts), "dropped": d["dropped"], "detectors": dets,
            "r_au": [round(min(g["r"]), 3), round(max(g["r"]), 3)],
            "delta_au": [round(min(g["delta"]), 3), round(max(g["delta"]), 3)],
            "alpha_deg": [round(min(g["alpha"]), 2), round(max(g["alpha"]), 2)],
            "geometry_vs_most_max_diff": {"r_au": round(dr, 4), "delta_au": round(dd, 4), "alpha_deg": round(da, 3)},
            "fit": "eta_free" if eta_ok else "eta_fixed_1.0",
            "D_km": best["D_km"], "D_err_km": best["D_err_km"], "pv": best["pv"], "pv_err": best["pv_err"],
            "eta": best["eta"], "eta_err": best["eta_err"],
            "fit_eta_free": free, "fit_eta_fixed": fixed,
            "published": pub,
            "ratio_D": best["D_km"] / pub["D_km"] if pub.get("D_km") else None,
            "ratio_pv": best["pv"] / pub["pv"] if pub.get("pv") else None,
            "points": [
                {"um": p["um"], "flux": p["flux"], "err": p["err"], "mjd": p["mjd"], "det": p["det"],
                 "used": bool(u), "model": round(float(a + b), 2), "thermal": round(float(b), 2)}
                for p, u, a, b in zip(pts, use, rf, tm)
            ],
        }
        results.append(res)
        print(f"{d['name']:26s} D={best['D_km']:7.1f}±{best['D_err_km']:5.1f} km (pub {pub.get('D_km')}) "
              f"pv={best['pv']:.3f}±{best['pv_err']:.3f} (pub {pub.get('pv')}) eta={best['eta']:.2f} "
              f"[{res['fit']}; free eta={free['eta']:.2f}±{free['eta_err']:.2f}] n={best['n_fit']} "
              f"chi2r={best['chi2_red']:.1f} 3um={best['band_3um'] and round(best['band_3um']['depth'], 3)}", flush=True)
    rD = np.array([x["ratio_D"] for x in results if x["ratio_D"]])
    rp = np.array([x["ratio_pv"] for x in results if x["ratio_pv"]])

    def stats(a: np.ndarray) -> dict:
        if not len(a):
            return {}
        q16, q50, q84 = np.percentile(a, [16, 50, 84])
        return {"n": int(len(a)), "median": float(q50), "p16": float(q16), "p84": float(q84),
                "scatter_mad": float(1.4826 * np.median(np.abs(a - q50))),
                "rms_log10": float(np.sqrt(np.mean(np.log10(a) ** 2)))}

    summary = {"ratio_D": stats(rD), "ratio_pv": stats(rp)}
    print("D ours/published:", summary["ratio_D"])
    print("pv ours/published:", summary["ratio_pv"])
    out = {
        "generated": time.strftime("%Y-%m-%d"),
        "method": {
            "data": "IRSA cutouts (2.4', 23 px) of every SPHEREx DR1/QR3 image listed by IRSA MOST, centred on "
                    "MOST's predicted position; centroid within 1.5 px; 2 px aperture with the core's 1.033 "
                    "aperture correction; sigma-clipped median background in a 4-9 px annulus; points with a "
                    "mask bit (incl. OVERFLOW/NONLINEAR = saturation) within 2 px are dropped.",
            "wavelength": "WCS-WAVE at the asteroid's pixel (um)",
            "geometry": "JPL Horizons, geocentre (location 500): SPHEREx is in low Earth orbit, < 1e-4 au away, "
                        "negligible for main-belt asteroids. Interpolated from a 12 h ephemeris.",
            "model": "flux = reflected + NEATM thermal. Reflected: CALSPEC solar model x H-G phase function x "
                     "p(lambda) D^2 with pv D^2 fixed by H (D = 1329/sqrt(pv) 10^-H/5) and p(lambda)/pv linear in "
                     "lambda. NEATM: emissivity 0.9, Bond albedo q pv with q = 0.290 + 0.684 G. Fit over "
                     f"{FIT_WINDOW[0]}-{FIT_WINDOW[1]} um excluding {HYDRATION[0]}-{HYDRATION[1]} um; error floor "
                     f"{ERR_FLOOR:.0%} for rotation and calibration; 4-sigma clipping (blends, cosmic rays).",
            "eta": "fitted when it is constrained (sigma < 0.3, 0.5-2.5), else fixed at 1.0, close to the "
                   "NEOWISE/IRAS/AKARI average for large main-belt asteroids (0.8-1.0) and the NEATM default "
                   "for slow rotators.",
            "published": "JPL SBDB phys_par (the adopted IRAS, NEOWISE or AKARI value; see D_ref/pv_ref).",
            "band_3um": f"1 - (observed / continuum model) averaged over {BAND_3UM[0]}-{BAND_3UM[1]} um.",
        },
        "summary": summary,
        "asteroids": results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1, default=float))
    print("wrote", OUT_JSON, OUT_JSON.stat().st_size // 1024, "kB")


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("fetch", "all"):
        cmd_fetch()
    if cmd in ("measure", "all"):
        cmd_measure()
    if cmd in ("fit", "all"):
        cmd_fit()
