import asyncio
import json
import sqlite3
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from backend import auth, backup, browser, config, db, main, protection, runs
from backend.worker import Worker


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(config, 'RETRY_DELAYS', [60, 300])
    db.init()
    return tmp_path


@pytest.fixture
def client(data, monkeypatch):
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)  # tests drive the queue themselves
    with TestClient(main.app) as instance:
        yield instance


def job(urls=('https://example.com/a',), profile='Первый'):
    profile_id = db.rows('SELECT id FROM profiles WHERE name=?', (profile,))
    profile_id = profile_id[0]['id'] if profile_id else db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', (profile, db.now()))
    return db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                      ('Сбор', profile_id, json.dumps(list(urls)), '', 2, db.now()))


def test_queue_claim_limits_and_profile_locks(data):
    first, second = job(), job(profile='Второй')
    same_profile = job()
    assert runs.enqueue(first)[1] is None
    assert runs.enqueue(first) == (None, 'active')
    runs.enqueue(second); runs.enqueue(same_profile)
    claimed = runs.claim('w1', max_browsers=2)
    assert claimed['job_id'] == first
    # The next queued run for the same profile is skipped while the profile is leased.
    other = runs.claim('w1', max_browsers=2)
    assert other['job_id'] == second
    assert runs.claim('w1', max_browsers=3) is None  # same_profile waits for the lease
    assert runs.claim('w1', max_browsers=2) is None  # browser limit reached
    runs.finish(claimed, 'done', worker_id='w1')
    assert runs.claim('w1', max_browsers=2)['job_id'] == same_profile
    # A headed window blocks queueing for its profile; a queued run can be cancelled at once.
    third = job(profile='Третий')
    profile = db.rows('SELECT profile_id FROM jobs WHERE id=?', (third,))[0]['profile_id']
    assert runs.acquire_lease(profile, 'window:x', 'window', 60)
    assert runs.enqueue(third) == (None, 'window')
    runs.release_lease(profile, 'window:x')
    runs.enqueue(third)
    assert runs.request_stop(third) == 'cancelled'
    assert db.rows('SELECT status FROM runs WHERE job_id=?', (third,))[0]['status'] == 'cancelled'


def test_lost_worker_run_is_interrupted_and_resumes_from_next_page(data):
    job_id = job(urls=['https://example.com/1', 'https://example.com/2', 'https://example.com/3'])
    runs.enqueue(job_id)
    run = runs.claim('dead', max_browsers=1)
    runs.progress(run['id'], 2, 5)
    db.execute('UPDATE runs SET heartbeat_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', run['id']))
    assert runs.reconcile(stale_seconds=10) == 1
    rows = db.rows('SELECT * FROM runs ORDER BY id')
    assert rows[0]['status'] == 'interrupted' and rows[0]['pages_done'] == 2
    retry = rows[1]
    assert (retry['trigger'], retry['status'], retry['attempt'], retry['resume_from'], retry['retry_of']) == ('retry', 'queued', 2, 2, run['id'])
    assert retry['not_before'] > retry['queued_at']  # bounded backoff
    assert not db.rows('SELECT * FROM profile_leases')
    # Attempts are bounded: after the configured retries nothing is queued again.
    db.execute("UPDATE runs SET not_before=? WHERE id=?", ('2000-01-01T00:00:00+00:00', retry['id']))
    for attempt in range(2):
        current = runs.claim('dead', max_browsers=1)
        db.execute('UPDATE runs SET heartbeat_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', current['id']))
        runs.reconcile(stale_seconds=10)
        db.execute("UPDATE runs SET not_before='2000-01-01T00:00:00+00:00' WHERE status='queued'")
    assert not db.rows("SELECT * FROM runs WHERE status IN ('queued','running')")
    assert db.rows('SELECT status FROM jobs WHERE id=?', (job_id,))[0]['status'] == 'interrupted'


def test_interval_schedule_dedupes_occurrences_and_collapses_missed(data):
    job_id = job()
    start = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)
    db.execute("UPDATE jobs SET schedule_kind='interval',interval_minutes=10,schedule_from=? WHERE id=?", (runs.iso(start), job_id))
    assert runs.schedule_tick(start + timedelta(minutes=5)) == []
    created = runs.schedule_tick(start + timedelta(minutes=25))
    assert len(created) == 1 and db.rows('SELECT scheduled_for FROM runs')[0]['scheduled_for'] == runs.iso(start + timedelta(minutes=20))
    assert runs.schedule_tick(start + timedelta(minutes=26)) == []  # a restarted scheduler does not create a second run
    # While the previous run is still active, the next occurrence is recorded as skipped, not stacked.
    runs.schedule_tick(start + timedelta(minutes=31))
    assert [r['status'] for r in db.rows('SELECT status FROM runs ORDER BY id')] == ['queued', 'skipped']
    db.execute("UPDATE runs SET status='done' WHERE status='queued'")
    # Hours of downtime collapse into a single catch-up run.
    runs.schedule_tick(start + timedelta(minutes=125))
    assert db.rows("SELECT scheduled_for FROM runs WHERE status='queued'") == [{'scheduled_for': runs.iso(start + timedelta(minutes=120))}]
    db.execute('UPDATE jobs SET paused=1 WHERE id=?', (job_id,))
    assert runs.schedule_tick(start + timedelta(minutes=200)) == []


