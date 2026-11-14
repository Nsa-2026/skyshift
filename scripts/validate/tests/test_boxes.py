"""Offline tests for _boxes: geometry, reference stacks, and finding a source (incl. saturated cores)."""

import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _boxes import Box, angsep_arcsec, offset_position, reference_stack, source_near  # noqa: E402
from skyshift_core import MASK_BITS, SAT_BITS, detect  # noqa: E402

PIX_DEG = 6.15 / 3600


def _wcs(ra=150.0, dec=2.0, n=41, rot_deg=0.0) -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    c, s = np.cos(np.radians(rot_deg)), np.sin(np.radians(rot_deg))
    w.wcs.cd = PIX_DEG * np.array([[-c, s], [s, c]])
    return w


def _star(n, x, y, amp, sigma=1.3, noise=0.01, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:n, :n]
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2)) + rng.normal(0, noise, (n, n))


def _box(img, w, flags=None):
    n = img.shape[0]
    return Box(img, np.zeros((n, n), np.int32) if flags is None else flags, np.full((n, n), 1e-4), w, fits.Header())


def test_offset_position_round_trip():
    for pa in (0, 45, 90, 200):
        ra, dec = offset_position(10.0, -30.0, 180.0, pa)
        assert abs(angsep_arcsec(10.0, -30.0, ra, dec) - 180.0) < 1e-6


def test_source_near_finds_star_and_not_empty_spot():
    w = _wcs()
    b = _box(_star(41, 20.3, 19.6, 1.0), w)
    hit = source_near(b.image, 20.0, 20.0, flags=b.flags, variance=b.variance)
    assert hit.found and hit.offset_px < 0.6
    miss = source_near(b.image, 8.0, 30.0, flags=b.flags, variance=b.variance)
    assert not miss.found


def test_reference_stack_resamples_rotated_photo():
    n = 41
    ra0, dec0 = 150.0, 2.0
    w0, w1 = _wcs(ra0, dec0, n), _wcs(ra0, dec0, n, rot_deg=30.0)
    # same sky star, 3 px east of centre, drawn in each photo's own pixels
    sra, sdec = offset_position(ra0, dec0, 3 * 6.15, 90.0)
    x0, y0 = (float(v) for v in w0.all_world2pix(sra, sdec, 0))
    x1, y1 = (float(v) for v in w1.all_world2pix(sra, sdec, 0))
    t = _box(_star(n, x0, y0, 1.0, seed=1), w0)
    refs = [_box(_star(n, x1, y1, 1.0, seed=s), w1) for s in (2, 3, 4)]
    stack, bad = reference_stack(t, refs)
    assert not bad[int(round(y0)), int(round(x0))]
    iy, ix = np.unravel_index(np.argmax(np.where(bad, -np.inf, stack)), stack.shape)
    assert abs(ix - x0) <= 1 and abs(iy - y0) <= 1


def test_saturated_core_uses_wings_not_the_ring():
    sat_bit = SAT_BITS & -SAT_BITS  # lowest saturation bit
    n, x, y = 41, 20.4, 19.7
    img = _star(n, x, y, 50.0, sigma=1.6, noise=0.02)
    flags = np.zeros((n, n), np.int32)
    yy, xx = np.mgrid[:n, :n]
    # an asymmetric saturated core: masked disc plus one extra masked pixel on one side
    core = (xx - x) ** 2 + (yy - y) ** 2 <= 2.2**2
    core[int(round(y)), int(round(x)) + 3] = True
    flags[core] = sat_bit
    hit = source_near(img, x, y, flags=flags, variance=np.full((n, n), 1e-4), wings=True)
    assert hit.found and hit.method in ("wings", "core")
    assert hit.offset_px < 0.5


