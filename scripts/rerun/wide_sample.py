#!/usr/bin/env python3
"""The WIDE test sample: SPHEREx sessions spread over every kind of sky condition, so the extraction is tested where
the bench (sessions around known objects) and the hold-out never went, before the one whole-sky run.

Run from the repo root (it needs the pipeline's packages):
    uv run --project pipeline python scripts/rerun/wide_sample.py <command> [options]

Commands (each writes under <data>/rerun/wide-sample/ unless said otherwise; nothing reads or writes gs://)
    gaia-counts   uncapped Gaia G < 17.5 density for the forced-list partitions that hit the cap (refcat/sky/forced_n32
                  keeps at most 8,221 rows = 2,449/deg² per nside-32 partition): one IRSA TAP count in a 0.3° circle at
                  each capped partition's centre (free). Resumable -> gaia_capped_counts.parquet
    conditions    the conditions of every photo of the S3 index (latest l2b version), from the index alone (no photo
                  read): detector, MJD, centre; ecliptic latitude/longitude, solar elongation and the Sun's ecliptic
                  longitude at that time (zodiacal glow), galactic l/b, Gaia G < 17.5 density near the centre (the forced
                  list's partition count; capped partitions get the IRSA count, `gaia_capped` says which), depth
                  (exposures of all detectors with their centre within 1.75° of this centre, / 6), and whether the
                  photo is in one of the 333 sky zones or in the bench / hold-out -> conditions.parquet,
                  conditions.json, conditions.md
    sample        the stratified sample of whole sessions: sample zones w_<name> (a small box at each chosen spot, its
                  photo list made by the pipeline's own photolist step from the S3 index, written as NEW files
                  photolists/zones/w_<name>.parquet), sessions picked over time and never one that holds a bench or
                  hold-out photo -> scripts/rerun/wide_targets.json, sample.parquet, sample.md
    truth         for every sample zone: refcat/w_<name>.parquet (+ _forced; Gaia G < 20 from IRSA/ESA, CatWISE/2MASS or
                  AllWISE from S3) and the known bodies of the sampled sessions (cache/mpc/w_<name>/ with kete and the MPC
                  orbits, cache/planets/w_<name>/ from JPL Horizons), with the pipeline's own refcat / known_mpc /
                  known_planets code
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rerun as R  # noqa: E402

OUT = "rerun/wide-sample"
WIDE_TARGETS = HERE / "wide_targets.json"
OBLIQUITY = np.deg2rad(23.4392911)  # J2000 mean obliquity
IRSA_SYNC = "https://irsa.ipac.caltech.edu/TAP/sync"
COUNT_RADIUS_DEG = 0.3
DEPTH_RADIUS_DEG = 1.75  # half a photo's side: a photo whose centre is this close covers the spot
N_DET = 6


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} wide: {msg}", flush=True)


def out_dir(data: Path) -> Path:
    d = data / OUT
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- geometry -------------------------------------------------------------------------------------------------------


def ecliptic(ra_deg, dec_deg) -> tuple[np.ndarray, np.ndarray]:
    """J2000 mean ecliptic (lon, lat) in degrees from ICRS ra, dec."""
    a, d = np.deg2rad(np.asarray(ra_deg, float)), np.deg2rad(np.asarray(dec_deg, float))
    sb = np.sin(d) * np.cos(OBLIQUITY) - np.cos(d) * np.sin(OBLIQUITY) * np.sin(a)
    lat = np.arcsin(np.clip(sb, -1, 1))
    lon = np.arctan2(np.sin(a) * np.cos(OBLIQUITY) + np.tan(d) * np.sin(OBLIQUITY), np.cos(a))
    return np.rad2deg(lon) % 360.0, np.rad2deg(lat)


def sun_lon(mjd) -> np.ndarray:
    """The Sun's apparent ecliptic longitude (deg), the Astronomical Almanac's low-precision formula (0.01°)."""
    n = np.asarray(mjd, float) - 51544.5
    L = (280.460 + 0.9856474 * n) % 360.0
    g = np.deg2rad((357.528 + 0.9856003 * n) % 360.0)
    return (L + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g)) % 360.0


def elongation(lon, lat, lsun) -> np.ndarray:
    """Angle (deg) between the direction (lon, lat) and the Sun (lsun, 0)."""
    c = np.cos(np.deg2rad(lat)) * np.cos(np.deg2rad(np.asarray(lon) - np.asarray(lsun)))
    return np.rad2deg(np.arccos(np.clip(c, -1, 1)))


def galactic(ra_deg, dec_deg) -> tuple[np.ndarray, np.ndarray]:
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    g = SkyCoord(np.asarray(ra_deg, float) * u.deg, np.asarray(dec_deg, float) * u.deg, frame="icrs").galactic
    return g.l.deg, g.b.deg


def healpix(nside: int):
    from astropy_healpix import HEALPix

    return HEALPix(nside=nside, order="nested")


def pix_of(h, ra, dec) -> np.ndarray:
    import astropy.units as u

    return h.lonlat_to_healpix(np.asarray(ra, float) * u.deg, np.asarray(dec, float) * u.deg)


def depth_counts(ra, dec, nside: int = 64, radius: float = DEPTH_RADIUS_DEG) -> np.ndarray:
    """For each position, the number of positions (photo centres) within `radius` degrees, on a HEALPix grid:
    centres counted per pixel, then summed over the pixels whose centre is within the radius of each pixel."""
    import astropy.units as u

    h = healpix(nside)
    p = pix_of(h, ra, dec)
    counts = np.bincount(p, minlength=h.npix)
    uniq = np.unique(p)
    lon, lat = h.healpix_to_lonlat(uniq)
    tot = np.empty(len(uniq), np.int64)
    for k, (lo, la) in enumerate(zip(lon.deg, lat.deg)):
        tot[k] = counts[h.cone_search_lonlat(lo * u.deg, la * u.deg, radius * u.deg)].sum()
    return tot[np.searchsorted(uniq, p)]


# --- Gaia density -----------------------------------------------------------------------------------------------------


def forced_manifest(data: Path) -> dict:
    return json.loads((data / "refcat" / "sky" / "forced_n32" / "_complete.json").read_text())


def irsa_count(ra: float, dec: float, radius: float = COUNT_RADIUS_DEG, g_max: float = 17.5) -> int:
    import requests

    q = (f"SELECT COUNT(*) AS n FROM gaia_dr3_source WHERE CONTAINS(POINT(ra,dec),CIRCLE({ra:.5f},{dec:.5f},{radius}))=1"
         f" AND phot_g_mean_mag < {g_max}")
    for attempt in range(1, 5):
        try:
            r = requests.get(IRSA_SYNC, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": q},
                             timeout=300)
            r.raise_for_status()
            return int(r.text.split()[1])
        except Exception:  # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(10 * attempt)
    raise RuntimeError("unreachable")


def cmd_gaia_counts(a) -> None:
    data = Path(a.data)
    m = forced_manifest(data)
    rows = np.asarray(m["rows_per_partition"])
    capped = np.where(rows >= int(m["cap_per_partition"]))[0]
    h = healpix(int(m["nside"]))
    dest = out_dir(data) / "gaia_capped_counts.parquet"
    have = pd.read_parquet(dest) if dest.exists() else pd.DataFrame(columns=["pix", "ra", "dec", "n", "per_deg2"])
    todo = sorted(set(int(p) for p in capped) - set(have["pix"].astype(int)))
    log(f"gaia-counts: {len(capped)} capped partitions, {len(todo)} to count, {a.threads} threads")
    area = np.pi * COUNT_RADIUS_DEG ** 2
    done = [have] if len(have) else []
    t0, new = time.time(), []

    def one(p: int) -> dict:
        lon, lat = h.healpix_to_lonlat(p)
        n = irsa_count(float(lon.deg), float(lat.deg))
        return {"pix": p, "ra": float(lon.deg), "dec": float(lat.deg), "n": n, "per_deg2": n / area}

    with ThreadPoolExecutor(max_workers=a.threads) as ex:
        futs = [ex.submit(one, p) for p in todo]
        for k, f in enumerate(as_completed(futs), 1):
            try:
                new.append(f.result())
            except Exception as e:  # noqa: BLE001
                log(f"gaia-counts: failed {str(e)[:120]}")
            if k % 200 == 0 or k == len(todo):
                pd.concat(done + [pd.DataFrame(new)], ignore_index=True).to_parquet(dest)
                log(f"gaia-counts: {k}/{len(todo)} in {time.time() - t0:.0f} s")
    if new or not dest.exists():
        pd.concat(done + [pd.DataFrame(new)], ignore_index=True).to_parquet(dest)


def gaia_density(data: Path, ra, dec) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(G < 17.5 per deg² from the forced list, capped flag, best estimate: the IRSA count where capped)."""
    m = forced_manifest(data)
    rows = np.asarray(m["rows_per_partition"], float)
    h = healpix(int(m["nside"]))
    import astropy.units as u

    area = h.pixel_area.to(u.deg ** 2).value
    p = pix_of(h, ra, dec)
    forced = rows[p] / area
    capped = rows[p] >= int(m["cap_per_partition"])
    best = forced.copy()
    f = out_dir(data) / "gaia_capped_counts.parquet"
    if f.exists():
        c = pd.read_parquet(f)
        lut = np.full(h.npix, np.nan)
        lut[c["pix"].astype(int).to_numpy()] = c["per_deg2"].to_numpy(float)
        est = lut[p]
        best = np.where(capped & np.isfinite(est), np.maximum(est, forced), np.where(capped, np.nan, forced))
    else:
        best = np.where(capped, np.nan, forced)
    return forced, capped, best


