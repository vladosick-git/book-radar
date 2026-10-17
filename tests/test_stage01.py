import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import auth, config, db, main, mail_registration as mail, protection


@pytest.fixture
def client(tmp_path, monkeypatch):
    key = tmp_path / 'vault.key'
    key.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(db, 'DATA', tmp_path / 'runtime')
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(key))
    monkeypatch.setattr(config, 'AUTH', False)
    monkeypatch.setattr(config, 'SERVER', False)
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    with TestClient(main.app) as instance:
        yield instance


def prepare(client, count=1):
    ids = client.post('/api/profiles/batch', json={'prefix': 'Пилот', 'count': count}).json()['ids']
    payload = {'profile_ids': ids, 'domain': 'example.org', 'zoid': '1234'}
    assert client.post('/api/mailboxes/prepare', json=payload).status_code == 201
    return payload, db.rows('SELECT * FROM mailboxes ORDER BY id')


def read_body(email='reader01@example.org', account='999', enabled=True):
    return {'status': {'code': 200}, 'data': [{'primaryEmailAddress': email, 'accountId': account,
        'mailboxStatus': 'enabled' if enabled else 'disabled'}]}


def reconcile(row, response):
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as remote:
            return await mail.reconcile_one(remote, row['id'], 'PRIVATE_TOKEN')
    return asyncio.run(exercise())


def test_server_mail_auth_encryption_and_secret_response(client, monkeypatch):
    monkeypatch.setattr(config, 'SERVER', True)
    monkeypatch.setattr(config, 'AUTH', True)
    monkeypatch.setattr(config, 'COOKIE_SECURE', False)
    monkeypatch.setattr(config, 'ADMIN_PASSWORD_HASH', auth.make_hash('panel-only'))
    assert client.post('/api/mailboxes/prepare', json={'profile_ids': [1], 'domain': 'example.org', 'zoid': '1'}).status_code == 401
    assert client.post('/api/mailboxes/1/reconcile', json={'access_token': 'PRIVATE_TOKEN'}).status_code == 401
    assert client.post('/api/login', json={'password': 'panel-only'}).status_code == 200
    payload, rows = prepare(client, 10)
    assert len(rows) == 10
    saved = rows[0]['password_cipher']
    assert saved.startswith(protection.VAULT_PREFIX)
    secret = mail.credentials(rows[0]['id'])['password']
    assert secret.encode() not in saved
    assert client.post('/api/mailboxes/prepare', json=payload).status_code == 201
    assert db.rows('SELECT * FROM mailboxes')[0]['password_cipher'] == saved
    state = client.get('/api/state').text
    assert secret not in state and 'password_cipher' not in state
    response = client.post('/api/mailboxes/1/credentials', json={})
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['password'] == secret
    keyfile = config.VAULT_KEY_FILE
    from pathlib import Path
    Path(keyfile).write_bytes(Fernet.generate_key())
    assert client.post('/api/mailboxes/1/credentials', json={}).status_code == 400
    assert db.rows('SELECT * FROM mailboxes')[0]['password_cipher'] == saved


def test_no_plaintext_storage_without_vault(client, monkeypatch):
    monkeypatch.setattr(config, 'SERVER', True)
    monkeypatch.setattr(config, 'AUTH', True)
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', '')
    with pytest.raises(HTTPException) as exc:
        mail.prepare(mail.PrepareInput(profile_ids=[1], domain='example.org', zoid='1'))
    assert exc.value.status_code == 503
    assert not db.rows('SELECT * FROM mailboxes')


