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
    monkeypatch.setattr(cutouts, "_layout_mem", {})
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


# --- ZODI model, sampled from a (synthetic) full file over HTTP ranges -----------------------------

def _full_file(nx=120, ny=200):
    """A file laid out like a Level 2 file: PRIMARY, IMAGE, FLAGS (compressed), VARIANCE, ZODI,
    EPSF, WCS-WAVE. ZODI is smooth with a slow ripple along y, plus two bad pixels."""
    yy, xx = np.mgrid[:ny, :nx].astype(np.float32)
    zodi = (0.1 + 2e-5 * yy + 1e-3 * np.sin(yy / 35.0) - 3e-6 * xx).astype(np.float32)
    truth = zodi.copy()
    zodi[48, 60] += 0.012  # single-pixel artefacts, one of them on a sampled row
    zodi[77, 30] += 0.012
    rng = np.random.default_rng(3)
    hdul = fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(rng.normal(0.3, 0.02, (ny, nx)).astype(np.float32), name="IMAGE"),
        fits.CompImageHDU(rng.integers(0, 4, (ny, nx)).astype(np.int32), name="FLAGS"),
        fits.ImageHDU(np.full((ny, nx), 4e-4, dtype=np.float32), name="VARIANCE"),
        fits.ImageHDU(zodi, name="ZODI"),
        fits.ImageHDU(np.zeros((40, 40), dtype=np.float32), name="EPSF"),
    ])
    return cutouts.to_fits_bytes(hdul), truth


def _serve(monkeypatch, blob):
    asked = []

    def get(url, *, headers=None, truncate=None, tries=1):
        a, b = headers["Range"].removeprefix("bytes=").split("-")
        asked.append(int(b) - int(a) + 1)
        return blob[int(a):int(b) + 1]

    monkeypatch.setattr(cutouts, "_get", get)
    return asked


def _cutout_of(x0, y0, nx, ny):
    """A Level2 standing for a cutout whose origin in the full frame is (x0, y0)."""
    l2 = open_level2(FIXTURE_CUTOUT)
    l2.data = l2.data[:ny, :nx]
    l2.header["CRPIX1W"], l2.header["CRPIX2W"] = 1.0 - x0, 1.0 - y0
    return l2


def test_zodi_layout_walks_the_headers(tmp_path, monkeypatch):
    blob, _ = _full_file()
    asked = _serve(monkeypatch, blob)
    c = Cache(str(tmp_path))
    offset, width, height = cutouts.zodi_layout(_im(), c)
    with fits.open(__import__("io").BytesIO(blob)) as h:
        assert offset == h["ZODI"].fileinfo()["datLoc"] and (width, height) == (120, 200)
    assert len(asked) == 5 and sum(asked) < 200_000  # five small header reads, never the pixels
    assert cutouts.zodi_layout(_im(), c) == (offset, width, height) and len(asked) == 5  # cached


def test_zodi_model_from_a_few_rows_matches_the_whole_model(tmp_path, monkeypatch):
    blob, truth = _full_file()
    asked = _serve(monkeypatch, blob)
    c = Cache(str(tmp_path))
    l2 = _cutout_of(10, 20, 90, 100)
    model = cutouts.zodi_model(_im(), l2, c)
    assert model.shape == (100, 90) and model.dtype == np.float32
    assert np.abs(model - truth[20:120, 10:100]).max() < 1e-4  # ripple followed, bad pixels ignored
    rows = cutouts.zodi_sample_rows(100)
    assert list(rows) == [0, 33, 66, 99]
    assert asked[5:] == [90 * 4] * len(rows)  # one short read per sampled row
    n = len(asked)
    again = cutouts.zodi_model(_im(), l2, c)
    assert np.array_equal(again, model) and len(asked) == n  # cached


def test_zodi_sample_rows_spacing():
    for ny in (5, 72, 213, 285, 849):
        rows = cutouts.zodi_sample_rows(ny)
        assert rows[0] == 0 and rows[-1] == ny - 1 and len(rows) >= min(ny, 4)
        assert np.diff(rows).max() <= cutouts.ZODI_ROW_STEP + 1


def test_zodi_model_missing_is_none_not_an_error(tmp_path, monkeypatch):
    c = Cache(str(tmp_path))
    assert cutouts.zodi_model(_im(s3=False), _cutout_of(10, 20, 90, 100), c) is None  # no S3 copy

    def down(url, **k):
        raise cutouts.UpstreamError("down")

    monkeypatch.setattr(cutouts, "_get", down)
    assert cutouts.zodi_model(_im(), _cutout_of(10, 20, 90, 100), c) is None
    blob, _ = _full_file()
    _serve(monkeypatch, blob)
    assert cutouts.zodi_model(_im(), _cutout_of(100, 20, 90, 100), c) is None  # does not fit the frame


def test_fetch_many_subtracts_zodi_only_when_asked(tmp_path, monkeypatch, cutout_bytes):
    monkeypatch.setattr(cutouts, "_from_irsa", lambda *a: cutout_bytes)
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")
    monkeypatch.setattr(cutouts, "zodi_model", lambda im, l2, cache: np.full(l2.data.shape, 0.25, dtype=np.float32))
    c = Cache(str(tmp_path))
    plain = cutouts.fetch_many([_im()], RA, DEC, 0.12, c)[0]
    n_sub, n_missing = cutouts.STATS.zodi_subtracted, cutouts.STATS.zodi_missing
    sub = cutouts.fetch_many([_im()], RA, DEC, 0.12, c, zodi=True)[0]
    assert (cutouts.STATS.zodi_subtracted, cutouts.STATS.zodi_missing) == (n_sub + 1, n_missing)
    assert np.allclose(plain.data - sub.data, 0.25, atol=1e-5, equal_nan=True)
    assert sub.header["ZODISUB"] is True and "ZODISUB" not in plain.header
    monkeypatch.setattr(cutouts, "zodi_model", lambda im, l2, cache: None)
    kept = cutouts.fetch_many([_im()], RA, DEC, 0.12, c, zodi=True)[0]
    assert cutouts.STATS.zodi_missing == n_missing + 1  # counted, so precompute can report it
    assert np.array_equal(kept.data, plain.data, equal_nan=True) and kept.header["ZODISUB"] is False


