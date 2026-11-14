"""NOTE(handoff): needs the astropy-healpix package; without it summary.reference = {error} and the spectrum has no DR1 line.

The DR1 all-sky spectral cube (HiPS order 5, 102 wavelength planes) as a reference sky.

Each HiPS tile is a 512x512x102 float32 cube on S3; one wavelength plane is a contiguous
1 MB block, so a small patch at all 102 wavelengths costs ~102 small range reads.
Uses:
  * reference spectrum of any pixel at 102 wavelengths (spectra on the fly, coadded)
  * reference image at any frame's own wavelength -> change map for any newer frame,
    without needing a second epoch observed at the same wavelength
  * new data releases = change CUBE_PREFIX (date-stamped folder)
"""
import os
import numpy as np
from astropy.wcs import WCS
from astropy.io import fits
from scipy.ndimage import map_coordinates
import concurrent.futures as cf

from . import s3
from .fitsrange import RemoteFITS

CUBE_PREFIX = os.environ.get("SPXTM_CUBE", "dr1/hips5_cube/hips_20261026/")
ORDER = 5


def tile_key(npix):
    return f"{CUBE_PREFIX}Norder{ORDER}/Dir{(npix // 10000) * 10000}/Npix{npix}_cube.fits"


def tiles_for(ra, dec, radius_deg):
    from astropy_healpix import HEALPix
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    hp = HEALPix(nside=2 ** ORDER, order="nested", frame="icrs")
    c = SkyCoord(ra * u.deg, dec * u.deg)
    return sorted(set(hp.cone_search_skycoord(c, radius=radius_deg * u.deg).tolist()) | {int(hp.skycoord_to_healpix(c))})


def _celestial_wcs(hdr):
    h = hdr.copy()
    for k in list(h.keys()):
        if k.endswith("3") and k[:-1] in ("CTYPE", "CUNIT", "CRPIX", "CRVAL", "CDELT", "CNAME", "PS3_", "PV3_") or k.startswith("PS3_") or k.startswith("PV3_"):
            del h[k]
    h["WCSAXES"] = 2; h["NAXIS"] = 2
    for k in ("PC1_3", "PC2_3", "PC3_1", "PC3_2", "PC3_3", "CNAME3"):
        if k in h:
            del h[k]
    return WCS(h, naxis=2)


def reference_cube(ra, dec, out_wcs, npix, planes=None, workers=16):
    """(102, npix, npix) reference images on our grid + wavelengths + bandwidths."""
    radius = npix * 6.15 / 3600 * 0.75 + 0.05
    yy, xx = np.mgrid[0:npix, 0:npix]
    sky = out_wcs.all_pix2world(np.c_[xx.ravel(), yy.ravel()], 0)
    cube = None
    lam = bw = None
    for tile in tiles_for(ra, dec, radius):
        rf = RemoteFITS(tile_key(tile))
        try:
            rf.scan()
        except Exception:
            continue
        h = rf.header("IMAGE")
        w = _celestial_wcs(h)
        pin = w.all_world2pix(sky, 0)
        px, py = pin[:, 0].reshape(npix, npix), pin[:, 1].reshape(npix, npix)
        inside = (px > -1) & (px < 512) & (py > -1) & (py < 512)
        if not inside.any():
            continue
        if lam is None:
            ww = rf.read_table_hdu("WCS-WAVE").data
            lam = np.asarray(ww["WAVELENGTHS"][0]).ravel(); bw = np.asarray(ww["BANDWIDTHS"][0]).ravel()
            cube = np.full((len(lam), npix, npix), np.nan, np.float32)
        y0 = max(0, int(np.floor(py[inside].min())) - 2); y1 = min(512, int(np.ceil(py[inside].max())) + 3)
        hd = rf.hdu("IMAGE")
        nz = h["NAXIS3"]
        ps = planes if planes is not None else range(nz)

        def plane(z):
            a = hd["data_start"] + (z * 512 * 512 + y0 * 512) * 4
            b = hd["data_start"] + (z * 512 * 512 + y1 * 512) * 4
            arr = np.frombuffer(s3.get_range(rf.key, a, b), ">f4").reshape(y1 - y0, 512).astype(np.float32)
            return z, map_coordinates(arr, [py - y0, px], order=1, mode="constant", cval=np.nan)
        with cf.ThreadPoolExecutor(workers) as ex:
            for z, img in ex.map(plane, ps):
                m = inside & np.isfinite(img)
                cube[z][m] = img[m]
    return cube, lam, bw


def reference_at(cube, lam, lam_t, bw_t=None):
    """Reference image at an arbitrary wavelength (linear interpolation between planes)."""
    order = np.argsort(lam)
    L, C = lam[order], cube[order]
    good = np.array([np.isfinite(c).any() for c in C])
    L, C = L[good], C[good]
    if len(L) == 0:
        return None
    i = np.searchsorted(L, lam_t)
    if i <= 0:
        return C[0]
    if i >= len(L):
        return C[-1]
    f = (lam_t - L[i - 1]) / (L[i] - L[i - 1])
    return (1 - f) * C[i - 1] + f * C[i]


def reference_spectrum(cube, lam, bw, x, y, r=1.5):
    """Mean surface brightness in a small aperture vs wavelength (MJy/sr)."""
    n = cube.shape[1]
    yy, xx = np.mgrid[0:n, 0:n]
    ap = np.hypot(xx - x, yy - y) <= r
    out = []
    for z in np.argsort(lam):
        v = cube[z][ap]
        v = v[np.isfinite(v)]
        if v.size:
            out.append(dict(lam=float(lam[z]), bw=float(bw[z]), sb=float(v.mean())))
    return out
