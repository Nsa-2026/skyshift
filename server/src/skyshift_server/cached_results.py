"""The blink engine's public interface (contracts/api.md). Implemented by the engine build.

The app (app.py) and scripts/precompute call only these two functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .cache import Cache


def build_frames(ra: float, dec: float, fov: float, clock: Literal["months", "hours"], det: int,
                 *, cache: Cache | None = None, view: str = 'raw') -> Frames:
    """Blink frames for one position, field (arcmin: months 5 or 15, hours 20 or 60) and detector."""
    from .blink import Frames
    import numpy as np
    from astropy.wcs import WCS
    from scipy.ndimage import map_coordinates
    from skyshift_core.visits import visits
    from .local_results import find_job
    from .errors import NotFoundError

    job = find_job(ra, dec)
    r = job['res']; meta = r['meta']
    times = np.asarray(r['mjd'])
    # Group on all exposure times, not on a detector's incomplete time series.
    visit = visits(times)
    selected = np.array([m['det'] == det for m in meta])
    if not selected.any():
        raise NotFoundError(f'No detector {det} exposures in this cached field')
    pix = ({5: 2.05, 15: 3.075} if clock == 'months' else {20: 6.15, 60: 12.3})[fov]
    n = min(300, round(fov * 60 / pix))
    output_wcs = WCS(naxis=2)
    output_wcs.wcs.crpix = [(n + 1)/2] * 2
    output_wcs.wcs.crval = [ra, dec]
    output_wcs.wcs.cdelt = [-pix/3600, pix/3600]
    output_wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    factor = 2 if clock == 'hours' and fov == 60 else 1
    yy, xx = np.mgrid[:n*factor, :n*factor]
    xx = (xx + .5) / factor - .5
    yy = (yy + .5) / factor - .5
    # numpy input rows increase northwards; wire rows start at the north edge.
    sky = output_wcs.all_pix2world(np.c_[xx.ravel(), (n-1-yy).ravel()], 0)
    xy = job['wcs'].all_world2pix(sky, 0)
    coords = [xy[:, 1].reshape(n*factor, n*factor), xy[:, 0].reshape(n*factor, n*factor)]
    groups = ([np.flatnonzero(selected & (visit == v)) for v in np.unique(visit[selected])]
              if clock == 'months' else [np.array([i]) for i in np.flatnonzero(selected)])
    epochs, blobs = [], {}
    for indices in groups:
        field = {'raw':'img','resid':'resid','model':'model','refdiff':'refdiff'}[view]
        if field not in r:
            raise NotFoundError('Reference images are not loaded; request the DR1 reference first')
        data = r[field][indices]
        with __import__('warnings').catch_warnings():
            __import__('warnings').simplefilter('ignore', RuntimeWarning)
            stack = np.nanmedian(data, axis=0)
        valid = np.isfinite(stack)
        weight = map_coordinates(valid.astype(float), coords, order=1, mode='constant', cval=0)
        grid = map_coordinates(np.where(valid, stack, 0), coords, order=1, mode='constant', cval=0)
        grid = np.divide(grid, weight, out=np.full_like(grid, np.nan), where=weight > .999)
        if factor == 2:
            grid = grid.reshape(n, 2, n, 2).mean(axis=(1, 3))
        finite = np.isfinite(grid)
        raw = np.zeros((n, n), dtype='<u2')
        if finite.any():
            lo, hi = float(grid[finite].min()), float(grid[finite].max())
            scale = (hi-lo)/65534 if hi > lo else 1.0
            zero = lo-scale
            raw[finite] = np.clip(np.rint((grid[finite]-zero)/scale), 1, 65535).astype('<u2')
        else:
            scale, zero = 1.0, 0.0
        measured_flags = measured_sat = None
        if 'flags' in r:
            from skyshift_core.flags import flag_frac, sat_frac
            union = np.bitwise_or.reduce(r['flags'][indices], axis=0)
            flag_grid = map_coordinates(union, coords, order=0, mode='constant', cval=1<<9).astype(np.int32)
            if factor == 2:
                flag_grid = np.bitwise_or.reduce(np.bitwise_or.reduce(flag_grid.reshape(n,2,n,2),axis=3),axis=1)
            measured_flags, measured_sat = flag_frac(flag_grid), sat_frac(flag_grid)
        a, b = float(times[indices].min()), float(times[indices].max())
        identifier = (f'd{det}-v{visit[indices[0]]}' if clock == 'months'
                      else f'd{det}-e{indices[0]}')
        name = f'frames/{identifier}.bin'
        # The legacy result has no FLAGS cube, MJD-BEG/END or wavelength map.
        # Missing measurements are null; never substitute central-pixel values.
        epochs.append(dict(id=identifier, kind='visit' if clock == 'months' else 'exposure',
                           mjd_mid=(a+b)/2, mjd_start=a if clock == 'months' else meta[indices[0]].get('mjd_beg'),
                           mjd_end=b if clock == 'months' else meta[indices[0]].get('mjd_end'), det=det,
                           um_min=min((meta[i].get('um_min', np.inf) for i in indices),default=np.inf) if any(meta[i].get('um_min') is not None for i in indices) else None,
                           um_max=max((meta[i].get('um_max', -np.inf) for i in indices),default=-np.inf) if any(meta[i].get('um_max') is not None for i in indices) else None, n_exposures=len(indices),
                           flag_frac=measured_flags, sat_frac=measured_sat, data_url=name, scale=scale, zero=zero))
        blobs[name] = raw.tobytes()
    releases = sorted({m['key'].split('/')[0].upper() for m in meta if m.get('key')})
    return Frames(dict(clock=clock, center=dict(ra=ra, dec=dec), pixel_arcsec=pix,
                       size=[n,n], epochs=epochs, gaia=_gaia(job), known=[],
                       data_release='+'.join(releases), archive_checked=job['_archive_checked']), blobs)


def build_object(ra: float, dec: float, *, cache: Cache | None = None) -> dict[str, Any]:
    """GET /api/object: name, gaia, known_ucd, spectrum[], measure (contracts/api.md)."""
    import numpy as np
    from skyshift_core.visits import visits
    from .local_results import find_job, target_for
    from .errors import NotFoundError
    from .resolve import position_now
    from skyshift_core.astrometry import tangent_offset_arcsec
    job = find_job(ra, dec)
    target = target_for(job)
    tx, ty = position_now(target) if target else (job['ra'],job['dec'])
    a,b = tangent_offset_arcsec(ra, dec, tx, ty)
    c,d = tangent_offset_arcsec(ra, dec, job['ra'], job['dec'])
    if min(float(np.hypot(a,b)), float(np.hypot(c,d))) > 6:
        raise NotFoundError('No cached object within 6 arcsec')
    r = job['res']; v = visits(r['mjd'])
    spectrum = []
    for point in job['summary']['spectrum']:
        i = min(range(len(r['meta'])), key=lambda i: (r['meta'][i]['det'] != point['det'],
                                                     abs(r['mjd'][i]-point['mjd'])))
        flags = r['meta'][i].get('flags_at_target', {})
        spectrum.append(dict(um=point['lam'], flux_ujy=point['flux']*1000,
                             err_ujy=point['err']*1000, det=point['det'], mjd=point['mjd'],
                             visit=int(v[i]), saturated=bool(flags.get('OVERFLOW') or flags.get('NONLINEAR'))))
    gaia = _gaia(job)
    return dict(name=target['name'] if target else None, gaia=gaia[0] if gaia else None, known_ucd=None,
                spectrum=sorted(spectrum, key=lambda p: p['um']), measure=None)


def _gaia(job):
    from .resolve import _targets_path
    from .local_results import target_for
    import json
    target = target_for(job)
    if not target:
        return []
    t = next(t for t in json.loads(_targets_path().read_text())['targets'] if t['slug'] == target['slug'])
    if not t['gaia_source_id']:
        return []
    return [dict(id=str(t['gaia_source_id']), ra=t['ra'], dec=t['dec'], pmra=t['pmra'],
                 pmdec=t['pmdec'], parallax=t['parallax'], g=t['phot_g_mean_mag'])]