def test_attach_is_stable_distinct_and_conflicts_are_preserved(client):
    profile = client.post('/api/profiles', json={'name': 'Владелец'}).json()['id']
    payload = {'profile_id': profile, 'provider': 'Proton', 'email': 'owner@example.org', 'password': 'PRIVATE_PASSWORD'}
    first = client.post('/api/mailboxes/attach', json=payload)
    assert first.status_code == 201 and first.json()['status'] == 'attached'
    original = db.rows('SELECT * FROM mailboxes')
    assert client.post('/api/mailboxes/attach', json=payload).json() == first.json()
    assert db.rows('SELECT * FROM mailboxes') == original
    public = client.get('/api/state').json()
    assert public['mailboxes'][0]['status'] == 'attached' and public['mailboxes'][0]['profile_id'] == profile
    assert 'PRIVATE_PASSWORD' not in json.dumps(public)
    assert client.post('/api/mailboxes/run', json={'mailbox_ids': [first.json()['id']], 'access_token': 'PRIVATE_TOKEN'}).status_code == 409
    assert client.post('/api/mailboxes/attach', json={**payload, 'password': 'different'}).status_code == 409
    assert db.rows('SELECT * FROM mailboxes') == original
    assert client.post(f"/api/accounts/{original[0]['account_id']}", json={'identifier': 'new@example.org', 'status': 'confirmed'}).status_code == 409


def test_optional_password_and_validation_never_echo_secrets(client):
    client.post('/api/profiles', json={'name': 'Без пароля'})
    response = client.post('/api/mailboxes/attach', json={'profile_id': 1, 'provider': 'Zoho', 'email': 'owner@example.org'})
    assert response.status_code == 201
    assert client.post('/api/mailboxes/1/credentials', json={}).status_code == 404
    secret = 'PRIVATE_PASSWORD' * 30
    response = client.post('/api/mailboxes/attach', json={'profile_id': 1, 'provider': 'Zoho', 'email': 'wrong', 'password': secret})
    assert response.status_code == 422 and secret not in response.text and '"input"' not in response.text
    response = client.post('/api/mailboxes/run', json={'mailbox_ids': [], 'access_token': {'token': 'PRIVATE_TOKEN'}})
    assert response.status_code == 422 and 'PRIVATE_TOKEN' not in response.text


def test_reconcile_confirms_identity_with_read_only_and_marks_password_unverified(client):
    _, rows = prepare(client)
    mail.set_status(1, 'unknown')
    calls = []
    def remote(request):
        calls.append(request)
        assert request.method == 'GET' and request.url.params['start'] == '1'
        assert request.url.host == 'mail.zoho.eu'
        return httpx.Response(200, json=read_body())
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as remote_client:
            return await mail.reconcile_one(remote_client, 1, 'PRIVATE_TOKEN')
    result = asyncio.run(exercise())
    assert result['outcome'] == 'confirmed' and len(calls) == 1
    row = mail.public_rows()[0]
    assert row['status'] == 'created' and row['remote_account_id'] == '999'
    assert row['password_state'] == 'unverified' and row['reconciled_at']
    assert client.post('/api/mailboxes/run', json={'mailbox_ids': [1], 'access_token': 'PRIVATE_TOKEN'}).status_code == 409
    assert 'PRIVATE_TOKEN' not in client.get('/api/state').text


@pytest.mark.parametrize('response,outcome', [
    (httpx.Response(200, json={'status': {'code': 200}, 'data': []}), 'not_found'),
    (httpx.Response(200, json=read_body(email='other@example.org')), 'not_found'),
    (httpx.Response(200, json=read_body(enabled=False)), 'inactive_or_unconfirmed'),
    (httpx.Response(200, json=read_body(account='bad')), 'inactive_or_unconfirmed'),
    (httpx.Response(401, json={'token': 'PRIVATE_TOKEN'}), 'unavailable'),
    (httpx.Response(500, json={'password': 'PRIVATE_PASSWORD'}), 'unavailable'),
    (httpx.Response(302, headers={'location': 'https://evil.test'}), 'unavailable'),
    (httpx.Response(200, text='bad json'), 'unavailable'),
])
def test_read_failure_or_absence_never_unlocks_unknown_creation(client, response, outcome):
    _, rows = prepare(client)
    mail.set_status(1, 'unknown')
    assert reconcile(rows[0], response)['outcome'] == outcome
    assert mail.public_rows()[0]['status'] == 'unknown'
    assert client.post('/api/mailboxes/run', json={'mailbox_ids': [1], 'access_token': 'PRIVATE_TOKEN'}).status_code == 409
    public = client.get('/api/state').text
    assert 'PRIVATE_TOKEN' not in public and 'PRIVATE_PASSWORD' not in public


