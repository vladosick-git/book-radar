import asyncio
import json
import re
import time
import ssl
from urllib.parse import unquote

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from backend import config, db, main, permanent_mail as mail, mailcow_client as provider, protection, simulation as sim, simulation_fleet as fleet
from test_simulation import client, http_server

KEY = 'fixture-api-key-no-production-access'
PASSWORD = 'fixture-owner-mail-password-A9!'
BODY = {'origin': 'https://mail.example.org', 'domain': 'example.org', 'api_key': KEY}


@pytest.fixture
def remote(client, monkeypatch, tmp_path):
    key_file = tmp_path / 'vault.key'
    key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(key_file))
    data = {'accounts': {}, 'posts': [], 'mode': 'ok', 'slots': 1000, 'logins': []}
    def handle(request):
        assert request.url.host == 'mail.example.org'
        assert request.headers['X-API-Key'] == KEY
        path = unquote(request.url.path)
        if path == '/api/v1/get/domain/example.org':
            return httpx.Response(200, json={'domain_name': 'example.org', 'active': '1', 'mboxes_left': data['slots']})
        if path.startswith('/api/v1/get/mailbox/'):
            address = path.rsplit('/', 1)[-1]
            return httpx.Response(200, json=data['accounts'].get(address, {}))
        if request.method == 'POST':
            body = json.loads(request.content)
            data['posts'].append(body)
            email = body['local_part'] + '@' + body['domain']
            record = {'username': email, 'active': '1', 'name': body['name']}
            if data['mode'] in ('timeout-created', 'ok', 'stop-after-one'):
                data['accounts'][email] = record
            if data['mode'] == 'stop-after-one':
                mail.stop_requested = True
            if data['mode'] == 'timeout-created':
                raise httpx.ReadTimeout('fixture secret must never reach errors', request=request)
            if data['mode'] == 'reject':
                return httpx.Response(200, json=[{'type': 'danger', 'msg': ['quota_exceeded', body['password']]}])
            if data['mode'] == 'malformed':
                return httpx.Response(200, json={'log': [body['password']], 'type': 'success'})
            return httpx.Response(200, json=[{'type': 'success', 'msg': ['mailbox_added', email], 'log': [body['password']]}])
        raise AssertionError(path)
    def client_factory(origin, key):
        assert origin == BODY['origin'] and key == KEY
        return httpx.AsyncClient(base_url=origin, headers={'X-API-Key': key}, transport=httpx.MockTransport(handle), follow_redirects=False)
    def login(host, email, password):
        data['logins'].append((host, email, password))
        if data['mode'] == 'bad-password':
            raise ValueError(PASSWORD)
    monkeypatch.setattr(provider, 'http_client', client_factory)
    monkeypatch.setattr(provider, 'imap_login', login)
    return data


def connect(client):
    r = client.post('/api/simulation/mail/connect', json=BODY)
    assert r.status_code == 200, r.text


def finish(client):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = client.get('/api/simulation/mail/state').json()
        if not state['running']:
            return state
        time.sleep(.02)
    raise AssertionError(state)


def attach(client, **extra):
    body = {'email': 'ivan.petrov@example.org', 'display_name': 'Иван Петров', 'provider': 'Yandex', 'password': PASSWORD, **extra}
    r = client.post('/api/simulation/mail/attach', json=body)
    assert r.status_code == 201, r.text
    return r.json()['id']


def test_unconfigured_create_never_generates_fake_permanent_mailboxes(client):
    assert client.post('/api/simulation/mail/create', json={'count': 2}).status_code == 409
    assert not db.rows('SELECT * FROM permanent_mail_accounts')
    assert not db.rows('SELECT * FROM simulation_profiles')
    assert not client.get('/api/simulation/mail/state').json()['configured']


def test_connection_is_https_only_and_secrets_stay_encrypted_and_out_of_state(client, remote):
    for url in ('http://mail.example.org', 'https://user:pass@mail.example.org', 'https://mail.example.org/api', 'https://mail.example.org?key=secret'):
        r = client.post('/api/simulation/mail/connect', json={**BODY, 'origin': url})
        assert r.status_code == 422 and KEY not in r.text
    connect(client)
    state = client.get('/api/simulation/mail/state')
    assert state.headers['cache-control'] == 'no-store'
    assert KEY not in state.text and 'key_cipher' not in state.text
    blob = bytes(db.rows('SELECT key_cipher FROM permanent_mail_config')[0]['key_cipher'])
    assert blob.startswith(protection.VAULT_PREFIX) and KEY.encode() not in blob


