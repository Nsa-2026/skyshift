"""Small boxes cut from SPHEREx Level 2 photos, reference stacks, and "is there a source here?".

Shared by v1_targets.py and v4_asteroids.py. Boxes are read with HTTP range reads from NASA's
public S3 copy (no IRSA server work, ~5 s and ~0.1 MB per 81 x 81 px box from Europe) and cached
under .cache/boxes/ as small FITS files: IMAGE, FLAGS, VARIANCE with CRPIX shifted to the box, as
in scripts/science/cutouts.py (fetch_s3_region), which this follows.

A reference stack is the median of other photos of the same sky in the same detector, each
resampled onto the science box's pixel grid through the two WCSs (bilinear; flags nearest).
"""

from __future__ import annotations

import hashlib
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from scipy.ndimage import label, map_coordinates

from skyshift_core import background, bad_pixel_mask, build_mask, detect, is_saturated_core, saturated_mask
from skyshift_core.detect import fit_centroid

from _common import CACHE

S3_HTTPS = "https://nasa-irsa-spherex.s3.amazonaws.com/"
_FSSPEC = {"block_size": 64 * 1024, "cache_type": "readahead"}
BOX_DIR = CACHE / "boxes"


def box_path(s3_key: str, ra: float, dec: float, half_px: int) -> Path:
    h = hashlib.sha1(f"{s3_key}|{ra:.6f}|{dec:.6f}|{half_px}".encode()).hexdigest()[:20]
    return BOX_DIR / f"{h}.fits"


def fetch_box(s3_key: str, ra: float, dec: float, half_px: int, retries: int = 3) -> Path:
    """(2 half_px + 1)^2 box around (ra, dec) from the S3 copy, cached. Raises if off the photo."""
    path = box_path(s3_key, ra, dec, half_px)
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with fits.open(S3_HTTPS + s3_key, use_fsspec=True, fsspec_kwargs=_FSSPEC,
                           lazy_load_hdus=True, memmap=False) as h:
                hdr = h["IMAGE"].header.copy()
                w = _wcs(hdr)
                x, y = w.all_world2pix(ra, dec, 0)
                xc, yc = int(round(float(x))), int(round(float(y)))
                ny, nx = int(hdr["NAXIS2"]), int(hdr["NAXIS1"])
                x0, x1 = max(xc - half_px, 0), min(xc + half_px + 1, nx)
                y0, y1 = max(yc - half_px, 0), min(yc + half_px + 1, ny)
                if x1 - x0 < half_px or y1 - y0 < half_px:
                    raise OffPhoto(f"({ra:.5f}, {dec:.5f}) is off or at the edge of {s3_key}")
                img = np.asarray(h["IMAGE"].section[y0:y1, x0:x1], dtype=np.float32)
                flg = np.asarray(h["FLAGS"].section[y0:y1, x0:x1]).astype(np.int32)
                var = np.asarray(h["VARIANCE"].section[y0:y1, x0:x1], dtype=np.float32)
            for k, d in (("CRPIX1", x0), ("CRPIX2", y0), ("CRPIX1W", x0), ("CRPIX2W", y0)):
                if k in hdr:
                    hdr[k] = float(hdr[k]) - d
            hdr["CUTX0"], hdr["CUTY0"] = x0, y0
            out = fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(img, header=hdr, name="IMAGE"),
                                fits.ImageHDU(flg, name="FLAGS"), fits.ImageHDU(var, name="VARIANCE")])
            tmp = path.with_suffix(".part")
            out.writeto(tmp, overwrite=True)
            tmp.replace(path)
            return path
        except OffPhoto:
            raise
        except Exception as e:  # noqa: BLE001 - retry any transient network failure
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"S3 box failed: {s3_key}: {last}")


class OffPhoto(ValueError):
    pass


def fetch_boxes(jobs: list[tuple[str, float, float, int]], workers: int = 12, label: str = "boxes"
                ) -> dict[tuple[str, float, float, int], Path | Exception]:
    """Fetch many boxes, `workers` in flight. Returns job -> path or the exception."""
    out: dict[tuple[str, float, float, int], Path | Exception] = {}
    todo = []
    for j in dict.fromkeys(jobs):
        p = box_path(*j)
        if p.exists() and p.stat().st_size > 0:
            out[j] = p
        else:
            todo.append(j)
    print(f"{label}: {len(out)} cached, {len(todo)} to fetch", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_box, *j): j for j in todo}
        for i, f in enumerate(as_completed(futs), 1):
            j = futs[f]
            try:
                out[j] = f.result()
            except Exception as e:  # noqa: BLE001
                out[j] = e
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} ({time.time() - t0:.0f} s)", flush=True)
    return out


