import asyncio
import json
import threading

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import db, main, mail_registration as registration


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    with TestClient(main.app) as client:
        yield client


def prepared(client, count=2):
    ids = client.post('/api/profiles/batch', json={'prefix': 'Почта', 'count': count}).json()['ids']
    payload = {'profile_ids': ids, 'domain': 'example.org', 'prefix': 'reader', 'zoid': '1234', 'region': 'eu'}
    result = client.post('/api/mailboxes/prepare', json=payload)
    assert result.status_code == 201
    return payload, result.json()


def body(email='reader01@example.org', account_id='9876'):
    return {'status': {'code': 201}, 'data': {'primaryEmailAddress': email, 'accountId': account_id, 'mailboxStatus': 'enabled'}}


def test_prepare_is_atomic_repeatable_and_private(client):
    payload, result = prepared(client)
    rows = db.rows('SELECT * FROM mailboxes')
    assert len(rows) == 2
    credentials = registration.credentials(rows[0]['id'])
    assert credentials['password'].encode() not in rows[0]['password_cipher']
    serialized = json.dumps(client.get('/api/state').json())
    assert 'password_cipher' not in serialized and credentials['password'] not in serialized
    assert client.post('/api/mailboxes/prepare', json=payload).json()['ids'] == result['ids']
    assert db.rows('SELECT * FROM mailboxes') == rows
    # A late conflict rolls back earlier members and services too.
    profile = client.post('/api/profiles', json={'name': 'Новый'}).json()['id']
    conflicting = {**payload, 'profile_ids': [profile, 1], 'prefix': 'different'}
    assert client.post('/api/mailboxes/prepare', json=conflicting).status_code == 409
    assert len(db.rows('SELECT * FROM mailboxes')) == 2
    assert not db.rows('SELECT * FROM accounts WHERE profile_id=?', (profile,))
    assert not db.rows("SELECT * FROM mailboxes WHERE status='created'")
    assert client.post('/api/mailboxes/prepare', json={**payload, 'domain': 'https://wrong.org'}).status_code == 422
    assert client.post('/api/mailboxes/prepare', json={**payload, 'region': 'https://attacker.test'}).status_code == 422
    assert client.post('/api/mailboxes/prepare', json={**payload, 'zoid': '../other'}).status_code == 422
    response = client.post(f"/api/mailboxes/{rows[0]['id']}/credentials", json={})
    assert response.headers['cache-control'] == 'no-store'
    assert response.json() == credentials


def test_create_uses_exact_region_and_requires_provider_evidence(client):
    prepared(client, 1)
    row = db.rows('SELECT * FROM mailboxes')[0]
    secret = registration.credentials(row['id'])['password']
    requests = []
    def remote(request):
        requests.append(request)
        data = json.loads(request.content)
        assert str(request.url) == 'https://mail.zoho.eu/api/organization/1234/accounts'
        assert request.headers['authorization'] == 'Zoho-oauthtoken TOKEN_TEST'
        assert data['role'] == 'member' and data['password'] == secret
        assert 'mobileNumber' not in data
        return httpx.Response(201, json=body())
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as remote_client:
            assert await registration.create_one(remote_client, row, 'TOKEN_TEST')
    asyncio.run(exercise())
    result = registration.public_rows()[0]
    assert result['status'] == 'created' and result['remote_account_id'] == '9876'
    assert client.get('/api/state').json()['accounts'][0]['status'] == 'in_progress'
    # Replaying a successful creation cannot generate another POST.
    with pytest.raises(HTTPException) as exc:
        registration.select_for_run(registration.RunInput(mailbox_ids=[row['id']], access_token='TOKEN_TEST'))
    assert exc.value.status_code == 409 and len(requests) == 1
    state = json.dumps(client.get('/api/state').json())
    assert secret not in state and 'TOKEN_TEST' not in state


@pytest.mark.parametrize('response', [
    httpx.Response(200, json=body('other@example.org')),
    httpx.Response(200, json={'status': {'code': 201}, 'data': {}}),
    httpx.Response(500, json={'password': 'ECHO_SECRET', 'token': 'ECHO_TOKEN'}),
    httpx.Response(200, text='not json'),
    httpx.Response(302, headers={'Location': 'https://attacker.test'}),
])
def test_ambiguous_response_never_claims_success_or_retries(client, response):
    prepared(client, 1)
    row = db.rows('SELECT * FROM mailboxes')[0]
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response), follow_redirects=False) as remote_client:
            assert not await registration.create_one(remote_client, row, 'ECHO_TOKEN')
    asyncio.run(exercise())
    assert registration.public_rows()[0]['status'] == 'unknown'
    assert client.post('/api/mailboxes/run', json={'mailbox_ids': [row['id']], 'access_token': 'ECHO_TOKEN'}).status_code == 409
    assert 'ECHO_' not in json.dumps(client.get('/api/state').json())


def test_timeout_and_restart_preserve_uncertain_creation(client):
    prepared(client, 2)
    row = db.rows('SELECT * FROM mailboxes')[0]
    def timeout(request):
        raise httpx.ReadTimeout('TOKEN_TEST and password might appear here', request=request)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as remote_client:
            assert not await registration.create_one(remote_client, row, 'TOKEN_TEST')
    asyncio.run(exercise())
    assert registration.public_rows()[0]['status'] == 'unknown'
    registration.set_status(2, 'creating')
    db.init()
    assert all(row['status'] == 'unknown' for row in registration.public_rows())
    assert 'TOKEN_TEST' not in json.dumps(client.get('/api/state').json())


def test_failure_stops_batch_without_registering_remaining_users(client, monkeypatch):
    prepared(client, 2)
    requests = []
    def remote(request):
        requests.append(request)
        return httpx.Response(401, json={'token': 'TOKEN_TEST', 'password': 'SECRET'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(registration.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(remote), **kwargs))
    asyncio.run(registration.run_batch(db.rows('SELECT * FROM mailboxes'), 'TOKEN_TEST'))
    assert len(requests) == 1
    assert [row['status'] for row in registration.public_rows()] == ['failed', 'prepared']
    assert 'TOKEN_TEST' not in json.dumps(client.get('/api/state').json())
    assert 'SECRET' not in json.dumps(client.get('/api/state').json())


def test_cancelled_request_is_unknown_and_second_batch_is_rejected(client, monkeypatch):
    _, result = prepared(client, 2)
    entered = threading.Event()
    async def blocked(rows, token):
        class Remote:
            async def post(self, *args, **kwargs):
                entered.set()
                await asyncio.Event().wait()
        await registration.create_one(Remote(), rows[0], token)
    monkeypatch.setattr(registration, 'run_batch', blocked)
    payload = {'mailbox_ids': result['ids'], 'access_token': 'TOKEN_TEST'}
    assert client.post('/api/mailboxes/run', json=payload).status_code == 202
    assert entered.wait(5), 'Запрос должен начаться перед проверкой остановки'
    # Both prepared and creating count as owned by the active batch.
    assert client.post('/api/mailboxes/run', json=payload).status_code == 409
    assert client.post('/api/mailboxes/stop', json={}).status_code == 200
    assert registration.public_rows()[0]['status'] == 'unknown'
    assert registration.public_rows()[1]['status'] == 'prepared'
    assert not client.get('/api/state').json()['mail_running']
