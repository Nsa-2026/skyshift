"""IRSA MOST: SPHEREx exposures and predicted positions for asteroids numbered 1-60 (input for V4).

Run: cd scripts/validate && uv run python most_asteroids.py [--first 1 --last 60] [--no-upload]

One MOST request per asteroid (catalog=spherex, output_mode=Brief, input_type=name_input with the
number, never the name), sequential, 1.5 s apart, retried once, failures skipped and logged.
Raw answers are cached in .cache/most/<n>.tbl, so a rerun only asks for what is missing.

Writes data/most_1_60.csv (one row per image = exposure x detector), data/most_1_60_summary.json,
data/most_1_60_failures.log, and copies the CSV and summary to gs://nasa-glazing-data/results/.

Columns: number, name, obs_id, detector, mjd_start, mjd_end, mjd_mid, ra_obj, dec_obj (MOST's
predicted position), vmag, sun_dist, geo_dist (au), phase (deg), dist_ctr (deg from image centre),
image_ra, image_dec, release, access_url, s3_key, rate_ra_arcsec_h (x cos dec), rate_dec_arcsec_h.
MOST Brief returns no rates: the rates here are derived from MOST's own positions in the nearest
other exposure of the same asteroid within 0.5 d (empty when there is none).
"""

from __future__ import annotations

import argparse
import csv
import json
import time

import numpy as np
import requests
from astropy.table import Table

from _common import BUCKET, CACHE, DATA, gcs_upload, today, write_json

URL = "https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most"
OBS_BEGIN, OBS_END = "2025 04 01", "2026 11 13"  # MOST clamps this to the SPHEREx data it holds
EPHEM_STEP_D = 1.0  # same images and positions as the 0.25 d default for Ceres and Vesta (checked 14 Nov)
PAUSE_S = 1.5
TIMEOUT_S = 600
HEADERS = {"User-Agent": "SkyShift-validate/0.1 (NASA Space Apps 2026)"}
IBE_ROOT = "https://irsa.ipac.caltech.edu/"

COLUMNS = ["number", "name", "obs_id", "detector", "mjd_start", "mjd_end", "mjd_mid", "ra_obj", "dec_obj",
           "vmag", "sun_dist", "geo_dist", "phase", "dist_ctr", "image_ra", "image_dec", "release",
           "access_url", "s3_key", "rate_ra_arcsec_h", "rate_dec_arcsec_h"]


def fetch(n: int, log: list[str]) -> str | None:
    """Raw MOST answer for asteroid n, from cache or the network (retry once). None on failure."""
    cdir = CACHE / "most"
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / f"{n}.tbl"
    if path.exists():
        return path.read_text()
    params = {"catalog": "spherex", "input_type": "name_input", "obj_name": str(n),
              "obs_begin": OBS_BEGIN, "obs_end": OBS_END, "ephem_step": str(EPHEM_STEP_D),
              "output_mode": "Brief"}
    for attempt in (1, 2):
        t0 = time.time()
        try:
            r = requests.get(URL, params=params, headers=HEADERS, timeout=TIMEOUT_S)
            r.raise_for_status()
            text = r.text
            if "|" not in text or ("error" in text[:2000].lower() and "\\object_name" not in text):
                raise ValueError(f"no table in answer: {text[:200]!r}")
            path.write_text(text)
            print(f"  {n}: {time.time() - t0:.1f} s")
            time.sleep(PAUSE_S)
            return text
        except (requests.RequestException, ValueError) as e:
            msg = f"{n}: attempt {attempt} failed after {time.time() - t0:.0f} s: {e}"
            print("  " + msg)
            log.append(msg)
            time.sleep(PAUSE_S * 2)
    return None


def parse(n: int, text: str) -> tuple[str, list[dict]]:
    t = Table.read(text, format="ascii.ipac")
    name = str(t.meta.get("keywords", {}).get("object_name", {}).get("value", "")).strip()
    rows = []
    for r in t:
        uri = str(r["uri"])
        mjd_s, mjd_e = float(r["mjd_start"]), float(r["mjd_end"])
        key = uri.removeprefix("ibe/data/spherex/")
        rows.append({
            "number": n, "name": name, "obs_id": str(r["observationid"]),
            "detector": int(str(r["productid"]).removeprefix("D")),  # MOST's productid column, 'D1'..'D6'
            "mjd_start": mjd_s, "mjd_end": mjd_e, "mjd_mid": (mjd_s + mjd_e) / 2,
            "ra_obj": float(r["ra_obj"]), "dec_obj": float(r["dec_obj"]),
            "vmag": None if np.ma.is_masked(r["vmag"]) else float(r["vmag"]),
            "sun_dist": float(r["sun_dist"]), "geo_dist": float(r["geo_dist"]), "phase": float(r["phase"]),
            "dist_ctr": float(r["dist_ctr"]), "image_ra": float(r["ra"]), "image_dec": float(r["dec"]),
            "release": key.split("/", 1)[0],  # 'dr1' or 'qr3': top folder of IRSA's archive path
            "access_url": IBE_ROOT + uri, "s3_key": key,
        })
    return name, rows


