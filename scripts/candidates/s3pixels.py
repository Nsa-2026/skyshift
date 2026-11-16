"""Small pixel reads from NASA's public S3 copy of SPHEREx Level 2 (anonymous HTTPS range requests, no whole files).

The method of server cutouts._region_fast and the artefact audit's partner.py, cut down to what a pixel check needs:
- layout(url): the PRIMARY + IMAGE headers (one 40 kB read) and the FLAGS header (one 35 kB read): where IMAGE and
  FLAGS start. Cached as JSON (one file per photo) so a rerun reads no header twice.
- strip(url, y, half): IMAGE rows y−half..y+half, full width, in ONE range request (rows are contiguous: 8 kB a row),
  and the same FLAGS rows (RICE_1, one tile per row: the tile descriptors, then their part of the heap; decoded with
  astropy's own codec). About 0.3 MB for 33 rows.
- listing(prefix): S3 ListObjectsV2 keys under a prefix (to find the exposure just before another, in another field).
Every byte read is counted in BYTES (thread-safe) for the report.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

BLOCK = 2880
S3_HTTPS = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/"
BYTES = [0]
REQUESTS = [0]
_LOCK = threading.Lock()


def get(url: str, a: int | None = None, b: int | None = None, tries: int = 4) -> bytes:
    """GET (bytes a..b inclusive when given), retried with backoff."""
    headers = {"User-Agent": "SkyShift-pixel-check/0.1"}
    if a is not None:
        headers["Range"] = f"bytes={a}-{b}"
    last: Exception | None = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                raw = r.read()
            with _LOCK:
                BYTES[0] += len(raw)
                REQUESTS[0] += 1
            return raw
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise FileNotFoundError(url) from e
            last = e
        except Exception as e:  # noqa: BLE001 - network errors of many kinds
            last = e
        time.sleep(1.0 + 2 * k)
    raise RuntimeError(f"gave up on {url}: {last!r}")


def _header(raw: bytes, pos: int) -> tuple[fits.Header | None, int | None]:
    """The FITS header starting at raw[pos] and the byte after its END block, or (None, None) if raw ends first."""
    end = pos
    while True:
        blk = raw[end:end + BLOCK]
        if len(blk) < BLOCK:
            return None, None
        end += BLOCK
        if any(blk[i:i + 8] == b"END     " for i in range(0, BLOCK, 80)):
            return fits.Header.fromstring(raw[pos:end].decode("ascii", "replace")), end


def _datasize(h: fits.Header) -> int:
    n = abs(int(h.get("BITPIX", 8))) // 8
    for i in range(1, int(h.get("NAXIS", 0)) + 1):
        n *= int(h[f"NAXIS{i}"])
    n += int(h.get("PCOUNT", 0))
    return (n + BLOCK - 1) // BLOCK * BLOCK


@dataclass
class Layout:
    image_header: str
    image: int  # first byte of the IMAGE pixels
    flags_header: str
    flags: int  # first byte of the FLAGS table (tile descriptors; the heap follows)
    nx: int
    ny: int

    def header(self) -> fits.Header:
        """The IMAGE header, parsed once (read it, don't change it)."""
        h = self.__dict__.get("_header")
        if h is None:
            h = self.__dict__["_header"] = fits.Header.fromstring(self.image_header)
        return h

    def wcs(self) -> WCS:
        """The celestial WCS, built once per photo (parsing the SIP header is the slow part of planning)."""
        w = self.__dict__.get("_wcs")
        if w is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                w = self.__dict__["_wcs"] = WCS(self.header()).celestial
        return w

    def mjd(self) -> float:
        return float(self.header().get("MJD-AVG", np.nan))

    def detector(self) -> int:
        return int(self.header().get("DETECTOR", -1))


class Reader:
    """Range reads with a header cache folder (cache/<key>.json)."""

    def __init__(self, cache: Path):
        self.cache = Path(cache)
        (self.cache / "layout").mkdir(parents=True, exist_ok=True)
        (self.cache / "listing").mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, Layout] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(url: str) -> str:
        return Path(urllib.parse.urlparse(url).path).name.removesuffix(".fits")

    def layout(self, url: str) -> Layout:
        k = self.key(url)
        if k in self._mem:
            return self._mem[k]
        p = self.cache / "layout" / f"{k}.json"
        if p.exists():
            lay = Layout(**json.loads(p.read_text()))
        else:
            raw = get(url, 0, 9 * BLOCK - 1)  # PRIMARY (1 block) + IMAGE header (8 blocks) in every file seen
            h0, p1 = _header(raw, 0)
            hi, pimg = _header(raw, p1) if p1 else (None, None)
            if hi is None:
                raw = get(url, 0, 60 * BLOCK - 1)
                h0, p1 = _header(raw, 0)
                hi, pimg = _header(raw, p1)
            if hi is None or str(hi.get("EXTNAME", "")).strip() != "IMAGE":
                raise ValueError(f"no IMAGE header in {url}")
            if int(hi.get("BITPIX", 0)) != -32 or any(k_ in hi for k_ in ("BZERO", "BSCALE")):
                raise ValueError("IMAGE is not plain float32")
            pf = pimg + _datasize(hi)
            fr = get(url, pf, pf + 6 * BLOCK - 1)  # the FLAGS header: 6 blocks in every file seen
            hf, pfd = _header(fr, 0)
            if hf is None:
                fr = get(url, pf, pf + 20 * BLOCK - 1)
                hf, pfd = _header(fr, 0)
            if hf is None or str(hf.get("EXTNAME", "")).strip() != "FLAGS":
                raise ValueError(f"FLAGS does not follow IMAGE in {url}")
            lay = Layout(hi.tostring(), int(pimg), hf.tostring(), int(pf + pfd), int(hi["NAXIS1"]), int(hi["NAXIS2"]))
            tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(asdict(lay)))
            tmp.replace(p)
        with self._lock:
            self._mem[k] = lay
        return lay

    def strip(self, url: str, y: float, half: int, x0: int | None = None, x1: int | None = None
              ) -> tuple[np.ndarray, np.ndarray, int, int]:
        """(IMAGE rows, FLAGS rows, first row, first column) of rows round(y)−half..round(y)+half, clipped to the frame.
        Full width in one request; with columns x0..x1 (exclusive) the IMAGE comes one small request per row (FLAGS
        rows are compressed whole: one request for their descriptors, one for their heap)."""
        from astropy.io.fits.hdu.compressed._codecs import Rice1

        lay = self.layout(url)
        y0 = max(0, int(round(y)) - half)
        y1 = min(lay.ny, int(round(y)) + half + 1)
        if y1 <= y0:
            raise ValueError("strip outside the frame")
        nx = lay.nx
        c0 = 0 if x0 is None else max(0, int(x0))
        c1 = nx if x1 is None else min(nx, int(x1))
        if c1 - c0 >= nx // 2:
            c0, c1 = 0, nx
        if (c0, c1) == (0, nx):
            a = lay.image + y0 * nx * 4
            raw = get(url, a, a + (y1 - y0) * nx * 4 - 1)
            data = np.frombuffer(raw, ">f4").reshape(y1 - y0, nx).astype(np.float64)
        else:
            rows = []
            for yy in range(y0, y1):
                a = lay.image + (yy * nx + c0) * 4
                rows.append(np.frombuffer(get(url, a, a + (c1 - c0) * 4 - 1), ">f4"))
            data = np.stack(rows).astype(np.float64)
        hf = fits.Header.fromstring(lay.flags_header)
        if (str(hf.get("ZCMPTYPE", "")).strip() != "RICE_1" or int(hf.get("ZTILE2", 0)) != 1
                or int(hf.get("ZTILE1", 0)) != nx):
            raise ValueError("FLAGS is not RICE_1 row tiles")
        row_bytes = int(hf["NAXIS1"])
        heap = lay.flags + int(hf.get("THEAP", row_bytes * int(hf["NAXIS2"])))
        desc = np.frombuffer(get(url, lay.flags + y0 * row_bytes, lay.flags + y1 * row_bytes - 1), ">i4")
        desc = desc.reshape(y1 - y0, 2).astype(np.int64)  # (bytes, heap offset) per row tile
        lo, hi = int(desc[:, 1].min()), int((desc[:, 1] + desc[:, 0]).max())
        blob = get(url, heap + lo, heap + hi - 1)
        params = {str(hf.get(f"ZNAME{i}", "")).strip(): int(hf[f"ZVAL{i}"]) for i in (1, 2, 3) if f"ZVAL{i}" in hf}
        codec = Rice1(blocksize=params.get("BLOCKSIZE", 32), bytepix=params.get("BYTEPIX", 4), tilesize=nx)
        flg = np.empty((y1 - y0, c1 - c0), np.int64)
        for i, (n, off) in enumerate(desc):
            flg[i] = codec.decode(np.frombuffer(blob, dtype=np.uint8, count=int(n), offset=int(off - lo)))[c0:c1]
        return data, flg, y0, c0

    def listing(self, prefix: str) -> list[str]:
        """Keys under prefix in the public bucket (cached)."""
        p = self.cache / "listing" / (re.sub(r"[^A-Za-z0-9_.-]", "_", prefix) + ".json")
        if p.exists():
            return json.loads(p.read_text())
        keys: list[str] = []
        token = None
        while True:
            q = {"list-type": "2", "prefix": prefix}
            if token:
                q["continuation-token"] = token
            raw = get(S3_HTTPS + "?" + urllib.parse.urlencode(q)).decode()
            keys += re.findall(r"<Key>([^<]+)</Key>", raw)
            m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", raw)
            if not m:
                break
            token = m.group(1)
        tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(keys))
        tmp.replace(p)
        return keys


OBS_RE = re.compile(r"^(?P<pp>\d{4}W\d{2}_\d[A-Z])_(?P<seq>\d{4})_(?P<exp>\d+)$")


def parse_obs(obs_id: str) -> tuple[str, int, int] | None:
    """('2025W34_1B', 366, 4) for '2025W34_1B_0366_4' (planning period, observation, exposure), else None."""
    m = OBS_RE.match(obs_id)
    return (m["pp"], int(m["seq"]), int(m["exp"])) if m else None
