"""Read pieces of a remote multi-extension FITS file with HTTP Range requests.

A SPHEREx L2 spectral image is ~60 MB (IMAGE, FLAGS, VARIANCE, ZODI, PSF, WCS-WAVE).
For a cutout we only need a band of rows from a few extensions, i.e. a few hundred kB.
"""
import numpy as np
from astropy.io import fits
from . import s3

BLOCK = 2880
_DT = {8: ">u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}


def _pad(n):
    return (n + BLOCK - 1) // BLOCK * BLOCK


class RemoteFITS:
    def __init__(self, key):
        self.key = key
        self.hdus = []  # list of dict(name, header, data_start, data_size)
        self._scanned = False

    def _read_header(self, offset):
        chunk = 0
        buf = b""
        while True:
            nxt = s3.get_range(self.key, offset + chunk, offset + chunk + 8 * BLOCK)
            buf += nxt
            # look for END card on 80-byte boundaries
            for i in range(max(0, chunk - 80), len(buf), 80):
                if buf[i:i + 8] == b"END     ":
                    hdr = fits.Header.fromstring(buf[: i + 80].decode("ascii", "replace"))
                    return hdr, offset + _pad(i + 80)
            chunk = len(buf)
            if len(nxt) < 8 * BLOCK:
                raise IOError("END card not found")

    def scan(self, upto=None):
        """Walk HDU headers (each is one small range read)."""
        off = 0 if not self.hdus else self.hdus[-1]["data_start"] + _pad(self.hdus[-1]["data_size"])
        while True:
            if upto is not None and len(self.hdus) > upto:
                return self
            try:
                hdr, dstart = self._read_header(off)
            except Exception:
                self._scanned = True
                return self
            naxis = hdr.get("NAXIS", 0)
            size = 0
            if naxis:
                size = abs(hdr["BITPIX"]) // 8
                for i in range(1, naxis + 1):
                    size *= hdr[f"NAXIS{i}"]
                size += hdr.get("PCOUNT", 0)
            name = hdr.get("EXTNAME", "PRIMARY" if not self.hdus else f"HDU{len(self.hdus)}")
            self.hdus.append(dict(name=name, header=hdr, data_start=dstart, data_size=size))
            off = dstart + _pad(size)
            if name == "WCS-WAVE":      # last extension of a SPHEREx L2 file
                self._scanned = True
                return self

    def hdu(self, name):
        for h in self.hdus:
            if h["name"] == name:
                return h
        idx = {"IMAGE": 1, "FLAGS": 2, "VARIANCE": 3, "ZODI": 4, "PSF": 5, "WCS-WAVE": 6}.get(name)
        self.scan(upto=idx)
        for h in self.hdus:
            if h["name"] == name:
                return h
        raise KeyError(name)

    def header(self, name="IMAGE"):
        return self.hdu(name)["header"]

    def read_rows(self, name, y0, y1, x0=None, x1=None):
        """Rows y0..y1-1 (0-based) of a 2-D image extension, optionally sliced in x."""
        h = self.hdu(name)
        hdr = h["header"]
        nx, ny = hdr["NAXIS1"], hdr["NAXIS2"]
        y0, y1 = max(0, y0), min(ny, y1)
        if y1 <= y0:
            return None
        bp = abs(hdr["BITPIX"]) // 8
        a = h["data_start"] + y0 * nx * bp
        b = h["data_start"] + y1 * nx * bp
        raw = s3.get_range(self.key, a, b, cache=True)
        arr = np.frombuffer(raw, dtype=_DT[hdr["BITPIX"]]).reshape(y1 - y0, nx)
        if x0 is not None:
            arr = arr[:, max(0, x0):min(nx, x1)]
        arr = arr.astype(np.float32 if hdr["BITPIX"] < 0 else np.int64)
        if "BSCALE" in hdr or "BZERO" in hdr:
            arr = arr * hdr.get("BSCALE", 1) + hdr.get("BZERO", 0)
        return arr

    def read_table_hdu(self, name):
        """Whole (small) extension as an astropy HDU, e.g. WCS-WAVE."""
        h = self.hdu(name)
        hdr = h["header"]
        raw = s3.get_range(self.key, h["data_start"], h["data_start"] + _pad(h["data_size"]))
        hstr = hdr.tostring()
        blob = hstr.encode() + raw
        return fits.BinTableHDU.fromstring(blob) if hdr["XTENSION"].strip() == "BINTABLE" else fits.ImageHDU.fromstring(blob)