# --- the conditions table ------------------------------------------------------------------------------------------


def load_index(data: Path) -> pd.DataFrame:
    """Every Level 2 photo, latest l2b version (rerun.latest_versions), with its centre and file size."""
    fs = sorted((data / "photolists" / "s3index").glob("*/*.parquet"))
    cols = ["obs_id", "detector", "collection", "s3_key", "size_bytes", "mjd", "s_ra", "s_dec"]
    idx = pd.concat([pd.read_parquet(f, columns=cols) for f in fs], ignore_index=True)
    return R.latest_versions(idx)


def sky_zone_photos(data: Path) -> pd.DataFrame:
    """(obs_id, detector) of every photo in the 333 sky zones (hunt + the ecliptic zones)."""
    names = R.sky_zone_names(data, "sky")
    parts = [pd.read_parquet(data / "photolists" / "zones" / f"{z}.parquet", columns=["obs_id", "detector"])
             for z in names]
    log(f"sky zones: {len(names)} lists")
    return pd.concat(parts, ignore_index=True).drop_duplicates()


def used_photos(data: Path, target_files: list[Path]) -> pd.DataFrame:
    """(obs_id, detector, zone, session, set) of every photo the bench and hold-out sessions hold."""
    parts = []
    for f in target_files:
        for zone, rows, _ in R.bench_photos(data, json.loads(f.read_text())["targets"]):
            parts.append(rows[["obs_id", "detector", "session"]].assign(zone=zone, set=f.stem.replace("_targets", "")))
    return pd.concat(parts, ignore_index=True)


