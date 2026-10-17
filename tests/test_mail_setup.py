"""Local owner flow, no requests to mail providers or Author.Today."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend import config, db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    key = tmp_path / 'vault.key'
    key.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(key))
    monkeypatch.setattr(db, 'DATA', tmp_path / 'runtime')
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    monkeypatch.setattr(config, 'AUTH', False)
    monkeypatch.setattr(config, 'SERVER', False)
    with TestClient(main.app) as client:
        yield client


def profile(client, name='Владелец'):
    return client.post('/api/profiles', json={'name': name}).json()['id']


def attached(client, pid, email='owner@example.test'):
    response = client.post('/api/mailboxes/attach', json={'profile_id': pid, 'provider': 'Yandex', 'email': email})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_prepare_is_local_repeatable_and_does_not_verify_mail(client):
    pid = profile(client)
    first = client.post('/api/mailboxes/setup', json={'profile_id': pid})
    assert first.status_code == 201 and first.json()['created']
    assert first.json()['registration_verified'] is False
    second = client.post('/api/mailboxes/setup', json={'profile_id': pid})
    assert second.json()['account_id'] == first.json()['account_id'] and not second.json()['created']
    assert db.rows('SELECT status,identifier,confirmed_at FROM accounts') == [{'status': 'planned', 'identifier': '', 'confirmed_at': None}]
    assert not db.rows('SELECT * FROM mailboxes') and not main.open_profiles


def test_missing_profile_and_service_conflict_are_atomic(client):
    assert client.post('/api/mailboxes/setup', json={'profile_id': 999}).status_code == 404
    pid = profile(client)
    db.execute('INSERT INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)',
        ('Яндекс · ручная регистрация', 'яндекс · ручная регистрация', 'site', 'https://example.test', db.now()))
    assert client.post('/api/mailboxes/setup', json={'profile_id': pid}).status_code == 409
    assert not db.rows('SELECT * FROM accounts')


def test_mail_and_at_stay_distinct_and_password_not_required(client):
    pid = profile(client)
    mid = attached(client, pid)
    response = client.post(f'/api/mailboxes/{mid}/at-preparation', json={})
    assert response.status_code == 201 and response.json()['registration_verified'] is False
    aid = response.json()['account_id']
    assert client.post(f'/api/mailboxes/{mid}/at-preparation', json={}).json()['account_id'] == aid
    account = db.rows('SELECT * FROM accounts WHERE id=?', (aid,))[0]
    assert account['identifier'] == 'owner@example.test' and account['status'] == 'planned' and account['confirmed_at'] is None
    assert not db.rows('SELECT * FROM onboarding_at')
    row = client.get('/api/state').json()['mailboxes'][0]
    assert row['provider'] == 'Yandex' and row['status'] == 'attached' and row['password_state'] == 'not_stored'
    assert not main.open_profiles


def test_existing_owner_pilot_is_reused_and_session_preserved(client):
    aid = client.post('/api/onboarding/at/pilot').json()['account_id']
    pid = db.rows('SELECT profile_id FROM accounts WHERE id=?', (aid,))[0]['profile_id']
    mid = attached(client, pid)
    db.execute("UPDATE accounts SET identifier='owner@example.test',status='confirmed',confirmed_at=? WHERE id=?", (db.now(), aid))
    from backend import onboarding
    onboarding.record(aid, 'check', 'session_verified', 'fixture', 'owner@example.test', checked=True)
    before = db.rows('SELECT * FROM accounts WHERE id=?', (aid,))
    state = db.rows('SELECT * FROM onboarding_at')
    assert client.post(f'/api/mailboxes/{mid}/at-preparation', json={}).json()['account_id'] == aid
    assert db.rows('SELECT * FROM accounts WHERE id=?', (aid,)) == before
    assert db.rows('SELECT * FROM onboarding_at') == state


def test_different_account_conflict_preserves_all_records(client):
    pid = profile(client)
    mid = attached(client, pid)
    aid = client.post(f'/api/mailboxes/{mid}/at-preparation', json={}).json()['account_id']
    db.execute("UPDATE accounts SET identifier='another@example.test',status='confirmed' WHERE id=?", (aid,))
    before = db.rows('SELECT * FROM accounts')
    assert client.post(f'/api/mailboxes/{mid}/at-preparation', json={}).status_code == 409
    assert db.rows('SELECT * FROM accounts') == before


def test_uncreated_mailbox_does_not_prepare_at(client):
    pid = profile(client)
    client.post('/api/mailboxes/prepare', json={'profile_ids': [pid], 'domain': 'example.test', 'zoid': '123'})
    mid = db.rows('SELECT id FROM mailboxes')[0]['id']
    assert client.post(f'/api/mailboxes/{mid}/at-preparation', json={}).status_code == 409
    assert client.post('/api/mailboxes/999/at-preparation', json={}).status_code == 404
    assert not db.rows("SELECT * FROM services WHERE kind='site'")


def test_auth_and_validation_are_private(client, monkeypatch):
    monkeypatch.setattr(config, 'AUTH', True)
    assert client.post('/api/mailboxes/setup', json={'profile_id': 1}).status_code == 401
    assert client.post('/api/mailboxes/1/at-preparation', json={}).status_code == 401
    monkeypatch.setattr(config, 'AUTH', False)
    response = client.post('/api/mailboxes/setup', json={'profile_id': 'PRIVATE_FIXTURE'})
    assert response.status_code == 422 and 'PRIVATE_FIXTURE' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    monkeypatch.setattr(config, 'SERVER', True)
    assert client.post('/api/mailboxes/setup', json={'profile_id': 1}).status_code == 409
