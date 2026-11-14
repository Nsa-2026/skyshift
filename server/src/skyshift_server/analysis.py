"""Experimental supplemental analysis; does not change frames/object contracts."""
import numpy as np

from skyshift_core.candidates import cold_candidates, false_alarm, motion_search
from skyshift_core.legacy import movers
from skyshift_core.legacy.detect import find_peaks, snr_map
from skyshift_core.visits import visits

from .local_results import find_job


def build_analysis(ra, dec, det=6, *, n_slides=100, cache=None):
    if n_slides < 1:
        raise ValueError('at least one background slide is required')
    job = find_job(ra, dec)
    r = job['res']; meta = r['meta']; t = np.asarray(r['mjd'])
    visit = visits(t)
    sel = np.array([m['det'] == det for m in meta])
    slow = []
    if len(np.unique(visit[sel])) >= 2:
        cats, _ = movers.epoch_catalogs(r['img'][sel], r['var'][sel], visit[sel], r['fwhm'][sel])
        vids = np.unique(visit[sel])
        tv = np.array([(t[sel & (visit == k)].min()+t[sel & (visit == k)].max())/2 for k in vids])
        # Same detector only. Parallax not fitted: legacy parallax was biased.
        slow = movers.slow_movers(cats, tv)
        _, bg = movers.slow_mover_background(cats, tv, r['img'].shape[1:], n_slides=n_slides)
        for candidate in slow:
            candidate['p_false'] = false_alarm(candidate['pm_sig'], bg)
            candidate['n_slides'] = n_slides
            candidate['pm_arcsec_yr'] = candidate.pop('pm_pix_yr') * 6.15
            candidate['pmra_arcsec_yr'] = -candidate['pm_x'] * 6.15
            candidate['pmdec_arcsec_yr'] = candidate['pm_y'] * 6.15
            candidate['mjd_ref'] = float(np.mean([tv[p['visit']] for p in candidate['points']]))
            candidate['status'] = 'unvetted'
            for p in candidate['points']:
                p['visit'] = int(vids[p['visit']])
    # Re-detect before modelling for the colour test; the source may be static.
    red_peaks = []
    for i, m in enumerate(meta):
        if m['det'] == 6:
            snr, _, _ = snr_map(r['img'][i], r['var'][i], r['fwhm'][i])
            red_peaks.extend(dict(p, t=i) for p in find_peaks(snr, 5))
    cold = cold_candidates(red_peaks, meta, r['img'], r['var'], r['fwhm'], flags=r.get('flags'))
    dets = [dict(d) for d in r.get('dets', []) if meta[d['t']]['det'] == det]
    fast = motion_search(dets, t, r['img'].shape[1:], n_slides=n_slides)
    for candidate in [*slow, *cold]:
        sky = job['wcs'].all_pix2world([[candidate['x'], candidate['y']]], 0)[0]
        candidate['ra'], candidate['dec'] = map(float, sky)
    for track in fast:
        sky = job['wcs'].all_pix2world([[track['x0'], track['y0']]], 0)[0]
        track['ra'], track['dec'] = map(float, sky)
        track['speed_arcsec_day'] = float(np.hypot(track['vx'], track['vy'])*6.15)
        track['status'] = 'unvetted'
    return dict(version=1, detector=det, n_slides=n_slides,
                source=dict(kind='skyshift' if job.get('_native') else 'spherex-tm-local-cache', file=job['_source'],
                            n_frames=len(meta), n_visits=int(visit.max()),
                            coverage_arcmin=job['size'], native_pixel_arcsec=6.15, flags_complete='flags' in r),
                warnings=([] if job.get('_native') else [
                    'Legacy cache is background/zodiacal-subtracted; coverage is limited to the cached field.',
                    'Legacy FLAGS mask differs from SkyShift; no FLAGS cube is retained. All candidates remain unvetted.',
                ]) + [
                    'Gaia/SkyBoT/CatWISE field vetoes have not been run. Known red sources can appear here.',
                    'Motion uses one detector and the native visit rule; parallax is not fitted.',
                    'p_false is an empirical search false-alarm estimate, not an object posterior probability.',
                    'The cold-source persistence radius is 2.5 native pixels; faster movers can be missed.'
                ], slow_movers=slow, fast_movers=fast, cold_candidates=cold)


def cached_analysis(ra,dec,det=6,cache=None):
    import json
    from .app import _key, get_cache, jsonable
    cache=cache or get_cache()
    key=f"api/analysis/{_key('analysis',ra=ra,dec=dec,det=det)}.json"
    raw=cache.get(key)
    if raw is not None:return json.loads(raw)
    result=jsonable(build_analysis(ra,dec,det))
    cache.put(key,json.dumps(result,allow_nan=False).encode(),'application/json')
    return result
