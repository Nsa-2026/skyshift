"""On-demand service integrations, all behind the same API and byte cache.

A failed service is reported, never replaced: the route answers 502 "upstream" naming the service
(IRSA, ESA Gaia) and nothing is cached. Known asteroids and comets come only from MPC orbits moved
with kete (known.py, the same source as /api/known); no other small-body service is queried.
"""
import csv
import io
import numpy as np
import requests
from skyshift_core.visits import sessions, visits
from .errors import ApiError, NotFoundError, UpstreamError
from .local_results import find_job
from .observations import science_result


def _service(name, fn, *args, **kwargs):
    """fn(*args) with any failure reported as 502 upstream that names the service."""
    try:
        return fn(*args, **kwargs)
    except ApiError:
        raise
    except Exception as error:
        raise UpstreamError(f'{name} did not answer ({type(error).__name__}: {str(error)[:200]}). '
                            'No catalogue results are shown; try again later.') from error


def enrich(ra,dec,kind):
    from .published_science import read, field
    published = read(ra,dec,'science')
    try:
        job=find_job(ra,dec)
    except NotFoundError:
        if published is None:
            raise
        # These catalogue checks need coordinates, dates and spectra, not the
        # private native image cube. Use the versioned published products.
        import json
        obj=json.loads((field(ra,dec)/'object.json').read_text())
        spectrum=[dict(lam=p['um'],flux=p['flux_ujy']/1000) for p in obj['spectrum']
                  if p['flux_ujy'] is not None and not p['saturated']]
        job=dict(ra=ra,dec=dec,size=published['source']['coverage_arcmin'],
                 summary=dict(target_motion=published['target_motion'],spectrum=spectrum),
                 res=dict(mjd=np.array([p['mjd'] for p in published['frames']])))
    s=job['summary'];r=job['res']
    if kind=='relay':
        from .science.algo import relay
        epochs=(published if published is not None else science_result(ra,dec))['bands']
        converted=[]
        for ep in epochs:
            converted.append(dict(mjd=ep['mjd'],**{b:(dict(vega_mag=ep[b]['vega_mag'],err_mJy=ep[b]['err_ujy']/1000,flux_mJy=ep[b]['flux_ujy']/1000) if ep[b] else None) for b in ('W1','W2')}))
        out=relay.relay_curve(ra,dec,converted,s.get('target_motion'))
        if out.get('error'):raise UpstreamError(f"NASA/IPAC IRSA (NEOWISE) did not answer: {out['error'][:200]}. "
                                                'No light curve is shown; try again later.')
        return dict(source='NASA/IPAC NEOWISE + SPHEREx synthetic W1/W2',**out)
    if kind=='comovers':
        from .science.algo import comover
        motion=s.get('target_motion')
        if not motion:return dict(source='ESA Gaia DR3',candidates=[],note='No significant target motion available')
        rows=_service('ESA Gaia archive',comover.query_gaia,ra,dec)
        result=comover.find_comovers(ra,dec,**{k:motion[k] for k in ('pmra','pmdec','epmra','epmdec')},rows=rows)
        lum=comover.luminosity_from_spectrum(s.get('spectrum',[]),result[0]['distance_pc']) if result and result[0].get('distance_pc') else None
        return dict(source='ESA Gaia DR3 co-moving companions',candidates=result[:10],luminosity=lum)
    if kind=='catwise':
        radius=job['size']/120*1.42
        query=f"SELECT source_name,ra,dec,w1mpro,w2mpro,pmra,pmdec FROM catwise_2020 WHERE CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra},{dec},{radius}))=1"
        def fetch():
            response=requests.get('https://irsa.ipac.caltech.edu/TAP/sync',params=dict(QUERY=query,FORMAT='csv'),timeout=20)
            response.raise_for_status()
            return response.text
        text=_service('NASA/IPAC IRSA (CatWISE2020)',fetch)
        rows=[]
        for row in csv.DictReader(io.StringIO(text)):
            item=dict(name=row['source_name'])
            for k in ('ra','dec','w1mpro','w2mpro','pmra','pmdec'):
                try:item[k]=float(row[k])
                except (ValueError,TypeError):item[k]=None
            rows.append(item)
        from .analysis import cached_analysis
        from .local_results import target_for
        target=target_for(job);det=6 if target and target['slug']=='wise0855' else 2
        analysis=read(ra,dec,'analysis',det)
        try:candidates=(analysis if analysis is not None else cached_analysis(ra,dec,det))['slow_movers']
        except NotFoundError:candidates=[]  # no analysis here: the catalogue rows still answer
        for i,candidate in enumerate(candidates):
            dt=(candidate['mjd_ref']-57170.0)/365.25
            cosd=np.cos(np.radians(candidate['dec']))
            a=candidate['ra']-candidate['pmra_arcsec_yr']*dt/(3600*cosd)
            d=candidate['dec']-candidate['pmdec_arcsec_yr']*dt/3600
            nearest=None
            for entry in rows:
                if entry['ra'] is None or entry['dec'] is None:continue
                sep=float(np.hypot((entry['ra']-a)*cosd,entry['dec']-d)*3600)
                if sep<12 and (nearest is None or sep<nearest[0]):nearest=(sep,entry)
            if nearest:
                nearest[1]['candidate']=i+1;nearest[1]['separation_arcsec']=round(nearest[0],3)
                nearest[1]['pm_difference_arcsec_yr']=float(np.hypot((nearest[1]['pmra'] or 0)-candidate['pmra_arcsec_yr'],(nearest[1]['pmdec'] or 0)-candidate['pmdec_arcsec_yr']))
        return dict(source='NASA/IPAC CatWISE2020; proper motions in arcsec/yr',sources=rows,
                    note='Catalog entries are provided for vetting; positional coincidence alone is not identification.')
    if kind=='asteroids':return asteroids(job)
    raise ApiError(400,'bad_request','unknown enrichment kind')


