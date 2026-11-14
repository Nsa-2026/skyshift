"""IRSA SIA discovery + server-side cutouts. Shared flags/time/detector rules."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import os

import numpy as np
import requests
from astropy.wcs import WCS
from astropy.io import fits
from scipy.ndimage import map_coordinates
from skyshift_core.flags import bad_pixel_mask, RECORD_BITS, flag_frac, sat_frac
from skyshift_core.level2 import open_level2, detector_from_bandpass


def target_wcs(ra, dec, n):
    w = WCS(naxis=2); w.wcs.crpix=[(n+1)/2]*2; w.wcs.crval=[ra,dec]
    w.wcs.cdelt=[-6.15/3600,6.15/3600]; w.wcs.ctype=['RA---TAN','DEC--TAN']
    return w


def discover(ra, dec, size):
    from astroquery.ipac.irsa import Irsa
    table = Irsa.query_sia(pos=(ra,dec,size/120), collection=['spherex_dr1','spherex_qr3'])
    rows=[]; seen=set()
    # Prefer DR1 if an exposure is available in both releases.
    for row in sorted(table, key=lambda r:'dr1' not in str(r['obs_collection']).lower()):
        collection=str(row['obs_collection']).lower()
        if collection not in ('spherex_dr1','spherex_qr3'):
            continue
        url=str(row['access_url'])
        if urlparse(url).hostname != 'irsa.ipac.caltech.edu' or urlparse(url).scheme!='https':
            continue
        det=detector_from_bandpass(str(row['energy_bandpassname']))
        obs=str(row['obs_id']); key=(obs,det)
        if key in seen: continue
        seen.add(key)
        rows.append(dict(url=url,det=det,obs=obs,collection=collection,
                         mjd=(float(row['t_min'])+float(row['t_max']))/2))
    return sorted(rows,key=lambda r:(r['mjd'],r['det']))


def load_frames(ra,dec,size=5,progress=lambda _:None):
    root=Path(os.environ.get('SKYSHIFT_JOBS','~/.cache/skyshift/jobs')).expanduser()/'cutouts'
    rows=discover(ra,dec,size)
    available=len(rows)
    n=int(round(size*60/6.15))|1
    pixel_budget=int(os.environ.get('SKYSHIFT_MAX_CUBE_PIXELS','8000000'))
    limit=max(6,min(int(os.environ.get('SKYSHIFT_MAX_FRAMES','800')),pixel_budget//(n*n)))
    if len(rows)>limit:
        # Sample complete observing groups, preserving simultaneous detector pairs.
        groups=list(dict.fromkeys(r['obs'] for r in rows))
        take=max(1,limit//6)
        chosen={groups[i] for i in np.linspace(0,len(groups)-1,min(take,len(groups))).round().astype(int)}
        rows=[r for r in rows if r['obs'] in chosen]
    progress(f'IRSA: {len(rows)} / {available} exposure cutouts (bounded sampling)' if len(rows)<available else f'IRSA: {len(rows)} exposure cutouts')
    n=int(round(size*60/6.15))|1; w=target_wcs(ra,dec,n)
    yy,xx=np.mgrid[:n,:n]; sky=w.all_pix2world(np.c_[xx.ravel(),yy.ravel()],0)
    failures=[]
    def one(row):
        try:
            key=hashlib.sha256(f"{row['url']}:{ra}:{dec}:{size}".encode()).hexdigest()
            path=root/(key+'.fits');path.parent.mkdir(parents=True,exist_ok=True)
            pixel_source='IRSA cutout'
            try:
                if not path.exists():
                    response=requests.get(row['url'],params={'center':f'{ra},{dec}','size':size/60},timeout=90)
                    response.raise_for_status(); temp=path.with_suffix('.tmp');temp.write_bytes(response.content);temp.replace(path)
                frame=open_level2(path,variance=True)
            except (requests.RequestException, OSError):
                # IRSA cutouts sometimes return 503. Preserve cutout-only IO by
                # reading a rectangular region via HTTP Range from NASA's mirror.
                prefix='/ibe/data/spherex/'
                parsed=urlparse(row['url'])
                if not parsed.path.startswith(prefix):raise
                mirror='https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/'+parsed.path[len(prefix):]
                with fits.open(mirror,use_fsspec=True,fsspec_kwargs={'block_size':65536}) as hdus:
                    header=hdus['IMAGE'].header.copy()
                original=WCS(header).celestial
                xy=original.all_world2pix(sky,0)
                x0=max(0,int(np.floor(xy[:,0].min()))-2);x1=min(header['NAXIS1'],int(np.ceil(xy[:,0].max()))+3)
                y0=max(0,int(np.floor(xy[:,1].min()))-2);y1=min(header['NAXIS2'],int(np.ceil(xy[:,1].max()))+3)
                if x1<=x0 or y1<=y0:raise RuntimeError('Target outside this exposure')
                frame=open_level2(mirror,variance=True,region=(x0,x1,y0,y1))
                pixel_source='NASA S3 HTTP Range fallback'
            if frame.detector!=row['det']: raise ValueError('detector header disagrees with SIA metadata')
            if frame.variance is None: raise ValueError('VARIANCE missing')
            xy=frame.wcs.all_world2pix(sky,0);coords=[xy[:,1].reshape(n,n),xy[:,0].reshape(n,n)]
            image=map_coordinates(frame.data,coords,order=1,mode='constant',cval=np.nan)
            var=map_coordinates(frame.variance,coords,order=1,mode='constant',cval=np.nan)
            flags=map_coordinates(frame.flags,coords,order=0,mode='constant',cval=1<<9).astype(np.int32)
            waves=map_coordinates(frame.wavelength_map(),coords,order=1,mode='constant',cval=np.nan)
            hist={}
            from skyshift_core.flags import bit_names
            for bit in bit_names(int(np.bitwise_or.reduce(flags[n//2-2:n//2+3,n//2-2:n//2+3].ravel(),initial=0))):hist[bit]=1
            meta=dict(key=f"{row['collection'].removeprefix('spherex_')}/irsa/{key}",
                det=frame.detector,obs=frame.obsid or row['obs'],expo=0,ver=row['collection'],mjd=frame.mjd_avg,
                mjd_beg=frame.mjd_beg,mjd_end=frame.mjd_end,date=datetime.fromtimestamp((frame.mjd_avg-40587)*86400,timezone.utc).isoformat(),
                search_total_exposures=available,pixel_source=pixel_source,wavelength=float(waves[n//2,n//2]),um_min=float(np.nanmin(waves)),um_max=float(np.nanmax(waves)),
                flag_frac=flag_frac(flags),sat_frac=sat_frac(flags),flags_at_target=hist,quality=dict(weight=1.,grade='ungraded'))
            return dict(image=image,var=var,zodi=np.zeros_like(image),flags=flags,bad=bad_pixel_mask(flags),
                        artifact=(flags & RECORD_BITS)!=0,meta=meta)
        except Exception as error:
            failures.append(str(error));return None
    with ThreadPoolExecutor(max_workers=6) as pool:frames=[f for f in pool.map(one,rows) if f is not None]
    if failures:progress(f'{len(failures)} cutouts unavailable: {failures[0][:160]}')
    if not frames:raise RuntimeError('No usable SPHEREx cutouts returned by IRSA'+(': '+failures[0] if failures else ''))
    frames.sort(key=lambda f:(f['meta']['mjd'],f['meta']['det']))
    return frames,w
