#!/usr/bin/env python3
"""Search depth and false alarms by condition group, from a run root's conditions files (extraction v2,
<root>/<v2 dots folder>_cond/<obs_id>D<det>.parquet; pipeline/src/skyshift_pipeline/cond.py) and, where truth exists,
a score_bench run's star rows (score_bench.py --stars-out):
    uv run --project pipeline python scripts/rerun/cond_report.py --root <root> [--stars stars.parquet] [--out x.json]

Groups: background noise (per detector, terciles of the tiles' noise_mjysr over the files read: n1 < n2 < n3) ×
star density (Gaia forced targets per deg² of good pixels: the forced list keeps the brightest 8,221 per HEALPix
nside-32 partition, ~2,450 per deg², so the top bin is "crowded or capped") × detector. Per group: photos, tiles,
median threshold (µJy, the matched filter's point-source threshold as applied), dots and flagged dots (dot_flag != 0)
per deg², negative-residual peaks per deg² at S/N 4 / 4.5 / 5 (when stored: a group's false-alarm rate), and the
share of isolated Gaia stars found (a dot within score_bench's 3″) by G bin; each star goes to the tile of its photo
whose centre is nearest on the sky.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIXEL_ARCSEC = 6.15
DENSITY_BINS = [0.0, 1000.0, 2000.0, np.inf]       # Gaia forced targets per deg²
DENSITY_LABELS = ["sparse", "mid", "crowded"]
G_BINS = [0, 14, 16, 17, 18, 19, 20]
COLS = ["tile", "ra", "dec", "n_good", "noise_mjysr", "thresh_ujy", "psf_sigma_ujy", "n_gaia", "n_dots",
        "n_dots_flagged", "n_neg_4", "n_neg_45", "n_neg_5"]


def default_folder() -> str:
    try:
        from skyshift_pipeline import config as C
        return C.DOTS_V2_SETTINGS.folder + "_cond"
    except ImportError:  # pragma: no cover
        return "dots_deblend1e-4_cp3_g6_psf_glow_v2_cond"


def load_tiles(cond_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Every tile of every conditions file in cond_dir, with photo, detector and has_neg; and the photos' summaries."""
    import pyarrow.parquet as pq
    parts, summaries = [], {}
    for p in sorted(cond_dir.glob("*.parquet")):
        f = pq.ParquetFile(p)
        md = (f.metadata.metadata or {}).get(b"skyshift_cond")
        s = json.loads(md) if md else {}
        photo = p.stem
        det = int(s.get("detector") or photo.rsplit("D", 1)[1])
        df = f.read(columns=[c for c in COLS if c in f.schema_arrow.names]).to_pandas()
        parts.append(df.assign(photo=photo, detector=det, has_neg=bool(s.get("neg"))))
        summaries[photo] = s
    if not parts:
        return pd.DataFrame(columns=COLS + ["photo", "detector", "has_neg"]), summaries
    return pd.concat(parts, ignore_index=True), summaries


def add_groups(t: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """noise_bin (per detector terciles of noise_mjysr), density_bin, area_deg2."""
    t = t.copy()
    t["area_deg2"] = t["n_good"].astype(float) * (PIXEL_ARCSEC / 3600.0) ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        dens = np.where(t["area_deg2"] > 0, t["n_gaia"].astype(float) / t["area_deg2"], np.nan)
    t["gaia_per_deg2"] = dens
    t["density_bin"] = pd.cut(dens, DENSITY_BINS, labels=DENSITY_LABELS, right=False).astype(str)
    edges = {}
    t["noise_bin"] = "n?"
    for d, g in t.groupby("detector"):
        v = g["noise_mjysr"].astype(float)
        q = np.nanquantile(v, [1 / 3, 2 / 3]) if v.notna().any() else [np.nan, np.nan]
        edges[int(d)] = [round(float(x), 5) for x in q]
        t.loc[g.index, "noise_bin"] = np.where(v < q[0], "n1", np.where(v < q[1], "n2", "n3"))
    return t, edges


def assign_stars(stars: pd.DataFrame, t: pd.DataFrame) -> pd.DataFrame:
    """Each star row (photo, ra, dec) with the tile of its photo whose centre is nearest (tile index), else -1."""
    from scipy.spatial import cKDTree

    def unit(ra, dec):
        r, d = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
        return np.c_[np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)]
    out = stars.copy()
    out["tile"] = -1
    tiles_by = {p: g for p, g in t.groupby("photo")}
    for p, g in out.groupby("photo"):
        tg = tiles_by.get(p)
        if tg is None:
            continue
        _, i = cKDTree(unit(tg["ra"], tg["dec"])).query(unit(g["ra"], g["dec"]))
        out.loc[g.index, "tile"] = tg["tile"].to_numpy()[i]
    return out


