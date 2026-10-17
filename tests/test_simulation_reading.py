import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from backend import db, main, simulation as sim, simulation_reading as reading
from test_simulation import client, create, http_server


def signup(client, pid):
    r = client.post('/api/simulation/site/register', json={'profile_id': pid, 'password': sim.password_for(pid),
        'width': 1280, 'height': 720, 'touch': 0, 'latitude': 55.7558, 'longitude': 37.6173})
    assert r.status_code == 201


def run_row(pid, pages=2, seconds=.2):
    reading.init()
    return db.execute('''INSERT INTO simulation_read_runs(profile_id,book,start_page,pages_requested,seconds_per_page,created_at)
        VALUES (?,'cloud-library',1,?,?,?)''', (pid, pages, seconds, db.now()))


def proof(page=1):
    return {'page': page, 'elapsed_ms': 1000, 'scroll_events': 4, 'scroll_fraction': 1, 'sample_index': 3}


def test_cold_session_verification_does_not_register_and_expiry_is_detected(client):
    pid = create(client)[0]
    with http_server(main.app) as origin:
        assert asyncio.run(sim.register_one(pid, origin))['status'] == 'registered'
        before = db.rows('SELECT * FROM simulation_accounts')
        tokens = db.rows('SELECT * FROM simulation_sessions')
        assert asyncio.run(sim.verify_one(pid, origin))['used_saved_session']
        assert db.rows('SELECT * FROM simulation_accounts') == before
        assert db.rows('SELECT * FROM simulation_sessions') == tokens
        db.execute('UPDATE simulation_sessions SET expires_at=0')
        assert asyncio.run(sim.verify_one(pid, origin))['status'] == 'failed'
        assert db.rows('SELECT * FROM simulation_accounts') == before
        assert len(db.rows("SELECT * FROM simulation_events WHERE action='site_register'")) == 1


def test_busy_profile_is_not_taken_by_register_verify_or_read(client):
    pid = create(client)[0]
    db.execute("UPDATE simulation_profiles SET status='reading',lease_until=? WHERE id=?", (time.time() + 60, pid))
    with pytest.raises(HTTPException) as e:
        asyncio.run(sim.register_one(pid, 'http://127.0.0.1:8797'))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException):
        asyncio.run(sim.verify_one(pid, 'http://127.0.0.1:8797'))
    with pytest.raises(HTTPException):
        asyncio.run(reading.read_one(pid, 'http://127.0.0.1:8797', reading.ReadInput(profile_ids=[pid])))
    assert db.rows('SELECT status FROM simulation_profiles WHERE id=?', (pid,))[0]['status'] == 'reading'


def test_browser_reads_phones_desktop_and_tablet_with_scroll_time_samples_and_screenshots(client):
    ids = create(client)
    more = client.post('/api/simulation/profiles', json={'profiles': [
        {'name': 'Калининград iPad', 'city': 'kaliningrad', 'device': 'ipadpro11'},
        {'name': 'Владивосток Samsung', 'city': 'vladivostok', 'device': 'galaxys24'}]}).json()['profile_ids']
    ids += more
    with http_server(main.app) as origin:
        async def go():
            for pid in ids:
                assert (await sim.register_one(pid, origin))['status'] == 'registered'
            return [await reading.read_one(pid, origin, reading.ReadInput(profile_ids=[pid], pages=2, min_seconds_per_page=.2, max_seconds_per_page=.4)) for pid in ids]
        results = asyncio.run(go())
    assert [r['status'] for r in results] == ['done'] * 5, results
    state = client.get('/api/simulation/reading').json()
    assert len(state['runs']) == 5
    for run in state['runs']:
        assert run['completed'] == 2 and run['status'] == 'done'
        assert [p['page'] for p in run['pages']] == [1, 2]
        for page in run['pages']:
            assert page['elapsed_ms'] >= 200 and page['scroll_events'] > 0 and page['scroll_fraction'] >= .95
            assert .2 <= page['target_seconds'] <= .4
            assert page['elapsed_ms'] >= page['target_seconds'] * 1000
            assert page['sample_text'] in reading.lines_for(run['book'], page['page'])
            assert (db.DATA / 'evidence' / page['screenshot']).is_file()
            r = client.get(f"/api/simulation/reading/{run['id']}/screenshot/{page['page']}")
            assert r.status_code == 200 and r.headers['content-type'] == 'image/png'
    assert {b['last_page'] for b in state['bookmarks']} == {2}
    assert not db.rows('SELECT * FROM accounts') and not db.rows('SELECT * FROM findings')


def test_reading_resumes_and_stops_at_book_end(client):
    pid = create(client)[1]
    with http_server(main.app) as origin:
        async def go():
            assert (await sim.register_one(pid, origin))['status'] == 'registered'
            for pages in (2, 6):
                result = await reading.read_one(pid, origin, reading.ReadInput(profile_ids=[pid], pages=pages, min_seconds_per_page=.2, max_seconds_per_page=.2))
                assert result['status'] == 'done', result
            finished = await reading.read_one(pid, origin, reading.ReadInput(profile_ids=[pid], pages=1, min_seconds_per_page=.2, max_seconds_per_page=.2))
            assert finished['status'] == 'already_finished'
        asyncio.run(go())
    runs = db.rows('SELECT start_page,completed FROM simulation_read_runs ORDER BY id')
    assert runs == [{'start_page': 1, 'completed': 2}, {'start_page': 3, 'completed': 4}]
    assert db.rows('SELECT last_page FROM simulation_bookmarks')[0]['last_page'] == 6


