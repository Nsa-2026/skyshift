"""Stamp lists (27 Nov, agent "comet-stamps"): for every photo of the sky, the targets the dots worker cuts a stamp
around (stamps.py). Built once on the PC, before the rerun, from the MPC's comet orbits with kete; nothing here reads a
photo or a bucket.

    uv run --project pipeline python -m skyshift_pipeline.stamplist --index ~/skyshift-data/photolists/s3index \\
        --orbits <dir with cometels.json.gz + allcometels.json.gz> --out <list dir> [--workers 3] [--periods a,b]

Layout: <out>/<kind>/<period>.parquet (one file per planning period of the photo index, empty when no target falls in
it) + <out>/<kind>/_complete.json last (orbits, cut, pad, counts). The period is the obs_id's first two parts
("2025W34_1B" of 2025W34_1B_0173_2), as in the S3 key. A generic row (LIST_COLUMNS) names a target, its kind and its
position at the photo's time; any kind of target fits (a fixed star: delta_au NaN, its proper motion in the rates).

Comets (kind "comet"): cometels.json.gz + allcometels.json.gz merged by known_mpc.merge_comets (cometels' row first,
allcometels' rows with an epoch from 2015 on), each moved with kete's n-body propagation (light time included, no
non-gravitational terms) and checked against every photo's footprint (s_region corners pushed out STAMP_LIST_PAD_DEG)
from the Earth's centre at the index's photo time (mid of t_min, t_max). The rows are geocentric: SPHEREx is up to
~7,100 km from the Earth's centre, 9.8″/Δ(AU) of parallax, which the worker removes with the photo's own X_SC.. at
MJD-AVG (stamps.topocentric). The MPC's comet magnitude law gives mag_pred = H + 5 log Δ + 2.5 G log r (total
magnitude, visible light; NaN without H); rows brighter than STAMP_COMET_MAG_MAX or with no H are kept.
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from . import log

L = log.get("stamplist")
AU_KM = 149_597_870.7
DEG_DAY_TO_ARCSEC_H = 3600.0 / 24.0
RAD_ARCSEC = 206_264.806
TELAPSE_S = 118.0  # start to stop of one exposure (CLAUDE.md)

# One row per (photo, target). Positions are geocentric (astrometric, light time to the Earth's centre) at mjd_ref.
LIST_COLUMNS = {
    "obs_id": "str", "detector": "int8", "kind": "str", "name": "str",
    "mjd_ref": "float64",          # UTC MJD the position and rates refer to (the photo index's mid-time)
    "ra": "float64", "dec": "float64",  # deg, ICRS, geocentric at mjd_ref
    "dra_arcsec_h": "float32", "ddec_arcsec_h": "float32",  # geocentric on-sky rates (dRA·cos dec), ″/h
    "delta_au": "float64",         # geocentric distance (NaN: no parallax, a fixed target)
    "r_au": "float32",             # heliocentric distance
    "phase_deg": "float32",        # Sun-target-Earth angle
    "mag_pred": "float32",         # comets: the MPC's total magnitude law (NaN unknown)
    "sigma_arcsec": "float32",     # position uncertainty (NaN: unknown; the MPC gives none for comets)
    "half_px": "int16",            # stamp half-width: the stamp is (2 half + 1)² px
    "edge_deg": "float32",         # signed distance of the geocentric position to the footprint edge (> 0 inside)
    "orbit_epoch_mjd": "float32", "orbit_file": "str", "h": "float32", "g": "float32",
}


def empty_list() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in LIST_COLUMNS.items()})


def period_of(obs_id: str) -> str:
    """'2025W34_1B_0173_2' -> '2025W34_1B' (the planning period of the S3 key)."""
    return "_".join(str(obs_id).split("_")[:2])


# ---------------------------------------------------------------- orbits

def comet_records(path: Path) -> list[dict]:
    with gzip.open(path, "rt") as f:
        return json.load(f)


def comet_orbits(orbit_dir: Path, min_jd: float = C.MPC_ALLCOMETS_MIN_JD) -> pd.DataFrame:
    """cometels + allcometels (the MPC's JSON files in orbit_dir) as kete's comet table (known_mpc.comet_table) plus
    h, g (the MPC's total-magnitude law), merged by known_mpc.merge_comets; column name = the designation stripped."""
    from . import known_mpc as KM

    def table(recs: list[dict]) -> pd.DataFrame:
        t = KM.comet_table(recs)
        t["h"] = [float(c["H"]) if c.get("H") is not None else np.nan for c in recs]
        t["g"] = [float(c["G"]) if c.get("G") is not None else np.nan for c in recs]
        return t

    cur = table(comet_records(Path(orbit_dir) / "cometels.json.gz"))
    every = table(comet_records(Path(orbit_dir) / "allcometels.json.gz"))
    out = KM.merge_comets(cur, every, min_jd=min_jd).reset_index(drop=True)
    out["name"] = out["desig"].astype(str).str.strip()
    return out


def comet_states(orb: pd.DataFrame) -> list:
    from . import known_mpc as KM
    return KM.build_states(orb[list(KM.COMET_COLS)])


# ---------------------------------------------------------------- the size rule

def half_px(delta_au, rate_arcsec_h, coma_km: float = C.STAMP_COMA_KM, margin: int = C.STAMP_MARGIN_PX,
            lo: int = C.STAMP_HALF_MIN_PX, hi: int = C.STAMP_HALF_MAX_PX) -> np.ndarray:
    """Stamp half-width (px): the coma's radius coma_km at Δ + half the motion over one exposure + margin (the
    stacker's background annulus and its along-track search), clipped to [lo, hi]."""
    d = np.asarray(delta_au, np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        coma = np.where(d > 0, coma_km / (d * AU_KM) * RAD_ARCSEC / C.PIXEL_ARCSEC, hi)
    move = np.abs(np.asarray(rate_arcsec_h, np.float64)) * TELAPSE_S / 3600.0 / C.PIXEL_ARCSEC / 2.0
    h = np.ceil(np.nan_to_num(coma, nan=hi) + np.nan_to_num(move) + margin)
    return np.clip(h, lo, hi).astype(np.int16)


def mag_pred(h, g, r_au, delta_au) -> np.ndarray:
    """The MPC comet law: m = H + 5 log10 Δ + 2.5 G log10 r (G = the MPC's slope: K1 = 2.5 G)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(h, float) + 5.0 * np.log10(delta_au) + 2.5 * np.asarray(g, float) * np.log10(r_au)


# ---------------------------------------------------------------- one period

def _vec(v) -> np.ndarray:
    return np.array([v.x, v.y, v.z], np.float64)


def period_rows(photos: pd.DataFrame, orb: pd.DataFrame, states: list | None = None,
                pad_deg: float = C.STAMP_LIST_PAD_DEG, mag_max: float = C.STAMP_COMET_MAG_MAX) -> pd.DataFrame:
    """The comet rows of one period's photos (obs_id, detector, mjd, s_region): one kete fov_state_check from the
    Earth's centre at each photo's mjd over every photo's padded footprint."""
    import kete

    from .known_mpc import padded_corners
    from .sky import Footprint

    photos = photos.drop_duplicates(["obs_id", "detector"]).sort_values("mjd").reset_index(drop=True)
    if photos.empty or orb.empty:
        return empty_list()
    states = comet_states(orb) if states is None else states
    jd = {o: kete.Time.from_mjd(float(m), scaling="utc").jd for o, m in photos.groupby("obs_id")["mjd"].first().items()}
    earth = {o: kete.spice.get_state("Earth", j) for o, j in jd.items()}
    states = kete.propagate_n_body(states, min(jd.values()))
    fovs, where = [], {}
    for k, r in enumerate(photos.itertuples()):
        corners = [kete.Vector.from_ra_dec(a, d) for a, d in padded_corners(r.s_region, pad_deg)]
        uri = str(k)
        fovs.append(kete.fov.SpherexCmos(corners, earth[r.obs_id], uri, str(int(r.detector))))
        where[uri] = k
    parts = []
    for ss in kete.fov_state_check(states, fovs):
        if not len(ss.states):
            continue
        k = where[ss.fov.uri]
        obs = _vec(ss.fov.observer.pos)
        rows = np.array([int(s.desig) for s in ss.states])
        rd = np.asarray(ss.ra_dec_with_rates, float).reshape(-1, 4)
        pos = np.array([_vec(s.pos) for s in ss.states])  # Sun-centred, ecliptic, at the light-time corrected time
        rel = pos - obs
        delta = np.linalg.norm(rel, axis=1)
        r_au = np.linalg.norm(pos, axis=1)
        with np.errstate(invalid="ignore"):
            cosph = np.einsum("ij,ij->i", -pos, -rel) / (r_au * delta)
        parts.append(pd.DataFrame({"k": k, "row": rows, "ra": rd[:, 0] % 360.0, "dec": rd[:, 1],
                                   "dra_arcsec_h": rd[:, 2] * DEG_DAY_TO_ARCSEC_H,
                                   "ddec_arcsec_h": rd[:, 3] * DEG_DAY_TO_ARCSEC_H, "delta_au": delta, "r_au": r_au,
                                   "phase_deg": np.degrees(np.arccos(np.clip(cosph, -1, 1)))}))
    if not parts:
        return empty_list()
    s = pd.concat(parts, ignore_index=True)
    o = orb.iloc[s["row"].to_numpy()]
    ph = photos.iloc[s["k"].to_numpy()]
    mag = mag_pred(o["h"].to_numpy(), o["g"].to_numpy(), s["r_au"].to_numpy(), s["delta_au"].to_numpy())
    keep = ~(mag > mag_max)  # NaN (no H) is kept
    rate = np.hypot(s["dra_arcsec_h"], s["ddec_arcsec_h"]).to_numpy()
    edge = np.empty(len(s))
    for k, idx in s.groupby("k").indices.items():
        fp = Footprint.from_s_region(photos["s_region"].iloc[k])
        edge[idx] = fp.edge_distance_deg(s["ra"].to_numpy()[idx], s["dec"].to_numpy()[idx])
    out = pd.DataFrame({
        "obs_id": ph["obs_id"].to_numpy(object), "detector": ph["detector"].to_numpy(),
        "kind": "comet", "name": o["name"].to_numpy(object), "mjd_ref": ph["mjd"].to_numpy(np.float64),
        "ra": s["ra"], "dec": s["dec"], "dra_arcsec_h": s["dra_arcsec_h"], "ddec_arcsec_h": s["ddec_arcsec_h"],
        "delta_au": s["delta_au"], "r_au": s["r_au"], "phase_deg": s["phase_deg"], "mag_pred": mag,
        "sigma_arcsec": np.nan, "half_px": half_px(s["delta_au"], rate), "edge_deg": edge,
        "orbit_epoch_mjd": pd.to_numeric(o["epoch"], errors="coerce").to_numpy() - 2400000.5,
        "orbit_file": o["orbit_file"].to_numpy(object), "h": o["h"].to_numpy(), "g": o["g"].to_numpy(),
    })[keep]
    out = out.sort_values(["obs_id", "detector", "name"]).reset_index(drop=True)
    return out.astype({c: t for c, t in LIST_COLUMNS.items() if t != "str"})


# ---------------------------------------------------------------- the whole index

def index_periods(index_dir: Path) -> dict[str, list[Path]]:
    """period -> its index files (dr1/<period>.parquet, qr3/<period>.parquet)."""
    out: dict[str, list[Path]] = {}
    for f in sorted(Path(index_dir).glob("*/*.parquet")):
        out.setdefault(f.stem, []).append(f)
    return out


def _period_job(period: str, files: list[str], orbit_dir: str, out_dir: str, pad_deg: float, mag_max: float,
                min_jd: float) -> dict:
    t0 = time.time()
    orb = comet_orbits(Path(orbit_dir), min_jd=min_jd)
    cols = ["obs_id", "detector", "mjd", "s_region"]
    photos = pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True)
    rows = period_rows(photos, orb, pad_deg=pad_deg, mag_max=mag_max)
    dest = Path(out_dir) / f"{period}.parquet"
    tmp = dest.with_suffix(".parquet.part")
    rows.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(dest)
    inside = rows["edge_deg"].to_numpy() > 0
    return {"period": period, "photos": int(photos.drop_duplicates(["obs_id", "detector"]).shape[0]),
            "rows": int(len(rows)), "rows_inside": int(inside.sum()), "s": round(time.time() - t0, 1)}


def build(index_dir: Path, orbit_dir: Path, out: Path, workers: int = 3, periods: list[str] | None = None,
          pad_deg: float = C.STAMP_LIST_PAD_DEG, mag_max: float = C.STAMP_COMET_MAG_MAX,
          min_jd: float = C.MPC_ALLCOMETS_MIN_JD, force: bool = False) -> dict:
    """Every period's comet list into out/comets/ (resumable: a period's file is skipped when it exists), then
    _complete.json (only when every period of the index was done)."""
    kdir = Path(out) / "comets"
    kdir.mkdir(parents=True, exist_ok=True)
    allp = index_periods(index_dir)
    todo = sorted(periods or allp)
    jobs = [p for p in todo if force or not (kdir / f"{p}.parquet").exists()]
    orb = comet_orbits(Path(orbit_dir), min_jd=min_jd)
    L.info("stamp list: %d comets, %d periods (%d to do), %d workers", len(orb), len(todo), len(jobs), workers)
    res = []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(_period_job, p, [str(f) for f in allp[p]], str(orbit_dir), str(kdir), pad_deg, mag_max,
                          min_jd) for p in jobs]
        for f in futs:
            r = f.result()
            res.append(r)
            L.info("stamp list %s: %d photos, %d rows (%d inside) in %.0f s", r["period"], r["photos"], r["rows"],
                   r["rows_inside"], r["s"])
    summary = {"kind": "comet", "orbits": {"dir": str(orbit_dir), "comets": int(len(orb)),
                                           "by_file": orb["orbit_file"].value_counts().to_dict()},
               "pad_deg": pad_deg, "mag_max": mag_max, "min_epoch_jd": min_jd, "coma_km": C.STAMP_COMA_KM,
               "half_px": [C.STAMP_HALF_MIN_PX, C.STAMP_HALF_MAX_PX], "margin_px": C.STAMP_MARGIN_PX,
               "periods_done_now": res, "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if set(allp) <= {p.stem for p in kdir.glob("*.parquet")}:
        (kdir / "_complete.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--index", required=True, help="photolists/s3index/ (the photo index)")
    ap.add_argument("--orbits", required=True, help="folder with the MPC's cometels.json.gz and allcometels.json.gz")
    ap.add_argument("--out", required=True, help="the stamp list folder (comets/ goes in it)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--periods", default="", help="comma list (default: every period of the index)")
    ap.add_argument("--pad-deg", type=float, default=C.STAMP_LIST_PAD_DEG)
    ap.add_argument("--mag-max", type=float, default=C.STAMP_COMET_MAG_MAX)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    log.setup()
    s = build(Path(a.index).expanduser(), Path(a.orbits).expanduser(), Path(a.out).expanduser(), a.workers,
              [p for p in a.periods.split(",") if p] or None, a.pad_deg, a.mag_max, force=a.force)
    print(json.dumps({k: v for k, v in s.items() if k != "periods_done_now"}, default=str))


if __name__ == "__main__":
    main()
