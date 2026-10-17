"""Batch 1 of 0.6.2: stage 26 (session transfer), 02 (session state, key rotation), 08 (per-URL checkpoints,
error codes), 09 (one-off runs, missed/overlap policies, working window, who changed) and the action journal."""
import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from backend import audit, auth, browser, config, db, main, protection, runs, sessions
from backend.worker import Worker

STATE = {'cookies': [{'name': 'radar_login', 'value': 'SECRET-SESSION-VALUE', 'domain': '127.0.0.1', 'path': '/', 'expires': -1,
                      'httpOnly': True, 'secure': False, 'sameSite': 'Lax'}],
         'origins': [{'origin': 'http://127.0.0.1', 'localStorage': [{'name': 'reader', 'value': 'owner'}]}]}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(config, 'RETRY_DELAYS', [60, 300])
    db.init()
    return tmp_path


@pytest.fixture
def client(data, monkeypatch):
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    with TestClient(main.app) as instance:
        yield instance


def profile(name='Владелец'):
    found = db.rows('SELECT id FROM profiles WHERE name=?', (name,))
    return found[0]['id'] if found else db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', (name, db.now()))


def job(urls=('https://example.com/a',), name='Владелец', **fields):
    columns = {'name': 'Сбор', 'profile_id': profile(name), 'urls': json.dumps(list(urls)), 'selector': '', 'wait_seconds': 2, 'created_at': db.now(), **fields}
    return db.execute(f'INSERT INTO jobs({",".join(columns)}) VALUES ({",".join("?" * len(columns))})', tuple(columns.values()))


# ---------- stage 26 / 02: session transfer and state ----------

def test_session_export_import_roundtrip_is_encrypted_and_isolated(client):
    source, target = profile('Локальный'), profile('Серверный')
    browser.write_state(source, STATE)
    assert client.post(f'/api/profiles/{source}/session/export', json={'passphrase': 'short'}).status_code == 400
    exported = client.post(f'/api/profiles/{source}/session/export', json={'passphrase': 'correct horse battery'})
    assert exported.status_code == 200 and exported.headers['cache-control'] == 'no-store'
    assert 'attachment' in exported.headers['content-disposition']
    text = exported.text
    assert 'SECRET-SESSION-VALUE' not in text and 'radar_login' not in text
    document = json.loads(text)
    assert document['format'] == sessions.FORMAT and document['cookies'] == 1 and document['domains'] == ['127.0.0.1']
    # Wrong passphrase or a foreign file never touch the target profile.
    assert client.post(f'/api/profiles/{target}/session/import', json={'passphrase': 'wrong passphrase!!', 'file_text': text}).status_code == 400
    assert client.post(f'/api/profiles/{target}/session/import', json={'passphrase': 'correct horse battery', 'file_text': '{"x":1}'}).status_code == 400
    assert browser.read_state(target) is None
    # A busy profile cannot be overwritten.
    assert runs.acquire_lease(target, 'w', 'collect', 60)
    assert client.post(f'/api/profiles/{target}/session/import', json={'passphrase': 'correct horse battery', 'file_text': text}).status_code == 409
    runs.release_lease(target, 'w')
    imported = client.post(f'/api/profiles/{target}/session/import', json={'passphrase': 'correct horse battery', 'file_text': text})
    assert imported.status_code == 200 and imported.json()['cookies'] == 1
    assert browser.read_state(target)['cookies'][0]['value'] == 'SECRET-SESSION-VALUE'
    state = client.get('/api/state').json()
    by_id = {p['id']: p for p in state['profiles']}
    assert by_id[target]['session_state'] == 'imported' and by_id[source]['session_state'] == 'stored'
    serialized = json.dumps(state, ensure_ascii=False)
    assert 'SECRET-SESSION-VALUE' not in serialized
    journal = json.dumps(client.get('/api/audit').json(), ensure_ascii=False)
    assert 'session_import' in journal and 'session_export' in journal and 'correct horse' not in journal


def test_new_profile_without_snapshot_is_empty(client):
    empty = profile('Пустой')
    assert {p['id']: p for p in client.get('/api/state').json()['profiles']}[empty]['session_state'] == 'empty'
    assert client.post(f'/api/profiles/{empty}/session/export', json={'passphrase': 'correct horse battery'}).status_code == 409


