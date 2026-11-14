"""IRSA SIA discovery + server-side cutouts. Shared flags/time/detector rules."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os

import numpy as np
from astropy.wcs import WCS
from scipy.ndimage import map_coordinates
from skyshift_core.flags import bad_pixel_mask, RECORD_BITS, flag_frac, sat_frac


def target_wcs(ra, dec, n):
    w = WCS(naxis=2); w.wcs.crpix=[(n+1)/2]*2; w.wcs.crval=[ra,dec]
    w.wcs.cdelt=[-6.15/3600,6.15/3600]; w.wcs.ctype=['RA---TAN','DEC--TAN']
    return w


def discover(ra,dec,size):
    from . import search
    from .app import get_cache
    return search.search(ra,dec,cache=get_cache()).images


def load_frames(ra,dec,size=5,progress=lambda _:None):
    rows=discover(ra,dec,size)
    available=len(rows)
    n=int(round(size*60/6.15))|1
    pixel_budget=int(os.environ.get('SKYSHIFT_MAX_CUBE_PIXELS','8000000'))
    limit=max(6,min(int(os.environ.get('SKYSHIFT_MAX_FRAMES','800')),pixel_budget//(n*n)))
    if len(rows)>limit:
        # Sample complete observing groups, preserving simultaneous detector pairs.
        groups=list(dict.fromkeys(r.obs_id for r in rows))
        take=max(1,limit//6)
        chosen={groups[i] for i in np.linspace(0,len(groups)-1,min(take,len(groups))).round().astype(int)}
        rows=[r for r in rows if r.obs_id in chosen]
    progress(f'IRSA: {len(rows)} / {available} exposure cutouts (bounded sampling)' if len(rows)<available else f'IRSA: {len(rows)} exposure cutouts')
    n=int(round(size*60/6.15))|1; w=target_wcs(ra,dec,n)
    yy,xx=np.mgrid[:n,:n]; sky=w.all_pix2world(np.c_[xx.ravel(),yy.ravel()],0)
    failures=[]
    from . import cutouts
    from .app import get_cache
    levels=cutouts.fetch_many(rows,ra,dec,cutouts.tier_for_field(size),get_cache(),variance=True,zodi=True)
    def one(pair):
        row,frame=pair
        try:
            if frame is None:return None
            if frame.detector!=row.det:raise ValueError('detector header disagrees with SIA metadata')
            if frame.variance is None: raise ValueError('VARIANCE missing')
            xy=frame.wcs.all_world2pix(sky,0);coords=[xy[:,1].reshape(n,n),xy[:,0].reshape(n,n)]
            image=map_coordinates(frame.data,coords,order=1,mode='constant',cval=np.nan)
            var=map_coordinates(frame.variance,coords,order=1,mode='constant',cval=np.nan)
            flags=map_coordinates(frame.flags,coords,order=0,mode='constant',cval=1<<9).astype(np.int32)
            waves=map_coordinates(frame.wavelength_map(),coords,order=1,mode='constant',cval=np.nan)
            hist={}
            from skyshift_core.flags import bit_names
            for bit in bit_names(int(np.bitwise_or.reduce(flags[n//2-2:n//2+3,n//2-2:n//2+3].ravel(),initial=0))):hist[bit]=1
            meta=dict(key=f"{row.collection.removeprefix('spherex_')}/irsa/{row.key}",
                det=frame.detector,obs=row.obs_id,expo=0,ver=row.collection,mjd=frame.mjd_avg,
                mjd_beg=frame.mjd_beg,mjd_end=frame.mjd_end,date=datetime.fromtimestamp((frame.mjd_avg-40587)*86400,timezone.utc).isoformat(),
                search_total_exposures=available,pixel_source='SkyShift canonical cutouts',zodi_subtracted=bool(frame.header.get('ZODISUB',False)),wavelength=float(waves[n//2,n//2]),um_min=float(np.nanmin(waves)),um_max=float(np.nanmax(waves)),
                flag_frac=flag_frac(flags),sat_frac=sat_frac(flags),flags_at_target=hist,quality=dict(weight=1.,grade='ungraded'))
            return dict(image=image,var=var,zodi=np.zeros_like(image),flags=flags,bad=bad_pixel_mask(flags),
                        artifact=(flags & RECORD_BITS)!=0,meta=meta)
        except Exception as error:
            failures.append(str(error));return None
    with ThreadPoolExecutor(max_workers=6) as pool:frames=[f for f in pool.map(one,zip(rows,levels)) if f is not None]
    if failures:progress(f'{len(failures)} cutouts unavailable: {failures[0][:160]}')
    if not frames:raise RuntimeError('No usable SPHEREx cutouts returned by IRSA'+(': '+failures[0] if failures else ''))
    frames.sort(key=lambda f:(f['meta']['mjd'],f['meta']['det']))
    return frames,w
