"""Stage 17 (rest): journal and run history for any period, error codes in the journal, Telegram notifications
(off by default, token only in the vault, one problem reported once) — checked against a fake Telegram API."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend import audit, config, db, main, notify, protection, runs
from backend.worker import Worker

TOKEN = '123456789:' + 'AAH' + 'x' * 32
OTHER_TOKEN = '987654321:' + 'BBK' + 'y' * 32


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    return tmp_path


@pytest.fixture
def client(data, monkeypatch):
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    with TestClient(main.app) as instance:
        yield instance


class FakeTelegram:
    """Records sendMessage calls. `fail` switches it to HTTP 401 with the token echoed in the description."""

    def __init__(self):
        self.messages, self.paths, self.fail = [], [], False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.paths.append(self.path)
                if outer.fail:
                    payload = {'ok': False, 'description': f'Unauthorized for {self.path.split("/")[1][3:]}'}
                    self.send_response(401)
                else:
                    outer.messages.append(body)
                    payload = {'ok': True, 'result': {'message_id': len(outer.messages)}}
                    self.send_response(200)
                self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(json.dumps(payload).encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'


@pytest.fixture
def telegram(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(config, 'TELEGRAM_API', fake.url)
    yield fake
    fake.server.shutdown()


def make_job(name='Сбор AT', urls='["https://site.test/a","https://site.test/b"]'):
    found = db.rows('SELECT id FROM profiles WHERE name=?', ('Профиль',))
    profile = found[0]['id'] if found else db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Профиль', db.now()))
    return db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                      (name, profile, urls, '', 2, db.now()))


def finished_run(job_id, status, code=None, error=None):
    run_id, _ = runs.enqueue(job_id)
    run = runs.claim('w', 4)
    assert run['id'] == run_id
    runs.progress(run_id, 1, 3)
    retry = runs.finish(run, status, error, worker_id='w', error_code=code)
    return run_id, retry


# ---------- notifications ----------

def enable(client, events=None):
    body = {'enabled': True, 'chat_id': '-1001234567890', 'token': TOKEN}
    if events is not None:
        body['events'] = events
    response = client.post('/api/notifications/settings', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_off_by_default_and_token_never_leaves_the_vault(client, telegram):
    assert client.get('/api/notifications').json()['settings'] == {'channel': 'telegram', 'enabled': False, 'chat_id': '',
                                                                    'events': notify.DEFAULT_EVENTS, 'token_set': False}
    job_id = make_job()
    run_id, _ = finished_run(job_id, 'error', 'TIMEOUT', 'Страница не ответила вовремя.')
    assert notify.on_run_finished(run_id) is None and not telegram.paths
    assert client.post('/api/notifications/settings', json={'enabled': True, 'chat_id': '-100'}).status_code == 400
    bad = client.post('/api/notifications/settings', json={'enabled': True, 'chat_id': '-100', 'token': 'not-a-token-' + 'z' * 40})
    assert bad.status_code == 422 and 'not-a-token' not in bad.text and bad.headers['cache-control'] == 'no-store'
    saved = enable(client)
    assert saved == {'channel': 'telegram', 'enabled': True, 'chat_id': '-1001234567890', 'events': notify.DEFAULT_EVENTS, 'token_set': True}
    stored = db.rows("SELECT value FROM settings WHERE key='notify.telegram_token'")[0]['value']
    assert TOKEN not in stored and TOKEN.split(':')[1] not in stored
    everywhere = json.dumps([client.get('/api/notifications').json(), client.get('/api/state').json(), client.get('/api/audit').json(),
                             client.get('/api/logs').json(), db.rows('SELECT * FROM notifications')], ensure_ascii=False)
    assert TOKEN not in everywhere and TOKEN.split(':')[1] not in everywhere
    # Saving other fields without a token keeps the stored one; clear_token removes it and switches off.
    assert client.post('/api/notifications/settings', json={'enabled': True, 'chat_id': '@radar_alerts'}).json()['token_set'] is True
    off = client.post('/api/notifications/settings', json={'enabled': True, 'chat_id': '@radar_alerts', 'clear_token': True})
    assert off.status_code == 400
    off = client.post('/api/notifications/settings', json={'enabled': False, 'chat_id': '@radar_alerts', 'clear_token': True}).json()
    assert off['token_set'] is False and off['enabled'] is False


def test_test_message_and_delivery_failure_hide_token(client, telegram):
    enable(client)
    assert client.post('/api/notifications/test').json()['status'] == 'sent'
    assert telegram.messages[-1]['chat_id'] == '-1001234567890' and 'проверка' in telegram.messages[-1]['text']
    assert telegram.paths[-1] == f'/bot{TOKEN}/sendMessage'
    telegram.fail = True
    failed = client.post('/api/notifications/test')
    assert failed.status_code == 502 and '401' in failed.text and TOKEN not in failed.text and TOKEN.split(':')[1] not in failed.text
    job_id = make_job()
    run_id, _ = finished_run(job_id, 'error', 'HTTP_5XX', 'Страница вернула HTTP 503.')
    assert notify.on_run_finished(run_id) == 'failed'
    warning = audit.search_logs(error_code='NOTIFY_FAILED')
    assert warning['total'] == 1 and TOKEN not in json.dumps(warning, ensure_ascii=False)
    assert TOKEN.split(':')[1] not in json.dumps(db.rows('SELECT * FROM notifications'), ensure_ascii=False)


def test_one_problem_is_reported_once_until_success(client, telegram):
    enable(client)
    job_id = make_job()
    first, _ = finished_run(job_id, 'error', 'TIMEOUT', 'Страница не ответила вовремя.')
    assert notify.on_run_finished(first) == 'sent'
    text = telegram.messages[-1]['text']
    assert 'сбой запуска' in text and '«Сбор AT»' in text and 'TIMEOUT' in text and f'#{first}' in text
    again, _ = finished_run(job_id, 'error', 'TIMEOUT', 'Страница не ответила вовремя.')
    assert notify.on_run_finished(again) == 'suppressed' and len(telegram.messages) == 1
    user, _ = finished_run(job_id, 'waiting_user', 'USER_ACTION', 'Сайт запросил проверку.')
    assert notify.on_run_finished(user) == 'sent' and 'нужен пользователь' in telegram.messages[-1]['text']
    ok, _ = finished_run(job_id, 'done')
    assert notify.on_run_finished(ok) == 'sent' and 'снова работает' in telegram.messages[-1]['text']
    quiet, _ = finished_run(job_id, 'done')
    assert notify.on_run_finished(quiet) is None and len(telegram.messages) == 3  # 'done' is off by default
    later, _ = finished_run(job_id, 'error', 'TIMEOUT')
    assert notify.on_run_finished(later) == 'sent' and len(telegram.messages) == 4  # the problem came back after a success
    # A retried failure, a manual stop and a cancelled run are not reported.
    retried, _ = finished_run(job_id, 'failed', 'BROWSER_CLOSED')
    assert notify.on_run_finished(retried, retried=True) is None
    stopped, _ = finished_run(job_id, 'stopped', 'STOPPED')
    assert notify.on_run_finished(stopped) is None and len(telegram.messages) == 4
    statuses = [row['status'] for row in db.rows('SELECT status FROM notifications ORDER BY id')]
    assert statuses == ['sent', 'suppressed', 'sent', 'sent', 'sent']
    shown = client.get('/api/notifications').json()['recent']
    assert shown[0]['job_name'] == 'Сбор AT' and shown[0]['event'] == 'failure'


def test_done_event_when_enabled(client, telegram):
    enable(client, events=['done'])
    job_id = make_job()
    run_id, _ = finished_run(job_id, 'partial', 'HTTP_4XX')
    assert notify.on_run_finished(run_id) == 'sent' and 'запуск завершён' in telegram.messages[-1]['text']


def test_worker_sends_failure_after_a_real_run(data, telegram, monkeypatch):
    monkeypatch.setattr(config, 'HEARTBEAT_SECONDS', 1)
    notify.save_settings(True, '-100777', notify.DEFAULT_EVENTS, TOKEN)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(503); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers()
            self.wfile.write('<p>Сервис недоступен</p>'.encode())

    site = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    try:
        job_id = make_job('Недоступный сайт', json.dumps([f'http://127.0.0.1:{site.server_port}/a']))
        runs.enqueue(job_id)

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(Worker(embedded=True).run(stop))
            for _ in range(120):
                await asyncio.sleep(0.5)
                if not db.rows("SELECT 1 FROM runs WHERE status IN ('queued','running')"):
                    break
            stop.set()
            await task
        asyncio.run(scenario())
    finally:
        site.shutdown()
    run = db.rows('SELECT * FROM runs WHERE job_id=?', (job_id,))[0]
    assert run['status'] == 'error' and run['error_code'] == 'HTTP_5XX'
    assert len(telegram.messages) == 1 and 'HTTP_5XX' in telegram.messages[0]['text'] and 'Недоступный сайт' in telegram.messages[0]['text']
    coded = audit.search_logs(job_id=job_id, error_code='HTTP_5XX')
    assert coded['total'] >= 1 and all(item['error_code'] == 'HTTP_5XX' for item in coded['items'])


def test_key_rotation_reencrypts_the_bot_token(data, monkeypatch):
    keys = data / 'keys'
    keys.mkdir()
    (keys / 'old.key').write_bytes(Fernet.generate_key())
    (keys / 'new.key').write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(keys / 'old.key'))
    notify.save_settings(False, '-100', ['failure'], TOKEN)
    before = db.rows("SELECT value FROM settings WHERE key='notify.telegram_token'")[0]['value']
    assert protection.rotate(keys / 'old.key', keys / 'new.key', data)['settings'] == 1
    assert db.rows("SELECT value FROM settings WHERE key='notify.telegram_token'")[0]['value'] != before
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(keys / 'new.key'))
    assert notify._token() == TOKEN


# ---------- journal and runs for any period ----------

def test_journal_search_any_period_codes_and_pages(client):
    job_id = make_job()
    profile_id = db.rows('SELECT profile_id FROM jobs WHERE id=?', (job_id,))[0]['profile_id']
    with db.connect() as connection:
        for index in range(450):
            day = f'2026-{8 + index // 150:02d}-{1 + index % 28:02d}T12:00:00+00:00'
            level = 'error' if index % 10 == 0 else 'warning' if index % 10 == 1 else 'info'
            code = 'TIMEOUT' if level == 'error' else 'NO_BLOCKS' if level == 'warning' else None
            connection.execute('INSERT INTO logs(job_id,level,message,created_at,source,error_code) VALUES (?,?,?,?,?,?)',
                               (job_id, level, f'Событие номер {index}: Страница Не Ответила', day, 'job', code))
        connection.execute("INSERT INTO logs(job_id,level,message,created_at,source,profile_id) VALUES (NULL,'info','Профиль открыт',?,'profile',?)",
                           (db.now(), profile_id))
    everything = client.get('/api/logs', params={'limit': 500}).json()
    assert everything['total'] >= 451 and len(everything['items']) >= 451
    oldest = client.get('/api/logs', params={'q': 'событие НОМЕР 0:'}).json()
    assert oldest['total'] == 1 and oldest['items'][0]['message'].startswith('Событие номер 0:')
    errors = client.get('/api/logs', params={'level': 'error'}).json()
    assert errors['total'] == 45 and errors['levels']['info'] >= 360
    assert client.get('/api/logs', params={'error_code': 'TIMEOUT'}).json()['total'] == 45
    assert client.get('/api/logs', params={'error_code': '*', 'q': 'событие'}).json()['total'] == 90
    august = client.get('/api/logs', params={'since': '2026-08-01', 'until': '2026-08-31', 'q': 'событие'}).json()
    assert august['total'] == 150
    by_profile = client.get('/api/logs', params={'profile_id': profile_id}).json()
    assert by_profile['total'] >= 451  # its job's events and its own
    pages = [client.get('/api/logs', params={'limit': 100, 'offset': offset, 'q': 'событие'}).json()['items'] for offset in range(0, 500, 100)]
    assert len({item['id'] for page in pages for item in page}) == 450
    assert client.get('/api/logs', params={'since': '14.10.2026'}).status_code == 400


def test_run_history_any_period(client):
    job_a, job_b = make_job('А'), make_job('Б', '["https://site.test/c"]')
    with db.connect() as connection:
        for index in range(230):
            job_id = job_a if index % 2 else job_b
            stamp = f'2026-09-{1 + index % 28:02d}T0{index % 10}:00:00+00:00'
            status = 'error' if index % 7 == 0 else 'done'
            connection.execute('''INSERT INTO runs(job_id,trigger,status,not_before,queued_at,finished_at,error_code) VALUES (?,?,?,?,?,?,?)''',
                               (job_id, 'schedule', status, stamp, stamp, stamp, 'TIMEOUT' if status == 'error' else None))
    assert client.get('/api/runs').json()['total'] == 230
    assert client.get('/api/runs', params={'status': 'error'}).json()['total'] == 33
    assert client.get('/api/runs', params={'job_id': job_a}).json()['total'] == 115
    assert client.get('/api/runs', params={'since': '2026-09-01', 'until': '2026-09-01'}).json()['total'] == 9
    ids = [r['id'] for offset in (0, 200) for r in client.get('/api/runs', params={'limit': 200, 'offset': offset}).json()['items']]
    assert len(set(ids)) == 230
    assert len(client.get('/api/state').json()['runs']) == 150  # live list for the status bar stays short


def test_audit_search_is_case_insensitive_for_cyrillic(client):
    audit.record('job_create', 'job', 1, name='Сбор Новинок')
    assert client.get('/api/audit', params={'q': 'сбор новинок'}).json()['total'] == 1
    assert client.get('/api/audit', params={'q': '100%'}).json()['total'] == 0
    assert 'job_create' in client.get('/api/audit').json()['actions']
    audit.record('notify_settings', 'notifications', None, enabled=True)
    # The panel shows Russian names of actions; a search typed in Russian finds them.
    assert [i['action'] for i in client.get('/api/audit', params={'q': 'уведомлен'}).json()['items']] == ['notify_settings']