def report(t: pd.DataFrame, stars: pd.DataFrame | None = None) -> pd.DataFrame:
    keys = ["noise_bin", "density_bin", "detector"]
    neg = bool(t["has_neg"].any()) if len(t) else False
    rows = []
    for k, g in t.groupby(keys):
        area = float(g["area_deg2"].sum())
        r = {"noise_bin": k[0], "density_bin": k[1], "detector": int(k[2]), "photos": int(g["photo"].nunique()),
             "tiles": int(len(g)), "area_deg2": round(area, 3),
             "noise_mjysr": round(float(g["noise_mjysr"].median()), 4),
             "thresh_ujy": round(float(g["thresh_ujy"].median()), 1),
             "dots_deg2": round(float(g["n_dots"].sum()) / area, 1) if area > 0 else None,
             "flagged_deg2": round(float(g["n_dots_flagged"].sum()) / area, 1) if area > 0 else None}
        if neg:
            gn = g[g["has_neg"]]
            an = float(gn["area_deg2"].sum())
            for c in ("n_neg_4", "n_neg_45", "n_neg_5"):
                r[c.replace("n_", "") + "_deg2"] = round(float(gn[c].sum()) / an, 2) if an > 0 else None
        rows.append(r)
    rep = pd.DataFrame(rows)
    if stars is not None and len(stars) and len(rep):
        s = stars[stars["tile"] >= 0].merge(t[["photo", "tile"] + keys], on=["photo", "tile"], how="inner")
        s["g_bin"] = pd.cut(s["g_mag"], G_BINS).astype(str)
        f = s.groupby(keys + ["g_bin"])["after"].agg(["mean", "size"]).reset_index()
        for gb, fg in f.groupby("g_bin"):
            m = fg.set_index(keys)
            name = f"gaia_{gb}"
            rep[name] = [None if (r.noise_bin, r.density_bin, r.detector) not in m.index else
                         f"{m.loc[(r.noise_bin, r.density_bin, r.detector), 'mean']:.2f} "
                         f"({int(m.loc[(r.noise_bin, r.density_bin, r.detector), 'size'])})"
                         for r in rep.itertuples()]
    return rep.sort_values(["detector", "noise_bin", "density_bin"]).reset_index(drop=True) if len(rep) else rep


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--cond-dir", default=None, help="default <root>/<v2 dots folder>_cond")
    ap.add_argument("--stars", default=None, help="score_bench.py --stars-out parquet (Gaia truth per star)")
    ap.add_argument("--out", default=None, help="write the table and settings as JSON here")
    a = ap.parse_args(argv)
    cond_dir = Path(a.cond_dir) if a.cond_dir else Path(a.root) / default_folder()
    t, summ = load_tiles(cond_dir)
    if not len(t):
        sys.exit(f"no conditions files in {cond_dir}")
    t, edges = add_groups(t)
    stars = None
    if a.stars:
        stars = assign_stars(pd.read_parquet(a.stars), t)
    rep = report(t, stars)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(f"{t['photo'].nunique()} photos, {len(t)} tiles from {cond_dir}")
    print(f"noise terciles (noise_mjysr) per detector: {edges}")
    print(rep.to_string(index=False))
    out = {"cond_dir": str(cond_dir), "photos": int(t["photo"].nunique()), "tiles": int(len(t)),
           "noise_edges_mjysr": edges, "density_bins_per_deg2": DENSITY_BINS[1:-1], "g_bins": G_BINS,
           "stars_matched": None if stars is None else int((stars["tile"] >= 0).sum()),
           "table": json.loads(rep.to_json(orient="records"))}
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