def asteroids(job):
    """Known asteroids and comets in the field at each observing session's mid-time (an asteroid
    crosses a 5' field within hours, so a visit's mean time would miss it): MPC orbits moved with
    kete, the source of /api/known. Predicted from the Earth's centre; V <= 21, comets kept."""
    from . import known as known_mod
    from .app import get_cache
    r=job['res'];t=np.asarray(r['mjd'],float)
    if t.size==0:raise NotFoundError('No observation times for this field')
    v=visits(t);s=sessions(t)
    keys=sorted({(int(a),int(b)) for a,b in zip(v,s)},key=lambda k:k[1])
    mjds=[float(t[s==b].mean()) for _,b in keys]
    radius=max(job['size']*0.6*1.42,1.0)  # arcmin: the field's corners, at least 1'
    cache=get_cache()
    index=known_mod.mpc_index(cache)  # UpstreamError (502) when the index is missing
    inside=[k for k,m in enumerate(mjds) if index.mjd_lo<=m<=index.mjd_hi]
    errors=[dict(visit=keys[k][0],mjd=mjds[k],error='outside the MPC index time range')
            for k in range(len(mjds)) if k not in inside]
    if not inside:raise NotFoundError('These observations are outside the known-object index time range')
    found=known_mod.near(job['ra'],job['dec'],radius,[mjds[k] for k in inside],epoch_ids=inside,cache=cache)
    rows=[dict(name=f['name'],ra=f['ra'],dec=f['dec'],v=f.get('v'),visit=keys[f['epoch_id']][0],
               mjd=mjds[f['epoch_id']]) for f in found]
    note=(f'{len(errors)} of {len(mjds)} sessions are outside the index; results are incomplete' if errors
          else f'All {len(mjds)} observing sessions checked')
    return dict(source='Minor Planet Center orbits (kete); geocentric predictions, V <= 21',
                objects=rows,errors=errors,note=note)
