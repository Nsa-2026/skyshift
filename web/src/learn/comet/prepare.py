"""Build the Learn comet cutouts from the three calibrated TESS FFIs and ephemeris.

Run with numpy, scipy and astropy: python prepare.py <directory> <TSSC ephemeris CSV>.
Use the MAST products in observations.json and the TSSC sector 1751 ephemeris
3iatlas_ephem_sector1751_20260326.csv from tessgi/tess-3i.
"""
from csv import DictReader
from pathlib import Path
import hashlib
import json
import sys

from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates

FILES = [
    "tess2026015072917-s1751-2-3-0301-s_ffic.fits",
    "tess2026015145917-s1751-2-3-0301-s_ffic.fits",
    "tess2026015225917-s1751-2-3-0301-s_ffic.fits",
]
SIZE = 200
SCALE = 21.

def prepare(source, ephemeris, output):
    with ephemeris.open() as csv:
        records = list(DictReader(csv))
    time, ra, dec = (np.array([float(row[key]) for row in records]) for key in ("time", "ra", "dec"))
    grid = WCS(naxis=2)
    grid.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    grid.wcs.radesys = "ICRS"
    grid.wcs.crval = [130.189624, 16.082719]
    grid.wcs.cdelt = [-SCALE / 3600, SCALE / 3600]
    grid.wcs.crpix = [(SIZE + 1) / 2] * 2
    y, x = np.indices((SIZE, SIZE), dtype=float)
    sky = grid.pixel_to_world(x, SIZE - 1 - y)
    images, records, seeds = [], [], []

    for filename in FILES:
        path = source / filename
        with fits.open(path) as hdus:
            sci = hdus[1]
            header = hdus[0].header
            if (sci.header["CAMERA"], sci.header["CCD"], sci.header["DQUALITY"]) != (2, 3, 0) \
                or sci.header["BUNIT"].lower() != "electrons/s":
                raise ValueError(f"Unexpected detector, quality flags or units in {filename}")
            ux, uy = WCS(sci.header, naxis=2).world_to_pixel(sky)
            image = map_coordinates(sci.data, (uy, ux), order=1, cval=np.nan)
            if not np.isfinite(image).all():
                raise ValueError(f"Cutout extends outside usable pixels in {filename}")
            images.append(image - np.median(image))
            start = Time(sci.header["DATE-OBS"], scale="utc")
            end = Time(sci.header["DATE-END"], scale="utc")
            mid = start + (end - start) / 2
            epoch = mid.tdb.jd - 2457000
            if not time[0] <= epoch <= time[-1]:
                raise ValueError(f"Ephemeris does not cover {filename}")
            coord = [np.interp(epoch, time, axis) for axis in (ra, dec)]
            px, py = grid.all_world2pix([coord], 0)[0]
            seeds.append((px, SIZE - 1 - py))
            records.append(dict(
                filename=filename, startMjd=start.mjd, endMjd=end.mjd,
                exposureSeconds=round((end - start).sec, 2),
                calibration=header.get("PROCVER") or sci.header["PROCVER"],
                sourceSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            ))

    # Subtract the other frames locally before finding the core, so a bright
    # background star near the third position does not become the answer.
    for i, (px, py) in enumerate(seeds):
        background = np.mean([image for j, image in enumerate(images) if j != i], axis=0)
        residual = gaussian_filter(images[i] - background, .65)
        nearby = np.hypot(x - px, y - py) <= 2.5
        cy, cx = np.unravel_index(np.argmax(np.where(nearby, residual, -np.inf)), residual.shape)
        weights = np.where(np.hypot(x - cx, y - cy) <= 1.5, np.maximum(residual, 0), 0)
        total = np.sum(weights)
        if total <= 0:
            raise ValueError(f"Comet is not detected near the ephemeris in {FILES[i]}")
        records[i]["mark"] = dict(
            x=round(float(np.sum(x * weights) / total) + .5, 3),
            y=round(float(np.sum(y * weights) / total) + .5, 3),
        )

    np.asarray(images, dtype="<f4").tofile(output / "frames.f32")
    metadata = dict(sector=1751, width=SIZE, height=SIZE, arcsecPerPixel=SCALE,
                    centreDegrees=list(grid.wcs.crval), observations=records)
    (output / "observations.json").write_text(json.dumps(metadata, indent=2) + "\n")

if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]), Path(__file__).parent)