def test_paginated_reconcile_uses_primary_address_not_alias(client):
    _, rows = prepare(client)
    mail.set_status(1, 'unknown')
    calls = []
    def remote(request):
        start = int(request.url.params['start']); calls.append(start)
        records = [{'primaryEmailAddress': f'user{i}@example.org', 'accountId': str(i), 'mailboxStatus': 'enabled',
            'emailAddress': [{'mailId': rows[0]['email'], 'isAlias': True}]} for i in range(100)] if start == 1 else read_body()['data']
        return httpx.Response(200, json={'status': {'code': 200}, 'data': records})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as remote_client:
            return await mail.reconcile_one(remote_client, 1, 'PRIVATE_TOKEN')
    assert asyncio.run(exercise())['outcome'] == 'confirmed'
    assert calls == [1, 101]


def test_live_lease_survives_worker_init_and_expired_lease_recovers(client):
    _, rows = prepare(client)
    owner = mail.claim_operation('create')
    mail.set_status(1, 'creating')
    db.init()  # a separate worker starts while API is provisioning
    assert mail.public_rows()[0]['status'] == 'creating'
    with pytest.raises(HTTPException):
        mail.claim_operation('create')
    db.execute('UPDATE mail_operations SET expires_at=0')
    db.init()
    assert mail.public_rows()[0]['status'] == 'unknown'
    assert mail.public_rows()[0]['error_code'] == 'INTERRUPTED'
    assert not mail.operation_active()


def test_two_real_processes_cannot_post_same_mailbox(client):
    _, rows = prepare(client)
    entered = threading.Event()
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            entered.set(); time.sleep(0.5)
            body = json.dumps({'status': {'code': 201}, 'data': {'primaryEmailAddress': rows[0]['email'], 'accountId': '987', 'mailboxStatus': 'enabled'}}).encode()
            self.send_response(201); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    script = '''import asyncio,sys
from fastapi import HTTPException
from backend import db,mail_registration as m
db.init()
try: owner=m.claim_operation('create')
except HTTPException: sys.exit(3)
try:
 m.REGIONS['eu']=sys.argv[1]
 rows,token=m.select_for_run(m.RunInput(mailbox_ids=[1],access_token='PRIVATE_TOKEN'))
 asyncio.run(m.run_batch(rows,token))
finally: m.release_operation(owner)
'''
    env = {**os.environ, 'RADAR_DATA_DIR': str(db.DATA), 'RADAR_VAULT_KEY_FILE': config.VAULT_KEY_FILE, 'RADAR_MODE': 'local'}
    def run():
        return subprocess.run([sys.executable, '-c', script, f'http://127.0.0.1:{server.server_port}'], env=env, capture_output=True, timeout=15)
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(run)
            assert entered.wait(10)
            second = pool.submit(run)
            results = [first.result(), second.result()]
        assert sorted(result.returncode for result in results) == [0, 3], [result.stderr for result in results]
        assert len(calls) == 1 and mail.public_rows()[0]['status'] == 'created'
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_second_failure_preserves_prior_success_and_untouched_remaining(client, monkeypatch):
    _, rows = prepare(client, 4)
    calls = []
    def remote(request):
        calls.append(json.loads(request.content)['primaryEmailAddress'])
        if len(calls) == 3:
            return httpx.Response(403, json={'password': 'PRIVATE_PASSWORD'})
        return httpx.Response(201, json={'status': {'code': 201}, 'data': {'primaryEmailAddress': calls[-1], 'accountId': str(100+len(calls)), 'mailboxStatus': 'enabled'}})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(mail.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(remote), **kwargs))
    asyncio.run(mail.run_batch(rows, 'PRIVATE_TOKEN'))
    assert [row['status'] for row in mail.public_rows()] == ['created', 'created', 'failed', 'prepared']
    assert len(calls) == 3 and mail.public_rows()[2]['error_code'] == 'HTTP_403'
    assert 'PRIVATE_PASSWORD' not in client.get('/api/state').text


