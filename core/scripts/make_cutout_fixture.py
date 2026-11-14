"""Make the small real test fixture in fixtures/fits/ from one SPHEREx Level 2 file.

    cd core && uv run python scripts/make_cutout_fixture.py [SRC.fits] [OUT_DIR]
    cd core && uv run python scripts/make_cutout_fixture.py --gaia   # only refresh the Gaia list

SRC defaults to the first photo, downloaded outside the repo (read-only, never committed):
    ~/skyshift-data/first-photo/level2_2025W18_1B_0074_2D1_spx_l2b-v27-2026-196.fits
from s3://nasa-irsa-spherex/dr1/level2/2025W18_1B/l2b-v27-2026-196/1/ (public).

It finds a 101 x 101 px box holding several bright, isolated, unsaturated stars, and writes:
- cutout_2025W18_1B_0074_2D1.fits: PRIMARY, IMAGE, FLAGS (tile-compressed, as in the original),
  VARIANCE and WCS-WAVE, with the headers of the original. The celestial WCS (TAN-SIP) is shifted
  by moving CRPIX1/2; the WCS-WAVE table is kept whole and CRPIX1W/2W are shifted the same way;
  the 'A' WCS (0-based pixel of the original frame) gets CRVAL1A/2A = x0, y0.
- cutout_2025W18_1B_0074_2D1.stars.json: the stars as found on the *full* frame (x, y in cutout
  pixels, ra, dec, flux), used by the tests as an independent reference.
- cutout_2025W18_1B_0074_2D1.gaia.json (--gaia, needs internet): Gaia DR3 stars with G < 16 on
  the cutout, from ESA's Gaia TAP (gea.esac.esa.int), epoch J2016.0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits

from skyshift_core import detect, flags_under, open_level2, pixel_to_sky
from skyshift_core.flags import MASK_BITS

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
DEFAULT_SRC = Path.home() / "skyshift-data/first-photo/level2_2025W18_1B_0074_2D1_spx_l2b-v27-2026-196.fits"
SIZE = 101
EDGE = 120  # stay away from the detector edges
STEM = "cutout_2025W18_1B_0074_2D1"


def pick_box(stars: np.ndarray, all_src: np.ndarray, shape: tuple[int, int]) -> tuple[int, int, np.ndarray]:
    """Box (x0, y0) holding the most good stars, ties broken by summed SNR."""
    best: tuple[int, float, int, int] | None = None
    ny, nx = shape
    step = 10
    for y0 in range(EDGE, ny - EDGE - SIZE, step):
        for x0 in range(EDGE, nx - EDGE - SIZE, step):
            # good stars at least 8 px inside the box
            inside = (stars["x"] >= x0 + 8) & (stars["x"] < x0 + SIZE - 8) & (stars["y"] >= y0 + 8) & (stars["y"] < y0 + SIZE - 8)
            n = int(inside.sum())
            if n < 3:
                continue
            # avoid boxes with very bright (likely saturated) sources anywhere
            in_all = (all_src["x"] >= x0) & (all_src["x"] < x0 + SIZE) & (all_src["y"] >= y0) & (all_src["y"] < y0 + SIZE)
            if np.any(all_src["peak"][in_all] > 1000):
                continue
            score = (n, float(stars["snr"][inside].sum()), x0, y0)
            if best is None or score[:2] > best[:2]:
                best = score
    if best is None:
        raise SystemExit("no box with 3 good stars")
    _, _, x0, y0 = best
    inside = (stars["x"] >= x0) & (stars["x"] < x0 + SIZE) & (stars["y"] >= y0) & (stars["y"] < y0 + SIZE)
    return x0, y0, stars[inside]


def fetch_gaia(out_dir: Path) -> None:
    """Gaia DR3 (G < 16) over the cutout footprint, via ESA's TAP sync endpoint."""
    import csv
    import io
    import urllib.parse
    import urllib.request

    from skyshift_core import sky_to_pixel

    l2 = open_level2(out_dir / f"{STEM}.fits")
    ny, nx = l2.data.shape
    ra_c, dec_c = pixel_to_sky(l2.wcs, (nx - 1) / 2, (ny - 1) / 2)
    query = ("SELECT source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag FROM gaiadr3.gaia_source "
             f"WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {float(ra_c):.6f}, {float(dec_c):.6f}, 0.15)) "
             "AND phot_g_mean_mag < 16")
    body = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}).encode()
    req = urllib.request.Request("https://gea.esac.esa.int/tap-server/tap/sync", data=body,
                                 headers={"User-Agent": "skyshift-fixture-script"})
    with urllib.request.urlopen(req, timeout=120) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode())))

    def num(v: str) -> float | None:
        return None if v in ("", "null") else float(v)

    stars = []
    for row in rows:
        x, y = sky_to_pixel(l2.wcs, float(row["ra"]), float(row["dec"]))
        if -2 <= x <= nx + 1 and -2 <= y <= ny + 1:
            stars.append({"id": row["source_id"], "ra": float(row["ra"]), "dec": float(row["dec"]),
                          "pmra": num(row["pmra"]), "pmdec": num(row["pmdec"]),
                          "parallax": num(row["parallax"]), "g": round(float(row["phot_g_mean_mag"]), 4)})
    meta = {"note": "Gaia DR3 (ESA/Gaia/DPAC) stars with G < 16 on the cutout, epoch J2016.0, "
                    "from gea.esac.esa.int TAP. Shape as contracts/api.md frames.gaia[].",
            "gaia": sorted(stars, key=lambda s: s["g"])}
    out = out_dir / f"{STEM}.gaia.json"
    out.write_text(json.dumps(meta, indent=1) + "\n")
    print(f"wrote {out}: {len(stars)} Gaia stars")