def add_rates(rows: list[dict]) -> None:
    """Rate from MOST's positions in the nearest other exposure (within 0.5 d), arcsec/h."""
    expo: dict[str, tuple[float, float, float]] = {}
    for r in rows:
        expo.setdefault(r["obs_id"], (r["mjd_mid"], r["ra_obj"], r["dec_obj"]))
    keys = sorted(expo, key=lambda k: expo[k][0])
    t = np.array([expo[k][0] for k in keys])
    rate: dict[str, tuple[float, float]] = {}
    for i, k in enumerate(keys):
        d = np.abs(t - t[i])
        d[i] = np.inf
        j = int(np.argmin(d)) if len(keys) > 1 else -1
        if j < 0 or d[j] > 0.5 or d[j] == 0:
            continue
        (t1, ra1, de1), (t2, ra2, de2) = expo[keys[min(i, j)]], expo[keys[max(i, j)]]
        dra = ((ra2 - ra1 + 180) % 360 - 180) * np.cos(np.radians((de1 + de2) / 2)) * 3600
        hours = (t2 - t1) * 24
        rate[k] = (dra / hours, (de2 - de1) * 3600 / hours)
    for r in rows:
        rr = rate.get(r["obs_id"])
        r["rate_ra_arcsec_h"], r["rate_dec_arcsec_h"] = (round(rr[0], 3), round(rr[1], 3)) if rr else (None, None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int, default=60)
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()

    log: list[str] = []
    all_rows: list[dict] = []
    per: list[dict] = []
    for n in range(args.first, args.last + 1):
        text = fetch(n, log)
        if text is None:
            per.append({"number": n, "status": "failed"})
            continue
        try:
            name, rows = parse(n, text)
        except Exception as e:  # noqa: BLE001 - skip and log
            log.append(f"{n}: parse failed: {e}")
            per.append({"number": n, "status": "parse_failed"})
            continue
        add_rates(rows)
        rows.sort(key=lambda r: (r["mjd_mid"], r["detector"]))
        all_rows += rows
        v = [r["vmag"] for r in rows if r["vmag"] is not None]
        per.append({"number": n, "name": name, "status": "ok", "images": len(rows),
                    "exposures": len({r["obs_id"] for r in rows}),
                    "vmag_min": min(v) if v else None, "vmag_max": max(v) if v else None})

    csv_path = DATA / "most_1_60.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: ("" if r[k] is None else (f"{r[k]:.7f}" if k.startswith("mjd") else r[k]))
                        for k in COLUMNS})

    ok = [p for p in per if p["status"] == "ok"]
    seen = [p for p in ok if p["images"] > 0]
    v = [r["vmag"] for r in all_rows if r["vmag"] is not None]
    summary = {
        "generated": today(),
        "query": {"url": URL, "catalog": "spherex", "output_mode": "Brief", "input_type": "name_input (number)",
                  "obs_begin": OBS_BEGIN, "obs_end": OBS_END, "ephem_step_d": EPHEM_STEP_D},
        "asked": len(per), "answered": len(ok), "failed": [p["number"] for p in per if p["status"] != "ok"],
        "asteroids_with_exposures": len(seen),
        "asteroids_without": [p["number"] for p in ok if p["images"] == 0],
        "images": len(all_rows),
        "exposures": len({(r["number"], r["obs_id"]) for r in all_rows}),
        "vmag_min": min(v) if v else None, "vmag_max": max(v) if v else None,
        "vmag_median": float(np.median(v)) if v else None,
        "images_with_v_12_15": sum(1 for x in v if 12 <= x <= 15),
        "releases": {k: sum(r["release"] == k for r in all_rows) for k in sorted({r["release"] for r in all_rows})},
        "per_asteroid": per,
    }
    sum_path = DATA / "most_1_60_summary.json"
    write_json(sum_path, summary)
    (DATA / "most_1_60_failures.log").write_text("\n".join(log) + ("\n" if log else ""))
    print(f"{summary['asteroids_with_exposures']}/{summary['asked']} asteroids with SPHEREx images; "
          f"{summary['images']} images, {summary['exposures']} exposures; V {summary['vmag_min']}-{summary['vmag_max']}; "
          f"failed {summary['failed']}; CSV {csv_path.stat().st_size / 1e6:.2f} MB")
    if not args.no_upload:
        gcs_upload(csv_path, f"{BUCKET}/results/{csv_path.name}")
        gcs_upload(sum_path, f"{BUCKET}/results/{sum_path.name}")


if __name__ == "__main__":
    main()
