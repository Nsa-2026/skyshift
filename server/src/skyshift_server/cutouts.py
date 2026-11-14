"""Small cutouts of Level 2 images, cached. Two sources, the same compact FITS from both.

1. IRSA's cutout service (the documented way):

       <IRSA image URL>?center=RA,DEC&size=DEG      (degrees; IRSA's servers only, not S3)

   Measured on 14 Nov 2026: a cutout is a FITS with PRIMARY, IMAGE, FLAGS (plain int32, not
   compressed), VARIANCE, ZODI, EPSF, WCS-WAVE, in that order. EPSF is always 3.9 MB, so a
   0.36 deg cutout is 4.6 MB of which we need 0.6 MB. The service ignores Range and gzip. So we
   stream the response, parse the FITS blocks as they arrive and close the connection after
   VARIANCE.

2. Range reads of the same region from NASA's public S3 copy (core.open_level2(url, region=)).
   On 14 Nov 2026 the cutout service answered 503 "Server busy, retry later" (Retry-After: 5) to
   most requests, even one at a time, so IRSA alone could not build a blink. S3 never refuses,
   but costs more bytes: the full-width rows of IMAGE and VARIANCE plus FLAGS tiles and headers
   (about 1.5 MB for a 5' field, 4 MB for 15'), which is nothing from Cloud Run and slow on a
   laptop line. Only the region is read, never the whole 60 MB file.

SKYSHIFT_PIXELS = "auto" (default): ask IRSA once; if it is busy, read S3, and after
BREAKER_AFTER busy answers in a row skip IRSA for BREAKER_SECONDS. "irsa": IRSA only, with
patient retries. "s3": S3 only.

What we keep is a compact FITS: PRIMARY, IMAGE, FLAGS, VARIANCE, WCS-WAVE, readable by
core.open_level2. WCS-WAVE is the detector's wavelength lookup table (calibration file
spectral_wcs_<det>_v2.2, identical in every DR1 and QR3 exposure we compared); the IMAGE header
carries the CRPIX1W/CRPIX2W that place the cutout on it. We read the table once per detector from
the tail of the S3 copy of one image (11 kB), or from one full IRSA cutout if S3 has no copy.

Sizes come in tiers so different fields share cached cutouts; a larger cached cutout at the same
centre is used before a smaller one is downloaded. At most 8 requests are in flight.
"""

from __future__ import annotations

import io
import logging
import os
import random
import tempfile
import warnings
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import requests
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from skyshift_core import Level2, open_level2

from .cache import Cache
from .errors import UpstreamError
from .search import Image, pos_key

log = logging.getLogger(__name__)

BLOCK = 2880
MAX_IN_FLIGHT = 8
SIZE_TIERS_DEG = (0.12, 0.36, 0.48, 1.45)  # covers the diagonal of 5', 15', 20' and 60' fields
KEEP_UNTIL = "VARIANCE"  # last HDU we need from the stream
TIMEOUT_S = (10, 60)
TRIES = 7  # patient mode (IRSA only)
NATIVE_ARCSEC = 6.15
FULL_FRAME = 2040
BREAKER_AFTER = 3  # busy answers in a row before IRSA is skipped
BREAKER_SECONDS = 60.0


def pixels_mode() -> str:
    m = os.environ.get("SKYSHIFT_PIXELS", "auto").lower()
    return m if m in ("auto", "irsa", "s3") else "auto"


class Busy(UpstreamError):
    """IRSA's cutout service refused (503 and friends) or broke the connection."""


class _Breaker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = 0
        self._until = 0.0

    def open(self) -> bool:
        return time.monotonic() < self._until

    def record(self, ok: bool) -> None:
        with self._lock:
            self._busy = 0 if ok else self._busy + 1
            if self._busy >= BREAKER_AFTER:
                self._until = time.monotonic() + BREAKER_SECONDS
                self._busy = 0


_irsa = _Breaker()

_pool = ThreadPoolExecutor(MAX_IN_FLIGHT, thread_name_prefix="cutout")
_wave_lock = threading.Lock()
_wave_mem: dict[int, bytes] = {}
_local = threading.local()