def _wcs(hdr: fits.Header) -> WCS:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return WCS(hdr).celestial


@dataclass
class Box:
    image: np.ndarray
    flags: np.ndarray
    variance: np.ndarray
    wcs: WCS
    header: fits.Header

    def to_pixel(self, ra: float, dec: float) -> tuple[float, float]:
        x, y = self.wcs.all_world2pix(ra, dec, 0)
        return float(x), float(y)


def load_box(path: Path) -> Box:
    with fits.open(path) as h:
        hdr = h["IMAGE"].header
        return Box(np.asarray(h["IMAGE"].data, dtype=np.float64), np.asarray(h["FLAGS"].data),
                   np.asarray(h["VARIANCE"].data, dtype=np.float64), _wcs(hdr), hdr.copy())


def reference_stack(target: Box, refs: list[Box], mask_any: bool = False) -> tuple[np.ndarray, np.ndarray] | None:
    """Median of the reference boxes resampled onto target's pixel grid, and its bad-pixel mask.

    The mask is where no box has a good pixel, and with mask_any=True also where any box is saturated
    (OVERFLOW/NONLINEAR: keeps a saturated core masked in the stack even when one photo saturated
    less; other bits, such as a cosmic ray in one photo, are left to the median).

    Each box is resampled bilinearly with its bad pixels left out: the good pixels and a validity
    weight (1 good, 0 bad) are interpolated separately, the value is their ratio, and a pixel is bad
    only where bad pixels carry more than half the weight. Interpolating NaNs instead turned a pixel
    bad whenever either neighbour it reads was bad, even at zero weight, so every masked hole grew by
    one pixel on one side (R Draconis visit 3 D1, one photo: masked-core centre 1.35 px from the
    prediction in its stack, 0.25 px in the photo itself).
    """
    if not refs:
        return None
    ny, nx = target.image.shape
    yy, xx = np.mgrid[:ny, :nx]
    ra, dec = target.wcs.all_pix2world(xx.ravel(), yy.ravel(), 0)
    planes, any_bad = [], np.zeros((ny, nx), dtype=bool)
    for r in refs:
        rx, ry = r.wcs.all_world2pix(ra, dec, 0)
        coords = np.vstack([ry, rx])
        badr = bad_pixel_mask(r.flags) | ~np.isfinite(r.image)
        wgt = map_coordinates((~badr).astype(np.float64), coords, order=1, mode="constant", cval=0.0)
        v = map_coordinates(np.where(badr, 0.0, r.image), coords, order=1, mode="constant", cval=0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            v = np.where(wgt >= 0.5, v / wgt, np.nan)
        planes.append(v.reshape(ny, nx))
        if mask_any:
            sat = saturated_mask(r.flags).astype(np.float32)
            b = map_coordinates(sat, coords, order=0, mode="constant", cval=0.0)
            any_bad |= b.reshape(ny, nx) > 0.5
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns at rotated corners
        med = np.nanmedian(np.stack(planes), axis=0)
    bad = ~np.isfinite(med) | any_bad
    return np.where(bad, 0.0, med), bad


@dataclass
class Hit:
    found: bool
    offset_px: float | None
    snr: float | None
    flux: float | None
    method: str  # "detect", "wings" (saturated core, centroid on the wings) or "none"


def source_near(image: np.ndarray, x: float, y: float, *, flags: np.ndarray | None = None,
                variance: np.ndarray | None = None, mask: np.ndarray | None = None,
                radius_px: float = 2.0, thresh: float = 5.0, wings: bool = False, min_snr: float = 0.0) -> Hit:
    """Is there a detection (sep, `thresh` sigma per pixel; aperture S/N >= min_snr) within radius_px of (x, y)?

    wings=True: when the core under (x, y) is saturated (OVERFLOW/NONLINEAR in `flags`) or masked
    (`mask`, e.g. a stack whose core is masked in every photo), sep's centroid of the remaining ring
    is biased by 1-2 px (Barnard's Star, D6: 1.75 px while Gaia field stars in the same box agree to
    0.03 px). Then the position is skyshift_core's Gaussian fit to the unmasked wings of a 13 x 13 px
    stamp, started at the prediction and bounded to +-2 px (a fit stopped at the bound is not a
    match), and the sep detection only has to be within radius_px + 4 of the prediction, or within
    radius_px of the masked core: around a very bright star (R Draconis in D4-D5) the masked core is
    6-9 px in radius and sep finds only its unmasked rim, 0.7-1.7 px outside it.
    """
    dets = detect(image, flags, variance=variance, mask=mask, thresh=thresh)
    if min_snr > 0 and len(dets):
        dets = dets[dets["snr"] >= min_snr]  # aperture S/N (skyshift_core.detect: 2 px aperture)
    d = np.hypot(dets["x"] - x, dets["y"] - y) if len(dets) else np.zeros(0)
    i = int(np.argmin(d)) if len(d) else -1
    m = build_mask(image, flags, mask) if wings and i >= 0 and _core_masked(image, x, y, flags, mask) else None
    if m is not None and (d[i] <= radius_px + 4.0
                          or _borders_core(m, x, y, float(dets["x"][i]), float(dets["y"][i]), radius_px)):
        sub = np.where(m, 0.0, image)
        sub = sub - background(sub, m).level
        c = fit_centroid(sub, m, x, y, half=6)
        method = "wings"
        if c is None or np.hypot(c[0] - x, c[1] - y) < 1e-3:
            # the fit never left its start (too few wing pixels around a large saturated core):
            # use the centre of the masked core itself, which is symmetric about the star
            c, method = _masked_core_centre(m, x, y), "core"
        if c is not None:
            # the fit decides: sep's centroid of the ring is the biased one
            off = float(np.hypot(c[0] - x, c[1] - y))
            if off <= 0.97 * min(radius_px, 2.0):
                return Hit(True, off, float(dets["snr"][i]), float(dets["flux"][i]), method)
            return Hit(False, off, float(dets["snr"][i]), float(dets["flux"][i]), method)
    if i >= 0 and d[i] <= radius_px:
        return Hit(True, float(d[i]), float(dets["snr"][i]), float(dets["flux"][i]), "detect")
    return Hit(False, None, None, None, "none")


def _masked_core(m: np.ndarray, x: float, y: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Pixels (px, py) of the largest connected masked region touching the 2 px disc around (x, y), >= 3 px."""
    lab, _ = label(m)
    ny, nx = m.shape
    yy, xx = np.ogrid[:ny, :nx]
    ids = np.unique(lab[((xx - x) ** 2 + (yy - y) ** 2 <= 4.0) & m])
    if ids.size == 0:
        return None
    best = max(ids, key=lambda k: int((lab == k).sum()))
    py, px = np.nonzero(lab == best)
    if px.size < 3:
        return None
    return px, py


def _masked_core_centre(m: np.ndarray, x: float, y: float) -> tuple[float, float] | None:
    """Centroid of the connected masked region touching the 2 px disc around (x, y) (>= 3 px)."""
    core = _masked_core(m, x, y)
    return None if core is None else (float(core[0].mean()), float(core[1].mean()))


def _borders_core(m: np.ndarray, x: float, y: float, dx: float, dy: float, radius_px: float) -> bool:
    """Is the detection at (dx, dy) within radius_px of a pixel of the masked core at (x, y)?"""
    core = _masked_core(m, x, y)
    return core is not None and float(np.hypot(core[0] - dx, core[1] - dy).min()) <= radius_px


def _core_masked(image: np.ndarray, x: float, y: float, flags: np.ndarray | None, mask: np.ndarray | None) -> bool:
    if flags is not None and is_saturated_core(flags, x, y, r=2.0):
        return True
    if mask is not None:
        ny, nx = image.shape
        yy, xx = np.ogrid[:ny, :nx]
        return bool(np.asarray(mask)[(xx - x) ** 2 + (yy - y) ** 2 <= 4.0].any())
    return False


def angsep_arcsec(ra1, dec1, ra2, dec2):
    """Great-circle separation in arcsec (haversine), arrays allowed."""
    r1, d1, r2, d2 = map(np.radians, (ra1, dec1, ra2, dec2))
    s = np.sin((d2 - d1) / 2) ** 2 + np.cos(d1) * np.cos(d2) * np.sin((r2 - r1) / 2) ** 2
    return np.degrees(2 * np.arcsin(np.sqrt(np.clip(s, 0, 1)))) * 3600.0


def offset_position(ra: float, dec: float, dist_arcsec: float, pa_deg: float) -> tuple[float, float]:
    """Position dist_arcsec away from (ra, dec) at position angle pa_deg (east of north)."""
    r, d = np.radians(ra), np.radians(dec)
    b, t = np.radians(dist_arcsec / 3600.0), np.radians(pa_deg)
    d2 = np.arcsin(np.sin(d) * np.cos(b) + np.cos(d) * np.sin(b) * np.cos(t))
    r2 = r + np.arctan2(np.sin(t) * np.sin(b) * np.cos(d), np.cos(b) - np.sin(d) * np.sin(d2))
    return float(np.degrees(r2) % 360.0), float(np.degrees(d2))
