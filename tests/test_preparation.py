import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    with TestClient(main.app) as instance:
        yield instance


def service(client, name='Тестовая почта', url='https://example.com/register'):
    response = client.post('/api/services', json={'name': name, 'kind': 'email', 'registration_url': url})
    assert response.status_code == 201
    return response.json()['id']


def assign(client, profiles, services):
    return client.post('/api/accounts/assign', json={'profile_ids': profiles, 'service_ids': services})


def test_batch_is_atomic_and_bounded(client):
    for payload in [{'prefix': ' ', 'count': 10}, {'prefix': 'Серия', 'count': 11}, {'prefix': 'Серия', 'count': 0}]:
        assert client.post('/api/profiles/batch', json=payload).status_code == 422
    client.post('/api/profiles', json={'name': 'Серия 04'})
    failed = client.post('/api/profiles/batch', json={'prefix': 'Серия', 'count': 3, 'start': 2})
    assert failed.status_code == 409
    assert [row['name'] for row in db.rows('SELECT * FROM profiles')] == ['Серия 04']
    result = client.post('/api/profiles/batch', json={'prefix': ' Проверка ', 'count': 10})
    assert result.status_code == 201
    assert result.json()['names'] == [f'Проверка {number:02d}' for number in range(1, 11)]
    assert len(set(result.json()['ids'])) == 10
    assert not db.rows('SELECT * FROM accounts')


def test_assignment_validation_idempotency_and_manual_confirmation(client):
    profiles = client.post('/api/profiles/batch', json={'prefix': 'Профиль', 'count': 2}).json()['ids']
    for url in ['file:///C:/secret', 'javascript:alert(1)', 'https://login:secret@example.com', 'https://example.com:bad', 'https://[invalid']:
        assert client.post('/api/services', json={'name': 'Ошибка', 'kind': 'email', 'registration_url': url}).status_code == 422
    first = service(client)
    assert client.post('/api/services', json={'name': 'ТЕСТОВАЯ ПОЧТА', 'kind': 'email', 'registration_url': 'https://example.com'}).status_code == 409
    second = service(client, 'Площадка')
    assert client.post(f'/api/services/{second}', json={'name': 'Тестовая почта', 'kind': 'site', 'registration_url': 'https://example.com/new'}).status_code == 409
    assert client.post(f'/api/services/{second}', json={'name': 'Книжная площадка', 'kind': 'site', 'registration_url': 'https://example.com/new'}).status_code == 200
    assert client.get('/api/state').json()['services'][1]['registration_url'] == 'https://example.com/new'
    assert assign(client, profiles + [999], [first]).status_code == 404
    assert assign(client, profiles, [999]).status_code == 404
    assert not db.rows('SELECT * FROM accounts')
    assert assign(client, profiles + [profiles[0]], [first, second]).json() == {'created': 4, 'existing': 0}
    account = db.rows('SELECT * FROM accounts')[0]
    assert account['status'] == 'planned' and account['confirmed_at'] is None
    assert client.post(f"/api/accounts/{account['id']}", json={'status': 'confirmed', 'identifier': ' '}).status_code == 422
    update = {'status': 'confirmed', 'identifier': ' reader@example.test ', 'note': ' Проверено вручную '}
    assert client.post(f"/api/accounts/{account['id']}", json=update).status_code == 200
    confirmed = db.rows('SELECT * FROM accounts WHERE id=?', (account['id'],))[0]
    assert confirmed['confirmed_at'] and confirmed['identifier'] == 'reader@example.test'
    assert assign(client, profiles, [first, second]).json() == {'created': 0, 'existing': 4}
    assert db.rows('SELECT * FROM accounts WHERE id=?', (account['id'],))[0] == confirmed
    assert client.post(f"/api/accounts/{account['id']}", json={'status': 'planned', 'identifier': 'reader@example.test'}).status_code == 200
    assert db.rows('SELECT * FROM accounts WHERE id=?', (account['id'],))[0]['confirmed_at'] is None
    assert client.post('/api/accounts/999', json={'status': 'planned'}).status_code == 404


