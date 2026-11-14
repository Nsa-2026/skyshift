"""Write SYNTHETIC example responses for every shape in contracts/api.md into fixtures/api/.

Stdlib only, deterministic. Run from anywhere:  python3 -I scripts/make_fixtures.py

Everything written here is made up (realistic numbers, fake pixels) and marked
"synthetic": true. It lets web/ and server/ start before real data exists.
Never copy these files into web/public/data/.
"""

import json
import math
import random
import shutil
import sys
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "api"
CHECKED = "2026-11-14"
RELEASE = "DR1+QR3"
NOTE = ("SYNTHETIC example made by scripts/make_fixtures.py. Fake pixels and made-up numbers in the "
        "shape of contracts/api.md. Not real SPHEREx data.")

GAIA_EPOCH_MJD = 57388.0          # J2016.0, Gaia DR3 reference epoch
SIDE = 48                         # tiny frames; real frames are up to 300 px a side
TELAPSE_D = 118.2 / 86400.0       # exposure start to stop, days
NATIVE_PIX = 6.15                 # arcsec
UJY_PER_MJYSR_NATIVE = 889.0      # 1 MJy/sr in one 6.15" pixel

# Barnard's Star, Gaia DR3 (epoch 2016.0)
BARNARD = {"id": "4472832130942575872", "ra": 269.44850252543836, "dec": 4.739420051112412,
           "pmra": -801.551, "pmdec": 10362.394, "parallax": 546.976, "g": 8.19}


def r(x, n=6):
    return None if x is None else round(x, n)


def move(star, mjd):
    """Move a Gaia star from epoch 2016.0 to mjd (proper motion only)."""
    dt_yr = (mjd - GAIA_EPOCH_MJD) / 365.25
    dec = star["dec"] + star["pmdec"] * dt_yr / 3.6e6
    ra = star["ra"] + star["pmra"] * dt_yr / 3.6e6 / math.cos(math.radians(star["dec"]))
    return ra, dec


class Grid:
    """Tangent-plane grid, north up, east left; centre at pixel ((n-1)/2, (n-1)/2)."""

    def __init__(self, ra0, dec0, pix_arcsec, n=SIDE):
        self.ra0, self.dec0, self.pix, self.n = ra0, dec0, pix_arcsec, n
        self.c = (n - 1) / 2.0
        self.cosd = math.cos(math.radians(dec0))

    def to_pix(self, ra, dec):
        east = (ra - self.ra0) * self.cosd * 3600.0
        north = (dec - self.dec0) * 3600.0
        return self.c - east / self.pix, self.c - north / self.pix   # (col, row)

    def to_sky(self, col, row):
        east = (self.c - col) * self.pix
        north = (self.c - row) * self.pix
        return self.ra0 + east / 3600.0 / self.cosd, self.dec0 + north / 3600.0


def render(n, background, noise, sources, rng, sat_level=None, nodata=()):
    """sources: (col, row, peak MJy/sr, sigma px). Returns (values, saturated count)."""
    img = [[background + rng.gauss(0.0, noise) for _ in range(n)] for _ in range(n)]
    for col, row, peak, sig in sources:
        rad = int(5 * sig) + 1
        for y in range(max(0, int(row) - rad), min(n, int(row) + rad + 2)):
            for x in range(max(0, int(col) - rad), min(n, int(col) + rad + 2)):
                img[y][x] += peak * math.exp(-((x - col) ** 2 + (y - row) ** 2) / (2 * sig * sig))
    nsat = 0
    if sat_level is not None:
        for y in range(n):
            for x in range(n):
                if img[y][x] > sat_level:
                    img[y][x] = sat_level
                    nsat += 1
    for x, y in nodata:
        img[y][x] = None
    return img, nsat


def encode(img, path):
    """Raw little-endian uint16, row 0 = north edge, col 0 = east edge. raw 0 = no data.
    value = raw * scale + zero, real values use raw 1..65535."""
    vals = [v for row in img for v in row if v is not None]
    vmin, vmax = min(vals), max(vals)
    scale = (vmax - vmin) / 65534.0 if vmax > vmin else 1.0
    zero = vmin - scale
    raw = array("H", (0 if v is None else max(1, min(65535, round((v - zero) / scale)))
                      for row in img for v in row))
    if sys.byteorder != "little":
        raw.byteswap()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw.tobytes())
    return scale, zero


