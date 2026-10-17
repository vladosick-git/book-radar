import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException

from backend import db, main, simulation as sim, simulation_batch as batch, simulation_fleet as fleet
from test_simulation import client, http_server


def create_many(client, count):
    r = client.post('/api/simulation/batch', json={'count': count})
    assert r.status_code == 201, r.text
    return r.json()['profile_ids']


def test_plan_500_with_15_addresses_and_no_partial_start(client):
    addresses = [f'pool-{i}@example.invalid' for i in range(15)]
    assert client.post('/api/simulation/mail-pool/import', json={'addresses': addresses}).status_code == 200
    fleet.init()
    with db.connect() as c:
        assert fleet.plan(c, 500) == {'target': 500, 'existing_bots': 0, 'free_addresses': 15, 'available': 15, 'need_profiles': 500, 'shortage': 485}
    with pytest.raises(HTTPException) as e:
        fleet.enqueue(fleet.StartInput(target=500))
    assert e.value.status_code == 409 and '485' in e.value.detail
    assert not db.rows('SELECT * FROM simulation_profiles')
    assert not db.rows('SELECT * FROM simulation_fleet_runs')
    create_many(client, 485)
    with db.connect() as c:
        assert fleet.plan(c, 500)['shortage'] == 0
    rid = fleet.enqueue(fleet.StartInput(target=500))
    assert len(db.rows('SELECT * FROM simulation_fleet_members WHERE run_id=?', (rid,))) == 500
    assert len(db.rows('SELECT * FROM simulation_profiles')) == 500
    assert len({p['email'] for p in db.rows('SELECT email FROM simulation_profiles')}) == 500
    assert len({(p['city'], p['device']) for p in db.rows('SELECT city,device FROM simulation_profiles')}) == 300
    assert client.get('/api/simulation/state').json()['mail_pool']['available'] == 0
    assert not db.rows('SELECT * FROM accounts') and not db.rows('SELECT * FROM mailboxes')


def test_enqueued_profiles_cannot_be_stolen_and_duplicate_fleet_rejected(client):
    ids = create_many(client, 3)
    rid = fleet.enqueue(fleet.StartInput(target=2))
    with pytest.raises(HTTPException):
        sim.claim_profile(ids[0], 'registering')
    assert sim.claim_profile(ids[2], 'verifying')['id'] == ids[2]
    assert sim.claim_profile(ids[0], 'registering', fleet_run_id=rid)['id'] == ids[0]
    with pytest.raises(HTTPException):
        fleet.enqueue(fleet.StartInput(target=1))
    assert sum(p['active'] for p in client.get('/api/simulation/state').json()['profiles']) == 3


def test_queue_respects_concurrency_and_reuses_existing_bots(client, monkeypatch):
    ids = create_many(client, 7)
    running, peak, seen = 0, 0, []
    async def register(pid, origin, fleet_run_id=None):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        seen.append(pid)
        await asyncio.sleep(.02)
        running -= 1
        return {'status': 'registered'}
    monkeypatch.setattr(sim, 'register_one', register)
    payload = fleet.StartInput(target=7, concurrency=2, read_books=False)
    rid = fleet.enqueue(payload)
    asyncio.run(fleet.execute(rid, 'http://127.0.0.1:8797', payload))
    result = fleet.state()['run']
    assert peak == 2 and sorted(seen) == ids
    assert result['status'] == 'done' and result['counts']['done'] == 7
    assert len(db.rows('SELECT * FROM simulation_profiles')) == 7


def test_background_api_registration_reading_and_reload_survive(client):
    create_many(client, 3)
    with http_server(main.app) as origin:
        async def go():
            async with httpx.AsyncClient(base_url=origin) as c:
                r = await c.post('/api/simulation/fleet/start', json={'target': 3, 'concurrency': 2,
                    'pages': 2, 'min_seconds_per_page': .2, 'max_seconds_per_page': .4})
                assert r.status_code == 202, r.text
                deadline = time.monotonic() + 40
                saw_background = False
                while time.monotonic() < deadline:
                    state = (await c.get('/api/simulation/fleet')).json()['run']
                    saw_background |= state['counts']['running'] > 0
                    # New panel connections only read state; they must not restart the batch.
                    assert len(db.rows('SELECT * FROM simulation_fleet_runs')) == 1
                    if state['status'] != 'running':
                        break
                    await asyncio.sleep(.1)
                assert saw_background and state['status'] == 'done', state
                assert state['counts']['done'] == 3 and state['counts']['failed'] == 0
        asyncio.run(go())
    assert len(db.rows('SELECT * FROM simulation_accounts')) == 3
    assert len(db.rows('SELECT * FROM simulation_read_pages WHERE completed_at IS NOT NULL')) == 6


def test_stop_cancels_active_and_waiting_work_and_releases_browser(client, monkeypatch):
    create_many(client, 5)
    async def slow(pid, origin, fleet_run_id=None):
        sim.claim_profile(pid, 'registering', fleet_run_id=fleet_run_id)
        try:
            await asyncio.sleep(1000)
        finally:
            db.execute("UPDATE simulation_profiles SET status='planned',lease_until=0 WHERE id=?", (pid,))
    monkeypatch.setattr(sim, 'register_one', slow)
    payload = fleet.StartInput(target=5, concurrency=2, read_books=False)
    async def go():
        rid = fleet.enqueue(payload)
        fleet.tasks[rid] = asyncio.create_task(fleet.execute(rid, 'http://127.0.0.1:8797', payload))
        await asyncio.sleep(.03)
        assert fleet.state()['run']['counts']['running'] == 2
        assert (await fleet.stop(rid))['stopped']
        assert (await fleet.stop(rid))['stopped']  # Idempotent.
        assert fleet.state()['run']['counts']['stopped'] == 5
        assert not fleet.tasks
    asyncio.run(go())
    assert not any(p['active'] for p in client.get('/api/simulation/state').json()['profiles'])


def test_restart_marks_inflight_work_interrupted_without_creating_accounts(client):
    ids = create_many(client, 2)
    rid = fleet.enqueue(fleet.StartInput(target=2))
    db.execute("UPDATE simulation_fleet_members SET status='running' WHERE run_id=? AND profile_id=?", (rid, ids[0]))
    db.execute("UPDATE simulation_profiles SET status='reading',lease_until=? WHERE id=?", (time.time() + 200, ids[0]))
    fleet.recover()
    assert fleet.state()['run']['status'] == 'interrupted'
    assert fleet.state()['run']['counts']['stopped'] == 2
    assert not any(p['active'] for p in client.get('/api/simulation/state').json()['profiles'])
    assert not db.rows('SELECT * FROM simulation_accounts')


def test_fleet_input_bounds(client):
    for payload in ({'target': 501}, {'target': 0}, {'concurrency': 4}, {'book': 'real-at'}):
        assert client.post('/api/simulation/fleet/start', json=payload).status_code == 422
