"""Read-only bridge to the owner's trusted, local spherex-tm result cache.

Only load pickles from SKYSHIFT_RESULTS (never accept uploaded pickle paths).
No import or running server from spherex-tm is needed. The exported static data
are portable and do not require this bridge at serving time.
"""
from functools import lru_cache
import os
import pickle
from pathlib import Path

import numpy as np

from .errors import NotFoundError


@lru_cache(maxsize=1)
def jobs():
    root = Path(os.environ.get('SKYSHIFT_RESULTS', '~/.spxtm_cache/results')).expanduser()
    result = []
    for path in sorted(root.glob('*.pkl')):
        with path.open('rb') as f:
            job = pickle.load(f)
        if job.get('state') == 'done' and 'res' in job:
            job['_source'] = path.name
            job['_archive_checked'] = __import__('datetime').datetime.fromtimestamp(
                path.stat().st_mtime, __import__('datetime').timezone.utc).date().isoformat()
            result.append(job)
    return result


def find_job(ra, dec, radius_arcsec=30):
    from skyshift_core.astrometry import tangent_offset_arcsec
    found = []
    from .jobs import completed
    native = completed()
    for job in (native if native and not os.environ.get('SKYSHIFT_RESULTS') else [*native, *jobs()]):
        dx, dy = tangent_offset_arcsec(ra, dec, job['ra'], job['dec'])
        found.append((float(np.hypot(dx, dy)), job))
    if not found or min(x[0] for x in found) > radius_arcsec:
        raise NotFoundError('No precomputed field here. Choose one of the five featured targets.')
    return min(found, key=lambda x: x[0])[1]


def target_for(job):
    from .resolve import builtins, position_now
    from skyshift_core.astrometry import tangent_offset_arcsec
    choices = []
    for target in builtins().values():
        ra, dec = position_now(target)
        dx, dy = tangent_offset_arcsec(job['ra'], job['dec'], ra, dec)
        choices.append((float(np.hypot(dx, dy)), target))
    distance, target = min(choices, key=lambda x: x[0])
    return target if distance < 30 else None
