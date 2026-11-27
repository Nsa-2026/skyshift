#!/usr/bin/env python3
"""Stack the dots worker's stamps of one listed target (a known comet) along its predicted path (agent "comet-stamps",
27 Nov). Reads only the stamp files (<v2 dots folder>_stamps/*.parquet, pipeline/src/skyshift_pipeline/stamps.py),
never a photo.

    uv run --project pipeline python scripts/rerun/stack_stamps.py --root <run root> --name "306P/LINEAR" \\
        [--frame comet|star] [--plane masked|sub|resid] [--detectors 3,6] [--out <dir>]

Per detector: every stamp of the target is resampled (bilinear, on IMAGE − background with 1/VARIANCE weights; bad
pixels weight 0) onto one grid of 6.152″ pixels whose centre is the target's predicted place in that photo (frame
`comet`) or one fixed sky point, the prediction at the middle photo's time (frame `star`, the control: the comet's light
smears along its path, the stars stack) or the path run backwards about the middle photo's place (frame `reverse`,
a second control: the comet's light lands 2× its motion away from the centre). Planes: `sub` IMAGE − the worker's background; `resid` the point-free
residual (stars and the glow subtracted; a comet bright enough to be a dot is subtracted too); `masked` (default) `sub`
with the pixels where the subtracted point model exceeds the noise set to weight 0, except within --keep-px of the
prediction (the target's own light stays). Each stamp's local background (median of its annulus 12–20 px around the
prediction) is removed first.

Measured on each stack: an r = 2 px aperture at (0, 0) and Gaussian matched filters 1×, 2×, 4× the PSF FWHM (the
point filter of a resampled stack: FWHM 1.5 px), each S/N divided by the map's own scatter (1.4826 × MAD of the
filter map, or of r = 2 px apertures, away from the centre), at (0, 0) and the best within --search-px; the radial
profile; the best single-photo S/N (the same aperture and noise rule in each stamp); controls: the same measurements at
8 places on a ring of --ring-px around (0, 0). Writes <out>/<name>_D<det>_<frame>.png and <out>/<name>_<frame>.json.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "pipeline" / "src"))

PIX = 6.152                  # arcsec per SPHEREx pixel (the stack grid's too)
UJY_PER_MJYSR_PX = 889.6     # 1 MJy/sr in one 6.152″ pixel
PSF_FWHM_PX = 1.5            # the point filter on a resampled stack (ePSF 7.53″ ⊗ the pixel and the resampling)
ANNULUS = (12.0, 20.0)       # per-stamp local background (comets.md)


def _stamps_mod():
    from skyshift_pipeline import stamps as SP
    return SP


def find_files(root: Path, folder: str | None = None) -> list[str]:
    pat = f"{folder}_stamps" if folder else "*_stamps"
    return sorted(glob.glob(str(Path(root) / pat / "*.parquet")))


def load(files: list[str], name: str, detectors: set | None = None) -> list[dict]:
    """Every stamp row of the target `name` (exact, else a prefix match) with its planes decoded."""
    SP = _stamps_mod()
    out = []
    for f in files:
        meta_df, _ = SP.read_stamps(f, planes=(), columns=["name", "detector"])
        hit = (meta_df["name"] == name) | meta_df["name"].str.startswith(name)
        if detectors:
            hit &= meta_df["detector"].isin(detectors)
        if not hit.any():
            continue
        df, meta = SP.read_stamps(f, planes=("image", "variance", "bkg", "resid", "bad"))
        for k in np.flatnonzero(hit.to_numpy()):
            r = df.iloc[k].to_dict()
            r["file"] = f
            out.append(r)
    return out


def plane(st: dict, which: str, keep_px: float) -> tuple[np.ndarray, np.ndarray]:
    """(data, weight) of one stamp: MJy/sr minus its annulus median; weight 1/VARIANCE, 0 where bad."""
    img = np.asarray(st["image"], np.float64)
    bkg = np.asarray(st["bkg"], np.float64)
    var = np.asarray(st["variance"], np.float64)
    bad = np.asarray(st["bad"], bool) | ~np.isfinite(img) | ~(var > 0)
    sub = img - bkg
    resid = np.asarray(st["resid"], np.float64)
    if which == "resid":
        d = resid
        bad |= ~np.isfinite(resid)
    elif which == "sub":
        d = sub
    elif which == "masked":
        d = sub
        if np.isfinite(resid).any():
            model = sub - np.where(np.isfinite(resid), resid, sub)
            yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
            near = np.hypot(xx - (st["x_pred"] - st["x0"]), yy - (st["y_pred"] - st["y0"])) <= keep_px
            bad |= (model > np.sqrt(np.where(var > 0, var, np.inf))) & ~near
    else:
        raise ValueError(which)
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    r = np.hypot(xx - (st["x_pred"] - st["x0"]), yy - (st["y_pred"] - st["y0"]))
    ann = (r >= ANNULUS[0]) & (r <= ANNULUS[1]) & ~bad & np.isfinite(d)
    d = d - (np.median(d[ann]) if ann.sum() > 20 else 0.0)
    w = np.where(bad | ~np.isfinite(d), 0.0, 1.0 / np.where(var > 0, var, np.inf))
    return np.where(w > 0, d, 0.0), w


def offsets_of(ra0, dec0, ra, dec) -> tuple[float, float]:
    """Tangent-plane offset (arcsec, east, north) of (ra, dec) from (ra0, dec0)."""
    a0, d0, a, d = map(np.radians, (ra0, dec0, ra, dec))
    c = np.sin(d0) * np.sin(d) + np.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / c
    eta = (np.cos(d0) * np.sin(d) - np.sin(d0) * np.cos(d) * np.cos(a - a0)) / c
    return float(np.degrees(xi) * 3600), float(np.degrees(eta) * 3600)


def resample(st: dict, d: np.ndarray, w: np.ndarray, half: int, centre_off: tuple[float, float]):
    """d·w and w on the stack grid (2 half + 1)², grid (0, 0) at the sky offset centre_off (arcsec east, north) from
    the stamp's prediction; grid x to the east, y to the north (PNG: north up, east left after the flip)."""
    from scipy.ndimage import map_coordinates

    g = np.arange(-half, half + 1) * PIX
    ge, gn = np.meshgrid(g, g)  # [y, x]
    e = (ge + centre_off[0]) / 3600.0
    n = (gn + centre_off[1]) / 3600.0
    J = np.array([[st["cd1_1"], st["cd1_2"]], [st["cd2_1"], st["cd2_2"]]])
    Ji = np.linalg.inv(J)
    dx = Ji[0, 0] * e + Ji[0, 1] * n
    dy = Ji[1, 0] * e + Ji[1, 1] * n
    px = st["x_pred"] - st["x0"] + dx
    py = st["y_pred"] - st["y0"] + dy
    coords = np.array([py.ravel(), px.ravel()])
    num = map_coordinates(d * w, coords, order=1, mode="constant", cval=0.0).reshape(ge.shape)
    den = map_coordinates(w, coords, order=1, mode="constant", cval=0.0).reshape(ge.shape)
    return num, den


