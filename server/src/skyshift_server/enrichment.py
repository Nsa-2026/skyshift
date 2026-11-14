"""On-demand service integrations, all behind the same API and byte cache."""
import csv
import io
import numpy as np
import requests
from skyshift_core.visits import visits
from .local_results import find_job
from .observations import science_result


def enrich(ra,dec,kind):
    job=find_job(ra,dec);s=job['summary'];r=job['res']
    if kind=='relay':
        from .science.algo import relay
        epochs=science_result(ra,dec)['bands']
        converted=[]
        for ep in epochs:
            converted.append(dict(mjd=ep['mjd'],**{b:(dict(vega_mag=ep[b]['vega_mag'],err_mJy=ep[b]['err_ujy']/1000,flux_mJy=ep[b]['flux_ujy']/1000) if ep[b] else None) for b in ('W1','W2')}))
        out=relay.relay_curve(ra,dec,converted,s.get('target_motion'))
        if out.get('error'):raise RuntimeError(out['error'])
        return dict(source='NASA/IPAC NEOWISE + SPHEREx synthetic W1/W2',**out)
    if kind=='comovers':
        from .science.algo import comover
        motion=s.get('target_motion')
        if not motion:return dict(source='ESA Gaia DR3',candidates=[],note='No significant target motion available')
        result=comover.find_comovers(ra,dec,**{k:motion[k] for k in ('pmra','pmdec','epmra','epmdec')})
        lum=comover.luminosity_from_spectrum(s.get('spectrum',[]),result[0]['distance_pc']) if result and result[0].get('distance_pc') else None
        return dict(source='ESA Gaia DR3 co-moving companions',candidates=result[:10],luminosity=lum)
    if kind=='catwise':
        radius=job['size']/120*1.42
        query=f"SELECT source_name,ra,dec,w1mpro,w2mpro,pmra,pmdec FROM catwise_2020 WHERE CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra},{dec},{radius}))=1"
        response=requests.get('https://irsa.ipac.caltech.edu/TAP/sync',params=dict(QUERY=query,FORMAT='csv'),timeout=60);response.raise_for_status()
        rows=[]
        for row in csv.DictReader(io.StringIO(response.text)):
            item=dict(name=row['source_name'])
            for k in ('ra','dec','w1mpro','w2mpro','pmra','pmdec'):
                try:item[k]=float(row[k])
                except (ValueError,TypeError):item[k]=None
            rows.append(item)
        from .analysis import cached_analysis
        from .local_results import target_for
        target=target_for(job);det=6 if target and target['slug']=='wise0855' else 2
        candidates=cached_analysis(ra,dec,det)['slow_movers']
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
    raise ValueError('Unknown enrichment')


def asteroids(job):
    from astropy.coordinates import SkyCoord
    from astropy.time import Time
    import astropy.units as u
    r=job['res'];v=visits(r['mjd']);c=SkyCoord(job['ra'],job['dec'],unit='deg')
    def one(visit):
        rows=[]
        mjd=float(np.mean(r['mjd'][v==visit]));when=Time(mjd,format='mjd').utc.strftime('%Y-%m-%d_%H:%M:%S')
        try:
            response=requests.get('https://ssd-api.jpl.nasa.gov/sb_ident.api',timeout=90,params={
                'mpc-code':'500','obs-time':when,'fov-ra-center':c.ra.to_string(u.hour,sep='-',precision=2,pad=True),
                'fov-dec-center':c.dec.to_string(u.deg,sep='-',precision=1,pad=True,alwayssign=True).replace('+',''),
                'fov-ra-hwidth':max(job['size']/120*1.2,.03),'fov-dec-hwidth':max(job['size']/120*1.2,.03),
                'two-pass':'true','suppress-first-pass':'true'})
            response.raise_for_status();data=response.json()
            if 'data_second_pass' not in data:raise RuntimeError(data.get('message','JPL returned no second-pass data'))
            for row in data.get('data_second_pass',[]) or []:
                pos=SkyCoord(row[1],row[2].replace("'",' ').replace('"',''),unit=(u.hourangle,u.deg))
                rows.append(dict(name=row[0],ra=pos.ra.deg,dec=pos.dec.deg,v=row[6],visit=int(visit),mjd=mjd))
            return rows,None
        except Exception as error:
            return [],dict(visit=int(visit),error=str(error)[:300])
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,np.unique(v)))
    rows=[row for group,_ in results for row in group]
    errors=[error for _,error in results if error]
    if len(errors)==len(results):raise RuntimeError(errors[0]['error'])
    return dict(source='NASA/JPL sb_ident; geocentric predictions',objects=rows,errors=errors,note=f'{len(errors)} visit queries failed; results are incomplete' if errors else 'All requested visits checked')