def field_stars(grid, rng, count, sigma, gmin=12.0, gmax=17.0, prefix="SYNTH-GAIA-"):
    stars, sources = [], []
    for i in range(count):
        col, row = rng.uniform(4, grid.n - 5), rng.uniform(4, grid.n - 5)
        g = rng.uniform(gmin, gmax)
        ra, dec = grid.to_sky(col, row)
        stars.append({"id": f"{prefix}{i + 1}", "ra": r(ra, 7), "dec": r(dec, 7),
                      "pmra": r(rng.uniform(-15, 15), 3), "pmdec": r(rng.uniform(-15, 15), 3),
                      "parallax": r(rng.uniform(0.2, 3.0), 3), "g": r(g, 3)})
        sources.append((col, row, 300.0 * 10 ** (-0.4 * (g - 12.0)), sigma))
    return stars, sources


def write_json(name, obj):
    path = OUT / name
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- Months clock: Barnard's Star
def months_barnard():
    rng = random.Random(20261114)
    det, pix = 2, 2.05                        # months 5' field: 3x oversampled, 2.05"/px
    visits = [(60929.41, 60947.83, 19), (61111.18, 61129.62, 18), (61293.96, 61311.47, 20)]
    mids = [(a + b) / 2 for a, b, _ in visits]
    grid = Grid(*move(BARNARD, mids[1]), pix)
    stars, field = field_stars(grid, rng, 7, sigma=1.56)
    gaia = [BARNARD] + stars
    psf_sigma = 7.53 / 2.3548 / pix          # PSF FWHM 7.53"
    flux_ujy = 1.2e7                          # Barnard's Star near 1.3 um, about 12 Jy
    peak = flux_ujy / (UJY_PER_MJYSR_NATIVE * (pix / NATIVE_PIX) ** 2) / (2 * math.pi * psf_sigma ** 2)
    epochs = []
    shutil.rmtree(OUT / "frames" / "barnard", ignore_errors=True)
    for k, ((start, end, nexp), mid) in enumerate(zip(visits, mids), start=1):
        col, row = grid.to_pix(*move(BARNARD, mid))
        nodata = [(5, 40), (6, 40)] if k == 2 else []     # exercise raw 0 = no data
        img, nsat = render(SIDE, 0.52, 0.08, field + [(col, row, peak, psf_sigma)], rng,
                           sat_level=3000.0, nodata=nodata)
        eid = f"d{det}-v{k}"
        rel = f"frames/barnard/{eid}.bin"
        scale, zero = encode(img, OUT / rel)
        epochs.append({"id": eid, "kind": "visit", "mjd_mid": r(mid, 5), "mjd_start": start,
                       "mjd_end": end, "det": det, "um_min": 1.1, "um_max": 1.6,
                       "n_exposures": nexp,
                       "flag_frac": r((len(nodata) + 9 + nsat) / SIDE ** 2, 4),
                       "sat_frac": r(nsat / SIDE ** 2, 4),
                       "data_url": rel, "scale": r(scale, 9), "zero": r(zero, 6)})
    frames = {
        "synthetic": True,
        "note": NOTE + " Barnard's Star-like: steps 5.2\" (2.5 px) north per visit, core saturated. "
                       "48x48 instead of 146x146; Gaia positions are at epoch 2016.0, so Barnard's ring "
                       "starts off the frame until moved to each mjd_mid.",
        "clock": "months", "center": {"ra": r(grid.ra0, 7), "dec": r(grid.dec0, 7)},
        "pixel_arcsec": pix, "size": [SIDE, SIDE], "epochs": epochs, "gaia": gaia, "known": [],
        "data_release": RELEASE, "archive_checked": CHECKED,
    }
    write_json("frames_barnard.json", frames)
    write_json("gaia_barnard.json", {"synthetic": True, "note": NOTE, "gaia": gaia})
    return mids, grid


