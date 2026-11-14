"""Where pixels come from: the IRSA-then-S3 order, the busy breaker, the cache tiers, and the
compact FITS made from an S3 region (offline: the region is cut from the fixture cutout)."""

import numpy as np
import pytest
from astropy.io import fits
from skyshift_core import open_level2

from skyshift_server import cutouts
from skyshift_server.cache import Cache
from skyshift_server.search import Image

from conftest import FIXTURE_CUTOUT

RA, DEC = 304.4799843, 41.8376316


def _im(det: int = 1, s3: bool = True) -> Image:
    return Image("spherex_dr1", "2025W18_1B_0074_2", det, 60794.017, 60794.0183, 60794.01766,
                 "https://irsa.example/ibe/x.fits", "https://s3.example/x.fits" if s3 else None, 1, 1)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(cutouts, "_irsa", cutouts._Breaker())
    monkeypatch.setattr(cutouts, "STATS", cutouts.Stats())
    monkeypatch.delenv("SKYSHIFT_PIXELS", raising=False)


def test_compact_fits_of_a_region_reads_back_the_same(cutout_bytes):
    whole = open_level2(FIXTURE_CUTOUT, variance=True)
    part = open_level2(FIXTURE_CUTOUT, variance=True, region=(20, 81, 30, 90))
    wave = cutouts._wave_hdu_from(cutout_bytes)
    assert wave is not None and len(wave) % 2880 == 0
    back = cutouts.open_bytes(cutouts.compact_fits(part, wave))
    assert back.data.shape == (60, 61)
    assert np.array_equal(back.data, whole.data[30:90, 20:81], equal_nan=True)
    assert np.array_equal(back.flags, whole.flags[30:90, 20:81])
    assert np.array_equal(back.variance, whole.variance[30:90, 20:81], equal_nan=True)
    assert back.mjd_avg == whole.mjd_avg and back.detector == whole.detector
    # same sky and same wavelength for the same pixel
    ra, dec = whole.wcs.all_pix2world(50.0, 60.0, 0)
    ra2, dec2 = back.wcs.all_pix2world(30.0, 30.0, 0)
    assert float(ra2) == pytest.approx(float(ra), abs=1e-9) and float(dec2) == pytest.approx(float(dec), abs=1e-9)
    assert float(back.wavelength(30.0, 30.0)) == pytest.approx(float(whole.wavelength(50.0, 60.0)), abs=1e-9)


def test_compact_fits_without_variance(cutout_bytes):
    part = open_level2(FIXTURE_CUTOUT, region=(0, 40, 0, 40))
    data = cutouts.compact_fits(part, cutouts._wave_hdu_from(cutout_bytes))
    assert not cutouts.has_variance(data)
    assert cutouts.open_bytes(data).variance is None


def test_region_for_clips_to_the_detector():
    with fits.open(FIXTURE_CUTOUT) as h:
        hdr = h["IMAGE"].header.copy()
    hdr["NAXIS1"], hdr["NAXIS2"] = 101, 101
    x, y = 38.12, 54.17  # where the star is in the fixture
    r = cutouts.region_for(hdr, RA, DEC, 20 * 6.15 / 3600)  # 20 px across
    assert r is not None
    x0, x1, y0, y1 = r
    assert x0 <= x - 10 < x0 + 1 and x1 - 2 <= x + 10 < x1 and y0 <= y - 10 < y0 + 1
    x0, x1, y0, y1 = cutouts.region_for(hdr, RA, DEC, 1.0)  # bigger than the image: clipped
    assert (x0, x1, y0, y1) == (0, 101, 0, 101)
    assert cutouts.region_for(hdr, RA + 3.0, DEC, 0.12) is None  # misses the image