# ---------------------------------------------------------------- direct S3 reads, time budget

def test_direct_s3_reader_matches_astropy(tmp_path, monkeypatch):
    import io

    blob, _ = _full_file()
    asked = _serve(monkeypatch, blob)
    c = Cache(str(tmp_path))
    lay = cutouts.s3_layout(_im(), c)
    with fits.open(io.BytesIO(blob)) as h:
        assert lay.image == h["IMAGE"].fileinfo()["datLoc"] and lay.zodi == h["ZODI"].fileinfo()["datLoc"]
        assert lay.variance == h["VARIANCE"].fileinfo()["datLoc"] and (lay.nx, lay.ny) == (120, 200)
        img, flg, var = h["IMAGE"].data.copy(), h["FLAGS"].data.copy(), h["VARIANCE"].data.copy()
    walked = len(asked)
    assert walked <= 4  # PRIMARY + IMAGE headers in one read, then FLAGS, VARIANCE, ZODI
    cutouts._layout_mem.clear()
    assert cutouts.s3_layout(_im(), c) == lay and len(asked) == walked  # from the cache
    url = _im().s3_url
    rows = cutouts._float_rows(url, lay.image, lay.nx, 20, 90, 10, 70)
    assert np.array_equal(rows, img[20:90, 10:70]) and len(asked) == walked + 1  # one request
    assert np.array_equal(cutouts._float_rows(url, lay.variance, lay.nx, 20, 90, 10, 70), var[20:90, 10:70])
    n = len(asked)
    f = cutouts._flag_rows(url, lay, 20, 90, 10, 70)
    assert f.dtype == np.int32 and np.array_equal(f, flg[20:90, 10:70])
    assert len(asked) == n + 2  # tile descriptors, then their part of the heap


def test_unexpected_flags_compression_is_refused(tmp_path, monkeypatch):
    blob, _ = _full_file()
    _serve(monkeypatch, blob)
    lay = cutouts.s3_layout(_im(), Cache(str(tmp_path)))
    lay.flags_header = lay.flags_header.replace("RICE_1  ", "GZIP_1  ")
    with pytest.raises(ValueError):  # _from_s3 then falls back to core.open_level2
        cutouts._flag_rows(_im().s3_url, lay, 0, 10, 0, 10)


def _ims(names):
    return [Image("spherex_dr1", n, 1, 60794.0 + i, 60794.001 + i, 60794.0005 + i,
                  f"https://irsa.example/ibe/{n}.fits", None, 1, 1) for i, n in enumerate(names)]


def test_fetch_many_returns_what_arrived_by_the_deadline(tmp_path, monkeypatch, cutout_bytes):
    import time

    from skyshift_server.budget import Budget

    def irsa(im, ra, dec, size, cache, tries, variance):
        if im.obs_id == "slow":
            time.sleep(1.0)
        return cutout_bytes

    monkeypatch.setattr(cutouts, "_from_irsa", irsa)
    monkeypatch.setattr(cutouts, "MAX_IN_FLIGHT", 1)
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")
    c = Cache(str(tmp_path))
    images = _ims(["fast", "slow", "next", "last"])
    b = Budget(0.5)
    t = time.monotonic()
    got = cutouts.fetch_many(images, RA, DEC, 0.12, c, budget=b)
    assert time.monotonic() - t < 0.9  # did not wait for the slow one
    assert got[0] is not None and got[1:] == [None, None, None]
    assert b.partial == ["3 of 4 cutouts not in time"]
    time.sleep(0.8)  # the slow download finishes in the background, into the cache
    again = cutouts.fetch_many(images, RA, DEC, 0.12, c, budget=Budget(5.0))
    assert all(l2 is not None for l2 in again)


def test_same_cutout_asked_twice_at_once_is_downloaded_once(tmp_path, monkeypatch, cutout_bytes):
    import threading
    import time

    calls = []

    def irsa(im, ra, dec, size, cache, tries, variance):
        calls.append(im.key)
        time.sleep(0.3)
        return cutout_bytes

    monkeypatch.setattr(cutouts, "_from_irsa", irsa)
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")
    c = Cache(str(tmp_path))
    out = []
    ts = [threading.Thread(target=lambda: out.append(cutouts.fetch(_im(), RA, DEC, 0.12, c))) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1 and out == [cutout_bytes] * 3


def test_s3first_reads_s3_and_asks_irsa_only_without_a_copy(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cutouts, "_from_irsa",
                        lambda im, ra, dec, size, cache, tries, variance: calls.append("irsa") or b"I")
    monkeypatch.setattr(cutouts, "_from_s3", lambda *a: calls.append("s3") or b"S")
    monkeypatch.setenv("SKYSHIFT_PIXELS", "s3first")
    c = Cache(str(tmp_path))
    assert cutouts.fetch(_im(), RA, DEC, 0.36, c) == b"S" and calls == ["s3"]
    assert cutouts.fetch(_im(s3=False), RA + 1, DEC, 0.36, c) == b"I" and calls == ["s3", "irsa"]