@dataclass
class Stats:
    """What a build downloaded (reported by scripts/precompute and the PR)."""

    requests: int = 0  # successful downloads (irsa + s3 + small ones)
    irsa: int = 0  # cutouts from IRSA's cutout service
    s3: int = 0  # cutouts read from S3
    cached: int = 0
    bytes: int = 0  # downloaded: exact for IRSA, estimated for S3 region reads
    retries: int = 0
    busy: int = 0  # IRSA "server busy" answers
    failed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **kw: int) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)


STATS = Stats()


def tier_for_field(fov_arcmin: float) -> float:
    """Smallest cutout size (deg) that covers a square field of fov arcmin at any roll angle."""
    need = fov_arcmin / 60.0 * 2**0.5
    for s in SIZE_TIERS_DEG:
        if s >= need - 1e-9:
            return s
    return SIZE_TIERS_DEG[-1]


def cache_key(im: Image, ra: float, dec: float, size_deg: float) -> str:
    return f"cutouts/{im.key}/{pos_key(ra, dec)}_{size_deg:g}.fits"


def _session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
        s.headers["User-Agent"] = "SkyShift/0.1"
    return s


# --- FITS stream ----------------------------------------------------------------------------

def _hdu_sizes(header_bytes: bytes) -> tuple[str, int]:
    """(EXTNAME, data bytes padded to 2880) of one HDU, from its raw header."""
    h = fits.Header.fromstring(header_bytes.decode("ascii", "replace"))
    naxis = int(h.get("NAXIS", 0))
    n = 0
    if naxis:
        n = 1
        for i in range(1, naxis + 1):
            n *= int(h[f"NAXIS{i}"])
        n = abs(int(h["BITPIX"])) // 8 * int(h.get("GCOUNT", 1)) * (int(h.get("PCOUNT", 0)) + n)
    return str(h.get("EXTNAME", "PRIMARY")).strip(), (n + BLOCK - 1) // BLOCK * BLOCK


def take_until(chunks, last_hdu: str = KEEP_UNTIL) -> bytes:
    """Read FITS bytes from an iterator of chunks and stop after the HDU named `last_hdu`.

    Returns the bytes of every HDU up to and including it (a valid FITS file).
    Raises ValueError if the stream ends first or is not FITS.
    """
    buf = bytearray()
    it = iter(chunks)

    def need(n: int) -> None:
        while len(buf) < n:
            try:
                buf.extend(next(it))
            except StopIteration:
                raise ValueError(f"stream ended at {len(buf)} bytes, before {last_hdu}") from None

    pos = 0
    while True:
        start = pos
        while True:  # header blocks until the END card
            need(pos + BLOCK)
            block = bytes(buf[pos:pos + BLOCK])
            pos += BLOCK
            if start == 0 and pos == BLOCK and not block.startswith(b"SIMPLE  ="):
                raise ValueError(f"not a FITS stream: {block[:60]!r}")
            if any(block[i:i + 8] == b"END     " for i in range(0, BLOCK, 80)):
                break
        name, size = _hdu_sizes(bytes(buf[start:pos]))
        need(pos + size)
        pos += size
        if name == last_hdu:
            return bytes(buf[:pos])


def _wave_hdu_from(data: bytes) -> bytes | None:
    """The raw bytes of the WCS-WAVE HDU inside `data` (a FITS file or its tail), or None."""
    i = data.rfind(b"XTENSION= 'BINTABLE'")
    while i >= 0:
        if b"EXTNAME = 'WCS-WAVE'" in data[i:i + BLOCK] and (len(data) - i) % BLOCK == 0:
            return data[i:]
        i = data.rfind(b"XTENSION= 'BINTABLE'", 0, i)
    return None


def _get(url: str, *, headers: dict[str, str] | None = None, truncate: bool = False,
         tries: int = TRIES) -> bytes:
    """GET with retries on 503 / broken connections. truncate: stop after VARIANCE.
    Raises Busy when every try was refused, FileNotFoundError on a 4xx."""
    last = ""
    for attempt in range(tries):
        if attempt:
            STATS.add(retries=1)
        try:
            with _session().get(url, headers=headers, stream=True, timeout=TIMEOUT_S) as r:
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}"
                    STATS.add(busy=1)
                    if attempt + 1 < tries:
                        wait = float(r.headers.get("Retry-After", 2) or 2)
                        time.sleep(min(wait, 10) * (0.6 + 0.8 * random.random()) + attempt)
                    continue
                if r.status_code >= 400:
                    raise FileNotFoundError(f"HTTP {r.status_code} for {url}")
                if truncate:
                    data = take_until(r.iter_content(64 * 1024))
                else:
                    data = r.content
                STATS.add(requests=1, bytes=len(data))
                return data
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
            if attempt + 1 < tries:
                time.sleep(1 + attempt + random.random())
    raise Busy(f"gave up on {url}: {last}")


