import numpy as np
from astropy.wcs import WCS

from skyshift_server import blink, local_results


def test_real_grid_orientation_no_data_and_quantization(monkeypatch):
    w=WCS(naxis=2);w.wcs.crval=[10,20];w.wcs.crpix=[20,20]
    w.wcs.cdelt=[-6.15/3600,6.15/3600];w.wcs.ctype=['RA---TAN','DEC--TAN']
    # Intensity increases northward; a separate spot is east of centre.
    yy,xx=np.mgrid[:39,:39]; image=yy.astype(float);image[19,14]=100
    job=dict(ra=10,dec=20,wcs=w,_archive_checked='2026-11-14',
             res=dict(img=np.stack([image,image+2]),mjd=np.array([60000,60100]),
                      meta=[dict(det=2,wavelength=1.3,key='dr1/x')]*2))
    monkeypatch.setattr(local_results,'find_job',lambda *a:job)
    monkeypatch.setattr(blink,'_gaia',lambda j:[])
    result=blink.build_frames(10,20,5,'months',2)
    assert result.meta['size']==[146,146]
    assert len(result.meta['epochs'])==2
    ep=result.meta['epochs'][0]
    raw=np.frombuffer(result.blobs[ep['data_url']],'<u2').reshape(146,146)
    values=raw*ep['scale']+ep['zero']
    assert raw[0,0]==0  # 4' input does not invent the missing 5' perimeter
    assert values[50,73]>values[95,73]  # north up
    assert values[73,58]>values[73,88]  # east left
    assert ep['flag_frac'] is None and ep['sat_frac'] is None
    assert ep['um_min'] is None  # central wavelength is not a full-frame range


def test_unimplemented_engine_does_not_show_synthetic_data_by_default(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from skyshift_server import app
    from skyshift_server.cache import Cache
    monkeypatch.delenv('SKYSHIFT_ALLOW_SYNTHETIC',raising=False)
    app.reset_cache(Cache(str(tmp_path)))
    monkeypatch.setattr(app,'_call',lambda *a,**kw:app._NOT_READY)
    response=TestClient(app.app).get('/api/frames?ra=1&dec=2')
    assert response.status_code==404
    assert response.json()['error']['code']=='not_found'
    app.reset_cache(None)