def mission_month(mjd) -> np.ndarray:
    """Whole months (30.44 d) since the first photo (MJD 60789, 2025-04-26)."""
    return np.floor((np.asarray(mjd, float) - MISSION_T0) / 30.44).astype(int)


MISSION_T0 = 60789.0
# The condition cells (edges); the last bin is open.
BETA_BINS = [0, 5, 15, 30, 60, 80, 90.01]
B_BINS = [0, 2, 5, 10, 20, 45, 90.01]
GAIA_BINS = [0, 300, 700, 1500, 2449, 5000, 1e9]  # G < 17.5 per deg²; 2,449 = the forced list's cap
DEPTH_BINS = [0, 60, 100, 150, 250, 500, 1000, 1e9]
ELONG_BINS = [0, 80, 85, 90, 95, 100, 180]


def lmc_smc(ra, dec) -> np.ndarray:
    """'lmc' / 'smc' / '' from the photo centre (LMC within 4° of (80.9, -69.8), SMC within 2.5° of (13.2, -72.8))."""
    from skyshift_pipeline.sky import angsep

    out = np.full(len(ra), "", dtype=object)
    out[angsep(80.894, -69.756, ra, dec) < 4.0] = "lmc"
    out[angsep(13.187, -72.829, ra, dec) < 2.5] = "smc"
    return out


def cell_table(df: pd.DataFrame) -> pd.DataFrame:
    """Each photo's condition cell labels."""
    def lab(x, bins):
        names = [f"{lo:g}–{hi:g}" if hi < 1e8 else f">={lo:g}" for lo, hi in zip(bins[:-1], bins[1:])]
        names = [n.replace("–90.01", "–90") for n in names]
        return pd.cut(x, bins, right=False, labels=names)

    return pd.DataFrame({
        "beta": lab(df["ecl_lat"].abs(), BETA_BINS), "elong": lab(df["elong"], ELONG_BINS),
        "gal_b": lab(df["gal_b"].abs(), B_BINS),
        "gaia": lab(df["gaia_per_deg2"].fillna(df["gaia_forced_per_deg2"]), GAIA_BINS),
        "depth": lab(df["depth"], DEPTH_BINS),
        "region": df["region"], "month": df["month"], "in_sky_zones": df["in_sky_zones"],
    }, index=df.index)


def region(df: pd.DataFrame) -> np.ndarray:
    """One named region per photo: lmc, smc, bulge (|l| < 15°, |b| < 10°), plane (|b| < 5°), nep / sep (|β| > 85°),
    highlat (|b| > 45°), else ''."""
    l180 = (df["gal_l"].to_numpy() + 180) % 360 - 180
    b, beta = df["gal_b"].to_numpy(), df["ecl_lat"].to_numpy()
    r = np.full(len(df), "", dtype=object)
    r[np.abs(b) > 45] = "highlat"
    r[beta > 85] = "nep"
    r[beta < -85] = "sep"
    r[np.abs(b) < 5] = "plane"
    r[(np.abs(l180) < 15) & (np.abs(b) < 10)] = "bulge"
    mc = lmc_smc(df["s_ra"].to_numpy(), df["s_dec"].to_numpy())
    r[mc != ""] = mc[mc != ""]
    return r


def describe(x: pd.Series, q=(0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1)) -> dict:
    x = pd.to_numeric(x, errors="coerce").dropna()
    return {f"p{int(k * 100)}": round(float(x.quantile(k)), 2) for k in q} | {"n": int(len(x))}


def counts(s: pd.Series) -> dict:
    return {str(k): int(v) for k, v in s.value_counts().sort_index().items()}


