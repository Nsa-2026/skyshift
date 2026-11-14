"""Science workspace endpoints on the default (Cloud Run) configuration: hosted jobs off, external
catalogues down. Every refusal or failure is the contract's error JSON, never a 500 and never an
invented result. Offline: every upstream call is replaced."""
import json

import numpy as np
import pytest
import requests
from fastapi.testclient import TestClient

from skyshift_server import app, enrichment, known as known_mod, local_results, observations
from skyshift_server.cache import Cache
from skyshift_server.errors import NotFoundError, UpstreamError
from skyshift_server.science.algo import comover, relay

ROOT = app._repo_root() / 'web/public/science-data'


def _center(slug):
    return json.loads((ROOT / slug / 'frames.json').read_text())['center']


def _no_local_cube(*_a, **_k):
    raise NotFoundError('No precomputed field here.')


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv('SKYSHIFT_ENABLE_SCIENCE_JOBS', raising=False)
    # Never read a developer's local analysis caches or job store.
    monkeypatch.setenv('SKYSHIFT_JOBS', str(tmp_path / 'jobs'))
    monkeypatch.setattr(local_results, 'find_job', _no_local_cube)
    monkeypatch.setattr(enrichment, 'find_job', _no_local_cube)
    app.reset_cache(Cache(str(tmp_path / 'cache')))
    yield TestClient(app.app)
    app.reset_cache(None)


def _error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {'error'} and body['error']['code'] == code and body['error']['message']
    return body['error']['message']


# ---------------------------------------------------------------- hosted jobs stay off

def test_capabilities_report_jobs_off(client):
    response = client.get('/api/science/capabilities')
    assert response.status_code == 200 and response.json() == {'analysis_jobs': False}
    assert response.headers['cache-control'] == app.CACHE_NONE


def test_post_jobs_refuses_cleanly_when_off(client, monkeypatch):
    from skyshift_server import jobs
    monkeypatch.setattr(jobs, 'start', lambda *a, **k: pytest.fail('a job started with jobs off'))
    message = _error(client.post('/api/jobs', params={'ra': 10, 'dec': 20}), 503, 'upstream')
    assert 'not enabled' in message
    # Even a bad size never reaches the queue: the gate answers first.
    _error(client.post('/api/jobs', params={'ra': 10, 'dec': 20, 'size': 7}), 503, 'upstream')


@pytest.mark.parametrize('job_id', ['0123456789abcdef01234567', 'not-a-job', '..%2F..%2Fetc'])
def test_unknown_job_is_404(client, job_id):
    _error(client.get(f'/api/jobs/{job_id}'), 404, 'not_found')


def test_reference_off_a_published_centre_is_404_when_off(client, monkeypatch):
    monkeypatch.setattr(observations, 'reference', lambda *a: pytest.fail('reference fetched with jobs off'))
    message = _error(client.post('/api/reference', params={'ra': 10, 'dec': 20}), 404, 'not_found')
    assert 'featured target' in message


def test_reference_at_a_published_centre_serves_the_product(client):
    center = _center('wise0855')
    response = client.post('/api/reference', params=center)
    assert response.status_code == 200
    assert response.json() == json.loads((ROOT / 'wise0855/science.json').read_text())


def test_reference_keeps_not_found_when_jobs_on(client, monkeypatch):
    """With jobs on, a position without a cube is still 404 (it was wrapped into a 502)."""
    monkeypatch.setenv('SKYSHIFT_ENABLE_SCIENCE_JOBS', '1')
    monkeypatch.setattr(observations, 'reference', _no_local_cube)
    _error(client.post('/api/reference', params={'ra': 10, 'dec': 20}), 404, 'not_found')
    monkeypatch.setattr(observations, 'reference', lambda *a: (_ for _ in ()).throw(RuntimeError('S3 slow')))
    _error(client.post('/api/reference', params={'ra': 10, 'dec': 20}), 502, 'upstream')


def test_science_and_analysis_off_a_published_centre_are_404(client):
    _error(client.get('/api/science', params={'ra': 10, 'dec': 20}), 404, 'not_found')
    _error(client.get('/api/analysis', params={'ra': 10, 'dec': 20, 'det': 2}), 404, 'not_found')
    # A featured field's other detectors have no published analysis: 404, not a computed guess.
    _error(client.get('/api/analysis', params={**_center('barnard'), 'det': 5}), 404, 'not_found')


def test_derived_layers_outside_the_published_grid_are_404(client):
    message = _error(client.get('/api/frames', params={**_center('barnard'), 'fov': 15, 'det': 2,
                                                       'view': 'model'}), 404, 'not_found')
    assert 'featured' in message


# ---------------------------------------------------------------- catalogues down (IRSA 502 tonight)

class _Proxy502:
    status_code = 502
    text = '<html>Proxy Error</html>'

    def raise_for_status(self):
        raise requests.HTTPError('502 Server Error: Proxy Error for url: https://irsa.ipac.caltech.edu/TAP/sync')


