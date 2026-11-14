"""Cutout photometry on the real fixture in fixtures/fits/ and the cutout stream truncation (offline)."""

import json
from pathlib import Path

import numpy as np
import pytest

import cutouts as C
from phot import measure

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "fits"
FITS = FIX / "cutout_2025W18_1B_0074_2D1.fits"
STARS = json.loads((FIX / "cutout_2025W18_1B_0074_2D1.stars.json").read_text())["stars"]


def test_measure_fixture_stars():
    l2 = C.open_cutout(FITS)  # the fixture keeps its own WCS-WAVE
    good = 0
    for s in STARS[:5]:
        pt = measure(l2, s["ra"], s["dec"], radii=(2.0, 3.0))
        if not pt.ok:
            continue
        good += 1
        assert pt.offset_px < 0.5
        assert 0.75 < pt.um < 1.15  # D1
        # flux in the 2 px aperture (with 1.033 correction) vs the full-frame detection flux
        ujy_per = 889.7
        assert pt.flux[2.0] == pytest.approx(s["flux_mjysr"] * ujy_per, rel=0.25)
        assert pt.flux[2.0] * 0.95 < pt.flux[3.0] < pt.flux[2.0] * 1.25  # curve of growth
        assert pt.err[2.0] > 0
    assert good >= 3


def test_measure_off_source_is_faint():
    l2 = C.open_cutout(FITS)
    s = STARS[0]
    pt = measure(l2, s["ra"], s["dec"], radii=(2.0,))
    # a blank-ish spot 15 px away, forced
    from skyshift_core import pixel_to_sky

    ra, dec = pixel_to_sky(l2.wcs, 50.0, 15.0)
    q = measure(l2, float(np.atleast_1d(ra)[0]), float(np.atleast_1d(dec)[0]), radii=(2.0,), recentre=False)
    if q.ok:
        assert abs(q.flux[2.0]) < 0.1 * pt.flux[2.0]


def test_keep_prefix_truncates_after_variance(tmp_path):
    raw = FITS.read_bytes()
    n = C.keep_prefix(raw)
    assert n is not None and n < len(raw)
    assert C.keep_prefix(raw[: n - 1]) is None  # incomplete stream: keep reading
    p = tmp_path / "cut.fits"
    p.write_bytes(raw[:n])
    from astropy.io import fits

    with fits.open(p) as h:
        assert [x.name for x in h] == ["PRIMARY", "IMAGE", "FLAGS", "VARIANCE"]
