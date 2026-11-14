"""Small SPHEREx cutouts from IRSA's cutout service, cached under .cache/cutouts/.

    <IRSA image URL>?center=RA,DEC&size=DEG       (degrees; IRSA servers only, not S3)

A cutout carries all six parts. EPSF alone is ~3.9 MB whatever the box size, so we stream the
response and stop after PRIMARY, IMAGE, FLAGS and VARIANCE (~60 kB for a 2.4' box). The
WCS-WAVE table is the same calibration file for every photo of one detector
(`spectral_wcs_<det>_v2.2.fits`, checked on files from different weeks and releases by
`check_wave_tables`), so it is fetched once per detector from one full cutout and reused; the
per-cutout CRPIX1W/2W shift is in the IMAGE header we keep.

server/src/skyshift_server/cutouts.py did not exist on main when this was written.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

import numpy as np
import requests
from astropy.io import fits

from skyshift_core import Level2, WaveTable, open_level2

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
CUT_DIR = CACHE / "cutouts"
WAVE_DIR = CACHE / "wave"
MAX_WORKERS = 8  # at most 8 IRSA requests in flight
KEEP = ("PRIMARY", "IMAGE", "FLAGS", "VARIANCE")
BLOCK = 2880
UA = {"User-Agent": "skyshift-science/0.1 (python-requests)"}

_session = requests.Session()
_session.headers.update(UA)


def cutout_url(access_url: str, ra: float, dec: float, size_deg: float) -> str:
    return f"{access_url}?center={ra:.7f},{dec:.7f}&size={size_deg:.5f}"


def _cache_path(url: str, tag: str = "") -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    return CUT_DIR / f"{tag}{h}.fits"


def _parse_header(buf: bytes, off: int) -> tuple[dict[str, str], int] | None:
    """Header cards of the HDU at `off` and the offset just after the header, or None if incomplete."""
    hdr: dict[str, str] = {}
    pos = off
    while pos + BLOCK <= len(buf):
        block = buf[pos:pos + BLOCK]
        pos += BLOCK
        for i in range(36):
            card = block[i * 80:(i + 1) * 80].decode("ascii", "replace")
            key = card[:8].strip()
            if key == "END":
                return hdr, pos
            if card[8:10] == "= ":
                hdr[key] = card[10:].split("/")[0].strip().strip("'").strip()
    return None


def _hdu_data_size(hdr: dict[str, str]) -> int:
    naxis = int(hdr.get("NAXIS", 0))
    n = 0
    if naxis > 0:
        n = 1
        for k in range(1, naxis + 1):
            n *= int(hdr[f"NAXIS{k}"])
    size = abs(int(hdr["BITPIX"])) // 8 * int(hdr.get("GCOUNT", 1)) * (int(hdr.get("PCOUNT", 0)) + n)
    return -(-size // BLOCK) * BLOCK


def keep_prefix(buf: bytes, keep: Iterable[str] = KEEP) -> int | None:
    """Byte length of the leading HDUs up to and including the last of `keep`, or None if not yet in buf.

    Returns len(buf) if the stream ended (caller passes the whole file) and fewer HDUs exist.
    """
    want = {k.upper() for k in keep}
    off = 0
    seen: set[str] = set()
    idx = 0
    while True:
        parsed = _parse_header(buf, off)
        if parsed is None:
            return None
        hdr, data_off = parsed
        name = "PRIMARY" if idx == 0 else hdr.get("EXTNAME", "").upper()
        end = data_off + _hdu_data_size(hdr)
        if end > len(buf):
            return None
        seen.add(name)
        if want <= seen:
            return end
        off = end
        idx += 1


def fetch(url: str, path: Path, *, keep: Iterable[str] | None = KEEP, retries: int = 3,
          timeout: float = 120.0) -> Path:
    """Stream a cutout to `path`, stopping after the HDUs in `keep` (None = whole file)."""
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with _session.get(url, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                buf = bytearray()
                cut = None
                for chunk in r.iter_content(chunk_size=16384):
                    buf.extend(chunk)
                    if keep is not None and len(buf) >= BLOCK:
                        cut = keep_prefix(bytes(buf), keep)
                        if cut is not None:
                            break
                if not buf.startswith(b"SIMPLE"):
                    raise ValueError(f"not FITS: {bytes(buf[:120])!r}")
                data = bytes(buf[:cut]) if cut is not None else bytes(buf)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            return path
        except Exception as e:  # noqa: BLE001 - retry any transient failure
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"cutout failed after {retries} tries: {url}: {last}")


def fetch_many(jobs: list[tuple[str, Path]], *, workers: int = MAX_WORKERS, log_every: int = 100
               ) -> dict[str, Path | Exception]:
    """Fetch (url, path) pairs, at most `workers` in flight. Returns url -> path or the exception."""
    out: dict[str, Path | Exception] = {}
    todo = [(u, p) for u, p in jobs if not (p.exists() and p.stat().st_size > 0)]
    for u, p in jobs:
        if (u, p) not in todo:
            out[u] = p
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(workers, MAX_WORKERS)) as ex:
        futs = {ex.submit(fetch, u, p): u for u, p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            u = futs[f]
            try:
                out[u] = f.result()
            except Exception as e:  # noqa: BLE001
                out[u] = e
            if log_every and i % log_every == 0:
                print(f"  {i}/{len(todo)} fetched ({time.time() - t0:.0f} s)", flush=True)
    return out


# --- WCS-WAVE tables, one per detector -----------------------------------------------------

def wave_table_path(det: int) -> Path:
    return WAVE_DIR / f"wcs_wave_D{det}.fits"


def ensure_wave_table(det: int, access_url: str, ra: float, dec: float) -> Path:
    """Fetch one full (tiny) cutout of a photo of detector `det` and keep its WCS-WAVE HDU."""
    p = wave_table_path(det)
    if p.exists():
        return p
    full = fetch(cutout_url(access_url, ra, dec, 0.005), WAVE_DIR / f"full_D{det}.fits", keep=None)
    with fits.open(full) as h:
        fits.HDUList([fits.PrimaryHDU(), h["WCS-WAVE"].copy()]).writeto(p, overwrite=True)
    return p


def load_wave_hdu(det: int) -> fits.FITS_rec:
    with fits.open(wave_table_path(det)) as h:
        return h["WCS-WAVE"].data.copy()


_WAVE: dict[int, fits.FITS_rec] = {}


def open_cutout(path: str | Path, *, variance: bool = True) -> Level2:
    """open_level2 on a truncated cutout, with the detector's WCS-WAVE table put back."""
    l2 = open_level2(path, variance=variance)
    if l2.wave is None:
        det = l2.detector
        if det not in _WAVE:
            _WAVE[det] = load_wave_hdu(det)
        l2.wave = WaveTable.from_hdus(l2.header, _WAVE[det])
    return l2