def test_imported_local_storage_is_restored_without_overwriting(data):
    target = profile()
    browser.write_state(target, STATE)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(b'<p>ok</p>')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    browser.write_state(target, {**STATE, 'origins': [{'origin': origin, 'localStorage': [{'name': 'reader', 'value': 'owner'}]}]})

    async def check():
        async with async_playwright() as pw:
            context = await browser.open_context(pw, target, headless=True)
            try:
                page = await context.new_page()
                await page.goto(origin + '/')
                first = await page.evaluate("localStorage.getItem('reader')")
                await page.evaluate("localStorage.setItem('reader', 'changed by site')")
                await page.goto(origin + '/again')
                return first, await page.evaluate("localStorage.getItem('reader')")
            finally:
                await context.close()
    try:
        assert asyncio.run(check()) == ('owner', 'changed by site')
    finally:
        server.shutdown()


def test_key_rotation_reencrypts_sessions_and_mail_ciphers(data, monkeypatch, tmp_path_factory):
    keys = tmp_path_factory.mktemp('keys')
    (keys / 'old.key').write_bytes(Fernet.generate_key())
    (keys / 'new.key').write_bytes(Fernet.generate_key())
    (keys / 'other.key').write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(keys / 'old.key'))
    owner = profile()
    browser.write_state(owner, STATE)
    service = db.execute('INSERT INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)', ('Почта', 'почта', 'email', 'https://mail.example', db.now()))
    account = db.execute('INSERT INTO accounts(profile_id,service_id,created_at,updated_at) VALUES (?,?,?,?)', (owner, service, db.now(), db.now()))
    mailbox = db.execute('INSERT INTO mailboxes(account_id,region,zoid,email,password_cipher,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                         (account, 'eu', '1', 'r@example.org', protection.protect(b'mail-password'), db.now(), db.now()))
    before = (data / 'profiles' / str(owner) / 'radar-state.vault').read_bytes()
    with pytest.raises(RuntimeError, match='Ничего не изменено'):
        protection.rotate(keys / 'other.key', keys / 'new.key', data)
    assert (data / 'profiles' / str(owner) / 'radar-state.vault').read_bytes() == before
    assert protection.rotate(keys / 'old.key', keys / 'new.key', data) == {'sessions': 1, 'mailboxes': 1, 'settings': 0}
    assert not list((data / 'profiles').glob('*/*.bak'))
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(keys / 'new.key'))
    assert browser.read_state(owner)['cookies'][0]['value'] == 'SECRET-SESSION-VALUE'
    cipher = db.rows('SELECT password_cipher FROM mailboxes WHERE id=?', (mailbox,))[0]['password_cipher']
    assert protection.unprotect(cipher) == b'mail-password'
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(keys / 'old.key'))
    with pytest.raises(RuntimeError, match='не подходит'):
        protection.unprotect(cipher)


# ---------- stage 08: per-URL checkpoints, error codes, login marker ----------

