"""Build web/public/data/spherex-now.json for the Sky map's "SPHEREx now" switch.

Reads the newest public SPHEREx batches straight from NASA's open bucket (anonymous HTTPS,
s3://nasa-irsa-spherex/qr3/level2/), one ranged GET per sampled photo: the IMAGE header's
CRVAL1/CRVAL2 (the field centre, CRPIX is the image centre) and MJD-AVG (UTC, exposure midpoint).
Each planning period is one half of a survey week (2026W42_1A, 2026W42_2A); IRSA uploads a whole
week in one go, so the S3 time of a period's first file is when that batch came out.

The browser turns the photos into the survey's pattern relative to the Sun and moves it with the
Sun (web/src/atlas/spherex-now.ts), so the file only needs recent real pointings.

    python scripts/precompute/spherex_now.py            # newest 6 weeks, every 4th detector-1 photo
    python scripts/precompute/spherex_now.py --weeks 8 --every 2

Standard library only. Re-run after IRSA publishes a new batch (about weekly), then deploy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BUCKET = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com"
PREFIX = "qr3/level2/"
HEADER_BYTES = 46080  # primary header plus the IMAGE header (16 FITS blocks)
OUT = Path(__file__).resolve().parents[2] / "web" / "public" / "data" / "spherex-now.json"


def listing(prefix: str, delimiter: bool = True) -> tuple[list[tuple[str, str]], list[str]]:
    """(keys with LastModified, common prefixes) under a prefix, all pages."""
    keys: list[tuple[str, str]] = []
    prefixes: list[str] = []
    token = None
    while True:
        q = {"list-type": "2", "prefix": prefix}
        if delimiter:
            q["delimiter"] = "/"
        if token:
            q["continuation-token"] = token
        xml = urllib.request.urlopen(f"{BUCKET}/?{urllib.parse.urlencode(q)}", timeout=60).read().decode()
        keys += re.findall(r"<Key>([^<]+)</Key>.*?<LastModified>([^<]+)</LastModified>", xml)
        prefixes += re.findall(r"<CommonPrefixes><Prefix>([^<]+)</Prefix>", xml)
        m = re.search(r"<NextContinuationToken>([^<]+)<", xml)
        if not m:
            return keys, prefixes
        token = m.group(1)


def card(header: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(f'{key:<8}')}= +'?([^'/]+?)'? *(/|$)", header, re.M)
    return m.group(1).strip() if m else None


def pointing(key: str) -> tuple[float, float, float] | None:
    """(ra, dec, mjd) of one photo from its IMAGE header, or None when it can't be read."""
    try:
        req = urllib.request.Request(f"{BUCKET}/{key}", headers={"Range": f"bytes=0-{HEADER_BYTES - 1}"})
        raw = urllib.request.urlopen(req, timeout=60).read().decode("latin1")
    except OSError:
        return None
    header = "\n".join(raw[i : i + 80] for i in range(0, len(raw), 80))
    at = header.find("EXTNAME = 'IMAGE")
    image = header[at:] if at >= 0 else header
    try:
        ra, dec = float(card(image, "CRVAL1") or "nan"), float(card(image, "CRVAL2") or "nan")
        mjd = float(card(image, "MJD-AVG") or card(header, "MJD-AVG") or "nan")
    except ValueError:
        return None
    if not all(map(lambda v: v == v, (ra, dec, mjd))):
        return None
    return ra, dec, mjd


def orbit() -> dict | None:
    """SPHEREx's latest mean orbit elements (NORAD 63182) from CelesTrak, or None when unreachable."""
    url = "https://celestrak.org/NORAD/elements/gp.php?CATNR=63182&FORMAT=JSON"
    try:
        gp = json.loads(urllib.request.urlopen(url, timeout=30).read().decode())[0]
    except (OSError, ValueError, IndexError, KeyError):
        print("CelesTrak didn't answer: no orbit elements this time", file=sys.stderr)
        return None
    epoch = datetime.fromisoformat(gp["EPOCH"]).replace(tzinfo=timezone.utc)
    return {
        "source": "CelesTrak GP, NORAD 63182 (SPHEREx)",
        "epoch_mjd": round(epoch.timestamp() / 86400 + 40587, 8),
        "inclination_deg": gp["INCLINATION"],
        "raan_deg": gp["RA_OF_ASC_NODE"],
        "eccentricity": gp["ECCENTRICITY"],
        "arg_perigee_deg": gp["ARG_OF_PERICENTER"],
        "mean_anomaly_deg": gp["MEAN_ANOMALY"],
        "mean_motion_rev_day": gp["MEAN_MOTION"],
        "mean_motion_dot": gp["MEAN_MOTION_DOT"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--weeks", type=int, default=6, help="newest survey weeks to read (two periods each)")
    ap.add_argument("--every", type=int, default=4, help="read every Nth detector-1 photo")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    periods = sorted(p for p in listing(PREFIX)[1] if re.search(r"/\d{4}W\d{2}_", p))[-2 * args.weeks :]
    batches, sample = [], []
    for period in periods:
        versions = [v for v in listing(period)[1] if "retry" not in v]
        files = [k for k in listing(period, delimiter=False)[0] if k[0].endswith(".fits") and any(k[0].startswith(v) for v in versions)]
        if not files:
            continue
        d1 = sorted(k for k, _ in files if any(k.startswith(f"{v}1/") for v in versions))
        published = min(t for _, t in files)
        batches.append({"period": period[len(PREFIX) : -1], "files": len(files), "published": published.replace(".000Z", "Z"), "keys": d1[:: args.every]})
        print(f"{period}: {len(files)} files, published {published}", file=sys.stderr)

    with ThreadPoolExecutor(48) as pool:
        for b in batches:
            got = [p for p in pool.map(pointing, b.pop("keys")) if p]
            b["first_mjd"] = round(min(p[2] for p in got), 4) if got else None
            b["last_mjd"] = round(max(p[2] for p in got), 4) if got else None
            sample += got

    sample.sort(key=lambda p: p[2])
    doc = {
        "orbit": orbit(),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "s3://nasa-irsa-spherex/qr3/level2/, IMAGE header CRVAL1/CRVAL2 (field centre) and MJD-AVG",
        "sample": f"every {args.every}th detector-1 photo of the newest {len(batches)} planning periods",
        "newest_mjd": round(sample[-1][2], 4),
        "batches": batches,
        "photos": [[round(ra, 2), round(dec, 2), round(mjd, 4)] for ra, dec, mjd in sample],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        f.write(json.dumps({k: v for k, v in doc.items() if k != "photos"}, indent=1)[:-2])
        f.write(',\n "photos": [\n')
        f.write(",\n".join(json.dumps(p, separators=(",", ":")) for p in doc["photos"]))
        f.write("\n ]\n}\n")
    print(f"wrote {args.out} ({len(sample)} photos, newest MJD {doc['newest_mjd']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