def test_created_mailboxes_have_human_names_distinct_credentials_and_verified_login(client, remote):
    connect(client)
    r = client.post('/api/simulation/mail/create', json={'count': 3})
    assert r.status_code == 202, r.text
    state = finish(client)
    assert len(state['accounts']) == 3 and state['linked'] == 3 and state['free'] == 0
    assert all(m['status'] == 'created' and m['ready'] for m in state['accounts'])
    assert len({p['password'] for p in remote['posts']}) == 3
    for row in state['accounts']:
        assert re.fullmatch(r'[a-z]+\.[a-z]+\d{2,4}@example\.org', row['email'])
        assert row['display_name'] and row['profile_id']
        secrets = client.post(f"/api/simulation/mail/{row['id']}/credentials").json()
        blob = bytes(db.rows('SELECT secret_cipher FROM permanent_mail_accounts WHERE id=?', (row['id'],))[0]['secret_cipher'])
        assert secrets['password'].encode() not in blob and blob.startswith(protection.VAULT_PREFIX)
        assert secrets['password'] not in json.dumps(state)
    assert len(remote['logins']) == 3
    assert not db.rows('SELECT * FROM mailboxes') and not db.rows('SELECT * FROM accounts')


def test_domain_capacity_is_checked_before_any_profiles_or_post(client, remote):
    connect(client)
    remote['slots'] = 1
    r = client.post('/api/simulation/mail/create', json={'count': 2})
    assert r.status_code == 409
    assert not remote['posts'] and not db.rows('SELECT * FROM simulation_profiles')


def test_existing_remote_mailbox_is_not_overwritten(client, remote, monkeypatch):
    connect(client)
    email = 'ivan.petrov27@example.org'
    remote['accounts'][email] = {'username': email, 'active': '1'}
    monkeypatch.setattr(mail, 'identity', lambda c, domain: ('Иван Петров', email))
    client.post('/api/simulation/mail/create', json={'count': 1})
    state = finish(client)
    assert state['accounts'][0]['status'] == 'failed'
    assert not remote['posts'] and not remote['logins']


def test_stop_drains_current_mailbox_without_sending_remaining_creates(client, remote):
    connect(client)
    remote['mode'] = 'stop-after-one'
    client.post('/api/simulation/mail/create', json={'count': 3})
    state = finish(client)
    assert len(remote['posts']) == 1
    assert sum(m['status'] == 'created' for m in state['accounts']) == 1
    assert sum(m['status'] == 'failed' for m in state['accounts']) == 2
    assert not client.post('/api/simulation/mail/stop').json()['stopping']


def test_unknown_post_is_not_repeated_and_reconciliation_checks_saved_password(client, remote):
    connect(client)
    remote['mode'] = 'timeout-created'
    assert client.post('/api/simulation/mail/create', json={'count': 1}).status_code == 202
    state = finish(client)
    row = state['accounts'][0]
    assert row['status'] == 'unknown' and not row['ready']
    asyncio.run(mail.create_one(row['id'], mail.configuration()))
    assert len(remote['posts']) == 1
    remote['mode'] = 'bad-password'
    assert not client.post(f"/api/simulation/mail/{row['id']}/reconcile").json()['confirmed']
    assert finish(client)['accounts'][0]['status'] == 'unknown'
    remote['mode'] = 'ok'
    assert client.post(f"/api/simulation/mail/{row['id']}/reconcile").json()['confirmed']
    assert finish(client)['accounts'][0]['ready']
    assert len(remote['posts']) == 1


@pytest.mark.parametrize('mode,expected', [('reject', 'failed'), ('malformed', 'unknown')])
def test_error_bodies_are_redacted_and_success_without_confirmation_is_not_ready(client, remote, mode, expected):
    connect(client)
    remote['mode'] = mode
    client.post('/api/simulation/mail/create', json={'count': 1})
    state = finish(client)
    assert state['accounts'][0]['status'] == expected and not state['accounts'][0]['ready']
    assert remote['posts'][0]['password'] not in json.dumps(state)


def test_mixed_provider_registry_encrypts_app_password_and_recovery_and_assigns_once(client, remote):
    first = attach(client, app_password='fixture-app-password-unique', recovery_email='recovery@example.org')
    second = attach(client, email='pavel.sokolov@mail.example.org', provider='Mail.ru')
    assert not remote['posts'] and not remote['logins']
    assert client.post('/api/simulation/mail/assign', json={'count': 1}).status_code == 409
    for mid in (first, second):
        assert client.post(f'/api/simulation/mail/{mid}/verify').json()['verified']
    assert remote['logins'][0][0] == 'imap.yandex.ru' and remote['logins'][0][2] == 'fixture-app-password-unique'
    assert remote['logins'][1][0] == 'imap.mail.ru'
    public = client.get('/api/simulation/mail/state').text
    assert 'fixture-app-password-unique' not in public and PASSWORD not in public and 'recovery@example.org' not in public
    reveal = client.post(f'/api/simulation/mail/{first}/credentials')
    assert reveal.headers['cache-control'] == 'no-store' and reveal.json()['recovery_email'] == 'recovery@example.org'
    assert client.post('/api/simulation/mail/assign', json={'count': 2}).status_code == 201
    assert client.post('/api/simulation/mail/assign', json={'count': 1}).status_code == 409
    assert len({p['email'] for p in db.rows('SELECT email FROM simulation_profiles')}) == 2
    assert client.post('/api/simulation/mail/attach', json={'email': 'IVAN.PETROV@example.org', 'password': 'different'}).status_code == 409


