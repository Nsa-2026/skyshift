"""Grid geometry, resampling, median stack with masks, and the 16-bit encoding."""

import json
from pathlib import Path

import numpy as np
import pytest
from skyshift_core import MASK_BITS, open_level2
from skyshift_core.flags import HOT, SOURCE

from skyshift_server import stack

from conftest import FIXTURE_CUTOUT

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("clock,fov,pix,n", [("months", 5, 2.05, 146), ("months", 15, 3.075, 293),
                                             ("hours", 20, 6.15, 195), ("hours", 60, 12.3, 293)])
def test_sampling_table_matches_the_contract(clock, fov, pix, n):
    grid, b = stack.grid_for(10.0, 20.0, clock, fov)
    final = grid.binned(b)
    assert final.n == n and final.n <= 300
    assert final.pixel_arcsec == pytest.approx(pix)
    assert abs(final.n * final.pixel_arcsec - fov * 60) < final.pixel_arcsec  # covers the field


def test_fov_not_allowed():
    with pytest.raises(ValueError):
        stack.grid_for(10.0, 20.0, "months", 20)


@pytest.mark.parametrize("dec", [4.77, -56.8, 66.0])
def test_grid_is_north_up_east_left_with_the_centre_in_the_middle(dec):
    g = stack.Grid(269.4462, dec, 2.05, 146)
    ra, de = g.sky()
    c = (g.n - 1) / 2.0
    # centre of the frame = the requested position, at pixel ((n-1)/2, (n-1)/2)
    r0, d0 = g.wcs.wcs_pix2world(c, c, 0)
    assert float(r0) == pytest.approx(g.ra, abs=1e-9) and float(d0) == pytest.approx(dec, abs=1e-9)
    mid = g.n // 2
    assert de[0, mid] > de[-1, mid]  # first row = north edge
    assert ra[mid, 0] > ra[mid, -1]  # first value of a row = east edge (RA grows to the east)
    # pixel scale on the tangent plane, both axes
    step_n = (de[mid - 1, mid] - de[mid, mid]) * 3600
    step_e = (ra[mid, mid - 1] - ra[mid, mid]) * 3600 * np.cos(np.deg2rad(dec))
    assert step_n == pytest.approx(2.05, rel=1e-4)
    assert step_e == pytest.approx(2.05, rel=1e-3)


def test_encode_round_trip_and_no_data():
    rng = np.random.default_rng(1)
    img = rng.normal(0.4, 0.02, (146, 146)).astype(np.float32)
    img[10:14, 20:25] = np.nan
    data, scale, zero = stack.encode(img)
    assert len(data) == 2 * 146 * 146
    raw = np.frombuffer(data, dtype="<u2").reshape(146, 146)
    assert (raw[10:14, 20:25] == 0).all()  # raw 0 = no data
    assert raw[np.isfinite(img)].min() >= 1  # real values use 1..65535
    back = stack.decode(data, scale, zero, (146, 146))
    assert np.isnan(back[10:14, 20:25]).all()
    ok = np.isfinite(img)
    assert np.abs(back[ok] - img[ok]).max() <= scale / 2 + 1e-7
    # row by row, first row first: the first value is the north-east corner
    assert raw[0, 0] == int.from_bytes(data[:2], "little")


def test_encode_matches_the_fixture_layout():
    """Decode a synthetic fixture frame with our decoder: same layout as scripts/make_fixtures.py."""
    meta = json.loads((REPO / "fixtures/api/frames_barnard.json").read_text())
    e = meta["epochs"][0]
    blob = (REPO / "fixtures/api" / e["data_url"]).read_bytes()
    nx, ny = meta["size"]
    img = stack.decode(blob, e["scale"], e["zero"], (ny, nx))
    assert np.nanmin(img) == pytest.approx(e["zero"] + e["scale"], rel=1e-6)
    data, scale, zero = stack.encode(img, max_step_sigma=np.inf)
    assert data == blob and scale == pytest.approx(e["scale"], rel=1e-6)


def test_encode_keeps_the_faint_sky_next_to_a_bright_star():
    rng = np.random.default_rng(2)
    img = rng.normal(0.4, 0.01, (64, 64))
    img[30, 30] = 5000.0  # a saturated star's wing
    data, scale, zero = stack.encode(img)
    assert scale <= 0.5 * 0.0115  # the step stays below the noise
    raw = np.frombuffer(data, dtype="<u2").reshape(64, 64)
    assert raw[30, 30] == 65535  # the top is clipped: a lower limit
    back = stack.decode(data, scale, zero, (64, 64))
    sky = np.ones((64, 64), bool)
    sky[30, 30] = False
    assert np.abs(back[sky] - img[sky]).max() <= scale / 2 + 1e-9


