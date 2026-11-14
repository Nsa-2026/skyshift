"""End-to-end: position -> frames -> aligned cube -> analyses."""
import os, json, time, hashlib
import concurrent.futures as cf
import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u

from . import s3

PIXSCALE = 6.15
from .algo import skymodel, detect, movers, astrometry, spectrum

RES_DIR = os.path.join(s3.CACHE_DIR, "results")



def build_cube(frames, subtract_zodi=True, mask_artifacts=False):
    img = np.stack([f["image"] for f in frames]).astype(np.float64)
    var = np.stack([f["var"] for f in frames]).astype(np.float64)
    if subtract_zodi:
        img = img - np.stack([f["zodi"] for f in frames])
    bad = np.stack([f["bad"] for f in frames])
    if mask_artifacts:
        bad |= np.stack([f["artifact"] for f in frames])
    img[bad] = np.nan
    # residual offset after zodi: subtract a robust per-frame pedestal
    for t in range(len(img)):
        v = img[t][np.isfinite(img[t])]
        if v.size:
            lo = np.percentile(v, 50)
            img[t] -= lo
    meta = [f["meta"] for f in frames]
    lam = np.array([m.get("wavelength") or np.nan for m in meta])
    return img, var, lam, meta


def epochs_of(mjd):
    from skyshift_core.visits import visits
    return visits(mjd) - 1


def stack(img, var, w=None, sel=None):
    sel = np.ones(len(img), bool) if sel is None else sel
    w = np.ones(len(img)) if w is None else np.asarray(w)
    ok = np.isfinite(img[sel]) & np.isfinite(var[sel]) & (var[sel] > 0)
    iv = np.where(ok, w[sel][:, None, None] / np.where(ok, var[sel], 1), 0)
    s = (np.where(ok, img[sel], 0) * iv).sum(0)
    n = iv.sum(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan), np.where(n > 0, 1 / n, np.nan)


