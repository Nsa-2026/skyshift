"""One bounded job queue for new coordinates; safe NPZ/JSON persistence, no Flask."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import threading
import uuid

import numpy as np
from astropy.wcs import WCS

_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='skyshift-science')
_lock=threading.RLock();_states={};_completed={}


def root():return Path(os.environ.get('SKYSHIFT_JOBS','~/.cache/skyshift/jobs')).expanduser()


def start(ra,dec,size=5):
    key=hashlib.sha256(f'v1:{ra:.7f}:{dec:.7f}:{size}'.encode()).hexdigest()[:24]
    if load(key) is not None:
        return status(key)
    with _lock:
        if key in _states and _states[key]['state']!='error':return dict(_states[key])
        if len([s for s in _states.values() if s['state'] in ('queued','running')])>=3:
            from .errors import ApiError
            raise ApiError(429,'upstream','Analysis queue is full; try again after the current field completes')
        _states[key]=dict(id=key,ra=ra,dec=dec,size=size,state='queued',progress=[],error=None)
        _pool.submit(_run,key,ra,dec,size)
        return dict(_states[key])


def _run(key,ra,dec,size):
    from .ingest import load_frames
    from .science.pipeline import analyse
    def progress(msg):
        with _lock:_states[key]['progress'].append(str(msg))
    with _lock:_states[key]['state']='running'
    try:
        frames,wcs=load_frames(ra,dec,size,progress)
        summary,res=analyse(frames,wcs,ra,dec,progress=progress)
        job=dict(ra=ra,dec=dec,size=size,state='done',summary=summary,res=res,wcs=wcs,
                 _source=key,_archive_checked=datetime.now(timezone.utc).date().isoformat(),_native=True)
        save(key,job)
        with _lock:_completed[key]=job;_states[key]['state']='done'
    except Exception as e:
        with _lock:_states[key]['state']='error';_states[key]['error']=str(e)[:500]


def status(key):
    with _lock:
        if key in _states:return {**_states[key],'progress':list(_states[key]['progress'])}
    job=load(key)
    if job:return dict(id=key,state='done',ra=job['ra'],dec=job['dec'],size=job['size'],progress=[],error=None)
    from .errors import NotFoundError
    raise NotFoundError('No such analysis job')


def save(key,job):
    from .app import jsonable
    job['_storage_key']=key
    dest=root()/key;dest.mkdir(parents=True,exist_ok=True)
    arrays={k:v for k,v in job['res'].items() if isinstance(v,np.ndarray)}
    token=uuid.uuid4().hex
    temp=dest/(token+'.npz');np.savez_compressed(temp,**arrays);temp.replace(dest/'arrays.npz')
    meta={k:v for k,v in job.items() if k not in ('res','wcs')}
    meta['res']={k:v for k,v in job['res'].items() if k not in arrays}
    meta['wcs']=job['wcs'].to_header().tostring()
    temp=dest/(token+'.json');temp.write_text(json.dumps(jsonable(meta),allow_nan=False));temp.replace(dest/'job.json')


def load(key):
    import re
    if not re.fullmatch('[a-f0-9]{24}',key):return None
    with _lock:
        if key in _completed:return _completed[key]
    dest=root()/key
    if not (dest/'job.json').is_file():return None
    from astropy.io.fits import Header
    job=json.loads((dest/'job.json').read_text());job['_storage_key']=key;job['wcs']=WCS(Header.fromstring(job['wcs']))
    with np.load(dest/'arrays.npz',allow_pickle=False) as data:job['res'].update({k:data[k] for k in data.files})
    with _lock:_completed[key]=job
    return job


def completed():
    for p in root().glob('*/job.json'):
        load(p.parent.name)
    with _lock:return list(_completed.values())