def object_barnard(mids):
    rng = random.Random(7)
    sed = [(0.8, 4.9e6), (1.25, 12.8e6), (1.65, 12.0e6), (2.2, 10.4e6), (3.4, 7.8e6), (4.6, 6.2e6)]

    def flux_at(um):
        for (u1, f1), (u2, f2) in zip(sed, sed[1:]):
            if um <= u2:
                t = (math.log(um) - math.log(u1)) / (math.log(u2) - math.log(u1))
                return math.exp(math.log(f1) + t * (math.log(f2) - math.log(f1)))
        return sed[-1][1]

    bands = [(1, 0.75, 1.1), (2, 1.1, 1.6), (3, 1.6, 2.4), (4, 2.4, 3.8), (5, 3.8, 4.4), (6, 4.4, 5.0)]
    spectrum = []
    for det, lo, hi in bands:
        for visit, mid in enumerate(mids, start=1):
            for j in range(3):
                um = lo + (hi - lo) * (j + 0.5) / 3
                f = flux_at(um) * rng.uniform(0.97, 1.03)
                spectrum.append({"um": r(um, 4), "flux_ujy": r(f, 0), "err_ujy": r(f * 0.02, 0),
                                 "det": det, "mjd": r(mid + rng.uniform(-6, 6), 5),
                                 "visit": visit, "saturated": det <= 4})
    spectrum.sort(key=lambda s: (s["um"], s["visit"]))
    dt_yr = (mids[-1] - mids[0]) / 365.25
    pm_total = math.hypot(BARNARD["pmra"], BARNARD["pmdec"]) / 1000.0
    shift = 10.21 * dt_yr
    obj = {
        "synthetic": True, "note": NOTE + " Numbers are plausible, not measured.",
        "name": "Barnard's Star", "gaia": BARNARD, "known_ucd": None, "spectrum": spectrum,
        "measure": {"shift_arcsec": r(shift, 3), "pa_deg": 356.1, "dt_yr": r(dt_yr, 4),
                    "rate_arcsec_yr": r(shift / dt_yr, 3), "sigma_arcsec": 0.9, "n_ref_stars": 12,
                    "predicted_arcsec": r(pm_total * dt_yr, 3)},
        "measure_reason": None, "measure_hint": None,
    }
    write_json("object_barnard.json", obj)


def object_moved_away(mids):
    """GET /api/object at Barnard's Star's Gaia (2016.0) position: by SPHEREx's visits the star is
    ~100" north, nothing is measured there, and measure_hint says where it went."""
    mid = (mids[0] + mids[-1]) / 2
    ra, dec = move(BARNARD, mid)
    obj = {
        "synthetic": True, "note": NOTE + " The position asked: Barnard's Star's Gaia DR3 position "
                                          "(269.4485, 4.7394, epoch 2016.0).",
        "name": None, "gaia": None, "known_ucd": None, "spectrum": [], "spectrum_reason": "no_data",
        "measure": None, "measure_reason": "moved_away",
        "measure_hint": {"name": "Barnard's Star", "ra": r(ra, 6), "dec": r(dec, 6), "mjd": r(mid, 2)},
    }
    write_json("object_moved_away.json", obj)


