"""The five challenge targets with Gaia DR3 astrometry (epoch 2016.0) -> data/targets.json.

Run: cd scripts/validate && uv run python targets.py

Gaia rows come from the ESA Gaia Archive TAP (astroquery.gaia) by source_id. The source_ids were
picked on 14 Nov 2026 from small cones around each star (brightest / fastest match; see `note`).
WISE 0855-0714 is not in Gaia (far too faint at G): it uses published astrometry, `source: "literature"`.
"""

from __future__ import annotations

import numpy as np
from astroquery.gaia import Gaia

from _common import DATA, GAIA_EPOCH_MJD, GAIA_EPOCH_YR, mjd_to_jyear, propagate, today, write_json

GAIA_TARGETS = [
    {
        "slug": "barnard",
        "name": "Barnard's Star",
        "gaia_source_id": "4472832130942575872",
        "note": "Fastest proper motion in a 5' cone (10.39\"/yr). K ~ 4.5: core expected to saturate.",
    },
    {
        "slug": "epsindib",
        "name": "Epsilon Indi B",
        "gaia_source_id": "6412596012146801152",
        "note": "Ba+Bb unresolved in Gaia and SPHEREx (<1\" apart). Epsilon Indi A (G ~ 4.3) is ~7' away.",
    },
    {
        "slug": "rwaur",
        "name": "RW Aurigae",
        "gaia_source_id": "156430822114424576",
        "note": "RW Aur A (G 12.18). RW Aur B (Gaia DR3 156430817820015232, G 13.04, RUWE 16) is 1.4\" "
                "away: one blend at SPHEREx's 6.15\" pixels.",
    },
    {
        "slug": "rdra",
        "name": "R Draconis",
        "gaia_source_id": "1648328016354496384",
        "note": "Mira variable (G 6.37 in DR3). Near the north ecliptic pole: many visits expected.",
    },
]

# WISE J085510.83-071442.5 (WISE 0855-0714): Luhman & Esplin 2016, AJ 152, 78 (arXiv:1605.06655).
#   Table 4: parallax 0.449 +- 0.008", mu_alpha cos(delta) = -8.118 +- 0.008 "/yr,
#            mu_delta = +0.680 +- 0.007 "/yr.
#   Table 2 (measured astrometry, J2000/ICRS): HST, MJD 57474.62:
#            RA 133.7819058 (+-0.020"), Dec -7.2440115 (+-0.020").
# The anchor is a measured position, so it contains that date's parallactic offset (<= 0.45").
# We move it linearly to J2016.0 so every target shares one reference epoch.
WISE0855 = {
    "slug": "wise0855",
    "name": "WISE 0855−0714",
    "anchor_ra": 133.7819058,
    "anchor_dec": -7.2440115,
    "anchor_mjd": 57474.62,
    "pmra": -8118.0,
    "pmdec": 680.0,
    "parallax": 449.0,
    "ref": "Luhman & Esplin 2016, AJ 152, 78 (arXiv:1605.06655), Tables 2 and 4",
}


def gaia_rows(ids: list[str]) -> dict[str, dict]:
    q = (
        "SELECT source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, ref_epoch, ruwe "
        f"FROM gaiadr3.gaia_source WHERE source_id IN ({', '.join(ids)})"
    )
    t = Gaia.launch_job(q).get_results()
    out = {}
    for r in t:
        out[str(r["source_id"])] = {
            k: None if np.ma.is_masked(r[k]) else float(r[k])
            for k in ("ra", "dec", "pmra", "pmdec", "parallax", "phot_g_mean_mag", "ref_epoch", "ruwe")
        }
    return out


def main() -> None:
    rows = gaia_rows([t["gaia_source_id"] for t in GAIA_TARGETS])
    targets = []
    for t in GAIA_TARGETS:
        g = rows[t["gaia_source_id"]]
        assert g["ref_epoch"] == GAIA_EPOCH_YR, g
        targets.append({
            "slug": t["slug"],
            "name": t["name"],
            "source": "gaia_dr3",
            "gaia_source_id": t["gaia_source_id"],
            "ra": g["ra"],
            "dec": g["dec"],
            "pmra": g["pmra"],
            "pmdec": g["pmdec"],
            "parallax": g["parallax"],
            "phot_g_mean_mag": g["phot_g_mean_mag"],
            "ruwe": g["ruwe"],
            "ref_epoch": GAIA_EPOCH_YR,
            "ref_mjd": GAIA_EPOCH_MJD,
            "ref": "Gaia DR3 (gaiadr3.gaia_source, ESA Gaia Archive TAP)",
            "note": t["note"],
        })

    w = WISE0855
    ra16, dec16 = propagate(w["anchor_ra"], w["anchor_dec"], w["pmra"], w["pmdec"], w["anchor_mjd"],
                            GAIA_EPOCH_MJD)
    targets.insert(2, {
        "slug": w["slug"],
        "name": w["name"],
        "source": "literature",
        "gaia_source_id": None,
        "ra": round(float(ra16), 7),
        "dec": round(float(dec16), 7),
        "pmra": w["pmra"],
        "pmdec": w["pmdec"],
        "parallax": w["parallax"],
        "phot_g_mean_mag": None,
        "ruwe": None,
        "ref_epoch": GAIA_EPOCH_YR,
        "ref_mjd": GAIA_EPOCH_MJD,
        "ref": w["ref"],
        "anchor": {"ra": w["anchor_ra"], "dec": w["anchor_dec"], "mjd": w["anchor_mjd"],
                   "jyear": round(mjd_to_jyear(w["anchor_mjd"]), 4), "instrument": "HST/WFC3"},
        "note": "Not in Gaia. ra/dec moved linearly from the HST anchor (MJD 57474.62) to J2016.0 with "
                "the published proper motion; the anchor includes that date's parallax offset (<= 0.45\"). "
                "Expected only in D5-D6.",
    })

    out = {
        "generated": today(),
        "units": {"ra": "deg", "dec": "deg", "pmra": "mas/yr (x cos dec)", "pmdec": "mas/yr",
                  "parallax": "mas", "phot_g_mean_mag": "mag"},
        "ref_epoch_note": "Gaia DR3 epoch J2016.0 = MJD 57388.5 (TCB; the 0.5 d offset from the "
                          "contract's 57388.0 is negligible).",
        "targets": targets,
    }
    path = DATA / "targets.json"
    write_json(path, out)
    for t in targets:
        print(f"{t['slug']:10s} {t['source']:10s} {t['ra']:.6f} {t['dec']:+.6f} "
              f"pm=({t['pmra']:.1f},{t['pmdec']:.1f}) plx={t['parallax']:.1f} G={t['phot_g_mean_mag']}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