def test_stale_created_row_with_broken_secret_cannot_become_retryable(client):
    _, rows = prepare(client)
    mail.set_status(1, 'created', remote_id='999')
    stale = {**rows[0], 'password_cipher': b'broken'}
    class NoNetwork:
        async def post(self, *args, **kwargs):
            pytest.fail('A previously created mailbox must not cause another POST')
    assert not asyncio.run(mail.create_one(NoNetwork(), stale, 'PRIVATE_TOKEN'))
    assert mail.public_rows()[0]['status'] == 'created'


def test_read_limit_is_bounded_and_never_replays_post(client):
    _, rows = prepare(client)
    mail.set_status(1, 'unknown')
    calls = []
    def remote(request):
        calls.append(request)
        return httpx.Response(200, json={'status': {'code': 200}, 'data': [
            {'primaryEmailAddress': f'other{i}@example.org', 'accountId': str(i), 'mailboxStatus': 'enabled'} for i in range(100)]})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as remote_client:
            return await mail.adapter.reconcile(remote_client, 1, 'PRIVATE_TOKEN')
    result = asyncio.run(exercise())
    assert result.outcome == 'limit_reached' and result.status == 'unknown'
    assert len(calls) == 10 and all(request.method == 'GET' for request in calls)


def test_migration_keeps_all_legacy_mailbox_fields(client):
    _, rows = prepare(client)
    columns = ('id', 'account_id', 'region', 'zoid', 'email', 'password_cipher', 'status', 'remote_account_id', 'error', 'created_at', 'updated_at')
    snapshot = [{key: row[key] for key in columns} for row in db.rows('SELECT * FROM mailboxes')]
    # Exercise actual ALTER migrations rather than comparing a freshly initialized schema.
    with db.connect() as connection:
        for name in ('provider', 'source', 'error_code', 'password_state', 'reconciled_at'):
            connection.execute(f'ALTER TABLE mailboxes DROP COLUMN {name}')
        connection.execute('DROP TABLE mail_operations')
    db.init(); db.init()
    after = [{key: row[key] for key in columns} for row in db.rows('SELECT * FROM mailboxes')]
    assert after == snapshot
    assert mail.public_rows()[0]['provider'] == 'Zoho'


def test_reconcile_endpoint_and_owner_lock_release(client, monkeypatch):
    _, rows = prepare(client)
    mail.set_status(1, 'unknown')
    real_client = httpx.AsyncClient
    monkeypatch.setattr(mail.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=read_body())), **kwargs))
    response = client.post('/api/mailboxes/1/reconcile', json={'access_token': 'PRIVATE_TOKEN'})
    assert response.status_code == 200 and response.json()['outcome'] == 'confirmed'
    assert response.headers['cache-control'] == 'no-store'
    assert not mail.operation_active()


def test_cancelling_before_task_entry_releases_durable_operation(client):
    _, rows = prepare(client)
    async def exercise():
        await main.run_mailboxes(mail.RunInput(mailbox_ids=[1], access_token='PRIVATE_TOKEN'))
        # No yield: the batch coroutine has not started, so its finally block cannot run.
        await main.stop_mailboxes()
    asyncio.run(exercise())
    assert not mail.operation_active() and mail.public_rows()[0]['status'] == 'prepared'


@pytest.mark.skipif(os.name != 'nt', reason='Legacy Windows DPAPI migration')
def test_legacy_dpapi_password_migrates_when_owner_reads_with_vault(client, monkeypatch):
    _, rows = prepare(client)
    original_key = config.VAULT_KEY_FILE
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', '')
    cipher = protection.protect(b'PRIVATE_PASSWORD')
    db.execute('UPDATE mailboxes SET password_cipher=? WHERE id=1', (cipher,))
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', original_key)
    assert mail.credentials(1)['password'] == 'PRIVATE_PASSWORD'
    assert db.rows('SELECT password_cipher FROM mailboxes')[0]['password_cipher'].startswith(protection.VAULT_PREFIX)