# ---------------------------------------------------------------- Hours clock: comet-like mover
def hours_comet():
    rng = random.Random(3)
    det, pix = 3, NATIVE_PIX                  # hours 20' field: native pixel
    grid = Grid(166.80, 5.30, pix)
    stars, field = field_stars(grid, rng, 8, sigma=0.6)
    t0 = 61023.30                             # 14 Dec 2025, like 3I/ATLAS's SPHEREx sequence
    step_d = 15 / 1440.0                      # one exposure every 15 min
    rate_e, rate_n = 172.0, -35.0             # arcsec/h east and north: about 176"/h
    col0, row0 = 41.0, 20.0
    ra0, dec0 = grid.to_sky(col0, row0)
    epochs, known = [], []
    shutil.rmtree(OUT / "frames" / "comet", ignore_errors=True)
    for k in range(6):
        mid = t0 + k * step_d
        hours = k * step_d * 24
        ra = ra0 + rate_e * hours / 3600.0 / grid.cosd
        dec = dec0 + rate_n * hours / 3600.0
        col, row = grid.to_pix(ra, dec)
        img, _ = render(SIDE, 0.41, 0.15, field + [(col, row, 6.0, 1.3), (col + 1.5, row - 0.4, 2.0, 1.8)], rng)
        obs_id = f"SYNTH-2025W50_1A_{k + 1:04d}_1"
        eid = f"d{det}-{obs_id}"
        rel = f"frames/comet/{eid}.bin"
        scale, zero = encode(img, OUT / rel)
        um_lo = 1.912 + 0.004 * k
        epochs.append({"id": eid, "kind": "exposure", "mjd_mid": r(mid, 6),
                       "mjd_start": r(mid - TELAPSE_D / 2, 6), "mjd_end": r(mid + TELAPSE_D / 2, 6),
                       "det": det, "um_min": r(um_lo, 3), "um_max": r(um_lo + 0.019, 3),
                       "n_exposures": 1, "flag_frac": r(rng.uniform(0.01, 0.03), 4), "sat_frac": 0.0,
                       "data_url": rel, "scale": r(scale, 9), "zero": r(zero, 6)})
        known.append({"name": "SYNTHETIC mover (3I/ATLAS-like)", "epoch_id": eid,
                      "ra": r(ra, 7), "dec": r(dec, 7), "v": 15.8, "source": "irsa-3i"})
    frames = {
        "synthetic": True,
        "note": NOTE + " Comet-like mover at 3I/ATLAS's rate (176\"/h, about 7 px per 15-min step), "
                       "6 exposures in D3. 48x48 instead of 195x195.",
        "clock": "hours", "center": {"ra": grid.ra0, "dec": grid.dec0}, "pixel_arcsec": pix,
        "size": [SIDE, SIDE], "epochs": epochs, "gaia": stars, "known": known,
        "data_release": RELEASE, "archive_checked": CHECKED,
    }
    write_json("frames_comet.json", frames)
    k = known[2]
    write_json("known_comet.json", {"synthetic": True, "note": NOTE, "mjd": epochs[2]["mjd_mid"],
                                    "known": [dict(k, epoch_id=None)]})


# ---------------------------------------------------------------- resolve, validation, suspects
def resolve():
    ra, dec = move(BARNARD, 61358.0)      # 14 Nov 2026
    write_json("resolve_barnard.json", {"synthetic": True, "note": NOTE, "q": "Barnard's Star",
                                        "name": "Barnard's Star", "ra": r(ra, 7), "dec": r(dec, 7),
                                        "slug": "barnard", "source": "builtin"})
    ra = (8 + 55 / 60 + 10.831 / 3600) * 15
    dec = -(7 + 14 / 60 + 42.53 / 3600)
    write_json("resolve_coords.json", {"synthetic": True, "note": NOTE, "q": "08h55m10.831s -7d14m42.53s",
                                       "name": None, "ra": r(ra, 7), "dec": r(dec, 7),
                                       "slug": None, "source": "coords"})