def test_irsa_first_then_s3_when_busy(tmp_path, monkeypatch):
    calls = []

    def irsa(im, ra, dec, size, cache, tries, variance):
        calls.append(("irsa", tries))
        raise cutouts.Busy("503")

    def s3(im, ra, dec, size, cache, variance):
        calls.append(("s3", variance))
        return b"S3DATA"

    monkeypatch.setattr(cutouts, "_from_irsa", irsa)
    monkeypatch.setattr(cutouts, "_from_s3", s3)
    c = Cache(str(tmp_path))
    assert cutouts.fetch(_im(), RA, DEC, 0.36, c) == b"S3DATA"
    assert calls == [("irsa", 1), ("s3", False)]  # one quick try, then S3; big cutout: no VARIANCE
    assert cutouts.fetch(_im(), RA, DEC, 0.36, c) == b"S3DATA" and len(calls) == 2  # cached
    # after BREAKER_AFTER busy answers in a row IRSA is skipped
    for det in (2, 3):
        cutouts.fetch(_im(det), RA, DEC, 0.12, c)
    calls.clear()
    cutouts.fetch(_im(4), RA, DEC, 0.12, c)
    assert calls == [("s3", True)]  # small cutouts always carry VARIANCE


def test_irsa_only_is_patient_and_s3_only_never_asks_irsa(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cutouts, "_from_irsa", lambda im, ra, dec, size, cache, tries, variance: calls.append(("irsa", tries)) or b"I")
    monkeypatch.setattr(cutouts, "_from_s3", lambda *a: calls.append(("s3",)) or b"S")
    c = Cache(str(tmp_path))
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")
    assert cutouts.fetch(_im(1), RA, DEC, 0.12, c) == b"I"
    assert cutouts.fetch(_im(2, s3=False), RA, DEC, 0.12, c) == b"I"
    monkeypatch.setenv("SKYSHIFT_PIXELS", "s3")
    assert cutouts.fetch(_im(3), RA, DEC, 0.12, c) == b"S"
    assert calls == [("irsa", cutouts.TRIES), ("irsa", cutouts.TRIES), ("s3",)]


def test_larger_cached_cutout_is_reused_unless_variance_is_missing(tmp_path, monkeypatch, cutout_bytes):
    c = Cache(str(tmp_path))
    with_var = cutout_bytes
    part = open_level2(FIXTURE_CUTOUT, region=(0, 101, 0, 101))
    no_var = cutouts.compact_fits(part, cutouts._wave_hdu_from(cutout_bytes))
    calls = []
    monkeypatch.setattr(cutouts, "_from_irsa", lambda im, ra, dec, size, cache, tries, variance: calls.append((size, variance)) or with_var)
    c.put(cutouts.cache_key(_im(), RA, DEC, 0.36), no_var)
    assert cutouts.fetch(_im(), RA, DEC, 0.36, c) == no_var and not calls
    assert cutouts.fetch(_im(), RA, DEC, 0.36, c, variance=True) == with_var  # asked again, with VARIANCE
    assert cutouts.fetch(_im(), RA, DEC, 0.12, c) == with_var  # 0.12 needs VARIANCE: the new 0.36 has it
    assert calls == [(0.36, True)]


def test_a_missing_image_is_remembered_and_one_failure_does_not_stop_the_rest(tmp_path, monkeypatch, cutout_bytes):
    def irsa(im, ra, dec, size, cache, tries, variance):
        if im.det == 2:
            raise FileNotFoundError("HTTP 404")
        if im.det == 3:
            raise cutouts.UpstreamError("broken")
        return cutout_bytes

    monkeypatch.setattr(cutouts, "_from_irsa", irsa)
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")
    c = Cache(str(tmp_path))
    out = cutouts.fetch_many([_im(1), _im(2), _im(3)], RA, DEC, 0.12, c)
    assert out[0] is not None and out[0].detector == 1 and out[1] is None and out[2] is None
    assert c.get(cutouts.cache_key(_im(2), RA, DEC, 0.12)) == b""  # "no pixels here" is cached
    assert c.get(cutouts.cache_key(_im(3), RA, DEC, 0.12)) is None  # a failure is not
    assert cutouts.STATS.failed == 1


def test_every_cutout_failing_is_an_upstream_error(tmp_path, monkeypatch):
    def irsa(im, ra, dec, size, cache, tries, variance):
        raise cutouts.UpstreamError("down")

    monkeypatch.setattr(cutouts, "_from_irsa", irsa)
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")
    with pytest.raises(cutouts.UpstreamError):
        cutouts.fetch_many([_im(1), _im(2)], RA, DEC, 0.12, Cache(str(tmp_path)))
