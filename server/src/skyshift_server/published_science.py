"""Portable featured-field science products, independent of local analysis caches."""
import json
import math
from pathlib import Path

from .errors import NotFoundError


def field(ra: float, dec: float) -> Path | None:
    from .app import _repo_root
    from skyshift_core.astrometry import tangent_offset_arcsec

    root = _repo_root() / 'web/public/science-data'
    for path in sorted(root.glob('*/frames.json')):
        center = json.loads(path.read_text())['center']
        dx, dy = tangent_offset_arcsec(ra, dec, center['ra'], center['dec'])
        if math.hypot(float(dx), float(dy)) <= 2:
            return path.parent
    return None


def read(ra: float, dec: float, product: str, det: int | None = None) -> dict | None:
    if product not in ('science', 'analysis'):
        raise ValueError('Unknown published science product')
    folder = field(ra, dec)
    if folder is None:
        return None
    path = folder / f'{product}.json'
    if product == 'analysis' and det is not None:
        selected = folder / f'analysis-d{det}.json'
        if selected.is_file():
            path = selected
    if not path.is_file():
        return None
    result = json.loads(path.read_text())
    if product == 'analysis' and result.get('detector') != det:
        return None
    return result


def frames(ra: float, dec: float, fov: float, clock: str, det: int, view: str):
    """Return only an exact published grid; never relabel a different field or clock."""
    from .blink import Frames
    folder = field(ra, dec)
    if folder is None or fov != 5 or clock != 'months' or view not in ('model', 'resid', 'refdiff'):
        return None
    path = folder / f'{view}.json'
    if not path.is_file():
        return None
    meta = json.loads(path.read_text())
    meta['epochs'] = [e for e in meta['epochs'] if e['det'] == det]
    if not meta['epochs']:
        raise NotFoundError('No published analysis images for this detector')
    blobs = {e['data_url']: (folder / e['data_url']).read_bytes() for e in meta['epochs']}
    return Frames(meta, blobs)
