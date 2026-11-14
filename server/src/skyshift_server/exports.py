"""Downloads share the same detector/visit selections as the player."""
import io
import csv
import numpy as np
from skyshift_core.visits import visits
from .local_results import find_job
from .observations import science_result


def download(ra,dec,det,clock,format):
    from .app import jsonable
    import json
    job=find_job(ra,dec);r=job['res']
    if format=='json':return json.dumps(jsonable(science_result(ra,dec)),ensure_ascii=False).encode(),'application/json','science.json'
    if format=='gif':
        from .science import render
        indices=np.array([m['det']==det for m in r['meta']]);groups=visits(r['mjd'])
        if clock=='months':
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',RuntimeWarning)
                images=[np.nanmedian(r['img'][indices & (groups==v)],axis=0) for v in np.unique(groups[indices])]
            labels=[f'D{det} visit {v}' for v in np.unique(groups[indices])]
        else:
            images=list(r['img'][indices]);labels=[f'D{det} MJD {m:.4f}' for m in r['mjd'][indices]]
        if not images:raise ValueError('No frames for this detector')
        return render.gif(images,render.stretch_limits(np.array(images)),labels=labels,scale=3,ms=600),'image/gif','blink.gif'
    if format=='csv':
        out=io.StringIO();writer=csv.DictWriter(out,fieldnames=['mjd','det','um','visit','quality','flags']);writer.writeheader();writer.writerows(science_result(ra,dec)['frames'])
        return out.getvalue().encode(),'text/csv','exposures.csv'
    if format in ('ades','mpc80'):
        from .analysis import cached_analysis
        from astropy.time import Time
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        tracks=cached_analysis(ra,dec,det)['fast_movers'];rows=[]
        if format=='ades':rows=['# version=2017','# DRAFT: unverified astrometry; observatory code must be assigned before submission.','trkSub|mode|stn|obsTime|ra|dec|remarks']
        for n,track in enumerate(tracks):
            for point in track['detections']:
                m=r['meta'][point['t']];a,d=job['wcs'].all_pix2world([[point['x'],point['y']]],0)[0];when=Time(m['mjd'],format='mjd')
                if format=='ades':rows.append(f"SPX{n:04d}|CCD||{when.isot}Z|{a:.7f}|{d:+.7f}|unvetted; p_false={track['p_false']:.4f}")
                else:
                    dt=when.datetime;day=dt.day+(dt.hour*3600+dt.minute*60+dt.second+dt.microsecond/1e6)/86400
                    c=SkyCoord(a,d,unit='deg');ra_s=c.ra.to_string(u.hour,sep=' ',precision=2,pad=True);de_s=c.dec.to_string(u.deg,sep=' ',precision=1,pad=True,alwayssign=True)
                    rows.append(f"     SPX{n:04d}  C{dt.year:04d} {dt.month:02d} {day:08.5f} {ra_s} {de_s}".ljust(77)+'XXX')
        return ('\n'.join(rows)+'\n').encode(),'text/plain','draft.ades.psv' if format=='ades' else 'draft.mpc80.txt'
    raise ValueError('Unsupported export')