def test_daily_schedule_uses_explicit_timezone(data):
    job_id = job()
    created = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)  # 13:00 Moscow
    db.execute("UPDATE jobs SET schedule_kind='daily',daily_time='03:30',timezone='Europe/Moscow',schedule_from=? WHERE id=?", (runs.iso(created), job_id))
    row = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
    assert runs.latest_due(row, created + timedelta(hours=1)) is None  # 03:30 today was before the schedule existed
    assert runs.next_due(row, created) == datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc)
    assert runs.latest_due(row, datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)) == datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc)


def test_schedule_api_validation_and_next_run(client):
    job_id = job()
    assert client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'daily', 'daily_time': '25:00'}).status_code == 422
    assert client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'interval'}).status_code == 422
    assert client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'daily', 'daily_time': '03:30', 'timezone': 'Mars/Base'}).status_code == 422
    result = client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'interval', 'interval_minutes': 30, 'timezone': 'Europe/Moscow'}).json()
    assert result['next_run_at']
    state = client.get('/api/state').json()
    assert state['jobs'][0]['next_run_at'] == result['next_run_at'] and state['server']['mode'] == 'local'
    paused = client.post(f'/api/jobs/{job_id}/schedule', json={'kind': 'interval', 'interval_minutes': 30, 'timezone': 'Europe/Moscow', 'paused': True}).json()
    assert paused['next_run_at'] is None
    assert db.rows('SELECT schedule_from FROM jobs WHERE id=?', (job_id,))[0]['schedule_from']


def test_login_required_in_server_mode(client, monkeypatch):
    monkeypatch.setattr(config, 'AUTH', True)
    monkeypatch.setattr(config, 'ADMIN_PASSWORD_HASH', auth.make_hash('correct horse battery', iterations=1000))
    monkeypatch.setattr(auth, '_failures', {})
    assert client.get('/api/state').status_code == 401
    assert client.get('/api/export.csv').status_code == 401
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/session').json() == {'auth_required': True, 'authenticated': False, 'mode': 'local'}
    assert client.post('/api/login', json={'password': 'wrong'}).status_code == 401
    ok = client.post('/api/login', json={'password': 'correct horse battery'})
    assert ok.status_code == 200 and 'httponly' in ok.headers['set-cookie'].lower() and 'samesite=strict' in ok.headers['set-cookie'].lower()
    assert client.get('/api/state').status_code == 200
    stored = db.rows('SELECT token_hash FROM sessions')[0]['token_hash']
    assert stored != client.cookies.get(auth.COOKIE)  # only a hash is stored
    client.post('/api/logout')
    assert client.get('/api/state').status_code == 401
    for _ in range(10):
        auth.note_failure('testclient')
    assert client.post('/api/login', json={'password': 'correct horse battery'}).status_code == 429


def test_server_mode_requires_mail_vault_and_disables_windows(client, monkeypatch):
    monkeypatch.setattr(config, 'SERVER', True)
    client.post('/api/profiles', json={'name': 'Сервер'})
    assert client.post('/api/profiles/1/open', json={}).status_code == 409
    assert client.post('/api/mailboxes/prepare', json={'profile_ids': [1], 'domain': 'example.org', 'zoid': '1'}).status_code == 503
    assert client.post('/api/mailboxes/1/credentials', json={}).status_code == 503
    assert client.get('/api/state').json()['server']['mail_enabled'] is True