def test_permanent_fleet_excludes_fixtures_and_unverified_mail(client, remote):
    client.post('/api/simulation/batch', json={'count': 3})
    attach(client)
    with db.connect() as c:
        assert fleet.plan(c, 1, True)['available'] == 0
    with pytest.raises(HTTPException):
        fleet.enqueue(fleet.StartInput(target=1, permanent_only=True, read_books=False))
    mid = client.get('/api/simulation/mail/state').json()['accounts'][0]['id']
    client.post(f'/api/simulation/mail/{mid}/verify')
    rid = fleet.enqueue(fleet.StartInput(target=1, permanent_only=True, read_books=False))
    members = db.rows('SELECT profile_id FROM simulation_fleet_members WHERE run_id=?', (rid,))
    assert len(members) == 1 and members[0]['profile_id'] == 4


def test_real_browser_can_use_profile_after_confirmed_permanent_mail_creation(client, remote):
    connect(client)
    client.post('/api/simulation/mail/create', json={'count': 1})
    pid = finish(client)['accounts'][0]['profile_id']
    with http_server(main.app) as origin:
        assert asyncio.run(sim.register_one(pid, origin))['status'] == 'registered'
        assert asyncio.run(sim.verify_one(pid, origin))['used_saved_session']
    assert db.rows('SELECT email FROM simulation_accounts')[0]['email'].endswith('@example.org')


def test_pending_mail_cannot_be_used_and_restart_does_not_retry_post(client, remote):
    connect(client)
    remote['mode'] = 'timeout-created'
    client.post('/api/simulation/mail/create', json={'count': 1})
    row = finish(client)['accounts'][0]
    with pytest.raises(HTTPException):
        sim.claim_profile(row['profile_id'], 'registering')
    db.execute("UPDATE permanent_mail_accounts SET status='creating'")
    mail.recover()
    assert client.get('/api/simulation/mail/state').json()['accounts'][0]['status'] == 'unknown'
    assert len(remote['posts']) == 1


def test_rotation_includes_mailbox_secrets_and_provider_key_atomically(client, remote, tmp_path, monkeypatch):
    connect(client)
    mid = attach(client)
    old_key = config.VAULT_KEY_FILE
    new_key = tmp_path / 'new-vault.key'
    new_key.write_bytes(Fernet.generate_key())
    result = protection.rotate(old_key, str(new_key), db.DATA)
    assert result['mailboxes'] == 1 and result['settings'] == 1
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(new_key))
    assert mail.configuration()['key'] == KEY
    assert mail.credentials(mid)['password'] == PASSWORD


def test_operation_gate_and_private_validation(client, remote):
    owner = mail.claim()
    try:
        assert client.post('/api/simulation/mail/connect', json=BODY).status_code == 409
    finally:
        mail.release(owner)
    body = {'email': 'invalid', 'password': PASSWORD}
    r = client.post('/api/simulation/mail/attach', json=body)
    assert r.status_code == 422 and PASSWORD not in r.text and 'input' not in r.text
    assert client.post('/api/simulation/mail/999/credentials').status_code == 404


def test_mailcow_redirect_never_forwards_key_to_another_origin(monkeypatch):
    original = httpx.AsyncClient
    seen = []
    def handle(request):
        seen.append(str(request.url))
        assert request.url.host == 'mail.example.org'
        return httpx.Response(302, headers={'Location': 'https://other.example.org/key-sink'})
    def client(**kwargs):
        assert kwargs['trust_env'] is False
        return original(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(provider.httpx, 'AsyncClient', client)
    with pytest.raises(provider.ProviderError):
        asyncio.run(provider.Mailcow(BODY['origin'], KEY).domain('example.org'))
    assert seen == ['https://mail.example.org/api/v1/get/domain/example.org']


def test_imap_probe_requires_tls_certificate_verification(monkeypatch):
    class FakeImap:
        def __init__(self, host, port, ssl_context, timeout):
            assert host == 'imap.example.org' and port == 993 and timeout <= 15
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED and ssl_context.check_hostname
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def login(self, email, password):
            assert email == 'ivan@example.org' and password == PASSWORD
            return 'OK', []
    monkeypatch.setattr(provider.imaplib, 'IMAP4_SSL', FakeImap)
    provider.imap_login('imap.example.org', 'ivan@example.org', PASSWORD)