def cutout_url(im: Image, ra: float, dec: float, size_deg: float) -> str:
    return f"{im.access_url}?center={ra:.6f},{dec:.6f}&size={size_deg:g}"


def wave_hdu(im: Image, cache: Cache, fallback_url: str | None = None) -> bytes:
    """Raw WCS-WAVE HDU for the image's detector (one table per detector, cached).
    Read from the tail of the S3 copy; if that fails, from one full IRSA cutout (fallback_url)."""
    det = im.det
    if det in _wave_mem:
        return _wave_mem[det]
    with _wave_lock:
        if det in _wave_mem:
            return _wave_mem[det]
        key = f"wave/D{det}.fits.part"
        hdu = cache.get(key)
        if hdu is None:
            if im.s3_url:
                try:
                    hdu = _wave_hdu_from(_get(im.s3_url, headers={"Range": f"bytes=-{4 * BLOCK}"}))
                except (UpstreamError, FileNotFoundError) as e:
                    log.warning("WCS-WAVE from S3 failed (%s); using a full IRSA cutout", e)
            if hdu is None and fallback_url:
                hdu = _wave_hdu_from(_get(fallback_url))  # the whole cutout, about 4 MB: rare
            if hdu is None:
                raise UpstreamError(f"no WCS-WAVE table for detector {det}")
            cache.put(key, hdu)
        _wave_mem[det] = hdu
        return hdu