def main() -> None:
    if "--gaia" in sys.argv:
        fetch_gaia(REPO / "fixtures" / "fits")
        return
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "fixtures" / "fits"
    out_dir.mkdir(parents=True, exist_ok=True)

    full = open_level2(src, variance=True)
    print(f"read {src.name}: {full.data.shape} D{full.detector} MJD-AVG {full.mjd_avg:.6f} {full.obsid}")

    src_all = detect(full.data, full.flags, variance=full.variance, thresh=5.0, box=64)
    # good stars: bright, clean (no mask or saturation bit within 2 px), isolated (no source
    # brighter than 5% of it within 6 px)
    good = []
    for i, s in enumerate(src_all):
        if s["snr"] < 50 or s["sep_flag"] != 0:
            continue
        if flags_under(full.flags, s["x"], s["y"], 2.0) & MASK_BITS:
            continue
        d = np.hypot(src_all["x"] - s["x"], src_all["y"] - s["y"])
        near = (d > 0) & (d < 6.0) & (src_all["flux"] > 0.05 * s["flux"])
        if near.any():
            continue
        good.append(i)
    stars = src_all[good]
    print(f"{len(src_all)} sources, {len(stars)} bright isolated clean stars")

    x0, y0, chosen = pick_box(stars, src_all, full.data.shape)
    print(f"box x0={x0} y0={y0}: {len(chosen)} stars")

    region = (x0, x0 + SIZE, y0, y0 + SIZE)
    with fits.open(src, memmap=False) as hdul:
        h_img = hdul["IMAGE"].header.copy()
        h_flg = hdul["FLAGS"].header.copy()
        h_var = hdul["VARIANCE"].header.copy()
        wave = hdul["WCS-WAVE"]
        wave_hdu = fits.BinTableHDU(data=wave.data.copy(), header=wave.header.copy(), name="WCS-WAVE")

    def shift_header(h: fits.Header) -> fits.Header:
        for ax, off in ((1, x0), (2, y0)):
            if f"CRPIX{ax}" in h:
                h[f"CRPIX{ax}"] = float(h[f"CRPIX{ax}"]) - off
            if f"CRPIX{ax}W" in h:
                h[f"CRPIX{ax}W"] = float(h[f"CRPIX{ax}W"]) - off
            if f"CRVAL{ax}A" in h:
                h[f"CRVAL{ax}A"] = float(h[f"CRVAL{ax}A"]) + off
        h["CUTX0"] = (x0, "SkyShift fixture: 0-based x of the cutout in the original")
        h["CUTY0"] = (y0, "SkyShift fixture: 0-based y of the cutout in the original")
        h["CUTSRC"] = (src.name[:60], "SkyShift fixture: source file")
        h.add_history("SkyShift test fixture: 101x101 cutout made by core/scripts/make_cutout_fixture.py")
        return h

    sl = np.s_[y0 : y0 + SIZE, x0 : x0 + SIZE]
    img = fits.ImageHDU(full.data[sl].astype(np.float32), header=shift_header(h_img), name="IMAGE")
    flg = fits.CompImageHDU(full.flags[sl].astype(np.int32), header=shift_header(_strip_comp(h_flg)), name="FLAGS",
                            compression_type="RICE_1")
    assert full.variance is not None
    var = fits.ImageHDU(full.variance[sl].astype(np.float32), header=shift_header(h_var), name="VARIANCE")
    out = out_dir / f"{STEM}.fits"
    fits.HDUList([fits.PrimaryHDU(), img, flg, var, wave_hdu]).writeto(out, overwrite=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")

    ra, dec = pixel_to_sky(full.wcs, chosen["x"], chosen["y"])
    rows = [
        {"x": round(float(s["x"]) - x0, 4), "y": round(float(s["y"]) - y0, 4),
         "ra": round(float(r), 7), "dec": round(float(d), 7),
         "flux_mjysr": round(float(s["flux"]), 3), "snr": round(float(s["snr"]), 1)}
        for s, r, d in zip(chosen, ra, dec)
    ]
    meta = {
        "note": "Stars detected on the FULL frame by skyshift_core.detect (thresh 5, box 64), "
                "x/y converted to 0-based cutout pixels. Reference for core/tests.",
        "source": src.name, "x0": x0, "y0": y0, "size": SIZE,
        "mjd_avg": full.mjd_avg, "detector": full.detector, "obsid": full.obsid,
        "stars": rows,
    }
    js = out_dir / f"{STEM}.stars.json"
    js.write_text(json.dumps(meta, indent=1) + "\n")
    print(f"wrote {js}")
    for r in rows:
        print(r)


def _strip_comp(h: fits.Header) -> fits.Header:
    """Drop structural keys so astropy can rebuild a compressed HDU header."""
    for k in ("XTENSION", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "PCOUNT", "GCOUNT"):
        h.remove(k, ignore_missing=True)
    return h


if __name__ == "__main__":
    main()