def _robust_sigma(v: np.ndarray) -> float:
    v = v[np.isfinite(v)]
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size > 10 else float("nan")


def gauss_kernel(fwhm: float) -> np.ndarray:
    s = fwhm / 2.3548
    h = int(np.ceil(3 * s))
    y, x = np.mgrid[-h:h + 1, -h:h + 1]
    k = np.exp(-(x ** 2 + y ** 2) / (2 * s * s))
    return k / k.sum()


def filter_snr(num: np.ndarray, den: np.ndarray, fwhm: float) -> np.ndarray:
    """Inverse-variance matched filter of the stack: Σ K·(d w) / √(Σ K² w) (S/N if w were 1/σ² and independent)."""
    from scipy.signal import fftconvolve
    k = gauss_kernel(fwhm)
    a = fftconvolve(num, k[::-1, ::-1], mode="same")
    b = fftconvolve(den, (k * k)[::-1, ::-1], mode="same")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > 0, a / np.sqrt(b), np.nan)


def aperture_sums(img: np.ndarray, ok: np.ndarray, xs, ys, r: float = 2.0) -> np.ndarray:
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    out = []
    for x, y in zip(xs, ys):
        m = np.hypot(xx - x, yy - y) <= r
        out.append(np.nan if not ok[m].all() else float(img[m].sum()))
    return np.array(out)


