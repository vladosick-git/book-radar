import asyncio
import json

from backend import db, main, simulation as sim
from test_simulation import client, http_server


def pool(client, addresses):
    r = client.post('/api/simulation/mail-pool/import', json={'addresses': addresses})
    assert r.status_code == 200, r.text
    return r.json()


def batch(client, count=3, new_emails=True):
    r = client.post('/api/simulation/batch', json={'count': count, 'new_emails': new_emails})
    assert r.status_code == 201, r.text
    return r.json()['profile_ids']


def test_batch_new_addresses_are_unique_and_environments_stay_assigned(client):
    ids = batch(client, count=10)
    before = client.get('/api/simulation/state').json()['profiles']
    assert len(before) == 10 and {p['id'] for p in before} == set(ids)
    assert len({p['email'] for p in before}) == 10
    assert len({p['city'] for p in before}) == 10 and len({p['device'] for p in before}) == 10
    assert all(p['email'].endswith('@example.invalid') and not p['active'] for p in before)
    assert set(batch(client, count=2)).isdisjoint(ids)
    assert client.get('/api/simulation/state').json()['profiles'][:10] == before
    assert not db.rows('SELECT * FROM profiles') and not db.rows('SELECT * FROM mailboxes')


def test_pool_assignment_is_exclusive_and_shortage_rolls_back_entire_batch(client):
    assert pool(client, ['Reader1@example.invalid', 'reader2@example.invalid'])['added'] == 2
    assert pool(client, ['READER1@example.invalid']) == {'added': 0, 'duplicates': 1}
    fail = client.post('/api/simulation/batch', json={'count': 3, 'new_emails': False})
    assert fail.status_code == 409
    assert not db.rows('SELECT * FROM simulation_profiles')
    ids = batch(client, count=2, new_emails=False)
    profiles = client.get('/api/simulation/state').json()['profiles']
    assert [p['email'] for p in profiles] == ['reader1@example.invalid', 'reader2@example.invalid']
    assert {m['profile_id'] for m in db.rows('SELECT * FROM simulation_mail_pool')} == set(ids)
    assert client.get('/api/simulation/state').json()['mail_pool'] == {'total': 2, 'used': 2, 'available': 0}
    pool(client, ['reader1@example.invalid'])
    assert client.post('/api/simulation/batch', json={'count': 1, 'new_emails': False}).status_code == 409
    assert len(db.rows('SELECT * FROM simulation_profiles')) == 2


def test_new_fixture_addresses_do_not_steal_reserved_pool_addresses(client):
    pool(client, ['sim-1@example.invalid', 'sim-2@example.invalid'])
    batch(client, count=1)
    manual = client.post('/api/simulation/profiles', json={'profiles': [{'name': 'Manual', 'city': 'tver', 'device': 'android'}]})
    assert manual.status_code == 201
    assert not {p['email'] for p in db.rows('SELECT email FROM simulation_profiles')} & {'sim-1@example.invalid', 'sim-2@example.invalid'}
    batch(client, count=2, new_emails=False)
    assert len({p['email'] for p in db.rows('SELECT email FROM simulation_profiles')}) == 4


def test_pool_and_batch_registration_use_only_local_fixture_accounts(client):
    pool(client, ['first@example.invalid', 'second@example.invalid'])
    ids = batch(client, count=2, new_emails=False)
    with http_server(main.app) as origin:
        async def go():
            result = [await sim.register_one(pid, origin) for pid in ids]
            verified = [await sim.verify_one(pid, origin) for pid in ids]
            return result, verified
        result, verified = asyncio.run(go())
    assert all(r['status'] == 'registered' for r in result + verified)
    assert {a['email'] for a in db.rows('SELECT email FROM simulation_accounts')} == {'first@example.invalid', 'second@example.invalid'}
    assert len(db.rows('SELECT * FROM simulation_sessions')) == 2
    assert not db.rows('SELECT * FROM accounts') and not db.rows('SELECT * FROM mailboxes')


def test_import_rejects_credentials_and_validation_does_not_echo_input(client):
    assert client.get('/api/simulation/state').status_code == 200
    for addresses in (['good@example.invalid', 'bad@example.invalid:secret-password'], ['two@@example.invalid'], ['a..b@example.invalid']):
        r = client.post('/api/simulation/mail-pool/import', json={'addresses': addresses})
        assert r.status_code == 422
        assert 'secret-password' not in r.text and 'input' not in r.text
        assert not db.rows('SELECT * FROM simulation_mail_pool')
    for value in (0, 501):
        assert client.post('/api/simulation/batch', json={'count': value}).status_code == 422
    assert 'password' not in json.dumps(client.get('/api/simulation/state').json())
