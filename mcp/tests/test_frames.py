import io

import numpy as np
import pytest
from PIL import Image

from skyshift_mcp import frames as fr


def _raw(a: np.ndarray) -> bytes:
    return a.astype("<u2").tobytes()


def test_decode_scale_zero_and_mask():
    a = np.array([[0, 1], [2, 65535]])
    f = fr.decode(_raw(a), [2, 2], scale=0.5, zero=1.0)
    assert f.mask[0, 0] and not f.mask[1, 1]
    assert f[0, 1] == pytest.approx(1.5) and f[1, 0] == pytest.approx(2.0)
    # first row is north, first value east: shape is (ny, nx)
    g = fr.decode(_raw(np.arange(1, 7)), [3, 2], 1.0, 0.0)
    assert g.shape == (2, 3) and g[0, 2] == 3


def test_decode_size_mismatch():
    with pytest.raises(ValueError):
        fr.decode(_raw(np.ones(5)), [2, 2], 1.0, 0.0)


def test_pick_epochs_spread_keeps_ends():
    eps = [{"id": f"e{i}", "det": 2, "mjd_mid": 100 + i} for i in range(10)] + [{"id": "x", "det": 5, "mjd_mid": 0}]
    got = fr.pick_epochs(list(reversed(eps)), 2, 4)
    assert [e["id"] for e in got][0] == "e0" and got[-1]["id"] == "e9" and len(got) == 4
    assert len(fr.pick_epochs(eps, 5, 4)) == 1


def test_contact_sheet_png():
    rng = np.random.default_rng(1)
    base = rng.normal(1.0, 0.05, (40, 40))
    a, b = base.copy(), base.copy()
    a[20, 15] += 5
    b[20, 25] += 5  # the star moved
    fa = np.ma.masked_array(a, mask=np.zeros_like(a, bool))
    fb = np.ma.masked_array(b, mask=np.zeros_like(b, bool))
    fb.mask[0, :] = True
    png = fr.contact_sheet([fa, fb], ["v1", "v2"])
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    factor = -(-fr.PANEL_MIN // 40)
    assert img.height == 40 * factor + fr.LABEL_H
    assert img.width == 3 * 40 * factor + 2 * fr.GAP  # two frames + difference


def test_label_for_dates():
    assert fr.label_for({"id": "d2-v1", "kind": "visit", "mjd_mid": 60908.5}) == "d2-v1  2025-08-21"
    assert fr.label_for({"id": "x", "kind": "exposure", "mjd_mid": 60908.5}).endswith("12:00")


def test_crop_centre_and_noop():
    f = np.ma.masked_array(np.arange(100.0).reshape(10, 10), mask=np.zeros((10, 10), bool))
    assert fr.crop(f, 6.0, None) is f
    assert fr.crop(f, 6.0, 10.0) is f  # larger than the frame
    # 54″ = 9 px → half 4 (minimum). The centre of an even frame (4.5, 4.5) falls between pixels,
    # so the crop is 8×8 (rows/columns 1–8) and keeps that centre at its own centre (3.5, 3.5).
    c = fr.crop(f, 6.0, 0.9)
    assert c.shape == (8, 8) and c[0, 0] == f[1, 1] and c[7, 7] == f[8, 8]
    assert fr.crop_origin(f.shape, 6.0, 0.9) == (1, 1)
    # odd frame: centre pixel (5, 5) stays the centre pixel (4, 4) of a 9×9 crop
    g = np.ma.masked_array(np.arange(121.0).reshape(11, 11), mask=np.zeros((11, 11), bool))
    cg = fr.crop(g, 6.0, 0.9)
    assert cg.shape == (9, 9) and cg[4, 4] == g[5, 5]
    assert fr.crop_origin(g.shape, 6.0, None) == (0, 0)