def test_encode_all_nan():
    data, scale, zero = stack.encode(np.full((4, 4), np.nan))
    assert data == b"\x00" * 32 and scale > 0


def test_median_stack_ignores_masked_pixels_and_removes_outliers():
    base = np.full((20, 20), 1.0, dtype=np.float32)
    frames = [base.copy() for _ in range(5)]
    frames[0][5, 5] = 500.0  # a cosmic ray or an asteroid in one exposure
    frames[1][7, 7] = np.nan  # masked in one exposure
    for f in frames:
        f[9, 9] = np.nan  # masked in every exposure
    out = stack.median_stack(frames)
    assert out[5, 5] == pytest.approx(1.0)
    assert out[7, 7] == pytest.approx(1.0)
    assert np.isnan(out[9, 9])


def test_median_stack_levels_the_sky():
    """Exposures with different sky levels and partial coverage leave no footprint edge."""
    a = np.full((10, 10), 0.3, dtype=np.float32)
    b = np.full((10, 10), 0.5, dtype=np.float32)
    c = np.full((10, 10), 0.7, dtype=np.float32)
    c[:, 5:] = np.nan  # covers half the frame
    out = stack.median_stack([a, b, c])
    assert np.allclose(out, 0.5)
    raw = stack.median_stack([a, b, c], level=False)
    assert raw[0, 0] == pytest.approx(0.5) and raw[0, 9] == pytest.approx(0.4)  # the edge we avoid


def test_bin_mean():
    a = np.arange(16, dtype=np.float32).reshape(4, 4)
    a[0, 0] = np.nan
    out = stack.bin_mean(a, 2)
    assert out.shape == (2, 2)
    assert out[0, 0] == pytest.approx((1 + 4 + 5) / 3)
    assert out[1, 1] == pytest.approx((10 + 11 + 14 + 15) / 4)


def test_resample_real_cutout_puts_stars_where_the_wcs_says():
    l2 = open_level2(FIXTURE_CUTOUT)
    ny, nx = l2.data.shape
    ra0, dec0 = (float(v) for v in l2.wcs.all_pix2world((nx - 1) / 2, (ny - 1) / 2, 0))
    grid, _ = stack.grid_for(ra0, dec0, "months", 5)
    rs = stack.resample(l2, grid)
    assert rs.image.shape == (146, 146)
    assert rs.covered.all()  # a 10' cutout covers the 5' field
    assert 0.74 < rs.um_min <= rs.um_max < 1.11  # detector 1
    # brightest unmasked source pixel in the field lands on the same sky position in the frame
    yy, xx = np.indices(l2.data.shape)
    r, d = l2.wcs.all_pix2world(xx, yy, 0)
    gx, gy = grid.wcs.wcs_world2pix(r, d, 0)
    inside = (gx > 15) & (gx < 130) & (gy > 15) & (gy < 130) & ((l2.flags & MASK_BITS) == 0)
    j = np.nanargmax(np.where(inside, l2.data, -np.inf))
    py, px = np.unravel_index(j, l2.data.shape)
    fy, fx = np.unravel_index(np.nanargmax(rs.image), rs.image.shape)
    assert abs(fx - gx[py, px]) <= 2.5 and abs(fy - gy[py, px]) <= 2.5  # within one native pixel
    # surface brightness is kept: the sky level is the same
    assert np.nanmedian(rs.image) == pytest.approx(float(np.nanmedian(l2.data[inside])), rel=0.05)


def test_resample_masks_bad_pixels_but_never_source():
    l2 = open_level2(FIXTURE_CUTOUT)
    ny, nx = l2.data.shape
    ra0, dec0 = (float(v) for v in l2.wcs.all_pix2world((nx - 1) / 2, (ny - 1) / 2, 0))
    grid = stack.Grid(ra0, dec0, 6.15, 41)
    l2.flags[:] = SOURCE  # every pixel "SOURCE": nothing may be masked
    rs = stack.resample(l2, grid)
    assert np.isfinite(rs.image).all() and not rs.bad.any()
    l2.flags[45:56, 45:56] |= HOT  # a block of hot pixels at the centre
    rs = stack.resample(l2, grid)
    assert np.isnan(rs.image[20, 20]) and rs.bad[20, 20]
    assert np.isfinite(rs.image[2, 2])
    ff, sf = stack.fractions([rs])
    assert 0.05 < ff < 0.09 and sf == 0.0  # 11 x 11 of 41 x 41
