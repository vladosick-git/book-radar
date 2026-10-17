import asyncio
import json
import socket
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from backend import config, db, main, simulation as sim

PROFILES = [{'name': 'Тверь Android', 'city': 'tver', 'device': 'android'},
            {'name': 'Москва Windows', 'city': 'moscow', 'device': 'windows'},
            {'name': 'Петербург iPhone', 'city': 'spb', 'device': 'iphone'}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'simulation')
    monkeypatch.setattr(config, 'SIMULATION', True)
    monkeypatch.setattr(config, 'SERVER', False)
    monkeypatch.setattr(config, 'AUTH', False)
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    with TestClient(main.app) as c:
        yield c


@contextmanager
def http_server(app):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Local fixture server did not start')
            time.sleep(.02)
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def create(client):
    response = client.post('/api/simulation/profiles', json={'profiles': PROFILES})
    assert response.status_code == 201, response.text
    return response.json()['profile_ids']


def test_partial_registration_keeps_earlier_results_and_continues(client, monkeypatch):
    from fastapi import HTTPException
    ids = create(client)

    async def register(pid, origin):
        if pid == ids[1]:
            raise HTTPException(409, 'Fixture busy')
        return {'profile_id': pid, 'status': 'registered'}
    monkeypatch.setattr(sim, 'register_one', register)
    response = client.post('http://127.0.0.1:8797/api/simulation/register', json={'profile_ids': ids})
    assert response.status_code == 200
    assert [r['status'] for r in response.json()['results']] == ['registered', 'blocked', 'registered']


def test_profiles_are_atomic_repeatable_and_separate_from_real_accounts(client):
    ids = create(client)
    assert create(client) == ids
    state = client.get('/api/simulation/state').json()
    assert state['registered'] == 0 and len(state['profiles']) == 3
    assert all(p['email'].endswith('@example.invalid') and p['status'] == 'planned' for p in state['profiles'])
    assert not db.rows('SELECT * FROM profiles') and not db.rows('SELECT * FROM accounts') and not db.rows('SELECT * FROM mailboxes')
    conflict = [{'name': 'Should rollback', 'city': 'tver', 'device': 'android'}, {**PROFILES[0], 'city': 'spb'}]
    assert client.post('/api/simulation/profiles', json={'profiles': conflict}).status_code == 409
    assert len(db.rows('SELECT * FROM simulation_profiles')) == 3


def test_mode_and_auth_gates(client, monkeypatch):
    assert client.post('/api/onboarding/at/pilot').status_code == 409
    assert client.post('/api/profiles', json={'name': 'Must not create'}).status_code == 409
    assert not db.rows('SELECT * FROM profiles')
    monkeypatch.setattr(config, 'SIMULATION', False)
    assert client.get('/api/simulation/state').status_code == 409
    assert client.get('/api/simulation/site/register').status_code == 409
    monkeypatch.setattr(config, 'SIMULATION', True)
    monkeypatch.setattr(config, 'SERVER', True)
    assert client.post('/api/simulation/profiles', json={'profiles': PROFILES}).status_code == 409
    monkeypatch.setattr(config, 'SERVER', False)
    monkeypatch.setattr(config, 'AUTH', True)
    assert client.get('/api/simulation/state').status_code == 401


@pytest.mark.parametrize('url', ['https://author.today', 'http://example.com', 'http://127.0.0.1.evil.test',
                               'http://user:pass@localhost:8797', 'http://127.0.0.1:8797/path',
                               'http://127.0.0.1:8797?redirect=1', 'http://127.0.0.1:bad'])
def test_production_or_ambiguous_origins_rejected(url):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        sim.local_origin(url)


def test_request_guard_checks_port_host_and_path():
    origin = sim.local_origin('http://127.0.0.1:8797/')
    assert sim.allowed_request(origin + '/api/simulation/site/meta', origin)
    assert not sim.allowed_request('http://127.0.0.1:8798/api/simulation/site/meta', origin)
    assert not sim.allowed_request('http://localhost:8797/api/simulation/site/meta', origin)
    assert not sim.allowed_request(origin + '/api/onboarding/at/1/form', origin)
    assert not sim.allowed_request('https://author.today/account/register', origin)


def test_actual_browser_registration_three_devices_and_repeat(client):
    ids = create(client)
    with http_server(main.app) as origin:
        async def run():
            results = [await sim.register_one(pid, origin) for pid in ids]
            repeat = await sim.register_one(ids[0], origin)
            return results, repeat
        results, repeat = asyncio.run(run())
    assert [r['status'] for r in results] == ['registered'] * 3, results
    assert repeat['status'] == 'registered'
    assert repeat['used_saved_session'] is True
    assert all(r['used_saved_session'] is False for r in results)
    by_device = {p['device']: p for p in client.get('/api/simulation/state').json()['profiles']}
    assert by_device['android']['observed']['width'] == 412 and by_device['android']['observed']['touch'] > 0
    assert 'Android' in by_device['android']['observed']['user_agent']
    assert by_device['windows']['observed']['width'] == 1280 and by_device['windows']['observed']['touch'] == 0
    assert 'Windows NT 10.0' in by_device['windows']['observed']['user_agent']
    assert by_device['iphone']['observed']['width'] == 390 and 'iPhone' in by_device['iphone']['observed']['user_agent']
    for p in by_device.values():
        city = sim.CITIES[p['city']]
        assert p['observed']['latitude'] == city['latitude'] and p['observed']['longitude'] == city['longitude']
        assert p['observed']['emulated'] is True
    assert len(db.rows('SELECT * FROM simulation_accounts')) == 3
    assert len(db.rows("SELECT * FROM simulation_events WHERE action='site_register'")) == 3
    assert len(db.rows("SELECT * FROM simulation_events WHERE action='site_login'")) == 0
    assert len(db.rows("SELECT * FROM simulation_events WHERE action='session_verified'")) == 1
    assert len(db.rows('SELECT * FROM simulation_sessions')) == 3
    assert not db.rows('SELECT * FROM accounts')


def test_redirect_cannot_reach_a_second_server(client):
    pid = create(client)[0]
    hits = []
    other = FastAPI()

    @other.get('/unexpected')
    def unexpected():
        hits.append(True)
        return {'bad': True}

    with http_server(other) as outside:
        redirector = FastAPI()

        @redirector.get('/api/simulation/site/meta')
        def redirect():
            return RedirectResponse(outside + '/unexpected')

        with http_server(redirector) as origin:
            result = asyncio.run(sim.register_one(pid, origin))
    assert result['status'] == 'failed' and not hits
    assert not db.rows('SELECT * FROM simulation_accounts')


def test_invalid_batch_and_private_validation(client):
    create(client)
    assert client.post('/api/simulation/register', json={'profile_ids': [1, 999]}).status_code == 400  # TestClient has a non-loopback base URL.
    assert client.post('/api/simulation/register', json={'profile_ids': [1, 1]}).status_code == 422
    assert client.post('/api/simulation/profiles', json={'profiles': [{**PROFILES[0], 'device': 'unknown'}]}).status_code == 422
    r = client.post('/api/simulation/site/register', json={'password': 'FIXTURE-PRIVATE-INPUT'})
    assert r.status_code == 422 and 'FIXTURE-PRIVATE-INPUT' not in r.text
    assert r.headers['cache-control'] == 'no-store'
    assert client.get('/api/simulation/site/me').status_code == 401
    assert not db.rows('SELECT * FROM simulation_accounts')
