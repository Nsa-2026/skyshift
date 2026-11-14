"""Cross-module integration: one service, units, native masks, persistence, exports."""
from pathlib import Path
import json
import numpy as np
import pytest
from astropy.wcs import WCS
from fastapi.testclient import TestClient

from skyshift_server import app, jobs, local_results, observations, ingest, enrichment
from skyshift_server.cache import Cache
from skyshift_core.visits import visits


def make_job():
    w=WCS(naxis=2);w.wcs.crval=[10,20];w.wcs.crpix=[20,20];w.wcs.cdelt=[-6.15/3600,6.15/3600];w.wcs.ctype=['RA---TAN','DEC--TAN']
    yy,xx=np.mgrid[:39,:39];image=np.exp(-((xx-19)**2+(yy-19)**2)/2)
    image=np.stack([image,image*1.1,image*1.3]);times=np.array([60000.,60050.,60200.])
    meta=[dict(mjd=float(t),det=2,key='dr1/example',obs=str(i),expo=0,ver='dr1',wavelength=1.3,
               quality=dict(weight=1.,grade='A'),flags_at_target={}) for i,t in enumerate(times)]
    summary=dict(spectrum=[dict(lam=1.3,flux=.2,err=.01,mjd=float(t),det=2,method='psf') for t in times],
                 halo_lightcurve=[],reference={},astrometry={},exclusion={})
    return dict(ra=10.,dec=20.,size=4.,state='done',_native=True,_source='test',_archive_checked='2026-11-14',wcs=w,summary=summary,
                res=dict(img=image,var=np.ones_like(image),model=image,resid=image*0,mjd=times,lam=np.full(3,1.3),
                         epoch=np.array([99]*3),flags=np.zeros_like(image,dtype=np.int32),fwhm=np.full(3,1.8),meta=meta,dets=[]))


def test_unified_visit_and_public_units(monkeypatch):
    from skyshift_server.science.pipeline import epochs_of
    from skyshift_server.science.algo.spectrum import epoch_split
    job=make_job();monkeypatch.setattr(observations,'find_job',lambda *a:job)
    assert epochs_of(job['res']['mjd']).tolist()==(visits(job['res']['mjd'])-1).tolist()
    assert len(epoch_split(job['summary']['spectrum']))==2
    out=observations.science_result(10,20)
    assert [p['visit'] for p in out['frames']]==[1,1,2]
    assert out['source']['flags_complete'] is True
    # Internal 0.2 mJy appears as 200 uJy, not 0.2 uJy.
    job['summary']['exclusion']={'limits':[dict(lam=1.3,flim_mjy=.2,ab_mag_5sigma=18,n_frames=3)]}
    assert observations.science_result(10,20)['sensitivity']['limits'][0]['flux_ujy_5sigma']==200


def test_job_safe_roundtrip_and_path_confinement(tmp_path,monkeypatch):
    monkeypatch.setenv('SKYSHIFT_JOBS',str(tmp_path));job=make_job();key='a'*24
    jobs.save(key,job);restored=jobs.load(key)
    np.testing.assert_equal(restored['res']['flags'],job['res']['flags'])
    np.testing.assert_allclose(restored['wcs'].wcs.crval,[10,20])
    assert jobs.load('../../elsewhere') is None
    assert json.loads((tmp_path/key/'job.json').read_text())['_native'] is True
    with np.load(tmp_path/key/'arrays.npz',allow_pickle=False) as arrays:assert arrays['img'].shape==(3,39,39)
    jobs._completed.pop(key,None)


def test_queue_deduplicates_and_reports_failures(tmp_path,monkeypatch):
    monkeypatch.setenv('SKYSHIFT_JOBS',str(tmp_path));jobs._states.clear()
    submitted=[];monkeypatch.setattr(jobs._pool,'submit',lambda *a:submitted.append(a))
    a=jobs.start(1,2);b=jobs.start(1,2)
    assert a['id']==b['id'] and len(submitted)==1
    monkeypatch.setattr(ingest,'load_frames',lambda *a:(_ for _ in ()).throw(RuntimeError('IRSA unavailable')))
    jobs._run(a['id'],1,2,5)
    assert jobs.status(a['id'])['state']=='error'
    assert 'IRSA unavailable' in jobs.status(a['id'])['error']
    jobs._states.clear()


def test_frames_alternate_view_and_export_use_same_source(tmp_path,monkeypatch):
    from skyshift_server import exports
    job=make_job();monkeypatch.setattr(local_results,'find_job',lambda *a:job)
    monkeypatch.setattr(exports,'find_job',lambda *a:job)
    app.reset_cache(Cache(str(tmp_path)));client=TestClient(app.app)
    raw=client.get('/api/frames?ra=10&dec=20&det=2');resid=client.get('/api/frames?ra=10&dec=20&det=2&view=resid')
    assert raw.status_code==resid.status_code==200
    assert raw.json()['epochs'][0]['data_url']!=resid.json()['epochs'][0]['data_url']
    assert client.get('/api/frames?ra=10&dec=20&view=invalid').status_code==400
    gif=client.get('/api/export?ra=10&dec=20&det=2&format=gif')
    assert gif.status_code==200 and gif.content.startswith(b'GIF')
    assert client.get('/api/export?ra=10&dec=20&format=invalid').status_code==400
    app.reset_cache(None)