def _bright_star_with_big_hole(n=41, x=20.3, y=19.6, hole_r=8.0):
    """R Draconis in D4: a halo bright out to ~17 px and a masked core 8 px in radius (OVERFLOW in
    the middle, other mask bits such as TRANSIENT/SUR_ERROR around it), so sep sees only the rim."""
    rng = np.random.default_rng(5)
    yy, xx = np.mgrid[:n, :n]
    r = np.hypot(xx - x, yy - y)
    img = 5000.0 / (1 + (r / 1.5) ** 2) ** 1.5 + rng.normal(0, 0.02, (n, n))
    other = MASK_BITS & ~SAT_BITS
    flags = np.zeros((n, n), np.int32)
    flags[r <= hole_r] = other & -other
    flags[r <= 3.0] = SAT_BITS & -SAT_BITS
    return img, flags


def test_very_bright_star_rim_beyond_6px_uses_masked_core():
    n, x, y = 41, 20.3, 19.6
    img, flags = _bright_star_with_big_hole(n, x, y)
    var = np.full((n, n), 1e-4)
    dets = detect(img, flags, variance=var, thresh=2.5)
    nearest = float(np.hypot(dets["x"] - x, dets["y"] - y).min())
    assert nearest > 6.0  # beyond the old radius_px + 4 gate: only the rim is detected
    hit = source_near(img, x, y, flags=flags, variance=var, thresh=2.5, min_snr=5.0, wings=True)
    assert hit.found and hit.method == "core" and hit.offset_px < 0.3


def test_big_masked_core_off_the_prediction_is_not_a_match():
    n, x, y = 41, 20.3, 19.6
    img, flags = _bright_star_with_big_hole(n, x, y)
    # predicted 3 px from the star, still inside its masked core: the core's centre is 3 px away
    hit = source_near(img, x + 3.0, y, flags=flags, variance=np.full((n, n), 1e-4), thresh=2.5, min_snr=5.0, wings=True)
    assert not hit.found and hit.offset_px > 2.5


def test_small_masked_blob_does_not_borrow_a_distant_star():
    n = 41
    img = _star(n, 29.0, 20.0, 1.0)  # an ordinary star 9 px from the prediction
    flags = np.zeros((n, n), np.int32)
    flags[19:22, 19:22] = SAT_BITS & -SAT_BITS  # a small saturated blob under the prediction
    hit = source_near(img, 20.0, 20.0, flags=flags, variance=np.full((n, n), 1e-4), thresh=2.5, wings=True)
    assert not hit.found


def _box_with_hole(w, n=41):
    """Flat sky with a 5 x 5 px masked hole (a mask bit, not saturation) at columns and rows 18-22."""
    other = MASK_BITS & ~SAT_BITS
    flags = np.zeros((n, n), np.int32)
    flags[18:23, 18:23] = other & -other
    img = 1.0 + np.random.default_rng(7).normal(0, 0.01, (n, n))
    return _box(img, w, flags)


def test_reference_stack_hole_does_not_grow_on_its_own_grid():
    t = _box_with_hole(_wcs())
    stack, bad = reference_stack(t, [t])
    inner = bad[5:-5, 5:-5]  # the box border can fall a hair outside the photo (round-trip residuals)
    assert inner.sum() == 25 and bad[18:23, 18:23].all()  # interpolating NaNs grew it to 30-36
    good = ~bad
    assert np.allclose(stack[good], t.image[good], atol=1e-6)


def test_reference_stack_hole_keeps_its_size_after_a_subpixel_shift():
    n = 41
    t = _box(np.ones((n, n)), _wcs())
    w1 = _wcs()
    w1.wcs.crpix = w1.wcs.crpix + np.array([0.3, 0.0])  # same sky lands 0.3 px further right in this photo
    r = _box_with_hole(w1)
    stack, bad = reference_stack(t, [r])
    # the hole is at x = 17.7-21.7 in the target's pixels: 5 columns, not 6
    inner = np.zeros_like(bad)
    inner[5:-5, 5:-5] = bad[5:-5, 5:-5]  # the box border can fall outside the shifted photo
    assert inner.sum() == 25
    cols = np.nonzero(inner.any(axis=0))[0]
    assert abs(cols.mean() - 19.7) <= 0.5