def test_worker_records_pages_codes_and_session_state(data, monkeypatch):
    monkeypatch.setattr(config, 'HEARTBEAT_SECONDS', 1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            signed_in = 'radar_login=' in (self.headers.get('Cookie') or '')
            status = 500 if self.path == '/broken' else 404 if self.path == '/missing' else 200
            body = ('<div id="user-menu">reader</div>' if signed_in else '<a href="/login">Войти</a>') + \
                   '<article data-ad><h2>Книга</h2><a href="https://example.com/b">Читать</a></article>'
            self.send_response(status); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers(); self.wfile.write(body.encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        signed = profile('С входом')
        browser.write_state(signed, {'cookies': [{**STATE['cookies'][0], 'domain': '127.0.0.1'}], 'origins': []})
        good = job([f'{base}/ok', f'{base}/broken', f'{base}/missing'], 'С входом', login_selector='#user-menu')
        anonymous = job([f'{base}/ok'], 'Без входа', login_selector='#user-menu')
        run_good, _ = runs.enqueue(good)
        run_anon, _ = runs.enqueue(anonymous)

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(Worker(embedded=True).run(stop))
            for _ in range(160):
                await asyncio.sleep(0.5)
                if not db.rows("SELECT 1 FROM runs WHERE status IN ('queued','running')"):
                    break
            stop.set()
            await task
        asyncio.run(scenario())
        pages = {p['url'].rsplit('/', 1)[1]: p for p in runs.run_pages(run_good)}
        assert pages['ok']['status'] == 'ok' and pages['ok']['found'] == 1 and pages['ok']['login'] == 1 and pages['ok']['screenshot']
        assert pages['broken']['status'] == 'error' and pages['broken']['error_code'] == 'HTTP_5XX'
        assert pages['missing']['error_code'] == 'HTTP_4XX'
        good_run = db.rows('SELECT * FROM runs WHERE id=?', (run_good,))[0]
        assert good_run['status'] == 'partial' and good_run['error_code'] == 'HTTP_5XX'
        states = {row['name']: row for row in db.rows('SELECT name,session_state,session_checked_at,last_used_at FROM profiles')}
        assert states['С входом']['session_state'] == 'active' and states['С входом']['last_used_at']
        assert states['Без входа']['session_state'] == 'expired' and states['Без входа']['session_checked_at']
        assert runs.run_pages(run_anon)[0]['login'] == 0
        assert any('Сессия профиля истекла' in log['message'] for log in db.rows('SELECT message FROM logs'))
    finally:
        server.shutdown()


def test_run_pages_api_and_error_code_catalog(client):
    job_id = job()
    run_id, _ = runs.enqueue(job_id)
    runs.record_page(run_id, {'index': 0, 'url': 'https://example.com/a', 'status': 'error', 'found': 0, 'error_code': 'TIMEOUT',
                              'message': 'Страница или браузер не ответили вовремя.', 'screenshot': None, 'login': None})
    assert client.get(f'/api/runs/{run_id}/pages').json()['pages'][0]['error_code'] == 'TIMEOUT'
    assert client.get('/api/runs/999/pages').status_code == 404
    assert 'TIMEOUT' in client.get('/api/state').json()['server']['error_codes']


# ---------- stage 09: schedule ----------

T0 = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)


def scheduled(job_id):
    return [(r['scheduled_for'], r['status']) for r in db.rows("SELECT scheduled_for,status FROM runs WHERE job_id=? AND trigger='schedule' ORDER BY id", (job_id,))]


def test_once_runs_a_single_time(data):
    job_id = job(schedule_kind='once', run_at=runs.iso(T0 + timedelta(hours=2)), schedule_from=runs.iso(T0))
    row = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
    assert runs.next_due(row, T0) == T0 + timedelta(hours=2)
    assert runs.schedule_tick(T0 + timedelta(hours=1)) == []
    assert len(runs.schedule_tick(T0 + timedelta(hours=2, minutes=1))) == 1
    db.execute("UPDATE runs SET status='done'")
    assert runs.schedule_tick(T0 + timedelta(days=3)) == []
    assert runs.next_due(row, T0 + timedelta(hours=3)) is None


def test_missed_policies(data):
    base = dict(schedule_kind='interval', interval_minutes=60, schedule_from=runs.iso(T0))
    one, skip, every = job(name='one', missed_policy='one', **base), job(name='skip', missed_policy='skip', **base), \
        job(name='all', missed_policy='all', overlap_policy='queue', **base)
    # The program was off for five hours.
    runs.schedule_tick(T0 + timedelta(hours=5, minutes=30))
    assert scheduled(one) == [(runs.iso(T0 + timedelta(hours=5)), 'queued')]
    assert scheduled(skip) == [(runs.iso(T0 + timedelta(hours=5)), 'skipped')]
    assert [s for _, s in scheduled(every)] == ['queued'] * 5
    # A punctual tick under 'skip' queues normally.
    runs.schedule_tick(T0 + timedelta(hours=6, minutes=2))
    assert scheduled(skip)[-1] == (runs.iso(T0 + timedelta(hours=6)), 'queued')


def test_overlap_queue_runs_sequentially(data):
    job_id = job(schedule_kind='interval', interval_minutes=10, schedule_from=runs.iso(T0), overlap_policy='queue')
    runs.schedule_tick(T0 + timedelta(minutes=11))
    runs.schedule_tick(T0 + timedelta(minutes=21))
    assert [s for _, s in scheduled(job_id)] == ['queued', 'queued']
    first = runs.claim('w', max_browsers=3)
    assert first is not None
    runs.release_lease(db.rows('SELECT profile_id FROM jobs WHERE id=?', (job_id,))[0]['profile_id'], 'w')
    assert runs.claim('w', max_browsers=3) is None  # the second run waits for the first of the same job


def test_working_window_limits_occurrences(data):
    # Every hour, only 01:00-04:00 Moscow time (22:00-01:00 UTC).
    job_id = job(schedule_kind='interval', interval_minutes=60, schedule_from=runs.iso(T0), timezone='Europe/Moscow',
                 window_start='01:00', window_end='04:00')
    row = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
    assert runs.next_due(row, T0 + timedelta(hours=1, minutes=5)) == T0 + timedelta(hours=22)
    assert runs.latest_due(row, T0 + timedelta(hours=5)) is None  # 04:00-08:00 MSK are outside the window
    runs.schedule_tick(T0 + timedelta(hours=22, minutes=5))
    assert scheduled(job_id) == [(runs.iso(T0 + timedelta(hours=22)), 'queued')]
    overnight = dict(row, window_start='22:00', window_end='06:00')
    assert runs.in_window(overnight, T0) and not runs.in_window(overnight, T0 + timedelta(hours=12))


def test_schedule_api_once_policies_and_who_changed(client, monkeypatch):
    job_id = job()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
    assert client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'once', 'run_at': past, 'timezone': 'UTC'}).status_code == 422
    assert client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'interval', 'interval_minutes': 5, 'window_start': '01:00'}).status_code == 422
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
    result = client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'once', 'run_at': future, 'timezone': 'UTC'}).json()
    assert result['next_run_at'].startswith(future.replace('T', 'T')[:13])
    response = client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'interval', 'interval_minutes': 30, 'timezone': 'Europe/Moscow',
                                                                   'missed_policy': 'skip', 'overlap_policy': 'queue', 'window_start': '22:00', 'window_end': '06:00'})
    assert response.status_code == 200
    row = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
    assert (row['missed_policy'], row['overlap_policy'], row['window_start'], row['window_end']) == ('skip', 'queue', '22:00', '06:00')
    assert row['schedule_updated_by'] == 'локальный пользователь' and row['schedule_updated_at']
    entries = client.get('/api/audit', params={'action': 'schedule_change'}).json()
    assert entries['total'] == 2 and entries['items'][0]['details']['missed_policy'] == 'skip'