def test_enrichment_error_is_structured_and_retryable(tmp_path,monkeypatch):
    app.reset_cache(Cache(str(tmp_path)));client=TestClient(app.app)
    monkeypatch.setattr(enrichment,'enrich',lambda *a:(_ for _ in ()).throw(TimeoutError('test upstream timeout')))
    result=client.get('/api/enrichment?ra=10&dec=20&kind=relay')
    assert result.status_code==502 and result.json()['error']['code']=='upstream'
    assert client.get('/api/enrichment?ra=10&dec=20&kind=bad').status_code==400
    app.reset_cache(None)


def test_native_pipeline_runs_without_old_server(monkeypatch,tmp_path):
    from skyshift_server.science.pipeline import analyse
    job=make_job();r=job['res'];frames=[]
    for i in range(3):
        frames.append(dict(image=r['img'][i],var=r['var'][i],zodi=np.zeros((39,39)),
                           flags=r['flags'][i],bad=np.zeros((39,39),bool),artifact=np.zeros((39,39),bool),meta=r['meta'][i]))
    out,result=analyse(frames,job['wcs'],10,20)
    assert out['epochs']==2 and result['flags'].dtype==np.int32
    assert 'dropouts' not in out and 'slow_movers' not in out # one candidate owner
    assert result['epoch'].tolist()==[0,0,1]


def test_native_store_is_used_without_reading_legacy_pickle(monkeypatch):
    monkeypatch.delenv('SKYSHIFT_RESULTS',raising=False)
    job=make_job()
    monkeypatch.setattr(jobs,'completed',lambda:[job])
    monkeypatch.setattr(local_results,'jobs',lambda:(_ for _ in ()).throw(AssertionError('legacy pickle was accessed')))
    assert local_results.find_job(10,20) is job


def test_science_loader_reuses_canonical_cutouts(monkeypatch):
    from types import SimpleNamespace
    from skyshift_server import cutouts
    w=ingest.target_wcs(10,20,49)
    frame=SimpleNamespace(data=np.ones((49,49)),variance=np.ones((49,49)),flags=np.zeros((49,49),np.int32),
                          wcs=w,detector=2,obsid='one',mjd_avg=60000.,mjd_beg=59999.99,mjd_end=60000.01,
                          header={'ZODISUB':True},wavelength_map=lambda:np.full((49,49),1.3))
    row=SimpleNamespace(obs_id='one',det=2,collection='spherex_dr1',key='oneD2')
    monkeypatch.setattr(ingest,'discover',lambda *a:[row])
    calls=[]
    monkeypatch.setattr(cutouts,'fetch_many',lambda *a,**kw:calls.append(kw) or [frame])
    frames,_=ingest.load_frames(10,20,5)
    assert calls==[{'variance':True,'zodi':True}]
    assert frames[0]['meta']['zodi_subtracted'] is True
    assert frames[0]['bad'].dtype==bool and frames[0]['flags'].dtype==np.int32



def test_reference_matches_background_convention_and_reuses_cached_cube(monkeypatch):
    from skyshift_server.science import reference as ref
    job=make_job();monkeypatch.setattr(observations,'find_job',lambda *a:job)
    cube=np.full((3,39,39),10.);lam=np.array([1.,1.3,2.])
    monkeypatch.setattr(ref,'reference_cube',lambda *a:(cube,lam,np.ones(3)))
    result=observations.reference(10,20)
    assert job['summary']['reference']['version']==2
    assert abs(float(np.nanmedian(job['res']['refdiff'])))<.01
    assert len(result['reference']['spectrum'])==3
    monkeypatch.setattr(ref,'reference_cube',lambda *a:(_ for _ in ()).throw(AssertionError('reference was downloaded twice')))
    assert len(observations.reference(10,20)['reference']['spectrum'])==3


def test_published_comet_exports_without_a_legacy_cube(monkeypatch):
    from skyshift_server import exports
    from skyshift_server.errors import NotFoundError
    monkeypatch.setattr(exports,'find_job',lambda *a:(_ for _ in ()).throw(NotFoundError('no cube')))
    blob,mime,name=exports.download(166.97483,5.22757,3,'hours','gif')
    assert blob.startswith(b'GIF') and mime=='image/gif' and name=='blink.gif'
    blob,_,_=exports.download(166.97483,5.22757,3,'hours','json')
    assert json.loads(blob)['object']['name'] is not None
