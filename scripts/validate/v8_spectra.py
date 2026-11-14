"""V8, spectra half: SPHEREx spectra of a pipeline zone's stars against 2MASS Ks and CatWISE W1.

The plan asked for two unsaturated stars; one zone gives thousands. For every source with a
102-channel spectrum (pipeline `spectra` step, results/<zone>/sources.parquet) matched one-to-one to
Gaia (< 1"), 2MASS (< 1.5") and, for W1, CatWISE (< 1.5") in results/<zone>/crossids.parquet, and
not flagged saturated or variable:

  SPHEREx band flux = mean of the channel fluxes (uJy) with centres inside the band, weighted by
                      channel width; at least 3 channels with data.
  Ks band 1.99-2.31 um, zero point 666.7 Jy (Cohen et al. 2003);
  W1 band 2.80-3.80 um, zero point 309.54 Jy (Jarrett et al. 2011), Vega magnitudes.
  ratio = SPHEREx / catalogue, reported by catalogue magnitude. Pass: median within 20% of 1 in the
  clean range (10-13 mag, bright enough to be precise, faint enough to be unsaturated).

No colour corrections: for stars they are a few percent at most.

Output: data/v8_spectra_<zone>.json, one per zone (build_validation.py reads them, the first zone as headline).

  uv run python v8_spectra.py                # hunt and pilot; fetches tables not cached yet (~180 + 30 MB, gcloud)
  uv run python v8_spectra.py --zones pilot --local DIR   # DIR holds sources.parquet, crossids.parquet, channels.json
  uv run python v8_spectra.py --zones hunt --out /tmp/x.json   # one zone, written elsewhere
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from _common import BUCKET, CACHE, DATA, today, write_json

ZONES = ("hunt", "pilot")
BANDS = {
    "Ks": {"um": (1.99, 2.31), "zp_jy": 666.7, "mag": "k", "match": ("tmass_sep_arcsec", "tmass_n"),
           "ref": "2MASS Ks, zero point 666.7 Jy (Cohen et al. 2003)"},
    "W1": {"um": (2.80, 3.80), "zp_jy": 309.54, "mag": "w1", "match": ("catwise_sep_arcsec", "catwise_n"),
           "ref": "CatWISE2020 W1, zero point 309.54 Jy (Jarrett et al. 2011)"},
}
MATCH_ARCSEC = 1.5
CLEAN = (10.0, 13.0)
BINS = [(9, 10), (10, 11), (11, 12), (12, 13), (13, 14), (14, 15)]
FILES = ("sources.parquet", "crossids.parquet", "channels.json")


def fetch(zone: str) -> Path:
    dest = CACHE / "results" / zone
    dest.mkdir(parents=True, exist_ok=True)
    gcloud = shutil.which("gcloud")
    for f in FILES:
        if not (dest / f).exists():
            if not gcloud:
                raise SystemExit(f"gcloud not on PATH: copy {BUCKET}/results/{zone}/{f} to {dest}/ (or use --local)")
            subprocess.run([gcloud, "storage", "cp", f"{BUCKET}/results/{zone}/{f}", str(dest / f)], check=True)
    return dest


def band_flux(F: np.ndarray, ch: pd.DataFrame, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    sel = ((ch["um_centre"] >= lo) & (ch["um_centre"] <= hi)).to_numpy()
    w = (ch["um_hi"] - ch["um_lo"]).to_numpy()[sel]
    f = F[:, sel]
    ok = np.isfinite(f)
    wsum = np.where(ok, w, 0.0).sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(ok, f * w, 0.0).sum(1) / wsum
    return mean, ok.sum(1)


def stats(r: np.ndarray) -> dict:
    if r.size == 0:
        return {"n": 0, "median": None, "scatter": None}
    med = float(np.median(r))
    return {"n": int(r.size), "median": round(med, 4), "scatter": round(float(1.4826 * np.median(np.abs(r - med))), 4)}


def out_path(zone: str) -> Path:
    return DATA / f"v8_spectra_{zone}.json"


def run(zone: str, src_dir: Path, out: Path) -> dict:
    """V8 spectra of one zone's tables in `src_dir`, written to `out`."""
    s = pd.read_parquet(src_dir / "sources.parquet")
    x = pd.read_parquet(src_dir / "crossids.parquet")
    ch = pd.DataFrame(json.loads((src_dir / "channels.json").read_text())).sort_values("channel")  # global 0-101; "index" is per detector
    F = np.stack(s["flux_ujy"].to_numpy()).astype(np.float64)
    cols = ["source_id", "gaia_sep_arcsec", "gaia_n", "tmass_sep_arcsec", "tmass_n", "k", "catwise_sep_arcsec", "catwise_n", "w1"]
    m = s[["source_id", "ra", "dec", "saturated", "variable"]].merge(x[cols], on="source_id", how="left")
    star = (m["gaia_sep_arcsec"] < 1.0) & (m["gaia_n"] == 1) & ~m["saturated"] & ~m["variable"]

    print(f"--- {zone}: {len(s):,} sources")
    out_bands = {}
    for name, b in BANDS.items():
        sp, nch = band_flux(F, ch, *b["um"])
        cat = b["zp_jy"] * 1e6 * 10 ** (-0.4 * m[b["mag"]].to_numpy(np.float64))
        sep, n = b["match"]
        good = (star & (m[sep] < MATCH_ARCSEC) & (m[n] == 1) & (nch >= 3) & np.isfinite(sp) & (sp > 0)
                & np.isfinite(cat)).to_numpy()
        ratio = sp / cat
        mag = m[b["mag"]].to_numpy(np.float64)
        rows = [{"band": name, "mag_min": lo, "mag_max": hi, **stats(ratio[good & (mag >= lo) & (mag < hi)])} for lo, hi in BINS]
        clean = stats(ratio[good & (mag >= CLEAN[0]) & (mag < CLEAN[1])])
        out_bands[name] = {"um": list(b["um"]), "reference": b["ref"], "clean_range_mag": list(CLEAN),
                           "clean": clean, "all": stats(ratio[good]), "by_mag": rows}
        print(f"{name}: clean {CLEAN[0]:g}-{CLEAN[1]:g} mag: n={clean['n']} median {clean['median']} scatter {clean['scatter']}")
        for r_ in rows:
            print(f"   {r_['mag_min']}-{r_['mag_max']}: n={r_['n']:5d} median {r_['median']} scatter {r_['scatter']}")

    ok = all(b["clean"]["median"] is not None and abs(b["clean"]["median"] - 1) <= 0.2 for b in out_bands.values())
    res = {"generated": today(), "check": "V8", "part": "spectra", "zone": zone,
           "method": __doc__.split("Output:")[0].strip(),
           "sources": int(len(s)), "pass": ok, "bands": out_bands}
    write_json(out, res)
    print(f"V8 spectra {zone}: {'pass' if ok else 'fail'}  -> {out}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zones", default=",".join(ZONES), help="pipeline zones, comma-separated (default hunt,pilot)")
    ap.add_argument("--zone", help="one zone only (same as --zones ZONE)")
    ap.add_argument("--local", type=Path, help="folder with one zone's sources.parquet, crossids.parquet, channels.json")
    ap.add_argument("--out", type=Path, help="output file for one zone (default data/v8_spectra_<zone>.json)")
    args = ap.parse_args()
    zones = [z.strip() for z in (args.zone or args.zones).split(",") if z.strip()]
    if (args.local or args.out) and len(zones) != 1:
        raise SystemExit("--local and --out take one zone: add --zones <zone>")
    for z in zones:
        run(z, args.local or fetch(z), args.out or out_path(z))


if __name__ == "__main__":
    main()
