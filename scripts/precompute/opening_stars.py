"""Real Gaia DR3 stars for the opening's flight (web/src/intro/stars.ts).

Writes web/public/opening/stars-gaia.bin: every Gaia DR3 star with G < G_MAX, a good parallax
(parallax > 0, parallax_over_error > 5) and a position inside the opening's SPHEREx backdrop
(a 50-degree SIN view at RA 281, Dec -4, 1600 x 1000 px, 32 px per degree, east left), plus a
margin. Stars only: no positions, distances or colours are invented.

Source: IRSA's copy of Gaia DR3 (table gaia_dr3_source). IRSA TAP (ADQL) first; when it fails,
IRSA Gator, which serves the same table with the same constraints. Not CDS/VizieR.
Gaia data: ESA/Gaia/DPAC, CC BY-SA 3.0 IGO.

Stdlib only, from the repository root:
    python3 -I scripts/precompute/opening_stars.py [--gmax 10.25] [--table cached.tbl]

File layout (little-endian), documented in web/public/opening/README.md:
    0   8 bytes  magic b"GDR3STAR"
    8   uint32   version (1)
    12  uint32   count n, stars sorted by G (brightest first)
    16  float32  ra_min, ra_span, dec_min, dec_span        (degrees, ICRS, epoch 2016.0)
    32  float32  lnplx_min, lnplx_span                       (natural log of parallax in mas)
    40  float32  g_min, g_span, bprp_min, bprp_span          (magnitudes)
    56  float32  ra0, dec0                                   (backdrop centre, degrees)
    64  uint16[n] ra  = ra_min + q / 65535 * ra_span
        uint16[n] dec = dec_min + q / 65535 * dec_span
        uint16[n] parallax = exp(lnplx_min + q / 65535 * lnplx_span)
        uint8[n]  G = g_min + q / 255 * g_span
        uint8[n]  bp_rp = bprp_min + (q - 1) / 254 * bprp_span; q = 0: no bp_rp
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web/public/opening/stars-gaia.bin"
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
IRSA_GATOR = "https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-query"
TABLE = "gaia_dr3_source"

# The backdrop (web/public/opening/spherex-sky.webp): SIN projection, centre RA 281, Dec -4,
# 1600 x 1000 px at 32 px per degree, so +-25 x +-15.6 degrees. Fitted on 287 stars against
# Gaia: scale and centre agree to 0.05% and 0.6 px.
RA0, DEC0 = 281.0, -4.0
HALF_X, HALF_Y = 26.5, 17.0  # backdrop plus a margin for the sky's turn (SIN degrees)
# The query box encloses that frame; stars are cut to the frame locally.
RA_BOX, DEC_BOX = (245.0, 317.0), (-37.0, 29.0)
G_QUERY = 10.5
G_MAX = 10.25  # about 36,000 stars, under 300 KB
MAGIC, VERSION = b"GDR3STAR", 1
COLUMNS = ("ra", "dec", "parallax", "phot_g_mean_mag", "bp_rp")
CONSTRAINTS = (f"phot_g_mean_mag < {G_QUERY} AND parallax > 0 AND parallax_over_error > 5 "
               f"AND ra BETWEEN {RA_BOX[0]} AND {RA_BOX[1]} AND dec BETWEEN {DEC_BOX[0]} AND {DEC_BOX[1]}")
ADQL = f"SELECT {', '.join(COLUMNS)} FROM {TABLE} WHERE {CONSTRAINTS}"


def http(url: str, params: dict[str, str], timeout: float = 900) -> str:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "skyshift-opening-stars/1"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", "replace")


def from_tap() -> list[tuple[float, ...]]:
    tables = http(IRSA_TAP, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                             "QUERY": f"SELECT table_name FROM TAP_SCHEMA.tables WHERE table_name = '{TABLE}'"}, 120)
    if TABLE not in tables:
        raise RuntimeError(f"IRSA TAP has no table {TABLE}: {tables[:200]!r}")
    text = http(IRSA_TAP, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": ADQL})
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "ra" not in rows[0]:
        raise RuntimeError(f"IRSA TAP answered without rows: {text[:200]!r}")
    return [tuple(num(r[c]) for c in COLUMNS) for r in rows]


def from_gator() -> str:
    gator = CONSTRAINTS.replace(" AND ", " and ").replace("BETWEEN", "between")
    return http(IRSA_GATOR, {"catalog": TABLE, "spatial": "NONE", "outfmt": "1",
                             "selcols": ",".join(COLUMNS), "constraints": gator})


def parse_ipac(text: str) -> list[tuple[float, ...]]:
    """An IPAC table from Gator: the header row names the columns; Gator adds clon/clat."""
    names: list[str] | None = None
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("\\"):
            continue
        if line.startswith("|"):
            names = names or [c.strip() for c in line.strip("|").split("|")]
            continue
        if names is None:
            raise RuntimeError(f"not an IPAC table: {text[:200]!r}")
        cells = dict(zip(names, line.split()))
        rows.append(tuple(num(cells[c]) for c in COLUMNS))
    if not rows:
        raise RuntimeError(f"no rows: {text[:300]!r}")
    return rows


def num(s: str) -> float:
    s = s.strip()
    return math.nan if s in ("", "null", "NaN", "nan") else float(s)


def sin_xy(ra: float, dec: float) -> tuple[float, float, float]:
    """SIN (orthographic) coordinates in degrees around (RA0, DEC0), and cos of the distance."""
    a, d, a0, d0 = map(math.radians, (ra, dec, RA0, DEC0))
    x = math.cos(d) * math.sin(a - a0)
    y = math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    return math.degrees(x), math.degrees(y), cosc


def select(rows, gmax: float):
    keep = []
    for ra, dec, plx, g, bprp in rows:
        if not (g < gmax and plx > 0):
            continue
        x, y, cosc = sin_xy(ra, dec)
        if cosc > 0 and abs(x) <= HALF_X and abs(y) <= HALF_Y:
            keep.append((ra, dec, plx, g, bprp))
    keep.sort(key=lambda r: (r[3], r[0], r[1]))  # by G; ties in a fixed order, whatever the server sends
    return keep


def quantise(values, lo: float, span: float, top: int) -> list[int]:
    return [max(0, min(top, round((v - lo) / span * top))) for v in values]


def encode(stars) -> bytes:
    ra = [s[0] for s in stars]; dec = [s[1] for s in stars]
    lnplx = [math.log(s[2]) for s in stars]; g = [s[3] for s in stars]
    bprp = [s[4] for s in stars if not math.isnan(s[4])]
    ranges = []
    for v in (ra, dec, lnplx, g, bprp):
        lo, hi = min(v), max(v)
        ranges += [lo, max(hi - lo, 1e-6)]
    (ra_lo, ra_sp, dec_lo, dec_sp, pl_lo, pl_sp, g_lo, g_sp, c_lo, c_sp) = ranges
    head = MAGIC + struct.pack("<II", VERSION, len(stars)) + struct.pack("<12f", *ranges, RA0, DEC0)
    assert len(head) == 64
    colour = [0 if math.isnan(s[4]) else 1 + round((s[4] - c_lo) / c_sp * 254) for s in stars]
    n = len(stars)
    return (head
            + struct.pack(f"<{n}H", *quantise(ra, ra_lo, ra_sp, 65535))
            + struct.pack(f"<{n}H", *quantise(dec, dec_lo, dec_sp, 65535))
            + struct.pack(f"<{n}H", *quantise(lnplx, pl_lo, pl_sp, 65535))
            + struct.pack(f"<{n}B", *quantise(g, g_lo, g_sp, 255))
            + struct.pack(f"<{n}B", *colour))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gmax", type=float, default=G_MAX)
    ap.add_argument("--table", type=Path, help="a Gator IPAC table saved earlier (skips the network)")
    ap.add_argument("--save", type=Path, help="keep the downloaded table here")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    if args.gmax > G_QUERY:
        ap.error(f"--gmax must be <= {G_QUERY}, the query limit")

    if args.table:
        rows, source = parse_ipac(args.table.read_text()), f"IRSA Gator table {args.table.name}"
    else:
        try:
            rows, source = from_tap(), f"IRSA TAP {IRSA_TAP}"
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            print(f"IRSA TAP failed ({e}); trying IRSA Gator", file=sys.stderr)
            text = from_gator()
            if args.save:
                args.save.write_text(text)
            rows, source = parse_ipac(text), f"IRSA Gator {IRSA_GATOR}"
    stars = select(rows, args.gmax)
    blob = encode(stars)
    args.out.write_bytes(blob)
    near = sum(1 for s in stars if s[2] > 20)
    print(f"source: {source}, {time.strftime('%Y-%m-%d')}")
    print(f"query: {ADQL}")
    print(f"rows {len(rows)}, kept {len(stars)} with G < {args.gmax} in the frame; "
          f"{near} nearer than 50 pc; brightest G {stars[0][3]:.2f}")
    print(f"wrote {args.out} ({len(blob)} bytes, sha256 {hashlib.sha256(blob).hexdigest()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