def test_upgrade_keeps_existing_records_and_is_repeatable(client):
    client.post('/api/profiles', json={'name': 'Старый профиль'})
    client.post('/api/jobs', json={'name': 'Старое задание', 'profile_id': 1, 'urls': ['https://example.com']})
    old_profiles = db.rows('SELECT * FROM profiles')
    old_jobs = db.rows('SELECT * FROM jobs')
    # Remove only new tables to reproduce the prior version's schema.
    with db.connect() as connection:
        connection.execute('DROP TABLE accounts')
        connection.execute('DROP TABLE services')
    db.init(); db.init()
    assert db.rows('SELECT * FROM profiles') == old_profiles
    assert db.rows('SELECT * FROM jobs') == old_jobs
    service_id = service(client)
    assert assign(client, [1], [service_id]).json()['created'] == 1


def test_failed_open_never_changes_preparation_state(client, monkeypatch):
    client.post('/api/profiles', json={'name': 'Профиль'})
    service_id = service(client)
    assign(client, [1], [service_id])
    async def failed(*args):
        raise HTTPException(500, 'Не удалось открыть браузер.')
    monkeypatch.setattr(main, 'open_profile', failed)
    assert client.post('/api/accounts/1/open').status_code == 500
    account = db.rows('SELECT * FROM accounts')[0]
    assert account['status'] == 'planned' and account['opened_at'] is None and account['confirmed_at'] is None


def test_real_registration_windows_preserve_isolation_and_honest_status(client):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append((self.path, self.headers.get('Cookie', '')))
            self.send_response(404 if self.path == '/missing' else 200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Set-Cookie', 'preparation_fixture=saved; HttpOnly; Path=/')
            self.end_headers()
            self.wfile.write('<h1>Локальная форма для проверки</h1><input name="login">'.encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        profiles = client.post('/api/profiles/batch', json={'prefix': 'Тест', 'count': 2}).json()['ids']
        good, missing = service(client, 'Форма', base + '/register'), service(client, 'Недоступно', base + '/missing')
        assign(client, profiles, [good, missing])
        first = db.rows('SELECT * FROM accounts WHERE profile_id=? AND service_id=?', (profiles[0], good))[0]['id']
        other = db.rows('SELECT * FROM accounts WHERE profile_id=? AND service_id=?', (profiles[1], good))[0]['id']
        unavailable = db.rows('SELECT * FROM accounts WHERE profile_id=? AND service_id=?', (profiles[0], missing))[0]['id']
        opened = client.post(f'/api/accounts/{first}/open')
        assert opened.status_code == 200 and opened.json() == {'status': 'open', 'loaded': True, 'account_status': 'in_progress'}
        assert client.post(f'/api/accounts/{unavailable}/open').status_code == 409
        assert db.rows('SELECT * FROM accounts WHERE id=?', (unavailable,))[0]['status'] == 'planned'
        assert db.rows('SELECT * FROM accounts WHERE id=?', (first,))[0]['confirmed_at'] is None
        client.post(f'/api/profiles/{profiles[0]}/close')
        assert client.post(f'/api/accounts/{other}/open').json()['loaded'] is True
        client.post(f'/api/profiles/{profiles[1]}/close')
        visits = [cookie for path, cookie in requests if path == '/register']
        assert visits[:2] == ['', '']  # A second profile never inherits the first profile's session.
        client.post(f'/api/accounts/{first}', json={'status': 'confirmed', 'identifier': 'manual-login'})
        reopened = client.post(f'/api/accounts/{first}/open')
        assert reopened.json()['account_status'] == 'confirmed'
        client.post(f'/api/profiles/{profiles[0]}/close')
        visits = [cookie for path, cookie in requests if path == '/register']
        assert 'preparation_fixture=saved' in visits[2]
        failed_navigation = client.post(f'/api/accounts/{unavailable}/open')
        assert failed_navigation.json() == {'status': 'open', 'loaded': False, 'account_status': 'in_progress'}
        assert db.rows('SELECT * FROM accounts WHERE id=?', (unavailable,))[0]['confirmed_at'] is None
        client.post(f'/api/profiles/{profiles[0]}/close')
    finally:
        server.shutdown(); thread.join()