def validation():
    pending = lambda i, claim, expected: {"id": i, "claim": claim, "status": "pending", "headline": "not run yet",
                                          "value": None, "unit": None, "expected": expected, "note": None, "rows": []}
    checks = [
        {"id": "V1", "claim": "Five test objects recovered", "status": "partial", "headline": "4 of 5",
         "value": 4, "unit": "targets", "expected": "5 of 5; WISE 0855 only in D5-D6",
         "note": "rows: slug, visits, detected, offset_px",
         "rows": [{"slug": "barnard", "visits": 3, "detected": 3, "offset_px": 0.4},
                  {"slug": "epsindib", "visits": 3, "detected": 3, "offset_px": 0.6},
                  {"slug": "wise0855", "visits": 3, "detected": 1, "offset_px": 1.3},
                  {"slug": "rwaur", "visits": 3, "detected": 3, "offset_px": 0.3},
                  {"slug": "rdra", "visits": 8, "detected": 8, "offset_px": 0.5}]},
        {"id": "V2", "claim": "Motion matches Gaia", "status": "pass",
         "headline": "Barnard's Star: 10.2\"/yr at 356°; Gaia says 10.39\"/yr at 355.6°", "value": 10.21,
         "unit": "arcsec/yr", "expected": "within 15% and 10° of 10.39\"/yr",
         "note": "rows: slug, rate_arcsec_yr, pa_deg, gaia_rate_arcsec_yr, gaia_pa_deg, sigma_arcsec",
         "rows": [{"slug": "barnard", "rate_arcsec_yr": 10.21, "pa_deg": 356.1, "gaia_rate_arcsec_yr": 10.39,
                   "gaia_pa_deg": 355.6, "sigma_arcsec": 0.9}]},
        {"id": "V3", "claim": "Position error floor", "status": "pass", "headline": "0.9\" per visit (12 stars)",
         "value": 0.9, "unit": "arcsec", "expected": "hoping for 0.6-1.2\"", "note": None, "rows": []},
        {"id": "V4", "claim": "Asteroids re-found, targeted", "status": "partial", "headline": "38 of 60; control 3%",
         "value": 38, "unit": "asteroids", "expected": "median offset under 1 px; control under 5%",
         "note": "rows: number, exposures, found, offset_px", "rows": []},
        pending("V5", "Asteroids re-found, blind (S1)", "recall by V magnitude"),
        pending("V6", "Fast stars (S2)", "slope near 1"),
        pending("V7", "Brown dwarfs (S3)", "a count"),
        pending("V8", "Spectra and speed", "within 20%; first frame under 15 s"),
    ]
    write_json("validation.json", {"synthetic": True, "note": NOTE, "generated": "2026-11-14T09:30:00Z",
                                   "data_release": RELEASE, "checks": checks})


def suspects():
    rng = random.Random(11)
    out = []
    for i in range(3):
        ra, dec = rng.uniform(125.0, 126.0), rng.uniform(5.0, 6.0)
        # cell: integer id of the 6″ sky cell, exactly contracts/dots.md (pipeline sky.cell_of); never a string
        band = math.floor((dec + 90) * 600)
        cos_c = math.cos(math.radians((band + 0.5) / 600 - 90))
        cell = band * 10_000_000 + math.floor(ra * cos_c * 600)
        out.append({"id": f"SYNTH-S{i + 1}", "ra": r(ra, 6), "dec": r(dec, 6),
                    "mjd": r(rng.uniform(60968.1, 60968.3), 6), "clock": "hours", "det": rng.choice([1, 2, 3]),
                    "evidence": ["two_exposures", "two_detectors"][i % 2], "n_dots": 2,
                    "snr": r(rng.uniform(6, 15), 1), "flux_ujy": r(rng.uniform(40, 300), 1), "cell": cell,
                    "nearest_known_arcsec": None if i == 0 else r(rng.uniform(40, 200), 1),
                    # vetting (14 Nov): one B, two C with their reasons; near_source_arcsec a warning only
                    "grade": "B" if i == 0 else "C", "reasons": [[], ["hot_pixel", "edge"], ["2mass"]][i],
                    "near_source_arcsec": [8.4, None, 4.1][i]})
    write_json("suspects.json", {"synthetic": True, "note": NOTE, "generated": "2026-11-14T09:30:00Z",
                                 "data_release": RELEASE, "zone": "hunt zone (synthetic)",
                                 "vetoes": ["mpc", "gaia", "catwise"], "provisional": False,
                                 "grades": {"A": 0, "B": 1, "C": 2},
                                 "gates": {"not_vetted": 0, "hot_pixel": 1, "bad_column": 0, "edge": 1, "masked_peak": 0,
                                           "sharp_outlier": 0, "very_sharp": 0, "flux_ratio": 0, "red_photo": 0, "2mass": 1,
                                           "allwise": 0},
                                 "suspects": out})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mids, _ = months_barnard()
    object_barnard(mids)
    object_moved_away(mids)
    hours_comet()
    resolve()
    validation()
    suspects()
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print(p.relative_to(ROOT), p.stat().st_size)


if __name__ == "__main__":
    main()