def cmd_conditions(a) -> pd.DataFrame:
    data = Path(a.data)
    od = out_dir(data)
    t0 = time.time()
    idx = load_index(data)
    log(f"index: {len(idx)} photos (latest versions) in {time.time() - t0:.0f} s")
    df = idx[["obs_id", "detector", "collection", "s3_key", "size_bytes", "mjd", "s_ra", "s_dec"]].copy()
    df["ecl_lon"], df["ecl_lat"] = ecliptic(df["s_ra"], df["s_dec"])
    df["sun_lon"] = sun_lon(df["mjd"])
    df["elong"] = elongation(df["ecl_lon"], df["ecl_lat"], df["sun_lon"])
    df["gal_l"], df["gal_b"] = galactic(df["s_ra"], df["s_dec"])
    f, c, best = gaia_density(data, df["s_ra"], df["s_dec"])
    df["gaia_forced_per_deg2"], df["gaia_capped"], df["gaia_per_deg2"] = f.astype(np.float32), c, best.astype(np.float32)
    t1 = time.time()
    df["depth"] = (depth_counts(df["s_ra"].to_numpy(), df["s_dec"].to_numpy()) / N_DET).astype(np.float32)
    log(f"depth in {time.time() - t1:.0f} s")
    sz = sky_zone_photos(data)
    key = df["obs_id"].astype(str) + "D" + df["detector"].astype(int).astype(str)
    df["in_sky_zones"] = key.isin(set(sz["obs_id"].astype(str) + "D" + sz["detector"].astype(int).astype(str)))
    used = used_photos(data, [HERE / "bench_targets.json", HERE / "holdout_targets.json"])
    ukey = used["obs_id"].astype(str) + "D" + used["detector"].astype(int).astype(str)
    df["in_bench"] = key.isin(set(ukey[used["set"] == "bench"]))
    df["in_holdout"] = key.isin(set(ukey[used["set"] == "holdout"]))
    df["month"] = mission_month(df["mjd"])
    df["region"] = region(df)
    df.to_parquet(od / "conditions.parquet")
    summary = conditions_summary(df)
    (od / "conditions.json").write_text(json.dumps(summary, indent=1))
    (od / "conditions.md").write_text(conditions_md(summary))
    log(f"conditions: {len(df)} photos -> {od}/conditions.parquet ({time.time() - t0:.0f} s)")
    return df


def conditions_summary(df: pd.DataFrame) -> dict:
    cells = cell_table(df)
    return {
        "made": time.strftime("%Y-%m-%dT%H:%M:%S"), "photos": int(len(df)),
        "in_sky_zones": int(df["in_sky_zones"].sum()), "outside_sky_zones": int((~df["in_sky_zones"]).sum()),
        "in_bench": int(df["in_bench"].sum()), "in_holdout": int(df["in_holdout"].sum()),
        "gaia_capped_photos": int(df["gaia_capped"].sum()),
        "gaia_capped_without_estimate": int((df["gaia_capped"] & df["gaia_per_deg2"].isna()).sum()),
        "mjd_range": [round(float(df["mjd"].min()), 2), round(float(df["mjd"].max()), 2)],
        "quantiles": {k: describe(v) for k, v in {
            "abs_ecl_lat_deg": df["ecl_lat"].abs(), "elong_deg": df["elong"], "abs_gal_b_deg": df["gal_b"].abs(),
            "gaia_g175_per_deg2": df["gaia_per_deg2"], "gaia_forced_per_deg2": df["gaia_forced_per_deg2"],
            "depth_exposures": df["depth"], "abs_ecl_lat_outside_zones": df.loc[~df["in_sky_zones"], "ecl_lat"].abs(),
            "depth_outside_zones": df.loc[~df["in_sky_zones"], "depth"]}.items()},
        "bins": {k: counts(cells[k]) for k in ("beta", "elong", "gal_b", "gaia", "depth", "region", "month")},
        "per_detector": counts(df["detector"]),
    }


def md_table(rows: list[dict], cols: list[str] | None = None) -> str:
    if not rows:
        return "(none)\n"
    cols = cols or list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
    return "\n".join(out) + "\n"


def conditions_md(s: dict) -> str:
    q = s["quantiles"]
    lines = [f"# Conditions of every SPHEREx Level 2 photo ({s['made']})", "",
             f"{s['photos']:,} photos (latest l2b version), MJD {s['mjd_range'][0]}–{s['mjd_range'][1]}. "
             f"In the 333 sky zones: {s['in_sky_zones']:,}; outside (polar): {s['outside_sky_zones']:,}. "
             f"Bench photos {s['in_bench']}, hold-out {s['in_holdout']}.", "",
             "Measured from the S3 index only (photo centre and MJD): ecliptic latitude, solar elongation (the Sun from "
             "the Almanac's low-precision formula), galactic b, Gaia G < 17.5 per deg² (forced-list partition; "
             f"{s['gaia_capped_photos']:,} photos lie in capped partitions and take an IRSA count in a 0.3° circle "
             f"instead, {s['gaia_capped_without_estimate']:,} without one yet), depth = photos of all detectors with "
             "their centre within 1.75° / 6.", "", "## Quantiles", ""]
    lines.append(md_table([{"measure": k, **v} for k, v in q.items()]))
    for k, v in s["bins"].items():
        lines += [f"## {k}", "", md_table([{"cell": c, "photos": n, "share": f"{n / s['photos']:.3f}"}
                                            for c, n in v.items()])]
    return "\n".join(lines)


# --- the sample -------------------------------------------------------------------------------------------------------