def analyse(frames, wcs, ra, dec, progress=None):
    img, var, lam, meta = build_cube(frames)
    mjd = np.array([m["mjd"] for m in meta])
    qw = np.array([m["quality"]["weight"] for m in meta])
    good_lam = np.isfinite(lam)
    lam = np.where(good_lam, lam, np.nanmedian(lam) if good_lam.any() else 2.0)
    fwhms = detect.psf_fwhm_pix(lam)
    ep = epochs_of(mjd)
    out = dict(n_frames=len(frames), epochs=int(ep.max() + 1) if len(ep) else 0,
               mjd_span=[float(mjd.min()), float(mjd.max())] if len(mjd) else None)
    if progress:
        progress("fitting spectral static-sky model")
    model, resid, coef, chi = skymodel.fit_static_sky(img, var, lam, frame_weight=qw)
    out["model_rms_chi"] = float(np.nanstd(np.clip(chi, -20, 20)))
    # spectrum of the target (from the pre-model cube, local background removed)
    cx, cy = wcs.all_world2pix([[ra, dec]], 0)[0]
    bkg = np.array([np.nanmedian(img[t]) for t in range(len(img))])[:, None, None] * np.ones_like(img)
    spec = spectrum.target_spectrum(img, var, lam, meta, cx, cy, fwhms)
    out["spectrum"] = spec
    out["halo_lightcurve"] = hl = spectrum.halo_lightcurve(img, lam, mjd, cx, cy)
    var_amp = {}
    for wdw in sorted(set(r["window"] for r in hl)):
        L = [r for r in hl if r["window"] == wdw]
        m = np.array([r["mjd"] for r in L]); d = np.array([r["dmag"] for r in L])
        bins = np.arange(m.min(), m.max() + 30, 30); ix = np.digitize(m, bins)
        med = [float(np.median(d[ix == i])) for i in set(ix.tolist()) if (ix == i).sum() > 3]
        if len(med) >= 2:
            var_amp[wdw] = round(max(med) - min(med), 2)
    out["variability_amplitude_mag"] = var_amp
    out["saturated_fraction"] = float(np.mean([p.get("method") == "wing-fit" for p in spec])) if spec else 0.0
    out["spectrum_epochs"] = [dict(e, W1=spectrum.synth_band(e["points"], "W1"), W2=spectrum.synth_band(e["points"], "W2"))
                              for e in spectrum.epoch_split(spec)]
    # --- DR1 all-sky spectral cube as a reference sky -----------------------------------
    try:
        if os.environ.get("SKYSHIFT_REFERENCE", "0") != "1":
            raise RuntimeError("DR1 reference not requested; use the reference action")
        from . import reference
        from scipy.ndimage import gaussian_filter
        if progress:
            progress("reading the DR1 all-sky spectral cube (102 wavelengths) as reference")
        rc, rl, rb = reference.reference_cube(ra, dec, wcs, img.shape[1])
        if rc is not None:
            n = img.shape[1]
            yy, xx = np.mgrid[0:n, 0:n]
            rr = np.hypot(xx - cx, yy - cy)
            ap, ann = rr <= 2.5, (rr > 5) & (rr < 9)
            k = spectrum.OMEGA_PIX_SR * 1e9
            ref_spec = []
            for z in np.argsort(rl):
                pl = rc[z]
                if np.isfinite(pl[ap]).sum() < ap.sum() * 0.8:
                    continue
                f = (np.nansum(pl[ap]) - np.nanmedian(pl[ann]) * ap.sum()) * k
                ref_spec.append(dict(lam=float(rl[z]), flux=float(f)))
            out["reference"] = dict(source=reference.CUBE_PREFIX, mjd=[60892.25, 61089.20], spectrum=ref_spec)
            # change maps: any frame vs the reference at that frame's own wavelength
            refdiff = np.full_like(img, np.nan)
            for t in range(len(img)):
                R = reference.reference_at(rc, rl, lam[t])
                if R is None:
                    continue
                F = gaussian_filter(np.nan_to_num(img[t]), 0.8)
                F[~np.isfinite(img[t])] = np.nan
                Rz = R - np.nanmedian(R)
                refdiff[t] = F - Rz
            res_extra = dict(refdiff=refdiff, ref_cube=rc, ref_lam=rl)
        else:
            res_extra = {}
    except Exception as e:
        out["reference"] = dict(error=str(e)); res_extra = {}
    if progress:
        progress("measuring motion of the target")
    # astrometry only from frames in which the target itself is clearly detected (for a Y dwarf
    # that means the 4-5 um frames; at 1 um it is invisible and a neighbour would be fitted)
    from .algo.detect import forced_flux
    tsnr = np.array([(lambda fe: fe[0] / fe[1] if np.isfinite(fe[0]) and fe[1] > 0 else 0)(forced_flux(img[t], var[t], cx, cy, fwhms[t]))
                     for t in range(len(img))])
    out["astrometry"] = astrometry.measure_track(img, var, mjd, wcs, ra, dec, fwhms, use=(tsnr > 5) & (np.array([m["det"] for m in meta]) == (6 if (tsnr > 5).any() and np.median(lam[tsnr > 5]) > 3.8 else 2)), fit_parallax=False, dets=np.array([m['det'] for m in meta]))
    out["astrometry"]["frames_used_wavelengths"] = [float(np.nanmin(lam[tsnr > 5])), float(np.nanmax(lam[tsnr > 5]))] if (tsnr > 5).any() else None
    # --- Planet X exclusion meter -----------------------------------------------------------
    try:
        from .algo import exclusion
        limits = []
        for lo, hi in ((0.75, 1.1), (1.1, 1.6), (1.6, 2.4), (2.4, 3.8), (3.8, 4.4), (4.4, 5.05)):
            sel = (lam >= lo) & (lam < hi)
            if sel.sum() < 2:
                continue
            rs, rv = stack(resid, var, qw, sel)
            limits.append(dict(lam=float(np.median(lam[sel])), flim_mjy=float(exclusion.depth_per_window(rs, rv, float(np.median(fwhms[sel])))),
                               n_frames=int(sel.sum())))
        for L in limits:
            L["ab_mag_5sigma"] = float(exclusion.ab_mag(L["flim_mjy"]))
        out["exclusion"] = dict(limits=limits, rmin=exclusion.exclusion(limits), p9=exclusion.p9_check(limits),
                                wise0855_twin_reach_pc=exclusion.brown_dwarf_reach(limits))
    except Exception as e:
        out["exclusion"] = dict(error=str(e))
    res = dict(img=img, var=var, lam=lam, meta=meta, model=model, resid=resid, chi=chi, mjd=mjd, epoch=ep, fwhm=fwhms, **res_extra)
    res['flags'] = np.stack([f['flags'] for f in frames]).astype(np.int32)
    res['dets'] = movers.tag_partner(movers.detect_all(resid, var, fwhms, model=model, img=img), resid, var, meta, fwhms)
    out['target_motion'] = None
    af = out['astrometry'].get('fit')
    if af and af['n'] >= 20 and af['pm_total'] / max(np.hypot(af['epmra'],af['epmdec']),1e-9) > 5:
        out['target_motion'] = dict(method='per-frame PSF fits (experimental)',
            **{k: af[k] for k in ('pmra','pmdec','epmra','epmdec')})
    return out, res


def jsonable(o):
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, float):
        return None if not np.isfinite(o) else o
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o