def measure(num: np.ndarray, den: np.ndarray, search_px: float, ring_px: float, seed: int = 0) -> dict:
    """The stack's S/N at its centre: r = 2 px aperture and matched filters, each against the map's own scatter."""
    half = num.shape[0] // 2
    with np.errstate(divide="ignore", invalid="ignore"):
        img = np.where(den > 0, num / den, np.nan)
    ok = den > 0.3 * np.nanmedian(den[den > 0]) if (den > 0).any() else den > 0
    yy, xx = np.mgrid[0:num.shape[0], 0:num.shape[1]]
    rr = np.hypot(xx - half, yy - half)
    out: dict = {"n_cov_centre": float(den[half, half])}
    # aperture: empirical noise from r = 2 px apertures at random places > 6 px from the centre
    rng = np.random.default_rng(seed)
    cand = np.flatnonzero((rr.ravel() > 6) & (rr.ravel() < half - 3) & ok.ravel())
    pick = rng.choice(cand, size=min(400, cand.size), replace=False) if cand.size else np.zeros(0, int)
    rand = aperture_sums(np.nan_to_num(img), ok, xx.ravel()[pick], yy.ravel()[pick])
    sig_ap = _robust_sigma(rand)
    f0 = aperture_sums(np.nan_to_num(img), ok, [half], [half])[0]
    with np.errstate(divide="ignore", invalid="ignore"):
        sig_prop = float(np.sqrt(np.sum(np.where(rr <= 2, 1.0 / den, 0.0))))
    out["ap2"] = {"flux_ujy": f0 * UJY_PER_MJYSR_PX, "snr": f0 / sig_ap if sig_ap > 0 else np.nan,
                  "snr_propagated": f0 / sig_prop if sig_prop > 0 else np.nan, "noise_ratio": sig_ap / sig_prop}
    ring = [(half + ring_px * np.cos(a), half + ring_px * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 9)[:-1]]
    rs = aperture_sums(np.nan_to_num(img), ok, [p[0] for p in ring], [p[1] for p in ring])
    out["ap2"]["ring_snr"] = [round(float(v / sig_ap), 2) if np.isfinite(v) else None for v in rs]
    out["ap2"]["random_frac_over_5"] = float(np.mean(np.abs(rand / sig_ap) > 5)) if rand.size else np.nan
    for scale in (1, 2, 4):
        m = filter_snr(num, den, PSF_FWHM_PX * scale)
        sel = (rr > 6 + 2 * scale) & ok
        s = _robust_sigma(m[sel])
        mn = m / s
        near = (rr <= search_px) & np.isfinite(mn)
        k = np.argmax(np.where(near, mn, -np.inf))
        ky, kx = np.unravel_index(k, mn.shape)
        ring_v = [float(mn[int(round(y)), int(round(x))]) for x, y in ring]
        out[f"mf{scale}"] = {"snr": float(mn[half, half]), "best_snr": float(mn[ky, kx]),
                             "best_dx_px": int(kx - half), "best_dy_px": int(ky - half),
                             "map_sigma": s, "ring_snr": [round(v, 2) for v in ring_v],
                             "frac_over_5": float(np.mean(mn[sel] > 5))}
    prof = []
    for r0 in range(0, min(half, 25)):
        m = (rr >= r0) & (rr < r0 + 1) & ok
        prof.append({"r_px": r0, "mean": float(np.nanmean(img[m])) if m.any() else None,
                     "err": float(np.nanstd(img[m]) / np.sqrt(m.sum())) if m.sum() > 1 else None, "n": int(m.sum())})
    out["profile"] = prof
    return out


