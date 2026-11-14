"""NEOWISE -> SPHEREx relay light curve.

NEOWISE (2013-2024) measured W1 (3.4 um) and W2 (4.6 um) every six months. SPHEREx
(2025-) measures a full 0.75-5 um spectrum. By synthesising W1/W2 from each SPHEREx visit's
spectrum we extend a 10-year NEOWISE light curve into the SPHEREx era with the same bands.

Needs IRSA TAP (irsa.ipac.caltech.edu): works from a normal internet connection.
"""
import numpy as np
import requests

IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP/sync"


def neowise_epochs(ra, dec, radius_arcsec=4.0, gap_days=30, pm=None, ref_mjd=None):
    """pm = (pmra, pmdec) in arcsec/yr at reference epoch ref_mjd (the SPHEREx epoch of ra/dec).
    For fast movers the cone follows the target back in time, otherwise a fixed 4" cone at the
    2025 position misses it (or catches a background source) in 2014-2024."""
    pm_tot = float(np.hypot(*pm)) if pm is not None and ref_mjd else 0.0
    search = radius_arcsec + pm_tot * 12.0
    q = f"""SELECT ra, dec, mjd, w1mpro, w1sigmpro, w2mpro, w2sigmpro, qual_frame, cc_flags
            FROM neowiser_p1bs_psd
            WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {search / 3600})) = 1
            AND qual_frame > 0"""
    # IRSA's FORMAT=json is VOTable-as-JSON (not the TAP 'metadata/data' layout), so use CSV.
    import csv, io
    r = requests.get(IRSA_TAP, params=dict(QUERY=" ".join(q.split()), FORMAT="csv"), timeout=20)
    r.raise_for_status()
    rows = []
    for x in csv.DictReader(io.StringIO(r.text)):
        d = {}
        for k, v in x.items():
            if k == "cc_flags":
                d[k] = v
            else:
                try:
                    d[k] = float(v) if v not in ("", None) else None
                except ValueError:
                    d[k] = None
        rows.append(d)
    rows = [x for x in rows if str(x.get("cc_flags", "00"))[:2] in ("00", "0", "")]
    cosd = np.cos(np.radians(dec))
    def near(x):
        dt = (x["mjd"] - ref_mjd) / 365.25 if pm_tot else 0.0
        ra_p = ra + (pm[0] * dt / 3600) / cosd if pm_tot else ra
        de_p = dec + (pm[1] * dt / 3600) if pm_tot else dec
        return np.hypot((x["ra"] - ra_p) * cosd, x["dec"] - de_p) * 3600 < radius_arcsec
    rows = [x for x in rows if x.get("ra") is not None and near(x)]
    rows.sort(key=lambda x: x["mjd"])
    # bin into ~6-monthly visits, robust mean in flux space
    out, cur = [], []
    for x in rows:
        if cur and x["mjd"] - cur[-1]["mjd"] > gap_days:
            out.append(cur); cur = []
        cur.append(x)
    if cur:
        out.append(cur)
    vis = []
    for g in out:
        d = dict(mjd=float(np.mean([x["mjd"] for x in g])), n=len(g), source="NEOWISE")
        for b in ("w1", "w2"):
            m = np.array([x[f"{b}mpro"] for x in g if x[f"{b}mpro"] is not None], float)
            if m.size:
                med = np.median(m); mad = 1.4826 * np.median(np.abs(m - med)) or 0.05
                d[b.upper()] = float(med); d[b.upper() + "_err"] = float(mad / np.sqrt(m.size))
        vis.append(d)
    return vis


def relay_curve(ra, dec, spherex_epochs, target_motion=None):
    """Combine NEOWISE visits with SPHEREx synthetic W1/W2 (from pipeline 'spectrum_epochs')."""
    try:
        pm = ref = None
        if target_motion and spherex_epochs:
            pm = (target_motion["pmra"], target_motion["pmdec"])
            ref = float(np.mean([e["mjd"] for e in spherex_epochs]))
        nw = neowise_epochs(ra, dec, pm=pm, ref_mjd=ref)
        err = None
    except Exception as e:
        nw, err = [], f"NEOWISE query failed: {e}"
    sp = []
    for e in spherex_epochs:
        d = dict(mjd=e["mjd"], source="SPHEREx (synthetic)")
        for b in ("W1", "W2"):
            if e.get(b) and e[b].get("vega_mag") is not None:
                d[b] = e[b]["vega_mag"]
                d[b + "_err"] = 2.5 / np.log(10) * e[b]["err_mJy"] / max(e[b]["flux_mJy"], 1e-9)
        sp.append(d)
    return dict(points=nw + sp, error=err)