def from_ecliptic(lon, lat) -> tuple[float, float]:
    """ICRS (ra, dec) of a J2000 ecliptic (lon, lat), degrees."""
    lo, la = np.deg2rad(lon), np.deg2rad(lat)
    sd = np.sin(la) * np.cos(OBLIQUITY) + np.cos(la) * np.sin(OBLIQUITY) * np.sin(lo)
    ra = np.arctan2(np.sin(lo) * np.cos(OBLIQUITY) - np.tan(la) * np.sin(OBLIQUITY), np.cos(lo))
    return float(np.rad2deg(ra) % 360.0), float(np.rad2deg(np.arcsin(sd)))


def from_galactic(l, b) -> tuple[float, float]:
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    c = SkyCoord(l=l * u.deg, b=b * u.deg, frame="galactic").icrs
    return float(c.ra.deg), float(c.dec.deg)


# The deliberate extremes: (name, ra, dec, why, how sessions are picked). "elong": the lowest solar elongation first.
def deliberate_spots() -> list[dict]:
    s = []
    for lon in (60, 200, 330):
        ra, dec = from_ecliptic(lon, 0.0)
        s.append({"name": f"zodi{lon:03d}", "ra": ra, "dec": dec, "pick": "elong",
                  "why": f"strongest zodiacal glow: on the ecliptic (λ {lon}°), lowest solar elongation"})
    s.append({"name": "nep", "ra": 270.0, "dec": 66.5607, "why": "north ecliptic pole: deepest coverage, outside the zones"})
    s.append({"name": "sep", "ra": 90.0, "dec": -66.5607, "why": "south ecliptic pole: deepest coverage, outside the zones"})
    for name, ra, dec in (("nep80", *from_ecliptic(90, 80.0)), ("sep80", *from_ecliptic(270, -80.0))):
        s.append({"name": name, "ra": ra, "dec": dec, "why": "ecliptic latitude ±80°: deep coverage, outside the zones"})
    s.append({"name": "gc", "ra": 266.405, "dec": -28.936, "why": "galactic centre: the most crowded field"})
    s.append({"name": "baade", "ra": 270.891, "dec": -30.035, "why": "galactic bulge, Baade's window (b -3.9°)"})
    for l, b in ((30, 0), (90, 0), (180, 0), (270, 0), (310, 0), (60, 4)):
        ra, dec = from_galactic(l, b)
        s.append({"name": f"plane{l:03d}" + ("" if b == 0 else f"b{b}"), "ra": ra, "dec": dec,
                  "why": f"galactic plane l {l}°, b {b}°"})
    s.append({"name": "lmc", "ra": 84.67, "dec": -69.10, "why": "Large Magellanic Cloud, 30 Doradus (crowding, nebula)"})
    s.append({"name": "smc", "ra": 13.19, "dec": -72.83, "why": "Small Magellanic Cloud (crowding)"})
    s.append({"name": "ngp", "ra": 192.86, "dec": 27.13, "why": "north galactic pole: high-latitude empty sky"})
    s.append({"name": "sgp", "ra": 12.86, "dec": -27.13, "why": "south galactic pole: high-latitude empty sky"})
    s.append({"name": "lockman", "ra": 161.25, "dec": 58.0, "why": "Lockman Hole: the emptiest high-latitude sky"})
    s.append({"name": "omegacen", "ra": 201.697, "dec": -47.480, "why": "omega Centauri: a globular cluster's crowded core"})
    s.append({"name": "orion", "ra": 83.82, "dec": -5.39, "why": "Orion Nebula: bright extended emission"})
    s.append({"name": "sirius", "ra": 101.287, "dec": -16.716, "why": "Sirius: the brightest star (glints, ghosts, bleed)"})
    return s


def box_for(ra: float, dec: float, side: float) -> tuple[float, float, float, float]:
    """An RA/Dec box `side` degrees on the sky centred near (ra, dec), moved so it never crosses RA 0/360."""
    h = side / 2.0
    hr = h / max(np.cos(np.deg2rad(min(abs(dec) + h, 89.0))), 0.05)
    ra = min(max(ra, hr + 1e-3), 360.0 - hr - 1e-3)
    return tuple(round(float(x), 4) for x in (ra - hr, ra + hr, dec - h, dec + h))


def stratified_spots(cond: pd.DataFrame, taken: list[tuple[float, float]], per_cell: int, min_sep: float,
                     min_photos: int, rng) -> list[dict]:
    """One spot (more for big cells) per (|β| cell × |b| cell) with at least min_photos photos: a random photo
    centre of the cell at least min_sep degrees from every spot already chosen; |dec| < 80 (no polar RA boxes)."""
    from skyshift_pipeline.sky import angsep

    cells = cell_table(cond)
    out = []
    groups = cond.assign(_be=cells["beta"].astype(str), _gb=cells["gal_b"].astype(str)).groupby(["_be", "_gb"])
    for (be, gb), g in groups:
        if len(g) < min_photos:
            continue
        g = g[(g["s_dec"].abs() < 80) & (g["region"].isin(["", "highlat", "plane", "nep", "sep"]))]
        n = per_cell + (1 if len(g) > 0.08 * len(cond) else 0)
        pos = rng.permutation(len(g))
        k = 0
        for i in pos[:5000]:
            r = g.iloc[int(i)]
            if taken and (angsep(r.s_ra, r.s_dec, np.array([t[0] for t in taken]),
                                 np.array([t[1] for t in taken])) < min_sep).any():
                continue
            taken.append((float(r.s_ra), float(r.s_dec)))
            k += 1
            out.append({"name": f"e{be.split('–')[0].split('=')[-1]:0>2}g{gb.split('–')[0].split('=')[-1]:0>2}"
                                f"{'abcdef'[k - 1]}".replace(".", ""),
                        "ra": float(r.s_ra), "dec": float(r.s_dec),
                        "why": f"stratified: |β| {be}°, |b| {gb}°"})
            if k >= n:
                break
    return out


