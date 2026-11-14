"""Comet 3I/ATLAS (C/2025 N1): coma photometry in several apertures, 0.75-5.0 um spectrum, gas bands.

    uv run python comet.py fetch      # cutouts centred on NASA's comet position, every image
    uv run python comet.py measure    # photometry -> .cache/comet_points.json
    uv run python comet.py fit        # Horizons + band excess + Haser -> data/comet_3i_gas.json
    uv run python comet.py all

Input: scripts/validate/data/photolists/3i-atlas.csv (IRSA's 3I/ATLAS table: comet_ra/comet_dec per
image, matched to DR1 by SIA). Visit 1 = Aug 2025 (pre-perihelion), visit 2 = Dec 2025.
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

import cutouts as C
import models as M
from phot import measure

HERE = Path(__file__).resolve().parent
LIST_CSV = HERE.parent / "validate" / "data" / "photolists" / "3i-atlas.csv"
POINTS_JSON = C.CACHE / "comet_points.json"
GEOM_JSON = C.CACHE / "comet_horizons.json"
OUT_JSON = HERE / "data" / "comet_3i_gas.json"
SIZE_DEG = 0.06  # 3.6' = 35 px
RADII = (1.0, 2.0, 3.0, 5.0)
ANNULUS = (12.0, 17.0)  # px; the CO2 coma reaches >= 3' (Lisse et al. 2025), so this removes some gas
PIX_ARCSEC = 6.152
SAMPLING_SYS = 0.15  # fractional, added to each band flux error
KM_PER_ARCSEC_AU = 725.27

# band: (window um, continuum side windows, polynomial order, line um, g-factor at 1 au, tau at 1 au s)
# g: Crovisier (1997) / Crovisier database, the values Lisse et al. (2025a,b) used, for comparison.
# tau: photodissociation lifetimes at 1 au, quiet Sun (Huebner et al. 1992).
BANDS = {
    "CO2": dict(band=(4.17, 4.37), sides=[(3.95, 4.13), (4.40, 4.56)], order=1, um=4.26, g1=2.6e-3, tau1=5.0e5),
    "CO": dict(band=(4.57, 4.79), sides=[(4.40, 4.55), (4.81, 5.00)], order=1, um=4.67, g1=2.6e-4, tau1=1.3e6),
    "H2O": dict(band=(2.55, 2.92), sides=[(2.25, 2.52), (2.93, 3.12)], order=1, um=2.70, g1=2.9e-4, tau1=8.3e4),
    "organics_CH": dict(band=(3.22, 3.60), sides=[(3.00, 3.20), (3.62, 3.80)], order=1, um=3.4, g1=None, tau1=None),
}


def load_list() -> list[dict]:
    with open(LIST_CSV) as fh:
        return [r for r in csv.DictReader(fh) if r["access_url"].startswith("https://irsa")]


def job(r: dict) -> tuple[str, Path]:
    u = C.cutout_url(r["access_url"], float(r["obj_ra"]), float(r["obj_dec"]), SIZE_DEG)
    return u, C._cache_path(u, tag="c3i_")


def cmd_fetch(source: str = "s3") -> None:
    """source "s3": range reads of the public S3 copy (35 x 35 px box); "irsa": IRSA cutout service."""
    rows = load_list()
    print(f"{len(rows)} comet cutouts ({source})", flush=True)
    if source == "s3":
        jobs = [(r["s3_key"], float(r["obj_ra"]), float(r["obj_dec"]), 17, job(r)[1]) for r in rows if r["s3_key"]]
        res = C.fetch_many_s3(jobs, log_every=50)
    else:
        res = C.fetch_many([job(r) for r in rows], log_every=50)
    bad = [u for u, v in res.items() if isinstance(v, Exception)]
    print(f"done: {len(res) - len(bad)} ok, {len(bad)} failed")
    for u in bad[:10]:
        print("  failed:", u, res[u])


def cmd_measure() -> None:
    rows = load_list()
    pts, why = [], defaultdict(int)
    for r in rows:
        u, p = job(r)
        if not p.exists():
            why["not fetched"] += 1
            continue
        l2 = C.open_cutout(p)
        ra, dec = float(r["obj_ra"]), float(r["obj_dec"])
        pt = measure(l2, ra, dec, radii=RADII, annulus=ANNULUS, recentre=True, max_offset_px=2.0, mask_r=2.0,
                     fill_masked=True)
        forced = False
        if not pt.ok and pt.why.startswith(("no centroid", "centroid")):
            pt = measure(l2, ra, dec, radii=RADII, annulus=ANNULUS, recentre=False, mask_r=2.0, fill_masked=True)
            forced = True
        if not pt.ok:
            why[pt.why] += 1
            continue
        pts.append({
            "um": round(pt.um, 4), "bw": round(pt.bw_um, 4), "mjd": round(l2.mjd_avg, 6), "det": l2.detector,
            "visit": int(r["visit"]), "session": int(r["session"]), "obsid": l2.obsid,
            "flux": {f"r{int(k)}": round(v, 2) for k, v in pt.flux.items()},
            "err": {f"r{int(k)}": round(v, 2) for k, v in pt.err.items()},
            "off_px": round(pt.offset_px, 2), "forced": forced, "rec_flags": pt.record_flags,
        })
    POINTS_JSON.write_text(json.dumps({"points": pts, "dropped": dict(why), "images": len(rows)}))
    print(f"{len(pts)}/{len(rows)} points; dropped {dict(why)}")


def horizons(mjds: np.ndarray) -> dict:
    from astropy.time import Time
    from astroquery.jplhorizons import Horizons

    out = {}
    lo, hi = float(mjds.min()) - 1, float(mjds.max()) + 1
    e = Horizons(id="C/2025 N1", id_type="smallbody", location="500",
                 epochs={"start": Time(lo, format="mjd").iso[:16], "stop": Time(hi, format="mjd").iso[:16],
                         "step": "6h"}).ephemerides(quantities="19,20,24")
    jd = np.asarray(e["datetime_jd"], float) - 2400000.5
    for k in ("r", "delta", "alpha"):
        out[k] = np.interp(mjds, jd, np.asarray(e[k], float)).tolist()
    return out


def cmd_fit() -> None:
    d = json.loads(POINTS_JSON.read_text())
    pts = d["points"]
    mjds = np.array([p["mjd"] for p in pts])
    geo = json.loads(GEOM_JSON.read_text()) if GEOM_JSON.exists() else None
    if geo is None or len(geo["r"]) != len(pts):
        geo = horizons(mjds)
        GEOM_JSON.write_text(json.dumps(geo))
    r_all, d_all, a_all = (np.asarray(geo[k]) for k in ("r", "delta", "alpha"))
    um_all = np.array([p["um"] for p in pts])
    visits = []
    for v in sorted({p["visit"] for p in pts}):
        sel = np.array([p["visit"] == v for p in pts])
        vp = [p for p, s in zip(pts, sel) if s]
        r0, d0 = float(np.median(r_all[sel])), float(np.median(d_all[sel]))
        # fixed angular aperture: dust and gas (rho << L) both scale as 1 / (r^2 Delta)
        scale = (r_all[sel] / r0) ** 2 * (d_all[sel] / d0)
        um = um_all[sel]
        vis = {
            "visit": v, "n_points": int(sel.sum()),
            "mjd": [float(mjds[sel].min()), float(mjds[sel].max())],
            "date": [time.strftime("%Y-%m-%d", time.gmtime((float(x) - 40587) * 86400)) for x in
                     (mjds[sel].min(), mjds[sel].max())],
            "r_au": r0, "delta_au": d0, "alpha_deg": float(np.median(a_all[sel])),
            "r_range_au": [float(r_all[sel].min()), float(r_all[sel].max())],
            "apertures": {},
        }
        for rad in RADII:
            k = f"r{int(rad)}"
            f = np.array([p["flux"][k] for p in vp]) * scale
            e = np.array([p["err"][k] for p in vp]) * scale
            # error floor 3%: activity changes and calibration within a visit
            e = np.hypot(e, 0.03 * np.abs(f))
            flam = M.ujy_to_flam(f, um)
            elam = M.ujy_to_flam(e, um)
            rho_km = rad * PIX_ARCSEC * d0 * KM_PER_ARCSEC_AU
            ann_km = (ANNULUS[0] * PIX_ARCSEC * d0 * KM_PER_ARCSEC_AU, ANNULUS[1] * PIX_ARCSEC * d0 * KM_PER_ARCSEC_AU)
            bands = {}
            for name, b in BANDS.items():
                ex = M.band_excess(um, flam, elam, b["band"], b["sides"], b["order"])
                if ex["flux_w_m2"] is not None:
                    # sampling systematic: 5-8 channel samples per band, channel response not modelled
                    # snr stays the detection significance (noise only): the sampling systematic
                    # scales with the band flux and cannot make a band out of nothing
                    ex["err_total_w_m2"] = float(np.hypot(ex["err_w_m2"], SAMPLING_SYS * abs(ex["flux_w_m2"])))
                if b["g1"] is not None and ex["flux_w_m2"] is not None:
                    v_kms = 0.85 / np.sqrt(r0)
                    pr = M.production_rate(ex["flux_w_m2"], b["g1"], b["um"], r0, d0, rho_km, v_kms, b["tau1"],
                                           annulus_km=ann_km)
                    ex["Q_per_s"] = pr["Q_per_s"]
                    ex["Q_err_per_s"] = abs(pr["Q_per_s"] * ex["err_total_w_m2"] / ex["flux_w_m2"]) if ex["flux_w_m2"] else None
                    ex["Q_3sigma_upper_per_s"] = pr["Q_per_s"] / ex["flux_w_m2"] * 3 * ex["err_w_m2"] if ex["flux_w_m2"] else None
                    ex["haser"] = {k2: pr[k2] for k2 in ("g", "L_km", "v_km_s", "rho_km", "annulus_fraction_lost")}
                bands[name] = ex
            vis["apertures"][k] = {"rho_km": rho_km, "bands": bands,
                                   "flux_1um_ujy": float(np.median(f[(um > 0.95) & (um < 1.05)])) if ((um > 0.95) & (um < 1.05)).any() else None}
        visits.append(vis)
        for k in ("r2", "r5"):
            bb = vis["apertures"][k]["bands"]
            print(f"visit {v} {vis['date']} r={r0:.2f} D={d0:.2f} {k}: " + "; ".join(
                f"{n} {x['snr']:.1f}sig Q={x.get('Q_per_s', 0) or 0:.2g}" for n, x in bb.items() if x["snr"] is not None))
    out = {
        "generated": time.strftime("%Y-%m-%d"),
        "target": "3I/ATLAS (C/2025 N1)",
        "method": {
            "data": f"IRSA cutouts ({SIZE_DEG * 60:.1f}', 35 px) of every SPHEREx DR1 image in IRSA's 3I/ATLAS "
                    "table, centred on NASA's comet position; centroid within 2 px (else forced at NASA's position); "
                    f"apertures {list(RADII)} px (6.15\"/px), sigma-clipped median background in a "
                    f"{ANNULUS[0]:.0f}-{ANNULUS[1]:.0f} px annulus; points with a mask bit within 2 px dropped. "
                    "No aperture correction (extended coma; the core's 1.033 is applied at r = 2 px only).",
            "scaling": "each point scaled to the visit's median r and Delta by (r/r0)^2 (Delta/Delta0), the fixed-"
                       "angular-aperture dependence of both dust (Afrho) and gas (rho << L) fluxes.",
            "band_excess": "a straight-line continuum fitted to side windows next to each band; excess = mean "
                           "residual f_lambda in the band x band width = integrated band flux (W m^-2).",
            "production_rate": "N = 4 pi Delta^2 F / (g h nu), g = g1 / r^2; Haser parent with v = 0.85 r^-0.5 "
                               "km/s and L = v tau1 r^2; the share of the column removed by the background "
                               "annulus is added back with the same Haser model. g1 (photons/s/molecule at 1 au): "
                               "CO2 2.6e-3, CO 2.6e-4, H2O 2.9e-4 (Crovisier 1997, as in Lisse et al.). tau1 (s, "
                               "quiet Sun, Huebner et al. 1992): CO2 5.0e5, CO 1.3e6, H2O 8.3e4.",
            "errors": "err_w_m2 = measurement errors + side-window jitter (stars crossing the aperture, "
                      "activity) through the trapezoid weights, plus the continuum error; snr = flux / err_w_m2 "
                      "(detection significance). err_total_w_m2 adds 15% for the sparse channel sampling. "
                      "Q_err_per_s scales err_total_w_m2; the "
                      "g-factors, lifetimes and v add a factor ~2 systematic.",
            "geometry": "JPL Horizons C/2025 N1, geocentre (SPHEREx is in low Earth orbit).",
        },
        "bands": {k: {"band_um": list(v["band"]), "sides_um": [list(s) for s in v["sides"]]} for k, v in BANDS.items()},
        "visits": visits,
        "dropped": d["dropped"], "images": d["images"],
        "points": [{"um": p["um"], "mjd": p["mjd"], "det": p["det"], "visit": p["visit"], "flux": p["flux"],
                    "err": p["err"]} for p in pts],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1, default=float))
    print("wrote", OUT_JSON, OUT_JSON.stat().st_size // 1024, "kB")


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("fetch", "all"):
        cmd_fetch(sys.argv[2] if len(sys.argv) > 2 else "s3")
    if cmd in ("measure", "all"):
        cmd_measure()
    if cmd in ("fit", "all"):
        cmd_fit()
