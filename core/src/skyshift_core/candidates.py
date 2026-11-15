"""SPHEREx candidate vetting adapted from spherex-tm.

Pure arrays in, dictionaries out. All coordinates are pixels on the input WCS;
visits come exclusively from skyshift_core.visits. The empirical background is
a search false-alarm estimate, not a posterior probability of an object being false.
"""
from __future__ import annotations

import numpy as np

from .flags import MASK_BITS, RECORD_BITS
from .legacy import movers
from .legacy.detect import forced_flux
from .visits import visits


def false_alarm(score, background):
    b = np.asarray(background, float)
    if b.size == 0 or not np.all(np.isfinite(b)) or not np.isfinite(score):
        return None
    return float((1 + np.count_nonzero(b >= score)) / (b.size + 1))


def cold_candidates(detections, meta, image, variance, fwhms, *, flags=None,
                    match_radius_pix=2.5):
    """D6 S/N >=5, simultaneous D3 S/N <2, persistent in >=2 native visits.

    Force photometry on the pre-model images: a static red source may have no
    residual in either band. A nondetection requires actual finite D3 coverage.
    Missing flag cubes keep the result explicitly unvetted. Persistence matching
    allows <=2.5 native pixels; faster movers need explicit track association.
    """
    times = np.array([m['mjd'] for m in meta])
    visit = visits(times)
    partner = {(m.get('ver'), m.get('obs'), m.get('expo'), m['det']): i
               for i, m in enumerate(meta)}
    hits = []
    for d in detections:
        i = int(d['t']); m = meta[i]
        if m['det'] != 6:
            continue
        j = partner.get((m.get('ver'), m.get('obs'), m.get('expo'), 3))
        if j is None or abs(times[i] - times[j]) > 1 / 86400:
            continue
        fl, el = forced_flux(image[i], variance[i], d['x'], d['y'], fwhms[i])
        fs, es = forced_flux(image[j], variance[j], d['x'], d['y'], fwhms[j])
        if not (np.isfinite([fl, el, fs, es]).all() and el > 0 and es > 0):
            continue
        if fl / el < 5 or fs / es >= 2:
            continue
        recorded = 0
        if flags is not None:
            x, y = round(d['x']), round(d['y'])
            patch = flags[[i, j], max(0, y-1):y+2, max(0, x-1):x+2]
            bits = int(np.bitwise_or.reduce(patch.ravel(), initial=0))
            if bits & MASK_BITS:
                continue
            recorded = bits & RECORD_BITS
        hits.append(dict(x=float(d['x']), y=float(d['y']), t=i,
                         visit=int(visit[i]), snr_long=float(fl/el),
                         snr_short=float(fs/es), flags=recorded))
    # Every group is bounded around its seed; no chaining across the field.
    groups = []
    for h in sorted(hits, key=lambda p: -p['snr_long']):
        group = next((g for g in groups if np.hypot(h['x']-g[0]['x'], h['y']-g[0]['y'])
                      <= match_radius_pix), None)
        if group is None:
            groups.append([h])
        elif not any(p['t'] == h['t'] for p in group):
            group.append(h)
    out = []
    for g in groups:
        nv = len({p['visit'] for p in g})
        record = any(p['flags'] for p in g)
        status = ('single_visit' if nv < 2 else 'unvetted' if flags is None
                  else 'artifact_warning' if record else 'persistent')
        out.append(dict(x=g[0]['x'], y=g[0]['y'], n_visits=nv,
                        status=status, detections=g, p_false=None))
    return out


def motion_search(detections, times, shape, *, n_slides=100, seed=0):
    if n_slides < 1:
        raise ValueError('at least one background slide is required')
    dets = movers.coincidence_filter([dict(d) for d in detections])
    kw = dict(vmax_pix_per_day=3.0)
    tracks = movers.link(dets, times, **kw)
    bg = movers.time_slides(dets, times, shape, n_slides=n_slides, seed=seed, **kw)
    for tr in tracks:
        tr['p_false'] = false_alarm(tr['score'], bg)
        tr['n_slides'] = n_slides
        tr['detections'] = [dets[i] for i in tr.pop('members')]
    return tracks
