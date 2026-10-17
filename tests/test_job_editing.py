import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import db, main
from backend.collector import save_finding


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    with TestClient(main.app) as instance:
        yield instance


def make_job(client):
    client.post('/api/profiles', json={'name': 'Первый'})
    payload = {'name': 'Сбор', 'profile_id': 1, 'urls': ['https://example.com/a'], 'selector': '.ad', 'wait_seconds': 5}
    return client.post('/api/jobs', json=payload).json()['id']


def test_edit_keeps_results_profile_and_history(client):
    job_id = make_job(client)
    job = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
    save_finding(job, {'title': 'Книга', 'text': 'Текст', 'destination': '', 'image_url': ''}, 'https://example.com/a', 'https://example.com/a', 'x.png')
    db.execute("UPDATE jobs SET status='done',last_count=1,last_run=? WHERE id=?", (db.now(), job_id))
    edit = {'name': 'Сбор 2', 'urls': ['https://example.com/b', ' https://example.com/b '], 'selector': ' #atRecs ', 'wait_seconds': 9, 'headed': True}
    assert client.post(f'/api/jobs/{job_id}', json=edit).status_code == 200
    job = client.get('/api/state').json()['jobs'][0]
    assert (job['name'], job['urls'], job['selector'], job['wait_seconds'], job['headed']) == ('Сбор 2', ['https://example.com/b'], '#atRecs', 9, 1)
    # Earlier results, last-run summary and profile are not rewritten by an edit.
    assert (job['profile_id'], job['status'], job['last_count']) == (1, 'done', 1)
    assert job['updated_at']
    assert client.get('/api/findings').json()['total'] == 1
    # A profile switch is not part of editing: unknown fields are ignored, never applied.
    client.post('/api/profiles', json={'name': 'Второй'})
    client.post(f'/api/jobs/{job_id}', json={**edit, 'profile_id': 2})
    assert db.rows('SELECT profile_id FROM jobs WHERE id=?', (job_id,))[0]['profile_id'] == 1


def test_edit_validation_and_missing_job(client):
    job_id = make_job(client)
    base = {'name': 'Сбор', 'urls': ['https://example.com'], 'wait_seconds': 5}
    for bad in [{'urls': []}, {'urls': ['file:///C:/x']}, {'name': '  '}, {'wait_seconds': 1}, {'wait_seconds': 31}]:
        assert client.post(f'/api/jobs/{job_id}', json={**base, **bad}).status_code == 422
    assert client.post('/api/jobs/999', json=base).status_code == 404
    assert db.rows('SELECT urls FROM jobs WHERE id=?', (job_id,))[0]['urls'] == json.dumps(['https://example.com/a'])


def test_running_job_rejects_edit_on_backend(client):
    job_id = make_job(client)
    original = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
    edit = {'name': 'Другое', 'urls': ['https://example.com/z'], 'wait_seconds': 5}
    # A queued run, and a persisted running status, both block editing.
    db.execute("INSERT INTO runs(job_id,trigger,status,not_before,queued_at) VALUES (?,?,?,?,?)", (job_id, 'manual', 'queued', '2999-01-01T00:00:00+00:00', db.now()))
    assert client.post(f'/api/jobs/{job_id}', json=edit).status_code == 409
    db.execute("UPDATE runs SET status='cancelled' WHERE job_id=?", (job_id,))
    db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    assert client.post(f'/api/jobs/{job_id}', json=edit).status_code == 409
    assert db.rows('SELECT name,urls FROM jobs WHERE id=?', (job_id,))[0] == {'name': original['name'], 'urls': original['urls']}


def test_duplicate_is_explicit_and_independent(client):
    job_id = make_job(client)
    db.execute("UPDATE jobs SET status='error',last_error='x',last_count=4 WHERE id=?", (job_id,))
    copy_id = client.post(f'/api/jobs/{job_id}/duplicate').json()['id']
    jobs = {job['id']: job for job in client.get('/api/state').json()['jobs']}
    copy = jobs[copy_id]
    assert copy['name'] == 'Сбор (копия)' and copy['status'] == 'ready' and copy['last_count'] == 0 and not copy['last_error']
    assert (copy['urls'], copy['selector'], copy['profile_id']) == (jobs[job_id]['urls'], '.ad', 1)
    long_id = client.post('/api/jobs', json={'name': 'Я' * 80, 'profile_id': 1, 'urls': ['https://example.com']}).json()['id']
    long_copy = client.post(f'/api/jobs/{long_id}/duplicate').json()['id']
    assert len(db.rows('SELECT name FROM jobs WHERE id=?', (long_copy,))[0]['name']) <= 80


def test_log_context_migration_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    # A 0.4 logs table without context columns.
    with sqlite3.connect(tmp_path / 'radar.sqlite') as raw:
        raw.execute('CREATE TABLE logs (id INTEGER PRIMARY KEY, job_id INTEGER, level TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL)')
        raw.executemany('INSERT INTO logs(job_id,level,message,created_at) VALUES (?,?,?,?)', [
            (3, 'info', 'Сбор начат.', '2026-10-01T00:00:00+00:00'),
            (None, 'info', 'Подготовлено ящиков Zoho: 2.', '2026-10-01T00:00:00+00:00'),
            (None, 'info', 'Создан профиль «А».', '2026-10-01T00:00:00+00:00')])
    db.init()
    db.init()
    rows = db.rows('SELECT job_id,message,source,profile_id FROM logs ORDER BY id')
    assert [row['source'] for row in rows] == ['job', 'mail', 'system']
    assert [row['message'] for row in rows] == ['Сбор начат.', 'Подготовлено ящиков Zoho: 2.', 'Создан профиль «А».']
    db.log(None, 'error', 'Окно', source='profile', profile_id=5)
    db.log(7, 'info', 'Задание', source='unknown')
    tail = db.rows('SELECT source,profile_id,job_id FROM logs ORDER BY id DESC LIMIT 2')
    assert tail == [{'source': 'job', 'profile_id': None, 'job_id': 7}, {'source': 'profile', 'profile_id': 5, 'job_id': None}]