def session_table(ph: pd.DataFrame) -> pd.DataFrame:
    """One row per zone session: photos, detectors, mid MJD, month, visit, mean elongation."""
    g = ph.groupby("session")
    return pd.DataFrame({"photos": g.size(), "detectors": g["detector"].nunique(), "mjd": g["mjd"].mean(),
                         "visit": g["visit"].first(), "elong": g["elong"].mean(), "gb": g["size_bytes"].sum() / 1e9,
                         "month": mission_month(g["mjd"].mean())}).reset_index()


def pick_sessions(st: pd.DataFrame, target: int, how: str = "time", max_sessions: int = 6,
                  min_photos: int = 2, max_photos: int = 20) -> list[int]:
    """Sessions spread over the mission: visits in time order taken at evenly spaced quantiles, the session with the
    most photos (≤ max_photos) of each, until `target` photos (at least two sessions). how="elong": the sessions
    with the lowest mean solar elongation (the strongest glow), from any visit."""
    st = st[(st["photos"] >= min_photos) & (st["photos"] <= max_photos)]
    if st.empty:
        return []
    if how == "elong":  # the sessions with the lowest elongation, whatever their visit
        out, n = [], 0
        for r in st.sort_values("elong").itertuples():
            if len(out) >= max_sessions or (n >= target and len(out) >= 2):
                break
            out.append(int(r.session))
            n += int(r.photos)
        return out
    best = st.sort_values(["photos", "detectors"], ascending=False).drop_duplicates("visit")
    vis = sorted(best["visit"])
    # as many evenly spaced visits as the target needs at this zone's usual session size, first and last included
    k = int(np.clip(np.ceil(target / max(1.0, float(best["photos"].median()))), 2, max_sessions))
    k = min(k, len(vis))
    order = list(dict.fromkeys(vis[int(round(i))] for i in np.linspace(0, len(vis) - 1, k))) if k > 1 else vis[:1]
    rest = [v for v in vis if v not in order]
    order += sorted(rest, key=lambda v: -min(abs(v - o) for o in order))  # then the visits farthest from those
    out, n = [], 0
    by_visit = best.set_index("visit")
    for v in order:
        if len(out) >= max_sessions or (n >= target and len(out) >= 2):
            break
        r = by_visit.loc[v]
        out.append(int(r["session"]))
        n += int(r["photos"])
    return out


def zone_list(data: Path, name: str, box: tuple) -> pd.DataFrame:
    """photolists/zones/<name>.parquet made by the pipeline's photolist step from the S3 index (a new file; an
    existing w_ list is reused, never rewritten)."""
    os.environ["SKYSHIFT_PHOTOLIST_SOURCE"] = "s3index"
    from skyshift_pipeline import config as C
    from skyshift_pipeline import photolist
    from skyshift_pipeline.store import Store

    assert name.startswith("w_"), name
    photolist.run(Store(str(data)), C.Zone(name, *box))
    return R.zone_photos(data, name)