def region_for(header: fits.Header, ra: float, dec: float, size_deg: float) -> tuple[int, int, int, int] | None:
    """(x0, x1, y0, y1) of the square of size_deg centred on (ra, dec) in a full frame, clipped
    to the detector; None if it misses the detector."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        w = WCS(header).celestial
        x, y = (float(v) for v in w.all_world2pix(ra, dec, 0, quiet=True))
    if not (np.isfinite(x) and np.isfinite(y)):
        return None
    nx, ny = int(header.get("NAXIS1", FULL_FRAME)), int(header.get("NAXIS2", FULL_FRAME))
    half = size_deg * 3600.0 / NATIVE_ARCSEC / 2.0
    x0, x1 = max(0, int(np.floor(x - half))), min(nx, int(np.ceil(x + half)) + 1)
    y0, y1 = max(0, int(np.floor(y - half))), min(ny, int(np.ceil(y + half)) + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, x1, y0, y1


def compact_fits(l2: Level2, wave: bytes) -> bytes:
    """A region read by core.open_level2 -> the compact cutout FITS (same as IRSA's cutouts:
    CRPIX and CRPIXnW shifted to the region, FLAGS as a plain int32 image)."""
    x0, y0 = l2.origin
    h = l2.header.copy()
    for key, off in (("CRPIX1", x0), ("CRPIX2", y0), ("CRPIX1A", x0), ("CRPIX2A", y0),
                     ("CRPIX1W", x0), ("CRPIX2W", y0)):
        if key in h:
            h[key] = float(h[key]) - off
    h["HISTORY"] = f"SkyShift: region x {x0}+{l2.data.shape[1]}, y {y0}+{l2.data.shape[0]} read from S3"
    hdus = [fits.PrimaryHDU(), fits.ImageHDU(np.asarray(l2.data, dtype=">f4"), header=h, name="IMAGE"),
            fits.ImageHDU(np.asarray(l2.flags, dtype=">i4"), name="FLAGS")]
    if l2.variance is not None:
        hdus.append(fits.ImageHDU(np.asarray(l2.variance, dtype=">f4"), name="VARIANCE"))
    return to_fits_bytes(fits.HDUList(hdus)) + wave


def _from_s3(im: Image, ra: float, dec: float, size_deg: float, cache: Cache) -> bytes | None:
    """The cutout read from the S3 copy with range requests. None if the field misses the image."""
    assert im.s3_url
    last = ""
    for attempt in range(3):
        try:
            # PRIMARY is one block; the IMAGE header (9 blocks measured) fits in the next 20
            raw = _get(im.s3_url, headers={"Range": f"bytes={BLOCK}-{21 * BLOCK - 1}"}, tries=2)
            header = fits.Header.fromstring(raw.decode("ascii", "replace"))
            region = region_for(header, ra, dec, size_deg)
            if region is None:
                return None
            l2 = open_level2(im.s3_url, variance=True, region=region)
            rows = region[3] - region[2]
            STATS.add(requests=1, s3=1, bytes=int(rows * FULL_FRAME * 4 * 2.4) + 6 * 256 * 1024)
            return compact_fits(l2, wave_hdu(im, cache))
        except FileNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001 - fsspec/aiohttp raise many kinds
            last = repr(e)
            STATS.add(retries=1)
            time.sleep(1 + attempt)
    raise UpstreamError(f"S3 read failed for {im.key}: {last}")


def _from_irsa(im: Image, ra: float, dec: float, size_deg: float, cache: Cache, tries: int) -> bytes:
    head = _get(cutout_url(im, ra, dec, size_deg), truncate=True, tries=tries)
    STATS.add(irsa=1)
    return head + wave_hdu(im, cache, cutout_url(im, ra, dec, 0.02))


def fetch(im: Image, ra: float, dec: float, size_deg: float, cache: Cache) -> bytes | None:
    """Compact cutout FITS (PRIMARY, IMAGE, FLAGS, VARIANCE, WCS-WAVE) of one image, or None if
    the image has no pixels there. Uses a cached cutout of this size or larger at this centre."""
    for s in SIZE_TIERS_DEG:
        if s >= size_deg - 1e-9:
            got = cache.get(cache_key(im, ra, dec, s))
            if got is not None:
                STATS.add(cached=1)
                return got or None  # b"" marks "no pixels here"
    mode = pixels_mode()
    has_s3 = bool(im.s3_url) and mode != "irsa"
    data: bytes | None = None
    done = False
    try:
        if mode != "s3" and not (has_s3 and _irsa.open()):
            try:
                data = _from_irsa(im, ra, dec, size_deg, cache, tries=1 if has_s3 else TRIES)
                done = True
                _irsa.record(True)
            except Busy:
                _irsa.record(False)
                if not has_s3:
                    raise
        if not done and has_s3:
            data = _from_s3(im, ra, dec, size_deg, cache)
            done = True
    except FileNotFoundError as e:
        log.info("no cutout for %s: %s", im.key, e)
        data, done = None, True
    if not done:
        raise UpstreamError(f"no source for {im.key}")
    cache.put(cache_key(im, ra, dec, size_deg), data or b"")
    return data


def open_bytes(data: bytes) -> Level2:
    """core.open_level2 on bytes (it wants a path)."""
    with tempfile.NamedTemporaryFile(suffix=".fits") as f:
        f.write(data)
        f.flush()
        try:
            return open_level2(f.name, variance=True)
        except KeyError:  # no VARIANCE HDU
            return open_level2(f.name)


def fetch_many(images: list[Image], ra: float, dec: float, size_deg: float, cache: Cache,
               centers: list[tuple[float, float]] | None = None) -> list[Level2 | None]:
    """Cutouts of many images, at most 8 requests in flight, in the order given.
    A failed image gives None (and is logged); it never fails the whole build.
    centers: one (ra, dec) per image when each cutout has its own centre (a moving object)."""

    def one(i: int) -> Level2 | None:
        im = images[i]
        r, d = centers[i] if centers else (ra, dec)
        try:
            data = fetch(im, r, d, size_deg, cache)
            return None if data is None else open_bytes(data)
        except Exception as e:  # noqa: BLE001
            STATS.add(failed=1)
            log.warning("cutout %s failed: %s", im.key, e)
            return None

    return list(_pool.map(one, range(len(images))))


def to_fits_bytes(hdul: fits.HDUList) -> bytes:
    b = io.BytesIO()
    hdul.writeto(b)
    return b.getvalue()
