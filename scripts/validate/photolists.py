"""SPHEREx Level 2 photo lists (metadata only) for the five targets and comet 3I/ATLAS.

Run: cd scripts/validate && uv run python photolists.py [--no-upload] [--only barnard,...]

Targets: IRSA SIA (Irsa.query_sia), collections spherex_dr1 and spherex_qr3 (no *_deep), at the
Gaia/literature position moved to 2025.8 so fast movers stay inside. One row per image
(exposure x detector). A (obs_id, detector) present in both collections keeps the DR1 row.

Comet 3I/ATLAS: IRSA's table of every SPHEREx image of the comet, with NASA's comet position per
image (https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/obscore_3I_Atlas.vot, which
points at spherex_qr2). Each (obs_id, detector) is looked up with SIA (per session, position + time) to get the
current DR1 (else QR3) file and its S3 key.

Writes data/photolists/<slug>.csv and data/photolists/summary.json, and copies the CSVs to
gs://nasa-glazing-data/photolists/.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import astropy.units as u
import numpy as np
import requests
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.time import Time
from astroquery.ipac.irsa import Irsa

from _common import (
    BUCKET, CACHE, DATA, PHOTOLISTS, detector_from_bandpass, edge_distance_px, gcs_upload,
    jyear_to_mjd, parse_polygon, propagate, sessions, today, visits, write_json,
)

COLLECTIONS = ["spherex_dr1", "spherex_qr3"]  # DR1 first: it wins duplicates
SEARCH_EPOCH_YR = 2025.8
SEARCH_RADIUS_ARCSEC = 1.0
COMET_VOT = "https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/obscore_3I_Atlas.vot"
COMET_SLUG = "3i-atlas"

COLUMNS = [
    "slug", "collection", "obs_id", "detector", "energy_bandpassname", "em_min_um", "em_max_um",
    "t_min", "t_max", "mjd_mid", "visit", "session", "obj_ra", "obj_dec", "obj_pos_source",
    "edge_px", "s_ra", "s_dec", "access_url", "s3_bucket", "s3_key", "s3_region", "cloud_access",
    "access_estsize_kb", "obs_publisher_did", "qr2_datalink",
]


def _s(v):
    return "" if v is None or np.ma.is_masked(v) else str(v)


def _cloud(raw: str) -> tuple[str, str, str]:
    try:
        aws = json.loads(raw)["aws"]
        return aws.get("bucket_name", ""), aws.get("key", ""), aws.get("region", "")
    except (ValueError, KeyError, TypeError):
        return "", "", ""


def _sia_row(r) -> dict | None:
    """One SIA result row -> plain dict (science images only)."""
    if str(r["dataproduct_subtype"]) != "science" or str(r["dataproduct_type"]) != "image":
        return None
    bucket, s3key, region = _cloud(_s(r["cloud_access"]))
    return {
        "collection": str(r["obs_collection"]),
        "obs_id": str(r["obs_id"]),
        "detector": detector_from_bandpass(r["energy_bandpassname"]),
        "energy_bandpassname": str(r["energy_bandpassname"]),
        "em_min_um": round(float(r["em_min"]) * 1e6, 4),
        "em_max_um": round(float(r["em_max"]) * 1e6, 4),
        "t_min": float(r["t_min"]),
        "t_max": float(r["t_max"]),
        "s_ra": float(r["s_ra"]),
        "s_dec": float(r["s_dec"]),
        "s_region": str(r["s_region"]),
        "access_url": str(r["access_url"]),
        "s3_bucket": bucket, "s3_key": s3key, "s3_region": region,
        "cloud_access": _s(r["cloud_access"]),
        "access_estsize_kb": int(r["access_estsize"]),
        "obs_publisher_did": str(r["obs_publisher_did"]),
    }


def sia_rows(ra: float, dec: float) -> list[dict]:
    """SIA search in both collections, one dict per image, DR1 winning duplicates."""
    pos = SkyCoord(ra, dec, unit="deg")
    seen: dict[tuple[str, int], dict] = {}
    for col in COLLECTIONS:
        for attempt in (1, 2):
            try:
                t = Irsa.query_sia(pos=(pos, SEARCH_RADIUS_ARCSEC * u.arcsec), collection=col)
                break
            except Exception as e:  # noqa: BLE001 - retry once, then give up loudly
                print(f"    SIA {col} failed ({e}); {'retrying' if attempt == 1 else 'giving up'}")
                if attempt == 2:
                    raise
                time.sleep(5)
        n_dup = 0
        for r in t:
            d = _sia_row(r)
            if d is None:
                continue
            key = (d["obs_id"], d["detector"])
            if key in seen:
                n_dup += 1
                continue
            seen[key] = d
        print(f"    {col}: {len(t)} rows, {n_dup} already in an earlier collection")
    return list(seen.values())


def finish(slug: str, rows: list[dict]) -> list[dict]:
    """Add mjd_mid, visit and session (per position), sort by time, set the slug."""
    if not rows:
        return rows
    mid = np.array([(r["t_min"] + r["t_max"]) / 2.0 for r in rows])
    v, s = visits(mid), sessions(mid)
    for r, m, vi, si in zip(rows, mid, v, s):
        r.update(slug=slug, mjd_mid=round(float(m), 7), visit=int(vi), session=int(si))
    rows.sort(key=lambda r: (r["mjd_mid"], r["detector"]))
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{r[k]:.7f}" if isinstance(r.get(k), float) else r.get(k, "")) for k in COLUMNS})


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"images": 0, "exposures": 0, "visits": 0}
    out_v = []
    for vid in sorted({r["visit"] for r in rows}):
        rv = [r for r in rows if r["visit"] == vid]
        mids = [r["mjd_mid"] for r in rv]
        out_v.append({
            "visit": vid,
            "mjd_start": min(mids), "mjd_end": max(mids),
            "date_start": _date(min(mids)), "date_end": _date(max(mids)),
            "images": len(rv),
            "exposures": len({r["obs_id"] for r in rv}),
            "sessions": len({r["session"] for r in rv}),
            "detectors": sorted({r["detector"] for r in rv}),
            "collections": sorted({r["collection"] for r in rv}),
        })
    edge = [r["edge_px"] for r in rows if r.get("edge_px") is not None]
    return {
        "images": len(rows),
        "exposures": len({r["obs_id"] for r in rows}),
        "visits": len(out_v),
        "sessions": len({r["session"] for r in rows}),
        "date_start": _date(min(r["mjd_mid"] for r in rows)),
        "date_end": _date(max(r["mjd_mid"] for r in rows)),
        "detectors": sorted({r["detector"] for r in rows}),
        "images_per_detector": {str(d): sum(r["detector"] == d for r in rows)
                                for d in sorted({r["detector"] for r in rows})},
        "collections": {c: sum(r["collection"] == c for r in rows) for c in sorted({r["collection"] for r in rows})},
        "min_edge_px": round(min(edge), 1) if edge else None,
        "images_obj_outside": sum(1 for e in edge if e < 0),
        "per_visit": out_v,
    }


def _date(mjd: float) -> str:
    from astropy.time import Time
    return Time(mjd, format="mjd", scale="utc").to_value("iso", subfmt="date_hm")


def target_photolist(t: dict) -> list[dict]:
    mjd_s = jyear_to_mjd(SEARCH_EPOCH_YR)
    ra_s, dec_s = propagate(t["ra"], t["dec"], t["pmra"], t["pmdec"], t["ref_mjd"], mjd_s)
    print(f"  search position at {SEARCH_EPOCH_YR}: {float(ra_s):.6f} {float(dec_s):+.6f}")
    rows = finish(t["slug"], sia_rows(float(ra_s), float(dec_s)))
    for r in rows:
        ra, dec = propagate(t["ra"], t["dec"], t["pmra"], t["pmdec"], t["ref_mjd"], r["mjd_mid"])
        r.update(obj_ra=round(float(ra), 7), obj_dec=round(float(dec), 7),
                 obj_pos_source=f"{t['source']} moved to mjd_mid (linear pm)")
        r["edge_px"] = round(edge_distance_px(float(ra), float(dec), parse_polygon(r["s_region"])), 1)
    return rows


# --- comet ----------------------------------------------------------------------------------
def comet_table() -> Table:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / "obscore_3I_Atlas.vot"
    if not path.exists():
        r = requests.get(COMET_VOT, timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
    return Table.read(path)


def comet_lookup(ct: Table) -> dict[tuple[str, int], dict]:
    """(obs_id, detector) -> current DR1 (else QR3) SIA row for every image in the comet table.

    One SIA query per session (images under 10 min apart): a circle around the comet's track in that
    session plus the session's time range. (spherex.obscore over TAP has no cloud_access or size, and
    an SIA query by publisher_did took 130 s for two images, so positional + time queries are used.)
    Cached in .cache/comet_sia.json.
    """
    cache = CACHE / "comet_sia.json"
    if cache.exists():
        return {(r["obs_id"], r["detector"]): r for r in json.loads(cache.read_text())}
    t_min = np.array(ct["t_min"], dtype=float)
    sess = sessions(t_min)
    want = {(str(c["obs_id"]), int(str(c["spherex_detector"]).removeprefix("D"))) for c in ct}
    found: dict[tuple[str, int], dict] = {}
    for col in COLLECTIONS:
        for s in np.unique(sess):
            sel = sess == s
            keys = {(str(c["obs_id"]), int(str(c["spherex_detector"]).removeprefix("D"))) for c in ct[sel]}
            if keys <= found.keys():
                continue
            ra = np.array(ct["comet_ra"][sel], dtype=float)
            dec = np.array(ct["comet_dec"][sel], dtype=float)
            c0 = SkyCoord(np.mean(ra), np.mean(dec), unit="deg")
            rad = float(c0.separation(SkyCoord(ra, dec, unit="deg")).max().arcsec) + 30.0
            tr = (Time(t_min[sel].min() - 1e-3, format="mjd"), Time(np.array(ct["t_max"][sel], dtype=float).max() + 1e-3, format="mjd"))
            for attempt in (1, 2):
                try:
                    t = Irsa.query_sia(pos=(c0, rad * u.arcsec), time=tr, collection=col)
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"    SIA {col} session {s} failed ({e}); {'retrying' if attempt == 1 else 'skipping'}")
                    t = []
                    time.sleep(5)
            for r in t:
                d = _sia_row(r)
                if d and (d["obs_id"], d["detector"]) in want:
                    found.setdefault((d["obs_id"], d["detector"]), d)
            time.sleep(0.5)
        print(f"    after {col}: {len(found)} of {len(want)} images found")
    cache.write_text(json.dumps(list(found.values())))
    return found


def comet_photolist() -> list[dict]:
    ct = comet_table()
    print(f"  IRSA 3I/ATLAS table: {len(ct)} images ({sorted(set(ct['obs_collection']))})")
    look = comet_lookup(ct)
    rows, missing = [], []
    for c in ct:
        det = int(str(c["spherex_detector"]).removeprefix("D"))  # the table's detector column
        key = (str(c["obs_id"]), det)
        o = look.get(key)
        row = {
            "obs_id": key[0], "detector": det,
            "obj_ra": float(c["comet_ra"]), "obj_dec": float(c["comet_dec"]),
            "obj_pos_source": "IRSA 3I/ATLAS table comet_ra/comet_dec",
            "qr2_datalink": str(c["access_url"]),
        }
        if o is None:
            missing.append(key)
            row.update(collection=str(c["obs_collection"]), energy_bandpassname=f"SPHEREx-D{det}",
                       em_min_um=round(float(c["em_min"]) * 1e6, 4), em_max_um=round(float(c["em_max"]) * 1e6, 4),
                       t_min=float(c["t_min"]), t_max=float(c["t_max"]), s_ra=float(c["s_ra"]),
                       s_dec=float(c["s_dec"]), s_region=str(c["s_region"]), access_url=str(c["access_url"]),
                       s3_bucket="", s3_key="", s3_region="", cloud_access="",
                       access_estsize_kb=int(c["access_estsize"]),
                       obs_publisher_did=f"ivo://irsa.ipac/spherex_qr2?{key[0]}/D{det}")
        else:
            row.update({k: v for k, v in o.items() if k not in row})
            dt_s = abs((o["t_min"] - float(c["t_min"])) * 86400)
            if dt_s > 1:
                print(f"    note: {key} t_min differs by {dt_s:.1f} s between the QR2 table and {o['collection']}")
        row["edge_px"] = round(edge_distance_px(row["obj_ra"], row["obj_dec"], parse_polygon(row["s_region"])), 1)
        rows.append(row)
    if missing:
        print(f"    {len(missing)} images not found in DR1/QR3, kept with QR2 metadata: {missing[:5]}...")
    return finish(COMET_SLUG, rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated slugs (incl. 3i-atlas)")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}

    targets = json.loads((DATA / "targets.json").read_text())["targets"]
    summary_path = PHOTOLISTS / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    summary.setdefault("targets", {})

    jobs = [(t["slug"], lambda t=t: target_photolist(t)) for t in targets] + [(COMET_SLUG, comet_photolist)]
    for slug, fn in jobs:
        if only and slug not in only:
            continue
        print(f"{slug}")
        rows = fn()
        path = PHOTOLISTS / f"{slug}.csv"
        write_csv(path, rows)
        s = summarize(rows)
        summary["targets"][slug] = s
        print(f"  {s['images']} images, {s['exposures']} exposures, {s['visits']} visits, "
              f"detectors {s.get('detectors')}, {s.get('date_start')} -> {s.get('date_end')}; "
              f"{path.stat().st_size / 1e6:.2f} MB")
        for v in s.get("per_visit", []):
            print(f"    visit {v['visit']}: {v['date_start']} -> {v['date_end']}  {v['sessions']} sessions, "
                  f"{v['exposures']} exposures, {v['images']} images, D{v['detectors']}")
        if not args.no_upload:
            gcs_upload(path, f"{BUCKET}/photolists/{path.name}")

    summary.update(generated=today(), collections=COLLECTIONS, search_epoch=SEARCH_EPOCH_YR,
                   visit_rule="skyshift_core.visits (contracts/api.md)")
    write_json(summary_path, summary)
    if not args.no_upload:
        gcs_upload(summary_path, f"{BUCKET}/photolists/summary.json")


if __name__ == "__main__":
    main()
