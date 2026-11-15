"""Release gates: the main science path remains canonical and public data is portable."""
import json

import pytest
from fastapi.testclient import TestClient

from skyshift_server import app, blink, local_results, published_science


def test_raw_observations_never_use_a_legacy_analysis_cache(monkeypatch):
    monkeypatch.setattr(local_results, 'find_job', lambda *args: pytest.fail('raw path read legacy cache'))
    def canonical(*args, **kwargs):
        raise RuntimeError('canonical search reached')
    monkeypatch.setattr(blink.search, 'search', canonical)
    with pytest.raises(RuntimeError, match='canonical search reached'):
        blink.build_frames(10, 20, 5, 'months', 2)


def test_published_science_serves_five_fields_without_local_cache(monkeypatch):
    monkeypatch.setattr(local_results, 'find_job', lambda *args: pytest.fail('published product read local cache'))
    client = TestClient(app.app)
    root = app._repo_root() / 'web/public/science-data'
    paths = sorted(root.glob('*/science.json'))
    assert len(paths) == 5
    for path in paths:
        center = json.loads((path.parent / 'frames.json').read_text())['center']
        expected = json.loads(path.read_text())
        response = client.get('/api/science', params=center)
        assert response.status_code == 200
        assert response.json() == expected
        analysis = json.loads((path.parent / 'analysis.json').read_text())
        response = client.get('/api/analysis', params={**center, 'det': analysis['detector']})
        assert response.status_code == 200 and response.json() == analysis
        assert all(candidate['p_false'] is None for candidate in analysis['cold_candidates'])


def test_published_layers_keep_their_grid_and_do_not_invent_coverage():
    root = app._repo_root() / 'web/public/science-data'
    for path in sorted(root.glob('*/model.json')):
        meta = json.loads(path.read_text())
        center = meta['center']
        result = published_science.frames(**center, fov=5, clock='months', det=2, view='model')
        assert result.meta['center'] == center
        assert all(e['det'] == 2 for e in result.meta['epochs'])
        assert all(len(blob) == 2 * meta['size'][0] * meta['size'][1] for blob in result.blobs.values())
        assert published_science.frames(**center, fov=15, clock='months', det=2, view='model') is None
        assert published_science.frames(**center, fov=5, clock='hours', det=2, view='model') is None


def test_published_exports_work_without_native_jobs(monkeypatch):
    from skyshift_server import exports
    from skyshift_server.errors import NotFoundError
    def unavailable(*args):
        raise NotFoundError('No local cube')
    monkeypatch.setattr(exports,'find_job',unavailable)
    root=app._repo_root()/'web/public/science-data/wise0855'
    center=json.loads((root/'frames.json').read_text())['center']
    expected=json.loads((root/'science.json').read_text())
    blob,mime,name=exports.download(**center,det=6,clock='months',format='json')
    assert json.loads(blob)==expected and name=='science.json'
    blob,_,name=exports.download(**center,det=6,clock='months',format='gif')
    assert blob.startswith(b'GIF') and name=='science-blink.gif'


def test_unconfigured_cloud_cannot_accept_ephemeral_background_jobs(monkeypatch):
    monkeypatch.delenv('SKYSHIFT_ENABLE_SCIENCE_JOBS', raising=False)
    client = TestClient(app.app)
    assert client.get('/api/science/capabilities').json()['analysis_jobs'] is False
    response = client.post('/api/jobs?ra=10&dec=20')
    assert response.status_code == 503
    assert 'not enabled' in response.json()['error']['message']