def single_snr(st: dict, d: np.ndarray, w: np.ndarray) -> float:
    """r = 2 px aperture S/N at the prediction in one stamp, noise from random apertures in that stamp."""
    x, y = st["x_pred"] - st["x0"], st["y_pred"] - st["y0"]
    ok = w > 0
    rng = np.random.default_rng(1)
    ny, nx = d.shape
    xs, ys = rng.uniform(3, nx - 3, 300), rng.uniform(3, ny - 3, 300)
    far = np.hypot(xs - x, ys - y) > 6
    rand = aperture_sums(d, ok, xs[far], ys[far])
    s = _robust_sigma(rand)
    f = aperture_sums(d, ok, [x], [y])[0]
    return float(f / s) if s > 0 else float("nan")


def stack(stamps: list[dict], frame: str = "comet", which: str = "masked", half: int = 30, keep_px: float = 3.0,
          search_px: float = 2.0, ring_px: float = 15.0) -> dict:
    """One detector's stack and its measurements."""
    if not stamps:
        return {"n": 0}
    order = np.argsort([s["mjd"] for s in stamps])
    mid = stamps[int(order[len(order) // 2])]
    num = np.zeros((2 * half + 1, 2 * half + 1))
    den = np.zeros_like(num)
    singles, used = [], 0
    for s in stamps:
        d, w = plane(s, which, keep_px)
        if frame == "comet":
            off = (0.0, 0.0)
        else:  # star: the middle photo's predicted place, fixed; reverse: the path run backwards about it
            e, n = offsets_of(s["ra_pred"], s["dec_pred"], mid["ra_pred"], mid["dec_pred"])
            off = (e, n) if frame == "star" else (2 * e, 2 * n)
        n, dn = resample(s, d, w, half, off)
        if dn.sum() == 0:
            continue
        num += n
        den += dn
        used += 1
        singles.append(single_snr(s, d, w))
    out = {"n": used, "frame": frame, "plane": which, "mjd": [float(np.min([s["mjd"] for s in stamps])),
                                                              float(np.max([s["mjd"] for s in stamps]))],
           "single_snr_max": float(np.nanmax(singles)) if singles else None,
           "single_snr_median": float(np.nanmedian(singles)) if singles else None,
           "single_snr_quadrature": float(np.sqrt(np.nansum(np.clip(singles, 0, None) ** 2))) if singles else None,
           "rate_arcsec_h": float(np.median([s["rate_arcsec_h"] for s in stamps])),
           "parallax_arcsec_max": float(np.max([s["parallax_arcsec"] for s in stamps]))}
    out.update(measure(num, den, search_px, ring_px))
    out["_num"], out["_den"] = num, den
    return out


def png(path: Path, res: dict, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    num, den = res["_num"], res["_den"]
    with np.errstate(divide="ignore", invalid="ignore"):
        img = np.where(den > 0, num / den, np.nan)
    snr = filter_snr(num, den, PSF_FWHM_PX * 2) / res["mf2"]["map_sigma"]
    fig, ax = plt.subplots(1, 3, figsize=(11, 3.8))
    half = img.shape[0] // 2
    ext = [half * PIX, -half * PIX, -half * PIX, half * PIX]  # east left, north up
    v = np.nanpercentile(img, [1, 99.5])
    ax[0].imshow(img, origin="lower", cmap="gray", vmin=v[0], vmax=v[1], extent=ext)
    ax[0].set_title("stack (MJy/sr)")
    im = ax[1].imshow(snr, origin="lower", cmap="magma", vmin=-3, vmax=max(6, np.nanmax(snr)), extent=ext)
    ax[1].set_title("2× PSF filter S/N")
    fig.colorbar(im, ax=ax[1], fraction=0.046)
    for a in ax[:2]:
        a.add_patch(plt.Circle((0, 0), 2 * PIX, fill=False, color="c", lw=1))
        a.set_xlabel("east ″"); a.set_ylabel("north ″")
    p = pd.DataFrame(res["profile"])
    ax[2].errorbar(p["r_px"] * PIX, p["mean"], yerr=p["err"], fmt="o", ms=3)
    ax[2].axhline(0, color="k", lw=0.5)
    ax[2].set_xlabel("r ″"); ax[2].set_title("radial profile")
    fig.suptitle(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def run(root: Path, name: str, out: Path, frame: str = "comet", which: str = "masked", detectors: set | None = None,
        folder: str | None = None, half: int = 30, keep_px: float = 3.0, search_px: float = 2.0, ring_px: float = 15.0,
        pngs: bool = True) -> dict:
    files = find_files(root, folder)
    stamps = load(files, name, detectors)
    out.mkdir(parents=True, exist_ok=True)
    res = {"name": name, "root": str(root), "frame": frame, "plane": which, "files": len(files), "stamps": len(stamps),
           "by_detector": {}}
    for det in sorted({int(s["detector"]) for s in stamps}):
        r = stack([s for s in stamps if int(s["detector"]) == det], frame, which, half, keep_px, search_px, ring_px)
        if pngs and r.get("n"):
            png(out / f"{slug(name)}_D{det}_{frame}_{which}.png", r,
                f"{name} D{det} {frame} frame, {which}: {r['n']} stamps, ap2 S/N {r['ap2']['snr']:.1f}, "
                f"mf1 {r['mf1']['snr']:.1f} mf2 {r['mf2']['snr']:.1f} mf4 {r['mf4']['snr']:.1f}")
        res["by_detector"][det] = {k: v for k, v in r.items() if not k.startswith("_")}
    (out / f"{slug(name)}_{frame}_{which}.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--name", required=True, help="the target's name in the stamp files (exact or a prefix)")
    ap.add_argument("--frame", choices=("comet", "star", "reverse"), default="comet")
    ap.add_argument("--plane", choices=("masked", "sub", "resid"), default="masked")
    ap.add_argument("--detectors", default="", help="comma list (default every detector with stamps)")
    ap.add_argument("--folder", default=None, help="the v2 dots folder (default: any *_stamps folder in the root)")
    ap.add_argument("--half", type=int, default=30, help="stack half-width (px)")
    ap.add_argument("--keep-px", type=float, default=3.0)
    ap.add_argument("--search-px", type=float, default=2.0)
    ap.add_argument("--ring-px", type=float, default=15.0)
    ap.add_argument("--out", default=None, help="default <root>/stamp_stacks")
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args(argv)
    dets = {int(x) for x in a.detectors.split(",") if x.strip()} or None
    out = Path(a.out) if a.out else Path(a.root) / "stamp_stacks"
    res = run(Path(a.root), a.name, out, a.frame, a.plane, dets, a.folder, a.half, a.keep_px, a.search_px, a.ring_px,
              not a.no_png)
    for det, r in res["by_detector"].items():
        if not r.get("n"):
            continue
        print(f"{a.name} D{det} {a.frame}/{a.plane}: n {r['n']}, ap2 S/N {r['ap2']['snr']:.2f} "
              f"(propagated {r['ap2']['snr_propagated']:.2f}), mf1/2/4 {r['mf1']['snr']:.2f}/{r['mf2']['snr']:.2f}/"
              f"{r['mf4']['snr']:.2f} (best within {a.search_px:g} px {r['mf1']['best_snr']:.2f}), single max "
              f"{r['single_snr_max']:.2f}, ring ap2 {r['ap2']['ring_snr']}")


if __name__ == "__main__":
    main()