def cmd_sample(a) -> dict:
    data = Path(a.data)
    od = out_dir(data)
    cond = pd.read_parquet(od / "conditions.parquet")
    rng = np.random.default_rng(a.seed)
    spots = deliberate_spots()
    taken = [(s["ra"], s["dec"]) for s in spots]
    spots += stratified_spots(cond, taken, a.per_cell, a.min_sep, a.min_cell_photos, rng)
    log(f"sample: {len(spots)} spots")
    key = lambda df: df["obs_id"].astype(str) + "D" + df["detector"].astype(int).astype(str)  # noqa: E731
    cond = cond.set_index(key(cond))
    used = set(cond.index[cond["in_bench"] | cond["in_holdout"]])
    chosen: set = set()
    targets, rows = [], []
    for s in spots:
        gaia = float(gaia_density(data, [s["ra"]], [s["dec"]])[2][0])
        side = a.dense_side if not np.isfinite(gaia) or gaia >= 2449 else a.side
        box = box_for(s["ra"], s["dec"], side)
        name = f"w_{s['name']}"
        ph = zone_list(data, name, box)
        k = key(ph)
        ph = ph.assign(_k=k.values).join(cond[["elong", "ecl_lat", "gal_b", "gaia_per_deg2", "gaia_forced_per_deg2",
                                                "depth", "region", "in_sky_zones"]], on="_k")
        bad = set(ph.loc[ph["_k"].isin(used | chosen), "session"])
        st = session_table(ph[~ph["session"].isin(bad)])
        sess = pick_sessions(st, a.photos_per_zone, s.get("pick", "time"), a.max_sessions)
        if not sess:
            log(f"sample: {name}: no usable session")
            continue
        sel = ph[ph["session"].isin(sess)]
        chosen |= set(sel["_k"])
        cells = cell_table(sel.assign(month=mission_month(sel["mjd"])))
        why = (f"{s['why']}. Cell: |β| {cells['beta'].astype(str).mode()[0]}°, elong "
               f"{sel['elong'].min():.0f}–{sel['elong'].max():.0f}°, |b| {cells['gal_b'].astype(str).mode()[0]}°, "
               f"Gaia G<17.5 {cells['gaia'].astype(str).mode()[0]}/deg²"
               f"{' (capped list)' if bool(sel['gaia_forced_per_deg2'].max() >= 2448) else ''}, depth "
               f"{cells['depth'].astype(str).mode()[0]} exposures, months "
               f"{','.join(str(m) for m in sorted(set(mission_month(sel['mjd']))))}, "
               f"{'in the sky zones' if sel['in_sky_zones'].all() else ('outside the sky zones' if not sel['in_sky_zones'].any() else 'partly outside the sky zones')}")
        targets.append({"zone": name, "sessions": sorted(int(x) for x in sess), "box": list(box), "why": why})
        rows.append(sel.drop(columns=["_k"]).assign(zone=name))
        log(f"sample: {name} box {box}: {len(ph)} photos in {ph['session'].nunique()} sessions; picked {len(sess)} "
            f"sessions, {len(sel)} photos")
    sample = pd.concat(rows, ignore_index=True)
    doc = {"about": "WIDE sample for scripts/rerun/rerun.py bench (scripts/rerun/wide_sample.py sample): whole sessions "
                    "of small sample zones w_<name> spread over the sky conditions (zodiacal glow, ecliptic poles, "
                    "galactic plane and bulge, Magellanic Clouds, high-latitude sky, time), none holding a bench or "
                    "hold-out photo. Same format as bench_targets.json; `box` is the zone's RA/Dec box (its photo list "
                    "photolists/zones/<zone>.parquet and its catalogue truth cover it).",
           "made": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": a.seed, "photos": int(len(sample)),
           "sessions": int(sum(len(t["sessions"]) for t in targets)), "targets": targets}
    Path(a.targets).write_text(json.dumps(doc, indent=1) + "\n")
    sample.to_parquet(od / "sample.parquet")
    cond_all = cond.reset_index(drop=True)
    sm = sample_md(cond_all, sample, doc)
    (od / "sample.md").write_text(sm)
    log(f"sample: {len(targets)} zones, {doc['sessions']} sessions, {len(sample)} photos, "
        f"{sample['size_bytes'].sum() / 1e9:.1f} GB -> {a.targets}")
    return doc


def crosstab_md(df: pd.DataFrame, r: str, c: str) -> str:
    t = pd.crosstab(df[r], df[c], dropna=False)
    rows = [{r + " \\ " + c: str(i), **{str(k): int(v) for k, v in row.items()}} for i, row in t.iterrows()]
    return md_table(rows)


def sample_md(cond: pd.DataFrame, sample: pd.DataFrame, doc: dict) -> str:
    ca, cs = cell_table(cond), cell_table(sample.assign(month=mission_month(sample["mjd"])))
    lines = [f"# WIDE sample ({doc['made']})", "",
             f"{len(doc['targets'])} sample zones, {doc['sessions']} whole sessions, {len(sample):,} photos, "
             f"{sample['size_bytes'].sum() / 1e9:.1f} GB. Whole sky: {len(cond):,} photos.", "",
             "## Photos per condition cell: sample vs whole sky", ""]
    for k in ("beta", "elong", "gal_b", "gaia", "depth", "region", "month", "in_sky_zones"):
        a_, s_ = ca[k].astype(str).value_counts(), cs[k].astype(str).value_counts()
        order = list(ca[k].cat.categories.astype(str)) if hasattr(ca[k], "cat") else \
            sorted(set(a_.index) | set(s_.index), key=lambda c: (int(c) if k == "month" else 0, c))
        rows = [{k: (c or "(none)"), "sample": int(s_.get(c, 0)), "sample share": f"{s_.get(c, 0) / len(sample):.3f}",
                 "sky": int(a_.get(c, 0)), "sky share": f"{a_.get(c, 0) / len(cond):.3f}"} for c in order]
        lines += [f"### {k}", "", md_table(rows)]
    lines += ["## |β| × |b|: sample", "", crosstab_md(cs, "beta", "gal_b"),
              "## |β| × |b|: whole sky", "", crosstab_md(ca, "beta", "gal_b"),
              "## Targets", "", md_table([{"zone": t["zone"], "sessions": len(t["sessions"]),
                                           "photos": int((sample["zone"] == t["zone"]).sum()), "why": t["why"]}
                                          for t in doc["targets"]])]
    return "\n".join(lines)


# --- truth ------------------------------------------------------------------------------------------------------------