def _cached_enrichment_keys(tmp_path):
    return [p for p in (tmp_path / 'cache').rglob('*') if p.is_file() and 'enrichment' in str(p)]


def test_catwise_down_is_502_and_not_cached(client, monkeypatch, tmp_path):
    monkeypatch.setattr(enrichment.requests, 'get', lambda *a, **k: _Proxy502())
    message = _error(client.get('/api/enrichment', params={**_center('wise0855'), 'kind': 'catwise'}),
                     502, 'upstream')
    assert 'IRSA' in message and 'CatWISE' in message and 'No catalogue results' in message
    assert _cached_enrichment_keys(tmp_path) == []


def test_neowise_down_is_502_not_a_spherex_only_curve(client, monkeypatch, tmp_path):
    monkeypatch.setattr(relay.requests, 'get', lambda *a, **k: _Proxy502())
    message = _error(client.get('/api/enrichment', params={**_center('wise0855'), 'kind': 'relay'}),
                     502, 'upstream')
    assert 'NEOWISE' in message and '502' in message
    assert _cached_enrichment_keys(tmp_path) == []


def test_gaia_archive_down_is_502(client, monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError('gea.esac.esa.int unreachable')
    monkeypatch.setattr(comover.requests, 'post', down)
    message = _error(client.get('/api/enrichment', params={**_center('epsindib'), 'kind': 'comovers'}),
                     502, 'upstream')
    assert 'Gaia' in message


def test_catwise_answer_is_cached_and_marks_no_invented_rows(client, monkeypatch, tmp_path):
    class Ok:
        text = 'source_name,ra,dec,w1mpro,w2mpro,pmra,pmdec\nJ1,1.0,2.0,15.1,,0.1,-0.2\n'

        def raise_for_status(self):
            pass
    monkeypatch.setattr(enrichment.requests, 'get', lambda *a, **k: Ok())
    response = client.get('/api/enrichment', params={**_center('wise0855'), 'kind': 'catwise'})
    assert response.status_code == 200
    rows = response.json()['sources']
    assert rows == [dict(name='J1', ra=1.0, dec=2.0, w1mpro=15.1, w2mpro=None, pmra=0.1, pmdec=-0.2)]
    assert len(_cached_enrichment_keys(tmp_path)) == 1


# ---------------------------------------------------------------- known asteroids: MPC orbits only

class _Index:
    mjd_lo, mjd_hi = 60000.0, 62000.0


def test_asteroids_come_from_mpc_orbits_at_session_times(client, monkeypatch):
    calls = []

    def near(ra, dec, r_arcmin, mjds, *, epoch_ids=None, cache=None):
        calls.append((r_arcmin, list(mjds), list(epoch_ids)))
        return [dict(name='(1) Ceres', epoch_id=epoch_ids[0], ra=ra, dec=dec, v=9.0, source='mpc')]
    monkeypatch.setattr(known_mod, 'mpc_index', lambda cache=None: _Index())
    monkeypatch.setattr(known_mod, 'near', near)
    monkeypatch.setattr(enrichment.requests, 'get', lambda *a, **k: pytest.fail('no other small-body service'))
    center = _center('wise0855')
    response = client.get('/api/enrichment', params={**center, 'kind': 'asteroids'})
    assert response.status_code == 200
    body = response.json()
    assert body['source'].startswith('Minor Planet Center') and body['errors'] == []
    assert body['objects'][0]['name'] == '(1) Ceres' and body['objects'][0]['visit'] == 1
    (r_arcmin, mjds, ids), = calls
    frames = json.loads((ROOT / 'wise0855/science.json').read_text())['frames']
    from skyshift_core.visits import sessions
    assert len(mjds) == len(np.unique(sessions([f['mjd'] for f in frames])))  # one time per session
    assert mjds == sorted(mjds) and ids == list(range(len(mjds))) and r_arcmin >= 1


def test_asteroids_without_the_index_is_502(client, monkeypatch, tmp_path):
    def missing(cache=None):
        raise UpstreamError('known-object index is missing from the cache')
    monkeypatch.setattr(known_mod, 'mpc_index', missing)
    _error(client.get('/api/enrichment', params={**_center('wise0855'), 'kind': 'asteroids'}), 502, 'upstream')
    assert _cached_enrichment_keys(tmp_path) == []


def test_asteroids_partly_outside_the_index_is_flagged_and_not_cached(client, monkeypatch, tmp_path):
    frames = json.loads((ROOT / 'wise0855/science.json').read_text())['frames']
    first = min(f['mjd'] for f in frames)

    class Late:
        mjd_lo, mjd_hi = first + 30, 62000.0  # the first visit is before the index
    monkeypatch.setattr(known_mod, 'mpc_index', lambda cache=None: Late())
    monkeypatch.setattr(known_mod, 'near', lambda *a, **k: [])
    response = client.get('/api/enrichment', params={**_center('wise0855'), 'kind': 'asteroids'})
    assert response.status_code == 200
    body = response.json()
    assert body['errors'] and 'incomplete' in body['note'] and body['objects'] == []
    assert _cached_enrichment_keys(tmp_path) == []
