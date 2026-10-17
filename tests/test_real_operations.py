import time
import pytest
from backend import config, db, main, onboarding, real_mail
from test_onboarding import client, site, pilot, FORM, EMAIL
from test_permanent_mail import remote, BODY, PASSWORD


def profiles(client, count=2):
    return [client.post('/api/profiles', json={'name': f'Проверка {i}'}).json()['id'] for i in range(count)]


def finish(client):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        data = client.get('/api/mail/permanent/state').json()
        if not data['running']:
            return data
        time.sleep(.02)
    raise AssertionError('Mail operation did not finish')


def test_collection_setup_is_atomic_repeatable_and_does_not_launch(client):
    ids = profiles(client)
    body = {'profile_ids': ids, 'urls': ['https://example.org/books', 'https://example.org/books'], 'wait_seconds': 15}
    result = client.post('/api/collection/setup', json=body)
    assert result.status_code == 201, result.text
    assert len(result.json()['created']) == 2 and not result.json()['queued']
    repeat = client.post('/api/collection/setup', json=body).json()
    assert not repeat['created'] and len(repeat['existing']) == 2
    assert len(db.rows('SELECT * FROM jobs')) == 2 and not db.rows('SELECT * FROM runs')
    body['profile_ids'] = [ids[0], 999]
    body['urls'] = ['https://example.org/other']
    assert client.post('/api/collection/setup', json=body).status_code == 404
    assert len(db.rows('SELECT * FROM jobs')) == 2
    for invalid in (['javascript:alert(1)'], ['http://user:pass@example.org/'], ['http://localhost/sample']):
        assert client.post('/api/collection/setup', json={**body, 'profile_ids': ids, 'urls': invalid}).status_code == 422


def test_no_provider_creates_no_mail_or_simulated_profiles(client):
    ids = profiles(client)
    result = client.post('/api/mail/permanent/create', json={'profile_ids': ids})
    assert result.status_code == 409
    assert not db.rows('SELECT * FROM permanent_mail_accounts')
    assert not db.rows('SELECT * FROM simulation_profiles')
    assert client.get('/api/simulation/mail/state').status_code == 409


def test_real_mail_batch_uses_real_profiles_and_confirms_external_result(client, remote):
    ids = profiles(client)
    assert client.post('/api/mail/permanent/connect', json=BODY).status_code == 200
    assert client.post('/api/mail/permanent/create', json={'profile_ids': ids}).status_code == 202
    data = finish(client)
    assert len(data['accounts']) == 2 and all(row['ready'] for row in data['accounts'])
    assert {row['profile_id'] for row in data['accounts']} == set(ids)
    assert not db.rows('SELECT * FROM simulation_profiles')
    assert len(remote['posts']) == 2
    state = client.get('/api/state').json()
    assert len(state['mailboxes']) == 2
    assert all(row['login_verified'] and row['provisioned'] for row in state['mailboxes'])
    assert BODY['api_key'] not in str(state) + str(data)
    assert client.post('/api/mail/permanent/create', json={'profile_ids': ids}).status_code == 409
    assert len(remote['posts']) == 2


def test_unknown_post_stops_real_batch_and_reconcile_never_posts_again(client, remote):
    ids = profiles(client)
    client.post('/api/mail/permanent/connect', json=BODY)
    remote['mode'] = 'timeout-created'
    client.post('/api/mail/permanent/create', json={'profile_ids': ids})
    data = finish(client)
    assert sorted(row['status'] for row in data['accounts']) == ['failed', 'unknown']
    assert len(remote['posts']) == 1
    unknown = next(row for row in data['accounts'] if row['status'] == 'unknown')
    assert client.post(f"/api/mail/permanent/{unknown['id']}/reconcile").json()['confirmed']
    assert len(remote['posts']) == 1
    assert len(client.get('/api/state').json()['mailboxes']) == 1


def test_manual_mail_verified_only_after_explicit_check_and_secrets_are_private(client, remote):
    pid = profiles(client, 1)[0]
    body = {'profile_id': pid, 'provider': 'Yandex', 'email': EMAIL, 'password': PASSWORD}
    response = client.post('/api/mail/permanent/attach', json=body)
    assert response.status_code == 201, response.text
    mid = response.json()['id']
    assert not client.get('/api/mail/permanent/state').json()['accounts'][0]['ready']
    assert not client.get('/api/state').json()['mailboxes']
    assert not remote['logins']
    assert client.post(f'/api/mail/permanent/{mid}/verify').json()['verified']
    assert len(client.get('/api/state').json()['mailboxes']) == 1
    credentials = client.post(f'/api/mail/permanent/{mid}/credentials')
    assert credentials.json()['password'] == PASSWORD and credentials.headers['cache-control'] == 'no-store'
    assert PASSWORD not in client.get('/api/mail/permanent/state').text
    invalid = client.post('/api/mail/permanent/attach', json={**body, 'email': 'invalid'})
    assert invalid.status_code == 422 and PASSWORD not in invalid.text


def test_server_at_registration_and_manual_browser_control_preserve_session(client, site, monkeypatch):
    aid = pilot(client)
    monkeypatch.setattr(config, 'SERVER', True)
    result = client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'action': 'login'})
    assert result.status_code == 200 and result.json()['remote_open'], result.text
    assert not site['posts']
    image = client.get(f'/api/onboarding/at/{aid}/screen')
    assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
    assert image.headers['cache-control'] == 'no-store'
    # A manual Enter in the already-filled password field submits the fixture login.
    control = client.post(f'/api/onboarding/at/{aid}/control', json={'action': 'key', 'key': 'Enter'})
    assert control.status_code == 200, control.text
    assert len(site['posts']) == 1
    assert client.post(f'/api/onboarding/at/{aid}/control', json={'action': 'navigate', 'url': 'https://other.example/'}).status_code == 422
    verified = client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True})
    assert verified.json()['status'] == 'session_verified', verified.text
    assert not verified.json()['remote_open']
    assert not main.open_profiles


def test_server_browser_respects_worker_lease(client, site, monkeypatch):
    from backend import runs
    aid = pilot(client)
    monkeypatch.setattr(config, 'SERVER', True)
    assert runs.acquire_lease(1, 'fixture-worker', 'collect', 60)
    response = client.post(f'/api/onboarding/at/{aid}/form', json=FORM)
    assert response.status_code == 409 and not main.open_profiles
    runs.release_lease(1, 'fixture-worker')
