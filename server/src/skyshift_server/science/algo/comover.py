"""Co-moving companion detective.

Given a proper motion measured from SPHEREx, look in Gaia DR3 for a star within a few
arcminutes-to-degrees that shares the motion. If found, the companion's parallax gives the
distance of the SPHEREx object, which turns its SPHEREx spectrum into a luminosity and an
effective temperature estimate (Epsilon Indi B -> Epsilon Indi A is the textbook case).

Requires network access to the ESA Gaia archive (gea.esac.esa.int).
"""
import numpy as np
import requests

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"


def query_gaia(ra, dec, radius_deg=1.0, min_pm_masyr=100):
    q = f"""SELECT source_id, ra, dec, parallax, parallax_error, pmra, pmdec, phot_g_mean_mag
            FROM gaiadr3.gaia_source
            WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {radius_deg}))
            AND SQRT(pmra*pmra + pmdec*pmdec) > {min_pm_masyr}"""
    r = requests.post(GAIA_TAP, data=dict(REQUEST="doQuery", LANG="ADQL", FORMAT="json", QUERY=q), timeout=20)
    r.raise_for_status()
    j = r.json()
    cols = [c["name"] for c in j["metadata"]]
    return [dict(zip(cols, row)) for row in j["data"]]


def find_comovers(ra, dec, pmra, pmdec, epmra, epmdec, radius_deg=1.0, n_sigma=3, rows=None):
    """pm in arcsec/yr (SPHEREx fit). Returns candidates ranked by pm agreement."""
    rows = rows if rows is not None else query_gaia(ra, dec, radius_deg)
    out = []
    for r in rows:
        dpa = (r["pmra"] / 1000 - pmra) / max(epmra, 0.005)
        dpd = (r["pmdec"] / 1000 - pmdec) / max(epmdec, 0.005)
        chi = float(np.hypot(dpa, dpd))
        if chi < n_sigma * 1.4142:
            sep = float(np.degrees(np.arccos(np.clip(
                np.sin(np.radians(dec)) * np.sin(np.radians(r["dec"])) +
                np.cos(np.radians(dec)) * np.cos(np.radians(r["dec"])) * np.cos(np.radians(r["ra"] - ra)), -1, 1))) * 3600)
            d_pc = 1000 / r["parallax"] if r.get("parallax") and r["parallax"] > 0 else None
            out.append(dict(r, pm_chi=chi, sep_arcsec=sep, distance_pc=d_pc,
                            projected_sep_au=(sep * d_pc if d_pc else None)))
    return sorted(out, key=lambda r: r["pm_chi"])


def luminosity_from_spectrum(spec_points, distance_pc):
    """Integrate the SPHEREx 0.75-5 um SED (mJy) -> lower-limit bolometric luminosity (L_sun)."""
    pts = sorted([p for p in spec_points if np.isfinite(p["flux"])], key=lambda p: p["lam"])
    if len(pts) < 5 or not distance_pc:
        return None
    lam = np.array([p["lam"] for p in pts]) * 1e-6
    fnu = np.array([p["flux"] for p in pts]) * 1e-29        # mJy -> W/m2/Hz
    nu = 2.998e8 / lam
    F = float(np.trapezoid(fnu[::-1], nu[::-1]))            # W/m2
    d = distance_pc * 3.0857e16
    L = 4 * np.pi * d ** 2 * F
    return dict(L_W=L, L_sun=L / 3.828e26, note="0.75-5 um only: a lower limit (brown dwarfs emit most of their light here)")