def test_audit_records_actor_and_never_secrets(client, monkeypatch):
    monkeypatch.setattr(config, 'AUTH', True)
    monkeypatch.setattr(config, 'ADMIN_PASSWORD_HASH', auth.make_hash('correct horse battery', iterations=1000))
    monkeypatch.setattr(auth, '_failures', {})
    client.post('/api/login', json={'password': 'nope'})
    client.post('/api/login', json={'password': 'correct horse battery'})
    job_id = client.post('/api/jobs', json={'name': 'Через API', 'profile_id': profile(), 'urls': ['https://example.com']}).json()['id']
    client.post(f'/api/jobs/{job_id}', json={'name': 'Переименовано', 'urls': ['https://example.com'], 'login_selector': '#me'})
    items = client.get('/api/audit').json()['items']
    actions = [item['action'] for item in items]
    assert {'login_failed', 'login', 'job_create', 'job_edit'} <= set(actions)
    edit = next(item for item in items if item['action'] == 'job_edit')
    assert edit['actor'].startswith('администратор · сессия ') and set(edit['details']['changed']) == {'name', 'login_selector'}
    assert 'correct horse' not in json.dumps(items, ensure_ascii=False)
    audit.record('test', password='x', token='y', ok=1)
    assert db.rows("SELECT details FROM audit WHERE action='test'")[0]['details'] == '{"ok": 1}'
    found = client.get('/api/audit', params={'q': 'Через API'}).json()
    assert found['total'] >= 1