def check_wave_tables(paths: list[Path]) -> bool:
    """True if all given full-cutout WCS-WAVE tables are identical."""
    vals = []
    for p in paths:
        with fits.open(p) as h:
            vals.append(np.asarray(h["WCS-WAVE"].data["VALUES"][0]))
    return all(np.array_equal(vals[0], v) for v in vals[1:])


# --- S3 region reads (same cache format, no IRSA server work) --------------------------------

S3_HTTPS = "https://nasa-irsa-spherex.s3.amazonaws.com/"
_S3_FSSPEC = {"block_size": 64 * 1024, "cache_type": "readahead"}


def fetch_s3_region(s3_key: str, ra: float, dec: float, half_px: int, path: Path, retries: int = 3) -> Path:
    """Read a (2 half_px + 1)^2 box around (ra, dec) from the public S3 copy with HTTP range reads
    and write it as a cutout-style FITS (PRIMARY, IMAGE, FLAGS, VARIANCE), CRPIX1/2 and CRPIX1W/2W
    shifted like IRSA's cutout service does. ~0.5 MB transferred per 23 x 23 box."""
    if path.exists() and path.stat().st_size > 0:
        return path
    from astropy.wcs import WCS

    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with fits.open(S3_HTTPS + s3_key, use_fsspec=True, fsspec_kwargs=_S3_FSSPEC,
                           lazy_load_hdus=True, memmap=False) as h:
                hdr = h["IMAGE"].header.copy()
                import warnings as _w

                with _w.catch_warnings():
                    _w.simplefilter("ignore")
                    w = WCS(hdr).celestial
                x, y = w.all_world2pix(ra, dec, 0)
                xc, yc = int(round(float(x))), int(round(float(y)))
                ny, nx = int(hdr["NAXIS2"]), int(hdr["NAXIS1"])
                x0, x1 = max(xc - half_px, 0), min(xc + half_px + 1, nx)
                y0, y1 = max(yc - half_px, 0), min(yc + half_px + 1, ny)
                if x1 <= x0 or y1 <= y0:
                    raise ValueError("position off the image")
                img = np.asarray(h["IMAGE"].section[y0:y1, x0:x1])
                flg = np.asarray(h["FLAGS"].section[y0:y1, x0:x1]).astype(np.int32)
                var = np.asarray(h["VARIANCE"].section[y0:y1, x0:x1])
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
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"S3 region failed: {s3_key}: {last}")


def fetch_many_s3(jobs: list[tuple[str, float, float, int, Path]], workers: int = 8, log_every: int = 100
                  ) -> dict[Path, Path | Exception]:
    out: dict[Path, Path | Exception] = {}
    todo = [j for j in jobs if not (j[4].exists() and j[4].stat().st_size > 0)]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_s3_region, *j): j[4] for j in todo}
        for i, f in enumerate(as_completed(futs), 1):
            p = futs[f]
            try:
                out[p] = f.result()
            except Exception as e:  # noqa: BLE001
                out[p] = e
            if log_every and i % log_every == 0:
                print(f"  s3 {i}/{len(todo)} ({time.time() - t0:.0f} s)", flush=True)
    return out
