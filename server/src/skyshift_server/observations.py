"""Single adapter from internal mJy science results to the public uJy product."""
import numpy as np
from skyshift_core.visits import visits
from .local_results import find_job


def science_result(ra,dec,*,cache=None):
    from .science.algo import spectrum
    job=find_job(ra,dec);s=job['summary'];r=job['res']
    groups=visits(r['mjd'])
    epochs=[]
    # Use full field time grouping even when some spectral samples are absent.
    for v in np.unique(groups):
        a=float(r['mjd'][groups==v].min());b=float(r['mjd'][groups==v].max())
        points=[p for p in s.get('spectrum',[]) if a<=p['mjd']<=b]
        bands={}
        for band in ('W1','W2'):
            value=spectrum.synth_band(points,band)
            bands[band]=None if value is None else dict(flux_ujy=value['flux_mJy']*1000,
                err_ujy=value['err_mJy']*1000,vega_mag=value['vega_mag'],n=value['n'])
        epochs.append(dict(visit=int(v),mjd=(a+b)/2,**bands))
    reference=s.get('reference',{})
    refspec=[dict(um=p['lam'],flux_ujy=p['flux']*1000) for p in reference.get('spectrum',[])]
    limits=[dict(um=p['lam'],flux_ujy_5sigma=p['flim_mjy']*1000,
                 ab_mag_5sigma=p['ab_mag_5sigma'],n_frames=p['n_frames']) for p in s.get('exclusion',{}).get('limits',[])]
    return dict(version=1,source=dict(kind='native' if job.get('_native') else 'legacy-cache',
                 coverage_arcmin=job['size'],n_frames=len(r['meta']),available_exposures=max([m.get('search_total_exposures',len(r['meta'])) for m in r['meta']]),flags_complete='flags' in r,
                 limitations=[] if job.get('_native') else ['Legacy mask differs from current SkyShift flags; quality fractions unavailable.']),
        target_motion=s.get('target_motion'),astrometry=s.get('astrometry',{}),
        lightcurve=s.get('halo_lightcurve',[]),bands=epochs,
        reference=dict(spectrum=refspec,error=reference.get('error'),source=reference.get('source')),
        sensitivity=dict(limits=limits,rmin=s.get('exclusion',{}).get('rmin',[]),
                         p9=s.get('exclusion',{}).get('p9',[]),wise0855_twin_reach_pc=s.get('exclusion',{}).get('wise0855_twin_reach_pc'),
                         note='Model-dependent 5-sigma sensitivity, not a discovery or global Planet X exclusion.'),
        frames=[dict(mjd=m['mjd'],det=m['det'],um=m.get('wavelength'),visit=int(groups[i]),
                     quality=m.get('quality',{}).get('grade'),flags=m.get('flags_at_target',{})) for i,m in enumerate(r['meta'])])


def reference(ra,dec):
    from .science import reference as ref
    job=find_job(ra,dec);r=job['res']
    if 'ref_cube' in r and job['summary'].get('reference',{}).get('version')==2:
        return science_result(ra,dec)
    if 'ref_cube' in r:
        cube,lam=r['ref_cube'],r['ref_lam']
    else:
        cube,lam,_=ref.reference_cube(ra,dec,job['wcs'],r['img'].shape[1])
    if cube is None:raise RuntimeError('No DR1 reference coverage returned')
    r['ref_cube']=cube;r['ref_lam']=lam
    from scipy.ndimage import gaussian_filter
    differences=[]
    for im,wave in zip(r['img'],r['lam']):
        model=ref.reference_at(cube,lam,wave)
        if model is None:
            differences.append(np.full_like(im,np.nan));continue
        filtered=gaussian_filter(np.nan_to_num(im),.8)
        filtered[~np.isfinite(im)]=np.nan
        differences.append(filtered-(model-np.nanmedian(model)))
    r['refdiff']=np.stack(differences)
    # Same aperture convention as the integrated science pipeline.
    n=cube.shape[1];yy,xx=np.mgrid[:n,:n];rr=np.hypot(xx-(n-1)/2,yy-(n-1)/2)
    ap=rr<=2.5;ann=(rr>5)&(rr<9);factor=(6.15/206265)**2*1e9
    points=[]
    for pl,wave in zip(cube,lam):
        if np.isfinite(pl[ap]).sum()<ap.sum()*.8:continue
        points.append(dict(lam=float(wave),flux=float((np.nansum(pl[ap])-np.nanmedian(pl[ann])*ap.sum())*factor)))
    job['summary']['reference']=dict(version=2,spectrum=points,source=ref.CUBE_PREFIX)
    if job.get('_storage_key'):
        from .jobs import save
        save(job['_storage_key'],job)
    return science_result(ra,dec)
