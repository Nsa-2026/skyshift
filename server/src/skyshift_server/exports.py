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
    from .errors import NotFoundError
    from .published_science import read
    if format in ('json','csv','gif') and read(ra,dec,'science') is not None:
        return static_download(ra,dec,det,clock,format)
    try:job=find_job(ra,dec)
    except NotFoundError:return static_download(ra,dec,det,clock,format)
    r=job['res']
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


def static_download(ra,dec,det,clock,format):
    """Published moving-object fields have no time-machine cube: use their raw twins."""
    from .app import _repo_root
    from .errors import NotFoundError
    from skyshift_core.astrometry import tangent_offset_arcsec
    import json
    from .published_science import read
    science=read(ra,dec,'science')
    if science is not None and format=='json':
        return json.dumps(science,ensure_ascii=False).encode(),'application/json','science.json'
    if science is not None and format=='csv':
        out=io.StringIO();writer=csv.DictWriter(out,fieldnames=['mjd','det','um','visit','quality','flags']);writer.writeheader();writer.writerows(science['frames'])
        return out.getvalue().encode(),'text/csv','exposures.csv'
    if science is not None and format=='gif':
        from .published_science import field
        from .science import render
        from .stack import decode
        folder=field(ra,dec);frames=json.loads((folder/'frames.json').read_text())
        epochs=[e for e in frames['epochs'] if e['det']==det]
        if frames['clock']!=clock or not epochs:
            raise NotFoundError('No published science frames for this clock and detector')
        nx,ny=frames['size']
        images=[decode((folder/e['data_url']).read_bytes(),e['scale'],e['zero'],(ny,nx))[::-1] for e in epochs]
        return render.gif(images,render.stretch_limits(np.array(images)),labels=[f"D{det} MJD {e['mjd_mid']:.5f}" for e in epochs],scale=2,ms=600),'image/gif','science-blink.gif'
    root=_repo_root()/'web/public/data'
    for index_path in root.glob('*/index.json'):
        meta=json.loads(index_path.read_text());center=meta['center']
        dx,dy=tangent_offset_arcsec(ra,dec,center['ra'],center['dec'])
        if float(np.hypot(dx,dy))>2:continue
        entry=next((f for f in meta['frames'] if f['clock']==clock and det in f['dets']),None)
        if entry is None:raise NotFoundError('No published frames for this detector and clock')
        folder=index_path.parent;frames=json.loads((folder/entry['file']).read_text());epochs=[e for e in frames['epochs'] if e['det']==det]
        if format=='json':return json.dumps(dict(frames=frames,object=json.loads((folder/'object.json').read_text())),ensure_ascii=False).encode(),'application/json','published-observations.json'
        if format=='csv':
            out=io.StringIO();writer=csv.DictWriter(out,fieldnames=['id','mjd_mid','det','n_exposures']);writer.writeheader();writer.writerows({k:e[k] for k in writer.fieldnames} for e in epochs)
            return out.getvalue().encode(),'text/csv','observations.csv'
        if format=='gif':
            from .science import render
            from .stack import decode
            nx,ny=frames['size'];images=[decode((folder/e['data_url']).read_bytes(),e['scale'],e['zero'],(ny,nx))[::-1] for e in epochs]
            return render.gif(images,render.stretch_limits(np.array(images)),labels=[f"D{det} MJD {e['mjd_mid']:.5f}" for e in epochs],scale=2,ms=600),'image/gif','blink.gif'
        raise NotFoundError('No measured mover tracks published for this field; astrometry drafts are unavailable')
    raise NotFoundError('No cached analysis or published field at this position')