def test_progress_requires_session_page_order_scroll_and_time_and_is_idempotent(client):
    ids = create(client)
    rid = run_row(ids[0])
    assert client.get(f'/api/simulation/site/read/{rid}/1').status_code == 401
    signup(client, ids[0])
    assert client.get(f'/api/simulation/site/read/{rid}/2').status_code == 409
    assert client.get(f'/api/simulation/site/read/{rid}/1').status_code == 200
    endpoint = f'/api/simulation/site/read/{rid}/progress'
    assert client.post(endpoint, json=proof()).status_code == 409  # Too soon on the server clock.
    db.execute('UPDATE simulation_read_pages SET opened_at=?', (time.time() - 5,))
    assert client.post(endpoint, json={**proof(), 'scroll_events': 0}).status_code == 409
    assert client.post(endpoint, json={**proof(), 'scroll_fraction': .1}).status_code == 409
    assert client.post(endpoint, json={**proof(), 'elapsed_ms': 10}).status_code == 409
    assert client.post(endpoint, json=proof()).json() == {'saved': True, 'next_page': 2}
    before = db.rows('SELECT * FROM simulation_read_pages')
    assert client.post(endpoint, json=proof()).status_code == 200
    assert db.rows('SELECT * FROM simulation_read_pages') == before
    assert db.rows('SELECT completed FROM simulation_read_runs')[0]['completed'] == 1
    assert len(db.rows("SELECT * FROM simulation_events WHERE action='page_read'")) == 1
    signup(client, ids[1])
    assert client.get(f'/api/simulation/site/read/{rid}/2').status_code == 404
    assert client.post(endpoint, json=proof(page=2)).status_code == 404


def test_failed_read_does_not_add_pages_and_unregistered_profile_is_rejected(client):
    pid = create(client)[0]
    with pytest.raises(HTTPException):
        asyncio.run(reading.read_one(pid, 'http://127.0.0.1:8797', reading.ReadInput(profile_ids=[pid])))
    assert not db.rows('SELECT * FROM simulation_read_runs')
    with http_server(main.app) as origin:
        assert asyncio.run(sim.register_one(pid, origin))['status'] == 'registered'
        db.execute('UPDATE simulation_sessions SET expires_at=0')
        result = asyncio.run(reading.read_one(pid, origin, reading.ReadInput(profile_ids=[pid], pages=2, min_seconds_per_page=.2, max_seconds_per_page=.2)))
        assert result['status'] == 'failed'
    assert not db.rows('SELECT * FROM simulation_read_pages')
    assert not db.rows('SELECT * FROM simulation_bookmarks')


def test_read_input_limits_and_private_state(client):
    create(client)
    for body in ({'book': 'real-author-today'}, {'pages': 100}, {'min_seconds_per_page': 700},
                 {'min_seconds_per_page': 120, 'max_seconds_per_page': 60}, {'profile_ids': [1, 1]}):
        r = client.post('/api/simulation/read', json={'profile_ids': [1], **body})
        assert r.status_code == 422
    assert client.get('/api/simulation/reading/999/screenshot/1').status_code == 404
    assert 'password_hash' not in json.dumps(client.get('/api/simulation/reading').json())


def test_random_minute_range_is_chosen_per_page_and_preserved_on_reload(client, monkeypatch):
    pid = create(client)[0]
    payload = reading.ReadInput(profile_ids=[pid])
    assert (payload.min_seconds_per_page, payload.max_seconds_per_page) == (60, 120)
    rid = run_row(pid, seconds=60)
    db.execute('UPDATE simulation_read_runs SET max_seconds_per_page=120 WHERE id=?', (rid,))
    signup(client, pid)
    values = iter([0, 60000])
    monkeypatch.setattr(reading.secrets, 'randbelow', lambda n: next(values))
    url = f'/api/simulation/site/read/{rid}/1'
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200  # Reload must not draw a shorter delay.
    first = db.rows('SELECT target_seconds FROM simulation_read_pages')[0]['target_seconds']
    assert first == 60
    db.execute('UPDATE simulation_read_pages SET opened_at=?', (time.time() - 61,))
    endpoint = f'/api/simulation/site/read/{rid}/progress'
    assert client.post(endpoint, json={**proof(), 'elapsed_ms': 59000}).status_code == 409
    assert client.post(endpoint, json={**proof(), 'elapsed_ms': 61000}).status_code == 200
    assert client.get(f'/api/simulation/site/read/{rid}/2').status_code == 200
    assert [p['target_seconds'] for p in db.rows('SELECT target_seconds FROM simulation_read_pages ORDER BY page')] == [60, 120]