def known_root(data: Path) -> Path:
    """The overlay root the known-body steps run in: photolists/zones/<zone>.parquet holds only the sampled sessions
    (their zone numbers kept, so the cache files are the ones the whole list would give) and cache/mpc/orbits/ is a
    copy of the shared snapshots (a comet snapshot completed with allcometels is written here, not into the shared
    cache). The results are copied to <data>/cache/{mpc,planets}/<zone>/ (new folders only)."""
    import shutil

    root = out_dir(data) / "known-root"
    orb = root / "cache" / "mpc" / "orbits"
    orb.mkdir(parents=True, exist_ok=True)
    for f in sorted((data / "cache" / "mpc" / "orbits").glob("*.parquet")):
        if not (orb / f.name).exists():
            shutil.copy2(f, orb / f.name)
    (root / "photolists" / "zones").mkdir(parents=True, exist_ok=True)
    return root


def install(src: Path, dest: Path) -> int:
    """Copy the files of src/ into dest/ (a w_ folder); never overwrites a file already there."""
    import shutil

    assert dest.name.startswith("w_"), dest
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.glob("*")):
        if f.is_file() and not (dest / f.name).exists():
            shutil.copy2(f, dest / f.name)
            n += 1
    return n


def cmd_truth(a) -> dict:
    os.environ.setdefault("SKYSHIFT_REFCAT_S3_FIRST", "1")  # CatWISE/2MASS from AllWISE on S3 first (IRSA TAP: minutes per tile)
    from skyshift_pipeline import config as C
    from skyshift_pipeline import known_mpc, known_planets, refcat
    from skyshift_pipeline import log as plog
    from skyshift_pipeline.store import Store

    plog.setup()
    data = Path(a.data)
    targets = json.loads(Path(a.targets).read_text())["targets"]
    if a.zones:
        targets = [t for t in targets if t["zone"] in set(a.zones.split(","))]
    kroot = known_root(data)
    status_p = out_dir(data) / "truth.json"
    status = json.loads(status_p.read_text()) if status_p.exists() else {}
    for t in targets:
        name = t["zone"]
        assert name.startswith("w_"), name
        zone = C.Zone(name, *map(float, t["box"]))
        st = status.setdefault(name, {})
        t0 = time.time()
        if "refcat" in a.only:
            try:
                out = refcat.run(Store(str(data)), zone, catalogs=refcat.FULL)
                st["refcat"] = {k: out.get(k) for k in ("rows", "services", "forced_rows", "skipped", "wall_s")}
            except Exception as e:  # noqa: BLE001
                st["refcat"] = {"error": str(e)[:300]}
                log(f"truth {name}: refcat failed: {e}")
        if "known" in a.only:
            ph = R.zone_photos(data, name)
            sub = ph[ph["session"].astype(int).isin(set(int(s) for s in t["sessions"]))]
            sub.to_parquet(kroot / "photolists" / "zones" / f"{name}.parquet")
            ks = Store(str(kroot))
            try:
                out = known_mpc.run(ks, zone)
                st["mpc"] = {k: out.get(k) for k in ("sessions", "computed", "cached", "orbits", "comets_complete",
                                                       "objects_per_session_median", "exposures_geocentre", "wall_s")}
                st["mpc"]["installed"] = install(kroot / "cache" / "mpc" / name, data / "cache" / "mpc" / name)
            except Exception as e:  # noqa: BLE001
                st["mpc"] = {"error": str(e)[:300]}
                log(f"truth {name}: known (MPC) failed: {e}")
            try:
                out = known_planets.run(ks, zone)
                st["planets"] = {k: out.get(k) for k in ("sessions", "computed", "near", "objects", "wall_s")}
                st["planets"]["installed"] = install(kroot / "cache" / "planets" / name,
                                                     data / "cache" / "planets" / name)
            except Exception as e:  # noqa: BLE001
                st["planets"] = {"error": str(e)[:300]}
                log(f"truth {name}: known (planets) failed: {e}")
        st["seconds"] = round(time.time() - t0, 1)
        status_p.write_text(json.dumps(status, indent=1, default=str))
        log(f"truth {name}: {json.dumps(st, default=str)[:400]}")
    return status


# --- main -------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        s = sub.add_parser(name)
        s.add_argument("--data", default=str(R.DEFAULT_DATA))
        if name == "gaia-counts":
            s.add_argument("--threads", type=int, default=8)
        if name in ("sample", "truth"):
            s.add_argument("--targets", default=str(WIDE_TARGETS))
        if name == "sample":
            s.add_argument("--seed", type=int, default=20261127)
            s.add_argument("--per-cell", type=int, default=1, help="stratified spots per |β| × |b| cell (+1 in big cells)")
            s.add_argument("--min-sep", type=float, default=8.0, help="degrees between spots")
            s.add_argument("--min-cell-photos", type=int, default=3000)
            s.add_argument("--side", type=float, default=1.0, help="sample zone box side (degrees on the sky)")
            s.add_argument("--dense-side", type=float, default=0.5, help="box side where the forced list is capped")
            s.add_argument("--photos-per-zone", type=int, default=36)
            s.add_argument("--max-sessions", type=int, default=6)
        if name == "truth":
            s.add_argument("--zones", default=None, help="comma list (default: every target zone)")
            s.add_argument("--only", default="refcat,known", help="refcat, known or both")
    a = ap.parse_args(argv)
    COMMANDS[a.cmd](a)


COMMANDS = {"gaia-counts": cmd_gaia_counts, "conditions": cmd_conditions, "sample": cmd_sample, "truth": cmd_truth}

if __name__ == "__main__":
    main()
