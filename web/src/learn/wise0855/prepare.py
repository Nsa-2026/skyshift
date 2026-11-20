"""Build the Learn cutouts from public HST/MAST program 13802.

Run with numpy, scipy and astropy: python prepare.py <directory of the three
*_drz.fits files>. The MAST product IDs and source hashes are in observations.json.
"""
from pathlib import Path
import hashlib
import json
import sys

from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates

# Per-date reference astrometry, Zapatero Osorio et al. 2016, Table 1.
PRODUCTS = [
    ("icit01010", "08h55m08.433s -07d14m39.49s", (0., 0.)),
    ("icit02010", "08h55m08.248s -07d14m39.29s", (-.215, .314)),
    ("icit04010", "08h55m08.163s -07d14m39.34s", (-.094, .240)),
]
SIZE = 192
SCALE = .1285

def prepare(source, output):
    # Register to one fixed sky grid. The small x/y corrections above were
    # estimated on stationary field sources, excluding the target's trail.
    grid = WCS(naxis=2)
    grid.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    grid.wcs.radesys = "ICRS"
    grid.wcs.crval = [133.785166666667, -7.243847222222]
    grid.wcs.cdelt = [-SCALE / 3600, SCALE / 3600]
    grid.wcs.crpix = [(SIZE + 1) / 2] * 2
    y, x = np.indices((SIZE, SIZE), dtype=float)
    images, observations, seeds = [], [], []
    for root, position, (dx, dy) in PRODUCTS:
        filename = f"{root}_drz.fits"
        path = source / filename
        with fits.open(path) as hdus:
            sci = hdus["SCI"]
            header = hdus[0].header
            if header["ROOTNAME"].lower() != root or header["PROPOSID"] != 13802 \
                or header["FILTER"] != "F110W" or sci.header["BUNIT"] != "ELECTRONS/S":
                raise ValueError(f"Unexpected observation or units in {filename}")
            sky = grid.pixel_to_world(x - dx, SIZE - 1 - (y - dy))
            sx, sy = WCS(sci.header).world_to_pixel(sky)
            samples = map_coordinates(sci.data, (sy, sx), order=1, cval=np.nan)
            weight = map_coordinates(hdus["WHT"].data, (sy, sx), order=1, cval=0)
            samples[weight <= 0] = np.nan
            samples -= np.nanmedian(samples)
            if not np.isfinite(samples).all():
                raise ValueError(f"The teaching cutout extends past usable data in {filename}")
            images.append(samples)
            px, py = grid.world_to_pixel(SkyCoord(position))
            seeds.append((float(px) + dx, SIZE - 1 - float(py) + dy))
            observations.append(dict(
                filename=filename, date=header["DATE-OBS"],
                startMjd=header["EXPSTART"], endMjd=header["EXPEND"],
                exposureSeconds=round(header["EXPTIME"], 2),
                calibration=header["CAL_VER"],
                sourceSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            ))

    # Use the published position as the search seed. Background stars subtract
    # out; a local residual centroid resolves the neighbouring source on Apr 11.
    for index, (px, py) in enumerate(seeds):
        others = [image for j, image in enumerate(images) if index != j]
        residual = gaussian_filter(images[index] - np.mean(others, axis=0), .65)
        nearby = np.hypot(x - px, y - py) <= 3
        cy, cx = np.unravel_index(np.argmax(np.where(nearby, residual, -np.inf)), residual.shape)
        weights = np.where(np.hypot(x - cx, y - cy) <= 1.5, np.maximum(residual, 0), 0)
        observations[index]["mark"] = dict(
            x=round(float(np.sum(x * weights) / np.sum(weights)) + .5, 3),
            y=round(float(np.sum(y * weights) / np.sum(weights)) + .5, 3),
        )

    np.asarray(images, dtype="<f4").tofile(output / "f110w.f32")
    metadata = dict(program=13802, width=SIZE, height=SIZE, arcsecPerPixel=SCALE,
                    centreDegrees=list(grid.wcs.crval), observations=observations)
    (output / "observations.json").write_text(json.dumps(metadata, indent=2) + "\n")

if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(__file__).parent)