def test_vault_roundtrip_wrong_key_and_session_migration(data, monkeypatch):
    from cryptography.fernet import Fernet
    key = data / 'vault.key'
    key.write_bytes(Fernet.generate_key())
    legacy = protection.protect(b'{"cookies": [{"name": "a", "value": "1"}], "origins": []}')  # DPAPI on Windows, plain elsewhere
    (data / 'profiles' / '7').mkdir(parents=True)
    (data / 'profiles' / '7' / ('radar-state.dpapi' if legacy.startswith(protection.PREFIX) else 'radar-state.json')).write_bytes(legacy)
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(key))
    sealed = protection.protect(b'secret')
    assert sealed.startswith(protection.VAULT_PREFIX) and b'secret' not in sealed
    assert protection.unprotect(sealed) == b'secret'
    assert browser.read_state(7)['cookies'][0]['value'] == '1'
    assert (data / 'profiles' / '7' / 'radar-state.vault').is_file()
    other = data / 'other.key'
    other.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', str(other))
    with pytest.raises(RuntimeError, match='не подходит'):
        protection.unprotect(sealed)
    monkeypatch.setattr(config, 'VAULT_KEY_FILE', '')
    with pytest.raises(RuntimeError, match='RADAR_VAULT_KEY_FILE'):
        protection.unprotect(sealed)


def test_backup_restore_and_corruption(data, tmp_path_factory):
    job_id = job()
    runs.enqueue(job_id)
    (data / 'evidence' / ('job-1-' + 'a' * 32 + '.png')).write_bytes(b'png')
    (data / 'profiles' / '1').mkdir(parents=True, exist_ok=True)
    (data / 'profiles' / '1' / 'radar-state.json').write_bytes(b'{}')
    out = tmp_path_factory.mktemp('backups')
    archive, manifest = backup.create(out, data_dir=data, keep=2)
    assert archive.suffix == '.zip' and not list(out.glob('*.partial'))
    assert manifest['counts']['jobs'] == 1 and manifest['counts']['runs'] == 1
    target = tmp_path_factory.mktemp('restore') / 'data'
    restored = backup.restore(archive, target)
    assert restored['counts'] == manifest['counts'] and (target / 'evidence' / ('job-1-' + 'a' * 32 + '.png')).read_bytes() == b'png'
    with pytest.raises(RuntimeError, match='не пуст'):
        backup.restore(archive, target)
    broken = out / 'broken.zip'
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(broken, 'w') as copy:
        for item in source.infolist():
            payload = source.read(item.filename)
            copy.writestr(item, payload + b'x' if item.filename.startswith('evidence/') else payload)
    with pytest.raises(RuntimeError, match='Контрольная сумма'):
        backup.verify(broken)


def test_worker_executes_resumes_and_stops(data, monkeypatch):
    monkeypatch.setattr(config, 'HEARTBEAT_SECONDS', 1)
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append(self.path)
            if self.path == '/slow':
                import time
                time.sleep(6)
            body = f'<article data-ad><h2>Объявление {self.path}</h2><a href="https://example.com{self.path}">Книга</a></article>'.encode()
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers(); self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        job_id = job(urls=[f'{base}/p1', f'{base}/p2', f'{base}/p3'])
        run_id, _ = runs.enqueue(job_id)
        db.execute('UPDATE runs SET resume_from=1,pages_done=1 WHERE id=?', (run_id,))  # page 1 was completed earlier
        slow_job = job(urls=[f'{base}/slow', f'{base}/never'], profile='Второй')

        async def scenario():
            stop = asyncio.Event()
            worker = Worker(embedded=True)
            task = asyncio.create_task(worker.run(stop))
            for _ in range(120):
                await asyncio.sleep(0.5)
                if db.rows('SELECT status FROM runs WHERE id=?', (run_id,))[0]['status'] not in runs.ACTIVE:
                    break
            runs.enqueue(slow_job)
            for _ in range(40):
                await asyncio.sleep(0.5)
                if db.rows("SELECT 1 FROM runs WHERE job_id=? AND status='running'", (slow_job,)):
                    break
            runs.request_stop(slow_job)
            for _ in range(40):
                await asyncio.sleep(0.5)
                if not db.rows("SELECT 1 FROM runs WHERE job_id=? AND status IN ('queued','running')", (slow_job,)):
                    break
            stop.set()
            await task

        asyncio.run(scenario())
        run = db.rows('SELECT * FROM runs WHERE id=?', (run_id,))[0]
        assert run['status'] == 'done' and run['pages_done'] == 3 and run['found'] == 2
        assert '/p1' not in hits and hits.count('/p2') == 1 and hits.count('/p3') == 1
        assert {f['title'] for f in db.rows('SELECT title FROM findings')} == {'Объявление /p2', 'Объявление /p3'}
        stopped = db.rows('SELECT * FROM runs WHERE job_id=?', (slow_job,))[0]
        assert stopped['status'] == 'stopped' and '/never' not in hits
        assert db.rows('SELECT stopped_at FROM workers')[0]['stopped_at']
        assert not db.rows('SELECT * FROM profile_leases')
        assert any(log['run_id'] == run_id for log in db.rows('SELECT run_id FROM logs'))
    finally:
        server.shutdown()
